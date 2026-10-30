# Track Record by Cap Tier

> Slug `track-record` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 230`.
> The operator approved the TRADING set and this page in it (2026-10-29: "write the plans"); the choices the build
> makes that this plan does not spell out go under "Choices to confirm" for the operator's review. The rules every
> dashboard follows: `docs/DASHBOARDS.md`; rule 6 (event-outcome views slice by the fixed market-cap bands) is this
> page's whole reason to exist. The fifth page of the TRADING set.

**Label:** Track Record. **Description:** "What happened after each Armed call, by market-cap tier at the event:
fixed horizons, against SPY and IWM, with n and the hit rate in every cell."

It answers the trader's question the Scoreboard does not: "after the platform armed a name, what did the tape do
over the next week, month, quarter, for names of THIS size?" The Scoreboard (`scoreboard/`) scores each arm
episode over ONE horizon (its own `exit_by`), with no benchmark, no fixed horizon and no size slice. The operator's
rule (`CLAUDE.md`, "Event-outcome reports are sliced by MARKET-CAP TIER; a pooled number never stands alone") is
that different things happen to different companies at different market caps. This page is the tiered reading of
the Scoreboard's own episodes: every number states its horizon, its comparison, its n and how often it held, per
tier. Display only: it never feeds a call, never writes, never calls the LLM, and it never says a signal "works".

## The events: the Scoreboard's arm episodes, unchanged

- **An event** is an arm episode exactly as the Scoreboard derives it (`replay/episodes.py::derive_episodes` over the
  honest timeline `scoreboard/record.py::thesis_timeline`): the first night a member appears in a card's
  `armed_members`, keyed (thesis, security, `arm_date`); a re-arm is a new episode. The page imports ONLY what
  `app/routers/scoreboard.py` already imports (the lean prod image has no `duckdb`; the Scoreboard route proves
  these modules load without it).
- **Honesty filters are the Scoreboard's**, with the same query parameters: `include_annotated` and `include_late`
  (defaults from `scoreboard_exclude_annotated` / `scoreboard_exclude_late`), `edition` (**omitted = 0, the calls
  log** while the editions nightly is paused; N = a stored edition; unlike the Scoreboard, which defaults to the
  newest published one). Reconstructed nights are never events. The Scoreboard's eligibility rule
  holds too: a `censored_start` episode (armed on the card's first recorded night) and an `ingest_flagged` one are
  counted on the work line and left out of every cell.
- **The as-of caps everything** (invariant #1): an episode with `arm_date` after the as-of is not an event; a
  horizon that has not matured by the as-of is not counted (its episode shows as "maturing"); every bar read is on
  or before the as-of through the request's one `serve_view(asof)` pair.
- **Episode facts carried onto each row:** `is_headline`, `key1_source`, `entry_grade`, `confirmation_grade`,
  `co_arm_bucket`, `exit_by`, `arm_until`, `close_reason` (the Scoreboard's `Episode` fields) for the filter chips.

## The measurement (per episode)

- **Entry** = the Scoreboard's own entry close: the first close on or after `arm_date` (`first_close_on_or_after`),
  so this page's "to exit_by" column reconciles with the Scoreboard's `forward_return` to the cent. A toggle
  **"Entry: next session"** moves the entry to the first close strictly after `arm_date` (the first session the
  operator could have acted on a call recorded at 22:30 ET); the page says which is on.
- **Horizons, in SESSIONS of the security's own bars, not calendar days:** 5, 10, 20, 60 sessions after the entry
  bar, plus **"to exit_by"** (the Scoreboard's horizon: the last bar on or before `exit_by`). A horizon's close is
  the as-traded close of the Nth bar after the entry bar; if the security's bars end before the Nth bar (a
  delisting, a halt, a stale tape), that horizon reads "path ended" and is not counted (listed on the work line).
  A name on the flagged-price list (`baskets.price_flags`, rule 5) is left out of every cell and counted.
- **Return** = horizon close / entry close − 1, as traded (no dividends).
- **Comparison** = the same span on **IWM** (the default: most armed names are small) and on **SPY**, each
  benchmark's close on the entry bar's date and the horizon bar's date (`securities/benchmarks.py`, the Relative
  Strength member's reference rows). **Excess** = the name's return minus the benchmark's. A selector switches the
  table between IWM, SPY and "raw" (no comparison; the column header says so).
- **Market cap AT THE EVENT** (`CLAUDE.md`'s formula; `docs/BACKTEST.md` §"Sliced by market cap"): the as-traded
  close of the session BEFORE `arm_date` × the last share count reported BEFORE `arm_date`
  (`pit.shares_outstanding_facts(sid)`, the newest `valid_from < arm_date`), both on the same split basis
  (`domain/splits.py::scale_after` from the share fact's date through the close's date), divided by the ADS ratio
  when known. It reuses the Peer Table's pure `cap_cell(CompanyReads(...))` (`dashboards/peer_table.py`) with the
  inputs chosen at the event instead of at the as-of, so the two pages cannot disagree on the arithmetic. The tier
  is `domain/market_cap.py::cap_tier`; a cap that cannot be pinned (no close before the event, no share count before
  it, an unread ADS ratio) puts the episode in an **"unpinned"** row, counted, never dropped. Every cap is labeled
  approximate.
- **Liquidity at the event:** the mean close × volume over the 20 sessions before `arm_date` (the Volume Regime
  member's arithmetic, `signals/display/volume_regime.py`, applied at the event date), in three bands (PROPOSED):
  under $1M · $1M to $10M · $10M and over; "unpinned" when fewer than 20 volume-bearing bars precede the event.

## The table

Rows = the five tiers in size order (nano · micro · small · mid · large), then **unpinned**, then **all tiers
(pooled)** LAST and labeled pooled, never first, never bold. Columns = the five horizons. A cell holds, for the
episodes of that tier matured at that horizon: **n**, the **median excess** (or median raw return under "raw"),
the **hit rate** (the share with excess > 0; raw return > 0 under "raw"), and the interquartile range on hover.
A cell with **n < 10 prints only its n and "too small to read"**; a toggle "show thin cells" draws its numbers
grayed. A cell with n = 0 is blank with "no matured episodes". Every cell's hover states the horizon in sessions,
the comparison, n, the hit rate and the medians of the name's and the benchmark's raw returns, so the four things
the rule demands are never apart.

**Slice selector:** by cap tier (default) or by liquidity band (the same table, rows swapped). **Filter chips**
(each narrows every cell and restates n): headline names only / every armed member; `key1_source` (insider ·
activist · ratified catalyst · corporate catalyst · revenue acceleration · theme); `entry_grade` (flip · core);
`co_arm_bucket`. Chips never cross-tabulate: one table, one slice dimension, the rest filters, so thin data stays
readable.

## The episode list (below the table)

One row per counted episode: ticker (opens the Cockpit on the name, `onSelectThesis(thesisId, ticker)`), card
chip, `arm_date`, tier and approximate cap (hover: close date, share count date, ADS ratio, split factor), the
liquidity band, `key1_source`, grade, then the five horizons' excess (or "maturing" / "path ended"), sorted by
`arm_date` newest first. Clicking a table cell filters the list to that cell's episodes. The unpinned episodes
and the left-out ones (flagged, censored start, ingest flagged) have their own folded sections, each counted.

## Loudness

The trading set's **tape look** (dense rows about 26 px, tabular numerals in the mono font, a sticky header, no
cards or chrome): the sign tone (the app's positive and negative mixed into gray, the Rotation Quadrant's recipe)
only on signed excess values; nothing bolded, no badges. The pooled row is visually the quietest row. No sentence
on the page judges a result: the wording is "median excess vs IWM over 20 sessions, n = 14, held 9 of 14".

## The quiet "show the work" line

The as-of; the clock; the edition and the honesty policies in force; episodes total / matured per horizon /
maturing; unpinned caps; names left out for a flagged price history (opens to the tickers), censored starts, ingest
flagged, paths ended; the entry rule in force; the comparison in force; the cache state and `compute_ms`. Below it,
fixed: "cap is approximate and measured the session before the arm date"; "horizons are sessions of the name's own
bars"; "as-traded closes, no dividends"; "a cell under 10 episodes is too small to read".

## Cache

`dashboards.cache.cached(conn, "track-record", (edition, include_annotated, include_late, clock), asof, ...)` holds
every episode's row with all five horizons, both benchmarks' spans, the raw return, the cap and the liquidity
figure. Tiering, slicing, filtering and the entry toggle derive from that one read (the "next session" entry needs
the second entry close, so the row carries both entry closes and both sets of horizon returns). The cache clears on
a new run or a basket edit (rule 7).

## Backend and frontend

- `backend/dashboards/track_record.py`, pure: `horizon_closes(bars, entry_index, sessions)`, the excess and hit
  arithmetic, `event_cap(...)` (chooses the inputs at the event and calls `cap_cell`), `event_adv(...)`, the tier
  and liquidity bucketing, the cell statistics with the n < 10 rule, the counts.
- `backend/app/routers/dashboards/track_record.py`: `GET /dashboards/track-record?asof=&edition=&include_annotated=
  &include_late=` (every episode row + the counts; the client builds the table). `known_at, clock = serve_view(asof)`
  threads into the Scoreboard read, every `PointInTimeData` and the benchmark read. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_track_record.py`: horizons count SESSIONS (a weekend and a holiday gap do not shift
  the Nth bar); a path that ends before the Nth bar reads "path ended" and is not counted; excess against a benchmark
  on hand-built bars; median, hit rate and the n < 10 rule; the cap at the event uses the close BEFORE the arm date
  and the share count filed BEFORE it (a share fact filed after the arm date is never used); an unread ADS ratio
  lands in "unpinned"; the liquidity bands; the entry toggle (the two entry closes on one row); and, DB-backed, **an
  episode armed after the as-of is not an event** and **a horizon not matured by the as-of is not counted** (the same
  episode at two as-ofs), plus a flagged price history left out and counted; the router off one cached read.
