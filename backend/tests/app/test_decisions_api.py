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

# "Today" is the `pinned_today` fixture (tests/conftest.py): the PRODUCTION clock, not the ambient one,
# AND pinned for the test so the decision-date guard and the call read in `app/routers/theses.py`
# (both `market_today()` at request time) see the same day the test does. Two ways this module once
# disagreed with the code: an ambient `date.today()` (422s once a UTC runner passes ~20:00 ET), then a
# module-level `TODAY = market_today()` read at IMPORT, which went stale when a full run crossed
# midnight ET (2026-10-16/17: seven failures here).


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


def _call_state(client, tid, asof: date) -> str:
    return client.get(f"/theses/{tid}/call", params={"asof": str(asof)}).json()["state"]


def _post(client, tid, today: date, **body):
    body.setdefault("decision_date", str(today))
    return client.post(f"/theses/{tid}/decisions", json=body)


# --- the loop: take -> Managing -> close -> reverts -----------------------------------------------------


def test_take_flips_the_call_to_managing_and_close_reverts(client, db, pinned_today):
    t = _thesis(db)
    # bare thesis, no signals, no position
    assert _call_state(client, t.id, pinned_today) == "incubating"

    r = _post(client, t.id, pinned_today, action="take", shares=10, price=100.0)
    assert r.status_code == 200
    assert r.json()["call_state"] is None  # no call-of-record existed when logged — honest None
    # the position now derives from the log
    assert _call_state(client, t.id, pinned_today) == "managing"

    assert _post(client, t.id, pinned_today, action="close", price=120.0).status_code == 200
    # closed -> the loop, not a ratchet
    assert _call_state(client, t.id, pinned_today) == "incubating"


def test_pass_never_touches_the_position(client, db, pinned_today):
    t = _thesis(db)
    r = _post(client, t.id, pinned_today, action="pass", reason="verdict is not-yet; agreed")
    assert r.status_code == 200
    assert _call_state(client, t.id, pinned_today) == "incubating"
    rows = client.get(f"/theses/{t.id}/decisions").json()
    assert [x["action"] for x in rows] == ["pass"]
    assert rows[0]["reason"] == "verdict is not-yet; agreed"


def test_take_snapshots_the_platform_stance(client, db, pinned_today):
    """The gate's record: the row carries the platform's state/verdict AT logging time (display
    denormalization; attribution re-derives from the calls-log join)."""
    t = _thesis(db)
    call_for_thesis(db, t.id, pinned_today, record=True)  # a call-of-record exists first
    db.commit()
    r = _post(client, t.id, pinned_today, action="take", price=10.0)
    assert r.json()["call_state"] == "incubating"
    assert r.json()["call_verdict"] is not None


# --- no lookahead: both time axes ------------------------------------------------------------------------


# F1 (2026-10-30): a past as-of is one BEFORE the current night (the night the pass last recorded), so these pin the
# market clock to an exact instant — 09:00 ET on Wednesday 2026-10-28: the current night is Tuesday (the 22:30 pass
# has not run yet), today and the action day Wednesday. Pinned through ``market_now`` (``pinned_today``'s seam) so
# the route's ``market_today()`` / ``market_clock()`` and the test agree; transaction time stays real.
_WED_MORNING = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)  # 09:00 EDT
_MON, _TUE, _WED = date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)


def _pin_clock(monkeypatch, instant: datetime) -> None:
    from domain import market_time
    from domain.settings import get_settings

    monkeypatch.delenv("ALPHADECK_CRON_AT", raising=False)
    get_settings.cache_clear()  # the 22:30 pass time these instants are read against
    monkeypatch.setattr(
        market_time, "market_now", lambda tz=None: instant.astimezone(tz or market_time.market_tz())
    )


def test_a_fill_is_invisible_to_a_past_asof(client, db, monkeypatch):
    """Valid time: a take dated today must not make a PAST call managing (a day before the current night)."""
    _pin_clock(monkeypatch, _WED_MORNING)
    t = _thesis(db)
    _post(client, t.id, _WED, action="take", price=10.0)
    assert _call_state(client, t.id, asof=_MON) == "incubating"
    assert _call_state(client, t.id, _WED) == "managing"


