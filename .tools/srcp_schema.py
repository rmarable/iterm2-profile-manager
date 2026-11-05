"""What the scheme DB's writers and readers must agree on, kept in one place.

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


def colors_hash(rgb_by_key):
    """color_hash from {key: (r, g, b)} with raw components 0-1, or None if a key is missing.

    Raw as stored, never converted, so the build (prefs dicts) and the main script (API
    colors) always agree.
    """
    try:
        parts = [",".join(f"{float32(v):.4f}" for v in rgb_by_key[k]) for k in HASH_KEYS]
    except KeyError:
        return None
    return hashlib.sha1("|".join(parts).encode(), usedforsecurity=False).hexdigest()


def is_busy(error):
    """The DB was locked past the timeout (e.g. the main script was writing)."""
    return "locked" in str(error) or "busy" in str(error)
