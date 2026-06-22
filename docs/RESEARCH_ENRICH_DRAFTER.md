# The research-and-enrich drafter

How the narrative→chain drafter (`docs/CHAIN_DRAFTER.md`) becomes a **research-and-enrich pipeline** — and
the invariant lines every piece is judged against. This is the design record; build status per slice is at the
bottom.

## Why

The drafter was a single **forced** Sonnet tool-call (`tool_choice` pinned to `draft_value_chain` — it
literally cannot search), proposing names from training recall. Three runs of the same psychedelic-therapy
thesis returned three different baskets (only two names survived all three; one run reached for NVAX, a
COVID-vaccine maker). That's recall, not research, and it can't be tuned away. Three failures compound it:

- a real listed name lands a **false absent** (MindMed → renamed Definium/DFTX, already in the master, but the
  model proposes the dead identity);
- the absent list is an opaque **"not in your universe"** wall — relevant-but-unplaceable names, zero facts,
  no reason;
- every drafted name shows **`high_beta`**, a non-answer wearing a classification's clothes.

The fix: recall → a **two-step research pass + a current-snapshot enrichment layer**, surfaced as
operator-actionable triage, with a derived archetype *suggestion*. The model proposes, **data decides**, the
operator confirms.

## The settled architecture (5 pieces)

