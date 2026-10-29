# Evidence Ledger

> Slug `evidence-ledger` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 180`.
> The operator approved this dashboard (2026-10-29); the choices the build makes that this plan does not spell out go
> under "Choices to confirm" for the operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`. The
> seventh page of the RESEARCH set; it reads the Filing Stream's shared reader (`backend/dashboards/filings.py`) and the
> recorded calls log, and adds no data.

**Label:** Evidence Ledger. **Description:** "Everything one card's call and facts rest on, in one list: each trigger
with its source, each ratified and stored fact, each catalyst and kill criterion, marked by who put it there."

It answers "what is this card's provenance, and how much of it did I ratify": one card at a time, one dated list of
every piece of evidence behind the card as recorded at the as-of, each row carrying its **authority class** and its
source link. It is the research set's audit page and the natural companion of the Filing Stream (what was filed) and
the Workbench (what was ratified). Display only: it never feeds a call, never writes, never calls the LLM. Reading the
recorded card is a read of the `calls` log (rule 1), never a live call.

## The rows

Every row is `{date, class, kind, label, company, source, link, detail}`, newest first, from these sources:

1. **The recorded card's triggers** (`class = system`): the card in effect at the as-of from the `calls` log (the What
   Changed dashboard's "in effect at D" rule: the greatest `(asof, seq)` with `asof <= D`, one row per card; at a live
   as-of the latest row), its `triggers_fired[]` and its fired risk signals, one row per `TriggerRef`: the label, kind,
   grade, event date, the company (`security_id` → ticker), and each `Provenance` as the source (`source` = form4 /
   price / xbrl / ...; `ref` = the accession or the price reference; `detail` = the computation inputs, shown on
   expand). An accession links to its EDGAR index (`filings.filing_index_url`); a price reference shows as text. A
   reconstructed row (`reconstructed = true`) is marked "reconstructed, never scored".
2. **Ratified and stored facts** (`class = operator` for `ratified_by` = operator, `class = automatic` for `auto` /
   NULL): the Filing Stream reader's `ratified` and `catalyst` kinds over the card's roster (`filings.read_filing_rows`,
   365 days, the roster at the as-of), each with its source reference and, for an operator row, who ratified it and
   when.
3. **The card's own entries** (`class = card`): its catalysts (`Catalyst`: label, kind, `when_date` or `when_label`),
   its kill criteria (`KillCriterion.text`) and its evidence links (`Evidence`: kind, label, ref, date_label). These are
   the operator-maintained spine entries; they load live (not snapshotted), and the page says so. A dated catalyst
   sorts under its date; an undated one and a kill criterion sit in a "standing" section above the dated list.
4. **Context roles** (`class = operator`): each role-tagged company on the roster at the as-of as one row dated by the
   roster snapshot that first carried the role when a snapshot exists (`thesis_repo` snapshots), else undated in the
   standing section; the role's operator-approved description on hover.

Not in the ledger: display signals (never evidence), thesis-fit prose (drafted description, not a fact), located
passages (not stored). Each is named in the work line as "not evidence".

## The authority classes

| Class | Means | Shown as |
|---|---|---|
| operator | a human ratified or set it | "your" tag, the ratifier and date |
| system | a computation on public data (a trigger, a risk signal) | "computed" tag, the inputs on expand |
| automatic | an ingest leg stored it, no human | "stored" tag, the source |
| card | a card-level entry the operator maintains | "card" tag |

The header carries the tallies: rows per class, and "N of M triggers trace to an EDGAR accession" (a trigger with only
a price reference still traces, to the bars; the count says which). Honest loudness: no row is louder than another;
the class tag is a quiet word.

## Filters (client side)

Class (multi), kind, company (type-ahead over the roster), "only rows with an EDGAR link", a date window (30 / 90 /
365 days / all stored). Component state only.

## Picking the card

The shared picker (`baskets.picker_cards`), the first card by default; the card's name calls `onSelectThesis(thesisId)`;
each company chip calls `onSelectThesis(thesisId, ticker)`.

## The quiet "show the work" line

The card and the as-of; the clock; which recorded night the triggers come from (its `asof` and whether it is
reconstructed); rows per class; triggers that trace to an accession / to a price reference; ratified vs stored facts;
catalysts dated / undated; kill criteria; roles; roster rows / unresolved; the "not evidence" list; the cache state;
the roster note when today's roster stands in.

