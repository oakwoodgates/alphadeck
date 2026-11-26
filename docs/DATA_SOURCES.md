# Data sources

Where Alpha Deck's data comes from and the rules every fetch keeps: vendors, EDGAR etiquette and the shared
SEC budget, cache freshness, when a filing became public, prices, the company list, USASpending and
Massive.com. The machine does most fetching (the nightly pass, onboarding, weekly jobs); the Workbench, a
steward's Admin action and a CLI use the same clients. Cadence is end of day; there is no intraday tape. Read
this before you add a vendor call, a fetch client or a cache. What each SEC filing family stores is in
[FILINGS](FILINGS.md); the ingest legs are in [INGEST](INGEST.md).

## Code

| Piece | Module |
|---|---|
| SEC client: cache, freshness, error-body guard | `ingest/edgar/client.py` (`EdgarClient`) |
| The global SEC request budget | `ingest/sec_budget.py`, table `sec_request_budget` |
| The companyfacts filing gate | `ingest/edgar/companyfacts_gate.py` |
| When a filing became public | `ingest/edgar/acceptance.py`; `ingest/shares.py` (`count_publication`) |
| Prices: source seam, Yahoo fetch, request gate | `ingest/prices/source.py`, `ingest/prices/eod_loader.py`, `ingest/http.py` |
| Company list, fund tickers, successor CIK | `securities/sec_tickers.py`, `securities/master_refresh.py`, `pipeline/populate_master.py`, `securities/fund_tickers.py`, `pipeline/successor_cik.py` |
| USASpending (DOE awards) | `ingest/doe/` |
| Massive.com | `ingest/funds/polygon.py`, `ingest/polygon_splits.py`, `ingest/massive_bars.py`, `ingest/massive_reference.py` |

## Sources

| Source | What it gives | Read by |
|---|---|---|
| SEC submissions JSON (`data.sec.gov`) | A company's filing list: forms, dates, acceptance times, 8-K items | Every filing leg, the filing index, the companyfacts gate |
| SEC filing documents (`Archives`) | Form 4 and 4/A XML, 13D/G covers, split filings, 10-Q and 10-K text, filing headers | Filing legs, the extractor, the acceptance rule |
| SEC companyfacts (XBRL) | Reported financials and cover share counts | Fundamentals, platform values |
| SEC full-text search (EFTS) | Filings that mention a term | Discovery, term mentions |
| SEC daily index | Every filing of a day | The SPAC radar, the fund launch radar |
| SEC ticker files | `company_tickers_exchange.json` (companies), `company_tickers_mf.json` (funds) | The company list, fund resolution, the successor-CIK scan |
| SEC browse-edgar feed | N-PORT filings of one fund series | ETF holdings |
| Yahoo chart API | End-of-day bars, symbol search | The price leg, benchmarks, the rename probe |
| Massive.com | Fund shares by date; the market's splits; raw bars; ticker overviews | ETF net flow, the split list, the tape cross-check, stale-row evidence |
| Issuer and aggregator fund pages | Fund shares outstanding (fallback) | ETF net flow |
| Nasdaq Trader symbol directory | Listed ETF symbols | The fund launch radar (`radar/funds.py`) |
| OpenFIGI | Ticker to identifier mapping | `securities/master.resolve` |
| USASpending | DOE awards and their terms | The DOE catalyst feed |

Free sources come first. A paid source is added only when a signal's quality depends on it and the free proxy
is shown inadequate; vendors considered and not used are on [ROADMAP](ROADMAP.md).

## EDGAR etiquette

SEC's fair-access rules are a correctness requirement here, not a courtesy: a blocked client loses filings
silently. Every SEC request goes through `EdgarClient` or one of the two ticker-file fetches
(`securities/sec_tickers.py`, `securities/fund_tickers.py`), and all of them keep these rules.

- **Declared User-Agent.** `ALPHADECK_USER_AGENT` names the operator and a contact. A live fetch without it
  raises; `/health` reports it missing.
- **Cache-first, live by opt-in.** A client reaches the network only when built with `allow_live`; tests keep
  it off, so a cache miss raises `CacheMiss` and the suite never touches SEC.
