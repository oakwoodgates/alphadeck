"""Rotation Map (``docs/dashboards/rotation-map.md``): which value-chain links, business types and sectors have
price moving, across every live card. Rows are GROUPS (a link of a card, a business type, a sector, or a
card); columns are the group's median trailing returns with "x/n up", the share above the 50-day SMA, the
median 20-day RVOL, recent golden / death crosses and RS leaders. Follows the as-of dial.

PURE: the router reads the cards, runs the four display members once per company and hands the result in
as ``RotationInputs`` (cached per as-of, ``dashboards/cache.py``); ``rotation_map(...)`` groups it per
request. Display only: every number here comes from a display member's own reading
(``signals/display/``), nothing here feeds a call, scores an idea or writes anything (rule 1).

The rules, in one place:
- **Median, "x/n up", thin groups.** A window's median is over the group's names priced for that window
  (a name with too little tape is left out of that window and shows in its ``n``). Fewer than
  ``MIN_PRICED`` priced names = no median ("—"), the Cockpit's link-header rule
  (``frontend/src/cockpit/groupAggregate.ts``).
- **Who counts.** Context and Bench memberships (``is_silent``) are left out unless ``include_silent``;
  then the Link view shows them as their own rows and the other views include their companies. In the Link
  view a company in two links of a card counts in both; in the Business type, Sector and Card views each
  company counts ONCE per group (by ``security_id``). A row with no link groups as "Unsegmented" (``None``);
  a company with no business type / sector as "Unclassified" (``None``). A basket row with no resolved
  security is counted on the quiet line, never dropped (rule 5 / invariant #9). These rules live in
  ``dashboards/baskets.py``, shared with the Cross Radar.
- **A flagged price history.** A company whose data flags reach its price readings at the as-of
  (``dashboards.baskets.price_flags``, the reader the Cockpit's grayed price cells come from) is left out of
  every aggregate: no window's median, "x/n up" or "above SPY", no above-50d share, RVOL median, cross or RS
  count. It still counts in its group's ``names`` (``flagged`` says how many), never in ``priced_companies``;
  its member row carries no reading and its ``flag``, and the page names it (``flagged``).
- **Color only the unusual.** A return median is toned ``pos`` / ``neg`` only when it sits in the top /
  bottom ``TONE_SHARE`` of the groups in view with a value in that column (cross-sectional, ties included,
  ``len // 10`` groups each side, so a view of fewer than 10 groups colors nothing). The vs-SPY view
  subtracts one number per column, so it never changes a rank or a tone.
- **Sort.** Default: the 7d median, highest first, "—" last; ties by label. Neutral wording only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from statistics import median
from typing import Literal
from uuid import UUID

# the cards, rosters, groups and who counts: shared with the Cross Radar (``dashboards/baskets.py``), and
# re-exported here under their old names
from dashboards.baskets import GROUP_BYS as GROUP_BYS
from dashboards.baskets import (
    FlaggedCompany,
    buckets,
    companies_in,
    counted_rows,
    display_ticker,
    flagged_in_view,
)
from dashboards.baskets import GroupBy as GroupBy
from dashboards.baskets import Membership as Membership
from dashboards.baskets import NameCard as NameCard
from dashboards.baskets import cards_that_existed as cards_that_existed
from dashboards.baskets import memberships_of as memberships_of
from signals.display.base import DisplaySignal

# the Cockpit's own windows and keys (the trailing_returns member: 1/7/30/90/252 trading bars)
WINDOWS: tuple[str, ...] = ("ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y")
DEFAULT_SORT = "ret_7d"
# fewer priced names than this = no median (the Cockpit link header's AGG_MIN_PRICED)
MIN_PRICED = 3
# a golden / death cross counts when it printed within this many calendar days on or before the as-of
# (the Cockpit's TAPE_CROSS_DAYS, frontend/src/cockpit/tapeMarks.ts)
CROSS_DAYS = 30
# the share of groups on each side of a column that is colored
TONE_SHARE = 0.10

# the display members this dashboard reads (by registered name)
MEMBERS: tuple[str, ...] = ("trailing_returns", "sma_position", "rvol", "relative_strength")

Tone = Literal["pos", "neg"]
Cross = Literal["golden", "death"]


# --- per-company readings, pulled out of the display members' own output ----------------------------------


@dataclass(frozen=True)
class CompanyTape:
    """One company's readings at the as-of. ``None`` = the member gave no reading (an honest gap)."""

    returns: Mapping[str, float | None]  # WINDOWS key -> % return
    pct_vs_50d: float | None = None  # sma_position's pct_vs_fast
    rvol20: float | None = None
    cross: Cross | None = None  # the member's last 50d x 200d cross, whenever it printed
    cross_date: date | None = None
    rs_leading: bool | None = None  # relative_strength headline == "leading"; None = no RS reading
    priced: bool = False  # trailing_returns gave a reading (the company has a close)


NO_TAPE = CompanyTape(returns={w: None for w in WINDOWS})


def _metric(sig: DisplaySignal | None, key: str) -> float | None:
    if sig is None:
        return None
    return next((m.value for m in sig.metrics if m.key == key), None)


