#!/usr/bin/env python3
"""Undo setup.sh's changes to iTerm2's prefs: the save_profile() key binding, optionally presets.

Removes every GlobalKeyMap entry that invokes save_profile(). With --presets DIR, also
removes each Custom Color Preset whose name and contents match a DIR/*.itermcolors
file exactly, so presets you've edited, or imported from elsewhere, stay. One backup,
one write.

Run from Terminal.app with iTerm2 quit (--dryrun works any time):
    python3 .tools/srcp_uninstall_prefs.py [--dryrun] [--presets ~/src/iTerm2-Color-Schemes/schemes]
"""

import argparse
import glob
import os
import plistlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_iterm_prefs import QUIT_FIRST, iterm_running, load_prefs, save_prefs

KEY_MAP = "GlobalKeyMap"
PRESETS = "Custom Color Presets"
SAVE_ACTION = {"Action": 60, "Text": "save_profile()"}  # as written by srcp_add_key_binding.py


def remove_key_binding(prefs):
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
    return bool(ours)


def remove_presets(prefs, schemes_dir):
    presets = prefs.get(PRESETS) or {}
    files = sorted(glob.glob(os.path.join(os.path.expanduser(schemes_dir), "*.itermcolors")))
    if not files:
        print(f"  (skipped presets: no .itermcolors files in {schemes_dir})")
        return False
    removed, edited = [], []
    for path in files:
        name = os.path.basename(path)[: -len(".itermcolors")]
        if name not in presets:
            continue
        with open(path, "rb") as f:
            if presets[name] == plistlib.load(f):
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
    ap.add_argument("--presets", metavar="DIR", help="also remove presets identical to DIR's files")
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
    changed = remove_key_binding(prefs)
    if args.presets:
        changed = remove_presets(prefs, args.presets) or changed
    if args.dry_run or not changed:
        if args.dry_run:
            print("  (dry run: nothing written)")
        return
    print(f"  Backup of previous iTerm2 prefs: {save_prefs(prefs)}")


if __name__ == "__main__":
    main()
