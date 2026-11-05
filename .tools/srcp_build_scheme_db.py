#!/usr/bin/env python3
"""Build the scheme database that AutoLaunch/srcp_set_random_color_profile.py picks from.

Classifies every installed color preset as light or dark, runs a readability gate over
it, and works out small color adjustments that make hard-to-see colors readable (the
AutoLaunch script applies them; config fix_colors = 0 turns that off). A rebuild replaces
only the derived `schemes` table (adjustments included) and the views; `overrides`, `config`,
`fonts`, `favorites`, `ratings` and `launches` are kept. An existing DB is snapshotted to
backups/ first.

Reads iTerm2's prefs without writing them, so it's safe with iTerm2 running:
    python3 .tools/srcp_build_scheme_db.py [--dryrun] [--repo DIR] [--db PATH]
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import math
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_db_backup import snapshot
from srcp_iterm_prefs import load_prefs
from srcp_schema import (  # shared with the main script and srcp_favorites.py
    COMPONENTS,
    FIX_ON_SQL,
    FONT_DIRS,
    HASH_KEYS,
    SEED_CONFIG,
    SEED_FONTS,
    colors_hash,
    contrast_ratio,
    encode_srgb,
    from_srgb,
    in_gamut,
    is_busy,
    linear,
    relative_luminance,
    schema_problem,
    space_of,
    to_srgb,
)
from srcp_schema import (
    DB_PATH as DEFAULT_DB,
)

KEY = "Custom Color Presets"
DEFAULT_REPO = os.path.expanduser(
    os.path.join(os.environ.get("SRC_DIR", "~/src"), "iTerm2-Color-Schemes")
)

ACCENT_KEYS = [f"Ansi {i} Color" for i in range(1, 7)]

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

# Color adjustments: each of these colors is moved (OKLCH lightness, hue kept) until it reaches
# its contrast against the background. ANSI 8 (comments, shell suggestions) gets a lower bar
# so it stays dimmer than text. The background, ANSI 0/7/15, cursor, bold and selection stay.
MIN_DIM_CONTRAST = 1.5
FIX_TARGETS = {
    "Foreground Color": MIN_TEXT_CONTRAST,
    **{f"Ansi {i} Color": MIN_ACCENT_CONTRAST for i in (1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14)},
    "Ansi 8 Color": MIN_DIM_CONTRAST,
}
FIX_STEP = 0.005  # OKLCH lightness (and chroma) per step
FIX_MAX_STEPS = 200  # enough to cross the whole 0-1 lightness range
DISTINCT_STEP = 2 * FIX_STEP  # lightness per move when separating look-alike accents
DISTINCT_MAX_STEPS = 40  # times the 6 accents: the moves all of them share, in all
GAMUT_EPS = 1e-6
# A color this close below its contrast target counts as reaching it: a P3 or Generic RGB
# color stored to 6 decimals can show a hair under the sRGB value it was moved to.
CONTRAST_EPS = 1e-4
GAMUT_FLOOR_MARGIN = 0.001  # last-resort chroma floor above CHROMA_MIN: still counts as colored

# Old-rotation presets just under the gate, included on the first build.
SEED_INCLUDES = [
    "Hot Dog Stand",
    "Lavandula",
    "Solarized Dark Patched",
    "iTerm2 Solarized Light",
    "Aura",
    "Aura Dark",
]
# PostScript name -> readable label, used in favorite profile names ("Kibble - Menlo").
SEED_FONT_LABELS = {
    "SFMono-Regular": "SF Mono",
    "Menlo-Regular": "Menlo",
    "Monaco": "Monaco",
    "MesloLGSNFM-Regular": "MesloLGS Nerd Font Mono",
}


# What the build learns about each preset (color math: WCAG contrast, OKLab; the readability
# gate; a color hash to find twins; the repo's yaml variants), and whether a font is installed.


def rgb(c):
    """A color as sRGB components 0-1, for the color math: Display P3 and Generic RGB colors
    are converted (srcp_schema.space_of), and one outside sRGB's gamut is clipped per
    channel."""
    raw = tuple(c[k] for k in COMPONENTS)
    space = space_of(c)
    if space == "srgb":
        return raw
    return tuple(min(1.0, max(0.0, v)) for v in to_srgb(space, raw))


def stored(c, srgb_rgb):
    """The components to store for c moved to srgb_rgb: back in c's own color space."""
    space = space_of(c)
    if space == "srgb":
        return srgb_rgb
    # 6 decimals, not 4: rounding in the color's own space moves the sRGB color it shows as,
    # too. The clamp only catches float noise: a fix is chosen inside the color's own gamut.
    return tuple(round(min(1.0, max(0.0, v)), 6) for v in from_srgb(space, srgb_rgb))


def has_colors(colors):
    return all(k in colors and all(p in colors[k] for p in COMPONENTS) for k in HASH_KEYS)


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


