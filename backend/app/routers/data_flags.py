"""The shared "is this real?" DATA FLAGS (split plan S3) — two compute-on-read endpoints.

- ``GET /theses/{thesis_id}/data-flags?asof=`` — the Cockpit's read: per resolved basket member with a flag or
  a split on the list, its flags (which cells they reach, the reason, the evidence, since when, what clears
  it) and its split rows (the NamePanel's split section, the Scoreboard chart's split marker).
- ``GET /data-flags?asof=`` — every flagged name across the active basket union, each with the theses that
  hold it: the Admin list and the Board's ticker marks.

Display-only (``domain/data_flags.py``): computed on read on the view's clock (``serve_view`` — a past as-of
reads what was public by then, #1), never stored, never on the call card, never an input to it, and never a
push alert (#7). The rules are ``pipeline/data_flags.py``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.deps import get_conn, get_current_tenant, get_thesis_or_404
from domain.data_flags import DataFlag, MemberFlags, SplitLabel
from domain.market_time import serve_view
from domain.settings import get_settings
from domain.thesis import Thesis
from pipeline import data_flags as flags_reader
from pipeline.cron_run_log import carried_inventory, list_run_logs
from securities import master

router = APIRouter(tags=["data-flags"])


class DataFlagEvidenceOut(BaseModel):
    """One piece of evidence behind a flag: a label, and a link when the source has one (an SEC filing)."""

    label: str
    url: str | None = None


class DataFlagOut(BaseModel):
    """One data issue on one name, as of the view's day (``domain/data_flags.py``). ``level``: ``in_doubt``
    (the number is shown grayed, the reason on hover) or ``known_wrong`` (the number is not shown: "—" with
    the reason). ``columns`` = the Cockpit cells it reaches (empty = no number on the row is touched: shown in
    the panel and Admin only). ``dates`` = the bars the Scoreboard chart shades. ``call_affected`` = the
    split-night hold is holding this name's price triggers on the call card."""

    kind: str
    label: str
    level: Literal["in_doubt", "known_wrong"]
    reason: str
    clears: str
    since: date | None = None
    affects: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    dates: list[date] = Field(default_factory=list)
    evidence: list[DataFlagEvidenceOut] = Field(default_factory=list)
    call_affected: bool = False


class DataFlagSplitOut(BaseModel):
    """One split on the list as the NamePanel and the chart label it: announced (``sec_filed``), confirmed
    (``confirmed_by``), took effect (``took_effect``), history corrected (``corrected_on`` — only when Alpha
    Deck re-scaled the vendor's bars before it). ``factor`` = prices before ``effective_date`` x factor.
    ``bars_ago`` = how many bars the first bar at the new price sits before the view's newest bar.
    """

    key_date: date
    effective_date: date
    label: str
    factor: float
    direction: str
    status: str
    confirmed_via: str | None = None
    confirmed_by: str | None = None
    value_source: str | None = None
    sec_form: str | None = None
    sec_filed: date | None = None
    sec_url: str | None = None
    polygon: bool = False
    note: str | None = None
    took_effect: bool = False
    corrected_on: date | None = None
    corrected_note: str | None = None
    bars_ago: int | None = None


class DataFlagThesisRefOut(BaseModel):
    id: UUID
    name: str


class MemberDataFlagsOut(BaseModel):
    """One name's flags and split rows. ``marked`` = some flag reaches a cell on its row (the row mark)."""

    security_id: UUID
    ticker: str | None = None
    marked: bool = False
    flags: list[DataFlagOut] = Field(default_factory=list)
    splits: list[DataFlagSplitOut] = Field(default_factory=list)
    theses: list[DataFlagThesisRefOut] = Field(default_factory=list)


class DataFlagsResponse(BaseModel):
    """The data flags as of ``asof`` (computed on read, never stored). ``thesis_id`` is set on the per-thesis
    read (members with a flag or a split row) and null on the union read (flagged members only). ``names`` =
    how many names were judged."""

    thesis_id: UUID | None = None
    asof: date
    names: int = 0
    members: list[MemberDataFlagsOut] = Field(default_factory=list)


def _flag_out(f: DataFlag) -> DataFlagOut:
    return DataFlagOut(
        kind=f.kind,
        label=f.label,
        level=f.level,  # type: ignore[arg-type]
        reason=f.reason,
        clears=f.clears,
        since=f.since,
        affects=list(f.affects),
        columns=list(f.columns),
        dates=list(f.dates),
        evidence=[DataFlagEvidenceOut(label=e.label, url=e.url) for e in f.evidence],
        call_affected=f.call_affected,
    )


