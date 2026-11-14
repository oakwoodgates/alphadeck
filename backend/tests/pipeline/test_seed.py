from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from domain.enums import State, Verdict
from domain.scopes import WORKSPACE_ANY
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import HIMS_THESIS_ID, seed_hims
from repositories import thesis_repo

_KNOWN = datetime(2100, 1, 1, tzinfo=timezone.utc)


def test_seed_hims_produces_a_curlable_armed_call(db):
    tid = seed_hims(db)
    db.commit()
    assert tid == HIMS_THESIS_ID
    # the seeded thesis computes the real Armed call (what the operator will curl at Checkpoint A)
    card = call_for_thesis(db, tid, date(2026, 6, 1), known_at=_KNOWN, record=False)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.STARTER_ENTRY
    assert card.armed_security_id is not None


def test_seed_is_idempotent(db):
    seed_hims(db)
    db.commit()
    seed_hims(db)  # second run must not error or duplicate the thesis
    db.commit()
    thesis = thesis_repo.get(db, HIMS_THESIS_ID, workspace=WORKSPACE_ANY)
    assert thesis is not None
    assert len(thesis.basket) == 1
    assert len(thesis.evidence) == 1


def test_the_boot_seed_writes_platform_facts_to_the_data_tenant(db, monkeypatch):
    """V2: with the demo cards in a workspace that is not the data tenant, the seeded share counts, cash/burn and
    company catalysts are platform data (the data tenant), while purity and the cards stay in the workspace.
    """
    from domain.scopes import DATA_TENANT_ID
    from pipeline import seed as seed_mod
    from tests.workspaces import new_workspace

    w = new_workspace(
        db, "seed workspace", workspace_id=UUID("00000000-0000-0000-0000-0000000000c1")
    )
    db.commit()
    monkeypatch.setenv(seed_mod.SEED_ON_BOOT_ENV, "1")
    seed_mod.main(workspace=w)
    db.rollback()  # read what main committed on its own connection
    with db.cursor() as cur:
        for table, want in (
            ("fact_shares_outstanding", {DATA_TENANT_ID}),
            ("fact_cash_burn", {DATA_TENANT_ID}),
            ("fact_catalyst", {DATA_TENANT_ID}),
            ("fact_revenue_mix", {w}),
            ("thesis", {w}),
        ):
            cur.execute(f"SELECT DISTINCT tenant_id FROM {table}")  # noqa: S608 — a fixed list
            assert {r["tenant_id"] for r in cur.fetchall()} == want, table
