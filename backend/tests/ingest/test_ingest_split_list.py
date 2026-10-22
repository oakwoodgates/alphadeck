"""The price leg with the split list (S2), DB-backed: N nightly pulls of the vendor's REAL 2026-10-20 series for
BTOC and OPTT through ``ingest_bars_for_security`` on the REAL stored tapes, before and after the repair.
COUNT THE TABLE: every assertion on what a pass wrote counts ``fact_price_eod`` rows, never the as-of read.

Two pulls are stated contracts on the real series with one stated edit each (``test_split_normalize``): the
adjusted pull (the real pull on the list's scale) and the one-bar correction (+2% on one real close).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.prices.eod_loader import ingest_prices
from ingest.prices.ingest_security import ingest_bars_for_security
from pipeline import repair_split_basis as tool
from repositories import split_repo
from tests import split_s2_fixtures as fx

NIGHTS = [datetime(2026, 10, 21 + i, 2, 30, tzinfo=timezone.utc) for i in range(4)]


class _Vendor:
    """The vendor serving one fixed series (the real pull), re-fetched every night."""

    def __init__(self, bars):
        self.bars = bars

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return [dict(b) for b in self.bars]


def _rows(db, sid) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (sid,))
        return cur.fetchone()["n"]


def _holds(db, sid) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM price_restatement_hold WHERE security_id = %s", (sid,)
        )
        return cur.fetchone()["n"]


def _seed(db, tk: str):
    sid = fx.seed_security(db, tk)
    fx.seed_splits(db, sid, tk)
    ingest_prices(db, sid, fx.rows(tk), tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    return sid


def _repair(db, tk: str) -> int:
    plans = tool.plan_all(db, tickers=[tk], today=fx.TODAY)
    assert [p.status for p in plans] == [tool.REPAIR], plans[0].refusals
    res = tool.apply_plans(db, plans)
    return res.appended


def _night(db, sid, tk, vendor, now):
    sec = Security(
        id=sid, tenant_id=DEFAULT_TENANT_ID, ticker=tk, price_symbol=fx.tape(tk)["price_symbol"]
    )
    res = ingest_bars_for_security(
        db,
        sec,
        tenant_id=DEFAULT_TENANT_ID,
        allow_live=True,
        force_refresh=True,
        source=vendor,
        now=now,
    )
    db.commit()
    return res


@pytest.mark.parametrize("tk", ["BTOC", "OPTT"])
def test_with_the_list_the_repaired_tape_survives_n_nights_of_the_real_raw_series(db, tk):
    sid = _seed(db, tk)
    appended = _repair(db, tk)
    assert appended > 400
    vendor = _Vendor(fx.yahoo(tk))
    n0 = _rows(db, sid)
    for now in NIGHTS:
        res = _night(db, sid, tk, vendor, now)
        assert (res.appended, res.reversioned, res.held) == (0, 0, 0)
        assert res.normalized > 400  # read on the list's scale, nothing written
    assert _rows(db, sid) == n0
    assert _holds(db, sid) == 0


@pytest.mark.parametrize("tk", ["BTOC", "OPTT"])
def test_without_the_list_the_repair_is_undone_after_two_nights(db, tk):
    """The regression: the split revoked from the list (S1's own verb), the same real series is a REVERT to
    values the store held — held on night 1, re-applied on night 2."""
    sid = _seed(db, tk)
    appended = _repair(db, tk)
    for v in split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[sid]):
        if v.status == "confirmed":
            split_repo.revoke(db, v.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    vendor = _Vendor(fx.yahoo(tk))
    n0 = _rows(db, sid)
    first = _night(db, sid, tk, vendor, NIGHTS[0])
    # T5 holds the older set; a pre-split bar inside the pull's newest three sessions keeps the exact compare
    # and re-versions at once (BTOC 10-13: the vendor's 10-14 / 10-15 are null), T5's own rule
    assert first.held > 400 and first.reversioned <= 1
    assert _rows(db, sid) == n0 + first.reversioned and _holds(db, sid) == 1
    second = _night(db, sid, tk, vendor, NIGHTS[1])
    assert second.reversioned > 400  # the repair is undone
    assert _rows(db, sid) == n0 + first.reversioned + second.reversioned
    assert (
        second.reversioned >= appended - 40
    )  # (the vendor's window omits the stale tail it never serves)


def test_an_adjusted_pull_writes_nothing(db):
    tk = "BTOC"
    sid = _seed(db, tk)
    _repair(db, tk)
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (d) d, close FROM fact_price_eod WHERE security_id = %s "
            "ORDER BY d, recorded_at DESC",
            (sid,),
        )
        repaired = {r["d"]: float(r["close"]) for r in cur.fetchall()}
    raw = {r["d"]: float(r["close"]) for r in fx.rows(tk)}
    adjusted = []
    for r in fx.yahoo(tk):
        k = repaired[r["d"]] / raw[r["d"]] if r["d"] in raw else 1.0
        adjusted.append(
            {
                **r,
                **{f: r[f] * k for f in ("open", "high", "low", "close") if r[f] is not None},
                "volume": None if r["volume"] is None else r["volume"] / k,
            }
        )
    n0 = _rows(db, sid)
    res = _night(db, sid, tk, _Vendor(adjusted), NIGHTS[0])
    assert (res.reversioned, res.held, res.normalized) == (0, 0, 0)
    assert _rows(db, sid) == n0


def test_a_genuine_one_bar_correction_lands_on_the_adjusted_scale(db):
    tk = "OPTT"
    sid = _seed(db, tk)
    _repair(db, tk)
    target = date(2026, 3, 2)
    edited = [
        {**r, "close": r["close"] * 1.02} if r["d"] == target else dict(r) for r in fx.yahoo(tk)
    ]
    n0 = _rows(db, sid)
    res = _night(db, sid, tk, _Vendor(edited), NIGHTS[0])
    assert (res.reversioned, res.held) == (1, 0)
    assert _rows(db, sid) == n0 + 1
    with db.cursor() as cur:
        cur.execute(
            "SELECT close FROM fact_price_eod WHERE security_id = %s AND d = %s "
            "ORDER BY recorded_at DESC LIMIT 2",
            (sid, target),
        )
        new, old = (float(r["close"]) for r in cur.fetchall())
    assert new == pytest.approx(old * 1.02, rel=1e-6)  # x30 scale kept, the 2% landed


def test_before_the_repair_deploying_the_loader_writes_nothing(db):
    """The order the operator will run things in: the code ships first, the repair later — meanwhile the
    broken tape (stored exactly as the vendor serves it) is left exactly as it is."""
    tk = "BTOC"
    sid = _seed(db, tk)
    n0 = _rows(db, sid)
    for now in NIGHTS[:2]:
        res = _night(db, sid, tk, _Vendor(fx.yahoo(tk)), now)
        assert (res.reversioned, res.held, res.normalized) == (0, 0, 0)
    assert _rows(db, sid) == n0


def test_a_split_on_the_list_but_not_in_force_is_never_applied(db):
    """MI's 1-for-80 still needs review on the list: the loader leaves its tape exactly as served."""
    tk = "MI"
    sid = _seed(db, tk)
    plans = tool.plan_all(db, tickers=[tk], today=fx.TODAY)
    assert [p.status for p in plans] == [tool.WAITING]
    stored = fx.rows(tk)
    vendor = [
        {k: (float(v) if k != "d" and v is not None else v) for k, v in r.items()}
        for r in stored
        if r["d"] >= fx.TODAY - timedelta(days=730)
    ]
    n0 = _rows(db, sid)
    res = _night(db, sid, tk, _Vendor(vendor), NIGHTS[0])
    assert (res.reversioned, res.normalized) == (0, 0)
    assert _rows(db, sid) == n0


def _real_series(tk: str) -> list[dict]:
    return [
        {k: (float(v) if k != "d" and v is not None else v) for k, v in r.items()}
        for r in fx.rows(tk)
        if r["d"] >= fx.TODAY - timedelta(days=730)
    ]


def test_the_vendor_re_basing_a_raw_store_lands_on_the_first_night_and_is_counted(db):
    """B1 (the invariant review), DB-backed, COUNT THE TABLE. CONTRACT, one stated edit: KITT's real tape with
    its run before the 2026-09-25 1-for-6 put back raw (the store a misread first night leaves); the vendor
    serves the real re-based tape. The old rule divided the re-base back every night (MEASURED: 516 bars read
    "vendor_doubled", 0 written). Now every pre-split bar re-versions on night one, nothing is divided back, and
    night two writes nothing."""
    tk, eff, k = "KITT", date(2026, 9, 25), 6
    sid = fx.seed_security(db, tk)
    fx.seed_splits(db, sid, tk)
    rows = []
    for r in fx.rows(tk):
        if r["d"] < eff:
            r = {
                **r,
                **{f: r[f] / k for f in ("open", "high", "low", "close") if r[f] is not None},
                "volume": None if r["volume"] is None else r["volume"] * k,
            }
        rows.append(r)
    ingest_prices(db, sid, rows, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    vendor = _Vendor(_real_series(tk))
    pre = sum(1 for r in vendor.bars if r["d"] < eff)
    n0 = _rows(db, sid)
    first = _night(db, sid, tk, vendor, NIGHTS[0])
    assert "vendor_doubled" not in first.split_normalized
    assert "mis_factored" not in first.split_normalized
    assert (first.reversioned, first.held) == (pre, 0)
    assert _rows(db, sid) == n0 + pre
    second = _night(db, sid, tk, vendor, NIGHTS[1])
    assert (second.reversioned, second.held, second.normalized) == (0, 0, 0)
    assert _rows(db, sid) == n0 + pre


def test_a_divide_back_is_counted_per_reason_and_the_price_leg_logs_nothing(db, caplog):
    """The visibility half of B1: PBM's repaired tape (raw prints) against the vendor's real double-adjusted
    pull — every pre-01-20 bar is divided back. Nothing is written and the count rides ``BarsResult`` per
    reason (into the run artifact and the pass summary). The price leg itself logs NOTHING: a divide-back that
    stands every night is not news (the re-review's N2) — the pass summary decides what to announce.
    """
    import logging

    tk = "PBM"
    sid = _seed(db, tk)
    plans = tool.plan_all(db, tickers=[tk], today=fx.TODAY, spots=tool.load_spots(fx.EVIDENCE))
    assert [p.status for p in plans] == [tool.REPAIR], plans[0].refusals
    tool.apply_plans(db, plans)
    n0 = _rows(db, sid)
    with caplog.at_level(logging.WARNING, logger="ingest.prices.ingest_security"):
        res = _night(db, sid, tk, _Vendor(fx.yahoo(tk)), NIGHTS[0])
    assert (res.reversioned, res.held) == (0, 0) and _rows(db, sid) == n0
    assert set(res.split_normalized) == {"vendor_doubled"}
    assert res.split_normalized["vendor_doubled"] > 300
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _names():
    import uuid

    from pipeline.ingest_thesis import NameResult

    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [
        NameResult("BTOC", a, 0, 0, price_bars_split_normalized={"vendor_raw": 496}),
        NameResult("BTOC", a, 0, 0, price_bars_split_normalized={"vendor_raw": 496}),  # 2nd thesis
        NameResult("PBM", b, 0, 0, price_bars_split_normalized={"vendor_doubled": 311}),
        NameResult("OPTT", uuid.uuid4(), 0, 0),
    ]
    return a, b, rows


def test_the_pass_summary_announces_a_divide_back_once_then_only_when_it_changes(capsys, caplog):
    """``print_split_lines`` (the re-review's N2, #7 honest loudness): ONE quiet totals line every pass, deduped
    by security across theses. A divide-back is named (and logged at WARNING) the first time — no baseline —
    and then only when it CHANGES: a standing one, drifting by a bar or two as the vendor's window slides, is
    not re-announced; a jump, a new reason, or the divide-back stopping is. vendor_raw stays in the totals.
    """
    import logging

    from pipeline.ingest_thesis import NameResult, print_split_lines

    a, b, rows = _names()

    def run(previous):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pipeline.ingest_thesis"):
            print_split_lines(rows, previous=previous)
        warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        return capsys.readouterr().out.splitlines(), warned

    out, warned = run(None)  # the first pass: no baseline — everything is news, once
    assert out[0].startswith("split list: 807 price bar(s) of 2 name(s)")
    assert "vendor doubled 311" in out[0] and "vendor raw 496" in out[0]
    assert "PBM: split list divided the vendor's bars back — 311 vendor doubled" in out
    assert not any(line.startswith("BTOC:") for line in out)
    assert len(warned) == 1 and "PBM" in warned[0]

    for prev in (311, 310, 307):  # standing (the window drift)
        out, warned = run({str(b): {"vendor_doubled": prev}, str(a): {"vendor_raw": 497}})
        assert len(out) == 1 and out[0].startswith("split list:") and warned == []

    out, warned = run({str(b): {"vendor_doubled": 150}})  # a jump: news
    assert (
        "PBM: split list divided the vendor's bars back — 311 vendor doubled (last pass: 150 vendor doubled)"
        in out
    )
    assert len(warned) == 1
    out, warned = run({str(b): {"mis_factored": 311}})  # a different reason: news
    assert any(line.startswith("PBM: split list divided") for line in out) and len(warned) == 1

    rows[2] = NameResult("PBM", b, 0, 0, price_bars_split_normalized={"vendor_raw": 311})
    out, warned = run({str(b): {"vendor_doubled": 311}})  # stopped dividing back: a quiet line
    assert "PBM: split list no longer divides the vendor's bars back" in out and warned == []

    print_split_lines([NameResult("OPTT", rows[3].security_id, 0, 0)], previous=None)
    assert capsys.readouterr().out == ""


def test_a_first_night_and_holes_left_as_served_are_always_reported(capsys, caplog):
    import logging
    import uuid

    from pipeline.ingest_thesis import NameResult, print_split_lines

    sid = uuid.uuid4()
    rows = [
        NameResult(
            "YYAI",
            sid,
            0,
            0,
            price_bars_split_normalized={"first_night": 399, "hole_as_served": 1},
            price_split_first_night=("1-for-20 reverse split 2026-08-17",),
        )
    ]
    with caplog.at_level(logging.WARNING, logger="pipeline.ingest_thesis"):
        print_split_lines(rows, previous={str(sid): {"first_night": 399}})
    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        "split list: 399 price bar(s) of 1 name(s) read onto the list's scale (first night 399); "
        "1 hole(s) left as served beside a rescaled neighbor"
    )
    assert out[1].startswith(
        "YYAI: split applied to the stored history for the first time — 1-for-20"
    )
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_the_split_reads_baseline_is_the_newest_live_finished_pass_that_recorded_them(tmp_path):
    """``cron_run_log.previous_split_reads``: the artifact carries ``split_reads`` per name; a ``--no-live``
    pass, a killed pass's start artifact and an artifact written before the key existed are skipped; no
    qualifying artifact = ``None`` (everything is news once)."""
    import json
    import uuid

    from pipeline import cron_run_log, daily
    from pipeline.ingest_thesis import NameResult

    t0 = datetime(2026, 10, 21, 2, 30, tzinfo=timezone.utc)
    sid = uuid.uuid4()

    def write(i, n, *, allow_live=True):
        res = daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T")
        res.ingested = [
            NameResult("PBM", sid, 0, 0, price_bars_split_normalized={"vendor_doubled": n})
        ]
        at = t0 + timedelta(hours=i)
        return cron_run_log.write_cron_run_log(
            [res],
            asof=date(2026, 10, 20),
            allow_live=allow_live,
            started_at=at,
            finished_at=at,
            base_dir=tmp_path,
        )

    assert cron_run_log.previous_split_reads(base_dir=tmp_path) is None
    first = write(0, 311)
    doc = json.loads(first.read_text(encoding="utf-8"))
    assert doc["split_reads"] == {
        str(sid): {"ticker": "PBM", "reasons": {"vendor_doubled": 311}, "first_night": []}
    }
    write(1, 999, allow_live=False)  # --no-live: never a baseline
    old = write(2, 555)  # strip the key: an artifact from before it existed
    blob = json.loads(old.read_text(encoding="utf-8"))
    blob.pop("split_reads")
    old.write_text(json.dumps(blob), encoding="utf-8")
    assert cron_run_log.write_run_start(
        asof=date(2026, 10, 20),
        allow_live=True,
        started_at=t0 + timedelta(hours=3),
        base_dir=tmp_path,
    )  # a pass that never finished
    assert cron_run_log.previous_split_reads(base_dir=tmp_path) == {
        str(sid): {"vendor_doubled": 311}
    }
