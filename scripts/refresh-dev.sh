#!/usr/bin/env bash
# refresh-dev.sh — ONE-WAY prod -> dev data refresh.
#
# Copies prod's data into the DEV database cheaply and safely: a read-only `pg_dump` of prod (never a
# writable connection) restored into the dev Postgres (project alphadeck_dev, DB alphadeck_dev, host
# :5545). This is the anti-truncation guarantee — prod (`alphadeck`, :5544, volume alphadeck_pgdata) is
# ONLY EVER READ. Full model: docs/DEV_PROD.md.
#
# Agent-legible by contract: non-interactive (no prompts), idempotent (drop-create-restore each run),
# exit-code-clean (0 = ok, non-zero = fail), greppable (--> progress + a terminal OK:/FAIL: line).
#
# Usage:
#   scripts/refresh-dev.sh [--from-latest | --fresh] [--with-cache] [--dry-run] [--help]
#
#   --from-latest   (default) use the newest ./data/backups/*.sql
#   --fresh         run `pipeline.backup --label pre-refresh` against PROD first (a read-only pg_dump),
#                   then use that brand-new dump
#   --with-cache    ALSO copy prod's appdata cache volume (EDGAR/price/draft_runs) into dev; default is
#                   DB-only (those caches are regenerable)
#   --dry-run       print the resolved plan and exit; touches NO Docker (safe anywhere, incl. worktrees)
#   --help          this help
#
# PowerShell (no Git Bash) — the restore redirection differs; see docs/DEV_PROD.md for the full sequence:
#   Get-Content <dump>.sql | docker exec -i <dev-pg> psql -U alphadeck -d alphadeck_dev
#
# Prereqs for a real run (NOT --dry-run): the DEV stack is up (`make dev-up`), and for --fresh the PROD
# stack is up too. Run from the MAIN checkout root (worktrees never run the stack).

set -euo pipefail

# Windows/Git Bash: stop MSYS from rewriting our (slash-free) docker args into host paths. No-op on Linux.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# --- constants ---------------------------------------------------------------------------------------
PROD_PROJECT="alphadeck"
DEV_PROJECT="alphadeck_dev"
DEV_DB="alphadeck_dev"
PROD_APPDATA_VOL="alphadeck_appdata"       # prod's named appdata volume (project alphadeck)
DEV_APPDATA_VOL="alphadeck_dev_appdata"    # dev's namespaced appdata volume (project alphadeck_dev)
PGUSER="alphadeck"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUPS_DIR="$REPO_ROOT/data/backups"
DEV_COMPOSE=(-f docker-compose.yml -f docker-compose.dev.yml -p "$DEV_PROJECT")   # relative -f names; we cd to REPO_ROOT before use (see below)

# --- helpers -----------------------------------------------------------------------------------------
say()  { echo "--> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
trap 'fail "unexpected error near line $LINENO"' ERR

# Print the header comment block (skip the shebang; strip a leading "# "; stop at the first non-# line).
usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; }

# --- args --------------------------------------------------------------------------------------------
SOURCE="from-latest"   # from-latest | fresh
WITH_CACHE=0
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from-latest) SOURCE="from-latest" ;;
    --fresh)       SOURCE="fresh" ;;
    --with-cache)  WITH_CACHE=1 ;;
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
  echo "refresh-dev PLAN (dry-run — nothing executed, no Docker touched)"
  echo "  source        : $SOURCE"
  if [ "$SOURCE" = "fresh" ]; then
    echo "  step 1        : docker exec <prod backend> python -m pipeline.backup --label pre-refresh   (READ prod)"
    echo "  dump          : (the newest ./data/backups/*.sql AFTER that backup)"
  else
    dump="$(latest_dump || true)"
    echo "  dump          : ${dump:-<none found — ./data/backups has no *.sql>}"
  fi
  echo "  target DB     : $DEV_DB  (dev Postgres, project $DEV_PROJECT, host :5545)"
  echo "  step (drop)   : docker exec <dev pg> psql -U $PGUSER -d postgres -c 'DROP DATABASE IF EXISTS $DEV_DB WITH (FORCE)'"
  echo "  step (create) : docker exec <dev pg> psql -U $PGUSER -d postgres -c 'CREATE DATABASE $DEV_DB'"
  echo "  step (restore): docker exec -i <dev pg> psql -U $PGUSER -d $DEV_DB -f - < <dump>"
  echo "  step (migrate): docker exec <dev backend> python -m db.migrate   (dev branch may be ahead of the dump)"
  [ "$WITH_CACHE" -eq 1 ] && echo "  step (cache)  : copy volume $PROD_APPDATA_VOL -> $DEV_APPDATA_VOL (read-only from prod)"
  echo "  prod writes   : NONE (prod is only ever pg_dump-read)"
  echo "OK: dry-run plan printed (no changes made)"
  exit 0
