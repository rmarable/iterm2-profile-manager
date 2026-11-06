#!/bin/bash
# Undo setup.sh on this Mac. By default:
#   1. stop the AutoLaunch script from starting (delete the installed copy; in a
#      checkout that is the Scripts folder itself, this one or another, rename it to
#      .py.disabled)
#   2. remove the srcp_ menu scripts setup.sh copied into iTerm2's Scripts folder
#   3. remove the favorites' Dynamic Profiles file (Profiles > Favorites)
#   4. remove the Ctrl-Opt-Cmd-S save_profile() key binding (iTerm2 prefs, backed up)
# Only when asked:
#   --data     export your curation to ~/srcp-curation-<time>.sql (and keep a copy of the
#              whole DB beside it, .db), then delete the scheme DB, its snapshots and launch
#              history (~/.local/share/iterm2-color-schemes); other files there, and
#              snapshots dated in the future, are kept
#   --presets  remove color presets matching the schemes repo's .itermcolors files
#              (current or earlier versions; ones you edited are kept)
#   --fonts    remove the Terminal.app fonts setup.sh installed, while unchanged since,
#              and the MesloLGS Nerd Font Homebrew cask if it installed it (its record
#              says which; fonts you had stay)
#   --repo     delete the iTerm2-Color-Schemes clone in $SRC_DIR (default ~/src), unless
#              it holds changes, commits, stashes, worktrees or ignored files of yours
#   --all      all of the above
#
# Run from Terminal.app with iTerm2 quit (it refuses otherwise):
#   ./uninstall.sh --dryrun    # show what would happen, change nothing
#   ./uninstall.sh [--data] [--presets] [--fonts] [--repo] | --all
set -euo pipefail

DRYRUN=false DATA=false PRESETS=false FONTS=false REPO=false DATA_REFUSED=false
for arg in "$@"; do
	case "$arg" in
	--dryrun | --dry-run) DRYRUN=true ;;
	--data) DATA=true ;;
	--presets) PRESETS=true ;;
	--fonts) FONTS=true ;;
	--repo) REPO=true ;;
	--all) DATA=true PRESETS=true FONTS=true REPO=true ;;
	-h | --help)
		# The header comment: line 2 up to the first line that isn't a comment.
		awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "$0"
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
# The Scripts folder is a checkout of this project: this one, or another clone (a .git and
# .tools/srcp_build_scheme_db.py, which setup.sh never copies there; a Scripts folder you keep
# in git yourself has none). Its files are tracked there, so they're never copied over or removed. The same
# test is in setup.sh: change both.
SCRIPTS_CHECKOUT=""
if [[ "$HERE" == "$SCRIPTS_REAL" ]]; then
	SCRIPTS_CHECKOUT="this checkout is the Scripts folder"
elif [[ -e "$SCRIPTS_REAL/.git" && -f "$SCRIPTS_REAL/.tools/srcp_build_scheme_db.py" ]]; then
	SCRIPTS_CHECKOUT="the Scripts folder, $SCRIPTS_REAL, is another checkout of this project"
fi
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

