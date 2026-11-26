"""Fill a session the price vendor served no close for, from Massive.com's raw bar (W6-3b, the SNDK fix).

THE DEFECT. Yahoo serves a NULL close for sessions around a split (MEASURED 2026-11-25: every post-split session
of SNDK's 8-for-1 of 11-20, SCNI's 1-for-4 of 11-19 and SOS; the first post-split session of DFSC, EMBJ, SGRX,
COSM, KALA, PARA). ``parse_yahoo_chart`` drops a null close, the price leg stores only what the vendor serves,
and nothing else prices the name: the tape stops, every price detector on it goes dark and the stale monitor
advises a rename that cannot help. Massive.com's raw (as-traded) bar for the same session is one read away: the
nightly's grouped day holds tonight's session (W6-3a, no request); an older one costs one per-ticker range read.

THE RULE (``plan_fill``, PURE: no I/O, no clock). A session ``s`` is filled only when ALL hold:

1. ``s`` is a candidate session (``sessions``: scheduled days up to tonight's SETTLED session — D2, never a bar
   whose session has not closed and settled; ``settled_session``), after the tape's first bar (a hole, not the
   tape's start);
2. the vendor's settled pull has no bar for ``s`` and the store has none — a vendor bar ALWAYS wins (GNS's
   zero-volume halt placeholders stay) and a stored bar is NEVER overwritten;
3. Massive.com has a bar for ``s`` with volume > 0 (a traded session; absent or 0 = no trade);
4. ``s`` is on or after the NEWEST split in force's first session ``f`` (``domain.splits.applicable``; none in
   force = any ``s``): there the raw bar is already on the split list's scale (``domain.splits.scale_after`` is 1,
   computed and recorded as ``factor``), so nothing is rescaled. A hole BEFORE ``f`` is never filled (listed);
5. never on or after the first session of a PENDING split (``domain.splits.pending``: needs review, or announced
   and not yet in force): a split the operator has not settled is applied to nothing, and a raw post-split bar
   beside an unsettled scale would mix the tape (DFSC and EMBJ on prod wait for the steward);
6. under ``scope = "split"`` (the default, decision W6-b): a split is in force and ``s`` is within
   ``split_window`` sessions of ``f`` (``f`` itself the first), OR the vendor has served no bar at all on or after
   ``f`` (SNDK while Yahoo keeps nulling, however long). ``"all"`` drops this clause (every traded gap, the OTC
   late closes included); ``"off"`` fills nothing.

A refused gap is listed with its reason (``FillPlan.refused``: the CLI prints them as "not fillable by rule"),
never silent. A filled bar is stored like any vendor bar (``ingest_security``), its provenance recorded in
``price_bar_fill`` (migration 0217), and it is never split evidence (``pipeline.detect_splits.tape_at``).

``GapSource`` is the pass's context: the grouped day fetched once before the per-thesis loop, the source for
the range reads, the dials, and the per-night cap on range reads (``max_names``: past it a name is DEFERRED and
counted; tonight's session still fills from the grouped day). Its reads go through the injected source only.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any, Protocol

from domain.market_time import market_tz
from domain.settings import get_settings
from domain.splits import ListedSplit, ratio_label, scale_after, session_distance
from domain.trading_days import is_scheduled_day
from ingest import CacheMiss
from ingest.massive_bars import (
    GroupedDay,
    MassiveBar,
    MassiveBarsError,
    NotServedYet,
    default_bar_source,
    massive_symbol,
)
from ingest.prices.settle import is_settled

SCOPE_OFF = "off"
SCOPE_SPLIT = "split"
SCOPE_ALL = "all"
SCOPES = (SCOPE_OFF, SCOPE_SPLIT, SCOPE_ALL)

SOURCE_MASSIVE = "massive"  # ``price_bar_fill.source`` (the CHECK admits this one value)

# why a gap WAS filled (``price_bar_fill.reason``)
REASON_SPLIT_WINDOW = (
    "split_window"  # within ``split_window`` sessions of the newest split's first session
)
REASON_VENDOR_SILENT = (
    "vendor_silent"  # the vendor has served no bar on or after that first session
)
REASON_ALL = "all"  # scope ``all``

# why a gap was NOT filled (listed, never silent)
WHY_BEFORE_SPLIT = "before_split"  # before the newest split in force's first session
WHY_PENDING_SPLIT = "pending_split"  # on/after a pending split's first session
WHY_NO_SPLIT = "no_split"  # scope ``split`` and no split in force
WHY_OUTSIDE_WINDOW = "outside_window"  # scope ``split``, past the window, the vendor serving again
WHY_NO_TRADE = "no_trade"  # Massive.com has no bar, or volume 0
WHY_NOT_READ = (
    "not_read"  # Massive.com was not read for it (deferred past the cap, or the read failed)
)


@dataclass(frozen=True)
class FillBar:
    """One planned fill: the bar as stored (``close`` / ``volume`` = the raw values x ``factor``, which is 1 by
    construction) and its provenance."""

    d: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal
    raw_close: Decimal
    raw_volume: Decimal
    vendor_symbol: str
    factor: Fraction
    reason: str

    def bar(self) -> dict[str, Any]:
        """The fill as a vendor bar row (``d`` / OHLCV), the shape every price writer takes."""
        return {
            "d": self.d,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class Refused:
    """A gap the rule did not fill, and why (``WHY_*``)."""

    d: date
    why: str


@dataclass
class FillPlan:
    """``plan_fill``'s answer. ``split`` = the newest split in force (for the report line); ``deferred`` /
    ``note`` are the pass context's (``GapSource.plan``): a range read past the cap, or one that failed.
    """

    fills: list[FillBar] = field(default_factory=list)
    refused: list[Refused] = field(default_factory=list)
    split: ListedSplit | None = None
    deferred: bool = False
    note: str | None = None

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(f.d for f in self.fills)

    @property
    def split_label(self) -> str | None:
        s = self.split
        if s is None:
            return None
        return f"{ratio_label(s.ratio_from, s.ratio_to)} of {s.effective_date.isoformat()}"


# --- the sessions -----------------------------------------------------------------------------------------


def settled_session(now: datetime) -> date:
    """The newest session whose bar is a settled close at ``now`` (AWARE): tonight's once the settle time has
    passed (D2), else the session before."""
    d = now.astimezone(market_tz()).date()
    while not (is_scheduled_day(d) and is_settled(d, now=now)):
        d -= timedelta(days=1)
    return d


def recent_sessions(through: date, n: int) -> list[date]:
    """The newest ``n`` sessions up to ``through`` (inclusive when it is one), oldest first."""
    out: list[date] = []
    d = through
    while len(out) < max(0, n):
        if is_scheduled_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def sessions_between(start: date, end: date) -> list[date]:
    """Every session ``start..end`` (inclusive), oldest first."""
    out: list[date] = []
    d = start
    while d <= end:
        if is_scheduled_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


# --- the rule ---------------------------------------------------------------------------------------------


def gaps(
    fresh: Sequence[Mapping], stored: Mapping[date, Any], sessions: Sequence[date]
) -> list[date]:
    """The candidate sessions with no vendor bar and no stored bar, after the tape's first bar, oldest first."""
    have = {b["d"] for b in fresh} | set(stored)
    if not have:
        return []
    first = min(have)
    return [s for s in sorted(set(sessions)) if is_scheduled_day(s) and s > first and s not in have]