fi

# ===================================== REAL RUN (Phase B) ============================================
command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"

# Run docker compose from REPO_ROOT with RELATIVE -f names. On Windows/Git Bash an absolute MSYS path
# ($REPO_ROOT = /c/Users/...) is NOT converted for docker.exe under MSYS_NO_PATHCONV=1 and gets mangled to
# `C:\c\Users\...` (caught in Phase B, 2026-07-26 — `docker compose config` with relative paths hid it).
cd "$REPO_ROOT" || fail "cannot cd to repo root: $REPO_ROOT"

# --fresh: a brand-new read-only pg_dump of PROD, then use it.
if [ "$SOURCE" = "fresh" ]; then
  say "resolving the prod backend container (for a fresh pre-refresh dump)"
  PROD_BACKEND="$(docker compose -p "$PROD_PROJECT" ps -q backend || true)"
  [ -n "$PROD_BACKEND" ] || fail "prod backend not running — start prod (make prod-up) for --fresh, or use --from-latest"
  say "dumping prod (READ-ONLY): pipeline.backup --label pre-refresh"
  docker exec "$PROD_BACKEND" python -m pipeline.backup --label pre-refresh \
    || fail "prod pre-refresh backup failed"
fi

dump="$(latest_dump || true)"
[ -n "$dump" ] || fail "no dump found in $BACKUPS_DIR (expected *.sql; run with --fresh, or use the DB-snapshot button)"
say "using dump: $dump"

say "resolving the dev Postgres + backend containers"
DEV_PG="$(docker compose "${DEV_COMPOSE[@]}" ps -q postgres || true)"
[ -n "$DEV_PG" ] || fail "dev Postgres not up — run 'make dev-up' first"
DEV_BACKEND="$(docker compose "${DEV_COMPOSE[@]}" ps -q backend || true)"
[ -n "$DEV_BACKEND" ] || fail "dev backend not up — run 'make dev-up' first"

# Drop + recreate the dev DB. WITH (FORCE) terminates the dev backend's open connections (Postgres 13+),
# so the drop can't hang on 'database is being accessed by other users'. Prod is never touched here.
say "dropping + recreating $DEV_DB on the dev Postgres"
docker exec "$DEV_PG" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS $DEV_DB WITH (FORCE)" || fail "drop $DEV_DB failed"
docker exec "$DEV_PG" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $DEV_DB" || fail "create $DEV_DB failed"

# Restore the plain-SQL pg_dump into the fresh dev DB. `-f -` reads the dump from stdin (bash redirection);
# the PowerShell form pipes Get-Content instead (see the header + docs/DEV_PROD.md).
say "restoring the dump into $DEV_DB"
docker exec -i "$DEV_PG" psql -U "$PGUSER" -d "$DEV_DB" -v ON_ERROR_STOP=1 -f - < "$dump" \
  || fail "restore into $DEV_DB failed"

# Migrate forward: the dev branch may carry newer migrations than the prod dump. Idempotent.
say "applying migrations forward (db.migrate) in the dev backend"
docker exec "$DEV_BACKEND" python -m db.migrate || fail "db.migrate failed"

# Optional: copy prod's appdata cache (EDGAR/price/draft_runs) into dev. Read-only mount of prod's volume.
if [ "$WITH_CACHE" -eq 1 ]; then
  say "copying appdata cache $PROD_APPDATA_VOL -> $DEV_APPDATA_VOL (prod mounted read-only)"
  docker run --rm -v "$PROD_APPDATA_VOL":/from:ro -v "$DEV_APPDATA_VOL":/to alpine \
    sh -c 'cp -a /from/. /to/' || fail "appdata cache copy failed"
fi

# Report counts from the refreshed dev DB (-tAc = tuples-only, unaligned, one command).
theses="$(docker exec "$DEV_PG" psql -U "$PGUSER" -d "$DEV_DB" -tAc 'SELECT count(*) FROM thesis' | tr -d '[:space:]')"
calls="$(docker exec "$DEV_PG" psql -U "$PGUSER" -d "$DEV_DB" -tAc 'SELECT count(*) FROM calls' | tr -d '[:space:]')"

echo "OK: dev refreshed from $(basename "$dump") (${theses:-?} theses, ${calls:-?} calls)"
