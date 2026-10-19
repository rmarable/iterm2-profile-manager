#!/usr/bin/env python3
"""Give each new session a color preset matching the macOS light/dark mode, plus a random font.

Picks from the scheme DB built by .tools/srcp_build_scheme_db.py: a starred favorite of
the current mode (weighted by stars), or otherwise a gate-passing preset of that
mode not shown in the last few launches. Records every launch and when it ended,
and snapshots the DB once a day. "Save/update this profile" (right-click) or the
save_profile() key binding stars the current look; "Remove from favorites"
(right-click, shown on favorites only) un-stars it. Every favorite is also an
iTerm2 profile ("Kibble - Menlo"), kept in a Dynamic Profiles file.
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import fcntl
import glob
import os
import plistlib
import random
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import IO, Any, TypeVar

import iterm2
from iterm2.capabilities import supports_context_menu_submenus_and_visibility

VERSION = "0.5.2"  # bump on every change, so the Console shows which code is running
DATA_DIR = os.path.expanduser("~/.local/share/iterm2-color-schemes")
DB_PATH = os.path.join(DATA_DIR, "schemes.db")
LOCK_PATH = os.path.join(DATA_DIR, ".lock")
BACKUP_KEEP = 14
BACKUP_EVERY_S = 24 * 3600
DEFAULT_SIZE = 13
DEFAULT_CONFIG = {"favorite_share": 0.5, "favorite_target": 10, "recent_window": 20}
# Font for characters the main font lacks, e.g. Powerlevel10k prompt icons; config overrides.
DEFAULT_NONASCII_FONT = "MesloLGSNFM-Regular"
MENU_ID = "com.rmarable.set-random-color-profile.save"
REMOVE_MENU_ID = "com.rmarable.set-random-color-profile.remove"
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
Pick = tuple[str, str | None, str, int | None]  # preset, font, source, stars

_warned: set[str] = set()


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log("warn", msg)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def acquire_lock() -> IO[str] | None:
    """Hold an exclusive lock for the life of the process, so restarts can't stack copies."""
    os.makedirs(DATA_DIR, exist_ok=True)
    f = open(LOCK_PATH, "w")  # noqa: SIM115 - held open until the process exits
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


class Store:
    """One read-write connection, used from worker threads one call at a time."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.db: sqlite3.Connection | None = None
        self.inode: int | None = None
        self.lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection | None:
        try:
            inode = os.stat(self.path).st_ino
        except FileNotFoundError:
            inode = None
        if self.db is not None and inode != self.inode:
            # Deleted or replaced by a rename (the README's restore does cp + mv): don't keep
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
        if self.db is not None and self.db.in_transaction:
            self.db.execute("ROLLBACK")

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


# ---- DB calls (run in a worker thread) ----


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


def pick(db: sqlite3.Connection, mode: str, exclude: set[str]) -> Pick | None:
    """Choose a favorite or a discovery preset for mode, or None if nothing fits."""
    cfg = read_config(db)
    favs: list[tuple[str, str, int]] = [
        r
        for r in db.execute("SELECT preset, font, stars FROM favorite_pool WHERE mode = ?", (mode,))
        if r[0] not in exclude
    ]
    disc = [
        n
        for (n,) in db.execute("SELECT name FROM discovery WHERE mode = ?", (mode,))
        if n not in exclude
    ]
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
    return random.choice([n for n in disc if n not in recent] or disc), None, "discovery", None


def nonascii_font(db: sqlite3.Connection) -> str | None:
    """config nonascii_font (PostScript name); '' turns it off."""
    row = db.execute("SELECT value FROM config WHERE key = 'nonascii_font'").fetchone()
    value = DEFAULT_NONASCII_FONT if row is None or row[0] is None else str(row[0]).strip()
    return value or None


def random_font(db: sqlite3.Connection) -> str | None:
    row = db.execute(
        "SELECT name FROM fonts WHERE enabled = 1 ORDER BY random() LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def record_launch(db: sqlite3.Connection, *values: Any) -> bool:
    """values: preset, font, size, mode, source, stars, iterm_profile, session_id."""
    db.execute(
        "INSERT INTO launches (launched_at, preset, font, size, mode, source, stars, "
        "iterm_profile, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now(), *values),
    )
    return True


def record_end(db: sqlite3.Connection, session_id: str) -> bool:
    t = now()
    db.execute(
        "UPDATE launches SET ended_at = ?, end_reason = 'closed', "
        "duration_s = CAST(round((julianday(?) - julianday(launched_at)) * 86400) AS INTEGER) "
        "WHERE session_id = ? AND ended_at IS NULL AND end_reason IS NULL",
        (t, t, session_id),
    )
    return True


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
) -> tuple[tuple[int, str] | None, int | None]:
    stats = db.execute(
        "SELECT launches, last_launched FROM launch_stats WHERE preset = ?", (preset,)
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
        "INSERT INTO ratings (at, preset, font, action, stars, previous_stars) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now(), preset, font, action, stars, previous),
    )


def save_favorite(db: sqlite3.Connection, preset: str, font: str, size: float, stars: int) -> bool:
    db.execute("BEGIN IMMEDIATE")  # Store.call rolls back if anything below fails
    # Starring a preset marked Never show (e.g. in a tab opened before) unhides it;
    # otherwise the favorite would never be picked or get a profile.
    db.execute("DELETE FROM overrides WHERE name = ? AND include = 0", (preset,))
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


def is_favorite(db: sqlite3.Connection, preset: str, font: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM favorites WHERE preset = ? AND font = ?", (preset, font)
        ).fetchone()
    )


def favorite_guid(preset: str, font: str) -> str:
    """Stable GUID of a favorite's profile, so iTerm2 keeps it across rewrites."""
    return str(uuid.uuid5(GUID_NAMESPACE, f"{preset}\0{font}")).upper()


