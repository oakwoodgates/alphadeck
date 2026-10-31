# Hot Board

> Slug `hot-board` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 190`.
> The operator approved the TRADING set and this page in it (2026-10-29: "write the plans"), including that a
> dashboard may rank by an opinionated formula as long as code computes it from data and the formula is stated. The
> choices the build makes that this plan does not spell out go under "Choices to confirm" for the operator's review.
> The rules every dashboard follows: `docs/DASHBOARDS.md`. The first page of the TRADING set; §Look below defines the
> set's shared look, which the other four plans reference.

**Label:** Hot Board. **Description:** "Every name the record shows Armed, one row each, ranked by a stated heat
formula: how recently it armed, how loud its tape is, how it leads the market, how far it has already moved."

It answers "which armed names deserve my next hour": the Board shows cards, the Cockpit one card, Leaders the tape
across every name whatever its card's state. The Hot Board starts from the CALL: the names the recorded card in effect
at the as-of lists in `armed_members` (both keys on, on that name), and puts the tape beside each. Display only: it
reads the `calls` log and the display members, never the live call; it never feeds a call, never writes, never calls
the LLM. Its ranking is a formula the page prints; it is not a verdict on any idea (invariant #4).

## Who is a row

- **The cards counted at the as-of** (`baskets.cards_as_of`), each read as its recorded call in effect at the as-of
  (the What Changed read: `DISTINCT ON (thesis_id) … WHERE asof <= D ORDER BY thesis_id, asof DESC, seq DESC`).
- **Default rows:** every `armed_members[]` entry of every card whose card in effect is **Armed**: one row per
  (card, name). A name armed on two cards is two rows, each with an "also armed on N other cards" chip; the tape
  columns are the same, the call columns differ (grades and `confidence` are per card).
- **Toggles, off by default:** "Warming names" adds, from each Warming card, the names with at least one live entry
  trigger in `triggers_fired` (one key turned; the row says which key); "Watch names" adds each Armed card's
  `watch_members` (confirmation only); "Managing" adds each Managing card's held and armed members. Every toggle's
  count shows on the toggle. Incubating cards and cards with no recorded call are counted on the work line.
- **Two reads of one candidate set (operator decision, 2026-10-30).** The candidate set (armed, warming, watch and
  managing rows) is listed once, from the recorded cards alone. The page's default read (`rows=armed`) runs the tape
  for the **armed** rows and carries the other three kinds only as COUNTS off those same recorded cards (no display
  member, data-flag read, arm-night walk or cap runs for a name that is only counted); the toggles show those counts.
  The first time a toggle that adds rows is checked, the page asks for the full read (`rows=all`: every candidate
  row with its tape, the rows the page carried before this change) once, and everything after that is client side.
  The work line says which read is loaded.
- A name whose `security_id` is not on the roster snapshot (`thesis_repo.get_asof`) still has its row (the master's
  ticker, `master.tickers_for`); a name with no ticker shows its id and is counted.

## The columns

**The call (per card):** ticker (opens the Cockpit on this card and name, `onSelectThesis(thesisId, ticker)`), the
card chip, the member's `verdict` (starter entry · core entry · flip only, the Board's labels), `entry_grade` (flip ·
core), `confidence` as "setup strength 0.62", the keys as a compact trigger list ("insider · breakout"; hover: each
trigger's `label`, `kind`, `grade`, fire date and source link by the Evidence Ledger's rule,
`dashboards/filings.py::source_url` with the issuer CIK), the risk signals on the card that name this security
(count; kinds on hover).

**The clocks:** the member's **arm night** (the first night of the card's current recorded Armed run on which it
appears in `armed_members`; the walk is the Entry Windows plan's, §Who is a row, same rule, same test); **sessions
since** the arm night on IWM's bar calendar (`dashboards/series.py::session_calendar`); **sessions left** to
`arm_until` ("closed N ago" past it); **days to `exit_by`**, a countdown that counts from the **countdown origin**
(F2, `DASHBOARDS.md` rule 2: the action day, the session after the night, on a live view; the as-of on a past one),
reading "today" on the origin day and, on a past view, carrying the horizon's date; the card's `entry_window_closing`
and `lapsing` flags as quiet tags.

**Since the arm night:** the name's as-traded return from the first close on or after the arm night (the
Scoreboard's entry rule) to the last close on or before the as-of (`pit.price_history`), and IWM over the same span
(`pit.benchmark_prices`).

**The tape (the display members at the as-of, `signals/display/`, run once per name through `baskets.per_company`
with `display_bounds()`):** RVOL 20 (`rvol`.`rvol20`), vs 50d (`sma_position`.`pct_vs_fast`), % off the 52-week high
(`range_52w`.`pct_off_52w_high`), RS vs IWM (`relative_strength`.`rs_iwm`, the headline `leading` as a mark), 7D and
30D (`trailing_returns`), insider buys 30d as "buys/buyers" (`insider_flow_90d`.`buy_count_30d` /
`distinct_buyers_30d`), $vol 20d (`volume_regime`.`adv_usd_20d`), a 90-bar sparkline (`price_path`). A name on the
flagged-price list (`baskets.price_flags`, rule 5) KEEPS its row: its tape cells read "flagged: <reason>" and its
heat is "—"; the work line counts it.

**Size:** the approximate market cap at the as-of and its tier (the Peer Table's `cap_cell` rule and inputs,
`dashboards/peer_table.py`; labeled approximate; blank with its gap reason when unpinned), a filter dimension only.

## Heat (the stated formula)

Heat ranks the rows on the page against each other; it is recomputed for the rows shown (toggles and filters change
it) and the page says so. Four parts, each the row's percentile among the shown rows with a reading (0 to 1):

1. **fresh** — fewer sessions since the arm night ranks higher;
2. **loud** — higher RVOL 20 ranks higher;
3. **leading** — higher RS vs IWM ranks higher;
4. **unextended** — a smaller return since the arm night ranks higher (the platform's timing opinion: a name far
   above its arm-night close has already moved).

**Heat = the mean of the four, shown 0 to 100** with the four parts on hover ("fresh 0.90 · loud 0.40 · leading 0.70
· unextended 0.80"). Equal weights (PROPOSED). A row missing any part (no tape, flagged, no bar since the arm night)
reads "—", sorts after every scored row and is counted. Ties share a percentile. The formula is printed under the
table in one line, and the column header reads "heat (stated formula)". Heat never feeds anything.

**Default sort:** heat, descending. Every column sorts. **Filters** (chips, each restating the row count): grade,
trigger kind, card, cap tier, "entry window open only".

## Look (the trading set's shared look; the other trading plans reference this section)

The research set is document-like; the trading set is a **tape**: dense rows about 26 px high, tabular numerals in
the mono font for every figure, a sticky header, no cards, no chrome, no borders between rows beyond a hairline, the
quiet work line above the table, filters as text chips. Tone: the sign tone (the app's positive and negative mixed
55% into gray, the Rotation Quadrant's recipe, set in each page's own CSS) only on signed returns; the Board's state
colors only on a state chip; ONE accent per page for the one exception the plan names (here `entry_window_closing`:
at most two sessions left). Heat draws as a short gray bar behind its number, never a color. Nothing bold but the
ticker. On a phone the table scrolls sideways with the ticker column pinned.

## Past as-of

The card in effect is the row recorded on or before the as-of; the run is walked on rows at or before it; the display
members read at the as-of on the public clock; the bars end at the as-of. A card armed after the as-of has no rows; a
member that joined later is not a row then.

## The quiet "show the work" line

The as-of; the clock; **which read is loaded** (the armed names alone, with what waits behind the toggles, or every
candidate row); cards counted / Armed / Warming / Managing / Incubating / no call yet; rows shown and what the
toggles add; names without a reading (per member), flagged (opens to the tickers), without a bar since the arm night;
the session calendar; the entry rule; the heat formula in force; the cache state and `compute_ms`.

## Cache

`dashboards.cache.cached(conn, "hot-board", (rows, clock, origin), asof, ...)` holds, per read, the rows that read carries
with their call fields, clocks, since-arm returns, tape readings and cap, plus the counts of every kind. Two entries
per as-of: `rows=armed` (the armed rows; the page's default read) and `rows=all` (every candidate row: Armed,
Warming, watch, Managing; read only after the first toggle). The toggles, filters, sorts and heat derive on the client
from whichever read is loaded. The `all` read is its own cold compute (it is not sliced out of, or into, the armed
entry). The cache clears on a new run or a basket edit (rule 7). Expected cold cost: the display members for the names
the read carries (about 16 ms a name, `docs/DISPLAY_SIGNALS.md`) plus their bars; the numbers measured for the
one-read version are under "MEASURED, BEFORE the on-demand change" below, and the numbers for the two reads are
recorded there once measured.

## Backend and frontend

- `backend/dashboards/hot_board.py`, pure: the row set from the cards in effect (armed, warming, watch, managing),
  `read_candidates(cands, rows)` (the subset a read runs the tape for) and `count_candidates` (every kind's count),
  `arm_night(run_rows, security_id)`, the since-arm return, the tape readings per member key, `heat(rows)` (the
  percentiles, the mean, the missing rule), the counts.
- `backend/app/routers/dashboards/hot_board.py`: `GET /dashboards/hot-board?asof=&rows=armed|all` (`armed` is the
  default: the armed rows + the counts of every kind; `all`: every candidate row + the counts; any other `rows` is a
  422). `known_at, clock = serve_view(asof)` threads into the `calls` read, `baskets.per_company`, the bars and the
  benchmark. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_hot_board.py`: the row sets (an Armed card's members; a Warming card's trigger
  names with the key named; watch; managing); the arm night per member (a member that joined the run later); heat
  percentiles on hand-built rows (ties, a missing part → "—" last, recomputed on a subset); the since-arm return and
  the IWM span; a flagged name keeps its row with grayed tape; the two reads (the default carries the armed rows plus
  the other kinds' counts and runs no tape, flag read, ticker read or arm-night walk for a counted-only name; `rows=all`
  is the full set; an armed row is the same row in both; an invalid `rows` is a 422; the two cache keys differ);
  and, DB-backed, **a card armed after the as-of has no rows** and **a member added after the as-of is not a row
  then**; the router off one cached read per `(rows, clock)`.
