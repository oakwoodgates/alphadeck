"""M2b — the daily call-of-record cron, end-to-end. The DB is real (the `db` fixture, against
alphadeck_test); the cron's network ingest step is stubbed (facts are controlled directly), so these tests
exercise the real list_all -> call_for_thesis -> record_if_changed wiring. The headline is the idempotency
gate: a same-day re-run appends ZERO rows — asserted by COUNTING the calls table, not by reading it (the log
dedups on read, so a duplicate append hides behind a correct read while the table silently grows).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.config import DEFAULT_CONFIG, config_hash
from domain.enums import Grade, Kind, State, Verdict
from domain.feed_kinds import StaleFeedLabel
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.edgar.form4 import ingest_form4
from ingest.edgar.submissions import DelistingFiling
from notify import ArmedName, LogNotifier
from pipeline import cron_run_log, daily
from pipeline.cron_run_log import write_cron_run_log
from pipeline.ingest_thesis import NameResult
from pipeline.insider_amendment_scan import AmendmentScan, SweepOutcome
from pipeline.missing_closes import MissingCloses
from pipeline.rename_probe import RenameProbe
from pipeline.split_basis import SplitScan
from pipeline.split_jumps import JumpScan
from pipeline.successor_cik import SuccessorScan
from repositories import calls_repo, price_feed_repo
from tests import price_feed_fixtures as fx

# form4_sample.xml is a senior (CEO) open-market P-buy dated 2026-06-01; this asof is within its flip
# liveness (18d), so ingesting it flips a fact-less thesis Incubating -> Warming.
_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)
_ASOF = date(2026, 6, 10)


class _FakeLock:
    """Stands in for a held call-of-record lock (C1) in THIS file, so the pass / CLI tests here stay
    DB-free where they were before the lock existed. The REAL lock — concurrency, release on close, the
    exit code, the Admin 409 — is proved against Postgres in ``tests/pipeline/test_run_lock.py``."""

    released = False

    def release(self):
        self.released = True


@pytest.fixture(autouse=True)
def _fake_run_lock(monkeypatch):
    monkeypatch.setattr(daily, "acquire_pass_lock", lambda **kw: _FakeLock())


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
                "(id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
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

        def notify_spac_status(
            self, event
        ):  # pragma: no cover — radar leg not exercised in run_daily
            pass

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


# --- the compact armed push: company-level detail threaded onto the TransitionEvent (+ fail-open) ---


def _armed_card(thesis_id, sid, asof):
    """A minimal ARMED CallCard whose single armed member fired two distinct entry triggers — the shape the
    daily enrichment reads (armed_members -> per-member security_id + entry_grade + trigger kinds).
    """
    return CallCard(
        thesis_id=thesis_id,
        asof=asof,
        state=State.ARMED,
        verdict=Verdict.CORE_ENTRY,
        armed_security_id=sid,
        expression="—",
        key_conviction=KeyState(turned=True, label="conviction"),
        key_confirmation=KeyState(turned=True, label="confirmation"),
        armed_members=[
            MemberCall(
                security_id=sid,
                verdict=Verdict.CORE_ENTRY,
                entry_grade=Grade.CORE,
                triggers=[
                    TriggerRef(label="P-buy", kind=Kind.INSIDER, security_id=sid),
                    TriggerRef(label="breakout", kind=Kind.TECHNICAL_BREAKOUT, security_id=sid),
                ],
            )
        ],
    )


class _Capture:
    def __init__(self):
        self.events = []

    def notify(self, event):
        self.events.append(event)

    def notify_spac_status(self, event):  # pragma: no cover — radar leg not exercised in run_daily
        pass


def test_daily_armed_transition_threads_company_detail_onto_the_event(db, security_id, monkeypatch):
    """A → armed transition resolves each armed member to its ticker (master.tickers_for) + its DISTINCT
    entry-trigger kinds and threads them onto the TransitionEvent — the compact push's data. The
    call-of-record is still written (the enrichment is additive, never gating)."""
    _no_network(monkeypatch)
    tid = _thesis(db, "AI Memory", members=[("DEVCO", security_id)])

    # day 1: the real (fact-less) call is INCUBATING — the prior to move FROM
    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)
    assert len(_calls(db, tid)) == 1

    # day 2: stub the assembler to return an ARMED card → INCUBATING → ARMED transition fires
    monkeypatch.setattr(
        daily, "call_for_thesis", lambda conn, thid, asof, **k: _armed_card(thid, security_id, asof)
    )
    cap = _Capture()
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=cap)}

    assert len(cap.events) == 1
    evt = cap.events[0]
    assert evt.from_state == "incubating" and evt.to_state == "armed"
    # DEVCO resolved from the security master; the two distinct kinds carried in fire order; grade carried
    assert evt.armed == (
        ArmedName("DEVCO", ("insider", "technical_breakout"), "core", security_id=security_id),
    )
    assert by[tid].recorded is True and len(_calls(db, tid)) == 2  # the record was still written


def test_daily_armed_enrichment_is_FAIL_OPEN(db, security_id, monkeypatch):
    """The enrichment is DB-touching and runs INSIDE the shared try that rolls back the call-of-record on
    any exception — so its ticker read is wrapped in a SAVEPOINT (conn.transaction()) and the whole block
    has its OWN fail-open guard. A resolver that RAISES rolls back to the savepoint (issuing real
    SAVEPOINT/ROLLBACK SQL on the test conn), re-raises into the except → the event degrades to thesis-only
    (armed=()), the OUTER txn stays clean, and the ARMED call-of-record is STILL written (the point of the
    savepoint: a query-specific fault never costs the thesis its record)."""
    _no_network(monkeypatch)
    tid = _thesis(db, "AI Memory", members=[("DEVCO", security_id)])
    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)  # INCUBATING prior

    monkeypatch.setattr(
        daily, "call_for_thesis", lambda conn, thid, asof, **k: _armed_card(thid, security_id, asof)
    )

    def _boom(*a, **k):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(daily.master, "tickers_for", _boom)  # the enrichment's DB read raises
    cap = _Capture()
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=cap)}

    assert len(cap.events) == 1
    evt = cap.events[0]
    assert evt.to_state == "armed" and evt.armed == ()  # degraded to the thesis-only line
    assert by[tid].recorded is True and len(_calls(db, tid)) == 2  # ...the record was NOT dropped
    # the savepoint kept the outer txn writable: the ARMED card actually landed as the call-of-record
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.ARMED


_NY_TZ = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("now", "timing"),
    [
        (datetime(2026, 6, 10, 22, 41, tzinfo=_NY_TZ), "current"),  # tonight's pass
        (datetime(2026, 6, 11, 0, 30, tzinfo=_NY_TZ), "past"),  # ran past midnight, still on time
        (datetime(2026, 6, 11, 17, 4, tzinfo=_NY_TZ), "late"),  # a next-afternoon boot catch-up
    ],
)
def test_daily_a_transition_pushed_for_a_past_or_late_night_says_so(
    db, security_id, monkeypatch, now, timing
):
    """N1 — the event is stamped with WHEN it is pushed relative to its night (the market clock at the
    push, judged by ``night_coverage.night_timing``), so the Slack header can say "(asof D, recorded
    late)". The call-of-record is written either way."""
    _no_network(monkeypatch)
    tid = _thesis(db, "AI Memory", members=[("DEVCO", security_id)])
    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)  # INCUBATING prior
    monkeypatch.setattr(
        daily, "call_for_thesis", lambda conn, thid, asof, **k: _armed_card(thid, security_id, asof)
    )
    monkeypatch.setattr(daily, "market_now", lambda: now)
    monkeypatch.setattr(daily, "market_tz", lambda: _NY_TZ)
    cap = _Capture()
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=cap)}

    (evt,) = cap.events
    assert evt.to_state == "armed" and evt.timing == timing
    assert by[tid].recorded is True


def test_daily_the_push_timing_is_FAIL_OPEN(db, security_id, monkeypatch):
    """The label runs inside the per-thesis try that rolls the record back on any exception — a clock or
    zone fault degrades it to "current" and never costs the night its call-of-record."""
    _no_network(monkeypatch)
    tid = _thesis(db, "AI Memory", members=[("DEVCO", security_id)])
    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)
    monkeypatch.setattr(
        daily, "call_for_thesis", lambda conn, thid, asof, **k: _armed_card(thid, security_id, asof)
    )

    def _bad_zone():
        raise RuntimeError("ALPHADECK_MARKET_TZ could not be resolved")

    monkeypatch.setattr(daily, "market_tz", _bad_zone)
    cap = _Capture()
    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=cap)}

    (evt,) = cap.events
    assert evt.timing == "current"
    assert by[tid].recorded is True and len(_calls(db, tid)) == 2


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


def test_assess_health_freeze_check_off_skips_the_FROZEN_page_only():
    # `freeze_check=False` skips the frozen predicate outright (since O5 no production caller passes it — a
    # catch-up is judged by `catch_up` + the skipped theses, below). The SAME numbers: a live run with 0
    # fetches pages FROZEN by default (the existing contract, unchanged)…
    results = [_tr(edgar_fetches=0, recorded=False), _tr(edgar_fetches=0, recorded=True)]
    h = daily.assess_health(results, asof=_ASOF, allow_live=True)
    assert h is not None and h.frozen is True
    # …and is a CLEAN run (None — no page) with freeze_check=False
    assert daily.assess_health(results, asof=_ASOF, allow_live=True, freeze_check=False) is None


def test_assess_health_freeze_check_off_STILL_pages_withheld_and_errored():
    # the skip is narrow: a total ingest failure or a thesis error on a catch-up still pages
    h = daily.assess_health(
        [
            _tr(edgar_fetches=0, withheld_reason="total ingest failure"),
            _tr(edgar_fetches=0, error="boom"),
        ],
        asof=_ASOF,
        allow_live=True,
        freeze_check=False,
    )
    assert h is not None and h.frozen is False
    assert h.withheld_failure == 1 and h.errored == 1
    assert "TOTAL INGEST FAILURE" in h.label and "FROZEN" not in h.label


# --- R6: the --catch-up guard in main (no DB — the guard returns before connect) ---
# The guard reads REAL run-of-record artifacts (written by `write_cron_run_log` into a redirected run dir)
# under the 2026-09-10 rule: a live pass for the as-of counts only if it STARTED at/after that night's RUN_AT
# in market time. `main` supplies RUN_AT + the zone from config; the tests pin the prod defaults (22:30,
# America/New_York) explicitly so an ambient ALPHADECK_CRON_AT / ALPHADECK_MARKET_TZ cannot move the cutoff.
# July + September 2026 are EDT: 22:30 ET == 02:30Z the next day; the writer stamps aware UTC.

_NY = ZoneInfo("America/New_York")


def _tonight(monkeypatch, asof: date) -> None:
    """Pin the CLI's market clock to 23:00 ET on ``asof``: a pass typed ON that night, which C3 lets
    through (an ``--asof`` older than the last expected night is refused before anything runs)."""
    monkeypatch.setattr(
        daily, "market_now", lambda: datetime.combine(asof, time(23, 0), tzinfo=_NY)
    )


def _no_connect(monkeypatch):
    """Make connect() explode, so a test proves whether main proceeded PAST the R6 guard into the real run."""

    def boom():
        raise RuntimeError("reached the run")

    monkeypatch.setattr(daily, "connect", boom)


def _pin_schedule(monkeypatch, tmp_path):
    """The prod schedule (22:30 America/New_York) + an EMPTY run dir the guard reads from."""
    monkeypatch.setattr(daily, "get_settings", lambda: SimpleNamespace(cron_run_at="22:30"))
    monkeypatch.setattr(daily, "market_tz", lambda: _NY)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)


def _seed_live_pass(tmp_path, *, asof: date, at_market: datetime):
    """A real live artifact for `asof`; `at_market` is the market-time wall clock the pass started at."""
    write_cron_run_log(
        [daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=True)],
        asof=asof,
        allow_live=True,
        started_at=at_market.astimezone(timezone.utc),
        finished_at=at_market.astimezone(timezone.utc),
        base_dir=tmp_path,
    )


def test_catch_up_is_a_NOOP_when_a_live_pass_already_ran(monkeypatch, tmp_path, capsys):
    _pin_schedule(monkeypatch, tmp_path)
    # the night's own pass: started 5 s after 22:30 ET on the as-of (post-RUN_AT)
    _seed_live_pass(
        tmp_path, asof=date(2026, 7, 17), at_market=datetime(2026, 7, 17, 22, 30, 5, tzinfo=_NY)
    )
    _no_connect(monkeypatch)  # if the guard fails, main() would connect and RuntimeError
    daily.main(["--catch-up", "--asof", "2026-07-17"])  # returns early, never connects
    out = capsys.readouterr().out
    assert "already ran" in out
    # the no-op message says what it checked
    assert "started at/after 22:30 America/New_York" in out


def test_catch_up_RUNS_when_no_live_pass_yet(monkeypatch, tmp_path):
    _pin_schedule(monkeypatch, tmp_path)  # an empty run dir: nothing ran
    _no_connect(monkeypatch)  # proves it proceeded past the guard into the run
    with pytest.raises(RuntimeError, match="reached the run"):
        daily.main(["--catch-up", "--asof", "2026-07-17"])


def test_a_pre_RUN_AT_live_pass_does_NOT_make_catch_up_a_noop(monkeypatch, tmp_path):
    """The Sep 9 2026 case: a pre-open Admin "Run daily now" for as-of Sep 9 at 09:15 ET ran on Sep 8's bars,
    and the host was then OFF at the 22:30 run. The boot catch-up the next day must still RUN — a pass that
    started before the night's RUN_AT is not the night's pass (the old any-live-pass guard called it a no-op,
    and Sep 9's record stayed a pre-open call)."""
    _pin_schedule(monkeypatch, tmp_path)
    _seed_live_pass(
        tmp_path, asof=date(2026, 9, 9), at_market=datetime(2026, 9, 9, 9, 15, tzinfo=_NY)
    )
    _no_connect(monkeypatch)
    with pytest.raises(RuntimeError, match="reached the run"):
        daily.main(["--catch-up", "--asof", "2026-09-09"])


def test_the_guard_is_NOT_consulted_without_catch_up(monkeypatch):
    # a normal cron run always runs — `--catch-up` absent must short-circuit before already_ran_live
    def guard(_asof, **_kw):
        raise AssertionError("already_ran_live consulted without --catch-up")

    monkeypatch.setattr(daily, "already_ran_live", guard)
    _no_connect(monkeypatch)
    _tonight(monkeypatch, date(2026, 7, 17))
    with pytest.raises(RuntimeError, match="reached the run"):
        daily.main(["--asof", "2026-07-17"])