def favorite_by_guid(db: sqlite3.Connection, guid: str) -> tuple[str, str, int, str] | None:
    """(preset, font, stars, mode) of the favorite whose profile has this GUID."""
    for preset, font, stars, mode in db.execute(
        "SELECT f.preset, f.font, f.stars, COALESCE(e.mode, '?') "
        "FROM favorites f LEFT JOIN effective e ON e.name = f.preset"
    ):
        if favorite_guid(preset, font) == guid:
            return preset, font, stars, mode
    return None


def font_label(db: sqlite3.Connection, font: str) -> str:
    try:
        row = db.execute("SELECT label FROM fonts WHERE name = ?", (font,)).fetchone()
    except sqlite3.OperationalError:  # DB built before fonts.label existed
        row = None
    return row[0] if row and row[0] else font.removesuffix("-Regular")


def sync_profiles(db: sqlite3.Connection) -> int | None:
    """Write one iTerm2 profile per favorite; return the count, or None if unchanged."""
    prefs = plistlib.loads(
        subprocess.run(
            ["defaults", "export", "com.googlecode.iterm2", "-"], capture_output=True, check=True
        ).stdout
    )
    presets = prefs.get("Custom Color Presets", {})
    default_guid = prefs.get("Default Bookmark Guid")
    parent = next(
        (b.get("Name") for b in prefs.get("New Bookmarks", []) if b.get("Guid") == default_guid),
        "Default",
    )
    fallback = nonascii_font(db)
    profiles = []
    for preset, font, size in db.execute(
        "SELECT preset, font, size FROM favorites "
        "WHERE preset NOT IN (SELECT name FROM overrides WHERE include = 0) "
        "ORDER BY preset, font"
    ):
        if preset not in presets:
            warn_once(f"profile-{preset}", f"favorite {preset!r} isn't an installed preset")
            continue
        pt = float(size or DEFAULT_SIZE)
        size_text = str(int(pt)) if pt.is_integer() else str(pt)
        profiles.append(
            {
                **presets[preset],
                "Name": f"{preset} - {font_label(db, font)}",
                "Guid": favorite_guid(preset, font),
                "Dynamic Profile Parent Name": parent,
                "Normal Font": f"{font} {size_text}",
                **(
                    {"Use Non-ASCII Font": True, "Non Ascii Font": f"{fallback} {size_text}"}
                    if fallback
                    else {}
                ),
                "Tags": [PROFILE_TAG],  # each tag is a Profiles submenu: one, so listed once
            }
        )
    data = plistlib.dumps({"Profiles": profiles})
    try:
        with open(DYNAMIC_PATH, "rb") as f:
            if f.read() == data:
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
    return len(profiles)


