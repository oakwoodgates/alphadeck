"""The equal-weighted line math the price dashboards share (``docs/DASHBOARDS.md``): one session calendar, a
company's closes aligned on it, and the chain that turns a set of members' closes into one equal-weighted,
daily-rebalanced line from ``BASE``. Built with the Narrative Index, shared since the Rotation Quadrant, so both
draw a basket's line the same way (never a second copy of the rule). ``dashboards.narrative_index`` re-exports
these under their old names.

The rules, in one place:
- **The sessions.** The benchmarks' bar dates ending at the as-of, plus any company bar dated after the
  benchmarks' last bar (a late benchmark never cuts the lines short). With no benchmark tape at all, the union
  of the companies' bar dates. The last ``sessions + 1`` dates (``sessions`` daily returns, one more point).
- **A bar.** A non-positive close is not a bar.
- **The line.** Equal weight, rebalanced daily, from ``BASE`` at session 0: on each session ``t``,
  ``level_t = level_{t-1} × (1 + mean of the daily close-to-close returns of the members with a bar on both
  t-1 and t)``. A member joins once it has two consecutive bars; a gap breaks only its own contribution. A
  session no member can contribute to carries the line flat (``contributors`` 0 there).
- **The line's start** (``line_start``): the session before the first one any member contributed a daily
  return to. Before it the line is only carried flat, which is not history. Moved here from the Rotation
  Quadrant (which re-exports it) when the Chain Wave needed it too.

PURE: no reads, no clock; the callers hand in the point-in-time bars (``PointInTimeData.price_history`` /
``benchmark_prices``, read with ``serve_view(asof)``). Display only (rule 1).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

# a year of trading sessions (daily returns): the Cockpit's own 1Y window
# (``signals/display/trailing_returns.py``: 252 trading bars)
YEAR_SESSIONS = 252
BASE = 100.0

SessionSource = Literal["benchmarks", "companies", "none"]


# --- the session calendar ---------------------------------------------------------------------------------


def session_calendar(
    benchmark_dates: Iterable[date],
    company_dates: Iterable[date],
    asof: date,
    sessions: int = YEAR_SESSIONS,
) -> tuple[tuple[date, ...], SessionSource]:
    """The shared sessions, ending at ``asof``: the last ``sessions + 1`` dates (``sessions`` daily returns).
    The benchmarks' bar dates plus any company bar after their last one; with no benchmark bar, the companies'.
    """
    bench = {d for d in benchmark_dates if d <= asof}
    companies = {d for d in company_dates if d <= asof}
    if bench:
        last = max(bench)
        days, source = bench | {d for d in companies if d > last}, "benchmarks"
    elif companies:
        days, source = companies, "companies"
    else:
        return (), "none"
    return tuple(sorted(days)[-(sessions + 1) :]), source  # type: ignore[return-value]


def aligned(closes: Mapping[date, float], sessions: Sequence[date]) -> tuple[float | None, ...]:
    """A company's closes on the shared sessions (``None`` = no bar that session; a non-positive close is no
    bar)."""
    out: list[float | None] = []
    for d in sessions:
        c = closes.get(d)
        out.append(c if c is not None and c > 0 else None)
    return tuple(out)


# --- the chain --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Chain:
    """An equal-weighted, daily-rebalanced index over the sessions, from ``BASE`` at session 0."""

    levels: tuple[float, ...]  # one per session
    contributors: tuple[int, ...]  # members contributing the step INTO each session (session 0: 0)
    contributed: tuple[tuple[int, ...], ...]  # per member, the sessions it contributed a step into


def chain(members: Sequence[Sequence[float | None]], n_sessions: int) -> Chain:
    """The index over ``n_sessions`` sessions: ``members[j][t]`` is member ``j``'s close on session ``t``.
    Session ``t`` steps by the mean daily return of the members with a close on both ``t-1`` and ``t``; with
    none, it carries flat."""
    if n_sessions == 0:
        return Chain((), (), tuple(() for _ in members))
    levels = [BASE]
    counts = [0]
    contributed: list[list[int]] = [[] for _ in members]
    for t in range(1, n_sessions):
        rets: list[float] = []
        for j, closes in enumerate(members):
            prev, now = closes[t - 1], closes[t]
            if prev is not None and now is not None:
                rets.append(now / prev - 1.0)
                contributed[j].append(t)
        counts.append(len(rets))
        levels.append(levels[-1] * (1.0 + sum(rets) / len(rets)) if rets else levels[-1])
    return Chain(tuple(levels), tuple(counts), tuple(tuple(c) for c in contributed))


def line_start(ch: Chain | None) -> int | None:
    """The session a line's first real step starts from (the one before the first session any member
    contributed a daily return to); ``None`` = the line never steps."""
    if ch is None:
        return None
    first = next((t for t, k in enumerate(ch.contributors) if k > 0), None)
    return None if first is None else first - 1


# --- the since-return of a name against the benchmark (built with the Trigger Log, shared since the Daily Digest
# and the Insider Tracker) -----------------------------------------------------------------------------------

BENCHMARK = "IWM"  # the comparison and the session calendar
SinceStatus = Literal["priced", "unpriced", "flagged"]


@dataclass(frozen=True)
class Series:
    """A company's positive closes, by date ascending, with the two lookups the entry rule needs."""

    dates: tuple[date, ...]
    closes: tuple[float, ...]

    @classmethod
    def of(cls, closes: Mapping[date, float]) -> Series:
        """A non-positive close is not a bar (the shared line math's rule, ``series.aligned``)."""
        pairs = sorted((d, c) for d, c in closes.items() if c is not None and c > 0)
        return cls(tuple(d for d, _ in pairs), tuple(c for _, c in pairs))

    def on_or_after(self, d: date) -> tuple[date, float] | None:
        i = bisect_left(self.dates, d)
        return (self.dates[i], self.closes[i]) if i < len(self.dates) else None

    def on_or_before(self, d: date) -> tuple[date, float] | None:
        i = bisect_right(self.dates, d) - 1
        return (self.dates[i], self.closes[i]) if i >= 0 else None

    def on(self, d: date) -> float | None:
        """The close dated exactly ``d`` (``None`` = no bar that day)."""
        i = bisect_left(self.dates, d)
        return self.closes[i] if i < len(self.dates) and self.dates[i] == d else None