def test_main_threads_catch_up_into_the_pass(monkeypatch):
    # `--catch-up` reaches run_daily_pass as catch_up=True (the sidecar's late-wake catch-up of a skipped
    # night: `--catch-up --asof <d>`); a plain scheduled run threads False. The pass is stubbed — this pins
    # the CLI → pass wiring only.
    monkeypatch.setattr(daily, "already_ran_live", lambda asof, **kw: False)
    seen: dict = {}
    now = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)

    def _pass(**kw):
        seen.update(kw)
        return daily.DailyPassOutcome(
            results=[],
            asof=kw["asof"],
            allow_live=kw["allow_live"],
            started_at=now,
            finished_at=now,
            log_path=None,
            catch_up=kw["catch_up"],
        )

    monkeypatch.setattr(daily, "run_daily_pass", _pass)
    daily.main(["--catch-up", "--asof", "2026-09-08"])
    assert seen["catch_up"] is True and seen["asof"] == date(2026, 9, 8)
    _tonight(monkeypatch, date(2026, 9, 8))  # the plain run is typed ON its night (C3)
    daily.main(["--asof", "2026-09-08"])
    assert seen["catch_up"] is False


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


# --- Band-02: the shared-input refresh legs in run_daily_pass (benchmarks + fundamentals) ---
# DB-free — mirroring the guard-test precedent above (which monkeypatches connect), every side-effecting
# seam run_daily_pass touches is stubbed: connect (all four connections), run_daily (the per-thesis pass),
# write_cron_run_log (the fail-open run-of-record write), the SPAC-radar leg, and the two lazily-imported
# ingests (patched on their OWN modules, since each leg does `from pipeline.ingest_* import ...` at call
# time — exactly like the radar leg's `from radar.spac import run_spac_radar`).


class _Silent:
    """A no-op notifier — run_daily is stubbed and assess_health([]) returns None, so nothing is emitted."""

    def notify(self, event):  # pragma: no cover - never reached with an empty result set
        pass

    def notify_health(self, event):  # pragma: no cover
        pass

    def notify_spac_status(self, event):  # pragma: no cover — radar leg stubbed in these pass tests
        pass


def _prime_pass(monkeypatch, *, bench_raises=False, sweep_raises=False):
    """Stub run_daily_pass's seams so the refresh + universe legs run DB-free. Returns (order, calls,
    seen): ``order`` is the recorded call sequence (the load-bearing ordering properties), ``calls``
    counts each seam, and ``seen`` captures the kwargs each leg was invoked with — so a test can prove
    the load-bearing wiring (benchmarks force_refresh=True; the shell sweep's Monday-only reenrich).
    The bare fundamentals leg is still stubbed and COUNTED, so a test can prove the pass no longer calls
    it (Q17 zero-lag: the refresh runs per thesis inside ``run_daily``).
    """
    order: list[str] = []
    calls = {"benchmarks": 0, "fundamentals": 0, "run_daily": 0, "shell_sweep": 0, "spac_radar": 0}
    seen: dict[str, dict] = {}  # last-seen kwargs per leg

    # a fake connection: `close` for the legs, `execute` / `commit` / `autocommit` for the C2 session
    # statement_timeout `_pass_connect` sets on every connection the pass opens
    monkeypatch.setattr(
        daily,
        "connect",
        lambda: SimpleNamespace(
            close=lambda: None, execute=lambda *a, **k: None, commit=lambda: None, autocommit=False
        ),
    )
    monkeypatch.setattr(daily, "write_cron_run_log", lambda *a, **k: None)  # fail-open, no file I/O
    # the V1 self-audit reads the calls TABLE — DB-free here; its checks are proved on a real DB in
    # tests/pipeline/test_self_audit.py
    monkeypatch.setattr(daily, "run_audit", lambda ctx, *a, **k: [])
    # the T4 split-basis scan reads the price TABLE — DB-free here; proved on a real DB (and end to end
    # through run_daily_pass) in tests/pipeline/test_split_basis.py
    monkeypatch.setattr(daily, "run_split_scan", lambda **k: SplitScan())
    monkeypatch.setattr(
        daily, "run_jump_scan", lambda **k: JumpScan()
    )  # S5 — proved in test_split_jumps.py
    # the missing-close check reads the price TABLE — DB-free here; proved on a real DB (and end to end
    # through run_daily_pass) in tests/pipeline/test_missing_closes.py
    monkeypatch.setattr(daily, "run_missing_closes", lambda **k: MissingCloses())
    # the T2 rename probe reads the price TABLE and fetches the vendor — DB- and network-free here; proved
    # on a real DB with a fake source in tests/pipeline/test_rename_probe.py
    monkeypatch.setattr(daily, "run_rename_probe", lambda **k: RenameProbe(evaluated=True))
    # the T7 successor-CIK scan reads the master and fetches SEC's ticker map — DB- and network-free here;
    # proved on a real DB with fixture EDGAR clients in tests/pipeline/test_successor_cik.py
    monkeypatch.setattr(daily, "run_successor_scan", lambda **k: SuccessorScan(evaluated=True))
    # R8-A — the Form 4/A inventory reads the amendment TABLES — DB-free here; proved on a real DB in
    # tests/pipeline/test_insider_amendment_scan.py
    monkeypatch.setattr(daily, "run_amendment_scan", lambda **k: AmendmentScan(evaluated=True))

    # R8-B — the Form 4/A sweep writes decisions — DB-free here (recorded in `order`, so a test can prove it
    # runs BEFORE the per-thesis loop); proved on a real DB in tests/pipeline/test_insider_amendment_scan.py
    def _amend_sweep(**k):
        order.append("amendment_sweep")
        seen["amendment_sweep"] = k
        return SweepOutcome(ran=True)

    monkeypatch.setattr(daily, "run_amendment_sweep", _amend_sweep)

    def _radar(conn, **k):
        order.append("spac_radar")
        calls["spac_radar"] += 1
        return SimpleNamespace(summary="stub", errors=[])

    # the SPAC-radar leg is also allow_live-gated — stub it so a live pass doesn't run real radar code
    monkeypatch.setattr("radar.spac.run_spac_radar", _radar)

    def _sweep(conn, **k):
        order.append("shell_sweep")
        calls["shell_sweep"] += 1
        seen["shell_sweep"] = k  # capture kwargs -> assert the Monday-only reenrich wiring
        if sweep_raises:
            raise RuntimeError("shell sweep boom")
        return SimpleNamespace(summary="stub", errors=[])

    # the shell-sweep leg (allow_live-gated too, lazily imported from radar.shell_sweep)
    monkeypatch.setattr("radar.shell_sweep.run_shell_sweep", _sweep)

    def _run_daily(conn, **k):
        order.append("run_daily")
        calls["run_daily"] += 1
        return []

    monkeypatch.setattr(daily, "run_daily", _run_daily)

    def _bench(conn, **k):
        order.append("benchmarks")
        calls["benchmarks"] += 1
        seen["benchmarks"] = k  # capture kwargs → assert force_refresh + allow_live
        if bench_raises:
            raise RuntimeError("benchmarks boom")
        return []

    monkeypatch.setattr("pipeline.ingest_benchmarks.ingest_benchmarks", _bench)

    def _fund(conn, **k):
        order.append("fundamentals")
        calls["fundamentals"] += 1
        seen["fundamentals"] = k
        return []

    monkeypatch.setattr("pipeline.ingest_fundamentals.ingest_fundamentals", _fund)
    return order, calls, seen


def test_pass_SKIPS_the_refresh_leg_on_no_live(monkeypatch):
    # --no-live: a cache-only run can't refresh/record — the leg is gated off, but run_daily still runs
    _, calls, _ = _prime_pass(monkeypatch)
    out = daily.run_daily_pass(asof=_ASOF, allow_live=False, notifier=_Silent())
    assert calls["benchmarks"] == 0 and calls["fundamentals"] == 0  # neither invoked
    assert calls["run_daily"] == 1  # run_daily still runs (it owns its own no-live withhold)
    assert isinstance(out, daily.DailyPassOutcome)


def test_pass_RUNS_the_benchmark_leg_and_NO_bare_fundamentals_leg_when_live(monkeypatch):
    # Q17 zero-lag: the fundamentals refresh moved INTO run_daily (per thesis, after its ingest), so the
    # pass must not also pull companyfacts up front — that would pull twice, and a pass late
    _, calls, _ = _prime_pass(monkeypatch)
    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
    assert calls["benchmarks"] == 1 and calls["fundamentals"] == 0
    assert calls["run_daily"] == 1  # ...and run_daily still runs
    assert isinstance(out, daily.DailyPassOutcome)


def test_pass_refresh_leg_runs_BEFORE_run_daily(monkeypatch):
    # THE load-bearing property: fresh data is landed before the per-thesis calls read it. (The
    # fundamentals refresh lands inside run_daily, per thesis, before that thesis's call —
    # tests/pipeline/test_daily_fundamentals.py.)
    order, _, _ = _prime_pass(monkeypatch)
    daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
    assert order.index("benchmarks") < order.index("run_daily")
    assert "fundamentals" not in order


def test_pass_threads_catch_up_to_the_artifact_and_the_freeze_check(monkeypatch):
    """catch_up=True reaches the run-of-record artifact and the R4 assessor (`catch_up=True`). O5: the pass
    no longer turns the freeze predicate OFF for a catch-up (`freeze_check` is never passed — it stays on);
    the assessor leaves the catch-up's SKIPPED theses out and judges the rest by completed fetches. The
    default — the scheduled run and the Admin "Run daily now" — passes catch_up False.
    """
    _prime_pass(monkeypatch)
    seen_log: dict = {}
    seen_health: dict = {}

    def _log(results, **kw):
        seen_log.clear()
        seen_log.update(kw)
        return None  # fail-open shape, no file I/O

    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen_health.clear()
        seen_health.update(kw)
        return real_assess(results, **kw)

    monkeypatch.setattr(daily, "write_cron_run_log", _log)
    monkeypatch.setattr(daily, "assess_health", _assess)

    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent(), catch_up=True)
    assert out.catch_up is True
    assert seen_log["catch_up"] is True
    assert seen_health["catch_up"] is True and "freeze_check" not in seen_health

    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
    assert out.catch_up is False
    assert seen_log["catch_up"] is False
    assert seen_health["catch_up"] is False and "freeze_check" not in seen_health


def test_pass_benchmarks_failure_is_FAIL_OPEN(monkeypatch):
    # a benchmarks fault must NOT fail the cron: the per-thesis pass still runs
    _, calls, _ = _prime_pass(monkeypatch, bench_raises=True)
    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())  # must not raise
    assert calls["benchmarks"] == 1  # attempted (then raised inside its own try)
    assert calls["run_daily"] == 1  # ...and the per-thesis pass STILL ran
    assert isinstance(out, daily.DailyPassOutcome)


def test_pass_fundamentals_known_404s_are_ONE_quiet_count_and_fresh_errors_stay_loud(
    monkeypatch, capsys
):
    """X8 at the pass: the per-thesis refreshes are summarized ONCE after the loop — a name the negative
    cache answered (``known_absent``) folds into the line as a count; any other fundamentals error —
    including a FRESH 404 — still prints its own ERROR line.
    """
    from pipeline.ingest_fundamentals import NameResult as FundResult

    _prime_pass(monkeypatch)
    sid = uuid.uuid4()
    res = [
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="T",
            recorded=True,
            fundamentals=(
                FundResult("AAA", sid, appended=2),
                FundResult(
                    "NOCF",
                    sid,
                    error="companyfacts/CIK1.json: (HTTP 404, cached)",
                    known_absent=True,
                ),
                FundResult("BAD", sid, error="Client error '404 Not Found' for url x"),
            ),
        )
    ]
    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: res)
    daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
    out = capsys.readouterr().out
    assert "fundamentals refresh: +2 quarters, 3 names, 1 known without companyfacts" in out
    assert "fundamentals refresh ERROR: Client error '404 Not Found'" in out
    assert "ERROR: companyfacts/CIK1.json" not in out


# --- the SPAC shell-sweep leg in run_daily_pass (PR-2): before-radar, fail-open, Monday reenrich ---


def test_pass_runs_shell_sweep_BEFORE_the_radar_leg(monkeypatch):
    # THE load-bearing ordering: the sweep commits its enrichments (per-CIK, inside enrich_for_ciks)
    # before the radar's run-start known_shell_ciks read — so the SAME night's radar collects a
    # freshly-admitted shell's 8-K/proxy/25 events instead of dropping them for one more day.
    order, calls, _ = _prime_pass(monkeypatch)
    daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
    assert calls["shell_sweep"] == 1 and calls["spac_radar"] == 1
    assert order.index("shell_sweep") < order.index("spac_radar")


def test_pass_shell_sweep_failure_is_FAIL_OPEN_radar_still_runs(monkeypatch):
    # a sweep fault must NOT skip the radar leg (separate try per leg) and must NOT fail the cron
    _, calls, _ = _prime_pass(monkeypatch, sweep_raises=True)
    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())  # must not raise
    assert calls["shell_sweep"] == 1  # attempted (then raised inside its own try)
    assert calls["spac_radar"] == 1  # ...the radar leg STILL ran
    assert isinstance(out, daily.DailyPassOutcome)


def test_pass_skips_shell_sweep_on_no_live(monkeypatch):
    # --no-live: a cache-only run can't refresh — the sweep (like every universe leg) is gated off;
    # `python -m pipeline.spac_sweep` stays the manual cache-only path
    _, calls, _ = _prime_pass(monkeypatch)
    daily.run_daily_pass(asof=_ASOF, allow_live=False, notifier=_Silent())
    assert calls["shell_sweep"] == 0 and calls["spac_radar"] == 0


def test_pass_shell_sweep_reenriches_on_mondays_only(monkeypatch):
    # the weekly de-SPAC re-enrich gate is a WEEKDAY (the cron fires Mon-Fri — a weekend gate would
    # never fire): a Monday asof threads reenrich=True, any other weekday False.
    _, _, seen = _prime_pass(monkeypatch)
    monday, tuesday = date(2026, 8, 24), date(2026, 8, 25)
    assert (monday.weekday(), tuesday.weekday()) == (0, 1)  # pin the fixture dates themselves
    daily.run_daily_pass(asof=monday, allow_live=True, notifier=_Silent())
    assert seen["shell_sweep"]["reenrich"] is True
    daily.run_daily_pass(asof=tuesday, allow_live=True, notifier=_Silent())
    assert seen["shell_sweep"]["reenrich"] is False