## Cache

`dashboards.cache.cached(conn, "evidence-ledger", (str(thesis_id), clock), asof, ...)` holds one card's ledger. The
picker list is cached as `("cards", clock)`. The cache clears on a new run or a basket edit (rule 7).

## Backend and frontend

- `backend/dashboards/evidence_ledger.py`, pure: the row assembly from the recorded card, the fact rows, the card
  entries and the roles; the class rule; the order; the tallies.
- `backend/app/routers/dashboards/evidence_ledger.py`: `GET /dashboards/evidence-ledger/cards?asof=` (the shared
  picker) and `GET /dashboards/evidence-ledger/{thesis_id}?asof=` (the ledger; 404 for a card not counted at the
  as-of). The recorded card read is the What Changed router's `_IN_EFFECT_SQL` pattern (a plain SELECT on `calls`,
  never `call_for_thesis`). `known_at, clock = serve_view(asof)` threads into every read.
- `backend/tests/dashboards/test_evidence_ledger.py`: the class rule for every source, a trigger's provenance rows
  (accession vs price reference, the EDGAR link), the in-effect card at a past as-of (a later night's row never
  read), a reconstructed row marked, the standing section, the tallies on hand-built rows; the router off one cached
  read with its 404; and, DB-backed, **a call recorded after a past as-of is not the card in effect** and **a fact
  ratified after it is not a row**.
- `frontend/src/dashboards/evidence-ledger/`: `entry.ts` (order 180, `asofAware: true`), `EvidenceLedger.tsx` (the
  picker, the tallies, the standing section, the dated list, the filters), its hooks, `ledgerView.ts`, CSS, tests. The
  Filing Stream page is the pattern (day headers, one line per row, the expanded detail block, the chips).

## Known limits (stated on the page too)

- **Card entries load live** (catalysts, kills, evidence links are not snapshotted); the triggers and facts are as of
  the as-of.
- **The `evidence` table is nearly unused today** (2 rows on 2026-10-29): the ledger's substance is the triggers, the
  facts, the catalysts and the kills.
- **A trigger's `detail` is the computation's own inputs**, shown as stored; the page does not re-derive them.
- **Located passages are not stored**, so a FLAG ratification links to its filing, not its passage.

## Out of scope

Any judgment of the evidence; editing anything (the Workbench is the editor); the read-through panel; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
Pre-decided here for the operator's review: the four authority classes; roles as ledger rows; card entries live; the
What Changed "in effect at D" rule for the recorded card.

The first four below are plan details that turned out different against the code; the build fixed them sensibly
and says how. The rest are choices the plan does not spell out.

1. **The row's `class` is `authority` on the wire (plan detail corrected).** `class` is a reserved word in Python and
   a poor field name in TypeScript, so the field is `authority` (`operator` | `system` | `automatic` | `card`). The
   page's quiet words are the plan's: "your", "computed", "stored", "card".
2. **The Filing Stream's reader now says who ratified a row (plan detail corrected).** The plan reads the class off
   `ratified_by`, but the shared `FilingRow` carried only text ("ratified by operator" inside a detail line), and
   parsing words would break on the next rewording. `filings.py` gains two optional fields, `ratifier` and
   `ratified_on` (default `None`), set only by the catalyst and the ratified-fact normalizers: the ratifier is the
   stored `ratified_by` when it names anyone but `auto`. The Filing Stream's wire ignores them (the OpenAPI diff holds
   only the new routes), and one public alias, `accession_of`, names the existing accession test. The class rule is
   one function, `fact_authority`: a ratifier means `operator`, none means `automatic`.
