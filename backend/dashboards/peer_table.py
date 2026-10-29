"""Peer Table (``docs/dashboards/peer-table.md``): the names across the cards counted at the as-of side by side
(size, revenue, runway, dilution, purity, tape and insider buying), each company ONCE, grouped by what it does, every
blank counted. Follows the as-of dial.

PURE: the router reads the counted cards' rosters as of the as-of (``dashboards/baskets.py``), the master identity
and, for the companies in the picked scope only, each company's readings through the same point-in-time view the
Cockpit builds (``baskets.per_company``, the request's one ``serve_view(asof)`` pair), and hands them in as
``PeerInputs``; ``peer_table(...)`` builds the scope's whole table ONCE per (scope, clock, as-of) in the shared
dashboard cache (``dashboards/cache.py``). Sorting and filtering are client side. Display only: nothing here feeds
a call, scores an idea or writes anything (rule 1). A cap tier is a slicing dimension, never a call input or sizing
(#4); every figure is one the Workbench or a display member already computes, and the model never writes one (#3).

The rules, in one place. This is our own simple table, stated here and on the page.
- **Which names.** Every RESOLVED roster row of the counted cards, Context and Bench included, each company ONCE by
  ``security_id`` (its cards and links folded into chips, its context roles as a quiet tag, "silent only" when every
  one of its rows is Context or Bench). A row with no resolved security can carry no reading: counted and listed,
  never a row (invariant #9). Business types are today's identity; links and rosters are as of the as-of.
- **The scopes.** One at a time: a business-type leaf, a supersector (grouped by leaf), one card (grouped by link in
  chain order: the card's real links, a roster link no longer on the card, "No link", then Context and Bench) or
  every counted card (grouped by supersector, then leaf). A company in several links of the picked card is listed
  ONCE, under the first of them; its other links ride in its cell. In a card scope the rows are that card's rows only.
- **Blank means not stated, never zero.** Every cell is stated (``gap`` is ``None``) or carries the reason it is
  blank (a ``Gap`` code, plus the member's own note where it gave one). Nothing is filled with a made-up number, and
  the coverage of every column (stated, total, blanks by reason) is part of the table.
- **Approximate market cap** = the latest close times the latest shares fact, with the Workbench's rules
  (``workbench/scoring._market_cap``, copied here, not imported: that module pulls the call config): an ``ads_ratio_status``
  of ``known`` divides the ordinary count by the ratio, ``unread`` WITHHOLDS the cap, NULL means 1:1 (a domestic
  name); a split in force between the count's date and the close's puts the count on the close's scale
  (``domain.splits.scale_after``, "never mix a split-adjusted price with a pre-split count"). The tier is
  ``domain.market_cap.cap_tier``. Labeled approximate everywhere.
- **Runway** = cash / (quarterly burn / 3) months from the latest cash/burn fact, "cash-positive" when the burn is
  zero or negative (``_runway``'s rule: the figure, not the pips). **Overhang** = the dilution clock's own convert
  overhang percent (``signals.dilution_clock.overhang_pct``). **Purity** = the largest revenue-mix line with its
  segment label (``_purity``'s rule). **Revenue** = the latest period's revenue from the XBRL series; **YoY** = the
  change against the row for the same period end one year earlier within 10 days, when that row is in the same read
  (``filings.prior_year_row``).
- **The tape** is the display members' own readings: 52-week high (``range_52w``), the 50- and 200-day averages
  (``sma_position``), RVOL 20 (``rvol``), 20-day dollar volume (``volume_regime``), the 30-trading-day return minus
  SPY's, in percentage points (``trailing_returns``; SPY by the same pure rule, blank when the two last closes differ,
  the Read-Throughs' rule), and the insider buys of the member's 90-day window as it screens them (``insider_flow_90d``).
  A company whose data flags reach its price readings (``baskets.price_flags``) keeps its row: its price-derived
  cells (cap, 52w, 50d, 200d, RVOL, dollar volume, vs SPY) are blank with the reason, and the page names it.
- **Groups.** Each group's row says what it rests on: names, the median approximate cap, the names above the 50-day
  average of those with a reading, the median 30-day return minus SPY's, the median YoY, the names with a runway
  figure. Medians only, equal weight, no score. Stated for every name in the group AND for the names that count (not
  silent-only), so the page's toggle changes them honestly.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any, Literal, get_args
from uuid import UUID

from dashboards.baskets import (
    FlaggedCompany,
    Membership,
    NameCard,
    display_ticker,
    flagged_in_view,
    link_count,
)
from dashboards.filings import prior_year_row
from domain.enums import ContextRole
from domain.market_cap import CapTier, cap_tier
from domain.splits import ListedSplit, scale_after
from domain.thesis import SILENT_LABELS
from signals.dilution_clock import overhang_pct
from signals.display.base import DisplayMetric, DisplaySignal

ScopeKind = Literal["business_type", "supersector", "card", "all"]
SCOPE_KINDS: tuple[ScopeKind, ...] = ("business_type", "supersector", "card", "all")
UNCLASSIFIED = "unclassified"  # the scope key of the companies with no business type / supersector
OTHER = "other"  # the visible tail leaf / supersector (an unmapped but present sector)
BENCHMARK = "SPY"
# the context roles in the enum's own order, so a role added to ``ContextRole`` lists itself
ROLE_ORDER: tuple[str, ...] = tuple(r.value for r in ContextRole)
NO_LINK = "No link"
# the columns the coverage is counted for, in the table's order
Column = Literal[
    "cap",
    "revenue",
    "yoy",
    "runway",
    "overhang",
    "purity",
    "off_high",
    "sma50",
    "sma200",
    "rvol20",
    "adv",
    "vs_spy",
    "insider",
]
COLUMNS: tuple[Column, ...] = get_args(Column)

# Why a cell is blank. A stated cell has no gap.
Gap = Literal[
    "flagged",  # data flags reach the price readings: withheld, the reason in ``note``
    "no_price",  # no price bars at the as-of
    "no_reading",  # the display member gave no value (too few bars, no volume); ``note`` says why
    "no_shares",  # a price but no shares fact
    "ads_unread",  # an ADR whose ordinary-shares-per-ADS ratio could not be read: the cap is withheld
    "no_cap",  # a share count or close that is not positive
    "no_revenue",  # no revenue facts
    "no_prior_year",  # a revenue print but no row for the same period a year earlier in the read
    "no_burn",  # no cash/burn fact
    "no_convert",  # no live convertible-note overhang on file
    "no_mix",  # no revenue-mix fact
    "no_form4",  # no Form 4 ingested for the name
    "no_spy",  # SPY has no bars at the as-of
    "ends_differ",  # the company's last close is not SPY's last close
]

GroupKind = Literal["supersector", "business_type", "link", "old_link", "no_link", "silent"]


# --- the inputs, as read --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardRef:
    """A counted card: its name and its links TODAY in chain order (links load live; only the roster is
    snapshotted), Context and Bench included."""

    thesis_id: UUID
    thesis_name: str
    segments: tuple[str, ...] = ()


@dataclass(frozen=True)
class Identity:
    """A company's master identity: its name, its leaf business type and supersector (the operator's re-tag already
    folded in, ``securities.business_type.resolve_business_type``; ``None`` = unclassified) and the royalty/streaming
    overlay."""

    name: str | None
    business_type: str | None
    supersector: str | None
    royalty: bool = False


def identities(raw: Mapping[UUID, Mapping[str, object]]) -> dict[UUID, Identity]:
    """``master.identity_for``'s rows as this page's ``Identity``."""
    out: dict[UUID, Identity] = {}
    for sid, row in raw.items():
        name, bt, ss = row.get("name"), row.get("business_type"), row.get("business_supersector")
        out[sid] = Identity(
            name=str(name) if name else None,
            business_type=str(bt) if bt else None,
            supersector=str(ss) if ss else None,
            royalty=bool(row.get("royalty")),
        )
    return out


@dataclass(frozen=True)
class ScopeBase:
    """The cheap roster-level read every view of the page starts from: the counted cards with their rosters as of
    the as-of, the master identity of every resolved company on them, and the counts the quiet line shows.
    """

    live_cards: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards: tuple[CardRef, ...]
    memberships: tuple[Membership, ...]
    identity: Mapping[UUID, Identity]


@dataclass(frozen=True)
class CompanyReads:
    """One company's raw readings, as the router's heavy read produced them. ``flag`` = why its price readings are
    withheld (``baskets.price_flags``); when set the price-derived fields are left ``None`` (never read).
    """

    flag: str | None = None
    close: tuple[date, float] | None = None  # the latest bar: (date, close)
    shares: Mapping[str, Any] | None = (
        None  # the latest shares fact: shares, valid_from, ads_ratio(_status)
    )
    splits: tuple[ListedSplit, ...] = ()
    revenue: tuple[
        Mapping[str, Any], ...
    ] = ()  # metric_key == "revenue" rows: period_end, value, valid_from
    cash_burn: Mapping[str, Any] | None = None  # the latest cash/burn fact
    dilution: tuple[Mapping[str, Any], ...] = ()  # fact_dilution rows (the clock's own input)
    mix: tuple[Mapping[str, Any], ...] = ()  # revenue-mix facts
    range_52w: DisplaySignal | None = None
    sma: DisplaySignal | None = None
    rvol: DisplaySignal | None = None
    volume: DisplaySignal | None = None
    returns: DisplaySignal | None = None
    insider: DisplaySignal | None = None


@dataclass(frozen=True)
class PeerInputs:
    """Everything the heavy read produces for ONE scope: the base, the readings of the companies in scope, and
    SPY's trailing returns (``None`` = SPY has no bars at the as-of)."""

    base: ScopeBase
    reads: Mapping[UUID, CompanyReads]
    spy: DisplaySignal | None


