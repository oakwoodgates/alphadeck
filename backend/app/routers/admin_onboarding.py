"""The Admin "Onboarding" surface (X1) — the staged cold-ingest queue, read-only but for one action.

What it shows (``pipeline/onboarding.readout``): how many names are queued / in progress / done / failed (and
PARKED — queued for a name no live basket holds any more), the pending names with their per-leg checkpoint, the
latest done ones, the latest job run and the nightly window the job yields to. QUIET unless a name FAILED (its
history legs failed ``Settings.onboard_max_attempts`` runs) or the queue STALLED (names waiting and no job run
finished for ``Settings.onboard_stalled_after_h``) — the only loud things here (WB#3).

The one action: **Retry** a failed name (back to ``queued``, its attempts reset, its checkpoint kept). Its
inverse is the job itself — a retried name either loads or fails again, visibly. Nothing here writes a fact or
a call; the job does, outside the nightly window. Auth stays deferred.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_conn, get_current_tenant
from app.schemas_api import OnboardingNameOut, OnboardingReadoutOut, OnboardingRunOut
from pipeline import onboarding

router = APIRouter(prefix="/admin/onboarding", tags=["admin"])


def _name_out(r: onboarding.OnboardingRow) -> OnboardingNameOut:
    return OnboardingNameOut(
        security_id=r.security_id,
        ticker=r.ticker,
        name=r.name,
        status=r.status,  # type: ignore[arg-type] — the table's CHECK holds it to the four values
        legs_done=list(r.legs_done),
        attempts=r.attempts,
        last_error=r.last_error,
        source=r.source,
        in_live_basket=r.in_live_basket,
        enqueued_at=r.enqueued_at,
        last_attempt_at=r.last_attempt_at,
        finished_at=r.finished_at,
    )


def _run_out(doc: dict | None) -> OnboardingRunOut | None:
    """The latest run artifact, or None when there is none or it cannot be read (fail-open, never a 500)."""
    if not doc:
        return None
    try:
        counts = doc.get("counts") or {}
        return OnboardingRunOut(
            started_at=datetime.fromisoformat(doc["started_at"]),
            finished_at=(
                datetime.fromisoformat(doc["finished_at"]) if doc.get("finished_at") else None
            ),
            status=str(doc.get("status") or "unknown"),
            run_kind=doc.get("run_kind"),
            stopped=doc.get("stopped"),
            detail=doc.get("detail"),
            counts={str(k): int(v) for k, v in counts.items()},
        )
    except Exception:  # noqa: BLE001 — a damaged artifact reads as "no run", never a failed page
        return None


def readout_out(r: onboarding.Readout) -> OnboardingReadoutOut:
    """``pipeline.onboarding.Readout`` -> the wire shape (shared by this router and the Workbench's
    per-thesis read)."""
    return OnboardingReadoutOut(
        counts=dict(r.counts),
        pending=[_name_out(x) for x in r.pending],
        recent_done=[_name_out(x) for x in r.recent_done],
        last_run=_run_out(r.last_run),
        window=r.window,
        stalled=r.stalled,
        paused=r.paused,
        history_legs=list(onboarding.HISTORY_LEGS),
    )


@router.get("", response_model=OnboardingReadoutOut)
def get_onboarding(
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> OnboardingReadoutOut:
    """The staged onboarding queue (X1): counts per status, the pending names with their per-leg checkpoint,
    the latest done ones, the latest job run, the nightly window the job yields to, and whether the queue
    STALLED. Read-only."""
    return readout_out(onboarding.readout(conn, tenant_id=tenant_id))


@router.post("/{security_id}/retry", response_model=OnboardingReadoutOut)
def retry_onboarding(
    security_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> OnboardingReadoutOut:
    """Re-queue a FAILED name (attempts reset, checkpoint kept); the job loads it on its next run outside the
    nightly window. 409 when the name is not failed (already queued, in progress or done); 404 when it is not
    in the queue. Returns the refreshed queue."""
    found = onboarding.rows(conn, tenant_id=tenant_id, security_ids=[security_id])
    if not found:
        raise HTTPException(status_code=404, detail="this name is not in the onboarding queue")
    if not onboarding.retry(conn, security_id, tenant_id=tenant_id):
        conn.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"only a failed name can be retried (this one is {found[0].status})",
        )
    conn.commit()
    return readout_out(onboarding.readout(conn, tenant_id=tenant_id))
