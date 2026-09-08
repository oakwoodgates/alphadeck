#!/usr/bin/env bash
# ensure-venv.sh -- provision (or refresh) THIS checkout's backend venv with the extras the suite needs.
#
# Makes `backend/.venv` exist and carry `pip install -e ".[dev,replay]"` -- exactly what CI installs
# (.github/workflows/ci.yml): the runtime deps, pytest + pytest-xdist (`pytest -n 6`) + pytest-timeout (the
# hang guard), the pinned ruff/black, and duckdb + pyarrow so the replay tests EXECUTE instead of being
# collect-skipped in a lean venv (skipped != passed). Per-checkout BY CONSTRUCTION: the venv lives inside the
# checkout this script sits in, so every git worktree provisions its OWN -- never borrow a sibling's or the
# main checkout's (its editable install points at the OTHER tree; cwd shadowing only makes it look fine).
#
# Agent-legible by contract: non-interactive (no prompts), idempotent (a ready venv is a ~1 s no-op -- the
# stamp + import probe below), non-destructive (never deletes a venv; `rm -rf backend/.venv` is the operator's
# call), exit-code-clean (0 = ready, 1 = fail, 2 = usage), greppable (--> progress + a terminal OK:/FAIL: line).
#
# Usage:
#   scripts/ensure-venv.sh [--check] [--force] [--help]
#
#   (default)   create backend/.venv if absent, then `pip install -e ".[dev,replay]"` unless already current
#   --check     report only: exit 0 if the venv is ready, 1 if not; installs NOTHING
#   --force     re-run the pip install even when the stamp says current
#   --help      this help
#
# Env: ALPHADECK_PYTHON -- the interpreter used to CREATE a missing venv (default: `python`, else `python3`);
#      must be >= 3.11 (requires-python). Ignored once the venv exists.
#
# "Current" = the venv's stamp matches the sha1 of backend/pyproject.toml (so a dependency bump on your branch
# re-pips on the next run) AND scripts/ensure-venv-probe.py passes: the imports are present and the editable
# `app` resolves to THIS checkout's backend/ (a borrowed/copied venv fails that and gets re-pointed).
#
# PowerShell twin: scripts/ensure-venv.ps1 (same contract, same stamp -- they interoperate on one venv).
# Make: `make venv` (the one Makefile target that is per-checkout -- fine from a worktree).

set -euo pipefail

say()   { echo "--> $*"; }
fail()  { echo "FAIL: $*" >&2; exit 1; }
usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; }

MODE="install"
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check)    MODE="check" ;;
        --force)    FORCE=1 ;;
        --help|-h)  usage; exit 0 ;;
        *)          echo "FAIL: unknown argument: $1" >&2; echo; usage; exit 2 ;;
    esac
    shift
done

# --- paths (Windows-native under Git Bash -- `pwd -W` gives C:/... so Python sees a path it understands;
#     on Linux/macOS `pwd -W` is absent and the plain `pwd` fallback applies) ------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && (pwd -W 2>/dev/null || pwd))"
ROOT="$(cd "$SCRIPT_DIR/.." && (pwd -W 2>/dev/null || pwd))"
BACKEND="$ROOT/backend"
VENV="$BACKEND/.venv"
PROBE="$SCRIPT_DIR/ensure-venv-probe.py"
EXTRAS="dev,replay"
STAMP="$VENV/.alphadeck-venv-stamp"

[ -f "$BACKEND/pyproject.toml" ] || fail "no backend/pyproject.toml under $ROOT -- is this an Alpha Deck checkout?"
[ -f "$PROBE" ] || fail "missing $PROBE"

venv_python() {  # the venv's interpreter: Windows layout first, then POSIX
    if [ -x "$VENV/Scripts/python.exe" ]; then echo "$VENV/Scripts/python.exe"
    elif [ -x "$VENV/bin/python" ]; then echo "$VENV/bin/python"
    else echo ""
    fi
}

# --- 1. create the venv if absent ---------------------------------------------------------------------
VPY="$(venv_python)"
if [ -z "$VPY" ]; then
    if [ -e "$VENV" ]; then
        fail "$VENV exists but has no interpreter (a broken venv) -- remove it (rm -rf) and re-run"
    fi
    if [ "$MODE" = "check" ]; then
        echo "FAIL: no venv at $VENV (run scripts/ensure-venv.sh to create it)" >&2; exit 1
    fi
    BASE="${ALPHADECK_PYTHON:-}"
    if [ -z "$BASE" ]; then
        for candidate in python python3; do
            if command -v "$candidate" >/dev/null 2>&1; then BASE="$candidate"; break; fi
        done
    fi
    [ -n "$BASE" ] || fail "no python on PATH -- install Python >= 3.11 or set ALPHADECK_PYTHON"
    if ! "$BASE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        fail "$BASE is $("$BASE" --version 2>&1 || echo 'not runnable') -- need Python >= 3.11 (set ALPHADECK_PYTHON)"
    fi
    say "creating $VENV with $BASE ($("$BASE" --version 2>&1))"
    "$BASE" -m venv "$VENV" || fail "venv creation failed"
    VPY="$(venv_python)"
    [ -n "$VPY" ] || fail "venv created but no interpreter found under $VENV"
fi

# --- 2. the venv's interpreter must run and be >= 3.11 ------------------------------------------------
if ! "$VPY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    fail "$VPY is not runnable or is < 3.11 ($("$VPY" --version 2>&1 || echo 'broken')) -- remove $VENV and re-run"
fi

# --- 3. current? (stamp + probe) -> fast no-op --------------------------------------------------------
# (quote-free one-liner, byte-identical to the .ps1 twin's -- PowerShell 5.1 strips embedded double quotes from native args)
want_stamp="pyproject_sha1=$("$VPY" -c 'import hashlib, pathlib, sys; print(hashlib.sha1(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$BACKEND/pyproject.toml");extras=$EXTRAS"
have_stamp="$(cat "$STAMP" 2>/dev/null || true)"
probe_out=""
if [ "$FORCE" = 0 ] && [ "$have_stamp" = "$want_stamp" ] && probe_out="$("$VPY" "$PROBE" "$BACKEND" 2>&1)"; then
    echo "OK: venv ready (no-op) -- $VPY | $probe_out"
    exit 0
fi
if [ "$MODE" = "check" ]; then
    if [ "$have_stamp" != "$want_stamp" ]; then
        reason="stamp mismatch (never installed, or backend/pyproject.toml changed since)"
    else
        reason="${probe_out:-probe failed}"
    fi
    echo "FAIL: venv at $VENV is not ready -- $reason (run scripts/ensure-venv.sh)" >&2
    exit 1
fi

# --- 4. install (editable + the extras) ---------------------------------------------------------------
if [ "$FORCE" = 1 ]; then
    say "--force: re-running the pip install"
elif [ "$have_stamp" != "$want_stamp" ]; then
    say "venv not current (never installed, or backend/pyproject.toml changed since)"
else
    say "venv not ready: $probe_out"
fi
say "pip install -e \".[$EXTRAS]\" into $VENV (a fresh venv takes a minute or two; pip's wheel cache makes it faster after)"
(cd "$BACKEND" && "$VPY" -m pip install --progress-bar off -e ".[$EXTRAS]") || fail "pip install failed"

# --- 5. prove it, then stamp it -----------------------------------------------------------------------
probe_out="$("$VPY" "$PROBE" "$BACKEND" 2>&1)" || fail "venv still not ready after the install -- $probe_out"
printf '%s' "$want_stamp" > "$STAMP"
echo "OK: venv ready -- $VPY | $probe_out"
