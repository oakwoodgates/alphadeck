"""Operator-ratify a shares-outstanding fact — a CLI until the Workbench exists.

The Workbench's market-cap figure = close × shares; the shares number is a SOURCED fact (the 10-Q cover /
XBRL), never a typed guess.

    python -m pipeline.ratify_shares --ticker OKLO --shares 141000000 \
        --source-url "https://www.sec.gov/...10-Q...cover" --date 2026-05-01
"""

from __future__ import annotations

import argparse
from datetime import date
from uuid import UUID

from db.session import DEFAULT_TENANT_ID, connect
from ingest.shares import ingest_shares_outstanding
from pipeline.ratify_common import resolve_security


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Operator-ratify a shares-outstanding fact.")
    p.add_argument("--ticker", required=True)
    p.add_argument("--shares", required=True, type=float, help="shares outstanding (a count)")
    p.add_argument(
        "--source-url",
        required=True,
        help="the 10-Q cover / XBRL fact (provenance) — never a guess",
    )
    p.add_argument("--date", required=True, help="the filing's effective date (YYYY-MM-DD)")
    p.add_argument("--by", default="operator", help="who ratified")
    p.add_argument(
        "--note", default=None, help="free-text provenance note (share-class composition / caveat)"
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
        fid = ingest_shares_outstanding(
            conn,
            sid,
            shares=a.shares,
            source="ratified",
            source_ref=a.source_url,
            event_date=date.fromisoformat(a.date),
            note=a.note,
            ratified_by=a.by,
            tenant_id=tenant_id,
        )
        conn.commit()
        print(f"ratified shares-outstanding {fid} on {a.ticker.upper()}: {a.shares:,.0f} shares")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