- `frontend/src/dashboards/hot-board/`: `entry.ts` (slug `hot-board`, order 190, `asofAware: true`), `HotBoard.tsx`
  (the table; a pure `heat.ts` mirroring the backend's percentile rule for the client-side recompute, tested against
  the same fixtures; sparklines as inline SVG), `useHotBoard.ts` (the armed read, and the full read held back until a
  toggle needs it), `hot-board.css`, tests (the toggle fetching the full read exactly once is counted against a real
  query client in `__tests__/HotBoard.reads.test.tsx`).
- **Layout:** §Look. The arm-run walk is also the Entry Windows page's: whichever lands second folds both copies
  into one shared `backend/dashboards/arm_runs.py` at integration (the reviewer's job, not the builder's).

## Known limits (stated on the page too)

- **Heat compares the rows shown with each other;** it is not a score with a meaning across days.
- **The arm night is the recorded night;** the fire dates are in the hover.
- **The tape is the display members' own reading** (`docs/DISPLAY_SIGNALS.md`): split-adjusted closes, no dividends.
- **A name on two cards is two rows.**

## Out of scope

Any wording that tells the operator what to do; sizing or instrument choice (#4, #5); writing heat anywhere; the
Leaders lists (near-high, coiling) as columns; options or intraday data; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: Armed members as the default rows with Warming, watch and
Managing behind toggles; the four heat parts with equal weights; percentiles among the rows shown; IWM as the
comparison and the session calendar; the one accent on `entry_window_closing`; the flagged name keeps its row.

### Built choices to confirm

The build (`backend/dashboards/hot_board.py`, `backend/app/routers/dashboards/hot_board.py`,
`frontend/src/dashboards/hot-board/`) made these choices where this plan is silent or could not be followed as
written. Each is the operator's to confirm or change.

**A plan line that could not be followed as written**

1. **"RS vs IWM" is the 30-day return minus IWM's, not `rs_iwm`.** `relative_strength.rs_iwm` is the name's close
   divided by IWM's close, a price-LEVEL ratio: a $400 stock reads a bigger number than a $5 one for the same move, so
   ranking it would rank share price. The "leading" heat part and the "vs IWM 30D" column use the Peer Table's tape
   rule against IWM instead: the name's `ret_30d` minus IWM's `ret_30d` (IWM by the same pure rule,
   `trailing_returns.compute` over `pit.benchmark_prices`), in percentage points, blank when the two last closes are not
   the same day. The headline `leading` stays as a quiet "RS" mark beside the figure (hover: which benchmark, SPY and/or
   IWM, the 13-week high is against).
2. **Sessions left to `arm_until` are counted Mon to Fri, not on IWM's bars.** A future session has no IWM bar. The
   count is the call path's own (`calls.assembler.sessions_left`, `domain.trading_days`: Mon-Fri minus the NYSE's
   market holidays since F5), so the page's number and the card's `entry_window_closing` flag always agree. "Closed N ago" and "sessions
   since the arm night" are counted on IWM's bar calendar, as planned.
