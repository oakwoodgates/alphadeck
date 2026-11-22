# Leaders

`leaders` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Which names across every counted card sit near their 52-week high, lead the market, coil, draw insider buying or
trade loud; the names on several lists; and the groups where insiders cluster. One row per company, a chip per card.
Each list reads one display member ([DISPLAY_SIGNALS](../DISPLAY_SIGNALS.md)); nothing ranks ideas (invariant 4).

| List | Member | On the list when |
|---|---|---|
| Near 52-week high | `range_52w` | `pct_off_52w_high` at or above `NEAR_HIGH_PCT` |
| RS leader | `relative_strength` | headline key `leading` |
| Coiling | `vcp` | headline key `coiling` |
| Insider buying | `insider_flow_90d` | `buy_count_30d` at least `INSIDER_MIN_BUYS`; a cluster at `CLUSTER_MIN_BUYERS` buyers |
| Volume | `rvol` | `rvol20` at or above the member's `LOUD_MULT_20` |

## Who counts

- The cards `baskets.cards_as_of` counts; Context and Bench only with `include_silent`.
- One row per `security_id`. A basket row with no resolved security, and a company a member gave no reading, are
  counted on the quiet line.
- A flagged price history puts a company on no list and out of every insider group; `FlaggedNote` names it (rule 5).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/leaders.py` (rules in its docstring) |
| Route | `GET /dashboards/leaders` (`include_silent`, `group_by`) |
| Shared readers | `baskets` (`cards_as_of`, `per_company`, `price_flags`), `filings.CLUSTER_MIN_BUYERS` |
| Cache key | `(clock,)`; the warm-up reads `group_by=link`, `include_silent=false` |
| Frontend | `frontend/src/dashboards/leaders/` |
| Tests | `backend/tests/dashboards/test_leaders.py`, frontend tests |

## As-of behavior

A past as-of reads the public clock (rule 2). A card with no roster snapshot by then reads today's roster, and the
page says how many. Company names, business type and sector are today's.

## Rules not visible in code

- RS leader reads the headline `leading`, which fires on SPY or IWM. Why: the Cockpit's Tape chip and the Rotation
  Map read the same key.
- Fewer than `relative_strength.MIN_BARS` aligned bars on both benchmarks is no reading, never "not leading".
- A range over fewer bars than `FULL_YEAR_BARS` still counts, and the row says how many bars, as the Cockpit's Off
  52w high does.
- An insider group's share is out of every unflagged company in it, those with no Form 4 data included: "2 of 5"
  means five names.
- The one accent is the cluster mark on a name, never on a group row. Volume has none: every row there clears the
  threshold, so an accent would mark the rule, not the exception (invariant 7).

## Known limits

- The insider read is the Cockpit's open-market screen; its caveats apply.
- A list shows a state, not what followed; a view of what followed needs rule 6's cap tiers.
