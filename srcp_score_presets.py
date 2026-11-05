#!/usr/bin/env python3
"""Report: rank every installed color preset by saturation ("pop" score).

Not used for selection; AutoLaunch/srcp_set_random_color_profile.py picks from the
scheme DB (.tools/srcp_build_scheme_db.py). Run from iTerm2's Scripts menu.
"""

import colorsys
import os
import sys

import iterm2

# .tools/srcp_schema.py converts Display P3 and Generic RGB colors to sRGB, so they're
# scored as they look, with the build's own contrast; setup.sh installs it.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tools"))
try:
    import srcp_schema
except ImportError:
    srcp_schema = None

ANSI_KEYS = [f"Ansi {i} Color" for i in range(16)]
BG_KEY = "Background Color"
FG_KEY = "Foreground Color"


def saturation(r, g, b):
    _, _, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return s


async def score_preset(connection, name):
    if srcp_schema is None:  # main() says so
        return None
    try:
        preset = await iterm2.ColorPreset.async_get(connection, name)
    except (iterm2.GetPresetException, iterm2.ListPresetsException):  # e.g. deleted meanwhile
        return None
    if preset is None:  # typed Optional, though the module raises instead
        return None

    colors = {}
    for v in preset.values:
        space = srcp_schema.space_named(v.color_space.value)  # no space at all: Calibrated
        srgb = srcp_schema.to_srgb(space, (v.red / 255, v.green / 255, v.blue / 255))
        colors[v.key] = tuple(255 * min(1.0, max(0.0, c)) for c in srgb)  # clipped, as the build

    bg = colors.get(BG_KEY)
    fg = colors.get(FG_KEY)
    if not bg or not fg:
        return None

    ansi_colors = [colors[k] for k in ANSI_KEYS if k in colors]
    if not ansi_colors:
        return None

    bg_sat = saturation(*bg)
    avg_ansi_sat = sum(saturation(*c) for c in ansi_colors) / len(ansi_colors)
    contrast = srcp_schema.contrast_ratio(tuple(c / 255 for c in bg), tuple(c / 255 for c in fg))

    pop_score = (avg_ansi_sat * 0.7) + (bg_sat * 0.3)

    return {
        "name": name,
        "pop_score": round(pop_score, 3),
        "bg_saturation": round(bg_sat, 3),
        "ansi_saturation": round(avg_ansi_sat, 3),
        "contrast": round(contrast, 2),
    }


async def main(connection):
    if srcp_schema is None:
        print("[error] .tools/srcp_schema.py not found: run ./setup.sh again from Terminal.app")
        return
    names = await iterm2.ColorPreset.async_get_list(connection)
    print(f"[info] scoring {len(names)} installed presets...\n")

    results = []
    for name in names:
        r = await score_preset(connection, name)
        if r:
            results.append(r)

    results.sort(key=lambda r: r["pop_score"], reverse=True)

    print(f"{'RANK':<5}{'POP':<7}{'ANSI_SAT':<10}{'BG_SAT':<8}{'CONTRAST':<9}NAME")
    for i, r in enumerate(results, 1):
        print(
            f"{i:<5}{r['pop_score']:<7}{r['ansi_saturation']:<10}{r['bg_saturation']:<8}{r['contrast']:<9}{r['name']}"
        )


iterm2.run_until_complete(main)