# --- the cells ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """A plain number cell: ``value`` is stated or ``gap`` says why not (never both, never zero for blank)."""

    value: float | None = None
    gap: Gap | None = None
    note: str | None = None


@dataclass(frozen=True)
class CapCell:
    """The approximate market cap and its tier, with the inputs that made it (the hover shows the product)."""

    value: float | None = None
    tier: CapTier | None = None
    gap: Gap | None = None
    note: str | None = None
    close: float | None = None
    close_date: date | None = None
    shares: float | None = (
        None  # the ordinary count on file (before the ADS ratio and any split scaling)
    )
    shares_date: date | None = None
    ads_ratio: int | None = None  # applied when the status is "known"
    split_factor: float | None = None  # the multiplier put on the count to match the close's scale


@dataclass(frozen=True)
class RevenueCell:
    """The latest period's revenue (``period_end`` on hover)."""

    value: float | None = None
    period_end: date | None = None
    gap: Gap | None = None


@dataclass(frozen=True)
class YoyCell:
    """The change against the same period a year earlier, in percent, with the prior figure it rests on."""

    value: float | None = None
    prior_value: float | None = None
    prior_period_end: date | None = None
    gap: Gap | None = None


@dataclass(frozen=True)
class RunwayCell:
    """Months of cash at the current burn, or ``cash_positive`` (burn at or below zero: no finite figure)."""

    months: float | None = None
    cash_positive: bool = False
    cash_usd: float | None = None
    burn_usd: float | None = None  # the quarterly burn
    as_of: date | None = None  # the fact's date
    gap: Gap | None = None


