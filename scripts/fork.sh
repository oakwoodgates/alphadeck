#!/usr/bin/env bash
# fork.sh — fork lifecycle: init | up | down | refresh | destroy. Full model: docs/FORKS.md.
#
# A FORK is an isolated experiment stack: its own worktree + branch, its own Postgres/DB/volumes/ports
# (namespaced by the Compose project alphadeck_<name>), cron OFF, seeded ONE-WAY from prod by
# scripts/refresh-fork.sh. Prod is never written by any fork operation — every guard here is about
# making that structural, not conventional.
#
# Usage:
#   scripts/fork.sh init <name> --slot N [--branch <existing>] [--from <base-ref>] [--fresh] [--no-cache]
#       From the MAIN checkout root: create the worktree .claude/worktrees/fork-<name> (a new branch
#       fork/<name> off --from [default: main], or an existing branch via --branch), scaffold its
#       .env.fork from .env.fork.example (slot N -> ports 808N/800N/554(4+N)), bring the stack up, and
#       seed it (refresh-fork --reset; --fresh/--no-cache pass through). Re-runnable: an existing
#       worktree/.env.fork is reused, not clobbered.
#   scripts/fork.sh up | down
#       From the fork worktree root: start / stop the fork stack. `down` KEEPS the fork's volumes (the
#       dev semantics); `destroy` is the one that deletes them.
#   scripts/fork.sh refresh --reset [--fresh] [--no-cache] [--dry-run]
#       From the fork worktree root: re-seed from prod. RESET-ONLY this pass — --reset is REQUIRED and
#       WIPES the fork's own theses (the app-preserving data mode is deferred; docs/FORKS.md).
#   scripts/fork.sh destroy --yes
#       From the fork worktree root: `down -v` — removes the fork's containers AND volumes
#       (alphadeck_<name>_pgdata / _appdata). Without --yes: prints what would be destroyed and exits
#       non-zero (scripts are non-interactive by contract — no prompts). Afterwards it PRINTS the
#       worktree/branch removal commands to run from the main checkout: a script cannot reliably remove
#       the worktree it is executing from (Windows holds the directory open).
#
# Slot ladder: slot N -> app 808N / api 800N / pg 554(4+N). Taken: prod=0, dev=1, sig=2; forks start at 3.
#
# Agent-legible by contract: non-interactive, idempotent where meaningful, exit-code-clean,
# greppable (--> progress + a terminal OK:/FAIL: line).

set -euo pipefail

# Windows/Git Bash: stop MSYS from rewriting our (slash-free) docker args into host paths. No-op on Linux.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

PROD_PROJECT="alphadeck"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # for up/down/refresh/destroy: the FORK WORKTREE root
ENV_FORK="$REPO_ROOT/.env.fork"

say()  { echo "--> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
trap 'fail "unexpected error near line $LINENO"' ERR

usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; }

# Grep-parse ONE key out of an env file — never `source` it (secret values may hold shell-unsafe
# characters); `tr -d '\r'` strips CRLF from Windows working copies (core.autocrlf=true).
envkey() { sed -n "s/^$2=//p" "$1" | tail -1 | tr -d '\r'; }

# FAIL-CLOSED name guard (mirrors refresh-fork.sh — both scripts stand alone): an empty/reserved name, a
# non-[a-z0-9_] name, or anything resolving to prod's project/DB dies loudly BEFORE any Docker call.
guard_name() { # $1 = candidate fork name
  case "$1" in
    "")                 fail "FORK_NAME is empty" ;;
    prod|dev|sig|test)  fail "FORK_NAME='$1' is reserved (a real tier) — refusing" ;;
  esac
  echo "$1" | grep -Eq '^[a-z0-9_]+$' || fail "FORK_NAME='$1' must match ^[a-z0-9_]+\$ (compose project/DB safe)"
  [ "alphadeck_$1" != "$PROD_PROJECT" ] || fail "resolved project collides with prod — refusing"
}

