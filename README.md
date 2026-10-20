# iterm2-profile-manager

Every new iTerm2 tab, window or split pane gets a random color preset that matches the macOS light/dark setting, and a random monospaced font. You rate the looks you like from 1 to 5 stars. Rated looks come up more often, and each one becomes an iTerm2 profile (e.g. "Kibble - Menlo") under **Profiles → Favorites**.

Presets come from [iTerm2-Color-Schemes](https://github.com/mbadolato/iTerm2-Color-Schemes) (815 schemes). Presets with hard-to-read colors are left out.

## Requirements

| Need | Why | How |
|---|---|---|
| macOS | Fonts are copied from Terminal.app. `setup.sh` exits on other systems. | |
| iTerm2 | Runs the scripts. Tested on 3.7.6. | [iterm2.com](https://iterm2.com) |
| iTerm2 Python API | Lets scripts control iTerm2. | Settings → General → Magic → **Enable Python API** |
| iTerm2 Python runtime | The main script runs on iTerm2's own Python 3.12. | Scripts → Manage → **Install Python Runtime** |
| Theme set to Automatic or Minimal | Only these themes follow the macOS light/dark setting. | Settings → Appearance → Theme |
| Xcode Command Line Tools | Provides `/usr/bin/python3` (3.9+) and `git`. | `xcode-select --install` |
| Homebrew (optional) | Installs MesloLGS Nerd Font, for shell prompt icons. Without it, that font is skipped. | [brew.sh](https://brew.sh) |

No pip packages are needed: the tools use only Python's standard library. The first install needs network access to clone the schemes repo.

## Install

### 1. Get the code

iTerm2 only runs scripts from `~/Library/Application Support/iTerm2/Scripts/`, and starts everything in its `AutoLaunch/` subfolder when iTerm2 launches. You can clone the repo anywhere and let `setup.sh` copy the scripts there (recommended):

```sh
git clone git@github.com:rmarable/iterm2-profile-manager.git ~/src/iterm2-profile-manager
cd ~/src/iterm2-profile-manager
```

Or clone it straight into the Scripts folder. This only works if that folder is missing or empty. Edits then take effect without copying, but every `.py` file in the repo shows up in iTerm2's Scripts menu.

```sh
git clone git@github.com:rmarable/iterm2-profile-manager.git ~/Library/Application\ Support/iTerm2/Scripts
```

### 2. Run setup

`setup.sh` changes iTerm2's settings file, which iTerm2 overwrites when it quits. So quit iTerm2 (⌘Q), open **Terminal.app**, `cd` to the repo, and run:

```sh
./setup.sh --dryrun         # show every step without changing anything (safe with iTerm2 open)
./setup.sh                  # the schemes repo is cloned into ~/src
SRC_DIR=~/code ./setup.sh   # or somewhere else
```

It does the following. Running it again is safe.

1. Copies Terminal.app's fonts (SF Mono, SF Mono Terminal) into `~/Library/Fonts`. These are Apple-licensed fonts from your own Mac; never commit them.
2. Installs MesloLGS Nerd Font with Homebrew, if Homebrew is present.
3. Clones or updates iTerm2-Color-Schemes in `$SRC_DIR/iTerm2-Color-Schemes`.
4. Imports its `schemes/*.itermcolors` files as iTerm2 color presets.
5. Builds the scheme database (see [How a look is picked](#how-a-look-is-picked)).
6. Binds ⌃⌥⌘S to the save dialog.
7. Copies the scripts into the Scripts folder (skipped if the repo is the Scripts folder).

Before each change to iTerm2's settings, the old settings are backed up to `~/Library/Preferences/com.googlecode.iterm2.backup-<timestamp>.plist`.

### 3. Relaunch iTerm2

The new fonts become available and the main script starts. To confirm it's running, open **Scripts → Manage → Console**: it shows `srcp_set_random_color_profile <version>`, and one line per new session, such as `[info] dark discovery: Nord | Menlo-Regular 13`.

## Usage

### Rate a look

Right-click in a session and choose **Save/update this profile**, or press ⌃⌥⌘S. The dialog shows the session's preset, font and how many times this look was launched.

| Button | Effect |
|---|---|
| **1 ★** … **5 ★★★★★** | Saves the preset and font as a favorite with that many stars. Choosing again changes the stars. |
| **Remove favorite** | Shown only for a favorite. Deletes the favorite; the preset goes back into the random rotation. |
| **Never show** | Never picks this preset again, and deletes its favorites. Rating it later undoes this. |
| **Cancel** | Does nothing. |

Right-click → **Remove from favorites** does the same as **Remove favorite** without opening the dialog. In iTerm2 3.7.6 this item appears on every tab, because hiding it needs a newer iTerm2 (Python API protocol 1.23, in nightly builds since 2026-10-12). On a tab that isn't a favorite it says so and changes nothing. Once iTerm2 supports hiding, the script hides it on its own.

Changes apply to the next new session.

### Favorite profiles

Each favorite becomes a profile named `<preset> - <font>`, listed under **Profiles → Favorites**. A session opened from one keeps that look instead of getting a random one.

The script writes these profiles to `~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist` and iTerm2 picks up changes on its own. Each profile is based on your default profile, so it keeps your other settings (triggers, key bindings, etc.). Changes you make to one in Settings are lost the next time the file is rewritten; to change a favorite, open it, adjust, and rate it again. Deleting the file removes all of them until the script next starts.

### Review your ratings

In iTerm2, **Scripts → srcp_ratings.py** opens a window with your favorites. The **History** button shows the last 50 rating changes, and **Hidden** shows presets marked Never show. You can select and copy text from it.

From a terminal:

```sh
python3 .tools/srcp_favorites.py                          # favorites: stars, launches, last used, average time open
python3 .tools/srcp_favorites.py --mode dark --min-stars 4
python3 .tools/srcp_favorites.py --history                # last 30 rating changes (--history 100 for more)
python3 .tools/srcp_favorites.py --hidden                 # Never show list, with how to undo it
```

```
Stars  Preset          Font    Mode  Launches  Last used         Avg open  Saved
-----  --------------  ------  ----  --------  ----------------  --------  ----------
5 ★    Selenized Dark  Monaco  dark  1         2026-10-17 21:03  2m 15s    2026-10-17
```

Rating history was first recorded on 2026-10-17 (database schema version 3); earlier changes weren't kept.

## How a look is picked

For each new session, the script:

1. **Skips sessions opened from a favorite profile.** They keep their look; the launch is still recorded.
2. **Reads the light/dark setting** from iTerm2. With the Automatic or Minimal theme, this follows macOS. Open sessions aren't recolored when the setting changes.
3. **Chooses a favorite or a new preset.** The chance of a favorite grows with the number of favorite presets in this mode: `favorite_share × min(1, favorite presets / favorite_target)`, so 50% once you have 10 or more. With no favorites it always picks a new preset.
   - **Favorite:** weighted by stars (a 5 ★ comes up 5 times as often as a 1 ★), with the font it was saved with.
   - **New preset:** any preset of this mode that passes the readability checks below and isn't a favorite, except the last 20 new presets picked in this mode. Presets with a colored background (yellow, pink, teal, … rather than white, grey or black) are picked 3 times as often as plain ones; there are few of them (12 of 168 light, 90 of 611 dark), so this raises their share to about 19% of light tabs and 34% of dark tabs. The font is random.
4. **Applies the preset and font** to this session only, at the profile's font size. It also sets MesloLGS Nerd Font as the font for non-ASCII characters, so shell prompt icons (Powerlevel10k etc.) display with any main font.
5. **Records the launch** in the database, and the time it's closed. Sessions still open when iTerm2 quits are marked `unknown` at the next start.

### Light or Dark Mode Selection

A preset counts as light if its background is brighter than its text. If the schemes repo labels it (a `variant:` line in `yaml/<name>.yml`), that label wins. The two methods agree on all 78 labeled schemes. You can override either (see [Configuration](#configuration)).

### Skipping Hard-to-Read Presets

A preset is picked at random only if:

| Check | Rule |
|---|---|
| Text | Text-to-background contrast is at least 4.5 (the WCAG AA standard). |
| Colors visible | At least 4 of the 6 main colors (ANSI red, green, yellow, blue, magenta, cyan) have contrast of at least 2 against the background. |
| Colors distinct | At least 5 of those 6 colors are clearly different from each other (at least 0.04 apart in OKLab). |

Monochrome presets (one hue, or nearly no color) only need the Text check.

Result as of 2026-10-17: 824 presets installed, 181 light and 643 dark; 167 light and 614 dark pass.

Some installed presets are older, byte-identical spellings of a repo scheme (e.g. `base16 icy` and `base16-icy`). Only the repo spelling is picked.

## Configuration

Settings live in the database. Edit them with `sqlite3`; changes apply to the next new session.

```sh
sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db
```

| `config` key | Default | Meaning |
|---|---|---|
| `favorite_share` | `0.5` | Highest chance of picking a favorite. |
| `favorite_target` | `10` | Number of favorite presets (per mode) at which that chance is reached. |
| `recent_window` | `20` | New presets don't repeat within this many picks. |
| `colorful_weight` | `3` | How many times as often a preset with a colored background (OKLab chroma ≥ 0.05) is picked. `1` treats all presets equally. |
| `nonascii_font` | `MesloLGSNFM-Regular` | Font for icons and other non-ASCII characters. `''` leaves the profile's setting alone. |

```sql
update config set value = '0.7' where key = 'favorite_share';
update config set value = '1' where key = 'colorful_weight';
update config set value = '' where key = 'nonascii_font';

-- fonts: name is the PostScript name; label is used in profile names
update fonts set enabled = 0 where name = 'Monaco';
insert into fonts (name, label) values ('JetBrainsMonoNL-Regular', 'JetBrains Mono');

-- force a preset in or out of the rotation, or set its mode
insert or replace into overrides (name, include) values ('Zenburn', 1);
insert or replace into overrides (name, mode) values ('Some Scheme', 'dark');
```

Default fonts: SF Mono, Menlo, Monaco, MesloLGS Nerd Font Mono. Fonts that aren't installed start disabled.

## Backups

The database is `~/.local/share/iterm2-color-schemes/schemes.db`. Snapshots are saved to `backups/` in the same folder:

- before every database rebuild,
- when the main script starts, if the newest snapshot is more than a day old.

The newest 14 are kept. The preset data can always be rebuilt; the snapshots protect your favorites, hidden presets, launch history and settings.

**Take a snapshot by hand** (safe any time):

```sh
cd ~/.local/share/iterm2-color-schemes
sqlite3 schemes.db ".backup backups/manual-$(date -u +%Y%m%dT%H%M%SZ).db"
```

Don't `cp` the database while iTerm2 runs: recent changes may still be in `schemes.db-wal`.

**Restore a snapshot:**

```sh
# Quit iTerm2 first.
cd ~/.local/share/iterm2-color-schemes
ls -t backups/                      # newest first
rm -f schemes.db-wal schemes.db-shm
cp backups/schemes-<timestamp>.db schemes.db.new && mv schemes.db.new schemes.db
python3 <repo>/.tools/srcp_build_scheme_db.py   # update it to match the installed presets
```

### Export your ratings

Snapshots stay on this Mac. To keep your ratings elsewhere, or move them to another Mac, export them:

```sh
python3 .tools/srcp_export_curation.py -o ~/curation.sql
```

The file is a few KB of SQL containing your favorites, rating history, hidden presets, settings and fonts: preset names, font names, stars and dates, nothing else. Launch history isn't included.

To load it into a database (on another Mac, after running `setup.sh`):

```sh
sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db < ~/curation.sql
```

Matching rows are updated and other rows are kept. Loading the same file twice doesn't duplicate history. Fonts disabled in this database stay disabled. If this database is older than the export, nothing is changed; rebuild it first with `.tools/srcp_build_scheme_db.py`.

Where to keep the export (not in this repo; `curation*.sql` is gitignored):

| Option | How | Notes |
|---|---|---|
| Private git repo, e.g. your dotfiles (recommended) | Copy the file in, commit, push. | Access control and full history. |
| Secret gist | `gh gist create curation.sql` | Anyone with the URL can read it. |
| iCloud Drive | `-o ~/Library/Mobile\ Documents/com~apple~CloudDocs/iterm2-curation.sql` | Syncs to your other Macs. |
| Password manager | Attach the file to an item. | Encrypted, manual updates. |

## Uninstall

Quit iTerm2, then from Terminal.app in the repo:

```sh
./uninstall.sh --dryrun        # show what would be removed (safe with iTerm2 open)
./uninstall.sh                 # remove the defaults below
./uninstall.sh --all           # remove everything setup.sh added
```

| What | Removed by |
|---|---|
| The main script (renamed to `.py.disabled` if the repo is the Scripts folder) | default |
| The menu scripts copied into the Scripts folder | default |
| The favorite profiles file | default |
| The ⌃⌥⌘S key binding (iTerm2 settings are backed up first) | default |
| The database, snapshots and launch history. Your ratings are exported to `~/srcp-curation-<time>.sql` first. | `--data` |
| Color presets that are unchanged copies of the repo's files | `--presets` |
| The copied Terminal.app fonts and the MesloLGS Homebrew package | `--fonts` |
| The iTerm2-Color-Schemes clone | `--repo` |

It doesn't touch the repo itself, iTerm2 settings backups, or your shell config. Sessions already open keep their colors until closed. Running `setup.sh` again turns the main script back on.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Console: `no scheme DB at ...` | Run `python3 .tools/srcp_build_scheme_db.py`. No restart needed. |
| Console: `preset not found: X` | The preset was deleted or renamed in iTerm2. Rebuild the database. |
| Most presets show your default profile's colors | In Settings → Profiles → Colors, turn off "Use separate colors for light and dark mode". Few presets define both. |
| Always light, or always dark | Set Settings → Appearance → Theme to Automatic or Minimal. |
| Console: `another copy is already running; exiting` | Normal after a restart; only one copy runs. |
| A font never appears | Check `select * from fonts`: it's disabled or not installed. Relaunch iTerm2 after installing fonts. |
| A favorite is missing from the Profiles menu | Its preset isn't installed, or it's marked Never show. |
| ⌃⌥⌘S does nothing | Check Settings → Keys → Key Bindings for `save_profile()`, and that the script is running (Scripts → Manage → Console). |
| Console: `database is locked` | Another program held the database for over 5 seconds (a rebuild, or an open `sqlite3` transaction). That launch isn't recorded. |
| Build: `schemes.db is missing but its -wal/-shm files are still there` | Delete the two leftover files, or restore a snapshot. |
| A tool says iTerm2 is running | Quit iTerm2 and run it from Terminal.app. |

## Reference

### Files

| Path | Purpose |
|---|---|
| `AutoLaunch/srcp_set_random_color_profile.py` | The main script. Starts with iTerm2, picks each session's look, records launches, provides the rating dialog and right-click items, writes the favorite profiles, takes daily snapshots. |
| `srcp_ratings.py` | Scripts menu: the ratings window. |
| `srcp_score_presets.py` | Scripts menu: ranks presets by saturation. A report only; not used for picking. |
| `setup.sh`, `uninstall.sh` | Install and uninstall. |
| `.tools/` | Command-line tools. The leading dot keeps them out of iTerm2's Scripts menu. |
| `.tools/srcp_build_scheme_db.py` | Labels each preset light or dark, runs the readability checks, writes the database. |
| `.tools/srcp_import_presets.py` | Imports a folder of `.itermcolors` files without a dialog per file. |
| `.tools/srcp_add_key_binding.py` | Binds ⌃⌥⌘S. |
| `.tools/srcp_favorites.py` | Ratings report. |
| `.tools/srcp_popup.js` | The ratings window (used by `srcp_ratings.py`). |
| `.tools/srcp_export_curation.py` | Exports ratings and settings as SQL. |
| `.tools/srcp_uninstall_prefs.py` | Removes the key binding and presets from iTerm2's settings (used by `uninstall.sh`). |
| `.tools/srcp_iterm_prefs.py`, `.tools/srcp_db_backup.py` | Shared helpers: iTerm2 settings access, snapshots. |
| `.tools/srcp_enable_claude_integration.py` | Optional; see below. |
| `lint.sh`, `pyproject.toml`, `AutoLaunch/ruff.toml` | Lint and type checks. |

### Manual install

Instead of `setup.sh`:

```sh
git clone https://github.com/mbadolato/iTerm2-Color-Schemes.git ~/src/iTerm2-Color-Schemes
python3 .tools/srcp_import_presets.py ~/src/iTerm2-Color-Schemes/schemes   # iTerm2 quit; --overwrite replaces existing presets
python3 .tools/srcp_build_scheme_db.py                                     # safe with iTerm2 open
python3 .tools/srcp_add_key_binding.py                                     # iTerm2 quit
mkdir -p ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch
cp AutoLaunch/srcp_set_random_color_profile.py ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch/
```

Every tool that writes takes `--dryrun`. To add the key binding by hand instead: Settings → Keys → Key Bindings → **+**, shortcut ⌃⌥⌘S, action **Invoke Script Function**, function `save_profile()`.

Double-clicking an `.itermcolors` file also imports it, but shows a dialog for each of the 815 files. The schemes repo's own `tools/import-scheme.sh` renames some presets and needs bash 4+.

### Database tables

| Table or view | Contents |
|---|---|
| `schemes` | One row per installed preset: mode, how the mode was decided, contrast and color scores, whether it passes, duplicate spelling, background colorfulness (`bg_chroma`). Rebuilt by `srcp_build_scheme_db.py`. |
| `overrides` | `name`, `mode` (force light/dark), `include` (1 = always pick, 0 = never). Starts with 6 presets that narrowly fail the checks, forced in. |
| `config` | Settings (see [Configuration](#configuration)). |
| `fonts` | `name` (PostScript name), `enabled`, `label`. |
| `favorites` | `preset`, `font`, `size`, `stars`, `saved_at`. One row per preset and font. |
| `ratings` | Every save, removal and Never show: `at`, `preset`, `font`, `action`, `stars`, `previous_stars`. Never cleared. |
| `launches` | One row per session: `launched_at`, `preset`, `font`, `size`, `mode`, `source` (favorite or discovery), `stars`, `ended_at`, `duration_s`, `end_reason`. |
| `effective` (view) | `schemes` with `overrides` applied. |
| `discovery` (view) | Presets eligible for random picks, with `colorful` (1 = colored background). |
| `favorite_pool` (view) | Favorites that can be picked, with their mode. |
| `launch_stats` (view) | Per preset: launch counts, first and last use, time open, stars. |

Times are stored in UTC. A rebuild replaces only `schemes` and the four views; everything else, including views you add, is kept.

```sql
select mode, count(*) from discovery group by mode;
select * from launch_stats order by launches desc limit 10;
select preset, font, launched_at, duration_s from launches order by id desc limit 20;
select name, fg_contrast, accent_contrast, distinct_accents from schemes where passes_gate = 0;
select * from favorites where preset not in (select name from schemes);  -- favorites whose preset is gone
```

### Claude Code integration (optional)

`setup.sh` doesn't do this. To turn on iTerm2's Claude Code integration from the command line, quit iTerm2 and run `python3 .tools/srcp_enable_claude_integration.py [--dryrun]` from Terminal.app. It makes the same changes iTerm2 3.7.6 makes when you enable it in the app:

- links `~/.config/iterm2/cc-status` to the helper inside iTerm.app,
- adds a `cc-status` hook for 10 Claude Code events to `~/.claude/settings.json` (other settings kept, old file backed up),
- adds two triggers per iTerm2 profile that track a running `claude` job,
- sets the two iTerm2 settings that record the above.

It doesn't touch iTerm2's AI chat settings or API key. Set `ITERM_APP` if iTerm2 isn't in `/Applications`.

### Development

```sh
./lint.sh         # ruff format --check, ruff check, pyright, shellcheck
./lint.sh --fix   # apply formatting and safe fixes first
```

Needs [uv](https://docs.astral.sh/uv/) and `brew install shellcheck`. Settings are in `pyproject.toml`: Black style, 100 columns. `.tools/` targets Python 3.9 (`/usr/bin/python3`); everything else targets 3.12 (iTerm2's Python), and pyright checks the main script against the real `iterm2` module.

The scripts only run inside iTerm2. Test database changes on a copy (`--db /tmp/test.db`) and use `--dryrun` for everything else.
