# Forming Quietly

> Slug `forming-quietly` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 240`.
> The operator asked for this page on 2026-10-30 ("yes, let's do it") from the first brainstorm's list; the choices the
> build makes that this plan does not spell out go under "Choices to confirm" for the operator's review. The rules every
> dashboard follows: `docs/DASHBOARDS.md`; invariant #7 (inverse loudness) is this page's design brief. The sixth page
> of the TRADING set, and the quietest page in the app.

**Label:** Forming Quietly. **Description:** "The cards where a key has turned or signals are gathering but nothing is
Armed yet, at low energy, with what is still missing."

It serves the operator's edge directly: early narrative-spotting. The Board shows Warming cards one at a time; the Hot
Board lists armed names and, behind a toggle, Warming names with a live trigger; nothing lists what is FORMING across
the cards with what each still needs. This page does, at the lowest energy the app has: gray rows, no accent, no
badge, no count in the menu. Display only: it never feeds a call, never writes, never calls the LLM; it never ranks
cards by promise (invariant #4) and never says "early", "setting up" or "about to".

## Who is a row

- **The cards counted at the as-of** (`baskets.cards_as_of`), each read as its **recorded call in effect at the as-of**
  (the What Changed read of the `calls` log: the greatest `(asof, seq)` with `asof <=` the as-of).
- **Rows by default:** every **Warming** card (one key turned), and every **Incubating** card that shows at least one
  **quiet signal** (below). An Incubating card with none is counted on the work line; a toggle "Show every Incubating
  card" adds them. Armed and Managing cards are never rows (the Hot Board and Entry Windows have them) and are
  counted. A card with no recorded call is counted ("no call yet").

## The row (one card)

- **Card** (opens the Cockpit, `onSelectThesis(thesisId)`), the state chip in gray (no Board color on this page),
  **days in state** (the current recorded run's first night, the Idea Timeline's rule: the highest `seq` wins a night,
  reconstructed nights marked) and the night the key turned when Warming.
- **The key that turned** (Warming): the recorded card's `key_conviction` / `key_confirmation` with `turned`, `label`
  and `detail`, quoted as recorded ("Conviction · insider cluster on 2 names"); the names behind it as ticker chips
  (each `triggers_fired` entry's `security_id` → ticker, kind on hover, source link by the Evidence Ledger's rule,
  `dashboards/filings.py::source_url`).
- **What is still missing:** the recorded card's `missing[]` lines verbatim (the assembler's own words: "Volume-confirmed
  breakout (the confirmation key)", "Risk must clear before arming: …"); the watch names (`watch_members`) as chips
  marked "confirmation only".
- **Risk signals** on the card: the count, kinds on hover (never hidden: a risk is part of what is forming).
- **Quiet signals on the roster** (the counting names at the as-of; Context and Bench never count here):
  - **insider buying:** names with an open-market buy in 30 days and the buyers per name (`insider_flow_90d`:
    `buy_count_30d`, `distinct_buyers_30d`; a quiet "cluster" word when `distinct_buyers_30d >= 2`, the Leaders rule);
  - **coiling:** names whose `vcp` headline reads `coiling`, with the contraction ratio on hover;
  - **leading:** names whose `relative_strength` headline reads `leading` (RS at a 13-week high vs SPY or IWM);
  - **breadth:** "x of n above the 50-day" and "above the 200-day" from `signals/display/theme_breadth.py::breadth_for`
    (one read per card);
  - **adoption:** the card's EDGAR term-mention slope from `pit.term_mentions(thesis_id)` (the Narrative Adoption
    table): documents summed over the card's SIGNAL terms for the last three CLOSED months against the three before,
    as a signed percent with the two spans on hover; "not counted yet" when fewer than six closed months are stored;
    the current partial month never counts.
  Each quiet signal shows its n ("3 of 41 names"); a name on the flagged-price list (`baskets.price_flags`, rule 5) is
  left out of the tape readings and counted, never dropped. The display members run once per name through
  `baskets.per_company` with `display_bounds()`, three members only (`insider_flow_90d`, `vcp`, `relative_strength`):
  expected cost about 16 ms a name over the Warming and Incubating rosters; MEASURE and record it here.

**Default order:** the key most recently turned first (Warming rows by the run's first night, newest; then Incubating
rows by adoption slope, largest first; then the rest by card name). Alternatives: days in state, insider buyers,
adoption slope, card name. **Filters:** state (Warming / Incubating), "has insider buying", "has coiling", card.

## Loudness (the point of the page)

The trading set's tape look (`docs/dashboards/hot-board.md` §Look) at its quietest: every figure in the gray scale,
**no accent anywhere**, no sign tone except on the adoption slope's percent, no chip colors, the state chip gray. No
sorting label says "best" or "strongest". The page's one sentence of framing, above the work line: "Nothing on this page
is Armed. It lists what the record shows forming and what each card still needs." The menu entry carries no count.

## Past as-of

The card in effect is the row recorded on or before the as-of; the runs are walked on rows at or before it; the display
members read at the as-of on the public clock; the term months are those closed by the as-of. A card that turned Warming
after the as-of is not a Warming row then.

## The quiet "show the work" line

The as-of; the clock; cards counted / Warming / Incubating with a quiet signal / Incubating without (shown or hidden) /
Armed / Managing / no call yet; names read / flagged (opens to the tickers); cards with term months / without; the
members run; the cache state and `compute_ms`.

## Cache

`dashboards.cache.cached(conn, "forming-quietly", (clock,), asof, ...)` holds every Warming and Incubating row with
its key, missing lines, names and quiet signals; the Incubating toggle, filters and orders are client side. The cache
clears on a new run or a basket edit (rule 7).

## Backend and frontend

- `backend/dashboards/forming_quietly.py`, pure: the row set and the counts, the key and missing lines, `run_start`
  (the current run's first night), the quiet-signal readings per card from the members' output, `adoption_slope(rows,
  asof)` (three closed months vs the three before), the orders.
- `backend/app/routers/dashboards/forming_quietly.py`: `GET /dashboards/forming-quietly?asof=` (every row + the counts).
  `known_at, clock = serve_view(asof)` threads into the `calls` read, `cards_as_of`, `price_flags`, `per_company` and
  the term-mention read. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_forming_quietly.py`: the row set (Warming; Incubating with a quiet signal; Incubating
  without, counted; Armed and Managing counted, never rows; no call yet); the key and missing lines carried verbatim;
  the run start across several nights (a higher `seq` wins; reconstructed marked); the adoption slope (six closed
  months; five → "not counted yet"; the partial month excluded; a zero prior span → no percent, the counts shown); the
  cluster word; a flagged name left out and counted; and, DB-backed, **a card that turned Warming after the as-of is
  not a row then** and **a term month stored after the as-of is not in the slope**; the router off one cached read.
