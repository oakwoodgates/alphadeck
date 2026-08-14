#!/usr/bin/env bash
# refresh-fork.sh — ONE-WAY prod -> fork data refresh (RESET mode; the ONLY mode this pass).
#
# Re-seeds a named fork's database from prod cheaply and safely: a read-only `pg_dump` of prod (never a
# writable connection) restored into the fork's Postgres (project alphadeck_<name>, DB alphadeck_<name>).
# This is the anti-truncation guarantee — prod (`alphadeck`, :5544, volume alphadeck_pgdata) is ONLY EVER
# READ; the refresh direction is prod -> fork only, NEVER the reverse. Full model: docs/FORKS.md.
#
# RESET-ONLY, and the flag is REQUIRED: `--reset` DROPS the fork DB and re-clones prod — the fork's own
# theses/baskets/calls are WIPED. The app-preserving "data mode" (merge prod's new facts, keep the fork's
# app plane) is DEFERRED and not built; requiring the explicit flag today means the bare command's meaning
# can never silently flip from "wipe + re-clone" to "merge" when data mode lands.
#
# A fork runs FROM ITS WORKTREE (the Signals-Lab precedent — maximal isolation; the main checkout never
# runs a fork stack). Two consequences this script handles:
#   - The fork compose files (docker-compose.yml + docker-compose.fork.yml) are the WORKTREE's own; we cd
#     to the worktree root and use RELATIVE -f names (the MSYS-path dodge, see below).
#   - Prod's *.sql dumps live in the MAIN checkout's ./data/backups (prod runs from there). That dir is a
#     shared, READ-ONLY prod->X dump path (the same idiom refresh-dev relies on — regenerable dumps, not
#     the live DB), so we resolve it via `git rev-parse --git-common-dir` and read the newest prod dump
#     from there. The worktree's OWN ./data/backups + ./data/scoreboard_replay are created too (the fork
#     containers host-bind them).
#
# Identity comes from ./.env.fork at the worktree root (scaffolded by `fork.sh init`), and the guards are
# FAIL-CLOSED: an empty/reserved FORK_NAME, or anything resolving to prod's project/DB, dies loudly BEFORE
# any Docker call — a misconfigured fork must never be able to touch prod.
#
# Agent-legible by contract: non-interactive (no prompts), idempotent (drop-create-restore each run),
# exit-code-clean (0 = ok, non-zero = fail), greppable (--> progress + a terminal OK:/FAIL: line).
#
# Usage:
#   scripts/refresh-fork.sh --reset [--from-latest | --fresh] [--no-cache | --with-cache] [--dry-run] [--help]
#
#   --reset         REQUIRED: drop + re-clone the fork DB from prod (wipes the fork's own app data; see
#                   RESET-ONLY above). There is deliberately no default mode.
#   --from-latest   (default source) use the newest <main-checkout>/data/backups/*.sql (prod's dump dir)
#   --fresh         run `pipeline.backup --label pre-refresh` against PROD first (a read-only pg_dump),
#                   then use that brand-new dump
#   --with-cache    ALSO copy prod's appdata cache volume (EDGAR/price/draft_runs) into the fork. DEFAULT
#                   ON (a fork wants prod's caches — they are what a UA-less fork reads instead of the
#                   live SEC); this flag is the explicit form of the default
#   --no-cache      DB-only: SKIP the appdata cache copy
#   --dry-run       print the resolved plan and exit; touches NO Docker (safe anywhere, incl. worktrees)
#   --help          this help
#
# PowerShell (no Git Bash) — the restore redirection differs; see docs/FORKS.md for the full sequence:
#   Get-Content <dump>.sql | docker exec -i <fork-pg> psql -U alphadeck -d alphadeck_<name>   # <-- pipe, not `< file`
#
# Prereqs for a real run (NOT --dry-run): the fork stack is up (`fork.sh up`), and for --fresh the PROD
# stack is up too. Run from the FORK WORKTREE root.

set -euo pipefail

# Windows/Git Bash: stop MSYS from rewriting our (slash-free) docker args into host paths. No-op on Linux.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# --- constants ---------------------------------------------------------------------------------------
PROD_PROJECT="alphadeck"
PROD_APPDATA_VOL="alphadeck_appdata"       # prod's named appdata volume (project alphadeck)
PGUSER="alphadeck"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"                 # the FORK WORKTREE root (the fork runs from here)
ENV_FORK="$REPO_ROOT/.env.fork"

# --- helpers -----------------------------------------------------------------------------------------
say()  { echo "--> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
trap 'fail "unexpected error near line $LINENO"' ERR

# Print the header comment block (skip the shebang; strip a leading "# "; stop at the first non-# line).
usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; }

