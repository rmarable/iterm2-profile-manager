#!/usr/bin/env python3
"""Give each new session a color preset matching the macOS light/dark mode, plus a random font.

Picks from the scheme DB built by .tools/srcp_build_scheme_db.py: sometimes
(favorite_share, ramped by the number of favorites) a starred favorite of the current
mode, weighted by stars; otherwise a preset from the discovery view not among the last
recent_window picks, with colored-background presets weighted by colorful_weight.
Colors the build found hard to see get its adjustments applied (config fix_colors = 0 turns
that off). Records every launch and when it ended, and snapshots the DB once a day (checked every 15
minutes while it runs).
"Save/update this profile" (right-click) or the save_profile() key binding stars the
current look; "Remove profile from favorites" (right-click; hidden on non-favorites only where
iTerm2 supports API protocol 1.23+) un-stars it; "Change to another profile" (right-click)
re-rolls the session's look; "Never show this profile" (right-click) hides the preset and
re-rolls (Scripts > srcp_never_show.py undoes it). Every favorite is also an
iTerm2 profile ("Kibble - Menlo"), kept in a Dynamic Profiles file. Tabs of a profile with
"Use separate colors for light and dark mode" on are left as they are (the Console says so).
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import contextvars
import fcntl
import json
import os
import plistlib
import random
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from pathlib import Path
from typing import IO, Any, Literal, NamedTuple, TypeVar

import iterm2
from iterm2 import api_pb2
from iterm2.capabilities import supports_context_menu_submenus_and_visibility

# What this script shares with the build and the other tools; setup.sh installs them in the
# Scripts folder's .tools/, next to AutoLaunch/ (a checkout that is the Scripts folder has them).
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tools")
sys.path.insert(0, TOOLS)
try:
    import srcp_db_backup
    import srcp_iterm_prefs
    from srcp_schema import (
        COMPONENTS,
        DATA_DIR,
        DB_PATH,
        FIX_ON_SQL,
        FONT_DIRS,
        MIN_SCHEMA,
        NUMERIC_CONFIG,
        REBUILD,
        SEED_CONFIG,
        SEED_FONTS,
        UNHIDE,
        colors_hash,
        config_number,
        default_font_label,
        is_busy,
        schema_problem,
        space_named,
        space_of,
    )
except ImportError as e:
    print(
        f"[error] missing or outdated {TOOLS}/srcp_schema.py, srcp_db_backup.py or "
        "srcp_iterm_prefs.py "
        f"({e}); run ./setup.sh again",
        flush=True,
    )
    sys.exit(1)

VERSION = "0.14.4"  # bump on every change, so the Console shows which code is running
LOCK_PATH = os.path.join(DATA_DIR, ".lock")
# How often the running script checks whether a snapshot is due. asyncio's clock stops while
# the Mac sleeps, so check often (it's a few file stats) to keep it close to daily.
SNAPSHOT_CHECK_S = 15 * 60
DEFAULT_SIZE = 13
# The numeric settings pick() reads, with the build's seed values when config lacks one.
DEFAULT_CONFIG = {k: float(SEED_CONFIG[k]) for k in NUMERIC_CONFIG}
# Font for characters the main font lacks, e.g. Powerlevel10k prompt icons; config overrides.
DEFAULT_NONASCII_FONT = SEED_CONFIG["nonascii_font"]
NONASCII_FONT_FILES = SEED_FONTS[DEFAULT_NONASCII_FONT]
MENU_ID = "com.rmarable.set-random-color-profile.save"
REMOVE_MENU_ID = "com.rmarable.set-random-color-profile.remove"
CHANGE_MENU_ID = "com.rmarable.set-random-color-profile.change"
NEVER_MENU_ID = "com.rmarable.set-random-color-profile.never"
# iTerm2 watches this folder and updates the Profiles menu when the file changes.
DYNAMIC_PATH = os.path.expanduser(
    "~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist"
)
PROFILE_TAG = "Favorites"
GUID_NAMESPACE = uuid.UUID("468579c8-bf93-425d-9e25-ff5f7ddebd11")  # fixed: GUIDs must not change
STAR_BUTTONS = [f"{n} {'★' * n}" for n in range(1, 6)]  # "3 ★★★"
REMOVE, NEVER, CANCEL = "Remove favorite", "Never show", "Cancel"
PRESET_ERRORS = (iterm2.GetPresetException, iterm2.ListPresetsException)

T = TypeVar("T")

_warned: set[str] = set()


class DbUnavailable(Exception):
    """A Store call didn't take: no DB, damaged, the wrong schema, busy, or it failed and was
    rolled back. Already logged when raised. Not an sqlite3.Error or OSError, so Store.call
    can't catch its own."""


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log("warn", msg)


def warn(key: str, msg: str, every: bool) -> None:
    """Log msg every time (asked for by hand, where a dialog says to look in the Console), or
    once per key."""
    if every:
        log("warn", msg)
    else:
        warn_once(key, msg)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


async def acquire_lock(wait_s: float = 5.0, every_s: float = 0.25) -> IO[str] | None:
    """Hold an exclusive lock for the life of the process, so restarts can't stack copies.

    Waits up to wait_s for it: a restart from the Scripts menu can start this copy while the
    old one is still exiting. Sessions opened meanwhile are colored by startup().
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    f = open(LOCK_PATH, "w")  # noqa: SIM115 - held open until the process exits
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            if time.monotonic() >= deadline:
                f.close()
                return None
            await asyncio.sleep(every_s)


def stale(db: sqlite3.Connection) -> bool:
    """Whether Store's open connection must be reopened: rebuilt to another schema since it
    connected, or unreadable (reopening says what to do, once; an open WAL connection mostly
    reads a file damaged in place from its cache, checked 2026-11-05). A busy DB raises.
    About a microsecond a call (measured 2026-11-05), and it leaves no transaction open."""
    try:
        return db.execute("PRAGMA user_version").fetchone()[0] != MIN_SCHEMA
    except sqlite3.DatabaseError as e:
        if is_busy(e):
            raise
        return True


class Store:
    """One read-write connection, used from worker threads one call at a time."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.db: sqlite3.Connection | None = None
        self.inode: int | None = None
        self.lock = threading.Lock()
        self._sync_lock: asyncio.Lock | None = None
        self._sync_loop: asyncio.AbstractEventLoop | None = None

    @property
    def sync_lock(self) -> asyncio.Lock:
        """Favorite profile syncs, one at a time: each reads the DB, then writes the file.

        Made in the running loop (one per loop: an asyncio.Lock can't move between them).
        """
        loop = asyncio.get_running_loop()
        if self._sync_lock is None or self._sync_loop is not loop:
            self._sync_lock, self._sync_loop = asyncio.Lock(), loop
        return self._sync_lock

    def _connect(self) -> sqlite3.Connection:
        """The open connection, (re)connecting if needed; raises DbUnavailable (logged once)
        for no DB, a damaged one or the wrong schema. A busy DB raises its sqlite3 error."""
        try:
            inode = os.stat(self.path).st_ino
        except FileNotFoundError:
            inode = None
        if self.db is not None and inode != self.inode:
            # Deleted, moved away or replaced by a rename: don't keep writing to the old file.
            # A cp over the file keeps its inode and isn't noticed (stale() mostly misses it too;
            # see its docstring).
            self.db.close()
            self.db = None
        if self.db is not None and stale(self.db):
            self.db.close()
            self.db = None
        if self.db is None:
            if inode is None:
                warn_once(
                    "no-db",
                    f"no scheme DB at {self.path}; run {REBUILD}. "
                    "Sessions keep their profile's colors until then.",
                )
                raise DbUnavailable("no scheme DB")
            # mode=rw: never create an empty DB.
            uri = Path(self.path).resolve().as_uri() + "?mode=rw"
            db = sqlite3.connect(
                uri, uri=True, timeout=5, isolation_level=None, check_same_thread=False
            )
            _warned.discard("no-db")
            # Checked on each connect: an old DB isn't kept open, so the call after the
            # rebuild this asks for (same file, same inode) connects again and passes.
            try:
                problem = schema_problem(db)
            except sqlite3.DatabaseError as e:  # not a DB at all, or damaged (not just busy)
                db.close()
                if is_busy(e):
                    raise
                warn_once(
                    "bad-db",
                    f"{self.path} is damaged ({e}); restore a snapshot: docs/REFERENCE.md, "
                    "Restore (the build refuses it too)",
                )
                raise DbUnavailable(f"damaged: {e}") from e
            if problem:
                try:
                    newer = db.execute("PRAGMA user_version").fetchone()[0] > MIN_SCHEMA
                finally:
                    db.close()
                # Own keys, so a rebuild by newer scripts after "rebuild it" still says restart.
                key = "new-schema" if newer else "old-schema"
                warn_once(key, f"{problem}. Sessions keep their profile's colors until then.")
                raise DbUnavailable(problem)
            for key in ("old-schema", "new-schema", "bad-db"):
                _warned.discard(key)
            self.db, self.inode = db, inode
        return self.db

    def _rollback(self) -> None:
        """Undo an open transaction. If even that fails (the file vanished, say), drop the
        connection so the next call reconnects, and keep the error that got us here."""
        if self.db is None or not self.db.in_transaction:
            return
        try:
            self.db.execute("ROLLBACK")
        except sqlite3.Error as e:
            log("warn", f"DB rollback failed ({e}); reconnecting on the next call")
            with contextlib.suppress(sqlite3.Error):
                self.db.close()
            self.db = None

    async def call(self, fn: Callable[..., T], *args: Any) -> T:
        """Run fn(db, *args) in a worker thread; raises DbUnavailable (logged) if there's no
        usable DB or the call failed."""

        def run() -> T:
            with self.lock:
                try:
                    return fn(self._connect(), *args)
                except (sqlite3.Error, OSError) as e:  # OSError: snapshot files
                    self._rollback()
                    log("warn", f"DB {fn.__name__}: {e}")
                    raise DbUnavailable(f"{fn.__name__}: {e}") from e
                except BaseException:
                    self._rollback()  # never leave a BEGIN IMMEDIATE open
                    raise

        return await asyncio.to_thread(run)


