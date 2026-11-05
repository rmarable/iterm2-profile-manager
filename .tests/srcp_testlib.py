"""Fixtures shared by the srcp_test_*.py files: test presets and a fake `defaults`.

Not a test file itself (lint.sh discovers srcp_test_*.py). Importing it puts .tools/ on
sys.path, so the tests can import the tools.
"""

import os
import plistlib
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".tools"))

import srcp_iterm_prefs  # noqa: E402  (needs .tools on sys.path first)


def color(r, g, b):
    return {"Red Component": r, "Green Component": g, "Blue Component": b}


def preset(bg, fg, accents):
    """A full preset: ANSI 0-15 (1-6 and 9-14 are the accents), foreground, background."""
    ansi = [(0, 0, 0), *accents, (0.8, 0.8, 0.8)]
    p = {f"Ansi {i} Color": color(*ansi[i % 8]) for i in range(16)}
    p["Foreground Color"] = color(*fg)
    p["Background Color"] = color(*bg)
    return p


DARK_ACCENTS = [(0.9, 0.2, 0.2), (0.2, 0.8, 0.2), (0.9, 0.9, 0.2), (0.3, 0.4, 1.0)]
DARK_ACCENTS += [(0.9, 0.3, 0.9), (0.2, 0.9, 0.9)]
LIGHT_ACCENTS = [(0.7, 0.0, 0.0), (0.0, 0.5, 0.0), (0.55, 0.45, 0.0), (0.0, 0.2, 0.8)]
LIGHT_ACCENTS += [(0.6, 0.0, 0.6), (0.0, 0.45, 0.5)]
DARK = preset((0.1, 0.1, 0.1), (0.9, 0.9, 0.9), DARK_ACCENTS)
LIGHT = preset((0.97, 0.97, 0.97), (0.1, 0.1, 0.1), LIGHT_ACCENTS)
YELLOW = preset((0.98, 0.9, 0.4), (0.1, 0.1, 0.1), LIGHT_ACCENTS)  # colored background
UNREADABLE = preset((0.2, 0.2, 0.2), (0.3, 0.3, 0.3), DARK_ACCENTS)
DARK_2 = preset((0.12, 0.1, 0.15), (0.85, 0.85, 0.8), DARK_ACCENTS)
PRESETS = {
    "Dark One": DARK,
    "Dark Two": DARK_2,
    "Dark Two Copy": DARK_2,  # the repo's own identical twin of Dark Two
    "dark one": DARK,  # an old spelling of Dark One, not in the repo
    "Light One": LIGHT,
    "Yellow": YELLOW,
    "Murky": UNREADABLE,
}
REPO_NAMES = ["Dark One", "Dark Two", "Dark Two Copy", "Light One", "Yellow", "Murky"]


def fake_defaults(test, live=None, running=False):
    """Swap srcp_iterm_prefs' subprocess for a recorder; return the list of (argv, input).

    `live` is the prefs as they are now (what `defaults export DOMAIN -` and `defaults read
    DOMAIN key` see); `running` is whether ps shows iTerm2. Prefs backups go to a temp
    folder. Nothing reaches the real defaults or ps; test.addCleanup restores both.
    """
    calls = []
    live = {} if live is None else live

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        if argv[0] == "ps":
            out = "/Applications/iTerm.app/Contents/MacOS/iTerm2\n" if running else "zsh\n"
            return types.SimpleNamespace(stdout=out, returncode=0)
        if argv[:2] == ["defaults", "read"]:
            if argv[3] not in live:
                return types.SimpleNamespace(stdout="", returncode=1)
            value = live[argv[3]]
            text = str(int(value)) if isinstance(value, bool) else str(value)
            return types.SimpleNamespace(stdout=text + "\n", returncode=0)
        if argv[:2] == ["defaults", "export"] and argv[3] == "-":
            return types.SimpleNamespace(stdout=plistlib.dumps(live), returncode=0)
        if argv[:2] == ["defaults", "export"]:  # the backup
            with open(argv[3], "wb") as f:
                plistlib.dump(live, f)
        return types.SimpleNamespace(stdout="", returncode=0)

    real_sub, real_dir = srcp_iterm_prefs.subprocess, srcp_iterm_prefs.BACKUP_DIR
    srcp_iterm_prefs.subprocess = types.SimpleNamespace(run=run)
    tmp = tempfile.mkdtemp(prefix="srcp-prefs-")
    srcp_iterm_prefs.BACKUP_DIR = os.path.join(tmp, "prefs-backups")
    test.addCleanup(setattr, srcp_iterm_prefs, "subprocess", real_sub)
    test.addCleanup(setattr, srcp_iterm_prefs, "BACKUP_DIR", real_dir)
    test.addCleanup(shutil.rmtree, tmp)
    return calls


def defaults_writes(calls):
    """The calls from fake_defaults that change prefs: (verb, rest of argv, input)."""
    return [
        (argv[1], argv[3:], data)
        for argv, data in calls
        if argv[0] == "defaults" and argv[1] not in ("export", "read")
    ]
