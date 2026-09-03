"""ETF net flow — is money actually moving into the sleeve's fund, or just its price moving.

The one number AUM cannot give you: an ETF's AUM rises when its price rises, but *flow* is creations
minus redemptions — the fund's OWN shares outstanding changing. Pure signed rollup over the sampled
share counts (``fact_fund_shares``): Δshares between consecutive stored samples, each delta priced at
that day's EOD close, summed over trailing 1-week and 1-month windows anchored at asof. Positive
Δshares = net creations = INFLOW; negative = redemptions = OUTFLOW; **shares flat ⇒ ZERO flow, no
matter what price, volume, or AUM did** (the three traps the golden tests pin). Display-only context
on the sleeve dossier — deliberately not a call input (promoting flow to a signal is F4, a separate
operator-signed slice); a non-ETF member has no samples and honestly renders nothing.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayHeadline,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "etf_flow"
LABEL = "Fund flow (Δshares × close)"
WINDOW_1W_DAYS = 7  # trailing calendar windows anchored at asof (the insider_flow_90d idiom)
WINDOW_1M_DAYS = 30


def _close_on_or_before(dates: list[date], closes: list[float], d: date) -> float | None:
    """The latest close ON OR BEFORE ``d`` (the standard as-of pricing convention — a weekend-dated
    sample prices at Friday's close). ``None`` when no bar precedes ``d`` at all."""
    i = bisect_right(dates, d)
    return closes[i - 1] if i else None


def _window(
    deltas: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    asof: date,
    window_days: int,
    label: str,
) -> tuple[DisplayMetric, DisplayMetric, float | None]:
    """One trailing window's two metrics (net flow $ + net Δshares as % of the window-start count),
    plus the net Δshares (the headline's sign carrier; ``None`` = the window has no honest value).

    A window states a value only when the WHOLE window is knowable: a BASELINE sample dated on/before
    the window start (so the full Δ is real, not a partial series) AND at least one sample inside the
    window (a series that stopped sampling is stale, not zero). Anything less is ``value=None`` with
    the why (#6/#7) — distinct from a true zero flow (two equal real samples ⇒ 0.0)."""
    start = asof - timedelta(days=window_days)
    baseline = next((s for s in reversed(samples) if s["d"] <= start), None)
    windowed = [dl for dl in deltas if start < dl["d"] <= asof]

    def na(note: str) -> tuple[DisplayMetric, DisplayMetric, None]:
        return (
            DisplayMetric(
                key=f"flow_{label}_usd", label=f"{label} net flow", unit="usd", note=note
            ),
            DisplayMetric(
                key=f"flow_{label}_pct_of_shares",
                label=f"{label} Δshares",
                unit="pct",
                note=note,
            ),
            None,
        )

    if baseline is None:
        sampled = sum(1 for s in samples if start < s["d"] <= asof)
        return na(
            f"n/a: {sampled}/{window_days} sampled days"
        )  # the series is younger than the window
    if not windowed:
        latest = samples[-1]["d"]
        return na(f"n/a: no sample in the last {window_days}d (latest {latest.isoformat()})")

    net_shares = sum(dl["dshares"] for dl in windowed)
    # the window's direction as a render tone — green inflow / red outflow; a true zero flow stays
    # neutral (no tint), the honest "flat" read (the bare minus was too quiet, per the operator)
    tone = "pos" if net_shares > 0 else "neg" if net_shares < 0 else None
    priced = [dl for dl in windowed if dl["usd"] is not None]
    unpriced = len(windowed) - len(priced)
    usd = DisplayMetric(
        key=f"flow_{label}_usd",
        label=f"{label} net flow",
        value=round(sum(dl["usd"] for dl in priced), 2),
        unit="usd",
        # an unpriced delta (no close on file up to its date) is excluded and SAID — never silently
        note=f"{unpriced} deltas unpriced (no close on file)" if unpriced else None,
        tone=tone,
    )
    base_shares = float(baseline["shares_out"])
    if base_shares > 0.0:
        pct = DisplayMetric(
            key=f"flow_{label}_pct_of_shares",
            label=f"{label} Δshares",
            value=round(net_shares / base_shares * 100.0, 2),
            unit="pct",
            tone=tone,
        )
    else:
        pct = DisplayMetric(
            key=f"flow_{label}_pct_of_shares",
            label=f"{label} Δshares",
            unit="pct",
            note="n/a: zero baseline shares",
        )
    return usd, pct, net_shares


def _headline(
    label: str, net_shares: float, pct: DisplayMetric, usd: DisplayMetric
) -> DisplayHeadline:
    """The sleeve chip's one-glance read — the window's DIRECTION in words, sized by % of shares when
    the baseline allowed one (else the $ figure). States the sampled tape, never a forecast (#4)."""
    if net_shares > 0:
        key, glyph, word = "net_inflow", "up", "INFLOW"
    elif net_shares < 0:
        key, glyph, word = "net_outflow", "down", "OUTFLOW"
    else:
        key, glyph, word = "net_flat", "flat", "flow flat"
    if pct.value is not None and net_shares != 0:
        size = f" {pct.value:+.1f}% of shares"
    elif usd.value is not None and net_shares != 0:
        size = f" ${usd.value:,.0f}"
    else:
        size = ""
    text = f"{label} net {word}{size}" if net_shares != 0 else f"{label} net flow flat"
    return DisplayHeadline(key=key, glyph=glyph, label=text)


def compute(
    samples: list[dict[str, Any]], bars: list[dict[str, Any]], asof: date
) -> DisplaySignal | None:
    """Pure flow rollup over shares-out samples (one per d — the deduped as-of read's shape) and EOD
    bars. ``None`` with no samples at all (every non-ETF member; an ETF before its first sample) —
    the honest nothing. With samples, the two windows degrade per-metric (#6/#7)."""
    samples = sorted((s for s in samples if s["d"] <= asof), key=lambda s: s["d"])
    if not samples:
        return None
    priced_bars = sorted(
        ((b["d"], float(b["close"])) for b in bars if b.get("close") is not None),
        key=lambda t: t[0],
    )
    bar_dates = [d for d, _ in priced_bars]
    bar_closes = [c for _, c in priced_bars]

    # Δshares between consecutive samples, each priced at the close on/before ITS date. A sparse gap
    # (missed sampler days) lumps the whole move at the later sample's close — the honest best read of
    # a sampled series; the basis carries sample_count so the resolution is visible.
    deltas: list[dict[str, Any]] = []
    for prev, cur in zip(samples, samples[1:]):
        dshares = float(cur["shares_out"]) - float(prev["shares_out"])
        close = _close_on_or_before(bar_dates, bar_closes, cur["d"])
        deltas.append(
            {
                "d": cur["d"],
                "dshares": dshares,
                "usd": dshares * close if close is not None else None,
            }
        )

    usd_1w, pct_1w, net_1w = _window(deltas, samples, asof, WINDOW_1W_DAYS, "1w")
    usd_1m, pct_1m, net_1m = _window(deltas, samples, asof, WINDOW_1M_DAYS, "1m")

    # the chip prefers the 1m read (the steadier window); 1w fills in while 1m still accrues
    if net_1m is not None:
        headline = _headline("1m", net_1m, pct_1m, usd_1m)
    elif net_1w is not None:
        headline = _headline("1w", net_1w, pct_1w, usd_1w)
    else:
        headline = None  # both windows still accruing — quiet n/a metrics only (#7)

    sources = sorted({s["source"] for s in samples if s.get("source")})
    note = f"sampled from {', '.join(sources)}" if sources else None
    if note and "stockanalysis" in sources:
        note += " (stockanalysis counts are ~10k-share rounded)"
    basis = DisplayBasis(
        source="fact_fund_shares",
        params={
            "window_1w_days": WINDOW_1W_DAYS,
            "window_1m_days": WINDOW_1M_DAYS,
            "priced_with": "fact_price_eod close on/before each sample date",
            "sample_count": len(samples),
            # the latest sample's exact page (#6) — the URL the newest count traces to
            "source_ref": samples[-1].get("source_ref"),
        },
        bars_used=len(samples),  # samples, not price bars — the series the reading stands on
        window_start=samples[0]["d"],
        window_end=samples[-1]["d"],  # the latest sample — the staleness tell
        note=note,
    )
    return DisplaySignal(
        kind=MEMBER_NAME,
        label=LABEL,
        headline=headline,
        metrics=[usd_1w, pct_1w, usd_1m, pct_1m],
        basis=basis,
    )


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read the sampled share counts + EOD closes via the point-in-time view; all arithmetic in the
    pure ``compute``. Both reads are the SAME bitemporal as-of every member uses — a replay pinned
    before the first sample honestly sees nothing (#1)."""
    return compute(pit.fund_shares(security_id), pit.price_history(security_id), asof)


# The READ-HORIZON declaration (``signals/horizons.py``).
# - ``fact_fund_shares`` is UNBOUNDED (``None``): the whole sampled series is the basis (the baseline
#   sample on/before a window start may be any age).
# - ``fact_price_eod`` at the LONGER trailing window (1 month): a delta is priced at "the close on/before
#   its sample date", and only deltas inside ``(asof - WINDOW_1M_DAYS, asof]`` reach a metric, so the
#   member's TRUE need is one close on/before each in-window sample date — normally that day's or the
#   prior day's bar. The one edge, NAMED not silent: a fund whose price tape has gone stale by more than
#   (the derived display bound - this window) days — today ~600 d — prices such a delta as
#   "unpriced (no close on file)", the honest note ``_window`` already carries, never a wrong number.
HORIZONS: dict[str, int | None] = {"fact_fund_shares": None, "fact_price_eod": WINDOW_1M_DAYS}


MEMBER = register_display_member(
    DisplayMember(name=MEMBER_NAME, compute=display, horizons=HORIZONS)
)
