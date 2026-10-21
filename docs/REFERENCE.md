# Reference

Details behind the [README](../README.md). The main script is `AutoLaunch/srcp_set_random_color_profile.py`; it runs inside iTerm2 and picks each session's look (color preset + font).

## Rating dialog

Right-click a session → **Save/update this profile**, or press ⌃⌥⌘S. The rating dialog shows the preset, mode, font, current stars, and the preset's launch count and last launch (any font). Changes apply to the next new session.

| Button | Effect |
|---|---|
| **1 ★** … **5 ★★★★★** | Saves preset + font as a favorite with that many stars. Rate again to change. |
| **Remove favorite** | Favorites only. Deletes this favorite. The preset returns to discovery once no font of it is a favorite. |
| **Never show** | Excludes the preset from all picks and deletes its favorites. Rating it again undoes this. |
| **Cancel** | Closes the dialog. |

Right-click → **Remove from favorites** does the same as **Remove favorite**, without the dialog. iTerm2 3.7.6 shows it on every session and, on a non-favorite, says so and changes nothing. iTerm2 with Python API protocol 1.23+ hides it on non-favorites; the script uses that when available.

## Favorite profiles

Each favorite becomes a profile named `<preset> - <font>` under **Profiles → Favorites**. Sessions opened from one keep that look.

