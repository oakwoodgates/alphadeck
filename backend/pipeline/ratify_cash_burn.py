"""Operator-ratify a cash + quarterly-burn fact — a CLI until the Workbench exists.

The Workbench's runway meter = cash / (quarterly_burn / 3) months; both numbers are SOURCED facts (the
10-Q), never typed guesses. A cash-flow-positive name passes a non-positive ``--quarterly-burn``.

    python -m pipeline.ratify_cash_burn --ticker OKLO --cash 280000000 --quarterly-burn 25000000 \
        --source-url "https://www.sec.gov/...10-Q..." --date 2026-05-01
"""

from __future__ import annotations

import argparse
from datetime import date
from uuid import UUID

from db.session import DEFAULT_TENANT_ID, connect
from ingest.cash_burn import ingest_cash_burn
from pipeline.ratify_common import resolve_security


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Operator-ratify a cash + quarterly-burn fact.")
    p.add_argument("--ticker", required=True)
    p.add_argument("--cash", required=True, type=float, help="cash + equivalents on hand (USD)")
    p.add_argument(
        "--quarterly-burn",
        required=True,
        type=float,
        help="net cash used in operations per quarter (USD); <= 0 means cash-positive",
    )
    p.add_argument("--source-url", required=True, help="the 10-Q (provenance) — never a guess")
    p.add_argument("--date", required=True, help="the filing's effective date (YYYY-MM-DD)")
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
        sid = resolve_security(conn, a.ticker, tenant_id)
        fid = ingest_cash_burn(
            conn,
            sid,
            cash_usd=a.cash,
            quarterly_burn_usd=a.quarterly_burn,
            source="ratified",
            source_ref=a.source_url,
            event_date=date.fromisoformat(a.date),
            ratified_by=a.by,
            tenant_id=tenant_id,
        )
        conn.commit()
        print(
            f"ratified cash-burn {fid} on {a.ticker.upper()}: "
            f"${a.cash:,.0f} cash, ${a.quarterly_burn:,.0f}/qtr burn"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
