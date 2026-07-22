# FEED_LOOP.md — how the platform feeds itself (the back-half ingest + the daily call-of-record cron)

> Repo path: `docs/FEED_LOOP.md`. The back half's **data loop**: how a thesis's basket gets the CALL-ENGINE
> facts (insider Form 4 + price EOD) it needs to WARM/ARM, and how a daily cron refreshes them and appends the
> day's **call-of-record** per thesis — so the platform *feeds itself*. This is the M2 subsystem (the MVP's
> second half). Companion to `CHAIN_DRAFTER.md` (the front door — narrative → chain), `CALL_LOGIC.md` (what the
> facts ARM into), `DATA_SOURCES.md` (the price/EDGAR sources + the Yahoo split-adjustment finding),
> `DATA_FLOW.md` (where data lives), `INVARIANTS.md` (#1 no-lookahead, #2 exact membership, the calls log).
> Engines: `backend/pipeline/ingest_thesis.py` · `backend/pipeline/daily.py` · `backend/ingest/prices/source.py`
> · `backend/repositories/calls_repo.py` (`record_if_changed` / `_canonical`) · the `cron` sidecar in
> `docker-compose.yml` + `backend/scripts/daily_cron.sh`.
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

`ingest_thesis(conn, thesis_id, *, allow_live, force_refresh, user_agent, price_source)` ingests insider +
price facts for each **resolved** basket member. CLI: `python -m pipeline.ingest_thesis --thesis <id>`.

- **Exact membership (#2).** It loops the thesis's basket and resolves each member by its already-resolved
  `security_id` via **`master.get`** (issuer ticker + CIK) — **never a fresh fuzzy resolve.** An unresolved
  member (`security_id` is null) is skipped; a placed id not in the tenant's master is reported, not guessed.
- **Two legs, each fail-visible.** The Form 4 leg and the price leg each run in **their own try**, committing
  on success and rolling back on failure, so one leg's error never discards the other's work and never aborts
  the run — the error is captured into the name's `NameResult` (improving on the scanner's bare `except:
  pass`). One bad name never stops the rest.
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
  instead of hiding behind a plausible quiet night. `already_ran_live(asof)` reads these logs (mode==`live`)
  to decide whether a day already ran — the basis of R6 catch-up.
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
- **A sleep-loop, not a cron daemon (deliberate).** `backend/scripts/daily_cron.sh` sleeps until `RUN_AT` in
  the container's `TZ`, skips weekends (markets closed → an idempotent no-op + a needless API hit), fires
  `python -m pipeline.daily`, and a failed run never kills the loop. It is **not Dagster, not APScheduler, not
  a cron daemon** — chosen because a sleep-loop **inherits the container env directly** (so the space-bearing
  `ALPHADECK_USER_AGENT` isn't mangled by a cron-style env snapshot) and honors `TZ` via `date`. Trivially
  swappable to real cron / supercronic later (the contract is "fire the CLI once a day").
- **Explicit TZ.** `TZ=America/New_York` (overridable) + `RUN_AT=22:30` (after the US close + EOD settle);
  `tzdata` is installed in the image (the slim base ships no zoneinfo, so an explicit TZ would silently fall
  back to UTC). **Never the container's default UTC.** *(The BACKEND container's `TZ` is pinned to match, #202,
  so a manual `docker exec … pipeline.daily` agrees on "today" — a container-scoped stopgap; the durable fix,
  a shared trading-day helper, is an open item — see `INVARIANTS.md`.)*
- **Catch-up on boot (R6, #200).** When the sidecar boots **past** today's `RUN_AT` on a weekday it fires
  `python -m pipeline.daily --catch-up`, which is a **no-op if a live pass already ran** for that `asof`
  (`already_ran_live`, reading the R3 logs). So a crash/redeploy after the scheduled time re-fires the missed
  run instead of waiting a full day. A `--no-live` run never satisfies the guard (mode-filtered), so it can't
  suppress a real catch-up. The CLI's `--asof YYYY-MM-DD` still allows a manual backfill (re-running is safe —
  idempotent). *(What this does NOT cover: a sidecar that never boots — see "Known gaps".)*
- No `ANTHROPIC_API_KEY` (the ingest + call engine are deterministic — no LLM on this path).

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

## Known gaps (as of 2026-07-17)

Two are recorded here where a builder of the pager/scheduler will hit them; the full account of each is in
`POSTMORTEM_CRON_FREEZE_2026-07.md`.

- **The R4 freeze page has a false-positive path.** It fires on `edgar_fetches == 0`, but ~0 is *also* what a
  correct run entirely inside the 12h EDGAR TTL looks like (all cache hits). The **nightly** cron is safe —
  always ~24h out, always past the TTL, always fetches in the thousands. What's exposed is **catch-ups, manual
  re-runs, and any second run in a night** — the paths R6 made routine (the 07-17 recovery run recorded
  `edgar_fetches = 1`, one short of a false page). Two fixes when built: (A, more correct) page on 0 only when
  the cache was *outside* its TTL for the names touched; (B, simpler) restrict the freeze page to *scheduled*
  runs (catch-ups/manual stay quiet on fetch count, still page on withheld/errors).
- **A missing run doesn't page (dead-man's switch).** `restart: unless-stopped` restarts the sidecar on
  crash/daemon-restart but **NOT** after a deliberate `docker compose stop`. R4 fires from *inside* a run, so a
  run that never happens produces no results → no run log → no page — byte-identical to a healthy silent night.
  R6 covers the crash/reboot case; a *persistent* absence needs an **external** heartbeat that alerts when
  today's run log is missing past a deadline (the sidecar can't page about its own absence).

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
