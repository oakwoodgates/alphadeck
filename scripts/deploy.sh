#!/usr/bin/env bash
# deploy.sh — take MERGED code live on the PROD stack, with the procedure's guards built in.
#
# Wraps docs/DEPLOY.md Flow A, which stays the manual fallback: refuse unless it is safe to deploy, take
# a labeled backup, rebuild ONLY the named services from the merged main (GIT_SHA is read from the
# checkout, never taken as an argument), apply the frontend-restart rule, then VERIFY the real artifact.
#
# Usage:
#   scripts/deploy.sh --label <label> <service>... [--dry-run]
#       services: frontend | backend | cron (one or more). backup -> build -> restart rule -> verify.
#   scripts/deploy.sh --env-only cron [--label <label>] [--dry-run]
#       a cron ENV change: recreate the sidecar with NO build (`up -d --no-deps --no-build cron` keeps
#       its image). No backup unless --label is given.
#   scripts/deploy.sh --help
#
#   --label L    names the pre-deploy backup (labeled = prune-exempt). Letters, digits, '.', '_', '-'.
#                REQUIRED for a build.
#   --dry-run    runs the LOCAL checks (git, the clock) and prints every stack check, command, and
#                verification; touches no Docker and no endpoint, changes nothing (`git ls-remote`
#                stands in for `git fetch`, so not even a remote-tracking ref moves).
#
# Refuses, changing nothing (exit 3), unless: this is the MAIN checkout (not a worktree) and has its
# .env; the tree is clean; the branch is main; HEAD == origin/main after a fetch (deploy what is merged);
# the America/New_York clock is outside 22:20-22:50 (the 22:30 nightly pass); no pass is running
# (/admin/status) and the call-of-record lock is free (pipeline/run_lock.py).
#
# Verifies: the backend is healthy; :8080/api/health answers 200 (the /api PROXY); ALPHADECK_IMAGE_SHA
# in each rebuilt backend/cron container equals the sha built; the cron container runs and printed its
# boot lines; /admin/status reads, with no new cron.alarms line and no quiet -> loud cron.status.
#
# Exit: 0 deployed + verified (or a clean dry-run) · 2 usage · 3 refused (nothing changed) ·
#       4 a step failed · 5 deployed but a verification failed. On 4/5: the stack-recovery skill.
# Knobs: DEPLOY_WAIT_S (default 240) bounds each readiness poll; DEPLOY_POLL_S (default 5) is its interval.
#
# Agent-legible by contract: non-interactive, exit-code-clean, greppable (--> progress, ok:/REFUSE:
# lines, a terminal OK:/FAIL: line). Run it from the MAIN checkout, on a clean main; never from a worktree.

set -euo pipefail

# --- constants ---------------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROD=(-f docker-compose.yml -f docker-compose.prod.yml)   # relative -f names; we cd to REPO_ROOT (the MSYS-path dodge, refresh-dev.sh)
BACKEND_C="alphadeck-backend-1"
CRON_C="alphadeck-cron-1"
FRONTEND_C="alphadeck-frontend-1"
ADMIN_URL="http://localhost:8000/admin/status"
PROXY_URL="http://localhost:8080/api/health"
WINDOW_FROM=2220   # HHMM America/New_York, inclusive both ends: the nightly pass fires at 22:30
WINDOW_TO=2250
WAIT_S="${DEPLOY_WAIT_S:-240}"
POLL_S="${DEPLOY_POLL_S:-5}"
RECOVERY="see the stack-recovery skill (.claude/skills/stack-recovery/SKILL.md); the manual steps are docs/DEPLOY.md Flow A"

# GIT_SHA is a provenance claim stamped on every call-of-record row: it comes from `git rev-parse HEAD`
# below and nowhere else, so a stale value exported in the caller's shell must not leak into a build.
unset GIT_SHA

