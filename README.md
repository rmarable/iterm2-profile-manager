# iTerm2 color preset scripts

iTerm2 Python API scripts that give every new tab, window or split pane a random color scheme, drawn from the most colorful presets you have installed.

- `AutoLaunch/random_profile_color.py` starts with iTerm2, watches for new sessions, and applies a random preset from its `PRESETS` list to each one.
- `score_presets.py` ranks every installed preset by how saturated its colors are and prints a ready-to-paste `PRESETS` list of the top 128.
- `tools/import_presets.py` imports a folder of `.itermcolors` files as iTerm2 color presets in one go.
- `tools/enable_claude_integration.py` turns on iTerm2's Claude Code integration (iTerm2 3.7 or later).
- `setup.sh` does the whole installation (below).

## Installation

The presets come from the [iTerm2-Color-Schemes](https://github.com/mbadolato/iTerm2-Color-Schemes) repository, which has hundreds of schemes (629 in the September 2026 checkout this was written against).

### Quick setup

Quit iTerm2 (Cmd-Q), open **Terminal.app**, and run:

```sh
./setup.sh --dryrun         # preview every step, change nothing (safe with iTerm2 open)
./setup.sh                  # uses ~/src
SRC_DIR=~/code ./setup.sh   # or pick another folder
```

It clones (or updates) the repo into `~/src/iTerm2-Color-Schemes`, imports every scheme as a color preset, enables iTerm2's Claude Code integration, and copies the scripts into `~/Library/Application Support/iTerm2/Scripts` if you're running it from somewhere else. You can run it again at any time: anything already in place is skipped. `--dry-run` works too. It doesn't change iTerm2's AI chat or API settings (see below).

### Claude Code integration

`tools/enable_claude_integration.py` sets up what iTerm2 3.7 installs when you turn the integration on in the app:

- a `~/.config/iterm2/cc-status` link to the helper inside `iTerm.app`
- a `cc-status` hook on 10 Claude Code events in `~/.claude/settings.json` (other hooks and settings are kept; the old file is backed up next to it)
- two triggers in every iTerm2 profile that group a `claude` job's activity
- the iTerm2 prefs flags that record the hooks and triggers are installed

> **Not included:** iTerm2's AI chat and API settings (AI provider, model, API URL, API key). The script never reads or writes them. Set them up yourself in iTerm2's Settings if you want AI chat.

This copies iTerm2's own setup as found in version 3.7.6, so a later iTerm2 version may set things up differently. Run it on its own with `python3 tools/enable_claude_integration.py [--dryrun]`; set `ITERM_APP` if iTerm2 isn't in `/Applications`.

### Manual setup

1. Clone the color schemes repo:

   ```sh
   mkdir -p ~/src
   git clone https://github.com/mbadolato/iTerm2-Color-Schemes.git ~/src/iTerm2-Color-Schemes
   ```

2. Quit iTerm2, then import every scheme from Terminal.app:

   ```sh
   python3 tools/import_presets.py ~/src/iTerm2-Color-Schemes/schemes
   ```

   Add `--dryrun` (or `--dry-run`) to preview, or `--overwrite` to replace presets you already have.

3. Copy `score_presets.py` and `AutoLaunch/random_profile_color.py` into `~/Library/Application Support/iTerm2/Scripts/` (same layout).

### Why not double-click each file?

Opening an `.itermcolors` file imports it, but iTerm2 shows a confirmation for each one, which means hundreds of dialogs to click. `import_presets.py` writes the same data straight into iTerm2's preferences instead: each preset is named after its file and stored exactly as the file contains it, which is what a double-click does. It goes through `defaults`, so it has to run while iTerm2 is closed, or iTerm2 will overwrite the change when it quits. It backs up your previous preferences to `~/Library/Preferences/com.googlecode.iterm2.backup-<timestamp>.plist` before writing.

The repo's own `tools/import-scheme.sh` also avoids the dialogs, but it changes names (for example "Birds Of Paradise" becomes "Birds of Paradise") and needs bash 4 or later.

### Enable the scripts in iTerm2

1. Settings → General → Magic → **Enable Python API**.
2. Scripts → Manage → **Install Python Runtime** (first time only).
3. Relaunch iTerm2. The AutoLaunch script starts on its own.

## Usage

### Choosing the random pool

1. Run **Scripts → score_presets.py** from the iTerm2 menu. Its output appears in Scripts → Manage → Console.
2. Copy the generated `PRESETS = {...}` block into `AutoLaunch/random_profile_color.py`, replacing the existing one.
3. Restart the AutoLaunch script (or relaunch iTerm2).

The score is 0.7 × the average saturation of the 16 ANSI colors + 0.3 × the background's saturation. The contrast ratio is printed for reference but doesn't affect the ranking. Change `TOP_N` in `score_presets.py` to make the pool bigger or smaller.

Names in `PRESETS` must exactly match installed preset names. If one doesn't, the AutoLaunch script logs `[warn] preset not found` and leaves that session's colors alone.

### Limitations

Light and dark themes are mixed in the same pool. Nothing picks a theme by time of day or by the macOS appearance setting.
