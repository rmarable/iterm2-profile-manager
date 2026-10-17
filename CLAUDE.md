# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## State tracking

Read `CLAUDE-STATE.md` at the start of a session and update it when the state changes or a decision is made.
- Keep `CLAUDE-STATE.md` to 100 lines or fewer in total.
- Keep its "Recent decisions" section to 10 lines or fewer, one dated line per decision (`YYYY-MM-DD: ...`).
- When adding a decision would go over 10 lines, move the oldest entries to the end of `docs/archived-decisions.md`.

## Git

- Always confirm with the user before every `git commit`, `git push`, or `git tag` (including `gh` commands that push or tag). Approval for one doesn't carry over to the next.
- Remote: private GitHub repo `rmarable/iterm2-profile-manager`, default branch `main`.

## What this is

iTerm2 Python API scripts living in iTerm2's own Scripts folder. There is no build, lint, or test setup; scripts are run by iTerm2 itself, not from the shell (they need the `iterm2` module and a live connection to the app).

- **Run a script manually:** iTerm2 menu → Scripts → `score_presets.py`. Output appears in Scripts → Manage → Console.
- **Setup and import** (plain shell, not iTerm2 API; must run from Terminal.app with iTerm2 quit): `./setup.sh` clones the schemes repo into `$SRC_DIR` (default `~/src`), runs `tools/import_presets.py` and `tools/enable_claude_integration.py`, and copies the scripts into iTerm2's Scripts folder. `./setup.sh --dryrun` (or either tool with `--dryrun`/`--dry-run`) previews safely while iTerm2 is running; use it to check changes on this Mac, since real runs refuse while iTerm2 is open.
- `enable_claude_integration.py` mirrors what iTerm2 3.7.6 itself installed (cc-status symlink, hooks in `~/.claude/settings.json`, per-profile workgroup triggers, two prefs flags). It deliberately leaves iTerm2's AI chat/API settings (`AIVendor`, `AiModel`, `AitermURL`, API key) alone; keep it that way. If iTerm2 changes its integration, re-derive from a Mac where it was enabled through the app.
- **AutoLaunch:** anything in `AutoLaunch/` starts automatically when iTerm2 launches. After editing `random_profile_color.py`, restart it from the Scripts menu (or relaunch iTerm2) for changes to take effect.

## How the two scripts fit together

1. `score_presets.py` (manual, one-shot) ranks every installed color preset by a "pop" score (0.7 × mean saturation of the 16 ANSI colors + 0.3 × background saturation; contrast ratio is printed but not used in ranking), then prints a ready-to-paste `PRESETS = {...}` dict of the top `TOP_N` (128).
2. That dict is pasted by hand into `AutoLaunch/random_profile_color.py`, which runs forever, watches for new sessions via `NewSessionMonitor`, and applies a random preset from `PRESETS` to each new session's profile.

The dict keys are unused labels (scorer emits `"01"`, the AutoLaunch file uses `"001"`); only the values (preset names) matter, and they must exactly match installed preset names or the script logs `[warn] preset not found`.

Neither script distinguishes light from dark themes; the random pool mixes both.

## Preset source

Installed presets come from a local clone of iTerm2-Color-Schemes at `~/src/iTerm2-Color-Schemes`:
- `schemes/*.itermcolors` — importable presets. iTerm2 stores them under `Custom Color Presets` in `com.googlecode.iterm2` prefs, keyed by file name with the file's dict stored verbatim. `tools/import_presets.py` writes that directly via `defaults` (no per-file dialog). Opening a file in iTerm2 also imports it, but shows a dialog each time.
- `iterm-dynamic-colors/*.sh` — same schemes as shell scripts using iTerm2's `OSC P` escape codes (`P0`–`Pf` ANSI, `Pg` fg, `Ph` bg, `Pi` bold, `Pj` selection, `Pk` selected text, `Pl` cursor); they recolor only the current session, no import needed.

A few installed presets use older spellings than the repo (e.g. `base16 icy` vs `base16-icy`, `Birds of Paradise` vs `Birds Of Paradise`); `PRESETS` must use the installed spelling.
