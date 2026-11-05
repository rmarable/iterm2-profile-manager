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

--remove undoes it: the link (only if it points at this iTerm2's cc-status: a link left from an
iTerm2 since moved is kept), each hook group that is exactly one cc-status hook, the two
triggers where they're exactly ours, and the two flags.
iTerm2's own setup installs the same items, so those go too; anything else stays.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_iterm_prefs import (
    CUSTOM_FOLDER,
    QUIT_FIRST,
    custom_folder,
    iterm_app,
    iterm_running,
    load_prefs,
    save_prefs,
)

CC_STATUS_SRC = os.path.join(iterm_app(), "Contents/Resources/utilities/cc-status")
CC_STATUS_LINK = os.path.expanduser("~/.config/iterm2/cc-status")
CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")

HOOK_EVENTS = [
    "Notification",
    "PermissionRequest",
    "PostToolUse",
    "PreToolUse",
    "SessionEnd",
    "SessionStart",
    "Stop",
    "StopFailure",
    "SubagentStop",
    "UserPromptSubmit",
]
ASYNC_EVENTS = {"PreToolUse", "PostToolUse"}

TRIGGERS = [
    {
        "action": "iTermEnterWorkgroupTrigger",
        "contentregex": "",
        "disabled": False,
        "eventParams": {"jobName": "claude"},
        "matchType": 113,
        "parameter": "builtin.claudeCode",
        "partial": False,
        "regex": "",
    },
    {
        "action": "iTermExitWorkgroupTrigger",
        "contentregex": "",
        "disabled": False,
        "eventParams": {"jobName": "claude", "leaderOnly": True},
        "matchType": 114,
        "parameter": "",
        "partial": False,
        "regex": "",
    },
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
    if os.path.lexists(CC_STATUS_LINK):  # a file, or a link to elsewhere: keep it
        backup = f"{CC_STATUS_LINK}.backup-{stamp()}"
        os.rename(CC_STATUS_LINK, backup)
        print(f"  (moved the existing file to {backup})")
    os.symlink(CC_STATUS_SRC, CC_STATUS_LINK)


def read_settings():
    if not os.path.exists(CLAUDE_SETTINGS):
        return {}
    with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            sys.exit(f"{CLAUDE_SETTINGS} isn't valid JSON ({e}); fix it and run this again.")


SETTINGS_BACKUP_KEEP = 10


def prune_settings_backups(keep):
    """Keep the newest SETTINGS_BACKUP_KEEP settings.json backups this tool made (named
    settings.json.backup-<local time>), always including keep, the one just written. Any other
    file, a hand-made backup included, and names dated after it (a clock set back) stay."""
    folder, base = os.path.split(CLAUDE_SETTINGS)
    ours = re.compile(re.escape(base) + r"\.backup-\d{8}-\d{6}")
    names = sorted(n for n in os.listdir(folder) if ours.fullmatch(n) and n < keep)
    for name in names[: max(0, len(names) - (SETTINGS_BACKUP_KEEP - 1))]:
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(folder, name))


def write_settings(settings):
    """Back up ~/.claude/settings.json, then replace it with settings in one rename."""
    if os.path.exists(CLAUDE_SETTINGS):
        backup = f"{CLAUDE_SETTINGS}.backup-{stamp()}"
        shutil.copy2(CLAUDE_SETTINGS, backup)
        prune_settings_backups(keep=os.path.basename(backup))
    # Write a temp file and rename it over the real one (through a symlink, e.g. dotfiles),
    # so a crash can't leave a half-written settings file.
    target = os.path.realpath(CLAUDE_SETTINGS)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = f"{target}.partial"
    with contextlib.suppress(FileNotFoundError):
        os.remove(tmp)  # a stale one from a crash would keep its old (maybe looser) mode
    try:
        # Owner-only from the start (settings.json can hold secrets), then the old file's mode.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if os.path.exists(target):
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise


def add_hooks(dry):
    settings = read_settings()
    hooks = settings.setdefault("hooks", {})

    added = []
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == CC_STATUS_LINK for g in groups for h in g.get("hooks", [])):
            continue
        hook: dict[str, object] = {"type": "command", "command": CC_STATUS_LINK}
        if event in ASYNC_EVENTS:
            hook["async"] = True
        groups.append({"hooks": [hook]})
        added.append(event)

    if not added:
        print(f"  = {CLAUDE_SETTINGS} already has all {len(HOOK_EVENTS)} hooks")
        return
    print(f"  + {CLAUDE_SETTINGS}: hooks for {', '.join(added)}")
    if not dry:
        write_settings(settings)


def has_trigger(existing, trig):
    return any(
        t.get("action") == trig["action"]
        and (t.get("eventParams") or {}).get("jobName") == "claude"
        for t in existing
    )


