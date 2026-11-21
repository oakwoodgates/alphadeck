# Scoreboard

The Scoreboard scores what the platform said against what prices did. It reads a workspace's call of
record (the immutable `calls` log), cuts it into arm episodes, scores each episode on realized, as-of-capped
prices, and sets the user's logged decisions beside the episodes they answered. Every signed-in user sees
their own workspace's Scoreboard at `/scoreboard`; the steward also gets one line on the Admin page. Read
this doc before you change `backend/scoreboard/`, `frontend/src/scoreboard/`, or which `calls` rows count
as the record.

The page shows one edition at a time. With none named it reads the workspace's newest published edition,
and Edition 0 (the record itself) when none is published. A stored edition is a frozen re-run of today's
rules, scored by the same code; [EDITIONS](EDITIONS.md) owns editions and lists what differs for one. This
doc describes Edition 0.

## Code

| Piece | Module |
|---|---|
| The record read; `RecordSource` (Edition 0) and `EditionSource` | `scoreboard/record.py` |
| Summary, metrics gate, maturity horizon, late and reconstructed lists | `scoreboard/assemble.py` |
| Record models (`ScoredEpisode`, the flags) | `scoreboard/schema.py`; wire models in `app/schemas_api.py` |
| Ingest provenance flags and the run-identity caption | `scoreboard/provenance.py` |
| The late-row judge | `scoreboard/late.py`, on `pipeline/night_coverage.is_late` |
| The operator track | `scoreboard/decisions.py` |
| Realized prices | `scoreboard/prices.py` (`PgRealizedPrices`) |
| Chart overlays | `scoreboard/overlays.py` |
| Episodes, scoring, metrics (shared with replay) | `replay/episodes.py`, `replay/scoring.py`, `replay/metrics.py` |
| Routes | `app/routers/scoreboard.py`, `app/routers/record.py` |
| CLI | `scoreboard/run.py` |
| The page | `frontend/src/scoreboard/` |

## How it works

### The one rule: the record is the scoring source

Edition 0 scores the cards the platform recorded, never a recompute. It reads the `calls` log through
`calls_repo.latest_for_thesis` (the final card per as-of) and scores those cards. Re-deriving past calls
with today's code is replay's job ([REPLAY](REPLAY.md)) or an edition's. Consequences:

- No backfill into the score. A workspace's record starts on the first night it holds an honest (not
  reconstructed) call (`calls_repo.record_start`); `GET /record/start` answers that night, or null before
  the first nightly call, for the caller's workspace only. Workspaces start on different nights.
- Censored starts. An episode already armed on its thesis's first recorded card has an unknowable arm date:
  it is flagged `censored_start`, shown, and left out of arm-anchored metrics.
- Gaps are fine. A new as-of always appends a row and `record_if_changed` dedups only within an as-of, so a
  membership change always recorded a row that day. Weekends and downtime leave gaps that `derive_episodes`
  reads as they are: the record last spoke on the prior row.
- Archived theses stay. Archiving stops accrual and never erases the record; `include_archived=false`
  (CLI `--exclude-archived`) is the explicit filter.
- Nothing is deleted or marked. The two filters below are read policies over rows that stay in the log.

### Reconstructed rows

`pipeline.backfill` reconstructs a missed night on the public clock with `known_at` pinned. The row carries
`calls.reconstructed = true` (migration 0042), a column off the card: a field on the card would fake a change
in `record_if_changed`'s canonical compare. A reconstructed row is reported, never scored. It is a recompute,
not what the platform said, and a backfilled night before `basket_snapshot` began (2026-09-15) runs today's
basket, because the roster on that night is unknowable.

- The record read passes `include_reconstructed=False` to its three winning-row reads
  (`latest_for_thesis`, `ingest_health_for_thesis`, `run_identity_for_thesis`), which filter before the
  per-as-of dedup. A night with both a nightly row and a reconstruction scores the nightly row; a night with
  only reconstructions reads like a cron gap.
- A reconstructed row never defines an episode boundary: it never arms, de-arms, or stamps a flag.
- `summary.reconstructed_nights` names, once, each night on which every row is reconstructed (one quiet
  banner line). A night with an honest row beside a reconstruction is scored and not listed.
