# Reference

Details behind the [README](../README.md). The main script is `AutoLaunch/srcp_set_random_color_profile.py`; it runs inside iTerm2 and picks each session's look (color preset + font).

## Rating dialog

Right-click a session → **Save/update this profile**, or press `⌃⌥⌘S`. The rating dialog shows the preset, mode, font, current stars, and the preset's launch count and last launch (any font). Changes apply to the next new session. Escape or **Cancel** closes it without saving; Return does nothing, so it can't save a rating by accident.

| Button | Effect |
|---|---|
| **1 ★** … **5 ★★★★★** | Saves preset + font as a favorite with that many stars. Rate again to change. |
| **Remove favorite** | Favorites only. Deletes this favorite. The preset returns to discovery once no font of it is a favorite. |
| **Never show** | Excludes the preset from all picks and deletes its favorites, then re-rolls the session. For a favorite it first lists the fonts and stars it will delete. Undo: rate it again, **Scripts → srcp_never_show.py** → **Show again**, or `srcp_favorites.py --show-again`. |
| **Cancel** | Closes the dialog. |

Right-click → **Never show this profile** does the same as **Never show**, without the rating dialog: both re-roll the session as in [Change to another profile](#change-to-another-profile), and for a favorite both first list the fonts and stars they will delete (**Cancel** or Escape keeps everything). Like the other items, iTerm2 3.7.6 shows it on every session; on one the script didn't color, it says so.

Right-click → **Remove profile from favorites** does the same as **Remove favorite**, without the dialog. iTerm2 3.7.6 shows it on every session and, on a non-favorite, says so and changes nothing; if the database is busy or missing it says that instead. iTerm2 with Python API protocol 1.23+ hides it on non-favorites; the script uses that when available.

## Change to another profile

Right-click a session → **Change to another profile** re-rolls its look on the spot: a new pick for the current light/dark mode, made exactly as for a new session (favorite or discovery, font included), never the preset it has now or a byte-identical twin of it. Works on any session, including ones opened from a favorite profile (only that session changes). The old look's `launches` row ends with `end_reason = 'changed'`, and the new look gets its own row. A second click while one change is still running is ignored; a dialog or menu action still running after 10 minutes (a reply from iTerm2 got lost) is cancelled by the next one. Re-rolled looks still count as launches; reports show them as **Changed** (the rating dialog: "changed away from N") and leave them out of average time open.

## Favorite profiles

Each favorite becomes a profile named `<preset> - <font>` under **Profiles → Favorites**. Sessions opened from one keep that look.

The main script writes these profiles to a Dynamic Profiles file (see [Files on disk](#files-on-disk)); iTerm2 reloads it automatically. Each profile is based on your default profile, so it keeps your other settings (triggers, key bindings, etc.). Edits made to one in Settings are lost when the file is rewritten. A favorite is one preset + font: changing the font in a session doesn't change what the rating dialog saves (it uses the font the script applied). To keep a preset with another font, rate it from a tab that shows it in that font; to drop the old one, open it from **Profiles → Favorites** and choose **Remove favorite**. If the file is deleted, it comes back within 30 seconds.

## Review ratings

**Scripts → srcp_ratings.py** lists your favorites in a window. **History** shows the last 50 rating changes; **Hidden** shows **Never show** presets. Text is selectable.

**Scripts → srcp_never_show.py** lists **Never show** presets with a dropdown: pick one, then **Show again**. It sets `overrides.include` back to `NULL` (a `mode` override stays). With `fix_colors` off, a preset that fails the readability checks gets `1` instead (it was showing before), except for a `duplicate_of` twin; with it on (the default) it's picked anyway, with its [color adjustments](#color-adjustments). Rating a hidden preset un-hides it by the same rule; since 0.8.0 a twin is no longer forced in that way (twins are never picked either way). Deleted favorites aren't restored, and no `ratings` row is written. The main script picks the change up at the next new session.

From the repo:

```sh
python3 .tools/srcp_favorites.py                          # favorites: stars, launches, re-rolls, last used, average time open
python3 .tools/srcp_favorites.py --mode dark --min-stars 4
python3 .tools/srcp_favorites.py --history                # last 30 rating changes; --history N for more
python3 .tools/srcp_favorites.py --hidden                 # Never show presets
python3 .tools/srcp_favorites.py --show-again '<preset>'  # undo Never show (the one write this tool does)
```

```text
Stars  Preset          Font    Mode  Launches  Changed  Last used         Avg open  Saved
-----  --------------  ------  ----  --------  -------  ----------------  --------  ----------
5 ★    Selenized Dark  Monaco  dark  1         0        2026-10-17 21:03  2m 15s    2026-10-17
```

## See the odds

`.tools/srcp_odds.py` shows, as bar charts, the chance that the next new session gets a favorite or a new preset, which favorites, a colored background, and each font, computed exactly from your database the way the main script picks (a test keeps them in step). Read-only.

```sh
python3 .tools/srcp_odds.py                  # light and dark
python3 .tools/srcp_odds.py --mode dark --top 20
python3 .tools/srcp_odds.py --json           # the numbers, for other tools
```

These are the odds, not what you actually got. A view of your launch history is planned for late November 2026, once there's enough of it ([ROADMAP](ROADMAP.md)).

## How a look is picked

For each new session, and at start for each open one with no look yet (buried ones too), the main script:

1. **Skips sessions opened from a favorite profile.** They keep their look; the launch is still logged.
2. **Reads the mode** from iTerm2 (follows macOS with the Regular, Minimal or Compact theme). Open sessions keep their colors when the mode changes.
3. **Chooses a favorite or a discovery preset.** Chance of a favorite: `favorite_share × min(1, favorite presets in this mode / favorite_target)`. With defaults that is 5% per favorite preset, capped at 50% from 10 on. No favorites means discovery every time.
   - **Favorite:** weighted by stars per saved preset + font (a 5 ★ is picked 5× as often as a 1 ★; a preset saved with three fonts has three chances), in its saved font. If that font has since been disabled, a random one.
   - **Discovery:** a preset of this mode (with `fix_colors` off: only one that passes the readability checks) that isn't a favorite, and isn't among the last 20 discovery picks in this mode. Colored-background presets (yellow, pink, teal rather than white, grey or black) get 3× weight. They are rare, so this lifts them to roughly a fifth of light and a third of dark sessions. Random font.
4. **Applies the preset and font** to this session only, at the profile's font size, with the preset's [color adjustments](#color-adjustments) (the Console line ends ", N colors adjusted", then how long coloring the tab took, e.g. "(84 ms)"). Also sets the non-ASCII font to MesloLGS Nerd Font (`nonascii_font`), so Oh My Zsh prompt icons (Powerlevel10k and the like) render with any font.
5. **Logs the launch** with how long the tab took, and the close time when the session ends. Sessions open when iTerm2 quits get `end_reason = 'unknown'` at the next start. A look replaced by **Change to another profile** ends with `end_reason = 'changed'`.

### Light or dark

Where the schemes repo labels a preset (`variant:` in `yaml/<name>.yml`), the label wins. Otherwise a preset is light if its background is brighter than its text. Override per preset with `overrides.mode` (see [Configuration](#configuration)).

### Readability checks

With `fix_colors = 0`, discovery uses only presets that pass all three (by default every preset is used, with its [color adjustments](#color-adjustments)):

| Check | Rule |
|---|---|
| Text | Text contrast ≥ 4.5 against the background (WCAG AA). |
| Colors visible | ≥ 4 of ANSI red, green, yellow, blue, magenta, cyan have contrast ≥ 2 against the background. |
| Colors distinct | ≥ 5 of those 6 are all ≥ 0.04 apart in OKLab. |

Monochrome presets (one hue, or near-grey) need only the Text check. A contrast within 0.0001 of its target counts as reaching it, as in the adjustments below. `overrides.include` forces a preset in or out.

### Color adjustments

`srcp_build_scheme_db.py` moves each color that is too close to its background until it reaches its target, and the main script applies the result to each new session and to favorites' profiles. iTerm2's installed presets are never changed.

| Color | Target contrast against the background |
|---|---|
| Text (`Foreground Color`) | 4.5 |
| ANSI 1-6 and 9-14 (red ... cyan, normal and bright) | 2 |
| ANSI 8 (bright black: comments, shell autosuggestions) | 1.5, so it stays dimmer than text |

- A color moves in OKLCH lightness (steps of 0.005) with its hue kept, toward white or black, whichever can reach the target (the side it's on first; one of them always reaches 4.5). Text stays on its side of the background if white or black on that side gets within 0.5 of 4.5 (text below 1.1 contrast with the background has no side), even though the other side would reach it: Hot Dog Stand's white text on red (4.2) stays white instead of turning black, and the preset keeps failing the Text check. Chroma drops only as far as needed to stay in gamut, and a colored ANSI color never so far that it stops counting as colored (OKLab chroma above 0.02); text may turn grey. If keeping the color stops it short of the target near white or black, it gets the best value on the way, if that beats the original by more than 0.001 (e.g. Unikitty's bright magenta); text and grey colors take the last step onto white or black (ending on it, to within rounding; a colored one would leave the gamut there).
- A bright color (ANSI 9-14) that would end up identical to its normal one gets a little more contrast, so the two stay apart.
- If ANSI 1-6 still have fewer than 5 distinct colors, one color of the closest pair moves 0.01 in lightness, away from the other or toward it (for two of the same lightness, away is up), only if the pair ends up further apart, the color stays at or above its target, and the palette isn't one it already passed through. Preferred, in order: a move that leaves the color further than the pair's distance from every other accent too, a move away, a move that raises its contrast. At most 40 moves per accent, shared among them: 240 in all. If that doesn't reach 5, those moves are dropped. They can lower an already readable color's contrast a little.
- Monochrome presets keep their look-alike accents (they are meant to be shades of one hue). Any preset whose six accents all look the same (HaX0R Blue, Gr33N and R3D, Pumpkin Spice, Retro) is first spread: ANSI 2-6 move 1 to 5 steps of 0.01 in lightness, alternately toward more contrast and away from it (ANSI 1 stays), then they are separated as above into distinct shades of the same hue.
- All of the above repeats until a pass changes nothing: a color stored rounded (4 decimals in sRGB, 6 in P3 or Generic RGB) can leave room for one more small move. After 3 passes the build keeps the result and prints `[warn] unsettled '<preset>'`.
- Not adjusted: the background, ANSI 0, 7 and 15, cursor, bold, selection, and the separate `(Light)`/`(Dark)` colors a few presets carry for iTerm2's "separate colors for light and dark mode".
- Each color is judged as iTerm2 draws it. iTerm2 draws a color by its `Color Space`: `sRGB` as sRGB, `P3` as Display P3, and anything else, a missing key included, as calibrated (Apple's Generic RGB; about 180 of the repo's presets have such colors). P3 and Generic RGB colors are converted to sRGB for the readability checks and the adjustments (CSS Color 4's P3 matrices; Generic RGB from macOS's own ICC profiles, which matches NSColor to within 6e-4); one outside sRGB's gamut is clipped per channel. An adjusted color keeps its own color space, stored back to 6 decimals, and is chosen inside that space's gamut (Generic RGB doesn't hold all of sRGB). `color_hash` marks P3 and Generic RGB colors, since the same numbers look different there.
- Adjustments are tied to the preset's colors at build time (`schemes.color_hash`, over 32-bit-rounded components, as iTerm2's API sends them). If a preset changes in iTerm2 after a build, its adjustments are skipped, with a Console warning, until the next rebuild.
- A rebuild needs no restart (unless the scripts were updated too: then restart the main script, which refuses a database newer than it reads): it rewrites the database in place, the main script reads each new tab's adjustments as it opens, and favorites' profiles update within 30 seconds (the main script checks for changes that often).
- `fix_colors = 0` turns all of this off: original colors, and only presets that pass the readability checks. Failing presets you showed again or rated while it was on then drop out too.

A few installed presets are byte-identical copies under an older name (`base16 icy` = `base16-icy`), and the repo has a few identical twins of its own (`Zenbones` = `Zenbones Light`). Discovery uses only one name for each: the repo's, and the first in sort order.

## Configuration

Settings live in the database's `config` table. Edit with `sqlite3`; changes apply to the next new session. A numeric setting that isn't a usable number (text, `inf`, `nan`, or beyond ±1,000,000) is ignored: its default is used, with one Console warning (`srcp_odds.py` ignores it too).

```sh
sqlite3 ~/.local/share/iterm2-color-schemes/schemes.db
```

| `config` key | Default | Meaning |
|---|---|---|
| `favorite_share` | `0.5` | Maximum chance of picking a favorite. |
| `favorite_target` | `10` | Favorite presets per mode needed to reach `favorite_share`. |
| `recent_window` | `20` | Discovery picks before a preset can repeat. |
| `colorful_weight` | `3` | Discovery weight of colored-background presets (OKLab chroma ≥ 0.05). `1` = no preference. |
| `fix_colors` | `1` | [Color adjustments](#color-adjustments). `0` (or `0.0`) turns them off; anything else, or no row, means on. Applies to new sessions without a rebuild. |
| `nonascii_font` | `MesloLGSNFM-Regular` | Font for icons and other non-ASCII characters (`setup.sh` installs it with Homebrew because Oh My Zsh prompts need it). `''` keeps the profile's setting. The default is skipped while its font file isn't installed; disabling it in `fonts` only keeps it out of the random main fonts. |

Default fonts: SF Mono, SF Mono Terminal, Menlo, Monaco, PT Mono, Andale Mono (no bold face; iTerm2 draws a synthetic bold), MesloLGS Nerd Font Mono. To add or remove one, use the [fonts tool](#fonts-tool). Fonts that aren't installed are disabled (`missing = 1`) until you install them and rebuild (`./setup.sh`, or `python3 .tools/srcp_build_scheme_db.py`). A font you disable yourself stays disabled. To keep a missing one off after you install it, also `set missing = 0`.

```sql
update config set value = '0.7' where key = 'favorite_share';
update config set value = '1' where key = 'colorful_weight';
update config set value = '' where key = 'nonascii_font';
update config set value = '0' where key = 'fix_colors';        -- original colors

-- fonts: name = PostScript name, label = name shown in profile names
update fonts set enabled = 0 where name = 'Monaco';
insert into fonts (name, label) values ('JetBrainsMonoNL-Regular', 'JetBrains Mono');

-- force a preset in or out of discovery, or set its mode
insert into overrides (name, include) values ('Zenburn', 1)
  on conflict (name) do update set include = excluded.include;
insert into overrides (name, mode) values ('Some Scheme', 'dark')
  on conflict (name) do update set mode = excluded.mode;
```

## Fonts tool

`.tools/srcp_fonts.py` compares fonts and adds them to, or takes them out of, the pool. Safe while iTerm2 runs; it writes no iTerm2 settings. Homebrew fonts aren't installed by default; MesloLGS Nerd Font is the exception (`setup.sh`, for Oh My Zsh prompts).

| Command | Does |
|---|---|
| `python3 .tools/srcp_fonts.py` | Writes `$TMPDIR/iterm2-font-inventory.html` and opens it: every font in the catalog ([MONOSPACE-FONT-INVENTORY.md](MONOSPACE-FONT-INVENTORY.md), the same fonts `--help` lists), each with pool, installed and source badges and a copyable `--add` command. Prints the same list. If the DB can't be read, the pool shows as unknown and no commands are offered. |
| `… --add FONT` | Catalog fonts only (label, cask or PostScript name). Checks the DB first. A Homebrew font not installed: `brew install --cask -- CASK` with `HOMEBREW_NO_AUTO_UPDATE`, `_NO_INSTALL_UPGRADE`, `_NO_INSTALL_CLEANUP`. Then waits up to 5 s for macOS to know the PostScript name (nothing changes in the pool until it does), and sets the `fonts` row `enabled = 1, missing = 0`, also for a font you'd turned off. |
| `… --remove FONT` | Sets every enabled row of the font's names to `enabled = 0` (favorites saved with it come up in another font). Then `brew uninstall --cask` only if this tool installed the cask, Homebrew's install time still matches the record, it isn't the MesloLGS cask, and nothing uses any face in its files (macOS lists them; not only fixed-width ones): no favorite, enabled `fonts` row, `nonascii_font` or iTerm2 profile (font or non-ASCII font). If any of that is true or can't be checked, the cask stays, and it says why and how to remove it by hand. |
| `… --dryrun` | With `--add`/`--remove`: prints the brew command it would run and, for a font already installed, the SQL; changes nothing. `brew list`/`brew info` still run (they may reach the network). |
| `… --catalog` | Prints `docs/MONOSPACE-FONT-INVENTORY.md` (a test holds the two equal). |
| `--out PATH`, `--no-open`, `--db PATH` | Page path, don't open it, another DB. |
| `--no-embed` | Don't embed. By default the page embeds the regular face of each font on it outside `/System` (`.ttf`/`.otf`, ≤ 5 MB each, ≤ 25 MB of font files in all, pool fonts first), since Safari won't draw fonts you installed yourself; in Safari, a card it couldn't carry says so. Some (SF Mono) are Apple's, so it refuses an `--out` inside any clone of this project unless `--no-embed`. |

Record: `~/.local/share/iterm2-profile-manager/added-fonts.txt` (edited under `added-fonts.txt.lock`), `cask <token> <installed_time>` per cask the tool installed (Homebrew's time then; empty if `brew info` failed right after the install, and then `--remove` keeps the cask and forgets it; a cask you already had is never recorded) and `file <PostScript name> <path>` per font file it added. A rebuild treats those fonts like the defaults: off (`missing = 1`) while their files are gone, back on when they return. `uninstall.sh`, even `--all`, leaves the record and those fonts; `--remove` them first. The curation export doesn't carry them to another Mac.

By hand, for a font not in the catalog: install it, find its PostScript name (Font Book → ⓘ), then `insert into fonts (name, label) values ('<PostScript name>', '<label>');`. The main script doesn't check the file of a font you add this way, so install it first.

## Backups

Snapshots of the database go to `backups/` beside it:

- Before every rebuild
- While the main script runs (at start, then every 15 minutes of awake time), if the newest snapshot is over a day old and the database passes `PRAGMA quick_check` (a damaged one is skipped with one Console warning, so the good snapshots aren't pruned out in its place)

The newest 14 are kept; `manual-*.db` snapshots are never pruned. Snapshots are aged by the UTC time in their names, not mtime; one named after now (a clock set ahead, or a file from another Mac) is neither counted nor pruned, and `uninstall.sh --data` leaves it in place and says so. `uninstall.sh --data` treats an empty (0-byte) `schemes.db` as missing and keeps the newest snapshot instead. If `schemes.db` is empty or missing but its `-wal` isn't, it deletes nothing: don't open the database then (SQLite discards a `-wal` beside an empty or missing database when it opens it); follow Restore below, which sets the files aside first. If copying or exporting the database fails, it stops before deleting anything in the data folder. Preset data can be rebuilt from iTerm2's settings; snapshots protect favorites, ratings, hidden presets, launches and settings.

### Manual snapshot

Safe any time:

```sh
cd ~/.local/share/iterm2-color-schemes
mkdir -p backups && sqlite3 schemes.db ".backup backups/manual-$(date -u +%Y%m%dT%H%M%SZ).db"
```

Don't `cp` the database while iTerm2 runs: recent changes may still be in `schemes.db-wal`.

### Restore

Quit iTerm2, then:

```sh
cd ~/.local/share/iterm2-color-schemes
ls -r backups/                      # by name, newest first (schemes-*, then manual-*)
d=aside-$(date -u +%Y%m%dT%H%M%SZ)
mkdir "$d" && mv schemes.db* "$d"/ && cp backups/schemes-<timestamp>.db schemes.db
python3 <repo>/.tools/srcp_build_scheme_db.py   # resync with installed presets
```

The `mv` sets the old database aside with its `-wal`/`-shm` files, names unchanged, so they stay paired: moving them back undoes the restore. If the build refuses the snapshot (damaged), don't repeat the `mkdir` line; `cp` the next older snapshot over `schemes.db` and build again. Keep the `aside-*` folder until the restored database works (tabs are colored, your favorites are there), then delete it; `uninstall.sh --data` leaves it in place. Never put a `-wal` or `-shm` file beside a different `schemes.db`: SQLite would replay it into that database.

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

Matching rows are updated, others kept; re-importing doesn't duplicate history. Fonts disabled in the target stay disabled, and fonts it doesn't have yet arrive disabled; the next rebuild there enables the default ones that are installed. Fonts you disabled yourself stay disabled in the target. An export made before schema 5 can't tell those apart from fonts that were only missing: a font it adds or turns off stays off, even once installed, so enable it with `update fonts set enabled = 1 where name = '<name>'`. If the target database's schema is older than the export's, nothing is imported: run `.tools/srcp_build_scheme_db.py` first.

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
- Adds a `cc-status` hook for 10 Claude Code events to `~/.claude/settings.json` (backs up the file first, newest 10 backups kept; other settings kept)
- Adds two triggers per iTerm2 profile that track a running `claude` job
- Sets the two iTerm2 flags that record this

It doesn't touch iTerm2's AI chat settings or API key. `uninstall.sh` doesn't undo it; `--remove` does (`--remove --dryrun` lists what it would take out): the `cc-status` link if it points at iTerm2's, each hook group in `~/.claude/settings.json` that is exactly one `cc-status` hook, the two triggers where they match exactly, and the two flags. iTerm2's own setup installs identical items, so those go too; your own hooks, triggers and a link pointing elsewhere stay. `settings.json` and the prefs are backed up first. Enabling moves a `cc-status` file or link of yours aside (`.backup-<time>`) rather than replacing it.

## Install options

### Custom schemes folder

`SRC_DIR=~/code ./setup.sh` clones the schemes repo into `~/code` instead of `~/src`. Set `SRC_DIR` again for later rebuilds and for `uninstall.sh --repo`; the database build reads the repo's light/dark labels from there. `--repo` leaves the clone alone if `git status --porcelain --ignored --untracked-files=all` shows anything but ignored `.DS_Store` / `._*` files (which Finder leaves in any folder it opens), `git rev-list --all --not --remotes` finds commits (branches, `HEAD`, tags, stashes) no remote has, it has linked worktrees, or git fails.

### Clone into the Scripts folder

iTerm2 runs scripts only from `~/Library/Application Support/iTerm2/Scripts/`, and starts everything in its `AutoLaunch/` folder at launch. Instead of letting `setup.sh` copy the scripts there, you can clone straight into it (it must be missing or empty). Edits and `git pull` then apply with no copy step; restart the main script from the Scripts menu after pulling. `setup.sh` and `uninstall.sh` run from another clone see that the Scripts folder is a checkout (a `.git` and `.tools/srcp_build_scheme_db.py`, which `setup.sh` never copies there) and never copy over or remove its files; uninstall only renames its main script to `.py.disabled`.

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
cp srcp_ratings.py srcp_never_show.py srcp_score_presets.py ~/Library/Application\ Support/iTerm2/Scripts/
mkdir -p ~/Library/Application\ Support/iTerm2/Scripts/.tools
cp .tools/srcp_favorites.py .tools/srcp_popup.py .tools/srcp_popup.js \
   .tools/srcp_schema.py .tools/srcp_db_backup.py .tools/srcp_iterm_prefs.py \
   ~/Library/Application\ Support/iTerm2/Scripts/.tools/                  # the main script needs the last three
```

Every tool that changes iTerm2's settings takes `--dryrun`, and so does the build; `srcp_favorites.py --show-again` writes the database at once (otherwise `srcp_favorites.py`, `srcp_odds.py` and `srcp_export_curation.py` only read it). Manual key binding: Settings → Keys → Key Bindings → **+**, shortcut `⌃⌥⌘S`, action **Invoke Script Function**, function `save_profile()`.

Double-clicking an `.itermcolors` file also imports it, with one dialog per file.

## Files

### Files on disk

| Path | Contents |
|---|---|
| `~/.local/share/iterm2-color-schemes/schemes.db` | The database. |
| `~/.local/share/iterm2-color-schemes/backups/` | Database snapshots. |
| `~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist` | Favorite profiles. |
| `~/.local/share/iterm2-profile-manager/prefs-backups/com.googlecode.iterm2-<UTC time>.plist` | iTerm2 settings backups, newest 10 kept (by the time in the name; local time before v0.12). Restore one with iTerm2 quit: `defaults import com.googlecode.iterm2 <file>`. Versions before 0.6.7 wrote them to `~/Library/Preferences/com.googlecode.iterm2.backup-*.plist`; those can be deleted. |
| `$SRC_DIR/iTerm2-Color-Schemes` (default `~/src`) | Schemes repo clone. |
| `~/Library/Fonts` | Copied Terminal.app fonts. |
| `~/.local/share/iterm2-profile-manager/installed.txt` | What `setup.sh` installed: `font <file>`, `fonthash <sha256> <file>` (the copy it installed) and `cask <name>` lines, so `uninstall.sh --fonts` removes only those, and only while unchanged. `setup.sh` updates a font only while it still has the recorded hash; any other copy is left alone (to get Terminal.app's copy, delete yours in Font Book and run `setup.sh` again; that is also the way out for a font recorded by 0.11.0, before hashes, that Terminal.app has since changed). The hash comes before the name, so names with spaces survive. An install from before 0.11.0, or one done by hand, has no record, and keeps its fonts on uninstall. |
| `~/.local/share/iterm2-profile-manager/added-fonts.txt`, `added-fonts.txt.lock` | What the [fonts tool](#fonts-tool) added: `cask <token> <installed_time>` and `file <PostScript name> <path>` lines, and the lock it edits them under. `uninstall.sh` leaves them. |
| `$TMPDIR/iterm2-font-inventory.html` | The fonts tool's comparison page (temporary; may carry copies of your fonts). |
| `~/srcp-curation-<time>.sql`, `~/srcp-curation-<time>.db` | Written by `uninstall.sh --data`: the ratings export, and a copy of the whole database. |

### Repo files

| Path | Purpose |
|---|---|
| `AutoLaunch/srcp_set_random_color_profile.py` | Main script. Starts with iTerm2; picks each session's look, logs launches, provides the rating dialog and right-click menu items, writes favorite profiles, takes daily snapshots (checked every 15 minutes). |
| `srcp_ratings.py` | Scripts menu: ratings window. |
| `srcp_never_show.py` | Scripts menu: list Never show presets, show one again. |
| `srcp_score_presets.py` | Scripts menu: ranks presets by saturation (report only). |
| `setup.sh`, `uninstall.sh` | Install and uninstall. |
| `.tools/` | Command-line tools. The leading dot keeps them out of iTerm2's Scripts menu. |
| `.tools/srcp_build_scheme_db.py` | Sets each preset's mode, runs the readability checks, writes the database. |
| `.tools/srcp_import_presets.py` | Imports a folder of `.itermcolors` files without a dialog per file. |
| `.tools/srcp_add_key_binding.py` | Binds `⌃⌥⌘S`. |
| `.tools/srcp_favorites.py` | Ratings report (CLI) and `--show-again`; also used by the menu scripts. |
| `.tools/srcp_odds.py` | Odds of the next session's look, as bar charts or JSON. |
| `.tools/srcp_popup.js`, `.tools/srcp_popup.py` | The window for `srcp_ratings.py` and `srcp_never_show.py` (JXA `NSAlert`, optional dropdown), and the Python side that runs it. |
| `.tools/srcp_fonts.py`, `.tools/srcp_fonts.js`, `.tools/srcp_fonts.html` | [Fonts tool](#fonts-tool): the comparison page, `--add`/`--remove`, the font catalog; macOS's font lookups (JXA); the page template. |
| `.tools/srcp_export_curation.py` | Exports ratings and settings as SQL. |
| `.tools/srcp_uninstall_prefs.py` | Removes the key binding and presets from iTerm2's settings (used by `uninstall.sh`). |
| `.tools/srcp_schema.py`, `.tools/srcp_iterm_prefs.py`, `.tools/srcp_db_backup.py`, `.tools/srcp_preset_history.py` | Shared helpers: what the DB's readers and writers agree on (color hash, `fix_colors` rule, seeds and their labels, the fonts tool's record), iTerm2 settings access, snapshots, past versions of the repo's presets. The main script imports the first three, so they're installed with it. |
| `.tools/srcp_enable_claude_integration.py` | See [Claude Code integration](#claude-code-integration-optional). |
| `docs/MONOSPACE-FONT-INVENTORY.md` | The font catalog, generated by `srcp_fonts.py --catalog`. |
| `lint.sh`, `pyproject.toml`, `AutoLaunch/ruff.toml` | Lint and type checks. |

## Database

`PRAGMA user_version` is the schema version (`MIGRATIONS` in the build). The scripts read only a database at exactly `srcp_schema.MIN_SCHEMA` (9, always the number of migrations). An older one gets one Console line asking for a rebuild, and works as soon as it's rebuilt, without a restart. A newer one (rebuilt by newer scripts) gets one line asking to restart the main script; the main script checks the version on every database call, so it notices a rebuild while it runs. Every change to the shape of `schemes` or the views, or to the columns the main script writes, adds a migration, even an empty one (7 marks the P3 color math, 8 Generic RGB; 9 adds the launch timing columns), so that check is all a reader needs. Going back to 0.14.3 or older: drop any view of yours that names the timing columns, then drop them and set the version back (`ALTER TABLE launches DROP COLUMN coloring_ms; ALTER TABLE launches DROP COLUMN queued_ms; ALTER TABLE launches DROP COLUMN queue; PRAGMA user_version = 8;`), which keeps everything else; or restore a snapshot from before the rebuild (the build takes one first; the newest 14 are kept, see [Restore](#restore)), which loses what changed since.

| Table or view | Contents |
|---|---|
| `schemes` | One row per installed preset: `mode`, `mode_source`, `fg_contrast`, `accent_contrast`, `distinct_accents`, `monochrome`, `passes_gate`, `in_repo` (1: the schemes repo has this preset), `color_hash` (of its colors as built: a tab or profile gets `fixes` only while the installed preset still matches), `duplicate_of`, `bg_chroma`, `fixes` (JSON `{key: [r, g, b, contrast before, contrast after]}`, or NULL), `fix_count`, `passes_gate_fixed` (the checks on the adjusted colors). Rebuilt by `srcp_build_scheme_db.py`. |
| `overrides` | `name`, `mode` (force light/dark), `include` (1 = always pick, 0 = never). Seeded with 6 near-miss presets forced in. |
| `config` | Settings (see [Configuration](#configuration)). |
| `fonts` | `name` (PostScript name), `enabled`, `label`, `missing` (1: disabled only because its file wasn't found; a rebuild enables it once it is). |
| `favorites` | `preset`, `font`, `size`, `stars`, `saved_at`. One row per preset and font. |
| `ratings` | Every save, removal and Never show: `id`, `at`, `preset`, `font`, `action`, `stars`, `previous_stars`. The scripts never delete rows; migration 4 removed exact duplicates once, and `uninstall.sh --data` deletes the database. Older databases have none from before it existed. |
| `launches` | One row per look shown in a session: `id`, `launched_at`, `preset`, `font`, `size`, `mode`, `source` (favorite or discovery), `stars`, `iterm_profile` (the session's profile), `session_id`, `ended_at`, `duration_s`, `end_reason` (`closed`, `changed` or `unknown`), `coloring_ms`, `queued_ms`, `queue` (`startup`, `new` or `reroll`); see below. |
| `effective` (view) | `schemes` with `overrides` applied. |
| `discovery` (view) | Presets eligible for discovery, with `colorful` (1 = colored background). |
| `favorite_pool` (view) | Favorites that can be picked, with their mode. |
| `launch_stats` (view) | Per preset: launch counts (`changed_launches`: re-rolled away from), first and last use, time open (`avg_duration_s` leaves out `changed` rows), stars. |

Timing (0.14.4 on; NULL before): `coloring_ms` is the whole coloring, from the tab's first check until its variables are set, less the wait for a coloring slot (`queued_ms`; NULL for a re-roll, which takes none, and the Console shows it only from 10 ms); the number in its Console line. It leaves out a re-roll's wait for a coloring already running on the tab, and the `launches` write. A coloring given up at the 15 s limit (one limit for the whole span) gets no row, only its Console line, so the slowest tabs aren't here.

Times are stored in UTC. A rebuild replaces only `schemes` and the four views; everything else, including views you add, is kept.

```sql
select mode, count(*), sum(passes_gate) from schemes group by mode;   -- installed and passing
select mode, count(*), sum(colorful) from discovery group by mode;    -- discovery pool, colored backgrounds
select * from launch_stats order by launches desc limit 10;
select launched_at, preset, queue, coloring_ms, queued_ms from launches order by coloring_ms desc limit 10;  -- slowest tabs
select preset, font, launched_at, duration_s from launches order by id desc limit 20;
select name, fg_contrast, accent_contrast, distinct_accents from schemes where passes_gate = 0;
select name, fix_count from schemes where fix_count > 0 order by fix_count desc limit 20;  -- most adjusted
select s.name, j.key, j.value from schemes s, json_each(s.fixes) j where s.name = 'Coffee Theme';
select * from favorites where preset not in (select name from schemes);  -- favorites whose preset is gone
```

### Trimming launches

`launches` grows by one row per tab (a few hundred bytes with its indexes) and is never trimmed, and each of the 14 snapshots carries a copy. To drop old history, run the line below (`launched_at` is UTC, as `datetime('now')` is). Its `.timeout` lets a save or snapshot in progress finish first: `VACUUM` fails on a write in flight. Nothing else needs changing: the launch stats just start later.

```sh
sqlite3 -cmd ".timeout 5000" ~/.local/share/iterm2-color-schemes/schemes.db \
  "DELETE FROM launches WHERE launched_at < datetime('now', '-365 days'); VACUUM;"
```

## Development

```sh
./lint.sh               # ruff format --check, ruff check, pyright, shellcheck, tests, VERSION bumped
./lint.sh --fix         # apply formatting and safe fixes first
./lint.sh --allow-skip  # pass even if pyright and the tests were skipped (no iTerm2 Python)
```

Needs [uv](https://docs.astral.sh/uv/) and shellcheck (`brew install shellcheck`). Config: `pyproject.toml`, `AutoLaunch/ruff.toml` (Black style, 100 columns). Targets: `.tools/` Python 3.9 (`/usr/bin/python3`), everything else 3.12; pyright checks the main script against the real `iterm2` module.

Scripts run only inside iTerm2. `./lint.sh` also runs the tests under iTerm2's Python, against temp folders: `.tests/srcp_test_db.py` (build, migrations, picking, rating, export, helpers) and `.tests/srcp_test_tools.py` (prefs helpers and writers, preset history, the menu scripts' setup, `setup.sh`/`uninstall.sh --dryrun`), stopped after 120 s. Try other database changes against a scratch database (`srcp_build_scheme_db.py --db /tmp/test.db`); use `--dryrun` for everything else.
