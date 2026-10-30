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
# - RETRIES A FAILED SCHEDULED RUN ONCE (G2a). A non-zero exit used to end the night: the loop logged it and
#   waited for tomorrow, so a transient fault (a DB restart mid-run, a vendor 5xx, a network blip) cost that
#   night's call-of-record outright — and a daytime row on the same day could keep the Admin page reading
#   "healthy" over the hole. Now the loop waits RETRY_DELAY_S (default 1200 s, sliced through `wait_until`
#   like the schedule wait) and fires ONE `python -m pipeline.daily --run-kind cron --catch-up --asof "$target"`.
#
# - EVERY invocation below passes `--run-kind cron` (F1 / migration 0044), which is stamped on each
#   call-of-record row this sidecar's runs append. THIS SCRIPT IS THE ONLY THING THAT MAY PASS IT: the CLI
#   defaults to `manual`, and the sidecar and an operator type the SAME command, so an explicit flag is the
#   only thing that can tell the nightly record from a hand run. Do not "simplify" it into an env var — a
#   `docker exec <this container> python -m pipeline.daily` would then stamp `cron` and lie.
#   WHAT A NON-ZERO EXIT MEANS (F4, 2026-09-13): the night has NO call-of-record at all. It used to mean
#   "some thesis errored", so a night that recorded 11 of 12 theses still cost a 20-minute pause and a
#   retry the guard then no-opped. The retry now fires only on a genuine total miss, which is the only
#   shape a re-run can fix. `--catch-up` stays as the guard anyway (belt-and-suspenders for the narrow
#   case of a pass that wrote its artifact and then died). The retry sits BEFORE the in-loop catch-up
#   window closes and before the nightly backup, so both still run after it.
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
# - WAITS FOR THE DB BEFORE THE BOOT CATCH-UP, AND RETRIES IT ONCE (2026-10-01). Restart policies ignore
#   `depends_on` (it orders only the INITIAL `up`), so after a Docker daemon restart every container comes
#   back in the same second and this sidecar can boot before Postgres accepts a connection. MEASURED on prod
#   2026-10-01: Docker Desktop was down 09-29..10-01; when it came back at 23:46:19 EDT, postgres, backend,
#   cron and frontend all started within that second, the boot catch-up for 10-01 fired at 23:46:20, lost the
#   race (`Connection refused` on the run and on every refresh leg), logged FAILED, and the loop re-anchored
#   to 10-02 22:30. The backend crash-looped 7x on `db.migrate` and self-healed through its restart policy,
#   but this script never exits, so nothing tried the night again — only a hand "Run daily now" saved it.
#   Now the boot polls a cheap connect (`wait_for_db`, below: `db.session.database_url()`, the CLI's own
#   DATABASE_URL path) every DB_POLL_S (5 s) through `wait_until` for at most DB_WAIT_S (default 300 s),
#   then fires the catch-up EITHER WAY (fail-open: a DB that never comes up costs the wait, never the loop);
#   a boot catch-up that still exits non-zero gets the scheduled run's ONE retry (G2a), RETRY_DELAY_S later.
#   The scheduled run polls the same way first (quiet when the DB is up: one probe, no log line). And since
#   the boot block can now run for many minutes, every night that became expected WHILE it ran (a boot at
#   22:20 whose block ends past 22:30) gets a `--catch-up` pass before the loop anchors — the loop would
#   otherwise compute `next` = tomorrow and drop tonight. Forward from the boot target only, so still never
#   an older hole.
# - BOUNDS EVERY PASS (C2, 2026-10-16). Each `python -m pipeline.daily` below runs through `run_pass`, under
#   `timeout -k 60 "$RUN_TIMEOUT_S"` (default 5400 s = 90 min). A hung pass (a black-holed socket mid-run, a
#   stuck lock) used to hold the loop forever: no exit code, so no retry, no page, no artifact, and every
#   later night queued behind it. A timeout is just a non-zero exit (124, logged as TIMED OUT), so it flows
#   into the SAME retry, page and heartbeat logic as any other failure. The bound is ~1.4x the worst
#   MEASURED cold pass (~65 min; a normal night is 7-15 min): a night that adds many cold names at once can
#   still exceed it, and then the retry (the ingest is incremental, so it resumes ahead) lands the night.
# - PAGES A NIGHT THAT FAILED FOR GOOD, WITH NO DB (H3, 2026-10-16). R4's HealthEvent pages from INSIDE a
#   finished pass, so a pass that died or never connected had no voice: MEASURED, the 10-12 run killed at
#   22:42 (a WSL auto-update took the VM down) and the 10-01 boot catch-up lost to the cold-start race both
#   paged nothing. Now the shell calls `python -m notify.page --asof D --reason ...` (`page`, below) when
#   the scheduled run's ONE retry fails, when the boot catch-up's retry fails, when a catch-up night fails
#   (it has no retry, so its one attempt is final), and when the backup fails. The CLI imports no DB, never
#   raises, always exits 0, and sends through R4's own notifier (Slack when SLACK_WEBHOOK_URL is set, else a
#   loud log line). The reason is this script's words plus a date and an exit code, never a thesis name.
#   It covers every failure the SHELL outlives (a crashed or OOM-killed pass, a DB gone mid-run, a hang the
#   timeout stops, the 10-01 boot); a box that dies whole, as on 10-12, takes this shell with it, and only
#   the external heartbeat (next bullet) can report that.
# - PINGS AN EXTERNAL HEARTBEAT WHEN THE LAST EXPECTED NIGHT IS RECORDED (H2, 2026-10-16). Nothing inside
#   the box can report the box's own absence (a host that stays off, a sidecar that never boots). MEASURED:
#   no heartbeat code existed, and every outage so far lasted until a human launched Docker. So the SHELL,
#   not the app, sends a bare `curl -fsS -m 10 --retry 3` GET to HEARTBEAT_URL (`beat_if_current`) after a
#   cron pass exits 0 for the night that is the LAST EXPECTED as-of at that instant: the scheduled run or
#   its retry, the boot catch-up or its retry, or a catch-up night. The ping means "the last expected night
#   is recorded", so a pass that lands an OLDER night (target Tue finishing after Wed's RUN_AT) holds the
#   ping until the catch-up of the newest night lands. A `--catch-up` that no-ops exits 0 because its night
#   already recorded, so it pings too. The external service (healthchecks.io, a Mon-Fri ET schedule with a
#   ~2 h grace) alerts when the ping does not come. Just before the scheduled run the shell also pings
#   HEARTBEAT_URL/start (healthchecks.io's convention), so a hang reads "started, never finished".
#   HEARTBEAT_BACKUP_URL gets a ping when `pipeline.backup` exits 0 (a fresh snapshot exists, dumped now
#   or still under the age gate). Never from the Admin "Run daily now" (that runs in the backend, not here)
#   and never on `--no-live` (this script never passes it). Both URLs are optional: unset is a silent
#   no-op. A failed ping is one log line and never blocks the loop; no URL is ever logged (a ping URL is
#   a credential).
# - BACKS UP ON BOOT BY AGE, AND AFTER EVERY WAKE THAT RAN A NIGHT (M3, 2026-10-16). The nightly dump rode only
#   a weekday-target wake, so a night caught up at BOOT got no snapshot (MEASURED: none for ~4 days around
#   10-12). Now the boot block ends with `pipeline.backup --if-older-than-h "$BACKUP_MAX_AGE_H"` (default
#   20; `snapshot ... gated`): it dumps only when the newest unlabeled snapshot is at least that old, else
#   it prints a no-op line, so the normal boot (last night's dump is hours old) adds nothing and a
#   crash-looping container dumps about once a day instead of on every restart. The in-loop dump stays
#   UNCONDITIONAL, deliberately: its job is to capture the run that just finished, and no earlier dump can
#   stand in for that. Gating it too would let any boot that dumped in the ~4 h before RUN_AT (the prior
#   night's dump is 20 h old by ~18:45) skip that night's post-run dump, leaving the night out of every
#   snapshot for another day (MEASURED under dash with a stubbed pass: a boot dump at 03:22 turned the 03:24
#   run's dump into a "0.0 h old" no-op). A wake whose target is a weekend now dumps too when its catch-up
#   covered a weekday (a suspend spanning the weekend), which got none before. Retention is
#   pipeline.backup's, unchanged.
# - LOGS LAND AS THEY HAPPEN (O3, 2026-10-16). The image sets PYTHONUNBUFFERED=1 and compose caps the
#   json-file log (50 MB x 5). MEASURED before: the passes' output arrived in 60-75-line bursts ~12 min late
#   and the tail was lost when the 10-12 run was killed; the log had no cap.
# - RUNS A SECOND SCHEDULE: THE STAGED COLD-INGEST JOB (X1, 2026-10-21). A cold name's first ingest costs 300-600 s
#   (MEASURED 2026-10-21: median 396 s, n=10; prod's X5 average was ~110 s) and a big onboarding inside the 22:30 pass would run the night past RUN_TIMEOUT_S, so the
#   history load is its own job (`python -m pipeline.onboarding`), fired by `onboard_loop` in the background of
#   this sidecar every ONBOARD_EVERY_S (default 1800 s) after the previous run ends. The job itself refuses inside
#   the nightly window and yields to any running pass (see `onboard_loop`); the nightly gives a name still
#   onboarding a bounded, windowed ingest and stamps its thesis `ingest_fresh=false` (docs/FEED_LOOP.md).
# - GROWS THE SCOREBOARD'S CURRENT EDITION AFTER THE BACKUP (editions S5, 2026-10-23). Right after the nightly
#   dump, `editions_nightly` fires `python -m editions.run nightly --apply` once per wake: tonight's page (plus a
#   catch-up page for any session the edition missed) per live thesis, the ink of every episode whose signal
#   window ended, and the live-edge self-check (a real mismatch pages from inside the job). The job gates itself
#   (only after a FINISHED nightly pass; exit 75 while the call-of-record lock or its own lock, id 4, is held),
#   is bounded by EDITIONS_TIMEOUT_S, and is fail-open here: one log line, never the end of the loop. The cron
#   image carries the `.[replay]` extra it needs (duckdb + pyarrow; docker-compose.yml's cron build args).
# - WARMS THE DASHBOARDS' CACHE AFTER THAT (2026-10-30). The dashboards' in-process cache is cleared by every new
#   cron run, so the operator's first visit paid each heavy page's cold compute (MEASURED on a prod copy: 7 to 96
#   s a page). Last in the nightly wake, `warm_dashboards` fires `python -m pipeline.warm_dashboards --night
#   <the last expected night>`: ONE GET per heavy page's default view against the running API (GETs only, no DB,
#   no write), for the as-of the NEXT visit sends (the day after the pass, plus the Monday after a weekend), so
#   the morning visit is a cache hit. Bounded by WARM_DASHBOARDS_TIMEOUT_S, fail-open (one log line, never the
#   loop, never the pass's status or exit code), off with ALPHADECK_WARM_DASHBOARDS=0 (an env-only cron deploy).
#   docs/FEED_LOOP.md §the dashboard cache warm-up.
# - RUNS A THIRD SCHEDULE: THE WEEKLY COMPANY-LIST REFRESH (2026-10-24). `master_refresh_loop` fires
#   `python -m pipeline.populate_master --live --apply --run-kind cron` every Saturday 09:00 (MASTER_REFRESH_DOW /
#   MASTER_REFRESH_AT, container TZ), in the background like onboard_loop: SEC's current company file onto the
#   master (a rename updates the company's row IN PLACE, so every fact stays attached) plus the fund list. The job
#   refuses inside the nightly window, holds its own lock (advisory id 5) and yields to a running pass; a refused
#   run retries hourly a few times; fail-open (one log line). docs/FEED_LOOP.md §the company list.
# - RUNS A FOURTH SCHEDULE: THE WEEKLY TERM-MENTIONS COUNT (2026-10-29). `term_mentions_loop` fires
#   `python -m pipeline.term_mentions --weekly --apply --run-kind cron` every Saturday 10:00 (TERM_MENTIONS_DOW /
#   TERM_MENTIONS_AT, container TZ — after the 09:00 company-list refresh, which is bounded at 900 s), in the
#   background like master_refresh_loop: one EDGAR full-text count per card SIGNAL term for the current month and
#   the one before (plus any month a term added since is missing), display data for the Narrative Adoption Curve
#   dashboard. A no-op until the operator has run the first backfill by hand. The job refuses inside the nightly
#   window, holds its own lock (advisory id 6), yields to a running pass and stops between terms when one starts;
#   a refused / stopped run retries hourly a few times; fail-open (one log line). docs/FEED_LOOP.md §term mentions.
# - The as-of dates come from this shell's `date` in the container TZ, which compose pins to the market TZ
#   (the same "today" `market_today()` derives) — keep those two in step.
# - Inherits the container env directly (DATABASE_URL / ALPHADECK_USER_AGENT / TZ), so unlike a cron daemon
#   there is no env snapshot to mangle the space-bearing User-Agent.
# - A failed run NEVER kills the loop (the cron survives a bad day); re-runs are safe (the CLI is idempotent
#   — incremental ingest + record_if_changed).
# - POSIX sh (the image's /bin/sh is dash) + GNU `date -d`. The schedule math below is pure over its
#   arguments (no ambient clock) and mirrors pipeline/schedule.py — keep the two in step; to exercise it by
#   hand, paste the functions into a shell with RUN_AT set and `python` stubbed (`python() { echo "$@"; }`).
#   To run JUST the boot block (the DB wait, the catch-up, its retry, the crossed-RUN_AT catch-up, the boot
#   backup) and exit before the loop, set CRON_EXIT_AFTER_BOOT=1 with a stub `python` first on PATH and small
#   DB_WAIT_S / DB_POLL_S / RETRY_DELAY_S / SLICE_S / RUN_TIMEOUT_S — a test seam only; compose never sets
#   it. A stub `curl` on PATH (and a HEARTBEAT_URL) exercises the heartbeat the same way.
set -u

