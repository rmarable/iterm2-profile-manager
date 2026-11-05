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
iTerm2 profile ("Kibble - Menlo"), kept in a Dynamic Profiles file.
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import fcntl
import json
import os
import plistlib
import random
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import IO, Any, NamedTuple, TypeVar

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
        SEED_CONFIG,
        SEED_FONTS,
        UNHIDE,
        colors_hash,
    )
except ImportError as e:
    print(
        f"[error] missing {TOOLS}/srcp_schema.py, srcp_db_backup.py or srcp_iterm_prefs.py "
        f"({e}); run ./setup.sh again",
        flush=True,
    )
    sys.exit(1)

VERSION = "0.10.1"  # bump on every change, so the Console shows which code is running
LOCK_PATH = os.path.join(DATA_DIR, ".lock")
# How often the running script checks whether a snapshot is due. asyncio's clock stops while
# the Mac sleeps, so check often (it's a few file stats) to keep it close to daily.
SNAPSHOT_CHECK_S = 15 * 60
DEFAULT_SIZE = 13
# The numeric settings pick() reads, with the build's seed values when config lacks one.
DEFAULT_CONFIG = {
    k: float(SEED_CONFIG[k])
    for k in ("favorite_share", "favorite_target", "recent_window", "colorful_weight")
}
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
Pick = tuple[str, str | None, str, int | None]  # preset, font (saved favorite's), source, stars

_warned: set[str] = set()


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log("warn", msg)


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

    def _connect(self) -> sqlite3.Connection | None:
        try:
            inode = os.stat(self.path).st_ino
        except FileNotFoundError:
            inode = None
        if self.db is not None and inode != self.inode:
            # Deleted or replaced by a rename (docs/REFERENCE.md's restore does cp + mv): don't keep
            # writing to the old file. A cp over the file keeps its inode and isn't noticed.
            self.db.close()
            self.db = None
        if self.db is None:
            if inode is None:
                warn_once(
                    "no-db",
                    f"no scheme DB at {self.path}; run .tools/srcp_build_scheme_db.py. "
                    "Sessions keep their profile's colors until then.",
                )
                return None
            # mode=rw: never create an empty DB.
            uri = Path(self.path).resolve().as_uri() + "?mode=rw"
            self.db = sqlite3.connect(
                uri, uri=True, timeout=5, isolation_level=None, check_same_thread=False
            )
            self.inode = inode
            _warned.discard("no-db")
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

    async def call(self, fn: Callable[..., T], *args: Any) -> T | None:
        """Run fn(db, *args) in a worker thread; None if there's no DB or it failed."""

        def run() -> T | None:
            with self.lock:
                try:
                    db = self._connect()
                    if db is None:
                        return None
                    return fn(db, *args)
                except (sqlite3.Error, OSError) as e:  # OSError: snapshot files
                    self._rollback()
                    log("warn", f"DB {fn.__name__}: {e}")
                    return None
                except BaseException:
                    self._rollback()  # never leave a BEGIN IMMEDIATE open
                    raise

        return await asyncio.to_thread(run)


# DB calls: each runs in a worker thread through Store.call, which rolls back on failure.


def read_config(db: sqlite3.Connection) -> dict[str, float]:
    cfg: dict[str, float] = dict(DEFAULT_CONFIG)
    for key, value in db.execute("SELECT key, value FROM config"):
        if key in cfg:
            try:
                cfg[key] = float(value)
            except (TypeError, ValueError):
                warn_once(
                    f"config-{key}", f"config {key}={value!r} isn't a number; using {cfg[key]}"
                )
    return cfg


