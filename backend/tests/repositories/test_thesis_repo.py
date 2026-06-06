from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Catalyst, Evidence, KillCriterion, Position, Thesis
from repositories import thesis_repo


def _thesis(security_id) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="HIMS — insider conviction",
        narrative="A director bought ~$1.2M open-market off the lows; watching for confirmation.",
        ticker="HIMS",
        basket=[
            BasketMember(
                ticker="HIMS",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
                detail="mkt ~$6B",
            )
        ],
        evidence=[
            Evidence(
                id=uuid.uuid4(),
                kind="FORM 4",
                label="Director bought $1.17M open-market",
                ref="0001773751-26-000086",
                date_label="1 wk",
            )
        ],
        catalysts=[
            Catalyst(
                id=uuid.uuid4(),
                label="Q2 earnings",
                kind="earnings",
                when_date=date(2026, 8, 4),
                when_label="~Q2",
            )
        ],
        kill_criteria=[
            KillCriterion(id=uuid.uuid4(), text="Closes back below the breakout base on volume")
        ],
    )


def test_upsert_then_get_roundtrips(db, security_id):
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    assert thesis_repo.get(db, t.id) == t  # full domain round-trip; no raw row escapes the repo


def test_get_missing_returns_none(db):
    assert thesis_repo.get(db, uuid.uuid4()) is None


def test_upsert_updates_mutable_fields_and_is_idempotent(db, security_id):
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()

    # mutate the narrative + log a fill, then re-upsert the same thesis id
    t2 = t.model_copy(
        update={
            "narrative": "Position opened; managing to exit-by.",
            "position": Position(entry_price=24.0, opened_on=date(2026, 6, 2)),
        }
    )
    thesis_repo.upsert(db, t2)
    db.commit()

    got = thesis_repo.get(db, t.id)
    assert got.narrative == "Position opened; managing to exit-by."
    assert got.position is not None and got.position.opened_on == date(2026, 6, 2)
    assert got.position.entry_price == 24.0
    # idempotent: children are not duplicated by a re-upsert
    assert len(got.basket) == 1
    assert len(got.evidence) == 1
    assert len(got.catalysts) == 1
    assert len(got.kill_criteria) == 1
