"""The push policy's CallCard side (Q13, operator 2026-10-17: O3 + O4) — what the daily cron reads off two
consecutive calls-of-record to build a ``TransitionEvent`` (O3) and the pass-end digest (O4).

The RULE is pure and lives in ``notify.policy`` (the notifier formats from it). This module only ENRICHES:
the grade and armed-name count straight off the two cards, the tickers + trigger kinds of the names (one
security-master read), and the card-carried reasons a quiet push states. Enrich in daily, the notifier stays
PURE — the #309 seam.

FAIL-OPEN, everywhere, for the record's sake:

- ``transition_event`` runs INSIDE the per-thesis transaction, BEFORE ``record_if_changed`` + ``commit``; the
  cron rolls that transaction back on any exception. So its one DB read sits in a SAVEPOINT
  (``conn.transaction()``) — a query-specific fault (a statement timeout, a lock, a transient blip) rolls back
  to the savepoint instead of poisoning the outer transaction — and the whole enrichment has its own guard: a
  fault degrades the event to the thesis-only line, never costs the thesis its call-of-record (the
  SAVEPOINT-in-the-cron-loop trap). The counts and grades need no DB, so they survive a failed read.
- ``digest_entry`` runs AFTER the thesis's ``commit`` — the record is already safe — and never raises: a
  fault rolls back the (read-only) transaction it opened and drops only that thesis's digest line.
- ``send_digest`` runs once at the pass end and never raises: a digest fault never costs a record or another
  push.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date
from uuid import UUID

import psycopg

from domain.call import CallCard, MemberCall
from domain.enums import Kind, State
from domain.thesis import Thesis
from notify import ArmedName, DigestEvent, DigestThesis, Notifier, Timing, TransitionEvent
from securities import master

_log = logging.getLogger(__name__)


def armed_ids(card: CallCard | None) -> list[UUID]:
    """The card's armed names — distinct ``security_id``s in ``armed_members`` rank order — or ``[]`` when the
    card is not ARMED (a Managing card's held name is not an arm)."""
    if card is None or card.state is not State.ARMED:
        return []
    return list(dict.fromkeys(m.security_id for m in card.armed_members))


def _grade(card: CallCard) -> str | None:
    return card.entry_grade.value if card.entry_grade else None


def _armed_name(m: MemberCall, tickers: dict[UUID, str | None]) -> ArmedName:
    return ArmedName(
        ticker=tickers.get(m.security_id),
        # distinct, order-preserving (headline kind first)
        trigger_kinds=tuple(dict.fromkeys(t.kind.value for t in m.triggers)),
        grade=(m.entry_grade.value if m.entry_grade else None),
    )


def _dearm_reason(card: CallCard, sid: UUID) -> str | None:
    """The de-arm signal (``Kind.BREAKDOWN``) the NEW card carries for a name that is no longer armed — the
    label's own head ("Fast breakdown: close ..." -> "fast breakdown"), distinct, in card order. ``None`` when
    the card carries none (an arm can also end because its window lapsed): then the push names the ticker.
    """
    heads = [
        r.label.split(":", 1)[0].strip().lower()
        for r in card.risk_signals
        if r.security_id == sid and r.kind is Kind.BREAKDOWN and r.label
    ]
    heads = list(dict.fromkeys(h for h in heads if h))
    return ", ".join(heads) if heads else None


def _reasons(
    prior: CallCard, card: CallCard, dropped: Sequence[UUID], tickers: dict[UUID, str | None]
) -> tuple[str, ...]:
    """What a QUIET armed -> armed push states as its reason, all of it read off the cards: the names no
    longer armed (each with the de-arm signal the card carries for it), and the confirmation key's own detail
    when the thesis-level confirmation grade moved. A dropped name with no ticker is counted, not dropped.
    """
    out: list[str] = []
    if dropped:
        items: list[str] = []
        unnamed = 0
        for sid in dropped:
            ticker = tickers.get(sid)
            if not ticker:
                unnamed += 1
                continue
            why = _dearm_reason(card, sid)
            items.append(f"{ticker} ({why})" if why else ticker)
        if unnamed:
            items.append(f"{unnamed} name{'s' if unnamed > 1 else ''} without a ticker")
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
    names (ticker + trigger kinds; built whenever the new card is ARMED: an entering-Armed or upgrade push
    lists them) and, on an armed -> armed move, the card-carried reasons a quiet push states."""
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
                tickers = master.tickers_for(
                    conn, [*card_ids, *dropped], tenant_id=thesis.tenant_id
                )
            if card.state is State.ARMED:
                armed_payload = tuple(_armed_name(m, tickers) for m in card.armed_members)
            if both_armed:
                reasons = _reasons(prior, card, dropped, tickers)
        except Exception:  # noqa: BLE001 — enrichment never risks the record
            _log.warning(
                "armed-notify enrichment failed for %s (fail-open)", thesis.id, exc_info=True
            )
            armed_payload = ()
            reasons = ()
    if both_armed and not reasons and prior.confirmation_grade != card.confirmation_grade:
        # the confirmation detail needs no DB — keep it even when the ticker read failed
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
    (its company lines), so the digest must not repeat them — but only if its enrichment actually resolved the
    names (a degraded thesis-only push listed nothing, so the digest still carries them). A quiet push names
    only what it lost, never what armed."""
    return event is not None and event.loud and any(a.ticker for a in event.armed)


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
    second pass never re-announces a name. Called AFTER the thesis's commit; NEVER raises (a fault rolls back
    the read and drops only this line)."""
    try:
        if covered:
            return None
        members = newly_armed(prior, card)
        if not members:
            return None
        try:
            tickers = master.tickers_for(
                conn, [m.security_id for m in members], tenant_id=thesis.tenant_id
            )
            conn.commit()  # end the read transaction it opened (the record is already committed)
        except (
            Exception
        ):  # noqa: BLE001 — an unresolved ticker still counts the name (never dropped)
            conn.rollback()
            _log.warning("digest ticker read failed for %s (fail-open)", thesis.id, exc_info=True)
            tickers = {}
        return DigestThesis(
            thesis_id=thesis.id,
            thesis_name=thesis.name,
            names=tuple(_armed_name(m, tickers) for m in members),
        )
    except Exception:  # noqa: BLE001 — the digest never costs the pass anything
        _log.warning("digest entry failed for %s (fail-open)", thesis.id, exc_info=True)
        return None


def send_digest(
    notifier: Notifier,
    entries: Iterable[DigestThesis],
    *,
    asof: date,
    timing: Timing,
) -> DigestEvent | None:
    """O4 — emit ONE quiet digest for the pass, or nothing when no name newly armed (never a "nothing new"
    ping). Returns the event it sent (``None`` when there was nothing to send, or it failed). NEVER raises.
    """
    try:
        theses = tuple(e for e in entries if e.names)
        if not theses:
            return None
        event = DigestEvent(asof=asof, theses=theses, timing=timing)
        notifier.notify_digest(event)
        return event
    except Exception:  # noqa: BLE001 — a digest fault never costs a record or another push
        _log.warning("the newly-armed digest failed (fail-open)", exc_info=True)
        return None
