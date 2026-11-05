# Changelog

What changed for someone using iTerm2 Profile Manager, newest first. Releases up to v0.7.1 are described on [GitHub releases](https://github.com/rmarable/iterm2-profile-manager/releases).

## v0.11.0 (2026-11-05)

- After updating, run `python3 .tools/srcp_build_scheme_db.py` once. The main script asks for it in the Console when the database is too old, and colors tabs again as soon as it's rebuilt.
- Presets that use Display P3 colors are judged and adjusted as they actually look, so they're as readable as the rest.
- **Profiles → Favorites** picks up changes within 30 seconds, without waiting for a new tab.
- A tab that iTerm2 stops answering for is given up on after 30 seconds instead of holding up the next ones.
- `.tools/srcp_enable_claude_integration.py --remove` undoes the optional Claude Code integration, leaving your own hooks and triggers alone.
- Enabling the Claude Code integration no longer deletes a `cc-status` link of yours; it moves it aside.
- `./uninstall.sh --fonts` removes only the fonts and Homebrew package that `setup.sh` installed (it keeps a list from now on).
- `./uninstall.sh --data` keeps a copy of the whole database next to the ratings export, and leaves other files in that folder alone.
- The rating dialog's note about adjusted colors is shorter: "1 color adjusted."
- The right-click item "Remove from favorites" is now called "Remove profile from favorites".
- **Never show** in the rating dialog now gives the tab a new look, as the right-click item does, and asks first if the preset is a favorite.
- After rebuilding the database, new tabs get the new color adjustments without restarting the main script (the docs used to say a restart was needed), and **Profiles → Favorites** is rewritten when a rebuild changes a favorite's colors.
- New tabs color a little faster: the script asks iTerm2 for several things at once instead of one after another, and refreshes your favorite profiles in the background instead of making the tab wait.
- Fixes: a tab re-rolled without a font of its own no longer keeps the old font and favorite mark; "Remove profile from favorites" says when the database was busy instead of "isn't a favorite"; saving a tab whose font the script didn't set now marks it as a favorite.
- When the main script is restarted, it waits up to 5 seconds for the old copy to exit instead of quitting at once.
- The Console says to rebuild the database when several presets it picks in a row are no longer installed.
- A hand-edited preset with a color value out of range no longer stops the database build.
- If you installed by hand (not with `setup.sh`), also copy `.tools/srcp_schema.py`, `.tools/srcp_db_backup.py` and `.tools/srcp_iterm_prefs.py` into the Scripts folder's `.tools/`: the main script now needs them, and says so in the Console if they're missing.
- Old database snapshots are cleaned up by the date in their names, so copying or touching a snapshot can no longer get a newer one deleted.
- Setup, uninstall and the other tools that change iTerm2's settings now write only the settings they change, and stop if iTerm2 was opened while they ran.
- The database is backed up once a day even when iTerm2 stays open for days; before, the backup was taken only when iTerm2 started.
- `./uninstall.sh --repo` keeps the iTerm2-Color-Schemes clone if it has changes, commits or stashes of yours.

## v0.9.0 (2026-11-04)

- Colors that are hard to see against their background are adjusted a little in each new tab and in favorites' profiles, so most presets are readable: text, the main colors and their bright versions, and bright black (comments, shell suggestions). The presets themselves aren't edited, only what a tab shows. `config fix_colors = 0` turns it off.
- Presets that used to be skipped as hard to read (about 5%) now come up too, with their colors adjusted.
- The Console line, the rating dialog and the favorites report say when a look's colors were adjusted.

## v0.8.0 (2026-11-04)

- New Scripts menu item **srcp_never_show.py**: lists the presets you've hidden; pick one and press **Show again** to bring it back.
- Right-click **Never show this profile** hides the tab's preset and switches the tab to another look. For a favorite it asks first, since its stars are deleted.
- `python3 .tools/srcp_favorites.py --show-again '<preset>'` undoes Never show from the command line.
