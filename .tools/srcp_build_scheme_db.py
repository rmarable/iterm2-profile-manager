#!/usr/bin/env python3
"""Build the scheme database that AutoLaunch/srcp_set_random_color_profile.py picks from.

Classifies every installed color preset as light or dark and runs a readability
gate over it. A rebuild replaces only the derived `schemes` table and the views;
`overrides`, `config`, `fonts`, `favorites`, `ratings` and `launches` are kept. An existing DB
is snapshotted to backups/ first.

Reads iTerm2's prefs without writing them, so it's safe with iTerm2 running:
    python3 .tools/srcp_build_scheme_db.py [--dryrun] [--repo DIR] [--db PATH]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import itertools
import math
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_db_backup import snapshot
from srcp_iterm_prefs import load_prefs

KEY = "Custom Color Presets"
DEFAULT_REPO = os.path.expanduser(
    os.path.join(os.environ.get("SRC_DIR", "~/src"), "iTerm2-Color-Schemes")
)
DEFAULT_DB = os.path.expanduser("~/.local/share/iterm2-color-schemes/schemes.db")

ANSI_KEYS = [f"Ansi {i} Color" for i in range(16)]
ACCENT_KEYS = [f"Ansi {i} Color" for i in range(1, 7)]
HASH_KEYS = [*ANSI_KEYS, "Foreground Color", "Background Color"]
COMPONENTS = ("Red Component", "Green Component", "Blue Component")

# Readability gate.
MIN_TEXT_CONTRAST = 4.5  # fg/bg, WCAG AA
MIN_ACCENT_CONTRAST = 2.0  # each of ANSI 1-6 against the bg...
MIN_READABLE_ACCENTS = 4  # ...for at least this many of the 6
MIN_ACCENT_DIST = 0.04  # OKLab distance below which two accents look the same
MIN_DISTINCT_ACCENTS = 5  # largest set of accents all MIN_ACCENT_DIST apart
CHROMA_MIN = 0.02  # OKLab chroma above which an accent counts as colored
MONO_ARC_DEG = 40  # colored accents within this hue arc = monochrome theme
# Background OKLab chroma at or above which a preset counts as colorful (config colorful_weight).
COLORFUL_BG_CHROMA = 0.05

# Old-rotation presets just under the gate, included on the first build.
SEED_INCLUDES = [
    "Hot Dog Stand",
    "Lavandula",
    "Solarized Dark Patched",
    "iTerm2 Solarized Light",
    "Aura",
    "Aura Dark",
]
SEED_CONFIG = {
    "favorite_share": "0.5",
    "favorite_target": "10",
    "recent_window": "20",
    "colorful_weight": "3",  # colorful-background presets picked this many times as often
    "nonascii_font": "MesloLGSNFM-Regular",  # prompt icons; '' turns it off
}
# PostScript name -> readable label, used in favorite profile names ("Kibble - Menlo").
SEED_FONT_LABELS = {
    "SFMono-Regular": "SF Mono",
    "Menlo-Regular": "Menlo",
    "Monaco": "Monaco",
    "MesloLGSNFM-Regular": "MesloLGS Nerd Font Mono",
}
# PostScript name -> font file names (any one present counts as installed).
SEED_FONTS = {
    "SFMono-Regular": ["SF-Mono-Regular.otf"],
    "Menlo-Regular": ["Menlo.ttc"],
    "Monaco": ["Monaco.ttf"],
    "MesloLGSNFM-Regular": ["MesloLGSNerdFontMono-Regular.ttf"],
}
FONT_DIRS = [
    "~/Library/Fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
]


# ---- color math ----


def rgb(c):
    return tuple(c[k] for k in COMPONENTS)


def has_colors(colors):
    return all(k in colors and all(p in colors[k] for p in COMPONENTS) for k in HASH_KEYS)


def linear(v):
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(c):
    r, g, b = (linear(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(c1, c2):
    l1, l2 = relative_luminance(c1) + 0.05, relative_luminance(c2) + 0.05
    return max(l1, l2) / min(l1, l2)


def oklab(c):
    r, g, b = (linear(v) for v in c)
    lc = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    mc = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    sc = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * lc + 0.7936177850 * mc - 0.0040720468 * sc,
        1.9779984951 * lc - 2.4285922050 * mc + 0.4505937099 * sc,
        0.0259040371 * lc + 0.7827717662 * mc - 0.8086757660 * sc,
    )


def is_monochrome(labs):
    """Fewer than 2 colored accents, or all of them within MONO_ARC_DEG of hue."""
    hues = sorted(
        math.degrees(math.atan2(b, a)) % 360 for _, a, b in labs if math.hypot(a, b) > CHROMA_MIN
    )
    if len(hues) < 2:
        return True
    gaps = [b - a for a, b in zip(hues, hues[1:])] + [hues[0] + 360 - hues[-1]]
    return 360 - max(gaps) <= MONO_ARC_DEG


def distinct_accents(labs):
    """Size of the largest set of accents that are all MIN_ACCENT_DIST apart."""
    for size in range(len(labs), 0, -1):
        for subset in itertools.combinations(labs, size):
            if all(
                math.dist(a, b) >= MIN_ACCENT_DIST for a, b in itertools.combinations(subset, 2)
            ):
                return size
    return 0


def classify(colors):
    bg, fg = rgb(colors["Background Color"]), rgb(colors["Foreground Color"])
    accents = [rgb(colors[k]) for k in ACCENT_KEYS]
    labs = [oklab(c) for c in accents]
    # The 4th-highest accent contrast decides "at least 4 of 6".
    accent_contrast = sorted(contrast_ratio(c, bg) for c in accents)[6 - MIN_READABLE_ACCENTS]
    distinct = distinct_accents(labs)
    mono = is_monochrome(labs)
    return {
        "lum_mode": "light" if relative_luminance(bg) > relative_luminance(fg) else "dark",
        "bg_chroma": round(math.hypot(*oklab(bg)[1:]), 4),
        "fg_contrast": round(contrast_ratio(fg, bg), 2),
        "accent_contrast": round(accent_contrast, 2),
        "distinct_accents": distinct,
        "monochrome": int(mono),
        "text_ok": contrast_ratio(fg, bg) >= MIN_TEXT_CONTRAST,
        "accent_ok": mono or accent_contrast >= MIN_ACCENT_CONTRAST,
        "distinct_ok": mono or distinct >= MIN_DISTINCT_ACCENTS,
    }


def color_hash(colors):
    parts = [",".join(f"{v:.4f}" for v in rgb(colors[k])) for k in HASH_KEYS]
    return hashlib.sha1("|".join(parts).encode(), usedforsecurity=False).hexdigest()


def yaml_variants(repo):
    """Preset name -> 'light'/'dark' from the repo's yaml `variant:` lines."""
    out = {}
    for path in glob.glob(os.path.join(repo, "yaml", "*.yml")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"""variant:\s*["']?([A-Za-z]*)""", line)
                if m:
                    v = m.group(1).lower()
                    if v in ("light", "dark"):
                        out[os.path.basename(path)[: -len(".yml")]] = v
                    break
    return out