def pick(db: sqlite3.Connection, mode: str, exclude: set[str]) -> Pick | tuple[()]:
    """Choose a favorite or a discovery preset for mode, or () if nothing fits.

    Not None: store.call returns None when the DB fails, and the caller tells them apart.
    """
    cfg = read_config(db)
    favs: list[tuple[str, str, int]] = [
        r
        for r in db.execute("SELECT preset, font, stars FROM favorite_pool WHERE mode = ?", (mode,))
        if r[0] not in exclude
    ]
    try:
        rows = db.execute("SELECT name, colorful FROM discovery WHERE mode = ?", (mode,)).fetchall()
    except sqlite3.OperationalError:  # DB built before colorful_weight: rebuild it to get it
        warn_once("no-colorful", "discovery has no colorful column; rebuild the DB")
        rows = [(n, 0) for (n,) in db.execute("SELECT name FROM discovery WHERE mode = ?", (mode,))]
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
        return preset, font, "favorite", stars
    if not disc:
        return ()
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
    return preset, None, "discovery", None


def look_fixes(db: sqlite3.Connection, preset: str) -> tuple[str | None, str | None]:
    """(adjustments as JSON or None, the build's color_hash) for preset."""
    try:
        row = db.execute(
            "SELECT CASE WHEN " + FIX_ON_SQL + " THEN fixes END, color_hash "
            "FROM schemes WHERE name = ?",
            (preset,),
        ).fetchone()
    except sqlite3.OperationalError:  # DB built before 0.9.0
        warn_once("no-fixes", "the DB has no color adjustments; rebuild it to get them")
        return None, None
    return (row[0], row[1]) if row else (None, None)


class Look(NamedTuple):
    """Everything color_session needs from the DB for one session, from one Store.call."""

    preset: str
    font: str | None  # the favorite's font, else a random enabled one; None if there's none
    source: str  # 'favorite' or 'discovery'
    stars: int | None
    fixes_json: str | None
    built_hash: str | None
    nonascii: str | None  # the icon font to set, if any
    is_favorite: bool  # this (preset, font) is a favorite


def pick_look(db: sqlite3.Connection, mode: str, exclude: set[str]) -> Look | tuple[()]:
    """pick(), plus its font, adjustments and icon font; () if nothing fits."""
    choice = pick(db, mode, exclude)
    if not choice:
        return ()
    preset, font, source, stars = choice
    font = font or random_font(db)
    return Look(
        preset,
        font,
        source,
        stars,
        *look_fixes(db, preset),
        nonascii_font(db),
        bool(font) and is_favorite(db, preset, font),
    )


