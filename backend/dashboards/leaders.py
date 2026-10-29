"""Leaders (``docs/dashboards/leaders.md``): names across every live card near their 52-week high, leading the
market, coiling, with insider buying or loud volume, at the as-of dial; the names on several of those lists;
and the groups where insiders cluster. One row per company, with a chip for every card and link it sits on.

PURE: the router reads the cards (``dashboards/baskets.py``, shared with the Rotation Map and the Cross Radar),
runs seven display members once per company and hands the readings in as ``LeadersInputs`` (cached per as-of,
``dashboards/cache.py``); ``leaders(...)`` builds one view per request. Display only: every number here comes
from a display member's own reading (``signals/display/``); nothing here feeds a call, scores an idea or writes
anything (rule 1).

The rules, in one place:
- **The lists** (a name can be on several):
  near 52-week high = ``range_52w``'s ``pct_off_52w_high`` >= ``NEAR_HIGH_PCT`` (-2.0%);
  RS leader = ``relative_strength``'s headline key ``leading`` (RS at a fresh 13-week high vs SPY or IWM);
  coiling = ``vcp``'s headline key ``coiling``;
  insider buying = ``insider_flow_90d``'s ``buy_count_30d`` >= ``INSIDER_MIN_BUYS`` (1), a CLUSTER when its
  ``distinct_buyers_30d`` >= ``CLUSTER_MIN_BUYERS`` (2, the Cockpit's Ins 30d accent);
  volume = ``rvol``'s ``rvol20`` >= the member's own ``LOUD_MULT_20`` (1.5, the Cockpit's "loud" threshold).
- **No reading** (a name absent from a list for want of data, counted per member on the quiet line, never
  silently gone): the member returned nothing, or the value the list reads is an honest gap. A thin RS (fewer
  than ``relative_strength.MIN_BARS`` aligned bars on both benchmarks: no 13-week verdict yet) is no reading,
  never "not leading".
- **Who counts / groups:** exactly the Rotation Map's (``dashboards/baskets.py``): Context and Bench left out
  unless ``include_silent``; each company once (by ``security_id``) with its cards and links folded together.
  In the insider-cluster Link view a company in two links of a card counts in both; elsewhere once per group.
- **Insider clusters by group:** a group shows when at least ``GROUP_MIN_NAMES`` (2) of its companies have an
  open-market buy in the last 30 days; its share is those names out of every company in the group.
- **Sort.** Each list carries a default order (the page re-sorts); wording stays neutral.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from dashboards.baskets import (
    GroupBy,
    Membership,
    NameCard,
    buckets,
    companies_in,
    counted_rows,
    display_ticker,
)
from signals.display import range52w, relative_strength, rvol
from signals.display.base import DisplaySignal

ListKey = Literal["near_high", "rs_leader", "coiling", "insider", "volume"]

# the lists, in page order, each with the display member it reads
LIST_MEMBER: dict[ListKey, str] = {
    "near_high": "range_52w",
    "rs_leader": "relative_strength",
    "coiling": "vcp",
    "insider": "insider_flow_90d",
    "volume": "rvol",
}
LISTS: tuple[ListKey, ...] = tuple(LIST_MEMBER)

# the display members this dashboard reads (by registered name): the five lists' plus the two column members
MEMBERS: tuple[str, ...] = (*LIST_MEMBER.values(), "trailing_returns", "sma_position")

# near the 52-week high: % off the high at or above this (the member rounds to 2 decimals: -2.0 in, -2.01 out)
NEAR_HIGH_PCT = -2.0
# the volume list: RVOL 20D at or above the member's own loud threshold (the Cockpit's accent), one place
RVOL_LOUD = rvol.LOUD_MULT_20
# insider buying: at least this many open-market buys in the last 30 days
INSIDER_MIN_BUYS = 1
# a name's insider CLUSTER accent: at least this many distinct buyers in the last 30 days (the Cockpit's Ins 30d)
CLUSTER_MIN_BUYERS = 2
# an insider-cluster group: at least this many of its names with open-market buys in the last 30 days
GROUP_MIN_NAMES = 2
# the top section: names on at least this many lists
MULTI_MIN_LISTS = 2
# a 52-week range over fewer bars than this is not a full year (the member's own cut; the row says so)
FULL_YEAR_BARS = range52w.FULL_YEAR_BARS


# --- per-company readings, pulled out of the display members' own output ----------------------------------


@dataclass(frozen=True)
class CompanyReading:
    """One company's readings at the as-of: the few numbers this page shows. ``None`` = no reading."""

    # range_52w
    pct_off_high: float | None = None
    high_52w: float | None = None
    high_date: date | None = None  # the high's print date (the high_52w metric's note)
    range_bars: int | None = None  # bars the range stands on (< FULL_YEAR_BARS = not a full year)
    # relative_strength: None = no 13-week verdict (no RS at all, or too few aligned bars on both benchmarks)
    rs_leading: bool | None = None
    rs_vs: tuple[str, ...] = ()  # the benchmarks the RS is at a 13-week high against
    # vcp: None = too little tape to read a base
    coiling: bool | None = None
    contraction: float | None = None  # the newest segment's depth / the oldest's
    # insider_flow_90d: None = no Form 4 ingested for the name
    buys_30d: int | None = None
    buyers_30d: int | None = None
    # rvol: None = no 20-bar reading
    rvol20: float | None = None
    # the columns every list shows
    ret_7d: float | None = None
    ret_30d: float | None = None
    above_50d: bool | None = None