- **One budget for every process.** SEC's ceiling is per egress IP, which a per-process limiter cannot hold.
  `ingest/sec_budget.py` keeps one Postgres row (`sec_request_budget`, migration 0058) holding the next free
  slot; each live request reserves its slot with one atomic upsert and sleeps until it, so any two SEC requests
  are at least one interval apart whichever process sent them. The rate is `sec_global_rate_per_sec`, under
  SEC's ceiling with headroom; each client also keeps a local cap, `edgar_rate_per_sec`. A static split per
  process or a file lock cannot cover a host-side CLI; Postgres is shared by every writer already.
- **Fair share by lane.** A recurring client (below) takes the recurring lane, anything else the interactive
  lane. Each lane holds at most `LANE_DEPTH` reservations not yet due, so two busy lanes alternate and each gets
  half the budget whatever its thread count; a lane alone gets all of it.
- **A 429 or 503 pauses everyone.** `polite_get` retries with jittered backoff, and the client's `on_throttle`
  pushes the shared slot out, so every process backs off together.
- **Fail-safe toward politeness.** If Postgres is unreachable, the request still goes behind a process-local
  limiter at half the global rate, counted as `sec_budget_fallback` on the pass meter; the database is retried
  after `FALLBACK_RETRY_S`. A budget fault never fails a fetch.
- **Forks never go live to SEC.**

## Cache freshness

`EdgarClient.get_text` decides freshness from the cache key's class, never from a per-call flag. The default is
to refresh; only a key class proven immutable is exempt, so a new mutable endpoint is safe without any caller
remembering anything. A cache that once served every key forever froze Form 4 discovery for about 11 days, and
the discovery universe and the extractor's share counts the same way; each looked fine when tested, because a
new key always fetches fresh.

| Key class | Rule | Why |
|---|---|---|
| `forms/<accession>/...` | Cached forever | A filed document never changes |
| A dated `daily-index/` or `full-index/` key | Cached forever once its day is before today in market time and the copy was written at or after 06:00 market time the next day | A finished day's index stops growing; a copy taken while the day was open is refreshed once more |
| `companyfacts/` on a recurring client | Re-pulled only when uncached, when the cached submissions index shows an XBRL-bearing filing accepted since the copy was fetched (less a grace period), or after a backstop of under a week | It changes only when the company files facts; the gate reads the index from disk, so it costs no request |
| A companyfacts 404 | A recurring client writes `<key>.404` and re-asks only when the same gate says due; an interactive client ignores it | Some filers have no companyfacts |
| Everything else (`submissions/`, `efts/`, `files/`, feeds) | Refreshed after the client's TTL | Mutable by default |

