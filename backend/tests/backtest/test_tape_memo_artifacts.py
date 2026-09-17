from __future__ import annotations

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

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(2025, 6, 30)
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
    # the comparison must not be vacuous: an empty run would match trivially
    assert memo.manifest.n_episodes > 0
    # both ran over the SAME frozen tape, which is what makes the byte comparison about the reader
    assert memo.manifest.mirror.hash == pre.manifest.mirror.hash
