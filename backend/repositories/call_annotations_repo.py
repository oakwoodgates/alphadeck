"""The call-annotation log (migration 0045, D3b) — append-only judgments ABOUT recorded call-of-record rows.

``calls`` is immutable; a judgment made LATER about one of its rows ("this night was a phantom arm under rule
X") lives here, beside it, with its own history. Three actions:

- ``exclude`` — the Scoreboard's record read may drop the row (when its policy says so), BEFORE the per-as-of
  dedup, exactly like ``calls.reconstructed`` (``excluded_clause`` below is the one filter).
- ``note``    — the row stays scored; a quiet caption in the episode drawer.
- ``revoke``  — withdraws one earlier exclude/note. Reversing a judgment is APPENDING, never an edit (the
  table carries the ``no_update`` trigger) and never a delete (this module offers none).

ACTIVE = an exclude/note no revoke references. ``ACTIVE_SQL`` states that predicate ONCE; the Scoreboard's
filter (``calls_repo``) and every read here use it, so "active" cannot mean two things.

Value-free by design (the repository discipline): it stores and returns judgments; it decides nothing. The
classifiers that produce them are ``pipeline.annotate_phantom_arms`` (D3b) and
``pipeline.annotate_no_session_rows`` (F10); the policy that reads them is the Scoreboard's
(``Settings.scoreboard_exclude_annotated`` + the ``include_annotated`` query param) — ONE policy over every
``exclude`` whatever its rule.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json
from pydantic import BaseModel

ACTIONS = ("exclude", "note")  # the judgments; 'revoke' is written only by `revoke`

# The rule FAMILIES the annotators write (``rule_family(rule)`` of ``'<family>@<image sha>'``), named ONCE here so
# the writers (the two CLIs) and the one reader that words them (the Scoreboard's drawer caption) cannot drift.
# pipeline.annotate_phantom_arms — a phantom arm (D3b)
RULE_FAMILY_D3A = "d3a-first-knowability"
# pipeline.annotate_no_session_rows — a row dated a day with no market session (F10)
RULE_FAMILY_NO_SESSION = "no-session-night"

# THE ACTIVE PREDICATE, stated once: annotation `a` is active iff no revoke row references it. The one
# revoke per annotation (0045's partial unique index) and the same-call / never-a-revoke trigger make this
# single NOT EXISTS exact — there is no un-revoke chain to walk.
ACTIVE_SQL = "NOT EXISTS (SELECT 1 FROM call_annotation r WHERE r.revokes = a.id)"


def excluded_clause(exclude_annotated: bool) -> str:
    """The Scoreboard's annotated-row filter (D3b) — the ``calls_repo._reconstructed_clause`` twin, appended
    to a ``... FROM calls WHERE ...`` read: with ``exclude_annotated=True`` a row carrying an ACTIVE
    ``exclude`` annotation is dropped INSIDE the WHERE, so ``DISTINCT ON`` never sees it and the per-as-of
    dedup picks among the remaining rows (a night whose only row is excluded reads exactly like a cron gap).
    ``False`` is the empty string — every reader that does not opt in sees every row, unchanged."""
    if not exclude_annotated:
        return ""
    return (
        " AND NOT EXISTS (SELECT 1 FROM call_annotation a WHERE a.call_id = calls.id "
        f"AND a.action = 'exclude' AND {ACTIVE_SQL})"
    )


class CallAnnotation(BaseModel):
    """One annotation row, as stored."""

    id: UUID
    tenant_id: UUID
    call_id: UUID
    thesis_id: UUID
    asof: date
    action: str
    reason: str
    rule: str
    detail: dict[str, Any] = {}
    revokes: UUID | None = None
    created_at: datetime


class AnnotatedNight(BaseModel):
    """One (thesis, as-of) carrying at least one ACTIVE annotation of an action — the Scoreboard banner's
    unit. ``reasons`` / ``rules`` are distinct and sorted; ``n_rows`` counts the annotated ``calls`` rows on
    the night (a night re-recorded twice can carry two)."""

    thesis_id: UUID
    thesis_name: str
    asof: date
    reasons: list[str]
    rules: list[str]
    n_rows: int


def rule_family(rule: str) -> str:
    """``'d3a-first-knowability@<sha>'`` -> ``'d3a-first-knowability'``: the rule's NAME without its code
    version. Idempotency keys on the family, so a re-run from a newer image never duplicates (or
    re-activates a revoked) judgment the same rule already made."""
    return rule.split("@", 1)[0]


def append_annotation(
    conn: psycopg.Connection,
    call_id: UUID,
    *,
    action: str,
    reason: str,
    rule: str,
    detail: dict[str, Any] | None = None,
) -> UUID:
    """Append one exclude/note annotation on ``call_id``. ``tenant_id`` / ``thesis_id`` / ``asof`` are
    COPIED from the ``calls`` row in the same statement, so they can never disagree with it. Raises
    ``ValueError`` for another action (a revoke goes through ``revoke``) or a blank reason/rule, and
    ``LookupError`` when the call does not exist. The caller owns the transaction."""
    if action not in ACTIONS:
        raise ValueError(
            f"action must be one of {ACTIONS} (a revoke goes through revoke()), got {action!r}"
        )
    if not reason.strip() or not rule.strip():
        raise ValueError("an annotation needs a reason and a rule")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO call_annotation
                   (tenant_id, call_id, thesis_id, asof, action, reason, rule, detail)
               SELECT c.tenant_id, c.id, c.thesis_id, c.asof, %(action)s, %(reason)s, %(rule)s,
                      %(detail)s
                 FROM calls c WHERE c.id = %(call_id)s
               RETURNING id""",
            {
                "call_id": call_id,
                "action": action,
                "reason": reason,
                "rule": rule,
                "detail": Json(detail or {}),
            },
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"call not found: {call_id}")
    return row["id"]


