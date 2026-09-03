"""``as_of_many`` + the point-in-time memo / prefetch / bounds — ACCESSOR-level equality against the plain
per-security ``as_of`` (Board/Cockpit perf PR-1b, proof obligation 1).

The batch read and the bounded, memoized view change WHERE rows are fetched from, never WHICH rows a
reader sees. Every case runs at ``known_at = PIN`` (sees every version) AND at ``MID`` — a transaction
time pinned BETWEEN two recorded versions of one price bar and one Form 4 (``tests/pit_fixtures.py``) —
because the latest-version pick under a past ``known_at`` is exactly where a batched ``DISTINCT ON``
could diverge from the scoped read. DB-backed (skips without Postgres).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

import signals.base as sb
from db.bitemporal import _FACT_IDENTITY, as_of, as_of_many
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from pipeline.seed import HIMS_SECURITY_ID, NNE_ID
from signals.base import PointInTimeData, window_prices
from signals.horizons import call_bounds, display_bounds
from tests.pit_fixtures import (
    ACCEPTED_V1,
    ACCEPTED_V2,
    MID,
    PIN,
    SECS,
    add_mid_versions,
    seed_all,
    seed_benchmark_bars,
)

SECURITY_TABLES = [t for t in _FACT_IDENTITY if t != "fact_theme_conviction"]
# every security-scoped PIT accessor -> the table it reads
ACCESSORS = {
    "insider_txns": "fact_insider_txn",
    "price_history": "fact_price_eod",
    "dilution_facts": "fact_dilution",
    "catalyst_facts": "fact_catalyst",
    "fundamentals_facts": "fact_fundamentals",
    "corporate_event_facts": "fact_corporate_event",
    "activist_stake_facts": "fact_activist_stake",
    "revenue_mix_facts": "fact_revenue_mix",
    "shares_outstanding_facts": "fact_shares_outstanding",
    "cash_burn_facts": "fact_cash_burn",
    "fund_shares": "fact_fund_shares",
}
ASOFS = (date(2025, 8, 15), date(2026, 6, 1))
_KNOWN_ATS = pytest.mark.parametrize("known_at", [PIN, MID], ids=["pin-sees-all", "mid-version"])


def _plain(db, table, sid, asof, known_at, lower_days=None):
    """The reference: the per-security ``as_of`` (unchanged SQL), trimmed in Python to a floor."""
    rows = as_of(
        db, table, security_id=sid, asof=asof, known_at=known_at, tenant_id=DEFAULT_TENANT_ID
    )
    if lower_days is not None:
        floor = asof - timedelta(days=lower_days)
        rows = [r for r in rows if r["valid_from"] >= floor]
    return rows


def test_the_mid_version_fixture_sits_between_two_recorded_versions(db):
    """The fixture is what it claims: at MID the T1 version of the bar / the Form 4 wins, at PIN the
    T2 version — so the equality tests below genuinely exercise the version pick under a past known_at.
    """
    seed_all(db)
    bar_d, close_t1, close_t2 = add_mid_versions(db)
    asof = date(2026, 6, 1)
    at_mid = {r["d"]: r for r in _plain(db, "fact_price_eod", HIMS_SECURITY_ID, asof, MID)}
    at_pin = {r["d"]: r for r in _plain(db, "fact_price_eod", HIMS_SECURITY_ID, asof, PIN)}
    assert at_mid[bar_d]["close"] == close_t1 and at_pin[bar_d]["close"] == close_t2
    assert close_t1 != close_t2
    ins_mid = _plain(db, "fact_insider_txn", HIMS_SECURITY_ID, asof, MID)
    ins_pin = _plain(db, "fact_insider_txn", HIMS_SECURITY_ID, asof, PIN)
    assert ins_mid and {r["accepted"] for r in ins_mid} == {ACCEPTED_V1}
    assert ins_pin and {r["accepted"] for r in ins_pin} == {ACCEPTED_V2}


@_KNOWN_ATS
def test_as_of_many_equals_as_of_per_security_on_every_table(db, known_at):
    """The batch read is row-for-row (and ORDER-for-order) the per-security read on every
    security-scoped fact table, with an entry for EVERY requested id (``[]`` when it has no rows), and
    an event-time floor drops whole facts only — never changes which version wins."""
    seed_all(db)
    add_mid_versions(db)
    for asof in ASOFS:
        for table in SECURITY_TABLES:
            batch = as_of_many(
                db,
                table,
                security_ids=SECS,
                asof=asof,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
            )
            assert set(batch) == set(SECS)
            for sid in SECS:
                assert batch[sid] == _plain(db, table, sid, asof, known_at), (table, sid, asof)
            floored = as_of_many(
                db,
                table,
                security_ids=SECS,
                asof=asof,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
                valid_from_lower=asof - timedelta(days=200),
            )
            for sid in SECS:
                assert floored[sid] == _plain(db, table, sid, asof, known_at, 200), (table, sid)
    assert (
        as_of_many(
            db,
            "fact_price_eod",
            security_ids=[],
            asof=ASOFS[0],
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
        )
        == {}
    )
    # the price floor is exercised for real: the seed holds > 200 d of HIMS bars
    full = _plain(db, "fact_price_eod", HIMS_SECURITY_ID, date(2026, 6, 1), known_at)
    assert min(r["d"] for r in full) < date(2026, 6, 1) - timedelta(days=200)


@_KNOWN_ATS
def test_pit_accessors_with_basket_and_bounds_equal_the_plain_read(db, known_at):
    """Every accessor of a PIT built with the basket + the derived bounds (the call's, the display's, and
    none) returns EXACTLY the plain per-security rows trimmed to that bound, in the same order — and a
    caller's own ``lookback_days`` trim is unchanged by the bound (the memo rule)."""
    seed_all(db)
    add_mid_versions(db)
    for asof in ASOFS:
        for bounds in (call_bounds(DEFAULT_CONFIG), display_bounds(), {}):
            pit = PointInTimeData(db, asof=asof, known_at=known_at, basket=SECS, bounds=bounds)
            for sid in SECS:
                for accessor, table in ACCESSORS.items():
                    expect = _plain(db, table, sid, asof, known_at, bounds.get(table))
                    if accessor == "price_history":
                        expect = window_prices(expect, asof, None)
                    got = getattr(pit, accessor)(sid)
                    assert got == expect, (accessor, sid, asof, known_at, bounds)
                unbounded = _plain(db, "fact_price_eod", sid, asof, known_at)
                assert pit.price_history(sid, lookback_days=120) == window_prices(
                    unbounded, asof, 120
                )


