# Backtest passes

The verdicts of the pre-registered backtest passes, and of the one tuning pass before them. They are the
evidence behind "no dial has been fitted to outcomes". This is a record: it is never updated.

## What ran

The operator registered each decision rule before launch and ruled on each result. Every pass below ran on
the dev copy of prod. Unless a row says otherwise, a backtest pass ran on the public clock over nine
disjoint 42-day windows from 2025-09-01 to 2026-09-14.

| Pass | Date | What it tested | Decision rule | Verdict |
|---|---|---|---|---|
| Pass 001 (the seed) | 2026-06-09 | First replay over the seed: 19 episodes, UNH and the nuclear basket; every dial inspected | change a dial only on evidence | Zero dial changes. The insider-core horizon gives back gains after the peak, but shortening it to 90 days stops UNH arming (the dial is also the co-location window): a shape problem, not a magnitude |
| Phase 1, `20260918T032952Z-public-h5-phase-1-the-convictio` | 2026-09-18 | The conviction horizons that set `exit_by`; 12 theses, K=5, 2,605 scored episodes | a plateau with cross-window sign agreement | Nothing promoted. Excess over the basket: median -0.47%, mean -5.13%. `catalyst_default_horizon_days` is inert (90 to 730 days byte-identical, pooled and on its slice): every catalyst fact publishes a term. `revenue_accel` at 60 days: paired median +0.00% while 561 of 1,931 shared episodes moved by a median +8.35% |
| H1/H3, `20260919T015210Z-public-h1-h3-co-first-measureme` | 2026-09-19 | The three revenue re-acceleration levers (`revenue_accel_grade`, `revenue_accel_key`, `computed_conviction_requires_core_confirmation`) and the breakdown de-arm dials; K=20, code `e339eb9`, mirror `ad8bd98a`, pin 2026-09-15T00Z | a plateau with strict cross-window sign agreement | No curve of the five showed `strict_measurable_agreement`; every plateau band empty; nothing promoted |
| Re-read after first-knowability dating (four runs: the H1/H3 baseline as reference; `20261017T175600Z-public-d3a-reread-old-mirror`; `20261017T181800Z-public-d3a-reread-new-mirror`; a control) | 2026-10-17 | The same measurement after fundamentals were dated at first knowability, crossed with a fresh tape. A re-measurement, not a trial | none (no dial moved) | Conclusions below |
| Session windows, `20261031T163001Z-public-counting-bar-driven-wind` | 2026-10-31 | `breakout_alpha_liveness_unit` and `_sessions`: 5 to 10 sessions against the calendar baseline; K=20; 62 theses, all on today's basket; baseline n 5,958 | adopt a session count only on a plateau with strict sign agreement in every window against the calendar baseline | No point agreed in sign across the windows; the plateau is empty; the breakout window stays in calendar days. The 52-week and laggard ladders were registered and not run |

The re-read's conclusions, reference against the corrected code on the same tape, pooled, on the slice-nulls
population:

| Conclusion | Verdict | Numbers |
|---|---|---|
| Revenue re-acceleration is the widest family and drove basket-wide arming | Weakened | 408 episodes disappear, every one with a revenue trigger; revenue-keyed episodes 1,246 to 858, below insider's 864; the family still loses to both nulls (-2.25 pp against timing, -0.22 pp against name) |
| The book loses to its nulls | Upheld | Actual -4.40% to -3.80%; the gap to the timing null -2.97 to -2.48 pp, to the name null -0.81 to -0.43 pp; it beats timing in 2 of 9 windows |
| Insider core is the only live pocket | Upheld | The only key-1 source and grade family that beats both nulls in all four runs (reference +1.59% against +1.21% and -2.00%); it beats timing in only 3 of 9 windows, so the edge is name selection and the timing is unproven |

## Artifacts

- Run directories and curves are in the dev store `data/backtest/` (gitignored), under the pass ids
  above. Prod's `/backtest` page serves a frozen copy of the keeper passes (`data/backtest_prod`).
- The overlay files for the session-window pass are in `backend/backtest/overlays/`.
- The re-read's control run used a separate store, deleted after the read. Pass 001's inputs are not in the
  repo.

## Decisions

- No dial in `domain/config.py` changed because of any pass. The levers tested stay at today's values:
  the revenue re-acceleration levers, the breakdown de-arm scope, `catalyst_max_horizon_days` (no cap) and
  the session-window dials (calendar days).
- From 2026-09-18, passes key their plateau on strict sign agreement. K rose from 5 to 20 after phase 1;
  it is part of each registration.

## Follow-ups

Tracked on [ROADMAP](../ROADMAP.md):

- Run or retire the registered 52-week and laggard session ladders.
- The setup-strength age decay that Pass 001 pointed to as the fix for the give-back.
- `catalyst_max_horizon_days` was built for a pass that reused phase 1's mirror; no result for it is
  recorded.

## Full runbook

- `git show 8094406a:docs/RECALIBRATION.md` (sections A, D and E, and the session-window registration with
  its result table).
- `git show 8094406a:docs/BACKTEST.md` ("Re-reading a pass after a correction" and the phase 1 sections).
