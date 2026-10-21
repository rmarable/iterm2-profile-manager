#!/bin/bash
# Undo setup.sh on this Mac. By default:
#   1. stop the AutoLaunch script from starting (delete the installed copy; in a
#      checkout that is the Scripts folder itself, rename it to .py.disabled)
#   2. remove the srcp_ menu scripts setup.sh copied into iTerm2's Scripts folder
#   3. remove the favorites' Dynamic Profiles file (Profiles > Favorites)
#   4. remove the Ctrl-Opt-Cmd-S save_profile() key binding (iTerm2 prefs, backed up)
# Only when asked:
#   --data     export your curation to ~/srcp-curation-<time>.sql, then delete the
#              scheme DB, its backups and launch history (~/.local/share/iterm2-color-schemes)
#   --presets  remove color presets matching the schemes repo's .itermcolors files
#              (current or earlier versions; ones you edited are kept)
#   --fonts    remove Terminal.app fonts identical to Terminal's copies, and the
#              MesloLGS Nerd Font Homebrew cask
#   --repo     delete the iTerm2-Color-Schemes clone in $SRC_DIR (default ~/src)
#   --all      all of the above
#
# Run from Terminal.app with iTerm2 quit (it refuses otherwise):
#   ./uninstall.sh --dryrun    # show what would happen, change nothing
#   ./uninstall.sh [--data] [--presets] [--fonts] [--repo] | --all
set -euo pipefail

DRYRUN=false DATA=false PRESETS=false FONTS=false REPO=false
for arg in "$@"; do
	case "$arg" in
	--dryrun | --dry-run) DRYRUN=true ;;
	--data) DATA=true ;;
	--presets) PRESETS=true ;;
	--fonts) FONTS=true ;;
	--repo) REPO=true ;;
	--all) DATA=true PRESETS=true FONTS=true REPO=true ;;
	-h | --help)
		sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	*)
		echo "Unknown option: $arg (try --help)" >&2
		exit 2
		;;
	esac
done

SRC_DIR="${SRC_DIR:-$HOME/src}"
REPO_URL="https://github.com/mbadolato/iTerm2-Color-Schemes.git"
REPO_DIR="$SRC_DIR/iTerm2-Color-Schemes"
HERE="$(cd "$(dirname "$0")" && pwd -P)"
SCRIPTS_DIR="$HOME/Library/Application Support/iTerm2/Scripts"
SCRIPTS_REAL="$(cd "$SCRIPTS_DIR" 2>/dev/null && pwd -P || echo "$SCRIPTS_DIR")"
DATA_DIR="$HOME/.local/share/iterm2-color-schemes"
DYNAMIC="$HOME/Library/Application Support/iTerm2/DynamicProfiles/iterm2-profile-manager.plist"
TERMINAL_FONTS="/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts"
FONT_DIR="$HOME/Library/Fonts"
DRY_FLAG=()
if $DRYRUN; then
	DRY_FLAG=(--dryrun)
	echo "Dry run: nothing will be changed."
fi

# Print a command; run it unless this is a dry run.
run() {
	echo "  \$ $*"
	if ! $DRYRUN; then "$@"; fi
}

if [[ "$(uname -s)" != "Darwin" ]]; then
	echo "This is for iTerm2 on macOS only." >&2
	exit 1
fi
# Same check as setup.sh: match the executable name, and let grep read all of ps's output.
# shellcheck disable=SC2009
if ! $DRYRUN && ps -axo comm= | grep '/iTerm2$' >/dev/null; then
	echo "iTerm2 is running. Quit it (Cmd-Q) and run this from Terminal.app." >&2
	exit 1
fi
# iTerm2 set to load its settings from a custom folder or URL would overwrite our changes.
if [[ "$(defaults read com.googlecode.iterm2 LoadPrefsFromCustomFolder 2>/dev/null || true)" == 1 ]]; then
	folder="$(defaults read com.googlecode.iterm2 PrefsCustomFolder 2>/dev/null || echo "unknown location")"
	echo "iTerm2 loads its settings from a custom folder or URL ($folder), so it would" >&2
	echo "overwrite changes made here. In iTerm2, turn off Settings > General > Settings >" >&2
	echo "Load settings from a custom folder or URL, quit it, run this again, then turn" >&2
	echo "that setting back on and save the current settings to the folder." >&2
	$DRYRUN || exit 1
fi

echo "==> AutoLaunch script"
AUTOLAUNCH="$SCRIPTS_DIR/AutoLaunch/srcp_set_random_color_profile.py"
if [[ "$HERE" == "$SCRIPTS_REAL" ]]; then
	# This checkout is the Scripts folder: keep the code, just stop it autostarting.
	if [[ -e "$AUTOLAUNCH" ]]; then
		run mv "$AUTOLAUNCH" "$AUTOLAUNCH.disabled"
	else
		echo "  = not enabled"
	fi
else
	run rm -f "$AUTOLAUNCH" "$SCRIPTS_DIR/AutoLaunch/set_random_color_profile.py"
