"""Persistence for the fund launch radar's three fact tables (migration 0074). Append-only discipline, the
SPAC radar's idiom (``radar/repo.py``): every ``record_*_if_changed`` reads the latest version of the natural
key and appends ONLY on change, so a re-scan of the same window grows nothing (idempotency tests COUNT THE
TABLE). Never imports ``calls/`` — a fund filing is a tape, not a trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings

SOURCE_SEC = "sec_ticker_file"
SOURCE_NASDAQ = "nasdaq_directory"


@dataclass(frozen=True)
class FundFilingRow:
    accession: str
    form: str
    filer_ciks: list[str]
    filer_name: str
    accepted_at: datetime | None
    filing_date: date | None
    effectiveness_date: date | None
    private_to_public: bool
    released_on: date | None
    public_date: date
    source_ref: str


@dataclass(frozen=True)
class FundSeriesRow:
    accession: str
    series_key: str  # the series ID, or 'cik:<filer cik>' for a name-only (S-1) row
    series_id: str | None
    series_name: str
    kind: str
    classes: list[dict[str, Any]]
    public_date: date


@dataclass(frozen=True)
class SymbolRow:
    series_id: str
    class_id: str
    symbol: str
    source: str
    night: date


@dataclass(frozen=True)
class SeriesView:
    """One fund-in-a-filing as read back: the latest series row joined to the latest version of its filing."""

    accession: str
    form: str
    filer_name: str
    filer_ciks: list[str]
    public_date: date
    private_to_public: bool
    effectiveness_date: date | None
    series_key: str
    series_id: str | None
    series_name: str
    kind: str
    classes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def source_ref(self) -> str:
        cik = self.filer_ciks[0] if self.filer_ciks else "0"
        nodash = self.accession.replace("-", "")
        base = get_settings().sec_archives_base
        return f"{base}/{int(cik)}/{nodash}/{self.accession}-index.htm"


def record_filing_if_changed(
    conn: psycopg.Connection, f: FundFilingRow, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT form, filer_ciks, filer_name, accepted_at, filing_date, effectiveness_date,
                      private_to_public, released_on, public_date
               FROM fact_fund_filing WHERE tenant_id = %s AND accession = %s
               ORDER BY recorded_at DESC LIMIT 1""",
            (tenant_id, f.accession),
        )
        row = cur.fetchone()
        if row is not None and (
            row["form"] == f.form
            and list(row["filer_ciks"]) == f.filer_ciks
            and row["filer_name"] == f.filer_name
            and row["accepted_at"] == f.accepted_at
            and row["filing_date"] == f.filing_date
            and row["effectiveness_date"] == f.effectiveness_date
            and row["private_to_public"] == f.private_to_public
            and row["released_on"] == f.released_on
            and row["public_date"] == f.public_date
        ):
            return False
        cur.execute(
            """INSERT INTO fact_fund_filing
               (tenant_id, accession, form, filer_ciks, filer_name, accepted_at, filing_date,
                effectiveness_date, private_to_public, released_on, public_date, source_ref, valid_from)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tenant_id,
                f.accession,
                f.form,
                f.filer_ciks,
                f.filer_name,
                f.accepted_at,
                f.filing_date,
                f.effectiveness_date,
                f.private_to_public,
                f.released_on,
                f.public_date,
                f.source_ref,
                f.public_date,
            ),
        )
        return True


def record_series_if_changed(
    conn: psycopg.Connection, s: FundSeriesRow, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT series_id, series_name, kind, classes, valid_from FROM fact_fund_series
               WHERE tenant_id = %s AND accession = %s AND series_key = %s
               ORDER BY recorded_at DESC LIMIT 1""",
            (tenant_id, s.accession, s.series_key),
        )
        row = cur.fetchone()
        if row is not None and (
            row["series_id"] == s.series_id
            and row["series_name"] == s.series_name
            and row["kind"] == s.kind
            and row["classes"] == s.classes
            and row["valid_from"] == s.public_date
        ):
            return False
        cur.execute(
            """INSERT INTO fact_fund_series
               (tenant_id, accession, series_key, series_id, series_name, kind, classes, valid_from)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tenant_id,
                s.accession,
                s.series_key,
                s.series_id,
                s.series_name,
                s.kind,
                Jsonb(s.classes),
                s.public_date,
            ),
        )
        return True


def record_symbol_if_new(
    conn: psycopg.Connection, s: SymbolRow, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> bool:
    """A symbol is a fact of its first sighting: once stored for (series, class, symbol, source) it is never
    re-versioned (the night we FIRST saw it is the whole fact)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM fact_fund_symbol
               WHERE tenant_id = %s AND series_id = %s AND class_id = %s AND symbol = %s
                 AND source = %s LIMIT 1""",
            (tenant_id, s.series_id, s.class_id, s.symbol, s.source),
        )
        if cur.fetchone() is not None:
            return False
        cur.execute(
            """INSERT INTO fact_fund_symbol (tenant_id, series_id, class_id, symbol, source, valid_from)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (tenant_id, s.series_id, s.class_id, s.symbol, s.source, s.night),
        )
        return True


def series_views(
    conn: psycopg.Connection, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> list[SeriesView]:
    """Every stored fund-in-a-filing (latest version of each series row, joined to the latest version of its
    filing), newest public date first. The stage derive needs the FULL history of a series (a fund filed in
    March goes effective in June), so this takes no window; the tables are small (tens of funds a week).
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.accession, f.form, f.filer_name, f.filer_ciks, f.public_date,
                      f.private_to_public, f.effectiveness_date,
                      s.series_key, s.series_id, s.series_name, s.kind, s.classes
               FROM (SELECT DISTINCT ON (accession, series_key) *
                     FROM fact_fund_series WHERE tenant_id = %s
                     ORDER BY accession, series_key, recorded_at DESC) s
               JOIN (SELECT DISTINCT ON (accession) *
                     FROM fact_fund_filing WHERE tenant_id = %s
                     ORDER BY accession, recorded_at DESC) f USING (accession)
               ORDER BY f.public_date DESC, s.accession, s.series_key""",
            (tenant_id, tenant_id),
        )
        return [
            SeriesView(
                accession=r["accession"],
                form=r["form"],
                filer_name=r["filer_name"],
                filer_ciks=list(r["filer_ciks"]),
                public_date=r["public_date"],
                private_to_public=r["private_to_public"],
                effectiveness_date=r["effectiveness_date"],
                series_key=r["series_key"],
                series_id=r["series_id"],
                series_name=r["series_name"],
                kind=r["kind"],
                classes=list(r["classes"] or []),
            )
            for r in cur.fetchall()
        ]


def symbol_rows(
    conn: psycopg.Connection, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> list[SymbolRow]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT series_id, class_id, symbol, source, valid_from
               FROM fact_fund_symbol WHERE tenant_id = %s
               ORDER BY valid_from, series_id, class_id, symbol""",
            (tenant_id,),
        )
        return [
            SymbolRow(r["series_id"], r["class_id"], r["symbol"], r["source"], r["valid_from"])
            for r in cur.fetchall()
        ]
