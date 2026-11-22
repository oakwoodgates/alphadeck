# The fund launch radar

The fund launch radar is the "Coming" face of the ETF radar: it catches new fund filings on EDGAR the night they
go public, follows each fund from filing to listing, and shows which cards each one fits. The machine scans
nightly; any signed-in user reads the tape and their own card's matches and may add a matched fund to a card's
ETFs link. Display only: it never feeds a call. `SignalKind.ETF_LAUNCH` stays reserved and nothing emits it
(`docs/CALL_LOGIC.md` section 1), and no radar module imports `calls/`, `signals/` or `llm/`
(`tests/radar/test_fund_match.py::test_the_radar_never_imports_the_call_machinery`). Read this before you change
the scan, its tables, the matching or the fund routes and screens.

## Code

| Piece | Module |
|---|---|
| The scan (daily index, headers, symbol files) | `radar/funds.py`, `radar/fund_header.py` |
| Store | `radar/fund_repo.py`; migration `0074_fund_radar.sql` |
| Stages; matching | `radar/fund_stage.py`; `radar/fund_match.py` over `radar/matcher.py` |
| Editable word lists | `radar/fund_wrapper_words.txt`, `radar/fund_name_stopwords.txt` |
| CLI | `pipeline/fund_radar.py` |
| Routes | `app/routers/fund_radar.py` |
| Screens | `frontend/src/radar/FundLaunches.tsx`, `frontend/src/cockpit/FundLaunchesLine.tsx` |

## What it reads

| Source | Gives | How |
|---|---|---|
| EDGAR daily index | the day's filings | `ingest/edgar/dailyindex.py`, the SPAC radar's reader; a missing day is a quiet skip |
| The filing header (`<accession>.hdr.sgml`) | form, acceptance, filers, every fund series and class | `EdgarClient`; an immutable cache key, so a re-run costs nothing |
| SEC `company_tickers_mf.json` | series to symbol | `EdgarClient`; a mutable key, so the recurring freshness rule applies |
| Nasdaq Trader `nasdaqtraded.txt`, `otherlisted.txt` | which symbols trade, with ETF and test-issue flags | a polite GET; not an SEC host |

Forms kept (`FUND_FORMS`, `S1_FORMS` in `radar/funds.py`): 485APOS (a new fund in an existing trust, the main
source), 485BPOS (a fund going effective), N-1A and N-1A/A (a new trust), and S-1 or S-1/A only when the filer's
name matches trust, ETF or fund. Crypto and commodity funds register that way; their header names no series, so the
filer's name is the fund name and the row is name-only. The index has one row per co-registrant, so rows are
de-duplicated by accession.

## Public dates

A filing counts from the day it became public (invariant 1):

- a private-to-public filing (an old confidential filing SEC released) counts from its release day, read from the
  header's first line, never from its old acceptance;
- otherwise from the night of its acceptance (`domain.market_time.edgar_night`): an evening acceptance counts that
  evening, though SEC dates it the next business day;
- with no acceptance, the filing date.

`recorded_at` is bookkeeping. Neither symbol file carries a date, so a symbol counts from the night it was first
seen, and a `--since` backfill can mark the symbol files only once, on the night it runs. The filings themselves
backfill exactly, because every header has its own acceptance time.

## Storage

Migration 0074; append-only and store-if-changed. Idempotency tests count the tables, not the read.

| Table | One row per | Holds |
|---|---|---|
| `fact_fund_filing` | accession | form, filers, acceptance, filing and effectiveness dates, the private-to-public flag and release date, the public date (`valid_from`), link |
| `fact_fund_series` | accession and series | kind `new`, `new_classes` or `existing` (a series listed twice keeps the strongest kind and the union of classes); an S-1's name-only row is keyed `cik:<filer cik>` |
| `fact_fund_symbol` | series, class, symbol and source (`sec_ticker_file` or `nasdaq_directory`) | dated by the first night seen; only funds some filing announced as new are followed to a ticker |

## Stages

Derived on read in `radar/fund_stage.py`, never stored, so the rules can change with no backfill:

| Stage | Reached when |
|---|---|
| filed | the first public date the series appears as new |
| effective | the first 485BPOS public date, on or after filed, that lists the series |
| ticker | the first night SEC's ticker file showed a symbol for the series |
| listed | the first night Nasdaq Trader's directory showed one of its symbols as an ETF, not a test issue |

## Matching

`radar/fund_match.py` reads the fund name with plain word matching and no model, reusing `radar/matcher.py`'s
word-boundary rules. Cards are passed in, so a term edit applies at once and a new card sees past launches. Both
reads are display only.

- **The theme read** matches the name against the card's term set. Any hit, SIGNAL or BROAD, is a match; the match
  keeps both lists and a tier (`signal` when any SIGNAL term hit, else `broad`). Why BROAD counts alone: fund names
  are short, and SIGNAL terms alone missed most real theme launches.
- **The name read** matches the name against the card's company members (not funds), in any link, each labeled
  with its link. It aims at single-stock wrappers ("2X Long `<TICKER>`").
  - a ticker of three or more characters matches alone, as a case-sensitive word;
  - a one- or two-character ticker matches only when the name also holds a wrapper word from
    `fund_wrapper_words.txt`;
  - the company name, legal forms stripped, matches as a whole phrase when it is two or more words or one word of
    five or more letters;
  - a matched token on `fund_name_stopwords.txt` (common fund words, sponsor and index brands) never counts, nor does
    a cleaned company name that is itself a wrapper word;
  - trademark marks are stripped first, and a hyphen is part of a word;
  - the matched token is returned as provenance.

