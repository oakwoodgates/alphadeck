"""The Admin "Splits" surface (split plan S1) — the operator's ratification of the split list the backfill
filled, every action with a visible inverse (WB#1).

What the system found (``pipeline.backfill_splits``; read, never auto-decided beyond the operator's own rule —
two independent sources agreeing): each split with Polygon's row and the SEC filing's located passage side by
side. Auto-confirmed and announced splits are QUIET lists; a split one source cannot settle (one source only, a
disagreement, an extractor abstain) is LOUD — the only loud thing here (WB#3).

What the operator can do, and its inverse:

- **Confirm SEC values** / **Confirm Polygon values** (when they differ), or **Confirm** (when only one source
  states both a ratio and a date) a needs-review split -> its values become the list's. Inverse: **Revoke**.
- **Reject** a needs-review or announced split. Inverse: **Restore** (back to the state before).
- **Revoke** any confirmed split, auto-confirmed or not. Inverse: **Restore** (back to confirmed).

Rejected and revoked splits stay listed, grayed (WB#2). Every action appends one version naming the operator
(``repositories/split_repo.py``) and names the version it acts on, so a stale card is refused (409), never
applied over a newer decision. No push notification is ever sent from here. What reads the list: the nightly
price leg and the repair tool keep the stored tape on its scale (S2), the insider screen and the market cap read
its factors (S2), and the split-night hold (S5) holds a company's price triggers across a split-shaped step no
confirmed split covers — so a confirm here lifts that hold the next night. Auth stays deferred.

**Split-shaped jumps with no split listed (S5)** — the panel also lists the price jumps the split-night hold
has released (three sessions on) that no source ever listed (``pipeline.split_jumps``): the operator's "is this
real?". A list, never an action and never a page (#7).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_conn, get_current_tenant
from db.session import current_tenant_id
from domain.market_time import market_today, market_tz
from domain.splits import (
    SET_ASIDE,
    SOURCE_POLYGON,
    SOURCE_SEC,
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_AUTO,
    VIA_POLYGON_TAPE,
    VIA_SEC_TAPE,
    direction,
    ratio_label,
)
from pipeline.split_jumps import UnlistedJump, scan_unlisted_jumps
from repositories import split_repo
from repositories.split_repo import SplitConflict, SplitVersion

router = APIRouter(prefix="/admin/splits", tags=["admin"])


def _num(v: object) -> str | None:
    """An exact ratio number as text ("16", "6.25") — never a float's rounding."""
    if v is None:
        return None
    return format(v.normalize(), "f")  # type: ignore[attr-defined]


class AdminSplitListSecOut(BaseModel):
    """The SEC filing behind a split: the extractor's reading and the LOCATED passage, verbatim.
    ``date_kind`` = ``trading`` (the filing states the first session) or ``legal`` (a legal effective date);
    ``abstain_reason`` = why no single ratio was read."""

    accession: str
    form: str | None = None
    filed: date | None = None
    url: str | None = None
    passage: str | None = None
    document: str | None = None
    effective_date: date | None = None
    date_kind: str | None = None
    ratio_from: str | None = None
    ratio_to: str | None = None
    ratio_label: str | None = None
    abstain_reason: str | None = None


class AdminSplitListPolygonOut(BaseModel):
    """Polygon's split-reference row: ``split_from`` old shares -> ``split_to`` new shares."""

    id: str
    ticker: str | None = None
    execution_date: date | None = None
    split_from: str | None = None
    split_to: str | None = None
    ratio_label: str | None = None


class AdminSplitListOptionOut(BaseModel):
    """One value the operator can confirm a needs-review split with."""

    source: Literal["sec", "polygon"]
    ratio_from: str
    ratio_to: str
    effective_date: date
    label: str


