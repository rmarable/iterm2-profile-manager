#!/usr/bin/env python3
"""Compare the fixed-width fonts on this Mac; add one to, or remove one from, the font pool.

Safe while iTerm2 runs (it writes no iTerm2 settings):
    python3 .tools/srcp_fonts.py                         # write and open a page comparing the fonts
    python3 .tools/srcp_fonts.py --add "JetBrains Mono"  # installs it with Homebrew if needed
    python3 .tools/srcp_fonts.py --remove "JetBrains Mono"
    python3 .tools/srcp_fonts.py --catalog               # docs/MONOSPACE-FONT-INVENTORY.md
--dryrun shows what --add or --remove would do and changes nothing.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import difflib
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python3 -I too
from srcp_favorites import connect
from srcp_iterm_prefs import load_prefs
from srcp_schema import DB_PATH as DEFAULT_DB
from srcp_schema import (
    FONT_DIRS,
    REBUILD,
    SEED_FONT_LABELS,
    SEED_FONTS,
    OldSchema,
    added_fonts_path,
    is_busy,
    read_added_fonts,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JXA = os.path.join(HERE, "srcp_fonts.js")
TEMPLATE = os.path.join(HERE, "srcp_fonts.html")
# setup.sh installs this one for the icon font (nonascii_font), so the tool never uninstalls it.
MESLO_CASK = "font-meslo-lg-nerd-font"
# What `brew install` would otherwise do on the side: update Homebrew, upgrade other packages.
BREW_ENV = {
    "HOMEBREW_NO_AUTO_UPDATE": "1",
    "HOMEBREW_NO_INSTALL_UPGRADE": "1",
    "HOMEBREW_NO_INSTALL_CLEANUP": "1",
}
RESOLVE_WAIT_S = 5.0  # macOS can take a moment to see a font Homebrew just installed
CHECKED = "2026-11-06"  # when the casks and PostScript names below were checked


class Font(NamedTuple):
    label: str
    names: tuple[str, ...]  # PostScript names to try, in order (variable fonts can vary)
    source: str  # "macOS", "Terminal.app" or "Homebrew"
    cask: str = ""  # the Homebrew cask, for Homebrew fonts
    files: tuple[str, ...] = ()  # font file names, for fonts that don't come from Homebrew
    web: tuple[str, ...] = ()  # web copies (regular, bold) for previews when not installed
    note: str = ""
    group: str = ""  # cards with the same group share one card on the page


def fontsource(font_id, version="5.3.0"):
    base = f"https://cdn.jsdelivr.net/fontsource/fonts/{font_id}@{version}/latin-"
    return (base + "400-normal.woff2", base + "700-normal.woff2")


# Where each default font comes from: (source, cask).
SEED_SOURCES = {
    "SFMono-Regular": ("Terminal.app", ""),
    "SFMonoTerminal-Regular": ("Terminal.app", ""),
    "Menlo-Regular": ("macOS", ""),
    "Monaco": ("macOS", ""),
    "PTMono-Regular": ("macOS", ""),
    "AndaleMono": ("macOS", ""),
    "MesloLGSNFM-Regular": ("Homebrew", MESLO_CASK),
}
SEED_NOTES = {
    "AndaleMono": "No bold face; iTerm2 draws a synthetic bold.",
    "MesloLGSNFM-Regular": "setup.sh installs it with Homebrew because Oh My Zsh prompts need it.",
}
_MESLO_NOTE = "Same as MesloLGS except for line spacing (S, M, L) and a dotted zero (DZ)."
OTHER_FONTS = (
    Font("Courier New", ("CourierNewPSMT",), "macOS", files=("Courier New.ttf",)),
    *(
        Font(
            f"MesloLG{v} Nerd Font Mono",
            (f"MesloLG{v}NFM-Regular",),
            "Homebrew",
            MESLO_CASK,
            note=_MESLO_NOTE,
            group="meslo",
        )
        for v in ("M", "L", "SDZ", "MDZ", "LDZ")
    ),
)
# The Homebrew casks, and the PostScript name of the face to use (read from each cask's own
# files, CHECKED). Some ship only variable fonts, whose name macOS reports can differ.
HOMEBREW_FONTS = (
    Font("0xProto", ("0xProto-Regular",), "Homebrew", "font-0xproto",
         web=("https://cdn.jsdelivr.net/gh/0xType/0xProto@main/fonts/0xProto-Regular.woff2",
              "https://cdn.jsdelivr.net/gh/0xType/0xProto@main/fonts/0xProto-Bold.woff2")),
    Font("Cascadia Code", ("CascadiaCode-Regular", "CascadiaCodeRoman-Regular"), "Homebrew",
         "font-cascadia-code", web=fontsource("cascadia-code")),
    Font("Commit Mono", ("CommitMono-Regular",), "Homebrew", "font-commit-mono",
         web=fontsource("commit-mono")),
    Font("DejaVu Sans Mono", ("DejaVuSansMono",), "Homebrew", "font-dejavu",
         web=fontsource("dejavu-mono"), note="The cask also installs DejaVu Sans and Serif."),
    Font("Fira Code", ("FiraCode-Regular",), "Homebrew", "font-fira-code",
         web=fontsource("fira-code")),
    Font("Geist Mono", ("GeistMono-Regular",), "Homebrew", "font-geist-mono",
         web=fontsource("geist-mono")),
    Font("Hack", ("Hack-Regular",), "Homebrew", "font-hack",
         web=("https://cdn.jsdelivr.net/npm/hack-font@3/build/web/fonts/hack-regular.woff2",
              "https://cdn.jsdelivr.net/npm/hack-font@3/build/web/fonts/hack-bold.woff2")),
    Font("IBM Plex Mono", ("IBMPlexMono",), "Homebrew", "font-ibm-plex-mono",
         web=fontsource("ibm-plex-mono")),
    Font("Inconsolata", ("Inconsolata-Regular",), "Homebrew", "font-inconsolata",
         web=fontsource("inconsolata")),
    Font("Intel One Mono", ("IntelOneMono-Regular",), "Homebrew", "font-intel-one-mono",
         web=fontsource("intel-one-mono")),
    Font("Iosevka Term", ("Iosevka-Term",), "Homebrew", "font-iosevka",
         web=fontsource("iosevka"),
         note="The cask installs every Iosevka style (about 130 MB); Term is made for terminals."),
    Font("JetBrains Mono", ("JetBrainsMono-Regular",), "Homebrew", "font-jetbrains-mono",
         web=fontsource("jetbrains-mono")),
    Font("Maple Mono", ("MapleMono-Regular",), "Homebrew", "font-maple-mono",
         web=fontsource("maple-mono")),
    Font("Monaspace Neon", ("MonaspaceNeon-Regular",), "Homebrew", "font-monaspace",
         web=fontsource("monaspace-neon"),
         note="The cask also installs Argon, Krypton, Radon and Xenon."),
    Font("Roboto Mono", ("RobotoMono-Regular",), "Homebrew", "font-roboto-mono",
         web=fontsource("roboto-mono")),
    Font("Source Code Pro", ("SourceCodePro-Regular",), "Homebrew", "font-source-code-pro",
         web=fontsource("source-code-pro", "5.3.1")),
    Font("Ubuntu Mono", ("UbuntuMono-Regular",), "Homebrew", "font-ubuntu-mono",
         web=fontsource("ubuntu-mono")),
    Font("Victor Mono", ("VictorMono-Regular",), "Homebrew", "font-victor-mono",
         web=fontsource("victor-mono")),
)  # fmt: skip


def catalog():
    """Every font the tool knows: the default pool first, then the rest."""
    seeds = tuple(
        Font(
            SEED_FONT_LABELS[name],
            (name,),
            *SEED_SOURCES[name],
            files=() if SEED_SOURCES[name][0] == "Homebrew" else tuple(files),
            note=SEED_NOTES.get(name, ""),
        )
        for name, files in SEED_FONTS.items()
    )
    return seeds + OTHER_FONTS + HOMEBREW_FONTS


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def find(query, fonts=None):
    """The catalog fonts matching a label, cask, PostScript name or slug ('jetbrains-mono')."""
    fonts = catalog() if fonts is None else fonts
    key = slug(query).removeprefix("font-")
    by_label = [f for f in fonts if slug(f.label) == key or any(slug(n) == key for n in f.names)]
    if by_label:
        return by_label
    return [f for f in fonts if f.cask and slug(f.cask).removeprefix("font-") == key]


def suggestions(query, fonts=None):
    labels = [f.label for f in (catalog() if fonts is None else fonts)]
    return difflib.get_close_matches(query, labels, n=4, cutoff=0.4)


def source_text(font):
    return {
        "macOS": "macOS system",
        "Terminal.app": "Terminal.app (copied by setup.sh)",
    }.get(font.source, f"Homebrew: {font.cask}")


def help_text():
    """The --help list: every font on the page, all of which --add knows, grouped."""
    out = ["Fonts --add knows, all shown on the page (the label, cask or PostScript name works):"]
    groups = (
        ("Default pool fonts:", lambda f: f.names[0] in SEED_FONTS),
        (
            "On this Mac already, or installed by setup.sh (--add puts it in the pool):",
            lambda f: f.names[0] not in SEED_FONTS and not (f.cask and f.cask != MESLO_CASK),
        ),
        (
            "Homebrew (not installed by default; --add installs the cask, then adds it):",
            lambda f: f.cask and f.cask != MESLO_CASK,
        ),
    )
    for title, keep in groups:
        out.append(f"  {title}")
        for f in catalog():
            if keep(f):
                out.append(f"    {f.label:<26} {f.cask or source_text(f)}")
    return "\n".join(out)


def run_jxa(*args):
    out = subprocess.run(
        ["osascript", "-l", "JavaScript", JXA, *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    return out.strip()


def scan():
    """[(PostScript name, family, path, weight, italic)] of the fixed-width faces macOS has,
    each (name, path) once: SF Mono Terminal's italics share SF Mono's names."""
    seen, faces = set(), []
    for ps, family, path, weight, italic in json.loads(run_jxa("scan") or "[]"):
        if (ps, path) not in seen:
            seen.add((ps, path))
            faces.append((ps, family, path, int(weight), bool(italic)))
    return faces


def resolve(names, wait=RESOLVE_WAIT_S, sleep=time.sleep):
    """The first of names macOS can open, retrying up to wait seconds; '' if none. A new
    osascript each try: a running process doesn't see fonts installed after it started."""
    deadline = time.monotonic() + wait
    while True:
        found = run_jxa("resolve", *names)
        if found or time.monotonic() >= deadline:
            return found
        sleep(0.5)


