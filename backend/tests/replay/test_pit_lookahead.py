from __future__ import annotations

from datetime import date, datetime, timezone

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror

# The mirror analogs of tests/db/test_bitemporal.py — the DuckDB/Parquet path is held to the IDENTICAL
# no-lookahead honesty bar as the live Postgres as_of, on BOTH bitemporal axes. This is the integrity
# heart: a replay pit at T can see nothing the system didn't know at (T, known_at).


def _insider(security_id, *, accession, valid_from, usd, recorded_at):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "insider_name": "CEO",
        "txn_code": "P",
        "usd": usd,
        "accession": accession,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def test_mirror_has_no_valid_time_lookahead(db, security_id, tmp_path):
    """Axis 1 (event time): a fact whose ``valid_from`` is after the as-of is invisible to the mirror."""
    t = datetime(2026, 6, 3, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id, accession="a-1", valid_from=date(2026, 6, 1), usd=1_000_000, recorded_at=t
        ),
    )
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id, accession="a-2", valid_from=date(2026, 6, 5), usd=2_000_000, recorded_at=t
        ),
    )
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        rep = ReplayPointInTimeData(
            con, asof=date(2026, 6, 2), known_at=datetime(2026, 6, 30, tzinfo=timezone.utc)
        )
        rows = rep.insider_txns(security_id)
        assert [r["accession"] for r in rows] == ["a-1"]  # the 06-05 txn is future as-of 06-02
        assert all(
            r["valid_from"] <= date(2026, 6, 2) for r in rows
        )  # nothing past the as-of leaks in
    finally:
        con.close()


def test_mirror_has_no_transaction_time_lookahead(db, security_id, tmp_path):
    """Axis 2 (transaction time): a correction recorded after ``known_at`` cannot leak into an earlier
    pinned read — the determinism PIN actually masks late knowledge in the mirror."""
    t1 = datetime(2026, 6, 3, 12, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="acc-X",
            valid_from=date(2026, 6, 1),
            usd=2_100_000,
            recorded_at=t1,
        ),
    )
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id, accession="acc-X", valid_from=date(2026, 6, 1), usd=900_000, recorded_at=t2
        ),
    )
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        asof = date(2026, 6, 30)
        at_t1 = ReplayPointInTimeData(con, asof=asof, known_at=t1).insider_txns(security_id)
        at_t2 = ReplayPointInTimeData(con, asof=asof, known_at=t2).insider_txns(security_id)
        assert (
            len(at_t1) == 1 and float(at_t1[0]["usd"]) == 2_100_000
        )  # correction not yet known at t1
        assert len(at_t2) == 1 and float(at_t2[0]["usd"]) == 900_000  # correction applied by t2
    finally:
        con.close()
