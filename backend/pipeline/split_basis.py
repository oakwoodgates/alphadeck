"""T4 — the SPLIT-BASIS defect monitor: "does a stored price tape mix two split bases?"

THE DEFECT (MEASURED 2026-10-16, read-only on prod). The price leg had no corporate-action handling of its
own (``ingest/prices/source.py`` — no ``get_splits``): it trusted the vendor to re-base the whole history on
every split, and its re-version pass (``ingest/prices/ingest_security.py``) mirrored whatever the vendor
served, bar for bar. (Since S2, 2026-10-20, the leg reads the split LIST and keeps the stored tape on its
scale — ``ingest/prices/split_normalize.py`` — and ``pipeline.repair_split_basis`` repairs the history; this
monitor stays the backstop for a split that is not on the list.) When the vendor serves a HALF-adjusted tape, or flips a bar between the adjusted and
the unadjusted basis from one pull to the next, the stored latest-version tape inherits it:

- VWAV did a 1-for-20 reverse split around 2026-09-17. The tape carries 09-15 = 0.532 on 4.5M shares and
  09-16 = 0.377 on 12.7M shares UNADJUSTED, between adjusted bars (9.06 on 09-14, 7.32 on 09-17) — a
  two-bar ISLAND that has persisted for weeks.
- GGRP / VRME / OPTT / CPOP / LRHC / BURU carry one-to-two-bar islands at a ×6..×40 factor, most of them
  starting 2026-07-29.
- MPU / SEGG / BURU FLAP: the same bar re-versions ×20 one night and ×0.05 a few nights later.
- A basis cliff reads as a +1,800% "breakout": ``technical_breakout`` triggers with close/base_high >= 3 on
  17 tickers are in the record.

WHAT THIS MODULE IS, AND IS NOT (the ``tape_health`` discipline):

- **A MONITOR, not a signal and not a repair.** It imports nothing from ``calls/`` or ``signals/`` (one
  exception, read lazily: ``view_lookback_days`` reads the readers' DECLARED price horizons off the
  ``signals.horizons`` registry — a number, never a detector) and neither imports it; nothing it finds
  reaches a detector, a CallCard or a stored bar. Plan Q10 (should a
  price detector ABSTAIN on a flagged name) closed as superseded: the split list repairs the stored bars
  (S2), and a split's first nights before it is listed are the split-night hold's (S5,
  ``domain/split_hold.py`` — its own bar-to-bar step test, never this monitor's flag). Its output rides
  the run artifact, the cron's printed report, the R4 health page and ``/admin/status`` — evidence for the
  operator (#3/#6), never a call input (#4). Since split plan S3 the Cockpit's data flag runs the pure
  detectors below on read over a name's whole stored tape (``pipeline/data_flags.py`` — the
  ``mixed_scale_history`` kind, display-only), and settles a flapping bar whose newest version sits on the
  split list's scale.
- **Deterministic and pure at the core.** ``tape_findings`` / ``flap_findings`` are pure functions of the
  bars (no I/O, no clock — the window bound is a parameter). ``scan_split_defects`` is the one READ: two
  windowed queries over the active basket union, then the pure detectors.
- **Read-only.** The scan's connection is ``read_only``; it writes no row. The settle check is a third
  SELECT on the same connection, for the flagged flap names only.

THE THREE KINDS. All work on ADJACENT-BAR close ratios (a "candidate boundary" is a pair of consecutive
bars whose close moves by at least ``K_MIN`` either way, both closes at or above ``MIN_CLOSE``), and the two
tape kinds additionally demand the INVERSE VOLUME move, which is what separates a basis change from a real
move: a split multiplies the price by k AND divides the share volume by k, so the DOLLAR volume carries
straight through it, while a genuine big move almost always comes on a volume SPIKE in the same direction.

1. **island** — a candidate boundary followed, within ``ISLAND_MAX_BARS`` bars, by an inverse boundary of
   about the same factor (the two factors agree within ``PAIR_TOL``), so the bars between sit on another
   basis than both their neighbors. The island's price level differs from its flanks' by ``K >= K_MIN``
   and its volume level moves the OTHER way by ``V = K ** -beta`` with ``BETA_MIN <= beta <= BETA_MAX``.
   This is the half-adjusted shape (VWAV), the highest-confidence kind.
2. **cliff** — a lone UP boundary (no inverse partner) after which the price level stays up by ``K`` and
   the volume level stays down by ``K ** -beta``: a reverse split the vendor never applied to the history
   before it. ONLY UP, deliberately: a lone DOWN boundary with rising volume is exactly what a genuine
   crash on heavy volume looks like (an offering, a delisting notice — common in this universe: MEASURED
   2026-10-16, allowing lone down cliffs would have flagged 19 more steps, essentially all genuine crashes),
   so an unadjusted FORWARD split cannot be told from one on the tape alone. That is a named residual, not
   an oversight; the flap check still catches a vendor that keeps changing its mind.
3. **flap** — from the version history: one stored bar whose consecutive versions moved by at least
   ``K_MIN`` in BOTH directions (a basis change and then a change back), the latest of them recorded (UTC
   date) on or after ``flap_since``. A SINGLE re-base (every old bar re-versioned once by the split factor)
   is the honest re-version the ingest is built to make (source-strategy Option A) and never flags.

Three gates keep a genuine move or a thin print from reading as a basis, each load-bearing on the measured
set (removing it adds a row judged false): a LEVEL is the median of at least ``MIN_LEVEL_BARS`` bars that
never cross another boundary, a side may not move by ``K`` or more within itself (a pump-and-fade whose
own swing exceeds its "step" is a move), and both levels must trade at least ``MIN_DOLLAR_VOLUME`` a day.

SETTLED FLAPS (2026-10-23). A flap is a fact about our STORED versions, not about what any reader saw: when
the vendor re-based a name's whole history at once (and back), each night's view still sat on one basis. So
a flap stays a DEFECT only if some reader actually saw two bases — (a) the newest-version tape (what the live
call reads) carries a basis break in the window (an island / cliff, or a flapping bar a basis away from both
neighbors), or (b) on some night in the window the HISTORY view on the public clock (``db.public_clock`` — the
same SQL every history view runs) read a lookback whose bars sit on two bases. Otherwise it is SETTLED: named
on the artifact and on Admin as one quiet line, never paged, never an alarm (``settle_flap``). Only the names
the detectors above already flag get the per-night check (``settle_split_defects``). Islands and cliffs are
findings ON the newest tape, so they never settle.

The thresholds, and the measured set they were checked against, are documented at each constant below;
``docs/FEED_LOOP.md`` (tape monitor) carries the prose and the precision table.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from statistics import median, median_low
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:  # the scan opens a connection; the pure core never needs psycopg
    import psycopg

    from domain.config import CallConfig

# --- the kinds (wire strings: they ride the artifact and the API; never reformat them) -------------------
ISLAND = "island"
CLIFF = "cliff"
FLAP = "flap"
KINDS = (ISLAND, CLIFF, FLAP)

# --- thresholds -----------------------------------------------------------------------------------------
# K_MIN — the smallest factor a candidate boundary moves by. 1.8, not 2: a 1-for-2 basis change on a day the
# name genuinely fell 10% reads 1.8. Smaller factors (3:2, 5:4) are not covered: on these names a 50% day is
# ordinary, so a 1.5 boundary is not evidence of anything (MEASURED 2026-10-16: the defects flagged were
# x5.7..x95, and the basket's clean single re-bases x3..x160 — no 3:2-class basis change anywhere).
K_MIN = 1.8
# MIN_CLOSE — the TICK floor: both closes of a boundary must be at least two cents. Below that a single
# one-cent quote increment is itself a x1.5..x2 move (MMTIF trades 0.01 <-> 0.02; CNBX / HCMC / RWAX /
# ENDV print 0.0001 <-> 0.0002 every few days), so a "factor" there is tick noise, not a basis. Compared
# with a float32 allowance (``_above_floor``): the vendor's closes are float32, so 0.02 is stored as
# 0.0199999995 and must still read as two cents.
MIN_CLOSE = 0.02
# ISLAND_MAX_BARS — the farthest the inverse boundary may sit from the first (the island's maximum length).
# The measured islands are 1-2 bars; 30 bars (~six weeks of sessions) is generous headroom for a vendor
# that leaves a longer stretch on the old basis, while keeping a genuine move and its unrelated reversal
# months later from pairing up.
ISLAND_MAX_BARS = 30
# PAIR_TOL — how closely the island's two boundary factors must agree (multiplicatively): the second must
# undo the first to within x1.5 either way. MEASURED on the 2026-10-16 islands: products 0.87..1.14 (the
# genuine moves on the two boundary days are what keep it from being exactly 1).
PAIR_TOL = 1.5
# FLANK_BARS — how many bars on each side form a level (median) for the volume test. Five sessions is a
# week: enough that one spike day cannot decide the median, short enough to stay on one basis. A flank
# stops early at the next boundary (it never crosses a basis). MIN_LEVEL_BARS — a LEVEL is the median of at
# least three bars, so no single day decides it: an island's outside (both flanks together, each at least
# one bar) and each side of a lone cliff need three. A pump whose every day is itself a x2 step has no
# level beside it, so its boundaries are not judged.
FLANK_BARS = 5
MIN_LEVEL_BARS = 3
# MIN_DOLLAR_VOLUME — the liquidity floor: both levels (median close x median volume) must trade at least
# $1,000 a day. A name that changes hands for tens of dollars a day (MVNC: 2..362 shares at ~$0.30) prints
# at whatever price the one order takes, so its "level" is noise and its volume cannot carry a basis.
# MEASURED margin on 2026-10-16: the thinnest TRUE finding traded $6.6K a day (DUKR, outside level), the
# rejected thin island $21.
MIN_DOLLAR_VOLUME = 1_000.0
# BETA — the inverse-volume test. ``beta = -log(V) / log(K)``: 1.0 = the volume level moved by exactly the
# inverse of the price factor (the textbook split), 0 = volume did not move, negative = volume moved WITH
# the price (a genuine rally on a volume spike, or a crash on a volume collapse). BETA_MIN = 0.5 demands the
# volume move be at least half the price factor in log terms, so a doubling on flat-to-slightly-lower volume
# (a squeeze's continuation day) cannot pass; BETA_MAX = 3.0 refuses a volume move wildly out of scale with
# the price factor (a one-day flash crash on a x100 volume spike). Both are judged on the FLANK-MEDIAN
# levels, never on single bars. MEASURED 2026-10-16: the true findings' beta ran 0.53..1.56; BETA_MAX
# changed nothing that night (kept as a physical-plausibility bound), and BETA_MIN cost two true EVENTS on
# names flagged anyway (BURU 09-02, LRHC 08-11 — post-split volume fell by less than half the factor).
BETA_MIN = 0.5
BETA_MAX = 3.0
# FLAP_RECENT_DAYS — a flap stays flagged while its latest basis change is at most this old. The vendor
# flips SLOWLY: MEASURED on MPU, a bar went x20 on 09-16, back on 09-18 and x20 again on 10-15 — 27 days
# between flips. 90 days is three such gaps, so a slow flapper stays ONE standing row instead of dropping
# out between flips and re-paging as "new" every month; a vendor that has held one basis for a quarter of
# nightly pulls has, for now, stopped (the row ages out, and pages again as new if it ever resumes).
FLAP_RECENT_DAYS = 90
# DATES_CAP — at most this many of a defect's affected dates ride the artifact (the most recent ones — the
# bars the detectors read first); ``events`` always carries the full count, so a cut is visible.
DATES_CAP = 12


def window_days(cfg: CallConfig | None = None) -> int:
    """The scan's CALENDAR window: the widest price lookback any call-path detector reads tonight — the
    52-week breakout, the 200-day SMA / laggard reads and the core breakdown all pull ~430 calendar days,
    the volume breakout 120. A basis defect anywhere inside that span can distort a detector tonight (a
    fake breakout, a poisoned 52-week high, a 200-day SMA averaged over two bases); one older than every
    lookback cannot touch the call. Read off the config (never a second copy of the numbers), so widening a
    lookback widens the monitor with it."""
    from domain.config import DEFAULT_CONFIG

    c = cfg or DEFAULT_CONFIG
    return max(
        c.breakout_lookback_days,
        c.breakout_52w_lookback_days,
        c.laggard_lookback_days,
        c.breakdown_core_lookback_days,
    )


def view_lookback_days(cfg: CallConfig | None = None) -> int:
    """The longest price lookback ANY reader takes on one night — what the settle check reads behind each
    night: the max DECLARED ``fact_price_eod`` horizon over every registered reader, the call path's (the
    52-week high, the 200-day average, the 8-day high, the 10-day return: ``window_days``) and the display
    columns' (the SMA-position history reads further back) alike. Read off the horizon registry
    (``signals.horizons``), never a second copy of the numbers, and never shorter than ``window_days``. A
    lazy import: the registry is the one thing this monitor reads from ``signals/`` (a declaration, not a
    detector)."""
    from domain.config import DEFAULT_CONFIG
    from signals.horizons import call_horizons, display_horizons

    c = cfg or DEFAULT_CONFIG
    declared = [
        h["fact_price_eod"]
        for h in (*call_horizons(c), *display_horizons())
        if h.get("fact_price_eod") is not None
    ]
    return max([window_days(c), *declared])


@dataclass(frozen=True)
class TapeBar:
    """One latest-version EOD bar as the detectors see it (``close`` / ``volume`` may be absent)."""

    d: date
    close: float | None
    volume: float | None


@dataclass(frozen=True)
class Finding:
    """What a pure detector found on ONE security: the ``kind``, the affected ``dates`` (island bars, the
    first bar on the new basis for a cliff, the flapping bar dates), and the ``factor`` (>= 1, the size of
    the basis step: x20 for a 1-for-20 split)."""

    kind: str
    dates: tuple[date, ...]
    factor: float


@dataclass(frozen=True)
class SplitDefect:
    """One flagged (security, kind): every finding of that kind on the name folded into one row. ``key``
    (``kind:security_id``) is the cross-run diff key — never the ticker (a ticker-less name must still page,
    and a ticker can change under a name). ``dates`` holds at most ``DATES_CAP`` of the most recent affected
    dates; ``events`` is the full count, so a cut is visible. ``factor`` is the median step over the
    findings, rounded to two decimals. ``evidence`` (a flap the settle check KEPT) says which reader saw two
    bases — the night and the two bases its view mixed, or the newest tape's own break; ``None`` otherwise.
    """

    security_id: UUID
    ticker: str | None
    kind: str
    dates: tuple[date, ...]
    factor: float
    events: int
    evidence: str | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.security_id}"

    @property
    def label(self) -> str:
        """``"<ticker or id> <kind> x<factor> (<newest date>)"`` — the page / panel / report line."""
        who = self.ticker or str(self.security_id)
        newest = f" ({self.dates[-1].isoformat()})" if self.dates else ""
        return f"{who} {self.kind} x{_fmt_factor(self.factor)}{newest}"

    def to_json(self) -> dict:
        out = {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "kind": self.kind,
            "dates": [d.isoformat() for d in self.dates],
            "factor": self.factor,
            "events": self.events,
        }
        if self.evidence is not None:
            out["evidence"] = self.evidence
        return out


@dataclass(frozen=True)
class SettledDefect:
    """A flap the settle check found NO reader sees (``settle_flap``): the stored history changed basis and
    back, but every night's view reads a single basis. Named (never dropped — the row and its ``reason`` ride
    the artifact and Admin's quiet line), never paged, never an alarm."""

    defect: SplitDefect
    reason: str

    def to_json(self) -> dict:
        return {**self.defect.to_json(), "reason": self.reason}


