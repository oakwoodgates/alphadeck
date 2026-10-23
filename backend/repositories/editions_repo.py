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

S5 adds the NIGHTLY side (``editions.nightly``): ``current_edition`` (the newest edition not closed), the
appends that grow it — ``append_pages`` (``nightly`` / ``catchup`` pages), ``append_entries`` (the join date
of a company new to the edition), ``append_event`` (the ``paged`` event) — and the INK (migration 0064,
``edition_outcome``): ``ink_outcomes`` writes an episode's final score once, ``outcomes_for_thesis`` is the
Scoreboard's read. Every append is ``ON CONFLICT DO NOTHING`` on the table's unique key and returns how many
rows it actually wrote, so a second run of the same night appends nothing (the tests count the table).

S7 adds the AUTOMATIC CUT's side (``editions.cut``, migration 0065): ``events`` (an edition's life events —
the ``trial_no_change`` records the cut reads back), ``rerun_pages`` (the pages the rules trial re-tests),
``frozen_bodies`` (the thesis bodies a publish froze), ``append_causes`` / ``cause_set`` (the changes the cut's
trial attributed to its event — the "what changed" comparison's ``cause``) and ``cut_notice`` (the quiet line
the Scoreboard and the Admin show for seven days after a cut).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.call import CallCard
from replay.schema import Outcome
from repositories.mappers import call_to_row, row_to_call

EDITION_0_LABEL = "the original record"
CAUSES = ("initial", "rule_change", "data_repair", "code_drift")
PAGE_KINDS = ("rerun", "nightly", "catchup")
INK_KINDS = ("matured", "running_at_close")
# the stored edition_cause values (0065) — what the cut's RULES trial proved about a change
STORED_CAUSES = ("rule_change", "unexplained", "not_separated")
# every value a "what changed" ``cause`` can take: the stored ones, plus what the trial's scope implies for a
# change outside the proven set — ``late_data`` (a night the rules trial re-tested and found unchanged: the
# data arrived after the earlier edition's cut) and ``data_repair`` (a repair trial: a repair or late data)
DIFF_CAUSES = (*STORED_CAUSES, "late_data", "data_repair")
NOTICE_DAYS = 7


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


def newest(conn: psycopg.Connection, *, tenant_id: UUID) -> Edition | None:
    """The tenant's highest-numbered stored edition — the Scoreboard's default view — or ``None`` when
    nothing is published (the record, Edition 0, is then the only edition)."""
    with conn.cursor() as cur:
        cur.execute(
            _EDITION_SELECT + " WHERE e.tenant_id = %s ORDER BY e.number DESC LIMIT 1", (tenant_id,)
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
    """Row counts of the edition tables plus ``calls`` — the count-the-table check a publish (and the nightly
    page job) reports."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM edition) AS edition, "
            "(SELECT count(*) FROM edition_event) AS edition_event, "
            "(SELECT count(*) FROM edition_entry) AS edition_entry, "
            "(SELECT count(*) FROM edition_call) AS edition_call, "
            "(SELECT count(*) FROM edition_outcome) AS edition_outcome, "
            "(SELECT count(*) FROM edition_cause) AS edition_cause, "
            "(SELECT count(*) FROM data_repair) AS data_repair, "
            "(SELECT count(*) FROM calls) AS calls"
        )
        return dict(cur.fetchone())


# --- the nightly side (S5): the current edition and the appends that grow it ---------------------------------


def current_edition(conn: psycopg.Connection, *, tenant_id: UUID) -> Edition | None:
    """The edition the nightly pages are appended to: the tenant's NEWEST edition, when it is not closed.
    ``None`` = no stored edition, or the newest one is closed (nothing to append to — a closed edition is
    frozen whole)."""
    eds = list_editions(conn, tenant_id=tenant_id)
    if not eds or eds[-1].closed_at is not None:
        return None
    return eds[-1]


def last_page_by_thesis(conn: psycopg.Connection, edition_id: UUID) -> dict[UUID, date]:
    """``thesis_id -> its newest page's as-of`` in the edition (a thesis with no page is absent)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, max(asof) AS last FROM edition_call WHERE edition_id = %s GROUP BY 1",
            (edition_id,),
        )
        return {r["thesis_id"]: r["last"] for r in cur.fetchall()}