The main script writes these profiles to a Dynamic Profiles file (see [Files on disk](#files-on-disk)); iTerm2 reloads it automatically. Each profile is based on your default profile, so it keeps your other settings (triggers, key bindings, etc.). Edits made to one in Settings are lost when the file is rewritten. A favorite is one preset + font: changing the font in a session doesn't change what the rating dialog saves (it uses the font the script applied). To keep a preset with another font, rate it from a tab that shows it in that font; to drop the old one, open it from **Profiles → Favorites** and choose **Remove favorite**. If the file is deleted, it comes back at the next script start or favorite change.

## Review ratings

**Scripts → srcp_ratings.py** lists your favorites in a window. **History** shows the last 50 rating changes; **Hidden** shows **Never show** presets. Text is selectable.

From the repo:

```sh
python3 .tools/srcp_favorites.py                          # favorites: stars, launches, last used, average time open
python3 .tools/srcp_favorites.py --mode dark --min-stars 4
python3 .tools/srcp_favorites.py --history                # last 30 rating changes; --history N for more
python3 .tools/srcp_favorites.py --hidden                 # Never show presets and how to undo
```

```text
Stars  Preset          Font    Mode  Launches  Last used         Avg open  Saved
-----  --------------  ------  ----  --------  ----------------  --------  ----------
5 ★    Selenized Dark  Monaco  dark  1         2026-10-17 21:03  2m 15s    2026-10-17
```

## How a look is picked

For each new session, the main script:

1. **Skips sessions opened from a favorite profile.** They keep their look; the launch is still logged.
2. **Reads the mode** from iTerm2 (follows macOS with the Automatic or Minimal theme). Open sessions keep their colors when the mode changes.
3. **Chooses a favorite or a discovery preset.** Chance of a favorite: `favorite_share × min(1, favorite presets in this mode / favorite_target)`. With defaults that is 5% per favorite preset, capped at 50% from 10 on. No favorites means discovery every time.
   - **Favorite:** weighted by stars per saved preset + font (a 5 ★ is picked 5× as often as a 1 ★; a preset saved with three fonts has three chances), in its saved font. If that font has since been disabled, a random one.
   - **Discovery:** a preset of this mode that passes the readability checks, isn't a favorite, and isn't among the last 20 discovery picks in this mode. Colored-background presets (yellow, pink, teal rather than white, grey or black) get 3× weight. They are rare, so this lifts them to roughly a fifth of light and a third of dark sessions. Random font.
4. **Applies the preset and font** to this session only, at the profile's font size. Also sets the non-ASCII font to MesloLGS Nerd Font (`nonascii_font`), so prompt icons (Powerlevel10k etc.) render with any font.
5. **Logs the launch**, and the close time when the session ends. Sessions open when iTerm2 quits get `end_reason = 'unknown'` at the next start.

### Light or dark

Where the schemes repo labels a preset (`variant:` in `yaml/<name>.yml`), the label wins. Otherwise a preset is light if its background is brighter than its text. Override per preset with `overrides.mode` (see [Configuration](#configuration)).

### Readability checks

Discovery uses only presets that pass all three:

| Check | Rule |
|---|---|
| Text | Text contrast ≥ 4.5 against the background (WCAG AA). |
| Colors visible | ≥ 4 of ANSI red, green, yellow, blue, magenta, cyan have contrast ≥ 2 against the background. |
| Colors distinct | ≥ 5 of those 6 are all ≥ 0.04 apart in OKLab. |

Monochrome presets (one hue, or near-grey) need only the Text check. `overrides.include` forces a preset in or out.

A few installed presets are byte-identical copies under an older name (`base16 icy` = `base16-icy`), and the repo has a few identical twins of its own (`Zenbones` = `Zenbones Light`). Discovery uses only one name for each: the repo's, and the first in sort order.

## Configuration

Settings live in the database's `config` table. Edit with `sqlite3`; changes apply to the next new session.

```sh
sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db
```

| `config` key | Default | Meaning |
|---|---|---|
| `favorite_share` | `0.5` | Maximum chance of picking a favorite. |
| `favorite_target` | `10` | Favorite presets per mode needed to reach `favorite_share`. |
| `recent_window` | `20` | Discovery picks before a preset can repeat. |
| `colorful_weight` | `3` | Discovery weight of colored-background presets (OKLab chroma ≥ 0.05). `1` = no preference. |
| `nonascii_font` | `MesloLGSNFM-Regular` | Font for icons and other non-ASCII characters. `''` keeps the profile's setting. |

Default fonts: SF Mono, Menlo, Monaco, MesloLGS Nerd Font Mono. Fonts that aren't installed start disabled and stay disabled after you install them; enable one with `update fonts set enabled = 1 where name = '<name>'`.

```sql
update config set value = '0.7' where key = 'favorite_share';
update config set value = '1' where key = 'colorful_weight';
update config set value = '' where key = 'nonascii_font';

-- fonts: name = PostScript name, label = name shown in profile names
update fonts set enabled = 0 where name = 'Monaco';
insert into fonts (name, label) values ('JetBrainsMonoNL-Regular', 'JetBrains Mono');

-- force a preset in or out of discovery, or set its mode
insert into overrides (name, include) values ('Zenburn', 1)
  on conflict (name) do update set include = excluded.include;
insert into overrides (name, mode) values ('Some Scheme', 'dark')
  on conflict (name) do update set mode = excluded.mode;
```

## Backups

Snapshots of the database go to `backups/` beside it:

- Before every rebuild
- At main-script start, if the newest snapshot is over a day old

The newest 14 are kept; `manual-*.db` snapshots are never pruned. Preset data can be rebuilt from iTerm2's settings; snapshots protect favorites, ratings, hidden presets, launches and settings.

### Manual snapshot

Safe any time:

```sh
cd ~/.local/share/iterm2-color-schemes
sqlite3 schemes.db ".backup backups/manual-$(date -u +%Y%m%dT%H%M%SZ).db"
```

Don't `cp` the database while iTerm2 runs: recent changes may still be in `schemes.db-wal`.

### Restore

Quit iTerm2, then:

```sh
cd ~/.local/share/iterm2-color-schemes
ls -t backups/                      # newest first
rm -f schemes.db-wal schemes.db-shm
cp backups/schemes-<timestamp>.db schemes.db.new && mv schemes.db.new schemes.db
python3 <repo>/.tools/srcp_build_scheme_db.py   # resync with installed presets
```

### Export ratings

Snapshots stay on this Mac. To back up ratings elsewhere or move them to another Mac:

```sh
python3 .tools/srcp_export_curation.py -o ~/curation.sql
```

Output: a few KB of SQL with favorites, rating history, hidden presets, settings and fonts (names, stars and dates only). Launches aren't exported.

Import (e.g. on another Mac after `setup.sh`):

```sh
sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db < ~/curation.sql
```

Matching rows are updated, others kept; re-importing doesn't duplicate history. Fonts disabled in the target stay disabled, and fonts it doesn't have yet arrive disabled (enable them once they're installed). If the target database's schema is older than the export's, nothing is imported: run `.tools/srcp_build_scheme_db.py` first.

Where to keep it (not this repo; `curation*.sql` is gitignored):

| Option | How | Notes |
|---|---|---|
| Private git repo, e.g. dotfiles | Copy the file in, commit, push. | Recommended. Access-controlled, versioned. |
| Secret gist | `gh gist create curation.sql` | Unlisted, not private: anyone with the URL can read it. |
| iCloud Drive | `python3 .tools/srcp_export_curation.py -o ~/Library/Mobile\ Documents/com~apple~CloudDocs/iterm2-curation.sql` | Syncs to your other Macs. |
| Password manager | Attach the file to an item. | Encrypted; manual updates. |

## Claude Code integration (optional)

Not run by `setup.sh`. Enables iTerm2's Claude Code integration without the app UI, making the same changes iTerm2 3.7.6 does. Quit iTerm2, then from Terminal.app:

```sh
python3 .tools/srcp_enable_claude_integration.py --dryrun   # preview
python3 .tools/srcp_enable_claude_integration.py
```

It:

- Links `~/.config/iterm2/cc-status` to the helper inside iTerm.app
- Adds a `cc-status` hook for 10 Claude Code events to `~/.claude/settings.json` (backs up the file; other settings kept)
- Adds two triggers per iTerm2 profile that track a running `claude` job
- Sets the two iTerm2 flags that record this

It doesn't touch iTerm2's AI chat settings or API key. `uninstall.sh` doesn't undo it: remove the `cc-status` hooks from `~/.claude/settings.json` (or restore its backup) and turn the integration off in iTerm2.

## Install options

### Custom schemes folder

`SRC_DIR=~/code ./setup.sh` clones the schemes repo into `~/code` instead of `~/src`. Set `SRC_DIR` again for later rebuilds and for `uninstall.sh --repo`; the database build reads the repo's light/dark labels from there.

### Clone into the Scripts folder

iTerm2 runs scripts only from `~/Library/Application Support/iTerm2/Scripts/`, and starts everything in its `AutoLaunch/` folder at launch. Instead of letting `setup.sh` copy the scripts there, you can clone straight into it (it must be missing or empty). Edits and `git pull` then apply with no copy step; restart the main script from the Scripts menu after pulling.

```sh
git clone https://github.com/rmarable/iterm2-profile-manager.git ~/Library/Application\ Support/iTerm2/Scripts
```

### Manual install

Equivalent to `setup.sh`, minus fonts:

```sh
git clone https://github.com/mbadolato/iTerm2-Color-Schemes.git ~/src/iTerm2-Color-Schemes
python3 .tools/srcp_import_presets.py ~/src/iTerm2-Color-Schemes/schemes   # iTerm2 must be quit; --overwrite also replaces presets you've edited
python3 .tools/srcp_build_scheme_db.py                                     # safe with iTerm2 open
python3 .tools/srcp_add_key_binding.py                                     # iTerm2 must be quit
mkdir -p ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch
cp AutoLaunch/srcp_set_random_color_profile.py ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch/
cp srcp_ratings.py srcp_score_presets.py ~/Library/Application\ Support/iTerm2/Scripts/
mkdir -p ~/Library/Application\ Support/iTerm2/Scripts/.tools
cp .tools/srcp_favorites.py .tools/srcp_popup.js ~/Library/Application\ Support/iTerm2/Scripts/.tools/
```

Every tool that writes takes `--dryrun`. Manual key binding: Settings → Keys → Key Bindings → **+**, shortcut ⌃⌥⌘S, action **Invoke Script Function**, function `save_profile()`.

Double-clicking an `.itermcolors` file also imports it, with one dialog per file.

## Files

### Files on disk

| Path | Contents |
|---|---|
| `~/.local/share/iterm2-color-schemes/schemes.db` | The database. |
| `~/.local/share/iterm2-color-schemes/backups/` | Database snapshots. |
| `~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist` | Favorite profiles. |
| `~/.local/share/iterm2-profile-manager/prefs-backups/com.googlecode.iterm2-<timestamp>.plist` | iTerm2 settings backups, newest 10 kept. Restore one with iTerm2 quit: `defaults import com.googlecode.iterm2 <file>`. Versions before 0.6.7 wrote them to `~/Library/Preferences/com.googlecode.iterm2.backup-*.plist`; those can be deleted. |
| `$SRC_DIR/iTerm2-Color-Schemes` (default `~/src`) | Schemes repo clone. |
| `~/Library/Fonts` | Copied Terminal.app fonts. |

### Repo files

| Path | Purpose |
|---|---|
| `AutoLaunch/srcp_set_random_color_profile.py` | Main script. Starts with iTerm2; picks each session's look, logs launches, provides the rating dialog and right-click menu items, writes favorite profiles, takes daily snapshots. |
| `srcp_ratings.py` | Scripts menu: ratings window. |
| `srcp_score_presets.py` | Scripts menu: ranks presets by saturation (report only). |
| `setup.sh`, `uninstall.sh` | Install and uninstall. |
| `.tools/` | Command-line tools. The leading dot keeps them out of iTerm2's Scripts menu. |
| `.tools/srcp_build_scheme_db.py` | Sets each preset's mode, runs the readability checks, writes the database. |
| `.tools/srcp_import_presets.py` | Imports a folder of `.itermcolors` files without a dialog per file. |
| `.tools/srcp_add_key_binding.py` | Binds ⌃⌥⌘S. |
| `.tools/srcp_favorites.py` | Ratings report (CLI). |
| `.tools/srcp_popup.js` | Draws the ratings window for `srcp_ratings.py`. |
| `.tools/srcp_export_curation.py` | Exports ratings and settings as SQL. |
| `.tools/srcp_uninstall_prefs.py` | Removes the key binding and presets from iTerm2's settings (used by `uninstall.sh`). |
| `.tools/srcp_iterm_prefs.py`, `.tools/srcp_db_backup.py`, `.tools/srcp_preset_history.py` | Shared helpers: iTerm2 settings access, snapshots, past versions of the repo's presets. |
| `.tools/srcp_enable_claude_integration.py` | See [Claude Code integration](#claude-code-integration-optional). |
| `lint.sh`, `pyproject.toml`, `AutoLaunch/ruff.toml` | Lint and type checks. |

## Database

| Table or view | Contents |
|---|---|
| `schemes` | One row per installed preset: `mode`, `mode_source`, `fg_contrast`, `accent_contrast`, `distinct_accents`, `monochrome`, `passes_gate`, `duplicate_of`, `bg_chroma`. Rebuilt by `srcp_build_scheme_db.py`. |
| `overrides` | `name`, `mode` (force light/dark), `include` (1 = always pick, 0 = never). Seeded with 6 near-miss presets forced in. |
| `config` | Settings (see [Configuration](#configuration)). |
| `fonts` | `name` (PostScript name), `enabled`, `label`. |
| `favorites` | `preset`, `font`, `size`, `stars`, `saved_at`. One row per preset and font. |
| `ratings` | Every save, removal and Never show: `at`, `preset`, `font`, `action`, `stars`, `previous_stars`. Never cleared. Older databases have none from before it existed. |
| `launches` | One row per session: `launched_at`, `preset`, `font`, `size`, `mode`, `source` (favorite or discovery), `stars`, `ended_at`, `duration_s`, `end_reason`. |
| `effective` (view) | `schemes` with `overrides` applied. |
| `discovery` (view) | Presets eligible for discovery, with `colorful` (1 = colored background). |
| `favorite_pool` (view) | Favorites that can be picked, with their mode. |
| `launch_stats` (view) | Per preset: launch counts, first and last use, time open, stars. |

Times are stored in UTC. A rebuild replaces only `schemes` and the four views; everything else, including views you add, is kept.

```sql
select mode, count(*), sum(passes_gate) from schemes group by mode;   -- installed and passing
select mode, count(*), sum(colorful) from discovery group by mode;    -- discovery pool, colored backgrounds
select * from launch_stats order by launches desc limit 10;
select preset, font, launched_at, duration_s from launches order by id desc limit 20;
select name, fg_contrast, accent_contrast, distinct_accents from schemes where passes_gate = 0;
select * from favorites where preset not in (select name from schemes);  -- favorites whose preset is gone
```

## Development

```sh
./lint.sh         # ruff format --check, ruff check, pyright, shellcheck
./lint.sh --fix   # apply formatting and safe fixes first
```

Needs [uv](https://docs.astral.sh/uv/) and shellcheck (`brew install shellcheck`). Config: `pyproject.toml`, `AutoLaunch/ruff.toml` (Black style, 100 columns). Targets: `.tools/` Python 3.9 (`/usr/bin/python3`), everything else 3.12; pyright checks the main script against the real `iterm2` module.

Scripts run only inside iTerm2. `./lint.sh` also runs `.tests/srcp_test_db.py` under iTerm2's Python: build, migrations, picking, rating, export and helpers, against a temp folder. Try other database changes against a scratch database (`srcp_build_scheme_db.py --db /tmp/test.db`); use `--dryrun` for everything else.
