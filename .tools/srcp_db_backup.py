"""Snapshot the scheme DB into backups/ next to it, keeping the newest KEEP.

Uses SQLite's online backup, so it's consistent even while the AutoLaunch script
has the DB open. AutoLaunch/srcp_set_random_color_profile.py has a copy of snapshot();
keep the two in step.
"""

from __future__ import annotations

import glob
import os
import sqlite3
import time
from pathlib import Path

KEEP = 14


def snapshot(db_path, keep=KEEP):
    """Copy db_path to backups/schemes-<UTC>.db; return the copy's path, or None if no DB."""
    if not os.path.exists(db_path):
        return None
    folder = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(folder, exist_ok=True)
    stem = os.path.join(folder, "schemes-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    dest, n = stem + ".db", 1
    while os.path.exists(dest):
        dest, n = f"{stem}-{n}.db", n + 1
    # Not mode=ro: a read-only connection can't open a WAL DB that nothing else has open.
    src = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=rw", uri=True)
    out = sqlite3.connect(dest + ".partial")
    try:
        src.backup(out)
    except BaseException:
        out.close()
        os.remove(dest + ".partial")
        raise
    finally:
        out.close()
        src.close()
    os.replace(dest + ".partial", dest)
    # By mtime: a same-second "-1" suffix sorts before the plain name.
    snapshots = sorted(glob.glob(os.path.join(folder, "schemes-*.db")), key=os.path.getmtime)
    for old in snapshots[:-keep]:
        os.remove(old)
    return dest
