"""§2.2 fundamentals backfill CLI — pull each basket member's quarterly REVENUE series + the Band 03 S4
SHARES-OUTSTANDING series (SEC companyfacts — both ride the same single pull per name) into
``fact_fundamentals`` with honest historical knowability.

companyfacts returns the FULL multi-year history in one pull, each period carrying its own ``filed``/``accn``,
so a single run reconstructs every historical quarter stamped at its TRUE filing date (R17 — never today).
This is the multi-year backfill the acceleration backtest needs. Incremental + idempotent: a re-run appends
ZERO rows for already-stored (security, metric, period_end, filed) versions (count-the-table guarded).

    python -m pipeline.ingest_fundamentals                 # bare = every (non-archived) thesis's basket
    python -m pipeline.ingest_fundamentals --thesis <uuid> # one thesis
    python -m pipeline.ingest_fundamentals --no-live        # cache-only (no network)

Fail-visible: each name runs in its own try; a failure (e.g. companyfacts 404 for a name data.sec.gov
doesn't serve) is captured into that name's result and the run continues. A security appearing in two
baskets is pulled ONCE (companyfacts is per-CIK). Advisory back-half data — never an operator-ratified fact,
but a DETERMINISTIC parse (#3): the number is XBRL, never model-sourced.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID

import psycopg

from db.session import connect
from ingest.edgar.client import EdgarClient
from ingest.fundamentals import ingest_fundamentals_for_security
from repositories import thesis_repo
from securities import master


@dataclass
class NameResult:
    """Per-name outcome — what was appended, what was already stored (idempotency), and any captured error."""

    ticker: str | None
    security_id: UUID
    appended: int = 0
    skipped: int = 0
    error: str | None = None


def ingest_fundamentals(
    conn: psycopg.Connection,
    *,
    thesis_id: UUID | None = None,
    allow_live: bool = True,
    user_agent: str | None = None,
    edgar_client: EdgarClient | None = None,
) -> list[NameResult]:
    """Ingest the quarterly revenue series for each resolved basket member of ``thesis_id`` (or, bare, EVERY
    non-archived thesis). Securities are de-duplicated across baskets (one companyfacts pull per CIK).
    Per-name fail-visible + committing on success; the caller owns nothing (this commits per name).
    """
    client = edgar_client or EdgarClient(allow_live=allow_live, user_agent=user_agent)
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis {thesis_id} not found")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)

    seen: set[UUID] = set()
    results: list[NameResult] = []
    for thesis in theses:
        for m in thesis.basket:
            if m.security_id is None or m.security_id in seen:
                continue  # unresolved placement, or a name already pulled from another basket
            seen.add(m.security_id)
            sec = master.get(conn, m.security_id, tenant_id=thesis.tenant_id)
            if sec is None:
                results.append(NameResult(m.ticker, m.security_id, error="not in tenant master"))
                continue
            try:
                res = ingest_fundamentals_for_security(
                    conn, sec, client=client, tenant_id=thesis.tenant_id
                )
                conn.commit()
                results.append(
                    NameResult(sec.ticker, sec.id, appended=res.appended, skipped=res.skipped)
                )
            except Exception as e:  # noqa: BLE001 — fail-visible: capture, roll back, keep going
                conn.rollback()
                results.append(NameResult(sec.ticker, sec.id, error=str(e)))
    return results


def _report(results: list[NameResult]) -> int:
    """Print a per-name summary; return the number that errored (the process exit signal)."""
    total_new = sum(r.appended for r in results)
    errored = [r for r in results if r.error]
    for r in results:
        if r.error:
            print(f"  {r.ticker or r.security_id}: ERROR: {r.error}")
        else:
            # skips (already-stored versions) surface only when nonzero — loudness marks the exception
            sk = f", {r.skipped} already stored" if r.skipped else ""
            print(f"  {r.ticker or r.security_id}: +{r.appended} quarters{sk}")
    print(f"done: {len(results)} names, +{total_new} quarterly facts, {len(errored)} errored")
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill the §2.2 quarterly revenue series (companyfacts) into fact_fundamentals."
    )
    p.add_argument("--thesis", default=None, help="thesis id (uuid); omit to run EVERY basket")
    p.add_argument(
        "--no-live",
        action="store_true",
        help="cache-only (no network); else live (needs ALPHADECK_USER_AGENT)",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        results = ingest_fundamentals(
            conn,
            thesis_id=UUID(args.thesis) if args.thesis else None,
            allow_live=not args.no_live,
        )
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a wrapper, non-silently


if __name__ == "__main__":
    main()
