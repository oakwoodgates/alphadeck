"""Narrative Index (``docs/dashboards/narrative-index.md``): each card's basket as ONE equal-weighted line against
SPY and IWM, with the day the card was created marked. It answers "is the market agreeing with my idea yet, and
since when?". Follows the as-of dial.

PURE: the router reads each counted card's roster as of the as-of (``dashboards/baskets.py``), every company's
closes and the two benchmarks' through the point-in-time view, and hands them to ``index_inputs(...)``, which
builds every card's 1Y series ONCE (cached per as-of, ``dashboards/cache.py``). ``narrative_index(...)`` then
slices and rebases that series per request for the chosen window. The line math (the session calendar, the
alignment, the chain) lives in ``dashboards/series.py``, shared with the Rotation Quadrant. Display only: nothing
here feeds a call, scores an idea or writes anything (rule 1).

The rules, in one place:
- **The basket.** Each counted card's roster AS OF the as-of, looking back (not the basket as it stood on each
  past day). Context and Bench are left out unless ``include_silent``. Each company counts ONCE per card (by
  ``security_id``); a basket row with no resolved security is counted as a name, never priced.
- **A flagged price history.** A company whose data flags reach its price readings at the as-of
  (``dashboards.baskets.price_flags``: the reader the Cockpit's grayed price cells come from) is left out of
  every card's line for the WHOLE 1Y read, so every window: its bars are never read (neither the line nor the
  session calendar), it never contributes a daily return. It still counts in ``names``, never in ``priced``,
  and each card lists it (``flagged``: its ticker and the flags' labels). A bad bar is never chained.
- **The sessions.** One calendar for every card: the dates the SPY / IWM tapes have a bar on, ending at the
  as-of, plus any company bar dated after the benchmarks' last bar (a late benchmark never cuts the lines
  short). With no benchmark tape at all, the union of the counted companies' bar dates. The 1Y window is the
  last ``WINDOWS["1y"]`` sessions (that many daily returns, one more point).
- **The index.** Equal weight, rebalanced daily, from ``BASE`` at the window start: on each session ``t``,
  ``index_t = index_{t-1} × (1 + mean of the daily close-to-close returns of the members with a bar on both
  t-1 and t)``. A member joins once it has two consecutive bars; a gap breaks only its own contribution. A
  session no member can contribute to carries the index flat and is counted. A non-positive close is not a
  bar. The bars are the point-in-time view's, on its split scale (``PointInTimeData.price_history``).
- **The windows.** 1Y (default), 90D, 30D in sessions. A shorter window is the tail of the 1Y series rebased
  to ``BASE``: each daily step depends only on the two sessions it spans, so rebasing IS recomputing from
  ``BASE`` at the window start.
- **The benchmarks.** SPY and IWM through the same chain (one member each), so a benchmark gap carries flat
  exactly as a card's does. A benchmark with no bar in the 1Y sessions is absent, with its reason.
- **Per card.** The window return; it minus SPY's; the return since the card was created, from the close of
  the last session on or before its creation day (market time), only when that day falls inside the window;
  names priced (contributed at least one daily return in the window) / in the basket; sessions carried flat.
  A card with no priced name in the window shows no return ("—"), never a flat 0%.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal
from uuid import UUID

from dashboards.baskets import Membership, counted_rows

# the line math: shared with the Rotation Quadrant (``dashboards/series.py``), and re-exported here under their
# old names
from dashboards.series import BASE as BASE
from dashboards.series import YEAR_SESSIONS
from dashboards.series import Chain as Chain
from dashboards.series import SessionSource as SessionSource
from dashboards.series import aligned as aligned
from dashboards.series import chain as chain
from dashboards.series import session_calendar as session_calendar

Window = Literal["1y", "90d", "30d"]
# trading sessions (daily returns) per window; the 1Y count is the Cockpit's own 1Y window
# (``signals/display/trailing_returns.py``: 252 trading bars)
WINDOWS: dict[Window, int] = {"1y": YEAR_SESSIONS, "90d": 90, "30d": 30}
DEFAULT_WINDOW: Window = "1y"
BENCHMARKS: tuple[str, ...] = ("SPY", "IWM")


# --- the cached 1Y read -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberSeries:
    """One company on a card (deduped by security), or one unresolved basket row (``security_id`` None)."""

    ticker: str
    security_id: UUID | None
    contributed: tuple[int, ...] = ()  # 1Y session indices it contributed a step into
    flag: str | None = None  # why its price history is left out (``price_flags``); None = read


@dataclass(frozen=True)
class CardSeries:
    thesis_id: UUID
    thesis_name: str
    state: str | None  # the latest recorded state at the as-of (None = no recorded call)
    created: date | None  # the market day the card was created
    members: tuple[MemberSeries, ...]  # resolved companies, each once, first-seen order
    unresolved: tuple[str, ...]  # tickers of the rows with no resolved security (deduped)
    silent_left_out: int  # Context / Bench rows left out by the toggle
    chain: Chain


@dataclass(frozen=True)
class BenchmarkSeries:
    symbol: str
    chain: Chain | None  # None = no bar in the 1Y sessions


@dataclass(frozen=True)
class IndexInputs:
    """Everything the heavy read produces: cached per (clock, include_silent, as-of), every window derived."""

    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    include_silent: bool
    sessions: tuple[date, ...]  # the 1Y sessions, oldest first
    session_source: SessionSource
    cards: tuple[CardSeries, ...]  # by name
    benchmarks: tuple[BenchmarkSeries, ...]  # BENCHMARKS order
    companies: int  # distinct resolved companies in view, across the cards
    priced_companies: int  # of those, companies with at least one daily return in the 1Y sessions
    silent_rows_left_out: int
    unresolved_rows: int
    flagged_companies: int  # distinct companies in view left out for a flagged price history


def card_members(
    rows: Sequence[Membership], include_silent: bool
) -> tuple[list[tuple[UUID, str]], tuple[str, ...], int]:
    """One card's rows -> ``(companies, unresolved tickers, silent rows left out)``: each resolved company ONCE
    (first-seen ticker, first-seen order), each unresolved ticker once."""
    counted = counted_rows(rows, include_silent)
    seen: dict[UUID, str] = {}
    for m in counted.rows:
        assert m.security_id is not None
        seen.setdefault(m.security_id, m.ticker)
    unresolved: list[str] = []
    for m in rows:
        if (
            m.security_id is None
            and (include_silent or not m.silent)
            and m.ticker not in unresolved
        ):
            unresolved.append(m.ticker)
    return list(seen.items()), tuple(unresolved), counted.silent_rows_left_out


def index_inputs(
    *,
    cards: Sequence[tuple[UUID, str]],
    memberships: Sequence[Membership],
    closes: Mapping[UUID, Mapping[date, float]],
    benchmarks: Mapping[str, Mapping[date, float]],
    created: Mapping[UUID, date],
    states: Mapping[UUID, str],
    asof: date,
    include_silent: bool,
    live_cards: int,
    cards_not_yet_created: int,
    roster_fallback_cards: int,
    flagged: Mapping[UUID, str] | None = None,
) -> IndexInputs:
    """Every counted card's 1Y series and the benchmarks', over one shared calendar. ``cards`` = the counted
    cards ``(id, name)``; ``closes`` = each company's ``{day: close}`` up to the as-of; ``benchmarks`` = each
    benchmark symbol's (an empty mapping = no tape); ``created`` = each card's creation day (market time);
    ``flagged`` = the companies whose data flags reach their price readings, each with its reason
    (``dashboards.baskets.price_flags``): their closes are never read.
    """
    flags = dict(flagged or {})
    rows_by_card: dict[UUID, list[Membership]] = {tid: [] for tid, _ in cards}
    for m in memberships:
        if m.thesis_id in rows_by_card:
            rows_by_card[m.thesis_id].append(m)
    per_card = {tid: card_members(rows, include_silent) for tid, rows in rows_by_card.items()}
    in_view = {sid for companies, _, _ in per_card.values() for sid, _ in companies}
    read = {sid for sid in in_view if sid not in flags}  # a flagged company's bars are never read

    sessions, source = session_calendar(
        (d for symbol in BENCHMARKS for d in benchmarks.get(symbol, {})),
        (d for sid in read for d in closes.get(sid, {})),
        asof,
    )
    n = len(sessions)
    no_bars: tuple[float | None, ...] = (None,) * n
    aligned_closes = {
        sid: aligned(closes.get(sid, {}), sessions) if sid in read else no_bars for sid in in_view
    }

    out_cards: list[CardSeries] = []
    priced: set[UUID] = set()
    for tid, name in cards:
        companies, unresolved, silent_out = per_card[tid]
        ch = chain([aligned_closes[sid] for sid, _ in companies], n)
        members = tuple(
            MemberSeries(
                ticker=ticker, security_id=sid, contributed=ch.contributed[j], flag=flags.get(sid)
            )
            for j, (sid, ticker) in enumerate(companies)
        )
        priced.update(m.security_id for m in members if m.contributed and m.security_id)
        out_cards.append(
            CardSeries(
                thesis_id=tid,
                thesis_name=name,
                state=states.get(tid),
                created=created.get(tid),
                members=members,
                unresolved=unresolved,
                silent_left_out=silent_out,
                chain=ch,
            )
        )
    out_cards.sort(key=lambda c: (c.thesis_name.casefold(), str(c.thesis_id)))

    bench: list[BenchmarkSeries] = []
    for symbol in BENCHMARKS:
        series = aligned(benchmarks.get(symbol, {}), sessions)
        has_bar = any(c is not None for c in series)
        bench.append(BenchmarkSeries(symbol, chain([series], n) if has_bar else None))

    return IndexInputs(
        live_cards=live_cards,
        cards_counted=len(cards),
        cards_not_yet_created=cards_not_yet_created,
        roster_fallback_cards=roster_fallback_cards,
        include_silent=include_silent,
        sessions=sessions,
        session_source=source,
        cards=tuple(out_cards),
        benchmarks=tuple(bench),
        companies=len(in_view),
        priced_companies=len(priced),
        silent_rows_left_out=sum(s for _, _, s in per_card.values()),
        unresolved_rows=sum(len(u) for _, u, _ in per_card.values()),
        flagged_companies=len(in_view) - len(read),
    )


# --- a window: slice, rebase, the per-card numbers --------------------------------------------------------


def window_start(n_sessions: int, window: Window) -> int:
    """The index of the window's first session in the 1Y sessions (0 when the tape is shorter)."""
    return max(0, n_sessions - 1 - WINDOWS[window])


