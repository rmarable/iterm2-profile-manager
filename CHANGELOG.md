# Changelog

What changed for someone using iTerm2 Profile Manager, newest first. Releases up to v0.7.1 are described on [GitHub releases](https://github.com/rmarable/iterm2-profile-manager/releases).

## v0.8.0 (2026-11-04)

- New Scripts menu item **srcp_never_show.py**: lists the presets you've hidden; pick one and press **Show again** to bring it back.
- Right-click **Never show this profile** hides the tab's preset and switches the tab to another look. For a favorite it asks first, since its stars are deleted.
- `python3 .tools/srcp_favorites.py --show-again '<preset>'` undoes Never show from the command line.
