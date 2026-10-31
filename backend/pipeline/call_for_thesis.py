from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import psycopg

from db.public_clock import Clock
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash
from domain.settings import get_settings
from domain.thesis import Position, Thesis
from pipeline.core import assemble_from_pit
from repositories import calls_repo, decisions_repo, thesis_repo
from signals.base import PointInTimeData
from signals.horizons import call_bounds


def call_for_thesis(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    *,
    known_at: datetime | None = None,
    cfg: CallConfig = DEFAULT_CONFIG,
    record: bool = True,
    run_kind: str = "manual",
    clock: Clock = "record",
    decisions_asof: date | None = None,
) -> CallCard:
    """Load the thesis, RE-DERIVE its dated signal stream from the bitemporal facts as-of (via the shared
    ``assemble_from_pit`` core), and (when ``record``) append the CallCard to the write-only ``calls``
    accountability log.

    There is no persisted firing layer: the stream is re-derived on every read, so a fact correction
    propagates automatically. The card is a pure function of (thesis, events, asof, cfg) — see
    ``pipeline.core.assemble_from_pit``, the seam the replay harness reuses with a Parquet-backed pit, so
    replay runs the identical pipeline (only the fact source differs). The API read path calls with
    ``record=False`` (a GET writes nothing); the batch ``pipeline.run`` is the writer of the call of record
    (``record=True``). When it writes, that append is the only write and is never read back to serve. The
    caller owns the transaction (commit/rollback). ``known_at`` defaults to now (live read); the replay
    harness pins it to a past transaction time. ``clock="public"`` makes it a HISTORY view (a past-asof
    serve recompute, the backfill): every fact is judged on its PUBLIC instant ``<= known_at``
    (``db.public_clock``) — see ``load_call_inputs``.

    ``run_kind`` (0044) labels the write for the record's run identity. It defaults to ``"manual"`` because
    the ONLY ``record=True`` caller is the one-off ``python -m pipeline.run`` CLI — the nightly cron and the
    backfill assemble here with ``record=False`` and do their own ``record_if_changed`` with their own kind.
    The stamped ``config_hash`` is computed from THIS call's ``cfg``, the same object the assembler below
    runs with, so the fingerprint can never describe a different policy than the one that produced the card.

    It is exactly ``assemble_from_pit(pit, thesis, asof, cfg)`` over ``load_call_inputs(...)`` plus the
    optional record — the input half is its own function so a DIAGNOSTIC caller can assemble twice over ONE
    prefetched view (``pipeline.annotate_phantom_arms``); this funnel's behavior is unchanged by that split.

    ``decisions_asof`` (F1, the SERVE path's live view only — ``domain.market_time.serve_decisions_asof``): read
    the operator's decisions log up to that day instead of ``asof``. ``None`` (every other caller: the nightly,
    the backfill, ``pipeline.run``, a past view) = ``asof``, the unchanged read. See ``load_call_inputs``.
    """
    thesis, pit = load_call_inputs(
        conn,
        thesis_id,
        asof,
        known_at=known_at,
        cfg=cfg,
        clock=clock,
        decisions_asof=decisions_asof,
    )
    card = assemble_from_pit(pit, thesis, asof, cfg)
    if record:
        calls_repo.append(
            conn,
            card,
            thesis.tenant_id,
            config_hash=config_hash(cfg),
            code_sha=get_settings().image_sha,
            run_kind=run_kind,
        )
    return card


