from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from db.bitemporal import _FACT_IDENTITY, append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals.horizons import call_bounds

# B5a — the replay PIT's memo + basket prefetch + registry bounds. The bar these tests hold is NOT
# "it got faster": it is that the batched read is row-for-row and ORDER-for-order the same view the
# per-security read gives, on every table, including the empty ones.
#
# WHY THE COUNT COMPARISON COMES FIRST. The mirror stores uuid columns as VARCHAR
# (``export._OID_ARROW[2950]`` is ``pa.string()``), so a prefetch that keys its memo on the raw
# ``security_id`` column keys it on TEXT while every lookup uses a UUID. Every lookup then misses, the
# prefetch serves empty lists, and the run is 35x "faster" while reading nothing. A set comparison of
# the rows that DID come back passes that bug; comparing COUNTS per security catches it immediately.

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_KNOWN = datetime(2026, 12, 31, tzinfo=timezone.utc)


@pytest.fixture
def securities(db) -> list[uuid.UUID]:
    """Five securities — enough that a batch read has to partition, few enough to assert row-by-row."""
    ids = [uuid.uuid4() for _ in range(5)]
    with db.cursor() as cur:
        for i, sid in enumerate(ids):
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, f"DEV{i}", f"000000000{i}", "2026-01-01"),
            )
    db.commit()
    return ids


def _insider(sid, *, accession, valid_from, usd, recorded_at, insider="CEO", seq=0):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "insider_name": insider,
        "txn_code": "P",
        "usd": usd,
        "accession": accession,
        "txn_seq": seq,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def _bar(sid, *, d, close, recorded_at):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "d": d,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000,
        "valid_from": d,
        "recorded_at": recorded_at,
    }


def _seed(db, securities):
    """A deliberately UNEVEN tape: different row counts per security, one security with nothing at all,
    shared accession numbers across securities (the insider identity is unique only WITHIN a security —
    the exact case a batch partition that forgets to prefix ``security_id`` collapses), and a
    re-versioned fact so the ``recorded_at DESC`` version pick is exercised on both paths."""
    t0 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    for i, sid in enumerate(securities[:-1]):  # the LAST security deliberately gets no rows at all
        for n in range(i + 1):
            append_fact(
                db,
                "fact_insider_txn",
                _insider(
                    sid,
                    accession="shared-accn",  # SAME accession across securities, on purpose
                    valid_from=date(2026, 1, 5) + timedelta(days=n),
                    usd=100_000 * (n + 1),
                    recorded_at=t0,
                    seq=n,
                ),
            )
        for n in range(3 + i):
            append_fact(
                db,
                "fact_price_eod",
                _bar(sid, d=date(2026, 1, 2) + timedelta(days=n), close=10.0 + n, recorded_at=t0),
            )
    # a RE-VERSION of one bar: same (security_id, d), later recorded_at, different close. The latest
    # version must win identically on both read paths.
    append_fact(
        db,
        "fact_price_eod",
        _bar(
            securities[0],
            d=date(2026, 1, 2),
            close=99.0,
            recorded_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        ),
    )
    db.commit()


_SEC_TABLES = ("fact_insider_txn", "fact_price_eod")


def test_prefetch_equals_per_security_rows(db, securities, tmp_path):
    """The batched read equals the per-security read for EVERY security and table: same COUNT first,
    then the same identity tuples IN THE SAME ORDER. Counts first — see the module note."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        asof = date(2026, 1, 31)
        scoped = ReplayPointInTimeData(con, asof=asof, known_at=_KNOWN)
        batched = ReplayPointInTimeData(con, asof=asof, known_at=_KNOWN, basket=securities)
        for table in _SEC_TABLES:
            ident = _FACT_IDENTITY[table]
            for sid in securities:
                a = scoped._as_of(table, "security_id", sid)
                b = batched._as_of(table, "security_id", sid)
                assert len(a) == len(b), f"{table} {sid}: {len(a)} scoped vs {len(b)} batched"
                assert [tuple(str(r[c]) for c in ident) for r in a] == [
                    tuple(str(r[c]) for c in ident) for r in b
                ], f"{table} {sid}: identity tuples differ in content or ORDER"
                assert a == b, f"{table} {sid}: full rows differ"
    finally:
        con.close()


def test_prefetch_picks_the_same_version(db, securities, tmp_path):
    """The re-versioned bar resolves to the LATEST version on both paths (``recorded_at DESC``)."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        asof = date(2026, 1, 31)
        sid = securities[0]
        scoped = ReplayPointInTimeData(con, asof=asof, known_at=_KNOWN).price_history(sid)
        batched = ReplayPointInTimeData(
            con, asof=asof, known_at=_KNOWN, basket=securities
        ).price_history(sid)
        first = [r for r in scoped if r["d"] == date(2026, 1, 2)]
        assert len(first) == 1 and first[0]["close"] == pytest.approx(99.0)
        assert scoped == batched
    finally:
        con.close()


