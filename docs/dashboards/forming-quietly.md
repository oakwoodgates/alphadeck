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
