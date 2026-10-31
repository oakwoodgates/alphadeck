"""Ingest a shares-outstanding fact — the basis for the Workbench's market-cap figure.

market cap = latest as-of close × latest as-of shares outstanding (derived on read, Option B). This is the
operator-ratified SOURCED fact behind the shares number (mirroring the catalyst bridge): ``source_ref`` is
the 10-Q cover / XBRL fact it came from — NEVER a model guess. Append-only; the caller owns the txn.

(Distinct from ``fact_dilution.shares_outstanding``, which the dilution clock reads on its own as-of/identity;
this is a general per-name source so a name without a convert still carries shares.)
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.acceptance import cached_listings, filing_date_bound, resolve_listed_filing

# ``https://www.sec.gov/Archives/edgar/data/<cik>/...`` — the CIK a cover citation names
_CIK_RE = re.compile(r"/edgar/data/(\d+)/")


def cik_of_source_ref(source_ref: str) -> int | None:
    """The CIK an EDGAR archive URL names, else ``None``."""
    m = _CIK_RE.search(source_ref or "")
    return int(m.group(1)) if m else None


def cover_rows(companyfacts: dict[str, Any] | None) -> list[dict[str, Any]]:
    """``dei:EntityCommonStockSharesOutstanding`` rows (the cover counts) of one companyfacts document."""
    node = (
        (companyfacts or {})
        .get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
    )
    return list(node.get("units", {}).get("shares", []))


def count_publication(
    rows: list[dict[str, Any]],
    listings: dict[str, tuple[str, str]],
    shares: float,
    as_of: date,
    cache_dir: Path | None = None,
) -> tuple[str | None, datetime | None]:
    """F9 — when a share count became PUBLIC: ``(accession, acceptance)`` of the EARLIEST filing whose
    companyfacts cover row carries exactly ``shares`` as of exactly ``as_of`` (the cited filing when the
    citation is right; a LATER filing's count cited under an earlier filing — the old reader's shape — gets the
    later filing's, so it is never early). The acceptance is the trusted one from the submissions index
    (``resolve_listed_filing``), else the END of the row's filing date in ET (a filing is accepted on or before
    the evening of its filing date — never early). ``(None, None)`` when no cover row carries the count: the
    ratification instant then stands (an operator-typed count counts from when it was made). A cover row dated
    AFTER its own filing (a filer's typo — ``ingest.edgar.extract.cover_after_filing``, the reader's refusal)
    never dates a count: it is ``pipeline.repair_shares_cover_dates``' to correct. PURE but for cached header
    reads."""
    from ingest.edgar.extract import (
        cover_after_filing,  # local: the extractor's import graph is wide
    )

    want = as_of.isoformat()
    hits = [
        r
        for r in rows
        if r.get("end") == want
        and r.get("filed")
        and r.get("accn")
        and _num(r.get("val")) == float(shares)
        and not cover_after_filing(as_of, date.fromisoformat(r["filed"]))
    ]
    if not hits:
        return None, None
    first = min(hits, key=lambda r: (r["filed"], r["accn"]))
    filed = date.fromisoformat(first["filed"])
    at = resolve_listed_filing(listings, first["accn"], filed, cache_dir).at
    return first["accn"], at if at is not None else filing_date_bound(filed)


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def shares_publication(
    cache_dir: Path | None, cik: str | int | None, shares: float, as_of: date
) -> tuple[str | None, datetime | None]:
    """``count_publication`` from the EDGAR cache ON DISK (the company's companyfacts + submissions index; never a
    request — the Workbench's get-data just warmed both). ``(None, None)`` when either is not cached.
    """
    if cache_dir is None or not cik:
        return None, None
    try:
        cf = json.loads(
            (Path(cache_dir) / "companyfacts" / f"CIK{int(cik):010d}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None, None
    return count_publication(
        cover_rows(cf), cached_listings(cache_dir, cik), shares, as_of, cache_dir
    )


def ingest_shares_outstanding(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    shares: float,
    source: str,
    source_ref: str,
    event_date: date,
    note: str | None = None,
    ratified_by: str | None = None,
    vouched: str | None = None,
    ads_ratio: int | None = None,
    ads_ratio_status: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
    filing_accession: str | None = None,
    filing_accepted: datetime | None = None,
) -> UUID:
    """Append a shares-outstanding fact (append-only; the caller owns the txn — no commit here).

    ``source_ref`` is the 10-Q cover / XBRL fact (provenance + identity); a restatement is a NEW row with a
    later ``recorded_at`` (latest-version-wins on the as-of read). ``vouched`` is confirm/override PROVENANCE
    ('confirmed' | 'overridden' | None) — never a scoring input. Returns the new fact id.

    ``ads_ratio`` / ``ads_ratio_status`` (annual-cover names, spec §10): derivation metadata for the
    market-cap scorer — ``shares`` stays the TRUE ordinary count from the cover; the ratio modulates the
    derivation, never the fact. None/None (every 10-Q name, every legacy row) = not applicable -> 1:1.

    ``filing_accession`` / ``filing_accepted`` (F9, migration 0072): the filing whose cover carries this count and
    its acceptance (``shares_publication``) — the version's public instant on history views (``db.public_clock``).
    None/None = the count matches no cover row: the ratification instant stands, as before.
    """
    values = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "shares": shares,
        "source": source,
        "source_ref": source_ref,
        "note": note,
        "ratified_by": ratified_by,
        "vouched": vouched,
        "ads_ratio": ads_ratio,
        "ads_ratio_status": ads_ratio_status,
        "valid_from": event_date,
        "filing_accession": filing_accession,
        "filing_accepted": filing_accepted,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    return append_fact(conn, "fact_shares_outstanding", values)
