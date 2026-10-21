# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## State tracking

Read `CLAUDE-STATE.md` at the start of a session and update it when the state changes or a decision is made. It and `docs/archived-decisions.md` are local working notes, gitignored: a fresh clone has neither, so create them if missing.
- Keep `CLAUDE-STATE.md` to 100 lines or fewer in total.
- Keep its "Recent decisions" section to 10 lines or fewer, one dated line per decision (`YYYY-MM-DD: ...`).
- When adding a decision would go over 10 lines, move the oldest entries to the end of `docs/archived-decisions.md`.

## Git

- Always confirm with the user before every `git commit`, `git push`, or `git tag` (including `gh` commands that push or tag). Approval for one doesn't carry over to the next.
- Remote: public GitHub repo `rmarable/iterm2-profile-manager`, default branch `main`.

## Docs

- `README.md` is the front door for casual terminal users (many running AI agents in several tabs): plain, inviting, no AI-sounding filler. What it does, screenshot, requirements, install, daily use, update, uninstall, troubleshooting.
- `docs/REFERENCE.md` holds the details for a technical macOS/shell audience: terse but descriptive, commands, paths, tables and SQL over prose.

## What this is

iTerm2 Python API scripts living in iTerm2's own Scripts folder. There is no build or test suite; scripts are run by iTerm2 itself, not from the shell (they need the `iterm2` module and a live connection to the app).

- **Lint after every code change:** `./lint.sh` (ruff format --check, ruff check, pyright, shellcheck; `--fix` applies formatting and safe fixes). It must pass before a change is done. Settings in `pyproject.toml` and `AutoLaunch/ruff.toml`: Black style at 100 columns, `.tools/` py39, `AutoLaunch/`, `srcp_score_presets.py` and `srcp_ratings.py` py312. Suppressions name the exact rule and say why.
- **Testing without iTerm2:** run `srcp_build_scheme_db.py --db <scratch path>`, and exercise the AutoLaunch script's DB functions by exec'ing its source minus the `iterm2.run_forever(main)` line under iTerm2's Python against a scratch DB. Never point tests at `~/.local/share/iterm2-color-schemes/`.

