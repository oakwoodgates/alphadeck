#!/bin/sh
# The daily call-of-record cron — the DUMB TRIGGER half of M2d. The idempotent `python -m pipeline.daily`
# CLI is the unit of work; this just fires it on a schedule. It runs only in the docker-compose `cron`
# profile sidecar (DISABLED by default), so local dev / `docker compose up` / tests never fire it.
#
# - Schedules at RUN_AT in the container's TZ (set to US market close, e.g. America/New_York — never the
#   default UTC), Mon-Fri only (markets closed on weekends -> no new EOD bars; a run would be an idempotent
#   no-op + a needless API hit).
# - Inherits the container env directly (DATABASE_URL / ALPHADECK_USER_AGENT / TZ), so unlike a cron daemon
#   there is no env snapshot to mangle the space-bearing User-Agent.
# - A failed run NEVER kills the loop (the cron survives a bad day); re-runs are safe (the CLI is idempotent
#   — incremental ingest + record_if_changed).
set -u

RUN_AT="${RUN_AT:-22:30}"
echo "daily-cron: scheduled for ${RUN_AT} (${TZ:-UTC}), Mon-Fri — the daily CLI is idempotent + force-refreshes"

# R6 — CATCH-UP ON START. A rebuild/restart AFTER today's RUN_AT re-anchors the loop to TOMORROW and silently
# skips tonight (Flag 6 — every `docker compose up` after the close dropped a night, invisibly). So on boot, if
# we are already PAST today's RUN_AT on a weekday, attempt a catch-up. `--catch-up` is a NO-OP unless a LIVE
# pass for today is genuinely missing (the CLI checks the run log — R3's memory), so a boot BEFORE RUN_AT, or a
# night that already ran, does nothing. Idempotent + fail-open: it never blocks the loop.
_boot_now=$(date +%s)
if [ "$(date -d "today ${RUN_AT}" +%s)" -le "${_boot_now}" ] && [ "$(date +%u)" -le 5 ]; then
  echo "daily-cron: booted past today's ${RUN_AT} — attempting catch-up (no-op if today already ran live)"
  python -m pipeline.daily --catch-up || echo "daily-cron: catch-up FAILED (continuing to the schedule)"
fi

while :; do
  now=$(date +%s)
  next=$(date -d "today ${RUN_AT}" +%s)
  if [ "${next}" -le "${now}" ]; then
    next=$(date -d "tomorrow ${RUN_AT}" +%s) # today's time already passed -> wait for tomorrow's
  fi
  echo "daily-cron: next run $(date -d "@${next}")"
  sleep "$((next - now))"
  if [ "$(date +%u)" -le 5 ]; then
    echo "daily-cron: $(date) — running pipeline.daily"
    python -m pipeline.daily || echo "daily-cron: run FAILED (continuing to the next day)"
    # Slice 4 — a nightly DB snapshot right after the daily pass, fail-open (a failed backup never kills
    # the loop). Deliberately in the SCHEDULING layer, NOT folded into run_daily_pass (so the manual "Run
    # daily now" button does not also dump). Retention (keep-last-N, labeled exempt) is pipeline.backup's.
    python -m pipeline.backup || echo "daily-cron: nightly backup FAILED (continuing)"
  else
    echo "daily-cron: $(date) — weekend, skipping"
  fi
done
