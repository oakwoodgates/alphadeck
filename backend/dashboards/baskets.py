"""The cross-card basket read the dashboards share (``docs/DASHBOARDS.md``): which cards count at an as-of and
their rosters then, the membership rows, the four groupings and who counts in each, and the chunked
point-in-time read every company goes through. Built with the Rotation Map, shared since the Cross Radar, so
both read the same cards, rosters and groups the same way (never a second copy of the rule).

The rules, in one place:
- **Which cards.** The live (unarchived) cards that existed by the as-of (``cards_that_existed``); at a past
  as-of each card's roster then (``thesis_repo.get_asof``), today's roster standing in for a card with no
  roster snapshot by then (counted, never hidden).
- **Who counts.** Context and Bench memberships (``is_silent``) are left out unless ``include_silent``; a
  basket row with no resolved security is counted, never grouped (rule 5 / invariant #9).
- **The groups.** Link (one per link of a card; a company in two links of a card counts in both), Business
  type, Sector and Card. Inside a group each company counts ONCE (by ``security_id``), its rows' cards and
  links folded together. A row with no link groups as "Unsegmented" (``None``); a company with no business
  type / sector as "Unclassified" (``None``).
- **A flagged price history.** ``price_flags``: the companies whose data flags reach their PRICE readings at
  the as-of, each with the flags' labels: the same reader the ``/data-flags`` routes serve
  (``pipeline.data_flags.read_flags``), and the domain's own reach rule: a flag whose ``columns`` (the
  Cockpit cells it reaches, ``domain.data_flags.cells_for``) include a price cell (``PRICE_CELLS``, the
  price group's cells). A price dashboard leaves such a company out rather than chain a bad bar, and names
  it (``flagged_in_view``: each company in view once, by ticker, with its reason): left out, never vanished.

Display only (rule 1): this module READS the cards, their roster snapshots and the point-in-time facts; it
writes nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Literal, NamedTuple, TypeVar
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.data_flags import PRICE_CELLS, DataFlag
from domain.market_time import market_tz
from domain.settings import get_settings
from domain.thesis import Thesis, is_silent
from pipeline import data_flags as flags_reader
from pipeline.backfill import thesis_existed_on
from repositories import thesis_repo
from securities import master
from signals.base import PointInTimeData
from signals.display import registered_display_members
from signals.display.base import DisplayMember
from signals.horizons import display_bounds

GroupBy = Literal["link", "business_type", "sector", "card"]
GROUP_BYS: tuple[GroupBy, ...] = ("link", "business_type", "sector", "card")

# Companies per point-in-time view. One view over all ~1,500 companies would hold every company's whole
# bounded price window (~630 calendar days) in memory at once; a view per chunk reads the same rows (the
# prefetch scope only batches the reads, it never changes what a reader sees) and frees them as it goes.
CHUNK = 100

T = TypeVar("T")


# --- the cards and their rosters ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Membership:
    """One basket row of a counted card, as of the as-of (its roster then)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str
    security_id: UUID | None
    segment: str | None
    silent: bool
    # the company's context role on this card (``BasketMember.context_role``: payer, counter_case, ...); None =
    # unset. Display metadata, read by the Chain Wave's role rows only (never a call input, #4)
    context_role: str | None = None


def cards_that_existed(
    theses: Iterable[Thesis], created_at: Mapping[UUID, datetime], asof: date, tz: tzinfo
) -> tuple[list[Thesis], int]:
    """The cards that existed by ``asof`` (``pipeline.backfill.thesis_existed_on``: created on or before the
    as-of day, market time) and how many were created after it. A missing ``created_at`` (impossible for a
    loaded row) keeps the card: the backfill's own recall-first rule."""
    kept: list[Thesis] = []
    later = 0
    for t in theses:
        born = created_at.get(t.id)
        if born is not None and not thesis_existed_on(born, asof, tz):
            later += 1
        else:
            kept.append(t)
    return kept, later


