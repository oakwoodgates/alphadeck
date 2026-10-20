"""The Form 4/A amendment log (migration 0050, R8-A) — the Admin panel's reads and the operator's decisions.

Three append-only tables (``insider_amendment`` as filed, ``insider_amendment_link``, the
``insider_amendment_decision`` log); this module reads them and WRITES ONLY decisions. The ingest writes the
filings and links (``ingest.edgar.form4a``); the read path resolves against all three
(``db.insider_resolution`` -> ``domain.insider_amendments``).

THE VERBS, each with its visible inverse (WB#1):

- ``decide(..., "apply")`` — ratify: a VOID / DUPLICATES keeps its suppression (now ratified), a routed
  restatement's rows replace the original's. ``decide(..., "keep")`` — the original stands; the amendment
  changes nothing (the AZTA power-of-attorney 4/A; also allowed on an auto-applied correction, to overrule it).
- ``link_candidate`` — an AMBIGUOUS amendment's original, picked by the operator from the candidates the
  cascade found; the disposition against it is computed HERE (against the full stored rows) and STORED on the
  decision, so a read never recomputes it from a windowed row set.
- ``revoke`` — the Undo of any standing decision: appends a ``revoke`` row naming it.

EXACTNESS RULES (why a decision can be trusted):

1. Every write locks the amendment's link rows ``FOR UPDATE`` first, so two clicks on one amendment
   serialize.
2. At most ONE standing link decision and ONE standing verdict per amendment: a second is refused
   (``AmendmentConflict`` — undo the first), and a link cannot be undone while a verdict decided under it
   stands. So the pure resolver's "newest standing of each kind" is always the one the operator meant.
3. Nothing judges the filing here: which disposition an amendment has is the linker's
   (``domain.insider_amendments``); this module only refuses a verdict that the disposition does not admit.

The caller owns the transaction (commit after a successful write; roll back on an exception).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.insider_amendments import (
    ACTION_APPLY,
    ACTION_LINK,
    AMBIGUOUS,
    AUTO_DISPOSITIONS,
    BASIS_NO_SIGNAL_EFFECT,
    INERT_DISPOSITIONS,
    ROUTED_DISPOSITIONS,
    STATUS_PENDING,
    VERDICTS,
    Effective,
    before_after,
    changed_signal_rows,
    effect_of,
    in_window_codes,
    latest_by_accession,
    link_to_candidate,
    standing_decisions,
)
from ingest.edgar.form4a import stored_rows_by_accession


class AmendmentConflict(Exception):
    """A decision the current state does not allow (nothing to ratify, a decision already stands, the
    candidate is not one the cascade found). The API maps it to **409**; the message is operator-facing.
    """


@dataclass
class AmendmentItem:
    """One amendment as the Admin panel shows it: the filing as filed, its link, its effective state, the
    standing decisions (each undoable), and the ORIGINAL rows it touches beside its own."""

    security_id: UUID
    ticker: str | None
    accession: str
    issuer_cik: str | None
    filed: date | None
    accepted: datetime | None
    recorded_at: datetime
    owners: list[dict[str, Any]]
    date_of_original_submission: date | None
    remarks: str | None
    footnotes: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    derivative_count: int
    effective: Effective
    why: str
    link_recorded_at: datetime
    decisions: list[dict[str, Any]] = field(default_factory=list)  # every decision, oldest first
    # accession -> its stored rows
    originals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # the signal-effect rule: does its effect change a row a call signal reads; was it resolved by the SYSTEM
    # (a standing ``system:no-signal-effect`` apply); which signal codes' changed rows sit inside the live read
    # horizon at the read's asof (the urgency); does either side carry a code-P buy
    signal_effect: bool = True
    auto_resolved: bool = False
    in_window: tuple[str, ...] = ()
    touches_buy: bool = False

    @property
    def needs_operator(self) -> bool:
        """Owed an operator decision: routed and not yet decided, or ambiguous and not yet linked."""
        return self.effective.status == STATUS_PENDING


def _lock(conn: psycopg.Connection, security_id: UUID, accession: str) -> list[dict[str, Any]]:
    """Lock every link version of one amendment for the caller's transaction; ``LookupError`` if none."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_link WHERE security_id = %s AND amendment_accession = %s "
            "ORDER BY recorded_at, id FOR UPDATE",
            (security_id, accession),
        )
        rows = cur.fetchall()
    if not rows:
        raise LookupError(f"no Form 4/A {accession} linked for security {security_id}")
    return rows


