# Trigger Log

> Slug `trigger-log` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 210`.
> The operator approved the TRADING set (2026-10-29: "write the plans"); this page is the cross-card form of the
> "Trigger Anatomy" of the first sketch: one call's triggers are already taken apart by the Cockpit's call card and
> the Evidence Ledger (`docs/dashboards/evidence-ledger.md`), so this page takes apart the POPULATION of triggers
> instead. The choices the build makes that this plan does not spell out go under "Choices to confirm" for the
> operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`. The third page of the TRADING set.

**Label:** Trigger Log. **Description:** "Every entry trigger and risk signal the record shows firing across the
cards in the last 90 days, newest first, with its source, the state it moved, and what the name's tape did after."

It answers "what fired lately, and has it already run": the Board and the Cockpit show the triggers live on a card
TODAY; What Changed diffs two nights. Neither lists triggers as events over time. The log does, from the recorded
calls alone: a trigger is an event dated by the first night the record shows it, with its fire date, its source, the
card's state that night and the night before, whether it is still live, and the name's as-traded move since.
Display only: it never feeds a call, never writes, never calls the LLM, and it shows NO aggregate (a median move
"after insider triggers" would be an event-outcome statistic, which rule 6 slices by cap tier: that is the Track
Record page's job, `docs/dashboards/track-record.md`; this page lists).

## What a row is

- **The source rows:** for the cards counted at the as-of (`baskets.cards_as_of`), every `calls` row with `asof` in
  [as-of − window, as-of] (window 30 · **90** · 180 days), each night's winning row (the highest `seq`), plus the one
  row in effect the night before the window opens (so the first night's state change can be read). Reconstructed
  nights count as nights and are marked.
- **A trigger's identity** is (`security_id`, `kind`, `event_date`, `label`) across the card's nights; the same
  trigger on twelve nights is ONE row. It is read from three places on each night's card: `triggers_fired` (an entry
  trigger), `risk_signals` (a risk signal), and each `armed_members[]` / `watch_members[]` entry's `triggers`
  (member-level entry triggers; the same identity merges with the card-level one).
- **Its recorded night** = the first night in the window the record shows it. A trigger first recorded BEFORE the
  window is not a row (it is on the Hot Board if still live); the work line counts how many such live triggers the
  cards in effect carry.
- **Its fire date** = `event_date` (an insider's trade date, a breakout's bar date; `None` reads "undated"). When the
  fire date is before the recorded night the row notes "recorded N days after" (a Form 4 filed days after its trade;
  the public clock, invariant #1).
- **Its role** = entry trigger or risk signal, by the list it came from; **its key** = conviction or confirmation, by
  `kind` against `DEFAULT_CONFIG.conviction_kinds` / `confirmation_kinds` (`domain/config.py`; the revenue
  acceleration exception as the assembler reads it, `calls/assembler.py`), shown as "turns: conviction"; a risk
  signal shows "risk".

## The columns

Recorded night · fire date (with the "recorded N days after" note) · ticker (opens the Cockpit on this card and name,
`onSelectThesis(thesisId, ticker)`; the roster snapshot's ticker, else `master.tickers_for`) · card chip · role glyph
· kind · grade (`flip` · `core` · none for a risk) · label · **source** (the Evidence Ledger's rule: an accession
links to its EDGAR index through `dashboards/filings.py::source_url` with the issuer CIK; a `price:` ref shows the
bar date as text; detail lines on hover) · **state that night**: the card's recorded state on the recorded night
beside the night before ("warming → armed"; "new card" when the recorded night is the card's first row; unchanged
reads the one state) · **live**: whether the same identity is on the card in effect at the as-of · **sessions since**
the recorded night on IWM's bar calendar (`dashboards/series.py::session_calendar`) · **since**: the name's as-traded
return from the first close on or after the recorded night (the Scoreboard's entry rule) to the last close on or
before the as-of (`pit.price_history`), with IWM over the same span (`pit.benchmark_prices`); a name with no bar
since, or on the flagged-price list (`baskets.price_flags`, rule 5), reads "unpriced" / "flagged: <reason>" and keeps
its row.

**Grouping (a selector):** by **week** (default; the recorded night's Monday-started week, newest first; each group
header counts its rows by role), by kind, or by card. **Filters** (chips, each restating the count): role, kind,
grade, card, "live only", "state changed only". **Default order within a group:** recorded night newest first, then
ticker.

## Loudness

The trading set's tape look (`docs/dashboards/hot-board.md` §Look). Entry triggers and risk signals share one gray;
the role is a glyph, not a color. The one accent: a row whose recorded night is the as-of's own night (the newest
possible), so "fired last night" stands out of the week. The sign tone only on the signed "since" values. Neutral
wording; the state cell quotes the record's words.

## Past as-of

The window ends at the as-of; rows recorded after it are not read; the cards in effect for "live" are those at the
as-of; the bars end at the as-of.

## The quiet "show the work" line

The as-of; the clock; the window; cards counted / with nights in the window / no call yet; nights read (reconstructed
marked); triggers listed by role; triggers live but first recorded before the window (not listed); names unpriced /
flagged (opens to the tickers); the session calendar; the entry rule; the cache state and `compute_ms`. Below it,
fixed: "no aggregate here: medians by tier are on the Track Record page".

## Cache

`dashboards.cache.cached(conn, "trigger-log", (window_days, clock), asof, ...)` holds the window's rows with
their since-returns; grouping, filters and sorts are client side. The cache clears on a new run or a basket edit
(rule 7). The read is the log's jsonb for the window (the log appends only on change, so far fewer rows than nights)
plus bars for the names touched; MEASURE and record it here.

## Backend and frontend

- `backend/dashboards/trigger_log.py`, pure: `trigger_events(nights)` (the identity merge, first night, role, key,
  the state pair, live), `sessions_since`, the since-return, the week grouping key, the counts.
- `backend/app/routers/dashboards/trigger_log.py`: `GET /dashboards/trigger-log?asof=&days=90` (every row + the
  counts). `known_at, clock = serve_view(asof)` threads into the `calls` read, the bars and the benchmark. Wire models
  in the module (rule 8).
- `backend/tests/dashboards/test_trigger_log.py`: one identity across several nights is one row dated the first night;
  a member-level and a card-level copy merge; the fire date note; role and key per kind (the revenue exception); the
  state pair including "new card" and the night-before row outside the window; `live` against the card in effect; a
  trigger first recorded before the window is not a row but is counted; the since-return and the IWM span; a flagged
  name keeps its row; and, DB-backed, **a night recorded after the as-of is not read** and **the window at a past
  as-of ends there**; the router off one cached read.
- `frontend/src/dashboards/trigger-log/`: `entry.ts` (slug `trigger-log`, order 210, `asofAware: true`),
  `TriggerLog.tsx` (the grouped table, the selectors and chips; a pure `logView.ts` for grouping and filtering),
  `useTriggerLog.ts`, `trigger-log.css`, tests.
- **Layout:** the tape look; group headers sticky under the table header; on a phone the source and state columns
  fold under the row.

## Known limits (stated on the page too)

- **The record shows live triggers only:** a trigger that fired and lapsed between two recorded nights never reached
  the log and is not here.
- **The recorded night is when the system saw it**, which can trail the fire date (the note says by how much).
- **A trigger first recorded before the window is not listed**, however live.
- **No aggregate on this page** (rule 6: the Track Record slices by tier).
- **As-traded closes, no dividends.**

## Out of scope

Any statistic across rows; any "works" wording; display members (the Hot Board has the tape); options or intraday
data; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: the 90-day default window; the identity (security, kind, fire
date, label); the first recorded night as the row's date; the week grouping by default; IWM as the comparison and the
session calendar; the one accent on last night's rows.
