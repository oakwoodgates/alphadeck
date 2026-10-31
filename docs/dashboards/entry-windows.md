# Entry Windows

> Slug `entry-windows` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 200`.
> The operator approved the TRADING set (2026-10-29: "write the plans"); this page replaces the "Catalyst
> Countdown" of the first sketch, whose job the Catalyst Calendar (`docs/dashboards/catalyst-calendar.md`) already
> does. The choices the build makes that this plan does not spell out go under "Choices to confirm" for the
> operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`. The second page of the TRADING set.

**Label:** Entry Windows. **Description:** "Each Armed card's entry window and hold horizon on one date axis, where
the as-of sits in them, and what the armed names have done since the arm night."

It answers "where in the window am I": the platform's two clocks per card are `arm_until` (the entry window ends;
the confirmation clock) and `exit_by` (the hold horizon; the conviction clock). The Catalyst Calendar lists those
dates in buckets, What Changed flags an `arm_until` within three days, the Idea Timeline draws state history. None
of them draws the window as a span with the as-of inside it and the tape since the arm night beside it. This page
does, one row per Armed card. Display only: it never feeds a call, never writes, never calls the LLM; it never says
"early", "late" or "chase": it shows where the as-of sits and what the closes did.

## Who is a row

- **The cards counted at the as-of** (`baskets.cards_as_of`), each read as its **recorded call in effect at the
  as-of**: the What Changed read (`DISTINCT ON (thesis_id) … WHERE asof <= D ORDER BY thesis_id, asof DESC, seq DESC`
  over the `calls` log). A card whose card in effect is **Armed** is a row. A **Managing** card (the hold horizon
  still runs) joins behind a toggle "Show Managing" (off by default); Warming and Incubating cards are counted on
  the work line, never rows. A card with no recorded call is counted ("no call yet").
- **The arm night** of a card is the first night of its CURRENT recorded Armed run: walk the card's log rows
  (`asof, seq, state, reconstructed`) back from the night in effect while the night's winning row (the highest
  `seq`, the Idea Timeline's rule) reads `armed`; the run's first night is the arm night. A reconstructed night
  counts as a night and is marked. The arm night is the **recorded** night (the public clock: when the operator could
  first have seen the name Armed), never a trigger's fire date; the fire dates show in the hover.
- **Each armed member's own arm night** is the first night of that run on which it appears in `card->'armed_members'`
  (a member can join an already-Armed card later). Its triggers (`MemberCall.triggers`: `label`, `kind`, `grade`,
  `event_date`, `sources`) are the hover's content, each source linked by the Evidence Ledger's rule
  (`dashboards/filings.py::source_url` with the issuer CIK; a `price:` ref stays text).

## The row

- **Card** (opens the Cockpit, `onSelectThesis(thesisId)`), the state chip, the armed names as ticker chips (each
  opens the Cockpit on the name), the card's `confidence` as "setup strength 0.62" in text.
- **The window picture**, on ONE shared date axis for the whole page (from the earliest arm night on the page, less
  five sessions, to the latest `exit_by`; the as-of is a vertical line through every row): two thin stacked bars from
  the arm night, the **entry window** to `arm_until` and the **hold horizon** to `exit_by` (either may be the longer;
  both are drawn from the arm night). A missing clock (`None`) draws no bar and says "no entry clock" / "no hold
  clock". The card's dated catalysts inside [arm night, `exit_by`] from the recorded card's `catalyst_surface` are
  ticks on the row (hollow when the date is before the as-of), labeled on hover with `label`, `kind` and the date.
