# Narrative Index

> Slug `narrative-index` · status `experimental` · follows the **as-of dial** (`asofAware: true`). The operator
> signed off this plan before the build; the choices the build made that the signed-off brief did not spell
> out are listed under "Choices to confirm" for the operator's review. The rules every dashboard follows:
> `docs/DASHBOARDS.md`.

**Label:** Narrative Index. **Description:** "Each card's basket as one equal-weighted line against SPY and IWM,
with the day the card was created marked."

It answers "is the market agreeing with my idea yet, and since when?": one small chart per card of its
equal-weighted basket against SPY and IWM, with the card's creation date marked. Most cards were created
Oct 26–28, 2026 in a reorganization, and card lineage is not built, so "since creation" is short for most of
them; that is why the default view is a trailing window on the basket, labeled honestly as a look back.
Display only: it never feeds a call, never writes to the database and never calls the LLM.

## The basket

- Each counted card's roster **as of the as-of**, through the shared read in `backend/dashboards/baskets.py`
  (`cards_as_of`: the live cards that existed by the as-of, each with its roster then; today's roster stands in
  for a card with no roster snapshot by then, counted on the quiet line).
- Names only. **Context and Bench** are left out by default, behind a "Show Context and Bench" toggle that
  includes them.
- Each company counts **once per card** (by `security_id`), however many links it sits in.
- The page says plainly, in a quiet line: "The card's basket as of <as-of>, looking back. Not the basket as it
  stood on each past day."

## The index (per card)

- **Equal weight, rebalanced daily**, over the trading sessions in the chosen window ending at the as-of. On
  each session t:
  `index_t = index_{t-1} × (1 + mean of the daily close-to-close returns of the members with a bar on both t-1 and t)`,
  starting at **100** at the window start.
- A member without bars yet **joins** when it has two consecutive bars. A member's **gap** breaks only its own
  contribution (its returns into and out of the missing session), never the whole line.
