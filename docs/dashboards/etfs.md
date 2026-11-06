# ETFs

> Slug `etfs` · status `experimental` · follows the **as-of dial** (`asofAware: true`). **Signed off by the
> operator as written, 2026-11-05.** That covers the eight "Choices to confirm" as proposed, and open question 1
> as proposed: ship without holdings. Open question 2 (a flow backfill) stays the operator's spend to run or
> not; the page ships either way. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** ETFs. **Description:** "The funds on the cards from five angles: each fund against its card's
companies, money in and out, size and trading, price trend, and which cards have a fund."

One page of five panels, each a different angle on the same funds. It answers "what are the funds on my cards
doing, and are they moving with the companies or apart from them?". Display only: it never feeds a call, never
writes to the database, never calls the LLM and never makes a network request (every panel reads stored data).
Funds sit in the silent ETFs link (#493; `ETFS_LABEL` in `backend/domain/thesis.py`), so none of them moves a
call either.

## What the page reads

All of it is already stored; the page adds no ingest.

| Data | Source | Fresh |
|---|---|---|
| Fund prices and volume | `fact_price_eod`: the nightly price leg (a fund is a basket member like any other) | nightly |
| Fund shares outstanding | `fact_fund_shares`: the nightly sample from Massive.com (`backend/ingest/funds/`), read through the `etf_flow` display member | nightly |
| The cards and their rosters | `dashboards.baskets.cards_as_of` | at the as-of |
| Which rows are funds | `security_master.instrument_kind = 'etf'` | |

Not read: a fund's N-PORT holdings and its filed net assets (the Workbench pulls those live, one fund per click;
see open question 1), expense ratios and new-fund filings (neither is built).

## Which names count

- **A fund** is a roster row, as of the as-of, whose security has `instrument_kind = 'etf'`, in any link. Since
  #493 (2026-11-04) the promote keeps every fund in the ETFs link and nothing else there, so today this IS the
  ETFs link. Reading the kind rather than the link also finds a fund at a past as-of, when funds still sat in
  other links.
