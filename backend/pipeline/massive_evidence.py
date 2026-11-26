"""MASSIVE.COM'S WORD ON THE STALE PRICE ROWS (W6-4) — reference evidence for the steward, never a class.

THE GAP THIS CLOSES. A stopped price tape reads "check for a ticker rename or a delisting and set the vendor price
symbol" whatever stopped it. MEASURED 2026-11-25 on prod's 9 open stale rows: Massive.com's ticker overview
(``ingest/massive_reference.py``) says ACLEW and IXQWF are OTC WARRANTS (active), INDV is delisted (2026-11-03),
SNDK and SCNI are common stock / an ADS that traded tonight (a vendor gap); NOTVQ, TMRC, MTMT and EULL are unknown
to it (404). Each says what the steward is looking at before any repair.

WHAT THIS MODULE IS, AND IS NOT:

- **EVIDENCE on the row, never a class** (``tape_health.mark_massive``, the ``deregistered`` discipline): closed,
  renamed, unpriceable, quiet and the newly-stale page read exactly as without it. Never on the CallCard, never
  paged: the run artifact's rows, the Admin tape and price-feed panels, the steward MCP and one quiet report line.
- **Few requests.** ``traded_on`` comes off tonight's grouped day, which the cross-check already fetched (no
  request). The overview is read once per open stale row whose symbol has NOT been read before — a row the
  previous passes read carries that read forward from the artifact (``carried_refs``) — at most
  ``Settings.massive_reference_max_names`` (5) a night, newest stale first (the newest edge; a never-priced row,
  with no edge, last), each cached per (symbol, market day). The rest wait for a later pass. A read that fails
  stops the night's reads (one failure, not five slow ones); the rows left are read on a later pass.
- **Fail-open.** No ``MASSIVE_API_KEY``, a cap of 0, a ``--no-live`` pass, a failed read or the unit's own fault is
  a status on ``ReferenceRun``; the pass goes on, and nothing here raises.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

from domain.feed_kinds import PRICE, feed_kind
from domain.settings import get_settings
from ingest.massive_reference import (
    MassiveRef,
    MassiveReferenceError,
    default_reference_source,
    is_massive_symbol,
)
from pipeline.cron_run_log import carried_inventory
from pipeline.tape_crosscheck import GroupedFetch, Name, names_of
from pipeline.tape_health import mark_massive

STATUS_ON = "on"
STATUS_OFF = "off"
STATUS_FAILED = "failed"
STATUS_NOT_RUN = "not_run"

_ERROR_CLIP = 200
_SHOWN = 8  # names on one line before "+N more"


@dataclass
class ReferenceRun:
    """One pass's reference reads. ``status`` (``on`` / ``off`` / ``failed`` / ``not_run``) and ``note``;
    ``error`` = the unit's own fault (nothing attached). ``read`` = the rows whose overview was read tonight,
    ``not_listed`` = those Massive.com does not list (a 404), ``deferred`` = never-read rows left for a later
    pass (the cap, or after a failed read), ``failed`` = the read failure that stopped tonight's reads (key-free),
    ``carried`` = rows carrying an earlier pass's read, ``traded`` = rows the grouped day shows traded tonight.
    """

    status: str = STATUS_NOT_RUN
    note: str | None = None
    error: str | None = None
    cap: int | None = None
    read: tuple[str, ...] = ()
    not_listed: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    failed: str | None = None
    carried: int = 0
    traded: int = 0


def _fault(e: BaseException) -> str:
    msg = " ".join(str(e).split())[:_ERROR_CLIP]
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def carried_refs(payloads: Iterable[dict]) -> dict[UUID, MassiveRef]:
    """``security_id -> MassiveRef`` that an earlier pass READ (``read_on`` set) for an open price row, from the
    per-thesis carry-forward the stale baseline reads (``cron_run_log.carried_inventory``): the newest read
    wins when two theses' entries differ. Pure over its input; a malformed row is skipped."""
    inv = carried_inventory(list(payloads), evaluated_key="tape_evaluated")
    out: dict[UUID, MassiveRef] = {}
    for t in inv.entries() if inv is not None else ():
        for s in t.get("tape_stale") or ():
            try:
                if not isinstance(s, dict) or feed_kind(s.get("kind")).key != PRICE.key:
                    continue
                ref = MassiveRef.from_json(s.get("massive_ref"))
                if ref is None or ref.read_on is None:
                    continue
                sid = UUID(str(s["security_id"]))
                prev = out.get(sid)
                if prev is None or (prev.read_on or date.min) < ref.read_on:
                    out[sid] = ref
            except Exception:  # noqa: BLE001 — a malformed row is skipped, never a raise
                continue
    return out


def plan_reads(
    stale: Sequence[Name], carried: Mapping[UUID, MassiveRef], *, cap: int
) -> tuple[list[Name], list[Name]]:
    """``(to read tonight, left for a later pass)``: the open stale rows with a Massive.com symbol (one the reader
    can ask for) and no earlier read OF THAT SYMBOL, newest stale first (the newest edge; no edge — never priced —
    last; first-seen order within a tie), the first ``cap`` read. Pure."""
    owed = [
        n
        for n in stale
        if is_massive_symbol(n.massive)
        and (n.security_id not in carried or carried[n.security_id].symbol != n.massive)
    ]
    owed.sort(key=lambda n: (n.edge is None, -(n.edge.toordinal()) if n.edge else 0))
    cap = max(0, cap)
    return owed[:cap], owed[cap:]