def test_a_bounded_read_trims_exactly_to_the_floor_and_the_floor_is_real(db):
    """A bound below the seed's tape depth actually removes rows (the test is not vacuous), and what
    remains is exactly the plain read's rows on/after the floor."""
    seed_all(db)
    asof = date(2026, 6, 1)
    pit = PointInTimeData(db, asof=asof, known_at=PIN, basket=SECS, bounds={"fact_price_eod": 100})
    rows = pit.price_history(HIMS_SECURITY_ID)
    full = window_prices(_plain(db, "fact_price_eod", HIMS_SECURITY_ID, asof, PIN), asof, None)
    assert rows and len(full) > len(rows)
    assert rows == [r for r in full if r["d"] >= asof - timedelta(days=100)]
    # ...and the same floor on the per-security fallback path (no basket): one memo rule, no exceptions
    solo = PointInTimeData(db, asof=asof, known_at=PIN, bounds={"fact_price_eod": 100})
    assert solo.price_history(HIMS_SECURITY_ID) == rows


def test_prefetch_is_one_query_per_table_and_memoizes_the_empty_case(db, monkeypatch):
    """With a basket: the FIRST read of a table is ONE ``as_of_many`` for the whole basket, every later
    read of that table (any member, any lookback) is a memo hit — including a member with NO rows,
    which is never re-queried. A benchmark OUTSIDE the basket falls back to the per-security read
    exactly once, trimmed to the same bound. No basket: per-security, memoized."""
    seed_all(db)
    asof = date(2026, 6, 1)
    spy = seed_benchmark_bars(db, asof)
    calls = {"many": 0, "one": 0}
    real_many, real_one = sb.as_of_many, sb.as_of

    def many(*a, **k):
        calls["many"] += 1
        return real_many(*a, **k)

    def one(*a, **k):
        calls["one"] += 1
        return real_one(*a, **k)

    monkeypatch.setattr(sb, "as_of_many", many)
    monkeypatch.setattr(sb, "as_of", one)
    bounds = call_bounds(DEFAULT_CONFIG)
    pit = PointInTimeData(db, asof=asof, known_at=PIN, basket=SECS, bounds=bounds)
    for sid in SECS:
        pit.price_history(sid)
        pit.insider_txns(sid)
        pit.catalyst_facts(sid)
    for sid in SECS:  # a second pass, with a caller trim: all memo hits
        pit.price_history(sid, lookback_days=120)
        pit.insider_txns(sid)
    assert calls == {"many": 3, "one": 0}
    assert pit.insider_txns(NNE_ID) == [] and calls == {"many": 3, "one": 0}  # empty, memoized
    b1 = pit.benchmark_prices("SPY")
    b2 = pit.benchmark_prices("SPY", lookback_days=95)
    assert calls == {"many": 3, "one": 1} and b1 and b2 == window_prices(b1, asof, 95)
    assert b1 == window_prices(
        _plain(db, "fact_price_eod", spy, asof, PIN, bounds["fact_price_eod"]), asof, None
    )
    calls.update(many=0, one=0)
    solo = PointInTimeData(db, asof=asof, known_at=PIN)
    solo.price_history(HIMS_SECURITY_ID)
    solo.price_history(HIMS_SECURITY_ID, lookback_days=10)
    assert calls == {"many": 0, "one": 1}


