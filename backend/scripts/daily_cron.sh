#!/bin/sh
# The daily call-of-record cron — the DUMB TRIGGER half of M2d. The idempotent `python -m pipeline.daily`
# CLI is the unit of work; this just fires it on a schedule. It runs in the docker-compose `cron` SERVICE,
# which is ON by default with the full-stack `docker compose up` (skip it with `--scale cron=0`; there are
# no `profiles:`). The DB-only dev loop (infra/docker-compose.yml), the dev override
# (docker-compose.dev.yml, which omits cron), and the test suite never start it.
#
# - Schedules at RUN_AT in the container's TZ (set to US market close, e.g. America/New_York — never the
#   default UTC), Mon-Fri only (markets closed on weekends -> no new EOD bars; a run would be an idempotent
#   no-op + a needless API hit).
# - FIRES FOR THE INTENDED NIGHT. The target as-of is fixed at SCHEDULE time (`target`, below) and passed
#   as `--asof`; it is never re-read after the sleep. On a laptop `sleep` overshoots by however long the
#   host was suspended — MEASURED late fires at 23:37 / 23:43 / 23:49 ET, one at 00:24 the next day, and
#   one at 09:09 the next morning (`next run Tue Sep 8 22:30` followed by `Wed Sep 9 09:09:42 — running`).
#   The old loop let the CLI default `asof` to the day it WOKE, so a fire past midnight recorded the NEXT
#   day's as-of and the intended night got no call-of-record at all — 6 of 13 weekdays since 2026-08-24
#   had zero `calls` rows, and the Admin edge check read "current" over every hole (a wrong-day run is a
#   healthy run). The weekday gate is on the TARGET too (a Friday target that wakes on Saturday still runs).
# - CATCHES UP the nights a long sleep ALSO skipped: after the scheduled run, every weekday strictly after
#   the target up to the last EXPECTED as-of at the instant the scheduled run FINISHES gets a `--catch-up`
#   pass. The window closes AFTER the run, not at the wake: a live run takes 7-15 min, so a wake shortly
#   before the next RUN_AT whose run crosses it (case F: target Tue, wake Wed 22:20, run ends 22:35) would
#   otherwise lose Wed outright — a wake-anchored window did not include it yet, and the loop then
#   re-anchored `next` to Thu (today's RUN_AT already past). Each pass is a no-op when a live pass for
#   that as-of is already in the run log. Bounded by construction — it walks FORWARD from
#   the target the loop just fired for, never backwards, so it can only ever cover the sleep that just
#   ended (a deploy never silently backfills old holes). Catch-up days run inside the EDGAR cache's 12h TTL
#   and legitimately make ~0 fetches; the CLI's `--catch-up` skips the freeze page for exactly that reason.
# - The as-of dates come from this shell's `date` in the container TZ, which compose pins to the market TZ
#   (the same "today" `market_today()` derives) — keep those two in step.
# - Inherits the container env directly (DATABASE_URL / ALPHADECK_USER_AGENT / TZ), so unlike a cron daemon
#   there is no env snapshot to mangle the space-bearing User-Agent.
# - A failed run NEVER kills the loop (the cron survives a bad day); re-runs are safe (the CLI is idempotent
#   — incremental ingest + record_if_changed).
# - POSIX sh (the image's /bin/sh is dash) + GNU `date -d`. The schedule math below is pure over its
#   arguments (no ambient clock) and mirrors pipeline/schedule.py — keep the two in step; to exercise it by
#   hand, paste the functions into a shell with RUN_AT set and `python` stubbed (`python() { echo "$@"; }`).
set -u

RUN_AT="${RUN_AT:-22:30}"

# --- schedule math (pure over their arguments; GNU date) ------------------------------------------------

# is_weekday YYYY-MM-DD -> true on Mon-Fri. The cron's calendar: holidays included, weekends out — the same
# gate as pipeline/schedule.is_scheduled_day.
is_weekday() {
  [ "$(date -d "$1" +%u)" -le 5 ]
}

# next_weekday YYYY-MM-DD -> the first Mon-Fri date strictly AFTER it.
next_weekday() {
  _nw=$(date -d "$1 + 1 day" +%F)
  while ! is_weekday "${_nw}"; do
    _nw=$(date -d "${_nw} + 1 day" +%F)
  done
  echo "${_nw}"
}

# last_expected_asof EPOCH -> the most recent as-of whose scheduled run should ALREADY have fired by that
# instant: that day itself once RUN_AT has passed on a weekday, else the most recent prior weekday
# (pipeline/schedule.last_expected_asof, in shell).
last_expected_asof() {
  _le_today=$(date -d "@$1" +%F)
  if is_weekday "${_le_today}" && [ "$(date -d "${_le_today} ${RUN_AT}" +%s)" -le "$1" ]; then
    echo "${_le_today}"
    return
  fi
  _le=$(date -d "${_le_today} - 1 day" +%F)
  while ! is_weekday "${_le}"; do
    _le=$(date -d "${_le} - 1 day" +%F)
  done
  echo "${_le}"
}

