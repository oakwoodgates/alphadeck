# The replay harness is an OPTIONAL extra (duckdb + pyarrow). In a lean `.[dev]`-only venv it isn't
# installed; skip collecting this whole package rather than erroring on import. CI installs `.[dev,replay]`
# so these run there (against the Postgres service).
try:
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401
except ImportError:  # pragma: no cover
    collect_ignore_glob = ["*"]
