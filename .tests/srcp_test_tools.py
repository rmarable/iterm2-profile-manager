"""Tests for the tools beyond the database: iTerm2 prefs access and writers, preset history,
the menu scripts' setup, and setup.sh / uninstall.sh dry runs.

Runs under iTerm2's Python, like srcp_test_db.py, against temp folders only.
"""

import ast
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
import time
import types
import unittest
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".tools"))  # before importing the tools

import srcp_add_key_binding  # noqa: E402  (after the sys.path line above)
import srcp_db_backup  # noqa: E402  (after the sys.path line above)
import srcp_enable_claude_integration  # noqa: E402  (after the sys.path line above)
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
