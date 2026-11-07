"""The fund radar's card inputs, read from the database: every live card as the matcher wants it (its term set and
its COMPANY members). Shared by the CLI report and the API routes so the two never disagree. Display only; never
imports ``calls/``."""

from __future__ import annotations

from uuid import UUID

import psycopg

from domain.scopes import DATA_TENANT_ID, WorkspaceScope
from domain.thesis import ETFS_LABEL, Thesis
from radar.fund_match import CardInput, CompanyRef
from repositories import thesis_repo
from securities import master


def card_inputs(
    conn: psycopg.Connection, theses: list[Thesis], *, tenant_id: UUID = DATA_TENANT_ID
) -> list[CardInput]:
    """The given theses as matcher inputs: term set + COMPANY members (basket rows whose master row is not a
    fund), each labeled with its link. A row with no master row counts as a company. ``tenant_id`` is the DATA
    tenant the master is read from."""
    sids = [m.security_id for t in theses for m in t.basket if m.security_id]
    secs = master.get_many(conn, sids, tenant_id=tenant_id)
    cards: list[CardInput] = []
    for t in theses:
        companies: list[CompanyRef] = []
        seen: set[tuple[str, str | None]] = set()
        for m in t.basket:
            sec = secs.get(m.security_id) if m.security_id else None
            if sec is not None and sec.instrument_kind == "etf":
                continue
            if sec is None and m.segment == ETFS_LABEL:
                continue
            key = (m.ticker, m.segment)
            if key in seen:
                continue
            seen.add(key)
            companies.append(CompanyRef(m.ticker, sec.name if sec else None, m.segment))
        cards.append(CardInput(str(t.id), t.name, list(t.term_set), companies))
    return cards


def load_cards(conn: psycopg.Connection, *, workspace: WorkspaceScope) -> list[CardInput]:
    """Every live card of ``workspace`` as matcher input (the API passes the caller's workspace; the CLI report
    passes the every-workspace sentinel)."""
    return card_inputs(conn, thesis_repo.list_all(conn, workspace=workspace))
