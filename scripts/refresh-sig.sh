#!/usr/bin/env bash
# refresh-sig.sh — ONE-WAY prod -> Signals Lab (sig) data refresh.
#
# Copies prod's data into the SIG database cheaply and safely: a read-only `pg_dump` of prod (never a
# writable connection) restored into the sig Postgres (project alphadeck_sig, DB alphadeck_sig, host
# :5546). This is the anti-truncation guarantee — prod (`alphadeck`, :5544, volume alphadeck_pgdata) is
# ONLY EVER READ; the refresh direction is prod -> sig only, NEVER the reverse. Full model: docs/SIGNALS_LAB.md.
#
# The Signals Lab runs FROM THE WORKTREE (maximal isolation — the main checkout is never involved in
# running the lab). Two consequences this script handles:
#   - The sig compose files (docker-compose.yml + docker-compose.sig.yml) are the WORKTREE's own; we cd to
#     the worktree root and use RELATIVE -f names (the MSYS-path dodge, see below).
#   - Prod's *.sql dumps live in the MAIN checkout's ./data/backups (prod runs from there). That dir is a
#     shared, READ-ONLY prod->X dump path (the same idiom refresh-dev relies on — regenerable dumps, not the
#     live DB), so we resolve it via `git rev-parse --git-common-dir` and read the newest prod dump from
#     there. The worktree's OWN ./data/backups + ./data/scoreboard_replay are created too (the sig
#     containers host-bind them).
#
# Agent-legible by contract: non-interactive (no prompts), idempotent (drop-create-restore each run),
# exit-code-clean (0 = ok, non-zero = fail), greppable (--> progress + a terminal OK:/FAIL: line).
#
# Usage:
#   scripts/refresh-sig.sh [--from-latest | --fresh] [--no-cache | --with-cache] [--dry-run] [--help]
#
#   --from-latest   (default) use the newest <main-checkout>/data/backups/*.sql (prod's dump dir)
#   --fresh         run `pipeline.backup --label pre-refresh` against PROD first (a read-only pg_dump),
#                   then use that brand-new dump
#   --with-cache    ALSO copy prod's appdata cache volume (EDGAR/price/draft_runs) into sig. DEFAULT ON for
#                   the lab (the backtest wants the caches); this flag is the explicit form of the default
#   --no-cache      DB-only: SKIP the appdata cache copy
#   --dry-run       print the resolved plan and exit; touches NO Docker (safe anywhere, incl. worktrees)
#   --help          this help
#
# PowerShell (no Git Bash) — the restore redirection differs; see docs/SIGNALS_LAB.md / docs/DEV_PROD.md:
#   Get-Content <dump>.sql | docker exec -i <sig-pg> psql -U alphadeck -d alphadeck_sig     # <-- pipe, not `< file`
#
# Prereqs for a real run (NOT --dry-run): the SIG stack is up, and for --fresh the PROD stack is up too.
# Run from the SIGNALS WORKTREE root.

set -euo pipefail

# Windows/Git Bash: stop MSYS from rewriting our (slash-free) docker args into host paths. No-op on Linux.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# --- constants ---------------------------------------------------------------------------------------
PROD_PROJECT="alphadeck"
SIG_PROJECT="alphadeck_sig"
SIG_DB="alphadeck_sig"
PROD_APPDATA_VOL="alphadeck_appdata"       # prod's named appdata volume (project alphadeck)
SIG_APPDATA_VOL="alphadeck_sig_appdata"    # sig's namespaced appdata volume (project alphadeck_sig)
PGUSER="alphadeck"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"                 # the WORKTREE root (sig runs from here)

# Prod's *.sql dumps live in the MAIN checkout's data/backups (prod runs from there). In a linked worktree
# `git rev-parse --git-common-dir` returns the MAIN checkout's absolute .git dir; its parent is that
# checkout. Fall back to this checkout if git can't resolve it (keeps --help/--dry-run working anywhere).
# NB: `(cd "$REPO_ROOT" && git …)` — NOT `git -C "$REPO_ROOT"`: under MSYS_NO_PATHCONV=1 (set above) the
# `-C /c/Users/…` arg reaches native git.exe unconverted and it can't chdir to an MSYS path; the bash `cd`
# builtin is immune. (Same class of Windows trap as the relative-`-f` docker-compose names below.)
_common_git="$(cd "$REPO_ROOT" && git rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$_common_git" ] && [ -d "$_common_git/.." ]; then
  MAIN_ROOT="$(cd "$_common_git/.." && pwd)"