def font_installed(files):
    return any(
        os.path.exists(os.path.join(os.path.expanduser(d), f)) for d in FONT_DIRS for f in files
    )


# ---- database ----

MIGRATIONS = [
    # 1: tables that survive rebuilds; seeds are added in migrate().
    """
    CREATE TABLE overrides (
        name TEXT PRIMARY KEY,
        mode TEXT CHECK (mode IN ('light', 'dark')),
        include INTEGER CHECK (include IN (0, 1))
    );
    CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE fonts (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE favorites (
        preset TEXT NOT NULL,
        font TEXT NOT NULL,
        size REAL,
        stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
        saved_at TEXT NOT NULL,
        PRIMARY KEY (preset, font)
    );
    CREATE TABLE launches (
        id INTEGER PRIMARY KEY,
        launched_at TEXT NOT NULL,
        preset TEXT NOT NULL,
        font TEXT,
        size REAL,
        mode TEXT,
        source TEXT CHECK (source IN ('favorite', 'discovery')),
        stars INTEGER,
        iterm_profile TEXT,
        session_id TEXT,
        ended_at TEXT,
        duration_s INTEGER,
        end_reason TEXT
    );
    CREATE INDEX launches_preset ON launches (preset);
    CREATE INDEX launches_session ON launches (session_id);
    CREATE INDEX launches_recent ON launches (mode, source, id);
    """,
    # 2: readable font names for favorite profile names; seeded in migrate().
    """
    ALTER TABLE fonts ADD COLUMN label TEXT;
    """,
    # 3: rating history: one row per save, removal or Never show, never wiped.
    """
    CREATE TABLE ratings (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        preset TEXT NOT NULL,
        font TEXT,
        action TEXT NOT NULL CHECK (action IN ('save', 'remove', 'never_show')),
        stars INTEGER,
        previous_stars INTEGER
    );
    CREATE INDEX ratings_preset ON ratings (preset, font, id);
    """,
    # 4: one row per event, so restoring an export twice (INSERT OR IGNORE) adds nothing.
    """
    DELETE FROM ratings WHERE id NOT IN (
        SELECT min(id) FROM ratings GROUP BY at, preset, COALESCE(font, ''), action
    );
    CREATE UNIQUE INDEX ratings_event ON ratings (at, preset, COALESCE(font, ''), action);
    """,
    # 5: missing = 1: disabled by the build only because its file wasn't found, so the build
    # enables it again once it is; a font you disable yourself has 0 and stays disabled.
    # Backfilled in migrate().
    """
    ALTER TABLE fonts ADD COLUMN missing INTEGER NOT NULL DEFAULT 0;
    """,
]

