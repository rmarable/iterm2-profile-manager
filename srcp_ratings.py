#!/usr/bin/env python3
"""Scripts menu: review favorites, rating history or hidden presets in a popup.

Shows the same tables as .tools/srcp_favorites.py in a native macOS alert with a
fixed-width font (.tools/srcp_popup.js), scrollable and selectable. The buttons
switch views in place. Reads the scheme DB only.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import os
import sqlite3
import sys
from pathlib import Path

import iterm2

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, ".tools")
POPUP = os.path.join(TOOLS, "srcp_popup.js")
sys.path.insert(0, TOOLS)
# .tools/srcp_favorites.py supplies the report's own tables, so both views agree. It's
# imported where used (pyright resolves it via extraPaths, so lint.sh checks the calls),
# after main() has checked it's installed.
HAVE_REPORT = importlib.util.find_spec("srcp_favorites") is not None

HISTORY_ROWS = 50
DONE = "Done"


def render(db: sqlite3.Connection, view: str) -> str:
    """The report's table for view, as text."""
    import srcp_favorites as report  # noqa: PLC0415  (main() checks it is installed first)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            if view == "Favorites":
                report.show_favorites(db, None, 1, cli_hints=False)  # the window has buttons
            elif view == "History":
                report.show_history(db, HISTORY_ROWS)
            else:
                report.show_hidden(db)
        except SystemExit as e:  # the report exits with a message when there's no data
            print(e)
        except sqlite3.OperationalError as e:  # e.g. a restored snapshot from an older schema
            print(f"The DB can't show this view ({e}).")
            print("Rebuild it: python3 .tools/srcp_build_scheme_db.py")
    return out.getvalue().rstrip()


def view_text(view: str) -> str:
    import srcp_favorites as report  # noqa: PLC0415  (main() checks it is installed first)

    if not os.path.exists(report.DEFAULT_DB):
        return f"No database at {report.DEFAULT_DB}.\nBuild it with .tools/srcp_build_scheme_db.py."
    # mode=rw (it only reads): mode=ro can fail on a WAL DB that nothing else has open.
    db = sqlite3.connect(Path(report.DEFAULT_DB).resolve().as_uri() + "?mode=rw", uri=True)
    try:
        return render(db, view)
    finally:
        db.close()


async def popup(title: str, text: str, buttons: list[str]) -> str:
    """Show the native alert; return the button pressed (DONE if it fails)."""
    proc = await asyncio.create_subprocess_exec(
        "osascript",
        "-l",
        "JavaScript",
        POPUP,
        title,
        text,
        *buttons,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    try:
        return buttons[int(out.decode().strip())]
    except (ValueError, IndexError):
        print(f"[warn] popup failed: {err.decode().strip() or out.decode().strip()}", flush=True)
        return DONE


async def main(connection: iterm2.Connection) -> None:
    if not HAVE_REPORT or not os.path.exists(POPUP):
        await iterm2.Alert(
            "srcp ratings",
            f"{TOOLS}/srcp_favorites.py or srcp_popup.js not found. "
            "Run setup.sh from the checkout.",
        ).async_run(connection)
        return
    view = "Favorites"
    while True:
        others = [v for v in ("Favorites", "History", "Hidden") if v != view]
        try:
            text = view_text(view)
        except Exception as e:  # show it in the window: the Console is easy to miss
            text = f"Couldn't read the database: {e!r}"
        choice = await popup(f"srcp ratings: {view}", text, [DONE, *others])
        if choice == DONE:
            return
        view = choice


iterm2.run_until_complete(main)