def test_pass_benchmarks_FORCE_REFRESH(monkeypatch):
    """The load-bearing freshness KWARGS the benchmark leg is written with, asserted where a stub that
    swallowed `**k` couldn't: force_refresh=True (+ allow_live=True) — the recurring path bypasses a stale
    cache HIT and re-pulls fresh SPY/IWM bars, else the tape freezes and benchmark_rs degrades silently (the
    #72 lesson). (The fundamentals refresh carries no flag at all: it rides the per-thesis RECURRING client,
    on which companyfacts is filing-gated by key class — tests/pipeline/test_daily_fundamentals.py.)
    """
    _, _, seen = _prime_pass(monkeypatch)
    daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    # benchmarks: the recurring fetch force-refreshes past a stale cache hit
    assert seen["benchmarks"].get("force_refresh") is True
    assert seen["benchmarks"].get("allow_live") is True
    # D1: the recurring leg never asks for the one-shot backfill's backdated stamp (recorded_at = now)
    assert not seen["benchmarks"].get("historical_backfill", False)


# --- G1: the recurring pass builds its EDGAR client with the RECURRING TTL (daytime warmth) ----------


class _RecordingEdgar:
    """Constructor-compatible fake that RECORDS the kwargs it was built with (the _DeadEdgar idiom from
    the shell-sweep tests). live_fetches / live_completed are instance attrs so run_daily's freeze-counter
    reads work.
    """

    seen: list[dict] = []

    def __init__(self, **kw) -> None:
        self.live_fetches = 0
        self.live_completed = 0
        _RecordingEdgar.seen.append(kw)


def test_run_daily_builds_its_edgar_client_with_the_RECURRING_TTL(db, monkeypatch):
    """G1 — THE WIRING: the per-thesis client on the RECURRING path must be built with the RECURRING TTL,
    so a daytime read of a company's submissions index can never leave the night's pass serving it from
    cache (blind to everything filed after the daytime fetch). Asserted against the CONSTANT, never a
    literal: the value is five MINUTES rather than zero because the three filing legs re-read one company's
    index milliseconds apart, and that trade-off is measured in ``tests/ingest/test_edgar_client.py`` and
    documented at the constant. The client is constructed INSIDE the per-thesis loop, so this needs a real
    thesis; the ingest itself is stubbed (no network)."""
    _no_network(monkeypatch)
    _thesis(db, "T")
    _RecordingEdgar.seen = []
    monkeypatch.setattr(daily, "EdgarClient", _RecordingEdgar)

    daily.run_daily(db, asof=_ASOF, allow_live=True)

    assert (
        len(_RecordingEdgar.seen) == 1
    )  # one client per thesis (the freeze counter is per-thesis)
    assert _RecordingEdgar.seen[0]["cache_ttl_s"] == RECURRING_CACHE_TTL_S
    assert _RecordingEdgar.seen[0]["allow_live"] is True  # the other kwargs are unchanged


# --- G4: a failed BENCHMARK refresh pages (the shared SPY/IWM tape feeding benchmark_rs) -------------


def test_assess_health_pages_a_failed_benchmark_refresh():
    """An otherwise-CLEAN run with a benchmark pull that failed is unhealthy: the calls that night were
    computed against a stale shared input, which the run's own counts cannot show."""
    h = daily.assess_health([], asof=_ASOF, allow_live=True, benchmark_errors=1)
    assert h is not None
    assert h.benchmark_errors == 1 and h.benchmark_leg_failed is False
    assert "benchmark refresh error" in h.label and "stale tape" in h.label


def test_assess_health_pages_a_benchmark_LEG_that_never_ran():
    """The other shape, reported DISTINCTLY: the leg raised before producing any per-benchmark result, so
    nothing refreshed at all — not "1 of 2 benchmarks failed"."""
    h = daily.assess_health([], asof=_ASOF, allow_live=True, benchmark_leg_failed=True)
    assert h is not None
    assert h.benchmark_leg_failed is True and h.benchmark_errors == 0
    assert "BENCHMARK REFRESH LEG FAILED" in h.label


def test_assess_health_stays_silent_when_the_benchmark_legs_were_clean():
    """No new false page: the defaults change nothing for a healthy run (loudness marks the exception)."""
    assert daily.assess_health([], asof=_ASOF, allow_live=True) is None
    assert (
        daily.assess_health(
            [], asof=_ASOF, allow_live=True, benchmark_errors=0, benchmark_leg_failed=False
        )
        is None
    )


def test_pass_threads_a_benchmark_FAULT_to_the_artifact_and_the_health_page(monkeypatch):
    """G4 END TO END in the pass: the benchmarks leg raising is still FAIL-OPEN (the loop still runs,
    the pass returns), but the fault now reaches BOTH the run-of-record artifact and assess_health — and
    it is carried on the outcome, which is what the Admin "run now" job rebuilds its payload from.
    """
    _prime_pass(monkeypatch, bench_raises=True)
    seen_log: dict = {}
    seen_health: dict = {}

    def _log(results, **kw):
        seen_log.clear()
        seen_log.update(kw)
        return None  # fail-open shape, no file I/O

    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen_health.clear()
        seen_health.update(kw)
        return real_assess(results, **kw)

    monkeypatch.setattr(daily, "write_cron_run_log", _log)
    monkeypatch.setattr(daily, "assess_health", _assess)

    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    assert seen_log["benchmark_leg_failed"] is True and seen_log["benchmark_errors"] == 0
    assert seen_health["benchmark_leg_failed"] is True
    assert out.benchmark_leg_failed is True and out.benchmark_errors == 0


def test_pass_counts_INDIVIDUAL_benchmark_errors_without_failing_the_leg(monkeypatch):
    """The partial shape: the leg RAN and returned results, one of which carried an error. That is a count,
    not a leg failure — the page must say which (a stale half-tape vs no refresh at all)."""
    _prime_pass(monkeypatch)
    monkeypatch.setattr(
        "pipeline.ingest_benchmarks.ingest_benchmarks",
        lambda conn, **k: [
            SimpleNamespace(bars_appended=3, error=None, bars_restated=0, bars_withheld=0),
            SimpleNamespace(bars_appended=0, error="yahoo 502", bars_restated=0, bars_withheld=0),
        ],
    )
    seen_health: dict = {}
    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen_health.clear()
        seen_health.update(kw)
        return real_assess(results, **kw)

    monkeypatch.setattr(daily, "assess_health", _assess)

    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    assert out.benchmark_errors == 1 and out.benchmark_leg_failed is False
    assert seen_health["benchmark_errors"] == 1


def test_pass_on_no_live_reports_NO_benchmark_fault(monkeypatch):
    """A --no-live pass SKIPS the refresh legs entirely, so it must report them clean — a skipped leg is
    not a failed one (the counts are initialized before the allow_live gate for exactly this)."""
    _prime_pass(monkeypatch, bench_raises=True)  # would raise IF the leg ran
    out = daily.run_daily_pass(asof=_ASOF, allow_live=False, notifier=_Silent())
    assert out.benchmark_errors == 0 and out.benchmark_leg_failed is False


# --- G5a: the price-tape recency monitor (judgment, diff, page) ---------------------------------------


def _ingest_returning(names):
    """Stub the cron's ingest step so it returns controlled NameResults — the `_no_network` sibling: this
    one hands back per-name outcomes instead of an empty list."""
    return lambda *a, **k: list(names)


def _nr(**kw) -> NameResult:
    base = dict(ticker="AAA", security_id=uuid.uuid4(), form4_appended=0, price_bars_appended=0)
    return NameResult(**{**base, **kw})


def _stale(
    ticker, *, edge=None, sid=None, kind="price", closed_at=None, renamed=False, edgar_tickers=None
):
    return daily.StaleTape(
        ticker=ticker,
        security_id=sid or uuid.uuid4(),
        edge=edge,
        kind=kind,
        closed_at=closed_at,
        renamed=renamed,
        edgar_tickers=edgar_tickers,
    )


def _lbl(label, *, kind="price"):
    """A newly-stale (kind, label) entry — what the diff hands the page."""
    return StaleFeedLabel(kind=kind, label=label)


def _thesis_result_with(stale):
    return [
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(), name="T", recorded=True, tape_stale=tuple(stale)
        )
    ]


def test_run_daily_judges_tape_staleness_against_the_runs_ASOF(db, monkeypatch):
    """The judgment lives in run_daily because this is the layer holding the run's asof — time stays a
    parameter, never an ambient clock inside the ingest unit. One dead tape, one fresh: only the dead one is
    reported, with its real edge."""
    dead = _nr(ticker="DEAD", tape_edge=_ASOF - timedelta(days=40))
    alive = _nr(ticker="ALIVE", tape_edge=_ASOF)
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning([dead, alive]))
    _thesis(db, "T")

    out = daily.run_daily(db, asof=_ASOF, allow_live=True)

    assert [s.ticker for s in out[0].tape_stale] == ["DEAD"]
    assert out[0].tape_stale[0].edge == _ASOF - timedelta(days=40)


def test_run_daily_does_NOT_judge_tape_staleness_on_a_NO_LIVE_pass(db, monkeypatch):
    """A cache-only pass evaluates nothing: it cannot see a tape's current edge honestly, its call is
    withheld anyway, and — the load-bearing part — it must never become the baseline the next live pass
    diffs against, or a hand-run dev pass would silence the next real page."""
    monkeypatch.setattr(
        daily, "ingest_thesis", _ingest_returning([_nr(ticker="DEAD", tape_edge=None)])
    )
    _thesis(db, "T")

    out = daily.run_daily(db, asof=_ASOF, allow_live=False)

    assert out[0].tape_stale == ()


def test_run_daily_tape_monitor_is_OFF_at_stale_days_zero(db, monkeypatch):
    """The documented off-switch (ALPHADECK_TAPE_STALE_DAYS=0) reaches the cron, not just the predicate —
    and each feed's switch is INDEPENDENT, so turning the price monitor off leaves the fund one running.
    """
    monkeypatch.setattr(
        daily, "ingest_thesis", _ingest_returning([_nr(ticker="DEAD", tape_edge=None)])
    )
    monkeypatch.setattr(
        daily,
        "get_settings",
        lambda: SimpleNamespace(tape_stale_days=0, fund_shares_stale_days=7, image_sha=None),
    )
    _thesis(db, "T")

    out = daily.run_daily(db, asof=_ASOF, allow_live=True)

    assert out[0].tape_stale == ()  # the price monitor is off; the name is not an ETF sleeve either


def _delisting(form: str, filed: date) -> DelistingFiling:
    return DelistingFiling(form=form, filed=filed, accession=f"ACC-{form}-{filed}")


def test_run_daily_classifies_a_stale_tape_CLOSED_RENAMED_or_REPAIR(db, monkeypatch):
    """G5b/T1 — run_daily classifies each stopped tape from the EDGAR facts on its NameResult, with the
    proximity window from Settings: a delisting form 2 days after the last bar on a company EDGAR lists
    none → CLOSED (still reported, never dropped, #9); an OLD form (400 days before) on a company EDGAR
    lists under another symbol → a RENAMED repair row (the VRME shape); no evidence → a plain repair row.
    Told apart deterministically by the forms and the EDGAR list, never a guess (#3)."""
    edge = _ASOF - timedelta(days=40)
    closed = _nr(
        ticker="GONE",
        tape_edge=edge,
        delisting_filings=(_delisting("25-NSE", edge + timedelta(days=2)),),
        edgar_tickers=(),
    )
    renamed = _nr(
        ticker="OLDSYM",
        tape_edge=edge,
        delisting_filings=(_delisting("25-NSE", edge - timedelta(days=400)),),
        edgar_tickers=("NEWSYM",),
    )
    repair = _nr(ticker="DEAD", tape_edge=edge)  # no EDGAR facts at all
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning([closed, renamed, repair]))
    _thesis(db, "T")

    out = daily.run_daily(db, asof=_ASOF, allow_live=True)

    rows = {s.ticker: s for s in out[0].tape_stale}
    assert rows["GONE"].closed is True and rows["GONE"].closed_at == edge + timedelta(days=2)
    assert rows["OLDSYM"].closed is False and rows["OLDSYM"].renamed is True
    assert rows["OLDSYM"].edgar_tickers == ("NEWSYM",)
    assert rows["DEAD"].closed is False and rows["DEAD"].renamed is False
    assert set(rows) == {"GONE", "OLDSYM", "DEAD"}  # nothing dropped from the inventory (#9)


def test_run_daily_reads_the_proximity_window_from_SETTINGS(db, monkeypatch):
    """The window is a setting, not a constant: a form filed 40 days after the last bar is NOT a close
    with the AFTER side set to 30, and IS one at the default 45."""
    edge = _ASOF - timedelta(days=60)
    gone = _nr(
        ticker="GONE",
        tape_edge=edge,
        delisting_filings=(_delisting("15-12G", edge + timedelta(days=40)),),
        edgar_tickers=(),
    )
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning([gone]))
    _thesis(db, "T")

    def _settings(after):
        return SimpleNamespace(
            tape_stale_days=5,
            fund_shares_stale_days=0,
            tape_closed_before_days=30,
            tape_closed_after_days=after,
            image_sha=None,
        )

    monkeypatch.setattr(daily, "get_settings", lambda: _settings(30))
    assert daily.run_daily(db, asof=_ASOF, allow_live=True)[0].tape_stale[0].closed is False
    monkeypatch.setattr(daily, "get_settings", lambda: _settings(45))
    assert daily.run_daily(db, asof=_ASOF, allow_live=True)[0].tape_stale[0].closed is True