def _fmt_factor(f: float) -> str:
    return f"{f:.0f}" if f >= 10 else f"{f:.1f}"


# --- the pure tape detectors ---------------------------------------------------------------------------


def _log_step(a: float, b: float) -> float:
    return math.log(b / a)


def _above_floor(close: float) -> bool:
    """At or above the ``MIN_CLOSE`` tick floor, allowing for float32 storage (relative 1e-6)."""
    return close >= MIN_CLOSE * (1 - 1e-6)


def _is_boundary(a: TapeBar, b: TapeBar) -> bool:
    """A CANDIDATE boundary: both closes present and at/above the tick floor, and the close moved by at
    least ``K_MIN`` either way."""
    if a.close is None or b.close is None:
        return False
    if not (_above_floor(a.close) and _above_floor(b.close)):
        return False
    return abs(_log_step(a.close, b.close)) >= math.log(K_MIN)


def _segment(
    bars: Sequence[TapeBar], cut: set[int], start: int, step: int, limit: int
) -> list[TapeBar]:
    """Up to ``limit`` bars from ``start`` walking by ``step`` (+1 / -1) WITHOUT crossing a candidate
    boundary — so a flank stays on one basis. ``cut`` holds the index ``i`` of every boundary between bars
    ``i-1`` and ``i``."""
    out: list[TapeBar] = []
    i = start
    while 0 <= i < len(bars) and len(out) < limit:
        out.append(bars[i])
        if step > 0 and (i + 1) in cut:  # the boundary AFTER bar i ends a forward walk
            break
        if step < 0 and i in cut:  # the boundary BEFORE bar i ends a backward walk
            break
        i += step
    return out