- `frontend/src/dashboards/track-record/`: `entry.ts` (slug `track-record`, order 230, `asofAware: true`),
  `TrackRecord.tsx` (the table, the selectors and chips, the list; a pure `recordView.ts` for the cell statistics
  and the filtering), `useTrackRecord.ts`, `track-record.css`, tests.
- **Layout:** the tape look; the table first, the work line above it, the list below; on a phone the table
  scrolls sideways and the list stacks its horizons under each row.

## Known limits (stated on the page too)

- **The record is young.** Most cards are weeks old; the 60-session and exit_by columns are mostly maturing. The
  page shows that as n and "maturing", never as a result.
- **The entry is the arm night's close** by default (the Scoreboard's rule), which a trader could not have taken;
  the "next session" toggle shows the actionable version.
- **Cap and liquidity are approximate** and measured the session before the arm date.
- **As-traded closes, no dividends; no survivorship fix:** a delisted name's path ends and is counted as such.
- **No null comparison here.** The timing null and the name null live in the backtest's pooled report
  (`backtest/nulls.py`), vs the thesis basket; this page compares to IWM and SPY only.

## Out of scope

Any "works" verdict or score; any cross-tabulation of two slice dimensions; position sizing or instrument choice
(#4, #5); nulls; options or intraday data; any change to the Scoreboard or the editions; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: the Scoreboard's episodes and honesty filters as the events;
the record (edition 0) as the default view while the editions nightly is paused, with a selector to the newest
published edition (2026-10-29); sessions 5 / 10 / 20 / 60 plus exit_by as the horizons; IWM as the default comparison with SPY and raw selectable;
the arm-night close as the default entry with a next-session toggle; the three PROPOSED liquidity bands; n < 10
prints n only; the pooled row last.

### Built choices to confirm

The build (2026-10-29) followed the plan above. These are the choices it had to make that the plan does not spell out,
each the operator's to confirm or change. Nothing here was measured on a prod copy; the compute and the wire shape are
pinned by tests on hand-built numbers, and the first real read is the orchestrator's.

**Maturing against path ended (the plan names both and not the line between them).**
- A horizon's bar is the Nth bar after the entry bar in the name's own bars. When it is not there the span reads
  **path ended** only if BOTH hold: the market has had that many sessions since the entry, and the name's tape stops
  more than `stale_days` calendar days behind the market's last bar. Otherwise it reads **maturing**. A name that is
  current but short (a vendor gap) is maturing, never path ended.
- "The market" is SPY's bar dates (IWM's when SPY has none), the Narrative Index's session calendar. With no market
  tape at all nothing can read path ended.
