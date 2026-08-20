# CORPORATE_EVENTS.md — the 8-K item-code tape (Band 03 · S3)

> One EDGAR ingest, many signals. 8-K **item codes** are structured, enumerable, and deterministic —
> the SEC's own taxonomy IS the classification (invariant #3: no NLP, no LLM anywhere on the fire
> path). This doc is the family's one page: the evidence layer, the policy map, the two detectors,
> and the shipping posture. **`corporate_risk` is live** (#280, 2026-08-18 — the 8-K counter-case
> teeth) and now carries the **`3.02` dilution risk** (the "1.01 decision", 2026-08-20);
> **`corporate_catalyst` stays inert** and is now **`5.02`-only** — `1.01` was demoted out of the
> catalyst (the decision below).

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

## The two detectors (both read the same tape; `corporate_catalyst` still `[PROPOSED]`-dialed)

### `corporate_catalyst` — the trigger side (extends the catalyst family)

`kind=CATALYST`, so it inherits the existing `conviction_kinds` membership (Key-1 co-location
arming) and `own_conviction_kinds` ranking with zero config-set changes, and `call_grade=max`
composes it beside ratified catalysts. v1 cut (after the 2026-08-20 decision): **5.02**
officer-director change → `personnel`/FLIP (direction ambiguous — the evidence link does the work) is
now the **only** trigger item. **`1.01` was DEMOTED** out of the catalyst (the resolved finding below);
it stays on the tape but fires no catalyst. Strongest-live-item wins (prefer core, then most recent —
`catalyst_conviction`'s selection; the prefer-core branch is unexercised by the 5.02-only cut but kept
for the next core-eligible item); every other live trigger item rides the provenance.

**Deliberately NOT a `fact_catalyst` feed:** writing pre-graded rows would bake policy into
evidence AND feed the live `catalyst_conviction` the moment facts land — no inert-first possible
(gating the ingest would starve the lab). Known v1 gap: 8-K catalysts do not feed the Workbench
catalyst-density meter (it reads `fact_catalyst`); revisit at flip time.