def _level(bars: Iterable[TapeBar], attr: str) -> float | None:
    """The median of a positive field over ``bars`` (None when no bar carries one)."""
    vals = [v for b in bars if (v := getattr(b, attr)) is not None and v > 0]
    return median(vals) if vals else None


def _range(side: Sequence[TapeBar]) -> float:
    """``max close / min close`` over a side (1.0 for a single bar) — its own genuine movement."""
    closes = [b.close for b in side if b.close is not None and b.close > 0]
    return max(closes) / min(closes) if closes else 1.0


def _inverse_volume(
    inside: Sequence[TapeBar], outside: Sequence[TapeBar], *, ranged: Sequence[Sequence[TapeBar]]
) -> tuple[float, float] | None:
    """The level test shared by islands and cliffs: ``(K, beta)`` when ``inside``'s median close differs
    from ``outside``'s by ``K >= K_MIN``, its median volume moved the OTHER way by
    ``beta in [BETA_MIN, BETA_MAX]``, both levels trade at least ``MIN_DOLLAR_VOLUME`` a day, and every
    side in ``ranged`` moved by less than ``K`` within itself (a basis step DOMINATES the genuine movement
    around it; a pump-and-fade whose own swing exceeds its "step" is a move, not a basis). Else None — and
    missing closes or volumes on either side are None too: without both levels the inverse-volume evidence
    does not exist, and a flag needs it. Levels are medians over the bars that carry the field (a
    zero-volume day is no volume level, not a level of zero)."""
    c_in, c_out = _level(inside, "close"), _level(outside, "close")
    v_in, v_out = _level(inside, "volume"), _level(outside, "volume")
    if not (c_in and c_out and v_in and v_out):
        return None
    if min(c_in * v_in, c_out * v_out) < MIN_DOLLAR_VOLUME:
        return None
    log_k = math.log(c_in / c_out)
    if abs(log_k) < math.log(K_MIN):
        return None
    k = math.exp(abs(log_k))
    if any(_range(side) >= k for side in ranged):
        return None
    beta = -math.log(v_in / v_out) / log_k
    if not (BETA_MIN <= beta <= BETA_MAX):
        return None
    return k, beta


