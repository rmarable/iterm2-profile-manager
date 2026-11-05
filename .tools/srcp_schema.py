"""What the scheme DB's writers and readers must agree on, kept in one place: the DB's path,
the hash and the fix_colors rule, seed values, the oldest schema the readers accept
(MIN_SCHEMA), the color space conversions to sRGB the color math uses (Display P3,
Generic RGB), and WCAG contrast.

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


HASH_TAGS = {"srgb": "", "p3": ":p3", "generic": ":cal"}


def colors_hash(rgb_by_key, spaces=None):
    """color_hash from {key: (r, g, b)} with raw components 0-1, or None if a key is missing.

    Raw as stored, never converted, so the build (prefs dicts) and the main script (API
    colors) always agree. spaces: {key: "srgb" | "p3" | "generic"} (space_of; missing:
    sRGB); the hash marks the others, since the same numbers look different there.
    """
    # A set: the P3 keys, as a main script before v0.12.1 passes them.
    named: dict[str, str] = (
        spaces if isinstance(spaces, dict) else dict.fromkeys(spaces or (), "p3")
    )
    tags = [HASH_TAGS[named.get(k, "srgb")] for k in HASH_KEYS]  # a bad name raises
    try:
        parts = [
            ",".join(f"{float32(v):.4f}" for v in rgb_by_key[k]) + tag
            for k, tag in zip(HASH_KEYS, tags)
        ]
    except KeyError:  # a color the preset doesn't have
        return None
    return hashlib.sha1("|".join(parts).encode(), usedforsecurity=False).hexdigest()


# The oldest DB schema (PRAGMA user_version) the scripts read. Two changes to the shape of
# `schemes` and the views (discovery.colorful in 0.6.0, schemes.fixes in 0.9.0) shipped without
# a MIGRATIONS step, so the version alone can't tell a 0.6 DB from a 0.9 one below 6; every
# DB at 6 or later was built by code that has all of them. Any later change to that shape
# adds a MIGRATIONS step and raises this, so readers never need to probe for columns.
MIN_SCHEMA = 8
REBUILD = "python3 .tools/srcp_build_scheme_db.py"


class OldSchema(Exception):
    """The DB doesn't match these scripts' version; the message says what to do."""


def schema_problem(db):
    """Why db doesn't fit these scripts, or None if it's fine. Exactly MIN_SCHEMA (always
    the number of migrations): an older DB needs a rebuild; a newer one was built by newer
    code than this, usually a main script still running from before an update."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == MIN_SCHEMA:
        return None
    if version > MIN_SCHEMA:
        return (
            f"the scheme DB is schema {version}, newer than this version reads "
            f"({MIN_SCHEMA}): it was built by newer scripts. Restart the main script (Scripts "
            "menu) or relaunch iTerm2; a tool run from an older copy needs that copy updated"
        )
    return (
        f"the scheme DB is schema {version}, older than this version reads ({MIN_SCHEMA}); "
        f"rebuild it: {REBUILD} (safe while iTerm2 runs), then restart the main script if "
        "it was updated too"
    )


def check_schema(db):
    """Raise OldSchema if db doesn't fit these scripts."""
    problem = schema_problem(db)
    if problem:
        raise OldSchema(problem)


# iTerm2 draws "sRGB" and "P3" colors as such, anything else (no key too) as calibrated,
# Apple's Generic RGB (NSDictionary+iTerm.m colorValueForKey:, master, checked 2026-11-05);
# its API reports those as Calibrated. Each is converted to sRGB for the color math.


def space_named(name):
    """ "srgb", "p3" or "generic" for a "Color Space" value (an API ColorSpace's .value too)."""
    return "p3" if name == "P3" else "srgb" if name == "sRGB" else "generic"


def space_of(color):
    """How iTerm2 draws this prefs color dict (space_named of its "Color Space")."""
    return space_named(color.get("Color Space"))


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


def _columns(r, g, b):
    """A matrix whose columns are the r, g, b colorants (XYZ)."""
    return tuple((r[i], g[i], b[i]) for i in range(3))


def _decode(v):  # sRGB (and P3) transfer function, gamma-encoded -> linear; keeps the sign
    a = abs(v)
    return v / 12.92 if a <= 0.04045 else (1 if v >= 0 else -1) * ((a + 0.055) / 1.055) ** 2.4