# Ours, dropped and recreated on every build; views a user adds are left alone.
VIEW_NAMES = ["launch_stats", "favorite_pool", "discovery", "effective"]
VIEWS = """
CREATE VIEW effective AS
    SELECT s.name, COALESCE(o.mode, s.mode) AS mode, o.include, s.passes_gate, s.duplicate_of,
           s.bg_chroma
    FROM schemes s LEFT JOIN overrides o ON o.name = s.name;

CREATE VIEW discovery AS
    SELECT name, mode, bg_chroma >= {colorful} AS colorful FROM effective
    WHERE duplicate_of IS NULL
      AND (include = 1 OR (include IS NULL AND passes_gate = 1))
      AND name NOT IN (SELECT preset FROM favorites);

CREATE VIEW favorite_pool AS
    SELECT f.preset, f.font, f.size, f.stars, e.mode
    FROM favorites f JOIN effective e ON e.name = f.preset
    WHERE e.include IS NOT 0;

CREATE VIEW launch_stats AS
    SELECT l.preset,
           count(*) AS launches,
           min(l.launched_at) AS first_launched,
           max(l.launched_at) AS last_launched,
           sum(l.source = 'favorite') AS favorite_launches,
           sum(l.source = 'discovery') AS discovery_launches,
           sum(l.end_reason = 'changed') AS changed_launches,
           sum(l.duration_s) AS total_duration_s,
           -- a look re-rolled away from (Change to another profile) isn't a session's length
           CAST(avg(CASE WHEN l.end_reason IS NOT 'changed' THEN l.duration_s END) AS INTEGER)
               AS avg_duration_s,
           (SELECT max(stars) FROM favorites f WHERE f.preset = l.preset) AS stars
    FROM launches l GROUP BY l.preset;
"""

SCHEMES_TABLE = """
CREATE TABLE schemes (
    name TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    mode_source TEXT NOT NULL,
    fg_contrast REAL,
    accent_contrast REAL,
    distinct_accents INTEGER,
    monochrome INTEGER,
    passes_gate INTEGER NOT NULL,
    in_repo INTEGER NOT NULL,
    color_hash TEXT,
    duplicate_of TEXT,
    bg_chroma REAL
)
"""


