"""Persistence for the SPAC Radar's two fact tables (migration 0030). Append-only discipline:
``record_*_if_changed`` reads the latest version of the natural key and appends ONLY on change —
the ``calls_repo.record_if_changed`` idiom, so a re-scan of the same window grows nothing
(idempotency tests COUNT THE TABLE, per convention)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today

# SIC 6770's official description, verbatim — the deterministic shell tell (`sector` stores
# EDGAR's sicDescription; the slice-0 FE classifier reads the same string).
BLANK_CHECKS = "Blank Checks"


def known_shell_ciks(conn: psycopg.Connection, *, tenant_id: UUID = DEFAULT_TENANT_ID) -> set[str]:
    """CIKs (zero-padded, the master's stored form) whose enriched sector reads blank-check —
    the radar's known-shell set (lazy accretion source (i))."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT cik FROM security_master "
            "WHERE tenant_id = %s AND sector = %s AND cik IS NOT NULL",
            (tenant_id, BLANK_CHECKS),
        )
        return {row["cik"] for row in cur.fetchall()}


def tickers_for(
    conn: psycopg.Connection, security_ids: list[UUID], *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> dict[UUID, str | None]:
    """Map master ids -> tickers (display join for the tape + the attach receipt)."""
    if not security_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker FROM security_master WHERE tenant_id = %s AND id = ANY(%s)",
            (tenant_id, security_ids),
        )
        return {row["id"]: row["ticker"] for row in cur.fetchall()}


@dataclass(frozen=True)
class SpacEvent:
    cik: str
    company_name: str
    form: str
    filed: date
    accession: str
    source_ref: str
    items: list[str] | None = None
    security_id: UUID | None = None


def record_event_if_changed(
    conn: psycopg.Connection, ev: SpacEvent, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> bool:
    """Append the event unless the latest version of this accession already says the same thing.
    The compare is the FACT surface (form / items / filed / security_id join) — a company-name
    casing drift alone never versions the log."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT form, items, filed, security_id FROM fact_spac_event
               WHERE tenant_id = %s AND accession = %s
               ORDER BY recorded_at DESC LIMIT 1""",
            (tenant_id, ev.accession),
        )
        row = cur.fetchone()
        if row is not None and (
            row["form"] == ev.form
            and row["items"] == ev.items
            and row["filed"] == ev.filed
            and row["security_id"] == ev.security_id
        ):
            return False
        cur.execute(
            """INSERT INTO fact_spac_event
               (tenant_id, cik, security_id, company_name, form, items, filed, accession,
                source_ref, valid_from)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tenant_id,
                ev.cik,
                ev.security_id,
                ev.company_name,
                ev.form,
                ev.items,
                ev.filed,
                ev.accession,
                ev.source_ref,
                ev.filed,
            ),
        )
        return True


@dataclass(frozen=True)
class SpacMatch:
    thesis_id: UUID
    cik: str
    accession: str
    matched_signal: list[str]
    matched_broad: list[str]
    truncated: bool
    source_ref: str
    filed: date


def record_match_if_changed(
    conn: psycopg.Connection, m: SpacMatch, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT matched_signal, matched_broad, truncated FROM fact_spac_match
               WHERE tenant_id = %s AND accession = %s AND thesis_id = %s
               ORDER BY recorded_at DESC LIMIT 1""",
            (tenant_id, m.accession, m.thesis_id),
        )
        row = cur.fetchone()
        if row is not None and (
            row["matched_signal"] == m.matched_signal
            and row["matched_broad"] == m.matched_broad
            and row["truncated"] == m.truncated
        ):
            return False
        cur.execute(
            """INSERT INTO fact_spac_match
               (tenant_id, thesis_id, cik, accession, matched_signal, matched_broad, truncated,
                source_ref, valid_from)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tenant_id,
                m.thesis_id,
                m.cik,
                m.accession,
                m.matched_signal,
                m.matched_broad,
                m.truncated,
                m.source_ref,
                m.filed,
            ),
        )
        return True


def list_events(
    conn: psycopg.Connection,
    *,
    days: int = 90,
    limit: int = 200,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> list[dict[str, Any]]:
    """The tape read: the LATEST version of each accession filed within the window, newest first."""
    cutoff = market_today() - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM (
                   SELECT DISTINCT ON (accession) *
                   FROM fact_spac_event
                   WHERE tenant_id = %s AND filed >= %s
                   ORDER BY accession, recorded_at DESC
               ) latest
               ORDER BY filed DESC, cik, accession
               LIMIT %s""",
            (tenant_id, cutoff, limit),
        )
        return list(cur.fetchall())


def events_for_ciks(
    conn: psycopg.Connection, ciks: list[str], *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> list[dict[str, Any]]:
    """The FULL latest-version event history for the given CIKs (no window) — the state derive
    must see history beyond the tape's display window or an old announcement reads as searching."""
    if not ciks:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (accession) *
               FROM fact_spac_event
               WHERE tenant_id = %s AND cik = ANY(%s)
               ORDER BY accession, recorded_at DESC""",
            (tenant_id, ciks),
        )
        return list(cur.fetchall())


def latest_matches(
    conn: psycopg.Connection, accessions: list[str], *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> list[dict[str, Any]]:
    """The latest version of every (accession, thesis) match row for the given accessions."""
    if not accessions:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (accession, thesis_id) *
               FROM fact_spac_match
               WHERE tenant_id = %s AND accession = ANY(%s)
               ORDER BY accession, thesis_id, recorded_at DESC""",
            (tenant_id, accessions),
        )
        return list(cur.fetchall())


def matched_terms_for(
    conn: psycopg.Connection,
    cik: str,
    thesis_id: UUID,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> list[str]:
    """The union of a CIK's matched terms for one thesis (latest version per accession) — the
    ``surfaced_terms`` provenance an attach freezes onto the new basket member."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (accession) matched_signal, matched_broad
               FROM fact_spac_match
               WHERE tenant_id = %s AND cik = %s AND thesis_id = %s
               ORDER BY accession, recorded_at DESC""",
            (tenant_id, cik, thesis_id),
        )
        terms: set[str] = set()
        for row in cur.fetchall():
            terms.update(row["matched_signal"] or [])
            terms.update(row["matched_broad"] or [])
        return sorted(terms)
