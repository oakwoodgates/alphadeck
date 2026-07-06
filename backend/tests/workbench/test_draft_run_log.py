"""The run-of-record writer (``workbench/draft_run_log``) — pure unit, no DB, no HTTP: the artifact lands at
the documented path, carries the run's inputs (thesis / term set as used / dials) plus the draft (round-trip
via ``model_dump(mode="json")``), and FAILS OPEN on any fault (the guardrail: a file write must never cost the
draft). The end-to-end proof that a real completed JOB writes it — and that its ``draft`` key round-trips the
actual ``ChainDraftOut`` wire shape — lives with the endpoint tests (``tests/app/test_workbench_api.py``).
"""

from __future__ import annotations

import json
import re
import uuid

from pydantic import BaseModel

from domain.enums import Authorship, TermTier
from domain.settings import get_settings
from domain.thesis import TermSetEntry, Thesis
from workbench.draft_run_log import write_draft_run_log


class _Draft(BaseModel):
    """A stand-in with the pydantic dump/validate contract — the writer duck-types ``model_dump`` and never
    imports the app wire schema (the layering stays one-way)."""

    thesis_id: uuid.UUID
    placements: list[str] = []


def _thesis() -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        name="psilocybin",
        narrative="psychedelic medicine is re-rating",
        term_set=[
            TermSetEntry(
                term="psilocybin", tier=TermTier.SIGNAL, authored_by=Authorship.OPERATOR_SET
            ),
            TermSetEntry(term="mental health", tier=TermTier.BROAD, source="keyword_gen"),
        ],
    )


def test_writes_one_json_under_the_thesis_dir(tmp_path):
    t = _thesis()
    path = write_draft_run_log(t, _Draft(thesis_id=t.id), "job1", base_dir=tmp_path)
    assert path is not None and path.parent == tmp_path / str(t.id)
    # <utc-timestamp>-<job_id>.json, colon-free (the filename must be legal on Windows)
    assert re.fullmatch(r"\d{8}T\d{6}Z-job1\.json", path.name)
    assert [p.name for p in (tmp_path / str(t.id)).iterdir()] == [path.name]  # one run, one file


def test_payload_carries_the_inputs_and_round_trips_the_draft(tmp_path):
    t = _thesis()
    draft = _Draft(thesis_id=t.id, placements=["Compass Pathways"])
    path = write_draft_run_log(t, draft, "job2", base_dir=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["job_id"] == "job2"
    assert payload["thesis"] == {
        "id": str(t.id),
        "name": "psilocybin",
        "narrative": "psychedelic medicine is re-rating",
    }
    # the term set AS USED — term/tier/authorship (+ source provenance), the exact entries discovery read
    assert [(e["term"], e["tier"], e["authored_by"]) for e in payload["term_set"]] == [
        ("psilocybin", "signal", "operator_set"),
        ("mental health", "broad", "system_drafted"),
    ]
    s = get_settings()  # the dials in effect — what makes one run's universe differ from another's
    assert payload["dials"] == {
        "discovery_hit_cap": s.discovery_hit_cap,
        "research_model": s.llm_research_model,
        "decompose_model": s.llm_decompose_model,
    }
    assert _Draft.model_validate(payload["draft"]) == draft  # the draft round-trips its dump


def test_fail_open_on_an_unwritable_home(tmp_path, caplog):
    """The guardrail, unit-enforced: an I/O fault (here: the base dir is a FILE, so mkdir raises) is a logged
    ``None`` — the writer NEVER raises out to the job layer."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    t = _thesis()
    with caplog.at_level("ERROR", logger="alphadeck.workbench"):
        assert write_draft_run_log(t, _Draft(thesis_id=t.id), "job3", base_dir=blocker) is None
    assert (
        "fail-open" in caplog.text
    )  # logged, per the guardrail — silent swallowing is not fail-open


def test_fail_open_on_an_undumpable_draft(tmp_path):
    t = _thesis()
    assert write_draft_run_log(t, object(), "job4", base_dir=tmp_path) is None  # no raise
    assert not (tmp_path / str(t.id)).exists() or not list((tmp_path / str(t.id)).iterdir())


def test_an_empty_term_set_still_records_honestly(tmp_path):
    """A done-but-empty draft (fail-open seams) is still a completed run — the record shows exactly what the
    run had (no terms), it doesn't skip. The artifact is the honesty log, not a success trophy."""
    t = Thesis(id=uuid.uuid4(), name="bare", narrative="n", term_set=[])
    path = write_draft_run_log(t, _Draft(thesis_id=t.id), "job5", base_dir=tmp_path)
    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8"))["term_set"] == []