Private-to-public filings are left out of matching unless asked for: stored and counted, never lost.

## Errors, cost and the CLI

An unreadable header goes on the run's `errors` and nothing is stored for it, so the next night's window retries it;
it is never silently skipped. A symbol file that cannot be read is listed and the other leg still runs.

All SEC reads go through `EdgarClient` with the recurring freshness rule, so they share the global SEC budget
([DATA_SOURCES](DATA_SOURCES.md)): a few dozen requests a night. A backfill is a hand-run CLI, never part of the
nightly, and costs about one request per header.

`python -m pipeline.fund_radar` (operator; flags in `--help`): scan the last days live, `--since` a backfill oldest
first, `--no-live` cache only, `--report` a read-only report per live card counted by filing and sponsor, not by
fund, so one batch cannot look like a wave. The nightly pass runs the scan as a passenger leg after the SPAC radar
(`pipeline/daily.py`): live passes only, its own connection, timed, fail-open ([FEED_LOOP](FEED_LOOP.md)).

## What you see

The routes read the stored facts, derive stages and matches on read, make no SEC request, and never feed a call.
Bodies: `backend/openapi.json`.

| Route | Who | Does |
|---|---|---|
| `GET /radar/funds` | anyone signed in | the tape: one row per filing, newest first by public date, each fund with its stages and every live card it fits in the caller's workspace |
| `GET /theses/{thesis_id}/fund-launches` | anyone signed in, own workspace | one card's matches as of a night |
| `POST /radar/funds/attach`, `/detach` | anyone signed in, own workspace | add a fund to the card's ETFs link, or remove it |

- **The tape's counts** (filings, funds, matched, unmatched, old filings held back) always cover the whole window,
  so the screen can say "N more"; `matched_only` trims only the rows.
- **The window rule:** a fund is in a card's window when any of its stage dates falls inside it, because a fund gets
  its ticker months after its filing.
- **The listing rule:** a ticker or listing date counts as activity only when it is later than the night the fund's
  new-fund row was first stored. A backfill stores old funds already trading, whose symbols are first seen that same
  night; such a fund still shows its stages. The stored time only judges whether a first-seen date is an observed
  arrival; history still filters by public date and symbol night at or before the as-of (invariant 1). The night is
  the market date of the stored time.
- Every stage comes only from facts public by the as-of, so a later 485BPOS or symbol never shows on a history view.
  The answer carries `symbol_history_since`, the earliest symbol night visible. The Cockpit's frozen MCP capture
  reads the same function (`read_fund_launches`).
- **Attach** is the user's click (invariant 10) and reversible (WB#1). The symbol must be a well-formed ticker
  (422 otherwise). A plain user may attach only a fund already in the shared company list
  (`instrument_kind='etf'`), else 404 with nothing written; a steward's attach resolves the symbol through the same
  function as `POST /workbench/securities/resolve-etf`, which may create the master row. Attach builds the member
  as "Surface an ETF" does and rewrites the card whole under the promote's own rules (`silent_conflicts`,
  `fund_conflicts`), so a fund never lands outside ETFs; a conflict is a 422 with nothing written. A fund already
  there answers `already`. After the commit it starts the fund's data load as a promote does (`start_member_load`,
  [PLATFORM_VALUES](PLATFORM_VALUES.md)). Detach removes only that fund's ETFs row.

**The Radar page** has a "Fund launches" tab, chosen by `?tab=funds` so it links and survives a reload: one row per
filing with its stages, matched cards, name hits and filing link. By default it shows only filings that fit a live
card; the unmatched and the old released filings are one click away and their counts stay visible.

**The Cockpit line** sits between the basket table and the evidence: collapsed, its own query at the Cockpit's
as-of, and not rendered when the count is 0 (invariant 7). Expanded, each matched fund at the ticker stage or later
offers add to ETFs (attach) and, once in, remove (detach). The screen pulls no prices itself.

Two in-process caches (the loaded fund rows and the computed tape) clear when something new happens: the fund
tables' newest `recorded_at`, the cards' change marker or the market day; never on a clock.

## Known limits

- A Nasdaq listing is found only through a ticker already known (from SEC's ticker file or a filing that names it),
  so a backfill that loads filings out of date order can date an old listing to the night a later-loaded filing
  first named its ticker. The nightly loads in order, so it does not recur there.
- `in_etfs` reads the card's basket as it stands now, not as of the night.
- Ticker and listing dates are the nights the symbol was first seen, so a fund already trading when the radar
  started reads the night the files were first read.
- A one- or two-character ticker inside a private-company wrapper name still hits when wrapper words are present.
  It is display only and the matched token is shown.

## Related

[DATA_SOURCES](DATA_SOURCES.md), [FEED_LOOP](FEED_LOOP.md), [BOARD](BOARD.md) (the Cockpit),
[dashboards/etfs](dashboards/etfs.md), [CALL_LOGIC](CALL_LOGIC.md).