def memberships_of(theses: Iterable[Thesis]) -> tuple[Membership, ...]:
    """Every basket row of the counted cards, silent ones included (each view filters its own)."""
    return tuple(
        Membership(
            thesis_id=t.id,
            thesis_name=t.name,
            ticker=m.ticker,
            security_id=m.security_id,
            segment=m.segment,
            silent=is_silent(m),
            context_role=m.context_role.value if m.context_role is not None else None,
        )
        for t in theses
        for m in t.basket
    )


class CardsAsOf(NamedTuple):
    """The counted cards at an as-of, with the counts a page's quiet line shows."""

    live_count: int  # unarchived cards today
    cards: list[Thesis]  # those that existed by the as-of, each with its roster then
    not_yet_created: int  # created after the as-of: left out
    roster_fallback: int  # counted cards with no roster snapshot by then: today's roster stands in


def cards_as_of(conn: psycopg.Connection, asof: date, known_at: datetime | None) -> CardsAsOf:
    """The live cards that existed by ``asof``, each with its roster as of ``known_at`` (``serve_view``'s:
    ``None`` = the live view, where today's roster IS the roster as of today)."""
    live = thesis_repo.list_all(conn)
    created = thesis_repo.created_at_for(conn, [t.id for t in live])
    kept, later = cards_that_existed(live, created, asof, market_tz())
    if known_at is None:
        # the live view: today's roster IS the roster as of today (get_asof(now) is byte-identical to it)
        cards, fallback = kept, 0
    else:
        cards, fallback = [], 0
        for t in kept:
            if not thesis_repo.snapshot_exists_asof(conn, t.id, known_at):
                fallback += 1  # no roster snapshot by then: get_asof keeps today's roster
            got = thesis_repo.get_asof(conn, t.id, known_at)
            if got is not None:
                cards.append(got)
    return CardsAsOf(len(live), cards, later, fallback)


def resolved_ids(memberships: Iterable[Membership]) -> list[UUID]:
    """Every resolved company on the rows, once, in a stable order (silent ones included)."""
    return sorted({m.security_id for m in memberships if m.security_id is not None}, key=str)


# --- the chunked point-in-time read --------------------------------------------------------------------------


def display_members(names: Sequence[str], dashboard: str) -> dict[str, DisplayMember]:
    """The named display members, in ``names`` order. A renamed member fails loud, never reads as "no tape"."""
    by_name = {m.name: m for m in registered_display_members()}
    missing = [n for n in names if n not in by_name]
    if missing:  # a renamed member must fail loud, never read as "no tape"
        raise RuntimeError(f"{dashboard}: display members not registered: {missing}")
    return {n: by_name[n] for n in names}


def per_company(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    read: Callable[[PointInTimeData, UUID], T],
) -> dict[UUID, T]:
    """``read(pit, sid)`` ONCE per company, through the same point-in-time view the Cockpit's
    ``/display-signals`` builds (``bounds=display_bounds()``; ``known_at`` + ``clock`` from ``serve_view``:
    the public clock at a past as-of, the live read today), ``CHUNK`` companies per view."""
    bounds = display_bounds()
    out: dict[UUID, T] = {}
    for i in range(0, len(sids), CHUNK):
        chunk = list(sids[i : i + CHUNK])
        pit = PointInTimeData(
            conn,
            asof=asof,
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
            basket=chunk,
            bounds=bounds,
            clock=clock,
        )
        for sid in chunk:
            out[sid] = read(pit, sid)
    return out


# --- the data flags that reach a price reading ----------------------------------------------------------------


def reaches_price(flag: DataFlag) -> bool:
    """Does the flag reach a PRICE reading? The domain's own rule: its ``columns`` (the Cockpit cells whose
    window reads a marked date, ``domain.data_flags.cells_for``; every cell of a stopped feed) include a price
    cell (``PRICE_CELLS``, the price group's cells, the 1Y return among them). A flag whose dates no price
    cell reads (history only, a split within the vendor's noise, a market cap, fundamentals) reaches none.
    """
    return any(cell in PRICE_CELLS for cell in flag.columns)


