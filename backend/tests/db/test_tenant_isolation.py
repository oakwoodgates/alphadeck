"""The poison-row tenant-isolation proof + the production-cut smoke (Phase-1, Step 3).

This is the load-bearing verification for the production cut: it certifies — structurally, the same
discipline-plus-test posture as the harness's lookahead boundary — that a DEMO fact NEVER appears in a
PRODUCTION read and vice versa, that an empty production tenant yields an honest ``Incubating`` (never a
crash or a demo leak), and that the call of record lands in the thesis's tenant. Isolation is the
discipline "every read passes the right tenant_id" (no DB-level RLS — the security_id FK carries no
tenant); these tests are what certify that discipline holds end-to-end.

DB-backed; skips when Postgres is unreachable (the shared ``db`` fixture). Everything runs in the test's
transaction and is rolled back at teardown — including the provisioned production tenant — so no row
persists between tests (the fixed PROD id + provision_tenant's ON CONFLICT keep it idempotent regardless).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from db.bitemporal import append_fact, as_of, as_of_thesis
from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype, State, Verdict
from domain.thesis import BasketMember, Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from pipeline.call_for_thesis import call_for_thesis
from pipeline.provision_tenant import provision_tenant
from repositories import thesis_repo
from securities import master
from signals.base import PointInTimeData
from workbench.chain_draft import (
    PlacementStatus,
    ProposedPlacement,
    ProposedSegment,
    resolve_placements,
)
from workbench.scoring import score_member

# A FIXED production tenant id, distinct from DEFAULT_TENANT_ID (the demo). Fixed (not random) because the
# `db` fixture does NOT truncate `tenant`; combined with provision_tenant's ON CONFLICT, re-runs are
# idempotent and never accrete tenant rows. The trailing 'ad' is just a readable marker (Alpha Deck).
PROD_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ad")

_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_ARM_ASOF = date(
    2026, 6, 1
)  # the proven HIMS breakout date (co-located with the Wells cluster buy)


def _security(
    db, tenant_id: uuid.UUID, *, ticker: str = "HIMS", cik: str = "0001773751"
) -> uuid.UUID:
    """Insert one security_master row under ``tenant_id`` and return its id. security_master is per-tenant,
    so each tenant owns its OWN row even for the same ticker — the realistic poison-row setup (a prod read
    must not pick up a demo row of the same name)."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, tenant_id, ticker, cik, date(2026, 1, 1)),
        )
    return sid


def _arm_facts(db, security_id: uuid.UUID, tenant_id: uuid.UUID, accession: str) -> None:
    """Ingest the proven HIMS arm fixtures (the Wells cluster buy + the breakout bars) under ``tenant_id``
    with a per-tenant ``accession`` — so the re-derived call's provenance reveals WHICH tenant's facts it
    read (the call-level isolation proof, not just the row-level one)."""
    ingest_form4(
        db,
        security_id,
        (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        accession,
        tenant_id=tenant_id,
    )
    ingest_prices(
        db,
        security_id,
        parse_yahoo_chart(
            json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
        ),
        tenant_id=tenant_id,
    )


def _single_name_thesis(security_id: uuid.UUID, tenant_id: uuid.UUID, name: str) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        narrative="Insider conviction; waiting for the market to confirm.",
        ticker="HIMS",
        basket=[
            BasketMember(
                ticker="HIMS",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
            )
        ],
    )


def _refs(card) -> list[str]:
    return [p.ref for t in card.triggers_fired for p in t.sources]