def test_a_fill_logged_this_morning_shows_on_the_current_night_view(client, db, monkeypatch):
    """F1: the default view is last night's (Tuesday's), read live, and the operator's decisions read up to the
    action day — so the take logged this morning (dated Wednesday) makes the card Managing before tonight's pass,
    agreeing with the thesis-detail position. The record path is untouched: Tuesday's call of record, assembled
    the way the nightly does (no action-day read), still reads the take as not yet made."""
    _pin_clock(monkeypatch, _WED_MORNING)
    t = _thesis(db)
    assert _call_state(client, t.id, _TUE) == "incubating"
    assert _post(client, t.id, _WED, action="take", price=10.0).status_code == 200
    assert _call_state(client, t.id, _TUE) == "managing"
    assert client.get(f"/theses/{t.id}").json()["position"]["opened_on"] == str(_WED)
    # the record path: a call for Tuesday as the nightly assembles it never sees a Wednesday fill
    assert call_for_thesis(db, t.id, _TUE, record=False).state.value == "incubating"
    db.commit()
    # after tonight's pass the night is Wednesday, so Tuesday is a past view and the take is after it again
    _pin_clock(
        monkeypatch, datetime(2026, 10, 29, 2, 45, tzinfo=timezone.utc)
    )  # 22:45 EDT Wednesday
    assert _call_state(client, t.id, _TUE) == "incubating"
    assert _call_state(client, t.id, _WED) == "managing"


def test_a_close_logged_this_morning_leaves_managing_on_the_current_night_view(
    client, db, monkeypatch
):
    """The other half of the same read: a close dated today ends the position on the current-night view at once,
    while Tuesday's call as the nightly assembles it still holds the position."""
    _pin_clock(monkeypatch, _WED_MORNING)
    t = _thesis(db)
    decisions_repo.append(
        db,
        thesis_id=t.id,
        tenant_id=DEFAULT_TENANT_ID,
        action="take",
        decision_date=_MON,
        price=5.0,
    )
    db.commit()
    assert _call_state(client, t.id, _TUE) == "managing"
    assert _post(client, t.id, _WED, action="close", price=6.0).status_code == 200
    assert _call_state(client, t.id, _TUE) == "incubating"
    assert call_for_thesis(db, t.id, _TUE, record=False).state.value == "managing"
    db.commit()