- **Sessions into the window:** the sessions from the arm night to the as-of, counted on the **benchmark's bars**
  (IWM's session calendar from `dashboards/series.py`), the as-of's own session included when it has printed.
- **Sessions left to `arm_until`:** the sessions strictly after the as-of through `arm_until`, on the same calendar;
  at or past the end the cell reads "entry window closed N sessions ago". The row's `entry_window_closing` flag (any
  armed member's) is the one accent on the page (below); a name whose own window is the one closing, and differs from
  the card's, is named under the figure ("BBB · 1 session left · to Oct 28").
- **Days to `exit_by`:** a countdown, so calendar days from the **countdown origin** (F2, `DASHBOARDS.md` rule 2: the
  action day, the session after the night, on a live view; the as-of on a past one); on the origin day it reads
  "today", before it "hold horizon passed". A catalyst dot is hollow when dated before that same origin. The session
  counts above stay on the as-of. The card's own `lapsing` flag
  (the headline name's) as a quiet tag, and under it a line for each other name whose hold clock is closing.
- **Since the arm night:** per armed name, the as-traded return from the **first close on or after the arm night**
  (the Scoreboard's entry rule, `scoreboard/`) to the last close on or before the as-of (`pit.price_history`); the
  row shows the **median over the priced armed names** ("median of 3"), **IWM and SPY over the same span**
  (`securities/benchmarks.py`), and **"x of n above the arm-night close"**. A name with no bar on or after the arm
  night, or on the flagged-price list (`baskets.price_flags`, rule 5), is unpriced: counted in n's denominator note,
  never silently dropped, and left out of the median.
- **A sparkline** per row under the window bars, on the same axis: the armed names' equal-weighted line since the arm
  night against IWM (the Narrative Index's shared series math, `dashboards/series.py`: the session calendar, the
  alignment, the chain), ending at the as-of. Flagged histories are left out of the line and named (`FlaggedNote`).
- **The fold** (click the row): one line per armed member: ticker, its own arm night, its triggers (kind, grade,
  fire date, source link), its return since its own arm night, and whether it is above its arm-night close.

**Default sort:** sessions left to `arm_until`, ascending (closing soonest first; closed windows after the open ones,
most recently closed first). Alternatives: arm night (newest first), days to `exit_by`, median since the arm night.

## Loudness

The trading set's **tape look**: dense rows, tabular numerals in the mono font, a sticky header, no cards or chrome.
The window bars are two grays; the as-of line is the only line color; a row whose `entry_window_closing` is true
(true when any armed member's own flag is: at most two sessions left in its window) carries the page's ONE accent on
its "sessions left" cell (rule 3: loudness marks the exception); the name lines under the figures are quiet. The sign tone (the Rotation Quadrant's muted pair)
only on the signed "since the arm night" values. Neutral wording throughout.

## Past as-of

At a past as-of the card in effect is the row recorded on or before that date, the run is walked on rows at or
before it, the bars end at the as-of, and a catalyst whose date is after the as-of is a solid tick (still ahead
then). A card armed after the as-of is not a row. Nothing recorded after the as-of is read.

## The quiet "show the work" line

The as-of; the clock; cards counted / Armed rows / Managing (shown or hidden) / Warming / Incubating / no call yet;
armed names priced / unpriced / flagged (opens to the tickers); reconstructed arm nights; the session calendar
(IWM's bars); the entry rule (first close on or after the arm night); the cache state and `compute_ms`.

## Cache

`dashboards.cache.cached(conn, "entry-windows", (clock,), asof, ...)` holds every Armed and Managing row with its
members, triggers, returns and sparkline series; the Managing toggle and the sorts are client side. The cache clears
on a new run or a basket edit (rule 7).

## Backend and frontend

- `backend/dashboards/entry_windows.py`, pure: `armed_run(rows)` (the run's first night, reconstructed nights
  marked), `member_arm_nights(run_cards)`, `sessions_between(calendar, a, b)`, the since-arm returns and the median
  with its n, the catalysts in the window, the axis bounds, the counts.
- `backend/app/routers/dashboards/entry_windows.py`: `GET /dashboards/entry-windows?asof=` (every row + the counts).
  `known_at, clock = serve_view(asof)` threads into the `calls` read, every `PointInTimeData` and the benchmark read.
  Wire models in the module (rule 8).
- `backend/tests/dashboards/test_entry_windows.py`: the run's first night across several nights (a same-night re-run
  with a higher `seq` wins; a reconstructed night marked; a run broken by a Warming night starts over); a member that
  joins an Armed card later gets its own arm night; sessions counted on the benchmark calendar (a weekend and a
  holiday do not count); the since-arm return per name and the median with its n (one unpriced name counted, not
  averaged); catalysts inside and outside the window, hollow when passed; a card with no `arm_until` draws no entry
  bar; and, DB-backed, **a card armed after the as-of is not a row** and **a night recorded after the as-of is not
  read**; the router off one cached read.
- `frontend/src/dashboards/entry-windows/`: `entry.ts` (slug `entry-windows`, order 200, `asofAware: true`),
  `EntryWindows.tsx` (inline SVG for the bars and sparklines laid out by a pure `windowsLayout.ts`; no chart library),
  `useEntryWindows.ts`, `entry-windows.css`, tests. The Chain Map's SVG pattern (pure layout, keyboard focus, one
  readout line) is the one to follow.
- **Layout:** the tape look; the axis header sticky above the rows; on a phone the picture scrolls sideways and the
  figures stack under the card name.

## Known limits (stated on the page too)

- **The arm night is the recorded night**, not the trigger's fire date (the hover shows both).
- **Sessions are the benchmark's bars;** a name halted on a session still counts the session.
- **Equal-weight medians, as-traded closes, no dividends.**
- **The Scoreboard's entry rule** (the arm night's own close) is one the operator could not have taken; the Track
  Record page opens on the next session's close.

## Out of scope

Any "early / late / chase" wording or score; sizing or instrument choice (#4, #5); display members on this page (the
Hot Board has the tape columns); risk signals; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: Armed rows by default with Managing behind a toggle; the
recorded run's first night as the arm night; IWM's bars as the session calendar; the Scoreboard's entry rule; IWM
and SPY both shown; the one accent on `entry_window_closing`.

### Built choices to confirm

Choices the build made that the plan above does not spell out, each the operator's to confirm or change. The code
lives in `backend/dashboards/entry_windows.py` (the rules are written at the top of that module), its router and
`frontend/src/dashboards/entry-windows/`.

**The run and the arm night**

1. **A Managing card's window.** The plan walks back "while the winning row reads `armed`", which stops at once on a
   Managing card. Built: a Managing card walks back through `armed` and `managing` nights and takes the first `armed`
   night of that span as its arm night (the Armed run the position came out of); the run runs from there to the night
   in effect. A Managing card whose span has no `armed` night has no window to draw: it is counted on the work line
   ("Managing with no Armed night recorded"), never a row.
2. **A card Armed with no armed name recorded** is still a row, with no chips ("no armed name recorded"). There is no
   fallback to `armed_security_id`.
3. **The two flags** (`entry_window_closing`, `lapsing`) exist on `MemberCall` only; `CallCard` has no card-level one.
   Built: the row's flag is true when ANY armed member's own flag is true, read from the recorded card in effect
   (operator decision 2026-10-30; the first build carried the headline member's alone, `armed_members[0]`). The
   accent therefore marks a card whose second name's window is closing too. The "sessions left" cell still counts to
   the card's own `arm_until` (the headline member's clock), so a marked card can read more than two sessions there.
   Built to close that gap (2026-10-30): the row carries `member_clocks`, one entry per armed name (each once, in the
   recorded order) whose own flag is true: `headline` (it is `armed_members[0]`), both flags, and, when its entry
   window is closing, its own `arm_until` with `sessions_left` / `closed_ago` counted to it by the same rule as the
   card's figure (`entry_clock`: the Mon-Fri days after the as-of while open, the IWM sessions since it closed).
   The page keeps the card's figure and adds one quiet line per such name under it, "BBB · 1 session left · to Oct
   28", **unless the name's `arm_until` is the card's own** (then the figure above already is its clock). Likewise
   "BBB · hold clock closing" under the days to `exit_by` for each name whose own `lapsing` is true, **unless the
   card's own hold clock (the headline name's) is closing**: the card's quiet tag, now the headline name's flag
   rather than any name's, already says so. The tooltip and the footnote say the accent marks any armed name's
   window at two sessions or fewer.

**Sessions**

4. **"Sessions into the window"** = the IWM sessions strictly after the arm night through the as-of (`a < d <= b`);
   the arm night's own session is not counted, so the arm night itself reads 0. The same rule makes "into" and "left"
   add up to the window.
5. **"Sessions left" after the as-of** cannot come from IWM's bars (none exist after the as-of). Built: the Mon-Fri
   days after the as-of through `arm_until`, the call path's own `sessions_left` count, so a market holiday ahead
   counts as a session (stated on the page). Everything up to the as-of is the IWM calendar.
6. **The window's last day.** When `arm_until` equals the as-of the cell reads "last day of the entry window", not
   "entry window closed 0 sessions ago". A later date reads "entry window closed N sessions ago", N on IWM's bars.
7. **IWM's calendar** is extended by any priced armed name's bar dated after IWM's last bar (the shared
   `session_calendar` rule, so a late benchmark never cuts a line short). With no IWM bars at all the calendar is the
   armed names' bars, and the quiet line says so.

**The picture**

8. **The axis** ends at the latest of the as-of, every `arm_until` and every `exit_by` (the plan says the latest
   `exit_by`; either clock may be the longer, and the as-of line must stay on the axis). It starts five IWM sessions
   before the earliest arm night, or a week before when the calendar does not reach back that far. It is linear in
   calendar days, one fixed 440 px picture per row.
9. **A clock dated before the arm night** draws no bar (the figures still say what they say).
10. **Catalyst ticks** are the card's dated catalysts from the arm night to `exit_by`; with no `exit_by` the window
    ends at `arm_until`; with neither, no ticks. The fold lists the same catalysts as text.
11. **Row height** is about 52 px (two bars, the dots and the sparkline stack), not the Hot Board's 26 px.
12. **The sparkline** starts at the session the armed names' line first steps from (the shared `line_start`; an
    earlier session would only carry it flat) and IWM is rebased to 100 at that same session. It ends at the as-of's
    session; with fewer than two sessions, or no name that ever steps, the row has none.
