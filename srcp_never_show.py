#!/usr/bin/env python3
"""Scripts menu: list the presets marked Never show, and show one again.

Pick a preset in the dropdown and press Show again; it can come up in new tabs right away
(srcp_set_random_color_profile.py reads the change on the next new session). Favorites
removed by Never show stay removed: rate the preset again to bring one back.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sqlite3
import sys

import iterm2

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, ".tools")
POPUP = os.path.join(TOOLS, "srcp_popup.js")
sys.path.insert(0, TOOLS)
# Imported where used, after main() has checked they're installed (as in srcp_ratings.py).
HAVE_TOOLS = all(
    importlib.util.find_spec(m) for m in ("srcp_favorites", "srcp_schema", "srcp_popup")
)  # srcp_favorites imports srcp_schema

TITLE = "srcp never show"
DONE, SHOW = "Done", "Show again"


def hidden_view(db: sqlite3.Connection) -> tuple[str, list[str]]:
    """(the table as text, hidden preset names)."""
    import srcp_favorites as report  # noqa: PLC0415  (main() checks it is installed first)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rows = report.show_hidden(db, "")
    return out.getvalue().rstrip(), [row[0] for row in rows]


def show_again_note(db: sqlite3.Connection, preset: str) -> str:
    import srcp_favorites as report  # noqa: PLC0415  (main() checks it is installed first)

    return report.shown_again_note(preset, report.show_again(db, preset))


async def main(connection: iterm2.Connection) -> None:
    if not HAVE_TOOLS or not os.path.exists(POPUP):
        await iterm2.Alert(
            TITLE,
            f"{TOOLS}/srcp_favorites.py, srcp_popup.py or srcp_popup.js not found. "
            "Run setup.sh from the checkout.",
        ).async_run(connection)
        return
    import srcp_favorites as report  # noqa: PLC0415  (checked above)
    from srcp_popup import popup  # noqa: PLC0415  (checked above)

    note = ""
    while True:
        names: list[str] = []
        if not os.path.exists(report.DEFAULT_DB):
            text = (
                f"No database at {report.DEFAULT_DB}.\n"
                "Build it with .tools/srcp_build_scheme_db.py."
            )
        else:
            try:
                db = report.connect(report.DEFAULT_DB)
                try:
                    text, names = hidden_view(db)
                finally:
                    db.close()
            except report.OldSchema as e:
                text = f"The database is too old: {e}."
            except Exception as e:  # show it in the window: the Console is easy to miss
                text = f"Couldn't read the database: {e!r}"
        if names:
            text += "\n\nPick one above, then Show again."
        if note:
            text = f"{note}\n\n{text}"
        buttons = [DONE, SHOW] if names else [DONE]
        choice, preset = await popup(TITLE, text, buttons, names or None)
        if choice != SHOW or not preset:
            return
        try:
            db = report.connect(report.DEFAULT_DB)
            try:
                note = show_again_note(db, preset)
            finally:
                db.close()
        except Exception as e:  # e.g. busy past the timeout: say so and show the list again
            busy = isinstance(e, sqlite3.Error) and report.is_busy(e)
            hint = " The database is busy; try again in a moment." if busy else ""
            note = f"Couldn't show {preset} again ({e!r}).{hint}"
        print(f"[info] {note}", flush=True)


iterm2.run_until_complete(main)
