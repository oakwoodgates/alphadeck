---
name: seed-thesis
description: >-
  Seed a thesis on the DEV stack from a spec — create → term set → run the
  draft-chain → grade — by replaying through the app's own writers (the promote
  endpoint + the narrow repo writers + draft-chain). Trigger on "seed a thesis",
  "stand up a thesis", "seed <name> on dev", "run the draft for X". Writes to
  dev; a prod seed needs the operator's explicit go. Prod DB stays read-only
  except the app's own writes.
---

# Seed a thesis — spec → discovery draft (on DEV)

Authoritative depth: `docs/DISCOVERY.md` (EDGAR-first discovery + the term tiers),
`docs/CHAIN_DRAFTER.md` (the narrative→chain draft), `docs/WORKBENCH_EXTRACTION.md`.
This skill is the executable checklist — follow it top to bottom, STOP on any failed
precondition (report, don't guess). Migrating a seeded thesis dev→prod is a DIFFERENT
job — use the `migrate-thesis` skill (the same wipe-guard applies).

## Guardrails (never violate)
- **DEV, unless the operator says prod.** Creating a thesis is a WRITE and the draft
  spends API (the Opus tail-sweep) — a prod seed needs the operator's explicit go.
  The dev API is `:8001`; prod is `:8000`.
- **Never seed the floodgate terms** — bare `bitcoin` / `crypto` / `digital asset` /
  2–4-char tickers-and-units / any term at EFTS's ~10,000 ceiling. A capped SIGNAL
  term is a recall hole wearing a discriminating name. **SIGNAL** = discriminating (a
  single hit PLACES the filer); **BROAD** = collision-prone (corroboration only).
- **Recall over speed (#9).** Don't trim discovery for cost; caps are PER-TIER
  (SIGNAL deep / BROAD shallow — the default) and stay visible (`⚠ capped`), never a
  silent drop.
- **Stack ops from the MAIN checkout**, never a worktree.

## Steps (dev, `:8001`)
1. **Create the thesis** — `POST /workbench/theses` (the promote writer):
   ```
   {"id": null,            # or a UUID to PRESERVE the id (aligns the draft_runs dir)
    "name": "...", "narrative": "...",
    "ticker": null,        # null = a multi-name theme; else the anchor ticker
    "basket": [], "segments": [], "identity_overrides": []}
   ```
   Returns the `ThesisDetail` with the id. Promote is the PURE structured writer — it
   does **NOT** carry `term_set` / `catalysts` / `kill_criteria` / `exclusions` (the
   wipe-guard); each needs its own narrow writer below.
2. **Seed the term set (THE WIPE-GUARD STEP — promote dropped it).** Either
   `PUT /workbench/theses/{id}/terms/edit` with `{"terms":[{"term":"...","tier":"signal|broad"}]}`
   (stamps `operator_set` — fine for a fresh seed), OR the narrow
   `thesis_repo.set_term_set(conn, id, [TermSetEntry(**e) …]); conn.commit()` in the
   backend container (preserves EXACT authorship — use this when migrating). Read the
   set back and confirm the count + the signal/broad split.
3. **(Optional) catalysts / kill_criteria / exclusions** — via their narrow writers
   (`set_catalysts` / `set_kill_criteria` / `set_exclusions`); promote drops these too.
4. **Run the draft** — `POST /workbench/theses/{id}/draft-chain` → **202 + `{job_id}`**;
   poll `GET /workbench/theses/{id}/draft-chain/jobs/{job_id}` until `done`/`failed`
   (minutes: EDGAR discovery → Opus tail-sweep → decompose → narrate). **RESPONSE-ONLY**
   — it saves a run under `data/draft_runs/<id>/` and persists NO basket; the operator
   triages/promotes. The LLM seams need `ANTHROPIC_API_KEY` (fail-open without → an
   empty draft, surfaced not silent).
5. **Grade the run** (from the job result / the run JSON): placed vs verify vs
   ambiguous/absent; the segments built; `capped_terms` + coverage (retry a DEGRADED
   run before trusting it — a gap scored as truth is the silent-drop failure). Then:
   the **recall floor** (did the expected spine PLACE?), the **tail** (names beyond
   your key — the payoff of clean SIGNAL seeds), and the **flood check** (ETPs a
   minority, not the bulk). Against a committed answer key → the `recall-rescore` skill.

## Traps (all hit in practice)
- **`GET /workbench/theses/{id}` is NOT a route → 404.** Read the thesis via
  `thesis_repo.get` in the backend container, or `GET .../scored` — never a bare GET.
  (A 404 body parses as all-null fields — don't mistake it for "empty thesis".)
- **The double-stdin trap:** `docker exec -i <c> python < script.py` uses stdin for the
  SCRIPT — you canNOT also pipe data via stdin (it silently arrives empty). Embed the
  data as a literal in the generated script, or `docker cp` a file into the container.
- **The cap↔decompose coupling:** a big discovery universe (a high cap) truncates the
  decompose/organize LLM at its `max_tokens` ceiling (8000 default → 0 segments →
  "draft timed out" when the reaper fires). Raising the discovery cap must co-raise
  `llm_decompose_max_tokens` (env `ALPHADECK_LLM_DECOMPOSE_MAX_TOKENS`) AND
  `draft_job_running_ttl_s` — the cap is not a standalone dial.
- **Per-tier caps** (`ALPHADECK_DISCOVERY_HIT_CAP` = SIGNAL, `ALPHADECK_DISCOVERY_BROAD_HIT_CAP`
  = BROAD): a high GLOBAL cap buys collision NOISE (deep pages of BROAD terms), not
  recall — keep SIGNAL deep, BROAD shallow. Seed-term TIERING is the operator's to get
  right (a collision token mis-tiered SIGNAL floods PLACED).
- **Cost thread:** cheap-cut first (triage on visible row data, zero API) BEFORE
  marking anything for data — a large draft is a lot of rows and a lot of EFTS pages.
