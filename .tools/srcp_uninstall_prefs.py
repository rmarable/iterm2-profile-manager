#!/usr/bin/env python3
"""Undo setup.sh's changes to iTerm2's prefs: the save_profile() key binding, optionally presets.

Removes every GlobalKeyMap entry that invokes save_profile(). With --presets DIR, also
removes each Custom Color Preset whose name and contents match a DIR/*.itermcolors
file, now or at any earlier commit of the repo (setup.sh imported it and the repo has
changed it since), so presets you've edited, or imported from elsewhere, stay. One
backup, one write.

Run from Terminal.app with iTerm2 quit (--dryrun works any time):
    python3 .tools/srcp_uninstall_prefs.py [--dryrun] [--presets ~/src/iTerm2-Color-Schemes/schemes]
"""

import argparse
import glob
import os
import plistlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_iterm_prefs import QUIT_FIRST, iterm_app, iterm_running, load_prefs, save_prefs
from srcp_preset_history import from_repo, repo_root

KEY_MAP = "GlobalKeyMap"
PRESETS = "Custom Color Presets"
SAVE_ACTION = {"Action": 60, "Text": "save_profile()"}  # as written by srcp_add_key_binding.py
DEFAULT_MAP = os.path.join(iterm_app(), "Contents/Resources/DefaultGlobalKeyMap.plist")


def is_default_keymap(keymap):
    """True if keymap is exactly iTerm2's built-in bindings (as srcp_add_key_binding copied)."""
    try:
        with open(DEFAULT_MAP, "rb") as f:
            return keymap == plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        return False


def remove_key_binding(prefs):
    """Remove our binding; return the top-level keys to delete (GlobalKeyMap, if now default)."""
    keymap = prefs.get(KEY_MAP) or {}
    ours = [
        k
        for k, v in keymap.items()
        if v.get("Action") == SAVE_ACTION["Action"] and v.get("Text") == SAVE_ACTION["Text"]
    ]
    for k in ours:
        print(f"  - {KEY_MAP}: {k} -> Invoke Script Function save_profile()")
        del keymap[k]
    if not ours:
        print("  = no save_profile() key binding")
        return None
    if is_default_keymap(keymap):
        # srcp_add_key_binding copied iTerm2's defaults in to add ours; deleting the key
        # puts iTerm2 back on its built-in defaults, which can change in later versions.
        print(f"  - {KEY_MAP}: the rest is iTerm2's defaults; remove it so iTerm2 uses its own")
        return [KEY_MAP]
    return []


def remove_presets(prefs, schemes_dir):
    presets = prefs.get(PRESETS) or {}
    files = sorted(glob.glob(os.path.join(os.path.expanduser(schemes_dir), "*.itermcolors")))
    if not files:
        print(f"  (skipped presets: no .itermcolors files in {schemes_dir})")
        return False
    root = repo_root(os.path.dirname(files[0]))
    removed, edited = [], []
    for path in files:
        name = os.path.basename(path)[: -len(".itermcolors")]
        if name not in presets:
            continue
        if from_repo(presets[name], path, root):
            removed.append(name)
            del presets[name]
        else:
            edited.append(name)
    print(f"  - {len(removed)} color presets matching {schemes_dir}")
    if edited:
        print(
            f"  = kept {len(edited)} with the same name but different colors: {', '.join(edited)}"
        )
    others = len(presets) - len(edited)
    if others:
        print(f"  = {others} other presets left alone (not from that folder)")
    return bool(removed)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument(
        "--presets",
        metavar="DIR",
        help="also remove presets matching DIR's files, now or in earlier commits",
    )
    ap.add_argument(
        "--dryrun",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report what would change without writing anything",
    )
    args = ap.parse_args()

    if not args.dry_run and iterm_running():
        sys.exit(QUIT_FIRST)
    prefs = load_prefs()
    delete = remove_key_binding(prefs)
    changed = delete is not None
    if args.presets:
        changed = remove_presets(prefs, args.presets) or changed
    if args.dry_run or not changed:
        if args.dry_run:
            print("  (dry run: nothing written)")
        return
    print(f"  Backup of previous iTerm2 prefs: {save_prefs(prefs, delete or ())}")


if __name__ == "__main__":
    main()
