---
name: migrate-thesis
description: >-
  Migrate ONE operator-built thesis from dev to prod faithfully, by REPLAYING it
  through the app's own writers — NEVER a raw dev->prod DB copy (the guardrail forbids
  it; a subgraph copy is fragile). Trigger on "migrate a thesis to prod", "move my dev
  thesis to prod", "promote this dev thesis to prod". THE TRAP: promote's upsert
  wipe-guard drops term_set / catalysts / kill_criteria / exclusions — each needs its
  own narrow writer.
---

# Migrate a thesis — dev → prod, faithfully

Move ONE operator-authored thesis from dev to prod by replaying it through the app's
sanctioned writers (the promote endpoint + the narrow repo writers), run against the
prod stack. Additive, prod-safe. Full record + traps: the
`alphadeck-thesis-dev-to-prod-migration` memory; dev/prod model in `docs/DEV_PROD.md`.

## Guardrails (never violate)
- **NEVER a raw dev→prod DB copy or write.** Prod's DB is read-only except the app's own
  writes (the anti-truncation guarantee — refresh is prod→dev ONLY). Replay via the API +
  repo writers only.
- **It's a prod write — get the operator's explicit go first** (it creates the thesis and
  kicks a background fact ingest).
- **Preserve the thesis id only if the operator asks** — the promote honors `req.id`;
  keeping it aligns dev/prod and the draft-run dir (named by thesis id).
- **Stack ops run from the MAIN checkout**, never a worktree.

## Preflight (READ dev + prod — no writes)
1. Confirm the thesis is on dev and NOT already on prod (`SELECT name FROM thesis …`).
2. Every member security must resolve on prod — compare **DISTINCT** sids. A name placed in
   two segments has duplicate basket rows, so `comm` on the raw sid list FALSE-flags them as
   missing; use `… LEFT JOIN security_master sm ON sm.id=t.sid WHERE sm.id IS NULL` → must be
   empty. A genuinely-missing security is added to prod's master first (else the promote
   404s that member).

## Migrate (prod writes — operator go required)
3. **Extract from dev with the app's serialization** (not raw SQL). In the dev backend
   container (`docker exec -i alphadeck_dev-backend-1 python < script.py`): `thesis_repo.get`
   → dump the PromoteThesisRequest subset (`id,name,narrative,ticker,basket=[m.model_dump()],
   segments=[s.model_dump()]`) AND `term_set` (+ catalysts / kill_criteria / exclusions if the
   source has any) separately.
4. **Promote to prod** (create): `POST http://localhost:8000/workbench/theses` with the payload
   (id preserved if asked). Fail-closes 404 on any member sid absent from prod's master;
   canonicalizes non-primary siblings; kicks the on-promote fact ingest for the new members.
5. **Set what the upsert DOESN'T carry — THE TRAP.** `thesis_repo.upsert` (what promote calls)
   deliberately never names `term_set` / `catalyst` / `kill_criterion` / `thesis_exclusion`
   (the wipe-guard), so promote alone drops them. In the prod backend container run the narrow
   writers with the dev data: `thesis_repo.set_term_set(conn, tid, [TermSetEntry(**e) …]);
   conn.commit()` (+ `set_catalysts` / `set_kill_criteria` / `set_exclusions` if present).
6. **Copy the draft-run JSON** (the DISCOVER accountability artifact),
   `data/draft_runs/<thesis_id>/<ts>-<job>.json`, mounted at `/data` (the appdata volume):
   `docker cp` dev→host→prod. **Set `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'`** or Git Bash
   mangles the container `/data/…` path and the cp fails "Could not find the file". No content
   edit is needed when the id is preserved and dev's master ⊆ prod's (id already matches, 0
   tenant/path refs, its discovered-universe sids all resolve on prod). It reads back only when
   `ALPHADECK_RUN_LOADER_ENABLED` is set on that stack.

## Verify
7. Basket **member-for-member `diff`** dev vs prod (ticker|segment|conviction|signed_off|
   authored_by, sorted) → identical; thesis parity (name / narrative-len / segments / terms).
8. The call computes: `GET /theses/<id>/call?asof=<today>` → a real state (a fresh migrate comes
   up Incubating/Warming as facts ingest). Confirm facts landing:
   `SELECT count(*) FROM fact_insider_txn … WHERE recorded_at > now()-'10 min'`.
