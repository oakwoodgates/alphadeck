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
**The episode drill-down: built** — the row-opened drawer + four timing lenses + Summary|Timing toggle
(#227–228), the row/icon split (row → drawer, ↗ → Cockpit; #230), the episode price chart with a numbered
event overlay (SMA context + insider/trigger/lifecycle chips, relevance-floored + as-of-correct on both axes;
Slice A), and the event ledger sharing the chart's numbering + a Cockpit strip (Slice B). See
§"The episode drill-down".

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
Friday has no entry bar until the next trading close lands) — awaiting data, not an error. It ALSO
covers a scored window that held no bar at all: an episode whose `arm_date` and `exit_by` fall on the
same non-trading day has nothing between them to measure, so it reports no return rather than one
built out of an entry and an exit that are not in the same window (see "the exit read" below). On a
MATURED episode the ledger says **"no bars in the scored window"** rather than "awaiting first bar" —
the window has closed and no later bar can enter it, so there is nothing to await.

**`truncated` = the episode's horizon extends past the end of THIS NAME's price tape**
(`exit_by > tape_edge`, where `tape_edge` is the last bar for that name within the reader's own as-of
cap). One condition, and it asks the tape rather than a calendar.

It used to be `exit_date < exit_by`, which collapsed four unrelated situations into one flag: a
still-running episode (structural — an immature episode's window is capped at the as-of, so it was
*always* true and carried nothing `matured == false` did not already say); an `exit_by` that landed
on a Saturday, Sunday or market holiday (nothing missed — there is no Sunday close and no later bar
will ever change the number); a genuinely dead tape (the one case worth acting on); and a stale
per-name ingest, which it could not detect at all because it never asked about the tape. It fired on
**91% of episodes**, and every matured fire was a weekend or Labor Day — it had never once fired for
the reason it was documented for.

The tape-edge rule answers the trading-day question **without a calendar**: a Sunday `exit_by` in the
past sits *before* a live name's tape edge, so the tape itself proves the horizon was covered; a
market holiday resolves identically, with no holiday table to rot every January; a delisted or
stalled name's edge sits *before* its horizon and reads true, which is the flag doing its actual job.
Immature episodes stay `true` — load-bearing, because `moveNote`, `peakTimingPhrase` and the chart's
"last bar" exit marker all anchor their phrasing on it.

The **badge is a separate question from the field**. The field answers *"did the measurement reach the
horizon?"* — a fact. The badge answers *"is that worth telling the operator?"* — a judgement. It gates
additionally on `matured` and reads **`TAPE ENDS`**: a running return short of its horizon is the
definition of running, while a *realized* one short of its horizon is a caveat on a number presented
as final. On the current record it fires **zero** times, which is the honest count — 99 securities in
the master have tapes that stop before 2026-08-01, none of them carrying an episode yet.

`truncated` is **not** a metric input. Eligibility is `matured & !censored_start & !ingest_flagged`
and the metric filter is `!insufficient_prices & forward_return is not None`; neither mentions it, and
nothing in `replay/metrics.py`, `scoreboard/assemble.py`, `domain/` or `calls/` reads it. `Outcome` is
computed on read and never persisted, so none of this is near the cron's `record_if_changed`.

**The exit read.** An episode's exit is the last bar of its own scored window `[arm_date, exit_by]`,
never the last bar anywhere `<= exit_by`. The two agree whenever the window holds a bar and diverge in
exactly one shape — when the last bar `<= exit_by` *precedes* `arm_date` — where the unbounded-below
read paired a later entry with an earlier exit and served a return measured **backwards in time**. Two
episodes on the record hit it (armed on a Sunday with `exit_by` the same Sunday: `market_today()` does
no weekend skip by design, so a Sunday backfill records a Sunday as-of and `derive_episodes` takes
card as-ofs as episode boundaries), one of them inside the live metrics as an adverse arm.

**A de-arm can postdate the scored exit, and that is the horizon working.** Seven episodes have
`exit_by < dearm_date` — the signal-validity horizon elapsed while the record kept the member armed.
`score_episode` scores `[arm_date, exit_by]` by design, so `exit_date <= exit_by < dearm_date` follows
by construction and `exit_vs_peak_days` can read negative. It is the documented "open-but-matured"
shape (`test_maturity_judged_only_at_exit_by` pins it), not a bug; those are also the episodes whose
de-arm marker has no place on the sparkline path (`dearm_index` is null, and the hover says so).

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