- A session no member can contribute to **carries the index flat** and is counted.
- The bars are the point-in-time view's (`PointInTimeData.price_history`, on its split scale), read with
  `serve_view(asof)`: at a past as-of only bars public by the end of that day count (invariant #1).
- **A flagged price history is left out.** A company whose data flags reach its price readings at the as-of
  (`dashboards.baskets.price_flags`: the same reader as `GET /theses/{id}/data-flags`, a flag marking one of
  the Cockpit's price cells, the reason the Cockpit grays them) never contributes a daily return, in any
  window, and its bars do not shape the session calendar. It still counts in the card's names, never in
  priced, and the card's quiet notes list it ("1 name left out: RBOT, mixed-scale price history"). Measured
  on dev with a prod data copy (as-of 2026-10-28, 90D): Medical & Surgical Robots read +310.6% because RBOT's
  stored tape steps +8,900% on 2026-10-22, a scale break its `mixed_scale_history` flag already named.
- **Benchmarks:** SPY and IWM over the same sessions, rebased to 100, from `pit.benchmark_prices`. If one is
  missing, its line is absent and the page says why.
- **Windows:** **1Y (default) · 90D · 30D** in trading sessions (252 / 90 / 30 daily returns). The 1Y series
  is computed once (cached), then sliced and rebased for the shorter windows. Each daily step depends only on
  the two sessions it spans, so rebasing the tail IS recomputing from 100 at the window start.

## Per-card numbers

- The index return over the window.
- That return minus SPY's over the same sessions.
- The return since the card was created, only when the creation date falls inside the window; otherwise
  "created before the window", or "—" when the day is unknown.
- The names priced / in the basket.
- The sessions carried flat.
- The names left out for a flagged price history, each with the flag's label.

## Layout

- **A grid of small multiples:** one mini chart per card, with the card name, its current state chip (from its
  latest `calls` row at the as-of, in the Board's state colors), the index line, SPY and IWM as faint lines, a
  vertical marker at the creation date, and the numbers above. The minis are inline SVG, never 62 chart-library
  instances.
- **Clicking a mini enlarges it in place:** a larger chart with the three lines, the creation marker and the
  names contributing each session (the basket size over time), plus each name's sessions in the window.
- **Clicking the card name** opens the card (`onSelectThesis(thesisId)`).
- **Sort options:** Name (default), Window return, Return vs SPY, Since creation. Neutral labels only.
- **The quiet "show the work" line:** the cards counted, the window (its sessions and dates), the toggle, the
  index rule ("equal weight, rebalanced daily, from 100"), "names with a flagged price history are left out"
  (with their count), the basket-as-of note, benchmark availability, the cache state and the as-of.

## Cache

`dashboards.cache.cached(conn, "narrative-index", (clock, include_silent), asof, ...)` computes every card's
1Y series once, with the benchmarks and the price flags; every window and the enlarged view are derived from
it per request. The shared cache clears on a new run or a basket edit (rule 7).

## Known limits

- This is a **look back on the card's basket as of the as-of**, not the basket as it stood each day: hindsight
  selection, labeled on the page.
- Most cards were created Oct 26–28, 2026, and lineage is not built, so "since creation" is short for most.
- Equal weight is not how anyone would size the basket.
- **The price flags are read inside the cached read**, so an operator's split review in Admin (confirming or
  rejecting a split, which clears a flag) reaches this page at the next run: a review moves no part of the
  cache's generation token (a new run, a basket edit, the live cards). A flag that clears on its own (a
  split-night hold after three sessions) clears here at that same next run.
- A flagged name is left out of **every** window: the flags are read once per as-of for the 1Y read, so a
  scale break 200 sessions back also leaves the name out of the 30D line.
- A **stopped price feed** (renamed, delisted, a feed gap) is a flag on every price cell, so such a name is
  left out too, including the sessions it traded before it stopped.

## Out of scope

The point-in-time basket per day (a later version on the roster snapshots, which exist only from 2026-09-15),
card lineage, and any scoring.

## Choices to confirm

These were decided in the build because the brief did not specify them; each is the operator's to confirm or
change.

1. **The session calendar** is one for every card: the dates the SPY / IWM tapes have a bar on, ending at the
   as-of, plus any company bar dated after the benchmarks' last bar (so a late benchmark never cuts the lines
   short). With no benchmark tape at all, the union of the counted companies' bar dates (the quiet line says
   so). A company bar on a date outside that calendar is not read.
2. **"Since creation" runs from the close of the last session on or before the creation day** (the
   `created_at` instant in market time), and the marker sits on that session. A card created after the
   window's last session (no session since) reads "since created —".
3. **"Priced"** = a name that contributed at least one daily return in the window. A card with no priced name
   shows no return ("—"), never a flat 0%.
4. **A non-positive close is not a bar.**
5. **Benchmarks run through the same chain** as a card (one member each), so a benchmark gap carries flat
   exactly as a card's does; a benchmark with no bar in the window is absent, with its reason.
6. **The state chip** is the `calls` row in effect at the as-of (the greatest `(asof, seq)` with `asof` on or
   before it, a reconstructed row included); a card with none reads "no recorded call".
7. **A basket row with no resolved security** counts as a name (deduped by ticker within the card), never
   priced, and is listed in the enlarged view.
8. **Sorting:** Name A→Z by default; the three numbers highest first; a card without the number sorts last
   both ways; picking the active sort again reverses it.
9. **Flat sessions show on a tile only when there are some** (a "0 flat" on every tile is noise, WB#3); the
   enlarged view's bars show the contributors per session.
10. **Each mini has its own y scale**, with a faint line at 100; nothing on a tile is colored except the state
    word.
11. **The enlarged view is SVG, not `lightweight-charts`:** the same points, no canvas, testable in jsdom, with a
    hover readout (date, the three levels, names contributing).
12. **"Basket size over time"** is drawn as the names contributing each session: the basket itself is fixed at
    the as-of.
13. **The enlarged view's names come from a second GET** (`/dashboards/narrative-index/{thesis_id}`), served off
    the same cached read; it 404s a card not counted at the as-of.
14. **The lookback** is the Cockpit's 1Y-return lookback (`trailing_returns.LOOKBACK_DAYS`, 420 calendar days,
    about 290 sessions for the 253 the 1Y window needs).
