from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from backtest.nulls import draw_nulls
from backtest.pooled import build_report
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import Grade
from replay.export import export_snapshot
from replay.pit import connect_mirror
from replay.schema import Episode
from replay.scoring import RealizedPrices, _f, score_episodes

# M1 -- THE TAPE MEMO on the SCORING reader.
#
# `RealizedPrices` had no cache, and every `score_window` issued TWO DuckDB queries. The null models call
# it K times per episode for the name PLUS once per basket member for the benchmark -- and because the
# timing null varies the entry date on every draw, `_BasketBenchmark`'s own `(thesis, entry, exit)` memo
# misses on every single one. That is `2*K*M + 4K + 2M` queries per episode: ~2,374 at K=5 on a 196-name
# basket, and MEASURED 658 s of nulls at K=5 on a two-week run.
#
# Three properties carry this slice, and two of them are the only reasons a cache is allowed here at all:
# the memoized reader returns BYTE-IDENTICAL artifacts to the one it replaces, the query count collapses
# to one per security TOUCHED, and the cache is keyed on the security alone -- no time key, because this
# reader is deliberately forward-unbounded (the distinction from `ReplayPointInTimeData`).

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_T0 = date(2026, 1, 5)


class _PreMemoRealizedPrices(RealizedPrices):
    """The reader as it was BEFORE M1, kept verbatim as the oracle.

    Equality is asserted against the code that was actually replaced, not against a stored snapshot of
    its output: a golden would only pin whatever this fixture happens to produce, while this pins that
    the memo changed nothing about what the reader answers."""

    def _closes(self, security_id, where, params):
        rows = self.con.execute(
            f"SELECT d, close FROM fact_price_eod "
            f"WHERE tenant_id = ? AND security_id = ? {where} "
            f"QUALIFY ROW_NUMBER() OVER "
            f"(PARTITION BY security_id, d ORDER BY recorded_at DESC, id DESC) = 1 "
            f"ORDER BY d",
            [str(self.tenant_id), str(security_id), *params],
        ).fetchall()
        return [(r[0], float(r[1])) for r in rows if r[1] is not None]

    def _bars(self, security_id, where, params):
        rows = self.con.execute(
            f"SELECT d, open, high, low, close, volume FROM fact_price_eod "
            f"WHERE tenant_id = ? AND security_id = ? {where} "
            f"QUALIFY ROW_NUMBER() OVER "
            f"(PARTITION BY security_id, d ORDER BY recorded_at DESC, id DESC) = 1 "
            f"ORDER BY d",
            [str(self.tenant_id), str(security_id), *params],
        ).fetchall()
        return [
            {
                "d": r[0],
                "open": _f(r[1]),
                "high": _f(r[2]),
                "low": _f(r[3]),
                "close": float(r[4]),
                "volume": _f(r[5]),
            }
            for r in rows
            if r[4] is not None
        ]

    def first_close_on_or_after(self, security_id, d):
        rows = self._closes(security_id, "AND d >= ?", [d])
        return rows[0] if rows else None

    def last_close_through(self, security_id, through):
        rows = self._closes(security_id, "AND d <= ?", [through])
        return rows[-1] if rows else None

    def closes_between(self, security_id, start, end):
        return self._closes(security_id, "AND d >= ? AND d <= ?", [start, end])

    def bars_between(self, security_id, start, end):
        return self._bars(security_id, "AND d >= ? AND d <= ?", [start, end])

    def tape_edge(self, security_id, on_or_after):
        rows = self._closes(security_id, "AND d >= ?", [on_or_after])
        return rows[-1][0] if rows else None


class _CountingConnection:
    """Counts the queries a reader issues. The B5a lesson, held as a test rather than as a hope: the cost
    was never query COMPLEXITY, so a timing would be the wrong instrument and would drift with the box.
    """

    def __init__(self, con):
        self._con = con
        self.sql: list[str] = []

    @property
    def queries(self) -> int:
        return len(self.sql)

    @property
    def tape_reads(self) -> int:
        """Per-SECURITY reads only — the term that used to scale with episodes x draws x basket size.
        Counted apart from the one tenant-wide `max(d)` market-edge probe, which is a different question
        and is already resolved once per reader."""
        return sum(1 for q in self.sql if "security_id = ?" in q)

    def execute(self, sql, *args, **kwargs):
        self.sql.append(sql)
        return self._con.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._con, name)


