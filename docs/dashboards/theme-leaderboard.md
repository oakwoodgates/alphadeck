# Theme Leaderboard

> Slug `theme-leaderboard` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 250`.
> The operator asked for this page on 2026-10-30 ("yes, let's do it") from the first brainstorm's list, under the standing
> allowance that a dashboard may rank by an opinionated formula as long as code computes it from data and the formula
> is stated (`docs/DASHBOARDS.md` rule 3). The choices the build makes that this plan does not spell out go under
> "Choices to confirm" for the operator's review. The seventh page of the TRADING set.

**Label:** Theme Leaderboard. **Description:** "Every counted card ranked by a stated composite of its tape, its
breadth, its keys, its adoption curve and its insiders, with the parts beside the score."

It answers "which narratives is the data moving behind right now": the Rotation Map shows a card's median returns
among other groupings, the Narrative Index draws one card's line, the Hot Board ranks names. Nothing ranks the CARDS
by one stated formula with its parts visible. This page does. What it ranks is what the data shows about each card's
names and record; it never judges whether a thesis is good (invariant #4), and the page says so in its framing line.
Display only: it never feeds a call, never writes, never calls the LLM.

## Who is a row

Every card counted at the as-of (`baskets.cards_as_of`), whatever its state (the state chip in the Board's colors is
the one color on the page). Archived cards are out (the shared read's default). A card with no recorded call keeps its
row with the record parts blank and is counted. Only the roster's **counting names** feed the tape parts (Context and
Bench never; `baskets.counted_rows` with `include_silent=False`); a name on the flagged-price list (`baskets.price_flags`,
rule 5) is left out of every tape reading and counted per card.

## The five parts (each the card's percentile among the cards shown, 0 to 1; equal weights, PROPOSED)

1. **tape** — the median 30-day return of the card's priced counting names minus IWM's 30-day return
   (`trailing_returns`.`ret_30d` per name, the Rotation Map's median rule with its `MIN_PRICED` floor; IWM through
   `pit.benchmark_prices`); "median of k" on hover.
2. **breadth** — the share of counting names above their 50-day SMA (`signals/display/theme_breadth.py::breadth_for`,
   one read per card; "x of n"); the share above the 200-day rides along as a column.
3. **keys** — the recorded card in effect (the What Changed read): 0, 1 or 2 keys turned (`key_conviction.turned` +
   `key_confirmation.turned`); Managing counts as 2. Shown as "keys 1 of 2 (confirmation off)".
4. **adoption** — the card's EDGAR term-mention slope from `pit.term_mentions(thesis_id)`: documents summed over the
   card's SIGNAL terms for the last three CLOSED months against the three before, a signed percent; "not counted yet"
   when fewer than six closed months are stored (that part is then missing for the card).
5. **insiders** — distinct open-market buyers across the counting names in 30 days (`insider_flow_90d`.
   `distinct_buyers_30d` summed), with the names on hover.

**Score = the mean of the five percentiles × 100**, the parts on hover ("tape 0.80 · breadth 0.55 · keys 0.50 ·
adoption 0.90 · insiders 0.20"). A card missing any part reads "—", sorts after every scored card and is counted; ties
share a percentile. The score is recomputed for the cards shown (filters change it) and the page says so. The formula
is printed under the table. Nothing writes the score anywhere.

## The columns

Card (opens the Cockpit, `onSelectThesis(thesisId)`) · state chip · score as a short gray bar with its number ·
the five parts as figures (30D vs IWM in points with "median of k"; above 50-day x/n; keys; adoption % with the two
spans on hover; buyers) · names priced / counting / flagged · 7D median · RS leaders (count of `relative_strength`
`leading` names) · the roster's cap mix as five small counts (the Peer Table's `cap_cell` tiers at the as-of; display
only; blanks counted) · the card's armed names count when Armed. **Default order:** score, descending. Every column
sorts. **Filters:** state; "has adoption months"; a card search box.

## Loudness

The trading set's tape look (`docs/dashboards/hot-board.md` §Look). The score bar is gray; the sign tone only on the
signed tape figures; the state chip is the one color. No accent on this page (no exception to mark). The framing
line above the work line: "A rank of what the data shows about each card's names and record, by the formula below.
It is not a judgment of any thesis."

## Past as-of

Every part at the as-of: the roster from the snapshot in force, the card in effect from rows on or before the as-of,
the display members on the public clock, the term months closed by then. A card created after the as-of is not a row.

## The quiet "show the work" line

The as-of; the clock; cards counted / scored / missing a part (which part, how many); names read / flagged; cards with
term months / without; the formula in force; the cache state and `compute_ms`.

## Cache

`dashboards.cache.cached(conn, "theme-leaderboard", (clock,), asof, ...)` holds every card's parts and columns; the
percentiles, score, filters and sorts derive on the client from that one read (a pure `score.ts` mirroring the backend's
percentile rule, tested against the same fixtures, as the Hot Board does). The cache clears on a new run or a basket
edit (rule 7). **Cost:** three display members (`trailing_returns`, `insider_flow_90d`, `relative_strength`) once per
counting name across every counted card (about 1,500 names at about 16 ms each) plus one breadth read per card: expect
30 to 60 s cold, like the Leaders and Peer Table all-cards reads; MEASURE and record it here.

## Backend and frontend

- `backend/dashboards/theme_leaderboard.py`, pure: the parts per card from the readings, `adoption_slope`, the median
  rule, `percentiles` and `score` (the Hot Board's rule), the counts.
- `backend/app/routers/dashboards/theme_leaderboard.py`: `GET /dashboards/theme-leaderboard?asof=`. `known_at, clock =
  serve_view(asof)` threads into every read. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_theme_leaderboard.py`: each part on hand-built inputs (the median with its floor, the
  breadth share, keys for every state, the adoption slope with the partial month excluded, the buyers sum); percentiles
  and score (ties, a missing part → "—" last, recomputed on a subset); Context and Bench never counted; a flagged name
  left out and counted; and, DB-backed, **a card created after the as-of is not a row** and **a term month stored
  after the as-of is not in the slope**; the router off one cached read.
- `frontend/src/dashboards/theme-leaderboard/`: `entry.ts` (slug `theme-leaderboard`, order 250, `asofAware: true`),
  `ThemeLeaderboard.tsx`, `score.ts`, `boardView.ts`, `useThemeLeaderboard.ts`, `theme-leaderboard.css`, tests.
- **Layout:** the tape look; one row per card; on a phone the parts wrap under the card name, the score first.

## Known limits (stated on the page too)

- **The score is relative to the cards shown** and means nothing across days.
- **The parts are display readings and the record**, never call inputs; a high score is not a call.
- **The adoption part** counts documents matching the card's terms on a weekly-refreshed table and is missing for a card
  with fewer than six closed months.
- **Medians over priced counting names;** a card with fewer priced names than the floor has no tape part.

## Out of scope

Any wording that judges a thesis; any write of the score; sizing or instrument choice (#4, #5); the LLM.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: the five parts with equal weights; keys as the record part (Managing
= 2); the three-against-three closed-month slope; percentiles among the cards shown; no accent.
