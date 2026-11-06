#!/bin/bash
# Format check, lint, type check and test everything (settings in pyproject.toml):
#   ./lint.sh               # check only
#   ./lint.sh --fix         # apply ruff format and safe ruff fixes first
#   ./lint.sh --allow-skip  # pass even if pyright and the tests were skipped (no iTerm2 Python)
# Needs uv (https://docs.astral.sh/uv/) for uvx, and shellcheck (brew install shellcheck).
set -euo pipefail

RUFF="ruff@0.16.9"
PYRIGHT="pyright@1.1.414"
# The iterm2 module's type hints come from iTerm2's own Python runtime.
ITERM_PY="$HOME/.config/iterm2/AppSupport/uv/venvs/3.12/bin/python"
TEST_LIMIT_S=120

FIX=false
ALLOW_SKIP=false
for arg in "$@"; do
	case "$arg" in
	--fix) FIX=true ;;
	--allow-skip) ALLOW_SKIP=true ;;
	-h | --help)
		sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	*)
		echo "Unknown option: $arg (try --help)" >&2
		exit 2
		;;
	esac
done

cd "$(dirname "$0")"
if $FIX; then
	uvx "$RUFF" format .
	uvx "$RUFF" check --fix .
fi

status=0
skipped=0
echo "==> ruff format"
uvx "$RUFF" format --check . || status=1
echo "==> ruff check"
uvx "$RUFF" check . || status=1
echo "==> pyright"
if [[ -x "$ITERM_PY" ]]; then
	uvx "$PYRIGHT" --pythonpath "$ITERM_PY" || status=1
else
	echo "  (skipped: iTerm2's Python runtime not found at $ITERM_PY)"
	skipped=1
fi
# Pyright skips dot-folders even when listed, so check a copy of .tools/ (Python 3.9).
echo "==> pyright .tools/ (copied to a temp folder; paths in errors are the copies)"
tools_tmp="$(mktemp -d)"
cp .tools/*.py "$tools_tmp/"
printf '{"pythonVersion": "3.9", "typeCheckingMode": "standard"}\n' >"$tools_tmp/pyrightconfig.json"
(cd "$tools_tmp" && uvx "$PYRIGHT") || status=1
rm -rf "$tools_tmp"
echo "==> shellcheck"
shellcheck setup.sh uninstall.sh lint.sh || status=1
# Database-layer tests, against a temp folder (never the real DB or iTerm2's settings).
echo "==> tests"
if [[ -x "$ITERM_PY" ]]; then
	# -W: the iterm2 module imports websockets.legacy, which warns on every run. A hung test
	# fails the run after TEST_LIMIT_S (stock macOS has no timeout command; perl's alarm
	# kills Python with SIGALRM, exit 142, but not processes it started). The suite takes
	# about 20 s.
	rc=0
	perl -e 'alarm shift; exec @ARGV or die "exec: $!\n"' "$TEST_LIMIT_S" \
		"$ITERM_PY" -W "ignore:websockets.legacy is deprecated:DeprecationWarning" \
		-m unittest discover -s .tests -p 'srcp_test_*.py' || rc=$?
	if ((rc == 142)); then
		echo "  tests stopped after $TEST_LIMIT_S s: one hangs"
		status=1
	elif ((rc != 0)); then
		status=1
	fi
else
	echo "  (skipped: iTerm2's Python runtime not found at $ITERM_PY)"
	skipped=1
fi
# CLAUDE.md: VERSION is bumped on every change to the main script, so the Console shows
# which code runs. Checked against the last commit, the last release tag and the commit
# that set the current VERSION (which catches a second commit after a bumped one).
echo "==> VERSION bumped"
main=AutoLaunch/srcp_set_random_color_profile.py
if [[ ! -f "$main" ]]; then
	echo "  (skipped: $main not there; parked as .py.disabled?)"
elif git rev-parse --git-dir >/dev/null 2>&1; then
	# The version alone: a commit that only edits the line's comment didn't bump it.
	version_of() { grep -m1 -o '^VERSION = "[^"]*"' || true; }
	ver="$(version_of <"$main")"
	if [[ -z "$ver" ]]; then
		echo "  no VERSION line in $main"
		status=1
	else
		# "|| true": a repo with no commits yet has nothing to compare with.
		bumped="$(git log -1 --format=%h -S"$ver" -- "$main" 2>/dev/null || true)"
		for base in HEAD "$(git describe --tags --abbrev=0 2>/dev/null || true)" "$bumped"; do
			if [[ -z "$base" ]] || ! git cat-file -e "$base:$main" 2>/dev/null; then continue; fi
			if ! git diff --quiet "$base" -- "$main" &&
				[[ "$(git show "$base:$main" | version_of)" == "$ver" ]]; then
				echo "  $main changed since $base, but VERSION didn't: bump it"
				status=1
				break
			fi
		done
	fi
else
	echo "  (skipped: not a git checkout)"
fi
if ((skipped)) && ! $ALLOW_SKIP; then
	echo "==> FAILED: pyright and the tests need $ITERM_PY; pass --allow-skip to accept skipping them"
	status=1
fi
exit "$status"