def test_run_daily_reads_each_window_FORM_25s_CLASS_through_the_THESIS_client(db, monkeypatch):
    """Q6 — THE WIRING: run_daily hands the stale classification its per-thesis EDGAR client, so a Form 25
    near a stopped tape is read for its class. REAL rows (prod's cached submissions, 2026-10-17) and the
    REAL documents (tests/fixtures/edgar/form25_cache — provenance in tests/ingest/test_form25.py), the
    client pointed at that cache with live pulls OFF: ACHR's 25-NSE removed its WARRANTS, so its stopped
    tape stays a loud repair row; RMAX's removed its Class A common, so RMAX reads closed. ACHR's stop is a
    hypothetical one on its real record (it still trades)."""
    cache = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form25_cache"
    monkeypatch.setattr(
        daily, "EdgarClient", lambda **kw: EdgarClient(cache_dir=cache, allow_live=False)
    )
    achr = _nr(
        ticker="ACHR",
        tape_edge=date(2026, 10, 16),
        delisting_filings=(
            DelistingFiling(
                form="25-NSE",
                filed=date(2026, 9, 16),
                accession="0000876661-26-000755",
                primary_doc="xslF25X02/primary_doc.xml",
                cik="0001824502",
            ),
        ),
        edgar_tickers=("ACHR",),
    )
    rmax = _nr(
        ticker="RMAX",
        tape_edge=date(2026, 8, 24),
        delisting_filings=(
            DelistingFiling(
                form="15-12G",
                filed=date(2026, 9, 4),
                accession="0001104659-26-105182",
                primary_doc="tm2623567d11_1512g.htm",
                cik="0001581091",
            ),
            DelistingFiling(
                form="25-NSE",
                filed=date(2026, 8, 25),
                accession="0000876661-26-000703",
                primary_doc="xslF25X02/primary_doc.xml",
                cik="0001581091",
            ),
        ),
        edgar_tickers=(),
    )
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning([achr, rmax]))
    _thesis(db, "T")

    rows = {
        s.ticker: s
        for s in daily.run_daily(db, asof=date(2026, 10, 21), allow_live=True)[0].tape_stale
    }

    assert rows["ACHR"].closed is False and rows["ACHR"].renamed is False
    assert rows["RMAX"].closed_at == date(2026, 8, 25)


def test_assess_health_pages_a_NEWLY_stale_tape_and_names_it():
    """It pages — every price-driven signal for that name is dark until the tape resumes — and the page
    NAMES the tape, because a bare "a tape went stale" is unactionable."""
    h = daily.assess_health(
        [], asof=_ASOF, allow_live=True, tape_stale_new=(_lbl("AAA"), _lbl("BBB"))
    )
    assert h is not None
    assert h.tape_stale_new == (_lbl("AAA"), _lbl("BBB"))
    assert "newly STALE" in h.label and "AAA, BBB" in h.label
    assert "not a cron error" in h.label  # a FEED gap, and the page says so


def test_assess_health_pages_EACH_FEED_KIND_on_its_own_line_with_its_own_repair():
    """F1: a price tape and a fund-shares tape stop the same silent way but are fixed differently, so one
    merged line would give half the names the wrong instruction. One line per feed, each with its own
    count, names and advice — and the fund line must NOT tell the operator to set a price symbol."""
    h = daily.assess_health(
        [],
        asof=_ASOF,
        allow_live=True,
        tape_stale_new=(_lbl("AAA"), _lbl("ETF1", kind="fund_shares"), _lbl("BBB")),
    )
    assert h is not None
    assert "2 price tape(s) newly STALE — AAA, BBB" in h.label
    assert "1 fund-shares tape(s) newly STALE — ETF1" in h.label
    price_line, fund_line = h.label.split("1 fund-shares")
    assert "price symbol" in price_line and "price symbol" not in fund_line
    assert "MASSIVE_API_KEY" in fund_line


def test_assess_health_is_SILENT_when_no_tape_became_stale():
    """Inverse loudness: the known-dead tapes of every previous night must not re-page, so only the DIFF
    reaches here and a night with nothing new is silent."""
    assert daily.assess_health([], asof=_ASOF, allow_live=True, tape_stale_new=()) is None


def test_pass_pages_a_NEWLY_stale_tape_and_stays_quiet_on_a_KNOWN_one(monkeypatch, tmp_path):
    """THE DIFF, across three passes through the REAL artifact writer (run-log home redirected to tmp):
    pass 1 finds a dead tape and pages it; pass 2 finds the SAME one and pages NOTHING; pass 3 finds an
    additional one and pages ONLY the new name. This is what stops a handful of known-dead tapes from crying
    wolf every night forever. The diff is keyed on kind + security_id, so the SAME ids are reused across
    passes.
    """
    a, b = uuid.uuid4(), uuid.uuid4()
    seen: list[tuple[str, ...]] = []
    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen.append(tuple(x.label for x in (kw.get("tape_stale_new") or ())))
        return real_assess(results, **kw)

    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setattr(daily, "assess_health", _assess)

    def _pass(stale):
        monkeypatch.setattr(daily, "run_daily", lambda conn, **k: _thesis_result_with(stale))
        return daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    out1 = _pass([_stale("AAA", sid=a)])
    out2 = _pass([_stale("AAA", sid=a)])
    out3 = _pass([_stale("AAA", sid=a), _stale("BBB", sid=b)])

    assert seen[0] == ("AAA",)  # the first evaluated pass: the inventory, paged once
    assert seen[1] == ()  # the SAME dead tape the next night: silent
    assert seen[2] == ("BBB",)  # only the genuinely new one
    assert out1.tape_evaluated and out2.tape_evaluated and out3.tape_evaluated
    assert out3.tape_stale_new == (StaleFeedLabel(kind="price", label="BBB"),)


def test_pass_does_NOT_page_a_CLOSED_delisted_tape(monkeypatch, tmp_path):
    """G5b/#7 — a name that stopped trading (delisted / acquired / deregistered) is an EXPECTED event, not a
    feed gap: it must NEVER page as newly-stale (paging "set the vendor price symbol" for it would be wrong
    advice). A plain stale-repair tape in the SAME pass DOES page. The closed one is still in the inventory
    (the artifact carries it, the panel shows it quietly) — only the loud newly-stale page skips it.
    """
    a, b = uuid.uuid4(), uuid.uuid4()
    seen: list[tuple[str, ...]] = []
    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen.append(tuple(x.label for x in (kw.get("tape_stale_new") or ())))
        return real_assess(results, **kw)

    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setattr(daily, "assess_health", _assess)
    monkeypatch.setattr(
        daily,
        "run_daily",
        lambda conn, **k: _thesis_result_with(
            [_stale("REPAIR", sid=a), _stale("GONE", sid=b, closed_at=date(2026, 4, 3))]
        ),
    )

    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    assert seen[0] == ("REPAIR",)  # only the feed gap reaches the page
    assert out.tape_stale_new == (StaleFeedLabel(kind="price", label="REPAIR"),)


def test_pass_pages_ONCE_when_a_CLOSED_tape_is_RECLASSIFIED_as_a_repair(monkeypatch, tmp_path):
    """T1 — the VRME night. Pass 1 (the old rule) wrote VRME as CLOSED, so it never paged. Pass 2 (the
    proximity rule) reclassifies the same security as a renamed REPAIR row: that is news the operator was
    never told, so it must page — exactly ONCE. Pass 3 sees the same repair row and stays quiet. Keyed on
    kind + security_id through the REAL artifact writer: without the baseline skipping closed rows, pass
    1's silenced row would count as "already told" and the reclassification would never page.
    """
    vrme, other = uuid.uuid4(), uuid.uuid4()
    seen: list[tuple[str, ...]] = []
    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen.append(tuple(x.label for x in (kw.get("tape_stale_new") or ())))
        return real_assess(results, **kw)

    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setattr(daily, "assess_health", _assess)

    def _pass(stale):
        monkeypatch.setattr(daily, "run_daily", lambda conn, **k: _thesis_result_with(stale))
        return daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    known = _stale("KNOWN", sid=other)  # a repair row present every night: never re-pages
    _pass([known, _stale("VRME", sid=vrme, closed_at=date(2025, 6, 23))])
    renamed = _stale("VRME", sid=vrme, renamed=True, edgar_tickers=("OPNW",))
    out2 = _pass([known, renamed])
    out3 = _pass([known, renamed])

    assert seen[0] == ("KNOWN",)  # night 1: VRME was (wrongly) closed — silent
    assert seen[1] == ("VRME",)  # night 2: reclassified → news, paged once
    assert seen[2] == ()  # night 3: the same repair row — quiet
    assert out2.tape_stale_new == (StaleFeedLabel(kind="price", label="VRME"),)
    assert out3.tape_stale_new == ()


def test_pass_pages_the_SAME_security_again_when_its_OTHER_feed_stops(monkeypatch, tmp_path):
    """F1, the reason the diff key carries the KIND: one name's price tape dies, and later its fund-shares
    sampling dies too. The second is news the operator has not been told, with a different repair — keyed
    on the id alone it would be silently swallowed as "already known"."""
    sid = uuid.uuid4()
    seen: list[tuple[tuple[str, str], ...]] = []
    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen.append(tuple((x.kind, x.label) for x in (kw.get("tape_stale_new") or ())))
        return real_assess(results, **kw)

    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setattr(daily, "assess_health", _assess)

    def _pass(stale):
        monkeypatch.setattr(daily, "run_daily", lambda conn, **k: _thesis_result_with(stale))
        return daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    _pass([_stale("AAA", sid=sid)])
    out2 = _pass([_stale("AAA", sid=sid), _stale("AAA", sid=sid, kind="fund_shares")])

    assert seen[0] == (("price", "AAA"),)
    assert seen[1] == (("fund_shares", "AAA"),)  # the SAME security, the OTHER feed: news
    assert out2.tape_stale_new == (StaleFeedLabel(kind="fund_shares", label="AAA"),)


def test_pass_on_a_FIRST_evaluated_run_pages_the_WHOLE_inventory(monkeypatch, tmp_path):
    """The operator's decision for the deploy night: with no previous evaluated artifact
    (``previous_stale_tapes() -> None``, deliberately distinct from an empty set) every currently-stale tape
    is news, so the first pass hands over the whole inventory ONCE rather than starting silent."""
    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)  # an EMPTY run home
    monkeypatch.setattr(
        daily,
        "run_daily",
        lambda conn, **k: _thesis_result_with([_stale("AAA"), _stale("BBB")]),
    )

    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    assert sorted(x.label for x in out.tape_stale_new) == ["AAA", "BBB"]


def test_pass_on_no_live_records_tape_evaluated_FALSE(monkeypatch):
    """A --no-live pass must record that it did not look, so it can never serve as the diff baseline."""
    _prime_pass(monkeypatch)
    out = daily.run_daily_pass(asof=_ASOF, allow_live=False, notifier=_Silent())
    assert out.tape_evaluated is False and out.tape_stale_new == ()


def test_report_lists_stale_tapes_deduped_and_only_when_there_are_any(capsys):
    """stdout is the first place an operator looks at a cron run. One dead tape held by TWO theses is ONE
    line (deduped across theses, like the page and the panel); a healthy universe says nothing about tapes.
    """
    shared = _stale("DEAD", edge=date(2026, 4, 1))
    two_theses = [
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(), name="T1", recorded=True, tape_stale=(shared,)
        ),
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(), name="T2", recorded=True, tape_stale=(shared,)
        ),
    ]
    daily._report(two_theses)
    out = capsys.readouterr().out
    assert out.count("DEAD: last bar 2026-04-01") == 1  # deduped across the two holders

    daily._report([daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=True)])
    assert "STALE PRICE TAPES" not in capsys.readouterr().out  # quiet when there are none


def test_report_prints_a_CLOSED_tape_in_the_QUIET_block_not_the_loud_stale_one(capsys):
    """G5b/#7 — a delisted name prints in the quiet 'closed tapes' block (no repair needed), NEVER in the
    loud 'STALE PRICE TAPES' block whose advice ('set the vendor price symbol') would be wrong for it. A
    plain stale tape in the same run stays loud."""
    closed = _stale("GONE", edge=date(2026, 4, 1), closed_at=date(2026, 4, 3))
    repair = _stale("DEAD", edge=date(2026, 4, 1))
    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(), name="T", recorded=True, tape_stale=(closed, repair)
            )
        ]
    )
    out = capsys.readouterr().out
    # the repair one is loud; the closed one is NOT in the loud block
    loud = out.split("closed tapes")[0]
    assert "STALE PRICE TAPES" in loud and "DEAD: last bar 2026-04-01" in loud
    assert "GONE" not in loud
    # ...it is in the quiet closed block, with its delisting date
    assert "closed tapes" in out and "GONE: last bar 2026-04-01 · delisting filed 2026-04-03" in out


def test_report_marks_a_RENAMED_repair_row_with_EDGARs_symbols(capsys):
    """T1 — a renamed row stays in the LOUD stale block (it is a repair) and names where EDGAR lists the
    company now, so the operator's next step is on the line; a plain repair row carries no marker.
    """
    renamed = _stale("VRME", edge=date(2026, 10, 6), renamed=True, edgar_tickers=("OPNW",))
    plain = _stale("WONDF", edge=None, edgar_tickers=())
    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(), name="T", recorded=True, tape_stale=(renamed, plain)
            )
        ]
    )
    out = capsys.readouterr().out
    assert "STALE PRICE TAPES" in out
    assert "VRME: last bar 2026-10-06 · renamed — EDGAR lists OPNW" in out
    # no rename marker on the other row — Q8 names it UNPRICEABLE instead (never priced, EDGAR lists no
    # symbol: the WONDF shape), still in the loud block until the operator ratifies it
    assert "WONDF: last bar never · UNPRICEABLE" in out
    assert "WONDF: last bar never · renamed" not in out
    assert "closed tapes" not in out


# --- F2: the transition baseline is the record AT OR BEFORE this as-of (no same-day re-notify) --------


def test_a_same_day_second_pass_does_NOT_re_notify_the_nights_transition(
    db, security_id, monkeypatch
):
    """THE BUG THIS CLOSES (MEASURED on dev 2026-09-13): a second pass on the same day — the Admin "Run
    daily now" button after the nightly, or the sidecar's retry/catch-up sequence — re-pushed the night's
    transitions to Slack, because the baseline was the latest row with `asof < today` (YESTERDAY) even
    though a row for TODAY already existed. Here: the morning pass pages incubating → warming, and the
    evening pass on unchanged facts pages NOTHING and appends nothing (count the table, both)."""
    _no_network(monkeypatch)
    tid = _thesis(db, "Nuclear", members=[("DEVCO", security_id)])
    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)  # the prior night: INCUBATING
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")  # the P-buy that warms it
    db.commit()

    morning = _Capture()
    daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=morning)
    assert [(e.from_state, e.to_state) for e in morning.events] == [("incubating", "warming")]
    rows_after_morning = len(_calls(db, tid))

    evening = _Capture()
    by = {
        r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=evening)
    }
    assert evening.events == []  # the same move is announced ONCE
    assert by[tid].transition is None
    assert len(_calls(db, tid)) == rows_after_morning  # ...and nothing was appended either


