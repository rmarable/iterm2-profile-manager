#!/usr/bin/env python3
"""Export your curation (favorites, ratings, overrides, config, fonts) from the scheme DB as SQL.

Launch history and the derived schemes table are left out. The output is a few KB
of INSERT OR REPLACE statements; restore it into a built DB with
    sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db < curation.sql

    python3 .tools/srcp_export_curation.py [-o FILE] [--db PATH]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = os.path.expanduser("~/.local/share/iterm2-color-schemes/schemes.db")
TABLES = ["favorites", "ratings", "overrides", "config", "fonts"]


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def restore_statement(table, cols, row):
    """SQL that restores one row without clobbering what the target DB already has."""
    values = dict(zip(cols, row))
    if table == "ratings":
        # History appends: no id (the target's own ids stay), and the unique event index
        # makes restoring the same export twice a no-op.
        cols = [c for c in cols if c != "id"]
        verb = "INSERT OR IGNORE"
    elif table == "fonts":
        # Never enable a font in the target (it may not be installed there): fonts it doesn't
        # know yet arrive disabled, marked missing if they were in use here, so a rebuild
        # enables them once installed. Fonts you disabled here, and your labels, carry over.
        mine_off = values.get("enabled") == 0 and not values.get("missing")
        new = dict(values)
        if "enabled" in values:
            new["enabled"] = 0
        if "missing" in values:
            new["missing"] = int(not mine_off)
        insert = (
            f'INSERT OR IGNORE INTO "fonts" ({", ".join(cols)}) '
            f"VALUES ({', '.join(sql_literal(new[c]) for c in cols)});\n"
        )
        sets = []
        if mine_off:
            sets.append("enabled = 0" + (", missing = 0" if "missing" in values else ""))
        if values.get("label"):
            sets.append(f"label = {sql_literal(values['label'])}")
        if not sets:
            return insert
        name = sql_literal(values["name"])
        return insert + f"UPDATE fonts SET {', '.join(sets)} WHERE name = {name};\n"
    else:
        verb = "INSERT OR REPLACE"
    literals = ", ".join(sql_literal(values[c]) for c in cols)
    return f'{verb} INTO "{table}" ({", ".join(cols)}) VALUES ({literals});\n'


def export(db, out):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    out.write(f"-- iterm2-profile-manager curation export, {stamp}, schema version {version}\n")
    out.write("-- Restore: sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db < this-file\n")
    # sqlite3 runs dot-commands from its input: stop at the first error instead of half-applying.
    out.write(".bail on\n")
    out.write(
        f"-- A 'CHECK constraint failed' here means the DB is older than schema {version}:\n"
        "-- rebuild it first (python3 .tools/srcp_build_scheme_db.py), then restore again.\n"
    )
    out.write(f"CREATE TEMP TABLE srcp_schema_check (v INTEGER CHECK (v >= {version}));\n")
    out.write("INSERT INTO srcp_schema_check SELECT user_version FROM pragma_user_version;\n")
    # Dropped again so a second export can be read in the same sqlite3 session.
    out.write("DROP TABLE srcp_schema_check;\n")
    out.write("BEGIN;\n")
    for table in TABLES:
        cols = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        if not cols:  # e.g. ratings, in a DB built before it existed
            continue
        query = f'SELECT {", ".join(cols)} FROM "{table}" ORDER BY 1'
        rows = db.execute(query).fetchall()
        out.write(f"-- {table}: {len(rows)} rows\n")
        for row in rows:
            out.write(restore_statement(table, cols, row))
    out.write("COMMIT;\n")


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("-o", "--output", help="write here instead of stdout")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"No database at {args.db}")
    # mode=rw (it only reads): mode=ro can fail on a WAL DB that nothing else has open.
    db = sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=rw", uri=True)
    try:
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                export(db, f)
            print(f"Wrote {args.output}", file=sys.stderr)
        else:
            export(db, sys.stdout)
    finally:
        db.close()


if __name__ == "__main__":
    main()
