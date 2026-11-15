"""V2 W4a: the per-company CARD COUNT (``platform_values.cards.card_counts``), read across every workspace.

The steward's queue orders companies by how many live cards hold them, pen-only companies last (approved B3, B5).
The count is the ONE read in W4 that crosses workspaces, so it answers two numbers per company and nothing else:
how many live (non-archived) cards hold the company in any basket row, and whether EVERY one of those rows is the
Discovered link (approved B5 (a)). No thesis id, name, workspace or user leaves it. W3's backfill reuses it.

Two workspaces (A, B), real basket rows, an archived card, the Discovered pen, a row with no link at all, and one
card holding a company twice.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from domain.scopes import DATA_TENANT_ID
from platform_values.cards import CardCount, card_counts
from tests.workspaces import new_workspace


def _company(db, ticker: str) -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", date(2026, 1, 1)),
        )
    return sid


def _card(db, workspace: UUID, rows: list[tuple[UUID, str | None]], *, archived: bool = False):
    """One card in ``workspace`` holding ``rows`` = (company, link label or None)."""
    tid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) "
            "VALUES (%s, %s, %s, 'a secret narrative', CASE WHEN %s THEN now() END)",
            (tid, workspace, f"secret card {tid.hex[:6]}", archived),
        )
        for i, (sid, segment) in enumerate(rows):
            cur.execute(
                "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id, segment) "
                "VALUES (%s, %s, %s, 'T', 'the name', %s, %s)",
                (workspace, tid, i, sid, segment),
            )
    return tid


def test_the_count_crosses_workspaces_and_skips_archived_cards(db):
    w_a, w_b = new_workspace(db, "A"), new_workspace(db, "B")
    x, y, z, w, v, q, nobody = (_company(db, t) for t in ("X", "Y", "Z", "W", "V", "Q", "NONE"))
    _card(db, w_a, [(x, "Enrichment"), (y, "Discovered"), (z, "Discovered")])
    _card(db, w_a, [(x, "Fuel"), (w, "Discovered"), (w, None)])
    _card(db, w_b, [(x, "Discovered"), (z, "Reactors"), (v, "Bench"), (v, "Reactors")])
    _card(db, w_b, [(x, "Fuel"), (y, "Fuel"), (q, "Fuel")], archived=True)
    db.commit()

    got = card_counts(db, [x, y, z, w, v, q, nobody])

    assert got == {
        x: CardCount(
            cards=3, pen_only=False
        ),  # two of A's, one of B's; B's archived card does not count
        y: CardCount(
            cards=1, pen_only=True
        ),  # only in A's Discovered pen (its archived real link is gone)
        z: CardCount(cards=2, pen_only=False),  # A's pen, but a real link on B's card
        w: CardCount(
            cards=1, pen_only=False
        ),  # a Discovered row AND a row with no link: not pen-only
        v: CardCount(cards=1, pen_only=False),  # one card holding it twice counts once
    }
    # a company on no live card (only an archived one, or none) is absent: the caller decides what that means
    assert q not in got and nobody not in got


def test_the_answer_is_two_numbers_per_company_and_nothing_else(db):
    w_a = new_workspace(db, "A")
    x = _company(db, "X")
    _card(db, w_a, [(x, "Discovered")])
    db.commit()
    got = card_counts(db, [x])
    assert list(got) == [x]
    assert CardCount._fields == ("cards", "pen_only")
    assert type(got[x].cards) is int and type(got[x].pen_only) is bool
    assert card_counts(db, []) == {}