- **A card's companies** (panel 1) are its voting rows: every link except Context, ETFs and Bench. Every fund is
  left out whatever its link (before #493 a fund could sit in a voting link). Each company counts once per card
  (by `security_id`). There is no toggle for the silent links: Context and Bench names are not the card's
  companies.
- **The cards** are the live (unarchived) cards that existed by the as-of, each with its roster then
  (`cards_as_of`). A card with no roster snapshot by then reads today's roster, counted on the quiet line.
- **A fund on two cards** is one fund in panels 2–4, with its cards listed beside it, and one row per card in
  panel 1.

## The panels

Each panel is one block: a title, its count, its lines, and a quiet note saying how it was computed. An empty
panel shows a quiet sentence ("No fund has a shares sample yet."); a panel never disappears for being empty
(the Daily Digest's rule). Panel 1 spans the full width; panels 2–5 sit in a two-column grid below it, one
column on a phone.

### 1. Fund vs its companies

One row per (card, fund) pair: does the fund move with the card's companies, or apart from them?

- **Windows:** 1W · **1M (default)** · 3M = 5 / 21 / 63 daily returns, on the session calendar of
  `backend/dashboards/series.py` (`session_calendar`, IWM's bars, `BENCHMARK`), ending at the as-of.
- **The fund's line:** its closes rebased to 100 at the window start (`series.aligned` + `series.chain` with
  one member).
- **The companies' line:** equal weight, rebalanced daily, from 100: the Narrative Index's rule through the
  same `series.chain`. A company joins when it has two consecutive bars; a gap breaks only its own
  contribution; a session no company can contribute to carries the line flat and is counted.
- **The numbers on a row:** the fund's return, the companies' return, the difference in points ("fund minus
  companies"), and the companies priced out of the companies on the card.
- **IWM's return** over the same window sits once in the panel header.
- **A mini chart per row:** the two lines (the fund in `--txt`, the companies in `--txt-3`), inline SVG with
  its own y scale and a faint line at 100. Never one chart-library instance per row.
- **Sort:** fund minus companies, highest first (default) · card A→Z · fund return · companies return. A row
  without the number sorts last both ways.
- **Clicks:** the card name opens the card (`onSelectThesis(thesisId)`); the fund opens the card's Cockpit at
  the fund (`onSelectThesis(thesisId, ticker)`).
- **No lead or lag reading.** A statement like "the fund moves first" would be a signal design, which is the
  parked ETF-signal work and needs its own sign-off. This panel shows the two moves and their difference,
  nothing more.

### 2. Money in and out

One row per fund: net creations minus redemptions.

- **The numbers:** 1W and 1M net flow in dollars and as a percent of shares, straight from the `etf_flow`
  display member (`dashboards.baskets.per_company` + `display_members(["etf_flow"])`). That member takes the
  change in shares between consecutive samples, prices each change at the close on or before its sample's
  date, and sums over the trailing 7 and 30 calendar days. Shares flat means zero flow, whatever the price did.
- **A bar per fund** for the 1M dollars, drawn left for outflow and right for inflow. The bar is absent while
  the 1M window is still filling.
- **A window still filling** shows its member's note ("n/a: 9/30 sampled days") and "history starts <first
  sample date>".
- **Sort:** 1M dollars, highest first; funds still filling go last, A→Z.
- **On hover:** the latest sample's date and source, plus the member's own staleness note when it fires ("no
  sample in the last 30d").

### 3. Size and trading

One row per fund: how big it is and how much it trades.

- **Approximate size:** the latest shares sample on or before the as-of times the close on or before that
  sample's date, labeled "approx." with the sample's date. It is not the fund's filed net assets (N-PORT,
  quarterly, not stored).
- **20-day average dollar volume:** the `volume_regime` display member's `adv_usd_20d` (mean close × volume
  over the last 20 bars). The Track Record's liquidity cut uses the same number.
- **Two horizontal bars per row**, on log scales.
- **Sort:** size, largest first. A fund with no shares sample shows its dollar volume and "no shares sample
  yet", and sorts last.
- **Words:** how big and how traded, never "use", "avoid" or "best" (rule 3).

### 4. Price trend

One row per fund, with the readings the Cockpit's basket table already shows for any name.

- **Columns:** the 1M return (`trailing_returns`), the distance from the 50-day and the 200-day averages
  (`sma_position`), relative volume (`rvol`), and the position in the 52-week range (`range_52w`).
- **Same members, same notes** ("n/a: 140/200 bars"), read through `per_company` + `display_members`.
- **Sort:** fund A→Z (default) or any column.

### 5. Coverage

One block, card grain:

- "N of M cards have a fund".
- The cards with no fund, A→Z, each opening the card.
- The funds on more than one card, each with its cards.

## Thin data and left-out names

- **A flagged price history is left out (rule 5).** A fund or company whose data flags reach its price
  readings at the as-of (`dashboards.baskets.price_flags`) is left out of every number that uses its price:
  panels 1 to 4, flow dollars included, since a scale break misprices a share change too. Each panel lists the
  names it left out, with the flag's label, in the shared `FlaggedNote`. A flagged fund stays in panel 5.
- **A basket row with no resolved security** counts as a company on its card and is never priced (the Narrative
  Index's choice 7).
- **A card whose companies are all unpriced** shows "—" for the companies' return and the difference, never a
  flat 0%.
- **A fund with too few bars** for a window shows "—" with the count it had ("n/a: 12/22 bars").

## The quiet "show the work" line

One gray line under the title (rule 4) says:

- the cards counted, and how many read today's roster for want of a snapshot;
- the funds found, and the rule that finds them ("a fund = a row whose security is an ETF, in any link");
- that Context and Bench are not a card's companies;
- the as-of and the cache state.

The page is not an event-outcome view ("what happened after X"), so rule 6's market-cap slicing does not apply.

## Loudness

The page is quiet:

- Text in the neutral tokens; a sign is shown as + or −.
- Only panel 2's flow bars carry `--pos` / `--neg`, matching the Cockpit's fund-flow chip.
- No state colors.
- No badge true of every row (WB principle 3).

## Past as-of

- **One public-clock read.** Every read takes the request's one `serve_view(asof)` pair: the rosters, the
  bars, the shares samples (each public from its `d`, the source's own stated date) and the price flags.
- **Before #493.** The ETFs link did not exist before 2026-11-04. Funds are found by kind (above), so a past
  view shows the funds that were on the cards then, wherever they sat.
- **Day counts.** There are no countdowns, so the F2 action-day rule does not apply. "History starts <date>"
  is a date, not a count.

## Cache and the warm-up

- **The cache.** `dashboards.cache.cached(conn, "etfs", (clock,), asof, compute)` holds every panel's data: the
  fund and company series for the longest window (63 sessions) and the display readings. The window is sliced
  per request (`?window=1w|1m|3m`), as the Narrative Index does.
- **The warm-up.** Add `("etfs", {"window": "1m"})` to `PAGES` in `backend/pipeline/warm_dashboards.py` and
  to the `FRONTEND_DEFAULT_QUERY` mirror in its test.
- **Read size.** The read covers the funds plus the companies of every card that has a fund (measured below).
  The builder measures the cold and the warm read on dev with a prod data copy.

## Thumbnail

Four small panels in a 2×2 grid: a solid line and a faint line pulling apart, up-and-down flow bars in
`--pos` / `--neg`, a column of size bars, and a row of trend dots. That is two accent tokens.

## Build notes

- **The usual files (rule 8):**
  - `backend/dashboards/etfs.py`;
  - `backend/app/routers/dashboards/etfs.py` (one `GET ""`);
  - `backend/tests/dashboards/test_etfs.py`;
  - `frontend/src/dashboards/etfs/` with `entry.ts` (its registry metadata inline, `order: 290`) and
    `thumbnail.svg`.
- **The contract pair** is regenerated in the same PR (rule 9).
- **Reuse, never copy from another dashboard:**
  - `baskets.cards_as_of`, `per_company`, `display_members`, `price_flags` and `FlaggedNote`;
  - `series.session_calendar`, `aligned`, `chain` and `BENCHMARK`;
  - the display members named above.
- **No network.** The compute imports no EDGAR client, fund source or OpenFIGI client. A test scans the module's
  imports for them.
- **The tests include:**
  - a past as-of where a shares sample and a bar dated after the as-of stay invisible;
  - a fund in a voting link (a roster from before #493), found by its kind and kept out of the companies' line;
  - a fund on two cards (one row in panels 2–4, two in panel 1);
  - a flagged fund, listed and left out of panels 1–4;
  - shares flat with the price moving, which reads zero flow.

## Measured on prod

MEASURED 2026-11-05 after the night of 2026-11-04 finished: read-only queries in a `default_transaction_read_only`
session, plus a listing of the backend's EDGAR cache. Counts only.

| What | Count |
|---|---|
| Live cards | 62 |
| Cards with a fund | 22 (16 with one fund, 6 with two) |
| Funds | 28, every one in the ETFs link; no other row in that link |
| Funds on more than one card | 0 |
| Funds with a shares sample | 28 |
| ... whose samples start on 2026-11-04 (the load night) | 26 |
| ... with a sample from before the 1M window (flows readable now) | 2 (from 2026-06-12) |
| Funds with at least 64 bars (the 3M window) | 28 |
| Funds with at least 201 bars (the 200-day average) | 27 |
| Funds with a bar on 2026-11-04 | 28 |
| Companies on the 22 cards (voting rows, funds out) | 555 distinct; 5 to 54 per card, median 23 |
| Funds whose N-PORT filing list is in the EDGAR cache | 0 (no `browse-nport/` folder on prod) |

What follows for the page:

- **Panel 1 has 28 rows today**, one per fund. Every fund has the bars for all three windows.
- **Panel 2 shows real flows for 2 funds.** The other 26 read "history starts 2026-11-04" until about 11-11
  (1W) and 12-04 (1M), unless they are backfilled (open question 2).
- **Panel 4:** one fund's 200-day reading is "n/a" for want of bars.
- **Panel 5:** 22 of 62 cards have a fund, so 40 are listed with none.
- **Read size:** 28 funds plus 555 companies, about six chunks of the shared `per_company` read.

## Open questions for the operator

1. **Holdings overlap as a sixth panel: the fund's top holdings against its card's companies (on the card / not
   on the card / not matched).** Pulling holdings costs about two SEC requests per fund (the series' filing list
   plus the filing itself) and a few OpenFIGI lookups: about 56 SEC requests for the 28 funds, a small one-off
   spend. (The ~270 requests quoted on 2026-11-04 were for the research's ~133 candidate funds, not the funds
   on the cards.) Two ways:
   - **(a)** a stored holdings fact, filled quarterly by its own job. That is not dashboard code (a dashboard
     never writes), so it is a small separate build.
   - **(b)** the page reads only the holdings already in the EDGAR cache. That costs zero requests, but nothing
     refreshes them except a Workbench click, and today it would show nothing: no fund's N-PORT has been
     pulled on prod (measured above).

   Proposed: ship without it, then (a) if the page earns it.
2. **Flow history: let it fill, or backfill?** 26 of the 28 funds were loaded on 2026-11-04. They read a 1W
   flow from about 11-11 and a 1M flow from about 12-04. `python -m pipeline.backfill_fund_shares --thesis <id>
   --days 35` fills them now:
   - about 36 Massive.com calls per fund, spaced 13 s apart on the free tier, so about 8 minutes a fund;
   - about 3.5 hours for the 26 funds (computed from the CLI's spacing, not measured);
   - run per card.

   It is a deliberate spend, run by the operator. The page ships either way; it shows the gap honestly.

## Choices to confirm

1. **Panel 1's windows:** 1W / 1M / 3M as 5 / 21 / 63 daily returns, with 1M the default.
2. **Panel 1's default sort:** fund minus companies, highest first.
3. **The grains:** (card, fund) in panel 1, fund in panels 2–4, card in panel 5.
4. **No Context/Bench toggle.**
5. **Color:** only the flow bars carry the sign colors.
6. **Fixed panels:** no hide, collapse or reorder. A remembered layout needs storage, which rule 1 and the
   no-browser-storage rule rule out. A `?hide=` link parameter can come later if wanted.
7. **Approximate size** is shares × close, not the filed net assets.
8. **Naming:** slug `etfs`, label "ETFs", menu order 290.
9. **A fund with a missing bar has no line.** Panel 1 draws the fund only when it has a bar on every session of the
   window ("n/a: 12/22 bars" otherwise). A single gap would drop two daily steps and misstate the return.
10. **Panel 4's return column** is the Cockpit's 30-trading-day return, labeled "30d". Panel 1's "1M" is 21
    sessions, so the two names stay apart.
11. **Panel 4's range column** is the position in the 52-week range, 0 at the low to 100 at the high, worked out
    from the range member's two percentages. The hover carries the member's note when it is missing.
12. **A fund is identified by its master row today.** Whether a security is an ETF is identity, not a dated fact,
    so a past view reads the kind as it stands now; only the rosters, bars and shares samples are judged as of the
    as-of.
13. **Where the flagged note sits.** Panel 1 lists the flagged funds and the flagged companies; panels 2 to 4 list
    the flagged funds. Panel 5 lists none, because it uses no price.
14. **Panels 2 to 4 list the cards beside each fund,** each opening the card's Cockpit at the fund. Panel 1's two
    names open the card, and the fund.
15. **Funds with no flow number yet** are grouped into one line by first-sample date instead of a row each.

## Later (not in this plan)

- Holdings overlap (open question 1).
- New-fund launches per theme: N-1A / 485 filings, the unbuilt phase-2 ETF radar.
- Expense ratios: no source yet.
- An ETF-signal rule reading the ETFs link: its own design and sign-off. This page only displays.