else
  MAIN_ROOT="$REPO_ROOT"
fi
BACKUPS_DIR="$MAIN_ROOT/data/backups"                    # prod's read-only dump path (the shared prod->X dir)
SIG_COMPOSE=(-f docker-compose.yml -f docker-compose.sig.yml -p "$SIG_PROJECT")   # relative -f names; we cd to REPO_ROOT before use (see below)

# --- helpers -----------------------------------------------------------------------------------------
say()  { echo "--> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
trap 'fail "unexpected error near line $LINENO"' ERR

# Print the header comment block (skip the shebang; strip a leading "# "; stop at the first non-# line).
usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; }

# --- args --------------------------------------------------------------------------------------------
SOURCE="from-latest"   # from-latest | fresh
WITH_CACHE=1           # DEFAULT ON for sig (the backtest wants prod's caches); --no-cache disables
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from-latest) SOURCE="from-latest" ;;
    --fresh)       SOURCE="fresh" ;;
    --with-cache)  WITH_CACHE=1 ;;   # explicit on (already the default) — kept for muscle memory / symmetry
    --no-cache)    WITH_CACHE=0 ;;
    --dry-run)     DRY_RUN=1 ;;
    --help|-h)     usage; exit 0 ;;
    *)             echo "FAIL: unknown argument: $1" >&2; echo; usage; exit 2 ;;
  esac
  shift
done

