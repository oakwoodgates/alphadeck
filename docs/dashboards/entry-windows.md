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
  at or past the end the cell reads "entry window closed N sessions ago". The card's `entry_window_closing` flag is
  the one accent on the page (below).
- **Days to `exit_by`:** calendar days from the as-of; past it, "hold horizon passed"; the card's `lapsing` flag as a
  quiet tag.
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
(the card's own flag: at most two sessions left) carries the page's ONE accent on its "sessions left" cell (rule 3:
loudness marks the exception). The sign tone (the Rotation Quadrant's muted pair) only on the signed "since the arm
night" values. Neutral wording throughout.

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
  Record page has the next-session toggle.

## Out of scope

Any "early / late / chase" wording or score; sizing or instrument choice (#4, #5); display members on this page (the
Hot Board has the tape columns); risk signals; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: Armed rows by default with Managing behind a toggle; the
recorded run's first night as the arm night; IWM's bars as the session calendar; the Scoreboard's entry rule; IWM
and SPY both shown; the one accent on `entry_window_closing`.
