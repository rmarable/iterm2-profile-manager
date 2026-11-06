#!/usr/bin/env python3
"""Review your favorites, their ratings and use, plus rating history and hidden presets.

Reads the scheme DB only, except --show-again; safe any time, even while iTerm2 runs:
    python3 .tools/srcp_favorites.py                     # favorites, best first
    python3 .tools/srcp_favorites.py --mode dark --min-stars 4
    python3 .tools/srcp_favorites.py --history [N]       # last N rating changes (default 30)
    python3 .tools/srcp_favorites.py --hidden            # presets marked Never show
    python3 .tools/srcp_favorites.py --show-again NAME   # undo Never show for NAME
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_schema import DB_PATH as DEFAULT_DB
from srcp_schema import FIX_ON_SQL, REBUILD, UNHIDE, OldSchema, check_schema, is_busy

FAVORITES = """
SELECT f.stars, f.preset, COALESCE(fo.label, f.font) AS font, e.mode,
       count(l.id) AS launches, COALESCE(sum(l.end_reason = 'changed'), 0) AS changed,
       max(l.launched_at) AS last_used,
       CAST(avg(CASE WHEN l.end_reason IS NOT 'changed' THEN l.duration_s END) AS INTEGER)
           AS avg_open, f.saved_at, {adjusted} AS adjusted
FROM favorites f
LEFT JOIN effective e ON e.name = f.preset
LEFT JOIN fonts fo ON fo.name = f.font
LEFT JOIN launches l ON l.preset = f.preset AND l.font = f.font
WHERE f.stars >= ? AND (? IS NULL OR e.mode = ?)
GROUP BY f.preset, f.font
ORDER BY f.stars DESC, f.preset, f.font
"""

# Colors the AutoLaunch script adjusts for this preset (0 if adjustments are off).
ADJUSTED = "CASE WHEN {fix_on} THEN max(e.fix_count) ELSE 0 END"

HISTORY = """
SELECT r.at, r.action, r.previous_stars, r.stars, r.preset, COALESCE(fo.label, r.font, '')
FROM ratings r LEFT JOIN fonts fo ON fo.name = r.font
ORDER BY r.id DESC LIMIT ?
"""

HIDDEN = """
SELECT o.name, COALESCE(o.mode, s.mode, '?'),
       (SELECT max(at) FROM ratings r WHERE r.preset = o.name AND r.action = 'never_show')
