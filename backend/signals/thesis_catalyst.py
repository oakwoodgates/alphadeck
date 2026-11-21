"""V2 W6c: a card's own counting catalyst entries, read into ITS call (the signed rule, ``docs/CALL_LOGIC.md`` §3c).

An entry on a card's catalyst list counts toward that card's call only when the operator switched it on with a grade
and a citation (``fact_thesis_catalyst``, written only by ``repositories.thesis_catalyst_repo``). This module is the
PURE read side: no connection, no clock, no network. Time is the point-in-time view's ``asof`` / ``known_at``: the
as-of read already returns, per entry, the newest version saved by the night it shows (invariant 1, R5), and
``counting_entries`` keeps the ones whose switch is on.

How an entry reaches the call, without a new detector or a new detector signature:

- **Naming members (R3).** ``pipeline.core.assemble_from_pit`` wraps the card's point-in-time view ONCE in
  ``ThesisCatalystView`` (the ``signals.split_hold.HeldPriceView`` pattern). Its ``catalyst_facts(security_id)``
  returns the platform's company catalysts unchanged and first, plus one fact per entry naming that security,
  shaped like a ``fact_catalyst`` row. The catalyst detector (``signals.catalyst_conviction``) then reads the
  entry exactly as it reads a platform catalyst: the same ``Kind.CATALYST`` event, the same core / flip scores and
  the same liveness. Each card builds its view from its OWN id, so an entry can never reach another card, even one
  holding the same company; another workspace's rows are never read (``domain.scopes``).
- **Naming no member (R4, operator decision N1 (a)).** ``whole_card_facts`` hands the card's whole-card entries to
  the theme conviction (``signals.theme_conviction``), whose eligibility and FLIP cap are unchanged: it reaches only
  a voting member with a live volume-backed confirmation and no conviction of its own. It never reaches a
  per-member read.
- **Silent links and a meta index card (R10)** need nothing here: ``voting_thesis`` has already removed the rows the
  call must not hear before any member is asked for, so an entry naming a silent member is never read.

The source an operator entry carries is ``thesis_catalyst`` (its citation as the ref, the entry id and the night it
counts from in the detail): the recorded card shows where the trigger came from in its existing provenance and
gains no field (operator decision N8 (a)).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from domain.config import CallConfig
from repositories.thesis_catalyst_repo import SOURCE, counting

OPERATOR_SOURCE = SOURCE  # "thesis_catalyst": the provenance source of an operator version
DETAIL_KEY = (
    "_provenance_detail"  # a fact key the catalyst detector turns into the provenance detail
)


def counting_entries(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The counting entries among a card's as-of versions (either engine), members normalized to a
    ``frozenset[UUID]``, ordered by entry id. Pure (``thesis_catalyst_repo.counting``)."""
    return counting(rows)


def _night(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def member_fact(entry: dict[str, Any]) -> dict[str, Any]:
    """One entry as a company-catalyst fact (the ``fact_catalyst`` row shape the detector reads): its night is
    its ``valid_from``, its horizon its relevant-until date, its provenance ``thesis_catalyst`` + its citation.
    """
    return {
        "valid_from": entry["valid_from"],
        "horizon_end": entry["relevant_until"],
        "grade": entry["grade"],
        "label": entry["label"],
        "source": entry["source"],
        "source_ref": entry["citation"],
        "catalyst_type": None,
        DETAIL_KEY: {
            "entry_id": str(entry["entry_id"]),
            "counts_from": _night(entry["valid_from"]),
        },
    }


def whole_card_facts(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The whole-card entries (no member named) as theme-conviction facts, the shape the theme broadcast has always
    read: ``valid_from``, ``horizon_end``, ``grade``, ``label``, ``source``, ``source_ref``. The one converted
    theme row (migration 0213) reads back exactly the dict its old table gave (``source`` ``ratified``).
    """
    return [
        {
            "valid_from": e["valid_from"],
            "horizon_end": e["relevant_until"],
            "grade": e["grade"],
            "label": e["label"],
            "source": e["source"],
            "source_ref": e["citation"],
        }
        for e in entries
        if not e["security_ids"]
    ]


class ThesisCatalystView:
    """The point-in-time view one CARD's detectors read: ``catalyst_facts(security_id)`` is the wrapped view's
    platform catalysts (unchanged, first) plus this card's counting entries that name ``security_id``; every other
    accessor and attribute is the wrapped view's. Pure: it holds the entries it was given and reads nothing else.
    """

    def __init__(self, inner: Any, entries: Sequence[dict[str, Any]]) -> None:
        self._inner = inner
        self._by_member: dict[UUID, list[dict[str, Any]]] = {}
        for e in entries:
            for sid in sorted(e["security_ids"], key=str):
                self._by_member.setdefault(sid, []).append(member_fact(e))

    @property
    def asof(self) -> date:
        return self._inner.asof

    @property
    def known_at(self) -> Any:
        return self._inner.known_at

    def catalyst_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return [*self._inner.catalyst_facts(security_id), *self._by_member.get(security_id, ())]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def HORIZONS(
    cfg: CallConfig,
) -> dict[str, int | None]:  # noqa: N802 — the registry's module-level name
    """The READ-HORIZON declaration (``signals/horizons.py``) for this THESIS-LEVEL reader: the card's counting
    entries are read UNBOUNDED (``None``): liveness selects from the full as-of tape."""
    return {"fact_thesis_catalyst": None}
