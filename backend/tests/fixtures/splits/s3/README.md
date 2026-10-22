# Split plan S3 — test fixtures (real data only)

Read by `tests/pipeline/test_data_flags.py` and `tests/app/test_data_flags_api.py`. Nothing here is synthesized,
rounded or rescaled.

| File | What | Where it came from |
|---|---|---|
| `views.json.gz` | Per (ticker, as-of, clock): the price tape, the split-list rows and the newest share count exactly as `PointInTimeData` read them, plus (live views) every stored version of each bar re-versioned in the flap window | A read-only `pg_dump` of prod (2026-10-22 16:14 UTC) restored to a scratch DB on the dev Postgres, then dropped. BTOC / JAGX / KITT / MVNC / WSHP / SKYT / ZCSH on the live view of 2026-10-22; ZCSH 2026-09-29 and SMTK 2026-08-20 on the public clock (the split-night hold's real nights). Numerics kept as strings. |
| `provenance.json` | The split-basis repair's provenance trimmed to BTOC and ARTL (their split rows as written; the first three re-scaled bars each, the per-bar old/new values dropped); SKYT's `tape_stale` row from that night's run artifact; HLLK's renamed `tape_stale` row from the 2026-10-19 pass (before the operator adopted SDCO) | Prod's `data/repairs/20261022T094736Z-split-basis.json`, `data/cron_runs/20261022T023001Z.json` and `data/cron_runs/20261020T023002Z.json`, copied read-only. |