def usable_fixes(
    preset: str,
    fixes_json: str | None,
    built_hash: str | None,
    live: dict[str, tuple[float, float, float]],
) -> dict[str, tuple[float, float, float]]:
    """{key: (r, g, b)} to apply to preset, or {} (none, or its colors changed since the build).

    live: the installed preset's own colors, before any adjustment.
    """
    if not fixes_json:
        return {}
    if colors_hash(live) != built_hash:
        warn_once(
            f"fixes-stale-{preset}",
            f"{preset} changed since the DB was built; its color adjustments are skipped "
            "until you rebuild (.tools/srcp_build_scheme_db.py)",
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
    fixes = usable_fixes(name, fixes_json, built_hash, live)
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
    fixes = usable_fixes(name, fixes_json, built_hash, live)  # pyright: ignore[reportArgumentType]  # 3-tuples by construction
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


def record_launch(db: sqlite3.Connection, launch: Launch) -> None:
    db.execute(
        "INSERT INTO launches (launched_at, preset, font, size, mode, source, stars, "
        "iterm_profile, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now(), *launch),
    )


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
    stale = [
        (i,)
        for i, sid in db.execute(
            "SELECT id, session_id FROM launches "
            "WHERE ended_at IS NULL AND end_reason IS NULL AND launched_at < ?",
            (started,),
        )
        if sid not in live_ids
    ]
    db.executemany("UPDATE launches SET end_reason = 'unknown' WHERE id = ?", stale)
    return len(stale)


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
    stars = db.execute(
        "SELECT stars FROM favorites WHERE preset = ? AND font = ?", (preset, font)
    ).fetchone()
    return stats, stars[0] if stars else None


def log_rating(
    db: sqlite3.Connection, preset: str, font: str | None, action: str, stars: int | None
) -> None:
    """Add a ratings row (history), if the DB has the table (schema version 3+)."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'ratings'").fetchone():
        warn_once("no-ratings", "no ratings table; rebuild the DB to keep rating history")
        return
    previous = None
    if font is not None:
        row = db.execute(
            "SELECT stars FROM favorites WHERE preset = ? AND font = ?", (preset, font)
        ).fetchone()
        previous = row[0] if row else None
    db.execute(
        # OR IGNORE: a second event for the same look in the same second would hit the
        # ratings_event unique index and roll back the whole save; history is best-effort.
        "INSERT OR IGNORE INTO ratings (at, preset, font, action, stars, previous_stars) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now(), preset, font, action, stars, previous),
    )


def save_favorite(db: sqlite3.Connection, preset: str, font: str, size: float, stars: int) -> bool:
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
    return True


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


def is_favorite(db: sqlite3.Connection, preset: str, font: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM favorites WHERE preset = ? AND font = ?", (preset, font)
        ).fetchone()
    )


def favorite_guid(preset: str, font: str) -> str:
    """Stable GUID of a favorite's profile, so iTerm2 keeps it across rewrites."""
    return str(uuid.uuid5(GUID_NAMESPACE, f"{preset}\0{font}")).upper()


def favorite_by_guid(
    db: sqlite3.Connection, guid: str
) -> tuple[str, str, int, str | None, int] | None:
    """(preset, font, stars, mode or None, colors adjusted) of the favorite with this GUID."""
    query = (
        "SELECT f.preset, f.font, f.stars, e.mode, {adjusted} "
        "FROM favorites f LEFT JOIN effective e ON e.name = f.preset"
    )
    try:
        rows = db.execute(
            query.format(adjusted="CASE WHEN " + FIX_ON_SQL + " THEN e.fix_count ELSE 0 END")
        ).fetchall()
    except sqlite3.OperationalError:  # DB built before 0.9.0
        rows = db.execute(query.format(adjusted="0")).fetchall()
    for preset, font, stars, mode, adjusted in rows:
        if favorite_guid(preset, font) == guid:
            return preset, font, stars, mode, adjusted or 0
    return None


def font_label(db: sqlite3.Connection, font: str) -> str:
    try:
        row = db.execute("SELECT label FROM fonts WHERE name = ?", (font,)).fetchone()
    except sqlite3.OperationalError:  # DB built before fonts.label existed
        row = None
    return row[0] if row and row[0] else font.removesuffix("-Regular")


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
_profile_adjusted: dict[str, int] = {}  # preset -> colors adjusted in its favorite profiles


def favorites_state(db: sqlite3.Connection, fallback: str | None) -> str:
    """A fingerprint of what the profiles are made of: favorites, Never show, fonts, and the
    favorites' adjustments and color hashes (a rebuild that changes either rewrites them).

    Cheap, so edits made in sqlite3 or by restoring an export show up without a restart.
    """
    fonts = "name || ':' || enabled || ':' || ifnull(label, '')"
    query = (
        "SELECT (SELECT group_concat(preset || ':' || font || ':' || stars || ':' || "
        "ifnull(size, ''), ';') FROM (SELECT * FROM favorites ORDER BY preset, font)), "
        "(SELECT group_concat(name, ';') FROM "
        "(SELECT name FROM overrides WHERE include = 0 ORDER BY name)), "
        "(SELECT group_concat({}, ';') FROM (SELECT * FROM fonts ORDER BY name))"
    )
    try:
        row = db.execute(query.format(fonts)).fetchone()
    except sqlite3.OperationalError:  # DB built before fonts.label existed
        row = db.execute(query.format("name || ':' || enabled")).fetchone()
    try:
        fixes = db.execute(
            "SELECT " + FIX_ON_SQL + ", (SELECT group_concat(n || ':' || ifnull(fx, '') || ':' "  # noqa: S608  (constants only)
            "|| ifnull(h, ''), ';') FROM (SELECT DISTINCT f.preset AS n, s.fixes AS fx, "
            "s.color_hash AS h FROM favorites f LEFT JOIN schemes s ON s.name = f.preset "
            "ORDER BY f.preset))"
        ).fetchone()
    except sqlite3.OperationalError:  # DB built before 0.9.0
        fixes = ()
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
    rows: list[tuple[Any, ...]]
    try:
        rows = db.execute(
            "SELECT f.preset, f.font, f.size, CASE WHEN " + FIX_ON_SQL + " THEN s.fixes END, "  # noqa: S608  (constants only)
            "s.color_hash FROM favorites f LEFT JOIN schemes s ON s.name = f.preset "
            + where
            + "ORDER BY f.preset, f.font"
        ).fetchall()
    except sqlite3.OperationalError:  # DB built before 0.9.0: no adjustments
        rows = [
            (*r, None, None)
            for r in db.execute(
                "SELECT f.preset, f.font, f.size FROM favorites f " + where + "ORDER BY 1, 2"  # noqa: S608  (constants only)
            )
        ]
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
        snap.rows, prefs.get("Custom Color Presets", {}), parent, snap.fallback
    )
    data = plistlib.dumps({"Profiles": profiles})
    try:
        with open(DYNAMIC_PATH, "rb") as f:
            if f.read() == data:
                _profile_adjusted.update(adjusted)
                return None
    except FileNotFoundError:
        pass
    folder, name = os.path.split(DYNAMIC_PATH)
    os.makedirs(folder, exist_ok=True)
    # Write, then rename, so iTerm2 never reads half a file. The temp name starts with a dot
    # because iTerm2 reloads every other file in the folder on any change (it skips dot-files;
    # iTermDynamicProfileManager.m), and a temp copy would duplicate every GUID.
    partial = os.path.join(folder, f".{name}.partial")
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
    """Snapshot the DB through this connection if the newest snapshot is a day old."""
    folder = os.path.join(os.path.dirname(DB_PATH), "backups")
    if srcp_db_backup.future_snapshots(folder):
        warn_once(
            "snapshot-future",
            f"snapshots in {folder} are named with a time in the future (a wrong clock, or "
            "copied from another Mac); they're kept but don't count as the newest",
        )
    return srcp_db_backup.snapshot_if_due(DB_PATH, src=db)


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
) -> bool:
    """A session opened from a favorite's profile keeps its look; True if this is one."""
    guid = profile.original_guid or profile.guid
    fav = await store.call(favorite_by_guid, guid) if guid else None
    if not fav:
        return False
    preset, font, stars, preset_mode, adjusted = fav
    # The preset's own mode, not the current theme: a dark favorite can be opened in light
    # mode. The theme's only if the preset is gone from the DB (not rebuilt since).
    mode = preset_mode or theme_mode
    # What its profile file got (none if the preset changed since the build), when known.
    adjusted = _profile_adjusted.get(preset, adjusted)
    _, size = split_font(profile.normal_font)
    # Its profile file already holds the adjusted colors (write_favorite_profiles).
    note = f", {colors_adjusted(adjusted)}" if adjusted else ""
    log("info", f"{mode} favorite profile {stars} ★: {preset} | {font} {size}{note}")
    await set_variables(
        session,
        color_preset=preset,
        font=font,
        font_size=size,
        mode=mode,
        is_favorite=1,  # 1/0, not True/False: the menu's visible_when compares with 1
        adjusted=adjusted,
    )
    launch = Launch(preset, font, size, mode, "favorite", stars, profile.name, session.session_id)
    await store.call(record_launch, launch)
    return True


