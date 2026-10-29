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
