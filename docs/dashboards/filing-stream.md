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
each the operator's to confirm or change. The first four are plan details that turned out different against the code;
the build fixed them sensibly and says how.

1. **Unlabeled 8-K items show as the bare code (plan detail corrected).** `item_label` answers "corporate event" for a
   code it has no label for (it knows seven items), which would read "9.01 corporate event". The reader treats that
   fallback as "unlabeled" and shows `9.01` alone, as the plan's own example does. The common items 2.02, 7.01, 8.01
   and 9.01 therefore show bare; a fuller label table is a one-line change in `filings.py` if you want it.
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
16. **Ratified facts are dated by the ratification,** the market-time date of their `recorded_at` (the public clock's own
    instant for these three tables), not by the period or cover date (`valid_from`), which precedes the filing's
    publication; that date rides in the detail. A cash and burn fact with a burn of zero or less reads "cash-positive
    quarter". A ratified catalyst reads "<type>: <label>", with its grade, horizon and source in the detail.
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
27. **Not measured:** the payload and compute time of a 365-day window on a large card with real data (this build ran on
    hand-built rows and the test database). A 365-day Form 4 history for a 150-name card is the heaviest case; the
    cache computes it once per card and as-of. Check it on dev with a prod copy before relying on it.
