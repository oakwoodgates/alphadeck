"""PIT PARITY for the internal-transfer screen: the DuckDB replay mirror tags a buy and a sale between linked filers
EXACTLY like Postgres (the ONE pure rule, inside the shared resolver seam, the 0055 text arrays decoded back to
lists), on the real filings of the verification table (``tests/transfer_fixtures.py``), on both read shapes, at
transaction instants before and after the filings — plus a non-vacuity check (the mirror really screens) and the
old-mirror case (a mirror without the 0055 columns links nothing by name; one filer's own buy and sale still pair).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pyarrow.parquet as pq
import pytest

from domain.insider_internal_transfers import INTERNAL_TRANSFER, is_internal_transfer
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals.base import PointInTimeData
from tests.replay.test_pit_insider_amendments import _assert_equal
from tests.transfer_fixtures import seed

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
CASES = ("AMKR", "SLSN", "ESI", "MRLN", "FTHM", "MGM")


@pytest.mark.parametrize("basket", [True, False], ids=["prefetch", "scoped"])
def test_the_mirror_screens_internal_transfers_exactly_like_postgres(db, tmp_path, basket):
    sids = {c: seed(db, c) for c in CASES}
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    ids = list(sids.values())
    checked = 0
    for asof in (
        date(2012, 12, 31),
        date(2018, 8, 10),
        date(2024, 9, 20),
        date(2025, 3, 3),
        date(2025, 12, 31),
    ):
        for known_at in (datetime(2025, 2, 27, tzinfo=timezone.utc), FAR):
            live = PointInTimeData(db, asof=asof, known_at=known_at, basket=ids if basket else None)
            rep = ReplayPointInTimeData(
                con, asof=asof, known_at=known_at, basket=ids if basket else None
            )
            for sid in ids:
                _assert_equal(live.insider_txns(sid), rep.insider_txns(sid))
                checked += 1
    assert checked == 5 * 2 * len(ids)


def test_parity_is_not_vacuous_the_mirror_really_screens(db, tmp_path):
    sids = {c: seed(db, c) for c in CASES}
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    amkr = ReplayPointInTimeData(con, asof=date(2025, 3, 3), known_at=FAR).insider_txns(
        sids["AMKR"]
    )
    legs = [r for r in amkr if r["valid_from"] == date(2025, 2, 24) and r["txn_code"] in ("P", "S")]
    assert len(legs) == 3 and all(is_internal_transfer(r) for r in legs)
    assert {r[INTERNAL_TRANSFER]["co_report"] for r in legs} == {False, True}
    fthm = ReplayPointInTimeData(con, asof=date(2024, 9, 20), known_at=FAR).insider_txns(
        sids["FTHM"]
    )
    assert fthm and not any(
        is_internal_transfer(r) for r in fthm
    )  # the private purchase stays counted
    # the text arrays read as lists on the mirror, as on Postgres
    assert all(isinstance(r["filing_natures"], list) for r in legs)


def test_a_mirror_without_the_capture_links_nothing_by_name(db, tmp_path):
    """A mirror exported BEFORE migration 0055 carries no ``ownership_nature`` / ``txn_footnotes`` /
    ``filing_natures``: the named-holding link has no evidence (AMKR stays counted), while one filer's own buy and
    sale still pair on identity alone (Pershing Square)."""
    sids = {c: seed(db, c) for c in ("AMKR", "ESI")}
    export_snapshot(db, tmp_path)
    facts = pq.read_table(tmp_path / "fact_insider_txn.parquet")
    pq.write_table(
        facts.drop_columns(["ownership_nature", "txn_footnotes", "filing_natures"]),
        tmp_path / "fact_insider_txn.parquet",
    )
    con = connect_mirror(tmp_path)
    amkr = ReplayPointInTimeData(con, asof=date(2025, 3, 3), known_at=FAR).insider_txns(
        sids["AMKR"]
    )
    assert not any(is_internal_transfer(r) for r in amkr)
    esi = ReplayPointInTimeData(con, asof=date(2015, 7, 1), known_at=FAR).insider_txns(sids["ESI"])
    assert {r["txn_code"] for r in esi if is_internal_transfer(r)} == {"P", "S"}