def brew_path():
    """Homebrew's brew, from PATH only, as setup.sh finds it."""
    return shutil.which("brew")


def brew_out(brew_bin, *args):
    """brew's output; raises CalledProcessError if it fails."""
    env = {**os.environ, **BREW_ENV}
    return subprocess.run(
        [brew_bin, *args], capture_output=True, text=True, check=True, env=env
    ).stdout


def brew_run(brew_bin, *args):
    """Run brew with its output shown as it goes (installs take a while); its exit status."""
    return subprocess.run([brew_bin, *args], env={**os.environ, **BREW_ENV}, check=False).returncode


def installed_casks(brew_bin):
    return set(brew_out(brew_bin, "list", "--cask", "-1").split())


class CaskInfo(NamedTuple):
    paths: list  # where its font files are installed
    installed_time: str  # Homebrew's, '' if not installed


def cask_info(brew_bin, cask):
    data = json.loads(brew_out(brew_bin, "info", "--cask", "--json=v2", "--", cask))
    for entry in data.get("casks", []):
        if entry.get("token") != cask:
            continue
        paths = []
        for art in entry.get("artifacts", []):
            if isinstance(art, dict) and art.get("font"):
                paths.append(
                    art.get("target")
                    or os.path.join(
                        os.path.expanduser("~/Library/Fonts"), os.path.basename(art["font"][0])
                    )
                )
        stamp = entry.get("installed_time")
        return CaskInfo(paths, "" if stamp is None else str(stamp))
    return CaskInfo([], "")


