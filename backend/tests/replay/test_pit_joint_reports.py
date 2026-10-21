"""PIT PARITY for the joint-report collapse: the DuckDB replay mirror collapses a trade several persons reported
EXACTLY like Postgres (the ONE pure rule, inside the shared resolver seam), on the real filings of the
verification table (``tests/joint_report_fixtures.py``), on both read shapes (the basket prefetch and the
per-security read), at transaction instants before and after the filings — plus a non-vacuity check (the mirror
really collapses) and the old-mirror case (a mirror without the 0054 columns reads exactly as before).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from domain.insider_joint_reports import JOINT_REPORTERS
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals.base import PointInTimeData
from tests.joint_report_fixtures import seed
from tests.replay.test_pit_insider_amendments import _assert_equal

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
TICKERS = ("INFQ", "AMKR", "SMCI", "OKLO", "ALAB", "TSM")


@pytest.mark.parametrize("basket", [True, False], ids=["prefetch", "scoped"])
def test_the_mirror_collapses_joint_reports_exactly_like_postgres(db, tmp_path, basket):
    sids = {t: seed(db, t) for t in TICKERS}
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    ids = list(sids.values())
    checked = 0
    for asof in (date(2025, 3, 3), date(2025, 8, 20), date(2026, 6, 5), date(2026, 10, 9)):
        for known_at in (datetime(2026, 1, 1, tzinfo=timezone.utc), FAR):
            live = PointInTimeData(db, asof=asof, known_at=known_at, basket=ids if basket else None)
            rep = ReplayPointInTimeData(
                con, asof=asof, known_at=known_at, basket=ids if basket else None
            )
            for sid in ids:
                _assert_equal(live.insider_txns(sid), rep.insider_txns(sid))
                checked += 1
    assert checked == 4 * 2 * len(ids)


def test_parity_is_not_vacuous_the_mirror_really_collapses(db, tmp_path):
    sids = {t: seed(db, t) for t in TICKERS}
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    rep = ReplayPointInTimeData(con, asof=date(2026, 6, 5), known_at=FAR)
    infq = rep.insider_txns(sids["INFQ"])
    assert "SINGER DAVID B" not in {r["insider_name"] for r in infq if r["txn_code"] == "S"}
    assert sum(1 for r in infq if r["txn_code"] == "S" and r.get(JOINT_REPORTERS)) == 16
    amkr = ReplayPointInTimeData(con, asof=date(2025, 3, 3), known_at=FAR).insider_txns(
        sids["AMKR"]
    )
    assert len([r for r in amkr if r["txn_code"] == "P"]) == 1
    alab = ReplayPointInTimeData(con, asof=date(2025, 8, 20), known_at=FAR).insider_txns(
        sids["ALAB"]
    )
    assert len(alab) == 4  # the negative stays two trades x two lots on the mirror too


def test_a_mirror_without_the_evidence_reads_exactly_as_before(db, tmp_path, monkeypatch):
    """A mirror exported BEFORE migration 0054 carries no ``direct_indirect`` / ``shares_owned_after`` (neither
    the fact columns nor the keys in the 4/A rows). Every row is then evidence-less and the rule — safe on missing
    evidence — collapses nothing: the mirror reads row-for-row what the uncollapsed Postgres read returns.
    """
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    import domain.insider_amendments as insider_amendments

    sid = seed(db, "INFQ")
    export_snapshot(db, tmp_path)
    # emulate the pre-0054 export ON DISK (the mirror's aux cache is primed when it is opened)
    facts = pq.read_table(tmp_path / "fact_insider_txn.parquet")
    pq.write_table(
        facts.drop_columns(["direct_indirect", "shares_owned_after"]),
        tmp_path / "fact_insider_txn.parquet",
    )
    am = pq.read_table(tmp_path / "insider_amendment.parquet")
    bare_rows = [
        json.dumps(
            [
                {k: v for k, v in r.items() if k not in ("direct_indirect", "shares_owned_after")}
                for r in json.loads(x)
            ]
        )
        for x in am.column("rows").to_pylist()
    ]
    idx = am.schema.get_field_index("rows")
    am = am.set_column(idx, "rows", pa.array(bare_rows, type=am.schema.field("rows").type))
    pq.write_table(am, tmp_path / "insider_amendment.parquet")
    con = connect_mirror(tmp_path)
    asof = date(2026, 6, 5)
    old = ReplayPointInTimeData(con, asof=asof, known_at=FAR).insider_txns(sid)
    assert not any(r.get(JOINT_REPORTERS) for r in old), [
        (r["accession"], r["txn_seq"], r.get("direct_indirect"), r.get("shares_owned_after"))
        for r in old
        if r.get(JOINT_REPORTERS)
    ]
    assert "SINGER DAVID B" in {r["insider_name"] for r in old if r["txn_code"] == "S"}
    monkeypatch.setattr(insider_amendments, "collapse_joint_reports", lambda rows: rows)
    uncollapsed = PointInTimeData(db, asof=asof, known_at=FAR).insider_txns(sid)
    assert sorted((r["accession"], r["txn_seq"]) for r in old) == sorted(
        (r["accession"], r["txn_seq"]) for r in uncollapsed
    )
