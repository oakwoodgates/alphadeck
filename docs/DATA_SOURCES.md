# DATA_SOURCES.md

> Repo path: `docs/DATA_SOURCES.md`. Consolidates where Alpha Deck's data comes from, free vs. paid,
> and which capability each source powers. Posture: **bootstrap on free sources; pay case-by-case for the
> right thing.** Cadence is EOD (nightly batch + on-demand pulls) — no streaming/intraday in v1.

---

## Free baseline (v1 runs entirely on these)

| Source | What it gives | Powers |
|---|---|---|
| **SEC EDGAR** — submissions API, **full-text search (EFTS)**, daily index/RSS | 8-Ks, Form 4 + Form 4/A (insider; amendments resolved at read — §Form 4/A), 10-K/Q, S-1/S-3 (offerings), 13D/G, 13F | filing intelligence, insider-conviction, dilution clock, language-diff, first-footprint, **the EDGAR-first name-discovery universe** (`DISCOVERY.md`) |
| **EDGAR — XBRL financial datasets + 10-Q/10-K filing text** | structured financials (cash, burn, shares) + the filing passages | dilution clock, fundamentals (§2.2 revenue + the Band 03 S4 shares-outstanding series: three companyfacts share concepts stored `_xbrl`-keyed for the `share_creep` risk detector), **the Workbench scoring-fact extractor** (10-Q/10-K → candidate purity / shares / burn facts) |
| **EDGAR — N-1A / 485 registrations** | new fund/ETF registrations & launches | **ETF radar** (coming launches = emergence signal) |
| **SEC `company_tickers_exchange.json`** (one file: ticker → CIK → name → exchange) | the full US filer universe | **the company list** (`populate_master`, weekly in the cron sidecar) — seeds and keeps current the security master so the extract → ratify → score loop runs on any name; a rename updates the company's row in place |
| **USASpending.gov** — federal award API (`spending_by_award` + `awards/{id}`) | DOE contracts, grants, OTAs, loan guarantees + structured terms (obligation, period of performance) | **catalyst-conviction** (the automated DOE feed, §below) — the first automated Key-1 source |
| **Public ETF holdings** (issuer/fund daily holdings files) | constituents, weights, expense ratio, AUM | **ETF radar** (universe seed + holdings/flows); pure-play scoring |
| **Polygon.io reference API** (free tier, keyed — `POLYGON_API_KEY`) | ETF shares outstanding **by date** (exact counts + real history; 5 req/min) | **ETF net flow** — the primary `fact_fund_shares` source when keyed (`ingest/funds/polygon.py`; the issuer/aggregator scrapers are the keyless fallback) + the historical backfill (`pipeline.backfill_fund_shares`); and the **split reference** (`/v3/reference/splits`, whole market by date — no ticker sent) — the second source the split list confirms against (`ingest/polygon_splits.py`, `pipeline.backfill_splits`; it shares the fund leg's 5/min pacer) |
| **FINRA short interest** (bi-monthly) | SI %, days-to-cover | squeeze radar (context tier) |
| **OpenFIGI** (free API) | CIK ↔ ticker ↔ CUSIP ↔ FIGI mapping | security master / entity resolution |
| **Free EOD price/fundamentals** (e.g. Stooq/Tiingo-free tier/equivalent) | daily OHLCV, splits/dividends, market cap | momentum-health, laggard scanner, liquidity/float, corporate actions |
| **On-chain / crypto data** (free, rich) | TVL, holders, flows for crypto-adjacent themes | emergence detector *for crypto-adjacent themes only* |

ETF **flows** (inflows/outflows) can be **derived for free** from daily shares-outstanding × NAV deltas
before paying a vendor.

## Paid, case-by-case (later, only when a module needs it)

| Source | Unlocks | Note |
|---|---|---|
| **Borrow fee / utilization** (daily) | the *real* squeeze signal (leads FINRA SI) | the squeeze radar's upgrade path |
| **Options / gamma** (e.g. ORATS-class) | gamma overlay, dealer positioning | gamma squeezes; options expression |
| **Premium fundamentals** | cleaner/deeper financials, estimates | quality-of-life, not required for v1 |
| **ETF flow data** | precise creation/redemption flows | only if the derived flows prove insufficient |

Decision rule: a paid source is justified only when a module's *signal quality* materially depends on it
and the free proxy is demonstrably inadequate. Default to free + derive.

## EDGAR etiquette (a correctness requirement, not a courtesy)

- Declared **User-Agent** with contact (config/env, not hardcoded).
- Respect the documented **rate limit** — and it is a GLOBAL limit (X2, 2026-10-21). SEC's fair-access
  ceiling is 10 requests/second per egress IP, so the budget is held ONCE for every process, in Postgres
  (`ingest/sec_budget.py`, table `sec_request_budget`, migration 0058): every live SEC request (the nightly's
  fetch-ahead workers, the Workbench discovery fan-out, an on-promote ingest, a CLI, the one-GET ticker
  universes) reserves its own slot with ONE atomic upsert (`next_slot := GREATEST(next_slot, now()) + 1/rate`)
  and sleeps until it. The row lock orders concurrent reservations, so **any two SEC requests are at least
  `1/rate` apart whichever process sent them** — the cap holds by construction. `sec_global_rate_per_sec` =
  **8.5** (at most 9 request starts in any closed one-second window: headroom under 10). Before X2 each
  `EdgarClient` ran its own in-process 8/s, so the cron and a Workbench discovery together could exceed 10/s;
  the per-client 8/s (`edgar_rate_per_sec`) stays as a second, local cap.
  - **Why Postgres, not a static split:** a split (cron 5/s + backend 4/s) guarantees nothing once a third
    process talks to SEC (a CLI, the Admin "Run daily now" child, a second web worker) and idles half the budget
    at 22:30; a file lock on `/data` does not reach a host-side CLI. Postgres is where every writer already
    meets (W1's company locks live there too). Cost: one ~1 ms round trip per SEC request, on a dedicated
    per-process session (`alphadeck-sec-budget`), never a writer's connection.
  - **The fair share:** each process LANE — `recurring` (a recurring-dial client: the nightly, the radar, the
    shell sweep, the successor scan) or `interactive` (everything else) — may hold at most 2 reservations that
    are not yet due. With both lanes busy they alternate, so each gets half the budget whatever its thread
    count (MEASURED in the suite: an 8-thread lane vs a 2-thread lane split 0.50 / 0.50; without the depth
    cap the 2-thread lane got 0.20–0.22); a lane alone gets all of it; a newcomer waits at most ~0.24 s.
  - **A 429 / 503 pauses everyone:** `polite_get`'s `on_throttle` pushes the global `next_slot` out by the
    (jittered) backoff, so every process backs off together.
  - **Fail-safe toward politeness:** if Postgres cannot be reached, the request is still sent, behind a
    process-local limiter at HALF the rate, logged once per outage and counted (`sec_budget_fallback` on the
    pass meter); the database is re-tried after 30 s.
  - **Residual (accepted):** the budget is per DATABASE. Dev (`alphadeck_dev`) and prod share this host's IP
    but not a database, so a dev discovery during the prod nightly can still push the IP past 10/s — keep live
    SEC work on dev out of the 22:20–23:00 window until the VPS move (X10) gives prod its own IP. Forks never
    go live to SEC.
- **Cache-first** on disk (`data/edgar_cache/`); the test transport raises on cache miss so tests never hit the network.
- Live pulls are explicit opt-in (env flag) and write only to the cache.
- **Cache freshness is KEY-CLASSED, not permanent.** The cache is heterogeneous: a **filing document**
  (`forms/<accession>/<doc>`) is immutable — an accession never changes — and is cached **forever**; every
  other prefix is **mutable** and refreshes on a **12h TTL** when live (`submissions/*` = the filing index,
  `companyfacts/*` = XBRL financials, `efts/*` = full-text-search pages). The policy lives on the cache-key
  prefix in `EdgarClient.get_text` (default-refresh; only immutables opt out), so no caller threads a flag —
  the next mutable endpoint is safe-by-default. **Why it exists:** a permanent cache silently froze THREE legs
  the same way — Form 4 discovery (the daily cron couldn't see a filing newer than the cache; ~11 days stale),
  the discovery universe (`efts` same terms → same keys → a name that starts matching next month is invisible),
  and the extract's share counts (`companyfacts` → a stale count feeds the auto-applied market cap). Each "works
  when you test it" because a *new* key/name fetches fresh. The TTL (< the daily cron period) makes the nightly
  run always see an expired index. Offline (`allow_live=False`: tests, `--no-live`) a stale hit is still served
  — better stale than a `CacheMiss`; the TTL only forces a refetch when a live pull is permitted. The #125
  rebuild-survival win holds: a re-draft **within** 12h is still free; only a draft a day later re-enumerates,
  which is correct — the universe drifted.
- **The 12h TTL is the INTERACTIVE default; every RECURRING caller builds its client with the RECURRING TTL
  (five minutes — `RECURRING_CACHE_TTL_S`).** The 12h clock starts when *that* company's key was last fetched,
  so any daytime read warmed it for up to 12h and the nightly pass served the warm file — blind to everything
  filed after the daytime fetch (a narrower, silent version of the same freeze; "run the daytime pass before
  10:30" is no rule, because the stamp is per COMPANY and a pass takes up to an hour). So the recurring
  constructors — `pipeline/daily.py`'s per-thesis client, `radar/spac.py`, `radar/shell_sweep.py` — pass
  `cache_ttl_s=RECURRING_CACHE_TTL_S`. This is the per-CLIENT dial (the parallel of `force_refresh=True` for
  prices), **not** a per-call flag: no caller threads anything, and the key-classed policy above is untouched,
  so immutable `forms/*` still cache forever and the cost is one index fetch per company per pass.
  **Five minutes, not zero — MEASURED, not stylistic.** Within ONE pass the three filing legs (`_form4_leg` /
  `_form8k_leg` / `_schedule13_leg`) each read the SAME `submissions/CIK<10>.json` key milliseconds apart, and
  `_is_stale` is `now - mtime > ttl` — so at zero a file written milliseconds ago is already stale and those
  three reads cost **3 live fetches instead of 1** (≈1,500 SEC requests a night on a 500-name universe, for no
  freshness gain — and EDGAR politeness is a correctness requirement here, not a courtesy). Five minutes keeps
  the same-pass re-reads free *and* still closes the gap across passes: EDGAR accepts filings 06:00–22:00 ET,
  so nothing can be filed in the five minutes before a 22:30 pass, and no daytime warmth survives five minutes
  (measured: a 20-minute-old key refetches). Accepted residual: a second pass started *within* five minutes of
  another reads the first's cache for the companies it reached last. The Workbench and a standalone
  `python -m pipeline.ingest_thesis` keep the 12h TTL (interactive paths). Offline (`allow_live=False`) the dial
  is inert — a stale hit is still served, so the suite and `--no-live` are unaffected.
- **A FINISHED day's dated index is immutable too (K1, 2026-10-17).** A `daily-index/` (or `full-index/`)
  key that names a `YYYYMMDD` date — `daily-index/master.20261015.idx`, the SPAC radar's spine — is cached
  forever once the date is strictly before TODAY IN MARKET TIME (not UTC: at 22:30 ET the UTC date has
  already rolled over while tonight's index still grows) AND the cached copy was written at or after
  06:00 ET on the following day. The second half matters: the radar caches today's index at ~22:45 ET
  while it is still growing, and freezing that copy when the date turned past would drop every filing
  indexed later, so an in-day copy is refreshed once more under the TTL and only that later copy is frozen.
  Today's and future dates, undated keys (the quarterly `full-index/.../master.idx`) and impossible dates
  stay mutable. Key-classed in `EdgarClient._is_stale`, like `forms/`, for every client.
- **`companyfacts/*` is FILING-GATED on a recurring client (Q17, 2026-10-17).** This reverses the earlier
  choice that "`ingest_fundamentals` keeps the 12h TTL deliberately (a quarterly series)". That choice saved
  nothing: by 22:30 the 12h clock had always expired, so the nightly fundamentals leg re-pulled every basket
  name's companyfacts (MEASURED 2026-10-17: 680 documents, 1.18 GB) to pick up the ~38 a night that had filed
  anything. A third key class now sits between immutable and plain-mutable: on a client built with the
  recurring dial (`EdgarClient.recurring` — the dial is the marker, no second knob), a cached companyfacts is
  re-pulled only when **(a)** it is not cached, **(b)** the company's cached `submissions/CIK<10>.json` lists a
  filing ACCEPTED since the companyfacts was fetched (less a 24 h grace for SEC's XBRL processing and clock
  skew) whose form is in the measured XBRL form set, or **(c)** a 6.5-day backstop has passed. The rule, the
  form set (every distinct `form` on any fact in prod's 688 cached companyfacts files, plus an `/A` for every
  base form) and its counts live in `ingest/edgar/companyfacts_gate.py`. The gate reads the index **from disk**
  (no fetch, no `live_fetches` count), so it adds no SEC request; a missing or unreadable index, or a
  qualifying row with no date, counts as due. **X8:** a companyfacts 404 writes `<key>.404` and a recurring
  client re-asks on the same gate rather than nightly (a known absence prints as one quiet count). An
  INTERACTIVE client keeps the plain 12h TTL for companyfacts and ignores the 404 marker — the Workbench's
  freshness is unchanged. **Same-night, by placement:** the refresh runs PER THESIS inside the nightly
  loop — right after that thesis's `ingest_thesis` re-pulled its members' submissions indexes at the
  five-minute recurring TTL, and before its call is assembled — so the gate reads TONIGHT's index and a quarter
  filed today reaches tonight's call, as the old nightly re-pull did (MEASURED: a 10-Q/A accepted 17:02 ET was
  in that night's companyfacts). One pass-wide memory keeps it to one pull per company per pass, however many
  theses (or share classes) hold it. A fundamentals fault is rolled back where it runs and never costs a
  thesis its record. Anything the index cannot show (a company whose index stopped refreshing, a form that
  starts carrying facts later) is re-pulled by the backstop within 6.5 days. Simulated on prod's real filing
  cadence (45 weekday passes, 2026-08-18 → 2026-10-16): ~178 pulls a pass instead of 680 (about 26%), ~0.29 GB
  instead of 1.18 GB. (The 24 h grace means a name pulled the night of its filing is pulled once more the next
  night; that second pull is most of the difference from the ~151 a one-pass-late placement would cost.)
  The gate reads the cached files' own clocks (the companyfacts copy's fetch time, the index's acceptance
  times), never `fact_fundamentals.recorded_at` — so the stamp change below does not touch it.
- **An error reply is never cached, never counted, never served (2026-10-23).** SEC can answer with a
  success status but an ERROR BODY instead of the document — MEASURED on prod from EFTS:
  `{"errorType": "ConnectionError", "errorMessage": "getaddrinfo EBUSY ...", ...}` (817 bytes, no `hits`).
  `polite_get` only retries a non-2xx, so that body used to be written to the cache like any document, served
  for the rest of its TTL (forever, on an immutable key), and read downstream as an empty one (eight EFTS pages
  on 2026-10-23: three seeds reported "empty", five deep pages silently lost — `DISCOVERY.md` §2). Now
  `EdgarClient.get_text` checks every body, fresh or cached, before returning it: **(a)** a generic guard for
  EVERY key rejects a body whose top level is a JSON object carrying `errorType` (no real SEC document has
  one — checked against every SEC fixture in the repo; it parses only a body that starts with `{` and contains
  `"errorType"`), and **(b)** a caller may pass `validate=` (`get_json`'s receives the parsed document; EFTS
  passes its page check). A rejected body raises `InvalidBody` (a `ValueError`; EFTS's is `EftsErrorBody`):
  a FRESH one is not written to the cache and does not count in `live_completed` (it was an attempt, not
  EDGAR data reaching the pass — the O5 freeze detector must not read it as a healthy fetch), and any
  companyfacts 404 marker is left as it was; a CACHED one is a miss — re-fetched when live (even on an
  immutable or in-TTL key) and raised when not live (never served, never a silent `CacheMiss`). The
  key-classed TTL, the immutable `forms/*`, the `companyfacts/*` filing gate and the 404 negative cache are
  otherwise unchanged. One older submissions page answered this way is a counted failed page
  (`submissions._tolerable_page_error`), like an HTTP error after the retries.
- **The fundamentals' stamp (migration 0057, 2026-10-21).** `fact_fundamentals` rows carry `valid_from =` the
  filing's acceptance date — the PUBLIC clock, what every detector and every history read dates by — and, since
  0057, `recorded_at =` the database instant the ingest stored them (`ingest.fundamentals.store_quarters`, one
  `db_now` per batch): honest bookkeeping, never backdated, never the history gate. Before 0057 the ingest
  backdated `recorded_at` to `filed 00:00 UTC` (MEASURED on prod 2026-10-21: all 120,876 rows, a median ~6.2
  years before the row was actually written, because companyfacts hands over a name's whole history in one
  pull). Those rows are kept as written; under the public clock the old stamp is moot. Detail:
  `FEED_LOOP.md` §Honest stamps — the fundamentals write path.
- **The cache PERSISTS across container rebuilds** — the compose stack mounts a named volume (`appdata:/data`,
  backend + the cron sidecar) over the runtime-data home, so `data/edgar_cache/` (and the price/DOE/FIGI/SEC
  caches beside it) survives `docker compose up --build`. Before the volume, every rebuild wiped the cache and
  each Workbench draft re-enumerated EFTS fully live — a politeness cost, and run-to-run index drift at the
  hit-cap boundary of mega-terms (three same-day drafts each re-fetched ~87 pages and saw slightly different
  universes as the live index moved). Cache etiquette only works if the cache survives the deploy loop.

## Long-outage data bounds — what a resumed feed cannot recover on its own  `[KNOWN; no code closes them yet]`

The freshness rules above make a pass SEE the current window — `force_refresh=True` on prices, the key-classed
TTL on EDGAR — but neither WIDENS the window a pull reads. Every incremental leg resumes the same way: re-read a
bounded window, append what is not yet stored. So an outage longer than that window (the stack down for months;
the cron sidecar dead while the app stayed up) leaves a hole the first pass back neither fills nor reports.
Two bounds are known today, stated as facts about current behavior — nothing in the code closes either yet:

- **Prices — a fixed two-year window.** The member-name pull is `YahooPriceSource(range_=DEFAULT_RANGE)` with
  `DEFAULT_RANGE = "2y"` (`ingest/prices/source.py`; `fetch_eod`'s own bare default is `"1y"`, reached only
  by direct callers such as the symbol resolver's probe; benchmarks pull `5y` through
  `pipeline.ingest_benchmarks --range`). `ingest_bars_for_security` appends the bars newer than the tape edge
  (`latest_bar_date`) and runs the re-version / hole-fill compare over the overlap, so an outage SHORTER than
  the window self-heals on the first pass back — the whole missed tail is inside the pull. An outage LONGER
  than the window does not: the bars between the old tape edge and the window's left edge (two years before
  the resume date) are never requested, and the tape resumes with a permanent hole that the tape-recency
  monitor (§When the SYMBOL drifts, below) cannot see — it judges the EDGE, which is fresh again. The repair
  is a manual wider re-pull: `pipeline.ingest_thesis` has no range knob, so it is a hand-run
  `YahooPriceSource(range_="5y")` (or `"max"`) through `ingest_bars_for_security`, whose overlap pass then
  hole-fills. The recovered bars carry the re-pull's `recorded_at`, so a replay pinned inside the gap still
  does not see them — honest: the system did not have them then.
- **EDGAR filings — `filings.recent` only.** Every filing leg that enumerates from a company's submissions
  JSON — Form 4 (`form4_filings`), the 8-K item tape, the 13D/G tape, `pipeline.backfill_accepted` — reads
  `filings.recent` (`ingest/edgar/submissions.py`, `filings_of` and its sibling readers) and never the older
  `filings.files[]` chunks. SEC bounds `recent` at roughly the **1,000 most-recent filings of ALL form
  types** (its documentation reads "at least one year or 1,000, whichever is more" — plan on the 1,000). For
  a light filer that is many years of history — the "≥ 1 year / 1,000" depth the tape sections below quote —
  but the window is a filing COUNT, so its span in time is set by cadence, not the calendar: a heavy filer
  (a structured-note 424B issuer, an issuer with many reporting insiders) can cycle it in months. The walk is
  incremental on accession (`existing_accessions` skips what is stored), so a filing that scrolled out of
  `recent` during an outage of months is never enumerated — a **permanent-loss** bound, silent by
  construction (nothing counts what `recent` no longer lists), and unlike prices there is no wider window to
  ask for today. The fix is paging the older `filings.files[]` chunks — the deferred `--deep` walk
  `backfill_accepted` already names — a known, not-yet-built follow-up.

## USASpending (DOE awards) — the automated catalyst feed `[BUILT, #37]`

The first **automated** catalyst-conviction source (`ingest/doe/`). It discovers DOE awards for a hand-curated
set of nuclear-basket entities and emits catalyst facts deterministically (grade + horizon from the structured
terms — invariant #3, never model-sourced). Grade rule = customer-vs-sponsor (`docs/CATALYST_CONVICTION.md`).

**Entity resolution is a curated allowlist, keyed on exact `recipient_id` — never fuzzy.** This is the
load-bearing decision; the spike found three reasons it has to be:

1. **API quirk — the `recipient_id` filter is silently ignored.** Passing `recipient_id` to
   `spending_by_award` does **not** filter; it returns *all* DOE awards by size. So we can't ask the API for
   "this recipient's awards" directly.
2. **Trap — fuzzy over-match.** `recipient_search_text="Centrus"` also returns **NAC INTERNATIONAL INC.**
   (unrelated). Text search is a wide net, not a resolver.
3. **Trap — the polluted homonym.** `recipient_search_text="Oklo"` surfaces **OKLO TECHNOLOGIES, INC.** — a
   *different* recipient_id carrying **$48B of national-lab management contracts** (Sandia/LANL/ORNL `DEAC…`).
   The real awardee is **OKLO INC.** Fuzzy-matching "Oklo" onto it would pin $48B of M&O contracts to a
   pre-revenue ticker, silently.

**So: fuzzy search is only a discovery NET; the ticker is assigned solely by exact membership in the curated
table** (`ingest/doe/entities.py`), keyed on `recipient_id`. It's an **allowlist, not a denylist** — an
unknown recipient is *dropped* (unresolved), never guessed. Subsidiary → parent → ticker is encoded by giving
each recipient_id its own row: `AMERICAN CENTRIFUGE OPERATING, LLC` + `CENTRUS ENERGY CORP.` both → `LEU`;
`OKLO INC.` → `OKLO`. Both traps have **rejection tests** pointed at them
(`tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy` asserts NAC / OKLO TECHNOLOGIES / unknown
all resolve to `None`).

**Pipeline:** discover (search net, one award-type group per call — the API 422s on mixed groups) → resolve
exactly by recipient_id → fetch award detail → derive grade + base-period-of-performance horizon → emit a
`fact_catalyst`. Expired awards are emitted too (real, provenanced) but liveness keeps them off the card.

**Etiquette** (mirrors EDGAR): cache-first on disk (`data/doe_cache/`; committed fixtures in
`backend/seed_data/doe/`), live pulls explicit opt-in, a rate-limit gate, the test transport raises on a
cache miss so the suite never hits the network.

## SEC company_tickers + filing extraction — the Workbench fact loop  `[BUILT, #55–#58]`

Two SEC capabilities feed the Workbench's per-name scoring facts (`docs/WORKBENCH_EXTRACTION.md`):

- **The universe (`company_tickers_exchange.json`).** One SEC file (~10k rows, `{fields, data}` shape:
  `cik` / `name` / `ticker` / **`exchange`** — the PER-INSTRUMENT venue, Nasdaq/NYSE/CBOE/OTC/None).
  `pipeline.populate_master` (the broadener) loads it into `security_master` — **(cik, ticker)-keyed,
  idempotent, additive, per-tenant** — so the operator can resolve *any* US filer, not just the seed. **Keyed
  on `(cik, ticker)`, NOT cik-alone:** dual-class issuers share one CIK (GOOGL/GOOG, BRK-A/B), so cik-alone
  keying would collapse them into a single row — a permanent systematic gap; the ticker in the key keeps both
  pickable. *(Switched from the plain `company_tickers.json` in the canonical-primary slice: the exchange
  variant carries the per-instrument venue the canonical rank needs — ASML=Nasdaq vs ASMLF=OTC.)*
  **Canonical primary:** each CIK's ONE tradeable instrument is flagged `is_primary` at populate
  (`sec_tickers.flag_primaries` — instrument class > exchange > F-ordinary demotion > SEC file order).
  **The file's own per-CIK row order is primary-first only USUALLY** — validated 2026-07-07 against all
  1,476 multi-row CIKs: 51 violations (OTC foreign-ordinaries, warrants/units, and preferred lines listed
  first), so file order is the rank's final TIEBREAK, never the rule. Identity only (CIK + ticker + name +
  exchange + is_primary; `figi` / `cusip` left NULL — nothing in the live path reads them). The master is
  **mutable identity metadata**: a new (cik, ticker) inserts, a changed name/exchange/is_primary UPDATEs in
  place (the id stays stable, so the fact FKs don't orphan), unchanged skips. It coexists with
  `master.resolve()` (the other live writer, ticker-keyed): both set `cik`, so neither duplicates the other's
  rows (tested both orders) — and post-broadener, `resolve()`'s OpenFIGI calls fall to ~zero (the universe is
  already loaded).
  **ONE RECORD PER COMPANY — a rename updates in place (the company-list refresh, 2026-10-24).** The
  `(cik, ticker)` keying used to INSERT a second row when SEC re-tickered a company (the old row kept its primary
  flag beside the new one, and the next Workbench Save re-pointed the member to the new, empty row — every fact
  left behind; MEASURED 2026-10-24: 23 live basket companies re-tickered since the 2026-08-16 load). Now
  `pipeline.populate_master` (DRY-RUN by default; `--live --apply` writes; weekly in the cron sidecar, Saturday
  09:00 ET — `FEED_LOOP.md` §the company list) plans against the master first (`securities/master_refresh.py`):
  a CIK whose current PRIMARY row's ticker SEC no longer lists has that row RENAMED IN PLACE to the file's
  primary ticker (the id stays; the old -> new pair goes to the append-only `security_ticker_history`, migration
  0066) — also over an EMPTY same-CIK placeholder row already holding it; other new tickers on the CIK are
  inserted non-primary; at most one `is_primary` per CIK after a run (a vanished row is demoted, never left
  beside the primary; primacy moves between listed lines only on structural grounds, never on SEC's file order
  alone); a re-key onto a ticker ANOTHER CIK holds (ticker
  reuse after a delisting) is a surfaced CONFLICT, never applied — and so is a new company whose ticker a LIVE
  basket row of another CIK holds (a possible successor issuer, the T7 case below); a CIK SEC no longer lists is
  left untouched. The price feed follows a rename only when safe: a renamed row with stored bars and no price
  symbol is PINNED to its old ticker (`price_symbol`, author `system:rename-pin`, undoable on Admin) until the
  nightly rename probe splice-verifies the new ticker (>= 20 shared bars at x1.000 → adopted automatically,
  `system:auto-adopt`, undoable); a row with a price symbol already set is never touched.
  **The cached file is now REFRESHED, weekly** — the refresh's `--live --apply` replaces
  `data/sec_cache/company_tickers_exchange.json` (an atomic replace, only after its plan committed), and that
  copy is also the stale-tape rename guard's second source (`sec_tickers.cached_tickers_for_cik`, cache-only):
  a stopped tape whose CIK SEC's map lists under another ticker is RENAMED, never CLOSED (the PSKY -> SKYD
  shape — a Form 25 at the last bar, EDGAR's own list empty).
  **Funds are not in this file** (PEPT, WGMI, DTCR, …): an ETF is a fund-trust series in SEC's
  `company_tickers_mf.json`, resolved ON THE FLY at the operator's holdings click (`securities/fund_tickers.py`;
  the trust CIK is never written into the master — a locked decision, ETF Sleeve Slice 2a) and entered into the
  master only by the surface-ETF flow (`master.resolve(..., instrument_kind=ETF)`). The weekly refresh replaces
  that file's cached copy too (one more GET, fail-open), so a fund launched since the last copy resolves; it
  writes no master row for a fund.
  **The CIK itself was never revisited — until T7 (2026-10-17).** Nothing above changes a stored CIK, and the
  populate path's ticker map was cached FOREVER until the weekly refresh (`securities/sec_tickers`, cache-first;
  prod's copy dated 2026-07-27). A company that re-registers under a SUCCESSOR CIK and keeps its ticker (Rule 12g-3: an 8-K12B
  on the new CIK, a 15-12G on the old) leaves the member on a dead CIK, and every filing leg goes quiet
  (MEASURED: UROY 0001711570 → 0002143673, REAX 0001862461 → 0002136387). The nightly successor-CIK detector
  (`pipeline/successor_cik.py`, `FEED_LOOP.md` §the identity monitor) reads the SAME map fresh each night
  through the polite `EdgarClient` (key `files/company_tickers_exchange.json`, the recurring five-minute TTL)
  and flags a member whose stored CIK lists no tickers while the map assigns its symbol to another CIK. The
  re-point is the operator's (Adopt on Admin → `master.set_cik`, in place, with an exact Undo — migration
  0049); never `populate_master`, which re-keys only a row of the SAME CIK and reports a successor CIK on a
  basket member's ticker as a conflict (an inserted successor row would block this Adopt, which refuses when
  another row already holds the new CIK). Serve-time EDGAR links are built from the CURRENT CIK, which is
  harmless after a re-point: EDGAR resolves a filing index by accession under any CIK path (MEASURED).
