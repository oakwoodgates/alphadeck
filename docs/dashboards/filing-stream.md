# Filing Stream

> Slug `filing-stream` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 120`.
> The operator approved this dashboard and the choices below (2026-10-29); the choices the build makes that this
> plan does not spell out go under "Choices to confirm" for the operator's review. The rules every dashboard
> follows: `docs/DASHBOARDS.md`. The first page of the RESEARCH set (Filing Stream, Read-Throughs, Chain Map,
> Peer Table, Fundamentals Multiples, Narrative Adoption Curve, Evidence Ledger).

**Label:** Filing Stream. **Description:** "Every filing-derived fact the system holds for one card's names, newest
first, as it was readable that night: 8-Ks with their items, Form 4s with the filer's words, 13D/G stakes, revenue
prints, every other form in the EDGAR index, splits, dilution, catalysts, ratified and stored facts."

It shows ONE card at a time: a dated stream, newest day first, of every fact the ingest has stored for the card's
names, each row opening to its EDGAR source. It is the analyst's reading queue: what did my names just file, and
what did the system make of it. Display only: it never feeds a call, never writes to the database and never calls
the LLM. At a past as-of it shows what was readable by the end of that night, on the public clock.

**It shows every form in each company's EDGAR index, plus what the system read from it.** The system reads INTO
8-Ks (item codes), Form 4s and 4/As, 13D/G stakes and the revenue series from 10-Qs and 10-Ks, and holds splits,
convertible-note dilution, catalysts and the ratified or stored facts. Since 2026-10-29 it also stores each basket
company's whole EDGAR filing index (`fact_filing_index`, migration 0070: every form `filings.recent` lists, written by
the nightly's filing-index leg off the submissions document it already reads, zero extra requests,
`docs/FEED_LOOP.md`). Every other form (an S-1, a 424B5, a DEF 14A, a 10-Q beyond its revenue print, ...) is one
`filing` row ("Other forms"): its form and primary document, nothing read from inside it. The index is EDGAR's
`recent` window: at least a year, or the last 1,000 filings, whichever reaches further; older filings are not stored.
Each filing shows once, as its richest row: an index row whose accession another row carries is dropped.

## The rows

Every row is one `FilingRow` from the SHARED reader `backend/dashboards/filings.py` (built with this dashboard, shared
since the Read-Throughs table): the per-company point-in-time fact lists, normalized to one shape so every page that
shows "what did this company file" reads the same rows.

```
FilingRow(security_id, ticker, kind, form, date, headline, detail, url, accession, tags, routine, usd, fold)
  kind      8k | form4 | form4a | stake | revenue | filing | split | dilution | catalyst | ratified
  form      the form as filed ("8-K", "8-K/A", "4", "4/A", "SC 13D", "SC 13G/A", "10-Q", "10-K", ...) or the row's
            own label ("split", "catalyst", "ratified fact", "stored fact")
  date      the PUBLIC date the row counts from (its filing / acceptance date; a split's own publication; a
            catalyst's event date; a stored fact's cover or filing date; a ratified fact's ratification): the day
            it sorts under
  headline  one neutral line (what the numbers show, never a verdict)
  detail    the lines the expanded row shows (the filer's own words, the items' labels, the terms)
  url       the EDGAR filing-index URL (the row's source_ref where stored; else built from the company's CIK and
            the accession: https://www.sec.gov/Archives/edgar/data/<cik as int>/<accession without dashes>/)
  tags      the quiet tags ("issuer self-filing", "internal transfer", "10b5-1 plan", "items not yet resolved",
            "re-reported", "needs review", "filer identity unresolved")
  routine   a fold candidate (below): a Form 4 whose rows hold no code-P purchase, a prospectus supplement, an
            insider ownership filing with no transaction rows stored
  usd       the row's dollar size when it has one (a Form 4's net, a dilution's principal), for the compact hint
  fold      the routine row's fold group: form4 | prospectus | ownership (null for a row that is not routine)
```

The sources, each through the SAME point-in-time view the Cockpit builds (`baskets.per_company` over `PointInTimeData`,
with the request's `known_at, clock = serve_view(asof)`; invariant #1):

1. **8-K**: `pit.corporate_event_facts`, one row per filing (`form`, `items`, `filed`, `accession`, `source_ref`).
   Headline: the items with their labels, `signals.corporate_events.item_label`'s own wording first and then the
   standard-item table in `filings.py` ("5.02 officer/director departure or appointment · 9.01 financial statements
   and exhibits"); items `NULL` = tag "items not yet resolved". An `8-K/A` says so in `form`.
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
8. **Catalyst**: `pit.catalyst_facts` (`catalyst_type`, `grade`, `label`, `source`, `source_ref`,
   `ratified_by`), dated `valid_from` (its event date, so a future-dated catalyst appears once its day passes; the
   card-level dated and undated catalysts are the Catalyst Calendar's and are NOT in the stream). The detail names
   the ratifier when the operator ratified it and says so when nobody did (an automated feed such as
   `doe_usaspending` stores it with no `ratified_by`): see choice 16.
9. **Ratified or stored fact**: `pit.cash_burn_facts`, `pit.shares_outstanding_facts`, `pit.revenue_mix_facts`, one
   row each, headline = the fact and its value ("cash $41.2M · burn $8.1M/qtr", "shares outstanding 84.2M", "revenue
   mix: nuclear 61%"), with `source_ref` and `ratified_by`. Whether it is a "ratified fact" or a "stored fact" turns on
   `ratified_by`: choice 16.
10. **Other forms** (kind `filing`, 2026-10-29): `pit.filing_index`, the company's EDGAR index (`form`, `accession`,
   `filed`, `primary_doc`, `source_ref`), dated `filed`. Headline: "<form> · <primary document>" (just the form when
   EDGAR left the document blank); no detail, no tags; the link is the stored EDGAR index page. Prospectus paper and
   index-only ownership forms are ROUTINE in their own fold group (the fold, below); every other form is loud. Built
   AFTER every other kind and only for an accession no other row carries (an 8-K, a Form 4 or 4/A, a 13D/G, a revenue
   print, a split, a dilution or a catalyst; accessions compared as stored, whitespace removed), so each filing shows
   once. A display accessor, off the detectors' protocol (the `fund_shares` precedent), with both clocks from the
   point-in-time view: the live read gates on `recorded_at <= known_at`, a history view on the filing date. Within a
   day it sorts right after the revenue print; its filter chip reads "Other forms".

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
  revenue, other forms, split, dilution, catalyst, ratified or stored fact), then by ticker.
- **The fold (honest loudness, Workbench principle 3):** on any card, Form 4 sales, grants, exercises and tax
  withholdings outnumber everything else. A day's `routine` Form 4 rows collapse into ONE quiet line, "N routine Form 4
  filings (sales, grants, exercises, withholding)", that expands in place; a Form 4 holding a code-P purchase is
  never routine. The fold is on by default and is a client-side toggle ("Show routine Form 4s"; "Show routine rows"
  once another group is on the page). Nothing is hidden from the counts: the work line counts every row, folded or not.
- **The other fold groups (2026-10-29, with the EDGAR index).** Two more kinds of row are routine, each folded per
  (day, company) into its own quiet line in the same style and under the same toggle, each line opening on its own
  (`filings.FOLD_GROUPS`, the form sets named once in `backend/dashboards/filings.py`):
  - `prospectus`: an index row whose form starts with 424B, 424H, 424I or 497, or is FWP — "JPM · 87 prospectus
    supplements (424B, FWP)". An S-1, an S-3 or an F-1 is NOT here: a new registration stays loud.
  - `ownership`: an index-only Form 3, 4, 5 (and their /A) — one no Form 4 tape row claims (derivative-only, or the
    company as another issuer's reporting owner) — and a Form 144 / 144/A notice of an intended sale (the loudness
    class of a Form 4 sale) — "META · 5 insider ownership filings (Forms 3/4/5 with no transaction rows stored, 144
    notices)", the parenthesis naming only what the line holds. The 144s sit behind ONE operator flag,
    `filings.FOLD_FORM_144` (default true): false makes every 144 a loud row again.
  Why (MEASURED on a dev copy of prod, 2026-10-29, 62 cards, 925,651 index rows): JPM (a Context name on one
  card) lists 26,439 filings in a year, 26,035 of them prospectus paper (424B2 22,965, FWP 1,817, 424B3
  1,217, 424B8 36); 15,254 index rows of form "4" and 6,523 of form "3" survive the dedupe; ~23,000 Form 144s a
  year. As loud rows they buried the stream and outshouted the parsed routine Form 4s.
- **The cap (server side, per request window).** Per (company, fold group) at most `ROUTINE_CAP` = 200 ROUTINE rows of
  the window are listed, newest first (the stream's stable order breaks ties). A row that is not routine is never cut
  (a purchase, an 8-K, an S-1, a stake). The rows cut are COUNTED, never vanished: `not_listed` (per company and
  group, largest first), `counts.not_listed`, and one quiet work line ("26,035 routine rows not listed (over 200 of
  one kind for one company in this window): JPM 25,835 prospectus supplements"); `counts.rows` / `by_kind` and each
  name's row count keep counting every row in the window, listed or not. The cached 365-day read keeps every row;
  the cap is applied when a window is sliced, so a 30-day window counts only the rows cut inside it. Why: the worst
  365-day payload went from 5.00 MB to 15.07 MB when the index landed (MEASURED on the dev copy). The Read-Throughs
  and the Chain Map carry no row lists (each picks ONE last filing per company), so the cap does not apply to them.
- **Measured with the fold and the cap** (the same dev copy, as-of 2026-10-29, 365 days, cold per card; before = the
  page without the index): the card holding JPM 3.35 MB / 2.3 s before, 15.07 MB / 4.7 s with the index
  unfolded, 4.35 MB / 3.8 s with the fold and cap; a card holding BLK 1.74 MB -> 2.27 MB. All 62 cards: median
  1.53 s / 1.23 MB -> 1.66 s / 1.77 MB; the worst card is now a 72-name card with no shelf issuer, 5.00 MB ->
  5.88 MB, from ordinary filings across its names (most of it folded 144s and index-only Form 3/4s, none over the
  cap). The cap left 592 routine Form 4 rows out of the 365-day lists, across the cards (counted; 6 basket companies
  hold more than 200 Form 4 filings in that year). The Chain Map: median
  1.78 s -> 2.20 s, max 6.25 s -> 7.91 s; the Read-Throughs' all-cards read: 20.1 s -> 23.7 s cold (both read each
  company's whole stored index, not only its last year).
- **Loudness:** no badges, no colors by kind. Each row is one line: date · ticker (a chip opening the Cockpit name panel
  via `onSelectThesis(thesisId, ticker)`) · form · headline · tags · the EDGAR link. The expanded row shows `detail`.

## Filters (client side, over the window's rows)

Kind (one chip per kind, all on by default), link (the card's links in chain order, plus Context, Bench, No link),
name (a type-ahead over the roster), role (the roles set on the card), "Counting names only", "Show routine Form 4s"
(or "Show routine rows").
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
prints, other forms, splits, dilution, catalysts, ratified or stored facts); folded routine rows; the cache state. Below
it, the honest notes: "every form in each company's EDGAR index is listed, from its recent window (at least a year, or
the last 1,000 filings); a form the system does not read into shows as its form and primary document under Other forms,
each filing once, as its richest row"; "the Cockpit's day-low and implausible-data insider screens are not applied here"; "4/As are shown as filings,
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

- The EDGAR index is its `recent` window (at least a year, or the last 1,000 filings): older filings are not stored
  (reading EDGAR's older index pages would cost extra requests). A form nothing reads into is listed by its form and
  primary document only. The index is insert-only: a filing EDGAR later re-lists with a changed form or document keeps
  its first-seen row (SEC's listed fields drift; re-versioning would churn the table nightly).
- At most 200 routine rows of one kind per company are listed in a window (the cap, above); the rest are counted in
  the work line and in every count, never listed. A routine row folds into a quiet line; a row that is not routine is
  never folded or cut.
- The Cockpit's day-low and implausible-data insider screens are not applied; the stream never says "open market".
- A 4/A is shown as a filing, not resolved against its original.
- A split is dated by its own publication as the view knows it; its status is as stored.
- The card-level catalysts (dated or undated) are the Catalyst Calendar's, not the stream's.
- The roster is the card's roster as of the as-of (hindsight selection); links are today's.
- No located passages: the row links to the filing, it does not quote it.

## Out of scope

Reading inside the other forms (an S-1's terms, a 424B5's size); EDGAR's index pages older than its `recent`
window; located passages; any LLM summary; a cross-card stream (the Read-Throughs table
is the cross-card lens); resolving amendments; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out (the Chain Wave's plan shows the form),
each the operator's to confirm or change. The first four are plan details that turned out different against the code;
the build fixed them sensibly and says how.

1. **Unlabeled 8-K items show as the bare code (plan detail corrected).** `item_label` answers "corporate event" for a
   code it has no label for (it knows seven items), which would read "9.01 corporate event". The reader treats that
   fallback as "unlabeled" and shows `9.01` alone, as the plan's own example does. The common items 2.02, 7.01, 8.01
   and 9.01 therefore showed bare. **Superseded by the Read-Throughs build:** `filings.py` now carries a display-only
   table of the standard Form 8-K items (1.01 to 9.01, SEC's short titles), used after `item_label`'s own wording and
   before the bare-code fallback, so 9.01 reads "9.01 financial statements and exhibits"; only a code in neither table
   still shows bare.
2. **The 4/A SELECT lives in the shared reader, not the router module (plan detail corrected).** The plan puts it in
   the router and also puts it inside `read_filing_rows`. It is in `filings.py` (`read_amendments`, a plain SELECT
   scoped by tenant), so the Read-Throughs table gets the same 4/A rows without a copy. The router only calls
   `read_filing_rows`.
3. **The 4/A gate falls back instead of dropping (plan detail corrected).** The plan's gate is `filed <= asof` on both
   clocks, plus `recorded_at <= known_at` on the record clock only. `insider_amendment.filed` is nullable, so a 4/A
   with no `filed` would vanish. The gate dates it by `COALESCE(filed, its acceptance in market time, the instant we
   first held it)`, the public clock's own order for a 4/A (`db.public_clock`). It is identical to the plan whenever
   `filed` is stored, which the ingest does unless the filing date fails to parse. The live record clock bounds `recorded_at` by the database clock
   (`db_now`), like every other live read. The newest stored version of each accession is the one read.
4. **`aff_10b5_1` exists, and it is filing-level (checked).** `fact_insider_txn.aff_10b5_1` (migration 0022) is a
   per-filing flag, so a Form 4 gets the "10b5-1 plan" tag when any of its rows says `True`; `False` and `NULL` assert
   nothing. Nothing is left out.
5. **The window includes both ends:** dates from `asof - N days` through `asof`, so "90 days" is 91 calendar dates.
   The same cut is made in three places (the SELECT, the reader and the page's window), so a row dated after the as-of
   cannot reach the page even if one of them were wrong.
6. **A Form 4 is dated by its SEC acceptance as a market-time date,** not by its trade date (`valid_from`, which rides
   in the detail line of each transaction). A filing with no acceptance counts from the first instant we held it, the
   public clock's rule for it. An 8-K and a 13D/G sort under SEC's `filed` date, so an after-hours acceptance sorts
   under the acceptance day for a Form 4 and under the next business day for an 8-K, each by its own public clock.
7. **Form 4 words:** the headline is `<filer> (<role>): ` then one phrase per code, "P purchase 12,000 sh @ $4.10 ·
   $49,200", in the order P, S, A, M, F, G, then any other code A to Z. The price is the shares-weighted mean over the
   rows that state both; dollars are the stored `usd` (else shares times price). The filer is the first row's owner,
   "and N more" when the filing holds other owners; a joint report's co-reporters are named in the detail. A column
   the row does not state is left out of the phrase, never filled in.
8. **`routine` = the filing holds no code-P row.** A purchase leg the internal-transfer screen tags, or an
   issuer-self purchase, is still a purchase and is never routine; it carries its tag. The fold line says "N routine
   Form 4 filings (none holds a purchase: sales, grants, exercises, withholding, other codes)": the plan's
   parenthetical named four codes, but "no purchase" also covers every other code (J, X, C, ...).
9. **A Form 4's `usd` is code-P dollars minus code-S dollars** (signed; empty when the filing holds neither; other
   codes are not counted). The page shows it at the line's right edge as "+$34.2K net", and a dilution's principal
   there as "$350.0M". No other row shows a figure there.
10. **An applied 4/A shows once.** An applied 4/A's rows replace the original's on the Form 4 tape and carry the 4/A's
    accession (`amends_accession`). They are folded into the 4/A's own row (detail lines, net dollars, an "applied 4/A"
    tag) instead of making a second filing row. The replaced original's Form 4 no longer shows, because the tape the
    Cockpit reads has replaced it. A tape group with no 4/A record in view stays a row of its own: a filing is never
    dropped.
11. **Quiet tags added beyond the plan's list:** "applied 4/A" (above), and "rejected" / "revoked" on a split with that
    status. A `needs_review` split gets "needs review" as planned; `confirmed` and `announced` get no tag (the status is
    in the detail either way).
12. **13D/G rows are listed as stored,** without the call's control screens (a 50%+ holder, an officer or director, a
    mis-attributed filer). The page says so beside the insider-screens note.
13. **Revenue words:** the form is the 10-K for a fiscal Q4 or FY row and the 10-Q for Q1 to Q3 (the stored series is
    quarterly, Q4 derived from the annual); a row with no fiscal period reads "10-Q / 10-K". The prior-year row is the one
    whose period end is nearest one year earlier within 10 days (a 52/53-week filer drifts a few days), from the same
    as-of read, with a positive value. The change shows as a whole percent from 10% up and one decimal below. A filing
    that states several periods makes one row headed by its latest period, the others in the detail. Revenue rows show
    no figure at the line's end.
14. **Splits:** every status is listed as stored. The date is the split's publication per clock: on the public clock the
    row's own public instant (`recorded_at` as `db.public_clock` rewrites it), on the live record clock the earlier of
    its `valid_from` and its effective date (`recorded_at` there is only when we stored it). The detail carries the
    status, the SEC filing's ratio and the Massive.com split that agreed, as stored; the link is the SEC URL when one is
    stored.
15. **Dilution overhang** = principal ÷ $1,000 × the conversion rate, as a percent of the offering's own stored
    `shares_outstanding` basis, per offering (the dilution signal sums the live offerings against the latest basis;
    this page shows each issuance on its own). It is left out unless the principal, the rate and a positive basis are all
    stored. A matured note is still listed (it is a filing).
16. **A fact is "ratified" only when someone ratified it; a machine-applied or feed-stored one is a "stored fact"
    (revised 2026-10-29 after dev with a prod data copy).** The three Workbench tables hold both: on dev,
    `fact_shares_outstanding` had 856 rows with `ratified_by = 'auto'` (source `10-q-cover`, applied by the
    Workbench's auto-confirm) against 113 `'operator'`, `fact_catalyst` had rows with `ratified_by` NULL (source
    `doe_usaspending`) and `fact_revenue_mix` one NULL. The first build called every one a "ratified fact" and dated it by
    its `recorded_at`, so a whole card's Oct 27 held six "ratified" rows that were an ingest batch.
    - **`ratified_by` is `operator`, or any other non-empty value but `auto`:** a "ratified fact", dated by the
      market-time date of its `recorded_at` (the public clock's own instant for these three tables: the instant the
      ratifier acted), as before. The period or cover date (`valid_from`) rides in the detail.
    - **`ratified_by` is NULL or `auto`:** a "stored fact" (`form` = "stored fact"). Its headline is led by its source as
      stored ("10-q-cover: shares outstanding 540.6M"; no lead when no source is stored), and its date is its
      `valid_from`, the cover or filing date (the fact's own public date, not the day we stored it). The detail says
      "Stored on <day>" (the market-time date of `recorded_at`), the source, and what the stored `ratified_by` says
      ("Applied automatically, not operator-ratified" for `auto`, "No operator ratification stored" for NULL). A stored
      fact with no `valid_from` falls back to its store day and says so; with neither date it is not a row (never a
      guessed day). The cover date can precede the 10-Q's acceptance by days, so the row can read a few days older
      than the filing itself; the reading is still gated by the point-in-time view (a past as-of never reads a fact
      we had not stored by then).
    - **The wire kind stays `ratified` for both** (no contract change, no new enum value): the row's `form` tells them
      apart, and the page's kind chip reads "Ratified and stored facts" (work line: "ratified or stored facts").
    - **A catalyst** keeps `kind` = `catalyst`, `form` = "catalyst" and its event date whoever stored it; only its
      source line changes: "ratified by <who>" appears only for a real ratifier, and a catalyst nobody ratified says so in
      the detail (it is no longer worded "ratified by auto").
    - A cash and burn fact with a burn of zero or less reads "cash-positive quarter". A catalyst reads
      "<type>: <label>", with its grade, horizon and source in the detail.
    - The shared reader exports `SEC_FILING_KINDS` (8-K, 13D/G, Form 4, 4/A, revenue print): the kinds that are SEC
      filings. Read-Throughs and the Chain Map pick "last filing" among these alone.
17. **A source link is made only from a real reference:** a stored http(s) URL as is, an accession-shaped reference
    through the EDGAR index, nothing for an award id or free text. A catalyst's non-EDGAR link reads "source" instead of
    "EDGAR".
18. **The roster:** a company is one entry whatever its rows (links folded, the role the first one set, silent when any
    row is). Its ticker on a row is the card's own (the Cockpit's `?name=` key). The link filter offers the card's real
    links in today's chain order (a link nobody sits in is still offered), then a roster link that is not on the card
    today, "No link", then Context and Bench when a company sits there. A company in two links matches both.
19. **Controls that cannot discriminate do not render** (Workbench principle 3): kind chips only for kinds the window
    holds (and only when it holds two or more), the Link select with one link, the Name box with one name, the Role
    select when no role is set, "Counting names only" with no silent name, "Show routine Form 4s" with nothing to fold.
    Switching card clears the link, name and role filters (the kinds and the two checkboxes stay).
20. **The fold is per day,** one line standing where the day's first routine row was; each day's fold opens on its own.
    The filters apply first, so a fold counts only the routine rows that pass them. The kind chips show the window's
    totals; the work line says how many rows the filters leave.
21. **The date is not repeated on each row:** the day header carries it. A row is chip, form, headline (opens the
    detail), quiet tags, dollar hint, source link.
22. **The 4/A's standing link** is a plain app link to `/admin`: the Admin page has no tab address for "Insider
    amendments".
23. **The window is an integer enum on the wire** (`FilingStreamDays`: 30 | 90 | 365), default 90. The 404 reads the
    cached picker list before the stream's read, like the Chain Wave.
24. **Within a day** rows sort by kind, then ticker, then accession, form and headline (a fixed order, so a page never
    reshuffles).
25. **The reader's signature has one addition:** `read_filing_rows(..., tickers=None)`, so the Filing Stream can show the
    card's own ticker; without it the master's ticker stands in.
26. **The shared picker moved with its helpers:** `baskets.picker_cards`, `CardChoice`, `CardsRead`, `card_choices` and
    `link_count`. The Chain Wave re-exports each under its old name and its tests pass unchanged. The frontend
    registry test, which pins the full dashboard list, gained this slug and order (the one shared test line a new
    dashboard edits).
27. **Measured on dev with a prod data copy (2026-10-29, 62 live cards, as-of 2026-10-29):** every card serves at the
    365-day window (0 failures after the zero-share fix). Cold compute: median 1.3 s, the heaviest card (the largest card:
    72 roster rows, 4,699 rows over 250 days) 6.0 s with a 4.8 MB payload; the second largest 3.3 s / 3.3 MB; the 90-day
    window is a cache hit on every card (50–90 ms, payloads 0.3–1.1 MB). The 365-day payload is large because every
    row carries its detail lines; a per-row detail read is the lever if it matters off localhost.
28. **A row states only what it states (found on dev with a prod copy).** A real Form 4 row can state a price against a
    share count of zero, which broke the shares-weighted mean price (a 500 on two large cards). The mean now needs a
    positive share weight; without one the "@ $price" is left out of the phrase (the row's own detail line still shows
    its stated price). A Form 4's net dollars are empty unless some row states dollars (or shares and a price to make
    them from), never `$0`. A stored number that is NaN or infinite reads as not stated; a stated price of exactly zero
    (a grant) reads "$0.00". The aware and naive stamps of one filing are read as UTC before they are ordered. Nothing
    in the reader catches an exception: a bug stays visible, so the normalizers are made total over what a row can hold
    instead (a test runs every mix of stated, zero, missing and non-finite numbers).
29. **Compact figures roll over:** a figure is rounded to what is shown (a whole number below a thousand, one decimal
    above) and one that rounds up to 1,000 moves to the next unit, so 999,949 reads "$999.9K" and 999,950 reads "$1.0M"
    (the dollar hint on a row said "-$1000.0K net"). The server's `fmt_compact` and the page's `compactUsd` share the
    rule. They differ only on an exact binary tie such as 1.25 (Python rounds to even, the browser up).
30. **Hover and open are neutral.** The headline, the fold line and the source link set every state from the text tokens
    (brighter text, a faint underline, a faint panel ground on the open row) and never `--pos` / `--neg` / `--armed`; a
    test scans the stylesheet for any state color. The red headline seen on dev could not be reproduced here (the
    computed color of the open headline is `--txt`, and no rule in the app's sheets reaches it), so every state is now
    explicit instead of left to a default; if it shows again, the computed color in the browser's inspector says which
    rule it is.
