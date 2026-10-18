# iterm2-profile-manager

iTerm2 Python API scripts that give every new tab, window or split pane a color scheme matching the macOS light/dark appearance, plus a random monospaced font. Looks you like can be starred: starred looks come back more often, and each becomes an iTerm2 profile ("Kibble - Menlo") you can open from the Profiles menu.

## Prerequisites

| Need | Why | How |
|---|---|---|
| macOS | Fonts come from Terminal.app; `setup.sh` exits elsewhere. | |
| iTerm2 | Runs the AutoLaunch script. Tested on 3.7.6; the right-click item needs Python API protocol 1.7+. | [iterm2.com](https://iterm2.com) |
| iTerm2 Python API | Lets scripts talk to iTerm2. | Settings → General → Magic → **Enable Python API** |
| iTerm2 Python runtime | The script runs on iTerm2's own Python 3.12 and `iterm2` module. | Scripts → Manage → **Install Python Runtime** |
| Theme Automatic or Minimal | Only these follow the macOS light/dark setting. | Settings → Appearance → Theme |
| Xcode Command Line Tools | `/usr/bin/python3` (3.9+) and `git`. | `xcode-select --install` |
| Network | Cloning the schemes repo, first run or update. | |
| Homebrew (optional) | Installs MesloLGS Nerd Font. Without it, that font stays disabled. | [brew.sh](https://brew.sh) |

No pip packages: the tools use only the standard library.

## Where the code goes

iTerm2 runs scripts only from `~/Library/Application Support/iTerm2/Scripts/`, and starts anything in its `AutoLaunch/` folder when iTerm2 launches. `~/.config/iterm2/AppSupport` is a symlink to `~/Library/Application Support/iTerm2`, so both paths reach the same folder.

**Clone anywhere (recommended):**

```sh
git clone git@github.com:rmarable/iterm2-profile-manager.git ~/src/iterm2-profile-manager
cd ~/src/iterm2-profile-manager
```

`setup.sh` copies `AutoLaunch/srcp_set_random_color_profile.py`, `srcp_score_presets.py`, `srcp_ratings.py` and `.tools/srcp_favorites.py` and `.tools/srcp_popup.js` (used by `srcp_ratings.py`) into the Scripts folder. The other `.tools/` commands stay in the checkout and run from there.

**Or clone into the Scripts folder itself**, if it doesn't exist yet or is empty (`git clone` refuses a non-empty folder):

```sh
git clone git@github.com:rmarable/iterm2-profile-manager.git ~/Library/Application\ Support/iTerm2/Scripts
```

Edits then take effect without copying, but every `.py` in the checkout shows up in iTerm2's Scripts menu.

## Files

| Path | What it does |
|---|---|
| `AutoLaunch/srcp_set_random_color_profile.py` | Starts with iTerm2. Colors each new session from the scheme DB, logs every launch, snapshots the DB daily, adds "Save/update this profile" and "Remove from favorites" to the right-click menu and the `save_profile()` key function, and keeps a profile per favorite in `~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist`. |
| `.tools/srcp_build_scheme_db.py` | Tags every installed preset light or dark, runs the readability gate, writes `~/.local/share/iterm2-color-schemes/schemes.db`, after snapshotting the old one. |
| `srcp_ratings.py` | Scripts menu: favorites, rating history or hidden presets in a native popup (fixed-width, scrollable, selectable). |
| `.tools/srcp_popup.js` | The popup: an AppKit alert in Menlo, run with `osascript -l JavaScript`. |
| `.tools/srcp_favorites.py` | Lists favorites with stars and use, rating history, and hidden presets. |
| `.tools/srcp_export_curation.py` | Exports favorites, rating history, overrides, config and fonts as SQL, for keeping off this Mac. |
| `.tools/srcp_import_presets.py` | Imports a folder of `.itermcolors` files as presets, with no dialog per file. |
| `.tools/srcp_add_key_binding.py` | Binds ⌃⌥⌘S to `save_profile()`. |
| `.tools/srcp_enable_claude_integration.py` | Optional, run by hand: turns on iTerm2's Claude Code integration (iTerm2 3.7+). Not part of `setup.sh`. |
| `.tools/srcp_iterm_prefs.py`, `.tools/srcp_db_backup.py` | Shared helpers: `defaults` read/write with backup; DB snapshots. |
| `.tools/` | Command-line tools. The leading dot keeps them out of iTerm2's Scripts menu, which can't pass them arguments. |
| `srcp_score_presets.py` | Report only: ranks presets by saturation. Not used for selection. |
| `setup.sh` | Runs the whole install. |
| `uninstall.sh` | Undoes it (see Uninstall). |
| `.tools/srcp_uninstall_prefs.py` | Uninstall's prefs part: removes the key binding and, optionally, unedited repo presets, in one backed-up write. |
| `lint.sh`, `pyproject.toml`, `AutoLaunch/ruff.toml` | Format, lint and type checks (see Development). |

## Install

Quit iTerm2 (⌘Q), open **Terminal.app**, `cd` to the checkout, and run:

```sh
./setup.sh --dryrun         # preview every step; safe with iTerm2 open
./setup.sh                  # schemes repo goes in ~/src
SRC_DIR=~/code ./setup.sh   # or elsewhere
```

In order, it:

1. Copies Terminal.app's bundled fonts (SF Mono, SF Mono Terminal) from `/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts/` into `~/Library/Fonts`. A changed file is re-copied. These are Apple-licensed fonts from your own macOS install; never commit them.
2. Installs MesloLGS Nerd Font with `brew install --cask font-meslo-lg-nerd-font` if Homebrew exists and the font is missing. A failure is reported and skipped.
3. Clones or updates [iTerm2-Color-Schemes](https://github.com/mbadolato/iTerm2-Color-Schemes) into `$SRC_DIR/iTerm2-Color-Schemes`. A failed `git pull` keeps the existing checkout.
4. Imports every `schemes/*.itermcolors` as a preset (`.tools/srcp_import_presets.py`).
5. Builds the scheme DB (`.tools/srcp_build_scheme_db.py`).
6. Binds ⌃⌥⌘S (`.tools/srcp_add_key_binding.py`).
7. Copies the scripts into the Scripts folder (unless the checkout is that folder) and removes the retired `AutoLaunch/random_profile_color.py`.

Every step is idempotent. `setup.sh` refuses to start while iTerm2 runs, because steps 4 and 6 write iTerm2's prefs and iTerm2 rewrites them when it quits. Each prefs write is backed up first to `~/Library/Preferences/com.googlecode.iterm2.backup-<timestamp>.plist`.

Then relaunch iTerm2: the new fonts appear and the AutoLaunch script starts.

## Manual install

```sh
git clone https://github.com/mbadolato/iTerm2-Color-Schemes.git ~/src/iTerm2-Color-Schemes
python3 .tools/srcp_import_presets.py ~/src/iTerm2-Color-Schemes/schemes   # iTerm2 quit; --overwrite replaces existing
python3 .tools/srcp_build_scheme_db.py                                     # safe with iTerm2 open
python3 .tools/srcp_add_key_binding.py                                     # iTerm2 quit
mkdir -p ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch
cp AutoLaunch/srcp_set_random_color_profile.py ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch/
```

The tools that write (`srcp_import_presets.py`, `srcp_build_scheme_db.py`, `srcp_add_key_binding.py`, `srcp_uninstall_prefs.py`, `srcp_enable_claude_integration.py`) take `--dryrun` (or `--dry-run`). Every tool works under `python3 -I`.

Key binding by hand instead: Settings → Keys → Key Bindings → **+**, shortcut ⌃⌥⌘S, action **Invoke Script Function**, function `save_profile()`. If `GlobalKeyMap` is unset, `srcp_add_key_binding.py` first copies iTerm2's default bindings from `iTerm.app/Contents/Resources/DefaultGlobalKeyMap.plist` so they aren't lost.

Why not double-click each `.itermcolors`? iTerm2 shows a dialog per file. `srcp_import_presets.py` stores the same thing (name = file name, contents verbatim). The repo's `tools/import-scheme.sh` renames some presets (e.g. "Birds Of Paradise" → "Birds of Paradise") and needs bash 4+.

## How a session is chosen

0. **Favorite profiles keep their look.** A session opened from a favorite's profile (Profiles menu) isn't recolored; it's logged and recorded as a favorite launch.
1. **Mode.** `app.async_get_theme()` returns iTerm2's effective theme. With the Automatic or Minimal theme, that follows the macOS appearance. The script tests for `dark`; anything else is light. Open sessions aren't recolored when the appearance changes.
2. **Favorite or discovery.** Probability of a favorite = `favorite_share × min(1, favorited presets in this mode / favorite_target)`, so a handful of favorites doesn't take over. The ramp counts presets, not (preset, font) pairs. No favorites → always discovery; no discovery candidates → favorites.
   - **Favorite:** weighted by stars (5 ★ = 5× a 1 ★), with the font it was saved with, or a random enabled font if that one has since been disabled. A preset saved with several fonts has one weighted entry per font.
   - **Discovery:** uniform over the `discovery` view for this mode, skipping the last `recent_window` discovery launches in that mode, with a random enabled font.
3. **Apply.** Sets the preset and the font face on the session's profile copy. The size stays the profile's (13 pt by default). It also turns on the non-ASCII font and sets it to `config.nonascii_font` (MesloLGS Nerd Font Mono) at the same size, so Powerlevel10k and other Nerd Font icons render whatever the main font is. A preset that no longer exists is skipped and another picked, up to 3 tries.
4. **Record.** Sets session variables `user.color_preset`, `user.font`, `user.font_size`, `user.mode`, `user.is_favorite` and inserts a `launches` row. When the session closes, the row gets `ended_at`, `duration_s` and `end_reason='closed'`. iTerm2 holds that event while closing can still be undone, so it lands a few seconds late. Sessions still open when iTerm2 quits get `end_reason='unknown'` on the next start.

At startup the Console shows `[info] srcp_set_random_color_profile <version> (file modified <time>), pid <n>`.  `[info] dark discovery: Nord | Menlo-Regular 13` or `[info] light favorite 4 ★: ...`.

### Light or dark

A preset is light if its background is brighter (relative luminance) than its foreground. A `variant:` line in the repo's `yaml/<name>.yml` takes precedence; the two agree on all 78 labeled schemes. `overrides.mode` overrides both.

### Readability gate

A preset is in `discovery` if all hold:

| Check | Rule |
|---|---|
| Text | fg/bg contrast ≥ 4.5 (WCAG AA) |
| Accents | at least 4 of ANSI 1-6 have contrast ≥ 2 against the bg |
| Distinct | the largest set of ANSI 1-6 that are all ≥ 0.04 apart in OKLab has at least 5 colors |

Monochrome themes (fewer than 2 accents with OKLab chroma > 0.02, or all of them within a 40° hue arc) skip the Accents and Distinct checks. There's no ranking: soft palettes are as likely as vivid ones. `overrides.include` beats the gate either way. Presets that are byte-identical older spellings of a repo scheme (e.g. `base16 icy` / `base16-icy`) are left out of discovery, even if included; include the repo spelling instead (the build warns).

As of 2026-10-17: 824 presets, 181 light / 643 dark, 167 / 614 pass the gate.

### Database

`~/.local/share/iterm2-color-schemes/schemes.db` (WAL mode). A rebuild replaces only `schemes` and the four views below; your own views and every other table are kept.

| Object | Contents |
|---|---|
| `schemes` | Derived per preset: `mode`, `mode_source` (`yaml`/`luminance`), `fg_contrast`, `accent_contrast`, `distinct_accents`, `monochrome`, `passes_gate`, `in_repo`, `color_hash`, `duplicate_of`. |
| `overrides` | `name`, `mode` (force light/dark), `include` (1 = always in, 0 = never). Seeded with 6 near-misses from the old rotation. |
| `config` | `favorite_share` (0.5), `favorite_target` (10), `recent_window` (20): a non-number falls back to the default, with a warning. `nonascii_font` (`MesloLGSNFM-Regular`): PostScript name of the icon font; `''` leaves the profile's non-ASCII font alone. |
| `fonts` | `name` (PostScript), `enabled`, `label` (used in profile names; defaults to the name minus `-Regular`). SF Mono, Menlo, Monaco, MesloLGS Nerd Font Mono; uninstalled ones start disabled. |
| `favorites` | `preset`, `font`, `size`, `stars` (1-5), `saved_at`; keyed on (preset, font). |
| `ratings` | Rating history, never wiped: `at`, `preset`, `font`, `action` (`save`/`remove`/`never_show`), `stars`, `previous_stars`. One row per save, removal, or favorite removed by Never show. Times are UTC. |
| `launches` | One row per colored session: `launched_at`, `preset`, `font`, `size`, `mode`, `source`, `stars`, `iterm_profile`, `session_id`, `ended_at`, `duration_s`, `end_reason`. Times are UTC. `mode` is the theme the preset was picked for; for a tab opened from a favorite's profile, the preset's own mode. |
| `effective` (view) | `schemes` with overrides applied. |
| `discovery` (view) | `name`, `mode` of discovery candidates (excludes favorited presets). |
| `favorite_pool` (view) | Favorites with their effective mode, minus `include=0`. |
| `launch_stats` (view) | Per preset: `launches`, first/last, favorite vs discovery counts, total/avg duration, `stars`. |

## Saving favorites

Right-click in a session → **Save/update this profile**, or press ⌃⌥⌘S. The dialog shows the preset, the font the script applied, and its launch count, with these buttons:

- **1 ★ … 5 ★★★★★:** save (preset, font) with that rating. Re-saving updates the stars.
- **Remove favorite** (shown only when this preset and font are a favorite): deletes that favorite. The preset goes back into discovery, and the rating is gone; saving it again starts fresh. The `ratings` history keeps the old rating (`previous_stars`). Removing something that isn't a favorite does nothing and logs nothing.
- **Never show:** sets `overrides.include=0` and deletes the preset's favorites.
- **Cancel**

Right-click → **Remove from favorites** does the same as the dialog's Remove favorite, without the dialog. iTerm2 shows it only on sessions whose preset and font are a favorite (`user.is_favorite`).

Every favorite is also an iTerm2 profile named `<preset> - <font label>` (e.g. `Kibble - Menlo`), listed under **Profiles → Favorites** (the profile tag `Favorites`). They live in one Dynamic Profiles file, `~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist`, which the script rewrites at startup, after every save, removal or Never show, and at the next new tab after favorites change any other way (sqlite3, a restored export); iTerm2 updates the Profiles menu on its own. Each profile is a child of your default profile (it inherits everything else, triggers included) with the preset's colors, the saved font, and the `nonascii_font` icon font. Edits made to these profiles in Settings aren't written back to the file, so they're lost the next time iTerm2 reloads the folder; to change one, save the look again. Starring a preset you'd marked Never show unhides it. Delete the file to remove them all (it's recreated at the next start while favorites exist).

Changes take effect on the next new session; no restart needed.

### Reviewing ratings

From iTerm2: **Scripts → srcp_ratings.py** opens a popup with the favorites table below, in Menlo (SF Mono has no ★). It scrolls and you can select and copy from it. **History** (last 50 changes) and **Hidden** switch views; **Done** closes it. Times are local. From a terminal:

```sh
python3 .tools/srcp_favorites.py                          # favorites, best first, with launches, last used, avg time open
python3 .tools/srcp_favorites.py --mode dark --min-stars 4
python3 .tools/srcp_favorites.py --history                # last 30 rating changes (--history 100 for more)
python3 .tools/srcp_favorites.py --hidden                 # Never show list, with how to unhide
```

```
Stars  Preset          Font    Mode  Launches  Last used         Avg open  Saved
-----  --------------  ------  ----  --------  ----------------  --------  ----------
5 ★    Selenized Dark  Monaco  dark  1         2026-10-17 21:03  2m 15s    2026-10-17
```

`favorites` holds only the current rating. Every change is also kept in `ratings`, from when the DB reached schema version 3 (2026-10-17); earlier changes weren't recorded.

## Backups

Snapshots go to `~/.local/share/iterm2-color-schemes/backups/schemes-<UTC timestamp>.db`, made with SQLite's online backup, so they're consistent while the script has the DB open:

- `srcp_build_scheme_db.py` snapshots the existing DB before every rebuild.
- The AutoLaunch script snapshots at startup when the newest snapshot is more than 24 hours old.
- The newest 14 are kept; older ones are deleted.

`schemes` and the views can always be rebuilt. The snapshots protect what can't: `favorites`, `overrides`, `launches`, and your `config`/`fonts` edits.

**Manual snapshot** (any time):

```sh
cd ~/.local/share/iterm2-color-schemes
sqlite3 schemes.db ".backup backups/manual-$(date -u +%Y%m%dT%H%M%SZ).db"
```

Don't `cp` a live `schemes.db`: in WAL mode, recent writes may still be in `schemes.db-wal`.

**Restore:**

```sh
# Quit iTerm2 first, so nothing has the DB open.
cd ~/.local/share/iterm2-color-schemes
ls -t backups/                      # newest first
rm -f schemes.db-wal schemes.db-shm
cp backups/schemes-<timestamp>.db schemes.db.new && mv schemes.db.new schemes.db  # new file: a running script notices
python3 <checkout>/.tools/srcp_build_scheme_db.py   # refresh schemes against the installed presets
```

Snapshots stay on this Mac (and in Time Machine, if `~/.local` isn't excluded). For an off-Mac copy of what matters, use the export below.

### Exporting your curation

```sh
python3 .tools/srcp_export_curation.py -o ~/curation.sql   # or no -o for stdout
```

The export is a few KB of `INSERT OR REPLACE` statements for `favorites`, `ratings`, `overrides`, `config` and `fonts`. Launch history and the derived `schemes` table are left out. It contains preset names, font names, star ratings and when they changed, nothing else.

Restore it into a built DB, on this Mac or a new one (after `setup.sh`):

```sh
sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db < ~/curation.sql
```

Favorites, overrides and config are replaced by key, so restoring over an existing DB updates matching rows and keeps the rest. Rating history is appended (restoring the same export twice adds nothing). A font is never re-enabled if this DB has it disabled, since it may not be installed here. The export stops before changing anything if this DB has an older schema than the export: rebuild it first.

### Where to keep the export

Don't commit it to this repo: the checkout is shared code, and the export is your personal taste. `curation*.sql` is gitignored.

| Option | Setup | Update | Notes |
|---|---|---|---|
| **Private git repo** (recommended; e.g. your dotfiles) | Copy the file in, commit. | Re-export, commit, push. | Real access control and history of every change. |
| **Secret gist** | `gh gist create curation.sql -d "iterm2-profile-manager curation"` (secret is the default) | `git clone https://gist.github.com/<id>.git`, copy the new export over, commit, push. | Unlisted, not private: anyone with the URL can read it. Fine for star ratings; don't add anything sensitive. |
| **iCloud Drive** | `python3 .tools/srcp_export_curation.py -o ~/Library/Mobile\ Documents/com~apple~CloudDocs/iterm2-curation.sql` | Same command. | Syncs to your other Macs; no history beyond iCloud's own. |
| **Password manager** (1Password, Bitwarden: attach as a file) | Attach `curation.sql` to an item. | Replace the attachment. | Encrypted, but manual. |

To keep it current, re-export after a session of starring. Or schedule it with a `launchd` agent that runs `srcp_export_curation.py -o <path>` daily.

## SQL cookbook

```sh
sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db
```

```sql
select mode, count(*) from discovery group by mode;
select * from favorite_pool order by stars desc;
select * from launch_stats order by launches desc limit 10;
select preset, font, launched_at, duration_s from launches order by id desc limit 20;

update config set value = '0.7' where key = 'favorite_share';
update fonts set enabled = 0 where name = 'Monaco';
update config set value = '' where key = 'nonascii_font';      -- stop setting the icon font
insert into fonts values ('JetBrainsMonoNL-Regular', 1);           -- PostScript name
insert or replace into overrides (name, include) values ('Zenburn', 1);
insert or replace into overrides (name, mode) values ('Some Scheme', 'dark');
select name, fg_contrast, accent_contrast, distinct_accents from schemes where passes_gate = 0;

-- favorites whose preset is no longer installed (never picked; delete or reinstall)
select * from favorites where preset not in (select name from schemes);
```

## Uninstall

Quit iTerm2, then from Terminal.app in the checkout:

```sh
./uninstall.sh --dryrun        # preview; safe with iTerm2 open
./uninstall.sh                 # the defaults below
./uninstall.sh --all           # everything setup.sh added
```

| What | Default | Flag |
|---|---|---|
| AutoLaunch script: installed copy deleted (renamed `.py.disabled` if this checkout is the Scripts folder) | yes | |
| `srcp_` menu scripts copied into the Scripts folder | yes | |
| Favorites' Dynamic Profiles file | yes | |
| ⌃⌥⌘S `save_profile()` key binding (prefs backed up first) | yes | |
| Scheme DB, snapshots, launch history. A curation export is saved to `~/srcp-curation-<time>.sql` first | | `--data` |
| Color presets identical to the repo's `.itermcolors` files; edited and other presets stay, and so do ones the repo changed since you imported them (`setup.sh` doesn't overwrite existing presets) | | `--presets` |
| Terminal.app fonts identical to Terminal's copies, and the MesloLGS Nerd Font cask (warns if a profile still uses it) | | `--fonts` |
| The `iTerm2-Color-Schemes` clone (only if its origin is the upstream repo) | | `--repo` |

It never touches the checkout itself, prefs backups, the Claude Code integration or your shell config. If `setup.sh` created `GlobalKeyMap` from iTerm2's defaults, that copy stays (only the ⌃⌥⌘S entry goes). Running `setup.sh` again re-enables a script `uninstall.sh` disabled. Sessions already colored keep their colors until closed.

## Claude Code integration (optional)

`setup.sh` doesn't run this. If you want it, quit iTerm2 and run `python3 .tools/srcp_enable_claude_integration.py [--dryrun]` from Terminal.app. It reproduces what iTerm2 3.7.6 installs when the integration is turned on in the app:

- `~/.config/iterm2/cc-status` → the helper inside `iTerm.app`
- a `cc-status` hook on 10 Claude Code events in `~/.claude/settings.json` (other settings kept, old file backed up)
- two triggers per iTerm2 profile that group a `claude` job's activity
- the two prefs flags recording that hooks and triggers are installed

It never reads or writes iTerm2's AI chat/API settings (provider, model, URL, API key). A later iTerm2 may set things up differently. Set `ITERM_APP` if iTerm2 isn't in `/Applications`.

## Development

```sh
./lint.sh         # ruff format --check, ruff check, pyright, shellcheck
./lint.sh --fix   # apply ruff format and safe fixes first
```

Needs [uv](https://docs.astral.sh/uv/) (ruff and pyright run through pinned `uvx`) and `brew install shellcheck`. Settings are in `pyproject.toml`: Black-style formatting at 100 columns; `.tools/` targets Python 3.9 (`/usr/bin/python3`); `AutoLaunch/` (its own `AutoLaunch/ruff.toml`), `srcp_score_presets.py` and `srcp_ratings.py` target 3.12 (iTerm2's runtime). Pyright skips dot-folders, so `lint.sh` type-checks a temporary copy of `.tools/`. Pyright reads the `iterm2` module's types from iTerm2's runtime, so the script is type-checked against the real API.

The scripts can't run outside iTerm2. Test DB changes against a copy with `--db /tmp/test.db`; dry-run everything else.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[warn] no scheme DB at ...` | Run `python3 .tools/srcp_build_scheme_db.py`. No restart needed. |
| `[warn] preset not found: X` | Preset deleted or renamed in iTerm2. Rebuild the DB. |
| Most presets show your Default profile's colors | Default has Settings → Profiles → Colors → "Use separate colors for light and dark mode" on; almost no preset defines both sets. Turn it off. |
| Always light (or dark) | Settings → Appearance → Theme must be Automatic or Minimal to follow macOS. |
| `another copy is already running; exiting` | Expected on a restart; the first copy holds `~/.local/share/iterm2-color-schemes/.lock`. |
| A font never shows up | `select * from fonts`: it's disabled or not installed. Relaunch iTerm2 after installing fonts. |
| A favorite isn't in the Profiles menu | Its preset isn't installed (`[warn] favorite ... isn't an installed preset`), or it's marked Never show. Check `~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist`. |
| ⌃⌥⌘S does nothing | Check Settings → Keys → Key Bindings for `save_profile()`, and that the AutoLaunch script is running (Scripts → Manage → Console). |
| Build: `schemes.db is missing but its -wal/-shm files are still there` | Delete the two leftover files, or restore a backup. |
| `[warn] DB ...: database is locked` | Another writer held the DB over 5 s (a long rebuild or a `sqlite3` session in a transaction). Finish it; that launch isn't recorded. |
| A tool says iTerm2 is running | Quit iTerm2 and run it from Terminal.app; iTerm2 rewrites its prefs on quit. |
