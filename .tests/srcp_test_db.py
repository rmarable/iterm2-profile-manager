"""Tests for the database layer: build, migrations, picking, rating, export and helpers.

Runs under iTerm2's Python (the main script imports iterm2) against a temp folder; nothing
touches the real database or iTerm2's settings. lint.sh runs it with
    "$ITERM_PY" -m unittest discover -s .tests -p 'srcp_test_*.py'

The dot keeps this folder out of iTerm2's Scripts menu, like .tools/.
"""

import contextlib
import io
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".tools"))

import srcp_build_scheme_db as build  # noqa: E402  (needs .tools on sys.path first)
import srcp_db_backup  # noqa: E402
import srcp_export_curation  # noqa: E402
import srcp_favorites  # noqa: E402
import srcp_iterm_prefs  # noqa: E402
import srcp_preset_history  # noqa: E402

MAIN = os.path.join(ROOT, "AutoLaunch", "srcp_set_random_color_profile.py")


def load_main(data_dir):
    """The main script's functions, minus run_forever, with its paths under data_dir."""
    with open(MAIN) as f:
        source = f.read().replace("iterm2.run_forever(main)", "")
    ns = {"__name__": "srcp_main", "__file__": MAIN}
    exec(compile(source, MAIN, "exec"), ns)  # noqa: S102  (our own script, read from the repo)
    ns["DATA_DIR"] = data_dir
    ns["DB_PATH"] = os.path.join(data_dir, "schemes.db")
    ns["LOCK_PATH"] = os.path.join(data_dir, ".lock")
    ns["DYNAMIC_PATH"] = os.path.join(data_dir, "dynamic.plist")
    return ns


def color(r, g, b):
    return {"Red Component": r, "Green Component": g, "Blue Component": b}


def preset(bg, fg, accents):
    """A full preset: ANSI 0-15 (1-6 and 9-14 are the accents), foreground, background."""
    ansi = [(0, 0, 0), *accents, (0.8, 0.8, 0.8)]
    p = {f"Ansi {i} Color": color(*ansi[i % 8]) for i in range(16)}
    p["Foreground Color"] = color(*fg)
    p["Background Color"] = color(*bg)
    return p


DARK_ACCENTS = [(0.9, 0.2, 0.2), (0.2, 0.8, 0.2), (0.9, 0.9, 0.2), (0.3, 0.4, 1.0)]
DARK_ACCENTS += [(0.9, 0.3, 0.9), (0.2, 0.9, 0.9)]
LIGHT_ACCENTS = [(0.7, 0.0, 0.0), (0.0, 0.5, 0.0), (0.55, 0.45, 0.0), (0.0, 0.2, 0.8)]
LIGHT_ACCENTS += [(0.6, 0.0, 0.6), (0.0, 0.45, 0.5)]
DARK = preset((0.1, 0.1, 0.1), (0.9, 0.9, 0.9), DARK_ACCENTS)
LIGHT = preset((0.97, 0.97, 0.97), (0.1, 0.1, 0.1), LIGHT_ACCENTS)
YELLOW = preset((0.98, 0.9, 0.4), (0.1, 0.1, 0.1), LIGHT_ACCENTS)  # colored background
UNREADABLE = preset((0.2, 0.2, 0.2), (0.3, 0.3, 0.3), DARK_ACCENTS)
DARK_2 = preset((0.12, 0.1, 0.15), (0.85, 0.85, 0.8), DARK_ACCENTS)
PRESETS = {
    "Dark One": DARK,
    "Dark Two": DARK_2,
    "Dark Two Copy": DARK_2,  # the repo's own identical twin of Dark Two
    "dark one": DARK,  # an old spelling of Dark One, not in the repo
    "Light One": LIGHT,
    "Yellow": YELLOW,
    "Murky": UNREADABLE,
}
REPO_NAMES = ["Dark One", "Dark Two", "Dark Two Copy", "Light One", "Yellow", "Murky"]


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="srcp-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "schemes"))
        for name in REPO_NAMES:
            with open(os.path.join(self.repo, "schemes", f"{name}.itermcolors"), "wb") as f:
                plistlib.dump(PRESETS[name], f)
        self.data = os.path.join(self.tmp, "data")
        self.db_path = os.path.join(self.data, "schemes.db")
        self.build()
        self.main = load_main(self.data)
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
        self.assertEqual(self.discovery("dark"), {"Dark One", "Dark Two"})

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

    def test_duplicated_constants_agree(self):
        seeded = {
            k: float(v) for k, v in build.SEED_CONFIG.items() if k in self.main["DEFAULT_CONFIG"]
        }
        self.assertEqual(seeded, {k: float(v) for k, v in self.main["DEFAULT_CONFIG"].items()})
        self.assertEqual(build.SEED_CONFIG["nonascii_font"], self.main["DEFAULT_NONASCII_FONT"])
        self.assertEqual(self.main["BACKUP_KEEP"], srcp_db_backup.KEEP)


