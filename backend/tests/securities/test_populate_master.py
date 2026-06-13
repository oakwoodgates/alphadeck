from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

from db.session import DEFAULT_TENANT_ID
from securities import master, sec_tickers

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_FIGI = _FIXTURES / "figi"
_SEC = _FIXTURES / "sec"


# --- sec_tickers.load_all (the parse) ---


def test_load_all_pads_cik_and_keeps_dual_class(tmp_path):
    """The whole universe as (cik, ticker, name) triples: CIK zero-padded to 10 digits, and a dual-class
    issuer (one CIK, two tickers) is preserved as TWO triples — both must stay pickable."""
    (tmp_path / "company_tickers.json").write_text(
        json.dumps(
            {
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
                "2": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
            }
        ),
        encoding="utf-8",
    )
    rows = sec_tickers.load_all(cache_dir=tmp_path)
    assert ("0000320193", "AAPL", "Apple Inc.") in rows
    assert sorted(t for c, t, _ in rows if c == "0001652044") == ["GOOG", "GOOGL"]


# --- master.populate_universe (the (cik, ticker)-keyed upsert) ---


def _rows(db, *, tenant_id=DEFAULT_TENANT_ID, ticker=None):
    sql = "SELECT id, cik, ticker, name FROM security_master WHERE tenant_id = %s"
    args: list = [tenant_id]
    if ticker is not None:
        sql += " AND ticker = %s"
        args.append(ticker)
    with db.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def test_populate_inserts_new_then_is_idempotent(db):
    rows = [("0000320193", "AAPL", "Apple Inc."), ("0001045810", "NVDA", "NVIDIA Corp")]
    assert master.populate_universe(db, rows) == {"inserted": 2, "updated": 0, "skipped": 0}
    db.commit()
    # re-run the identical universe -> a pure no-op (idempotent + additive)
    assert master.populate_universe(db, rows) == {"inserted": 0, "updated": 0, "skipped": 2}
    db.commit()
    assert len(_rows(db)) == 2


def test_populate_keeps_dual_class_as_two_rows(db):
    master.populate_universe(
        db,
        [("0001652044", "GOOGL", "Alphabet Inc."), ("0001652044", "GOOG", "Alphabet Inc.")],
    )
    db.commit()
    assert sorted(r["ticker"] for r in _rows(db)) == ["GOOG", "GOOGL"]  # one CIK, both pickable


def test_populate_updates_name_in_place_id_stable(db):
    master.populate_universe(db, [("0001326801", "META", "Facebook, Inc.")])
    db.commit()
    before = _rows(db, ticker="META")[0]
    counts = master.populate_universe(db, [("0001326801", "META", "Meta Platforms, Inc.")])
    db.commit()
    assert counts == {"inserted": 0, "updated": 1, "skipped": 0}
    after = _rows(db, ticker="META")
    assert len(after) == 1  # NOT a new row
    assert after[0]["id"] == before["id"]  # SAME id -> the 8 FK fact tables don't orphan
    assert after[0]["name"] == "Meta Platforms, Inc."


def test_populate_reuses_seeded_id_never_duplicates(db):
    """Seed reconcile: a pre-existing (seed-style) fixed-id row matches on (cik, ticker) and is reused, so
    a populate run never duplicates it (which would orphan its facts)."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "0001822966", "SMR", "NuScale Power", date(2026, 1, 1)),
        )
    db.commit()
    counts = master.populate_universe(db, [("0001822966", "SMR", "NuScale Power")])
    db.commit()
    assert counts["inserted"] == 0
    rows = _rows(db, ticker="SMR")
    assert len(rows) == 1 and rows[0]["id"] == sid  # the seeded id, reused


def test_populate_skips_rows_missing_cik_or_ticker(db):
    counts = master.populate_universe(
        db,
        [("", "BAD", "no cik"), ("0000000001", "", "no ticker"), ("0000320193", "AAPL", "Apple")],
    )
    db.commit()
    assert counts["inserted"] == 1  # only the exact, complete row is written (INVARIANT #2)
    assert [r["ticker"] for r in _rows(db)] == ["AAPL"]


# --- reconciliation with resolve() (the other live master writer) ---


def _resolve(conn, ticker):
    return master.resolve(conn, ticker, figi_cache_dir=_FIGI, sec_cache_dir=_SEC, allow_live=False)


def test_resolve_then_populate_no_duplicate(db):
    sec = _resolve(db, "AAPL")  # resolve inserts the row (and sets cik 0000320193)
    counts = master.populate_universe(db, [("0000320193", "AAPL", "Apple Inc.")])
    db.commit()
    assert counts["inserted"] == 0  # the broadener finds resolve's row by (cik, ticker)
    rows = _rows(db, ticker="AAPL")
    assert len(rows) == 1 and rows[0]["id"] == sec.id


def test_populate_then_resolve_no_duplicate(db):
    master.populate_universe(db, [("0000320193", "AAPL", "Apple Inc.")])
    db.commit()
    _resolve(db, "AAPL")  # resolve finds the broadener's row by ticker -> no re-insert
    assert len(_rows(db, ticker="AAPL")) == 1
