"""The call-annotation repo (D3b) + the calls-log read policy it feeds — COUNT THE TABLE, not the read.

Two things are pinned here. (1) The repo's own contract: an annotation copies its call's tenant / thesis /
as-of, a revoke is an appended row that ends the target's activity, and the reads return ACTIVE rows only.
(2) The ``exclude_annotated`` read policy on ``calls_repo``: a row with an ACTIVE ``exclude`` is dropped
BEFORE the per-as-of dedup (the ``include_reconstructed`` precedent, row for row), the three peer reads pick
the same winning row under it, the default (``False``) is byte-identical to today, and a revoke restores
the row. Cards are real assembler output (``assemble_call``) on a persisted thesis. DB-backed.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Verdict
from repositories import call_annotations_repo, calls_repo, thesis_repo
from tests.calls.factories import make_thesis

_RULE = "d3a-first-knowability@abc1234"


def _thesis(db):
    thesis = make_thesis(basket=[])  # basket=[] avoids the security_master FK
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis


def _row_ids(db, thesis_id) -> list[uuid.UUID]:
    with db.cursor() as cur:
        cur.execute("SELECT id FROM calls WHERE thesis_id = %s ORDER BY seq", (thesis_id,))
        return [r["id"] for r in cur.fetchall()]


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test-local literal
        return cur.fetchone()["n"]


def test_append_copies_the_call_identity_and_refuses_bad_input(db):
    thesis = _thesis(db)
    calls_repo.append(db, assemble_call(thesis, [], date(2026, 10, 14), DEFAULT_CONFIG))
    db.commit()
    (cid,) = _row_ids(db, thesis.id)

    aid = call_annotations_repo.append_annotation(
        db, cid, action="exclude", reason="stale_comparative_revenue", rule=_RULE, detail={"k": 1}
    )
    db.commit()
    (a,) = call_annotations_repo.active_for_call(db, cid)
    assert (a.id, a.thesis_id, a.asof, a.tenant_id) == (
        aid,
        thesis.id,
        date(2026, 10, 14),
        DEFAULT_TENANT_ID,
    )
    assert (a.action, a.reason, a.rule, a.detail, a.revokes) == (
        "exclude",
        "stale_comparative_revenue",
        _RULE,
        {"k": 1},
        None,
    )

    with pytest.raises(ValueError, match="revoke"):
        call_annotations_repo.append_annotation(db, cid, action="revoke", reason="r", rule=_RULE)
    with pytest.raises(ValueError, match="reason"):
        call_annotations_repo.append_annotation(db, cid, action="note", reason=" ", rule=_RULE)
    with pytest.raises(LookupError):
        call_annotations_repo.append_annotation(
            db, uuid.uuid4(), action="note", reason="r", rule=_RULE
        )
    db.rollback()
    assert _count(db, "call_annotation") == 1


def test_revoke_ends_activity_once_and_the_family_reads_see_both_states(db):
    thesis = _thesis(db)
    calls_repo.append(db, assemble_call(thesis, [], date(2026, 10, 14), DEFAULT_CONFIG))
    db.commit()
    (cid,) = _row_ids(db, thesis.id)
    aid = call_annotations_repo.append_annotation(db, cid, action="exclude", reason="r", rule=_RULE)
    db.commit()

    call_annotations_repo.revoke(db, aid, reason="operator: score it", rule="operator-revoke@x")
    db.commit()
    assert call_annotations_repo.active_for_call(db, cid) == []
    assert call_annotations_repo.active_for_thesis(db, thesis.id) == []
    assert call_annotations_repo.active_for_rule_family(db, "d3a-first-knowability") == []
    # the idempotency read still sees the (revoked) judgment, so a re-run never re-judges it
    fam = call_annotations_repo.all_for_rule_family(db, "d3a-first-knowability")
    assert [(a.id, a.action) for a in fam] == [(aid, "exclude")]
    with pytest.raises(LookupError):  # already revoked: nothing active to revoke
        call_annotations_repo.revoke(db, aid, reason="again", rule="operator-revoke@x")
    db.rollback()
    assert _count(db, "call_annotation") == 2  # the exclude + its one revoke


def test_rule_family_strips_the_code_version():
    assert (
        call_annotations_repo.rule_family("d3a-first-knowability@5f3146d")
        == "d3a-first-knowability"
    )
    assert call_annotations_repo.rule_family("d3a-first-knowability") == "d3a-first-knowability"


def test_exclude_annotated_filters_BEFORE_the_dedup_and_all_three_peer_reads_agree(db):
    """A night with an on-time row and a later re-record: excluding the LATER row lets the earlier one win
    (never a silent gap); a night whose only row is excluded reads like a cron gap; the default read is
    unchanged; and a revoke restores the row — counting the table throughout (nothing is ever deleted).
    """
    thesis = _thesis(db)
    d1, d2, d3 = date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)
    early = assemble_call(thesis, [], d1, DEFAULT_CONFIG)
    late = early.model_copy(
        update={"verdict": Verdict.NOT_YET}
    )  # a later re-record of the same night
    calls_repo.append(db, early, ingest_fresh=True, config_hash="h-early", run_kind="cron")
    calls_repo.append(db, late, ingest_fresh=False, config_hash="h-late", run_kind="manual")
    calls_repo.append(db, assemble_call(thesis, [], d2, DEFAULT_CONFIG), run_kind="cron")
    calls_repo.append(db, assemble_call(thesis, [], d3, DEFAULT_CONFIG), run_kind="cron")
    db.commit()
    rows = _row_ids(db, thesis.id)
    late_id, d2_id = rows[1], rows[2]
    for cid in (late_id, d2_id):
        call_annotations_repo.append_annotation(db, cid, action="exclude", reason="r", rule=_RULE)
    note_id = call_annotations_repo.append_annotation(
        db, rows[3], action="note", reason="r", rule=_RULE
    )
    db.commit()
    before_calls, before_ann = _count(db, "calls"), _count(db, "call_annotation")

    # the default read: byte-identical to today (the late row wins d1, every night present)
    every = {c.asof: c for c in calls_repo.latest_for_thesis(db, thesis.id)}
    assert set(every) == {d1, d2, d3} and every[d1].verdict is Verdict.NOT_YET

    kw = {"include_reconstructed": False, "exclude_annotated": True}
    honest = {c.asof: c for c in calls_repo.latest_for_thesis(db, thesis.id, **kw)}
    assert set(honest) == {d1, d3}  # d2 had only an excluded row -> a gap, like a cron gap
    assert (
        honest[d1].verdict is early.verdict
    )  # the excluded LATER row never shadows the earlier one
    # the peer reads describe the SAME winning row per night
    assert calls_repo.ingest_health_for_thesis(db, thesis.id, **kw) == {
        d1: (True, None),
        d3: (None, None),
    }
    ident = calls_repo.run_identity_for_thesis(db, thesis.id, **kw)
    assert (ident[d1].config_hash, ident[d1].run_kind) == ("h-early", "cron")
    # a note never filters; the caption read finds it on its (winning) row
    assert [a.id for a in calls_repo.annotations_for_thesis(db, thesis.id, **kw)[d3]] == [note_id]
    # under the INCLUDE reading the excluded rows' annotations ride the caption read instead
    incl = calls_repo.annotations_for_thesis(db, thesis.id, include_reconstructed=False)
    assert {d: [a.action for a in v] for d, v in incl.items()} == {
        d1: ["exclude"],
        d2: ["exclude"],
        d3: ["note"],
    }
    assert [
        (n.asof, n.thesis_id, n.n_rows)
        for n in call_annotations_repo.active_nights(db, action="exclude", upto=d3)
    ] == [(d1, thesis.id, 1), (d2, thesis.id, 1)]
    assert (
        call_annotations_repo.active_nights(db, action="exclude", upto=d1)[0].asof == d1
    )  # asof-capped

    # revoke the d2 exclusion: the night comes back, exactly as it reads with the policy off
    (d2_ann,) = call_annotations_repo.active_for_call(db, d2_id)
    call_annotations_repo.revoke(db, d2_ann.id, reason="operator", rule="operator-revoke@x")
    db.commit()
    assert {c.asof for c in calls_repo.latest_for_thesis(db, thesis.id, **kw)} == {d1, d2, d3}
    # COUNT THE TABLE: no calls row was touched; the annotation log only grew (by the one revoke)
    assert _count(db, "calls") == before_calls
    assert _count(db, "call_annotation") == before_ann + 1