**Resolved (2026-08-20) — the `1.01` flood; option A + `3.02` risk-routing.** The prod force-on
measurement (`call_for_thesis(record=False)`, off-vs-on) had found that enabling the catalyst newly
**arms ~80 names** (~⅓ of the psychedelic basket), because **Item `1.01` is mostly financing, not
deals.** Of 894 live basket `1.01`s: ~30% carry `+3.02` (unregistered equity → **dilution**; verified on
PSQH's real securities-purchase agreement, which had fired as a *core bullish* catalyst), ~31% `+2.03`
(debt), ~7% `+2.01` (M&A), ~44% "bare" — **~6 in 10 are financing** (a lower bound; registered offerings
carry no `3.02` tell and hide in the bare bucket). **Grade does not gate the flood:** the assembler arms
on a conviction-KIND that co-locates with a confirmation, and grade is computed *after* — it only labels
the verdict, breaks the freshness tiebreak, and matches de-arms. So regrading `1.01` core→flip would
have kept all ~80 arms with a quieter label; **the lever is the conviction _role_, not the grade.**

**The operator chose option A:** `1.01` is removed from `corporate_event_items` entirely — it is no
longer a conviction (conviction is what _ratified_ catalysts carry), stays on the tape (#9), and fires
nothing. The dilution it hides is routed to the **risk** side as **`3.02`** (`risk` / 0.50 / 180d),
**single-item by design:** a `3.02` = unregistered equity SOLD = dilution regardless of a co-located
`1.01`, so it routes to risk on its own — MEASURED **99** live basket names carry a `3.02` in the
trailing 180d, incl. **32 with no `1.01`** at all, which a co-item-only split would miss. So a financing
8-K now reads correctly *bearish* (a sub-veto dilution counter-case) instead of falsely *bullish*.
`corporate_catalyst` becomes **5.02-only** and **stays parked** (its switch is still OFF); the `3.02`
risk rides the already-LIVE `corporate_risk`. The deferred `1.01`-co-item split (below) is
**superseded** by A. A ratified-subset / located-passage `1.01` path (**C** — an LLM _recommends_, the
operator ratifies, #3/#10) remains a possible later build.

### `corporate_risk` — the risk side (`Kind.CORPORATE_RISK`)

Grade-blind like dilution (`grade=None`, no `dearm_grade` — not a de-arm); the assembler composes
it through the **existing role+score path with zero kind branches**: a SEVERE item
(score ≥ `risk_block_severity`) withholds the NAME on timing; a moderate one feeds the counter-case
+ the per-risk confidence haircut. v1 cut: **3.01** listing-deficiency + **3.02** unregistered equity
(dilution) + **4.01** auditor change = moderate (0.50); **4.02** non-reliance/restatement (0.80) +
**1.03** bankruptcy (0.90) = SEVERE. (`3.02` was added 2026-08-20 as the dilution-risk half of the
`1.01` decision — see the resolved catalyst finding above.)

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

`corporate_catalyst_enabled` / `corporate_risk_enabled` — one switch per side because the blast
radii differ (the trigger side can warm/arm, the risk side can withhold). Both **shipped**
default-OFF (the `insider_sell` precedent): registered but `detect()` no-ops until enabled → nothing
reaches a card, every golden is byte-for-byte unchanged, the pure `score()` functions stay testable
ungated. `replay.run --corporate-catalyst` / `--corporate-risk` (`ALPHADECK_CORPORATE_CATALYST` /
`ALPHADECK_CORPORATE_RISK`) force one on for the sig-lab pass — fire distribution, per-item
frequency, verdict-diff off-vs-on, EDGAR ground-truth spot-checks — which finalizes its `[PROPOSED]`
dials before the operator flips that default. Nothing re-verdicts prod unmeasured.

**Live state (2026-08-18 → 2026-08-20): `corporate_risk` is ON** (`corporate_risk_enabled=True`,
#280) — pre-verified safe (0 withheld / 0 verdict changes on real prod data), surfacing moderate 8-K
counter-case (e.g. 29 fires on the psychedelic thesis's card). The **2026-08-20 `1.01` decision** then
**added `3.02`** (unregistered-equity dilution, 0.50 moderate) to its live cut — a real new dilution
counter-case on financing 8-Ks (sub-veto: 0.50 < `risk_block_severity` 0.70, can never withhold an
arm). **`corporate_catalyst` stays OFF** and is now **5.02-only** — `1.01` was demoted out of the
catalyst (above).

## Deferred — same ingest, no re-ingest (the recall-safe payoff)

- **The `1.01` co-item split — SUPERSEDED by option A (2026-08-20).** This was the alternative routing
  (`1.01+3.02` → **risk** [dilution; ~270 cases flip from false-bullish to correctly-bearish],
  `1.01+2.03` → quiet **debt context**, and **bare `1.01`** as the only candidate catalyst; bare-only
  trimmed firing securities 284→197, still hiding registered offerings). The operator instead demoted
  `1.01` **entirely** and routed `3.02` as a **single-item** risk (above) — simpler, and it catches the
  32 `3.02`-without-`1.01` dilution names a co-item rule would have missed. Kept here as the record of
  the path not taken.
- **§3b cadence** (`2.02`/`7.01`/`8.01` trailing counts → `promoter_attention`): a COUNTING
  detector — must dedupe 8-K/A amendments, and its baseline may want the paginated older
  submissions pages (`filings.files`) beyond `recent`.
- **§3c uplisting** (Form 8-A / exchange 8-K → core-eligible catalyst).
- **`3.03`/`5.03` reverse-split tells** (dilution family).
- The Workbench catalyst-density meter reading 8-K catalysts; the inline located passage (above).