RUN_AT="${RUN_AT:-22:30}"
# the wait_until slice (seconds) — how late a fire can be once the host is awake; see the header. A
# non-numeric or non-positive value would spin `sleep 0` in a hot loop, so it falls back to the default.
SLICE_S="${SLICE_S:-60}"
[ "${SLICE_S}" -ge 1 ] 2>/dev/null || SLICE_S=60
# G2a — how long to wait before the ONE retry of a scheduled run that exited non-zero (see the retry block
# in the loop). 20 minutes by default: long enough for a transient fault to pass (a DB restart, a vendor
# 5xx, a network blip), short enough to land the night's record well before the next RUN_AT. Same
# non-numeric / non-positive guard as SLICE_S.
RETRY_DELAY_S="${RETRY_DELAY_S:-1200}"
[ "${RETRY_DELAY_S}" -ge 1 ] 2>/dev/null || RETRY_DELAY_S=1200
# The DB wait (2026-10-01; see the header) — the most the boot catch-up and the scheduled run wait for
# Postgres to accept a connection before firing anyway. 5 minutes by default: a cold Postgres after an
# unclean daemon stop replays its WAL before it accepts a connection ("the database system is starting
# up"), normally seconds; five minutes covers a slow recovery without pushing a night meaningfully late.
# DB_POLL_S is the gap between probes — a script default like SLICE_S (not in compose), tunable only to
# exercise the wait by hand. Same non-numeric / non-positive guard as SLICE_S for both.
DB_WAIT_S="${DB_WAIT_S:-300}"
[ "${DB_WAIT_S}" -ge 1 ] 2>/dev/null || DB_WAIT_S=300
DB_POLL_S="${DB_POLL_S:-5}"
[ "${DB_POLL_S}" -ge 1 ] 2>/dev/null || DB_POLL_S=5
# C2 — the most one `pipeline.daily` pass may run before `timeout` stops it (seconds; see the header). 90
# minutes by default, ~1.4x the worst measured cold pass. Same non-numeric / non-positive guard as SLICE_S.
RUN_TIMEOUT_S="${RUN_TIMEOUT_S:-5400}"
[ "${RUN_TIMEOUT_S}" -ge 1 ] 2>/dev/null || RUN_TIMEOUT_S=5400
# M3 — the BOOT backup's age gate (hours): dump only when the newest unlabeled snapshot is at least this old.
# A script default like DB_POLL_S (not in compose), tunable only to exercise the gate by hand. Same guard.
BACKUP_MAX_AGE_H="${BACKUP_MAX_AGE_H:-20}"
[ "${BACKUP_MAX_AGE_H}" -ge 1 ] 2>/dev/null || BACKUP_MAX_AGE_H=20
# H2 — the external heartbeat's ping URLs. Both optional: unset or empty is a silent no-op (no ping, no log
# line), which is the state until the operator creates the heartbeat account.
HEARTBEAT_URL="${HEARTBEAT_URL:-}"
HEARTBEAT_BACKUP_URL="${HEARTBEAT_BACKUP_URL:-}"
# X1 — the STAGED COLD-INGEST job's schedule (the second schedule in this sidecar; see `onboard_loop`): how
# long to wait after one onboarding run ends before firing the next (seconds; 0 turns the loop OFF), and the
# hard `timeout` around one run. The job's own wall budget (Settings.onboard_batch_budget_s, 1500 s) is checked
# between LEGS, so a run can overshoot it by one leg — MEASURED up to ~10 min for a heavy filer's whole Form 4
# history (CAT, 596 s for the name) — hence 2700 s: this only stops a HUNG run. Same non-numeric guard as
# SLICE_S; a negative cadence reads as the default.
ONBOARD_EVERY_S="${ONBOARD_EVERY_S:-1800}"
[ "${ONBOARD_EVERY_S}" -ge 0 ] 2>/dev/null || ONBOARD_EVERY_S=1800
ONBOARD_TIMEOUT_S="${ONBOARD_TIMEOUT_S:-2700}"
[ "${ONBOARD_TIMEOUT_S}" -ge 1 ] 2>/dev/null || ONBOARD_TIMEOUT_S=2700
# Editions S5 — the Scoreboard editions NIGHTLY PAGE job (`editions_nightly`, below): the hard `timeout` around
# one run (seconds) and its kill switch (EDITIONS_NIGHTLY=0 = off, an env-only cron deploy). A normal night is a
# public-clock export (~1 min on prod) plus ~10 pages. S7: a night that also runs the AUTOMATIC CUT's trial (a
# rule change or a data repair since the edition's cut) re-runs the whole edition once or twice — MEASURED on dev
# 2026-10-23: ~4.5 min per re-run at 4 workers for 659 pages, ~9 min both — so 60 minutes, and it only stops a
# HUNG run (a long catch-up after an outage resumes the next night — every page it did not write is still
# missing then). Same guard as SLICE_S.
EDITIONS_TIMEOUT_S="${EDITIONS_TIMEOUT_S:-3600}"
[ "${EDITIONS_TIMEOUT_S}" -ge 1 ] 2>/dev/null || EDITIONS_TIMEOUT_S=3600
EDITIONS_NIGHTLY="${EDITIONS_NIGHTLY:-1}"
# The dashboards' cache warm-up (`warm_dashboards`, below): the hard `timeout` around one run (seconds; its own
# budget is 1200 s between requests plus up to 300 s for the one in flight, so 1800 only stops a HUNG run). Its
# kill switch is ALPHADECK_WARM_DASHBOARDS=0 (read at the call, below, and again by the CLI). Same guard as SLICE_S.
WARM_DASHBOARDS_TIMEOUT_S="${WARM_DASHBOARDS_TIMEOUT_S:-1800}"
[ "${WARM_DASHBOARDS_TIMEOUT_S}" -ge 1 ] 2>/dev/null || WARM_DASHBOARDS_TIMEOUT_S=1800
# The company-list refresh — the THIRD schedule (`master_refresh_loop`, below): weekly, on day MASTER_REFRESH_DOW
# (`date +%u`: 1 = Monday .. 7 = Sunday; default 6 = Saturday) at MASTER_REFRESH_AT in the container TZ (the
# market's; default 09:00 — far outside the nightly window), each run bounded by MASTER_REFRESH_TIMEOUT_S. A run
# REFUSED (exit 75: a pass or another refresh holds its lock) is retried every MASTER_REFRESH_RETRY_S, at most
# MASTER_REFRESH_RETRIES times, then waits for the next week. MASTER_REFRESH=0 turns it OFF (an env-only cron
# deploy). Same non-numeric guards as SLICE_S; an out-of-range day reads as Saturday.
MASTER_REFRESH="${MASTER_REFRESH:-1}"
MASTER_REFRESH_DOW="${MASTER_REFRESH_DOW:-6}"
case "${MASTER_REFRESH_DOW}" in [1-7]) ;; *) MASTER_REFRESH_DOW=6 ;; esac
MASTER_REFRESH_AT="${MASTER_REFRESH_AT:-09:00}"
MASTER_REFRESH_TIMEOUT_S="${MASTER_REFRESH_TIMEOUT_S:-900}"
[ "${MASTER_REFRESH_TIMEOUT_S}" -ge 1 ] 2>/dev/null || MASTER_REFRESH_TIMEOUT_S=900
MASTER_REFRESH_RETRY_S="${MASTER_REFRESH_RETRY_S:-3600}"
[ "${MASTER_REFRESH_RETRY_S}" -ge 1 ] 2>/dev/null || MASTER_REFRESH_RETRY_S=3600
MASTER_REFRESH_RETRIES="${MASTER_REFRESH_RETRIES:-6}"
[ "${MASTER_REFRESH_RETRIES}" -ge 0 ] 2>/dev/null || MASTER_REFRESH_RETRIES=6
# The term-mentions count — the FOURTH schedule (`term_mentions_loop`, below): weekly, on day TERM_MENTIONS_DOW
# (`date +%u`; default 6 = Saturday) at TERM_MENTIONS_AT in the container TZ (default 10:00 — after the 09:00
# company-list refresh, whose run is bounded at 900 s), each run bounded by TERM_MENTIONS_TIMEOUT_S (default an
# hour; a weekly run is ~1,500 requests — MEASURED 1,492 by the dry run on 2026-10-29 — minutes at the SEC budget — this only stops a HUNG run). A run REFUSED or
# STOPPED (exit 75: the window, another run, a running pass) is retried every TERM_MENTIONS_RETRY_S, at most
# TERM_MENTIONS_RETRIES times, then waits for the next week. TERM_MENTIONS=0 turns it OFF (an env-only cron
# deploy). Same non-numeric guards as SLICE_S; an out-of-range day reads as Saturday.
TERM_MENTIONS="${TERM_MENTIONS:-1}"
TERM_MENTIONS_DOW="${TERM_MENTIONS_DOW:-6}"
case "${TERM_MENTIONS_DOW}" in [1-7]) ;; *) TERM_MENTIONS_DOW=6 ;; esac
TERM_MENTIONS_AT="${TERM_MENTIONS_AT:-10:00}"
TERM_MENTIONS_TIMEOUT_S="${TERM_MENTIONS_TIMEOUT_S:-3600}"
[ "${TERM_MENTIONS_TIMEOUT_S}" -ge 1 ] 2>/dev/null || TERM_MENTIONS_TIMEOUT_S=3600
TERM_MENTIONS_RETRY_S="${TERM_MENTIONS_RETRY_S:-3600}"
[ "${TERM_MENTIONS_RETRY_S}" -ge 1 ] 2>/dev/null || TERM_MENTIONS_RETRY_S=3600
TERM_MENTIONS_RETRIES="${TERM_MENTIONS_RETRIES:-6}"
[ "${TERM_MENTIONS_RETRIES}" -ge 0 ] 2>/dev/null || TERM_MENTIONS_RETRIES=6

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

