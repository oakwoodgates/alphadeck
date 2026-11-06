"""``list_theses``: every card with the nightly record in effect at the as-of (``GET /theses?with_call=true``).

The queue is the ``/theses`` order filtered to live cards whose record is ARMED and dated the as-of itself (a stale
record, ``record_asof < asof``, is never in it: the Board bars a previous-date call from its Decision Queue). It is
never sorted. Every value is copied from the API's JSON; ``stale`` and the counts only compare and count them.
"""

from __future__ import annotations

from typing import Any, Literal

from ..budget import TrimRule, fit

BUDGET_QUEUE = 12_000
BUDGET_FULL = 24_000
NAME_LIMIT = 60
STATES = ("armed", "warming", "incubating", "managing")

Detail = Literal["queue", "full"]


def _name(entry: dict[str, Any], out: dict[str, Any]) -> None:
    name = entry.get("name") or ""
    out["name"] = name[:NAME_LIMIT]
    if len(name) > NAME_LIMIT:
        out["name_total_chars"] = len(name)


def _stale(call: dict[str, Any] | None, asof: str) -> bool:
    return call is not None and str(call.get("record_asof")) < asof


def _full(entry: dict[str, Any], asof: str) -> dict[str, Any]:
    call = entry.get("call")
    out: dict[str, Any] = {"id": entry.get("id")}
    _name(entry, out)
    out.update(
        {
            "ticker": entry.get("ticker"),
            "basket_size": entry.get("basket_size"),
            "archived": entry.get("archived"),
        }
    )
    if call is None:
        out.update({"state": None, "record_asof": None, "stale": False})
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
            "stale": _stale(call, asof),
            "reconstructed": call.get("reconstructed"),
        }
    )
    return out


def _brief(entry: dict[str, Any]) -> dict[str, Any]:
    call = entry.get("call")
    out: dict[str, Any] = {"id": entry.get("id")}
    _name(entry, out)
    out["state"] = call.get("state") if call else None
    out["record_asof"] = call.get("record_asof") if call else None
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


def view_list_theses(
    theses: list[dict[str, Any]],
    *,
    asof: str,
    clock: dict[str, Any],
    detail: Detail = "queue",
    state: str | None = None,
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
    listed = [t for t in theses if state is None or (t.get("call") or {}).get("state") == state]
    page = listed[cursor:]
    if detail == "full":
        cards = [_full(t, asof) for t in page]
    else:
        cards = [_full(t, asof) if t["id"] in in_queue else _brief(t) for t in page]
    reply: dict[str, Any] = {
        "asof": asof,
        "source": "record",
        "clock": {
            "current_night": clock.get("current_night"),
            "action_day": clock.get("action_day"),
        },
        "detail": detail,
        "state_filter": state,
        "counts": counts,
        "queue": queue,
        "cards_listed": len(listed),
        "cards": cards,
    }
    budget = BUDGET_FULL if detail == "full" else BUDGET_QUEUE
    return fit(reply, budget, [TrimRule(("cards",), min_keep=1, start=cursor)])
