"""Overlap Map (``docs/dashboards/overlap-map.md``): the cards as bubbles joined by the companies they share, the
data the Venn and the sector grid are drawn from, and the layout. A structure-only view (no prices, no calls) at
the as-of dial.

PURE: ``overlap_map(cards, identity, ...)`` takes the counted cards as ``dashboards.baskets.cards_as_of`` loaded
them (each with its roster then) and the master's identity (``securities.master.identity_for``), and returns the
whole payload; the router only reads, caches and calls it. Display only (``docs/DASHBOARDS.md`` rule 1): nothing
here feeds a call, scores a card or writes anything.

The rules, in one place:
- **Who counts.** ``baskets.counted_rows(memberships_of(cards), include_silent)`` and nothing else decides it: a
  Context / ETFs / Bench row (``is_silent``) is left out unless ``include_silent``, and a row with no resolved
  security is counted on the quiet line, never matched. No label is named here.
- **Matching is by ``security_id``.** A company with several rows on one card (several links) counts once for
  that card; its links ride together on that card's entry.
- **Pairs.** Every pair of cards sharing at least one company, with the shared count. The page's threshold only
  hides lines on the client; nothing here depends on it, and ``groups`` holds the answer for each threshold 1-5.
- **Groups.** At threshold N, cards are joined when they share at least N companies; a group is a connected set
  of 2+ cards, and a card in no group has "no line".
- **The layout** is a deterministic force layout (``layout_bubbles``): each card starts from a hash of its id
  (``hashlib``, never Python's per-process ``hash()``), a fixed number of iterations, a pull toward every card it
  shares companies with proportional to the shared count (over ALL pairs), a push between every pair of bubbles,
  a weak pull to the middle, and a final pass that leaves no two bubbles touching. Positions are then scaled
  uniformly (positions and radii together, so nothing overlaps) into a fixed box. A card sharing no company
  with any other has no position: it sits in the page's "Shares no names" strip, never dropped.
- **Sector and business type** are today's master (``identity_for``'s ``business_supersector`` and
  ``business_type``, the read ``baskets.group_of`` uses); a company with neither carries ``None`` (the page's
  "Unclassified"), never dropped.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from dashboards.baskets import CountedRows, Membership, counted_rows, memberships_of
from domain.thesis import Thesis

# --- the layout's constants (fixed: a layout is the same on every visit) --------------------------------------

BOX_W = 1000.0
BOX_H = 640.0
MARGIN = 24.0
R_SCALE = (
    6.0  # bubble radius = R_SCALE * sqrt(companies): area proportional to the card's companies
)
R_MIN = 7.0  # a 1-name card stays visible
GAP = 3.0  # clear space left between two bubbles
ITERATIONS = 240  # fixed
START_RADIUS = 260.0  # the start disc a card's hash places it in
PULL = 1.0  # spring stiffness per shared company
REPEL = 900.0  # the push between every pair of bubbles (over distance)
CENTER_PULL = 0.012  # weak pull to the middle so groups sharing nothing stay in view
MAX_STEP_START = 28.0
MAX_STEP_END = 0.6
RELAX_PASSES = 4000  # the no-overlap pass stops at the first clean sweep
MAX_SCALE_UP = 2.0
THRESHOLDS = (1, 2, 3, 4, 5)


# --- the payload ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlapCard:
    """One counted card: its companies in view, the bubble (``x`` / ``y`` / ``r`` in the page's box; ``x`` and
    ``y`` are ``None`` for a card in the "Shares no names" strip, ``r`` is still its size)."""

    thesis_id: UUID
    name: str
    companies: int
    x: float | None
    y: float | None
    r: float
    shares_nothing: bool


@dataclass(frozen=True)
class OverlapPair:
    """Two cards (indexes into ``cards``, ``a`` < ``b``) sharing ``shared`` companies in view (at least 1)."""

    a: int
    b: int
    shared: int


@dataclass(frozen=True)
class OverlapGroups:
    """The groups at one threshold: the size of each group of 2+ cards (largest first) and how many cards have
    no line (join no group)."""

    threshold: int
    sizes: tuple[int, ...]
    no_line: int


@dataclass(frozen=True)
class CompanyOnCard:
    """One company's place on one card: ``card`` indexes ``cards``; ``ticker`` = its ticker there (the
    Cockpit's ``?name=`` key); ``links`` = its links there (basket order, deduped; ``None`` = no link);
    ``silent`` = every row of it there is Context / ETFs / Bench."""

    card: int
    ticker: str
    links: tuple[str | None, ...]
    silent: bool
    context_role: str | None


@dataclass(frozen=True)
class OverlapCompany:
    security_id: UUID
    ticker: str
    name: str | None
    supersector: str | None
    business_type: str | None
    rows: tuple[CompanyOnCard, ...]  # by card index


@dataclass(frozen=True)
class OverlapMapResult:
    include_silent: bool
    box_w: float
    box_h: float
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # rows in view with no resolved security: counted, never matched
    companies_in_view: int  # distinct companies in view
    companies_shared: int  # of those, on 2+ cards
    cards: tuple[OverlapCard, ...]  # A->Z by name
    pairs: tuple[OverlapPair, ...]
    groups: tuple[OverlapGroups, ...]
    companies: tuple[OverlapCompany, ...]  # by ticker


# --- the layout ---------------------------------------------------------------------------------------------------


def radius(companies: int) -> float:
    """A bubble's radius: area proportional to the companies, with a floor."""
    return max(R_MIN, R_SCALE * math.sqrt(max(companies, 0)))