def _split_out(s: SplitLabel) -> DataFlagSplitOut:
    return DataFlagSplitOut(
        key_date=s.key_date,
        effective_date=s.effective_date,
        label=s.label,
        factor=s.factor,
        direction=s.direction,
        status=s.status,
        confirmed_via=s.confirmed_via,
        confirmed_by=s.confirmed_by,
        value_source=s.value_source,
        sec_form=s.sec_form,
        sec_filed=s.sec_filed,
        sec_url=s.sec_url,
        polygon=s.polygon,
        note=s.note,
        took_effect=s.took_effect,
        corrected_on=s.corrected_on,
        corrected_note=s.corrected_note,
        bars_ago=s.bars_ago,
    )


def _member_out(
    m: MemberFlags, theses: list[DataFlagThesisRefOut] | None = None
) -> MemberDataFlagsOut:
    return MemberDataFlagsOut(
        security_id=m.security_id,  # type: ignore[arg-type]
        ticker=m.ticker,
        marked=m.marked,
        flags=[_flag_out(f) for f in m.flags],
        splits=[_split_out(s) for s in m.splits],
        theses=theses or [],
    )


def _run_reads() -> tuple[dict, dict]:
    """The nightly's readings the flags borrow on TODAY's view (read from the run artifacts on disk, the same
    carry-forward the Admin price-feed panel renders) and the "history corrected" record (the repair tool's
    provenance + the price leg's first-night application). Fail-open: no artifacts -> neither."""
    logs = list_run_logs(limit=20)
    inv = carried_inventory(logs, evaluated_key="tape_evaluated")
    readings = flags_reader.stop_readings(inv.as_doc() if inv else None)
    corrections = {
        **flags_reader.first_night_corrections(logs),
        **flags_reader.repair_corrections(),
    }
    return readings, corrections


def _read(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    ids: list[UUID],
    asof: date,
) -> dict[UUID, MemberFlags]:
    known_at, clock = serve_view(asof)
    readings, corrections = _run_reads()
    return flags_reader.read_flags(
        conn,
        tenant_id=tenant_id,
        security_ids=ids,
        tickers=master.tickers_for(conn, set(ids), tenant_id=tenant_id),
        asof=asof,
        known_at=known_at,
        clock=clock,
        stale_days=get_settings().tape_stale_days,
        readings=readings,
        corrections=corrections,
    )


@router.get("/theses/{thesis_id}/data-flags", response_model=DataFlagsResponse)
def get_thesis_data_flags(
    asof: date = Query(..., description="as-of date; the flags use no data public after it"),
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> DataFlagsResponse:
    """The data flags on one thesis's resolved basket at ``asof`` — which numbers on each row rest on data the
    platform knows is wrong or cannot vouch for, why, and what clears it — plus each name's split rows. Only
    names with a flag or a split row are listed. Computed on read, never stored; display-only."""
    ids: list[UUID] = []
    for m in thesis.basket:
        if m.security_id is not None and m.security_id not in ids:
            ids.append(m.security_id)
    out = _read(conn, tenant_id=thesis.tenant_id, ids=ids, asof=asof)
    return DataFlagsResponse(
        thesis_id=thesis.id,
        asof=asof,
        names=len(ids),
        members=[_member_out(out[s]) for s in ids if out[s].flags or out[s].splits],
    )


@router.get("/data-flags", response_model=DataFlagsResponse)
def get_data_flags(
    asof: date = Query(..., description="as-of date; the flags use no data public after it"),
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> DataFlagsResponse:
    """Every flagged name across the active basket union at ``asof``, each with the theses holding it — the
    Admin list and the Board's marks. Computed on read, never stored; display-only."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT bm.security_id, t.id, t.name FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            "WHERE t.archived_at IS NULL AND t.tenant_id = %s AND bm.security_id IS NOT NULL "
            "ORDER BY t.name, bm.ordinal",
            (tenant_id,),
        )
        rows = cur.fetchall()
    held: dict[UUID, list[DataFlagThesisRefOut]] = {}
    for r in rows:
        refs = held.setdefault(r["security_id"], [])
        if all(x.id != r["id"] for x in refs):
            refs.append(DataFlagThesisRefOut(id=r["id"], name=r["name"]))
    ids = list(held)
    out = _read(conn, tenant_id=tenant_id, ids=ids, asof=asof)
    members = [_member_out(out[s], held[s]) for s in ids if out[s].flags]
    members.sort(key=lambda m: (not m.marked, m.ticker or str(m.security_id)))
    return DataFlagsResponse(asof=asof, names=len(ids), members=members)
