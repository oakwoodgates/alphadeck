from __future__ import annotations

import json
from datetime import date, datetime, timezone
from uuid import uuid4

from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult
from pipeline.ingest_thesis import NameResult

_START = datetime(2026, 7, 17, 22, 30, 1, tzinfo=timezone.utc)
_END = datetime(2026, 7, 17, 23, 35, 12, tzinfo=timezone.utc)


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
