# Changelog

What changed for someone using iTerm2 Profile Manager, newest first. Releases up to v0.7.1 are described on [GitHub releases](https://github.com/rmarable/iterm2-profile-manager/releases).

## v0.14.3 (2026-11-06)

- `.tools/srcp_export_curation.py` says the database is busy and to try again, instead of a traceback, and a failed export with `-o` leaves the earlier file as it was instead of a half-written one.
- After a rebuild (`.tools/srcp_build_scheme_db.py`), text stays on its side of the background (lighter or darker) when that side nearly reaches the contrast target, instead of flipping: Hot Dog Stand keeps its white text on red. Monochrome presets whose six accent colors are all one color (HaX0R Blue, Gr33N and R3D, Pumpkin Spice, Retro) get distinct shades of it.
- `uninstall.sh --data` no longer takes an empty (0-byte) database as the one to keep and then deletes every snapshot: it keeps the newest snapshot instead, and deletes nothing if the database is empty or missing but its `-wal` file holds data (and says so at the end, exiting with an error). When copying or exporting the database fails, it now says which step failed and what to do, and removes the half-written copy.
- The daily snapshot skips a damaged database (one Console warning) instead of copying the damage into `backups/`, where it would push the good snapshots out over two weeks.
- `setup.sh` and `uninstall.sh` run from a second clone no longer copy over or delete the files of a Scripts folder that is itself a clone of this repo.
- `setup.sh` updates `~/src/iTerm2-Color-Schemes` only if it is a clone of that repo; any other repo there is used as it is.
- `.tools/srcp_enable_claude_integration.py` stops with a message naming the problem, before changing anything, when `~/.claude/settings.json` has hooks it can't read, instead of a traceback.
- `uninstall.sh --fonts` with no install record now says it may be a manual install, not only one from before 0.11.0.
- Sessions buried with Session > Bury Session when the script starts are colored too.
- If a dialog or right-click item on a tab never finishes (a reply from iTerm2 got lost), the next right-click item on that tab after 10 minutes replaces it, instead of the tab's items staying blocked until a restart.
- **Change to another profile** on a favorite saved under an older preset name no longer lands on the same colors under the other name.
- A `config` value that isn't a usable number (`inf`, `nan`, or a typo like `1e19`) is ignored with a Console warning, like text, instead of stopping new tabs from being colored or favorites from being picked. `.tools/srcp_odds.py` ignores it the same way, instead of crashing or showing odds the script doesn't use.
- A tab opened from a favorite's profile right after the script starts no longer shows a count of adjusted colors that may be wrong; the rating dialog leaves it out.
- New tabs are colored much faster (32 tabs opened at once took 1.5 s instead of 12 s in a test), and a tab that iTerm2 is slow to answer no longer holds up the others. Two tabs opened together don't get the same preset from discovery.
- When **Change to another profile** can't switch a tab, its alert now says why (for example, the profile's "Use separate colors for light and dark mode" setting). So does the re-roll after **Never show this profile**.
- A tab closed while it was still being colored now gets its real end time, instead of being marked "unknown" at the next restart.

## v0.13.1 (2026-11-06)

- The restore steps (docs/REFERENCE.md, Restore) now set the old database and its `-wal` changes aside in a dated folder instead of deleting them, so you can go back if the restore goes wrong.
- `uninstall.sh --data` leaves database snapshots dated in the future (a clock set ahead, or copied from another Mac) in place and says so, instead of deleting them.
- Tabs of a profile with "Use separate colors for light and dark mode" on are left as they are, with one Console line saying how to turn it off, instead of getting colors that don't show. Favorites' profiles have that setting off.
- After you save or remove a favorite, **Remove profile from favorites** now shows or hides correctly in buried sessions (Session > Bury Session) too.
- A favorite opened in another of its saved fonts, because its own font was turned off, shows that font's stars.
- A tab closed while one of the script's dialogs was open no longer prints a traceback in the Console.
- A database rebuilt by newer scripts while the main script runs is noticed right away, and the Console says to restart the script, instead of the database being read as if nothing had changed. This also works after an earlier "rebuild it" message.
- "Change to another profile" says so in a dialog when the tab couldn't get a new look, instead of only in the Console.
- When coloring a tab is given up after the time limit, the Console says whether the tab was changed at all.
- A new tab that can't be colored is given up after 15 seconds, not 30.
- The database build says the database is locked, and whether it was written, instead of stopping with an error when another program holds it.
- Presets with identical accent colors (Aura, Aura Dark) now get distinct ones after a rebuild.

