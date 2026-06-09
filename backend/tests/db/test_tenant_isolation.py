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
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from pipeline.call_for_thesis import call_for_thesis
from pipeline.provision_tenant import provision_tenant
from repositories import thesis_repo
from securities import master

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