# ---------------------------------------------------------------------------------------------------------
# (a) Read isolation, both scope axes — no fact read crosses tenants.
# ---------------------------------------------------------------------------------------------------------
def test_read_isolation_both_axes(db):
    """A demo fact and a prod fact at the same as-of are each visible ONLY under their own tenant, and the
    CROSS-reads (a tenant querying the other tenant's security/thesis id) return nothing — proving the
    ``WHERE tenant_id`` filter on every ``as_of`` / ``as_of_thesis`` read is load-bearing, not just the
    scope-id."""
    provision_tenant(db, "prod-isolation", tenant_id=PROD_TENANT_ID)
    demo_sec = _security(db, DEFAULT_TENANT_ID)
    prod_sec = _security(db, PROD_TENANT_ID)

    # security-scoped axis: one insider fact per tenant, same valid_from
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": demo_sec,
            "accession": "DEMO-0001",
            "insider_name": "Demo Insider",
            "txn_code": "P",
            "valid_from": date(2026, 5, 20),
        },
    )
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": PROD_TENANT_ID,
            "security_id": prod_sec,
            "accession": "PROD-0001",
            "insider_name": "Prod Insider",
            "txn_code": "P",
            "valid_from": date(2026, 5, 20),
        },
    )
    asof = date(2026, 6, 1)

    demo_rows = as_of(
        db,
        "fact_insider_txn",
        security_id=demo_sec,
        asof=asof,
        known_at=_KNOWN,
        tenant_id=DEFAULT_TENANT_ID,
    )
    prod_rows = as_of(
        db,
        "fact_insider_txn",
        security_id=prod_sec,
        asof=asof,
        known_at=_KNOWN,
        tenant_id=PROD_TENANT_ID,
    )
    assert [r["accession"] for r in demo_rows] == ["DEMO-0001"]
    assert [r["accession"] for r in prod_rows] == ["PROD-0001"]

    # cross-reads: querying the OTHER tenant's security id under your own tenant sees nothing — the tenant
    # filter excludes it even though the security id is real (in the other tenant).
    assert (
        as_of(
            db,
            "fact_insider_txn",
            security_id=prod_sec,
            asof=asof,
            known_at=_KNOWN,
            tenant_id=DEFAULT_TENANT_ID,
        )
        == []
    )
    assert (
        as_of(
            db,
            "fact_insider_txn",
            security_id=demo_sec,
            asof=asof,
            known_at=_KNOWN,
            tenant_id=PROD_TENANT_ID,
        )
        == []
    )

    # thesis-scoped axis (as_of_thesis, the other entry point): a prod theme conviction is invisible to demo.
    prod_thesis = _single_name_thesis(prod_sec, PROD_TENANT_ID, "prod theme")
    thesis_repo.upsert(db, prod_thesis)
    append_fact(
        db,
        "fact_theme_conviction",
        {
            "tenant_id": PROD_TENANT_ID,
            "thesis_id": prod_thesis.id,
            "grade": "flip",
            "label": "prod theme conviction",
            "source": "ratified",
            "source_ref": "PROD-THEME-0001",
            "valid_from": date(2026, 5, 1),
        },
    )
    seen_prod = as_of_thesis(
        db,
        "fact_theme_conviction",
        thesis_id=prod_thesis.id,
        asof=asof,
        known_at=_KNOWN,
        tenant_id=PROD_TENANT_ID,
    )
    assert [r["source_ref"] for r in seen_prod] == ["PROD-THEME-0001"]
    assert (
        as_of_thesis(
            db,
            "fact_theme_conviction",
            thesis_id=prod_thesis.id,
            asof=asof,
            known_at=_KNOWN,
            tenant_id=DEFAULT_TENANT_ID,
        )
        == []
    )


