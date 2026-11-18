"""Shared fixtures for the V2 W6a counting-catalyst tests (a card with two members and one calendar row)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.thesis import BasketMember, Catalyst, Thesis
from repositories import thesis_repo


def card(
    db: psycopg.Connection, *, workspace: uuid.UUID = DEFAULT_TENANT_ID, n_members: int = 2
) -> tuple[uuid.UUID, list[uuid.UUID], uuid.UUID]:
    """A card in ``workspace`` with ``n_members`` companies and one calendar row typed 90 days before a fixed
    date. Returns ``(thesis_id, member ids, entry_id)``."""
    sids = [uuid.uuid4() for _ in range(n_members)]
    with db.cursor() as cur:
        for sid in sids:
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    sid,
                    DEFAULT_TENANT_ID,
                    f"W6A{sid.hex[:6]}",
                    f"{sid.int % 10**10:010d}",
                    date(2026, 1, 1),
                ),
            )
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=workspace,
        name="a card",
        narrative="n",
        basket=[
            BasketMember(ticker=f"W6A{i}", role="r", security_id=s) for i, s in enumerate(sids)
        ],
    )
    thesis_repo.upsert(db, thesis)
    entry = uuid.uuid4()
    thesis_repo.set_catalysts(
        db,
        thesis.id,
        [Catalyst(id=entry, label="DOE loan", kind="regulatory", when_date=date(2026, 3, 1))],
        tenant_id=workspace,
    )
    return thesis.id, sids, entry


def insert_version(
    db: psycopg.Connection,
    thesis_id: uuid.UUID,
    entry_id: uuid.UUID,
    *,
    at: datetime,
    valid_from: date,
    counts: bool = True,
    security_ids: list[uuid.UUID] | None = None,
    grade: str = "flip",
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    label: str = "DOE loan",
) -> uuid.UUID:
    """A TEST-ONLY version at a fixed instant (the precedent of the other public-clock tests): the sole writer
    stamps the database's ``now()``, which a test cannot place at a night boundary."""
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO fact_thesis_catalyst
                   (tenant_id, thesis_id, entry_id, counts, security_ids, grade, label, citation, source,
                    entered_at, valid_from, recorded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'https://example.com/doe', 'thesis_catalyst', %s, %s, %s)
               RETURNING id""",
            (
                tenant_id,
                thesis_id,
                entry_id,
                counts,
                security_ids or [],
                grade,
                label,
                at,
                valid_from,
                at,
            ),
        )
        return cur.fetchone()["id"]


def count(db: psycopg.Connection) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_thesis_catalyst")
        return cur.fetchone()["n"]