# Read /admin/status's JSON in the backend's own Python (the host may have no jq or python). Fail-closed:
# the caller accepts only a known cron.status. `running` = the verdict says so, OR the newest run artifact
# is still running inside its timeout: the verdict ranks unhealthy/stale/gappy ABOVE running, so a pass in
# flight on an alarmed stack reads `unhealthy` and only unfinished_run shows it. Single quotes only: this
# string crosses MSYS into docker.exe on Windows.
STATUS_PY="import json,sys; d=json.load(sys.stdin); c=d.get('cron') or {}; u=d.get('unfinished_run') or {}; run=c.get('status')=='running' or (u.get('status')=='running' and not u.get('past_timeout')); print('status=%s' % c.get('status')); print('running=%d' % run); [print('alarm=' + ' '.join(str(a).split())) for a in (c.get('alarms') or [])]"

# Is the call-of-record advisory lock held (a daily pass, an Admin "Run daily now", or a backfill)? A
# READ of pg_locks, never pipeline.run_lock.probe_pass_lock: that one TAKES the lock for an instant and
# could make a pass starting in that instant refuse itself. Prints `free` or `held: <who>`.
LOCK_PY="from db.session import connect; from pipeline.run_lock import LOCK_NAMESPACE as k1, LOCK_ID_CALL_OF_RECORD as k2, describe_holder; c=connect(); n=c.execute('SELECT count(*) AS n FROM pg_locks WHERE locktype = %s AND granted AND classid = %s AND objid = %s AND objsubid = 2 AND database = (SELECT oid FROM pg_database WHERE datname = current_database())', ('advisory', k1, k2)).fetchone()['n']; print(('held: ' + (describe_holder(c) or 'an unnamed session')) if n else 'free'); c.close()"

# --- helpers -----------------------------------------------------------------------------------------
say()  { echo "--> $*"; }
ok()   { echo "    ok: $*"; }
usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; }
usage_refuse() { echo "REFUSE: $*" >&2; echo >&2; usage >&2; exit 2; }
step_fail() { echo "FAIL: $* — $RECOVERY" >&2; exit 4; }
trap 'echo "FAIL: unexpected error near line $LINENO — $RECOVERY" >&2; exit 4' ERR

REFUSED=0
refuse() { echo "REFUSE: $*" >&2; REFUSED=$((REFUSED + 1)); }
VFAILED=0
vfail() { echo "    FAIL: $*" >&2; VFAILED=$((VFAILED + 1)); }

# Windows/Git Bash: no MSYS path rewriting of docker's arguments (a /path argument would reach docker.exe as
# C:/Program Files/Git/path). Scoped to docker on purpose, NOT exported: curl.exe is native too and must keep
# its normal conversion. A no-op on Linux.
docker() { MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' command docker "$@"; }

# Print a command, then run it; under --dry-run only print it.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    would run: $*"
    return 0
  fi
  echo "+ $*"
  "$@"
}

has() { # is $1 among the requested services?
  local s
  for s in ${SERVICES[@]+"${SERVICES[@]}"}; do [ "$s" = "$1" ] && return 0; done
  return 1
}

# The wall clock in America/New_York, whatever the host's TZ, as "YYYY-mm-dd HH:MM EST|EDT". The IANA
# name first (Linux, any host with tzdata). Git for Windows ships NO zoneinfo, so there
# `TZ=America/New_York date` SILENTLY prints UTC labeled "GMT" (MEASURED 2026-10-17 on the deploy host,
# 05:56 GMT for 01:56 EDT): hence the zone check, and the fallback to the POSIX rule for US Eastern since
# 2007 (DST from the second Sunday of March to the first Sunday of November), which needs no zoneinfo at
# all. Neither yields EST/EDT -> non-zero, and the caller refuses: the window cannot be checked.
ny_clock() {
  local tz out
  for tz in America/New_York 'EST5EDT,M3.2.0,M11.1.0'; do
    out="$(TZ="$tz" date '+%Y-%m-%d %H:%M %Z' 2>/dev/null || true)"
    if [[ "$out" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}\ E[SD]T$ ]]; then
      echo "$out"
      return 0
    fi
  done
  return 1
}