def test_a_later_recorded_fill_is_invisible_to_a_pinned_known_at(db, pinned_today):
    """Transaction time: a replay with known_at BEFORE the append sees the log as it stood — no
    rows at all (any_rows False), so even the fallback path is exactly the pre-log world.

    The unpinned read below is also the TWO-CLOCK regression guard. ``recorded_at`` is stamped by the
    POSTGRES clock (``DEFAULT now()``, 0019); ``derived_position`` once defaulted its bound to
    ``datetime.now(timezone.utc)`` on the APP HOST, so the two clocks were compared against each
    other and a row appended ~1 ms earlier could read as not-yet-recorded — measured on the dev box,
    the DB clock ran ~0.2-0.6 ms ahead while the whole append-then-read window was ~0.5-1.5 ms, and
    the filter is exact to the microsecond. It failed here once under a full-suite run and passed in
    isolation. The bound now comes from the DB (``clock_timestamp()``), so this read is one-clock — do
    NOT "stabilize" it by widening the window or padding with a sleep; that would hide the seam
    instead of closing it.
    """
    t = _thesis(db)
    decisions_repo.append(
        db,
        thesis_id=t.id,
        tenant_id=DEFAULT_TENANT_ID,
        action="take",
        decision_date=pinned_today - timedelta(days=3),
        price=5.0,
    )
    db.commit()
    pos, any_rows = decisions_repo.derived_position(
        db, t.id, asof=pinned_today, known_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    assert pos is None and any_rows is False
    pos, any_rows = decisions_repo.derived_position(db, t.id, asof=pinned_today)
    assert any_rows is True and pos is not None
    assert pos.opened_on == pinned_today - timedelta(days=3) and pos.entry_price == 5.0


def test_future_decision_date_is_rejected(client, db, pinned_today):
    t = _thesis(db)
    r = _post(
        client,
        t.id,
        pinned_today,
        action="take",
        decision_date=str(pinned_today + timedelta(days=1)),
    )
    assert r.status_code == 422
    assert "future" in r.json()["detail"]


# --- reversibility: void appends, restores, and stays visible --------------------------------------------


def test_void_restores_the_prior_state_and_the_table_only_grows(client, db, pinned_today):
    t = _thesis(db)
    take_id = _post(client, t.id, pinned_today, action="take", price=10.0).json()["id"]
    assert _call_state(client, t.id, pinned_today) == "managing"

    assert _post(client, t.id, pinned_today, action="void", voids=take_id).status_code == 200
    # the void un-does the take on read
    assert _call_state(client, t.id, pinned_today) == "incubating"

    rows = client.get(f"/theses/{t.id}/decisions").json()
    assert len(rows) == 2  # COUNT the table: the correction APPENDED — nothing was deleted
    by_action = {x["action"]: x for x in rows}
    assert by_action["take"]["voided"] is True  # grayed, not hidden
    assert by_action["void"]["voided"] is False
    assert by_action["void"]["voids"] == take_id


def test_void_validations(client, db, pinned_today):
    t = _thesis(db)
    assert _post(client, t.id, pinned_today, action="void").status_code == 422  # no target
    # an unknown target
    assert (
        _post(client, t.id, pinned_today, action="void", voids=str(uuid.uuid4())).status_code == 422
    )
    take_id = _post(client, t.id, pinned_today, action="take", price=1.0).json()["id"]
    void_id = _post(client, t.id, pinned_today, action="void", voids=take_id).json()["id"]
    # already voided
    assert _post(client, t.id, pinned_today, action="void", voids=take_id).status_code == 422
    # a void is final
    assert _post(client, t.id, pinned_today, action="void", voids=void_id).status_code == 422


# --- one open position per thesis (v1) --------------------------------------------------------------------


def test_take_requires_flat_and_close_requires_open(client, db, pinned_today):
    t = _thesis(db)
    assert _post(client, t.id, pinned_today, action="close").status_code == 422  # nothing to close
    assert _post(client, t.id, pinned_today, action="take", price=1.0).status_code == 200
    r = _post(client, t.id, pinned_today, action="take", price=2.0)
    assert r.status_code == 422 and "open position already exists" in r.json()["detail"]
    assert _post(client, t.id, pinned_today, action="close").status_code == 200
    # flat again -> allowed
    assert _post(client, t.id, pinned_today, action="take", price=3.0).status_code == 200


# --- precedence: the log beats the seed-era stored columns ------------------------------------------------


def test_seed_position_yields_to_the_log_and_never_resurrects(client, db, pinned_today):
    """A thesis with a stored (seed-era) position reads Managing via the fallback — but one logged
    close makes the LOG authoritative: net-closed stays closed; the stale columns don't come back.
    """
    t = _thesis(db, position=Position(entry_price=10.0, opened_on=date(2026, 1, 2)))
    # the fallback honors the seed columns
    assert _call_state(client, t.id, pinned_today) == "managing"

    # open (via fallback) -> closable
    assert _post(client, t.id, pinned_today, action="close").status_code == 200
    assert _call_state(client, t.id, pinned_today) == "incubating"  # the log (net closed) now wins


# --- per-member attribution: the held NAME reads managing on the member menu (CALL_LOGIC §4) -------------


def test_take_on_a_name_attributes_managing_per_member(client, db, security_id, pinned_today):
    """A take logged ON a name threads its security_id through the derived position into the member
    menu: the held member's call leads armed_members with verdict=managing (confidence null), its
    ticker resolved through the master like any member."""
    t = _thesis(db)
    r = _post(client, t.id, pinned_today, action="take", price=10.0, security_id=str(security_id))
    assert r.status_code == 200
    card = client.get(f"/theses/{t.id}/call", params={"asof": str(pinned_today)}).json()
    assert card["state"] == "managing"
    assert [m["security_id"] for m in card["armed_members"]] == [str(security_id)]
    held = card["armed_members"][0]
    assert held["verdict"] == "managing" and held["confidence"] is None
    assert held["ticker"] == "DEVCO"


def test_thesis_level_take_attributes_no_member(client, db, pinned_today):
    """A thesis-level take (no name on the row) flips the state but attributes nothing per-member —
    honest absence, never a guessed name (the seed-era stored columns behave the same way)."""
    t = _thesis(db)
    _post(client, t.id, pinned_today, action="take", price=10.0)
    card = client.get(f"/theses/{t.id}/call", params={"asof": str(pinned_today)}).json()
    assert card["state"] == "managing"
    assert card["armed_members"] == []


def test_tenant_isolation_on_the_log(db, pinned_today):
    t = _thesis(db)
    decisions_repo.append(
        db, thesis_id=t.id, tenant_id=DEFAULT_TENANT_ID, action="pass", decision_date=pinned_today
    )
    db.commit()
    assert decisions_repo.list_for_thesis(db, t.id, tenant_id=uuid.uuid4()) == []
    assert len(decisions_repo.list_for_thesis(db, t.id, tenant_id=DEFAULT_TENANT_ID)) == 1
