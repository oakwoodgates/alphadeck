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
from ingest.edgar.converts import clean_filing_text, ingest_convert_terms, parse_convert_terms
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from repositories import thesis_repo

# Fixed ids -> re-running the seed is idempotent and the curl URL is stable.
HIMS_SECURITY_ID = UUID("11150000-0000-0000-0000-000000000001")
HIMS_THESIS_ID = UUID("11150000-0000-0000-0000-000000000002")
_HIMS_CIK = "0001773751"
_WELLS_ACCESSION = "0001773751-26-000086"
_SEED_DATA = Path(__file__).resolve().parent.parent / "seed_data"

# --- Small-scale nuclear (M4a-ii): a multi-name THEME thesis (no single headline ticker) ---
NUCLEAR_THESIS_ID = UUID("2c1ea400-0000-0000-0000-000000000001")
SMR_ID = UUID("2c1ea400-0000-0000-0000-0000000000a1")
OKLO_ID = UUID("2c1ea400-0000-0000-0000-0000000000a2")
NNE_ID = UUID("2c1ea400-0000-0000-0000-0000000000a3")
LEU_ID = UUID("2c1ea400-0000-0000-0000-0000000000a4")
# (security_id, cik, ticker, company) — the basket members
_NUCLEAR_SECURITIES = [
    (SMR_ID, "0001822966", "SMR", "NuScale Power"),
    (OKLO_ID, "0001849056", "OKLO", "Oklo Inc"),
    (NNE_ID, "0001923891", "NNE", "Nano Nuclear Energy"),
    (LEU_ID, "0001065059", "LEU", "Centrus Energy"),
]


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
        (_SEED_DATA / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
    )
    ingest_prices(
        conn,
        security_id,
        parse_yahoo_chart(
            json.loads((_SEED_DATA / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
        ),
    )
    thesis = _hims_thesis(security_id)
    thesis_repo.upsert(conn, thesis)

    # the real ~$402.5M convertible-notes overhang, parsed deterministically from the committed 8-Ks
    terms = parse_convert_terms(
        clean_filing_text(
            (_SEED_DATA / "edgar" / "hims_converts_8k.htm").read_text(encoding="utf-8")
        ),
        clean_filing_text(
            (_SEED_DATA / "edgar" / "hims_converts_pricing.htm").read_text(encoding="utf-8")
        ),
    )
    ingest_convert_terms(
        conn,
        security_id,
        terms,
        accession="0001193125-26-234847",
        shares_outstanding=228_357_303,  # HIMS Q1-26 10-Q
        shares_outstanding_ref="0001773751-26-000076",
    )
    return thesis.id


def _ensure_security(conn: psycopg.Connection, sid: UUID, cik: str, ticker: str, name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
            (sid, DEFAULT_TENANT_ID, cik, ticker, name, date(2026, 1, 1)),
        )


def _nuclear_thesis() -> Thesis:
    return Thesis(
        id=NUCLEAR_THESIS_ID,
        tenant_id=DEFAULT_TENANT_ID,
        name="Small-scale nuclear",
        narrative=(
            "Sentiment has flipped in younger generations who didn't live through the meltdowns; "
            "clean power matters for the climate; the technology has improved (small-scale modular "
            "reactors vs giant ones); and power demand is surging (AI / high-performance compute). "
            "A multi-name bet on the SMR / advanced-nuclear build-out."
        ),
        ticker=None,  # a theme, not a single name
        basket=[
            BasketMember(
                ticker="SMR",
                role="Most de-risked SMR (NRC-approved design)",
                archetype=Archetype.LEADER,
                security_id=SMR_ID,
                detail="the leader",
            ),
            BasketMember(
                ticker="OKLO",
                role="High-profile microreactor pure-play",
                archetype=Archetype.HIGH_BETA,
                security_id=OKLO_ID,
                detail="high-beta",
            ),
            BasketMember(
                ticker="NNE",
                role="Early micro-reactor",
                archetype=Archetype.LOTTO,
                security_id=NNE_ID,
                detail="speculative",
            ),
            BasketMember(
                ticker="LEU",
                role="HALEU enrichment — the fuel supplier",
                archetype=Archetype.SHOVEL,
                security_id=LEU_ID,
                detail="picks-and-shovels",
            ),
        ],
        kill_criteria=[
            KillCriterion(
                id=UUID("2c1ea400-0000-0000-0000-0000000000d1"),
                text="A reactor incident or major regulatory setback re-freezes public sentiment",
            ),
            KillCriterion(
                id=UUID("2c1ea400-0000-0000-0000-0000000000d2"),
                text="AI / datacenter power demand stalls, removing the secular tailwind",
            ),
        ],
    )


def seed_nuclear(conn: psycopg.Connection) -> UUID:
    """Seed the small-scale-nuclear THEME thesis (idempotent). Caller commits. Returns the id.

    The basket broke out sector-wide on 2026-06-02 but has no insider-conviction signal, so this is
    an honest WARMING thesis: the platform won't arm a sector move without a conviction key.
    """
    for sid, cik, ticker, name in _NUCLEAR_SECURITIES:
        _ensure_security(conn, sid, cik, ticker, name)
        bars = parse_yahoo_chart(
            json.loads((_SEED_DATA / "prices" / f"{ticker}.yahoo.json").read_text(encoding="utf-8"))
        )
        ingest_prices(conn, sid, bars)
    thesis = _nuclear_thesis()
    thesis_repo.upsert(conn, thesis)
    return thesis.id


# --- UNH (M4a-iii): the May-2025 CEO-led open-market insider cluster — a CORE core_entry ---
UNH_SECURITY_ID = UUID("c0ffee00-0000-0000-0000-000000000001")
UNH_THESIS_ID = UUID("c0ffee00-0000-0000-0000-000000000002")
_UNH_CIK = "0000731766"
# the five real open-market BUY Form 4s (mid-May 2025, ~$31.6M total) — the parse oracle
_UNH_FORM4S = [
    ("0000731766-25-000145", "unh_hemsley_form4.xml"),  # CEO Stephen Hemsley ~$25.0M
    ("0000731766-25-000146", "unh_rex_form4.xml"),  # President/CFO John Rex ~$5.0M
    ("0000731766-25-000142", "unh_flynn_form4.xml"),  # director Timothy Flynn
    ("0000731766-25-000141", "unh_gil_form4.xml"),  # director Kristen Gil
    ("0000731766-25-000140", "unh_noseworthy_form4.xml"),  # director John Noseworthy
]


def _unh_thesis() -> Thesis:
    return Thesis(
        id=UNH_THESIS_ID,
        tenant_id=DEFAULT_TENANT_ID,
        name="UNH — insider cluster",
        narrative=(
            "After the selloff cut UnitedHealth ~46%, the board brought Stephen Hemsley back as CEO — "
            "and in a single week (mid-May 2025) he, the CFO, and three directors bought ~$31.6M of "
            "stock open-market. The strongest insider tell: senior management buying its own "
            "beaten-down stock in size. Conviction the franchise is mispriced — wait for the market to "
            "confirm the bottom before sizing up."
        ),
        ticker="UNH",
        basket=[
            BasketMember(
                ticker="UNH",
                role="the name",
                archetype=Archetype.LEADER,
                security_id=UNH_SECURITY_ID,
                detail="mega-cap insider-cluster conviction play",
            )
        ],
        evidence=[
            Evidence(
                id=UUID("c0ffee00-0000-0000-0000-0000000000e1"),
                kind="FORM 4",
                label="CEO Hemsley + CFO Rex + 3 directors bought $31.6M open-market (code P)",
                ref="0000731766-25-000145",
                date_label="mid-May 2025",
            )
        ],
        kill_criteria=[
            KillCriterion(
                id=UUID("c0ffee00-0000-0000-0000-0000000000d1"),
                text="The regulatory / DOJ overhang that drove the selloff materially worsens",
            ),
            KillCriterion(
                id=UUID("c0ffee00-0000-0000-0000-0000000000d2"),
                text="The insiders begin selling back the cluster",
            ),
        ],
    )


def seed_unh(conn: psycopg.Connection) -> UUID:
    """Seed the UNH insider-cluster thesis (idempotent). Caller commits. Returns the id.

    A 2025 case: the CEO-led CORE cluster (mid-May) WARMS, then ARMS at the August volume-backed
    breakout (a real core_entry) — the graded core-conviction horizon spans the ~3-month gap that the
    old flat 18-day clock dropped. At today's board date it has aged out to Incubating; scrub the
    as-of back to 2025 to see the warm -> arm arc.
    """
    _ensure_security(conn, UNH_SECURITY_ID, _UNH_CIK, "UNH", "UnitedHealth Group")
    for accession, fname in _UNH_FORM4S:
        ingest_form4(
            conn,
            UNH_SECURITY_ID,
            (_SEED_DATA / "edgar" / fname).read_text(encoding="utf-8"),
            accession,
        )
    ingest_prices(
        conn,
        UNH_SECURITY_ID,
        parse_yahoo_chart(
            json.loads((_SEED_DATA / "prices" / "UNH.yahoo.json").read_text(encoding="utf-8"))
        ),
    )
    thesis = _unh_thesis()
    thesis_repo.upsert(conn, thesis)
    return thesis.id


def main() -> None:
    conn = connect()
    try:
        hims_id = seed_hims(conn)
        nuclear_id = seed_nuclear(conn)
        unh_id = seed_unh(conn)
        conn.commit()
        print(f"seeded HIMS thesis:    {hims_id}")
        print(f"seeded nuclear thesis: {nuclear_id}")
        print(f"seeded UNH thesis:     {unh_id}")
        print(f'try: curl "http://127.0.0.1:8000/theses/{hims_id}/call?asof=2026-06-01"')
    finally:
        conn.close()


if __name__ == "__main__":
    main()