NO_READING = CompanyReading()


def _metric(sig: DisplaySignal | None, key: str) -> float | None:
    if sig is None:
        return None
    return next((m.value for m in sig.metrics if m.key == key), None)


def _note_date(sig: DisplaySignal | None, key: str) -> date | None:
    """A level metric's print date, off its note ("on 2026-05-29"); ``None`` without a parsable one."""
    note = next((m.note for m in (sig.metrics if sig else []) if m.key == key), None)
    if not note or not note.startswith("on "):
        return None
    try:
        return date.fromisoformat(note[3:].strip())
    except ValueError:
        return None


def _headline_is(sig: DisplaySignal | None, key: str) -> bool:
    return sig is not None and sig.headline is not None and sig.headline.key == key


def _rs(sig: DisplaySignal | None) -> tuple[bool | None, tuple[str, ...]]:
    """``(leading, benchmarks it leads)``; ``(None, ())`` without a 13-week verdict. The member's
    ``bars_used`` is its most aligned bars over the benchmarks, so under ``MIN_BARS`` no benchmark has one.
    """
    if sig is None or (sig.basis.bars_used or 0) < relative_strength.MIN_BARS:
        return None, ()
    events = {e.key for e in sig.events}
    vs = tuple(s for s in relative_strength.BENCHMARKS if f"rs_high_{s.lower()}" in events)
    return _headline_is(sig, "leading"), vs


def _count(v: float | None) -> int | None:
    return None if v is None else round(v)


def company_reading(signals: Mapping[str, DisplaySignal | None]) -> CompanyReading:
    """Read one company's ``CompanyReading`` off the seven members' signals (keyed by member name)."""
    rng = signals.get("range_52w")
    rs_leading, rs_vs = _rs(signals.get("relative_strength"))
    vcp = signals.get("vcp")
    ins = signals.get("insider_flow_90d")
    trail = signals.get("trailing_returns")
    vs_50d = _metric(signals.get("sma_position"), "pct_vs_fast")
    return CompanyReading(
        pct_off_high=_metric(rng, "pct_off_52w_high"),
        high_52w=_metric(rng, "high_52w"),
        high_date=_note_date(rng, "high_52w"),
        range_bars=rng.basis.bars_used if rng is not None else None,
        rs_leading=rs_leading,
        rs_vs=rs_vs,
        coiling=None if vcp is None else _headline_is(vcp, "coiling"),
        contraction=_metric(vcp, "contraction"),
        buys_30d=None if ins is None else _count(_metric(ins, "buy_count_30d")) or 0,
        buyers_30d=None if ins is None else _count(_metric(ins, "distinct_buyers_30d")) or 0,
        rvol20=_metric(signals.get("rvol"), "rvol20"),
        ret_7d=_metric(trail, "ret_7d"),
        ret_30d=_metric(trail, "ret_30d"),
        above_50d=None if vs_50d is None else vs_50d > 0,
    )


def on_list(r: CompanyReading, key: ListKey) -> bool:
    """Whether a company's reading puts it on one list."""
    if key == "near_high":
        return r.pct_off_high is not None and r.pct_off_high >= NEAR_HIGH_PCT
    if key == "rs_leader":
        return r.rs_leading is True
    if key == "coiling":
        return r.coiling is True
    if key == "insider":
        return r.buys_30d is not None and r.buys_30d >= INSIDER_MIN_BUYS
    return r.rvol20 is not None and r.rvol20 >= RVOL_LOUD


def has_reading(r: CompanyReading, key: ListKey) -> bool:
    """Whether the member a list reads gave this company a reading at all."""
    value = {
        "near_high": r.pct_off_high,
        "rs_leader": r.rs_leading,
        "coiling": r.coiling,
        "insider": r.buys_30d,
        "volume": r.rvol20,
    }[key]
    return value is not None


def lists_of(r: CompanyReading) -> tuple[ListKey, ...]:
    return tuple(k for k in LISTS if on_list(r, k))


