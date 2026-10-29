# Chain Wave

> Slug `chain-wave` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 110`. The
> operator approved this dashboard; the choices the build made that the brief did not spell out are listed under
> "Choices to confirm" for the operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Chain Wave. **Description:** "One card's links in chain order, week by week against SPY, so a move
traveling along the chain shows as a diagonal; its payers beside its counting names."

It shows ONE card at a time: its links in chain order down the side, calendar weeks across, each cell that link's
weekly return minus SPY's. A move that starts in the first links and reaches the later ones week by week shows as a
diagonal. Below the chain: the card's counting names as one row, and one row per context role it has set (payers
first). Display only: it never feeds a call, never writes to the database and never calls the LLM.

## The rows (one card), top to bottom

1. **SPY:** SPY's own weekly return (absolute, a small gray bar up or down), so the market's week is in view.
2. **The chain:** the card's real links in its chain order (`thesis.segments` as loaded: today's chain, Discovered
   wherever it sits). Each link = its companies on the roster at the as-of, each once (by `security_id`).
   - A roster row (at a past as-of) whose link is not on the card today: its own row under the old label, after the
     real links, marked "not a link today". Never dropped.
   - Rows with no link: one "No link" row after those.
   - **Context and Bench** are out of the chain by default. The "Show Context and Bench" toggle (`include_silent`)
     adds them as the last two chain rows.
3. **Counting names:** every company that counts toward the call (every row not in Context or Bench), each once.
4. **Role rows:** one row per context role the card has set, each company once, in this order: payer, counter_case,
   direct_read, participant, financier, benchmark. They include Context and Bench names whatever the toggle says
   (that is where the roles live). A card with no roles set shows none, with a quiet note.

Each row's label shows "names priced / names". A row whose names are all unpriced shows "—" in every cell. A row with
fewer than 3 priced names is `thin`, drawn at half strength. There is no name floor: every row is shown.

The roster comes through the shared basket read (`backend/dashboards/baskets.py`): the cards that existed by the as-of
(`cards_that_existed`), the card's roster then (`thesis_repo.get_asof`; today's roster stands in before its first
snapshot, said in a quiet note), `memberships_of` (which now carries each row's `context_role`) and `is_silent` for
Context and Bench.

## The columns (weeks)

- Calendar weeks, Monday to Sunday, on the shared session calendar (`backend/dashboards/series.py`
  `session_calendar`: SPY's bar dates, plus any company bar after SPY's last; with no SPY tape, the companies').
  A week's close = its last session in the calendar.
- The last column is the week holding the as-of, labeled "wk to date" when the as-of is not that week's last session.
- **Window: 13 · 26 (default) · 52 weeks**, all sliced from one cached read of 53 calendar weeks of sessions (the
  52 columns and the week before the first, whose close is its base): about 265 sessions with a full tape, inside the
  420-calendar-day lookback the other price dashboards use (`trailing_returns.LOOKBACK_DAYS`).

## The cell

**The cell = the row's weekly return minus SPY's weekly return, in percentage points.**

- The row's line is its equal-weighted line, rebalanced daily, through the shared chain (`series.chain`); SPY runs
  through the same chain (one member). A member joins once it has two consecutive bars; a gap breaks only its own
  contribution.
- Weekly return = the line's level at this week's close ÷ its level at last week's close − 1.
- "—" when (checked in this order, the reason rides along as `gap` and shows on hover):
  - the week has no session in the calendar (`no_session`);
  - the row's line had not started by last week's close (`not_started`, `series.line_start`);
  - no member contributed a daily return in that week (`no_return`: a fully flat week is not a 0% week);
  - SPY has no return that week (`no_spy`).
- **Color:** a muted diverging scale (the Rotation Quadrant's approach: the Cockpit's `--pos` / `--neg` mixed 60%
  toward gray), symmetric, saturating at ±10 pp. No badges, nothing loud. Every number lives in the readout.
- **Hover / keyboard focus readout** (one line above the grid): the row, the week's dates, the row's return, SPY's,
  the difference, and the names contributing that week. The grid is one tab stop; the arrow keys move the focus.

## Readout

One quiet line under the card's name, only when the card has payers: "Last 4 weeks vs SPY: payers +X.X pp · counting
names +Y.Y pp". Each value = the line's return from the close before the last 4 week columns to the last session,
minus SPY's over the same span (the cell's "—" rules over that span). Numbers only: no "lead" label, no ranking,
neutral wording.

## Expand a row

Clicking a chain row, the counting names row or a role row shows its companies as sub-rows, each with its own weekly
cells vs SPY (a one-name line). Each ticker chip calls `onSelectThesis(thesisId, ticker)`. A flagged company is listed
as "left out: <reason>"; a basket row with no resolved security as "no resolved security: never priced".

## Picking the card

A searchable picker (a search box over a select): the cards counted at the as-of, A→Z, each with its link count today.
The default is the first A→Z. The card's name calls `onSelectThesis(thesisId)`. The selection is component state (no
browser storage).

## A flagged price history

Following the Rotation Quadrant / Narrative Index pattern exactly (`baskets.price_flags` + `flagged_in_view`): a
company whose data flags reach its price readings at the as-of never contributes a return, and its bars never shape the
calendar. It still counts in its rows' names, never in priced, and is named with its reason (in its rows' sub-rows and
the shared quiet note, `FlaggedNote`).

## The quiet "show the work" line

The measure (weekly return minus SPY's, equal weight, rebalanced daily, full color at ±10 pp); the window and its
dates; the counting names priced / total; the flagged names left out; the toggle; the unresolved rows; SPY's
availability; the cache state and the as-of. Below it: the roster note when today's roster stands in ("rosters are
point-in-time only from Sep 15, 2026"), the flagged-names note and the known limits.

## Cache

`dashboards.cache.cached(conn, "chain-wave", (str(thesis_id), clock), asof, ...)` holds every row's weekly cells for
all 52 weeks, Context and Bench included, with each company's own cells; the window and the toggle are sliced per
request. The picker list is cached as `("cards", clock)`. The shared cache clears on a new run or a basket edit
(rule 7).

## Backend and frontend

- `backend/dashboards/chain_wave.py`: pure. Week bucketing, per-row weekly cells, the role rows, the chain order, the
  readout, the window slice.
- `backend/app/routers/dashboards/chain_wave.py`: `GET /dashboards/chain-wave/cards?asof=` (the picker) and
  `GET /dashboards/chain-wave/{thesis_id}?asof=&weeks=13|26|52&include_silent=` (the grid, each row with its member
  sub-rows; 404 for a card not counted at the as-of). The request's `known_at, clock = serve_view(asof)` is threaded
  into every read (invariant #1).
- `frontend/src/dashboards/chain-wave/`: `entry.ts`, `ChainWave.tsx` (a CSS grid, no chart library), its hooks
  (`useChainWave.ts`), its view helpers (`waveView.ts`), its CSS and its tests.
- Shared: `line_start` moved from `rotation_quadrant.py` into `series.py` (re-exported from `rotation_quadrant` under
  the same name), and `baskets.Membership` gained an optional `context_role`.

## Known limits (stated on the page too)

- **A look back on the card's roster as of the as-of**, not the roster each past week (hindsight selection).
- **Chain order is today's:** links load live; only the roster is snapshotted.
- **Equal weight.**
- **Small links are one or two stocks;** the label's n says so.
- **Our own simple measure,** not a published method.
- **Price flags are read once per as-of,** so a flagged name is left out of every week.

## Out of scope

Any computed "wave detected" label or score; the roster per past week; card lineage; any Cockpit change; cross-card
views.

## Choices to confirm

These were decided in the build because the brief did not specify them; each is the operator's to confirm or change.

1. **"wk to date" = an as-of before that week's Friday.** There is no trading calendar in the app
   (`domain/market_time.py` has none), so a Thursday as-of before a Good Friday reads "wk to date" although Thursday is
   that week's last session.
2. **A week with no session** (a data gap: SPY's tape missing a whole week) shows "—" in every row, and the next week
   measures from the last close before its Monday, so it spans the gap.
3. **The read is 53 calendar weeks** from the Monday 52 weeks before the as-of's week, not a session count.
4. **The calendar includes Context and Bench companies' bars** (they are in the cached read; the toggle is sliced per
   request), so a silent name's bar after SPY's last can add a session to every row.
5. **A real link with no names on the roster at the as-of is still shown** (0/0, every cell "—") in its chain place.
   At a past as-of this includes a link added since (links load live).
6. **Context and Bench both show with the toggle**, even when one is empty. The "No link" row shows only when some row
   has no link.
7. **Old links** ("not a link today") come in roster order, after the real links and before "No link".
8. **A basket row with no resolved security counts as a name** (each once by its ticker, any case), never priced, and
   is listed in its rows' sub-rows. It is counted on the work line.
9. **Priced and thin follow the window:** a name is priced when it contributed a daily return in one of the window's
   weeks, so a name can be priced over 52 weeks and not over 13.
10. **The readout spans the last 4 week columns whatever the window**, ignores the toggle, and its counting-names line
    excludes Context and Bench (they never count) while the payers line includes them.
11. **Gap order:** no session, then not started, then no return, then no SPY return. The hover still shows a row's own
    return when only SPY's is missing.
12. **A company that starts trading mid-week** reads "—" (not started) in its own sub-row that week, while its row's
    line takes its daily returns from its second bar on.
13. **Values on the wire** are percent (pp for the difference) rounded to 4 decimals; the page shows 1 decimal.
14. **Colors:** the hue is mixed 60% toward gray and drawn at up to 80% strength over a faint base at ±10 pp; a thin
    row at half strength; exactly 0.0 is the base. SPY's bar reaches the cell's edge at ±5% (its weeks are small).
15. **Sub-row order:** priced first, then not priced, then left out (flagged, then no resolved security), by ticker
    within; recomputed per window.
16. **The counting names row expands too** (every counting company once).
17. **Role rows are labeled as groups** ("Payers", "Counter-case", "Direct reads", "Participants", "Financiers",
    "Benchmarks"), with the role's operator-approved description on hover.
18. **The picker** is a search box filtering a native select; the current card stays listed while the search narrows
    the rest. A→Z is by name, any case, ties by id. The link count is the real links today (Discovered included,
    Context and Bench not). A picked card stays picked across an as-of change while it is still counted; otherwise the
    first A→Z.
19. **The picker reads no roster:** it uses `cards_that_existed` over the live cards rather than `cards_as_of`, which
    would load every card's roster at a past as-of.
20. **The one card's roster follows `cards_as_of`'s rule for one card:** the live view reads today's roster
    (`thesis_repo.get`); a past view reads `get_asof` and `snapshot_exists_asof` for the note. It does not call
    `cards_as_of` for every card on each card switch.
21. **The 404 reads the cached picker list** (the cards counted at the as-of) before the grid's read.
22. **The roster note shows whenever today's roster stands in** for the card (no snapshot by the as-of), naming the
    Sep 15, 2026 date, the same note the other dashboards show.
23. **The window is an integer enum on the wire** (`ChainWaveWeeks`: 13 | 26 | 52); default 26.
24. **The header** labels the month where it changes (by each week's Monday) and the to-date week; each column carries
    its full dates on hover.
25. **Layout:** a 240px label column (150px on a phone) that stays in view while the weeks scroll sideways; cells at
    least 9px wide; the grid scrolls inside the page, never the page itself.
26. **Keyboard:** the grid is one tab stop (the newest SPY week first), the arrow keys move the focus, and focus drives
    the same readout as hover.
