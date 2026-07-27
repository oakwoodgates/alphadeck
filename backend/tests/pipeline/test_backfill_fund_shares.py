"""The Polygon historical backfill — real DB (the ``db`` fixture), stubbed polygon. The headline is
the COUNT-THE-TABLE idempotency gate (a re-run appends ZERO rows — the table count is the assertion,
because the bitemporal read dedups and a duplicate append would hide behind a correct read), plus the
no-lookahead stamping (``valid_from`` = the historical queried ``d``, ``recorded_at`` = now) and the
gap-skipped-visibly rule (#9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from ingest.funds.polygon import PolygonError
from pipeline.backfill_fund_shares import BackfillResult, backfill_dates, backfill_thesis

_END = date(2026, 7, 25)


class _StubPolygon:
    """A dated polygon stub: ``counts[d]`` -> the count (``None``/absent = the null-gap date);
    ``raise_on`` simulates a real failure (401-class) at one date. Records every dated call."""

    def __init__(self, counts, raise_on=None):
        self.counts, self.raise_on = counts, raise_on
        self.calls: list[tuple[str, date]] = []

    def get_snapshot_at(self, ticker, d, *, allow_live=False, force_refresh=False):
        self.calls.append((ticker, d))
        if self.raise_on is not None and d == self.raise_on:
            raise PolygonError(
                "polygon: HTTP 401 for https://api.polygon.io/... — bad POLYGON_API_KEY"
            )
        count = self.counts.get(d)
        if count is None:
            return None
        return {
            "d": d,
            "shares_out": count,
            "source": "polygon",
            "source_ref": f"https://api.polygon.io/v3/reference/tickers/{ticker}?date={d.isoformat()}",
        }


def _flat_counts(dates, count=138_000_000.0, skip=()):
    return {d: (None if d in skip else count) for d in dates}


def _add_master(db, *, ticker, tenant=DEFAULT_TENANT_ID, instrument_kind="etf") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, tenant, ticker, "2026-01-01", instrument_kind),
        )
    db.commit()
    return sid


def _make_thesis(db, members, *, tenant=DEFAULT_TENANT_ID) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, tenant, "Backfill thesis", "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, archetype, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), tenant, tid, i, ticker, "—", "fund", sid),
            )
    db.commit()
    return tid


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) n FROM fact_fund_shares")
        return cur.fetchone()["n"]


# --- backfill_dates (pure) ---------------------------------------------------------------------------


def test_backfill_dates_is_end_anchored_daily_and_oldest_first():
    ds = backfill_dates(_END, 4, 1)
    assert ds == [date(2026, 7, 21) + timedelta(days=i) for i in range(5)]  # end-4 … end inclusive


def test_backfill_dates_cadence_walks_back_from_the_end():
    ds = backfill_dates(_END, 7, 3)
    assert ds == [date(2026, 7, 19), date(2026, 7, 22), _END]  # the END is always sampled
    with pytest.raises(ValueError):
        backfill_dates(_END, 7, 0)


# --- the runner against the real DB ------------------------------------------------------------------


def test_backfill_lands_history_with_the_gap_skipped_and_provenance(db):
    sid = _add_master(db, ticker="URA")
    tid = _make_thesis(db, [("URA", sid)])
    dates = backfill_dates(_END, 4, 1)  # 5 dates
    stub = _StubPolygon(_flat_counts(dates, skip={date(2026, 7, 23)}))  # one null-gap hole

    results = backfill_thesis(db, tid, days=4, cadence=1, end=_END, polygon=stub)

    assert results == [
        BackfillResult("URA", sid, appended=4, reversioned=0, gaps_skipped=1, dates_queried=5)
    ]
    assert _count(db) == 4  # the gap date landed NO row — skipped, never a fake zero (#9)
    rows = as_of(
        db,
        "fact_fund_shares",
        security_id=sid,
        asof=_END,
        known_at=datetime(2100, 1, 1, tzinfo=timezone.utc),
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert [r["d"] for r in rows] == [d for d in dates if d != date(2026, 7, 23)]
    assert all(r["valid_from"] == r["d"] for r in rows)  # the historical QUERIED date (#1)
    assert all(r["source"] == "polygon" for r in rows)
    assert all(r["source_ref"].startswith("https://api.polygon.io/") for r in rows)  # #6


def test_backfill_rows_are_invisible_to_a_replay_pinned_before_the_run(db):
    """#1's transaction axis: recorded_at = now (never backdated), so a replay pinned before the
    backfill ran sees NONE of the history it added."""
    sid = _add_master(db, ticker="URA")
    tid = _make_thesis(db, [("URA", sid)])
    stub = _StubPolygon(_flat_counts(backfill_dates(_END, 3, 1)))
    backfill_thesis(db, tid, days=3, cadence=1, end=_END, polygon=stub)

    past = datetime(2026, 7, 1, tzinfo=timezone.utc)  # before the backfill's recorded_at
    assert (
        as_of(
            db,
            "fact_fund_shares",
            security_id=sid,
            asof=_END,
            known_at=past,
            tenant_id=DEFAULT_TENANT_ID,
        )
        == []
    )


def test_rerun_appends_zero_rows_count_the_table(db):
    """THE idempotency gate: the same walk re-run appends NOTHING — the table count is the assertion."""
    sid = _add_master(db, ticker="URA")
    tid = _make_thesis(db, [("URA", sid)])
    stub = _StubPolygon(_flat_counts(backfill_dates(_END, 4, 1)))
    backfill_thesis(db, tid, days=4, cadence=1, end=_END, polygon=stub)
    before = _count(db)

    results = backfill_thesis(db, tid, days=4, cadence=1, end=_END, polygon=stub)

    assert _count(db) == before  # the TABLE did not grow
    assert results[0].appended == 0 and results[0].reversioned == 0


def test_restated_count_reversions_not_duplicates(db):
    """A changed count for an already-stored d is a NEW version (the bitemporal native move) — reported
    loudly as reversioned, never a silent duplicate or an overwrite."""
    sid = _add_master(db, ticker="URA")
    tid = _make_thesis(db, [("URA", sid)])
    dates = backfill_dates(_END, 2, 1)
    backfill_thesis(db, tid, days=2, cadence=1, end=_END, polygon=_StubPolygon(_flat_counts(dates)))
    before = _count(db)

    restated = _flat_counts(dates)
    restated[_END] = 139_000_000.0  # polygon restated the newest date
    results = backfill_thesis(db, tid, days=2, cadence=1, end=_END, polygon=_StubPolygon(restated))

    assert results[0].appended == 0 and results[0].reversioned == 1
    assert _count(db) == before + 1  # one new VERSION row; history keeps both


def test_non_etf_members_are_never_walked_and_ticker_filters(db):
    equity = _add_master(db, ticker="CCJ", instrument_kind="equity")
    ura = _add_master(db, ticker="URA")
    urnm = _add_master(db, ticker="URNM")
    tid = _make_thesis(db, [("CCJ", equity), ("URA", ura), ("URNM", urnm)])
    dates = backfill_dates(_END, 2, 1)
    stub = _StubPolygon(_flat_counts(dates))

    results = backfill_thesis(db, tid, days=2, cadence=1, end=_END, ticker="URNM", polygon=stub)

    assert [r.ticker for r in results] == [
        "URNM"
    ]  # the equity is not part of this run; URA filtered
    assert {t for t, _ in stub.calls} == {"URNM"}  # the walk never spent a call on the others


def test_a_real_failure_stops_that_members_walk_and_is_captured(db):
    """A 401-class failure mid-walk: the dates already landed stay committed, the member's walk stops
    (no point spending ninety more spaced calls into a bad key), the error is VISIBLE on the result,
    and no exception escapes the run."""
    sid = _add_master(db, ticker="URA")
    tid = _make_thesis(db, [("URA", sid)])
    dates = backfill_dates(_END, 4, 1)  # 5 dates
    stub = _StubPolygon(_flat_counts(dates), raise_on=dates[2])

    results = backfill_thesis(db, tid, days=4, cadence=1, end=_END, polygon=stub)

    r = results[0]
    assert r.appended == 2 and r.dates_queried == 3  # stopped AT the failing date
    assert r.error is not None and "HTTP 401" in r.error
    assert _count(db) == 2  # partial progress kept (per-date commits)


def test_end_is_capped_at_today_never_future_dated(db):
    sid = _add_master(db, ticker="URA")
    tid = _make_thesis(db, [("URA", sid)])
    future = date.today() + timedelta(days=10)
    stub = _StubPolygon(_flat_counts(backfill_dates(date.today(), 3, 1)))

    backfill_thesis(db, tid, days=3, cadence=1, end=future, polygon=stub)

    assert max(d for _, d in stub.calls) <= date.today()  # no future-dated sample (#1)


def test_without_a_key_the_backfill_refuses_loudly(db, monkeypatch):
    sid = _add_master(db, ticker="URA")
    tid = _make_thesis(db, [("URA", sid)])
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="POLYGON_API_KEY is not set"):
            backfill_thesis(db, tid, days=3, cadence=1, end=_END)
    finally:
        get_settings.cache_clear()
