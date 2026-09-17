from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

# The run CLI drives the replay engine, which is the optional .[replay] extra.
pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import backtest.run as run_mod  # noqa: E402
from replay.export import export_snapshot  # noqa: E402
from tests.replay.test_tape_memo import _PreMemoRealizedPrices  # noqa: E402

# M1 at the ARTIFACT level. `tests/replay/test_tape_memo.py` proves the reader answers identically and
# that the query count collapses; this proves the same thing about the BYTES a reviewer actually opens,
# through the real `execute()` composition rather than a hand-assembled one.
#
# Two things make the comparison fair and both are load-bearing:
#   * ONE shared mirror, passed to both runs, so neither re-exports and the two sweep the same tape;
#   * an EXPLICIT `null_seed`. It defaults to the run_id, which differs between two runs by construction,
#     so without pinning it the two passes would draw different nulls and the comparison would measure
#     the seed rather than the memo.

# The window and pin the backtest suite already runs `execute()` on, taken VERBATIM from
# `tests/backtest/test_run_e2e.py` and `tests/backtest/test_parallel.py` — and proved to arm by
# `tests/replay/test_scoring.py::test_unh_arm_scores_a_finite_forward_outcome`, which asserts "UNH should
# produce at least one arm episode" over exactly this span. The first draft of this file shortened it to
# 2025-06-30 to be cheap; the seed arms NOTHING there, so all three byte comparisons passed over empty
# artifacts and only the non-vacuity guard caught it. A real instance where the seed genuinely arms, never
# a window chosen to make a number appear.
_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(2026, 6, 1)
_SEED = "m1-artifact-parity"


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_memo_leaves_the_run_artifacts_byte_identical(db, tmp_path, monkeypatch):
    """Outcomes and the pooled report, as BYTES on disk, from the memoized reader and from the code it
    replaced. Episodes are asserted too — they come from the replay pass, which this slice does not
    touch, so a difference there would mean the memo had reached somewhere it has no business being.
    """
    from pipeline.seed import seed_unh

    seed_unh(db)
    db.commit()

    mirror = tmp_path / "mirror"
    export_snapshot(db, mirror)

    memo = run_mod.execute(
        db,
        start=_START,
        end=_END,
        pin=_PIN,
        mirror_dir=mirror,
        null_draws=5,
        null_seed=_SEED,
        root=tmp_path / "store",
    )
    # ...and again through the reader as it was before M1
    monkeypatch.setattr(run_mod, "RealizedPrices", _PreMemoRealizedPrices)
    pre = run_mod.execute(
        db,
        start=_START,
        end=_END,
        pin=_PIN,
        mirror_dir=mirror,
        null_draws=5,
        null_seed=_SEED,
        root=tmp_path / "store",
    )

    for name in ("episodes.parquet", "outcomes.parquet", "pooled.json"):
        assert (memo.path / name).read_bytes() == (pre.path / name).read_bytes(), name
    # THE COMPARISON MUST NOT BE VACUOUS, at three levels — empty artifacts match trivially, and that is
    # exactly what the first draft of this test did (a window where the seed never armed).
    assert memo.manifest.n_episodes > 0
    pooled = json.loads((memo.path / "pooled.json").read_text(encoding="utf-8"))
    assert (
        pooled["n_scoreable"] > 0
    ), "the nulls must have priced something for this to compare them"
    assert pooled["metrics"][0]["actual"]["n"] > 0, "a metric with n=0 is not evidence of agreement"
    # both ran over the SAME frozen tape, which is what makes the byte comparison about the reader
    assert memo.manifest.mirror.hash == pre.manifest.mirror.hash