async def color_session(
    connection: iterm2.Connection,
    app: iterm2.App,
    store: Store,
    session: iterm2.Session,
    reroll: bool = False,
) -> None:
    """reroll: replace the session's look (the menu's Change), even a favorite profile's."""
    current = await session.async_get_variable("user.color_preset")
    # Already has a look: a restored window, or one startup() colored while it was also
    # queued as new. Coloring again would change it and log a second launch.
    if current and not reroll:
        return
    # Picks up favorites changed outside the dialog, without holding up this tab: rewriting
    # the profiles reads all of iTerm2's settings (about 0.3 s, or up to 30 s if stuck).
    in_background(sync_and_log(store), "favorite profiles")
    theme, profile = await asyncio.gather(app.async_get_theme(), session.async_get_profile())
    mode = "dark" if "dark" in theme else "light"
    if not reroll and await keep_favorite_profile(store, session, profile, mode):
        return
    exclude: set[str] = {current} if current else set()  # a re-roll never gives the same back
    for _ in range(3):
        look = await store.call(pick_look, mode, exclude)
        if look is None:  # store.call already logged why
            log("warn", "couldn't read the DB; session left as is")
            return
        if not look:
            warn_once(f"empty-{mode}", f"no {mode} presets to pick from; sessions left as is")
            return
        preset = await get_preset(connection, look.preset)
        if preset:
            break
        log("warn", f"preset not found: {look.preset} (removed from iTerm2? rebuild the DB)")
        exclude.add(look.preset)
    else:
        warn_once(
            "presets-missing",
            "3 picks in a row weren't installed presets, so this session was left as is; "
            "rebuild the DB (.tools/srcp_build_scheme_db.py) after adding or removing presets",
        )
        return

    adjusted = adjust_preset(look.preset, preset, look.fixes_json, look.built_hash)
    _, size = split_font(profile.normal_font)

    async def icon_font(name: str) -> None:  # set the font before switching it on
        await profile.async_set_non_ascii_font(f"{name} {size}")
        await profile.async_set_use_non_ascii_font(True)

    await asyncio.gather(
        profile.async_set_color_preset(preset),
        *([profile.async_set_normal_font(f"{look.font} {size}")] if look.font else []),
        *([icon_font(look.nonascii)] if look.nonascii else []),
    )

    label = f"favorite {look.stars} ★" if look.source == "favorite" else "discovery"
    note = f", {colors_adjusted(adjusted)}" if adjusted else ""
    font_text = look.font or "(profile font)"
    log("info", f"{mode} {label}: {look.preset} | {font_text} {size}{note}")
    # After the profile writes, so a tab that closed meanwhile never names a look it didn't
    # get. font "" on a tab with no font of ours, so a re-roll doesn't keep the old one's.
    await set_variables(
        session,
        color_preset=look.preset,
        font=look.font or "",
        font_size=size,
        mode=mode,
        is_favorite=int(look.is_favorite),  # 1/0: the menu's visible_when compares with 1
        adjusted=adjusted,  # for the rating dialog
    )
    launch = Launch(
        look.preset,
        look.font,
        size,
        mode,
        look.source,
        look.stars,
        profile.name,
        session.session_id,
    )
    await store.call(record_change if reroll else record_launch, launch)