def _decisions(conn: psycopg.Connection, security_id: UUID, accession: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_decision WHERE security_id = %s AND amendment_accession = %s "
            "ORDER BY recorded_at, id",
            (security_id, accession),
        )
        return cur.fetchall()


def _state(
    links: list[dict[str, Any]], decisions: list[dict[str, Any]], accession: str
) -> tuple[Effective, dict[str, Any] | None, dict[str, Any] | None]:
    link = latest_by_accession(links, "amendment_accession")[accession]
    link_d, verdict = standing_decisions(decisions).get(accession, (None, None))
    return effect_of(link, link_d, verdict), link_d, verdict


def _insert(conn: psycopg.Connection, values: dict[str, Any]) -> dict[str, Any]:
    cols = list(values)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO insider_amendment_decision ({', '.join(cols)}) "  # noqa: S608 — fixed columns
            f"VALUES ({', '.join('%s' for _ in cols)}) RETURNING *",
            [Json(v) if isinstance(v, dict) else v for v in values.values()],
        )
        return cur.fetchone()


def decide(
    conn: psycopg.Connection,
    security_id: UUID,
    accession: str,
    action: str,
    *,
    reason: str,
    basis: str,
) -> dict[str, Any]:
    """Append the operator's verdict (``apply`` / ``keep``) on one amendment. ``AmendmentConflict`` when the
    disposition admits no such verdict or one already stands; ``LookupError`` for an unknown amendment.
    """
    if action not in VERDICTS:
        raise ValueError(f"action must be one of {sorted(VERDICTS)}, got {action!r}")
    if not reason.strip() or not basis.strip():
        raise ValueError("a decision needs a reason and a basis")
    links = _lock(conn, security_id, accession)
    eff, link_d, verdict = _state(links, _decisions(conn, security_id, accession), accession)
    if verdict is not None:
        who = (
            "the system (no signal effect)"
            if verdict.get("basis") == BASIS_NO_SIGNAL_EFFECT
            else "you"
        )
        raise AmendmentConflict(
            f"{accession}: a '{verdict['action']}' decision by {who} already stands — undo it first"
        )
    disp = eff.disposition
    if disp == AMBIGUOUS and link_d is None:
        raise AmendmentConflict(
            f"{accession} is ambiguous — link it to one of its candidates before ratifying"
        )
    if disp in INERT_DISPOSITIONS:
        raise AmendmentConflict(f"{accession} is {disp} — there is nothing to ratify")
    if disp in AUTO_DISPOSITIONS and action == ACTION_APPLY:
        raise AmendmentConflict(f"{accession} is an auto-applied correction — it already applies")
    if disp not in ROUTED_DISPOSITIONS and disp not in AUTO_DISPOSITIONS:
        raise AmendmentConflict(f"{accession}: unknown disposition {disp!r}")
    sec = links[-1]
    return _insert(
        conn,
        {
            "tenant_id": sec["tenant_id"],
            "security_id": security_id,
            "amendment_accession": accession,
            "action": action,
            "reason": reason.strip(),
            "basis": basis,
            "detail": {"disposition": disp, "status_before": eff.status},
        },
    )


def link_candidate(
    conn: psycopg.Connection,
    security_id: UUID,
    accession: str,
    target: str,
    *,
    reason: str,
    basis: str,
    signal_codes: Collection[str] | None = None,
) -> dict[str, Any]:
    """Link an AMBIGUOUS amendment to the candidate original ``target``: the disposition against the target's
    full stored rows is computed now (with the signal codes — a role change on a signal row is a
    ``field_change``, never clerical) and stored on the decision. ``AmendmentConflict`` unless the amendment
    is ambiguous, unlinked by the operator, and ``target`` is one of its candidates."""
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    if not reason.strip() or not basis.strip():
        raise ValueError("a link needs a reason and a basis")
    links = _lock(conn, security_id, accession)
    link = latest_by_accession(links, "amendment_accession")[accession]
    _, link_d, _ = _state(links, _decisions(conn, security_id, accession), accession)
    if link["disposition"] != AMBIGUOUS:
        raise AmendmentConflict(
            f"{accession} is not ambiguous ({link['disposition']}) — nothing to link"
        )
    if link_d is not None:
        raise AmendmentConflict(
            f"{accession} is already linked to {link_d['target_accession']} — undo that link first"
        )
    if target not in (link["candidate_accessions"] or []):
        raise AmendmentConflict(f"{target} is not a candidate original of {accession}")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rows FROM insider_amendment WHERE security_id = %s AND accession = %s "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (security_id, accession),
        )
        row = cur.fetchone()
    amend_rows = (row or {}).get("rows") or []
    held = stored_rows_by_accession(conn, security_id, tenant_id=link["tenant_id"])
    disposition, originals = link_to_candidate(amend_rows, target, held.get(target, []), codes)
    return _insert(
        conn,
        {
            "tenant_id": link["tenant_id"],
            "security_id": security_id,
            "amendment_accession": accession,
            "action": ACTION_LINK,
            "target_accession": target,
            "disposition": disposition,
            "original_accessions": list(originals),
            "reason": reason.strip(),
            "basis": basis,
            "detail": {"candidates": list(link["candidate_accessions"] or [])},
        },
    )