3. **"Who ratified it and when" (plan detail corrected).** A ratified fact's row date IS the day it was ratified, so
   `on` repeats it. A catalyst fact keeps its EVENT date as the row date (as the Filing Stream dates it), and the day
   it was ratified rides in `on` (the market-time date of the fact's stored instant). A stored fact names no ratifier.
4. **The recorded card is read by its night, not by when it was written (plan detail corrected).** The plan says "the
   last row recorded on or before the as-of". The log's rows are keyed by the night they describe (`asof`), and a
   reconstructed night is written later than the night it describes, so the page uses What Changed's rule exactly
   (the greatest `(asof, seq)` with `asof <=` the as-of). A reconstructed night therefore counts under its night and
   is marked on its rows, "reported, never scored". The `call_annotation` rows (a set-aside phantom arm) are not
   applied: the ledger shows what the log recorded.
5. **Card entries are read live, per request; only the heavy read is cached.** The plan has the cache hold one card's
   ledger, but editing a catalyst or a kill criterion moves none of the cache's tokens (a run, a basket edit, a card
   rename), so a cached ledger would show a stale entry while saying "loads live". The cache holds the triggers,
   facts, roles and roster; the entries come from `thesis_repo.get` on every request.
6. **"N of M triggers trace to an accession" counts triggers and risk signals.** The ledger lists both, so the
   header's sentence is over the "fired signals": N trace to an EDGAR accession, K to a price reference only, J to
   another source, L state no source. A signal traces to an accession when ANY of its sources' references is
   accession-shaped.
7. **One row per `TriggerRef`, all its sources on it.** An insider cluster cites one accession per filing; the row
   lists each (with its inputs under it) and the line links the first, EDGAR before any other. An accession links
   through the ISSUER's CIK from the master (never the accession's own prefix, which is the filing agent's); a
   company with no CIK gets the reference as text and no link. A price reference is text. A trigger with no event date
   sits in the standing section and a trigger's "inputs" are the detector's own `detail`, sorted by key, a value it
   did not state left out.
8. **Evidence links sit in the standing section.** `Evidence.date_label` is free text ("Oct 2026"), not a date, so a
   link is never dated; its label shows in the row. Its reference is a link only when it is a stored URL (an accession
   alone names no company here).
9. **A role is dated by the first snapshot of the unbroken run that carries its CURRENT role.** The plan says "the
   roster snapshot that first carried the role". A role that changed or was dropped and came back would otherwise be
   dated by an earlier, different role, so the run restarts on a change. The snapshot's `taken_at` is our own store
   time of the promote (the only record of when a roster edit happened), read in market time, and only snapshots taken
   by the as-of are read. A role no snapshot dates (today's roster standing in, or a roster never snapshotted) is
   undated and says so.
10. **Context and Bench companies are on the roster and tagged, with a "Counting names only" check (not in the plan).**
    The facts are read for every roster row, as the Filing Stream does, and a row of a Context or Bench company carries
    the quiet "Context" / "Bench" tag. The check leaves them out; it renders only when such a row exists. The
    triggers of the recorded card are voting names by construction.
11. **The date window defaults to "All rows held".** 30 / 90 / 365 days cut DATED rows by their date from the as-of;
    a standing row has no date and is never cut, and a row dated after the as-of (a future card catalyst) is never
    cut either. The stored and ratified facts are read for the last 365 days only, so "all" never reaches further back
    than that for them (the work line says "the last 365 days").
12. **A card catalyst dated after the as-of is in the dated list at its date** ("Dated 2026-11-20, after the as-of").
    The entries load live, so at a past as-of this can list an entry the operator added later; the page's limits line
    says entries are today's.
13. **A recorded row the call model cannot read is said, not raised.** The page shows "a call is recorded for this
    as-of but its card could not be read" and no triggers, and the rest of the ledger stands.
14. **Controls that cannot discriminate do not render** (WB#3): the class and kind filters with one value held, the
    company type-ahead on a one-company roster, "Only rows with an EDGAR link" with no such row, the window with no
    dated row. A filter hides rows from the page, never from the tallies or the work line.
15. **Measured on dev with a prod data copy (2026-10-29, 62 live cards, as-of 2026-10-29):** every card serves; cold
    compute median 1.2 s, max 4.8 s; payload at most 164 KB. Every card's triggers came from the call recorded for
    2026-10-28. The heaviest card: 174 rows, 97 fired signals (45 tracing to an EDGAR accession, 52 to a price
    reference), 50 stored facts, 2 ratified, 8 dated and 4 undated catalysts, 8 dated roles. A past as-of (2026-09-15)
    reads the card in effect that night on the public clock (the first card: 280 rows, 99 ratified facts then).