def test_a_genuine_intraday_MOVE_still_notifies_against_the_same_day_row(
    db, security_id, monkeypatch
):
    """The other half: quieting the re-push must not mute a REAL move later the same day. The evening
    pass pages warming → armed — measured against the MORNING row, not against yesterday's incubating
    one, so the arrow describes the move the operator has not yet been told about."""
    _no_network(monkeypatch)
    _thesis(db, "Nuclear", members=[("DEVCO", security_id)])
    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)  # the prior night: INCUBATING
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    db.commit()

    morning = _Capture()
    daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=morning)
    assert [(e.from_state, e.to_state) for e in morning.events] == [("incubating", "warming")]

    # the evening pass, SAME as-of: the card has moved on (the assembler stubbed, the house pattern)
    monkeypatch.setattr(
        daily, "call_for_thesis", lambda conn, thid, asof, **k: _armed_card(thid, security_id, asof)
    )
    evening = _Capture()
    daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=evening)

    assert len(evening.events) == 1
    evt = evening.events[0]
    assert (evt.from_state, evt.to_state) == ("warming", "armed")  # NOT incubating → armed


def test_a_catch_up_for_a_PAST_asof_compares_to_that_nights_predecessor(
    db, security_id, monkeypatch
):
    """`<=` can never reach FORWARD. With rows for Jun 5 (incubating) and Jun 11 (warming), a catch-up
    pass for Jun 10 compares against Jun 5 — that night's predecessor — and still pages the move. If the
    baseline could see the later row it would read warming → warming and say nothing."""
    _no_network(monkeypatch)
    _thesis(db, "Nuclear", members=[("DEVCO", security_id)])
    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)  # INCUBATING
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    db.commit()
    daily.run_daily(db, asof=date(2026, 6, 11), allow_live=True, notifier=_Capture())  # WARMING

    cap = _Capture()
    daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=cap)  # Jun 10, between the two

    assert len(cap.events) == 1
    assert (cap.events[0].from_state, cap.events[0].to_state) == ("incubating", "warming")


# --- F4: the exit code answers "does tonight have a record?", not "did anything go wrong" ------------


def _outcome_with(results):
    """A finished pass carrying `results` — the shape `main` reads for its exit decision."""
    now = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)
    return daily.DailyPassOutcome(
        results=results,
        asof=_ASOF,
        allow_live=True,
        started_at=now,
        finished_at=now,
        log_path=None,
    )


def _exit_code(monkeypatch, results) -> int:
    """Run the CLI over a stubbed pass and return the process exit code (0 = no SystemExit)."""
    monkeypatch.setattr(daily, "run_daily_pass", lambda **kw: _outcome_with(results))
    _tonight(monkeypatch, _ASOF)  # the night's own pass (C3 refuses a past --asof)
    try:
        daily.main(["--asof", _ASOF.isoformat()])
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _res(**kw):
    return daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", **kw)


def test_exit_0_when_the_pass_recorded_for_SOME_thesis(monkeypatch):
    """The headline: a night that recorded is a night with a record, whatever else went wrong. One
    appended row beside an errored thesis exits 0 — before F4 that cost the sidecar a 20-minute pause
    and a retry the guard immediately no-opped, while the log read "run FAILED" over a recorded night.
    An UNCHANGED-only night (recorded False, no row — the common healthy-quiet outcome) counts too.
    """
    assert _exit_code(monkeypatch, [_res(recorded=True), _res(recorded=None, error="boom")]) == 0
    assert _exit_code(monkeypatch, [_res(recorded=False), _res(recorded=False)]) == 0


def test_exit_1_when_NOTHING_recorded_for_any_thesis(monkeypatch):
    """The retry's real trigger: theses existed and the call step reached none of them, so the night has
    no call-of-record and a re-run is exactly the right move."""
    assert (
        _exit_code(monkeypatch, [_res(recorded=None, error="boom"), _res(recorded=None, error="x")])
        == 1
    )
    # the other total-failure shape: withheld for a REAL reason (not by request)
    assert (
        _exit_code(monkeypatch, [_res(recorded=None, withheld_reason="total ingest failure")]) == 1
    )


def test_exit_0_on_a_no_live_pass_that_withheld_everything(monkeypatch):
    """`python -m pipeline.daily --no-live` withholds every call BY DESIGN (the recording gate, Source
    A) and is a documented operator command — it must keep exiting 0. The cron never passes --no-live,
    so this exemption can never hide a real miss from the sidecar."""
    withheld = [_res(recorded=None, withheld_reason="no-live") for _ in range(3)]
    assert _exit_code(monkeypatch, withheld) == 0


def test_exit_0_when_there_are_NO_theses(monkeypatch):
    """A pass over an empty roster recorded nothing because there was nothing to record (a fresh
    install). Exiting non-zero would make the sidecar retry every night forever on a working stack.
    """
    assert _exit_code(monkeypatch, []) == 0


def test_the_exit_predicate_and_the_ARTIFACT_guard_agree_where_they_must(monkeypatch):
    """Two functions answer "did the call step run for some thesis" — `_failed_to_record` over live
    results (the CLI's exit code) and `cron_run_log._recorded_for_some_thesis` over a parsed artifact
    (the --catch-up guard). On a LIVE pass with theses they must agree, or the sidecar would retry a
    night the guard then refuses to re-run (or skip one it would have).

    They diverge on exactly two shapes, deliberately, because their fail-open directions differ: an
    EMPTY roster and a --no-live pass are not failures for the exit code, while the guard credits
    neither as evidence the night ran (it errs toward RUNNING — a repeated run is safe, a skipped one
    is the silent gap). Pinned here so the divergence stays a decision rather than a drift."""
    now = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)

    def guard_says_recorded(results, *, allow_live=True):
        payload = cron_run_log.build_run_payload(
            results, asof=_ASOF, allow_live=allow_live, started_at=now, finished_at=now
        )
        return cron_run_log._recorded_for_some_thesis(payload)

    for results in (
        [_res(recorded=True), _res(recorded=None, error="boom")],  # partial
        [_res(recorded=False), _res(recorded=False)],  # unchanged-only
        [_res(recorded=None, error="boom")],  # total failure
    ):
        assert daily._failed_to_record(results) is not guard_says_recorded(results)

    empty: list = []
    assert daily._failed_to_record(empty) is False and guard_says_recorded(empty) is False
    no_live = [_res(recorded=None, withheld_reason="no-live")]
    assert daily._failed_to_record(no_live) is False
    assert guard_says_recorded(no_live, allow_live=False) is False


# --- F1: the fund-shares feed judged beside the price tape, with its own threshold ------------------


def test_run_daily_judges_BOTH_feeds_against_the_runs_asof(db, monkeypatch):
    """One name's price tape is dead, another's fund-shares sampling is — both are reported, each tagged
    with the feed that stopped, because the repairs differ. The judgment stays in run_daily (the layer
    holding the run's asof) for both."""
    dead_tape = _nr(ticker="DEADTAPE", tape_edge=_ASOF - timedelta(days=40))
    dead_fund = _nr(
        ticker="DEADFUND",
        tape_edge=_ASOF,  # its PRICE tape is fine...
        fund_shares_tracked=True,
        fund_shares_edge=_ASOF - timedelta(days=40),  # ...its sampling stopped
    )
    healthy_fund = _nr(
        ticker="OKFUND", tape_edge=_ASOF, fund_shares_tracked=True, fund_shares_edge=_ASOF
    )
    monkeypatch.setattr(
        daily, "ingest_thesis", _ingest_returning([dead_tape, dead_fund, healthy_fund])
    )
    _thesis(db, "T")

    out = daily.run_daily(db, asof=_ASOF, allow_live=True)

    assert [(s.ticker, s.kind) for s in out[0].tape_stale] == [
        ("DEADTAPE", "price"),
        ("DEADFUND", "fund_shares"),
    ]
    assert out[0].tape_stale[1].edge == _ASOF - timedelta(days=40)


def test_run_daily_never_judges_the_fund_feed_of_a_NON_ETF_member(db, monkeypatch):
    """THE TRAP the tracked flag exists for: an equity member has no fund-shares samples, so its edge is
    None — and None means STALE to the rule. Ungated, every equity in every basket would be reported as a
    dead fund tape. The member here has a healthy price tape and an untracked (default) fund feed.
    """
    monkeypatch.setattr(
        daily, "ingest_thesis", _ingest_returning([_nr(ticker="EQUITY", tape_edge=_ASOF)])
    )
    _thesis(db, "T")

    out = daily.run_daily(db, asof=_ASOF, allow_live=True)

    assert out[0].tape_stale == ()


def test_run_daily_fund_monitor_is_OFF_at_its_OWN_zero(db, monkeypatch):
    """Each feed's off-switch is independent: ALPHADECK_FUND_SHARES_STALE_DAYS=0 silences the fund monitor
    while the price monitor keeps working."""
    dead_both = _nr(
        ticker="DEAD",
        tape_edge=_ASOF - timedelta(days=40),
        fund_shares_tracked=True,
        fund_shares_edge=_ASOF - timedelta(days=40),
    )
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning([dead_both]))
    monkeypatch.setattr(
        daily,
        "get_settings",
        lambda: SimpleNamespace(
            tape_stale_days=5,
            fund_shares_stale_days=0,
            tape_closed_before_days=30,  # T1 — the price monitor reads its proximity window too
            tape_closed_after_days=45,
            image_sha=None,
        ),
    )
    _thesis(db, "T")

    out = daily.run_daily(db, asof=_ASOF, allow_live=True)

    assert [s.kind for s in out[0].tape_stale] == [
        "price"
    ]  # the fund half is off, the price half isn't


def test_report_lists_each_FEED_KIND_in_its_own_block(capsys):
    """stdout is the first place an operator looks. The two feeds get separate blocks with separate advice
    — a merged list would hand half the names the wrong repair — and each row says when that feed last had
    data, in that feed's own words (a bar, a sample)."""
    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(),
                name="T",
                recorded=True,
                tape_stale=(
                    _stale("DEADTAPE", edge=date(2026, 4, 1)),
                    _stale("DEADFUND", edge=date(2026, 4, 2), kind="fund_shares"),
                ),
            )
        ]
    )
    out = capsys.readouterr().out
    assert "DEADTAPE: last bar 2026-04-01" in out
    assert "DEADFUND: last sample 2026-04-02" in out
    price_block, fund_block = out.split("STALE FUND-SHARES TAPES")
    assert "price symbol" in price_block and "price symbol" not in fund_block


# --- run identity on the recorded row (F1, migration 0044) ---------------------------------------------