class AdminSplitListEntryOut(BaseModel):
    """One split on the list (its NEWEST version). ``version_id`` names that version — every action posts it,
    and a stale one is refused. ``ratio_from`` -> ``ratio_to`` = old shares -> new shares; ``price_factor`` =
    old / new (prices before the split x it). ``decided_by`` is the source label the panel shows ("auto: SEC +
    Polygon", "you, 10-20"). ``actions`` = what the status allows; ``restore_to`` = where Restore returns it.
    """

    version_id: UUID
    security_id: UUID
    ticker: str | None = None
    name: str | None = None
    key_date: date
    effective_date: date
    ratio_from: str
    ratio_to: str
    price_factor: str
    direction: str
    label: str
    status: str
    confirmed_via: str | None = None
    value_source: str
    author: str
    action: str
    recorded_at: datetime
    decided_by: str
    valid_from: date
    note: str | None = None
    sec: AdminSplitListSecOut | None = None
    polygon: AdminSplitListPolygonOut | None = None
    confirm_options: list[AdminSplitListOptionOut] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    restore_to: str | None = None


class AdminSplitJumpBarOut(BaseModel):
    """One stored bar after a split-shaped jump (what the price did next)."""

    d: date
    close: float | None = None
    volume: float | None = None


class AdminSplitJumpOut(BaseModel):
    """A split-shaped price jump with NO split listed (split plan S5): the close moved x3 or more (or to a third
    or less) between two stored bars with the volume moving the other way, the split-night hold has released it
    (three sessions on), and no SEC filing or Polygon row on the list sits near it. A real move or a split no
    source has listed yet — the operator's question ("is this real?"). ``step`` = close / prior close.
    """

    security_id: UUID
    ticker: str | None = None
    prior_date: date
    step_date: date
    prior_close: float
    close: float
    prior_volume: float
    volume: float
    step: float
    label: str
    sessions_since: int
    after: list[AdminSplitJumpBarOut] = Field(default_factory=list)


class AdminSplitListOut(BaseModel):
    """The "Splits" panel. ``needs_review`` is the one LOUD list (one source could not settle it);
    ``announced`` (both sources agree, the first session is still ahead), ``confirmed`` and ``set_aside``
    (rejected / revoked — kept, grayed) are QUIET lists. ``total`` = every split on the list. Read live.
    ``unlisted_jumps`` (S5) — split-shaped jumps on basket names that the split-night hold has released and no
    source ever listed, the last 30 days; ``unlisted_jumps_error`` when that read failed (shown, never hidden).
    """

    needs_review: list[AdminSplitListEntryOut] = Field(default_factory=list)
    announced: list[AdminSplitListEntryOut] = Field(default_factory=list)
    confirmed: list[AdminSplitListEntryOut] = Field(default_factory=list)
    set_aside: list[AdminSplitListEntryOut] = Field(default_factory=list)
    total: int = 0
    unlisted_jumps: list[AdminSplitJumpOut] = Field(default_factory=list)
    unlisted_jumps_error: str | None = None


class AdminSplitListConfirmIn(BaseModel):
    """Whose values to confirm: the SEC filing's or Polygon's."""

    source: Literal["sec", "polygon"]


def _md(ts: datetime) -> str:
    return ts.astimezone(market_tz()).strftime("%m-%d")


# the two-source pairings a split can be auto-confirmed by (S1's SEC + Polygon; S4's tape pairings)
_AUTO_LABELS = {
    VIA_AUTO: "auto: SEC + Polygon",
    VIA_SEC_TAPE: "auto: SEC + tape",
    VIA_POLYGON_TAPE: "auto: Polygon + tape",
}


def _decided_by(v: SplitVersion) -> str:
    """The source label: "auto: SEC + Polygon" (or "auto: SEC + tape" / "auto: Polygon + tape") for the
    two-source rule, "you, 10-20" for the operator, else who listed it (the backfill / the nightly).
    """
    auto = _AUTO_LABELS.get(v.confirmed_via or "")
    if v.author != split_repo.AUTHOR_OPERATOR:
        if auto:
            return auto
        return "the nightly" if v.author == split_repo.AUTHOR_DETECT else "the backfill"
    verb = {
        "confirm": "you",
        "reject": "rejected by you",
        "revoke": "revoked by you",
        "restore": "restored by you",
    }.get(v.action, "you")
    return f"{auto + ' · ' if auto else ''}{verb}, {_md(v.recorded_at)}"


