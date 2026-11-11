"""The dashboards' cache against its REAL token reads (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7):
the two levels and the per-company change stamp, on the test Postgres.

- SOFT (a basket snapshot, a card): page entries clear, pieces survive (the next compute reads NO company);
- a company's STAMP: page entries clear and that company's pieces only drop (the next compute reads exactly it);
- a BENCHMARK's stamp: every piece drops;
- HARD (a ``data_repair`` row, a new cron run): everything clears;
- a compute during which a stamp moves stores nothing;
- the end-to-end gap the stamp closes: a page computed, then a company's data written and its company lock
  released, then the next read recomputes and shows the new data.

The pieces here are one test kind (``"bars"``: the company's stored bar count, read from the REAL table) so each
read is COUNTED. The only stand-in is the cron run identity (a run artifact on disk, not the database).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from psycopg.types.json import Json

from dashboards import cache as cache_mod
from dashboards.cache import DashboardCache, get_piece, put_piece
from db.change_stamp import mark_changed
from db.session import DEFAULT_TENANT_ID, connect
from db.write_lock import acquire_company_lock
from ingest.prices.eod_loader import ingest_prices
from repositories import data_repair_repo

D = date(2026, 6, 1)
W = DEFAULT_TENANT_ID


@pytest.fixture
def run(monkeypatch):
    """The newest cron run's identity, movable (it is a run artifact on disk, not a database row)."""
    state = {"v": ("2026-06-01T02:30:00+00:00", "finished", "x")}
    monkeypatch.setattr(cache_mod, "latest_run_identity", lambda: state["v"])
    return state


@pytest.fixture
def writer(db):
    c = connect()
    yield c
    c.close()


def _company(conn, ticker: str, *, kind: str = "equity") -> uuid.UUID:
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, kind, date(2020, 1, 1)),
        )
    conn.commit()
    return sid


def _bar(d: date, close: float) -> dict:
    return {"d": d, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


class Page:
    """A page whose compute reads one piece per company (the company's stored bar count), counting every
    company it had to READ (a piece miss)."""

    def __init__(self, conn, ids):
        self.conn, self.ids, self.reads = conn, list(ids), []

    def __call__(self):
        out = {}
        for sid in self.ids:
            found, value = get_piece("bars", sid, D, "public", None)
            if not found:
                self.reads.append(sid)
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (sid,)
                    )
                    value = cur.fetchone()["n"]
                put_piece("bars", sid, D, "public", None, value)
            out[sid] = value
        return out


def _get(cache, conn, page, slug="page"):
    return cache.get(conn, slug, (), D, page, workspace=W)


def _card(conn) -> uuid.UUID:
    tid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "a card", "a narrative"),
        )
    conn.commit()
    return tid


def _snapshot(conn, tid) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
            "VALUES (gen_random_uuid(), %s, %s, clock_timestamp(), %s, md5(random()::text))",
            (DEFAULT_TENANT_ID, tid, Json([])),
        )
    conn.commit()


def test_a_SOFT_move_clears_the_pages_and_keeps_every_piece(db, run):
    a, b = _company(db, "AAA"), _company(db, "BBB")
    tid = _card(db)
    cache, page = DashboardCache(), Page(db, [a, b])
    assert _get(cache, db, page).status == "computed" and page.reads == [a, b]
    assert _get(cache, db, page).status == "hit"
    _snapshot(db, tid)  # a roster edit
    again = _get(cache, db, page)
    assert again.status == "computed"  # the page entry was cleared...
    assert page.reads == [a, b]  # ...and no company was read again
    assert cache.piece_count() == 2
    with db.cursor() as cur:  # the meta index switch is soft too
        cur.execute("UPDATE thesis SET meta_index = true WHERE id = %s", (tid,))
    db.commit()
    assert _get(cache, db, page).status == "computed" and page.reads == [a, b]


