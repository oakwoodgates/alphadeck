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
- **A trigger's identity** is (`security_id`, `kind`, `event_date`) within its role (entry or risk), across the
  card's nights; the same trigger on twelve nights is ONE row. **The label is not part of it** (2026-10-29,
  measured on the dev stack: a label can carry that night's numbers, so the same fire date under a new label is
  the same trigger). The row shows the label of its FIRST recorded night and carries `label_changes` = how many
  distinct other labels the record showed for it from that night through the window's end (hover: "label changed N
  times", only when N > 0). It is read from three places on each night's card: `triggers_fired` (an entry
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

**Role loaded (a selector, beside the window):** `entry` (the default) · `risk` · `both`. Risk signals are the
bulk of the record, so each choice is its own read (`GET /dashboards/trigger-log?role=`), never a client-side
filter of one big payload; the work line says which role is loaded and how many rows the other role holds (counted
from the same walk of the nights, never built, priced or sent). With `both` loaded the Role chips below filter
client side as before.

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

`dashboards.cache.cached(conn, "trigger-log", (window_days, role, clock), asof, ...)` holds the window's rows of
the role loaded with their since-returns; grouping, filters and sorts are client side. The cache clears on a new run or a basket edit
(rule 7). The read is the log's jsonb for the window (the log appends only on change, so far fewer rows than nights)
plus bars for the names touched; MEASURE and record it here.

## Backend and frontend

- `backend/dashboards/trigger_log.py`, pure: `trigger_events(nights)` (the identity merge, first night, role, key,
  the state pair, live), `sessions_since`, the since-return, the week grouping key, the counts.
- `backend/app/routers/dashboards/trigger_log.py`: `GET /dashboards/trigger-log?asof=&days=90&role=entry` (every row of the role + the
  counts). `known_at, clock = serve_view(asof)` threads into the `calls` read, the bars and the benchmark. Wire models
  in the module (rule 8).
- `backend/tests/dashboards/test_trigger_log.py`: one identity across several nights (and several labels) is one row
  dated the first night with the first night's label and a `label_changes` count; each role is its own read and the
  other role's rows are counted; a member-level and a card-level copy merge; the fire date note; role and key per kind (the revenue exception); the
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
- **One row per trigger, whatever its label did:** the same security, kind and fire date is one row showing the
  first night's label; the hover on the label says how many times the record changed it.
- **No aggregate on this page** (rule 6: the Track Record slices by tier).
- **As-traded closes, no dividends.**

## Out of scope

Any statistic across rows; any "works" wording; display members (the Hot Board has the tape); options or intraday
data; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: the 90-day default window; the identity (security, kind, fire
date; the label left out on 2026-10-29, see "MEASURED" below); the entry triggers as the default role; the first
recorded night as the row's date; the week grouping by default; IWM as the comparison and the
session calendar; the one accent on last night's rows.

### Built choices to confirm

Every choice the build made that the plan does not spell out, each the operator's to confirm or change. Nothing
below was measured on prod data: the build environment had no prod copy, so the page's own `compute_ms` and the
checks named under "To verify on the dev stack" are the orchestrator's.

**What a row is**

1. **The window is inclusive at both ends:** `[as-of − days, as-of]` in calendar days. The "one row in effect
   before the window" is the newest row dated before the window's first day (`asof < as-of − days`).
2. **The role is part of the identity** (a four-part key since the label left it), so an entry trigger and a risk
   signal that somehow shared the other fields never merge and hide one. Today no kind appears in both roles, so no row changes.
3. **The first copy stands for a merged trigger:** the card-level `triggers_fired` copy, else an armed member's, else
   a watch member's, else the risk signal. Its grade, label and sources are the row's. A later copy's label counts
   into `label_changes` when it differs from the standing one, the same night included.
4. **"First recorded before the window" means "the one row before the window shows it."** That row is all the log
   the page reads of the past, so an identity it shows is never a row, even if it lapsed inside the window and came
   back; a trigger that lapsed before that row and returned inside the window is listed (new to the record we read).
   A trigger that lapses and returns inside the window is one row dated its first night.
5. **The count of live triggers first recorded before the window** (`carried_live`) counts both roles: every
   identity the row before the window shows that is still on the card in effect at the as-of.
6. **No Context / Bench filtering:** a recorded call never carries a silent name's trigger (`voting_thesis` removes
   it at the assembly seam), so there is nothing to filter; a name the roster has since moved to Context keeps its
   past rows.
