from __future__ import annotations

import uuid
from datetime import date

from app.deps import get_current_tenant
from app.main import app
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from scoreboard.prices import PgRealizedPrices
from tests.scoreboard.helpers import bar, persist_thesis


def ohlcv_bar(db, security_id, d, o, h, low, c, v):
    """Seed a FULL OHLCV bar (the shared ``bar`` helper writes close-only)."""
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "d": d,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": v,
            "valid_from": d,
        },
    )
    db.commit()


# GET /scoreboard/price-window — the drawer sparkline's on-demand read (Slice 3). The load-bearing
# test is the no-lookahead point-in-time one: the series is capped at asof SERVER-SIDE (cap=asof on
# the valid axis), so a future `end` can never widen it past the as-of. Plus tenant isolation → 404,
# the single-arm-bar 1-point series, and the null-close skip.

_START = "2026-06-01"
_END = "2026-09-01"  # a FUTURE exit_by relative to every asof below — the cap must still bite


def _get(client, thesis_id, security_id, asof, *, start=_START, end=_END):
    return client.get(
        "/scoreboard/price-window",
        params={
            "thesis_id": str(thesis_id),
            "security_id": str(security_id),
            "start": start,
            "end": end,
            "asof": asof,
        },
    )


def test_price_window_no_lookahead_caps_at_asof(client, db, security_id):
    """Invariant #1, the required point-in-time test: seed a window whose exit_by is in the future;
    an early asof returns FEWER bars, NO bar with d > asof ever appears, and the series is exactly
    what the scorer's asof-capped reader returns."""
    thesis = persist_thesis(db, security_id)
    for d, c in [
        (date(2026, 6, 1), 100.0),
        (date(2026, 6, 8), 104.0),
        (date(2026, 6, 15), 102.0),
        (date(2026, 6, 22), 108.0),
        (date(2026, 7, 1), 111.0),
        (date(2026, 7, 15), 115.0),
        (date(2026, 8, 1), 120.0),  # beyond BOTH asofs — must never surface at an earlier asof
    ]:
        bar(db, security_id, d, c)

    early = _get(client, thesis.id, security_id, "2026-06-15")
    late = _get(client, thesis.id, security_id, "2026-07-15")
    assert early.status_code == 200 and late.status_code == 200
    eb, lb = early.json()["bars"], late.json()["bars"]

    # (a) the earlier asof returns strictly FEWER bars — the valid-time cap bites
    assert len(eb) == 3 and len(lb) == 6
    assert len(eb) < len(lb)
    # (b) NO bar past the as-of, whatever `end` the client passed (end is 2026-09-01, far future).
    #     ISO date strings compare chronologically.
    assert all(b["d"] <= "2026-06-15" for b in eb)
    assert all(b["d"] <= "2026-07-15" for b in lb)
    assert eb[-1]["d"] == "2026-06-15" and lb[-1]["d"] == "2026-07-15"
    assert "2026-08-01" not in [b["d"] for b in lb]  # the beyond-cap bar never leaks
    # (c) the series MATCHES the closes the scorer would use — a reader built exactly as the endpoint
    #     builds it (cap=asof, the thesis's tenant, known_at defaulted to now — the scorer's config).
    reader = PgRealizedPrices(db, tenant_id=thesis.tenant_id, cap=date(2026, 7, 15))
    expected = [
        (d.isoformat(), c)
        for d, c in reader.closes_between(security_id, date(2026, 6, 1), date(2026, 9, 1))
    ]
    assert [(b["d"], b["close"]) for b in lb] == expected

    # the echoed contract (provenance + the request shape)
    body = late.json()
    assert body["thesis_id"] == str(thesis.id)
    assert body["security_id"] == str(security_id)
    assert body["start"] == _START and body["end"] == _END and body["asof"] == "2026-07-15"
    assert body["source"] == "fact_price_eod"


def test_price_window_other_tenant_thesis_404(client, db, security_id):
    """Tenant isolation: a thesis the deployment tenant can't see is a 404 (get_thesis_or_404 loads by
    id only — the current-tenant guard is what enforces isolation on this direct-fetch endpoint)."""
    thesis = persist_thesis(db, security_id)  # tenant = the default
    bar(db, security_id, date(2026, 6, 1), 100.0)
    # flip the deployment's current tenant to a DIFFERENT one → the thesis is not visible
    app.dependency_overrides[get_current_tenant] = lambda: uuid.uuid4()
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 404


def test_price_window_unknown_thesis_404(client, db, security_id):
    """An unknown thesis id → 404 (mirrors get_thesis_or_404)."""
    r = _get(client, uuid.uuid4(), security_id, "2026-07-15")
    assert r.status_code == 404


def test_price_window_single_arm_bar_one_point(client, db, security_id):
    """Only the arm-day bar has landed → a 1-bar series (the honest thin-data case the FE draws
    as 'no price path yet', never a fake line). A close-only bar surfaces null OHL/volume, never
    invented values."""
    thesis = persist_thesis(db, security_id)
    bar(db, security_id, date(2026, 6, 1), 100.0)  # the arm-day bar only (close-only)
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 200
    assert r.json()["bars"] == [
        {"d": "2026-06-01", "open": None, "high": None, "low": None, "close": 100.0, "volume": None}
    ]


def test_price_window_full_ohlcv_bar_rides_the_wire(client, db, security_id):
    """A full OHLCV bar carries every field straight from fact_price_eod (the line still draws close;
    open/high/low/volume ride the wire for a later candlestick — no second contract change)."""
    thesis = persist_thesis(db, security_id)
    ohlcv_bar(db, security_id, date(2026, 6, 1), 99.5, 103.0, 98.0, 101.25, 1_250_000.0)
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 200
    assert r.json()["bars"] == [
        {
            "d": "2026-06-01",
            "open": 99.5,
            "high": 103.0,
            "low": 98.0,
            "close": 101.25,
            "volume": 1250000.0,
        }
    ]


def test_price_window_null_close_skipped(client, db, security_id):
    """A day with no close is skipped — never a null-close bar in the series (parity with the scorer)."""
    thesis = persist_thesis(db, security_id)
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 2), None)  # no close — dropped
    bar(db, security_id, date(2026, 6, 3), 103.0)
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 200
    bars = r.json()["bars"]
    assert [b["d"] for b in bars] == ["2026-06-01", "2026-06-03"]
    assert all(b["close"] is not None for b in bars)
