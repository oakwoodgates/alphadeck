# CORPORATE_EVENTS.md — the 8-K item-code tape (Band 03 · S3)

> One EDGAR ingest, many signals. 8-K **item codes** are structured, enumerable, and deterministic —
> the SEC's own taxonomy IS the classification (invariant #3: no NLP, no LLM anywhere on the fire
> path). This doc is the family's one page: the evidence layer, the policy map, the two detectors,
> and the inert-first posture. Shipped **inert** (both switches OFF); the sig-lab pass finalizes the
> `[PROPOSED]` dials before the operator flips anything.

## The organizing seam — evidence vs policy

- **EVIDENCE (code, objective):** `fact_corporate_event` (migration 0038) + the per-member ingest
  leg. One row per (security, 8-K filing): `form` (8-K | 8-K/A), `items text[]`, `accession`,
  `filed`, `source_ref` (the EDGAR filing-index URL). **Every 8-K is stored with whatever items it
  carries** — recall-safe (#9): an item outside today's detector cut stays on the tape, so the
  deferred slices (§below) need no re-ingest.
- **CALL-POLICY (config, tunable):** `CallConfig.corporate_event_items` — the item-code →
  (role, grade, type, score, liveness) map, applied on **read** by the detectors, never baked into
  stored rows. Retuning a value re-derives every call with zero data repair; `kind` is derived from
  role (trigger ⇒ `CATALYST`, risk ⇒ `CORPORATE_RISK`) and *severe* is derived from
  `score >= risk_block_severity` — neither is a stored field, so a flag can never contradict the
  score. Dial rows: `docs/RECALIBRATION.md`.

## Evidence layer

- **Bitemporal + append-only**, the full fact-family checklist: `_FACT_IDENTITY` entry
  (`["accession"]` within the security scope) · live PIT accessor `corporate_event_facts` +
  `SignalPointInTimeData` protocol entry · the `replay/pit.py` mirror twin (+ the `items`
  `text[]` ↔ JSON round-trip via `_JSON_COLS`) + parity/lookahead tests · count-the-table
  idempotency · the tenant-isolation poison-row growth.
- **Knowability is free here:** `valid_from = filed` — the EDGAR acceptance date IS when the filing
  became knowable (the cleanest bitemporal source on the signal menu). `recorded_at` = the DB's
  `now()`, never backdated (#4).
- **The natural-key constraint carries `security_id` from birth** —
  `UNIQUE (tenant_id, security_id, accession, recorded_at)` — the 0037 lesson: one issuer held as
  two master rows stores the same filing once per scope, and same-instant re-versions under two
  securities must not collide. Pinned by a regression test
  (`tests/ingest/test_form8k.py::test_same_filing_two_securities_same_instant_does_not_collide`).
- **Ingest = a fourth leg on `pipeline.ingest_thesis`** (`ingest/edgar/form8k.py` +
  `_form8k_leg`), riding the nightly cron automatically. It reads the **same submissions JSON the
  Form 4 leg fetches** — the `items` array parallels `accessionNumber` — so the whole tape costs
  **zero extra fetches** (no per-filing documents). Same accepted depth as Form 4: submissions
  `recent` covers ≥ 1 year / 1,000 filings.
- **Append-if-changed:** a new accession appends; an unchanged one appends nothing (count-the-table
  idempotent); a stored `items = NULL` filing (EDGAR hadn't classified it yet — stored as NULL,
  never guessed) gets a **new version** when its items resolve. The shared items parser is
  `submissions.parse_item_codes` — one parser, also the SPAC radar's (`spac._items_for`).

## The two detectors (both read the same tape; both `[PROPOSED]`-dialed)

### `corporate_catalyst` — the trigger side (extends the catalyst family)

`kind=CATALYST`, so it inherits the existing `conviction_kinds` membership (Key-1 co-location
arming) and `own_conviction_kinds` ranking with zero config-set changes, and `call_grade=max`
composes it beside ratified catalysts. v1 cut: **1.01** material definitive agreement →
`contract`/CORE (a material contract is the narrative landing in the business); **5.02**
officer-director change → `personnel`/FLIP (direction ambiguous — the evidence link does the work).
Strongest-live-item wins (prefer core, then most recent — `catalyst_conviction`'s selection); every
other live trigger item rides the provenance.

**Deliberately NOT a `fact_catalyst` feed:** writing pre-graded rows would bake policy into
evidence AND feed the live `catalyst_conviction` the moment facts land — no inert-first possible
(gating the ingest would starve the lab). Known v1 gap: 8-K catalysts do not feed the Workbench
catalyst-density meter (it reads `fact_catalyst`); revisit at flip time.

### `corporate_risk` — the risk side (`Kind.CORPORATE_RISK`)

Grade-blind like dilution (`grade=None`, no `dearm_grade` — not a de-arm); the assembler composes
it through the **existing role+score path with zero kind branches**: a SEVERE item
(score ≥ `risk_block_severity`) withholds the NAME on timing; a moderate one feeds the counter-case
+ the per-risk confidence haircut. v1 cut: **3.01** listing-deficiency + **4.01** auditor change =
moderate (0.50); **4.02** non-reliance/restatement (0.80) + **1.03** bankruptcy (0.90) = SEVERE.

One event per name per read (the detector contract): the max-severity live item headlines; every
other live risk item is enumerated in the label and carried in provenance. Accepted consequence:
co-live moderates cost ONE confidence haircut, not one each. **Freshness is detector-enforced**
(the assembler never ages risks): each item lives `liveness_days` from its filing date, then drops
out of the re-derived stream.

**8-K/A:** an amendment is its own tape row; these existence/latest-shaped reads can't double-count
it (the score is the item's policy value, never a sum). Amendment *reconciliation* is out of scope,
matching the recorded Form 4/A known-gap posture (INVARIANTS.md).

## Provenance (#6) — the located-passage descope

v1 provenance = accession + item code + the EDGAR filing-index URL (the `dilution_clock` 8-K
shape) — a real, checkable source on every fired event. The inline **located passage** is a
**deferred, operator-priced follow-up** (operator decision 2026-08-17): a passage cannot be fetched
at detect time (detectors are pure — no network) and storing one at ingest means fetching every
8-K's full text per member, an ambient cost the cost-thread reserves for the operator. The radar's
`matcher.fetch_filing_text` is the reusable piece when wanted.

## Shipping posture — inert-first, two switches

`corporate_catalyst_enabled` / `corporate_risk_enabled`, **both default OFF** (the `insider_sell`
precedent; one per side because the blast radii differ — the trigger side can warm/arm, the risk
side can withhold). Registered but `detect()` no-ops until enabled → with the live `DEFAULT_CONFIG`
nothing reaches a card and every golden is byte-for-byte unchanged; the pure `score()` functions
stay testable ungated. `replay.run --corporate-catalyst` / `--corporate-risk`
(`ALPHADECK_CORPORATE_CATALYST` / `ALPHADECK_CORPORATE_RISK`) force them on for the sig-lab pass —
fire distribution, per-item frequency, verdict-diff off-vs-on, EDGAR ground-truth spot-checks —
which finalizes the `[PROPOSED]` dials before the operator flips either default. Nothing
re-verdicts prod unmeasured.

## Deferred — same ingest, no re-ingest (the recall-safe payoff)

- **§3b cadence** (`2.02`/`7.01`/`8.01` trailing counts → `promoter_attention`): a COUNTING
  detector — must dedupe 8-K/A amendments, and its baseline may want the paginated older
  submissions pages (`filings.files`) beyond `recent`.
- **§3c uplisting** (Form 8-A / exchange 8-K → core-eligible catalyst).
- **`3.03`/`5.03` reverse-split tells** (dilution family).
- The Workbench catalyst-density meter reading 8-K catalysts; the inline located passage (above).
