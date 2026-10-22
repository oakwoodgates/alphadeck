"""The split-night hold's ESCALATION (split plan S5, rule 3; ``docs/CALL_LOGIC.md`` §2b): a split-shaped price
jump that the hold has RELEASED (``domain.split_hold.HOLD_SESSIONS`` sessions passed) and that no source ever
listed — no split-list row of any status near it. It goes to the operator as a "split-shaped jump with no split
listed — is this real?" item on the Admin Splits panel, plus one quiet line in the nightly report. Never a page
(#7): a genuine crash on heavy volume has the same shape as an unadjusted forward split, and only the operator
can tell them apart.

**A read, never a write, never a call input.** It changes no card (the hold itself is the call's — ``signals/
split_hold.py``); it lists what the operator may want to look at. The read is the hold's own: one
``PointInTimeData`` on the PUBLIC clock over the active basket union (``price_history`` + ``split_events``, the
same accessors and the same pure rule), so the panel judges a step exactly as the cards did. For today's asof
the public clock IS the live read (``known_at_for_asof(today)`` = now).

What it does NOT show: Polygon's adjusted bars. The split plan proposed them as an independent second view, but
no Polygon aggregates client exists (only the split reference), and a request per jump would spend the shared
free-tier budget on a read the operator did not ask for (the cost thread). Named, not faked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg

from domain.market_time import known_at_for_asof
from domain.split_hold import SplitStep, find_steps, sessions_since, unlisted
from signals.base import PointInTimeData

# How far back a released, unlisted jump stays on the operator's list (calendar days before the asof). A month
# keeps a step in view for about 20 sessions after its hold ends — long enough to be seen on a weekly look —
# and lets a genuine move age out instead of standing forever. PROPOSED (not an operator-set number).
WINDOW_DAYS = 30
# the bars shown after the step (the panel's "what happened next")
AFTER_BARS = 5

_UNION = """
    SELECT DISTINCT bm.security_id, sm.ticker
      FROM basket_member bm
      JOIN thesis t ON t.id = bm.thesis_id
      JOIN security_master sm ON sm.id = bm.security_id
     WHERE t.archived_at IS NULL AND bm.security_id IS NOT NULL AND bm.tenant_id = %s
"""


@dataclass(frozen=True)
class UnlistedJump:
    """One released split-shaped jump no source listed: the step and the bars after it (date, close, volume)."""

    security_id: UUID
    ticker: str | None
    step: SplitStep
    sessions_since: int
    after: tuple[tuple[date, float | None, float | None], ...]

    @property
    def label(self) -> str:
        """ "BTOC stepped x15.8 on 10-15" — the report / panel line."""
        return f"{self.ticker or self.security_id} {self.step.describe()}"


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def jumps_for(
    security_id: UUID,
    ticker: str | None,
    bars: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    *,
    asof: date,
) -> list[UnlistedJump]:
    """The released, unlisted jumps on one company's tape (pure over the view's bars + split rows)."""
    out: list[UnlistedJump] = []
    for step in find_steps(bars):
        if (asof - step.d).days > WINDOW_DAYS or not unlisted(step, split_rows, asof=asof):
            continue
        after = tuple(
            (b["d"], _num(b.get("close")), _num(b.get("volume"))) for b in bars if b["d"] > step.d
        )[:AFTER_BARS]
        out.append(
            UnlistedJump(
                security_id=security_id,
                ticker=ticker,
                step=step,
                sessions_since=sessions_since(step, asof),
                after=after,
            )
        )
    return out


def scan_unlisted_jumps(
    conn: psycopg.Connection,
    *,
    asof: date,
    tenant_id: UUID,
    known_at: datetime | None = None,
) -> tuple[UnlistedJump, ...]:
    """Every released, unlisted split-shaped jump on the active basket union, as of ``asof`` on the public clock
    (``known_at`` defaults to the end of the asof's market day, capped at now). SELECTs only; ordered by step
    date (newest first), then ticker."""
    with conn.cursor() as cur:
        cur.execute(_UNION, (tenant_id,))
        union = {r["security_id"]: r["ticker"] for r in cur.fetchall()}
    if not union:
        return ()
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at or known_at_for_asof(asof),
        tenant_id=tenant_id,
        basket=union,
        bounds={"fact_price_eod": WINDOW_DAYS + 10},
        clock="public",
    )
    out: list[UnlistedJump] = []
    for sid, ticker in union.items():
        bars = pit.price_history(sid, lookback_days=WINDOW_DAYS + 10)
        out.extend(jumps_for(sid, ticker, bars, pit.split_events(sid), asof=asof))
    out.sort(key=lambda j: (j.step.d, j.ticker or ""), reverse=True)
    return tuple(out)


@dataclass
class JumpScan:
    """One nightly pass's escalation read: ``jumps`` when it ran, ``error`` when it could not (fail-open for the
    pass — a fault is one report line, never a failed night)."""

    evaluated: bool = False
    jumps: tuple[UnlistedJump, ...] = field(default_factory=tuple)
    error: str | None = None


def run_jump_scan(
    *,
    asof: date,
    allow_live: bool,
    tenant_id: UUID,
    connect: Callable[[], psycopg.Connection],
) -> JumpScan:
    """The nightly unit: its own read-only connection; a ``--no-live`` pass does not scan (it re-pulled no
    tapes). Never raises."""
    if not allow_live:
        return JumpScan()
    try:
        conn = connect()
        try:
            conn.read_only = True
            jumps = scan_unlisted_jumps(conn, asof=asof, tenant_id=tenant_id)
            conn.rollback()
        finally:
            conn.close()
    except (
        Exception
    ) as e:  # noqa: BLE001 — an escalation read fault is reported, never a failed pass
        msg = " ".join(str(e).split())[:200]
        return JumpScan(error=f"{type(e).__name__}: {msg}" if msg else type(e).__name__)
    return JumpScan(evaluated=True, jumps=jumps)


def report_lines(scan: JumpScan) -> list[str]:
    """ONE quiet line (lowercase, the known-defect casing convention) when any released jump is unlisted, or
    when the read failed; nothing on a clean or un-scanned night (loudness marks the exception, WB#3).
    """
    if scan.error:
        return [
            f"split-shaped jump check failed — {scan.error} (the Admin Splits panel reads it live)"
        ]
    if not scan.evaluated or not scan.jumps:
        return []
    shown = ", ".join(j.label for j in scan.jumps[:5])
    more = f" (+{len(scan.jumps) - 5} more)" if len(scan.jumps) > 5 else ""
    return [
        f"{len(scan.jumps)} split-shaped jump(s) past the split-night hold with no split listed — is this "
        f"real? (Admin → Splits): {shown}{more}"
    ]
