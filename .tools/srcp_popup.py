"""Show srcp_popup.js's native alert from a menu script; shared by srcp_ratings.py and
srcp_never_show.py (a menu script can't import another: importing one runs it).
"""

from __future__ import annotations

import asyncio
import os

POPUP_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "srcp_popup.js")


def popup_args(
    title: str, text: str, buttons: list[str], choices: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """(osascript arguments, the choices actually offered)."""
    args = ["osascript", "-l", "JavaScript", POPUP_JS]
    offered: list[str] = []
    if choices is not None:
        # One argument, a name per line, and "index<TAB>chosen" back: a name with either
        # character in it can't be passed or read back, and srcp_popup.js drops empty lines,
        # so those are left out here too and the two lists' indexes stay the same.
        for name in choices:
            if not name or "\n" in name or "\t" in name:
                print(f"[warn] popup: {name!r} can't be listed (empty, newline or tab)", flush=True)
            else:
                offered.append(name)
        args += ["--choose", "\n".join(offered)]
    return [*args, title, text, *buttons], offered


async def popup(
    title: str, text: str, buttons: list[str], choices: list[str] | None = None
) -> tuple[str, str | None]:
    """Show the alert; return (button pressed, chosen name or None).

    The first button is returned if the alert fails, so it should be the do-nothing one.
    """
    args, offered = popup_args(title, text, buttons, choices)
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    result = parse_result(out.decode(), buttons, offered)
    if result is None:
        print(f"[warn] popup failed: {err.decode().strip() or out.decode().strip()}", flush=True)
        return buttons[0], None
    return result


def parse_result(out: str, buttons: list[str], offered: list[str]) -> tuple[str, str | None] | None:
    """srcp_popup.js's "index" or "index<TAB>chosen" as (button, name); None if unreadable.

    Out-of-range indexes (-1: nothing selected) give no name, never wrap to the last one.
    """
    button, _, chosen = out.strip().partition("\t")
    try:
        b = int(button)
    except ValueError:
        return None
    if not 0 <= b < len(buttons):
        return None
    try:
        c = int(chosen)
    except ValueError:
        return buttons[b], None
    return buttons[b], offered[c] if 0 <= c < len(offered) else None
