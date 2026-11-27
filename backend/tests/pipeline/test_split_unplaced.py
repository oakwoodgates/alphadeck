"""F1-1: the one reader of unplaced split filings (``pipeline/split_unplaced.py``), the nightly's run artifact
and the long-window check's record merged, and the record ``detect_splits --record`` writes. Pure file reads
and writes over a tmp dir, no DB. The nightly artifact is a cut of prod's 2026-11-25 run
(``tests/fixtures/splits/unplaced``, written before a row carried its evidence); the check rows are the 800-day
dry run's own UNPLACED lines (2026-11-26), with a stand-in security id where the log names none.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from pipeline import split_unplaced as su
from pipeline.detect_splits import SplitDetection, Unplaced

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "splits"
    / "unplaced"
    / "nightly_2026-11-25.json"
)
NIGHTLY = json.loads(FIXTURE.read_text(encoding="utf-8"))
NIGHTLY_NAME = "20261126T033001Z.json"
META_ID = uuid.UUID("826a470d-040b-4381-92c3-3c4e19cb81ad")  # the nightly artifact's META row
VRM_ID = uuid.UUID(int=7)  # a stand-in: the 800-day log names the ticker, not the security id
SKYA_ID = uuid.UUID(int=8)  # the same
NO_DATE = "no first-session or effective date stated"
CHECK_START = datetime(2026, 11, 27, 15, 0, 0, tzinfo=timezone.utc)

ROW_KEYS = {
    "security_id",
    "ticker",
    "accession",
    "form",
    "filed",
    "reason",
    "url",
    "document",
    "passage",
    "ratio_from",
    "ratio_to",
    "abstain_reason",
    "date_note",
}


def _nightly(**top) -> dict:
    doc = copy.deepcopy(NIGHTLY)
    doc.update(top)
    return doc


def _put(d: Path, name: str, doc: dict | str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return p


def _check_rows() -> list[Unplaced]:
    """The 800-day run's lines for META (also on the nightly), VRM and SKYA, as the detector builds them."""
    return [
        Unplaced(
            META_ID,
            "META",
            "0001628280-26-066700",
            "8-K",
            date(2026, 10, 21),
            f"2-for-1 forward split stated, but {NO_DATE}",
            ratio_from=Decimal(1),
            ratio_to=Decimal(2),
            date_note=NO_DATE,
        ),
        Unplaced(
            VRM_ID,
            "VRM",
            "0000950170-25-005647",
            "8-K",
            date(2025, 1, 15),
            f"1-for-5 reverse split stated, but {NO_DATE}",
            ratio_from=Decimal(5),
            ratio_to=Decimal(1),
            date_note=NO_DATE,
        ),
        Unplaced(
            VRM_ID,
            "VRM",
            "0001193125-25-000195",
            "8-K",
            date(2025, 1, 2),
            f"1-for-5 reverse split stated, but {NO_DATE}",
            ratio_from=Decimal(5),
            ratio_to=Decimal(1),
            date_note=NO_DATE,
        ),
        Unplaced(
            SKYA_ID,
            "SKYA",
            "0001493152-24-046590",
            "1-A/A",
            date(2024, 11, 18),
            "1-for-22 reverse split stated, but 3 different legal dates stated: 2023-02-03, 2024-05-30, "
            "2024-10-16",
            ratio_from=Decimal(22),
            ratio_to=Decimal(1),
            date_note="3 different legal dates stated: 2023-02-03, 2024-05-30, 2024-10-16",
        ),
    ]


def _record(records: Path, *, started_at: datetime = CHECK_START, rows=None) -> Path:
    return su.record_check(
        SplitDetection(evaluated=True, unplaced=_check_rows() if rows is None else rows),
        started_at=started_at,
        finished_at=started_at.replace(minute=40),
        asof=date(2026, 11, 27),
        sec_days=800,
        polygon_days=800,
        base_dir=records,
    )


# --- the record -----------------------------------------------------------------------------------------------


