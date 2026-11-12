"""The steward's queue for HELD insider rows (``insider_txn_hold``, 0082) — the Admin panel's read and the
steward's decisions (``insider_txn_hold_decision``, 0083). The Form 4/A queue's shape
(``repositories.insider_amendment_repo``), smaller.

THE READ (``items``). Every held row of the DATA tenant, each in ONE bucket (``domain.insider_holds.BUCKETS``),
judged by ``domain.insider_holds.hold_state`` — the same ladder the insider read (``resolve_holds``) runs, over
the same inputs (the 4/A amendments, links and decisions ``db.insider_resolution.load_inputs`` loads, and the
steward's decisions), as of NOW on the record clock: what today's view reads. So the panel can never disagree
with the read. Each item also carries the SUGGESTED date (``domain.insider_holds.suggested_date`` — arithmetic on
the filing's own fields, never a model) and every decision about the row.

THE VERBS, each with its visible inverse (WB#1) — every one ONE append-only INSERT, recorded now (never
back-dated: on the public clock a steward decision counts from when it was made, ``db.public_clock``):

- ``approve`` — count the row, dated at the RATIFIED date (the suggestion when none is sent). The date must pass
  the year rule and sit on or before the filing's market date (``domain.insider_holds.ratify_problem``).
- ``dismiss`` — do not count the row ("not a trade"); a reason is required.
- ``revoke`` — the Undo of one approve / dismiss; the state before it comes back.

EXACTNESS RULES (the Form 4/A decisions' own):

1. Every write locks the held row ``FOR UPDATE`` first (as a Form 4/A decision locks its amendment's link rows),
   so two clicks on one row serialize. Neither takes the company write lock (``db.write_lock``): like the Form
   4/A decisions, each moves the company's change stamp (``db.change_stamp.mark_changed``) in its own
   transaction, so dashboards re-read the company.
2. At most ONE standing decision per held row: a second is refused (``HoldConflict`` — undo the first). A held
   row the filer's own amendment already decides (``filer_corrected``) and a held Form 4/A row (applying one is
   not built) take no decision.
3. LABELED — who, when, why. The role is in ``basis`` the way the Form 4/A decisions record theirs
   (``<actor>:<action>`` — ``steward:approve`` / ``steward:dismiss`` / ``steward:revoke``); the PERSON is in
   ``detail.decided_by`` (``{"user_id", "display_name"}`` — never an email), ``null`` when the caller names no
   user (a direct repo call, a CLI: never an invented name); when is ``recorded_at``; why is ``reason``.
   ``detail`` also keeps the evidence the steward saw (date as filed, period of report, signature date,
   acceptance, the suggested date and whether it agrees with the filing).

Like the Form 4/A decisions, no ``data_repair`` journal row is written: a steward decision is an operator
decision, not a repair tool's run.

The caller owns the transaction (commit after a successful write; roll back on an exception).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.change_stamp import mark_changed
from db.insider_resolution import load_inputs
from domain.config import DEFAULT_CONFIG
from domain.insider_holds import (
    ACTION_APPROVE,
    ACTION_DISMISS,
    ACTION_REVOKE,
    BUCKET_AMENDMENT_ROW,
    BUCKET_FILER,
    BUCKET_OWED,
    BUCKETS,
    HOLD_FORM_4,
    HoldState,
    date_as_filed,
    filing_market_date,
    hold_states,
    ratify_problem,
    suggested_date,
)
from domain.scopes import DATA_TENANT_ID

ACTOR_STEWARD = "steward"
DEFAULT_APPROVE_REASON = "date ratified by the steward"
DEFAULT_REVOKE_REASON = "undone by the steward"


class HoldConflict(Exception):
    """A decision the current state does not allow (a decision already stands, the filer's own amendment decides
    the row, a held Form 4/A row, a revoke of a revoke or of an already-undone decision). The API maps it to
    **409**; the message is operator-facing."""


def basis_of(actor: str, action: str) -> str:
    """The author of a decision, the Form 4/A way: ``<actor>:<action>`` (``steward:approve``)."""
    return f"{actor}:{action}"


@dataclass
class HoldItem:
    """One held row as the steward's panel shows it: the hold as filed, its company, where it sits (``state``),
    the suggested date, and every decision about it (oldest first)."""

    hold: dict[str, Any]
    ticker: str | None
    name: str | None
    issuer_cik: str | None
    state: HoldState
    suggested: date | None
    agrees: bool
    decisions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def id(self) -> UUID:
        return self.hold["id"]

    @property
    def bucket(self) -> str:
        return self.state.bucket

    @property
    def standing(self) -> Mapping[str, Any] | None:
        """The standing steward decision the bucket rests on (``decided`` / ``dismissed``), else ``None``."""
        return self.state.decision


_HOLDS_SQL = """
    SELECT h.*, sm.ticker, sm.name AS company_name, sm.cik AS issuer_cik
      FROM insider_txn_hold h
      LEFT JOIN security_master sm ON sm.id = h.security_id AND sm.tenant_id = h.tenant_id
     WHERE h.tenant_id = %(t)s {where}
