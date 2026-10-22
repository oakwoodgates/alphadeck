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
> reconstructed with a PINNED `known_at`) · `backend/pipeline/tape_health.py` (the price-tape recency
> monitor — a monitor, never a signal) · `backend/pipeline/split_basis.py` (the split-basis defect monitor,
> T4 — likewise flag only).
>
> **Status: BUILT** — the per-thesis ingest (PR #70), the daily cron + `record_if_changed` (#71), the
> fresh-data fix + the price-source seam (#72), the scheduling sidecar (#73), and the **cron-freeze
> remediation** (#196–#200): the key-classed EDGAR cache TTL, a recording gate, a run-of-record log, a health
> pager, and catch-up-on-boot; and the **missed-nights arc** (#330–#332, #337–#338): the sidecar fires for
> the INTENDED night and waits in wall-clock slices, the freshness read is hole-aware (`gappy`), and a missed
> night is reconstructed with a PINNED clock and marked `calls.reconstructed` (reported on the Scoreboard,
> never scored). With it, **M2 — "the functional platform feeds itself" — is complete**, and the
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
- **Politeness.** Every SEC request draws from the ONE global SEC budget every process shares (X2 — 8.5
  req/s, held in Postgres, fair-shared between the nightly and the Workbench: `docs/DATA_SOURCES.md`
  §EDGAR etiquette), behind the client's own 8/s; every Yahoo request from the process-wide Yahoo gate (X7 —
  4/s). On top of them the shared `ingest/http.py:polite_get` adds **reactive** backoff: it retries **429 /
  transient 5xx** with capped, jittered exponential backoff, honoring a numeric `Retry-After`, and a 429 / 503
  pauses the whole pool, before raising (so a leg fails visibly rather than hammering). Tenant comes from the
  thesis (one thesis = one tenant).
- **Concurrent fetch, serialized write — the fetch-ahead** (`pipeline/fetch_ahead.py`, 2026-10-21). The walk
  was latency-bound: MEASURED on prod 2026-10-20, 1,001 SEC requests at ~0.29 s each and 740 Yahoo pulls,
  all strictly serial (~3.4 SEC req/s against SEC's 10/s), were 536 s of a 686 s pass. So on a live walk a
  pool of `Settings.ingest_fetch_concurrency` workers (default 8) makes each upcoming company's VENDOR reads
  ahead of the writer — its submissions index, its new Form 4 / 4-A documents (each its own task, so a
  document-heavy company uses the whole pool) and its price pull — at most `2 x workers` companies ahead. The
  writer still walks the basket in order on its ONE connection, takes each company's W1 lock and runs every
  leg exactly as before; no worker ever touches the database. **The contract:** a fetched read IS the
  writer's first read of the same key, performed early — its outcome (the document, or the exception) is
  handed to the legs in place of their own first network read (`ReplayingEdgar` / `ReplayingPrices`), so the
  legs see byte-identical inputs and write the same facts and versions; an exception is raised exactly where
  the serial read would have raised it (per-name, per-leg, visible — #9). **Serial semantics wherever a
  handoff could be ambiguous:** a company's second basket row, a CIK or price symbol an earlier row already
  fetched, a company the W1 lock deferred (it re-reads after the wait), a `--no-live` walk, and the kill
  switch `ingest_fetch_concurrency = 1` (no pool: the serial loop verbatim). **The settle guard (D2)** judges
  a handed-over price pull at the instant the worker STARTED it (when the caller passed no `now`) —
  conservative, never a loss. **Proved** (`tests/pipeline/test_fetch_ahead.py`): serial vs concurrent on the
  same fixtures (real client, real legs, real ATCH/FLS 4/As; only the HTTP layer stubbed, completions
  scrambled) write identical rows in EVERY table (counted), identical `NameResult`s, identical printed lines,
  the identical multiset of requests, identical freeze counters and an identical call card; the test fails
  under each of three mutations of the handoff. Cache files are written atomically (`ingest.write_atomic`),
  so a reader never sees a document a worker is still writing.

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

**…and the 12h TTL is an INTERACTIVE default: the recurring pass takes the RECURRING TTL, five minutes
(G1)** `[BUILT]`. The TTL
closed the forever-cache, but left a narrower version of the same hole, and this one was *silent and common*.
The clock starts when a given company's index was **last fetched**, so **any daytime read warms that company
for up to 12h**: the Admin "Run daily now", a sidecar boot catch-up, the on-promote ingest of new members, a
Workbench identity/extraction pull. The 22:30 pass then served that file off disk and was structurally blind
to everything filed after the daytime fetch — the day's call-of-record missed those Form 4s / 8-Ks / 13Ds, and
an arm or risk veto they caused was dated **a night late**. "Just run the daytime pass before 10:30" is not a
rule to live by: the stamp is **per company** and a full pass takes up to an hour, so a run *started* early
still warms the companies it reaches late. So every **recurring** EDGAR client is now built with
`cache_ttl_s=RECURRING_CACHE_TTL_S` — the per-CLIENT dial, the exact parallel of `force_refresh=True` for
prices, with no flag threaded through callers: `pipeline/daily.py`'s per-thesis client, `radar/spac.py`,
`radar/shell_sweep.py` (whose whole job is reading a CIK's *current* SIC — a warm index made the night's
enrichment a no-op). Immutable `forms/*` documents still cache forever, so the cost is **one index fetch per
company per pass**, which the nightly run already paid whenever it ran outside the TTL.

**Five minutes, not zero — and the difference was MEASURED.** `ingest_thesis` runs three filing legs per
company back to back, and each reads the SAME `submissions/CIK<10>.json` key (that shared read is why the
8-K and 13D/G legs cost "zero extra enumeration fetches"). `_is_stale` is `now - mtime > ttl`, so at **zero**
a file written milliseconds ago is already stale and those three reads cost **3 live fetches instead of 1** —
tripling the per-company index cost (≈1,500 SEC requests a night on a 500-name universe) for no freshness
gain, against an API whose politeness this repo treats as a **correctness** requirement. Five minutes keeps
the same-pass re-reads free and still closes the daytime gap: EDGAR accepts filings **06:00–22:00 ET**, so
nothing can be filed in the five minutes before a 22:30 pass, and no daytime warmth survives five minutes
(measured: a 20-minute-old key refetches). **Accepted residual:** a second pass started *within* five minutes
of another reads the first's cache for the companies it reached last. Since O5 (2026-10-21) the R4 freeze
page no longer exempts a `--catch-up` wholesale for it — a catch-up's SKIPPED theses are left out and the
rest are judged like any pass's (see "Known gaps").

The interactive paths (the Workbench, a standalone `python -m pipeline.ingest_thesis`) keep the 12h TTL.
Consequence for the operator: **"Run daily now" is safe at any hour**.

**…and `companyfacts` is FILING-GATED on the recurring pass (Q17, 2026-10-17)** `[BUILT]`. `ingest_fundamentals`
used to be the one deliberate exception to the recurring dial, keeping 12h "because the series is quarterly"
— but by 22:30 the 12h clock had always expired, so the leg re-pulled every basket name's companyfacts every
night (MEASURED: 680 documents, 1.18 GB). On a recurring client the `companyfacts/` key class is now re-pulled
only when uncached, when the company's cached submissions index shows a new XBRL-bearing filing since the
last pull, or after a 6.5-day backstop; a 404 is negative-cached on the same gate (X8). The gate reads the
index from disk, so it costs no SEC request. And the refresh moved INTO the per-thesis loop (step 1b of
`run_daily`): it runs right after that thesis's ingest re-pulled tonight's submissions indexes and before its
call is assembled, so a quarter filed today still reaches tonight's call. A pass-wide memory pulls each company
at most once, and a fundamentals fault is rolled back where it runs — recorded on the thesis's
`fundamentals_error`, never its `error` — so it never costs the night its record. The pass prints one
fundamentals summary after the loop. The rule, the measured form set and the cost: `DATA_SOURCES.md`
§cache-freshness and `ingest/edgar/companyfacts_gate.py`.

## Settled closes, honest stamps — the price write path (D1 + D2, 2026-10-16)  `[BUILT]`

Freshness (above) makes a pass SEE the current bars. Two older faults made some passes WRITE wrong ones.

**D2 — no in-progress bar is ever stored as a close.** A daytime pass — the Admin "Run daily now" in market
hours, or a boot catch-up after a daytime cold start (one fired at 12:07 ET on 2026-10-14) — pulled the
vendor's "today" bar, which is the session *so far*, and stored it as if it were final (MEASURED in the
review: 1,065 partial bars and 38 same-day call rows from mid-session runs, 2026-07-16 → 08-27). Now every
price write — the thesis price leg (`ingest_bars_for_security`: the per-thesis ingest, the cron, the
Workbench pull) and the SPY/IWM leg — first passes the fresh pull through `ingest/prices/settle.py`
`withhold_unsettled`: a bar dated `d` is stored only once `now >= d at Settings.price_settle_time`
(**18:00 market time**, env `ALPHADECK_PRICE_SETTLE_TIME`). One comparison, no trading calendar:
- a **prior day's** bar is always settled, so a **post-midnight catch-up** (market_today is already the next
  day, pre-open) still stores the previous session; the **22:30 RUN_AT** is 4.5 h past the settle time, so
  the scheduled pass always stores its own night's bar;
- **today's** bar is withheld before 18:00, and a withheld bar is dropped from BOTH the tail and the
  restatement compare (an in-progress value can neither append nor re-version a stored bar);
- a **future-dated** bar is never settled; a **holiday** has no vendor bar, so nothing to withhold; a
  **half-day** bar (13:00 close) is held until 18:00 too — conservative, never a loss.
- **Withholding never loses a bar**: the tail is "newer than the latest STORED bar", so the next settled
  pass appends it as an ordinary new bar. The pass says so ONCE, quietly — `N name(s): today's in-progress
  bar withheld …` (deduped across theses), and the benchmark line's `, N unsettled bar(s) withheld`.

*Why 18:00 (MEASURED on prod, read-only, 2026-10-16 — every first version recorded on its bar's own ET date,
checked against any later version within 4 days):* of the 1,896 bars first stored 09:00–14:59 ET, **1,835
later moved their close** (median ~0.7–1.0% — in-progress values), while every hour measured after the close
sat at 3–12% with no trend between 17:00 and midnight (17:xx 73/632, 18:xx 4/88, 20:xx 25/230, 21:xx 22/284,
22:xx 733/15,907, 23:xx 103/3,205) — vendor revisions, which the restatement compare re-versions. So the
guard's job is to exclude the SESSION; the data does not pick a finer hour, and 18:00 is a 2 h margin past
the 16:00 close that still sits 4.5 h before RUN_AT.

**The guard is AUDITED every night, not only enforced at ingest (2026-10-21)** `[BUILT]`. The self-audit's
`no_unsettled_bars` check (`pipeline/self_audit.py`) reads `fact_price_eod` — every version, every tenant,
SPY/IWM included — and FAILS (pages) when any row was **recorded** before its bar date's settle instant
(`d` at `price_settle_time` in market time, built with the market zone so a DST change moves it in UTC
exactly as the guard does). It is the one hard guard for the coming open / provisional-close runs
(`docs/temp/note-for-cron-server-agent-2026-10-19.md` §4): a writer in another process that bypassed
`withhold_unsettled` is caught the same night. A backdated knowability stamp before the settle instant
fails too — it claims what an in-progress store would. **Its window:** the rows recorded since the newest
finished pass that JUDGED the check (its `finished_at`, less one minute for the process-vs-DB clock;
`cron_run_log.previous_settle_audit_end` + `self_audit.settle_window_since`), so every price write — the
pass's own legs, the Workbench pull, the on-promote ingest, a repair CLI — is judged by exactly one pass
and a violation pages once; with no judging pass on record, the last 7 days. **Never before the guard's
deploy** (`SETTLE_GUARD_DEPLOYED_AT` = 2026-10-17 03:23:20Z, the pre-deploy backup of #393's deploy):
*MEASURED on prod, read-only, 2026-10-21* — **5,597** stored rows were recorded before their settle instant,
**none** since that instant (the newest 2026-10-16 00:00Z). 2,999 are real-time stamps from nine pre-guard
passes, by recorded date (ET): 07-09 ×12 (11:07–11:41), 07-16 ×95 (13:33), 07-28 ×303 (12:30), 08-05 ×449
(13:29), 08-25 ×449 (13:21), 08-27 ×892 (14:07 and 16:41), 09-01 ×164 (14:10), 09-09 ×3 (09:16), 10-13 ×632
(17:02–17:13, the boot catch-up — CDRE's 10-13 bar at 17:08, volume 627,717, re-versioned at 22:35 to
627,700); 2,992 of them were later superseded by a settled version, **7 never were** (CBT, NEGG, APD
2026-07-09; SCE-PG 2026-08-27; SCE-PG, ETHE, SAIC 2026-10-13 — each bar's only stored version is the early
pull). The other 2,598 are the SPY/IWM rows the pre-D1 leg stamped `d 00:00 UTC` (2021-08-16 → 2026-10-16;
12 since superseded). Judging that history would fail every night, so no window reaches it. The read is
cheap: MEASURED 35 ms for one night's window on prod (809 rows; the whole table is 404,503 rows).

**D1 — the benchmark leg's honest transaction time + a bounded restatement.** `pipeline/ingest_benchmarks.py`
was written as a ONE-SHOT historical backfill (`valid_from = recorded_at = the bar date` — the replay
contract for deep history) and the nightly leg reused it, so every nightly SPY/IWM bar claimed
`recorded_at = D 00:00 UTC`: ~26.5 h before it was ingested, before its own session opened. With no
re-version pass either, the two market-hours bars it froze (SPY 2026-08-25 765.27 vs a final 765.91; 08-27
770.82 vs 771.10; IWM the same two days) were the only versions ever held. Now:
- the **recurring path** (the nightly leg, and any pass through `run_daily_pass`) stamps `recorded_at = now`
  from the **database clock** (`db_now`, the one-clock rule). The **CLI** (`python -m
  pipeline.ingest_benchmarks`) kept the backdated `d 00:00 UTC` stamp "for deep history" until 2026-10-21;
  it now stamps `now` too (the operator's "no backdated recorded_at" bar — and the settle audit pages on a
  close the store claims was known before its session settled). `eod_loader.ingest_prices_backfill` has no
  production caller left (a test pins it); it stays only to seed fixtures with the history prod holds. The
  accepted consequence: a CLI pull into an EMPTY store lands its range stamped now, so a scrub-back read
  pinned before that pull shows no RS history — honest, never a lookahead;
- the thesis path's EXACT restatement compare (`_restated`, unchanged; Q11's older-bar tolerance and revert
  hold below are the thesis path's alone) runs over the vendor's newest
  **`Settings.benchmark_restate_window` = 5 settled sessions**: a revised stored bar gets a NEW version
  stamped `now` (never backdated, on either path); an in-window date the store lacks is filled. **Older bars
  are never re-versioned by a pass** — that is the operator's Q3 repair.

*The visibility contract, checked against every reader:* the bar for D, ingested ~22:35 ET on D, is inside
the night's own call (`known_at = db_now`, later), the serve-path scrub-back (`serve_known_at` →
`known_at_for_asof(D)`, the end of D's MARKET day) and a backfilled night's `--known-at next-run`, and
outside every read with `asof < D` (`valid_from = D`) and any read pinned before 22:35 (the old stamp leaked
there). The **replay/backtest** read no benchmark bar at all (`ReplayPointInTimeData` has no
`benchmark_prices` — RS is a display signal), and even the mirror's clocks would not care: a record-clock
run pins one `known_at` after every nightly ingest; a public-clock mirror rewrites `fact_price_eod`'s
`recorded_at` to the bar date. Already-stored nightly bars keep their backdated stamps (no rewrite).

**Q3 — the one-off repair of the frozen bars (`pipeline/repair_benchmark_bars.py`, 2026-10-16).** The
operator approved re-versioning exactly the two frozen days, SPY + IWM 2026-08-25 and 2026-08-27 (4 bars);
the other SPY/IWM bars that differ from the vendor (small, upward volume-only revisions after the 22:30
pull) stay as stored. `python -m pipeline.repair_benchmark_bars --dates 2026-08-25,2026-08-27` is a
**dry run by default** (read-only connection, no lock, nothing written: it prints each stored version next
to the vendor's current bar); `--apply` appends. The shape is D1's restatement: the vendor bar comes through
the `PriceSource` seam live with `force_refresh` (into a throwaway cache dir, so the shared price cache is
untouched); a bar that differs in any OHLCV field under the `_restated` tolerance gets the vendor's whole bar
as a NEW version through `ingest_prices` (`valid_from = d`, `recorded_at = db_now`, an INSERT, one
transaction); a matching bar, a date with no vendor bar, and a date with no stored bar append nothing.
Re-applying appends zero, and a read pinned before the repair still sees the frozen close. The guard takes
only the `securities.benchmarks` rows and only the dates passed, and refuses an empty list, a weekend, today
or later, and an unsettled session (exit 2). `--apply` holds the call-of-record lock (exit 75 when it is
held) and writes its provenance to `data/repairs/`. **It re-records no call:** the benchmark tape feeds only
the display-only RS column (`benchmark_prices` is called only from `signals/display/relative_strength.py`;
no basket member is a benchmark row). Run `--apply` on prod after a labeled backup.

**The same repair for ANY security's named bars (2026-10-21)** `[BUILT]`. `--bars TICKER:YYYY-MM-DD[,...]`
(or `--ticker T --dates D[,...]`) compares only the named pairs — never a cross product — for any ticker in
the master (resolved read-only; one target per master row carrying it), priced under the nightly's vendor
symbol (`price_symbol or ticker`) at the nightly range (`2y`). The same guard, compare, count-verified append,
lock and dry-run default; provenance `<stamp>-price-bars.json`. **New in both modes: a BASIS check** — the
vendor's chart is split-adjusted, so a bar to append is first checked against the vendor sessions within a
week of it (excluding the repaired dates): if any stored neighbor's close disagrees with the vendor's by more
than 5% the tape is on another basis (a split re-base), and the bar is `basis_differs` — reported, never
appended (one re-based bar would plant a mixed-basis island). It exists for the settle audit's history: the
**seven bars whose only stored version is a pre-settle pull**. *DRY-RUN against prod, read-only, 2026-10-21*:
CBT, NEGG, APD 2026-07-09 (11:07 / 11:41 ET intraday — close and volume differ) and ETHE, SAIC 2026-10-13
(17:04 ET — only high/low differ by cents) would each append the vendor's final; **SCE-PG 2026-08-27 and
2026-10-13 cannot be repaired from the vendor** — Yahoo's chart for SCE-PG returns ONE bar (the current
day) under every range (2y, ytd), so both are reported `no_vendor_bar` and left as stored. (The same fact
means the nightly builds SCE-PG's tape one live bar per night: no restatement compare ever runs for it, and a
missed night is never backfilled.) The operator chose to MARK it as a known feed limitation (0056, below —
"Known feed limitations"); once marked, the repair prints those two bars as the known limitation, not news.

### Price restatements — which restated bars a pass writes (Q11: T6 + T5, 2026-10-17)  `[BUILT]`

The thesis price leg (`ingest/prices/ingest_security.py`; the rules in `ingest/prices/restatement.py`) used to
re-version EVERY overlap bar the vendor's pull differed on beyond float noise (rel 1e-9). The operator decided
(Q11) to stop chasing two kinds of vendor noise. **The SPY/IWM leg (D1's 5-session window, `_restated`) and the
Q3 repair (`_moved`, explicit dates) are untouched — both stay exact.**

**T6 — the old-bar tolerance.** The overlap splits at the fresh pull's newest **3 session dates** (D1's own
count: the vendor's session dates, no calendar). A stored bar inside that window keeps the **exact** compare —
late volume settling lands there (BTOC's 10-14 bar: 12,592 -> 13,069 shares the next night). An OLDER bar
re-versions only when its close moved **>= 0.5%** or its volume **>= 5%**, relative to the stored value (a
stored zero or absent value keeps the exact rule). A split moves both by an integer factor, so it still lands.
A hole (an overlap date never stored) is still stored.

**T5 — revert hysteresis.** The older restated bars are decided **all-or-nothing per (security, pass)**:

- a bar is a **revert** when its fresh value matches (within the T6 tolerance) a **superseded** — non-latest —
  stored version of itself: the vendor is going back to a value the store held and replaced;
- the set is a revert only when **every** bar is one; a single value the store never held makes it
  **first-time** and it applies in the same pass (a genuine split re-base can never be held back);
- a revert set is **held**, whole, until a pull on a **later market date** agrees (the same dates still
  covered, each within the tolerance of what the held pull said). Two pulls on one night (a name in two
  theses, a second "Run daily now") never confirm each other, and a cache-first read (the Workbench pull)
  never starts, confirms or clears a hold. If the next pull serves the stored basis again (A -> B -> A -> B),
  nothing is appended and the hold clears;
- the trailing-window bars are **not** part of the decision: they always apply (exact compare);
- the pending state is `price_restatement_hold` (migration **0047**): one OPERATIONAL row per held security
  (`pull_date`, the held `bars`), upserted / deleted freely; `fact_price_eod` stays strictly INSERT-only;
- **a hold is never silent (#9):** `BarsResult.held` -> `NameResult.price_bars_held` -> the run artifact's
  per-thesis `price_bars_held`, one quiet printed line per held name (deduped across theses), and one log line
  from the price leg.

**The replay that set and checks the rule** (MEASURED, 2026-10-17, read-only on prod: the branch's code re-run
over every member-price version stored 2026-09-01..10-16 — 30 cron nights, SPY/IWM excluded; a pass = the rows
sharing one `recorded_at`, its pull = those rows plus the stored value of every other date in the 2y window;
nights a name was pulled but nothing changed are added as empty pulls):

| | old rule (as stored) | new rule |
|---|---|---|
| re-versions written | 27,367 | 17,845 (16,807 the same pass, 1,038 one night later on a T5 confirm) |
| sub-tolerance wiggles on older bars (T6) | stored | 8,772 not stored (6,412 within tolerance + 2,360 that only wiggled back to the value the store kept) |
| revert holds (T5) | — | 16: 10 confirmed by the next night's pull, 3 superseded by a first-time re-base, 2 bounced (HIMS, 1 bar, both same-night), 1 open (BTOC, 10-16) |
| first-time split-sized (>= x1.8) re-bases | applied | 43 of 43 applied the SAME pass (VWAV x20, OPTT x30, BURU x40, BTOC x16, MPU x20, DCX x160, ...) |
| (name, night) whose tape carries a T4 island / cliff | 173 | 172 |
| price-detector decisions (fire / event date / grade) that differ | — | 4, every one a T5 hold of a flip to the unadjusted basis: BTOC 10-16's fake `volume_breakout` + `breakdown_flip`, MPU 09-17's fake `volume_breakout`, REAX 09-03's `breakdown_flip`. T6 alone changes none. |

**What the numbers say about T5, honestly.** The vendor's flips mostly PERSIST: of the 15 holds a later pull
resolved, 10 were confirmed by it, 3 gave way to a first-time re-base and only 2 (HIMS, one bar, the same night)
bounced back. So one night of hysteresis mostly DELAYS a flip rather than absorbing it, and the delay cuts
both ways — it avoided BTOC 10-16's x16 cliff, MPU 09-17's cliff and VRME 10-07's islands, and it kept ARTL
09-02 and SEGG 09-01 / 09-03 on a defective basis one night longer (each of those reverts was a return TO the
consistent basis). Net: one defect-night fewer in thirty. BTOC 10-16 itself is decided by the next pull: if
the vendor still serves the unadjusted history, the cliff lands one night later.

**Why "every" bar, not "any" or "most".** Mixed sets (some bars revert, some never held) were 12 of the 195
older restated sets. "Any" would hold genuine first re-bases that carry a coincidental match (ARTL / BURU 09-05:
1 of 492 bars; REAX 09-05: 2 of 493) — on REAX it adds a cliff night. "Most" (0.9 / 0.5) holds more and moved
the defect count by at most one night: the odd bars in a flip are cent-rounding (BURU 2025-04-22 stored 0.8059,
re-served 0.81 = +0.51%; MPU 2025-03-07 12.934 -> 13.0), so the fraction measures rounding, not intent. Under
"every", a value the store never held is never held back — by construction, not by a tuned fraction.

**Why the window bars always apply.** Holding them with the older set was the first design; on LRHC 09-09 it
kept a pre-split bar on the adjusted basis between unadjusted neighbors — a fake `volume_breakout` the stored
tape never had. Applying them removed it, and no replayed hold had a basis change spanning both regions.

**What T6 changes in a recorded call** (the same replay with T5 switched off, so only the tolerance acts; the
detectors run on each night's tape with `asof` = the night; open / high / low were not replayed, so the
breakout's close-strength follow-through is not modeled). No fire / event-date / grade decision of the four
price detectors (`volume_breakout`, `breakout_52w`, `breakdown` core + flip) differs on any (active name, cron
night). They DO read older bars finer than the tolerance — a base high, a base-volume average — so an older
bar's sub-tolerance drift can move a LABEL digit: 9 detector outputs on 3 names in thirty nights (JAGX
"202.1x" vs "202.0x" avg volume, BIIB "1.6x" vs "1.7x", CPOP's 200-day base 2.75 vs 2.76), plus the
provenance detail of 62 more and the low digits of 13 `breakout_52w` scores. A CallCard carrying such a
trigger would record the other digit; neither value is more right — the difference is the vendor's own
post-settle noise.

## Honest stamps — the fundamentals write path (migration 0057, 2026-10-21)  `[BUILT]`

The acceptance bar (cron-correctness plan §2.4): facts dated by when they were first knowable, and no
backdated `recorded_at` on the live path. Under the operator's public-clock rule (`INVARIANTS.md` §4,
2026-10-21) `recorded_at` is honest BOOKKEEPING — when this system stored a row, stamped now, never backdated —
and history is judged on the PUBLIC clock. `fact_fundamentals` was the last writer that backdated it:
`ingest.fundamentals.store_quarters` stamped `recorded_at = filed 00:00 UTC`.

**MEASURED on prod, read-only, 2026-10-21.** All **120,876** rows carry exactly that stamp. No column records the
real insert time, but each row's transaction id (`xmin`) orders it among the transactions of tables whose
`recorded_at` is honest: against the nearest earlier one (a lower bound) the stamp runs a median **2,274 days**
early, over a year for **108,919** rows — companyfacts hands over a name's whole history in one pull, so its
onboarding night wrote years of quarters. **20,752** identities hold two or more versions written in ONE
transaction (an onboarding pull carries the original quarter and its later comparatives together).

**The change.**
- **The writer** stamps `recorded_at = db_now()` — the DATABASE clock (the one-clock rule; D1's benchmark
  precedent), read once per batch after the companyfacts pull, so a name's history shares one instant. Not
  injectable. `valid_from = filed` is unchanged: it is this table's PUBLIC clock and what every detector dates
  by. Existing rows are untouched — no rewrite of history.
- **The table's UNIQUE moved** (migration 0057) from `(tenant, security, metric_key, period_end, recorded_at)`
  to `(…, period_end, valid_from)`. The old key really meant "one version per FILING" because the stamp was a
  function of the filing; under one honest stamp per batch it would REJECT a history whose period carries
  several filings, and it would no longer refuse a duplicate of the same filing. The new key is the ingest's own
  idempotency key (MEASURED: 0 prod groups violate it).
- **The read** (`db/bitemporal.first_knowable_versions`, shared by the DuckDB mirror) orders a period's versions
  by `valid_from, recorded_at, id` — the latest FILING wins — and dates the run by its first FILING
  (`knowable_from = valid_from`; the operator approved retiring D3a's `max(valid_from, market date of
  recorded_at)` term, 2026-10-21). On every pre-0057 row both are identical to the old rules (`recorded_at DESC,
  id DESC` and the `max`). Under the honest stamp the old rules would have MOVED calls: the pick would fall to a
  random uuid among one batch's versions, and every quarter of a newly onboarded name would date at its
  onboarding night — a years-old inflection firing as fresh, the class D3a closed. So a fundamentals fact's
  date and pick are both stated on its public filing; the store stamp plays no part in either.

**What a reader sees.**
- *The nightly call* (`asof = today`, where the two clocks coincide): every quarter seen, picked and dated
  exactly as before.
- *History* — the backtest's public-clock mirror, the Scoreboard editions re-run, and the history views as they
  move to the public clock: fundamentals are clocked at `valid_from` (the filing), which the stamp never touched
  — unchanged, old rows and new alike.
- *The pre-0057 rows' backdated stamps* stay as written (operator decision 2026-10-21) and are MOOT under the
  public clock: nothing judges history by `recorded_at`, and `filed 00:00 UTC` already sits on the filing date.
- *The Scoreboard* reads the immutable `calls` log, never `fact_fundamentals`. *The Q17 companyfacts gate* reads
  the cache files' own clocks, never the stamp.

**The call effect, MEASURED on prod (read-only, 2026-10-21).** The new reader vs `origin/main`'s over EVERY
prod fundamentals row (661 securities, 69,289 picked rows at the live pin), at every pin tried: **0 securities
differ**. All 12 non-archived theses re-assembled at asof 2026-10-21: **0 of 12 cards differ**. On the public
clock the effect is zero by construction — the public mirror clocks each version at its filing, where the old
and new rules coincide — and `tests/replay/test_pit_first_knowability.py` holds it at every pin.

## The daily cron — `pipeline/daily.py`  `[BUILT #71]`

`run_daily(conn, *, asof=today, known_at=now, allow_live=True, force_refresh=True, notifier=None, …)`.
CLI: `python -m pipeline.daily`. For **each** thesis (`thesis_repo.list_all` — tenant intrinsic
per-thesis; **archived theses are skipped by the list's default**, the archive slice):

1. **Refresh facts** — `ingest_thesis` (incremental + fail-visible; `force_refresh=True`, the recurring path).
2. **Assemble TODAY's call WITHOUT writing** — `call_for_thesis(asof=today, known_at=now, record=False)`.
3. **Detect a MATERIAL TRANSITION** — state or verdict changed vs the call-of-record **at or before**
   this as-of →
   emit a `TransitionEvent` through the **notify seam** (`backend/notify`: a `Notifier` protocol; the
   `LogNotifier` records every move — a loud log line + the summary's TRANSITIONS block, printed only when
   there are any — and the `SlackNotifier`, when `SLACK_WEBHOOK_URL` is set, pushes by the Q13 push policy
   below; a further channel is one adapter behind `get_notifier()`, zero cron rework). This
   is also the calls-log **material-change line**: clock/trigger churn versions the log via
   `record_if_changed` *without* being a transition; a state/verdict MOVE is what an operator would want
   to be told about.
   **The baseline is `asof <= this as-of`, not `asof <`** (F2, 2026-09-13). It used to be strictly
   earlier, so a **second pass on the same day** — an Admin "Run daily now" after the nightly, the
   sidecar's retry or catch-up sequence — compared against *yesterday* even though a row for today
   already existed, and re-pushed a transition it had announced hours before (MEASURED on dev: two later
   passes each re-reported the first pass's transitions while appending 0 rows). At or before means the
   same move is announced **once**, a genuine intraday move still pages (measured against the morning
   row, so the arrow describes what you have not yet been told), and a catch-up for a past night still
   compares to that night's own row when one exists, else its predecessor — it can never reach forward.
   It also makes one rule of two: this is the *same* row `record_if_changed` compares against, so
   "unchanged card → no row" and "unchanged card → no page" can no longer disagree within a day.
   **A push about a past or late night names it (N1, 2026-10-17):** the event carries `timing`
   (`current` | `past` | `late`, `night_coverage.night_timing` at the push instant), and the Slack header
   appends "(asof D, recorded late)" / "(asof D)" — so a catch-up's arm is never read as tonight's.
   **The push policy (Q13, operator 2026-10-17: O3 + O4 + SPAC grouping)** — below.
4. **Append the call-of-record — GATED, and only if it changed** — `calls_repo.record_if_changed`, *unless the
   recording gate withholds it* (below).

- **The recording gate (R2, #198) — don't record a call built on bad data.** Before appending, the run
  computes a `withheld_reason` and **skips assemble/record entirely** when it is set: `"no-live"` (a
  `--no-live` run — cache-only, not a real call-of-record) or `"total ingest failure"` (the ingest raised, or
  *every* name errored). A **partial** failure still records, but the `calls` row is stamped
  `ingest_fresh=(ingest_errors==0)` + `ingest_errors` (provenance-only — never read by scoring; see migration
  `0023`). This closed the "1.64 s cron that recorded 6 calls off 0 ingested facts" hole (a total failure used
  to fall through and record).
- **The run identity (F1, migration `0044`) — which POLICY and which CODE produced the row.** Every recorded
  row carries `config_hash` (sha256 of the `CallConfig` the assembler ran with — `domain.config.config_hash`,
  the helper the backtest reuses verbatim), `code_sha` (the git SHA baked into the image: `ARG GIT_SHA` →
  `ENV ALPHADECK_IMAGE_SHA` → `Settings.image_sha`; **`NULL` when unknown, never fabricated**) and `run_kind`
  (`cron` | `manual` | `backfill`). *Why:* the record used to confound "the facts changed" with "the policy or
  the data arrived" — MEASURED, the two largest arm bursts in the honest record were weekend **manual** runs
  on deploy days, and nothing on the row said so, so no dial change could be evaluated against the record.
  **The three ride the write exactly like `ingest_fresh` / `reconstructed`: off the card and OUT of
  `_canonical`'s compare** — so a dial edit or a deploy alone never re-records an unchanged call (the churn
  gate), and an unchanged config never suppresses a changed one. `run_kind` is an explicit
  `pipeline.daily --run-kind` flag (default `manual`; `scripts/daily_cron.sh` passes `cron` at every one of its
  invocations), never an ambient env var — the sidecar and an operator type the same command, so only the
  flag can tell the nightly record from a hand run. Legacy rows stay `NULL`: we do not know what produced
  them, which is the finding. The Scoreboard drawer reads it as a quiet provenance caption
  (`calls_repo.run_identity_for_thesis`, the same winning-row rule as `ingest_health_for_thesis`); nothing in
  scoring branches on it.
- **The run-of-record log (R3, #197).** Every run writes one JSON to `data/cron_runs/*.json`
  (`pipeline/cron_run_log.py`, write-only, fail-open) — timing, `asof`, `mode`, per-thesis
  `withheld_reason`/`edgar_fetches`/`edgar_fetches_completed`/counts. `edgar_fetches` counts network
  *attempts*; since O5 (2026-10-21) the **freeze detector** is `edgar_fetches_completed` — the attempts that
  returned data (a frozen cache reaches out 0 times, a dead feed reaches out and gets nothing back; a healthy
  run completes hundreds), so a freeze is visible in the log instead of hiding behind a plausible quiet
  night. Since the ratio floor (2026-10-21) the log also carries the per-COMPANY freshness the floor reads —
  `edgar_companies` / `edgar_companies_fresh` / `freeze_completed_floor` per run, the first two per thesis
  ("Known gaps" below). `already_ran_live(asof, run_at, tz)` reads these logs
  (mode==`live`, **started at/after that night's `RUN_AT`** in market time, **and the call step RECORDED for
  at least one thesis** — G2b) to decide whether the night already ran — the basis of R6 catch-up and of the
  failed-run retry above. **Why the recorded test (G2b):** the guard used to credit any live post-`RUN_AT`
  artifact *regardless of health*, so a pass that COMPLETED with every thesis withheld or errored (the DB
  gone after connect; an empty User-Agent erroring every name) left an artifact that blocked both the retry
  and any later boot catch-up — the night kept no honest post-close row and nothing ever tried again. The
  test is deliberately generous: `recorded is False` (unchanged, so no row appended) is the common
  healthy-quiet night and counts; only `None` everywhere means nothing recorded. Fail-open toward RUNNING as
  ever — a damaged or pre-schema `theses` list is no evidence, so a needed catch-up still fires.
  **One file per pass, written twice (M1, 2026-10-16).** The artifact used to be written only at the END of
  a pass, so a pass that died mid-loop left nothing: MEASURED on 2026-10-12, the 22:30 run died at ~22:42
  when the host VM went down, 4 of 12 theses had rows, there was no artifact, and Admin read healthy for
  ~18 h. Now `run_daily_pass` writes a **start artifact** (`status: "running"`, `started_at`,
  `finished_at: null`, the run identity + env fingerprint) the moment it holds the run lock, and the end of
  the pass **finalizes the same file** (same name — the started-at second — atomic `tmp` + `os.replace`) to
  `status: "finished"`. An exception the pass sees re-marks it `crashed` (with the reason) beside the H3
  page; a HARD kill (the VM, the C2 timeout's `SIGKILL`) leaves `running`. An artifact written before M1 has
  no `status` and reads finished. **The guard trap:** `already_ran_live` counts ONLY a finished (or legacy)
  artifact — a `running` or `crashed` one never credits the night, or a killed night would block its own
  recovery. The stale-tape / name-error baselines and the backfill's `next-run` pin read finished passes
  only, too. A failed FINAL write leaves the start file `running`: the catch-up then re-runs the night
  (safe — and since M2 it re-records nothing that already has an on-time row) and Admin reads "did not
  finish" after the run timeout.
- **A catch-up never re-records an on-time thesis (M2, operator Q4 = "never", 2026-10-16).** MEASURED on
  10-12: the 22:30 pass died after 4 theses recorded on-time rows (22:42–22:43); the next day's 16:57 boot
  catch-up re-ran ALL 12, and `record_if_changed` appended a new version wherever restated bars (and Form 4s
  SEC accepted the next day) had changed a card — for AI Memory & Storage among the on-time 4 (seq 1334 at
  22:42 → seq 1367 at 17:04; the other three re-assembled identical cards and appended nothing, re-counted
  2026-10-17), so next-day knowledge superseded an honest on-time row, and the Scoreboard scored the late
  ones (L1, below, now sets them aside). Now a `--catch-up` pass for night D reads, once and from the TABLE,
  every thesis that already holds a
  **non-reconstructed row for D recorded ON TIME** — inside `[D's RUN_AT, 06:00 ET on the next weekday)`, the
  next EDGAR open (`pipeline/night_coverage.on_time_window`, the ONE helper M2 and the late-row policy L1
  share) — and **skips that thesis entirely: no ingest, no assemble, no write**. Its per-thesis artifact entry
  carries `skipped` with the reason, the summary counts them, and `_report` prints one quiet line. A thesis
  with no on-time row (none at all, a daytime-only row, a reconstructed row, or a LATE row) is processed as
  before. A killed night's catch-up therefore records ONLY the missing theses. A skipped thesis counts as
  "the night has its record" everywhere that asks (the exit code, the guard, the self-audit), and a pass that
  skipped any thesis counts toward the stale-tape / name-error baselines only for the theses it COVERED (a
  skipped one keeps its entries from the last pass that covered it — the per-thesis carry-forward, G5a
  below). It IS the split-basis baseline IN WHOLE: that scan reads the whole basket union, skipped theses'
  names included (T4 follow-up, 2026-10-17 — see the tape monitor, part 2). The
  scheduled run and the Admin button never skip: they re-version on purpose. If the skip read itself fails,
  the catch-up processes every thesis (one loud line) — the pre-M2 behavior, never a silent gap.
- **A LATE row is recorded, never scored (L1, operator Q5 = A, 2026-10-16).** M2 stops a catch-up from
  re-versioning an on-time thesis; it still (rightly) records the theses that HAVE no on-time row, and when
  it runs after the night's window closed — 06:00 ET on the next weekday, `night_coverage.is_late` — those
  rows are **late**: they can have read filings SEC accepted after the night (the 2026-10-12 catch-up's rows
  cite three Form 4s accepted on 10-13). The cron writes them exactly as before — the `calls` log is the
  record of what the platform said, and when — and the Scoreboard's default reading sets them aside BEFORE
  the per-as-of dedup, so an on-time row for the night wins and a late-only night reads like a cron gap
  (`docs/SCOREBOARD.md` §"Late-recorded rows"; reversible by `ALPHADECK_SCOREBOARD_EXCLUDE_LATE` /
  `?include_late=`). Lateness is DERIVED from `recorded_at`, never stamped, so nothing about the write path
  changes. Three consequences an operator should know: (1) a catch-up that lands after 06:00 the next weekday
  still repairs the Admin hole (the night is `covered`, "recorded late") but does NOT put the night back on
  the Scoreboard — the M1 coverage words and the Scoreboard's reading are deliberately different questions;
  (2) a **Friday** night's window runs to **Monday 06:00**, so a weekend catch-up is on time; (3) a
  `pipeline.backfill` reconstruction is a separate path (reported, never scored) and is never judged late.
  The Slack push says so too (**N1**): a transition pushed for a night whose window has closed reads
  "(asof 2026-10-12, recorded late)" on its header, and one pushed for an earlier night still on time (a
  pass that ran past midnight) reads "(asof 2026-10-12)" — `TransitionEvent.timing`, judged by
  `night_coverage.night_timing` at the push instant; tonight's push is unchanged, and the label is
  fail-open (a clock fault reads as tonight's, never costs the record).
- **The push policy (Q13, operator 2026-10-17: O3 + O4 + SPAC grouping)** `[BUILT]`. Inverse loudness (#7)
  for the Slack pushes, so loudness marks a REAL arm and nothing else. *Why:* the push used to fire on "the
  move lands IN armed", so any verdict change while a thesis STAYED armed read like a new arm — the red "ARMED"
  header even when the setup got WEAKER (MEASURED: DAT 2026-09-23, core_entry → starter_entry with 4 → 2
  armed names; Nuclear Buildout 2026-10-07, core_entry → starter_entry with 4 → 3) — while a NAME arming
  inside an already-armed thesis pushed nothing at all. The rule (`notify/policy.py`, pure; the cards are
  read by `pipeline/push_policy.py`, so the notifier stays pure — the #309 seam):
  - **Into Armed** from any other state → **LOUD**, the existing 🔴 ARMED push. **Unchanged.**
  - **Out of or short of Armed** (a de-arm, warming, incubating, managing) → **recorded, never pushed.**
    **Unchanged** — a de-arm has never pushed, and Incubating never nags.
  - **Armed → Armed** (a verdict change while armed — the event trigger itself is unchanged) compares two
    numbers read straight off the CallCard: the thesis call's **grade** = `CallCard.entry_grade` (the weaker
    key, the one that drives the verdict; `flip` < `core`) and the **armed-name count** = the distinct
    `security_id`s in `CallCard.armed_members`. Something rose and nothing fell → **UPGRADE, LOUD**: the
    ARMED style with an up marker and what improved ("🔴 Nuclear Buildout — ARMED ↑ now core, +2 names
    (starter_entry → core_entry)" + the company lines). Something fell and nothing rose → **DOWNGRADE,
    QUIET**: no emoji, no capitals, worded honestly ("Nuclear Buildout — still armed, weaker: core → flip;
    4 → 3 names (core_entry → starter_entry)") with, on a second line, the reason the card carries — the
    names no longer armed, each with the de-arm signal the new card holds for it ("no longer armed: GHM
    (structural break, fast breakdown)"), and the confirmation key's own detail when its grade moved.
    **Opposite directions** (grade up but names down, or the reverse) → **QUIET, both stated** ("still
    armed, mixed: flip → core; 2 → 1 names") — a weaker side never rides a loud header. Neither moved (e.g.
    `flip_only` ↔ `starter_entry`, a validity-window change at the same grade) → a quiet "still armed,
    changed" note.
  - **The nightly digest (O4).** ONE quiet message per pass, at the pass end after the per-thesis loop,
    listing the names that NEWLY armed inside theses that were already Armed and stayed Armed — the new
    card's armed set against the SAME baseline row the transition compare and `record_if_changed` read
    (F2), so a same-day second pass never re-announces a name. Grouped by thesis in the compact #309 shape
    ("Modern Defense: LMT catalyst, breakout · HONA (re-arm) insider buy, breakout"). Within each thesis's
    line the **FIRST-TIME arms come first, unmarked, then the RE-ARMS, marked "(re-arm)"** (the flapping
    names), each group in the card's rank order. THE ONE DEFINITION (`pipeline/push_policy.py`
    `_EVER_ARMED_SQL`): a name is a re-arm when it is an armed member of an ARMED, non-reconstructed
    call-of-record of THIS thesis at an as-of on or before the night, earlier than this pass's own row; read
    once per pass, read-only, at digest-build time — and if that read fails every name reads first-time
    (nothing dropped, the failure logged). Excluded: the names that thesis's own push already listed (an
    upgrade push lists every armed name — unless its enrichment failed open); a thesis ENTERING Armed (its
    loud push is the news); reconstructed rows (`pipeline.backfill` imports no notifier — pinned by its
    import guard). **No names, no message** — never a "nothing new" ping. A catch-up or late pass carries
    N1's label on the digest header. Built per thesis AFTER that thesis's commit and sent once after the
    loop, both fail-open: a digest fault never costs a record or another push.
  - **Names, never counts (#9 — operator, Q13 review).** Every push LISTS every name — the digest, the loud
    push's company lines, a quiet push's "no longer armed" reason: by ticker; a name the master holds with no
    ticker by its company name ("Acme Uranium Corp (no ticker)"); and if even the master read failed, by its
    security id. No format collapses names into a count.
  - **One SPAC push per announcement.** The radar's →announced page is ONE event per transitioned company,
    listing every matched thesis and its matched terms (`radar.spac.announcements`); it used to page once
    per (company, thesis).
  - **Deliberately NOT a push:** an armed thesis whose armed-name count moves while its verdict holds still
    emits no `TransitionEvent` (the trigger is unchanged); its newly armed names reach the operator through
    the digest. MEASURED alternative (not built): making a names-only move a thesis-level event would have
    added 80 loud upgrades + 80 quiet downgrades over the record, against 26 loud pushes in all as built.
  - **MEASURED replay** over the honest prod record (642 non-reconstructed rows, 74 passes, through
    2026-10-16; the F2 baseline as of each row's write): loud pushes **34 → 26** (17 entering Armed, 9
    upgrades), **8** quiet (5 downgrades, 3 mixed — the 17 armed → armed events used to be 17 loud), **46**
    digest messages carrying **288** names — **140 first-time arms, 148 re-arms** — and **21** duplicates
    suppressed (names an upgrade push listed). The
    309 name-level arms (164 distinct thesis × name pairs) all reach the operator now: 288 in a digest, 21 on
    an upgrade push. SPAC, over every recorded radar run: **221 → 136** pages, 62 multi-thesis
    announcements collapsed (2 to 5 theses each).
- **The health pager (R4, #199).** `assess_health` emits a `HealthEvent` through the notify seam
  (Slack via `SLACK_WEBHOOK_URL`, **fail-open**; `LogNotifier` otherwise) when a run is a **FREEZE**
  (since O5: `frozen = allow_live and` the theses the pass PROCESSED — a `--catch-up`'s M2-skipped ones left
  out — completed **zero** EDGAR fetches; the page says "0 of N EDGAR fetches COMPLETED" when pulls were
  attempted; **or**, by the RATIO FLOOR, fewer than `Settings.freeze_completed_floor` (50%) of its
  EDGAR-eligible companies got fresh SEC data — "the SEC feed looks starved: only N of M companies"; see
  "Known gaps"; an artifact written before O5 keeps the old `edgar_fetches == 0` rule with a catch-up exempt,
  and one written before the floor keeps the rule it was paged under, so the Admin history never changes a
  past verdict), has **withheld** calls, has **thesis
  errors**, **failed to refresh the benchmark tape** (below), or found a **newly stale price tape** (G5a,
  below — pageable but not a cron alarm). A healthy run returns `None` — silent
  (loudness marks the exception). This is the page R1 lacked: the platform now notices its own blindness.
- **A failed BENCHMARK refresh pages too (G4)** `[BUILT]`. The shared-input legs that run *before* the
  per-thesis loop (the SPY/IWM tape feeding `benchmark_rs`; each basket's quarterly revenue) are
  **fail-open passengers** — a fault must never fail the call cron. But fail-open had meant *silent*: the
  fault printed to stdout, stdout dies on the next `docker compose up`, and the night still produced calls
  computed against a **stale** shared input with nothing saying so. The benchmarks leg's outcome is now
  carried on the pass: `benchmark_errors` (individual pulls that failed — a partly stale tape) and
  `benchmark_leg_failed` (the leg raised before producing any result — no refresh at all), reported
  **distinctly** because they are different news. Both go into the **run-of-record artifact** as well as the
  page, so the Admin history re-derives the verdict the run actually paged (a count that reached only the
  notifier would make that night re-read forever as a green row), and both make the Admin cron verdict
  `unhealthy` — a real alarm, not a benign note. The fundamentals leg keeps stdout-only reporting: a
  quarterly series tolerates a day.
- **A price tape that STOPPED pages too (G5a)** `[BUILT]`. The price leg appends bars after the latest stored
  one, so a name whose vendor series simply **ends** — the SEC ticker stays canonical but the vendor prices it
  under a new symbol after a rename, or it delisted — appends **zero bars with no error**, which is
  byte-identical to a market holiday. Nothing read the tape's edge, so for that name every price-driven
  detector went dark from the stop date (no breakout, no SMA flip, no RVOL — and no price-based de-arm
  either) while the CIK-keyed filing legs kept flowing, so it could still WARM on a filing and never confirm
  on price. Now `ingest_thesis` reports each name's **tape edge** (`NameResult.tape_edge` — a FACT), `run_daily`
  judges it against the run's `asof` (`pipeline/tape_health.py` — pure; `Settings.tape_stale_days`, default 5
  CALENDAR days, `0` disables), the stale set lands per-thesis on the run-of-record artifact, and **newly**
  stale names page. Newly only, diffed against the previous evaluated pass and keyed on `security_id` (never
  the ticker — a ticker-less name must still page, and a ticker changing under a name is half the point): a
  handful of known-dead tapes must not re-page nightly, while the FIRST evaluated pass hands over the whole
  inventory once. A `--no-live` pass evaluates nothing and records `tape_evaluated: false`, so it can never
  become the baseline and silence the next real page. It is a **MONITOR**: it touches no detector, no
  `calls/` module, and nothing on the CallCard — a day-varying card field would flap `record_if_changed`'s
  substance compare and break the cron's idempotency. And it is NOT a cron alarm: the run worked, the FEED has
  a gap, so the Admin verdict stays `healthy` while the panel lists it (`ADMIN.md` §stale price tapes). The
  repair is the operator's and is data, not code (`DATA_SOURCES.md` §when the symbol drifts).
- **The stale-tape and name-error baselines carry each THESIS forward (2026-10-17)** `[BUILT]`. Both
  inventories are per thesis (each entry belongs to a thesis the pass ingested), and an M2 `--catch-up`
  covers only the theses whose on-time row was missing. The two baselines used to read only FULL-roster
  passes, so a catch-up was invisible to the next pass — MEASURED with contract inputs: a name a catch-up
  FIRST flagged paged there and again at the next full pass (which diffed against the pass before the
  catch-up), and a name the catch-up saw CLEAR stayed "already told", so its return never paged. Now the
  baseline is, for each thesis on the newest qualifying pass's roster, its entry from the newest finished,
  live, evaluated pass that COVERED it, full or partial; a thesis a catch-up skipped keeps its entry from the
  last pass that did cover it (`cron_run_log.carried_inventory`, read by `previous_stale_tapes` and
  `previous_name_errors`). Coverage comes from the artifact alone: a thesis is covered unless its entry
  carries an M2 `skipped` reason, and an artifact written before M2 covers exactly the theses it lists. The
  look-back ends at the newest full-roster pass (it covered every thesis then active), and a thesis no longer
  on the roster (archived) takes its entries with it. So a name first flagged by a catch-up pages ONCE, a
  name cleared in a catch-up and later back pages again (a genuine return), and a name in a skipped thesis
  never re-pages. Fail loud: no qualifying pass at all pages the whole inventory once (as before), and a
  thesis no readable pass covered contributes nothing, so its names read as news. The Admin freshness panel
  and the Price feed repairs card read the SAME carried inventory, so what they list is exactly what the next
  pass treats as already told, and the panel's `newly_stale` is what the newest such pass paged (`ADMIN.md`).
  **Known limit (accepted):** the artifact records a thesis's flagged names, not its members, so a name held
  by a thesis the catch-up skipped AND by one it covered and saw clear stays in the skipped thesis's carried
  entry — if it comes back by the next pass, that return does not page; the full pass after reads both
  theses fresh. The T7 successor-CIK baseline follows this same rule (§the identity monitor). The split-basis
  baseline keeps its own rule (part 2): its scan reads the whole basket union
  rather than the ingested theses' names.
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

**A new card field re-records only the cards where it is NOT its default (Q14, 2026-10-18).** The prior row
is parsed back through the CURRENT model (`row_to_call` → `CallCard.model_validate`) before the compare, so a
row recorded before a field existed reads that field as its default. Adding `MemberCall.entry_window_closing`
(default `false`) therefore changes `_canonical` only for a card where some member's flag is `true` — not for
every thesis. MEASURED with a read-only re-assembly of the 2026-10-16 night on prod (`known_at` pinned to
each row's `recorded_at`, the same pin for the code with and without the change): **1 of 12 theses differs
— Modern Defense, whose armed TPCS had its entry window ending Sun 10-18 (0 sessions left on Fri 10-16).**
Q12's de-arm copy (same branch, same deploy) changes `expression` on every de-armed card instead: on the
same re-assembly it adds **2 — AI Memory & Storage and Nuclear Buildout**, both de-armed by fast breakdowns
under the current code (their recorded 10-16 rows, written by the older image, were armed — code drift
since that night, not Q12). Together: **3 of 12**. On a NEW night every thesis appends its first row regardless (a new `asof` has no prior), so the churn that
matters is a same-`asof` re-run straddling the deploy — which is why a change like this ships after a clean
nightly. The flip side is a **rollback hazard**: `DomainModel` is `extra="forbid"`, so once a row carrying
the new field is recorded, OLDER code cannot parse that row (`row_to_call` raises on the unknown key), and the
log is immutable — so a code rollback past such a change must keep the field in the model (or drop
`extra="forbid"` for card reads) rather than revert it wholesale.

**One writer at a time — the cross-process run lock (C1, 2026-10-16).** Read-compare-append is only
idempotent when nothing else appends in between, and two passes DID overlap (MEASURED 09-01 and 09-09: the
sidecar's late pass and an Admin click, 17 rows for one night across 12 theses). `run_daily_pass` now holds a
Postgres **session-level** advisory lock (`pipeline/run_lock.py`, key `(0x41444B, 1)` — `classid 4277323` in
`pg_locks`) on a dedicated autocommit connection for the WHOLE pass (pre-legs, loop, artifact, post-legs);
`pipeline.backfill` takes the same lock for a real (non-dry) run. Session-level means a killed pass's lock
dies with its connection — no stale lock can block the next night. A second pass never queues: it prints
`ALREADY RUNNING — … held by pid N (alphadeck-pass:<kind>, …)`, writes nothing, pages nothing, and the CLI
**exits 75** (`EXIT_ALREADY_RUNNING`) — distinct from 0 (it produced no record) and 1 (the night is not
failed; the holder is making its record). `main` takes the lock itself and re-checks the `--catch-up` guard
under it. The Admin button probes it (409 naming the holder). The Workbench's on-promote ingest and the
CLI ingest never take this lock (they write facts, never `calls`) — they are serialized PER COMPANY instead
(W1, next paragraph), so a Workbench ingest can never make a pass exit 75.

**One writer per COMPANY — the per-company write lock (W1, 2026-10-21).** Every fact ingest is a
read-compare-append (read what is stored — `existing_accessions`, `latest_bar_date`, the stored versions —
then append what is new), so two writers walking the SAME company at once can each read "not stored yet" and
each append: a duplicate version that hides behind the as-of read's dedup while the table grows. MEASURED on
prod 2026-10-21 (read-only, identical payload to the version immediately before it, `recorded_at` within
60 s): **1,519 such pairs** — 1,128 `fact_price_eod` bars across 217 securities, 354 `fact_insider_txn` rows
across 33, 28 `fact_corporate_event`, 6 `fact_activist_stake`, 1 `fact_fund_shares`, 0 `fact_fundamentals`,
0 `insider_amendment` — **every one on 2026-09-09 13:15–13:22 UTC, all within 5 s** (most within 10 ms): the
two overlapping daily passes C1 now refuses (the `calls` log shows the same 17 rows / 12 theses in that
window). No pair from any other writer pairing exists yet; 6 further identical insider versions are > 10 min
apart (not concurrency). Repair was out of scope (report only). The open/close intraday runs coming next
make the overlap routine, which is why the guard is per company rather than per pass:
- **The mechanism** (`db/write_lock.py`): two Postgres SESSION advisory locks taken together on the writer's
  OWN working connection — the company lock `(0x414443 "ADC", first 32 bits of security_id)` EXCLUSIVE, and
  the fact-writer gate `(0x41444B, 2)` SHARED. Session-level, so the company stays held across the legs'
  per-leg commits and rollbacks and dies with a dead writer's session (nothing stale); re-entrant on its
  session; released only after the writes it guards commit. Every acquisition is a TRY or a bounded poll of
  tries — no session ever blocks inside Postgres on these, so no deadlock involving them can form.
- **Who takes it:** `pipeline.ingest_thesis` per member (so the nightly's per-thesis ingest, the Workbench
  on-promote job and `python -m pipeline.ingest_thesis` all do), the nightly's Q17 fundamentals refresh and
  `python -m pipeline.ingest_fundamentals` per name, `ingest_benchmarks` per benchmark (SPY/IWM can be a
  thesis's ETF sleeve), the Workbench per-name price pull, and `pipeline.backfill_fund_shares` per date.
  Every fact REPAIR takes the gate EXCLUSIVELY after the call-of-record lock (`run_lock.acquire_repair_lock`:
  `repair_insider_filer_text`, `repair_insider_ownership`, `repair_benchmark_bars`, `relink_amendments`,
  and — new — `dedup_identical_versions`, `backfill_accepted`, `backfill_aff10b5_1`,
  `repair_adr_insider_misattribution`, `repair_activist_misattribution`, `repair_impossible_txn_dates`), so a
  repair never runs beside any company writer and none starts under it.
- **The lock order (the deadlock rule):** call-of-record lock → the fact-writer gate → a company lock, never
  the reverse. The nightly holds call-of-record and takes gate(shared)+company per company; a repair holds
  call-of-record, then the gate (exclusive); the Workbench and the CLI take only gate(shared)+company. A
  writer holds ONE company at a time (no hold-and-wait across companies), and one writer unit uses one
  connection for its locks and its writes. Pinned by `tests/db/test_write_lock.py` and
  `tests/pipeline/test_ingest_write_lock.py` (incl. two writers walking three companies in opposite orders).
- **The waits** (`domain.settings`): `ingest_lock_wait_s` = 180 s for a batch writer (the nightly, the
  on-promote job, the CLIs) — and `ingest_thesis` first TRIES every company without waiting, DEFERS a held
  one to the end of its walk and only then waits, so a busy company never delays the free ones;
  `ingest_lock_request_wait_s` = 15 s for the Workbench price pull (then a **409** naming the holder, never
  a hung request); `repair_lock_wait_s` = 60 s for a repair waiting for writers to drain (then exit 75,
  nothing written).
- **What the nightly does with a held company:** waits (deferred, bounded), then ingests it — a quiet line
  and `company_lock_waits: [{…, ingested: true, waited_s}]` on the thesis's artifact entry. One STILL held
  after the wait is not ingested by THIS pass (the holder is writing those very facts): `NameResult.lock_busy`
  names the holder, the monitor facts (edges, stop facts) are still read, and it is **never an error** — it
  does not count toward `ingest_errors` or the recording gate, so a Workbench ingest can never withhold or
  fail a night — but the call does rest on a name this pass did not refresh, so the row is stamped
  `ingest_fresh=false` (R2b's meaning). Loud: a `COMPANY LOCK — N name(s) NOT ingested by this pass` block
  in the report and `ingested: false` + `holder` on the artifact. The Workbench on-promote job reports the
  mirror case on its poll (a visible failed job: "N not ingested here because another ingest was writing
  the same company"), and the CLI ingest exits 1 for it.
- **Not covered (named):** `pipeline.seed` (the boot demo seed writes its fixture facts in ONE transaction
  committed at the end, so serializing it would mean holding every seed company until that commit; its
  guards make it a no-op on an already-seeded DB, leaving a first-boot-only window); the Workbench
  RATIFY writes (`/facts` — operator-typed `fact_shares_outstanding` / `fact_revenue_mix` / `fact_catalyst`
  / `fact_cash_burn`, keyed by source and written by no ingest) and the operator's Form 4/A decisions; the
  pass's pre-loop Form 4/A sweep (decisions only, under the call-of-record lock; an ingest stores a new
  4/A and its decision in ONE leg transaction, so the sweep never sees one undecided); the SPAC radar's
  `fact_spac_*` (one writer, the nightly). The deploy wrapper deliberately ignores company locks
  (`docs/DEPLOY.md`).

**What the pass now leaves behind (Phase 1B).** Beyond the counts, the run-of-record artifact carries:
the **run identity** (`code_sha`, `config_hash`, `run_kind`) and a **non-secret env fingerprint** (`TZ`, the
market zone, `RUN_AT` / `cron_at` as values; the User-Agent, Slack webhook, Polygon key and heartbeat URL as
**set/unset booleans only**; the notifier class) — the Admin page alarms on an unknown or mismatched sha, the
LOG notifier and an unset UA; **per-name ingest errors** by leg (`name_errors`), diffed against the last-known
inventory (each thesis as the newest live pass that covered it saw it — the per-thesis carry-forward, G5a
above) so only NEWLY failing (security, leg) pairs print loud and page, the known ones collapsing to one
quiet count; and the **self-audit** (`audit`, `pipeline/self_audit.py`), whose failures page. Its checks
(`DEFAULT_CHECKS`, in order): `on_time_rows` (every thesis in the night's roster has a non-reconstructed row
recorded at/after `RUN_AT`, counted in the `calls` table), `code_sha_known`, `every_thesis_finished`,
`split_defects_flagged` (T4, below — the split-basis scan ran) and `no_unsettled_bars` (2026-10-21 — no
`fact_price_eod` row recorded before its bar's settle instant, judged over the rows written since the last
judging pass; "Settled closes" above). Every
connection the pass opens sets a session **`statement_timeout`** (`ALPHADECK_CRON_STATEMENT_TIMEOUT_S`,
default 300 s — sized ~2,000x the slowest measured single read, 151 ms; 0 disables), so a lock wait or a
stuck read raises inside the per-thesis isolation instead of hanging the loop. And an **unhandled exception**
in the pass sends ONE health page (`PASS CRASHED — <type>: <message>`) before it re-raises — fail-open, it
can never mask the original. (A process that is KILLED runs no Python at all; the sidecar's own page and the
external heartbeat cover that.)

**Known skipped filings are a count, not ~440 lines a night (Q18, 2026-10-17)** `[BUILT]`. Three filing
paths skip ONE filing and keep walking, and a skipped filing is never stored, so it is re-tried and its line
re-printed every pass: a Form 4 that cannot be fetched or parsed (`form4_unparseable`), a 13D-family filing
in a company's own feed that names another subject — the company's own outbound stake (`schedule13_outbound`),
and a 13D/G whose filer identity did not resolve (`schedule13_identity`, stored with a NULL filer). MEASURED
2026-10-17 by replaying the legs read-only over prod's cache and DB: 76 Form 4 lines and 365 schedule-13
lines a night, about 73% of the cron log. **Audited before anything was collapsed** (the skip-counter lesson:
a rising Form 4 skip count once hid a timezone bug that dropped 49 real buys): all 76 Form 4s are pre-XML
filings dated 1997 to 2003-04-02 — 60 SGML text documents, 15 accessions with no primary document (the
fetched "document" is EDGAR's directory listing) and 1 whose `0001.txt` 404s (checked live; the filing is a
2000 text Form 4) — and none fails with a `ValueError`; all 365 schedule-13 lines (328 accessions) are
13D-family filings whose feed owner is the lead filer (364) or a co-reporting person (1). No group was a
parser bug. The mechanism is O1's, mirrored (`ingest/skips.py`, `pipeline/ingest_skips.py`): before its loop
the pass reads the skip baseline (`cron_run_log.previous_ingest_skips`): each thesis's skipped filings,
recorded on its own artifact entry like `name_errors`, from the newest finished, live pass that COVERED it
(the per-thesis carry-forward `carried_inventory`, shared with the stale-tape and name-error baselines, so a
filing first skipped by a catch-up is not re-printed at the next full pass and a thesis the catch-up skipped
keeps its filings). A filing found there is recorded but not re-printed, and a NEW one prints its one line
naming the accession. An artifact entry without that per-thesis list (the first Q18 build) reads as unknown:
its filings print once more, never muted. After the loop the artifact carries, per
kind, `total`, `known`, `new` (accession, security, ticker, reason), `previous_total`, `delta` (so a jump in
the known inventory shows) and the full `accessions` list, each thesis entry its own `ingest_skips` (the
next baseline), and stdout gets one quiet line
per kind. The first pass after the deploy has no baseline, so it prints every skip once. **Only the logging
changed**: what is stored, skipped, counted (`form4_skipped` on each thesis line) or raised is identical, a
CLI or on-promote ingest (no baseline) prints everything, and no raising path became a swallow-and-count.
The P1 `REJECT insider txn` lines (impossible transaction dates) are untouched and stay loud.

### Where a pass spends its time — the `timings` block (X5, 2026-10-17)  `[BUILT]`

**Measurement only.** Before the cron grows (more names, more theses) and moves off the laptop, two questions
needed numbers: where does a pass spend its time and memory, and how does that scale? The artifact carried a
start, a finish and a per-thesis `edgar_fetches`, nothing per leg. `pipeline/pass_timings.py` adds a meter;
it changes nothing that is ingested, recorded, paged or scored, and nothing reads it back into the pass.

**How it is wired.** `run_daily_pass` starts one `PassTimer` and makes it ACTIVE for the pass through a
`ContextVar` (the seam is `ingest/instrument.py`, so `ingest` never imports `pipeline`). The instrumented
sites need no new parameter and are a no-op wherever no pass runs (the Workbench, a standalone CLI, the
suite): `per_thesis(...)` wraps the loop's iterable (each thesis is a scope), `timed(leg)` wraps a call, and
`note(kind, n)` counts on the innermost open leg.

- **Per-thesis legs** (summed over the thesis's names): `form4`, `form8k`, `schedule13`, `price`,
  `fund_shares` (`ingest_thesis`), `submissions` (the EDGAR client's reads of a company's submissions index,
  fetch or cache read plus parse, whichever leg made them — mostly inside `form4`, the first reader),
  `fundamentals` (the Q17 filing-gated refresh), `call` (the assemble), `notify` (a transition push),
  `record` (`record_if_changed`). Whatever no leg claimed is the thesis's `other`: the master reads, the
  tape-edge and delisting reads, the feed-recency judgment (its Form 25 class reads included), the prior-call
  read, the commit.
- **Pass legs:** `benchmarks`, `baselines` (the previous-artifact reads for the skip / stale-tape /
  name-error diffs; the split and successor baselines are read inside their own scans), `digest` (the Q13
  newly-armed digest after the loop: its re-arm history read + the push; absent on a pass with no newly
  armed name), `split_scan`,
  `rename_probe`, `successor_scan`, `self_audit`, `artifact_write`, `health_page`, `shell_sweep`,
  `spac_radar`; the gaps between them are the pass's `other`. **Not in the process:** the nightly backup is a
  separate `python -m pipeline.backup` the sidecar runs after the pass, so it is not measured here. The
  printed report runs in `main` after the artifact is final; it is stdout formatting only and is not timed.
- **Counts:** `sec_live` / `sec_cached` / `sec_cached_404` (the EDGAR client's network decision for each
  request: a fetch, a disk-cache answer, a negative-cache answer; `sec_live` is the same decision
  `edgar_fetches` counts), `http:<host>` (every attempt of the polite GET, retries included — the Yahoo price
  pulls are `http:query1.finance.yahoo.com` / `query2`), `http_retry:<host>`, `backoff_s` (seconds slept
  before a retry), `rate_wait_s` (seconds a proactive rate limiter or the SEC budget slept),
  `http_<status>:<host>` (X7 — every non-2xx answer by status: `http_429:…` is the vendor's rate-limit
  rate, `http_404:…` the expected misses), `sec_budget_fallback` (an SEC request paced by the local fallback
  because the global budget's database was unreachable — absent on a healthy pass). No new network call is
  made. The printed timings line ends with `HTTP 429s: <host> <n>` on a pass that received any.

**Exclusive time.** A leg's seconds are its SELF time: a leg timed inside another (`submissions` inside
`form4`) is subtracted from the outer one, so a thesis's legs sum to its `total_s` and the pass's legs sum
to the pass `total_s`. Legs only: one clock pair per leg per name, never per row or per filing.

**Concurrent fetches (v2, 2026-10-21).** The fetch-ahead's workers never touch the pass meter: each task
runs under its own CHILD meter, which the writer ABSORBS when it takes the task's result. The child's COUNTS
merge into the thesis under the same legs the serial walk counts them on (a worker's index fetch is
`submissions: sec_live`, its Form 4 documents `form4: sec_live`, its price pull `price: http:query1…`), so
every request is still counted once, on its usual leg — MEASURED identical to the serial walk in the suite.
The child's SECONDS go to `concurrent_s` (per thesis, summed per pass): worker-seconds by leg, which overlap
each other and the writer, so they are kept OUT of the exclusive `legs`. The writer's own wait for a fetch
is the leg `fetch_wait`. So on a concurrent pass `submissions` / `price` in `legs` are only the writer's
cache reads and writes, `fetch_wait` is what the vendors still cost the walk, and `concurrent_s` divided by
the thesis's `total_s` is the concurrency actually achieved.

**Memory.** `peak_rss_kb` is `getrusage(RUSAGE_SELF).ru_maxrss` (KB on Linux) for the pass process. The
sidecar runs every pass as its own `python -m pipeline.daily`, and the Admin "Run daily now" runs it in a
spawned child (`pipeline/bounded_pass.py`), so in both cases the measured process IS the pass.
`peak_rss_children_kb` is `RUSAGE_CHILDREN` (the pass spawns no child today). Each thesis records the peak and
the current RSS (`/proc/self/statm`) at its end, so growth across the loop shows (a climbing current RSS is a
leak; a peak with a falling current RSS is a spike). `resource` and `/proc` do not exist on Windows, where
the suite runs: those fields are `null` there.

**The artifact field** (`build_run_payload`):

```json
"timings": {
  "v": 2,
  "total_s": 912.4,
  "legs": {"fetch_wait": 301.2, "form4": 190.7, "price": 60.3, "call": 88.1, "other": 41.0, "...": 0},
  "counts": {
    "submissions": {"sec_live": 731, "sec_cached": 2193, "rate_wait_s": 61.3},
    "form4": {"sec_live": 12, "http:www.sec.gov": 12},
    "price": {"http:query1.finance.yahoo.com": 740, "http_429:query1.finance.yahoo.com": 2}
  },
  "concurrent_s": {"submissions": 700.4, "price": 420.9, "form4": 30.2},
  "theses": [
    {"id": "…", "name": "Example thesis", "total_s": 108.3,
     "legs": {"fetch_wait": 40.1, "price": 10.2, "form4": 20.0, "other": 9.8, "...": 0},
     "counts": {"submissions": {"sec_live": 196, "sec_cached": 588}},
     "concurrent_s": {"submissions": 180.2, "price": 95.0},
     "rss_kb": 301244, "peak_rss_kb": 355120}
  ],
  "peak_rss_kb": 412300, "peak_rss_children_kb": 0, "rss_kb": 390112,
  "errors": []
}
```

(The numbers above illustrate the SHAPE; they are not a measurement.) `null` on a pass that carried no meter
and on every artifact written before X5. `errors` lists instrumentation faults (capped at 20): a timer that
raises is recorded there and the work runs untimed, never failed.

**One block, rewritten in place.** The artifact is first written with the snapshot taken just before it; the
health page, the shell sweep and the SPAC radar run after that write, so `run_daily_pass` then REPLACES the
block in place with the final one (`cron_run_log.amend_run_timings`: only a file that reads `finished` and
carries this pass's `started_at`). During the loop the meter also writes its snapshot onto the START artifact
after every thesis (only while it reads `running`), so a pass KILLED mid-loop (the C2 timeout, the OOM
killer, the VM) still leaves where it spent its time, and `mark_run_crashed` carries the snapshot at a
Python-visible crash. `timings.total_s` runs from the lock to the end of the SPAC radar; `duration_s` keeps
its meaning (`started_at` to the end of the per-thesis loop).

**The printed report** ends with one line: `timings: 912.4 s total · top legs submissions 301.2 s, form4
190.7 s, price 160.3 s · slowest thesis Example thesis 108.3 s · peak RSS 403 MB` (the same illustrative
numbers). The Admin run line
shows the whole-pass total and the slowest leg (`AdminRunOut.pass_total_s` / `top_leg` / `top_leg_s`).

**The "before" picture (MEASURED from prod's existing artifacts, 2026-10-17, read-only).** 79 artifacts
(2026-07-17 to 2026-10-17); none carries per-leg data, so this is the coarse baseline X5 replaces:

- Warm nightly passes at today's size (12 theses, 740 name-memberships), 22 live non-catch-up passes since
  09-14: `duration_s` median **730 s**, min 578, max 1,250; `edgar_fetches` median 757. By universe size,
  median `duration_s`: 255 memberships 55 s, 335 230 s, 494 426 s, 706 649 s, 740 731 s.
- Cold passes (the first pass after names were added): 2026-07-18 **4,171 s** (255 memberships, 18,190
  fetches), 2026-08-05 **5,587 s** (494, 29,294), 2026-09-01 **21,632 s** (690, 46,208 — about 0.47 s per
  fetch). Today's 5,400 s run timeout would stop a cold pass that size; the per-thesis checkpoint is what
  leaves its timings behind.
- Per thesis, from the call-of-record `recorded_at` stamps of the 22 passes in which every thesis appended
  (the gap between consecutive theses' stamps — `recorded_at` is the call step's transaction start, so each
  gap is shifted by one call step; the first thesis's gap includes the benchmark refresh):
  the FIRST thesis in the loop takes a median **357 s** for its 90 names (4.0 s per name, about half the
  pass); the four other large theses (79 to 196 names) take 44 to 108 s, about 0.55 s per name, and the rest
  under 20 s each. Why the first thesis runs seven times slower per name is the first question the per-leg
  block answers.

### The tape monitor, part 2 — split-basis defects (T4, 2026-10-16)  `[BUILT]`

**The defect.** The price leg owns no corporate-action data (`ingest/prices/source.py`: no `get_splits`); it
trusts the vendor to re-base the whole history on a split, and the re-version pass mirrors whatever the
vendor serves, bar for bar. When the vendor serves a HALF-adjusted tape, or flips a bar between the adjusted
and unadjusted basis from one pull to the next, the stored latest-version tape inherits it. MEASURED on prod
(read-only, 2026-10-16): VWAV's 1-for-20 split left 09-15 = 0.532 on 4.5M and 09-16 = 0.377 on 12.7M
UNADJUSTED between adjusted bars (9.06, 7.32) — for weeks; six more basket names carry one-to-two-bar islands
at x6..x40 from 2026-07-29; MPU / SEGG / BURU flip bars x20 / x7 / x40 and back across nights; and a basis
cliff reads as a +1,800% `technical_breakout` (close/base_high >= 3 on 17 tickers in the record).

**What runs.** After the per-thesis loop and before the artifact is written, `run_daily_pass` calls
`split_basis.run_split_scan`: TWO windowed, read-only queries over the **active basket union** (every
resolved member of every non-archived thesis) — the latest-version tape, and the version history of every
bar re-versioned in the last 90 days — then pure detectors per security. Measured on prod: **~3 s** for 687
names and a 430-day window. Window = the widest price lookback on the call path (`max` of the breakout,
52-week, laggard and core-breakdown lookbacks = **430 calendar days**), read off the config: a defect inside
it can distort a detector tonight, one older cannot.

**The rule** (constants and their measured justification in the module):

| kind | shape | key thresholds |
|---|---|---|
| `island` | a boundary (adjacent closes x>=1.8 either way, both closes >= $0.02) and, within 30 bars, an inverse boundary undoing it to within x1.5 — the bars between sit on another basis than both neighbors | the island's median close differs from its flanks' (<=5 bars a side, never crossing another boundary, >= 3 bars together) by K >= 1.8, its median volume moves the OTHER way with `beta = -log V / log K` in [0.5, 3.0], each side moves less than K within itself, and both levels trade >= $1,000/day |
| `cliff` | a lone UP boundary (no inverse partner) whose new price level stays up and volume level stays down — a reverse split the vendor never applied to the history before it | the same level test, 3+ bars each side |
| `flap` | one stored bar whose consecutive versions changed basis by x>=1.8 in BOTH directions, the latest change within 90 days | a single re-base (the honest split re-version) never counts |

The inverse-volume test is what separates a basis from a move: a split divides the share count by the factor
it multiplies the price by, so dollar volume carries straight through; a genuine big move comes on a volume
spike in the SAME direction. **Lone DOWN cliffs are never flagged, by design:** a crash on heavy volume (an
offering, a delisting notice) is the same shape as an unadjusted FORWARD split — MEASURED, allowing them would
have added 19 rows, essentially all genuine crashes (AXG, BTAI, CORT, LPCN, VTGN, …). That residual
(an unadjusted forward split the vendor never re-based) is named, not missed; the flap check still catches a
vendor that keeps changing its mind.

**Precision on prod, 2026-10-16** (the branch's code, read-only, over the basket union right after that
night's 22:30 pass): **26 rows on 17 names, every one judged a true defect** — 7 islands (BURU x38, CPOP
x14, GGRP x8, LRHC x6, OPTT x30, VRME x10, VWAV x18: exactly the research's seven), 10 flaps (ARTL, BTOC,
BURU, LRHC, MPU, REAX, SEGG, VRME, VWAV, YYAI — each version history shows a flip and a flip back), 9 lone
cliffs (CPOP, GGRP, LRHC, OPTT at the same factor as their own islands; BTOC x13; DUKR x24, MI x95, OPAD
x6.6, SMX x7.4 — unadjusted reverse splits judged from the tape: a x6..x95 step on a /k volume level that
persists, NOT cross-checked against a corporate-action record, which the platform does not have). **BTOC
is the monitor's first live catch:** absent from the same scan at ~22:20 ET (24 rows), it was re-based x16
on 10-15 and that night's pull flipped its history BACK to unadjusted (0.303 -> 4.84 -> 0.303 on 09-08),
leaving a x13 cliff at its 10-14 split. Each gate is load-bearing on that data (removing it adds a
judged-false row): the tick floor (4 sub-two-cent OTC flips), the 3-bar level (4 genuine pump days), the
within-side range test (3 pump-and-fade "islands"), the $1,000 liquidity floor (1 thin-print island);
`BETA_MAX` changed nothing that night and is kept only as a physical-plausibility bound. **Known misses at
the event level** (the names are flagged anyway): BURU's 09-02 cliff and LRHC's 08-11 island (their post-split
volume fell by less than half the factor in log terms, beta < 0.5) and CPOP's 08-07 island (the island day
itself was a volume spike). The research counted 14 flapping names (method not reproduced here); within the
active basket and the 430-day window, before that night's pass, 10 names carried a both-direction version
flip at x>=1.8 and 9 flag — LIVG's are sub-two-cent tick flips, excluded by the floor (BTOC joined after the
pass). **Cross-check against the record:** 13 tickers carry a `technical_breakout` whose label puts the close
at >= 3x its base high; 7 are flagged today (BURU, CPOP, GGRP, MPU, OPTT, REAX, VRME), 4 sit on a history
re-based ONCE since (GAME, JUNS, MDCX, NXL — the mixed-basis window between a split and the vendor's re-base,
now consistent, so correctly not flagged today; their breakout factors match their re-base factors), and 2
read as genuine volume-spike moves on today's tape (JAGX 09-22 on x383 volume, WGRX's 09-16 pump).

**Where it shows, and what it never does.** The artifact carries `split_evaluated` / `split_error` /
`split_window_days` / `split_defects` (`[{security_id, ticker, kind, dates, factor, events}]`, at most the 12
newest dates per row) / `split_defects_new`; the printed report shows NEW defects loud and the known ones as
one quiet count; NEW defects page once through the R4 health event (diffed on `kind:security_id` against the
previous live pass that scanned and FINISHED (M1) — `cron_run_log.previous_split_defects`, so a killed pass
never becomes the baseline; the first pass after deploy hands the inventory over once). A `--catch-up` that
SKIPPED theses (M2) **is** a baseline here IN WHOLE — `tape_stale_new` and the name errors count it only
for the theses it covered, since their inventories are per thesis (the per-thesis carry-forward, above) —
because the scan reads the whole active basket union from the table, so a skipped thesis's names are in its
inventory. Excluding it
(the original T4 rule) re-paged a defect the catch-up had already paged, because the next full pass diffed
against the pass before the catch-up — MEASURED on a real DB with VWAV's real tape, 2026-10-17 — and kept a
defect the catch-up saw cleared in the baseline, so it stayed silent when it came back. Now a defect pages
once, and again only after a pass genuinely found it gone. `/admin/status` carries the panel
(`split_defects`, `ADMIN.md`, read from that same pass); and the V1 self-audit's
`split_defects_flagged` check passes with defects present and fails only when the scan could not run (there
is no "acknowledged" state, and failing on a standing, already-flagged defect would page every night). It is
**benign-class** — a vendor tape defect, so the Admin verdict stays about the cron. It is a **MONITOR**:
nothing it finds reaches a detector, the CallCard, or a stored bar. Whether price detectors should ABSTAIN on
a flagged name (plan Q10) and any repair of stored bars are separate, undecided steps. The two-agreeing-pulls
hysteresis on a vendor REVERT (T5) is built in the price leg itself — see "Price restatements" above; it does
not read this monitor.

### The tape monitor, part 3 — price-feed repairs: renamed, unpriceable, expected 404s (T2 + Q8 + T3, 2026-10-17)  `[BUILT]`

T1 classifies a stopped price tape **closed**, **renamed** or a **feed gap** (see "Known gaps" below). This
part turns the three readings into decisions the operator ratifies, and stops the expected ones from
spoiling `ingest_fresh`.

**The measured night it fixes (prod, 2026-10-16).** Six names failed their price leg every night with a
vendor **404** — HLLK, GTLS, XOMA, CPRX, AVNS, WONDF — and because `ingest_fresh = ingest_errors == 0`, every
row of Modern Defense, Nuclear Buildout, Rainbow Rush and The Everything Exchange was stamped
`ingest_fresh=false` every night, so the flag carried no signal. Four of the six are genuine closes; HLLK is a
rename (SDCO); WONDF (a Canadian 40-F filer, EDGAR `tickers=[]`) has never had a bar. And four renamed tapes
(HLLK → SDCO, BRR → SVIA/SVIAW, BTAI → BTAIQ, VRME → OPNW) were starving with the existing resolver
(`pipeline/resolve_price_symbols.py`) unable to reach them: OTC-only, thin-history-gated, matched on the stale
master name, never run by the cron.

**T2 — the rename recommender** (`pipeline/rename_probe.py`, operator Q7 = nightly). After the per-thesis loop,
for every stopped price row T1 marks `renamed` (deduped by security), each symbol in its `edgar_tickers` is
fetched ONCE through the `PriceSource` seam into a **throwaway temp dir** (the shared price cache is never
touched — the `repair_benchmark_bars.py` pattern) and **spliced** against our stored tape: `overlap` = the
dates both tapes carry a close on, `ratio` = the MEDIAN of candidate/ours, **verified** = overlap >= 5 and the
ratio within 1%. MEASURED on the extracted prod tapes: SDCO 30/30 shared dates x1.000000, SVIA 30 x1.000000
— and BRR's **warrants** SVIAW 30 shared dates at x0.121: **rejected**, the instrument a blind adoption of
EDGAR's list could have filed under the member. Each row carries the EDGAR evidence (current entity name,
`formerNames`, item-5.03 8-Ks — read by the ingest from the same cached submissions, zero fetches). The probe
writes NOTHING: its result rides the artifact (`rename_probe`), the printed report ("renamed — EDGAR lists
SDCO · splice-verified SDCO (30 shared bars, x1.000, bars to 2026-10-15) — Adopt on the Admin page") and the
Admin "Price feed repairs" panel. Cost: one request per distinct candidate per pass (five tonight), nothing on
a night with no renamed tape, nothing on `--no-live`. Fail-open: a candidate's failed fetch is recorded and
simply not verified; a fault reading our own tape fails the probe into `rename_probe.error`, printed loud and
shown on Admin.

**Q8 — the UNPRICEABLE class** (`tape_health.is_unpriceable`): a price row with NO stored bar ever
(`edge` null) that is neither closed nor renamed. It is surfaced **loudly** — it pages ONCE when new through
the existing newly-stale diff (R4), and stays on Admin until the operator ratifies one of: **set a price
symbol** (validated by one vendor fetch — bars must exist), **acknowledge** it ("keep it, unpriced"), or
**remove it from the basket** (a link to the existing editor; there is no second delete path). An
**acknowledged** row (an active `price_feed_ack`, migration 0046) goes QUIET like a closed row: out of the
newly-stale diff on both sides (so it never pages, and a REVOKED acknowledgment pages once more as the news it
then is), one quiet report line, a quiet count on Admin — still listed (#9). Only an unpriceable row can be
marked: an acknowledgment never silences a name that HAS bars and stopped (that is a feed gap). The
acknowledged set is read once per pass, fail-open toward LOUD (a failed read leaves every name loud).

**T3 — expected 404s** (`pipeline/price_feed.py`, operator Q9 = yes). The ingest records a FACT —
`NameResult.price_not_found`, the vendor answered 404 — and the pass judges it after the tape classification:
a 404 on a name tonight's pass classified **closed**, or that the operator **acknowledged** unpriceable, is
**expected**. It leaves the name's `error` / `leg_errors` (the error string is rebuilt from the remaining legs
exactly as the ingest builds it), so it is not a name error, does not count toward `ingest_errors`, does not
make `ingest_fresh` false and does not page; it is kept as an `ExpectedPriceMiss` on the thesis result — the
artifact's per-thesis `expected_price_misses` and ONE quiet report line — never dropped. **Still loud, on
purpose:** a renamed name not yet adopted (HLLK — one Adopt fixes it), an unpriceable name not yet
acknowledged (WONDF until the operator decides), any non-404 price failure, and every other leg's error. **The
fetch still runs for closed and acknowledged names**, deliberately: the classification is derived from the
tape, so skipping the fetch would make "closed" self-perpetuating — a relisted name, or a WONDF the vendor
starts serving, is noticed only because the bars can still land (the edge moves, the row stops being stale,
the excuse no longer applies). Eight requests a night is the price of that.

**The operator's ratifications** live on the Admin page (`ADMIN.md`, "Price feed repairs") and in two
append-only tables (0046): `price_symbol_change` (Adopt / Set / Undo — every row carries the master's PRIOR
and NEW `price_symbol`, `price_symbol_basis` and, when an Adopt refreshed it, `name`, so an Undo restores the
master byte-for-byte) and `price_feed_ack` (Acknowledge / Revoke). The writes go through the existing
`master.set_price_symbol` (basis `operator:adopt` / `operator:set`); nothing is ever applied by the pass.

**What it predicts for the next pass (MEASURED read-only on prod with this branch, 2026-10-17** — the branch
code on a `PYTHONPATH` overlay, a read-only connection, the cached submissions, the latest artifact's price
errors, and the probe's real Yahoo fetches into a throwaway dir): the probe made **5** requests in **1.5 s**
and verified all four renamed successors — HLLK → **SDCO** (49 shared dates, x1.000), BRR → **SVIA** (328,
x1.000; SVIAW rejected at x0.143), BTAI → **BTAIQ** (495, x1.000), VRME → **OPNW** (493, x1.000); WONDF reads
**unpriceable**; the 8 closes read closed. Nuclear Buildout and Rainbow Rush get `ingest_fresh=true` back
(GTLS / XOMA / CPRX / AVNS are excused as closed); Modern Defense (HLLK) and The Everything Exchange (WONDF)
stay `false` until the operator Adopts SDCO and decides WONDF.

### Known feed limitations — a vendor that serves no history (0056, 2026-10-21)  `[BUILT]`

A fourth reading of a price feed, beside closed / renamed / unpriceable — and the only one that is about a
LIVE tape. **The case (MEASURED on prod, read-only, 2026-10-21): SCE-PG**, a Southern California Edison
preferred in Nuclear Buildout. Yahoo's chart returns exactly ONE bar for it — the current session — under every
range (2y, ytd; the payload's `firstTradeDate` is 2026-07-17), so:

- its tape is built **one live bar per night** — the price leg appends the night's bar and has no overlap to
  restate against (no D1/Q11 restatement ever runs for it, so a pre-settle or provisional value is never
  corrected);
- a night the pass MISSED is a **permanent hole**: the stored tape (304 dates, 2025-07-25 → 2026-10-20, from
  when the vendor still served a history — its first pull on 2026-07-27 brought a year) lacks **8 sessions
  between 2026-08-20 and 10-20 that SPY has** (08-31, 09-01, 09-04, 09-08, 09-09, 09-29, 09-30, 10-12);
- the named-bar repair cannot fix its two pre-settle-only bars (08-27, 10-13): both read `no_vendor_bar`.

The operator chose (2026-10-21) to **mark it as a known limitation** rather than find another price source.
The mark is an append-only `price_feed_ack` row with `action = 'limited_history'` (migration 0056 widened the
action CHECK; the revoke, the pairing CHECK, the one-revoke index and the same-security trigger are 0046's),
with the operator's REQUIRED reason, basis `operator:limited-history`, and in `detail` the ONE vendor pull that
evidenced it (the Admin endpoint refuses a feed whose pull returns no bars — an unpriceable feed — or more
than 5 — a feed with a history). Undo appends the `revoke`; the undone mark stays listed on Admin with "Mark
again".

**What the mark changes, and what it does not — by construction, not by a filter.** A new `action` value (not a
`kind` column) because every existing reader of the table selects `action = 'acknowledge'`: the pass's
acknowledged-unpriceable set (Q8's quiet row) and its expected-404 excuse (T3), the Admin "acknowledged" list,
the acknowledge idempotency read. So a limitation is INVISIBLE to the quieting machinery — it cannot make a
stale row quiet or excuse a 404 even through a reader that forgets a filter. A marked name whose tape STOPS is
still the loud stale row the recency monitor makes of any name (it pages once through the newly-stale diff;
the Admin freshness card keeps it, and the limitation row says "its tape has STOPPED"), and its vendor 404 is
still a name error. **The readers that note it** are only those that would be loud SOLELY because the vendor
lacks history — found by reading every reader of vendor history:

- **the named-bar repair** (`pipeline/repair_benchmark_bars.py --bars` / `--ticker`) — its `no_vendor_bar` on
  a marked name prints as "a KNOWN FEED LIMITATION … this bar can never be repaired from it (marked <date>:
  <reason>)", the summary counts it ("no vendor bar N (M a known feed limitation)"), and the provenance's
  skipped entry carries `feed_limitation`. The outcome is unchanged (reported, never fabricated, nothing
  appended);
- **the Cockpit name panel** — a quiet "price history limited: vendor serves no history" note beside the
  thin-history flag (`ScoredMemberOut.price_feed_limitation`, the reason on hover; read live — it describes
  the feed, not a fact as-of anything; display-only, never a call input).

Checked and **left alone**, because none is loud for a no-history vendor: the D1/Q11 restatement (it compares
only the overlap the vendor serves — a one-bar pull has none and says nothing), the split-basis monitor (T4 —
it reads the STORED tape only), the settle audit (it judges rows recorded since the previous pass, so the
pre-guard SCE-PG bars never page), and the thin-history flag (SCE-PG's 304 stored dates clear its 200-bar
floor; on a marked name that is starved the flag stays — the starvation is real, and the note explains it).

### The identity monitor — a SUCCESSOR CIK (T7, 2026-10-17)  `[BUILT]`

Every filing leg reads `fetch_submissions(client, sec.cik)`, and nothing ever changes a stored CIK. A company
that re-registers under a NEW CIK but keeps its ticker — a redomicile or holding-company reorganization, the
successor issuer of Exchange Act Rule 12g-3 (it files an **8-K12B** / 8-K12G3; the predecessor files a
**15-12B / 15-12G / 15-15D**) — therefore goes silent on the insider, 8-K and 13D/G legs while its price tape
(keyed on the ticker) stays fresh, so the tape monitor never looks.

**The measured finding (prod, read-only, 2026-10-17).** UROY (Nuclear Buildout): stored 0001711570 — now
"Uranium Royalty Corp. /CA", `tickers=[]`, 15-12G 2026-07-31; the live CIK is 0002143673 (8-K12B
2026-07-28). Missed: the CEO's open-market Form 4 buy (2026-07-30), 8-Ks 08-07 / 09-15, a 13D, the 10-K /
10-K/A / 10-Q. REAX (The Everything Exchange): stored 0001862461 — `tickers=[]`, 15-12G 2026-09-04; the live
CIK is 0002136387 "Real REMAX Group Inc." (8-K12B 2026-08-24). Missed: 25 Form 4s + 6 Form 4/As, an 8-K, a
13D. Both masters still said `recent_foreign_form=40-F` / `files_domestic_forms=false`, so the app told the
operator insider flow was structurally unavailable.

**The detector** (`pipeline/successor_cik.py`, nightly, after the rename probe). For EVERY member the pass
ingested — fresh tapes included — it flags the member when BOTH hold: **(a)** the stored CIK's submissions list
no tickers (`NameResult.edgar_tickers == ()`, read by the ingest from the cached doc — zero extra fetches) and
**(b)** SEC's ticker map (`company_tickers_exchange.json`) assigns the member's `ticker` or `price_symbol` to a
DIFFERENT CIK. (a) alone also matches a closed name (its ticker leaves the map with it), a no-ticker filer that
still files (UCLE, KAYS) and a never-listed one (WONDF); (b) is what separates a successor. The evidence rides
the row as DISPLAY, never a gate (`ingest/edgar/succession.py`): the old CIK's deregistration, current entity
name and `formerNames` (re-read from the ingest's cache by a cache-only client), and the successor's 8-K12B /
8-K12G3, entity name, `formerNames` and symbols, plus its IDENTITY (`parse_identity` — the `enrich_identity`
derivation) so the operator's Adopt needs no fetch of its own. `recommended` = the one candidate (an ambiguous
map is shown, never adoptable).

- **Cost:** ONE ticker-map request through the polite cached `EdgarClient` on the RECURRING five-minute TTL
  (key `files/company_tickers_exchange.json` — a mutable class, so it is tonight's map, never the
  forever-cached copy `securities/sec_tickers` keeps for `populate_master`; prod's was from 2026-07-27, before
  either 8-K12B), only on a night some member lists no tickers, plus ONE submissions fetch per flagged
  candidate (two today). Nothing on `--no-live`.
- **Visibility:** a row whose key (security + successor CIK) is absent from the carried inventory pages ONCE
  through the R4 health event (`successor_cik_new`, benign-class like a stale feed); a known row is one quiet
  report line and stays on the artifact (`successor_cik` block) and the Admin panel. An Undo makes it news
  again (pages once). **The baseline carries each THESIS forward (2026-10-17)**, the stale-tape / name-error
  rule (`successor_cik.carried_successors` over `cron_run_log.carried_inventory`): the scan judges only the
  members a pass ingested, so an M2 `--catch-up` judges none of a skipped thesis's members. Each row names
  the ingested theses that hold its member (`rows[].theses`), and each thesis on the newest scanned pass's
  roster contributes the rows attributed to it on the newest scanned pass (live, finished, scan evaluated —
  `is_scan_pass`) that COVERED it; a skipped thesis keeps its rows from the last pass that covered it. The
  full-roster-only rule it replaces re-paged a row a catch-up first flagged at the next full pass, and kept a
  row the catch-up saw clear "already told", so its return never paged (MEASURED with the real scan over the
  measured UROY / REAX documents; the sequences are pinned in `tests/pipeline/test_successor_cik.py`). A row
  with no thesis attribution covers no thesis: it counts only when its pass is the newest, and is never
  carried (a repeat page, never a silent miss) — every T7 artifact records the attribution, and none had yet
  been written on prod when this shipped (read 2026-10-17). The Admin panel reads the same carried rows.
- **Fail-open:** any fault returns `evaluated=false` with the `error` (printed loud, shown on Admin); the
  night's record never depends on it.

**The operator's Adopt / Undo** (`ADMIN.md`, "Price feed repairs"; migration **0049** `security_cik_change`,
append-only, `no_update`, one undo per change). Adopt accepts ONLY the CIK the detector recommended, then — in
one transaction under the master row's `FOR UPDATE` lock — re-points `security_master.cik` IN PLACE through the
new narrow writer `master.set_cik` (the row id stays: the tape, every fact, the basket links and the sign-off
stay attached; NOT `populate_master`, which keys on `(cik, ticker)` and would insert a second row), applies the
recorded identity through `master.enrich` (the same derivation `enrich_identity` runs) and the successor's
entity name. The row records the PRIOR and NEW value of every touched column (`master.CIK_IDENTITY_COLUMNS`:
cik, name, the submissions-sourced identity, `enriched_source` / `enriched_at`), so Undo restores them byte for
byte (`master.restore_cik_identity`), only for the newest standing change, and 409s if the master drifted.
Adopt also refuses when the master's CIK moved since the pass, or another master row already carries the
successor CIK (that would be a row merge, which this verb does not do). The click makes no network request.

**The 8-K12B question — decided by the operator 2026-10-17: STORED on the 8-K item tape, SKIPPED by the
event detectors** (`docs/CORPORATE_EVENTS.md` §succession reports). MEASURED on the two real filings
(submissions `items`): UROY's 8-K12B carries 1.01, 2.01, 2.02, 3.01, 3.02, 3.03, 5.01, 5.02, 5.03, 7.01, 9.01;
REAX's 1.01, 2.01, 2.03, 3.01, 3.03, 5.01, 5.02, 5.03, 8.01, 9.01. In a succession report those items describe
the reorganization itself — 3.01 the listing TRANSFER (not a deficiency), 5.02 the carried-over officers,
3.02 / 3.03 the share exchange. Read as ordinary events they would change what fires: the live
`corporate_risk` detector reads 3.01 / 3.02 at 0.50 (a counter-case + setup-strength haircut on both names,
within the 180-day window today), and 5.02 is a personnel trigger (dormant behind
`corporate_catalyst_enabled=False`). So `form8k_filings` now walks `domain/edgar_forms.CORPORATE_EVENT_FORMS`
(the 8-K / 8-K/A plus the succession set 8-K12B / 8-K12G3 / 8-K15D5 and their amendments — the SAME set the
T7 reader names), the stored row carries its verbatim `form`, and both corporate detectors read through
`signals/corporate_events.event_facts`, which drops a succession row BY FORM (a plain 8-K with the same items
still fires). After the operator Adopts UROY's / REAX's successor CIKs, the next nightly pass stores each
8-K12B (plus the plain 8-Ks the re-point recovers — UROY 08-07 / 09-15, REAX 08-28) and neither 8-K12B fires
anything. The same first pass also stores the succession reports already sitting under 26 other members'
CURRENT CIKs — MEASURED read-only 2026-10-17 over the cron container's cached submissions: 29 filings
(e.g. BKKT 8-K12B 2025-11-03, ATAI 2025-12-31, BBUC 8-K12G3 2026-03-27), none within a detector window today,
and fired by nothing either way. No migration: `fact_corporate_event.form` already stores the form (NOT NULL
since 0038), and every stored row before this change is an `8-K` / `8-K/A` (MEASURED on prod the same day:
60,699 / 1,827).

**Serve-time EDGAR links after a re-point.** The CallCard's filing links are built at serve time from the
security's CURRENT CIK (`schemas_api.edgar_url` over `master.ciks_for`), so a pre-re-point filing's link
would name the successor's CIK in its path. MEASURED 2026-10-17: EDGAR resolves a filing-index page by
accession whatever the CIK segment — UROY's 15-12G index returned 200 under the old CIK, the new CIK and an
unrelated one (Apple's 320193), naming the right filer each time — so the links keep working; only the URL's
CIK segment is cosmetic. Left as a documented limitation (the 8-K rows also store their own `source_ref`
built from the CIK at ingest; Form 4 rows store the filing's `issuer_cik`). UROY and REAX hold no
old-CIK Form 4 / 8-K rows, so nothing changes for them today.

**MEASURED on prod with this branch (read-only, 2026-10-17)** — the branch's `run_successor_scan` over prod's
688 basket securities (a read-only export of the master, the cron container's cached submissions copied out,
SEC's map and the two successors fetched live into a scratch cache): 1 member without a CIK, 687 judged,
**12** list no tickers (ATAI, AVNS, CPRX, GTLS, KAYS, REAX, RMAX, SKYT, UCLE, UROY, WONDF, XOMA), **2
flagged — UROY → 0002143673 and REAX → 0002136387**, nothing else; **3** SEC requests, 2.3 s.

### The amendment leg — Form 4/A, stored, linked and resolved (R8-A, 2026-10-17)  `[BUILT]`

The Form 4 leg (`_form4_leg`) now ends with the **4/A leg** (`ingest/edgar/form4a.py`): every `4/A` in the same
cached submissions document not already stored is fetched (immutable `forms/` cache), parsed, screened (an
amendment about ANOTHER issuer is never stored — a `form4_other_issuer` skip, the same screen the Form 4 loop
now applies), linked to its original by the deterministic cascade, and appended to `insider_amendment` +
`insider_amendment_link` in the leg's transaction. It runs AFTER the night's originals, so a Form 4 and its
4/A arriving together link. Nothing is written into `fact_insider_txn`; every as-of read of it resolves the
stored rows against the amendments, links and operator decisions recorded at/before its `known_at`
(`db.insider_resolution` + the replay mirror's twin, one pure resolver — parity-gated). Dispositions and their
effect: `docs/CALL_LOGIC.md` §3 (insider). The data contract: `docs/DATA_SOURCES.md` §Form 4/A.

**Paging.** After the per-thesis loop, `pipeline/insider_amendment_scan.py` reads every amendment of the active
basket union OWED the operator's decision and diffs it against the newest live, finished pass that evaluated
it; a NEW one pages ONCE through the run's health event (benign-class: it never makes the Admin cron verdict
unhealthy). **What is owed — since R8-B (2026-10-20, §below) only what the system cannot resolve and a call can
read:** an AMBIGUOUS link, an UNRESOLVED row match (the filer's words neither fix nor add) or a HELD void
(retraction words against several trades) whose changed `P`/`S` rows sit inside the review horizon (940 days at
today's config), plus an operator-linked one awaiting its verdict and one the operator took back. Everything else
resolved to a definite original is RESOLVED BY THE SYSTEM and never pages; the report carries ONE quiet line,
"Form 4/A: N applied automatically (filer corrections), K kept as filed (their text retracts nothing), M owed
your decision (quiet)" (the "kept as filed" clause only when K > 0). (R8-A's 2026-10-18 signal-effect rule —
route a signal-touching amendment to the operator, apply one with no signal effect — survives only as the
system's BASIS: `system:no-signal-effect` vs `system:filer-correction`.) A pending void / duplicates still
suppresses its original meanwhile; the other pending kinds have no effect until applied. The inventory is read
WHOLE from the tables every pass, so the newest evaluated pass is a complete baseline — T4's split-scan rule,
not the per-thesis carry-forward (a catch-up that skipped theses read the same tables). A decision takes an
item out of the inventory; its Undo puts it back and it pages once more. Artifact block: `insider_amendments`
(`cron_run_log.build_run_payload`; R8-B adds `applied`, `swept`, `sweep_error` and a per-new-row `named`).

**Urgency — the page names every IN-WINDOW item.** An owed item is *in-window* when one of its changed signal
rows of code `c` is dated on/after `asof − horizon(c)`, the horizon being the detectors' OWN declaration (the
oldest row that can still enter that signal: `signals.horizons.insider_signal_codes` — `P` 210 days, `S` 120
days at today's config; never a copied number). Admin and the scan list in-window items first (then
buy-touching, then suppressing); the page NAMES every new in-window item (never collapsed into a count), then
— since R8-B — the other new owed items up to 8 names in all, and counts the rest as "+N more in Admin". The
page-once baseline is unchanged. **(The figures below are R8-A's, before R8-B; see §R8-B for today's.)
Re-measured read-only on
prod 2026-10-18** (the research corpus' two-year window linked against today's stored rows, the final
resolver): **67 owed a decision** — void 16, partial 19, restatement 10, field change 7, addition 6,
consolidation 4, ambiguous 4, duplicates 1; **12** touch a code-`P` row, 55 code `S` only — and **121 applied
by the system** (void 63, partial 30, addition 10, consolidation 10, restatement 8). The 7 field changes are
ETR, FRHC, RIOT, KN ×2, CRCW, SKYT (5 flip the 10b5-1 flag, 2 restate the owner's role — FRHC, CRCW). **In-window
at asof 2026-10-16: 11** — FLS and AVEX (code-`P` voids), and IEHC, FRHC, MRCY, TDG, MRAM ×3, KN
(0001196914-26-000004), SMR (code `S`). In-window means the row CAN still enter a signal, not that it moves a
card tonight (TDG's sells sit inside the 120-day horizon but outside the live sell cluster — no card moved for
them, below). The live leg reads the whole `recent` window, which can reach back further than two years for a
light filer, so the real first-night count can be somewhat higher.

**No history annotation — and the condition it rested on, MEASURED (prod, read-only, 2026-10-17).** The
operator's decision was "no annotation of past rows" ON CONDITION that no thesis-level arm would have flipped.
Method (the D3b shape): every one of the **642 honest call rows** re-assembled at its own `(asof, known_at =
recorded_at)` through the live funnel, twice over one point-in-time view — today's stored reading, and a
DIAGNOSTIC view (measurement script only, never the live path) resolving the 552 linked (security, 4/A) pairs
**as if each had been known at its SEC acceptance**, no operator decisions (voids/duplicates pending =
suppressing, corrections applied), plus the other-issuer screen. Result: **0 thesis-level arm flips**. 12 rows
change composition — all Nuclear Buildout 07-30 → 08-17, all by FLS leaving the armed set (its cancelled buy
suppressed); three of them change the headline (FLS → J on 08-05 / 08-06, FLS → FCEL on 07-30, which also reads
`flip_only` instead of `starter_entry` — that row already did not reproduce today, see below). 120 more rows
change only an insider trigger's composition (FLS, AVEX, TDG, SMR, ARBE, ATCH, AEHR); 510 are unchanged. The
other-issuer screen alone changes no thesis shape. (209 of the 642 rows do not reproduce under today's code —
code/config drift since they were recorded, D3b's "drift" class; the flip test compares the two assemblies under
the SAME current code, so drift cannot be blamed on R8-A.)

**The forward effect** (asof 2026-10-16, read-only, with vs without the resolver) — four cards change, none in
state, verdict, headline or armed set: **Nuclear Buildout** loses FLS's CORE trigger (pending void) and the SMR
insider-sell risk signal re-reads ($633,527 / 3 insiders → $352,894 / 2); **Small-scale nuclear** the same SMR
re-read; **The Everything Exchange** gains ATCH's CORE trigger (5 insiders, $162,524, the auto-applied `A`→`P`
recodes); **Modern Defense** gains ARBE's FLIP trigger (the 50→50,000-share correction). TDG's duplicate sells are
suppressed too, but they are outside tonight's sell window, so no card moves for them. Each changed card
re-records on the first pass after deploy (`record_if_changed`), so ship after a clean nightly; the first pass
also ingests every 4/A in the window (~550 document fetches) and pages its pending inventory once. The operator
then ratifies the pending voids on Admin.

### R8-B — the filer's own correction is applied by default; the operator rules only on what the system can't resolve (2026-10-20)  `[BUILT]`

**Why.** The first live night (2026-10-20) ingested the FULL 4/A history back to 2003, not the two-year research
corpus, and left **379 pending** (partial 136, void 96, ambiguous 43, addition 41, restatement 26, field change
24, consolidation 12, duplicates 1). The operator is not an SEC specialist and was clicking "yes, believe the
correction" on items with nothing to judge. A Form 4/A is the insider's OWN legal correction, filed under
penalty — deterministic data, not an LLM recommendation — so applying it is data handling, not a decision taken
from the operator (#3 / #10 untouched; the operator keeps the Undo). Rule and rationale: `CALL_LOGIC.md` §3
insider; vocabulary and the pure rules: `domain/insider_amendments.py`.

**How it runs.** (1) **At ingest** (`ingest.edgar.form4a`): every amendment the linker resolves to a definite
original gets the system's decision in the link's own transaction (one `recorded_at` — a new 4/A is never
pending): an `apply`, basis `system:filer-correction` (or `system:no-signal-effect` when it changes no `P`/`S`
row), carrying its row match (below); for a void, the reading of its words (below) — `apply` when it retracts a
single-trade original, else a `keep` (`system:no-retraction`, or `system:held` — owed). An unresolved row match
gets no decision: it is the operator's.
(2) **The SWEEP** (`pipeline/insider_amendment_scan.run_amendment_sweep` → `insider_amendment_repo.sweep`), run
by the pass BEFORE its per-thesis loop — so the first nightly after deploy already calls on the applied state —
appends the same decision for every resolvable amendment that has NO decision at all (the pre-R8-B backlog),
stamped `recorded_at = now` (a replay pinned before it reads the pending state the system held: no rewrite of
the past) — carrying the row match below. Idempotent by construction — anything ever decided on an
amendment (an operator verdict, link or
Undo; an earlier system application) is never touched, re-checked under the amendment's row lock — so a
re-run appends nothing (COUNT THE TABLE: `tests/db/test_insider_amendments.py`). Own connection, committed;
fail-open (a fault is printed loud, rides the artifact, and the backlog simply waits — voids still
suppressing). `--no-live` does not sweep. (3) **The scan** then counts the system resolutions recorded since
the pass began — the ONE quiet line — and pages only the NEW owed items. The Admin panel lists what the system
resolved ("Resolved automatically", collapsed, each with its evidence and its exact Undo — **Keep the original**
for an applied correction, **Use the correction** for a void kept as filed) and the unresolvable ones outside the
horizon ("Unresolved — historical", collapsed and grayed, still decidable); `docs/ADMIN.md`.

**The row match (operator decision 2026-10-21, revised the same day: a correction removes a buy or sale only
when it ACCOUNTS for that row).** An applied partial / consolidation / addition / restatement / field change
replaces only the original rows it accounts for (`domain.insider_amendments.match_rows`, a pure function).
Accounting is **exact shape only**, same owner first: identical; same date + code + shares (a price fix); same
date + shares, another code (a recode — BAH's `S`→`F`); same code + shares, another date (a date fix); a SUM on
the same date + code (a consolidation — BHVN's 25,000 + 3,400 restated as 28,400; or one sale split into lots —
BETR's reverse direction); then same date + code + price (a share fix — after the sums, so equal-priced lots
consolidate by their sum). A bare "same date + same code" never matches: IBKR's 4/A adds an omitted 09-19 sale
beside two real 09-19 sales, and that rule replaced both ($3,275,356). **Leftovers** — an unaccounted original
and an unmatched amendment row of the same owner AND code — are settled by **the filer's own words** (remarks +
footnotes, the phrase lists below — a deterministic match, never a model):
- **FIX** words → the amendment's rows replace those leftovers (MRAM ×5, BKSY ×4);
- **ADD** words → every original stays and the amendment's rows are added (IBKR);
- **neither** → **unresolved**: not applied, owed to the operator inside the review horizon (historical outside),
  the original standing meanwhile (YYAI). A leftover of a non-`P`/`S` code with no decisive words is simply kept
  beside the addition (no call reads it);
- **both** → FIX when, in every leftover (owner, code) group, the amendment re-reports at least as many rows as
  the original (its rows restate them all), else unresolved. **IEHC** carries both ("to correct information on
  the previous Form 4 … related to dates and prices of sales …, together with the sale of additional shares"):
  its 4/A re-reports 4 sales (09-03 / 09-04, 5,000 shares) against the original's 2 (08-13 / 08-14, 4,240) →
  FIX, both originals replaced by the 4 (the coordinator's lean, decided by this tie-break).

Every other unaccounted original row STANDS; every other unmatched amendment row is an addition. The match is
computed when the decision is written and stored on it (`replaced_rows`, migration 0053: the original row keys
it replaces, `[]` = replaces nothing); NULL — every pre-R8-B decision and the operator's **Use the correction** —
replaces the whole original, as it always did.

**The voids (operator decision 2026-10-21).** A zero-row 4/A retracts its original ONLY when BOTH (a) the filer's
own text carries explicit **retraction** language AND (b) the original holds exactly ONE transaction, a code
`P`/`S` one — the words can then mean only that trade (`void_reading`). Then the system applies it and the
original is hidden (FLS, AVEX, MNKD, DVLT ×2). **No retraction language** → the system keeps the original as filed
(`keep`, basis `system:no-retraction` — quiet, under "Resolved automatically"; SMR, AZTA, VWAV and the other
holdings / Table II / power-of-attorney fixes). **Retraction language against several transactions** → held
(`keep`, basis `system:held`): owed inside the review horizon, historical outside, the original standing meanwhile
(FIGR — "a transaction that did not in fact occur" beside a $7.43M sale). Words are never mapped to codes: FLS's
text says "sale" for a code-`P` buy. A duplicates set keeps its own handling (the later copies suppressed).

**The phrase lists** (`domain/insider_amendments.py` — `RETRACTION_PHRASES`, `FIX_PHRASES`, `ADD_PHRASES`;
case-insensitive regular expressions over the remarks + footnotes; TXN = sale | purchase | acquisition |
disposition | transaction | grant, optionally plural):
- **Retraction:** `cancel` / `canceled` / `cancelled` / `cancellation` / `canceling` ONLY within 60 characters of
  the same sentence (either order) of a TXN | `trade(s)` | `order(s)` (FLS's "previously reported sale … was
  cancelled", CZR's "cancellation of such transaction" — never "options … cancelled in exchange", an earn-out's
  "forfeited and canceled" or a merger's "each share … was canceled and converted"; "granted" is no "grant");
  `did not (in fact) occur`; `never occurred`; `no transaction(s) occurred`; `not (actually) executed |
  consummated | effected`; `should not have been filed | reported | made`; `filed | submitted in error`;
  `erroneously filed | submitted`; `erroneously reported a | an | the TXN` (the noun is required: DKNG's
  "erroneously reported 0 derivative securities" is not a retraction); `rescind(ed)`; `duplicate filing(s) |
  Form 4(s) | report(s) | submission(s)`, `filed in duplicate`, `duplicative filing(s) | report(s)` (a duplicate
  FILING only — never joint-filer "may be duplicative of the shares reported by X"); `void(ed) ab initio`; `never
  held | owned | acquired | purchased | sold`; `does not hold | own any`; `mistakenly filed | reported a | an |
  the [word] TXN`. (Tightened 2026-10-21 on review: "attributable to him | her | them" dropped as ownership
  boilerplate — MNKD still retracts on "should not have been filed". MEASURED on prod, read-only: the 96
  formerly pending voids keep the same 8 / 5 / 83 split and the sweep plan is unchanged; across all 338 stored
  voids only KITT ×3 — earn-out "forfeited and canceled", already decided under R8-A — move, held → kept as
  filed.)
- **Fix:** `correct(s|ed|ing)` followed within the sentence (≤ 120 characters, no `.`/`;`) by `date(s)` |
  `price(s)` | `number of shares sold | purchased | acquired | disposed` | `amount(s) of shares | securities sold |
  purchased | acquired | disposed` | `transaction code(s)`; `incorrectly reported` followed within 60 characters
  by `date(s)` | `price(s)` | `details` | `transaction(s)` | `sale(s)` | `purchase(s)`; `correct(s|ed|ing) a | an |
  the miscalculation` (added 2026-10-21 — ASTH `-18-000259`, "to correct a miscalculation in the number of shares
  … deemed to be beneficially owned"; MEASURED: the one stored 4/A it changes, §R8 re-derivation).
- **Add:** `inadvertent(ly) excluded | omitted | omission`; `to include` followed within 80 characters by `not
  (previously) reported` | `additional` | `omitted` | `excluded`; `to report a(n) additional | omitted`;
  `additional shares | sale(s) | purchase(s) | transaction(s)`.
- **Supersede** (the composition only — `SUPERSEDE_PHRASES`, added 2026-10-21, §R8 re-derivation below): an active
  `supersede(s) | superseding | replace(s) | replacing` (never after `not `; never the passive "superseded by")
  whose OBJECT is an earlier AMENDMENT — `the | any | all | each | such | its | their | that | those`, up to four
  words, then `amendment(s)` | `Form 4/A` | `amended Form 4` | `Form 4 amendment(s)` ("This amendment supersedes
  the amendment filed on February 11, 2020" — ONTO). "Supersedes the previously filed Form 4" names the ORIGINAL
  and is not this rule's. A date in the sentence picks the earlier amendment(s) filed or accepted that day.

Known limit, on purpose: a phrase names a transaction, never a row — which is exactly why a void with retraction
words acts only on a single-trade original, and why a leftover is only ever settled within one (owner, code)
group.

**The operator's three verdicts** on an owed item, each with its exact Undo (a `revoke` that restores the
system's resolution, or the pending state): **Use the correction** (`apply` — the amendment's rows replace the
whole original; for a void, the original is hidden), **Keep the original** (`keep`), **Add to the original**
(`apply` with `replaced_rows = []`, basis `operator:add` — every original row stays and the amendment's rows are
added; not offered for a zero-row 4/A, which has nothing to add). An operator verdict may stand over any system
decision; one over another operator verdict is refused ("undo it first").

**The review horizon** is the dial `insider_amendment_review_horizon_days` (`Settings`; unset = derived by
`signals.horizons.insider_amendment_review_horizon`): the two-year backtest window (730 days — the member price
tape's `DEFAULT_RANGE = "2y"`, so no live call, scrub-back or backtest is computed before it) plus the longest
insider read horizon a call detector declares (`P` 210) = **940 days**. An ambiguous amendment whose changed
signal rows are all older is read by no call — owing the operator a decision about it would be loudness that
marks nothing (WB#3). A widened detector widens the horizon with no edit; an unbounded one unbounds it.

**MEASURED on prod, read-only, asof 2026-10-20** (the branch's own reads over the stored links on 2026-10-21 —
the revised rule; the sweep's plan computed without writing):
- **The real-row check** — each also a test on the real filing (`tests/db/test_insider_amendments.py
  ::test_the_real_row_check_table` + `tests/domain/test_insider_amendments.py`; the documents copied read-only
  from prod's EDGAR cache, provenance in `tests/form4a_fixtures.py`):

  | 4/A | read as | the original's rows | where it sits |
  |---|---|---|---|
  | SMR 0001922054-26-000007, AZTA 0001193125-25-282945, VWAV 0002105221-26-000002 | void, no retraction words | all stand | resolved automatically — kept as filed |
  | FIGR 0001493152-25-024508 | void, retraction words, several trades | all stand | owed (held) |
  | FLS 0001628280-26-037326, AVEX 0001193125-26-166819, MNKD 0001193125-26-215284, DVLT 0001104659-24-127212 / -127204 | void, retraction words, one `P`/`S` trade | hidden | resolved automatically |
  | IBKR 0001381197-25-000139 | ADD words | 4 sales stand, the omitted 6,928-share sale added | resolved automatically |
  | MRAM 0001628280-26-047722 / -047720 / -047718 / -047716 / -047714 | FIX words | the tax sale replaced (the grant stands on -047716 / -047714) | resolved automatically |
  | BKSY 0001615454-25-000014, 0001400954-25-000012, 0001349983-25-000011, 0001443092-25-000015 | FIX words | the sale replaced (2 rows on -000015) | resolved automatically |
  | IEHC 0001174947-26-000849 | FIX + ADD words → FIX (tie-break) | both sales replaced by the 4 re-reported | resolved automatically |
  | YYAI 0002091629-25-000011 | neither | both buys stand meanwhile | owed (unresolved) |
  | BAH 0001443646-24-000094 … -000101 (8) | exact: recode `S`→`F` | the `S` replaced, the grant stands | resolved automatically |
  | MRCY 0001049521-26-000071 | exact | both sales stand, the grant added | resolved automatically (no signal effect) |
  | BETR 0001628280-24-050110 | exact: one sale = the sum of two lots | replaced by the two lots | resolved automatically |
  | BHVN 0001027035-26-000006 | exact: two lots = one consolidated row | the 2 replaced, the 2 trust lots stand | resolved automatically |

- **The revised rule over the 379 formerly pending:** the 239 matched kinds read **191 exact**, **23 FIX**
  (MRAM ×5, BKSY ×4, IEHC, ESI ×5, DKNG, TDG, ZG, …), **7 ADD** (IBKR, HRMY, TLS, URG, AUSI, CDP, an older SMR) and
  **18 unresolved** (1 inside the horizon — YYAI; 17 historical). The 96 voids: **8 retract** (FLS, AVEX, MNKD,
  DVLT ×2 inside the horizon; CVU, CZR, KTOS older), **83 kept as filed** (11 inside the horizon — AZTA, BTAI,
  CRCW, HIMS ×2, MPTI, SCHW, SMR, STEX, SUPN, VWAV), **5 held** (FIGR inside; HROW, RLBY, SMR 2023 and MRAM
  0001104659-21-045009 older — MRAM's 2021 void retracts a grant beside a sale, so it no longer removes the sale).
  Plus 43 ambiguous and 1 duplicates set.
- **The queue after the first sweep:** **owed 6** — 4 ambiguous (CVNA, FIS, HROW, SKYT), 1 unresolved match
  (YYAI), 1 held void (FIGR); none touches a row inside tonight's signal windows — **historical 60** (39
  ambiguous, 17 unresolved, 4 held), **resolved automatically 1,693** (no signal effect 1,204, filer correction
  406, kept as filed 83), **inert 1,229**. The sweep records **1,080** decisions (no signal effect 586, filer
  correction 406, no retraction 83, held 5): the 318 resolvable formerly pending plus the 762 corrections already in
  effect by their links. The first nightly's quiet line will read "~992 applied automatically (filer corrections),
  83 kept as filed …, 6 owed" (thereafter single digits); it pages nothing new — each of the 6 was in the
  2026-10-20 pass's inventory (379 pending, all paged; checked in that pass's artifact).
- **The forward call effect** (all 12 active theses re-assembled through the production funnel at asof
  2026-10-20, known_at = now: today's prod reading vs the branch's, the sweep's decisions injected in memory):
  **no state, verdict, headline, armed set or setup strength changes.** Four cards change insider-sell risk text:
  - *Nuclear Buildout* and *Small-scale nuclear*: SMR's line comes back to "3 insiders incl. senior officer sold
    $633,527 open-market (code S) across 3 txns; 1 planned-sale (10b5-1) txn ($189,800) screened" (2026-08-27,
    citing 0001922054-26-000003) from today's "2 insiders … $352,894 … across 2 txns" (2026-08-18). The cause:
    SMR 0001922054-26-000007, a zero-row 4/A with no retraction words, pending on prod today and so suppressing
    Hamady's 2026-08-27 sale (29,880 shares at $9.392 = $280,633) — kept as filed, the sale stands.
  - *Modern Defense* gains "KN: 2 insiders incl. senior officer sold $484,028 open-market (code S) across 3 txns;
    5 planned-sale (10b5-1) txns ($9,350,704) screened" (2026-08-17): KN 0001196914-26-000004, a field change —
    the director's footnote says the original "inadvertently indicated" a 10b5-1 plan; the 4/A unchecks it on
    the same two rows (6,443 + 175 shares, $259,891), so the sale is discretionary.
  - *The Everything Exchange*: FRHC's line cites the 4/A 0000929638-26-003399 in place of 0000929638-26-003183 (a
    title-only field change; the same $4,947,337 / 8 txns).

  KN and FRHC changed in every R8-B build; SMR is new with the void rule. These four re-record their content on
  the first nightly after deploy (its new as-of always appends).
- **The history counterfactual** (R8-A's method: each of the **666** honest call rows re-assembled at its own
  `(asof, known_at = recorded_at)`, every link and standing decision treated as known at its SEC acceptance — the
  R8-A rule vs the revised R8-B rule, the sweep's 1,080 decisions injected): **0 thesis-level arm flips; 0
  changes of state, verdict, headline or armed set; 179 rows change only insider-sell risk text, 487
  unchanged.** *AI Memory & Storage* 58 (07-13 → 10-02): MRAM's restated tax sales applied by their FIX words
  (07-13: "$732,573 … across 3 txns" dated 07-01 → "$757,154 … across 3 txns" dated 07-06, citing the three
  4/As); *Modern Defense* 35 (08-25 → 10-20): KN's corrected 10b5-1 flag and INFQ's addition disclosing that part
  of a fund distribution was sold; *Nuclear Buildout* 26 and *Small-scale nuclear* 26 (09-11 → 10-20): SMR's sale
  back (its void retracts nothing); *The Everything Exchange* 34 (09-01 → 10-20): FRHC's provenance. (Row match
  v1, before the filer's words: 69 rows — MRAM owed then, SMR's void still suppressing; the first R8-B build,
  before any row match: 140.)

### The R8 linker fix — an original precedes its amendment; one accounted candidate settles an ambiguous set (2026-10-21)  `[BUILT]`

**The bug (found on prod 2026-10-21).** IEHC 4/A 0001174947-26-000336 re-reports the CEO's 2025-06-30 option
exercise (M 46,217 sh at $6.00, F 29,190 sh at $9.50); its footnote names the original, "filed on July 1, 2025". It
was linked to a MARCH 2026 Form 4 (two sales, 4,650 + 350 sh). **Root cause, from the stored data:** its
`dateOfOriginalSubmission` is **2026-03-10 — its own filing date**, not July 1, 2025. Step 1 found the owner's
only Form 4 filed that day, `-000338` — which SEC accepted **49 seconds AFTER the 4/A** (21:23:37 vs 21:22:48
UTC). The July 2025 original (`0001174947-25-000979`, Table I identical) IS stored; step 2 would have found it,
but step 1 never fell through. Under R8-B the row match read "exact" (the sales kept, the M / F rows ADDED), so
the read carried the exercise twice.

**Rule 1 — an original is never made public after its amendment** (`domain.insider_amendments.made_public_after`,
both cascade steps; refused candidates ride on the link as `excluded_later`). The two instants come from ONE
source: the submissions index the 4/A leg already reads (`acceptanceDateTime`, threaded as
`accepted_by_accession`; else its `filingDate`s; unknown = kept, #9). **Not** the stored rows' `accepted`
column — MEASURED (prod, read-only): it disagrees with the index by −4 h / −5 h / −8 h on 72,290 of 117,444
stored filings of the 409 securities with a 4/A (another writer's clock), while every stored 4/A's `accepted`
equals the index exactly. With the stored column the order test would have missed VVX and AIP below.

**Rule 2 — the row tie-break** (`row_tie_break`, coordinator request): an AMBIGUOUS candidate set (either step)
links to the ONE candidate holding a row the amendment accounts for under R8-B's exact shapes (`match_rows` with
no filer words: identical, price / share / date fix, recode, sum), when every other candidate holds none;
otherwise it stays the operator's. The link keeps every candidate (`candidate_accessions`) and `tie_break: true`.

**What the fix deliberately does NOT add** (deviations from the first spec, MEASURED on prod's 2,988 stored links):
- *No "filed after the stated date" bound on step 2.* 81 step-2 links have their original filed AFTER the stated
  `dateOfOriginalSubmission` — the filer typed the TRANSACTION date (SBET, LRCX, MNKD …) or a year typo (VST
  "1998-11-13", NKTR, BLNE, TDG) — and every one shares the amendment's exact transaction dates (gap 0). The bound
  would unlink all of them. Step 2 only runs when the stated date found nothing; its evidence is the shared rows.
- *No transaction-date-gap rule on step 1.* 26 links sit > 60 days from the amendment's rows; the ones read row
  by row are correct by the filer's own words (CUBI, RDNW and AUSI name the original's filing date; ASTH rescinds
  a grant months later), and the 365 / 366-day gaps link as 1:1 corrections (RBC, PDFS, NEU, DCO, RMAX — a
  year typo fixed). The order test catches the wrong ones (IEHC, VIRT, AMSC, VVX, AIP) without a window.

**The re-link — `python -m pipeline.relink_amendments`** (dry-run by default; `--apply` takes the call-of-record
lock, appends in one transaction, counts the tables). It re-runs the fixed cascade over every stored link against
the security's stored rows and its CACHED submissions index (cache-only, no SEC request) and moves a link ONLY when
one of the two rules explains the difference (`relink_rule`; anything else is reported as drift, never moved) and
NOTHING the operator did touches the amendment. Append-only: a new `insider_amendment_link` version, a system
`revoke` (basis `system:relink`) of the standing system decision computed against the wrong original, and the
system's decision against the new one when it resolves — all at `recorded_at = now`, so a read pinned before
sees what the system held then. A re-run plans nothing. (`review` reads "the operator took it back" from an
OPERATOR decision only, so the system's own revoke never makes an amendment owed.)

**MEASURED on prod, read-only, 2026-10-21** (the branch's plan over the stored links + a read-only copy of prod's
cached submissions index; the effects with the planned rows injected in memory, nothing written):
- **The plan:** 2,988 links considered, **40 re-linked** — 12 by the order test, 28 by the tie-break; **0 drift**,
  0 operator-held, 0 securities without an index. The order test: IEHC, VIRT, AMSC, VVX and AIP (4/As dated their
  own filing day, the same-day "original" accepted after them) re-link to the true original found by step 2
  (VIRT, IEHC, AIP clerical — an emitted duplicate disappears; VVX a correction against its Dec 2018 Form 4; AMSC
  a partial against its Dec 2010 Form 4); AIN ×4 (zero-row 4/As filed 16-30 minutes BEFORE the Form 4 they were
  linked to, which their applied voids were suppressing) and ONTO find no original (inert; 24 AIN rows return);
  BHVN and GSIT (one candidate refused) leave a single original.
- **Ambiguous: 43 → 13** — 30 settle (28 by the tie-break, 2 by the order test): owed **4 → 2** (FIS → its
  withholding's filing, the code-P buy on the other candidate untouched; SKYT → the 81,401-share sale restated as
  71,693, the exercise-and-sale lines standing); historical **39 → 11** (21 now applied automatically, 7 inert).
  **CVNA and HROW stay owed:** both candidates hold an accounted row — CVNA's 28 sales match `-000271` and a
  coincidental SUM (its 96-share line = `-000273`'s two 48-share lines) matches `-000273`; HROW's sale is
  identical on `-011028` and a price-fix shape on `-000741` (its remarks name the March 20 filing — words never
  pick a filing). PROPOSED, not built: dropping sums from the tie-break settles CVNA too (31 of 43); ranking by
  most accounted rows settles 34 (adds CVNA, CTS, ENDV, SNX).
- **Forward call effect** (all 12 active theses through the production funnel at asof 2026-10-21, known_at = now,
  with vs without the planned rows): **0 card fields change** on any thesis.
- **History counterfactual** (each honest call row at its own `(asof, known_at = recorded_at)`, every 4/A record /
  link / standing decision treated as known at its SEC acceptance, today's links vs the re-linked ones): of the
  **666** honest rows, the **409** whose thesis roster (live or any snapshot) ever held a re-linked security were
  re-assembled — **all 409 identical card-for-card: 0 thesis-level flips, 0 changes of state, verdict,
  headline, armed set or any card text**; the other 257 read identical inputs by construction (the resolver is
  per-security). The `P`/`S` rows the re-link changes are SKYT's 2026-03-16 sale (81,401 → 71,693 shares; inside
  the 120-day sell window only until 2026-07-14, and no card changed) and 2008–2022 buys / sales of ASTH, AMSC,
  UAMY, LEU, STEX, GSIT and AIP; every other changed row is a non-`P`/`S` code.

**The resolver composition — every applied amendment of one original counts (2026-10-21, same branch).** The
resolver used to emit only the NEWEST applied statement per original. Under R8-B's row-level replacement that
silently lost rows: an OLDER applied amendment's `replaced_rows` still suppressed its original lines while its
own rows were dropped. `domain.insider_amendments.compose_statements` now composes them, in SEC-acceptance order
(`statement_order`: the 4/A record's `accepted` — the index clock — else its filing date, else its accession):
- a **whole** replacement (`replaced_rows` NULL — a 1:1 correction, every pre-row-match decision, the operator's
  **Use the correction**) supersedes the original AND every earlier amendment of it: the latest complete
  statement of the filing is the truth;
- a **row-level** amendment re-reports the SAME trade as an earlier one when they replace a common original line
  (an earlier whole one replaced them all); it then replaces the rows of that amendment it accounts for by R8-B's
  own rules against that amendment's standing rows (`match_rows` — exact shapes, sums, and since the composition
  fix below the filer's FIX / ADD words) — a second correction of the same trade replaces the first, never stacks
  (LFST: 10,000 → 5,000 → 10,000). Amendments of DIFFERENT trades never touch each other's rows (AMSC: two
  re-dated buys and a re-reported third all stand — without this scoping the share-fix shape would pair the
  80,000-share line with a 34,000-share one at the same date and price);
- a row-level amendment whose own words SUPERSEDE an earlier amendment replaces that amendment's rows entirely,
  whatever the scoping (the composition fix, §R8 re-derivation below).

One pure function, computed over the amendment records' full rows, for the Postgres read and the replay mirror
(parity-tested; the Postgres loader now carries the 4/A's `filed`). MEASURED on prod (read-only, 2026-10-21; main's
resolver vs this one, every security with a stored 4/A, full history at asof 2026-10-21):
- **Rows restored:** AIR `0001127602-18-001958` — G 76,738 (`-001961`), G 76,738 + **S 76,738 at $41.73**
  (`-002197`), 2018-01-15; AVAV `0001567619-23-000939` — **S 2,100 at $88.47** (`-23-001558`), 2023-01-12; COIN
  `0001415889-24-004798` — F 2,247 at $180.31 (`-24-000053`), 2024-02-20; and, after the re-link, AMSC
  `0001209191-10-061925` — **P 20,000 + P 34,000 at $28.87** (`-10-062237`), 2010-12-17. None inside the
  940-day review horizon. Of the 9 originals first flagged, the other 6 (AVAV `-21-013077`, ELTP ×2, LFST, TAYD,
  WLTH) lost nothing: each later amendment re-reports the earlier one's lines, and the composition replaces them.
  **Every other original with 2+ applied emitting amendments (31 in all) reads exactly as before**; 3 of 409
  securities' reads change (4 with the re-link), only by the rows above.
- **NULL against stored cases:** in 4 originals a later WHOLE amendment does not re-report an earlier one's
  lines, so "NULL supersedes every earlier amendment" drops them, exactly as the newest-only rule already did:
  CZR (A 33,118), BHVN (three J lines), ASTH (J 1,664,054), ONTO (A 3,901). Every one is a legacy
  `system:no-signal-effect` decision written with NULL before the row match existed (no code-P/S row). An
  applied void or duplicates set on an original that also has an applied emitting amendment (FCEL ×3, RTX) is
  outside the composition (a void emits nothing), unchanged. *(Re-measured by the re-derivation, §below: three of
  the four are legacy decisions and are re-derived; ONTO's A 3,901 is not — a 1:1 CORRECTION, whole by meaning,
  drops it — and the legacy NULLs reach far beyond these four: 277 decisions on 272 originals.)*
- **Call effect** (main's resolver on today's links vs this resolver plus the re-link): forward, the 12 active
  theses at asof 2026-10-21 — **0 card fields change**; history (the method above, every link and standing
  decision known at its SEC acceptance) — of the 666 honest rows, the **409** whose roster ever held a re-linked
  or composed security re-assembled **all identical: 0 thesis-level flips, 0 changes of any card field**; the
  other 257 read identical inputs by construction. Every restored row is outside every live signal window.

### The R8 re-derivation — the first night's whole-original system decisions get their row match (2026-10-21)  `[BUILT]`

**The leftover.** The first live night (2026-10-20) applied every resolvable 4/A BEFORE the row match existed, so
its system decisions carry `replaced_rows` NULL — and the resolver reads NULL as the WHOLE original: an original
line the amendment never mentions leaves the read, and (under the composition above) a later NULL supersedes every
earlier amendment's lines too. R8-B writes a row match on every matched disposition, so these are the only NULLs
of their kind. Rows silently vanish from the read: a recall issue (#9), on non-call codes.

**Which.** A standing SYSTEM `apply` with `replaced_rows` NULL on a MATCHED disposition — partial, consolidation,
addition, restatement, field change (`domain.insider_amendments.is_legacy_whole`). Never a 1:1 correction (whole
is its meaning), a void, a duplicates set, a keep, or an operator verdict (the operator's **Use the correction** is
whole by the operator's choice). Its row match is computed exactly as R8-B computes it at ingest — `match_for`
against the FULL stored originals with the filer's words — and its basis by the signal effect under that match.

**Only where the read changes** (`rederive_scope`, one pure function over the whole-history 4/A resolution): the
read with every legacy decision re-derived vs the read as stored. An ORIGINAL whose read changes has EVERY legacy
decision amending it re-derived (its chain then reads as R8-B composes it); a legacy decision whose originals read
the same is left exactly as it is (no churn). If re-deriving only those does not reproduce the fully re-derived
read (one amendment spanning originals), the whole security's legacy decisions are re-derived — so after an apply
the read IS the fully re-derived one and a re-run plans nothing. An UNRESOLVED match (R8-B would owe it to the
operator) is reported, never moved; an amendment the operator ever decided is never touched.

**The CLI — `python -m pipeline.rederive_amendment_matches`** (dry-run by default, on a read-only connection;
`--apply` takes the W1 repair lock — the call-of-record lock, then the fact-writer gate exclusively, exit 75 when
either is busy — appends in ONE transaction and counts `insider_amendment_decision` before
commit; `--report-json PATH` writes the plan). Append-only, the re-link's mechanics: per decision a SYSTEM `revoke`
(basis `system:rederive`) of the legacy decision and the system's `apply` carrying the row match (`via` =
`rederive`, the revoke's `detail` naming the reading and the rows), both at `recorded_at = now` — a read pinned
before it reads exactly what the system held then (#1). The review reads it as before (`auto`): the system's own
revoke is not the operator's Undo.

**The composition fix (same branch, 2026-10-21 — the coordinator's rule; first measured as a stack).** Re-deriving
the legacy decisions row by row exposed a gap in the composition: a later amendment that restates an EARLIER
AMENDMENT's lines accounts for no ORIGINAL line by exact shape, so it shared no replaced line with the earlier one
and both statements stood (ONTO `-20-000030`: `-000045`'s four lines beside `-000102`'s four — the grant twice).
`compose_statements` now (a) matches a later amendment of the same trade against the earlier amendment's STANDING
rows with R8-B's own rules — the exact shapes, the sums, and the filer's FIX / ADD words on the leftovers (the
words are new here; the shapes and sums were already applied) — and (b) reads the filer's SUPERSEDE words
(`superseded_amendments`, the phrase list in §R8-B): an amendment whose words supersede an earlier AMENDMENT
replaces that amendment's rows entirely, whatever the scoping. There is deliberately NO looser owner + date + code
rule (the IBKR-class error: an added trade on the same day never consumes a different trade); anything still
unmatched stands, and a genuine pure addition still never consumes. The words reach the composition on both engines:
the Postgres loader (`db.insider_resolution.load_inputs`) now selects `remarks` + `footnotes`, and the replay mirror
decodes `footnotes` (`replay.pit._AUX_JSON_COLS`; mirrors already export every column) — parity-tested with ONTO
and ASTH in `tests/replay/test_pit_insider_amendments.py`.

**MEASURED on prod, read-only, 2026-10-21** (the branch's dry run over the stored tables, with the composition fix
and the "correct a miscalculation" FIX phrase; the effects with the planned decisions injected in memory, nothing
written):
- **The composition fix alone changes nothing on prod as stored**: every one of the 409 securities with a 4/A
  reads row-for-row the same whole-history under main's composition and the branch's (0 rows gained or lost).
  Across all 2,988 stored 4/As the SUPERSEDE list matches ONE text, ONTO `-20-000102`; FIX / ADD words in the
  composition move no stored read.
- **The FIX phrase `correct(s|ed|ing) a | an | the miscalculation`** (§R8-B's list) changes exactly ONE stored
  amendment's row match across the 2,988 — ASTH `-18-000259`, add → fix — and no stored read (its legacy decision
  is whole). It fires on four more texts, ASTH's 2020 option-grant 4/As (`-20-046377/79/81/83`), all zero-row
  (`no_rows`, inert); the 20 other stored texts that say "miscalculat…" ("inadvertently miscalculated", "a clerical
  miscalculation" …) do not match it (`tests/domain/test_insider_amendments.py::
  test_correct_a_miscalculation_is_a_fix_and_miscalculated_alone_is_not` — LQDT `-23-024301`, unchanged).
- **The plan:** **372** legacy decisions — every one `system:no-signal-effect` (partial 213, addition 90,
  consolidation 43, restatement 26; no field change), 0 operator-touched, 0 unresolved. **276 re-derived** on 112
  securities (reading exact 236, add 35, fix 5); **96 unchanged**, left as they are — the 87 whose match accounts
  for every original line, 6 partials whose original an applied void already removes (RTX, FCEL ×3) or a later
  applied amendment already settles (AVAV ×2), ONTO's two (`-000045`, `-000102` — their SUPERSEDE words) and ASTH
  `-18-000259` (its FIX words): each one's row-level composition reads exactly what its stored whole decision reads.
  (First build: 279 re-derived, 93 unchanged; with the composition fix alone: 277 / 95.)
- **The read gains 536 rows on 271 originals and loses none** — by code A 190, M 153, F 110, J 30, D 28, C 13, G 12;
  **no `P` / `S` row**; dated 2008 → 2026-08-24. 269 originals regain only their own unaccounted lines (R8-B's rule:
  an original line the amendment does not account for STANDS — including the operator-approved clause that a
  non-`P`/`S` leftover with no decisive words is kept beside the addition); 2 also regain an earlier amendment's
  lines (the composition): CZR `-17-011774` (ten original lines + `-012327`'s A 33,118 — the test fixture) and BHVN
  `-22-007074` (24 original lines + `-007103`'s lots 249,130 / 156,750 / 9,565).
- **ONTO `-20-000030` and ASTH `-17-001361` resolve without stacking:** forced row by row, main's composition reads
  eight ONTO lines (`-000045`'s and `-000102`'s), the branch's `-000102`'s four; ASTH reads `-18-000259`'s two
  re-computed J lines, never the correction's 1,664,054 beside them — both exactly what the stored whole decisions
  read, so neither is re-derived.
- **Known, not fixed — BHVN's fourth lot:** `-22-007176` reports a 0-share, $0 J line on 2022-10-03 (an RSU
  adjustment); the share-fix shape (same owner, date, code, price $0) pairs it with `-007103`'s 46,025-share lot, so
  that lot is replaced by the 0-share line. One non-`P`/`S` row. **Considered and NOT adopted — "the share fix
  needs a nonzero price on both rows"** (MEASURED read-only over all 2,988 stored 4/As, 2026-10-21): it changes 59
  stored row matches, not one — 48 turn exact → add, keeping an original $0 line beside its restatement, among
  them genuine share corrections that would STACK again (GD `-24-005239`, "to correct the number of shares
  granted": A 4,510 → 3,605 would read both; BRR A 200,000 + 400,000; NRG's four restated withholdings beside the
  originals); 11 turn exact → fix and 10 grant lines would LEAVE (TW ×7 — the leftover group's FIX words take the
  03-15 grant though the 4/A restates the 03-18 line; CYCN ×3); 8 stored R8-B decisions would no longer match the rule
  (MRCY, ACHR, SONM, DIOD, INFQ ×2, NOC ×2), and 3 link tie-breaks would move (TER, UAVS → ambiguous; CTS →
  settled); shipped reads +47 / −10 rows on 28 securities (0 `P` / `S`). The narrower "a 0-SHARE line never pairs
  by the share fix" changes 2 matches — BHVN (right) and OSS `-26-000006` (wrong: its 0-share A line restates a
  21,000-unit grant the filer says was reported in error, which would come back) — so it is not adopted either.
- **ONTO's A 3,901** (named in §composition above) is NOT a legacy decision's: original `-20-000038`'s later 4/A
  `-000094` is a 1:1 CORRECTION, whole by meaning, so it supersedes `-000040`'s grant line — unchanged here (out of
  this repair's scope).
- **Call effect** (main — main's composition, decisions as stored — vs the branch — the fixed composition, the new
  FIX phrase and the planned re-derivations): forward, the 12 active theses at asof 2026-10-21 (known_at = now)
  through the production funnel — **0 of 12 cards change**. History (R8-A's method: each honest row at its own
  `(asof, known_at = recorded_at)`, every 4/A record, latest link and standing decision treated as known at its SEC
  acceptance): of the **666** honest rows, the **501** whose roster held ANY security with a stored 4/A were
  re-assembled — **all identical card-for-card, 0 thesis-level flips**; the other 165 read identical inputs by
  construction (the resolver is per-security).

## The scheduling sidecar — the `cron` service + `scripts/daily_cron.sh`  `[BUILT #73]`

The CLI is the **unit of work**; the sidecar is a **dumb trigger**.

- **On by default.** The `cron` service comes up with the normal `docker compose up` (no profile flag), so the
  deployed stack notifies itself with no extra command to remember. Skip it for one run with
  `docker compose up -d --scale cron=0`. *(Local dev uses `infra/docker-compose.yml` — DB only — and tests use
  pytest, so neither starts it.)* `restart: unless-stopped` — see the missing-sidecar gap in "Known gaps".
- **A failed scheduled run is RETRIED ONCE (G2a)** `[BUILT]`. A non-zero exit used to end the night: the loop
  logged `run FAILED (continuing to the next day)` and waited for tomorrow, so a transient fault (a DB
  restart mid-run, a vendor 5xx, a network blip) cost that night's call-of-record outright — and with a
  daytime row on the same day the Admin page could still read "healthy" right over the hole. Now the loop
  waits **`RETRY_DELAY_S`** (env, default **1200 s**; compose passes `ALPHADECK_CRON_RETRY_DELAY_S`) through
  the same sliced `wait_until` the schedule uses (a raw `sleep` would overshoot by a whole host suspend) and
  fires ONE `python -m pipeline.daily --catch-up --asof "$target"`.
  **What a non-zero exit MEANS (F4, 2026-09-13): the night has no call-of-record at all.** It used to be
  the count of theses that ERRORED, so a night that recorded 11 of 12 still cost a 20-minute pause and a
  retry the guard immediately no-opped, while the log read `run FAILED` over a recorded night. The CLI now
  exits 0 whenever the call step reached at least one thesis — a quiet unchanged night, a partial night
  where some theses errored, an empty roster, a deliberate `--no-live` — and non-zero only when nothing
  recorded, which is the only shape a re-run can fix. Per-thesis errors still print and still page through
  R4; the scheduler simply no longer acts on them. Two shapes reach the retry, and **`--catch-up` stays
  the guard** for both so the shell never has to know which it hit: a pass that **crashed or was killed**
  leaves a `crashed` / `running` start artifact the guard never credits (M1), so the retry runs the night —
  re-recording none of the theses that already have an on-time row (M2); a pass that completed with
  **every** thesis withheld or errored recorded nothing, so the retry runs too (that is G2b, below). The
  guard also covers the narrow case of a pass that wrote its artifact and then died after recording.
  The retry sits before the in-loop catch-up window closes and before the nightly backup, so both
  still run after it. Exactly ONE retry — a second failure is logged, paged from the shell (H3, below), and
  the loop moves on.
  **Exit 75 (C1, 2026-10-16) = "did not run — another pass holds the run lock"** (the Admin button, a hand
  `pipeline.daily`, a backfill). The shell treats it as non-zero, so it takes the same single retry under
  `--catch-up`: a no-op if the holder recorded the night after `RUN_AT`, a real pass if the holder was a
  pre-`RUN_AT` one that does not count for the night. (The shell's log line still reads `run FAILED` for it
  until the sidecar script learns the code.)
- **A sleep-loop, not a cron daemon (deliberate).** `backend/scripts/daily_cron.sh` waits until `RUN_AT` in
  the container's `TZ` — in **short slices (`SLICE_S`, default 60 s), re-reading the wall clock between
  them** rather than one long `sleep` (why: two bullets down) — skips weekends (markets closed → an
  idempotent no-op + a needless API hit), fires `python -m pipeline.daily --late-ok --asof <target>`, and a
  failed run never kills the loop. (`--late-ok`, C3: the CLI refuses an `--asof` older than the last expected
  night; the target is fixed before the sleep, so on time it IS the last expected night and the flag is
  inert — it carries only a suspend that overshoots past the NEXT weekday's `RUN_AT`, which is late by
  construction. Every other sidecar invocation is a `--catch-up`, already exempt; a static test pins both.)
  It is **not Dagster, not
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
  `pipeline/schedule.py` — keep the two in step. The run artifact carries `catch_up: true` (the Admin
  history tags the row). A catch-up used to skip the R4 freeze page outright (it ran inside the EDGAR cache's
  12h TTL and legitimately fetched ~0); since G1's five-minute recurring TTL it re-pulls every company it
  ingests, so since O5 its freeze page judges the theses it processed like any pass and leaves out only the
  ones M2 skipped.
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
- **RUN_AT stays 22:30 ET, with an accepted residual (Q19, operator decision 2026-10-17).** About 3% of
  basket members' bars for the night are still missing from the vendor at 22:30. The pass was NOT moved to a
  pre-dawn run for the prior night; that residual is **accepted**: that night's call-of-record is scored on a
  slightly incomplete tape, and the next pass's incremental tail append picks the late bars up. It is a known
  property of the record, not a fault to page on.
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
  **It waits for the DB first, retries once, and never eats tonight (2026-10-01).** Restart policies ignore
  `depends_on` — it orders only the INITIAL `up` — so after a Docker daemon restart every container comes
  back in the same second. MEASURED on prod: Docker Desktop was down 09-29..10-01; when it came back at
  23:46:19 EDT, postgres / backend / cron / frontend all started within that second, the boot catch-up for
  10-01 fired at 23:46:20 into `Connection refused` (the run and every refresh leg), logged `FAILED`, and the
  loop re-anchored to 10-02 22:30. The backend crash-looped 7× on `db.migrate` and self-healed through its
  restart policy, but the sidecar's script never exits, so nothing tried the night again — only the
  operator's "Run daily now" saved it. Now: (1) before the catch-up, `wait_for_db` polls a cheap connect
  to `db.session.database_url()` (the CLI's own `DATABASE_URL` path; `connect_timeout=5` bounds one probe)
  every 5 s via the sliced `wait_until`, for at most **`DB_WAIT_S`** (env, default **300 s**; compose passes
  `ALPHADECK_CRON_DB_WAIT_S`), logging one line when it starts waiting and one when the DB is ready or the
  bound expires — and fires the catch-up **either way** (fail-open; a DB that never comes up costs the wait,
  never the loop). Quiet when the first probe passes. (2) A boot catch-up that still exits non-zero gets the
  scheduled run's ONE G2a retry, `RETRY_DELAY_S` later, still `--catch-up`. (3) Because that block can now
  run for many minutes, every weekday that became expected **while it ran** (a boot at 22:20 whose block
  ends past 22:30) gets a `--catch-up` pass before the loop anchors — otherwise the loop computes
  `next` = tomorrow and silently skips tonight (a latent edge before this too: a boot catch-up that ran
  past `RUN_AT`). Forward from the boot target only, so never an older hole. The scheduled run polls the
  same way before it fires (a Postgres restarting at `RUN_AT` costs up to `DB_WAIT_S` of delay rather than a
  failed run plus a 20-minute retry). Exercised under dash in a `--network none` container with a stubbed
  `python` (the `CRON_EXIT_AFTER_BOOT=1` seam runs just the boot block) and with the real probe against
  real psycopg — refused, unresolvable, and a black-holed listener the 5 s connect timeout cut off.
- **Every pass is bounded (C2, 2026-10-16)** `[BUILT]`. Each `python -m pipeline.daily` the script fires
  (the scheduled run, its retry, the boot catch-up and its retry, every catch-up night) runs under
  `timeout -k 60 "$RUN_TIMEOUT_S"` (env, default **5400 s** = 90 min; compose passes
  `ALPHADECK_CRON_RUN_TIMEOUT_S`). A hung pass used to hold the loop forever: no exit code, so no retry, no
  page and no artifact, with every later night queued behind it. A timeout is exit 124 (logged as
  `TIMED OUT`), one more non-zero exit, so it takes the same retry → page path as any failure. The default
  is ~1.4× the worst measured cold pass (~65 min; a normal night is 7–15 min), so a night that onboards many
  cold names at once can still exceed it; the retry then resumes ahead, since the ingest is incremental.
  **The Admin "Run daily now" pass is bounded by the same value (2026-10-17)** `[BUILT]`: it runs in a
  child process of the backend (`pipeline/bounded_pass.py`) for at most `Settings.cron_run_timeout_s` (the
  backend's mirror of `RUN_TIMEOUT_S`, one host var), is stopped the same way past it, and — since the
  backend outlives the child — its start artifact is re-marked `crashed` with the reason and one crash page
  goes out. The run lock goes with the child's Postgres session (`ADMIN.md` §Run daily now).
- **A night that failed for good pages from the shell, with no DB (H3, 2026-10-16)** `[BUILT]`. R4 pages
  from *inside* a finished pass, so a pass that died or never connected had no voice. MEASURED: the 10-12
  run killed at 22:42 (a WSL auto-update) and the 10-01 boot catch-up lost to the cold-start race both paged
  nothing. Now the script runs `python -m notify.page --asof D --reason "…"` when the scheduled run's ONE
  retry fails, when the boot catch-up's retry fails, when a catch-up night fails (it has no retry, so its
  one attempt is final), and when a backup fails. `notify/page.py` imports nothing from `db` (a test pins
  it in a fresh interpreter), never raises, always exits 0, and sends a `HealthEvent` subclass through
  R4's own `get_notifier().notify_health`: Slack when `SLACK_WEBHOOK_URL` is set, else the LogNotifier's
  `CRON HEALTH` line. If even the notifier cannot be built (a malformed env value makes `get_settings()`
  raise), a stdlib POST to the raw webhook keeps the page alive. The reason is the script's own words, a
  date and an exit code; no thesis name can ride it. The script wraps the call in `timeout`, too. It
  covers every failure the **shell outlives**: a crashed or OOM-killed pass, a DB gone mid-run, a hang the
  C2 timeout stops, a boot that cannot reach the DB (10-01). A box that dies whole, as on 10-12 (the VM
  itself was killed), takes the shell with it; only the external heartbeat (next bullet) reports that.
- **An external heartbeat, pushed by the shell (H2, 2026-10-16)** `[BUILT — inert until configured]`.
  Nothing inside the box can report the box's own absence (MEASURED: no heartbeat code existed, and every
  outage so far lasted until a human launched Docker). Two optional env vars, both empty by default, where
  empty means a silent no-op:
  - **`HEARTBEAT_URL`**: a bare `curl -fsS -m 10 --retry 3` GET after a cron pass exits 0 for the night
    that is the **last expected as-of at that instant**: the scheduled run or its retry, the boot catch-up
    or its retry, or a catch-up night. The ping always means "the last expected night is recorded", so a
    pass that lands an *older* night (target Tue finishing after Wed's `RUN_AT`) logs `heartbeat held` and
    the newest night's catch-up pings instead. A `--catch-up` that no-ops exits 0 because its night already
    recorded, so it pings too. Just before the scheduled run the shell also pings `HEARTBEAT_URL/start`
    (healthchecks.io's start signal), so a hang reads "started, never finished".
  - **`HEARTBEAT_BACKUP_URL`**: pinged when `pipeline.backup` exits 0 (a fresh snapshot exists, dumped now
    or still under the boot's age gate).
  The **shell** sends them, not the app, so a broken import cannot fake a success. They never fire from the
  Admin "Run daily now" (it runs in the backend) or on `--no-live` (the script never passes it). A failed
  ping is one log line; no URL is ever logged (a ping URL is a credential). The suggested check is
  healthchecks.io on a `30 22 * * 1-5` America/New_York schedule with ~2 h grace (see `.env.example`).
  Creating the account is the operator's step (plan Phase 0).
- **Logs that survive a crash (O3, 2026-10-16)** `[BUILT]`. The backend image sets `PYTHONUNBUFFERED=1`,
  and compose caps the json-file log of backend, cron and frontend at 50 MB × 5. MEASURED before: the
  passes' output arrived in 60–75-line bursts about 12 min late, the buffered tail was lost when the 10-12
  run was killed, and the log had no cap. (Postgres is left uncapped for now: changing its logging config
  would make the next `up` recreate the database container.)
- No `ANTHROPIC_API_KEY` (the ingest + call engine are deterministic — no LLM on this path).

### Backfilling a missed night — `pipeline.backfill`  `[BUILT]`

When a night has no call-of-record (`/admin/status` says `gappy`; `schedule.missed_asofs` lists it),
`python -m pipeline.backfill --asof <night> --known-at <pin>` reconstructs it: the SAME `call_for_thesis`
assembly the cron runs, with the transaction clock PINNED, then `record_if_changed`. `--dry-run` first.

- **`pipeline.daily --asof <past>` is REFUSED (C3, 2026-10-16).** It used to be only a warning in these
  docs: a hand-typed `python -m pipeline.daily --asof <a night older than the last expected one>` assembled
  that night with `known_at = now` and appended a SCORED call-of-record. `main` now exits 2 before anything
  runs (no lock, no artifact, no row) with a message pointing here, unless the run is `--catch-up` (the
  sidecar's guarded catch-up), `--late-ok` (a deliberate late record — the sidecar's scheduled run passes it
  for an overshot sleep) or `--no-live` (records nothing). Today's as-of and the last expected night itself
  stay allowed (a daytime manual pass for today; the scheduled run's own target), and so does the Admin
  "Run daily now", which takes no as-of (it always runs today's).
- **Why a pin, not `pipeline.daily --asof <past>`.** The `calls` log is immutable and bitemporal — a row's
  `recorded_at` is always now — and the as-of gate is two-axis (`valid_from <= asof` AND `recorded_at <=
  known_at`, `INVARIANTS.md` #4). A naive re-run computes the past night with `known_at = now`: TODAY's
  knowledge, "what the platform says now about that night", not what it would have logged. MEASURED on dev:
  one thesis backfilled as ARMED on four missed nights while the real nightly runs around those nights recorded
  INCUBATING, because that thesis's facts arrived after them. The pinned backfill records what the cron WOULD
  have logged as far as the FACTS go — faithful to its PIN, not guaranteed identical to its recorded neighbors.
  Two things make a neighbor differ honestly: `asof` is a LIVENESS parameter, so a drifted nightly row (a
  wrong-day fire — the prior night's bars under the wake day's label) can count a filing dated on its label
  day that the intended night cannot; and a neighbor computed under the 2026-07 frozen cache understates.
  The reconstruction is the right row; the neighbors are the ones carrying label drift — read a difference
  against them with that in mind, never as a hidden transition. The two axes a pin cannot fix are below.
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
  ledger names, once, the nights it has nothing honest for). Until baskets are point-in-time, that holds for every row this
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
- **What a reconstruction changes on the ops surface.** The Admin freshness reads (`record_edge` /
  `record_first` / `recorded_asofs`, and the Scoreboard's `record_edge` line) count EVERY row, reconstructed
  included: after a backfill the night is no longer a hole, `gappy` clears, and the edge can advance. That is
  deliberate — freshness asks whether the log advanced, not whether a row is scoreable; the Scoreboard's
  record path is the ONE reader that filters (`docs/ADMIN.md`, `docs/SCOREBOARD.md`).
- **Provenance + idempotency.** One write-only, fail-open JSON per invocation under
  `data/backfills/<utc-ts>.json` (`pipeline/backfill_log.py`: `asof`, `known_at`, `known_at_policy`
  `explicit` | `next-run`, per-thesis state / verdict / recorded / error). NOT a cron run artifact —
  `already_ran_live` stays False for the night. A `--dry-run` writes NOTHING (no row, no artifact). A
  re-run with the same `asof` + `known_at` appends zero rows (count the table); a different pin that sees
  different facts is a genuine change and appends one versioned row.

### Annotating phantom arms — `pipeline.annotate_phantom_arms`  `[BUILT — D3b]`

The reconstructed rule's sibling, and its cross-reference: `calls.reconstructed` marks rows a backfill
WROTE (a fact, always filtered from scoring); `call_annotation` (migration 0045) holds JUDGMENTS made later
about honest rows, which the Scoreboard filters by POLICY (`docs/SCOREBOARD.md` §"Phantom-arm (annotated)
nights"). The first judgment is D3a's: before #390 the fundamentals read dated a re-reported comparative
quarter at its later filing, so some nightly arms rested only on a year-old revenue inflection.

- **Annotate ONLY what D3a changes.** Every honest (`NOT reconstructed`) row is re-assembled TWICE with the
  current code at `asof = row.asof`, `known_at = row.recorded_at` (the cron assembles and records in one
  transaction, so `recorded_at` — the transaction's start — is the instant its read began; the PIT's
  `db_now` pin is milliseconds later in that same transaction): (i) D3a OFF — `PreD3aDatingView`, a
  diagnostic view living only in the CLI, hands the detectors the fundamentals rows with `knowable_from` /
  `knowable_accession` replaced by the picked version's `valid_from` / `accession` (exactly the pre-#390
  read; D3a's tie-break, which only moves a same-date citation, is not reverted); (ii) D3a ON — the code as
  it ships. Both run the live funnel (`pipeline.call_for_thesis.load_call_inputs` + `assemble_from_pit`;
  `call_for_thesis(record=False)` is those two calls) over ONE prefetched view, so the second assembly costs
  no DB reads. **No dial, flag, or argument on the live path can switch D3a off**: the view is imported by
  no production module (an AST scan in `tests/pipeline/test_annotate_phantom_arms.py` pins it).
- **The classification.** `drift` — (i) does not reproduce the recorded state, verdict, AND armed-member set
  (other code/config/roster drift; reported, never annotated). `exclude` — reproduced, armed, and (ii) is not
  armed (a phantom arm). `note` — reproduced, and (ii) differs in state, verdict, headline or armed set.
  `unchanged` otherwise. Each result names the roster it ran on: the latest `basket_snapshot` at/before
  `recorded_at`, else the LIVE roster (every row before the 0043 seed on 2026-09-15) — the reproduction test
  is what keeps a live-roster row from being blamed on D3a.
- **Safety — the backfill's shape.** Dry-run is the DEFAULT, on a READ-ONLY connection (it physically cannot
  write), prints every exclude / note / drift row with its stale-trigger evidence (security, detector, the
  period, the date + filing the pre-D3a read cited, and the period's FIRST filing) and a per-thesis summary;
  `--report-json PATH` writes the full evidence and is the only file a dry run writes. `--apply` appends the
  annotations in ONE transaction (count-checked), writes `data/annotations/<utc-ts>.json`, and holds the
  call-of-record advisory lock (`pipeline/run_lock.py`; exit 75 if a daily pass or backfill holds it). A dry
  run takes no lock. Idempotent: a (call, action) its rule family (`d3a-first-knowability`, versioned
  `@<image sha>` in `rule`) already judged — active or revoked — is never judged again (count the table: a
  second `--apply` appends zero). `--revoke <id>` / `--revoke-all` + `--reason` append revokes (dry-run
  unless `--apply`). The operator runs `--apply` on prod after a backup, from the deployed image — never an
  agent.
- **Runtime (MEASURED, prod dry-run, 2026-10-16):** 630 rows in ~9–10 minutes (580 s and 554 s on two runs;
  ~1 s per row on a 79-name basket, ~3 s on the 196-name one).

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
- **And on boot, by age (M3, 2026-10-16).** The nightly dump rode only a weekday-target wake, so a night
  the sidecar caught up at **boot** got no snapshot (MEASURED: none for about 4 days around 10-12). The
  boot block now ends with `python -m pipeline.backup --if-older-than-h 20`: it dumps only when the newest
  **unlabeled** snapshot is at least 20 h old (or there is none) and otherwise prints `backup: skipped — …`
  and exits 0. A normal boot (last night's dump is hours old) adds nothing, and a crash-looping container
  dumps about once a day instead of on every restart. The in-loop dump stays **unconditional** on purpose:
  it captures the run that just finished, and no earlier dump holds that. Gating it too would let any boot
  that dumped in the ~4 h before `RUN_AT` (the prior night's dump turns 20 h old around 18:45) skip that
  night's post-run dump. The harness showed exactly that: a boot dump at 03:22 turned the 03:24 run's dump
  into a "0.0 h old" no-op. The in-loop dump now also runs after a weekend-target wake whose catch-up
  recorded a weekday (a suspend spanning the weekend), which got none before. A failed backup now pages
  (H3) and a successful one pings `HEARTBEAT_BACKUP_URL` (H2). Retention is unchanged: a gated dump is a
  `run_backup` and prunes the same way, and a skip prunes nothing. `fresh_snapshot` is the pure gate; a
  labeled dump never satisfies it, and a future-dated one fails toward dumping.
- **Retention = keep-last-N unlabeled; labeled "90 days, or the last 20" (Q16).** After a *successful*
  dump the newest `ALPHADECK_BACKUP_KEEP` (default **7**) UNLABELED snapshots are kept and older unlabeled
  ones pruned. A **labeled** dump (created with a `label`, e.g. `pre-migration`) is deleted only when it is
  BOTH older than `ALPHADECK_BACKUP_LABELED_MAX_AGE_DAYS` (default **90**) AND outside the newest
  `ALPHADECK_BACKUP_LABELED_KEEP` (default **20**) labeled dumps, so whichever rule keeps more wins; either
  dial `<= 0` turns labeled pruning off (the old keep-forever). Labeled dumps used to be kept forever:
  MEASURED 2026-10-17, 65 of them held 19.8 GB of the 22.6 GB of dumps. Every removed file is logged by
  name, at WARNING so it reaches the cron and backend logs. A **failed** dump never prunes (it must not
  shrink the safety net), and the atomic `tmp → os.replace` means a crashed dump never lists.
  `python -m pipeline.backup --prune-dry-run [--dir D]` dumps nothing and removes nothing: it prints every
  file the next prune would delete, with its size and the rule that selected it, plus the bytes freed (run
  it before a deploy that changes retention, and show the operator the list).
- **The password never reaches the log or `ps` (2026-10-17).** `pg_dump` used to get the full
  `DATABASE_URL` on its command line, so the password was in `ps` while a dump ran and in every failure
  traceback (`CalledProcessError` prints the command). It now gets the URL WITHOUT the password and the
  password as `PGPASSWORD` in its environment (`_dump_target`; URI userinfo, `?password=` and key=value DSNs
  alike); every message the backup path raises is redacted and raised without the chained subprocess error.
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
- **The R4 freeze page's false-positive path is NARROWED again by G1 — closed for the scheduled pass and
  the Admin "Run daily now", NOT for a second pass five minutes behind another.** It fires on
  `edgar_fetches == 0`, which used to be *also* what a correct run entirely inside the 12h EDGAR TTL looked
  like (all cache hits) — so the page was unreliable in the benign direction, and a reader learned to shrug
  at it. With the recurring client now on the five-minute **RECURRING TTL**, a live pass re-fetches every
  company's index whenever the previous read is older than five minutes, so for the **nightly scheduled
  pass** and the **Admin trigger** `0 fetches` means the cache genuinely never reached out — a **true**
  freeze. Still exposed, by design: **a SECOND pass started within five minutes of another** (a hand-run
  `python -m pipeline.daily` right after the nightly) legitimately reads the first pass's cache for the
  companies it reached last, and can report ~0 — a repeat page, never a silenced one. **The `--catch-up`
  exemption is NARROWED (O5, 2026-10-21):** it was wholesale (`freeze_check=not catch_up`) for the 12h-TTL
  reason G1 removed; a catch-up's retry waits 20 minutes, a boot catch-up after a killed pass processes the
  theses that pass never reached, and a late-wake catch-up follows a full pass, so the predicate now leaves
  out only the theses M2 SKIPPED (no ingest, no client) and judges the rest like any pass. Accepted residual,
  loud by construction: a catch-up whose every processed company was fetched in the previous five minutes
  (only right behind a pass killed mid-thesis, which already paged) pages FROZEN once. Option A (page on 0
  only when the cache was outside its TTL for the names touched) remains the more correct fix for that
  residual when built.
- **`edgar_fetches` counts ATTEMPTS, not successes — CLOSED (O5, 2026-10-21).** `EdgarClient.get_text` does
  `live_fetches += 1` immediately *before* calling `_fetch`, so a pull that RAISES still increments the
  counter, and the freeze verdict used to read it: *intent to fetch*, not network. Observed **2026-07-22**: an
  empty `ALPHADECK_USER_AGENT` (the stack had been rebuilt from a git worktree, where the gitignored `.env`
  does not exist, so compose resolved `${ALPHADECK_USER_AGENT:-}` to empty) raised on all 255 pulls before a
  byte left the box — and the run logged `255 EDGAR fetches`, `frozen = false`; the R2 withhold guard caught
  it (every name errored), not the detector. Now the client also counts `live_completed` (a 2xx body
  arrived; a 404 is not one), the artifact carries `edgar_fetches_completed` per thesis and per run, and the
  freeze verdict reads it — the page says "0 of N EDGAR fetches COMPLETED" when pulls were attempted.
  *MEASURED over prod's 81 stored artifacts (2026-07-17 → 2026-10-21, read-only):* the old detector fired on
  **no** night (the only zero-attempt passes were three `--no-live` passes); it was silent on three passes
  where no EDGAR data arrived — **2026-07-22 21:59 and 22:54 ET** (255 attempted, 255 of 255 names errored,
  every thesis withheld) and **2026-07-24 21:05 ET** (181 attempted, 181 names errored, and two theses with 90
  and 159 names still RECORDED their call-of-record — the partial shape the R2 withhold misses). The corrected
  rule pages FROZEN on those shapes (INFERRED for history — completions were not recorded then; 07-22 21:59's
  cause is the documented empty User-Agent, which completes none). Every stored artifact re-derives with
  its old verdict (81 of 81 MEASURED through the new Admin reader). The one stored catch-up (2026-10-13
  16:57 ET, 744 attempts) is unchanged by the narrowing.
- **A near-zero pass is not a zero pass — CLOSED by the RATIO FLOOR (operator decision 2026-10-21).** O5's
  predicate fires only at EXACTLY zero completed fetches, and the July freeze did not look like zero: MEASURED
  on the same 81 artifacts, eleven live passes made 1–4 attempts across 255–494 names (0.2–1.6% of names)
  while most theses fetched nothing — 07-17 23:00, 07-21 08:43, 07-21 22:30, 07-22 23:43, 07-23 22:52,
  07-23 23:06, 07-24 22:29, 08-17 22:30, 08-25 22:55, 08-27 16:41, 08-27 23:14 ET — all pre-G1 (12h TTL): some
  read a cache hours old (the daytime-warmth class G1 closed), some ran minutes after a full pass. Now a LIVE
  pass also pages FROZEN when **fewer than `Settings.freeze_completed_floor` (default 0.5 — 50%) of its
  EDGAR-eligible companies got fresh SEC data** (`pipeline/edgar_coverage.py`), with the page reading e.g.
  `FROZEN — the SEC feed looks starved: only 4 of 255 companies (1.6%) got fresh SEC data this pass — below
  the 50% floor`. The exact ratio:
  - **a company is a CIK** (zero-padded; two share classes or N segment rows of one issuer are one company);
  - **denominator** = the distinct CIKs whose EDGAR legs (Form 4 / 8-K / 13D/G) RAN, across the theses the
    pass PROCESSED (a `--catch-up`'s M2-skipped theses add nothing). Out: a member with no CIK (its legs
    return before any request), one missing from the master, and a company another writer held past the W1
    wait (`lock_busy` — its legs did not run here). A CLOSED or acknowledged-unpriceable name stays IN: T3 /
    Q8 excuse only its price 404, its EDGAR legs run like any other's; an ETF is in exactly when it has a CIK;
  - **numerator** = those with ≥ 1 COMPLETED EDGAR fetch (a 2xx body) while their legs ran, **anywhere in the
    pass** — each name's completions are the client's `live_completed` delta across its legs
    (`NameResult.edgar_completed`), and the pass takes the UNION over theses, because each thesis builds its
    own client and a company another thesis fetched minutes earlier is a fresh-cache hit there. MEASURED on
    the 2026-10-19 22:30 ET pass (X5 counters + the baskets' CIKs, read-only): a 14-company thesis run right
    after a 190-company one holding 13 of them made exactly 1 submissions fetch — 1 of 14 per thesis, all 14
    fresh per pass;
  - FROZEN when `fresh < floor × eligible` — strictly fewer, exactly at the floor does not page; no eligible
    company = no floor; `--no-live` is never frozen; 0 disables the floor (the zero rule still pages).

  The artifact carries the coverage and the floor at run level (`edgar_companies`, `edgar_companies_fresh`,
  `freeze_completed_floor`) plus a per-thesis diagnostic pair (its companies, and how many were fresh anywhere
  in the pass). The Admin history re-judges the floor from those keys **with the floor that pass recorded**,
  so changing the setting never recolors a past night; an artifact without them (every one written before
  the floor, O5's included) keeps the rule it was paged under. *MEASURED separation, prod's 81 stored
  artifacts (2026-10-21, read-only):* none carries per-company completions (no stored artifact was written
  by O5 or later code), so the history is read through the proxy they DO hold — the run's EDGAR fetch
  ATTEMPTS over its basket ROWS, the reading the "≥ 96% since G1" figure was made in (attempts include Form 4
  documents and companyfacts; rows count a multi-segment or multi-thesis company more than once). At 50% that
  proxy flags **twelve passes, every one pre-G1** — the eleven above plus 2026-07-31 22:30 ET (36 / 335 =
  10.7%; its small theses made 0 attempts against 1 the night before) — and **none since G1**: the lowest
  of the 25 live passes since G1 is 2026-10-13 22:30 ET at 710 / 740 = 95.9%. Not flagged by the proxy,
  pre-G1: 07-24 21:05 ET (71.0% — but every EDGAR-fetching name errored, so its completions were 0 and the
  zero rule pages it; INFERRED, as above) and 07-24 21:18 ET (74.9%). For the July passes the proxy is a BOUND: each completed fetch serves at most one company, so ≤ 4
  of ~255 companies were fresh. *Per company, MEASURED directly once:* the 2026-10-20 22:30 ET pass rewrote
  the submissions index of 686 of its 687 eligible companies DURING the pass (the cache's file times; the
  687th was re-fetched since, so unknown; 0 were older than the pass) — a healthy pass sits near 100%.
  Residual, loud by construction (as for the zero rule): a pass whose companies were mostly fetched in the
  five minutes before it (only a catch-up right behind a killed pass) reads them as cache hits and can page
  once — a repeat page, never a silenced freeze.
- **A missing run doesn't page (dead-man's switch) — BUILT (H2, 2026-10-16), inert until the operator
  creates the check.** The sidecar now pings `HEARTBEAT_URL` from the shell once the last expected night is
  recorded (the sidecar section), so an outside service alerts when the ping does not come: a host that
  stays off, a sidecar that never boots, a `docker compose stop` nobody undid. Until `HEARTBEAT_URL` is set
  it is a silent no-op and the history below still describes prod. The original account:
  `restart: unless-stopped` restarts the
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
  absence). Unchanged, still open. **Narrowed again by G2:** a pass that FIRED and failed is now retried
  once, and a night whose only row is a pre-`RUN_AT` one is listed as a hole instead of reading covered — so
  the remaining uncovered case is precisely "nothing fired at all", which is the heartbeat's job.
- **A failed scheduled run, and a daytime row hiding the failed night — CLOSED (G2).** Two halves of one
  blind spot. (a) The sidecar caught the CLI's non-zero exit, logged it, and waited for tomorrow, so a
  transient fault cost the night outright; it now retries once after `RETRY_DELAY_S` via `--catch-up` (see
  the sidecar section) — **and since F4 (2026-09-13) that exit code means "the night has no record",
  not "some thesis errored"**, so the retry fires on a genuine total miss instead of pausing the loop 20
  minutes for a night that recorded 11 of 12 theses. (b) The `--catch-up` guard credited any live post-`RUN_AT` artifact regardless of
  health, so a pass that completed with everything withheld blocked both the retry and a later boot
  catch-up; it now requires that the call step RECORDED for some thesis. (c) The Admin hole check counted any
  row for the as-of, so a pre-open "Run daily now" row covered a night whose post-close pass failed; a night
  is now covered only by a row recorded at/after that night's `RUN_AT` in market time, and a daytime-only
  night is listed with a distinct mark (`ADMIN.md` §what "covered" means). **Still open, by design:** the
  retry is ONE attempt, and a multi-night host-off outage still reruns only the last expected night on boot
  (never older holes — a deploy must not silently backfill history); `pipeline.backfill` with a pinned
  `known_at` remains the operator's tool for an older hole.
- **A boot catch-up lost to the cold-start DB race — CLOSED (2026-10-01).** R6 fired exactly once, at
  boot, and restart policies ignore `depends_on`, so after a daemon restart the sidecar booted in the same
  second as Postgres, its catch-up hit `Connection refused`, and nothing tried that night again (MEASURED on
  prod 10-01; the operator's "Run daily now" saved it). Now the boot waits up to `DB_WAIT_S` for a connect,
  retries a failed catch-up once, and catches up any night the boot block itself crossed (the R6 bullet in
  the sidecar section). **Still open, by design:** a DB still down after `DB_WAIT_S` + `RETRY_DELAY_S`
  (~25 min by default) loses the boot's night under the same one-retry rule as the scheduled run (an
  operator re-run that night, else `pipeline.backfill`, is the tool), but since H3 that loss now **pages**
  instead of only logging. *(The other half, "a night the boot block catches up gets no nightly dump", is
  CLOSED by M3, 2026-10-16: the boot block ends with an age-gated dump; see "Backups & restore".)*
- **A pass that died, hung, or never connected paged nothing — CLOSED while the box stays up (H3 + C2,
  2026-10-16).** R4's `HealthEvent` pages from inside a pass that finished, so the 10-12 run killed at
  22:42 (a WSL auto-update) and the 10-01 boot catch-up lost to the cold-start race both went unpaged
  (MEASURED), and a pass that hung would have held the loop with no exit code at all. Now every pass is
  bounded by `RUN_TIMEOUT_S`, and the shell pages through the DB-free `python -m notify.page` when a night
  or a backup fails for good (the sidecar section), which covers the 10-01 shape. **Still open:** the
  10-12 shape, a box that dies whole, takes the shell with it, so no in-band page can fire; that is the
  external heartbeat's (above), which alerts only once the operator configures it. The page also needs the
  `notify` package importable. The in-process half, a wrapper so an unhandled exception inside
  `run_daily_pass` pages before it re-raises, is a separate change.
- **A killed or partial pass read "healthy" — CLOSED (M1, 2026-10-16).** MEASURED 2026-10-12: the 22:30
  pass died at ~22:42 (the host VM went down) with 4 of 12 theses recorded; it left no artifact, and
  `/admin/status` read healthy for ~18 h — the hole check credited the night because ONE row (any thesis)
  landed after `RUN_AT` (`recorded_asof_stamps` is a per-night `MAX` across all theses), and `last_run`
  showed the previous night's artifact. Now (a) every pass writes a `running` start artifact and finalizes
  it; one still `running` past the sidecar's run timeout (`ALPHADECK_CRON_RUN_TIMEOUT_S`, mirrored to the
  backend) or `crashed` is a `cron.alarms` line ("the last pass did not finish"), while a pass inside its
  timeout reads `running`; (b) every recent night is judged PER THESIS (`pipeline/night_coverage.py`, the
  self-audit's own rule): a night where some thesis in its roster has no post-`RUN_AT` row AND no
  `pipeline.backfill` reconstruction is listed PARTIAL ("2026-10-12: 4 of 12 theses") and the verdict is
  `gappy` — a backfilled thesis is resolved (the operator's answer to that hole, so after a backfill the
  hole clears and stays visible only as a quiet `reconstructed_nights` count); (c) a night with
  post-`RUN_AT` rows but NO run artifact at all alarms too. And the guard never credits a `running` /
  `crashed` artifact, so the killed night's catch-up runs — recording only the missing theses (M2).
  **By design:** a late catch-up row still counts as COVERING its night here (the pass did record it; the
  Admin partial line counts such rows as "recorded late") — but since L1 (operator Q5 = A) the Scoreboard
  never SCORES it: see "A LATE row is recorded, never scored" above.
- **A catch-up re-recorded on-time rows with next-day knowledge — CLOSED (M2, 2026-10-16; L1 2026-10-17).**
  See "A catch-up never re-records an on-time thesis" above (10-12: AI Memory & Storage's honest on-time row
  superseded at 17:04 the next day, and 8 theses recorded only late). The rows stay in the immutable log;
  the Scoreboard sets the late ones aside (L1), so the on-time row scores its night again.
- **A hand-typed `pipeline.daily --asof <past>` wrote a scored row — CLOSED (C3, 2026-10-16).** Refused
  (exit 2) unless `--catch-up` / `--late-ok` / `--no-live`; see "Backfilling a missed night".
- **The cron's log was buffered and uncapped — CLOSED (O3, 2026-10-16).** `PYTHONUNBUFFERED=1` in the
  image and a 50 MB × 5 json-file cap on backend, cron and frontend (the sidecar section).
- **A per-name feed that silently ENDED — MONITORED (G5a price tapes · F1 fund shares), the repair still
  manual.** Zero rows appended with no error is what a dead feed and a market holiday both look like, so a
  rename-starved name could sit dark
  indefinitely: no breakout, no SMA flip, no RVOL, and no price-based de-arm, while its filing feeds kept it
  warming. The nightly pass now reads each name's feed edges, flags a price tape `tape_stale_days` (default 5
  calendar days) or more behind the as-of, pages the **newly** stale ones, and the Admin panel lists the
  current inventory — so the gap is visible and attributable.
  **F1 extends the same rule to the ETF fund-shares feed**, with its own threshold
  (`fund_shares_stale_days`, default **7** — MEASURED: a healthy sleeve's sample sits 0–2 days behind the
  pass, because the primary source states the pull date exactly while the fallback stated a two-day-old
  date, so 7 clears that lag plus a long weekend plus two failed nights), its own rows (`kind`) and its own
  repair. Only ETF sleeves are judged: an equity has no samples, and a `null` edge means **stale** to the
  rule, so an ungated monitor would report every equity in every basket. One rule, one dedup, two
  thresholds; the prose for each feed lives once in `domain/feed_kinds.py`.
  **A stopped price tape is classified closed, renamed, or a feed gap** (G5b, tightened by T1 2026-10-16;
  `pipeline/tape_health.classify_stop`, derive-on-read from the submissions the Form 4 leg already cached,
  plus — for a stale name only — the Form 25 documents in its window; nothing persisted):
  - **closed** (quiet, never pages) only when a SEC delisting form (25 / 25-NSE / 15-12B / 15-12G) was
    filed within `[last bar − tape_closed_before_days, last bar + tape_closed_after_days]` (defaults 30 / 45)
    and on or before the as-of, **and** EDGAR lists no different ticker for the company. `closed_at` is the
    filing nearest the last bar that counts. A never-priced name has no last bar, so it can never be closed
    this way. **A Form 25 / 25-NSE counts only if it removed THIS security** (Q6, 2026-10-17): its primary
    document names the class removed, and one naming another class — warrants, units, rights, notes,
    preferred — is passed over for the next-nearest form. Read by `ingest/edgar/form25.py` (deterministic
    text: the 25-NSE's `<descriptionClassSecurity>`, or the issuer template's "(Description of class of
    securities)" line; the LEADING class decides, since every warrant description names the common it
    converts into) through the night's EDGAR client, only for a name already judged stale, nearest-first
    and only until a form counts. `forms/*` caches forever, so that is one fetch per Form 25 the first night
    a name goes stale and a disk read after. An **unknown** class (a paper filing, a failed fetch, an
    unrecognized description) still counts, exactly as before Q6, and prints one `warn:` line saying so.
  - **renamed** — a loud repair row marked "renamed — EDGAR lists X": EDGAR's current `tickers` holds
    neither the master `ticker` nor `price_symbol` (membership, never `tickers[0]`). Display only; nothing
    adopts the symbol.
  - **unpriceable** (Q8) — no stored bar ever, neither closed nor renamed (WONDF); loud until the operator
    sets a symbol, acknowledges it (then quiet) or removes it.
  - otherwise a plain **stale — repair** row.

  The G5b rule it replaced closed a stopped tape on ANY delisting form ever filed, dated by the newest.
  MEASURED 2026-10-16 on prod: that labeled VRME "closed" off a 2025-06-23 Form 25-NSE for its
  **warrants**, 470 days before its common stock's tape stopped (the company had become OpenWorld, OPNW);
  192 of 675 still-trading basket names carry some delisting form, and every one would have been silenced
  the night its tape stopped. Under the proximity rule the 8 genuine closes stay closed (nearest form within
  [−7, +13] days of the last bar), VRME / HLLK / BRR / BTAI become renamed repair rows, and **7** of the 192
  would still read closed if their tape stopped tonight, each on a 25-NSE filed in the last 30 days (ACHR,
  ARBE, BKKT, IONQ, KTTA, PDYN, VSTD). **Q6 opened those forms** (MEASURED 2026-10-17, each window Form 25
  fetched through `EdgarClient`): ACHR, ARBE, BKKT, IONQ, KTTA and PDYN each removed **warrants**, so none
  of them reads closed any more; VSTD's removed its **Class A Common Stock** (a Nasdaq 12d2-2(b) strike),
  so if its tape stops it still reads closed — correctly. The 8 genuine closes stay closed (every one's
  Form 25 reads "Common Stock" / "Class A common stock"); one `closed_at` moves — XOMA's nearest form, the
  2026-07-16 25-NSE, removed its **preferred** depositary shares, so its close now dates from the 2026-07-27
  15-12G. On a 316-document set of real Form 25s the reader called 228 another class, 81 the common/ADS and
  7 unknown (all paper filings). A name reclassified closed → repair is news: the newly-stale diff leaves
  closed rows out of its baseline, so it pages **once**.
  **Still open by design:** the repair is the
  operator's (set the vendor symbol override for a tape; check fund/ticker/source for a sleeve), because an
  automatic rename-follower could file another company's tape under a member — worse than a visible gap
  (#4/#6). **Ratifiable since T2/Q8/T3 (2026-10-17):** a renamed name carries a nightly splice-verified
  successor to Adopt, an unpriceable one can be Set / Acknowledged / removed, and a closed or acknowledged
  name's vendor 404 is expected — see "The tape monitor, part 3" above. Still unbuilt: the existing
  `--adopt` CLI and the finalize self-heal still write `price_symbol` without a `price_symbol_change` row (an
  Admin Undo refuses rather than clobber what they wrote).

## The count-the-table idempotency discipline (the load-bearing test pattern)

The bitemporal read **dedups** (`SELECT DISTINCT ON (natural-key) … recorded_at DESC`), so a duplicate append
**hides behind a correct read** while the table silently grows. Therefore the idempotency tests **count the
table** (`count(*)` / `list_for_thesis` length) before and after a re-run and assert it did **not grow** — a
read-based assertion would pass even as the store bloated. This is the load-bearing pattern across M2a (the
fact tables) and M2b (the calls log): `test_rerun_appends_zero_rows_count_the_table`,
`test_daily_idempotent_end_to_end_count_the_table`, and the `record_if_changed` skip-identical test. See
`CLAUDE.md` (conventions).

## What this subsystem is NOT (deferred / parked)

- **The restatement re-version** `[BUILT — Option A, 2026-07-11; tolerance + revert hysteresis Q11,
  2026-10-17]` — re-storing a stored bar when a fresh pull's value differs (Yahoo re-bases the whole history on
  a split, and the incremental `d > last` guard never refreshes old bars → a thesis living across a FUTURE
  split accumulated mixed-basis bars). The operator kept Yahoo + re-version; which restated bars a pass writes
  is now "Price restatements" (above). A raw+splits source owning the adjustment at read time stays the
  endgame (`DATA_SOURCES.md`). Kept for its history:
  - *The original build spec (generic — no split math):* on each pull, compare the fresh value for each
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
