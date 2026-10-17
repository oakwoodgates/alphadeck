from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from domain.feed_kinds import StaleFeedLabel
from pipeline.cron_run_log import (
    already_ran_live,
    is_finished,
    list_run_logs,
    mark_run_crashed,
    previous_stale_tapes,
    run_status,
    write_cron_run_log,
    write_run_start,
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
    assert doc["status"] == "finished"  # M1 — the finalized artifact
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
        "skipped": 0,  # M2 — theses a catch-up left alone (none on a scheduled pass)
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


def _log_for_results(tmp_path, results, *, asof: date, at: datetime, allow_live: bool = True):
    """``_log_for``'s sibling for the G2b guard tests: the same REAL writer, but the caller supplies the
    per-thesis results, because what the guard now reads is whether any thesis RECORDED."""
    path = write_cron_run_log(
        results,
        asof=asof,
        allow_live=allow_live,
        started_at=at,
        finished_at=at,
        base_dir=tmp_path,
    )
    assert path is not None
    return path


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


def _st(
    ticker, *, sid=None, edge=None, kind="price", closed_at=None, renamed=False, edgar_tickers=None
):
    return StaleTape(
        ticker=ticker,
        security_id=sid or uuid4(),
        edge=edge,
        kind=kind,
        closed_at=closed_at,
        renamed=renamed,
        edgar_tickers=edgar_tickers,
    )


def test_payload_carries_the_per_thesis_STALE_TAPES_and_the_run_level_keys(tmp_path):
    """The artifact is the monitor's durable record and has four jobs: the per-thesis rows are the
    inventory the Admin panel renders AND the key the next run's diff reads; `tape_stale_new` is that
    diff's output for the night (so the history shows the page this run emitted); `tape_evaluated` says the
    pass actually looked; and the two threshold keys say what it judged under. `edge` null means the name
    has no data at all, and `kind` says WHICH feed stopped."""
    sid, fund_sid, closed_sid = uuid4(), uuid4(), uuid4()
    res = _thesis_result(
        recorded=True,
        tape_stale=(
            _st("AAA", sid=sid, edge=date(2026, 6, 1)),
            _st(None, edge=None),
            _st("ETF1", sid=fund_sid, edge=date(2026, 6, 2), kind="fund_shares"),
            # G5b — a CLOSED (delisted) row carries its delisting-form date so the panel renders it quietly
            _st(
                "GONE",
                sid=closed_sid,
                edge=date(2026, 6, 3),
                closed_at=date(2026, 6, 5),
                edgar_tickers=(),
            ),
            # T1 — a RENAMED repair row carries the marker and EDGAR's current symbols
            _st("VRME", edge=date(2026, 6, 4), renamed=True, edgar_tickers=("OPNW",)),
        ),
    )
    path = write_cron_run_log(
        [res],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
        tape_stale_new=(StaleFeedLabel("price", "AAA"), StaleFeedLabel("fund_shares", "ETF1")),
        tape_stale_days=5,
        fund_shares_stale_days=7,
    )
    doc = _read(path)

    assert doc["tape_evaluated"] is True
    assert doc["tape_stale_new"] == [
        {"kind": "price", "label": "AAA"},
        {"kind": "fund_shares", "label": "ETF1"},
    ]
    assert doc["tape_stale_days"] == 5 and doc["fund_shares_stale_days"] == 7
    rows = doc["theses"][0]["tape_stale"]
    assert rows[0] == {
        "security_id": str(sid),
        "ticker": "AAA",
        "edge": "2026-06-01",
        "kind": "price",
        "closed_at": None,  # G5b — a plain feed-gap row: not closed
        "renamed": False,  # T1 — ...and not renamed
        "edgar_tickers": None,  # T1 — EDGAR's list not read for this row (null, not [])
        "unpriceable": False,  # Q8 — it HAS bars (an edge), so it is not the never-priced class
        "acknowledged": False,  # Q8 — ...and nothing the operator acknowledged
    }
    assert rows[1]["ticker"] is None and rows[1]["edge"] is None  # a ticker-less, never-priced name
    assert rows[1]["unpriceable"] is True  # Q8 — no bar ever, neither closed nor renamed
    assert rows[2]["kind"] == "fund_shares" and rows[2]["edge"] == "2026-06-02"
    # G5b — the CLOSED row serializes its delisting-form date, so the panel reads "closed — stopped
    # trading <date>" instead of the loud "stale — repair"
    assert rows[3]["ticker"] == "GONE" and rows[3]["closed_at"] == "2026-06-05"
    assert rows[3]["edgar_tickers"] == []  # EDGAR read, and lists none
    # T1 — the renamed repair row: marker + EDGAR's symbols, not closed
    assert rows[4]["ticker"] == "VRME" and rows[4]["renamed"] is True
    assert rows[4]["edgar_tickers"] == ["OPNW"] and rows[4]["closed_at"] is None


def test_payload_records_a_DISABLED_monitors_threshold_as_zero(tmp_path):
    """F6: 0 is a REAL stored value — that feed's monitor was off for the pass — so the panel's reader must
    distinguish it from "the artifact does not carry the key". Pinned here on the writing side."""
    path = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=date(2026, 9, 8),
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
        tape_stale_days=5,
        fund_shares_stale_days=0,
    )
    doc = _read(path)
    assert doc["tape_stale_days"] == 5 and doc["fund_shares_stale_days"] == 0


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
    """Keyed on kind + security_id, never ticker: a ticker-less name must still take part in the diff, a
    ticker changing under a name is half of why this monitor exists, and one security can be stale on both
    feeds with a different repair for each. An EMPTY set (a pass that looked and found nothing) is a real
    baseline — distinct from None."""
    a, b, f = uuid4(), uuid4(), uuid4()
    write_cron_run_log(
        [
            _thesis_result(
                recorded=True,
                tape_stale=(
                    _st("AAA", sid=a),
                    _st(None, sid=b),
                    _st("ETF1", sid=f, kind="fund_shares"),
                ),
            )
        ],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    assert previous_stale_tapes(base_dir=tmp_path) == {
        f"price:{a}",
        f"price:{b}",
        f"fund_shares:{f}",
    }

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


def test_previous_stale_tapes_LEAVES_OUT_closed_rows_so_a_reclassification_is_news(tmp_path):
    """T1 — a CLOSED row never paged, so it must not sit in the baseline as "already told": the night a
    name is reclassified closed → repair (VRME's, the first pass under the proximity rule), its key is
    absent here and the diff pages it once. A renamed REPAIR row IS in the baseline like any repair row,
    so it never re-pages."""
    repair, closed, renamed = uuid4(), uuid4(), uuid4()
    write_cron_run_log(
        [
            _thesis_result(
                recorded=True,
                tape_stale=(
                    _st("DEAD", sid=repair),
                    _st("GONE", sid=closed, closed_at=date(2025, 6, 23)),
                    _st("HLLK", sid=renamed, renamed=True, edgar_tickers=("SDCO",)),
                ),
            )
        ],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    assert previous_stale_tapes(base_dir=tmp_path) == {f"price:{repair}", f"price:{renamed}"}


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
        f"price:{a}"
    }  # the LIVE pass is still the baseline


def test_previous_stale_tapes_reads_a_PRE_KIND_row_as_a_PRICE_row(tmp_path):
    """THE DEPLOY-NIGHT TEST. Every stale row written before the fund-shares feed existed carries no
    `kind`, and those rows are price rows. If the baseline read them as anything else, the first pass
    after this ships would find none of its known-dead price tapes in the baseline and re-page the ENTIRE
    existing inventory as if new — the exact nightly crying-wolf this diff exists to prevent."""
    a = uuid4()
    path = write_cron_run_log(
        [_thesis_result(recorded=True, tape_stale=(_st("AAA", sid=a),))],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_END,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    doc = _read(path)
    del doc["theses"][0]["tape_stale"][0]["kind"]  # an artifact from before the field existed
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert previous_stale_tapes(base_dir=tmp_path) == {f"price:{a}"}


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


# --- G2b: the guard credits only a pass that RECORDED for some thesis --------------------------------
#
# `already_ran_live` used to credit any live post-RUN_AT artifact REGARDLESS of health, so a pass that
# COMPLETED with every thesis withheld or errored left an artifact that blocked both the sidecar's retry and
# any later boot catch-up — the night kept no honest post-close row and nothing ever tried again.


def test_a_COMPLETED_pass_that_recorded_NOTHING_is_not_evidence(tmp_path):
    """THE G2b CASE: the pass ran to completion and wrote its artifact, but every thesis was WITHHELD (the
    DB went away after connect, or the User-Agent was empty so every name errored). Nothing was recorded, so
    the night did not happen — the guard must let the retry run."""
    _log_for_results(
        tmp_path,
        [_thesis_result(withheld_reason="total ingest failure")],
        asof=_JUL17,
        at=_NIGHT_JUL17,
    )
    assert _ran(tmp_path, _JUL17) is False


def test_a_pass_whose_every_thesis_ERRORED_in_the_call_step_is_not_evidence(tmp_path):
    """The other nothing-recorded shape: the ingest was fine but the call step raised for every thesis, so
    `recorded` is None everywhere. Same verdict — no honest row for the night."""
    _log_for_results(
        tmp_path,
        [_thesis_result(recorded=None, error="call: boom")],
        asof=_JUL17,
        at=_NIGHT_JUL17,
    )
    assert _ran(tmp_path, _JUL17) is False


def test_a_PARTIAL_pass_with_one_recorded_thesis_IS_evidence(tmp_path):
    """The retry must NOT re-run a pass that already recorded. `python -m pipeline.daily` exits non-zero when
    ANY thesis errors, so a single bad thesis out of several trips the shell's retry — and this is what keeps
    that retry cheap: one recorded thesis means the night happened, so the `--catch-up` guard no-ops and the
    ~65-minute ingest is not repeated."""
    _log_for_results(
        tmp_path,
        [_thesis_result(recorded=True), _thesis_result(recorded=None, error="call: boom")],
        asof=_JUL17,
        at=_NIGHT_JUL17,
    )
    assert _ran(tmp_path, _JUL17) is True


def test_an_UNCHANGED_only_pass_IS_evidence(tmp_path):
    """`recorded is False` = the call was assembled and compared and matched the prior row, so no row was
    appended. That is the COMMON healthy-quiet night and absolutely counts as "the night ran" — reading it as
    nothing-recorded would re-run the whole ingest after every uneventful evening."""
    _log_for_results(tmp_path, [_thesis_result(recorded=False)], asof=_JUL17, at=_NIGHT_JUL17)
    assert _ran(tmp_path, _JUL17) is True


def test_an_artifact_with_NO_theses_list_is_not_evidence(tmp_path):
    """Fail-open toward RUNNING, like every other branch of the guard: an artifact damaged or written before
    the per-thesis schema existed is no evidence, so a needed catch-up still fires. A repeated run is safe
    (`record_if_changed`); a skipped one is the silent gap the guard exists to close."""
    path = _log_for_results(tmp_path, [_thesis_result(recorded=True)], asof=_JUL17, at=_NIGHT_JUL17)
    assert _ran(tmp_path, _JUL17) is True  # ...before the damage
    doc = _read(path)
    del doc["theses"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False


def test_an_artifact_with_ZERO_theses_is_not_evidence(tmp_path):
    """A fresh install with no theses: the pass recorded nothing because there was nothing to record. The
    guard credits nothing, so a catch-up re-fires — correct, and instant over an empty universe."""
    _log_for_results(tmp_path, [], asof=_JUL17, at=_NIGHT_JUL17)
    assert _ran(tmp_path, _JUL17) is False


def test_a_NON_BOOL_recorded_value_is_not_mistaken_for_an_outcome(tmp_path):
    """`isinstance(..., bool)`, not `in (True, False)`: in Python `1 in (True, False)` is True, so an integer
    that wandered into the field would otherwise read as "recorded"."""
    path = _log_for_results(tmp_path, [_thesis_result(recorded=True)], asof=_JUL17, at=_NIGHT_JUL17)
    doc = _read(path)
    doc["theses"][0]["recorded"] = 1
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False


def test_the_recorded_test_is_ANDed_with_the_RUN_AT_cutoff_not_a_replacement(tmp_path):
    """Both rules still apply: a pass that recorded but started BEFORE the night's RUN_AT (a pre-open "Run
    daily now") is still not the night's pass."""
    sep9 = date(2026, 9, 9)
    _log_for_results(
        tmp_path, [_thesis_result(recorded=True)], asof=sep9, at=_utc(2026, 9, 9, 9, 15)
    )
    assert already_ran_live(sep9, run_at=_RUN_AT, tz=_NY, base_dir=tmp_path) is False


# --- O1: the per-name errors on the artifact + the newly-failing baseline -----------------------------


def test_payload_persists_WHY_each_name_errored_by_leg(tmp_path):
    """The count (`names_errored`) used to be ALL the artifact kept; the reasons were gone by morning. Now
    each thesis carries one row per failed LEG, keyed on the security (the ticker can change under it).
    """
    sid = uuid4()
    bad = _name(
        ticker="HLLK",
        security_id=sid,
        error="price: 404; form4: parse",
        leg_errors=(("price", "HTTP 404 Not Found"), ("form4", "parse")),
    )
    path = write_cron_run_log(
        [_thesis_result(recorded=True, ingested=[bad, _name(ticker="OK")])],
        asof=_JUL17,
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    t = _read(path)["theses"][0]
    assert t["names_errored"] == 1  # the count is unchanged
    assert t["name_errors"] == [
        {"security_id": str(sid), "ticker": "HLLK", "leg": "price", "error": "HTTP 404 Not Found"},
        {"security_id": str(sid), "ticker": "HLLK", "leg": "form4", "error": "parse"},
    ]


def test_a_name_with_only_the_joined_error_is_REPORTED_under_the_ingest_leg(tmp_path):
    """No silent drop (#9): a NameResult that carries `error` but no structured legs still yields a row."""
    path = write_cron_run_log(
        [_thesis_result(recorded=True, ingested=[_name(ticker="X", error="boom")])],
        asof=_JUL17,
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    (row,) = _read(path)["theses"][0]["name_errors"]
    assert row["leg"] == "ingest" and row["error"] == "boom"


def test_a_long_multiline_error_is_CLIPPED_to_one_line(tmp_path):
    from pipeline.name_errors import MAX_ERROR_CHARS

    msg = "line one\nline two " + "x" * 1000
    path = write_cron_run_log(
        [_thesis_result(ingested=[_name(error="e", leg_errors=(("price", msg),))])],
        asof=_JUL17,
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    err = _read(path)["theses"][0]["name_errors"][0]["error"]
    assert "\n" not in err and len(err) <= MAX_ERROR_CHARS and err.startswith("line one line two")


def test_previous_name_errors_NONE_before_any_evaluated_pass_then_the_KEYS(tmp_path):
    """NONE (the first pass after O1 deploys pages the inventory once) vs a set of `security_id:leg` keys.
    A live artifact that predates O1 (no `name_errors_evaluated`) is not a baseline — read as "nothing
    errored" it would re-page every known-dead name; a --no-live pass is never a baseline either."""
    from pipeline.cron_run_log import previous_name_errors

    assert previous_name_errors(base_dir=tmp_path) is None
    sid = uuid4()
    failing = [
        _thesis_result(
            recorded=True,
            ingested=[_name(security_id=sid, error="e", leg_errors=(("price", "404"),))],
        )
    ]
    write_cron_run_log(  # a PRE-O1 live pass (the key defaults False)
        failing,
        asof=_JUL17,
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
    )
    assert previous_name_errors(base_dir=tmp_path) is None
    write_cron_run_log(  # an evaluated live pass
        failing,
        asof=_JUL17,
        allow_live=True,
        started_at=_utc(2026, 7, 18, 22, 30),
        finished_at=_END,
        base_dir=tmp_path,
        name_errors_evaluated=True,
    )
    assert previous_name_errors(base_dir=tmp_path) == {f"{sid}:price"}
    write_cron_run_log(  # a NEWER no-live pass — skipped as a baseline
        [_thesis_result(withheld_reason="no-live")],
        asof=_JUL17,
        allow_live=False,
        started_at=_utc(2026, 7, 19, 22, 30),
        finished_at=_END,
        base_dir=tmp_path,
        name_errors_evaluated=False,
    )
    assert previous_name_errors(base_dir=tmp_path) == {f"{sid}:price"}


# --- I1/I2 + V1: the identity, the env fingerprint and the audit ride the artifact --------------------


def test_payload_carries_the_RUN_IDENTITY_env_and_AUDIT(tmp_path):
    from pipeline.name_errors import NameIngestError
    from pipeline.self_audit import AuditResult

    sid = uuid4()
    path = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=_JUL17,
        allow_live=True,
        started_at=_START,
        finished_at=_END,
        base_dir=tmp_path,
        code_sha="2f3cdfa",
        config_hash="ab" * 32,
        run_kind="cron",
        env={"tz": "America/New_York", "user_agent_set": True, "notifier": "SlackNotifier"},
        name_errors_evaluated=True,
        name_errors_new=(NameIngestError(sid, "WONDF", "price", "no data"),),
        name_errors_known=5,
        audit=(AuditResult("code_sha_known", True, "code_sha 2f3cdfa"),),
    )
    doc = _read(path)
    assert (doc["code_sha"], doc["config_hash"], doc["run_kind"]) == ("2f3cdfa", "ab" * 32, "cron")
    assert doc["env"]["notifier"] == "SlackNotifier"
    assert doc["name_errors_new"] == [{"security_id": str(sid), "ticker": "WONDF", "leg": "price"}]
    assert doc["name_errors_known"] == 5
    assert doc["audit"] == [{"check": "code_sha_known", "ok": True, "detail": "code_sha 2f3cdfa"}]


def test_payload_identity_DEFAULTS_are_honest_nulls(tmp_path):
    """Nothing fabricated: a caller that did not supply the identity writes nulls, never a guess."""
    doc = _read(
        write_cron_run_log(
            [], asof=_JUL17, allow_live=True, started_at=_START, finished_at=_END, base_dir=tmp_path
        )
    )
    assert doc["code_sha"] is None and doc["run_kind"] is None and doc["env"] is None
    assert doc["audit"] == [] and doc["name_errors_new"] == []


# --- M1: the START artifact, finalized in place; the guard counts only a FINISHED pass ---------------------
# Every artifact below comes from the REAL writers: `write_run_start` (what a pass leaves the moment it holds
# the run lock — and ALL a hard-killed pass leaves), `write_cron_run_log` (the finalize), `mark_run_crashed`
# (the crash path). The 10-12 shape: the 22:30 pass started, recorded 4 of 12 theses, and the VM died at
# 22:42 — on disk that is a start artifact for the night that never finalized.


def _start(tmp_path, *, asof: date = _JUL17, at: datetime = _NIGHT_JUL17, **kw):
    path = write_run_start(asof=asof, allow_live=True, started_at=at, base_dir=tmp_path, **kw)
    assert path is not None
    return path


def test_a_pass_writes_a_START_artifact_and_FINALIZES_the_same_file(tmp_path):
    start = _start(tmp_path, run_kind="cron", code_sha="2f3cdfa", config_hash="ab" * 32)
    doc = _read(start)
    assert doc["status"] == "running" and doc["finished_at"] is None
    assert (doc["asof"], doc["mode"], doc["run_kind"], doc["code_sha"]) == (
        "2026-07-17",
        "live",
        "cron",
        "2f3cdfa",
    )
    # nothing a reader could mistake for an outcome
    assert "theses" not in doc and "summary" not in doc and "tape_evaluated" not in doc
    final = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_utc(2026, 7, 17, 22, 41),
        base_dir=tmp_path,
    )
    assert final == start  # one file per pass
    assert sorted(p.name for p in tmp_path.glob("*.json")) == [start.name]
    assert _read(final)["status"] == "finished"
    assert not list(tmp_path.glob(".*.tmp"))  # the atomic write left no temp behind


def test_THE_GUARD_TRAP_a_RUNNING_artifact_never_makes_the_catch_up_skip_the_night(tmp_path):
    """The load-bearing M1 rule. The killed pass's start artifact is live, for the night, and started 1 s
    after RUN_AT — every condition the guard used to check — yet it must NOT credit the night, or the
    killed night blocks its own recovery. Pinned on the STATUS, not on the accident that a start artifact
    carries no `theses`: the same running artifact carrying a recorded thesis still does not count.
    """
    path = _start(tmp_path)
    assert _ran(tmp_path, _JUL17) is False
    doc = _read(
        path
    )  # a running artifact that DID carry per-thesis outcomes (a future progress write)
    doc["theses"] = [{"id": str(uuid4()), "recorded": True}]
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False
    # crashed: the same — the pass did not finish
    doc["status"] = "crashed"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False
    # and once the catch-up FINISHES, the night is credited (a separate, later pass)
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=_utc(2026, 7, 18, 9, 5))
    assert _ran(tmp_path, _JUL17) is True


def test_a_LEGACY_artifact_with_no_status_still_satisfies_the_guard(tmp_path):
    """Back-compat: every artifact written before M1 has no `status` and was only ever written at the END
    of a completed pass, so it reads FINISHED — the deploy must not reopen every recorded night."""
    path = write_cron_run_log(
        [_thesis_result(recorded=False)],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_NIGHT_JUL17,
        base_dir=tmp_path,
    )
    doc = _read(path)
    del doc["status"]  # exactly a pre-M1 artifact
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run_status(doc) == "finished" and is_finished(doc)
    assert _ran(tmp_path, _JUL17) is True
    # a damaged status never credits a night (err toward running)
    doc["status"] = None
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _ran(tmp_path, _JUL17) is False


def test_mark_run_crashed_marks_ONLY_this_passs_RUNNING_file(tmp_path):
    path = _start(tmp_path)
    crashed_at = _utc(2026, 7, 17, 22, 42)
    marked = mark_run_crashed(
        started_at=_NIGHT_JUL17,
        crashed_at=crashed_at,
        error="RuntimeError: boom",
        base_dir=tmp_path,
    )
    assert marked == path
    doc = _read(path)
    assert (doc["status"], doc["error"]) == ("crashed", "RuntimeError: boom")
    assert doc["crashed_at"] == crashed_at.isoformat()
    # a FINALIZED pass that raised afterwards keeps its finished record
    fin_at = _utc(2026, 7, 20, 22, 30, 2)
    write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=date(2026, 7, 20),
        allow_live=True,
        started_at=fin_at,
        finished_at=fin_at,
        base_dir=tmp_path,
    )
    assert (
        mark_run_crashed(started_at=fin_at, crashed_at=fin_at, error="x", base_dir=tmp_path) is None
    )
    assert _read(tmp_path / f"{fin_at.strftime('%Y%m%dT%H%M%SZ')}.json")["status"] == "finished"
    # no file at all -> nothing to mark (fail-open, no raise)
    nowhere = _utc(2026, 7, 21, 22, 30)
    assert (
        mark_run_crashed(started_at=nowhere, crashed_at=nowhere, error="x", base_dir=tmp_path)
        is None
    )


def test_the_start_write_NEVER_clobbers_another_passs_artifact(tmp_path):
    """The name is the started-at second; at start time a file there can only be ANOTHER pass's. Two passes
    cannot start in one second under the run lock, but if one ever did, the earlier pass's finished record
    (and the baselines read from it) must survive — the later pass just runs without a start record.
    """
    path = write_cron_run_log(
        [_thesis_result(recorded=True)],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_NIGHT_JUL17,
        base_dir=tmp_path,
    )
    again = write_run_start(
        asof=_JUL17, allow_live=True, started_at=_NIGHT_JUL17, base_dir=tmp_path
    )
    assert again is None
    assert _read(path)["status"] == "finished"


def test_a_RUNNING_artifact_is_never_a_baseline_and_a_PARTIAL_one_never_drops_a_skipped_thesis(
    tmp_path,
):
    """A start artifact is no baseline (nothing evaluated, never finished). An M2 catch-up that SKIPPED a
    thesis counts only for the theses it COVERED (the per-thesis carry-forward, ``carried_inventory``): the
    skipped thesis keeps its entry from the full pass before it, so its known-dead tape is still "already
    told" and the next pass does not re-page it. (Until 2026-10-17 the partial pass was excluded outright;
    the catch-up sequences are pinned in ``test_catchup_baseline.py``.)"""
    sid = uuid4()
    aaa_thesis = uuid4()
    write_cron_run_log(  # the full baseline: AAA is a known-dead tape
        [
            _thesis_result(
                thesis_id=aaa_thesis,
                tape_stale=(StaleTape(security_id=sid, ticker="AAA", edge=None),),
            )
        ],
        asof=_JUL17,
        allow_live=True,
        started_at=_NIGHT_JUL17,
        finished_at=_NIGHT_JUL17,
        base_dir=tmp_path,
        tape_evaluated=True,
    )
    write_cron_run_log(  # a later catch-up that skipped AAA's thesis: evaluated, a partial roster
        [
            _thesis_result(thesis_id=aaa_thesis, skipped="on-time call-of-record already recorded"),
            _thesis_result(recorded=True),
        ],
        asof=date(2026, 7, 20),
        allow_live=True,
        started_at=_utc(2026, 7, 21, 9, 0),
        finished_at=_utc(2026, 7, 21, 9, 5),
        base_dir=tmp_path,
        tape_evaluated=True,
        catch_up=True,
    )
    _start(tmp_path, asof=date(2026, 7, 21), at=_utc(2026, 7, 21, 22, 30, 1))  # newest: in flight
    assert previous_stale_tapes(base_dir=tmp_path) == {f"price:{sid}"}  # AAA's thesis, carried


def test_a_SKIPPED_thesis_counts_as_the_night_recorded_for_the_guard(tmp_path):
    """M2 twin of `daily._failed_to_record`: a catch-up whose every thesis was skipped (their on-time rows
    already stood) HAS the night — a later catch-up must not re-run it."""
    _log_for_results(
        tmp_path,
        [_thesis_result(skipped="on-time call-of-record already recorded 2026-07-17 22:41 EDT")],
        asof=_JUL17,
        at=_utc(2026, 7, 18, 9, 0),
    )
    assert _ran(tmp_path, _JUL17) is True


# --- the per-thesis carry-forward's BOUNDS (the catch-up sequences: test_catchup_baseline.py) ----------


def _evaluated(tmp_path, results, *, at: datetime, catch_up: bool = False):
    """One finished live pass that evaluated both per-thesis inventories."""
    write_cron_run_log(
        results,
        asof=at.date(),
        allow_live=True,
        started_at=at,
        finished_at=at,
        base_dir=tmp_path,
        tape_evaluated=True,
        name_errors_evaluated=True,
        catch_up=catch_up,
    )


def test_the_carry_forward_STOPS_at_the_newest_FULL_roster_pass(tmp_path):
    """A full-roster pass covered every thesis then active, so nothing older is newer evidence: thesis A
    flagged N on night 1, was ARCHIVED for night 2's full pass (absent from its roster), and is back on a
    catch-up's roster (skipped — its on-time row stood). A is carried from nothing: night 2 is where the
    look-back ends, so N is not "already told" and pages if A flags it again."""
    a, b, n = uuid4(), uuid4(), uuid4()
    _evaluated(
        tmp_path,
        [_thesis_result(thesis_id=a, tape_stale=(_st("NNN", sid=n),)), _thesis_result(thesis_id=b)],
        at=_utc(2026, 7, 14, 22, 30),
    )
    _evaluated(tmp_path, [_thesis_result(thesis_id=b)], at=_utc(2026, 7, 15, 22, 30))
    _evaluated(
        tmp_path,
        [_thesis_result(thesis_id=a, skipped="on-time row stands"), _thesis_result(thesis_id=b)],
        at=_utc(2026, 7, 17, 9, 0),
        catch_up=True,
    )
    assert previous_stale_tapes(base_dir=tmp_path) == set()


def test_carried_inventory_reads_the_PAIRING_and_the_roster_order(tmp_path):
    """The carried view the Admin panels walk: the newest pass's roster order, each entry paired with the
    pass it came from; ``None`` with no qualifying pass at all (a --no-live pass, a not-evaluated one and a
    killed pass's start artifact never qualify)."""
    from pipeline.cron_run_log import carried_inventory

    _log_for(tmp_path, asof=_JUL17, allow_live=False, at=_utc(2026, 7, 13, 22, 30))  # no-live
    _log_for(tmp_path, asof=_JUL17, allow_live=True, at=_utc(2026, 7, 13, 22, 31))  # not evaluated
    _start(tmp_path, asof=_JUL17, at=_utc(2026, 7, 13, 22, 32))  # killed: still "running"
    assert (
        carried_inventory(list_run_logs(base_dir=tmp_path), evaluated_key="tape_evaluated") is None
    )

    a, b = uuid4(), uuid4()
    _evaluated(
        tmp_path, [_thesis_result(thesis_id=a), _thesis_result(thesis_id=b)], at=_NIGHT_JUL17
    )
    _evaluated(
        tmp_path,
        [_thesis_result(thesis_id=b), _thesis_result(thesis_id=a, skipped="on-time row stands")],
        at=_utc(2026, 7, 18, 9, 0),
        catch_up=True,
    )
    inv = carried_inventory(list_run_logs(base_dir=tmp_path), evaluated_key="tape_evaluated")
    assert [(t["id"], i) for t, i in inv.theses] == [(str(b), 0), (str(a), 1)]
    assert inv.newest["catch_up"] is True and len(inv.passes) == 2
    assert [t["id"] for t in inv.as_doc()["theses"]] == [str(b), str(a)]
