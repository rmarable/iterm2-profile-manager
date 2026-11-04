"""Tests for the database layer: build, migrations, picking, rating, export and helpers.

Runs under iTerm2's Python (the main script imports iterm2) against a temp folder; nothing
touches the real database or iTerm2's settings. lint.sh runs it with
    "$ITERM_PY" -m unittest discover -s .tests -p 'srcp_test_*.py'

The dot keeps this folder out of iTerm2's Scripts menu, like .tools/.
"""

import asyncio
import contextlib
import io
import json
import math
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".tools"))

import srcp_build_scheme_db as build  # noqa: E402  (needs .tools on sys.path first)
import srcp_db_backup  # noqa: E402
import srcp_export_curation  # noqa: E402
import srcp_favorites  # noqa: E402
import srcp_iterm_prefs  # noqa: E402
import srcp_odds  # noqa: E402
import srcp_preset_history  # noqa: E402
import srcp_uninstall_prefs  # noqa: E402

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


class FakePreset(str):
    """What get_preset returns in the tests: the preset's name, with no colors to adjust."""

    values = ()


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
        self.assertEqual(self.main["FONT_DIRS"], build.FONT_DIRS)
        nonascii = self.main["DEFAULT_NONASCII_FONT"]
        self.assertEqual(self.main["NONASCII_FONT_FILES"], build.SEED_FONTS[nonascii])

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
        self.db.execute("PRAGMA user_version = 4")
        self.db.execute("UPDATE fonts SET enabled = 0")
        self.db.execute("UPDATE fonts SET enabled = 1 WHERE name = 'Monaco'")
        with self.install_fonts("Menlo-Regular", "Monaco"):
            self.build()
        fonts = self.fonts()
        self.assertEqual(fonts["SFMono-Regular"], (0, 1))  # not installed: the build's doing
        self.assertEqual(fonts["Menlo-Regular"], (0, 0))  # installed yet off: your choice
        self.assertEqual(fonts["Monaco"], (1, 0))


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
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            asyncio.run(self.main["exit_on_disconnect"](connection))
        self.assertEqual(cm.exception.code, 0)

    def test_change_ends_the_old_launch_and_close_ends_the_new(self):
        launch = ("Dark One", "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        self.main["record_launch"](self.db, *launch)
        self.main["record_end"](self.db, "s1", "changed")
        self.main["record_launch"](self.db, "Dark Two", *launch[1:])
        self.main["record_end"](self.db, "s1")
        rows = self.db.execute("SELECT preset, end_reason, duration_s FROM launches ORDER BY id")
        self.assertEqual(rows.fetchall(), [("Dark One", "changed", 0), ("Dark Two", "closed", 0)])

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
        self.main["FONT_DIRS"] = []  # no icon font
        self.main["sync_and_log"] = mock.AsyncMock()
        self.main["get_preset"] = mock.AsyncMock(side_effect=lambda _c, name: FakePreset(name))
        self.fixing("0")  # Murky (unreadable) stays out of the re-roll
        variables = {"user.color_preset": "Dark One"}

        async def set_variable(name, value):
            variables[name] = value

        profile = mock.AsyncMock(normal_font="Monaco 13")
        profile.name = "Default"  # not a constructor argument: name= names the mock
        session = mock.AsyncMock(session_id="s1", async_set_variable=set_variable)
        session.async_get_variable.side_effect = variables.get
        session.async_get_profile.return_value = profile
        app = mock.AsyncMock()
        app.async_get_theme.return_value = ["dark"]
        store = self.main["Store"](self.db_path)
        color = self.main["color_session"]
        launch = ("Dark One", "Monaco", 13, "dark", "discovery", None, "Default", "s1")
        self.main["record_launch"](self.db, *launch)
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(color(None, app, store, session))  # has a look: left alone
            profile.async_set_color_preset.assert_not_called()
            asyncio.run(color(None, app, store, session, reroll=True))
        profile.async_set_color_preset.assert_called_once_with("Dark Two")
        self.assertEqual(variables["user.color_preset"], "Dark Two")
        rows = self.db.execute("SELECT preset, end_reason FROM launches ORDER BY id")
        self.assertEqual(rows.fetchall(), [("Dark One", "changed"), ("Dark Two", None)])

    def test_profiles_sync_on_a_db_without_font_labels(self):
        self.main["subprocess"] = types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout=plistlib.dumps({}))
        )
        self.main["_profiles_state"][0] = None
        self.db.execute("ALTER TABLE fonts DROP COLUMN label")  # as in a schema-1 DB
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNotNone(self.main["sync_if_changed"](self.db))

    def test_font_label_edits_trigger_a_profiles_rewrite(self):
        self.main["subprocess"] = types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout=stdout)
        )
        stdout = plistlib.dumps({"Custom Color Presets": {"Dark One": DARK}})
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 4)
        self.main["_profiles_state"][0] = None
        self.assertIsNotNone(self.main["sync_if_changed"](self.db))
        self.assertIsNone(self.main["sync_if_changed"](self.db))
        self.db.execute("UPDATE fonts SET label = 'Mo' WHERE name = 'Monaco'")
        self.assertIsNotNone(self.main["sync_if_changed"](self.db))

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
            self.main["record_launch"](
                self.db, preset, None, 13, "dark", "discovery", None, "p", preset
            )

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
        self.main["record_launch"](
            self.db, "Dark Two", None, 13, "dark", "discovery", None, "p", "s"
        )
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
        self.assertIn("Restart srcp_set_random_color_profile.py", text)
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
        self.assertEqual(self.main["FIX_ON_SQL"], build.FIX_ON_SQL)
        self.assertEqual(srcp_favorites.FIX_ON_SQL, build.FIX_ON_SQL)
        self.assertEqual(self.main["HASH_KEYS"], build.HASH_KEYS)
        self.assertEqual(self.main["COMPONENTS"], build.COMPONENTS)
        on = [None, "1", "1.0", " 1 ", "off", "", "nan", "yes"]
        for value in [*on, "0", "0.0", " 0 "]:
            with self.subTest(value=value):
                self.fixing(value)
                expected = value not in ("0", "0.0", " 0 ")
                self.assertEqual("Murky" in self.discovery("dark"), expected)
                fixes, _ = self.main["look_fixes"](self.db, "Murky")
                self.assertEqual(fixes is not None, expected)

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
        self.main["subprocess"] = types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout=plistlib.dumps(prefs))
        )
        # The build hashes components only, so the added Color Space doesn't change the hash.
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.main["sync_profiles"](self.db), 1)
        with open(self.main["DYNAMIC_PATH"], "rb") as f:
            fg = plistlib.load(f)["Profiles"][0]["Foreground Color"]
        expected = json.loads(self.scheme_fixes("Murky"))["Foreground Color"][:3]
        self.assertEqual([fg[c] for c in build.COMPONENTS], expected)
        self.assertEqual(fg["Color Space"], "P3")  # kept
        self.assertEqual(plistlib.dumps(prefs), before)  # nothing mutated
        state = self.main["_profiles_state"]
        state[0] = None
        with contextlib.redirect_stdout(io.StringIO()):
            self.main["sync_if_changed"](self.db)
            first = state[0]
            self.db.execute("UPDATE schemes SET fixes = '{}' WHERE name = 'Murky'")
            self.main["sync_if_changed"](self.db)
        self.assertNotEqual(state[0], first)  # changed adjustments rewrite the profiles

    def scheme_fixes(self, name):
        return self.db.execute("SELECT fixes FROM schemes WHERE name = ?", (name,)).fetchone()[0]

    def test_a_tab_with_an_adjusted_preset_says_so(self):
        for name in ("Dark One", "Dark Two"):  # leave only Murky to pick
            self.main["never_show"](self.db, name)
        self.main["FONT_DIRS"] = []
        self.main["sync_and_log"] = mock.AsyncMock()
        preset = self.fake_preset(UNREADABLE)
        self.main["get_preset"] = mock.AsyncMock(return_value=preset)
        variables = {}

        async def set_variable(name, value):
            variables[name] = value

        profile = mock.AsyncMock(normal_font="Monaco 13")
        profile.name = "Default"
        session = mock.AsyncMock(session_id="s1", async_set_variable=set_variable)
        session.async_get_variable.side_effect = variables.get
        session.async_get_profile.return_value = profile
        app = mock.AsyncMock()
        app.async_get_theme.return_value = ["dark"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            asyncio.run(
                self.main["color_session"](None, app, self.main["Store"](self.db_path), session)
            )
        n = len(json.loads(self.scheme_fixes("Murky")))
        line = next(x for x in out.getvalue().splitlines() if "Murky |" in x)  # random font
        self.assertTrue(line.endswith(f" 13, {self.main['colors_adjusted'](n)}"), line)
        self.assertEqual(variables["user.adjusted"], n)
        profile.async_set_color_preset.assert_called_once_with(preset)
        fg = next(v for v in preset.values if v.key == "Foreground Color")
        self.assertNotEqual(round(fg.red / 255, 4), UNREADABLE["Foreground Color"]["Red Component"])

    def test_a_favorite_profile_session_reports_its_adjustments(self):
        self.main["save_favorite"](self.db, "Murky", "Monaco", 13, 3)
        guid = self.main["favorite_guid"]("Murky", "Monaco")
        fav = self.main["favorite_by_guid"](self.db, guid)
        self.assertEqual(fav[4], len(json.loads(self.scheme_fixes("Murky"))))
        # Its profile got none (the preset changed since the build): the session says none.
        changed = with_colors(UNREADABLE, Ansi_1_Color=(0.5, 0.1, 0.1))
        with contextlib.redirect_stdout(io.StringIO()):
            self.main["adjust_colors"]("Murky", changed, self.scheme_fixes("Murky"), "x")
        self.assertEqual(self.main["_profile_adjusted"]["Murky"], 0)
        self.fixing("0")
        self.assertEqual(self.main["favorite_by_guid"](self.db, guid)[4], 0)

    def test_the_float32_round_trip_keeps_the_hash(self):
        # Values on a .4f boundary (Sandstone presets) format differently after iTerm2's 32-bit
        # floats unless both sides round to 32 bits first.
        colors = with_colors(UNREADABLE, Ansi_1_Color=(0.02745098039215686, 0.5, 0.5))
        live = {
            k: tuple(build.float32(c) * 255 / 255 for c in build.rgb(colors[k]))
            for k in build.HASH_KEYS
        }
        self.assertEqual(self.main["colors_hash"](live), build.color_hash(colors))

    def test_reports_work_on_a_db_from_before_adjustments(self):
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 3)
        for view in ("favorite_pool", "discovery", "effective"):
            self.db.execute(f"DROP VIEW {view}")
        self.db.execute("ALTER TABLE schemes DROP COLUMN fix_count")
        self.db.execute("CREATE VIEW effective AS SELECT s.name, s.mode FROM schemes s")
        rows = srcp_favorites.favorite_rows(self.db, None, 1)
        self.assertEqual((rows[0][1], rows[0][-1]), ("Dark One", 0))
        self.assertEqual(
            self.main["favorite_by_guid"](
                self.db, self.main["favorite_guid"]("Dark One", "Monaco")
            )[4],
            0,
        )

    def test_a_db_from_before_adjustments_still_works(self):
        self.db.execute("ALTER TABLE schemes DROP COLUMN fixes")
        self.main["save_favorite"](self.db, "Dark One", "Monaco", 13, 3)
        self.main["subprocess"] = types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(
                stdout=plistlib.dumps({"Custom Color Presets": {"Dark One": DARK}})
            )
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.main["look_fixes"](self.db, "Dark One"), (None, None))
            self.assertEqual(self.main["sync_profiles"](self.db), 1)
            self.main["_profiles_state"][0] = None
            self.assertEqual(self.main["sync_if_changed"](self.db), None)  # file already current


