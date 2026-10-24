#!/usr/bin/env python3
"""Show the odds of what the next new session will get, from your scheme DB.

Exact probabilities, computed the way the AutoLaunch script's pick() chooses (a test keeps
the two in step): favorite or new preset, which favorites, colored backgrounds, fonts.
Reads the scheme DB only; safe any time:
    python3 .tools/srcp_odds.py                  # light and dark, as bar charts
    python3 .tools/srcp_odds.py --mode dark --top 20
    python3 .tools/srcp_odds.py --json           # the same numbers, for other tools
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_build_scheme_db import SEED_CONFIG

DEFAULT_DB = os.path.expanduser("~/.local/share/iterm2-color-schemes/schemes.db")
NUMERIC = ("favorite_share", "favorite_target", "recent_window", "colorful_weight")
BAR = 30


def read_config(db):
    """config as pick() reads it: numbers, with the seeded defaults for missing or bad values."""
    cfg = {k: float(SEED_CONFIG[k]) for k in NUMERIC}
    for key, value in db.execute("SELECT key, value FROM config"):
        if key in cfg:
            with contextlib.suppress(TypeError, ValueError):  # pick() keeps the default too
                cfg[key] = float(value)
    return cfg


def odds(db, mode):
    """The chances for one new session in mode, as a dict (see --json)."""
    cfg = read_config(db)
    favs = db.execute(
        "SELECT preset, font, stars FROM favorite_pool WHERE mode = ? ORDER BY preset, font",
        (mode,),
    ).fetchall()
    try:
        disc = db.execute("SELECT name, colorful FROM discovery WHERE mode = ?", (mode,)).fetchall()
    except sqlite3.OperationalError:  # DB built before colorful_weight
        disc = [(n, 0) for (n,) in db.execute("SELECT name FROM discovery WHERE mode = ?", (mode,))]

    n_presets = len({f[0] for f in favs})
    target = cfg["favorite_target"]
    share = cfg["favorite_share"] * (min(1.0, n_presets / target) if target > 0 else 1.0)
    if not favs:
        p_fav = 0.0
    elif not disc:
        p_fav = 1.0
    else:
        p_fav = min(1.0, max(0.0, share))  # random() < share
    p_new = 1.0 - p_fav if disc else 0.0

    window = max(0, int(cfg["recent_window"]))
    recent = {
        n
        for (n,) in db.execute(
            "SELECT preset FROM launches WHERE mode = ? AND source = 'discovery' "
            "ORDER BY id DESC LIMIT ?",
            (mode, window),
        )
    }
    pool = [d for d in disc if d[0] not in recent] or disc
    weight = max(0.0, cfg["colorful_weight"])
    weights = [weight if colorful else 1.0 for _, colorful in pool]
    if sum(weights) <= 0:
        weights = [1.0] * len(pool)
    total = sum(weights) or 1.0
    colorful = sum(1 for _, c in pool if c)
    plain = len(pool) - colorful

    enabled = [n for (n,) in db.execute("SELECT name FROM fonts WHERE enabled = 1 ORDER BY name")]
    try:
        labels = dict(db.execute("SELECT name, label FROM fonts WHERE label IS NOT NULL"))
    except sqlite3.OperationalError:  # DB built before fonts.label existed
        labels = {}
    disabled = {n for (n,) in db.execute("SELECT name FROM fonts WHERE enabled = 0")}
    star_total = sum(f[2] for f in favs) or 1
    favorites = []
    random_font_share = p_new  # new presets always get a random font
    font_share = {}
    for preset, font, stars in favs:
        p = p_fav * stars / star_total
        label = labels.get(font) or font.removesuffix("-Regular")
        favorites.append(
            {"preset": preset, "font": font, "label": label, "stars": stars, "chance": p}
        )
        if font in disabled:
            random_font_share += p
        else:
            font_share[font] = font_share.get(font, 0.0) + p
    for font in enabled:
        font_share[font] = font_share.get(font, 0.0) + random_font_share / len(enabled)
    if not enabled and random_font_share:
        font_share["(profile font)"] = font_share.get("(profile font)", 0.0) + random_font_share

    return {
        "mode": mode,
        "config": cfg,
        "favorite_presets": n_presets,
        "chance_favorite": p_fav,
        "chance_new": p_new,
        "chance_nothing": 1.0 - p_fav - p_new,
        "favorites": sorted(favorites, key=lambda f: (-f["chance"], f["preset"], f["font"])),
        "new": {
            "eligible": len(disc),
            "recently_shown": len(disc) - len(pool) if len(pool) < len(disc) else 0,
            "colorful": colorful,
            "plain": plain,
            "chance_colorful": p_new * weight * colorful / total if pool else 0.0,
            "chance_each_colorful": p_new * weight / total if colorful else 0.0,
            "chance_each_plain": p_new / total if plain else 0.0,
            "presets": {n: p_new * w / total for (n, _), w in zip(pool, weights)},
        },
        "fonts": dict(sorted(font_share.items(), key=lambda kv: (-kv[1], kv[0]))),
        "font_labels": {f: labels.get(f) or f.removesuffix("-Regular") for f in font_share},
    }


def bar(p):
    filled = round(max(0.0, min(1.0, p)) * BAR)
    return "█" * filled + "░" * (BAR - filled)


def pct(p):
    return f"{p * 100:5.1f}%" if p >= 0.001 or p == 0 else f"{p * 100:5.2f}%"


def rows(items, width=None):
    width = width or max((len(label) for label, _ in items), default=0)
    for label, p in items:
        print(f"  {label.ljust(width)}  {bar(p)}  {pct(p)}")


def show(o, top):
    cfg = o["config"]
    print(f"\n{o['mode'].upper()} MODE: the next new session gets")
    rows(
        [("a favorite", o["chance_favorite"]), ("a new preset", o["chance_new"])]
        + ([("nothing (no presets)", o["chance_nothing"])] if o["chance_nothing"] > 1e-9 else [])
    )
    target = int(cfg["favorite_target"])
    n = o["favorite_presets"]
    print(
        f"  ({n} favorite preset{'' if n == 1 else 's'}; favorite_share "
        f"{cfg['favorite_share']:g} is reached at {target})"
    )

    new = o["new"]
    if o["chance_new"]:
        print(
            f"\n  New presets: {new['eligible']} eligible"
            + (
                f", {new['recently_shown']} shown recently and skipped"
                if new["recently_shown"]
                else ""
            )
            + f"; {new['colorful']} with a colored background (each "
            f"{cfg['colorful_weight']:g}x), {new['plain']} plain"
        )
        rows(  # as shares of all new sessions, like every bar here
            [
                ("colored background", new["chance_colorful"]),
                ("plain background", o["chance_new"] - new["chance_colorful"]),
            ]
        )
        print(
            f"  (any one colored preset {pct(new['chance_each_colorful']).strip()}, "
            f"any one plain {pct(new['chance_each_plain']).strip()})"
        )

    if o["favorites"]:
        shown = o["favorites"][:top]
        print(f"\n  Favorites (chance per new session, top {len(shown)} of {len(o['favorites'])})")
        rows([(f"{f['stars']} ★  {f['preset']} - {f['label']}", f["chance"]) for f in shown])

    print("\n  Fonts")
    rows([(o["font_labels"].get(f, f), p) for f, p in o["fonts"].items()])


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--mode", choices=["light", "dark"], help="only this mode")
    ap.add_argument("--top", type=int, default=10, metavar="N", help="favorites to list (10)")
    ap.add_argument("--json", action="store_true", help="print the numbers as JSON")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"No database at {args.db}. Build it with .tools/srcp_build_scheme_db.py.")
    # mode=rw (it only reads): mode=ro can fail on a WAL DB that nothing else has open.
    db = sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=rw", uri=True)
    try:
        modes = [args.mode] if args.mode else ["light", "dark"]
        results = [odds(db, m) for m in modes]
    except sqlite3.OperationalError as e:  # e.g. a restored snapshot from an older schema
        sys.exit(
            f"The DB can't show this ({e}). Rebuild it: python3 .tools/srcp_build_scheme_db.py"
        )
    finally:
        db.close()
    if args.json:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return
    print("Odds for the next new iTerm2 session (sessions opened from a favorite profile keep it).")
    for o in results:
        show(o, args.top)


if __name__ == "__main__":
    main()