def rebased(levels: Sequence[float], start: int) -> tuple[float, ...]:
    """The levels from ``start`` on, rebased to ``BASE`` at ``start``."""
    if not levels:
        return ()
    base = levels[start]
    return tuple(v / base * BASE for v in levels[start:])


def pct(end: float, start: float) -> float:
    return round((end / start - 1.0) * 100.0, 2)


def _any_in(indices: Sequence[int], lo: int, hi: int) -> bool:
    """Is any sorted index in ``[lo, hi]``?"""
    i = bisect_left(indices, lo)
    return i < len(indices) and indices[i] <= hi


@dataclass(frozen=True)
class BenchmarkView:
    symbol: str
    available: bool
    reason: str | None  # why the line is absent
    ret: float | None
    points: tuple[float, ...] | None
    sessions_flat: int


@dataclass(frozen=True)
class MemberView:
    ticker: str
    security_id: UUID | None
    sessions: int  # daily returns it contributed in the window
    first: date | None  # the first / last session it contributed in the window
    last: date | None
    flag: str | None = None  # why it is left out (a flagged price history); None = read


@dataclass(frozen=True)
class FlaggedName:
    """A company on a card left out of its line: its price history is flagged at the as-of."""

    ticker: str
    reason: str  # the flags' labels ("mixed-scale price history")