def system_paths(font):
    """Where a non-Homebrew font's files are, in FONT_DIRS order."""
    out = []
    for name in font.files:
        for d in FONT_DIRS:
            path = os.path.join(os.path.expanduser(d), name)
            if os.path.exists(path):
                out.append(path)
                break
    return out


@contextlib.contextmanager
def editing_record():
    """The tool's record (srcp_schema.added_fonts_path) as a namespace with .casks
    ({cask: installed_time}) and .files ({PostScript name: [paths]}): read fresh under a lock,
    so two runs at once can't undo each other's changes, and saved on the way out."""
    path = added_fonts_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        casks, files = read_added_fonts(path)
        record = types.SimpleNamespace(casks=casks, files=files)
        yield record
        lines = [f"cask {c} {t}".rstrip() for c, t in record.casks.items()]
        lines += [f"file {n} {p}" for n, paths in record.files.items() for p in paths]
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".added-fonts-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("".join(line + "\n" for line in lines))
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise


def pool(db_path):
    """({name: (enabled, label)} of the fonts table, or None, and why not)."""
    if not os.path.exists(db_path):
        return None, f"No database at {db_path}, so the pool isn't shown. Build it with {REBUILD}."
    try:
        db = connect(db_path)
    except OldSchema as e:
        return None, f"The pool isn't shown: {e}."
    except sqlite3.Error as e:
        return None, f"The pool isn't shown: the database couldn't be read ({e})."
    try:
        rows = db.execute("SELECT name, enabled, label FROM fonts").fetchall()
    except sqlite3.Error as e:
        return None, f"The pool isn't shown: the database couldn't be read ({e})."
    finally:
        db.close()
    return {n: (e, label) for n, e, label in rows}, None