# catch_up_between TARGET LAST_EXPECTED [WHY] -> a `--catch-up` pass for each day catch_up_days lists (the
# nights the sleep ALSO skipped, or that became expected while the boot block ran). Each day is its own log
# line + its own fail-open invocation; `--catch-up` is a no-op when a live pass for that as-of already ran
# (the R3 run log is the memory). WHY labels the log line (default "late wake", the in-loop caller). A
# catch-up night has no retry, so a failed one is final: it pages (H3). One that lands pings the heartbeat
# when it is the last expected night (H2).
catch_up_between() {
  for _cb in $(catch_up_days "$1" "$2"); do
    echo "daily-cron: ${3:-late wake} — catching up ${_cb}"
    if run_pass --run-kind cron --catch-up --asof "${_cb}"; then
      beat_if_current "${_cb}"
    else
      _cb_rc=$?
      echo "daily-cron: catch-up ${_cb} FAILED (exit ${_cb_rc}) (continuing)"
      page "${_cb}" "the ${3:-late wake} catch-up FAILED (exit ${_cb_rc}); the night may have no call-of-record"
    fi
  done
}

# --- the waits (the helpers that read the ambient clock) -------------------------------------------------

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

# db_probe -> exit 0 once Postgres accepts a connection at `db.session.database_url()` — the SAME
# DATABASE_URL resolution `db.session.connect` gives every CLI pass below — else non-zero with the error
# collapsed to ONE stderr line (an import failure included, so a broken image cannot spray a traceback per
# probe). An explicit `connect_timeout=5` bounds one attempt, so a black-holed host cannot hold a probe
# open (explicit rather than the PGCONNECT_TIMEOUT env var: psycopg honors the kwarg on every 3.x, the env
# var only on the newer ones, and pyproject floors it at 3.1).
db_probe() {
  python -c '
import sys
try:
    import psycopg
    from db.session import database_url
    psycopg.connect(database_url(), connect_timeout=5).close()
except Exception as e:
    sys.exit(type(e).__name__ + ": " + " ".join(str(e).split()))
'
}

