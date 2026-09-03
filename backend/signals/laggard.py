"""Laggard rotation (§1.2) — a cross-member sympathy CONFIRMATION at the thesis level.

When a basket LEADER breaks out (a live ``volume_breakout`` in the assembled member stream), a
co-basket name that is LAGGING the basket's move but still in a structural uptrend is the sympathy
catch-up candidate. This detector emits a flip-grade ``Kind.LAGGARD`` confirmation on each such
laggard; because ``LAGGARD`` is a ``confirmation_kind`` (``domain/config.py``), the assembler's
existing co-location (conv ∩ conf) ARMS a laggard that carries an OWN conviction but hasn't broken
out itself (R3/R4). It never fires or arms on its own — it is a Key-2 confirmation exactly like
``volume_breakout``; arming stays the assembler's job.

Thesis-level, run AFTER the per-member loop (``pipeline/core.py``) because leader detection reads the
assembled member event stream — the same seam ``theme_conviction.broadcast`` runs on. Pure:
``f(pit, thesis, member_events, asof, cfg) -> list[SignalEvent]``; reuses the EOD bars + the member
stream, adds NO ingest, NO new fact table, NO new ``Kind``.

Deferential on thesis, opinionated on timing (#4): it only times a sympathy entry on a name the
operator already curated into the basket, never judges whether the idea is good, and cannot reach
outside the basket. #9: a member with too little history to assess is simply not fired on (a call
trigger declines without evidence) — never dropped from any universe.
"""

from __future__ import annotations

from datetime import date
from statistics import median
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import SignalEvent
from domain.thesis import Thesis
from signals.base import SignalPointInTimeData
from signals.common import fired_signal, source_provenance

DETECTOR_NAME = "laggard"


def HORIZONS(
    cfg: CallConfig,
) -> dict[str, int | None]:  # noqa: N802 — the registry's module-level name
    """The READ-HORIZON declaration (``signals/horizons.py``) for this THESIS-LEVEL reader (not a
    registered per-security detector, so it declares here): exactly the lookback ``detect`` passes to
    ``price_history`` for every basket member."""
    return {"fact_price_eod": cfg.laggard_lookback_days}


def _return(closes: list[float], days: int) -> float | None:
    """Close-to-close return over ``days`` TRADING bars (``close_now / close_{days back} − 1``) — the
    same window convention as ``signals.display.trailing_returns``. ``None`` (an honest gap, never a
    fabricated 0) when the tape is shorter than ``days + 1`` bars or the base close is non-positive.
    """
    if len(closes) < days + 1:
        return None
    base = closes[-(days + 1)]
    if base <= 0.0:
        return None
    return closes[-1] / base - 1.0


def _sma(closes: list[float], window: int) -> float | None:
    """Simple mean of the last ``window`` closes (the SAME arithmetic as ``signals.display.sma``), or
    ``None`` when fewer than ``window`` bars exist."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _score(ret: float, med: float, cfg: CallConfig) -> float:
    """A modest flip-confirmation score scaled by the catch-up GAP to the basket (``median − ret``),
    floored so a qualifying laggard reads as a real confirmation and capped BELOW a volume-backed
    breakout. STARTING calibration, never precision; the entry grade is flip anyway (capped at
    starter), so the exact number is not load-bearing."""
    gap = max(med - ret, 0.0)
    return round(min(0.45 + gap, 0.7), 4)


def detect(
    pit: SignalPointInTimeData,
    thesis: Thesis,
    member_events: list[SignalEvent],
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> list[SignalEvent]:
    """Emit a flip ``Kind.LAGGARD`` confirmation for each lagging-but-uptrend basket member while a
    DIFFERENT member has a live breakout (R3). Pure over the point-in-time view + the assembled member
    stream."""
    # Leaders = members with a live volume_breakout in the stream. A present, fired breakout event is
    # already live: ``volume_breakout`` only reports a breakout still inside its alpha-liveness window,
    # so presence == live (the same discipline ``theme_conviction.broadcast`` relies on). The freshest
    # leader breakout bar anchors the laggard's fire date (fire-date-anchored, so its clock never slides).
    breakouts = [e for e in member_events if e.fired and e.kind is Kind.TECHNICAL_BREAKOUT]
    if not breakouts:
        return []
    leader_secs = {e.security_id for e in breakouts}
    leader_date = max(e.asof for e in breakouts)

    # One price read per resolved member -> its trailing return (for the basket median) + its trend SMA.
    per_member: dict[UUID, dict[str, float | None]] = {}
    for member in thesis.basket:
        sid = member.security_id
        if sid is None or sid in per_member:
            continue  # unresolved / duplicate — never a None-keyed reading (recall-safe, #9)
        bars = pit.price_history(sid, lookback_days=cfg.laggard_lookback_days)
        closes = [float(b["close"]) for b in bars if b.get("close") is not None]
        per_member[sid] = {
            "ret": _return(closes, cfg.laggard_return_days),
            "sma": _sma(closes, cfg.laggard_trend_sma_window),
            "close": closes[-1] if closes else None,
        }

    rets = [m["ret"] for m in per_member.values() if m["ret"] is not None]
    if not rets:
        return []
    med = median(rets)
    threshold = med - cfg.laggard_lag_pts  # lag the basket median by >= laggard_lag_pts to qualify

    events: list[SignalEvent] = []
    for sid, m in per_member.items():
        if sid in leader_secs:
            continue  # this name has run (its own breakout) — a leader, not a laggard
        ret, sma, close = m["ret"], m["sma"], m["close"]
        if ret is None or ret >= threshold:
            continue  # can't assess the lag, or not lagging the basket enough
        if sma is None or close is None or close < sma:
            continue  # uptrend not intact / unknown — a call trigger declines without evidence (#4/#9)
        events.append(_event(sid, ret, med, threshold, sma, close, leader_date, cfg))
    return events


def _event(
    sid: UUID,
    ret: float,
    med: float,
    threshold: float,
    sma: float,
    close: float,
    leader_date: date,
    cfg: CallConfig,
) -> SignalEvent:
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=sid,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.LAGGARD,
        grade=Grade.FLIP,  # a fast sympathy confirmation — never a core (R4)
        score=_score(ret, med, cfg),
        label=(
            f"Laggard rotation: {ret * 100:.0f}% over {cfg.laggard_return_days}d trails the basket "
            f"median {med * 100:.0f}% by ≥{cfg.laggard_lag_pts * 100:.0f}pts, uptrend intact "
            f"(≥{cfg.laggard_trend_sma_window}d SMA) — a co-basket leader's breakout is the cue"
        ),
        alpha_liveness_days=cfg.laggard_alpha_liveness_days,
        provenance=[
            source_provenance(
                "price",
                f"price:{sid}:{leader_date.isoformat()}",
                detail={
                    "ret": round(ret, 4),
                    "median": round(med, 4),
                    "threshold": round(threshold, 4),
                    "sma": round(sma, 4),
                    "close": round(close, 4),
                    "return_days": cfg.laggard_return_days,
                    "trend_sma_window": cfg.laggard_trend_sma_window,
                    "lag_pts": cfg.laggard_lag_pts,
                    "leader_breakout_date": leader_date.isoformat(),
                },
            )
        ],
        asof=leader_date,
    )
