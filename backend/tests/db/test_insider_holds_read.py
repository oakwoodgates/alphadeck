"""Rule ``2026-11-insider-rows-dated-after-filing`` — the insider READ resolves held rows (``domain.insider_holds``),
on REAL filings (``tests/held_fixtures.py``), through the real as-of read on BOTH clocks and through the replay
twin (record and public mirrors): an on-time filing's held row counts from its filing night, a late one waits
for a steward, the steward's decision is honored, the filer's own 4/A wins, and a trade is counted ONCE.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from replay.export import HOLD_TABLES, export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals.base import PointInTimeData
from tests import held_fixtures as H
from tests.replay.test_pit_parity import _canon

FAR = H.FAR
_KEY = ("accession", "insider_name", "valid_from", "txn_seq", "hold_id")


def _cap(asof: date) -> datetime:
    """The end of night ``asof`` — what a history view of that night reads."""
    return known_at_for_asof(asof, FAR)


def _read(db, sid, asof: date, known_at: datetime | None = None, clock: str = "public"):
    return PointInTimeData(
        db, asof=asof, known_at=known_at or _cap(asof), clock=clock, workspace=DEFAULT_TENANT_ID
    ).insider_txns(sid)


def _dates(rows) -> list[tuple]:
    """The rows as (date, code, shares, how dated), sorted — what each expectation pins."""
    return sorted(
        (r["valid_from"], r["txn_code"], float(r["shares"]), r.get("date_basis")) for r in rows
    )


def _both(db, sid, asof: date, known_at: datetime | None = None) -> list[tuple]:
    """The read on the PUBLIC and the RECORD clock — equal for these fixtures (each filing is ingested a minute
    after its acceptance), so one expectation pins both."""
    pub = _dates(_read(db, sid, asof, known_at, "public"))
    rec = _dates(_read(db, sid, asof, known_at, "record"))
    assert pub == rec
    return pub


def _count_holds(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_txn_hold")
        return cur.fetchone()["n"]


FN = "filing_night"


# --- the placement rule: on-time filings count from the filing night ------------------------------------------


def test_on_time_buys_dated_after_the_filing_count_from_the_filing_night(db):
    sid = H.seed(db, "on_time_a")
    assert _count_holds(db) == 3  # every row is dated after the filing: all held, none stored
    assert _both(db, sid, date(2015, 1, 1)) == []
    night = [(date(2015, 1, 2), "P", s, FN) for s in (300.0, 500.0, 500.0)]
    assert _both(db, sid, date(2015, 1, 2)) == night
    # never at the written date, and never twice: a read after the written date still reads the three once
    assert _both(db, sid, date(2016, 1, 4)) == night


def test_an_on_time_buy_dated_two_months_after_its_filing_counts_from_the_filing_night(db):
    sid = H.seed(db, "on_time_b")
    assert _both(db, sid, date(2010, 8, 9)) == []
    assert _both(db, sid, date(2010, 8, 10)) == [(date(2010, 8, 10), "P", 3000.0, FN)]
    assert _both(db, sid, date(2010, 10, 11)) == [(date(2010, 8, 10), "P", 3000.0, FN)]


def test_impossible_year_rows_of_an_on_time_filing_count_from_the_filing_night(db):
    sid = H.seed(db, "impossible_year")
    assert _both(db, sid, date(2024, 5, 27)) == []
    rows = _both(db, sid, date(2024, 5, 28))
    assert rows == sorted(
        [(date(2024, 5, 28), "S", s, None) for s in (2511.0, 162176.0, 24883.0, 100.0)]
        + [(date(2024, 5, 28), "S", s, FN) for s in (140035.0, 49635.0)]
    )
    # the as-filed date rides on the held rows, the four stored rows read as stored (no new keys)
    full = _read(db, sid, date(2024, 5, 28))
    held = [r for r in full if r.get("date_basis")]
    assert sorted(r["txn_date_as_filed"] for r in held) == ["2028-05-24", "2028-05-24"]
    assert all("date_basis" not in r for r in full if not r.get("date_basis"))


def test_a_filer_signing_ahead_of_new_york_is_not_held_and_reads_as_stored(db):
    sid = H.seed(db, "signed_ahead")
    assert _count_holds(db) == 0
    assert _both(db, sid, date(2014, 7, 25)) == [(date(2014, 7, 25), "S", 18239594.0, None)]


# --- a late filing waits for the steward ------------------------------------------------------------------


LATE = "0000899243-21-012557"


def test_a_late_filing_waits_until_a_steward_approves_then_counts_at_the_ratified_dates(db):
    sid = H.seed(db, "late")
    assert _count_holds(db) == 3
    for asof in (date(2021, 3, 18), date(2021, 12, 6), date(2022, 6, 1)):
        assert _both(db, sid, asof) == []
    t1 = datetime(2022, 1, 10, 15, tzinfo=timezone.utc)
    H.decide(db, sid, LATE, 0, "approve", at=t1, ratified=date(2020, 11, 16))
    H.decide(db, sid, LATE, 1, "approve", at=t1, ratified=date(2020, 12, 3))
    asof = date(2022, 6, 1)
    # a read before the decision was made (on the public clock: when the steward decided) still waits
    assert _both(db, sid, asof, known_at=t1 - timedelta(seconds=1)) == []
    assert _both(db, sid, asof, known_at=t1) == [
        (date(2020, 11, 16), "P", 800.0, "ratified"),
        (date(2020, 12, 3), "P", 300.0, "ratified"),
    ]
    # the asof gate applies to the ratified date
    assert _both(db, sid, date(2020, 11, 30), known_at=FAR) == [
        (date(2020, 11, 16), "P", 800.0, "ratified")
    ]


def test_dismiss_returns_nothing_and_a_revoke_restores_the_earlier_state(db):
    sid = H.seed(db, "late")
    t1 = datetime(2022, 1, 10, 15, tzinfo=timezone.utc)
    approve = H.decide(db, sid, LATE, 0, "approve", at=t1, ratified=date(2020, 11, 16))
    t2 = t1 + timedelta(days=1)
    dismiss = H.decide(db, sid, LATE, 0, "dismiss", at=t2)
    asof = date(2022, 6, 1)
    approved = [(date(2020, 11, 16), "P", 800.0, "ratified")]
    assert _both(db, sid, asof, known_at=t1) == approved
    assert _both(db, sid, asof, known_at=t2) == []  # the newest standing decision: dismiss
    t3 = t2 + timedelta(days=1)
    H.decide(db, sid, LATE, 0, "revoke", at=t3, revokes=dismiss)
    assert (
        _both(db, sid, asof, known_at=t3) == approved
    )  # the dismiss undone: the approve stands again
    t4 = t3 + timedelta(days=1)
    H.decide(db, sid, LATE, 0, "revoke", at=t4, revokes=approve)
    assert _both(db, sid, asof, known_at=t4) == []  # nothing standing: it waits again


def test_the_decision_table_refuses_a_bad_shape(db):
    import psycopg

    sid = H.seed(db, "late")
    t = datetime(2022, 1, 10, tzinfo=timezone.utc)
    with pytest.raises(psycopg.errors.CheckViolation):
        H.decide(db, sid, LATE, 0, "approve", at=t)  # an approve needs its ratified date
    db.rollback()
    d = H.decide(db, sid, LATE, 0, "dismiss", at=t)
    r = H.decide(db, sid, LATE, 0, "revoke", at=t, revokes=d)
    with pytest.raises(psycopg.errors.RaiseException):
        H.decide(db, sid, LATE, 0, "revoke", at=t, revokes=r)  # never a revoke of a revoke
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        H.decide(db, sid, LATE, 1, "revoke", at=t, revokes=d)  # a revoke names the same held row
    db.rollback()


# --- the filer's own correction wins ------------------------------------------------------------------------


def test_the_filers_4a_restating_an_after_filing_row_counts_the_trade_once(db):
    sid = H.seed(db, "amended_after_filing")
    asof = date(2020, 3, 20)
    between = H.FILINGS["0001179110-20-004036"].accepted - timedelta(minutes=1)
    # before the 4/A is public: the two stored rows and the held row from the filing night
    assert _both(db, sid, asof, known_at=between) == [
        (date(2020, 3, 18), "P", 2200.0, None),
        (date(2020, 3, 20), "P", 750.0, FN),
        (date(2020, 3, 20), "P", 800.0, None),
    ]
    # once the 4/A counts: its three rows, the 750-share trade once, from the amendment
    after = _read(db, sid, asof)
    assert _dates(after) == [
        (date(2020, 3, 18), "P", 2200.0, None),
        (date(2020, 3, 20), "P", 750.0, None),
        (date(2020, 3, 20), "P", 800.0, None),
    ]
    assert {r["accession"] for r in after} == {"0001179110-20-004036"}
    assert _both(db, sid, asof) == _dates(after)


def test_the_filers_4a_restating_an_impossible_year_row_replaces_the_held_row(db):
    sid = H.seed(db, "amended_impossible_year")
    before = _read(db, sid, date(2023, 11, 20))
    assert _dates(before) == [
        (date(2023, 11, 16), "S", 15000.0, None),
        (date(2023, 11, 17), "S", 15000.0, FN),
    ]
    assert _both(db, sid, date(2023, 11, 20)) == _dates(before)
    after = _read(db, sid, date(2023, 11, 29))
    trade = [r for r in after if float(r["price"]) == pytest.approx(17.7108)]
    assert len(trade) == 1  # counted once
    assert trade[0]["accession"] == "0001628280-23-040161" and not trade[0].get("date_basis")
    assert trade[0]["valid_from"] == date(2023, 11, 17)
    assert not any(r.get("hold_id") for r in after)


# --- the replay twin -------------------------------------------------------------------------------------------


def _seed_all(db):
    sids = {g: H.seed(db, g) for g in H.CIKS}
    t1 = datetime(2022, 1, 10, 15, tzinfo=timezone.utc)
    approve = H.decide(db, sids["late"], LATE, 0, "approve", at=t1, ratified=date(2020, 11, 16))
    H.decide(db, sids["late"], LATE, 1, "dismiss", at=t1)
    H.decide(db, sids["late"], LATE, 0, "revoke", at=t1 + timedelta(days=2), revokes=approve)
    return sids, t1


_ASOFS = (
    date(2010, 8, 10),
    date(2014, 7, 25),
    date(2015, 1, 2),
    date(2016, 1, 4),
    date(2020, 3, 20),
    date(2022, 6, 1),
    date(2023, 11, 20),
    date(2023, 11, 29),
    date(2024, 5, 28),
)


def _assert_equal(live: list[dict], rep: list[dict]) -> None:
    lk = {tuple(_canon(r.get(c)) for c in _KEY): r for r in live}
    rk = {tuple(_canon(r.get(c)) for c in _KEY): r for r in rep}
    assert len(lk) == len(live) and len(rk) == len(rep)
    assert set(lk) == set(rk), set(lk) ^ set(rk)
    for k, lr in lk.items():
        rr = rk[k]
        assert set(lr) <= set(rr), set(lr) - set(rr)
        for col in lr:
            assert _canon(lr[col]) == _canon(rr[col]), (k, col, lr[col], rr[col])


@pytest.mark.parametrize("clock", ["record", "public"])
@pytest.mark.parametrize("basket", [True, False], ids=["prefetch", "scoped"])
def test_the_mirror_resolves_holds_exactly_like_postgres(db, tmp_path, clock, basket):
    sids, t1 = _seed_all(db)
    manifest = export_snapshot(db, tmp_path, clock=clock, workspace=DEFAULT_TENANT_ID)
    assert set(manifest["hold_tables"]) == set(HOLD_TABLES)
    assert manifest["hold_tables"]["insider_txn_hold"]["rows"] == _count_holds(db) > 0
    assert manifest["hold_tables"]["insider_txn_hold_decision"]["rows"] == 3
    con = connect_mirror(tmp_path)
    ids = list(sids.values())
    checked = 0
    for asof in _ASOFS:
        for known_at in (_cap(asof), t1, t1 + timedelta(days=3), FAR):
            live = PointInTimeData(
                db,
                asof=asof,
                known_at=known_at,
                basket=ids if basket else None,
                clock=clock,
                workspace=DEFAULT_TENANT_ID,
            )
            rep = ReplayPointInTimeData(
                con,
                asof=asof,
                known_at=known_at,
                basket=ids if basket else None,
                workspace=DEFAULT_TENANT_ID,
            )
            for sid in ids:
                lr, rr = live.insider_txns(sid), rep.insider_txns(sid)
                _assert_equal(lr, rr)
                checked += bool(lr)
    assert checked > 20  # the comparison is not vacuous


def test_a_mirror_without_the_hold_tables_reads_no_holds_and_a_partial_pair_refuses(db, tmp_path):
    sid = H.seed(db, "on_time_b")
    export_snapshot(db, tmp_path, workspace=DEFAULT_TENANT_ID)
    for t in HOLD_TABLES:
        (tmp_path / f"{t}.parquet").unlink()
    with pytest.raises(FileNotFoundError, match="records"):
        connect_mirror(tmp_path)  # the manifest records the pair: a lost file is loud
    manifest = tmp_path / "manifest.json"
    import json

    blob = json.loads(manifest.read_text(encoding="utf-8"))
    blob.pop("hold_tables")  # a mirror exported before the pair existed
    manifest.write_text(json.dumps(blob), encoding="utf-8")
    con = connect_mirror(tmp_path)
    rep = ReplayPointInTimeData(
        con, asof=date(2010, 8, 10), known_at=FAR, workspace=DEFAULT_TENANT_ID
    )
    assert rep.insider_txns(sid) == []  # no holds on the mirror: nothing to count (what it held)
    export_snapshot(db, tmp_path, workspace=DEFAULT_TENANT_ID)
    (tmp_path / "insider_txn_hold_decision.parquet").unlink()
    with pytest.raises(FileNotFoundError, match="partial held-row set"):
        connect_mirror(tmp_path)
