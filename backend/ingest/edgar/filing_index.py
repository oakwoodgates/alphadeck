"""The filing-index ingest (2026-10-29) — every filing a basket company's EDGAR index lists, stored as a fact.

Appends one ``fact_filing_index`` row (migration 0070) per (security, filing) from the SAME submissions JSON the
Form 4 / 8-K / 13D/G legs already read (``ingest.edgar.submissions.index_filings``: every form in
``filings.recent``), so the whole index costs ZERO extra SEC requests. DISPLAY data: the research dashboards list
it (``dashboards/filings.py``); no detector, call or grade reads it.

- **Insert-only.** One SELECT of the accessions already stored for (tenant, security), then the not-yet-stored
  ones in ONE batched insert. A stored accession is never re-versioned: the first sighting wins. RESIDUAL,
  named: SEC's listed acceptance times drift (``ingest/edgar/acceptance.py`` — 25-45% of a capture at an hour
  EDGAR is closed, from 2026-10-23), so re-versioning on a changed listed field would churn the table nightly;
  the price is that a later correction of a listed field (a form, a primary document) is never picked up.
- **Per-filing fail-visible.** A row with no accession, no form or an unreadable filing date is SKIPPED and
  COUNTED with its reason (never silently dropped, never a reason to abort the company). An accession EDGAR lists
  twice in one index stores once (the first listing) and the repeat is counted.
- **The acceptance.** ``accepted`` is the listed SEC time only when it is TRUSTED — its ET date is the filing
  date and its ET time is within EDGAR's hours (``acceptance.trusted_listed``, request-free); otherwise NULL,
  and the set-aside is counted. Never a header fetch here: an index row's acceptance is display, and the public
  clock dates the row by ``filed``.
- **No-lookahead.** ``valid_from = filed`` (the public date); ``recorded_at`` is left to the DB's ``now()``,
  NEVER backdated (invariant #1).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.acceptance import parse_filing_date, trusted_listed
from ingest.edgar.form8k import filing_index_url

TABLE = "fact_filing_index"

# the per-filing skip reasons (the operator reads these on the run's report)
SKIP_NO_ACCESSION = "no accession number"
SKIP_NO_FORM = "no form type"
SKIP_BAD_DATE = "unreadable filing date"
SKIP_LISTED_TWICE = "listed twice in the index (the first listing is stored)"
# every reason a skip can start with (the bad-date one carries the raw value after it)
SKIP_REASONS: tuple[str, ...] = (SKIP_NO_ACCESSION, SKIP_NO_FORM, SKIP_BAD_DATE, SKIP_LISTED_TWICE)


@dataclass(frozen=True)
class FilingIndexResult:
    """One security's filing-index outcome: ``appended`` = filings stored for the first time; ``skipped`` = the
    rows that could not be stored, each ``(accession, or "row N" when it has none, reason)``;
    ``accepted_set_aside`` = how many of the APPENDED rows carried a listed acceptance time that was not trusted
    (stored NULL)."""

    appended: int = 0
    skipped: tuple[tuple[str, str], ...] = ()
    accepted_set_aside: int = 0


def stored_accessions(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> set[str]:
    """Every accession already stored for (tenant, security) — any version (the table is insert-only)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT accession FROM fact_filing_index "
            "WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        return {r["accession"] for r in cur.fetchall()}


def ingest_filing_index(
    conn: psycopg.Connection,
    security_id: UUID,
    cik: str | int,
    rows: Iterable[Mapping[str, Any]],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at: datetime | None = None,
) -> FilingIndexResult:
    """Append the not-yet-stored filings of one security's index (the caller owns the transaction — no commit).

    ``rows`` is ``submissions.index_filings`` output (raw strings). ``recorded_at`` is a TEST seam only —
    production leaves it to the DB's ``now()`` (never backdated, #1)."""
    seen = stored_accessions(conn, security_id, tenant_id=tenant_id)
    batch: list[dict[str, Any]] = []
    batched: set[str] = set()
    skipped: list[tuple[str, str]] = []
    set_aside = 0
    for i, r in enumerate(rows):
        accession = str(r.get("accession") or "").strip()
        form = str(r.get("form") or "").strip()
        label = accession or f"row {i}"
        if not accession:
            skipped.append((label, SKIP_NO_ACCESSION))
            continue
        if not form:
            skipped.append((label, SKIP_NO_FORM))
            continue
        raw_filed = str(r.get("filed") or "").strip()
        filed = parse_filing_date(raw_filed) if raw_filed else None
        if filed is None:
            skipped.append((label, f"{SKIP_BAD_DATE} {raw_filed!r}"))
            continue
        if accession in seen:
            continue  # already stored — insert-only, the first sighting wins
        if accession in batched:
            skipped.append((label, SKIP_LISTED_TWICE))
            continue
        raw_accepted = str(r.get("accepted_raw") or "").strip()
        accepted = trusted_listed(raw_accepted, filed) if raw_accepted else None
        if raw_accepted and accepted is None:
            set_aside += 1  # a listed time we do not trust: stored NULL, counted
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "security_id": security_id,
            "form": form,
            "accession": accession,
            "filed": filed,
            "primary_doc": str(r.get("primary_doc") or "").strip() or None,
            "accepted": accepted,
            "source_ref": filing_index_url(cik, accession),
            "valid_from": filed,
        }
        if recorded_at is not None:
            values["recorded_at"] = recorded_at
        batch.append(values)
        batched.add(accession)
    if batch:
        cols = list(batch[0])
        query = sql.SQL("INSERT INTO {table} ({cols}) VALUES ({vals})").format(
            table=sql.Identifier(TABLE),
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            vals=sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
        )
        with conn.cursor() as cur:
            cur.executemany(query, batch)  # ONE batched insert, never a round trip per filing
    return FilingIndexResult(
        appended=len(batch), skipped=tuple(skipped), accepted_set_aside=set_aside
    )