def split_out(v: SplitVersion, *, restore_to: str | None = None) -> AdminSplitListEntryOut:
    options = []
    for src in split_repo.confirm_options(v):
        vals = split_repo.confirm_values(v, src)
        assert vals is not None
        options.append(
            AdminSplitListOptionOut(
                source=src,  # type: ignore[arg-type]
                ratio_from=_num(vals[0]) or "",
                ratio_to=_num(vals[1]) or "",
                effective_date=vals[2],
                label=f"{ratio_label(vals[0], vals[1])}, first session {vals[2].isoformat()}",
            )
        )
    sec = (
        AdminSplitListSecOut(
            accession=v.sec_accession,
            form=v.sec_form,
            filed=v.sec_filed,
            url=v.sec_url,
            passage=v.sec_passage,
            document=v.sec_document,
            effective_date=v.sec_effective_date,
            date_kind=v.sec_date_kind,
            ratio_from=_num(v.sec_ratio_from),
            ratio_to=_num(v.sec_ratio_to),
            ratio_label=(
                ratio_label(v.sec_ratio_from, v.sec_ratio_to)
                if v.sec_ratio_from is not None and v.sec_ratio_to is not None
                else None
            ),
            abstain_reason=v.sec_abstain_reason,
        )
        if v.sec_accession
        else None
    )
    poly = (
        AdminSplitListPolygonOut(
            id=v.polygon_id,
            ticker=v.polygon_ticker,
            execution_date=v.polygon_execution_date,
            split_from=_num(v.polygon_split_from),
            split_to=_num(v.polygon_split_to),
            ratio_label=(
                ratio_label(v.polygon_split_from, v.polygon_split_to)
                if v.polygon_split_from is not None and v.polygon_split_to is not None
                else None
            ),
        )
        if v.polygon_id
        else None
    )
    return AdminSplitListEntryOut(
        version_id=v.id,
        security_id=v.security_id,
        ticker=v.ticker,
        name=v.name,
        key_date=v.key_date,
        effective_date=v.effective_date,
        ratio_from=_num(v.ratio_from) or "",
        ratio_to=_num(v.ratio_to) or "",
        price_factor=_num(v.price_factor) or "",
        direction=direction(v.ratio_from, v.ratio_to),
        label=ratio_label(v.ratio_from, v.ratio_to),
        status=v.status,
        confirmed_via=v.confirmed_via,
        value_source=v.value_source,
        author=v.author,
        action=v.action,
        recorded_at=v.recorded_at,
        decided_by=_decided_by(v),
        valid_from=v.valid_from,
        note=v.note,
        sec=sec,
        polygon=poly,
        confirm_options=options,
        actions=split_repo.allowed_actions(v),
        restore_to=restore_to,
    )


def jump_out(j: UnlistedJump) -> AdminSplitJumpOut:
    s = j.step
    return AdminSplitJumpOut(
        security_id=j.security_id,
        ticker=j.ticker,
        prior_date=s.prior_d,
        step_date=s.d,
        prior_close=s.prior_close,
        close=s.close,
        prior_volume=s.prior_volume,
        volume=s.volume,
        step=s.step,
        label=j.label,
        sessions_since=j.sessions_since,
        after=[AdminSplitJumpBarOut(d=d, close=c, volume=v) for d, c, v in j.after],
    )


def splits_out(
    conn: psycopg.Connection, *, tenant_id: UUID, asof: date | None = None
) -> AdminSplitListOut:
    """The panel, read live from the list's newest versions — plus the released, unlisted split-shaped jumps
    as of ``asof`` (default: today's market day; ``pipeline.split_jumps``). SELECTs only."""
    versions = split_repo.latest_versions(conn, tenant_id=tenant_id)
    out = AdminSplitListOut(total=len(versions))
    try:
        jumps = scan_unlisted_jumps(conn, asof=asof or market_today(), tenant_id=tenant_id)
        out.unlisted_jumps = [jump_out(j) for j in jumps]
    except (
        Exception
    ) as e:  # noqa: BLE001 — a failed jump read is shown on the panel, never a failed panel
        conn.rollback()
        out.unlisted_jumps_error = f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"
    for v in versions:
        o = split_out(
            v,
            restore_to=split_repo.prior_status(conn, v) if v.status in SET_ASIDE else None,
        )
        if v.status == STATUS_NEEDS_REVIEW:
            out.needs_review.append(o)
        elif v.status == STATUS_ANNOUNCED:
            out.announced.append(o)
        elif v.status == STATUS_CONFIRMED:
            out.confirmed.append(o)
        else:
            out.set_aside.append(o)
    newest_first = lambda o: (o.effective_date, o.ticker or "")  # noqa: E731
    out.needs_review.sort(key=newest_first, reverse=True)
    out.confirmed.sort(key=newest_first, reverse=True)
    out.set_aside.sort(key=newest_first, reverse=True)
    out.announced.sort(key=newest_first)
    return out


