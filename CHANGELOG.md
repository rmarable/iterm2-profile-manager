# Changelog

What changed for someone using iTerm2 Profile Manager, newest first. Releases up to v0.7.1 are described on [GitHub releases](https://github.com/rmarable/iterm2-profile-manager/releases).

## Unreleased

- Colors that are hard to see against their background are adjusted a little in each new tab and in favorites' profiles, so most presets are readable: text, the main colors and their bright versions, and bright black (comments, shell suggestions). The presets themselves aren't edited, only what a tab shows. `config fix_colors = 0` turns it off.
- Presets that used to be skipped as hard to read (about 5%) now come up too, with their colors adjusted.
- The Console line, the rating dialog and the favorites report say when a look's colors were adjusted.

## v0.8.0 (2026-11-04)

- New Scripts menu item **srcp_never_show.py**: lists the presets you've hidden; pick one and press **Show again** to bring it back.
- Right-click **Never show this profile** hides the tab's preset and switches the tab to another look. For a favorite it asks first, since its stars are deleted.
- `python3 .tools/srcp_favorites.py --show-again '<preset>'` undoes Never show from the command line.
