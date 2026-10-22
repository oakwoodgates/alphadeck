"""The Scoreboard EDITIONS store (editions S3, migration 0063) — the one reader and writer of the four tables.

Edition 0 is the original record, the ``calls`` log, and is never stored: this repo only ever holds Edition
1+. Everything here is append-only (the ``no_update`` trigger on every table); ``publish_edition`` writes one
whole edition (its row, its join dates, its pages, its ``published`` event) inside the CALLER's transaction,
and REFUSES — before writing a row — when the tenant already has that edition number or an edition at that
same cut. The unique constraints are the race guard behind that check.

Self-contained on purpose: nothing outside ``editions/`` may import ``editions`` (the import-graph test), and
the Scoreboard API reads editions through this module with psycopg alone (the lean API image has no
duckdb). The publisher (``editions.run publish``) hands join dates over as plain dicts in
``editions.entry.Entry.as_json`` shape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.call import CallCard
from repositories.mappers import call_to_row, row_to_call

EDITION_0_LABEL = "the original record"
CAUSES = ("initial", "rule_change", "data_repair", "code_drift")
PAGE_KINDS = ("rerun", "nightly", "catchup")


class EditionExists(Exception):
    """The tenant already holds this edition (same number, or the same cut). Nothing was written."""


@dataclass(frozen=True)
class Edition:
    """One stored edition, with the read-side counts the Scoreboard shows beside its label."""

    id: UUID
    tenant_id: UUID
    number: int
    parent_id: UUID | None
    label: str
    causes: tuple[str, ...]
    reason: dict[str, Any]
    engine_fingerprint: str
    config_hash: str
    code_sha: str | None
    data_known_by: datetime
    first_night: date
    created_at: datetime
    last_night: date | None = None
    n_pages: int = 0
    n_theses: int = 0
    n_entries: int = 0
    n_entries_estimated: int = 0
    closed_at: datetime | None = None


@dataclass(frozen=True)
class JoinDate:
    """One frozen join date (``edition_entry``): when the edition takes the company to have joined."""

    thesis_id: UUID
    security_id: UUID
    entry_date: date
    estimated: bool
    method: str
    evidence: dict[str, Any] = field(default_factory=dict)


_EDITION_SELECT = """
SELECT e.id, e.tenant_id, e.number, e.parent_id, e.label, e.causes, e.reason, e.engine_fingerprint,
       e.config_hash, e.code_sha, e.data_known_by, e.first_night, e.created_at,
       (SELECT max(c.asof) FROM edition_call c WHERE c.edition_id = e.id) AS last_night,
       (SELECT count(*) FROM edition_call c WHERE c.edition_id = e.id) AS n_pages,
       (SELECT count(DISTINCT c.thesis_id) FROM edition_call c WHERE c.edition_id = e.id) AS n_theses,
       (SELECT count(*) FROM edition_entry x WHERE x.edition_id = e.id) AS n_entries,
       (SELECT count(*) FROM edition_entry x WHERE x.edition_id = e.id AND x.estimated)
           AS n_entries_estimated,
       (SELECT min(v.at) FROM edition_event v WHERE v.edition_id = e.id AND v.kind = 'closed')
           AS closed_at
  FROM edition e