def execute_all(db, sql):
    # Not executescript(): it commits first, ending the build's transaction.
    stmt = ""
    for line in sql.splitlines(keepends=True):
        stmt += line
        if sqlite3.complete_statement(stmt):
            db.execute(stmt)
            stmt = ""
    if stmt.strip():
        db.execute(stmt)


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], version + 1):
        execute_all(db, sql)
        if i == 1:  # once only: a user who deletes a seeded override keeps it deleted
            db.executemany(
                "INSERT OR IGNORE INTO overrides (name, include) VALUES (?, 1)",
                [(n,) for n in SEED_INCLUDES],
            )
        if i == 5:  # disabled seeded fonts still not installed were most likely disabled for that
            db.executemany(
                "UPDATE fonts SET missing = 1 WHERE name = ? AND enabled = 0",
                [(n,) for n, files in SEED_FONTS.items() if not font_installed(files)],
            )
        db.execute(f"PRAGMA user_version = {i}")
    seed_every_build(db)


def seed_every_build(db):
    """Seeds that are safe to re-apply, so ones added later reach existing DBs."""
    db.executemany("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", SEED_CONFIG.items())
    for name, files in SEED_FONTS.items():
        installed = font_installed(files)
        db.execute(
            "INSERT OR IGNORE INTO fonts (name, enabled, missing) VALUES (?, ?, ?)",
            (name, int(installed), int(not installed)),
        )
        if installed:  # installed since the build disabled it
            db.execute(
                "UPDATE fonts SET enabled = 1, missing = 0 WHERE name = ? AND missing = 1", (name,)
            )
        else:  # uninstalled since, or enabled by a curation export from another Mac
            db.execute(
                "UPDATE fonts SET enabled = 0, missing = 1 WHERE name = ? AND enabled = 1", (name,)
            )
    db.executemany(
        "UPDATE fonts SET label = ? WHERE name = ? AND label IS NULL",
        [(label, n) for n, label in SEED_FONT_LABELS.items()],
    )