- **Filing extraction (10-Q/10-K).** The three-tier extractor pulls a resolved name's latest 10-Q + 10-K (via
  the same cache-first `EdgarClient` + declared UA + rate-limit gate) and produces *candidate* scoring facts
  the operator ratifies — the extractor LOCATES, the operator RATIFIES. Detail in `WORKBENCH_EXTRACTION.md`;
  the etiquette is the EDGAR etiquette above.

## Free EOD prices — the price-source seam, adjustment, and cache  `[BUILT, M2a/the price-source slice]`

The EOD price source feeds `volume_breakout` (Key 2) and the Workbench market-cap figure.

- **Current source: Yahoo's chart API** (`query1.finance.yahoo.com/v8/finance/chart`) — free, no key, but
  **unofficial / uncontracted** (a swap candidate, hence the seam below). **`quote.close` AND `quote.volume`
  are already split-adjusted, and Yahoo RE-BASES the entire history on every new split** — verified live on a
  forward (NVDA 10:1) and a reverse (LCID 1:10): pre-split bars are continuous with post-split (no
  order-of-magnitude cliff in close or volume). This is a **property of the Yahoo adapter**, not an assumption
  baked into the system. *(Re-basing-on-split is universal to adjusted feeds — e.g. Tiingo's docs likewise say
  re-download when `splitFactor != 1`.)*
