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
from domain.enums import Archetype, CatalystType, Grade
from domain.thesis import BasketMember, Catalyst, Evidence, KillCriterion, Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.catalyst import ingest_catalyst
from ingest.doe import entities as doe_entities
from ingest.doe.client import UsaSpendingClient
from ingest.doe.feed import run_doe_feed
from ingest.edgar.converts import clean_filing_text, ingest_convert_terms, parse_convert_terms
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from ingest.theme_conviction import ingest_theme_conviction
from repositories import thesis_repo
from securities import master

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
        name="HIMS — insider conviction (system)",
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


def _persist_thesis(conn: psycopg.Connection, thesis: Thesis) -> None:
    """Upsert + the child-list writers: catalysts / kill criteria are no longer ``upsert`` children
    (the structural wipe-guard — a promote that doesn't carry them cannot wipe them), so the seed
    persists its authored lists through their sole writers."""
    thesis_repo.upsert(conn, thesis)
    tenant = thesis.tenant_id or DEFAULT_TENANT_ID
    thesis_repo.set_catalysts(conn, thesis.id, thesis.catalysts, tenant_id=tenant)
    thesis_repo.set_kill_criteria(conn, thesis.id, thesis.kill_criteria, tenant_id=tenant)


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
    _persist_thesis(conn, thesis)

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
        name="Small-scale nuclear (system)",
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
    _persist_thesis(conn, thesis)
    return thesis.id


def seed_nuclear_catalyst(conn: psycopg.Connection) -> None:
    """Operator-ratify OKLO's catalyst (#10 demo, the PROVISIONAL one) — the DOE Reactor Pilot Program OTA
    (DENE0009589), graded FLIP (a signed but $0-DOE-obligation authorization pathway: a real, not-yet-binding
    commitment -> small entry) with the agreement term (-> 2029-07-01) as its liveness horizon. Co-located
    with OKLO's 2026-06-02 breakout it arms OKLO as a disciplined STARTER. Paired with seed_leu_catalyst (the
    BINDING one); kept OUT of seed_nuclear, and separate from LEU, so each path tests in isolation.
    """
    ingest_catalyst(
        conn,
        OKLO_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=Grade.FLIP,
        label=(
            "DOE Reactor Pilot Program OTA (DENE0009589) — authorization pathway, "
            "$0 DOE obligation (company-funded)"
        ),
        source="ratified",
        source_ref="https://www.usaspending.gov/award/ASST_NON_DENE0009589_089",
        event_date=date(2026, 2, 9),
        horizon_end=date(2029, 7, 1),
        ratified_by="operator",
    )


def seed_leu_catalyst(conn: psycopg.Connection) -> None:
    """Operator-ratify LEU's catalyst (the BINDING one) — Centrus's DOE HALEU production contract
    (89243223CNE000030), graded CORE (a binding, multi-year federal production contract: ~$317M obligated
    AND exercised on USAspending = real contracted revenue -> build the position). Liveness = the contract's
    own BASE term (-> 2026-06-30); the ~$1.1B all-options ceiling to 2028 is DOE-discretion and is NOT folded
    in (option A: liveness = the binding horizon, not the optimistic one). Co-located with LEU's 2026-06-02
    core breakout it arms LEU as a real core_entry.

    Entity resolution: the USAspending recipient AMERICAN CENTRIFUGE OPERATING, LLC -> Centrus Energy (parent)
    -> LEU. This hand mapping is the FIRST ROW of the curated awardee->ticker table the automated
    DOE/USAspending feed will reuse (invariant #3: deterministic, never model-sourced).

    Near-the-edge by design: the base term ends 2026-06-30 — ~3wk past the demo asof — so the card's hold
    clock (exit_by) lands on 2026-06-30, surfacing a renewal/option cliff rather than an open-ended core hold.
    Kept OUT of seed_nuclear, and separate from OKLO, so each path tests in isolation.
    """
    ingest_catalyst(
        conn,
        LEU_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=Grade.CORE,
        label=(
            "DOE HALEU production contract (89243223CNE000030) — ~$317M obligated, "
            "base term through 2026-06-30 (options to 2028 at DOE discretion, excluded)"
        ),
        source="ratified",
        source_ref="https://www.usaspending.gov/award/CONT_AWD_89243223CNE000030_8900_-NONE-_-NONE-",
        event_date=date(2022, 11, 30),
        horizon_end=date(2026, 6, 30),
        ratified_by="operator",
    )


