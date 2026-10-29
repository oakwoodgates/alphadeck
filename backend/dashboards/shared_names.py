"""Shared Names (``docs/dashboards/shared-names.md``): the companies that sit on more than one live card, and
which cards share them. A "now" view: today's baskets on the live (unarchived) cards.

PURE: ``shared_names(theses, names, counting_only=...)`` takes the cards as loaded (``thesis_repo.list_all``)
and the master's company names, and returns the whole payload; the router only reads and calls it. Display
only: nothing here feeds a call, scores an idea or writes anything (``docs/DASHBOARDS.md`` rule 1).

The rules, in one place:
- A company is matched by ``security_id``. A basket row with no resolved security is COUNTED
  (``unresolved_rows``), never silently dropped (rule 5 / invariant #9).
- A company with several rows on one card (several links) counts ONCE for that card; its links are listed
  together on that card's chip.
- A Context / Bench membership (``is_silent``) is included by default and tagged on its chip. With
  ``counting_only`` those rows are dropped BEFORE both sections are computed (and counted as left out).
- "Opposite sides": the company COUNTS on one card (any non-silent row there) and carries the context role
  ``counter_case`` on a DIFFERENT card.
- Sorts are neutral and computed: companies by most cards, then ticker; pairs by most shared, then card names.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from domain.enums import ContextRole
from domain.thesis import BasketMember, Thesis, is_silent


@dataclass(frozen=True)
class CardRef:
    thesis_id: UUID
    thesis_name: str


@dataclass(frozen=True)
class CardChip:
    """One card a company sits on: its links there (basket order, deduped; ``None`` = no link), the silent
    link when its membership is Context / Bench, its context role on that card, and whether it COUNTS there
    (any non-silent row). ``ticker`` is the company's ticker on that card (the Cockpit's ``?name=`` key).
    """

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: tuple[str | None, ...]
    silent_link: str | None
    context_role: ContextRole | None
    counts: bool


@dataclass(frozen=True)
class SharedCompany:
    security_id: UUID
    ticker: str
    name: str | None
    card_count: int
    opposite_sides: bool
    cards: tuple[CardChip, ...]  # by card name


@dataclass(frozen=True)
class CardPair:
    card_a: CardRef
    card_b: CardRef  # card_a sorts first by name
    shared_count: int
    tickers: tuple[str, ...]  # sorted
    security_ids: tuple[UUID, ...]  # in ticker order


@dataclass(frozen=True)
class SharedNames:
    counting_only: bool
    live_cards: int
    basket_rows: int  # every row on the live cards, before any filter
    silent_rows_left_out: int  # Context / Bench rows dropped by ``counting_only`` (0 when off)
    unresolved_rows: int  # rows with no resolved security: counted here, not matched
    companies: int  # distinct resolved companies on the live cards (after the filter)
    names: tuple[SharedCompany, ...]  # the companies on 2+ cards
    pairs: tuple[CardPair, ...]


def _card_key(card: CardRef) -> tuple[str, str]:
    return (card.thesis_name.casefold(), str(card.thesis_id))


def _chip(thesis: Thesis, rows: list[BasketMember]) -> CardChip:
    links: list[str | None] = []
    for m in rows:
        if not is_silent(m) and m.segment not in links:
            links.append(m.segment)
    silent = next((m.segment for m in rows if is_silent(m)), None)
    # one role per company per card (the promote enforces it); the first set one is that role
    role = next((m.context_role for m in rows if m.context_role is not None), None)
    return CardChip(
        thesis_id=thesis.id,
        thesis_name=thesis.name,
        ticker=rows[0].ticker,
        links=tuple(links),
        silent_link=silent,
        context_role=role,
        counts=any(not is_silent(m) for m in rows),
    )


def _opposite_sides(chips: list[CardChip]) -> bool:
    counting = {c.thesis_id for c in chips if c.counts}
    counter = {c.thesis_id for c in chips if c.context_role == ContextRole.COUNTER_CASE}
    return any(a != b for a in counting for b in counter)


def _display_ticker(chips: Iterable[CardChip]) -> str:
    """The ticker most cards carry for this security (alphabetical on a tie); normally they all agree."""
    counts = Counter(c.ticker for c in chips)
    return min(counts, key=lambda t: (-counts[t], t))


def shared_names(
    theses: Iterable[Thesis],
    names: Mapping[UUID, str | None],
    *,
    counting_only: bool = False,
) -> SharedNames:
    """The Shared Names payload over ``theses`` (the live cards; the caller excludes archived ones)."""
    theses = list(theses)
    basket_rows = 0
    silent_left_out = 0
    unresolved = 0
    # security_id -> thesis_id -> that company's rows on that card, in basket order
    by_company: dict[UUID, dict[UUID, list[BasketMember]]] = {}
    by_id = {t.id: t for t in theses}
    for t in theses:
        for m in t.basket:
            basket_rows += 1
            if counting_only and is_silent(m):
                silent_left_out += 1
                continue
            if m.security_id is None:
                unresolved += 1
                continue
            by_company.setdefault(m.security_id, {}).setdefault(t.id, []).append(m)

    companies: list[SharedCompany] = []
    for sid, per_card in by_company.items():
        if len(per_card) < 2:
            continue
        chips = sorted(
            (_chip(by_id[tid], rows) for tid, rows in per_card.items()),
            key=lambda c: _card_key(CardRef(c.thesis_id, c.thesis_name)),
        )
        companies.append(
            SharedCompany(
                security_id=sid,
                ticker=_display_ticker(chips),
                name=names.get(sid),
                card_count=len(chips),
                opposite_sides=_opposite_sides(chips),
                cards=tuple(chips),
            )
        )
    companies.sort(key=lambda c: (-c.card_count, c.ticker.casefold(), str(c.security_id)))

    # every pair of cards sharing at least one company
    shared: dict[tuple[UUID, UUID], tuple[CardRef, CardRef, list[SharedCompany]]] = {}
    for co in companies:
        refs = sorted((CardRef(c.thesis_id, c.thesis_name) for c in co.cards), key=_card_key)
        for a, b in combinations(refs, 2):
            shared.setdefault((a.thesis_id, b.thesis_id), (a, b, []))[2].append(co)
    pairs: list[CardPair] = []
    for a, b, cos in shared.values():
        cos.sort(key=lambda co: (co.ticker.casefold(), str(co.security_id)))
        pairs.append(
            CardPair(
                card_a=a,
                card_b=b,
                shared_count=len(cos),
                tickers=tuple(co.ticker for co in cos),
                security_ids=tuple(co.security_id for co in cos),
            )
        )
    pairs.sort(key=lambda p: (-p.shared_count, _card_key(p.card_a), _card_key(p.card_b)))

    return SharedNames(
        counting_only=counting_only,
        live_cards=len(theses),
        basket_rows=basket_rows,
        silent_rows_left_out=silent_left_out,
        unresolved_rows=unresolved,
        companies=len(by_company),
        names=tuple(companies),
        pairs=tuple(pairs),
    )