# Read /admin/status into S_STATUS (one cron.status word), S_RUNNING (0|1) and S_ALARMS (one line each).
# Non-zero, with S_ERR, when it cannot be read or parsed into a known verdict (fail-closed).
S_STATUS="" ; S_RUNNING="" ; S_ALARMS=() ; S_ERR=""
read_status() {
  local json parsed line
  S_STATUS="" ; S_RUNNING="" ; S_ALARMS=() ; S_ERR=""
  if ! json="$(curl -fsS --max-time 60 "$ADMIN_URL" 2>&1)"; then
    S_ERR="curl: ${json##*$'\n'}"
    return 1
  fi
  if ! parsed="$(printf '%s' "$json" | docker exec -i "$BACKEND_C" python -c "$STATUS_PY" 2>&1)"; then
    S_ERR="parse: ${parsed##*$'\n'}"
    return 1
  fi
  while IFS= read -r line; do
    line="${line%$'\r'}"
    case "$line" in
      status=*)  S_STATUS="${line#status=}" ;;
      running=*) S_RUNNING="${line#running=}" ;;
      alarm=*)   S_ALARMS+=("${line#alarm=}") ;;
    esac
  done <<< "$parsed"
  case "$S_STATUS" in
    healthy|stale|never_ran|unhealthy|gappy|running) ;;
    *) S_ERR="unrecognized cron.status '${S_STATUS}'"; return 1 ;;
  esac
  case "$S_RUNNING" in
    0|1) ;;
    *) S_ERR="unrecognized running flag '${S_RUNNING}'"; return 1 ;;
  esac
}

print_alarms() {
  local a
  for a in ${S_ALARMS[@]+"${S_ALARMS[@]}"}; do echo "        alarm: $a"; done
}

is_quiet() { case "$1" in healthy|never_ran|running) return 0 ;; esac; return 1; }

# Poll a predicate until it holds or DEPLOY_WAIT_S passes (always at least one try).
poll() {
  local deadline=$((SECONDS + WAIT_S))
  while :; do
    "$@" && return 0
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep "$POLL_S"
  done
}
LAST=""
backend_healthy() {
  LAST="$(docker inspect --format '{{.State.Health.Status}}' "$BACKEND_C" 2>&1)" || true
  LAST="${LAST//$'\r'/}"
  [ "$LAST" = "healthy" ]
}
proxy_200() {
  local out
  out="$(curl -s --max-time 10 -w '\n%{http_code}' "$PROXY_URL" 2>&1)" || true
  LAST="${out##*$'\n'}"
  LAST="${LAST//$'\r'/}"
  [ "$LAST" = "200" ]
}
CRON_LOGS=""
cron_up() {
  LAST="$(docker inspect --format '{{.State.Status}}' "$CRON_C" 2>&1)" || true
  LAST="${LAST//$'\r'/}"
  [ "$LAST" = "running" ]
}
cron_booted() { # running AND the sidecar printed its second boot line (it does so before any DB wait)
  cron_up || return 1
  CRON_LOGS="$(docker logs --tail 60 "$CRON_C" 2>&1)" || true
  [[ "$CRON_LOGS" == *"daily-cron: each pass bounded at"* ]]
}
image_sha() { # ALPHADECK_IMAGE_SHA inside container $1 ("" when unset or unreadable)
  local v
  v="$(docker exec "$1" printenv ALPHADECK_IMAGE_SHA 2>/dev/null)" || v=""
  v="${v//$'\r'/}"
  echo "${v%%$'\n'*}"
}