fi

echo "==> Menu scripts"
if [[ "$HERE" == "$SCRIPTS_REAL" ]]; then
	echo "  = this checkout is the Scripts folder; its files stay (delete the checkout to remove them)"
else
	run rm -f "$SCRIPTS_DIR/srcp_score_presets.py" "$SCRIPTS_DIR/srcp_ratings.py" \
		"$SCRIPTS_DIR/.tools/srcp_favorites.py" "$SCRIPTS_DIR/.tools/srcp_popup.js"
	if [[ -d "$SCRIPTS_DIR/.tools" ]] && ! $DRYRUN; then
		rm -rf "$SCRIPTS_DIR/.tools/__pycache__" # left by srcp_ratings.py importing srcp_favorites
		rmdir "$SCRIPTS_DIR/.tools" 2>/dev/null || true # only if now empty
	fi
fi

echo "==> Favorites' Dynamic Profiles"
if [[ -e "$DYNAMIC" ]]; then
	run rm -f "$DYNAMIC"
else
	echo "  = none"
fi

echo "==> iTerm2 prefs: key binding$($PRESETS && echo " and color presets")"
PREFS_ARGS=("${DRY_FLAG[@]+"${DRY_FLAG[@]}"}")
if $PRESETS; then
	if [[ -d "$REPO_DIR/schemes" ]]; then
		PREFS_ARGS+=(--presets "$REPO_DIR/schemes")
	else
		echo "  (presets skipped: $REPO_DIR/schemes not found, so they can't be matched)"
	fi
fi
python3 "$HERE/.tools/srcp_uninstall_prefs.py" "${PREFS_ARGS[@]+"${PREFS_ARGS[@]}"}"

if $DATA; then
	echo "==> Scheme DB, backups and launch history: $DATA_DIR"
	export_file="$HOME/srcp-curation-$(date +%Y%m%d-%H%M%S).sql"
	newest_backup=""
	for f in "$DATA_DIR"/backups/schemes-*.db; do
		[[ -e "$f" ]] || continue
		if [[ -z "$newest_backup" || "$f" -nt "$newest_backup" ]]; then newest_backup="$f"; fi
	done
	if [[ -e "$DATA_DIR/schemes.db" ]]; then
		run python3 "$HERE/.tools/srcp_export_curation.py" -o "$export_file"
	elif [[ -n "$newest_backup" ]]; then
		echo "  (no schemes.db: exporting from the newest snapshot instead)"
		run python3 "$HERE/.tools/srcp_export_curation.py" --db "$newest_backup" -o "$export_file"
	fi
	if [[ -d "$DATA_DIR" ]]; then
		run rm -rf "$DATA_DIR"
	else
		echo "  = none"
	fi
fi

if $FONTS; then
	echo "==> Terminal.app fonts in $FONT_DIR (only identical copies)"
	# Not grep -q: with pipefail, an early exit can turn defaults' SIGPIPE into "no match".
	if defaults read com.googlecode.iterm2 "New Bookmarks" 2>/dev/null | grep SFMono >/dev/null; then
		echo "  ! an iTerm2 profile still uses SF Mono; change its font after this"
	fi
	if [[ -d "$TERMINAL_FONTS" ]]; then
		for src in "$TERMINAL_FONTS"/*; do
			[[ -e "$src" ]] || continue # empty folder: the glob stays literal
			name="$(basename "$src")"
			if [[ -e "$FONT_DIR/$name" ]] && cmp -s "$src" "$FONT_DIR/$name"; then
				run rm -f "$FONT_DIR/$name"
			fi
		done
	fi
	echo "==> MesloLGS Nerd Font (Homebrew)"
	if command -v brew >/dev/null && brew list --cask font-meslo-lg-nerd-font >/dev/null 2>&1; then
		# Not grep -q: with pipefail, an early exit can turn defaults' SIGPIPE into "no match".
		if defaults read com.googlecode.iterm2 "New Bookmarks" 2>/dev/null | grep MesloLGS >/dev/null; then
			echo "  ! an iTerm2 profile still uses MesloLGS; change its font after this"
		fi
		run brew uninstall --cask font-meslo-lg-nerd-font || echo "  (brew failed; continuing)"
	else
		echo "  = not installed with Homebrew"
	fi
fi

if $REPO; then
	echo "==> Schemes repo: $REPO_DIR"
	if [[ -d "$REPO_DIR/.git" ]] && [[ "$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null)" == "$REPO_URL" ]]; then
		run rm -rf "$REPO_DIR"
	elif [[ -d "$REPO_DIR" ]]; then
		echo "  ! $REPO_DIR isn't a clone of $REPO_URL; left alone"
	else
		echo "  = none"
	fi
fi

$DRYRUN && exit 0
cat <<'EOF'

Done. Relaunch iTerm2. Sessions it colored keep their colors until closed.
Not touched: this checkout, prefs backups (~/.local/share/iterm2-profile-manager/prefs-backups),
the Claude Code integration, and your shell config.
EOF