def colors_adjusted(n: int) -> str:
    return "1 color adjusted" if n == 1 else f"{n} colors adjusted"


_background: set[asyncio.Task[Any]] = set()  # kept so they aren't collected mid-run


def in_background(coro: Coroutine[Any, Any, Any], what: str) -> None:
    """Run coro without waiting for it; log if it fails."""
    task = asyncio.create_task(coro)
    _background.add(task)

    def done(t: asyncio.Task[Any]) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception():
            log("warn", f"{what} failed: {t.exception()!r}")

    task.add_done_callback(done)


async def sync_and_log(store: Store) -> int | None:
    """Rewrite the favorites' profiles if what they're made of changed; the count, or None."""
    async with store.sync_lock:  # the read and the write, so two syncs can't interleave
        snap = await store.call(read_favorites)
        if snap is None or snap.state == _profiles_state[0]:
            return None
        try:
            count = await asyncio.to_thread(write_favorite_profiles, snap)
        except Exception as e:  # e.g. defaults export failed: profiles are optional, coloring isn't
            log("warn", f"favorite profiles not updated: {e!r}")
            return None
        _profiles_state[0] = snap.state  # only once written, so a failed write is retried
    if count is not None:
        log("info", f"favorite profiles: {count} in {DYNAMIC_PATH}")
    return count


async def favorites_changed(app: iterm2.App, store: Store, preset: str) -> None:
    """Update user.is_favorite on open sessions showing preset, and the profiles file."""

    async def update(session: iterm2.Session) -> None:
        try:
            if await session.async_get_variable("user.color_preset") != preset:
                return
            font = await session.async_get_variable("user.font")
            fav = bool(font) and bool(await store.call(is_favorite, preset, font))
            await session.async_set_variable("user.is_favorite", int(fav))
        except iterm2.RPCException:
            pass  # closed while we were looking

    try:
        sessions = [s for w in app.windows for t in w.tabs for s in t.all_sessions]
        await asyncio.gather(*(update(s) for s in sessions))
    finally:
        await sync_and_log(store)


