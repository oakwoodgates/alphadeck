"""V2 W6b: the COUNTING ROUTE, ``PUT /theses/{thesis_id}/catalysts/{entry_id}/counting``.

It switches a calendar row's counting entry on (grade, members, citation, optional relevant-until), edits it, and
switches it off, through the sole writer (``repositories/thesis_catalyst_repo.py``). Nothing reads the store into a
call yet (W6c), so no call changes. The entry time is the database's: the request model forbids unknown keys, so a
body that carries a time (``entered_at``, ``recorded_at``, ``valid_from``, ``when_date``) is a 422 and nothing is
written. ``GET /theses/{id}`` returns each row's current counting state in ``catalyst_counting`` (additive).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from domain.market_time import edgar_night
from repositories import thesis_catalyst_repo
from tests.thesis_catalyst_fixtures import card


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_thesis_catalyst")
        return cur.fetchone()["n"]


def _tonight(db):
    with db.cursor() as cur:
        cur.execute("SELECT now() AS t")
        return edgar_night(cur.fetchone()["t"])


def _url(tid, entry) -> str:
    return f"/theses/{tid}/catalysts/{entry}/counting"


ON = {"counts": True, "grade": "core", "citation": "https://example.com/doe-award"}


def test_switch_on_writes_one_version_from_tonight_not_the_typed_date(client, db, pinned_today):
    tid, sids, entry = card(db)  # the calendar row is typed 2026-03-01, months back
    db.commit()
    r = client.put(_url(tid, entry), json={**ON, "security_ids": [str(sids[0])]})
    assert r.status_code == 200, r.text
    assert _count(db) == 1
    (v,) = thesis_catalyst_repo.current(db, tid)
    assert v["valid_from"] == _tonight(db)
    assert v["entered_at"] == v["recorded_at"]
    assert v["label"] == "DOE loan"  # the calendar row's label; never taken from the request
    assert v["entered_by"] is not None  # the signed-in user
    (state,) = r.json()["catalyst_counting"]
    assert state["entry_id"] == str(entry) and state["counts"] is True
    assert state["grade"] == "core" and state["citation"] == "https://example.com/doe-award"
    assert state["members"] == [{"security_id": str(sids[0]), "ticker": "W6A0"}]
    assert state["counts_from"] == _tonight(db).isoformat()
    # the plain read carries it too, and the existing fields are unchanged
    detail = client.get(f"/theses/{tid}").json()
    assert detail["catalyst_counting"] == r.json()["catalyst_counting"]
    assert [c["id"] for c in detail["catalysts"]] == [str(entry)]


@pytest.mark.parametrize("field", ["entered_at", "recorded_at", "valid_from", "when_date"])
def test_a_time_in_the_body_is_refused_and_writes_nothing(client, db, pinned_today, field):
    tid, sids, entry = card(db)
    db.commit()
    body = {**ON, "security_ids": [str(sids[0])], field: "2026-01-02"}
    r = client.put(_url(tid, entry), json=body)
    assert r.status_code == 422, r.text
    assert _count(db) == 0


def test_a_time_in_the_body_cannot_move_an_entry_that_counts(client, db, pinned_today):
    tid, sids, entry = card(db)
    db.commit()
    assert (
        client.put(_url(tid, entry), json={**ON, "security_ids": [str(sids[0])]}).status_code == 200
    )
    (before,) = thesis_catalyst_repo.current(db, tid)
    r = client.put(
        _url(tid, entry),
        json={**ON, "security_ids": [str(sids[0])], "valid_from": "2026-01-02"},
    )
    assert r.status_code == 422
    assert _count(db) == 1
    assert thesis_catalyst_repo.current(db, tid)[0]["valid_from"] == before["valid_from"]


@pytest.mark.parametrize(
    ("body", "sentence"),
    [
        ({"counts": True, "citation": "https://example.com/x"}, "Pick a grade."),
        (
            {"counts": True, "grade": "flip"},
            "Add a citation: a link (https://…) or an SEC accession number.",
        ),
        (
            {"counts": True, "grade": "flip", "citation": "10-K p.43"},
            "Add a citation: a link (https://…) or an SEC accession number.",
        ),
        (
            {"counts": True, "grade": "flip", "citation": "javascript:alert(1)"},
            "Add a citation: a link (https://…) or an SEC accession number.",
        ),
        (
            {"counts": True, "grade": "core", "citation": "0001193125-26-234847"},
            "A whole-card entry can only be Flip.",
        ),
    ],
)
def test_validation_refusals(client, db, pinned_today, body, sentence):
    tid, _sids, entry = card(db)
    db.commit()
    r = client.put(_url(tid, entry), json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == sentence
    assert _count(db) == 0


def test_a_member_not_on_this_card_is_refused(client, db, pinned_today):
    tid, _sids, entry = card(db)
    _other, other_sids, _e = card(db)
    db.commit()
    r = client.put(_url(tid, entry), json={**ON, "security_ids": [str(other_sids[0])]})
    assert r.status_code == 422
    assert r.json()["detail"] == "That company is not on this card."
    assert _count(db) == 0


def test_relevant_until_on_or_before_tonight_is_refused(client, db, pinned_today):
    tid, sids, entry = card(db)
    db.commit()
    tonight = _tonight(db)
    for day in (tonight, tonight - timedelta(days=3)):
        r = client.put(
            _url(tid, entry),
            json={**ON, "security_ids": [str(sids[0])], "relevant_until": day.isoformat()},
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"].startswith("“Relevant until” must be after")
    assert _count(db) == 0
    later = tonight + timedelta(days=30)
    r = client.put(
        _url(tid, entry),
        json={**ON, "security_ids": [str(sids[0])], "relevant_until": later.isoformat()},
    )
    assert r.status_code == 200, r.text
    assert r.json()["catalyst_counting"][0]["relevant_until"] == later.isoformat()


def test_an_accession_number_is_a_citation_and_a_whole_card_flip_counts(client, db, pinned_today):
    tid, _sids, entry = card(db)
    db.commit()
    r = client.put(
        _url(tid, entry),
        json={"counts": True, "grade": "flip", "citation": "0001193125-26-234847"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["catalyst_counting"][0]["members"] == []


def test_unknown_entry_and_unknown_thesis_are_404(client, db, pinned_today):
    tid, sids, _entry = card(db)
    db.commit()
    r = client.put(_url(tid, uuid.uuid4()), json={**ON, "security_ids": [str(sids[0])]})
    assert r.status_code == 404 and r.json()["detail"] == "catalyst entry not found"
    r = client.put(_url(uuid.uuid4(), uuid.uuid4()), json=ON)
    assert r.status_code == 404 and r.json()["detail"] == "thesis not found"
    # a row of ANOTHER card is not an entry of this one
    _t2, _s2, e2 = card(db)
    db.commit()
    r = client.put(_url(tid, e2), json={**ON, "security_ids": [str(sids[0])]})
    assert r.status_code == 404
    assert _count(db) == 0


def test_a_no_op_writes_nothing_and_edits_and_switch_off_write_one_each(client, db, pinned_today):
    tid, sids, entry = card(db)
    db.commit()
    body = {**ON, "security_ids": [str(sids[0])]}
    assert client.put(_url(tid, entry), json=body).status_code == 200
    assert client.put(_url(tid, entry), json=body).status_code == 200
    assert _count(db) == 1  # store-on-diff
    edited = {**body, "security_ids": [str(s) for s in sids]}
    r = client.put(_url(tid, entry), json=edited)
    assert r.status_code == 200 and _count(db) == 2
    assert len(r.json()["catalyst_counting"][0]["members"]) == 2
    # switch off: one version; the fields stay so the form re-opens filled
    r = client.put(_url(tid, entry), json={"counts": False})
    assert r.status_code == 200 and _count(db) == 3
    (state,) = r.json()["catalyst_counting"]
    assert state["counts"] is False and state["grade"] == "core"
    # switching off what does not count writes nothing
    assert client.put(_url(tid, entry), json={"counts": False}).status_code == 200
    assert _count(db) == 3


def test_the_full_loop_on_then_off_then_the_row_is_an_ordinary_item_again(client, db, pinned_today):
    tid, sids, entry = card(db)
    db.commit()
    body = {**ON, "security_ids": [str(sids[0])]}
    assert client.put(_url(tid, entry), json=body).status_code == 200
    assert client.put(f"/theses/{tid}/catalysts", json=[]).status_code == 409
    assert client.put(_url(tid, entry), json={"counts": False}).status_code == 200
    assert thesis_catalyst_repo.counting_entry_ids(db, tid) == set()
    r = client.put(f"/theses/{tid}/catalysts", json=[])
    assert r.status_code == 200, r.text
    assert r.json()["catalysts"] == [] and r.json()["catalyst_counting"] == []
    assert _count(db) == 2  # history is never deleted


def test_a_meta_index_card_is_not_refused(client, db, pinned_today):
    """The route records the operator's statement on a meta index card; the call never listens there (W6c)."""
    tid, sids, entry = card(db)
    db.commit()
    assert client.put(f"/theses/{tid}/meta-index", json={"meta_index": True}).status_code == 200
    r = client.put(_url(tid, entry), json={**ON, "security_ids": [str(sids[0])]})
    assert r.status_code == 200, r.text
    assert _count(db) == 1


def test_a_calendar_row_with_no_entry_reads_no_counting_state(client, db, pinned_today):
    tid, _sids, _entry = card(db)
    db.commit()
    assert client.get(f"/theses/{tid}").json()["catalyst_counting"] == []