def _unit_pair(key: str) -> tuple[float, float]:
    """Two stable numbers in [0, 1) from a key (sha-256; Python's ``hash()`` varies per process)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return (
        int.from_bytes(digest[0:4], "big") / 2**32,
        int.from_bytes(digest[4:8], "big") / 2**32,
    )


def layout_bubbles(
    keys: Sequence[str],
    sizes: Sequence[int],
    weights: Mapping[tuple[int, int], int],
    *,
    box_w: float = BOX_W,
    box_h: float = BOX_H,
) -> list[tuple[float, float, float]]:
    """``(x, y, r)`` per bubble, in the order of ``keys``, inside the ``box_w`` x ``box_h`` box.

    ``keys`` are stable ids (a card's id as text: they seed each start); ``sizes`` the companies per bubble;
    ``weights`` maps an index pair ``(i, j)``, ``i < j``, to its shared count (ALL pairs sharing at least one).
    Pure and deterministic: the same input gives the same output, in any process. No threshold is an input.
    The result leaves no two bubbles overlapping (centers at least ``r_i + r_j`` apart).
    """
    n = len(keys)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: keys[i])  # the sim walks the cards in a fixed (id) order
    radii = [radius(s) for s in sizes]
    xs = [0.0] * n
    ys = [0.0] * n
    for i in range(n):
        u1, u2 = _unit_pair(keys[i])
        angle = 2 * math.pi * u1
        dist = START_RADIUS * math.sqrt(u2)
        xs[i], ys[i] = dist * math.cos(angle), dist * math.sin(angle)

    springs: list[tuple[int, int, float, float]] = []  # (i, j, stiffness, rest length)
    stiff = [0.0] * n
    for (i, j), w in sorted(weights.items()):
        k = PULL * w
        springs.append((i, j, k, radii[i] + radii[j] + GAP))
        stiff[i] += k
        stiff[j] += k
    damp = [1.0 / (1.0 + stiff[i] + CENTER_PULL * 100) for i in range(n)]

    for step in range(ITERATIONS):
        cool = MAX_STEP_END + (MAX_STEP_START - MAX_STEP_END) * (1.0 - step / ITERATIONS)
        fx = [-CENTER_PULL * xs[i] for i in range(n)]
        fy = [-CENTER_PULL * ys[i] for i in range(n)]
        for p in range(n):
            i = order[p]
            xi, yi = xs[i], ys[i]
            for q in range(p + 1, n):
                j = order[q]
                dx = xi - xs[j]
                dy = yi - ys[j]
                d2 = dx * dx + dy * dy
                if d2 < 1e-9:  # coincident: a stable direction from the pair's keys
                    u1, _ = _unit_pair(keys[i] + keys[j])
                    dx, dy, d2 = math.cos(2 * math.pi * u1), math.sin(2 * math.pi * u1), 1.0
                d = math.sqrt(d2)
                push = REPEL / (d * d)  # force REPEL / d, split into its x / y parts over d
                fx[i] += dx * push
                fy[i] += dy * push
                fx[j] -= dx * push
                fy[j] -= dy * push
        for i, j, k, rest in springs:
            dx = xs[j] - xs[i]
            dy = ys[j] - ys[i]
            d = math.sqrt(dx * dx + dy * dy)
            if d < 1e-9:
                continue
            pull = k * (d - rest) / d  # toward each other past the rest length, apart inside it
            fx[i] += dx * pull
            fy[i] += dy * pull
            fx[j] -= dx * pull
            fy[j] -= dy * pull
        for i in range(n):
            mx, my = fx[i] * damp[i], fy[i] * damp[i]
            m = math.sqrt(mx * mx + my * my)
            if m > cool:
                mx, my = mx * cool / m, my * cool / m
            xs[i] += mx
            ys[i] += my

    _separate(keys, order, radii, xs, ys)

    # normalize: one uniform scale (positions and radii together, so a clean layout stays clean) into the box
    left = min(xs[i] - radii[i] for i in range(n))
    right = max(xs[i] + radii[i] for i in range(n))
    top = min(ys[i] - radii[i] for i in range(n))
    bottom = max(ys[i] + radii[i] for i in range(n))
    scale = min(
        (box_w - 2 * MARGIN) / max(right - left, 1e-9),
        (box_h - 2 * MARGIN) / max(bottom - top, 1e-9),
        MAX_SCALE_UP,
    )
    ox = box_w / 2 - scale * (left + right) / 2
    oy = box_h / 2 - scale * (top + bottom) / 2
    return [
        (round(ox + scale * xs[i], 2), round(oy + scale * ys[i], 2), round(scale * radii[i], 2))
        for i in range(n)
    ]


def _separate(
    keys: Sequence[str],
    order: Sequence[int],
    radii: Sequence[float],
    xs: list[float],
    ys: list[float],
) -> None:
    """Push overlapping bubbles apart (half each) until a whole sweep finds none, so no two bubbles touch."""
    n = len(order)
    for _ in range(RELAX_PASSES):
        moved = False
        for p in range(n):
            i = order[p]
            for q in range(p + 1, n):
                j = order[q]
                need = radii[i] + radii[j] + GAP
                dx = xs[j] - xs[i]
                dy = ys[j] - ys[i]
                d2 = dx * dx + dy * dy
                if d2 >= need * need:
                    continue
                if d2 < 1e-9:
                    u1, _ = _unit_pair(keys[i] + keys[j])
                    dx, dy, d2 = math.cos(2 * math.pi * u1), math.sin(2 * math.pi * u1), 1.0
                d = math.sqrt(d2)
                shift = (need - d) / 2 + 0.01
                ux, uy = dx / d, dy / d
                xs[i] -= ux * shift
                ys[i] -= uy * shift
                xs[j] += ux * shift
                ys[j] += uy * shift
                moved = True
        if not moved:
            return


# --- the groups ---------------------------------------------------------------------------------------------------


def groups_at(card_count: int, pairs: Sequence[OverlapPair], threshold: int) -> OverlapGroups:
    """Cards joined when they share at least ``threshold`` companies: the sizes of the groups of 2+ cards
    (largest first) and the cards with no line."""
    parent = list(range(card_count))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for p in pairs:
        if p.shared >= threshold:
            ra, rb = find(p.a), find(p.b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    size = Counter(find(i) for i in range(card_count))
    sizes = sorted((s for s in size.values() if s >= 2), reverse=True)
    return OverlapGroups(threshold, tuple(sizes), sum(1 for s in size.values() if s == 1))


# --- the compute ---------------------------------------------------------------------------------------------------


def _text(identity: Mapping[UUID, Mapping[str, object]], sid: UUID, field_name: str) -> str | None:
    v = (identity.get(sid) or {}).get(field_name)
    return str(v) if v else None


def _display_ticker(rows: Sequence[CompanyOnCard]) -> str:
    """The ticker most of its cards use (alphabetical on a tie); normally they all agree."""
    counts = Counter(r.ticker for r in rows)
    return min(counts, key=lambda t: (-counts[t], t))


def overlap_map(
    cards: Sequence[Thesis],
    identity: Mapping[UUID, Mapping[str, object]],
    *,
    include_silent: bool,
    live_cards: int | None = None,
    not_yet_created: int = 0,
    roster_fallback_cards: int = 0,
) -> OverlapMapResult:
    """The whole payload over ``cards`` (the counted cards, each with its roster at the as-of)."""
    ordered = sorted(cards, key=lambda t: (t.name.casefold(), str(t.id)))
    index = {t.id: i for i, t in enumerate(ordered)}
    memberships: tuple[Membership, ...] = memberships_of(ordered)
    counted: CountedRows = counted_rows(memberships, include_silent)

    # security -> card index -> that company's rows on that card, in basket order
    by_company: dict[UUID, dict[int, list[Membership]]] = {}
    for m in counted.rows:
        assert m.security_id is not None
        by_company.setdefault(m.security_id, {}).setdefault(index[m.thesis_id], []).append(m)

    per_card = Counter(ci for per in by_company.values() for ci in per)
    pair_counts: Counter[tuple[int, int]] = Counter()
    for per in by_company.values():
        for a, b in combinations(sorted(per), 2):
            pair_counts[(a, b)] += 1
    pairs = tuple(OverlapPair(a, b, w) for (a, b), w in sorted(pair_counts.items()))

    connected = sorted({i for p in pairs for i in (p.a, p.b)})
    placed: dict[int, tuple[float, float, float]] = {}
    if connected:
        pos = {c: k for k, c in enumerate(connected)}
        got = layout_bubbles(
            [str(ordered[i].id) for i in connected],
            [per_card[i] for i in connected],
            {(pos[a], pos[b]): w for (a, b), w in sorted(pair_counts.items())},
        )
        # the uniform scale is the layout's: a strip card draws at that same scale
        placed = {c: g for c, g in zip(connected, got)}
        scale = got[0][2] / radius(per_card[connected[0]])
    else:
        scale = 1.0

    out_cards: list[OverlapCard] = []
    for i, t in enumerate(ordered):
        if i in placed:
            x, y, r = placed[i]
            out_cards.append(OverlapCard(t.id, t.name, per_card[i], x, y, r, False))
        else:
            out_cards.append(
                OverlapCard(
                    t.id,
                    t.name,
                    per_card[i],
                    None,
                    None,
                    round(scale * radius(per_card[i]), 2),
                    True,
                )
            )

    companies: list[OverlapCompany] = []
    for sid, per in by_company.items():
        rows: list[CompanyOnCard] = []
        for ci in sorted(per):
            ms = per[ci]
            links: list[str | None] = []
            for m in ms:
                if m.segment not in links:
                    links.append(m.segment)
            rows.append(
                CompanyOnCard(
                    card=ci,
                    ticker=ms[0].ticker,
                    links=tuple(links),
                    silent=all(m.silent for m in ms),
                    context_role=next((m.context_role for m in ms if m.context_role), None),
                )
            )
        companies.append(
            OverlapCompany(
                security_id=sid,
                ticker=_display_ticker(rows),
                name=_text(identity, sid, "name"),
                supersector=_text(identity, sid, "business_supersector"),
                business_type=_text(identity, sid, "business_type"),
                rows=tuple(rows),
            )
        )
    companies.sort(key=lambda c: (c.ticker.casefold(), str(c.security_id)))

    return OverlapMapResult(
        include_silent=include_silent,
        box_w=BOX_W,
        box_h=BOX_H,
        live_cards=live_cards if live_cards is not None else len(ordered),
        cards_counted=len(ordered),
        cards_not_yet_created=not_yet_created,
        roster_fallback_cards=roster_fallback_cards,
        basket_rows=counted.basket_rows,
        silent_rows_left_out=counted.silent_rows_left_out,
        unresolved_rows=counted.unresolved_rows,
        companies_in_view=len(by_company),
        companies_shared=sum(1 for per in by_company.values() if len(per) >= 2),
        cards=tuple(out_cards),
        pairs=pairs,
        groups=tuple(groups_at(len(ordered), pairs, n) for n in THRESHOLDS),
        companies=tuple(companies),
    )
