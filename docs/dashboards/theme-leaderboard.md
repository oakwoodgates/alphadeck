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

### Built choices to confirm

The build made these choices that the plan above does not spell out. Each is the operator's to confirm or change.

1. **Keys is the count 0, 1 or 2.** Managing counts as 2. A card with no recorded call at the as-of has no keys part (it is
   missing, so the card has no score), never 0. The count has three values, so most cards tie on it and share a percentile.
2. **Breadth has no `MIN_PRICED` floor.** It is `theme_breadth.breadth_for`'s own reading ("x/n" is always shown, so a share
   over two names reads as "2/2"). A name with too little history for it (under 70 bars) is shown and not counted, as the
   Cockpit's basket header does. A flagged name is left out of the card's breadth read.
3. **The 200-day share comes from a fourth display member.** The plan's cost line names three members. The share above the
   200-day average is read per name from `sma_position` (`pct_vs_slow`), because `breadth_for` looks back 250 calendar days and
   cannot reach 200 bars. It rides along as a column and is not one of the five parts.
4. **Adoption: what "closed" and "the card's terms" mean.**
   - A month is closed once its last day is before the as-of, so the as-of's own month never counts, even on its last day.
   - The sum is over the card's CURRENT SIGNAL terms (the term set is not versioned, so at a past as-of today's terms stand;
     a term removed since stays stored and is ignored).
   - Only a term with all six closed months stored counts; a term added since the last weekly run, or missing a month, is left
     out and counted ("4 terms of 5 counted" on hover).
   - A document matching two terms counts twice (the page says so).
5. **Adoption with no base.** When the earlier three months total 0 documents there is no percent to state, so the part is
   missing ("no base"), even if the recent three have documents. The alternative (rank it highest) would put a 0-to-1 jump
   above a real trend.
6. **Insiders.** The buyers are summed over the counting names that have a Form 4 reading. A name with no Form 4 ingested is not
   a zero; a card where no name has one has no insiders part. A flagged name keeps its Form 4 count (it is not a price reading,
   the Hot Board's and the Peer Table's precedent) while every price reading leaves it out.
7. **Tape against IWM.** IWM's 30-day return is read once (`trailing_returns` over `pit.benchmark_prices("IWM")`) and subtracted
   from each card's median. The Hot Board's per-name "same last close day as IWM" check is not applied: a stopped feed is
   already a data flag and the name is left out.
8. **The percentile rule is copied, not imported.** `percentiles` in `theme_leaderboard.py` is the Hot Board's rule written
   again (a dozen lines) so each dashboard deletes cleanly. The page's `score.ts` and the backend are both tested against
   `frontend/src/dashboards/theme-leaderboard/__tests__/score-fixtures.json`, derived by hand.
9. **The payload ships the five parts, not the score.** The cards arrive in the default order (the score among every card
   counted); the page recomputes the percentiles and the score for the cards it shows, so two different scores are never on
   the screen.
10. **"has adoption months"** keeps the cards with at least one SIGNAL term whose six closed months are stored (the adoption
    part is counted, or has no base). It shows only when some, not all, cards qualify.
11. **Extra columns the plan implies.** The cap mix is five sortable columns (nano to large), one per band; the blanks (no cap)
    are in each cell's hover. A small "R" marks a card whose recorded state was written by the backfill (shown only then).
12. **Sorting.** State sorts in lifecycle order (Armed, Managing, Warming, Incubating) and a card with no call is always last;
    a blank is last in either direction on every column.
13. **Phone layout is CSS only.** On a narrow screen each row becomes a block: the score, the card and its state on the first
    line, the parts under them with their labels. The build did not render it in a browser.
14. **The recorded card in effect** is read as the What Changed and Hot Board pages read it: the greatest `(asof, seq)` with
    `asof` on or before the as-of, not gated by `known_at`.
15. **Cost not measured.** The plan asks to MEASURE the cold read here; the build did not (the orchestrator measures on dev
    with a production copy). What the read does, per request: four display members plus the price, shares and split reads for
    the cap, once per counting name (a name only on Context or Bench is never read); one `breadth_for` view per card (a second
    read of each name's last 250 days of bars); one term-mention read per card; the data-flag read; IWM's bars.

### MEASURED (dev with a prod data copy, 2026-10-29, after the nightly pass)

- Live as-of: 62 cards, 51 KB, cold compute 96,032 ms (four display members over every counting name across the 62
  cards plus one breadth read per card), cache hit under 50 ms. Past as-of 2026-09-15 on the public clock: 10 cards,
  10 KB, cold 33,352 ms.
- The cold read is the heaviest in the app (the Peer Table's all-cards read measured 69 s, the Trigger Log 33 s). The
  cache clears on every new run, so the first visit after a nightly waits for it. A post-nightly cache warm-up (the
  cron hitting each heavy dashboard once after the pass) is the follow-up that fixes this for every such page at once;
  a lighter read (fewer members, or the Rotation Map's readings shared) is the per-page option.
