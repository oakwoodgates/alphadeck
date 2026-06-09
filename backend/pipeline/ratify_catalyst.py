"""The operator-ratified catalyst bridge (#10) — a CLI until the Workbench exists.

The operator turns a verifiable conviction into a real, provenanced, append-only fact; the
``catalyst_conviction`` detector then re-derives it as a Key-1 conviction, so the thesis arms once the
market confirms (a co-located breakout). The grade is the operator's call at ratification.

    python -m pipeline.ratify_catalyst --ticker OKLO --type contract --grade core \
        --label "20-year power-offtake agreement with <hyperscaler>" \
        --source-url "https://www.sec.gov/...8-K..." --date 2025-07-15
"""

from __future__ import annotations

import argparse
from datetime import date
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID, connect
from domain.enums import CatalystType, Grade
from ingest.catalyst import ingest_catalyst


def _resolve(conn: psycopg.Connection, ticker: str, tenant_id: UUID) -> UUID:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM security_master WHERE tenant_id = %s AND ticker = %s "
            "ORDER BY recorded_at DESC LIMIT 1",
            (tenant_id, ticker.upper()),
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(f"no security_master row for {ticker!r} — seed the security first")
    return row["id"]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Operator-ratify a catalyst conviction (the #10 bridge)."
    )
    p.add_argument("--ticker", required=True)
    p.add_argument("--type", required=True, choices=[t.value for t in CatalystType])
    p.add_argument("--grade", required=True, choices=[g.value for g in Grade])
    p.add_argument("--label", required=True, help="human description of the catalyst")
    p.add_argument(
        "--source-url", required=True, help="the real source (provenance) — never a guess"
    )
    p.add_argument("--date", required=True, help="the catalyst event date (YYYY-MM-DD)")
    p.add_argument(
        "--horizon-end",
        default=None,
        help="the agreement term / relevance-horizon end (YYYY-MM-DD); drives liveness, else the default",
    )
    p.add_argument("--by", default="operator", help="who ratified")
    p.add_argument(
        "--tenant-id",
        default=str(DEFAULT_TENANT_ID),
        help="tenant to ratify under (resolve + fact); defaults to the demo tenant",
    )
    a = p.parse_args(argv)

    tenant_id = UUID(a.tenant_id)
    conn = connect()
    try:
        sid = _resolve(conn, a.ticker, tenant_id)
        fid = ingest_catalyst(
            conn,
            sid,
            catalyst_type=CatalystType(a.type),
            grade=Grade(a.grade),
            label=a.label,
            source="ratified",
            source_ref=a.source_url,
            event_date=date.fromisoformat(a.date),
            horizon_end=date.fromisoformat(a.horizon_end) if a.horizon_end else None,
            ratified_by=a.by,
            tenant_id=tenant_id,
        )
        conn.commit()
        print(f"ratified catalyst {fid} on {a.ticker.upper()} ({a.grade}) — {a.label}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
