"""S2 — the insider price screen on the split list's scale, on REAL data: the Form 4 states the price AS TRADED,
the stored tape is on the list's scale, so the screen compares the price x the factor of the splits after the
trade (``domain.splits.tape_scale``). Real buys (``tests/split_s2_fixtures``):

- OPAD — CEO Brian Bair bought 125,000 shares at $0.8208 on 2026-06-02 and 122,920 at $0.7924 on 06-03, a
  week before OPAD's 1-for-10 (06-09). On the repaired tape that day's low is ~$8: without the factor the buys
  read "below the day's low" and dropped out of the call.
- BURU — co-CEO Alessandro Zamboni bought $1.5M at $0.3453 on 2025-12-31, before the 1-for-4.99 the vendor
  applied (2026-03-02) and the 1-for-40 it did not (09-02): dropped TODAY already (the vendor's x4.99), kept
  with the chain's x199.6.
- YYAI — a 10% owner bought $1.0M at $0.3205 on 2025-10-08, before three splits the vendor applied (x40,000).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from db.bitemporal import append_fact
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.splits import applicable, pending, tape_scale
from ingest.prices.eod_loader import ingest_prices
from pipeline import repair_split_basis as tool
from pipeline import split_repair as sr
from signals import insider_conviction
from signals.base import PointInTimeData
from signals.display import insider_flow
from tests import split_s2_fixtures as fx

T = fx.TODAY


def _lows(bars) -> dict[date, float]:
    return {b.d: b.low for b in bars if b.low is not None}


def _repaired(tk: str) -> list[sr.Bar]:
    sp = fx.splits(tk)
    c = sr.classify(fx.bars(tk), applicable(sp, on=T), today=T, transparent=pending(sp, on=T))
    assert c.ok, c.refusals
    return sr.apply_labels(fx.bars(tk), c.labels)


def _scale(tk: str, through: date):
    return tape_scale(fx.tape(tk)["splits"], through=through)


def test_opad_ceo_buys_stay_counted_on_the_repaired_tape():
    sid = fx.tape("OPAD")["security_id"]
    txns = fx.insider_rows("OPAD")
    assert {t["valid_from"] for t in txns} == {date(2026, 6, 2), date(2026, 6, 3)}
    asof = date(2026, 6, 15)
    lows = _lows(_repaired("OPAD"))
    assert lows[date(2026, 6, 2)] > 7  # the adjusted day low
    dropped = insider_conviction.score(txns, sid, asof, DEFAULT_CONFIG, day_lows=lows)
    assert dropped is None  # the bug the repair would have made: the CEO's buys read as off-market
    kept = insider_conviction.score(
        txns, sid, asof, DEFAULT_CONFIG, day_lows=lows, price_scale=_scale("OPAD", T)
    )
    assert kept is not None and "$200,002" in kept.label


def test_before_the_repair_the_scaled_price_is_still_kept_on_the_raw_tape():
    """Between the code shipping and the repair being applied the list knows the split but the tape is still
    raw: a reverse split only RAISES the scaled price, so the below-low screen cannot drop the buy.
    """
    sid = fx.tape("OPAD")["security_id"]
    lows = _lows(fx.bars("OPAD"))
    ev = insider_conviction.score(
        fx.insider_rows("OPAD"),
        sid,
        date(2026, 6, 15),
        DEFAULT_CONFIG,
        day_lows=lows,
        price_scale=_scale("OPAD", T),
    )
    assert ev is not None


def test_buru_chain_one_split_the_vendor_applied_one_it_did_not():
    sid = fx.tape("BURU")["security_id"]
    txns = fx.insider_rows("BURU")
    asof = date(2026, 3, 20)  # inside the core liveness of the 12-31 senior $1.5M buy
    today_lows = _lows(fx.bars("BURU"))  # the vendor's x4.99 already in the stored tape
    assert insider_conviction.score(txns, sid, asof, DEFAULT_CONFIG, day_lows=today_lows) is None
    scale = _scale("BURU", T)
    assert scale(date(2025, 12, 31)) == pytest.approx(4.99 * 40)
    ev = insider_conviction.score(
        txns, sid, asof, DEFAULT_CONFIG, day_lows=_lows(_repaired("BURU")), price_scale=scale
    )
    assert ev is not None and ev.grade is not None


def test_yyai_buy_before_three_vendor_applied_splits():
    sid = fx.tape("YYAI")["security_id"]
    txns = fx.insider_rows("YYAI")
    lows = _lows(fx.bars("YYAI"))
    asof = date(2025, 10, 20)
    assert insider_conviction.score(txns, sid, asof, DEFAULT_CONFIG, day_lows=lows) is None
    scale = _scale("YYAI", T)
    assert scale(date(2025, 10, 8)) == 50 * 40 * 20
    assert (
        insider_conviction.score(txns, sid, asof, DEFAULT_CONFIG, day_lows=lows, price_scale=scale)
        is not None
    )


def test_the_display_flow_counts_the_buy_the_same_way():
    lows = _lows(_repaired("OPAD"))
    rows = fx.insider_rows("OPAD")
    asof = date(2026, 6, 15)
    without = insider_flow.compute(rows, asof, day_lows=lows)
    with_scale = insider_flow.compute(rows, asof, day_lows=lows, price_scale=_scale("OPAD", T))
    count = lambda sig: next(m.value for m in sig.metrics if m.key == "buy_count")  # noqa: E731
    assert count(without) == 0 and count(with_scale) == 2


def test_tape_scale_applies_only_splits_in_force_through_the_view():
    rows = fx.tape("MI")["splits"]  # MI's 1-for-80 still needs review: not in force, never applied
    assert tape_scale(rows, through=T)(date(2026, 1, 5)) == 1.0
    opad = fx.tape("OPAD")["splits"]
    assert (
        tape_scale(opad, through=date(2026, 6, 8))(date(2026, 6, 2)) == 1.0
    )  # before its first session
    assert tape_scale(opad, through=date(2026, 6, 9))(date(2026, 6, 2)) == 10.0
    assert tape_scale(opad, through=T)(date(2026, 6, 9)) == 1.0  # traded on the new scale


# --- DB: no lookahead on the transaction axis (#1) ------------------------------------------------------------


def _seed_opad(db):
    sid = fx.seed_security(db, "OPAD")
    ingest_prices(db, sid, fx.rows("OPAD"), tenant_id=DEFAULT_TENANT_ID)
    for t in fx.insider_rows("OPAD"):
        append_fact(
            db,
            "fact_insider_txn",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                **{
                    k: t[k]
                    for k in (
                        "accession",
                        "insider_name",
                        "insider_role",
                        "txn_code",
                        "txn_seq",
                        "price",
                        "shares",
                        "usd",
                        "aff_10b5_1",
                        "rpt_owner_cik",
                        "issuer_cik",
                        "issuer_name",
                        "security_title",
                        "issuer_foreign_symbol",
                        "accepted",
                    )
                },
                "valid_from": t["valid_from"],
            },
        )
    db.commit()
    before_list = db_now(db)
    db.commit()  # the split rows get a NEW transaction, so their now() is after ``before_list``
    fx.seed_splits(db, sid, "OPAD")
    db.commit()
    tool.apply_plans(db, tool.plan_all(db, tickers=["OPAD"], today=T))
    return sid, before_list


def test_a_split_not_yet_recorded_at_known_at_is_never_applied(db):
    sid, before_list = _seed_opad(db)
    asof = date(2026, 6, 15)
    early = PointInTimeData(db, asof=asof, known_at=before_list)
    assert early.split_events(sid) == []  # the list was not recorded yet
    assert tape_scale(early.split_events(sid), through=before_list.date())(date(2026, 6, 2)) == 1.0
    # ... and at that instant the tape was still raw, so the buy is kept on its own as-traded scale
    assert insider_conviction.detect(early, sid, asof, DEFAULT_CONFIG) is not None
    now = PointInTimeData(db, asof=asof)
    assert [r["effective_date"] for r in now.split_events(sid)] == [date(2026, 6, 9)]
    ev = insider_conviction.detect(now, sid, asof, DEFAULT_CONFIG)
    assert ev is not None and "$200,002" in ev.label


def test_a_past_asof_read_at_a_later_known_at_uses_the_splits_that_tape_carries(db):
    """The pin-mode read (a backtest pinned at now, the as-of slider): asof 06-03 is BEFORE the split, but the
    bars read at known_at = now are repaired — so the factor comes from the splits recorded and in force by
    known_at's market day, not by asof."""
    sid, _ = _seed_opad(db)
    asof = date(2026, 6, 3)
    pit = PointInTimeData(db, asof=asof)
    ev = insider_conviction.detect(pit, sid, asof, DEFAULT_CONFIG)
    assert ev is not None