- Every other reader keeps the default and sees every row: the cron's transition compare, the backfill's
  prior-row report, the freshness reads, the decision log's stance.

Pinned by `tests/scoreboard/test_record.py`, `tests/repositories/test_calls_repo.py` and
`tests/db/test_migration_0042_reconstructed.py`.

### Late-recorded rows

A late row is a non-reconstructed row recorded at or after the end of its night's on-time window, the next
weekday's 06:00 ET EDGAR open (`pipeline.night_coverage.is_late`; the catch-up skip uses the same window, so
"late" has one definition). A Friday night's window runs to Monday's open; a daytime row before the nightly
run is not late. A late row can have read filings its night could not, so by default it never defines an
episode.

- Lateness is derived from `recorded_at` on every read (`scoreboard/late.py`): no column, no stamp. The
  cron still writes late rows, because the log records what was said and when; every reader but this one
  sees them, and a push for a late night says so ([FEED_LOOP](FEED_LOOP.md)).
- Under `Settings.scoreboard_exclude_late` (see the setting), a thesis's late rows are judged once
  (`late_call_ids`) and their ids dropped from the same three winning-row reads before the per-as-of dedup,
  beside the reconstructed filter (`calls_repo._ids_clause`). An on-time row for the night wins over a later
  late one; a night whose only rows are late reads like a cron gap.
- The filters compose by AND: a row is scored only if it is not reconstructed (always) and not late (under
  the policy).
- `GET /scoreboard?include_late=true` scores late rows, `false` sets them aside, omitted follows the
  setting (CLI: `scoreboard.run --include-late`). Including them reproduces an unfiltered read exactly
  (`tests/scoreboard/test_late_rows.py`). `summary.late_policy` names the reading on every response; name it
  when you quote a number.
- `summary.late_nights` lists each late thesis-night either way, with the honest row scored instead, or null
  for a gap. A night holding a reconstruction and a late row is listed here, as a gap, not in
  `reconstructed_nights`.
- Admin counts a late row as covering its night (the pass did record it), so a night recorded only late
  reads covered there and as a gap here. Admin lists exactly those nights (`record.late_only_nights` on
  `GET /admin/status`, from `scoreboard/late.late_only_nights`, the same judge) as one quiet line, never an
  alarm or a cron verdict ([ADMIN](ADMIN.md); `tests/scoreboard/test_late_only_nights.py`).

The on-time side belongs to the nightly: a catch-up skips a thesis that already holds an on-time row, and
`pipeline.daily --asof` for an older night is refused unless asked for on purpose ([FEED_LOOP](FEED_LOOP.md);
`tests/pipeline/test_killed_runs.py`, `tests/pipeline/test_night_coverage.py`).

### Episodes and their flags

The unit is replay's arm episode (`replay/episodes.derive_episodes`): one basket member's contiguous run in
`armed_members`, keyed `(thesis_id, security_id, arm_date)`, scored by `replay/scoring.score_episode` over
`[arm_date, exit_by]`. `exit_by` is the call's signal-validity horizon, not a trade exit. [REPLAY](REPLAY.md)
owns the unit, the close reasons and the metric set. The Scoreboard adds flags about the record:

| Flag | Meaning |
|---|---|
| `status` | open while still armed at the record edge (a running return, not a verdict), else closed |
| `matured` | the episode's `exit_by` is at or before the as-of |
| `censored_start` | armed since before the record began |
| `arm_ingest_fresh` | the arm-date row's ingest health, from the same winning row; `false` = a partial ingest; null is never coerced |
| `freeze_era` | the arm falls in `provenance.FREEZE_WINDOW`, the July 2026 EDGAR cache freeze |
| `thaw_lag_days` | the largest ingest lag over the arm triggers' cited Form 4s; above `THAW_LAG_DAYS` marks a thawed-late arm; null = unknown |
| `ingest_flagged` | any of the three above; `ingest_note` is the backend-written why |
| `triggers_at_arm` | the arm card's trigger evidence (invariant 6) |

