"""Tier 2 cold-compute speedup tests:
- ``PgRealizedPrices`` batch prefetch + ``bisect`` edge cases and query-count guards
- Batching equivalence (multi-thesis Edition 0, Edition N, ``assemble_scoreboard``, ``edition_diff``)
  and O(1) query-count verification (no per-thesis N+1, no duplicate ``fact_price_eod`` scan)
- Two-pass JSONB trimming (``SCOREBOARD_RECORD_DROP_CARD_KEYS`` + selective ``risk_signals``
  hydration) and per-thesis malformed-card fault isolation across Edition 0, Edition N, and diff.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from app import call_cache
from app.routers import scoreboard as scoreboard_router
from calls.assembler import assemble_call
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind
from domain.thesis import BasketMember, Segment, Thesis
from replay.schema import Episode
from replay.scoring import score_episode
from repositories import calls_repo, decisions_repo, editions_repo, thesis_repo
from scoreboard.assemble import assemble_scoreboard
from scoreboard.diff import edition_diff
from scoreboard.prices import PgRealizedPrices
from scoreboard.record import (
    SCOREBOARD_RECORD_DROP_CARD_KEYS,
    EditionSource,
    derive_thesis_record,
    scoreboard_records,
)
from tests.calls.factories import (
    breakdown_event,
    dilution_event,
    insider_event,
    insider_sell_event,
)
from tests.scoreboard.helpers import bar, keys_fired, on_time, persist_thesis, record_day

CUT = datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc)


class _CountingConn:
    """Thin psycopg connection proxy that records every executed SQL statement."""

    def __init__(self, conn):
        self._conn = conn
        self.sql: list[str] = []

    def cursor(self, *args, **kwargs):
        return _CountingCursor(self._conn.cursor(*args, **kwargs), self.sql)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class _CountingCursor:
    def __init__(self, cur, sql_log: list[str]):
        self._cur = cur
        self._sql = sql_log

    def execute(self, query, params=None, *args, **kwargs):
        self._sql.append(str(query))
        return self._cur.execute(query, params, *args, **kwargs)

    def __enter__(self):
        self._cur.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._cur.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)


def _extra_security(db, ticker: str, cik: str, name: str = "Extra Corp") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, '2026-01-01')",
            (sid, DEFAULT_TENANT_ID, ticker, cik, name),
        )
    db.commit()
    return sid


def _pin_snapshot(db, thesis_id: uuid.UUID, when: datetime) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE id = ("
            "  SELECT id FROM basket_snapshot WHERE thesis_id = %s "
            "  ORDER BY taken_at DESC, id DESC LIMIT 1"
            ")",
            (when, thesis_id),
        )
    db.commit()


# ---------------------------------------------------------------------------------------------------------
# 2.2 PgRealizedPrices prefetch + bisect edge cases
# ---------------------------------------------------------------------------------------------------------


def test_pg_prices_prefetch_loads_all_securities_in_one_query_and_answers_via_bisect(db):
    s1 = _extra_security(db, "PF1", "0000100001")
    s2 = _extra_security(db, "PF2", "0000100002")
    empty_sid = _extra_security(db, "PF3", "0000100003")

    t1 = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 2, 12, tzinfo=timezone.utc)
    # s1: restated bar, restated-to-NULL bar (must be skipped!), holiday bar (Labor Day 2026-09-07),
    # and a bar past the cap
    bar(db, s1, date(2026, 6, 1), 100.0, recorded_at=t1, high=102.0, low=99.0)
    bar(db, s1, date(2026, 6, 1), 101.5, recorded_at=t2, high=103.0, low=98.0)
    bar(db, s1, date(2026, 6, 2), 105.0, recorded_at=t1)
    bar(db, s1, date(2026, 6, 2), None, recorded_at=t2)  # later null close supersedes 105.0 -> skip
    bar(db, s1, date(2026, 6, 5), 110.0, recorded_at=t2, high=112.0, low=108.0)
    bar(db, s1, date(2026, 9, 7), 110.0, recorded_at=t2)  # Labor Day closure -> skip
    bar(db, s1, date(2026, 9, 8), 115.0, recorded_at=t2)
    bar(db, s1, date(2026, 9, 20), 200.0, recorded_at=t2)  # beyond cap

    # s2: simple two-bar tape
    bar(db, s2, date(2026, 6, 3), 50.0, recorded_at=t2)
    bar(db, s2, date(2026, 6, 10), 55.0, recorded_at=t2)

    cap = date(2026, 9, 10)
    counting = _CountingConn(db)
    prefetched = PgRealizedPrices(counting, tenant_id=DEFAULT_TENANT_ID, cap=cap)
    lazy = PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap)

    counting.sql.clear()
    prefetched.prefetch([s1, s2, empty_sid, s1])  # duplicates tolerated
    assert len(counting.sql) == 1  # ONE batch SQL query for all three securities
    prefetched.prefetch([s1, s2, empty_sid])
    assert len(counting.sql) == 1  # already cached -> 0 queries

    probes = [
        date(2026, 5, 31),
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 5),
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 10),
        date(2026, 9, 25),
    ]
    for sid in (s1, s2, empty_sid):
        for d in probes:
            assert prefetched.first_close_on_or_after(sid, d) == lazy.first_close_on_or_after(
                sid, d
            )
            assert prefetched.last_close_through(sid, d) == lazy.last_close_through(sid, d)
            assert prefetched.tape_edge(sid, d) == lazy.tape_edge(sid, d)
            for end in probes:
                assert prefetched.closes_between(sid, d, end) == lazy.closes_between(sid, d, end)
                assert prefetched.bars_between(sid, d, end) == lazy.bars_between(sid, d, end)

    # All lookups above hit the prefetched bisect cache -> ZERO extra SQL queries!
    assert len(counting.sql) == 1

    # Explicit edge checks:
    # - restated-to-NULL bar on 06-02 is absent
    assert [d for d, _ in prefetched.closes_between(s1, date(2026, 6, 1), date(2026, 6, 5))] == [
        date(2026, 6, 1),
        date(2026, 6, 5),
    ]
    assert prefetched.first_close_on_or_after(s1, date(2026, 6, 1)) == (date(2026, 6, 1), 101.5)
    # - holiday bar on 09-07 is absent
    assert date(2026, 9, 7) not in {
        d for d, _ in prefetched.closes_between(s1, date(2026, 9, 1), date(2026, 9, 10))
    }
    # - inverted window start > end -> []
    assert prefetched.closes_between(s1, date(2026, 6, 10), date(2026, 6, 1)) == []
    assert prefetched.bars_between(s1, date(2026, 6, 10), date(2026, 6, 1)) == []
    # - mutating bars_between dicts never mutates internal cache
    bars = prefetched.bars_between(s1, date(2026, 6, 1), date(2026, 6, 5))
    bars[0]["close"] = -999.0
    bars[0]["sma_50"] = 42.0
    fresh = prefetched.bars_between(s1, date(2026, 6, 1), date(2026, 6, 5))
    assert fresh[0]["close"] == 101.5 and "sma_50" not in fresh[0]

    # - episode scoring parity
    ep = Episode(
        thesis_id=uuid.uuid4(),
        security_id=s1,
        is_headline=True,
        arm_date=date(2026, 6, 1),
        last_armed_date=date(2026, 6, 5),
        dearm_date=date(2026, 6, 6),
        close_reason="dearmed_other",
        exit_by=date(2026, 9, 8),
    )
    assert score_episode(ep, prefetched) == score_episode(ep, lazy)


def test_pg_prices_market_tape_edge_caches_none_when_tape_empty(db):
    sid = _extra_security(db, "EMPTY", "0000100009")
    counting = _CountingConn(db)
    reader = PgRealizedPrices(counting, tenant_id=DEFAULT_TENANT_ID, cap=date(2026, 6, 10))
    counting.sql.clear()
    assert reader.market_tape_edge(sid) is None
    assert len(counting.sql) == 1
    assert reader.market_tape_edge(sid) is None
    assert len(counting.sql) == 1  # cached None, never rescans


# ---------------------------------------------------------------------------------------------------------
# 2.1 + 2.3 Multi-thesis batching equivalence, O(1) query counts, and selective risk_signals hydration
# ---------------------------------------------------------------------------------------------------------


def test_scoreboard_records_and_assemble_batch_equivalence_and_query_counts(db, security_id):
    """Multi-thesis Edition 0: batched ``scoreboard_records`` matches per-thesis
    ``derive_thesis_record`` field-for-field, selective Pass-2 ``risk_signals`` keeps exact wire order
    for episode securities only, and ``assemble_scoreboard`` + ``read_scoreboard`` avoid N+1 queries
    and duplicate ``fact_price_eod`` scans."""
    s1 = security_id
    s2 = _extra_security(db, "CO2", "0000200002", "Company Two Inc.")
    s3 = _extra_security(db, "CO3", "0000200003", "Company Three Inc.")
    s_other = _extra_security(db, "OTHR", "0000200004", "Non-Episode Member Inc.")

    # Thesis 1: multi-member basket (s1 + s_other), PIT snapshot change, episode with risk signals on
    # both s1 and s_other, breakdown de-arm, Form 4 provenance, operator take + close
    t1 = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Alpha Multi",
        narrative="multi-member thesis",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="DEVCO", role="r", security_id=s1, segment="Core")],
    )
    thesis_repo.upsert(db, t1)
    db.commit()
    _pin_snapshot(db, t1.id, datetime(2026, 5, 1, tzinfo=timezone.utc))
    t1_v2 = t1.model_copy(
        update={
            "basket": [
                t1.basket[0],
                BasketMember(ticker="OTHR", role="r", security_id=s_other, segment="Core"),
            ]
        }
    )
    thesis_repo.upsert(db, t1_v2)
    db.commit()
    _pin_snapshot(db, t1.id, datetime(2026, 6, 20, tzinfo=timezone.utc))

    # Thesis 2: single-name warming-only thesis (exercises withheld-arm metric in assemble_scoreboard)
    t2 = persist_thesis(db, s2, thesis_id=uuid.uuid4())
    # Thesis 3: single-name armed open episode with late re-version + reconstructed row
    t3 = persist_thesis(db, s3, thesis_id=uuid.uuid4())

    # Seed Thesis 1 cards: warming -> armed with risks (s1 + s_other) -> breakdown de-arm
    warm1 = insider_event(security_id=s1, liveness=60).model_copy(
        update={"asof": date(2026, 5, 29)}
    )
    conv1, conf1 = keys_fired(s1, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    sell_s1 = insider_sell_event().model_copy(
        update={"security_id": s1, "asof": date(2026, 6, 2), "label": "CEO sold $1M"}
    )
    dil_s1 = dilution_event(score=0.5).model_copy(
        update={"security_id": s1, "asof": date(2026, 6, 2), "label": "Shelf filed"}
    )
    sell_other = insider_sell_event().model_copy(
        update={"security_id": s_other, "asof": date(2026, 6, 2), "label": "Other member risk"}
    )
    down_s1 = breakdown_event(dearm_grade=Grade.CORE, asof=date(2026, 6, 5), security_id=s1)

    record_day(db, t1_v2, [warm1, sell_other], date(2026, 5, 29), run_kind="cron", config_hash="c1")
    record_day(
        db,
        t1_v2,
        [conv1, conf1],
        date(2026, 6, 1),
        ingest_fresh=True,
        ingest_errors=0,
        config_hash="c1",
        code_sha="sha1",
        run_kind="cron",
    )
    record_day(
        db,
        t1_v2,
        [conv1, conf1, sell_s1, dil_s1, sell_other],
        date(2026, 6, 2),
        config_hash="c1",
        run_kind="cron",
    )
    record_day(db, t1_v2, [conv1, conf1, down_s1, sell_other], date(2026, 6, 5), config_hash="c1")

    # Seed Thesis 2 (warming run -> withheld arm)
    warm2 = insider_event(security_id=s2, liveness=60).model_copy(update={"asof": date(2026, 6, 2)})
    record_day(db, t2, [], date(2026, 6, 1))
    record_day(db, t2, [warm2], date(2026, 6, 2))
    record_day(db, t2, [warm2], date(2026, 6, 4))
    record_day(db, t2, [], date(2026, 6, 8))

    # Seed Thesis 3 (armed + late re-version + reconstructed row)
    conv3, conf3 = keys_fired(s3, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    record_day(db, t3, [conv3], date(2026, 5, 29))
    record_day(
        db,
        t3,
        [conv3, conf3],
        date(2026, 6, 1),
        ingest_fresh=False,
        ingest_errors=2,
        run_kind="cron",
    )
    record_day(
        db,
        t3,
        [conv3],
        date(2026, 6, 1),
        recorded_at=datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc),  # late -> excluded
    )
    record_day(db, t3, [conv3], date(2026, 6, 3), reconstructed=True)  # excluded

    # Seed Form 4 fact row for thaw lag
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": s1,
            "accession": "0000000000-26-000001",
            "insider_name": "Buyer",
            "txn_code": "P",
            "valid_from": date(2026, 6, 1),
            "recorded_at": datetime(2026, 6, 2, 12, tzinfo=timezone.utc),
        },
    )

    # Seed operator decisions on Thesis 1
    decisions_repo.append(
        db,
        thesis_id=t1.id,
        action="take",
        decision_date=date(2026, 6, 1),
        tenant_id=DEFAULT_TENANT_ID,
        security_id=s1,
        price=100.0,
        call_state="armed",
        call_verdict="core_entry",
    )
    decisions_repo.append(
        db,
        thesis_id=t1.id,
        action="close",
        decision_date=date(2026, 6, 5),
        tenant_id=DEFAULT_TENANT_ID,
        security_id=s1,
        price=112.0,
    )

    # Seed prices for s1, s2, s3
    for d, px in (
        (date(2026, 6, 1), 100.0),
        (date(2026, 6, 2), 104.0),
        (date(2026, 6, 5), 112.0),
        (date(2026, 6, 8), 115.0),
    ):
        bar(db, s1, d, px, high=px + 1.0, low=px - 1.0)
        bar(db, s2, d, px / 2.0)
        bar(db, s3, d, px / 4.0)
    db.commit()

    asof = date(2026, 6, 10)
    known_at = datetime(
        2026, 6, 15, tzinfo=timezone.utc
    )  # between t1's 1-member & 2-member snapshots

    # 1. Exact equivalence: batched scoreboard_records vs individual derive_thesis_record
    batched_result, batched_timelines, batched_single = scoreboard_records(
        db, asof, known_at=known_at, workspace=DEFAULT_TENANT_ID
    )
    by_id = {t.thesis_id: t for t in batched_result.theses}
    assert by_id[t1.id].basket_size == 1  # PIT basket size at 06-15 (before 06-20 upsert)
    assert batched_single == {t2.id: s2, t3.id: s3}

    for thesis in thesis_repo.list_all(db, workspace=DEFAULT_TENANT_ID, include_archived=True):
        pit_size = thesis_repo.basket_size_asof(
            db, thesis.id, known_at, live_fallback=len(thesis.basket)
        )
        single_rec, single_snaps = derive_thesis_record(
            db, thesis, asof, known_at=known_at, basket_size=pit_size
        )
        assert by_id[thesis.id].model_dump() == single_rec.model_dump()
        assert [s.model_dump() for s in batched_timelines[thesis.id]] == [
            s.model_dump() for s in single_snaps
        ]

    # Verify selective Pass-2 risk_signals preserved exact risk_events and dearm_detail for t1
    (t1_ep,) = by_id[t1.id].episodes
    assert t1_ep.dearm_detail == down_s1.label
    assert [r.kind for r in t1_ep.risk_events] == [
        Kind.INSIDER_SELL,
        Kind.DILUTION_RISK,
        Kind.BREAKDOWN,
    ]
    assert all(r.security_id == s1 for r in t1_ep.risk_events)

    # 2. Query-count guard: O(1) queries on assemble_scoreboard + read_scoreboard (no per-thesis N+1,
    #    no duplicate market_tape_edge scan)
    counting = _CountingConn(db)
    resp = scoreboard_router.read_scoreboard(
        counting,
        asof=asof,
        workspace=DEFAULT_TENANT_ID,
        now=datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc),
    )
    assert resp.summary.n_theses == 3
    sqls = counting.sql
    assert sum(1 for q in sqls if "FROM basket_snapshot" in q) == 1
    assert sum(1 for q in sqls if "FROM operator_decision" in q) == 1
    assert sum(1 for q in sqls if "SELECT max(d) AS edge FROM fact_price_eod" in q) == 1
    assert sum(1 for q in sqls if "SELECT id, cik FROM security_master" in q) == 1
    assert sum(1 for q in sqls if "SELECT id, ticker FROM security_master" in q) == 1
    assert sum(1 for q in sqls if "SELECT id, name FROM security_master" in q) == 1
    # Pass-1 cards query runs once (not once per thesis) and strips unread keys in SQL
    pass1 = [q for q in sqls if "DISTINCT ON (thesis_id, asof)" in q and "FROM calls" in q]
    assert len(pass1) == 1
    assert "card - %s::text[]" in pass1[0]


def test_stored_edition_batch_equivalence_and_diff_query_counts(db, security_id):
    """Multi-thesis stored Edition >= 1: batched ``scoreboard_records(edition=ed)`` matches
    ``derive_thesis_record(..., source=EditionSource(ed))`` field-for-field, and ``edition_diff``
    batches page reads + price prefetch in O(1) queries."""
    s1 = security_id
    s2 = _extra_security(db, "ED2", "0000300002", "Edition Two Corp.")
    t1 = persist_thesis(db, s1, thesis_id=uuid.uuid4())
    t2 = persist_thesis(db, s2, thesis_id=uuid.uuid4())

    conv1, conf1 = keys_fired(s1, date(2026, 7, 1), conv_liveness=5, conf_liveness=5)
    down1 = breakdown_event(dearm_grade=Grade.CORE, asof=date(2026, 7, 3), security_id=s1)
    conv2, conf2 = keys_fired(s2, date(2026, 7, 2), conv_liveness=30, conf_liveness=30)

    pages = [
        assemble_call(t1, [conv1], date(2026, 6, 30), DEFAULT_CONFIG),
        assemble_call(t1, [conv1, conf1], date(2026, 7, 1), DEFAULT_CONFIG),
        assemble_call(t1, [conv1, conf1, down1], date(2026, 7, 3), DEFAULT_CONFIG),
        assemble_call(t2, [conv2], date(2026, 7, 1), DEFAULT_CONFIG),
        assemble_call(t2, [conv2, conf2], date(2026, 7, 2), DEFAULT_CONFIG),
        assemble_call(t2, [conv2, conf2], date(2026, 7, 6), DEFAULT_CONFIG),
    ]
    # Also record Edition 0 rows so edition_diff(None -> ed) has both sides differing on both theses
    record_day(db, t1, [conv1], date(2026, 6, 30))
    record_day(db, t1, [conv1, conf1], date(2026, 7, 1))
    record_day(db, t1, [conv1, conf1], date(2026, 7, 3))
    record_day(db, t2, [conv2], date(2026, 7, 1))
    record_day(db, t2, [conv2], date(2026, 7, 6))

    ed_id = editions_repo.publish_edition(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        number=1,
        label="initial re-run",
        causes=["initial"],
        engine={"fingerprint": "a" * 64, "config_hash": "b" * 64, "code_sha": "c" * 16},
        data_known_by=CUT,
        pages=pages,
        entries=[
            {
                "thesis_id": t1.id,
                "security_id": s1,
                "entry_date": date(2026, 7, 1),
                "estimated": True,
                "method": "first_pull",
            },
            {
                "thesis_id": t2.id,
                "security_id": s2,
                "entry_date": date(2026, 7, 1),
                "estimated": False,
                "method": "snapshot",
            },
        ],
        computed_at=CUT,
    )
    for d, px in (
        (date(2026, 7, 1), 100.0),
        (date(2026, 7, 2), 102.0),
        (date(2026, 7, 3), 105.0),
        (date(2026, 7, 6), 110.0),
    ):
        bar(db, s1, d, px)
        bar(db, s2, d, px / 2.0)

    # Ink t1's matured episode so one episode is inked and t2's episode is scored on read
    ed = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1)
    assert ed is not None and ed.id == ed_id
    src = EditionSource(ed)
    t1_single, _ = derive_thesis_record(db, t1, date(2026, 7, 10), source=src)
    (t1_ep,) = t1_single.episodes
    editions_repo.ink_outcomes(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        edition_id=ed.id,
        rows=[
            {
                "thesis_id": t1.id,
                "security_id": s1,
                "arm_date": t1_ep.episode.arm_date,
                "exit_by": t1_ep.episode.exit_by,
                "kind": "matured",
                "outcome": t1_ep.outcome,
                "night": date(2026, 7, 10),
                "prices_known_by": CUT,
            }
        ],
    )
    db.commit()

    asof = date(2026, 7, 10)
    counting = _CountingConn(db)
    assembled = assemble_scoreboard(counting, asof=asof, edition=ed, workspace=DEFAULT_TENANT_ID)
    by_id = {t.thesis_id: t for t in assembled.theses}

    for thesis in (t1, t2):
        single_rec, _ = derive_thesis_record(db, thesis, asof, source=src)
        assert by_id[thesis.id].model_dump() == single_rec.model_dump()

    assert by_id[t1.id].episodes[0].inked == "matured"
    assert by_id[t1.id].episodes[0].censored_start is True  # armed on estimated join night
    assert by_id[t1.id].episodes[0].dearm_detail == down1.label
    assert by_id[t2.id].episodes[0].inked is None

    # O(1) edition queries: 1 pages_for_theses, 1 edition_entry, 1 edition_outcome, 1 price prefetch
    assert (
        sum(1 for q in counting.sql if "FROM edition_call" in q and "jsonb_array_elements" not in q)
        == 1
    )
    assert sum(1 for q in counting.sql if "FROM edition_entry" in q) == 1
    assert sum(1 for q in counting.sql if "FROM edition_outcome" in q) == 1
    assert sum(1 for q in counting.sql if "SELECT max(d) AS edge FROM fact_price_eod" in q) == 1

    # Edition diff: O(1) queries for both sides + 1 price prefetch + batched master lookups at router
    diff_conn = _CountingConn(db)
    diff = edition_diff(
        diff_conn, before=None, after=ed, through=date(2026, 7, 6), workspace=DEFAULT_TENANT_ID
    )
    assert len(diff.theses) == 2
    assert sum(1 for q in diff_conn.sql if "FROM edition_call" in q) == 1
    assert (
        sum(1 for q in diff_conn.sql if "DISTINCT ON (thesis_id, asof)" in q and "FROM calls" in q)
        == 1
    )
    assert sum(1 for q in diff_conn.sql if "DISTINCT ON (security_id, d)" in q) == 1

    call_cache.CACHE.clear()
    router_diff_conn = _CountingConn(db)
    diff_resp = scoreboard_router.get_scoreboard_edition_diff(
        before_number=0, after_number=1, conn=router_diff_conn, current_tenant=DEFAULT_TENANT_ID
    )
    assert len(diff_resp.theses) == 2
    assert (
        sum(1 for q in router_diff_conn.sql if "SELECT id, ticker FROM security_master" in q) == 1
    )
    assert sum(1 for q in router_diff_conn.sql if "SELECT id, name FROM security_master" in q) == 1


# ---------------------------------------------------------------------------------------------------------
# 2.3 JSONB trimming & malformed-card fault isolation (Edition 0, Edition N, and edition_diff)
# ---------------------------------------------------------------------------------------------------------


def test_jsonb_trimming_strips_unread_keys_in_sql_without_losing_card_fidelity(db, security_id):
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    sell = insider_sell_event().model_copy(
        update={"security_id": security_id, "asof": date(2026, 6, 1)}
    )
    record_day(db, thesis, [conv, conf, sell], date(2026, 6, 1))

    trimmed = calls_repo.latest_records_for_theses(
        db,
        [thesis.id],
        upto=date(2026, 6, 5),
        include_reconstructed=False,
        drop_card_keys=SCOREBOARD_RECORD_DROP_CARD_KEYS,
    )[thesis.id]
    assert len(trimmed) == 1
    for dropped in SCOREBOARD_RECORD_DROP_CARD_KEYS:
        assert dropped not in trimmed[0].raw_card


def test_malformed_risk_signals_and_bogus_cards_stay_fault_isolated_across_batches(db, security_id):
    """In a multi-thesis batch (both Edition 0 and Edition N):
    - a top-level bogus card in thesis A -> thesis A errors, siblings score cleanly
    - a non-array ``risk_signals`` in thesis B -> thesis B errors, siblings score cleanly
    - a malformed ``TriggerRef`` inside ``risk_signals`` on an episode day in thesis C -> Pass-2 SQL
      does NOT crash the batch; thesis C errors during per-thesis validation, siblings score cleanly.
    """
    bad_top = persist_thesis(db, security_id, thesis_id=uuid.uuid4())
    bad_risk_type = persist_thesis(db, security_id, thesis_id=uuid.uuid4())
    bad_risk_elem = persist_thesis(db, security_id, thesis_id=uuid.uuid4())
    good = persist_thesis(db, security_id, thesis_id=uuid.uuid4())

    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    good_card = assemble_call(good, [conv, conf], date(2026, 6, 1), DEFAULT_CONFIG)
    record_day(db, good, [conv, conf], date(2026, 6, 1))

    # Build corrupted JSONB payloads from a real armed card
    non_array_card = good_card.model_dump(mode="json")
    non_array_card["thesis_id"] = str(bad_risk_type.id)
    non_array_card["risk_signals"] = "not-a-list"

    bad_elem_card = good_card.model_dump(mode="json")
    bad_elem_card["thesis_id"] = str(bad_risk_elem.id)
    bad_elem_card["risk_signals"] = [
        {
            "security_id": str(security_id),
            "bogus_field": 123,
        }  # matches episode sid, invalid TriggerRef
    ]

    stamp = on_time(date(2026, 6, 1))
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, recorded_at) "
            "VALUES (%s, %s, %s, 'armed', 'core_entry', '{\"bogus_key\": 1}'::jsonb, %s)",
            (DEFAULT_TENANT_ID, bad_top.id, date(2026, 6, 1), stamp),
        )
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, recorded_at) "
            "VALUES (%s, %s, %s, 'armed', 'core_entry', %s::jsonb, %s)",
            (
                DEFAULT_TENANT_ID,
                bad_risk_type.id,
                date(2026, 6, 1),
                json.dumps(non_array_card),
                stamp,
            ),
        )
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, recorded_at) "
            "VALUES (%s, %s, %s, 'armed', 'core_entry', %s::jsonb, %s)",
            (
                DEFAULT_TENANT_ID,
                bad_risk_elem.id,
                date(2026, 6, 1),
                json.dumps(bad_elem_card),
                stamp,
            ),
        )
    db.commit()

    # Edition 0 fault isolation
    res, _, _ = scoreboard_records(db, date(2026, 6, 5), workspace=DEFAULT_TENANT_ID)
    by_id = {t.thesis_id: t for t in res.theses}
    assert by_id[bad_top.id].error is not None and by_id[bad_top.id].episodes == []
    assert by_id[bad_risk_type.id].error is not None and by_id[bad_risk_type.id].episodes == []
    assert by_id[bad_risk_elem.id].error is not None and by_id[bad_risk_elem.id].episodes == []
    assert by_id[good.id].error is None and len(by_id[good.id].episodes) == 1

    # Stored Edition fault isolation + edition_diff fault isolation
    ed_id = editions_repo.publish_edition(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        number=1,
        label="fault isolation test",
        causes=["initial"],
        engine={"fingerprint": "1" * 64, "config_hash": "2" * 64, "code_sha": None},
        data_known_by=CUT,
        pages=[good_card],
        entries=[],
        computed_at=CUT,
    )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO edition_call "
            "(tenant_id, edition_id, thesis_id, asof, state, verdict, card, data_known_by, "
            " engine_fingerprint, page_kind, computed_at) "
            "VALUES (%s, %s, %s, %s, 'armed', 'core_entry', '{\"bogus\": 1}'::jsonb, %s, %s, 'rerun', %s)",
            (DEFAULT_TENANT_ID, ed_id, bad_top.id, date(2026, 6, 1), CUT, "1" * 64, CUT),
        )
        cur.execute(
            "INSERT INTO edition_call "
            "(tenant_id, edition_id, thesis_id, asof, state, verdict, card, data_known_by, "
            " engine_fingerprint, page_kind, computed_at) "
            "VALUES (%s, %s, %s, %s, 'armed', 'core_entry', %s::jsonb, %s, %s, 'rerun', %s)",
            (
                DEFAULT_TENANT_ID,
                ed_id,
                bad_risk_elem.id,
                date(2026, 6, 1),
                json.dumps(bad_elem_card),
                CUT,
                "1" * 64,
                CUT,
            ),
        )
    db.commit()

    ed = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1)
    assert ed is not None
    ed_res, _, _ = scoreboard_records(db, date(2026, 6, 5), edition=ed, workspace=DEFAULT_TENANT_ID)
    ed_by_id = {t.thesis_id: t for t in ed_res.theses}
    assert ed_by_id[bad_top.id].error is not None
    assert ed_by_id[bad_risk_elem.id].error is not None
    assert ed_by_id[good.id].error is None and len(ed_by_id[good.id].episodes) == 1

    diff = edition_diff(
        db, before=None, after=ed, through=date(2026, 6, 5), workspace=DEFAULT_TENANT_ID
    )
    diff_by_id = {t.thesis_id: t for t in diff.theses}
    assert diff_by_id[bad_top.id].error is not None
    assert diff_by_id[good.id].error is None
