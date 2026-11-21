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

Authoritative sources: `docs/INVARIANTS.md` (invariants 1-14 in full — READ the
relevant sections during the review, don't work from memory of them; its "Former
numbers" table decodes an old cite) + `CLAUDE.md` (the same numbers in one line each,
interaction principles, cost thread). This complements /code-review (bugs); it does
not replace it.

## Guardrails (never violate)
- **Findings are proposals.** Report VIOLATION / PASS / N-A with file:line and the
  invariant by number; propose the fix, don't apply it unasked (propose-don't-assume).
- **Label MEASURED vs PROPOSED.** A claim you verified by running something is
  MEASURED; a judgment from reading is PROPOSED. Never blur them.

## Checks — trust invariants (backend / call path)
- **No lookahead (invariant 1):** every historical read via the as-of accessors, a
  fact counted from when it became public (`db/public_clock.py`); writers stamp
  `now()`, never a backdated time.
- **Everything attaches to a thesis (invariant 2):** no orphan screener or tool.
- **LLM never sources (invariant 3):** any new LLM touchpoint goes through
  `backend/llm`, response-only, no write rail, no value field in its schema.
- **Timing, not judgment (invariant 4):** nothing ranks or vetoes a thesis; the user's
  conviction on a member is a displayed weight and never touches the call machinery.
- **Advisory only (invariant 5):** nothing sizes, routes or executes a trade.
- **Provenance (invariant 6):** every new trigger carries a checkable source.
- **Inverse loudness (invariant 7):** only a move into Armed is loud.
- **Dilution overhang (invariant 8):** one raw computation, never backed out of severity.
- **Recall (invariant 9):** ANY change to discovery/classify/filters/caps/term tiers
  triggers the five rules of invariant 9 — require the `recall-rescore` skill's
  re-score as the gate.
- **Recommend→confirm (invariant 10):** nothing model-suggested auto-applies;
  authorship transfers only on an operator edit.
- **Exact-membership resolution (invariant 11):** fuzzy may discover, never decide.
- **Tenant isolation (invariant 12):** new read paths use the tenant-filtered
  accessors, and the poison-row test GROWS to cover them; nothing deletes from
  `calls` or the editions store.
- **Pure assembler (invariant 13) + property-factoring (invariant 14):** no
  clock/DB/network in the call path; no bare `date.today()` / no-arg `datetime.now()`
  (the AST scans in `tests/domain/test_market_time.py` enforce — new code must pass
  them); behavior keys on the driving property, never `if kind ==`.
- **Display signals stay display:** no import path into `calls/`, never persisted.

## Checks — frontend / workflow
- **Reversibility:** every new operator action has a visible inverse.
- **Hide, never vanish:** pruning grays and keeps the row; hiding is an explicit,
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
`curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8001/workbench/theses/<id>/draft-chain`
for the chain seam, or `POST /workbench/facts/explain` (steward-only) for the flag seam — and report
the live result as MEASURED. Every route except `/health` needs a sign-in: `$TOKEN` is a dev-stack
user's token for the thesis's workspace (a steward's for the explain route), minted in the dev backend
and kept out of the transcript:
```
TOKEN=$(docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev exec -T backend python -m pipeline.users token create --email <dev user> --name review | sed -n 's/^token (shown once): //p' | tr -d '\r')
```
No key available → say the live confirmation is OUTSTANDING; never imply it
happened.
