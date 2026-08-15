---
name: invariant-review
description: >-
  Review a diff against Alpha Deck's OWN rules — the trust invariants
  (docs/INVARIANTS.md), the Workbench interaction principles, and the cost thread —
  the project-specific overlay a generic code review can't do. Trigger on "review
  against the invariants", "invariant check", or before merging anything touching
  signals, calls, discovery, the Workbench, or backend/llm. Reports findings as
  proposals; it never auto-fixes.
---

# Invariant review — the Alpha Deck overlay

Authoritative sources: `docs/INVARIANTS.md` (implementation invariants — READ the
relevant sections during the review, don't work from memory of them) + `CLAUDE.md`
(product invariants #1–7, interaction principles, cost thread). This complements
/code-review (bugs); it does not replace it.

## Guardrails (never violate)
- **Findings are proposals.** Report VIOLATION / PASS / N-A with file:line and the
  invariant by number; propose the fix, don't apply it unasked (propose-don't-assume).
- **Label MEASURED vs PROPOSED.** A claim you verified by running something is
  MEASURED; a judgment from reading is PROPOSED. Never blur them.

## Checks — trust invariants (backend / call path)
- **No lookahead (#1 / INV §4):** every historical read via the as-of accessors; no
  bare `date.today()` / no-arg `datetime.now()` (the AST scans in
  `tests/domain/test_market_time.py` enforce — new code must pass them).
- **LLM never sources (#3 / INV §1):** any new LLM touchpoint goes through
  `backend/llm`, response-only, no write rail, no value field in its schema.
- **Exact-membership resolution (INV §2):** fuzzy may discover, never decide.
- **Provenance (#6 / INV §3):** every new trigger carries a checkable source.
- **Tenant isolation (INV §5):** new read paths use the tenant-filtered accessors,
  and the poison-row test GROWS to cover them.
- **Pure assembler + property-factoring (INV §6/§7):** no clock/DB/network in the
  call path; behavior keys on the driving property, never `if kind ==`.
- **The two convictions never cross (#4):** operator conviction (size weight) must
  never touch the call machinery.
- **Recall (#9):** ANY change to discovery/classify/filters/caps/term tiers triggers
  the five §9 rules — require the `recall-rescore` skill's re-score as the gate.
- **Recommend→confirm (#10):** nothing model-suggested auto-applies; authorship
  transfers only on an operator edit.
- **Display signals stay display:** no import path into `calls/`, never persisted.

## Checks — frontend / workflow
- **Reversibility:** every new operator action has a visible inverse.
- **Hide, never vanish:** pruning greys and keeps the row; hiding is an explicit,
  reversible filter, never a delete or a default.
- **Honest loudness:** a badge true of every row is noise; loudness marks the
  exception. A control that doesn't discriminate shouldn't render.
- **Cost is the operator's to spend:** no new ambient API/data spend; expensive
  operations stay behind an explicit per-name/per-section opt-in.

## Checks — mechanical gates
- **Contract:** FastAPI-schema-touching diff carries the regenerated pair
  (`contract-sync` skill, same PR).
- **Bitemporal writes:** insert-a-new-version, never UPDATE-in-place on temporal facts.
- **Idempotency tests count the table**, not the read.

## Conditional — the live-seam check (diff touches `backend/llm/*` or a seam's prompt/schema)
Fake-client tests prove WIRING only. Before claiming the seam works, confirm against
live Anthropic (needs `ANTHROPIC_API_KEY`; run on DEV, never prod) — e.g.
`curl -X POST http://localhost:8001/workbench/theses/<id>/draft-chain` for the chain
seam, or the explain endpoint for the flag seam — and report the live result as
MEASURED. No key available → say the live confirmation is OUTSTANDING; never imply it
happened.
