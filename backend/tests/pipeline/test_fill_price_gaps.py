"""``pipeline.fill_price_gaps`` (W6-3b) — the price gap fill by hand, DB-backed. COUNT THE TABLES.

The probe's REAL series (``tests/fixtures/massive/gap_fill``): SNDK (the store ends 11-19, Yahoo nulls every
post-split session, Massive.com traded each) and GNS (Yahoo's null 11-19 sits before the split's first session).
No network: the vendor and Massive.com are fakes over those fixtures; the clock is pinned to the night of 11-25.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from ingest.prices.eod_loader import ingest_prices
from pipeline import fill_price_gaps as fpg
from tests.ingest.test_gap_fill import (
    GNS_1_FOR_10,
    NOV19,
    NOV25,
    SNDK_8_FOR_1,
    _night,
    _range,
    _seed,
    _yahoo,
)


class _Vendor:
    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return [dict(b) for b in _yahoo(ticker)]


class _Massive:
    def __init__(self):
        self.asked: list[tuple[str, date, date]] = []

    def bars(self, symbol, start, end, *, allow_live):
        self.asked.append((symbol, start, end))
        return {d: b for d, b in _range(symbol).items() if start <= d <= end}


@pytest.fixture
def names(db, monkeypatch):
    sndk = _seed(db, "SNDK", SNDK_8_FOR_1)
    gns = _seed(db, "GNS", GNS_1_FOR_10)
    ingest_prices(
        db, sndk, [b for b in _yahoo("SNDK") if b["d"] <= NOV19], tenant_id=DEFAULT_TENANT_ID
    )
    ingest_prices(
        db, gns, [b for b in _yahoo("GNS") if b["d"] < NOV25], tenant_id=DEFAULT_TENANT_ID
    )
    db.commit()
    massive = _Massive()
    monkeypatch.setattr(fpg, "default_bar_source", lambda: massive)
    monkeypatch.setattr(fpg, "YahooPriceSource", _Vendor)
    return sndk, gns, massive


def _counts(db) -> tuple[int, int]:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_price_eod")
        bars = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM price_bar_fill")
        fills = cur.fetchone()["n"]
    db.rollback()
    return bars, fills


def test_a_dry_run_lists_the_fills_and_the_refusals_and_writes_nothing(db, names, capsys):
    _, _, massive = names
    before = _counts(db)
    fpg.main(["--ticker", "SNDK", "--ticker", "GNS"], now=_night(NOV25))
    assert _counts(db) == before  # COUNT THE TABLES: a dry run writes nothing
    out = capsys.readouterr().out
    assert "DRY-RUN (read-only connection; nothing written)" in out
    for d, close in (
        ("2026-11-20", "79.24"),
        ("2026-11-23", "87.71"),
        ("2026-11-24", "77.95"),
        ("2026-11-25", "73.61"),
    ):
        assert f"FILL {d}  close {close}" in out
    assert "  SNDK — 8-for-1 forward split of 2026-11-20" in out
    assert "  GNS — 1-for-10 reverse split of 2026-11-20" in out
    assert "not fillable by rule: 2026-11-19 (before split)" in out
    assert "FILL 2026-11-25  close 0.435" in out  # GNS tonight: Yahoo's close not served yet
    assert "PLAN: 2 name(s) · 5 planned · 1 not fillable by rule · 0 deferred · 0 error(s)" in out
    assert sorted(s for s, *_ in massive.asked) == ["GNS", "SNDK"]  # one range read per name


def test_apply_writes_the_fills_with_their_provenance_and_a_rerun_plans_nothing(db, names, capsys):
    sndk, _, _ = names
    b0, f0 = _counts(db)
    fpg.main(["--ticker", "SNDK", "--apply"], now=_night(NOV25))
    assert _counts(db) == (b0 + 4, f0 + 4)
    with db.cursor() as cur:
        cur.execute(
            "SELECT d, source, vendor_symbol, raw_close, factor, reason FROM price_bar_fill "
            "WHERE security_id = %s ORDER BY d",
            (sndk,),
        )
        rows = cur.fetchall()
    db.rollback()
    assert [(r["d"].isoformat(), str(r["raw_close"])) for r in rows] == [
        ("2026-11-20", "79.24"),
        ("2026-11-23", "87.71"),
        ("2026-11-24", "77.95"),
        ("2026-11-25", "73.61"),
    ]
    assert {(r["source"], r["vendor_symbol"], r["factor"], r["reason"]) for r in rows} == {
        ("massive", "SNDK", 1, "split_window")
    }
    capsys.readouterr()
    fpg.main(["--ticker", "SNDK"], now=_night(NOV25))
    assert "PLAN: 1 name(s) · 0 planned" in capsys.readouterr().out


def test_apply_is_refused_inside_the_nightly_window_and_writes_nothing(db, names, monkeypatch):
    before = _counts(db)
    monkeypatch.setattr(
        fpg, "_inside_the_window", lambda: "inside the nightly window (21:30-23:30)"
    )
    with pytest.raises(SystemExit) as exit_:
        fpg.main(["--ticker", "SNDK", "--apply"], now=_night(NOV25))
    assert exit_.value.code == fpg.EXIT_BUSY
    assert _counts(db) == before


def test_no_key_and_scope_off_read_nothing(db, names, monkeypatch, capsys):
    _, _, massive = names
    fpg.main(["--ticker", "SNDK", "--scope", "off"], now=_night(NOV25))
    monkeypatch.setattr(fpg, "default_bar_source", lambda: None)
    fpg.main(["--ticker", "SNDK"], now=_night(NOV25))
    out = capsys.readouterr().out
    assert "the fill is off" in out and "no MASSIVE_API_KEY" in out and massive.asked == []


def test_an_unknown_ticker_is_an_empty_plan_never_an_error(db, names, capsys):
    fpg.main(["--ticker", str(uuid.uuid4())[:8].upper()], now=_night(NOV25))
    assert "PLAN: 0 name(s)" in capsys.readouterr().out