def update_prefs(dry):
    # Again here, after the link and hooks: iTerm2 opened since main()'s check would make
    # save_prefs refuse midway. Re-running this after quitting iTerm2 finishes the job.
    if not dry and iterm_running():
        sys.exit(QUIT_FIRST)
    prefs = load_prefs()

    changed = []  # the top-level keys this changes, for save_prefs
    for profile in prefs.get("New Bookmarks", []):
        triggers = profile.setdefault("Triggers", [])
        missing = [t for t in TRIGGERS if not has_trigger(triggers, t)]
        if missing:
            print(f"  + profile {profile.get('Name')!r}: {len(missing)} Claude Code trigger(s)")
            triggers.extend(dict(t) for t in missing)
            if "New Bookmarks" not in changed:
                changed.append("New Bookmarks")
    for key, value in FLAGS.items():
        if prefs.get(key) != value:
            print(f"  + iTerm2 pref {key} = {value}")
            prefs[key] = value
            changed.append(key)

    if not changed:
        print("  = iTerm2 profiles and flags already set")
        return
    if dry:
        return
    backup = save_prefs(prefs, changed)
    print(f"  Backup of previous iTerm2 prefs: {backup}")


def is_our_hook_group(group):
    """A group exactly as add_hooks writes it: one cc-status hook and nothing else (no matcher,
    timeout or other key of yours)."""
    if not isinstance(group, dict) or set(group) != {"hooks"}:
        return False
    hooks = group["hooks"]
    return (
        isinstance(hooks, list)
        and len(hooks) == 1
        and isinstance(hooks[0], dict)
        and set(hooks[0]) <= {"type", "command", "async"}
        and hooks[0].get("type") == "command"
        and hooks[0].get("command") == CC_STATUS_LINK
    )


def unlink_cc_status(dry):
    if not os.path.islink(CC_STATUS_LINK):
        print(
            f"  = {CC_STATUS_LINK}: no link"
            + (" (a file there: left alone)" if os.path.lexists(CC_STATUS_LINK) else "")
        )
        return
    if os.path.realpath(CC_STATUS_LINK) != os.path.realpath(CC_STATUS_SRC):
        print(f"  = {CC_STATUS_LINK} points elsewhere ({os.readlink(CC_STATUS_LINK)}); left alone")
        return
    print(f"  - link {CC_STATUS_LINK}")
    if not dry:
        os.remove(CC_STATUS_LINK)


def remove_hooks(dry):
    settings = read_settings()
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        print(f"  = {CLAUDE_SETTINGS}: no hooks")
        return
    removed = []
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = [g for g in groups if not is_our_hook_group(g)]
        if len(kept) != len(groups):
            removed.append(event)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]  # it held only ours
    if not removed:
        print(f"  = {CLAUDE_SETTINGS}: no cc-status hooks")
        return
    if not hooks:
        del settings["hooks"]
    print(f"  - {CLAUDE_SETTINGS}: cc-status hooks for {', '.join(removed)}")
    if not dry:
        write_settings(settings)


def remove_prefs(dry):
    if not dry and iterm_running():  # again: see update_prefs
        sys.exit(QUIT_FIRST)
    prefs = load_prefs()
    triggers_changed = False
    for profile in prefs.get("New Bookmarks", []):
        triggers = profile.get("Triggers")
        if not isinstance(triggers, list):
            continue
        kept = [t for t in triggers if t not in TRIGGERS]  # exactly ours only
        if len(kept) != len(triggers):
            gone = len(triggers) - len(kept)
            print(f"  - profile {profile.get('Name')!r}: {gone} Claude Code trigger(s)")
            profile["Triggers"] = kept
            triggers_changed = True
    keys = ["New Bookmarks"] if triggers_changed else []
    delete = [k for k in FLAGS if k in prefs]
    for key in delete:
        print(f"  - iTerm2 pref {key}")
    if not (keys or delete):
        print("  = no Claude Code triggers or flags in iTerm2's settings")
        return
    if not dry:
        print(f"  Backup of previous iTerm2 prefs: {save_prefs(prefs, keys, delete)}")


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument(
        "--dryrun",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report what would change without writing anything",
    )
    ap.add_argument(
        "--remove", action="store_true", help="undo it: remove the link, hooks, triggers, flags"
    )
    args = ap.parse_args()

    if not args.remove and not os.path.exists(CC_STATUS_SRC):
        sys.exit(
            f"{CC_STATUS_SRC} not found. This needs iTerm2 3.7 or later "
            "(set ITERM_APP if iTerm2 isn't in /Applications or ~/Applications)."
        )
    if not args.dry_run and iterm_running():
        sys.exit(QUIT_FIRST)
    folder = custom_folder(load_prefs())
    if folder:  # before any write, not just at save_prefs: the link and hooks come first
        if not args.dry_run:
            sys.exit(CUSTOM_FOLDER.format(folder))
        print(f"  ! {CUSTOM_FOLDER.format(folder)}")

    if args.remove:
        unlink_cc_status(args.dry_run)
        remove_hooks(args.dry_run)
        remove_prefs(args.dry_run)
    else:
        link_cc_status(args.dry_run)
        add_hooks(args.dry_run)
        update_prefs(args.dry_run)
    if args.dry_run:
        print("  (dry run: nothing written)")


if __name__ == "__main__":
    main()
