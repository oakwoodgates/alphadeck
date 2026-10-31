"""S2 — what an edition's mirror SEES: main's PUBLIC clock (``db.public_clock``, the operator's 2026-10-21
rule), the edition's FREEZE CEILING (``known_by``) and the identity aux.

No lookahead (#1) on the public clock: a bar dated after T is invisible at T; a Form 4 counts from its SEC
acceptance — one accepted after T is invisible at T, one accepted before T is visible at T even though this
system stored it weeks later. The ceiling is the edition's freeze, not a history gate: a row recorded after the
cut does not exist in the mirror at all, and every cross-version read (the price tape's edge and the split list
behind a bar's scale) is the store as it stood at the cut. A backtest export is unchanged by any of it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from psycopg import sql

from db.public_clock import CEILING_PARAM, PUBLIC_TIME, public_versions_sql
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from replay.export import IDENTITY_TABLE, export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals.base import PointInTimeData
from tests.editions.fixtures import (
    ACCEPTED,
    BUY_DAY,
    INGESTED,
    evening,
    seed_bar,
    seed_big_buy,
    seed_tape,
    weekdays,
)

_PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)


def _view(con, asof: date, known_by: datetime = _PIN) -> ReplayPointInTimeData:
    return ReplayPointInTimeData(con, asof=asof, known_at=known_at_for_asof(asof, known_by))


def _one(mirror, query: str) -> tuple:
    return mirror.execute(query).fetchone()


def _mirror(db, path, **kw):
    export_snapshot(db, path, clock="public", **kw)
    return connect_mirror(path)


# --- no lookahead on the public clock ---------------------------------------------------------------------


def test_a_bar_dated_after_T_is_invisible_at_T(db, security_id, tmp_path):
    seed_tape(db, security_id, start=date(2026, 6, 1), end=date(2026, 6, 12))
    db.commit()
    con = _mirror(db, tmp_path)
    try:
        bars = _view(con, date(2026, 6, 5)).price_history(security_id)
        assert max(b["d"] for b in bars) == date(2026, 6, 5)  # 06-08 .. 06-12 exist, unseen
        assert [b["d"] for b in _view(con, date(2026, 6, 12)).price_history(security_id)][-1] == (
            date(2026, 6, 12)
        )
    finally:
        con.close()


def test_a_bar_stored_late_is_public_at_its_own_settle(db, security_id, tmp_path):
    """The public clock, not when we held it: a bar first stored a month after its session still counts from
    that session's settle — the record clock would hide it on its own night."""
    seed_bar(
        db, security_id, date(2026, 6, 5), recorded_at=datetime(2026, 7, 6, tzinfo=timezone.utc)
    )
    db.commit()
    con = _mirror(db, tmp_path)
    try:
        (bar,) = _view(con, date(2026, 6, 5)).price_history(security_id)
        assert bar["d"] == date(2026, 6, 5)
    finally:
        con.close()


def test_a_form4_accepted_after_T_is_invisible_at_T(db, security_id, tmp_path):
    """Accepted Monday 06-22 10:00 ET: invisible on Friday 06-19's night — which since F6 runs to Monday 06:00
    ET (a Saturday instant would be Friday's; EDGAR is closed then) — and on the weekend's, visible Monday.
    """
    seed_big_buy(
        db,
        security_id,
        accepted=datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc),  # 10:00 ET Monday 06-22
        recorded_at=datetime(
            2026, 6, 19, tzinfo=timezone.utc
        ),  # cannot happen live; the clock still wins
    )
    db.commit()
    con = _mirror(db, tmp_path)
    try:
        for night in (date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 21)):
            assert _view(con, night).insider_txns(security_id) == [], night
        assert [
            r["valid_from"] for r in _view(con, date(2026, 6, 22)).insider_txns(security_id)
        ] == [BUY_DAY]
    finally:
        con.close()


