# The fund launch radar

The "Coming" face of the ETF radar (`DATA_SOURCES.md` §"Notes on the ETF radar's three faces"): catch new fund
filings on EDGAR the night they go public, follow each fund from filing to listing, and show which cards each one
fits. **Display only** (Kind B: context). It never feeds a call: `SignalKind.ETF_LAUNCH` stays reserved and
nothing emits it (`CALL_LOGIC.md` §taxonomy), and the radar's modules never import `calls/` (a test proves it).
PR 1 built the tape, the nightly leg and the CLI report; PR 2 the API and the screens (§"What you see").

## What it reads

| Source | What it gives | How |
|---|---|---|
| EDGAR daily index | the day's filings | `ingest/edgar/dailyindex.py`, the SPAC radar's reader; a 403/404 day is a quiet skip |
| The filing header, `<accession>.hdr.sgml` (~2 KB) | form, acceptance time, filers, every fund series and class | `EdgarClient`, cache key `forms/<accession>/hdr.sgml` (immutable, so a re-run costs nothing) |
| SEC `company_tickers_mf.json` | series ID to symbol | `EdgarClient` (the global SEC budget; a mutable key class, so the recurring TTL applies) |
| Nasdaq Trader `nasdaqtraded.txt` + `otherlisted.txt` | which symbols trade, with an ETF flag and a test-issue flag | the polite GET; not an SEC host |

**Forms kept:** 485APOS (a new fund added to an existing trust: the main source), 485BPOS (a fund going
effective), N-1A and N-1A/A (a new trust), and S-1 / S-1/A **only when the filer's name matches** `trust|etf|fund`
(crypto and commodity funds register this way; their header names no series, so the filer's name is the fund name
and the row is "name only"). The index has one row per co-registrant, so rows are de-duplicated by accession.

## The dates (invariant #1, the public clock)

A filing counts from the day it became public:

- a **private-to-public** filing (an old confidential filing SEC released; 27 of 55 unique 485APOS in the measured
  week) counts from its **release day**, read from the header's first line (`<SEC-HEADER>….hdr.sgml : 20261104`),
  never from its old acceptance;
- otherwise from the **night of its `ACCEPTANCE-DATETIME`** (`domain.market_time.edgar_night`): an 18:00 acceptance
  counts that evening even though SEC dates it the next business day;
- with no acceptance, the filing date.

`recorded_at` is only bookkeeping. **Neither symbol file carries a date**, so a symbol counts from the night we
first saw it; a `--since` backfill can only mark the symbol files **once, tonight**. The filings themselves
backfill exactly, because every header has its own acceptance time.

## What is stored (migration 0074; append-only, store-if-changed)

- `fact_fund_filing`: one row per accession (form, filers, acceptance, filing date, effectiveness date,
  private-to-public flag, release date, public date = `valid_from`, link).
- `fact_fund_series`: one row per (accession, series). Kind is `new` (a new fund), `new_classes` (new share classes
  on an existing fund) or `existing`. A header can list a series twice (once under new classes, once under
  existing); the row keeps the strongest kind and the union of the classes. An S-1's name-only row is keyed
  `cik:<filer cik>` with no series ID.
- `fact_fund_symbol`: one row per (series, class, symbol, source = `sec_ticker_file` | `nasdaq_directory`), dated
  by the first night seen. Only funds some filing announced as **new** are followed to a ticker (a 485BPOS lists
  hundreds of long-standing mutual-fund classes).

Idempotency tests count the tables, not the read.

## Stages (derived on read, never stored: `radar/fund_stage.py`)

| Stage | Meaning |
|---|---|
| filed | the first public date the series appears as NEW |
| effective | the first 485BPOS public date (on or after filed) that lists the series ID |
| ticker | the first night SEC's ticker file showed a symbol for the series |
| listed | the first night Nasdaq Trader's directory showed one of its symbols as an ETF (not a test issue) |

Because the stage is read, the rules can change with no backfill.

## The two reads (`radar/fund_match.py`; derived on read, plain word matching, no LLM)

