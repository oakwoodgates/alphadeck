# FEED_LOOP.md — how the platform feeds itself (the back-half ingest + the daily call-of-record cron)

> Repo path: `docs/FEED_LOOP.md`. The back half's **data loop**: how a thesis's basket gets the CALL-ENGINE
> facts (insider Form 4 + price EOD) it needs to WARM/ARM, and how a daily cron refreshes them and appends the
> day's **call-of-record** per thesis — so the platform *feeds itself*. This is the M2 subsystem (the MVP's
> second half). Companion to `CHAIN_DRAFTER.md` (the front door — narrative → chain), `CALL_LOGIC.md` (what the
> facts ARM into), `DATA_SOURCES.md` (the price/EDGAR sources + the Yahoo split-adjustment finding),
> `DATA_FLOW.md` (where data lives), `INVARIANTS.md` (#1 no-lookahead, #2 exact membership, the calls log).
> Engines: `backend/pipeline/ingest_thesis.py` · `backend/pipeline/daily.py` · `backend/ingest/prices/source.py`
> · `backend/repositories/calls_repo.py` (`record_if_changed` / `_canonical`) · the `cron` sidecar in
> `docker-compose.yml` + `backend/scripts/daily_cron.sh` · `backend/pipeline/backfill.py` (a missed night,
> reconstructed with a PINNED `known_at`).
>
> **Status: BUILT** — the per-thesis ingest (PR #70), the daily cron + `record_if_changed` (#71), the
> fresh-data fix + the price-source seam (#72), the scheduling sidecar (#73), and the **cron-freeze
> remediation** (#196–#200): the key-classed EDGAR cache TTL, a recording gate, a run-of-record log, a health
> pager, and catch-up-on-boot. With it, **M2 — "the functional platform feeds itself" — is complete**, and the
> North Star is reachable end to end: create a thesis (M1) → `ingest_thesis` pulls real insider + price → it
> WARMS/ARMS on real data → the daily cron logs the call-of-record.
>
> **Freshness caveat (load-bearing): "feeds itself" was literally true only after #196.** For ~11 days the
> EDGAR cache was cache-first *forever*, so the daily cron silently could not see a Form 4 newer than the
> cache — the insider leg of "feeds itself" was frozen while every run looked healthy (0 appended == a quiet
> day). R1's key-classed 12h TTL (`DATA_SOURCES.md:45–58`) made it real and enforced; R2–R6 make a recurrence
> *visible*. Full account: `POSTMORTEM_CRON_FREEZE_2026-07.md`.
>
> **Trust caveat (load-bearing): "feeds itself" is NOT "validated forward."** This arc is platform PLUMBING,
> not the call engine. It did not change the trust validation — still in-sample (n=19; see `ROADMAP.md`'s "Keep
> the trust state honest" box); the forward trust loop's instrument (the **Scoreboard v1**) is now BUILT and reads this record. The daily
> call-of-record is the forward RECORD it tracks — still **Scoreboard-ready, not Scoreboard-coupled** (zero Scoreboard code in the cron).
>
> **Legend:** `[BUILT]` shipped · `[FILED]`/`[DEFERRED]` not built.

---

## The gap it closes

The create/promote path writes only the **spine** (the thesis + its basket + the chain structure). It does
**not** ingest the call-engine facts. So a freshly-created or freshly-drafted thesis has a basket with
`security_id`s but **no insider/price facts in the point-in-time store** → `assemble_from_pit` finds no
SignalEvents → it scores + promotes to **Incubating but never WARMS or ARMS.** M2 fills that: the back-half
facts are ingested per thesis, on demand and on a daily cadence.

The facts are **deterministic** (real Form 4 filings + real EOD bars) — **never model-sourced** (INVARIANT #3,
no LLM on this path).

## The per-thesis ingest — `pipeline/ingest_thesis.py`  `[BUILT #70]`

`ingest_thesis(conn, thesis_id, *, allow_live, force_refresh, user_agent, price_source, fund_source)`
ingests insider + price facts — and, for an ETF sleeve member, a fund shares-outstanding sample — for each
**resolved** basket member. CLI: `python -m pipeline.ingest_thesis --thesis <id>`.

- **Exact membership (#2).** It loops the thesis's basket and resolves each member by its already-resolved
  `security_id` via **`master.get`** (issuer ticker + CIK) — **never a fresh fuzzy resolve.** An unresolved
  member (`security_id` is null) is skipped; a placed id not in the tenant's master is reported, not guessed.
- **Three legs, each fail-visible.** The Form 4 leg, the price leg, and the fund-shares leg (ETF net flow —
  `ingest.funds.ingest_security`, gated on the member's `instrument_kind == 'etf'`: a non-ETF member
  contributes no shares sample and never touches the source, the form4 no-CIK mirror) each run in **their
  own try**, committing on success and rolling back on failure, so one leg's error never discards the
  others' work and never aborts the run — the error is captured into the name's `NameResult` (improving on
  the scanner's bare `except: pass`). One bad name never stops the rest. The fund-shares sample stores the
  page's OWN stated as-of date, appends nothing on an unchanged `(d, count)` re-sample (count-the-table
  guarded), and RE-VERSIONS a restated same-day count; its cache (`data/fund_cache/`) is a homogeneous
  daily cache on the **prices** freshness mechanism (`force_refresh`), not EDGAR's key-classed TTL.
- **Per-filing tolerance inside the Form 4 leg.** One unfetchable or unparseable filing — pre-2004-06-30
  Form 4s are SGML/text, not XML, and some ancient document URLs 404 (seen live: NVEC/INTT parse errors,
  ASYS/CVV year-2000 404s blanking whole names) — is **skipped-and-counted** (`NameResult.form4_skipped`,
  a printed per-filing warning, surfaced in the summaries only when nonzero) instead of aborting the leg.
  A skipped accession is never stored, so later runs re-attempt it rather than marking it done. Systemic
  failures (DB errors, a cache miss with live pulls off, a missing User-Agent) still abort the leg — those
  are the environment's fault, not one filing's.
- **Incremental — a re-run appends NOTHING.** Form 4: `form4.existing_accessions` skips filings already
  stored (accession is the filing identity). Prices: `eod_loader.latest_bar_date` → ingest only bars with
  `d > latest`. So re-ingesting an already-current name writes zero rows. *(This is the write-side guard; the
  bitemporal read already dedups — see the count-the-table discipline below for why the write guard matters.)*
- **No-lookahead (#1).** Both ingest fns leave `recorded_at` to the DB default **`now()`** — **never
  backdated**. A fact ingested today gets `recorded_at = now`, so an as-of read pinned at an earlier
  transaction time (`known_at` in the past) cannot see it; the replay guarantee holds.
- **Politeness.** EDGAR has a proactive token-bucket throttle (≤8 req/s, SEC etiquette). On top of it, the
  shared `ingest/http.py:polite_get` adds **reactive** backoff: it retries **429 / transient 5xx** with capped
  exponential backoff, honoring a numeric `Retry-After`, before raising (so a leg fails visibly rather than
  hammering). Tenant comes from the thesis (one thesis = one tenant).

## The price-source seam — `ingest/prices/source.py`  `[BUILT #72]`

The EOD price source sits behind an interface, so swapping it is changing an **adapter**, not a rewrite:

- **`PriceSource`** — a `get_bars(ticker, *, allow_live, force_refresh) -> [normalized EOD bars]` Protocol.
  The normalized bar is `{d, open, high, low, close, volume}` — exactly what `ingest_prices` consumes.
- **`YahooPriceSource`** (the live default) + **`StooqPriceSource`** (the formalized fallback) — thin
  adapters over the cache-first fetchers.
- **The price leg is DECOUPLED from the back-half loop** (`ingest.prices.ingest_security.
  ingest_bars_for_security` — ONE implementation): `pipeline.ingest_thesis` calls it per member inside
  its loop, and the Workbench's finalize screen calls it per name / per section
  (`POST /workbench/securities/{id}/ingest-prices`) so real market caps + live archetype hints exist
  BEFORE the operator promotes. Same incremental / cache-first / no-lookahead rules on both paths — the
  interactive path stays `force_refresh=False` (a first pull is a cache miss and fetches live; the daily
  cron owns force-refresh).
- **The contract is "a source of EOD bars," not "Yahoo's adjusted bars."** Deliberately **no `get_splits`**
  yet: owning the split adjustment ourselves (adjusting at read time from raw bars) is a larger storage+read
  change that would EXTEND this interface if/when we adopt such a source — the seam eases that swap, it does
  not pre-build it. (Today's Yahoo bars are already split-adjusted + re-based on every split — a property of
  the Yahoo adapter, documented in `DATA_SOURCES.md`, not baked into the contract.)
- **The modularity template.** This is the pattern the other sources (EDGAR/Form 4) can follow when they need
  the same swappability; this slice set it for prices (the source that was biting), not for everything.

## Fresh data — two caches, two freshness mechanisms

The two legs cache differently, and each froze its own way. **Both freshness policies are now in place** —
but they are DIFFERENT mechanisms; don't conflate them.

**The price leg — a per-call `force_refresh` flag** `[BUILT #72]`. `fetch_eod`/`fetch_csv` are **cache-first**:
a cache hit returns the stored bars and never re-pulls. That is right for dev / `--no-live` (reproducible,
polite), but **wrong for the daily cron** — a cache hit would return **stale** bars every run. The fix:
**`force_refresh`** (meaningful only WITH `allow_live`) bypasses a cache hit to re-pull live and **overwrite**
the cache. The recurring/daily path sets it; the dev/`--no-live` path leaves it off and stays cache-first; a
cache MISS always fetches (a new name's first ingest is fresh regardless).

**The EDGAR leg — a structural key-classed TTL, NOT a flag** `[BUILT #196]`. The far more damaging freeze was
here: the EDGAR cache served every key cache-first *forever*, so the daily cron could not see a Form 4 filing
newer than a name's cached `submissions` index — **~11 days of silently-frozen insider discovery** (the
`companyfacts` share counts and the `efts` discovery universe froze the same way). The fix is *not* a
per-call flag (that is "the #72 boolean wearing a timedelta — the next mutable endpoint forgets it"): freshness
is **key-classed on the cache-key prefix** — `forms/<accession>/<doc>` is immutable (cached forever), every
other prefix (`submissions`/`companyfacts`/`efts`) refreshes on a **12h TTL** when live. Default-refresh, so a
new mutable endpoint is safe-by-default; no caller threads anything. Full detail + the "works when you test it"
trap: `DATA_SOURCES.md:45–58`; the whole episode: `POSTMORTEM_CRON_FREEZE_2026-07.md`.

## The daily cron — `pipeline/daily.py`  `[BUILT #71]`

`run_daily(conn, *, asof=today, known_at=now, allow_live=True, force_refresh=True, notifier=None, …)`.
CLI: `python -m pipeline.daily`. For **each** thesis (`thesis_repo.list_all` — tenant intrinsic
per-thesis; **archived theses are skipped by the list's default**, the archive slice):

1. **Refresh facts** — `ingest_thesis` (incremental + fail-visible; `force_refresh=True`, the recurring path).
2. **Assemble TODAY's call WITHOUT writing** — `call_for_thesis(asof=today, known_at=now, record=False)`.
3. **Detect a MATERIAL TRANSITION** — state or verdict changed vs the PRIOR as-of's call-of-record →
   emit a `TransitionEvent` through the **notify seam** (`backend/notify`: a `Notifier` protocol; v1
   ships `LogNotifier` — a loud log line + the summary's TRANSITIONS block, printed only when there are
   any. DELIVERY is deferred: a channel is one adapter behind `get_notifier()`, zero cron rework). This
   is also the calls-log **material-change line**: clock/trigger churn versions the log via
   `record_if_changed` *without* being a transition; a state/verdict MOVE is what an operator would want
   to be told about.
4. **Append the call-of-record — GATED, and only if it changed** — `calls_repo.record_if_changed`, *unless the
   recording gate withholds it* (below).

- **The recording gate (R2, #198) — don't record a call built on bad data.** Before appending, the run
  computes a `withheld_reason` and **skips assemble/record entirely** when it is set: `"no-live"` (a
  `--no-live` run — cache-only, not a real call-of-record) or `"total ingest failure"` (the ingest raised, or
  *every* name errored). A **partial** failure still records, but the `calls` row is stamped
  `ingest_fresh=(ingest_errors==0)` + `ingest_errors` (provenance-only — never read by scoring; see migration
  `0023`). This closed the "1.64 s cron that recorded 6 calls off 0 ingested facts" hole (a total failure used
  to fall through and record).
- **The run-of-record log (R3, #197).** Every run writes one JSON to `data/cron_runs/*.json`
  (`pipeline/cron_run_log.py`, write-only, fail-open) — timing, `asof`, `mode`, per-thesis
  `withheld_reason`/`edgar_fetches`/counts. `edgar_fetches` is the **freeze detector**: it counts network
  *attempts* (a frozen cache reaches out 0 times; a healthy run, thousands), so a freeze is visible in the log
  instead of hiding behind a plausible quiet night. `already_ran_live(asof, run_at, tz)` reads these logs
  (mode==`live`, **started at/after that night's `RUN_AT`** in market time) to decide whether the night
  already ran — the basis of R6 catch-up.
- **The health pager (R4, #199).** `assess_health` emits a `HealthEvent` through the notify seam
  (Slack via `SLACK_WEBHOOK_URL`, **fail-open**; `LogNotifier` otherwise) when a run is a **FREEZE**
  (`frozen = allow_live and theses > 0 and edgar_fetches == 0`), has **withheld** calls, or has **thesis
  errors**. A healthy run returns `None` — silent (loudness marks the exception). This is the page R1 lacked:
  the platform now notices its own blindness. *(Known false-positive path — see "Known gaps".)*
- **Per-thesis isolation.** Each thesis's ingest and call each run in their own try; one thesis's failure is
  captured into its `ThesisRunResult` and skipped — **never fatal** to the run (the cron finishes the rest).
- **No-lookahead.** `asof = today`, `known_at = now` (`PointInTimeData` defaults `None → now`); never backdated.
- **Option B intact.** The cron ingests **FACTS** and appends the **call-of-record** (the write-only
  accountability log). It builds **NO read-serving signal/score cache** — calls still re-derive on read. The
  call-of-record log is never read back to serve (`INVARIANTS.md` #6; `DATA_FLOW.md`).
- **Scoreboard-ready, not coupled.** One clean versioned row per (thesis, day); same-day re-runs collapse via
  `calls_repo.latest_for_thesis`'s `DISTINCT ON (asof)`. That is exactly what the built Scoreboard reads —
  with zero Scoreboard code in the cron.

### `record_if_changed` + `_canonical` — idempotent append to an immutable log

The `calls` log is **immutable** (a `no_update` trigger) and its `(thesis_id, asof)` index is **non-unique**,
so an UPSERT is impossible. `record_if_changed(conn, card, tenant_id)` therefore **reads-compares-then-
conditionally-appends**: it finds today's latest call-of-record for `(thesis, card.asof)` and appends a new
versioned row **only if none exists yet or the latest differs in substance**. A same-day re-run on unchanged
facts appends **nothing**; a genuine change (Incubating→Warming→Armed, `confidence` [setup strength] /
`exit_by` [signal-validity horizon] / provenance / members) appends **exactly one** new row
(latest-append-per-asof wins on read).

`_canonical(card)` is the substance compare: it serializes the CallCard order-INDEPENDENTLY (recursively
**sorts dict keys AND list elements**) and **rounds floats**, so a pure reorder of an unordered card list
(triggers / members / a member's own triggers / provenance) or jsonb/IEEE repr noise can't **flap** it into a
false re-append. (The CallCard has no wall-clock field, so the no-change case is deterministic.)

## The scheduling sidecar — the `cron` service + `scripts/daily_cron.sh`  `[BUILT #73]`

The CLI is the **unit of work**; the sidecar is a **dumb trigger**.

- **On by default.** The `cron` service comes up with the normal `docker compose up` (no profile flag), so the
  deployed stack notifies itself with no extra command to remember. Skip it for one run with
  `docker compose up -d --scale cron=0`. *(Local dev uses `infra/docker-compose.yml` — DB only — and tests use
  pytest, so neither starts it.)* `restart: unless-stopped` — see the missing-sidecar gap in "Known gaps".
- **A sleep-loop, not a cron daemon (deliberate).** `backend/scripts/daily_cron.sh` waits until `RUN_AT` in
  the container's `TZ` — in **short slices (`SLICE_S`, default 60 s), re-reading the wall clock between
  them** rather than one long `sleep` (why: two bullets down) — skips weekends (markets closed → an
  idempotent no-op + a needless API hit), fires `python -m pipeline.daily --asof <target>`, and a failed run
  never kills the loop. It is **not Dagster, not
  APScheduler, not a cron daemon** — chosen because a sleep-loop **inherits the container env directly** (so
  the space-bearing `ALPHADECK_USER_AGENT` isn't mangled by a cron-style env snapshot) and honors `TZ` via
  `date`. Trivially swappable to real cron / supercronic later (the contract is "fire the CLI once a day").
- **It fires for the INTENDED night — the target as-of is fixed at schedule time (2026-09-09).** On a laptop
  a single long `sleep` overshot by however long the host was suspended (MEASURED late fires at 23:37 / 23:43
  / 23:49 ET, one at 00:24 the next day, one at 09:09 the next morning). The loop used to let the CLI default `asof`
  to the day it *woke*, so a fire past midnight recorded the NEXT day's as-of and the intended night got no
  call-of-record at all — 6 of 13 weekdays since 2026-08-24 had zero `calls` rows. Now the loop captures
  `target=$(date -d "@$next" +%F)` when it schedules, gates the weekday check on the **target** (a Friday
  target that wakes on Saturday still runs), passes `--asof "$target"`, logs the overshoot in minutes when a
  wake is ≥5 min late, and then **catches up the weekdays the sleep also skipped**: every weekday `d` with
  `target < d <= last_expected_asof(after)` — `after` being the clock **when the scheduled run finishes**,
  not the wake — gets `python -m pipeline.daily --catch-up --asof "$d"` (a no-op when a live pass for that
  as-of already ran). The window closes after the run on purpose: a live run takes 7–15 min, so a wake
  shortly before the next `RUN_AT` whose run crosses it (target Tue, wake Wed 22:20, run ends 22:35) would
  otherwise lose Wed outright — a wake-anchored window did not include it yet, and the loop then re-anchored
  `next` to Thu. Bounded by construction — it walks *forward* from the target
  it just fired for, never backwards, so a deploy can never silently backfill old holes. The nightly backup
  still runs once per wake. The shell's `is_weekday` / `next_weekday` / `last_expected_asof` mirror
  `pipeline/schedule.py` — keep the two in step. A catch-up runs inside the EDGAR cache's 12h TTL and
  legitimately fetches ~0, so the CLI's `--catch-up` skips the R4 freeze page for that pass only (withheld /
  errored still page) and the run artifact carries `catch_up: true` (the Admin history tags the row).
- **It waits in slices and re-checks the WALL clock — the lateness itself (2026-09-10).** `sleep` counts the
  monotonic clock, and on Docker Desktop / WSL2 the VM's monotonic clock does not advance while the host is
  suspended, so one long `sleep "$((next - now))"` overshot by exactly the suspended interval — even on a host
  wide awake at `RUN_AT`. MEASURED on prod 2026-09-10: the sidecar booted 13:47 ET and computed `sleep 31376`
  for 22:30; at 22:33 ET the wall clock had advanced 31,569 s since boot, the container's monotonic clock
  28,688 s, and the sleep still had 2,707 s left (~48 min of daytime suspend) — the pass fired ~23:18. Now
  `wait_until "$next"` sleeps at most `SLICE_S` (default 60 s, env-tunable) at a time and re-reads `date +%s`
  between slices (a remaining time ≤ 0 breaks out, so a clock jump can never produce a negative sleep; no
  per-slice log line): a host awake at `RUN_AT` fires at `RUN_AT`, and a host suspended *across* `RUN_AT`
  fires within a minute of resuming. The fixed target, the ≥5-min LATE WAKE line (which now measures the
  suspend itself) and the forward-only late-wake catch-up above all still apply to that spanning-suspend case.
- **Explicit TZ.** `TZ=America/New_York` (overridable) + `RUN_AT=22:30` (after the US close + EOD settle);
  `tzdata` is installed in the image (the slim base ships no zoneinfo, so an explicit TZ would silently fall
  back to UTC). **Never the container's default UTC.** *(The BACKEND container's `TZ` is pinned to match, #202,
  so a manual `docker exec … pipeline.daily` agrees on "today" — a container-scoped stopgap; the durable fix,
  a shared trading-day helper, is an open item — see `INVARIANTS.md`.)*
- **Catch-up on boot (R6, #200 — widened to the LAST EXPECTED night, 2026-09-10).** On boot the sidecar
  fires **one** `python -m pipeline.daily --catch-up --asof <last_expected_asof(now)>` — the most recent
  as-of whose scheduled run should *already* have fired (today once `RUN_AT` has passed on a weekday, else
  the most recent prior weekday; the shell's `last_expected_asof` mirrors `schedule.last_expected_asof`).
  Unconditional on boot; the CLI's guard is what makes it a no-op: `already_ran_live(asof, run_at, tz)` reads
  the R3 logs and counts a live pass **only if it started at/after that night's `RUN_AT` in market time**
  (`datetime.combine(asof, run_at, tz)`, compared as aware datetimes; `main` supplies `RUN_AT` + the market
  zone from config, the guard itself is pure). Both rules come from one measured night. On **2026-09-09** the
  prod host was OFF at the 22:30 run, rebooted 03:34, and Docker (the sidecar) came back at **13:47 on Sep
  10**: the old block fired only when a boot was *past today's* `RUN_AT`, so a 13:47 boot did nothing and the
  loop re-anchored to Sep 10 — Sep 9 was never attempted; and two pre-open "Run daily now" passes for as-of
  Sep 9 (09:09 / 09:15 ET, on Sep 8's bars) satisfied the old any-live-pass guard, so even a wider catch-up
  would have been a no-op — Sep 9's record stayed a pre-open call. Now a next-day boot catches last night up,
  and a pre-close manual pass never masks the missed post-close pass (a late-wake or boot catch-up the next
  morning started *after* the cutoff and does count — a `--catch-up` pass is a live pass). A `--no-live` run
  never satisfies the guard (mode-filtered), and an artifact with a missing / unparseable / naive
  `started_at` is skipped — fail-open toward *running*, never toward a silent skip. The CLI's
  `--asof YYYY-MM-DD` still allows a manual re-run (idempotent). Bounded to **exactly one night** by
  construction — never older holes; a deploy must never silently backfill history (the operator's tool for a
  hole is `pipeline.backfill`, below; the in-loop late-wake catch-up above is bounded to the sleep that just
  ended for the same reason). *(What this does NOT cover: a sidecar that never boots — see "Known gaps".)*
- No `ANTHROPIC_API_KEY` (the ingest + call engine are deterministic — no LLM on this path).

### Backfilling a missed night — `pipeline.backfill`  `[BUILT]`

When a night has no call-of-record (`/admin/status` says `gappy`; `schedule.missed_asofs` lists it),
`python -m pipeline.backfill --asof <night> --known-at <pin>` reconstructs it: the SAME `call_for_thesis`
assembly the cron runs, with the transaction clock PINNED, then `record_if_changed`. `--dry-run` first.

- **Why a pin, not `pipeline.daily --asof <past>`.** The `calls` log is immutable and bitemporal — a row's
  `recorded_at` is always now — and the as-of gate is two-axis (`valid_from <= asof` AND `recorded_at <=
  known_at`, `INVARIANTS.md` #4). A naive re-run computes the past night with `known_at = now`: TODAY's
  knowledge, "what the platform says now about that night", not what it would have logged. MEASURED on dev:
  Modern Defense backfilled as ARMED on Aug 24–31 while the real nightly runs around those nights recorded
  INCUBATING, because that thesis's facts arrived after them. The pinned backfill records what the cron WOULD
  have logged, consistent with its recorded neighbors.
- **The pin — `--known-at next-run`** resolves to the `finished_at` of the FIRST live run after the night
  (`resolve_next_run_known_at`, pure over the R3 run artifacts; `no-live` runs and runs started ON the as-of
  day do not count, a `--catch-up` pass does). That run ingested the night's own EOD bar and its filings and
  nothing that arrived later — a pin at 22:30 THAT night would MISS the night's bar, because the next
  morning's run is what ingested it. An explicit ISO instant WITH an offset or `Z` is accepted instead; a
  naive one is refused (a wrong zone shifts real answers invisibly), as is a pin before the as-of day begins
  and an `--asof` that is not in the past (tonight is the cron's job). The resolved pin prints in UTC and
  market time before anything runs.
- **It never ingests or notifies.** No refresh legs, no SPAC legs, no `EdgarClient` / `PriceSource`, no
  `TransitionEvent` (a reconstructed row is a RECORD, never a nag — a Slack "ARMED" for a night two weeks ago
  is exactly the wrong loudness, #7). A pure recompute-and-record over facts already in the store, pinned
  structurally by an import-guard test. Per-thesis isolation is `run_daily`'s (own try, commit / rollback).
- **The three axes of a reconstruction — only one is faithful.** (1) *The clock* — pinned, faithful. (2)
  *Thesis existence* — ENFORCED: a thesis created after the night was not in that night's cron, so it
  gets NO row. `run_backfill` skips it (`thesis_existed_on`: `thesis.created_at` in market time vs the
  as-of day — a thesis created during the day of `asof` WAS in that night's run) and reports the skip
  loudly: its own line + count in the summary, in `--dry-run`, and in the provenance artifact (`skipped`
  per thesis + a summary count). MEASURED before the gate existed (dev copy of the 2026-09-09 backfill):
  37 of the 144 reconstructed rows predate their thesis, 20 of them warming/armed, two of them arm
  episodes for a thesis that did not exist. (3) *Basket composition* — NOT reconstructable:
  `basket_member` is full-replace with no timestamps, so every reconstruction ran on TODAY's roster. A
  reconstructed row therefore can never be shown honest, and the Scoreboard scores none of them
  (`docs/SCOREBOARD.md` §"The one rule" — a reconstructed row never defines an episode boundary; the
  ledger names the excluded nights once). Until baskets are point-in-time, that holds for every row this
  tool writes.
- **The markers.** Every row the backfill writes carries `calls.reconstructed = true` (migration 0042; the
  cron never sets it) — the explicit provenance the Scoreboard's record path filters on, threaded to
  `record_if_changed` OFF the card (so it never fakes a change in the idempotency compare). `ingest_fresh`
  / `ingest_errors` stay NULL — there was no ingest, so NULL is the honest stamp. 0042 stamped the legacy
  rows by the exact derived rule (`ingest_fresh IS NULL AND (recorded_at AT TIME ZONE 'UTC')::date - asof
  > 1`: MEASURED on dev, every backfill row landed >= 5 days late and every honest NULL-stamp row at lag
  <= 1) with a count assertion, the `no_update` trigger disabled for that ONE statement inside the
  migration's single transaction (the flag is provenance; nothing the call says is rewritten).
- **Repairing the pre-creation rows — `pipeline.repair_reconstructed_precreation`.** Deletes the
  reconstructed rows dated before their thesis existed — the SAME `thesis_existed_on` rule, imported, so
  the two tools cannot disagree. Dry-run is the DEFAULT and prints every row (thesis, asof, state /
  verdict, the creation date in market time); `--apply` deletes and prints per-thesis counts;
  `DATABASE_URL` must be explicit (no dev-default for a destructive tool). The operator runs it on prod
  after a backup and after re-running the classification query there (the pins differ per stack) — never
  an agent. Reconstructed rows for theses that DID exist are left alone: reported-not-scored, and the
  evidence the backfill happened.
- **Provenance + idempotency.** One write-only, fail-open JSON per invocation under
  `data/backfills/<utc-ts>.json` (`pipeline/backfill_log.py`: `asof`, `known_at`, `known_at_policy`
  `explicit` | `next-run`, per-thesis state / verdict / recorded / error). NOT a cron run artifact —
  `already_ran_live` stays False for the night. A `--dry-run` writes NOTHING (no row, no artifact). A
  re-run with the same `asof` + `known_at` appends zero rows (count the table); a different pin that sees
  different facts is a genuine change and appends one versioned row.

## Backups & restore (Slice 4)  `[BUILT]`

The 2026-07-21 truncation cost the whole demo DB; recovery only worked because an **ad-hoc** `pg_dump`
happened to exist. So a snapshot is now a one-click safety net — and a nightly one.

- **Create + list, from the Admin page.** `POST /admin/backup` kicks a background job (202 → poll
  `GET /admin/backup/jobs/{job_id}`, single-slot 409 guard) that shells `pg_dump` (**read-only** — the runner
  opens no app connection and mutates no row) to `./data/backups/alphadeck-<UTC>[-<label>].sql`;
  `GET /admin/backups` lists them newest-first, and `/admin/status` carries the last-snapshot age. The trigger
  is **operator-initiated only** (never on load/mount/poll — the same cost-thread as "Run daily now"); the
  reads may poll. The runner + registry mirror the daily ones (`pipeline/backup.py` + `pipeline/backup_job.py`,
  peers of `daily.py` + `daily_job.py`).
- **Nightly, in the cron sidecar.** `scripts/daily_cron.sh` runs `python -m pipeline.backup` right after the
  scheduled `pipeline.daily` (weekday branch), fail-open (a failed backup never kills the loop). It is
  deliberately **not** folded into `run_daily_pass`, so a manual "Run daily now" does not also dump.
- **Retention = keep-last-N, labeled EXEMPT.** After a *successful* dump the newest `ALPHADECK_BACKUP_KEEP`
  (default **7**) UNLABELED snapshots are kept and older unlabeled ones pruned (each prune logged); a
  **labeled** dump (created with a `label`, e.g. `pre-migration`) is never auto-deleted. A **failed** dump
  never prunes (it must not shrink the safety net), and the atomic `tmp → os.replace` means a crashed dump
  never lists.
- **The host bind is on BOTH services.** `./data/backups:/data/backups` (writable, the `scoreboard_replay`
  idiom minus `:ro`) is bound on `backend` (the button + list) **and** `cron` (the nightly dump), so the two
  share ONE host directory — host-accessible so a dump is copyable off-box. `pg_dump` comes from
  `postgresql-client-16` (PGDG apt repo, matching server 16 — Debian's default client lags and refuses a newer
  server).
- **RESTORE is CLI-only — never a button.** A restore is destructive (drop-schema + reload) and belongs in
  human hands. The documented sequence (the one used on 2026-07-21):
  `docker exec -i alphadeck-postgres-1 psql -U alphadeck -d alphadeck < ./data/backups/<file>`. *(The
  replay-snapshot regenerate button stays out of scope — deferred to the replay-panel work.)*

## Known gaps (as of 2026-09-10)

Recorded here where a builder of the pager/scheduler will hit them; the full account of the first three is in
`POSTMORTEM_CRON_FREEZE_2026-07.md`.

- **The wrong-day fire — CLOSED (A + C, 2026-09-09), recorded so the shape is recognizable.** The sidecar's
  `sleep` overshoots by however long the laptop was suspended (MEASURED: fires at 23:37 / 23:43 / 23:49 ET,
  00:24 and 09:09 the *next* day — the log literally showed `next run Tue Sep 8 22:30` followed by `Wed Sep 9
  09:09:42 — running pipeline.daily`). The CLI defaulted `asof` to `market_today()` at *fire* time, so a
  post-midnight fire recorded the NEXT day and the intended night got nothing; a Friday target that woke on
  Saturday was skipped outright by the wake-day weekday check. MEASURED on prod: **6 of the 13 weekdays since
  2026-08-24 had zero `calls` rows** (Aug 24, 26, 28, 31, Sep 4, Sep 8) — and the Admin freshness read said
  `current — no scheduled run is missing` over every hole, because `expected_runs_behind` compares only
  `MAX(asof)` against the last expected run: a wrong-day fire *advances the edge right over the night it
  skipped*. R6 could not help (a wake is not a boot) and R4 pages only from inside a run (a wrong-day run is a
  healthy run). **A** (the sidecar) fixes the cause: the target as-of is fixed at schedule time and passed as
  `--asof`, the weekday gate is on the target, and the weekdays a long sleep also skipped are caught up
  forward-only. **C** (the freshness read) makes the shape visible: `/admin/status` scans the last
  `ALPHADECK_ADMIN_MISSED_WINDOW` (default 10) scheduled weekdays for nights with no call-of-record
  (`schedule.missed_asofs`, bounded to the record's own span) and reports `gappy` + the dates. The existing
  prod holes are **not** backfilled by either (deliberate — the operator's call, and the boot catch-up is
  bounded to the *last expected* night, never older); the operator's tool for a hole is `pipeline.backfill`
  with a PINNED `known_at` ("Backfilling a missed night", above) — never `pipeline.daily --asof <past>`,
  which records today's knowledge.
- **The R4 freeze page's false-positive path is NARROWED, not removed.** It fires on `edgar_fetches == 0`, but
  ~0 is *also* what a correct run entirely inside the 12h EDGAR TTL looks like (all cache hits). The
  **nightly** cron is safe — always ~24h out, always past the TTL, always fetches in the thousands. Option B
  from the original note **shipped for catch-ups** (2026-09-09): a `--catch-up` pass runs with
  `assess_health(freeze_check=False)` — quiet on fetch count, still paging on withheld / errors — and its
  artifact carries `catch_up: true` so the Admin history re-derives the same verdict. Still exposed: **manual
  re-runs and any second scheduled run in a night** (a hand-run `python -m pipeline.daily` or the Admin "Run
  daily now" right after the nightly). Option A (page on 0 only when the cache was *outside* its TTL for the
  names touched) remains the more correct fix when built.
- **`edgar_fetches` counts ATTEMPTS, not successes.** `EdgarClient.get_text` does `live_fetches += 1`
  immediately *before* calling `_fetch`, so a pull that RAISES still increments the counter. A run whose every
  fetch fails therefore reports a large, reassuring number, and `frozen` (which trips only at exactly 0) can
  never fire — the freeze detector reports *intent to fetch*, not network. Observed **2026-07-22**: an empty
  `ALPHADECK_USER_AGENT` (the stack had been rebuilt from a git worktree, where the gitignored `.env` does not
  exist, so compose resolved `${ALPHADECK_USER_AGENT:-}` to empty) raised on all 255 pulls before a byte left
  the box — and the run logged `255 EDGAR fetches`, `frozen = false`. What actually caught it was the **R2
  withhold guard** (every name errored → *total ingest failure* → 5 calls correctly withheld), not the detector
  built for exactly this. Fix when built: count *completions*, or carry a failure tally beside the attempt
  count, and let the freeze verdict read the former. Note this gap **compounds** with the false-positive above —
  the same number is unreliable in both directions.
- **A missing run doesn't page (dead-man's switch) — STILL OPEN.** `restart: unless-stopped` restarts the
  sidecar on crash/daemon-restart but **NOT** after a deliberate `docker compose stop`. R4 fires from *inside*
  a run, so a run that never happens produces no results → no run log → no page — byte-identical to a healthy
  silent night. R6 covers the crash/reboot case and the late-wake catch-up (A) covers a sidecar that sleeps
  through nights and *eventually wakes*; the hole-aware read (C) at least makes the absence **visible** on the
  Admin page (`gappy` / `stale`) — but only when someone looks. **Narrowed 2026-09-10:** the boot catch-up
  now targets the *last expected* night and the guard only credits a pass started at/after that night's
  `RUN_AT`, so a sidecar that boots the *next day* (the Sep 9 case: host off at 22:30, Docker back at 13:47)
  catches last night up even when a pre-open manual pass ran. A *persistent* absence — a host that stays off,
  a sidecar that never boots — still produces no boot and no run, and still needs an **external** heartbeat
  that alerts when the night's run log is missing past a deadline (the sidecar can't page about its own
  absence). Unchanged, still open.

## The count-the-table idempotency discipline (the load-bearing test pattern)

The bitemporal read **dedups** (`SELECT DISTINCT ON (natural-key) … recorded_at DESC`), so a duplicate append
**hides behind a correct read** while the table silently grows. Therefore the idempotency tests **count the
table** (`count(*)` / `list_for_thesis` length) before and after a re-run and assert it did **not grow** — a
read-based assertion would pass even as the store bloated. This is the load-bearing pattern across M2a (the
fact tables) and M2b (the calls log): `test_rerun_appends_zero_rows_count_the_table`,
`test_daily_idempotent_end_to_end_count_the_table`, and the `record_if_changed` skip-identical test. See
`CLAUDE.md` (conventions).

## What this subsystem is NOT (deferred / parked)

- **The restatement re-version** `[DEFERRED]` — re-storing a stored bar when a fresh pull's value differs
  (Yahoo re-bases the whole history on a split, and the incremental `d > last` guard never refreshes old
  bars → a thesis living across a FUTURE split accumulates mixed-basis bars). Parked behind the
  **source-strategy decision** (keep Yahoo + re-version, vs move to a raw+splits source and own the adjustment
  at read time — which dissolves it). Safe for the MVP: the seeded names have no splits, and a fresh thesis
  pulls a continuous whole-history-adjusted series. Detail + the two paths: `DATA_SOURCES.md`.
  - *The build spec, when it lands (generic — no split math):* on each pull, compare the fresh value for each
    already-stored date against the latest stored version; **if it differs beyond a stable float tolerance, append
    the fresh value as a NEW version** (`append_fact`, `recorded_at=now`) — idempotent when nothing differs. This
    is the same bitemporal shape as any restatement: a past as-of read (`known_at` < the re-version time) still
    sees the ORIGINAL bars (the new version's `recorded_at` is filtered out), the live read sees the corrected
    series via `recorded_at DESC` — **no backward leak.** **Two load-bearing traps:** (1) it must **override the
    `d > last` skip** for the differing stored dates, or they never refresh; (2) it must override that skip **only
    when the value differs** — else a no-split re-run re-appends the same bars daily and the table grows forever
    (the count-the-table failure on this path). Compare at a stable precision so a re-pull's float noise doesn't
    fake a difference. No `fact_price_eod` schema change; the replay/Parquet PIT's split fidelity is separate.
- **The Scoreboard** `[BUILT]` (v1) — the forward trust loop's instrument over this record: the episode
  ledger + the operator track. The `n ≥ 5` aggregate UI gate suppresses tiny summaries; it is a presentation
  safeguard, not an evidence threshold. Forward calibration arrives only as a materially useful record grows.
  `docs/SCOREBOARD.md`; `ROADMAP.md`.
- **Scaling the cron** `[FILED]` — at today's scale the cron ingests every thesis daily. As theses
  accumulate, decouple "record the call-of-record for ALL theses" (cheap — keep) from "ingest ALL theses
  daily" (expensive — live pulls): ingest **active** theses daily, dormant ones less often. A post-MVP
  scaling refinement, not needed now.