class NeverShowTest(DbTest):
    """Right-click Never show this profile, and Show again (srcp_never_show.py's DB side)."""

    def show_again(self, name):
        return srcp_favorites.show_again(self.db, name)

    def include(self, name):
        row = self.db.execute("SELECT include FROM overrides WHERE name = ?", (name,)).fetchone()
        return row[0] if row else "no row"

    def test_unhide_rule_is_the_same_in_both_scripts(self):
        self.assertEqual(self.main["UNHIDE"], srcp_favorites.UNHIDE)

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
        self.main["FONT_DIRS"] = []  # no icon font
        self.main["sync_and_log"] = mock.AsyncMock()
        self.main["get_preset"] = mock.AsyncMock(side_effect=lambda _c, name: FakePreset(name))
        self.fixing("0")  # Murky (unreadable) stays out of the re-roll
        variables = {"user.color_preset": preset, "user.font": "Monaco"}

        async def set_variable(name, value):
            variables[name] = value

        profile = mock.AsyncMock(normal_font="Monaco 13")
        profile.name = "Default"
        session = mock.AsyncMock(session_id="s1", async_set_variable=set_variable)
        session.async_get_variable.side_effect = variables.get
        session.async_get_profile.return_value = profile
        app = mock.MagicMock(windows=[])
        app.get_session_by_id.return_value = session
        app.get_window_and_tab_for_session.return_value = (None, None)
        app.async_get_theme = mock.AsyncMock(return_value=["dark"])
        store = self.main["Store"](self.db_path if not fail else os.path.join(self.tmp, "none"))
        self.main["record_launch"](
            self.db, preset, "Monaco", 13, "dark", "discovery", None, "Default", "s1"
        )
        texts = []

        class Alert:
            def __init__(self, title, text, window_id=None):
                texts.append(text)
                self.buttons = []

            def add_button(self, title):
                self.buttons.append(title)

            async def async_run(self, connection):
                return 1000 + self.buttons.index(button) if self.buttons else 1000

        with (
            mock.patch.object(self.main["iterm2"], "Alert", Alert),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            asyncio.run(self.main["never_show_from_menu"](None, app, store, "s1"))
        self.variables, self.profile = variables, profile
        return texts

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

    def test_uninstall_removes_our_binding_and_restores_default_keymap(self):
        defaults = {"0xf700-0x300000": {"Action": 10, "Text": ""}}
        with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as f:
            plistlib.dump(defaults, f)
        self.addCleanup(os.remove, f.name)
        real = srcp_uninstall_prefs.DEFAULT_MAP
        srcp_uninstall_prefs.DEFAULT_MAP = f.name
        self.addCleanup(setattr, srcp_uninstall_prefs, "DEFAULT_MAP", real)
        ours = {"Action": 60, "Text": "save_profile()"}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prefs = {"GlobalKeyMap": {**defaults, "0x73-0x1c0000-0x1-0x1": ours}}
            self.assertEqual(srcp_uninstall_prefs.remove_key_binding(prefs), ["GlobalKeyMap"])
            self.assertEqual(prefs["GlobalKeyMap"], defaults)
            edited = {**defaults, "0x61-0x100000": {"Action": 12, "Text": "x"}}
            prefs = {"GlobalKeyMap": {**edited, "0x73-0x1c0000-0x1-0x1": ours}}
            self.assertEqual(srcp_uninstall_prefs.remove_key_binding(prefs), [])
            self.assertEqual(prefs["GlobalKeyMap"], edited)  # the user's own binding stays
            self.assertIsNone(srcp_uninstall_prefs.remove_key_binding({"GlobalKeyMap": edited}))

    def test_uninstall_removes_current_and_past_presets_but_keeps_edited(self):
        with tempfile.TemporaryDirectory() as repo:
            schemes = os.path.join(repo, "schemes")
            os.makedirs(schemes)
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run([*git[:3], "init", "-q"], check=True, env=env)
            for version in (DARK, DARK_2):  # A changes upstream; B never does
                for name, colors in (("A", version), ("B", LIGHT)):
                    with open(os.path.join(schemes, f"{name}.itermcolors"), "wb") as f:
                        plistlib.dump(colors, f)
                subprocess.run([*git, "add", "."], check=True, env=env)
                subprocess.run([*git, "commit", "-qm", "v", "--allow-empty"], check=True, env=env)
            prefs = {"Custom Color Presets": {"A": DARK, "B": YELLOW, "Mine": LIGHT}}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(srcp_uninstall_prefs.remove_presets(prefs, schemes))
            # A was imported before upstream changed it: ours. B was edited; Mine isn't the repo's.
            self.assertEqual(sorted(prefs["Custom Color Presets"]), ["B", "Mine"])

    def test_custom_settings_folder_is_refused_before_writing(self):
        prefs = {"LoadPrefsFromCustomFolder": True, "PrefsCustomFolder": "/elsewhere"}
        self.assertEqual(srcp_iterm_prefs.custom_folder(prefs), "/elsewhere")
        self.assertIsNone(srcp_iterm_prefs.custom_folder({}))

        def no_defaults(*args, **kwargs):  # if the guard ever regressed, never touch real prefs
            raise AssertionError(f"save_prefs ran {args[0]!r} instead of refusing")

        real = srcp_iterm_prefs.subprocess
        srcp_iterm_prefs.subprocess = types.SimpleNamespace(run=no_defaults)
        self.addCleanup(setattr, srcp_iterm_prefs, "subprocess", real)
        with self.assertRaises(SystemExit):
            srcp_iterm_prefs.save_prefs(prefs)


if __name__ == "__main__":
    unittest.main()
