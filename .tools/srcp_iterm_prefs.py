"""Read and write iTerm2's prefs through `defaults`, shared by the tools here.

Writes go through `defaults import`, so the macOS prefs cache stays consistent.
iTerm2 rewrites its prefs when it quits, so writers must run with iTerm2 quit.
"""

import os
import plistlib
import subprocess
import time

DOMAIN = "com.googlecode.iterm2"
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


def iterm_running():
    # Not `pgrep -x iTerm2`: it can miss iTerm2 (seen from a sandboxed shell).
    out = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, check=True).stdout
    return any(os.path.basename(line.strip()) == "iTerm2" for line in out.splitlines())


def load_prefs():
    return plistlib.loads(
        subprocess.run(["defaults", "export", DOMAIN, "-"], capture_output=True, check=True).stdout
    )


def save_prefs(prefs):
    """Back up the current prefs, write `prefs`, and return the backup path."""
    folder = custom_folder(prefs)
    if folder:  # every writer comes through here; setup.sh and uninstall.sh check earlier too
        raise SystemExit(CUSTOM_FOLDER.format(folder))
    stem = os.path.expanduser(
        f"~/Library/Preferences/{DOMAIN}.backup-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    backup, n = stem + ".plist", 1
    while os.path.exists(backup):  # two writers in one setup.sh run can share a second
        backup, n = f"{stem}-{n}.plist", n + 1
    subprocess.run(["defaults", "export", DOMAIN, backup], check=True)
    subprocess.run(
        ["defaults", "import", DOMAIN, "-"],
        input=plistlib.dumps(prefs, fmt=plistlib.FMT_BINARY),
        check=True,
    )
    return backup
