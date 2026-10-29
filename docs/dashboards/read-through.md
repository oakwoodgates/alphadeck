# Read-Throughs

> Slug `read-through` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 130`.
> The operator approved this dashboard and the choices below (2026-10-29); the choices the build makes that this
> plan does not spell out go under "Choices to confirm" for the operator's review. The rules every dashboard
> follows: `docs/DASHBOARDS.md`. The second page of the RESEARCH set; it reads the Filing Stream's shared reader
> (`backend/dashboards/filings.py`, `docs/dashboards/filing-stream.md`), so it is built after it.

**Label:** Read-Throughs. **Description:** "Every role-tagged company across your cards (payers, counter-cases,
direct reads, participants, financiers, benchmarks) with what it last filed, its next dated catalyst, and its tape
against SPY."

It answers "what did the payers, the rivals and the direct reads just say": one table, across every card counted at
the as-of, of every company the operator tagged with a context role, sorted by what it filed most recently. It is the
cross-card lens on the roles backfilled in Phase 1 (`docs/TRIAGE.md`), and the row's drill-in is where the Phase 2
read-through panel lands later. Display only: it never feeds a call, never writes to the database and never calls the
LLM. A role is display metadata (#4) and is never written by the model (#3); this page only shows it.

## The rows

**One row per (company, role).** A company tagged `payer` on two cards is ONE row with two card chips; a company tagged
`payer` on one card and `counter_case` on another is TWO rows (the promote keeps a role identical across a company's
rows ON ONE CARD, not across cards). Companies are matched by `security_id`; a basket row with no resolved security
cannot carry a role here and is counted in the work line.

The roster comes through the shared basket read, exactly as the other as-of dashboards: `baskets.cards_as_of(conn,
asof, known_at)` (the live cards that existed by the as-of, each with its roster then; today's roster stands in before
a card's first snapshot, said in a quiet note), then `memberships_of` and each row's `context_role`. Every row with a
role counts, **Context and Bench included** (that is where most roles live); a role on a counting row is shown too, with
the row's link.

## The columns

| Column | What it shows | Source |
|---|---|---|
| Company | ticker (a chip opening the Cockpit name panel on its first card, `onSelectThesis(thesisId, ticker)`) and the master's name | `master.names_for` / `tickers_for` |
| Role | the role as a quiet lowercase tag with its operator-approved hover description | `CONTEXT_ROLE_LABEL`, `CONTEXT_ROLE_DESCRIPTION` (`workbench/format.ts`) |
| Cards | one single-line pill per card: the card's name (cut with an ellipsis when long), then the link the company sits in after it in the quiet style (Context / Bench by label when silent); the pill's hover shows the full name and links; the pill opens that card; the pills wrap as a row of pills and the column keeps a minimum width | the memberships |
| Business type | the leaf business type derived on read from the master (the operator's re-tag wins), blank when unknown | `security_master.business_type` / `securities.business_type.resolve_business_type` |
| Last filing | the newest SEC filing (an 8-K, 13D/G, Form 4, 4/A or a 10-Q / 10-K revenue print: `filings.SEC_FILING_KINDS`) in the last 365 days before the as-of: its date · form · headline, hover the full line, click the EDGAR link; "none stored in the last year" when none | `filings.read_filing_rows(..., days=365)` |
| Next dated catalyst | the nearest card-level catalyst with `when_date >= asof` across the row's cards, with its card and days to go; "—" when none | the cards' `Catalyst.when_date` (today's list; see limits) |
| 1W vs SPY · 1M vs SPY | the company's 1-week and 1-month trailing return minus SPY's, in percentage points; blank when unpriced; grayed with the reason when its price history is flagged | `display_members(["trailing_returns"])` through `per_company`, SPY via `pit.benchmark_prices` (the Leaders pattern); `baskets.price_flags` |

Loudness: no badges, no color by role. The two tape columns use the Rotation Quadrant's muted diverging scale at
most; a plain signed number is acceptable. Neutral wording everywhere (rule 3): the page says what was filed and
when, never what it means.

## Tabs, sort, filters (client side)

- **Tabs:** All roles · one tab per role in the Chain Wave's order (payer, counter_case, direct_read, participant,
  financier, benchmark), each tab labeled as a group ("Payers", "Counter-cases", "Direct reads", "Participants",
  "Financiers", "Benchmarks") with its count. A role with no rows shows its tab grayed with 0, never hidden.
- **Default sort:** last filing date, newest first; rows with no filing last, A to Z by ticker. Every column sorts.
- **Filters:** card (a type-ahead), business type, "only rows with a filing in the last N days" (30 / 90 / 365).
  Component state only, nothing in the URL or browser storage.

## The quiet "show the work" line

The as-of and the clock; live cards counted / not yet created; cards with at least one role / cards with none (the
"none" cards listed on expand: the page is also the to-do list for roles); roles set / companies / rows; rows by role;
rows with a filing in the last year; flagged names (the shared `FlaggedNote`); unresolved basket rows; the cache
state; the roster note when today's roster stands in for any card.

## Cache

`dashboards.cache.cached(conn, "read-through", (clock,), asof, ...)` holds the whole table for the as-of: the rows, their
last filing, their catalysts, their tape and flags. Tabs, sort and filters are client side. The shared cache clears on
a new run or a basket edit (rule 7). This is the heaviest research read (every role-tagged company's year of facts plus
its bars); the first load may take seconds, like the Leaders. The page shows `compute_ms` in the work line.

## Backend and frontend

- `backend/dashboards/read_through.py`, pure: the (company, role) rows from the memberships, the card chips, the
  last-filing pick, the next-catalyst pick, the role order, the counts.
- `backend/app/routers/dashboards/read_through.py`: one `GET /dashboards/read-through?asof=` returning the table with
  its counts; `known_at, clock = serve_view(asof)` threads into every read (`cards_as_of`, `read_filing_rows`,
  `per_company`, `price_flags`). Wire models in this module (rule 8).
- `backend/tests/dashboards/test_read_through.py`: the row rule (one row per company per role; two roles on two cards
  = two rows; the same role on two cards = one row with two chips; a counting row with a role; a silent row; an
  unresolved row counted, never a row), the last-filing pick (newest by date, ties by kind order), the next-catalyst
  pick (nearest `when_date >= asof`, a dated catalyst in the past ignored, undated ignored), the role order, the
  counts; the router off one cached read; and, DB-backed, **a role set after the as-of does not show**: upsert a card
  with no role, then upsert it again with a role (a later roster snapshot); the view at a `known_at` between the two
  shows no row for it, the live view shows one (the roster's point-in-time read, `thesis_repo.get_asof`).
- `frontend/src/dashboards/read-through/`: `entry.ts` (slug `read-through`, order 130, `asofAware: true`),
  `ReadThrough.tsx` (tabs, the sortable table, the filters, the work line; no chart library), `useReadThrough.ts`
  (the one read, in this folder), `tableView.ts` (sort, filters, counts), `read-through.css`, tests. The Shared Names
  page is the closest table pattern to follow.
- **Layout:** the research set's document-like look (the Filing Stream's): a wide table that scrolls sideways on a
  phone with the Company column pinned; one line per row; tabular numbers.

## Known limits (stated on the page too)

- **A role names a part, not a target.** The row points at the card, never at specific names. Letting a role name the
  link it reads through to is a later spine change (deferred to Phase 2).
- **Catalysts are today's list:** card-level catalysts load live (they are not snapshotted), so a past as-of shows the
  catalysts as entered today.
- **Last filing = ingested filings only**, with the Filing Stream's limits (no full filing index yet; the Cockpit's
  insider price screens not applied). A split, a catalyst or a ratified or stored fact is never counted as a filing.
- **The cold read is heavy** (MEASURED 2026-10-29 on dev with a prod data copy: 18.2 s for the live as-of). It is
  dominated by the 365-day filing read over every role-tagged company, which builds one point-in-time view per company
  (214 of them on that read). It runs once per as-of per run (the shared cache then serves it in ~40 ms); a shared
  per-company filing cache is the lever if it matters.
- **The roster is each card's roster as of the as-of;** links and business types are today's.
- A flagged price history grays the tape cells and names the reason; the row stays.

## Out of scope

The read-through panel (Phase 2: a role-tagged name's own disclosures read against the voting names); role targets;
any score or ranking beyond sorting by what the data shows; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
The first three are plan details that turned out different against the code; the build fixed them sensibly and says how.
The page was built against fixtures and a two-company DB test; the measurements on real data are choice 18.

1. **The tape windows are 7 and 30 trading days, labeled "7D vs SPY" and "30D vs SPY" (plan detail corrected).** The
   plan asks for a 1-week and 1-month return from the `trailing_returns` display member, but that member's windows are
   1, 7, 30, 90 and 252 trading BARS; it has no week or month. The build uses its `ret_7d` and `ret_30d`, the same
   numbers the Cockpit's 7d / 30d columns and the Leaders show, so every figure reconciles with them, and labels them
   the way those pages do. The hover says "trading days" and shows the working. SPY's side is the member's own pure rule
   (`trailing_returns.compute`) over SPY's bars from `pit.benchmark_prices`, so both sides use one definition. If you
   want true calendar weeks and months, it is a different measure (the Chain Wave's weekly one) and a small change.
2. **Business type and name come from `master.identity_for`, not `names_for` plus `resolve_business_type` (plan detail
   corrected).** `identity_for` is one SELECT that already runs `resolve_business_type` over the stored sector (the
   operator's re-tag wins) and returns the master's name, so the values are the ones the plan names with no second read.
3. **The cache holds the finished table, not the raw reads (plan detail corrected).** The other research pages cache
   inputs and slice per request. This page has no request parameter but the as-of, so the cached value is the table
   itself, keyed by clock and as-of, as the plan's "holds the whole table" says.
4. **Last filing is an SEC filing only (revised 2026-10-29 after dev with a prod data copy).** The first build took the
   newest `FilingRow` of any kind, as the plan's wording read. On dev that column was dominated by rows that are not
   filings: the machine-applied and feed-stored facts (856 `auto` shares rows), whose store date was an ingest batch, and
   splits and catalysts. The pick now considers only the kinds `8k`, `form4`, `form4a`, `stake` and `revenue`
   (`filings.SEC_FILING_KINDS`, shared with the Chain Map); a split, a dilution fact, a catalyst and a ratified or stored
   fact are never picked, whatever their date. A company with only those rows reads "none stored in the last year". The
   column and the glyph are still called "last filing". Ties on the date still go by the reader's kind order (8-K,
   13D/G, Form 4, 4/A, revenue).
5. **A tie on the date goes by the reader's kind order, then accession, form and headline.** So an 8-K beats a Form 4
   filed the same day; the pick never depends on the order the rows arrive in.
6. **A vs-SPY cell is blank when the company's last close is not SPY's last close.** Not in the plan. The two returns
   would otherwise end on different days (a halted or late-pulled tape against SPY's as-of bar). The cell's hover says
   both dates and the company's own return, and the work line counts the rows (`ends_differ`). A short window is blank
   on that window alone.
7. **A flagged price history shows the word "flagged" in both tape cells and no number at all.** The plan says "grayed
   with the reason". The reason is in the cell's hover and in the shared "left out for a flagged price history" note
   under the table; the returns are not sent to the page, so a chained bad bar is never on screen.
8. **One row per (company, role); the chip's links are the links of the rows that carry that role.** A company that holds
   two different roles on one card (the promote refuses it, a stored roster could still carry it) is two rows, each with
   that card's chip. A company with no link on a card shows "No link" in its chip.
9. **The company's ticker is the one most of its cards use** (`baskets.display_ticker`, ties by ticker). The ticker chip
   opens the Cockpit name panel on the company's first card A to Z by name, with that card's own ticker as the `?name=`
   key; a card chip opens the card.
10. **Role rows with no resolved security are counted and listed, and only role rows.** A basket row with no security and
    no role has nothing to do with this page, so it is not in the work line. "Roles set" counts one per (card, company);
    "companies" counts distinct resolved role companies.
11. **Only the companies that carry a role are read.** Filings, tape, flags and identity are read for them alone, not for
    every roster company, so the cold read scales with the roles the operator has set.
12. **The tab counts follow the filters** (a tab's count is the rows it would show), and a role with no rows stays a
    grayed tab with 0, also when a filter is what emptied it. An empty tab says so in a line.
13. **The filters are a card-name substring (a type-ahead over the card names in the table), a business-type select
    (with "Unclassified" for the rows with none) and "Filed in the last" Any / 30 / 90 / 365 days.** A row with no
    filing never passes a 30 / 90 / 365 window. A control that cannot tell rows apart (one card, one business type, no
    filing in the table) does not render; "clear filters" shows only while one is on.
14. **Sorting.** The page opens in the server's order (newest last filing first, none last A to Z). A click starts a
    column in its natural direction (text A to Z, filing newest first, catalyst nearest first, tape largest first) and a
    second click flips it. A blank value is last in either direction. "Cards" sorts by the first card's name. Ties break
    by ticker, then the role order.
15. **The as-of day counts for a catalyst** (`when_date >= asof`, as the plan says), worded "on the as-of date" at 0 days.
    A tie on the date goes by card name, then the catalyst's label.
16. **Cell words.** The last-filing cell reads "Oct 20 · 4 · <headline>" (the form is not said twice when the headline
    already opens with it, as an 8-K's does) with the year in the hover; the catalyst cell
    reads "Nov 12 · in 15 days · Q3 earnings · Grid". The EDGAR link is "EDGAR" for a sec.gov URL and "source" for any
    other stored source (the Filing Stream's wording).
17. **Adjacent fix: the 8-K headline now labels every standard item.** `signals.corporate_events.item_label` knows seven
    items, so the Filing Stream and this page showed 9.01, 2.02, 7.01 and 8.01 as bare codes. `filings.py` now carries a
    display-only table of the standard Form 8-K items (1.01 to 9.01), SEC's short titles in neutral lowercase words;
    `item_label`'s own wording still wins for the seven it knows, and a code in neither (an item the SEC adds later) still
    shows as the bare code. This supersedes choice 1 of the Filing Stream's list and changes its headline for 9.01
    ("9.01 financial statements and exhibits"). `signals/` is untouched.
18. **Measured on dev with a prod data copy (2026-10-29, 62 live cards).** At the live as-of 2026-10-29 the page serves
    249 rows for 214 companies (334 role tags, set on 58 of the 62 cards), a 281 KB payload; the cold compute took
    18.2 s and a cache hit 40 ms. At as-of 2026-09-15 on the public clock it serves 0 rows (10 cards existed then and
    none carried a role yet) in 245 ms. The cold cost is the 365-day filing read over every role-tagged company, one
    point-in-time view per company (see the known limits); it runs once per as-of per run.
19. **The Cards column is a row of single-line pills (found on dev with a prod copy).** A chip read "A Long Card Name With Several Words · Context" broke over four to six lines in the 280 px cell. Each pill is now one line: the card's name
    is cut with an ellipsis at a maximum width, the links follow it in the quiet style (cut on their own width), and the
    pill's hover title carries the whole "Open <card> · <links>" line. The pills wrap as pills inside the cell, the column
    has a minimum width (240 px; 200 px on a phone), and the table scrolls sideways with the Company column pinned, as
    before. The pill's text content is unchanged ("Grid · Turbines, Cables"), so a screen reader hears the same line.