def seed_nuclear_theme_conviction(conn: psycopg.Connection) -> None:
    """Operator-ratify the small-scale-nuclear THEME conviction (M5b) — a thesis-level belief that supplies
    Key 1 as a FALLBACK for a confirmed basket member with no name-specific conviction of its own. Graded
    FLIP (capped at starter — belief never mints a core, rule 2) with a ~12-month operator horizon (re-
    ratify to keep it live, rule 3). Co-located with SMR's 2026-06-02 VOLUME-BACKED (CORE, ~1.96x) breakout
    it arms SMR as a disciplined theme-armed STARTER; NNE's MOMENTUM-ONLY (flip, ~1.19x) breakout is
    correctly not enough (rule 4: volume-backed only), so NNE stays in the watch tier — one theme-armed
    name, the volume gate working. Kept OUT of seed_nuclear and separate from the catalysts so each path
    tests in isolation; ranks beneath OKLO (own flip) and LEU (own core) — own-above-theme + freshness.
    """
    ingest_theme_conviction(
        conn,
        NUCLEAR_THESIS_ID,
        grade=Grade.FLIP,
        label=(
            "Structural small-scale-nuclear tailwind — the ADVANCE Act (2024) streamlines NRC "
            "licensing, DOE HALEU / reactor-pilot programs fund the build-out, and AI / datacenter "
            "power demand is surging (operator-ratified theme conviction)"
        ),
        source="ratified",
        source_ref="https://www.congress.gov/bill/118th-congress/senate-bill/1111",
        event_date=date(2026, 1, 15),
        horizon_end=date(
            2027, 1, 15
        ),  # ~12-month operator horizon (live at the 2026-06-05 demo asof)
        ratified_by="operator",
    )


def seed_doe_catalysts(conn: psycopg.Connection) -> list:
    """Run the DOE/USASpending AUTOMATED feed OFFLINE (committed fixtures) to emit the nuclear catalysts.

    This is the automated replacement for the hand-ratify bridge (seed_nuclear_catalyst / seed_leu_catalyst)
    for DOE awards: the feed discovers DOE awards for the curated entities, resolves them EXACTLY by
    recipient_id (no fuzzy matching), and derives grade + horizon from the structured terms. At the demo
    asof only the LIVE awards arm — LEU's $317M HALEU contract (core, -> 2026-06-30) headlines a core_entry;
    OKLO's reactor-pilot OTA (flip, -> 2029) sits beneath as a starter. Expired DOE awards are emitted too
    (real, provenanced) but liveness keeps them off the card. Caller commits.
    """
    client = UsaSpendingClient(cache_dir=_SEED_DATA / "doe", allow_live=False)
    ids = master.ids_for_tickers(conn, doe_entities.curated_tickers())
    return run_doe_feed(conn, client, ids.get)


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
        name="UNH — insider cluster (system)",
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
    _persist_thesis(conn, thesis)
    return thesis.id


# EDGAR provenance for the nuclear Workbench scoring facts — operator-ratified 2026-06 against the filings
# (the source_ref + the fact's natural identity). 10-K -> revenue-mix (purity); 10-Q -> shares + cash/burn.
_LEU_10Q = "https://www.sec.gov/Archives/edgar/data/1065059/000162828026030891/leu-20260331.htm"
_LEU_10K = "https://www.sec.gov/Archives/edgar/data/1065059/000162828026007117/leu-20251231.htm"
_SMR_10Q = "https://www.sec.gov/Archives/edgar/data/1822966/000182296626000054/smr-20260331.htm"
_SMR_10K = "https://www.sec.gov/Archives/edgar/data/1822966/000182296626000018/smr-20251231.htm"
_OKLO_10Q = "https://www.sec.gov/Archives/edgar/data/1849056/000162828026034095/oklo-20260331.htm"
_OKLO_10K = "https://www.sec.gov/Archives/edgar/data/1849056/000162828026018698/oklo-20251231.htm"
_NNE_10Q = "https://www.sec.gov/Archives/edgar/data/1923891/000149315226023071/form10-q.htm"
_NNE_10K = "https://www.sec.gov/Archives/edgar/data/1923891/000149315225028285/form10-k.htm"


def seed_nuclear_revenue_mix(conn: psycopg.Connection) -> None:
    """Operator-ratified exposure-PURITY facts for the nuclear basket (the Workbench purity meter).

    The BASIS is explicit in ``source`` so a revenue-backed 100% (SMR) and a pre-revenue 100% (OKLO/NNE)
    never flatten: ``10-k-segment`` = a real revenue-segment %, ``10-k-business-description`` = a pure-play
    read off the Item-1 Business section. Purity is exposure CONCENTRATION — NOT discounted for pre-revenue
    (runway + dilution carry funding risk). Real figures, ratified against the filings (2026-06).
    """
    rows = [
        (
            LEU_ID,
            "enrichment",
            77,
            "10-k-segment",
            _LEU_10K,
            date(2025, 12, 31),
            "LEU (enrichment) segment $346.2M of $448.7M total revenue, FY2025 (~77%).",
        ),
        (
            SMR_ID,
            "nuclear",
            100,
            "10-k-segment",
            _SMR_10K,
            date(2025, 12, 31),
            "Single reportable segment; 100% of $31.5M FY2025 revenue is SMR nuclear technology/services "
            "(revenue-backed).",
        ),
        (
            OKLO_ID,
            "nuclear",
            100,
            "10-k-business-description",
            _OKLO_10K,
            date(2025, 12, 31),
            "100% advanced-fission pure-play (Aurora powerhouses + fuel recycling); business-description basis, "
            "pre-revenue ($0 FY2025 revenue).",
        ),
        (
            NNE_ID,
            "nuclear",
            100,
            "10-k-business-description",
            _NNE_10K,
            date(2025, 9, 30),
            "100% advanced-nuclear / micro-reactor pure-play (KRONOS MMR); business-description basis, "
            "pre-commercial-revenue (trivial historical consulting/lease income).",
        ),
    ]
    for sid, seg, pct, source, ref, event_date, note in rows:
        ingest_revenue_mix(
            conn,
            sid,
            segment_label=seg,
            mix_pct=pct,
            source=source,
            source_ref=ref,
            event_date=event_date,
            note=note,
            ratified_by="operator",
        )


