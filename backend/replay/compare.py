from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID

import duckdb
import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.config import CallConfig
from domain.enums import Grade
from replay.episodes import episodes_for
from replay.export import export_snapshot
from replay.harness import replay_all
from replay.metrics import ReplayMetrics, compute_metrics
from replay.pit import connect_mirror
from replay.schema import Outcome
from replay.scoring import RealizedPrices, score_episodes
from repositories import thesis_repo

# A THIN recalibration sweep/compare runner AROUND the built instrument — it touches none of
# export/pit/harness/episodes/scoring/metrics. It exports the Parquet mirror ONCE, then replays each
# CallConfig variant over that frozen mirror and tabulates the metric deltas. The window + PIN are MODULE
# CONSTANTS (not per-variant), so ONLY `cfg` varies between variants and the delta is real, not noise
# (the harness is value-identical). Vary a dial via `DEFAULT_CONFIG.model_copy(update={dial: v})`.
#
# The canonical pinned run (matches docs/REPLAY.md + tests/replay/test_run.py).
START = date(2025, 4, 1)
END = date(2026, 6, 30)
PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)


@dataclass
class VariantResult:
    label: str
    outcomes: list[Outcome]
    metrics: ReplayMetrics


def _single_name_security(conn: psycopg.Connection, tenant_id: UUID) -> dict[UUID, UUID]:
    """thesis_id -> sole member's security_id (single-name theses). Replicated from run.py's trivial
    private helper rather than importing an underscore-private symbol across modules."""
    out: dict[UUID, UUID] = {}
    for t in thesis_repo.list_all(conn):
        sids = [m.security_id for m in t.basket if m.security_id is not None]
        if len(sids) == 1:
            out[t.id] = sids[0]
    return out


def _score_one(
    conn: psycopg.Connection,
    con: duckdb.DuckDBPyConnection,
    cfg: CallConfig,
    tenant_id: UUID,
) -> tuple[list[Outcome], ReplayMetrics]:
    timeline = replay_all(
        conn, con, start=START, end=END, known_at=PIN, cfg=cfg, tenant_id=tenant_id
    )
    outcomes = score_episodes(episodes_for(timeline), RealizedPrices(con, tenant_id=tenant_id))
    metrics = compute_metrics(
        outcomes,
        timeline=timeline,
        realized=RealizedPrices(con, tenant_id=tenant_id),
        single_name_security=_single_name_security(conn, tenant_id),
    )
    return outcomes, metrics


def compare(
    conn: psycopg.Connection,
    out_dir,
    variants: list[tuple[str, CallConfig]],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> list[VariantResult]:
    """Export the mirror ONCE, then replay+score each (label, cfg) variant over that same frozen snapshot —
    so the only difference between variants is `cfg`. Returns a VariantResult per variant. Read-only over
    the SoR; writes only the Parquet mirror to ``out_dir``. The instrument is used as built, never modified.
    """
    export_snapshot(conn, out_dir, tenant_id=tenant_id)
    con = connect_mirror(out_dir)
    try:
        results = []
        for label, cfg in variants:
            outcomes, metrics = _score_one(conn, con, cfg, tenant_id)
            results.append(VariantResult(label=label, outcomes=outcomes, metrics=metrics))
        return results
    finally:
        con.close()


def subset(
    outcomes: list[Outcome],
    *,
    thesis_id: UUID | None = None,
    conviction_grade: Grade | None = None,
) -> list[Outcome]:
    """Filter scored outcomes — e.g. the UNH-core subset, so an insider-dial delta is attributable to that
    dial and not contaminated by the nuclear arms (whose exit_by is un-tunable ratified data)."""
    out = outcomes
    if thesis_id is not None:
        out = [o for o in out if o.thesis_id == thesis_id]
    if conviction_grade is not None:
        out = [o for o in out if o.conviction_grade is conviction_grade]
    return out