def is_cluster(r: CompanyReading) -> bool:
    return r.buyers_30d is not None and r.buyers_30d >= CLUSTER_MIN_BUYERS


# --- the cached inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LeadersInputs:
    """Everything the heavy read produces: cached per as-of, shared by every grouping and toggle."""

    live_cards: int  # unarchived cards today
    cards_counted: int  # those that existed by the as-of
    cards_not_yet_created: int  # created after the as-of: left out
    roster_fallback_cards: (
        int  # counted cards with no roster snapshot by then: today's roster stands in
    )
    memberships: tuple[Membership, ...]
    readings: Mapping[
        UUID, CompanyReading
    ]  # every resolved company on the counted cards, silent included


# --- the payload -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaderName:
    """One company on at least one list, every list's value beside it."""

    security_id: UUID
    ticker: str
    name: str | None  # the company's name (today's master)
    cards: tuple[NameCard, ...]
    lists: tuple[ListKey, ...]
    pct_off_high: float | None
    high_52w: float | None
    high_date: date | None
    range_bars: int | None
    rs_vs: tuple[str, ...]
    contraction: float | None
    buys_30d: int | None
    buyers_30d: int | None
    cluster: bool  # buyers_30d >= CLUSTER_MIN_BUYERS
    rvol20: float | None
    ret_7d: float | None
    ret_30d: float | None
    above_50d: bool | None


@dataclass(frozen=True)
class LeaderList:
    key: ListKey
    member: str  # the display member it reads
    security_ids: tuple[UUID, ...]  # its names, in its default order
    no_reading: int  # companies in view the member gave no reading


@dataclass(frozen=True)
class ClusterName:
    security_id: UUID
    ticker: str
    name: str | None
    cards: tuple[NameCard, ...]
    buys_30d: int
    buyers_30d: int
    cluster: bool


@dataclass(frozen=True)
class InsiderGroup:
    key: str
    label: (
        str  # the sort's tie-break, as shown (the page words its own label from the fields below)
    )
    link: str | None = (
        None  # Link view: the link (None = Unsegmented; "Context" / "Bench" when silent)
    )
    thesis_id: UUID | None = None  # Link and Card views
    thesis_name: str | None = None
    business_type: str | None = None  # Business type view (None = Unclassified)
    supersector: str | None = None  # Sector view (None = Unclassified)
    names: int = 0  # companies in the group, each once
    with_buys: int = 0  # of those, names with an open-market buy in the last 30 days
    no_insider_data: int = 0  # of those, names with no Form 4 ingested
    share: float = 0.0  # with_buys / names
    buys: int = 0  # the group's open-market buys in the last 30 days
    members: tuple[ClusterName, ...] = ()  # the names with buys, most buys first


@dataclass(frozen=True)
class Leaders:
    asof: date
    group_by: GroupBy
    include_silent: bool
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # rows in view with no resolved security: counted, never listed
    companies: int  # distinct resolved companies in view
    names: tuple[LeaderName, ...]  # every company on at least one list, in the several-lists order
    several: tuple[UUID, ...]  # names on MULTI_MIN_LISTS or more lists, in their default order
    lists: tuple[LeaderList, ...]
    insider_groups: tuple[InsiderGroup, ...]


def _ret_desc(v: float | None) -> tuple[bool, float]:
    """Highest first, no value last."""
    return (v is None, -(v or 0.0))


def _tie(n: LeaderName) -> tuple[str, str]:
    return (n.ticker.casefold(), str(n.security_id))


def _list_order(key: ListKey, n: LeaderName) -> tuple:
    """Each list's default order: near-high closest first; RS by the 30D return; coiling tightest first;
    insider by buys then buyers; volume by RVOL 20D; ties by ticker."""
    if key == "near_high":
        return (*_ret_desc(n.pct_off_high), *_tie(n))
    if key == "rs_leader":
        return (*_ret_desc(n.ret_30d), *_tie(n))
    if key == "coiling":
        return (n.contraction is None, n.contraction or 0.0, *_tie(n))
    if key == "insider":
        return (-(n.buys_30d or 0), -(n.buyers_30d or 0), *_tie(n))
    return (*_ret_desc(n.rvol20), *_tie(n))


def _several_order(n: LeaderName) -> tuple:
    """The several-lists order: the number of lists, then the 30D return, ties by ticker."""
    return (-len(n.lists), *_ret_desc(n.ret_30d), *_tie(n))


