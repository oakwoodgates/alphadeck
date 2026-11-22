---
name: recall-rescore
description: >-
  Use when a change touches discovery, classification, filters, caps or term tiers, or when asked to re-score
  recall or run the answer key. Runs a live draft on dev for the psychedelic thesis and scores it at CIK level.
---

# Recall re-score

Invariant 9 forbids a silently dropped name. This is its gate: a live discovery draft scored against the committed
answer key, `backend/tests/fixtures/recall_answer_key.py`. Read the fixture's header first: it carries the
procedure, the current bar and the operator's rulings. This is a live procedure, not a pytest.

## Guardrails

- Dev only, never prod. The draft spends the model and EDGAR budget, and a steward's draft writes company identity
  onto the shared master.
- Full recall, never trimmed. Never cap, sample or shorten the run for latency or cost: a capped run cannot prove
  invariant 9. Fail visibly instead.
- Never edit the fixture to make a score pass. It changes only when the ground truth does (a delisting, a rename,
  a real omission), an operator ruling recorded in its header.
- A degraded run is not a score. If the draft's `report` shows `coverage.failed_terms`, `coverage.pages_ok` below
  `pages_attempted`, any `capped_terms`, or a `scope` other than `full`, retry until it is clean. A gap scored as
  truth is the silent-drop failure the gate exists to catch.
- A miss is investigated, never waved through. "The dropped name is probably noise" is how past recall losses
  passed their own tests.

## Steps

1. Bring up dev (the `dev-stack` skill) with `ALPHADECK_USER_AGENT` (live EFTS needs it) and `ANTHROPIC_API_KEY`
   (without it the model steps fail open and the run under-counts) in `.env.dev`. Find the psychedelic thesis id
   in its owner's workspace.
2. Mint `$TOKEN` for the user whose workspace holds that thesis (another workspace's thesis answers 404) and keep
   it out of the transcript. Run the mint in the dev backend container: on the host the default database URL is
   prod.
   ```
   TOKEN=$(docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev exec -T backend python -m pipeline.users token create --email <owner> --name rescore | sed -n 's/^token (shown once): //p' | tr -d '\r')
   ```
3. Start the draft (a background job: 202, then poll):
   ```
   curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8001/workbench/theses/<id>/draft-chain
   ```
   A plain user's kick-off spends one daily `chain_draft` allowance (429 once spent, or while the global draft cap
   is full); a steward is never counted. Poll `GET .../draft-chain/jobs/<job_id>` with the same header until
   `status` is `done` (`failed` carries an `error`). The result is `ChainDraftOut`: PLACED and VERIFY placements,
   and `report`.
4. Score at CIK level with a scratch scorer outside the repo that imports `SEEDS` and `ANSWER` from the fixture. A
   company counts as recalled when any ticker in its group matches any ticker of a surfaced CIK: one CIK carries
   several ticker rows (common stock, warrants), so ticker-string equality miscounts.
5. `SURFACED_ONLY_CANARY` names are not counted. Check whether each appears anywhere on the draft, any status, and
   report it. A tail sweep that is not `ran` makes that report unreliable: say so.
6. Verdict: every `ANSWER` name surfaced (the fixture header names the bar). On a miss, name the company and
   the investigation before the change lands.

## Checks

- The report states the draft's `scope`, pages ok of attempted, `capped_terms`, `tail_sweep`, the count recalled
  of `ANSWER` and the canary result, all from the run.
- The diff leaves `tests/fixtures/recall_answer_key.py` alone unless the operator ruled the ground truth changed.

## Stop and ask when

- Dev is down, or it lacks the key or the User-Agent.
- A clean run cannot be had after retries, or any name in `ANSWER` is missing.
- The operator has not said the fixture's ground truth changed.

## Model

[INVARIANTS], 9 "Recall is sacred".

[INVARIANTS]: ../../../docs/INVARIANTS.md
