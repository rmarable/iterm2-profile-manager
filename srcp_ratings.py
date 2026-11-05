#!/usr/bin/env python3
"""Scripts menu: review favorites, rating history or hidden presets in a popup.

Shows the same tables as .tools/srcp_favorites.py in a native macOS alert with a
fixed-width font (.tools/srcp_popup.py runs .tools/srcp_popup.js), scrollable and
selectable. The buttons switch views in place. Reads the scheme DB only.
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
# .tools/srcp_favorites.py supplies the report's own tables, so both views agree, and
# .tools/srcp_popup.py shows the window. They're imported where used (pyright resolves them
# via extraPaths, so lint.sh checks the calls), after main() has checked they're installed.
HAVE_TOOLS = all(
    importlib.util.find_spec(m) for m in ("srcp_favorites", "srcp_schema", "srcp_popup")
)  # srcp_favorites imports srcp_schema

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
                report.show_hidden(db, "Show one again: Scripts > srcp_never_show.py")
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
    db = report.connect(report.DEFAULT_DB)  # it only reads
    try:
        return render(db, view)
    finally:
        db.close()


async def main(connection: iterm2.Connection) -> None:
    if not HAVE_TOOLS or not os.path.exists(POPUP):
        await iterm2.Alert(
            "srcp ratings",
            f"{TOOLS}/srcp_favorites.py, srcp_popup.py or srcp_popup.js not found. "
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
        from srcp_popup import popup  # noqa: PLC0415  (checked above)

        choice, _ = await popup(f"srcp ratings: {view}", text, [DONE, *others])
        if choice == DONE:
            return
        view = choice


iterm2.run_until_complete(main)