def _security(db, ticker: str, *, seq: int) -> uuid.UUID:
    """DETERMINISTIC ids. `episode_seed` hashes the security id, so random ids would reshuffle every
    null draw between runs and the query count this file REPORTS would move on every invocation. A test
    that prints a measured number has to print the same one twice."""
    sid = uuid.UUID(int=0xF00 + seq)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            # the CIK comes from `seq`, NOT from hash(ticker): `hash` of a str is randomized per process
            # (PYTHONHASHSEED), which would make a fixture that calls itself deterministic not be
            (sid, DEFAULT_TENANT_ID, ticker, f"{seq:010d}", "2025-01-01"),
        )
    return sid


def _tape(db, sid: uuid.UUID, *, bars: int, base: float) -> None:
    """A simple ascending tape. Weekends included deliberately -- the reader slices whatever the mirror
    holds, and a bar's existence is the tape's business, not the calendar's."""
    for i in range(bars):
        d = _T0 + timedelta(days=i)
        px = base + i * 0.5
        append_fact(
            db,
            "fact_price_eod",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                "d": d,
                "open": px - 0.25,
                "high": px + 0.75,
                "low": px - 0.75,
                "close": px,
                "volume": 1000 + i,
                "valid_from": d,
                "recorded_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
            },
        )


#: The bar that is CORRECTED in the fixture — same ``(security_id, d)``, a later ``recorded_at``, a
#: different close and different wicks. It is what makes the dedup a TEST rather than an argument: the
#: memo reads a security's whole tape in one query and slices it, so if dedup-then-slice ever stopped
#: agreeing with slice-then-dedup, this is the row that would catch it.
_CORRECTED_OFFSET = 30
_CORRECTED_CLOSE = 999.5


def _correct_bar(db, sid: uuid.UUID, d: date, *, close: float) -> None:
    """Re-version one bar: the SAME ``(security_id, d)`` natural key, a LATER ``recorded_at``, different
    close and different wicks. The reader must return this one and never the original."""
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "open": close - 1.0,
            "high": close + 5.0,
            "low": close - 5.0,
            "close": close,
            "volume": 4242,
            "valid_from": d,
            # LATER than the original's recorded_at, which is what makes it win the QUALIFY
            "recorded_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        },
    )


@pytest.fixture
def basket(db, tmp_path):
    """A roster of 8 priced names over 120 bars, exported to a mirror. Small enough to be fast, wide
    enough that the per-member benchmark cost (the term the memo kills) is actually visible.

    ONE BAR IS RE-VERSIONED (see ``_CORRECTED_OFFSET``): ``DEV0`` carries a later-recorded correction on
    day 30. Without it the whole "the QUALIFY dedup partitions by date, so filtering commutes with it"
    argument would be asserted only in prose — every row would be its own latest version and a broken
    memo would pass."""
    sids = [_security(db, f"DEV{i}", seq=i) for i in range(8)]
    for i, sid in enumerate(sids):
        _tape(db, sid, bars=120, base=100.0 + i)
    _correct_bar(db, sids[0], _T0 + timedelta(days=_CORRECTED_OFFSET), close=_CORRECTED_CLOSE)
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        yield sids, con
    finally:
        con.close()


def _episodes(sids, tid: uuid.UUID, n: int) -> list[Episode]:
    return [
        Episode(
            thesis_id=tid,
            security_id=sids[i % len(sids)],
            is_headline=True,
            arm_date=_T0 + timedelta(days=10 + i),
            last_armed_date=_T0 + timedelta(days=10 + i),
            dearm_date=None,
            close_reason="window_end",
            exit_by=_T0 + timedelta(days=40 + i),
            entry_grade=Grade.CORE,
            key1_source="insider",
            co_arm_bucket="alone",
        )
        for i in range(n)
    ]


def _run(reader, episodes, sids, tid, sessions, *, draws=5):
    """Score + both nulls + the pooled report, exactly as `backtest.run.execute` composes them."""
    outcomes = score_episodes(episodes, reader)
    nulls = draw_nulls(
        episodes,
        reader,
        roster_at=lambda _t, _d, _s=sids: _s,
        sessions=sessions,
        seed="m1-fixed-seed",
        draws=draws,
    )
    pooled = build_report(
        episodes, nulls, draws=draws, seed="m1-fixed-seed", sessions=len(sessions)
    )
    return outcomes, pooled


