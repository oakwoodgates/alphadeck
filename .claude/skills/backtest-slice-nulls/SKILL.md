---
name: backtest-slice-nulls
description: >-
  Read whether ONE algorithm family — a marginal slice like key1_source=<value>, or a
  two-key family such as key1_source=<value>,confirmation_grade=<value> — beats ITS OWN
  timing null and name null on a completed backtest pass. The pooled report only gives the
  whole book and fully-crossed cells; this RE-DRAWS a marginal slice's own two nulls
  faithfully off the frozen mirror. Use after a pass to test "does this sub-family beat its
  own nulls / is there a pocket the pooled median hides"; also trigger on "marginal nulls",
  "does family X beat its nulls", "slice the backtest nulls". Read-only, measurement only;
  runs on the working store, never prod; refuses any run it cannot reproduce.
---

# Marginal per-family nulls — does one family beat ITS OWN nulls?

Source: `backend/backtest/slice_nulls.py` (+ `backend/tests/backtest/test_slice_nulls.py`);
built on the pooled report's null model (`backtest/pooled.py`) and `rebench.py`'s "worth only
what the old numbers reproduce" reproduction philosophy. The pooled report gives the whole
book and fully-crossed cells; `sweep.py --metric-slice` re-slices only the ACTUAL return.
This tool RE-DRAWS a marginal slice's own two nulls faithfully off the frozen mirror the pass
already cites — same K, same seed, each episode's own horizon — and verifies every run against
its stored `pooled.json` before pooling. Measurement only, never promotion.

## Guardrails (never violate)
- READ-ONLY: no Postgres, no re-run of the pass, no new mirror, no write to the store,
  nothing touches prod. Run against the working store (`data/backtest`), never the frozen
  prod archive.
- Slice only ALGORITHM dimensions (`pooled.SLICE_KEYS`: key1_source, confirmation_grade,
  co_arm_bucket, close_reason), one or two at a time. Slicing by thesis is refused (#4, the
  leaderboard trap).
- The tool REFUSES a run it cannot verify (the reproduction gate). A refusal protects the
  number — never weaken the gate to make one go away.

## Run it
From `backend/` with the venv active (or `PYTHONPATH=backend` +
`backend/.venv/Scripts/python` from the repo root):
```
python -m backtest.slice_nulls --pass-id <PASS_ID> --slice key1_source=<value>
python -m backtest.slice_nulls --pass-id <PASS_ID> --slice key1_source=<value>,confirmation_grade=<value>
```
Flags: `--config-hash <hash|short>` (which point of the pass; default = the current
production baseline), `--run-id <id>` (repeatable, instead of `--pass-id`), `--mirror-dir`
(default: found in the store by the manifest's hash), `--out <path.json>`, `--root <store>`
(default `data/backtest`).

## Read the output
Per slice, pooled across the pass's windows: `actual` beside `vs_timing` and `vs_name`
(median + mean), `excess vs basket` (median + mean), effective-n (distinct name-days — the
independence-adjusted count), % positive, and a per-window `beat X of M` tally for each null
(M = windows where that null was drawable). Keep the two nulls distinct: `vs_name` is name
selection, `vs_timing` is entry timing. A SHORT-WINDOW flag means the timing null drew < 30
sessions and is near-vacuous there — lean on the name null and the pooled figure.

## Traps
- **Only passes whose manifests carry `member_ids` are analyzable.** The name-null roster and
  the timing-null sessions are reconstructed from `manifest.theses[].member_ids`; a pass that
  predates that field refuses EVERY run with "do not reproduce" and empty (`None`) nulls. If a
  whole pass refuses that way, check `member_ids` on a run's manifest first.
- **`--config-hash` defaults to the CURRENT `DEFAULT_CONFIG` hash.** A pass whose baseline
  used an earlier config hash (before a dial was added) matches no runs under the default —
  pass that pass's own baseline `config_short` explicitly.
- A refusal names its reason (wrong mirror, missing `pooled.json`, or a re-draw that did not
  reproduce). "Did not reproduce" against real stored values means the reconstruction is wrong
  for that run — investigate the manifest (usually the `member_ids` trap), never bypass the
  gate.
