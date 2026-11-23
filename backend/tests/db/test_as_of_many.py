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
from db.bitemporal import (
    _FACT_IDENTITY,
    append_fact,
    as_of,
    as_of_many,
    as_of_many_theses,
    as_of_thesis,
)
from db.public_clock import SCOPE_COL
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from pipeline.seed import (
    HIMS_SECURITY_ID,
    HIMS_THESIS_ID,
    NNE_ID,
    NUCLEAR_THESIS_ID,
    UNH_THESIS_ID,
)
from signals.base import PointInTimeData, window_prices
from signals.horizons import call_bounds, display_bounds
from tests.pit_fixtures import (
    ACCEPTED_V1,
    ACCEPTED_V2,
    MID,
    PIN,
    SECS,
    T1,
    T2,
    THESES,
    add_mid_versions,
    seed_all,
    seed_benchmark_bars,
)

# every security-scoped fact table (the thesis-scoped ones — the theme conviction, the term-mentions count —
# have no security_id to batch on)
SECURITY_TABLES = [t for t in _FACT_IDENTITY if SCOPE_COL[t] == "security_id"]
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
    "filing_index": "fact_filing_index",
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
            pit = PointInTimeData(
                db,
                asof=asof,
                known_at=known_at,
                basket=SECS,
                bounds=bounds,
                workspace=DEFAULT_TENANT_ID,
            )
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
    pit = PointInTimeData(
        db,
        asof=asof,
        known_at=PIN,
        basket=SECS,
        bounds={"fact_price_eod": 100},
        workspace=DEFAULT_TENANT_ID,
    )
    rows = pit.price_history(HIMS_SECURITY_ID)
    full = window_prices(_plain(db, "fact_price_eod", HIMS_SECURITY_ID, asof, PIN), asof, None)
    assert rows and len(full) > len(rows)
    assert rows == [r for r in full if r["d"] >= asof - timedelta(days=100)]
    # ...and the same floor on the per-security fallback path (no basket): one memo rule, no exceptions
    solo = PointInTimeData(
        db, asof=asof, known_at=PIN, bounds={"fact_price_eod": 100}, workspace=DEFAULT_TENANT_ID
    )
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
    pit = PointInTimeData(
        db, asof=asof, known_at=PIN, basket=SECS, bounds=bounds, workspace=DEFAULT_TENANT_ID
    )
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
    solo = PointInTimeData(db, asof=asof, known_at=PIN, workspace=DEFAULT_TENANT_ID)
    solo.price_history(HIMS_SECURITY_ID)
    solo.price_history(HIMS_SECURITY_ID, lookback_days=10)
    assert calls == {"many": 0, "one": 1}


