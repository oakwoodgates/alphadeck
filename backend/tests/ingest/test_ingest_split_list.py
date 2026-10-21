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


def test_a_divide_back_is_counted_per_reason_and_logged_at_warning(db, caplog):
    """The visibility half of B1: PBM's repaired tape (raw prints) against the vendor's real double-adjusted
    pull — every pre-01-20 bar is divided back. Nothing is written, the count rides ``BarsResult`` per reason
    (into the run artifact and the pass summary), and the price leg logs it at WARNING — the level that reaches
    stderr with no logging config."""
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
    assert (
        set(res.split_normalized) == {"vendor_doubled"}
        and res.split_normalized["vendor_doubled"] > 300
    )
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "PBM" in r.getMessage()]
    assert warned and "vendor doubled" in warned[0].getMessage()


def test_the_pass_summary_prints_the_split_reads(capsys):
    """``print_split_lines``: one quiet totals line, deduped by security across theses; a divide-back and a
    first night each get their own line; vendor_raw alone stays in the totals."""
    import uuid

    from pipeline.ingest_thesis import NameResult, print_split_lines

    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [
        NameResult("BTOC", a, 0, 0, price_bars_split_normalized={"vendor_raw": 496}),
        NameResult("BTOC", a, 0, 0, price_bars_split_normalized={"vendor_raw": 496}),  # 2nd thesis
        NameResult(
            "PBM",
            b,
            0,
            0,
            price_bars_split_normalized={"vendor_doubled": 311},
            price_split_first_night=("1-for-10 reverse split 2026-10-26",),
        ),
        NameResult("OPTT", uuid.uuid4(), 0, 0),
    ]
    print_split_lines(rows)
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("split list: 807 price bar(s) of 2 name(s)")
    assert "vendor doubled 311" in out[0] and "vendor raw 496" in out[0]
    assert "PBM: split list divided the vendor's bars back — 311 vendor doubled" in out
    assert any(line.startswith("PBM: split applied to the stored history") for line in out)
    assert not any(line.startswith("BTOC:") for line in out)
    print_split_lines([NameResult("OPTT", uuid.uuid4(), 0, 0)])
    assert capsys.readouterr().out == ""
