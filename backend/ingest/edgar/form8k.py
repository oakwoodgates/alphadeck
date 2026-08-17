"""8-K corporate-event ingest (Band 03 S3) — the item-code tape's evidence layer.

Appends one ``fact_corporate_event`` row per (security, 8-K filing) from the SAME submissions JSON
the Form 4 leg already fetches — the ``items`` array parallels ``accessionNumber``, so the whole
tape costs ZERO extra document fetches. Store EVERY 8-K with its items, not just the detector cut
(#9 recall): the deferred cadence/uplisting/reverse-split slices ride this same table, no re-ingest.

Append-if-changed (the SPAC radar's ``record_event_if_changed`` semantics, in the form4-leg shape):

- a NEW accession appends (``appended``);
- a stored accession whose fact surface (form / items / filed) is unchanged appends NOTHING — a
  re-run is count-the-table idempotent;
- a stored accession whose surface CHANGED — the load-bearing case being ``items`` resolving from
  NULL once EDGAR processes the filing — appends a new VERSION (``reversioned``; the bitemporal
  correction discipline: a new row, never an UPDATE).

``valid_from = filed`` (the EDGAR acceptance date IS the knowability — no-lookahead is free here);
``recorded_at`` is left to the DB's ``now()``, NEVER backdated (invariant #4). The natural-key
constraint carries ``security_id`` (migration 0038 — the 0037 lesson), so an issuer held as two
master rows stores the same filing once per security scope without same-instant collisions.

Deterministic end-to-end (#3): forms + item codes only — no document text, no LLM. The located
passage is a DEFERRED, operator-priced follow-up; v1 provenance = accession + items + the EDGAR
filing-index URL (the ``dilution_clock`` 8-K shape).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings


@dataclass(frozen=True)
class Form8kResult:
    """One security's 8-K ingest outcome: ``appended`` = brand-new filings stored; ``reversioned`` =
    new VERSIONS of already-stored filings whose fact surface changed (items resolved / a corrected
    filed date) — the exceptional path, reported loudly only when nonzero."""

    appended: int = 0
    reversioned: int = 0


def filing_index_url(cik: str | int, accession: str) -> str:
    """The EDGAR filing-index URL for an accession — the row's checkable source (#6). The same shape
    the SPAC radar records (``radar/spac.py::_filing_index_url``)."""
    nodash = accession.replace("-", "")
    return f"{get_settings().sec_archives_base}/{int(cik)}/{nodash}/{accession}-index.htm"


def existing_8k_events(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> dict[str, dict[str, Any]]:
    """The LATEST stored version of each 8-K accession for (tenant, security) — the compare surface
    (form / items / filed) the append-if-changed reads. Accession is the filing identity (the
    natural key's lead within the security scope); the latest version per accession is what the
    as-of read would return at now, so "unchanged vs latest" == "the tape already says this"."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (accession) accession, form, items, filed "
            "FROM fact_corporate_event WHERE tenant_id = %s AND security_id = %s "
            "ORDER BY accession, recorded_at DESC, id DESC",
            (tenant_id, security_id),
        )
        return {r["accession"]: r for r in cur.fetchall()}


def ingest_form8k(
    conn: psycopg.Connection,
    security_id: UUID,
    cik: str | int,
    filings: list[dict[str, Any]],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> Form8kResult:
    """Append-if-changed the 8-K tape for one security (the caller owns the txn — no commit here).

    ``filings`` is ``submissions.form8k_filings`` output: ``{accession, form, filed, items}`` with
    ``filed`` an ISO date string and ``items`` a ``list[str] | None`` (None = not-yet-resolved —
    stored as NULL and re-versioned by a later run once it resolves). A filing whose surface matches
    the stored latest version appends nothing (count-the-table idempotent). ``recorded_at`` is a
    TEST seam only — production leaves it to the DB's ``now()`` (never backdated, #4).
    """
    existing = existing_8k_events(conn, security_id, tenant_id=tenant_id)
    appended = 0
    reversioned = 0
    for f in filings:
        filed = date.fromisoformat(f["filed"])
        items = f["items"]
        prior = existing.get(f["accession"])
        if prior is not None and (
            prior["form"] == f["form"] and prior["items"] == items and prior["filed"] == filed
        ):
            continue  # the tape already says this — no duplicate append
        values = {
            "tenant_id": tenant_id,
            "security_id": security_id,
            "form": f["form"],
            "items": items,
            "accession": f["accession"],
            "filed": filed,
            "source_ref": filing_index_url(cik, f["accession"]),
            "valid_from": filed,
        }
        if recorded_at is not None:
            values["recorded_at"] = recorded_at
        append_fact(conn, "fact_corporate_event", values)
        if prior is None:
            appended += 1
        else:
            reversioned += 1
    return Form8kResult(appended=appended, reversioned=reversioned)