@dataclass(frozen=True)
class PurityCell:
    """The largest revenue-mix line: its percent and its segment label."""

    value: float | None = None
    segment: str | None = None
    gap: Gap | None = None


@dataclass(frozen=True)
class SpyCell:
    """The 30-trading-day return minus SPY's, in percentage points, with the two returns it is made of."""

    value: float | None = None
    ret_pct: float | None = None  # the company's own return
    spy_pct: float | None = None
    last_close: date | None = None
    gap: Gap | None = None
    note: str | None = None


@dataclass(frozen=True)
class InsiderCell:
    """The member's open-market buys in its 90-day window: count, dollars and distinct buyers. ``buys = 0`` is a
    stated zero (Form 4s are ingested, none was a buy); no Form 4 ingested at all is a blank."""

    buys: int | None = None
    usd: float | None = None
    buyers: int | None = None
    gap: Gap | None = None
    note: str | None = None


# --- the rules ------------------------------------------------------------------------------------------------


def _day(v: Any) -> date | None:
    return v if isinstance(v, date) else None


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def metric_of(sig: DisplaySignal | None, key: str) -> DisplayMetric | None:
    """The named metric of a display member's output, or ``None``."""
    if sig is None:
        return None
    return next((m for m in sig.metrics if m.key == key), None)


def metric_cell(sig: DisplaySignal | None, key: str, flag: str | None) -> Cell:
    """A display member's metric as a cell: withheld for a flagged price history, ``no_price`` without bars,
    ``no_reading`` (with the member's own note: "n/a: 120/200 bars") when the member stated no value.
    """
    if flag is not None:
        return Cell(gap="flagged", note=flag)
    if sig is None:
        return Cell(gap="no_price")
    m = metric_of(sig, key)
    if m is None or m.value is None:
        return Cell(gap="no_reading", note=m.note if m else None)
    return Cell(value=m.value)


def cap_cell(reads: CompanyReads) -> CapCell:
    """The approximate market cap, by ``workbench/scoring._market_cap``'s rule (copied; see the module note).

    A flagged price history withholds it. No price bars: ``no_price`` ("awaiting price"). A price but no shares
    fact: ``no_shares`` ("awaiting shares"). ``ads_ratio_status`` ``unread``, or ``known`` with a missing or
    absurd ratio (a corrupt pairing is treated as unread: fail closed), withholds the cap; ``known`` divides the
    ordinary count by the ratio; NULL is not applicable and counts 1:1. A split in force with a first session
    after the count's date and on or before the close's puts the count on the close's scale.
    """
    if reads.flag is not None:
        return CapCell(gap="flagged", note=reads.flag)
    sh, px = reads.shares, reads.close
    if px is None:
        return CapCell(gap="no_price", shares=_f(sh["shares"]) if sh else None)
    close_date, close = px
    if sh is None:
        return CapCell(gap="no_shares", close=close, close_date=close_date)
    count, shares_date = float(sh["shares"]), _day(sh.get("valid_from"))
    status, ratio = sh.get("ads_ratio_status"), sh.get("ads_ratio")
    base = dict(close=close, close_date=close_date, shares=count, shares_date=shares_date)
    if status == "unread" or (status == "known" and not (ratio and float(ratio) >= 1)):
        return CapCell(gap="ads_unread", **base)
    divisor = float(ratio) if status == "known" else 1.0
    k = scale_after(list(reads.splits), shares_date, through=close_date) if shares_date else 1
    cap = count / divisor / float(k) * close
    if cap <= 0:
        return CapCell(gap="no_cap", note="the share count or the close is not positive", **base)
    return CapCell(
        value=float(round(cap)),
        tier=cap_tier(cap),
        ads_ratio=int(ratio) if status == "known" and ratio else None,
        split_factor=float(1 / k) if k != 1 else None,
        **base,
    )


