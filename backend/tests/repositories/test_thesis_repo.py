from __future__ import annotations

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from db.session import DEFAULT_TENANT_ID, connect
from domain.enums import Archetype, Authorship, TermTier
from domain.thesis import (
    BasketMember,
    Catalyst,
    Evidence,
    KillCriterion,
    Position,
    Segment,
    TermSetEntry,
    Thesis,
)
from repositories import thesis_repo


def _thesis(security_id) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="HIMS — insider conviction",
        narrative="A director bought ~$1.2M open-market off the lows; watching for confirmation.",
        ticker="HIMS",
        segments=[
            Segment(label="Telehealth platforms", descriptor="catalyst-rich"),
            Segment(label="Compounding / supply"),
        ],
        basket=[
            BasketMember(
                ticker="HIMS",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
                detail="mkt ~$6B",
                segment="Telehealth platforms",
                thesis_fit="the leading US telehealth platform",
                authored_by=Authorship.OPERATOR_SET,
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


def test_chain_structure_survives_reload(db, security_id):
    """The HARD Workbench-MVP requirement: the value-chain STRUCTURE — the segment list, which name sits
    in which link, and authorship — survives a reload. Proven by reading it back on a SEPARATE connection
    (durable in the store, not just the writer's session view). If this doesn't hold, the MVP isn't done.
    """
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()

    reloaded = connect()  # a fresh connection — the chain must be durable, not session-local
    try:
        got = thesis_repo.get(reloaded, t.id)
    finally:
        reloaded.close()

    assert got is not None
    assert [(s.label, s.descriptor) for s in got.segments] == [
        ("Telehealth platforms", "catalyst-rich"),
        ("Compounding / supply", None),
    ]
    assert got.basket[0].segment == "Telehealth platforms"
    assert (
        got.basket[0].thesis_fit == "the leading US telehealth platform"
    )  # the prose is durable too
    assert got.basket[0].authored_by is Authorship.OPERATOR_SET
    assert got == t  # full structural round-trip across the reconnect


def test_thesis_rejects_member_in_unknown_segment():
    """A name cannot sit in a link that isn't in the chain — the segment-consistency validator (no DB)."""
    with pytest.raises(ValidationError):
        Thesis(
            id=uuid.uuid4(),
            name="x",
            narrative="x",
            segments=[Segment(label="Reactor developers")],
            basket=[
                BasketMember(
                    ticker="ZZZ",
                    role="r",
                    archetype=Archetype.ADJACENT,
                    segment="Fuel & enrichment",  # not among the thesis's segments
                )
            ],
        )


def test_get_missing_returns_none(db):
    assert thesis_repo.get(db, uuid.uuid4()) is None


# --- the persisted, tiered term set (discovery precision; written ONLY by set_term_set) ---


def test_set_term_set_roundtrips(db, security_id):
    """``set_term_set`` is the sole writer of the tiered term set; term + tier + authored_by default + source
    all round-trip through ``get``."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    terms = [
        TermSetEntry(term="psilocybin", tier=TermTier.SIGNAL, source="keyword_gen"),
        TermSetEntry(term="MDMA", tier=TermTier.BROAD, source="keyword_gen"),
    ]
    thesis_repo.set_term_set(db, t.id, terms)
    db.commit()
    assert thesis_repo.get(db, t.id).term_set == terms  # full structural round-trip


def test_set_term_set_touches_only_the_term_set(db, security_id):
    """The NARROW writer touches ONLY ``term_set`` — the chain (segments + basket) is untouched."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    thesis_repo.set_term_set(db, t.id, [TermSetEntry(term="ibogaine", tier=TermTier.SIGNAL)])
    db.commit()
    got = thesis_repo.get(db, t.id)
    assert [s.label for s in got.segments] == ["Telehealth platforms", "Compounding / supply"]
    assert len(got.basket) == 1 and got.basket[0].ticker == "HIMS"
    assert [e.term for e in got.term_set] == ["ibogaine"]


def test_upsert_cannot_blank_a_persisted_term_set(db, security_id):
    """THE STRUCTURAL WIPE-GUARD (the seam where this design would fail SILENTLY): once a term set is produced,
    a later ``upsert`` of the SAME thesis whose object carries an empty ``term_set`` — exactly what ``promote``
    builds from a request that omits it — must NOT blank the stored set. ``upsert`` never names the column, so
    it can't. (A wiped term set is indistinguishable from a never-produced one — that's why this is load-bearing.)
    """
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    thesis_repo.set_term_set(db, t.id, [TermSetEntry(term="psilocin", tier=TermTier.SIGNAL)])
    db.commit()
    # re-upsert the thesis object (its term_set defaults to [], as a promote would) + a narrative edit
    thesis_repo.upsert(db, t.model_copy(update={"narrative": "edited"}))
    db.commit()
    got = thesis_repo.get(db, t.id)
    assert got.narrative == "edited"  # upsert DID write the fields it owns
    assert [e.term for e in got.term_set] == ["psilocin"]  # the term set SURVIVED unblanked


def test_upsert_updates_mutable_fields_and_is_idempotent(db, security_id):
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()

    # mutate the narrative + log a fill + RE-SEGMENT the chain (re-label the link, re-place the name,
    # mark it operator-edited), then re-upsert the same thesis id
    t2 = t.model_copy(
        update={
            "narrative": "Position opened; managing to exit-by.",
            "position": Position(entry_price=24.0, opened_on=date(2026, 6, 2)),
            "segments": [Segment(label="Reactor developers", descriptor="re-segmented")],
            "basket": [
                t.basket[0].model_copy(
                    update={
                        "segment": "Reactor developers",
                        "authored_by": Authorship.OPERATOR_EDITED,
                    }
                )
            ],
        }
    )
    thesis_repo.upsert(db, t2)
    db.commit()

    got = thesis_repo.get(db, t.id)
    assert got.narrative == "Position opened; managing to exit-by."
    assert got.position is not None and got.position.opened_on == date(2026, 6, 2)
    assert got.position.entry_price == 24.0
    # the edited chain round-trips: re-labeled segment, re-placed name, edited authorship
    assert [s.label for s in got.segments] == ["Reactor developers"]
    assert got.basket[0].segment == "Reactor developers"
    assert got.basket[0].authored_by is Authorship.OPERATOR_EDITED
    # idempotent: children edited in place by the DELETE-reinsert, never duplicated
    assert len(got.basket) == 1
    assert len(got.evidence) == 1
    assert len(got.catalysts) == 1
    assert len(got.kill_criteria) == 1


# --- TRIAGE: the per-member conviction/size weight ---


def test_conviction_roundtrips(db, security_id):
    """The operator's per-name weight (1–5) persists through the full-replace promote."""
    t = _thesis(security_id)
    t.basket[0].conviction = 4
    thesis_repo.upsert(db, t)
    db.commit()
    assert thesis_repo.get(db, t.id).basket[0].conviction == 4


def test_unset_conviction_stays_none_never_zero(db, security_id):
    """Honest confidence (#6): an unweighted name reads back NULL ("operator hasn't said"), never coerced to 0
    — so future size-weighted attribution can't silently treat unset as zero-weight."""
    t = _thesis(security_id)
    assert t.basket[0].conviction is None  # the default is unset
    thesis_repo.upsert(db, t)
    db.commit()
    got = thesis_repo.get(db, t.id)
    assert got.basket[0].conviction is None  # NOT 0


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_conviction_out_of_range_rejected(bad):
    """The 1–5 scale is validated at the model (no DB): 0 is not "unset" — unset is NULL."""
    with pytest.raises(ValidationError):
        BasketMember(ticker="X", role="r", archetype=Archetype.HIGH_BETA, conviction=bad)


def test_conviction_survives_a_resave_through_the_mapper(db, security_id):
    """THE WIPE-TRAP guard: every promote reads the basket THROUGH _row_to_basket_member before DELETE+reinsert,
    so an UNMAPPED field is silently wiped on any unrelated resave (a narrative edit, the archetype-apply). Set
    conviction, then resave the READ-BACK thesis with only the narrative changed — conviction must survive.
    """
    t = _thesis(security_id)
    t.basket[0].conviction = 3
    thesis_repo.upsert(db, t)
    db.commit()

    got = thesis_repo.get(db, t.id)  # read back THROUGH the mapper
    assert got.basket[0].conviction == 3
    # an unrelated edit that resends the read-back basket verbatim (mimics the narrative-edit / archetype-apply)
    got.narrative = "edited narrative — the basket is resent verbatim"
    thesis_repo.upsert(db, got)
    db.commit()

    assert thesis_repo.get(db, t.id).basket[0].conviction == 3  # not wiped — the mapper carried it
