"""``list_theses``: every card with the nightly record in effect at the as-of (``GET /theses?with_call=true``).

The queue is the ``/theses`` order filtered to live cards whose record is ARMED and dated the as-of itself (a stale
record, ``record_asof < asof``, is never in it: the Board bars a previous-date call from its Decision Queue). It is
never sorted.

``cards`` is ordered QUEUE-FIRST so the first page holds the whole queue: the queue cards in full, in queue order
(each with its record's first fired trigger), then the other live cards briefly in ``/theses`` order, then the archived
cards briefly. A full record is lean: ``ticker`` is omitted when null, ``archived`` / ``stale`` / ``reconstructed``
when false. A card with no nightly record carries ``no_record: true`` beside ``state: null``. Every value is copied from the API's JSON; ``stale``,
``trigger_count`` and the counts only compare and count them.
"""

from __future__ import annotations

from typing import Any, Literal

from ..budget import TrimRule, fit

# the one exception to the 8K rule: the queue page must hold the whole Decision Queue in one read
BUDGET_QUEUE = 28_000
BUDGET_FULL = 24_000
BUDGET_LIMITED = 8_000  # a short review (limit set): the rest of the cards continue by cursor
NAME_LIMIT = 120
QUEUE_TRIGGERS = 1
STATES = ("armed", "warming", "incubating", "managing")

Detail = Literal["queue", "full"]


def _name(entry: dict[str, Any], out: dict[str, Any]) -> None:
    name = entry.get("name") or ""
    out["name"] = name[:NAME_LIMIT]
    if len(name) > NAME_LIMIT:
        out["name_total_chars"] = len(name)


def _stale(call: dict[str, Any] | None, asof: str) -> bool:
    return call is not None and str(call.get("record_asof")) < asof


def _full(entry: dict[str, Any], asof: str, *, detail: Detail, queued: bool) -> dict[str, Any]:
    call = entry.get("call")
    out: dict[str, Any] = {"id": entry.get("id")}
    _name(entry, out)
    if entry.get("ticker") is not None:
        out["ticker"] = entry["ticker"]
    out["basket_size"] = entry.get("basket_size")
    if entry.get("archived"):
        out["archived"] = True
    if call is None:
        out.update({"state": None, "record_asof": None, "no_record": True})
        return out
    out.update(
        {
            "state": call.get("state"),
            "verdict": call.get("verdict"),
            "grades": {
                "conviction": call.get("conviction_grade"),
                "confirmation": call.get("confirmation_grade"),
                "entry": call.get("entry_grade"),
            },
            "setup_strength": call.get("confidence"),
            "exit_by": call.get("exit_by"),
            "arm_until": call.get("arm_until"),
            "armed_ticker": call.get("armed_ticker"),
            "armed_count": call.get("armed_count"),
            "missing_count": call.get("missing_count"),
            "record_asof": call.get("record_asof"),
        }
    )
    if _stale(call, asof):
        out["stale"] = True
    if call.get("reconstructed"):
        out["reconstructed"] = True
    if queued:
        triggers = call.get("triggers") or []
        out["trigger_count"] = len(triggers)
        out["triggers"] = [
            {
                "ticker": t.get("ticker"),
                "kind": t.get("kind"),
                "label": t.get("label"),
                "event_date": t.get("event_date"),
            }
            for t in triggers[:QUEUE_TRIGGERS]
        ]
    return out


def _brief(entry: dict[str, Any]) -> dict[str, Any]:
    call = entry.get("call")
    out: dict[str, Any] = {"id": entry.get("id")}
    _name(entry, out)
    out["basket_size"] = entry.get("basket_size")
    out["state"] = call.get("state") if call else None
    out["record_asof"] = call.get("record_asof") if call else None
    if call is None:
        out["no_record"] = True
    return out


def queue_ids(theses: list[dict[str, Any]], asof: str) -> list[str]:
    """Live cards whose record is armed and not stale, in the API's order."""
    return [
        t["id"]
        for t in theses
        if not t.get("archived")
        and (call := t.get("call")) is not None
        and call.get("state") == "armed"
        and not _stale(call, asof)
    ]


def queue_first(theses: list[dict[str, Any]], queue: list[str]) -> list[dict[str, Any]]:
    """The queue cards (queue order), then the other live cards, then the archived ones (both in list order)."""
    in_queue = set(queue)
    by_id = {t["id"]: t for t in theses}
    return [
        *(by_id[i] for i in queue),
        *(t for t in theses if t["id"] not in in_queue and not t.get("archived")),
        *(t for t in theses if t["id"] not in in_queue and t.get("archived")),
    ]


def view_list_theses(
    theses: list[dict[str, Any]],
    *,
    asof: str,
    clock: dict[str, Any],
    detail: Detail = "queue",
    state: str | None = None,
    limit: int | None = None,
    cursor: int = 0,
) -> dict[str, Any]:
    queue = queue_ids(theses, asof)
    in_queue = set(queue)
    counts = {
        "cards": len(theses),
        **{s: sum(1 for t in theses if (t.get("call") or {}).get("state") == s) for s in STATES},
        "no_record": sum(1 for t in theses if t.get("call") is None),
        "stale": sum(1 for t in theses if _stale(t.get("call"), asof)),
    }
    listed = [
        t
        for t in queue_first(theses, queue)
        if state is None or (t.get("call") or {}).get("state") == state
    ]
    # ``limit`` caps the cards shown in full (the first ones, in the order above); the rest are shown briefly.
    # ``queue`` and ``counts`` stay whole either way.
    full_ids: set[str] = set()
    for t in listed:
        if (detail == "full" or t["id"] in in_queue) and (limit is None or len(full_ids) < limit):
            full_ids.add(t["id"])
    page = listed[cursor:]
    cards = [
        (
            _full(t, asof, detail=detail, queued=t["id"] in in_queue)
            if t["id"] in full_ids
            else _brief(t)
        )
        for t in page
    ]
    reply: dict[str, Any] = {
        "asof": asof,
        "source": "record",
        "clock": {
            "current_night": clock.get("current_night"),
            "action_day": clock.get("action_day"),
        },
        "detail": detail,
        "state_filter": state,
        "limit": limit,
        "counts": counts,
        "queue": queue,
        "cards_total": len(
            listed
        ),  # every card the list holds after the state filter; cards is one page
        "cards": cards,
    }
    budget = (
        BUDGET_LIMITED if limit is not None else BUDGET_FULL if detail == "full" else BUDGET_QUEUE
    )
    return fit(reply, budget, [TrimRule(("cards",), min_keep=1, start=cursor)])