# Why a clone holds work of yours (empty if it holds none): uncommitted or untracked files
# (even if git config hides untracked ones), ignored files other than .DS_Store/._*, commits
# no remote has (on a branch, HEAD, a tag or a stash), or linked worktrees. A git error keeps
# the clone too. Read-only, so a dry run reports it as well.
repo_keep_reason() {
	local dir="$1" out ignored
	# Keep git status from refreshing .git/index, so this stays strictly read-only.
	local -x GIT_OPTIONAL_LOCKS=0
	# -z: paths as they are, never quoted (git quotes ones with spaces or accents otherwise).
	if ! out="$(git -C "$dir" status --porcelain -z --ignored --untracked-files=all 2>/dev/null |
		tr '\0' '\n')"; then
		echo "a state git couldn't check"
	elif [[ -n "$out" ]] && grep -qv '^!! ' <<<"$out"; then
		echo "uncommitted or untracked changes"
	elif ignored="$(grep '^!! ' <<<"$out" | grep -Ev '(^!! |/)(\.DS_Store|\._[^/]*)$' || true)" &&
		[[ -n "$ignored" ]]; then
		# Files .gitignore (or a global excludes file) hides, e.g. notes.bak; not what macOS
		# drops into any folder Finder opens.
		echo "ignored files of yours ($(head -n 3 <<<"$ignored" | cut -c4- | paste -sd, - |
			sed 's/,/, /g'))"
	elif ! out="$(git -C "$dir" rev-list --count --all --not --remotes 2>/dev/null)"; then
		echo "a state git couldn't check"
	elif ((out > 0)); then
		echo "commits not on any remote (or stashes)"
	elif ! out="$(git -C "$dir" worktree list --porcelain 2>/dev/null)"; then
		echo "a state git couldn't check"
	elif (($(grep -c '^worktree ' <<<"$out") > 1)); then
		echo "linked worktrees"
	fi
}

# Snapshots the scripts made (.tools/srcp_db_backup.py names them so): time, -n, .partial.
SNAPSHOT_RE='^schemes-([0-9]{8}T[0-9]{6}Z)(-([0-9]+))?\.db(\.partial)?$'
# A real file (not a link) directly in backups/, named as the scripts name snapshots.
is_our_snapshot() {
	[[ "$(dirname "$1")" == "$DATA_DIR/backups" && -f "$1" && ! -L "$1" && "$(basename "$1")" =~ $SNAPSHOT_RE ]]
}
# A finished snapshot named after now (a clock set ahead, or copied from another Mac): as
# .tools/srcp_db_backup.py does, never counted, kept or deleted. Half-written ones go anyway.
NOW_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
is_future_snapshot() {
	[[ "${1##*/}" =~ $SNAPSHOT_RE && -z "${BASH_REMATCH[4]}" && "${BASH_REMATCH[1]}" > "$NOW_UTC" ]]
}
# The DB and the files SQLite and the main script keep next to it.
DB_FILES=(schemes.db schemes.db-wal schemes.db-shm schemes.db-journal .lock)
# What setup.sh installed (format: docs/REFERENCE.md, Files, installed.txt; setup.sh writes it).
MANIFEST="$HOME/.local/share/iterm2-profile-manager/installed.txt"

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
if [[ -n "$SCRIPTS_CHECKOUT" ]]; then
	# A checkout is the Scripts folder: keep the code, just stop it autostarting.
	echo "  ($SCRIPTS_CHECKOUT)"
	if [[ -e "$AUTOLAUNCH" ]]; then
		run mv "$AUTOLAUNCH" "$AUTOLAUNCH.disabled"
	else
		echo "  = not enabled"
	fi
else
	run rm -f "$AUTOLAUNCH" "$SCRIPTS_DIR/AutoLaunch/set_random_color_profile.py"
fi

echo "==> Menu scripts"
if [[ -n "$SCRIPTS_CHECKOUT" ]]; then
	echo "  = $SCRIPTS_CHECKOUT; its files stay (delete that checkout to remove them)"
else
	run rm -f "$SCRIPTS_DIR/srcp_score_presets.py" "$SCRIPTS_DIR/srcp_ratings.py" \
		"$SCRIPTS_DIR/srcp_never_show.py" "$SCRIPTS_DIR/.tools/srcp_favorites.py" \
		"$SCRIPTS_DIR/.tools/srcp_popup.py" "$SCRIPTS_DIR/.tools/srcp_popup.js" \
		"$SCRIPTS_DIR/.tools/srcp_schema.py" "$SCRIPTS_DIR/.tools/srcp_db_backup.py" \
		"$SCRIPTS_DIR/.tools/srcp_iterm_prefs.py"
	if [[ -d "$SCRIPTS_DIR/.tools" ]] && ! $DRYRUN; then
		rm -rf "$SCRIPTS_DIR/.tools/__pycache__" # left by the menu scripts importing .tools/
		rmdir "$SCRIPTS_DIR/.tools" 2>/dev/null || true # only if now empty
	fi