def _identity_rows(db, thesis_id) -> list[tuple]:
    """(config_hash, code_sha, run_kind) per row, oldest first — read from the RAW table, because the
    stamps live off the card and so never come back through `list_for_thesis`."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT config_hash, code_sha, run_kind FROM calls WHERE thesis_id = %s ORDER BY seq",
            (thesis_id,),
        )
        return [(r["config_hash"], r["code_sha"], r["run_kind"]) for r in cur.fetchall()]


def test_run_daily_stamps_manual_by_DEFAULT(db, monkeypatch):
    """The default is `manual`, and that is the safe direction: the sidecar and an operator invoke the
    SAME command, so anything that forgets to say otherwise must NOT claim to be the nightly record. This
    also covers the Admin "Run daily now" button, which calls run_daily_pass without a kind."""
    _no_network(monkeypatch)
    tid = _thesis(db, "identity-default")

    daily.run_daily(db, asof=_ASOF)

    assert [r[2] for r in _identity_rows(db, tid)] == ["manual"]


def test_run_daily_stamps_cron_when_the_sidecar_says_so(db, monkeypatch):
    """`scripts/daily_cron.sh` passes `--run-kind cron` at every invocation; this is the wiring that
    carries it onto the row."""
    _no_network(monkeypatch)
    tid = _thesis(db, "identity-cron")

    daily.run_daily(db, asof=_ASOF, run_kind="cron")

    assert [r[2] for r in _identity_rows(db, tid)] == ["cron"]


def test_the_stamped_hash_is_the_hash_OF_THE_CFG_THAT_RAN(db, monkeypatch):
    """The anti-drift assertion behind the explicit `cfg` local in run_daily: the fingerprint must be
    computed from the same CallConfig the assembler used, not from a separately-hashed DEFAULT_CONFIG that
    merely happens to match today. Proven by moving a dial and watching the stamp follow it."""
    _no_network(monkeypatch)
    tid = _thesis(db, "identity-cfg")
    moved = DEFAULT_CONFIG.model_copy(update={"warming_min_entry_triggers": 3})
    monkeypatch.setattr(daily, "DEFAULT_CONFIG", moved)

    daily.run_daily(db, asof=_ASOF)

    assert [r[0] for r in _identity_rows(db, tid)] == [config_hash(moved)]
    assert config_hash(moved) != config_hash(DEFAULT_CONFIG)  # the fixture is meaningful


def test_the_code_sha_is_NULL_when_the_image_did_not_declare_one(db, monkeypatch):
    """ "Never fabricated": an image built without GIT_SHA stamps NULL, not "" and not a guess."""
    _no_network(monkeypatch)
    tid = _thesis(db, "identity-nosha")
    monkeypatch.delenv("ALPHADECK_IMAGE_SHA", raising=False)
    get_settings.cache_clear()
    try:
        daily.run_daily(db, asof=_ASOF)
    finally:
        get_settings.cache_clear()

    assert [r[1] for r in _identity_rows(db, tid)] == [None]


def test_a_re_run_under_a_DIFFERENT_identity_appends_NOTHING(db, monkeypatch):
    """The churn gate, end to end through the cron rather than at the repo: a deploy (new code sha) or a
    dial edit does not re-record a thesis whose call is unchanged. COUNT THE TABLE — the read dedups, so a
    spurious append would hide behind a correct-looking call of record."""
    _no_network(monkeypatch)
    tid = _thesis(db, "identity-churn")
    daily.run_daily(db, asof=_ASOF, run_kind="cron")
    before = len(_identity_rows(db, tid))

    monkeypatch.setattr(
        daily, "DEFAULT_CONFIG", DEFAULT_CONFIG.model_copy(update={"warming_min_entry_triggers": 3})
    )
    monkeypatch.setenv("ALPHADECK_IMAGE_SHA", "feedface" * 5)
    get_settings.cache_clear()
    try:
        daily.run_daily(db, asof=_ASOF, run_kind="manual")
    finally:
        get_settings.cache_clear()

    assert len(_identity_rows(db, tid)) == before  # the table did NOT grow
    assert _identity_rows(db, tid)[0][2] == "cron"  # and the first run's identity is untouched


def test_the_cli_threads_run_kind(db, monkeypatch):
    """`main` is where the flag becomes a stamp; the choices are constrained by argparse so a typo cannot
    reach the DB either."""
    seen = {}

    def _fake_pass(**kwargs):
        seen.update(kwargs)
        return daily.DailyPassOutcome(
            results=[],
            asof=_ASOF,
            allow_live=False,
            started_at=None,
            finished_at=None,
            log_path=None,
        )

    monkeypatch.setattr(daily, "run_daily_pass", _fake_pass)
    monkeypatch.setattr(daily, "_report", lambda *a, **k: None)
    monkeypatch.setattr(daily, "_failed_to_record", lambda *a, **k: False)

    daily.main(["--no-live", "--asof", _ASOF.isoformat(), "--run-kind", "cron"])
    assert seen["run_kind"] == "cron"

    seen.clear()
    daily.main(["--no-live", "--asof", _ASOF.isoformat()])
    assert seen["run_kind"] == "manual"  # the default

    with pytest.raises(SystemExit):
        daily.main(["--run-kind", "weekend"])


# --- P1: the rejected-transaction count surfaces in the cron summary, only when nonzero ---------------


def _name_result(**kw):
    kw.setdefault("form4_appended", 0)
    return NameResult(ticker="T", security_id=uuid.uuid4(), price_bars_appended=0, **kw)


def test_report_NAMES_the_rejected_insider_txns_when_there_are_any(capsys):
    """The aggregate a gate can watch. A filer's impossible date is refused at the row level and printed
    in full during the ingest leg; this line is the summary's roll-up, so an operator scanning the cron's
    output sees THAT it happened on this thesis and can go find the itemized reason above it."""
    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(),
                name="T1",
                recorded=True,
                ingested=[_name_result(form4_txn_rejected=2), _name_result(form4_txn_rejected=1)],
            )
        ]
    )
    out = capsys.readouterr().out
    assert "3 insider txn REJECTED (impossible date)" in out  # summed across the thesis's names


def test_report_is_SILENT_about_rejections_on_an_ordinary_night(capsys):
    """Honest loudness (#7): every night rejects zero, so a line that always printed would carry no
    information and would train the eye to skip the one night it matters."""
    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(),
                name="T1",
                recorded=True,
                ingested=[_name_result(form4_appended=5)],
            )
        ]
    )
    assert "REJECTED" not in capsys.readouterr().out


# --- D2: today's in-progress bars withheld — ONE quiet line, deduped across theses --------------------


def test_report_counts_withheld_bars_in_ONE_quiet_line_deduped_across_theses(capsys):
    """A daytime pass withholds today's bar for every priced name. That is an expected state the next
    settled pass resolves, so it gets ONE lowercase count line — never a line per name — and a name held
    by two theses counts once."""
    shared = uuid.uuid4()

    def held(sid):
        return NameResult(
            ticker="T",
            security_id=sid,
            form4_appended=0,
            price_bars_appended=0,
            price_bars_withheld=1,
        )

    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(), name="T1", recorded=True, ingested=[held(shared)]
            ),
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(),
                name="T2",
                recorded=True,
                ingested=[held(shared), held(uuid.uuid4())],
            ),
        ]
    )
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "withheld" in ln]
    assert len(lines) == 1 and lines[0].startswith("2 name(s): today's in-progress bar withheld")


def test_report_is_SILENT_about_withholding_after_the_settle_time(capsys):
    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(), name="T1", recorded=True, ingested=[_name_result()]
            )
        ]
    )
    assert "in-progress bar withheld" not in capsys.readouterr().out


# --- O1: per-name errors — persisted, diffed against the last evaluated pass, NEW ones paged -----------


def _erroring(sid, ticker, *legs):
    """A name whose listed legs FAILED, in the exact shape `ingest_thesis` builds (joined `error` +
    structured `leg_errors`)."""
    pairs = tuple((leg, f"{leg} failed for {ticker}") for leg in legs)
    return _nr(
        ticker=ticker,
        security_id=sid,
        error="; ".join(f"{leg}: {msg}" for leg, msg in pairs),
        leg_errors=pairs,
    )


def test_pass_pages_NEWLY_failing_names_and_collapses_the_KNOWN_ones(monkeypatch, tmp_path, capsys):
    """THE O1 DIFF, across three passes through the REAL artifact writer: pass 1 (no baseline yet) pages the
    whole inventory ONCE; pass 2 with the SAME failures pages NOTHING and prints them as one quiet count;
    pass 3 adds a NEW failing LEG on a known name plus a new name — and pages exactly those two. Keyed on
    `security_id:leg`, so the same ids are reused across passes."""
    hllk, gtls, newco = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pages: list[tuple[str, ...]] = []
    real_assess = daily.assess_health

    def _assess(results, **kw):
        pages.append(tuple(kw.get("name_errors_new") or ()))
        return real_assess(results, **kw)

    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setattr(daily, "assess_health", _assess)

    def _pass(names):
        res = [
            daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=True, ingested=names)
        ]
        monkeypatch.setattr(daily, "run_daily", lambda conn, **k: res)
        out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
        daily._report(
            out.results,
            name_errors_new=out.name_errors_new,
            name_errors_known=out.name_errors_known,
            name_errors_evaluated=out.name_errors_evaluated,
        )
        return out, capsys.readouterr().out

    out1, text1 = _pass([_erroring(hllk, "HLLK", "price"), _erroring(gtls, "GTLS", "price")])
    out2, text2 = _pass([_erroring(hllk, "HLLK", "price"), _erroring(gtls, "GTLS", "price")])
    out3, text3 = _pass(
        [
            _erroring(hllk, "HLLK", "price", "form4"),  # a NEW leg on a known name
            _erroring(gtls, "GTLS", "price"),
            _erroring(newco, "NEWCO", "fund_shares"),
        ]
    )

    assert set(pages[0]) == {
        "GTLS (price)",
        "HLLK (price)",
    }  # first evaluated pass: the inventory, once
    assert pages[1] == ()  # the same failures the next night: silent
    assert set(pages[2]) == {"HLLK (form4)", "NEWCO (fund_shares)"}  # only the genuinely new
    assert (out2.name_errors_known, out3.name_errors_known) == (2, 2)
    # stdout: loud per-name lines for the new ones (with the reason), ONE quiet count for the known
    assert "NAME ERRORS — NEWLY FAILING" in text1 and "HLLK (price): price failed for HLLK" in text1
    assert "NAME ERRORS" not in text2 and "2 known name error(s)" in text2
    assert "NEWCO (fund_shares): fund_shares failed for NEWCO" in text3
    assert "2 known name error(s)" in text3
    # the artifact holds every reason, per thesis, and the run-level diff
    doc = json.loads(sorted(tmp_path.glob("*.json"))[-1].read_text(encoding="utf-8"))
    assert doc["name_errors_evaluated"] is True and doc["name_errors_known"] == 2
    assert {e["leg"] for e in doc["theses"][0]["name_errors"]} == {"price", "form4", "fund_shares"}


def test_pass_mutes_KNOWN_skipped_filings_and_names_only_the_NEW(monkeypatch, tmp_path, capsys):
    """Q18 ACROSS THREE PASSES through the REAL artifact writer, the O1 twin: pass 1 has no baseline, so the
    loop is handed ``known_skips=None`` (every skip prints, as before) and all are new; pass 2 with the SAME
    skipped filings hands the loop exactly those keys and counts them known; pass 3 adds one new filing — it
    is the only one named, and the delta shows the jump."""
    from ingest.skips import IngestSkip

    sid = uuid.uuid4()
    seen_known: list = []
    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer

    def _pass(accessions):
        skips = tuple(
            IngestSkip(kind, acc, sid, "FRHC", "syntax error: line 1, column 0")
            for kind, acc in accessions
        )
        res = [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(), name="T", recorded=True, ingested=[_nr(ingest_skips=skips)]
            )
        ]

        def _run_daily(conn, **k):
            seen_known.append(k.get("known_skips"))
            return res

        monkeypatch.setattr(daily, "run_daily", _run_daily)
        out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
        daily._report(out.results, skips=out.skips)
        return out, capsys.readouterr().out

    night = [
        ("form4_unparseable", "0000950144-97-007016"),
        ("schedule13_outbound", "0000950103-26-005722"),
    ]
    out1, _ = _pass(night)
    out2, text2 = _pass(night)
    out3, text3 = _pass(night + [("form4_unparseable", "0001094891-03-000080")])

    assert seen_known[0] is None  # no baseline: nothing muted
    assert seen_known[1] == {f"{k}:{a}" for k, a in night}  # the previous pass's inventory
    f4 = [
        next(k for k in o.skips.kinds if k.kind == "form4_unparseable") for o in (out1, out2, out3)
    ]
    assert (f4[0].known, len(f4[0].new)) == (0, 1)
    assert (f4[1].known, len(f4[1].new), f4[1].delta) == (1, 0, 0)
    assert (f4[2].known, f4[2].delta) == (1, 1)
    assert [s.accession for s in out3.skips.new] == ["0001094891-03-000080"]
    assert "form4_unparseable: 1 known" in text2 and "0 new, +0 vs the last pass" in text2
    assert "1 new, +1 vs the last pass" in text3
    doc = json.loads(sorted(tmp_path.glob("*.json"))[-1].read_text(encoding="utf-8"))
    assert doc["ingest_skips_evaluated"] is True
    assert doc["ingest_skips"]["form4_unparseable"]["total"] == 2
    assert doc["ingest_skips"]["schedule13_outbound"]["known"] == 1


def test_a_name_held_by_TWO_theses_is_ONE_error_row_in_the_diff(monkeypatch, tmp_path):
    sid = uuid.uuid4()
    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    res = [
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(), name=n, recorded=True, ingested=[_erroring(sid, "DUP", "price")]
        )
        for n in ("T1", "T2")
    ]
    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: res)
    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
    assert [e.label for e in out.name_errors_new] == ["DUP (price)"]


def test_a_NO_LIVE_pass_does_not_evaluate_name_errors(monkeypatch, tmp_path):
    _prime_pass(monkeypatch)
    res = [
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="T",
            withheld_reason="no-live",
            ingested=[_erroring(uuid.uuid4(), "X", "price")],
        )
    ]
    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: res)
    out = daily.run_daily_pass(asof=_ASOF, allow_live=False, notifier=_Silent())
    assert out.name_errors_evaluated is False and out.name_errors_new == ()


def test_report_on_a_NO_LIVE_pass_prints_its_name_errors_UNDIFFED_and_quiet(capsys):
    daily._report(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(),
                name="T",
                withheld_reason="no-live",
                ingested=[_erroring(uuid.uuid4(), "X", "price")],
            )
        ],
        name_errors_evaluated=False,
    )
    out = capsys.readouterr().out
    assert "1 name error(s) — not diffed" in out and "NAME ERRORS" not in out


def test_main_RE_CHECKS_the_catch_up_guard_UNDER_the_lock(monkeypatch, capsys):
    """The TOCTOU close: a pass that finished between the first guard read and the lock has written the
    night's artifact — the re-check under the lock turns a redundant full pass into the no-op it is.
    """
    answers = iter([False, True])  # not yet ran ... then, under the lock, it has
    monkeypatch.setattr(daily, "already_ran_live", lambda asof, **kw: next(answers))
    lock = _FakeLock()
    monkeypatch.setattr(daily, "acquire_pass_lock", lambda **kw: lock)

    def _never(**kw):
        raise AssertionError("the pass ran although the night had just been recorded")

    monkeypatch.setattr(daily, "run_daily_pass", _never)
    daily.main(["--catch-up", "--asof", "2026-09-08"])
    assert "catch-up is a no-op" in capsys.readouterr().out
    assert lock.released  # and the lock it took is released


def test_the_name_error_PAGE_names_tickers_and_legs_never_a_thesis():
    h = daily.assess_health(
        [daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="Secret Thesis Name", recorded=True)],
        asof=_ASOF,
        allow_live=True,
        freeze_check=False,
        name_errors_new=("HLLK (price)",),
    )
    assert h is not None and "1 name(s) NEWLY failing to ingest: HLLK (price)" in h.label
    assert "Secret Thesis Name" not in h.label
    assert "not a cron error" in h.label  # benign-class: the thesis recorded


# --- I1/I2: the run identity + the NON-SECRET env fingerprint ------------------------------------------


def test_env_fingerprint_records_SET_or_UNSET_and_NEVER_a_secret_value():
    secrets = {
        "ALPHADECK_USER_AGENT": "Operator Name operator-secret@example.com",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T000/B000/SECRETTOKEN",
        "MASSIVE_API_KEY": "pk_live_SECRETKEY",
        "HEARTBEAT_URL": "https://hc-ping.com/secret-uuid-1234",
    }
    settings = get_settings().model_copy(
        update={
            "user_agent": secrets["ALPHADECK_USER_AGENT"],
            "slack_webhook_url": secrets["SLACK_WEBHOOK_URL"],
            "polygon_api_key": secrets["MASSIVE_API_KEY"],
            "market_tz": "America/New_York",
            "cron_run_at": "22:30",
        }
    )
    environ = {"TZ": "America/New_York", "RUN_AT": "22:30", **secrets}

    class SlackNotifier:  # the fingerprint records the CLASS NAME in use
        pass

    fp = daily.env_fingerprint(SlackNotifier(), environ=environ, settings=settings)

    assert fp == {
        "tz": "America/New_York",
        "market_tz": "America/New_York",
        "run_at": "22:30",
        "cron_at": "22:30",
        "user_agent_set": True,
        "slack_webhook_set": True,
        "polygon_api_key_set": True,
        "heartbeat_url_set": True,
        "notifier": "SlackNotifier",
    }
    blob = json.dumps(fp)
    for value in secrets.values():
        assert value not in blob
    for fragment in ("SECRET", "secret", "hooks.slack", "hc-ping", "pk_live", "@example"):
        assert fragment not in blob

    bare = daily.env_fingerprint(
        LogNotifier(),
        environ={},
        settings=settings.model_copy(
            update={"user_agent": "  ", "slack_webhook_url": None, "polygon_api_key": None}
        ),
    )
    assert bare["user_agent_set"] is False  # a blank UA is UNSET (the /health rule)
    assert bare["slack_webhook_set"] is False and bare["heartbeat_url_set"] is False
    assert bare["notifier"] == "LogNotifier" and bare["tz"] is None


def test_the_PASS_writes_its_identity_and_env_onto_the_artifact(monkeypatch, tmp_path):
    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setenv("ALPHADECK_IMAGE_SHA", "2f3cdfa0123456789")
    get_settings.cache_clear()
    try:
        out = daily.run_daily_pass(
            asof=_ASOF, allow_live=True, notifier=LogNotifier(), run_kind="cron"
        )
    finally:
        get_settings.cache_clear()
    doc = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert doc["code_sha"] == "2f3cdfa0123456789" == out.code_sha
    assert doc["config_hash"] == config_hash(
        DEFAULT_CONFIG
    )  # the same hash run_daily stamps on rows
    assert doc["run_kind"] == "cron"
    assert doc["env"]["notifier"] == "LogNotifier"
    assert set(doc["env"]) >= {"tz", "market_tz", "run_at", "user_agent_set", "slack_webhook_set"}


# --- H3: an unhandled exception pages ONCE, then re-raises the ORIGINAL ------------------------------


class _Pager:
    def __init__(self, *, raises: BaseException | None = None):
        self.health = []
        self._raises = raises

    def notify(self, event):  # pragma: no cover
        pass

    def notify_spac_status(self, event):  # pragma: no cover
        pass

    def notify_health(self, event):
        self.health.append(event)
        if self._raises is not None:
            raise self._raises


def test_HealthEvent_still_constructs_WITHOUT_the_phase_1b_fields():
    """Every field Phase 1B added to HealthEvent has a default, so a caller (or a subclass — Phase 1A's
    `notify/page.py` RunFailurePage) that predates them constructs it unchanged and reads clean."""
    from dataclasses import dataclass

    from notify import HealthEvent

    base = dict(
        asof=_ASOF,
        theses=0,
        withheld_no_live=0,
        withheld_failure=0,
        errored=0,
        edgar_fetches=0,
        frozen=False,
    )
    h = HealthEvent(**base)
    assert (h.crashed, h.name_errors_new, h.audit_failed) == (None, (), ())

    @dataclass(frozen=True)
    class _Sub(HealthEvent):
        pass

    assert _Sub(**base).crashed is None


def test_an_unhandled_exception_PAGES_once_and_RE_RAISES_the_original(monkeypatch):
    _prime_pass(monkeypatch)
    boom = RuntimeError("the loop exploded\nsecond line")

    def _explode(conn, **k):
        raise boom

    monkeypatch.setattr(daily, "run_daily", _explode)
    pager = _Pager()
    with pytest.raises(RuntimeError) as exc:
        daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=pager)
    assert exc.value is boom  # the ORIGINAL exception, unchanged
    assert len(pager.health) == 1
    page = pager.health[0]
    assert page.crashed == "RuntimeError: the loop exploded second line"
    assert "PASS CRASHED" in page.label and page.asof == _ASOF


def test_the_crash_page_can_NEVER_mask_the_original_exception(monkeypatch):
    """Fail-open: a notifier that itself raises (a Slack adapter bug, an outage that escaped) must not
    replace the exception being reported — the caller still sees the pass's own error."""
    _prime_pass(monkeypatch)
    boom = ValueError("the real problem")
    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: (_ for _ in ()).throw(boom))
    pager = _Pager(raises=RuntimeError("the pager broke too"))
    with pytest.raises(ValueError) as exc:
        daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=pager)
    assert exc.value is boom
    assert len(pager.health) == 1  # it tried, once