# ---------------------------------------------------------------------------------------------------------
# (b) Empty production tenant -> honest no-data (never a crash, never a demo leak).
# ---------------------------------------------------------------------------------------------------------
def test_empty_prod_tenant_is_honest_incubating(db):
    """An empty production tenant whose thesis names the SAME ticker as a fully-armed demo thesis still
    re-derives to ``Incubating`` with no triggers — the demo arm does not bleed across the tenant seam.
    """
    # demo is fully seeded + armed (the bleed source)
    demo_sec = _security(db, DEFAULT_TENANT_ID)
    _arm_facts(db, demo_sec, DEFAULT_TENANT_ID, "DEMO-WELLS-0001")
    demo_thesis = _single_name_thesis(demo_sec, DEFAULT_TENANT_ID, "demo HIMS")
    thesis_repo.upsert(db, demo_thesis)
    assert (
        call_for_thesis(db, demo_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False).state
        is State.ARMED
    )

    # prod: provisioned, a thesis on a prod HIMS security, but NO prod facts
    provision_tenant(db, "prod-empty", tenant_id=PROD_TENANT_ID)
    prod_sec = _security(db, PROD_TENANT_ID)
    prod_thesis = _single_name_thesis(prod_sec, PROD_TENANT_ID, "prod HIMS")
    thesis_repo.upsert(db, prod_thesis)

    card = call_for_thesis(db, prod_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False)
    assert card.state is State.INCUBATING
    assert card.triggers_fired == []


# ---------------------------------------------------------------------------------------------------------
# (c) Production re-derives from PRODUCTION facts only — the call-level isolation proof.
# ---------------------------------------------------------------------------------------------------------
def test_prod_call_rederives_from_prod_facts_only(db):
    """With BOTH tenants armed off the same fixtures under DISTINCT accessions, each tenant's call cites
    only ITS OWN provenance — the prod card carries the prod accession and never the demo one, and vice
    versa. The pit threaded ``thesis.tenant_id`` into every read, so the cards cannot cross-contaminate.
    """
    demo_sec = _security(db, DEFAULT_TENANT_ID)
    _arm_facts(db, demo_sec, DEFAULT_TENANT_ID, "DEMO-WELLS-0001")
    demo_thesis = _single_name_thesis(demo_sec, DEFAULT_TENANT_ID, "demo HIMS")
    thesis_repo.upsert(db, demo_thesis)

    provision_tenant(db, "prod-rederive", tenant_id=PROD_TENANT_ID)
    prod_sec = _security(db, PROD_TENANT_ID)
    _arm_facts(db, prod_sec, PROD_TENANT_ID, "PROD-WELLS-0001")
    prod_thesis = _single_name_thesis(prod_sec, PROD_TENANT_ID, "prod HIMS")
    thesis_repo.upsert(db, prod_thesis)

    prod_card = call_for_thesis(db, prod_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False)
    demo_card = call_for_thesis(db, demo_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False)

    assert prod_card.state is State.ARMED and demo_card.state is State.ARMED
    assert prod_card.armed_security_id == prod_sec
    assert "PROD-WELLS-0001" in _refs(prod_card)
    assert "DEMO-WELLS-0001" not in _refs(
        prod_card
    )  # the demo provenance never leaks into the prod call
    assert "DEMO-WELLS-0001" in _refs(demo_card)
    assert "PROD-WELLS-0001" not in _refs(demo_card)


# ---------------------------------------------------------------------------------------------------------
# (d) The call of record is written under the thesis's tenant (Option B, proven end-to-end).
# ---------------------------------------------------------------------------------------------------------
def test_call_of_record_writes_thesis_tenant(db):
    """A ``record=True`` call appends the call of record under the THESIS's tenant — a prod call lands in
    prod, a demo call lands in demo. (Threading the read tenant but hardcoding the write would split the
    record across tenants; Option B threads ``thesis.tenant_id`` into the writer.)"""
    demo_sec = _security(db, DEFAULT_TENANT_ID)
    _arm_facts(db, demo_sec, DEFAULT_TENANT_ID, "DEMO-WELLS-0001")
    demo_thesis = _single_name_thesis(demo_sec, DEFAULT_TENANT_ID, "demo HIMS")
    thesis_repo.upsert(db, demo_thesis)

    provision_tenant(db, "prod-write", tenant_id=PROD_TENANT_ID)
    prod_sec = _security(db, PROD_TENANT_ID)
    _arm_facts(db, prod_sec, PROD_TENANT_ID, "PROD-WELLS-0001")
    prod_thesis = _single_name_thesis(prod_sec, PROD_TENANT_ID, "prod HIMS")
    thesis_repo.upsert(db, prod_thesis)

    call_for_thesis(db, demo_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=True)
    call_for_thesis(db, prod_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=True)

    with db.cursor() as cur:
        cur.execute("SELECT tenant_id FROM calls WHERE thesis_id = %s", (prod_thesis.id,))
        prod_tenants = {r["tenant_id"] for r in cur.fetchall()}
        cur.execute("SELECT tenant_id FROM calls WHERE thesis_id = %s", (demo_thesis.id,))
        demo_tenants = {r["tenant_id"] for r in cur.fetchall()}
    assert prod_tenants == {PROD_TENANT_ID}
    assert demo_tenants == {DEFAULT_TENANT_ID}


