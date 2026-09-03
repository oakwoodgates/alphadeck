"""Benchmark relative strength — is the name LEADING the market, and which theme leads (Signals §2.1/§1.3).

Relative strength (RS) = the member's close divided by a benchmark's close, aligned by date. The
leadership tell (R5, ratified): the RS line making a fresh **13-week high** — the member pulling ahead
of the market, not just rising with it. Benchmarks are **SPY** (the broad market) and **IWM**
(small-caps); RS is computed against each.

Display-only tape context, structurally OFF the call path (#4/#6): no ``role``, it cannot fire / arm /
veto / grade, and it never feeds the call. It is the "leading the market" HALF of the breakout read,
surfaced beside the call as a Cockpit column (like ``rvol``) — promoting RS to a breakout-SCORE input
is a later, separately-signed step (it would widen ``SignalPointInTimeData`` + ``ReplayPointInTimeData``;
v0 is display-only).

Two shapes share the pure RS math:

* **§2.1 — the per-name column** (registered display member ``relative_strength``): RS vs SPY and vs
  IWM, each with the 13-week-high leadership tell. Reads the benchmark tape via the sanctioned
  ``pit.benchmark_prices`` widening (``DISPLAY_SIGNALS.md``); honestly ABSENT (returns ``None``) when no
  benchmark tape overlaps the member — exactly like ``etf_flow`` with no samples, never a fabricated ratio.
* **§1.3 — the supersector rollup** (``sector_rs_for``, thesis-level like ``theme_breadth``): group the
  resolved members by their ``BusinessSupersector`` and count how many LEAD (RS at a 13-week high vs the
  broad market, SPY) — rotation's "which theme is leading". The router resolves each member's supersector
  (identity, off the seam) and passes it in as an opaque grouping label, so this module never touches the
  master or the db.

#9: a member with too little overlapping tape to call a 13-week high is SHOWN-not-counted (an honest
"thin" gap, its ratio still reported), never fabricated as a false "at high". Dials are named module
constants in ``basis.params`` (the display-seam convention), never ``CallConfig`` dials — RS is
display-first. ``BENCHMARKS`` is hand-kept equal to ``securities.benchmarks.BENCHMARKS`` (the seam cannot
import ``securities``); ``test_relative_strength`` drift-guards the two.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayEvent,
    DisplayHeadline,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "relative_strength"
LABEL = "Relative strength (vs SPY/IWM)"
# the benchmarks RS is computed against — MUST mirror securities.benchmarks.BENCHMARKS (drift-guarded).
BENCHMARKS: tuple[str, ...] = ("SPY", "IWM")
RS_HIGH_WEEKS = 13
# ~13 trading weeks ≈ 65 bars; MIN_BARS is the floor below which a "13-week high" is not yet callable
# (the ratio is still shown, the leadership tell is an honest UNKNOWN). Kept a touch under 65 so a
# name with a holiday-thinned quarter still earns a verdict.
MIN_BARS = 60
# ~13 weeks + weekend/holiday slack, in CALENDAR days (price_history trims by calendar days).
LOOKBACK_DAYS = 95
_EPS = (
    1e-9  # a fresh high must clear the prior high beyond float-repr noise (a flat RS never "leads")
)

# the supersector rollup (§1.3) prices leadership against the BROAD market — "is this theme leading THE
# MARKET" — so it reads one benchmark, SPY, not both (the per-name column keeps both).
_ROLLUP_BENCHMARK = "SPY"


def _aligned_rs(
    member_bars: list[dict[str, Any]], bench_bars: list[dict[str, Any]]
) -> list[tuple[date, float]]:
    """The RS series = member close ÷ benchmark close over the dates present in BOTH tapes (ascending).
    A benchmark bar with a non-positive close is dropped (no divide-by-zero / negative ratio). An empty
    result means no overlap — the honest "no benchmark tape" gap, never a fabricated value."""
    bench: dict[date, float] = {}
    for b in bench_bars:
        c = b.get("close")
        if c is not None and float(c) > 0.0:
            bench[b["d"]] = float(c)
    out: list[tuple[date, float]] = []
    for b in member_bars:
        c = b.get("close")
        if c is None:
            continue
        bc = bench.get(b["d"])
        if bc is None:
            continue
        out.append((b["d"], float(c) / bc))
    return out


def _leadership(rs: list[tuple[date, float]]) -> tuple[float | None, bool | None, int, date | None]:
    """(current RS, at_fresh_13w_high, aligned bars, last aligned date) over the RS series.

    ``at_fresh_high`` is:
      * ``None``  — fewer than ``MIN_BARS`` aligned bars: a 13-week high is not yet callable (thin, #9);
      * ``True``  — the last RS strictly exceeds every prior RS in the window (a genuine fresh high —
        a flat RS that merely equals a prior high does NOT lead, hence strict + ``_EPS``);
      * ``False`` — enough history, but the RS is below its 13-week high.
    Returns ``(None, None, 0, None)`` when the RS series is empty (no benchmark overlap)."""
    if not rs:
        return (None, None, 0, None)
    last_d, current = rs[-1]
    n = len(rs)
    if n < MIN_BARS:
        return (current, None, n, last_d)
    prior_high = max(v for _, v in rs[:-1])
    at_high = current > prior_high + abs(prior_high) * _EPS
    return (current, at_high, n, last_d)


def compute(
    member_bars: list[dict[str, Any]],
    bench_bars_by_symbol: dict[str, list[dict[str, Any]]],
    asof: date,
) -> DisplaySignal | None:
    """Pure RS-vs-benchmarks over ascending EOD bars. One ``rs_<sym>`` ratio per benchmark plus, when
    the RS prints a fresh 13-week high, a leadership EVENT (exception-loud, #7). Returns ``None`` when
    nothing is computable — no member closes, or no benchmark tape overlaps EITHER benchmark (an honest
    absence like ``etf_flow`` with no samples), never a fabricated ratio."""
    priced = [b for b in member_bars if b.get("close") is not None]
    if not priced:
        return None

    metrics: list[DisplayMetric] = []
    events: list[DisplayEvent] = []
    leading: list[str] = []
    any_value = False
    window_start: date | None = None
    window_end: date | None = None
    bars_used = 0

    for sym in BENCHMARKS:
        rs = _aligned_rs(priced, bench_bars_by_symbol.get(sym, []))
        current, at_high, n, last_d = _leadership(rs)
        key = f"rs_{sym.lower()}"
        label = f"RS vs {sym}"
        if current is None:
            metrics.append(
                DisplayMetric(
                    key=key, label=label, unit="ratio", note=f"n/a: no aligned {sym} bars as-of"
                )
            )
            continue
        any_value = True
        note = (
            f"{RS_HIGH_WEEKS}w-high n/a: {n}/{MIN_BARS} aligned bars" if at_high is None else None
        )
        metrics.append(
            DisplayMetric(key=key, label=label, value=round(current, 4), unit="ratio", note=note)
        )
        window_start = rs[0][0] if window_start is None else min(window_start, rs[0][0])
        window_end = rs[-1][0] if window_end is None else max(window_end, rs[-1][0])
        bars_used = max(bars_used, n)
        if at_high:
            leading.append(sym)
            events.append(
                DisplayEvent(
                    key=f"rs_high_{sym.lower()}",
                    label=f"RS vs {sym} at a {RS_HIGH_WEEKS}-week high",
                    date=last_d,
                    direction="up",
                )
            )

    if not any_value:
        return None  # no benchmark overlap at all — honestly absent (never a fabricated RS)

    basis = DisplayBasis(
        source="fact_price_eod",
        params={
            "benchmarks": list(BENCHMARKS),
            "rs_high_weeks": RS_HIGH_WEEKS,
            "min_bars": MIN_BARS,
            "lookback_days": LOOKBACK_DAYS,
        },
        bars_used=bars_used or None,
        window_start=window_start,
        window_end=window_end,
    )
    return DisplaySignal(
        kind=MEMBER_NAME,
        label=LABEL,
        headline=_headline(leading),
        metrics=metrics,
        events=events,
        basis=basis,
    )


def _headline(leading: list[str]) -> DisplayHeadline:
    """The posture chip — LOUD only when the RS prints a fresh 13-week high vs a benchmark (#7). ``key``
    is the stable categorical (``leading`` / ``inline``) a Board/Cockpit chip consumes directly."""
    if leading:
        vs = " and ".join(leading)
        return DisplayHeadline(
            key="leading",
            label=f"Leading — RS at a {RS_HIGH_WEEKS}-week high vs {vs}",
            glyph="up",
            detail="relative strength is making a fresh high — the name is pulling ahead of the market",
        )
    return DisplayHeadline(
        key="inline",
        label=f"Not at a {RS_HIGH_WEEKS}-week RS high",
        glyph=None,
        detail="moving with the market, not ahead of it",
    )


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read the member's and the benchmarks' as-of EOD bars via the point-in-time view (both no-lookahead
    capped, #1); all arithmetic happens in the pure ``compute``."""
    member_bars = pit.price_history(security_id, lookback_days=LOOKBACK_DAYS)
    benches = {sym: pit.benchmark_prices(sym, lookback_days=LOOKBACK_DAYS) for sym in BENCHMARKS}
    return compute(member_bars, benches, asof)


# --- §1.3 — the supersector rollup (thesis-level, like theme_breadth; NOT a per-name registry member) ---

KIND_SECTOR = "sector_rs"
LABEL_SECTOR = "Sector RS leadership (vs SPY)"


def _pretty(supersector: str | None) -> str:
    return supersector.replace("_", " ") if supersector else "unclassified"


def sector_rs_for(
    pit: DisplayPointInTimeData,
    security_ids: list[UUID],
    supersector_by_sid: dict[UUID, str | None],
    *,
    benchmark: str = _ROLLUP_BENCHMARK,
) -> DisplaySignal | None:
    """Rotation's "which theme is leading" (§1.3): group the resolved members by ``BusinessSupersector``
    and count how many LEAD the broad market (RS vs SPY at a fresh 13-week high). A THESIS-level DISPLAY
    reading over the same as-of bitemporal prices — never a ``SignalEvent`` / call input (#4).

    ``supersector_by_sid`` is the router-resolved ``{security_id: supersector-string | None}`` (identity,
    resolved OFF the seam — the display module never reads the master); an unclassified member is grouped
    under "unclassified", shown-not-dropped (#9). Returns ``None`` when there are no resolved members, or
    when no benchmark tape is available at all (RS is uncomputable — an honest absence, matching the
    per-name column; the benchmark bars must be ingested first)."""
    if not security_ids:
        return None
    bench_bars = pit.benchmark_prices(benchmark, lookback_days=LOOKBACK_DAYS)
    if not bench_bars:
        return None  # no benchmark tape -> RS uncomputable -> honestly absent (ingest benchmarks first)

    # supersector -> [leaders, counted (verdict), thin (unknown), total]
    groups: dict[str | None, dict[str, int]] = {}
    for sid in security_ids:
        ss = supersector_by_sid.get(sid)
        member_bars = [
            b
            for b in pit.price_history(sid, lookback_days=LOOKBACK_DAYS)
            if b.get("close") is not None
        ]
        _, at_high, _, _ = _leadership(_aligned_rs(member_bars, bench_bars))
        g = groups.setdefault(ss, {"leaders": 0, "counted": 0, "thin": 0, "total": 0})
        g["total"] += 1
        if at_high is None:
            g["thin"] += 1
        else:
            g["counted"] += 1
            if at_high:
                g["leaders"] += 1

    # render leaders-first, then by supersector name — the leading theme sits at the top
    ordered = sorted(groups.items(), key=lambda kv: (-kv[1]["leaders"], _pretty(kv[0])))
    metrics: list[DisplayMetric] = []
    for ss, g in ordered:
        parts = [f"{g['leaders']}/{g['counted']} leading"]
        if g["thin"]:
            parts.append(f"{g['thin']} thin")
        metrics.append(
            DisplayMetric(
                key=f"rs_lead_{ss or 'unclassified'}",
                label=_pretty(ss),
                value=float(g["leaders"]),
                unit="count",
                note=", ".join(parts),
            )
        )

    counted_total = sum(g["counted"] for g in groups.values())
    basis = DisplayBasis(
        source="fact_price_eod",
        params={
            "benchmark": benchmark,
            "rs_high_weeks": RS_HIGH_WEEKS,
            "min_bars": MIN_BARS,
            "lookback_days": LOOKBACK_DAYS,
            "members_resolved": len(security_ids),
        },
        bars_used=counted_total or None,
    )
    return DisplaySignal(
        kind=KIND_SECTOR,
        label=LABEL_SECTOR,
        headline=_sector_headline(ordered, counted_total),
        metrics=metrics,
        basis=basis,
    )


def _sector_headline(
    ordered: list[tuple[str | None, dict[str, int]]], counted_total: int
) -> DisplayHeadline:
    """LOUD only when a supersector is actually leading (#7): names the leading theme(s); else states the
    quiet tape; ``unknown`` when every member is too thin for a verdict."""
    if counted_total == 0:
        return DisplayHeadline(key="unknown", label="Sector RS n/a — thin history", glyph=None)
    leaders = [(ss, g) for ss, g in ordered if g["leaders"] > 0]
    if leaders:
        top = ", ".join(_pretty(ss) for ss, _ in leaders)
        return DisplayHeadline(
            key="leading",
            label=f"Leading: {top}",
            glyph="up",
            detail=f"{sum(g['leaders'] for _, g in leaders)} member(s) at a {RS_HIGH_WEEKS}-week RS high vs the market",
        )
    return DisplayHeadline(
        key="quiet",
        label="No sector leading the market",
        glyph=None,
        detail=f"no member at a {RS_HIGH_WEEKS}-week RS high across {counted_total} counted",
    )


# The READ-HORIZON declarations (``signals/horizons.py``). The per-name member reads the member's bars
# AND the benchmarks' bars at the same lookback (both ``fact_price_eod``); the thesis-level rollup
# (``sector_rs_for``, not a registered member) declares its own, identical need.
HORIZONS: dict[str, int | None] = {"fact_price_eod": LOOKBACK_DAYS}
SECTOR_RS_HORIZONS: dict[str, int | None] = {"fact_price_eod": LOOKBACK_DAYS}


MEMBER = register_display_member(
    DisplayMember(name=MEMBER_NAME, compute=display, horizons=HORIZONS)
)