FROM overrides o LEFT JOIN schemes s ON s.name = o.name
WHERE o.include = 0 ORDER BY o.name
"""

# FIX_ON_SQL (whether color adjustments are on) and UNHIDE (undo Never show) come from
# .tools/srcp_schema.py, shared with the build and the main script.


def stars(n):
    return f"{n} ★" if n else ""


def local(utc, fmt="%Y-%m-%d %H:%M"):
    """DB times are UTC ('2026-10-18 01:19:05'); show them in local time."""
    if not utc:
        return ""
    try:
        when = dt.datetime.strptime(utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:  # e.g. a row edited by hand in sqlite3: show it as stored
        return utc
    return when.astimezone().strftime(fmt)


def duration(seconds):
    if seconds is None:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s" if m else f"{s}s"


def table(headers, rows):
    rows = [["" if v is None else str(v) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    for line in [headers, ["-" * w for w in widths], *rows]:
        print("  ".join(v.ljust(w) for v, w in zip(line, widths)).rstrip())


def favorite_rows(db, mode, min_stars):
    query = FAVORITES.format(adjusted=ADJUSTED.format(fix_on=FIX_ON_SQL))
    return db.execute(query, (min_stars, mode, mode)).fetchall()


def show_favorites(db, mode, min_stars, cli_hints=True):
    rows = favorite_rows(db, mode, min_stars)
    if not rows:
        print("No favorites match." if mode or min_stars > 1 else "No favorites yet.")
        return
    table(
        [
            "Stars",
            "Preset",
            "Font",
            "Mode",
            "Launches",
            "Changed",
            "Last used",
            "Avg open",
            "Saved",
            "Adjusted",
        ],
        [
            (
                stars(st),
                p,
                f,
                m or "?",
                n,
                ch,
                local(last),
                duration(avg),
                local(saved, "%Y-%m-%d"),
                {0: "", 1: "1 color"}.get(adj, f"{adj} colors"),
            )
            for st, p, f, m, n, ch, last, avg, saved, adj in rows
        ],
    )
    hidden = db.execute("SELECT count(*) FROM overrides WHERE include = 0").fetchone()[0]
    noun = "favorite" if len(rows) == 1 else "favorites"
    hints = " (--hidden). Rating history: --history." if cli_hints else "."
    print(f"\n{len(rows)} {noun}. {hidden} hidden{hints}")


def show_history(db, limit):
    rows = db.execute(HISTORY, (limit,)).fetchall()
    if not rows:
        print("No rating changes recorded yet.")
        return
    labels = {"save": "saved", "remove": "removed", "never_show": "never show"}
    table(
        ["When", "Action", "Stars", "Preset", "Font"],
        [
            (
                local(at),
                labels.get(action, action),
                f"{stars(prev) or '-'} -> {stars(new) or '-'}" if prev or new else "",
                p,
                f,
            )
            for at, action, prev, new, p, f in rows
        ],
    )


SHOW_AGAIN_HINT = "Show one again: python3 .tools/srcp_favorites.py --show-again '<preset>'"


def hidden_rows(db):
    """(name, mode, hidden at) of each Never show preset."""
    return db.execute(HIDDEN).fetchall()


def show_hidden(db, hint=SHOW_AGAIN_HINT):
    """Print the Never show table; hint is printed under it ('' for none). Returns the rows."""
    rows = hidden_rows(db)
    if not rows:
        print("No hidden presets.")
        return rows
    table(["Preset", "Mode", "Hidden"], [(n, m, local(at)) for n, m, at in rows])
    if hint:
        print(f"\n{hint}")
    return rows


class ShownAgain(NamedTuple):
    shown: bool  # False: it wasn't hidden
    installed: bool  # has a schemes row, so it can be picked
    forced: bool  # fails the readability gate and adjustments are off: back with include = 1


def shown_again_note(preset, result):
    """What Show again did, in plain words (the popup and the command line share it)."""
    if not result.shown:
        return f"{preset} isn't hidden (shown again already?)."
    if not result.installed:
        return f"{preset} is back, but it isn't an installed preset, so it won't come up."
    if result.forced:
        return (
            f"{preset} is back. It fails the readability check, "
            "but it's allowed anyway since you had it before."
        )
    return f"{preset} is back."


def show_again(db, preset):
    """Undo Never show for preset; see ShownAgain.

    db must be in autocommit mode (isolation_level=None) so BEGIN IMMEDIATE takes the lock.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        shown = db.execute(UNHIDE, {"name": preset}).rowcount > 0
        installed = db.execute("SELECT 1 FROM schemes WHERE name = ?", (preset,)).fetchone()
        forced = db.execute("SELECT include FROM overrides WHERE name = ?", (preset,)).fetchone()
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return ShownAgain(shown, installed is not None, shown and forced is not None and forced[0] == 1)


def connect(path, timeout=5.0):
    """Read-write connection in autocommit mode; mode=rw never creates an empty DB.

    Raises OldSchema (message: what to do) for a DB that doesn't fit these scripts.
    """
    # mode=rw even to read: mode=ro can fail on a WAL DB that nothing else has open.
    db = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=rw", uri=True, timeout=timeout, isolation_level=None
    )
    try:
        check_schema(db)
    except BaseException:
        db.close()
        raise
    return db


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--mode", choices=["light", "dark"], help="only favorites of this mode")
    ap.add_argument(
        "--min-stars",
        type=int,
        default=1,
        choices=range(1, 6),
        metavar="N",
        help="only favorites rated at least N stars (1-5; default 1)",
    )
    ap.add_argument(
        "--history", type=int, nargs="?", const=30, metavar="N", help="last N rating changes"
    )
    ap.add_argument("--hidden", action="store_true", help="presets marked Never show")
    ap.add_argument("--show-again", metavar="NAME", help="undo Never show for this preset")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"No database at {args.db}. Build it with {REBUILD}.")
    try:
        db = connect(args.db)
    except OldSchema as e:
        sys.exit(f"The DB doesn't fit this version: {e}.")
    try:
        if args.show_again is not None:
            result = show_again(db, args.show_again)
            note = shown_again_note(args.show_again, result)
            if not result.shown:
                sys.exit(f"{note} See --hidden.")
            print(note)
        elif args.history is not None:
            show_history(db, args.history)
        elif args.hidden:
            show_hidden(db)
        else:
            show_favorites(db, args.mode, args.min_stars)
    except sqlite3.OperationalError as e:
        if is_busy(e):
            sys.exit(f"The DB is busy ({e}). Try again in a moment.")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