async def apply_choice(
    app: iterm2.App, store: Store, choice: str, look: tuple[str, str, float]
) -> bool:
    """look: (preset, font, size) of the session the choice was made in.

    Returns False if the DB call failed (store.call gives None: locked, missing, ...), so the
    caller can tell the user; a legitimate "nothing to do" (False from the call) is True here.
    """
    preset, font, size = look
    result: bool | None = False
    if choice in STAR_BUTTONS:
        rating = STAR_BUTTONS.index(choice) + 1
        if result := await store.call(save_favorite, preset, font, size, rating):
            log("info", f"saved favorite {rating} ★: {preset} | {font} {size}")
    elif choice == REMOVE:
        if result := await store.call(remove_favorite, preset, font):
            log("info", f"removed favorite: {preset} | {font} {size}")
        elif result is not None:
            log("info", f"not a favorite, nothing removed: {preset} | {font} {size}")
    elif choice == NEVER and (result := await store.call(never_show, preset)):
        log("info", f"never show: {preset}")
    if result:
        await favorites_changed(app, store, preset)
    return result is not None


SAVE_TITLE = "Save/update this profile"


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
    lines = [f"{preset} ({mode})", f"{await store.call(font_label, font) or font} {size}"]
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
    if info is None:
        await db_failed_alert(connection, SAVE_TITLE, window_id)
        return
    text, stars = await save_dialog_text(store, session, (preset, font, size), info)
    alert = iterm2.Alert(SAVE_TITLE, text, window_id)
    # Remove favorite only appears when this (preset, font) is a favorite. Cancel goes first so
    # Return can't save 1 star: NSAlert gives Return to the first button and Escape to one
    # titled Cancel, so here Escape cancels and Return only beeps (checked in iTerm2 3.7.6).
    buttons = [CANCEL, *STAR_BUTTONS, *([REMOVE] if stars else []), NEVER]
    for b in buttons:
        alert.add_button(b)
    index = await alert.async_run(connection) - 1000
    choice = buttons[index] if 0 <= index < len(buttons) else CANCEL
    # Never show on a favorite asks first, as the right-click item does.
    go = choice != CANCEL and (
        choice != NEVER or await confirm_never_show(connection, store, preset, window_id)
    )
    if not go:
        if go is None:
            await db_failed_alert(connection, SAVE_TITLE, window_id)
        return
    if not applied_font:
        # The save uses the profile's font; record it as this tab's, or favorites_changed would
        # find no user.font here and mark the tab as not a favorite.
        await session.async_set_variable("user.font", font)
    if not await apply_choice(app, store, choice, (preset, font, size)):
        await db_failed_alert(connection, SAVE_TITLE, window_id)
        return
    if choice == NEVER:  # hidden now (or already was): give the tab another look, as the menu does
        await reroll_after_hide(connection, app, store, session)


def not_colored(consequence: str) -> str:
    """Text for a menu item used on a tab this script didn't color."""
    return f"This tab wasn't colored by srcp_set_random_color_profile.py, so {consequence}."


async def db_failed_alert(connection: iterm2.Connection, title: str, window_id: str | None) -> None:
    """Tell the user a dialog action was lost to a DB failure (store.call gave None)."""
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
    title = "Remove profile from favorites"
    fav = await store.call(is_favorite, preset, font) if preset and font else False
    if fav is None:  # the DB failed, which isn't the same as "not a favorite"
        await db_failed_alert(connection, title, window_id)
        return
    if fav:
        if not await apply_choice(app, store, REMOVE, (preset, font, size)):
            await db_failed_alert(connection, title, window_id)
        return
    # iTerm2 before API protocol 1.23 shows the item on every tab, so say why nothing happens.
    if preset and font:
        label = await store.call(font_label, font) or font
        text = f"{preset} with {label} isn't a favorite, so there's nothing to remove."
    elif preset:  # colored, but no font of ours was set (none enabled), so none was saved
        text = f"This tab's {preset} has no font of its own, so it isn't a favorite."
    else:
        text = not_colored("it has no favorite")
    await iterm2.Alert(title, text, window_id).async_run(connection)


