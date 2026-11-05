"""Read and write iTerm2's prefs through `defaults`, shared by the tools here.

Writes go through `defaults import`, so the macOS prefs cache stays consistent.
iTerm2 rewrites its prefs when it quits, so writers must run with iTerm2 quit.
"""

import calendar
import glob
import os
import plistlib
import re
import subprocess
import time

DOMAIN = "com.googlecode.iterm2"
# Not ~/Library/Preferences: every plist there shows up as a settings domain.
BACKUP_DIR = os.path.expanduser("~/.local/share/iterm2-profile-manager/prefs-backups")
BACKUP_KEEP = 10
# <UTC time>[-n] since v0.12; <local time>[-n] before (still rotated with the rest).
BACKUP_RE = re.compile(re.escape(DOMAIN) + r"-(\d{8}T\d{6}Z|\d{8}-\d{6})(?:-(\d+))?\.plist")
APP_DIRS = ["/Applications/iTerm.app", os.path.expanduser("~/Applications/iTerm.app")]
QUIT_FIRST = "iTerm2 is running. Quit it (Cmd-Q) and run this from Terminal.app."
CUSTOM_FOLDER = (
    "iTerm2 loads its settings from a custom folder or URL ({}), so it would overwrite "
    "changes written here the next time it starts. In iTerm2, turn off Settings > General > "
    "Settings > Load settings from a custom folder or URL, quit it, run this again, then "
    "turn that setting back on and save the current settings to the folder."
)


def custom_folder(prefs):
    """Where iTerm2 loads its settings from, if not from its own prefs; else None."""
    if prefs.get("LoadPrefsFromCustomFolder"):
        return prefs.get("PrefsCustomFolder") or "unknown location"
    return None


def live_custom_folder():
    """custom_folder() of the prefs as they are now, read key by key: a full export is ~7 MB."""

    def read(key):
        out = subprocess.run(
            ["defaults", "read", DOMAIN, key], capture_output=True, text=True, check=False
        )
        return out.stdout.strip() if out.returncode == 0 else None  # 1: the key isn't set

    if read("LoadPrefsFromCustomFolder") not in ("1", "YES", "true"):
        return None
    return read("PrefsCustomFolder") or "unknown location"


def iterm_app():
    """iTerm.app's path: $ITERM_APP, else the first of APP_DIRS that exists."""
    return os.environ.get("ITERM_APP") or next(
        (d for d in APP_DIRS if os.path.isdir(d)), APP_DIRS[0]
    )


def iterm_running():
    # Not `pgrep -x iTerm2`: it can miss iTerm2 (seen from a sandboxed shell).
    out = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, check=True).stdout
    return any(os.path.basename(line.strip()) == "iTerm2" for line in out.splitlines())


def load_prefs(timeout=None):
    """iTerm2's prefs as a dict (a fresh export; about 7 MB with every preset installed)."""
    return plistlib.loads(
        subprocess.run(
            ["defaults", "export", DOMAIN, "-"], capture_output=True, check=True, timeout=timeout
        ).stdout
    )


def save_prefs(prefs, keys, delete=()):
    """Back up the current prefs, write prefs[k] for each top-level key in `keys`, delete the
    top-level keys in `delete`, and return the backup path.

    Only the named keys are written, so a change iTerm2 or anything else made to another key
    since `prefs` was loaded survives. `defaults import` merges top-level keys: one it isn't
    given stays as it is, and one it is given is replaced whole (checked on a scratch domain,
    2026-11-05). A change made to a named key since loading is still lost; callers keep the
    gap short and check iterm_running() first, and this checks it again just before writing.
    """
    keys, delete = list(keys), list(delete)
    unknown = [k for k in keys if k not in prefs]
    both = set(keys) & set(delete)
    if unknown or both:
        problems = [f"not in prefs: {unknown}"] if unknown else []
        problems += [f"in both keys and delete: {sorted(both)}"] if both else []
        raise ValueError("save_prefs: keys " + "; ".join(problems))
    # The prefs the caller loaded, then (after iTerm2 may have been opened and the setting
    # turned on since) the live ones. Every writer comes through here; setup.sh and
    # uninstall.sh check up front too.
    folder = custom_folder(prefs)
    if not folder:
        if iterm_running():  # iTerm2 rewrites its prefs when it quits
            raise SystemExit(QUIT_FIRST)
        folder = live_custom_folder()
    if folder:
        raise SystemExit(CUSTOM_FOLDER.format(folder))
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # UTC: local time repeats an hour when the clocks go back, and the newest could sort first.
    stem = os.path.join(BACKUP_DIR, f"{DOMAIN}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    backup, n = stem + ".plist", 1
    while os.path.exists(backup):  # two writers in one setup.sh run can share a second
        backup, n = f"{stem}-{n}.plist", n + 1
    subprocess.run(["defaults", "export", DOMAIN, backup], check=True)
    if keys:
        subprocess.run(
            ["defaults", "import", DOMAIN, "-"],
            input=plistlib.dumps({k: prefs[k] for k in keys}, fmt=plistlib.FMT_BINARY),
            check=True,
        )
    for key in delete:
        subprocess.run(["defaults", "delete", DOMAIN, key], check=True)
    for path in backup_files()[:-BACKUP_KEEP]:
        os.remove(path)
    return backup


def backup_time(stamp):
    """Seconds since the epoch of a backup name's time (UTC, or local before v0.12); None if
    it isn't a real time."""
    try:
        if stamp.endswith("Z"):
            return calendar.timegm(time.strptime(stamp, "%Y%m%dT%H%M%SZ"))
        return time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S"))
    except (ValueError, OverflowError):
        return None


def backup_files(now=None):
    """The prefs backups save_prefs made, oldest first by the time in their names, then -n.

    By name, not mtime: a copy or touch changes that. Names dated in the future (a wrong
    clock) are left out, so they are never pruned; other files in the folder are left alone.
    """
    now = time.time() if now is None else now
    found = []
    for path in glob.glob(os.path.join(BACKUP_DIR, f"{DOMAIN}-*.plist")):
        m = BACKUP_RE.fullmatch(os.path.basename(path))
        if not m:
            continue
        when = backup_time(m.group(1))
        if when is not None and when <= now + 1:  # + 1: the one just written, this second
            found.append((when, int(m.group(2) or 0), path))
    return [path for *_, path in sorted(found)]