3. **The "card's `entry_window_closing` and `lapsing` flags" are the member's own** (`MemberCall`); `CallCard` has
   neither. `arm_until` and `exit_by` are the member's, falling back to the card's when the member has none.

**The heat**

4. **The percentile is the mid-rank over n - 1:** `(below + (equal - 1) / 2) / (n - 1)`. The best of n rows is 1, the
   worst 0, a tie shares the average of the ranks it spans, a lone row (or rows all equal) is 0.5. Heat is the mean of
   the four times 100, kept unrounded and shown as a whole number.
5. **A row joins the pool of every part it has a reading for,** even when another part keeps it from a score. So a
   Warming or watch row (no arm night, no "fresh") still ranks the armed rows' loud and leading parts while its toggle
   is on, and a flagged row still ranks "fresh". That follows the plan's wording ("among the shown rows with a
   reading"); the alternative (only rows that can be scored join) is a one-line change in `heat.ts` and `heat()`.
6. **The backend's `heat()` is the reference, not the payload.** The wire carries each row's four readings and no heat
   (the page recomputes it on the rows shown). The backend and `heat.ts` are tested against ONE shared fixture file,
   `frontend/src/dashboards/hot-board/__tests__/heat-fixtures.json`, read by both test suites.

**Who is a row, and the arm night**

