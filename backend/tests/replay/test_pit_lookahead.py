from __future__ import annotations

from datetime import date, datetime, timezone

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror

# The mirror analogs of tests/db/test_bitemporal.py — the DuckDB/Parquet path is held to the IDENTICAL
# no-lookahead honesty bar as the live Postgres as_of, on BOTH bitemporal axes. This is the integrity
# heart: a replay pit at T can see nothing the system didn't know at (T, known_at).


def _insider(security_id, *, accession, valid_from, usd, recorded_at, accepted=None):
    values = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "insider_name": "CEO",
        "txn_code": "P",
        "usd": usd,
        "accession": accession,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }
    if accepted is not None:
        values["accepted"] = accepted
    return values


def test_mirror_transaction_time_gate_keys_on_accepted(db, security_id, tmp_path):
    """The MRVL two-clock fix in the REPLAY path (#1): the DuckDB mirror's knowability gate is
    ``COALESCE(accepted, recorded_at)`` — BYTE-IDENTICAL to Postgres. A filing accepted 06-03 but
    re-ingested 09-01 is visible to a mirror read pinned 06-10 (after acceptance, before our ingest) and
    invisible pinned 06-02 (pre-acceptance) — the same honesty the live PIT gives."""
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="acc-late",
            valid_from=date(2026, 6, 1),
            usd=1_000_000,
            recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            accepted=datetime(2026, 6, 3, 12, tzinfo=timezone.utc),
        ),
    )
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        asof = date(2026, 6, 15)
        seen = ReplayPointInTimeData(
            con, asof=asof, known_at=datetime(2026, 6, 10, tzinfo=timezone.utc)
        ).insider_txns(security_id)
        assert [r["accession"] for r in seen] == ["acc-late"]  # visible from acceptance
        blind = ReplayPointInTimeData(
            con, asof=asof, known_at=datetime(2026, 6, 2, tzinfo=timezone.utc)
        ).insider_txns(security_id)
        assert blind == []  # pinned before acceptance -> invisible (no lookahead)
    finally:
        con.close()


def _fundamentals(security_id, *, period_end, valid_from, value, recorded_at, accn="f-1"):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "metric_key": "revenue",
        "period_end": period_end,
        "fiscal_period": "Q4",
        "fiscal_year": period_end.year,
        "value": value,
        "unit": "USD",
        "basis": "native",
        "accession": accn,
        "source": "companyfacts",
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def test_mirror_has_no_valid_time_lookahead_fundamentals(db, security_id, tmp_path):
    """§2.2 knowability in the replay path: a quarter FILED after the as-of is invisible to the mirror EVEN
    THOUGH its period END precedes the as-of. Visibility keys on valid_from (= filed), not the period end —
    the trap that makes an as-of backtest honest (#1)."""
    t = datetime(2024, 6, 1, tzinfo=timezone.utc)
    # the quarter's period ends 2023-12-31 but it was FILED 2024-02-15 (a real ~46d filing lag)
    append_fact(
        db,
        "fact_fundamentals",
        _fundamentals(
            security_id,
            period_end=date(2023, 12, 31),
            valid_from=date(2024, 2, 15),
            value=500,
            recorded_at=t,
        ),
    )
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        known = datetime(2024, 6, 30, tzinfo=timezone.utc)
        # as-of 2024-01-15: AFTER the period end, BEFORE the filing -> invisible (no valid-time lookahead)
        blind = ReplayPointInTimeData(con, asof=date(2024, 1, 15), known_at=known)
        assert blind.fundamentals_facts(security_id) == []
        # as-of the filing date -> visible, and stamped at filed
        seeing = ReplayPointInTimeData(con, asof=date(2024, 2, 15), known_at=known)
        rows = seeing.fundamentals_facts(security_id)
        assert len(rows) == 1 and rows[0]["period_end"] == date(2023, 12, 31)
        assert rows[0]["valid_from"] == date(2024, 2, 15)
    finally:
        con.close()


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


def _corporate_event(security_id, *, accession, filed, items, recorded_at):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "form": "8-K",
        "items": items,
        "accession": accession,
        "filed": filed,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
        "valid_from": filed,
        "recorded_at": recorded_at,
    }


def test_mirror_has_no_valid_time_lookahead_corporate_events(db, security_id, tmp_path):
    """Band 03 S3 knowability in the replay path: an 8-K is visible from its FILED date (valid_from
    = filed, the acceptance date IS the knowability) — a filing filed after the as-of is invisible,
    and the items list survives the text[] -> JSON-string -> list mirror round-trip intact (the
    resolve VERSION winning the dedup, the unresolved NULL staying None)."""
    t = datetime(2026, 6, 3, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_corporate_event",
        _corporate_event(
            security_id, accession="e-1", filed=date(2026, 6, 1), items=None, recorded_at=t
        ),
    )
    append_fact(  # the resolve — a later version of e-1 with its items known
        db,
        "fact_corporate_event",
        _corporate_event(
            security_id,
            accession="e-1",
            filed=date(2026, 6, 1),
            items=["4.02", "9.01"],
            recorded_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        ),
    )
    append_fact(
        db,
        "fact_corporate_event",
        _corporate_event(
            security_id, accession="e-2", filed=date(2026, 6, 5), items=["1.01"], recorded_at=t
        ),
    )
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        known = datetime(2026, 6, 30, tzinfo=timezone.utc)
        rows = ReplayPointInTimeData(
            con, asof=date(2026, 6, 2), known_at=known
        ).corporate_event_facts(security_id)
        assert [r["accession"] for r in rows] == ["e-1"]  # e-2 (filed 06-05) is future as-of 06-02
        assert rows[0]["items"] == ["4.02", "9.01"]  # the resolved VERSION, decoded back to a list
        # pinned BEFORE the resolve was recorded: the same filing honestly reads unresolved (None)
        early = ReplayPointInTimeData(con, asof=date(2026, 6, 2), known_at=t).corporate_event_facts(
            security_id
        )
        assert len(early) == 1 and early[0]["items"] is None
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