# --- args --------------------------------------------------------------------------------------------
LABEL=""
ENV_ONLY=0
DRY_RUN=0
SERVICES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --label)   [ $# -ge 2 ] || usage_refuse "--label needs a value"; LABEL="$2"; shift ;;
    --label=*) LABEL="${1#--label=}" ;;
    --env-only) ENV_ONLY=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --help|-h) usage; exit 0 ;;
    -*)        usage_refuse "unknown option: $1" ;;
    frontend|backend|cron) SERVICES+=("$1") ;;
    *)         usage_refuse "unknown service '$1' — the services are frontend, backend, cron" ;;
  esac
  shift
done

# Canonical order, duplicates dropped.
_want=()
for _s in frontend backend cron; do has "$_s" && _want+=("$_s"); done
SERVICES=(${_want[@]+"${_want[@]}"})
[ ${#SERVICES[@]} -gt 0 ] || usage_refuse "name at least one service: frontend, backend, cron"

if [ "$ENV_ONLY" -eq 1 ]; then
  [ "${SERVICES[*]}" = "cron" ] \
    || usage_refuse "--env-only recreates the cron sidecar only (scripts/deploy.sh --env-only cron); a frontend or backend change is a build"
else
  [ -n "$LABEL" ] \
    || usage_refuse "a build needs --label <label> — the pre-deploy backup is not optional (e.g. --label pre-deploy-pr123)"
fi
if [ -n "$LABEL" ] && ! [[ "$LABEL" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
  usage_refuse "--label '$LABEL' must be letters, digits, '.', '_', '-' (start alphanumeric, at most 64): pipeline.backup slugs anything else, and a label with no letters or digits makes an UNLABELED, prunable dump"
fi

cd "$REPO_ROOT" || step_fail "cannot cd to the repo root: $REPO_ROOT"

if [ "$ENV_ONLY" -eq 1 ]; then
  what="cron (env-only: recreate with no build; the image is kept)"
else
  what="${SERVICES[*]} (rebuild)"
fi
if [ "$DRY_RUN" -eq 1 ]; then
  echo "deploy PLAN for $what — DRY RUN: no backup, no build, no restart, no Docker or endpoint touched"
else
  echo "deploy $what — PROD, from $REPO_ROOT"
fi

# ===================================== CHECKS (nothing changes) ======================================
say "local checks"

if [ -d .git ]; then
  ok "the main checkout (.git is a directory)"
else
  refuse "this is a linked worktree (its .git is a file) — deploy runs from the MAIN checkout, never a worktree (docs/DEPLOY.md)"
fi

if [ -f .env ]; then
  ok ".env present (compose auto-loads it)"
else
  refuse "no .env at $REPO_ROOT — a recreate without it drops the containers' secrets (Anthropic key, Slack webhook, heartbeat URLs)"
fi

if ! dirty="$(git status --porcelain 2>&1)"; then
  refuse "git status failed: ${dirty##*$'\n'}"
elif [ -n "$dirty" ]; then
  refuse "the tree is dirty — a build copies what is on disk, so deploy only a clean checkout:"
  { printf '%s\n' "$dirty" | head -n 10 | sed 's/^/        /'; } >&2 || true # || true: a long list SIGPIPEs printf
else
  ok "tree clean"
fi

branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || branch=""
if [ "$branch" = "main" ]; then
  ok "on main"
else
  refuse "on ${branch:-a detached HEAD}, not main — deploy from main: git checkout main && git pull --ff-only origin main"
fi

head_sha="$(git rev-parse HEAD 2>/dev/null)" || head_sha=""
remote_sha=""
if [ "$DRY_RUN" -eq 1 ]; then
  how="per git ls-remote; a dry-run fetches nothing"
  ls_out="$(git ls-remote origin refs/heads/main 2>/dev/null)" || ls_out=""
  remote_sha="${ls_out%%[[:space:]]*}"
else
  how="after git fetch origin main"
  if git fetch --quiet origin main; then
    remote_sha="$(git rev-parse origin/main 2>/dev/null)" || remote_sha=""
  fi
fi
if [ -z "$head_sha" ] || [ -z "$remote_sha" ]; then
  refuse "could not read HEAD and origin/main ($how) — cannot prove HEAD is what is merged"
elif [ "$head_sha" != "$remote_sha" ]; then
  refuse "HEAD ${head_sha:0:12} != origin/main ${remote_sha:0:12} ($how) — deploy only what is merged: if behind, git pull --ff-only origin main; if ahead, those commits are not merged"
else
  ok "HEAD == origin/main = ${head_sha:0:12} ($how)"
fi

if now_ny="$(ny_clock)"; then
  hhmm=$((10#${now_ny:11:2}${now_ny:14:2}))
  if [ "$hhmm" -ge "$WINDOW_FROM" ] && [ "$hhmm" -le "$WINDOW_TO" ]; then
    refuse "it is $now_ny — inside 22:20-22:50 America/New_York, around the 22:30 nightly pass; deploy after 22:50"
  else
    ok "outside 22:20-22:50 America/New_York (now $now_ny)"
  fi
else
  refuse "cannot read the America/New_York clock (neither TZ=America/New_York nor the EST5EDT rule gave EST/EDT) — the 22:20-22:50 window cannot be checked"
fi

PRE_STATUS=""
PRE_ALARMS=0
CRON_SHA_BEFORE=""
if [ "$DRY_RUN" -eq 1 ]; then
  say "stack checks — a dry-run touches no stack; the real run makes these READS before it changes anything:"
  echo "    would read: curl -fsS --max-time 60 $ADMIN_URL | docker exec -i $BACKEND_C python -c <parse> — refuse if a pass is running (cron.status running, or unfinished_run running inside its timeout)"
  echo "    would read: docker exec $BACKEND_C python -c <pg_locks read> — refuse if the call-of-record lock is held (pipeline/run_lock.py)"
elif [ "$REFUSED" -gt 0 ]; then
  say "stack checks skipped — a local check already refused"
else
  say "stack checks (read-only)"
  if read_status; then
    PRE_STATUS="$S_STATUS"
    PRE_ALARMS=${#S_ALARMS[@]}
    if [ "$S_RUNNING" = 1 ]; then
      refuse "a pass is running (cron.status=$S_STATUS) — never rebuild under a pass; wait for it to finish (/admin/status)"
    else
      ok "no pass running (cron.status=$S_STATUS · ${PRE_ALARMS} alarm(s) before the deploy)"
      print_alarms
    fi
  else
    refuse "cannot read cron.status from $ADMIN_URL ($S_ERR) — a running pass cannot be ruled out. If the backend is down this is a recovery, not a deploy: $RECOVERY"
  fi
  if lock="$(docker exec "$BACKEND_C" python -c "$LOCK_PY" 2>&1)"; then
    lock="${lock//$'\r'/}"
    lock="${lock##*$'\n'}"
    case "$lock" in
      free)   ok "the call-of-record lock is free" ;;
      held:*) refuse "the call-of-record lock is ${lock} — a daily pass or a backfill is writing calls; wait for it to finish" ;;
      *)      refuse "the call-of-record lock read gave '${lock}' — a running pass cannot be ruled out" ;;
    esac
  else
    refuse "cannot read the call-of-record lock (${lock##*$'\n'}) — a running pass or backfill cannot be ruled out"
  fi
fi

if [ "$REFUSED" -gt 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "FAIL: refused — $REFUSED check(s) failed (the REFUSE: lines above); nothing was changed" >&2
  exit 3
fi

# ============================ ACTIONS (a dry-run prints them, changes nothing) =========================
SHA="$head_sha"

say "backup"
if [ -n "$LABEL" ]; then
  run docker compose "${PROD[@]}" exec -T backend python -m pipeline.backup --label "$LABEL" \
    || step_fail "the pre-deploy backup failed — nothing was rebuilt"
else
  echo "    skipped: --env-only without --label (an env recreate keeps every image and writes no data)"
fi

if [ "$ENV_ONLY" -eq 1 ]; then
  say "recreate cron with its new env (no build: it keeps its image)"
  if [ "$DRY_RUN" -eq 0 ]; then
    CRON_SHA_BEFORE="$(image_sha "$CRON_C")"
  fi
  run docker compose "${PROD[@]}" up -d --no-deps --no-build cron \
    || step_fail "the cron recreate failed"
else
  say "build ${SERVICES[*]} at ${SHA:0:12} (GIT_SHA from git rev-parse HEAD)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    would run: GIT_SHA=$SHA docker compose ${PROD[*]} up -d --build --no-deps ${SERVICES[*]}"
  else
    echo "+ GIT_SHA=$SHA docker compose ${PROD[*]} up -d --build --no-deps ${SERVICES[*]}"
    GIT_SHA="$SHA" docker compose "${PROD[@]}" up -d --build --no-deps "${SERVICES[@]}" \
      || step_fail "the rebuild failed (docker compose up --build)"
  fi
fi

say "frontend restart rule"
if has backend && ! has frontend; then
  echo "    backend recreated WITHOUT frontend: nginx cached the old backend IP, so every /api call 502s until it restarts"
  run docker restart "$FRONTEND_C" || step_fail "the frontend restart failed"
elif has backend; then
  echo "    not needed: the frontend was recreated with the backend (nginx re-resolved it)"
else
  echo "    not needed: the backend was not recreated"
fi

# ==================================== VERIFY (the real artifact) =====================================
recreated_cron=0
if has cron; then recreated_cron=1; fi

if [ "$DRY_RUN" -eq 1 ]; then
  say "verify (each check would poll up to ${WAIT_S}s where it waits on startup)"
  echo "    would verify: docker inspect --format '{{.State.Health.Status}}' $BACKEND_C -> healthy"
  echo "    would verify: curl -s --max-time 10 -w '\\n%{http_code}' $PROXY_URL -> 200 (the /api proxy, not just :8000)"
  for svc in backend cron; do
    if [ "$ENV_ONLY" -eq 1 ] && [ "$svc" = "cron" ]; then
      echo "    would verify: docker exec $CRON_C printenv ALPHADECK_IMAGE_SHA -> unchanged from before the recreate"
    elif has "$svc"; then
      echo "    would verify: docker exec alphadeck-$svc-1 printenv ALPHADECK_IMAGE_SHA -> $SHA"
    else
      echo "    would show:   docker exec alphadeck-$svc-1 printenv ALPHADECK_IMAGE_SHA (not rebuilt; informational)"
    fi
  done
  if [ "$recreated_cron" -eq 1 ]; then
    echo "    would verify: docker inspect --format '{{.State.Status}}' $CRON_C -> running, and docker logs --tail 60 $CRON_C shows its boot lines"
  else
    echo "    would verify: docker inspect --format '{{.State.Status}}' $CRON_C -> running (the log tail is shown)"
  fi
  echo "    would verify: curl -fsS --max-time 60 $ADMIN_URL -> cron.status + cron.alarms: no new alarm, no quiet -> loud status"
  if [ "$REFUSED" -gt 0 ]; then
    echo "FAIL: dry-run — the real run would REFUSE: $REFUSED check(s) failed (the REFUSE: lines above); nothing was changed" >&2
    exit 3
  fi
  echo "OK: dry-run — every local check passed; nothing was changed (the real run reads the stack before changing anything)"
  exit 0
fi

say "verify"

if poll backend_healthy; then
  ok "$BACKEND_C is healthy"
else
  vfail "$BACKEND_C health is '${LAST}', not healthy, after ${WAIT_S}s"
fi

if poll proxy_200; then
  ok "$PROXY_URL -> 200 (the /api proxy)"
else
  vfail "$PROXY_URL -> ${LAST} after ${WAIT_S}s (a 502 is the stale-backend-IP trap: docker restart $FRONTEND_C)"
fi

backend_sha=""
cron_sha=""
for svc in backend cron; do
  c="alphadeck-$svc-1"
  got="$(image_sha "$c")"
  if [ "$svc" = "backend" ]; then backend_sha="$got"; else cron_sha="$got"; fi
  if [ "$ENV_ONLY" -eq 1 ] && [ "$svc" = "cron" ]; then
    if [ -z "$CRON_SHA_BEFORE" ]; then
      echo "    note: $c ALPHADECK_IMAGE_SHA=${got:-<unset>} — no sha was readable before the recreate, so 'the image was kept' cannot be checked"
    elif [ "$got" = "$CRON_SHA_BEFORE" ]; then
      ok "$c ALPHADECK_IMAGE_SHA=$got (unchanged: the env-only recreate kept the image)"
    else
      vfail "$c ALPHADECK_IMAGE_SHA=${got:-<unset>}, was $CRON_SHA_BEFORE before the env-only recreate"
    fi
  elif has "$svc"; then
    if [ "$got" = "$SHA" ]; then
      ok "$c ALPHADECK_IMAGE_SHA=$got (the sha just built)"
    else
      vfail "$c ALPHADECK_IMAGE_SHA=${got:-<unset>}, expected $SHA (the sha just built)"
    fi
  else
    echo "    info: $c ALPHADECK_IMAGE_SHA=${got:-<unset>} (not rebuilt by this deploy)"
  fi
done
if [ -n "$backend_sha" ] && [ -n "$cron_sha" ] && [ "$backend_sha" != "$cron_sha" ]; then
  echo "    note: backend and cron run different code (${backend_sha:0:12} vs ${cron_sha:0:12}) — right only if the change is off the nightly pipeline.daily path (docs/DEPLOY.md, the cron caveat)"
fi

if [ "$recreated_cron" -eq 1 ]; then
  if poll cron_booted; then
    ok "$CRON_C is running and printed its boot lines"
  elif [ "$LAST" != "running" ]; then
    vfail "$CRON_C is '${LAST}', not running, after ${WAIT_S}s"
  else
    vfail "$CRON_C never printed its boot line ('daily-cron: each pass bounded at') within ${WAIT_S}s"
  fi
else
  if cron_up; then
    ok "$CRON_C is running (not recreated by this deploy)"
  else
    vfail "$CRON_C is '${LAST}', not running (not recreated by this deploy: it was down before or went down since)"
  fi
  CRON_LOGS="$(docker logs --tail 60 "$CRON_C" 2>&1)" || true
fi
echo "    cron log tail:"
{ printf '%s\n' "$CRON_LOGS" | tail -n 8 | sed 's/^/      /'; } || true

if poll read_status; then
  echo "    cron.status=$S_STATUS · ${#S_ALARMS[@]} alarm(s) (before: ${PRE_STATUS} · ${PRE_ALARMS})"
  print_alarms
  if [ "${#S_ALARMS[@]}" -gt "$PRE_ALARMS" ]; then
    vfail "cron.alarms grew from $PRE_ALARMS to ${#S_ALARMS[@]} during the deploy (the lines above)"
  elif is_quiet "$PRE_STATUS" && ! is_quiet "$S_STATUS"; then
    vfail "cron.status went $PRE_STATUS -> $S_STATUS during the deploy"
  elif ! is_quiet "$S_STATUS"; then
    ok "/admin/status reads; cron.status=$S_STATUS was already $PRE_STATUS before the deploy (not caused by it)"
  else
    ok "/admin/status reads; cron.status=$S_STATUS"
  fi
else
  vfail "cannot read cron.status from $ADMIN_URL after the deploy ($S_ERR)"
fi

if [ "$VFAILED" -gt 0 ]; then
  echo "FAIL: deployed ${SHA:0:12} ($what) but $VFAILED verification check(s) failed (the FAIL: lines above) — $RECOVERY" >&2
  exit 5
fi
echo "OK: deployed ${SHA:0:12} to prod: $what — every verification passed"