# --- resolve the dump (filesystem only — safe in --dry-run) ------------------------------------------
latest_dump() { ls -1t "$BACKUPS_DIR"/*.sql 2>/dev/null | head -1; }

# --- DRY RUN: print the plan, touch no Docker -------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
  echo "refresh-sig PLAN (dry-run — nothing executed, no Docker touched)"
  echo "  source        : $SOURCE"
  echo "  prod dump dir : $BACKUPS_DIR  (the MAIN checkout — prod's read-only dump path)"
  if [ "$SOURCE" = "fresh" ]; then
    echo "  step 1        : docker exec <prod backend> python -m pipeline.backup --label pre-refresh   (READ prod)"
    echo "  dump          : (the newest $BACKUPS_DIR/*.sql AFTER that backup)"
  else
    dump="$(latest_dump || true)"
    echo "  dump          : ${dump:-<none found — $BACKUPS_DIR has no *.sql>}"
  fi
  echo "  target DB     : $SIG_DB  (sig Postgres, project $SIG_PROJECT, host :5546)"
  echo "  worktree dirs : ensure $REPO_ROOT/data/backups + $REPO_ROOT/data/scoreboard_replay exist (sig host-binds)"
  echo "  step (drop)   : docker exec <sig pg> psql -U $PGUSER -d postgres -c 'DROP DATABASE IF EXISTS $SIG_DB WITH (FORCE)'"
  echo "  step (create) : docker exec <sig pg> psql -U $PGUSER -d postgres -c 'CREATE DATABASE $SIG_DB'"
  echo "  step (restore): docker exec -i <sig pg> psql -U $PGUSER -d $SIG_DB -f - < <dump>"
  echo "  step (migrate): docker exec <sig backend> python -m db.migrate   (sig branch may be ahead of the dump)"
  if [ "$WITH_CACHE" -eq 1 ]; then
    echo "  step (cache)  : copy volume $PROD_APPDATA_VOL -> $SIG_APPDATA_VOL (read-only from prod) [DEFAULT ON]"
  else
    echo "  step (cache)  : SKIPPED (--no-cache)"
  fi
  echo "  prod writes   : NONE (prod is only ever pg_dump-read)"
  echo "OK: dry-run plan printed (no changes made)"
  exit 0
fi

# ===================================== REAL RUN (Phase B) ============================================
command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"

# Run docker compose from REPO_ROOT with RELATIVE -f names. On Windows/Git Bash an absolute MSYS path
# ($REPO_ROOT = /c/Users/...) is NOT converted for docker.exe under MSYS_NO_PATHCONV=1 and gets mangled to
# `C:\c\Users\...` (the refresh-dev Phase-B lesson). ($BACKUPS_DIR is only ever used for a bash `< file`
# redirection + `ls`, never passed to docker.exe, so its absolute path is safe.)
cd "$REPO_ROOT" || fail "cannot cd to worktree root: $REPO_ROOT"

# Ensure the worktree's OWN data dirs exist — the sig containers host-bind ./data/backups +
# ./data/scoreboard_replay (sig runs from here). Idempotent; not the prod dump dir (that's $BACKUPS_DIR).
mkdir -p "$REPO_ROOT/data/backups" "$REPO_ROOT/data/scoreboard_replay" \
  || fail "cannot create the worktree data dirs under $REPO_ROOT/data"

# --fresh: a brand-new read-only pg_dump of PROD, then use it (it lands in the MAIN checkout's data/backups
# = $BACKUPS_DIR, prod's own bind).
if [ "$SOURCE" = "fresh" ]; then
  say "resolving the prod backend container (for a fresh pre-refresh dump)"
  PROD_BACKEND="$(docker compose -p "$PROD_PROJECT" ps -q backend || true)"
  [ -n "$PROD_BACKEND" ] || fail "prod backend not running — start prod (make prod-up) for --fresh, or use --from-latest"
  say "dumping prod (READ-ONLY): pipeline.backup --label pre-refresh"
  docker exec "$PROD_BACKEND" python -m pipeline.backup --label pre-refresh \
    || fail "prod pre-refresh backup failed"
fi

dump="$(latest_dump || true)"
[ -n "$dump" ] || fail "no dump found in $BACKUPS_DIR (expected *.sql; run with --fresh, or use the DB-snapshot button on prod)"
say "using dump: $dump"

say "resolving the sig Postgres + backend containers"
SIG_PG="$(docker compose "${SIG_COMPOSE[@]}" ps -q postgres || true)"
[ -n "$SIG_PG" ] || fail "sig Postgres not up — bring the sig stack up first (see docs/SIGNALS_LAB.md)"
SIG_BACKEND="$(docker compose "${SIG_COMPOSE[@]}" ps -q backend || true)"
[ -n "$SIG_BACKEND" ] || fail "sig backend not up — bring the sig stack up first (see docs/SIGNALS_LAB.md)"

# Drop + recreate the sig DB. WITH (FORCE) terminates the sig backend's open connections (Postgres 13+),
# so the drop can't hang on 'database is being accessed by other users'. Prod is never touched here.
say "dropping + recreating $SIG_DB on the sig Postgres"
docker exec "$SIG_PG" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS $SIG_DB WITH (FORCE)" || fail "drop $SIG_DB failed"
docker exec "$SIG_PG" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $SIG_DB" || fail "create $SIG_DB failed"

# Restore the plain-SQL pg_dump into the fresh sig DB. `-f -` reads the dump from stdin (bash redirection);
# the PowerShell form pipes Get-Content instead (see the header + docs/SIGNALS_LAB.md).
say "restoring the dump into $SIG_DB"
docker exec -i "$SIG_PG" psql -U "$PGUSER" -d "$SIG_DB" -v ON_ERROR_STOP=1 -f - < "$dump" \
  || fail "restore into $SIG_DB failed"

# Migrate forward: the sig branch may carry newer migrations than the prod dump. Idempotent.
say "applying migrations forward (db.migrate) in the sig backend"
docker exec "$SIG_BACKEND" python -m db.migrate || fail "db.migrate failed"

# Optional: copy prod's appdata cache (EDGAR/price/draft_runs) into sig. Read-only mount of prod's volume.
# DEFAULT ON for the lab (the backtest leans on the caches); pass --no-cache to skip.
if [ "$WITH_CACHE" -eq 1 ]; then
  say "copying appdata cache $PROD_APPDATA_VOL -> $SIG_APPDATA_VOL (prod mounted read-only)"
  docker run --rm -v "$PROD_APPDATA_VOL":/from:ro -v "$SIG_APPDATA_VOL":/to alpine \
    sh -c 'cp -a /from/. /to/' || fail "appdata cache copy failed"
fi

# Report counts from the refreshed sig DB (-tAc = tuples-only, unaligned, one command).
theses="$(docker exec "$SIG_PG" psql -U "$PGUSER" -d "$SIG_DB" -tAc 'SELECT count(*) FROM thesis' | tr -d '[:space:]')"
calls="$(docker exec "$SIG_PG" psql -U "$PGUSER" -d "$SIG_DB" -tAc 'SELECT count(*) FROM calls' | tr -d '[:space:]')"

echo "OK: sig refreshed from $(basename "$dump") (${theses:-?} theses, ${calls:-?} calls)"