def revoke(
    conn: psycopg.Connection, decision_id: UUID, *, reason: str, basis: str
) -> dict[str, Any]:
    """Undo one STANDING decision by appending a ``revoke`` row naming it. ``LookupError`` when no standing
    decision has that id; ``AmendmentConflict`` when it is a link a verdict still stands under."""
    if not reason.strip() or not basis.strip():
        raise ValueError("a revoke needs a reason and a basis")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment_decision WHERE id = %s", (decision_id,))
        target = cur.fetchone()
    if target is None or target["action"] == "revoke":
        raise LookupError(f"no decision with id {decision_id}")
    links = _lock(conn, target["security_id"], target["amendment_accession"])
    decisions = _decisions(conn, target["security_id"], target["amendment_accession"])
    if any(d["revokes"] == decision_id for d in decisions):
        raise LookupError(f"decision {decision_id} was already undone")
    _, link_d, verdict = _state(links, decisions, target["amendment_accession"])
    if target["action"] == ACTION_LINK and verdict is not None:
        raise AmendmentConflict(
            f"a '{verdict['action']}' decision stands under this link — undo that one first"
        )
    if target["action"] in VERDICTS and (verdict is None or verdict["id"] != decision_id):
        raise LookupError(f"decision {decision_id} is not the standing verdict")
    if target["action"] == ACTION_LINK and (link_d is None or link_d["id"] != decision_id):
        raise LookupError(f"decision {decision_id} is not the standing link")
    return _insert(
        conn,
        {
            "tenant_id": target["tenant_id"],
            "security_id": target["security_id"],
            "amendment_accession": target["amendment_accession"],
            "action": "revoke",
            "revokes": decision_id,
            "reason": reason.strip(),
            "basis": basis,
        },
    )


# --- the reads ---------------------------------------------------------------------------------------------