def tape_findings(bars: Sequence[TapeBar]) -> list[Finding]:
    """The ISLAND and CLIFF findings on one security's latest-version tape (ascending by date). Pure.

    1. Every adjacent pair is tested as a candidate boundary (``_is_boundary``).
    2. Each boundary, in date order, looks for the NEAREST later boundary within ``ISLAND_MAX_BARS`` that
       moves the other way by about the same factor (``PAIR_TOL``). A pair is an island candidate: the bars
       between are tested against their flanks (``FLANK_BARS`` each side, never crossing another boundary)
       and flag only on the inverse-volume level test. A pair that FAILS it is a genuine spike and
       reversal — neither boundary is then judged as a lone cliff.
    3. A boundary with no partner is judged as a lone cliff — only an UP boundary (see the module
       docstring), by the same level test over ``FLANK_BARS`` before vs ``FLANK_BARS`` from it.
    """
    bars = [b for b in bars if b.close is not None and b.close > 0]
    cut = {i for i in range(1, len(bars)) if _is_boundary(bars[i - 1], bars[i])}
    steps = {i: _log_step(bars[i - 1].close, bars[i].close) for i in cut}  # type: ignore[arg-type]
    order = sorted(cut)
    used: set[int] = set()
    out: list[Finding] = []
    for i in order:
        if i in used:
            continue
        partner = next(
            (
                j
                for j in order
                if i < j <= i + ISLAND_MAX_BARS
                and j not in used
                and steps[j] * steps[i] < 0
                and abs(steps[i] + steps[j]) <= math.log(PAIR_TOL)
            ),
            None,
        )
        if partner is not None:
            used.update((i, partner))
            inside = bars[i:partner]
            before = _segment(bars, cut, i - 1, -1, FLANK_BARS)
            after = _segment(bars, cut, partner, +1, FLANK_BARS)
            if not before or not after or len(before) + len(after) < MIN_LEVEL_BARS:
                continue  # no outside level to compare against — not judged
            hit = _inverse_volume(inside, before + after, ranged=(inside, before, after))
            if hit is not None:
                out.append(Finding(ISLAND, tuple(b.d for b in inside), hit[0]))
            continue
        used.add(i)
        if steps[i] <= 0:
            continue  # a lone DOWN boundary is indistinguishable from a genuine crash — never flagged
        before = _segment(bars, cut, i - 1, -1, FLANK_BARS)
        after = _segment(bars, cut, i, +1, FLANK_BARS)
        if len(before) < MIN_LEVEL_BARS or len(after) < MIN_LEVEL_BARS:
            continue  # a level on each side, or no judgment
        hit = _inverse_volume(after, before, ranged=(before, after))
        if hit is not None:
            out.append(Finding(CLIFF, (bars[i].d,), hit[0]))
    return out


# --- the pure flap detector -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BarVersion:
    """One stored version of one bar: when it was recorded and its close."""

    recorded_at: datetime
    close: float | None


def flap_findings(
    history: Mapping[date, Sequence[BarVersion]], *, flap_since: date
) -> list[Finding]:
    """The FLAP finding on one security's version history (``d`` -> its versions, any order). Pure.

    A bar FLAPS when its consecutive versions (ordered by ``recorded_at``) carry a basis change of at least
    ``K_MIN`` in BOTH directions — the vendor changed the basis and then changed it back — and the latest
    such change was recorded on or after ``flap_since`` (the market date of ``recorded_at``). One finding
    per security folds every flapping bar: ``dates`` are the bars, ``factor`` the median step. A bar
    re-based ONCE (the honest split re-version) never flags."""
    dates: list[date] = []
    ks: list[float] = []
    for d, versions in history.items():
        vs = sorted(
            (v for v in versions if v.close is not None and v.close > 0),
            key=lambda v: v.recorded_at,
        )
        ups = downs = 0
        last: datetime | None = None
        bar_ks: list[float] = []
        for a, b in zip(vs, vs[1:]):
            if not (_above_floor(a.close) and _above_floor(b.close)):  # type: ignore[arg-type]
                continue  # the tick floor applies to versions too: a 0.0001 <-> 0.0002 re-print
            step = _log_step(a.close, b.close)  # type: ignore[arg-type]
            if abs(step) < math.log(K_MIN):
                continue
            ups += step > 0
            downs += step < 0
            last = b.recorded_at
            bar_ks.append(math.exp(abs(step)))
        if ups and downs and last is not None and last.date() >= flap_since:
            dates.append(d)
            ks.extend(bar_ks)
    if not dates:
        return []
    return [Finding(FLAP, tuple(sorted(dates)), median(ks))]


def fold(security_id: UUID, ticker: str | None, findings: Iterable[Finding]) -> list[SplitDefect]:
    """Fold one security's findings into one ``SplitDefect`` per kind (KINDS order). Pure."""
    by_kind: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_kind[f.kind].append(f)
    out: list[SplitDefect] = []
    for kind in KINDS:
        fs = by_kind.get(kind)
        if not fs:
            continue
        all_dates = sorted({d for f in fs for d in f.dates})
        out.append(
            SplitDefect(
                security_id=security_id,
                ticker=ticker,
                kind=kind,
                dates=tuple(all_dates[-DATES_CAP:]),
                factor=round(median(f.factor for f in fs), 2),
                events=len(all_dates),
            )
        )
    return out


# --- SETTLED: a flap no reader saw on two bases (the module note; 2026-10-23) ---------------------------


@dataclass(frozen=True)
class PublicVersion:
    """One stored version of one bar as a HISTORY view orders it (``db.public_clock``): ``public`` = its
    PUBLIC instant, then ``recorded_at`` / ``vid`` — the view's own pick, the newest version public by its
    ``known_at`` (``public DESC, recorded_at DESC, id DESC``)."""

    public: datetime
    recorded_at: datetime
    vid: UUID
    close: float | None
    volume: float | None = None