# --- (a) byte identity ------------------------------------------------------------------------------


def test_the_memo_changes_no_artifact_byte(basket):
    """The whole licence for the cache. Outcomes and the pooled report are compared as SERIALIZED BYTES,
    not field by field: a field-wise assertion silently stops covering a field the moment one is added,
    and these are the artifacts a reader quotes from."""
    sids, con = basket
    tid = uuid.UUID(int=0xE1)
    sessions = [_T0 + timedelta(days=i) for i in range(120)]
    episodes = _episodes(sids, tid, 6)

    memo_out, memo_pooled = _run(RealizedPrices(con), episodes, sids, tid, sessions)
    pre_out, pre_pooled = _run(_PreMemoRealizedPrices(con), episodes, sids, tid, sessions)

    assert [o.model_dump_json() for o in memo_out] == [o.model_dump_json() for o in pre_out]
    assert memo_pooled.model_dump_json() == pre_pooled.model_dump_json()
    # ...and the run was not vacuous: a comparison of two empty things proves nothing
    assert any(o.forward_return is not None for o in memo_out)
    assert memo_pooled.n_scoreable > 0


def test_every_reader_method_answers_identically_including_the_edges(basket):
    """Method by method, on the boundaries a bisect is the likeliest place to get wrong: before the tape,
    after it, exactly on the first and last bar, and on a date the tape does not contain."""
    sids, con = basket
    memo, pre = RealizedPrices(con), _PreMemoRealizedPrices(con)
    sid = sids[0]
    probes = [
        _T0 - timedelta(days=5),  # before the tape
        _T0,  # the first bar exactly
        _T0 + timedelta(days=59),
        _T0 + timedelta(days=119),  # the last bar exactly
        _T0 + timedelta(days=400),  # past the end
    ]
    for d in probes:
        assert memo.first_close_on_or_after(sid, d) == pre.first_close_on_or_after(sid, d), d
        assert memo.last_close_through(sid, d) == pre.last_close_through(sid, d), d
        assert memo.tape_edge(sid, d) == pre.tape_edge(sid, d), d
    for lo in probes:
        for hi in probes:
            assert memo.closes_between(sid, lo, hi) == pre.closes_between(sid, lo, hi), (lo, hi)
            assert memo.bars_between(sid, lo, hi) == pre.bars_between(sid, lo, hi), (lo, hi)
    # an inverted window is empty on both, not an error and not a reversed slice
    hi, lo = _T0, _T0 + timedelta(days=30)
    assert memo.closes_between(sid, lo, hi) == pre.closes_between(sid, lo, hi) == []

    # THE RE-VERSIONED BAR: agreeing with the oracle is not enough, because both could be wrong the same
    # way. The corrected close is asserted directly — latest version wins, through the memo's one-query
    # read and its slice, on the close AND on the wicks (`bars_between` reads different columns).
    corrected = _T0 + timedelta(days=_CORRECTED_OFFSET)
    assert memo.closes_between(sid, corrected, corrected) == [(corrected, _CORRECTED_CLOSE)]
    bar = memo.bars_between(sid, corrected, corrected)[0]
    assert bar["close"] == _CORRECTED_CLOSE
    assert bar["high"] == _CORRECTED_CLOSE + 5.0 and bar["volume"] == 4242
    assert memo.first_close_on_or_after(sid, corrected) == (corrected, _CORRECTED_CLOSE)
    assert memo.last_close_through(sid, corrected) == (corrected, _CORRECTED_CLOSE)


def test_a_security_with_no_tape_caches_the_EMPTY_answer(basket, db):
    """A name the mirror never priced. The empty answer has to be cached like any other, or a basket
    holding unpriced members would re-query them on every benchmark window — the exact cost this slice
    removes, quietly surviving for the names most likely to appear in a wide basket."""
    sids, con = basket
    unpriced = _security(db, "NOTAPE", seq=99)
    db.commit()
    counting = _CountingConnection(con)
    memo, pre = RealizedPrices(counting), _PreMemoRealizedPrices(con)
    assert memo.first_close_on_or_after(unpriced, _T0) is pre.first_close_on_or_after(unpriced, _T0)
    assert memo.closes_between(unpriced, _T0, _T0 + timedelta(days=30)) == []
    assert memo.tape_edge(unpriced, _T0) is None
    for _ in range(20):
        memo.first_close_on_or_after(unpriced, _T0)
        memo.bars_between(unpriced, _T0, _T0 + timedelta(days=30))
    assert counting.tape_reads == 1, counting.sql


