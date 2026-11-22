# Backtest passes

The verdicts of the pre-registered backtest passes, and of the one tuning pass before them: the evidence behind
"no dial has been fitted to outcomes". A record, never updated.

## What ran

The operator registered each decision rule before launch and ruled on each result. Every pass ran on the dev
copy of prod; unless a row says otherwise, on the public clock over nine disjoint 42-day windows from
2025-09-01 to 2026-09-14.

| Pass | Date | What it tested | Decision rule | Verdict |
|---|---|---|---|---|
| Pass 001 (the seed) | 2026-06-09 | First replay over the seed: 19 episodes, UNH and the nuclear basket | change a dial only on evidence | Zero dial changes. The insider-core horizon gives back gains after the peak, but 90 days stops UNH arming: a shape problem, not a magnitude |
| Phase 1, `20260918T032952Z-public-h5-phase-1-the-convictio` | 2026-09-18 | Conviction horizons (`exit_by`); 12 theses, K=5, 2,605 episodes | a plateau with cross-window sign agreement | Nothing promoted. Excess over the basket: median -0.47%, mean -5.13%. `catalyst_default_horizon_days` is inert (90 to 730 days byte-identical): every catalyst fact publishes a term. `revenue_accel` at 60 days: paired median +0.00% though 561 of 1,931 shared episodes moved (median +8.35%) |
| H1/H3, `20260919T015210Z-public-h1-h3-co-first-measureme` | 2026-09-19 | The revenue re-acceleration levers and the breakdown de-arm dials; K=20, code `e339eb9`, mirror `ad8bd98a`, pin 2026-09-15T00Z | a plateau with strict cross-window sign agreement | No curve of the five showed `strict_measurable_agreement`; every plateau band empty |
| Re-read after first-knowability dating: the H1/H3 baseline, `20261017T175600Z-public-d3a-reread-old-mirror`, `20261017T181800Z-public-d3a-reread-new-mirror` and a control | 2026-10-17 | The same measurement after fundamentals were dated at first knowability, on a fresh tape; not a trial | none | Below |
| Session windows, `20261031T163001Z-public-counting-bar-driven-wind` | 2026-10-31 | `breakout_alpha_liveness_unit` and `_sessions`: 5 to 10 sessions against the calendar baseline; K=20; 62 theses on today's basket; baseline n 5,958 | a session count only on a plateau with strict sign agreement in every window | No point agreed in sign across windows; the plateau is empty; the breakout window stays in calendar days |

The re-read's conclusions (corrected code on the same tape, pooled, slice-nulls population):

| Conclusion | Verdict | Numbers |
|---|---|---|
| Revenue re-acceleration is the widest family and drove basket-wide arming | Weakened | 408 episodes disappear, all with a revenue trigger; revenue-keyed episodes 1,246 to 858, below insider's 864; loses to both nulls (-2.25 pp timing, -0.22 pp name) |
| The book loses to its nulls | Upheld | Actual -4.40% to -3.80%; gap to the timing null -2.97 to -2.48 pp, to the name null -0.81 to -0.43 pp; beats timing in 2 of 9 windows |
| Insider core is the only live pocket | Upheld | The only key-1 source and grade family beating both nulls in all four runs (reference +1.59% against +1.21% and -2.00%); beats timing in 3 of 9 windows, so the edge is name selection and the timing is unproven |

## Artifacts

- Run directories and curves: the dev store `data/backtest/` (gitignored), under the pass ids above. Prod's
  `/backtest` page serves a frozen copy of the keeper passes (`data/backtest_prod`).
- Session-window overlays: `backend/backtest/overlays/`.
- The re-read's control store was deleted after the read. Pass 001's inputs are not in the repo.

## Decisions

- No dial in `domain/config.py` changed because of any pass; every lever tested keeps today's value.
- From 2026-09-18, passes key their plateau on strict sign agreement; K rose from 5 to 20 after phase 1 and is
  part of each registration.

## Follow-ups

Tracked on [ROADMAP](../ROADMAP.md): run or retire the registered 52-week and laggard session ladders;
the setup-strength age decay Pass 001 pointed to for the give-back; a result for `catalyst_max_horizon_days`
(built for a pass that reused phase 1's mirror; none recorded).

## Full runbook

- `git show 8094406a:docs/RECALIBRATION.md` (sections A, D, E and the session-window registration).
- `git show 8094406a:docs/BACKTEST.md` ("Re-reading a pass after a correction" and the phase 1 sections).