- `frontend/src/dashboards/forming-quietly/`: `entry.ts` (slug `forming-quietly`, order 240, `asofAware: true`),
  `FormingQuietly.tsx`, a pure `quietView.ts` (orders, filters), `useFormingQuietly.ts`, `forming-quietly.css`, tests.
- **Layout:** the tape look; one row per card, the quiet signals as short gray phrases in fixed columns; on a phone the
  columns stack under the card name.

## Known limits (stated on the page too)

- **Quiet signals are display members' readings** (`docs/DISPLAY_SIGNALS.md`), never call inputs; a card can be
  Incubating with every quiet signal lit and that means nothing to the call.
- **The adoption slope counts documents matching the card's terms** (a document matching two terms counts twice), on a
  weekly-refreshed table, closed months only.
- **Incubating rows are opt-in beyond the quiet-signal rule**, so the page stays short.
- **Context and Bench names never count** here.

## Out of scope

Any ranking by promise, any "early" / "setting up" wording or score; Armed or Managing cards; notifications of any
kind (#7); the LLM; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: Warming rows plus Incubating rows with a quiet signal; three
display members only; the three-against-three closed-month slope; the cluster word at two buyers; no accent on the page.

### Built choices to confirm

The build (2026-10-30) made these choices that the plan above does not spell out, each the operator's to confirm or change.
Nothing in this list was measured against prod data: the build ran on neutral fixtures, and the timing under "Cost" below is for the
orchestrator to measure on dev with a prod copy.

1. **What lights an Incubating card.** The plan says an Incubating card "shows at least one quiet signal" but gives a
   threshold for only two of the five. Built: insider buying (at least one open-market buy in 30 days, the Leaders rule),
   coiling (`vcp`'s headline), leading (`relative_strength`'s headline) and the theme-breadth member's own `thrust` headline
   light a card. The plain breadth count and the adoption slope are shown wherever they exist and never light a card alone
   (the plan names no threshold for either; a threshold on adoption or on plain breadth is the operator's to pick). The
   server sends every Incubating card with a `lit` flag; the toggle "Show every Incubating card" is client side.
2. **Breadth is the 50-day only. The plan's "above the 200-day" is NOT built.** `theme_breadth.breadth_for` reads only the
   50-day line (and its 250-calendar-day pull holds about 170 bars, fewer than a 200-day line needs); a 200-day reading
   would need a fourth display member, which the plan rules out ("three members only"). The cell reads "x of n above the
   50-day" with the thin-history names counted apart (a name with too little history is never counted as below the line).
3. **A flagged price history is left out of every reading, the insider one included** (the Leaders rule: the insider
   screen reads the price tape, and a scale break there changes which buys pass). The plan says "tape readings". The name is
   counted in the card's `names`, listed under the card's quiet signals and on the page's flagged note, never read.
4. **"The night the key turned" is the first night of the card's current run of its recorded state** (the Idea
   Timeline's rule, the highest `seq` winning a night). No per-key history is walked, so a Warming card whose other key
   turned later still reads from the run's first night. "Days in state" is calendar days from that night to the as-of
   ("0 days" on the first night). The recorded night in effect (`recorded_on`) rides in the state cell's hover, since a
   weekend as-of reads Friday's call. A reconstructed first night is marked "R".
5. **The adoption slope's term set.** It sums the card's CURRENT SIGNAL terms (`thesis.term_set`, not versioned: a past
   as-of uses today's terms; BROAD terms and a removed term's stored months are never summed). A term counts only when
   all six closed months are stored (like for like); a card with no such term reads "not counted yet", with the
   months stored and the terms partly stored or not stored in the hover. A month is closed when its last day is before the
   as-of, so the as-of's own month never counts, not even on its last day. A zero earlier span has no percent (the two
   counts show). The percent shows as a whole number. When a counted term's latest closed month was last read on or before its
   last day (the weekly job had not yet re-read it after it closed), the hover says the count may be low (`latest_partial`);
   the number is not dropped.
6. **"Cards with term months / without"** on the work line = row cards with at least one stored SIGNAL-term month among
   their six closed months, and the rest.
7. **Breadth cost.** One `breadth_for` read per row card runs on its own point-in-time view over the card's unflagged
   counting names, so a name on several row cards has its closes read once for the three members (through
   `baskets.per_company`) and once more per card for breadth. This is the plan's wording taken literally; a shared read
   would save the repeat if the measured cost asks for it.
8. **Default order and the sorts.** Warming cards by the run's first night (newest first, ties by card name); then
   Incubating cards with a slope percent (largest first); then the rest by card name. The server sends this order and
   "the record order" is the page's default. The alternatives (card name, days in state, insider buyers, adoption percent)
   are client side, a blank always last: "insider buyers" sorts on the distinct buyers summed over the names that had a
   buy. No header says "best" or "strongest".
9. **The cluster word** (two distinct buyers) sits beside the ticker of each such name in the insider cell, not as a badge
   on the card. The insider, coiling and leading cells show up to three tickers and "+n".
10. **The names behind the key** are every `triggers_fired` entry grouped by security (not split by which key the trigger
    turns); the hover lists each trigger's label, kind, grade, fire date and its source links by the Evidence Ledger's rule.
    The confirmation-only names are all of the recorded call's `watch_members`.
11. **Filters and the toggle render only when they can tell the rows apart** (workflow principle 3): the state chips need
    both states on show, "has insider buying" and "has coiling" need a card with one, the card list needs two cards, and
    the Incubating toggle needs a hidden Incubating card.
12. **Loudness.** No accent anywhere (the stylesheet names no Board state color), the state chip a gray outline, the sign
    tone only on the adoption percent. A test reads the stylesheet and the rendered markup for this and for the words the page
    never says.
13. **Cache.** `cached(conn, "forming-quietly", (clock,), asof, ...)` holds every Warming and Incubating row; the toggle,
    filters and orders are the page's.

#### Cost (PROPOSED, not measured)

The plan's expected cost is about 16 ms a name over the Warming and Incubating rosters, plus one breadth read per row card
and one term-month read per row card. The build has not measured it: record the cold `compute_ms` and the name count from
the page's own work line on dev with a prod copy here.

### MEASURED (dev with a prod data copy, 2026-10-29, after the nightly pass)

- Live as-of: 19 rows (the Warming cards and the Incubating cards lit by a quiet signal), 157 KB, cold compute
  11,997 ms (three display members over the rows' rosters plus one breadth read per row card), cache hit under 10 ms.
  Past as-of 2026-09-15 on the public clock: 4 rows, 17 KB, 1,314 ms.