def _newest_first_session(in_force: Sequence[ListedSplit]) -> date | None:
    return max((s.effective_date for s in in_force), default=None)


def _rule(
    s: date,
    *,
    f: date | None,
    pending: Sequence[ListedSplit],
    vendor_since_f: bool,
    scope: str,
    split_window: int,
) -> tuple[str | None, str | None]:
    """``(reason, None)`` when rules 4-6 allow filling ``s``, else ``(None, why)``."""
    if f is not None and s < f:
        return None, WHY_BEFORE_SPLIT
    if any(p.effective_date <= s for p in pending):
        return None, WHY_PENDING_SPLIT
    if scope == SCOPE_ALL:
        return REASON_ALL, None
    if f is None:
        return None, WHY_NO_SPLIT
    if session_distance(f, s) < split_window:
        return REASON_SPLIT_WINDOW, None
    if not vendor_since_f:
        return REASON_VENDOR_SILENT, None
    return None, WHY_OUTSIDE_WINDOW


def wanted(
    fresh: Sequence[Mapping],
    stored: Mapping[date, Any],
    in_force: Sequence[ListedSplit],
    *,
    sessions: Sequence[date],
    scope: str,
    split_window: int,
    pending: Sequence[ListedSplit] = (),
) -> list[date]:
    """The gaps rules 1-2 and 4-6 would fill if Massive.com traded them: the sessions worth reading it for."""
    if scope not in (SCOPE_SPLIT, SCOPE_ALL):
        return []
    f = _newest_first_session(in_force)
    vendor_since_f = f is not None and any(b["d"] >= f for b in fresh)
    return [
        s
        for s in gaps(fresh, stored, sessions)
        if _rule(
            s,
            f=f,
            pending=pending,
            vendor_since_f=vendor_since_f,
            scope=scope,
            split_window=split_window,
        )[0]
        is not None
    ]