def revenue_cells(rows: Sequence[Mapping[str, Any]]) -> tuple[RevenueCell, YoyCell]:
    """The latest period's revenue and its year-over-year change. Latest = the newest period end (ties by filing
    date); YoY = against ``filings.prior_year_row`` (the row for the same period end one year earlier within 10
    days, from the SAME read), blank when there is none or it is not positive."""
    rev = [r for r in rows if _f(r.get("value")) is not None and _day(r.get("period_end"))]
    if not rev:
        return RevenueCell(gap="no_revenue"), YoyCell(gap="no_revenue")
    latest = max(rev, key=lambda r: (r["period_end"], _day(r.get("valid_from")) or date.min))
    value = float(latest["value"])
    cell = RevenueCell(value=value, period_end=latest["period_end"])
    prior = prior_year_row(latest, rev)
    then = _f(prior.get("value")) if prior else None
    if prior is None or then is None or then <= 0:
        return cell, YoyCell(gap="no_prior_year")
    return cell, YoyCell(
        value=round((value / then - 1.0) * 100.0, 1),
        prior_value=then,
        prior_period_end=prior["period_end"],
    )


def runway_cell(fact: Mapping[str, Any] | None) -> RunwayCell:
    """Months of cash at the current burn from the latest cash/burn fact (``_runway``'s figure, not its pips):
    cash / (quarterly burn / 3); ``cash_positive`` when the burn is zero or negative."""
    if fact is None:
        return RunwayCell(gap="no_burn")
    cash, burn = float(fact["cash_usd"]), float(fact["quarterly_burn_usd"])
    base = dict(cash_usd=cash, burn_usd=burn, as_of=_day(fact.get("valid_from")))
    if burn <= 0:
        return RunwayCell(cash_positive=True, **base)
    return RunwayCell(months=round(cash / (burn / 3.0), 1), **base)


def overhang_cell(facts: Sequence[Mapping[str, Any]], asof: date) -> Cell:
    """The dilution clock's own convert-overhang percent over the as-of dilution facts (rounded to one decimal, as
    the Workbench meter shows it); ``no_convert`` when none is live or no share basis exists (never a 0).
    """
    pct = overhang_pct([dict(f) for f in facts], asof)
    if pct is None:
        return Cell(
            gap="no_convert",
            note=(
                "convertible-note facts on file, none live with a share basis" if facts else None
            ),
        )
    return Cell(value=round(pct, 1))


def purity_cell(facts: Sequence[Mapping[str, Any]]) -> PurityCell:
    """The largest revenue-mix line (``_purity``'s rule) with its segment label."""
    if not facts:
        return PurityCell(gap="no_mix")
    best = max(facts, key=lambda f: float(f["mix_pct"]))
    return PurityCell(value=float(best["mix_pct"]), segment=best.get("segment_label"))


def spy_cell(own: DisplaySignal | None, spy: DisplaySignal | None, flag: str | None) -> SpyCell:
    """The company's 30-trading-day return minus SPY's, in percentage points (the Read-Throughs' tape rule):
    withheld for a flagged price history, ``no_price`` without bars, ``no_spy`` without SPY bars, ``ends_differ``
    when the two last closes are not the same day (the returns would end on different days), ``no_reading`` when a
    window is short."""
    if flag is not None:
        return SpyCell(gap="flagged", note=flag)
    if own is None or own.basis.window_end is None:
        return SpyCell(gap="no_price")
    ret = metric_of(own, "ret_30d")
    ret_pct = ret.value if ret else None
    last = own.basis.window_end
    if spy is None or spy.basis.window_end is None:
        return SpyCell(ret_pct=ret_pct, last_close=last, gap="no_spy")
    spy_m = metric_of(spy, "ret_30d")
    spy_pct = spy_m.value if spy_m else None
    if last != spy.basis.window_end:
        return SpyCell(ret_pct=ret_pct, spy_pct=spy_pct, last_close=last, gap="ends_differ")
    if ret_pct is None or spy_pct is None:
        return SpyCell(
            ret_pct=ret_pct,
            spy_pct=spy_pct,
            last_close=last,
            gap="no_reading",
            note=(ret.note if ret and ret.value is None else spy_m.note if spy_m else None),
        )
    return SpyCell(
        value=round(ret_pct - spy_pct, 2), ret_pct=ret_pct, spy_pct=spy_pct, last_close=last
    )


def insider_cell(sig: DisplaySignal | None) -> InsiderCell:
    """The ``insider_flow_90d`` member's open-market buys as it screens them (the Cockpit's column). The member
    gives no output when no Form 4 is ingested for the name: ``no_form4`` (a blank, not a zero)."""
    if sig is None:
        return InsiderCell(gap="no_form4")
    buys, usd, buyers = (metric_of(sig, k) for k in ("buy_count", "buy_usd", "distinct_buyers"))
    if buys is None or buys.value is None:
        return InsiderCell(gap="no_form4")
    return InsiderCell(
        buys=int(buys.value),
        usd=usd.value if usd else None,
        buyers=int(buyers.value) if buyers and buyers.value is not None else None,
        note=usd.note if usd else None,
    )


