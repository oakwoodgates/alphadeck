# Insider Buying

> Slug `insider-tracker` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 260`.
> The operator asked for this page on 2026-10-30 from the first brainstorm's list ("you had mentioned an insider buying
> tracker"); the choices the build makes that this plan does not spell out go under "Choices to confirm" for the
> operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`. The eighth page of the TRADING set.

**Label:** Insider Buying. **Description:** "Every open-market insider purchase across the counted cards' names in the
window, newest first: who bought, how much, whether others bought too, whether it became a trigger, and what the tape
did since."

It answers "who is buying their own stock this week": the Leaders page lists names with buys and the groups where
insiders cluster, the Filing Stream shows one card's Form 4s, the Trigger Log shows the buys that became triggers.
Nothing lists the purchases themselves across every card. This page does, one row per Form 4 with a purchase, on the
public clock (a filing counts from the day it was filed, not the trade day). Display only: it never feeds a call, never
writes, never calls the LLM, and it re-derives none of the trigger path's screens.

## What a row is

- **The names:** the counting names of the cards counted at the as-of (`baskets.cards_as_of`, `counted_rows`); Context
  and Bench names join behind a toggle "Include Context and Bench" (off by default), tagged.
- **The filings:** `dashboards/filings.py::read_filing_rows(..., kinds=FORM4_KINDS)` for those names over the window (30 days by default; 90
  selectable), kind `form4` and `form4a` rows that are NOT routine (the Filing Stream's grouping: one row per accession,
  `routine` false means the filing holds a code-P purchase; `usd` is the net dollar size, `tags` carry issuer-self,
  internal-transfer, 10b5-1, applied 4/A, filer-unresolved; the headline and detail lines carry the filer, the role and
  the filer's own words as the reader gives them). A filing with sales only is not a row; the page never re-parses a
  Form 4.
- **Dating:** the row's date is the FILING date (`FilingRow.date`, the acceptance clock); the trade date from the detail
  lines shows beside it with "filed N days after the trade" when they differ.
- **Cluster:** two or more distinct buyers on the same name within 30 days of each other among the rows read (the
  Leaders rule, `CLUSTER_MIN_BUYERS = 2`), computed from the rows themselves; the row shows "cluster · 3 buyers" and the
  names of the other buyers on hover. A repeat buy by the same person is not a cluster.
- **Became a trigger?** The recorded card in effect at the as-of for each card the name sits on (the What Changed read):
  if any insider trigger (`triggers_fired` or a member's `triggers` with `kind` insider) carries this filing's accession
  among its `sources`, the row reads "trigger on <card>" (a chip per card); otherwise "not a trigger" with the hover
  "below the detector's bar or screened (a control holder, an internal transfer); the page does not re-derive the
  screens". Honest and undecided: the trigger path owns that judgment.
- **Since:** the name's as-traded return from the first close on or after the FILING date to the last close on or
  before the as-of (`pit.price_history`), with IWM over the same span; "unpriced" / "flagged: <reason>" rows keep their
  place (`baskets.price_flags`, rule 5).
- **Size and cap:** the dollar size (`usd`), shares and price from the row; the company's approximate cap at the as-of
  and its tier (the Peer Table's `cap_cell` rule; blank with its gap reason), a filter dimension only.

## The columns

Filed · trade date (+N note) · ticker (opens the Cockpit on the first card, `onSelectThesis(thesisId, ticker)`) · card
chips · filer · role (as the filing states it) · shares · price · $ size · cluster · tags · trigger (card chips or
"not a trigger") · since · IWM · cap ≈ and tier.

**Grouping (a selector):** by filing week (default, newest first; each header counts rows and distinct names), by name
(each header: buyers, total $, the cluster word), by card. **Filters** (chips, each restating the count): size bands
($100K · $1M · $10M and over; PROPOSED), role (officer · director · 10% owner · other, from the role words the reader
gives), "clusters only", "triggers only", cap tier, card. **Default order within a group:** filed date newest first,
then $ size.

## No aggregate here

A median move "after insider buys" is an event-outcome statistic, which rule 6 slices by cap tier with horizon,
comparison, n and hit rate: that is the Track Record's job (its `key1_source` filter already isolates insider-keyed
episodes). This page lists; the only numbers across rows are counts (rows, names, buyers, $ total per group).

## Loudness

The trading set's tape look (`docs/dashboards/hot-board.md` §Look). The one accent is the cluster word (the Leaders
page's cluster accent, the same tone), because a cluster is the exception among purchases. The sign tone only on the
signed "since" values. Tags are gray words. Neutral wording.

## Past as-of

The window ends at the as-of; a filing filed after it is not a row (the Form 4/A two-clock gate the Filing Stream
uses); the bars end at the as-of; the trigger match reads the cards in effect at the as-of.

## The quiet "show the work" line

The as-of; the clock; the window; cards counted / names read / Context and Bench (included or not) / flagged (opens
to the tickers); filings read / purchases / routine folded away (counted) / filer unresolved; clusters; rows that are
triggers; the cache state and `compute_ms`. Below it, fixed: "open-market purchases (code P) only; a filing counts
from the day it was filed; no aggregate here: outcomes by tier are on the Track Record page".

## Cache

`dashboards.cache.cached(conn, "insider-tracker", (window_days, include_silent, clock), asof, ...)` holds the window's
rows with their cluster, trigger and since fields; grouping, filters and sorts are client side. The cache clears on a
new run or a basket edit (rule 7). **Cost:** the filing reader over about 1,500 names for 30 days plus bars for the
names with a purchase. MEASURED on a dev stack with a prod copy (as-of 2026-10-29), with the reader reading every kind:
the 30-day cold read took 67.7 s (1,150 names, 1,960 filings read, 90 purchases) and the 90-day read 72 s, over the
minute this plan set, so the reader is narrowed to the Form 4 kinds (built choice 14 below). The re-measure after the
narrowing is not recorded here yet.

## Backend and frontend

- `backend/dashboards/insider_tracker.py`, pure: the purchase rule over `FilingRow`s, `clusters(rows)`, the trigger
  match by accession, the "filed N days after" note, the since span, the grouping keys, the counts.
- `backend/app/routers/dashboards/insider_tracker.py`: `GET /dashboards/insider-tracker?asof=&days=30&silent=false`.
  `known_at, clock = serve_view(asof)` threads into the filing reader, the `calls` read, the bars and `price_flags`.
  Wire models in the module (rule 8).
- `backend/tests/dashboards/test_insider_tracker.py`: a filing with a purchase is a row and a sales-only filing is not;
  the cluster rule (two buyers within 30 days; the same buyer twice is not; 31 days apart is not); the filed-after note;
  the tags carried; the trigger match by accession (one card with, one without); the since span and IWM; a flagged
  name keeps its row; Context and Bench behind the toggle; and, DB-backed, **a filing filed after the as-of is not a
  row** and **a bar printed after the as-of is not read**; the router off one cached read.
- `frontend/src/dashboards/insider-tracker/`: `entry.ts` (slug `insider-tracker`, order 260, `asofAware: true`),
  `InsiderTracker.tsx`, a pure `trackerView.ts` (grouping, filters), `useInsiderTracker.ts`, `insider-tracker.css`,
  tests. The Trigger Log's grouped table is the pattern to follow.
- **Layout:** the tape look; group headers sticky under the table header; on a phone the filer, tags and trigger
  columns fold under the row.

## Known limits (stated on the page too)

- **Open-market purchases only (code P);** option exercises, grants and conversions are not purchases here.
- **The role is the filing's word,** as the reader gives it.
- **Foreign private issuers file no Form 4,** so ADR names are absent, not clean.
- **"Not a trigger" says nothing about why;** the detector's screens are not re-derived.
- **No aggregate on this page** (rule 6).

## Out of scope

Any outcome statistic; any re-derivation of the insider detector's screens (the 13D control screen, the issuer-self
and internal-transfer rules); sales; options; the LLM; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: the 30-day default window; Context and Bench behind a toggle; the
cluster rule at two buyers within 30 days; the trigger match by accession; the PROPOSED size bands; the cluster word
as the one accent.


### Built choices to confirm

The build (2026-10-30) made these choices where the plan did not spell one out, or departed from it. Each is the
operator's to confirm or change. Nothing below is a measurement.

1. **DEPARTURE: the trigger match reads every recorded night in the window, not only the card in effect at the as-of.**
   A flip insider trigger lives 18 days (`insider_flip_alpha_liveness_days`) and the default window is 30, so reading
   only the card in effect would label a buy that DID fire "not a trigger" once its trigger aged off the card. The page
   reads each counted card's winning row per night in the window (one SELECT, the Trigger Log's), matches the
   accession on an insider entry trigger (`triggers_fired` or a member's `triggers`; a risk-list copy never matches),
   and the chip says the first night the record shows it and whether it is still on the card ("off the card" in the row,
   in the hover). "Not a trigger" keeps the plan's hover. To follow the plan literally instead, read the one row in
   effect per card and drop the `live` word.
2. **The reader's words are read back; the reader is unchanged.** `FilingRow` has no structured purchase fields, so the
   filer, role, trade date, shares, price and dollars come from the reader's own headline and detail lines
   (`insider_tracker.purchase_of`), and a round-trip test builds the rows through the real reader so a change to its
   wording fails loud. A Form 4 the reader did not fold as routine is ALWAYS a row (fields blank where they do not
   parse, never dropped). Giving `FilingRow` structured purchase fields would remove the parsing; that is a change to the
   shared reader and was not made here. A name that ends in a parenthetical with no role behind it reads that
   parenthetical as the role (the text cannot tell them apart).
3. **The `$ size` is the purchases' dollars, not the reader's `usd`.** The reader's `usd` is purchases minus sales for
   the whole filing; a filing that holds both would understate what was bought. The page sums the code-P lines (the
   reader's `_code_summary` rule: stated dollars, else shares times price). The two differ only for a filing with a sale.
4. **Cluster details.** Dated by FILING date; row-centered and inclusive (two buyers 30 days apart are a cluster, 31 are
   not; the middle buyer of a chain can be in a larger cluster than its ends). A buyer is the filer the headline names
   (the first reporting owner, casefolded, whitespace collapsed); "and N more" co-filers are shown and not counted as
   further buyers (their names are not on the row); a filing naming no filer joins no cluster and is counted ("filings
   naming no filer"). The read reaches 30 days before the window so a row at the window's edge counts the buyers just
   before it; those lead-in rows are neither listed nor counted as read.
5. **Trade date** = the earliest code-P line's trade date; "filed N days after the trade" only when the filing is later.
6. **Size bands** read the plan's three edges as a partition: under $100K · $100K to $1M · $1M to $10M · $10M and over ·
   size not stated (PROPOSED). They live in `trackerView.ts` (`SIZE_FLOORS`).
7. **Role classes** read the filing's role WORDS: officer (an officer word or a title such as CEO, CFO, President, VP,
   General Counsel), director (director, chair), 10% owner; several can hold; none = other. "Director of Sales" reads as a
   director. A 4/A record row states no role, so it reads "not stated" and class other.
8. **Groupings.** By filing week (newest first); by name (newest filing first, then ticker; header: buyers, dollars
   stated added up with a hover "from N of M purchases that state a size", the cluster word); by card (A to Z; a name on
   two cards is listed under both, and the header says purchases and names). Chips render only where the payload holds two
   or more values, and each restates its count with the other filters applied.
9. **Cap at the as-of, not at the event.** The cap is the latest close at the as-of times the latest shares fact (the Peer
   Table's `cap_cell`); it only filters. The page makes no outcome claim, so rule 6's "measured at the event" does not apply.
10. **Reuse across dashboards.** The compute imports `Series`, `since_move`, `copies` and the night types from
    `dashboards/trigger_log.py`, `CLUSTER_MIN_BUYERS` from `dashboards/leaders.py` and `cap_cell` from
    `dashboards/peer_table.py` (the cross-import precedent: Hot Board, Ticker Lookup and Track Record import the Peer
    Table). Cutting the Trigger Log or Leaders needs those few names moved first.
11. **Added, not in the plan:** a row opens to the reader's headline, detail lines and accession ("show the work"); the card
    chips each open the Cockpit on that card and its ticker; the 90-day window is the only other choice (the wire `days` is
    30 or 90); the work line counts 4/As whose applied rows hold no purchase ("4/As with no purchase row").
12. **"Filer unresolved" in the plan's work line** has no Form 4 tag behind it (that tag belongs to the 13D/G reader), so
    the line counts filings that name no filer instead.
13. **Cost is NOT measured by this build.** The read is `read_filing_rows` over every counted name for the window plus 30
    days, then bars, flags and the cap for the names with a purchase only. The plan's rule stands: measure the 30-day cold
    read on dev with a prod copy and, if it exceeds a minute, narrow the reader to the Form 4 kinds before anything else.
    (The dev measure came back at 67.7 s, so choice 14 narrows it.)
14. **The reader is narrowed to the Form 4 kinds.** `read_filing_rows` and `rows_for_company` take an optional
    `kinds=` (`None`, the default, is every kind and every other page passes nothing, so the Filing Stream, the
    Read-Throughs, the Ticker Lookup, the Chain Map and the Evidence Ledger read exactly as before). This page passes
    `FORM4_KINDS` (`form4`, `form4a`): the 8-K, 13D/G, revenue, split, dilution, catalyst, ratified-fact and
    filing-index reads, and their point-in-time tables, are skipped outright (not read and filtered), and the Form 4/A
    SELECT runs only because `form4a` is asked for. The Form 4 / 4/A rows and their two-clock gate are untouched, and a
    test pins that the narrowed rows equal the full read's Form 4 and 4/A rows and that the page is identical either
    way. The page never shows another kind, so no row, count or note moves. A kind name the reader does not have
    raises, so a typo cannot read as "no rows". The new post-narrowing cold time is to be measured on dev with a prod
    copy and recorded in the Cache section.