# wait_for_db WHAT -> returns once db_probe passes or DB_WAIT_S has elapsed, whichever comes first — and
# returns either way (fail-open: the caller fires WHAT regardless; its own exit + retry are the backstop).
# Quiet when the first probe passes (the normal night: one probe, no log line). Otherwise one line when
# the wait starts and one when it ends, ready or not, with the last error. Polls every DB_POLL_S through
# wait_until — sliced on the wall clock, the same suspend-safe idiom as the schedule wait, never one long
# `sleep` — and bounded on the wall clock too, so a probe's own run time counts against DB_WAIT_S (the wait
# can end at most one probe past the bound: MEASURED 13-14 s at DB_WAIT_S=8 against a black-holed server,
# each probe cut off at its 5 s connect_timeout).
wait_for_db() {
  _dbw_err=$(db_probe 2>&1) && return 0
  _dbw_start=$(date +%s)
  _dbw_end=$(( _dbw_start + DB_WAIT_S ))
  echo "daily-cron: $(date) — DB not ready for the $1 (${_dbw_err}); waiting up to ${DB_WAIT_S}s"
  while :; do
    _dbw_now=$(date +%s)
    if [ "${_dbw_now}" -ge "${_dbw_end}" ]; then
      echo "daily-cron: DB still not ready after $(( _dbw_now - _dbw_start ))s (${_dbw_err}) — firing the $1 anyway"
      return 0
    fi
    _dbw_next=$(( _dbw_now + DB_POLL_S ))
    [ "${_dbw_next}" -gt "${_dbw_end}" ] && _dbw_next="${_dbw_end}"
    wait_until "${_dbw_next}"
    if _dbw_err=$(db_probe 2>&1); then
      echo "daily-cron: DB ready after $(( $(date +%s) - _dbw_start ))s — firing the $1"
      return 0
    fi
  done
}

# --- the failure signals: the bounded pass, the page, the heartbeat, the age-gated backup ----------------

# run_pass ARGS... -> `python -m pipeline.daily ARGS...` under `timeout` (C2; see the header), returning its
# exit status. 124 means the bound stopped it, logged here so the caller's FAILED line has a cause; with
# `-k 60`, a pass that outlives the TERM is KILLed a minute later (137). The callers' retry / page / ping
# logic reads the status exactly as before: a timeout is one more non-zero exit.
run_pass() {
  timeout -k 60 "${RUN_TIMEOUT_S}" python -m pipeline.daily "$@"
  _rp_rc=$?
  case "${_rp_rc}" in
    124) echo "daily-cron: pass TIMED OUT after ${RUN_TIMEOUT_S}s and was stopped (pipeline.daily $*)" ;;
    137) echo "daily-cron: pass was KILLED (SIGKILL: the timeout's -k, or the OOM killer) (pipeline.daily $*)" ;;
  esac
  return "${_rp_rc}"
}

