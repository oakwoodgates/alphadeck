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
  else
    echo "daily-cron: $(date) — weekend, skipping"
  fi
done