_profiles_state: list[str | None] = [None]  # what sync_profiles last wrote from


def sync_if_changed(db: sqlite3.Connection) -> int | None:
    """sync_profiles, but only when favorites, Never show or the icon font changed.

    Cheap enough to call for every new session, so restoring an export or editing
    favorites in sqlite3 shows up in the Profiles menu without a restart.
    """
    state = "|".join(
        str(v)
        for v in db.execute(
            "SELECT (SELECT group_concat(preset || ':' || font || ':' || stars || ':' || "
            "ifnull(size, ''), ';') FROM (SELECT * FROM favorites ORDER BY preset, font)), "
            "(SELECT group_concat(name, ';') FROM "
            "(SELECT name FROM overrides WHERE include = 0 ORDER BY name)), "
            "(SELECT value FROM config WHERE key = 'nonascii_font')"
        ).fetchone()
    )
    if state == _profiles_state[0]:
        return None
    count = sync_profiles(db)
    _profiles_state[0] = state
    return count


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
    """Same as .tools/srcp_db_backup.snapshot(), when the newest snapshot is a day old."""
    folder = os.path.join(DATA_DIR, "backups")
    existing = [os.path.getmtime(p) for p in glob.glob(os.path.join(folder, "schemes-*.db"))]
    if existing and time.time() - max(existing) < BACKUP_EVERY_S:
        return None
    os.makedirs(folder, exist_ok=True)
    stem = os.path.join(folder, "schemes-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    dest, n = stem + ".db", 1
    while os.path.exists(dest):
        dest, n = f"{stem}-{n}.db", n + 1
    out = sqlite3.connect(dest + ".partial")
    try:
        db.backup(out)
    except BaseException:
        out.close()
        os.remove(dest + ".partial")
        raise
    finally:
        out.close()
    os.replace(dest + ".partial", dest)
    # By mtime: a same-second "-1" suffix sorts before the plain name.
    snapshots = sorted(glob.glob(os.path.join(folder, "schemes-*.db")), key=os.path.getmtime)
    for old in snapshots[:-BACKUP_KEEP]:
        os.remove(old)
    return dest


# ---- iTerm2 side ----


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
    for name, value in values.items():
        await session.async_set_variable(f"user.{name}", value)


async def keep_favorite_profile(
    store: Store, session: iterm2.Session, profile: iterm2.Profile, mode: str
) -> bool:
    """A session opened from a favorite's profile keeps its look; True if this is one."""
    guid = profile.original_guid or profile.guid
    fav = await store.call(favorite_by_guid, guid) if guid else None
    if not fav:
        return False
    preset, font, stars, preset_mode = fav
    _, size = split_font(profile.normal_font)
    # The preset's own mode, not the current theme: a dark favorite can be opened in light mode.
    log("info", f"{preset_mode} favorite profile {stars} ★: {preset} | {font} {size}")
    await set_variables(
        session,
        color_preset=preset,
        font=font,
        font_size=size,
        mode=preset_mode,
        is_favorite=1,  # 1/0, not True/False: the menu's visible_when compares with 1
    )
    await store.call(
        record_launch,
        preset,
        font,
        size,
        preset_mode,
        "favorite",
        stars,
        profile.name,
        session.session_id,
    )
    return True


async def color_session(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session: iterm2.Session
) -> None:
    theme = await app.async_get_theme()
    mode = "dark" if "dark" in theme else "light"
    await sync_and_log(store)  # picks up favorites changed outside the dialog
    profile = await session.async_get_profile()
    if await keep_favorite_profile(store, session, profile, mode):
        return
    exclude: set[str] = set()
    for _ in range(3):
        choice = await store.call(pick, mode, exclude)
        if choice is None:
            warn_once(f"empty-{mode}", f"no {mode} presets to pick from; sessions left as is")
            return
        preset_name, font, source, stars = choice
        preset = await get_preset(connection, preset_name)
        if preset:
            break
        log("warn", f"preset not found: {preset_name} (removed from iTerm2? rebuild the DB)")
        exclude.add(preset_name)
    else:
        return

    await profile.async_set_color_preset(preset)
    _, size = split_font(profile.normal_font)
    font = font or await store.call(random_font)
    if font:
        await profile.async_set_normal_font(f"{font} {size}")
    fallback = await store.call(nonascii_font)
    if fallback:  # set the font before switching it on, so the old one never shows
        await profile.async_set_non_ascii_font(f"{fallback} {size}")
        await profile.async_set_use_non_ascii_font(True)

    label = f"favorite {stars} ★" if source == "favorite" else "discovery"
    log("info", f"{mode} {label}: {preset_name} | {font or '(profile font)'} {size}")
    variables: dict[str, Any] = {"color_preset": preset_name, "font_size": size, "mode": mode}
    if font:
        variables["font"] = font
        variables["is_favorite"] = int(bool(await store.call(is_favorite, preset_name, font)))
    await set_variables(session, **variables)
    await store.call(
        record_launch,
        preset_name,
        font,
        size,
        mode,
        source,
        stars,
        profile.name,
        session.session_id,
    )


async def sync_and_log(store: Store) -> None:
    count = await store.call(sync_if_changed)
    if count is not None:
        log("info", f"favorite profiles: {count} in {DYNAMIC_PATH}")


async def favorites_changed(app: iterm2.App, store: Store, preset: str) -> None:
    """Update user.is_favorite on open sessions showing preset, and the profiles file."""
    try:
        sessions = [s for w in app.windows for t in w.tabs for s in t.all_sessions]
        for session in sessions:
            try:
                if await session.async_get_variable("user.color_preset") != preset:
                    continue
                font = await session.async_get_variable("user.font")
                fav = bool(font) and bool(await store.call(is_favorite, preset, font))
                await session.async_set_variable("user.is_favorite", int(fav))
            except iterm2.RPCException:
                continue  # closed while we were looking
    finally:
        await sync_and_log(store)


async def apply_choice(
    app: iterm2.App, store: Store, choice: str, look: tuple[str, str, float]
) -> None:
    """look: (preset, font, size) of the session the choice was made in."""
    preset, font, size = look
    done = False
    if choice in STAR_BUTTONS:
        rating = STAR_BUTTONS.index(choice) + 1
        if done := bool(await store.call(save_favorite, preset, font, size, rating)):
            log("info", f"saved favorite {rating} ★: {preset} | {font} {size}")
    elif choice == REMOVE:
        if done := bool(await store.call(remove_favorite, preset, font)):
            log("info", f"removed favorite: {preset} | {font} {size}")
        else:
            log("info", f"not a favorite, nothing removed: {preset} | {font} {size}")
    elif choice == NEVER and (done := bool(await store.call(never_show, preset))):
        log("info", f"never show: {preset}")
    if done:
        await favorites_changed(app, store, preset)


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
        await iterm2.Alert(
            "Save/update this profile",
            "This session wasn't colored by srcp_set_random_color_profile.py, "
            "so there's nothing to save.",
            window_id,
        ).async_run(connection)
        return
    # The font the script applied. Reading it back from the session gave the wrong face
    # once (Monaco launched, Menlo-Regular read back; 2026-10-17), so that's the fallback.
    profile = await session.async_get_profile()
    live_font, size = split_font(profile.normal_font)
    font = await session.async_get_variable("user.font") or live_font
    if not font:
        log("warn", f"save: no font known for {preset}")
        return
    mode = await session.async_get_variable("user.mode")

    info = await store.call(preset_info, preset, font)
    if info is None:
        return
    stats, stars = info
    lines = [f"{preset} ({mode})", f"{await store.call(font_label, font) or font} {size}"]
    if stats:
        times = "1 time" if stats[0] == 1 else f"{stats[0]} times"
        lines.append(f"Launched {times}, last {local_time(stats[1])}.")
    else:
        lines.append("Not launched before.")
    if stars:
        lines.append(f"Saved now with {STAR_BUTTONS[stars - 1]}.")
    alert = iterm2.Alert("Save/update this profile", "\n".join(lines), window_id)
    # Remove favorite only appears when this (preset, font) is a favorite.
    buttons = [*STAR_BUTTONS, *([REMOVE] if stars else []), NEVER, CANCEL]
    for b in buttons:
        alert.add_button(b)
    index = await alert.async_run(connection) - 1000
    choice = buttons[index] if 0 <= index < len(buttons) else CANCEL
    await apply_choice(app, store, choice, (preset, font, size))


async def remove_from_menu(
    connection: iterm2.Connection, app: iterm2.App, store: Store, session_id: str
) -> None:
    session = app.get_session_by_id(session_id)
    if not session:
        return
    preset = await session.async_get_variable("user.color_preset")
    font = await session.async_get_variable("user.font")
    size = await session.async_get_variable("user.font_size") or DEFAULT_SIZE
    if preset and font and await store.call(is_favorite, preset, font):
        await apply_choice(app, store, REMOVE, (preset, font, size))
        return
    # iTerm2 before API protocol 1.23 shows the item on every tab, so say why nothing happens.
    if preset and font:
        label = await store.call(font_label, font) or font
        text = f"{preset} with {label} isn't a favorite, so there's nothing to remove."
    else:
        text = "This tab wasn't colored by srcp_set_random_color_profile.py, so it has no favorite."
    window, _ = app.get_window_and_tab_for_session(session)
    await iterm2.Alert(
        "Remove from favorites", text, window.window_id if window else None
    ).async_run(connection)


async def register_save_handlers(
    connection: iterm2.Connection, app: iterm2.App, store: Store
) -> None:
    """Save (right-click and save_profile() key) and Remove from favorites (right-click)."""
    running: dict[str, asyncio.Task[None]] = {}

    def start(key: str, coro: Coroutine[Any, Any, None]) -> None:
        session_id = key.split(":", maxsplit=1)[1]
        if session_id in running:
            coro.close()
            return  # one dialog or removal per session at a time, so they can't race
        # Run outside the RPC so iTerm2's RPC timeout can't cut a dialog off.
        task = asyncio.create_task(coro)
        running[session_id] = task

        def done(t: asyncio.Task[None]) -> None:
            running.pop(session_id, None)
            if not t.cancelled() and t.exception():
                log("warn", f"{key.split(':', maxsplit=1)[0]} failed: {t.exception()!r}")

        task.add_done_callback(done)

    def start_dialog(session_id: str) -> None:
        start(f"save:{session_id}", save_dialog(connection, app, store, session_id))

    @iterm2.RPC
    async def save_profile(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]
        start_dialog(session_id)

    @iterm2.ContextMenuProviderRPC
    async def save_profile_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]
        start_dialog(session_id)

    @iterm2.ContextMenuProviderRPC
    async def remove_favorite_menu(session_id: str = iterm2.Reference("id")) -> None:  # pyright: ignore[reportArgumentType]
        start(f"remove:{session_id}", remove_from_menu(connection, app, store, session_id))

    await save_profile.async_register(connection)
    await save_profile_menu.async_register(connection, "Save/update this profile", MENU_ID)
    # Hide the item on non-favorites where iTerm2 can (API protocol 1.23+, newer than 3.7.6);
    # elsewhere it shows on every tab and remove_from_menu explains. Passing visible_when to
    # an older iTerm2 only adds a warning to the Console.
    hide = supports_context_menu_submenus_and_visibility(connection)
    await remove_favorite_menu.async_register(
        connection,
        "Remove from favorites",
        REMOVE_MENU_ID,
        visible_when="user.is_favorite == 1" if hide else None,
    )


async def main(connection: iterm2.Connection) -> None:
    modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(__file__)))
    log(
        "info",
        f"srcp_set_random_color_profile {VERSION} (file modified {modified}), pid {os.getpid()}",
    )
    lock = acquire_lock()
    if lock is None:
        log("info", "another copy is already running; exiting")
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
        await startup(app, store, started)
        await asyncio.gather(
            watch_new(connection, app, store, new_sessions),
            watch_ended(store, ended_sessions),
        )


async def startup(app: iterm2.App, store: Store, started: str) -> None:
    live = {s.session_id for w in app.windows for t in w.tabs for s in t.all_sessions}
    live |= {s.session_id for s in app.buried_sessions}
    stale = await store.call(close_stale, live, started)
    if stale:
        log("info", f"{stale} launches from a previous run ended as 'unknown'")
    backup = await store.call(daily_snapshot)
    if backup:
        log("info", f"DB snapshot: {backup}")
    await sync_and_log(store)


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


iterm2.run_forever(main)