# DB calls: each runs in a worker thread through Store.call, which rolls back on failure and
# raises DbUnavailable.


def read_config(db: sqlite3.Connection) -> dict[str, float]:
    cfg: dict[str, float] = dict(DEFAULT_CONFIG)
    for key, value in db.execute("SELECT key, value FROM config"):
        if key not in cfg:
            continue
        number = config_number(value)  # None for inf, nan, a huge value or a non-number
        if number is not None:
            cfg[key] = number
        else:
            warn_once(
                f"config-{key}", f"config {key}={value!r} isn't a usable number; using {cfg[key]}"
            )
    return cfg


class Pick(NamedTuple):
    preset: str
    font: str | None  # a favorite's saved font; None for discovery, or if it's disabled since
    source: str  # 'favorite' or 'discovery'
    stars: int | None


def with_twins(db: sqlite3.Connection, names: set[str]) -> set[str]:
    """names plus their byte-identical twins (old spellings, the repo's own; duplicate_of both
    ways), so leaving out a look leaves it out under every name."""
    if not names:
        return names
    twins = db.execute(
        "SELECT name FROM schemes WHERE coalesce(duplicate_of, name) IN "
        "(SELECT coalesce(duplicate_of, name) FROM schemes "
        "WHERE name IN (SELECT value FROM json_each(?)))",
        (json.dumps(sorted(names)),),
    )
    return names | {n for (n,) in twins}


def pick(db: sqlite3.Connection, mode: str, exclude: set[str]) -> Pick | None:
    """Choose a favorite or a discovery preset for mode, or None if nothing fits."""
    cfg = read_config(db)
    exclude = with_twins(db, exclude)
    favs: list[tuple[str, str, int]] = [
        r
        for r in db.execute("SELECT preset, font, stars FROM favorite_pool WHERE mode = ?", (mode,))
        if r[0] not in exclude
    ]
    rows = db.execute("SELECT name, colorful FROM discovery WHERE mode = ?", (mode,)).fetchall()
    disc = [(n, c) for n, c in rows if n not in exclude]
    # Ramp up by distinct presets, so saving one preset with several fonts doesn't speed it.
    target = cfg["favorite_target"]
    n_presets = len({f[0] for f in favs})
    share = cfg["favorite_share"] * (min(1.0, n_presets / target) if target > 0 else 1.0)
    if favs and (not disc or random.random() < share):
        preset, font, stars = random.choices(favs, weights=[f[2] for f in favs])[0]
        enabled = db.execute("SELECT enabled FROM fonts WHERE name = ?", (font,)).fetchone()
        if enabled is not None and not enabled[0]:
            font = None  # disabled since it was saved: a random font instead
        return Pick(preset, font, "favorite", stars)
    if not disc:
        return None
    window = max(0, int(cfg["recent_window"]))
    recent = {
        n
        for (n,) in db.execute(
            "SELECT preset FROM launches WHERE mode = ? AND source = 'discovery' "
            "ORDER BY id DESC LIMIT ?",
            (mode, window),
        )
    }
    pool = [d for d in disc if d[0] not in recent] or disc
    weight = max(0.0, cfg["colorful_weight"])
    weights = [weight if colorful else 1.0 for _, colorful in pool]
    preset = random.choices(pool, weights=weights if sum(weights) > 0 else None)[0][0]
    return Pick(preset, None, "discovery", None)


