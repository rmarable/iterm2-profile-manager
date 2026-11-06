"""Tests for the tools beyond the database: iTerm2 prefs access and writers, preset history,
the menu scripts' setup, and setup.sh / uninstall.sh dry runs.

Runs under iTerm2's Python, like srcp_test_db.py, against temp folders only.
"""

import ast
import base64
import contextlib
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".tools"))  # before importing the tools

import srcp_add_key_binding  # noqa: E402  (after the sys.path line above)
import srcp_build_scheme_db  # noqa: E402  (after the sys.path line above)
import srcp_db_backup  # noqa: E402  (after the sys.path line above)
import srcp_enable_claude_integration  # noqa: E402  (after the sys.path line above)
import srcp_fonts  # noqa: E402  (after the sys.path line above)
import srcp_import_presets  # noqa: E402  (after the sys.path line above)
import srcp_iterm_prefs  # noqa: E402  (after the sys.path line above)
import srcp_preset_history  # noqa: E402  (after the sys.path line above)
import srcp_schema  # noqa: E402  (after the sys.path line above)
import srcp_uninstall_prefs  # noqa: E402  (after the sys.path line above)
from srcp_testlib import (  # noqa: E402  (after the sys.path line above)
    DARK,
    DARK_2,
    LIGHT,
    UNREADABLE,
    YELLOW,
    defaults_writes,
    exec_main,
    fake_defaults,
    git,
    schemes_repo,
)


def setUpModule():
    """No test here may reach the real prefs backups, even one that forgets fake_defaults."""
    tmp = tempfile.mkdtemp(prefix="srcp-backups-")
    unittest.addModuleCleanup(shutil.rmtree, tmp)
    real = srcp_iterm_prefs.BACKUP_DIR
    srcp_iterm_prefs.BACKUP_DIR = os.path.join(tmp, "prefs-backups")
    unittest.addModuleCleanup(setattr, srcp_iterm_prefs, "BACKUP_DIR", real)


class HelperTest(unittest.TestCase):
    def test_preset_history_finds_older_versions(self):
        path = os.path.join(schemes_repo(self, [{"A": DARK}, {"A": DARK_2}]), "A.itermcolors")
        root = srcp_preset_history.repo_root(os.path.dirname(path))
        self.assertEqual(len(srcp_preset_history.past_versions(root, path)), 2)
        self.assertTrue(srcp_preset_history.from_repo(DARK, path, root))
        self.assertFalse(srcp_preset_history.from_repo(LIGHT, path, root))

    def test_uninstall_removes_our_binding_and_restores_default_keymap(self):
        defaults = {"0xf700-0x300000": {"Action": 10, "Text": ""}}
        with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as f:
            plistlib.dump(defaults, f)
        self.addCleanup(os.remove, f.name)
        real = srcp_uninstall_prefs.DEFAULT_KEY_MAP
        srcp_uninstall_prefs.DEFAULT_KEY_MAP = f.name
        self.addCleanup(setattr, srcp_uninstall_prefs, "DEFAULT_KEY_MAP", real)
        ours = {"Action": 60, "Text": "save_profile()"}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prefs = {"GlobalKeyMap": {**defaults, "0x73-0x1c0000-0x1-0x1": ours}}
            self.assertEqual(srcp_uninstall_prefs.remove_key_binding(prefs), ["GlobalKeyMap"])
            self.assertEqual(prefs["GlobalKeyMap"], defaults)
            edited = {**defaults, "0x61-0x100000": {"Action": 12, "Text": "x"}}
            prefs = {"GlobalKeyMap": {**edited, "0x73-0x1c0000-0x1-0x1": ours}}
            out.seek(0)
            out.truncate(0)
            self.assertEqual(srcp_uninstall_prefs.remove_key_binding(prefs), [])
            self.assertIn("GlobalKeyMap kept", out.getvalue())  # and how to go back to defaults
            out.seek(0)
            out.truncate(0)
            self.assertEqual(prefs["GlobalKeyMap"], edited)  # the user's own binding stays
            self.assertIsNone(srcp_uninstall_prefs.remove_key_binding({"GlobalKeyMap": edited}))
        self.assertNotIn("GlobalKeyMap kept", out.getvalue())  # no binding of ours: nothing to say

    def test_menu_scripts_check_for_every_tool_they_import(self):
        for script in MENU_SCRIPTS:
            checked = have_tools(os.path.join(ROOT, script))
            if checked is None:
                continue  # the main script exits on ImportError instead
            with self.subTest(script=script):
                uses = tools_used(os.path.join(ROOT, script)) - {"srcp_popup.js"}
                self.assertTrue(uses)
                self.assertEqual({f"{m}.py" for m in checked}, uses)

    def test_uninstall_removes_current_and_past_presets_but_keeps_edited(self):
        # A changes upstream; B never does.
        schemes = schemes_repo(self, [{"A": DARK, "B": LIGHT}, {"A": DARK_2}])
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
            srcp_iterm_prefs.save_prefs(prefs, [])

    def test_prefs_backups_keep_the_newest_by_name(self):
        fake_defaults(self)
        folder = srcp_iterm_prefs.BACKUP_DIR
        os.makedirs(folder)
        stem = f"{srcp_iterm_prefs.DOMAIN}-20261101T0000"
        # One second with many backups: -10 is newer than -9 and -2, though it sorts first as
        # text, and the cut falls among them.
        names = [f"{stem}10Z.plist", f"{stem}11Z.plist"]
        names += [f"{stem}11Z-{n}.plist" for n in range(1, 11)]
        decoys = ["my-own-backup.plist", f"{srcp_iterm_prefs.DOMAIN}-notes.plist"]
        for i, name in enumerate([*names, *decoys]):
            path = os.path.join(folder, name)
            open(path, "w").close()
            scrambled = time.time() - 1000 * ((i * 5) % 13)  # mtime says nothing
            os.utime(path, (scrambled, scrambled))
        utc = time.strptime("20261101T000011Z", "%Y%m%dT%H%M%SZ")
        with (
            mock.patch.object(srcp_iterm_prefs.time, "gmtime", return_value=utc),
            mock.patch.object(srcp_iterm_prefs.time, "time", return_value=1793491211.0),
        ):
            new = srcp_iterm_prefs.save_prefs({"K": 1}, ["K"])  # that second again: -11
        self.assertTrue(new.endswith("-20261101T000011Z-11.plist"))
        ours = [*names, os.path.basename(new)][-srcp_iterm_prefs.BACKUP_KEEP :]
        self.assertEqual(sorted(os.listdir(folder)), sorted([*ours, *decoys]))

    def test_prefs_backups_from_before_and_after_utc_names_rotate_together(self):
        fake_defaults(self)  # also points BACKUP_DIR at a temp folder
        folder = srcp_iterm_prefs.BACKUP_DIR
        os.makedirs(folder)
        local = time.strftime("%Y%m%d-%H%M%S", time.localtime(1_000_000_000))  # 2001
        old = f"{srcp_iterm_prefs.DOMAIN}-{local}.plist"
        new = f"{srcp_iterm_prefs.DOMAIN}-20260101T000000Z.plist"
        future = f"{srcp_iterm_prefs.DOMAIN}-29990101T000000Z.plist"
        bad = f"{srcp_iterm_prefs.DOMAIN}-20261399T000000Z.plist"  # month 13: not one of ours
        for name in (new, future, old, bad):
            open(os.path.join(folder, name), "w").close()
        got = [os.path.basename(p) for p in srcp_iterm_prefs.backup_files(now=1_800_000_000)]
        self.assertEqual(got, [old, new])  # oldest first across both formats; future left out

    def test_load_prefs_reads_defaults_export(self):
        fake_defaults(self, live={"Custom Color Presets": {"A": DARK}, "Flag": True})
        self.assertEqual(
            srcp_iterm_prefs.load_prefs(), {"Custom Color Presets": {"A": DARK}, "Flag": True}
        )

    def test_iterm_running_matches_the_executable_name_only(self):
        cases = {
            "/Applications/iTerm.app/Contents/MacOS/iTerm2": True,
            "iTerm2": True,
            "/Applications/iTerm.app/Contents/MacOS/iTerm2Helper": False,
            "/usr/local/bin/iTerm2-foo": False,
            "/bin/zsh": False,
        }
        for comm, expected in cases.items():
            with self.subTest(comm=comm):
                out = types.SimpleNamespace(stdout=f"/sbin/launchd\n{comm}\n")
                fake = types.SimpleNamespace(run=lambda *a, out=out, **k: out)
                with mock.patch.object(srcp_iterm_prefs, "subprocess", fake):
                    self.assertEqual(srcp_iterm_prefs.iterm_running(), expected)

    def test_save_prefs_writes_only_the_named_keys(self):
        calls = fake_defaults(self, live={"Other": 1})
        prefs = {"Custom Color Presets": {"A": DARK}, "Other": 2, "GlobalKeyMap": {}}
        srcp_iterm_prefs.save_prefs(prefs, ["Custom Color Presets"], delete=["GlobalKeyMap"])
        writes = defaults_writes(calls)
        self.assertEqual([w[0] for w in writes], ["import", "delete"])  # deletes come after
        self.assertEqual(plistlib.loads(writes[0][2]), {"Custom Color Presets": {"A": DARK}})
        self.assertEqual(writes[1][1], ["GlobalKeyMap"])

    def test_save_prefs_refuses_if_iterm2_started_since_loading(self):
        calls = fake_defaults(self, running=True)
        with self.assertRaises(SystemExit) as cm:
            srcp_iterm_prefs.save_prefs({"K": 1}, ["K"])
        self.assertEqual(str(cm.exception), srcp_iterm_prefs.QUIT_FIRST)
        self.assertEqual(defaults_writes(calls), [])

    def test_save_prefs_rechecks_the_custom_folder_on_the_live_prefs(self):
        live = {"LoadPrefsFromCustomFolder": True, "PrefsCustomFolder": "/elsewhere"}
        calls = fake_defaults(self, live=live)
        with self.assertRaises(SystemExit) as cm:
            srcp_iterm_prefs.save_prefs({"K": 1}, ["K"])  # loaded before the folder was set
        self.assertIn("/elsewhere", str(cm.exception))
        self.assertEqual(defaults_writes(calls), [])
        # Read key by key, not by exporting all of the prefs.
        self.assertFalse([a for a, _ in calls if a[:2] == ["defaults", "export"] and a[3] == "-"])

    def test_save_prefs_rejects_unknown_or_overlapping_keys(self):
        calls = fake_defaults(self)
        with self.assertRaisesRegex(ValueError, r"not in prefs: \['Missing'\]$"):
            srcp_iterm_prefs.save_prefs({"K": 1}, ["Missing"])
        with self.assertRaisesRegex(ValueError, r"in both keys and delete: \['K'\]$"):
            srcp_iterm_prefs.save_prefs({"K": 1}, ["K"], delete=["K"])
        self.assertEqual(calls, [])

    def test_save_prefs_with_only_deletes_imports_nothing(self):
        calls = fake_defaults(self)
        srcp_iterm_prefs.save_prefs({"GlobalKeyMap": {}}, [], delete=["GlobalKeyMap"])
        self.assertEqual([w[0] for w in defaults_writes(calls)], ["delete"])

    def test_uninstall_prefs_names_the_keys_it_changed(self):
        keymap, presets = "GlobalKeyMap", "Custom Color Presets"
        cases = [  # (remove_key_binding's result, presets removed) -> (keys, delete) or None
            (None, False, None),  # no binding and no presets: nothing written
            ([], False, ([keymap], [])),
            ([keymap], False, ([], [keymap])),
            (None, True, ([presets], [])),
            ([], True, ([keymap, presets], [])),
            ([keymap], True, ([presets], [keymap])),
        ]
        for binding, removed, expected in cases:
            with self.subTest(binding=binding, presets=removed):
                prefs = {keymap: {"0x73": {}}, presets: {"A": DARK}}
                with (
                    mock.patch.object(srcp_uninstall_prefs, "iterm_running", return_value=False),
                    mock.patch.object(srcp_uninstall_prefs, "load_prefs", return_value=prefs),
                    mock.patch.object(
                        srcp_uninstall_prefs, "remove_key_binding", return_value=binding
                    ),
                    mock.patch.object(srcp_uninstall_prefs, "remove_presets", return_value=removed),
                    mock.patch.object(srcp_uninstall_prefs, "save_prefs") as save,
                    mock.patch.object(sys, "argv", ["srcp_uninstall_prefs.py", "--presets", "x"]),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    srcp_uninstall_prefs.main()
                if expected is None:
                    save.assert_not_called()
                    continue
                keys, delete = save.call_args.args[1], list(save.call_args.args[2])
                self.assertEqual((keys, delete), expected)
                # What save_prefs itself requires: every named key is there, none in both.
                self.assertLessEqual(set(keys), set(prefs))
                self.assertFalse(set(keys) & set(delete))


class PrefsToolsTest(unittest.TestCase):
    """setup.sh's prefs writers: the preset import and the key binding, with defaults faked."""

    def run_main(self, tool, *args, live):
        """tool.main() against `live` prefs; returns (what it imported, or None if it wrote
        nothing at all, its output). A write other than one import fails the test."""
        calls = fake_defaults(self, live=live)
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [f"{tool.__name__}.py", *args]),
            contextlib.redirect_stdout(out),
        ):
            tool.main()
        writes = defaults_writes(calls)
        self.assertIn([verb for verb, _, _ in writes], ([], ["import"]))
        return (plistlib.loads(writes[0][2]) if writes else None), out.getvalue()

    def test_import_adds_new_updates_ours_and_keeps_edited_presets(self):
        first = {"New": LIGHT, "Current": DARK, "Past": DARK, "Edited": DARK}
        schemes = schemes_repo(self, [first, {"Past": DARK_2}])  # only Past changes upstream
        installed = {"Current": DARK, "Past": DARK, "Edited": YELLOW, "Mine": UNREADABLE}
        live = {srcp_iterm_prefs.PRESETS_KEY: installed, "Other": 1}
        expected = {**installed, "New": LIGHT, "Past": DARK_2}  # Edited and Mine stay
        written, out = self.run_main(srcp_import_presets, schemes, live=live)
        self.assertEqual(written, {srcp_iterm_prefs.PRESETS_KEY: expected})  # only that key
        self.assertIn("1 new, 1 updated, 1 up to date, 1 kept", out)
        written, _ = self.run_main(srcp_import_presets, schemes, "--overwrite", live=live)
        self.assertEqual(written, {srcp_iterm_prefs.PRESETS_KEY: {**expected, "Edited": DARK}})
        written, out = self.run_main(srcp_import_presets, schemes, "--dryrun", live=live)
        self.assertIsNone(written)
        self.assertIn("(dry run: nothing written)", out)

    def test_key_binding_is_added_once_and_never_over_yours(self):
        tool = srcp_add_key_binding
        ours = srcp_iterm_prefs.SAVE_BINDING
        other = {"0x61-0x100000": {"Action": 12, "Text": "x"}}
        live = {tool.KEY_MAP: other}
        written, _ = self.run_main(tool, live=live)
        self.assertEqual(written, {tool.KEY_MAP: {**other, tool.BINDING_KEY: ours}})
        live = {tool.KEY_MAP: {**other, tool.BINDING_KEY: dict(ours)}}  # already there
        written, out = self.run_main(tool, live=live)
        self.assertIsNone(written)
        self.assertIn("already bound", out)
        self.assertNotIn("left alone", out)
        # Yours, under another keycode of the same character and modifiers, or in the
        # two-part form without one.
        for key in (f"{tool.CHAR_MODS}-0x2c-0x1", tool.CHAR_MODS):
            with self.subTest(key=key):
                yours = {key: {"Action": 12, "Text": "mine"}}
                written, out = self.run_main(tool, live={tool.KEY_MAP: {**other, **yours}})
                self.assertIsNone(written)
                self.assertIn("left alone", out)