@dataclass(frozen=True)
class MixedView:
    """The first night a history view read TWO bases: on ``night`` its lookback held ``events`` bar(s) (the
    most recent ``dates``) at ``other`` x the newest-version tape while the rest sat at ``majority`` x it.
    ``nights`` = how many nights in the window read a mixed lookback. ``example`` = one off-basis bar as
    ``(d, the close the view read, the newest close)``, both put on that night's scale
    (``domain.splits.on_view_scale``)."""

    night: date
    majority: float
    other: float
    dates: tuple[date, ...]
    events: int
    nights: int
    example: tuple[date, float, float] | None = None

    @property
    def factor(self) -> float:
        """How far apart the two bases sit (>= ``K_MIN``)."""
        return math.exp(abs(math.log(self.other / self.majority)))

    def describe(self) -> str:
        span = (
            self.dates[0].isoformat()
            if len(self.dates) == 1
            else f"{self.dates[0].isoformat()}..{self.dates[-1].isoformat()}"
        )
        ex = ""
        if self.example is not None:
            d, seen, ref = self.example
            ex = f"; e.g. it read {d.isoformat()} at {seen:.4g} where the newest tape reads {ref:.4g}"
        return (
            f"the history view of {self.night.isoformat()} read {self.events} bar(s) ({span}) at "
            f"x{self.other:.3g} of the newest tape beside bars at x{self.majority:.3g} — two bases "
            f"x{_fmt_factor(self.factor)} apart{ex} ({self.nights} night(s) mixed)"
        )


@dataclass(frozen=True)
class Settle:
    """``settle_flap``'s verdict: ``settled`` (no reader saw two bases) and the operator-facing ``reason``
    either way; ``nights`` checked; ``mixed`` = the view evidence when a night mixed bases."""

    settled: bool
    reason: str
    nights: int = 0
    mixed: MixedView | None = None


# "every night" = each night's HISTORY view as the platform reads it (the public clock, the operator's rule
# for judging history, 2026-10-21) — not the record-clock call logged that night, which is bookkeeping
SETTLED_REASON = (
    "the stored history changed basis and back, but every night's view reads a single basis"
)


def view_known_at(night: date) -> datetime:
    """The ``known_at`` a HISTORY view of ``night`` reads with — the night cap the serve recomputes, the
    Scoreboard drawer and the backfill share (``domain.market_time.known_at_for_asof``): what was public
    by the end of that night (EDGAR's next open, F6)."""
    from domain.market_time import known_at_for_asof

    return known_at_for_asof(night)


def settle_flap(
    flap_dates: Iterable[date],
    versions: Mapping[date, Sequence[PublicVersion]],
    *,
    asof: date,
    window: int,
    lookback: int,
    split_rows: Sequence[Mapping[str, Any]] = (),
    store_through: date | None = None,
    known_at: Callable[[date], datetime] | None = None,
) -> Settle:
    """Did any READER see this flapping name's tape on two bases? Pure over its inputs.

    ``versions`` = every stored version of every bar a view in the window can read (``d`` from
    ``asof - window - lookback`` through ``asof``), each with its public instant; ``split_rows`` = the split
    list's current reading and ``store_through`` the scale the stored values are on (``on_view_scale``'s
    inputs, used for the evidence only — see 3). A flap stays a DEFECT when:

    1. the NEWEST-version tape in the window (what the live call reads) carries an island or a cliff
       (``tape_findings``); or a flapping bar's newest close sits ``K_MIN`` or more from BOTH neighboring
       bars' newest closes (a basis step at the bar itself the level gates might not judge — the data flag's
       own S2 rule); or
    2. on some NIGHT in ``[asof - window, asof]`` (every calendar day: a view can be read for any as-of), the
       history view — each bar's newest version public by ``known_at(night)``, the view's own pick — read a
       ``lookback``-day lookback with a bar on another basis than the rest: its close over the newest
       version's close differs from the night's median such ratio by ``K_MIN`` or more. The newest tape is
       one basis (1 failed), so a night whose ratios agree read one basis, whatever that basis was (a
       wholesale re-base reads x0.1 on every bar — one basis). Bars under the tick floor are not judged
       (``MIN_CLOSE``, the detectors' rule).
    3. Why the RAW ratio is the view's: ``domain.splits.on_view_scale`` multiplies a bar's every version by
       the same factor (it depends only on the bar's date, the store's scale and the view's day), so the
       ratio of two versions of one bar is the same on the view's scale — the listed splits act on the
       verdict through the public instants alone (a bar before a listed split reads its NEWEST version on
       every night, ``db.public_clock``). ``on_view_scale`` puts the evidence on the night's scale.

    Otherwise SETTLED. The reason names the night and the two bases (2), or the newest tape's break (1).
    """
    ka = known_at or view_known_at
    days = sorted(d for d, vs in versions.items() if vs)
    order = {d: sorted(versions[d], key=lambda v: (v.public, v.recorded_at, v.vid)) for d in days}
    newest = {d: max(versions[d], key=lambda v: (v.recorded_at, v.vid)) for d in days}
    since = asof - timedelta(days=window)

    # 1. the newest-version tape — what the live call reads
    tape = [TapeBar(d, newest[d].close, newest[d].volume) for d in days if since <= d <= asof]
    shaped = tape_findings(tape)
    if shaped:
        return Settle(
            False,
            "the newest-version tape itself carries a basis break inside the window "
            f"({shaped[0].kind} {shaped[0].dates[0].isoformat()})",
        )
    pos = {d: i for i, d in enumerate(days)}
    for d in sorted(set(flap_dates)):
        i = pos.get(d)
        c = newest[d].close if i is not None else None
        if c is None or c <= 0:
            continue  # not stored in the read (outside every view's lookback), or no close to judge
        near = [newest[days[j]].close for j in (i - 1, i + 1) if 0 <= j < len(days)]  # type: ignore[operator]
        if not any(
            n is not None and n > 0 and abs(math.log(c / n)) < math.log(K_MIN) for n in near
        ):
            return Settle(
                False,
                f"the newest version of {d.isoformat()} sits a basis away from both neighboring bars",
            )

    # 2. every night's history view
    publics = {d: [v.public for v in order[d]] for d in days}
    multi = [(d, order[d][-1].public) for d in days if len(order[d]) > 1]
    multi_days = [d for d, _ in multi]
    step = math.log(K_MIN)
    first: tuple[date, datetime, float, list[tuple[date, float, float]]] | None = None
    mixed_nights = nights = 0
    n = since
    while n <= asof:
        nights += 1
        k = ka(n)
        lo = n - timedelta(days=lookback)
        a, b = bisect_left(multi_days, lo), bisect_right(multi_days, n)
        # a night where every bar in the lookback reads its NEWEST version reads the newest tape: one basis
        if any(k < multi[j][1] for j in range(a, b)):
            ratios: list[tuple[date, float, float]] = []
            for d in days[bisect_left(days, lo) : bisect_right(days, n)]:
                idx = bisect_right(publics[d], k) - 1
                if idx < 0:
                    continue  # not public yet on that night
                c, ref = order[d][idx].close, newest[d].close
                if c is None or ref is None or not (_above_floor(c) and _above_floor(ref)):
                    continue
                ratios.append((d, c / ref, c))
            if ratios:
                med = median_low(r for _, r, _ in ratios)
                off = [x for x in ratios if abs(math.log(x[1] / med)) >= step]
                if off:
                    mixed_nights += 1
                    if first is None:
                        first = (n, k, med, off)
        n += timedelta(days=1)
    if first is None:
        return Settle(
            True,
            f"{SETTLED_REASON} ({nights} night(s) checked, each over its {lookback}-day lookback)",
            nights=nights,
        )
    night, k, med, off = first
    mixed = MixedView(
        night=night,
        majority=med,
        other=median(r for _, r, _ in off),
        dates=tuple(d for d, _, _ in off)[-DATES_CAP:],
        events=len(off),
        nights=mixed_nights,
        example=_view_example(off[0], newest, k, split_rows, store_through),
    )
    return Settle(False, mixed.describe(), nights=nights, mixed=mixed)