def seed_nuclear_shares(conn: psycopg.Connection) -> None:
    """Operator-ratified shares-outstanding facts (the Workbench market-cap basis) — latest 10-Q covers."""
    rows = [
        (
            LEU_ID,
            19_672_794,
            _LEU_10Q,
            date(2026, 5, 1),
            "Total economic = Class A 18,953,594 + Class B 719,200 (Class B is economic common, par "
            "$0.10; the A/B split is voting, not economics), as of 2026-05-01.",
        ),
        (
            SMR_ID,
            365_481_156,
            _SMR_10Q,
            date(2026, 4, 30),
            "Total economic = Class A 346,105,785 + Class B 19,375,371 (Up-C), as of 2026-04-30.",
        ),
        (
            OKLO_ID,
            173_990_987,
            _OKLO_10Q,
            date(2026, 5, 7),
            "Single common class as of 2026-05-07.",
        ),
        (NNE_ID, 52_083_294, _NNE_10Q, date(2026, 5, 12), "Common stock as of 2026-05-12."),
    ]
    for sid, shares, ref, event_date, note in rows:
        ingest_shares_outstanding(
            conn,
            sid,
            shares=shares,
            source="10-q-cover",
            source_ref=ref,
            event_date=event_date,
            note=note,
            ratified_by="operator",
        )


def seed_nuclear_cash_burn(conn: psycopg.Connection) -> None:
    """Operator-ratified cash + quarterly-burn facts (the Workbench runway basis) — latest 10-Qs.

    ``cash_usd`` is the UNIFORM runway numerator: cash + equivalents + ALL marketable securities (current
    AND noncurrent) — they are liquid Treasuries regardless of balance-sheet classification. NuScale's burn
    is the RECURRING figure (the one-time ENTRA1 settlement tranche backed out); see the note. (To be
    formalized as a config rule at the Slice-3 gate.)
    """
    rows = [
        (
            LEU_ID,
            1_868_200_000,
            35_100_000,
            _LEU_10Q,
            "Cash+equiv $1,868.2M (no marketable securities reported); operating cash use -$35.1M, Q1 2026.",
        ),
        (
            SMR_ID,
            1_008_763_000,
            50_483_000,
            _SMR_10Q,
            "Cash+equiv $341.1M + short-term investments $549.0M + noncurrent investments $118.6M = $1,008.8M "
            "(all marketable/Treasuries). Recurring burn = reported Q1'26 operating cash use -$314.678M less the "
            "-$264.195M ENTRA1 Milestone Contribution settlement tranche (a $507.4M strategic-partner obligation "
            "under the Partnership Milestones Agreement, recognized as FY2025 G&A, paid to ENTRA1 in tranches).",
        ),
        (
            OKLO_ID,
            2_536_898_000,
            17_867_000,
            _OKLO_10Q,
            "Cash+equiv $1,594.1M + marketable debt securities $942.8M (current $614.5M + noncurrent $328.3M) = "
            "$2,536.9M; operating cash use -$17.9M, Q1 2026.",
        ),
        (
            NNE_ID,
            568_895_558,
            5_264_361,
            _NNE_10Q,
            "Cash+equiv $197.7M + short-term investments $371.0M + marketable securities $0.22M = $568.9M; "
            "operating burn ~$5.26M, DERIVED as Q2 FY2026 (six-month YTD operating cash use $9.254M - Q1 $3.990M; "
            "the 10-Q discloses only a six-month cash-flow column).",
        ),
    ]
    for sid, cash, burn, ref, note in rows:
        ingest_cash_burn(
            conn,
            sid,
            cash_usd=cash,
            quarterly_burn_usd=burn,
            source="10-q",
            source_ref=ref,
            event_date=date(2026, 3, 31),
            note=note,
            ratified_by="operator",
        )


def main() -> None:
    conn = connect()
    try:
        hims_id = seed_hims(conn)
        nuclear_id = seed_nuclear(conn)
        seed_doe_catalysts(
            conn
        )  # the DOE/USASpending automated feed -> OKLO starter + LEU core_entry
        seed_nuclear_theme_conviction(
            conn
        )  # M5b -> SMR theme-armed starter (NNE stays watch: momentum-only)
        seed_nuclear_revenue_mix(conn)  # Workbench purity facts (operator-ratified, real 10-K)
        seed_nuclear_shares(conn)  # Workbench market-cap basis (real 10-Q covers)
        seed_nuclear_cash_burn(conn)  # Workbench runway basis (real 10-Q, uniform cash rule)
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
