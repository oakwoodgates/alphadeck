"""R8-A — PIT PARITY for the Form 4/A resolution: the DuckDB replay mirror resolves ``fact_insider_txn``
byte-for-byte like Postgres (the ONE pure resolver, fed the same visible inputs), on REAL filings, at every
transaction instant that matters — before the links, after them, before and after the operator's decisions —
on both read shapes (the basket prefetch and the per-security read). Plus the public-clock export: an
amendment becomes visible at its SEC acceptance, and the operator's standing decision rides that clock.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from db.clock import db_now
from domain.market_time import known_at_for_asof
from ingest.edgar.form4 import ingest_form4
from replay.export import AUX_TABLES, export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories import insider_amendment_repo
from signals.base import PointInTimeData
from tests.form4a_fixtures import (
    FILINGS,
    amendments_of,
    ingest_amendment,
    ingest_originals,
    make_security,
    xml,
)
from tests.replay.test_pit_parity import _canon

T_ORIG = datetime(2026, 9, 1, 2, 30, tzinfo=timezone.utc)
T_LINK = datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc)
FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
_KEY = ("accession", "insider_name", "valid_from", "txn_seq")


def _seed(db) -> dict[str, object]:
    """Every disposition the resolver acts on, from the real filings: ATCH corrections, the FLS void, the TDG
    duplicates, the SKYT pair the row tie-break settles (a system-applied partial), the CVNA ambiguous pair, the
    IEHC 4/A whose same-day later filing the order test refuses, the AZTA power-of-attorney void, the AVAV grant-only void the
    system resolved (its ``system:no-signal-effect`` decision rides the mirror too), the FRHC field change,
    and a VWAV other-issuer filing stored the way the leg stored it before R8-A."""
    sids: dict[str, object] = {}
    for t in ("ATCH", "FLS", "TDG", "SKYT", "CVNA", "IEHC-MISLINK", "AZTA", "AVAV", "FRHC"):
        sid = make_security(db, t)
        ingest_originals(db, sid, t, recorded_at=T_ORIG)
        for acc in amendments_of(t):
            ingest_amendment(db, sid, acc, recorded_at=T_LINK)
        sids[t] = sid
    vwav = make_security(db, "VWAV")
    ingest_form4(db, vwav, xml("0001731122-26-000909"), "0001731122-26-000909", recorded_at=T_ORIG)
    db.commit()
    sids["VWAV"] = vwav
    return sids


def _decide(db, sids) -> datetime:
    """The operator's decisions (recorded NOW): keep AZTA's buy (a keep stacked over the SYSTEM's application —
    R8-B), take FLS's void back from the system (revoke its application: pending, still suppressing), link +
    apply CVNA. Returns an instant BEFORE all of them."""
    before = db_now(db)
    db.commit()
    insider_amendment_repo.decide(
        db, sids["AZTA"], "0001193125-25-282945", "keep", reason="POA only", basis="operator:keep"
    )
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM insider_amendment_decision WHERE security_id = %s AND basis LIKE 'system:%%'",
            (sids["FLS"],),
        )
        (system,) = cur.fetchall()
    insider_amendment_repo.revoke(db, system["id"], reason="mine", basis="operator:revoke")
    insider_amendment_repo.link_candidate(
        db,
        sids["CVNA"],
        "0001690820-25-000275",
        "0001690820-25-000273",
        reason="S",
        basis="operator:link",
    )
    insider_amendment_repo.decide(
        db, sids["CVNA"], "0001690820-25-000275", "apply", reason="restated", basis="operator:apply"
    )
    db.commit()
    return before


def _assert_equal(live: list[dict], rep: list[dict]) -> None:
    lk = {tuple(_canon(r[c]) for c in _KEY): r for r in live}
    rk = {tuple(_canon(r[c]) for c in _KEY): r for r in rep}
    assert set(lk) == set(rk), set(lk) ^ set(rk)
    for k, lr in lk.items():
        rr = rk[k]
        assert set(lr) <= set(rr), set(lr) - set(rr)
        for col in lr:
            assert _canon(lr[col]) == _canon(rr[col]), (k, col, lr[col], rr[col])


@pytest.mark.parametrize("basket", [True, False], ids=["prefetch", "scoped"])
def test_the_mirror_resolves_amendments_exactly_like_postgres(db, tmp_path, basket):
    sids = _seed(db)
    before_decisions = _decide(db, sids)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    ids = list(sids.values())
    asofs = (
        date(2025, 8, 15),
        date(2026, 3, 31),
        date(2026, 6, 15),
        date(2026, 6, 30),
        date(2026, 10, 1),
    )
    pins = (T_LINK - timedelta(seconds=1), T_LINK, before_decisions, FAR)
    checked = 0
    for asof in asofs:
        for known_at in pins:
            live = PointInTimeData(db, asof=asof, known_at=known_at, basket=ids if basket else None)
            rep = ReplayPointInTimeData(
                con, asof=asof, known_at=known_at, basket=ids if basket else None
            )
            for sid in ids:
                _assert_equal(live.insider_txns(sid), rep.insider_txns(sid))
                checked += 1
    assert checked == len(asofs) * len(pins) * len(ids)


def test_the_mirror_reads_the_sweep_exactly_like_postgres_before_and_after_it(db, tmp_path):
    """R8-B: the sweep's SYSTEM decisions (recorded NOW over links a pre-R8-B ingest left without one) — and the
    ROW MATCH they carry — resolve on the mirror byte-for-byte like Postgres, pinned before the sweep (FRHC's
    original title, FLS's buy and SMR's sale suppressed by pending voids, MRCY's two sales) and after it (FRHC's
    corrected title; MRCY's sales STAND beside the added grant; IEHC's sales restated by FIX words; SMR's sale
    back — its 4/A retracts nothing)."""
    sids = {}
    for t in ("FRHC", "FLS", "MRCY", "IEHC", "SMR"):
        sid = make_security(db, t)
        ingest_originals(db, sid, t, recorded_at=T_ORIG)
        for acc in amendments_of(t):
            ingest_amendment(db, sid, acc, recorded_at=T_LINK)
        sids[t] = sid
    with db.cursor() as cur:  # the decision-less backlog a pre-R8-B ingest left
        cur.execute("DELETE FROM insider_amendment_decision WHERE detail->>'via' = 'ingest'")
    db.commit()
    before_sweep = db_now(db)
    db.commit()
    insider_amendment_repo.sweep(db, security_ids=list(sids.values()))
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    ids = list(sids.values())
    for known_at in (T_LINK, before_sweep, FAR):
        for asof in (date(2026, 6, 15), date(2026, 8, 31), date(2026, 10, 20)):
            live = PointInTimeData(db, asof=asof, known_at=known_at, basket=ids)
            rep = ReplayPointInTimeData(con, asof=asof, known_at=known_at, basket=ids)
            for sid in ids:
                _assert_equal(live.insider_txns(sid), rep.insider_txns(sid))
    pre = ReplayPointInTimeData(con, asof=date(2026, 10, 20), known_at=before_sweep)
    post = ReplayPointInTimeData(con, asof=date(2026, 10, 20), known_at=FAR)
    assert {r["insider_role"] for r in pre.insider_txns(sids["FRHC"])} == {
        "Chief Financial Officer"
    }
    assert {r["insider_role"] for r in post.insider_txns(sids["FRHC"])} == {
        "Special Advisor to CEO"
    }
    assert sorted(r["txn_code"] for r in pre.insider_txns(sids["MRCY"])) == ["S", "S"]
    assert sorted(r["txn_code"] for r in post.insider_txns(sids["MRCY"])) == ["A", "S", "S"]
    assert {r["accession"] for r in pre.insider_txns(sids["IEHC"])} == {"0001174947-26-000817"}
    assert {r["accession"] for r in post.insider_txns(sids["IEHC"])} == {"0001174947-26-000849"}
    assert "S" not in {r["txn_code"] for r in pre.insider_txns(sids["SMR"])}
    assert "S" in {r["txn_code"] for r in post.insider_txns(sids["SMR"])}


def test_parity_is_not_vacuous_the_resolution_moves_rows(db, tmp_path):
    """The parity above must compare RESOLVED reads, not two engines agreeing on raw rows: on the mirror the
    TDG duplicates collapse, the ATCH rows read code P, the FLS buy is gone and VWAV is screened."""
    sids = _seed(db)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    rep = ReplayPointInTimeData(
        con, asof=date(2026, 10, 1), known_at=FAR, basket=list(sids.values())
    )
    assert {r["accession"] for r in rep.insider_txns(sids["TDG"])} == {"0001352573-26-000008"}
    assert {r["txn_code"] for r in rep.insider_txns(sids["ATCH"])} == {"P"}
    assert "0001628280-26-035913" not in {r["accession"] for r in rep.insider_txns(sids["FLS"])}
    assert rep.insider_txns(sids["VWAV"]) == []
    pre = ReplayPointInTimeData(con, asof=date(2026, 10, 1), known_at=T_LINK - timedelta(seconds=1))
    assert {r["txn_code"] for r in pre.insider_txns(sids["ATCH"])} == {
        "A"
    }  # before the link: the original


def test_a_mirror_without_the_aux_tables_reads_unresolved(db, tmp_path):
    """A mirror exported BEFORE R8-A carries no aux files: it reads exactly what it held (no resolution);
    a PARTIAL aux set is a broken export and refuses to open."""
    sids = _seed(db)
    export_snapshot(db, tmp_path)
    for t in AUX_TABLES:
        (tmp_path / f"{t}.parquet").unlink()
    con = connect_mirror(tmp_path)
    rep = ReplayPointInTimeData(con, asof=date(2026, 10, 1), known_at=FAR)
    assert {r["txn_code"] for r in rep.insider_txns(sids["ATCH"])} == {"A"}
    export_snapshot(db, tmp_path)
    (tmp_path / "insider_amendment_link.parquet").unlink()
    with pytest.raises(FileNotFoundError, match="partial insider-amendment aux set"):
        connect_mirror(tmp_path)


def test_public_clock_shows_an_amendment_at_its_sec_acceptance(db, tmp_path):
    sid = make_security(db, "ATCH")
    # originals carry their own acceptance (the public clock); the 4/As are linked "tonight" (system time)
    ingest_originals(db, sid, "ATCH", recorded_at=T_LINK)
    for acc in amendments_of("ATCH"):
        ingest_amendment(db, sid, acc, recorded_at=FAR - timedelta(days=1))
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    first = min(FILINGS[a].accepted for a in amendments_of("ATCH"))  # 2026-09-29T01:01:24Z
    last = max(FILINGS[a].accepted for a in amendments_of("ATCH"))  # 2026-09-29T01:01:30Z
    asof = date(2026, 9, 28)
    # the originals were public at 17:22Z on 09-28; the 4/As at 21:01 ET the same market day
    before = ReplayPointInTimeData(con, asof=asof, known_at=first - timedelta(seconds=1))
    assert {r["txn_code"] for r in before.insider_txns(sid)} == {"A"}
    after = ReplayPointInTimeData(con, asof=asof, known_at=last)
    assert {r["txn_code"] for r in after.insider_txns(sid)} == {"P"}
    # lockstep's end of the 09-28 market day already holds the 4/As (accepted 21:01 ET that evening)
    eod = ReplayPointInTimeData(con, asof=asof, known_at=known_at_for_asof(asof, FAR))
    assert {r["txn_code"] for r in eod.insider_txns(sid)} == {"P"}


def test_public_clock_carries_only_the_standing_decision_at_the_acceptance(db, tmp_path):
    sid = make_security(db, "AZTA")
    ingest_originals(db, sid, "AZTA", recorded_at=T_ORIG)
    ingest_amendment(db, sid, "0001193125-25-282945", recorded_at=T_LINK)
    keep = insider_amendment_repo.decide(
        db, sid, "0001193125-25-282945", "keep", reason="POA", basis="operator:keep"
    )
    insider_amendment_repo.revoke(db, keep["id"], reason="undo", basis="operator:revoke")
    insider_amendment_repo.decide(
        db, sid, "0001193125-25-282945", "keep", reason="POA, again", basis="operator:keep"
    )
    db.commit()
    manifest = export_snapshot(db, tmp_path, clock="public")
    assert (
        manifest["aux_tables"]["insider_amendment_decision"]["rows"] == 1
    )  # the standing keep only
    con = connect_mirror(tmp_path)
    accepted = FILINGS["0001193125-25-282945"].accepted
    rows = con.execute("SELECT action, recorded_at FROM insider_amendment_decision").fetchall()
    assert [r[0] for r in rows] == ["keep"] and rows[0][1] == accepted
    rep = ReplayPointInTimeData(con, asof=date(2025, 8, 15), known_at=accepted)
    assert any(r["txn_code"] == "P" for r in rep.insider_txns(sid))  # kept: the buy stands