def _view_example(
    off: tuple[date, float, float],
    newest: Mapping[date, PublicVersion],
    known_at: datetime,
    split_rows: Sequence[Mapping[str, Any]],
    store_through: date | None,
) -> tuple[date, float, float] | None:
    """One off-basis bar as the night's view read it and as the newest tape reads it, both on the night's
    scale (``domain.splits.on_view_scale``, the history views' own conversion)."""
    from domain.market_time import view_day
    from domain.splits import STORE_THROUGH_COL, on_view_scale

    d, _r, seen = off
    ref = newest[d].close
    if ref is None:
        return None
    rows = [
        {"d": d, "close": seen, STORE_THROUGH_COL: store_through},
        {"d": d, "close": ref, STORE_THROUGH_COL: store_through},
    ]
    through = view_day(known_at)
    a, b = on_view_scale(rows, split_rows, through=through)
    return d, float(a["close"]), float(b["close"])


# --- the scan: one windowed read over the active basket union, then the pure detectors ------------------

# The ACTIVE basket union: every resolved member of every non-archived thesis — the names the nightly pass
# walks (``thesis_repo.list_all`` skips archived theses; ``ingest_thesis`` skips unresolved members).
_UNION = """
    SELECT DISTINCT bm.tenant_id, bm.security_id
      FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id
     WHERE t.archived_at IS NULL AND bm.security_id IS NOT NULL
"""

# The latest-version tape in the window — the SAME pick the as-of read makes (``DISTINCT ON`` the natural
# key, ``recorded_at DESC, id DESC``), with no transaction-time gate: a live pass reads everything stored.
_TAPE_SQL = f"""
    WITH u AS ({_UNION})
    SELECT DISTINCT ON (p.security_id, p.d)
           p.security_id, sm.ticker, p.d, p.close, p.volume
      FROM fact_price_eod p
      JOIN u ON u.tenant_id = p.tenant_id AND u.security_id = p.security_id
      JOIN security_master sm ON sm.id = p.security_id
     WHERE p.d >= %(since)s AND p.d <= %(asof)s
     ORDER BY p.security_id, p.d, p.recorded_at DESC, p.id DESC
"""

# The version history of every bar in the window that was RE-VERSIONED recently (>= 2 versions, the newest
# recorded on/after flap_since) — a flap needs both, so nothing else is fetched.
_VERSIONS_SQL = f"""
    WITH u AS ({_UNION}),
    multi AS (
        SELECT p.tenant_id, p.security_id, p.d
          FROM fact_price_eod p
          JOIN u ON u.tenant_id = p.tenant_id AND u.security_id = p.security_id
         WHERE p.d >= %(since)s AND p.d <= %(asof)s
         GROUP BY p.tenant_id, p.security_id, p.d
        HAVING count(*) > 1 AND max(p.recorded_at) >= %(flap_since)s
    )
    SELECT p.security_id, p.d, p.recorded_at, p.close
      FROM fact_price_eod p
      JOIN multi m ON m.tenant_id = p.tenant_id AND m.security_id = p.security_id AND m.d = p.d
     ORDER BY p.security_id, p.d, p.recorded_at, p.id
"""


def _num(v: object) -> float | None:
    return None if v is None else float(v)  # numeric arrives as Decimal


def scan_split_defects(
    conn: psycopg.Connection,
    *,
    asof: date,
    window: int | None = None,
    flap_recent_days: int = FLAP_RECENT_DAYS,
) -> tuple[SplitDefect, ...]:
    """Every split-basis defect on the active basket union's tapes, as of ``asof``. TWO windowed queries
    (the latest-version tape, and the version history of recently re-versioned bars) — never a per-name
    walk — then the pure detectors per security. SELECTs only; transaction control is the caller's
    (``run_split_scan`` opens its own ``read_only`` connection). Ordered by ticker (then id), kinds in
    KINDS order.

    ``window`` (calendar days, default ``window_days()``) bounds both reads; ``flap_recent_days`` bounds
    how old a flap's latest change back may be."""
    span = window if window is not None else window_days()
    since = asof - timedelta(days=span)
    flap_since = asof - timedelta(days=flap_recent_days)
    params = {"since": since, "asof": asof, "flap_since": flap_since}
    tapes: dict[UUID, list[TapeBar]] = defaultdict(list)
    tickers: dict[UUID, str | None] = {}
    history: dict[UUID, dict[date, list[BarVersion]]] = defaultdict(lambda: defaultdict(list))
    with conn.cursor() as cur:
        cur.execute(_TAPE_SQL, params)
        for r in cur.fetchall():
            tapes[r["security_id"]].append(TapeBar(r["d"], _num(r["close"]), _num(r["volume"])))
            tickers[r["security_id"]] = r["ticker"]
        cur.execute(_VERSIONS_SQL, params)
        for r in cur.fetchall():
            history[r["security_id"]][r["d"]].append(BarVersion(r["recorded_at"], _num(r["close"])))
    out: list[SplitDefect] = []
    for sid in sorted(tapes, key=lambda s: (tickers.get(s) or "", str(s))):
        findings = tape_findings(tapes[sid])
        if sid in history:
            findings += flap_findings(history[sid], flap_since=flap_since)
        out.extend(fold(sid, tickers.get(sid), findings))
    return tuple(out)


