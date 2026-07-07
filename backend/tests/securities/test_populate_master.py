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


# --- sec_tickers.load_all (the parse, exchange-file shape) ---


def _exchange_file(*rows: tuple) -> str:
    return json.dumps(
        {"fields": ["cik", "name", "ticker", "exchange"], "data": [list(r) for r in rows]}
    )


def test_load_all_pads_cik_keeps_dual_class_and_carries_exchange(tmp_path):
    """The whole universe as (cik, ticker, name, exchange) quadruples IN FILE ORDER: CIK zero-padded to 10
    digits, the PER-INSTRUMENT exchange carried (the ADR/foreign-ordinary discriminator), and a dual-class
    issuer (one CIK, two tickers) preserved as TWO rows — both must stay pickable."""
    (tmp_path / "company_tickers_exchange.json").write_text(
        _exchange_file(
            (320193, "Apple Inc.", "AAPL", "Nasdaq"),
            (1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"),
            (1652044, "Alphabet Inc.", "GOOG", "Nasdaq"),
            (937966, "ASML HOLDING NV", "ASML", "Nasdaq"),
            (937966, "ASML HOLDING NV", "ASMLF", "OTC"),
        ),
        encoding="utf-8",
    )
    rows = sec_tickers.load_all(cache_dir=tmp_path)
    assert ("0000320193", "AAPL", "Apple Inc.", "Nasdaq") in rows
    assert [t for c, t, _, _ in rows if c == "0001652044"] == ["GOOGL", "GOOG"]  # FILE ORDER kept
    assert ("0000937966", "ASMLF", "ASML HOLDING NV", "OTC") in rows  # per-instrument venue


# --- master.populate_universe (the (cik, ticker)-keyed upsert + the canonical-primary flag) ---


def _rows(db, *, tenant_id=DEFAULT_TENANT_ID, ticker=None):
    sql = (
        "SELECT id, cik, ticker, name, exchange, is_primary FROM security_master "
        "WHERE tenant_id = %s"
    )
    args: list = [tenant_id]
    if ticker is not None:
        sql += " AND ticker = %s"
        args.append(ticker)
    with db.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def test_populate_inserts_new_then_is_idempotent(db):
    rows = [
        ("0000320193", "AAPL", "Apple Inc.", "Nasdaq"),
        ("0001045810", "NVDA", "NVIDIA Corp", "Nasdaq"),
    ]
    assert master.populate_universe(db, rows) == {"inserted": 2, "updated": 0, "skipped": 0}
    db.commit()
    # re-run the identical universe -> a pure no-op (idempotent + additive, incl. exchange/is_primary)
    assert master.populate_universe(db, rows) == {"inserted": 0, "updated": 0, "skipped": 2}
    db.commit()
    assert len(_rows(db)) == 2
    assert all(r["is_primary"] is True for r in _rows(db))  # single-row CIKs are their own primary


def test_populate_flags_one_primary_per_cik(db):
    """A multi-sibling CIK gets EXACTLY one is_primary=True — the canonical instrument the resolvers pick:
    exchange splits the ADR pair (ASML over ASMLF/OTC), the derivative demotion splits the warrant pair on
    the SAME venue (KTTA over KTTAW), and the SEC file order breaks the dual-class tie (GOOGL — the ratified
    governance-primary proxy)."""
    master.populate_universe(
        db,
        [
            ("0001652044", "GOOGL", "Alphabet Inc.", "Nasdaq"),
            ("0001652044", "GOOG", "Alphabet Inc.", "Nasdaq"),
            ("0000937966", "ASML", "ASML HOLDING NV", "Nasdaq"),
            ("0000937966", "ASMLF", "ASML HOLDING NV", "OTC"),
            ("0001841330", "KTTA", "Pasithea Therapeutics", "Nasdaq"),
            ("0001841330", "KTTAW", "Pasithea Therapeutics", "Nasdaq"),
        ],
    )
    db.commit()
    flags = {r["ticker"]: r["is_primary"] for r in _rows(db)}
    assert flags == {
        "GOOGL": True,
        "GOOG": False,
        "ASML": True,
        "ASMLF": False,
        "KTTA": True,
        "KTTAW": False,
    }
    assert {r["ticker"]: r["exchange"] for r in _rows(db)}["ASMLF"] == "OTC"  # per-instrument venue


def test_populate_updates_identity_in_place_id_stable(db):
    master.populate_universe(db, [("0001326801", "META", "Facebook, Inc.", "Nasdaq")])
    db.commit()
    before = _rows(db, ticker="META")[0]
    counts = master.populate_universe(
        db, [("0001326801", "META", "Meta Platforms, Inc.", "Nasdaq")]
    )
    db.commit()
    assert counts == {"inserted": 0, "updated": 1, "skipped": 0}
    after = _rows(db, ticker="META")
    assert len(after) == 1  # NOT a new row
    assert after[0]["id"] == before["id"]  # SAME id -> the 8 FK fact tables don't orphan
    assert after[0]["name"] == "Meta Platforms, Inc."


def test_populate_reuses_seeded_id_and_backfills_the_flag(db):
    """Seed reconcile: a pre-existing (seed-style) fixed-id row matches on (cik, ticker) and is reused —
    never duplicated (which would orphan its facts) — and the populate pass BACKFILLS its exchange +
    is_primary (the migration window: a seeded row starts NULL, the next populate run is the backfill).
    """
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "0001822966", "SMR", "NuScale Power", date(2026, 1, 1)),
        )
    db.commit()
    counts = master.populate_universe(db, [("0001822966", "SMR", "NuScale Power", "NYSE")])
    db.commit()
    assert counts["inserted"] == 0 and counts["updated"] == 1  # the flag/exchange backfill
    rows = _rows(db, ticker="SMR")
    assert len(rows) == 1 and rows[0]["id"] == sid  # the seeded id, reused
    assert rows[0]["exchange"] == "NYSE" and rows[0]["is_primary"] is True
    # and the backfilled universe is a no-op on the next run
    assert master.populate_universe(db, [("0001822966", "SMR", "NuScale Power", "NYSE")]) == {
        "inserted": 0,
        "updated": 0,
        "skipped": 1,
    }