class ClaudeIntegrationTest(unittest.TestCase):
    """srcp_enable_claude_integration.py: --remove takes out exactly what it installs, a dry
    run of either changes nothing, and a new settings.json is owner-only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="srcp-claude-")
        self.addCleanup(shutil.rmtree, self.tmp)
        tool = srcp_enable_claude_integration
        self.src = os.path.join(self.tmp, "iTerm.app", "cc-status")
        os.makedirs(os.path.dirname(self.src))
        open(self.src, "w").close()
        self.link = os.path.join(self.tmp, "config", "cc-status")
        self.settings = os.path.join(self.tmp, "claude", "settings.json")
        for name, value in (
            ("CC_STATUS_SRC", self.src),
            ("CC_STATUS_LINK", self.link),
            ("CLAUDE_SETTINGS", self.settings),
        ):
            patch = mock.patch.object(tool, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def run_tool(self, *args, prefs=None):
        """main() with prefs faked; returns the (prefs, keys, delete) it saved."""
        tool = srcp_enable_claude_integration
        saved = []

        def save(prefs, keys, delete=()):
            saved.append((prefs, keys, list(delete)))
            return "backup"

        with (
            mock.patch.object(tool, "iterm_running", return_value=False),
            mock.patch.object(tool, "load_prefs", return_value=prefs if prefs is not None else {}),
            mock.patch.object(tool, "save_prefs", save),
            mock.patch.object(sys, "argv", ["srcp_enable_claude_integration.py", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            tool.main()
        return saved

    def test_enable_then_remove_leaves_only_what_was_yours(self):
        tool = srcp_enable_claude_integration
        mine = {"type": "command", "command": "/usr/local/bin/my-hook"}
        mixed = {"hooks": [mine, {"type": "command", "command": self.link}]}  # not only ours
        tuned = {"matcher": "Bash", "hooks": [{"type": "command", "command": self.link}]}
        timed = {"hooks": [{"type": "command", "command": self.link, "timeout": 5}]}
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, "w") as f:
            hooks = {
                "Stop": [{"hooks": [mine]}],
                "PreToolUse": [mixed],
                "PostToolUse": [tuned],
                "SessionEnd": [timed],
            }
            json.dump({"hooks": hooks, "x": 1}, f)
        my_trigger = {"action": "MyTrigger", "regex": "error"}
        prefs = {"New Bookmarks": [{"Name": "Default", "Triggers": [my_trigger]}]}
        saved = self.run_tool(prefs=prefs)  # enable
        self.assertEqual(os.readlink(self.link), self.src)
        enabled = saved[0][0]
        self.assertEqual(len(enabled["New Bookmarks"][0]["Triggers"]), 3)

        saved = self.run_tool("--remove", prefs=enabled)
        self.assertFalse(os.path.lexists(self.link))
        with open(self.settings) as f:
            left = json.load(f)
        self.assertEqual(left["x"], 1)
        self.assertEqual(left["hooks"]["Stop"], [{"hooks": [mine]}])  # yours stays
        self.assertEqual(left["hooks"]["PreToolUse"], [mixed])  # a group with yours in it too
        self.assertEqual(left["hooks"]["PostToolUse"], [tuned])  # yours: it has a matcher
        self.assertEqual(left["hooks"]["SessionEnd"], [timed])  # yours: it has a timeout
        self.assertEqual(set(left["hooks"]), {"Stop", "PreToolUse", "PostToolUse", "SessionEnd"})
        prefs_after, keys, delete = saved[0]
        self.assertEqual(prefs_after["New Bookmarks"][0]["Triggers"], [my_trigger])
        self.assertEqual((keys, sorted(delete)), (["New Bookmarks"], sorted(tool.FLAGS)))
        backups = [n for n in os.listdir(os.path.dirname(self.settings)) if ".backup-" in n]
        self.assertTrue(backups)  # settings.json was backed up before each write

    def test_dry_runs_of_enable_and_remove_change_nothing(self):
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, "w") as f:
            json.dump({"x": 1}, f)

        def prefs():  # fresh each run: the tool edits the dict it loads, even in a dry run
            return {"New Bookmarks": [{"Name": "Default", "Triggers": []}]}

        before = tree(self.tmp)
        self.assertEqual(self.run_tool("--dryrun", prefs=prefs()), [])
        self.assertEqual(tree(self.tmp), before)  # no link, settings.json as it was, no backup
        ((enabled, _, _),) = self.run_tool(prefs=prefs())
        before = tree(self.tmp)
        self.assertEqual(self.run_tool("--remove", "--dryrun", prefs=enabled), [])
        self.assertEqual(tree(self.tmp), before)
        self.assertEqual(os.readlink(self.link), self.src)

    def test_a_new_settings_file_is_owner_only(self):
        old = os.umask(0o022)  # a plain open() would then give 0o644, whatever this Mac's umask
        self.addCleanup(os.umask, old)
        self.run_tool(prefs={})
        self.assertEqual(os.stat(self.settings).st_mode & 0o777, 0o600)  # it can hold secrets

    def test_remove_with_only_the_flags_set_writes_no_profiles(self):
        prefs = {
            "New Bookmarks": [{"Name": "Default", "Triggers": []}],
            **dict.fromkeys(srcp_enable_claude_integration.FLAGS, True),
        }
        ((_, keys, delete),) = self.run_tool("--remove", prefs=prefs)
        self.assertEqual(keys, [])  # no trigger was removed, so New Bookmarks isn't rewritten
        self.assertEqual(sorted(delete), sorted(srcp_enable_claude_integration.FLAGS))

    def test_a_link_of_yours_is_kept_by_enable_and_by_remove(self):
        os.makedirs(os.path.dirname(self.link))
        os.symlink("/usr/local/bin/elsewhere", self.link)
        self.run_tool("--remove")
        self.assertEqual(os.readlink(self.link), "/usr/local/bin/elsewhere")  # not ours: stays
        self.run_tool()
        self.assertEqual(os.readlink(self.link), self.src)
        folder = os.path.dirname(self.link)
        kept = [n for n in os.listdir(folder) if ".backup-" in n]
        self.assertEqual(len(kept), 1)  # yours, moved aside rather than deleted
        self.assertEqual(os.readlink(os.path.join(folder, kept[0])), "/usr/local/bin/elsewhere")

    def test_enable_refuses_settings_it_cant_read_before_changing_anything(self):
        os.makedirs(os.path.dirname(self.settings))
        for settings, said in (
            ([], "isn't a JSON object"),
            ({"hooks": []}, '"hooks" isn\'t an object'),
            ({"hooks": {"Stop": {}}}, '"hooks.Stop" isn\'t a list'),
            ({"hooks": {"Stop": ["x"]}}, '"hooks.Stop[0]" isn\'t an object'),
            ({"hooks": {"Stop": [{"hooks": "x"}]}}, '"hooks.Stop[0].hooks" isn\'t a list of'),
            ({"hooks": {"Stop": [{"hooks": [1]}]}}, '"hooks.Stop[0].hooks" isn\'t a list of'),
        ):
            with self.subTest(settings=settings):
                with open(self.settings, "w") as f:
                    json.dump(settings, f)
                before = tree(self.tmp)
                with self.assertRaises(SystemExit) as caught:
                    self.run_tool(prefs={})
                self.assertIn(said, str(caught.exception))
                self.assertEqual(tree(self.tmp), before)  # no link, no backup, no write
        with open(self.settings, "w") as f:
            json.dump({"hooks": {"Other": "of yours"}}, f)  # not an event this adds: fine
        self.run_tool(prefs={})
        self.assertEqual(os.readlink(self.link), self.src)

    def test_remove_with_unreadable_settings_keeps_the_link(self):
        self.run_tool(prefs={})  # enabled: the link is there
        with open(self.settings, "w") as f:
            f.write("[")  # not JSON
        with self.assertRaises(SystemExit):
            self.run_tool("--remove", prefs={})
        self.assertEqual(os.readlink(self.link), self.src)  # not half removed

    def test_settings_backups_keep_the_newest_ten_of_ours(self):
        folder = os.path.dirname(self.settings)
        os.makedirs(folder)
        ours = [f"settings.json.backup-202501{d:02d}-120000" for d in range(1, 13)]
        future = "settings.json.backup-29990101-000000"  # a clock that was set ahead
        mine = ["settings.json.backup-before-upgrade", "cc-status.backup-20250101-000000"]
        for name in [*ours, future, *mine, "settings.json"]:
            with open(os.path.join(folder, name), "w") as f:
                f.write("{}")
        srcp_enable_claude_integration.write_settings({"x": 1})
        left = sorted(os.listdir(folder))
        for name in [*mine, future]:
            self.assertIn(name, left)  # not this tool's, or dated after now: never pruned
        backups = [n for n in left if re.fullmatch(r"settings\.json\.backup-\d{8}-\d{6}", n)]
        backups.remove(future)
        self.assertEqual(len(backups), srcp_enable_claude_integration.SETTINGS_BACKUP_KEEP)
        self.assertEqual(backups[:-1], ours[3:])  # the oldest three went; the new one stays


class TreeTest(unittest.TestCase):
    """tree(), which the dry-run tests compare before and after, sees git changes too."""

    def test_tree_sees_a_commit_branch_or_packed_ref_change(self):
        repo = os.path.dirname(schemes_repo(self, [{"A": DARK}]))
        before = tree(repo)
        git(repo, "branch", "mine")
        self.assertNotEqual(tree(repo), before)
        git(repo, "branch", "-D", "mine")
        self.assertEqual(tree(repo), before)
        git(repo, "branch", "mine")
        git(repo, "pack-refs", "--all")  # as git gc does: refs move into packed-refs
        packed = tree(repo)
        git(repo, "branch", "-D", "mine")  # changes only packed-refs
        self.assertNotEqual(tree(repo), packed)
        deleted = tree(repo)
        git(repo, "commit", "-q", "--allow-empty", "-m", "b")
        self.assertNotEqual(tree(repo), deleted)


class ShellDryRunTest(unittest.TestCase):
    """setup.sh and uninstall.sh --dryrun change nothing.

    They run from a temp copy of this checkout, in a temp HOME that holds a full install, with
    stand-ins for defaults, brew, ps, git, curl and open that log every call. A dry run may only
    make the calls in ALLOWED; anything else (a write, or a new command) fails the test.
    """

    REPO_URL = "https://github.com/mbadolato/iTerm2-Color-Schemes.git"
    DOMAIN = "com.googlecode.iterm2"
    # Every call a dry run is allowed to make, as the stand-ins log it.
    ALLOWED = re.compile(
        r"(defaults read com\.googlecode\.iterm2 .+"
        r"|defaults export com\.googlecode\.iterm2 -"
        r"|brew list .+"
        r"|ps -axo comm="
        r"|git -C \S.*)$"  # the git stand-in itself lets only exact read-only calls through
    )
    STUBS: ClassVar[dict[str, str]] = {
        # Reads of single keys: "not set". The export is $STUB_PREFS; a write fails.
        "defaults": (
            'case "$1" in\n'
            "read) exit 1 ;;\n"
            'export) [[ "$3" == "-" ]] && exec cat "$STUB_PREFS"; exit 3 ;;\n'
            "*) exit 3 ;;\n"
            "esac"
        ),
        "brew": "exit 1",  # nothing installed with Homebrew
        # Neither script fetches anything or opens apps; one that did is logged, and fails.
        "curl": "exit 3",
        "open": "exit 3",
        # iTerm2 "is running": a dry run must still go through (its guards skip the check).
        "ps": "echo /Applications/iTerm.app/Contents/MacOS/iTerm2",
        # The real git, for exactly the read-only calls the scripts make (after "-C <dir>");
        # anything else is refused, logged as REFUSED, which ALLOWED doesn't match.
        "git": (
            '[[ "$1" == -C ]] && call=("${@:3}") || call=("$@")\n'
            'case "${call[*]}" in\n'
            '"status --porcelain -z --ignored --untracked-files=all" | '
            '"rev-list --count --all --not --remotes" | "worktree list --porcelain" | '
            '"remote get-url origin" | '
            '"rev-parse --show-toplevel" | "log --format=%H -- "* | "show "[0-9a-f]*:*)\n'
            '    exec /usr/bin/git "$@" ;;\n'
            "esac\n"
            'echo "REFUSED git $*" >> "$STUB_LOG"\n'
            "exit 3"
        ),
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="srcp-shell-")
        self.addCleanup(shutil.rmtree, self.tmp)
        # A copy of the checkout, so nothing a regression does can touch the real one.
        self.checkout = os.path.join(self.tmp, "checkout")
        shutil.copytree(
            ROOT,
            self.checkout,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".*_cache", ".tests", "images"),
        )
        self.home = os.path.join(self.tmp, "home")
        stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(stubs)
        self.log = os.path.join(self.tmp, "stub.log")
        for name, body in self.STUBS.items():
            path = os.path.join(stubs, name)
            with open(path, "w") as f:
                f.write(f'#!/bin/bash\necho "{name} $*" >> "$STUB_LOG"\n{body}\n')
            os.chmod(path, 0o700)  # run by this user only
        app = os.path.join(self.tmp, "iTerm.app")
        os.makedirs(os.path.join(app, "Contents", "Resources"))
        with open(
            os.path.join(app, "Contents", "Resources", "DefaultGlobalKeyMap.plist"), "wb"
        ) as f:
            plistlib.dump({}, f)
        self.prefs = os.path.join(self.tmp, "prefs.plist")
        self.env = {
            "HOME": self.home,
            "PATH": f"{stubs}:/usr/bin:/bin",
            "STUB_LOG": self.log,
            "STUB_PREFS": self.prefs,
            "ITERM_APP": app,
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        self.repo, self.git = self.clone()
        self.install()

    def git_run(self, *args):
        subprocess.run([*self.git, *args], env=self.env, check=True, capture_output=True)

    def clone(self):
        """A stand-in schemes clone (origin: the real URL, fully pushed) with two presets."""
        repo = os.path.join(self.home, "src", "iTerm2-Color-Schemes")
        os.makedirs(os.path.join(repo, "schemes"))
        for name, colors in (("Dark One", DARK), ("Light One", LIGHT)):
            with open(os.path.join(repo, "schemes", f"{name}.itermcolors"), "wb") as f:
                plistlib.dump(colors, f)
        git = ["/usr/bin/git", "-C", repo]
        self.git = git
        self.git_run("init", "-q")
        self.git_run("config", "status.showUntrackedFiles", "no")  # uninstall must see past it
        self.git_run("add", "-A")
        self.git_run("commit", "-q", "-m", "a")
        self.git_run("remote", "add", "origin", self.REPO_URL)
        self.git_run("update-ref", "refs/remotes/origin/main", "HEAD")  # as if fetched
        return repo, git

    def install(self):
        """What setup.sh leaves in HOME, so every uninstall step has something to remove."""
        support = os.path.join(self.home, "Library", "Application Support", "iTerm2")
        scripts = os.path.join(support, "Scripts")
        for rel in (*MENU_SCRIPTS, ".tools/srcp_favorites.py", ".tools/srcp_schema.py"):
            os.makedirs(os.path.dirname(os.path.join(scripts, rel)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(scripts, rel))
        os.makedirs(os.path.join(support, "DynamicProfiles"))
        with open(
            os.path.join(support, "DynamicProfiles", "iterm2-profile-manager.plist"), "wb"
        ) as f:
            plistlib.dump({"Profiles": []}, f)
        data = os.path.join(self.home, ".local", "share", "iterm2-color-schemes")
        os.makedirs(os.path.join(data, "backups"))
        with contextlib.closing(sqlite3.connect(os.path.join(data, "schemes.db"))) as db:
            db.execute("CREATE TABLE t (x)")  # not empty: the build refuses a 0-byte DB
        sqlite3.connect(os.path.join(data, "backups", "schemes-20260101T000000Z.db")).close()
        os.makedirs(os.path.join(self.home, "Library", "Fonts"))
        terminal = sorted(os.listdir(TERMINAL_FONTS)) if os.path.isdir(TERMINAL_FONTS) else []
        for name in terminal[:3]:  # identical copies, as setup.sh installs them
            shutil.copy(
                os.path.join(TERMINAL_FONTS, name), os.path.join(self.home, "Library", "Fonts")
            )

    def state(self):
        """What a dry run must leave as it was: HOME, the checkout, iTerm2's prefs."""
        with open(self.prefs, "rb") as f:
            return tree(self.home), tree(self.checkout), f.read()

    def set_prefs(self, **prefs):
        with open(self.prefs, "wb") as f:
            plistlib.dump(prefs, f)

    def run_script(self, *args):
        result = subprocess.run(
            ["/bin/bash", os.path.join(self.checkout, args[0]), *args[1:]],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def assert_only_allowed_calls(self, expected=()):
        """Every logged call is allowed, and the expected ones were made (so a script that
        bypassed the stand-ins, with an absolute path, can't pass on an empty log)."""
        with open(self.log) as f:
            calls = f.read().splitlines()
        self.assertEqual([c for c in calls if not self.ALLOWED.match(c)], [])
        for call in expected:
            self.assertIn(call, calls)

    def test_setup_dry_run_changes_nothing(self):
        self.set_prefs(**{"Custom Color Presets": {"Dark One": DARK}})  # no key binding yet
        before = self.state()
        out = self.run_script("setup.sh", "--dryrun")
        self.assertEqual(self.state(), before)
        self.assert_only_allowed_calls([f"defaults export {self.DOMAIN} -"])
        # The prefs writers got as far as their own dry run, and said so.
        self.assertIn("+ Light One", out)  # import: one new preset
        self.assertIn("Ctrl-Opt-Cmd-S -> Invoke Script Function", out)  # key binding
        self.assertEqual(out.count("(dry run: nothing written)"), 3)  # import, build, binding
        self.assertNotIn("key binding skipped", out)
        # It would install exactly the .tools/ files the installed scripts import.
        self.assertEqual(tool_files(out, "$ cp "), tools_needed())

    def test_uninstall_dry_run_changes_nothing(self):
        binding = {"0x73-0x1c0000-0x1-0x1": {"Action": 60, "Text": "save_profile()"}}
        self.set_prefs(
            **{"GlobalKeyMap": binding, "Custom Color Presets": {"Dark One": DARK, "Mine": LIGHT}}
        )
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a") as f:
            f.write(".DS_Store\n._*\n")  # as the schemes repo's own .gitignore does
        os.makedirs(os.path.join(self.repo, "my café"))  # git quotes such paths unless -z
        for name in (".DS_Store", "._mine.itermcolors", "my café/.DS_Store"):
            open(os.path.join(self.repo, name), "w").close()  # what Finder leaves
        before = self.state()
        out = self.run_script("uninstall.sh", "--dryrun", "--all")
        self.assertEqual(self.state(), before)
        self.assert_only_allowed_calls([f"defaults export {self.DOMAIN} -"])
        self.assertIn("(dry run: nothing written)", out)  # the prefs part
        self.assertIn("- 1 color presets matching", out)  # Dark One; Mine isn't the repo's
        for step in ("iterm2-profile-manager.plist", "srcp_ratings.py", "srcp-curation-"):
            self.assertIn(step, out)  # each would be removed (or exported), had this been real
        self.assertIn(
            "$ rm -rf", out
        )  # the clean, pushed clone would go: Finder's files aren't yours
        self.assertEqual(tool_files(out, "$ rm "), tools_needed())  # what setup.sh installs

    def write_record(self, lines, end="\n"):
        """setup.sh's install record, with these lines (None: none, an old install); end="":
        no newline after the last one, as a hand edit can leave it."""
        path = os.path.join(self.home, ".local", "share", "iterm2-profile-manager", "installed.txt")
        if lines is None:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("\n".join(lines) + end)
        return path

    def fonts_out(self, manifest, end="\n"):
        """uninstall.sh --dryrun --fonts with this install record (None: none, an old install)."""
        self.write_record(manifest, end)
        self.set_prefs()
        return self.run_script("uninstall.sh", "--dryrun", "--fonts")

    def installed_font(self, n=1):
        """(folder, name) of a Terminal.app font install() put in HOME (with n > 1, (folder,
        names)), or skip."""
        fonts = os.path.join(self.home, "Library", "Fonts")
        names = sorted(os.listdir(fonts))
        if len(names) < n:
            self.skipTest("not enough Terminal.app fonts on this Mac")
        return (fonts, names[0]) if n == 1 else (fonts, names[:n])

    def sha256(self, path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_setup_never_overwrites_a_font_it_cant_tell_is_its_own(self):
        fonts, (unhashed, replaced, ours) = self.installed_font(3)
        for name in (unhashed, replaced, ours):
            with open(os.path.join(fonts, name), "ab") as f:
                f.write(b"different")  # differs from Terminal.app's copy now
        self.set_prefs()
        left_alone = "= {}: a different copy is installed; left alone"
        out = self.run_script("setup.sh", "--dryrun")  # no record: an install before 0.11.0
        for name in (unhashed, replaced, ours):
            self.assertIn(left_alone.format(name), out)
        record = [
            f"font {unhashed}",  # recorded before hashes were: can't tell it's ours
            f"font {replaced}",
            f"fonthash {'0' * 64} {replaced}",  # not the copy setup.sh installed: yours
            f"font {ours}",
            f"fonthash {self.sha256(os.path.join(fonts, ours))} {ours}",  # setup.sh's, unchanged
        ]
        self.write_record(record)
        out = self.run_script("setup.sh", "--dryrun")
        for name in (unhashed, replaced):
            self.assertIn(left_alone.format(name), out)
            self.assertNotIn(f"{name} {os.path.join(fonts, name)}", out)  # no install over it
        self.assertIn(f"updated: {ours}", out)
        self.assertIn(f"{TERMINAL_FONTS}/{ours} {os.path.join(fonts, ours)}", out)

    def test_uninstall_fonts_checks_the_records_fonts_even_if_terminal_lost_them(self):
        fonts, name = self.installed_font()
        open(os.path.join(fonts, "Gone.otf"), "w").close()  # recorded; Terminal.app has no copy
        path = os.path.join(fonts, name)
        with open(path, "ab") as f:
            f.write(b"older")  # Terminal's copy changed since setup.sh installed this one
        record = [
            f"font {name}",
            f"fonthash {self.sha256(path)} {name}",
            "font Gone.otf",  # last, with no newline after it
        ]
        os.remove(os.path.join(self.tmp, "stubs", "brew"))  # Homebrew not on PATH
        out = self.fonts_out(["cask font-meslo-lg-nerd-font", *record], end="")
        self.assertIn("Gone.otf: Terminal.app's copy not found, so it can't be checked", out)
        self.assertIn(f"$ rm -f {path}", out)  # unchanged since setup.sh installed it: ours
        self.assertIn("Homebrew not found", out)
        self.assertNotIn("installed.txt", out)  # kept: it still lists Gone.otf and the cask
        self.assertIn("record keeps what's still there: font Gone.otf, cask font-meslo-lg", out)

    def test_uninstall_data_removes_exactly_the_files_the_scripts_make(self):
        data = os.path.join(self.home, os.path.relpath(srcp_schema.DATA_DIR, HOME))
        backups = os.path.join(data, "backups")
        main = exec_main(self.home)  # its paths, as they are with this HOME
        lock = os.path.basename(main["LOCK_PATH"])
        ours = [os.path.join(data, n) for n in ("schemes.db-wal", "schemes.db-shm", lock)]
        for path in ours:
            open(path, "w").close()
        stem = os.path.join(backups, "schemes-20260102T000000Z")
        ours += [srcp_db_backup.claim(stem) + ".partial", srcp_db_backup.claim(stem)]  # as named
        os.rename(ours[-1] + ".partial", ours[-1])  # a finished snapshot -1 beside it
        ours += [
            os.path.join(data, "schemes.db"),
            os.path.join(backups, "schemes-20260101T000000Z.db"),
        ]
        ours.append(os.path.join(backups, "schemes-29990101T000000Z.db.partial"))  # half-written
        mine = os.path.join(backups, "manual-x.db")
        # Dated after now (a clock set ahead), plain and with a -n suffix.
        future = [os.path.join(backups, f"schemes-29990101T000000Z{n}.db") for n in ("", "-1")]
        linked = os.path.join(backups, "schemes-20260103T000000Z.db")  # named as ours, a link
        for path in (ours[-1], mine, *future):
            open(path, "w").close()
        os.symlink(mine, linked)
        self.set_prefs()
        out = self.run_script("uninstall.sh", "--dryrun", "--data")
        section = out.split("==> Scheme DB")[1]
        removed = [line[len("  $ rm -f ") :] for line in section.splitlines() if "$ rm -f" in line]
        self.assertEqual(sorted(removed), sorted(ours))  # ours, and nothing else
        self.assertIn(f"left in place: {mine}", out)
        self.assertIn(f"left in place: {linked}", out)
        for path in future:
            self.assertIn(f"left in place (dated in the future): {path}\n", out)
            self.assertEqual(out.count(path + "\n"), 1)  # said once, not listed again
        self.assertIn(f"$ rm -f {main['DYNAMIC_PATH']}\n", out)  # the file the script writes

    def data_section(self):
        self.set_prefs()
        return self.run_script("uninstall.sh", "--dryrun", "--data").split("==> Scheme DB")[1]

    def test_uninstall_data_keeps_the_newest_snapshot_over_an_empty_db(self):
        data = os.path.join(self.home, ".local", "share", "iterm2-color-schemes")
        open(os.path.join(data, "schemes.db"), "w").close()  # 0 bytes: holds nothing
        snapshot = os.path.join(data, "backups", "schemes-20260101T000000Z.db")
        out = self.data_section()
        self.assertIn("schemes.db is empty: keeping and exporting the newest snapshot", out)
        self.assertIn(f"$ cp {snapshot} ", out)
        self.assertNotIn("VACUUM INTO", out)
        self.assertIn(f"$ rm -f {snapshot}\n", out)  # after it was kept, as with no DB

    def test_uninstall_data_deletes_nothing_for_an_empty_db_with_a_wal(self):
        data = os.path.join(self.home, ".local", "share", "iterm2-color-schemes")
        open(os.path.join(data, "schemes.db"), "w").close()
        with open(os.path.join(data, "schemes.db-wal"), "w") as f:
            f.write("changes not yet written back")
        for db_there in (True, False):  # an empty DB, or none, beside a -wal with data
            with self.subTest(db_there=db_there):
                if not db_there:
                    os.remove(os.path.join(data, "schemes.db"))
                self.set_prefs()
                result = subprocess.run(
                    [
                        "/bin/bash",
                        os.path.join(self.checkout, "uninstall.sh"),
                        "--dryrun",
                        "--data",
                    ],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                self.assertEqual(result.returncode, 1)  # a failure, said again at the end
                self.assertIn("! The scheme DB folder was left alone", result.stderr)
                self.assertIn("is empty or missing but", result.stderr)
                self.assertIn("Don't open the DB", result.stderr)
                section = result.stdout.split("==> Scheme DB")[1]
                self.assertNotIn("$ rm", section)
                self.assertNotIn("$ cp", section)

    def test_setup_pulls_only_a_clone_of_the_schemes_repo(self):
        self.set_prefs()
        pull = f"$ git -C {self.repo} pull --ff-only"
        self.assertIn(pull, self.run_script("setup.sh", "--dryrun"))
        self.git_run("remote", "set-url", "origin", "https://example.invalid/mine.git")
        out = self.run_script("setup.sh", "--dryrun")
        self.assertNotIn(pull, out)
        self.assertIn("origin: https://example.invalid/mine.git): not pulled, used as it is", out)

    def test_a_scripts_folder_that_is_another_clone_is_left_alone(self):
        scripts = os.path.join(self.home, "Library", "Application Support", "iTerm2", "Scripts")
        os.makedirs(os.path.join(scripts, ".git"))  # a Scripts folder you keep in git yourself
        tools = os.path.join(scripts, ".tools")
        autolaunch = os.path.join(scripts, "AutoLaunch", "srcp_set_random_color_profile.py")
        self.assertTrue(os.path.exists(autolaunch))  # install() put it there
        self.set_prefs()
        out = self.run_script("setup.sh", "--dryrun")
        self.assertIn("==> Installing scripts into", out)  # copies: not a checkout of ours
        self.assertIn("$ rm -f", self.run_script("uninstall.sh", "--dryrun"))
        open(os.path.join(scripts, "setup.sh"), "w").close()  # a setup.sh of yours: still not
        self.assertIn("==> Installing scripts into", self.run_script("setup.sh", "--dryrun"))
        shutil.copy(os.path.join(ROOT, ".tools", "srcp_build_scheme_db.py"), tools)  # a checkout
        out = self.run_script("setup.sh", "--dryrun")
        self.assertIn(f"the Scripts folder, {os.path.realpath(scripts)}, is another checkout", out)
        self.assertNotIn("retired AutoLaunch scripts", out)  # an old checkout may track them
        self.assertNotIn(f" {scripts}/", out.split("==> Not installing")[1].split("==>")[0])
        self.assertNotIn("$ cp ", out)
        out = self.run_script("uninstall.sh", "--dryrun")
        self.assertIn(f"$ mv {autolaunch} {autolaunch}.disabled", out)  # stops it, keeps it
        self.assertIn("its files stay", out)
        self.assertNotIn("srcp_ratings.py", out)
        os.rename(autolaunch, autolaunch + ".disabled")  # as uninstall leaves it: still ours
        out = self.run_script("setup.sh", "--dryrun")
        self.assertNotIn("$ cp ", out)
        self.assertIn(f"$ mv {autolaunch}.disabled", out)  # re-enabled, not copied over

    def test_uninstall_fonts_removes_only_what_setup_installed(self):
        fonts = os.path.join(self.home, "Library", "Fonts")
        if not os.listdir(fonts):
            self.skipTest("no Terminal.app fonts on this Mac")
        name = os.listdir(fonts)[0]  # a copy identical to Terminal's
        self.assertIn(f"$ rm -f {fonts}/{name}", self.fonts_out([f"font {name}"]))
        out = self.fonts_out(["font some-other.otf"])  # setup didn't install this one
        self.assertIn(f"{name}: not in setup.sh's record; left alone", out)
        self.assertNotIn(f"rm -f {fonts}/{name}", out)
        self.assertIn("not installed by setup.sh; left alone", out)  # nor the Homebrew font

    def test_uninstall_fonts_without_a_record_leaves_everything(self):
        fonts = os.path.join(self.home, "Library", "Fonts")
        if not os.listdir(fonts):
            self.skipTest("no Terminal.app fonts on this Mac")
        out = self.fonts_out(None)  # an install from before setup.sh kept a record
        self.assertIn("no record of what setup.sh installed", out)
        self.assertNotIn(f"$ rm -f {fonts}/", out)
        self.assertNotIn("$ brew uninstall", out)

    def test_uninstall_fonts_keeps_the_record_of_what_it_left(self):
        fonts = os.path.join(self.home, "Library", "Fonts")
        if not os.listdir(fonts):
            self.skipTest("no Terminal.app fonts on this Mac")
        name = os.listdir(fonts)[0]
        with open(os.path.join(fonts, name), "ab") as f:
            f.write(b"edited")  # changed since setup.sh copied it
        out = self.fonts_out([f"font {name}"])
        self.assertIn(f"{name}: changed since setup.sh installed it; left alone", out)
        self.assertNotIn("installed.txt", out)  # the record isn't deleted: it still lists it

    def test_uninstall_data_keeps_a_copy_and_files_of_yours(self):
        data = os.path.join(self.home, ".local", "share", "iterm2-color-schemes")
        mine = [
            os.path.join(data, "backups", "manual-before-upgrade.db"),
            os.path.join(data, "notes.txt"),
        ]
        for path in mine:
            open(path, "w").close()
        self.set_prefs()
        out = self.run_script("uninstall.sh", "--dryrun", "--data")
        self.assertRegex(out, r"srcp-curation-\d{8}-\d{6}\.db")  # the copy beside the export
        self.assertIn(f"$ rm -f {data}/backups/schemes-20260101T000000Z.db", out)
        self.assertNotIn("rm -rf", out)
        for path in mine:
            self.assertIn(f"left in place: {path}", out)

    def test_uninstall_data_with_nothing_to_keep_deletes_nothing(self):
        data = os.path.join(self.home, ".local", "share", "iterm2-color-schemes")
        os.remove(os.path.join(data, "schemes.db"))
        backups = os.path.join(data, "backups")
        os.rename(
            os.path.join(backups, "schemes-20260101T000000Z.db"),
            os.path.join(backups, "schemes-29990101T000000Z.db"),  # dated in the future
        )
        self.set_prefs()
        out = self.run_script("uninstall.sh", "--dryrun", "--data")
        self.assertIn("no snapshot to keep", out)
        self.assertNotIn("$ rm -f", out.split("==> Scheme DB")[1])

    def test_uninstall_data_never_keeps_a_link_named_like_a_snapshot(self):
        data = os.path.join(self.home, ".local", "share", "iterm2-color-schemes")
        os.remove(os.path.join(data, "schemes.db"))  # so the newest snapshot is what's kept
        mine = os.path.join(data, "backups", "manual-x.db")
        open(mine, "w").close()
        os.symlink(mine, os.path.join(data, "backups", "schemes-20260103T000000Z.db"))  # newer
        self.set_prefs()
        out = self.run_script("uninstall.sh", "--dryrun", "--data")
        real = os.path.join(data, "backups", "schemes-20260101T000000Z.db")
        self.assertIn(f"$ cp {real} ", out)  # the real snapshot, not the link's target
        self.assertIn(f"--db {real} ", out)

    def test_help_prints_the_whole_header_comment(self):
        for script, first, last in (
            ("setup.sh", "Set up iTerm2", "SRC_DIR=~/code ./setup.sh"),
            ("uninstall.sh", "Undo setup.sh", "./uninstall.sh [--data] [--presets]"),
        ):
            with self.subTest(script=script):
                lines = self.run_script(script, "--help").splitlines()
                self.assertTrue(lines[0].startswith(first))
                self.assertTrue(lines[-1].strip().startswith(last))
                self.assertFalse([line for line in lines if line.startswith(("#", "set -"))])

    def test_dry_runs_when_the_checkout_is_the_scripts_folder(self):
        support = os.path.join(self.home, "Library", "Application Support", "iTerm2")
        shutil.rmtree(os.path.join(support, "Scripts"))
        os.symlink(self.checkout, os.path.join(support, "Scripts"))
        self.set_prefs()
        before = self.state()
        setup = self.run_script("setup.sh", "--dryrun")
        uninstall = self.run_script("uninstall.sh", "--dryrun")
        self.assertEqual(self.state(), before)
        self.assert_only_allowed_calls()
        self.assertNotIn("==> Installing scripts into", setup)
        self.assertIn("this checkout is the Scripts folder", uninstall)
        self.assertIn(".py.disabled", uninstall)  # it would disable the script, not delete it

    def test_uninstall_repo_keeps_a_clone_with_work_in_it(self):
        self.set_prefs()

        def outcome():
            out = self.run_script("uninstall.sh", "--dryrun", "--repo")
            return "kept" if "left alone" in out else "deleted" if "$ rm -rf" in out else out

        self.assertEqual(outcome(), "deleted")  # clean and pushed: nothing of yours in it
        open(os.path.join(self.repo, "mine.itermcolors"), "w").close()
        self.assertEqual(outcome(), "kept")  # untracked, though git config hides those
        self.git_run("add", "-A")
        self.git_run("stash", "-q")
        self.assertEqual(outcome(), "kept")  # stash
        self.git_run("stash", "drop", "-q")
        self.git_run("worktree", "add", "-q", "--detach", os.path.join(self.tmp, "wt"))
        self.assertEqual(outcome(), "kept")  # a linked worktree
        self.git_run("worktree", "remove", os.path.join(self.tmp, "wt"))
        self.assertEqual(outcome(), "deleted")  # back to nothing of yours
        self.git_run("commit", "-q", "--allow-empty", "-m", "b")
        self.assertEqual(outcome(), "kept")  # a commit no remote has
        self.assertTrue(os.path.isdir(self.repo))
        self.assert_only_allowed_calls()

    def test_uninstall_repo_keeps_ignored_files_of_yours(self):
        self.set_prefs()
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a") as f:
            f.write("*.bak\n")  # as a global excludes file would
        out = self.run_script("uninstall.sh", "--dryrun", "--repo")
        self.assertIn("$ rm -rf", out)  # an exclude alone isn't yours: nothing matches it yet
        open(os.path.join(self.repo, "notes.bak"), "w").close()
        out = self.run_script("uninstall.sh", "--dryrun", "--repo")
        self.assertIn("has ignored files of yours (notes.bak); left alone", out)
        self.assertNotIn("$ rm -rf", out)
        self.assert_only_allowed_calls()


MENU_SCRIPTS = (
    "AutoLaunch/srcp_set_random_color_profile.py",
    "srcp_ratings.py",
    "srcp_never_show.py",
    "srcp_score_presets.py",
)
TERMINAL_FONTS = "/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts"
HOME = os.path.expanduser("~")  # the real one, which the modules' paths were built from


def tools_used(path):
    """The .tools/ files a script needs: its srcp_ imports found in .tools/, theirs in turn,
    and srcp_popup.js with srcp_popup.py."""
    found, todo = set(), [path]
    while todo:
        with open(todo.pop()) as f:
            parsed = ast.parse(f.read())
        for node in ast.walk(parsed):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                file = os.path.join(ROOT, ".tools", f"{name}.py")
                if name.startswith("srcp_") and os.path.exists(file) and f"{name}.py" not in found:
                    found.add(f"{name}.py")
                    todo.append(file)
    if "srcp_popup.py" in found:
        found.add("srcp_popup.js")
    return found


def tools_needed():
    """Every .tools/ file the installed scripts need."""
    return set().union(*(tools_used(os.path.join(ROOT, script)) for script in MENU_SCRIPTS))


def tool_files(out, command):
    """The .tools/ file names on a dry run's echoed lines for command (e.g. "$ cp ")."""
    pattern = re.compile(r"/\.tools/(srcp_\w+\.(?:py|js))")
    return {m for line in out.splitlines() if command in line for m in pattern.findall(line)}


def have_tools(path):
    """The module names a menu script's HAVE_TOOLS checks, or None if it has none."""
    with open(path) as f:
        parsed = ast.parse(f.read())
    for node in parsed.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "HAVE_TOOLS" for t in node.targets
        ):
            return {
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
    return None


# A brew stand-in for the fonts tool: logs every call; `install` puts font-hack's font file in
# $HOME/Library/Fonts and notes an install time, `uninstall` takes both away. $FAKE_BREW
# holds its state; `info` reports installed_time only while the cask is installed.
FAKE_BREW = r"""#!/bin/sh
echo "brew $*" >>"$FAKE_BREW/log"
case "$1" in
list) cat "$FAKE_BREW/installed" 2>/dev/null || true ;;
info) [ -n "$FAKE_BREW_INFO_FAIL" ] && exit 1
	cask="$5"; stamp=null
	grep -qx "$cask" "$FAKE_BREW/installed" 2>/dev/null && stamp=$(cat "$FAKE_BREW/time-$cask")
	font='{"font": ["ttf/Hack-Regular.ttf"], "target": "%s/Library/Fonts/Hack-Regular.ttf"}'
	head='{"casks": [{"token": "%s", "installed_time": %s, "artifacts": '
	[ -n "$FAKE_BREW_NO_FONTS" ] && font='{"zap": [], "home": "%s"}'
	printf "$head[$font, {}]}]}" \
		"$cask" "$stamp" "$HOME" ;;
install) [ -n "$FAKE_BREW_FAIL" ] && exit 1
	echo "$4" >>"$FAKE_BREW/installed"; date +%s%N >"$FAKE_BREW/time-$4"
	touch "$HOME/Library/Fonts/Hack-Regular.ttf" ;;
uninstall) grep -vx "$4" "$FAKE_BREW/installed" >"$FAKE_BREW/i.new"
	mv "$FAKE_BREW/i.new" "$FAKE_BREW/installed"; rm -f "$HOME/Library/Fonts/Hack-Regular.ttf" ;;
esac
"""


class FontsToolTest(unittest.TestCase):
    """.tools/srcp_fonts.py with a temp HOME and DB, a brew stand-in on PATH (only there: the
    tool looks nowhere else), and macOS's font lookups and iTerm2's settings faked."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="srcp-fonts-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        self.fonts_dir = os.path.join(self.home, "Library", "Fonts")
        os.makedirs(self.fonts_dir)
        self.brew_state = os.path.join(self.tmp, "brew")
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.brew_state)
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, "brew"), "w") as f:
            f.write(FAKE_BREW)
        os.chmod(os.path.join(bin_dir, "brew"), 0o755)  # noqa: S103  (a temp stand-in we run)
        env = {"HOME": self.home, "PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_BREW": self.brew_state}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        # Every seeded font "installed", then a DB built against them.
        self.seeds = os.path.join(self.tmp, "seeds")
        os.makedirs(self.seeds)
        for files in srcp_schema.SEED_FONTS.values():
            open(os.path.join(self.seeds, files[0]), "w").close()
        self.db_path = os.path.join(self.tmp, "data", "schemes.db")
        with (
            mock.patch.object(srcp_build_scheme_db, "FONT_DIRS", [self.seeds]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            srcp_build_scheme_db.write_db(self.db_path, [])
        self.known = set()  # the PostScript names macOS "knows"
        self.faces = [
            ["Menlo-Regular", "Menlo", "/System/Library/Fonts/Menlo.ttc", 5, False],
            ["Menlo-Bold", "Menlo", "/System/Library/Fonts/Menlo.ttc", 9, False],
            ["CourierNewPSMT", "Courier New", "/System/Library/Fonts/Supplemental/Courier New.ttf",
             5, False],
            ["Odd-Regular", "Odd Mono", "/Library/Fonts/Odd.ttf", 5, False],
            ["Hack-Regular", "Hack", os.path.join(self.fonts_dir, "Hack-Regular.ttf"), 5, False],
        ]  # fmt: skip
        hack = os.path.join(self.fonts_dir, "Hack-Regular.ttf")
        self.file_faces = {hack: ["Hack-Regular", "HackSans-Regular"]}
        for target, value in (
            ("run_jxa", mock.DEFAULT),
            ("RESOLVE_WAIT_S", 0),
            ("profiles_using", mock.DEFAULT),
        ):
            p = mock.patch.object(srcp_fonts, target, value)
            mocked = p.start()
            self.addCleanup(p.stop)
            if target == "run_jxa":
                mocked.side_effect = self.fake_jxa
            elif target == "profiles_using":
                self.profiles = mocked
                mocked.return_value = []

    def fake_jxa(self, *args):
        if args[0] == "resolve":
            return next((n for n in args[1:] if n in self.known), "")
        if args[0] == "faces":  # every face in those files, not only the fixed-width ones
            return json.dumps([n for path in args[1:] for n in self.file_faces.get(path, [])])
        return json.dumps(self.faces)

    def run_tool(self, *args):
        out = io.StringIO()
        code = 0
        with (
            contextlib.redirect_stdout(out),
            mock.patch.object(srcp_fonts, "FONT_DIRS", [self.seeds, *srcp_schema.FONT_DIRS[2:]]),
        ):
            try:
                srcp_fonts.main([*args, "--db", self.db_path])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def brew_log(self):
        try:
            with open(os.path.join(self.brew_state, "log")) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def installs(self):
        return [c for c in self.brew_log() if " install " in f" {c} " or "uninstall" in c]

    def db_exec(self, sql):
        db = sqlite3.connect(self.db_path)
        db.execute(sql)
        db.commit()
        db.close()

    def row(self, name):
        db = sqlite3.connect(self.db_path)
        try:
            return db.execute(
                "SELECT enabled, missing, label FROM fonts WHERE name = ?", (name,)
            ).fetchone()
        finally:
            db.close()

    def record(self):
        return srcp_schema.read_added_fonts()

    def test_names_match_by_label_cask_slug_or_postscript_name(self):
        queries = (
            "JetBrains Mono",
            "font-jetbrains-mono",
            "jetbrains-mono",
            "JetBrainsMono-Regular",
        )
        for query in queries:
            self.assertEqual([f.label for f in srcp_fonts.find(query)], ["JetBrains Mono"], query)
        self.assertEqual(srcp_fonts.find("nonsense"), [])
        self.assertEqual(len(srcp_fonts.find("font-meslo-lg-nerd-font")), 6)  # "name one"
        code, _ = self.run_tool("--add", "Fira Cod")
        self.assertIn("Did you mean: Fira Code", str(code))

    def test_catalog_has_every_seed_once_with_a_source(self):
        fonts = srcp_fonts.catalog()
        names = [n for f in fonts for n in f.names]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(srcp_fonts.SEED_SOURCES), set(srcp_schema.SEED_FONTS))
        for f in fonts:
            self.assertIn(f.source, ("macOS", "Terminal.app", "Homebrew"), f.label)
            self.assertEqual(bool(f.cask), f.source == "Homebrew", f.label)
            if f.cask:
                self.assertRegex(f.cask, r"^font-[a-z0-9-]+$")

    def test_help_lists_every_font_on_the_page(self):
        cards, _ = srcp_fonts.page_fonts(srcp_fonts.scan(), None, embed=False)
        text = srcp_fonts.parser().format_help()
        shown = [c["label"] for c in cards if not c["label"].startswith("MesloLG M / L")]
        shown += re.findall(r"MesloLG\w+ Nerd Font Mono", " ".join(c["note"] for c in cards))
        self.assertIn("MesloLGLDZ Nerd Font Mono", shown)
        for label in shown:
            self.assertIn(label, text)
        self.assertNotIn("Odd Mono", [c["label"] for c in cards])  # only catalog fonts

    def test_catalog_doc_matches_the_committed_one(self):
        path = os.path.join(ROOT, "docs", "MONOSPACE-FONT-INVENTORY.md")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), srcp_fonts.catalog_doc(), "rerun --catalog > the doc")

    def test_page_sections_and_add_commands(self):
        table = {"Menlo-Regular": (1, None), "CascadiaCodeRoman-Regular": (1, None)}
        cards, _ = srcp_fonts.page_fonts(srcp_fonts.scan(), table, False)
        by = {c["label"]: c for c in cards}
        self.assertEqual(by["Menlo"]["section"], "pool")
        self.assertEqual(by["Menlo"]["add"], "")
        self.assertEqual(by["Courier New"]["section"], "inst")
        self.assertIn('--add "Courier New"', by["Courier New"]["add"])
        self.assertEqual(by["Fira Code"]["section"], "miss")
        self.assertTrue(by["Fira Code"]["web"] and by["Fira Code"]["add"])
        self.assertEqual(by["Cascadia Code"]["section"], "pool")  # its second name is in the pool
        self.assertEqual(by["SF Mono"]["section"], "miss")  # not on this "Mac", not Homebrew:
        self.assertEqual(by["SF Mono"]["add"], "")  # no command that would fail

    def test_page_with_the_pool_unknown_offers_no_commands(self):
        cards, _ = srcp_fonts.page_fonts(srcp_fonts.scan(), None, False)
        by = {c["label"]: c for c in cards}
        self.assertEqual(by["Menlo"]["section"], "inst")
        self.assertFalse(by["Menlo"]["pool_known"])
        self.assertEqual([c["add"] for c in cards if c["add"]], [])

    def test_page_embeds_small_regular_pool_fonts_only_and_escapes(self):
        small = os.path.join(self.fonts_dir, "SF-Mono-Regular.otf")
        with open(small, "wb") as f:
            f.write(b"FONTBYTES")
        big = os.path.join(self.fonts_dir, "Hack-Regular.ttf")
        with open(big, "wb") as f:
            f.truncate(srcp_fonts.EMBED_MAX + 1)
        ttc = os.path.join(self.fonts_dir, "Iosevka.ttc")
        open(ttc, "w").close()
        evil = "SF Mono <!--EMBEDS--></script><img src=x onerror=alert(1)>"
        self.faces[:0] = [
            ["SFMono-Regular", evil, small, 5, False],
            ["Iosevka-Term", "Iosevka Term", ttc, 5, False],
        ]
        table = {
            n: (1, None)
            for n in ("SFMono-Regular", "Menlo-Regular", "Hack-Regular", "Iosevka-Term")
        }
        cards, embeds = srcp_fonts.page_fonts(srcp_fonts.scan(), table, embed=True)
        self.assertEqual([cards[i]["label"] for i in embeds], ["SF Mono"])  # not Menlo, big, .ttc
        page = srcp_fonts.render_page(cards, embeds, "note")
        self.assertIn(base64.b64encode(b"FONTBYTES").decode(), page)
        self.assertNotIn("<img", page)
        self.assertEqual(page.count('id="emb-'), 1)
        self.assertEqual(srcp_fonts.page_fonts(srcp_fonts.scan(), table, embed=False)[1], {})

    def test_page_refuses_to_embed_inside_any_clone_and_reports_db_problems(self):
        clone = os.path.join(self.tmp, "clone")
        os.makedirs(os.path.join(clone, ".git"))
        os.makedirs(os.path.join(clone, ".tools"))
        open(os.path.join(clone, ".tools", "srcp_build_scheme_db.py"), "w").close()
        for out in (os.path.join(ROOT, "page.html"), os.path.join(clone, "sub", "page.html")):
            code, _ = self.run_tool("--out", out, "--no-open")
            self.assertIn("a clone of this project", str(code))
            self.assertFalse(os.path.exists(out))
        self.db_exec("PRAGMA user_version = 1")
        out_path = os.path.join(self.tmp, "page.html")
        code, out = self.run_tool("--out", out_path, "--no-open")
        self.assertEqual(code, 0)
        self.assertIn("The pool isn't shown", out)
        self.assertIn("installed (pool unknown)", out)

    def test_add_installs_records_with_the_time_then_adds_and_remove_undoes_it(self):
        self.known = {"Hack-Regular"}
        code, out = self.run_tool("--add", "Hack")
        self.assertEqual(code, 0, out)
        self.assertIn("brew install --cask -- font-hack", self.brew_log())
        casks, files = self.record()
        self.assertTrue(casks["font-hack"])  # Homebrew's install time
        self.assertEqual(files["Hack-Regular"], [os.path.join(self.fonts_dir, "Hack-Regular.ttf")])
        self.assertEqual(self.row("Hack-Regular"), (1, 0, "Hack"))
        self.assertEqual(self.run_tool("--add", "Hack")[0], 0)  # again: still the tool's
        self.assertIn("font-hack", self.record()[0])
        code, out = self.run_tool("--remove", "Hack")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.row("Hack-Regular")[0], 0)
        self.assertIn("brew uninstall --cask -- font-hack", self.brew_log())
        self.assertEqual(self.record(), ({}, {}))

    def test_a_cask_you_had_or_reinstalled_is_never_uninstalled(self):
        with open(os.path.join(self.brew_state, "installed"), "w") as f:
            f.write("font-hack\n")
        with open(os.path.join(self.brew_state, "time-font-hack"), "w") as f:
            f.write("1")
        self.known = {"Hack-Regular"}
        self.assertEqual(self.run_tool("--add", "Hack")[0], 0)
        self.assertNotIn("font-hack", self.record()[0])  # yours: not recorded
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("this tool didn't install it", out)
        os.makedirs(os.path.dirname(srcp_schema.added_fonts_path()), exist_ok=True)
        with open(srcp_schema.added_fonts_path(), "w") as f:
            f.write("cask font-hack 0\n")  # recorded, but installed again since (time 1)
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("reinstalled since", out)
        self.assertFalse([c for c in self.brew_log() if "uninstall" in c])
        self.assertEqual(self.record()[0], {})

    def test_remove_keeps_a_cask_whose_fonts_are_in_use_or_unknown(self):
        self.known = {"Hack-Regular"}
        self.run_tool("--add", "Hack")
        self.profiles.return_value = ["Work"]
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("Work", out)
        self.assertIn("brew uninstall --cask font-hack", out)  # how, by hand
        self.profiles.return_value = None
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("couldn't be read", out)
        self.profiles.return_value = []
        self.db_exec(
            "INSERT INTO favorites (preset, font, size, stars, saved_at) "
            "VALUES ('X', 'Hack-Regular', 13, 4, '2026-11-06')"
        )
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("1 favorite(s)", out)
        self.assertFalse([c for c in self.brew_log() if "uninstall" in c])
        self.assertIn("font-hack", self.record()[0])

    def test_add_checks_the_db_before_installing_anything(self):
        self.db_exec("PRAGMA user_version = 1")
        self.known = {"Hack-Regular"}
        code, _ = self.run_tool("--add", "Hack")
        self.assertIn("doesn't fit this version", str(code))
        self.assertEqual(self.brew_log(), [])
        os.remove(self.db_path)
        code, _ = self.run_tool("--add", "Hack")
        self.assertIn("No database", str(code))
        self.assertEqual(self.brew_log(), [])

    def test_add_refuses_a_name_macos_doesnt_know_but_keeps_the_record(self):
        code, _ = self.run_tool("--add", "Hack")  # self.known is empty
        self.assertIn("doesn't know a font named Hack-Regular", str(code))
        self.assertIsNone(self.row("Hack-Regular"))
        self.assertIn("font-hack", self.record()[0])  # installed: --remove can undo it

    def test_remove_keeps_a_cask_when_a_face_of_it_is_used_or_unknown(self):
        self.known = {"Hack-Regular"}
        self.run_tool("--add", "Hack")
        # A profile's non-ASCII font is a face from the cask that isn't fixed-width.
        self.profiles.side_effect = lambda names: ["Work"] if "HackSans-Regular" in names else []
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("Work", out)
        self.profiles.side_effect = None
        self.profiles.return_value = []
        self.file_faces.clear()  # macOS can't list the cask's fonts: keep it
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("couldn't list the fonts", out)
        self.file_faces[os.path.join(self.fonts_dir, "Hack-Regular.ttf")] = ["Hack-Regular"]
        self.db_exec("INSERT INTO fonts (name, label) VALUES ('HackSans-Regular', 'Hack Sans')")
        self.file_faces[os.path.join(self.fonts_dir, "Hack-Regular.ttf")].append("HackSans-Regular")
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("still in the pool: HackSans-Regular", out)
        self.db_exec("UPDATE fonts SET enabled = 0 WHERE name = 'HackSans-Regular'")
        self.db_exec("UPDATE config SET value = 'HackSans-Regular' WHERE key = 'nonascii_font'")
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("icon font", out)
        self.assertFalse([c for c in self.brew_log() if "uninstall" in c])

    def test_remove_keeps_the_cask_when_the_font_list_fails(self):
        self.known = {"Hack-Regular"}
        self.run_tool("--add", "Hack")
        jxa = self.fake_jxa

        def failing(*args):
            if args[0] == "faces":
                raise subprocess.CalledProcessError(1, ["osascript"])
            return jxa(*args)

        with mock.patch.object(srcp_fonts, "run_jxa", side_effect=failing):
            code, out = self.run_tool("--remove", "Hack")
        self.assertIn(code, (0, None))
        self.assertIn("list of its fonts couldn't be read", out)
        self.assertFalse([c for c in self.brew_log() if "uninstall" in c])

    def test_remove_keeps_a_cask_homebrew_lists_no_font_files_for(self):
        self.known = {"Hack-Regular"}
        self.run_tool("--add", "Hack")
        with mock.patch.dict(os.environ, {"FAKE_BREW_NO_FONTS": "1"}):
            _, out = self.run_tool("--remove", "Hack")
        self.assertIn("lists no font files", out)
        self.assertFalse([c for c in self.brew_log() if "uninstall" in c])

    def test_a_failing_brew_info_after_install_keeps_the_cask_recorded(self):
        self.known = {"Hack-Regular"}
        with mock.patch.dict(os.environ, {"FAKE_BREW_INFO_FAIL": "1"}):
            code, _ = self.run_tool("--add", "Hack")
        self.assertIn("failed", str(code))
        self.assertEqual(self.record()[0], {"font-hack": ""})
        _, out = self.run_tool("--remove", "Hack")
        self.assertIn("install time wasn't recorded", out)  # unknown: kept, never guessed
        self.assertEqual(self.record()[0], {})

    def test_page_and_db_problems_are_messages_not_tracebacks(self):
        code, _ = self.run_tool("--out", os.path.join(self.tmp, "nope", "page.html"), "--no-open")
        self.assertIn("folder doesn't exist", str(code))
        with open(self.db_path, "wb") as f:
            f.write(b"not a database at all, just bytes" * 100)
        code, _ = self.run_tool("--add", "Monaco")
        self.assertIn("couldn't be opened", str(code))

    def test_a_failed_record_save_leaves_no_temp_file(self):
        folder = os.path.dirname(srcp_schema.added_fonts_path())
        with (
            mock.patch.object(srcp_fonts.os, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
            srcp_fonts.editing_record() as record,
        ):
            record.casks["font-x"] = "1"
        self.assertEqual([n for n in os.listdir(folder) if n.startswith(".added-fonts-")], [])
        self.assertEqual(self.record(), ({}, {}))

    def test_add_reenables_a_font_you_turned_off_and_a_system_font_needs_no_brew(self):
        self.db_exec("UPDATE fonts SET enabled = 0 WHERE name = 'Monaco'")
        self.known = {"Monaco"}
        code, out = self.run_tool("--add", "Monaco")
        self.assertEqual(code, 0, out)
        self.assertIn("back in the pool", out)
        self.assertEqual(self.row("Monaco")[0], 1)
        self.assertEqual(self.brew_log(), [])
        self.assertEqual(self.record(), ({}, {}))  # seeds: the build handles them

    def test_remove_turns_off_every_name_of_a_font(self):
        self.db_exec(
            "INSERT INTO fonts (name, label) VALUES "
            "('CascadiaCode-Regular', 'Cascadia Code'), "
            "('CascadiaCodeRoman-Regular', 'Cascadia Code')"
        )
        _, out = self.run_tool("--remove", "Cascadia Code")
        self.assertIn("CascadiaCode-Regular, CascadiaCodeRoman-Regular", out)
        self.assertEqual(
            (self.row("CascadiaCode-Regular")[0], self.row("CascadiaCodeRoman-Regular")[0]), (0, 0)
        )

    def test_meslo_cask_is_never_recorded_or_uninstalled(self):
        self.known = {"MesloLGMNFM-Regular"}
        self.assertEqual(self.run_tool("--add", "MesloLGM Nerd Font Mono")[0], 0)
        self.assertIn("brew install --cask -- font-meslo-lg-nerd-font", self.brew_log())
        self.assertEqual(self.record()[0], {})
        _, out = self.run_tool("--remove", "MesloLGM Nerd Font Mono")
        self.assertIn("the icon font comes from it", out)
        self.assertFalse([c for c in self.brew_log() if "uninstall" in c])

    def test_record_survives_odd_spacing_and_a_concurrent_edit(self):
        path = srcp_schema.added_fonts_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("cask  font-a 12\nfile X-Regular  /p q/X.ttf\nbogus\n\ncask font-b\n")
        self.assertEqual(
            srcp_schema.read_added_fonts(),
            ({"font-a": "12", "font-b": ""}, {"X-Regular": ["/p q/X.ttf"]}),
        )
        # Two runs at once: the second waits for the lock, then edits what the first saved.
        done = threading.Event()

        def other_run():
            with srcp_fonts.editing_record() as record:
                record.casks["font-c"] = "3"
            done.set()

        with srcp_fonts.editing_record() as record:
            worker = threading.Thread(target=other_run)
            worker.start()
            self.assertFalse(done.wait(0.3))  # held off by the lock
            record.casks.pop("font-a")
        worker.join(10)
        self.assertEqual(self.record()[0], {"font-b": "", "font-c": "3"})

    def test_dryrun_changes_nothing_and_only_reads_brew(self):
        self.known = {"Hack-Regular", "CourierNewPSMT"}
        with open(self.db_path, "rb") as f:
            db_bytes = f.read()

        def state():
            # The stand-in's log and folder, and the DB's folder and -wal/-shm files, which
            # opening the DB touches; the DB's own bytes are compared below.
            return {
                k: v
                for k, v in tree(self.tmp).items()
                if not k.startswith(("brew", "data")) or k == os.path.join("data", "schemes.db")
            }

        before = state()
        system = ["/System/Library/Fonts/Supplemental"]
        for args in (("--add", "Hack"), ("--add", "Courier New"), ("--remove", "Menlo")):
            with mock.patch.object(srcp_fonts, "FONT_DIRS", system):
                code, out = self.run_tool(*args, "--dryrun")
            self.assertIn(code, (0, None), out)
            self.assertIn("[dryrun] would", out)
        after = state()
        before.pop(os.path.join("data", "schemes.db"), None)
        after.pop(os.path.join("data", "schemes.db"), None)
        self.assertEqual(after, before)
        with open(self.db_path, "rb") as f:
            self.assertEqual(f.read(), db_bytes)
        self.assertTrue(self.brew_log())
        for call in self.brew_log():
            self.assertRegex(call, r"^brew (list|info) ")

    def test_brew_missing_from_path_is_reported(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            code, _ = self.run_tool("--add", "Hack")
        self.assertIn("brew isn't on PATH", str(code))


def tree(root):
    """{relative path: (size, mtime, mode)} of every file and folder under root (links not
    followed). Of a .git folder, only HEAD, packed-refs and refs/, by content: what a commit,
    checkout, branch or stash changes (git status itself may touch the rest)."""
    out = {}
    for folder, dirs, files in os.walk(root):
        if ".git" in dirs and not os.path.islink(os.path.join(folder, ".git")):
            git_dir = os.path.join(folder, ".git")
            refs = [os.path.join(git_dir, n) for n in ("HEAD", "packed-refs")]
            for sub, _, names in os.walk(os.path.join(git_dir, "refs")):
                refs += [os.path.join(sub, n) for n in names]
            for path in refs:
                if os.path.isfile(path) and not os.path.islink(path):
                    with open(path, "rb") as f:
                        out[os.path.relpath(path, root)] = hashlib.sha256(f.read()).hexdigest()
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in dirs + files:
            path = os.path.join(folder, name)
            st = os.lstat(path)
            size = 0 if os.path.isdir(path) and not os.path.islink(path) else st.st_size
            out[os.path.relpath(path, root)] = (size, st.st_mtime_ns, st.st_mode)
    return out


if __name__ == "__main__":
    unittest.main()