# --- the settle read: every version a history view can pick, for the flagged flap names only ------------


@dataclass
class PublicTape:
    """One security's stored versions as the history views order them (``versions``: ``d`` -> its versions,
    ascending in the view's pick order) and ``store_through`` — the scale the stored values are on (the
    ``split_store_through`` every public-clock price row carries)."""

    versions: dict[date, list[PublicVersion]]
    store_through: date | None = None


def read_public_tapes(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_ids: Sequence[UUID],
    asof: date,
    since: date,
) -> dict[UUID, PublicTape]:
    """Every stored version of every bar dated ``since..asof`` for ``security_ids``, each with its PUBLIC
    instant — ``db.public_clock.public_versions_sql``, the ONE builder every history view and the backtest's
    public mirror run (a bar before a listed split has every version public at its session settle; any
    other re-version from its own ``recorded_at``). ONE SELECT; an entry for every id that has a bar.
    """
    from psycopg import sql

    from db.public_clock import PUBLIC_COL, STORE_THROUGH_COL, public_params, public_versions_sql

    versions = public_versions_sql(
        "fact_price_eod",
        where=sql.SQL(
            "tenant_id = %(tenant_id)s AND security_id = ANY(%(ids)s) "
            "AND valid_from >= %(lo)s AND valid_from <= %(asof)s"
        ),
    )
    query = sql.SQL(
        "WITH v AS ({versions}) SELECT security_id, d, recorded_at, id, close, volume, "
        "{pub} AS public, {through} AS store_through FROM v "
        "ORDER BY security_id, d, {pub}, recorded_at, id"
    ).format(
        versions=versions,
        pub=sql.Identifier(PUBLIC_COL),
        through=sql.Identifier(STORE_THROUGH_COL),
    )
    params = {
        "tenant_id": tenant_id,
        "ids": list(security_ids),
        "lo": since,
        "asof": asof,
        **public_params(),
    }
    out: dict[UUID, PublicTape] = {}
    with conn.cursor() as cur:
        cur.execute(query, params)
        for r in cur.fetchall():
            t = out.setdefault(r["security_id"], PublicTape({}, r["store_through"]))
            t.versions.setdefault(r["d"], []).append(
                PublicVersion(
                    r["public"], r["recorded_at"], r["id"], _num(r["close"]), _num(r["volume"])
                )
            )
    return out


def settle_split_defects(
    conn: psycopg.Connection,
    defects: Sequence[SplitDefect],
    *,
    asof: date,
    window: int | None = None,
    lookback: int | None = None,
    flap_recent_days: int = FLAP_RECENT_DAYS,
) -> tuple[tuple[SplitDefect, ...], tuple[SettledDefect, ...]]:
    """Split the scan's rows into the ones some reader saw (``kept``, order preserved — a kept flap carries
    its ``evidence``) and the SETTLED flaps (``settle_flap``). Islands and cliffs are kept as they are (they
    are findings on the newest tape). The cost is bounded by the detectors: only the flagged FLAP names are
    read — two SELECTs (their public-clock versions, the split list) — and a name the read finds no bar for
    stays a defect (nothing to judge on is never "settled"). SELECTs only; the caller owns the transaction.
    """
    from repositories import split_repo

    span = window if window is not None else window_days()
    back = lookback if lookback is not None else view_lookback_days()
    flap_ids = sorted({d.security_id for d in defects if d.kind == FLAP}, key=str)
    if not flap_ids:
        return tuple(defects), ()
    by_tenant: dict[UUID, list[UUID]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT u.tenant_id, u.security_id FROM ({_UNION}) u "  # noqa: S608 — a literal
            "WHERE u.security_id = ANY(%(ids)s)",
            {"ids": flap_ids},
        )
        for r in cur.fetchall():
            by_tenant[r["tenant_id"]].append(r["security_id"])
    tapes: dict[UUID, PublicTape] = {}
    splits: dict[UUID, list[dict[str, Any]]] = {}
    lo = asof - timedelta(days=span + back)
    for tenant_id, sids in by_tenant.items():
        tapes.update(
            read_public_tapes(conn, tenant_id=tenant_id, security_ids=sids, asof=asof, since=lo)
        )
        splits.update(split_repo.current_rows(conn, tenant_id=tenant_id, security_ids=sids))
    since = asof - timedelta(days=span)
    flap_since = asof - timedelta(days=flap_recent_days)
    kept: list[SplitDefect] = []
    settled: list[SettledDefect] = []
    for d in defects:
        tape = tapes.get(d.security_id) if d.kind == FLAP else None
        if tape is None:
            kept.append(d)
            continue
        # the flap's FULL date set (the row carries at most DATES_CAP): the same rule over the same bars
        history = {
            day: [BarVersion(v.recorded_at, v.close) for v in vs]
            for day, vs in tape.versions.items()
            if day >= since and len(vs) > 1
        }
        full = flap_findings(history, flap_since=flap_since)
        verdict = settle_flap(
            full[0].dates if full else d.dates,
            tape.versions,
            asof=asof,
            window=span,
            lookback=back,
            split_rows=splits.get(d.security_id, ()),
            store_through=tape.store_through,
        )
        if verdict.settled:
            settled.append(SettledDefect(d, verdict.reason))
        else:
            kept.append(replace(d, evidence=verdict.reason))
    return tuple(kept), tuple(settled)


# --- the nightly integration: the scan, the cross-run diff, the artifact keys, the report -------------


