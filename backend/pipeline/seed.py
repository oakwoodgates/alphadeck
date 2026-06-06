"""Seed the HIMS demo thesis so Checkpoint A is curl-able locally.

A DEV convenience: it ingests the committed real-data fixtures (the same Wells Form 4 + HIMS EOD the
tests use) and upserts the HIMS thesis under fixed ids, so re-running is idempotent and the curl URL
is stable. Run after `docker compose up` + `python -m db.migrate`:

    python -m pipeline.seed
    curl "http://127.0.0.1:8000/theses/<printed-id>/call?asof=2026-06-01"

A live seed (resolve HIMS + pull from EDGAR/Yahoo) can replace the fixture ingest later; the thesis
definition and the wiring stay the same.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID, connect
from domain.enums import Archetype
from domain.thesis import BasketMember, Catalyst, Evidence, KillCriterion, Thesis
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from repositories import thesis_repo

# Fixed ids -> re-running the seed is idempotent and the curl URL is stable.
HIMS_SECURITY_ID = UUID("11150000-0000-0000-0000-000000000001")
HIMS_THESIS_ID = UUID("11150000-0000-0000-0000-000000000002")
_HIMS_CIK = "0001773751"
_WELLS_ACCESSION = "0001773751-26-000086"
_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _ensure_hims_security(conn: psycopg.Connection) -> UUID:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
            (
                HIMS_SECURITY_ID,
                DEFAULT_TENANT_ID,
                _HIMS_CIK,
                "HIMS",
                "Hims & Hers Health",
                date(2026, 1, 1),
            ),
        )
    return HIMS_SECURITY_ID


def _hims_thesis(security_id: UUID) -> Thesis:
    return Thesis(
        id=HIMS_THESIS_ID,
        tenant_id=DEFAULT_TENANT_ID,
        name="HIMS — insider conviction",
        narrative=(
            "Director David Wells bought ~$1.2M HIMS open-market off the lows — a high-USD senior "
            "insider buy. Watching for the market to confirm with a volume-backed breakout."
        ),
        ticker="HIMS",
        basket=[
            BasketMember(
                ticker="HIMS",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
                detail="single-name conviction play",
            )
        ],
        evidence=[
            Evidence(
                id=UUID("11150000-0000-0000-0000-0000000000e1"),
                kind="FORM 4",
                label="David Wells bought ~$1.17M open-market (code P)",
                ref=_WELLS_ACCESSION,
                date_label="late May",
            )
        ],
        catalysts=[
            Catalyst(
                id=UUID("11150000-0000-0000-0000-0000000000c1"),
                label="Next quarterly earnings",
                kind="earnings",
                when_date=date(2026, 8, 4),
                when_label="~Q2",
            )
        ],
        kill_criteria=[
            KillCriterion(
                id=UUID("11150000-0000-0000-0000-0000000000d1"),
                text="Closes back below the breakout base on rising volume",
            )
        ],
    )


def seed_hims(conn: psycopg.Connection) -> UUID:
    """Ingest the HIMS fixtures + upsert the HIMS thesis (idempotent). Caller commits. Returns the id."""
    security_id = _ensure_hims_security(conn)
    ingest_form4(
        conn,
        security_id,
        (_FIXTURES / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
    )
    ingest_prices(
        conn,
        security_id,
        parse_yahoo_chart(
            json.loads((_FIXTURES / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
        ),
    )
    thesis = _hims_thesis(security_id)
    thesis_repo.upsert(conn, thesis)
    return thesis.id


def main() -> None:
    conn = connect()
    try:
        thesis_id = seed_hims(conn)
        conn.commit()
        print(f"seeded HIMS thesis: {thesis_id}")
        print(f'try: curl "http://127.0.0.1:8000/theses/{thesis_id}/call?asof=2026-06-01"')
    finally:
        conn.close()


if __name__ == "__main__":
    main()