# page ASOF REASON -> the in-band failure page (H3; see the header): `python -m notify.page`, which needs no
# DB, never raises and exits 0. Bounded by `timeout` and fail-open, so a page that cannot run costs one log
# line, never a stalled loop (the external heartbeat is the backstop for that case).
page() {
  echo "daily-cron: PAGING — ${1}: ${2}"
  timeout -k 10 120 python -m notify.page --asof "$1" --reason "$2" \
    || echo "daily-cron: the failure page did not run cleanly (exit $?) (continuing)"
}

# ping_url URL WHAT -> ONE best-effort GET (H2): `curl -fsS -m 10 --retry 3`, so at most about 50 s even
# when the service is unreachable (MEASURED offline: 4 attempts, ~7 s, on a DNS failure). Logs one line
# either way, naming WHAT and curl's LAST error line (the retries repeat it) but never the URL (a ping URL
# is a credential: anyone holding it can fake the heartbeat). Never fails its caller.
ping_url() {
  if _pu_err=$(curl -fsS -m 10 --retry 3 -o /dev/null "$1" 2>&1); then
    echo "daily-cron: heartbeat sent — ${2}"
  else
    echo "daily-cron: heartbeat FAILED — ${2} ($(printf '%s\n' "${_pu_err}" | tail -n 1)) (continuing)"
  fi
}

# beat_if_current ASOF -> the success ping, sent only when ASOF is the LAST EXPECTED as-of right now, so the
# ping always means "the last expected night is recorded" (see the header). A pass that landed an older
# night holds it, and the catch-up of the newest night pings instead. Silent when HEARTBEAT_URL is unset.
beat_if_current() {
  [ -n "${HEARTBEAT_URL}" ] || return 0
  _bc_last=$(last_expected_asof "$(date +%s)")
  if [ "$1" = "${_bc_last}" ]; then
    ping_url "${HEARTBEAT_URL}" "night $1 recorded"
  else
    echo "daily-cron: heartbeat held — $1 recorded, but the last expected night is ${_bc_last}"
  fi
}

# beat_start ASOF -> HEARTBEAT_URL/start just before the scheduled run (healthchecks.io's start signal: a run
# that starts and never reports success alerts after the grace, and the dashboard shows the run's duration).
# Silent when HEARTBEAT_URL is unset.
beat_start() {
  [ -n "${HEARTBEAT_URL}" ] || return 0
  ping_url "${HEARTBEAT_URL%/}/start" "run $1 started"
}

# snapshot ASOF WHERE [gated] -> one `python -m pipeline.backup` (Slice 4 / M3; see the header). With `gated`
# (the boot) it passes `--if-older-than-h BACKUP_MAX_AGE_H`, so it dumps only when the newest unlabeled
# snapshot is at least that old and otherwise prints a no-op line; without it (the in-loop wake) it always
# dumps. Exit 0 means a fresh snapshot exists either way, so it pings HEARTBEAT_BACKUP_URL; a failure pages,
# naming ASOF (the night it ran after) and WHERE (boot / nightly). Fail-open: never kills the loop.
snapshot() {
  _sn_gate=""
  [ "${3:-}" = gated ] && _sn_gate="--if-older-than-h ${BACKUP_MAX_AGE_H}"
  # _sn_gate is split on purpose: empty, or the flag plus its (guarded, numeric) value
  if python -m pipeline.backup ${_sn_gate}; then
    if [ -n "${HEARTBEAT_BACKUP_URL}" ]; then
      ping_url "${HEARTBEAT_BACKUP_URL}" "backup fresh"
    fi
  else
    _sn_rc=$?
    echo "daily-cron: ${2} backup FAILED (exit ${_sn_rc}) (continuing)"
    page "$1" "the ${2} DB backup FAILED (exit ${_sn_rc}); there may be no fresh snapshot"
  fi
}

# editions_nightly ASOF -> Editions S5: one `python -m editions.run nightly --apply` (the Scoreboard editions'
# nightly page + catch-up, the ink, the live-edge self-check; editions/nightly.py), run right AFTER the post-pass
# backup in this SCHEDULING layer — never inside run_daily_pass, so the Admin "Run daily now" never fires it.
# Every rule lives in the job: it runs only when the newest run artifact is a FINISHED nightly pass (it names
# its own as-of; ASOF here is for the log line only), it refuses (exit 75) while the call-of-record lock is
# held (a probe, never a take) or another editions job runs, and a dry/held/up-to-date night is a quiet line.
# Bounded by `timeout` (EDITIONS_TIMEOUT_S), FAIL-OPEN: a failure is one log line (and a `crashed` artifact the
# Admin line shows), never a page from here and never the end of the loop — the next night's catch-up fills
# every page this one did not write. Its own self-check pages a real mismatch itself (notify_health).
editions_nightly() {
  if [ "${EDITIONS_NIGHTLY}" = 0 ]; then
    echo "daily-cron: EDITIONS_NIGHTLY=0 — the editions nightly page job is OFF"
    return 0
  fi
  timeout -k 60 "${EDITIONS_TIMEOUT_S}" python -m editions.run nightly --apply
  _en_rc=$?
  case "${_en_rc}" in
    0) ;;
    75) echo "daily-cron: editions nightly REFUSED for ${1} (a pass or another editions job holds its lock) (continuing)" ;;
    124) echo "daily-cron: editions nightly TIMED OUT after ${EDITIONS_TIMEOUT_S}s for ${1} (continuing; the next night catches up)" ;;
    *) echo "daily-cron: editions nightly FAILED (exit ${_en_rc}) for ${1} (continuing; the next night catches up)" ;;
  esac
  return 0
}

# warm_dashboards NIGHT -> the dashboards' cache warm-up (2026-10-30): one `python -m pipeline.warm_dashboards
# --night NIGHT`, which GETs each heavy dashboard's default view once from the running API so the next visit is a
# cache hit (the cache is in the API process and is emptied by every cron run; see the header). It sits LAST in the
# nightly wake, after the backup and the editions page, in this SCHEDULING layer: never inside run_daily_pass, so
# the Admin "Run daily now" never fires it, and it runs after the heartbeat has already closed the night. NIGHT is
# the last expected night; the job warms the as-of the NEXT visit sends (the day after it, plus the Monday after a
# weekend), not NIGHT itself. It reads over HTTP only (base URL: ALPHADECK_WARM_BASE_URL, default the compose
# service `backend`), so it needs no DB and writes nothing but its own run artifact (`data/warm_dashboards`).
# Bounded by `timeout` (WARM_DASHBOARDS_TIMEOUT_S), FAIL-OPEN and ALWAYS returns 0: a failure, a timeout or a page
# that errored is one log line (the CLI logs one line per page), never the end of the loop and never a change to
# the pass's status or exit code. ALPHADECK_WARM_DASHBOARDS=0 turns it OFF (the CLI checks the switch too, so a
# hand run honors it).
warm_dashboards() {
  if [ "${ALPHADECK_WARM_DASHBOARDS:-1}" = 0 ]; then
    echo "daily-cron: ALPHADECK_WARM_DASHBOARDS=0 — the dashboard cache warm-up is OFF"
    return 0
  fi
  timeout -k 30 "${WARM_DASHBOARDS_TIMEOUT_S}" python -m pipeline.warm_dashboards --night "$1"
  _wd_rc=$?
  case "${_wd_rc}" in
    0) ;;
    124) echo "daily-cron: dashboard warm-up TIMED OUT after ${WARM_DASHBOARDS_TIMEOUT_S}s for night $1 (continuing; the first visit computes cold)" ;;
    *) echo "daily-cron: dashboard warm-up FAILED (exit ${_wd_rc}) for night $1 (continuing; the first visit computes cold)" ;;
  esac
  return 0
}

