---
name: backtest
description: >-
  Use when running, resuming or re-reading a backtest pass (backtest.run, backtest.sweep, rebench,
  pair, slice_nulls), testing whether one algorithm family beats its own nulls, or writing up a
  pass's numbers.
---

# Backtest pass

## Guardrails

- `backtest.run`, `backtest.sweep`, `rebench` without `--no-db` and `replay.run` open a Postgres
  connection, and `db/session.py` defaults to PROD. An agent opens no database connection outside pytest
  (`CLAUDE.md`). `pair` and `slice_nulls` read artifacts only.
- Never use prod's database or write into the frozen archive `data/backtest_prod`.
- Pass `--clock public` for anything you will quote; the default is the record clock.
- Register before launch (`--hypothesis`, `--decision-rule`, with K, window size and plateau rule).
  Nothing is amended after launch; a sliced curve is a new hypothesis.
- Never slice by thesis or name a best point (invariant 4). A pass promotes nothing; the operator decides
  (`docs/CALL_LOGIC.md`).
- One checkout and one venv per pass: pyarrow's build is in every file's bytes, and so in the hash.
- Never delete, re-export or re-hash a run or mirror, and never weaken a gate that refuses.

## Steps

1. Provision the venv (`bash scripts/ensure-venv.sh` from the root); run the commands below
   from `backend/` with its `python`. Export `DATABASE_URL` naming the dev database (port 5545) from your
   env file without printing it, and `ALPHADECK_IMAGE_SHA="$(git rev-parse HEAD)"`.
2. Runs land in this checkout's `data/backtest`; `--out-root` naming the main checkout's puts them on
   the dev stack's `/backtest` page (steward only).
3. Write down the registration and the dial's prior trials (`dial_trials` on `GET /backtest/runs`).
4. One run: `python -m backtest.run --start <date> --end <date> --clock public`; add
   `--config <overlay.json> --hypothesis "<text>" --decision-rule "<text>"` for an overlay.
5. A pass: `python -u -m backtest.sweep --start <date> --end <date> --pin <ISO> --clock public
   --ladder <dial>=<v1>,<v2> --hypothesis "<text>" --decision-rule "<text>"`, with `--overlay-ladder`,
   `--null-draws`, `--workers`, `--concurrency`, `--plateau-rule` as registered.
6. Same tape, new pass: add `--mirror-dir <mirror>` and drop `--clock` (inherited). On `MirrorExists`,
   reuse the tape this way or pick another pin or window.
7. A window job died: rerun the same command with `--resume <pass_id>` (same windows and clock). Name
   the dead pass id in the write-up; its finished runs count as trials.
8. Re-read: `python -m backtest.pair --pass-id <id> --dial <dial> --values <a>,<b>`;
   `python -m backtest.rebench --pass-id <id> --no-db`.
9. One family against its own nulls: `python -m backtest.slice_nulls --pass-id <id> --slice
   key1_source=<value>` (two keys comma-separated; `pooled.SLICE_KEYS` only). `--config-hash` picks the
   point (default: today's production config).

## Checks

- Numbers, never verdicts ("X works"): describe what happened.
- Each figure states its horizon, comparison, n and how often it held. Headline: median excess over the
  basket, mean beside it.
- Quote the pass id, clock, pin, windows, K, config and mirror hashes, plateau rule and trial count; the
  plateau band (empty or not), the agreement field it used and `n_unmeasurable`.
- On windows near 30 sessions, carry: "a `vs_timing` close to `actual` on a window of about 30 sessions
  means there was no comparison, not that there is no edge."
- Print the paired composition line (`n_shared`, `n_changed`, median change when changed). Never write
  "same episodes, re-timed": pairing fixes the entry, not the horizon.
- A sliced pass adds a slice header (n of the pool) saying every figure is the slice's; the most the pooled
  median could move, before the result; per-window n beside the deltas and statuses; the unmeasurable
  windows by name with their registration date; all three agreement fields. On a horizon dial, a falling
  slice n is composition, not timing.
- Slice nulls: keep `vs_name` (selection) and `vs_timing` (timing) apart; give the effective n and each
  null's "beat X of M"; on a SHORT WINDOW flag, lean on the name null.
- Event-outcome reports follow the market-cap rule in `CLAUDE.md`: tiered table first, pooled below it
  and labeled, tiers with n < 10 too small to read, the liquidity cut labeled.
- A re-read after a correction quotes the correction and the tape's growth separately; it is no new trial.

## Stop and ask when

- A step would open a database connection: give the operator the exact command, or run it only on their
  explicit instruction for this pass.
- `DATABASE_URL` is unset or names prod.
- Anything refuses (`MirrorExists`, a resume mismatch, a run that does not reproduce): report it. Slice
  nulls refusing every run means the pass predates `member_ids`; no matching runs means its baseline used
  an older config (pass its `--config-hash`).
- A result looks like a case for changing a dial, K, the window size or the plateau rule.

## Model

[`docs/BACKTEST.md`](../../../docs/BACKTEST.md)