async def change_from_menu(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session_id: str
) -> None:
    session = app.get_session_by_id(session_id)
    if not session:
        log("warn", f"change: session {session_id} not found")
        return
    await color_session(connection, app, store, session, reroll=True)


NEVER_TITLE = "Never show this profile"


async def confirm_never_show(
    connection: iterm2.Connection, store: Store, preset: str, window_id: str | None
) -> bool | None:
    """If preset is a favorite, ask before Never show drops it: True to go ahead, False if
    cancelled, None if the DB failed. True straight away for a preset with no favorites."""
    favs = await store.call(favorite_fonts, preset)
    if favs is None:
        return None
    if not favs:
        return True
    lines = [f"{preset} is a favorite:"]
    lines += [f"  {label} {STAR_BUTTONS[stars - 1]}" for label, stars in favs]
    lines.append("Never show removes it from your favorites and from Profiles > Favorites.")
    alert = iterm2.Alert(NEVER_TITLE, "\n".join(lines), window_id)
    buttons = [CANCEL, NEVER]  # Cancel first: Escape cancels and Return can't hide it
    for b in buttons:
        alert.add_button(b)
    index = await alert.async_run(connection) - 1000
    return 0 <= index < len(buttons) and buttons[index] == NEVER


async def reroll_after_hide(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session: iterm2.Session
) -> None:
    """Give a tab whose preset was just hidden another look, or say why it can't have one."""
    preset = await session.async_get_variable("user.color_preset")
    window, _ = app.get_window_and_tab_for_session(session)
    window_id = window.window_id if window else None
    try:
        await color_session(connection, app, store, session, reroll=True)
        new = await session.async_get_variable("user.color_preset")
    except iterm2.RPCException as e:  # closed while the question was up: it's hidden anyway
        log("info", f"never show: {preset} hidden; its tab closed before the new look ({e})")
        return
    if new == preset:  # no other preset left, or the DB failed (color_session logged which)
        text = (
            f"{preset} is hidden, but this tab couldn't switch to another look, so it keeps "
            "this one until you close it. Scripts > Manage > Console says why (usually: "
            "no other preset of this light/dark mode is left)."
        )
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
    confirmed = await confirm_never_show(connection, store, preset, window_id)
    if confirmed is None:
        await db_failed_alert(connection, NEVER_TITLE, window_id)
        return
    if not confirmed:
        return
    # Not apply_choice: that needs the tab's font, which a tab without one doesn't have.
    hidden = await store.call(never_show, preset)
    if hidden is None:
        await db_failed_alert(connection, NEVER_TITLE, window_id)
        return  # nothing hidden, so keep the look rather than re-roll into confusion
    if hidden:
        log("info", f"never show: {preset}")
        await favorites_changed(app, store, preset)
    else:
        log("info", f"never show: {preset} was already hidden")
    await reroll_after_hide(connection, app, store, session)


