from __future__ import annotations

import ast
import inspect
from datetime import date, datetime, timezone
from math import isfinite

import replay.harness
import replay.scoring
from pipeline.seed import UNH_SECURITY_ID, UNH_THESIS_ID, seed_unh
from replay.episodes import derive_episodes
from replay.export import export_snapshot
from replay.harness import replay_thesis
from replay.pit import connect_mirror
from replay.scoring import RealizedPrices, score_episodes
from repositories import thesis_repo

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _import_modules(module) -> set[str]:
    """The modules a module actually IMPORTS (via the AST — docstring/comment mentions don't count)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _imported_names(module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.ImportFrom):
            names |= {a.name for a in node.names}
    return names


def test_lookahead_boundary_is_structural():
    """The scorer must not import the as-of-capped replay pit, and the replay loop must not import the
    forward RealizedPrices reader — so neither pass can reach the other's data (the lookahead boundary,
    as a CI-checkable import-graph fact)."""
    assert "replay.pit" not in _import_modules(replay.scoring)
    assert "RealizedPrices" not in _imported_names(replay.harness)


def test_unh_arm_scores_a_finite_forward_outcome(db, tmp_path):
    """End-to-end through the scoring pass: the UNH August-2025 arm scores a finite, correctly-shaped
    realized forward return to its own ``exit_by`` (the conviction hold horizon, within the data).
    """
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        thesis = thesis_repo.get(db, UNH_THESIS_ID)
        snaps = replay_thesis(
            con, thesis, start=date(2025, 4, 1), end=date(2026, 6, 1), known_at=_PIN
        )
        episodes = derive_episodes(snaps)
        assert episodes, "UNH should produce at least one arm episode"

        outs = score_episodes(episodes, RealizedPrices(con))
        first = min(outs, key=lambda o: o.arm_date)
        assert first.is_headline and first.security_id == UNH_SECURITY_ID
        assert not first.insufficient_prices
        assert first.entry_close and first.exit_close
        assert first.forward_return is not None and isfinite(first.forward_return)
        # the realized peak within the hold window is never below the exit return (peak = max close)
        assert first.peak_return is not None and first.peak_return >= first.forward_return - 1e-9
    finally:
        con.close()