def test_one_companys_STAMP_drops_its_pieces_only_and_clears_the_pages(db, run):
    a, b, c = (_company(db, t) for t in ("AAA", "BBB", "CCC"))
    cache, page = DashboardCache(), Page(db, [a, b, c])
    _get(cache, db, page)
    other = Page(db, [b])
    assert _get(cache, db, other, "other").status == "computed" and other.reads == []
    mark_changed(db, [b])
    db.commit()
    again = _get(cache, db, page)
    assert again.status == "computed"
    assert page.reads == [a, b, c, b]  # exactly the changed company was read again
    assert _get(cache, db, other, "other").status == "computed"  # every page entry went
    assert other.reads == []  # and it found b's fresh piece
    assert cache.piece_count() == 3


def test_a_BENCHMARKS_stamp_drops_every_piece(db, run):
    a, b = _company(db, "AAA"), _company(db, "BBB")
    bench = _company(db, "SPY", kind="etf")
    cache, page = DashboardCache(), Page(db, [a, b])
    _get(cache, db, page)
    mark_changed(db, [bench])
    db.commit()
    _get(cache, db, page)
    assert page.reads == [a, b, a, b]


def test_a_company_not_on_any_page_still_clears_the_pages_but_keeps_the_pieces(db, run):
    a, z = _company(db, "AAA"), _company(db, "ZZZ")
    cache, page = DashboardCache(), Page(db, [a])
    _get(cache, db, page)
    mark_changed(db, [z])
    db.commit()
    assert _get(cache, db, page).status == "computed" and page.reads == [a]


def test_a_DATA_REPAIR_clears_everything(db, run):
    a = _company(db, "AAA")
    cache, page = DashboardCache(), Page(db, [a])
    _get(cache, db, page)
    data_repair_repo.journal(db, tool="a repair", summary="fixed rows", rows_changed=1)
    assert _get(cache, db, page).status == "computed"
    assert page.reads == [a, a]


def test_a_NEW_CRON_RUN_clears_everything(db, run):
    a = _company(db, "AAA")
    cache, page = DashboardCache(), Page(db, [a])
    _get(cache, db, page)
    run["v"] = ("2026-06-02T02:30:00+00:00", "running", None)
    assert _get(cache, db, page).status == "computed"
    assert page.reads == [a, a]


def test_a_compute_during_which_a_STAMP_moves_stores_nothing(db, run, writer):
    a, b = _company(db, "AAA"), _company(db, "BBB")
    cache, page = DashboardCache(), Page(db, [a, b])

    def moving():
        got = page()
        mark_changed(writer, [b])  # another session's write lands mid-compute
        writer.commit()
        return got

    got = cache.get(db, "page", (), D, moving, workspace=W)
    assert got.status == "computed"
    assert len(cache) == 0 and cache.piece_count() == 0  # neither the page nor a staged piece
    assert _get(cache, db, page).status == "computed"
    assert page.reads == [a, b, a, b]


def test_the_GAP_a_companys_write_then_its_lock_release_reaches_the_next_read(db, run, writer):
    """Problem 1, end to end: a page is computed; another writer appends this company's bars under its company
    lock, commits, releases; the next read of the page recomputes that company and shows the new data.
    """
    a, b = _company(db, "AAA"), _company(db, "BBB")
    cache, page = DashboardCache(), Page(db, [a, b])
    first = _get(cache, db, page)
    assert first.value == {a: 0, b: 0}
    assert _get(cache, db, page).status == "hit"

    lock = acquire_company_lock(writer, a, wait_s=0)
    with lock:
        assert ingest_prices(writer, a, [_bar(date(2026, 5, 29), 10.0)]) == 1
        writer.commit()  # the write commits under the lock; the release moves the stamp

    again = _get(cache, db, page)
    assert again.status == "computed"
    assert again.value == {a: 1, b: 0}  # the new data, for that company
    assert page.reads == [a, b, a]  # and only that company was read again


def test_an_unchanged_token_stays_a_hit(db, run):
    a = _company(db, "AAA")
    mark_changed(db, [a])
    db.commit()
    cache, page = DashboardCache(), Page(db, [a])
    _get(cache, db, page)
    for _ in range(3):
        assert _get(cache, db, page).status == "hit"
    assert page.reads == [a]
