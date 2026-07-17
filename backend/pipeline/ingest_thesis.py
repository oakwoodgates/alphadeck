"""Per-thesis back-half ingest — insider Form 4 + price EOD for a thesis's RESOLVED basket (M2a).

The create/promote path writes only the spine, so a freshly-authored thesis has no CALL-ENGINE facts and
can never WARM/ARM. This CLI fills that gap on demand: for each resolved basket member it ingests the insider
+ price facts the detectors read. It is:

- **Incremental** — only NEW Form 4 accessions (``existing_accessions``) and only EOD bars newer than the
  latest stored one (``latest_bar_date``). A re-run of an already-current name appends ZERO rows; the
  append-only fact tables never silently grow (the read dedups; we stop the write).
- **Fail-visible** — each leg runs in its own try; a failure is CAPTURED into the name's result and the run
  continues (one bad name never aborts the others), improving on the scanner's bare ``except: pass``. Inside
  the Form 4 leg the tolerance is also PER-FILING: one unfetchable or unparseable filing (pre-2004-06-30
  Form 4s are SGML/text, not XML, and some ancient document URLs 404) is skipped-and-counted with a warning
  (``NameResult.form4_skipped``) — a single bad old filing never blanks the name's whole insider history.
  The live fetchers are polite (``ingest.http.polite_get`` retries 429 / transient 5xx with backoff; EDGAR
  also throttles ≤8 req/s).
- **No-lookahead** — ``recorded_at`` is left to the DB default ``now()`` (NEVER backdated), so a fact ingested
  today is invisible to an as-of read pinned at an earlier transaction time (the replay guarantee holds).
- **Exact membership (#2)** — it targets the member's RESOLVED ``security_id`` via ``master.get`` (the issuer
  ticker + CIK), never a fresh fuzzy resolve. Tenant comes from the thesis (one thesis = one tenant).

    python -m pipeline.ingest_thesis --thesis <uuid>
    python -m pipeline.ingest_thesis --thesis <uuid> --no-live   # cache-only (no network)
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from uuid import UUID

import psycopg

from db.session import connect
from domain.security import Security
from ingest.edgar.client import EdgarClient
from ingest.edgar.form4 import existing_accessions, ingest_form4
from ingest.edgar.submissions import fetch_submissions, form4_doc_url, form4_filings
from ingest.prices.ingest_security import ingest_bars_for_security
from ingest.prices.source import PriceSource, YahooPriceSource
from repositories import thesis_repo
from securities import master


@dataclass
class NameResult:
    """The per-name outcome — what was appended, what was tolerated-and-skipped, and the captured error
    if a leg failed (fail-visible)."""

    ticker: str | None
    security_id: UUID
    form4_appended: int
    price_bars_appended: int
    error: str | None = None
    form4_skipped: int = 0  # filings skipped per-filing (pre-XML era / unfetchable), never appended
    # overlap bars re-stored because the source RESTATED them (a split re-base; source-strategy A) —
    # the exceptional path, reported loudly only when nonzero
    price_bars_reversioned: int = 0


def _tolerable_filing_error(e: Exception) -> bool:
    """Is ``e`` ONE filing's fetch/parse failure (skip-and-count) rather than the whole leg's?

    Tolerated: an unparseable document (pre-2004-06-30 Form 4s are SGML/text, not XML →
    ``ET.ParseError``; a malformed value inside one → ``ValueError``) and a fetch that still fails after
    the polite retries (ancient accessions 404 → ``httpx.HTTPError``). Everything else — DB errors,
    ``CacheMiss`` with live pulls off, a missing User-Agent — is systemic, not one filing's fault, and
    must still abort the leg."""
    if isinstance(e, (ET.ParseError, ValueError)):
        return True
    try:
        import httpx  # lazy, mirroring the clients — the package imports without it
    except ImportError:  # pragma: no cover — with httpx absent, no httpx error can have been raised
        return False
    return isinstance(e, httpx.HTTPError)


def _form4_leg(
    conn: psycopg.Connection, client: EdgarClient, sec: Security, *, tenant_id: UUID
) -> tuple[int, int]:
    """Ingest only Form 4 accessions not already stored for this security (incremental). Returns
    ``(appended, skipped)``. Needs the issuer CIK; a name without one contributes no insider facts.

    PER-FILING tolerance: a filing whose fetch or parse fails (``_tolerable_filing_error``) is skipped
    with a printed warning and counted — never aborting the leg, so one bad old filing can't blank the
    name's whole insider history. A skipped accession is never stored, so every later run re-attempts it
    (and re-counts it) rather than silently marking it done."""
    if not sec.cik:
        return 0, 0
    seen = existing_accessions(conn, sec.id, tenant_id=tenant_id)
    appended = 0
    skipped = 0
    for f in form4_filings(fetch_submissions(client, sec.cik)):
        if f["accession"] in seen:
            continue  # already have this filing's txns — skip (no duplicate append)
        url = form4_doc_url(sec.cik, f["accession"], f["primary_doc"])
        doc = f["primary_doc"].rsplit("/", 1)[-1]
        try:
            xml = client.get_text(url, f"forms/{f['accession']}/{doc}")
            appended += ingest_form4(conn, sec.id, xml, f["accession"], tenant_id=tenant_id)
        except Exception as e:
            # A tolerated error can only fire BEFORE this filing's first row: parse_form4 fully parses
            # the doc before ingest_form4 appends anything (append failures are DB errors → re-raised),
            # so a skipped filing never leaves partial rows behind.
            if not _tolerable_filing_error(e):
                raise
            skipped += 1
            print(
                f"  warn: {sec.ticker or sec.id} form4 {f['accession']} ({f['filed']}) skipped: {e}"
            )
    return appended, skipped


# The price leg lives in ``ingest.prices.ingest_security.ingest_bars_for_security`` now — ONE
# implementation shared with the Workbench's per-name / per-section pull (the finalize screen needs
# real caps + hints BEFORE promote), so the incremental / cache-first / no-lookahead rules can't fork.


def ingest_thesis(
    conn: psycopg.Connection,
    thesis_id: UUID,
    *,
    allow_live: bool = True,
    force_refresh: bool = False,
    user_agent: str | None = None,
    price_source: PriceSource | None = None,
    edgar_client: EdgarClient | None = None,
) -> list[NameResult]:
    """Ingest insider + price facts for each RESOLVED basket member of ``thesis_id``.

    Per member: resolve the id to the master row (skip unresolved / foreign ids), then run the Form 4 leg
    and the price leg — EACH in its own try, COMMITTING on success and ROLLING BACK on failure, so one
    leg's error never discards the other's work and never aborts the run (the error is captured into the
    name's ``NameResult``). Incremental + no-lookahead (see the module docstring). Returns one ``NameResult``
    per member that had a resolved id.

    ``force_refresh`` makes the price leg bypass a stale cache hit (the recurring/daily path sets it; see
    ``eod_loader.fetch_eod``). ``price_source`` is the swappable EOD source (defaults to Yahoo).
    ``edgar_client`` lets the caller INJECT the client (else one is constructed) so it can read
    ``client.live_fetches`` afterwards — the daily cron does, to record the freeze-detector count.
    """
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise LookupError(f"thesis {thesis_id} not found")
    client = edgar_client or EdgarClient(allow_live=allow_live, user_agent=user_agent)
    source = price_source or YahooPriceSource()
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
        f4_skipped = 0
        try:
            f4, f4_skipped = _form4_leg(conn, client, sec, tenant_id=thesis.tenant_id)
            conn.commit()
        except (
            Exception
        ) as e:  # noqa: BLE001 — fail-visible: capture, roll back this leg, keep going
            conn.rollback()
            errs.append(f"form4: {e}")
        px_appended, px_reversioned = 0, 0
        try:
            bars = ingest_bars_for_security(
                conn,
                sec,
                tenant_id=thesis.tenant_id,
                allow_live=allow_live,
                force_refresh=force_refresh,
                source=source,
            )
            px_appended, px_reversioned = bars.appended, bars.reversioned
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            errs.append(f"price: {e}")
        results.append(
            NameResult(
                sec.ticker,
                sec.id,
                f4,
                px_appended,
                "; ".join(errs) or None,
                f4_skipped,
                price_bars_reversioned=px_reversioned,
            )
        )
    return results


def _report(results: list[NameResult]) -> int:
    """Print a per-name summary; return the number of names that errored (the process exit signal).
    Skips surface only when nonzero — loudness marks the exception."""
    total_f4 = sum(r.form4_appended for r in results)
    total_px = sum(r.price_bars_appended for r in results)
    total_sk = sum(r.form4_skipped for r in results)
    errored = [r for r in results if r.error]
    for r in results:
        tail = f"   ERROR: {r.error}" if r.error else ""
        skips = (
            f", {r.form4_skipped} form4 skipped (pre-XML era / unfetchable)"
            if r.form4_skipped
            else ""
        )
        print(
            f"  {r.ticker or r.security_id}: +{r.form4_appended} form4, "
            f"+{r.price_bars_appended} bars{skips}{tail}"
        )
    sk = f", {total_sk} form4 skipped" if total_sk else ""
    print(
        f"done: {len(results)} names, +{total_f4} insider txns, +{total_px} price bars{sk}, "
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
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="re-pull live + overwrite the cache (bypass a stale cache hit) — for a manual re-ingest",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        results = ingest_thesis(
            conn,
            UUID(args.thesis),
            allow_live=not args.no_live,
            force_refresh=args.force_refresh,
        )
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a cron / wrapper, non-silently


if __name__ == "__main__":
    main()
