"""Tests for the database layer and the main script: build, migrations, picking, rating,
export, snapshots, and the per-tab and menu paths (iTerm2 mocked). Prefs and the shell
scripts: srcp_test_tools.py; shared fixtures: srcp_testlib.py.

Runs under iTerm2's Python (the main script imports iterm2) against a temp folder; nothing
touches the real database or iTerm2's settings. lint.sh runs it with
    "$ITERM_PY" -m unittest discover -s .tests -p 'srcp_test_*.py'

The dot keeps this folder out of iTerm2's Scripts menu, like .tools/.
"""

import asyncio
import contextlib
import fcntl
import glob
import hashlib
import io
import json
import math
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".tools"))  # before importing the tools

import srcp_build_scheme_db as build  # noqa: E402  (after the sys.path line above)
import srcp_db_backup  # noqa: E402  (after the sys.path line above)
import srcp_export_curation  # noqa: E402  (after the sys.path line above)
import srcp_favorites  # noqa: E402  (after the sys.path line above)
import srcp_iterm_prefs  # noqa: E402  (after the sys.path line above)
import srcp_odds  # noqa: E402  (after the sys.path line above)
import srcp_schema  # noqa: E402  (after the sys.path line above)
from srcp_testlib import (  # noqa: E402  (after the sys.path line above)
    DARK,
    LIGHT,
    MAIN,
    MAIN_PATHS,
    PRESETS,
    REPO_NAMES,
    RUN_FOREVER,
    UNREADABLE,
    color,
    load_main,
)

BUILD = os.path.join(ROOT, ".tools", "srcp_build_scheme_db.py")


class Stop(Exception):
    """Ends a forever-loop under test."""


class FakePreset(str):
    """What get_preset returns in the tests: the preset's name, with no colors to adjust."""

    values = ()


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="srcp-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        # The main script quits with os._exit, which would end the whole test run with
        # status 0 (so lint.sh would pass with the rest unrun): raise SystemExit instead.
        patch = mock.patch.object(os, "_exit", side_effect=sys.exit)
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "schemes"))
        for name in REPO_NAMES:
            with open(os.path.join(self.repo, "schemes", f"{name}.itermcolors"), "wb") as f:
                plistlib.dump(PRESETS[name], f)
        self.data = os.path.join(self.tmp, "data")
        self.db_path = os.path.join(self.data, "schemes.db")
        # Every seeded font "installed" in a temp folder, so results don't depend on this Mac.
        # Not tmp/fonts: install_fonts and some tests rebuild that one.
        self.sysfonts = os.path.join(self.tmp, "sysfonts")
        os.makedirs(self.sysfonts)
        for files in build.SEED_FONTS.values():
            open(os.path.join(self.sysfonts, files[0]), "w").close()
        patch = mock.patch.object(build, "FONT_DIRS", [self.sysfonts])
        patch.start()
        self.addCleanup(patch.stop)
        self.build()
        self.main = load_main(self.data, self.sysfonts)
        # set_variables sends one RPC for all of a session's variables; pass each to the
        # session's async_set_variable, which the tests fake.
        self.sessions = {}
        ok = self.main["api_pb2"].VariableResponse.Status.Value("OK")

        self.variable_rpcs = []  # the number of variables each call set

        async def one_call(_connection, session_id, sets, _gets):
            self.variable_rpcs.append(len(sets))
            for name, value in sets:
                await self.sessions[session_id].async_set_variable(name, json.loads(value))
            return types.SimpleNamespace(variable_response=types.SimpleNamespace(status=ok))

        patch = mock.patch.object(self.main["iterm2"].rpc, "async_variable", one_call)
        patch.start()
        self.addCleanup(patch.stop)
        self.db = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(self.db.close)

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rows, _ = build.build_rows(PRESETS, self.repo)
            build.write_db(self.db_path, rows)

    def scheme(self, name):
        return self.db.execute(
            "SELECT mode, passes_gate, duplicate_of FROM schemes WHERE name = ?", (name,)
        ).fetchone()

    def discovery(self, mode):
        return {n for (n,) in self.db.execute("SELECT name FROM discovery WHERE mode = ?", (mode,))}

    def tab(self, variables, buttons=(), db_path=None, normal_font="Monaco 13"):
        """Fake a tab showing `variables`; each dialog answers with the next of `buttons`.

        Sets self.session, self.profile, self.app, self.store; dialog texts go to self.texts.
        Replaces the main script's sync_and_log (profiles aren't refreshed) and get_preset (a
        FakePreset, with no colors), and turns the icon font off.
        """
        self.main["FONT_DIRS"] = []  # no icon font
        self.main["sync_and_log"] = mock.AsyncMock()
        self.main["get_preset"] = mock.AsyncMock(side_effect=lambda _c, name: FakePreset(name))
        self.variables = variables

        async def set_variable(name, value):
            variables[name] = value

        self.profile = mock.AsyncMock(normal_font=normal_font, original_guid=None, guid=None)
        self.profile.name = "Default"
        self.session = mock.AsyncMock(session_id="s1", async_set_variable=set_variable)
        self.sessions["s1"] = self.session
        self.session.async_get_variable.side_effect = variables.get
        self.session.async_get_profile.return_value = self.profile
        self.app = mock.MagicMock(windows=[])
        self.app.get_session_by_id.return_value = self.session
        self.app.get_window_and_tab_for_session.return_value = (None, None)
        self.app.async_get_theme = mock.AsyncMock(return_value=["dark"])
        self.store = self.main["Store"](db_path or self.db_path)
        self.texts = []
        answers, texts = iter(buttons), self.texts

        class Alert:
            def __init__(self, title, text, window_id=None):
                texts.append(text)
                self.buttons = []

            def add_button(self, title):
                self.buttons.append(title)

            async def async_run(self, connection):
                return 1000 + self.buttons.index(next(answers)) if self.buttons else 1000

        patch = mock.patch.object(self.main["iterm2"], "Alert", Alert)
        patch.start()
        self.addCleanup(patch.stop)

    def run_quietly(self, coro):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            asyncio.run(coro)
        return out.getvalue()

    def record(self, *values):
        """Add a launches row: values as the fields of the main script's Launch."""
        self.main["record_launch"](self.db, self.main["Launch"](*values))

    def write_profiles(self):
        """Write the favorites' profiles file now; the count, or None if it was current."""
        return self.main["write_favorite_profiles"](self.main["read_favorites"](self.db))

    def sync_now(self):
        """The main script's sync_and_log: rewrite the profiles if they changed."""
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(self.main["sync_and_log"](self.main["Store"](self.db_path)))

    def export_prefs(self, prefs):
        """Make the main script's prefs read (srcp_iterm_prefs.load_prefs) return prefs."""
        data = plistlib.dumps(prefs)
        result = types.SimpleNamespace(stdout=data, returncode=0)
        fake = types.SimpleNamespace(run=lambda *a, **k: result)
        patch = mock.patch.object(srcp_iterm_prefs, "subprocess", fake)
        patch.start()
        self.addCleanup(patch.stop)

    def fixing(self, value):
        """Set config fix_colors (None deletes it)."""
        self.db.execute("DELETE FROM config WHERE key = 'fix_colors'")
        if value is not None:
            self.db.execute("INSERT INTO config VALUES ('fix_colors', ?)", (value,))


