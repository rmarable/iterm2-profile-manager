#!/usr/bin/env python3
"""Review your favorites, their ratings and use, plus rating history and hidden presets.

Reads the scheme DB only; safe any time:
    python3 .tools/srcp_favorites.py                     # favorites, best first
    python3 .tools/srcp_favorites.py --mode dark --min-stars 4
    python3 .tools/srcp_favorites.py --history [N]       # last N rating changes (default 30)
    python3 .tools/srcp_favorites.py --hidden            # presets marked Never show
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = os.path.expanduser("~/.local/share/iterm2-color-schemes/schemes.db")

FAVORITES = """
SELECT f.stars, f.preset, COALESCE(fo.label, f.font) AS font, e.mode,
       count(l.id) AS launches, max(l.launched_at) AS last_used,
       CAST(avg(l.duration_s) AS INTEGER) AS avg_open, f.saved_at
FROM favorites f
LEFT JOIN effective e ON e.name = f.preset
LEFT JOIN fonts fo ON fo.name = f.font
LEFT JOIN launches l ON l.preset = f.preset AND l.font = f.font
WHERE f.stars >= ? AND (? IS NULL OR e.mode = ?)
GROUP BY f.preset, f.font
ORDER BY f.stars DESC, f.preset, f.font
"""

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


def has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()


def show_favorites(db, mode, min_stars, cli_hints=True):
    rows = db.execute(FAVORITES, (min_stars, mode, mode)).fetchall()
    if not rows:
        print("No favorites match." if mode or min_stars > 1 else "No favorites yet.")
        return
    table(
        ["Stars", "Preset", "Font", "Mode", "Launches", "Last used", "Avg open", "Saved"],
        [
            (stars(st), p, f, m or "?", n, local(last), duration(avg), local(saved, "%Y-%m-%d"))
            for st, p, f, m, n, last, avg, saved in rows
        ],
    )
    hidden = db.execute("SELECT count(*) FROM overrides WHERE include = 0").fetchone()[0]
    noun = "favorite" if len(rows) == 1 else "favorites"
    hints = " (--hidden). Rating history: --history." if cli_hints else "."
    print(f"\n{len(rows)} {noun}. {hidden} hidden{hints}")


def show_history(db, limit):
    if not has_table(db, "ratings"):
        sys.exit("No rating history in this DB yet: rebuild it (.tools/srcp_build_scheme_db.py).")
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


def show_hidden(db):
    query = HIDDEN
    if not has_table(db, "ratings"):  # DB from before rating history: no "hidden" times
        query = HIDDEN.replace(
            "(SELECT max(at) FROM ratings r WHERE r.preset = o.name AND r.action = 'never_show')",
            "NULL",
        )
    rows = db.execute(query).fetchall()
    if not rows:
        print("No hidden presets.")
        return
    table(["Preset", "Mode", "Hidden"], [(n, m, local(at)) for n, m, at in rows])
    print("\nUnhide: delete from overrides where name = '<preset>';")


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--mode", choices=["light", "dark"], help="only favorites of this mode")
    ap.add_argument("--min-stars", type=int, default=1, choices=range(1, 6), metavar="N")
    ap.add_argument(
        "--history", type=int, nargs="?", const=30, metavar="N", help="last N rating changes"
    )
    ap.add_argument("--hidden", action="store_true", help="presets marked Never show")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"No database at {args.db}. Build it with .tools/srcp_build_scheme_db.py.")
    # mode=rw (it only reads): mode=ro can fail on a WAL DB that nothing else has open.
    db = sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=rw", uri=True)
    try:
        if args.history is not None:
            show_history(db, args.history)
        elif args.hidden:
            show_hidden(db)
        else:
            show_favorites(db, args.mode, args.min_stars)
    except sqlite3.OperationalError as e:  # e.g. a restored snapshot from an older schema
        sys.exit(
            f"The DB can't show this ({e}). Rebuild it: python3 .tools/srcp_build_scheme_db.py"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
