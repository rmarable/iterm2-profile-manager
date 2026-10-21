#!/usr/bin/env python3
"""Bulk-import .itermcolors files into iTerm2's Custom Color Presets.

Same result as double-clicking each file (preset name = file name, contents
stored verbatim), but with no "imported" dialog per file. Writes through
`defaults` so the macOS prefs cache stays consistent.

An installed preset that differs from its file is updated when it equals an older
version of the file in the repo's git history (imported earlier and changed upstream
since), and kept when it doesn't (you edited it, or it came from elsewhere).

Run from Terminal.app with iTerm2 quit:
    python3 .tools/srcp_import_presets.py ~/src/iTerm2-Color-Schemes/schemes
"""

import argparse
import glob
import os
import plistlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_iterm_prefs import QUIT_FIRST, iterm_running, load_prefs, save_prefs
from srcp_preset_history import past_versions, repo_root

KEY = "Custom Color Presets"


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("schemes_dir", help="folder of .itermcolors files")
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="replace every preset that differs from its file, even ones you edited",
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

    files = sorted(glob.glob(os.path.join(os.path.expanduser(args.schemes_dir), "*.itermcolors")))
    if not files:
        sys.exit(f"No .itermcolors files in {args.schemes_dir}")

    prefs = load_prefs()
    presets = prefs.setdefault(KEY, {})
    root = repo_root(os.path.dirname(files[0]))

    added, replaced, current, kept = [], [], [], []
    for path in files:
        name = os.path.basename(path)[: -len(".itermcolors")]
        with open(path, "rb") as f:
            colors = plistlib.load(f)
        if name not in presets:
            added.append(name)
        elif presets[name] == colors:
            current.append(name)
            continue
        elif args.overwrite or (root and presets[name] in past_versions(root, path)):
            replaced.append(name)
        else:
            kept.append(name)
            continue
        presets[name] = colors

    print(
        f"{len(files)} files: {len(added)} new, {len(replaced)} updated, "
        f"{len(current)} up to date, {len(kept)} kept (differ from the repo's versions)"
    )
    for name in added:
        print(f"  + {name}")
    for name in replaced:
        print(f"  ~ {name}")
    if kept:
        print(f"  = kept: {', '.join(kept)} (--overwrite replaces them)")

    if args.dry_run or not (added or replaced):
        if args.dry_run:
            print("(dry run: nothing written)")
        return

    backup = save_prefs(prefs)
    print(f"Done. Backup of previous prefs: {backup}")


if __name__ == "__main__":
    main()
