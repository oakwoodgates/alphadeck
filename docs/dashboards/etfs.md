# ETFs

`etfs` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

The funds on the user's cards, in five panels: each fund against its card's companies over a window (`WINDOWS`,
default 1M); money in and out; approximate size and trading; price trend; which cards have a fund. Funds sit in the
silent ETFs link (`ETFS_LABEL`), so none moves a call. Only panel 2's flow bars carry a sign color.

## Who counts

- A fund is a roster row whose security has `instrument_kind = 'etf'`, in any link. Why: a past view finds funds
  that then sat in voting links.
- A card's companies (panel 1) are its voting rows, every fund out; Context, ETFs and Bench are not companies, with
  no toggle. A row with no resolved security counts, never priced.
- Cards: the caller's live cards that existed by the as-of, each with its roster then (`baskets.cards_as_of`);
  today's roster stands in, counted, where no snapshot exists.
- A fund on two cards is one row in panels 2 to 4, one per card in panel 1.
- A flagged price history is left out of panels 1 to 4, flow dollars included, and listed with its reason
  (`FlaggedNote`); panel 5 keeps it.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/etfs.py` (`etf_inputs`, then `etfs` per window) |
| Route | `GET /dashboards/etfs` (`window`) |
| Shared readers | `baskets`, `series`, display `MEMBERS`; `fact_price_eod`, `fact_fund_shares` |
| Cache key | `(clock,)`; one read serves every window (warmed: `window=1m`) |
| Frontend | `frontend/src/dashboards/etfs/` |
| Tests | `backend/tests/dashboards/test_etfs.py`, `etfs/__tests__/` |

## As-of behavior

One public-clock read (`serve_view`); a shares sample or bar after the as-of stays invisible
(`test_a_sample_and_a_bar_dated_after_the_as_of_stay_invisible`). A fund's kind is today's: identity, not a
dated fact.

## Rules not visible in code

- Flow is the change in shares between samples, priced at the close on or before the sample's date; flat shares is
  zero flow, whatever the price did (`etf_flow`). Why: price moves size with no creations.
- No lead or lag reading. Why: that is an ETF signal design needing its own sign-off.
- Approximate size is the latest shares sample times the close. Why: filed net assets are not stored.
- A fund missing a bar in the window has no line; a card with no priced companies shows no return, never 0%. Why:
  a gap misstates the return.
- No network request (`test_the_page_imports_no_network_client`).

## Known limits

- Flow history starts at a fund's first shares sample; `pipeline.backfill_fund_shares --thesis` fills it (operator-run).
- Not on the page: holdings overlap, expense ratios, new-fund launches ([FUND_RADAR](../FUND_RADAR.md)).
