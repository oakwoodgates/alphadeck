"""The thin-history DATA-HEALTH classifier (``securities/price_history_health.py``) — a pure bar-count vs
the threshold, structurally OUT of the call path."""

from __future__ import annotations

import ast
from pathlib import Path

from securities.price_history_health import THIN_HISTORY_BARS, is_thin_history


def test_threshold_is_the_longest_active_lookback():
    """THIN_HISTORY_BARS is the SMA slow window (200 bars) — the longest active price-history lookback
    (> the breakout's ~82). Pinned so a change is deliberate."""
    assert THIN_HISTORY_BARS == 200


def test_is_thin_history_boundary_and_blind_spot():
    assert (
        is_thin_history(0) is True
    )  # a genuinely-uncovered name (0 bars) — the resolver's blind spot
    assert is_thin_history(199) is True  # one short of the threshold — still starved
    assert is_thin_history(200) is False  # exactly the threshold — enough tape
    assert is_thin_history(251) is False  # a full year — healthy


def test_health_module_is_structurally_out_of_the_call_path():
    """The flag must be UNABLE to affect a call: the module imports NOTHING from ``calls`` / ``signals``
    (the origin.py / filer_coverage.py discipline). Asserted against the source imports, so a future edit
    that reaches into the call path fails here."""
    src = (
        Path(__file__).resolve().parents[2] / "securities" / "price_history_health.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    assert not any(m.startswith(("calls", "signals")) for m in imported), imported
