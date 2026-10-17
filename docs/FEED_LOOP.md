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
of another reads the first's cache for the companies it reached last — which is exactly why the R4 freeze
page keeps its `--catch-up` exemption (see "Known gaps").

The interactive paths (the Workbench, a standalone `python -m pipeline.ingest_thesis`) keep the 12h TTL.
Consequence for the operator: **"Run daily now" is safe at any hour**.

**…and `companyfacts` is FILING-GATED on the recurring pass (Q17, 2026-10-17)** `[BUILT]`. `ingest_fundamentals`
used to be the one deliberate exception to the recurring dial, keeping 12h "because the series is quarterly"
— but by 22:30 the 12h clock had always expired, so the leg re-pulled every basket name's companyfacts every
night (MEASURED: 680 documents, 1.18 GB). It now builds a recurring client, and on a recurring client the
`companyfacts/` key class is re-pulled only when uncached, when the company's cached submissions index shows
a new XBRL-bearing filing since the last pull, or after a 6.5-day backstop; a 404 is negative-cached on the
same gate (X8). The gate reads the index from disk, so it costs no SEC request — but it reads the index the
PREVIOUS pass fetched (this leg runs before the per-thesis loop), so a filing accepted today reaches the
call one pass later than before. The rule, the measured form set and the bound: `DATA_SOURCES.md`
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

**D1 — the benchmark leg's honest transaction time + a bounded restatement.** `pipeline/ingest_benchmarks.py`
was written as a ONE-SHOT historical backfill (`valid_from = recorded_at = the bar date` — the replay
contract for deep history) and the nightly leg reused it, so every nightly SPY/IWM bar claimed
`recorded_at = D 00:00 UTC`: ~26.5 h before it was ingested, before its own session opened. With no
re-version pass either, the two market-hours bars it froze (SPY 2026-08-25 765.27 vs a final 765.91; 08-27
770.82 vs 771.10; IWM the same two days) were the only versions ever held. Now:
- the **recurring path** (the nightly leg, and any pass through `run_daily_pass`) stamps `recorded_at = now`
  from the **database clock** (`db_now`, the one-clock rule); only the **CLI** (`python -m
  pipeline.ingest_benchmarks` → `historical_backfill=True`) backdates, for deep history;
- the thesis path's restatement compare (`_restated`, tolerance unchanged) runs over the vendor's newest
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

## The daily cron — `pipeline/daily.py`  `[BUILT #71]`