def entries_by_thesis(
    conn: psycopg.Connection, edition_id: UUID
) -> dict[UUID, dict[UUID, JoinDate]]:
    """Every frozen join date in the edition, ``thesis_id -> security_id -> JoinDate``."""
    out: dict[UUID, dict[UUID, JoinDate]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, security_id, entry_date, estimated, method, evidence FROM edition_entry "
            "WHERE edition_id = %s",
            (edition_id,),
        )
        for r in cur.fetchall():
            out.setdefault(r["thesis_id"], {})[r["security_id"]] = JoinDate(
                thesis_id=r["thesis_id"],
                security_id=r["security_id"],
                entry_date=r["entry_date"],
                estimated=r["estimated"],
                method=r["method"],
                evidence=dict(r["evidence"] or {}),
            )
    return out


def pages_at(conn: psycopg.Connection, edition_id: UUID, asof: date) -> dict[UUID, CallCard]:
    """The edition's page for ``asof``, per thesis (one per night by the unique key)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, card FROM edition_call WHERE edition_id = %s AND asof = %s",
            (edition_id, asof),
        )
        return {r["thesis_id"]: row_to_call(r) for r in cur.fetchall()}


def append_pages(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    edition_id: UUID,
    pages: Sequence[tuple[CallCard, str]],
    data_known_by: datetime,
    engine_fingerprint: str,
    computed_at: datetime,
) -> int:
    """Append ``(card, page_kind)`` pages to an edition, each stamped with its own watermark and engine
    fingerprint. ``ON CONFLICT DO NOTHING`` on (edition, thesis, night): a page already there is never
    replaced and never duplicated. Returns the rows written. The caller owns the transaction."""
    for _, kind in pages:
        if kind not in PAGE_KINDS:
            raise ValueError(f"page_kind must be one of {PAGE_KINDS}, got {kind!r}")
    written = 0
    with conn.cursor() as cur:
        for card, kind in pages:
            r = call_to_row(card, tenant_id)
            cur.execute(
                """INSERT INTO edition_call
                       (tenant_id, edition_id, thesis_id, asof, state, verdict, card, data_known_by,
                        engine_fingerprint, page_kind, computed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (edition_id, thesis_id, asof) DO NOTHING""",
                (
                    tenant_id,
                    edition_id,
                    r["thesis_id"],
                    r["asof"],
                    r["state"],
                    r["verdict"],
                    Json(r["card"]),
                    data_known_by,
                    engine_fingerprint,
                    kind,
                    computed_at,
                ),
            )
            written += cur.rowcount
    return written


def append_entries(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    edition_id: UUID,
    entries: Sequence[Mapping[str, Any]],
) -> int:
    """Append join dates (``editions.entry.Entry.as_json`` shape) for companies NEW to the edition.
    ``ON CONFLICT DO NOTHING`` on (edition, thesis, company): a frozen join date is never replaced. Returns
    the rows written."""
    written = 0
    with conn.cursor() as cur:
        for e in entries:
            cur.execute(
                """INSERT INTO edition_entry
                       (tenant_id, edition_id, thesis_id, security_id, entry_date, estimated, method,
                        evidence)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (edition_id, thesis_id, security_id) DO NOTHING""",
                (
                    tenant_id,
                    edition_id,
                    UUID(str(e["thesis_id"])),
                    UUID(str(e["security_id"])),
                    date.fromisoformat(str(e["entry_date"])),
                    bool(e["estimated"]),
                    e["method"],
                    Json(dict(e.get("evidence") or {})),
                ),
            )
            written += cur.rowcount
    return written


def append_event(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    edition_id: UUID,
    kind: str,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """Append one life event (``published`` / ``closed`` / ``paged`` / ``trial_no_change``)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO edition_event (tenant_id, edition_id, kind, detail) VALUES (%s, %s, %s, %s)",
            (tenant_id, edition_id, kind, Json(dict(detail or {}))),
        )


