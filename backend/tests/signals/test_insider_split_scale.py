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

from datetime import date, datetime, timezone

import pytest

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.market_time import serve_view
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


# --- DB: HISTORY views on the PUBLIC clock (#1, the operator's rule of 2026-10-21) --------------------------


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
    fx.seed_splits(db, sid, "OPAD")
    db.commit()
    assert tool.apply_plans(db, tool.plan_all(db, tickers=["OPAD"], today=T)).appended > 400
    return sid


def _history(db, asof: date) -> PointInTimeData:
    known_at, clock = serve_view(asof)
    assert clock == "public"  # a past asof: a history view
    return PointInTimeData(db, asof=asof, known_at=known_at, clock=clock)


def test_a_split_counts_from_its_own_publication_never_from_the_day_we_listed_it(db):
    """The list rows (and the repaired bars) were written TODAY. On a history view OPAD's 1-for-10 is listed
    from its public date (06-09: its first session — no earlier SEC filing states it) and not a day before —
    and either way a view before 06-09 reads the tape on its own day's scale, so no factor applies there.
    """
    sid = _seed_opad(db)
    assert _history(db, date(2026, 6, 8)).split_events(sid) == []
    on = _history(db, date(2026, 6, 9)).split_events(sid)
    assert [(r["effective_date"], r["status"]) for r in on] == [(date(2026, 6, 9), "confirmed")]


@pytest.mark.parametrize("asof, k", [(date(2026, 6, 5), 1.0), (date(2026, 6, 15), 10.0)])
def test_on_a_history_view_the_ceos_buys_are_judged_on_that_days_scale(db, asof, k):
    """06-05 is BEFORE the split (and after both Form 4s were accepted, 06-04 and 06-05 — when they became
    public): the view reads the repaired tape divided back to 06-05's scale (the bar of 06-02 at its as-traded
    ~$0.8, the scale the CEO paid $0.8208 on) and applies no factor. 06-15 is after:
    the view reads the post-split scale and puts the price x10. Both keep the buys (B6's cron-side half: on
    the old record-clock factor a history view before the split dropped them against the repaired tape).
    """
    sid = _seed_opad(db)
    pit = _history(db, asof)
    scale = tape_scale(pit.split_events(sid), through=asof)
    assert scale(date(2026, 6, 2)) == k
    bar = next(b for b in pit.price_history(sid) if b["d"] == date(2026, 6, 2))
    stored = next(r for r in fx.rows("OPAD") if r["d"] == date(2026, 6, 2))
    # the repaired store holds 06-02 x10 (the list's scale); the view puts it on ITS day's scale
    assert float(bar["low"]) == pytest.approx(float(stored["low"]) * k, rel=1e-9)
    ev = insider_conviction.detect(pit, sid, asof, DEFAULT_CONFIG)
    assert ev is not None and "$200,002" in ev.label


def test_the_public_mirror_reads_the_split_list_and_the_scale_exactly_like_postgres(db, tmp_path):
    """The backtest's public mirror (``export_snapshot(clock="public")``) materializes the SAME split read and
    the same price rows (``db.public_clock``), and the mirror view converts them with the SAME pure function
    (``domain.splits.on_view_scale``): the split list, every bar and the call agree at a view before the
    split and one after it."""
    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid = _seed_opad(db)
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)

    def splits(rows):
        return [
            (r["key_date"], r["effective_date"], float(r["ratio_from"]), r["status"]) for r in rows
        ]

    def bars(rows):
        return [
            (r["d"], *(None if r[c] is None else float(r[c]) for c in ("low", "close", "volume")))
            for r in rows
        ]

    for asof in (date(2026, 6, 5), date(2026, 6, 15)):
        live = _history(db, asof)
        rep = ReplayPointInTimeData(con, asof=asof, known_at=live.known_at)
        assert splits(rep.split_events(sid)) == splits(live.split_events(sid))
        assert bars(rep.price_history(sid)) == bars(live.price_history(sid))
        a = insider_conviction.detect(live, sid, asof, DEFAULT_CONFIG)
        b = insider_conviction.detect(rep, sid, asof, DEFAULT_CONFIG)
        assert a is not None and b is not None and a.label == b.label


def test_an_old_mirror_without_the_split_table_still_opens_and_reads_no_splits(db, tmp_path):
    """A frozen backtest mirror exported before S2 has no ``security_split.parquet`` (and no manifest key):
    it opens unchanged and replays as it always did — no split listed."""
    import json

    from replay.export import MANIFEST_NAME, SPLIT_TABLE, export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid = _seed_opad(db)
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


def test_a_mirror_whose_manifest_records_the_split_table_but_lost_the_file_fails_loudly(
    db, tmp_path
):
    """The B5 review fix: a post-S2 mirror that LOST ``security_split.parquet`` is a broken export, not an old
    one — its manifest says the file was written, so opening it raises instead of silently replaying with the
    pre-S2 behavior (genuine pre-split buys dropped)."""
    from replay.export import SPLIT_TABLE, export_snapshot, read_mirror_manifest
    from replay.pit import connect_mirror

    _seed_opad(db)
    export_snapshot(db, tmp_path)
    assert read_mirror_manifest(tmp_path).split_table is True
    (tmp_path / f"{SPLIT_TABLE}.parquet").unlink()
    with pytest.raises(FileNotFoundError, match=SPLIT_TABLE):
        connect_mirror(tmp_path)


@pytest.mark.parametrize("recorded", [True, False])
def test_a_run_directory_reopened_as_a_mirror_is_held_to_its_split_files_record(
    db, tmp_path, recorded
):
    """B5's follow-up: a run that exports its own mirror overwrites the mirror's manifest with the RUN manifest,
    so the run's ``MirrorInfo`` carries the split file's record too. A run that recorded the file and lost it
    refuses to open; one from before S2 (``split_table`` null) opens and reads no splits."""
    import json

    from backtest.manifest import MirrorInfo, SplitTableInfo
    from replay.export import MANIFEST_NAME, SPLIT_TABLE, export_snapshot, read_mirror_manifest
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid = _seed_opad(db)
    export_snapshot(db, tmp_path)
    info = MirrorInfo(hash="0" * 64, split_table=SplitTableInfo(rows=1) if recorded else None)
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps({"run_id": "r", "clock": "record", "mirror": info.model_dump(mode="json")}),
        encoding="utf-8",
    )
    (tmp_path / f"{SPLIT_TABLE}.parquet").unlink()
    assert read_mirror_manifest(tmp_path).split_table is recorded
    if recorded:
        with pytest.raises(FileNotFoundError, match=SPLIT_TABLE):
            connect_mirror(tmp_path)
        return
    con = connect_mirror(tmp_path)
    rep = ReplayPointInTimeData(
        con, asof=date(2026, 6, 15), known_at=datetime(2100, 1, 1, tzinfo=timezone.utc)
    )
    assert rep.split_events(sid) == []
