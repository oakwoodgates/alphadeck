"""The pandas-sentinel regression pin (the module comment in ``replay/pit.py`` has the mechanism).

DuckDB probes ``import pandas`` per bound parameter on every execute, and CPython never caches a failed
import, so with pandas absent every replay query re-walks ``sys.path`` (a stat storm). On Windows that is
the difference between ~31 min and ~6 min for the FULL suite. Removing the sentinel regresses SILENTLY —
the rows are identical either way, only the wall clock moves — so this test is the only thing that notices.
"""

from __future__ import annotations

import importlib.machinery
import sys


def test_pandas_probe_is_short_circuited_when_pandas_is_absent():
    """pandas absent -> ``sys.modules["pandas"]`` must be the ``None`` sentinel (an immediate ImportError,
    no path scan). pandas present -> the sentinel must NOT be set (the guard leaves a real install alone):
    ``sys.modules`` then holds either nothing or the real module, never ``None``. The presence probe goes
    through ``PathFinder`` directly (it walks ``sys.path`` and ignores ``sys.modules``), so a guard that
    wrongly fired in a pandas venv is caught rather than masked by its own sentinel."""
    import replay.pit  # noqa: F401  (importing the module is what installs the sentinel)

    pandas_installed = importlib.machinery.PathFinder.find_spec("pandas") is not None
    if pandas_installed:
        assert (
            sys.modules.get("pandas", "missing") is not None
        ), "the sentinel fired in a venv that HAS pandas — the find_spec guard is broken"
    else:
        assert (
            sys.modules.get("pandas", "missing") is None
        ), "pandas is absent but replay.pit did not install the sentinel (the ~5x replay tax is back)"
