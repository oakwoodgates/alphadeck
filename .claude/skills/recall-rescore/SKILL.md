---
name: recall-rescore
description: >-
  Run INVARIANT #9's recall gate — a LIVE discovery run against the psychedelic
  thesis, scored at CIK level against the committed answer key (hard bar 30/30
  on ANSWER, plus a report of whether the PRTG and ATAI canaries surfaced, any
  status). REQUIRED after any change touching discovery,
  classify, filters, caps, or term tiers; also trigger on "re-score recall",
  "run the answer key". Runs on DEV, never prod; a silent drop is a system
  failure, never waved through.
---

# Recall re-score — prove no name was silently dropped

Authoritative sources: `docs/INVARIANTS.md` §9 + `docs/DISCOVERY.md`. The ground
truth is the committed fixture `backend/tests/fixtures/recall_answer_key.py` —
READ ITS HEADER before scoring; it carries the procedure, the current result, and
the operator rulings. This is NOT an automated pytest — it is a live procedure,
and this skill is its executable home.

## Guardrails (never violate)
- **Full recall, never trimmed.** Never cap, sample, or shorten the run for
  latency/cost — a capped run cannot prove #9. Fail visibly instead.
- **Never edit the fixture to make a score pass.** It changes only when the
  ground truth genuinely changes (delist, rename, a real omission) — an operator
  decision, on the record in its header.
- **A degraded run is not a score.** If the draft's coverage report shows failed
  terms/pages or `capped_terms`, retry to a clean run before scoring — a gap
  scored as truth is exactly the silent-drop failure #9 exists to catch.
- **Any miss is investigated, never waved through.** "The dropped name is probably
  noise" is the exact wording that preceded all five historical violations.

## Steps
1. **Run on DEV** (the `dev-stack` skill; live EFTS needs the UA, the tail-sweep
   needs `ANTHROPIC_API_KEY` — without it PRTG-class surfaced-only names can't
   surface, so the run under-counts). Find the psychedelic thesis id on dev.
2. **Trigger the draft** (a background job — 202, then poll):
   ```
   curl -X POST http://localhost:8001/workbench/theses/<id>/draft-chain
   ```
   Collect the finished `ChainDraftOut`: PLACED + VERIFY placements, the
   surfaced-not-placed context, and the coverage report.
3. **Score at CIK level** — write a scratch scorer (scratchpad, not the repo)
   importing `SEEDS`/`ANSWER` from the fixture. A company counts recalled if ANY
   ticker in its group matches ANY ticker of a surfaced CIK (a CIK carries several
   ticker rows — common + warrants; ticker-string equality miscounts). The
   delisted canaries (`SURFACED_ONLY_CANARY`: PRTG, ATAI) are not counted —
   check and report whether each surfaced (any status), don't assume.
4. **Verdict:** recall holds at the fixture's current bar (30/30 on ANSWER, plus
   the PRTG / ATAI canary report). Report the MEASURED score, the coverage report, and — on any miss —
   which company, and the investigation, before any change lands.