@dataclass(frozen=True)
class CardView:
    thesis_id: UUID
    thesis_name: str
    state: str | None
    created: date | None
    created_in_window: bool
    created_base: date | None  # the session whose close "since creation" runs from
    names: int  # companies on the card (each once) + unresolved rows; flagged names included
    priced: int  # of those, names with a daily return in the window (never a flagged one)
    unresolved: int
    silent_left_out: int
    ret: float | None  # the window return (%); None when no name is priced in the window
    vs_spy: float | None  # ret minus SPY's window return
    since_created: float | None  # from the creation base to the as-of; None outside the window
    sessions_flat: int  # window sessions no member could contribute to
    points: tuple[float, ...]  # the rebased index, one per window session
    flagged: tuple[FlaggedName, ...]  # names left out for a flagged price history, card order


@dataclass(frozen=True)
class NarrativeIndex:
    asof: date
    window: Window
    include_silent: bool
    # daily returns in the window (fewer than its nominal count when the tape is short)
    sessions: int
    dates: tuple[date, ...]  # the window's sessions, oldest first
    session_source: SessionSource
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    companies: int
    priced_companies: int
    silent_rows_left_out: int
    unresolved_rows: int
    flagged_companies: int
    benchmarks: tuple[BenchmarkView, ...]
    cards: tuple[CardView, ...]


def _benchmark_view(b: BenchmarkSeries, start: int, end: int) -> BenchmarkView:
    if b.chain is None:
        return BenchmarkView(b.symbol, False, f"no {b.symbol} tape at this as-of", None, None, 0)
    steps = b.chain.contributors[start + 1 : end + 1]
    if not any(steps):
        reason = f"no {b.symbol} bars in this window"
        return BenchmarkView(b.symbol, False, reason, None, None, len(steps))
    pts = rebased(b.chain.levels, start)
    return BenchmarkView(
        b.symbol, True, None, pct(pts[-1], pts[0]), pts, sum(1 for c in steps if c == 0)
    )