"""


def _edition(r: Mapping[str, Any]) -> Edition:
    return Edition(
        id=r["id"],
        tenant_id=r["tenant_id"],
        number=r["number"],
        parent_id=r["parent_id"],
        label=r["label"],
        causes=tuple(r["causes"]),
        reason=dict(r["reason"] or {}),
        engine_fingerprint=r["engine_fingerprint"],
        config_hash=r["config_hash"],
        code_sha=r["code_sha"],
        data_known_by=r["data_known_by"],
        first_night=r["first_night"],
        created_at=r["created_at"],
        last_night=r["last_night"],
        n_pages=r["n_pages"],
        n_theses=r["n_theses"],
        n_entries=r["n_entries"],
        n_entries_estimated=r["n_entries_estimated"],
        closed_at=r["closed_at"],
    )


def list_editions(conn: psycopg.Connection, *, tenant_id: UUID) -> list[Edition]:
    """Every stored edition of the tenant, ascending by number (Edition 0 is virtual and never listed)."""
    with conn.cursor() as cur:
        cur.execute(_EDITION_SELECT + " WHERE e.tenant_id = %s ORDER BY e.number", (tenant_id,))
        return [_edition(r) for r in cur.fetchall()]


def get_by_number(conn: psycopg.Connection, *, tenant_id: UUID, number: int) -> Edition | None:
    with conn.cursor() as cur:
        cur.execute(
            _EDITION_SELECT + " WHERE e.tenant_id = %s AND e.number = %s", (tenant_id, number)
        )
        r = cur.fetchone()
    return _edition(r) if r else None


def get_by_cut(
    conn: psycopg.Connection, *, tenant_id: UUID, data_known_by: datetime
) -> Edition | None:
    with conn.cursor() as cur:
        cur.execute(
            _EDITION_SELECT + " WHERE e.tenant_id = %s AND e.data_known_by = %s",
            (tenant_id, data_known_by),
        )
        r = cur.fetchone()
    return _edition(r) if r else None


def publish_edition(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    number: int,
    label: str,
    causes: Sequence[str],
    engine: Mapping[str, Any],
    data_known_by: datetime,
    pages: Sequence[CallCard],
    entries: Sequence[Mapping[str, Any]],
    computed_at: datetime,
    page_kind: str = "rerun",
    parent_id: UUID | None = None,
    reason: Mapping[str, Any] | None = None,
    event_detail: Mapping[str, Any] | None = None,
) -> UUID:
    """Append ONE whole edition: the ``edition`` row, its join dates, one ``edition_call`` page per
    (thesis, night) and its ``published`` event. The caller owns the transaction (commit / rollback), so a
    failure part-way leaves nothing. ``engine`` = ``editions.fingerprint.engine_fingerprint(cfg)``.

    Refuses with ``EditionExists`` — before any INSERT — when the tenant already has ``number`` or an edition
    at ``data_known_by`` (a second publish of the same cut). Raises ``ValueError`` on an empty page set, an
    unknown cause or page kind, or a page whose thesis is not this tenant's."""
    if number < 1:
        raise ValueError(
            "edition numbers start at 1 (Edition 0 is the original record, never stored)"
        )
    if not causes or any(c not in CAUSES for c in causes):
        raise ValueError(f"causes must be a non-empty subset of {CAUSES}, got {list(causes)!r}")
    if page_kind not in PAGE_KINDS:
        raise ValueError(f"page_kind must be one of {PAGE_KINDS}, got {page_kind!r}")
    if not pages:
        raise ValueError("an edition needs at least one page")
    prior = get_by_number(conn, tenant_id=tenant_id, number=number) or get_by_cut(
        conn, tenant_id=tenant_id, data_known_by=data_known_by
    )
    if prior is not None:
        raise EditionExists(
            f"Edition {prior.number} is already published for this tenant (cut "
            f"{prior.data_known_by.isoformat()}); editions are immutable — nothing was written"
        )
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM thesis WHERE tenant_id = %s", (tenant_id,))
        own = {r["id"] for r in cur.fetchall()}
        foreign = {p.thesis_id for p in pages} - own
        if foreign:
            raise ValueError(f"pages for theses outside this tenant: {sorted(map(str, foreign))}")
        cur.execute(
            """INSERT INTO edition
                   (tenant_id, number, parent_id, label, causes, reason, engine_fingerprint,
                    config_hash, code_sha, data_known_by, first_night)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                tenant_id,
                number,
                parent_id,
                label,
                list(causes),
                Json(dict(reason or {})),
                engine["fingerprint"],
                engine["config_hash"],
                engine.get("code_sha"),
                data_known_by,
                min(p.asof for p in pages),
            ),
        )
        edition_id = cur.fetchone()["id"]
        cur.executemany(
            """INSERT INTO edition_entry
                   (tenant_id, edition_id, thesis_id, security_id, entry_date, estimated, method,
                    evidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (
                    tenant_id,
                    edition_id,
                    UUID(str(e["thesis_id"])),
                    UUID(str(e["security_id"])),
                    date.fromisoformat(str(e["entry_date"])),
                    bool(e["estimated"]),
                    e["method"],
                    Json(dict(e.get("evidence") or {})),
                )
                for e in entries
            ],
        )
        rows = [call_to_row(p, tenant_id) for p in pages]
        cur.executemany(
            """INSERT INTO edition_call
                   (tenant_id, edition_id, thesis_id, asof, state, verdict, card, data_known_by,
                    engine_fingerprint, page_kind, computed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (
                    tenant_id,
                    edition_id,
                    r["thesis_id"],
                    r["asof"],
                    r["state"],
                    r["verdict"],
                    Json(r["card"]),
                    data_known_by,
                    engine["fingerprint"],
                    page_kind,
                    computed_at,
                )
                for r in rows
            ],
        )
        cur.execute(
            "INSERT INTO edition_event (tenant_id, edition_id, kind, detail) "
            "VALUES (%s, %s, 'published', %s)",
            (tenant_id, edition_id, Json(dict(event_detail or {}))),
        )
    return edition_id


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Row counts of the four edition tables plus ``calls`` — the count-the-table check a publish reports."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM edition) AS edition, "
            "(SELECT count(*) FROM edition_event) AS edition_event, "
            "(SELECT count(*) FROM edition_entry) AS edition_entry, "
            "(SELECT count(*) FROM edition_call) AS edition_call, "
            "(SELECT count(*) FROM calls) AS calls"
        )
        return dict(cur.fetchone())


# --- the Scoreboard's reads ----------------------------------------------------------------------------


def pages_for_thesis(
    conn: psycopg.Connection, edition_id: UUID, thesis_id: UUID, *, upto: date
) -> list[CallCard]:
    """The edition's frozen pages for one thesis, ASCENDING, ``asof <= upto``. One page per night by the
    unique key, so there is nothing to dedup; a ``calls`` row is never read."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT card FROM edition_call WHERE edition_id = %s AND thesis_id = %s AND asof <= %s "
            "ORDER BY asof",
            (edition_id, thesis_id, upto),
        )
        return [row_to_call(r) for r in cur.fetchall()]


def joins_for_thesis(
    conn: psycopg.Connection, edition_id: UUID, thesis_id: UUID
) -> dict[UUID, JoinDate]:
    """The join dates the edition was cut with for one thesis, by security."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, security_id, entry_date, estimated, method, evidence FROM edition_entry "
            "WHERE edition_id = %s AND thesis_id = %s",
            (edition_id, thesis_id),
        )
        return {
            r["security_id"]: JoinDate(
                thesis_id=r["thesis_id"],
                security_id=r["security_id"],
                entry_date=r["entry_date"],
                estimated=r["estimated"],
                method=r["method"],
                evidence=dict(r["evidence"] or {}),
            )
            for r in cur.fetchall()
        }