def plan_fill(
    fresh: Sequence[Mapping],
    stored: Mapping[date, Any],
    in_force: Sequence[ListedSplit],
    *,
    sessions: Sequence[date],
    massive: Mapping[date, MassiveBar],
    scope: str,
    split_window: int,
    symbol: str,
    pending: Sequence[ListedSplit] = (),
    unread: Collection[date] = (),
) -> FillPlan:
    """The fills for one name (the module docstring's rule). ``fresh`` = the vendor's settled, session-dated
    pull; ``stored`` = the newest stored version per date; ``in_force`` / ``pending`` = the split list read on the
    pass's market day; ``massive`` = Massive.com's raw bars by session (``symbol`` its spelling); ``unread`` = the
    sessions Massive.com was not read for (listed ``not_read``, never ``no_trade``)."""
    out = FillPlan(split=max(in_force, key=lambda s: s.effective_date, default=None))
    if scope not in (SCOPE_SPLIT, SCOPE_ALL):
        return out
    f = _newest_first_session(in_force)
    vendor_since_f = f is not None and any(b["d"] >= f for b in fresh)
    through = max(sessions) if sessions else None
    for s in gaps(fresh, stored, sessions):
        reason, why = _rule(
            s,
            f=f,
            pending=pending,
            vendor_since_f=vendor_since_f,
            scope=scope,
            split_window=split_window,
        )
        if reason is None:
            out.refused.append(Refused(s, why or WHY_NO_TRADE))
            continue
        bar = massive.get(s)
        if bar is None:
            out.refused.append(Refused(s, WHY_NOT_READ if s in unread else WHY_NO_TRADE))
            continue
        if bar.volume <= 0:
            out.refused.append(Refused(s, WHY_NO_TRADE))
            continue
        k = scale_after(in_force, s, through=through or s)
        if k != 1:  # unreachable on/after the newest split's first session; never a rescaled fill
            out.refused.append(Refused(s, WHY_BEFORE_SPLIT))
            continue
        out.fills.append(
            FillBar(
                d=s,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                raw_close=bar.close,
                raw_volume=bar.volume,
                vendor_symbol=symbol,
                factor=k,
                reason=reason,
            )
        )
    return out


# --- the pass's context -----------------------------------------------------------------------------------


class RangeSource(Protocol):
    """The range read's seam (``ingest.massive_bars.MassiveBarSource.bars``)."""

    def bars(
        self, symbol: str, start: date, end: date, *, allow_live: bool
    ) -> Mapping[date, MassiveBar]: ...


@dataclass(frozen=True)
class MassiveRead:
    """Massive.com's bars for the sessions asked: ``unread`` = the ones not read (deferred, failed, no source)."""

    bars: dict[date, MassiveBar]
    unread: tuple[date, ...] = ()
    deferred: bool = False
    note: str | None = None


