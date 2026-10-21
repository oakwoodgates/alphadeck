"""The Form 4/A amendment log (migration 0050, R8-A / R8-B) — the Admin panel's reads, the operator's decisions,
and the nightly SWEEP that applies the filer's own corrections.

Three append-only tables (``insider_amendment`` as filed, ``insider_amendment_link``, the
``insider_amendment_decision`` log); this module reads them and WRITES ONLY decisions. The ingest writes the
filings and links (``ingest.edgar.form4a``, with the system's ``apply`` for every resolvable one since R8-B);
the read path resolves against all three (``db.insider_resolution`` -> ``domain.insider_amendments``).

THE VERBS, each with its visible inverse (WB#1):

- ``decide(..., "apply")`` — ratify: a VOID / DUPLICATES keeps its suppression (now ratified), a routed
  restatement's rows replace the original's. ``decide(..., "keep")`` — the original stands; the amendment
  changes nothing (the AZTA power-of-attorney 4/A). Since R8-B ``keep`` is also the exact UNDO of a SYSTEM
  application (and of a correction applied by its link): it stands OVER the system's ``apply`` without revoking
  it, so revoking the keep restores the system's application.
- ``link_candidate`` — an AMBIGUOUS amendment's original, picked by the operator from the candidates the
  cascade found; the disposition against it is computed HERE (against the full stored rows) and STORED on the
  decision, so a read never recomputes it from a windowed row set.
- ``revoke`` — the Undo of any standing decision: appends a ``revoke`` row naming it.
- ``sweep`` (R8-B, the SYSTEM's only write here) — appends a system ``apply`` for every resolvable amendment
  that has NO decision at all (a pre-R8-B link left pending). Idempotent by construction: it never touches an
  amendment anything was ever decided on.

EXACTNESS RULES (why a decision can be trusted):

1. Every write locks the amendment's link rows ``FOR UPDATE`` first, so two clicks on one amendment
   serialize (and the sweep serializes with a click).
2. At most ONE standing link decision and ONE standing OPERATOR verdict per amendment: a second is refused
   (``AmendmentConflict`` — undo the first), and a link cannot be undone while a verdict decided under it
   stands. The one sanctioned stack is the operator's ``keep`` over a standing SYSTEM ``apply`` (R8-B); the
   pure resolver's "newest standing verdict" is then the operator's keep — the one the operator meant.
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
    ACTION_KEEP,
    ACTION_LINK,
    AMBIGUOUS,
    AUTO_DISPOSITIONS,
    BASIS_NO_SIGNAL_EFFECT,
    INERT_DISPOSITIONS,
    REVIEW_AUTO,
    REVIEW_OWED,
    ROUTED_DISPOSITIONS,
    STATUS_APPLIED,
    VERDICTS,
    Effective,
    RowMatch,
    before_after,
    changed_signal_rows,
    effect_of,
    in_window_codes,
    is_system_basis,
    latest_by_accession,
    link_to_candidate,
    match_for,
    review_bucket,
    standing_decisions,
    sweep_basis,
    within_review_horizon,
)
from ingest.edgar.form4a import stored_rows_by_accession, system_decision_values


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
    # does its effect change a row a call signal reads; was it applied by the SYSTEM (``review == auto``);
    # which signal codes' changed rows sit inside the live read horizon at the read's asof (the urgency); does
    # either side carry a code-P buy
    signal_effect: bool = True
    auto_resolved: bool = False
    in_window: tuple[str, ...] = ()
    touches_buy: bool = False
    # R8-B — the basis of the standing verdict (``system:*`` = the system applied it, ``operator:*`` = yours);
    # does a changed signal row sit inside the review horizon at the read's asof; where it sits in the
    # operator's review (``domain.insider_amendments.review_bucket``)
    verdict_basis: str | None = None
    in_review: bool = False
    review: str = REVIEW_OWED
    # R8-B — a MATCHED disposition's row match against the FULL stored originals (``match_rows``): which original
    # rows it accounts for, which STAND, and whether it is unresolved (owed); ``None`` for any other disposition
    match: RowMatch | None = None
    # the original rows (``row_key``) this amendment does not replace — they STAND beside it: when applied, the
    # originals its stored match did not account for (none for a whole-original replacement); else its match's
    kept_rows: tuple[str, ...] = ()

    @property
    def unresolved(self) -> bool:
        return self.match is not None and self.match.unresolved

    @property
    def needs_operator(self) -> bool:
        """OWED an operator decision (R8-B): an ambiguous link touching a signal row inside the review horizon,
        an operator-linked one awaiting its verdict, or one the operator took back with an Undo. Never a
        system application, never a historical ambiguous one."""
        return self.review == REVIEW_OWED

    @property
    def system_basis(self) -> str | None:
        """The basis of the standing SYSTEM application, or ``None`` (not applied by the system, or a
        correction applied by its link alone — a pre-R8-B link the sweep has not recorded yet)."""
        return self.verdict_basis if is_system_basis(self.verdict_basis) else None


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


def _amendment_rows(
    conn: psycopg.Connection, security_id: UUID, accession: str
) -> list[dict[str, Any]]:
    """The 4/A's Table I rows as filed (its newest stored version)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rows FROM insider_amendment WHERE security_id = %s AND accession = %s "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (security_id, accession),
        )
        row = cur.fetchone()
    return list((row or {}).get("rows") or [])


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

    R8-B: a ``keep`` over a standing SYSTEM ``apply`` is allowed — it is that application's Undo ("keep the
    original"), stacked over it rather than revoking it, so revoking the keep restores the system's
    application. An ``apply`` over one is refused (it already applies). An operator ``apply`` of a partial /
    consolidation / addition / restatement / field change stores its row match (``replaced_rows``): the
    original rows it does not account for stand — unless the match is unresolved, where the operator's apply
    ratifies it as a full restatement."""
    if action not in VERDICTS:
        raise ValueError(f"action must be one of {sorted(VERDICTS)}, got {action!r}")
    if not reason.strip() or not basis.strip():
        raise ValueError("a decision needs a reason and a basis")
    links = _lock(conn, security_id, accession)
    eff, link_d, verdict = _state(links, _decisions(conn, security_id, accession), accession)
    over_system = verdict is not None and is_system_basis(verdict.get("basis"))
    if over_system and action == ACTION_APPLY:
        raise AmendmentConflict(f"{accession}: the system already applied it — nothing to ratify")
    if verdict is not None and not (over_system and action == ACTION_KEEP):
        who = (
            "the system (no signal effect)"
            if verdict.get("basis") == BASIS_NO_SIGNAL_EFFECT
            else "the system (the filer's correction)" if over_system else "you"
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
    replaced: list[str] | None = None
    if action == ACTION_APPLY:
        # R8-B — a matched disposition replaces only the original rows it accounts for (computed now, against
        # the full stored rows, and stored). An UNRESOLVED match the operator applies anyway is the operator
        # ratifying it as a restatement: the whole original is replaced (``None``).
        match = match_for(
            disp,
            eff.original_accessions,
            stored_rows_by_accession(conn, security_id, tenant_id=sec["tenant_id"]),
            _amendment_rows(conn, security_id, accession),
            frozenset(default_horizons()),
        )
        if match is not None and not match.unresolved:
            replaced = list(match.replaced)
    return _insert(
        conn,
        {
            "tenant_id": sec["tenant_id"],
            "security_id": security_id,
            "amendment_accession": accession,
            "action": action,
            "reason": reason.strip(),
            "basis": basis,
            "replaced_rows": replaced,
            "detail": {
                "disposition": disp,
                "status_before": eff.status,
                # the system application this keep stands over (its Undo restores it) — R8-B
                **({"over_system_decision": str(verdict["id"])} if over_system else {}),
            },
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


_UNSET: Any = object()


def items(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID] | None = None,
    asof: date | None = None,
    horizons: Mapping[str, int | None] | None = None,
    review_days: int | None = _UNSET,
) -> list[AmendmentItem]:
    """Every linked amendment (optionally of these securities) with its CURRENT effective state and where it
    sits in the operator's review (R8-B ``review``) — the Admin panel's whole read, the nightly scan's and the
    sweep's. Ordered by URGENCY: owed a decision AND changing a signal row inside a signal's live read horizon
    at ``asof`` first (``in_window``), then the rest by ticker and acceptance (newest first). ``horizons`` is
    ``{code: days}`` from the detectors' own declarations (``signals.horizons.insider_signal_codes``; its keys
    are the signal codes) — derived when omitted; without ``asof`` nothing is in-window. ``review_days`` is the
    review horizon (``review_horizon_days()`` when omitted; ``None`` = unbounded) — without ``asof`` an
    ambiguous amendment is judged against today's market day. Read-only."""
    if horizons is None:
        horizons = default_horizons()
    if review_days is _UNSET:
        review_days = review_horizon_days()
    codes = frozenset(horizons)
    if asof is None:
        from domain.market_time import market_today  # lazy: the review horizon needs SOME asof

        review_asof = market_today()
    else:
        review_asof = asof
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
            # R8-B — the row match against the FULL stored originals; what an APPLIED one replaces is what its
            # decision stored (whole originals for a pre-R8-B decision), else what the match accounts for
            match = match_for(
                eff.disposition, eff.original_accessions, held_cache[sid], amend_rows, codes
            )
            replaced: tuple[str, ...] | None
            kept: tuple[str, ...] = ()
            if match is None:
                replaced = None
            elif eff.status == STATUS_APPLIED:
                replaced = None if eff.suppress else eff.suppress_rows
                if replaced is not None:
                    gone = set(replaced)
                    kept = tuple(k for k in sorted((*match.replaced, *match.kept)) if k not in gone)
            else:
                replaced = None if match.unresolved else match.replaced
                kept = match.kept
            before, after = before_after(
                eff.disposition,
                original_accessions=eff.original_accessions,
                voided_accessions=eff.voided_accessions,
                candidate_accessions=eff.candidate_accessions,
                stored=held_cache[sid],
                amend_rows=amend_rows,
                replaced=replaced,
            )
            changed = changed_signal_rows(eff.disposition, before, after, codes)
            own = [d for d in by_sid_dec.get(sid, []) if d["amendment_accession"] == acc]
            unresolved = match is not None and match.unresolved
            in_review = within_review_horizon(
                match.conflicts if unresolved else changed, review_asof, review_days
            )
            review = review_bucket(
                eff,
                verdict_basis=verdict.get("basis") if verdict is not None else None,
                ever_decided=bool(own),
                in_review=in_review,
                unresolved=unresolved,
            )
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
                    decisions=sorted(own, key=lambda d: (d["recorded_at"], str(d["id"]))),
                    originals={a: held_cache[sid].get(a, []) for a in sorted(wanted)},
                    signal_effect=bool(changed),
                    auto_resolved=review == REVIEW_AUTO,
                    in_window=(
                        in_window_codes(changed, asof, horizons) if asof is not None else ()
                    ),
                    touches_buy=any(r.get("txn_code") == "P" for r in (*before, *after)),
                    verdict_basis=(
                        str(verdict["basis"])
                        if verdict is not None and eff.verdict is not None
                        else None
                    ),
                    in_review=in_review,
                    review=review,
                    match=match,
                    kept_rows=kept,
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