def test_accessors_hand_back_fresh_lists(db):
    """A reader that appends to / reorders the list it was handed cannot poison the next reader."""
    seed_all(db)
    asof = date(2026, 6, 1)
    pit = PointInTimeData(
        db,
        asof=asof,
        known_at=PIN,
        basket=SECS,
        bounds=call_bounds(DEFAULT_CONFIG),
        workspace=DEFAULT_TENANT_ID,
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
    pit = PointInTimeData(
        proxy, asof=date(2026, 6, 1), known_at=PIN, basket=SECS, workspace=DEFAULT_TENANT_ID
    )
    names = {sid: pit.security_name(sid) for sid in SECS}
    ciks = {sid: pit.security_cik(sid) for sid in SECS}
    assert proxy.cursors == 1
    assert all(names.values()) and ciks[HIMS_SECURITY_ID] == "0001773751"
    stranger = uuid.uuid4()
    assert pit.security_name(stranger) is None and pit.security_cik(stranger) is None
    assert proxy.cursors == 2
    assert pit.security_name(stranger) is None and proxy.cursors == 2
    assert pit._benchmark_id("SPY") == pit._benchmark_id("spy") and proxy.cursors == 3


def _seed_thesis_facts(db) -> None:
    """Seed multi-thesis, multi-version facts on ``fact_term_mentions`` and ``fact_theme_conviction``,
    leaving ``UNH_THESIS_ID`` empty on both tables to exercise empty-list memoization."""

    def mention(tid: uuid.UUID, term: str, month: date, valid_from: date, docs: int, rec):
        return {
            "tenant_id": DEFAULT_TENANT_ID,
            "thesis_id": tid,
            "term": term,
            "month": month,
            "documents": docs,
            "reports": docs,
            "events": 0,
            "issuance": 0,
            "other": 0,
            "filers": 1,
            "filers_sampled": False,
            "filer_ciks": ["0000000001"],
            "new_filers": None,
            "source_ref": "https://efts.sec.gov/LATEST/search-index?fixture",
            "fetched_at": rec,
            "valid_from": valid_from,
            "recorded_at": rec,
        }

    # NUCLEAR_THESIS_ID: two months, with a re-version between T1 and T2 on 2026-04
    append_fact(
        db,
        "fact_term_mentions",
        mention(
            NUCLEAR_THESIS_ID, "small modular reactor", date(2025, 7, 1), date(2025, 7, 31), 8, T1
        ),
    )
    append_fact(
        db,
        "fact_term_mentions",
        mention(
            NUCLEAR_THESIS_ID, "small modular reactor", date(2026, 4, 1), date(2026, 4, 30), 12, T1
        ),
    )
    append_fact(
        db,
        "fact_term_mentions",
        mention(
            NUCLEAR_THESIS_ID, "small modular reactor", date(2026, 4, 1), date(2026, 4, 30), 15, T2
        ),
    )
    # HIMS_THESIS_ID: one month
    append_fact(
        db,
        "fact_term_mentions",
        mention(HIMS_THESIS_ID, "glp-1 compounding", date(2026, 3, 1), date(2026, 3, 31), 20, T1),
    )

    # fact_theme_conviction: two theses, with a re-version on NUCLEAR_THESIS_ID between T1 and T2
    base_conv = {
        "tenant_id": DEFAULT_TENANT_ID,
        "grade": "flip",
        "source": "ratified",
        "ratified_by": "operator",
    }
    first = append_fact(
        db,
        "fact_theme_conviction",
        {
            **base_conv,
            "thesis_id": NUCLEAR_THESIS_ID,
            "source_ref": "https://example.com/nuclear-policy",
            "label": "nuclear policy v1",
            "valid_from": date(2025, 8, 1),
            "horizon_end": date(2026, 8, 1),
            "recorded_at": T1,
        },
    )
    append_fact(
        db,
        "fact_theme_conviction",
        {
            **base_conv,
            "thesis_id": NUCLEAR_THESIS_ID,
            "source_ref": "https://example.com/nuclear-policy",
            "label": "nuclear policy v2",
            "valid_from": date(2025, 8, 1),
            "horizon_end": date(2027, 1, 15),
            "recorded_at": T2,
            "supersedes": first,
        },
    )
    append_fact(
        db,
        "fact_theme_conviction",
        {
            **base_conv,
            "thesis_id": HIMS_THESIS_ID,
            "source_ref": "https://example.com/telehealth",
            "label": "telehealth adoption",
            "valid_from": date(2026, 2, 1),
            "horizon_end": date(2027, 2, 1),
            "recorded_at": T1,
        },
    )
    db.commit()


@_KNOWN_ATS
@pytest.mark.parametrize("clock", ["record", "public"])
def test_as_of_many_theses_equals_as_of_thesis_on_both_clocks(db, known_at, clock):
    """``as_of_many_theses`` matches ``as_of_thesis`` row-for-row and order-for-order across both
    ``fact_term_mentions`` and ``fact_theme_conviction`` on both ``record`` and ``public`` clocks,
    including event-time floors, empty theses, empty inputs, and unknown-table validation."""
    seed_all(db)
    _seed_thesis_facts(db)
    for table in ("fact_term_mentions", "fact_theme_conviction"):
        for asof in ASOFS:
            batch = as_of_many_theses(
                db,
                table,
                thesis_ids=THESES,
                asof=asof,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
                clock=clock,
            )
            assert set(batch) == set(THESES)
            assert batch[UNH_THESIS_ID] == []
            for tid in THESES:
                plain = as_of_thesis(
                    db,
                    table,
                    thesis_id=tid,
                    asof=asof,
                    known_at=known_at,
                    tenant_id=DEFAULT_TENANT_ID,
                    clock=clock,
                )
                assert batch[tid] == plain, (table, tid, asof, known_at, clock)
            lower = asof - timedelta(days=120)
            floored = as_of_many_theses(
                db,
                table,
                thesis_ids=THESES,
                asof=asof,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
                valid_from_lower=lower,
                clock=clock,
            )
            for tid in THESES:
                plain_floored = [
                    r
                    for r in as_of_thesis(
                        db,
                        table,
                        thesis_id=tid,
                        asof=asof,
                        known_at=known_at,
                        tenant_id=DEFAULT_TENANT_ID,
                        clock=clock,
                    )
                    if r["valid_from"] >= lower
                ]
                assert floored[tid] == plain_floored, (table, tid, asof, clock)
    assert (
        as_of_many_theses(
            db,
            "fact_term_mentions",
            thesis_ids=[],
            asof=ASOFS[0],
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
            clock=clock,
        )
        == {}
    )
    with pytest.raises(ValueError, match="unknown thesis-scoped fact table"):
        as_of_many_theses(
            db,
            "fact_price_eod",
            thesis_ids=THESES,
            asof=ASOFS[0],
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
            clock=clock,
        )


def test_pit_thesis_basket_prefetches_term_mentions_in_one_query(db, monkeypatch):
    """``PointInTimeData(..., thesis_basket=...)`` prefetches ``fact_term_mentions`` for all theses
    in one ``as_of_many_theses`` call — including memoizing empty lists for theses with no rows —
    falls back to ``as_of_thesis`` once for an out-of-basket thesis, and returns fresh lists."""
    seed_all(db)
    _seed_thesis_facts(db)
    asof = date(2026, 6, 1)
    calls = {"many": 0, "one": 0}
    real_many, real_one = sb.as_of_many_theses, sb.as_of_thesis

    def many(*a, **k):
        calls["many"] += 1
        return real_many(*a, **k)

    def one(*a, **k):
        calls["one"] += 1
        return real_one(*a, **k)

    monkeypatch.setattr(sb, "as_of_many_theses", many)
    monkeypatch.setattr(sb, "as_of_thesis", one)

    pit = PointInTimeData(
        db,
        asof=asof,
        known_at=PIN,
        thesis_basket=THESES,
        workspace=DEFAULT_TENANT_ID,
    )
    first_nuc = pit.term_mentions(NUCLEAR_THESIS_ID)
    assert len(first_nuc) == 2
    assert {r["documents"] for r in first_nuc} == {8, 15}
    assert len(pit.term_mentions(HIMS_THESIS_ID)) == 1
    assert pit.term_mentions(UNH_THESIS_ID) == []
    # second pass, including the empty thesis: all memo hits
    assert pit.term_mentions(UNH_THESIS_ID) == []
    assert pit.term_mentions(NUCLEAR_THESIS_ID) == first_nuc
    assert calls == {"many": 1, "one": 0}

    # fresh-list guarantee: mutating a returned list cannot poison the memo
    snapshot = list(first_nuc)
    first_nuc.append({"poison": True})
    assert pit.term_mentions(NUCLEAR_THESIS_ID) == snapshot

    # out-of-basket thesis falls back to as_of_thesis once and is memoized
    outsider = uuid.uuid4()
    assert pit.term_mentions(outsider) == []
    assert pit.term_mentions(outsider) == []
    assert calls == {"many": 1, "one": 1}


def test_pit_price_memo_cleans_holidays_sorts_and_bisects_fresh_lists(monkeypatch):
    """Pure unit proof (no Postgres): ``fact_price_eod`` is holiday-filtered and sorted once on
    memo store, ``lookback_days`` slices via bisect matching ``window_prices``, and every call
    returns a fresh list."""
    asof = date(2026, 9, 10)
    sid = uuid.uuid4()
    raw_bars = [
        {"d": date(2026, 9, 9), "valid_from": date(2026, 9, 9), "close": 103.0},
        # Labor Day 2026 (NYSE full-day closure) must be dropped at memo store
        {"d": date(2026, 9, 7), "valid_from": date(2026, 9, 7), "close": 101.0},
        {"d": date(2026, 6, 1), "valid_from": date(2026, 6, 1), "close": 90.0},
        {"d": date(2026, 9, 4), "valid_from": date(2026, 9, 4), "close": 101.0},
    ]
    calls = {"many": 0}

    def fake_many(conn, table, *, security_ids, **kwargs):
        calls["many"] += 1
        assert table == "fact_price_eod"
        return {s: list(raw_bars) if s == sid else [] for s in security_ids}

    monkeypatch.setattr(sb, "as_of_many", fake_many)
    pit = PointInTimeData(
        None,  # type: ignore[arg-type]
        asof=asof,
        known_at=PIN,
        basket=[sid],
        workspace=DEFAULT_TENANT_ID,
    )
    all_bars = pit.price_history(sid)
    assert all_bars == window_prices(raw_bars, asof, None)
    assert [b["d"] for b in all_bars] == [date(2026, 6, 1), date(2026, 9, 4), date(2026, 9, 9)]
    short_bars = pit.price_history(sid, lookback_days=10)
    assert short_bars == window_prices(raw_bars, asof, 10)
    assert [b["d"] for b in short_bars] == [date(2026, 9, 4), date(2026, 9, 9)]
    assert calls["many"] == 1
    # fresh-list guarantee for both lookback_days=None and lookback_days=10
    all_bars.append({"d": date(2099, 1, 1)})
    short_bars.clear()
    assert len(pit.price_history(sid)) == 3
    assert len(pit.price_history(sid, lookback_days=10)) == 2


def test_pit_thesis_basket_unit_prefetches_and_validates(monkeypatch):
    """Pure unit proof (no Postgres): ``thesis_basket`` prefetches in one ``as_of_many_theses``
    call, memoizes empty lists, falls back for outsiders, and ``as_of_many_theses`` validates
    table scope and empty inputs."""
    asof = date(2026, 6, 1)
    t1, t2, t3, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    calls = {"many": 0, "one": 0}

    def fake_many_theses(conn, table, *, thesis_ids, **kwargs):
        calls["many"] += 1
        assert table == "fact_term_mentions"
        return {
            tid: (
                [{"thesis_id": tid, "valid_from": date(2026, 4, 30), "documents": 10}]
                if tid in (t1, t2)
                else []
            )
            for tid in thesis_ids
        }

    def fake_one_thesis(conn, table, *, thesis_id, **kwargs):
        calls["one"] += 1
        return []

    monkeypatch.setattr(sb, "as_of_many_theses", fake_many_theses)
    monkeypatch.setattr(sb, "as_of_thesis", fake_one_thesis)
    pit = PointInTimeData(
        None,  # type: ignore[arg-type]
        asof=asof,
        known_at=PIN,
        thesis_basket=[t1, t2, t3],
        workspace=DEFAULT_TENANT_ID,
    )
    rows_t1 = pit.term_mentions(t1)
    assert len(rows_t1) == 1
    assert len(pit.term_mentions(t2)) == 1
    assert pit.term_mentions(t3) == []
    assert pit.term_mentions(t3) == []
    assert calls == {"many": 1, "one": 0}
    rows_t1.append({"poison": True})
    assert len(pit.term_mentions(t1)) == 1
    assert pit.term_mentions(outsider) == []
    assert pit.term_mentions(outsider) == []
    assert calls == {"many": 1, "one": 1}

    assert (
        as_of_many_theses(
            None,  # type: ignore[arg-type]
            "fact_term_mentions",
            thesis_ids=[],
            asof=asof,
            known_at=PIN,
            tenant_id=DEFAULT_TENANT_ID,
        )
        == {}
    )
    with pytest.raises(ValueError, match="unknown thesis-scoped fact table"):
        as_of_many_theses(
            None,  # type: ignore[arg-type]
            "fact_price_eod",
            thesis_ids=[t1],
            asof=asof,
            known_at=PIN,
            tenant_id=DEFAULT_TENANT_ID,
        )