# --- the ink (S5, migration 0064): an episode's final score, written once -------------------------------------


@dataclass(frozen=True)
class InkedOutcome:
    """One inked episode score (``edition_outcome``)."""

    thesis_id: UUID
    security_id: UUID
    arm_date: date
    exit_by: date | None
    kind: str  # matured | running_at_close
    outcome: Outcome
    night: date
    prices_known_by: datetime
    inked_at: datetime


def ink_outcomes(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    edition_id: UUID,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    """Ink episode scores: each row ``{thesis_id, security_id, arm_date, exit_by, kind, outcome (an
    Outcome), night, prices_known_by}``. ``ON CONFLICT DO NOTHING`` on the episode key — an inked score is
    never re-scored, and a second ink appends nothing. Returns the rows written. The caller owns the
    transaction."""
    written = 0
    with conn.cursor() as cur:
        for r in rows:
            if r["kind"] not in INK_KINDS:
                raise ValueError(f"ink kind must be one of {INK_KINDS}, got {r['kind']!r}")
            outcome: Outcome = r["outcome"]
            cur.execute(
                """INSERT INTO edition_outcome
                       (tenant_id, edition_id, thesis_id, security_id, arm_date, exit_by, kind, outcome,
                        night, prices_known_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (edition_id, thesis_id, security_id, arm_date) DO NOTHING""",
                (
                    tenant_id,
                    edition_id,
                    r["thesis_id"],
                    r["security_id"],
                    r["arm_date"],
                    r["exit_by"],
                    r["kind"],
                    Json(outcome.model_dump(mode="json")),
                    r["night"],
                    r["prices_known_by"],
                ),
            )
            written += cur.rowcount
    return written


def outcomes_for_thesis(
    conn: psycopg.Connection, edition_id: UUID, thesis_id: UUID
) -> dict[tuple[UUID, date], InkedOutcome]:
    """The edition's inked scores for one thesis, keyed ``(security_id, arm_date)`` — the episode key."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, security_id, arm_date, exit_by, kind, outcome, night, prices_known_by, "
            "inked_at FROM edition_outcome WHERE edition_id = %s AND thesis_id = %s",
            (edition_id, thesis_id),
        )
        return {
            (r["security_id"], r["arm_date"]): InkedOutcome(
                thesis_id=r["thesis_id"],
                security_id=r["security_id"],
                arm_date=r["arm_date"],
                exit_by=r["exit_by"],
                kind=r["kind"],
                outcome=Outcome.model_validate(r["outcome"]),
                night=r["night"],
                prices_known_by=r["prices_known_by"],
                inked_at=r["inked_at"],
            )
            for r in cur.fetchall()
        }


# --- the Scoreboard's reads ----------------------------------------------------------------------------


def pages_for_thesis(
    conn: psycopg.Connection,
    edition_id: UUID,
    thesis_id: UUID,
    *,
    upto: date,
    drop_card_keys: Sequence[str] = (),
) -> list[CallCard]:
    """The edition's frozen pages for one thesis, ASCENDING, ``asof <= upto``. One page per night by the
    unique key, so there is nothing to dedup; a ``calls`` row is never read. ``drop_card_keys`` (default
    empty: the whole card) projects top-level card keys away in SQL — ``calls_repo.latest_for_thesis``'s
    twin, for a reader that only builds snapshot timelines."""
    card_sql, card_params = (
        ("card - %s::text[] AS card", (list(drop_card_keys),)) if drop_card_keys else ("card", ())
    )
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {card_sql} FROM edition_call WHERE edition_id = %s AND thesis_id = %s "
            "AND asof <= %s ORDER BY asof",
            (*card_params, edition_id, thesis_id, upto),
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


# --- the automatic cut (S7, migration 0065) -----------------------------------------------------------------


@dataclass(frozen=True)
class EditionEvent:
    kind: str
    at: datetime
    detail: dict[str, Any]


def events(
    conn: psycopg.Connection, edition_id: UUID, *, kind: str | None = None
) -> list[EditionEvent]:
    """The edition's life events, oldest first (``kind`` narrows them)."""
    sql = "SELECT kind, at, detail FROM edition_event WHERE edition_id = %s"
    params: list[Any] = [edition_id]
    if kind is not None:
        sql += " AND kind = %s"
        params.append(kind)
    with conn.cursor() as cur:
        cur.execute(sql + " ORDER BY at, id", params)
        return [EditionEvent(r["kind"], r["at"], dict(r["detail"] or {})) for r in cur.fetchall()]


def rerun_pages(conn: psycopg.Connection, edition_id: UUID) -> dict[UUID, list[CallCard]]:
    """The edition's ``rerun`` pages — the ones its publish computed at its own cut — per thesis, ascending.
    The automatic cut's rules trial re-tests exactly these (a nightly / catch-up page has its own cut).
    """
    out: dict[UUID, list[CallCard]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, card FROM edition_call WHERE edition_id = %s AND page_kind = 'rerun' "
            "ORDER BY thesis_id, asof",
            (edition_id,),
        )
        for r in cur.fetchall():
            out.setdefault(r["thesis_id"], []).append(row_to_call(r))
    return out


def rerun_nights(conn: psycopg.Connection, edition_id: UUID) -> dict[UUID, list[date]]:
    """The nights of the edition's ``rerun`` pages, per thesis, ascending (no card read)."""
    out: dict[UUID, list[date]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, asof FROM edition_call WHERE edition_id = %s AND page_kind = 'rerun' "
            "ORDER BY thesis_id, asof",
            (edition_id,),
        )
        for r in cur.fetchall():
            out.setdefault(r["thesis_id"], []).append(r["asof"])
    return out


def frozen_bodies(conn: psycopg.Connection, edition_id: UUID) -> dict[UUID, dict[str, Any]] | None:
    """The thesis bodies (kill criteria + catalysts) the edition's publish froze, per thesis — ``None`` for an
    edition published before S7 froze them (its ``published`` event carries no ``bodies``)."""
    pub = events(conn, edition_id, kind="published")
    if not pub or "bodies" not in pub[0].detail:
        return None
    return {UUID(k): v for k, v in pub[0].detail["bodies"].items()}


def append_causes(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    edition_id: UUID,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    """Append the cut's proven changes: ``{thesis_id, security_id, night | arm_date, cause}`` each.
    ``ON CONFLICT DO NOTHING`` on the key. Returns the rows written. The caller owns the transaction.
    """
    written = 0
    with conn.cursor() as cur:
        for r in rows:
            if r["cause"] not in STORED_CAUSES:
                raise ValueError(f"cause must be one of {STORED_CAUSES}, got {r['cause']!r}")
            cur.execute(
                """INSERT INTO edition_cause
                       (tenant_id, edition_id, thesis_id, security_id, night, arm_date, cause)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (
                    tenant_id,
                    edition_id,
                    r["thesis_id"],
                    r["security_id"],
                    r.get("night"),
                    r.get("arm_date"),
                    r["cause"],
                ),
            )
            written += cur.rowcount
    return written


@dataclass(frozen=True)
class CauseSet:
    """What the cut that published an edition PROVED, for the "what changed" comparison with its parent: the
    proven company-nights and episodes (``edition_cause``) and the trial's scope (``edition.reason.trial``):
    whether it ran a rules trial and/or a repair trial, and the nights per thesis the rules trial re-tested.
    ``night`` / ``episode`` turn one change into its cause, never a guess: a change in the proven set carries
    the stored cause; any other change carries what the scope implies — ``late_data`` on a night the rules
    trial re-tested and found unchanged, ``data_repair`` (a repair or late data, which a repair trial cannot
    separate), ``not_separated`` on a night the rules trial did not re-test (a nightly or catch-up page).
    """

    rules: bool
    repair: bool
    tested: dict[UUID, tuple[date, date]]
    nights: dict[tuple[UUID, UUID, date], str]
    episodes: dict[tuple[UUID, UUID, date], str]

    def _scope(self, thesis_id: UUID, d: date) -> str:
        win = self.tested.get(thesis_id)
        tested = self.rules and win is not None and win[0] <= d <= win[1]
        if self.repair:
            return "data_repair" if (tested or not self.rules) else "not_separated"
        return "late_data" if tested else "not_separated"

    def night(self, thesis_id: UUID, security_id: UUID, night: date) -> str:
        return self.nights.get((thesis_id, security_id, night)) or self._scope(thesis_id, night)

    def episode(self, thesis_id: UUID, security_id: UUID, arm_dates: Sequence[date]) -> str:
        for d in arm_dates:
            hit = self.episodes.get((thesis_id, security_id, d))
            if hit:
                return hit
        return self._scope(thesis_id, min(arm_dates))


def cause_set(
    conn: psycopg.Connection, *, before: Edition | None, after: Edition
) -> CauseSet | None:
    """The causes for comparing ``before`` -> ``after``, or ``None`` when no trial speaks for that pair: only
    an edition the automatic cut published, compared with the edition it replaced (its parent), has them
    (Edition 1 was published, not cut; a comparison across several cuts has no single trial)."""
    trial = (after.reason or {}).get("trial")
    if before is None or after.parent_id != before.id or not trial:
        return None
    tested = {
        UUID(k): (date.fromisoformat(v[0]), date.fromisoformat(v[1]))
        for k, v in (trial.get("tested") or {}).items()
    }
    nights: dict[tuple[UUID, UUID, date], str] = {}
    episodes: dict[tuple[UUID, UUID, date], str] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, security_id, night, arm_date, cause FROM edition_cause "
            "WHERE edition_id = %s",
            (after.id,),
        )
        for r in cur.fetchall():
            if r["night"] is not None:
                nights[(r["thesis_id"], r["security_id"], r["night"])] = r["cause"]
            else:
                episodes[(r["thesis_id"], r["security_id"], r["arm_date"])] = r["cause"]
    return CauseSet(
        rules=bool(trial.get("rules")),
        repair=bool(trial.get("repair")),
        tested=tested,
        nights=nights,
        episodes=episodes,
    )


@dataclass(frozen=True)
class CutNotice:
    """The quiet line for seven days after the automatic cut published the newest edition."""

    number: int
    previous: int
    label: str
    published_at: datetime
    n_company_nights_changed: int
    n_pages_changed: int
    unexplained: bool

    def line(self) -> str:
        n = self.n_company_nights_changed
        return f"Edition {self.number} · {self.label} · {n} company-night{'s' if n != 1 else ''} changed"


def cut_notice(
    conn: psycopg.Connection, *, tenant_id: UUID, now: datetime, days: int = NOTICE_DAYS
) -> CutNotice | None:
    """The newest edition's notice when the automatic cut published it (it has a parent and a trial) less
    than ``days`` ago; ``None`` otherwise (Edition 1, an older cut, nothing published)."""
    ed = newest(conn, tenant_id=tenant_id)
    if ed is None or ed.parent_id is None or not (ed.reason or {}).get("trial"):
        return None
    if now - ed.created_at > timedelta(days=days):
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT number FROM edition WHERE id = %s", (ed.parent_id,))
        r = cur.fetchone()
    trial = ed.reason["trial"]
    return CutNotice(
        number=ed.number,
        previous=r["number"] if r else ed.number - 1,
        label=ed.label,
        published_at=ed.created_at,
        n_company_nights_changed=int(trial.get("n_company_nights_changed") or 0),
        n_pages_changed=int(trial.get("n_pages_changed") or 0),
        unexplained=bool(trial.get("unexplained")),
    )