# --- (b) the query count ----------------------------------------------------------------------------


def test_the_query_count_collapses_to_one_per_security_touched(basket):
    """The point of the slice, as a COUNT. Un-memoized, the nulls issue `2*K*M + 4K + 2M` queries per
    episode and the `K*M` term dominates; memoized, a whole pass costs one query per security touched
    however many episodes, draws and benchmark windows ask for it."""
    sids, con = basket
    tid = uuid.UUID(int=0xE2)
    sessions = [_T0 + timedelta(days=i) for i in range(120)]
    episodes = _episodes(sids, tid, 6)

    memo_con = _CountingConnection(con)
    _run(RealizedPrices(memo_con), episodes, sids, tid, sessions)
    pre_con = _CountingConnection(con)
    _run(_PreMemoRealizedPrices(pre_con), episodes, sids, tid, sessions)

    touched = len(sids)
    # EXACTLY one tape read per security touched, however many episodes, draws and benchmark windows
    # asked for it. The one remaining non-tape query is `market_tape_edge`'s tenant-wide `max(d)`, which
    # answers a different question and was already resolved once per reader before this slice.
    assert memo_con.tape_reads == touched, (memo_con.tape_reads, touched)
    assert memo_con.queries == touched + 1, memo_con.sql
    # ...against a count that scales with episodes x draws x basket size
    # A FLOOR, not the real-world figure: the pre-memo count is ~2*K*M per episode, so the ratio grows
    # with basket size M. This fixture uses M=8 to stay fast and still measures ~79x; the operator's
    # 196-name basket is ~25x wider again.
    assert pre_con.tape_reads > 50 * memo_con.tape_reads, (pre_con.tape_reads, memo_con.tape_reads)
    print(
        f"\nMEASURED tape reads: pre-memo {pre_con.tape_reads} -> memo {memo_con.tape_reads} "
        f"({pre_con.tape_reads / memo_con.tape_reads:.0f}x fewer) "
        f"[{len(episodes)} episodes, K=5, basket {touched}]"
    )


def test_the_cache_is_per_security_and_survives_every_shape_of_read(basket):
    """One query per security no matter which method asks first, and no re-query for a different window
    of the same name -- the property that makes the benchmark's per-member cost vanish."""
    sids, con = basket
    counting = _CountingConnection(con)
    r = RealizedPrices(counting)
    sid = sids[0]
    r.first_close_on_or_after(sid, _T0)
    assert counting.queries == 1
    for _ in range(50):
        r.closes_between(sid, _T0, _T0 + timedelta(days=30))
        r.bars_between(sid, _T0 + timedelta(days=5), _T0 + timedelta(days=9))
        r.last_close_through(sid, _T0 + timedelta(days=80))
        r.tape_edge(sid, _T0)
    assert counting.queries == 1, "a second window of the same name must not re-query"
    r.first_close_on_or_after(sids[1], _T0)
    assert counting.queries == 2, "a different security is a different tape"


# --- the rule the next reader will be tempted to break ------------------------------------------------


def test_the_cache_has_no_time_key_and_must_not_grow_one():
    """Structural, so it holds without running anything. The PIT's memo REQUIRES an (asof, known_at,
    tenant) key; this reader is its opposite -- deliberately forward-unbounded and time-capless, which is
    what lets the scorer read past the as-of while no path exists from forward data back to an as-of call.
    A time key here would not tighten anything; it would silently truncate the scorer."""
    import ast
    import inspect
    import textwrap

    # the BODY, with the docstring dropped -- the docstring says "asof/known_at" on purpose, to explain
    # the very rule this test enforces, and scanning it would make the test assert the opposite
    tree = ast.parse(textwrap.dedent(inspect.getsource(RealizedPrices._tape)))
    fn = tree.body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    code = "\n".join(ast.unparse(node) for node in body)
    assert "asof" not in code and "known_at" not in code, code
    # ...and the cache is keyed by the security alone, in the signature and in the dict
    assert [a.arg for a in fn.args.args] == ["self", "security_id"]
    window = ast.parse(textwrap.dedent(inspect.getsource(RealizedPrices._window)))
    assert [a.arg for a in window.body[0].args.args] == ["self", "security_id", "lo", "hi"]