# ---------------------------------------------------------------------------------------------------------
# Smoke — the cut, demonstrated end-to-end: provision -> ingest one tenant's facts -> call re-derives Armed.
# ---------------------------------------------------------------------------------------------------------
def test_production_cut_smoke(db):
    """The whole cut on the identical code path: provision a fresh production tenant, ingest its own facts
    (the proven cluster-buy + breakout arm path), upsert a production thesis, and re-derive an Armed call
    isolated to production — with the per-tenant security master resolving prod's ticker and NOT bleeding
    to demo. This is the structural demonstration of an empty tenant becoming a real, isolated, Armed call;
    the operator's actual real-data population is their ongoing use, not this test."""
    tid = provision_tenant(db, "production", tenant_id=PROD_TENANT_ID)
    assert tid == PROD_TENANT_ID

    prod_sec = _security(db, PROD_TENANT_ID, ticker="HIMS", cik="0001773751")
    _arm_facts(db, prod_sec, PROD_TENANT_ID, "PROD-SMOKE-0001")
    prod_thesis = _single_name_thesis(prod_sec, PROD_TENANT_ID, "HIMS — production")
    thesis_repo.upsert(db, prod_thesis)

    card = call_for_thesis(db, prod_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=True)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.STARTER_ENTRY
    assert "PROD-SMOKE-0001" in _refs(card)

    # the per-tenant security master resolves prod's name under prod, and not under demo (cross-tenant ->
    # omitted) — the same threading the API's ticker resolution relies on.
    assert master.tickers_for(db, {prod_sec}, tenant_id=PROD_TENANT_ID) == {prod_sec: "HIMS"}
    assert master.tickers_for(db, {prod_sec}, tenant_id=DEFAULT_TENANT_ID) == {}


