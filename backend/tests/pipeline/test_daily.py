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

import pytest

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


def test_daily_warns_loudly_when_primary_flags_were_never_stamped(db, monkeypatch, capsys):
    """The canonical-primary guard: a master with multi-row CIKs and ZERO is_primary flags resolves each
    multi-sibling CIK to an ARBITRARY row — silently (nothing errors). The cron is the daily surface: it
    prints the warning (naming the one-command fix) ONLY in the broken state, and stays quiet once any
    flag is stamped (loudness marks the exception)."""
    _no_network(monkeypatch)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES "
            "(%s, %s, 'ASML',  '0000937966', '2026-01-01'), "
            "(%s, %s, 'ASMLF', '0000937966', '2026-01-01')",
            (uuid.uuid4(), DEFAULT_TENANT_ID, uuid.uuid4(), DEFAULT_TENANT_ID),
        )
    db.commit()

    daily.run_daily(db, asof=_ASOF, allow_live=True)
    out = capsys.readouterr().out
    assert "ZERO is_primary flags" in out and "populate_master --live" in out  # loud + actionable

    with db.cursor() as cur:  # what a populate_master re-run does — the guard must then go quiet
        cur.execute("UPDATE security_master SET is_primary = true WHERE ticker = 'ASML'")
    db.commit()
    daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert "is_primary" not in capsys.readouterr().out


def test_daily_idempotent_end_to_end_count_the_table(db, monkeypatch):
    _no_network(monkeypatch)
    tid = _thesis(db, "Fact-less thesis")

    daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert len(_calls(db, tid)) == 1  # one call-of-record appended

    daily.run_daily(db, asof=_ASOF, allow_live=True)  # identical re-run, same day
    assert len(_calls(db, tid)) == 1  # COUNT the table: STILL one row, not two


def test_daily_appends_exactly_one_when_the_call_changes(db, security_id, monkeypatch):
    _no_network(monkeypatch)
    tid = _thesis(db, "Nuclear", members=[("DEVCO", security_id)])

    daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert len(_calls(db, tid)) == 1
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.INCUBATING  # no facts yet

    # a GENUINE change at the SAME asof: a senior open-market P-buy (valid_from 2026-06-01) warms it
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    db.commit()

    daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert len(_calls(db, tid)) == 2  # exactly one NEW versioned row
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.WARMING  # the changed call wins

    # and re-running on the now-unchanged data appends nothing more
    daily.run_daily(db, asof=_ASOF, allow_live=True)
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
        for r in daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True, notifier=Capture())
    }
    assert captured == [] and by[tid].transition is None  # first-ever call: no prior, no event

    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    db.commit()
    by = {
        r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=Capture())
    }
    assert len(captured) == 1
    evt = captured[0]
    assert evt.from_state == "incubating" and evt.to_state == "warming"
    assert by[tid].transition == evt.label and "incubating → warming" in evt.label

    captured.clear()
    day3 = {
        r.thesis_id: r
        for r in daily.run_daily(db, asof=date(2026, 6, 11), allow_live=True, notifier=Capture())
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

    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True)}

    assert by[good].recorded is True and by[good].error is None
    assert by[bad].recorded is None and by[bad].error and "boom" in by[bad].error
    assert len(_calls(db, good)) == 1  # the good thesis still got its call-of-record
    assert len(_calls(db, bad)) == 0  # the failed one wrote nothing


# --- R4: assess_health — the pageable run-health signal (pure; no DB) ---


def _tr(**kw):
    return daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", **kw)


def test_assess_health_flags_a_FREEZE():
    # a live run, names present, ZERO EDGAR fetches summed = the cache never refreshed = the R1 freeze
    h = daily.assess_health(
        [_tr(edgar_fetches=0, recorded=True), _tr(edgar_fetches=0, recorded=False)],
        asof=_ASOF,
        allow_live=True,
    )
    assert h is not None and h.frozen is True and "FROZEN" in h.label


def test_assess_health_is_NONE_when_healthy():
    # a live run that fetched + recorded, no withheld/errors → healthy → NO page (loudness marks the exception)
    assert (
        daily.assess_health(
            [_tr(edgar_fetches=88, recorded=True), _tr(edgar_fetches=90, recorded=False)],
            asof=_ASOF,
            allow_live=True,
        )
        is None
    )


