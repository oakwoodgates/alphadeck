"""The push policy's CallCard side (Q13, operator 2026-10-17: O3 + O4) — what the daily cron reads off two
consecutive calls-of-record to build a ``TransitionEvent`` (O3) and the pass-end digest (O4).

The RULE is pure and lives in ``notify.policy`` (the notifier formats from it). This module only ENRICHES:
the grade and armed-name count straight off the two cards, each name's identity (ticker, else company name —
one security-master read; NAMES, NEVER COUNTS, #9) + trigger kinds, the card-carried reasons a quiet push
states, and — for the digest — whether a newly armed name is a FIRST-TIME arm or a RE-ARM (one read of the
thesis's recorded history per pass). Enrich in daily, the notifier stays PURE — the #309 seam.

FAIL-OPEN, everywhere, for the record's sake:

- ``transition_event`` runs INSIDE the per-thesis transaction, BEFORE ``record_if_changed`` + ``commit``; the
  cron rolls that transaction back on any exception. So its DB read sits in a SAVEPOINT
  (``conn.transaction()``) — a query-specific fault (a statement timeout, a lock, a transient blip) rolls back
  to the savepoint instead of poisoning the outer transaction — and the whole enrichment has its own guard: a
  fault degrades the event to the thesis-only line, never costs the thesis its call-of-record (the
  SAVEPOINT-in-the-cron-loop trap). The counts and grades need no DB, so they survive a failed read.
- ``digest_entry`` runs AFTER the thesis's ``commit`` — the record is already safe — and never raises: a
  failed identity read lists each name by its security id (never dropped), any other fault drops only that
  thesis's line.
- ``send_digest`` runs once at the pass end and never raises: a failed history read labels every name a
  first-time arm (nothing dropped, logged), and a digest fault never costs a record or another push.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Sequence
from datetime import date
from uuid import UUID

import psycopg

from domain.call import CallCard, MemberCall
from domain.enums import Kind, State
from domain.thesis import Thesis
from notify import (
    ArmedName,
    DigestEvent,
    DigestThesis,
    Notifier,
    Timing,
    TransitionEvent,
    name_label,
)
from securities import master

_log = logging.getLogger(__name__)

# security_id -> (ticker, company name): how a push names a company (``notify.name_label``)
Identities = dict[UUID, tuple[str | None, str | None]]


def armed_ids(card: CallCard | None) -> list[UUID]:
    """The card's armed names — distinct ``security_id``s in ``armed_members`` rank order — or ``[]`` when the
    card is not ARMED (a Managing card's held name is not an arm)."""
    if card is None or card.state is not State.ARMED:
        return []
    return list(dict.fromkeys(m.security_id for m in card.armed_members))


def _grade(card: CallCard) -> str | None:
    return card.entry_grade.value if card.entry_grade else None


def identities(conn: psycopg.Connection, sids: Iterable[UUID], *, tenant_id: UUID) -> Identities:
    """Each name's (ticker, company name) from the security master. The company name is read ONLY for the
    names with no ticker (normally none — MEASURED 2026-10-17: every one of prod's 10,630 master rows has a
    ticker), so the common case stays one query. Raises on a DB fault — the caller decides how to fail open.
    """
    ids = list(dict.fromkeys(sids))
    tickers = master.tickers_for(conn, ids, tenant_id=tenant_id)
    missing = [s for s in ids if not tickers.get(s)]
    names = master.names_for(conn, missing, tenant_id=tenant_id) if missing else {}
    return {s: (tickers.get(s), names.get(s)) for s in ids}


def _armed_name(m: MemberCall, ident: Identities) -> ArmedName:
    ticker, name = ident.get(m.security_id, (None, None))
    return ArmedName(
        ticker=ticker,
        # distinct, order-preserving (headline kind first)
        trigger_kinds=tuple(dict.fromkeys(t.kind.value for t in m.triggers)),
        grade=(m.entry_grade.value if m.entry_grade else None),
        name=name,
        security_id=m.security_id,
    )


def _dearm_reason(card: CallCard, sid: UUID) -> str | None:
    """The de-arm signal (``Kind.BREAKDOWN``) the NEW card carries for a name that is no longer armed — the
    label's own head ("Fast breakdown: close ..." -> "fast breakdown"), distinct, in card order. ``None`` when
    the card carries none (an arm can also end because its window lapsed): then the push names the company.
    """
    heads = [
        r.label.split(":", 1)[0].strip().lower()
        for r in card.risk_signals
        if r.security_id == sid and r.kind is Kind.BREAKDOWN and r.label
    ]
    heads = list(dict.fromkeys(h for h in heads if h))
    return ", ".join(heads) if heads else None


def _reasons(
    prior: CallCard, card: CallCard, dropped: Sequence[UUID], ident: Identities
) -> tuple[str, ...]:
    """What a QUIET armed -> armed push states as its reason, all of it read off the cards: the names no
    longer armed (each with the de-arm signal the card carries for it), and the confirmation key's own detail
    when the thesis-level confirmation grade moved. EVERY dropped name is listed — by ticker, else by company
    name — never collapsed into a count (#9)."""
    out: list[str] = []
    if dropped:
        items: list[str] = []
        for sid in dropped:
            ticker, name = ident.get(sid, (None, None))
            who = name_label(ticker, name, sid)
            why = _dearm_reason(card, sid)
            items.append(f"{who} ({why})" if why else who)
        out.append("no longer armed: " + ", ".join(items))
    if prior.confirmation_grade != card.confirmation_grade and card.key_confirmation.detail:
        out.append(card.key_confirmation.detail)
    return tuple(out)


def transition_event(
    conn: psycopg.Connection,
    thesis: Thesis,
    prior: CallCard,
    card: CallCard,
    *,
    asof: date,
    timing: Timing,
) -> TransitionEvent:
    """The ``TransitionEvent`` for a state/verdict move ``prior`` -> ``card`` (the caller decided it IS one).
    Carries the grade + armed-name count the Q13 policy classifies on, and — enrichment, fail-open — the armed
    names (identity + trigger kinds; built whenever the new card is ARMED: an entering-Armed or upgrade push
    lists them all) and, on an armed -> armed move, the card-carried reasons a quiet push states."""
    prior_ids = armed_ids(prior)
    card_ids = armed_ids(card)
    both_armed = prior.state is State.ARMED and card.state is State.ARMED
    dropped = [s for s in prior_ids if s not in set(card_ids)] if both_armed else []
    armed_payload: tuple[ArmedName, ...] = ()
    reasons: tuple[str, ...] = ()
    if card_ids or dropped:
        try:
            # SAVEPOINT: isolate the enrichment read from the record's transaction (the module docstring).
            # The tuple-build stays OUTSIDE the savepoint (pure Python, no DB).
            with conn.transaction():
                ident = identities(conn, [*card_ids, *dropped], tenant_id=thesis.tenant_id)
            if card.state is State.ARMED:
                armed_payload = tuple(_armed_name(m, ident) for m in card.armed_members)
            if both_armed:
                reasons = _reasons(prior, card, dropped, ident)
        except Exception:  # noqa: BLE001 — enrichment never risks the record
            _log.warning(
                "armed-notify enrichment failed for %s (fail-open)", thesis.id, exc_info=True
            )
            armed_payload = ()
            reasons = ()
    if both_armed and not reasons and prior.confirmation_grade != card.confirmation_grade:
        # the confirmation detail needs no DB — keep it even when the identity read failed
        detail = card.key_confirmation.detail
        reasons = (detail,) if detail else ()
    return TransitionEvent(
        thesis_id=thesis.id,
        thesis_name=thesis.name,
        asof=asof,
        from_state=prior.state.value,
        to_state=card.state.value,
        from_verdict=prior.verdict.value,
        to_verdict=card.verdict.value,
        armed=armed_payload,
        timing=timing,
        from_grade=_grade(prior),
        to_grade=_grade(card),
        from_armed=len(prior_ids),
        to_armed=len(card_ids),
        reasons=reasons,
    )


def covers_names(event: TransitionEvent | None) -> bool:
    """Did this thesis-level push already LIST the thesis's armed names? An entering-Armed or upgrade push does
    (its company lines name every armed member), so the digest must not repeat them — unless its enrichment
    failed open and the push went out thesis-only (then the digest still carries them). A quiet push names
    only what it lost, never what armed."""
    return event is not None and event.loud and bool(event.armed)


def newly_armed(prior: CallCard | None, card: CallCard) -> list[MemberCall]:
    """O4 — the members armed on ``card`` that were NOT armed on ``prior``, when the thesis was ARMED on both
    (a thesis entering Armed is the transition push's news, not the digest's). Card rank order, distinct.
    """
    if prior is None or prior.state is not State.ARMED or card.state is not State.ARMED:
        return []
    before = set(armed_ids(prior))
    seen: set[UUID] = set()
    out: list[MemberCall] = []
    for m in card.armed_members:
        if m.security_id in before or m.security_id in seen:
            continue
        seen.add(m.security_id)
        out.append(m)
    return out


def digest_entry(
    conn: psycopg.Connection,
    thesis: Thesis,
    prior: CallCard | None,
    card: CallCard,
    *,
    covered: bool,
) -> DigestThesis | None:
    """This thesis's line for the pass-end digest, or ``None`` (nothing newly armed, or ``covered`` — the same
    pass's thesis-level push already listed the names). The baseline ``prior`` is the SAME row the transition
    compare and ``record_if_changed`` read (the latest call-of-record at or before the as-of), so a same-day
    second pass never re-announces a name. Every name is listed in ``names`` here, card rank order; the
    first-time / re-arm split happens once for the pass, in ``send_digest``. Called AFTER the thesis's commit;
    NEVER raises (a failed identity read lists the names by security id; any other fault drops the line).
    """
    try:
        if covered:
            return None
        members = newly_armed(prior, card)
        if not members:
            return None
        try:
            ident = identities(conn, [m.security_id for m in members], tenant_id=thesis.tenant_id)
            conn.commit()  # end the read transaction it opened (the record is already committed)
        except (
            Exception
        ):  # noqa: BLE001 — an unresolved name is still LISTED (by id), never dropped
            conn.rollback()
            _log.warning("digest identity read failed for %s (fail-open)", thesis.id, exc_info=True)
            ident = {}
        return DigestThesis(
            thesis_id=thesis.id,
            thesis_name=thesis.name,
            names=tuple(_armed_name(m, ident) for m in members),
        )
    except Exception:  # noqa: BLE001 — the digest never costs the pass anything
        _log.warning("digest entry failed for %s (fail-open)", thesis.id, exc_info=True)
        return None


# THE ONE DEFINITION of "ever armed before" (Q13 review, operator 2026-10-17): a name is a RE-ARM in thesis T
# on night D when it is an armed member of an ARMED call-of-record of T that is HONEST (not reconstructed), at
# an as-of on or before D, and EARLIER than this pass's own row; otherwise it is a FIRST-TIME arm. "This pass's
# own row" is the newest row for (T, D): a digest entry exists only when the new card's armed set differs from
# the baseline row, which is the very row ``record_if_changed`` compared against, so this pass APPENDED (T, D)'s
# newest row (and under the C1 pass lock nothing else writes). The window excludes exactly that row; an earlier
# same-night row (a pass before this one) still counts — a name armed on it and armed again now flapped.
_EVER_ARMED_SQL = """
SELECT DISTINCT c.thesis_id, (m->>'security_id')::uuid AS security_id
FROM (
    SELECT thesis_id, asof, seq, card,
           max(seq) OVER (PARTITION BY thesis_id, asof) AS newest_seq
    FROM calls
    WHERE thesis_id = ANY(%(tids)s) AND NOT reconstructed AND state = 'armed' AND asof <= %(asof)s
) c
CROSS JOIN LATERAL jsonb_array_elements(c.card->'armed_members') AS m
WHERE c.asof < %(asof)s OR c.seq < c.newest_seq
"""


def ever_armed(
    conn: psycopg.Connection, thesis_ids: Collection[UUID], *, asof: date
) -> set[tuple[UUID, UUID]]:
    """``{(thesis_id, security_id)}`` armed on an earlier honest recorded row of that thesis (the ONE
    definition above) — ONE read for the whole pass, read-only. Raises on a DB fault (``send_digest`` fails
    open)."""
    if not thesis_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(_EVER_ARMED_SQL, {"tids": list(thesis_ids), "asof": asof})
        return {(r["thesis_id"], r["security_id"]) for r in cur.fetchall()}


def split_rearms(
    entries: Iterable[DigestThesis], ever: Collection[tuple[UUID, UUID]]
) -> tuple[DigestThesis, ...]:
    """Pure: each entry's names split into FIRST-TIME arms (``names``, listed first, unmarked) and RE-ARMS
    (``rearms``, marked), each keeping the card's rank order. A name with no security id cannot be looked up
    and stays first-time. Entries with no names are dropped (no line, never a "nothing new" ping).
    """
    out: list[DigestThesis] = []
    for e in entries:
        if not e.all_names:
            continue
        first: list[ArmedName] = []
        rearms: list[ArmedName] = []
        for n in e.all_names:
            seen_before = n.security_id is not None and (e.thesis_id, n.security_id) in ever
            (rearms if seen_before else first).append(n)
        out.append(
            DigestThesis(
                thesis_id=e.thesis_id,
                thesis_name=e.thesis_name,
                names=tuple(first),
                rearms=tuple(rearms),
            )
        )
    return tuple(out)


def send_digest(
    conn: psycopg.Connection,
    notifier: Notifier,
    entries: Iterable[DigestThesis],
    *,
    asof: date,
    timing: Timing,
) -> DigestEvent | None:
    """O4 — emit ONE quiet digest for the pass, or nothing when no name newly armed (never a "nothing new"
    ping). Reads the pass's first-time / re-arm history ONCE (``ever_armed``); if that read fails, every name
    is a first-time arm — nothing is dropped — and the failure is logged. Returns the event it sent (``None``
    when there was nothing to send, or it failed). NEVER raises."""
    try:
        entries = tuple(e for e in entries if e.all_names)
        if not entries:
            return None
        try:
            ever = ever_armed(conn, {e.thesis_id for e in entries}, asof=asof)
            conn.commit()  # end the read transaction (every record of the pass is already committed)
        except Exception:  # noqa: BLE001 — unlabeled, never dropped
            conn.rollback()
            _log.warning(
                "digest re-arm history read failed (fail-open: every name reads first-time)",
                exc_info=True,
            )
            ever = set()
        event = DigestEvent(asof=asof, theses=split_rearms(entries, ever), timing=timing)
        notifier.notify_digest(event)
        return event
    except Exception:  # noqa: BLE001 — a digest fault never costs a record or another push
        _log.warning("the newly-armed digest failed (fail-open)", exc_info=True)
        return None
