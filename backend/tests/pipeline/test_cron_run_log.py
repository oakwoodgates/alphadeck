from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from pipeline.cron_run_log import (
    already_ran_live,
    list_run_logs,
    previous_stale_tapes,
    write_cron_run_log,
)
from pipeline.daily import ThesisRunResult
from pipeline.ingest_thesis import NameResult
from pipeline.tape_health import StaleTape

_START = datetime(2026, 7, 17, 22, 30, 1, tzinfo=timezone.utc)
_END = datetime(2026, 7, 17, 23, 35, 12, tzinfo=timezone.utc)

# The R6 guard reads the night in MARKET time: a live artifact for an as-of counts only if it STARTED at/after
# that night's RUN_AT (2026-09-10). Its fixtures are authored as a market-time wall clock and converted to the
# UTC-aware instant `write_cron_run_log` stamps (`run_daily_pass` records `datetime.now(timezone.utc)`, so a
# real artifact's `started_at` is an aware UTC ISO string — exactly what `_utc` produces). July/September 2026
# are EDT: 22:30 ET == 02:30Z the next day, 09:15 ET == 13:15Z.
_NY = ZoneInfo("America/New_York")
_RUN_AT = time(22, 30)


def _utc(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    """A market-time wall clock as the UTC-aware instant the writer stamps."""
    return datetime(y, m, d, hh, mm, ss, tzinfo=_NY).astimezone(timezone.utc)


_JUL17 = date(2026, 7, 17)
_NIGHT_JUL17 = _utc(2026, 7, 17, 22, 30, 1)  # the scheduled pass, 1 s after RUN_AT


def _name(**kw) -> NameResult:
    base = dict(ticker="AAA", security_id=uuid4(), form4_appended=0, price_bars_appended=0)
    return NameResult(**{**base, **kw})


def _thesis_result(**kw) -> ThesisRunResult:
    base = dict(thesis_id=uuid4(), name="T")
    return ThesisRunResult(**{**base, **kw})


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_writes_a_run_record_with_the_run_shape(tmp_path):
    results = [
        _thesis_result(
            name="AI Memory",
            recorded=True,
            ingested=[_name(form4_appended=3, price_bars_appended=1), _name()],
        ),
        _thesis_result(name="HIMS", recorded=False),  # unchanged
    ]
    path = write_cron_run_log(
        results,
        asof=date(2026, 7, 17),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    assert path is not None and path.name == "20260717T223001Z.json"
    doc = _read(path)
    assert doc["mode"] == "live"
    assert doc["asof"] == "2026-07-17"
    assert doc["started_at"] == _START.isoformat() and doc["finished_at"] == _END.isoformat()
    assert doc["duration_s"] == 3911.0  # 1h5m11s — the ingest-duration signal (Flag 5)
    assert doc["summary"] == {
        "theses": 2,
        "appended": 1,
        "unchanged": 1,
        "withheld": 0,
        "errored": 0,
        "transitions": 0,
    }
    ai = next(t for t in doc["theses"] if t["name"] == "AI Memory")
    assert (
        ai["form4_appended"] == 3 and ai["price_bars_appended"] == 1 and ai["names_ingested"] == 2
    )


def test_mode_records_no_live_so_the_R2_gate_can_read_it(tmp_path):
    path = write_cron_run_log(
        [_thesis_result()],
        asof=date(2026, 7, 17),
        allow_live=False,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    assert _read(path)["mode"] == "no-live"


def test_names_errored_surfaces_the_total_ingest_failure_shape(tmp_path):
    # every name errored → the Source-C fingerprint R2 will gate on: names_errored == names_ingested
    failed = _thesis_result(
        name="Frozen", ingested=[_name(error="form4: boom"), _name(error="form4: boom")]
    )
    path = write_cron_run_log(
        [failed],
        asof=date(2026, 7, 17),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    t = _read(path)["theses"][0]
    assert t["names_ingested"] == 2 and t["names_errored"] == 2  # totally failed, distinguishable


def test_a_zero_fact_healthy_run_is_distinguishable_from_a_no_op(tmp_path):
    # THE trap R2 must not fall into: a current thesis appends 0 facts on a healthy live run and is NOT a
    # failure. names_ingested>0 + names_errored==0 + appended:0 is a clean quiet day, not a do-nothing.
    quiet = _thesis_result(name="Current", recorded=False, ingested=[_name(), _name(), _name()])
    path = write_cron_run_log(
        [quiet],
        asof=date(2026, 7, 17),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    t = _read(path)["theses"][0]
    assert t["names_ingested"] == 3 and t["names_errored"] == 0 and t["form4_appended"] == 0


def test_edgar_fetches_separates_a_FREEZE_from_a_healthy_quiet_day(tmp_path):
    """THE addition: fact tallies alone can't tell a stale-index FREEZE from a healthy nothing-filed night —
    both show 0 appended, N skipped, names_ingested>0, names_errored 0. The ONLY difference is whether the
    network happened. A FREEZE reads edgar_fetches 0 on a `live` run; a healthy night is nonzero — pageable.
    """
    frozen = _thesis_result(
        name="Frozen", recorded=False, edgar_fetches=0, ingested=[_name(), _name()]
    )  # served the stale cache: 0 network pulls
    healthy = _thesis_result(
        name="Healthy quiet", recorded=False, edgar_fetches=88, ingested=[_name(), _name()]
    )  # refreshed the index, nothing new filed
    doc = _read(
        write_cron_run_log(
            [frozen, healthy],
            asof=date(2026, 7, 17),
            allow_live=True,
            started_at=_START,
            finished_at=_END,
            base_dir=tmp_path,
        )
    )
    # identical fact tallies…
    for t in doc["theses"]:
        assert t["form4_appended"] == 0 and t["names_ingested"] == 2 and t["names_errored"] == 0
    # …but the freeze detector separates them, at the run level and per thesis
    assert doc["edgar_fetches"] == 88  # run total
    assert next(t for t in doc["theses"] if t["name"] == "Frozen")["edgar_fetches"] == 0
    assert next(t for t in doc["theses"] if t["name"] == "Healthy quiet")["edgar_fetches"] == 88


def test_payload_carries_catch_up_default_false(tmp_path):
    # the sidecar's boot / late-wake `--catch-up` pass is marked on the artifact (the admin history skips
    # its freeze check); a scheduled pass records False
    scheduled = write_cron_run_log(
        [_thesis_result()],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    assert _read(scheduled)["catch_up"] is False
    caught_up = write_cron_run_log(
        [_thesis_result()],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_END,  # a distinct started-at → a distinct filename
        finished_at=_END,
        base_dir=tmp_path,
        catch_up=True,
    )
    assert _read(caught_up)["catch_up"] is True
    # a catch-up is a LIVE pass (it satisfies already_ran_live — mode is untouched by the flag)
    assert _read(caught_up)["mode"] == "live"


def test_an_artifact_WITHOUT_the_catch_up_key_still_parses(tmp_path):
    # an artifact written before the key existed: list_run_logs returns it unchanged (the reader is
    # fail-open per artifact, never strict over new keys) and the guard still counts it as ran-live
    path = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,  # the night's own pass (post-RUN_AT), so the guard's other rule holds
        finished_at=_END,
        base_dir=tmp_path,
    )
    doc = _read(path)
    del doc["catch_up"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    logs = list_run_logs(base_dir=tmp_path)
    assert len(logs) == 1 and "catch_up" not in logs[0]
    assert logs[0].get("catch_up", False) is False  # the admin reconstruction's read
    assert already_ran_live(_JUL17, run_at=_RUN_AT, tz=_NY, base_dir=tmp_path) is True


def test_payload_carries_the_BENCHMARK_counts_default_clean(tmp_path):
    """G4 — the shared-input refresh leg's outcome is RUN-LEVEL on the artifact, beside edgar_fetches.
    It must live here, not only in the live page: the admin history re-derives health from this file, so a
    count that reached only the notifier would make the night the platform PAGED about re-read as green.
    """
    clean = write_cron_run_log(
        [_thesis_result()],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    doc = _read(clean)
    assert doc["benchmark_errors"] == 0 and doc["benchmark_leg_failed"] is False

    faulted = write_cron_run_log(
        [_thesis_result()],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_END,  # a distinct started-at -> a distinct filename
        finished_at=_END,
        base_dir=tmp_path,
        benchmark_errors=2,
        benchmark_leg_failed=True,
    )
    doc = _read(faulted)
    assert doc["benchmark_errors"] == 2 and doc["benchmark_leg_failed"] is True


def test_an_artifact_WITHOUT_the_BENCHMARK_keys_still_parses(tmp_path):
    """The same back-compat rule as the catch_up key, and the reason the admin reader uses .get: an
    artifact written before these keys existed must read CLEAN, never broken — a strict read would raise,
    the caller would skip it fail-open, and the whole run history would silently blank after the deploy.
    """
    path = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
    )
    doc = _read(path)
    del doc["benchmark_errors"], doc["benchmark_leg_failed"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    logs = list_run_logs(base_dir=tmp_path)
    assert len(logs) == 1 and "benchmark_errors" not in logs[0]
    # the admin reconstruction's read of each (int(... or 0) / bool(..., False))
    assert int(logs[0].get("benchmark_errors") or 0) == 0
    assert bool(logs[0].get("benchmark_leg_failed", False)) is False


def test_records_thesis_level_error_and_transition(tmp_path):
    results = [
        _thesis_result(name="Broke", error="ingest: db down", recorded=None),
        _thesis_result(name="Armed", recorded=True, transition="Warming → Armed"),
    ]
    doc = _read(
        write_cron_run_log(
            results,
            asof=date(2026, 7, 17),
            allow_live=True,
            started_at=_START,
            finished_at=_END,
            base_dir=tmp_path,
        )
    )
    assert doc["summary"]["errored"] == 1 and doc["summary"]["transitions"] == 1
    assert next(t for t in doc["theses"] if t["name"] == "Broke")["error"] == "ingest: db down"
    assert next(t for t in doc["theses"] if t["name"] == "Armed")["transition"] == "Warming → Armed"


# --- R6: already_ran_live — the catch-up guard (boot + late-wake) ---
# Every artifact below is written by the REAL writer (`write_cron_run_log`); the stamps are the UTC-aware
# instants it records (see `_utc` at the top). The guard is pure over (asof, run_at, tz) — the tests pass
# the prod defaults explicitly (22:30, America/New_York).


def _log_for(tmp_path, *, asof: date, allow_live: bool, at: datetime, catch_up: bool = False):
    write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=asof,
        allow_live=allow_live,
        started_at=at,
        finished_at=at,
        base_dir=tmp_path,
        catch_up=catch_up,
    )


def _ran(tmp_path, asof: date) -> bool:
    return already_ran_live(asof, run_at=_RUN_AT, tz=_NY, base_dir=tmp_path)


def test_already_ran_live_true_after_the_nights_live_pass(tmp_path):
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=_NIGHT_JUL17)
    assert _ran(tmp_path, _JUL17) is True


def test_already_ran_live_false_for_a_different_day(tmp_path):
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=_NIGHT_JUL17)
    assert _ran(tmp_path, date(2026, 7, 18)) is False


def test_a_NO_LIVE_run_does_NOT_count_as_ran(tmp_path):
    # THE load-bearing filter (the unchanged rule): a --no-live dev run (like the R4 page test) writes a log
    # too — even one started after RUN_AT — but must NOT suppress the real nightly catch-up, else the real run
    # silently never happens.
    _log_for(tmp_path, asof=_JUL17, allow_live=False, at=_NIGHT_JUL17)
    assert _ran(tmp_path, _JUL17) is False


def test_a_live_pass_started_BEFORE_that_nights_RUN_AT_does_NOT_count(tmp_path):
    """THE Sep 9 2026 case (MEASURED on prod): two pre-open "Run daily now" passes for as-of Sep 9, at 09:09
    and 09:15 ET, ran on Sep 8's bars; the host was then OFF at the 22:30 run. Under the old any-live-pass
    rule they reported "already ran" and masked the missed post-close pass — Sep 9's record stayed a pre-open
    call. A pass started before the night's RUN_AT is not the night's pass, so the boot catch-up RUNS.
    """
    sep9 = date(2026, 9, 9)
    _log_for(tmp_path, asof=sep9, allow_live=True, at=_utc(2026, 9, 9, 9, 9))
    _log_for(tmp_path, asof=sep9, allow_live=True, at=_utc(2026, 9, 9, 9, 15))
    assert _ran(tmp_path, sep9) is False


def test_a_live_pass_started_AT_RUN_AT_counts(tmp_path):
    # the boundary is inclusive: the scheduled fire itself starts at RUN_AT
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=_utc(2026, 7, 17, 22, 30, 0))
    assert _ran(tmp_path, _JUL17) is True


def test_a_next_morning_catch_up_pass_counts(tmp_path):
    # a late-wake / boot catch-up fires the NEXT morning (the measured 09:09 wake) with --catch-up: it started
    # after the night's RUN_AT, so it IS the night's pass — a second catch-up for that night is a no-op
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=_utc(2026, 7, 18, 9, 9), catch_up=True)
    assert _ran(tmp_path, _JUL17) is True


def test_a_NAIVE_started_at_never_reports_ran(tmp_path):
    # fail-open toward RUNNING: a naive stamp cannot be compared honestly against a market-time cutoff, so it
    # is no evidence the night ran. The writer stamps whatever it is handed — this is the artifact a caller
    # that passed a naive datetime leaves behind (22:30:01 ET as a naive UTC wall clock).
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=datetime(2026, 7, 18, 2, 30, 1))
    assert _ran(tmp_path, _JUL17) is False


def test_a_missing_or_unparseable_started_at_never_reports_ran(tmp_path):
    # the same fail-open for an artifact whose stamp was lost or mangled: written by the real writer, then
    # damaged in place (the pattern of the catch_up-key test above)
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=_NIGHT_JUL17)
    (path,) = tmp_path.glob("*.json")
    doc = _read(path)
    doc["started_at"] = "not a timestamp"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False
    del doc["started_at"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False


def test_already_ran_live_false_when_no_logs_dir(tmp_path):
    assert _ran(tmp_path / "nope", _JUL17) is False


def test_a_corrupt_artifact_never_reports_ran(tmp_path):
    # err toward RUNNING: a bad file must not make the guard falsely say "ran" and cancel a needed catch-up
    # (the write side is idempotent, so a repeated run is safe; a skipped one is the silent gap R6 fixes).
    (tmp_path / "20260717T000000Z.json").write_text("{ not json", encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False


def test_fail_open_returns_none_never_raises(tmp_path):
    # an unwritable base_dir (a FILE where the dir should be) must not raise — the cron is unaffected
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    assert (
        write_cron_run_log(
            [_thesis_result()],
            asof=date(2026, 7, 17),
            allow_live=True,
            started_at=_START,
            finished_at=_END,
            base_dir=blocker,
        )
        is None
    )


# --- G5a: the price-tape monitor's durable record + the newly-stale baseline --------------------------


def _st(ticker, *, sid=None, edge=None):
    return StaleTape(ticker=ticker, security_id=sid or uuid4(), edge=edge)


def test_payload_carries_the_per_thesis_STALE_TAPES_and_the_run_level_keys(tmp_path):
    """The artifact is the monitor's durable record and has three jobs: the per-thesis rows are the
    inventory the Admin panel renders AND the key the next run's diff reads; `tape_stale_new` is that
    diff's output for the night (so the history shows the page this run emitted); `tape_evaluated` says the
    pass actually looked. `edge` null means the name has no bars at all."""
    sid = uuid4()
    res = _thesis_result(
        recorded=True,
        tape_stale=(_st("AAA", sid=sid, edge=date(2026, 6, 1)), _st(None, edge=None)),
    )
    path = write_cron_run_log(
        [res],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
        tape_stale_new=("AAA",),
    )
    doc = _read(path)

    assert doc["tape_evaluated"] is True and doc["tape_stale_new"] == ["AAA"]
    rows = doc["theses"][0]["tape_stale"]
    assert rows[0] == {"security_id": str(sid), "ticker": "AAA", "edge": "2026-06-01"}
    assert rows[1]["ticker"] is None and rows[1]["edge"] is None  # a ticker-less, never-priced name


def test_payload_defaults_to_NOT_evaluated_with_no_stale_rows(tmp_path):
    """The quiet default: a pass that did not evaluate recency (no-live, or the monitor disabled) records
    exactly that, and a healthy universe carries an empty list rather than a missing key."""
    path = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    doc = _read(path)
    assert doc["tape_evaluated"] is False and doc["tape_stale_new"] == []
    assert doc["theses"][0]["tape_stale"] == []


def test_previous_stale_tapes_is_NONE_when_nothing_has_evaluated_yet(tmp_path):
    """NONE, not an empty set — the distinction the first-night inventory page rests on. No run home at all,
    and a home whose only artifact never evaluated recency, both read as "no baseline"."""
    assert previous_stale_tapes(base_dir=tmp_path / "nope") is None

    write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
    )  # tape_evaluated defaults False — a pass from BEFORE this monitor existed
    assert previous_stale_tapes(base_dir=tmp_path) is None


def test_previous_stale_tapes_returns_the_security_IDS_of_the_newest_evaluated_pass(tmp_path):
    """Keyed on security_id, never ticker: a ticker-less name must still take part in the diff, and a ticker
    changing under a name is half of why this monitor exists. An EMPTY set (a pass that looked and found
    nothing) is a real baseline — distinct from None."""
    a, b = uuid4(), uuid4()
    write_cron_run_log(
        [_thesis_result(recorded=True, tape_stale=(_st("AAA", sid=a), _st(None, sid=b)))],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    assert previous_stale_tapes(base_dir=tmp_path) == {str(a), str(b)}

    write_cron_run_log(  # a LATER evaluated pass with a clean universe -> an empty baseline, not None
        [_thesis_result(recorded=True)],
        asof=date(2026, 7, 20),
        allow_live=True,
        started_at=_utc(2026, 7, 20, 22, 30),
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    assert previous_stale_tapes(base_dir=tmp_path) == set()


def test_previous_stale_tapes_SKIPS_a_no_live_artifact(tmp_path):
    """The load-bearing filter: a hand-run `--no-live` pass writes an artifact too, and if it could become
    the baseline it would silence the next real page for every already-known dead tape. Its `tape_evaluated`
    is False by construction, and `mode` is checked as well — belt and suspenders, like `already_ran_live`.
    """
    a = uuid4()
    write_cron_run_log(
        [_thesis_result(recorded=True, tape_stale=(_st("AAA", sid=a),))],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    write_cron_run_log(  # a LATER cache-only pass: newest on disk, but never the baseline
        [_thesis_result(withheld_reason="no-live")],
        asof=date(2026, 7, 20),
        allow_live=False,
        started_at=_utc(2026, 7, 20, 23, 0),
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,  # even if it claimed to have looked, mode excludes it
    )
    assert previous_stale_tapes(base_dir=tmp_path) == {
        str(a)
    }  # the LIVE pass is still the baseline


def test_previous_stale_tapes_tolerates_a_MALFORMED_row_and_an_old_artifact(tmp_path):
    """Fail-open per artifact, like every other reader here: a damaged row is skipped rather than raising,
    because the worst case of a thin baseline is a repeated page — never a missed one."""
    path = write_cron_run_log(
        [_thesis_result(recorded=True, tape_stale=(_st("AAA"),))],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    doc = _read(path)
    doc["theses"][0]["tape_stale"] = ["not-a-dict", {"ticker": "NOID"}]  # no security_id
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert previous_stale_tapes(base_dir=tmp_path) == set()  # skipped, not an exception


def test_an_artifact_WITHOUT_the_TAPE_keys_still_parses(tmp_path):
    """The same back-compat rule as `catch_up` and the benchmark counts: an artifact written before these
    keys existed must read CLEAN, never broken — a strict read would raise, the caller would skip it
    fail-open, and the whole run history would silently blank after the deploy."""
    path = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
    )
    doc = _read(path)
    del doc["tape_evaluated"], doc["tape_stale_new"]
    del doc["theses"][0]["tape_stale"]
    path.write_text(json.dumps(doc), encoding="utf-8")

    logs = list_run_logs(base_dir=tmp_path)
    assert len(logs) == 1 and "tape_evaluated" not in logs[0]
    assert bool(logs[0].get("tape_evaluated", False)) is False  # the readers' .get shape
    assert tuple(logs[0].get("tape_stale_new") or ()) == ()
    assert previous_stale_tapes(base_dir=tmp_path) is None  # and it is no baseline
    assert already_ran_live(_JUL17, run_at=_RUN_AT, tz=_NY, base_dir=tmp_path) is True  # unaffected
