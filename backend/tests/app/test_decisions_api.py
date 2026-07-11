"""Decision capture (gate-1 ratified 2026-07-10) — the operator-decisions log + the position feed.

One log, three rooms: the Scoreboard's operator column (capture starts NOW — it can never be
backfilled), the derived position that makes the Managing state REACHABLE, and the gate's override
record. These tests pin the trust properties: the take → Managing → close loop on the live call
read; NO LOOKAHEAD on both time axes (a fill is invisible to a past ``asof``, and a later-RECORDED
fill is invisible to a pinned ``known_at`` — the replay discipline); append-only reversibility
(void restores the prior state by APPENDING — the table only ever grows); and the precedence rule
(any log rows beat the seed-era ``thesis.position_*`` columns, including "net closed", so a stale
stored position can never resurrect after a logged close).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from db.session import DEFAULT_TENANT_ID
from domain.thesis import Position, Thesis
from pipeline.call_for_thesis import call_for_thesis
from repositories import decisions_repo, thesis_repo

TODAY = date.today()


def _thesis(db, position: Position | None = None) -> Thesis:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="decision capture",
        narrative="x",
        segments=[],
        basket=[],
        position=position,
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t


def _call_state(client, tid, asof: date = TODAY) -> str:
    return client.get(f"/theses/{tid}/call", params={"asof": str(asof)}).json()["state"]


def _post(client, tid, **body):
    body.setdefault("decision_date", str(TODAY))
    return client.post(f"/theses/{tid}/decisions", json=body)


# --- the loop: take -> Managing -> close -> reverts -----------------------------------------------------


def test_take_flips_the_call_to_managing_and_close_reverts(client, db):
    t = _thesis(db)
    assert _call_state(client, t.id) == "incubating"  # bare thesis, no signals, no position

    r = _post(client, t.id, action="take", shares=10, price=100.0)
    assert r.status_code == 200
    assert r.json()["call_state"] is None  # no call-of-record existed when logged — honest None
    assert _call_state(client, t.id) == "managing"  # the position now derives from the log

    assert _post(client, t.id, action="close", price=120.0).status_code == 200
    assert _call_state(client, t.id) == "incubating"  # closed -> the loop, not a ratchet


def test_pass_never_touches_the_position(client, db):
    t = _thesis(db)
    r = _post(client, t.id, action="pass", reason="verdict is not-yet; agreed")
    assert r.status_code == 200
    assert _call_state(client, t.id) == "incubating"
    rows = client.get(f"/theses/{t.id}/decisions").json()
    assert [x["action"] for x in rows] == ["pass"]
    assert rows[0]["reason"] == "verdict is not-yet; agreed"


def test_take_snapshots_the_platform_stance(client, db):
    """The gate's record: the row carries the platform's state/verdict AT logging time (display
    denormalization; attribution re-derives from the calls-log join)."""
    t = _thesis(db)
    call_for_thesis(db, t.id, TODAY, record=True)  # a call-of-record exists first
    db.commit()
    r = _post(client, t.id, action="take", price=10.0)
    assert r.json()["call_state"] == "incubating"
    assert r.json()["call_verdict"] is not None


# --- no lookahead: both time axes ------------------------------------------------------------------------


def test_a_fill_is_invisible_to_a_past_asof(client, db):
    """Valid time: a take dated today must not make YESTERDAY's call managing."""
    t = _thesis(db)
    _post(client, t.id, action="take", price=10.0)
    assert _call_state(client, t.id, asof=TODAY - timedelta(days=1)) == "incubating"
    assert _call_state(client, t.id) == "managing"