- **Run a script manually:** iTerm2 menu → Scripts → `srcp_score_presets.py`. Output appears in Scripts → Manage → Console.
- **Runtime:** iTerm2 runs scripts with `~/.config/iterm2/AppSupport/uv/venvs/3.12/bin/python` (Python 3.12, iterm2 2.30; API source in that venv's `site-packages/iterm2`). The `.tools/` scripts run under whatever `python3` is first on PATH (`/usr/bin/python3` 3.9 on a stock Mac), so keep them 3.9-compatible and stdlib-only. Each puts its own folder on `sys.path` before importing `srcp_iterm_prefs`/`srcp_db_backup`, so `python3 -I` works.
- **Setup and import** (plain shell, not iTerm2 API; must run from Terminal.app with iTerm2 quit): `./setup.sh` installs Terminal.app's fonts and MesloLGS Nerd Font, clones the schemes repo into `$SRC_DIR` (default `~/src`), runs `.tools/srcp_import_presets.py`, `.tools/srcp_build_scheme_db.py` and `.tools/srcp_add_key_binding.py`, and copies the scripts into iTerm2's Scripts folder. `./setup.sh --dryrun` (or any tool with `--dryrun`/`--dry-run`) previews safely while iTerm2 is running; use it to check changes on this Mac, since real runs refuse while iTerm2 is open. `srcp_build_scheme_db.py` only reads prefs, so a real run is safe any time.
- **Uninstall:** `./uninstall.sh` undoes `setup.sh` (defaults: AutoLaunch script, menu scripts, favorites' Dynamic Profiles, key binding; opt-in `--data`, `--presets`, `--fonts`, `--repo`, `--all`). Its prefs part is `.tools/srcp_uninstall_prefs.py`. Anything new that `setup.sh` installs needs a matching undo there.
- Prefs writers share `.tools/srcp_iterm_prefs.py` (`load_prefs`, `save_prefs` with backup, `iterm_running`). `save_prefs` refuses when iTerm2 loads its settings from a custom folder (`LoadPrefsFromCustomFolder`), which would overwrite our writes; `setup.sh` and `uninstall.sh` check it up front too. An installed preset equal to any committed version of its repo file (`.tools/srcp_preset_history.py`) counts as ours: the import updates it, `--presets` removes it. `iterm_running` uses `ps`, not `pgrep -x iTerm2`, which misses iTerm2 from Claude's sandboxed shell. Never test a writer by running it for real to see the guard fire.
- `srcp_enable_claude_integration.py` is optional and run by hand; `setup.sh` deliberately doesn't run it, so it isn't imposed on other users. It mirrors what iTerm2 3.7.6 itself installed (cc-status symlink, hooks in `~/.claude/settings.json`, per-profile workgroup triggers, two prefs flags). It deliberately leaves iTerm2's AI chat/API settings (`AIVendor`, `AiModel`, `AitermURL`, API key) alone; keep it that way. If iTerm2 changes its integration, re-derive from a Mac where it was enabled through the app.
- **`.tools/`** holds the command-line tools; the dot keeps them out of iTerm2's Scripts menu (the menu can't pass arguments, and several tools refuse while iTerm2 runs). Menu-runnable scripts live at the top level (`srcp_ratings.py`, `srcp_score_presets.py`) or in `AutoLaunch/`. iTerm2's own `Alert` can't change font or width, so `srcp_ratings.py` shows the report via `.tools/srcp_popup.js` (JXA NSAlert with a Menlo text view; `SRCP_POPUP_DRYRUN=1` builds it without showing it, for tests). Pyright skips dot-folders, so `lint.sh` type-checks a temp copy of `.tools/`.
- **Naming:** every Python file starts with `srcp_` so ours are easy to find in iTerm2's Scripts menu (it lists every `.py` in the folder); keep that for new ones.
- **Version:** bump `VERSION` in `AutoLaunch/srcp_set_random_color_profile.py` on every change to it; the script logs it (with the file's mtime and pid) at startup, so the Console shows which code is running.
- **AutoLaunch:** any `.py` in `AutoLaunch/` starts automatically when iTerm2 launches. This checkout *is* iTerm2's Scripts folder, so adding a `.py` there enables it; never put a helper module there. A script being reviewed is parked as `.py.disabled` (ruff and pyright skip it in place: lint a copy named `.py`). After editing `srcp_set_random_color_profile.py`, restart it from the Scripts menu (or relaunch iTerm2); its flock on `~/.local/share/iterm2-color-schemes/.lock` makes a second copy exit.

## How the pieces fit together

1. `.tools/srcp_build_scheme_db.py` reads `Custom Color Presets` from the prefs and writes `~/.local/share/iterm2-color-schemes/schemes.db`: each preset's mode (yaml `variant:` from the repo, else bg vs fg luminance) and a readability gate (text contrast ≥ 4.5; 4 of ANSI 1-6 at contrast ≥ 2; the largest set of ANSI 1-6 all ≥ 0.04 apart in OKLab has ≥ 5; monochrome themes skip the accent checks). It snapshots the DB to `backups/` first (`.tools/srcp_db_backup.py`, newest 14 kept). A rebuild replaces only `schemes` and its own four views (`VIEW_NAMES`; user views survive); `overrides`, `config`, `fonts`, `favorites`, `ratings`, `launches` persist. Schema changes go in `MIGRATIONS` (tracked by `PRAGMA user_version`), never by editing migration 1.
2. `AutoLaunch/srcp_set_random_color_profile.py` runs forever. For each new session (`NewSessionMonitor`) it gets the mode from `app.async_get_theme()`, picks a favorite (weighted by stars, saved font) or a discovery preset (from the `discovery` view, skipping recent ones, presets with `colorful = 1` (background OKLab chroma ≥ `COLORFUL_BG_CHROMA` 0.05, set by the build) weighted by `config.colorful_weight`, default 3; random enabled font), applies it to the session's profile copy, sets `user.*` session variables, and logs a `launches` row. `SessionTerminationMonitor` fills in the end time. At startup it snapshots the DB if the newest snapshot is over a day old; its `daily_snapshot()` duplicates `.tools/srcp_db_backup.snapshot()` because `setup.sh` installs the script without `.tools/`, so keep the two in step. "Save/update this profile" (context menu) and `save_profile()` (⌃⌥⌘S) share one dialog, run in a task so the RPC timeout can't cut it off; "Remove from favorites" (context menu, `visible_when="user.is_favorite == 1"`; the variable is set as 1/0, not a bool; passed only when iTerm2 reports API protocol 1.23+, which 3.7.6 (1.21) does not, so there the item shows everywhere and explains on non-favorites. The iterm2 module's docs wrongly say 3.7.6) skips the dialog. The script mirrors `favorites` into `~/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist` (`sync_profiles`; GUIDs are uuid5 of preset+font under a fixed namespace, so never change `GUID_NAMESPACE`), and sessions opened from those profiles keep their look. Saves use the applied font (`user.font`), not the font read back from the session.
3. Saves, removals and Never show also append to `ratings` (history, migration 3) in the same transaction; on an older DB the script skips history with one warning. `.tools/srcp_favorites.py` reports favorites (with per preset+font launch stats), `--history` and `--hidden`; read-only.
4. `.tools/srcp_export_curation.py` dumps `favorites`, `ratings`, `overrides`, `config`, `fonts` as SQL for keeping off this Mac (`INSERT OR REPLACE`; ratings and fonts `INSERT OR IGNORE`, fonts never re-enabled) (docs/REFERENCE.md: Backups).
5. `srcp_score_presets.py` is a report only, not used for selection.

Preset names in the DB must exactly match installed names, or the script logs `[warn] preset not found`.

## Preset source

Installed presets come from a local clone of iTerm2-Color-Schemes at `~/src/iTerm2-Color-Schemes`:
- `schemes/*.itermcolors` — importable presets. iTerm2 stores them under `Custom Color Presets` in `com.googlecode.iterm2` prefs, keyed by file name with the file's dict stored verbatim. `.tools/srcp_import_presets.py` writes that directly via `defaults` (no per-file dialog). Opening a file in iTerm2 also imports it, but shows a dialog each time.
- `iterm-dynamic-colors/*.sh` — same schemes as shell scripts using iTerm2's `OSC P` escape codes (`P0`–`Pf` ANSI, `Pg` fg, `Ph` bg, `Pi` bold, `Pj` selection, `Pk` selected text, `Pl` cursor); they recolor only the current session, no import needed.

A few installed presets use older spellings than the repo (e.g. `base16 icy` vs `base16-icy`, `Birds of Paradise` vs `Birds Of Paradise`). They are byte-identical twins; `srcp_build_scheme_db.py` marks the old spelling `duplicate_of` and leaves it out of discovery.
