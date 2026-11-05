"""Read and write iTerm2's prefs through `defaults`, shared by the tools here.

Writes go through `defaults import`, so the macOS prefs cache stays consistent.
iTerm2 rewrites its prefs when it quits, so writers must run with iTerm2 quit.
"""

import glob
import os
import plistlib
import subprocess
import time

DOMAIN = "com.googlecode.iterm2"
# Not ~/Library/Preferences: every plist there shows up as a settings domain.
BACKUP_DIR = os.path.expanduser("~/.local/share/iterm2-profile-manager/prefs-backups")
BACKUP_KEEP = 10
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


def load_prefs():
    return plistlib.loads(
        subprocess.run(["defaults", "export", DOMAIN, "-"], capture_output=True, check=True).stdout
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
    stem = os.path.join(BACKUP_DIR, f"{DOMAIN}-{time.strftime('%Y%m%d-%H%M%S')}")
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
    old = sorted(glob.glob(os.path.join(BACKUP_DIR, f"{DOMAIN}-*.plist")), key=os.path.getmtime)
    for path in old[:-BACKUP_KEEP]:
        os.remove(path)
    return backup