def attach(results: Iterable[Any], evidence: Mapping[UUID, MassiveRef]) -> None:
    """Put each company's evidence on its stale rows in every thesis (``mark_massive``: open price rows only)."""
    for r in results:
        rows = getattr(r, "tape_stale", ()) or ()
        if any(s.security_id in evidence for s in rows):
            r.tape_stale = tuple(mark_massive(s, evidence.get(s.security_id)) for s in rows)


def _traded(grouped: GroupedFetch | None, n: Name) -> date | None:
    day = grouped.day if grouped is not None else None
    bar = day.get(n.massive) if day is not None else None
    return grouped.session if bar is not None and bar.volume > 0 else None


def run_massive_reference(
    *,
    results: Sequence[Any],
    asof: date,
    allow_live: bool,
    grouped: GroupedFetch | None,
    payloads: Callable[[], Iterable[dict]],
    source: Any = None,
) -> ReferenceRun:
    """The nightly unit, after the per-thesis loop and before the artifact: read what is owed (``plan_reads``),
    carry the earlier reads, mark tonight's trade, and put the evidence on the rows. ``source`` = the overview
    reader (``None`` = ``default_reference_source()``; still ``None`` = no key). Never raises."""
    if not allow_live:
        return ReferenceRun(note="a --no-live pass")
    try:
        cap = int(get_settings().massive_reference_max_names)
        _, stale, _ = names_of(results)
        if not stale:
            return ReferenceRun(status=STATUS_ON, cap=cap)
        carried = carried_refs(payloads())
        run = ReferenceRun(status=STATUS_ON, cap=cap)
        src = source if source is not None else default_reference_source()
        todo: list[Name] = []
        if src is None:
            run.status, run.note = STATUS_OFF, "no MASSIVE_API_KEY"
        elif cap <= 0:
            run.status, run.note = STATUS_OFF, "ALPHADECK_MASSIVE_REFERENCE_MAX_NAMES=0"
        else:
            todo, later = plan_reads(stale, carried, cap=cap)
            run.deferred = tuple(n.label for n in later)
        reads: dict[UUID, MassiveRef] = {}
        for i, n in enumerate(todo):
            try:
                found = src.overview(n.massive, day=asof, allow_live=True)
            except (
                Exception
            ) as e:  # noqa: BLE001 — a failed read stops tonight's reads, never the pass
                # the reader's own message is key-scrubbed and names the status; anything else by its type
                run.failed = (
                    " ".join(str(e).split())[:_ERROR_CLIP]
                    if isinstance(e, MassiveReferenceError)
                    else _fault(e)
                )
                run.deferred = tuple(m.label for m in todo[i:]) + run.deferred
                break
            reads[n.security_id] = MassiveRef.read(n.massive, asof, found)
            run.read += (n.label,)
            if found is None:
                run.not_listed += (n.label,)
        evidence: dict[UUID, MassiveRef] = {}
        for n in stale:
            ref = reads.get(n.security_id)
            if (
                ref is None
                and n.security_id in carried
                and carried[n.security_id].symbol == n.massive
            ):
                ref = carried[n.security_id]
                run.carried += 1
            traded = _traded(grouped, n)
            if traded is not None:
                run.traded += 1
                ref = (ref or MassiveRef(n.massive)).traded(traded)
            elif ref is not None:
                ref = replace(ref, traded_on=None)  # a carried read's trade was another night's
            if ref is not None:
                evidence[n.security_id] = ref
        attach(results, evidence)
    except Exception as e:  # noqa: BLE001 — the unit's fault is reported, never a failed pass
        return ReferenceRun(status=STATUS_FAILED, error=_fault(e))
    return run


# --- the words (the cron log) --------------------------------------------------------------------------------


def _names(labels: Sequence[str]) -> str:
    shown = ", ".join(labels[:_SHOWN])
    return shown + (f", +{len(labels) - _SHOWN} more" if len(labels) > _SHOWN else "")


def report_lines(run: ReferenceRun) -> list[str]:
    """The cron's printed line (``pipeline.daily.main``): one quiet line when anything was read, failed or left
    for a later pass — "Massive.com reference · 5 stale symbols read (5-a-night cap); 2 wait for a later pass";
    the unit's own fault loud; nothing on a quiet night. Never a page."""
    if run.error:
        return [
            f"MASSIVE.COM REFERENCE FAILED — {run.error} (tonight's stale rows carry no Massive.com reference)"
        ]
    parts: list[str] = []
    if run.read:
        n = len(run.read)
        part = f"{n} stale symbol{'' if n == 1 else 's'} read ({run.cap}-a-night cap)"
        if run.not_listed:
            part += f", {len(run.not_listed)} not listed: {_names(run.not_listed)}"
        parts.append(part)
    if run.failed:
        parts.append(f"the read failed ({run.failed})")
    if run.deferred:
        parts.append(f"{len(run.deferred)} wait for a later pass")
    return ["Massive.com reference · " + "; ".join(parts)] if parts else []