# onboard_loop -> X1, THE SECOND SCHEDULE: the staged cold-ingest job (`python -m pipeline.onboarding`), fired
# every ONBOARD_EVERY_S after the previous run ENDS (so two runs never overlap), forever, in the background of
# this one sidecar. The loop is deliberately dumb — every rule lives in the job and is checked there:
#   - it REFUSES inside the nightly window (Settings.onboard_window_before_min / _after_min around RUN_AT,
#     21:30-23:30 ET by default) and STOPS between names / legs when it enters it, or when it sees a pass
#     holding the call-of-record lock (the nightly, its retry, a catch-up, an Admin "Run daily now") — it reads
#     pg_locks, never takes that lock, so it can never make a pass exit 75;
#   - one batch is bounded by names, wall time and a daily cap (Settings.onboard_*), and resumable: a run
#     killed here (the timeout, a deploy, the VM) leaves each name's per-leg checkpoint, which the next run
#     resumes;
#   - an idle run (nothing queued) or a refusal prints one `onboarding:` line and writes nothing.
# Its OWN run kind (`--run-kind cron`, pass_kind "onboard"), its OWN artifacts (data/onboard_runs, never the
# nightly's cron_runs) and no heartbeat (Admin reads a stalled queue from the table + the artifacts). Each run
# is bounded by `timeout` like a pass (C2), so a hang cannot freeze the loop. Fail-open: a run that fails is one
# log line, never the end of the loop, and the nightly loop below never waits for it.
onboard_loop() {
  echo "onboard-cron: every ${ONBOARD_EVERY_S}s after the previous run ends, outside the nightly window — each run bounded at ${ONBOARD_TIMEOUT_S}s"
  while :; do
    timeout -k 60 "${ONBOARD_TIMEOUT_S}" python -m pipeline.onboarding --run-kind cron
    _ob_rc=$?
    case "${_ob_rc}" in
      0) ;;
      124) echo "onboard-cron: run TIMED OUT after ${ONBOARD_TIMEOUT_S}s and was stopped (its names resume next run)" ;;
      *) echo "onboard-cron: run FAILED (exit ${_ob_rc}) (continuing; its names resume next run)" ;;
    esac
    wait_until "$(( $(date +%s) + ONBOARD_EVERY_S ))"
  done
}

# next_weekly_epoch EPOCH [DOW AT] -> the epoch of the next DOW (`date +%u`) at AT strictly AFTER EPOCH (container
# TZ). DOW / AT default to MASTER_REFRESH_DOW / MASTER_REFRESH_AT (the company-list refresh); the term-mentions
# loop passes its own. Pure over its arguments + those two settings, like the schedule math above.
next_weekly_epoch() {
  _nw_dow="${2:-${MASTER_REFRESH_DOW}}"
  _nw_at="${3:-${MASTER_REFRESH_AT}}"
  _nw_day=$(date -d "@$1" +%F)
  _nw_i=0
  while [ "${_nw_i}" -le 7 ]; do
    _nw_cand=$(date -d "${_nw_day} + ${_nw_i} day" +%F)
    if [ "$(date -d "${_nw_cand}" +%u)" = "${_nw_dow}" ]; then
      _nw_e=$(date -d "${_nw_cand} ${_nw_at}" +%s)
      if [ "${_nw_e}" -gt "$1" ]; then
        echo "${_nw_e}"
        return 0
      fi
    fi
    _nw_i=$(( _nw_i + 1 ))
  done
  echo $(( $1 + 604800 )) # unreachable with a valid day; never a hot loop
}

# master_refresh_run ARGS... -> ONE bounded `python -m pipeline.populate_master --live --apply --run-kind cron`,
# returning its exit status (75 = refused). One log line on anything but success; the job prints its own quiet
# "company-list: Company list · refreshed ..." line (and, loud, any conflict).
master_refresh_run() {
  timeout -k 60 "${MASTER_REFRESH_TIMEOUT_S}" python -m pipeline.populate_master --live --apply --run-kind cron "$@"
  _mr_rc=$?
  case "${_mr_rc}" in
    0) ;;
    75) echo "master-refresh: REFUSED (the nightly window, another refresh or a running pass) — nothing written" ;;
    124) echo "master-refresh: TIMED OUT after ${MASTER_REFRESH_TIMEOUT_S}s and was stopped (one transaction: nothing written)" ;;
    *) echo "master-refresh: FAILED (exit ${_mr_rc}) (continuing; the next schedule retries)" ;;
  esac
  return "${_mr_rc}"
}

# master_refresh_loop -> THE THIRD SCHEDULE: the weekly company-list refresh (`python -m pipeline.populate_master
# --live --apply`, securities/master_refresh.py): SEC's current company file onto the master — new companies, a
# rename updated IN PLACE (every fact stays attached), new classes, the primary flags — plus the fund list. In the
# background of this sidecar, like onboard_loop, and deliberately dumb: the job refuses inside the nightly window,
# holds its OWN lock (advisory id 5) and yields to a running pass (a pg_locks probe; it never takes the
# call-of-record lock). At start it runs once with `--if-stale-days 7` (a no-op unless the newest applied refresh
# is over a week old — a Saturday the box missed; never on a first deploy, which has no applied run yet), then
# fires every MASTER_REFRESH_DOW at MASTER_REFRESH_AT. A refused run retries MASTER_REFRESH_RETRY_S later, at most
# MASTER_REFRESH_RETRIES times. Fail-open: a failed run is one log line, never the end of the loop.
master_refresh_loop() {
  echo "master-refresh: weekly on day ${MASTER_REFRESH_DOW} (date +%u) at ${MASTER_REFRESH_AT} (${TZ:-UTC}) — each run bounded at ${MASTER_REFRESH_TIMEOUT_S}s"
  master_refresh_run --if-stale-days 7
  while :; do
    wait_until "$(next_weekly_epoch "$(date +%s)")"
    _mr_try=0
    while :; do
      master_refresh_run && break
      [ "$?" = 75 ] || break
      _mr_try=$(( _mr_try + 1 ))
      [ "${_mr_try}" -le "${MASTER_REFRESH_RETRIES}" ] || break
      echo "master-refresh: retry ${_mr_try} of ${MASTER_REFRESH_RETRIES} in ${MASTER_REFRESH_RETRY_S}s"
      wait_until "$(( $(date +%s) + MASTER_REFRESH_RETRY_S ))"
    done
  done
}