def test_assess_health_splits_a_TOTAL_FAILURE_from_a_benign_no_live():
    # a real total-ingest failure is an ALARM; a --no-live withhold is BENIGN — the page must say which
    h = daily.assess_health(
        [
            _tr(edgar_fetches=90, withheld_reason="total ingest failure"),
            _tr(edgar_fetches=88, withheld_reason="no-live"),
            _tr(edgar_fetches=90, error="boom"),
        ],
        asof=_ASOF,
        allow_live=True,
    )
    assert h is not None and h.frozen is False  # fetches happened → not frozen
    assert h.withheld_failure == 1 and h.withheld_no_live == 1 and h.errored == 1
    assert h.withheld == 2  # the property sums both reasons
    assert "TOTAL INGEST FAILURE" in h.label  # the alarm is loud
    assert "not an error" in h.label  # the no-live part is explicitly benign


def test_a_no_live_run_is_withheld_NOT_flagged_frozen_and_reads_BENIGN():
    # --no-live legitimately makes 0 EDGAR fetches — EXPECTED, not a freeze. It surfaces as a benign no-live
    # note (never FROZEN, which requires allow_live), so a hand-run dev pass never reads as "failure".
    h = daily.assess_health(
        [_tr(edgar_fetches=0, withheld_reason="no-live")], asof=_ASOF, allow_live=False
    )
    assert h is not None and h.frozen is False and h.withheld == 1
    assert "not an error" in h.label and "FAILURE" not in h.label


# --- R6: the --catch-up guard in main (no DB — the guard returns before connect) ---


def _no_connect(monkeypatch):
    """Make connect() explode, so a test proves whether main proceeded PAST the R6 guard into the real run."""

    def boom():
        raise RuntimeError("reached the run")

    monkeypatch.setattr(daily, "connect", boom)


def test_catch_up_is_a_NOOP_when_a_live_pass_already_ran(monkeypatch, capsys):
    monkeypatch.setattr(daily, "already_ran_live", lambda asof: True)
    _no_connect(monkeypatch)  # if the guard fails, main() would connect and RuntimeError
    daily.main(["--catch-up", "--asof", "2026-07-17"])  # returns early, never connects
    assert "already ran" in capsys.readouterr().out


def test_catch_up_RUNS_when_no_live_pass_yet(monkeypatch):
    monkeypatch.setattr(daily, "already_ran_live", lambda asof: False)
    _no_connect(monkeypatch)  # proves it proceeded past the guard into the run
    with pytest.raises(RuntimeError, match="reached the run"):
        daily.main(["--catch-up", "--asof", "2026-07-17"])


def test_the_guard_is_NOT_consulted_without_catch_up(monkeypatch):
    # a normal cron run always runs — `--catch-up` absent must short-circuit before already_ran_live
    def guard(_asof):
        raise AssertionError("already_ran_live consulted without --catch-up")

    monkeypatch.setattr(daily, "already_ran_live", guard)
    _no_connect(monkeypatch)
    with pytest.raises(RuntimeError, match="reached the run"):
        daily.main(["--asof", "2026-07-17"])


# --- R2a: the recording gate (a run that didn't refresh must not write the log of record) ---


def _name_result(*, error=None):
    from pipeline.ingest_thesis import NameResult

    return NameResult(
        ticker="T", security_id=uuid.uuid4(), form4_appended=0, price_bars_appended=0, error=error
    )


def _stub_ingest(monkeypatch, fn):
    monkeypatch.setattr(daily, "ingest_thesis", fn)


