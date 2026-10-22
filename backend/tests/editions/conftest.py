# The editions ENGINE is the replay engine (duckdb + pyarrow, the optional `.[replay]` extra). In a lean
# `.[dev]`-only venv skip the engine tests rather than erroring on import; the join-date tests (Postgres
# only) and the import-graph test always run. CI installs `.[dev,replay]`.
try:
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401
except ImportError:  # pragma: no cover
    collect_ignore_glob = [
        "test_public_view.py",
        "test_engine.py",
        "test_selfcheck.py",
        "test_parity.py",
        "test_set_aside_ceiling.py",
    ]