def write_db(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
        try:
            # Views first, so a migration that alters a table doesn't trip over them.
            for view in VIEW_NAMES:
                db.execute(f'DROP VIEW IF EXISTS "{view}"')
            migrate(db)
            db.execute("DROP TABLE IF EXISTS schemes")
            db.execute(SCHEMES_TABLE)
            db.executemany("INSERT INTO schemes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            execute_all(db, VIEWS.format(colorful=COLORFUL_BG_CHROMA))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def build_rows(presets, repo):
    """Return (rows, report) for every preset with a full set of colors."""
    repo_names = {
        os.path.basename(p)[: -len(".itermcolors")]
        for p in glob.glob(os.path.join(repo, "schemes", "*.itermcolors"))
    }
    if not repo_names:
        print(f"[warn] no schemes in {repo}: yaml labels and repo duplicates not used")
    variants = yaml_variants(repo)

    info, skipped, repo_hashes = {}, [], {}
    for name in sorted(presets):
        if not has_colors(presets[name]):
            skipped.append(name)
            continue
        info[name] = classify(presets[name]), color_hash(presets[name])
        if name in repo_names:
            repo_hashes.setdefault(info[name][1], name)

    rows, disagree = [], []
    fails = {"text": 0, "accent": 0, "distinct": 0}
    for name, (c, h) in info.items():
        mode, source = c["lum_mode"], "luminance"
        if name in variants:
            mode, source = variants[name], "yaml"
            if mode != c["lum_mode"]:
                disagree.append(name)
        in_repo = name in repo_names
        passes = c["text_ok"] and c["accent_ok"] and c["distinct_ok"]
        for check in fails:
            fails[check] += not c[f"{check}_ok"]
        rows.append(
            (
                name,
                mode,
                source,
                c["fg_contrast"],
                c["accent_contrast"],
                c["distinct_accents"],
                c["monochrome"],
                int(passes),
                int(in_repo),
                h,
                # An old spelling of a repo scheme, or the repo's own identical twin
                # (e.g. Zenbones = Zenbones Light): only the first repo name is picked.
                twin if (twin := repo_hashes.get(h)) != name else None,
                c["bg_chroma"],
            )
        )
    return rows, {"disagree": disagree, "fails": fails, "skipped": skipped}


def print_report(rows, report):
    counts = {m: [0, 0] for m in ("light", "dark")}
    for r in rows:
        counts[r[1]][0] += 1
        counts[r[1]][1] += r[7]
    print(
        f"{len(rows)} schemes: {counts['light'][0]} light / {counts['dark'][0]} dark "
        f"({sum(r[2] == 'yaml' for r in rows)} yaml-labeled, "
        f"{len(report['disagree'])} disagree with luminance)"
    )
    for name in report["disagree"]:
        print(f"  yaml/luminance disagree: {name}")
    print(
        f"gate pass: {counts['light'][1]} light / {counts['dark'][1]} dark "
        f"({sum(r[6] for r in rows)} monochrome)"
    )
    f = report["fails"]
    print(
        f"gate fails: text contrast {f['text']}, faint accents {f['accent']}, "
        f"repeated accents {f['distinct']}"
    )
    print(f"duplicates (old spellings, repo twins): {sum(r[10] is not None for r in rows)}")
    for name in report["skipped"]:
        print(f"[warn] skipped {name!r}: missing ANSI/fg/bg colors")
    for font, files in SEED_FONTS.items():
        if not font_installed(files):
            print(f"[warn] font not installed: {font} (disabled until it is and you rebuild)")


def warn_included_duplicates(path):
    db = sqlite3.connect(path)
    try:
        for name, twin in db.execute(
            "SELECT s.name, s.duplicate_of FROM schemes s JOIN overrides o ON o.name = s.name "
            "WHERE o.include = 1 AND s.duplicate_of IS NOT NULL"
        ):
            print(f"[warn] overrides includes {name!r}, a duplicate of {twin!r}: include that")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"iTerm2-Color-Schemes checkout (default {DEFAULT_REPO})",
    )
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    ap.add_argument(
        "--dryrun",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print the counts without writing anything",
    )
    args = ap.parse_args()
    args.db = os.path.abspath(os.path.expanduser(args.db))  # a bare "t.db" has no dirname

    if not os.path.exists(args.db) and any(os.path.exists(args.db + s) for s in ("-wal", "-shm")):
        sys.exit(
            f"{args.db} is missing but its -wal/-shm files are still there, so a build would "
            f"write into them. Delete {args.db}-wal and {args.db}-shm, or restore a backup."
        )

    if os.path.exists(args.db):
        db = sqlite3.connect(args.db)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        finally:
            db.close()
        if version > len(MIGRATIONS):  # a rebuild here would recreate schemes in an older shape
            sys.exit(
                f"{args.db} is schema {version}, newer than this script ({len(MIGRATIONS)}). "
                "Update this checkout (git pull) and run it again."
            )

    # The DB checks above come first: they need no prefs and fail fast.
    presets = load_prefs().get(KEY, {})
    if not presets:
        msg = f"No {KEY!r} in iTerm2's prefs. Import presets first (.tools/srcp_import_presets.py)."
        if args.dry_run:  # e.g. setup.sh --dryrun on a fresh Mac, before any import
            print(f"  (skipped: {msg})")
            return
        sys.exit(msg)

    rows, report = build_rows(presets, args.repo)
    print_report(rows, report)
    if args.dry_run:
        print("(dry run: nothing written)")
        return
    backup = snapshot(args.db)
    if backup:
        print(f"Backup: {backup}")
    try:
        write_db(args.db, rows)
    except sqlite3.OperationalError as e:
        if "locked" not in str(e):
            raise
        sys.exit(
            f"{args.db} is locked (another sqlite3 session or a long write?). "
            "Nothing was changed; run this again."
        )
    warn_included_duplicates(args.db)
    print(f"Wrote {args.db}")


if __name__ == "__main__":
    main()