def test_empty_member_is_memoized_not_requeried(db, securities, tmp_path):
    """A basket member with NO rows in a table gets an empty list from the prefetch and is never
    re-queried — the ``as_of_many`` rule (an entry for every requested id, ``[]`` included)."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        pit = ReplayPointInTimeData(con, asof=date(2026, 1, 31), known_at=_KNOWN, basket=securities)
        empty = securities[-1]  # seeded with nothing
        assert pit.insider_txns(empty) == []
        n_after_first = _count_queries(con, lambda: pit.insider_txns(empty))
        assert n_after_first == 0, "an empty memo entry must not re-query"
    finally:
        con.close()


def _count_queries(con, fn) -> int:
    """Run ``fn`` and return how many ``execute`` calls it issued on ``con``."""
    calls = {"n": 0}
    orig = type(con).execute

    def counting(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    type(con).execute = counting
    try:
        fn()
    finally:
        type(con).execute = orig
    return calls["n"]


def test_memo_key_excludes_lookback_days(db, securities, tmp_path):
    """``price_history``'s per-call ``lookback_days`` is NEVER a memo key: two calls with different
    windows issue ONE query, and each still sees at least its own window (the live twin's memo rule).
    """
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        sid = securities[3]
        pit = ReplayPointInTimeData(con, asof=date(2026, 1, 31), known_at=_KNOWN, basket=securities)
        n_first = _count_queries(con, lambda: pit.price_history(sid, 3650))
        n_second = _count_queries(con, lambda: pit.price_history(sid, 7))
        assert n_first == 1, "the first read is the ONE basket-wide query"
        assert n_second == 0, "a second read with a different lookback must hit the memo"
        wide = pit.price_history(sid, 3650)
        narrow = pit.price_history(sid, 7)
        assert len(wide) >= len(narrow) and len(wide) > 0
    finally:
        con.close()


def test_prefetch_issues_one_query_per_table(db, securities, tmp_path):
    """The whole point: a basket-wide sweep costs ONE query per table, not one per (table, member)."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        pit = ReplayPointInTimeData(con, asof=date(2026, 1, 31), known_at=_KNOWN, basket=securities)

        def read_all():
            for sid in securities:
                pit.insider_txns(sid)
                pit.price_history(sid)

        assert _count_queries(con, read_all) == 2  # one per table, for all five members
    finally:
        con.close()


def test_row_order_is_pinned_and_deterministic(db, securities, tmp_path):
    """Row ORDER is an explicit ``ORDER BY``, not whatever ``QUALIFY`` happened to emit — repeated reads
    on fresh views agree, and the batched order matches the scoped order within a security."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        asof = date(2026, 1, 31)
        sid = securities[3]
        runs = [
            ReplayPointInTimeData(con, asof=asof, known_at=_KNOWN).insider_txns(sid)
            for _ in range(5)
        ]
        assert all(r == runs[0] for r in runs)
        batched = ReplayPointInTimeData(
            con, asof=asof, known_at=_KNOWN, basket=securities
        ).insider_txns(sid)
        assert batched == runs[0]
        ident = _FACT_IDENTITY["fact_insider_txn"]
        keys = [tuple(str(r[c]) for c in ident) for r in runs[0]]
        assert keys == sorted(keys), "the pinned order is the identity order"
    finally:
        con.close()


def test_bounds_floor_drops_only_older_facts(db, securities, tmp_path):
    """A registry floor drops whole facts OLDER than the floor and nothing else — it never changes which
    version of a surviving fact wins (``db/bitemporal.py:176-182``)."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        asof = date(2026, 1, 31)
        sid = securities[3]
        unbounded = ReplayPointInTimeData(con, asof=asof, known_at=_KNOWN).price_history(sid)
        # a 10-day floor sits after the seeded bars (2026-01-02..) as of 2026-01-31 -> all dropped
        tight = ReplayPointInTimeData(
            con, asof=asof, known_at=_KNOWN, bounds={"fact_price_eod": 10}
        ).price_history(sid)
        assert unbounded and tight == []
        # a floor wider than the tape changes nothing at all
        wide = ReplayPointInTimeData(
            con, asof=asof, known_at=_KNOWN, bounds={"fact_price_eod": 3650}
        ).price_history(sid)
        assert wide == unbounded
        # ...and the surviving re-versioned bar still resolves to the LATEST version under a floor
        sid0 = securities[0]
        bounded0 = ReplayPointInTimeData(
            con, asof=asof, known_at=_KNOWN, bounds={"fact_price_eod": 3650}
        ).price_history(sid0)
        first = [r for r in bounded0 if r["d"] == date(2026, 1, 2)]
        assert len(first) == 1 and first[0]["close"] == pytest.approx(99.0)
    finally:
        con.close()


def test_bounds_apply_identically_on_both_paths(db, securities, tmp_path):
    """The floor is applied on the scoped path AND the prefetch path, with the same result."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        asof = date(2026, 1, 31)
        bounds = {"fact_price_eod": 32, "fact_insider_txn": 32}
        scoped = ReplayPointInTimeData(con, asof=asof, known_at=_KNOWN, bounds=bounds)
        batched = ReplayPointInTimeData(
            con, asof=asof, known_at=_KNOWN, basket=securities, bounds=bounds
        )
        for sid in securities:
            assert scoped.price_history(sid) == batched.price_history(sid)
            assert scoped.insider_txns(sid) == batched.insider_txns(sid)
    finally:
        con.close()


def test_bounds_come_from_the_run_cfg_not_the_default():
    """A sweep that widens a liveness dial MUST widen the PIT floor with it, or the read silently
    truncates the very window the sweep is measuring. ``call_bounds`` is the derivation; this pins that
    it actually MOVES with the cfg, so wiring the harness to ``DEFAULT_CONFIG`` would fail visibly here.

    Note the floor moves on BOTH bounded tables, not just the obvious one: the insider detector declares
    ``insider_core_alpha_liveness_days`` against ``fact_price_eod`` as well (it prices the off-market
    screen), so widening it 180 -> 900 moves the price floor 460 -> 960. That coupling is exactly why the
    floor is registry-DERIVED rather than hand-typed per table.
    """
    base = call_bounds(DEFAULT_CONFIG)
    widened = call_bounds(
        DEFAULT_CONFIG.model_copy(update={"insider_core_alpha_liveness_days": 900})
    )
    assert base["fact_insider_txn"] is not None and base["fact_price_eod"] is not None
    assert widened["fact_insider_txn"] > base["fact_insider_txn"]
    assert widened["fact_price_eod"] > base["fact_price_eod"]
    # ...and a POLICY dial (assembler-side, declared by no detector horizon) moves no floor at all
    policy_only = call_bounds(DEFAULT_CONFIG.model_copy(update={"headline_lapsing_soon_days": 999}))
    assert policy_only == base


def test_unbounded_by_default(db, securities, tmp_path):
    """No ``bounds`` argument means NO floor — every pre-B5a caller (and the parity gate) is unchanged."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        pit = ReplayPointInTimeData(con, asof=date(2026, 1, 31), known_at=_KNOWN)
        assert pit._lower("fact_price_eod") is None
        assert len(pit.price_history(securities[3])) == 6
    finally:
        con.close()


def test_unknown_table_still_raises(db, securities, tmp_path):
    """The whitelist guard survives the seam rewrite."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        pit = ReplayPointInTimeData(con, asof=date(2026, 1, 31), known_at=_KNOWN)
        with pytest.raises(ValueError, match="unknown fact table"):
            pit._as_of("fact_not_a_table", "security_id", securities[0])
    finally:
        con.close()


def test_memo_hands_back_a_fresh_list(db, securities, tmp_path):
    """Mutating a returned list must not corrupt the memo (the live twin's rule)."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        pit = ReplayPointInTimeData(con, asof=date(2026, 1, 31), known_at=_KNOWN, basket=securities)
        sid = securities[3]
        rows = pit.price_history(sid)
        n = len(rows)
        rows.clear()
        assert len(pit.price_history(sid)) == n
    finally:
        con.close()


def test_member_outside_the_basket_falls_back(db, securities, tmp_path):
    """An id outside the basket (a benchmark) takes the per-security path and is memoized there."""
    _seed(db, securities)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        outsider = securities[3]
        pit = ReplayPointInTimeData(
            con, asof=date(2026, 1, 31), known_at=_KNOWN, basket=securities[:2]
        )
        rows = pit.price_history(outsider)
        assert rows  # served, not silently empty
        assert _count_queries(con, lambda: pit.price_history(outsider)) == 0  # and memoized
    finally:
        con.close()