"""


def _holds(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    hold_ids: Collection[UUID] | None,
    security_ids: Collection[UUID] | None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    p: dict[str, Any] = {"t": tenant_id}
    if hold_ids is not None:
        clauses.append("AND h.id = ANY(%(hids)s)")
        p["hids"] = list(hold_ids)
    if security_ids is not None:
        clauses.append("AND h.security_id = ANY(%(sids)s)")
        p["sids"] = list(security_ids)
    with conn.cursor() as cur:
        cur.execute(_HOLDS_SQL.format(where=" ".join(clauses)), p)
        return cur.fetchall()


def _decisions(
    conn: psycopg.Connection, *, tenant_id: UUID, security_ids: Collection[UUID]
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_txn_hold_decision "
            "WHERE tenant_id = %s AND security_id = ANY(%s) ORDER BY recorded_at, id",
            (tenant_id, list(security_ids)),
        )
        return cur.fetchall()


def _key(r: Mapping[str, Any]) -> tuple[str, str, int, str]:
    return (str(r["security_id"]), str(r["accession"]), int(r["txn_seq"]), str(r["form"]))


def items(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID = DATA_TENANT_ID,
    hold_ids: Collection[UUID] | None = None,
    security_ids: Collection[UUID] | None = None,
    now: datetime | None = None,
    on_time_days: int = DEFAULT_CONFIG.insider_hold_on_time_days,
) -> list[HoldItem]:
    """Every held row of ``tenant_id`` (the DATA tenant: a steward route acts on it, never on a workspace),
    optionally only ``hold_ids`` / ``security_ids``, each with its bucket as the read sees it at ``now`` (default:
    now, the record clock — today's view). Ordered by bucket (``BUCKETS``: owed first), then ticker, accession
    and row number. Read-only."""
    holds = _holds(conn, tenant_id=tenant_id, hold_ids=hold_ids, security_ids=security_ids)
    if not holds:
        return []
    sids = list(dict.fromkeys(h["security_id"] for h in holds))
    at = now or datetime.now(timezone.utc)
    inputs = load_inputs(conn, sids, known_at=at, tenant_id=tenant_id, clock="record")
    decisions = _decisions(conn, tenant_id=tenant_id, security_ids=sids)
    # the steward decisions the READ sees (recorded by ``at``) decide the bucket; the panel lists every one
    by_sid_dec: dict[str, list[dict[str, Any]]] = {}
    by_key_dec: dict[tuple[str, str, int, str], list[dict[str, Any]]] = {}
    for d in decisions:
        by_key_dec.setdefault(_key(d), []).append(d)
        if d["recorded_at"] <= at:
            by_sid_dec.setdefault(str(d["security_id"]), []).append(d)
    # one ladder per security (each hold is judged on its own; the 4/A inputs are the security's)
    all_of_sid: dict[str, list[dict[str, Any]]] = {}
    for h in holds:
        all_of_sid.setdefault(str(h["security_id"]), []).append(h)
    out: list[HoldItem] = []
    for key, hs in all_of_sid.items():
        states = hold_states(
            hs,
            hold_decisions=by_sid_dec.get(key, ()),
            links=inputs.links.get(key, ()),
            decisions=inputs.decisions.get(key, ()),
            amendments=inputs.amendments.get(key, ()),
            on_time_days=on_time_days,
        )
        for h in hs:
            suggestion, agrees = suggested_date(h)
            out.append(
                HoldItem(
                    hold=h,
                    ticker=h.get("ticker"),
                    name=h.get("company_name"),
                    issuer_cik=h.get("issuer_cik"),
                    state=states[(str(h["accession"]), int(h["txn_seq"]), str(h["form"]))],
                    suggested=suggestion,
                    agrees=agrees,
                    decisions=by_key_dec.get(_key(h), []),
                )
            )
    order = {b: i for i, b in enumerate(BUCKETS)}
    out.sort(
        key=lambda it: (
            order.get(it.bucket, len(BUCKETS)),
            it.ticker or "~",
            str(it.hold["accession"]),
            int(it.hold["txn_seq"]),
            str(it.hold["form"]),
        )
    )
    return out


def one(conn: psycopg.Connection, hold_id: UUID, *, tenant_id: UUID = DATA_TENANT_ID) -> HoldItem:
    """One held row's item; ``LookupError`` when the data tenant holds no such row."""
    found = items(conn, tenant_id=tenant_id, hold_ids=[hold_id])
    if not found:
        raise LookupError(f"no held insider row {hold_id}")
    return found[0]


def count_owed(conn: psycopg.Connection, *, tenant_id: UUID = DATA_TENANT_ID) -> int:
    """How many held rows are OWED a steward decision (the Admin status count)."""
    return sum(1 for it in items(conn, tenant_id=tenant_id) if it.bucket == BUCKET_OWED)


def owed_by_security(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    *,
    tenant_id: UUID = DATA_TENANT_ID,
    now: datetime | None = None,
) -> dict[UUID, list[HoldItem]]:
    """``security_id -> its OWED held rows`` (the data flag's read). Absent = none owed."""
    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return {}
    out: dict[UUID, list[HoldItem]] = {}
    for it in items(conn, tenant_id=tenant_id, security_ids=ids, now=now):
        if it.bucket == BUCKET_OWED:
            out.setdefault(it.hold["security_id"], []).append(it)
    return out


def _lock(conn: psycopg.Connection, hold_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    """Lock one held row for the caller's transaction; ``LookupError`` if the data tenant has none."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_txn_hold WHERE id = %s AND tenant_id = %s FOR UPDATE",
            (hold_id, tenant_id),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"no held insider row {hold_id}")
    return row


def decider(user_id: UUID | str, display_name: str) -> dict[str, str]:
    """The person a decision records (``detail.decided_by``): the user id and the display name — never an email."""
    return {"user_id": str(user_id), "display_name": str(display_name)}


def _person(decided_by: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Only the two fields a decision may carry, or ``None`` (no user named: never invented)."""
    if not decided_by:
        return None
    return decider(decided_by["user_id"], decided_by.get("display_name") or "")


def _evidence(it: HoldItem) -> dict[str, Any]:
    """What the steward saw when deciding (#6) — display only, never read back by the read."""
    h = it.hold
    ceiling = filing_market_date(h)
    accepted = h.get("accepted")
    return {
        "date_as_filed": date_as_filed(h),
        "period_of_report_as_filed": h.get("period_of_report_as_filed"),
        "signature_date_as_filed": h.get("signature_date_as_filed"),
        "filed": h["filed"].isoformat() if h.get("filed") else None,
        "accepted": accepted.isoformat() if isinstance(accepted, datetime) else None,
        "filing_market_date": ceiling.isoformat() if ceiling else None,
        "suggested_date": it.suggested.isoformat() if it.suggested else None,
        "agrees": it.agrees,
        "bucket_before": it.bucket,
        "reason_held": h.get("reason"),
    }


def _insert(
    conn: psycopg.Connection, hold: Mapping[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    row = {
        "tenant_id": hold["tenant_id"],
        "security_id": hold["security_id"],
        "accession": hold["accession"],
        "txn_seq": hold["txn_seq"],
        "form": hold["form"],
        **values,
    }
    cols = list(row)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO insider_txn_hold_decision ({', '.join(cols)}) "  # noqa: S608 — fixed columns
            f"VALUES ({', '.join('%s' for _ in cols)}) RETURNING *",
            [Json(v) if isinstance(v, dict) else v for v in row.values()],
        )
        out = cur.fetchone()
    # a decision changes which insider rows count for the company: move its change stamp (dashboards)
    mark_changed(conn, [hold["security_id"]])
    return out


def _decidable(it: HoldItem) -> None:
    """Refuse a new approve / dismiss the state does not allow (``HoldConflict``)."""
    if str(it.hold["form"]) != HOLD_FORM_4 or it.bucket == BUCKET_AMENDMENT_ROW:
        raise HoldConflict(
            f"held row {it.id} is a row of a Form 4/A — applying a held amendment row is not built"
        )
    if it.bucket == BUCKET_FILER:
        raise HoldConflict(
            f"held row {it.id} is already decided by the filer's own Form 4/A ({it.state.why})"
        )
    if it.standing is not None:
        raise HoldConflict(
            f"a '{it.standing['action']}' decision already stands on held row {it.id} — undo it first"
        )


def approve(
    conn: psycopg.Connection,
    hold_id: UUID,
    ratified_date: date | None,
    reason: str | None,
    actor: str = ACTOR_STEWARD,
    *,
    tenant_id: UUID = DATA_TENANT_ID,
    decided_by: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Count the held row dated at ``ratified_date`` — the suggested date when ``None`` (``ValueError`` when there
    is none). ``ValueError`` when the date fails the year rule or is after the filing's market date (or that is
    unknown); ``HoldConflict`` per ``_decidable``; ``LookupError`` for an unknown hold."""
    hold = _lock(conn, hold_id, tenant_id)
    it = one(conn, hold_id, tenant_id=tenant_id)
    _decidable(it)
    day = ratified_date if ratified_date is not None else it.suggested
    if day is None:
        raise ValueError(
            "no date was sent and the filing gives no suggestion — send the date to ratify"
        )
    problem = ratify_problem(hold, day)
    if problem is not None:
        raise ValueError(problem)
    why = (reason or "").strip() or DEFAULT_APPROVE_REASON
    return _insert(
        conn,
        hold,
        {
            "action": ACTION_APPROVE,
            "ratified_date": day,
            "reason": why,
            "basis": basis_of(actor, ACTION_APPROVE),
            "detail": {
                **_evidence(it),
                "ratified_from": "suggestion" if ratified_date is None else "steward",
                "decided_by": _person(decided_by),
            },
        },
    )


def dismiss(
    conn: psycopg.Connection,
    hold_id: UUID,
    reason: str,
    actor: str = ACTOR_STEWARD,
    *,
    tenant_id: UUID = DATA_TENANT_ID,
    decided_by: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Do not count the held row ("not a trade"). ``ValueError`` without a reason; ``HoldConflict`` per
    ``_decidable``; ``LookupError`` for an unknown hold."""
    why = (reason or "").strip()
    if not why:
        raise ValueError("a dismiss needs a reason")
    hold = _lock(conn, hold_id, tenant_id)
    it = one(conn, hold_id, tenant_id=tenant_id)
    _decidable(it)
    return _insert(
        conn,
        hold,
        {
            "action": ACTION_DISMISS,
            "reason": why,
            "basis": basis_of(actor, ACTION_DISMISS),
            "detail": {**_evidence(it), "decided_by": _person(decided_by)},
        },
    )


def revoke(
    conn: psycopg.Connection,
    decision_id: UUID,
    reason: str | None,
    actor: str = ACTOR_STEWARD,
    *,
    tenant_id: UUID = DATA_TENANT_ID,
    decided_by: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], UUID]:
    """Undo one approve / dismiss by appending a ``revoke`` naming it. Returns ``(the revoke row, the hold id)``.
    ``LookupError`` for an unknown decision (or one whose held row is gone); ``HoldConflict`` for a revoke of a
    revoke, or of a decision already undone."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_txn_hold_decision WHERE id = %s AND tenant_id = %s",
            (decision_id, tenant_id),
        )
        target = cur.fetchone()
        if target is None:
            raise LookupError(f"no held-row decision {decision_id}")
        if target["action"] == ACTION_REVOKE:
            raise HoldConflict(f"decision {decision_id} is itself an Undo — it cannot be undone")
        cur.execute(
            "SELECT id FROM insider_txn_hold WHERE tenant_id = %s AND security_id = %s "
            "AND accession = %s AND txn_seq = %s AND form = %s",
            (
                target["tenant_id"],
                target["security_id"],
                target["accession"],
                target["txn_seq"],
                target["form"],
            ),
        )
        h = cur.fetchone()
    if h is None:
        raise LookupError(f"decision {decision_id} names no held insider row")
    hold = _lock(conn, h["id"], tenant_id)
    with conn.cursor() as cur:  # re-read under the lock: two Undo clicks serialize here
        cur.execute("SELECT 1 FROM insider_txn_hold_decision WHERE revokes = %s", (decision_id,))
        if cur.fetchone() is not None:
            raise HoldConflict(f"decision {decision_id} was already undone")
    why = (reason or "").strip() or DEFAULT_REVOKE_REASON
    row = _insert(
        conn,
        hold,
        {
            "action": ACTION_REVOKE,
            "revokes": decision_id,
            "reason": why,
            "basis": basis_of(actor, ACTION_REVOKE),
            "detail": {"revoked_action": target["action"], "decided_by": _person(decided_by)},
        },
    )
    return row, hold["id"]