def _leader_name(
    sid: UUID,
    cards: tuple[NameCard, ...],
    r: CompanyReading,
    identity: Mapping[UUID, Mapping[str, object]],
) -> LeaderName:
    name = (identity.get(sid) or {}).get("name")
    return LeaderName(
        security_id=sid,
        ticker=display_ticker(cards),
        name=str(name) if name else None,
        cards=cards,
        lists=lists_of(r),
        pct_off_high=r.pct_off_high,
        high_52w=r.high_52w,
        high_date=r.high_date,
        range_bars=r.range_bars,
        rs_vs=r.rs_vs,
        contraction=r.contraction,
        buys_30d=r.buys_30d,
        buyers_30d=r.buyers_30d,
        cluster=is_cluster(r),
        rvol20=r.rvol20,
        ret_7d=r.ret_7d,
        ret_30d=r.ret_30d,
        above_50d=r.above_50d,
    )


def _insider_group(
    key: str,
    label: str,
    meta: Mapping[str, object],
    companies: Sequence[tuple[UUID, tuple[NameCard, ...]]],
    readings: Mapping[UUID, CompanyReading],
    identity: Mapping[UUID, Mapping[str, object]],
) -> InsiderGroup | None:
    """One group's insider read, or ``None`` when fewer than ``GROUP_MIN_NAMES`` of its names bought."""
    buyers: list[ClusterName] = []
    no_data = 0
    for sid, cards in companies:
        r = readings[sid]
        if r.buys_30d is None:
            no_data += 1
        if not on_list(r, "insider"):
            continue
        assert r.buys_30d is not None and r.buyers_30d is not None
        name = (identity.get(sid) or {}).get("name")
        buyers.append(
            ClusterName(
                security_id=sid,
                ticker=display_ticker(cards),
                name=str(name) if name else None,
                cards=cards,
                buys_30d=r.buys_30d,
                buyers_30d=r.buyers_30d,
                cluster=is_cluster(r),
            )
        )
    if len(buyers) < GROUP_MIN_NAMES:
        return None
    buyers.sort(key=lambda c: (-c.buys_30d, -c.buyers_30d, c.ticker.casefold(), str(c.security_id)))
    return InsiderGroup(
        key=key,
        label=label,
        names=len(companies),
        with_buys=len(buyers),
        no_insider_data=no_data,
        share=round(len(buyers) / len(companies), 4),
        buys=sum(c.buys_30d for c in buyers),
        members=tuple(buyers),
        **meta,  # type: ignore[arg-type]
    )


def insider_groups(
    rows: Iterable[Membership],
    readings: Mapping[UUID, CompanyReading],
    identity: Mapping[UUID, Mapping[str, object]],
    group_by: GroupBy,
) -> list[InsiderGroup]:
    """The groups where at least ``GROUP_MIN_NAMES`` names bought, by names with buys, then share, then label."""
    out: list[InsiderGroup] = []
    for key, (members, label, meta) in buckets(rows, group_by, identity).items():
        g = _insider_group(key, label, meta, companies_in(members), readings, identity)
        if g is not None:
            out.append(g)
    out.sort(key=lambda g: (-g.with_buys, -g.share, g.label.casefold(), g.key))
    return out


def leaders(
    inputs: LeadersInputs,
    identity: Mapping[UUID, Mapping[str, object]],
    *,
    asof: date,
    group_by: GroupBy = "link",
    include_silent: bool = False,
) -> Leaders:
    """The Leaders payload for one view. ``identity`` maps a security to its ``name``, ``business_type`` and
    ``business_supersector`` (``securities.master.identity_for``)."""
    counted = counted_rows(inputs.memberships, include_silent)
    companies = companies_in(counted.rows)
    readings = {sid: inputs.readings.get(sid, NO_READING) for sid, _ in companies}

    names = [
        _leader_name(sid, cards, readings[sid], identity)
        for sid, cards in companies
        if lists_of(readings[sid])
    ]
    names.sort(key=_several_order)
    lists = tuple(
        LeaderList(
            key=key,
            member=LIST_MEMBER[key],
            security_ids=tuple(
                n.security_id
                for n in sorted(
                    (n for n in names if key in n.lists), key=lambda n: _list_order(key, n)
                )
            ),
            no_reading=sum(1 for r in readings.values() if not has_reading(r, key)),
        )
        for key in LISTS
    )
    return Leaders(
        asof=asof,
        group_by=group_by,
        include_silent=include_silent,
        live_cards=inputs.live_cards,
        cards_counted=inputs.cards_counted,
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        basket_rows=counted.basket_rows,
        silent_rows_left_out=counted.silent_rows_left_out,
        unresolved_rows=counted.unresolved_rows,
        companies=len(companies),
        names=tuple(names),
        several=tuple(n.security_id for n in names if len(n.lists) >= MULTI_MIN_LISTS),
        lists=lists,
        insider_groups=tuple(insider_groups(counted.rows, readings, identity, group_by)),
    )