- **The stored close is `quote.close` — split-adjusted, NOT dividend-adjusted.** MEASURED on prod
  2026-10-16: the stored SPY and IWM closes equal the vendor's current `quote.close` on 1,252 of the 1,254
  overlapping dates each, and `adjclose` on only 20–23 (the dates after the last dividend, where the two
  coincide). The two mismatches are the intraday-frozen 2026-08-25 / 08-27 closes (below) — no dividend drift.
- **The vendor's daily bar is not final in-session, and its volume keeps settling after the evening.**
  Mid-session the "today" bar is the session so far; and even at the 22:30 pull the day's VOLUME is not
  final — MEASURED on prod 2026-10-16: of the 43 sessions 2026-08-14..10-14, the stored volume differs from
  the vendor's current one on 38 for SPY and 32 for IWM, always upward (+0.05% to +3.2%, ignoring the two
  frozen bars), while every stored bar before 08-14 matches exactly; the closes match apart from the two
  frozen bars. So **no bar is stored before its session settles** (D2, `Settings.price_settle_time` = 18:00
  market time, `ingest/prices/settle.py` — the evidence for the hour is on the setting), and post-settle
  revisions are re-versioned by the restatement compare (thesis names: exactly inside the pull's newest 3
  sessions, and an OLDER bar only when its close moved >= 0.5% or its volume >= 5% — Q11; SPY/IWM: exactly,
  the newest `Settings.benchmark_restate_window` = 5 sessions). Detail: `FEED_LOOP.md` §Settled closes and
  §Price restatements.
