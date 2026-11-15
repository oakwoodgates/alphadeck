"""V2 W3a: the background extraction's run artifact (``pipeline/extraction_run_log.py``), ``data/extraction_runs/``.

One write-only JSON file per run (the backfill CLI and the onboarding step write it in later PRs; the nightly puts
the same block in its own artifact). The ``edition_run_log`` shape: atomic, fail-open, the newest parseable file
wins, an unreadable one is skipped. Files only (redirected to tmp), no database, no network.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from pipeline import extraction_run_log as log


def _payload(started: str, line: str = "Shared facts · Nov 14: 1 extracted") -> dict:
    return {"kind": log.KIND, "run_kind": "backfill", "started_at": started, "admin_line": line}


def test_a_written_run_is_the_latest(tmp_path):
    first = log.write_extraction_run(
        _payload("2026-11-14T03:00:00+00:00", "first"), base_dir=tmp_path
    )
    second = log.write_extraction_run(
        _payload("2026-11-15T03:00:00+00:00", "second"), base_dir=tmp_path
    )
    assert first is not None and second is not None and first != second
    # the start instant to the microsecond, the run kind and a short random token: sortable by time, never shared
    assert re.fullmatch(r"20261115T030000\.000000Z-backfill-[0-9a-f]{6}\.json", second.name)
    assert sorted(p.name for p in tmp_path.glob("*.json")) == [first.name, second.name]
    assert log.latest_extraction_run(base_dir=tmp_path)["admin_line"] == "second"
    assert list(tmp_path.glob(".*.tmp")) == []  # atomic: no temp file left behind


def test_two_runs_that_start_in_the_same_second_leave_two_files(tmp_path):
    """A builder found two runs starting in the same second overwrote each other (the name was the second)."""
    same = "2026-11-15T03:00:00+00:00"
    a = log.write_extraction_run(_payload(same, "a"), base_dir=tmp_path)
    b = log.write_extraction_run(_payload(same, "b"), base_dir=tmp_path)
    assert a is not None and b is not None and a != b
    lines = {
        json.loads(p.read_text(encoding="utf-8"))["admin_line"] for p in tmp_path.glob("*.json")
    }
    assert lines == {"a", "b"}


def test_an_unreadable_or_foreign_file_is_skipped(tmp_path):
    log.write_extraction_run(_payload("2026-11-14T03:00:00+00:00", "good"), base_dir=tmp_path)
    (tmp_path / "20261116T030000Z.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "20261117T030000Z.json").write_text(json.dumps({"kind": "other"}), encoding="utf-8")
    assert log.latest_extraction_run(base_dir=tmp_path)["admin_line"] == "good"


def test_no_directory_is_no_run_and_a_failed_write_is_none(tmp_path):
    assert log.latest_extraction_run(base_dir=tmp_path / "absent") is None
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    started = datetime(2026, 11, 14, tzinfo=timezone.utc).isoformat()
    assert log.write_extraction_run(_payload(started), base_dir=blocker / "runs") is None


def test_the_default_directory_is_data_extraction_runs():
    assert log.EXTRACTION_RUNS_DIR.parts[-2:] == ("data", "extraction_runs")