def oklab_to_linear(lab):
    """Linear RGB for an OKLab color; components outside 0-1 mean out of gamut."""
    lightness, a, b = lab
    lc = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    mc = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    sc = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )


def lch_to_rgb(lightness, chroma, hue, space="srgb"):
    """Rounded sRGB (4 decimals, as color_hash), or None if out of sRGB's gamut or of the
    gamut of space, the color's own (Generic RGB doesn't hold all of sRGB)."""
    lin = oklab_to_linear((lightness, chroma * math.cos(hue), chroma * math.sin(hue)))
    if any(v < -GAMUT_EPS or v > 1 + GAMUT_EPS for v in lin):
        return None
    out = tuple(round(encode_srgb(min(1.0, max(0.0, v))), 4) for v in lin)
    return out if space == "srgb" or in_gamut(space, out) else None


def move_to_contrast(c, bg, target, floor, space="srgb"):
    """c moved in lightness (hue kept) until its contrast with bg reaches target.

    Steps toward whichever end (white or black) can reach the target, preferring the side c
    is already on (one of them always reaches 4.5, the highest target). Chroma drops only as
    far as needed to stay in gamut, never below floor. Returns the first point that reaches
    the target, else the best point found (the floor ran out of gamut first), or None if
    nothing beat c.
    """
    lightness, a, b = oklab(c)
    chroma, hue = math.hypot(a, b), math.atan2(b, a)
    up, down = contrast_ratio((1, 1, 1), bg), contrast_ratio((0, 0, 0), bg)
    lighter = relative_luminance(c) >= relative_luminance(bg)
    if (up if lighter else down) >= target:
        direction = 1 if lighter else -1
    else:
        direction = -1 if lighter else 1
    best, best_contrast = None, contrast_ratio(c, bg)
    for step in range(1, FIX_MAX_STEPS + 1):
        lt = lightness + direction * step * FIX_STEP
        if not 0.0 <= lt <= 1.0:
            break
        cc, rgb_ = chroma, lch_to_rgb(lt, chroma, hue, space)
        while rgb_ is None and cc > floor:
            cc = max(floor, cc - FIX_STEP)
            rgb_ = lch_to_rgb(lt, cc, hue, space)
        if rgb_ is None:  # even the chroma floor is out of gamut here: keep the best so far
            break
        contrast = contrast_ratio(rgb_, bg)
        if contrast >= target:
            return rgb_
        if contrast > best_contrast:
            best, best_contrast = rgb_, contrast
    return best


def adjust_one(c, bg, target, is_ansi, space="srgb"):
    """move_to_contrast with the chroma floors in order: a colored ANSI color first keeps a
    margin above CHROMA_MIN; if that misses the target, just above it (still colored, so the
    gate's monochrome check can't flip). Text may go grey. None if nothing beat c."""
    chroma = math.hypot(*oklab(c)[1:])
    if not is_ansi:  # text may go grey: one pass, no floor
        floors = [0.0]
    elif chroma <= CHROMA_MIN:  # an ANSI grey: keep its faint tint if that's enough, else lose it
        floors = [min(chroma, CHROMA_MIN + FIX_STEP), 0.0]
    else:
        floors = [CHROMA_MIN + FIX_STEP, CHROMA_MIN + GAMUT_FLOOR_MARGIN]
    best = None
    for floor in floors:
        moved = move_to_contrast(c, bg, target, min(chroma, floor), space)
        if moved is not None and (
            best is None or contrast_ratio(moved, bg) > contrast_ratio(best, bg)
        ):
            best = moved
        if best is not None and contrast_ratio(best, bg) >= target:
            break
    return best


def separate_accents(palette, bg, spaces):
    """Nudge look-alike accents (ANSI 1-6) apart until 5 are distinct; None if it can't.

    Deterministic, at most DISTINCT_MAX_STEPS * 6 moves shared by all the accents (one accent
    can take most of them): each move takes one of the closest pair DISTINCT_STEP in lightness
    away from the other, preferring a move that also raises its contrast, and never below its
    contrast target. So a readable accent can lose some contrast (down to its target) to
    tell it from its look-alike. spaces: {key: its color space}, for the gamut check.
    """
    work = dict(palette)
    bg_lightness = oklab(bg)[0]
    for _ in range(DISTINCT_MAX_STEPS * len(ACCENT_KEYS)):
        labs = {k: oklab(work[k]) for k in ACCENT_KEYS}
        if distinct_accents(list(labs.values())) >= MIN_DISTINCT_ACCENTS:
            return work
        k1, k2 = min(
            itertools.combinations(ACCENT_KEYS, 2), key=lambda p: math.dist(labs[p[0]], labs[p[1]])
        )
        options = []
        for k, other in ((k1, k2), (k2, k1)):
            lightness, a, b = labs[k]
            direction = 1 if lightness >= labs[other][0] else -1
            candidate = lch_to_rgb(
                lightness + direction * DISTINCT_STEP,
                math.hypot(a, b),
                math.atan2(b, a),
                spaces[k],
            )
            if candidate and contrast_ratio(candidate, bg) >= FIX_TARGETS[k]:
                raises = (lightness >= bg_lightness) == (direction > 0)
                options.append((not raises, ACCENT_KEYS.index(k), k, candidate))
        if not options:
            return None
        _, _, k, candidate = min(options)
        work[k] = candidate
    return None