async def register_save_handlers(
    connection: iterm2.Connection, app: iterm2.App, store: Store
) -> None:
    """Save (right-click and save_profile() key), and the right-click Remove profile from favorites,
    Change to another profile and Never show this profile."""
    running: dict[str, asyncio.Task[None]] = {}

    def start(key: str, coro: Coroutine[Any, Any, None]) -> None:
        action, session_id = key.split(":", maxsplit=1)
        if session_id in running:
            coro.close()  # one dialog or removal per session at a time, so they can't race
            log("info", f"{action}: another action is still running on this session; ignored")
            return
        # Run outside the RPC so iTerm2's RPC timeout can't cut a dialog off.
        task = asyncio.create_task(coro)
        running[session_id] = task

        def done(t: asyncio.Task[None]) -> None:
            running.pop(session_id, None)
            if not t.cancelled() and t.exception():
                log("warn", f"{action} failed: {t.exception()!r}")

        task.add_done_callback(done)

    def start_dialog(session_id: str) -> None:
        start(f"save:{session_id}", save_dialog(connection, app, store, session_id))

    @iterm2.RPC
    async def save_profile(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        start_dialog(session_id)

    @iterm2.ContextMenuProviderRPC
    async def save_profile_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        start_dialog(session_id)

    @iterm2.ContextMenuProviderRPC
    async def remove_favorite_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        start(f"remove:{session_id}", remove_from_menu(connection, app, store, session_id))

    @iterm2.ContextMenuProviderRPC
    async def change_profile_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        start(f"change:{session_id}", change_from_menu(connection, app, store, session_id))

    @iterm2.ContextMenuProviderRPC
    async def never_show_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]  # Reference resolves to the id at call time
        start(f"never:{session_id}", never_show_from_menu(connection, app, store, session_id))

    await save_profile.async_register(connection)
    await save_profile_menu.async_register(connection, SAVE_TITLE, MENU_ID)
    await change_profile_menu.async_register(
        connection, "Change to another profile", CHANGE_MENU_ID
    )
    await never_show_menu.async_register(connection, NEVER_TITLE, NEVER_MENU_ID)
    # Hide the item on non-favorites where iTerm2 can (API protocol 1.23+, newer than 3.7.6);
    # elsewhere it shows on every tab and remove_from_menu explains. Passing visible_when to
    # an older iTerm2 only adds a warning to the Console.
    hide = supports_context_menu_submenus_and_visibility(connection)
    await remove_favorite_menu.async_register(
        connection,
        "Remove profile from favorites",
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
        await startup(connection, app, store, started)
        await asyncio.gather(
            watch_new(connection, app, store, new_sessions),
            watch_ended(store, ended_sessions),
            snapshot_periodically(store),
            exit_on_disconnect(connection),
        )


async def startup(
    connection: iterm2.Connection, app: iterm2.App, store: Store, started: str
) -> None:
    open_sessions = [s for w in app.windows for t in w.tabs for s in t.all_sessions]
    live = {s.session_id for s in open_sessions}
    live |= {s.session_id for s in app.buried_sessions}
    stale = await store.call(close_stale, live, started)
    if stale:
        log("info", f"{stale} launches from a previous run ended as 'unknown'")
    backup = await store.call(daily_snapshot)
    if backup:
        log("info", f"DB snapshot: {backup}")
    await sync_and_log(store)
    # Windows iTerm2 opened before this script connected (the one at launch) got no look.
    # Restored windows keep theirs: iTerm2 saves user.* variables with them.
    for session in open_sessions:
        try:
            await color_session(connection, app, store, session)
        except Exception as e:  # one session must not stop the rest, or startup
            log("warn", f"coloring open session failed: {e!r}")


async def watch_new(
    connection: iterm2.Connection,
    app: iterm2.App,
    store: Store,
    mon: iterm2.NewSessionMonitor,
) -> None:
    while True:
        session_id = await mon.async_get()
        session = app.get_session_by_id(session_id)
        if not session:
            log("warn", f"new session {session_id} not found; left as is")
            continue
        try:
            await color_session(connection, app, store, session)
        except Exception as e:  # never stop watching over one session
            log("warn", f"coloring session failed: {e!r}")


async def watch_ended(store: Store, mon: iterm2.SessionTerminationMonitor) -> None:
    while True:
        session_id = await mon.async_get()
        try:
            await store.call(record_end, session_id)
        except Exception as e:
            log("warn", f"recording session end failed: {e!r}")


async def exit_on_disconnect(connection: iterm2.Connection) -> None:
    # The monitors never return once iTerm2 quits, so without this the process outlives it
    # and holds the lock, and the copy the next iTerm2 launch starts exits.
    if connection.websocket is not None:
        await connection.websocket.wait_closed()
    log("info", "iTerm2 closed the connection; exiting")
    sys.exit(0)  # returning would leave the watchers, and so the process, alive


iterm2.run_forever(main)