def test_a_LOCK_REFUSAL_is_not_a_crash_and_does_not_page(monkeypatch):
    _prime_pass(monkeypatch)

    def _held(**kw):
        raise daily.PassAlreadyRunning("pid 1 (alphadeck-pass:cron)")

    monkeypatch.setattr(daily, "acquire_pass_lock", _held)
    pager = _Pager()
    with pytest.raises(daily.PassAlreadyRunning):
        daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=pager)
    assert pager.health == []


def test_main_PAGES_when_the_lock_cannot_be_taken_for_a_DB_reason(monkeypatch):
    """main takes the lock itself (to re-check the catch-up guard under it), so a DB that is down at that
    point must page there too — the pass never starts, and this is the only record it leaves."""
    pager = _Pager()
    monkeypatch.setattr(daily, "get_notifier", lambda: pager)

    def _db_down(**kw):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(daily, "acquire_pass_lock", _db_down)
    _tonight(monkeypatch, _ASOF)
    with pytest.raises(ConnectionRefusedError):
        daily.main(["--asof", _ASOF.isoformat()])
    assert [p.crashed for p in pager.health] == ["ConnectionRefusedError: connection refused"]


# --- C2: the statement_timeout on every connection the pass opens --------------------------------------


def _show_timeout(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        return cur.fetchone()["statement_timeout"]


def test_pass_connections_carry_the_DEFAULT_statement_timeout(db):
    conn = daily._pass_connect()
    try:
        assert get_settings().cron_statement_timeout_s == 300.0
        assert _show_timeout(conn) == "5min"
    finally:
        conn.close()


def test_the_statement_timeout_CANCELS_a_stuck_statement_and_survives_a_ROLLBACK(db, monkeypatch):
    """The point of C2: a statement that runs past the ceiling raises instead of hanging the pass — and the
    ceiling survives the per-thesis rollback the loop does after an error (a SET left inside an open
    transaction would have been rolled back with it)."""
    import psycopg

    monkeypatch.setenv("ALPHADECK_CRON_STATEMENT_TIMEOUT_S", "0.25")
    get_settings.cache_clear()
    try:
        conn = daily._pass_connect()
    finally:
        get_settings.cache_clear()
    try:
        assert _show_timeout(conn) == "250ms"
        with pytest.raises(psycopg.errors.QueryCanceled):
            conn.execute("SELECT pg_sleep(3)")
        conn.rollback()  # what run_daily does after a per-thesis failure
        assert _show_timeout(conn) == "250ms"  # still bound
    finally:
        conn.close()


def test_a_ZERO_statement_timeout_leaves_the_server_default(db, monkeypatch):
    monkeypatch.setenv("ALPHADECK_CRON_STATEMENT_TIMEOUT_S", "0")
    get_settings.cache_clear()
    try:
        conn = daily._pass_connect()
    finally:
        get_settings.cache_clear()
    try:
        assert _show_timeout(conn) == "0"
    finally:
        conn.close()


def test_EVERY_connection_the_pass_opens_gets_the_ceiling(monkeypatch):
    """Wiring: each connect() inside run_daily_pass (the refresh leg, the loop, the sweep, the radar)
    is followed by the statement_timeout call on THAT connection — none bypasses `_pass_connect`."""
    _prime_pass(monkeypatch)
    opened, bound = [], []

    def _connect():
        c = SimpleNamespace(close=lambda: None, execute=lambda *a, **k: None, commit=lambda: None)
        opened.append(c)
        return c

    monkeypatch.setattr(daily, "connect", _connect)
    monkeypatch.setattr(daily, "set_statement_timeout", lambda conn, s: bound.append((conn, s)))
    daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())
    # benchmarks, the per-thesis loop (which now also carries the fundamentals refresh), shell sweep,
    # SPAC radar
    assert len(opened) == 4
    assert [c for c, _ in bound] == opened
    assert {s for _, s in bound} == {get_settings().cron_statement_timeout_s}


def test_pass_threads_the_SPLIT_scan_to_the_artifact_the_audit_and_the_page(monkeypatch):
    """T4 wiring: the pass runs the split-basis scan through `_pass_connect` (the C2 ceiling) with its own
    asof / allow_live, then hands the SAME result to the artifact, the self-audit and the R4 assessor (the
    new defects' labels page). The scan itself is proved on a real DB in tests/pipeline/test_split_basis.py.
    """
    from pipeline.split_basis import ISLAND, SplitDefect

    _prime_pass(monkeypatch)
    d = SplitDefect(uuid.uuid4(), "VWAV", ISLAND, (date(2026, 9, 15),), 18.0, 1)
    scan = SplitScan(evaluated=True, defects=(d,), new=(d,), window_days=430)
    seen: dict = {}

    def _scan(**kw):
        seen["scan"] = kw
        return scan

    def _audit(ctx, *a, **k):
        seen["audit_split"] = ctx.split
        return []

    def _log(results, **kw):
        seen["log_split"] = kw.get("split")

    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen["page"] = kw.get("split_defects_new")
        return real_assess(results, **kw)

    monkeypatch.setattr(daily, "run_split_scan", _scan)
    monkeypatch.setattr(daily, "run_audit", _audit)
    monkeypatch.setattr(daily, "write_cron_run_log", _log)
    monkeypatch.setattr(daily, "assess_health", _assess)

    out = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Silent())

    assert seen["scan"]["asof"] == _ASOF and seen["scan"]["allow_live"] is True
    assert seen["scan"]["connect"] is daily._pass_connect
    assert out.split is scan and seen["audit_split"] is scan and seen["log_split"] is scan
    assert seen["page"] == ("VWAV island x18 (2026-09-15)",)


def test_pass_sweeps_form4a_BEFORE_the_loop_and_threads_it_to_the_scan(monkeypatch):
    """R8-B wiring: the Form 4/A sweep runs BEFORE `run_daily` (so tonight's calls read the applied state)
    through `_pass_connect` with the pass's allow_live; the scan then gets the SAME sweep outcome and the
    pass's start instant (its count of system applications). The sweep itself is proved on a real DB in
    tests/pipeline/test_insider_amendment_scan.py."""
    order, _, seen = _prime_pass(monkeypatch)
    swept = SweepOutcome(applied=3, by_basis={"system:filer-correction": 3}, ran=True)

    def _amend_sweep(**kw):
        order.append("amendment_sweep")
        seen["amendment_sweep"] = kw
        return swept

    def _scan(**kw):
        seen["amendment_scan"] = kw
        return AmendmentScan(evaluated=True)

    monkeypatch.setattr(daily, "run_amendment_sweep", _amend_sweep)
    monkeypatch.setattr(daily, "run_amendment_scan", _scan)
    started = datetime(2026, 10, 21, 2, 30, tzinfo=timezone.utc)
    daily._run_daily_pass_locked(
        asof=_ASOF,
        allow_live=True,
        notifier=_Silent(),
        catch_up=False,
        run_kind="manual",
        started_at=started,
    )
    assert order.index("amendment_sweep") < order.index("run_daily")
    assert seen["amendment_sweep"] == {"allow_live": True, "connect": daily._pass_connect}
    assert seen["amendment_scan"]["sweep"] is swept
    assert seen["amendment_scan"]["since"] == started


# --- T3 + Q8 + T2: expected price 404s, the unpriceable class, the rename probe on the pass -------------
# REAL shapes from prod 2026-10-16 (tests/fixtures/prices/price_feed_repairs_2026_10.json): GTLS a genuine
# close (its 25-NSE on its last bar, vendor 404), HLLK renamed to SDCO (vendor 404 under HLLK), WONDF never
# priced (EDGAR lists no symbol, vendor 404).

_OCT16 = date(2026, 10, 16)


def _real(ticker, sid, *, tape_edge, delisting=(), edgar_tickers=(), not_found=True, **kw):
    """A NameResult as the ingest builds it for a name whose price leg got the vendor's real 404."""
    msg = str(fx.not_found(ticker))
    return _nr(
        ticker=ticker,
        security_id=sid,
        tape_edge=tape_edge,
        delisting_filings=delisting,
        edgar_tickers=edgar_tickers,
        error=f"price: {msg}" if not_found else None,
        leg_errors=(("price", msg),) if not_found else (),
        price_not_found=not_found,
        **kw,
    )


def test_T3_a_CLOSED_names_vendor_404_is_EXPECTED_and_ingest_fresh_stays_TRUE(db, monkeypatch):
    """The measured Nuclear Buildout night: GTLS closed (T1) and 404s at the vendor every night. Before T3
    that 404 made `ingest_fresh=false` on EVERY row of the thesis. Now it is an expected miss: not a name
    error, 0 ingest errors, `ingest_fresh` true — and still visible, on the result and in the inventory.
    """
    gtls = uuid.uuid4()
    names = [
        _real("GTLS", gtls, tape_edge=fx.last_bar("GTLS"), delisting=fx.delisting("GTLS")),
        _nr(ticker="CEG", tape_edge=_OCT16),
    ]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names))
    tid = _thesis(db, "Nuclear Buildout")

    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)

    assert _health(db, tid) == {"ingest_fresh": True, "ingest_errors": 0}
    assert [x.error for x in res.ingested] == [None, None]
    (miss,) = res.expected_price_misses
    assert (miss.ticker, miss.reason) == ("GTLS", "closed")
    assert miss.error.startswith("Client error '404 Not Found'")
    (row,) = res.tape_stale
    assert row.closed and row.closed_at == date(2026, 7, 16)  # still in the inventory (#9)


def test_T3_a_RENAMED_but_UNADOPTED_names_404_STILL_errors(db, monkeypatch):
    """HLLK: EDGAR lists SDCO. Its 404 is the news (one Adopt fixes it), so it stays an error and the row
    is marked partial — exactly as before T3."""
    names = [_real("HLLK", uuid.uuid4(), tape_edge=fx.last_bar("HLLK"), edgar_tickers=("SDCO",))]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names + [_nr(tape_edge=_OCT16)]))
    tid = _thesis(db, "Modern Defense")

    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)

    assert _health(db, tid) == {"ingest_fresh": False, "ingest_errors": 1}
    assert res.expected_price_misses == ()
    assert res.tape_stale[0].renamed is True


def test_Q8_an_UNACKNOWLEDGED_unpriceable_404_errors_and_an_ACKNOWLEDGED_one_is_expected(
    db, monkeypatch
):
    """WONDF (never priced, EDGAR lists nothing): loud until the operator ratifies it — its 404 is an error
    and its row an unpriceable repair row. After the operator ACKNOWLEDGES it (an append-only
    price_feed_ack), the same night's 404 is expected and the row is quiet."""
    wondf = fx.insert_security(db, "WONDF", with_tape=False)
    names = [_real("WONDF", wondf, tape_edge=None), _nr(tape_edge=_OCT16)]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names))
    _thesis(db, "The Everything Exchange")

    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)
    assert [x.error is not None for x in res.ingested] == [True, False]
    (row,) = res.tape_stale
    assert row.unpriceable and not row.acknowledged and not row.quiet

    price_feed_repo.acknowledge(db, wondf, reason="keep it", basis="operator:acknowledge")
    db.commit()
    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)
    (row,) = res.tape_stale
    assert row.unpriceable and row.acknowledged and row.quiet
    assert [x.error for x in res.ingested] == [None, None]  # 0 ingest errors -> ingest_fresh true
    assert [(m.ticker, m.reason) for m in res.expected_price_misses] == [("WONDF", "acknowledged")]


