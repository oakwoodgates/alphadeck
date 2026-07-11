"""M2b — the daily call-of-record cron, end-to-end. The DB is real (the `db` fixture, against
alphadeck_test); the cron's network ingest step is stubbed (facts are controlled directly), so these tests
exercise the real list_all -> call_for_thesis -> record_if_changed wiring. The headline is the idempotency
gate: a same-day re-run appends ZERO rows — asserted by COUNTING the calls table, not by reading it (the log
dedups on read, so a duplicate append hides behind a correct read while the table silently grows).
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from db.session import DEFAULT_TENANT_ID
from domain.enums import State
from ingest.edgar.form4 import ingest_form4
from pipeline import daily
from repositories import calls_repo

# form4_sample.xml is a senior (CEO) open-market P-buy dated 2026-06-01; this asof is within its flip
# liveness (18d), so ingesting it flips a fact-less thesis Incubating -> Warming.
_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)
_ASOF = date(2026, 6, 10)


def _no_network(monkeypatch):
    """Stub the cron's ingest step so run_daily never hits the network — facts are set directly here."""
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])


def _thesis(db, name, *, members=()):
    """Persist a thesis (members = list of (ticker, security_id))."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, archetype, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", "high_beta", sid),
            )
    db.commit()
    return tid


def _calls(db, thesis_id):
    return calls_repo.list_for_thesis(db, thesis_id)  # the full append-only history (every row)


def test_daily_idempotent_end_to_end_count_the_table(db, monkeypatch):
    _no_network(monkeypatch)
    tid = _thesis(db, "Fact-less thesis")

    daily.run_daily(db, asof=_ASOF, allow_live=False)
    assert len(_calls(db, tid)) == 1  # one call-of-record appended

    daily.run_daily(db, asof=_ASOF, allow_live=False)  # identical re-run, same day
    assert len(_calls(db, tid)) == 1  # COUNT the table: STILL one row, not two


def test_daily_appends_exactly_one_when_the_call_changes(db, security_id, monkeypatch):
    _no_network(monkeypatch)
    tid = _thesis(db, "Nuclear", members=[("DEVCO", security_id)])

    daily.run_daily(db, asof=_ASOF, allow_live=False)
    assert len(_calls(db, tid)) == 1
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.INCUBATING  # no facts yet

    # a GENUINE change at the SAME asof: a senior open-market P-buy (valid_from 2026-06-01) warms it
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    db.commit()

    daily.run_daily(db, asof=_ASOF, allow_live=False)
    assert len(_calls(db, tid)) == 2  # exactly one NEW versioned row
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.WARMING  # the changed call wins

    # and re-running on the now-unchanged data appends nothing more
    daily.run_daily(db, asof=_ASOF, allow_live=False)
    assert len(_calls(db, tid)) == 2


def test_daily_emits_a_transition_only_on_a_real_state_or_verdict_move(
    db, security_id, monkeypatch
):
    """The notify seam (slice C): a MATERIAL transition = state/verdict changed vs the PRIOR as-of's
    call-of-record. Three days pin the line: day 1 (first-ever call — a birth is not a transition, no
    prior to move from), day 2 (the P-buy lands: incubating → warming — ONE event, and the result row
    carries the label), day 3 (still warming: the card CHANGES — its clocks shift with asof, so
    record_if_changed appends a new version — but state/verdict hold, so NO event: churn ≠ transition,
    the calls-log material-change question answered where it bites)."""
    _no_network(monkeypatch)
    tid = _thesis(db, "Nuclear", members=[("DEVCO", security_id)])
    captured: list = []

    class Capture:
        def notify(self, event):
            captured.append(event)

    by = {
        r.thesis_id: r
        for r in daily.run_daily(db, asof=date(2026, 6, 5), allow_live=False, notifier=Capture())
    }
    assert captured == [] and by[tid].transition is None  # first-ever call: no prior, no event

    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    db.commit()
    by = {
        r.thesis_id: r
        for r in daily.run_daily(db, asof=_ASOF, allow_live=False, notifier=Capture())
    }
    assert len(captured) == 1
    evt = captured[0]
    assert evt.from_state == "incubating" and evt.to_state == "warming"
    assert by[tid].transition == evt.label and "incubating → warming" in evt.label

    captured.clear()
    day3 = {
        r.thesis_id: r
        for r in daily.run_daily(db, asof=date(2026, 6, 11), allow_live=False, notifier=Capture())
    }
    assert captured == [] and day3[tid].transition is None  # a changed card, but no MOVE
    assert day3[tid].recorded is True  # ...and it DID version the log (churn without transition)


def test_daily_one_thesis_failure_does_not_abort_the_rest(db, monkeypatch):
    _no_network(monkeypatch)
    good = _thesis(db, "GOOD")
    bad = _thesis(
        db, "BAD"
    )  # sorts first in list_all (by name) -> proves an early failure isn't fatal

    real = daily.call_for_thesis

    def flaky(conn, thesis_id, asof, **kw):
        if thesis_id == bad:
            raise RuntimeError("boom")
        return real(conn, thesis_id, asof, **kw)

    monkeypatch.setattr(daily, "call_for_thesis", flaky)

    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=False)}

    assert by[good].recorded is True and by[good].error is None
    assert by[bad].recorded is None and by[bad].error and "boom" in by[bad].error
    assert len(_calls(db, good)) == 1  # the good thesis still got its call-of-record
    assert len(_calls(db, bad)) == 0  # the failed one wrote nothing
