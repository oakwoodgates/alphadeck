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
| Cards | one chip per card: the card's name · the link the company sits in (Context / Bench by label when silent); the chip opens that card | the memberships |
| Business type | the leaf business type derived on read from the master (the operator's re-tag wins), blank when unknown | `security_master.business_type` / `securities.business_type.resolve_business_type` |
| Last filing | the newest `FilingRow` in the last 365 days before the as-of: its date · form · headline, hover the full line, click the EDGAR link; "none stored in the last year" when none | `filings.read_filing_rows(..., days=365)` |
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
- **Last filing = ingested facts only**, with the Filing Stream's limits (no full filing index yet; the Cockpit's
  insider price screens not applied).
- **The roster is each card's roster as of the as-of;** links and business types are today's.
- A flagged price history grays the tape cells and names the reason; the row stays.

## Out of scope

The read-through panel (Phase 2: a role-tagged name's own disclosures read against the voting names); role targets;
any score or ranking beyond sorting by what the data shows; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