# ---------------------------------------------------------------------------------------------------------
# The Workbench scoring facts (Slice 2) — every new read surface stays tenant-isolated.
# ---------------------------------------------------------------------------------------------------------
def test_scoring_facts_read_isolation(db):
    """The three new scoring-fact accessors (revenue_mix / shares_outstanding / cash_burn) are tenant-scoped
    like every other fact read: a demo fact and a prod fact on same-ticker securities are each visible ONLY
    under their own tenant, and the cross-reads return []. Grows the isolation proof as new read surfaces
    land — discipline-not-RLS only holds if each new path stays on the tenant-filtered accessor."""
    provision_tenant(db, "prod-scoring", tenant_id=PROD_TENANT_ID)
    demo_sec = _security(db, DEFAULT_TENANT_ID)
    prod_sec = _security(db, PROD_TENANT_ID)

    ingest_revenue_mix(
        db,
        demo_sec,
        segment_label="telehealth",
        mix_pct=90,
        source="ratified",
        source_ref="DEMO-MIX",
        event_date=date(2026, 1, 1),
    )
    ingest_revenue_mix(
        db,
        prod_sec,
        segment_label="nuclear",
        mix_pct=100,
        source="ratified",
        source_ref="PROD-MIX",
        event_date=date(2026, 1, 1),
        tenant_id=PROD_TENANT_ID,
    )
    ingest_shares_outstanding(
        db,
        demo_sec,
        shares=228_000_000,
        source="ratified",
        source_ref="DEMO-SH",
        event_date=date(2026, 1, 1),
    )
    ingest_shares_outstanding(
        db,
        prod_sec,
        shares=141_000_000,
        source="ratified",
        source_ref="PROD-SH",
        event_date=date(2026, 1, 1),
        tenant_id=PROD_TENANT_ID,
    )
    ingest_cash_burn(
        db,
        demo_sec,
        cash_usd=1_000_000_000,
        quarterly_burn_usd=0,
        source="ratified",
        source_ref="DEMO-CB",
        event_date=date(2026, 1, 1),
    )
    ingest_cash_burn(
        db,
        prod_sec,
        cash_usd=280_000_000,
        quarterly_burn_usd=25_000_000,
        source="ratified",
        source_ref="PROD-CB",
        event_date=date(2026, 1, 1),
        tenant_id=PROD_TENANT_ID,
    )

    asof = date(2026, 6, 1)
    demo_pit = PointInTimeData(db, asof=asof, known_at=_KNOWN, tenant_id=DEFAULT_TENANT_ID)
    prod_pit = PointInTimeData(db, asof=asof, known_at=_KNOWN, tenant_id=PROD_TENANT_ID)

    def _assert_isolated(accessor: str, demo_ref: str, prod_ref: str) -> None:
        assert [r["source_ref"] for r in getattr(demo_pit, accessor)(demo_sec)] == [demo_ref]
        assert [r["source_ref"] for r in getattr(prod_pit, accessor)(prod_sec)] == [prod_ref]
        # cross-reads: querying the OTHER tenant's security under your own tenant sees nothing.
        assert getattr(demo_pit, accessor)(prod_sec) == []
        assert getattr(prod_pit, accessor)(demo_sec) == []

    _assert_isolated("revenue_mix_facts", "DEMO-MIX", "PROD-MIX")
    _assert_isolated("shares_outstanding_facts", "DEMO-SH", "PROD-SH")
    _assert_isolated("cash_burn_facts", "DEMO-CB", "PROD-CB")


def test_master_population_is_tenant_isolated(db):
    """The broadener (``populate_universe``) is a new WRITE surface — it stays tenant-scoped. Populating the
    same SEC rows under two tenants writes each its OWN row (distinct ids), and a populate under one tenant
    neither creates nor mutates the other's. Grows the poison-row proof to the master-population path.
    """
    provision_tenant(db, "prod-master", tenant_id=PROD_TENANT_ID)
    rows = [("0001822966", "SMR", "NuScale Power")]

    master.populate_universe(db, rows, tenant_id=DEFAULT_TENANT_ID)
    master.populate_universe(db, rows, tenant_id=PROD_TENANT_ID)
    db.commit()

    demo = master.ids_for_tickers(db, ["SMR"], tenant_id=DEFAULT_TENANT_ID)
    prod = master.ids_for_tickers(db, ["SMR"], tenant_id=PROD_TENANT_ID)
    assert set(demo) == {"SMR"} and set(prod) == {"SMR"}
    assert demo["SMR"] != prod["SMR"]  # each tenant owns its OWN row for the same name

    # a re-populate under demo leaves prod's row untouched (not mutated, not duplicated)
    prod_id = prod["SMR"]
    master.populate_universe(db, rows, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM security_master WHERE tenant_id = %s AND ticker = 'SMR'",
            (PROD_TENANT_ID,),
        )
        assert [r["id"] for r in cur.fetchall()] == [prod_id]


