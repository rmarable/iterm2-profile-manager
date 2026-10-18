#!/usr/bin/env python3
"""Report: rank every installed color preset by saturation ("pop" score).

Not used for selection; AutoLaunch/srcp_set_random_color_profile.py picks from the
scheme DB (.tools/srcp_build_scheme_db.py). Run from iTerm2's Scripts menu.
"""

import colorsys

import iterm2

ANSI_KEYS = [f"Ansi {i} Color" for i in range(16)]
BG_KEY = "Background Color"
FG_KEY = "Foreground Color"


def saturation(r, g, b):
    _, _, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return s


def relative_luminance(r, g, b):
    def chan(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast_ratio(rgb1, rgb2):
    l1 = relative_luminance(*rgb1) + 0.05
    l2 = relative_luminance(*rgb2) + 0.05
    return max(l1, l2) / min(l1, l2)


async def score_preset(connection, name):
    preset = await iterm2.ColorPreset.async_get(connection, name)
    if not preset:
        return None

    colors = {}
    for v in preset.values:
        colors[v.key] = (v.red, v.green, v.blue)

    bg = colors.get(BG_KEY)
    fg = colors.get(FG_KEY)
    if not bg or not fg:
        return None

    ansi_colors = [colors[k] for k in ANSI_KEYS if k in colors]
    if not ansi_colors:
        return None

    bg_sat = saturation(*bg)
    avg_ansi_sat = sum(saturation(*c) for c in ansi_colors) / len(ansi_colors)
    contrast = contrast_ratio(bg, fg)

    pop_score = (avg_ansi_sat * 0.7) + (bg_sat * 0.3)

    return {
        "name": name,
        "pop_score": round(pop_score, 3),
        "bg_saturation": round(bg_sat, 3),
        "ansi_saturation": round(avg_ansi_sat, 3),
        "contrast": round(contrast, 2),
    }


async def main(connection):
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