def creation_base(created: date | None, dates: Sequence[date]) -> int | None:
    """The window index of the last session on or before ``created`` (``None`` when the card was created
    before the window's first session, or the day is unknown)."""
    if created is None or not dates or created < dates[0]:
        return None
    return bisect_right(dates, created) - 1


def card_view(
    c: CardSeries, sessions: Sequence[date], start: int, spy_ret: float | None
) -> CardView:
    end = len(sessions) - 1
    dates = sessions[start:]
    steps = c.chain.contributors[start + 1 : end + 1]
    priced = sum(1 for m in c.members if _any_in(m.contributed, start + 1, end))
    pts = rebased(c.chain.levels, start)
    ret = pct(pts[-1], pts[0]) if priced and pts else None
    base = creation_base(c.created, dates)
    since: float | None = None
    if base is not None and any(c.chain.contributors[start + base + 1 : end + 1]):
        since = pct(pts[-1], pts[base])
    return CardView(
        thesis_id=c.thesis_id,
        thesis_name=c.thesis_name,
        state=c.state,
        created=c.created,
        created_in_window=base is not None,
        created_base=dates[base] if base is not None else None,
        names=len(c.members) + len(c.unresolved),
        priced=priced,
        unresolved=len(c.unresolved),
        silent_left_out=c.silent_left_out,
        ret=ret,
        vs_spy=None if ret is None or spy_ret is None else round(ret - spy_ret, 2),
        since_created=since,
        sessions_flat=sum(1 for n in steps if n == 0),
        points=pts,
        flagged=tuple(FlaggedName(m.ticker, m.flag) for m in c.members if m.flag is not None),
    )


def narrative_index(inputs: IndexInputs, *, asof: date, window: Window) -> NarrativeIndex:
    """The page for one window: every card's index sliced to the window and rebased to ``BASE``, with the
    benchmarks over the same sessions and the per-card numbers."""
    n = len(inputs.sessions)
    start = window_start(n, window)
    end = n - 1
    benches = tuple(_benchmark_view(b, start, end) for b in inputs.benchmarks)
    spy = next((b.ret for b in benches if b.symbol == "SPY"), None)
    return NarrativeIndex(
        asof=asof,
        window=window,
        include_silent=inputs.include_silent,
        sessions=max(0, end - start),
        dates=inputs.sessions[start:],
        session_source=inputs.session_source,
        live_cards=inputs.live_cards,
        cards_counted=inputs.cards_counted,
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        companies=inputs.companies,
        priced_companies=inputs.priced_companies,
        silent_rows_left_out=inputs.silent_rows_left_out,
        unresolved_rows=inputs.unresolved_rows,
        flagged_companies=inputs.flagged_companies,
        benchmarks=benches,
        cards=tuple(card_view(c, inputs.sessions, start, spy) for c in inputs.cards),
    )


@dataclass(frozen=True)
class CardDetail:
    """One card enlarged: its window view, the names contributing per session and each name's window."""

    page: NarrativeIndex  # the window's shared parts (its ``cards`` holds just this card)
    contributors: tuple[int, ...]  # names contributing the step into each window session (first: 0)
    members: tuple[MemberView, ...]


def card_detail(
    inputs: IndexInputs, thesis_id: UUID, *, asof: date, window: Window
) -> CardDetail | None:
    """The enlarged view of one card (``None`` when the card is not counted at the as-of)."""
    card = next((c for c in inputs.cards if c.thesis_id == thesis_id), None)
    if card is None:
        return None
    page = narrative_index(replace(inputs, cards=(card,)), asof=asof, window=window)
    n = len(inputs.sessions)
    start, end = window_start(n, window), n - 1
    members: list[MemberView] = []
    for m in card.members:
        inside = [t for t in m.contributed if start + 1 <= t <= end]
        members.append(
            MemberView(
                ticker=m.ticker,
                security_id=m.security_id,
                sessions=len(inside),
                first=inputs.sessions[inside[0]] if inside else None,
                last=inputs.sessions[inside[-1]] if inside else None,
                flag=m.flag,
            )
        )
    members += [MemberView(t, None, 0, None, None) for t in card.unresolved]
    contributors = (0,) + card.chain.contributors[start + 1 : end + 1] if n else ()
    return CardDetail(page=page, contributors=contributors, members=tuple(members))