def test_a_form4_accepted_before_T_but_recorded_after_T_is_visible_at_T(db, security_id, tmp_path):
    seed_big_buy(db, security_id)  # accepted 06-17 17:00 ET, stored 07-19's pass
    db.commit()
    assert ACCEPTED < INGESTED
    con = _mirror(db, tmp_path)
    try:
        assert _view(con, date(2026, 6, 16)).insider_txns(security_id) == []  # not public yet
        (row,) = _view(con, date(2026, 6, 17)).insider_txns(security_id)
        assert (
            row["valid_from"] == BUY_DAY
        )  # public that night, though we did not hold it until 07-19
        assert row["recorded_at"] == ACCEPTED and row["ingested_at"] == INGESTED
    finally:
        con.close()


def test_the_mirror_reads_exactly_what_the_postgres_history_read_reads(db, security_id, tmp_path):
    """One definition: on the public clock the edition's mirror and the Postgres past-asof read agree."""
    seed_tape(db, security_id)
    seed_big_buy(db, security_id)
    db.commit()
    con = _mirror(db, tmp_path, known_by=datetime.now(timezone.utc), identity=True)
    try:
        for asof in (date(2026, 6, 16), date(2026, 6, 17), date(2026, 7, 10)):
            k = known_at_for_asof(asof)
            live = PointInTimeData(db, asof=asof, known_at=k, clock="public")
            rep = ReplayPointInTimeData(con, asof=asof, known_at=k)
            for accessor in ("insider_txns", "price_history"):
                lr, rr = getattr(live, accessor)(security_id), getattr(rep, accessor)(security_id)
                key = ("valid_from", "recorded_at")
                assert [tuple(r[c] for c in key) for r in lr] == [
                    tuple(r[c] for c in key) for r in rr
                ]
            assert live.security_name(security_id) == rep.security_name(security_id)
            assert live.security_cik(security_id) == rep.security_cik(security_id) == "0001234567"
    finally:
        con.close()


# --- the freeze ceiling -------------------------------------------------------------------------------------


def test_nothing_recorded_after_the_cut_exists_in_the_mirror(db, security_id, tmp_path):
    seed_tape(db, security_id)  # 06-01 .. 07-10, each bar stored its own evening
    seed_big_buy(
        db, security_id, accession="early", recorded_at=datetime(2026, 6, 18, tzinfo=timezone.utc)
    )
    seed_big_buy(db, security_id, accession="late", recorded_at=INGESTED)
    db.commit()
    cut = datetime(2026, 7, 1, tzinfo=timezone.utc)  # 20:00 ET 06-30
    man = export_snapshot(db, tmp_path, clock="public", known_by=cut)
    assert man["known_by"] == cut.isoformat()
    con = connect_mirror(tmp_path)
    try:
        view = _view(con, date(2026, 7, 31), cut)
        assert [r["accession"] for r in view.insider_txns(security_id)] == ["early"]
        assert max(b["d"] for b in view.price_history(security_id)) == date(2026, 6, 30)
        # every row in the file was held by the cut
        for table in ("fact_insider_txn", "fact_price_eod"):
            (late,) = con.execute(
                f"SELECT count(*) FROM {table} WHERE ingested_at > ?", [cut]
            ).fetchone()
            assert late == 0
    finally:
        con.close()