7. **A member's arm night is the start of its CURRENT unbroken streak:** walk the nights back from the one in effect
   while the night's state is in the run AND the member is in that night's `armed_members`. A member that dropped out of
   the list and came back has its arm night at the return, not at its first appearance in the run. (The plan's wording,
   "the first night of the run on which it appears", reads either way; this is the one place the choice matters for
   "fresh".)
8. **A Managing card's run also walks back over its Armed nights** (it was armed before the position was entered); an
   Armed card's run stops at a Managing night. A Warming or watch row has no arm night, so no clocks, no return since
   the arm night and no heat.
9. **Warming rows: which key** comes from the call config's own kind sets (`DEFAULT_CONFIG.conviction_kinds` and
   `confirmation_kinds`), per trigger; a name with both shows "conviction + confirmation key". The dormant
   revenue-acceleration exception (keyed on a detector the wire does not carry) is not followed.
10. **Watch rows come only from an Armed card,** and not for a name already armed on that card. A Managing card's watch
    members are not rows.
11. **"Also armed on N other cards"** counts the OTHER cards on which the same security is an Armed row, and shows on
    any row of that name (a watch or Warming row of a name armed elsewhere carries it too); the hover names the cards.
12. **An arm night before the earliest bar the point-in-time view reads** (`display_bounds()`, about 450 days) gets no
    session count and no return ("before the window"), counted on the page's lines, because the first close on or after
    it would not exist.
13. **IWM "over the same span"** is IWM's first close on or after the arm night to IWM's last close on or before the
    as-of, independent of the name's own dates (they differ only when the name's tape is stale).
14. **A reconstructed arm night** shows an "R" and is counted on the work line; the Idea Timeline's `exclude`
    annotations are not applied (they bear on the Scoreboard).

**Flagged names**

15. **A flagged price history keeps its row;** every price-derived cell (RVOL, vs 50d, off high, vs IWM, 7D, 30D, $vol,
    the return since the arm night, the sparkline, the cap) reads "flagged" with the reason in its hover, and it has no
    heat. The insider counts stay (a Form 4 count is not a price reading), as the Peer Table keeps them.

**The page**

