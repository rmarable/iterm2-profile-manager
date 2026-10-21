#!/bin/bash
# Format check, lint, type check and test everything (settings in pyproject.toml):
#   ./lint.sh          # check only
#   ./lint.sh --fix    # apply ruff format and safe ruff fixes first
# Needs uv (https://docs.astral.sh/uv/) for uvx, and shellcheck (brew install shellcheck).
set -euo pipefail

RUFF="ruff@0.16.9"
PYRIGHT="pyright@1.1.414"
# The iterm2 module's type hints come from iTerm2's own Python runtime.
ITERM_PY="$HOME/.config/iterm2/AppSupport/uv/venvs/3.12/bin/python"

cd "$(dirname "$0")"
if [[ "${1:-}" == "--fix" ]]; then
	uvx "$RUFF" format .
	uvx "$RUFF" check --fix .
fi

status=0
echo "==> ruff format"
uvx "$RUFF" format --check . || status=1
echo "==> ruff check"
uvx "$RUFF" check . || status=1
echo "==> pyright"
if [[ -x "$ITERM_PY" ]]; then
	uvx "$PYRIGHT" --pythonpath "$ITERM_PY" || status=1
else
	echo "  (skipped: iTerm2's Python runtime not found at $ITERM_PY)"
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
	# -W: the iterm2 module imports websockets.legacy, which warns on every run.
	"$ITERM_PY" -W "ignore:websockets.legacy is deprecated:DeprecationWarning" \
		-m unittest discover -s .tests -p 'srcp_test_*.py' || status=1
else
	echo "  (skipped: iTerm2's Python runtime not found at $ITERM_PY)"
fi
exit "$status"
