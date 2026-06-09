from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from db.session import DEFAULT_TENANT_ID, connect
from domain.config import DEFAULT_CONFIG, CallConfig
from replay.episodes import episodes_for
from replay.export import export_snapshot
from replay.harness import replay_all
from replay.metrics import ReplayMetrics, compute_metrics
from replay.pit import connect_mirror
from replay.scoring import RealizedPrices, score_episodes
from repositories import thesis_repo


def _single_name_security(conn: psycopg.Connection, tenant_id: UUID) -> dict[UUID, UUID]:
    """thesis_id -> its sole member's security_id, for single-name theses (the unit the withheld-arm
    metric can price). Multi-name themes are omitted."""
    out: dict[UUID, UUID] = {}
    for t in thesis_repo.list_all(conn):
        sids = [m.security_id for m in t.basket if m.security_id is not None]
        if len(sids) == 1:
            out[t.id] = sids[0]
    return out


def _write_parquet(path: Path, rows: list[dict]) -> None:
    if rows:  # all-five tables are populated on the seed; skip writing an empty artifact
        pq.write_table(pa.Table.from_pylist(rows), path)


def run(
    conn: psycopg.Connection,
    *,
    start: date,
    end: date,
    pin: datetime,
    out_dir: str | Path,
    cfg: CallConfig = DEFAULT_CONFIG,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> ReplayMetrics:
    """The full instrument: export the SoR to the Parquet mirror, replay every thesis day-by-day over
    ``[start, end]`` at ``known_at=pin`` (the determinism pin), derive arm episodes, score them against
    realized forward prices, and aggregate the metric set. Writes ``outcomes.parquet`` / ``episodes.parquet``
    (DuckDB-queryable) + ``metrics.json`` (the readable summary) to ``out_dir``. Deterministic for a given
    ``(snapshot, pin, window, cfg)`` — no clock/random in the loop. Returns the metrics."""
    out = Path(out_dir)
    export_snapshot(conn, out, tenant_id=tenant_id)
    con = connect_mirror(out)
    try:
        timeline = replay_all(
            conn, con, start=start, end=end, known_at=pin, cfg=cfg, tenant_id=tenant_id
        )
        episodes = episodes_for(timeline)
        realized = RealizedPrices(con, tenant_id=tenant_id)
        outcomes = score_episodes(episodes, realized)
        metrics = compute_metrics(
            outcomes,
            timeline=timeline,
            realized=realized,
            single_name_security=_single_name_security(conn, tenant_id),
        )
        _write_parquet(out / "outcomes.parquet", [o.model_dump(mode="json") for o in outcomes])
        _write_parquet(out / "episodes.parquet", [e.model_dump(mode="json") for e in episodes])
        (out / "metrics.json").write_text(
            metrics.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        return metrics
    finally:
        con.close()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Alpha Deck replay/backtest harness (Phase 1, the instrument)."
    )
    p.add_argument("--start", required=True, help="window start, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="window end, YYYY-MM-DD")
    p.add_argument("--pin", required=True, help="the known_at determinism pin, ISO timestamp")
    p.add_argument(
        "--out", required=True, help="output dir for the Parquet mirror + outcomes + metrics"
    )
    args = p.parse_args()
    pin = datetime.fromisoformat(args.pin)
    if pin.tzinfo is None:  # the recorded_at axis is tz-aware; assume UTC for a bare timestamp
        pin = pin.replace(tzinfo=timezone.utc)
    conn = connect()
    try:
        metrics = run(
            conn,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            pin=pin,
            out_dir=args.out,
        )
        print(metrics.model_dump_json(indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