def items(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID] | None = None,
    asof: date | None = None,
    horizons: Mapping[str, int | None] | None = None,
) -> list[AmendmentItem]:
    """Every linked amendment (optionally of these securities) with its CURRENT effective state — the Admin
    panel's whole read, and the nightly scan's. Ordered by URGENCY: owed a decision AND changing a signal row
    inside a signal's live read horizon at ``asof`` first (``in_window``), then the rest by ticker and
    acceptance (newest first). ``horizons`` is ``{code: days}`` from the detectors' own declarations
    (``signals.horizons.insider_signal_codes``; its keys are the signal codes) — derived when omitted; without
    ``asof`` nothing is in-window. Read-only."""
    if horizons is None:
        horizons = default_horizons()
    codes = frozenset(horizons)
    sid_filter = list(security_ids) if security_ids is not None else None
    where = "" if sid_filter is None else " WHERE x.security_id = ANY(%(ids)s)"
    p: dict[str, Any] = {"ids": sid_filter}
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment_link x" + where, p)  # noqa: S608
        links = cur.fetchall()
        cur.execute("SELECT * FROM insider_amendment_decision x" + where, p)  # noqa: S608
        decisions = cur.fetchall()
        cur.execute(
            "SELECT DISTINCT ON (x.security_id, x.accession) x.*, sm.ticker FROM insider_amendment x "
            "JOIN security_master sm ON sm.id = x.security_id"
            + where
            + " ORDER BY x.security_id, x.accession, x.recorded_at DESC, x.id DESC",  # noqa: S608
            p,
        )
        filings = {(r["security_id"], r["accession"]): r for r in cur.fetchall()}
    by_sid_links: dict[UUID, list[dict[str, Any]]] = {}
    for r in links:
        by_sid_links.setdefault(r["security_id"], []).append(r)
    by_sid_dec: dict[UUID, list[dict[str, Any]]] = {}
    for d in decisions:
        by_sid_dec.setdefault(d["security_id"], []).append(d)
    out: list[AmendmentItem] = []
    held_cache: dict[UUID, dict[str, list[dict[str, Any]]]] = {}
    for sid, sid_links in by_sid_links.items():
        latest = latest_by_accession(sid_links, "amendment_accession")
        standing = standing_decisions(by_sid_dec.get(sid, []))
        for acc, link in latest.items():
            f = filings.get((sid, acc))
            if f is None:
                continue  # a link without its filing record cannot be shown honestly
            link_d, verdict = standing.get(acc, (None, None))
            eff = effect_of(link, link_d, verdict)
            if sid not in held_cache:
                held_cache[sid] = stored_rows_by_accession(conn, sid, tenant_id=link["tenant_id"])
            wanted = (
                set(eff.candidate_accessions)
                | set(eff.original_accessions)
                | set(eff.voided_accessions)
            )
            amend_rows = list(f.get("rows") or [])
            before, after = before_after(
                eff.disposition,
                original_accessions=eff.original_accessions,
                voided_accessions=eff.voided_accessions,
                candidate_accessions=eff.candidate_accessions,
                stored=held_cache[sid],
                amend_rows=amend_rows,
            )
            changed = changed_signal_rows(eff.disposition, before, after, codes)
            auto = verdict is not None and verdict.get("basis") == BASIS_NO_SIGNAL_EFFECT
            out.append(
                AmendmentItem(
                    security_id=sid,
                    ticker=f.get("ticker"),
                    accession=acc,
                    issuer_cik=f.get("issuer_cik"),
                    filed=f.get("filed"),
                    accepted=f.get("accepted"),
                    recorded_at=f["recorded_at"],
                    owners=list(f.get("owners") or []),
                    date_of_original_submission=f.get("date_of_original_submission"),
                    remarks=f.get("remarks"),
                    footnotes=list(f.get("footnotes") or []),
                    rows=list(f.get("rows") or []),
                    derivative_count=int(f.get("derivative_count") or 0),
                    effective=eff,
                    why=str((link.get("detail") or {}).get("why") or ""),
                    link_recorded_at=link["recorded_at"],
                    decisions=sorted(
                        (d for d in by_sid_dec.get(sid, []) if d["amendment_accession"] == acc),
                        key=lambda d: (d["recorded_at"], str(d["id"])),
                    ),
                    originals={a: held_cache[sid].get(a, []) for a in sorted(wanted)},
                    signal_effect=bool(changed),
                    auto_resolved=auto and eff.status == "applied",
                    in_window=(
                        in_window_codes(changed, asof, horizons) if asof is not None else ()
                    ),
                    touches_buy=any(r.get("txn_code") == "P" for r in (*before, *after)),
                )
            )
    out.sort(
        key=lambda i: (
            not (i.needs_operator and i.in_window),
            i.ticker or "",
            -(i.accepted.timestamp() if i.accepted else i.recorded_at.timestamp()),
            i.accession,
        )
    )
    return out


def default_horizons() -> dict[str, int | None]:
    """``{code: days}`` a CALL signal reads from ``fact_insider_txn`` — the registered detectors' own
    declarations under the config the cron runs (lazy: the detector registry sits above the repository).
    """
    from domain.config import DEFAULT_CONFIG
    from signals.horizons import insider_signal_codes

    return insider_signal_codes(DEFAULT_CONFIG)


def pending_keys(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID],
    asof: date | None = None,
    horizons: Mapping[str, int | None] | None = None,
) -> dict[str, AmendmentItem]:
    """``"<security_id>:<accession>" -> item`` for every amendment OWED an operator decision among these
    securities — the nightly scan's inventory (it pages the NEW ones once), urgency-ordered (``items``). The
    ones the system resolved (no signal effect) are applied, so never here and never paged."""
    return {
        f"{i.security_id}:{i.accession}": i
        for i in items(conn, security_ids=security_ids, asof=asof, horizons=horizons)
        if i.needs_operator
    }