def add_command(font):
    tool = os.path.join(ROOT, ".tools", "srcp_fonts.py")
    return f'python3 "{tool}" --add "{font.label}"'


EMBED_MAX = 5_000_000  # bytes; bigger files (and collections) are drawn from a web copy, if any
EMBED_TYPES = {".ttf": "font/ttf", ".otf": "font/otf"}  # not .ttc: it would draw its first face


def page_fonts(faces, fonts_table, embed):
    """The page's cards, one per catalog font (the Meslo variants share one), and
    {card index: file} to embed: the regular face of pool fonts you installed yourself."""
    regular = {}
    for ps, family, path, _, italic in faces:
        if not italic:
            regular.setdefault(ps, (family, path))
    cards, embeds, grouped = [], {}, {}
    for font in catalog():
        if font.group:
            grouped.setdefault(font.group, []).append(font)
            continue
        card = _card([font], regular, fonts_table)
        cards.append(card)
        path = regular.get(card["ps"], ("", ""))[1]
        if embed and card["section"] == "pool" and embeddable(path):
            embeds[len(cards) - 1] = path
    for fonts in grouped.values():
        card = _card(fonts, regular, fonts_table)
        in_pool = [f.label for f in fonts if card_in_pool(f, fonts_table)]
        card["label"] = "MesloLG M / L / DZ Nerd Font Mono"
        card["note"] = (
            f"{_MESLO_NOTE} Variants: {', '.join(f.label for f in fonts)}"
            + (f"; in the pool: {', '.join(in_pool)}" if in_pool else "")
            + '. Add one with --add "<variant>".'
        )
        card["ps"] = ", ".join(f.names[0] for f in fonts)
        card["add"] = ""
        cards.append(card)
    return cards, embeds


def embeddable(path):
    if path.startswith("/System/") or os.path.splitext(path)[1].lower() not in EMBED_TYPES:
        return False
    try:
        return os.path.getsize(path) <= EMBED_MAX
    except OSError:
        return False


def card_in_pool(font, fonts_table):
    """True/False, or None when the pool is unknown; any of the font's names counts."""
    if fonts_table is None:
        return None
    return any(fonts_table.get(n, (0,))[0] == 1 for n in font.names)


def _card(fonts, regular, fonts_table):
    """One card for fonts (one font, or variants sharing a card): the first installed one."""
    font = next((f for f in fonts if any(n in regular for n in f.names)), fonts[0])
    ps = next((n for n in font.names if n in regular), font.names[0])
    installed = ps in regular
    pooled = [card_in_pool(f, fonts_table) for f in fonts]
    in_pool = None if fonts_table is None else any(pooled)
    section = "pool" if in_pool else ("inst" if installed else "miss")
    addable = in_pool is False and (installed or bool(font.cask))
    return {
        "label": font.label,
        "section": section,
        "installed": installed,
        "pool_known": fonts_table is not None,
        "source": source_text(font),
        "ps": ps,
        "add": add_command(font) if addable else "",
        "note": font.note,
        "family": regular[ps][0] if installed else f"W {font.label}",
        "web": list(font.web) if not installed and font.web else None,
        "embed": None,
    }