def fix_colors(colors):
    """{key: (r, g, b, contrast before, contrast after)} for the colors to adjust; {} if none.

    A fixed point: fix_colors() of a palette with these applied returns {}.
    """
    bg = rgb(colors["Background Color"])
    palette = {k: rgb(colors[k]) for k in FIX_TARGETS}
    shown_before = dict(palette)  # converted once: rgb() of a P3 or Generic RGB color isn't free
    spaces = {k: space_of(colors[k]) for k in FIX_TARGETS}
    for key, target in FIX_TARGETS.items():
        if contrast_ratio(palette[key], bg) < target - CONTRAST_EPS:
            moved = adjust_one(palette[key], bg, target, key != "Foreground Color", spaces[key])
            if moved is not None:
                palette[key] = moved
    # A bright color (ANSI 9-14) that ended up identical to its normal one, though they
    # differed, gets a little more contrast so the two stay apart.
    for i in range(1, 7):
        normal, bright = f"Ansi {i} Color", f"Ansi {i + 8} Color"
        if palette[bright] == palette[normal] and shown_before[bright] != shown_before[normal]:
            nudged = adjust_one(
                palette[bright], bg, contrast_ratio(palette[bright], bg) + 0.1, True, spaces[bright]
            )
            if nudged is not None:
                palette[bright] = nudged
    labs = [oklab(palette[k]) for k in ACCENT_KEYS]
    if not is_monochrome(labs) and distinct_accents(labs) < MIN_DISTINCT_ACCENTS:
        palette = separate_accents(palette, bg, spaces) or palette  # best effort: keep fixes
    out = {}
    for k, v in palette.items():
        before = shown_before[k]
        if v != before:
            keep = stored(colors[k], v)
            shown = rgb({**colors[k], **dict(zip(COMPONENTS, keep))})  # as it will look
            out[k] = (
                *keep,
                round(contrast_ratio(before, bg), 2),
                round(contrast_ratio(shown, bg), 2),
            )
    return out


def fixes_json(fixes):
    """schemes.fixes: {key: [r, g, b, contrast before, contrast after]}, or None."""
    return json.dumps({k: list(v) for k, v in fixes.items()}, sort_keys=True) if fixes else None


def apply_fixes(colors, fixes):
    """A copy of colors with fixes applied (each color keeps its other keys: space, alpha)."""
    out = dict(colors)
    for key, (r, g, b, *_) in fixes.items():
        out[key] = {**colors[key], COMPONENTS[0]: r, COMPONENTS[1]: g, COMPONENTS[2]: b}
    return out


def color_hash(colors):
    """srcp_schema.colors_hash of a prefs preset dict: its raw components, as stored."""
    # The stored components, not rgb(): that converts P3 and Generic RGB ones for the math.
    raw = {k: tuple(colors[k][c] for c in COMPONENTS) for k in HASH_KEYS}
    return colors_hash(raw, {k: space_of(colors[k]) for k in HASH_KEYS})


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


# The database: migrations (one per schema version), the rebuild and its views.

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
    # 6: launches still open (close_stale's query at script start). Small now; also marks the
    # DBs this version built, which every current column and view exists in.
    """
    CREATE INDEX launches_open ON launches (launched_at)
        WHERE ended_at IS NULL AND end_reason IS NULL;
    """,
    # 7: no table changes. Display P3 colors are now converted for the color math and marked
    # in color_hash, so schemes rows from before are stale; the scripts refuse older DBs
    # (srcp_schema.MIN_SCHEMA), so this rebuild happens.
    """
    -- nothing to change
    """,
    # 8: no table changes. Untagged and "Calibrated" colors are now judged as the Generic RGB
    # iTerm2 draws them in, and marked in color_hash: stale rows again, so the same rebuild.
    """
    -- nothing to change either
    """,
]