- **The benchmark tape's stamp (D1).** The nightly SPY/IWM leg stamps `recorded_at = now` like every other
  live price bar, and since 2026-10-21 so does the one-shot CLI (`python -m pipeline.ingest_benchmarks`),
  which used to backdate first versions to their bar date. Before 2026-10-16 the nightly leg backdated too
  (`recorded_at = D 00:00 UTC`), so the stored pre-D1 bars carry that stamp (2,598 rows on prod, history).
- **Cache behavior.** Cache-first on disk (`data/price_cache/`) for dev and the `--no-live` path (re-runs
  reproducible, polite). The **recurring/daily path force-refreshes** (`fetch_eod(force_refresh=True)`): it
  re-pulls live and overwrites the cache, so the daily cron gets NEW bars instead of a frozen cache hit. A
  cache MISS always fetches, so a new thesis's first ingest is fresh regardless. The pull itself is a fixed
  **two-year window** (`YahooPriceSource(range_=DEFAULT_RANGE)`, `DEFAULT_RANGE = "2y"`): the refresh makes a
  pass see the CURRENT window, never a wider one, so an outage longer than the window leaves a permanent hole
  (§Long-outage data bounds, above). The cache file is written atomically (temp file + rename,
  `ingest.write_atomic`), so a concurrent reader never sees a half-written series.
- **The Yahoo gate (X7, 2026-10-21).** Every Yahoo request — the chart pulls (`fetch_eod`: the price leg, the
  benchmark leg, the rename probe) and the symbol search — passes ONE process-wide token bucket
  (`ingest.http.yahoo_limiter`, `Settings.yahoo_rate_per_sec`, default **4.0/s**). Yahoo publishes no limit,
  so the default is set off what was MEASURED clean: the serial price leg pulled 740 series a night at up to
  3.3/s sustained (Modern Defense, 2026-10-20: 196 pulls in 58.7 s) with ZERO 429s on every X5-instrumented
  pass; 4.0 is a modest step above that. A 429 / 503 is retried after a JITTERED exponential backoff (1–1.5x
  the plain one: never shorter, spread so the pool does not retry in lock-step; a numeric `Retry-After` is
  honored exactly) and PUSHES THE GATE BACK, so every puller pauses, not only the one that was told. Every
  refusal is counted per host on the pass meter (`http_429:query1.finance.yahoo.com`, …) and the printed
  timings line names the 429s when there are any — move the dial on that evidence. Process-local (unlike the
  SEC budget): Yahoo is not a fair-access correctness requirement, and the nightly is the one process that
  pulls hundreds of series.
- **The `PriceSource` seam** (`ingest/prices/source.py`). The source sits behind a `get_bars(ticker, *,
  allow_live, force_refresh) -> [normalized EOD bars]` interface; `YahooPriceSource` and `StooqPriceSource`
  are adapters; the ingest path depends on the interface, so swapping the source is changing an **adapter**,
  not a rewrite. The contract is "a source of EOD bars", not "Yahoo's adjusted bars" — a future raw-prices +
  corporate-actions adapter isn't boxed out. (Still no `get_splits` on the seam: since S2 the price leg owns
  the adjustment for every split on Alpha Deck's own list and puts each vendor bar on its scale —
  `FEED_LOOP.md` §The split list.)