def review_horizon_days() -> int | None:
    """R8-B — the review horizon in force: the ``insider_amendment_review_horizon_days`` setting when set (a
    negative value reads as 0), else the DERIVED default — the backtest window plus the longest detector read
    horizon on ``fact_insider_txn`` (``signals.horizons.insider_amendment_review_horizon``; ``None`` =
    unbounded). Lazy imports: the detector registry sits above the repository."""
    from domain.config import DEFAULT_CONFIG
    from domain.settings import get_settings
    from signals.horizons import insider_amendment_review_horizon

    pinned = get_settings().insider_amendment_review_horizon_days
    if pinned is not None:
        return max(0, int(pinned))
    return insider_amendment_review_horizon(DEFAULT_CONFIG)


def pending_keys(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID],
    asof: date | None = None,
    horizons: Mapping[str, int | None] | None = None,
    review_days: int | None = _UNSET,
) -> dict[str, AmendmentItem]:
    """``"<security_id>:<accession>" -> item`` for every amendment OWED an operator decision among these
    securities — the nightly scan's inventory (it pages the NEW ones once), urgency-ordered (``items``). R8-B:
    only ambiguous links that touch a signal row inside the review horizon (plus operator-linked ones awaiting
    a verdict, and any the operator took back) — a system application and a historical ambiguous link are
    never here and never paged."""
    return {
        f"{i.security_id}:{i.accession}": i
        for i in items(
            conn, security_ids=security_ids, asof=asof, horizons=horizons, review_days=review_days
        )
        if i.needs_operator
    }