def price_reason(flags: Iterable[DataFlag]) -> str | None:
    """The labels of the flags that reach a price reading, in the reader's order (``sort_flags``: a known
    wrong number first), each once; ``None`` when none does."""
    labels = list(dict.fromkeys(f.label for f in flags if reaches_price(f)))
    return "; ".join(labels) if labels else None


def price_flags(
    conn: psycopg.Connection,
    security_ids: Sequence[UUID],
    asof: date,
    *,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[UUID, str]:
    """``security_id -> reason`` for each company whose data flags reach its price readings at ``asof``
    (``reaches_price``; the reason is the flags' labels, e.g. "mixed-scale price history").

    The SAME read the ``/data-flags`` routes serve (``app/routers/data_flags._read``): ``read_flags`` with the
    nightly's readings and corrections (``run_reads``), the master's tickers and ``tape_stale_days``, on the
    view's clock. ``known_at`` + ``clock`` are the request's ``serve_view(asof)`` pair (the route takes the
    same pair; a dashboard calls ``serve_view`` once per request, ``docs/DASHBOARDS.md`` rule 2). Read
    ``CHUNK`` companies per call: each company's flags are its own reads, so a chunk changes nothing but the
    memory one view holds."""
    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return {}
    readings, corrections = flags_reader.run_reads()
    tickers = master.tickers_for(conn, ids, tenant_id=tenant_id)
    stale_days = get_settings().tape_stale_days
    out: dict[UUID, str] = {}
    for i in range(0, len(ids), CHUNK):
        got = flags_reader.read_flags(
            conn,
            tenant_id=tenant_id,
            security_ids=ids[i : i + CHUNK],
            tickers=tickers,
            asof=asof,
            known_at=known_at,
            clock=clock,
            stale_days=stale_days,
            readings=readings,
            corrections=corrections,
        )
        for sid, member in got.items():
            reason = price_reason(member.flags)
            if reason is not None:
                out[sid] = reason
    return out


# --- who counts, and the groups ------------------------------------------------------------------------------


class CountedRows(NamedTuple):
    rows: list[Membership]  # the rows in view: resolved, silent ones only when included
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # rows in view with no resolved security: counted, never grouped


def counted_rows(memberships: Iterable[Membership], include_silent: bool) -> CountedRows:
    """The rows a view groups: Context and Bench left out unless ``include_silent``, unresolved rows
    counted and set aside."""
    rows = list(memberships)
    basket_rows = len(rows)
    silent_left_out = 0
    if not include_silent:
        silent_left_out = sum(1 for m in rows if m.silent)
        rows = [m for m in rows if not m.silent]
    unresolved = sum(1 for m in rows if m.security_id is None)
    rows = [m for m in rows if m.security_id is not None]
    return CountedRows(rows, basket_rows, silent_left_out, unresolved)


def link_label(segment: str | None) -> str:
    return segment if segment is not None else "Unsegmented"


def group_of(
    m: Membership, group_by: GroupBy, identity: Mapping[UUID, Mapping[str, object]]
) -> tuple[str, str, dict[str, object]]:
    """The group a (resolved) row lands in: ``(key, label, meta)``. ``meta`` is the group's identity on the
    wire (Link: ``link`` + its card; Card: the card; Business type: ``business_type``; Sector:
    ``supersector``). ``identity`` maps a security to its ``business_type`` and ``business_supersector``
    (``securities.master.identity_for``); only the type and sector views read it."""
    sid = m.security_id
    assert sid is not None

    def ident(field_name: str) -> str | None:
        v = (identity.get(sid) or {}).get(field_name)
        return str(v) if v else None

    if group_by == "link":
        key = f"link:{m.thesis_id}:{m.segment if m.segment is not None else ''}"
        label = f"{link_label(m.segment)} · {m.thesis_name}"
        meta: dict[str, object] = {
            "link": m.segment,
            "thesis_id": m.thesis_id,
            "thesis_name": m.thesis_name,
        }
        return key, label, meta
    if group_by == "card":
        return (
            f"card:{m.thesis_id}",
            m.thesis_name,
            {"thesis_id": m.thesis_id, "thesis_name": m.thesis_name},
        )
    if group_by == "business_type":
        bt = ident("business_type")
        return f"type:{bt or ''}", bt or "Unclassified", {"business_type": bt}
    ss = ident("business_supersector")
    return f"sector:{ss or ''}", ss or "Unclassified", {"supersector": ss}


def buckets(
    rows: Iterable[Membership],
    group_by: GroupBy,
    identity: Mapping[UUID, Mapping[str, object]],
) -> dict[str, tuple[list[Membership], str, dict[str, object]]]:
    """Every group in view, in first-seen order: ``key -> (its rows, label, meta)``."""
    out: dict[str, tuple[list[Membership], str, dict[str, object]]] = {}
    for m in rows:
        key, label, meta = group_of(m, group_by, identity)
        out.setdefault(key, ([], label, meta))[0].append(m)
    return out


@dataclass(frozen=True)
class NameCard:
    thesis_id: UUID
    thesis_name: str
    ticker: str  # the company's ticker on that card (the Cockpit's ?name= key)
    links: tuple[str | None, ...]  # its links on that card in this group (None = Unsegmented)


def companies_in(rows: Sequence[Membership]) -> list[tuple[UUID, tuple[NameCard, ...]]]:
    """A group's companies, each ONCE (first-seen order), with its cards there (by name) and its links on
    each card folded together."""
    by_company: dict[UUID, dict[UUID, list[Membership]]] = {}
    for m in rows:
        assert m.security_id is not None
        by_company.setdefault(m.security_id, {}).setdefault(m.thesis_id, []).append(m)
    out: list[tuple[UUID, tuple[NameCard, ...]]] = []
    for sid, per_card in by_company.items():
        cards: list[NameCard] = []
        for tid, ms in per_card.items():
            links: list[str | None] = []
            for m in ms:
                if m.segment not in links:
                    links.append(m.segment)
            cards.append(NameCard(tid, ms[0].thesis_name, ms[0].ticker, tuple(links)))
        cards.sort(key=lambda c: (c.thesis_name.casefold(), str(c.thesis_id)))
        out.append((sid, tuple(cards)))
    return out


def display_ticker(cards: Sequence[NameCard]) -> str:
    """The ticker a company shows under: the one most of its cards use, ties by ticker."""
    counts: dict[str, int] = {}
    for c in cards:
        counts[c.ticker] = counts.get(c.ticker, 0) + 1
    return min(counts, key=lambda t: (-counts[t], t))


@dataclass(frozen=True)
class FlaggedCompany:
    """A company in view that a price dashboard leaves out: its data flags reach its price readings at the
    as-of (``price_flags``). Named on the page's quiet note, never silently dropped (rule 5 / #9).
    """

    security_id: UUID
    ticker: str  # its display ticker (``display_ticker``)
    reason: str  # the flags' labels ("mixed-scale price history")


def flagged_in_view(
    rows: Sequence[Membership], flagged: Mapping[UUID, str]
) -> tuple[FlaggedCompany, ...]:
    """The flagged companies among the (resolved) rows in view, each ONCE, by ticker."""
    if not flagged:
        return ()
    out = [
        FlaggedCompany(sid, display_ticker(cards), flagged[sid])
        for sid, cards in companies_in(rows)
        if sid in flagged
    ]
    out.sort(key=lambda f: (f.ticker.casefold(), str(f.security_id)))
    return tuple(out)
