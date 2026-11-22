# Rotation Quadrant

`rotation-quadrant` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Each group's strength against SPY as one dot with a tail, so a group turning up shows before it leads. Groups are
links, business types, sectors or cards (`group_by`). Our own simple measures, not a published method:

- **x, RS level**: 100 times today's RS ratio (the group's line over SPY's) over its mean across the last
  `LEVEL_SESSIONS`, today included.
- **y, RS momentum**: 100 times today's x over x `MOMENTUM_SESSIONS` ago.
- **Quadrant**: Leading, Weakening, Lagging or Improving; exactly 100 is never above. The tail is `TAIL_POINTS`
  points, one every `TAIL_STEP` sessions.

## Who counts

- Cards counted at the as-of (`baskets.cards_as_of`); Context and Bench only when toggled in. A company in two
  links of a card counts in both; in the other views, once per group.
- A group's line is equal-weighted, rebalanced daily (`series.py`), on SPY's calendar.
- Under `MIN_PRICED` priced names is "too small"; short of history is "not enough history". Both are listed with
  a dash, never dropped. With no SPY tape nothing is measured.
- A flagged price history is left out of the line, counted in the group's names and named in its row (rule 5).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/rotation_quadrant.py` |
| Route | `GET /dashboards/rotation-quadrant` (`group_by`, `include_silent`) |
| Shared readers | `baskets`, `series`, `master.identity_for` |
| Cache key | `(clock, include_silent)`: one read holds all four groupings. Warmed for Link without Context and Bench |
| Frontend | `frontend/src/dashboards/rotation-quadrant/` |
| Tests | `test_rotation_quadrant.py`, frontend `__tests__/` |

## As-of behavior

A past as-of reads bars on the public clock (`serve_view`, rule 2) and the roster snapshot in force (today's when
there is none). Business type and sector are always today's master. No countdown.

## Rules not visible in code

- The quadrant is read off x and y rounded as shown (`point`). Why: the label never disagrees with the page.
- The RS ratio starts at the later of the two lines' starts. Why: before a group's first return its line only
  carries flat, so a group whose names joined 80 sessions ago has no y.
- Priced means a daily return into one of the last `PRICED_SESSIONS`. Why: a stopped tape must not hold a group
  above the floor.

## Known limits

- Equal weight is not how anyone sizes a basket; groups use the basket as of the as-of, not as it stood each day.
- Lagging by construction: y turns before x.
- A business-type re-tag shows at the next cache clear.