Metrics judge only matured, non-censored, unflagged episodes that have a price result, so a running return
never enters `false_arm_rate` before its window ends. A flagged episode carries the INGEST badge, stays on
the ledger, and is left out of aggregate metrics with no toggle. The flags are composed after scoring from
reads the scoring path never sees: `scoreboard/provenance.py` imports nothing from `calls/` and nothing on
the call path imports it, so a clean, a flagged and a legacy-null episode score the same
(`tests/scoreboard/test_provenance.py`).

- The maturity horizon turns an empty gate into a countdown: `next_maturity`, `n_maturing_30d`, and
  `projected_min_n_date` (when the eligible pool could reach `MIN_N`, counting only non-censored, unflagged
  maturities). It is a projection over recorded episodes, never a promise.
- `insufficient_prices` means no bar on or after the arm yet, or, on a matured episode, no bar in the
  scored window at all; the ledger words the two differently.
- When the only bar is the arm-day bar, the return is a degenerate 0.0%, so the ledger shows a dash and
  "awaiting forward bar" (`rows.ts` `awaitingForwardBar`). A matured single-bar episode still reads
  realized.
- `truncated` is true when `exit_by` is past this name's last bar within the reader's cap
  (`replay/scoring.py`). It asks the tape, not a calendar: a weekend or holiday `exit_by` before a live
  name's edge reads false; a stalled or delisted name reads true. A running episode is always truncated, and
  three phrasings rely on that (`moveNote`, `peakTimingPhrase`, the chart's "last bar" marker).
- `tape_behind_market` is truncated and `exit_by` before the latest bar anywhere in the tape. It is a second
  field, never a narrowing of `truncated`: folding the market leg in would untruncate every running episode
  and falsify those phrasings (`test_asof_cap_no_future_leak` fails). It spares an episode maturing today
  and a feed stalled everywhere (the freshness line's job). The market edge is resolved once per request.
- The TAPE ENDS badge needs matured, `tape_behind_market` and a price result. The market leg is strict, so
  an `exit_by` on the market's latest bar fires a day later
  (`test_market_edge_equal_to_exit_by_is_suppressed`), bought against same-day flicker.
- `truncated` is not a metric input; `Outcome` is computed on read and never stored.
- The exit is the last bar inside `[arm_date, exit_by]`, never the last bar at or before `exit_by`; the
  unbounded read could pair a later entry with an earlier exit.
- A de-arm can come after the scored exit when the horizon elapsed while the member stayed armed. That is
  the horizon working (`test_maturity_judged_only_at_exit_by`): `exit_vs_peak_days` can read negative and
  `dearm_index` is null.

### Setup strength and the n >= 5 gate

The per-call display is setup strength (wire field `confidence`): an experimental, relative read of trigger
composition and risk, not a probability of success ([CALL_LOGIC](CALL_LOGIC.md) section 7). Only matured
forward outcomes can calibrate it.

Every aggregate metric carries its `n` and `insufficient_n`; below `MIN_N` (5, `replay/metrics.py`) the page
withholds the number. The gate is a presentation safeguard against over-reading a tiny sample, not an
evidence threshold: clearing n >= 5 makes no metric conclusive, establishes no calibration, and turns setup
strength into no probability. Sample composition, per-bucket counts, censoring and stability over a much
larger record still decide what a number means. The platform's trust state is in [ROADMAP](ROADMAP.md).

### Prices

`PgRealizedPrices` is the Postgres twin of replay's DuckDB `RealizedPrices`: the reader surface
`score_episode` uses, the same latest-version-per-day dedup, and two caps. `d <= asof` keeps a scrubbed-back
view from seeing a later bar; `recorded_at <= known_at` (default now) lets a corrected bar's latest version
win. The DuckDB reader stays forward-unbounded, as scoring there requires.
`tests/replay/test_pg_prices_parity.py` pins the two row for row, `Outcome` included. `replay/scoring.py`
imports duckdb for typing only, because the API image lacks the replay extra
(`tests/scoreboard/test_lean_import.py`).

### The operator track

The append-only decision log (`operator_decision`) joined to the episodes it answered
(`scoreboard/decisions.py`). Voided decisions are left out of all math and counted; decisions after the
as-of are ignored.

- Took: the earliest take-to-close span whose take falls in an episode's window on the same name fills its
  `operator` slot. A logged fill price wins; a missing one falls back to the close, flagged `inferred`. No
  counterfactual or delta fields: the row shows the record's return and the user's side by side.
- Passed: a pass inside an armed window fills the slot when no take did; a thesis-level pass lands on the
  headline episode.
- No decision: `operator` stays null, the visible capture gap.
- Off-record spans: a span that answers no episode rides `operator_spans` with the stance frozen on its take
  row; `override` is true when that stance was neither armed nor managing. A thesis-level take stays
  unpriced, never guessed onto a name.
- Anomalies: a log shape the API should have prevented surfaces as `decision_anomaly`; the pairing never
  repairs the log.

### Record freshness

`GET /scoreboard` carries `record_edge` (the newest as-of in the whole `calls` log, not capped by the
request, reconstructed rows included), `days_behind` and `stale`, measured against the last expected session
and run (`pipeline/schedule.py`, the contract the Admin page shares). The edge is platform-wide because it
describes the cron, which walks every workspace; Edition 0's first and last nights are the caller's
workspace's. The page shows the line on the live view only, and loud only when stale.

## The page

The ledger shows each thesis as a heading with its episodes below; off-record operator spans follow them.

- Summary and Timing views (`LedgerHead.tsx`) swap the middle columns; `rows.ts` holds the column sets.
  Armed and De-armed are separate columns; the censored-start mark stays on the arm date.
- A new column goes in four places: `LedgerHead`, `EpisodeRow`, `SpanRow` (in `Scoreboard.tsx`, the one
  most often missed) and `ledgerColCount`. A short row renders narrow and shifts its cells without an error;
  `Scoreboard.test.tsx` walks every body row in both views against the head.
- Sorting (`sortLedger.ts`) re-orders rows inside their thesis heading; headings never move and spans sort
  among themselves. A key is read through the same access path and dash guard as its cell, so the ranking
  matches what is shown. Nulls sort last in both directions; a sort never removes a row. Each host (the
  Scoreboard, the backtest run ledger) keeps its own sort state.
- The filter chips (`filters.ts`) are the one control that hides rows: none is on by default, the hidden
  count is always shown, a thesis keeps its heading with an "N of M" count, and each chip counts over the
  whole record (WB#2).
- The late-nights and reconstructed-nights lines and the include-late toggle (`late.ts`) change this view
  only.
- The ledger box `.sb-scroll` is separate from the Cockpit's `.basket-scroll`. Its height cap,
  `--sb-scroll-max` in `index.css`, is the one knob that lets both sticky headers work; the sticky rules
  target cells, not rows.
- The excursion columns pair each close-based figure (`peak_return`, `trough_return`) with its intraday
  wick (`intraday_high_return`, `intraday_low_return`). A wick field is all-or-nothing: one bar missing its
  high or low nulls that field, which never falls back to the close (invariant 6). `trough_return` reads a
  real 0.0 when the name never closed below entry. Each hover describes only its own figure.
- The path sparkline (`EpisodeSparkline.tsx`) draws the scored window's closes, from the same read as the
  excursions, at variable length: episodes share no time axis, so a steeper line is not a faster move. A
  de-arm outside the window draws no tick, and the hover says why.
- No descriptive field moves a metric: `compute_metrics` reads a fixed set in `replay/metrics.py`.

A row click opens the drill-down drawer (`components/Drawer.tsx`); the row's arrow icon opens the Cockpit.

- Four timing lenses (`EpisodeScorecard.tsx`) show `Outcome` fields the row cannot fit; they compute
  nothing new.
- The run-identity caption names the recording row's policy hash, run kind and image
  (`provenance.run_identity_note`; full values on hover). It flags and excludes nothing; for a row recorded
  before the stamp existed it is absent, not dashed.
- The chart (`GET /scoreboard/price-window`, loaded on demand) draws closes with SMA 50 and 200 context and
  numbered event chips: insider buys and sales, the arm's triggers, the lifecycle moments. Each insider trade
  carries a server-classified `character`; a set-aside one is grayed and labeled, never hidden, and the
  counted chips reconcile with the NamePanel's net flow. A counted buy that the phrase list locates as a
  private purchase gets one quiet line, never grayed (`domain/insider_private_purchases.py`). The event floor
  (`scoreboard/overlays.py`) bounds off-story events, never recall.
- No lookahead on either axis: bars are capped at the as-of, and an insider trade is placed on its
  transaction date but shown only once public, on the public clock for a past as-of and the record clock
  for today (`domain.market_time.known_at_for_asof`). 8-Ks and 13D/Gs are gated by their public date too.
- Events are numbered 1..N once over the loaded range, a stable id shared with the event ledger below
  (`EventLedger.tsx`): row N is chip N. Crowded chips stack into a visible "+N", never a silent drop.
- The record trail (`transitions`: verdict, entry grade and conviction grade changes inside the run) is an
  un-numbered list below the event ledger; the numbered ledger stays chip-events-only.
- `dearm_detail` composes the why behind a `dearmed_other` close; `risk_events` are the member's fired risks
  over the closed run, deduped by kind and date, a chip family distinct from sell facts.

The edition switcher and the "what changed" drawer are in [EDITIONS](EDITIONS.md).

## Contracts

- Compute on read: the Scoreboard owns no tables, writes nothing and runs no model
  (`tests/scoreboard/test_record.py::test_scoreboard_writes_nothing` counts the tables).
- Keep `CallCard` changes additive. The log outlives schema changes and `DomainModel` forbids extra fields,
  so an old card must stay loadable; a thesis with an unreadable card shows a per-thesis `error`, never a
  blank page.
- `/backtest` renders its drill-down with this page's components (`LedgerHead`, `EpisodeRow`,
  `EpisodeScorecard`, `MetricsStrip`) and builds its ledger with
  `scoreboard/replay_snapshot.build_snapshot`, so a row has one definition. Three rules hold:
  1. The wire shape is shared: `ScoreboardEpisodeOut` serves both pages, so a field added for one arrives on
     the other.
  2. Backtest-only fields (co-arm counts, the key source, the confirmation grade at arm) ride the backtest's
     own response, joined on `(thesis_id, security_id, arm_date)`, never on `ScoredEpisode`.
  3. Nothing flows back: the Scoreboard's numbers are the record's, a backtest is a recompute, and the two
     are never pooled or linked.
- Wire shapes: `backend/openapi.json`.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `GET /scoreboard`, `GET /scoreboard/price-window` | anyone signed in, own workspace; a thesis in another workspace is a 404 | `require_user` on the router (`app/main.py`), `get_workspace`, `get_thesis_or_404`; `tests/app/test_auth_gate.py`, `tests/app/test_workspace_isolation.py` |
| `GET /record/start` | anyone signed in, own workspace | the same; `tests/app/test_record_start_api.py` |
| Logging a decision (`POST /theses/{thesis_id}/decisions`) | a user, on a thesis in their workspace | `get_thesis_or_404` |
| Views, sort, filter chips, include-late toggle | the viewer, this view only | page state |
| The late-only nights line on the Admin page | steward | `require_super_admin` |
| `python -m scoreboard.run` | operator or developer; reads every workspace, writes nothing | none: a host run with no `DATABASE_URL` reads production |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `scoreboard_exclude_late` (`ALPHADECK_SCOREBOARD_EXCLUDE_LATE`) | `domain/settings.py` | no |
| `scoreboard_default_edition` (see [EDITIONS](EDITIONS.md)) | `domain/settings.py` | no |

## Known limits

- The withheld-arm metric's warming-run timelines are not provenance-filtered; episodes are the provenance
  unit.
- The earliest record rests on the July 2026 cache freeze. `freeze_era` and `thaw_lag_days` mark those arms;
  they stay on the ledger.
- Admin's per-night coverage and this page disagree on late-only nights by design; Admin names them.

## Related

[EDITIONS](EDITIONS.md) · [REPLAY](REPLAY.md) · [BACKTEST](BACKTEST.md) · [FEED_LOOP](FEED_LOOP.md) ·
[ADMIN](ADMIN.md) · [CALL_LOGIC](CALL_LOGIC.md) · [INVARIANTS](INVARIANTS.md) (1, 6, 13) ·
[Track Record](dashboards/track-record.md)
