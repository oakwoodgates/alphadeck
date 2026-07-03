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
> fresh-data fix + the price-source seam (#72), the scheduling sidecar (#73). With it, **M2 — "the functional
> platform feeds itself" — is complete**, and the North Star is reachable end to end: create a thesis (M1) →
> `ingest_thesis` pulls real insider + price → it WARMS/ARMS on real data → the daily cron logs the
> call-of-record.
>
> **Trust caveat (load-bearing): "feeds itself" is NOT "validated forward."** This arc is platform PLUMBING,
> not the call engine. It did not change the trust validation — still in-sample (n=19; see `ROADMAP.md`'s "Keep
> the trust state honest" box); the forward trust loop (the **Scoreboard**) stays PARKED. The daily call-of-record is the forward
> RECORD the Scoreboard will later track — built **Scoreboard-ready, not Scoreboard-coupled**.
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
  adapters over the cache-first fetchers. `ingest_thesis._price_leg` depends on the **interface**.
- **The contract is "a source of EOD bars," not "Yahoo's adjusted bars."** Deliberately **no `get_splits`**
  yet: owning the split adjustment ourselves (adjusting at read time from raw bars) is a larger storage+read
  change that would EXTEND this interface if/when we adopt such a source — the seam eases that swap, it does
  not pre-build it. (Today's Yahoo bars are already split-adjusted + re-based on every split — a property of
  the Yahoo adapter, documented in `DATA_SOURCES.md`, not baked into the contract.)
- **The modularity template.** This is the pattern the other sources (EDGAR/Form 4) can follow when they need
  the same swappability; this slice set it for prices (the source that was biting), not for everything.

## Fresh data — cache-first, force-refresh on the recurring path  `[BUILT #72]`

`fetch_eod`/`fetch_csv` are **cache-first**: a cache hit returns the stored bars and never re-pulls. That is
right for dev / `--no-live` (reproducible, polite), but **wrong for the daily cron** — a cache hit would
return **stale** bars every run, so the cron would never see a new day (the "feeds itself" promise would be
latent). The fix: **`force_refresh`** (meaningful only WITH `allow_live`) bypasses a cache hit to re-pull live
and **overwrite** the cache. The recurring/daily path sets it; the dev/`--no-live` path leaves it off and
stays cache-first; a cache MISS always fetches (a new name's first ingest is fresh regardless).

## The daily cron — `pipeline/daily.py`  `[BUILT #71]`

`run_daily(conn, *, asof=today, known_at=now, allow_live=True, force_refresh=True, …)`. CLI: `python -m
pipeline.daily`. For **each** thesis (`thesis_repo.list_all` — tenant intrinsic per-thesis):

1. **Refresh facts** — `ingest_thesis` (incremental + fail-visible; `force_refresh=True`, the recurring path).
2. **Assemble TODAY's call WITHOUT writing** — `call_for_thesis(asof=today, known_at=now, record=False)`.
3. **Append the call-of-record ONLY if it changed** — `calls_repo.record_if_changed`.

- **Per-thesis isolation.** Each thesis's ingest and call each run in their own try; one thesis's failure is
  captured into its `ThesisRunResult` and skipped — **never fatal** to the run (the cron finishes the rest).
- **No-lookahead.** `asof = today`, `known_at = now` (`PointInTimeData` defaults `None → now`); never backdated.
- **Option B intact.** The cron ingests **FACTS** and appends the **call-of-record** (the write-only
  accountability log). It builds **NO read-serving signal/score cache** — calls still re-derive on read. The
  call-of-record log is never read back to serve (`INVARIANTS.md` #6; `DATA_FLOW.md`).
- **Scoreboard-ready, not coupled.** One clean versioned row per (thesis, day); same-day re-runs collapse via
  `calls_repo.latest_for_thesis`'s `DISTINCT ON (asof)`. That is exactly what the future Scoreboard reads —
  with zero Scoreboard code in the cron.

### `record_if_changed` + `_canonical` — idempotent append to an immutable log

The `calls` log is **immutable** (a `no_update` trigger) and its `(thesis_id, asof)` index is **non-unique**,
so an UPSERT is impossible. `record_if_changed(conn, card, tenant_id)` therefore **reads-compares-then-
conditionally-appends**: it finds today's latest call-of-record for `(thesis, card.asof)` and appends a new
versioned row **only if none exists yet or the latest differs in substance**. A same-day re-run on unchanged
facts appends **nothing**; a genuine change (Incubating→Warming→Armed, confidence / exit_by / provenance /
members) appends **exactly one** new row (latest-append-per-asof wins on read).

`_canonical(card)` is the substance compare: it serializes the CallCard order-INDEPENDENTLY (recursively
**sorts dict keys AND list elements**) and **rounds floats**, so a pure reorder of an unordered card list
(triggers / members / a member's own triggers / provenance) or jsonb/IEEE repr noise can't **flap** it into a
false re-append. (The CallCard has no wall-clock field, so the no-change case is deterministic.)

## The scheduling sidecar — `docker-compose` `cron` profile + `scripts/daily_cron.sh`  `[BUILT #73]`

The CLI is the **unit of work**; the sidecar is a **dumb trigger**.

- **Disabled by default.** The `cron` service is gated behind a compose **profile** (`profiles: [cron]`), so
  `docker compose up` / local dev / tests **never** fire it. Run it with `docker compose --profile cron up`.
- **A sleep-loop, not a cron daemon (deliberate).** `backend/scripts/daily_cron.sh` sleeps until `RUN_AT` in
  the container's `TZ`, skips weekends (markets closed → an idempotent no-op + a needless API hit), fires
  `python -m pipeline.daily`, and a failed run never kills the loop. It is **not Dagster, not APScheduler, not
  a cron daemon** — chosen because a sleep-loop **inherits the container env directly** (so the space-bearing
  `ALPHADECK_USER_AGENT` isn't mangled by a cron-style env snapshot) and honors `TZ` via `date`. Trivially
  swappable to real cron / supercronic later (the contract is "fire the CLI once a day").
- **Explicit TZ.** `TZ=America/New_York` (overridable) + `RUN_AT=22:30` (after the US close + EOD settle);
  `tzdata` is installed in the image (the slim base ships no zoneinfo, so an explicit TZ would silently fall
  back to UTC). **Never the container's default UTC.**
- **No auto-catch-up.** A missed day is not back-filled automatically; the CLI's `--asof YYYY-MM-DD` allows a
  manual backfill (and re-running is safe — idempotent).
- No `ANTHROPIC_API_KEY` (the ingest + call engine are deterministic — no LLM on this path).

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
- **The Scoreboard** `[PARKED]` — the forward trust loop. The daily call-of-record is its forward record;
  building it is the post-MVP step that earns forward trust (and drives the second, out-of-sample
  recalibration). `ROADMAP.md`.
- **Scaling the cron** `[FILED]` — at today's scale the cron ingests every thesis daily. As theses
  accumulate, decouple "record the call-of-record for ALL theses" (cheap — keep) from "ingest ALL theses
  daily" (expensive — live pulls): ingest **active** theses daily, dormant ones less often. A post-MVP
  scaling refinement, not needed now.