# Grep-parse ONE key out of .env.fork — never `source` it: secret values (API keys) may hold characters
# that are unsafe to eval. `tr -d '\r'` strips the CRLF that core.autocrlf=true puts on Windows working
# copies (a stray \r would poison the name into the guards below and every Docker identifier after them).
envkey() { sed -n "s/^$1=//p" "$ENV_FORK" | tail -1 | tr -d '\r'; }

# --- args --------------------------------------------------------------------------------------------
RESET=0
SOURCE="from-latest"   # from-latest | fresh
WITH_CACHE=1           # DEFAULT ON for forks (prod's caches stand in for the live SEC); --no-cache disables
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --reset)       RESET=1 ;;
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

# --reset is REQUIRED (see the header): the bare command has NO meaning this pass, so its meaning cannot
# silently flip when the app-preserving data mode lands as a future default.
[ "$RESET" -eq 1 ] || fail "refresh-fork is RESET-ONLY this pass — pass --reset (DROPS the fork DB and \
re-clones prod, wiping the fork's own theses). The app-preserving data mode is deferred; see docs/FORKS.md."

# --- fork identity + FAIL-CLOSED guards (before ANY Docker call) -------------------------------------
[ -f "$ENV_FORK" ] || fail "no .env.fork at $REPO_ROOT — run from a fork worktree root (fork.sh init scaffolds it)"
FORK_NAME="$(envkey FORK_NAME)"
case "$FORK_NAME" in
  "")                 fail "FORK_NAME is empty in .env.fork" ;;
  prod|dev|sig|test)  fail "FORK_NAME='$FORK_NAME' is reserved (a real tier) — refusing" ;;
esac
echo "$FORK_NAME" | grep -Eq '^[a-z0-9_]+$' \
  || fail "FORK_NAME='$FORK_NAME' must match ^[a-z0-9_]+\$ (compose project/DB safe)"
FORK_PROJECT="alphadeck_${FORK_NAME}"
FORK_DB="alphadeck_${FORK_NAME}"
FORK_APPDATA_VOL="${FORK_PROJECT}_appdata"   # the fork's namespaced appdata volume (project alphadeck_<name>)
# Belt-and-suspenders (unreachable given the checks above, kept EXPLICIT per the non-negotiable guard):
# nothing this script derives may ever equal prod's project or DB.
{ [ "$FORK_PROJECT" != "$PROD_PROJECT" ] && [ "$FORK_DB" != "alphadeck" ]; } \
  || fail "resolved project/DB collides with prod ('$FORK_PROJECT'/'$FORK_DB') — refusing"

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
# Relative -f names + --env-file (docker-compose.fork.yml interpolates the ports/name from .env.fork);
# we cd to REPO_ROOT before use (see below).
FORK_COMPOSE=(-f docker-compose.yml -f docker-compose.fork.yml -p "$FORK_PROJECT" --env-file .env.fork)