1. **Research, not recall** — a web-search research pass finds the CURRENTLY-LISTED companies in the thesis
   space, then the structured decompose call runs *with those findings in context*. Fixes coverage, stability,
   currency (proposes Definium, not MindMed) and off-thesis drift (won't reach for NVAX — it isn't in "listed
   psychedelic-therapy companies").
2. **Rename bridge** — a BACKSTOP (mostly subsumed by Piece 1): EDGAR `formerNames` resolves an otherwise-absent
   dead identity to its current CIK. Build only if stragglers remain after Piece 1.
3. **Enrichment** — attach market cap, exchange, location, sector, and public/private/delisted status to every
   surfaced name. Placed names from sources we already own (EDGAR submissions + companyfacts × the price
   source); absent names from the research pass itself.
4. **Investability triage** — reframe the table-state zones into what the operator can DO: in-universe-and-
   scorable · investable-but-needs-resolving · relevant-but-not-investable-here. An empty segment becomes an
   insight, not an empty box.
5. **Suggested basket** — derive the archetype suggestion from the Piece-3 facts (market cap writes most of
   it), fall back to the model's categorical guess, render it as a marked SUGGESTION the operator confirms —
   never a classification, replacing the blanket `high_beta`.

## Settled decisions (do not relitigate)

- **Two-step research flow** (research pass → structured decompose call), not a single agentic call — for clean
  #3 isolation (the structured step is value-free by schema) and a guaranteed structured output.
- **Absent-name facts: STATUS + COARSE BANDS only** (private / foreign / delisted / exchange / sector; size as
  micro/small/mid/large), never a precise research-sourced number. Status facts (categories) are always fine.
  Placed-name facts are DATA-derived.
- **Enrichment timing: CURRENT-SNAPSHOT, labeled "as of today".** The draft is a present-tense build-my-basket
  action; the score stays strictly bitemporal (extract→ratify, untouched). The label is a HARD requirement.
- **Budget:** a multi-query research pass at ~$0.20–0.50/draft is approved (10×+ the prior $0.02–0.05).

## Open decisions — recommendations

- **D1 research mechanics.** A NEW `LLMClient.research()` (auto-tool web_search → free text) runs first; its
  synthesis threads into `decompose_narrative(..., research_context=...)`. #3 is **structural**: the decompose
  tool has no number field, so the chain is value-free regardless of the research text. Research failure
  **degrades to the recall-only decompose** (an enhancement, not a hard dependency).
- **D2 enrichment plumbing.** Option B, **read-time, no new table.** Placed: a `company_meta(cik)` parser over
  the submissions JSON we already fetch + a standalone current-snapshot market cap (live shares × close,
  labeled "today", **never written to fact tables** — the #1 split). Absent: status + size band from research.
- **D3 research model: Opus 4.8** (`claude-opus-4-8`); decompose stays Sonnet 4.6. Research-synthesis quality
  IS the leverage of Slice 1; the budget has 10× headroom; the draft is an infrequent, high-value action where
  the best model earns its cost; Anthropic pairs web_search with Opus. A Settings dial — a cost surprise is a
  one-env-var step down to Sonnet.
- **D4 triage taxonomy.** Cause = resolver `PlacementStatus` × enrichment status. Three zones (ready-to-score /
  needs-a-step / not-investable-here); empty segments become insights. Final copy is the operator's voice.
- **D5 archetype derivation.** Market cap writes the size-driven archetypes (leader / high-beta / lotto;
  thresholds the operator's call); the model + segment context write role-driven ones (shovel / adjacent /
  fund). A marked SUGGESTION with its basis, replacing the `high_beta` default.

## Invariant-fit

- **#3 (never a number) — STRUCTURAL, two points:** the chain/prose schema has no number field; absent-name
  enrichment offers only a size BAND enum + status categories (no market_cap field). Placed-name market cap is
  data-derived (our shares × our price), not model-sourced. **The load-bearing line.**
- **#1 (no-lookahead) — current-vs-as-of split:** enrichment is an ephemeral current-snapshot read, labeled
  "today", never written to a fact table; the scoring/call path stays strictly as-of from ratified facts. A
  current figure can't contaminate an as-of call because it never enters the spine.
- **#2 (exact membership decides):** research/enrichment WIDEN the net and add context; the resolver still
  decides PLACED by exact membership; triage labels never auto-place.
- **Response-only:** the draft writes nothing; promote is the only writer.

## The two-step seam (built)

`POST /workbench/theses/{id}/draft-chain` (`app/routers/workbench.py`) now runs two LLM steps:

1. `research_companies(research_llm, narrative)` (`llm/chain_decomposition.py`) — calls `LLMClient.research`
   (`llm/client.py`: `tool_choice=auto`, the server-side `web_search` tool) → a plain-text synthesis, or
   `None` on any trouble (fail-open).
2. `decompose_narrative(decompose_llm, narrative, research_context=<step 1>)` — the existing forced-tool
   structured call, with the research synthesis appended to the user message as **context** (the tool schema
   is unchanged — value-free).

Then `resolve_placements` (`workbench/chain_draft.py`, the 5a decider) places each name by exact membership, as
before. The two clients come from `get_research_client` (Opus dials) and `get_decompose_client` (Sonnet dials)
in `app/deps.py`; the dials live in `domain/settings.py` (`llm_research_*` operational dials + the
**code-coupled** `research_web_search_tool` version field — see its comment).

## The wire

Slice 1 is **wire-clean in shape** (the `ChainDraftOut` / `ResolvedPlacement` shape is unchanged). The only
schema change is the `draft_chain` operation **description** (the route docstring rewrite) — `openapi.json` +
`types.gen.ts` are regenerated in lockstep (the OpenAPI-contract rule). Slices 2–4 deliberately EXTEND
`ResolvedPlacement` (enrichment fields, a triage zone, a suggested archetype + basis); those regens are
reviewed, not byte-clean.

## Slices

- **Slice 1 — research discovery — BUILT.** The two-step research→decompose; `LLMClient.research`; the
  `llm_research_*` dials + the code-coupled web_search version field; `get_research_client`; the
  `chain_research.md` prompt; research-failure degrades to recall-only. The former-name bridge (Piece 2) is a
  MEASURED backstop — build only if stragglers remain.
- **Slice 2 — enrichment (Piece 3).** `company_meta(cik)` parser + a read-only current-snapshot enrichment
  module (market cap "as of today"; status/sector/exchange/location; absent-name size band). Option B, no
  table. Wire changes.
- **Slice 3 — triage + zone reframe (Piece 4).** The three investability zones + copy; empty-segment insights;
  ingest-to-place via `master.resolve`.
- **Slice 4 — suggested archetype (Piece 5).** Derive from market cap + model fallback; `suggested_archetype` +
  basis on the wire; the FE uses it instead of the `high_beta` default.

## Verification

Per slice, at gate-2: backend suite EXECUTED vs `alphadeck_test` (0 skipped), ruff + black, FE vitest + tsc +
build. **Slice 1** adds a LIVE gate-2 (needs `ANTHROPIC_API_KEY`): the same narrative run repeatedly
**converges** on a stable basket; **no number** appears in any prose (manual, #3); current identities
(Definium-class) **place**; off-thesis names (NVAX-in-psychedelics) **do not appear** — and the absent-straggler
count informs whether Piece 2 is needed. Fake-client unit tests cover the two-step wiring + the fail-open
fallback to recall-only.