# Load + validate the fork identity from ./.env.fork (up/down/refresh/destroy paths).
load_identity() {
  [ -f "$ENV_FORK" ] || fail "no .env.fork at $REPO_ROOT — run from a fork worktree root (fork.sh init scaffolds it)"
  FORK_NAME="$(envkey "$ENV_FORK" FORK_NAME)"
  guard_name "$FORK_NAME"
  FORK_PROJECT="alphadeck_${FORK_NAME}"
  # Relative -f names + --env-file; callers cd to REPO_ROOT first (the MSYS-path dodge — see refresh-fork.sh).
  FORK_COMPOSE=(-f docker-compose.yml -f docker-compose.fork.yml -p "$FORK_PROJECT" --env-file .env.fork)
}

cmd="${1:-}"; shift || true
case "$cmd" in

  # ---------------------------------------------------------------------------------------- init ----
  init)
    NAME="${1:-}"; shift || true
    [ -n "$NAME" ] || { echo "FAIL: init needs a fork name" >&2; echo; usage; exit 2; }
    guard_name "$NAME"
    SLOT="" ; BRANCH="" ; FROM="main" ; PASSTHRU=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --slot)     SLOT="${2:-}"; shift ;;
        --branch)   BRANCH="${2:-}"; shift ;;
        --from)     FROM="${2:-}"; shift ;;
        --fresh|--no-cache) PASSTHRU+=("$1") ;;
        --help|-h)  usage; exit 0 ;;
        *)          echo "FAIL: unknown init argument: $1" >&2; echo; usage; exit 2 ;;
      esac
      shift
    done
    echo "$SLOT" | grep -Eq '^[3-9]$' \
      || fail "--slot must be a single digit 3-9 (0=prod, 1=dev, 2=sig are taken; the 808N ladder needs one digit)"
    APP_PORT="808${SLOT}" ; API_PORT="800${SLOT}" ; PG_PORT="$((5544 + SLOT))"

    # init runs from the MAIN checkout root: it creates the worktree there. The main checkout has a .git
    # DIRECTORY; a linked worktree has a .git FILE — the discriminator that catches "ran init from a fork".
    [ -d .git ] || fail "run init from the MAIN checkout root (its .git is a directory; a worktree's is a file)"
    WT=".claude/worktrees/fork-${NAME}"

    # Courtesy scan: warn (not fail) if a sibling worktree already claims this slot or name — Docker's
    # port-bind failure is the loud backstop; this just says it earlier.
    for f in .claude/worktrees/*/.env.fork; do
      [ -f "$f" ] || continue
      [ "$f" = "$WT/.env.fork" ] && continue
      other_slot="$(envkey "$f" FORK_SLOT)"; other_name="$(envkey "$f" FORK_NAME)"
      [ "$other_slot" = "$SLOT" ] && echo "WARN: slot $SLOT already claimed by $f (fork '$other_name') — ports will collide if both run"
      [ "$other_name" = "$NAME" ] && echo "WARN: fork name '$NAME' already used by $f"
    done

    if [ -d "$WT" ]; then
      say "worktree $WT already exists — reusing (init is re-runnable)"
    elif [ -n "$BRANCH" ]; then
      say "adding worktree $WT on existing branch $BRANCH"
      git worktree add "$WT" "$BRANCH" || fail "git worktree add failed"
    else
      say "adding worktree $WT on new branch fork/$NAME (off $FROM)"
      git worktree add -b "fork/$NAME" "$WT" "$FROM" || fail "git worktree add failed"
    fi

    # The worktree must carry the fork tooling (a base ref that predates it can't run a fork).
    { [ -f "$WT/.env.fork.example" ] && [ -f "$WT/scripts/refresh-fork.sh" ] && [ -f "$WT/docker-compose.fork.yml" ]; } \
      || fail "the branch in $WT predates the fork tooling — base it on a ref that contains docker-compose.fork.yml"

    if [ -f "$WT/.env.fork" ]; then
      existing="$(envkey "$WT/.env.fork" FORK_NAME)"
      [ "$existing" = "$NAME" ] || fail "$WT/.env.fork already exists for fork '$existing' (not '$NAME') — fix it by hand"
      say ".env.fork already scaffolded — reusing"
    else
      say "scaffolding $WT/.env.fork (name=$NAME slot=$SLOT -> $APP_PORT/$API_PORT/$PG_PORT)"
      sed -e "s/^FORK_NAME=.*/FORK_NAME=$NAME/" \
          -e "s/^FORK_SLOT=.*/FORK_SLOT=$SLOT/" \
          -e "s/^FORK_APP_PORT=.*/FORK_APP_PORT=$APP_PORT/" \
          -e "s/^FORK_API_PORT=.*/FORK_API_PORT=$API_PORT/" \
          -e "s/^FORK_PG_PORT=.*/FORK_PG_PORT=$PG_PORT/" \
          "$WT/.env.fork.example" > "$WT/.env.fork" || fail "scaffolding .env.fork failed"
    fi

    # Hand off to the WORKTREE's own scripts (the fork runs from its worktree, never from here).
    say "bringing the fork stack up (this builds the backend image — first run takes a while)"
    (cd "$WT" && bash scripts/fork.sh up) || fail "fork up failed"
    say "seeding the fork from prod (refresh-fork --reset)"
    (cd "$WT" && bash scripts/refresh-fork.sh --reset ${PASSTHRU[@]+"${PASSTHRU[@]}"}) || fail "fork seed failed"
    echo "OK: fork '$NAME' is up — app http://localhost:$APP_PORT · api http://localhost:$API_PORT/docs · worktree $WT"
    ;;

  # ------------------------------------------------------------------------------------ up / down ----
  up)
    load_identity
    cd "$REPO_ROOT" || fail "cannot cd to worktree root: $REPO_ROOT"
    mkdir -p data/backups data/scoreboard_replay || fail "cannot create the worktree data dirs"
    say "starting fork '$FORK_NAME' (project $FORK_PROJECT)"
    docker compose "${FORK_COMPOSE[@]}" up -d --build || fail "compose up failed"
    echo "OK: fork '$FORK_NAME' up — app http://localhost:$(envkey "$ENV_FORK" FORK_APP_PORT) · api http://localhost:$(envkey "$ENV_FORK" FORK_API_PORT)/docs"
    ;;

  down)
    load_identity
    cd "$REPO_ROOT" || fail "cannot cd to worktree root: $REPO_ROOT"
    say "stopping fork '$FORK_NAME' (volumes are KEPT — use destroy to delete them)"
    docker compose "${FORK_COMPOSE[@]}" down || fail "compose down failed"
    echo "OK: fork '$FORK_NAME' stopped (volumes ${FORK_PROJECT}_pgdata / ${FORK_PROJECT}_appdata kept)"
    ;;

  # ------------------------------------------------------------------------------------- refresh ----
  refresh)
    # Delegate to the engine (which enforces --reset + re-checks every guard itself).
    exec bash "$SCRIPT_DIR/refresh-fork.sh" "$@"
    ;;

  # ------------------------------------------------------------------------------------- destroy ----
  destroy)
    load_identity
    YES=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --yes)     YES=1 ;;
        --help|-h) usage; exit 0 ;;
        *)         echo "FAIL: unknown destroy argument: $1" >&2; echo; usage; exit 2 ;;
      esac
      shift
    done
    if [ "$YES" -ne 1 ]; then
      echo "destroy would REMOVE fork '$FORK_NAME': containers + volumes ${FORK_PROJECT}_pgdata / ${FORK_PROJECT}_appdata."
      echo "Nothing on prod/dev/sig is touched. Re-run with --yes to proceed."
      exit 3
    fi
    cd "$REPO_ROOT" || fail "cannot cd to worktree root: $REPO_ROOT"
    say "destroying fork '$FORK_NAME' (down -v — containers AND volumes)"
    docker compose "${FORK_COMPOSE[@]}" down -v || fail "compose down -v failed"
    echo "OK: fork '$FORK_NAME' destroyed (prod/dev/sig untouched)."
    echo "To remove the worktree + branch, run FROM THE MAIN CHECKOUT root:"
    echo "  git worktree remove .claude/worktrees/fork-${FORK_NAME}"
    echo "  git branch -D fork/${FORK_NAME}    # only if init created the branch and you're done with it"
    ;;

  --help|-h|help|"")
    usage; exit 0
    ;;

  *)
    echo "FAIL: unknown command: $cmd" >&2; echo; usage; exit 2
    ;;
esac
