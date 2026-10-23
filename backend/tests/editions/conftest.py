# The editions ENGINE is the replay engine (duckdb + pyarrow, the optional `.[replay]` extra). In a lean
# `.[dev]`-only venv skip the engine tests rather than erroring on import; the join-date tests (Postgres
# only) and the import-graph test always run. CI installs `.[dev,replay]`.
import pytest

try:
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401
except ImportError:  # pragma: no cover
    collect_ignore_glob = [
        "test_public_view.py",
        "test_engine.py",
        "test_selfcheck.py",
        "test_parity.py",
        "test_publish.py",
        "test_set_aside_ceiling.py",
        "test_nightly.py",
        "test_ink.py",
    ]


@pytest.fixture(autouse=True)
def _edition_run_homes(tmp_path, monkeypatch):
    """S5 — the nightly page job's homes, per test: its run artifacts (``pipeline.edition_run_log``), its
    working mirror (``editions.nightly._WORK_DIR``, only when the engine is installed) and the cron run
    artifacts its gate reads (``pipeline.cron_run_log``) — so a test never reads or litters a dev machine's
    real ``data/``. A test that seeds artifacts requests ``tmp_path`` and uses the same directories.
    """
    from pipeline import cron_run_log, edition_run_log

    monkeypatch.setattr(edition_run_log, "EDITION_RUNS_DIR", tmp_path / "edition_runs")
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path / "cron_runs")
    try:
        from editions import nightly
    except ImportError:  # pragma: no cover — a lean venv: the nightly module needs the engine
        return
    monkeypatch.setattr(nightly, "_WORK_DIR", tmp_path / "nightly_work")