fi

echo "==> Favorites' Dynamic Profiles"
# iTerm2 also keeps copies in its settings ("Is Dynamic Profile"); at launch it removes any
# whose file is gone (iTermDynamicProfileManager reallyReloadDynamicProfiles), so the file is enough.
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
	# Newest by the UTC time in the name (as .tools/srcp_db_backup.py prunes), not mtime; only
	# real files of ours (a link named like one could point at a file of yours).
	newest_backup="" newest_key="" snapshots=0
	for f in "$DATA_DIR"/backups/schemes-*.db; do
		is_our_snapshot "$f" || continue
		[[ "${f##*/}" =~ $SNAPSHOT_RE ]] || continue
		[[ -z "${BASH_REMATCH[4]}" ]] || continue # half-written: nothing to keep, fine to delete
		snapshots=$((snapshots + 1))
		key="${BASH_REMATCH[1]}-$(printf '%09d' "$((10#${BASH_REMATCH[3]:-0}))")"
		is_future_snapshot "$f" && continue
		if [[ -z "$newest_key" || "$key" > "$newest_key" ]]; then
			newest_backup="$f" newest_key="$key"
		fi
	done
	# A copy of the whole DB next to the export, so nothing is lost if the export isn't enough.
	keep_file="${export_file%.sql}.db" kept=false refused=false db="$DATA_DIR/schemes.db"
	if [[ -e "$keep_file" || -e "$export_file" ]]; then # so a failure below removes only ours
		echo "  ! ${export_file%.sql}.* already exists; run this again in a second" >&2
		exit 1
	fi
	# Stop before anything in DATA_DIR is deleted, saying which step failed (and dropping what
	# it left half written).
	data_failed() {
		rm -f "$keep_file" "$export_file" "$export_file.partial"
		echo "  ! $1 failed, so nothing in $DATA_DIR was deleted, and the rest of this run" >&2
		echo "    (--fonts, --repo) didn't happen. If the error above says the DB is damaged or" >&2
		echo "    not a database, restore a snapshot (docs/REFERENCE.md, Restore); then run this again." >&2
		exit 1
	}
	# An empty (0-byte) schemes.db holds nothing, so it counts as none: otherwise the empty
	# file would be kept and every snapshot deleted.
	if [[ -s "$db" ]]; then
		# VACUUM INTO: a consistent copy, -wal changes included, in rollback-journal mode (no
		# -wal/-shm left beside it). A ' in the path is written '' in SQL.
		q="'"
		run sqlite3 "$db" "VACUUM INTO '${keep_file//$q/$q$q}';" || data_failed "Copying $db"
		run python3 "$HERE/.tools/srcp_export_curation.py" -o "$export_file" ||
			data_failed "Exporting $db"
		kept=true
	elif [[ -s "$db-wal" ]]; then
		# The data may all be in the -wal still, and SQLite discards a -wal beside an empty or
		# missing DB when it opens it (checked 2026-11-06): don't judge, and don't open it.
		echo "  ! $db is empty or missing but $db-wal holds data: nothing here deleted." >&2
		echo "    Don't open the DB (SQLite would discard that -wal). Set the files aside and copy" >&2
		echo "    the newest snapshot in (docs/REFERENCE.md, Restore), then run this again." >&2
		refused=true
		DATA_REFUSED=true
	else
		if [[ -e "$db" ]]; then none="schemes.db is empty"; else none="no schemes.db"; fi
		if [[ -n "$newest_backup" ]]; then
			echo "  ($none: keeping and exporting the newest snapshot instead)"
			run cp "$newest_backup" "$keep_file" || data_failed "Copying $newest_backup"
			run python3 "$HERE/.tools/srcp_export_curation.py" --db "$newest_backup" -o "$export_file" ||
				data_failed "Exporting $newest_backup"
			kept=true
		fi
	fi
	if $refused; then
		:
	elif ! $kept && ((snapshots > 0)); then
		echo "  ! $none, and no snapshot to keep (all dated in the future?): left alone"
	elif [[ -d "$DATA_DIR" ]]; then
		# Then (set -e: only if those worked) delete what the scripts made, not the folder.
		for f in "${DB_FILES[@]}"; do
			if [[ -e "$DATA_DIR/$f" ]]; then run rm -f "$DATA_DIR/$f"; fi
		done
		for f in "$DATA_DIR"/backups/schemes-*; do
			is_our_snapshot "$f" || continue
			if is_future_snapshot "$f"; then
				echo "  = left in place (dated in the future): $f"
				continue
			fi
			run rm -f "$f"
		done
		if ! $DRYRUN; then
			rmdir "$DATA_DIR/backups" 2>/dev/null || true # only if now empty
			rmdir "$DATA_DIR" 2>/dev/null || true
		fi
		# Anything else (a manual-*.db snapshot, a folder or link, files of your own) is
		# left, and listed.
		while IFS= read -r f; do
			if is_our_snapshot "$f"; then continue; fi
			if [[ "$(dirname "$f")" == "$DATA_DIR" && " ${DB_FILES[*]} " == *" $(basename "$f") "* ]]; then
				continue
			fi
			echo "  = left in place: $f"
		done < <(find "$DATA_DIR" -mindepth 1 \( -type f -o -type l \) 2>/dev/null | sort)
	else
		echo "  = none"
	fi
fi

if $FONTS; then
	# Only what setup.sh's record lists. No record means an install from before 0.11.0, when
	# it kept none, or fonts installed by hand: nothing can be told apart from fonts of yours,
	# so all are left alone.
	installed() { [[ -f "$MANIFEST" ]] && grep -qxF "$1" "$MANIFEST"; }
	still_listed=() # what the record lists that's still there afterwards
	echo "==> Terminal.app fonts in $FONT_DIR (only ones setup.sh installed, unchanged since)"
	if [[ ! -f "$MANIFEST" ]]; then
		echo "  (no record of what setup.sh installed: from before 0.11.0, or set up by hand; all left alone;"
		echo "   remove fonts by hand in Font Book, and brew uninstall --cask font-meslo-lg-nerd-font)"
	fi
	# Not grep -q: with pipefail, an early exit can turn defaults' SIGPIPE into "no match".
	if defaults read com.googlecode.iterm2 "New Bookmarks" 2>/dev/null | grep SFMono >/dev/null; then
		echo "  ! an iTerm2 profile still uses SF Mono; change its font after this"
	fi
	font_hash() { shasum -a 256 "$1" | cut -d' ' -f1; }
	# The sha256 setup.sh recorded for font NAME (empty if none: recorded before v0.12).
	recorded_hash() {
		N="$1" awk '$1 == "fonthash" && substr($0, length($1) + length($2) + 3) == ENVIRON["N"] { h = $2 }
			END { print h }' "$MANIFEST"
	}
	if [[ -f "$MANIFEST" ]]; then
		# The record's fonts, so one Terminal.app no longer has is still checked (and kept).
		# "|| [[ -n ]]": a last line without a newline (a hand edit) still counts.
		while IFS= read -r line || [[ -n "$line" ]]; do
			[[ "$line" == "font "* ]] || continue
			name="${line#font }"
			[[ -e "$FONT_DIR/$name" ]] || continue # already gone: nothing to remove or keep
			hash="$(recorded_hash "$name")"
			terminal="$TERMINAL_FONTS/$name"
			if [[ -n "$hash" ]]; then
				# The hash of the copy setup.sh installed decides, whatever Terminal has now.
				ours=false
				[[ "$(font_hash "$FONT_DIR/$name")" == "$hash" ]] && ours=true
			else
				# Recorded before hashes were: ours only while identical to Terminal's copy.
				ours=false
				[[ -f "$terminal" ]] && cmp -s "$terminal" "$FONT_DIR/$name" && ours=true
			fi
			if $ours; then
				run rm -f "$FONT_DIR/$name"
				continue
			elif [[ -n "$hash" || -f "$terminal" ]]; then
				echo "  = $name: changed since setup.sh installed it; left alone"
			else
				echo "  = $name: Terminal.app's copy not found, so it can't be checked; left alone"
			fi
			still_listed+=("$line")
			if [[ -n "$hash" ]]; then still_listed+=("fonthash $hash $name"); fi
		done <"$MANIFEST"
	fi
	if [[ -d "$TERMINAL_FONTS" ]]; then
		for src in "$TERMINAL_FONTS"/*; do
			[[ -e "$src" ]] || continue # empty folder: the glob stays literal
			name="$(basename "$src")"
			if [[ -e "$FONT_DIR/$name" ]] && ! installed "font $name"; then
				# setup.sh records what it installs since 0.11.0; one from before isn't listed.
				echo "  = $name: not in setup.sh's record; left alone" \
					"(remove it in Font Book if setup.sh put it there before 0.11.0)"
			fi
		done
	else
		echo "  (Terminal.app fonts not found)"
	fi
	echo "==> MesloLGS Nerd Font (Homebrew)"
	cask=font-meslo-lg-nerd-font
	if ! installed "cask $cask"; then
		echo "  = not installed by setup.sh; left alone"
	elif ! command -v brew >/dev/null; then
		echo "  = Homebrew not found (not on PATH?), so it can't be removed now; kept in the record"
		still_listed+=("cask $cask")
	elif brew list --cask "$cask" >/dev/null 2>&1; then
		# Not grep -q: with pipefail, an early exit can turn defaults' SIGPIPE into "no match".
		if defaults read com.googlecode.iterm2 "New Bookmarks" 2>/dev/null | grep MesloLGS >/dev/null; then
			echo "  ! an iTerm2 profile still uses MesloLGS; change its font after this"
		fi
		if ! run brew uninstall --cask "$cask"; then
			echo "  (brew failed; continuing)"
			still_listed+=("cask $cask")
		fi
	else
		echo "  = not installed with Homebrew"
	fi
	# The record keeps what's still there, so a later --fonts can finish the job.
	if [[ -f "$MANIFEST" ]]; then
		if ((${#still_listed[@]})); then
			kept_lines="$(printf '%s\n' "${still_listed[@]}" | grep -v '^fonthash ' | paste -sd, - | sed 's/,/, /g')"
			echo "  = setup.sh's record keeps what's still there: $kept_lines"
			$DRYRUN || printf '%s\n' "${still_listed[@]}" >"$MANIFEST"
		else
			run rm -f "$MANIFEST"
		fi
	fi
fi

if $REPO; then
	echo "==> Schemes repo: $REPO_DIR"
	# The same test is in setup.sh (before its pull): change both.
	if [[ -d "$REPO_DIR/.git" ]] && [[ "$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null)" == "$REPO_URL" ]]; then
		keep="$(repo_keep_reason "$REPO_DIR")"
		if [[ -n "$keep" ]]; then
			echo "  ! $REPO_DIR has $keep; left alone"
		else
			run rm -rf "$REPO_DIR"
		fi
	elif [[ -d "$REPO_DIR" ]]; then
		echo "  ! $REPO_DIR isn't a clone of $REPO_URL; left alone"
	else
		echo "  = none"
	fi
fi

if $DATA_REFUSED; then
	# Again at the end, where it can't scroll past, and as a failure.
	echo "! The scheme DB folder was left alone: its -wal holds data (see the --data lines above)." >&2
	exit 1
fi
$DRYRUN && exit 0
cat <<'EOF'

Done. Relaunch iTerm2. Sessions it colored keep their colors until closed.
Not touched: this checkout, prefs backups (~/.local/share/iterm2-profile-manager/prefs-backups),
the Claude Code integration (.tools/srcp_enable_claude_integration.py --remove undoes it),
and your shell config.
EOF
