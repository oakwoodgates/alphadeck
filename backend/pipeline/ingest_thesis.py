"""Per-thesis back-half ingest — insider Form 4 + price EOD for a thesis's RESOLVED basket (M2a).

The create/promote path writes only the spine, so a freshly-authored thesis has no CALL-ENGINE facts and
can never WARM/ARM. This CLI fills that gap on demand: for each resolved basket member it ingests the insider
+ price facts the detectors read. It is:

- **Incremental** — only NEW Form 4 accessions (``existing_accessions``) and only EOD bars newer than the
  latest stored one (``latest_bar_date``). A re-run of an already-current name appends ZERO rows; the
  append-only fact tables never silently grow (the read dedups; we stop the write).
- **Fail-visible** — each leg runs in its own try; a failure is CAPTURED into the name's result and the run
  continues (one bad name never aborts the others), improving on the scanner's bare ``except: pass``. The
  live fetchers are polite (``ingest.http.polite_get`` retries 429 / transient 5xx with backoff; EDGAR also
  throttles ≤8 req/s).
- **No-lookahead** — ``recorded_at`` is left to the DB default ``now()`` (NEVER backdated), so a fact ingested
  today is invisible to an as-of read pinned at an earlier transaction time (the replay guarantee holds).
- **Exact membership (#2)** — it targets the member's RESOLVED ``security_id`` via ``master.get`` (the issuer
  ticker + CIK), never a fresh fuzzy resolve. Tenant comes from the thesis (one thesis = one tenant).

    python -m pipeline.ingest_thesis --thesis <uuid>
    python -m pipeline.ingest_thesis --thesis <uuid> --no-live   # cache-only (no network)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID

import psycopg

from db.session import connect
from domain.security import Security
from ingest.edgar.client import EdgarClient
from ingest.edgar.form4 import existing_accessions, ingest_form4
from ingest.edgar.submissions import fetch_submissions, form4_doc_url, form4_filings
from ingest.prices.eod_loader import fetch_eod, ingest_prices, latest_bar_date
from repositories import thesis_repo
from securities import master


@dataclass
class NameResult:
    """The per-name outcome — what was appended, and the captured error if a leg failed (fail-visible)."""

    ticker: str | None
    security_id: UUID
    form4_appended: int
    price_bars_appended: int
    error: str | None = None


def _form4_leg(
    conn: psycopg.Connection, client: EdgarClient, sec: Security, *, tenant_id: UUID
) -> int:
    """Ingest only Form 4 accessions not already stored for this security (incremental). Returns the count
    appended. Needs the issuer CIK; a name without one contributes no insider facts."""
    if not sec.cik:
        return 0
    seen = existing_accessions(conn, sec.id, tenant_id=tenant_id)
    appended = 0
    for f in form4_filings(fetch_submissions(client, sec.cik)):
        if f["accession"] in seen:
            continue  # already have this filing's txns — skip (no duplicate append)
        url = form4_doc_url(sec.cik, f["accession"], f["primary_doc"])
        doc = f["primary_doc"].rsplit("/", 1)[-1]
        xml = client.get_text(url, f"forms/{f['accession']}/{doc}")
        appended += ingest_form4(conn, sec.id, xml, f["accession"], tenant_id=tenant_id)
    return appended


def _price_leg(
    conn: psycopg.Connection, sec: Security, *, tenant_id: UUID, allow_live: bool
) -> int:
    """Ingest only EOD bars newer than the latest stored bar for this security (incremental). Returns the
    count appended."""
    if not sec.ticker:
        return 0
    last = latest_bar_date(conn, sec.id, tenant_id=tenant_id)
    rows = [
        r for r in fetch_eod(sec.ticker, allow_live=allow_live) if last is None or r["d"] > last
    ]
    return ingest_prices(conn, sec.id, rows, tenant_id=tenant_id)


def ingest_thesis(
    conn: psycopg.Connection,
    thesis_id: UUID,
    *,
    allow_live: bool = True,
    user_agent: str | None = None,
) -> list[NameResult]:
    """Ingest insider + price facts for each RESOLVED basket member of ``thesis_id``.

    Per member: resolve the id to the master row (skip unresolved / foreign ids), then run the Form 4 leg
    and the price leg — EACH in its own try, COMMITTING on success and ROLLING BACK on failure, so one
    leg's error never discards the other's work and never aborts the run (the error is captured into the
    name's ``NameResult``). Incremental + no-lookahead (see the module docstring). Returns one ``NameResult``
    per member that had a resolved id."""
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise LookupError(f"thesis {thesis_id} not found")
    client = EdgarClient(allow_live=allow_live, user_agent=user_agent)
    results: list[NameResult] = []
    for m in thesis.basket:
        if m.security_id is None:
            continue  # unresolved placement — no exact member to ingest against
        sec = master.get(conn, m.security_id, tenant_id=thesis.tenant_id)
        if sec is None:
            # a placed id not in this tenant's master (shouldn't happen post-promote-guard) — report it
            results.append(NameResult(m.ticker, m.security_id, 0, 0, "not in tenant master"))
            continue
        errs: list[str] = []
        f4 = 0
        try:
            f4 = _form4_leg(conn, client, sec, tenant_id=thesis.tenant_id)
            conn.commit()
        except (
            Exception
        ) as e:  # noqa: BLE001 — fail-visible: capture, roll back this leg, keep going
            conn.rollback()
            errs.append(f"form4: {e}")
        px = 0
        try:
            px = _price_leg(conn, sec, tenant_id=thesis.tenant_id, allow_live=allow_live)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            errs.append(f"price: {e}")
        results.append(NameResult(sec.ticker, sec.id, f4, px, "; ".join(errs) or None))
    return results


def _report(results: list[NameResult]) -> int:
    """Print a per-name summary; return the number of names that errored (the process exit signal)."""
    total_f4 = sum(r.form4_appended for r in results)
    total_px = sum(r.price_bars_appended for r in results)
    errored = [r for r in results if r.error]
    for r in results:
        tail = f"   ERROR: {r.error}" if r.error else ""
        print(
            f"  {r.ticker or r.security_id}: +{r.form4_appended} form4, +{r.price_bars_appended} bars{tail}"
        )
    print(
        f"done: {len(results)} names, +{total_f4} insider txns, +{total_px} price bars, "
        f"{len(errored)} errored"
    )
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Ingest back-half facts (Form 4 + EOD) for a thesis's resolved basket."
    )
    p.add_argument("--thesis", required=True, help="thesis id (uuid)")
    p.add_argument(
        "--no-live",
        action="store_true",
        help="cache-only (no network); else live (needs ALPHADECK_USER_AGENT)",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        results = ingest_thesis(conn, UUID(args.thesis), allow_live=not args.no_live)
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a cron / wrapper, non-silently


if __name__ == "__main__":
    main()
