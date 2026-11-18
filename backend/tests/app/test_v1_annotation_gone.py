"""V2 W8b: V1's call-annotation machinery is GONE (the table stays, empty after the cutover).

The cutover resets V1's calls log and empties ``call_annotation``, so nothing is left to annotate or to set
aside. These tests pin the end state: the three CLIs and the repository cannot be imported, the Scoreboard
and Track Record have no annotated reading (no ``include_annotated`` parameter, no ``annotated_policy`` /
``annotation_note``), the setting is gone, and no source file outside the few allowed ones names the old
machinery again.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from app.main import app
from domain.settings import Settings

BACKEND = Path(__file__).resolve().parents[2]

GONE_MODULES = (
    "pipeline.annotate_phantom_arms",
    "pipeline.annotate_no_session_rows",
    "pipeline.repair_reconstructed_precreation",
    "repositories.call_annotations_repo",
)

# built from pieces so this file never trips its own scan
FORBIDDEN = (
    "call_" + "annotation",
    "annotate_" + "phantom_arms",
    "annotate_" + "no_session_rows",
    "repair_" + "reconstructed_precreation",
    "include_" + "annotated",
    "exclude_" + "annotated",
)

# the table stays: its migrations, its schema test, its scope entry and the cutover's reset entry name it
ALLOWED = (
    "db/migrations/",
    "tests/db/test_migration_0045_call_annotation.py",
    "domain/scopes.py",
    "pipeline/v2_cutover.py",
    "pipeline/v2_reset.py",  # W10a: the reset empties the table
    # tests of the kept pieces that list the table among the tables they cover
    "tests/pipeline/test_v2_cutover_plan.py",
    "tests/pipeline/test_v2_reset.py",
    "tests/db/test_0078_founding_workspace.py",
    "tests/app/test_v1_annotation_gone.py",
)

# build output, caches, the virtualenv and local data are not source
SKIP_DIRS = {
    ".venv",
    "__pycache__",
    "alphadeck_backend.egg-info",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "data",
}
TEXT_SUFFIXES = {
    ".py",
    ".sql",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
    ".cfg",
    ".ini",
    ".csv",
}


@pytest.mark.parametrize("module", GONE_MODULES)
def test_the_module_cannot_be_imported(module: str) -> None:
    assert importlib.util.find_spec(module) is None


def _query_names(path: str) -> set[str]:
    op = app.openapi()["paths"][path]["get"]
    return {p["name"] for p in op.get("parameters", [])}


@pytest.mark.parametrize("path", ["/scoreboard", "/dashboards/track-record"])
def test_include_annotated_is_not_a_parameter(path: str) -> None:
    assert "include_" + "annotated" not in _query_names(path)


def test_unknown_parameter_is_ignored_and_the_response_has_no_annotated_fields(client) -> None:
    """The app does not forbid unknown query parameters (FastAPI's default): the old one is simply ignored."""
    r = client.get("/scoreboard", params={"asof": "2026-07-31", "include_" + "annotated": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    summary = body["summary"]
    assert "annotated_policy" not in summary and "annotated_nights" not in summary
    for thesis in body["theses"]:
        for ep in thesis["episodes"]:
            assert "annotation_note" not in ep


def test_the_setting_is_gone() -> None:
    assert "scoreboard_exclude_" + "annotated" not in Settings.model_fields


def test_nothing_outside_the_allowed_files_names_the_old_machinery() -> None:
    hits: list[str] = []
    for root, dirs, names in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            p = Path(root) / name
            rel = p.relative_to(BACKEND).as_posix()
            if p.suffix not in TEXT_SUFFIXES or any(rel.startswith(a) for a in ALLOWED):
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            hits += [f"{rel}: {w}" for w in FORBIDDEN if w in text]
    assert not hits, "\n".join(hits)
