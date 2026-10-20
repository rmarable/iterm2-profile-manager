#!/bin/bash
# Set up iTerm2 color preset scripts on this Mac:
#   1. install Terminal.app's bundled fonts (SF Mono, SF Mono Terminal)
#   2. install MesloLGS Nerd Font with Homebrew, if brew exists
#   3. clone (or update) iTerm2-Color-Schemes into $SRC_DIR (default ~/src)
#   4. import every .itermcolors file as an iTerm2 color preset (no dialogs)
#   5. build the light/dark scheme database the AutoLaunch script picks from
#   6. bind Ctrl-Opt-Cmd-S to the "Save/update this profile" dialog
#   7. install the scripts into iTerm2's Scripts folder, if this checkout isn't
#      already that folder, and remove retired AutoLaunch scripts
#
# Run from Terminal.app with iTerm2 quit (it refuses otherwise):
#   ./setup.sh
#   ./setup.sh --dryrun        # show what would happen, change nothing
#   SRC_DIR=~/code ./setup.sh
set -euo pipefail

DRYRUN=false
for arg in "$@"; do
	case "$arg" in
	--dryrun | --dry-run) DRYRUN=true ;;
	-h | --help)
		sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
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
# -P: run via the ~/.config/iterm2/AppSupport symlink, this still matches SCRIPTS_DIR.
HERE="$(cd "$(dirname "$0")" && pwd -P)"
SCRIPTS_DIR="$HOME/Library/Application Support/iTerm2/Scripts"
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
# Not `pgrep -x iTerm2`, which can miss it; match the executable name instead.
# Not `grep -q`: it stops reading early, and pipefail would turn ps's SIGPIPE into a pass.
# shellcheck disable=SC2009
if ! $DRYRUN && ps -axo comm= | grep '/iTerm2$' >/dev/null; then
	echo "iTerm2 is running. Quit it (Cmd-Q) and run this from Terminal.app." >&2
	exit 1
fi

echo "==> Installing Terminal.app fonts"
# Apple-licensed fonts from this Mac's own macOS install; never commit them.
if [[ -d "$TERMINAL_FONTS" ]]; then
	run mkdir -p "$FONT_DIR"
	for src in "$TERMINAL_FONTS"/*; do
		[[ -e "$src" ]] || continue # empty folder: the glob stays literal
		name="$(basename "$src")"
		if [[ ! -e "$FONT_DIR/$name" ]]; then
			run install -m 644 "$src" "$FONT_DIR/$name"
		elif cmp -s "$src" "$FONT_DIR/$name"; then
			echo "  already installed: $name"
		else
			echo "  updated: $name"
			run install -m 644 "$src" "$FONT_DIR/$name"
		fi
	done
else
	echo "  (skipped: Terminal.app fonts not found)"
fi

echo "==> MesloLGS Nerd Font"
if [[ -e "$FONT_DIR/MesloLGSNerdFontMono-Regular.ttf" ]]; then
	echo "  already installed"
elif command -v brew >/dev/null; then
	run brew install --cask font-meslo-lg-nerd-font || echo "  (brew failed; continuing without it)"
else
	echo "  (skipped: no Homebrew; install MesloLGS Nerd Font by hand, or it stays out of the font pool)"
fi

echo "==> Color schemes repo: $REPO_DIR"
run mkdir -p "$SRC_DIR"
if [[ -d "$REPO_DIR/.git" ]]; then
	run git -C "$REPO_DIR" pull --ff-only || echo "  (pull failed; using the existing checkout)"
else
	run git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

echo "==> Importing color presets"
if [[ -d "$REPO_DIR/schemes" ]]; then
	python3 "$HERE/.tools/srcp_import_presets.py" "${DRY_FLAG[@]+"${DRY_FLAG[@]}"}" "$REPO_DIR/schemes"
else
	echo "  (skipped: $REPO_DIR/schemes doesn't exist yet)"
fi

echo "==> Building scheme database"
python3 "$HERE/.tools/srcp_build_scheme_db.py" "${DRY_FLAG[@]+"${DRY_FLAG[@]}"}" --repo "$REPO_DIR"

echo "==> Key binding: Ctrl-Opt-Cmd-S saves the current profile"
python3 "$HERE/.tools/srcp_add_key_binding.py" "${DRY_FLAG[@]+"${DRY_FLAG[@]}"}"

if [[ "$HERE" != "$(cd "$SCRIPTS_DIR" 2>/dev/null && pwd -P || echo "$SCRIPTS_DIR")" ]]; then
	echo "==> Installing scripts into $SCRIPTS_DIR"
	run mkdir -p "$SCRIPTS_DIR/AutoLaunch"
	run mkdir -p "$SCRIPTS_DIR/.tools"
	run cp "$HERE/srcp_score_presets.py" "$HERE/srcp_ratings.py" "$SCRIPTS_DIR/"
	run cp "$HERE/.tools/srcp_favorites.py" "$HERE/.tools/srcp_popup.js" "$SCRIPTS_DIR/.tools/" # used by srcp_ratings.py
	run cp "$HERE/AutoLaunch/srcp_set_random_color_profile.py" "$SCRIPTS_DIR/AutoLaunch/"
fi
# uninstall.sh disables the script this way when this checkout is the Scripts folder.
if [[ -e "$SCRIPTS_DIR/AutoLaunch/srcp_set_random_color_profile.py.disabled" ]] &&
	[[ ! -e "$SCRIPTS_DIR/AutoLaunch/srcp_set_random_color_profile.py" ]]; then
	echo "==> Re-enabling the AutoLaunch script uninstall.sh disabled"
	run mv "$SCRIPTS_DIR/AutoLaunch/srcp_set_random_color_profile.py.disabled" \
		"$SCRIPTS_DIR/AutoLaunch/srcp_set_random_color_profile.py"
fi
echo "==> Removing retired AutoLaunch scripts (random_profile_color.py, pre-srcp_ names)"
run rm -f "$SCRIPTS_DIR/AutoLaunch/random_profile_color.py" \
	"$SCRIPTS_DIR/AutoLaunch/set_random_color_profile.py"

$DRYRUN && exit 0
cat <<'EOF'

Done. Next, in iTerm2:
  - Settings > General > Magic > Enable Python API
  - Scripts > Manage > Install Python Runtime (first time only)
  - Relaunch iTerm2 so the new fonts appear and the AutoLaunch script starts.
    If an old random_profile_color.py is still running, the relaunch stops it
    (or terminate it in Scripts > Manage > Console).
EOF
