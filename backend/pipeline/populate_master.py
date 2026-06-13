"""Populate the per-tenant security master from the SEC company_tickers universe — the broadener.

The scoring-fact loop (resolve -> extract -> ratify -> score) can only point at names already in the
master, and the seed loads a handful. This lifts it to "any name you just thought of": one polite GET of
SEC's company_tickers.json (~12k names) upserted into THIS tenant's master, idempotent and additive,
keyed on ``(cik, ticker)``. No read-side change — ``master.search``/``extract``/the scorer already read a
bigger master. See docs + ``securities.master.populate_universe`` for the keying/convention details.

    # cache-first (uses a cached company_tickers.json under data/sec_cache if present):
    python -m pipeline.populate_master
    # live fetch (the real ~12k universe) — needs ALPHADECK_USER_AGENT (SEC etiquette):
    python -m pipeline.populate_master --live
    # a non-default tenant (the production cut): pass an explicit id, else the deployment tenant is used
    python -m pipeline.populate_master --live --tenant-id <uuid>
"""

from __future__ import annotations

import argparse
from uuid import UUID

from db.session import connect, current_tenant_id
from securities import master, sec_tickers


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Populate the security master from SEC company_tickers."
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="fetch company_tickers.json live from SEC (needs ALPHADECK_USER_AGENT); else cache-only",
    )
    p.add_argument(
        "--tenant-id",
        default=None,
        help="target tenant UUID; defaults to the deployment tenant ($ALPHADECK_TENANT_ID / demo)",
    )
    a = p.parse_args(argv)
    tenant_id = UUID(a.tenant_id) if a.tenant_id else current_tenant_id()

    rows = sec_tickers.load_all(allow_live=a.live)
    conn = connect()
    try:
        counts = master.populate_universe(conn, rows, tenant_id=tenant_id)
        conn.commit()
        print(
            f"populated master for tenant {tenant_id}: "
            f"{counts['inserted']} inserted, {counts['updated']} updated, "
            f"{counts['skipped']} unchanged ({len(rows)} SEC rows)"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