def _health(db, thesis_id):
    """The recorded call's ingest-health provenance (R2b) — NOT on the CallCard, read straight from calls."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT ingest_fresh, ingest_errors FROM calls WHERE thesis_id=%s ORDER BY seq DESC LIMIT 1",
            (thesis_id,),
        )
        return cur.fetchone()


def test_no_live_run_WITHHOLDS_the_call(db, monkeypatch):
    """Source A (decision Q3, structural): a --no-live / cache-only run has no business writing the log of
    record — even though (verified) it does NOT error on a warm cache. Gated on allow_live, not on failure.
    """
    _stub_ingest(monkeypatch, lambda *a, **k: [])  # a clean, do-nothing ingest — no error
    tid = _thesis(db, "Nuclear")
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=False)}
    assert by[tid].withheld_reason == "no-live"
    assert by[tid].recorded is None
    assert len(_calls(db, tid)) == 0  # COUNT the table — nothing written


def test_total_ingest_failure_WITHHOLDS_but_a_healthy_thesis_still_records(db, monkeypatch):
    """Source C (the all-errored form): a thesis whose EVERY name errored withholds; the `continue` means a
    healthy thesis in the same run still records (isolation). Neither is a raise — both ingests returned.
    """
    good = _thesis(db, "GOOD")
    bad = _thesis(db, "BAD")

    def ingest(conn, thesis_id, **k):
        return (
            [_name_result(error="form4: boom"), _name_result(error="form4: boom")]
            if thesis_id == bad
            else []
        )

    _stub_ingest(monkeypatch, ingest)
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True)}

    assert by[bad].withheld_reason == "total ingest failure" and len(_calls(db, bad)) == 0
    assert (
        by[good].recorded is True and len(_calls(db, good)) == 1
    )  # the `continue` didn't skip the rest


def test_thesis_level_ingest_RAISE_withholds_and_isolates(db, monkeypatch):
    """Source C (the raised form): a thesis-level ingest exception withholds AND the missing `continue` now
    stops it falling through to record a call on failed facts — while the next thesis still records.
    """
    good = _thesis(db, "GOOD")
    bad = _thesis(db, "BAD")

    def ingest(conn, thesis_id, **k):
        if thesis_id == bad:
            raise RuntimeError("ingest exploded")
        return []

    _stub_ingest(monkeypatch, ingest)
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True)}

    assert by[bad].withheld_reason == "total ingest failure" and "ingest exploded" in by[bad].error
    assert len(_calls(db, bad)) == 0  # the `continue` prevented a call-on-failed-facts
    assert by[good].recorded is True and len(_calls(db, good)) == 1


def test_healthy_run_records_and_stamps_ingest_fresh_TRUE(db, monkeypatch):
    """A clean ingest (every name ok — here an empty basket, the new-thesis case) records AND stamps
    ingest_fresh=True. A quiet current thesis (0 appended, no errors) is healthy, not a failure."""
    _stub_ingest(monkeypatch, lambda *a, **k: [_name_result(), _name_result()])
    tid = _thesis(db, "Nuclear")
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True)}
    assert by[tid].recorded is True and by[tid].withheld_reason is None
    assert _health(db, tid) == {"ingest_fresh": True, "ingest_errors": 0}


def test_PARTIAL_failure_records_but_marks_ingest_fresh_FALSE(db, monkeypatch):
    """The case R2b exists for: SOME names errored, some clean → the call STILL records (a partial ingest is
    not a total failure), but ingest_fresh=False + the error count marks it so the Scoreboard can discount it.
    """
    _stub_ingest(monkeypatch, lambda *a, **k: [_name_result(), _name_result(error="form4: boom")])
    tid = _thesis(db, "Nuclear")
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True)}
    assert by[tid].recorded is True and by[tid].withheld_reason is None  # partial still records
    assert _health(db, tid) == {"ingest_fresh": False, "ingest_errors": 1}  # ...but marked


def test_freshness_is_NOT_in_the_change_compare(db, security_id, monkeypatch):
    """ingest_fresh rides the WRITE, never `_canonical`: a same-asof re-run producing the IDENTICAL card must
    append ZERO rows even if the health differs — else a partial→clean flip would fake a change every run.
    """
    tid = _thesis(db, "Nuclear", members=[("DEVCO", security_id)])
    # run 1: PARTIAL ingest (one clean, one errored) → records the Incubating card, marked partial
    _stub_ingest(monkeypatch, lambda *a, **k: [_name_result(), _name_result(error="boom")])
    daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert len(_calls(db, tid)) == 1 and _health(db, tid)["ingest_fresh"] is False
    # run 2: healthy ingest, SAME card (no facts changed) → NO new row; the original stamp stands
    _stub_ingest(monkeypatch, lambda *a, **k: [_name_result()])
    daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert len(_calls(db, tid)) == 1  # freshness flip did NOT append (COUNT the table)
    assert _health(db, tid)["ingest_fresh"] is False  # still the first run's stamp
