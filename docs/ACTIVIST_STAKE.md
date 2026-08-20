# The SC 13D activist-stake tape (Band 03 · S5)

**Status: LIVE (master switch `activist_stake_enabled` default ON — flipped 2026-08-20).** The
evidence layer (`fact_activist_stake` + the `schedule13` ingest leg) fills nightly; the detector
(`signals/activist_stake.py`) fires a **Key-1 CORE conviction** on a live 13D-family original. The
switch flipped after the **data-quality screen** for mis-attributed rows (below) shipped and a
**clean off-vs-on re-measure** validated it (asof 2026-08-20, prod, via `call_for_thesis(record=False)`):
**29 clean warm fires → 10 arms, ≤4/thesis (NO flood)**, and every surviving fire is a real 13D
(filer ≠ subject, pct ≥ 5) — RLBY, FRMI, NUCL, BBUC, JAGU, PBM, SPTX, PBLS, STIM, INM, USAQ, XOMA.
The screen **removed the mis-attributed tail** (the 5 self-filed / sub-5% pre-screen fires: UEC
self-filed 7.7%, ISOU self-filed 16.1%, CBRS 0.4%, GME/EBAY 0.01%) while **keeping the one valid
cross-subject** (JAGU, IsoEnergy→Jaguar from ISOU's own accession — per-subject screening, not
per-accession). The earlier 2026-08-19 sig-lab pass (pre-screen: 36 warm fires → 8 arms) is what
surfaced the defect.
Related: `docs/CORPORATE_EVENTS.md` (the S3 sibling), `docs/RECALIBRATION.md` (the dial rows),
`docs/DATA_SOURCES.md` §SC 13D/G.

## What it is

A new **SC 13D** filed *about* a basket member means an outside party crossed **5% with intent to
influence** — a rare, deliberate, capital-committed act by an informed party. The activist
event-study literature (Brav/Jiang et al.) measures persistent post-**filing** abnormal returns;
practitioners treat a credible 13D the way this platform treats an insider cluster. It fires a
**Key-1 CORE conviction** (`Kind.ACTIVIST_STAKE`, a `conviction_kinds` member): it **WARMS**, and
arming still needs a co-located confirmation (the two-key gate) — 13D originals are rare per name
(measured: ONE per six years on the richest real subject), so it cannot flood.