## The ledger table itself

Three structural notes for anyone editing it.

**It wears the `.basket` skin but it is NOT the Cockpit's contained table.** The Cockpit's containment
machinery (`min-width: max-content`, its sticky header, its sticky identity columns) is scoped to
`.basket-scroll` precisely so it cannot reach here — unscoped, it overflowed this page by 498px (see
`BOARD.md`). The ledger has its own box, `.sb-scroll`, with its own rules; the two are deliberately
separate even where they agree.

**Both headers stick, and the height cap is the price.** `.sb-scroll` caps its height at
`--sb-scroll-max` (**the one knob** — set it to `none` and everything below reverts). That cap is what
gives the box a vertical scroll of its own, and a sticky `<thead>` can only stick to the box that
actually scrolls it: per spec `overflow-x: auto` computes the other axis to `auto`, so `.sb-scroll`
was already a scroll container on both axes and the page was never the header's scrollport. The cost
is real and was accepted knowingly — a nested vertical scroll inside a flowing document, with the
metrics strip above it and the replay panel below — because the alternative on a ~10,000px record is a
column header that is off-screen for all of it. The thesis heading sticks **below** the column header
(`top: var(--sb-head-h)`, z-index 2 vs the header's 3), so a heading scrolling up slides *under* the
header rather than over it. `position: sticky` on a `<tr>` is unreliable — both rules target the
**cells**.

**Every column must be added in four places**: `LedgerHead` (the `<colgroup>` + `<th>`s), `EpisodeRow`,
`SpanRow` (in `Scoreboard.tsx` — the off-record operator spans, the one most easily missed), and
`ledgerColCount` (which the group/note rows' `colSpan` tracks). Miss one and that row's cells sit a
column off their headers, silently — a short `<tr>` just renders narrow. Pinned by a test that walks
every body row in both views and compares its colSpan-weighted cell count to the head's.

### Sorting — a within-group re-order, never a flattening

`sortLedger.ts` (pure, unit-tested, beside `rows.ts` the way `cockpit/sortBasket.ts` sits beside
`buckets.ts`) holds the comparators; the two hosts — `Scoreboard` and `ReplayPanel` — each hold their
**own** sort state, because the replay set is a recompute and the two are never pooled.

Sortable: **Name · Armed · De-armed · Return · Peak · Peak high · Worst · Worst low · Past peak**.
Not sortable, deliberately: **Path** (a shape has no honest scalar), **Status** (a badge set has no
ordering the record gives it), and the Summary-only **Why** / **Operator** / **Exit-by**.

Three rules, inherited from the Cockpit's sort because they are the same rules:

1. The group is the **spine** — rows re-rank *inside* their thesis heading and the headings never move.
   Operator spans re-rank among themselves, never interleaved with the arm episodes above them (a
   logged take is not an episode).
2. A key is read off the **same access path and the same dash guard** as the cell, so the ranking always
   matches what the operator sees — `noForwardBar` dashes the whole timing lens, a wick is
   independently absent under the all-or-nothing rule, and a real `0.0` still ranks.
3. **Nulls last in both directions** (a missing measurement is not a small one) and the cycle is
   reversible: desc → asc → off, back to the record's own order. A sort is a re-order, never a filter —
   no row can be sorted off the ledger (#9).

## The episode drill-down: drawer, chart, ledger

Every ledger row opens a **drill-down drawer** (`components/Drawer.tsx` — a reusable slide-out, ~600px with
expand-to-full-screen; ✕/backdrop/Esc close, focus returns to the opener; read-only, a sibling overlay so
opening it never re-renders the ledger). A **row click** opens it; the row's **↗** icon is the distinct jump
to the fuller per-name Cockpit (#227–#230). It surfaces the per-episode `Outcome` fields the row can't fit.

**Four timing lenses** (`EpisodeScorecard.tsx`) — all from already-computed `Outcome` fields (it surfaces
more of what is computed; it changes no computation): *The move* (entry/exit close + the return — the
"show the prices" gap), *Horizon calibration* (`peak_return`/`peak_date`/`exit_vs_peak_days` — was `exit_by`
well-timed? hidden without a peak), *Edge preservation* (`warm_return` vs `forward_return` — armed in time or
missed the early move?), *Entry window + setup* (`arm_until_return` + grades + setup strength). A
**Summary | Timing** column toggle (`LedgerHead.tsx`) swaps the ledger's middle columns so one timing lens
can be scanned down the whole universe (#228). Summary is **9 columns** —
`Name · Armed · De-armed · Why · Exit-by · Status · Return · Peak · Operator`. Timing is **11** —
`Name · Armed · De-armed · Path · Return · Peak · Peak high · Worst · Worst low · Past peak · Status`.

**Armed and De-armed are two columns**, not one cell reading `Aug 7 → Aug 11`: they are two
measurements, and packed together they could be neither scanned down nor sorted on. The censored-start
`*` stays with the **arm** date — it is the arm that is unknowable. A still-open episode's De-armed
cell reads `—`; so does a `SpanRow`'s, because an operator span is a logged take, not an arm.

### The excursion pair — how far it went each way, not just where it ended

A row used to say where an episode ENDED and how high it got, so a name that went straight up and a name
that was 12% underwater before recovering rendered **identically**. MEASURED on the record: of 98 episodes
that ended positive, **22% first drew down worse than −5% on closes** and **58% dipped worse than −5%
intraday**. The ledger could not tell "the call was right and easy" from "the call was right and would have
stopped you out" — which is the operator's stated weakness (timing), and something `false_arm_rate` cannot
see because it only looks at the endpoint.

Two bases, six `Outcome` fields, **one read**:

| field | basis | meaning |
|---|---|---|
| `peak_return` / `peak_date` | close | **MFE** — maximum favorable excursion |
| `trough_return` / `trough_date` | close | **MAE** — maximum adverse excursion |
| `intraday_high_return` / `intraday_high_date` | wick | the best price that actually traded |
| `intraday_low_return` / `intraday_low_date` | wick | the worst price that actually traded |

**All four are columns**, each close figure adjacent to its own wick so the pair reads together:
`Peak · Peak high · Worst · Worst low`. The close pair anchors the row — `forward_return` is close-based
and `exit_vs_peak_days` is anchored on the close-based `peak_date`, so Return · Peak · Worst read as three
numbers on one tape — and the wick sits beside each, set one tone quieter (`.sb-wick`) because the close
is the basis and the wick is the check against it.

The wick pair used to ride the close cells' hovers, on the grounds that MEASURED it differs from the close
on 96–97% of episodes but by a median of only **+2.0pp / +2.1pp**, and a 2pp correction is something you
*check*, not *scan*. **The operator overruled that** and asked for all four visible. Promoting them was a
pure FE change (the wire already carried all four). It came with a second half: each of the four hovers now
describes **only its own figure** — a hover that restated its neighbour would be exactly the repetition the
columns were promoted to remove, and the neighbour's own cell answers for the neighbour.

Three honesty rules:

- **Wick fields are all-or-nothing, per column.** One bar missing `high` nulls `intraday_high_*` entirely.
  The field asks *"what is the most extreme price this actually traded at?"* — if one bar's extremes are
  unknown, the true extreme could be **inside** that bar, so an extreme over the remaining bars is not
  conservative, it is **wrong**, and silently so (#6). A wick field **never** falls back to the close.
- **`trough_return` reads a real `0.0`**, not null, for a name that never closed below entry — **24% of the
  record**. `0.0` is the measurement; null would claim ignorance about a number we know exactly.
- **The two adverse figures never agree on "untouched".** MEASURED: `intraday_low_return` reads 0 on
  **0 of 250** episodes (its maximum across the whole record is **−0.2%**) — every name traded below its
  entry close at some point intraday. Only the *close* MAE can ever say "it never went against you".

### The episode path — the shape behind the endpoints

`path` is the scored window's closes, ascending; `dearm_index` is the de-arm's slot in it. Both come off the
**same `bars_between` read** as the excursions — the excursion window and the sparkline path are the *same
list*, not two reads that agree. `score_episode`'s window read swapped `closes_between` → `bars_between`:
same rows, same query count, four more columns.

The ledger cell (`EpisodeSparkline.tsx`) reuses the Cockpit's `sparkGeometry` (pure, unit-tested, breaks on
a gap rather than bridging it, floors at two real values). It is **variable-length**, which deliberately
**inverts** the Cockpit's fixed-slot choice: the Cockpit pads every name to one shared 90-bar window so
shapes are comparable down the column, but Scoreboard episodes share **no** window (median **13** bars, max
**42**, and 75 of 131 securities carry more than one episode). Padding them to a common width would draw a
13-bar episode as a stub beside a 42-bar one and **imply a shared time axis that does not exist**. The cost
is real and goes in the hover: **a steeper line does not mean a faster move.**

`dearm_index` is **null when the de-arm fell outside the scored window** — the real **8-episode** case where
the horizon elapsed while the record kept the member armed (see the de-arm note below). The marker is simply
not drawn there, and the hover says *why*: a silently absent tick would read as "never de-armed", which is
false.

**A de-arm can postdate the scored exit, by design.** MEASURED: **7 episodes** have `exit_by < dearm_date`.
`score_episode` scores `[arm_date, exit_by]`, so `exit_date ≤ exit_by < dearm_date` follows by construction —
that is the open-but-matured shape `test_maturity_judged_only_at_exit_by` already pins, not a defect.

**None of these fields moves a metric.** `compute_metrics` reads `forward_return`, `warm_return`,
`peak_return`, `exit_vs_peak_days`, `entry_grade`, `is_headline`, `close_reason` and `insufficient_prices`;
the excursion and path fields are descriptive. `Outcome` is computed on read and never persisted, so this is
nowhere near `record_if_changed`.

**The reader asymmetry is preserved, not harmonised.** `PgRealizedPrices.bars_between` keeps its double cap
(`d <= cap` on the valid axis, `recorded_at <= known_at` on the transaction axis); the DuckDB
`RealizedPrices.bars_between` stays deliberately **forward-unbounded**, as the scoring pass requires and as
`_closes` already is on that side. No as-of read is widened — the new read sees exactly the universe
`closes_between` already saw, and `test_ohlc_window_and_path_respect_the_asof_cap` pins that a bar past the
cap reaches neither the excursions nor the path.

### The episode chart — a numbered event overlay (Slice A)

The *move* lens mounts a **price chart**: an on-demand read (`GET /scoreboard/price-window` — the SAME
asof-capped `PgRealizedPrices`, served on request so the ledger payload stays lean; lightweight-charts,
lazy-loaded). It draws the **close line + faint SMA 50/200 context**, and overlays the episode's **recorded
events** as **colored numbered chips**:

- **What rides on it** — insider **code-P buys** (`fact_insider_txn`), the **arm's triggers**, and the
  **lifecycle** moments (warm/arm/dearm/exit). Each buy carries a server-classified **`character`**
  (Band 03 S2c — the display rail's `_screen`, the same predicates the NamePanel's `_is_open_market_buy`
  composes): `open_market` (the unbadged norm — "passed the available screens", never "proven
  discretionary") · `self_filing` (labeled — still counted in the panel's net-flow; that re-base is
  deferred) · `primary_market` / `implausible` (**set aside** — greyed chips + muted ledger rows, present
  and labeled instead of hidden, WB #2/#9). The **counted** (non-set-aside) dots reconcile with the
  NamePanel's net-flow figure.
- **The relevance floor** — the loaded universe is `[max(thesis.created_at − 365d, first_bar), now]`: a thesis
  born in 2026 does not plot a 2020 buy. A *relevance* bound on off-story events, never a recall cut (#9); the
  server owns it and echoes the effective floor.
- **Stable unified numbering** — the events number chronologically **1..N once over the whole universe** — a
  STABLE per-event id, identical across the compact/expanded views and shared with the ledger. The default
  visible range is the recent episode; **pan/zoom** reveals earlier events and de-crowds dense clusters
  (collision-stacking spills to a visible "+N", never a silent drop).
- **No-lookahead on both axes** — an insider buy is **positioned by its transaction date** (`valid_from`) but
  **gated by disclosure** (`recorded_at ≤ known_at`, capped at the as-of), so a scrubbed-back Scoreboard hides
  later bars AND later-*disclosed* buys — the honesty a filing's days-to-months lag demands (the price bar's
  `valid_from == d` needs only the valid-axis cap). SMA is a warm-up read with an honest `None` gap where
  history is short (`scoreboard/overlays.py`).
- **Explainability (#6)** — hover any chip for the buyer/$/date, the **disclosure lag**, the buy's
  **character line** (why it did or didn't count; open-market stays unbadged, the 10b5-1 plan note renders
  only on an explicit `true` — tri-state), and the market-price context (the close that day, % vs now),
  with a guide-line to the price point; every chip traces to a recorded row. A legend names only the
  present families (#7).

### The event ledger + Cockpit strip (Slice B)

Below the chart, an **event ledger** (`EventLedger.tsx`) lists the **SAME numbered events** — **row #N is
chip #N** (one shared array, built once in `EpisodeScorecard`, never re-numbered). It is the *complete* list,
the antidote to the chart's focused view (which hides off-view chips and clusters dense ones into "+N"). Hover
a row → its chip rings; hover a chip → the row tints (a lifted `activeN`, kept OUT of the chart's canvas
effect, so highlighting never rebuilds it).

A **Cockpit strip** brings over *some* per-name context without duplicating the NamePanel: an identity line
(archetype · sector · exchange · market-cap, from the workbench-scored read) + the present-only display-signal
**headlines** (SMA position · 52-week range · volume regime · insider-flow 90d, reusing the Cockpit's
`DisplayHeadlineRow`). For a **closed** episode those trailing windows are labeled **"current tape · as-of X"**
— a name-current 90d figure is not the episode's own period. Read-only, FE-only (reuses the workbench-scored
and display-signals endpoints; no new backend). The finer intra-run calls-log transitions have no chip — the
numbered ledger stays chip-events-only; they surface as the un-numbered **record trail** below it (Slice C3,
the `transitions[]` field — see §"Episode enrichment").

### Episode enrichment (Slice C)

Two additive episode fields feed the drawer from the recorded cards themselves — dict lookups over
the cards `derive_thesis_record` already holds, no new queries. Both DEFAULT on the replay path
(`CallSnapshot` deliberately drops risk/missing detail — never widened for this), so an old replay
artifact parses to the defaults and the endpoint never 500s:

- **`dearm_detail`** — the composed WHY behind a `dearmed_other` close, the ONE opaque token (the
  other four tokens self-explain; composing under all five would be noise, #7). Backend-authored
  copy, one authority (the `ingest_note` precedent), from the de-arm-day card in priority order:
  the member's own fired risk labels (first 2, joined) → `now missing: <missing[0]>` (deliberately
  BEFORE the state phrase — `missing` is non-empty exactly when a key un-turned, so state-first
  would make it dead code) → `thesis fell back to Warming/Incubating` → `left the armed set (thesis
  still Armed)` (the member-only de-arm) → nothing. The FE renders it on the drawer's close-reason
  line and the de-armed ledger row/tooltip as "de-armed — ‹detail›"; the raw wire token stays one
  hover away in `title=`.
- **`risk_events`** — the member's fired risk signals over the CLOSED interval `[arm_date,
  dearm_date or the record edge]` (arm day included: a risk live AT arm haircut the arm's own setup
  strength, and omitting it would show a clean tape for an arm the record itself discounted;
  de-arm day included: its risk is what ended the run — exactly what `dearm_detail` reads), DEDUPED
  by `(kind, event_date)` — a live risk re-fires on every daily card under a stable fact-anchored
  event date; different kinds on one day stay distinct (#9). A dateless (legacy) risk is stamped
  with the first card-asof it appeared on — a recorded fact (when the record first said it), not
  the market event date. On the chart/ledger they ride as the quiet **risk** chip family (hollow
  muted negative — deliberately distinct from the `sell` FACT family: a fact is what the tape did;
  a risk signal is what the record made of it, so one sell cluster appearing as both is by design).
- **`transitions`** (C3) — the member's intra-run RECORD changes over `[arm_date,
  last_armed_date]`: consecutive-card diffs of **verdict / entry_grade / conviction_grade only**
  (never the daily confidence wobble). The arm card is the baseline (the episode already carries
  the at-arm values); a change across a weekend/cron gap lands on the LATER card's asof — a
  recorded fact, the same stamp rule as `risk_events`. Rendered as the quiet un-numbered **record
  trail** below the event ledger — deliberately NOT chips and NOT in the 1..N array (the numbered
  ledger stays chip-events-only).

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
accrues) · persistence/caching of scores · cron changes · notifications · the second
recalibration (unlocked by this, not part of it) · a transaction-time (`known_at`) scrub
parameter · stitching replayed and recorded episodes across the seam (noted, never merged).
*(Charts moved from this list to built — the episode drill-down chart, Slice A; the chip-less
intra-run "record trail" likewise — Slice C3, §"Episode enrichment".)*