13. **The state chip** is gray text on an Armed row (every row of the default page, so a color would mark nothing)
    and takes the Board's Managing color only on a Managing row. The accent is `--warm` (amber) and the as-of line
    `--leader` (blue).
14. **Controls render only when they discriminate:** "Show Managing" only when a Managing card exists, the order
    control only for two or more shown rows.
15. **Phone:** the header is hidden, each row stacks (card, picture in its own sideways-scrolling box, labeled
    figures), and the faint date gridlines stay in every row's picture. The app's own top nav is wider than 390 px
    (measured: the page scrolls sideways on the dashboards index and the Narrative Index too); nothing on this page
    is.

**The tape**

16. **Row median.** Every armed name is measured from the CARD's arm night (the plan's rule); the fold's per-name
    figure is from its OWN arm night. The page-level "armed names priced / unpriced" counts each name once, by its own
    arm night.
17. **Bars are read back to the earliest arm night** (plus 14 days, plus the horizon registry's 30-day margin), not
    the Cockpit's display window, so an arm night older than that window is never entered at a later bar.
18. **Returns are percents, as traded** (the view's split scale, no dividends), IWM and SPY from the first close on or
    after the same arm night. A flagged name's bars are never read (not in the median, the line or the calendar).
19. **Orders** (client side; the server sends rows by card name). "Sessions left": open windows soonest first, then
    closed ones most recently closed first, then no clock. "Days to `exit_by`": the same shape. "Median since": largest
    first, none last. "Arm night": newest first. Every order ends in the card's name.

**Wire**

20. The page carries `clock` (`record` | `public`) so the quiet line can say how the as-of was read. One GET holds
    every Armed and Managing row; the Managing toggle and the sorts do not call the server.
21. The counted cards come from `baskets.cards_as_of` as the plan says, which also reads each card's roster
    snapshot at a past as-of; this page never uses a roster (a Context or Bench name cannot be armed), so that read
    is cost without use. Not measured on production-sized data.

### MEASURED (dev with a prod data copy, 2026-10-29, after the nightly pass)

- Live as-of: 43 Armed rows (18 Warming and 1 Incubating card counted, 0 Managing), 78 armed names all priced, 152 KB,
  cold compute 6,705 ms, cache hit 30 to 70 ms; the session calendar from IWM's bars; axis 2026-07-14 to 2027-04-27.
- Past as-of 2026-09-15 on the public clock: 10 cards counted (52 not yet created), 6 Armed rows, 19 names, 28 KB,
  cold 2,802 ms, one reconstructed arm night marked.
- Renders at desktop width with the window bars, the as-of line, the catalyst ticks and the since-arm figures per
  row. The cold read validates one full card blob per night of every Armed run (the arm-night walk); 6.7 s is
  acceptable for a cached page, so the narrower read (armed ids per night, the full card only for the night in
  effect) stays a noted option, not a change.
