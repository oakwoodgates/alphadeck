# Rotation Quadrant

> Slug `rotation-quadrant` · status `experimental` · follows the **as-of dial** (`asofAware: true`). The operator
> approved this dashboard as the next in the queue; the choices the build made that the brief did not spell out
> are listed under "Choices to confirm" for the operator's review. The rules every dashboard follows:
> `docs/DASHBOARDS.md`.

**Label:** Rotation Quadrant. **Description:** "Each group's strength vs SPY and its momentum, with an 8-week
tail: leading, weakening, lagging, improving."

It plots each group as one dot by its strength against SPY (x) and that strength's momentum (y), with a short
tail of where the dot has been over the last 8 weeks, so a group turning up shows before it leads. Display only:
it never feeds a call, never writes to the database and never calls the LLM.

## The groups

- **Groupings:** Link (default) · Business type · Sector · Card, through the shared read in
  `backend/dashboards/baskets.py` (`cards_as_of`, `counted_rows`, `buckets`, `companies_in`): the live cards that
  existed by the as-of, each with its roster then (today's roster stands in for a card with no roster snapshot
  by then, counted on the quiet line).
- **Context and Bench** are left out by default, behind a "Show Context and Bench" toggle (their own groups in the
  Link view).
- In the **Link** view a company in two links of a card counts in both links; in the **Business type**, **Sector**
  and **Card** views each company counts **once** per group.
- A basket row with no resolved security is counted on the quiet line, never grouped (the baskets rule).

## The line (per group)