class BuildTest(DbTest):
    def test_schema_version_and_rebuild_keeps_user_data(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, len(build.MIGRATIONS))
        self.db.execute("CREATE VIEW my_view AS SELECT name FROM schemes")
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.build()
        self.assertEqual(self.db.execute("SELECT count(*) FROM my_view").fetchone()[0], 7)
        self.assertEqual(self.db.execute("SELECT stars FROM favorites").fetchall(), [(4,)])

    def test_modes_gate_and_colorful(self):
        self.assertEqual(self.scheme("Dark One")[:2], ("dark", 1))
        self.assertEqual(self.scheme("Light One")[:2], ("light", 1))
        self.assertEqual(self.scheme("Murky")[1], 0)
        colorful = dict(self.db.execute("SELECT name, colorful FROM discovery"))
        self.assertEqual((colorful["Yellow"], colorful["Light One"]), (1, 0))

    def test_duplicates_stay_out_of_discovery(self):
        self.assertEqual(self.scheme("dark one")[2], "Dark One")  # old spelling
        self.assertEqual(self.scheme("Dark Two Copy")[2], "Dark Two")  # repo twin
        self.assertIsNone(self.scheme("Dark Two")[2])
        self.assertEqual(self.discovery("dark"), {"Dark One", "Dark Two", "Murky"})  # adjusted
        self.fixing("0")
        self.assertEqual(self.discovery("dark"), {"Dark One", "Dark Two"})

    def test_open_launches_use_their_index(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        self.assertGreaterEqual(version, 6)
        plan = self.db.execute(
            "EXPLAIN QUERY PLAN SELECT id, session_id FROM launches "
            "WHERE ended_at IS NULL AND end_reason IS NULL AND launched_at < ?",
            ("2026-11-05 00:00:00",),
        ).fetchall()
        self.assertIn("launches_open", str(plan))

    def test_newer_schema_is_refused(self):
        self.db.execute("PRAGMA user_version = 99")
        result = subprocess.run(
            [sys.executable, "-I", build.__file__, "--db", self.db_path, "--repo", self.repo],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("newer than this script", result.stderr)

    def test_load_main_paths_stay_in_the_temp_folder(self):
        for name in MAIN_PATHS:
            value = self.main[name]
            for path in value if isinstance(value, list) else [value]:
                self.assertTrue(path.startswith(self.tmp), f"{name} = {path!r}")
        # Every other path the script holds too, so a new one under ~ can't reach the real
        # home: HOME pointed into the temp folder while its source ran.
        home = os.path.expanduser("~")
        for name, value in self.main.items():
            if name.startswith("__") or name == "TOOLS":  # the real checkout, on purpose
                continue
            for item in value if isinstance(value, (list, tuple)) else [value]:
                if isinstance(item, str):
                    self.assertFalse(item.startswith(home + os.sep), f"{name} = {item!r}")

    def test_load_main_refuses_a_reworded_run_forever_line(self):
        with open(MAIN) as f:
            reworded = f.read().replace(RUN_FOREVER, "iterm2.run_forever(main, retry=True)")
        fake_open = mock.mock_open(read_data=reworded)
        with mock.patch("builtins.open", fake_open), self.assertRaises(AssertionError):
            load_main(self.data, self.sysfonts)

    def test_shared_values_come_from_one_place(self):
        # The main script imports these; it doesn't keep copies.
        for name in ("FIX_ON_SQL", "UNHIDE", "COMPONENTS", "SEED_CONFIG", "SEED_FONTS"):
            self.assertIs(self.main[name], getattr(srcp_schema, name), name)
        self.assertIs(srcp_favorites.FIX_ON_SQL, srcp_schema.FIX_ON_SQL)
        self.assertIs(srcp_favorites.UNHIDE, srcp_schema.UNHIDE)
        self.assertIs(build.FIX_ON_SQL, srcp_schema.FIX_ON_SQL)
        self.assertIs(self.main["colors_hash"], srcp_schema.colors_hash)
        seeded = {k: float(build.SEED_CONFIG[k]) for k in self.main["DEFAULT_CONFIG"]}
        self.assertEqual(seeded, self.main["DEFAULT_CONFIG"])
        nonascii = self.main["DEFAULT_NONASCII_FONT"]
        self.assertEqual(nonascii, build.SEED_CONFIG["nonascii_font"])
        self.assertEqual(self.main["NONASCII_FONT_FILES"], build.SEED_FONTS[nonascii])

    def test_no_hand_copies_left(self):
        names = "FIX_ON_SQL|UNHIDE|HASH_KEYS|COMPONENTS|FONT_DIRS|SEED_FONTS|SEED_CONFIG"
        copies = re.compile(rf'^({names}) =|"schemes-\*\.db"', re.M)
        for path in (MAIN, BUILD, os.path.join(ROOT, ".tools", "srcp_favorites.py")):
            with open(path) as f, self.subTest(path=os.path.basename(path)):
                self.assertEqual(copies.findall(f.read()), [])

    def test_a_missing_tools_folder_exits_with_a_clear_line(self):
        real_import = __import__

        def no_schema(name, *args, **kwargs):
            if name == "srcp_schema":
                raise ImportError("No module named 'srcp_schema'")
            return real_import(name, *args, **kwargs)

        out = io.StringIO()
        with (
            mock.patch("builtins.__import__", no_schema),
            contextlib.redirect_stdout(out),
            self.assertRaises(SystemExit),
        ):
            load_main(self.data, self.sysfonts)
        self.assertIn("[error] missing", out.getvalue())
        self.assertIn("run ./setup.sh again", out.getvalue())

    def fonts(self):
        return {
            n: (e, m) for n, e, m in self.db.execute("SELECT name, enabled, missing FROM fonts")
        }

    def install_fonts(self, *names):
        """Only these seeded fonts are installed, in a temp font folder."""
        folder = os.path.join(self.tmp, "fonts")
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        for name in names:
            open(os.path.join(folder, build.SEED_FONTS[name][0]), "w").close()
        return mock.patch.object(build, "FONT_DIRS", [folder])

    def test_a_font_installed_later_is_enabled_but_one_you_disabled_is_not(self):
        with self.install_fonts():
            self.build()
        self.assertEqual(set(self.fonts().values()), {(0, 1)})
        self.db.execute("UPDATE fonts SET missing = 0 WHERE name = 'Monaco'")  # keep it off
        with self.install_fonts("Menlo-Regular", "Monaco"):
            self.build()
        fonts = self.fonts()
        self.assertEqual(fonts["Menlo-Regular"], (1, 0))
        self.assertEqual(fonts["Monaco"], (0, 0))
        self.assertEqual(fonts["SFMono-Regular"], (0, 1))
        self.db.execute("UPDATE fonts SET enabled = 0 WHERE name = 'Menlo-Regular'")
        with self.install_fonts("Menlo-Regular", "Monaco"):
            self.build()
        self.assertEqual(self.fonts()["Menlo-Regular"], (0, 0))
        with self.install_fonts():  # uninstalled: off until it's back
            self.build()
        self.assertEqual(self.fonts()["Monaco"], (0, 0))
        self.db.execute("UPDATE fonts SET enabled = 1 WHERE name = 'Monaco'")
        with self.install_fonts():
            self.build()
        self.assertEqual(self.fonts()["Monaco"], (0, 1))

    def test_schema_5_marks_disabled_fonts_that_are_still_missing(self):
        self.db.execute("ALTER TABLE fonts DROP COLUMN missing")
        self.db.execute("DROP INDEX launches_open")  # migration 6's, so this is a version 4 DB
        self.db.execute("PRAGMA user_version = 4")
        self.db.execute("UPDATE fonts SET enabled = 0")
        self.db.execute("UPDATE fonts SET enabled = 1 WHERE name = 'Monaco'")
        with self.install_fonts("Menlo-Regular", "Monaco"):
            self.build()
        fonts = self.fonts()
        self.assertEqual(fonts["SFMono-Regular"], (0, 1))  # not installed: the build's doing
        self.assertEqual(fonts["Menlo-Regular"], (0, 0))  # installed yet off: your choice
        self.assertEqual(fonts["Monaco"], (1, 0))


class RepoModeTest(DbTest):
    def test_the_repos_yaml_variant_decides_the_mode(self):
        self.assertEqual(self.scheme("Dark One")[0], "dark")  # by luminance
        os.makedirs(os.path.join(self.repo, "yaml"))
        with open(os.path.join(self.repo, "yaml", "Dark One.yml"), "w") as f:
            f.write('name: "Dark One"\nvariant: "light"\nbackground: "#000000"\n')
        self.build()
        self.assertEqual(self.scheme("Dark One")[0], "light")  # the repo says so: it wins


class MigrationTest(DbTest):
    """A rebuild brings a database from every older schema up to date and keeps its data."""

    # sha1 of each shipped MIGRATIONS step, whitespace collapsed. A DB that already ran a step
    # never runs it again, so editing one in place reaches nobody who has it: add a step instead.
    SHIPPED = (
        "e54775956c4fa3f823009b7513402fbcc6d8c8f0",
        "c3d0f080a1ce57ef28326c8f10662cc88c721302",
        "a69899c5ffbea0a73650d114ebea22b784547cd6",
        "3f138a2cfe42d0754b6a3db5439f5316bef1f6f0",
        "b25981c4480b3f6e37af9a840e9a8714a0b3020c",
        "190d77892180900af589600618cac1ed2cc3312f",
        "5f5c310bd33c72ef4b7aa0f1dc85fbba846a0a13",
    )

    def test_min_schema_is_the_newest_migration(self):
        # Stricter than "a change to the shape or meaning of schemes raises it": every step so
        # far has, and a step that didn't would let readers accept a DB they can't read.
        self.assertEqual(srcp_schema.MIN_SCHEMA, len(build.MIGRATIONS))

    def test_shipped_migrations_are_never_edited(self):
        for n, (sql, sha) in enumerate(zip(build.MIGRATIONS, self.SHIPPED, strict=False), 1):
            with self.subTest(migration=n):
                got = hashlib.sha1(" ".join(sql.split()).encode(), usedforsecurity=False)
                self.assertEqual(got.hexdigest(), sha, "a shipped step changed: add a new one")
        self.assertEqual(len(self.SHIPPED), len(build.MIGRATIONS), "list a new step's sha1 here")

    def db_at_version(self, n):
        """A DB as a build with only MIGRATIONS[:n] left it, holding some user data.

        Migrations 1-4 shipped together (the first release), so real DBs started at schema 4;
        1-3 are here so each migration is also checked on its own.
        """
        path = os.path.join(self.tmp, f"v{n}", "schemes.db")
        os.makedirs(os.path.dirname(path))
        db = sqlite3.connect(path, isolation_level=None)
        for i, sql in enumerate(build.MIGRATIONS[:n], 1):
            build.execute_all(db, sql)
            if i == 1:  # as migrate() seeds it
                db.executemany(
                    "INSERT OR IGNORE INTO overrides (name, include) VALUES (?, 1)",
                    [(name,) for name in build.SEED_INCLUDES],
                )
        db.execute("CREATE TABLE schemes (name TEXT PRIMARY KEY, mode TEXT)")  # an older shape
        db.execute("CREATE VIEW my_view AS SELECT preset, stars FROM favorites")  # the user's
        db.execute("INSERT INTO config VALUES ('favorite_share', '0.7')")  # no fix_colors yet
        db.execute("INSERT INTO overrides (name, include) VALUES ('Yellow', 0)")  # Never show
        # Both disabled; Monaco's file is installed, SF Mono's isn't (see test below).
        db.execute("INSERT INTO fonts (name, enabled) VALUES ('Monaco', 0), ('SFMono-Regular', 0)")
        if n >= 2:
            db.execute("UPDATE fonts SET label = 'Mo' WHERE name = 'Monaco'")  # the user's label
        if n >= 5:  # migration 5 backfilled it then
            db.execute("UPDATE fonts SET missing = 1 WHERE name = 'SFMono-Regular'")
        db.execute(
            "INSERT INTO favorites VALUES ('Dark One', 'Monaco', 13, 4, '2026-10-01 00:00:00')"
        )
        db.execute(
            "INSERT INTO launches (launched_at, preset, font, size, mode, source, session_id) "
            "VALUES ('2026-10-01 00:00:00', 'Dark One', 'Monaco', 13, 'dark', 'favorite', 's1')"
        )
        if n >= 3:
            rating = (
                "INSERT INTO ratings (at, preset, font, action, stars) "
                "VALUES ('2026-10-01 00:00:00', 'Dark One', ?, 'save', 4)"
            )
            copies = 2 if n == 3 else 1  # duplicates only before migration 4's unique index
            for _ in range(copies):
                db.execute(rating, ("Monaco",))
                db.execute(rating, (None,))
        db.execute(f"PRAGMA user_version = {n}")
        db.close()
        return path

    def test_every_old_schema_upgrades_and_keeps_its_data(self):
        # Every seeded font is installed (DbTest.setUp) but SF Mono, so its backfill shows.
        os.remove(os.path.join(self.sysfonts, build.SEED_FONTS["SFMono-Regular"][0]))
        latest = len(build.MIGRATIONS)
        for n in range(1, latest):
            with self.subTest(schema=n):
                path = self.db_at_version(n)
                with contextlib.redirect_stdout(io.StringIO()):
                    rows, _ = build.build_rows(PRESETS, self.repo)
                    build.write_db(path, rows)
                db = sqlite3.connect(path)
                self.addCleanup(db.close)

                def one(sql, db=db):
                    return db.execute(sql).fetchall()

                self.assertEqual(one("PRAGMA user_version"), [(latest,)])
                config = dict(one("SELECT key, value FROM config"))
                self.assertEqual(config["favorite_share"], "0.7")  # kept
                self.assertEqual(config["fix_colors"], "1")  # added later, seeded
                fonts_now = {
                    r[0]: r[1:] for r in one("SELECT name, enabled, missing, label FROM fonts")
                }
                self.assertEqual(fonts_now["Monaco"], (0, 0, "Mo" if n >= 2 else "Monaco"))
                self.assertEqual(fonts_now["SFMono-Regular"], (0, 1, "SF Mono"))  # backfilled
                self.assertEqual(fonts_now["Menlo-Regular"][:2], (1, 0))  # seeded, installed
                self.assertEqual(one("SELECT preset, stars FROM my_view"), [("Dark One", 4)])
                self.assertEqual(len(one("SELECT * FROM launches")), 1)
                self.assertIn(("Yellow", 0), one("SELECT name, include FROM overrides"))
                indexes = {r[0] for r in one("SELECT name FROM sqlite_master WHERE type='index'")}
                self.assertLessEqual({"ratings_event", "launches_open"}, indexes)
                if n >= 3:  # the duplicate pairs collapsed to one row each
                    self.assertEqual(one("SELECT count(*) FROM ratings"), [(2,)])
                    # And the unique index keeps them out from now on, NULL font included.
                    db.execute(
                        "INSERT OR IGNORE INTO ratings (at, preset, font, action, stars) "
                        "VALUES ('2026-10-01 00:00:00', 'Dark One', NULL, 'save', 4)"
                    )
                    self.assertEqual(one("SELECT count(*) FROM ratings"), [(2,)])
                # The rebuilt views work: the favorite is in its pool, the hidden one is out.
                self.assertEqual(one("SELECT preset FROM favorite_pool"), [("Dark One",)])
                discovery = {r[0] for r in one("SELECT name FROM discovery")}
                self.assertIn("Dark Two", discovery)
                self.assertNotIn("Yellow", discovery)


class OldSchemaTest(DbTest):
    """A DB older than srcp_schema.MIN_SCHEMA: one message to rebuild, then it just works."""

    def make_old(self):
        """This DB as the build before the newest step left it (only the version is behind:
        every step so far that raised MIN_SCHEMA was harmless to run again)."""
        self.db.execute(f"PRAGMA user_version = {srcp_schema.MIN_SCHEMA - 1}")

    def test_the_main_script_asks_once_for_a_rebuild_then_works_without_a_restart(self):
        self.make_old()
        store = self.main["Store"](self.db_path)
        look_fixes = self.main["look_fixes"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(asyncio.run(store.call(look_fixes, "Murky")))
            self.assertIsNone(asyncio.run(store.call(look_fixes, "Murky")))
        self.assertEqual(out.getvalue().count("rebuild it: python3 .tools/srcp_build"), 1)
        self.build()  # what the message says to do: same file, same inode, same open Store
        with contextlib.redirect_stdout(io.StringIO()):
            fixes, _built_hash = asyncio.run(store.call(look_fixes, "Murky"))
        self.assertIsNotNone(fixes)
        self.assertNotIn("old-schema", self.main["_warned"])

    def test_a_file_that_isnt_a_db_is_reported_once(self):
        path = os.path.join(self.tmp, "not-a-db.db")
        with open(path, "wb") as f:
            f.write(b"this is not SQLite" * 100)
        store = self.main["Store"](path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for _ in range(3):
                self.assertIsNone(asyncio.run(store.call(self.main["look_fixes"], "Murky")))
        self.assertEqual(out.getvalue().count("can't be read"), 1)

    def test_the_build_doesnt_compare_an_old_dbs_fixes(self):
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        self.assertIn("Murky", build.favorite_fixes(self.db_path))
        self.make_old()
        self.assertIsNone(build.favorite_fixes(self.db_path))  # can't tell: no "changed" list

    def test_the_tools_refuse_an_old_db_with_one_line(self):
        self.make_old()
        with self.assertRaises(srcp_schema.OldSchema):
            srcp_favorites.connect(self.db_path)
        for tool in ("srcp_favorites.py", "srcp_odds.py"):
            with self.subTest(tool=tool):
                path = os.path.join(ROOT, ".tools", tool)
                result = subprocess.run(
                    [sys.executable, "-I", path, "--db", self.db_path],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("The DB is too old", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


class MainScriptTest(DbTest):
    def test_pick_respects_mode_and_exclude(self):
        self.fixing("0")  # Murky (unreadable) stays out
        for _ in range(50):
            name, font, source, _stars = self.main["pick"](self.db, "dark", {"Dark Two"})
            self.assertEqual((name, font, source), ("Dark One", None, "discovery"))

    def test_pick_with_nothing_left_is_empty_not_none(self):
        # None means the DB call failed (store.call); () means nothing fits.
        self.assertEqual(self.main["pick"](self.db, "light", {"Light One", "Yellow"}), ())

    def test_icon_font_follows_its_file_not_the_random_font_pool(self):
        fonts = os.path.join(self.tmp, "fonts")
        os.makedirs(fonts)
        self.main["FONT_DIRS"] = [fonts]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(self.main["nonascii_font"](self.db))  # not installed
        open(os.path.join(fonts, self.main["NONASCII_FONT_FILES"][0]), "w").close()
        font = self.main["DEFAULT_NONASCII_FONT"]
        self.assertEqual(self.main["nonascii_font"](self.db), font)
        # Disabled only means "not a random main font": still the icon font.
        self.db.execute("UPDATE fonts SET enabled = 0 WHERE name = ?", (font,))
        self.assertEqual(self.main["nonascii_font"](self.db), font)

    def test_exits_when_iterm2_closes_the_connection(self):
        async def closed():
            return None

        connection = types.SimpleNamespace(websocket=types.SimpleNamespace(wait_closed=closed))
        store = self.main["Store"](self.db_path)
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(store.call(self.main["read_config"]))  # opens its connection
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(os, "_exit", side_effect=SystemExit) as exit_now,
            self.assertRaises(SystemExit),
        ):
            asyncio.run(self.main["exit_on_disconnect"](connection, store))
        with self.assertRaises(sqlite3.ProgrammingError):
            store.db.execute("SELECT 1")  # closed first, so SQLite cleans up its -wal/-shm
        # os._exit, not sys.exit: unwinding would unsubscribe the monitors over the closed
        # connection.
        exit_now.assert_called_once_with(0)

    def test_change_ends_the_old_launch_and_close_ends_the_new(self):
        self.main["now"] = lambda: "2026-11-05 12:00:00"  # no second boundary mid-test
        launch = ("Dark One", "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        self.record(*launch)
        self.main["record_end"](self.db, "s1", "changed")
        self.record("Dark Two", *launch[1:])
        self.main["record_end"](self.db, "s1")
        rows = self.db.execute("SELECT preset, end_reason, duration_s FROM launches ORDER BY id")
        self.assertEqual(rows.fetchall(), [("Dark One", "changed", 0), ("Dark Two", "closed", 0)])

    def test_startup_ends_only_launches_whose_session_is_gone(self):
        self.main["now"] = lambda: "2026-11-05 11:00:00"  # a previous run
        launch = ("Dark One", "Monaco", 13, "dark", "discovery", None, "Default")
        self.record(*launch, "still-open")
        self.record(*launch, "gone")
        self.main["now"] = lambda: "2026-11-05 12:30:00"
        self.record(*launch, "new")  # colored since this run started, so not in live_ids
        ended = self.main["close_stale"](self.db, {"still-open"}, "2026-11-05 12:00:00")
        self.assertEqual(ended, 1)
        rows = self.db.execute("SELECT session_id, end_reason FROM launches ORDER BY id")
        self.assertEqual(
            rows.fetchall(), [("still-open", None), ("gone", "unknown"), ("new", None)]
        )

    def test_changed_looks_count_apart_and_stay_out_of_the_average(self):
        for duration, reason in ((60, "closed"), (120, "closed"), (2, "changed")):
            self.db.execute(
                "INSERT INTO launches (launched_at, preset, font, size, mode, source, "
                "duration_s, end_reason) VALUES ('2026-10-26 12:00:00', 'Dark One', 'Monaco', "
                "13, 'dark', 'discovery', ?, ?)",
                (duration, reason),
            )
        row = self.db.execute(
            "SELECT launches, changed_launches, avg_duration_s, total_duration_s "
            "FROM launch_stats WHERE preset = 'Dark One'"
        )
        self.assertEqual(row.fetchone(), (3, 1, 90, 182))
        stats, stars = self.main["preset_info"](self.db, "Dark One", "Monaco")
        self.assertEqual((stats, stars), ((3, "2026-10-26 12:00:00", 1), None))
        self.assertIsNone(self.main["preset_info"](self.db, "Dark Two", "Monaco")[0])

    def test_reroll_replaces_the_look_and_never_repeats_it(self):
        self.fixing("0")  # Murky (unreadable) stays out of the re-roll
        self.tab({"user.color_preset": "Dark One"})
        color = self.main["color_one_at_a_time"]  # colors, then records the launch
        self.record("Dark One", "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        self.run_quietly(color(None, self.app, self.store, self.session))  # has a look: left alone
        self.profile.async_set_color_preset.assert_not_called()
        self.run_quietly(color(None, self.app, self.store, self.session, reroll=True))
        self.profile.async_set_color_preset.assert_called_once_with("Dark Two")
        self.assertEqual(self.variables["user.color_preset"], "Dark Two")
        rows = self.db.execute("SELECT preset, end_reason FROM launches ORDER BY id")
        self.assertEqual(rows.fetchall(), [("Dark One", "changed"), ("Dark Two", None)])

    def test_a_favorite_warns_again_when_its_preset_goes_missing_after_coming_back(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        rows = self.main["read_favorites"](self.db).rows
        favorite_profiles = self.main["favorite_profiles"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            favorite_profiles(rows, {}, "Default", None)  # not installed: warned
            favorite_profiles(rows, {}, "Default", None)  # still not: once
            favorite_profiles(rows, {"Dark One": DARK}, "Default", None)  # back
            favorite_profiles(rows, {}, "Default", None)  # gone again: warned again
        self.assertEqual(out.getvalue().count("isn't an installed preset"), 2)

    def test_a_current_profiles_file_clears_a_half_written_copy(self):
        self.export_prefs({"Custom Color Presets": {"Dark One": DARK}})
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.assertIsNotNone(self.write_profiles())
        folder, name = os.path.split(self.main["DYNAMIC_PATH"])
        partial = os.path.join(folder, f".{name}.partial")
        open(partial, "w").close()  # a run that quit between the write and the rename
        self.assertIsNone(self.write_profiles())  # current: nothing written
        self.assertFalse(os.path.exists(partial))

    def test_a_deleted_profiles_file_is_written_again(self):
        self.export_prefs({"Custom Color Presets": {"Dark One": DARK}})
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.main["_profiles_state"][0] = None
        self.assertEqual(self.sync_now(), 1)
        self.assertIsNone(self.sync_now())  # current: nothing to do
        os.remove(self.main["DYNAMIC_PATH"])  # deleted by hand
        self.assertEqual(self.sync_now(), 1)
        self.assertTrue(os.path.exists(self.main["DYNAMIC_PATH"]))

    def test_font_label_edits_trigger_a_profiles_rewrite(self):
        self.export_prefs({"Custom Color Presets": {"Dark One": DARK}})
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.main["_profiles_state"][0] = None
        self.assertIsNotNone(self.sync_now())
        self.assertIsNone(self.sync_now())
        self.db.execute("UPDATE fonts SET label = 'Mo' WHERE name = 'Monaco'")
        self.assertIsNotNone(self.sync_now())

    def test_never_show_hides_and_rating_unhides_keeping_mode(self):
        self.db.execute("INSERT INTO overrides (name, mode) VALUES ('Dark One', 'light')")
        self.assertTrue(self.main["never_show"](self.db, "Dark One"))
        self.assertNotIn("Dark One", self.discovery("light"))
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 5)
        row = self.db.execute("SELECT mode, include FROM overrides WHERE name = 'Dark One'")
        self.assertEqual(row.fetchone(), ("light", None))

    def test_with_adjustments_off_rating_a_hidden_unreadable_preset_forces_it_in(self):
        self.fixing("0")
        self.main["never_show"](self.db, "Murky")
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 2)
        self.main["remove_favorite"](self.db, "Murky", "Monaco")
        self.assertIn("Murky", self.discovery("dark"))

    def test_second_rating_in_one_second_saves_but_skips_history(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 3)
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.assertEqual(self.db.execute("SELECT stars FROM favorites").fetchall(), [(4,)])
        # Both events share (at, preset, font, action): history keeps the first only.
        self.assertEqual(self.db.execute("SELECT count(*) FROM ratings").fetchone()[0], 1)

    def test_sync_profiles_writes_the_favorites_file(self):
        prefs = {
            "Custom Color Presets": {n: PRESETS[n] for n in ("Dark One", "Light One", "Yellow")},
            "Default Bookmark Guid": "BASE",
            "New Bookmarks": [{"Guid": "OTHER", "Name": "Other"}, {"Guid": "BASE", "Name": "Base"}],
        }
        self.export_prefs(prefs)
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.main["save_favorite"](self.db, "Light One", "Menlo-Regular", 12.5, 2)
        self.main["save_favorite"](self.db, "Yellow", "Monaco", 13, 3)
        self.main["never_show"](self.db, "Yellow")  # hidden: no profile
        self.db.execute(  # its preset isn't installed: no profile
            "INSERT INTO favorites (preset, font, size, stars, saved_at) "
            "VALUES ('Gone', 'Monaco', 13, 5, '2026-10-21 00:00:00')"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.write_profiles(), 2)
        with open(self.main["DYNAMIC_PATH"], "rb") as f:
            profiles = {p["Name"]: p for p in plistlib.load(f)["Profiles"]}
        self.assertEqual(set(profiles), {"Dark One - Monaco", "Light One - Menlo"})
        dark = profiles["Dark One - Monaco"]
        # Pinned: a changed GUID_NAMESPACE or key format would orphan every user's profiles.
        self.assertEqual(dark["Guid"], "E9B9D727-497D-5735-8B63-7E1716D30663")
        self.assertEqual(dark["Dynamic Profile Parent Name"], "Base")
        self.assertEqual(dark["Normal Font"], "Monaco 13")
        self.assertEqual(dark["Non Ascii Font"], "MesloLGSNFM-Regular 13")
        self.assertEqual(dark["Tags"], ["Favorites"])
        self.assertEqual(dark["Background Color"], DARK["Background Color"])
        self.assertEqual(profiles["Light One - Menlo"]["Normal Font"], "Menlo-Regular 12.5")
        self.assertIsNone(self.write_profiles())  # unchanged: no rewrite
        visible = [n for n in os.listdir(self.data) if not n.startswith(".")]
        self.assertNotIn("dynamic.plist.partial", visible)

    def test_favorite_guid_is_stable(self):
        guid = self.main["favorite_guid"]("Dark One", "Monaco")
        self.assertEqual(guid, self.main["favorite_guid"]("Dark One", "Monaco"))
        self.assertNotEqual(guid, self.main["favorite_guid"]("Dark One", "Menlo-Regular"))


class SnapshotTest(DbTest):
    def snapshots(self):
        return sorted(glob.glob(os.path.join(self.data, "backups", "schemes-*.db")))

    def run_ticks(self, store, before_each):
        """Run snapshot_periodically for len(before_each) ticks, calling before_each[i]() first."""
        ticks = iter(before_each)

        async def sleep(_seconds):
            step = next(ticks, None)
            if step is None:
                raise Stop
            step()

        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(Stop):
            asyncio.run(self.main["snapshot_periodically"](store, 0, sleep))
        return out.getvalue()

    def backdate(self):
        """Make every snapshot 25 hours old, by name and by mtime (either may be read)."""
        day_ago = time.time() - 25 * 3600
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(day_ago))
        for i, path in enumerate(self.snapshots()):
            old = os.path.join(os.path.dirname(path), f"schemes-{stamp}-{i + 100}.db")
            os.rename(path, old)
            os.utime(old, (day_ago, day_ago))

    def test_snapshots_keep_coming_while_iterm2_stays_open(self):
        store = self.main["Store"](self.db_path)
        before = len(self.snapshots())
        nothing = lambda: None  # noqa: E731  (a no-op tick, beside the two methods it sits with)
        self.run_ticks(store, [self.backdate, nothing, self.backdate])
        # Tick 1: due (none, or a day old); tick 2: one is fresh, so none; tick 3: due again.
        self.assertEqual(len(self.snapshots()), before + 2)

    def test_only_old_partial_copies_are_cleaned_up(self):
        folder = os.path.join(self.tmp, "partials")
        os.makedirs(folder)
        old, fresh = (os.path.join(folder, f"schemes-{n}.db.partial") for n in "ab")
        for path in (old, fresh):
            open(path, "w").close()
        two_hours_ago = time.time() - 7200
        os.utime(old, (two_hours_ago, two_hours_ago))
        srcp_db_backup.remove_stale_partials(folder)
        self.assertEqual(os.listdir(folder), [os.path.basename(fresh)])  # may be in progress

    def test_a_snapshot_on_a_filesystem_without_hard_links_still_finishes(self):
        with mock.patch.object(srcp_db_backup.os, "link", side_effect=PermissionError(1, "no")):
            got = srcp_db_backup.snapshot(self.db_path)
        self.assertTrue(os.path.exists(got))
        self.assertEqual(glob.glob(os.path.join(os.path.dirname(got), "*.partial")), [])

    def test_a_snapshot_finishing_under_the_same_name_is_never_overwritten(self):
        folder = os.path.join(self.data, "backups")
        then = time.strptime("20261105T120000Z", "%Y%m%dT%H%M%SZ")
        other = os.path.join(folder, "schemes-20261105T120000Z.db")
        real = sqlite3.connect(self.db_path)
        self.addCleanup(real.close)

        class Racing:  # copies the DB, then another snapshot finishes under the same name
            def backup(self, out):
                real.backup(out)
                with open(other, "w") as f:
                    f.write("the other snapshot")

        with mock.patch.object(srcp_db_backup.time, "gmtime", return_value=then):
            got = srcp_db_backup.snapshot(self.db_path, src=Racing())
        self.assertEqual(got, other[: -len(".db")] + "-1.db")
        with open(other) as f:
            self.assertEqual(f.read(), "the other snapshot")
        self.assertEqual(glob.glob(os.path.join(folder, "*.partial")), [])

    def test_a_snapshot_being_written_in_the_same_second_keeps_its_name(self):
        folder = os.path.join(self.tmp, "claims")
        os.makedirs(folder)
        stem = os.path.join(folder, "schemes-20261105T120000Z")
        open(stem + ".db.partial", "w").close()  # another snapshot is writing this one now
        open(stem + "-1.db", "w").close()  # and one finished a moment ago
        self.assertEqual(srcp_db_backup.claim(stem), stem + "-2.db")
        self.assertTrue(os.path.exists(stem + "-2.db.partial"))  # taken, so not handed out again
        self.assertEqual(srcp_db_backup.claim(stem), stem + "-3.db")

    def make_snapshots(self, names):
        folder = os.path.join(self.tmp, "rotation")
        os.makedirs(folder)
        for i, name in enumerate(names):
            path = os.path.join(folder, name)
            open(path, "w").close()
            scrambled = time.time() - 1000 * ((i * 7) % len(names))  # mtime says nothing
            os.utime(path, (scrambled, scrambled))
        return folder

    def test_rotation_goes_by_the_name_not_the_file_time(self):
        names = ["schemes-20261101T000000Z.db"]
        names += [f"schemes-20261102T000000Z{s}.db" for s in ("", "-1", "-2", "-10")]
        future = "schemes-29990101T000000Z.db"
        others = ["manual-before-upgrade.db", "schemes-old.db", "schemes-20261399T000000Z.db"]
        folder = self.make_snapshots([*names, future, *others])
        srcp_db_backup.prune(folder, keep=3)
        kept = sorted(os.listdir(folder))
        # The 3 newest by name (-2 and -10 after -1: numbers, not text), plus what isn't ours.
        expected = [*names[-3:], future, *others]
        self.assertEqual(kept, sorted(expected))

    def test_pruning_never_takes_every_snapshot_and_tolerates_a_race(self):
        folder = self.make_snapshots([f"schemes-202610{d:02d}T000000Z.db" for d in (1, 2, 3)])
        with self.assertRaises(ValueError):
            srcp_db_backup.prune(folder, keep=0)
        self.assertEqual(len(os.listdir(folder)), 3)
        real_remove = os.remove

        def already_gone(path):  # the other pruner got there first
            real_remove(path)
            raise FileNotFoundError(path)

        with mock.patch.object(srcp_db_backup.os, "remove", already_gone):
            srcp_db_backup.prune(folder, keep=1)
        self.assertEqual(os.listdir(folder), ["schemes-20261003T000000Z.db"])

    def test_a_future_dated_snapshot_neither_counts_nor_gets_hourly_ones_pruned(self):
        real = [f"schemes-202610{d:02d}T000000Z.db" for d in range(1, 15)]  # 14 real ones
        folder = self.make_snapshots([*real, "schemes-29990101T000000Z.db"])
        db_path = os.path.join(os.path.dirname(folder), "rotation.db")
        sqlite3.connect(db_path).close()
        backups = os.path.join(os.path.dirname(db_path), "backups")
        shutil.copytree(folder, backups)
        with contextlib.redirect_stdout(io.StringIO()):
            made = srcp_db_backup.snapshot_if_due(db_path)  # the future one isn't "newest"
        self.assertIsNotNone(made)
        left = sorted(os.listdir(backups))
        self.assertIn("schemes-29990101T000000Z.db", left)
        self.assertEqual(len([n for n in left if n.startswith("schemes-2026")]), 14)
        self.assertNotIn(real[0], left)  # the oldest real one made room for the new one
        self.assertIsNone(srcp_db_backup.snapshot_if_due(db_path))  # now fresh: none due

    def test_the_main_script_warns_once_about_future_dated_snapshots(self):
        backups = os.path.join(self.data, "backups")
        os.makedirs(backups, exist_ok=True)
        open(os.path.join(backups, "schemes-29990101T000000Z.db"), "w").close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.main["daily_snapshot"](self.db)
            self.main["daily_snapshot"](self.db)
        self.assertEqual(out.getvalue().count("time in the future"), 1)

    def test_a_failed_check_does_not_stop_later_ones(self):
        calls = []

        class FlakyStore:
            async def call(self, fn, *args):
                calls.append(fn.__name__)
                if len(calls) == 1:
                    raise RuntimeError("boom")

        self.run_ticks(FlakyStore(), [lambda: None, lambda: None])
        self.assertEqual(calls, ["daily_snapshot", "daily_snapshot"])

    def test_a_failing_check_warns_once_until_one_succeeds(self):
        results = iter(["fail", "fail", "ok", "fail"])

        class Store:
            async def call(self, fn, *args):
                if next(results) == "fail":
                    raise RuntimeError("boom")

        out = self.run_ticks(Store(), [lambda: None] * 4)
        # Two failures in a row: one line; a success re-arms it, so the next failure shows.
        self.assertEqual(out.count("snapshot check failed"), 2)


class AsyncHelpersTest(DbTest):
    def test_the_lock_waits_for_an_exiting_copy_then_gives_up(self):
        os.makedirs(self.data, exist_ok=True)
        held = open(self.main["LOCK_PATH"], "w")  # noqa: SIM115  (released below)
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquire = self.main["acquire_lock"]
        self.assertIsNone(asyncio.run(acquire(wait_s=0.3, every_s=0.05)))

        async def release_then_acquire():
            asyncio.get_running_loop().call_later(0.1, held.close)  # the old copy exits
            return await acquire(wait_s=2, every_s=0.05)

        got = asyncio.run(release_then_acquire())
        self.assertIsNotNone(got)
        got.close()

    def test_a_locked_db_is_reported_as_locked_not_as_old(self):
        store = self.main["Store"](self.db_path)

        def locked(db):
            raise sqlite3.OperationalError("database is locked")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(asyncio.run(store.call(locked)))
        self.assertIn("DB locked: database is locked", out.getvalue())
        self.assertNotIn("rebuild", out.getvalue())

    def test_a_failed_rollback_keeps_the_error_and_reconnects(self):
        store = self.main["Store"](self.db_path)

        def boom(db):
            db.execute("BEGIN IMMEDIATE")
            store.db = FailingRollback(db)  # ROLLBACK itself fails now
            raise sqlite3.OperationalError("disk I/O error")

        class FailingRollback:
            in_transaction = True

            def __init__(self, real):
                self.real = real

            def execute(self, sql, *args):
                if sql == "ROLLBACK":
                    self.real.execute("ROLLBACK")  # leave the real DB clean for the next call
                    raise sqlite3.OperationalError("database disk image is malformed")
                return self.real.execute(sql, *args)

            def close(self):
                self.real.close()

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(asyncio.run(store.call(boom)))
        self.assertIn("disk I/O error", out.getvalue())  # the original error, not the rollback's
        self.assertIn("rollback failed", out.getvalue())
        self.assertIsNone(store.db)  # the next call reconnects
        self.assertEqual(asyncio.run(store.call(lambda db: 1)), 1)


class PickOddsTest(DbTest):
    """pick()'s odds as docs/REFERENCE.md "How a look is picked" states them.

    The fixture's discovery pools: dark = Dark One, Dark Two; light = Light One, Yellow
    (Yellow has a colored background). A fixed seed keeps every run the same.
    """

    N = 4000

    def setUp(self):
        super().setUp()
        self.main["random"].seed(20261024)

    def config(self, **values):
        for key, value in values.items():
            self.db.execute("UPDATE config SET value = ? WHERE key = ?", (str(value), key))

    def picks(self, mode):
        return [self.main["pick"](self.db, mode, set()) for _ in range(self.N)]

    def share(self, picks, test):
        return sum(1 for p in picks if test(p)) / len(picks)

    def test_no_favorites_means_discovery_every_time(self):
        self.assertTrue(all(p[2] == "discovery" for p in self.picks("dark")))

    def test_favorite_share_ramps_with_favorite_presets(self):
        self.main["save_favorite"](self.db, "Light One", "Monaco", 13, 3)
        # One favorite preset of the default target 10: 0.5 x 1/10 = 5%.
        self.assertAlmostEqual(
            self.share(self.picks("light"), lambda p: p[2] == "favorite"), 0.05, delta=0.015
        )
        # At the target, the full favorite_share.
        self.config(favorite_target=1)
        self.assertAlmostEqual(
            self.share(self.picks("light"), lambda p: p[2] == "favorite"), 0.5, delta=0.03
        )
        # More fonts of the same preset don't speed the ramp (it counts presets).
        self.config(favorite_target=10)
        self.main["save_favorite"](self.db, "Light One", "Menlo-Regular", 13, 3)
        self.assertAlmostEqual(
            self.share(self.picks("light"), lambda p: p[2] == "favorite"), 0.05, delta=0.015
        )

    def test_favorites_are_weighted_by_stars_per_preset_and_font(self):
        self.config(favorite_share=1, favorite_target=1)  # always a favorite
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 1)
        self.main["save_favorite"](self.db, "Dark Two", "Monaco", 13, 5)
        picks = self.picks("dark")
        five = self.share(picks, lambda p: p[0] == "Dark Two")
        self.assertAlmostEqual(five / (1 - five), 5, delta=0.6)  # 5 stars : 1 star
        self.assertEqual({(p[2], p[3]) for p in picks if p[0] == "Dark Two"}, {("favorite", 5)})
        # A second saved font is a second ticket: 1 + 1 stars against 5.
        self.main["save_favorite"](self.db, "Dark One", "Menlo-Regular", 13, 1)
        five = self.share(self.picks("dark"), lambda p: p[0] == "Dark Two")
        self.assertAlmostEqual(five / (1 - five), 2.5, delta=0.3)

    def test_colored_backgrounds_get_colorful_weight(self):
        yellow = self.share(self.picks("light"), lambda p: p[0] == "Yellow")
        self.assertAlmostEqual(yellow, 0.75, delta=0.03)  # default weight 3: 3 / (3 + 1)
        self.config(colorful_weight=1)
        yellow = self.share(self.picks("light"), lambda p: p[0] == "Yellow")
        self.assertAlmostEqual(yellow, 0.5, delta=0.03)

    def test_recent_discovery_picks_are_skipped_until_all_are_recent(self):
        def launch(preset):
            self.record(preset, None, 13, "dark", "discovery", None, "p", preset)

        self.fixing("0")  # Murky (unreadable) stays out
        launch("Dark One")
        self.assertEqual({p[0] for p in self.picks("dark")}, {"Dark Two"})
        launch("Dark Two")  # every preset is recent: the window falls back to all of them
        self.assertEqual({p[0] for p in self.picks("dark")}, {"Dark One", "Dark Two"})
        self.config(recent_window=1)  # only Dark Two is recent now
        self.assertEqual({p[0] for p in self.picks("dark")}, {"Dark One"})

    def test_odds_report_matches_pick(self):
        # .tools/srcp_odds.py computes pick()'s odds exactly; check it against pick() itself.
        self.config(favorite_target=2)
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 1)
        self.main["save_favorite"](self.db, "Dark One", "Menlo-Regular", 13, 4)
        self.main["save_favorite"](self.db, "Light One", "Monaco", 13, 3)
        self.record("Dark Two", None, 13, "dark", "discovery", None, "p", "s")
        for mode in ("light", "dark"):
            expected = srcp_odds.odds(self.db, mode)
            picks = self.picks(mode)
            got = self.share(picks, lambda p: p[2] == "favorite")
            self.assertAlmostEqual(got, expected["chance_favorite"], delta=0.02, msg=mode)
            for fav in expected["favorites"]:
                got = self.share(picks, lambda p, f=fav: (p[0], p[1]) == (f["preset"], f["font"]))
                self.assertAlmostEqual(got, fav["chance"], delta=0.02, msg=fav)
            for name, chance in expected["new"]["presets"].items():
                got = self.share(picks, lambda p, n=name: p[0] == n and p[2] == "discovery")
                self.assertAlmostEqual(got, chance, delta=0.02, msg=name)
            total = sum(expected["fonts"].values())
            self.assertAlmostEqual(total, expected["chance_favorite"] + expected["chance_new"])

    def test_a_favorite_whose_font_was_disabled_gets_a_random_font(self):
        self.config(favorite_share=1, favorite_target=1)
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.main["save_favorite"](self.db, "Dark Two", "Monaco", 13, 4)
        self.assertEqual({p[1] for p in self.picks("dark")}, {"Monaco"})
        self.db.execute("UPDATE fonts SET enabled = 0 WHERE name = 'Monaco'")
        self.assertEqual({p[1] for p in self.picks("dark")}, {None})  # caller picks a random font


class ExportTest(DbTest):
    def export(self):
        out = io.StringIO()
        srcp_export_curation.export(self.db, out)
        return out.getvalue()

    def restore(self, sql, db_path):
        subprocess.run(["sqlite3", db_path], input=sql + sql, text=True, check=True)

    def test_restore_twice_is_a_no_op_and_new_fonts_arrive_disabled(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.db.execute(
            "INSERT INTO fonts (name, enabled, label) VALUES ('Other-Regular', 1, 'Other')"
        )
        sql = self.export()
        target = os.path.join(self.tmp, "target", "schemes.db")
        with contextlib.redirect_stdout(io.StringIO()):
            rows, _ = build.build_rows(PRESETS, self.repo)
            build.write_db(target, rows)
        self.restore(sql, target)  # the same export read twice in one session
        db = sqlite3.connect(target)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT count(*) FROM ratings").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT stars FROM favorites").fetchall(), [(4,)])
        enabled = db.execute("SELECT enabled FROM fonts WHERE name = 'Other-Regular'")
        self.assertEqual(enabled.fetchone(), (0,))

    def test_a_name_with_a_quote_restores(self):
        self.main["save_favorite"](self.db, "O'Hara", "Monaco", 13, 5)
        sql = self.export()
        target = os.path.join(self.tmp, "target", "schemes.db")
        with contextlib.redirect_stdout(io.StringIO()):
            rows, _ = build.build_rows(PRESETS, self.repo)
            build.write_db(target, rows)
        self.restore(sql, target)
        db = sqlite3.connect(target)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT preset FROM favorites").fetchall(), [("O'Hara",)])

    def test_fonts_you_disabled_carry_over_but_missing_ones_do_not(self):
        self.db.execute("UPDATE fonts SET enabled = 0, missing = 0 WHERE name = 'Monaco'")
        self.db.execute("UPDATE fonts SET enabled = 0, missing = 1 WHERE name = 'Menlo-Regular'")
        self.db.execute("INSERT INTO fonts (name, enabled) VALUES ('Other-Regular', 1)")
        sql = self.export()
        target = os.path.join(self.tmp, "target", "schemes.db")
        folder = os.path.join(self.tmp, "fonts")
        os.makedirs(folder)
        for files in build.SEED_FONTS.values():
            open(os.path.join(folder, files[0]), "w").close()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(build, "FONT_DIRS", [folder]),
        ):
            rows, _ = build.build_rows(PRESETS, self.repo)
            build.write_db(target, rows)
        self.restore(sql, target)
        db = sqlite3.connect(target)
        self.addCleanup(db.close)
        fonts = {n: (e, m) for n, e, m in db.execute("SELECT name, enabled, missing FROM fonts")}
        self.assertEqual(fonts["Monaco"], (0, 0))
        self.assertEqual(fonts["Menlo-Regular"], (1, 0))  # installed in the target
        self.assertEqual(fonts["Other-Regular"], (0, 1))


def with_colors(base, **changes):
    """A copy of a test preset with some colors replaced: key=(r, g, b), spaces in key → _."""
    out = {k: dict(v) for k, v in base.items()}
    for key, rgb in changes.items():
        out[key.replace("_", " ")] = {**out[key.replace("_", " ")], **color(*rgb)}
    return out


class ColorFixTest(DbTest):
    """Color adjustments: the build's fix_colors(), and how the script applies them."""

    CASES: ClassVar[dict[str, dict]] = {
        # Light-on-light accents, as in Coffee Theme: only darker can reach the target.
        "light on light": with_colors(
            LIGHT, Background_Color=(0.95, 0.92, 0.88), Ansi_3_Color=(0.95, 0.93, 0.6)
        ),
        "dark ANSI 8 on dark": with_colors(DARK, Ansi_8_Color=(0.05, 0.05, 0.05)),
        "ANSI 8 equal to bg": with_colors(DARK, Ansi_8_Color=(0.1, 0.1, 0.1)),
        # White text on red, as in Hot Dog Stand: white tops out at 4.0, black reaches 5.25.
        "black beats white": with_colors(
            DARK, Background_Color=(1.0, 0.0, 0.0), Foreground_Color=(1.0, 1.0, 1.0)
        ),
        "saturated accent leaves gamut": with_colors(
            DARK, Background_Color=(0.05, 0.05, 0.25), Ansi_4_Color=(0.0, 0.0, 1.0)
        ),
        "unreadable text": UNREADABLE,
        # Two pairs of near-identical readable accents: only 4 of 6 distinct until some move.
        "look-alike accents": with_colors(
            DARK,
            Ansi_2_Color=(0.9, 0.21, 0.21),
            Ansi_6_Color=(0.9, 0.31, 0.9),
            Ansi_8_Color=(0.5, 0.5, 0.5),
        ),
    }

    def test_every_bad_color_reaches_its_target_and_nothing_else_moves(self):
        for name, colors in self.CASES.items():
            with self.subTest(name):
                fixes = build.fix_colors(colors)
                self.assertTrue(fixes, "something to fix")
                bg = build.rgb(colors["Background Color"])
                after = build.apply_fixes(colors, fixes)
                for key, target in build.FIX_TARGETS.items():
                    reachable = max(
                        build.contrast_ratio((1, 1, 1), bg), build.contrast_ratio((0, 0, 0), bg)
                    )
                    if reachable >= target:
                        got = build.contrast_ratio(build.rgb(after[key]), bg)
                        self.assertGreaterEqual(got, target, key)
                for key, target in build.FIX_TARGETS.items():
                    already = build.contrast_ratio(build.rgb(colors[key]), bg) >= target
                    # Separating look-alikes may move readable accents (ANSI 1-6), nothing else.
                    separating = name == "look-alike accents" and key in build.ACCENT_KEYS
                    if already and not separating:
                        self.assertNotIn(key, fixes, key)
                for key in ("Background Color", "Ansi 0 Color", "Ansi 7 Color", "Ansi 15 Color"):
                    self.assertNotIn(key, fixes)
                for key, (r, g, b, *_) in fixes.items():
                    self.assertTrue(all(0 <= v <= 1 for v in (r, g, b)), key)

    def test_hue_and_the_monochrome_check_are_kept(self):
        for name, colors in self.CASES.items():
            with self.subTest(name):
                fixes = build.fix_colors(colors)
                after = build.apply_fixes(colors, fixes)
                self.assertEqual(
                    build.classify(after)["monochrome"], build.classify(colors)["monochrome"]
                )
                for key, (r, g, b, *_) in fixes.items():
                    _, a1, b1 = build.oklab(build.rgb(colors[key]))
                    _, a2, b2 = build.oklab((r, g, b))
                    if min(math.hypot(a1, b1), math.hypot(a2, b2)) < build.CHROMA_MIN:
                        continue  # a gray has no hue to keep
                    drift = math.degrees(math.atan2(b1, a1) - math.atan2(b2, a2))
                    self.assertLess(abs((drift + 180) % 360 - 180), 1.0, key)

    @staticmethod
    def as_p3(colors):
        """The same numbers, tagged Display P3 (as many iTerm2 presets are)."""
        return {k: {**v, "Color Space": "P3"} for k, v in colors.items()}

    def test_the_p3_conversion_is_the_css_color_4_one(self):
        m = srcp_schema.P3_TO_SRGB
        for row in m:
            self.assertAlmostEqual(sum(row), 1.0, places=12)  # white stays white
        # From the spec's fractions (P3 -> XYZ -> sRGB), as published linear P3 -> sRGB tables give.
        self.assertAlmostEqual(m[0][0], 1.2249401762805596, places=12)
        self.assertAlmostEqual(m[2][2], 1.0982736001409663, places=12)
        for rgb in ((0.3, 0.6, 0.2), (1, 1, 1), (0.05, 0.0, 0.9)):  # the inverse undoes it
            back = srcp_schema.p3_to_srgb(srcp_schema.srgb_to_p3(rgb))
            for v, w in zip(back, rgb, strict=True):
                self.assertAlmostEqual(v, w, places=12)
        red = srcp_schema.p3_to_srgb((1, 0, 0))  # wider than sRGB: outside its gamut
        self.assertEqual([round(v, 4) for v in red], [1.0931, -0.2267, -0.1501])

    def test_p3_colors_are_judged_as_they_look(self):
        red, bg = DARK["Ansi 1 Color"], DARK["Background Color"]
        wide = {**red, "Color Space": "P3"}  # the same numbers in P3: a more vivid red
        self.assertNotEqual(build.rgb(wide), build.rgb(red))
        p3 = build.contrast_ratio(build.rgb(wide), build.rgb(bg))
        self.assertNotAlmostEqual(p3, build.contrast_ratio(build.rgb(red), build.rgb(bg)), 2)
        grey = {**DARK["Foreground Color"], "Color Space": "P3"}  # greys look the same in both
        self.assertEqual([round(v, 9) for v in build.rgb(grey)], [0.9, 0.9, 0.9])

    def test_fixed_p3_colors_stay_p3_meet_their_targets_and_stay_fixed(self):
        p3 = self.as_p3(UNREADABLE)
        fixes = build.fix_colors(p3)
        self.assertIn("Foreground Color", fixes)
        after = build.apply_fixes(p3, fixes)
        bg = build.rgb(after["Background Color"])
        for key in fixes:
            with self.subTest(key=key):
                self.assertEqual(after[key]["Color Space"], "P3")
                shown = build.contrast_ratio(build.rgb(after[key]), bg)  # as iTerm2 shows it
                self.assertGreaterEqual(shown, build.FIX_TARGETS[key] - build.CONTRAST_EPS)
                self.assertAlmostEqual(fixes[key][4], shown, places=2)  # "after" is the truth
        self.assertEqual(build.fix_colors(after), {})  # a fixed point, P3 rounding and all

    def test_a_moved_color_is_stored_in_its_own_color_space(self):
        target = (0.2, 0.4, 0.9)  # where the sRGB math moved a color to
        p3 = {**color(0.1, 0.1, 0.5), "Color Space": "P3"}
        kept = build.stored(p3, target)
        expected = srcp_schema.srgb_to_p3(target)  # P3 numbers that show as the target
        self.assertEqual(kept, tuple(round(v, 6) for v in expected))
        self.assertNotEqual([round(v, 3) for v in kept], [round(v, 3) for v in target])
        self.assertEqual(build.stored(color(0.1, 0.1, 0.5), target), target)  # sRGB: as is

    def test_p3_hashes_agree_between_the_build_and_the_main_script(self):
        p3 = self.as_p3(UNREADABLE)
        live = {k: build.rgb({**v, "Color Space": None}) for k, v in p3.items()}  # raw values
        keys = set(p3)
        self.assertEqual(self.main["colors_hash"](live, keys), build.color_hash(p3))
        self.assertNotEqual(self.main["colors_hash"](live), build.color_hash(p3))
        # A P3 preset with the same numbers as an sRGB one isn't its twin.
        with contextlib.redirect_stdout(io.StringIO()):
            rows, _ = build.build_rows({"Plain": UNREADABLE, "Wide": p3}, self.repo)
        duplicate_of = {r[0]: r[10] for r in rows}  # schemes.duplicate_of
        self.assertEqual(duplicate_of, {"Plain": None, "Wide": None})

    def test_fixing_twice_changes_nothing(self):
        for name, colors in self.CASES.items():
            with self.subTest(name):
                after = build.apply_fixes(colors, build.fix_colors(colors))
                self.assertEqual(build.fix_colors(after), {})
        self.assertEqual(build.fix_colors(LIGHT), {})  # already readable

    def test_look_alike_accents_are_moved_apart_or_left_alone(self):
        colors = self.CASES["look-alike accents"]
        labs = [build.oklab(build.rgb(colors[k])) for k in build.ACCENT_KEYS]
        self.assertLess(build.distinct_accents(labs), build.MIN_DISTINCT_ACCENTS)
        fixes = build.fix_colors(colors)
        after = build.apply_fixes(colors, fixes)
        labs = [build.oklab(build.rgb(after[k])) for k in build.ACCENT_KEYS]
        self.assertGreaterEqual(build.distinct_accents(labs), build.MIN_DISTINCT_ACCENTS)
        self.assertTrue(set(fixes) & set(build.ACCENT_KEYS))
        # If they can't be separated, those moves are dropped and the rest stays a fixed point.
        with mock.patch.object(build, "DISTINCT_MAX_STEPS", 0):
            fixes = build.fix_colors(colors)
            self.assertFalse(set(fixes) & set(build.ACCENT_KEYS))
            self.assertEqual(build.fix_colors(build.apply_fixes(colors, fixes)), {})

    def test_a_bright_color_never_ends_up_identical_to_its_normal_one(self):
        colors = with_colors(DARK, Ansi_4_Color=(0.05, 0.05, 0.3), Ansi_12_Color=(0.06, 0.05, 0.3))
        after = build.apply_fixes(colors, build.fix_colors(colors))
        self.assertNotEqual(build.rgb(after["Ansi 4 Color"]), build.rgb(after["Ansi 12 Color"]))

    def test_build_summary_and_changed_favorites(self):
        rows, report = build.build_rows(PRESETS, self.repo)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            build.print_report(rows, report)
            build.report_changed_favorites({"Murky": None}, {"Murky": "{}", "Dark One": None})
            build.report_changed_favorites(None, {"Murky": "{}"})  # no old DB: nothing to compare
        text = out.getvalue()
        self.assertIn("colors adjusted for readability", text)
        self.assertIn("changed for favorites: Murky", text)
        self.assertEqual(text.count("changed for favorites"), 1)
        self.assertIn("New tabs get these adjustments", text)
        self.assertNotIn("Restart", text)  # a rebuild needs no restart
        self.assertEqual(build.favorite_fixes(os.path.join(self.tmp, "none.db")), None)
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        self.assertIn("Foreground Color", build.favorite_fixes(self.db_path)["Murky"])

    def test_build_stores_adjustments_and_the_gate_on_them(self):
        row = self.db.execute(
            "SELECT passes_gate, passes_gate_fixed, fix_count, fixes FROM schemes "
            "WHERE name = 'Murky'"
        ).fetchone()
        self.assertEqual(row[:2], (0, 1))
        self.assertEqual(row[2], len(json.loads(row[3])))
        self.assertIn("Foreground Color", json.loads(row[3]))
        self.assertEqual(
            self.db.execute(
                "SELECT fix_count, fixes FROM schemes WHERE name = 'Light One'"
            ).fetchone(),
            (0, None),
        )

    def test_the_setting_means_the_same_everywhere(self):
        on = [None, "1", "1.0", " 1 ", "off", "", "nan", "yes"]
        for value in [*on, "0", "0.0", " 0 "]:
            with self.subTest(value=value):
                self.fixing(value)
                expected = value not in ("0", "0.0", " 0 ")
                self.assertEqual("Murky" in self.discovery("dark"), expected)
                fixes, _ = self.main["look_fixes"](self.db, "Murky")
                self.assertEqual(fixes is not None, expected)

    def test_out_of_range_components_dont_stop_the_build(self):
        # A hand-edited preset. All three below 0: unclamped, OKLab's cube root goes complex.
        odd = with_colors(DARK, Ansi_1_Color=(-0.01, -0.01, -0.01), Ansi_2_Color=(0.2, 1.02, 0.2))
        with contextlib.redirect_stdout(io.StringIO()):
            rows, _ = build.build_rows({"Odd": odd}, self.repo)
        self.assertEqual(len(rows), 1)
        self.assertTrue(all(math.isfinite(v) for v in build.oklab(build.rgb(odd["Ansi 1 Color"]))))
        self.assertEqual(build.rgb(odd["Ansi 1 Color"])[0], -0.01)  # the preset isn't changed
        self.assertNotEqual(build.color_hash(odd), build.color_hash(DARK))  # raw values hashed

    def test_colors_hash_matches_the_build(self):
        live = {k: build.rgb(UNREADABLE[k]) for k in build.HASH_KEYS}
        self.assertEqual(self.main["colors_hash"](live), build.color_hash(UNREADABLE))

    def fake_preset(self, colors):
        values = [
            types.SimpleNamespace(
                key=k,
                red=v["Red Component"] * 255,
                green=v["Green Component"] * 255,
                blue=v["Blue Component"] * 255,
            )
            for k, v in colors.items()
        ]
        return types.SimpleNamespace(values=values)

    def test_a_tab_gets_the_adjusted_colors_unless_its_preset_changed(self):
        fixes, built = self.main["look_fixes"](self.db, "Murky")
        preset = self.fake_preset(UNREADABLE)
        with contextlib.redirect_stdout(io.StringIO()):
            n = self.main["adjust_preset"]("Murky", preset, fixes, built)
        self.assertEqual(n, len(json.loads(fixes)))
        fg = next(v for v in preset.values if v.key == "Foreground Color")
        expected = json.loads(fixes)["Foreground Color"][:3]
        self.assertEqual([round(c / 255, 4) for c in (fg.red, fg.green, fg.blue)], expected)
        # Edited in iTerm2 since the build: the hash differs, so nothing is applied.
        changed = self.fake_preset(with_colors(UNREADABLE, Ansi_1_Color=(0.5, 0.1, 0.1)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.main["adjust_preset"]("Murky", changed, fixes, built), 0)
        self.assertIn("changed since the DB was built", out.getvalue())
        self.fixing("0")
        self.assertEqual(self.main["look_fixes"](self.db, "Murky")[0], None)

    def test_favorite_profiles_get_the_adjusted_colors_and_prefs_stay_untouched(self):
        murky = {k: dict(v) for k, v in UNREADABLE.items()}
        murky["Foreground Color"]["Color Space"] = "P3"
        prefs = {"Custom Color Presets": {"Murky": murky}}
        before = plistlib.dumps(prefs)
        self.export_prefs(prefs)
        # The hash marks P3 colors (same numbers, different look), so build from this version.
        self.assertNotEqual(build.color_hash(murky), build.color_hash(UNREADABLE))
        with contextlib.redirect_stdout(io.StringIO()):
            rows, _ = build.build_rows({**PRESETS, "Murky": murky}, self.repo)
            build.write_db(self.db_path, rows)
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.write_profiles(), 1)
        with open(self.main["DYNAMIC_PATH"], "rb") as f:
            fg = plistlib.load(f)["Profiles"][0]["Foreground Color"]
        expected = json.loads(self.scheme_fixes("Murky"))["Foreground Color"][:3]
        self.assertEqual([fg[c] for c in build.COMPONENTS], expected)
        self.assertEqual(fg["Color Space"], "P3")  # kept
        self.assertEqual(plistlib.dumps(prefs), before)  # nothing mutated
        state = self.main["_profiles_state"]
        state[0] = None
        with contextlib.redirect_stdout(io.StringIO()):
            self.sync_now()
            first = state[0]
            self.db.execute("UPDATE schemes SET fixes = '{}' WHERE name = 'Murky'")
            self.sync_now()
        self.assertNotEqual(state[0], first)  # changed adjustments rewrite the profiles

    def scheme_fixes(self, name):
        return self.db.execute("SELECT fixes FROM schemes WHERE name = ?", (name,)).fetchone()[0]

    def test_a_tab_with_an_adjusted_preset_says_so(self):
        for name in ("Dark One", "Dark Two"):  # leave only Murky to pick
            self.main["never_show"](self.db, name)
        self.tab({})
        preset = self.fake_preset(UNREADABLE)
        self.main["get_preset"] = mock.AsyncMock(return_value=preset)
        out = self.run_quietly(self.main["color_session"](None, self.app, self.store, self.session))
        n = len(json.loads(self.scheme_fixes("Murky")))
        line = next(x for x in out.splitlines() if "Murky |" in x)  # random font
        self.assertRegex(line, rf" 13, {self.main['colors_adjusted'](n)} \(\d+ ms\)$")
        self.assertEqual(self.variables["user.adjusted"], n)
        self.profile.async_set_color_preset.assert_called_once_with(preset)
        fg = next(v for v in preset.values if v.key == "Foreground Color")
        self.assertNotEqual(round(fg.red / 255, 4), UNREADABLE["Foreground Color"]["Red Component"])

    def test_a_favorite_profile_session_reports_its_adjustments(self):
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        guid = self.main["favorite_guid"]("Murky", "Monaco")
        fav = self.main["favorite_by_guid"](self.db, guid)
        self.assertEqual(fav[4], len(json.loads(self.scheme_fixes("Murky"))))
        # Its profile got none (the preset changed since the build): the session says none.
        changed = with_colors(UNREADABLE, Ansi_1_Color=(0.5, 0.1, 0.1))
        self.export_prefs({"Custom Color Presets": {"Murky": changed}})
        with contextlib.redirect_stdout(io.StringIO()):
            self.write_profiles()
        self.assertEqual(self.main["_profile_adjusted"]["Murky"], 0)
        self.fixing("0")
        self.assertEqual(self.main["favorite_by_guid"](self.db, guid)[4], 0)

    def test_the_float32_round_trip_keeps_the_hash(self):
        # Values on a .4f boundary (Sandstone presets) format differently after iTerm2's 32-bit
        # floats unless both sides round to 32 bits first.
        colors = with_colors(UNREADABLE, Ansi_1_Color=(0.02745098039215686, 0.5, 0.5))
        live = {
            k: tuple(srcp_schema.float32(c) * 255 / 255 for c in build.rgb(colors[k]))
            for k in build.HASH_KEYS
        }
        self.assertEqual(self.main["colors_hash"](live), build.color_hash(colors))


class TabTest(DbTest):
    """The main script's per-tab paths, with a fake iTerm2 session, app and dialogs."""

    def test_a_new_tab_sets_all_its_variables_in_one_call(self):
        self.fixing("0")
        self.tab({})
        self.run_quietly(self.main["color_session"](None, self.app, self.store, self.session))
        self.assertEqual(self.variable_rpcs, [6])
        self.assertEqual(self.variables["user.mode"], "dark")

    def test_a_new_tab_leaves_the_profiles_refresh_to_its_own_loop(self):
        self.fixing("0")
        self.tab({})
        self.main["sync_and_log"] = mock.AsyncMock()
        self.run_quietly(self.main["color_session"](None, self.app, self.store, self.session))
        self.main["sync_and_log"].assert_not_called()  # sync_periodically does it, off the tab
        self.assertEqual(self.variables["user.mode"], "dark")

    def test_the_periodic_sync_picks_up_an_edit_without_a_tab(self):
        self.export_prefs({"Custom Color Presets": {"Dark One": DARK}})
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 3)  # as if in sqlite3
        ticks = iter([None])

        async def sleep(_seconds):
            if next(ticks, "stop") == "stop":
                raise Stop

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Stop):
            asyncio.run(self.main["sync_periodically"](self.main["Store"](self.db_path), 0, sleep))
        with open(self.main["DYNAMIC_PATH"], "rb") as f:
            names = [p["Name"] for p in plistlib.load(f)["Profiles"]]
        self.assertEqual(names, ["Dark One - Monaco"])

    def test_a_session_is_colored_by_one_call_at_a_time(self):
        self.tab({})
        calls = []
        release = None

        async def slow(connection, app, store, session, reroll=False):
            calls.append(reroll)
            await release.wait()

        self.main["color_session"] = slow
        color_once = self.main["color_one_at_a_time"]

        async def scenario():
            nonlocal release
            release = asyncio.Event()
            first = asyncio.ensure_future(color_once(None, self.app, self.store, self.session))
            await asyncio.sleep(0)
            await color_once(None, self.app, self.store, self.session)  # seen twice: skipped
            again = asyncio.ensure_future(
                color_once(None, self.app, self.store, self.session, reroll=True)
            )
            await asyncio.sleep(0)
            self.assertEqual(calls, [False])  # the re-roll waits for the first look
            release.set()
            await asyncio.gather(first, again)

        self.run_quietly(scenario())
        self.assertEqual(calls, [False, True])
        self.assertEqual(self.main["_coloring"], {})

    def test_a_coloring_that_never_returns_is_given_up(self):
        self.tab({})
        color_once = self.main["color_one_at_a_time"]

        async def hung(connection, app, store, session, reroll=False):
            await asyncio.sleep(3600)  # a lost iTerm2 reply

        self.main["color_session"] = hung
        self.main["COLOR_TIMEOUT_S"] = 0.05
        out = self.run_quietly(color_once(None, self.app, self.store, self.session))
        self.assertIn("took over 0.05 s", out)
        self.assertIn("Change to another profile", out)  # how to fix a half-done tab
        self.assertEqual(self.main["_coloring"], {})  # freed for the next try

    def test_a_locked_db_while_recording_doesnt_give_the_tab_up(self):
        self.fixing("0")
        self.tab({})
        real = self.main["record_launch"]

        def locked(db, launch):  # waits on the DB, as a busy one makes it (up to 5 s)
            time.sleep(1.0)
            real(db, launch)

        self.main["record_launch"] = locked
        self.main["COLOR_TIMEOUT_S"] = 0.5  # shorter than the wait: only the coloring counts
        color_once = self.main["color_one_at_a_time"]
        out = self.run_quietly(color_once(None, self.app, self.store, self.session))
        self.assertNotIn("given up", out)
        self.assertIn("user.color_preset", self.variables)
        self.assertEqual(self.db.execute("SELECT count(*) FROM launches").fetchone(), (1,))

    def test_a_favorite_profile_tab_logs_its_time_and_is_recorded(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.tab({})
        self.profile.original_guid = self.main["favorite_guid"]("Dark One", "Monaco")
        color_once = self.main["color_one_at_a_time"]
        out = self.run_quietly(color_once(None, self.app, self.store, self.session))
        self.assertRegex(out, r"favorite profile 4 ★: Dark One \| Monaco 13(, [^(]+)? \(\d+ ms\)")
        rows = self.db.execute("SELECT preset, source, end_reason FROM launches").fetchall()
        self.assertEqual(rows, [("Dark One", "favorite", None)])

    def test_a_reroll_waits_until_the_previous_one_is_recorded(self):
        self.fixing("0")
        self.tab({"user.color_preset": "Dark One"})
        self.record("Dark One", "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        main, real = self.main, self.store
        picks = []

        class GatedStore:  # the first re-roll's row waits, without holding the DB lock
            def __init__(self):
                self.gate, self.recording = asyncio.Event(), asyncio.Event()

            async def call(self, fn, *args):
                if fn is main["pick_look"]:
                    picks.append(1)
                if fn is main["record_change"] and not self.gate.is_set():
                    self.recording.set()
                    await self.gate.wait()
                return await real.call(fn, *args)

        color_once = self.main["color_one_at_a_time"]

        async def scenario():
            store = GatedStore()
            first = asyncio.ensure_future(
                color_once(None, self.app, store, self.session, reroll=True)
            )
            await store.recording.wait()  # colored, its row not written yet
            second = asyncio.ensure_future(
                color_once(None, self.app, store, self.session, reroll=True)
            )
            await asyncio.sleep(0.05)
            self.assertEqual(len(picks), 1)  # the second waits for the first's row
            store.gate.set()
            await asyncio.gather(first, second)

        self.run_quietly(scenario())
        rows = self.db.execute("SELECT preset, end_reason FROM launches ORDER BY id").fetchall()
        self.assertEqual([r[1] for r in rows], ["changed", "changed", None])
        self.assertEqual(rows[-1][0], self.variables["user.color_preset"])

    def test_a_favorite_whose_saved_font_is_off_says_so(self):
        self.fixing("0")
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.db.execute("UPDATE fonts SET enabled = 0 WHERE name = 'Monaco'")
        self.db.execute(
            "UPDATE config SET value = '1' WHERE key IN ('favorite_share', 'favorite_target')"
        )  # always the favorite
        self.tab({})
        out = self.run_quietly(self.main["color_session"](None, self.app, self.store, self.session))
        self.assertIn("favorite 4 ★ (saved font off; random font): Dark One", out)
        self.assertEqual(self.variables["user.is_favorite"], 0)  # not saved in this font
        self.db.execute("UPDATE fonts SET enabled = 0")  # no font left to give
        self.tab({})
        out = self.run_quietly(self.main["color_session"](None, self.app, self.store, self.session))
        self.assertIn("(saved font off; profile font): Dark One | (profile font) 13", out)

    def test_a_disabled_saved_font_is_noted_even_if_the_random_one_is_also_saved(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.main["save_favorite"](self.db, "Dark One", "Menlo-Regular", 13, 2)
        self.db.execute("UPDATE fonts SET enabled = (name = 'Menlo-Regular')")  # Monaco off
        self.main["pick"] = lambda db, mode, exclude: ("Dark One", None, "favorite", 4)  # Monaco's
        look = self.main["pick_look"](self.db, "dark", set())
        self.assertEqual(
            (look.font, look.is_favorite, look.font_off), ("Menlo-Regular", True, True)
        )

    def test_a_favorite_in_its_saved_font_has_no_note_even_with_others_off(self):
        self.fixing("0")
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.db.execute(
            "UPDATE config SET value = '1' WHERE key IN ('favorite_share', 'favorite_target')"
        )
        self.tab({})
        out = self.run_quietly(self.main["color_session"](None, self.app, self.store, self.session))
        self.assertIn("favorite 4 ★: Dark One | Monaco 13", out)
        self.assertNotIn("saved font off", out)

    def test_a_db_failure_leaves_a_tabs_favorite_mark_alone(self):
        self.tab({"user.color_preset": "Dark One", "user.font": "Monaco", "user.is_favorite": 1})
        tab = types.SimpleNamespace(all_sessions=[self.session])
        self.app.windows = [types.SimpleNamespace(tabs=[tab])]

        def locked(db, preset, font):
            raise sqlite3.OperationalError("database is locked")

        self.main["is_favorite"] = locked
        tab.all_sessions.append(self.session)  # two tabs of it: one failure, said once
        out = self.run_quietly(self.main["favorites_changed"](self.app, self.store, "Dark One"))
        self.assertEqual(self.variables["user.is_favorite"], 1)  # unknown, so not changed
        self.assertEqual(out.count("database is locked"), 1)

    def test_a_failing_tab_doesnt_stop_the_favorites_check(self):
        self.tab({"user.color_preset": "Dark One", "user.font": "Monaco", "user.is_favorite": 1})
        tab = types.SimpleNamespace(all_sessions=[self.session])
        self.app.windows = [types.SimpleNamespace(tabs=[tab])]

        def broken(db, preset, font):
            raise ValueError("unexpected")

        self.main["is_favorite"] = broken
        spawned = []
        self.main["spawn"] = lambda what, coro: spawned.append(what) or coro.close()
        out = self.run_quietly(self.main["favorites_changed"](self.app, self.store, "Dark One"))
        self.assertIn("favorite mark not updated on session s1: ValueError", out)
        self.assertEqual(spawned, ["favorite profiles sync"])  # still synced after it

    def test_nothing_left_to_pick_on_a_reroll_is_said_every_time(self):
        self.tab({"user.color_preset": "Dark One"})
        self.main["pick_look"] = lambda db, mode, exclude: ()
        color = self.main["color_session"]
        out = "".join(
            self.run_quietly(color(None, self.app, self.store, self.session, reroll=True))
            for _ in range(2)
        )
        self.assertEqual(out.count("nothing left to pick"), 2)  # the dialog says to look here
        self.tab({})
        out = "".join(
            self.run_quietly(color(None, self.app, self.store, self.session)) for _ in range(2)
        )
        self.assertEqual(out.count("no dark presets to pick from"), 1)  # new tabs: once

    def test_a_background_task_that_fails_is_logged(self):
        async def broken():
            raise RuntimeError("boom")

        async def scenario():
            self.main["spawn"]("the check", broken())
            await asyncio.sleep(0.01)

        out = self.run_quietly(scenario())
        self.assertIn("[warn] the check failed: RuntimeError('boom')", out)
        self.assertIn("Traceback", out)  # a bug: where it happened, too
        self.assertEqual(self.main["_background"], set())

    def test_startup_colors_open_tabs_before_reading_the_settings(self):
        self.tab({})
        tab = types.SimpleNamespace(all_sessions=[self.session])
        self.app.windows = [types.SimpleNamespace(tabs=[tab])]
        self.app.buried_sessions = []
        order = []

        async def colored(*args, **kwargs):
            order.append("color")

        async def synced(store):
            order.append("sync")

        self.main["color_one_at_a_time"] = colored
        self.main["sync_and_log"] = synced
        self.run_quietly(self.main["startup"](None, self.app, self.store, "2026-11-05 12:00:00"))
        self.assertEqual(order, ["color", "sync"])

    def test_a_failing_profiles_write_is_retried_rarely_and_logged_once(self):
        self.export_prefs({"Custom Color Presets": {"Dark One": DARK}})
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 3)
        blocker = os.path.join(self.tmp, "a-file")
        open(blocker, "w").close()
        self.main["DYNAMIC_PATH"] = os.path.join(blocker, "profiles.plist")  # can't be written
        writes = []
        real = self.main["write_favorite_profiles"]
        self.main["write_favorite_profiles"] = lambda snap: writes.append(1) or real(snap)
        store = self.main["Store"](self.db_path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for _ in range(3):  # three 30 s ticks, nothing changed
                asyncio.run(self.main["sync_and_log"](store))
        self.assertEqual(len(writes), 1)
        self.assertEqual(out.getvalue().count("favorite profiles not updated"), 1)
        self.main["_profiles_failed"][1] -= self.main["SYNC_RETRY_S"]  # ten minutes later
        with contextlib.redirect_stdout(out):
            asyncio.run(self.main["sync_and_log"](store))
        self.assertEqual(len(writes), 2)  # tried again
        self.assertEqual(out.getvalue().count("favorite profiles not updated"), 1)  # quietly

    def test_the_periodic_sync_survives_an_unexpected_error(self):
        calls = []

        async def broken(store):
            calls.append(store)
            raise ValueError("a corrupt row")

        self.main["sync_and_log"] = broken
        ticks = iter([None, None])

        async def sleep(_seconds):
            if next(ticks, "stop") == "stop":
                raise Stop

        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(Stop):
            asyncio.run(self.main["sync_periodically"](None, 0, sleep))
        self.assertEqual(len(calls), 2)  # the loop went on
        self.assertEqual(out.getvalue().count("favorite profiles check failed"), 1)

    def test_the_sync_lock_works_in_each_event_loop(self):
        store = self.main["Store"](self.db_path)

        async def second_sync_waits():
            async with store.sync_lock:
                try:  # while one sync holds it, another can't get in
                    await asyncio.wait_for(store.sync_lock.acquire(), 0.05)
                except TimeoutError:
                    return True
                return False

        for _ in range(2):  # a lock bound to the first loop would fail in the second
            self.assertTrue(asyncio.run(second_sync_waits()))

    def test_a_reroll_with_no_font_of_ours_clears_the_old_font_and_favorite(self):
        self.fixing("0")
        self.db.execute("UPDATE fonts SET enabled = 0")  # no random font to give
        self.tab({"user.color_preset": "Dark One", "user.font": "Monaco", "user.is_favorite": 1})
        self.run_quietly(
            self.main["color_session"](None, self.app, self.store, self.session, reroll=True)
        )
        self.assertEqual(self.variables["user.color_preset"], "Dark Two")
        self.assertEqual((self.variables["user.font"], self.variables["user.is_favorite"]), ("", 0))

    def test_a_reroll_ends_the_old_launch_and_adds_the_new_one_together(self):
        self.record("Dark One", "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        launch = self.main["Launch"]("Dark Two", "Monaco", 13, "dark", "discovery", None, "D", "s1")

        def broken_insert(db, launch):
            raise sqlite3.IntegrityError("simulated")

        self.main["record_launch"] = broken_insert
        store = self.main["Store"](self.db_path)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(asyncio.run(store.call(self.main["record_change"], launch)))
        rows = self.db.execute("SELECT preset, end_reason FROM launches").fetchall()
        self.assertEqual(rows, [("Dark One", None)])  # still open: neither change happened

    def test_three_missing_presets_in_a_row_say_rebuild_once(self):
        self.tab({})
        self.main["get_preset"] = mock.AsyncMock(return_value=None)
        color = self.main["color_session"]
        out = self.run_quietly(color(None, self.app, self.store, self.session))
        out += self.run_quietly(color(None, self.app, self.store, self.session))
        self.assertEqual(out.count("3 picks in a row"), 1)
        self.assertNotIn("user.color_preset", self.variables)

    def test_a_favorite_profile_whose_preset_is_gone_logs_the_theme_mode(self):
        self.db.execute(
            "INSERT INTO favorites (preset, font, size, stars, saved_at) "
            "VALUES ('Gone', 'Monaco', 13, 4, '2026-11-05 00:00:00')"
        )
        self.tab({})
        self.profile.original_guid = self.main["favorite_guid"]("Gone", "Monaco")
        color = self.main["color_one_at_a_time"]
        self.run_quietly(color(None, self.app, self.store, self.session))
        self.assertEqual(self.db.execute("SELECT mode FROM launches").fetchall(), [("dark",)])

    def test_never_show_from_the_rating_dialog_rerolls_the_tab(self):
        self.fixing("0")
        self.tab({"user.color_preset": "Dark One", "user.font": "Monaco"}, ["Never show"])
        self.record("Dark One", "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        self.run_quietly(self.main["save_dialog"](None, self.app, self.store, "s1"))
        self.assertNotIn("Dark One", self.discovery("dark"))
        self.profile.async_set_color_preset.assert_called_once_with("Dark Two")

    def test_never_show_from_the_rating_dialog_asks_first_for_a_favorite(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.tab({"user.color_preset": "Dark One", "user.font": "Monaco"}, ["Never show", "Cancel"])
        self.run_quietly(self.main["save_dialog"](None, self.app, self.store, "s1"))
        self.assertEqual(len(self.texts), 2)  # the rating dialog, then the question
        self.assertIn("is a favorite", self.texts[1])
        self.assertTrue(self.main["is_favorite"](self.db, "Dark One", "Monaco"))
        self.profile.async_set_color_preset.assert_not_called()

    def test_saving_a_tab_with_no_font_of_ours_records_the_font_it_saved(self):
        self.tab({"user.color_preset": "Dark One"}, ["3 ★★★"])
        self.run_quietly(self.main["save_dialog"](None, self.app, self.store, "s1"))
        self.assertEqual(self.variables["user.font"], "Monaco")  # from the profile
        self.assertTrue(self.main["is_favorite"](self.db, "Dark One", "Monaco"))

    def test_the_rating_dialog_says_how_many_colors_were_adjusted(self):
        tab = {"user.color_preset": "Murky", "user.font": "Monaco", "user.adjusted": 1}
        self.tab(tab, ["Cancel"])
        self.run_quietly(self.main["save_dialog"](None, self.app, self.store, "s1"))
        self.assertEqual(self.texts[0].splitlines()[-1], "1 color adjusted.")

    def test_cancelling_the_rating_dialog_changes_nothing(self):
        self.tab({"user.color_preset": "Dark One"}, ["Cancel"])
        self.run_quietly(self.main["save_dialog"](None, self.app, self.store, "s1"))
        self.assertNotIn("user.font", self.variables)
        self.assertFalse(self.main["is_favorite"](self.db, "Dark One", "Monaco"))

    def test_remove_when_the_db_fails_says_so_instead_of_not_a_favorite(self):
        missing = os.path.join(self.tmp, "none.db")
        self.tab({"user.color_preset": "Dark One", "user.font": "Monaco"}, db_path=missing)
        self.run_quietly(self.main["remove_from_menu"](None, self.app, self.store, "s1"))
        self.assertEqual(len(self.texts), 1)
        self.assertIn("didn't take", self.texts[0])

    def test_remove_on_a_tab_with_no_font_of_ours_says_why(self):
        self.tab({"user.color_preset": "Dark One", "user.font": ""})
        self.run_quietly(self.main["remove_from_menu"](None, self.app, self.store, "s1"))
        self.assertIn("has no font of its own", self.texts[0])

    def test_a_rebuild_reaches_an_open_store_without_a_restart(self):
        store = self.main["Store"](self.db_path)
        look_fixes = self.main["look_fixes"]
        before = asyncio.run(store.call(look_fixes, "Murky"))
        darker = with_colors(UNREADABLE, Foreground_Color=(0.2, 0.2, 0.2))
        with contextlib.redirect_stdout(io.StringIO()):
            rows, _ = build.build_rows({**PRESETS, "Murky": darker}, self.repo)
            build.write_db(self.db_path, rows)
        after = asyncio.run(store.call(look_fixes, "Murky"))
        self.assertNotEqual(after, before)
        self.assertEqual(after[1], build.color_hash(darker))

    def test_a_failed_profiles_write_leaves_the_adjusted_counts_alone(self):
        self.export_prefs({"Custom Color Presets": {"Murky": UNREADABLE}})
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        blocker = os.path.join(self.tmp, "a-file")
        open(blocker, "w").close()
        self.main["DYNAMIC_PATH"] = os.path.join(blocker, "profiles.plist")  # can't be written
        with self.assertRaises(OSError), contextlib.redirect_stdout(io.StringIO()):
            self.write_profiles()
        self.assertNotIn("Murky", self.main["_profile_adjusted"])  # the file never held it

    def test_a_new_color_hash_rewrites_the_favorite_profiles(self):
        self.export_prefs({"Custom Color Presets": {"Murky": UNREADABLE}})
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        self.assertIsNotNone(self.sync_now())
        self.assertIsNone(self.sync_now())  # nothing changed
        self.db.execute("UPDATE schemes SET color_hash = 'other' WHERE name = 'Murky'")
        self.assertIsNotNone(self.sync_now())  # its adjustments no longer apply: rewritten


class NeverShowTest(DbTest):
    """Right-click Never show this profile, and Show again (srcp_never_show.py's DB side)."""

    def show_again(self, name):
        return srcp_favorites.show_again(self.db, name)

    def include(self, name):
        row = self.db.execute("SELECT include FROM overrides WHERE name = ?", (name,)).fetchone()
        return row[0] if row else "no row"

    def test_show_again_brings_it_back_keeping_its_mode_override(self):
        self.db.execute("INSERT INTO overrides (name, mode) VALUES ('Dark One', 'light')")
        self.main["never_show"](self.db, "Dark One")
        self.assertNotIn("Dark One", self.discovery("light"))
        self.assertEqual(self.show_again("Dark One"), (True, True, False))
        self.assertIn("Dark One", self.discovery("light"))
        self.assertIsNone(self.include("Dark One"))

    def test_show_again_of_an_unreadable_preset_with_adjustments_on_leaves_no_override(self):
        self.main["never_show"](self.db, "Murky")
        self.assertEqual(self.show_again("Murky"), (True, True, False))
        self.assertIsNone(self.include("Murky"))
        self.assertIn("Murky", self.discovery("dark"))
        self.fixing("0")  # off again: back as before, so it's out
        self.assertNotIn("Murky", self.discovery("dark"))

    def test_with_adjustments_off_show_again_forces_in_an_unreadable_preset_but_not_a_twin(self):
        self.fixing("0")
        self.main["never_show"](self.db, "Murky")
        self.assertEqual(self.show_again("Murky"), (True, True, True))
        self.assertIn("Murky", self.discovery("dark"))
        self.db.execute("UPDATE schemes SET passes_gate = 0 WHERE name = 'Dark Two Copy'")
        self.main["never_show"](self.db, "Dark Two Copy")
        self.assertEqual(self.show_again("Dark Two Copy"), (True, True, False))
        self.assertIsNone(self.include("Dark Two Copy"))  # a twin stays out of discovery

    def test_show_again_on_a_preset_that_isnt_hidden_changes_nothing(self):
        self.assertFalse(self.show_again("Dark One").shown)
        self.assertEqual(self.include("Dark One"), "no row")
        self.assertFalse(self.db.in_transaction)

    def test_show_again_on_a_preset_no_longer_installed_says_so(self):
        self.main["never_show"](self.db, "Gone")  # no schemes row
        result = self.show_again("Gone")
        self.assertEqual(result, (True, False, False))
        self.assertIn("isn't an installed preset", srcp_favorites.shown_again_note("Gone", result))

    def test_show_again_command_line(self):
        self.main["never_show"](self.db, "Yellow")
        tool = os.path.join(ROOT, ".tools", "srcp_favorites.py")
        run = [sys.executable, "-I", tool, "--db", self.db_path, "--show-again"]
        done = subprocess.run([*run, "Yellow"], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Yellow is back.", done.stdout)
        again = subprocess.run([*run, "Yellow"], capture_output=True, text=True, check=False)
        self.assertIn("isn't hidden", again.stderr)
        self.assertNotEqual(again.returncode, 0)

    def menu(self, preset, button="Never show", fail=False, never_show=None):
        """Run the right-click item on a tab showing preset; return the alerts' texts.

        fail: no DB at all; never_show: replaces the main script's never_show.
        """
        if never_show is not None:
            self.main["never_show"] = never_show
        self.fixing("0")  # Murky (unreadable) stays out of the re-roll
        missing = os.path.join(self.tmp, "none") if fail else None
        self.tab({"user.color_preset": preset, "user.font": "Monaco"}, [button] * 3, missing)
        self.record(preset, "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        self.run_quietly(self.main["never_show_from_menu"](None, self.app, self.store, "s1"))
        return self.texts

    def test_menu_hides_the_preset_and_rerolls_the_tab(self):
        self.assertEqual(self.menu("Dark One"), [])  # not a favorite: no question asked
        self.assertNotIn("Dark One", self.discovery("dark"))
        self.profile.async_set_color_preset.assert_called_once_with("Dark Two")
        rows = self.db.execute("SELECT preset, end_reason FROM launches ORDER BY id")
        self.assertEqual(rows.fetchall(), [("Dark One", "changed"), ("Dark Two", None)])

    def test_menu_asks_first_for_a_favorite_and_cancel_keeps_it(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        texts = self.menu("Dark One", button="Cancel")
        self.assertEqual(len(texts), 1)
        self.assertIn("4 ★★★★", texts[0])
        self.assertEqual(self.include("Dark One"), "no row")  # not hidden
        self.assertTrue(self.main["is_favorite"](self.db, "Dark One", "Monaco"))
        self.profile.async_set_color_preset.assert_not_called()
        self.menu("Dark One")
        self.assertFalse(self.main["is_favorite"](self.db, "Dark One", "Monaco"))
        self.assertEqual(self.include("Dark One"), 0)

    def test_menu_on_a_preset_already_hidden_elsewhere_still_rerolls(self):
        self.main["never_show"](self.db, "Dark One")  # hidden from another tab
        self.assertEqual(self.menu("Dark One"), [])
        self.profile.async_set_color_preset.assert_called_once_with("Dark Two")

    def test_menu_says_so_when_no_other_preset_is_left(self):
        self.main["never_show"](self.db, "Dark Two")
        texts = self.menu("Dark One")
        self.assertEqual(len(texts), 1)
        self.assertIn("couldn't switch to another look", texts[0])
        self.assertEqual(self.variables["user.color_preset"], "Dark One")
        self.assertEqual(self.include("Dark One"), 0)

    def test_menu_when_hiding_fails_after_the_question_keeps_the_look(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)

        def broken(db, preset):
            raise sqlite3.OperationalError("database is locked")

        texts = self.menu("Dark One", never_show=broken)
        self.assertEqual(len(texts), 2)  # the question, then "didn't take"
        self.assertIn("didn't take", texts[1])
        self.profile.async_set_color_preset.assert_not_called()
        self.assertTrue(self.main["is_favorite"](self.db, "Dark One", "Monaco"))

    def test_popup_output_never_wraps_to_the_last_name(self):
        import srcp_popup  # noqa: PLC0415  (.tools is on sys.path; only these tests need it)

        buttons, names = ["Done", "Show again"], ["A", "B"]
        self.assertEqual(srcp_popup.parse_result("1\t1\n", buttons, names), ("Show again", "B"))
        self.assertEqual(srcp_popup.parse_result("1\t-1", buttons, names), ("Show again", None))
        self.assertEqual(srcp_popup.parse_result("1\t5", buttons, names), ("Show again", None))
        self.assertEqual(srcp_popup.parse_result("0", buttons, names), ("Done", None))
        self.assertIsNone(srcp_popup.parse_result("", buttons, names))
        self.assertIsNone(srcp_popup.parse_result("-1", buttons, names))
        with contextlib.redirect_stdout(io.StringIO()):
            _, offered = srcp_popup.popup_args("T", "x", buttons, ["A", "", "B"])
        self.assertEqual(offered, ["A", "B"])  # srcp_popup.js drops empty lines too

    def test_menu_without_a_db_alerts_and_keeps_the_look(self):
        texts = self.menu("Dark One", fail=True)
        self.assertEqual(len(texts), 1)
        self.assertIn("didn't take", texts[0])
        self.profile.async_set_color_preset.assert_not_called()

    @unittest.skipUnless(shutil.which("osascript"), "needs macOS")
    def test_popup_lists_the_choices(self):
        sys_path = os.path.join(ROOT, ".tools")
        import srcp_popup  # noqa: PLC0415  (.tools is on sys.path; only this test needs it)

        self.assertTrue(srcp_popup.POPUP_JS.startswith(sys_path))
        with contextlib.redirect_stdout(io.StringIO()):
            buttons = ["Done", "Show again"]
            args, offered = srcp_popup.popup_args("T", "a\nb", buttons, ["X", "Y\tZ"])
        self.assertEqual(offered, ["X"])  # a tab can't be read back
        env = {**os.environ, "SRCP_POPUP_DRYRUN": "1"}
        try:
            out = subprocess.run(
                args, capture_output=True, text=True, env=env, timeout=20, check=False
            )
        except subprocess.TimeoutExpired:
            self.skipTest("osascript hung (no window server?)")
        if out.returncode != 0:
            self.skipTest(f"osascript can't build the alert here: {out.stderr.strip()}")
        self.assertIn("choices=1", out.stdout)
        self.assertIn("buttons=2", out.stdout)


class ReportTest(DbTest):
    def test_all_views_render(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.db.execute("UPDATE favorites SET saved_at = '2026-10-21'")  # edited by hand
        self.main["save_favorite"](self.db, "Dark Two", "Monaco", 13, 2)  # never launched
        self.main["never_show"](self.db, "Yellow")
        self.db.execute(
            "INSERT INTO launches (launched_at, preset, font, size, mode, source, duration_s, "
            "end_reason) VALUES ('2026-10-26 12:00:00', 'Dark One', 'Monaco', 13, 'dark', "
            "'favorite', 5, 'changed')"
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            srcp_favorites.show_favorites(self.db, None, 1, cli_hints=False)
            srcp_favorites.show_history(self.db, 10)
            srcp_favorites.show_hidden(self.db)
        text = out.getvalue()
        for expected in ("Dark One", "2026-10-21", "Yellow", "1 hidden.", "Adjusted", "1 color"):
            self.assertIn(expected, text)
        rows = {r[1]: r[4:8] for r in srcp_favorites.favorite_rows(self.db, None, 1)}
        self.assertEqual(rows["Dark One"][:2], (1, 1))  # launches, changed
        self.assertIsNone(rows["Dark One"][3])  # avg open leaves the changed look out
        self.assertEqual(rows["Dark Two"][:2], (0, 0))  # no launches: 0, not blank


if __name__ == "__main__":
    unittest.main()