- `stale_days` is `Settings.tape_stale_days` (the recency monitor's threshold, 5); `0` disables that monitor, so the
  page keeps 5. The comparison is strict: 5 days behind is not behind, 6 is.
- No entry bar yet (no bar on or after the arm date): path ended if the tape is behind the market, else maturing
  (tonight's bar is not in).
- **to exit_by**: not counted before `exit_by` has arrived (maturing); no `exit_by`, or no bar between the entry and
  `exit_by` for that entry rule, reads **none** (a fourth status, "nothing to measure"); the tape stopping short of
  `exit_by` while behind the market is path ended. Otherwise it is the last bar on or before `exit_by`, so it equals
  the Scoreboard's `forward_return` to the cent (a DB test pins that on the arm-night entry).

**The comparison.**
- A benchmark's return uses its close on exactly the entry bar's date and the horizon bar's date. A missing benchmark
  bar leaves that comparison blank for that span, never a nearby stand-in; the cell counts those spans apart
  ("1 more had no IWM bar") and a raw cell needs no benchmark.
- A hit is strictly above zero (an excess of exactly 0.00 did not hold). The interquartile range is linear
  interpolation between order statistics (the median of an even n is the mean of the two middle values).

**The cap at the event.**
- The share count is the newest fact whose `valid_from` is before the arm date, as the plan says. That date is the
  cover date, which precedes the filing by weeks, so a count could in principle have been filed after the arm date.
  The fact's public instant is the operator's ratification (later for every old fact), so requiring it before the arm
  date would leave nearly every past event unpinned. At a past as-of the point-in-time view still hides a fact
  ratified after the as-of.
- The close is the last session before the arm date, put back to the price as traded that day (the bars are on the
  view's split scale) before the Peer Table's `cap_cell` puts the count on the close's scale. A reverse split after the
  event does not inflate the cap, and a test pins it.
- A tier boundary belongs to the higher band (`domain/market_cap.py`'s own rule).

**Liquidity.** The Volume Regime member's `compute` run over the last 20 volume-bearing bars strictly before the arm
date (a bar with no volume is skipped, not read as zero). A value on a band's bound is in the higher band; a day of zero
dollars is "under $1M" (a real measurement); under 20 volume-bearing bars is unpinned.

**Which episodes.**
- Archived cards are included (the Scoreboard's default). The route takes the Scoreboard's `edition`,
  `include_annotated` and `include_late` and resolves the edition with the Scoreboard's own function (404 on an
  unknown one). The page has a selector for the edition only (below); its first work line states the reading in force.
- An episode left out is counted once, in this order: flagged price history, censored start, ingest flag. A left-out
  row carries identity only (nothing is read for it).
- **A stopped price feed is a flagged price history** by the shared rule (`baskets.price_flags` includes
  `price_feed_stopped`, which reaches every price cell). So at the live as-of a delisted name, or one whose vendor
  symbol went quiet, is "left out: flagged" with its reason named, not "path ended". Either way no cell counts it,
  which is the plan's "no survivorship fix" limit; if the operator wants stopped-feed names to read path ended
  instead (and stay visible in the denominators), it is one decision in `read_record`.
- Reads use the default tenant, like every dashboard (auth is deferred).

**The table and the page.**
- The pooled row is every counted episode, unpinned ones included, so the rows add up to it. A left-out episode is in
  no row. A tier with no episode is still a row (n = 0, "no matured episodes" in its hover).
- A cell under 10 episodes prints `n 4` and "too small to read"; "show thin cells" draws its numbers grayed with no sign
  tone. An empty cell is an em dash.
- Filters default to every armed member. Within one chip group any picked value passes, across groups all must; a
  group with a single value has no chips (WB#3). A null Key-1 source or entry grade is its own "none stated" chip.
- A cell click lists exactly the episodes the cell rests on (a counted span at that horizon, and the comparison's
  return unless raw); changing the slice, comparison, entry or filters clears the pick. The main list holds the
  counted episodes whose slice row is pinned (the unpinned ones are in their own fold), newest arm first, 150 at a
  time.
- Cell statistics are written twice, a Python reference (`track_record.cell_stats`) and its mirror in `recordView.ts`,
  with the same hand-computed vectors pinned in both test files. The response also carries `opening`, the table at the
  opening choices (IWM, arm-night entry, by cap tier, every armed member) computed by the server; the page does not
  draw it, but the frontend test reproduces it cell by cell from `rows`, so the two cannot drift apart.
- The plan's `horizon_closes` is named `horizon_close` (it returns one bar). Price reads are unbounded in time and
  chunked 40 companies per point-in-time view (an episode can sit anywhere in a name's history).
- The cache key is the plan's tuple with the RESOLVED values: the edition number, then whether annotated and late
  nights are included (fixed for a stored edition, which has none of those rows), then the clock.

**The edition: the record by default while the editions nightly is paused (2026-10-29, after the dev walk below).**
- **The route's `edition` defaults to 0**, the calls log, when the caller passes nothing (`Query(0, ge=0)`). It is
  the Scoreboard's own resolver (`_resolve_edition`) that still answers a named N, with its 404. The Scoreboard route
  itself is unchanged: it still defaults to the newest published edition.
- **The response carries `newest_edition`** (`{number, label}`, `null` when nothing is published): the newest
  PUBLISHED edition by `editions_repo.newest`, one small read outside the cache. It is deliberately not the
  Scoreboard's `_default_edition`, which a server setting (`scoreboard_default_edition`) can pin to another edition:
  the page's selector says "newest published", and this is that. With no override set the two are the same edition.
- **The page's selector** "Edition: The record · Edition N (newest published)" refetches with `edition=`. The record
  is pressed by default; the control renders only when an edition is published (one option cannot discriminate,
  WB#3). Picking an edition clears a picked cell; the slice, comparison, entry and filter choices stay. Each edition
  is its own server read and its own cache entry.
- **The work line says which is in force.** For the record it reads "edition 0: the original record (the default;
  editions are paused)"; for a stored edition "edition N: <label>". "Editions are paused" is the operator's hold on
  the editions nightly (2026-10-26), stated as a fact about the system and not computed from the data: **when the
  editions nightly resumes, flip the route's default back to the Scoreboard's resolver and drop that clause in
  `recordView.ts::editionWords` in the same change.**
- `include_annotated` and `include_late` are unchanged.

### MEASURED (dev, prod copy, 2026-10-29)

The orchestrator's walk on the dev stack (a prod data copy), as-of 2026-10-29, BEFORE this change (the page defaulted
to the newest published edition). These numbers are the orchestrator's, recorded here as the brief gave them; the
build re-measured nothing.

- **Edition 1** (cut 2026-10-23; the editions nightly is OFF by the operator's hold, so it is also the newest): 11
  cards, 501 episodes, 439 counted.
- **The record** (`edition=0`, the calls log): 46 cards, 469 episodes, 240 counted, h20 148 counted.
- **Cost:** cold compute 12.0 s (Edition 1) and 14.8 s (the record); a cache hit 50 ms; payload 884 KB.
- **Why the change:** the default view covered 11 of the 46 cards the record covers, from a frozen cut, so a
  reader opening the page saw the older and smaller picture without asking for it.

**AFTER (the orchestrator fills these in from the dev stack, same as-of 2026-10-29):**

- no `edition` (now the record): cards 46 · episodes 469 · counted 240 · h20 counted 148 · `newest_edition` {number 1, "first re-run"}
- `edition=1`: cards 11 · episodes 501 · counted 439
- cold compute 16,909 to 17,069 ms · cache hit 50 to 100 ms · payload 574 KB