# catch_up_days TARGET LAST_EXPECTED -> the weekdays d with TARGET < d <= LAST_EXPECTED, one per line,
# ascending (empty when nothing was skipped). Pure — the list the runner below walks.
catch_up_days() {
  _cd=$(next_weekday "$1")
  _cd_end=$(date -d "$2" +%Y%m%d)
  while [ "$(date -d "${_cd}" +%Y%m%d)" -le "${_cd_end}" ]; do
    echo "${_cd}"
    _cd=$(next_weekday "${_cd}")
  done
}

# catch_up_between TARGET LAST_EXPECTED -> a `--catch-up` pass for each day catch_up_days lists (the nights
# the sleep ALSO skipped). Each day is its own log line + its own fail-open invocation; `--catch-up` is a
# no-op when a live pass for that as-of already ran (the R3 run log is the memory).
catch_up_between() {
  for _cb in $(catch_up_days "$1" "$2"); do
    echo "daily-cron: late wake — catching up ${_cb}"
    python -m pipeline.daily --catch-up --asof "${_cb}" || echo "daily-cron: catch-up ${_cb} FAILED (continuing)"
  done
}

# --- the trigger ------------------------------------------------------------------------------------------

echo "daily-cron: scheduled for ${RUN_AT} (${TZ:-UTC}), Mon-Fri — the daily CLI is idempotent + force-refreshes"

# R6 — CATCH-UP ON START. A rebuild/restart AFTER today's RUN_AT re-anchors the loop to TOMORROW and silently
# skips tonight (Flag 6 — every `docker compose up` after the close dropped a night, invisibly). So on boot, if
# we are already PAST today's RUN_AT on a weekday, attempt a catch-up. `--catch-up` is a NO-OP unless a LIVE
# pass for today is genuinely missing (the CLI checks the run log — R3's memory), so a boot BEFORE RUN_AT, or a
# night that already ran, does nothing. Idempotent + fail-open: it never blocks the loop. TODAY-ONLY, on
# purpose: a deploy must never silently backfill old holes (the in-loop catch-up above is bounded to the
# sleep that just ended for the same reason).
_boot_now=$(date +%s)
_boot_today=$(date +%F)
if [ "$(date -d "today ${RUN_AT}" +%s)" -le "${_boot_now}" ] && is_weekday "${_boot_today}"; then
  echo "daily-cron: booted past today's ${RUN_AT} — attempting catch-up for ${_boot_today} (no-op if it already ran live)"
  python -m pipeline.daily --catch-up --asof "${_boot_today}" || echo "daily-cron: catch-up FAILED (continuing to the schedule)"
fi

while :; do
  now=$(date +%s)
  next=$(date -d "today ${RUN_AT}" +%s)
  if [ "${next}" -le "${now}" ]; then
    next=$(date -d "tomorrow ${RUN_AT}" +%s) # today's time already passed -> wait for tomorrow's
  fi
  # THE TARGET AS-OF — the night this run is FOR, fixed NOW at schedule time. Never re-read after the sleep
  # (the sleep can overshoot by a whole suspend; see the header).
  target=$(date -d "@${next}" +%F)
  echo "daily-cron: next run $(date -d "@${next}") — asof ${target}"
  sleep "$((next - now))"
  woke=$(date +%s)
  late_min=$(( (woke - next) / 60 ))
  if [ "${late_min}" -ge 5 ]; then
    # the sleep drift, visible without forensics (a suspended laptop; the measured 23:37 / 00:24 / 09:09 wakes)
    echo "daily-cron: LATE WAKE — $(date) is ${late_min} min past the scheduled $(date -d "@${next}")"
  fi
  if is_weekday "${target}"; then
    echo "daily-cron: $(date) — running pipeline.daily --asof ${target}"
    # the SCHEDULED run always fires (no --catch-up): it re-versions even if an operator hand-ran that day
    python -m pipeline.daily --asof "${target}" || echo "daily-cron: run FAILED (continuing to the next day)"
  else
    echo "daily-cron: $(date) — weekend (asof ${target}), no scheduled run"
  fi
  # A long sleep can skip MORE than the target night (a weekend target that wakes on Tuesday skipped Monday):
  # catch up every weekday strictly after the target up to the last expected as-of at the instant the run
  # above FINISHED — the clock NOW, not `woke`. A live run takes 7-15 min; anchored at the wake, a run that
  # crossed the next RUN_AT (case F in the header) lost that night — the window did not include it yet, and
  # the loop then re-anchored `next` past it. `woke` stays for the LATE WAKE line only.
  after=$(date +%s)
  catch_up_between "${target}" "$(last_expected_asof "${after}")"
  if is_weekday "${target}"; then
    # Slice 4 — a nightly DB snapshot right after the daily pass, ONCE PER WAKE (not per catch-up day),
    # fail-open (a failed backup never kills the loop). Deliberately in the SCHEDULING layer, NOT folded
    # into run_daily_pass (so the manual "Run daily now" button does not also dump). Retention
    # (keep-last-N, labeled exempt) is pipeline.backup's.
    python -m pipeline.backup || echo "daily-cron: nightly backup FAILED (continuing)"
  fi
done