def test_the_replay_mirror_reads_the_same_split_list(db, tmp_path):
    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid, before_list = _seed_opad(db)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    asof = date(2026, 6, 15)
    for known_at in (before_list, db_now(db) + timedelta(seconds=1)):
        live = PointInTimeData(db, asof=asof, known_at=known_at)
        rep = ReplayPointInTimeData(con, asof=asof, known_at=known_at)
        key = lambda rows: [  # noqa: E731
            (
                r["key_date"],
                r["effective_date"],
                float(r["ratio_from"]),
                float(r["ratio_to"]),
                r["status"],
            )
            for r in rows
        ]
        assert key(rep.split_events(sid)) == key(live.split_events(sid))
        a = insider_conviction.detect(live, sid, asof, DEFAULT_CONFIG)
        b = insider_conviction.detect(rep, sid, asof, DEFAULT_CONFIG)
        assert (a is None) == (b is None) and (a is None or a.label == b.label)


def test_an_old_mirror_without_the_split_table_still_opens_and_reads_no_splits(db, tmp_path):
    """A frozen backtest mirror exported before S2 has no ``security_split.parquet`` (and no manifest key):
    it opens unchanged and replays as it always did — no split listed."""
    import json

    from replay.export import MANIFEST_NAME, SPLIT_TABLE, export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid, _ = _seed_opad(db)
    export_snapshot(db, tmp_path)
    (tmp_path / f"{SPLIT_TABLE}.parquet").unlink()
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest.pop("split_table")
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    con = connect_mirror(tmp_path)
    rep = ReplayPointInTimeData(
        con, asof=date(2026, 6, 15), known_at=datetime(2100, 1, 1, tzinfo=timezone.utc)
    )
    assert rep.split_events(sid) == []
    assert rep.price_history(sid)  # the facts are all there