**The clean-#3 core: the FORM TYPE is the entire fire decision.** 13D vs 13G is the SEC's own
deterministic intent classification — no NLP, no cover parse, no LLM anywhere near the fire path.
The filer identity and %-owned the tape carries are **evidence** shown beside the call (#6), never
fire inputs.

## The enumeration (verified live, 2026-08-18)

A 13D/G is filed **by** the activist **about** the member — but EDGAR indexes an ownership schedule
under **both** entities, so the member's own `data.sec.gov/submissions/{CIK}.json` lists every
13D/G filed about it. Verified on three real subjects (CMPS: 35 rows across both form eras; NRXP:
32 incl. three SC 13Ds; MindMed: 18, all 13G — correctly no 13D, cross-checked against EFTS and
the FCM MM Holdings episode, which was DFAN14A proxy material and never a 13D). The leg therefore
reads the **same submissions JSON the Form 4 / 8-K legs already fetch** — zero extra enumeration
fetches; identity costs one bounded, immutable-cached document fetch per filing.

**The rename trap (#9):** EDGAR renamed the form type at the ~2024-12 structured-XML cutover:
`SC 13D` / `SC 13D/A` / `SC 13G` / `SC 13G/A` (classic) → `SCHEDULE 13D` / `SCHEDULE 13D/A` /
`SCHEDULE 13G` / `SCHEDULE 13G/A` (structured). The ingest matches **all eight strings**
(`submissions.SCHEDULE13_FORMS`); an SC-only match silently drops every 2025+ filing.

## The tape (evidence layer)

`fact_activist_stake` (migration 0039) — one row per (SUBJECT security, filing), append-only,
natural key `UNIQUE (tenant_id, security_id, accession, recorded_at)` (security_id from birth — the
0037 lesson): `form` · `filer_cik`/`filer_name` (the activist — the 0024 capture pattern; parsed
from the structured `primary_doc.xml` post-2024-12, from the SGML `FILED BY` header for classic-era
13D-family, NULL-by-design for classic-era 13G, which can never fire) · `pct_owned` (structured
cover `percentOfClass`; nullable, evidence-only) · `accession` · `filed` · `source_ref` (the
filing-index URL, #6). **Every 13D/G-family filing is stored** — 13Gs and amendments included
(#9): the deferred refinements ride this same tape with no re-ingest. Identity failures store the
row with NULL filer + a loud counter — never a drop. Append-if-changed: identity resolving on a
retry appends a new VERSION (never an UPDATE).

**Knowability (gold-doc trap #4):** `valid_from = filed`. The stake crossing inside the filing
predates dissemination by up to 10 days, and the structured cover's `dateOfEvent` /
`date5PercentOwnership` are deliberately never read for time. A fixed-timestamp fixture pins it.

## The fire policy (config-on-read; forks operator-confirmed 2026-08-18)

- **13D-family ORIGINALS fire** — the most recent live original anchors, CORE fixed in the detector
  (the R6/R9 structural-grade precedent: "core = a rare, deliberate capital commitment" — the
  grade-philosophy line item 1.01 failed). Score `activist_13d_score` (0.9 — `_CORE_SCORE`
  parity), liveness `activist_13d_liveness_days` (180d — the literature's months; insider-CORE
  parity; ≥ the 90d hold threshold → hold-and-build). Both `[PROPOSED]`, lab-finalized.
- **Fork 3 — 13G fires NOTHING.** Passive crossings are mostly index-fund plumbing: measured ~2
  13G originals/yr/name vs one 13D per six years. Firing 13G would re-land the S3 `1.01` flood
  (routine paperwork warming names). The rows stay on the tape for the deferred 13G→13D switch —
  the loudest version of this signal — which needs per-(filer, subject) history.
- **Fork 4 — amendments NEVER re-anchor a fire.** A 13D/A is direction-blind: an increase, a
  sell-down, and an exit all file identically. The live proof: CMPS's latest 13D/A (2026-02-17,
  AtaiBeckley Inc.) reports **4.96% — a sell-down below 5%**; latest-wins-including-/A would fire
  a fresh CORE on an exit filing today. Amendments inside the fired window ride the PROVENANCE so
  the operator sees the whole episode. Honest cost, documented both ways: v1 neither re-fires on
  an increase-/A nor kills on an exit-/A — the %-owned refinement (deferred, same tape) recovers
  both.

## The data-quality screen (measured 2026-08-19 — the sig-lab pass; it made the 2026-08-20 flip safe)

The sig-lab off-vs-on pass (asof 2026-08-19, prod, via the production read path
`call_for_thesis(record=False)`) confirmed the detector does **not** flood — 36 warm fires → only 8
arms, ≤3 per thesis, 0 state/verdict flips — but surfaced a **data defect**: ~9% of the live
13D-family *originals* are **mis-attributed** — the ingest fanned a filing onto the **wrong SUBJECT**
security. Two self-evidently-wrong shapes, both confirmed on real rows:

- **self-filed** — `filer_cik` == the SUBJECT's own cik: a basket company recorded as its OWN 13D
  subject. Measured: **UEC** `SCHEDULE 13D` `0001437749-26-024641`, filer == subject `0001334933`,
  7.7%. A 13D is filed ABOUT a company by an OUTSIDE party — never by the subject on itself.
- **sub-5% ownership** — `pct_owned` PRESENT and `< 5.0`: statutorily impossible for a 13D (the form
  is *required* only above 5%). Measured: **GameStop**'s `SCHEDULE 13D` `0001193125-26-202465`
  (filer `0001326380`) fanned onto BOTH **GME** (self, 0.01%) and **EBAY** (0.01%).

`signals/activist_stake._is_misattributed` screens these out of the **firing set** before an original
can anchor a fire (`detect` resolves the subject's own cik via `pit.security_cik`, the `security_name`
identity-read sibling; the pure `score` takes it as `subject_cik`). **NULL-safe / recall-sacred (#9):**
a NULL/missing `filer_cik` or `pct_owned` is KEPT and still fires — unparsed ≠ invalid; only a PRESENT
contradiction screens. The form-type fire policy (#3) is unchanged. This is a **firing-side** clean-up
so a later switch-flip is safe; it does **not** touch the tape (every row stays stored, #9). The
threshold is strictly `< 5.0`, so a 13D reporting EXACTLY 5% (the real LRHC 2026-06-23) still fires.

**The ingest root-cause — now fixed at the source.** WHY a filing was fanned onto a wrong subject:
EDGAR indexes an ownership schedule under BOTH the filer's and the subject's submissions feed, so the
per-SUBJECT enumeration (`schedule13_filings` over a member's own feed) also picks up the schedules the
member FILED **about other companies** — its OUTBOUND stakes. The fix reads each 13D-family filing's
TRUE subject — the structured cover's `<issuerCIK>` (free; the cover is already fetched for identity)
or the classic SGML `SUBJECT COMPANY` block — and **skips** the filing when that subject is not the
feed's owner (the owner is the filer, not the subject; the row lands under the true subject's own feed).
VERIFIED on the two cited instances: UEC's `0001437749-26-024641` (subject Uranium Royalty `0002143673`)
and GameStop's `0001193125-26-202465` (subject eBay `0001065088`) are both dropped under the filer's
feed and kept under the subject's. Scope is **13D-family only** (the fire case; 13G fires nothing and
its classic era fetches no header — verifying it would cost ~14k header pulls for no fire impact; the
switch's own `filer≠subject` guard tolerates any residual 13G mis-fan). An UNRESOLVED subject keeps the
row (recall-safe #9). The firing-side `_is_misattributed` screen stays as belt-and-suspenders for any
pre-fix rows still on the tape; a one-time repair (`pipeline/repair_activist_misattribution.py`) deletes
the already-stored self-filed rows.

## The golden (real, cited)

atai × COMPASS Pathways (CMPS, an answer-key basket member): the ORIGINAL **SC 13D accession
`0001193125-21-171001`** (filed 2021-05-24, FILED BY ATAI Life Sciences B.V., CIK 0001840904 —
SGML header committed as a fixture) fires CORE inside its window; the real structured-era
**SCHEDULE 13D/A `0001140361-26-005810`** (filed 2026-02-17, AtaiBeckley Inc., CIK 0002081043,
4.96% — raw XML committed as a fixture) is the fork-4 must-not-fire instance. MindMed's all-13G
tape is the fires-nothing negative. NRXP × Javitt (SC 13D `0000950142-21-001848`) is the second
real subject on the discovery path.

## The 13G→13D switch (BUILT — the loudest version)

A fired 13D whose filer held a PRIOR same-filer **13G** on this subject's tape is a passive holder
going activist — a stronger tell. `signals/activist_stake._switch_from_13g` finds the EARLIEST prior
same-filer 13G-family filing (when they first disclosed a passive stake) and ENRICHES the fired CORE:
the label gains `— ESCALATED from a prior 13G passive stake filed <date>` and the 13G rides ahead of
the episode in the provenance (#6). **Enrichment only** — the fire, grade (CORE), and score are
untouched, so it can never flood or re-grade. Guards: same filer (both CIKs resolved — an unresolved
filer never asserts a match, #9), the `_is_misattributed` screen (a mis-fanned 13G can't fabricate a
false switch), and a **minimum-gap dial** (`CallConfig.activist_switch_min_gap_days`, [PROPOSED] 30d):
a 13G and 13D filed ~a day apart is a RE-CLASSIFICATION, not an escalation. Measured, cited real
instances: **Gemini** — Winklevoss Capital Fund's `SCHEDULE 13G` `0001104659-25-112696` (2025-11-14)
→ `SCHEDULE 13D` `0001193125-26-229103` (2026-05-18, 65.1%), ~185 days = a switch; **QNTM** — Malone
Wealth Ventures' 13G `0002072045-25-000001` (2025-06-11) → 13D `0002072045-25-000002` (2025-06-12),
one day = NOT a switch. Known v1 bound — the **affiliate edge**: a control-person vehicle (Winklevoss
→ the Gemini exchange) passes `filer≠subject` and reads as a switch though it is a governance
reshuffle; v1 does not distinguish a same-party 13G→13D from an outside activist's escalation.

## Deferred (named; all ride the existing tape, no re-ingest)

- **%-owned refinements** — percent rising across amendments as re-affirmation; percent < 5 on an
  /A as stake-death; the Item-4 purpose text as a located passage (#6, the S3 descope pattern).
- **Group-filing capture** — v1 stores the LEAD reporting person only; the provenance URL shows
  the rest.
- **Identity backfill** for old-era 13G rows (NULL filer by design today).

## Shipping posture

Inert-first, now COMPLETE (the S1/S3/S4 precedent): merged + deployed with the switch OFF → ran the
sig lab (`python -m replay.run … --activist-stake`, or `ALPHADECK_ACTIVIST_STAKE=1`) → shipped the
data-quality screen → the operator ratified on the 2026-08-20 clean re-measure → the one-line config
flip (`activist_stake_enabled` False → True). The pure `score()` is fully tested ungated; the
standing-guard test runs the whole pipeline with the switch forced on, so the flip could not
surface a protocol-incomplete test double (the corporate_risk-flip lesson, made structural).