# term_mentions_run ARGS... -> ONE bounded `python -m pipeline.term_mentions --weekly --apply --run-kind cron`,
# returning its exit status (75 = refused or stopped). One log line on anything but success; the job prints its
# own summary line (and one line per failed term-month).
term_mentions_run() {
  timeout -k 60 "${TERM_MENTIONS_TIMEOUT_S}" python -m pipeline.term_mentions --weekly --apply --run-kind cron "$@"
  _tm_rc=$?
  case "${_tm_rc}" in
    0) ;;
    75) echo "term-mentions: REFUSED or STOPPED (the nightly window, another run or a running pass) — the terms already written are kept" ;;
    124) echo "term-mentions: TIMED OUT after ${TERM_MENTIONS_TIMEOUT_S}s and was stopped (one transaction per term: the terms already written are kept)" ;;
    *) echo "term-mentions: FAILED (exit ${_tm_rc}) (continuing; the next schedule retries)" ;;
  esac
  return "${_tm_rc}"
}

# term_mentions_loop -> THE FOURTH SCHEDULE: the weekly EDGAR term-mentions count (`python -m pipeline.term_mentions
# --weekly --apply`): per card SIGNAL term, the current month and the one before re-counted (plus any month a term
# added since is missing) — display data only (the Narrative Adoption Curve dashboard). In the background of this
# sidecar, like master_refresh_loop, and deliberately dumb: the job is a no-op until the operator's first backfill
# (an empty table), refuses inside the nightly window, holds its OWN lock (advisory id 6), yields to a running pass
# (a pg_locks probe; it never takes the call-of-record lock) and stops between terms when one starts. At start it
# runs once with `--if-stale-days 7` (a no-op unless the newest applied run is over a week old — a Saturday the
# box missed; never before the first backfill), then fires every TERM_MENTIONS_DOW at TERM_MENTIONS_AT. A refused /
# stopped run retries TERM_MENTIONS_RETRY_S later, at most TERM_MENTIONS_RETRIES times. Fail-open: a failed run is
# one log line, never the end of the loop.
term_mentions_loop() {
  echo "term-mentions: weekly on day ${TERM_MENTIONS_DOW} (date +%u) at ${TERM_MENTIONS_AT} (${TZ:-UTC}) — each run bounded at ${TERM_MENTIONS_TIMEOUT_S}s"
  term_mentions_run --if-stale-days 7
  while :; do
    wait_until "$(next_weekly_epoch "$(date +%s)" "${TERM_MENTIONS_DOW}" "${TERM_MENTIONS_AT}")"
    _tm_try=0
    while :; do
      term_mentions_run && break
      [ "$?" = 75 ] || break
      _tm_try=$(( _tm_try + 1 ))
      [ "${_tm_try}" -le "${TERM_MENTIONS_RETRIES}" ] || break
      echo "term-mentions: retry ${_tm_try} of ${TERM_MENTIONS_RETRIES} in ${TERM_MENTIONS_RETRY_S}s"
      wait_until "$(( $(date +%s) + TERM_MENTIONS_RETRY_S ))"
    done
  done
}

# --- the trigger ------------------------------------------------------------------------------------------

echo "daily-cron: scheduled for ${RUN_AT} (${TZ:-UTC}), Mon-Fri — the daily CLI is idempotent + force-refreshes"
# the failure signals' state, in one line the deploy check can read (on/off only: the URLs are credentials)
_hb_run=off
[ -n "${HEARTBEAT_URL}" ] && _hb_run=on
_hb_bk=off
[ -n "${HEARTBEAT_BACKUP_URL}" ] && _hb_bk=on
echo "daily-cron: each pass bounded at ${RUN_TIMEOUT_S}s · heartbeat ${_hb_run} · backup heartbeat ${_hb_bk} · boot backup age gate ${BACKUP_MAX_AGE_H}h"

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
# Idempotent + fail-open: it can DELAY the loop (the DB wait + one retry, both bounded) but never block it.
# Exactly ONE night by construction — never older holes (a deploy must never silently backfill history; the
# in-loop catch-up above is bounded to the sleep that just ended for the same reason). The target is fixed
# HERE, at boot, before any wait: the nights that become expected during the block are the crossed-RUN_AT
# catch-up's (below), not a re-read of the target.
_boot_target=$(last_expected_asof "$(date +%s)")
echo "daily-cron: booted $(date) — catch-up for the last expected night ${_boot_target} (no-op if its post-${RUN_AT} live pass already ran)"
# WAIT FOR THE DB FIRST (2026-10-01 — the cold-start race in the header): on a daemon restart this sidecar
# boots in the same second as Postgres, and the old block fired straight into `Connection refused`.
wait_for_db "boot catch-up ${_boot_target}"
if run_pass --run-kind cron --catch-up --asof "${_boot_target}"; then
  beat_if_current "${_boot_target}" # recorded now, or already (a no-op): either way the night is in
else
  # The scheduled run's G2a retry, given to the boot too: a boot catch-up that failed used to be the end
  # of that night (the loop re-anchors to the next RUN_AT and nothing re-tries a past night). ONE retry,
  # RETRY_DELAY_S later through wait_until; still `--catch-up`, so a no-op if the night recorded meanwhile
  # (an operator's "Run daily now", or a pass that died after recording).
  _rc=$?
  echo "daily-cron: boot catch-up ${_boot_target} FAILED (exit ${_rc}) — ONE retry in ${RETRY_DELAY_S}s (a no-op if the night recorded anyway)"
  wait_until "$(( $(date +%s) + RETRY_DELAY_S ))"
  echo "daily-cron: $(date) — retrying boot catch-up ${_boot_target} (--catch-up)"
  if run_pass --run-kind cron --catch-up --asof "${_boot_target}"; then
    beat_if_current "${_boot_target}"
  else
    # H3 — the boot's night failed for good: page it from here, since no pass lived to (the 10-01 shape)
    _rc=$?
    echo "daily-cron: boot catch-up retry ${_boot_target} FAILED (exit ${_rc}) (continuing to the schedule)"
    page "${_boot_target}" "the boot catch-up and its ONE retry both FAILED (last exit ${_rc}); the night may have no call-of-record"
  fi
fi
# THE NIGHTS THE BOOT BLOCK ITSELF CROSSED. The wait + the catch-up + its retry can run for many minutes,
# and the loop below anchors on the clock it finds: a block that ends past today's RUN_AT on a weekday gets
# `next` = tomorrow, and tonight would never fire (a boot at 22:20 whose block ends at 22:35). Catch up
# every weekday strictly after the boot target up to the last expected as-of NOW — exactly the nights that
# became expected during the block (empty on almost every boot), never an older one.
catch_up_between "${_boot_target}" "$(last_expected_asof "$(date +%s)")" "boot block crossed ${RUN_AT}"
# M3 — THE BOOT'S SNAPSHOT, after the catch-up so it holds the night just landed. The nightly dump used to
# ride only the in-loop wake, so a night caught up here got none (MEASURED: ~4 days without a dump around
# 10-12). Age-gated, so the normal boot (last night's dump is hours old) is a no-op line, and a
# crash-looping container cannot dump on every restart.
snapshot "${_boot_target}" "boot" gated
if [ "${CRON_EXIT_AFTER_BOOT:-}" = 1 ]; then
  # the test seam (header): stop before the infinite loop so the boot block can be exercised on its own
  echo "daily-cron: CRON_EXIT_AFTER_BOOT=1 — exiting before the schedule loop"
  exit 0
fi

# X1 — THE SECOND SCHEDULE, started AFTER the boot block (so a boot catch-up never shares the box with an
# onboarding batch) and in the BACKGROUND (so the nightly loop below never waits for it). ONBOARD_EVERY_S=0 = off.
if [ "${ONBOARD_EVERY_S}" -gt 0 ]; then
  onboard_loop &