# --- resolve the dump (filesystem only — safe in --dry-run) ------------------------------------------
latest_dump() { ls -1t "$BACKUPS_DIR"/*.sql 2>/dev/null | head -1; }

# --- DRY RUN: print the plan, touch no Docker -------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
  echo "refresh-fork PLAN (dry-run — nothing executed, no Docker touched)"
  echo "  fork          : $FORK_NAME  (project $FORK_PROJECT, DB $FORK_DB)"
  echo "  mode          : RESET (drop + re-clone; the fork's own app data is wiped)"
  echo "  source        : $SOURCE"
  echo "  prod dump dir : $BACKUPS_DIR  (the MAIN checkout — prod's read-only dump path)"
  if [ "$SOURCE" = "fresh" ]; then
    echo "  step 1        : docker exec <prod backend> python -m pipeline.backup --label pre-refresh   (READ prod)"
    echo "  dump          : (the newest $BACKUPS_DIR/*.sql AFTER that backup)"
  else
    dump="$(latest_dump || true)"
    echo "  dump          : ${dump:-<none found — $BACKUPS_DIR has no *.sql>}"
  fi
  echo "  target DB     : $FORK_DB  (fork Postgres, project $FORK_PROJECT)"
  echo "  worktree dirs : ensure $REPO_ROOT/data/backups + $REPO_ROOT/data/scoreboard_replay exist (the fork host-binds)"
  echo "  step (drop)   : docker exec <fork pg> psql -U $PGUSER -d postgres -c 'DROP DATABASE IF EXISTS $FORK_DB WITH (FORCE)'"
  echo "  step (create) : docker exec <fork pg> psql -U $PGUSER -d postgres -c 'CREATE DATABASE $FORK_DB'"
  echo "  step (restore): docker exec -i <fork pg> psql -U $PGUSER -d $FORK_DB -f - < <dump>"
  echo "  step (migrate): docker exec <fork backend> python -m db.migrate   (the fork branch may be ahead of the dump)"
  if [ "$WITH_CACHE" -eq 1 ]; then
    echo "  step (cache)  : copy volume $PROD_APPDATA_VOL -> $FORK_APPDATA_VOL (read-only from prod) [DEFAULT ON]"
  else
    echo "  step (cache)  : SKIPPED (--no-cache)"
  fi
  echo "  prod writes   : NONE (prod is only ever pg_dump-read)"
  echo "OK: dry-run plan printed (no changes made)"
  exit 0
fi

# ===================================== REAL RUN ======================================================
command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"

# Run docker compose from REPO_ROOT with RELATIVE -f names. On Windows/Git Bash an absolute MSYS path
# ($REPO_ROOT = /c/Users/...) is NOT converted for docker.exe under MSYS_NO_PATHCONV=1 and gets mangled to
# `C:\c\Users\...` (the refresh-dev Phase-B lesson). ($BACKUPS_DIR is only ever used for a bash `< file`
# redirection + `ls`, never passed to docker.exe, so its absolute path is safe.)
cd "$REPO_ROOT" || fail "cannot cd to worktree root: $REPO_ROOT"

# Ensure the worktree's OWN data dirs exist — the fork containers host-bind ./data/backups +
# ./data/scoreboard_replay (the fork runs from here). Idempotent; not the prod dump dir (that's $BACKUPS_DIR).
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

say "resolving the fork Postgres + backend containers (project $FORK_PROJECT)"
FORK_PG="$(docker compose "${FORK_COMPOSE[@]}" ps -q postgres || true)"
[ -n "$FORK_PG" ] || fail "fork Postgres not up — run 'fork.sh up' from this worktree first (see docs/FORKS.md)"
FORK_BACKEND="$(docker compose "${FORK_COMPOSE[@]}" ps -q backend || true)"
[ -n "$FORK_BACKEND" ] || fail "fork backend not up — run 'fork.sh up' from this worktree first (see docs/FORKS.md)"

# Drop + recreate the fork DB. WITH (FORCE) terminates the fork backend's open connections (Postgres 13+),
# so the drop can't hang on 'database is being accessed by other users'. Prod is never touched here.
say "dropping + recreating $FORK_DB on the fork Postgres"
docker exec "$FORK_PG" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS $FORK_DB WITH (FORCE)" || fail "drop $FORK_DB failed"
docker exec "$FORK_PG" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $FORK_DB" || fail "create $FORK_DB failed"

# Restore the plain-SQL pg_dump into the fresh fork DB. `-f -` reads the dump from stdin (bash redirection);
# the PowerShell form pipes Get-Content instead (see the header + docs/FORKS.md).
say "restoring the dump into $FORK_DB"
docker exec -i "$FORK_PG" psql -U "$PGUSER" -d "$FORK_DB" -v ON_ERROR_STOP=1 -f - < "$dump" \
  || fail "restore into $FORK_DB failed"

# Migrate forward: the fork branch may carry newer migrations than the prod dump. Idempotent.
say "applying migrations forward (db.migrate) in the fork backend"
docker exec "$FORK_BACKEND" python -m db.migrate || fail "db.migrate failed"

# Optional: copy prod's appdata cache (EDGAR/price/draft_runs) into the fork. Read-only mount of prod's
# volume. DEFAULT ON (the caches are what a UA-less fork reads instead of the live SEC); --no-cache skips.
if [ "$WITH_CACHE" -eq 1 ]; then
  say "copying appdata cache $PROD_APPDATA_VOL -> $FORK_APPDATA_VOL (prod mounted read-only)"
  docker run --rm -v "$PROD_APPDATA_VOL":/from:ro -v "$FORK_APPDATA_VOL":/to alpine \
    sh -c 'cp -a /from/. /to/' || fail "appdata cache copy failed"
fi

# Report counts from the refreshed fork DB (-tAc = tuples-only, unaligned, one command).
theses="$(docker exec "$FORK_PG" psql -U "$PGUSER" -d "$FORK_DB" -tAc 'SELECT count(*) FROM thesis' | tr -d '[:space:]')"
calls="$(docker exec "$FORK_PG" psql -U "$PGUSER" -d "$FORK_DB" -tAc 'SELECT count(*) FROM calls' | tr -d '[:space:]')"

echo "OK: fork '$FORK_NAME' reset from $(basename "$dump") (${theses:-?} theses, ${calls:-?} calls)"