def _split(db, sid, *, effective: date, ratio_from: int, recorded_at: datetime) -> None:
    """A confirmed reverse split (``ratio_from`` -> 1), stored at ``recorded_at``."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_split (tenant_id, security_id, key_date, effective_date, ratio_from, "
            "ratio_to, status, confirmed_via, value_source, action, author, valid_from, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, 1, 'confirmed', 'auto:sec+polygon', 'both', 'backfill', 'test', "
            "%s, %s)",
            (DEFAULT_TENANT_ID, sid, effective, effective, ratio_from, effective, recorded_at),
        )


def test_the_cut_reads_each_bar_on_the_scale_the_store_held_then(db, security_id, tmp_path):
    """A 1-for-10 reverse split on 09-01: the price leg re-stores the June bars x10 and the split is listed,
    both on 09-02. A mirror cut on 08-15 must read the store as it stood THEN — June bars at 1.00, on the scale
    of the tape edge held then, with no split listed. Reading the edge or the split list from today instead
    would divide the 1.00 bars by a split the cut-time store never carried (0.10)."""
    june = weekdays(date(2026, 6, 1), date(2026, 6, 30))
    for d in june:
        seed_bar(db, security_id, d, close=1.0)
    rescaled = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    for d in june:  # the split night's rescale of the stored history
        seed_bar(db, security_id, d, close=10.0, recorded_at=rescaled)
    for d in weekdays(date(2026, 9, 1), date(2026, 9, 30)):
        seed_bar(db, security_id, d, close=10.0)
    _split(db, security_id, effective=date(2026, 9, 1), ratio_from=10, recorded_at=rescaled)
    db.commit()

    now = _mirror(db, tmp_path / "now")
    cut = datetime(2026, 8, 15, tzinfo=timezone.utc)
    then = _mirror(db, tmp_path / "then", known_by=cut)
    try:
        june_30 = date(2026, 6, 30)
        # today's store, read at 06-30: the view's own day is before the split -> the pre-split scale
        assert _view(now, june_30).price_history(security_id)[-1]["close"] == 1.0
        assert _view(now, date(2026, 9, 30)).price_history(security_id)[0]["close"] == 10.0
        # the cut's store, read at 06-30 and at a later night: still 1.00, never 0.10
        assert _view(then, june_30, cut).price_history(security_id)[-1]["close"] == 1.0
        assert _view(then, date(2026, 9, 30), cut).price_history(security_id)[0]["close"] == 1.0
        assert _one(then, "SELECT count(*) FROM security_split") == (0,)
        assert _one(then, "SELECT DISTINCT split_store_through FROM fact_price_eod") == (june_30,)
    finally:
        now.close()
        then.close()


def test_without_a_cut_the_export_is_the_backtests_byte_for_byte(db, security_id, tmp_path):
    """No ceiling and no identity aux = the export before either existed: same SQL, same manifest keys."""
    w = sql.SQL("tenant_id = %(tenant_id)s")
    for t in PUBLIC_TIME:
        plain = public_versions_sql(t, where=w).as_string(db)
        assert CEILING_PARAM not in plain
        assert CEILING_PARAM in public_versions_sql(t, where=w, ceiling=True).as_string(db)
    seed_tape(db, security_id)
    seed_big_buy(db, security_id)
    db.commit()
    for clock in ("record", "public"):
        man = export_snapshot(db, tmp_path / clock, clock=clock)
        assert "known_by" not in man and "identity_table" not in man
        assert not (tmp_path / clock / f"{IDENTITY_TABLE}.parquet").exists()
        # a cut after every row changes nothing a reader sees
        late = export_snapshot(db, tmp_path / f"{clock}-cut", clock=clock, known_by=_PIN)
        assert {t: v["rows"] for t, v in man["tables"].items()} == {
            t: v["rows"] for t, v in late["tables"].items()
        }


def test_the_identity_aux_answers_like_the_live_pit(db, security_id, tmp_path):
    e = _mirror(db, tmp_path / "e", identity=True)
    p = _mirror(db, tmp_path / "p")
    try:
        live = PointInTimeData(db, asof=date(2026, 6, 1), known_at=evening(date(2026, 6, 1)))
        assert _view(e, date(2026, 6, 1)).security_cik(security_id) == live.security_cik(
            security_id
        )
        assert _view(e, date(2026, 6, 1)).security_cik(security_id) == "0001234567"
        assert (
            _view(p, date(2026, 6, 1)).security_cik(security_id) is None
        )  # a backtest mirror: unchanged
    finally:
        e.close()
        p.close()


def test_a_mirror_that_lost_its_identity_file_fails_loudly(db, security_id, tmp_path):
    export_snapshot(db, tmp_path, clock="public", identity=True)
    (tmp_path / f"{IDENTITY_TABLE}.parquet").unlink()
    with pytest.raises(FileNotFoundError, match=IDENTITY_TABLE):
        connect_mirror(tmp_path)