# Ours, dropped and recreated on every build; views a user adds are left alone.
VIEW_NAMES = ["launch_stats", "favorite_pool", "discovery", "effective"]
VIEWS = """
CREATE VIEW effective AS
    SELECT s.name, COALESCE(o.mode, s.mode) AS mode, o.include, s.passes_gate, s.duplicate_of,
           s.bg_chroma, s.fix_count, s.passes_gate_fixed
    FROM schemes s LEFT JOIN overrides o ON o.name = s.name;

-- With adjustments on (config fix_colors), presets that fail the gate are picked too.
CREATE VIEW discovery AS
    SELECT name, mode, bg_chroma >= {colorful} AS colorful FROM effective
    WHERE duplicate_of IS NULL
      AND (include = 1 OR (include IS NULL AND (passes_gate = 1 OR {fix_on})))
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
    bg_chroma REAL,
    fixes TEXT,
    fix_count INTEGER NOT NULL DEFAULT 0,
    passes_gate_fixed INTEGER
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
            db.executemany("INSERT INTO schemes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            execute_all(db, VIEWS.format(colorful=COLORFUL_BG_CHROMA, fix_on=FIX_ON_SQL))
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
        info[name] = classify(presets[name]), color_hash(presets[name]), fix_colors(presets[name])
        if name in repo_names:
            repo_hashes.setdefault(info[name][1], name)

    rows, disagree = [], []
    fails = {"text": 0, "accent": 0, "distinct": 0}
    for name, (c, h, fixes) in info.items():
        mode, source = c["lum_mode"], "luminance"
        if name in variants:
            mode, source = variants[name], "yaml"
            if mode != c["lum_mode"]:
                disagree.append(name)
        in_repo = name in repo_names
        passes = c["text_ok"] and c["accent_ok"] and c["distinct_ok"]
        for check in fails:
            fails[check] += not c[f"{check}_ok"]
        fixed = classify(apply_fixes(presets[name], fixes)) if fixes else c
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
                fixes_json(fixes),
                len(fixes),
                int(fixed["text_ok"] and fixed["accent_ok"] and fixed["distinct_ok"]),
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
    adjusted = [r for r in rows if r[13]]
    still = sum(not r[14] for r in rows if r[10] is None)  # twins are never picked
    print(
        f"colors adjusted for readability: {sum(r[13] for r in rows)} in {len(adjusted)} presets; "
        f"{still} still fail the gate after adjusting (picked anyway unless config fix_colors = 0)"
    )
    for name in report["skipped"]:
        print(f"[warn] skipped {name!r}: missing ANSI/fg/bg colors")
    for font, files in SEED_FONTS.items():
        if not font_installed(files):
            print(f"[warn] font not installed: {font} (disabled until it is and you rebuild)")


def favorite_fixes(path):
    """{preset: fixes JSON} for the favorites in the DB at path, or None if it can't tell
    (no DB, or one older than the scripts read, whose fixes may not exist or mean the same)."""
    if not os.path.exists(path):
        return None
    db = sqlite3.connect(path)
    try:
        if schema_problem(db):
            return None
        return dict(
            db.execute(
                "SELECT DISTINCT f.preset, s.fixes FROM favorites f "
                "LEFT JOIN schemes s ON s.name = f.preset"
            )
        )
    finally:
        db.close()


def report_changed_favorites(before, after):
    changed = sorted(p for p in after if before is not None and before.get(p) != after[p])
    if changed:
        print(f"color adjustments changed for favorites: {', '.join(changed)}")
    print("New tabs get these adjustments; favorites' profiles update within 30 seconds.")


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


def check_existing_db(path):
    """Exit, changing nothing, if the DB at path is damaged or newer than this script."""
    version, check = 0, "ok"
    if os.path.getsize(path) == 0:  # SQLite would take it as a new, empty DB: data gone
        check = "the file is empty"
    db = sqlite3.connect(path)
    try:
        if check != "ok":
            raise sqlite3.DatabaseError(check)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        # Before the snapshot: a copy of a damaged DB would push a good snapshot out.
        check = db.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as e:
        if is_busy(e):
            raise
        check = str(e)
    finally:
        db.close()
    if check != "ok":
        detail = " ".join(check.split())[:160]  # quick_check's report can run many lines
        sys.exit(
            f"{path} is damaged ({detail}), so it was left as is, with no snapshot. Quit "
            "iTerm2, move it and any -wal/-shm files beside it out of the way, copy the "
            "newest snapshot from backups/ in its place (docs/REFERENCE.md, Restore) and "
            "run this again; if it refuses that one too, try the next older snapshot. With "
            "no snapshot, move it away and run this for a fresh "
            "database, then restore an export (srcp-curation-*.sql) if you have one."
        )
    if version > len(MIGRATIONS):  # a rebuild here would recreate schemes in an older shape
        sys.exit(
            f"{path} is schema {version}, newer than this script ({len(MIGRATIONS)}). "
            "Update this checkout (git pull) and run it again."
        )


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
        check_existing_db(args.db)

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
    before = favorite_fixes(args.db)
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
    report_changed_favorites(before, favorite_fixes(args.db) or {})


if __name__ == "__main__":
    main()