else
  echo "onboard-cron: ONBOARD_EVERY_S=0 — the staged onboarding job is OFF in this sidecar"
fi
# THE THIRD SCHEDULE — the weekly company-list refresh, likewise after the boot block and in the background.
if [ "${MASTER_REFRESH}" != 0 ]; then
  master_refresh_loop &
else
  echo "master-refresh: MASTER_REFRESH=0 — the weekly company-list refresh is OFF in this sidecar"
fi
# THE FOURTH SCHEDULE — the weekly term-mentions count, likewise after the boot block and in the background.
if [ "${TERM_MENTIONS}" != 0 ]; then
  term_mentions_loop &
else
  echo "term-mentions: TERM_MENTIONS=0 — the weekly term-mentions count is OFF in this sidecar"
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
  wait_until "${next}"
  woke=$(date +%s)
  late_min=$(( (woke - next) / 60 ))
  if [ "${late_min}" -ge 5 ]; then
    # the sleep drift, visible without forensics (a suspended laptop; the measured 23:37 / 00:24 / 09:09 wakes)
    echo "daily-cron: LATE WAKE — $(date) is ${late_min} min past the scheduled $(date -d "@${next}")"
  fi
  if is_weekday "${target}"; then
    # the same bounded, fail-open DB wait as the boot (quiet when the DB is up): a Postgres restarting at
    # RUN_AT costs up to DB_WAIT_S of delay instead of a failed run and a RETRY_DELAY_S retry
    wait_for_db "scheduled run ${target}"
    echo "daily-cron: $(date) — running pipeline.daily --late-ok --asof ${target}"
    beat_start "${target}" # H2 — "started"; the success ping below (or a catch-up's) closes it
    # the SCHEDULED run always fires (no --catch-up): it re-versions even if an operator hand-ran that day.
    # --late-ok (C3): the CLI refuses an --asof older than the last expected night unless told otherwise.
    # The target is fixed BEFORE the sleep, so on time it IS the last expected night and the flag is inert;
    # it matters only when a suspend overshoots past the NEXT weekday's RUN_AT (target Tue, wake Wed 22:40),
    # where the night is late by construction — the same pass this loop always ran, never a refusal + a
    # 20-minute retry. Catch-ups and retries pass --catch-up, which the refusal already exempts.
    if run_pass --run-kind cron --late-ok --asof "${target}"; then
      beat_if_current "${target}"
    else
      _rc=$? # the failed run's exit status, read first (any later command resets $?)
      # G2a — RETRY ONCE. A scheduled run that fails used to be the end of the night: the loop caught the
      # non-zero exit, logged it, and waited for tomorrow, so a transient fault (a DB restart mid-run, a
      # vendor 5xx, a network blip) cost that night's call-of-record outright — and with a daytime row on
      # the same day the Admin page could still read healthy over it (see the hole check in
      # app/routers/admin.py). One retry, `RETRY_DELAY_S` later.
      #
      # THE EXIT CODE NOW MEANS "THE NIGHT HAS NO RECORD" (F4). `pipeline.daily` exits 0 whenever the call
      # step reached at least one thesis — a quiet unchanged night, a partial night where some theses
      # errored, an empty roster, a deliberate `--no-live` — and non-zero ONLY when nothing recorded. So
      # reaching this branch already means a re-run is the right move; per-thesis errors page through R4
      # instead of pausing the loop for nothing. Two shapes land here, and both resolve correctly without
      # the shell knowing which it hit:
      #   - the pass CRASHED before writing its artifact (DB unreachable at connect, killed mid-run,
      #     stopped by the C2 timeout) -> no artifact -> the guard finds nothing -> the retry runs the
      #     night in full;
      #   - the pass COMPLETED with EVERY thesis withheld/errored -> nothing recorded -> the guard lets the
      #     retry run (that is 2b; before it, this artifact would have blocked even a boot catch-up).
      # --catch-up STAYS the guard regardless — belt-and-suspenders for the narrow case of a pass that
      # wrote its artifact and then died after recording: it no-ops when a LIVE pass for this as-of that
      # STARTED at/after tonight's RUN_AT already RECORDED for some thesis (cron_run_log.already_ran_live).
      # wait_until, not sleep: the same monotonic-clock reason as the schedule wait (a suspended host does
      # not advance `sleep`'s clock, so a raw sleep could overshoot by the whole suspend).
      echo "daily-cron: run FAILED (exit ${_rc}) — ONE retry in ${RETRY_DELAY_S}s (a no-op if the night recorded anyway)"
      wait_until "$(( $(date +%s) + RETRY_DELAY_S ))"
      echo "daily-cron: $(date) — retrying ${target} (--catch-up)"
      if run_pass --run-kind cron --catch-up --asof "${target}"; then
        beat_if_current "${target}"
      else
        # H3 — the night failed for good: page it from here, since no pass lived to say so
        _rc=$?
        echo "daily-cron: retry ${target} FAILED (exit ${_rc}) (continuing to the next day)"
        page "${target}" "the scheduled run and its ONE retry both FAILED (last exit ${_rc}); the night may have no call-of-record"
      fi
    fi
  else
    echo "daily-cron: $(date) — weekend (asof ${target}), no scheduled run"
  fi
  # A long sleep can skip MORE than the target night (a weekend target that wakes on Tuesday skipped Monday):
  # catch up every weekday strictly after the target up to the last expected as-of at the instant the run
  # above FINISHED — the clock NOW, not `woke`. A live run takes 7-15 min; anchored at the wake, a run that
  # crossed the next RUN_AT (case F in the header) lost that night — the window did not include it yet, and
  # the loop then re-anchored `next` past it. `woke` stays for the LATE WAKE line only.
  after=$(date +%s)
  _last=$(last_expected_asof "${after}")
  catch_up_between "${target}" "${_last}"
  if is_weekday "${target}" || [ -n "$(catch_up_days "${target}" "${_last}")" ]; then
    # Slice 4 — a nightly DB snapshot right after the daily pass, ONCE PER WAKE (not per catch-up day),
    # fail-open (a failed backup never kills the loop). Deliberately in the SCHEDULING layer, NOT folded
    # into run_daily_pass (so the manual "Run daily now" button does not also dump). Retention
    # (keep-last-N, labeled exempt) is pipeline.backup's. UNCONDITIONAL, unlike the boot's: it captures
    # the run that just finished, which no earlier dump holds (M3 in the header). It also runs after a
    # weekend-target wake whose catch-up covered a weekday (M3), and a failure now pages (H3).
    snapshot "${target}" "nightly"
    # Editions S5 — the Scoreboard editions' nightly page, AFTER the backup (so the dump never waits on it and
    # never captures a half-run), in this scheduling layer (never run_daily_pass). Fail-open; see the function.
    editions_nightly "${target}"
    # The dashboards' cache warm-up, LAST: after the pass, the backup and the editions page, so the cache it fills
    # is the generation the morning visit reads (nothing after this moves it). NIGHT is the last expected night
    # (`_last`, read above), the one the pass or the catch-up just recorded. Fail-open; see the function.
    warm_dashboards "${_last}"
  fi
done
