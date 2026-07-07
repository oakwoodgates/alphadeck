# WORKBENCH_ENRICHMENT.md — the SURFACE identity layer

> Repo path: `docs/WORKBENCH_ENRICHMENT.md`. Part of the **SURFACE** stage (`STAGE_MODEL.md`): when a discovered
> name arrives, the system POPULATES its **machine-parsed identity** (sector / exchange / listing status / filer
> category) and a **derived archetype recommendation** — so a discovered name shows up already characterized, for
> the operator to confirm. Companion to `WORKBENCH_EXTRACTION.md` (the *scoring-fact* side of SURFACE) and
> `DISCOVERY.md` (the DISCOVER stage that feeds it). Engine: `backend/ingest/edgar/submissions.py:parse_identity`,
> `backend/workbench/enrichment.py`, `backend/securities/master.py`; carried on
> `backend/workbench/chain_draft.py:ResolvedPlacement`.
>
> **Status: BUILT** — identity columns + parser (#105), lazy enrich + listing-status gate (#106), identity badges
> + market cap on the FE (#107), the derived archetype recommendation (#108), the filer-category chip (#118).

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

## How it flows — lazy, just-in-time, fail-visible

The enrichment runs on the **draft path, BEFORE resolution** (`execute_draft`: discovery → **ENRICH** → resolve),
so the reconciler's status-gate reads a fresh listing status. `enrich_for_ciks` (`workbench/enrichment.py`) fetches
each discovered CIK's submissions, parses identity, and writes only the master's descriptive columns via
`master.enrich` (an UPDATE-in-place — identity is **mutable metadata, not append-only**; `INVARIANTS.md` / the
security master is canonical). It is **per-CIK isolated + FAIL-VISIBLE** (#9): a fetch/parse/write fault logs and
skips that name (its row stays un-enriched → abstains), **never** aborting the draft. Only a **genuine** submissions
doc enriches (the response must echo a top-level `cik`) — so a bad fetch can never harden into a false `inactive`.
The identity then rides onto every `ResolvedPlacement` (`_enrich_placements` in `chain_draft.py`), carried by
`security_id` onto the FE placed row as quiet chips. Migrations: `0013_master_identity`, `0016_master_category`.

## The listing-status gate — a frictionless rescue, never a verdict (#9)

A PLACED name whose master row reads `"inactive"` is **DOWNGRADED to AMBIGUOUS** — never auto-placed — with its own
row as the single pick. The operator sees a **hedged flag** ("no current listing found in EDGAR — a guess, not a
delisting; place it anyway if it's real") and a one-click "place anyway…". So a false-inactive (a recent IPO not
yet in the snapshot) costs **one extra click, never a silent drop** (#9); an un-enriched row keeps
`listing_status=None` — no flag, no gate. This is the allowlist discipline of #2 applied to listing presence: a
guess surfaces for the operator to ratify, it never decides.

## The derived archetype — a #10 recommendation the operator confirms

The blanket `high_beta` default is **gone entirely** (item F, the three-gate round): a placed-but-not-finalized
member carries **`archetype = NULL`** — placement never characterizes, and save never coerces a default (nullable
end-to-end since migration `0018`). What replaces it is a **deterministic** archetype recommendation computed from
the name's **market cap + purity** (not the LLM — a pure derivation), surfaced as **`archetype_hint`** on
`ScoredMemberOut`, distinct from the placed `archetype` the operator owns:

- **Abstention is a feature.** When cap or purity is missing, or the signal is ambiguous, the derivation **declines
  to recommend** (no hint) rather than guessing — the honest fallback, matching the enrichment discipline.
- **It RECOMMENDS, the operator DECIDES (#10) — on the rail, the archetype's SINGLE home.** The hint rides
  display-only; the operator applies it (or picks manually from the rail's set control — the same decision point;
  relational calls like `shovel`/`fund` the rule never guesses) and either act stamps
  `authored_by → operator_edited` — so a confirmed recommendation is operator-authored and stable across a
  re-roll. Nothing auto-applies, and no other surface (placement rows, AddName, the editor) offers the field —
  one decision point, one moment, one provenance.
- **It never feeds the call.** Archetype is basket-member role metadata; the back-half grade/size still flow from
  the signals (`INVARIANTS.md` #7 — never an `if kind ==` branch).

## Invariant fit

- **#1 / #3** — identity + category are descriptive strings, never numbers, never a fact row, never on the call
  path. The archetype derivation is deterministic (cap+purity), not model-sourced.
- **#2** — identity is display-only on the placement, **never promoted onto `BasketMember`**; the listing-status
  gate is the exact-membership allowlist applied to listing presence (a guess surfaces, never decides).
- **#9** — every gate/abstention is VISIBLE + reversible (the hedged "not listed" pick, the declined-to-recommend
  archetype); a bad fetch abstains, never hardens into a false verdict.
- **#10** — the archetype hint is a pending recommendation; the operator's confirm is what acts.