def test_a_later_recorded_fill_is_invisible_to_a_pinned_known_at(db):
    """Transaction time: a replay with known_at BEFORE the append sees the log as it stood — no
    rows at all (any_rows False), so even the fallback path is exactly the pre-log world."""
    t = _thesis(db)
    decisions_repo.append(
        db,
        thesis_id=t.id,
        tenant_id=DEFAULT_TENANT_ID,
        action="take",
        decision_date=TODAY - timedelta(days=3),
        price=5.0,
    )
    db.commit()
    pos, any_rows = decisions_repo.derived_position(
        db, t.id, asof=TODAY, known_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    assert pos is None and any_rows is False
    pos, any_rows = decisions_repo.derived_position(db, t.id, asof=TODAY)
    assert any_rows is True and pos is not None
    assert pos.opened_on == TODAY - timedelta(days=3) and pos.entry_price == 5.0


def test_future_decision_date_is_rejected(client, db):
    t = _thesis(db)
    r = _post(client, t.id, action="take", decision_date=str(TODAY + timedelta(days=1)))
    assert r.status_code == 422
    assert "future" in r.json()["detail"]


# --- reversibility: void appends, restores, and stays visible --------------------------------------------


def test_void_restores_the_prior_state_and_the_table_only_grows(client, db):
    t = _thesis(db)
    take_id = _post(client, t.id, action="take", price=10.0).json()["id"]
    assert _call_state(client, t.id) == "managing"

    assert _post(client, t.id, action="void", voids=take_id).status_code == 200
    assert _call_state(client, t.id) == "incubating"  # the void un-does the take on read

    rows = client.get(f"/theses/{t.id}/decisions").json()
    assert len(rows) == 2  # COUNT the table: the correction APPENDED — nothing was deleted
    by_action = {x["action"]: x for x in rows}
    assert by_action["take"]["voided"] is True  # greyed, not hidden
    assert by_action["void"]["voided"] is False
    assert by_action["void"]["voids"] == take_id


def test_void_validations(client, db):
    t = _thesis(db)
    assert _post(client, t.id, action="void").status_code == 422  # no target
    assert _post(client, t.id, action="void", voids=str(uuid.uuid4())).status_code == 422  # unknown
    take_id = _post(client, t.id, action="take", price=1.0).json()["id"]
    void_id = _post(client, t.id, action="void", voids=take_id).json()["id"]
    assert _post(client, t.id, action="void", voids=take_id).status_code == 422  # already voided
    assert _post(client, t.id, action="void", voids=void_id).status_code == 422  # a void is final


# --- one open position per thesis (v1) --------------------------------------------------------------------


def test_take_requires_flat_and_close_requires_open(client, db):
    t = _thesis(db)
    assert _post(client, t.id, action="close").status_code == 422  # nothing to close
    assert _post(client, t.id, action="take", price=1.0).status_code == 200
    r = _post(client, t.id, action="take", price=2.0)
    assert r.status_code == 422 and "open position already exists" in r.json()["detail"]
    assert _post(client, t.id, action="close").status_code == 200
    assert _post(client, t.id, action="take", price=3.0).status_code == 200  # flat again -> allowed


# --- precedence: the log beats the seed-era stored columns ------------------------------------------------


def test_seed_position_yields_to_the_log_and_never_resurrects(client, db):
    """A thesis with a stored (seed-era) position reads Managing via the fallback — but one logged
    close makes the LOG authoritative: net-closed stays closed; the stale columns don't come back.
    """
    t = _thesis(db, position=Position(entry_price=10.0, opened_on=date(2026, 1, 2)))
    assert _call_state(client, t.id) == "managing"  # the fallback honors the seed columns

    assert _post(client, t.id, action="close").status_code == 200  # open (via fallback) -> closable
    assert _call_state(client, t.id) == "incubating"  # the log (net closed) now wins


# --- per-member attribution: the held NAME reads managing on the member menu (CALL_LOGIC §4) -------------


def test_take_on_a_name_attributes_managing_per_member(client, db, security_id):
    """A take logged ON a name threads its security_id through the derived position into the member
    menu: the held member's call leads armed_members with verdict=managing (confidence null), its
    ticker resolved through the master like any member."""
    t = _thesis(db)
    r = _post(client, t.id, action="take", price=10.0, security_id=str(security_id))
    assert r.status_code == 200
    card = client.get(f"/theses/{t.id}/call", params={"asof": str(TODAY)}).json()
    assert card["state"] == "managing"
    assert [m["security_id"] for m in card["armed_members"]] == [str(security_id)]
    held = card["armed_members"][0]
    assert held["verdict"] == "managing" and held["confidence"] is None
    assert held["ticker"] == "DEVCO"


def test_thesis_level_take_attributes_no_member(client, db):
    """A thesis-level take (no name on the row) flips the state but attributes nothing per-member —
    honest absence, never a guessed name (the seed-era stored columns behave the same way)."""
    t = _thesis(db)
    _post(client, t.id, action="take", price=10.0)
    card = client.get(f"/theses/{t.id}/call", params={"asof": str(TODAY)}).json()
    assert card["state"] == "managing"
    assert card["armed_members"] == []


def test_tenant_isolation_on_the_log(db):
    t = _thesis(db)
    decisions_repo.append(
        db, thesis_id=t.id, tenant_id=DEFAULT_TENANT_ID, action="pass", decision_date=TODAY
    )
    db.commit()
    assert decisions_repo.list_for_thesis(db, t.id, tenant_id=uuid.uuid4()) == []
    assert len(decisions_repo.list_for_thesis(db, t.id, tenant_id=DEFAULT_TENANT_ID)) == 1