def has_workbench_fact(reads: CompanyReads) -> bool:
    """Does the company hold any Workbench fact (shares, cash and burn, revenue mix, convertible notes)? Those
    exist only where the operator marked a name for data or an automated leg reached it."""
    return bool(reads.shares or reads.cash_burn or reads.mix or reads.dilution)


# --- the companies --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PeerCard(NameCard):
    """A card a company sits on (a ``NameCard``: its ticker there, the Cockpit's ``?name=`` key, and its links
    there, ``None`` = no link) with whether the company is in Context or Bench there and its context role there.
    """

    silent: bool = False
    role: str | None = None


@dataclass(frozen=True)
class PeerCompany:
    """One company, once. ``key`` = its ``security_id``. ``roles`` = the distinct context roles it holds across its
    cards (role order); ``silent_only`` = every one of its rows is Context or Bench (it never counts toward a call)
    and ``rows`` = its roster rows in view. ``workbench_facts`` = it holds a Workbench fact. ``flag`` = the reason
    its price readings are withheld."""

    key: str
    security_id: UUID
    ticker: str
    name: str | None
    business_type: str | None
    supersector: str | None
    royalty: bool
    cards: tuple[PeerCard, ...]
    roles: tuple[str, ...]
    silent_only: bool
    rows: int
    workbench_facts: bool
    flag: str | None
    cap: CapCell
    revenue: RevenueCell
    yoy: YoyCell
    runway: RunwayCell
    overhang: Cell
    purity: PurityCell
    off_high: Cell
    sma50: Cell
    sma200: Cell
    rvol20: Cell
    adv: Cell
    vs_spy: SpyCell
    insider: InsiderCell


def _cards_of(rows: Sequence[Membership]) -> tuple[PeerCard, ...]:
    by_card: dict[UUID, list[Membership]] = {}
    for m in rows:
        by_card.setdefault(m.thesis_id, []).append(m)
    chips = [
        PeerCard(
            thesis_id=tid,
            thesis_name=ms[0].thesis_name,
            ticker=ms[0].ticker,
            links=tuple(dict.fromkeys(m.segment for m in ms)),
            silent=any(m.silent for m in ms),
            role=next((m.context_role for m in ms if m.context_role), None),
        )
        for tid, ms in by_card.items()
    ]
    chips.sort(key=lambda c: (c.thesis_name.casefold(), str(c.thesis_id)))
    return tuple(chips)


def _roles_of(cards: Sequence[PeerCard]) -> tuple[str, ...]:
    held = {c.role for c in cards if c.role}
    known = [r for r in ROLE_ORDER if r in held]
    return tuple(
        known + sorted(held - set(ROLE_ORDER))
    )  # a role the order does not know: listed anyway


def company_row(
    sid: UUID,
    rows: Sequence[Membership],
    identity: Identity | None,
    reads: CompanyReads | None,
    spy: DisplaySignal | None,
    asof: date,
) -> PeerCompany:
    """One company from its roster rows, its identity and its readings. A company with no readings at all (its
    read never ran) is every cell blank for want of data, never a made-up number."""
    cards = _cards_of(rows)
    r = reads or CompanyReads()
    revenue, yoy = revenue_cells(r.revenue)
    flag = r.flag
    return PeerCompany(
        key=str(sid),
        security_id=sid,
        ticker=display_ticker(cards),
        name=identity.name if identity else None,
        business_type=identity.business_type if identity else None,
        supersector=identity.supersector if identity else None,
        royalty=identity.royalty if identity else False,
        cards=cards,
        roles=_roles_of(cards),
        silent_only=all(m.silent for m in rows),
        rows=len(rows),
        workbench_facts=has_workbench_fact(r),
        flag=flag,
        cap=cap_cell(r),
        revenue=revenue,
        yoy=yoy,
        runway=runway_cell(r.cash_burn),
        overhang=overhang_cell(r.dilution, asof),
        purity=purity_cell(r.mix),
        off_high=metric_cell(r.range_52w, "pct_off_52w_high", flag),
        sma50=metric_cell(r.sma, "pct_vs_fast", flag),
        sma200=metric_cell(r.sma, "pct_vs_slow", flag),
        rvol20=metric_cell(r.rvol, "rvol20", flag),
        adv=metric_cell(r.volume, "adv_usd_20d", flag),
        vs_spy=spy_cell(r.returns, spy, flag),
        insider=insider_cell(r.insider),
    )


# --- the scope: who is in it ----------------------------------------------------------------------------------


def _key_of(value: str | None) -> str:
    return value if value else UNCLASSIFIED


def _by_company(rows: Iterable[Membership]) -> dict[UUID, list[Membership]]:
    out: dict[UUID, list[Membership]] = {}
    for m in rows:
        if m.security_id is not None:
            out.setdefault(m.security_id, []).append(m)
    return out


