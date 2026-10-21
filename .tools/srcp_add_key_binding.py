#!/usr/bin/env python3
"""Bind Ctrl-Opt-Cmd-S to save_profile() (srcp_set_random_color_profile.py's save dialog).

Adds a global key binding in Settings > Keys > Key Bindings with the action
"Invoke Script Function". An existing Ctrl-Opt-Cmd-S binding is left alone. If
GlobalKeyMap isn't set yet, iTerm2 is using its built-in defaults, so those are
copied in first and kept.

Run from Terminal.app with iTerm2 quit (--dryrun works any time):
    python3 .tools/srcp_add_key_binding.py [--dryrun]
"""

import argparse
import os
import plistlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_iterm_prefs import QUIT_FIRST, iterm_app, iterm_running, load_prefs, save_prefs

DEFAULT_MAP = os.path.join(iterm_app(), "Contents/Resources/DefaultGlobalKeyMap.plist")
KEY_MAP = "GlobalKeyMap"

# 0x<character>-0x<modifiers>-0x<keycode>-0x1, as in iterm2.KeyBinding.key:
# "s", Control|Option|Command (1<<18 | 1<<19 | 1<<20), kVK_ANSI_S = 1.
# Format confirmed working in iTerm2 3.7.6.
CHAR_MODS = "0x73-0x1c0000"
BINDING_KEY = CHAR_MODS + "-0x1-0x1"
BINDING = {"Action": 60, "Text": "save_profile()"}  # 60 = INVOKE_SCRIPT_FUNCTION


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
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
    keymap = prefs.get(KEY_MAP)
    if keymap is None:
        if not os.path.exists(DEFAULT_MAP):
            sys.exit(
                f"{KEY_MAP} isn't set and {DEFAULT_MAP} is missing, so adding a key "
                "would drop iTerm2's default bindings. Add it by hand in Settings > Keys "
                "(set ITERM_APP if iTerm2 isn't in /Applications or ~/Applications)."
            )
        with open(DEFAULT_MAP, "rb") as f:
            keymap = plistlib.load(f)
        print(f"  + {KEY_MAP}: start from iTerm2's {len(keymap)} default bindings")

    existing = [k for k in keymap if k == CHAR_MODS or k.startswith(CHAR_MODS + "-")]
    if existing:
        entry = keymap[existing[0]]
        same = entry.get("Action") == BINDING["Action"] and entry.get("Text") == BINDING["Text"]
        print(
            f"  = Ctrl-Opt-Cmd-S already bound ({existing[0]}: {entry})"
            + ("" if same else "; left alone")
        )
        return

    print(f"  + {KEY_MAP}: Ctrl-Opt-Cmd-S -> Invoke Script Function {BINDING['Text']}")
    if args.dry_run:
        print("  (dry run: nothing written)")
        return
    keymap[BINDING_KEY] = BINDING
    prefs[KEY_MAP] = keymap
    print(f"  Backup of previous iTerm2 prefs: {save_prefs(prefs)}")


if __name__ == "__main__":
    main()