## v0.12.2 (2026-11-05)

- The database build refuses a damaged database, with a sentence on how to restore a snapshot, instead of stopping with an error after copying the damage into `backups/`, where it could push a good snapshot out.
- Restoring a curation export (`sqlite3 schemes.db < srcp-curation-….sql`) waits up to 5 seconds for the main script to finish a write, instead of failing with "database is locked".
- After updating, run `./setup.sh` (with iTerm2 quit, from Terminal.app) or just `python3 .tools/srcp_build_scheme_db.py` once (the database is now schema 8), then restart the main script from the Scripts menu or relaunch iTerm2. Until it's restarted, a main script from before the update would treat about 180 presets as changed and leave them unadjusted.
- From this version on, a main script still running from before an update says to restart it, instead of reading a database built by newer code.
- Presets whose colors have no color space, or "Calibrated" (about 180 of the repo's), are judged and adjusted as iTerm2 actually draws them, in Apple's Generic RGB, instead of as sRGB. Most of them are dark themes that need fewer adjustments now; a few, like Violet Light and Grass, are now adjusted more or judged harder to read.
- The Scripts menu's **srcp_score_presets.py** scores those presets as they look too. It now needs `.tools/srcp_schema.py` (`setup.sh` installs it) and says so instead of scoring without it.
- Each new tab's Console line ends with how long coloring it took, e.g. `(84 ms)`.
- A busy database no longer makes a tab that was colored fine report "took over 30 s and was given up"; its launch is recorded once the database is free.
- Tabs opened while the main script is starting up are colored sooner: they no longer wait for it to finish, and the tabs already open are colored before it reads iTerm2's settings.
- When iTerm2 quits, the main script exits at once, and closes the database first, so its `schemes.db-wal` / `-shm` files are cleaned up (unless another tool has the database open).
- The Console line for a favorite whose saved font you've since turned off says so (it still comes up, in a random font, or the profile's if none is on).
- **Profiles → Favorites** is written again within 30 seconds if its file was deleted.
- The Console warns again about a favorite whose preset goes missing a second time, once its profiles have been rewritten in between.
- **Change to another profile** and **Never show** say in the Console every time there's nothing left to pick, not only the first time.
- A database problem while you save or remove a favorite no longer marks open tabs of it as not favorites.
- **Never show** (from the rating dialog or right-click) gives the tab its new look without waiting for **Profiles → Favorites** to be rewritten; saving and removing favorites don't wait for it either.
- `setup.sh` no longer overwrites a font you already have under the same name; it only updates its own copy, and leaves any other one alone. To get Terminal.app's copy instead, delete yours in Font Book and run `setup.sh` again.
- `./uninstall.sh --repo` keeps the iTerm2-Color-Schemes clone if it holds files of yours that git ignores (for example `notes.bak`), not only changes and commits. `.DS_Store` files don't count.
- `./uninstall.sh --fonts` also removes a font `setup.sh` installed after Terminal.app's own copy has changed, as long as the installed one is unchanged.
- `./uninstall.sh --fonts` keeps its record of what it couldn't check (Homebrew not on PATH, or Terminal.app's copy gone), so a later run can finish.
- `./uninstall.sh --fonts` says what it kept in `setup.sh`'s record.
- `./uninstall.sh` says when it keeps your key bindings, and how to go back to iTerm2's defaults.
- iTerm2 settings backups are named in UTC, so a clock change can't get the newest one deleted.
- `srcp_enable_claude_integration.py` keeps its newest 10 `settings.json` backups instead of all of them.

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