Both read the **fund name** and reuse `radar/matcher.py`'s word-boundary rules, so "IND" cannot match inside
"INDEX". Cards are passed in; a term edit applies at once, and a new card sees past launches.

- **Theme read:** the name against the card's term set. **Any hit, SIGNAL or BROAD, is a match**; the match keeps
  both lists and a tier ("signal" when any SIGNAL term hit, else "broad"), and the report shows it. BROAD counts
  on its own because fund names are short (operator decision, reversing the signed plan's Q1): a dev check over 24
  months found SIGNAL alone matched 5 funds while BROAD added 66, nearly all real theme launches. Display only,
  never a call input.
- **Name read:** the name against the card's **company** members (basket rows whose master row is not a fund, in
  any link, each labeled with its link; the silent links count). Aimed at single-stock wrappers ("2X Long
  `<TICKER>`", "`<TICKER>` PowerMaxx"), about 82% of new funds in the measured week.
  - a ticker of 3+ characters matches alone, as a case-sensitive word token;
  - a 1-2 character ticker matches only when the name also holds a **wrapper word**, from the editable
    `backend/radar/fund_wrapper_words.txt` (one per line, case-insensitive);
  - the company name with legal suffixes stripped matches as a whole phrase, case-insensitive, only when the
    cleaned name is 2+ words or one word of 5+ letters.
  - a cleaned company name that is itself a wrapper word never matches, and any matched token (a ticker or a
    cleaned company name, any word count) never counts when it is on the editable stoplist
    `backend/radar/fund_name_stopwords.txt` (same format; common fund-name words and fund-sponsor / index
    brands), so a company called "Income Corp" is not every "... Daily Income ETF";
  - trademark marks ((TM), (R), (SM) and the symbols) are stripped from the fund name first, and a hyphen is part
    of the word (a ticker or name never matches beside one);
  - legal forms are stripped first (dots removed, so "N.V." and "S.A." go; also Group, NV, SA, SpA, plc, Ltd,
    Co, a trailing "& Co", a leading "The"), so "Zorbex Group N.V." can match "Zorbex";
  - the matched token is returned as provenance.

  **Known, accepted noise:** a 1-2 character ticker inside a private-company wrapper name still hits when wrapper
  words are present ("Acme 2X Long Zorbex AI Daily ETF" hits a company with ticker AI). It is display only,
  and the report shows the matched token, so it is easy to ignore.

Private-to-public filings are left out of matching unless a flag asks for them (stored and counted, never lost:
recall is kept, loudness is honest).

## Errors and recall

An unreadable header goes on the run's `errors` and nothing is stored for it, so the next night's 3-day window
retries it. It is never silently skipped. A symbol file that cannot be read is listed (`symbols_skipped` for a
cache-only run, `errors` for a real fault) and the other leg still runs.

## Cost

About 20-45 SEC requests a night: 1-3 index files (a finished day's index is cached forever), ~6-30 headers for the
APOS/N-1A forms, ~10 for 485BPOS, 1 ticker file; plus 2 Nasdaq files (not SEC). All SEC reads go through
`EdgarClient` with the recurring TTL, so they share the global budget. Backfill is a hand-run CLI, never inside the
nightly: roughly 25 headers a session, so 24 months is on the order of 12-15k requests at the shared rate.

## Running it

```
python -m pipeline.fund_radar --days 3                       # scan + symbol files (live)
python -m pipeline.fund_radar --since 2024-11-01             # backfill, oldest first; symbol files marked once, tonight
python -m pipeline.fund_radar --no-live                      # cache-only
python -m pipeline.fund_radar --report [--window-days 90] [--include-private]   # read-only
```

`--report` prints, per live card, the theme matches (fund, filing, public date, stage, tier, the SIGNAL / BROAD terms that hit; the card header splits the filing count by tier)
and the name hits (company, token, fund), counted by **filing and by sponsor, not by fund** so one batch cannot look
like a wave; then the totals: new funds in the window, matched, unmatched, and private-to-public held back.

The nightly runs it as a passenger leg right after the SPAC radar (`FEED_LOOP.md` §"The fund launch radar"):
`allow_live` only, its own connection, `timed("fund_radar")`, fail-open.

## What you see (PR 2: the display)

All display only (Kind B): the routes read the stored facts, derive stages and card matches on read, make no SEC
request, and never feed a call. Code: `backend/app/routers/fund_radar.py`, `frontend/src/radar/FundLaunches.tsx`,
`frontend/src/cockpit/FundLaunchesLine.tsx`.

**Routes**

- `GET /radar/funds?days=90&include_private=false&matched_only=true`: one row per FILING (the filing that first
  announced its funds), newest first by public date. Each filing carries its funds (stage dates, symbols) and, per
  fund, every live card it fits (theme read with its tier and terms, name read with company ticker, link and token).
  The counts (filings, funds, matched, unmatched, old filings held back) **always cover the whole window**, so the UI
  can say "N more"; `matched_only` only trims the rows.
- `GET /theses/{id}/fund-launches?asof=YYYY-MM-DD&days=90`: this card's matches **as of a night** (invariant #1).
  Only filings public by `asof` and inside `asof - days .. asof`; each fund's stage is derived **only from facts public
  by `asof`**, so a 485BPOS, or a symbol first seen after `asof`, never shows on a history view. Counts by filing and by
  sponsor (never by fund), split by tier, plus name hits. Old confidential filings SEC released are left out. Honest
  limit: `in_etfs` reads the card's basket as it stands **now** (a roster as of a night is not reconstructed for this
  flag), and the ticker / listed dates are the nights we first saw the symbol (neither file carries a date), so a fund
  already trading when the radar started reads that start night.
- `POST /radar/funds/attach` and `/detach` `{thesis_id, symbol}`: the operator's click (#10), reversible (#1). Attach
  resolves the symbol through the **same function as `POST /workbench/securities/resolve-etf`** (lookup-or-create,
  `instrument_kind='etf'`), builds the member as "Surface an ETF" does (role "ETF sleeve", segment ETFs, a model
  draft, signed off, no conviction), and re-writes the card whole from its stored copy (nothing it carries is wiped).
  It runs the promote's own rules (`silent_conflicts`, `fund_conflicts`, the silent links last), so a fund can never
  land outside ETFs; a conflict is a 422 and nothing is written. Already in ETFs is `already`, with no write. Detach
  removes only that fund's ETFs row; not there is a no-op. No prices are pulled by the endpoint: the screen calls the
  existing price ingest on the returned security, as "Surface an ETF" does.

**The Radar page** has two tabs, "SPAC" (the existing tape, unchanged) and "Fund launches", chosen by `?tab=funds` so
it links and survives a reload. The fund tab shows one row per filing: date, fund name (a batch filing is one
collapsed "N funds" row, opened on click), trust, form, stage chips for the stages reached, the matched cards with
their terms and a small tier tag, name hits (company ticker and its link), and a filing link. By default it shows
only filings with a fund that fits a live card; **"show all (N unmatched)"** and **"include old filings SEC released
(N)"** bring the rest back, and the counts stay visible (keep-it-visible). Quiet styling: no red, no badge on every
row.

**The Cockpit line** sits between the basket table and the evidence: "fund launches · N filings from M sponsors in 90
days (signal a · broad b · name c)". Collapsed by default, its own query (it never slows the card), at the Cockpit's
as-of, and **rendered not at all when the count is 0**. Expanded, each matched fund shows its stage chips and card
terms. For a fund at the ticker stage or later: **+ add to ETFs** (attach, then a price pull on the returned
security) and, once in, **✓ in ETFs, remove** (detach).

**Cost.** MEASURED against the dev DB (24-month backfill, 65 live cards): reading every card against about 1,000 funds
took about 60 s before the matcher's substring prefilters, and the first (cold) tape read is now about 1.7 s; a cached
read is about 0.03 s. Two small in-process caches (the loaded fund rows, and the computed tape) are cleared by "something
new happened" (the fund tables' newest `recorded_at`, the cards' change marker, the market day), never by a clock.

## Not built

Slack pages (none at first: inverse loudness), the prospectus-text read for opaque fund names, exemptive applications
(40-APP), the fund launches panel on the ETFs dashboard, and any `etf_launch` signal.