def render_page(cards, embeds, dbnote, now=None):
    now = now or dt.datetime.now().astimezone()
    blocks = []
    for i, path in sorted(embeds.items()):
        card = cards[i]
        card["family"] = f"E {card['label']}"
        card["embed"] = f"emb-{i}"
        mime = EMBED_TYPES[os.path.splitext(path)[1].lower()]
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        blocks.append(f'<script type="text/plain" id="emb-{i}" data-mime="{mime}">{data}</script>')
    data = {
        "made": f"Made {now:%Y-%m-%d %H:%M} on this Mac by .tools/srcp_fonts.py.",
        "dbnote": dbnote or "",
        "pool_known": all(c["pool_known"] for c in cards),
        "fonts": cards,
    }
    # Nothing in the data can end the script block or start markup: <, > and & as escapes.
    payload = (
        json.dumps(data, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    page = page.replace("<!--EMBEDS-->", "\n".join(blocks), 1)  # before the data goes in
    return page.replace("__DATA__", payload, 1)


CATALOG_INTRO = """\
# Monospace font inventory

<!-- Generated: python3 .tools/srcp_fonts.py --catalog > docs/MONOSPACE-FONT-INVENTORY.md
     (a test checks it matches; edit the tool, not this file). -->

The fixed-width fonts iTerm2 Profile Manager knows about, where each comes from, and how to
add one to the fonts it picks at random for new tabs.

## See the fonts on your Mac

```sh
python3 .tools/srcp_fonts.py
```

writes a page showing every font below side by side in the same sample text, and opens it in
your browser. Each one says whether it's in the pool, whether it's installed on your Mac, and
where it comes from; Homebrew ones you don't have yet are previewed from a web copy, where
there is one. Run it again any time to see the current state. `--no-open` and `--out PATH`
are in `--help`.

## Add or remove a font

```sh
python3 .tools/srcp_fonts.py --add "JetBrains Mono"
python3 .tools/srcp_fonts.py --remove "JetBrains Mono"
```

Homebrew fonts aren't installed by default. The one exception is MesloLGS Nerd Font:
`setup.sh` installs it with Homebrew, if Homebrew is there, because Oh My Zsh prompts need it.
`--add` installs a Homebrew font for you, then adds it to the pool; a font already on your Mac
is just added. `--help` lists every font below. Details: [REFERENCE.md](REFERENCE.md#fonts-tool).

## Catalog

Homebrew casks and PostScript names checked {checked}. Names come from each cask's own font
files; for variable fonts `--add` tries each name listed until macOS knows one.
"""


def catalog_doc():
    out = [CATALOG_INTRO.replace("{checked}", CHECKED)]
    out.append("| Font | Source | In the default pool | PostScript name | Add it |")
    out.append("|---|---|---|---|---|")
    for f in catalog():
        names = " or ".join(f"`{n}`" for n in f.names)
        seed = "yes" if f.names[0] in SEED_FONTS else ""
        add = "" if seed else f'`--add "{f.label}"`'
        out.append(f"| {f.label} | {source_text(f)} | {seed} | {names} | {add} |")
    return "\n".join(out) + "\n"


def stdout_table(cards):
    width = max(len(c["label"]) for c in cards)
    for c in cards:
        state = "installed" if c["installed"] else "not installed"
        if c["section"] == "pool":
            state = "in the pool"
        elif not c["pool_known"]:
            state += " (pool unknown)"
        print(f"  {c['label']:<{width}}  {state:<28} {c['source']}")


def project_clone(path):
    """The clone of this project (any one, like setup.sh's check) that path is in, or ''."""
    folder = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    while True:
        git = os.path.join(folder, ".git")
        tool = os.path.join(folder, ".tools", "srcp_build_scheme_db.py")
        if os.path.exists(git) and os.path.isfile(tool):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return ""
        folder = parent


def write_page(args):
    clone = project_clone(args.out)
    if clone and not args.no_embed:
        sys.exit(
            f"Not writing {args.out}: it's inside {clone}, a clone of this project, and the page "
            "carries copies of fonts (some licensed by Apple) that must not be committed. Use "
            "--no-embed, or another --out."
        )
    if not os.path.isdir(os.path.dirname(os.path.abspath(args.out))):
        sys.exit(f"Not writing {args.out}: its folder doesn't exist.")
    try:
        faces = scan()
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        sys.exit(f"macOS's list of fonts couldn't be read ({e}).")
    fonts_table, dbnote = pool(args.db)
    if dbnote:
        print(f"[warn] {dbnote}")
    cards, embeds = page_fonts(faces, fonts_table, not args.no_embed)
    page = render_page(cards, embeds, dbnote)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    stdout_table(cards)
    print(f"\nPage: {args.out}")
    if embeds:
        print("It carries copies of your pool fonts (some are Apple's): keep it to yourself.")
    if not args.no_open:
        subprocess.run(["open", args.out], check=False)


def say(dryrun, would, done):
    """Print what a dry run would do, or what was done (done='' prints nothing)."""
    text = f"[dryrun] would {would}" if dryrun else done
    if text:
        print(text)


def need_brew(font):
    brew_bin = brew_path()
    if not brew_bin:
        sys.exit(
            f"{font.label} comes from Homebrew, and brew isn't on PATH. Install Homebrew "
            "(https://brew.sh), then run this again."
        )
    return brew_bin


def install_cask(font, args):
    """Install font's cask unless it's there; the paths of its font files ([] in a dry run
    of an install). A cask this run installs goes in the record, with Homebrew's install time,
    before anything else changes; one you already had never does."""
    brew_bin = need_brew(font)
    if font.cask in installed_casks(brew_bin):
        print(f"{font.cask} is already installed.")
        return cask_info(brew_bin, font.cask).paths
    env = " ".join(f"{k}=1" for k in BREW_ENV)
    say(args.dryrun, f"run: {env} brew install --cask -- {font.cask}", f"Installing {font.cask}:")
    if args.dryrun:
        return []
    if brew_run(brew_bin, "install", "--cask", "--", font.cask) != 0:
        sys.exit(f"brew couldn't install {font.cask}; nothing else was changed.")
    if font.cask == MESLO_CASK:
        return cask_info(brew_bin, font.cask).paths
    with editing_record() as record:  # first, so a failing brew info can't lose it
        record.casks[font.cask] = ""
    info = cask_info(brew_bin, font.cask)
    with editing_record() as record:
        record.casks[font.cask] = info.installed_time
    return info.paths


ADD_SQL = (
    "INSERT INTO fonts (name, label) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET "
    "enabled = 1, missing = 0, label = COALESCE(fonts.label, excluded.label)"
)


def add(font, args):
    with db_for_writing(args.db):
        pass  # the DB is there and fits, before anything is installed
    if font.cask:
        paths = install_cask(font, args)
        if args.dryrun and not paths:
            names = " or ".join(font.names)
            print(f"[dryrun] would check that macOS knows {names}, then add it to the pool")
            return
    else:
        paths = system_paths(font)
        if not paths:
            sys.exit(f"{font.label} isn't on this Mac ({', '.join(font.files)} not found).")
    name = resolve(font.names, wait=0 if args.dryrun else RESOLVE_WAIT_S)
    if not name:
        sys.exit(
            f"macOS doesn't know a font named {' or '.join(font.names)} (yet), so it wasn't "
            "added to the pool. Try again in a moment; if it still fails, the cask may have "
            "renamed it."
        )
    if args.dryrun:
        print(f"[dryrun] would run on {args.db}: {ADD_SQL} -- {name!r}, {font.label!r}")
        return
    if name not in SEED_FONTS:
        with editing_record() as record:
            record.files[name] = paths
    with db_for_writing(args.db) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            before = db.execute("SELECT enabled FROM fonts WHERE name = ?", (name,)).fetchone()
            db.execute(ADD_SQL, (name, font.label))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    if before and before[0] == 1:
        print(f"{font.label} ({name}) was already in the pool.")
    elif before:
        print(f"{font.label} ({name}) is back in the pool (it was turned off).")
    else:
        print(f"Added {font.label} ({name}) to the pool. New tabs can use it now, no restart.")


def profiles_using(names, load=load_prefs):
    """iTerm2 profiles whose font or non-ASCII font is one of names, or None if the settings
    can't be read."""
    try:
        prefs = load(timeout=20)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return [
        str(b.get("Name", "?"))
        for b in prefs.get("New Bookmarks", [])
        if any(str(b.get(k, "")).split(" ")[0] in names for k in ("Normal Font", "Non Ascii Font"))
    ]


def take_out(font, args):
    """Turn every enabled row of font's names off. Exits for a DB that doesn't fit; with no
    DB, says so and carries on, so a cask can still be uninstalled."""
    if not os.path.exists(args.db):
        print(f"No database at {args.db}, so nothing to take out of the pool.")
        return
    marks = ",".join("?" * len(font.names))
    with db_for_writing(args.db) as db:
        on = [
            n
            for (n,) in db.execute(
                f"SELECT name FROM fonts WHERE enabled = 1 AND name IN ({marks})",  # noqa: S608  (placeholders only)
                font.names,
            )
        ]
        if not on:
            print(f"{font.label} isn't in the pool.")
            return
        say(
            args.dryrun,
            f"take {font.label} ({', '.join(on)}) out of the pool",
            f"Took {font.label} ({', '.join(on)}) out of the pool.",
        )
        if not args.dryrun:
            db.executemany("UPDATE fonts SET enabled = 0 WHERE name = ?", [(n,) for n in on])


def face_names(paths):
    """The PostScript names of every face in these font files (not only fixed-width ones)."""
    return set(json.loads(run_jxa("faces", *paths) or "[]"))


def cask_in_use(font, paths, args):
    """Why the cask's fonts may still be in use ('' if they aren't): a favorite, another pool
    row, the icon font or an iTerm2 profile uses one of its faces, or that can't be told."""
    if not paths:
        return "Homebrew lists no font files for it, so what uses it can't be checked"
    try:
        names = face_names(paths)
    except (subprocess.SubprocessError, OSError, ValueError):
        return "macOS's list of its fonts couldn't be read"
    if not names:
        return "macOS couldn't list the fonts in its files"
    names |= set(font.names)
    reason = db_uses(names, args.db)
    if not reason:
        profiles = profiles_using(names)
        if profiles is None:
            reason = "iTerm2's settings couldn't be read to check its profiles"
        elif profiles:
            reason = f"iTerm2 profile(s) use its fonts: {', '.join(profiles)}"
    return reason


def db_uses(names, db_path):
    """Why the DB still uses one of names (favorites, an enabled pool row, the icon font), or ''."""
    if not os.path.exists(db_path):
        return ""
    marks = ",".join("?" * len(names))
    with db_for_writing(db_path) as db:
        favorites = db.execute(
            f"SELECT count(*) FROM favorites WHERE font IN ({marks})",  # noqa: S608  (placeholders only)
            sorted(names),
        ).fetchone()[0]
        pooled = [
            n
            for (n,) in db.execute(
                f"SELECT name FROM fonts WHERE enabled = 1 AND name IN ({marks})",  # noqa: S608  (placeholders only)
                sorted(names),
            )
        ]
        icon = db.execute("SELECT value FROM config WHERE key = 'nonascii_font'").fetchone()
    if favorites:
        return f"{favorites} favorite(s) were saved with its fonts"
    if pooled:
        return f"its fonts are still in the pool: {', '.join(pooled)}"
    if icon and icon[0] in names:
        return f"the icon font (config nonascii_font) is {icon[0]}"
    return ""


def uninstall_cask(font, args):
    """Uninstall font's cask, only if this tool installed it (and it hasn't been reinstalled
    since), it isn't the icon font's, and nothing uses its fonts."""
    if not font.cask:
        return
    if font.cask == MESLO_CASK:
        print(f"{font.cask} stays installed: the icon font comes from it.")
        return
    # Read without the lock: two --remove runs at once can both get past this, and the second
    # brew uninstall then just fails; the record itself only changes under the lock below.
    stamp = read_added_fonts()[0].get(font.cask)
    if stamp is None:
        print(f"{font.cask} stays installed: this tool didn't install it.")
        return
    brew_bin = brew_path()
    if not brew_bin:
        print(f"brew isn't on PATH, so {font.cask} stays installed (and in the record).")
        return
    info = cask_info(brew_bin, font.cask)
    by_hand = f"To remove it anyway: brew uninstall --cask {font.cask}"
    if not info.installed_time:
        say(args.dryrun, f"forget {font.cask} (already uninstalled)", "")
    elif not stamp:
        say(
            args.dryrun,
            f"forget {font.cask}, which stays installed (its install time wasn't recorded)",
            f"{font.cask} stays installed: its install time wasn't recorded, so this tool can't "
            f"tell it's still the copy it installed. {by_hand}",
        )
    elif info.installed_time != stamp:
        say(
            args.dryrun,
            f"forget {font.cask}, which stays installed (reinstalled since this tool did)",
            f"{font.cask} stays installed: it was reinstalled since this tool installed it.",
        )
    else:
        reason = cask_in_use(font, info.paths, args)
        if reason:
            print(f"{font.cask} stays installed: {reason}. {by_hand}")
            return
        say(args.dryrun, f"run: brew uninstall --cask -- {font.cask}", f"Uninstalling {font.cask}:")
        if not args.dryrun and brew_run(brew_bin, "uninstall", "--cask", "--", font.cask):
            print(f"brew couldn't uninstall {font.cask}; it stays in the record.")
            return
    if not args.dryrun:
        with editing_record() as record:
            record.casks.pop(font.cask, None)


def remove(font, args):
    take_out(font, args)
    if not args.dryrun:
        with editing_record() as record:
            for n in font.names:
                record.files.pop(n, None)
    uninstall_cask(font, args)


@contextlib.contextmanager
def db_for_writing(path):
    """srcp_favorites.connect (mode=rw, schema checked), closed after; exits with a message
    for a missing, outdated or busy DB."""
    if not os.path.exists(path):
        sys.exit(f"No database at {path}. Build it with {REBUILD}.")
    try:
        db = connect(path)
    except OldSchema as e:
        sys.exit(f"The DB doesn't fit this version: {e}.")
    except sqlite3.DatabaseError as e:
        sys.exit(f"The DB at {path} couldn't be opened ({e}).")
    try:
        yield db
    except sqlite3.OperationalError as e:
        if is_busy(e):
            sys.exit(f"The DB is busy ({e}). Try again in a moment.")
        raise
    finally:
        db.close()


def parser():
    ap = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
        epilog=help_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--add", metavar="FONT", help="add FONT to the pool (installing it if needed)")
    ap.add_argument("--remove", metavar="FONT", help="take FONT out of the pool")
    ap.add_argument("--dryrun", "--dry-run", action="store_true", help="show, change nothing")
    ap.add_argument("--catalog", action="store_true", help="print the catalog doc")
    default_out = os.path.join(tempfile.gettempdir(), "iterm2-font-inventory.html")
    ap.add_argument("--out", default=default_out, help=f"page path (default {default_out})")
    ap.add_argument("--no-open", action="store_true", help="don't open the page")
    ap.add_argument(
        "--no-embed", action="store_true", help="don't carry copies of your own fonts in the page"
    )
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    if args.add and args.remove:
        sys.exit("Use --add or --remove, not both.")
    if args.catalog:
        sys.stdout.write(catalog_doc())
        return
    query = args.add or args.remove
    if not query:
        if args.dryrun:
            sys.exit("--dryrun goes with --add or --remove.")
        write_page(args)
        return
    matches = find(query)
    if not matches:
        near = suggestions(query)
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        sys.exit(f"{query!r} isn't in the catalog.{hint} --help lists every font.")
    if len(matches) > 1:
        sys.exit(f"{query!r} matches {', '.join(f.label for f in matches)}; name one.")
    (font,) = matches
    try:
        if args.add:
            add(font, args)
        else:
            remove(font, args)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip().splitlines()[-1:] or [f"exit status {e.returncode}"]
        sys.exit(f"{' '.join(map(str, e.cmd[:3]))} failed: {detail[0]}")
    except subprocess.TimeoutExpired as e:
        sys.exit(f"{' '.join(map(str, e.cmd[:3]))} took too long.")
    except ValueError as e:  # brew info's JSON
        sys.exit(f"Homebrew's answer couldn't be read ({e}).")


if __name__ == "__main__":
    main()
