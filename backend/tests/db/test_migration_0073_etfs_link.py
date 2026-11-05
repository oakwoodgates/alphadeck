"""Migration 0073 — every stored fund row moves into the ETFs link, and the live call's roster follows.

The runner applies each migration ONCE, so (the 0035 precedent) these re-execute the FILE's SQL over rows written
the way the old "Surface an ETF" left them (a fund with no link, in Discovered, in Context with a role). Proven:
funds move to ETFs and nothing else about any row changes; a moved card gets ONE new roster snapshot in the exact
shape the repo writes (so ``get_asof(now)`` — what the live call reads — sees the move, and a later promote of the
same roster appends nothing); an untouched card gets none; a re-run is a no-op (COUNT THE TABLE); and the two
refusals RAISE and change nothing: the same fund twice on one card, and a non-fund in a link named like ETFs.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.thesis import BasketMember, Segment, Thesis
from repositories import thesis_repo

_SQL = (MIGRATIONS_DIR / "0073_etfs_link.sql").read_text(encoding="utf-8")


def _security(db, ticker: str, kind: str = "equity") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, kind, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _card(db, name: str, basket: list[BasketMember], segments: list[str]) -> uuid.UUID:
    """A card saved through the REPO (no promote rule) — the shape the old write left on disk."""
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="n",
        segments=[Segment(label=s) for s in segments],
        basket=basket,
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t.id


def _m(ticker, sid, segment, **kw) -> BasketMember:
    kw.setdefault("role", "r")
    return BasketMember(ticker=ticker, security_id=sid, segment=segment, **kw)


def _rows(db, tid) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT ordinal, ticker, segment, role, thesis_fit, authored_by, signed_off, "
            "conviction, context_role FROM basket_member WHERE thesis_id = %s ORDER BY ordinal",
            (tid,),
        )
        return cur.fetchall()


def _snapshots(db, tid) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM basket_snapshot WHERE thesis_id = %s", (tid,))
        return cur.fetchone()["n"]


def _run(db) -> None:
    with db.cursor() as cur:
        cur.execute(_SQL)
    db.commit()


@pytest.fixture
def cards(db):
    fa, fb, fc = (_security(db, t, "etf") for t in ("URA", "LIT", "SMH"))
    co = _security(db, "QZRA")
    moved = _card(
        db,
        "funds scattered",
        [
            _m("QZRA", co, "Reactors", thesis_fit="why", conviction=3, signed_off=True),
            _m("URA", fa, None, role="ETF sleeve", signed_off=True),
            _m("LIT", fb, "Discovered"),
            _m("SMH", fc, "Context", context_role=ContextRole.BENCHMARK),
        ],
        ["Reactors", "Discovered"],
    )
    untouched = _card(db, "no funds", [_m("QZRA", co, "Reactors")], ["Reactors"])
    return {"moved": moved, "untouched": untouched, "fund_ids": (fa, fb, fc), "co": co}


def test_every_fund_row_moves_to_etfs_and_nothing_else_changes(db, cards):
    tid = cards["moved"]
    before = _rows(db, tid)
    _run(db)
    after = _rows(db, tid)
    assert [(r["ticker"], r["segment"]) for r in after] == [
        ("QZRA", "Reactors"),
        ("URA", "ETFs"),
        ("LIT", "ETFs"),
        ("SMH", "ETFs"),
    ]
    # every other column is byte-identical (the context role on the Context fund included)
    strip = [{k: v for k, v in r.items() if k != "segment"} for r in before]
    assert [{k: v for k, v in r.items() if k != "segment"} for r in after] == strip
    assert after[3]["context_role"] == "benchmark"
    assert len(after) == len(before)  # COUNT THE TABLE: no row appears or vanishes


def test_a_moved_card_gets_one_snapshot_the_live_call_reads_and_an_untouched_card_none(db, cards):
    moved, untouched = cards["moved"], cards["untouched"]
    snaps = (_snapshots(db, moved), _snapshots(db, untouched))
    _run(db)
    assert (_snapshots(db, moved), _snapshots(db, untouched)) == (snaps[0] + 1, snaps[1])

    # the live call reads the latest roster snapshot (get_asof(now)) — it sees every fund in ETFs
    live = thesis_repo.get_asof(db, moved, None)
    assert {m.ticker: m.segment for m in live.basket} == {
        "QZRA": "Reactors",
        "URA": "ETFs",
        "LIT": "ETFs",
        "SMH": "ETFs",
    }
    assert live.basket == thesis_repo.get(db, moved).basket  # byte-identical to the live rows

    # the snapshot is in the repo's own shape: re-saving the same roster dedups against it (no new row)
    thesis_repo.upsert(db, thesis_repo.get(db, moved))
    db.commit()
    assert _snapshots(db, moved) == snaps[0] + 1


def test_a_rerun_moves_nothing_and_writes_nothing(db, cards):
    _run(db)
    rows = _rows(db, cards["moved"])
    snaps = _snapshots(db, cards["moved"])
    _run(db)
    assert _rows(db, cards["moved"]) == rows
    assert _snapshots(db, cards["moved"]) == snaps


def test_the_same_fund_twice_on_one_card_raises_and_changes_nothing(db, cards):
    fa = cards["fund_ids"][0]
    dup = _card(
        db, "a fund twice", [_m("URA", fa, None), _m("URA", fa, "Discovered")], ["Discovered"]
    )
    before = {tid: _rows(db, tid) for tid in (dup, cards["moved"])}
    with pytest.raises(psycopg.errors.RaiseException, match="same fund in more than one row"):
        _run(db)
    db.rollback()
    assert {
        tid: _rows(db, tid) for tid in (dup, cards["moved"])
    } == before  # nothing moved anywhere


def test_a_non_fund_in_a_link_named_like_etfs_raises_and_changes_nothing(db, cards):
    odd = _card(db, "a real link named etfs", [_m("QZRA", cards["co"], "etfs")], ["etfs"])
    before = {tid: _rows(db, tid) for tid in (odd, cards["moved"])}
    with pytest.raises(psycopg.errors.RaiseException, match="not a fund sits in a link named ETFs"):
        _run(db)
    db.rollback()
    assert {tid: _rows(db, tid) for tid in (odd, cards["moved"])} == before


def test_a_fund_in_a_lowercase_etfs_link_is_respelled(db, cards):
    fa = cards["fund_ids"][0]
    tid = _card(db, "fund in etfs", [_m("URA", fa, "etfs")], ["etfs"])
    _run(db)
    assert [r["segment"] for r in _rows(db, tid)] == ["ETFs"]