class GapSource:
    """The pass's fill context (built once, before the per-thesis loop): ``tonight`` = the grouped day W6-3a
    fetched (its session costs no request), ``source`` = the range reads for older sessions, at most
    ``max_names`` a pass (past it a name is deferred: ``deferred`` names them). ``since`` (the CLI) widens the
    candidate sessions from the newest ``split_sessions`` to every session since that day."""

    def __init__(
        self,
        source: RangeSource | None,
        *,
        tonight: GroupedDay | None = None,
        scope: str = SCOPE_SPLIT,
        split_sessions: int = 20,
        max_names: int = 10,
        allow_live: bool = True,
        since: date | None = None,
    ) -> None:
        if scope not in SCOPES:
            raise ValueError(f"price gap fill scope must be one of {SCOPES}, not {scope!r}")
        self.source = source
        self.tonight = tonight
        self.scope = scope
        self.split_sessions = split_sessions
        self.max_names = max_names
        self.allow_live = allow_live
        self.since = since
        self.range_reads = 0
        self.deferred: list[str] = []

    def sessions(self, now: datetime) -> list[date]:
        through = settled_session(now)
        if self.since is not None:
            return sessions_between(self.since, through)
        return recent_sessions(through, self.split_sessions)

    def read(self, symbol: str | None, dates: Sequence[date]) -> MassiveRead:
        """Massive.com's bars for ``dates``: tonight's session from the grouped day, the rest in ONE range read
        (``min..max``), or deferred past the cap. A failed read is a note, never an exception."""
        if not dates:
            return MassiveRead({})
        if symbol is None:
            return MassiveRead({}, tuple(dates), note="no symbol to read Massive.com under")
        out: dict[date, MassiveBar] = {}
        rest = list(dates)
        if self.tonight is not None and self.tonight.session in rest:
            bar = self.tonight.get(symbol)
            if bar is not None:
                out[self.tonight.session] = bar
            rest = [d for d in rest if d != self.tonight.session]
        if not rest:
            return MassiveRead(out)
        if self.source is None:
            return MassiveRead(out, tuple(rest), note="no Massive.com range source")
        if self.range_reads >= self.max_names:
            self.deferred.append(symbol)
            return MassiveRead(out, tuple(rest), deferred=True)
        self.range_reads += 1
        try:
            got = self.source.bars(symbol, min(rest), max(rest), allow_live=self.allow_live)
        except (NotServedYet, CacheMiss, MassiveBarsError, ValueError) as e:
            return MassiveRead(out, tuple(rest), note=f"{type(e).__name__}: {e}"[:200])
        keep = set(rest)
        out.update({d: b for d, b in got.items() if d in keep})
        return MassiveRead(out)

    def plan(
        self,
        fresh: Sequence[Mapping],
        stored: Mapping[date, Any],
        in_force: Sequence[ListedSplit],
        *,
        symbol: str | None,
        now: datetime,
        pending: Sequence[ListedSplit] = (),
    ) -> FillPlan:
        """One name's fills for a pass at ``now``: the rule's gaps, Massive.com read only for those."""
        msym = massive_symbol(symbol)
        sessions = self.sessions(now)
        want = wanted(
            fresh,
            stored,
            in_force,
            sessions=sessions,
            scope=self.scope,
            split_window=self.split_sessions,
            pending=pending,
        )
        read = self.read(msym, want)
        plan = plan_fill(
            fresh,
            stored,
            in_force,
            sessions=sessions,
            massive=read.bars,
            scope=self.scope,
            split_window=self.split_sessions,
            symbol=msym or "",
            pending=pending,
            unread=read.unread,
        )
        plan.deferred, plan.note = read.deferred, read.note
        return plan


def default_gap_source(*, tonight: GroupedDay | None, allow_live: bool) -> GapSource | None:
    """The nightly's context from the settings, or ``None`` when the fill is off (``price_gap_fill = "off"``) or
    there is no ``MASSIVE_API_KEY``. Raises on a settings fault: the caller is fail-open."""
    s = get_settings()
    scope = s.price_gap_fill
    if scope == SCOPE_OFF:
        return None
    source = default_bar_source()
    if source is None:
        return None
    return GapSource(
        source,
        tonight=tonight,
        scope=scope,
        split_sessions=s.price_gap_fill_split_sessions,
        max_names=s.price_gap_fill_max_names,
        allow_live=allow_live,
    )
