"""What the scheme DB's writers and readers must agree on, kept in one place: the DB's path,
the hash and the fix_colors rule, seed values, the oldest schema the readers accept
(MIN_SCHEMA), and the Display P3 to sRGB conversion the color math uses.

Imported by the AutoLaunch script (under iTerm2's Python 3.12) and by the build,
srcp_favorites.py, srcp_odds.py and srcp_export_curation.py in .tools/ (under
/usr/bin/python3 3.9), so this stays 3.9-compatible and stdlib-only. setup.sh installs it
into the Scripts folder's .tools/.
"""

from __future__ import annotations

import hashlib
import os
import struct

# Where the scheme DB lives (its snapshots go to backups/ beside it).
DATA_DIR = os.path.expanduser("~/.local/share/iterm2-color-schemes")
DB_PATH = os.path.join(DATA_DIR, "schemes.db")

# What color_hash covers: a preset whose hash changed since the build gets no adjustments.
HASH_KEYS = [*(f"Ansi {i} Color" for i in range(16)), "Foreground Color", "Background Color"]
COMPONENTS = ("Red Component", "Green Component", "Blue Component")

# Whether color adjustments are on: config fix_colors, anything but '0' / '0.0' (missing = on).
# The build's views embed it, so changing the setting needs no rebuild.
FIX_ON_SQL = (
    "(COALESCE(trim((SELECT value FROM config WHERE key = 'fix_colors')), '1') NOT IN ('0', '0.0'))"
)
# Undo Never show (the main script's save, and srcp_favorites.py --show-again). A mode override
# survives. A preset that fails the gate is forced in (include 1), since it was wanted, unless
# it's a byte-identical twin or adjustments are on (it's picked anyway, and turning them off
# then puts things back as they were).
UNHIDE = (
    "UPDATE overrides SET include = CASE WHEN "  # noqa: S608  (built from constants only)
    "(SELECT passes_gate FROM schemes WHERE name = :name) = 0 AND "
    "(SELECT duplicate_of FROM schemes WHERE name = :name) IS NULL AND NOT "
    + FIX_ON_SQL
    + " THEN 1 END WHERE name = :name AND include = 0"
)

# config rows the first build writes; the main script falls back to the same values.
SEED_CONFIG = {
    "favorite_share": "0.5",
    "favorite_target": "10",
    "recent_window": "20",
    "colorful_weight": "3",  # colorful-background presets picked this many times as often
    "nonascii_font": "MesloLGSNFM-Regular",  # prompt icons; '' turns it off
    "fix_colors": "1",  # adjust hard-to-see colors; 0 turns it off
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


def float32(v):
    """v as iTerm2's API sends it (32-bit; prefs hold 64-bit), so both sides hash alike."""
    return struct.unpack("f", struct.pack("f", v))[0]


def colors_hash(rgb_by_key, p3_keys=()):
    """color_hash from {key: (r, g, b)} with raw components 0-1, or None if a key is missing.

    Raw as stored, never converted, so the build (prefs dicts) and the main script (API
    colors) always agree. p3_keys: the keys whose color space is Display P3, which the
    hash marks, since the same numbers look different there.
    """
    try:
        parts = [
            ",".join(f"{float32(v):.4f}" for v in rgb_by_key[k]) + (":p3" if k in p3_keys else "")
            for k in HASH_KEYS
        ]
    except KeyError:
        return None
    return hashlib.sha1("|".join(parts).encode(), usedforsecurity=False).hexdigest()


# The oldest DB schema (PRAGMA user_version) the scripts read. Two changes to the shape of
# `schemes` and the views (discovery.colorful in 0.6.0, schemes.fixes in 0.9.0) shipped without
# a MIGRATIONS step, so the version alone can't tell a 0.6 DB from a 0.9 one below 6; every
# DB at 6 or later was built by code that has all of them. Any later change to that shape
# adds a MIGRATIONS step and raises this, so readers never need to probe for columns.
MIN_SCHEMA = 7
REBUILD = "python3 .tools/srcp_build_scheme_db.py"


class OldSchema(Exception):
    """The DB was built by an older version; rebuilding it (the message says how) fixes it."""


def schema_problem(db):
    """Why db is too old for these scripts, or None if it's fine."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version >= MIN_SCHEMA:
        return None
    return (
        f"the scheme DB is schema {version}, older than this version reads ({MIN_SCHEMA}); "
        f"rebuild it: {REBUILD} (safe while iTerm2 runs)"
    )


def check_schema(db):
    """Raise OldSchema if db is too old for these scripts."""
    problem = schema_problem(db)
    if problem:
        raise OldSchema(problem)


# Display P3 colors, converted to sRGB so they're judged as they look: linear P3 -> XYZ (D65)
# -> linear sRGB, with the exact fractions and the sign-keeping sRGB transfer functions of CSS
# Color 4's sample code (drafts.csswg.org/css-color-4/conversions.js, checked 2026-11-05).
P3_TO_XYZ = (
    (608311 / 1250200, 189793 / 714400, 198249 / 1000160),
    (35783 / 156275, 247089 / 357200, 198249 / 2500400),
    (0 / 1, 32229 / 714400, 5220557 / 5000800),
)
XYZ_TO_SRGB = (
    (12831 / 3959, -329 / 214, -1974 / 3959),
    (-851781 / 878810, 1648619 / 878810, 36519 / 878810),
    (705 / 12673, -2585 / 12673, 705 / 667),
)


def _times(m, v):
    return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))


def _product(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _inverse(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return (
        ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
        ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
        ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det),
    )


P3_TO_SRGB = _product(XYZ_TO_SRGB, P3_TO_XYZ)  # linear to linear; every row sums to 1
SRGB_TO_P3 = _inverse(P3_TO_SRGB)


def _decode(v):  # sRGB (and P3) transfer function, gamma-encoded -> linear; keeps the sign
    a = abs(v)
    return v / 12.92 if a <= 0.04045 else (1 if v >= 0 else -1) * ((a + 0.055) / 1.055) ** 2.4


def _encode(v):  # linear -> gamma-encoded; keeps the sign (out-of-gamut values are negative)
    a = abs(v)
    return 12.92 * v if a <= 0.0031308 else (1 if v >= 0 else -1) * (1.055 * a ** (1 / 2.4) - 0.055)


def p3_to_srgb(rgb):
    """A Display P3 color as sRGB, components 0-1 when in sRGB's gamut, else beyond."""
    return tuple(_encode(v) for v in _times(P3_TO_SRGB, [_decode(v) for v in rgb]))


def srgb_to_p3(rgb):
    """An sRGB color as Display P3 (always inside P3's gamut, which holds all of sRGB)."""
    return tuple(_encode(v) for v in _times(SRGB_TO_P3, [_decode(v) for v in rgb]))


def is_busy(error):
    """The DB was locked past the timeout (e.g. the main script was writing)."""
    return "locked" in str(error) or "busy" in str(error)
