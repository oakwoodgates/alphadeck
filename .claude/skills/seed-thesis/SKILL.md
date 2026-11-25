---
name: seed-thesis
description: >-
  Use when seeding a thesis card on the dev stack from a spec: create it, set its term set, run the chain draft
  and grade the result through the app's routes with a dev token. Prod needs the operator's go.
---

# Seed a thesis on dev

## Guardrails

- Dev (`:8001`) unless the operator says prod (`:8000`). Creating a card is a write, and a draft spends model calls.
- Every route except `/health` needs a token: send `-H "Authorization: Bearer $TOKEN"` on every call. The card is
  created in the token user's workspace, taken from the token and never from the body; another workspace's card
  answers 404.
- Mint the token inside the dev backend container, never on the host (the host's default database URL is prod), and
  keep it out of the transcript.
- A plain user's calls spend daily allowances: `term_run` (produce terms, recommend tiers), `chain_draft` (each
  draft) and `price_pull` (each new company a save loads at once). A steward is not counted, and a steward's draft
  also writes identity to the shared master.
- Workspace caps (live cards, companies, terms per card) refuse a save or term edit that would rise past them: 409,
  nothing written.
- Never seed a floodgate term as SIGNAL: a bare generic word (`bitcoin`, `crypto`), a two-to-four-character ticker or
  unit, any term near EFTS's result ceiling. A SIGNAL hit places a company on its own; collision-prone terms are BROAD.
- Recall over speed (invariant 9): never trim terms or caps to save cost. Caps are per tier and a capped term is
  flagged, never silently cut.
- Run stack commands from the main checkout, never a worktree. Copying a card to prod has no skill: stop and ask.

## Steps

1. Mint the token (`python -m pipeline.users list` in the same container names the users):

   ```bash
   TOKEN=$(docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev exec -T backend python -m pipeline.users token create --email <dev user> --name seed --write | sed -n 's/^token (shown once): //p' | tr -d '\r')
   ```

2. Create the card with `POST /workbench/theses`, body
   `{"id": null, "name": "...", "narrative": "...", "ticker": null, "basket": [], "segments": []}`, and keep the
   returned `id`. The save never carries the term set, catalysts, kill criteria or exclusions; each has its own route.

   ```bash
   curl -s -X POST http://localhost:8001/workbench/theses -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @thesis.json
   ```

3. Set the term set with `PUT /workbench/theses/{id}/terms/edit`, body
   `{"terms": [{"term": "...", "tier": "signal"}, {"term": "...", "tier": "broad"}]}` (no model call, no allowance).
   To add keyword proposals on top, `POST /workbench/theses/{id}/terms` with `{"seeds": [...]}` (spends `term_run`).
   Read it back with `GET /theses/{id}` and confirm the count and the SIGNAL/BROAD split.

4. Optional lists: `PUT /theses/{id}/catalysts`, `PUT /theses/{id}/kill-criteria`, `PUT /theses/{id}/exclusions`.
5. Start the draft with `POST /workbench/theses/{id}/draft-chain` (no body for a full draft, or
   `{"scope": "seeds_only"}`). It answers 202 with a `job_id`. Poll
   `GET /workbench/theses/{id}/draft-chain/jobs/{job_id}` every few seconds until `done` or `failed`; a full draft
   takes minutes. The draft saves no basket; the completed run is written under `data/draft_runs/<id>/`.

   ```bash
   curl -s -X POST http://localhost:8001/workbench/theses/$ID/draft-chain -H "Authorization: Bearer $TOKEN"
   curl -s http://localhost:8001/workbench/theses/$ID/draft-chain/jobs/$JOB -H "Authorization: Bearer $TOKEN"
   ```

6. Grade the run (see Checks). The user prunes and saves in the Workbench.

## Checks

- Counts: PLACED, VERIFY, AMBIGUOUS and ABSENT, and the segments built. No segments and every name in Discovered on
  a large universe means the organizer hit its token ceiling.
- The report: coverage, `failed_terms`, `capped_terms`, `empty_terms`, the tail-sweep state and the narration fill.
  Re-run a degraded draft before trusting it: a gap graded as truth is a silent drop.
- Recall floor: did the expected core companies place? Tail: names beyond your key. Flood: funds and other
  exchange-traded products should be a minority.
- With a committed answer key, run the `recall-rescore` skill.
- No drafted sentence states a number.
- `GET /workbench/theses/{id}` is not a route (404); read the card with `GET /theses/{id}`.

## Stop and ask when

- The target is prod, or the token's workspace is not the one you expected.
- A call answers 429 (an allowance or the draft capacity) or 409 (a workspace cap or a running draft); do not loop.
- The draft fails with "discovery unavailable" twice, or failed pages remain after a re-run.
- A SIGNAL term reaches its cap.
- You want to raise a discovery cap or a model dial (`llm_decompose_max_tokens`, `draft_job_running_ttl_s`). Compose
  does not pass the model dials, and a bigger universe needs the organizer ceiling and the job TTL raised with it.

## Model

[docs/DISCOVERY.md](../../../docs/DISCOVERY.md): the term set, the draft pipeline and its report. What a user does
with the draft: [docs/WORKBENCH.md](../../../docs/WORKBENCH.md).