def test_Q8_an_acknowledged_thesis_records_ingest_fresh_TRUE(db, monkeypatch):
    """The recorded stamp itself (R2b): with WONDF acknowledged, the thesis's call-of-record is written with
    `ingest_fresh` true and 0 errors."""
    wondf = fx.insert_security(db, "WONDF", with_tape=False)
    price_feed_repo.acknowledge(db, wondf, reason="keep it", basis="operator:acknowledge")
    db.commit()
    names = [_real("WONDF", wondf, tape_edge=None), _nr(tape_edge=_OCT16)]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names))
    tid = _thesis(db, "The Everything Exchange")

    daily.run_daily(db, asof=_OCT16, allow_live=True)

    assert _health(db, tid) == {"ingest_fresh": True, "ingest_errors": 0}


def test_Q8_an_acknowledgment_never_silences_a_name_that_HAS_bars(db, monkeypatch):
    """An acknowledged id whose tape has bars and stopped is a FEED GAP, not unpriceable: not marked, and
    its 404 is not excused."""
    sid = fx.insert_security(db, "WONDF", with_tape=False)
    price_feed_repo.acknowledge(db, sid, reason="keep it", basis="operator:acknowledge")
    db.commit()
    names = [_real("WONDF", sid, tape_edge=_OCT16 - timedelta(days=30)), _nr(tape_edge=_OCT16)]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names))
    tid = _thesis(db, "T")

    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)

    assert res.tape_stale[0].acknowledged is False and res.expected_price_misses == ()
    assert _health(db, tid) == {"ingest_fresh": False, "ingest_errors": 1}


def _sce_pg(db) -> uuid.UUID:
    """SCE-PG's master row as prod holds it (MEASURED 2026-10-21, read-only)."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                sid,
                DEFAULT_TENANT_ID,
                "SCE-PG",
                "0000092103",
                "SOUTHERN CALIFORNIA EDISON Co",
                "2026-01-01",
            ),
        )
    db.commit()
    return sid


def test_a_LIMITED_HISTORY_mark_never_quiets_a_STOPPED_tape_nor_excuses_its_404(db, monkeypatch):
    """0056: the operator marked SCE-PG "the vendor serves no history" (Yahoo serves it one bar). The mark
    quiets NOTHING on the nightly path — the pass's quieting reads acknowledgments only. So a marked name
    whose tape STOPPED (no new bar for 30 days — no error, nothing appended) is a LOUD stale row, and a
    vendor 404 on it is still a name error (ingest_fresh false), exactly as for an unmarked name."""
    sce = _sce_pg(db)
    price_feed_repo.mark_limited_history(
        db, sce, reason="Yahoo serves one bar for SCE-PG", basis="operator:limited-history"
    )
    db.commit()
    stopped = _OCT16 - timedelta(days=30)

    # the stopped-tape shape: the vendor answers, nothing new lands
    names = [_nr(ticker="SCE-PG", security_id=sce, tape_edge=stopped), _nr(tape_edge=_OCT16)]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names))
    _thesis(db, "Nuclear Buildout")
    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)
    (row,) = res.tape_stale
    assert (row.security_id, row.edge) == (sce, stopped)
    assert not row.quiet and not row.acknowledged and not row.closed and not row.unpriceable

    # the vendor-404 shape: still a name error (it counts toward ingest_errors), never an expected miss
    names = [_real("SCE-PG", sce, tape_edge=stopped), _nr(tape_edge=_OCT16)]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names))
    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)
    assert res.expected_price_misses == () and not res.tape_stale[0].quiet
    assert [x.error is not None for x in res.ingested] == [True, False]


def test_the_acknowledged_read_FAILS_TOWARD_LOUD(db, monkeypatch, capsys):
    """A fault reading the acknowledgments is one warning line and an empty set: the unpriceable name stays
    loud and its 404 an error for this pass — never silenced on a guess — and the pass still records.
    """

    def _boom(conn):
        raise RuntimeError("relation price_feed_ack does not exist")

    monkeypatch.setattr(price_feed_repo, "acknowledged_security_ids", _boom)
    names = [_real("WONDF", uuid.uuid4(), tape_edge=None), _nr(tape_edge=_OCT16)]
    monkeypatch.setattr(daily, "ingest_thesis", _ingest_returning(names))
    tid = _thesis(db, "T")

    (res,) = daily.run_daily(db, asof=_OCT16, allow_live=True)

    assert "could not read the acknowledged-unpriceable names" in capsys.readouterr().out
    assert res.recorded is True and res.tape_stale[0].acknowledged is False
    assert _health(db, tid) == {"ingest_fresh": False, "ingest_errors": 1}


def test_pass_pages_a_NEW_unpriceable_name_ONCE_and_a_KNOWN_one_never(monkeypatch, tmp_path):
    """Q8 paging through the existing R4 newly-stale path, across passes with the REAL artifact writer:
    WONDF pages the first night (new), is silent the next (known), stays silent once ACKNOWLEDGED (quiet),
    and pages ONCE more when the acknowledgment is REVOKED (the operator asked for it loud again).
    """
    wondf = uuid.uuid4()
    seen: list[tuple[str, ...]] = []
    real_assess = daily.assess_health

    def _assess(results, **kw):
        seen.append(tuple(x.label for x in (kw.get("tape_stale_new") or ())))
        return real_assess(results, **kw)

    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setattr(daily, "assess_health", _assess)

    def _pass(*, acknowledged):
        row = daily.StaleTape(
            ticker="WONDF",
            security_id=wondf,
            edge=None,
            edgar_tickers=(),
            acknowledged=acknowledged,
        )
        monkeypatch.setattr(daily, "run_daily", lambda conn, **k: _thesis_result_with([row]))
        return daily.run_daily_pass(asof=_OCT16, allow_live=True, notifier=_Silent())

    _pass(acknowledged=False)
    _pass(acknowledged=False)
    _pass(acknowledged=True)
    _pass(acknowledged=True)
    _pass(acknowledged=False)

    assert seen == [("WONDF",), (), (), (), ("WONDF",)]


def test_pass_threads_the_RENAME_probe_to_the_artifact(monkeypatch):
    """T2 wiring: the pass runs the probe over `run_daily`'s results through `_pass_connect` (the C2
    ceiling) with its own asof / allow_live, and hands the SAME probe to the artifact and the outcome.
    """
    from pipeline.rename_probe import CandidateProbe, RenameRecommendation

    _prime_pass(monkeypatch)
    sid = uuid.uuid4()
    rec = RenameRecommendation(
        security_id=sid,
        ticker="HLLK",
        edge=date(2026, 9, 24),
        edgar_tickers=("SDCO",),
        candidates=(CandidateProbe("SDCO", verified=True, overlap=30, ratio=1.0),),
    )
    probe = RenameProbe(evaluated=True, rows=(rec,), fetches=1)
    stale = _stale("HLLK", sid=sid, edge=date(2026, 9, 24), renamed=True, edgar_tickers=("SDCO",))
    results = _thesis_result_with([stale])
    seen: dict = {}

    def _probe(**kw):
        seen["probe"] = kw
        return probe

    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: results)
    monkeypatch.setattr(daily, "run_rename_probe", _probe)
    monkeypatch.setattr(daily, "write_cron_run_log", lambda r, **kw: seen.update(log=kw))

    out = daily.run_daily_pass(asof=_OCT16, allow_live=True, notifier=_Silent())

    assert seen["probe"]["results"] is results and seen["probe"]["connect"] is daily._pass_connect
    assert seen["probe"]["asof"] == _OCT16 and seen["probe"]["allow_live"] is True
    assert out.rename_probe is probe and seen["log"]["rename_probe"] is probe


# --- T7: the successor-CIK detector on the pass ---------------------------------------------------------
# The REAL measured case (tests/fixtures/edgar/successor_cik_2026_10.json): UROY's stored CIK 0001711570
# lists no tickers; SEC's map assigns UROY to 0002143673 (8-K12B 2026-07-28). The scan itself is proved on
# a real DB in tests/pipeline/test_successor_cik.py; these prove the pass's wiring.


def _uroy_row(sid):
    from pipeline.successor_cik import SuccessorCandidate, SuccessorRow

    return SuccessorRow(
        security_id=sid,
        ticker="UROY",
        price_symbol=None,
        stored_cik="0001711570",
        master_name="Uranium Royalty Corp.",
        candidates=(SuccessorCandidate(cik="0002143673", matched_on=("UROY",)),),
    )


def test_pass_threads_the_SUCCESSOR_scan_to_the_artifact_the_outcome_and_the_page(monkeypatch):
    _prime_pass(monkeypatch)
    row = _uroy_row(uuid.uuid4())
    scan = SuccessorScan(evaluated=True, rows=(row,), new=(row,), judged=1, no_ticker=1)
    results = _thesis_result_with([])
    seen: dict = {}

    def _scan(**kw):
        seen["scan"] = kw
        return scan

    real_assess = daily.assess_health

    def _assess(r, **kw):
        seen["page"] = kw.get("successor_cik_new")
        return real_assess(r, **kw)

    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: results)
    monkeypatch.setattr(daily, "run_successor_scan", _scan)
    monkeypatch.setattr(daily, "write_cron_run_log", lambda r, **kw: seen.update(log=kw))
    monkeypatch.setattr(daily, "assess_health", _assess)

    out = daily.run_daily_pass(asof=_OCT16, allow_live=True, notifier=_Silent())

    assert seen["scan"]["results"] is results and seen["scan"]["connect"] is daily._pass_connect
    assert seen["scan"]["allow_live"] is True
    assert seen["scan"]["previous"] is daily.previous_successor_keys
    assert out.successor is scan and seen["log"]["successor"] is scan
    assert seen["page"] == ("UROY -> CIK 0002143673",)


def test_pass_pages_a_NEW_successor_ONCE_and_a_KNOWN_one_never(monkeypatch, tmp_path):
    """Across passes with the REAL artifact writer and the REAL baseline reader (the stubbed scan applies
    the pass's own ``previous``): UROY pages the first night, is silent the next."""
    sid = uuid.uuid4()
    pages: list = []

    class _Pager(_Silent):
        def notify_health(self, event):
            pages.append(event)

    def _scan(**kw):
        row = _uroy_row(sid)
        prior = kw["previous"]()
        return SuccessorScan(
            evaluated=True,
            rows=(row,),
            new=(row,) if prior is None or row.key not in prior else (),
        )

    _prime_pass(monkeypatch)
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)  # REAL writer
    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: _thesis_result_with([]))
    monkeypatch.setattr(daily, "run_successor_scan", _scan)

    daily.run_daily_pass(asof=_OCT16, allow_live=True, notifier=_Pager())
    daily.run_daily_pass(asof=_OCT16, allow_live=True, notifier=_Pager())

    successor_pages = [p.successor_cik_new for p in pages if p.successor_cik_new]
    assert successor_pages == [("UROY -> CIK 0002143673",)]  # ONCE
    # the newest artifact (two passes inside one second share a file name — the later one wins) still
    # carries the known row: on the artifact and the Admin panel, just not paged again
    newest = cron_run_log.list_run_logs(base_dir=tmp_path)[0]["successor_cik"]
    assert len(newest["rows"]) == 1 and newest["new"] == []


def test_report_prints_NEW_successors_loud_and_KNOWN_ones_quiet(capsys):
    from pipeline.successor_cik import report_lines

    a, b = _uroy_row(uuid.uuid4()), _uroy_row(uuid.uuid4())
    res = daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=False)
    daily._report([res], successor=SuccessorScan(evaluated=True, rows=(a, b), new=(a,)))
    out = capsys.readouterr().out
    assert "SUCCESSOR CIK — NEW" in out and "  UROY -> CIK 0002143673" in out
    assert "1 known successor-CIK name(s) awaiting the operator's Adopt" in out
    assert report_lines(SuccessorScan(evaluated=True)) == []


def test_report_carries_the_probe_verdict_and_the_QUIET_lines(capsys):
    """The printed report: the renamed row names its splice-verified successor (to Adopt — never adopted
    here); the acknowledged unpriceable name leaves the loud block for ONE quiet line; the expected 404s
    collapse to ONE quiet line; a failed probe is loud."""
    from pipeline.price_feed import ExpectedPriceMiss
    from pipeline.rename_probe import CandidateProbe, RenameRecommendation

    hllk, wondf, gtls = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rec = RenameRecommendation(
        security_id=hllk,
        ticker="HLLK",
        edge=date(2026, 9, 24),
        edgar_tickers=("SDCO",),
        candidates=(
            CandidateProbe(
                "SDCO", verified=True, overlap=30, ratio=1.0, last_bar=date(2026, 10, 15)
            ),
        ),
    )
    stale = (
        _stale("HLLK", sid=hllk, edge=date(2026, 9, 24), renamed=True, edgar_tickers=("SDCO",)),
        daily.StaleTape(ticker="WONDF", security_id=wondf, edge=None, acknowledged=True),
        _stale("GTLS", sid=gtls, edge=date(2026, 7, 16), closed_at=date(2026, 7, 16)),
    )
    res = daily.ThesisRunResult(
        thesis_id=uuid.uuid4(),
        name="T",
        recorded=True,
        tape_stale=stale,
        expected_price_misses=(
            ExpectedPriceMiss(gtls, "GTLS", "closed", "Client error '404 Not Found'"),
            ExpectedPriceMiss(wondf, "WONDF", "acknowledged", "Client error '404 Not Found'"),
        ),
    )
    daily._report([res], probe=RenameProbe(evaluated=True, rows=(rec,)))
    out = capsys.readouterr().out

    assert (
        "HLLK: last bar 2026-09-24 · renamed — EDGAR lists SDCO · splice-verified SDCO (30 shared bars, "
        "x1.000, bars to 2026-10-15) — Adopt on the Admin page" in out
    )
    assert "WONDF: last bar" not in out  # acknowledged: out of the loud block…
    assert (
        "1 acknowledged unpriceable name(s) — kept unpriced by the operator" in out
    )  # …one quiet line
    assert (
        "2 expected price 404(s) — closed or acknowledged-unpriceable names, not errors "
        "(ingest_fresh unaffected): GTLS (closed), WONDF (acknowledged)" in out
    )
    daily._report([res], probe=RenameProbe(error="OperationalError: boom"))
    assert "RENAME PROBE FAILED — OperationalError: boom" in capsys.readouterr().out