def look_fixes(db: sqlite3.Connection, preset: str) -> tuple[str | None, str | None]:
    """(adjustments as JSON or None, the build's color_hash) for preset."""
    row = db.execute(
        "SELECT CASE WHEN " + FIX_ON_SQL + " THEN fixes END, color_hash "
        "FROM schemes WHERE name = ?",
        (preset,),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


class Look(NamedTuple):
    """Everything color_session needs from the DB for one session, from one Store.call."""

    preset: str
    font: str | None  # the favorite's font, else a random enabled one; None if there's none
    source: str  # 'favorite' or 'discovery'
    # The picked favorite's, or the random font's own when that (preset, font) is one too.
    stars: int | None
    fixes_json: str | None
    built_hash: str | None
    nonascii: str | None  # the icon font to set, if any
    is_favorite: bool  # this (preset, font) is a favorite
    font_off: bool  # a favorite picked without its saved font, since disabled


def pick_look(db: sqlite3.Connection, mode: str, exclude: set[str]) -> Look | None:
    """pick(), plus its font, adjustments and icon font; None if nothing fits.

    In one read transaction, so a rebuild committing meanwhile can't pair one build's pick
    with another's adjustments (under WAL a reader never blocks the rebuild).
    """
    db.execute("BEGIN")  # Store.call rolls back if anything below fails
    choice = pick(db, mode, exclude)
    if choice is None:
        look = None
    else:
        font = choice.font or random_font(db)
        # pick() drops a disabled saved font.
        font_off = choice.source == "favorite" and choice.font is None
        font_stars = favorite_stars(db, choice.preset, font) if font else None
        stars = choice.stars
        if font_off and font_stars is not None:
            stars = font_stars  # the random font is a favorite too: show its own stars
        look = Look(
            choice.preset,
            font,
            choice.source,
            stars,
            *look_fixes(db, choice.preset),
            nonascii_font(db),
            font_stars is not None,
            font_off,
        )
    db.execute("COMMIT")
    return look


def usable_fixes(
    preset: str,
    fixes_json: str | None,
    built_hash: str | None,
    live: dict[str, tuple[float, float, float]],
    spaces: dict[str, str],
) -> dict[str, tuple[float, float, float]]:
    """{key: (r, g, b)} to apply to preset, or {} (none, or its colors changed since the build).

    live: the installed preset's own colors, before any adjustment; spaces: each one's color
    space, as srcp_schema.space_of names them (the fixes are in each color's own space).
    """
    if not fixes_json:
        return {}
    if colors_hash(live, spaces) != built_hash:
        warn_once(
            f"fixes-stale-{preset}",
            f"{preset} changed since the DB was built; its color adjustments are skipped "
            f"until you rebuild ({REBUILD})",
        )
        return {}
    try:
        return {
            k: (float(v[0]), float(v[1]), float(v[2])) for k, v in json.loads(fixes_json).items()
        }
    except (ValueError, TypeError, IndexError, AttributeError) as e:
        warn_once(f"fixes-bad-{preset}", f"{preset}: unreadable color adjustments ({e}); skipped")
        return {}


def adjust_preset(
    name: str, preset: iterm2.ColorPreset, fixes_json: str | None, built_hash: str | None
) -> int:
    """Apply the adjustments to preset's colors in place (each keeps its color space)."""
    live = {v.key: (v.red / 255, v.green / 255, v.blue / 255) for v in preset.values}
    # The API reports a color with no space as Calibrated, as iTerm2 draws it (checked
    # 2026-11-05, read-only ColorPreset.async_get), so this hashes like the build's prefs dicts.
    spaces = {v.key: space_named(v.color_space.value) for v in preset.values}
    fixes = usable_fixes(name, fixes_json, built_hash, live, spaces)
    for v in preset.values:
        if v.key in fixes:
            v.red, v.green, v.blue = (c * 255 for c in fixes[v.key])
    return len(fixes)


def adjust_colors(
    name: str, colors: dict[str, Any], fixes_json: str | None, built_hash: str | None
) -> tuple[dict[str, Any], int]:
    """(a copy of a prefs preset dict with the adjustments, how many): color space and alpha
    kept. The count is noted, so a session opened from the profile reports what it shows."""
    live = {
        k: tuple(float(v[c]) for c in COMPONENTS)
        for k, v in colors.items()
        if isinstance(v, dict) and all(c in v for c in COMPONENTS)
    }
    spaces = {k: space_of(v) for k, v in colors.items() if isinstance(v, dict)}
    fixes = usable_fixes(name, fixes_json, built_hash, live, spaces)  # pyright: ignore[reportArgumentType]  # 3-tuples by construction
    adjusted = {
        k: {**colors[k], **dict(zip(COMPONENTS, rgb, strict=True))} for k, rgb in fixes.items()
    }
    return {**colors, **adjusted}, len(fixes)


def nonascii_font(db: sqlite3.Connection) -> str | None:
    """config nonascii_font (PostScript name); '' turns it off."""
    row = db.execute("SELECT value FROM config WHERE key = 'nonascii_font'").fetchone()
    value = DEFAULT_NONASCII_FONT if row is None or row[0] is None else str(row[0]).strip()
    if not value:
        return None
    # The default may not be installed (MesloLGS comes from Homebrew, which is optional);
    # setting a missing icon font makes iTerm2 fall back to another face for every icon.
    # Checks the file, not fonts.enabled: that only keeps a font out of the random pool.
    installed = any(
        os.path.exists(os.path.join(os.path.expanduser(d), f))
        for d in FONT_DIRS
        for f in NONASCII_FONT_FILES
    )
    if value == DEFAULT_NONASCII_FONT and not installed:
        warn_once("no-nonascii-font", f"{value} isn't installed; icon font left as is")
        return None
    return value


def random_font(db: sqlite3.Connection) -> str | None:
    row = db.execute(
        "SELECT name FROM fonts WHERE enabled = 1 ORDER BY random() LIMIT 1"
    ).fetchone()
    return row[0] if row else None


Queue = Literal["startup", "new", "reroll"]  # which coloring a launches row came from


class Launch(NamedTuple):
    """A launches row, as the script writes it (launched_at is added)."""

    preset: str
    font: str | None
    size: float
    mode: str | None
    source: str  # 'favorite' or 'discovery'
    stars: int | None
    iterm_profile: str | None
    session_id: str
    # How long the tab took (LaunchTiming); None only where a test builds a row.
    coloring_ms: int | None = None
    queued_ms: int | None = None  # None for a re-roll, which takes no slot
    queue: Queue | None = None


# Built from Launch's fields, so the two can't drift apart (a test checks each is a column).
LAUNCH_INSERT = (
    f"INSERT INTO launches (launched_at, {', '.join(Launch._fields)}) "  # noqa: S608  (field names only)
    f"VALUES (?{', ?' * len(Launch._fields)})"
)


def record_launch(db: sqlite3.Connection, launch: Launch) -> None:
    db.execute(LAUNCH_INSERT, (now(), *launch))


def record_change(db: sqlite3.Connection, launch: Launch) -> None:
    """End the session's open launch as 'changed' and add its new one, both or neither."""
    db.execute("BEGIN IMMEDIATE")  # Store.call rolls back if anything below fails
    record_end(db, launch.session_id, "changed")
    record_launch(db, launch)
    db.execute("COMMIT")


def record_end(db: sqlite3.Connection, session_id: str, reason: str = "closed") -> None:
    """reason: 'closed' (session ended) or 'changed' (re-rolled from the menu)."""
    t = now()
    db.execute(
        "UPDATE launches SET ended_at = ?, end_reason = ?, "
        "duration_s = CAST(round((julianday(?) - julianday(launched_at)) * 86400) AS INTEGER) "
        "WHERE session_id = ? AND ended_at IS NULL AND end_reason IS NULL",
        (t, reason, t, session_id),
    )


def close_stale(db: sqlite3.Connection, live_ids: set[str], started: str) -> int:
    """Launches whose session is gone without an end event (iTerm2 quit) end as 'unknown'.

    Only launches from before this script started: sessions colored since then aren't in
    live_ids, which was taken at startup.
    """
    # The ids as one JSON list; empty ones dropped, since a NULL in NOT IN matches nothing.
    live = json.dumps([i for i in live_ids if i])
    return db.execute(  # one statement, so one transaction
        "UPDATE launches SET end_reason = 'unknown' "
        "WHERE ended_at IS NULL AND end_reason IS NULL AND launched_at < ? "
        "AND (session_id IS NULL OR session_id NOT IN (SELECT value FROM json_each(?)))",
        (started, live),
    ).rowcount


def preset_info(
    db: sqlite3.Connection, preset: str, font: str
) -> tuple[tuple[int, str, int] | None, int | None]:
    """((launches, last launched, changed away from) or None, stars or None)."""
    # From launches directly (the same counts as the launch_stats view, which is kept for
    # users' own queries): one query on the launches_preset index, not a whole-table GROUP BY.
    stats = db.execute(
        "SELECT count(*), max(launched_at), sum(end_reason = 'changed') FROM launches "
        "WHERE preset = ? GROUP BY preset",
        (preset,),
    ).fetchone()
    return stats, favorite_stars(db, preset, font)


def log_rating(
    db: sqlite3.Connection, preset: str, font: str | None, action: str, stars: int | None
) -> None:
    """Add a ratings row (history)."""
    previous = favorite_stars(db, preset, font) if font is not None else None
    db.execute(
        # OR IGNORE: a second event for the same look in the same second would hit the
        # ratings_event unique index and roll back the whole save; history is best-effort.
        "INSERT OR IGNORE INTO ratings (at, preset, font, action, stars, previous_stars) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now(), preset, font, action, stars, previous),
    )


def save_favorite(db: sqlite3.Connection, preset: str, font: str, size: float, stars: int) -> None:
    db.execute("BEGIN IMMEDIATE")  # Store.call rolls back if anything below fails
    # Starring a preset marked Never show (e.g. in a tab opened before) unhides it;
    # otherwise the favorite would never be picked or get a profile. Update rather than
    # delete (UNHIDE), so a mode override survives; rating it says it's wanted (and it may
    # have been a seeded near-miss).
    db.execute(UNHIDE, {"name": preset})
    log_rating(db, preset, font, "save", stars)
    db.execute(
        "INSERT INTO favorites (preset, font, size, stars, saved_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (preset, font) DO UPDATE SET "
        "size = excluded.size, stars = excluded.stars, saved_at = excluded.saved_at",
        (preset, font, size, stars, now()),
    )
    db.execute("COMMIT")


def remove_favorite(db: sqlite3.Connection, preset: str, font: str) -> bool:
    """Un-star one (preset, font); the preset goes back to discovery. False if not a favorite."""
    db.execute("BEGIN IMMEDIATE")
    if not is_favorite(db, preset, font):
        db.execute("ROLLBACK")
        return False
    log_rating(db, preset, font, "remove", None)
    db.execute("DELETE FROM favorites WHERE preset = ? AND font = ?", (preset, font))
    db.execute("COMMIT")
    return True


def favorite_fonts(db: sqlite3.Connection, preset: str) -> list[tuple[str, int]]:
    """(font label, stars) of each favorite of preset, best first."""
    return [
        (font_label(db, font), stars)
        for font, stars in db.execute(
            "SELECT font, stars FROM favorites WHERE preset = ? ORDER BY stars DESC, font",
            (preset,),
        ).fetchall()
    ]


def favorite_stars(db: sqlite3.Connection, preset: str, font: str) -> int | None:
    """The stars of (preset, font), or None if it isn't a favorite."""
    row = db.execute(
        "SELECT stars FROM favorites WHERE preset = ? AND font = ?", (preset, font)
    ).fetchone()
    return row[0] if row else None


def is_favorite(db: sqlite3.Connection, preset: str, font: str) -> bool:
    return favorite_stars(db, preset, font) is not None


def favorite_guid(preset: str, font: str) -> str:
    """Stable GUID of a favorite's profile, so iTerm2 keeps it across rewrites."""
    return str(uuid.uuid5(GUID_NAMESPACE, f"{preset}\0{font}")).upper()


def favorite_by_guid(db: sqlite3.Connection, guid: str) -> tuple[str, str, int, str | None] | None:
    """(preset, font, stars, mode or None) of the favorite with this GUID."""
    rows = db.execute(
        "SELECT f.preset, f.font, f.stars, e.mode "
        "FROM favorites f LEFT JOIN effective e ON e.name = f.preset"
    ).fetchall()
    for preset, font, stars, mode in rows:
        if favorite_guid(preset, font) == guid:
            return preset, font, stars, mode
    return None


def font_label(db: sqlite3.Connection, font: str) -> str:
    row = db.execute("SELECT label FROM fonts WHERE name = ?", (font,)).fetchone()
    return row[0] if row and row[0] else default_font_label(font)


# Favorites' iTerm2 profiles (Profiles > Favorites). sync_and_log reads what they're made of
# under Store.lock (read_favorites), then reads iTerm2's prefs and writes the file outside it
# (write_favorite_profiles), so a slow `defaults export` never holds up other DB calls.


class FavoriteRow(NamedTuple):
    preset: str
    font: str
    size: float | None
    fixes_json: str | None
    built_hash: str | None
    label: str


class FavoritesSnapshot(NamedTuple):
    state: str  # changes whenever the profiles would
    rows: list[FavoriteRow]
    fallback: str | None  # the icon font, as applied


_profiles_state: list[str | None] = [None]  # the FavoritesSnapshot.state last written from
_profiles_failed: list[Any] = [None, 0.0]  # the state a write last failed for, and when
SYNC_RETRY_S = 600  # how soon a failed profiles write is tried again with nothing changed
_profile_adjusted: dict[str, int] = {}  # preset -> colors adjusted in its favorite profiles


def favorites_state(db: sqlite3.Connection, fallback: str | None) -> str:
    """A fingerprint of what the profiles are made of: favorites, Never show, fonts, and the
    favorites' adjustments and color hashes (a rebuild that changes either rewrites them).

    Cheap, so edits made in sqlite3 or by restoring an export show up without a restart.
    """
    row = db.execute(
        "SELECT (SELECT group_concat(preset || ':' || font || ':' || stars || ':' || "
        "ifnull(size, ''), ';') FROM (SELECT * FROM favorites ORDER BY preset, font)), "
        "(SELECT group_concat(name, ';') FROM "
        "(SELECT name FROM overrides WHERE include = 0 ORDER BY name)), "
        "(SELECT group_concat(name || ':' || enabled || ':' || ifnull(label, ''), ';') "
        "FROM (SELECT * FROM fonts ORDER BY name))"
    ).fetchone()
    fixes = db.execute(
        "SELECT " + FIX_ON_SQL + ", (SELECT group_concat(n || ':' || ifnull(fx, '') || ':' "  # noqa: S608  (constants only)
        "|| ifnull(h, ''), ';') FROM (SELECT DISTINCT f.preset AS n, s.fixes AS fx, "
        "s.color_hash AS h FROM favorites f LEFT JOIN schemes s ON s.name = f.preset "
        "ORDER BY f.preset))"
    ).fetchone()
    return "|".join(str(v) for v in (*row, *fixes, fallback))


def read_favorites(db: sqlite3.Connection) -> FavoritesSnapshot:
    """What the favorites' profiles are made of, from the DB (Never show ones left out).

    One read transaction, so the fingerprint and the rows agree even if another tool (an
    export restore, srcp_never_show.py) commits meanwhile.
    """
    db.execute("BEGIN")  # Store.call rolls back if anything below fails
    fallback = nonascii_font(db)
    state = favorites_state(db, fallback)
    where = "WHERE f.preset NOT IN (SELECT name FROM overrides WHERE include = 0) "
    rows: list[tuple[Any, ...]] = db.execute(
        "SELECT f.preset, f.font, f.size, CASE WHEN " + FIX_ON_SQL + " THEN s.fixes END, "  # noqa: S608  (constants only)
        "s.color_hash FROM favorites f LEFT JOIN schemes s ON s.name = f.preset "
        + where
        + "ORDER BY f.preset, f.font"
    ).fetchall()
    favorites = [
        FavoriteRow(preset, font, size, fixes_json, built_hash, font_label(db, font))
        for preset, font, size, fixes_json, built_hash in rows
    ]
    db.execute("COMMIT")
    return FavoritesSnapshot(state, favorites, fallback)


def favorite_profiles(
    rows: list[FavoriteRow],
    presets: dict[str, Any],
    parent: str,
    fallback: str | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """(one profile dict per favorite whose preset is installed, colors adjusted by preset)."""
    profiles, adjusted = [], {}
    for row in rows:
        if row.preset not in presets:
            warn_once(f"profile-{row.preset}", f"favorite {row.preset!r} isn't an installed preset")
            continue
        _warned.discard(f"profile-{row.preset}")  # installed again: say so again if it goes
        colors, adjusted[row.preset] = adjust_colors(
            row.preset, presets[row.preset], row.fixes_json, row.built_hash
        )
        pt = float(row.size or DEFAULT_SIZE)
        size_text = str(int(pt)) if pt.is_integer() else str(pt)
        profiles.append(
            {
                **colors,
                "Name": f"{row.preset} - {row.label}",
                "Guid": favorite_guid(row.preset, row.font),
                "Dynamic Profile Parent Name": parent,
                "Normal Font": f"{row.font} {size_text}",
                # Off: with it on, iTerm2 draws the light/dark color keys, not these.
                "Use Separate Colors for Light and Dark Mode": False,
                **(
                    {"Use Non-ASCII Font": True, "Non Ascii Font": f"{fallback} {size_text}"}
                    if fallback
                    else {}
                ),
                "Tags": [PROFILE_TAG],  # each tag is a Profiles submenu: one, so listed once
            }
        )
    return profiles, adjusted


def write_favorite_profiles(snap: FavoritesSnapshot) -> int | None:
    """Write one iTerm2 profile per favorite; return the count, or None if the file is current.

    Reads iTerm2's prefs (the presets, and the default profile as the parent), so it runs
    outside Store.lock.
    """
    prefs = srcp_iterm_prefs.load_prefs(timeout=30)
    default_guid = prefs.get("Default Bookmark Guid")
    parent = next(
        (b.get("Name") for b in prefs.get("New Bookmarks", []) if b.get("Guid") == default_guid),
        "Default",
    )
    profiles, adjusted = favorite_profiles(
        snap.rows, prefs.get(srcp_iterm_prefs.PRESETS_KEY, {}), parent, snap.fallback
    )
    data = plistlib.dumps({"Profiles": profiles})
    folder, name = os.path.split(DYNAMIC_PATH)
    partial = os.path.join(folder, f".{name}.partial")
    try:
        with open(DYNAMIC_PATH, "rb") as f:
            if f.read() == data:
                _profile_adjusted.update(adjusted)
                with contextlib.suppress(FileNotFoundError):
                    os.remove(partial)  # left by a run that quit mid-write
                return None
    except FileNotFoundError:
        pass
    os.makedirs(folder, exist_ok=True)
    # Write, then rename, so iTerm2 never reads half a file. The temp name starts with a dot
    # because iTerm2 reloads every other file in the folder on any change (it skips dot-files;
    # iTermDynamicProfileManager.m), and a temp copy would duplicate every GUID.
    with open(partial, "wb") as f:
        f.write(data)
    os.replace(partial, DYNAMIC_PATH)
    _profile_adjusted.update(adjusted)  # only what the file now holds
    return len(profiles)


def never_show(db: sqlite3.Connection, preset: str) -> bool:
    """Hide preset and drop its favorites; False if it was already hidden with none."""
    db.execute("BEGIN IMMEDIATE")  # Store.call rolls back if anything below fails
    fonts = [f for (f,) in db.execute("SELECT font FROM favorites WHERE preset = ?", (preset,))]
    hidden = db.execute(
        "SELECT 1 FROM overrides WHERE name = ? AND include = 0", (preset,)
    ).fetchone()
    if hidden and not fonts:
        db.execute("ROLLBACK")
        return False
    db.execute(
        "INSERT INTO overrides (name, include) VALUES (?, 0) "
        "ON CONFLICT (name) DO UPDATE SET include = 0",
        (preset,),
    )
    for font in fonts:  # one row per favorite it removes, so their stars are on record
        log_rating(db, preset, font, "never_show", None)
    if not fonts:
        log_rating(db, preset, None, "never_show", None)
    db.execute("DELETE FROM favorites WHERE preset = ?", (preset,))
    db.execute("COMMIT")
    return True


def daily_snapshot(db: sqlite3.Connection) -> str | None:
    """Snapshot the DB through this connection if the newest snapshot is a day old and the DB
    passes its integrity check (else warn once and skip)."""
    folder = os.path.join(os.path.dirname(DB_PATH), "backups")
    if srcp_db_backup.future_snapshots(folder):
        warn_once(
            "snapshot-future",
            f"snapshots in {folder} are named with a time in the future (a wrong clock, or "
            "copied from another Mac); they're kept but don't count as the newest",
        )
    rearm = True
    try:
        return srcp_db_backup.snapshot_if_due(DB_PATH, src=db)
    except srcp_db_backup.DamagedDb as e:
        # Checked again every SNAPSHOT_CHECK_S; the snapshots already there are kept.
        rearm = False
        warn_once(
            "snapshot-damaged",
            f"daily DB snapshot skipped: {DB_PATH} fails its integrity check ({e}); the "
            f"snapshots in {folder} are kept. Restore one: docs/REFERENCE.md, Restore",
        )
        return None
    except sqlite3.Error as e:
        rearm = not is_busy(e)  # busy: the check may not have run, so nothing is known
        raise
    finally:
        # Anything else (not due, a snapshot, or a failure after the check passed) re-arms it.
        if rearm:
            _warned.discard("snapshot-damaged")


async def snapshot_periodically(
    store: Store,
    every_s: float = SNAPSHOT_CHECK_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Take the daily snapshot while iTerm2 stays open for days; startup takes the first."""
    while True:
        await sleep(every_s)
        try:
            backup = await store.call(daily_snapshot)
        except DbUnavailable:  # logged; try again next time
            continue
        except Exception as e:  # a bad check must not stop later ones, or log every time
            warn_once("snapshot-check", f"DB snapshot check failed: {e!r}")
            continue
        _warned.discard("snapshot-check")
        if backup:
            log("info", f"DB snapshot: {backup}")


# The iTerm2 side: coloring sessions, the dialogs and the right-click menu items.


def local_time(utc: str) -> str:
    """DB times are UTC ('2026-10-18 01:19:05'); show them in local time."""
    try:
        when = time.strptime(utc, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(utc)
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(calendar.timegm(when)))


def split_font(normal_font: str | None) -> tuple[str | None, float]:
    """'MesloLGSNFM-Regular 13' -> ('MesloLGSNFM-Regular', 13); size falls back to 13."""
    name, _, size_text = (normal_font or "").rpartition(" ")
    try:
        size = float(size_text)
    except ValueError:
        return (normal_font or None), DEFAULT_SIZE
    return name or None, int(size) if size.is_integer() else size


async def get_preset(connection: iterm2.Connection, name: str) -> iterm2.ColorPreset | None:
    try:
        return await iterm2.ColorPreset.async_get(connection, name)
    except PRESET_ERRORS:  # the module raises for a missing preset despite its docstring
        return None


async def set_variables(session: iterm2.Session, **values: Any) -> None:
    """Set user.<name> for each value in one round trip (session.async_set_variable takes
    one per variable; this is the call it makes, with all of them)."""
    sets = [(f"user.{name}", json.dumps(value)) for name, value in values.items()]
    result = await iterm2.rpc.async_variable(session.connection, session.session_id, sets, [])
    status = result.variable_response.status
    if status != api_pb2.VariableResponse.Status.Value("OK"):
        raise iterm2.RPCException(api_pb2.VariableResponse.Status.Name(status))


async def keep_favorite_profile(
    store: Store, session: iterm2.Session, profile: iterm2.Profile, theme_mode: str
) -> tuple[Launch, int | None] | None:
    """A session opened from a favorite's profile keeps its look: (its launch row to record,
    colors adjusted or None if not known yet), or None if this isn't one."""
    guid = profile.original_guid or profile.guid
    fav = await store.call(favorite_by_guid, guid) if guid else None
    if not fav:
        return None
    preset, font, stars, preset_mode = fav
    # The preset's own mode, not the current theme: a dark favorite can be opened in light
    # mode. The theme's only if the preset is gone from the DB (not rebuilt since).
    mode = preset_mode or theme_mode
    # What its profile file holds (none if the preset changed since the build). Unknown until
    # this run has read or written the file (startup colors open tabs first), since a file from
    # an earlier run may not match the DB; then the tab and its rating dialog don't say.
    adjusted = _profile_adjusted.get(preset)
    _, size = split_font(profile.normal_font)
    _touched.add(session.session_id)
    # Not name_the_look: nothing was sent, so the tab keeps its profile's own look if this fails.
    await set_variables(
        session,
        color_preset=preset,
        font=font,
        font_size=size,
        mode=mode,
        is_favorite=1,  # 1/0, not True/False: the menu's visible_when compares with 1
        **({} if adjusted is None else {"adjusted": adjusted}),
    )
    launch = Launch(preset, font, size, mode, "favorite", stars, profile.name, session.session_id)
    return launch, adjusted


# Coloring a session: at most one at a time per session, and never forever. The iterm2 module
# sends a request before it listens for the reply, so a reply can in rare cases be lost and
# its call never return; the timeout frees the session (and its slot) instead. Its DB calls
# are reads, which WAL never makes wait on a writer, but each can queue behind another call
# on Store.lock (a write waits up to 5 s on a busy DB); each coloring logs its time.
COLOR_TIMEOUT_S = 15
# Tabs colored at once, per queue (the tabs open at startup, and new tabs): with one RPC per
# look, 2 colored 32 tabs in 1.5 s against 2.5 s one at a time (measured 2026-11-06), and
# while one waits on a lost reply the queue goes on through the other slot.
COLOR_SLOTS = 2
MISSING_PICKS = 3  # picks in a row of presets no longer installed before giving up
_coloring: dict[str, asyncio.Task[str | None]] = {}
_touched: set[str] = set()  # sessions whose coloring has started changing them
_picked: dict[str, str] = {}  # session -> discovery preset picked, until its row is recorded
_ended: set[str] = set()  # sessions closed while being colored: the coloring records the end


class LaunchTiming(NamedTuple):
    """A launches row's timing fields, as its Console line shows them."""

    coloring_ms: int
    queued_ms: int | None
    queue: Queue


class Timing(NamedTuple):
    """One coloring's clock, started by color_one_at_a_time before its first call to iTerm2."""

    started: float  # time.monotonic()
    queue: Queue
    queued_s: float | None = None  # its wait for a slot; None: it took none (a re-roll)

    def spent_s(self) -> float:
        """The coloring so far, less the slot wait: what the time limit and the row count."""
        return time.monotonic() - self.started - (self.queued_s or 0.0)

    def done(self) -> LaunchTiming:
        """The row's timing fields, now."""
        queued = None if self.queued_s is None else round(self.queued_s * 1000)
        return LaunchTiming(round(self.spent_s() * 1000), queued, self.queue)


_timing: contextvars.ContextVar[Timing | None] = contextvars.ContextVar("timing", default=None)


@contextlib.asynccontextmanager
async def slot(slots: asyncio.Semaphore | None, timing: Timing) -> AsyncIterator[None]:
    """Hold one of slots, if given. The wait goes into this coloring's context (_timing), not
    into timing, which stays as passed; with no slots it stays None (a re-roll takes none)."""
    if slots is None:
        yield
        return
    queued = time.monotonic()
    async with slots:
        _timing.set(timing._replace(queued_s=time.monotonic() - queued))
        yield


# The reason color_one_at_a_time gives for a tab given up after its look was sent.
UNRECORDED = "it took too long, and may show a new look that wasn't recorded"
# The Console's version, for a coloring stopped after its look was sent.
UNRECORDED_WHAT = (
    "the tab may show a new look that wasn't recorded; right-click > Change to another profile"
)


class LeftAsIs(Exception):
    """color_session couldn't give the tab a look (already logged); the reason, as a clause
    for the Change alert."""


def took(timing: LaunchTiming) -> str:
    """' (84 ms)', or ' (84 ms, 312 ms queued)' after a wait of 10 ms or more for a slot: the
    numbers the launches row keeps."""
    queued = timing.queued_ms
    wait = f", {queued} ms queued" if queued is not None and queued >= 10 else ""
    return f" ({timing.coloring_ms} ms{wait})"


def skips_separate_colors(profile: iterm2.Profile, reroll: bool) -> bool:
    """True if the profile draws its own light/dark colors (a preset wouldn't show); says so
    once per profile (again if it's turned back on later), every time on a re-roll."""
    key = f"separate-{profile.name}"
    if not profile.use_separate_colors_for_light_and_dark_mode:
        _warned.discard(key)
        return False
    why = (
        f'profile {profile.name!r} has "Use separate colors for light and dark mode" on '
        "(Settings > Profiles > Colors), so its tabs are left as is; turn it off to have "
        "them colored (see Troubleshooting in the README)"
    )
    warn(key, why, every=reroll)
    return True


async def color_session(
    connection: iterm2.Connection,
    app: iterm2.App,
    store: Store,
    session: iterm2.Session,
    reroll: bool = False,
) -> Launch:
    """Give the session a look; the launch row to record. Raises LeftAsIs (already logged)
    when it can't have one.

    reroll: replace the session's look (the menu's Change), even a favorite profile's. The row
    is recorded by the caller, after the time limit, so a busy DB can't run the coloring out.
    """
    # Started by color_one_at_a_time, before its pre-check; here when called on its own.
    timing = _timing.get() or Timing(time.monotonic(), "reroll" if reroll else "new")
    # A new tab's caller has checked it has no look yet (color_one_at_a_time, before taking a
    # slot); a re-roll reads the look it replaces.
    current = await session.async_get_variable("user.color_preset") if reroll else None
    theme, profile = await asyncio.gather(app.async_get_theme(), session.async_get_profile())
    mode = "dark" if "dark" in theme else "light"
    if not reroll:
        # Before the separate-colors check: a favorite's own profile turns that setting off,
        # but until startup rewrites the file it may inherit it from its parent profile.
        kept = await keep_favorite_profile(store, session, profile, mode)
        if kept:
            launch, adjusted = kept
            done = timing.done()  # after its set_variables, as below
            # Its profile file already holds the adjusted colors (write_favorite_profiles).
            note = f", {colors_adjusted(adjusted)}" if adjusted else ""
            log(
                "info",
                f"{launch.mode} favorite profile {launch.stars} ★: {launch.preset} | "
                f"{launch.font} {launch.size}{note}{took(done)}",
            )
            return launch._replace(**done._asdict())
    if skips_separate_colors(profile, reroll):
        raise LeftAsIs(
            f'its profile "{profile.name}" has "Use separate colors for light and dark mode" '
            "on (Settings > Profiles > Colors)"
        )
    exclude: set[str] = {current} if current else set()  # a re-roll never gives the same back
    sid = session.session_id

    def pick_unlike_others(db: sqlite3.Connection) -> Look | None:
        # A tab colored alongside this one isn't given the same discovery preset: neither has
        # its launches row yet, which the recent rule reads. Read and noted under Store.lock,
        # so two picks can't miss each other.
        # A copy (the loop thread may drop an entry meanwhile); only live colorings count, not
        # an entry a pick wrote after its coloring was given up.
        others = {p for s, p in list(_picked.items()) if s != sid and s in _coloring}
        look = pick_look(db, mode, exclude | others)
        if look is None and others:  # only the other tabs' presets left: repeat one
            look = pick_look(db, mode, exclude)
        if look and look.source == "discovery":
            _picked[sid] = look.preset  # until its row is recorded
        return look

    for _ in range(MISSING_PICKS):
        # A DB failure raises DbUnavailable, which color_one_at_a_time handles.
        look = await store.call(pick_unlike_others)
        if look is None:
            if reroll:
                why = f"nothing left to pick: no {mode} presets; this tab keeps its look"
            else:
                why = f"no {mode} presets to pick from; sessions left as is"
            warn(f"empty-{mode}", why, every=reroll)
            raise LeftAsIs(f"no other {mode} preset is left to pick")
        preset = await get_preset(connection, look.preset)
        if preset:
            break
        log("warn", f"preset not found: {look.preset} (removed from iTerm2? rebuild the DB)")
        exclude.add(look.preset)
    else:
        warn_once(
            "presets-missing",
            f"{MISSING_PICKS} picks in a row weren't installed presets, so this session was "
            f"left as is; rebuild the DB ({REBUILD}) after adding or "
            "removing presets",
        )
        raise LeftAsIs("the presets it picked aren't installed in iTerm2 (rebuild the database)")

    adjusted = adjust_preset(look.preset, preset, look.fixes_json, look.built_hash)
    _, size = split_font(profile.normal_font)
    _touched.add(session.session_id)
    # One RPC for the whole look (iTerm2 handles one call per color ~5x slower, measured
    # 2026-11-06), to the session's copy of its profile, as the per-color setters write.
    await session.async_set_profile_properties(
        look_properties(preset, look.font, look.nonascii, size)
    )

    label = f"favorite {look.stars} ★" if look.source == "favorite" else "discovery"
    if look.font_off:
        label += f" (saved font off; {'random font' if look.font else 'profile font'})"
    note = f", {colors_adjusted(adjusted)}" if adjusted else ""
    line = f"{mode} {label}: {look.preset} | {look.font or '(profile font)'} {size}{note}"
    # After the profile writes, so a tab that closed meanwhile never names a look it didn't
    # get. font "" on a tab with no font of ours, so a re-roll doesn't keep the old one's.
    await name_the_look(
        session,
        line,
        color_preset=look.preset,
        font=look.font or "",
        font_size=size,
        mode=mode,
        is_favorite=int(look.is_favorite),  # 1/0: the menu's visible_when compares with 1
        adjusted=adjusted,  # for the rating dialog
    )
    # The tab is done: its look and variables are set.
    log("info", f"{line}{took(done := timing.done())}")
    return Launch(
        look.preset,
        look.font,
        size,
        mode,
        look.source,
        look.stars,
        profile.name,
        session.session_id,
        *done,
    )


async def name_the_look(session: iterm2.Session, line: str, **values: Any) -> None:
    """set_variables for a look just sent; if that fails, times out or is cancelled, the
    Console still says which look the tab may show (line: its Console line, untimed)."""
    try:
        await set_variables(session, **values)
    except BaseException:
        log("warn", f"{line}: sent, but the tab's variables weren't set")
        raise


def look_properties(
    preset: iterm2.ColorPreset, font: str | None, nonascii: str | None, size: float
) -> iterm2.LocalWriteOnlyProfile:
    """A look's profile writes, for one async_set_profile_properties call: each of the
    preset's colors as async_set_color_preset sends it (adjusted, alpha and color space
    kept), then the font, then the icon font before the switch that turns it on."""
    props = iterm2.LocalWriteOnlyProfile()
    for v in preset.values:
        color = iterm2.Color(v.red, v.green, v.blue, v.alpha, v.color_space)
        props.values[v.key] = json.dumps(color.get_dict())
    if font:
        props.set_normal_font(f"{font} {size}")
    if nonascii:
        props.set_non_ascii_font(f"{nonascii} {size}")
        props.set_use_non_ascii_font(True)
    return props


def colors_adjusted(n: int) -> str:
    return "1 color adjusted" if n == 1 else f"{n} colors adjusted"


async def color_one_at_a_time(  # noqa: PLR0913  (the four every coloring takes, then reroll, slots, queue)
    connection: iterm2.Connection,
    app: iterm2.App,
    store: Store,
    session: iterm2.Session,
    *,
    reroll: bool = False,
    slots: asyncio.Semaphore | None = None,
    queue: Literal["startup", "new"] = "new",
) -> str | None:
    """color_session, unless this session is already being colored (a re-roll waits for that
    first) or, for a new one, already has a look; in one of `slots` if given; given up after
    COLOR_TIMEOUT_S; then record the launch it returns, and the session's end if it closed
    meanwhile. Why the tab got no new look (a clause for the alerts), or None. queue: 'startup'
    or 'new', for the launches row and its timing (a re-roll's is 'reroll')."""
    sid = session.session_id
    running = _coloring.get(sid)
    if running and not running.done() and not reroll:
        return None  # startup and the new-session watcher both saw it
    # A re-roll waits for whatever is coloring the session now, even another re-roll that
    # started while it waited, so two never run at once. Shielded: this wait being cancelled
    # mustn't cancel the other's coloring.
    while (running := _coloring.get(sid)) and not running.done():
        with contextlib.suppress(Exception):  # its own caller logs how it went
            await asyncio.shield(running)

    async def color_then_record() -> str | None:
        limit = COLOR_TIMEOUT_S  # read now, so a test can shorten it; a slot wait isn't counted
        _touched.discard(sid)
        # The clock starts before the pre-check: the row's coloring_ms is the whole tab, less
        # the slot wait, which it keeps apart. This task's own context, so tabs don't mix.
        timing = Timing(time.monotonic(), "reroll" if reroll else queue)
        _timing.set(timing)  # this task's context; slot() adds the wait to it
        try:
            try:
                # Already has a look (a restored window, or one startup colored while it was
                # also queued as new): no slot, and no second launch.
                if not reroll and await asyncio.wait_for(
                    session.async_get_variable("user.color_preset"), limit
                ):
                    return None
                async with slot(slots, timing):
                    # What's left of the limit after the pre-check, so the row's coloring_ms
                    # never passes it (the slot wait counts against neither).
                    launch = await asyncio.wait_for(
                        color_session(connection, app, store, session, reroll),
                        limit - (_timing.get() or timing).spent_s(),
                    )
            except TimeoutError:
                # Not recorded either way: a tab given up after its look was sent may show it,
                # without the user.* variables, until it's re-rolled.
                if sid in _touched:
                    what, why = UNRECORDED_WHAT, UNRECORDED
                else:
                    what = "nothing was changed (the DB or iTerm2 was busy); the tab keeps its look"
                    why = "it took too long (the database or iTerm2 was busy)"
                log("warn", f"coloring session {sid} took over {limit} s and was given up; {what}")
                return why
            except DbUnavailable:  # store.call logged why
                log("warn", "couldn't read the DB; session left as is")
                return "the database couldn't be read"
            except LeftAsIs as e:  # logged where it was raised
                return str(e)
            except asyncio.CancelledError:  # its caller was cancelled, e.g. a stale menu action
                if sid in _touched:
                    log("warn", f"coloring session {sid} was cancelled; {UNRECORDED_WHAT}")
                raise
            finally:
                _touched.discard(sid)
            # Outside the time limit and the slot (a locked DB delays this row, and every DB
            # call queued behind it, up to 5 s), but inside this session's own coloring, so a
            # re-roll's row can't land first.
            with contextlib.suppress(DbUnavailable):  # store.call logged it
                await store.call(record_change if reroll else record_launch, launch)
            return None
        finally:
            _picked.pop(sid, None)
            # Closed meanwhile: watch_ended left the end to this, after the row. Nothing awaits
            # between this check and the task finishing, so watch_ended can't miss it.
            if sid in _ended:
                _ended.discard(sid)
                await session_ended(store, sid)

    task = asyncio.ensure_future(color_then_record())
    _coloring[sid] = task
    try:
        return await task
    finally:
        if _coloring.get(sid) is task:
            del _coloring[sid]


# How often the favorites' profiles are checked for changes made outside the dialogs (edits
# in sqlite3, an export restored, a rebuild). Off the tab path: a rewrite reads all of
# iTerm2's settings (0.5-0.75 s with iTerm2 idle, measured 2026-11-06; up to 1.3 s under load
# in the 2026-11-06 review; up to 30 s if stuck).
SYNC_EVERY_S = 30


async def sync_periodically(
    store: Store,
    every_s: float = SYNC_EVERY_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        await sleep(every_s)
        try:
            await sync_and_log(store)  # logs the failures it expects
        except Exception as e:  # anything else must not stop the script, or log every 30 s
            warn_once("sync-check", f"favorite profiles check failed: {e!r}")
            continue
        _warned.discard("sync-check")  # so a later, different failure is reported too


async def sync_and_log(store: Store) -> int | None:
    """Rewrite the favorites' profiles if what they're made of changed; the count, or None."""
    async with store.sync_lock:  # the read and the write, so two syncs can't interleave
        try:
            snap = await store.call(read_favorites)
        except DbUnavailable:  # logged; nothing to sync from
            return None
        if snap.state == _profiles_state[0] and os.path.exists(DYNAMIC_PATH):
            return None  # current (a file deleted by hand is written again)
        failed_state, failed_at = _profiles_failed
        if snap.state == failed_state and time.monotonic() - failed_at < SYNC_RETRY_S:
            return None  # the same write failed a moment ago: don't hammer defaults or the log
        try:
            count = await asyncio.to_thread(write_favorite_profiles, snap)
        except Exception as e:  # e.g. defaults export failed: profiles are optional, coloring isn't
            if snap.state != failed_state:
                retry = f"tried again every {SYNC_RETRY_S // 60} minutes"
                log("warn", f"favorite profiles not updated ({retry}): {e!r}")
            _profiles_failed[:] = [snap.state, time.monotonic()]
            return None
        _profiles_state[0] = snap.state  # only once written, so a failed write is retried
        _profiles_failed[:] = [None, 0.0]
    if count is not None:
        log("info", f"favorite profiles: {count} in {DYNAMIC_PATH}")
    return count


# How long favorites_changed spends on the open tabs' marks before giving up on the rest (a
# lost iTerm2 reply would otherwise hold the dialog's task, and its re-roll, forever).
FAVORITES_MARK_TIMEOUT_S = 15


async def favorites_changed(app: iterm2.App, store: Store, preset: str) -> None:
    """Update user.is_favorite on open sessions showing preset, and the profiles file."""

    db_failed = False

    async def update(session: iterm2.Session) -> None:
        nonlocal db_failed
        try:
            if db_failed:
                return
            font = await session.async_get_variable("user.font")
            fav = await store.call(is_favorite, preset, font) if font else False
            await session.async_set_variable("user.is_favorite", int(fav))
        except DbUnavailable:  # logged: leave the marks alone, and don't wait on the DB per tab
            db_failed = True
        except iterm2.RPCException:
            pass  # closed while we were looking
        except Exception as e:  # one tab must not stop the others, or the sync below
            log("warn", f"favorite mark not updated on session {session.session_id}: {e!r}")

    sessions = [s for w in app.windows for t in w.tabs for s in t.all_sessions]
    sessions += app.buried_sessions  # Session > Bury Session
    try:
        async with asyncio.timeout(FAVORITES_MARK_TIMEOUT_S):
            # Every tab's preset at once (an error, e.g. a tab closed meanwhile, never
            # matches); then the tabs showing it, one at a time, as each makes a DB call.
            shown = await asyncio.gather(
                *(s.async_get_variable("user.color_preset") for s in sessions),
                return_exceptions=True,
            )
            for session, name in zip(sessions, shown, strict=True):
                if name == preset:
                    await update(session)
    except TimeoutError:
        log(
            "warn",
            f"favorite marks not all updated within {FAVORITES_MARK_TIMEOUT_S} s "
            "(iTerm2 or the DB was busy); some tabs' Remove item may be out of date",
        )
    # In the background: a rewrite reads all of iTerm2's settings, and the dialog's re-roll
    # shouldn't wait for it. sync_lock keeps syncs from overlapping.
    spawn("favorite profiles sync", sync_and_log(store))


_background: set[asyncio.Task[Any]] = set()


def spawn(what: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Run coro as a task, kept referenced until done, and log it if it fails (with the
    traceback, unless it's expected: an OSError (a file or a command) or an RPCException (the
    tab closed meanwhile), said in one line; DbUnavailable too, should one ever get here)."""
    task = asyncio.create_task(coro)
    _background.add(task)

    def done(t: asyncio.Task[Any]) -> None:
        _background.discard(t)
        e = None if t.cancelled() else t.exception()
        if e is None:
            return
        log("warn", f"{what} failed: {e!r}")
        if not isinstance(e, (OSError, iterm2.RPCException, DbUnavailable)):
            print("".join(traceback.format_exception(e)), end="", flush=True)

    task.add_done_callback(done)
    return task


async def apply_choice(
    app: iterm2.App, store: Store, choice: str, look: tuple[str, str, float]
) -> None:
    """look: (preset, font, size) of the session the choice was made in. Raises DbUnavailable
    if the write didn't take (menu_action tells the user)."""
    preset, font, size = look
    result = False
    if choice in STAR_BUTTONS:
        rating = STAR_BUTTONS.index(choice) + 1
        await store.call(save_favorite, preset, font, size, rating)
        log("info", f"saved favorite {rating} ★: {preset} | {font} {size}")
        result = True
    elif choice == REMOVE:
        if result := await store.call(remove_favorite, preset, font):
            log("info", f"removed favorite: {preset} | {font} {size}")
        else:
            log("info", f"not a favorite, nothing removed: {preset} | {font} {size}")
    elif choice == NEVER and (result := await store.call(never_show, preset)):
        log("info", f"never show: {preset}")
    if result:
        await favorites_changed(app, store, preset)


# The dialogs' titles, which are also the right-click items' names (save: also ⌃⌥⌘S).
SAVE_TITLE = "Save/update this profile"
REMOVE_TITLE = "Remove profile from favorites"
CHANGE_TITLE = "Change to another profile"
NEVER_TITLE = "Never show this profile"


async def save_dialog_text(
    store: Store,
    session: iterm2.Session,
    look: tuple[str, str, float],
    info: tuple[tuple[int, str, int] | None, int | None],
) -> tuple[str, int | None]:
    """(the rating dialog's text, current stars) for look = (preset, font, size)."""
    preset, font, size = look
    stats, stars = info
    mode = await session.async_get_variable("user.mode")
    try:
        label = await store.call(font_label, font)
    except DbUnavailable:  # only the label: the dialog can still show
        label = font
    lines = [f"{preset} ({mode})", f"{label} {size}"]
    if stats:
        times = "1 time" if stats[0] == 1 else f"{stats[0]} times"
        changed = f", changed away from {stats[2]}" if stats[2] else ""
        lines.append(f"Launched {times}, last {local_time(stats[1])}{changed}.")
    else:
        lines.append("Not launched before.")
    if stars:
        lines.append(f"Saved now with {STAR_BUTTONS[stars - 1]}.")
    adjusted = await session.async_get_variable("user.adjusted")
    if adjusted:
        lines.append(f"{colors_adjusted(int(adjusted))}.")
    return "\n".join(lines), stars


async def save_dialog(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session_id: str
) -> None:
    session = app.get_session_by_id(session_id)
    if not session:
        log("warn", f"save: session {session_id} not found")
        return
    window, _ = app.get_window_and_tab_for_session(session)
    window_id = window.window_id if window else None
    preset = await session.async_get_variable("user.color_preset")
    if not preset:
        await iterm2.Alert(SAVE_TITLE, not_colored("there's nothing to save"), window_id).async_run(
            connection
        )
        return
    # The font the script applied. Reading it back from the session gave the wrong face
    # once (Monaco launched, Menlo-Regular read back; 2026-10-17), so that's the fallback.
    profile = await session.async_get_profile()
    live_font, size = split_font(profile.normal_font)
    applied_font = await session.async_get_variable("user.font")
    font = applied_font or live_font
    if not font:
        log("warn", f"save: no font known for {preset}")
        return
    info = await store.call(preset_info, preset, font)
    text, stars = await save_dialog_text(store, session, (preset, font, size), info)
    alert = iterm2.Alert(SAVE_TITLE, text, window_id)
    # Remove favorite only appears when this (preset, font) is a favorite. Cancel goes first so
    # Return can't save 1 star: NSAlert gives Return to the first button and Escape to one
    # titled Cancel, so here Escape cancels and Return only beeps (checked in iTerm2 3.7.6).
    buttons = [CANCEL, *STAR_BUTTONS, *([REMOVE] if stars else []), NEVER]
    choice = await alert_choice(connection, alert, buttons)
    # Never show on a favorite asks first, as the right-click item does.
    go = choice != CANCEL and (
        choice != NEVER or await confirm_never_show(connection, store, preset, window_id)
    )
    if not go:
        return
    if not applied_font:
        # The save uses the profile's font; record it as this tab's, or favorites_changed would
        # find no user.font here and mark the tab as not a favorite.
        await session.async_set_variable("user.font", font)
    await apply_choice(app, store, choice, (preset, font, size))
    if choice == NEVER:  # hidden now (or already was): give the tab another look, as the menu does
        await reroll_after_hide(connection, app, store, session)


def not_colored(consequence: str) -> str:
    """Text for a menu item used on a tab this script didn't color."""
    return f"This tab wasn't colored by srcp_set_random_color_profile.py, so {consequence}."


async def db_failed_alert(connection: iterm2.Connection, title: str, window_id: str | None) -> None:
    """Tell the user a dialog action was lost to a DB failure (DbUnavailable)."""
    await iterm2.Alert(
        title,
        "That didn't take: the database was busy or missing. Try again in a moment; "
        "Scripts > Manage > Console has the details.",
        window_id,
    ).async_run(connection)


async def remove_from_menu(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session_id: str
) -> None:
    session = app.get_session_by_id(session_id)
    if not session:
        return
    preset = await session.async_get_variable("user.color_preset")
    font = await session.async_get_variable("user.font")
    size = await session.async_get_variable("user.font_size") or DEFAULT_SIZE
    window, _ = app.get_window_and_tab_for_session(session)
    window_id = window.window_id if window else None
    # A DB failure raises, which isn't the same as "not a favorite": menu_action says so.
    if preset and font and await store.call(is_favorite, preset, font):
        await apply_choice(app, store, REMOVE, (preset, font, size))
        return
    # iTerm2 before API protocol 1.23 shows the item on every tab, so say why nothing happens.
    if preset and font:
        try:
            label = await store.call(font_label, font)
        except DbUnavailable:  # only the label
            label = font
        text = f"{preset} with {label} isn't a favorite, so there's nothing to remove."
    elif preset:  # colored, but no font of ours was set (none enabled), so none was saved
        text = f"This tab's {preset} has no font of its own, so it isn't a favorite."
    else:
        text = not_colored("it has no favorite")
    await iterm2.Alert(REMOVE_TITLE, text, window_id).async_run(connection)


async def change_from_menu(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session_id: str
) -> None:
    session = app.get_session_by_id(session_id)
    if not session:
        log("warn", f"change: session {session_id} not found")
        return
    before = await session.async_get_variable("user.color_preset")
    # Not in a coloring slot: asked for by hand, it shouldn't queue behind new tabs.
    why = await color_one_at_a_time(connection, app, store, session, reroll=True)
    if await session.async_get_variable("user.color_preset") == before:
        window, _ = app.get_window_and_tab_for_session(session)
        text = kept_look_text(why, "This tab")
        await iterm2.Alert(CHANGE_TITLE, text, window.window_id if window else None).async_run(
            connection
        )


def kept_look_text(why: str | None, subject: str) -> str:
    """The alert for a re-roll that left the tab's look as it was; why from
    color_one_at_a_time."""
    console = "Scripts > Manage > Console has the details."
    if why == UNRECORDED:  # it may have switched after all
        return (
            f"{subject}'s new look may not have been recorded: it took too long. Right-click > "
            f"Change to another profile to get one that is. {console}"
        )
    if why:
        return f"{subject} couldn't switch to another look: {why}. {console}"
    return f"{subject} couldn't switch to another look, so it keeps this one. {console}"


async def alert_choice(
    connection: iterm2.Connection, alert: iterm2.Alert, buttons: list[str]
) -> str:
    """Show alert with these buttons (added in order); the one clicked, or CANCEL."""
    for b in buttons:
        alert.add_button(b)
    index = await alert.async_run(connection) - 1000  # NSAlert numbers the first button 1000
    return buttons[index] if 0 <= index < len(buttons) else CANCEL


async def confirm_never_show(
    connection: iterm2.Connection, store: Store, preset: str, window_id: str | None
) -> bool:
    """If preset is a favorite, ask before Never show drops it: True to go ahead, False if
    cancelled. True straight away for a preset with no favorites."""
    favs = await store.call(favorite_fonts, preset)
    if not favs:
        return True
    lines = [f"{preset} is a favorite:"]
    lines += [f"  {label} {STAR_BUTTONS[stars - 1]}" for label, stars in favs]
    lines.append("Never show removes it from your favorites and from Profiles > Favorites.")
    alert = iterm2.Alert(NEVER_TITLE, "\n".join(lines), window_id)
    buttons = [CANCEL, NEVER]  # Cancel first: Escape cancels and Return can't hide it
    return await alert_choice(connection, alert, buttons) == NEVER


async def reroll_after_hide(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session: iterm2.Session
) -> None:
    """Give a tab whose preset was just hidden another look, or say why it can't have one."""
    preset = await session.async_get_variable("user.color_preset")
    window, _ = app.get_window_and_tab_for_session(session)
    window_id = window.window_id if window else None
    try:
        why = await color_one_at_a_time(connection, app, store, session, reroll=True)
        new = await session.async_get_variable("user.color_preset")
    except iterm2.RPCException as e:  # closed while the question was up: it's hidden anyway
        log("info", f"never show: {preset} hidden; its tab closed before the new look ({e})")
        return
    if new == preset:  # color_one_at_a_time says why (and logged it)
        text = kept_look_text(why, f"{preset} is hidden, but this tab")
        await iterm2.Alert(NEVER_TITLE, text, window_id).async_run(connection)


async def never_show_from_menu(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session_id: str
) -> None:
    """Hide the tab's preset (asking first if it's a favorite), then give the tab a new look."""
    session = app.get_session_by_id(session_id)
    if not session:
        log("warn", f"never show: session {session_id} not found")
        return
    window, _ = app.get_window_and_tab_for_session(session)
    window_id = window.window_id if window else None
    preset = await session.async_get_variable("user.color_preset")
    if not preset:
        await iterm2.Alert(
            NEVER_TITLE, not_colored("there's nothing to hide"), window_id
        ).async_run(connection)
        return
    if not await confirm_never_show(connection, store, preset, window_id):
        return
    # Not apply_choice: that needs the tab's font, which a tab without one doesn't have. A DB
    # failure raises: nothing hidden, so the tab keeps its look rather than re-roll.
    hidden = await store.call(never_show, preset)
    if hidden:
        log("info", f"never show: {preset}")
        await favorites_changed(app, store, preset)
    else:
        log("info", f"never show: {preset} was already hidden")
    await reroll_after_hide(connection, app, store, session)


async def menu_action(
    connection: iterm2.Connection,
    app: iterm2.App,
    title: str,
    session_id: str,
    coro: Coroutine[Any, Any, None],
) -> None:
    """Run a dialog or menu item; if the DB failed it (DbUnavailable), tell the user."""
    try:
        await coro
    except DbUnavailable:
        session = app.get_session_by_id(session_id)
        window = app.get_window_and_tab_for_session(session)[0] if session else None
        await db_failed_alert(connection, title, window.window_id if window else None)


# An action still running after this long is taken as lost: a dialog's reply can go missing
# (Alert.async_run has no time limit), and the tab's menu would stay dead until a restart.
ACTION_STALE_S = 600


class SessionActions:
    """The dialogs and menu actions running, one per session at a time so they can't race."""

    def __init__(self, connection: iterm2.Connection, app: iterm2.App) -> None:
        self.connection, self.app = connection, app
        self.running: dict[str, tuple[asyncio.Task[None], float]] = {}  # task, when started

    def start(
        self, action: str, title: str, session_id: str, coro: Coroutine[Any, Any, None]
    ) -> None:
        """Run coro through menu_action as a task (outside the RPC, so iTerm2's RPC timeout
        can't cut a dialog off), unless another runs on this session: then it's ignored, or,
        if that one is ACTION_STALE_S old, it's cancelled and this runs."""
        held = self.running.get(session_id)
        if held and not held[0].done():  # done, but its callback hasn't run yet: free
            age = time.monotonic() - held[1]
            if age < ACTION_STALE_S:
                coro.close()  # never run: no "never awaited" warning
                log("info", f"{action}: another action is still running on this session; ignored")
                return
            held[0].cancel()
            log(
                "warn",
                f"{action}: an action started {age / 60:.0f} min ago on this session never "
                "finished (a lost iTerm2 reply?); cancelled it",
            )
        task = spawn(action, menu_action(self.connection, self.app, title, session_id, coro))
        self.running[session_id] = (task, time.monotonic())

        def done(t: asyncio.Task[None]) -> None:
            # Only its own entry: a cancelled stale task finishes after its replacement starts.
            if (entry := self.running.get(session_id)) and entry[0] is t:
                del self.running[session_id]

        task.add_done_callback(done)


async def register_save_handlers(
    connection: iterm2.Connection, app: iterm2.App, store: Store
) -> None:
    """Save (right-click and save_profile() key), and the right-click Remove profile from favorites,
    Change to another profile and Never show this profile."""
    start = SessionActions(connection, app).start

    def start_dialog(session_id: str) -> None:
        start("save", SAVE_TITLE, session_id, save_dialog(connection, app, store, session_id))

    @iterm2.RPC
    async def save_profile(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        start_dialog(session_id)

    @iterm2.ContextMenuProviderRPC
    async def save_profile_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        start_dialog(session_id)

    @iterm2.ContextMenuProviderRPC
    async def remove_favorite_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        coro = remove_from_menu(connection, app, store, session_id)
        start("remove", REMOVE_TITLE, session_id, coro)

    @iterm2.ContextMenuProviderRPC
    async def change_profile_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        coro = change_from_menu(connection, app, store, session_id)
        start("change", CHANGE_TITLE, session_id, coro)

    @iterm2.ContextMenuProviderRPC
    async def never_show_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        coro = never_show_from_menu(connection, app, store, session_id)
        start("never", NEVER_TITLE, session_id, coro)

    await save_profile.async_register(connection)
    await save_profile_menu.async_register(connection, SAVE_TITLE, MENU_ID)
    await change_profile_menu.async_register(connection, CHANGE_TITLE, CHANGE_MENU_ID)
    await never_show_menu.async_register(connection, NEVER_TITLE, NEVER_MENU_ID)
    # Hide the item on non-favorites where iTerm2 can (API protocol 1.23+, newer than 3.7.6);
    # elsewhere it shows on every tab and remove_from_menu explains. Passing visible_when to
    # an older iTerm2 only adds a warning to the Console.
    hide = supports_context_menu_submenus_and_visibility(connection)
    await remove_favorite_menu.async_register(
        connection,
        REMOVE_TITLE,
        REMOVE_MENU_ID,
        visible_when="user.is_favorite == 1" if hide else None,
    )


async def main(connection: iterm2.Connection) -> None:
    modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(__file__)))
    log(
        "info",
        f"srcp_set_random_color_profile {VERSION} (file modified {modified}), pid {os.getpid()}",
    )
    lock = await acquire_lock()
    if lock is None:
        log("info", "another copy is still running after 5 s; exiting")
        sys.exit(0)  # returning would leave run_forever's process alive
    app = await iterm2.async_get_app(connection)
    if app is None:
        log("warn", "couldn't get the iTerm2 app object; exiting")
        sys.exit(1)
    store = Store(DB_PATH)
    started = now()
    async with contextlib.AsyncExitStack() as stack:
        # Subscribe first: sessions opened during the startup work below queue up, not get lost.
        new_sessions = await stack.enter_async_context(iterm2.NewSessionMonitor(connection))
        ended_sessions = await stack.enter_async_context(
            iterm2.SessionTerminationMonitor(connection)
        )
        await register_save_handlers(connection, app, store)
        # startup with the watchers, not before them: a tab opened while it reads the
        # settings or colors the windows already open isn't held up behind it.
        await asyncio.gather(
            startup(connection, app, store, started),
            watch_new(connection, app, store, new_sessions),
            watch_ended(store, ended_sessions),
            snapshot_periodically(store),
            sync_periodically(store),
            exit_on_disconnect(connection, store),
        )


async def startup(
    connection: iterm2.Connection, app: iterm2.App, store: Store, started: str
) -> None:
    open_sessions = [s for w in app.windows for t in w.tabs for s in t.all_sessions]
    open_sessions += app.buried_sessions  # Session > Bury Session
    live = {s.session_id for s in open_sessions}
    # No usable DB (DbUnavailable, logged) skips these; tabs are left as is until there is one.
    with contextlib.suppress(DbUnavailable):
        ended = await store.call(close_stale, live, started)
        if ended:
            log("info", f"{ended} launches from a previous run ended as 'unknown'")
    with contextlib.suppress(DbUnavailable):
        backup = await store.call(daily_snapshot)
        if backup:
            log("info", f"DB snapshot: {backup}")
    # Windows iTerm2 opened before this script connected (the one at launch) got no look.
    # Restored windows keep theirs: iTerm2 saves user.* variables with them. COLOR_SLOTS at a
    # time, in their own slots, so a new tab never queues behind them.
    slots = asyncio.Semaphore(COLOR_SLOTS)  # made here: it belongs to the running loop

    async def color_open(session: iterm2.Session) -> None:
        try:
            await color_one_at_a_time(connection, app, store, session, slots=slots, queue="startup")
        except Exception as e:  # one session must not stop the rest, or startup
            log("warn", f"coloring open session failed: {e!r}")

    await asyncio.gather(*(color_open(s) for s in open_sessions))
    # Last: parsing iTerm2's settings holds Python's GIL (0.2-0.25 s of a 0.5-0.75 s read with
    # iTerm2 idle, measured 2026-11-06; the read took up to 1.3 s under load in the 2026-11-06
    # review) and slows every coloring that overlaps it. With no DB it syncs nothing.
    await sync_and_log(store)


async def watch_new(
    connection: iterm2.Connection,
    app: iterm2.App,
    store: Store,
    mon: iterm2.NewSessionMonitor,
) -> None:
    slots = asyncio.Semaphore(COLOR_SLOTS)  # made here: it belongs to the running loop

    async def color_new(session: iterm2.Session) -> None:
        try:
            await color_one_at_a_time(connection, app, store, session, slots=slots, queue="new")
        except Exception as e:  # logged here, so spawn() never prints a traceback for it
            log("warn", f"coloring session failed: {e!r}")

    while True:
        session_id = await mon.async_get()
        session = app.get_session_by_id(session_id)
        if not session:
            log("warn", f"new session {session_id} not found; left as is")
            continue
        spawn("coloring", color_new(session))  # a slow tab doesn't hold up the watcher


async def watch_ended(store: Store, mon: iterm2.SessionTerminationMonitor) -> None:
    while True:
        session_id = await mon.async_get()
        coloring = _coloring.get(session_id)
        if coloring and not coloring.done():
            _ended.add(session_id)  # its coloring records the end after its row
            continue
        await session_ended(store, session_id)


async def session_ended(store: Store, session_id: str) -> None:
    try:
        await store.call(record_end, session_id)
    except DbUnavailable:  # logged; that launch stays open (startup's close_stale ends it)
        pass
    except Exception as e:
        log("warn", f"recording session end failed: {e!r}")


async def exit_on_disconnect(connection: iterm2.Connection, store: Store) -> None:
    # The monitors never return once iTerm2 quits, so without this the process outlives it
    # and holds the lock, and the copy the next iTerm2 launch starts exits.
    if connection.websocket is not None:
        await connection.websocket.wait_closed()
    log("info", "iTerm2 closed the connection; exiting")
    # Close the DB, so SQLite folds in and deletes its -wal/-shm files; not past 1 s, in case
    # a call holds it (a locked DB's 5 s wait): WAL recovers at the next open anyway.
    if store.lock.acquire(timeout=1) and store.db is not None:
        with contextlib.suppress(sqlite3.Error):
            store.db.close()
    # os._exit, not sys.exit: unwinding would unsubscribe the monitors over the closed
    # connection (the iterm2 module's monitors do so on exit). Nothing else is left: log()
    # flushes, writes are whole-file renames, and the kernel drops the flock.
    os._exit(0)


iterm2.run_forever(main)