7. **A night whose stored card no longer validates** is counted on the work line ("N unreadable, not listed"), never
   a 500. If the row before the window is the unreadable one, the first night's state cell reads `? → armed`, not
   "new card"; if the card in effect at the as-of is the unreadable one, `live` is unknown ("—") for every row.

**Role, key, state, live**

8. **The key is read with today's `DEFAULT_CONFIG`,** not the config in force the night the call was recorded.
9. **An entry trigger whose kind is in neither key set** (a squeeze, say) turns no key and reads "no key".
10. **The revenue-acceleration exception is recognized by its provenance,** because the assembler keys it on the
    detector name, which never reaches the wire: a catalyst whose source is `xbrl` with `metric: revenue`. A test pins
    the metric against the detector's own constant and the source against `COMPUTED_CONVICTION_SOURCES`.
11. **"State changed only" means the previous recorded night's state is known and differs.** A new card began; it did
    not change.
12. **`live` is "still on the card in effect at the as-of"** (the newest night in the window, else the row before it).
    Off the card reads "off the card", not "lapsed" (a re-dated trigger also leaves the card).

**The tape**

13. **Sessions since** = the sessions between the entry bar's session and the last session on or before the as-of, on
    IWM's calendar: the number of daily steps the return spans (0 = the same session; none = "—").
14. **IWM is read on the name's own two dates** (its entry bar's date and its end bar's date), by exact date. If IWM
    has no bar on either, "IWM" shows nothing: never a shifted span.