def test_workbench_scored_read_is_tenant_isolated(db):
    """The Workbench scored READ (the scorer over the pit) inherits the tenant filter: a prod member scores
    off PROD's facts, a demo member off DEMO's — same-ticker securities never cross, and scoring a prod
    security under the demo tenant sees no prod fact ("—"). The scored endpoint surface, proven isolated.
    """
    provision_tenant(db, "prod-scored", tenant_id=PROD_TENANT_ID)
    demo_sec = _security(db, DEFAULT_TENANT_ID)
    prod_sec = _security(db, PROD_TENANT_ID)
    ingest_revenue_mix(
        db,
        demo_sec,
        segment_label="x",
        mix_pct=50,
        source="10-k-segment",
        source_ref="DEMO",
        event_date=date(2026, 1, 1),
    )
    ingest_revenue_mix(
        db,
        prod_sec,
        segment_label="x",
        mix_pct=100,
        source="10-k-business-description",
        source_ref="PROD",
        event_date=date(2026, 1, 1),
        tenant_id=PROD_TENANT_ID,
    )
    asof = date(2026, 6, 1)
    demo_member = BasketMember(
        ticker="HIMS", role="r", archetype=Archetype.HIGH_BETA, security_id=demo_sec
    )
    prod_member = BasketMember(
        ticker="HIMS", role="r", archetype=Archetype.HIGH_BETA, security_id=prod_sec
    )
    demo_scored = score_member(
        PointInTimeData(db, asof=asof, known_at=_KNOWN, tenant_id=DEFAULT_TENANT_ID), demo_member
    )
    prod_scored = score_member(
        PointInTimeData(db, asof=asof, known_at=_KNOWN, tenant_id=PROD_TENANT_ID), prod_member
    )
    assert demo_scored.purity.value == 50.0 and demo_scored.purity.provenance[0].ref == "DEMO"
    assert prod_scored.purity.value == 100.0 and prod_scored.purity.provenance[0].ref == "PROD"
    # cross: scoring prod's security under the DEMO tenant reads no prod fact -> "—"
    cross = score_member(
        PointInTimeData(db, asof=asof, known_at=_KNOWN, tenant_id=DEFAULT_TENANT_ID), prod_member
    )
    assert cross.purity.pips is None


def test_securities_search_is_tenant_isolated(db):
    """The Workbench add-a-name search (Slice 4b) is a new tenant-scoped read surface: a same-ticker
    security in demo + prod each surfaces ONLY under its own tenant — a demo search returns demo's row and
    never prod's, and vice versa. Grows the poison-row proof (discipline-not-RLS holds only if each new
    read path stays on the tenant filter)."""
    provision_tenant(db, "prod-search", tenant_id=PROD_TENANT_ID)
    demo_sec = _security(db, DEFAULT_TENANT_ID, ticker="OKLO")
    prod_sec = _security(db, PROD_TENANT_ID, ticker="OKLO")

    assert [s.id for s in master.search(db, "OKLO", tenant_id=DEFAULT_TENANT_ID)] == [demo_sec]
    assert [s.id for s in master.search(db, "OKLO", tenant_id=PROD_TENANT_ID)] == [prod_sec]


def test_chain_resolution_is_tenant_isolated(db):
    """The narrative→chain resolver (Slice 5b's draft endpoint calls it, keyed on the THESIS's tenant) is
    tenant-scoped: a name resolves against ONLY the given tenant's master. A ticker present in demo but not
    prod PLACES under demo and is ABSENT under prod — resolve_placements threads the tenant through master.*.
    Grows the poison-row proof to the draft read surface (discipline-not-RLS holds only if each new read path
    stays on the tenant filter)."""
    provision_tenant(db, "prod-draft", tenant_id=PROD_TENANT_ID)
    _security(db, DEFAULT_TENANT_ID, ticker="OKLO")  # OKLO exists ONLY in demo
    seg = [
        ProposedSegment(
            label="reactors", placements=[ProposedPlacement(name="Oklo", ticker="OKLO")]
        )
    ]
    demo = resolve_placements(db, seg, tenant_id=DEFAULT_TENANT_ID)
    prod = resolve_placements(db, seg, tenant_id=PROD_TENANT_ID)
    assert demo.placements[0].status is PlacementStatus.PLACED  # demo owns OKLO
    assert prod.placements[0].status is PlacementStatus.ABSENT  # prod has no OKLO -> not leaked
