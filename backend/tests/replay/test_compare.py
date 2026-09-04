from __future__ import annotations

import pytest

from domain.config import DEFAULT_CONFIG
from pipeline.seed import seed_unh
from replay.compare import compare


@pytest.mark.slow  # ~66 s serial with the pandas sentinel (was 316 s before it)
@pytest.mark.timeout(300)  # overrides the 120 s ini guard: no measured margin under -n 6
def test_compare_varies_only_cfg(db, tmp_path):
    """The recalibration sweep runner exports the mirror ONCE and replays each cfg variant over that same
    frozen snapshot, so the only difference between variants is ``cfg``: a base-vs-base pair is
    value-identical (determinism + shared mirror), and a base-vs-tweaked pair differs (cfg genuinely flows
    through — the delta is real, not noise). UNH-only keeps it cheap."""
    seed_unh(db)
    db.commit()
    base = DEFAULT_CONFIG
    tweaked = DEFAULT_CONFIG.model_copy(
        update={"breakout_min_return": 0.50}
    )  # unreachable -> no arm
    results = compare(db, tmp_path, [("base", base), ("base2", base), ("tweaked", tweaked)])
    by = {r.label: r for r in results}

    # base vs base: value-identical (same frozen mirror + same cfg -> deterministic)
    assert by["base"].metrics.model_dump() == by["base2"].metrics.model_dump()
    # base arms (UNH); the unreachable breakout bar suppresses arms -> cfg genuinely flows through
    assert by["base"].metrics.n_episodes > 0
    assert by["tweaked"].metrics.n_episodes < by["base"].metrics.n_episodes
