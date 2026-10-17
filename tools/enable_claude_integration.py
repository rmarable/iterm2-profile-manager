#!/usr/bin/env python3
"""Enable iTerm2's Claude Code integration without going through iTerm2's UI.

Reproduces what iTerm2 3.7 sets up itself:
  1. ~/.config/iterm2/cc-status -> iTerm.app/Contents/Resources/utilities/cc-status
  2. a cc-status command hook on each Claude Code event in ~/.claude/settings.json
  3. the two Claude Code "workgroup" triggers in every iTerm2 profile
  4. the iTerm2 flags recording that the hooks and triggers are installed

Does not touch iTerm2's AI chat/API settings (provider, model, API URL, API key).

Idempotent: anything already present is left alone. Steps 3-4 write iTerm2's
prefs, so run from Terminal.app with iTerm2 quit (--dryrun works any time).
"""
import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time

DOMAIN = "com.googlecode.iterm2"
APP = os.environ.get("ITERM_APP", "/Applications/iTerm.app")
CC_STATUS_SRC = os.path.join(APP, "Contents/Resources/utilities/cc-status")
CC_STATUS_LINK = os.path.expanduser("~/.config/iterm2/cc-status")
CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")

HOOK_EVENTS = [
    "Notification", "PermissionRequest", "PostToolUse", "PreToolUse", "SessionEnd",
    "SessionStart", "Stop", "StopFailure", "SubagentStop", "UserPromptSubmit",
]
ASYNC_EVENTS = {"PreToolUse", "PostToolUse"}

TRIGGERS = [
    {"action": "iTermEnterWorkgroupTrigger", "contentregex": "", "disabled": False,
     "eventParams": {"jobName": "claude"}, "matchType": 113,
     "parameter": "builtin.claudeCode", "partial": False, "regex": ""},
    {"action": "iTermExitWorkgroupTrigger", "contentregex": "", "disabled": False,
     "eventParams": {"jobName": "claude", "leaderOnly": True}, "matchType": 114,
     "parameter": "", "partial": False, "regex": ""},
]
FLAGS = {"ClaudeCodeTriggersInstalled": True, "NoSyncClaudeCodeHooksInstalled": True}


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def link_cc_status(dry):
    if os.path.realpath(CC_STATUS_LINK) == os.path.realpath(CC_STATUS_SRC):
        print(f"  = {CC_STATUS_LINK} already linked")
        return
    print(f"  + link {CC_STATUS_LINK} -> {CC_STATUS_SRC}")
    if dry:
        return
    os.makedirs(os.path.dirname(CC_STATUS_LINK), exist_ok=True)
    if os.path.lexists(CC_STATUS_LINK):
        os.remove(CC_STATUS_LINK)
    os.symlink(CC_STATUS_SRC, CC_STATUS_LINK)


def add_hooks(dry):
    settings = {}
    if os.path.exists(CLAUDE_SETTINGS):
        with open(CLAUDE_SETTINGS) as f:
            settings = json.load(f)
    hooks = settings.setdefault("hooks", {})

    added = []
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == CC_STATUS_LINK for g in groups for h in g.get("hooks", [])):
            continue
        hook = {"type": "command", "command": CC_STATUS_LINK}
        if event in ASYNC_EVENTS:
            hook["async"] = True
        groups.append({"hooks": [hook]})
        added.append(event)

    if not added:
        print(f"  = {CLAUDE_SETTINGS} already has all {len(HOOK_EVENTS)} hooks")
        return
    print(f"  + {CLAUDE_SETTINGS}: hooks for {', '.join(added)}")
    if dry:
        return
    if os.path.exists(CLAUDE_SETTINGS):
        shutil.copy2(CLAUDE_SETTINGS, f"{CLAUDE_SETTINGS}.backup-{stamp()}")
    os.makedirs(os.path.dirname(CLAUDE_SETTINGS), exist_ok=True)
    with open(CLAUDE_SETTINGS, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def has_trigger(existing, trig):
    return any(t.get("action") == trig["action"]
               and (t.get("eventParams") or {}).get("jobName") == "claude" for t in existing)


def update_prefs(dry):
    prefs = plistlib.loads(subprocess.run(
        ["defaults", "export", DOMAIN, "-"], capture_output=True, check=True).stdout)

    changed = False
    for profile in prefs.get("New Bookmarks", []):
        triggers = profile.setdefault("Triggers", [])
        missing = [t for t in TRIGGERS if not has_trigger(triggers, t)]
        if missing:
            print(f"  + profile {profile.get('Name')!r}: {len(missing)} Claude Code trigger(s)")
            triggers.extend(dict(t) for t in missing)
            changed = True
    for key, value in FLAGS.items():
        if prefs.get(key) != value:
            print(f"  + iTerm2 pref {key} = {value}")
            prefs[key] = value
            changed = True

    if not changed:
        print("  = iTerm2 profiles and flags already set")
        return
    if dry:
        return
    backup = os.path.expanduser(f"~/Library/Preferences/{DOMAIN}.backup-{stamp()}.plist")
    subprocess.run(["defaults", "export", DOMAIN, backup], check=True)
    subprocess.run(["defaults", "import", DOMAIN, "-"],
                   input=plistlib.dumps(prefs, fmt=plistlib.FMT_BINARY), check=True)
    print(f"  Backup of previous iTerm2 prefs: {backup}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dryrun", "--dry-run", dest="dry_run", action="store_true",
                    help="report what would change without writing anything")
    args = ap.parse_args()

    if not os.path.exists(CC_STATUS_SRC):
        sys.exit(f"{CC_STATUS_SRC} not found. This needs iTerm2 3.7 or later "
                 "(set ITERM_APP if iTerm2 isn't in /Applications).")
    if not args.dry_run and subprocess.run(["pgrep", "-xq", "iTerm2"]).returncode == 0:
        sys.exit("iTerm2 is running. Quit it (Cmd-Q) and run this from Terminal.app.")

    link_cc_status(args.dry_run)
    add_hooks(args.dry_run)
    update_prefs(args.dry_run)
    if args.dry_run:
        print("  (dry run: nothing written)")


if __name__ == "__main__":
    main()