def test_populate_skips_rows_missing_cik_or_ticker(db):
    counts = master.populate_universe(
        db,
        [
            ("", "BAD", "no cik", None),
            ("0000000001", "", "no ticker", None),
            ("0000320193", "AAPL", "Apple", "Nasdaq"),
        ],
    )
    db.commit()
    assert counts["inserted"] == 1  # only the exact, complete row is written (INVARIANT #2)
    assert [r["ticker"] for r in _rows(db)] == ["AAPL"]


# --- reconciliation with resolve() (the other live master writer) ---


def _resolve(conn, ticker):
    return master.resolve(conn, ticker, figi_cache_dir=_FIGI, sec_cache_dir=_SEC, allow_live=False)


def test_resolve_then_populate_no_duplicate(db):
    sec = _resolve(db, "AAPL")  # resolve inserts the row (and sets cik 0000320193)
    counts = master.populate_universe(db, [("0000320193", "AAPL", "Apple Inc.", "Nasdaq")])
    db.commit()
    assert counts["inserted"] == 0  # the broadener finds resolve's row by (cik, ticker)
    rows = _rows(db, ticker="AAPL")
    assert len(rows) == 1 and rows[0]["id"] == sec.id


def test_populate_then_resolve_no_duplicate(db):
    master.populate_universe(db, [("0000320193", "AAPL", "Apple Inc.", "Nasdaq")])
    db.commit()
    _resolve(db, "AAPL")  # resolve finds the broadener's row by ticker -> no re-insert
    assert len(_rows(db, ticker="AAPL")) == 1