def test_the_record_is_one_file_named_by_its_start_with_the_pinned_keys(tmp_path):
    records = tmp_path / "split_unplaced"  # created by the write
    path = _record(records)
    assert path == records / "20261127T150000Z.json"
    assert sorted(p.name for p in records.iterdir()) == [path.name]  # no temp file left behind
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == {
        "kind",
        "started_at",
        "finished_at",
        "asof",
        "sec_days",
        "polygon_days",
        "unplaced",
    }
    assert (doc["kind"], doc["asof"], doc["sec_days"], doc["polygon_days"]) == (
        "detect_splits",
        "2026-11-27",
        800,
        800,
    )
    assert (doc["started_at"], doc["finished_at"]) == (
        "2026-11-27T15:00:00+00:00",
        "2026-11-27T15:40:00+00:00",
    )
    assert doc["unplaced"] == [u.to_json() for u in _check_rows()]
    assert set(doc["unplaced"][1]) == ROW_KEYS
    assert (doc["unplaced"][1]["ratio_from"], doc["unplaced"][1]["ratio_to"]) == ("5", "1")


# --- the reader -----------------------------------------------------------------------------------------------


def test_the_nightly_and_the_check_merge_newest_filing_first_and_the_nightly_row_wins(tmp_path):
    runs, records = tmp_path / "cron_runs", tmp_path / "split_unplaced"
    _put(runs, NIGHTLY_NAME, NIGHTLY)
    _record(records)
    got = su.read_unplaced(runs_dir=runs, records_dir=records)
    assert got.error is None
    assert [(r["ticker"], r["filed"], r["source"], r["seen_on"]) for r in got.rows] == [
        # same filed date: by ticker
        ("SPRC", "2026-11-25", "nightly", "2026-11-25"),
        ("TURB", "2026-11-25", "nightly", "2026-11-25"),
        ("WLDS", "2026-11-25", "nightly", "2026-11-25"),
        ("META", "2026-10-21", "nightly", "2026-11-25"),  # on both: the nightly's row, once
        ("EVGN", "2026-09-28", "nightly", "2026-11-25"),
        ("VRM", "2025-01-15", "check", "2026-11-27"),
        ("VRM", "2025-01-02", "check", "2026-11-27"),
        ("SKYA", "2024-11-18", "check", "2026-11-27"),
    ]
    assert all(set(r) == ROW_KEYS | {"source", "seen_on"} for r in got.rows)
    vrm = got.rows[5]
    assert (vrm["security_id"], vrm["accession"], vrm["form"]) == (
        str(VRM_ID),
        "0000950170-25-005647",
        "8-K",
    )
    assert (vrm["ratio_from"], vrm["ratio_to"], vrm["date_note"]) == ("5", "1", NO_DATE)
    assert got.runs == [
        {
            "source": "nightly",
            "asof": "2026-11-25",
            "started_at": "2026-11-26T03:30:01.884083+00:00",
            "sec_days": None,
            "rows": 5,
        },
        {
            "source": "check",
            "asof": "2026-11-27",
            "started_at": "2026-11-27T15:00:00+00:00",
            "sec_days": 800,
            "rows": 4,
        },
    ]


def test_an_artifact_written_before_rows_carried_evidence_reads_null(tmp_path):
    runs = tmp_path / "cron_runs"
    _put(runs, NIGHTLY_NAME, NIGHTLY)
    got = su.read_unplaced(runs_dir=runs, records_dir=tmp_path / "none")
    meta = next(r for r in got.rows if r["ticker"] == "META")
    assert meta["reason"] == f"2-for-1 forward split stated, but {NO_DATE}"
    for k in (
        "url",
        "document",
        "passage",
        "ratio_from",
        "ratio_to",
        "abstain_reason",
        "date_note",
    ):
        assert meta[k] is None, k
    assert [r["source"] for r in got.runs] == ["nightly"] and got.error is None


def test_running_crashed_no_live_and_unevaluated_artifacts_are_skipped(tmp_path):
    runs = tmp_path / "cron_runs"
    _put(runs, "20261125T033001Z.json", NIGHTLY)  # the oldest: the one that qualifies
    newer = {
        "20261126T033001Z.json": _nightly(status="running", finished_at=None),
        "20261127T033001Z.json": _nightly(status="crashed"),
        "20261128T033001Z.json": _nightly(mode="no-live"),
        "20261129T033001Z.json": _nightly(split_detect={"evaluated": False, "unplaced": []}),
        "20261130T033001Z.json": {k: v for k, v in NIGHTLY.items() if k != "split_detect"},
    }
    for name, doc in newer.items():
        if name == "20261126T033001Z.json":
            doc["split_detect"]["unplaced"] = doc["split_detect"]["unplaced"][:1]
        _put(runs, name, doc)
    got = su.read_unplaced(runs_dir=runs, records_dir=tmp_path / "none")
    assert got.error is None
    assert len(got.rows) == 5 and got.runs[0]["started_at"] == NIGHTLY["started_at"]


