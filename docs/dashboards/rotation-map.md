# Rotation Map

`rotation-map` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Which value-chain links, business types, sectors or cards have price moving, and where golden and death crosses
are printing. One row per group (link, business type, sector or card); a row expands to its names.
Per group, from four display members: the median return per trailing window with "x of n up", names above their
50-day average, median 20-day RVOL, crosses in the last `CROSS_DAYS` calendar days and RS leaders (headline
`leading`). The vs-SPY toggle subtracts SPY's return per window, one number per column, so it never changes an order
or a color.

## Who counts

- The cards `baskets.cards_as_of` counts. Context and Bench only with `include_silent`: their own rows in the Link
  view, their companies in the others.
- Link view: a company in two links of a card counts in both. Other views: once per group (`security_id`).
- Fewer than `MIN_PRICED` priced names for a window gives no median ("—"). A row with no resolved security is
  counted on the quiet line.
- A flagged price history leaves a company out of every aggregate but not the group's names; the expanded group
  lists it with the reason (rule 5).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/rotation_map.py` (rules in its docstring) |
| Route | `GET /dashboards/rotation-map` (`group_by`, `include_silent`) |
| Shared readers | `baskets` (`cards_as_of`, `per_company`, `price_flags`) |
| Cache key | `(clock,)`; every company's reading, so one read serves every grouping and toggle; the warm-up reads `group_by=link`, `include_silent=false` |
| Frontend | `frontend/src/dashboards/rotation-map/` |
| Tests | `backend/tests/dashboards/test_rotation_map.py`, frontend tests |

## As-of behavior

A past as-of reads the public clock (rule 2). A card with no roster snapshot by then reads today's roster, and the
page says how many. Business type and sector are today's.

## Rules not visible in code

- A return median is colored only in the top or bottom `TONE_SHARE` of the groups shown in that column (ties at
  the cut included), so a view of fewer than ten groups colors nothing. Why: loudness marks the exception only
  (invariant 7).
- `CROSS_DAYS` and `MIN_PRICED` copy the Cockpit's `TAPE_CROSS_DAYS` and `AGG_MIN_PRICED`, so the page reads as the
  Cockpit does. The member emits each name's most recent cross only.
- Default order: 7d median, highest first, no value last. A group under `MIN_PRICED` names sorts as no value on the
  above-50d share too, though it still shows "x of n".

## Known limits

- Coloring is cross-sectional, against the other groups in view, never a group's own history.