def count_needs_review(conn: psycopg.Connection, *, tenant_id: UUID | None = None) -> int:
    """The Admin status count ("N splits need review") for this deployment's tenant. SELECTs only."""
    return split_repo.count_needs_review(conn, tenant_id=tenant_id or current_tenant_id())


def _commit(conn: psycopg.Connection, fn: Callable[[], SplitVersion]) -> SplitVersion:
    """One decision in its own transaction: commit on success; on ANY failure roll back and map the
    repository's errors to the operator-facing status (404 unknown, 409 conflict, 422 invalid)."""
    try:
        out = fn()
        conn.commit()
        return out
    except LookupError as e:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SplitConflict as e:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except BaseException:
        conn.rollback()
        raise


def _out(conn: psycopg.Connection, v: SplitVersion) -> AdminSplitListEntryOut:
    fresh = split_repo.get_version(conn, v.id) or v
    try:
        return split_out(
            fresh,
            restore_to=split_repo.prior_status(conn, fresh) if fresh.status in SET_ASIDE else None,
        )
    finally:
        conn.rollback()  # a read: hold no snapshot


@router.get("", response_model=AdminSplitListOut)
def list_splits(
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> AdminSplitListOut:
    """Every split on the list (its newest version) with both sources' evidence, bucketed for the panel:
    needs review (loud), announced / confirmed / rejected-or-revoked (quiet) — and the split-shaped price jumps
    on basket names that no source listed, after the split-night hold released them (the operator's "is this
    real?"). Read-only."""
    try:
        return splits_out(conn, tenant_id=tenant_id)
    finally:
        conn.rollback()


@router.post("/{version_id}/confirm", response_model=AdminSplitListEntryOut)
def confirm_split(
    version_id: UUID,
    body: AdminSplitListConfirmIn,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> AdminSplitListEntryOut:
    """CONFIRM a split that needs review with one source's values (``source`` = ``sec`` | ``polygon``): its
    ratio and its first session become the list's (a first session still ahead lands as ``announced``). Undo
    with ``POST /admin/splits/{version_id}/revoke``. **404** unknown version, **409** not the split's newest
    version / not awaiting review / that source states no complete value / another split is listed on that
    date."""
    v = _commit(
        conn,
        lambda: split_repo.confirm(
            conn,
            version_id,
            source=SOURCE_SEC if body.source == "sec" else SOURCE_POLYGON,
            tenant_id=tenant_id,
            today=market_today(),
        ),
    )
    return _out(conn, v)


@router.post("/{version_id}/reject", response_model=AdminSplitListEntryOut)
def reject_split(
    version_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> AdminSplitListEntryOut:
    """REJECT a split that needs review (or an announced one): it stays listed, grayed. Undo with
    ``POST /admin/splits/{version_id}/restore``. **404** unknown version, **409** not the newest version / a
    confirmed split (revoke it instead)."""
    v = _commit(conn, lambda: split_repo.reject(conn, version_id, tenant_id=tenant_id))
    return _out(conn, v)


@router.post("/{version_id}/revoke", response_model=AdminSplitListEntryOut)
def revoke_split(
    version_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> AdminSplitListEntryOut:
    """REVOKE a confirmed split, auto-confirmed or not: it stays listed, grayed. Undo with
    ``POST /admin/splits/{version_id}/restore``. **404** unknown version, **409** not the newest version /
    not confirmed."""
    v = _commit(conn, lambda: split_repo.revoke(conn, version_id, tenant_id=tenant_id))
    return _out(conn, v)


@router.post("/{version_id}/restore", response_model=AdminSplitListEntryOut)
def restore_split(
    version_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> AdminSplitListEntryOut:
    """RESTORE a rejected or revoked split to exactly the state before (a rejected split back to review, a
    revoked one back to confirmed). **404** unknown version, **409** not the newest version / not set
    aside."""
    v = _commit(conn, lambda: split_repo.restore(conn, version_id, tenant_id=tenant_id))
    return _out(conn, v)
