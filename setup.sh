#!/bin/bash
# Set up iTerm2 color preset scripts on this Mac:
#   1. clone (or update) iTerm2-Color-Schemes into $SRC_DIR (default ~/src)
#   2. import every .itermcolors file as an iTerm2 color preset (no dialogs)
#   3. enable iTerm2's Claude Code integration
#   4. install score_presets.py and AutoLaunch/random_profile_color.py into
#      iTerm2's Scripts folder, if this checkout isn't already that folder
#
# Run from Terminal.app with iTerm2 quit (steps 2-3 refuse to run otherwise):
#   ./setup.sh
#   ./setup.sh --dryrun        # show what would happen, change nothing
#   SRC_DIR=~/code ./setup.sh
set -euo pipefail

DRYRUN=false
for arg in "$@"; do
	case "$arg" in
	--dryrun | --dry-run) DRYRUN=true ;;
	-h | --help)
		sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
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
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$HOME/Library/Application Support/iTerm2/Scripts"
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
if ! $DRYRUN && pgrep -xq iTerm2; then
	echo "iTerm2 is running. Quit it (Cmd-Q) and run this from Terminal.app." >&2
	exit 1
fi

echo "==> Color schemes repo: $REPO_DIR"
run mkdir -p "$SRC_DIR"
if [[ -d "$REPO_DIR/.git" ]]; then
	run git -C "$REPO_DIR" pull --ff-only
else
	run git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

echo "==> Importing color presets"
if [[ -d "$REPO_DIR/schemes" ]]; then
	python3 "$HERE/tools/import_presets.py" "${DRY_FLAG[@]+"${DRY_FLAG[@]}"}" "$REPO_DIR/schemes"
else
	echo "  (skipped: $REPO_DIR/schemes doesn't exist yet)"
fi

echo "==> Enabling iTerm2's Claude Code integration"
python3 "$HERE/tools/enable_claude_integration.py" "${DRY_FLAG[@]+"${DRY_FLAG[@]}"}"

if [[ "$HERE" != "$SCRIPTS_DIR" ]]; then
	echo "==> Installing scripts into $SCRIPTS_DIR"
	run mkdir -p "$SCRIPTS_DIR/AutoLaunch"
	run cp "$HERE/score_presets.py" "$SCRIPTS_DIR/"
	run cp "$HERE/AutoLaunch/random_profile_color.py" "$SCRIPTS_DIR/AutoLaunch/"
fi

$DRYRUN && exit 0
cat <<'EOF'

Done. Next, in iTerm2:
  - Settings > General > Magic > Enable Python API
  - Scripts > Manage > Install Python Runtime (first time only)
  - Relaunch iTerm2 so the AutoLaunch script starts
EOF
