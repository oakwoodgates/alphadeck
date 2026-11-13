from __future__ import annotations

import json
import random
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.scopes import DATA_TENANT_ID

# NO BACKTEST RESULT MAY DEPEND ON THE ROW ORDER INSIDE A FACT FILE.
#
# The record-clock export now writes every fact file in ``(recorded_at, id)`` order; before, the order was
# whatever plan Postgres picked. That change is safe only if no reader of a fact file ever sees its order:
# every DuckDB fact read sorts on a key that is unique after its own version pick (``replay.pit``,
# ``replay.scoring``, ``replay.harness``). This proves it with data, on a window that really arms (the CI run
# that exposed the unordered read produced ZERO episodes, so its "outputs matched" proved nothing): the same
# mirror with every fact file shuffled, and again reversed, must give byte-identical results. The aux, hold,
# split and CIK files are left alone: the resolver reads them in file order, which is part of their contract.

_PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(
    2026, 6, 1
)  # the UNH arc arms in this window (test_harness_unh)
_SAME_BYTES = (
    "episodes.parquet",
    "outcomes.parquet",
    "pooled.json",
    "metrics.json",
    "episodes.json",
)


def _rewrite_fact_files(src: Path, dst: Path, order: str) -> None:
    """Copy the mirror (``manifest.json`` included), then rewrite every FACT file with its rows reordered:
    a seeded random permutation, or fully reversed. Same rows, same schema."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from replay.export import FACT_TABLES

    shutil.copytree(src, dst)
    for table in FACT_TABLES:
        path = dst / f"{table}.parquet"
        t = pq.read_table(path)
        idx = list(range(t.num_rows))
        if order == "reversed":
            idx.reverse()
        else:
            random.Random(f"row-order:{table}").shuffle(idx)
        pq.write_table(t.take(pa.array(idx, type=pa.int64())), path)


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_backtest_results_do_not_depend_on_fact_file_row_order(db, tmp_path):
    pytest.importorskip("duckdb")
    import pyarrow.parquet as pq

    from backtest.run import execute
    from pipeline.seed import seed_unh
    from replay.export import export_snapshot

    seed_unh(db)
    db.commit()
    base = tmp_path / "mirror"
    export_snapshot(db, base, data_tenant=DATA_TENANT_ID, workspace=DEFAULT_TENANT_ID)
    _rewrite_fact_files(base, tmp_path / "shuffled", "shuffled")
    _rewrite_fact_files(base, tmp_path / "reversed", "reversed")
    # the teeth check: the rewrite really moved rows, so equal results are not equal inputs
    insider = "fact_insider_txn.parquet"
    rows = {
        name: pq.read_table(tmp_path / name / insider).column("id").to_pylist()
        for name in ("mirror", "shuffled", "reversed")
    }
    assert rows["mirror"] != rows["shuffled"] and rows["mirror"] != rows["reversed"]
    assert sorted(rows["mirror"]) == sorted(rows["shuffled"]) == sorted(rows["reversed"])

    runs = []
    for second, name in enumerate(("mirror", "shuffled", "reversed")):
        runs.append(
            execute(
                db,
                start=_START,
                end=_END,
                pin=_PIN,
                mirror_dir=tmp_path / name,
                root=tmp_path / "store",
                null_seed="row-order",
                now=datetime(2026, 11, 13, 4, 0, second, tzinfo=timezone.utc),
            )
        )
    assert runs[0].manifest.n_episodes > 0, "the window must arm, or equal outputs prove nothing"
    for other in runs[1:]:
        assert other.manifest.n_episodes == runs[0].manifest.n_episodes
        for name in _SAME_BYTES:
            assert (runs[0].path / name).read_bytes() == (other.path / name).read_bytes(), name
        a, b = (
            json.loads((r.path / "ledger.json").read_text(encoding="utf-8"))
            for r in (runs[0], other)
        )
        # the ledger stamps the run's own `now` (distinct per run by construction), at its snapshot
        for blob in (a, b):
            assert blob["snapshot"].pop("generated_at")
        assert a == b
