# WORKBENCH_ENRICHMENT.md — the SURFACE identity layer

> Repo path: `docs/WORKBENCH_ENRICHMENT.md`. Part of the **SURFACE** stage (`STAGE_MODEL.md`): when a discovered
> name arrives, the system POPULATES its **machine-parsed identity** (sector / exchange / listing status / filer
> category) and the **derived business type** — so a discovered name shows up already characterized, for
> the operator to confirm. Companion to `WORKBENCH_EXTRACTION.md` (the *scoring-fact* side of SURFACE) and
> `DISCOVERY.md` (the DISCOVER stage that feeds it). Engine: `backend/ingest/edgar/submissions.py:parse_identity`,
> `backend/workbench/enrichment.py`, `backend/securities/master.py`; carried on
> `backend/workbench/chain_draft.py:ResolvedPlacement`.
>
> **Status: BUILT** — identity columns + parser (#105), lazy enrich + listing-status gate (#106), identity badges
> + market cap on the FE (#107), the retired archetype recommendation (#108; replaced by Business-Type M1), the filer-category chip (#118),
> the identity LIFECYCLE (the standalone `pipeline.enrich_identity` command + the scored-join read with derived
> `origin`).

---

## What it is — machine-parsed IDENTITY, never a fact

Discovery finds a name by CIK. Before that name reaches the operator, the enrichment layer fills its **descriptive
identity** from the name's EDGAR submissions JSON. This is **not extraction and not a fact**: identity strings
(sector, exchange, listing status, filer category) never enter a `fact_*` table, never feed a number on a call
card, and are **never promoted onto a `BasketMember`** (#2) — they ride **display-only** on the placement, exactly
like `matched_terms` / `discovery_source`. `#1/#3` govern *numbers*; identity is descriptive metadata.

**Why it exists:** without it, every discovered name defaulted to a blank identity and a blanket `high_beta`
archetype — "high-beta on everything." Enrichment makes a discovered name arrive *characterized*, so TRIAGE is a
judgment over a populated row, not data entry.

## The fields — parsed from the submissions JSON

`parse_identity(submissions)` (pure, no I/O — `ingest/edgar/submissions.py`) reads:

- **`sector`** — `sicDescription` (the SEC SIC industry description).
- **`exchange`** — the first of `exchanges` — a **COMPANY-level** value, so `master.enrich` only **fills a
  NULL, never overwrites**: the populate path writes the SEC table's PER-INSTRUMENT venue (authoritative —
  ASML=Nasdaq vs ASMLF=OTC), and the company-level overwrite is how the ASMLF foreign ordinary once got
  stamped "Nasdaq" (the canonical-primary slice killed that class of wrong-tradeable-attribute).
- **`status`** — a **listing-presence HEURISTIC**, never a delisting feed: a current ticker AND a current exchange
  → `"active"`, else `"inactive"` ("no current listing found in EDGAR"). It **must never** be surfaced as a hard
  "delisted" verdict — the operator-facing label stays a hedged guess.
- **`category`** — the SEC filer category (e.g. "Large accelerated filer" / "Smaller reporting company"), a rough
  **maturity / size tell**. EDGAR joins multiple category attributes with a literal `<br>`; the parser strips HTML
  tags to a clean `·`-joined string, so **no raw markup reaches the chip** (e.g. `Non-accelerated filer · Smaller
  reporting company`). Presented as **IDENTITY** — it sits next to sector/exchange, **NOT** near the archetype (it
  is a filing-status fact, not a competing classification).
- **`former_names`** — parsed, **unused** (`master.enrich` doesn't persist it). Its planned consumer — the
  identity-bridge slice — was **DROPPED** (operator decision, 2026-07-06: renames are already handled by
  CIK-keying, the ATAI dual-CIK redomicile surfaces live, and a merge would be subtle-bug-prone for cosmetic
  value; the record is in `DISCOVERY.md`). Kept parsed: it is cheap, tested, and the natural data shape if a
  real false-ABSENT ever motivates revisiting.

All optional — an un-enriched row reads `None` (the honest fallback: no chip, no gate).

## How it flows — the identity LIFECYCLE (two writers, one read)

**Write, draft-time (lazy, just-in-time).** The enrichment runs on the **draft path, BEFORE resolution**
(`execute_draft`: discovery → **ENRICH** → resolve), so the reconciler's status-gate reads a fresh listing status.
`enrich_for_ciks` (`workbench/enrichment.py`) fetches each discovered CIK's submissions, parses identity, and
writes only the master's descriptive columns via `master.enrich` (an UPDATE-in-place — identity is **mutable
metadata, not append-only**; `INVARIANTS.md` / the security master is canonical). It is **per-CIK isolated +
FAIL-VISIBLE** (#9): a fetch/parse/write fault logs and skips that name (its row stays un-enriched → abstains),
**never** aborting the draft. Only a **genuine** submissions doc enriches (the response must echo a top-level
`cik`) — so a bad fetch can never harden into a false `inactive`. The identity then rides onto every
`ResolvedPlacement` (`_carry_identity_and_gate` in `chain_draft.py`), carried by `security_id` onto the FE placed
row as quiet chips. Migrations: `0013_master_identity`, `0016_master_category`, `0028_master_origin`.

**Write, on demand (the standing backfill).** `python -m pipeline.enrich_identity` (bare = `--baskets`; also
`--thesis <id>` and `--universe`; `--live` opt-in, cache-first default) resolves a scope to per-tenant
`{cik: security_id}` maps and runs the SAME `enrich_for_ciks` over them — full re-enrich every run (no
`--missing-only`: a per-field "missing" predicate rots each time a field is added), a per-tenant receipt, and a
non-zero exit when a `--live` run enriched nothing while skipping names. Because `master.enrich` writes **every**
submissions-sourced column each run, adding a field to `parse_identity` + the `enrich` UPDATE means the backfill
is just a re-run — no bespoke script. The daily cron stays OUT of identity (operator decision: blast radius on
the call-of-record path, ~static data, and a receipt-producing command beats an ambient side effect).

**Read (the scored join).** `master.identity_for` joins name / sector / exchange / category **+ a derived
`origin`** onto every scored member (`ScoredMemberOut.origin`; the raw 0028 locator ingredients stay OFF the
wire — `resolve_origin` derives the display string on read, the same discipline as the draft path). The
Workbench editor reads the join as its identity **baseline** (`idFor` in `ChainEditor.tsx`): the live join WINS
over the draft/session map entry (it self-heals after a backfill), the map covering only what the join doesn't
(a just-drafted, unsaved member). So chips + Country/Exchange filters work on a saved thesis opened with **no
draft and no session**. Cockpit's NamePanel and the Scoreboard drawer show the same wire field as an Origin cell;
the Board stays identity-free.

## The listing-status gate — a frictionless rescue, never a verdict (#9)

A PLACED name whose master row reads `"inactive"` is **DOWNGRADED to AMBIGUOUS** — never auto-placed — with its own
row as the single pick. The operator sees a **hedged flag** ("no current listing found in EDGAR — a guess, not a
delisting; place it anyway if it's real") and a one-click "place anyway…". So a false-inactive (a recent IPO not
yet in the snapshot) costs **one extra click, never a silent drop** (#9); an un-enriched row keeps
`listing_status=None` — no flag, no gate. This is the allowlist discipline of #2 applied to listing presence: a
guess surfaces for the operator to ratify, it never decides.

## The business type — the two-level derived characterization (RETIRED: the archetype hint)

> **Business-Type M1 (2026-08):** the size-derived archetype recommendation (`_archetype_hint`,
> `archetype`/`archetype_hint` on the wire, the `basket_member.archetype` column) is **retired** — measured
> live, 486/494 members never carried a value; the operator organizes by what a name DOES, not its size
> tier. What replaced it: the **two-level business type** — a `BusinessType` leaf + `BusinessSupersector`
> rollup + the royalty/streaming name-overlay — derived on read from the stored `sector` via the
> **operator-editable maps in `backend/securities/business_type/`** (see that folder's README). It rides
> `ScoredMemberOut` (`business_type` / `business_supersector` / `royalty` / `business_type_override` /
> `instrument_kind`) like `sector`/`origin`: auto-enriched identity with a basis, never a fact.
>
> The #10 seam moved with it: the maps RECOMMEND (the derived leaf shows immediately, marked "from SIC");
> the operator can **re-tag** one security on the rail (`POST /workbench/securities/{id}/business-type`,
> master-level, store-on-diff — an agreeing pick stores nothing) and a standing re-tag reads
> **"your tag · revert"** (the visible inverse, WB #1). The old `fund` archetype's information lives on
> `instrument_kind='etf'`; `adjacent` (off-thesis) lives in the purity meter's off-thesis read.

## Invariant fit

- **#1 / #3** — identity + category are descriptive strings, never numbers, never a fact row, never on the call
  path. The business-type derivation is deterministic (the SIC maps + a name regex), not model-sourced.
- **#2** — identity is display-only on the placement, **never promoted onto `BasketMember`**; the listing-status
  gate is the exact-membership allowlist applied to listing presence (a guess surfaces, never decides).
- **#9** — every gate/abstention is VISIBLE + reversible (the hedged "not listed" pick; an unmapped sector reads
  a visible `other`, an un-enriched one an honest unclassified); a bad fetch abstains, never hardens into a false verdict.
- **#10** — the maps recommend the classification; the operator's re-tag (or their leaving it derived) is what stands.