def test_the_reader_stops_at_the_first_qualifying_artifact(tmp_path, monkeypatch):
    runs = tmp_path / "cron_runs"
    _put(runs, "20261122T033001Z.json", NIGHTLY)
    _put(runs, "20261123T033001Z.json", _nightly(asof="2026-11-23"))  # the newest that qualifies
    _put(runs, "20261124T033001Z.json", _nightly(status="running", finished_at=None))
    _put(runs, "20261125T033001Z.json", _nightly(mode="no-live"))
    _put(runs, "20261126T033001Z.json", _nightly(status="crashed"))
    read: list[str] = []
    real = su._load

    def spy(path: Path):
        read.append(path.name)
        return real(path)

    monkeypatch.setattr(su, "_load", spy)
    got = su.read_unplaced(runs_dir=runs, records_dir=tmp_path / "none")
    assert read == [
        "20261126T033001Z.json",
        "20261125T033001Z.json",
        "20261124T033001Z.json",
        "20261123T033001Z.json",
    ]
    assert {r["seen_on"] for r in got.rows} == {"2026-11-23"}


def test_only_the_newest_check_record_is_read(tmp_path):
    records = tmp_path / "split_unplaced"
    _record(records, started_at=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc))
    _record(records, rows=_check_rows()[3:])  # 11-27: SKYA only
    got = su.read_unplaced(runs_dir=tmp_path / "none", records_dir=records)
    assert [(r["ticker"], r["source"]) for r in got.rows] == [("SKYA", "check")]
    assert [(r["source"], r["started_at"], r["rows"]) for r in got.runs] == [
        ("check", "2026-11-27T15:00:00+00:00", 1)
    ]


def test_a_corrupt_newest_file_is_skipped_and_named(tmp_path):
    runs, records = tmp_path / "cron_runs", tmp_path / "split_unplaced"
    _put(runs, NIGHTLY_NAME, NIGHTLY)
    _put(runs, "20261127T033001Z.json", '{"status": "finished", "split_de')  # cut mid-write
    _record(records)
    got = su.read_unplaced(runs_dir=runs, records_dir=records)
    assert len(got.rows) == 8 and [r["source"] for r in got.runs] == ["nightly", "check"]
    assert got.error is not None and "20261127T033001Z.json" in got.error


def test_nothing_readable_is_an_error_with_no_rows(tmp_path):
    runs, records = tmp_path / "cron_runs", tmp_path / "split_unplaced"
    _put(runs, NIGHTLY_NAME, "not json")
    _put(records, "20261127T150000Z.json", "[1, 2]")  # JSON, but not a record
    got = su.read_unplaced(runs_dir=runs, records_dir=records)
    assert (got.rows, got.runs) == ([], [])
    assert NIGHTLY_NAME in got.error and "20261127T150000Z.json" in got.error


def test_no_artifacts_and_no_records_is_empty_and_no_error(tmp_path):
    got = su.read_unplaced(runs_dir=tmp_path / "a", records_dir=tmp_path / "b")
    assert (got.rows, got.runs, got.error) == ([], [], None)


def test_the_default_dirs_are_the_cron_runs_home_and_the_data_volume(tmp_path, monkeypatch):
    """The nightly side follows ``cron_run_log._DEFAULT_CRON_RUNS`` (which every test conftest pins), the check
    side ``UNPLACED_DIR``, both read when called."""
    from pipeline import cron_run_log

    assert su.UNPLACED_DIR.parts[-2:] == ("data", "split_unplaced")
    runs, records = tmp_path / "cron_runs", tmp_path / "split_unplaced"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", runs)
    monkeypatch.setattr(su, "UNPLACED_DIR", records)
    _put(runs, NIGHTLY_NAME, NIGHTLY)
    path = su.record_check(
        SplitDetection(evaluated=True, unplaced=_check_rows()[1:2]),
        started_at=CHECK_START,
        finished_at=CHECK_START,
        asof=date(2026, 11, 27),
        sec_days=800,
        polygon_days=800,
    )
    assert path.parent == records
    got = su.read_unplaced()
    assert [r["source"] for r in got.runs] == ["nightly", "check"] and len(got.rows) == 6