def returns_of(sig: DisplaySignal | None) -> dict[str, float | None]:
    """The trailing_returns member's five windows (``None`` where its tape is too short)."""
    return {w: _metric(sig, w) for w in WINDOWS}


def company_tape(signals: Mapping[str, DisplaySignal | None]) -> CompanyTape:
    """Read one company's ``CompanyTape`` off the four members' signals (keyed by member name)."""
    trail = signals.get("trailing_returns")
    sma = signals.get("sma_position")
    rs = signals.get("relative_strength")
    cross_ev = next(
        (e for e in (sma.events if sma else []) if e.key in ("golden_cross", "death_cross")), None
    )
    return CompanyTape(
        returns=returns_of(trail),
        pct_vs_50d=_metric(sma, "pct_vs_fast"),
        rvol20=_metric(signals.get("rvol"), "rvol20"),
        cross=(
            None if cross_ev is None else ("golden" if cross_ev.key == "golden_cross" else "death")
        ),
        cross_date=cross_ev.date if cross_ev else None,
        rs_leading=(
            None if rs is None else (rs.headline is not None and rs.headline.key == "leading")
        ),
        priced=trail is not None,
    )


def recent_cross(tape: CompanyTape, asof: date) -> Cross | None:
    """The company's cross when it printed within ``CROSS_DAYS`` calendar days on or before ``asof``."""
    if tape.cross is None or tape.cross_date is None:
        return None
    if asof - timedelta(days=CROSS_DAYS) <= tape.cross_date <= asof:
        return tape.cross
    return None


# --- the cached inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RotationInputs:
    """Everything the heavy read produces: cached per as-of, shared by every grouping and toggle."""

    live_cards: int  # unarchived cards today
    cards_counted: int  # those that existed by the as-of
    cards_not_yet_created: int  # created after the as-of: left out
    roster_fallback_cards: (
        int  # counted cards with no roster snapshot by then: today's roster stands in
    )
    memberships: tuple[Membership, ...]
    tapes: Mapping[
        UUID, CompanyTape
    ]  # every resolved company on the counted cards, silent ones included
    spy: Mapping[str, float | None] | None  # SPY's own trailing returns; None = no SPY tape
    # companies whose data flags reach their price readings at the as-of, each with its reason
    # (``dashboards.baskets.price_flags``): left out of every aggregate
    flagged: Mapping[UUID, str] = field(default_factory=dict)


# --- the payload -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowStat:
    key: str
    median: float | None  # None when fewer than MIN_PRICED names are priced for the window
    n: int  # names priced for the window
    up: int  # of those, names with a positive return
    above_spy: int | None  # of those, names whose return beats SPY's (None = no SPY return for it)
    tone: Tone | None


@dataclass(frozen=True)
class NameRow:
    security_id: UUID
    ticker: str
    cards: tuple[NameCard, ...]
    returns: Mapping[str, float | None]
    above_50d: bool | None
    rvol20: float | None
    cross: Cross | None  # within CROSS_DAYS of the as-of
    cross_date: date | None
    rs_leading: bool | None
    flag: str | None = None  # why it is left out (a flagged price history); None = read


@dataclass(frozen=True)
class GroupRow:
    key: str
    link: str | None = (
        None  # Link view: the link (None = Unsegmented; "Context" / "Bench" when silent)
    )
    thesis_id: UUID | None = None  # Link and Card views
    thesis_name: str | None = None
    business_type: str | None = None  # Business type view (None = Unclassified)
    supersector: str | None = None  # Sector view (None = Unclassified)
    names: int = 0  # companies in the group, each once (flagged ones included)
    flagged: int = 0  # of those, left out of every aggregate for a flagged price history
    windows: tuple[WindowStat, ...] = ()
    above_50d: int = 0
    above_50d_n: int = 0
    rvol20_median: float | None = None
    rvol20_n: int = 0
    golden: int = 0
    death: int = 0
    rs_leaders: int = 0
    rs_n: int = 0
    members: tuple[NameRow, ...] = field(default=())
    label: str = ""  # the sort's tie-break (case-folded)


@dataclass(frozen=True)
class RotationMap:
    asof: date
    group_by: GroupBy
    include_silent: bool
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # rows in view with no resolved security: counted, never grouped
    companies: int  # distinct resolved companies in view
    priced_companies: int  # of those, companies with a close at the as-of (never a flagged one)
    spy: Mapping[str, float | None] | None
    groups: tuple[GroupRow, ...]
    # companies in view left out for a flagged price history, by ticker
    flagged: tuple[FlaggedCompany, ...] = ()


def _median(values: Sequence[float]) -> float | None:
    return round(median(values), 2) if len(values) >= MIN_PRICED else None


def _window_stat(key: str, tapes: Sequence[CompanyTape], spy: float | None) -> WindowStat:
    vals = [v for t in tapes if (v := t.returns.get(key)) is not None]
    return WindowStat(
        key=key,
        median=_median(vals),
        n=len(vals),
        up=sum(1 for v in vals if v > 0),
        above_spy=None if spy is None else sum(1 for v in vals if v > spy),
        tone=None,
    )