16. **Controls render only when they discriminate** (Workbench principle 3): a toggle appears only when it adds at least
    one row (with its count), a filter chip group only when the rows on offer differ on it, and "Entry window open only"
    only when some rows are open and some are not. "Open" = the first session after the as-of is on or before
    `arm_until` (F3, the call path's own lapse test), so an open row always has at least one session left; a window
    with none (`arm_until` on the as-of, or a weekend end after a Friday as-of) reads "closed".
17. **Sorts:** a text column starts A to Z, a figure biggest first, a blank last in either direction, ties by ticker; a
    closed entry window sorts below every open one (more sessions since it ended = lower). The sparkline column is a
    picture and does not sort.
18. **The state chip** (the Board's state color) appears only on rows a toggle added (warming, managing); an Armed row
    carries none, since it would be true of every row. The one accent is `entry_window_closing` on the "sessions left"
    cell.
19. **Columns beyond the plan's list:** the company's name is in the ticker's hover; the "Risk" cell is the count with
    the kinds on hover; "Setup strength" is the call's `confidence` as 0.00 to 1.00 text.
20. **Coupling:** the cap reuses `dashboards/peer_table.py` (`cap_cell`, `CapCell`, `CompanyReads`, `Gap`,
    `metric_of`) by import, not a second copy of the rule. Cutting the Peer Table therefore needs those moved to a shared
    module first (the reviewer's fold, like the arm-run walk).
21. **The sparkline** is the `price_path` series rounded to 4 decimals, drawn as a 72 by 16 inline SVG path, broken on a
    blank slot.

**Not measured**

22. **The cold cost is NOT MEASURED by the builder** (the local database holds only the demo seed and the stack's
    Postgres is prod's). The orchestrator records it from dev with a prod copy. A read does one `per_company` pass
    over the distinct securities of the rows it carries (the armed names for `rows=armed`, all four kinds for
    `rows=all`), eight display members each, plus one data-flag read and one IWM read. The builder did not measure the
    cost of the on-demand change either: the tests count which securities each read is asked for, not milliseconds.

**The on-demand read (operator decision, 2026-10-30: the open question under the first measurement, accepted)**

23. **The default read carries the armed rows and counts the rest; `rows=all` is today's read.** `GET
    /dashboards/hot-board?asof=&rows=armed` (the default) returns the armed rows with their tape and
    `candidate_counts` (`armed`, `warming`, `watch`, `managing`: the rows the recorded cards in effect list, counted
    with no display member, flag read, ticker read or arm-night walk for a name that is only counted); `rows=all`
    returns every candidate row with its tape, each row byte-for-byte the row the page carried before this change; any
    other `rows` value is a 422. The wire gains `rows_loaded` (which read this is) and `candidate_counts`. The cache
    key is `(rows, clock)` per as-of: the two reads are two entries and the `all` read is its own cold compute, so the
    armed names' tape is computed once per read that carries them (a slice of the armed entry would save that, and
    would couple the two entries).
24. **How the page asks.** The toggles show their counts from the default read. The first time a checked toggle adds
    rows, the page requests `rows=all` once for that as-of (10-minute client cache, like the default read) and keeps
    the armed rows on screen meanwhile, with "reading the warming, watch and managing rows…"; a failed full read says
    so and leaves the armed rows (unchecking every toggle and checking one again retries). Unchecking every toggle
    returns to the armed read (the full one stays cached, so checking a toggle again costs nothing). Moving the as-of
    reads the full rows for the new as-of only while a visible, checked toggle adds rows there. Heat is still
    recomputed among the rows shown; the toggle's hover says the rows load on first use; the work line says which read
    is loaded.
25. **What follows the read:** the work line's flagged names (and the "N names left out for a flagged price history"
    note) and its no-ticker count describe the rows the loaded read carries, so a flagged name that sits only on a
    warming, watch or managing row appears there once the full read loads. The arm-night walk reads the calls log only
    for the cards that have an armed or managing row in the read (the full read no longer reads the log of an Armed
    card with watch members alone; no row changes).

### MEASURED, BEFORE the on-demand change (dev with a prod data copy, 2026-10-29, after the nightly pass)

These are the numbers for the one-read version (every candidate row in one request, the toggles free).

- Live as-of: 95 Armed rows shown by default; the one read carries 488 candidate rows (the Warming toggle adds 122,
  the watch toggle 271), 1.1 MB, cold compute 19,586 ms, cache hit 70 ms; 5 names flagged (rows kept, tape grayed).
- Past as-of 2026-09-15 on the public clock: 133 candidate rows, 298 KB, cold 5,858 ms, 1 flagged.
- The 95 rows ranked by heat render with every column and filter at desktop width; the phone check waits for the
  re-measure after the Trigger Log fix (the backend was busy computing another page during the first phone load).
- The open question these numbers raised (load the Warming and watch rows on demand, for about half the payload and
  the cold compute) was accepted by the operator on 2026-10-30; see items 23 to 25.

### MEASURED, AFTER the on-demand change
- Live as-of 2026-10-30: the default read (`rows=armed`) 95 rows, 257 KB, cold compute 8,485 ms, cache hit 100 ms, with
  the toggle counts carried (122 Warming, 271 watch, 0 Managing); the full read on first toggle use (`rows=all`) 488
  rows, 1.1 MB, cold 19,794 ms, hit 300 ms. The morning visit now waits 8.5 s cold instead of 19.6 s, and the
  post-nightly warm-up (its own change) makes it a hit.


Not yet measured: the orchestrator records the default read's payload and cold compute, and the `rows=all` read's,
from dev with a prod data copy, on the same live and past as-ofs as above.
