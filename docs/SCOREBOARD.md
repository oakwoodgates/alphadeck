# The Scoreboard — SCORE, the forward trust loop

The sixth stage: forward attribution over the platform's own record. Two tracks in v1 — **the
platform's calls** (the daily call-of-record, scored against realized prices on its own yardstick)
and **the operator's decisions** (the append-only decision log, joined to the episodes it answered).
The follow-blindly counterfactual track and its deltas are **v2** (additive on the same computation);
the immediate follow-up after v1 is surfacing **replay's historical episodes alongside** the live
record (clearly separated — the record stays clean). "The platform feeds itself" became true at M2;
*this* is the instrument through which forward evidence accrues. Its existence — or a small sample crossing
a UI gate — does not by itself make the platform "validated forward."

> **Freshness caveat on the early record.** The record began when the daily cron first wrote (2026-07-10 in
> production), but the cron's insider data was **frozen** on a cache-first-forever EDGAR cache until R1's
> key-classed 12h TTL (#196, 2026-07-17). So "feeds itself" became true as a *record* at M2 but as *fresh
> insider data* only at #196 — the earliest cards were built on stale insider indexes. Full account:
> `POSTMORTEM_CRON_FREEZE_2026-07.md`. **Now marked per-episode (Slice 3):** the record-provenance flags
> below carry this caveat onto each episode (`ingest_flagged` + the INGEST badge) — freeze-era and
> thawed/partial-ingest arms stay ledger-visible but are excluded from the aggregate metrics.

Status: **v1 built** — SB1 (the scoring engine + CLI) + SB2 (`GET /scoreboard` + gated metrics) + SB3
(the operator track) + SB4 (the FE view: the ledger behind the Scoreboard nav, `frontend/src/scoreboard/`).
**RH (replay-alongside): built** — RH-A (the snapshot CLI + `GET /scoreboard/replay`) + RH-B (the FE
historical section: collapsed-by-default below the live ledger, `frontend/src/scoreboard/ReplayPanel.tsx`).

## The one rule everything hangs on

**The record is the scoring source — never a recompute.** The Scoreboard reads the immutable `calls`
log (what the platform actually said, when it said it) via `calls_repo.latest_for_thesis` (the
final card per as-of), and scores those cards. Re-deriving past calls with today's code/dials is
replay's job (`docs/REPLAY.md` — the historical twin); attribution's source is the record
(`docs/BOARD.md`). Consequences, all deliberate:

- **No backfill.** The record began when the daily cron first wrote (2026-07-10 in production);
  earlier history is replay's domain. An empty early Scoreboard is the honest launch state.
- **Censored starts.** An episode already armed on its thesis's *first recorded card* has an
  unknowable true arm date (`censored_start`) — shown in the ledger ("record began mid-arm"),
  **excluded from arm-anchored metrics**, never reconstructed.
- **Gaps are fine.** The log is dense per cron day (a new as-of always appends; `record_if_changed`
  dedups same-as-of only); weekends/downtime leave gaps, but episode boundaries stay exact because a
  membership change always recorded a row that day. `derive_episodes` consumes the gapped timeline
  as-is.

## The scoring unit and its flags

The unit is replay's **arm episode** (`replay/episodes.py::derive_episodes`, reused as-is): a
contiguous run of one basket member in `armed_members`, keyed `(thesis_id, security_id, arm_date)`,
scored by `replay/scoring.py::score_episode` over `[arm_date, exit_by]` — the system's own
**signal-validity horizon**, the honest yardstick. It is not a mandatory trade exit or sell-by date. The
live additions (`scoreboard/schema.py`) are honesty about the
record, per episode:

| Flag | Meaning |
|---|---|
| `status` open/closed | open = still armed at the record edge ≤ asof (replay's `window_end`, read live); its return is a RUNNING return, not a verdict |
| `matured` | the episode's own `exit_by` signal-validity endpoint has elapsed (≤ asof). **Metrics judge only matured, non-censored, clean-ingest episodes** — a running return must never drift inside `false_arm_rate` before the scoring window ends |
| `censored_start` | armed since before the record began (above) |
| `arm_ingest_fresh` | **provenance A (run stamp):** the arm-date row's ingest health (migration 0023, cron R2b), read raw off the same winning row the scored card comes from — `false` = the arm rested on a PARTIAL ingest; `NULL` (legacy/manual append) is never coerced to a judgement |
| `freeze_era` | **provenance B1 (freeze window):** the arm falls inside the 2026-07 EDGAR cache freeze `[2026-07-10, 2026-07-17]` (`provenance.FREEZE_WINDOW`, dates per the postmortem) — the cohort-level marker B2 cannot see: an arm inside the window may rest on promptly-ingested older facts while the frozen index hid newer filings |
| `thaw_lag_days` | **provenance B2 (derived thaw marker):** max calendar-day ingest lag — first `recorded_at` vs latest `valid_from` — across the arm triggers' cited form4 accessions (`fact_insider_txn`'s bitemporal axes; the derivation the 0023 comment promises). Beyond `THAW_LAG_DAYS = 7` marks a thawed-late arm; `NULL` = no form4 sources or no fact rows (unknown, un-flagged). Deliberately also flags arms resting on facts backfilled at basket-add time — same semantics |
| `triggers_at_arm` | the arm-date card's member trigger evidence — the WHY rides every row (invariant #6) |

The three mechanisms roll up into `ingest_flagged` (+ the backend-authored `ingest_note`, the one
authority for the "why"): partial stamp OR freeze-era OR thawed-late. A flagged episode carries the
**INGEST** badge and is **excluded from the aggregate metrics, ON by default (no toggle)** — the
same conservative posture as `censored_start` — while staying **ledger-visible always** (the
recall-is-sacred cousin: nothing drops from the ledger). The banner's eligibility parenthetical
reads `matured + non-censored + clean-ingest`. Consequence, stated plainly: the launch record is
12/12 freeze-touched, so the metrics stay honestly empty until the first clean-data arm matures.
The flags are composed AFTER `score_episode`, from reads the scoring path never sees
(`scoreboard/provenance.py` imports nothing from `calls/`, and nothing on the call/write path
imports it) — a clean, flagged, and legacy-NULL episode score identically, pinned by test.
**Named limitation (deliberate):** the withheld-arm metric's warming-run timelines are NOT
provenance-filtered — episodes are the provenance unit.

The summary also carries a **maturity horizon** (2e), turning the mute "0 eligible" gate into a
countdown: `next_maturity` (the earliest FUTURE `exit_by`, ledger-wide — every episode is still
judged at its own deadline), `n_maturing_30d`, and `projected_min_n_date` — the date the ELIGIBLE
pool could reach `MIN_N`, counting only non-censored, non-flagged future maturities (a flagged
immature episode does not advance it); `null` when already cleared or not reachable from current
episodes. Asof-pure, derived from episodes already in hand; **a projection over currently-recorded
episodes, never a promise** (new arms or de-arms shift it). The FE renders it as one quiet line
beside the metrics gate.

`Outcome.insufficient_prices` on a fresh arm means "no bar on/after the arm yet" (an arm recorded
Friday has no entry bar until the next trading close lands) — awaiting data, not an error.
`truncated` = the signal-validity horizon ran past the available (asof-capped) bars: the running-return shape.

A related **single-bar** case gets its own honest label (Slice 2, #209): when the ONLY bar on/after the arm
is the arm-day bar itself (`exit_date === arm_date`), `forward_return` is a degenerate `0.0%` over one bar —
**not a flat move** — so the ledger reads **"awaiting forward bar"** and shows `—`, distinct from
`insufficient_prices`'s **"awaiting first bar"** (no bar at all). Once a forward bar lands
(`exit_date > arm_date`) the return becomes a real (running) number, even if ~0%. The check
(`frontend/src/scoreboard/rows.ts::awaitingForwardBar`) runs AFTER the realized check, so a degenerate
matured single-bar episode still reads "realized" — it only overrides the "running" label. Same instinct as
the arm-day dash: never let a mechanical 0.0% read as a real return.

## Setup strength and the small-sample gate

The per-call display is **setup strength**; its stable wire field remains `confidence`. It is an experimental
relative read of trigger composition and risk penalties, **not a probability of success**. The legacy metric
slug `grade_confidence_calibration` asks whether grade/setup-strength ordering discriminates realized outcomes
monotonically; only matured forward outcomes can support that calibration.

`MIN_N = 5` / `insufficient_n` controls how early aggregate metrics are presented in the UI. It is a
**safeguard against over-reading tiny summaries, not an evidence threshold**: clearing `n ≥ 5` does not make a
metric conclusive, establish calibration, or convert setup strength into a probability. Sample composition,
per-bucket counts, censoring, and stability over a materially larger forward record still matter.

## Prices: the Postgres twin, asof-capped

`scoreboard/prices.py::PgRealizedPrices` is the Postgres twin of replay's DuckDB `RealizedPrices` —
the same three-method surface `score_episode` duck-types against, same latest-version-per-day dedup
and `recorded_at DESC, id DESC` tiebreak, plus two caps: `d <= asof` (the request as-of — scrubbing
the Scoreboard back can never see a later bar; open episodes' returns run to the last bar ≤ asof)
and `recorded_at <= known_at` (default now — a re-versioned/restated bar's latest version wins:
score against the corrected tape). A parity test (`tests/replay/test_pg_prices_parity.py`) pins the
two readers row-for-row equal, including the identical `Outcome`.

This required one 2-line enabler in replay: `replay/scoring.py`'s `import duckdb` moved under
`TYPE_CHECKING` (duckdb is the optional `.[replay]` extra, absent from the lean prod image; the
import was annotation-only). `tests/scoreboard/test_lean_import.py` pins that structurally.

## The operator track (SB3)

The decision log (`operator_decision` — append-only, "the Scoreboard's missing column") joined to
the episodes it answered. Voids resolve first (a voided decision is excluded from all math, still
counted in `n_voided`); the valid axis caps at the request asof (`decision_date <= asof`).

- **took** — the earliest take→close span whose take date falls inside an episode's window
  (`[arm_date, dearm or asof]`) on the same name fills that episode's `operator` slot. Prices: a
  logged fill always wins; a missing one falls back to the close, flagged `inferred`, never silent
  (entry = first close on/after the take — blind-entry parity; a running span's exit = last close
  ≤ asof). **No delta/counterfactual fields** — the row shows the record's return and the
  operator's side by side (deltas ride with the v2 follow-blindly track).
- **passed** — a pass inside an armed window fills the slot when no take did (same name; a
  thesis-level pass lands on the **headline** episode — Decision Queue semantics). No prices; the
  episode's own outcome sits beside it.
- **no decision logged** — an armed episode nobody answered keeps `operator: null`: the honest
  capture gap, rendered as such, never an error.
- **off-record spans / overrides** — a span answering no episode rides `operator_spans`, carrying
  the stance **frozen on the take row at logging time** (`call_state`/`call_verdict` — the record,
  not a recompute, is attribution's source). `override=true` when that stance was not
  armed/managing (`managing` means an operator-entered thesis is being monitored, not risk-managed):
  the gate's logged override, now with its outcome attached. A thesis-level take
  (no name) stays **unpriced** — visible, never guessed onto a name.
- **anomalies** — a log shape the API should have prevented (take-while-open, close-while-flat)
  surfaces as a per-thesis `decision_anomaly` note; the pairing never silently fixes the log.

## The historical panel (replay-alongside, RH)

The immediate post-v1 follow-up: replayed history in a **clearly-separated** section, so the page
has depth while the forward record accrues — without polluting it. Structure over trust:

- **An operator-kicked artifact, never live compute.** Replay needs the `.[replay]` extra (absent
  from the lean prod image) and takes minutes — so `python -m scoreboard.replay_snapshot` (dev
  venv) runs replay and writes ONE JSON artifact (`data/scoreboard_replay/latest.json`,
  latest-only: the snapshot is deterministic per (SoR, pin, window, cfg)). The app only READS it
  (`GET /scoreboard/replay`; `available:false` when absent/unreadable — never a 500). In compose,
  that one subpath is a **read-only host bind** over the appdata volume: the container serves the
  artifact but physically cannot write it. Cost stays the operator's to spend, never ambient.
- **The seam.** The window defaults to ending at `record_began − 1`: replay covers history, the
  record covers everything after — no double-counted arms. A replayed episode still armed at the
  seam (`window_end`) and a censored record episode on the same name are the same real arm, split
  at the seam (noted, never stitched). Pushing `--end` past the record is allowed but LOUD
  (`window_overlaps_record` + a banner warning), never silent.
- **A RECOMPUTE, labeled as one.** Today's code + dials over historical facts; baskets are not
  versioned (REPLAY.md's known limitation) — the caveat rides the banner permanently. Separate
  endpoint, separate section, metrics never pooled with the live summary.
- **The same honesty rules as the record**, so the two strips are comparable: `censored_start` on
  the window's first replayed day; `matured` against the data edge; metrics over matured ∧
  non-censored only; the WHY rides each episode from the arm-date snapshot (`MemberRow.triggers`,
  the one additive replay-schema change). Platform track only — decision capture post-dates
  history, so the operator column is structurally absent.

## Record freshness on the live view (Slice 2, #209)

The Scoreboard also answers **"is the call-of-record current *now*?"** — the same question the Admin page
asks (`ADMIN.md`), surfaced here because the Board-vs-Scoreboard confusion happened on this page.
`GET /scoreboard` carries `record_edge` (the **uncapped** calls-log `MAX(asof)` — independent of the request
as-of, so it reads the same whether the view is scrubbed to the past or to today) measured against the last
**expected** Mon-Fri + `RUN_AT` run (`pipeline/schedule.py` — ONE contract shared with the Admin surface,
never raw `today − edge`). The FE shows it **only on the live view** (`asof >= today`): staleness answers
"current now", not "as of a past date", so a scrubbed-back view suppresses it. It goes **loud only when
stale** ("record last advanced ‹edge› · N expected run(s) behind"); **quiet** when current or never-begun
(honest loudness, mirroring the Admin copy). Compute-on-read — the freshness read still writes nothing.

## Reading it

```powershell
python -m scoreboard.run --asof 2026-07-11              # the human-readable ledger
python -m scoreboard.run --asof 2026-07-11 --json       # the full analytical dump
python -m scoreboard.run --asof 2026-07-11 --exclude-archived
```

**Archived theses are INCLUDED by default** — archiving stops accrual (the cron skips archived); it
never erases the record. `--exclude-archived` (and the endpoint param, SB2) is the explicit,
reversible filter. Compute-on-read: the whole path owns no tables and writes nothing
(`test_scoreboard_writes_nothing` counts the tables to prove it), no LLM anywhere.

A thesis with an unreadable historical card (the log outlives `CallCard` schema changes;
`DomainModel` is `extra="forbid"`) surfaces a per-thesis `error` and never blanks the board —
keep `CallCard` evolution **additive-only** so old cards stay loadable.

## Deliberately NOT here (v1)

Follow-blindly track + deltas (v2) · a second metrics-led view behind a toggle (v2, once n
accrues) · charts · persistence/caching of scores · cron changes · notifications · the second
recalibration (unlocked by this, not part of it) · a transaction-time (`known_at`) scrub
parameter · stitching replayed and recorded episodes across the seam (noted, never merged).