def scope_rows(base: ScopeBase, scope: ScopeKind, key: str | None) -> list[Membership]:
    """The resolved roster rows of the companies in the scope: a leaf's or supersector's companies on ALL their
    counted cards; the picked card's own rows; every resolved row for ``all``."""
    resolved = [m for m in base.memberships if m.security_id is not None]
    if scope == "all":
        return resolved
    if scope == "card":
        return [m for m in resolved if str(m.thesis_id) == key]
    pick = "business_type" if scope == "business_type" else "supersector"
    want = {
        sid
        for sid in {m.security_id for m in resolved}
        if sid is not None and _key_of(getattr(base.identity.get(sid), pick, None)) == key
    }
    return [m for m in resolved if m.security_id in want]


def scope_sids(base: ScopeBase, scope: ScopeKind, key: str | None) -> list[UUID]:
    """The companies in the scope, once each, in a stable order (the heavy read's list)."""
    return sorted({m.security_id for m in scope_rows(base, scope, key) if m.security_id}, key=str)


def unresolved_rows(base: ScopeBase, scope: ScopeKind, key: str | None) -> list[Membership]:
    """The roster rows with no resolved security that the scope would have shown: the picked card's, else every
    counted card's (a row with no security has no business type, so no scope can claim it). Counted and listed on
    the page, never a row (invariant #9)."""
    rows = [m for m in base.memberships if m.security_id is None]
    if scope == "card":
        rows = [m for m in rows if str(m.thesis_id) == key]
    return rows


# --- the scope lists (the pickers) -----------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyOption:
    """A business-type leaf or a supersector present on the rosters, with its distinct companies."""

    key: str
    companies: int


@dataclass(frozen=True)
class CardOption:
    thesis_id: UUID
    name: str
    links: int  # real links today (Context and Bench not counted)
    companies: int  # distinct resolved companies on the roster at the as-of


@dataclass(frozen=True)
class Scopes:
    """What can be picked at the as-of. ``default_scope`` / ``default_key`` = the business-type leaf to open on:
    the first with two or more companies (the lists are biggest first, Unclassified last); ``None`` with no
    company at all. ``companies`` = distinct resolved companies on the counted cards."""

    asof: date
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    companies: int
    unresolved_rows: int
    business_types: tuple[KeyOption, ...]
    supersectors: tuple[KeyOption, ...]
    cards: tuple[CardOption, ...]
    default_scope: ScopeKind | None
    default_key: str | None


def _options(counts: Mapping[str, int]) -> tuple[KeyOption, ...]:
    """Biggest first, then A to Z by key; Unclassified last, whatever its size (it is the tail, not a pick)."""
    items = sorted(counts.items(), key=lambda kv: (kv[0] == UNCLASSIFIED, -kv[1], kv[0]))
    return tuple(KeyOption(k, n) for k, n in items)


def scopes(base: ScopeBase, asof: date) -> Scopes:
    """The pickers: the leaves and supersectors present on the counted rosters (each company counted ONCE, by its
    master identity), the counted cards A to Z with their companies, and the leaf the page opens on.
    """
    by_company = _by_company(base.memberships)
    leaf: Counter[str] = Counter()
    sector: Counter[str] = Counter()
    for sid in by_company:
        ident = base.identity.get(sid)
        leaf[_key_of(ident.business_type if ident else None)] += 1
        sector[_key_of(ident.supersector if ident else None)] += 1
    per_card: dict[UUID, set[UUID]] = {}
    for m in base.memberships:
        if m.security_id is not None:
            per_card.setdefault(m.thesis_id, set()).add(m.security_id)
    cards = sorted(
        (
            CardOption(
                c.thesis_id,
                c.thesis_name,
                link_count(c.segments),
                len(per_card.get(c.thesis_id, ())),
            )
            for c in base.cards
        ),
        key=lambda o: (o.name.casefold(), str(o.thesis_id)),
    )
    types = _options(leaf)
    default = next((o for o in types if o.companies >= 2 and o.key != UNCLASSIFIED), None)
    default = (
        default
        or next((o for o in types if o.key != UNCLASSIFIED), None)
        or (types[0] if types else None)
    )
    return Scopes(
        asof=asof,
        live_cards=base.live_cards,
        cards_counted=len(base.cards),
        cards_not_yet_created=base.cards_not_yet_created,
        roster_fallback_cards=base.roster_fallback_cards,
        companies=len(by_company),
        unresolved_rows=sum(1 for m in base.memberships if m.security_id is None),
        business_types=types,
        supersectors=_options(sector),
        cards=tuple(cards),
        default_scope="business_type" if default else None,
        default_key=default.key if default else None,
    )


def scope_present(found: Scopes, scope: ScopeKind, key: str | None) -> bool:
    """Is the scope pickable at the as-of? (The router's 404 for a key that is not.) ``all`` always is."""
    if scope == "all":
        return True
    if scope == "business_type":
        return any(o.key == key for o in found.business_types)
    if scope == "supersector":
        return any(o.key == key for o in found.supersectors)
    return any(str(o.thesis_id) == key for o in found.cards)


# --- the groups and what they rest on --------------------------------------------------------------------------


