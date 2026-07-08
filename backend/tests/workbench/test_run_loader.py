"""The run-loader reader (``workbench/run_loader``) — pure unit, no DB, no HTTP. Lists saved draft-run
artifacts and reads one back as a plain dict, with the traversal guard proven directly (no HTTP-routing noise).
The counterpart to ``test_draft_run_log`` (the writer)."""

from __future__ import annotations

import json
from uuid import uuid4

from workbench.run_loader import list_runs, read_run


def _write(base, tid, run_id, *, placements=1, segments=1, written_at="2026-07-06T12:00:00+00:00"):
    d = base / str(tid)
    d.mkdir(parents=True, exist_ok=True)
    draft = {
        "thesis_id": str(tid),
        "segments": [{"label": f"L{i}", "descriptor": None} for i in range(segments)],
        "placements": [{"name": f"C{i}"} for i in range(placements)],
    }
    payload = {"written_at": written_at, "job_id": run_id.split("-")[-1], "draft": draft}
    (d / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return draft


def test_list_runs_newest_first_with_summary_fields(tmp_path):
    tid = uuid4()
    _write(tmp_path, tid, "20260706T100000Z-a", placements=2, segments=1)
    _write(tmp_path, tid, "20260706T120000Z-b", placements=5, segments=3)
    runs = list_runs(tid, base_dir=tmp_path)
    # filenames are UTC-timestamp-prefixed → newest first
    assert [r["run_id"] for r in runs] == ["20260706T120000Z-b", "20260706T100000Z-a"]
    assert runs[0]["placement_count"] == 5 and runs[0]["segment_count"] == 3
    assert runs[0]["job_id"] == "b" and runs[0]["written_at"] == "2026-07-06T12:00:00+00:00"


def test_list_runs_empty_for_missing_dir(tmp_path):
    assert list_runs(uuid4(), base_dir=tmp_path) == []


def test_list_runs_skips_a_corrupt_file(tmp_path):
    tid = uuid4()
    _write(tmp_path, tid, "20260706T100000Z-a")
    (tmp_path / str(tid) / "20260706T110000Z-bad.json").write_text("{not json", encoding="utf-8")
    runs = list_runs(tid, base_dir=tmp_path)
    assert [r["run_id"] for r in runs] == ["20260706T100000Z-a"]  # corrupt skipped, not fatal


def test_read_run_returns_the_inner_draft(tmp_path):
    tid = uuid4()
    draft = _write(tmp_path, tid, "20260706T100000Z-a", placements=2)
    assert read_run(tid, "20260706T100000Z-a", base_dir=tmp_path) == draft


def test_read_run_unknown_id_is_none(tmp_path):
    tid = uuid4()
    _write(tmp_path, tid, "20260706T100000Z-a")
    assert read_run(tid, "nope", base_dir=tmp_path) is None


def test_read_run_traversal_is_none(tmp_path):
    # a saved run in the thesis dir, plus a "secret" OUTSIDE it — a traversal run_id must never reach it.
    tid = uuid4()
    _write(tmp_path, tid, "20260706T100000Z-a")
    (tmp_path / "secret.json").write_text(
        json.dumps({"draft": {"thesis_id": "x"}}), encoding="utf-8"
    )
    assert read_run(tid, "../secret", base_dir=tmp_path) is None
    assert read_run(tid, "../../etc/passwd", base_dir=tmp_path) is None
