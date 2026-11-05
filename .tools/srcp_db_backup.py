"""Snapshot the scheme DB into backups/ next to it, keeping the newest KEEP.

Uses SQLite's online backup, so it's consistent even while the AutoLaunch script has the DB
open. The build snapshots before every rebuild (snapshot); the AutoLaunch script, which imports
this, once a day (snapshot_if_due, through its own connection).

Snapshots are named schemes-<UTC time>[-n].db and are aged and pruned by that name, not by
file times (a copy, restore or touch changes those). Other files there (manual-*.db, a
schemes-old.db of your own) are never pruned, and neither are names dated in the future.
"""

from __future__ import annotations

import calendar
import contextlib
import glob
import os
import re
import sqlite3
import time
from pathlib import Path

KEEP = 14
DAY_S = 24 * 3600
NAME_TIME = "%Y%m%dT%H%M%SZ"
SNAPSHOT_RE = re.compile(r"schemes-(\d{8}T\d{6}Z)(?:-(\d+))?\.db")


def snapshot_files(folder):
    """[(UTC time, n, path)] of the snapshots in folder, oldest first by name."""
    found = []
    for path in glob.glob(os.path.join(folder, "schemes-*.db")):
        m = SNAPSHOT_RE.fullmatch(os.path.basename(path))
        if not m:
            continue
        try:
            when = calendar.timegm(time.strptime(m.group(1), NAME_TIME))
        except ValueError:  # e.g. month 13: not one of ours
            continue
        found.append((when, int(m.group(2) or 0), path))
    return sorted(found)


def future_snapshots(folder, now=None):
    """Snapshots named after now (a wrong clock, or copied from another Mac)."""
    now = time.time() if now is None else now
    return [path for when, _, path in snapshot_files(folder) if when > now]


def remove_stale_partials(folder, older_than_s=3600):
    """Delete half-written copies a killed run left. Only old ones: the main script or the
    build may be writing its own snapshot right now."""
    for path in glob.glob(os.path.join(folder, "schemes-*.db.partial")):
        try:
            if time.time() - os.path.getmtime(path) > older_than_s:
                os.remove(path)
        except FileNotFoundError:
            pass


def snapshot(db_path, keep=KEEP, src=None):
    """Copy db_path to backups/schemes-<UTC>.db; return the copy's path, or None if no DB.

    src: an open connection to db_path to copy from (the AutoLaunch script's); else one is
    opened and closed here.
    """
    if not os.path.exists(db_path):
        return None
    folder = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(folder, exist_ok=True)
    remove_stale_partials(folder)
    stem = os.path.join(folder, "schemes-" + time.strftime(NAME_TIME, time.gmtime()))
    dest, n = stem + ".db", 1
    while os.path.exists(dest):
        dest, n = f"{stem}-{n}.db", n + 1
    # Not mode=ro: a read-only connection can't open a WAL DB that nothing else has open.
    own = src is None
    uri = Path(db_path).resolve().as_uri() + "?mode=rw"
    source = sqlite3.connect(uri, uri=True) if own else src
    out = sqlite3.connect(dest + ".partial")
    try:
        source.backup(out)
    except BaseException:
        out.close()
        os.remove(dest + ".partial")
        raise
    finally:
        out.close()
        if own:
            source.close()
    os.replace(dest + ".partial", dest)
    prune(folder, keep)
    return dest


def prune(folder, keep=KEEP, now=None):
    """Keep the newest `keep` snapshots by name; future-dated ones are left alone."""
    if keep < 1:  # never prune them all, the one just written included
        raise ValueError(f"prune: keep must be at least 1, not {keep}")
    now = time.time() if now is None else now
    dated = [path for when, _, path in snapshot_files(folder) if when <= now]
    for path in dated[:-keep]:
        with contextlib.suppress(FileNotFoundError):  # the build and the main script both prune
            os.remove(path)


def snapshot_if_due(db_path, every_s=DAY_S, keep=KEEP, src=None, now=None):
    """snapshot() if the newest snapshot (by name, not dated in the future) is every_s old."""
    now = time.time() if now is None else now
    folder = os.path.join(os.path.dirname(db_path), "backups")
    dated = [when for when, _, _ in snapshot_files(folder) if when <= now]
    if dated and now - max(dated) < every_s:
        return None
    return snapshot(db_path, keep, src)