# --- the SYSTEM's sweep (R8-B) ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepResult:
    """One sweep's outcome: the decisions it appended, by basis and by disposition (``applied`` = keys
    ``"<security_id>:<accession>"``), and how many it considered."""

    considered: int = 0
    applied: tuple[str, ...] = ()
    by_basis: dict[str, int] = field(default_factory=dict)
    by_disposition: dict[str, int] = field(default_factory=dict)


def sweep_plan(found: Iterable[AmendmentItem]) -> list[tuple[AmendmentItem, str]]:
    """PURE over ``items`` output — ``(item, basis)`` for every amendment the system applies now: no decision
    ever recorded on it, a disposition resolved to a definite original, and a resolved row match
    (``domain.insider_amendments.sweep_basis``). An ambiguous link, an unresolved row match, an inert one, and
    anything the operator (or the system) ever decided are left exactly as they are."""
    out: list[tuple[AmendmentItem, str]] = []
    for it in found:
        basis = sweep_basis(
            it.effective,
            ever_decided=bool(it.decisions),
            signal_effect=it.signal_effect,
            unresolved=it.unresolved,
        )
        if basis is not None:
            out.append((it, basis))
    return out


def sweep(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID] | None = None,
    signal_codes: Collection[str] | None = None,
) -> SweepResult:
    """R8-B — APPLY every resolvable amendment a pre-R8-B ingest left without a decision: append one SYSTEM
    ``apply`` each (basis ``system:filer-correction``, or ``system:no-signal-effect`` when it changes no signal
    row — ``domain.insider_amendments.system_basis``; a matched disposition carries its row match, so only the
    original rows it accounts for are replaced), stamped ``recorded_at = now()`` — so a read pinned
    before tonight still sees the state the system held then (pending; a void suppressing): no rewrite of the
    past. Every security when ``security_ids`` is None (the resolver reads amendments for any security).

    IDEMPOTENT — re-running appends nothing: an amendment is swept only while it has NO decision row at all,
    re-checked under the amendment's ``FOR UPDATE`` lock (so a click racing the sweep wins or waits, never
    doubles). Never touches an operator decision, an operator link, or an Undo (an amendment the operator took
    back stays the operator's). The caller owns the transaction."""
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    # the plan is time-free (no asof, an unbounded review horizon): WHETHER the system applies an amendment
    # depends on its disposition and decisions only, never on a date window
    found = items(
        conn, security_ids=security_ids, horizons={c: None for c in codes}, review_days=None
    )
    applied: list[str] = []
    by_basis: dict[str, int] = {}
    by_disp: dict[str, int] = {}
    for it, basis in sweep_plan(found):
        links = _lock(conn, it.security_id, it.accession)
        decisions = _decisions(conn, it.security_id, it.accession)
        eff, _, _ = _state(links, decisions, it.accession)
        if decisions or eff.disposition != it.effective.disposition:
            continue  # decided (or re-linked) since the read: the newer state wins
        v = system_decision_values(
            tenant_id=links[-1]["tenant_id"],
            security_id=it.security_id,
            accession=it.accession,
            basis=basis,
            disposition=eff.disposition,
            signal_codes=codes,
            via="sweep",
            replaced_rows=it.match.replaced if it.match is not None else None,
        )
        _insert(conn, v)
        applied.append(f"{it.security_id}:{it.accession}")
        by_basis[basis] = by_basis.get(basis, 0) + 1
        by_disp[eff.disposition] = by_disp.get(eff.disposition, 0) + 1
    return SweepResult(
        considered=len(found),
        applied=tuple(applied),
        by_basis=by_basis,
        by_disposition=by_disp,
    )


def system_applied_since(conn: psycopg.Connection, since: datetime) -> dict[str, int]:
    """``{basis: n}`` of the SYSTEM applications recorded at/after ``since`` (any security) — the cron's quiet
    count line ("N applied automatically"). Read-only."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT basis, count(*) AS n FROM insider_amendment_decision "
            "WHERE action = 'apply' AND basis LIKE 'system:%%' AND recorded_at >= %s GROUP BY basis",
            (since,),
        )
        return {r["basis"]: int(r["n"]) for r in cur.fetchall()}