@dataclass(frozen=True)
class Median:
    """A median and the number of names it rests on (``value`` None with ``n`` 0)."""

    value: float | None
    n: int


def _median(values: Sequence[float], digits: int | None = None) -> Median:
    if not values:
        return Median(None, 0)
    m = float(median(values))
    return Median(round(m, digits) if digits is not None else m, len(values))


@dataclass(frozen=True)
class GroupStats:
    """What a group row says, each figure with its n: ``names``; the median approximate cap; ``above_50d`` of
    ``priced_50d`` (the names with a 50-day reading); the median 30-day return minus SPY's; the median YoY; the
    names with a runway figure (months or cash-positive). Medians only, equal weight, no score."""

    names: int
    cap: Median
    above_50d: int
    priced_50d: int
    vs_spy: Median
    yoy: Median
    runway: int


def group_stats(companies: Sequence[PeerCompany]) -> GroupStats:
    sma = [c.sma50.value for c in companies if c.sma50.gap is None and c.sma50.value is not None]
    return GroupStats(
        names=len(companies),
        cap=_median([c.cap.value for c in companies if c.cap.value is not None]),
        above_50d=sum(1 for v in sma if v > 0),
        priced_50d=len(sma),
        vs_spy=_median([c.vs_spy.value for c in companies if c.vs_spy.value is not None], 2),
        yoy=_median([c.yoy.value for c in companies if c.yoy.value is not None], 1),
        runway=sum(1 for c in companies if c.runway.gap is None),
    )


@dataclass(frozen=True)
class Group:
    """A group header row and its companies. ``depth`` 0 or 1 (a leaf under a supersector, in the all scope).
    ``members`` = the keys of the companies listed under it (a supersector header of the all scope lists none: its
    leaves do). ``stats`` = every name in the group, ``stats_counting`` = the names that count (not silent-only).
    """

    key: str
    label: str
    kind: GroupKind
    depth: int
    members: tuple[str, ...]
    stats: GroupStats
    stats_counting: GroupStats


def _make_group(
    key: str,
    label: str,
    kind: GroupKind,
    depth: int,
    listed: Sequence[PeerCompany],
    counted: Sequence[PeerCompany],
) -> Group:
    return Group(
        key=key,
        label=label,
        kind=kind,
        depth=depth,
        members=tuple(c.key for c in listed),
        stats=group_stats(counted),
        stats_counting=group_stats([c for c in counted if not c.silent_only]),
    )


def _a_to_z(companies: Iterable[PeerCompany]) -> list[PeerCompany]:
    return sorted(companies, key=lambda c: (c.ticker.casefold(), c.key))


def _tail_last(keys: Iterable[str]) -> list[str]:
    """Keys A to Z, with the visible tail ``other`` after the rest and Unclassified at the very end."""
    return sorted(set(keys), key=lambda k: (k == UNCLASSIFIED, k == OTHER, k))


def _link_order(card: CardRef | None, companies: Sequence[PeerCompany]) -> list[str | None]:
    """The card scope's groups in order: the card's real links in today's chain order, a roster link that is not
    on the card today (first seen), the rows with no link (``None``), then Context and Bench."""
    on_roster = [lk for c in companies for card_ in c.cards for lk in card_.links]
    real = [s for s in dict.fromkeys(card.segments if card else ()) if s not in SILENT_LABELS]
    order: list[str | None] = list(real)
    order += [
        lk
        for lk in dict.fromkeys(on_roster)
        if lk is not None and lk not in SILENT_LABELS and lk not in real
    ]
    if None in on_roster:
        order.append(None)
    order += [s for s in SILENT_LABELS if s in on_roster]
    return order


def build_groups(
    scope: ScopeKind, key: str | None, companies: Sequence[PeerCompany], card: CardRef | None
) -> tuple[Group, ...]:
    """The group rows, in the table's order, each company listed under exactly one."""
    if scope == "business_type":
        return (
            _make_group(
                f"type:{key}", key or "", "business_type", 0, _a_to_z(companies), companies
            ),
        )
    if scope == "supersector":
        return _leaf_groups(companies, depth=0)
    if scope == "all":
        out: list[Group] = []
        for ss in _tail_last(_key_of(c.supersector) for c in companies):
            inside = [c for c in companies if _key_of(c.supersector) == ss]
            out.append(_make_group(f"sector:{ss}", ss, "supersector", 0, (), inside))
            out += _leaf_groups(inside, depth=1)
        return tuple(out)
    # a card: grouped by link, a company under the first of its links in the group order
    order = _link_order(card, companies)
    rank = {link: i for i, link in enumerate(order)}
    placed: dict[str | None, list[PeerCompany]] = {link: [] for link in order}
    for c in companies:
        links = [lk for card_ in c.cards for lk in card_.links]
        first = min(links, key=lambda lk: rank[lk]) if links else None
        placed.setdefault(first, []).append(c)
    groups = []
    for link in order:
        label = NO_LINK if link is None else link
        kind: GroupKind = (
            "no_link"
            if link is None
            else (
                "silent" if link in SILENT_LABELS else "link" if link in _real(card) else "old_link"
            )
        )
        gkey = "no_link" if link is None else f"{kind}:{link}"
        groups.append(_make_group(gkey, label, kind, 0, _a_to_z(placed[link]), placed[link]))
    return tuple(groups)


