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
#   as `--asof`; it is never re-read after the wait. A single long `sleep` used to overshoot by however long
#   the host was suspended — MEASURED late fires at 23:37 / 23:43 / 23:49 ET, one at 00:24 the next day, and
#   one at 09:09 the next morning (`next run Tue Sep 8 22:30` followed by `Wed Sep 9 09:09:42 — running`).
#   The old loop let the CLI default `asof` to the day it WOKE, so a fire past midnight recorded the NEXT
#   day's as-of and the intended night got no call-of-record at all — 6 of 13 weekdays since 2026-08-24
#   had zero `calls` rows, and the Admin edge check read "current" over every hole (a wrong-day run is a
#   healthy run). The weekday gate is on the TARGET too (a Friday target that wakes on Saturday still runs).
#   The fixed target still matters with the sliced wait below: a suspend that SPANS RUN_AT still lands late.
# - WAITS IN SHORT SLICES, RE-CHECKING THE WALL CLOCK (`wait_until`, below; 2026-09-10). `sleep` counts the
#   MONOTONIC clock, and on Docker Desktop / WSL2 the VM's monotonic clock does not advance while the host is
#   suspended — so one long `sleep` overshoots by exactly the suspended interval even when the host is wide
#   awake at RUN_AT. MEASURED on prod 2026-09-10: booted 13:47 ET, `sleep 31376` for 22:30; at 22:33 ET the
#   wall clock had advanced 31,569 s since boot, the container's monotonic clock 28,688 s, and the sleep had
#   2,707 s left (~48 min of daytime suspend) — the pass fired ~23:18. Now the wait sleeps at most SLICE_S
#   (60 s) at a time and re-reads `date +%s` between slices: a host awake at RUN_AT fires AT RUN_AT, and a
#   host suspended across RUN_AT fires within one slice of resuming (the LATE WAKE line then measures the
#   suspend itself, and the catch-up below covers any nights it spanned). Quiet — no per-slice log line
#   (~1,440 wakeups a day would drown the log).
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
# - CATCHES UP THE LAST EXPECTED NIGHT ON BOOT (R6, widened 2026-09-10). A boot fires ONE `--catch-up` for
#   `last_expected_asof(now)` — the most recent as-of whose scheduled run should ALREADY have fired — so a
#   host that was OFF at RUN_AT catches last night up whenever it comes back, not only when it boots later the
#   SAME evening. MEASURED: prod was off at Wed 2026-09-09 22:30, rebooted 03:34, Docker (this sidecar) back
#   at 13:47 on Thu Sep 10 — the old today-only block ("booted past TODAY's RUN_AT?") did nothing at 13:47 and
#   re-anchored to Thu 22:30; Sep 9 was never attempted. Still exactly ONE night by construction (never older
#   holes — a deploy must never silently backfill history); the CLI's guard makes it a no-op when the night
#   genuinely ran, and a pre-open manual pass for that as-of does NOT count as the night (see the CLI).
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
# the wait_until slice (seconds) — how late a fire can be once the host is awake; see the header. A
# non-numeric or non-positive value would spin `sleep 0` in a hot loop, so it falls back to the default.
SLICE_S="${SLICE_S:-60}"
[ "${SLICE_S}" -ge 1 ] 2>/dev/null || SLICE_S=60

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

# --- the wait (the one helper that reads the ambient clock) ----------------------------------------------

# wait_until EPOCH -> returns once the WALL clock (`date +%s`) is >= EPOCH. Sleeps in slices of at most
# SLICE_S seconds and re-reads the wall clock after each: a suspended host does not advance the monotonic
# clock `sleep` counts on, so one long sleep overshoots by the whole suspend (measured 48 min on
# 2026-09-10); sliced, the loop returns within one slice of EPOCH once the host is awake. A remaining time
# <= 0 (already past, or a clock jump) breaks out — it can never issue `sleep -5`. Quiet: no per-slice log.
wait_until() {
  while :; do
    _rem=$(( $1 - $(date +%s) ))
    [ "${_rem}" -le 0 ] && break
    [ "${_rem}" -gt "${SLICE_S}" ] && _rem="${SLICE_S}"
    sleep "${_rem}"
  done
}

# --- the trigger ------------------------------------------------------------------------------------------

echo "daily-cron: scheduled for ${RUN_AT} (${TZ:-UTC}), Mon-Fri — the daily CLI is idempotent + force-refreshes"

# R6 — CATCH-UP ON BOOT, for the LAST EXPECTED night. A rebuild/restart AFTER today's RUN_AT re-anchors the
# loop to TOMORROW and would silently skip tonight (Flag 6 — every `docker compose up` after the close dropped
# a night, invisibly). `last_expected_asof(now)` is the most recent as-of whose scheduled run should ALREADY
# have fired: today once RUN_AT has passed on a weekday, else the most recent prior weekday (always a weekday
# — no separate gate). The old block was TODAY-ONLY ("booted past today's RUN_AT?"), which covered a rebuild
# after the close but NOT a host that was OFF at RUN_AT and came back the next day: on 2026-09-09 prod was off
# at 22:30, rebooted 03:34, and Docker (this sidecar) came back at 13:47 on Sep 10 — before Sep 10's RUN_AT, so
# nothing fired, the loop re-anchored to Sep 10 22:30, and Sep 9 was never attempted. UNCONDITIONAL on boot,
# on purpose: `--catch-up` is the guard — a NO-OP unless a LIVE pass for that as-of that STARTED at/after its
# RUN_AT is genuinely missing (the CLI reads the run log — R3's memory; a pre-open "Run daily now" for the same
# as-of does NOT count — it lacks the night's close — so it can no longer mask a missed post-close pass).
# Idempotent + fail-open: it never blocks the loop. Exactly ONE night by construction — never older holes (a
# deploy must never silently backfill history; the in-loop catch-up above is bounded to the sleep that just
# ended for the same reason).
_boot_target=$(last_expected_asof "$(date +%s)")
echo "daily-cron: booted $(date) — catch-up for the last expected night ${_boot_target} (no-op if its post-${RUN_AT} live pass already ran)"
python -m pipeline.daily --catch-up --asof "${_boot_target}" || echo "daily-cron: boot catch-up ${_boot_target} FAILED (continuing to the schedule)"

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
  wait_until "${next}"
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