- The group's **equal-weighted line**, rebalanced daily, through the shared line math in
  `backend/dashboards/series.py` (moved there from the Narrative Index, which re-exports it under its old names):
  one session calendar (SPY's bar dates ending at the as-of, plus any company bar after SPY's last), each
  company's closes aligned on it, and the chain (`level_t = level_{t-1} × (1 + mean daily return of the members
  with a bar on both t-1 and t)`). A member joins once it has two consecutive bars; a gap breaks only its own
  contribution; a session no member can contribute to carries the line flat.
- **SPY's line** runs through the same chain (one member).
- **The read:** a year of sessions (252 daily returns, the Narrative Index's 1Y read) ending at the as-of. The
  measures need 125 of them (below).
- The bars are the point-in-time view's (`PointInTimeData.price_history` / `benchmark_prices`), read with
  `serve_view(asof)`: at a past as-of only bars public by the end of that day count (invariant #1).
- **A flagged price history is left out.** A company whose data flags reach its price readings at the as-of
  (`dashboards.baskets.price_flags`, the Narrative Index's rule) never contributes a daily return and its bars do
  not shape the session calendar. It still counts in the group's names, never in priced, and the group's
  expanded row names it ("1 name left out: RBOT, mixed-scale price history").

## The measures (our own simple measures, stated on the page)

These are our own simple measures, not a published methodology; no proprietary formula is used or named.

- **RS ratio** = the group's line ÷ SPY's line.
- **x = RS level** = 100 × (today's RS ratio ÷ its own trailing 65-session average). 100 = at its 13-week
  average; above 100 = stronger than usual vs SPY.
- **y = RS momentum** = 100 × (today's x ÷ x 20 sessions ago). 100 = flat; above 100 = strength rising.
- **Quadrants:** Leading (x > 100, y > 100) · Weakening (x > 100, y ≤ 100) · Lagging (x ≤ 100, y ≤ 100) ·
  Improving (x ≤ 100, y > 100).
- **Tail:** x / y every 5 sessions back over the last 8 weeks: 9 points, oldest to newest. "4 weeks ago" = 20
  sessions back (the tail's middle point).
- So today's y reads 85 sessions of RS (x 20 sessions back needs its own 65), and the oldest tail point's y reads
  125 (`MEASURE_SPAN`).

## Who's shown

- **Priced** = a name with a daily return into one of the last 65 sessions.
- A group needs **at least 3 priced names** to be measured and plotted. Fewer = "too small": counted, listed in
  the table with "—", never dropped.
- A group without the history for today's x or y = "not enough history": counted, listed with "—" (its x shows
  when x alone is measurable).
- With no SPY tape at the as-of, nothing can be measured; the page says so.

## Layout

1. **The quadrant plot** (inline SVG, no chart library): axes centered on 100, the four quadrants faintly
   labeled, one dot per plotted group with its tail as a thin line ending at the dot. Hover (or keyboard focus)
   shows the group, x, y, n (names priced of names) and the quadrant 4 weeks ago → now, in a readout above the
   plot. Clicking a dot selects its row in the table (highlighted and scrolled into view). Quiet: the dots are
   small and muted, color only marks the quadrant.
2. **The table:** group · quadrant now · quadrant 4 weeks ago · x · y · names priced/total. Sortable; the
   default sort is quadrant (Improving, Leading, Weakening, Lagging), then x highest first. A filter "Changed
   quadrant in the last 4 weeks". Expanding a row shows its names (each card chip opens the Cockpit on that name,
   `onSelectThesis(thesisId, ticker)`) and the flagged-name note. Neutral wording, no verdicts.
3. **The quiet "show the work" lines:** the measures and their windows; the cards counted; the groups plotted /
   too small / not enough history; the companies and how many are priced; the flagged names left out; the
   Context/Bench toggle; the unresolved rows; the sessions read; SPY availability; the cache state; the as-of;
   and, before 2026-09-15, the shared roster note ("Today's roster stands in for N cards: rosters are
   point-in-time only from Sep 15, 2026").
4. A slow-first-load message while the first read computes.

## Cache

`dashboards.cache.cached(conn, "rotation-quadrant", (clock, include_silent), asof, ...)` computes every group's
line and measures for all four groupings once per as-of (with the closes, SPY, the master's business types and
sectors, and the price flags); each request picks one grouping off it. The shared cache clears on a new run or a
basket edit (rule 7).

## Known limits

- **These are our own simple measures, not a published methodology.**
- **Equal weight**, which is not how anyone would size a basket.
- **Business type and sector come from today's master**, not the master as it stood at the as-of. They are read
  inside the cached read, so an operator's business-type re-tag reaches this page at the next run or basket edit.
- **Lagging by construction:** x compares the RS ratio against a 65-session average, so a turn shows in x only
  as it lifts the ratio above where it has been for 13 weeks; y (20 sessions) turns first.
- **A look back on each group's basket as of the as-of**, not the basket as it stood each day (hindsight
  selection, the Narrative Index's limit).
- The price flags are read once per as-of, so a flagged name is left out of the whole year's line, and a split
  review in Admin reaches this page at the next run.

## Out of scope

The point-in-time basket per day, any published rotation methodology, any scoring or ranking of ideas.

## Choices to confirm

These were decided in the build because the brief did not specify them; each is the operator's to confirm or
change.

1. **The 65-session average includes today** (the 65 sessions ending today).
2. **The quadrant is read off x and y as shown**, rounded to 2 decimals, so the label never disagrees with the
   numbers on the page (a raw 100.004 shows as 100.00 and is not above 100). y is computed from the unrounded x.
3. **The RS ratio starts at the later of the two lines' starts** (the session before the first one any member
   contributed a daily return to). Before that, a group's line is only carried flat, which is not history: a
   group whose names joined 80 sessions ago is "not enough history" for y even though SPY has a year.
4. **"Priced" = a daily return into one of the last 65 sessions** (the window x averages over), so a name whose
   tape stopped months ago does not hold a group above the 3-name floor.
5. **A "not enough history" group still shows x** when x alone is measurable (y, the quadrants and the dot do
   not).
6. **No SPY tape = nothing measured;** the session calendar then comes from the companies' bars, and every group
   with 3 priced names counts as "not enough history".
7. **The calendar is SPY's** (not SPY and IWM, as on the Narrative Index), through the same shared rule.
8. **The cache holds all four groupings**, with the master's business types and sectors read inside it (the
   Landing and the Rotation Map read identity per request instead). This follows the brief's cache key and its
   "every group's series once per as-of"; the cost is the re-tag lag under Known limits.
9. **The changed-quadrant filter applies to the plot too** (it declutters a busy Link view), and counts a change
   only when both quadrants are known.
10. **Sorts:** the two quadrant columns start in the default order, the numbers highest first, the group name
    A→Z; "—" sorts last both ways; ties by x highest first, then the label. "Priced" sorts by names priced, then
    by names.
11. **A dot's click selects its row** (highlight and scroll into view) without expanding it; a row's click
    expands it and selects its dot.
12. **The colors:** four hues mixed 55% toward gray (Leading the Cockpit's `--pos`, Improving `--leader`,
    Weakening `--warm`, Lagging `--neg`); every number stays gray. `--warm` is also the Warming state's hue.
13. **The plot's scale** is symmetric around 100 on each axis: the farthest dot or tail point plus 10%, never
    less than ±1. Only the hovered or selected dot carries a label.
14. **A gap in a tail ends its drawn line:** the line runs through the measurable points ending at today's dot.
15. **Flat sessions:** the sessions in the span the measures read (125) that no name could contribute to are
    counted per group and noted in its expanded row only when there are some.
16. **The lookback** is the Narrative Index's (`trailing_returns.LOOKBACK_DAYS`, 420 calendar days).
17. **A group's names** list flagged names last, then names not priced, then by ticker.