def _name_sort_key(row: NameRow) -> tuple:
    v = row.returns.get(DEFAULT_SORT)
    return (v is None, -(v or 0.0), row.ticker.casefold(), str(row.security_id))


def _group(
    key: str,
    rows: Sequence[Membership],
    tapes: Mapping[UUID, CompanyTape],
    spy: Mapping[str, float | None] | None,
    asof: date,
    label: str,
    flagged: Mapping[UUID, str],
    **ident: object,
) -> GroupRow:
    """One group over its membership rows: each company once (its rows' cards and links folded together).
    A flagged company is listed with no reading and its ``flag``, and left out of every aggregate.
    """
    members: list[NameRow] = []
    group_tapes: list[CompanyTape] = []
    for sid, cards in companies_in(rows):
        flag = flagged.get(sid)
        tape = NO_TAPE if flag is not None else tapes.get(sid, NO_TAPE)
        if flag is None:
            group_tapes.append(tape)
        cross = recent_cross(tape, asof)
        members.append(
            NameRow(
                security_id=sid,
                ticker=display_ticker(cards),
                cards=cards,
                returns=dict(tape.returns),
                above_50d=None if tape.pct_vs_50d is None else tape.pct_vs_50d > 0,
                rvol20=tape.rvol20,
                cross=cross,
                cross_date=tape.cross_date if cross else None,
                rs_leading=tape.rs_leading,
                flag=flag,
            )
        )
    members.sort(key=_name_sort_key)
    above = [t.pct_vs_50d > 0 for t in group_tapes if t.pct_vs_50d is not None]
    rvols = [t.rvol20 for t in group_tapes if t.rvol20 is not None]
    crosses = [recent_cross(t, asof) for t in group_tapes]
    rs = [t.rs_leading for t in group_tapes if t.rs_leading is not None]
    return GroupRow(
        key=key,
        names=len(members),
        flagged=len(members) - len(group_tapes),
        windows=tuple(_window_stat(w, group_tapes, (spy or {}).get(w)) for w in WINDOWS),
        above_50d=sum(above),
        above_50d_n=len(above),
        rvol20_median=_median(rvols),
        rvol20_n=len(rvols),
        golden=crosses.count("golden"),
        death=crosses.count("death"),
        rs_leaders=sum(rs),
        rs_n=len(rs),
        members=tuple(members),
        label=label.casefold(),
        **ident,  # type: ignore[arg-type]
    )


def _tones(groups: list[GroupRow]) -> list[GroupRow]:
    """Tone each return median in the top / bottom ``TONE_SHARE`` of the groups with a value in its column."""
    tones: dict[tuple[int, str], Tone] = {}
    for w in WINDOWS:
        vals = [
            (i, s.median)
            for i, g in enumerate(groups)
            for s in g.windows
            if s.key == w and s.median is not None
        ]
        k = int(len(vals) * TONE_SHARE)
        if k == 0:
            continue
        ordered = sorted(v for _, v in vals)
        low_cut, high_cut = ordered[k - 1], ordered[-k]
        for i, v in vals:
            if v >= high_cut and v > low_cut:
                tones[(i, w)] = "pos"
            elif v <= low_cut and v < high_cut:
                tones[(i, w)] = "neg"
    out: list[GroupRow] = []
    for i, g in enumerate(groups):
        wins = tuple(
            WindowStat(s.key, s.median, s.n, s.up, s.above_spy, tones.get((i, s.key)))
            for s in g.windows
        )
        out.append(replace(g, windows=wins))
    return out


def group_sort_key(g: GroupRow, col: str = DEFAULT_SORT) -> tuple:
    """Highest first, groups without a value last, ties by label: the page's default order."""
    v = next((s.median for s in g.windows if s.key == col), None)
    return (v is None, -(v or 0.0), g.label, g.key)


def rotation_map(
    inputs: RotationInputs,
    identity: Mapping[UUID, Mapping[str, object]],
    *,
    asof: date,
    group_by: GroupBy = "link",
    include_silent: bool = False,
) -> RotationMap:
    """The Rotation Map payload for one view. ``identity`` maps a security to its ``business_type`` and
    ``business_supersector`` (``securities.master.identity_for``); only the type and sector views read it.
    """
    counted = counted_rows(inputs.memberships, include_silent)
    rows = counted.rows
    flagged = inputs.flagged
    groups = [
        _group(key, members, inputs.tapes, inputs.spy, asof, label, flagged, **meta)
        for key, (members, label, meta) in buckets(rows, group_by, identity).items()
    ]
    groups = _tones(groups)
    groups.sort(key=group_sort_key)

    companies = {m.security_id for m in rows}
    return RotationMap(
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
        priced_companies=sum(
            1 for sid in companies if sid not in flagged and inputs.tapes.get(sid, NO_TAPE).priced
        ),
        spy=inputs.spy,
        groups=tuple(groups),
        flagged=flagged_in_view(rows, flagged),
    )