def load_call_inputs(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    *,
    known_at: datetime | None = None,
    cfg: CallConfig = DEFAULT_CONFIG,
    clock: Clock = "record",
    decisions_asof: date | None = None,
) -> tuple[Thesis, PointInTimeData]:
    """The INPUT half of ``call_for_thesis`` — everything the funnel reads before it assembles: the thesis
    with its roster AS OF ``known_at`` (basket_snapshot), its position as of both axes (the decisions log),
    and the prefetching point-in-time view over the resolved basket under ``call_bounds(cfg)``. Returns
    ``(thesis, pit)``; ``assemble_from_pit(pit, thesis, asof, cfg)`` over them IS the call. Raises
    ``LookupError`` for an unknown thesis.

    Split out (D3b) so ``pipeline.annotate_phantom_arms`` can run its D3a-off and D3a-on re-assemblies over
    ONE view — the PIT memo makes the second assembly cost no further DB reads — while still reading
    through exactly this funnel. The live callers (serve, the cron, the backfill, ``pipeline.run``) go
    through ``call_for_thesis`` and are unchanged.

    ``clock`` reaches ONLY the fact view. The roster (``basket_snapshot``) and the position (the decisions log)
    are the OPERATOR's decisions, and an operator decision counts from when the operator made it — its
    ``recorded_at`` — so both stay read as of ``known_at`` on either clock (on the public clock that is the
    end of the as-of's market day: the roster and the fills the operator had logged by then).

    ``decisions_asof`` moves ONLY the position's valid-time bound (``_position``); the roster and every fact
    stay at ``asof``. ``None`` = ``asof`` (the unchanged read).
    """
    # THE ROSTER FEED (F12, the SECOND bitemporal leak): read the basket as it was known at ``known_at``
    # (via basket_snapshot), MIRRORING the position feed just below. ``basket_member`` is full-replace /
    # non-temporal, so a past-asof call (or a pinned-known_at run) would otherwise recompute on TODAY's
    # roster (#1); get_asof reads the roster as it was known then. Fed here, at the single assembly funnel,
    # so serve / pipeline.run / the daily cron / backfill all get it from one place. A thesis with no
    # qualifying snapshot (pre-F12) falls back to the live roster; get_asof(now) == get, so a live/today
    # call is byte-identical.
    thesis = thesis_repo.get_asof(conn, thesis_id, known_at)
    if thesis is None:
        raise LookupError(f"thesis not found: {thesis_id}")

    # THE POSITION FEED (decision capture): the operator-decisions log is the source of truth for the
    # position, read as-of BOTH time axes — a past-asof call (or a pinned-known_at run) never sees a
    # later-logged fill (#1). Fed here, at the single assembly funnel, so the API read, pipeline.run,
    # and the daily cron all get it from one place and the assembler stays pure. A thesis with no
    # decision rows keeps its stored seed-era position (the precedence rule in decisions_repo).
    thesis.position = _position(conn, thesis, asof, known_at, decisions_asof)

    # Derive the tenant from the THESIS (auth deferred): a thesis lives in exactly one tenant, so its
    # tenant_id scopes EVERY fact read for this call (threaded once into the pit) AND the call-of-record
    # write — making both the API read path and the batch pipeline tenant-correct from this one place.
    # Pass it explicitly: the column is NOT NULL, so a loaded thesis's tenant_id is non-None; never
    # `or DEFAULT` here, so a None would surface as a bug rather than silently read demo facts.
    # The resolved basket is the PIT's PREFETCH scope (one query per fact table for the whole thesis)
    # and ``call_bounds(cfg)`` its registry-DERIVED read bounds — from the SAME cfg the assembler runs
    # with, so a widened dial widens the bound. Which rows a detector sees is unchanged (the memo
    # rule, ``signals/base.py``); only where they are fetched from.
    basket = {m.security_id for m in thesis.basket if m.security_id is not None}
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=thesis.tenant_id,
        basket=basket,
        bounds=call_bounds(cfg),
        clock=clock,
    )
    return thesis, pit


def _position(
    conn: psycopg.Connection,
    thesis: Thesis,
    asof: date,
    known_at: datetime | None,
    decisions_asof: date | None,
) -> Position | None:
    """The position the call sees: the decisions log as of ``decisions_asof`` (default ``asof``) on the
    transaction axis ``known_at`` (``decisions_repo.effective_position``, unchanged).

    THE LIVE VIEW'S SAME-DAY FILL (F1). On the serve path's live view of a night before today,
    ``decisions_asof`` is the action day (``domain.market_time.serve_decisions_asof``), so a take logged this
    morning — dated today, after the night — is read. The assembler's own no-lookahead gate makes a position
    MANAGING only once ``opened_on <= asof`` (``calls.assembler._state``), which would hide exactly that take
    again; so a position opened after ``asof`` but on or before ``decisions_asof`` is handed to the call as
    open at ``asof`` (``opened_on = asof``). Nothing else on the call reads ``opened_on`` and the card carries
    no position, so that gate is the only thing it moves; the stored row and every other reader keep the real
    date. A close dated today needs no such step: the position simply reads ``None``. With
    ``decisions_asof`` ``None`` or ``<= asof`` (every record path, every past view) nothing here changes.
    """
    horizon = asof if decisions_asof is None or decisions_asof < asof else decisions_asof
    pos = decisions_repo.effective_position(conn, thesis, asof=horizon, known_at=known_at)
    if (
        horizon > asof
        and pos is not None
        and pos.opened_on is not None
        and asof < pos.opened_on <= horizon
    ):
        pos = pos.model_copy(update={"opened_on": asof})
    return pos
