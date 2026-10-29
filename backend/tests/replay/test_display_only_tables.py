"""The DISPLAY-ONLY fact tables (``db.bitemporal.DISPLAY_ONLY_FACT_TABLES`` — today the filing index, migration
0070) stay OUT of the replay mirror. ``connect_mirror`` requires a parquet file for every table in
``replay.export.FACT_TABLES``, so a display table registered there would make every frozen mirror written before
it (the backtest archives, the editions run dirs) fail to open — for rows no replay reader uses. DB-backed.
"""

from __future__ import annotations

from db.bitemporal import _FACT_IDENTITY, DISPLAY_ONLY_FACT_TABLES
from db.public_clock import PUBLIC_TIME
from ingest.edgar.filing_index import ingest_filing_index
from replay.export import FACT_TABLES, export_snapshot
from replay.pit import connect_mirror

_INDEX = "fact_filing_index"


def test_the_registry_keeps_the_index_live_readable_and_out_of_the_mirror_set():
    assert DISPLAY_ONLY_FACT_TABLES == frozenset({_INDEX})
    assert _INDEX in _FACT_IDENTITY and _INDEX in PUBLIC_TIME  # the live PIT reads it, both clocks
    assert _INDEX not in FACT_TABLES
    assert FACT_TABLES == tuple(t for t in _FACT_IDENTITY if t not in DISPLAY_ONLY_FACT_TABLES)


def test_an_export_never_writes_the_index_and_the_mirror_opens_without_it(
    db, security_id, tmp_path
):
    ingest_filing_index(
        db,
        security_id,
        "0001234567",
        [
            {
                "form": "S-1",
                "accession": "0001234567-26-000001",
                "primary_doc": "s1.htm",
                "filed": "2026-06-01",
                "accepted_raw": "",
            }
        ],
    )
    db.commit()
    for clock in ("record", "public"):
        out = tmp_path / clock
        manifest = export_snapshot(db, out, clock=clock)
        assert not (out / f"{_INDEX}.parquet").exists()
        assert _INDEX not in manifest["tables"] and _INDEX not in manifest["excluded_tables"]
        con = connect_mirror(out)  # a mirror without the file opens (as every pre-0070 mirror does)
        try:
            names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()
        assert set(FACT_TABLES) <= names and _INDEX not in names