def _real(card: CardRef | None) -> set[str]:
    return {s for s in (card.segments if card else ()) if s not in SILENT_LABELS}


def _leaf_groups(companies: Sequence[PeerCompany], depth: int) -> tuple[Group, ...]:
    keys = _tail_last(_key_of(c.business_type) for c in companies)
    out = []
    for leaf in keys:
        inside = [c for c in companies if _key_of(c.business_type) == leaf]
        out.append(
            _make_group(f"type:{leaf}", leaf, "business_type", depth, _a_to_z(inside), inside)
        )
    return tuple(out)


# --- the coverage ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BlankCount:
    gap: Gap
    n: int


@dataclass(frozen=True)
class ColumnCoverage:
    """A column's coverage: ``stated`` of ``total`` names, the blanks by reason (most first)."""

    column: Column
    stated: int
    total: int
    blanks: tuple[BlankCount, ...]


def coverage(companies: Sequence[PeerCompany]) -> tuple[ColumnCoverage, ...]:
    """Every column's coverage over the companies. Blank means not stated, never zero: a blank is counted here by
    reason, and a stated cell is exactly one with no gap."""
    out = []
    for col in COLUMNS:
        gaps = Counter(g for c in companies if (g := getattr(c, col).gap) is not None)
        blanks = tuple(
            BlankCount(g, n) for g, n in sorted(gaps.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        out.append(ColumnCoverage(col, len(companies) - sum(gaps.values()), len(companies), blanks))
    return tuple(out)


# --- the table -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnresolvedRow:
    """A roster row with no resolved security: counted and listed, never a row."""

    ticker: str
    thesis_id: UUID
    thesis_name: str


@dataclass(frozen=True)
class SpyRef:
    """SPY's own 30-trading-day return at the as-of, the benchmark every vs-SPY cell subtracts."""

    ret_30d: float | None
    end: date | None


@dataclass(frozen=True)
class PeerTable:
    """One scope's table. ``companies`` = every company in scope once, in the table's default order (group order,
    names A to Z inside); ``groups`` list their keys. ``scope_name`` = the card's name in a card scope. ``scope_cards``
    = the counted cards that hold at least one company in scope; ``rows`` = their roster rows in view;
    ``silent_only`` = companies whose every row is Context or Bench. ``flagged`` = companies in scope whose data
    flags reach their price readings (their price cells are blank, the row stays)."""

    asof: date
    scope: ScopeKind
    key: str | None
    scope_name: str | None
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    scope_cards: int
    rows: int
    companies: tuple[PeerCompany, ...]
    groups: tuple[Group, ...]
    silent_only: int
    unresolved: tuple[UnresolvedRow, ...]
    flagged: tuple[FlaggedCompany, ...]
    coverage: tuple[ColumnCoverage, ...]
    coverage_counting: tuple[ColumnCoverage, ...]
    spy: SpyRef | None


def peer_table(inputs: PeerInputs, scope: ScopeKind, key: str | None, asof: date) -> PeerTable:
    """The scope's whole table: each company once with every cell, the groups and their figures, the coverage."""
    base = inputs.base
    rows = scope_rows(base, scope, key)
    folded = _by_company(rows)
    companies = [
        company_row(sid, ms, base.identity.get(sid), inputs.reads.get(sid), inputs.spy, asof)
        for sid, ms in folded.items()
    ]
    card = next((c for c in base.cards if scope == "card" and str(c.thesis_id) == key), None)
    groups = build_groups(scope, key, companies, card)
    by_key = {c.key: c for c in companies}
    ordered = [by_key[k] for g in groups for k in g.members]
    assert len(ordered) == len(companies)  # each company is listed under exactly one group
    missing = unresolved_rows(base, scope, key)
    spy_ret = metric_of(inputs.spy, "ret_30d") if inputs.spy else None
    return PeerTable(
        asof=asof,
        scope=scope,
        key=key,
        scope_name=card.thesis_name if card else None,
        live_cards=base.live_cards,
        cards_counted=len(base.cards),
        cards_not_yet_created=base.cards_not_yet_created,
        roster_fallback_cards=base.roster_fallback_cards,
        scope_cards=len({m.thesis_id for m in rows}),
        rows=len(rows),
        companies=tuple(ordered),
        groups=groups,
        silent_only=sum(1 for c in companies if c.silent_only),
        unresolved=tuple(UnresolvedRow(m.ticker, m.thesis_id, m.thesis_name) for m in missing),
        flagged=flagged_in_view(rows, {c.security_id: c.flag for c in companies if c.flag}),
        coverage=coverage(companies),
        coverage_counting=coverage([c for c in companies if not c.silent_only]),
        spy=(
            SpyRef(spy_ret.value if spy_ret else None, inputs.spy.basis.window_end)
            if inputs.spy
            else None
        ),
    )