@dataclass(frozen=True)
class SinceMove:
    """The name's as-traded return from the entry bar to the end bar, with IWM on the same two dates. ``status``
    ``priced`` = both bars exist; ``unpriced`` = no bar from the recorded night to the as-of; ``flagged`` = the
    name's price history is on the flagged-price list (``reason``), never priced. ``bench_ret`` is ``None``
    when IWM has no bar on one of those two dates."""

    status: SinceStatus
    reason: str | None = None
    entry_date: date | None = None
    end_date: date | None = None
    ret: float | None = None
    bench_ret: float | None = None


def sessions_since(sessions: Sequence[date], night: date, asof: date) -> int | None:
    """The sessions between the first one on or after ``night`` (the entry bar's) and the last one on or
    before ``asof``, on the shared session calendar: the number of daily steps the since-return spans. ``None``
    = no session in ``[night, asof]``."""
    first = bisect_left(sessions, night)
    last = bisect_right(sessions, asof) - 1
    if first >= len(sessions) or last < first:
        return None
    return last - first


def since_move(own: Series | None, bench: Series, night: date, asof: date) -> SinceMove:
    """The since-return of one name over the span from its first close on or after ``night`` to its last close
    on or before ``asof``; IWM's closes on those same two dates (``None`` when IWM has no bar on either, never
    a different span)."""
    start = own.on_or_after(night) if own is not None else None
    end = own.on_or_before(asof) if own is not None else None
    if start is None or end is None or start[0] > end[0]:
        return SinceMove("unpriced")
    b0, b1 = bench.on(start[0]), bench.on(end[0])
    return SinceMove(
        "priced",
        entry_date=start[0],
        end_date=end[0],
        ret=end[1] / start[1] - 1.0,
        bench_ret=b1 / b0 - 1.0 if b0 is not None and b1 is not None else None,
    )


def week_start(d: date) -> date:
    """The Monday of ``d``'s week: the page's default grouping key."""
    return d - timedelta(days=d.weekday())
