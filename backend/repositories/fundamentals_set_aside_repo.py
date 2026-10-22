"""The fundamentals set-aside log (migration 0062) — append-only judgments naming stored ``fact_fundamentals``
VERSIONS the read skips.

``fact_fundamentals`` is append-only; a judgment made LATER about one of its rows ("the data-quality screen would
have refused this version") lives here, beside it, with its own history (the ``call_annotations_repo`` pattern).
Two actions:

- ``set_aside`` — every read of the table skips the version (``db.set_aside``: both clocks, the basket prefetch,
  the replay mirror), so the read falls back to the version it superseded.
- ``revoke``    — withdraws one earlier set_aside. Reversing a judgment is APPENDING, never an edit (the table
  carries the ``no_update`` trigger) and never a delete (this module offers none).

ACTIVE = a set_aside no revoke references — ``db.set_aside.ACTIVE_SQL``, stated once and used by the read and by
every query here, so "active" cannot mean two things.

Value-free by design (the repository discipline): it stores and returns judgments; it decides nothing. The rule
that produces them is ``pipeline.set_aside_fundamentals`` (#417's own screen over the stored rows).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json
from pydantic import BaseModel

from db.set_aside import ACTIVE_SQL


class SetAside(BaseModel):
    """One ``fundamentals_set_aside`` row, as stored."""

    id: UUID
    tenant_id: UUID
    fact_id: UUID
    security_id: UUID
    metric_key: str
    period_end: date
    valid_from: date
    fact_supersedes: UUID | None = None
    action: str
    reason: str
    rule: str
    author: str
    detail: dict[str, Any] = {}
    revokes: UUID | None = None
    recorded_at: datetime


def rule_family(rule: str) -> str:
    """``'fundamentals-quality-screen@<sha>'`` -> ``'fundamentals-quality-screen'``: the rule's NAME without its
    code version. Idempotency keys on the family, so a re-run from a newer image never duplicates (or
    re-activates a revoked) judgment the same rule already made."""
    return rule.split("@", 1)[0]


def append_set_aside(
    conn: psycopg.Connection,
    fact_id: UUID,
    *,
    reason: str,
    rule: str,
    author: str,
    detail: dict[str, Any] | None = None,
) -> UUID:
    """Append one set_aside naming the stored version ``fact_id``. ``tenant_id`` and the version's 0060 key
    (security, metric, period, filing, supersedes) are COPIED from the ``fact_fundamentals`` row in the same
    statement, so they can never disagree with it. Raises ``ValueError`` on a blank reason / rule / author and
    ``LookupError`` when the version does not exist. The caller owns the transaction."""
    if not reason.strip() or not rule.strip() or not author.strip():
        raise ValueError("a set-aside needs a reason, a rule and an author")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fundamentals_set_aside
                   (tenant_id, fact_id, security_id, metric_key, period_end, valid_from, fact_supersedes,
                    action, reason, rule, author, detail)
               SELECT f.tenant_id, f.id, f.security_id, f.metric_key, f.period_end, f.valid_from,
                      f.supersedes, 'set_aside', %(reason)s, %(rule)s, %(author)s, %(detail)s
                 FROM fact_fundamentals f WHERE f.id = %(fact_id)s
               RETURNING id""",
            {
                "fact_id": fact_id,
                "reason": reason,
                "rule": rule,
                "author": author,
                "detail": Json(detail or {}),
            },
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"fact_fundamentals version not found: {fact_id}")
    return row["id"]


def revoke(
    conn: psycopg.Connection, set_aside_id: UUID, *, reason: str, rule: str, author: str
) -> UUID:
    """Withdraw one ACTIVE set_aside by appending a ``revoke`` row that names it (copying its version's columns).
    Raises ``LookupError`` when no ACTIVE set_aside has that id (unknown, already revoked, or itself a revoke) —
    the DB's trigger + unique index refuse the same shapes a second time, so a hand-typed revoke cannot build
    one either. The caller owns the transaction."""
    if not reason.strip() or not rule.strip() or not author.strip():
        raise ValueError("a revoke needs a reason, a rule and an author")
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO fundamentals_set_aside
                    (tenant_id, fact_id, security_id, metric_key, period_end, valid_from, fact_supersedes,
                     action, reason, rule, author, detail, revokes)
                SELECT s.tenant_id, s.fact_id, s.security_id, s.metric_key, s.period_end, s.valid_from,
                       s.fact_supersedes, 'revoke', %(reason)s, %(rule)s, %(author)s, '{{}}'::jsonb, s.id
                  FROM fundamentals_set_aside s
                 WHERE s.id = %(id)s AND {ACTIVE_SQL}
                RETURNING id""",  # noqa: S608 — ACTIVE_SQL is a module constant, never caller input
            {"id": set_aside_id, "reason": reason, "rule": rule, "author": author},
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"no active set-aside with id {set_aside_id}")
    return row["id"]


def _rows(conn: psycopg.Connection, where: str, params: dict[str, Any]) -> list[SetAside]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.* FROM fundamentals_set_aside s WHERE "
            + where
            + " ORDER BY s.recorded_at, s.id",
            params,
        )
        return [SetAside(**r) for r in cur.fetchall()]


def all_for_rule_family(conn: psycopg.Connection, family: str) -> list[SetAside]:
    """EVERY set_aside row (active or revoked) whose rule belongs to ``family`` — the CLI's idempotency read: a
    version the family already judged is never judged again, so a re-run appends nothing and a revoke the
    operator made sticks. Read-only."""
    return _rows(
        conn,
        "s.action = 'set_aside' AND split_part(s.rule, '@', 1) = %(family)s",
        {"family": family},
    )


def active(conn: psycopg.Connection, *, family: str | None = None) -> list[SetAside]:
    """Every ACTIVE set_aside (optionally of one rule ``family``), oldest first. Read-only."""
    where = ACTIVE_SQL
    if family is not None:
        where += " AND split_part(s.rule, '@', 1) = %(family)s"
    return _rows(conn, where, {"family": family})


def count_active(conn: psycopg.Connection, *, tenant_id: UUID | None = None) -> int:
    """How many stored versions are set aside right now (optionally for one tenant) — the Admin page's one
    quiet line. Read-only."""
    where = ACTIVE_SQL + (" AND s.tenant_id = %(tenant_id)s" if tenant_id is not None else "")
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM fundamentals_set_aside s WHERE {where}",  # noqa: S608
            {"tenant_id": tenant_id},
        )
        return cur.fetchone()["n"]


__all__ = [
    "SetAside",
    "rule_family",
    "append_set_aside",
    "revoke",
    "all_for_rule_family",
    "active",
    "count_active",
]
