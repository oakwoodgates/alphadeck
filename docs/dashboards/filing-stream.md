# Filing Stream

> Slug `filing-stream` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 120`.
> The operator approved this dashboard and the choices below (2026-10-29); the choices the build makes that this
> plan does not spell out go under "Choices to confirm" for the operator's review. The rules every dashboard
> follows: `docs/DASHBOARDS.md`. The first page of the RESEARCH set (Filing Stream, Read-Throughs, Chain Map,
> Peer Table, Fundamentals Multiples, Narrative Adoption Curve, Evidence Ledger).

**Label:** Filing Stream. **Description:** "Every filing-derived fact the system holds for one card's names, newest
first, as it was readable that night: 8-Ks with their items, Form 4s with the filer's words, 13D/G stakes, revenue
prints, splits, dilution, ratified catalysts."

It shows ONE card at a time: a dated stream, newest day first, of every fact the ingest has stored for the card's
names, each row opening to its EDGAR source. It is the analyst's reading queue: what did my names just file, and
what did the system make of it. Display only: it never feeds a call, never writes to the database and never calls
the LLM. At a past as-of it shows what was readable by the end of that night, on the public clock.

**It shows ingested facts, not every filing.** The system stores 8-Ks (with item codes), Form 4s and 4/As, 13D/G
stakes, the revenue series from 10-Qs and 10-Ks, splits, convertible-note dilution, and the operator's ratified
facts. Other forms (S-1, 424B, DEF 14A, 10-Q text beyond revenue, …) are not stored yet; the page says so in its
work line. A later change stores the full filing index per basket company from the submissions index the nightly
already fetches (its own PR, a pipeline change, not dashboard code); the stream picks it up as one more row kind.

## The rows

Every row is one `FilingRow` from the SHARED reader `backend/dashboards/filings.py` (built with this dashboard, shared
since the Read-Throughs table): the per-company point-in-time fact lists, normalized to one shape so every page that
shows "what did this company file" reads the same rows.

```
FilingRow(security_id, ticker, kind, form, date, headline, detail, url, accession, tags, routine, usd)
  kind      8k | form4 | form4a | stake | revenue | split | dilution | catalyst | ratified
  form      the form as filed ("8-K", "8-K/A", "4", "4/A", "SC 13D", "SC 13G/A", "10-Q", "10-K", ...) or the row's
            own label ("split", "catalyst", "ratified fact")
  date      the PUBLIC date the row counts from (its filing / acceptance date; a split's own publication; a
            ratified catalyst's event date): the day it sorts under
  headline  one neutral line (what the numbers show, never a verdict)
  detail    the lines the expanded row shows (the filer's own words, the items' labels, the terms)
  url       the EDGAR filing-index URL (the row's source_ref where stored; else built from the company's CIK and
            the accession: https://www.sec.gov/Archives/edgar/data/<cik as int>/<accession without dashes>/)
  tags      the quiet tags ("issuer self-filing", "internal transfer", "10b5-1 plan", "items not yet resolved",
            "re-reported", "needs review", "filer identity unresolved")
  routine   a fold candidate (below): true for a Form 4 whose rows hold no code-P purchase
  usd       the row's dollar size when it has one (a Form 4's net, a dilution's principal), for the compact hint
```

The sources, each through the SAME point-in-time view the Cockpit builds (`baskets.per_company` over `PointInTimeData`,
with the request's `known_at, clock = serve_view(asof)`; invariant #1):

1. **8-K**: `pit.corporate_event_facts`, one row per filing (`form`, `items`, `filed`, `accession`, `source_ref`).
   Headline: the items with their labels from `signals.corporate_events.item_label` ("5.02 officer/director departure
   or appointment · 9.01"); items `NULL` = tag "items not yet resolved". An `8-K/A` says so in `form`.
2. **Form 4**: `pit.insider_txns`, the stored transaction rows GROUPED BY ACCESSION into one row per filing. Headline:
   the filer's name and role, then the transactions summed by code ("P 12,000 sh @ $4.10 · $49,200"; codes labeled
   P purchase · S sale · A grant/award · M option exercise · F tax withholding · G gift · others by their code).
   Detail: each transaction line (date, code, shares, price, USD, direct/indirect, shares owned after), then the
   filer's own words where captured: `ownership_nature`, `txn_footnotes`, `filing_natures` (NULL = not captured, never
   shown as "none"; an empty list = "none stated"). Tags, by PURE field predicates only (#3; the row is never mutated):
   - **issuer self-filing**: the issuer filing on itself. `rpt_owner_cik` equals `issuer_cik` (leading zeros
     ignored), else the normalized filer name equals the issuer's name. The same rule `signals/insider_conviction.py`
     and `signals/display/insider_flow.py` each hold privately (the seam's duplicate-with-pointer pattern); the reader
     holds its own copy with a comment pointing at both, and does NOT import or edit `signals/` (a `signals/` edit
     puts the whole slow suite on every iteration for a one-line alias).
   - **internal transfer**: `domain.insider_internal_transfers.is_internal_transfer(row)` (public).
   - **10b5-1 plan**: only on an explicit `aff_10b5_1 is True`; `None` and `False` assert nothing.
   - NOT applied here (a known limit, stated on the page): the day-low price screen and the implausible-data screen
     the Cockpit's insider column applies (they need the bars and the split scale). The stream never writes
     "open market": a code-P row with no tag is shown as a purchase, nothing more.
3. **Form 4/A**: the `insider_amendment` rows for the company (a plain SELECT in the router module, beside the other
   reads): `filed`, `accepted`, `owners`, `remarks`, `rows` (count), `date_of_original_submission`. Headline: "4/A by
   <owner> amends the Form 4 filed <date>" (or "amends an earlier Form 4" when the date is NULL). Detail: the remarks.
   Shown AS A FILING, never resolved against its original here: its standing (applied, kept, pending) lives on the
   Admin "Insider amendments" page, which the row links to. **The two-clock gate:** on both clocks `filed <= asof`
   (a 4/A counts from its own filing, invariant #1); on the record clock also `recorded_at <= known_at`; the public
   clock never reads `recorded_at` (operator decision 2026-10-21). The DB test below covers a 4/A filed after the
   as-of.
4. **13D / 13G**: `pit.activist_stake_facts` (`form`, `filer_name` with NULL = tag "filer identity unresolved",
   `pct_owned`, `filed`, `accession`, `source_ref`). Headline: "SC 13D · <filer> · 7.2%".
5. **Revenue print**: `pit.fundamentals_facts` with `metric_key = 'revenue'`, GROUPED BY ACCESSION (one filing may
   carry several periods). `form` = 10-Q or 10-K by `fiscal_period` (Q4 / FY = 10-K; the builder states the rule);
   date = `valid_from` (the filing date). Headline: "revenue, period ending <period_end>: $X (basis native|derived)",
   and the YoY against the row for the same `period_end` one year earlier WHEN that row is in the same as-of read
   ("+34% YoY"), else no YoY. A row whose `knowable_from` is earlier than its `valid_from` is a re-report: tag
   "re-reported", with the first-knowable date in the detail.
6. **Split**: `pit.split_events`, the split's ratio, effective date and status (`confirmed` / `needs_review` /
   other, as stored), dated by the split's own publication as the view knows it. Tag "needs review" when the status
   says so. Detail: the sources that agreed (as stored).
7. **Dilution**: `pit.dilution_facts`, `instrument_kind`, `principal_total_usd`, and the overhang as a percent of
   `shares_outstanding` when both are present (the stored seeded basis; the builder states the formula), dated
   `valid_from`, linked by `accession`.
8. **Ratified catalyst**: `pit.catalyst_facts` (`catalyst_type`, `grade`, `label`, `source`, `source_ref`,
   `ratified_by`), dated `valid_from` (its event date, so a future-dated catalyst appears once its day passes; the
   card-level dated and undated catalysts are the Catalyst Calendar's and are NOT in the stream).
9. **Ratified fact**: `pit.cash_burn_facts`, `pit.shares_outstanding_facts`, `pit.revenue_mix_facts`, one row each,
   headline = the fact and its value ("cash $41.2M · burn $8.1M/qtr", "shares outstanding 84.2M", "revenue mix:
   nuclear 61%"), with `source_ref` and `ratified_by`. These are the operator's own ratifications, so they are few.

Unresolved basket rows (no `security_id`) can carry no facts: they are counted in the work line and listed in the
roster note, never in the stream (invariant #9: counted, never silently dropped).

## Which names count

**Every roster row at the as-of, Context and Bench included.** Research is where the silent names earn their place,
so they are in the stream by default, each row carrying the name's link and, when set, its context role as a quiet
lowercase tag (`contextRoleTag`) or its silent label (Context / Bench). A **"Counting names only"** toggle (client
side) leaves the silent rows out. The roster comes through the shared basket read: the cards that existed by the
as-of (`baskets.cards_that_existed`), the card's roster then (`thesis_repo.get_asof`; today's roster stands in
before its first snapshot, said in a quiet note), `memberships_of` (with each row's `context_role` and `silent`).

## The window, the order, the fold

- **Window: the last 30 · 90 (default) · 365 days** before the as-of, inclusive. The server slices the window from ONE
  cached 365-day read per (card, clock, as-of). No "all": a year is the research horizon here.
- **Order:** by `date` descending, grouped under a day header; within a day by kind (8-K, 13D/G, Form 4, 4/A,
  revenue, split, dilution, catalyst, ratified fact), then by ticker.
- **The fold (honest loudness, Workbench principle 3):** on any card, Form 4 sales, grants, exercises and tax
  withholdings outnumber everything else. A day's `routine` Form 4 rows collapse into ONE quiet line, "N routine Form 4
  filings (sales, grants, exercises, withholding)", that expands in place; a Form 4 holding a code-P purchase is
  never routine. The fold is on by default and is a client-side toggle ("Show routine Form 4s"). Nothing is hidden
  from the counts: the work line counts every row, folded or not.
- **Loudness:** no badges, no colors by kind. Each row is one line: date · ticker (a chip opening the Cockpit name panel
  via `onSelectThesis(thesisId, ticker)`) · form · headline · tags · the EDGAR link. The expanded row shows `detail`.

## Filters (client side, over the window's rows)

Kind (one chip per kind, all on by default), link (the card's links in chain order, plus Context, Bench, No link),
name (a type-ahead over the roster), role (the roles set on the card), "Counting names only", "Show routine Form 4s".
The URL carries none of these; they are component state (no browser storage).

## Picking the card

The same picker the Chain Wave has: a search box over a select of the cards counted at the as-of, A to Z, each with its
link count today; the first by default; the card's name calls `onSelectThesis(thesisId)`; a picked card stays picked
across an as-of change while it is still counted. **The picker read is lifted into the shared basket module:**
`baskets.picker_cards(conn, asof)` (the Chain Wave's `read_cards` + `card_choices` + `CardChoice`, moved; the Chain
Wave re-exports them under their old names and uses the shared one), so the next one-card dashboard reads it too.

## The quiet "show the work" line

The card and the as-of; the window's dates; the clock ("live" or "as it was public by <date>"); roster rows / resolved /
unresolved / silent; rows in the window by kind (8-Ks, Form 4s with how many hold a purchase, 4/As, stakes, revenue
prints, splits, dilution, catalysts, ratified facts); folded routine rows; the cache state. Below it, the honest notes:
"ingested facts only: forms other than 8-K, Form 4 and 4/A, 13D/G and the 10-Q/10-K revenue series are not stored
yet"; "the Cockpit's day-low and implausible-data insider screens are not applied here"; "4/As are shown as filings,
their standing is on Admin › Insider amendments"; the roster note when today's roster stands in ("rosters are
point-in-time only from Sep 15, 2026").

## Cache

`dashboards.cache.cached(conn, "filing-stream", (str(thesis_id), clock), asof, ...)` holds the 365-day `FilingRow`s for
the card (Context and Bench included); the window and every filter slice per request or on the client. The picker list
is cached as `("cards", clock)`. The shared cache clears on a new run or a basket edit (rule 7).

## Backend and frontend

- `backend/dashboards/filings.py`, SHARED. Pure normalizers per source (`rows_from_corporate_events(...)`,
  `rows_from_insider_txns(...)`, and so on, each over the plain row dicts the PIT returns, hand-testable), the Form 4
  code labels, the issuer-self and internal-transfer tags, the EDGAR URL builder (`filing_index_url(cik, accession)`),
  the kind order, and `read_filing_rows(conn, sids, asof, known_at, clock, *, days)` (the read, through `per_company`
  plus the 4/A SELECT, returning `{security_id: tuple[FilingRow, ...]}`).
- `backend/dashboards/filing_stream.py`, pure. The window slice, the day grouping, the fold, the counts, the work line's
  numbers.
- `backend/app/routers/dashboards/filing_stream.py`: `GET /dashboards/filing-stream/cards?asof=` (the picker) and
  `GET /dashboards/filing-stream/{thesis_id}?asof=&days=30|90|365` (the window's rows, each with its link, role and
  silent flag, plus the roster counts and the kind counts; 404 for a card not counted at the as-of). `known_at, clock
  = serve_view(asof)` threads into every read. Wire models in this module (rule 8).
- `backend/tests/dashboards/test_filing_stream.py`: each normalizer on hand-built rows (every field, the grouping by
  accession, the YoY rule, the tags, NULL vs empty filer text), the URL builder, the fold, the window, the day order;
  the router off one cached read with its 404; and, DB-backed (the Chain Wave test's pattern: `thesis_repo.upsert` +
  `append_fact` + `pinned_today`), **a fact published after a past as-of never reaching the stream**: an 8-K with a
  later `filed`, a Form 4 row with a later `valid_from`, and a 4/A with a later `filed`, each invisible at the earlier
  as-of and visible at the later one. The isolation and discovery tests already cover the new files.
- `frontend/src/dashboards/filing-stream/`: `entry.ts` (slug `filing-stream`, order 120, `asofAware: true`),
  `FilingStream.tsx` (the picker, the window control, the filters, the day-grouped list; no chart library), its hooks
  (`useFilingStream.ts`: the two reads over `api/client.ts`, in this folder, not `api/hooks.ts`), its view helpers
  (`streamView.ts`: filters, the fold, the counts shown), `filing-stream.css` and its tests. Role tags via
  `CONTEXT_ROLE_LABEL` / `contextRoleTag` from `workbench/format.ts`.
- **Layout (the research set's own look, allowed to depart from the Board's card chrome):** a document-like page: a
  single column, full-width day headers, one line per row, tabular numbers, the expanded row as an indented block;
  colors from the app's tokens so both themes hold; readable on a phone (the headline wraps, the chips stay).

## Known limits (stated on the page too)

- Ingested facts only (above). The full filing index is a later pipeline change.
- The Cockpit's day-low and implausible-data insider screens are not applied; the stream never says "open market".
- A 4/A is shown as a filing, not resolved against its original.
- A split is dated by its own publication as the view knows it; its status is as stored.
- The card-level catalysts (dated or undated) are the Catalyst Calendar's, not the stream's.
- The roster is the card's roster as of the as-of (hindsight selection); links are today's.
- No located passages: the row links to the filing, it does not quote it.

## Out of scope

The full filing index (its own PR); located passages; any LLM summary; a cross-card stream (the Read-Throughs table
is the cross-card lens); resolving amendments; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out (the Chain Wave's plan shows the form),
each the operator's to confirm or change.
