"""Operator-ratify an exposure-purity (revenue-mix) fact — a CLI until the Workbench exists.

The Workbench's purity meter re-derives from this fact; the number is a SOURCED fact (the 10-K segment
that reports it), never a typed guess. The LLM may later propose the source; the operator ratifies the
real figure here.

    python -m pipeline.ratify_revenue_mix --ticker LEU --segment enrichment --pct 100 \
        --source-url "https://www.sec.gov/...10-K...#segments" --date 2026-01-01
"""

from __future__ import annotations

import argparse
from datetime import date
from uuid import UUID

from db.session import DEFAULT_TENANT_ID, connect
from ingest.revenue_mix import ingest_revenue_mix
from pipeline.ratify_common import resolve_security


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Operator-ratify an exposure-purity (revenue-mix) fact."
    )
    p.add_argument("--ticker", required=True)
    p.add_argument("--segment", required=True, help="the revenue line, e.g. 'nuclear'")
    p.add_argument(
        "--pct", required=True, type=float, help="percent of revenue from that line (0..100)"
    )
    p.add_argument(
        "--source-url", required=True, help="the 10-K segment (provenance) — never a guess"
    )
    p.add_argument("--date", required=True, help="the filing's effective date (YYYY-MM-DD)")
    p.add_argument("--by", default="operator", help="who ratified")
    p.add_argument(
        "--note", default=None, help="free-text provenance note (basis / composition / caveat)"
    )
    p.add_argument(
        "--tenant-id",
        default=str(DEFAULT_TENANT_ID),
        help="tenant to ratify under (resolve + fact); defaults to the demo tenant",
    )
    a = p.parse_args(argv)

    tenant_id = UUID(a.tenant_id)
    conn = connect()
    try:
        sid = resolve_security(conn, a.ticker, tenant_id)
        fid = ingest_revenue_mix(
            conn,
            sid,
            segment_label=a.segment,
            mix_pct=a.pct,
            source="ratified",
            source_ref=a.source_url,
            event_date=date.fromisoformat(a.date),
            note=a.note,
            ratified_by=a.by,
            tenant_id=tenant_id,
        )
        conn.commit()
        print(f"ratified revenue-mix {fid} on {a.ticker.upper()}: {a.pct}% {a.segment}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