@dataclass
class SplitScan:
    """One pass's split-basis monitor outcome — what rides the artifact (``payload``), the printed report
    (``report_lines``) and the R4 page. ``evaluated`` = the scan RAN (a live pass whose read succeeded);
    ``error`` = why it did not, on a live pass (a broken monitor is reported, never silently absent — the
    self-audit fails on it). ``new`` are the defects whose key was absent from the previous evaluated pass
    (``previous_split_defects``) — what PAGES, once; ``known`` counts the rest (one quiet line).

    ``defects`` are the rows some reader saw (``settle_split_defects``' kept rows); ``settled`` the flaps no
    reader saw on two bases — named on the artifact and Admin, never paged, never in the diff baseline (so a
    settled flap that later turns into a real defect pages then, once). ``settle_error`` = the settle check
    could not run: every flap then stays a defect (today's loud behavior), never silently settled.
    ``settle_duration_s`` is the check's share of ``duration_s``."""

    evaluated: bool = False
    defects: tuple[SplitDefect, ...] = ()
    new: tuple[SplitDefect, ...] = ()
    error: str | None = None
    window_days: int = 0
    duration_s: float = 0.0
    settled: tuple[SettledDefect, ...] = ()
    settle_error: str | None = None
    settle_duration_s: float = 0.0

    @property
    def known(self) -> int:
        return len(self.defects) - len(self.new)

    @property
    def new_labels(self) -> tuple[str, ...]:
        return tuple(d.label for d in self.new)

    def payload(self) -> dict:
        """The run artifact's split keys (FLAT, beside ``tape_stale_new`` / ``name_errors_new``). Every
        reader uses ``.get``: an artifact written before T4 lacks them and reads as "not evaluated", never
        as an alarm and never as an unparseable row (one written before the settle check lacks
        ``split_settled``: nothing settled)."""
        return {
            "split_evaluated": self.evaluated,
            "split_error": self.error,
            "split_window_days": self.window_days,
            "split_duration_s": round(self.duration_s, 3),
            "split_settle_duration_s": round(self.settle_duration_s, 3),
            "split_settle_error": self.settle_error,
            "split_settled": [s.to_json() for s in self.settled],
            "split_defects": [d.to_json() for d in self.defects],
            "split_defects_new": [
                {
                    "security_id": str(d.security_id),
                    "ticker": d.ticker,
                    "kind": d.kind,
                    "label": d.label,
                }
                for d in self.new
            ],
        }


def defect_keys_of(doc: Mapping) -> set[str]:
    """The ``kind:security_id`` keys one artifact's ``split_defects`` inventory holds — what the next pass
    diffs against. WHICH artifact is the baseline is ``cron_run_log.previous_split_defects``' rule (beside
    its stale-tape / name-error twins). Fail-open: a malformed row is skipped, never a raise."""
    out: set[str] = set()
    for d in doc.get("split_defects") or []:
        if isinstance(d, Mapping) and d.get("security_id") and d.get("kind"):
            out.add(f"{d['kind']}:{d['security_id']}")
    return out


def new_labels_of(doc: Mapping) -> tuple[str, ...]:
    """The NEW-defect labels one artifact recorded (what that pass paged) — the Admin history re-derives
    the run's page from these. ``()`` for an artifact without the key; a malformed entry is skipped.
    """
    out: list[str] = []
    for d in doc.get("split_defects_new") or []:
        if isinstance(d, Mapping) and d.get("label"):
            out.append(str(d["label"]))
    return tuple(out)


def run_split_scan(
    *,
    asof: date,
    allow_live: bool,
    connect: Callable[[], psycopg.Connection],
    previous: Callable[[], set[str] | None],
    clock: Callable[[], float],
) -> SplitScan:
    """The nightly unit: scan (its own connection, ``read_only``), settle the flaps no reader saw
    (``settle_split_defects``, same connection), then diff the KEPT rows against the previous evaluated
    pass. FAIL-OPEN for the pass, LOUD for the operator: a scan that raises returns ``evaluated=False``
    with the ``error`` (the self-audit fails on it and pages), never an exception into the cron; a settle
    check that raises keeps EVERY row a defect (``settle_error``) — it can only ever quiet a row it judged.
    A ``--no-live`` pass does not scan (it never re-pulled the tapes, and it must never become the diff
    baseline). ``previous`` is called only after a successful scan (a failed one diffs nothing)."""
    span = window_days()
    if not allow_live:
        return SplitScan(window_days=span)
    t0 = clock()
    settle_error: str | None = None
    settled: tuple[SettledDefect, ...] = ()
    try:
        conn = connect()
        try:
            conn.read_only = True  # the monitor reads; structurally it cannot write a row
            defects = scan_split_defects(conn, asof=asof, window=span)
            t1 = clock()
            try:
                defects, settled = settle_split_defects(conn, defects, asof=asof, window=span)
            except Exception as e:  # noqa: BLE001 — a settle fault keeps every row loud
                conn.rollback()
                settle_error = _fault(e)
            settle_s = clock() - t1
            conn.rollback()  # end the read-only transaction; nothing to keep
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a monitor fault is reported, never a failed pass
        return SplitScan(window_days=span, error=_fault(e), duration_s=clock() - t0)
    prior = previous()
    new = tuple(d for d in defects if prior is None or d.key not in prior)
    return SplitScan(
        evaluated=True,
        defects=defects,
        new=new,
        window_days=span,
        duration_s=clock() - t0,
        settled=settled,
        settle_error=settle_error,
        settle_duration_s=settle_s,
    )


def _fault(e: BaseException) -> str:
    msg = " ".join(str(e).split())[:200]
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def report_lines(scan: SplitScan) -> list[str]:
    """The cron's printed block (``pipeline.daily._report``): NEW defects LOUD (an uppercase header, one
    line each with its dates), KNOWN ones ONE quiet lowercase line (the closed-tape / known-name-error
    casing convention), a failed scan LOUD, and nothing at all on a clean or un-scanned (``--no-live``)
    night — loudness marks the exception."""
    if scan.error:
        return [f"SPLIT-BASIS MONITOR FAILED — {scan.error} (tonight's tapes were not checked)"]
    if not scan.evaluated:
        return []
    lines: list[str] = []
    if scan.new:
        lines.append(
            "SPLIT-BASIS DEFECTS — NEW since the last pass (a price tape mixes split bases; flag only, the "
            "call is unchanged — see docs/FEED_LOOP.md, tape monitor):"
        )
        for d in scan.new:
            more = f" (+{d.events - len(d.dates)} more)" if d.events > len(d.dates) else ""
            dates = ", ".join(x.isoformat() for x in d.dates)
            why = f" — still a defect: {d.evidence}" if d.evidence else ""
            lines.append(f"  {d.label}: {d.events} {_noun(d.kind)} — {dates}{more}{why}")
    if scan.known:
        lines.append(
            f"{scan.known} known split-basis defect(s) — the same names and kinds as the last pass "
            "(quiet; the list is in the run artifact and on the Admin page)"
        )
    if scan.settled:
        lines.append(
            f"{len(scan.settled)} settled split-basis flap(s) — {SETTLED_REASON}; no view is affected "
            "(quiet; named in the run artifact and on the Admin page)"
        )
    if scan.settle_error:
        lines.append(
            f"split-basis settle check could not run ({scan.settle_error}) — every flap is reported as a "
            "defect tonight"
        )
    return lines


def _noun(kind: str) -> str:
    return {ISLAND: "island bar(s)", CLIFF: "cliff(s)", FLAP: "flapping bar(s)"}.get(kind, kind)