15. **An entry bar that is also the end bar** (recorded on the as-of's own session) is a priced 0.0%, not unpriced.
16. **A flagged name's bars are never read** (it keeps its row; the cell reads "flagged: <reason>").
17. **The sign tone sits on the name's own return only;** IWM beside it stays gray.
18. **Bars are read over the window** (`lookback_days = days`), which holds every entry bar (the recorded night is
    never before the window's first day).

**The page**

19. **The one accent is exact equality:** `recorded night == the as-of date`. On the live view, before tonight's pass,
    last night's rows are dated yesterday, so nothing is accented until the pass runs. The alternative is "the newest
    recorded night on or before the as-of" (accents last night all day): say so and it is one comparison.
20. **Filters:** role, kind and grade are chip sets (any of a dimension's chips, all across dimensions); **card is a
    picker, not chips** (a card list can run to dozens); "live only" and "state changed only" are chips. Each chip
    restates how many rows it shows with every OTHER filter applied; the page restates "N of M" when any filter is on.
    A control with fewer than two values in the payload does not render (WB#3).
21. **Group order:** weeks newest first, kinds and cards A→Z; inside a group the server's order (recorded night
    newest first, then ticker, no-ticker last, then card, role, kind, label, fire date). A group header counts its
    rows by role and leaves a zero out.
22. **A reconstructed night is a dagger (†)** on the recorded night, with the explanation on hover and a line on the
    work line; the page footnote counts them.
23. **The fire-date note is its own small line under the date** ("recorded 5 days after"); no note when the fire date
    is the recorded night, later than it, or absent ("undated").
24. **Tickers:** the roster at the as-of (first basket row of the company wins), else the master's; a name with
    neither shows "—", opens the Cockpit by its security id and is counted on the work line.
25. **Phone:** below 760 px the column header goes and each row folds into five lines (night · ticker · kind · since;
    fire date · card; label; source · state; live · sessions · grade · turns), with a quiet label in front of a cell
    whose words do not carry its meaning. The table sits in its own scroll box (at most 72% of the viewport tall) so
    its sticky header and group headers hold; a desktop narrower than about 1,330 px scrolls it sideways.
26. **API:** `asof` is required (the shell always sends it); `days` is the enum 30 | 90 | 180 (422 otherwise); returns
    are fractions; each row has a short stable `key` (a hash of its identity) and its `week` (the Monday).
27. **The description is the plan's, word for word ("the last 90 days")** though the window picker offers 30 and 180
    too; 90 is the default.
28. **`registry.test.ts` pins the slug list and the order list;** this build appends `trigger-log` / 210 after
    `evidence-ledger` / 180. The other trading pages add their own lines to the same two arrays, so the integration
    merge will conflict there: resolve by keeping every slug in `order` order.

**The label left the identity, and the role loads on demand (2026-10-29, after the dev walk below)**

29. **The role is a query parameter, not a client filter.** `role` = `entry` (the default) | `risk` | `both`; each is
    its own read and its own cache entry `(window_days, role, clock)`; any other value is a 422. The role not loaded
    is walked once more over the nights already read only to COUNT its rows (`counts.other_role_rows`), so it adds no
    query; its rows are never built, priced or sent, and only the loaded role's names are priced.
30. **`label_changes` counts the distinct OTHER labels** the record showed for the identity from its first night
    through the window's end: a label that goes A, B, A counts 1 (not 2), and another copy's label on the first
    night counts. The hover reads "label changed N times" (only when N > 0). A label the record shows before the
    window, on an identity first recorded before it, is not a row and counts nowhere.
31. **`carried_live` counts the loaded role only** (the live triggers first recorded before the window that the
    rows of the role loaded would otherwise have shown).
32. **The controls:** a "Load" group (Entry triggers · Risk signals · Both) beside the Window group, each choice its
    own fetch while the previous view stays on screen. The Role filter chips stay (they render only when both roles
    are in the payload, WB#3).
33. **The work line** names the role loaded and the other role's row count ("entry triggers loaded · N risk signals
    not loaded"; "no risk signals in the window" when it holds none), and the empty state names the role.
34. **The row key no longer includes the label** (a short hash of thesis, security, role, kind and fire date), so a
    row keeps its key across a label change.

**To verify on the dev stack (prod data copy)**

- **Label churn: DONE (2026-10-29), see "MEASURED" below.** The first identity included the label; the dev copy
  showed it multiplied rows (a breakdown's label embeds the night's close), so the label left the identity.
- **Cold cost and the cache:** the plan asked to MEASURE the read (the window's `calls` jsonb plus bars for the names
  touched) and record it here. **Not measured:** the page prints the cache state and the cold `compute_ms` on its
  last quiet line; record both numbers for the 30, 90 and 180 windows from the dev stack.
- **Flags on the live view:** the build's DB tests ran at past as-ofs only, and I did not run a live as-of on real
  data. The Chain Wave test notes that a synthetic tape ending at today's as-of reads as a stopped feed, so check on
  the dev copy that a live as-of shows no unexpected "flagged" rows.

### MEASURED (dev, prod copy, 2026-10-29)

The orchestrator's walk on the dev stack (a prod data copy), as-of 2026-10-29, window 90 days, BEFORE this change
(identity = security, kind, fire date, label; both roles in one read). These numbers are the orchestrator's,
recorded here as the brief gave them; the build re-measured nothing.

- **The read:** 23,526 rows, 18,588 of them risk signals; payload 25.3 MB; cold compute 37,848 ms; a cache HIT still
  3.3 s (serializing that payload); in the browser the page never finished rendering.
- **Label churn:** 1,675 of 11,536 (card, security, kind, fire date) groups carried more than one label. A
  `breakdown` risk signal's label embeds the night's close ("Structural break: close 127.19 closed below the
  200-day base" the first night, "close 127.30 ..." the next), up to 56 labels for one signal; `technical_breakout`
  groups up to 4; `corporate_risk` up to 9.
- **Causes:** (1) the label in the identity multiplied rows, since a label can carry the night's numbers; (2) risk
  signals swamp the entry triggers (18,588 of 23,526 rows).
- **The change:** the label leaves the identity (the row shows the first night's label and a `label_changes`
  count) and the role loads on demand (`role=entry` by default).

**AFTER (the orchestrator fills these in from the dev stack, same as-of 2026-10-29 and window 90 days):**

- `role=entry` (the default): rows 4,515 · payload 4.9 MB · cold compute 33,296 ms · cache hit 680 ms (the serialization of 4.9 MB)
- `role=risk`: rows 6,941 · payload 8.7 MB · cold compute 32,449 ms
- `role=both`: rows 11,456 (was 23,526 with the label in the identity) · payload 13.6 MB · cold compute 33,811 ms
- The cold compute is the same whatever the role: it is the read of 785 recorded nights' card blobs and the names' bars, not the rows. A narrower read (the three trigger arrays by jsonb path instead of validating every CallCard) is the next cut if the first visit after each nightly matters.
- rows with `label_changes` > 0: ____ of ____ (largest `label_changes` ____)
- the page renders in the browser: ____

