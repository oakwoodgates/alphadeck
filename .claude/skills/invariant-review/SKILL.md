---
name: invariant-review
description: >-
  Use when reviewing a diff against Alpha Deck's own rules (invariants 1 to 14, the Workbench interaction
  principles, the cost rule), or before merging changes to signals, calls, discovery, tenancy, the Workbench or
  `backend/llm`. Findings are proposals.
---

# Invariant review

The project-specific overlay a generic code review cannot do; it complements `/code-review` (bugs) and does not
replace it. Read the relevant sections of `docs/INVARIANTS.md` during the review rather than working from memory;
its "Former numbers" table decodes an old cite. The interaction principles WB#1 to WB#3 are in
`frontend/CLAUDE.md`.

## Guardrails

- Findings are proposals. Report VIOLATION, PASS or N/A with the file, the line and the invariant by number;
  propose the fix, never apply it unasked.
- Label each finding run (you executed something) or read (a judgment from the diff). Never blur them.
- A rule that seems to need breaking, or a call-logic rule `docs/CALL_LOGIC.md` does not specify, goes to the
  operator, not into the review's own fix.

## Steps

1. Read the diff and the invariants its areas own, then walk the checks below.
2. Report per invariant, then the mechanical gates, then the live-seam check when it applies.

## Checks

Trust invariants (backend and call path):

- 1 No lookahead: every historical read goes through the as-of accessors and counts a fact from when it became
  public (`db/public_clock.py`); writers stamp the database's `now()`, never a backdated time.
- 2 Everything attaches to a thesis: no orphan screener or tool.
- 3 The model never sources a number or a firing: a new model call goes through `backend/llm`, response only,
  with no write path and no value field in its schema.
- 4 Timing, not judgment: nothing ranks or vetoes a thesis; a user's conviction on a member is a displayed weight
  and never reaches the call.
- 5 Advisory only: nothing sizes, routes or executes a trade.
- 6 Explainability: every new trigger carries a checkable source.
- 7 Inverse loudness: only entering Armed or an armed upgrade is loud (`notify/policy.py`).
- 8 Dilution overhang: one raw computation, never backed out of severity.
- 9 Recall: any change to discovery, classification, filters, caps or term tiers triggers its five rules; the
  `recall-rescore` skill's re-score is the gate.
- 10 The model recommends, a person decides: nothing model-suggested auto-applies; authorship transfers only when
  a person edits.
- 11 Exact membership: fuzzy search may discover, never decide a mapping.
- 12 Two tenant scopes: a new table is named in `domain/scopes.py` (`SCOPE_OF` or `UNSCOPED_TABLES`); reads use
  the tenant-filtered accessors and the poison-row test grows to cover them; `WORKSPACE_ANY` stays out of `app/`
  and `dashboards/`; a dashboard cache key carries the workspace; nothing deletes from `calls` or the editions
  store.
- 13 Pure assembler: no clock, database or network in the call path. No bare `date.today()` or argument-less
  `datetime.now()`; the scans in `tests/domain/test_market_time.py` fail a violation.
- 14 Property factoring: behavior keys on the property that drives it, never `if kind == ...`.
- Display signals stay display: no import path into `calls/`, never persisted.

Workflow (frontend):

- Reversibility (WB#1): every new user action has a visible inverse.
- Hide, never vanish (WB#2): pruning grays and keeps the row; hiding is an explicit, reversible filter.
- Honest loudness (WB#3): a badge true of every row is noise; a control that does not discriminate does not
  render.
- Cost: no new ambient spend. A fetch a user triggers stays behind an explicit action, bounded by an allowance
  (`app/allowance.py`) and the workspace caps (`app/workspace_caps.py`); see `docs/WORKBENCH.md`, "Cost and
  limits".

Mechanical gates:

- A diff that touches the FastAPI schema carries the regenerated contract pair (`contract-sync`, same PR).
- Temporal facts append a new version, never `UPDATE` in place.
- Idempotency tests count the table, not the read.

## The live-seam check

When the diff touches `backend/llm/` or a seam's prompt or schema: fake-client tests prove wiring only. Confirm
against live Anthropic on dev, never prod (it needs `ANTHROPIC_API_KEY`), and report what the call returned. Use
`POST /workbench/theses/<id>/draft-chain` for the chain seam (a plain user's token for the thesis's workspace; it
spends a `chain_draft` allowance) or `POST /workbench/facts/explain` for the flag seam (a steward's token). Mint
the token and call the route as `recall-rescore` steps 2 and 3 do, keeping the token out of the transcript.

## Stop and ask when

- A change needs an invariant broken, or a call-logic rule that is not specified.
- The live check needs a key or a dev stack you do not have: report the confirmation as outstanding, never imply
  it happened.

## Model

[INVARIANTS].

[INVARIANTS]: ../../../docs/INVARIANTS.md