> **RESOLVED — the re-version pass `[BUILT]` (was: mixed-basis bars across a FUTURE split).** Because the
> adjustment happens *outside* our bitemporal store (in a source that re-bases on a split) and our ingest is
> incremental (`d > latest_bar_date`), a thesis that lives ACROSS a future split accumulates **old-basis
> stored bars + new-basis appended bars** → a distorted `volume_breakout` ratio over that window. **Trigger:**
> a tracked thesis living across a future split. **DECIDED — Option (a), operator pick 2026-07-11:** keep
> Yahoo and **re-version restated bars** — the daily force-refresh already re-pulls each whole series; an
> overlap compare appends new versions where fresh ≠ stored (the bitemporal store's native move; as-of reads
> pick latest `recorded_at`), so the series snaps to the new basis within one cron tick, volume adjustment
> stays free, and the mixed-basis window is ≤ 1 day. **BUILT:** `ingest_bars_for_security` now runs the
> overlap compare on every pull (`stored_bars` deduped read → append a new version where fresh ≠ stored
> beyond float noise, close AND volume; a hole in the overlap backfills through the same pass). Idempotent
> (COUNT-the-table), replay-honest (a `known_at` before the correction still sees the OLD basis — the
> bitemporal store's transaction-time guarantee), and loud only on the exception (the daily prints "N bars
> RE-VERSIONED (restated)" only when nonzero). **AMENDED — Q11, operator decision 2026-10-17:** the vendor
> also re-serves OLD bars with sub-percent rounding wiggles, and flips some histories between the adjusted and
> the unadjusted basis from one pull to the next (T4's flaps). So an older bar (before the pull's newest 3
> sessions) re-versions only beyond 0.5% close / 5% volume (T6), and a pass whose older restated bars ALL go
> back to values the store held before (a revert) holds them, whole, until a pull on a later night agrees
> (T5; the pending state is `price_restatement_hold`, migration 0047). A first-time re-base — a genuine split
> — still lands the same pass. Rule, replay numbers and the residuals: `FEED_LOOP.md` §Price restatements.
> Option (b) — a **raw + splits** source owning the adjustment at read time — stays the endgame if a paid
> data tier arrives; the `PriceSource` seam deliberately doesn't box it out.

> **Canceled with reason — the parser split-adjustment (old M2c).** An earlier plan was to compute our own
> split factor and adjust the bars at parse. **Verified unnecessary and harmful:** Yahoo already adjusts close
> + volume (above), so a second adjustment would DOUBLE-adjust (÷ an already-÷10 close). Not built.

### When the SYMBOL drifts: the OTC class, the RENAME class, and the recency monitor

The SEC ticker is canonical for identity, but the vendor may price a name under a **different** symbol — and
the ingest cannot tell that from "this name had a quiet year". Two classes, one shared repair:

- **The OTC class `[BUILT, #252]`.** An OTC name quotes under a suffixed symbol (a "D" appended after a
  corporate action, for instance) and the canonical ticker returns a stub history. Fix: the **vendor symbol
  override** on the security master (`security_master.price_symbol`, migration 0032) — stored only when it
  DIFFERS from the ticker; `ingest_bars_for_security` fetches `price_symbol or ticker`, and the bars land
  under the same `security_id`, so the detectors simply start seeing the fuller history (no call-path touch).
  A verified symbol-search resolver populates it, deliberately **OTC-scoped and thin-history-gated**.
- **The RENAME class — the silent one `[MONITORED, G5a; repair ratifiable since T2]`.** After a rename the vendor's
  series for the OLD symbol simply **ENDS**. The ingest appends bars after the latest stored one, so it
  appends **zero, with no error** — byte-identical to a market holiday. The resolver above cannot reach this
  case by construction (it is OTC-scoped and gated on thin history, while a renamed name has a long, healthy
  history that merely stops), so such a tape could sit dead indefinitely while every price-driven signal for
  that name went dark and nothing said so.
- **The monitor that makes it visible (G5a).** The nightly pass now records each name's **tape edge** (its
  latest stored bar date) and flags a tape whose edge is `ALPHADECK_TAPE_STALE_DAYS` (default 5 calendar days;
  `0` disables) or more behind the run's as-of, or that has no bars at all. The stale set lands on the
  run-of-record artifact, **newly** stale names page through the health notifier, and the Admin freshness
  panel lists every currently stale tape with its last-bar date (`ADMIN.md` §stopped feeds). It is a
  MONITOR: no detector, no call input, and nothing on the CallCard (a day-varying card field would flap the
  cron's `record_if_changed` idempotency).
- **The repair stays the operator's, and it is data.** Set `price_symbol` to the vendor's current symbol and
  the next nightly pass re-pulls the full two-year window, appends the missing tail and hole-fills the
  overlap (splices measured clean). Teaching the resolver to follow renames **automatically** is deliberately
  **not built**: a wrong auto-resolve files ANOTHER company's tape under the member, which is far worse than a
  visible gap (`INVARIANTS.md` #4/#6) — it needs the operator's explicit call.
- **Not every stopped tape is a rename.** A genuine **delisting** (EDGAR Form 25 / 25-NSE / 15-12B /
  15-12G) also ends the series, and correctly: there is nothing to repair, the name is simply no longer
  priced. The monitor tells the two apart from EDGAR alone (G5b/T1, `pipeline/tape_health.classify_stop`):
  **closed** only when a delisting form was filed near the tape's last bar (default window −30 / +45
  calendar days) **and** EDGAR's current `tickers` is empty or still holds our symbol; **renamed** when EDGAR
  lists the company under symbols that include neither our `ticker` nor `price_symbol` (a loud repair row
  naming EDGAR's list — the operator still sets `price_symbol`, nothing adopts it). A form on file at some
  other time is not a close: a company files these for ANY listed class, and VRME's 2025 warrant 25-NSE
  once labeled its still-trading common stock "closed". The residual the window cannot catch — a 25-NSE for
  another class filed within weeks of a stop — is closed by the **Form 25 class check** `[BUILT, Q6
  2026-10-17]` (`ingest/edgar/form25.py`): for a stale name only, each window Form 25's primary document is
  read (one immutable `forms/*` fetch, polite client) and one naming warrants / units / rights / notes /
  preferred stops counting; an unreadable class still counts. See `FEED_LOOP.md`.
- **The rename repair is now RATIFIABLE `[BUILT, T2 2026-10-17]` — still never automatic.** Every night a
  renamed tape's EDGAR-listed symbols are **spliced** against our stored tape (`pipeline/rename_probe.py`):
  ONE Yahoo fetch per candidate symbol, into a throwaway temp dir (the shared `data/price_cache` is never
  written by a probe), verified when at least 5 dates are shared and the median close ratio is within 1%.
  The same company's tape under its new symbol matches ours to float noise (HLLK/SDCO and BRR/SVIA: 30 shared
  dates, x1.000000); another class does not (BRR's warrants SVIAW: x0.121 — the exact mistake a blind
  adoption of EDGAR's list would make). The verified symbol is a pending recommendation on the Admin page;
  the operator's **Adopt** writes `price_symbol` (basis `operator:adopt`) and Undo restores the prior value
  exactly (`price_symbol_change`, migration 0046). A symbol the operator TYPES (Set — e.g. a never-priced
  name's TSX listing) is validated the same way first: one vendor fetch, bars must exist.
- **Never priced** (Q8). A member with no stored bar ever that is neither closed nor renamed (WONDF: a 40-F
  filer EDGAR lists no symbol for; Yahoo 404s it) is the UNPRICEABLE class — loud until the operator sets a
  symbol, removes it, or **acknowledges** it ("keep it, unpriced": quiet, revocable).
- **A vendor 404 is EXPECTED on a closed or acknowledged name** (T3). Yahoo drops a delisted symbol (404),
  which is exactly right; the price leg records the 404 as a fact (`NameResult.price_not_found`) and the
  nightly pass excuses it for a name its tape classification calls closed (or the operator acknowledged), so
  it no longer counts as an ingest error or spoils `ingest_fresh`. The fetch still runs for those names
  every night — the classification is derived from the tape, and a name that starts trading again is noticed
  only because its bars can still land.
- **Some symbols get NO history from Yahoo — only the current bar** `[MARKABLE, 0056 2026-10-21]`. Seen on a
  preferred share (and the shape a newly listed or re-listed symbol can take): Yahoo's chart for **SCE-PG**
  returns exactly ONE bar, the current session, under every range (2y, ytd; `firstTradeDate` 2026-07-17 in
  the payload — MEASURED 2026-10-21), although the same symbol served a year of history on 2026-07-27. The
  nightly leg still stores that night's bar, so the tape grows one bar per night, but it can never be
  restated (no overlap to compare), repaired (the named-bar repair reads `no_vendor_bar`) or backfilled (a
  missed night is a permanent hole — 8 such sessions on SCE-PG between 2026-08-20 and 10-20). Nothing
  detects this automatically; the operator MARKS it on Admin as a **known feed limitation** (an append-only
  `price_feed_ack` `limited_history` row with a reason, evidenced by one vendor pull of ≤ 5 bars; Undo-able).
  The mark only changes wording where the missing history alone would be loud (the bar repair, the name
  panel note) — it never quiets a stopped tape or excuses a 404. The alternative, a second price source for
  such names, was considered and not chosen (2026-10-21). See `FEED_LOOP.md` §Known feed limitations.

### The ETF fund-shares sampler stops the same silent way `[MONITORED, F1; repair is manual]`

An ETF sleeve's shares-outstanding sample (`fact_fund_shares`, the net-flow read's basis) comes from a
**fallback chain** — Polygon when `POLYGON_API_KEY` is set, then the issuer's fund page, then the
aggregator — whose legs miss independently. It ends without an error in two ways: every leg misses (the fund
closed or renamed, a source page was redesigned — the chain warns visibly when a *primary* leg errors, so
that half is at least loud in the run output), or a page's own **stated as-of date freezes** while it keeps
serving the same count, which the incremental compare correctly skips as an unchanged sample. The leg then
reports success while the series stands still.

- **Its own threshold, for a measured reason.** The nightly pass records each sleeve's **sample edge** and
  flags it at `ALPHADECK_FUND_SHARES_STALE_DAYS` (default **7** calendar days; `0` disables this feed
  independently). Not the price tape's 5: MEASURED on dev, the primary source states the **pull date
  exactly** (lag 0 across 28 post-backfill samples) while the aggregator fallback stated a **two-day-old**
  date, so a healthy sleeve's edge sits 0–2 days behind the pass that sampled it. 7 clears that stated lag
  plus a long weekend plus two failed nights; a dead sampler still surfaces inside a week.
- **`d` is the SOURCE's own stated as-of date, never an assumed "today"** (migration 0027) — which is
  exactly why a frozen stated date is a real failure mode rather than a theoretical one.
- **Only ETF sleeves are judged** (the ingest leg's own gate, shared with the monitor): an equity has no
  samples, and a `null` edge means *stale* to the rule, so an ungated monitor would report every equity in
  every basket. A tracked sleeve with no samples at all IS reported — its sampler has never worked.
- **The repair is the operator's and is NOT `price_symbol`** (that is the price leg's lever): check the fund
  still trades under that ticker, read the run's fund-shares warnings for a broken source page, and confirm
  the shares-source key. What goes quiet meanwhile is the sleeve's **display** flow read — never a call
  input (#4/#6) — while its price and filing feeds keep running.

## Form 4 — the Rule 10b5-1 checkbox `[BUILT — CAPTURE-ONLY]`

`fact_insider_txn.aff_10b5_1` records the filing's **Rule 10b5-1 checkbox** (`<aff10b5One>`). **Nothing reads
it.** It is captured so the history ACCRUES while the call-logic question stays open for the operator.

**Why it exists.** Insider *selling* is ~95 % of Form 4 flow (one operator thesis: **30,388 sells vs 1,441 open-market
buys**), and much of it is **autopilot** — pre-scheduled under a 10b5-1 plan adopted months earlier, executing
whether the insider is euphoric or terrified, so it carries ~no information about their current view. Only a
**discretionary** sale (chosen now, with everything they know) could. Measured live: **MU had 7 of 13 recent
filings flagged planned — every one a sale**; NVDA had 1 of 14. Without the flag those are indistinguishable,
so any sales-derived signal would be dominated by autopilot and be actively misleading.

- **Tri-state; the NULL is load-bearing.** `TRUE` = planned · `FALSE` = discretionary · **`NULL` = UNKNOWN**.
  Absence must NEVER coerce to `FALSE` — that asserts "this sale was discretionary" about a filing that never
  said so (inventing a fact, #3). The SEC added the checkbox in the **Dec-2022 amendments** (with cooling-off
  periods + certifications), so ~everything filed before 2023 has no element at all.
- **Filing-level, not transaction-level.** The element sits on the ownership document (after
  `</reportingOwner>`), so it stamps every row from that filing. A filing mixing planned and discretionary
  trades is ambiguous by construction — we record what the SEC gives us, we don't resolve it.
- **Not retroactive.** The ingest is incremental (`existing_accessions` skips stored filings), so the flag
  populates on **newly-ingested filings only**; rows already stored stay `NULL`. Backfilling is a separate
  decision (the fact tables are append-only — a `no_update` trigger enforces it).
- **The detectors are unchanged.** `insider_conviction` reads code `P` and never fires on sales;
  `dilution_clock` remains the only `RISK_SIGNAL`. Whether discretionary selling should feed the counter-case
  is **unspecified in `CALL_LOGIC.md`** and is the operator's call — a test asserts a buy scores identically
  whether planned, discretionary, or unknown.

## Form 4 / 4/A — the acceptance clock: SEC's listed time vs the filing's own header `[BUILT — 2026-10-24]`

**Why the stored time matters.** `fact_insider_txn.accepted` (and `insider_amendment` / `insider_amendment_link
.accepted`) is the PUBLIC instant every history view dates an insider filing by (the editions, the drawer, a
past-asof recompute, the backtest mirror — `db/public_clock.py`); the live nightly reads the record clock. A
night ends at midnight ET, so an early time on the right date is harmless and a late time across midnight moves
the filing to the next night.

**The drift (MEASURED on prod 2026-10-24, read-only).** The time comes from the submissions JSON's
`acceptanceDateTime`. From ~2026-10-23 04:00 ET that list became inconsistent PER FILING: every capture since
(onboarding loads, the 10-23 nightly) stored 25–45% of accessions at an hour EDGAR is closed (before 06:00 or
after 22:00 ET), mostly +4 h / +5 h late; captures 10-20..10-22 were clean. Against SEC's own header
(`ACCEPTANCE-DATETIME`, ET wall clock, in `<accession>-index-headers.html`): 0000902664-25-005199 was filed
2025-12-04, its header says 19:00:20 ET, the list now says 00:00:20 ET on 12-05 — a night late. 5,776 stored
accessions sat a night late (859 of them buys/sells on basket companies since 2025-03-01). An older writer's
clock also left stored times 4–8 h EARLY.

**The rule** (`ingest/edgar/acceptance.py`, one helper for every leg that stores the time): trust the listed
time when its ET date is the `filingDate` and its ET time is within 06:00–22:00 inclusive (a Form 3/4/5
accepted until 22:00 is filed that day); otherwise read the filing's header through the client (the global SEC
budget; the page is immutable, cached forever under `forms/<accession>/`); header unreadable → the END of the
filing date in ET (23:59:59) — never early, the right night, and never NULL (a NULL dates the fact at our first
ingest). The companyfacts gate stays request-free: it takes the earlier of the listed time and that bound. The
4/A linker's order map keeps only a trusted listed time (else the filing dates order the pair). The nightly
prints one quiet `acceptance · …` line when any listed time was set aside. Stored rows are repaired by
`python -m pipeline.backfill_accepted --drift` (dry-run by default; `--execute` after a backup).

## Form 4/A — amendments, stored as filed and resolved at read `[BUILT — R8-A, R8-B]`

**The gap it closes.** The Form 4 leg matched the form type exactly (`filings_of(subs, "4")`), so a **Form 4/A**
was never ingested: an amended-away buy kept firing and a corrected one never did. MEASURED 2026-10-17 over two
years across the 687 member CIKs: **556 4/As against 39,001 Form 4s (1.4%)**, 46 touching a buy.

**What is ingested now.** The same Form 4 leg (`pipeline.ingest_thesis._form4_leg`), from the SAME cached
submissions document, then walks every `4/A` not yet stored (`ingest.edgar.form4a`): the document is fetched
through the immutable `forms/` cache (a re-run costs no request), parsed (every reporting owner,
`dateOfOriginalSubmission`, `periodOfReport`, remarks, footnotes, Table I rows), screened, and stored in
`insider_amendment` — **never** in `fact_insider_txn` (the natural key carries the accession, so a 4/A's rows
there would sit beside the original's: a clerical amendment would double-count, a zero-row void would remove
nothing). An append-only `insider_amendment_link` names the original(s) and the disposition (migration 0050).
Depth: the submissions `recent` window, the same accepted depth as Form 4.

- **The link cascade** (deterministic; MEASURED on the 553 unique 4/As: 422 linked uniquely by step 1, 18 more
  by step 2, 4 ambiguous): (1) a stored Form 4 of this security **filed** on `dateOfOriginalSubmission` (the
  submissions index's `filingDate` — an acceptance after 17:30 ET files the next business day, so the
  acceptance instant is not a substitute) whose rows belong to one of the amendment's reporting owners;
  several with IDENTICAL Table I are filing-agent duplicates, several that differ are ambiguous. (2) Else an
  earlier Form 4 of the same owner sharing transaction dates (a tie is ambiguous). (3) Else unlinked. **The R8
  linker fix (2026-10-21):** a Form 4 SEC made public AFTER the amendment is never its original (both
  acceptance instants read from the SAME submissions index — `acceptanceDateTime` — else the filing dates; the
  stored rows' `accepted` column is not that clock), and an ambiguous set is settled when exactly one candidate
  holds a row the amendment accounts for by exact shape. Rule, measurement and the re-link of the stored links:
  `FEED_LOOP.md` §the R8 linker fix.
- **The dispositions** and what each does to the read are in `CALL_LOGIC.md` §3 (insider) and
  `domain/insider_amendments.py`: correction, void, duplicates, partial / consolidation / addition /
  restatement, **field change** (Table I identical but a signal row's owner role / name / 10b5-1 flag moved —
  migration 0051 adds it to the link's disposition check), ambiguous; inert = clerical, no Table I rows, no
  stored original. **R8-B (operator decision 2026-10-20, revised 2026-10-21): the filer's own correction is
  applied by default** — every amendment resolved to a definite original is RESOLVED BY THE SYSTEM, an
  append-only decision written at ingest in the link's transaction or, for the pre-R8-B backlog, by the nightly
  sweep (`recorded_at = now`): an `apply` under basis `system:filer-correction` (or `system:no-signal-effect`
  when it changes no code-`P`/`S` row — the signal code set derived from the detectors,
  `signals.horizons.insider_signal_codes`), or, for a zero-row 4/A its words do not retract, a `keep`
  (`system:no-retraction` / `system:held`). Each keeps its exact Undo on Admin. An applied partial /
  consolidation / addition / restatement / field change replaces only the original rows it ACCOUNTS for (the
  row match, `domain.insider_amendments.match_rows` — exact shape only: identical, a price / share / date fix,
  a recode, a same-day sum), and the leftovers of one owner and code are settled by the filer's own words — FIX
  replaces them, ADD keeps every original and adds; a zero-row 4/A retracts its original only with explicit
  retraction words AND a single-`P`/`S`-trade original (the phrase lists and the measured outcome:
  `FEED_LOOP.md` §R8-B). A Form 4/A is the filer's legal correction of their own filing — deterministic data,
  not a model's recommendation — so the operator rules only on what the system cannot resolve: an
  **ambiguous** link, an **unresolved row match** (its words neither fix nor add), or a **held void**
  (retraction words against several trades), that touches a `P`/`S` row inside the review horizon
  (`insider_amendment_review_horizon_days`, derived 940 days = the 730-day backtest window + the 210-day `P`
  read) — with three verdicts: Use the correction, Keep the original, Add to the original. An older one is
  listed as "unresolved — historical", the originals standing. MEASURED 2026-10-21 on prod, asof 2026-10-20
  (2,988 linked pairs — the live leg pulled the full history back to 2003, not the two-year research corpus),
  after the first sweep: **6 owed** (4 ambiguous, 1 unresolved match, 1 held void), **60 historical**, **1,693
  resolved by the system**, **1,229 inert** (`FEED_LOOP.md` §R8-B). (R8-A's figures, 2026-10-18, two-year
  window: 67 owed / 121 applied by the system.)
- **The other-issuer screen.** A company's own submissions feed lists every ownership filing it is a party to —
  including the ones it filed as the REPORTING OWNER of another company's stock (147 such rows across 11
  tickers sat on member tapes on 2026-10-17; 25 of them code `P`). A Form 4 or 4/A whose `issuerCik` is not
  the member's is no longer stored (a `form4_other_issuer` Q18 skip — known ones counted, never re-printed);
  the rows already stored are KEPT and screened at read (the ADR keep-and-screen precedent — deleting them is
  a separate data repair needing the operator's go). The member's CIK set is the master's plus every T7
  CIK-change re-point, so a successor-CIK adopt never screens the predecessor's own history.
- **Backfill.** The first nightly pass after deploy ingests every 4/A in the window (~550 document fetches,
  rate-limited) with `recorded_at = now`, so no past replay moves; its pending items page once. (MEASURED
  2026-10-20: the live leg walks the whole submissions history — 2,988 linked pairs back to 2003.) R8-B's
  first nightly sweeps that backlog the same way — one system decision per resolvable amendment, `recorded_at =
  now` — so no past replay moves either.

## Form 4 — joint reports: one trade, several filers, counted once `[BUILT — 0054; counted once at read]`

**The defect (found 2026-10-21, INFQ "$95.2M across 2 insiders").** `parse_form4` stores each Table I
transaction ONCE, under the filing's first `<reportingOwner>` — a joint filing's co-owners are not stored, so one
filing never double-counts (MEASURED: 0 of 146,111 stored (security, accession) pairs carry a second owner). The
double count is ACROSS filings: everyone with a beneficial interest in a trade held through a fund, a trust or a
spouse files their OWN Form 4 for it. MEASURED on prod (read-only), cross-filing groups identical on (security,
date, code, shares, price) with ≥ 2 owners: 15,268 (2,710 code P/S); by the filings' own XML, 2,083 of the P/S
groups leave the SAME post-trade holding (the same position re-reported) and 627 a different one (separate
trades); within one filing, identical rows are separate lots (several funds each selling the same lot size).

**The capture (migration 0054).** `fact_insider_txn.direct_indirect` (`'D'`/`'I'`, per transaction —
`<ownershipNature><directOrIndirectOwnership>`) and `shares_owned_after` (`<sharesOwnedFollowingTransaction>`;
NULL when the filing reports a value instead). Nullable, progressive (the `aff_10b5_1` / `security_title` shape);
4/A rows carry both keys through `parse_form4`. A malformed holding costs the row its evidence, never the filing
its ingest.

**The read (`domain/insider_joint_reports.py`, after the 4/A resolution, both engines).** The rule and its
measured effect: `CALL_LOGIC.md` §3 ("Joint reports"). Every as-of reader goes through it: the two insider
detectors, the Cockpit's insider-flow display member (its basis note names the joint reports), the Scoreboard's
buy/sell ledger overlays (the survivor's name reads "A / B (joint report)"), and the replay mirror. Not collapsed,
on purpose: per-filing views (the Admin 4/A panel's original-vs-amendment rows, the 4/A linker's stored-row read).

**The history repair (`python -m pipeline.repair_insider_ownership`).** Re-parses the cached filing (no SEC
request) and appends a NEW version of each candidate row with the evidence — the latest-version Form 4 rows and
4/A records' rows that share (security, date, code, shares, price) with ANOTHER owner's row in ANOTHER filing (the
only rows the rule can ever read evidence for), narrowed to the components of filings a P/S reader can reach
(MEASURED 2026-10-21, prod read-only: 8,437 Form 4 rows + 128 4/A records from 2,650 cached filings, none
uncached or mismatched, out of 50,635 candidates of every code). NULL-only + idempotent, dry-run by default (read-only connection),
`--apply` under the call-of-record lock with the appended counts verified. Versions are stamped `now`: a replay
pinned before the repair still reads the double count the system held (#1). A row it cannot repair (uncached,
unparseable, a date/code/shares disagreement) stays bare — and a bare row never merges.

## Form 4 — the filer's own words: internal transfers + private purchases `[BUILT — 0055; screened at read]`

**The defect (found 2026-10-21, AMKR).** An internal transfer looks like a buy plus a sale: James J. Kim's
869,565 AMKR shares "distributed" to his children's Sujoda Investments, LP at $21.85 armed AMKR on a phantom CORE
buy. The 0054 evidence does not link the two sides (MEASURED: it links 0 of the 3 AMKR transfer pairs — the
holdings differ); what does is in each filing's own words, which no stored row carried.

**The capture (migration 0055).** Per transaction: `ownership_nature` (`<natureOfOwnership>` — "By Spouse",
"By Sujoda Investments, LP") and `txn_footnotes` (the text of every footnote the transaction cites, first-reference
order). Per filing, stamped on each of its rows: `filing_natures` (every INDIRECT ownership nature the filing
states on its Table I transactions AND holdings — "By James J. Kim 2024 GRAT dtd. 8/5/24"). `text[]` for the two
arrays (NULL = not captured, `{}` = the filing states none — the `fact_corporate_event.items` precedent, so every
repair tool that copies a row verbatim keeps working); the mirror decodes them back to lists. 4/A rows carry all
three keys through `parse_form4`. Nullable, progressive.

**The read.** `domain/insider_internal_transfers.py` (after the joint collapse, both engines) tags a buy and a
sale between linked filers — the rule and its measured effect: `CALL_LOGIC.md` §3 ("Internal transfers"). Readers:
the two insider detectors (left out, named), the Cockpit's insider-flow member (set aside on BOTH sides, named in
the basis — the panel claims open-market flow), the Scoreboard ledgers (`character = internal_transfer`,
`transfer_with`), the replay mirror. `domain/insider_private_purchases.py` (display only) locates a private-
purchase passage in a counted buy's `txn_footnotes` for the Event Ledger note — MEASURED over prod's 26,573
code-P rows: the raw phrases matched 2,730 rows, almost all the weighted-average boilerplate ("purchased in
multiple transactions at prices ranging from …"); the shipped list (a FROM-party required; "open market",
public offerings and plan purchases — dividend reinvestment, ESPP — excluded) locates 86 rows on prod's cache,
every passage read by eye (25 in the last two years, 6 inside the 240-day insider read).

**The history repair (`python -m pipeline.repair_insider_filer_text`).** The 0054 tool's mechanism and rules
(cached XML, `parse_form4`, verified on date + code + shares, NULL-only, idempotent, dry-run by default,
`--apply` under the call-of-record lock, count-verified, stamped `now` — #1). WHICH ROWS: every code-P / code-S
row on a (security, date, shares, price) signature holding a buy and a sale of DIFFERENT owners (the only rows the
named-holding link reads; a same-owner pair links on identity alone), plus every code-P row whose own footnotes
the note's phrase list locates; a version also fills the 0054 evidence where the row lacks it (the co-report rule
reads `direct_indirect`). MEASURED 2026-10-21 (prod read-only, the plan against a local copy of the cron's forms
cache): 126 transfer candidates (124 Form 4 + 2 rows of 4/A records), 27,122 code-P rows read across 10,613
filings → WOULD version 203 Form 4 rows (transfer 122, private purchase 86 — 5 both) + 1 4/A record; 255 filings
referenced by candidate rows are NOT in prod's forms cache (507 of their rows are 4/A records' code-P rows; 2 are
transfer candidates) — they stay bare: counted, never screened by name, no note.

## 8-K item codes — the corporate-event tape `[BUILT — Band 03 S3; corporate_risk LIVE]`

`fact_corporate_event` stores **every basket member's 8-Ks with their SEC item codes** — parsed from
the SAME submissions JSON the Form 4 leg already fetches (the `items` array parallels
`accessionNumber`), so the whole tape costs **zero extra EDGAR fetches** and rides the nightly cron
as a fourth `ingest_thesis` leg. The item-code taxonomy is the SEC's own deterministic
classification (#3 — no NLP, no LLM near the fire path); the detector cut (the parked 5.02 trigger;
the live 3.01/3.02/4.01/4.02/1.03 risks) lives in `CallConfig.corporate_event_items` and is applied on
READ, so items outside the cut stay stored (#9) and a policy retune needs no re-ingest.

- **Forms ingested** (`domain/edgar_forms.CORPORATE_EVENT_FORMS`, the one list): `8-K`, `8-K/A`, and —
  since 2026-10-17 (operator decision) — a successor issuer's first report, `8-K12B` / `8-K12G3` /
  `8-K15D5` and their `/A` amendments. The stored `form` column says which one a row is. The succession
  reports are STORED but SKIPPED by every event detector, by form: their items describe the
  reorganization itself (3.01 = the listing transfer, 5.02 = the carried-over officers). Full rule:
  `docs/CORPORATE_EVENTS.md` §succession reports.
- **Knowability:** `valid_from = filed` — the acceptance date IS when an 8-K became knowable (the
  cleanest bitemporal source on the menu; no-lookahead is free).
- **`items` may be NULL** — EDGAR hadn't classified the filing yet; stored as NULL (never guessed)
  and re-versioned append-only when it resolves on a later run.
- **Depth:** submissions `recent` (≥ 1 year / 1,000 filings), the same accepted depth as Form 4;
  the deferred cadence-baseline slice may need the paginated older pages.
- **Detectors:** `corporate_risk` is LIVE (switch flipped ON 2026-08-17); `corporate_catalyst` stays
  parked (switch OFF, 5.02-only since the 2026-08-20 `1.01` decision). Full family doc:
  `docs/CORPORATE_EVENTS.md`.

## SEC split filings — the split list's company-side source `[BUILT — S1 / S4; Items 8.01 / 7.01 since 2026-10-23]`

The split list (`security_split`, `FEED_LOOP.md` §The split list) confirms a split only when two independent
sources agree; the company's own filing is one of them, Polygon's split reference the other (the Polygon row
above). The filings read (`pipeline/backfill_splits.py` `SPLIT_8K_ITEMS` / `SEC_FORMS`, the nightly's
`pipeline/detect_splits.py` too): **8-Ks under Item 3.03 / 5.03** (the charter amendment), **8-Ks under Item
8.01 / 7.01** (since 2026-10-23 — a split with no charter amendment: one paid as a stock dividend, a trust's share
split, a board-set ratio; the operator's 2026-10-22 review found ENHA, SNEX ×3, ZCSH, ETH, BTC and OFAL missing
for this reason), **6-Ks** (foreign filers) and **Reg A** forms (1-A / 1-U). Each is fetched ONCE as its full
submission (`forms/*`, immutable, cached forever) and read by the deterministic extractor
(`ingest/edgar/split_filing.py` — fixed patterns, the located passage verbatim; its docstring holds every rule).

- **8.01 is the catch-all item**, so an 8-K read only for 8.01 / 7.01 is also screened against its FILING date:
  a date more than 90 days before it is a past split's (the filing restates it), never this filing's first
  session. MEASURED 2026-10-23 on prod's basket (688 companies): 852 such 8-Ks in the backfill windows and the
  nightly's 60-day lookback; 47 mention a split ratio; 37 state one — 32 agree with a listed split, 0 contradict
  a confirmed one, 5 state no date (never listed alone). The screen keeps AIRE's 8-K/A (a 1-for-25 restated, a
  2025-12-31 tax-table date) from being listed as a new split.
- **Cost (MEASURED on the 2026-10-23 dry run of the detect leg):** the first night reads 474 more filings
  (~871 MB, median 270 KB; each once, ever — the cache is immutable); after that ~7 a night (50 were filed
  10-16..10-22). No extra request for the item codes — they come from the submissions JSON the 8-K leg already
  reads.

## SC 13D/G — the activist-stake ownership tape `[BUILT — Band 03 S5, INERT]`

`fact_activist_stake` stores **every 13D/G-family schedule filed ABOUT a basket member** (the
subject), enumerated from the member's OWN submissions JSON — verified live 2026-08-18: EDGAR
indexes an ownership schedule under both the filer and the subject, so the same document the
Form 4 / 8-K legs fetch lists the whole tape at **zero extra enumeration fetches** (a fifth
`ingest_thesis` leg on the nightly cron). The FORM TYPE is the classification (#3): 13D = ">5%
with intent", 13G = passive; the fire policy (13D-family ORIGINALS only) is applied on READ.

- **The rename trap (#9):** EDGAR renamed the form type at the ~2024-12 structured-XML cutover —
  `SC 13D` → `SCHEDULE 13D` (measured: the same issuer's tape flips between 2024-11-14 and
  2025-02-05). The ingest matches ALL EIGHT strings across both eras; an SC-only match silently
  drops every 2025+ filing. (EFTS's `forms=SC 13D` filter has exactly that gap — one reason the
  submissions path won.)
- **Knowability (gold-doc trap #4):** `valid_from = filed` — the stake CROSSING inside the filing
  predates dissemination by up to 10 days, and the structured cover's `dateOfEvent` is deliberately
  never read for time.
- **Filer identity (the 0024 capture pattern):** the subject's submissions rows carry no filer, so
  identity costs one small immutable-cached fetch per filing — structured era: the raw
  `primary_doc.xml` (`reportingPersonName`/`CIK`, `percentOfClass`); classic-era 13D-family: the
  SGML `FILED BY` header; classic-era 13G: no fetch (NULL filer by design — those rows never
  fire). An identity failure stores the row with NULL filer + a loud counter, never a drop (#9).
- **Depth:** submissions `recent` (≥ 1 year / 1,000 filings) — full history for the measured
  microcap subjects; the detector's months-scale liveness never needs deeper.
- **Ships INERT** (`activist_stake_enabled` default OFF) pending the sig-lab pass. Full family
  doc: `docs/ACTIVIST_STAKE.md`.

## Point-in-time discipline (applies to every source)

Every ingested fact lands with `valid_from` = event/effective time and `recorded_at` = ingest time, into
the bitemporal store (see `CLAUDE.md`). No source is read in a way that lets a detector see data dated
after its `asof`. This is what makes the replay harness honest. History is judged on the PUBLIC clock
(`INVARIANTS.md` §4, operator decision 2026-10-21); `recorded_at` is bookkeeping. Two writers used to backdate
it and no longer do — the pre-D1 SPY/IWM bars (`recorded_at = D 00:00 UTC`, 2,598 rows) and the pre-0057
`fact_fundamentals` rows (`recorded_at = filed 00:00 UTC`, 120,876 rows) keep those stamps as written, which
is moot under the public clock (each sits on its public date already).

## Notes on the ETF radar's three faces

1. **Availability** — map theme → expressing ETFs (curation + holdings-overlap against the theme universe).
2. **Coming** — watch EDGAR N-1A/485 for new thematic registrations; a fresh launch is an emergence-kind
   signal (early tell, occasionally a top).
3. **Holdings & flows** — holdings seed the decomposition for free; holdings changes + derived flows are a
   positioning signal. Always expose fund internals so a mislabeled/thin/expensive fund is visible.