def _encode(v):  # linear -> gamma-encoded; keeps the sign (out-of-gamut values are negative)
    a = abs(v)
    return 12.92 * v if a <= 0.0031308 else (1 if v >= 0 else -1) * (1.055 * a ** (1 / 2.4) - 0.055)


encode_srgb = _encode  # for the build, which moves colors in linear sRGB (decoding: linear())


# Display P3: linear P3 -> XYZ (D65) -> linear sRGB, with the exact fractions and the
# transfer functions of CSS Color 4's sample code (drafts.csswg.org/css-color-4/conversions.js,
# checked 2026-11-05).
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
P3_TO_SRGB = _product(XYZ_TO_SRGB, P3_TO_XYZ)  # linear to linear; every row sums to 1
SRGB_TO_P3 = _inverse(P3_TO_SRGB)

# Generic RGB as NSColor converts it to sRGB: the D50 colorants in /System/Library/ColorSync/
# Profiles/{Generic RGB,sRGB} Profile.icc (s15Fixed16 / 65536), gamma 461/256, linear (v / 16)
# below 1/32. Within 4e-4 of NSColor (237 samples, 2026-11-05); a 0.3% step at the knee.
GENERIC_D50 = _columns(
    (29773 / 65536, 15854 / 65536, 976 / 65536),
    (23157 / 65536, 44147 / 65536, 5940 / 65536),
    (10266 / 65536, 5535 / 65536, 47158 / 65536),
)
SRGB_D50 = _columns(
    (28578 / 65536, 14581 / 65536, 912 / 65536),
    (25241 / 65536, 46981 / 65536, 6362 / 65536),
    (9376 / 65536, 3972 / 65536, 46799 / 65536),
)
GENERIC_TO_SRGB = _product(_inverse(SRGB_D50), GENERIC_D50)  # linear to linear
SRGB_TO_GENERIC = _inverse(GENERIC_TO_SRGB)
GENERIC_GAMMA = 461 / 256


def _generic_decode(v):
    a = abs(v)
    return v / 16 if a < 1 / 32 else (1 if v >= 0 else -1) * a**GENERIC_GAMMA


def _generic_encode(v):
    a = abs(v)
    return 16 * v if a < 1 / 512 else (1 if v >= 0 else -1) * a ** (1 / GENERIC_GAMMA)


# space -> (linear to linear sRGB, linear sRGB to linear, decode, encode). No "srgb" entry:
# it's the identity, passed through untouched (a round trip would change its floats).
SPACES = {
    "p3": (P3_TO_SRGB, SRGB_TO_P3, _decode, _encode),
    "generic": (GENERIC_TO_SRGB, SRGB_TO_GENERIC, _generic_decode, _generic_encode),
}


def to_srgb(space, rgb):
    """A color in space as sRGB, components 0-1 when in sRGB's gamut, else beyond."""
    if space == "srgb":
        return tuple(rgb)
    matrix, _, decode, _ = SPACES[space]
    return tuple(_encode(v) for v in _times(matrix, [decode(v) for v in rgb]))


def from_srgb(space, rgb):
    """An sRGB color in space (components outside 0-1: not in that space's gamut)."""
    if space == "srgb":
        return tuple(rgb)
    _, matrix, _, encode = SPACES[space]
    return tuple(encode(v) for v in _times(matrix, [_decode(v) for v in rgb]))


def in_gamut(space, rgb, eps=1e-4):
    """An sRGB color can be stored in space (P3 holds all of sRGB; Generic RGB doesn't). eps
    allows for the 4-decimal sRGB a fix is chosen in; stored() clamps that much away."""
    return all(-eps <= v <= 1 + eps for v in from_srgb(space, rgb))


# WCAG 2 contrast, shared by the build and srcp_score_presets.py, on sRGB components 0-1.


def linear(v):
    # Clamped: a hand-edited preset with a component below 0 or above 1 would otherwise give
    # a complex number further on (oklab's cube root). Hashes use the raw values.
    return _decode(min(1.0, max(0.0, v)))


def relative_luminance(c):
    r, g, b = (linear(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(c1, c2):
    l1, l2 = relative_luminance(c1) + 0.05, relative_luminance(c2) + 0.05
    return max(l1, l2) / min(l1, l2)


def is_busy(error):
    """The DB was locked past the timeout (e.g. the main script was writing)."""
    return "locked" in str(error) or "busy" in str(error)
