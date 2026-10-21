# Disclaimer

This is a personal project, shared as-is in the hope it's useful. It is not a supported product. The [LICENSE](LICENSE) (MIT) is what governs its use; this page just says the same thing in plain language.

## Use at your own risk

There is no warranty, no support commitment, and no guarantee it works on your Mac or with your version of iTerm2. Read `./setup.sh --dryrun` before running anything for real.

## What it changes on your Mac

| What | Changed by | Backed up first |
|---|---|---|
| iTerm2 settings (`com.googlecode.iterm2`): color presets, a key binding | `setup.sh` | Yes, `~/Library/Preferences/com.googlecode.iterm2.backup-<timestamp>.plist` |
| iTerm2's Scripts folder and Dynamic Profiles | `setup.sh`, the main script | No (regenerated) |
| `~/Library/Fonts`: copies of fonts already on your Mac | `setup.sh` | No |
| Homebrew: MesloLGS Nerd Font | `setup.sh`, if Homebrew is installed | No |
| `~/src/iTerm2-Color-Schemes` (or `$SRC_DIR`) | `setup.sh` | No |
| `~/.local/share/iterm2-color-schemes/` (database) | the scripts | Yes, rolling snapshots |
| `~/.claude/settings.json`, every iTerm2 profile's triggers | `.tools/srcp_enable_claude_integration.py`, only if you run it | Yes |

`./uninstall.sh` undoes all of these except the last row; [docs/REFERENCE.md](docs/REFERENCE.md#claude-code-integration-optional) says how to undo that by hand. Keep your own backup of anything you care about.

## Third-party software and content

- Not affiliated with, endorsed by, or supported by iTerm2, Apple, Anthropic, OpenAI, or the iTerm2-Color-Schemes project. Names are used only to say what this works with.
- Color schemes are downloaded from [iTerm2-Color-Schemes](https://github.com/mbadolato/iTerm2-Color-Schemes) at install time and are covered by that project's license.
- SF Mono and other Apple fonts are copied from your own Mac's Terminal.app and remain under Apple's license. They are not included in this repo; don't redistribute them.
- MesloLGS Nerd Font is installed by Homebrew under its own license.
- iTerm2 changes its settings format and Python API over time. An update may break these scripts.

## Built with AI assistance

Most of this code and documentation was written with Claude Code and reviewed by the author. It has been tested on one Mac (iTerm2 3.7.6, macOS 27). Review it before running it, as you would any script from the internet.

## Your data

Everything stays on your Mac. Nothing is sent anywhere, apart from `git clone`/`git pull` of the schemes repo and Homebrew's download. Rating exports (`.tools/srcp_export_curation.py`) go only where you put them.