def revoke(conn: psycopg.Connection, annotation_id: UUID, *, reason: str, rule: str) -> UUID:
    """Withdraw one ACTIVE exclude/note by appending a ``revoke`` row that names it. The revoke copies the
    target's call / thesis / as-of / tenant. Raises ``LookupError`` when no ACTIVE exclude/note has that id
    (unknown, already revoked, or itself a revoke) — the DB's trigger + unique index refuse the same shapes
    a second time, so a hand-typed revoke cannot build one either. The caller owns the transaction.
    """
    if not reason.strip() or not rule.strip():
        raise ValueError("a revoke needs a reason and a rule")
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO call_annotation
                    (tenant_id, call_id, thesis_id, asof, action, reason, rule, detail, revokes)
                SELECT a.tenant_id, a.call_id, a.thesis_id, a.asof, 'revoke', %(reason)s, %(rule)s,
                       '{{}}'::jsonb, a.id
                  FROM call_annotation a
                 WHERE a.id = %(id)s AND a.action IN ('exclude', 'note') AND {ACTIVE_SQL}
                RETURNING id""",  # noqa: S608 — ACTIVE_SQL is a module constant, never caller input
            {"id": annotation_id, "reason": reason, "rule": rule},
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"no active exclude/note annotation with id {annotation_id}")
    return row["id"]


def _rows(conn: psycopg.Connection, where: str, params: dict[str, Any]) -> list[CallAnnotation]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.* FROM call_annotation a WHERE "
            + where
            + " ORDER BY a.asof, a.created_at, a.id",
            params,
        )
        return [CallAnnotation(**r) for r in cur.fetchall()]


def active_for_call(conn: psycopg.Connection, call_id: UUID) -> list[CallAnnotation]:
    """Every ACTIVE exclude/note on one ``calls`` row. Read-only."""
    return _rows(
        conn,
        f"a.call_id = %(id)s AND a.action IN ('exclude', 'note') AND {ACTIVE_SQL}",
        {"id": call_id},
    )


def active_for_thesis(
    conn: psycopg.Connection, thesis_id: UUID, *, upto: date | None = None
) -> list[CallAnnotation]:
    """Every ACTIVE exclude/note on any of a thesis's rows (optionally capped at ``asof <= upto``), oldest
    as-of first. Read-only."""
    where = f"a.thesis_id = %(id)s AND a.action IN ('exclude', 'note') AND {ACTIVE_SQL}"
    if upto is not None:
        where += " AND a.asof <= %(upto)s"
    return _rows(conn, where, {"id": thesis_id, "upto": upto})


def all_for_rule_family(conn: psycopg.Connection, family: str) -> list[CallAnnotation]:
    """EVERY row (active, revoked, and the revokes themselves) whose rule belongs to ``family`` — the
    annotator's idempotency read: a (call, action) the family already judged is never judged again, so a
    re-run appends nothing and a revoke the operator made sticks. Read-only."""
    return _rows(
        conn,
        "split_part(a.rule, '@', 1) = %(family)s",
        {"family": family},
    )


def active_for_rule_family(conn: psycopg.Connection, family: str) -> list[CallAnnotation]:
    """Every ACTIVE exclude/note whose rule belongs to ``family`` (a revoke carries the REVOKER's rule, so
    "is it revoked" is the ``ACTIVE_SQL`` test, never a family match on the revoke row). Read-only.
    """
    return _rows(
        conn,
        f"split_part(a.rule, '@', 1) = %(family)s AND a.action IN ('exclude', 'note') AND {ACTIVE_SQL}",
        {"family": family},
    )


def active_nights(conn: psycopg.Connection, *, action: str, upto: date) -> list[AnnotatedNight]:
    """Every (thesis, as-of <= ``upto``) carrying at least one ACTIVE annotation of ``action``,
    ledger-wide (every thesis, every tenant — the ``calls_repo.reconstructed_asofs`` scope), ordered by
    as-of then thesis name. The Scoreboard banner's list. Read-only."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, got {action!r}")
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT a.thesis_id, t.name AS thesis_name, a.asof,
                       array_agg(DISTINCT a.reason ORDER BY a.reason) AS reasons,
                       array_agg(DISTINCT a.rule ORDER BY a.rule) AS rules,
                       count(DISTINCT a.call_id) AS n_rows
                  FROM call_annotation a
                  JOIN thesis t ON t.id = a.thesis_id
                 WHERE a.action = %(action)s AND a.asof <= %(upto)s AND {ACTIVE_SQL}
                 GROUP BY a.thesis_id, t.name, a.asof
                 ORDER BY a.asof, t.name""",  # noqa: S608 — ACTIVE_SQL is a module constant
            {"action": action, "upto": upto},
        )
        return [AnnotatedNight(**r) for r in cur.fetchall()]