`run_daily(conn, *, asof=today, known_at=now, allow_live=True, force_refresh=True, notifier=None, …)`.
CLI: `python -m pipeline.daily`. For **each** thesis (`thesis_repo.list_all` — tenant intrinsic
per-thesis; **archived theses are skipped by the list's default**, the archive slice):

1. **Refresh facts** — `ingest_thesis` (incremental + fail-visible; `force_refresh=True`, the recurring path).
2. **Assemble TODAY's call WITHOUT writing** — `call_for_thesis(asof=today, known_at=now, record=False)`.
3. **Detect a MATERIAL TRANSITION** — state or verdict changed vs the call-of-record **at or before**
   this as-of →
   emit a `TransitionEvent` through the **notify seam** (`backend/notify`: a `Notifier` protocol; v1
   ships `LogNotifier` — a loud log line + the summary's TRANSITIONS block, printed only when there are
   any. DELIVERY is deferred: a channel is one adapter behind `get_notifier()`, zero cron rework). This
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
  `withheld_reason`/`edgar_fetches`/counts. `edgar_fetches` is the **freeze detector**: it counts network
  *attempts* (a frozen cache reaches out 0 times; a healthy run, thousands), so a freeze is visible in the log
  instead of hiding behind a plausible quiet night. `already_ran_live(asof, run_at, tz)` reads these logs
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
- **The health pager (R4, #199).** `assess_health` emits a `HealthEvent` through the notify seam
  (Slack via `SLACK_WEBHOOK_URL`, **fail-open**; `LogNotifier` otherwise) when a run is a **FREEZE**
  (`frozen = allow_live and theses > 0 and edgar_fetches == 0`), has **withheld** calls, has **thesis
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
  theses fresh. The split-basis baseline keeps its own rule (part 2): its scan reads the whole basket union
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
under it. The Admin button probes it (409 naming the holder). **Not covered:** the Workbench's on-promote
ingest (facts only, never `calls`) takes no lock — under an overlap with the cron it can append duplicate
identical fact versions (the bitemporal read dedups them; the table grows), unchanged from before.

**What the pass now leaves behind (Phase 1B).** Beyond the counts, the run-of-record artifact carries:
the **run identity** (`code_sha`, `config_hash`, `run_kind`) and a **non-secret env fingerprint** (`TZ`, the
market zone, `RUN_AT` / `cron_at` as values; the User-Agent, Slack webhook, Polygon key and heartbeat URL as
**set/unset booleans only**; the notifier class) — the Admin page alarms on an unknown or mismatched sha, the
LOG notifier and an unset UA; **per-name ingest errors** by leg (`name_errors`), diffed against the last-known
inventory (each thesis as the newest live pass that covered it saw it — the per-thesis carry-forward, G5a
above) so only NEWLY failing (security, leg) pairs print loud and page, the known ones collapsing to one
quiet count; and the **self-audit** (`audit`, `pipeline/self_audit.py`), whose failures page. Every
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
the pass reads the previous FINISHED, live, full-roster artifact's skip inventory
(`cron_run_log.previous_ingest_skips`); a filing that skipped the same way there is recorded but not
re-printed, and a NEW one prints its one line naming the accession. After the loop the artifact carries, per
kind, `total`, `known`, `new` (accession, security, ticker, reason), `previous_total`, `delta` (so a jump in
the known inventory shows) and the full `accessions` list (the next baseline), and stdout gets one quiet line
per kind. The first pass after the deploy has no baseline, so it prints every skip once. **Only the logging
changed**: what is stored, skipped, counted (`form4_skipped` on each thesis line) or raised is identical, a
CLI or on-promote ingest (no baseline) prints everything, and no raising path became a swallow-and-count.
The P1 `REJECT insider txn` lines (impossible transaction dates) are untouched and stay loud.

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
a flagged name (plan Q10), a two-agreeing-pulls hysteresis before re-basing (T5) and any repair of stored
bars are separate, undecided steps.

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
- **Visibility:** a row whose key (security + successor CIK) was absent from the previous baseline artifact
  (live, finished, full-roster, scan evaluated — `successor_cik.is_baseline`) pages ONCE through the R4
  health event (`successor_cik_new`, benign-class like a stale feed); a known row is one quiet report line and
  stays on the artifact (`successor_cik` block) and the Admin panel. An Undo makes it news again (pages once).
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

**The 8-K12B question — decided: NOT added to the 8-K item tape (`form8k_filings` stays `8-K` / `8-K/A`).**
MEASURED on the two real filings (submissions `items`): UROY's 8-K12B carries 1.01, 2.01, 2.02, 3.01, 3.02,
3.03, 5.01, 5.02, 5.03, 7.01, 9.01; REAX's 1.01, 2.01, 2.03, 3.01, 3.03, 5.01, 5.02, 5.03, 8.01, 9.01. In a
succession report those items describe the reorganization itself — 3.01 the listing TRANSFER (not a
deficiency), 5.02 the carried-over officers, 3.02 / 3.03 the share exchange. Admitted to the tape they WOULD
change what fires: the live `corporate_risk` detector reads 3.01 / 3.02 at 0.50 (a counter-case + setup-strength
haircut on both names, within the 180-day window today), and 5.02 is a personnel trigger (dormant behind
`corporate_catalyst_enabled=False`). That is a call-policy change, so it is left for the operator: the
proposal is to store succession 8-Ks on the tape with a form-aware cut in the detectors (stored, fired by
nothing — #9). The recall the research flagged is closed by the re-point itself: the successors' ordinary
8-Ks (UROY 08-07 / 09-15, REAX 08-28) are plain `8-K`s the existing leg reads once the CIK is adopted.

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
  `python -m pipeline.daily` right after the nightly, the retry/catch-up sequence) legitimately reads the
  first pass's cache for the companies it reached last, and can report ~0. That is precisely why the
  `--catch-up` exemption — `assess_health(freeze_check=False)`, artifact `catch_up: true` — **stays**. Option
  A (page on 0 only when the cache was outside its TTL for the names touched) remains the more correct fix
  for that residual when built. The other unreliability is the ATTEMPTS-not-successes gap below.
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