def test_accessors_hand_back_fresh_lists(db):
    """A reader that appends to / reorders the list it was handed cannot poison the next reader."""
    seed_all(db)
    asof = date(2026, 6, 1)
    pit = PointInTimeData(
        db, asof=asof, known_at=PIN, basket=SECS, bounds=call_bounds(DEFAULT_CONFIG)
    )
    for accessor in ("insider_txns", "price_history", "catalyst_facts"):
        first = getattr(pit, accessor)(HIMS_SECURITY_ID)
        snapshot = list(first)
        first.append({"poison": True})
        first.reverse()
        assert getattr(pit, accessor)(HIMS_SECURITY_ID) == snapshot


def test_identity_reads_are_batched_and_memoized(db):
    """``security_name`` / ``security_cik`` for the basket come from ONE tenant-filtered master query,
    then memo hits; an id outside the basket is a single per-id read, memoized; unknown -> (None, None).
    """
    seed_all(db)

    class _CountingConn:
        def __init__(self, conn):
            self._conn, self.cursors = conn, 0

        def cursor(self):
            self.cursors += 1
            return self._conn.cursor()

    proxy = _CountingConn(db)
    pit = PointInTimeData(proxy, asof=date(2026, 6, 1), known_at=PIN, basket=SECS)
    names = {sid: pit.security_name(sid) for sid in SECS}
    ciks = {sid: pit.security_cik(sid) for sid in SECS}
    assert proxy.cursors == 1
    assert all(names.values()) and ciks[HIMS_SECURITY_ID] == "0001773751"
    stranger = uuid.uuid4()
    assert pit.security_name(stranger) is None and pit.security_cik(stranger) is None
    assert proxy.cursors == 2
    assert pit.security_name(stranger) is None and proxy.cursors == 2
    assert pit._benchmark_id("SPY") == pit._benchmark_id("spy") and proxy.cursors == 3