class MainScriptTest(DbTest):
    def test_pick_respects_mode_and_exclude(self):
        for _ in range(50):
            name, font, source, _stars = self.main["pick"](self.db, "dark", {"Dark Two"})
            self.assertEqual((name, font, source), ("Dark One", None, "discovery"))

    def test_never_show_hides_and_rating_unhides_keeping_mode(self):
        self.db.execute("INSERT INTO overrides (name, mode) VALUES ('Dark One', 'light')")
        self.assertTrue(self.main["never_show"](self.db, "Dark One"))
        self.assertNotIn("Dark One", self.discovery("light"))
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 5)
        row = self.db.execute("SELECT mode, include FROM overrides WHERE name = 'Dark One'")
        self.assertEqual(row.fetchone(), ("light", None))

    def test_rating_a_hidden_unreadable_preset_forces_it_in(self):
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
        stdout = plistlib.dumps(prefs)
        self.main["subprocess"] = types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout=stdout)
        )
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.main["save_favorite"](self.db, "Light One", "Menlo-Regular", 12.5, 2)
        self.main["save_favorite"](self.db, "Yellow", "Monaco", 13, 3)
        self.main["never_show"](self.db, "Yellow")  # hidden: no profile
        self.db.execute(  # its preset isn't installed: no profile
            "INSERT INTO favorites (preset, font, size, stars, saved_at) "
            "VALUES ('Gone', 'Monaco', 13, 5, '2026-10-21 00:00:00')"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.main["sync_profiles"](self.db), 2)
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
        self.assertIsNone(self.main["sync_profiles"](self.db))  # unchanged: no rewrite
        visible = [n for n in os.listdir(self.data) if not n.startswith(".")]
        self.assertNotIn("dynamic.plist.partial", visible)

    def test_favorite_guid_is_stable(self):
        guid = self.main["favorite_guid"]("Dark One", "Monaco")
        self.assertEqual(guid, self.main["favorite_guid"]("Dark One", "Monaco"))
        self.assertNotEqual(guid, self.main["favorite_guid"]("Dark One", "Menlo-Regular"))


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


class ReportTest(DbTest):
    def test_all_views_render(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.db.execute("UPDATE favorites SET saved_at = '2026-10-21'")  # edited by hand
        self.main["never_show"](self.db, "Yellow")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            srcp_favorites.show_favorites(self.db, None, 1, cli_hints=False)
            srcp_favorites.show_history(self.db, 10)
            srcp_favorites.show_hidden(self.db)
        text = out.getvalue()
        for expected in ("Dark One", "2026-10-21", "Yellow", "1 hidden."):
            self.assertIn(expected, text)


class HelperTest(unittest.TestCase):
    def test_preset_history_finds_older_versions(self):
        with tempfile.TemporaryDirectory() as repo:
            path = os.path.join(repo, "schemes", "A.itermcolors")
            os.makedirs(os.path.dirname(path))
            git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
            # Ignore the user's git config (commit signing, hooks), which could fail commits.
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            subprocess.run([*git[:3], "init", "-q"], check=True, env=env)
            for version in (DARK, DARK_2):
                with open(path, "wb") as f:
                    plistlib.dump(version, f)
                subprocess.run([*git, "add", "."], check=True, env=env)
                subprocess.run([*git, "commit", "-qm", "v"], check=True, env=env)
            root = srcp_preset_history.repo_root(os.path.dirname(path))
            self.assertEqual(len(srcp_preset_history.past_versions(root, path)), 2)
            self.assertTrue(srcp_preset_history.from_repo(DARK, path, root))
            self.assertFalse(srcp_preset_history.from_repo(LIGHT, path, root))

    def test_custom_settings_folder_is_refused_before_writing(self):
        prefs = {"LoadPrefsFromCustomFolder": True, "PrefsCustomFolder": "/elsewhere"}
        self.assertEqual(srcp_iterm_prefs.custom_folder(prefs), "/elsewhere")
        self.assertIsNone(srcp_iterm_prefs.custom_folder({}))
        with self.assertRaises(SystemExit):
            srcp_iterm_prefs.save_prefs(prefs)


if __name__ == "__main__":
    unittest.main()
