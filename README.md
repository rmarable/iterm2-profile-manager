# About This Toolkit

- Are you new to this AI concept of "living in the terminal"?
- Do you now find yourself running Claude Code, Codex or another AI agent in multiple tabs at once?
- Do you hate how limited Terminal's colors are?
- If you made the move from Terminal to iTerm2, are you bored with the iTerm2 default color scheme?
- Wouldn't it be great to have something you can control in your life (like the colors and font of your terminal session)?

If you answered "yes" to any of the above questions, meet your new best friend, the iTerm2 Profile Manager. It gives every iTerm2 tab and window its own look and fixed-width font, so you can easily tell your sessions apart at a glance. Rate and save your favorite combinations, review your past choices, and more.

The presets come from [iTerm2-Color-Schemes](https://github.com/mbadolato/iTerm2-Color-Schemes) (800+). Light presets are used when macOS is in Light Mode and dark ones in Dark Mode. About 5% are hard to read and never get picked.

Press ⌃⌥⌘S to rate the current preset and font from 1 to 5 stars. Rated combinations come up more often and are saved as profiles under **Profiles → Favorites**. **Never show** in the same dialog removes a preset for good.

![One iTerm2 window split three ways: two Claude Code sessions and a shell, each with its own color scheme](images/profile-chooser.jpg)

Only iTerm2's colors and fonts change. Your shell and prompt are left alone. Installation is a single script.

## Why it helps with AI agents

Agents run in long-lived sessions, and it's easy to lose track of which tab is which.

| Default iTerm2 Color Scheme | With the Profile Manager Activated |
|---|---|
| Five black tabs, all running `claude` | Five different colors and fonts |
| Typing into the wrong agent's tab | The color tells you where you are before you type |
| Same look for "agent is working" and "my shell" | Open a favorite profile for agent work, let other tabs stay random |

## Requirements

### Software

| Software | Notes |
|---|---|
| macOS | Required. `setup.sh` exits on other systems. |
| [iTerm2](https://iterm2.com) | Tested on 3.7.6. Install in `/Applications`, or set `ITERM_APP` to its path. |
| Xcode Command Line Tools | `xcode-select --install`. Provides `git` and `/usr/bin/python3` (3.9+). |
| [Homebrew](https://brew.sh) | Optional. Installs MesloLGS Nerd Font for prompt icons. |

No pip packages are needed. Setup needs network access to clone the schemes repo.

### iTerm2 settings

Set these before running setup:

| Setting | Where | Why |
|---|---|---|
| Python API | Settings → General → Magic → **Enable Python API** | Scripts can't connect without it. |
| Python runtime | Scripts → Manage → **Install Python Runtime** | The main script runs on it (Python 3.12). |
| Theme | Settings → Appearance → Theme → **Automatic** or **Minimal** | Only these follow the macOS light/dark mode. |

## Install

Setup changes iTerm2's settings, and iTerm2 overwrites them when it quits, so run it with iTerm2 closed.

1. Quit iTerm2 (⌘Q) and open **Terminal.app** (Applications → Utilities).
2. Run:

   ```sh
   git clone https://github.com/rmarable/iterm2-profile-manager.git ~/src/iterm2-profile-manager
   cd ~/src/iterm2-profile-manager
   ./setup.sh --dryrun   # optional: shows every step, changes nothing
   ./setup.sh
   ```

3. Open iTerm2. **Scripts → Manage → Console** should show `srcp_set_random_color_profile <version>`, then one line per new session, e.g. `[info] dark discovery: Nord | Menlo-Regular 13`.

`setup.sh` is safe to re-run. It:

1. Copies SF Mono and SF Mono Terminal from Terminal.app into `~/Library/Fonts`.
2. Installs MesloLGS Nerd Font via Homebrew, if installed.
3. Clones or updates iTerm2-Color-Schemes in `~/src/iTerm2-Color-Schemes` (set `SRC_DIR` to use another folder).
4. Imports its schemes as iTerm2 color presets. Existing presets are left alone.
5. Builds the database the main script picks from.
6. Binds ⌃⌥⌘S to the rating dialog.
7. Copies the scripts into iTerm2's Scripts folder.

It backs up iTerm2's settings to `~/Library/Preferences/com.googlecode.iterm2.backup-<timestamp>.plist` before changing them. For a custom `SRC_DIR`, cloning straight into the Scripts folder, or a manual install, see [docs/REFERENCE.md](docs/REFERENCE.md#install-options).

## Daily use

| To | Do |
|---|---|
| Keep a look you like | Right-click → **Save/update this profile**, or press ⌃⌥⌘S, then pick 1 to 5 stars. Rate again to change. |
| Open a saved look | **Profiles → Favorites → \<name\>** |
| Drop a favorite | Same dialog → **Remove favorite** |
| Never see a scheme again | Same dialog → **Never show**. It's gone for good, with every font, and survives updates. |
| See your ratings | **Scripts → srcp_ratings.py** |
| See schemes you've hidden | **Scripts → srcp_ratings.py** → **Hidden** |
| Bring a hidden scheme back | **Hidden** shows how; or rate it from a tab that still has it |

Changes apply to the next session you open. Sessions opened from a favorite profile keep that look.

To change how often favorites come up, which fonts are used, or which schemes are allowed, see [Configuration](docs/REFERENCE.md#configuration).

## Update

Quit iTerm2, then from Terminal.app in the repo:

```sh
git pull
./setup.sh   # pulls the schemes repo, imports new presets, rebuilds the database, copies the scripts
```

Relaunch iTerm2. If the repo is the Scripts folder, `git pull` alone updates the scripts; restart the main script from the Scripts menu, or relaunch iTerm2.

## Uninstall

Quit iTerm2, then from Terminal.app in the repo:

```sh
./uninstall.sh --dryrun        # preview; safe with iTerm2 open
./uninstall.sh                 # remove the defaults below
./uninstall.sh --all           # also --data --presets --fonts --repo
```

| Item | Flag |
|---|---|
| Main script (renamed `.py.disabled` if the repo is the Scripts folder) | (default) |
| Menu scripts copied into the Scripts folder | (default) |
| Favorite profiles file | (default) |
| ⌃⌥⌘S key binding (settings backed up first) | (default) |
| Database, snapshots, launch history (ratings exported to `~/srcp-curation-<time>.sql` first) | `--data` |
| Presets identical to the repo's files | `--presets` |
| Copied Terminal.app fonts; MesloLGS Homebrew package | `--fonts` |
| iTerm2-Color-Schemes clone | `--repo` |

Kept: this repo, iTerm2 settings backups, shell config, and the Claude Code integration. Open sessions keep their colors until closed. Re-run `setup.sh` to reinstall.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Tabs don't change color | Check **Scripts → Manage → Console** for errors, and that the Python API is enabled. |
| Console: `no scheme DB at ...` | Run `python3 .tools/srcp_build_scheme_db.py`. No restart needed. |
| Console: `preset not found: X` | Preset renamed or deleted in iTerm2. Run `python3 .tools/srcp_build_scheme_db.py`. |
| Most presets show your default profile's colors | Settings → Profiles → Colors: turn off **Use separate colors for light and dark mode** (few presets define both). |
| Always light, or always dark | Settings → Appearance → Theme: **Automatic** or **Minimal**. |
| Console: `another copy is already running; exiting` | Expected after a restart; a lock allows one copy. |
| A font never appears | Disabled or not installed: `select name, enabled from fonts`. Relaunch iTerm2 after installing a font. |
| A favorite is missing from the Profiles menu | Preset not installed, or marked **Never show**. |
| ⌃⌥⌘S does nothing | Check Settings → Keys → Key Bindings for `save_profile()`, and the Console for the main script's startup line. |
| Console: `database is locked` | A rebuild or open `sqlite3` transaction held the lock over 5 s. That launch isn't logged; no action needed. |
| `srcp_build_scheme_db.py`: `schemes.db is missing but its -wal/-shm files are still there` | Delete `schemes.db-wal` and `schemes.db-shm`, or restore a snapshot. |
| A tool says iTerm2 is running | Quit iTerm2 (⌘Q) and run the tool from Terminal.app. |

## More

- [docs/REFERENCE.md](docs/REFERENCE.md): how a look is picked, configuration, backups and restoring, exporting ratings, the Claude Code integration, the database, and development.
- [DISCLAIMER.md](DISCLAIMER.md): what this changes on your Mac.
- If you use Claude Code or Codex, open it in this repo and ask it to explain or change anything here.

## License

[MIT](LICENSE). Color presets come from [iTerm2-Color-Schemes](https://github.com/mbadolato/iTerm2-Color-Schemes), cloned at install time under its own license.