**Two TTLs.** The default TTL (`_DEFAULT_CACHE_TTL_S`, 12 hours) is the interactive dial for the Workbench,
discovery and a standalone CLI. Its clock starts when that company's key was last fetched, so a daytime read would
leave a company warm and the nightly pass blind to everything filed after it. So every recurring client (the
nightly pass, onboarding, fundamentals, background extraction, the weekly jobs, the radars, the successor scan
and the steward's "Extract now" client in `app/deps.py`) is built with `cache_ttl_s=RECURRING_CACHE_TTL_S`, five
minutes; `git grep RECURRING_CACHE_TTL_S` lists them. The dial is also the marker: `EdgarClient.recurring` is true
at or below it, which switches on the companyfacts gate, the 404 marker and the recurring budget lane. A daytime
"Run daily now" therefore never blinds the nightly pass.

**Five minutes, not zero.** Within one pass three filing legs read the same `submissions/CIK<10>.json` key
milliseconds apart; at zero each read refetches, tripling the per-company cost for no freshness. Five minutes
keeps those re-reads free and still closes the daytime gap, since EDGAR accepts filings only 06:00-22:00 market
time and the pass starts after that.

**Offline** (`allow_live` off: tests, `--no-live`), a stale cached copy is still served.

**The error-body guard.** SEC sometimes answers 2xx with an error body (a JSON object carrying `errorType`).
`get_text` checks every body, fresh or cached, against that guard and the caller's `validate`; a rejected body
raises `InvalidBody`. A fresh one is never cached and never counted in `live_completed`, which the freeze check
reads; a cached one is a miss, refetched when live and raised when not.

**Prices** cache differently: `fetch_eod` is cache-first, the recurring path passes `force_refresh=True`, and a
miss always fetches. Massive.com caches are keyed by the market day or the queried date, so a new day is a miss.

**Persistence.** The caches live under `data/`, the `appdata` volume in the backend and cron containers, so
they survive a rebuild; files are written atomically (`ingest.write_atomic`).

## When a filing became public

`db/public_clock.py` dates every history view by when a fact became public, and detectors gate a filing fact on
`valid_from <= asof` ([invariant 1](INVARIANTS.md#1-no-lookahead-history-is-judged-on-the-public-clock)), so the
stored acceptance decides which night a filing lands on. `ingest/edgar/acceptance.py` is the one home of the
rule; every leg that stores an acceptance, and both repairs, call it.

- **Form 4 and 4/A** (`resolve_acceptance`). Trust SEC's listed `acceptanceDateTime` when its market-time date
  is the listed filing date and its time is within EDGAR's hours, 06:00-22:00; SEC's list has shown times hours
  late for some filings. Otherwise read the filing's own header through the client. If the header cannot be
  read, store the end of the filing date in market time: never early, always the right night, never NULL.
- **8-K, 13D/G and 10-Q / 10-K revenue rows** (`resolve_public_acceptance`, rule
  `2026-11-filings-dated-by-acceptance`). A filing submitted after 17:30 market time carries the next business
  day's filing date, though EDGAR shows it that evening. Trust the listed time when the Form 4 test passes or
  when it is that roll (the business day before the filing date, 17:30-22:00); otherwise the header from the
  cache only; otherwise nothing is stored and the filing date stands, late but never early. `public_date` makes
  a trusted time's market-time night the row's `valid_from`.
- **Share counts** (`count_publication`). A count is public from the acceptance of the earliest filing whose
  companyfacts cover row states exactly that count on that date, else the end of that filing's date. The machine
  writer and the steward's write (`platform_values/machine.py`, `platform_values/steward.py`) stamp it from the
  cache at write time; a count no cover row states counts from when it was written.
- **No leg re-dates a stored row.** `pipeline.backfill_accepted --drift` re-dates Form 4 and 4/A acceptance
  times; `pipeline.repair_filing_dates` re-dates 8-K, 13D/G, revenue and share-count rows. Both are dry runs by
  default and append versions (the `data-repair` skill).

## Prices (Yahoo)

The live source is Yahoo's chart API: free, no key, unofficial and uncontracted. The ingest depends on the
`PriceSource` seam (`ingest/prices/source.py`), so a new vendor is a new adapter; `YahooPriceSource` is the
default and `StooqPriceSource` a fallback whose live endpoint is now gated. Massive.com's raw bars are only a
nightly cross-check ([FEED_MONITORS](FEED_MONITORS.md)).

- The stored close is Yahoo's `quote.close`: split-adjusted, not dividend-adjusted. Yahoo re-bases a history on
  a split, but not always stably, so the price leg puts every bar on the scale of Alpha Deck's own split list
  (`ingest/prices/split_normalize.py`) and never adds an adjustment of its own.
- The day's bar is not final during the session, and its volume keeps settling upward into the evening. No bar
  is stored before its session settles (`Settings.price_settle_time`, `ingest/prices/settle.py`); later
  revisions are re-versioned by the restatement compare (`ingest/prices/restatement.py`).
- A delisted symbol returns 404. Some symbols return only the current bar; the steward can mark such a name as
  a known feed limitation on Admin.
- The member pull reads a fixed window, `DEFAULT_RANGE` (two years: a one-year return plus the longest
  moving-average lookback). Benchmarks pull five years (`pipeline.ingest_benchmarks`).

**The Yahoo gate.** Every Yahoo request passes one process-wide token bucket, `ingest.http.yahoo_limiter`, at
`yahoo_rate_per_sec`. A 429 or 503 is retried after a jittered backoff that pushes the gate back for every
puller, and each refusal is counted per host on the pass meter (`http_429:<host>`). It is process-local:
Yahoo is not a fair-access requirement, and the nightly is the one heavy puller.

**When the symbol drifts.** The vendor may price a name under a symbol other than its SEC ticker (an OTC suffix,
a new ticker after a rename); `security_master.price_symbol` (migration 0032) overrides the ticker for the price
pull only. A renamed symbol's old series simply ends, which looks like a quiet market until the stale-tape
monitor sees it ([FEED_MONITORS](FEED_MONITORS.md)). Renames are not followed automatically beyond the company
list's pin and splice (below): a wrong guess files another company's tape under the member, which is worse than
a visible gap.

The nightly pass and the onboarding job pull with `force_refresh`; a steward can pull one name
(`POST /workbench/securities/{security_id}/ingest-prices`, cache-first). The write path is in
[INGEST](INGEST.md).

## Company list and identity

`security_master` is platform data in the data tenant ([TENANT_SCOPES](TENANT_SCOPES.md)): one row per tradeable
line, identity only, updated in place. It is loaded from SEC's `company_tickers_exchange.json`, which carries a
per-instrument exchange.

- **Keyed on `(cik, ticker)`, not `cik`.** Dual-class issuers share one CIK (GOOGL and GOOG).
- **One primary line per company.** `sec_tickers.flag_primaries` ranks a CIK's lines by instrument class, venue,
  then a demotion for five-letter OTC foreign ordinaries; SEC's file order is only the last tiebreak, because
  the file is usually, not reliably, primary-first.
- **The weekly refresh.** `python -m pipeline.populate_master` is a dry run by default; `--live --apply` reads
  SEC's current file, writes the plan in one transaction, caches the file it planned from, refreshes the fund
  file and writes a run artifact under `data/master_runs/`. The cron sidecar runs it weekly and at boot when the
  newest applied run is over a week old. An apply refuses with exit 75 inside the nightly window, while another
  refresh holds its lock, or while a pass holds the call-of-record lock. The rules are
  `securities/master_refresh.py`'s: a rename updates the company's row in place (the id stays, so every fact
  stays attached) and appends the pair to `security_ticker_history` (migration 0066); new classes are inserted
  non-primary; at most one primary per CIK; primacy moves only on structural grounds, never on file order alone;
  a ticker another CIK holds is a conflict, never applied; a CIK SEC no longer lists is left untouched.
- **Prices follow a rename only when safe.** A renamed row with stored bars and no price symbol is pinned to its
  old ticker (`system:rename-pin`, undoable on Admin); once the old symbol stops, the rename probe adopts the new
  ticker only on a splice of at least `AUTO_ADOPT_MIN_OVERLAP` shared bars at a ratio of exactly one
  (`pipeline/rename_probe.py`, `system:auto-adopt`). A row whose price symbol is set is never touched.
- **The Workbench reads the history**: its search matches a former ticker, and the promote's identity guard
  reads a basket label holding the former ticker as a rename, not a mismatch.
- **Funds.** An ETF is not in the company file. `securities/fund_tickers.py` resolves a fund symbol on the fly
  from `company_tickers_mf.json`; the trust CIK is never written into the master, because extraction keys on
  `cik`.
- **Successor CIK.** A company that re-registers under a new CIK and keeps its ticker leaves the member on a
  dead CIK, and its filing legs go quiet. `pipeline/successor_cik.py` reads SEC's ticker map each night and
  flags such a member; the re-point is the steward's Adopt on Admin (`master.set_cik`, migration 0049, with an
  exact Undo). `populate_master` never re-keys a CIK. EDGAR links built from the current CIK still resolve.

## USASpending (DOE awards)

`ingest/doe/` turns DOE awards into catalyst facts deterministically: grade and horizon come from the award's
structured terms, never from a model (invariant 3); the grade rule is the catalyst detector's
([DETECTORS](DETECTORS.md)). The feed has one caller today, the demo seed (`pipeline/seed.py`), which runs it
offline against the committed payloads in `backend/seed_data/doe/`; no nightly leg pulls USASpending.

A recipient maps to a ticker only by exact `recipient_id` in `ingest/doe/entities.py` (invariant 11); an
unknown recipient is dropped. Three API traps force this: `spending_by_award` silently ignores a `recipient_id`
filter and returns every DOE award by size; text search over-matches ("Centrus" returns an unrelated
recipient); and homonyms ("Oklo" surfaces OKLO TECHNOLOGIES, INC., a different recipient holding tens of billions
of dollars of national-lab contracts; the awardee is OKLO INC.).
`tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy` pins them. Discovery queries one award-type
group per call (the API answers 422 to mixed groups); the loans group is not queried.

A DOE catalyst counts from the date the award was recorded (`date_signed`, its first action date), else the work
period's start; with neither it is skipped (`feed._event_date`, rule `2026-11-catalysts-dated-by-record-date`).
A work period can start before the public knew; the record date is late by design, because this source carries
no announcement date. `UsaSpendingClient` is cache-first like the EDGAR client, with its own rate limit.

## Massive.com

Read with `MASSIVE_API_KEY`; without it nothing is sent. The adapters use the vendor's legacy host
(`Settings.polygon_base`), as older file names do.

- **Fund shares outstanding by date** (`ingest/funds/polygon.py`): the primary `fact_fund_shares` source
  when keyed, with real history for `pipeline.backfill_fund_shares`. A sample takes the queried date; a
  null count is a miss, never a zero. The fallbacks are the issuer's fund page, then an aggregator
  (`ingest/funds/source.py`), each dated by the page's stated as-of date.
- **The split reference** (`ingest/polygon_splits.py`): every split in the market from a date, one
  `/stocks/v1/splits` query, no ticker; the second source the split list confirms against
  ([FILINGS](FILINGS.md#split-filings)). Massive.com's `adjustment_type` label is stored as evidence, not content.
- **Pacing.** The free tier allows about five requests a minute; every adapter shares one limiter and waits out
  a 429. No cached URL or raised message carries the key.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| Nightly, onboarding and weekly fetches | Machine (cron sidecar) | `backend/scripts/daily_cron.sh` |
| `POST /workbench/securities/{security_id}/ingest-prices` | Steward | `require_super_admin`; `tests/app/test_auth_gate.py` |
| CIK and price-symbol Adopt, Set, Acknowledge, Undo (`/admin/price-feed/...`) | Steward | `require_super_admin` on the router; `tests/app/test_auth_gate.py` |
| Workbench discovery (EFTS) | Any signed-in user | `require_user`; allowances in [AUTH](AUTH.md) |
| `pipeline.populate_master --live --apply` | Operator, or the cron sidecar | Its lock and window refusals |
| `pipeline.backfill_accepted`, `pipeline.repair_filing_dates`, `pipeline.repair_catalyst_dates` | Operator | Dry run by default; the `data-repair` skill |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `ALPHADECK_USER_AGENT` | `domain/settings.py` | Yes |
| `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC` | `domain/settings.py` | Yes; every process must agree |
| `ALPHADECK_YAHOO_RATE_PER_SEC` | `domain/settings.py` | Yes |
| `MASSIVE_API_KEY` | `domain/settings.py` (`polygon_api_key`) | Yes |
| `ALPHADECK_POLYGON_SPLITS_PATH` (`/v3/reference/splits` = fallback) | `domain/settings.py` | Yes |
| `ALPHADECK_EDGAR_RATE_PER_SEC`, `ALPHADECK_USASPENDING_RATE_PER_SEC`, `ALPHADECK_HTTP_TIMEOUT_S` | `domain/settings.py` | No |
| `ALPHADECK_PRICE_SETTLE_TIME`, `OPENFIGI_API_KEY`, vendor base URLs | `domain/settings.py` | No |

## Known limits

- **The SEC budget is per database.** Dev and prod run on the operator's machine and share its IP but not a
  database, so live SEC work on dev during the prod nightly can push the IP past SEC's ceiling.
- **A second pass started within five minutes of another** reads the first pass's cached indexes for the
  companies it reached last.
- **A long outage leaves holes a resumed feed cannot see.** The price pull reads a fixed two-year window, so
  older bars are never requested again (the repair is a hand-run wider pull). Filing legs read only the
  submissions `filings.recent` window (SEC's newest 1,000 or so filings of every form) and never enumerate the
  older `filings.files[]` pages, so a filing that scrolled out during an outage is lost without a trace
  (`pipeline.backfill_accepted --deep` reads those pages only to date rows already stored).
- **The DOE record date** is not proven never to precede public knowledge: a record can appear on USASpending
  some days after its action date.

## Related

[FILINGS](FILINGS.md), [INGEST](INGEST.md), [FEED_MONITORS](FEED_MONITORS.md),
[PLATFORM_VALUES](PLATFORM_VALUES.md), [DISCOVERY](DISCOVERY.md), [FUND_RADAR](FUND_RADAR.md),
[INVARIANTS](INVARIANTS.md) (1, 3, 9, 11).
