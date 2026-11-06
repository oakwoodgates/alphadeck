"""``daily-digest``: what the last pass changed, what is due, the insider purchases, the tape on armed names and
what is forming (``GET /dashboards/daily-digest``, dated; display only).

The default reply keeps the counts, the nights, what is forming and the positions whole, then the leading rows of each
list (the API's order; the tape shows its first AND last rows, since the API lists rises first and the falls sit at the
tail): ``last_night.cards`` is the ONE paged list (the rest continues by cursor); the tape, insider
and due lists show their leading rows and carry the full count beside them (``section`` reads one in full). Every
value is copied from the API's JSON; entries are only counted and strings cut (with their full length).
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import (
    BUDGET,
    Request,
    ViewError,
    coverage,
    header,
    narrowed,
    pick,
    put_cut,
)

SLUG = "daily-digest"
SECTIONS = ("last_night", "tape", "insider", "due")
CARDS_SHOWN = 60  # the cap before the size rule: the budget decides how many fit
TAPE_SHOWN = 3
INSIDER_SHOWN = 2
DUE_SHOWN = 4

NOT_FRESH_SHOWN = 10

CARD_KEYS = ("thesis_id", "thesis_name", "state", "previous_state", "tier")
HEADLINE_KEYS = ("change", "ticker", "from_state", "to_state", "on")
LINE_KEYS = ("change", "ticker", "from_state", "to_state", "label", "on", "signal_kind")
TAPE_KEYS = ("ticker", "thesis_name", "sessions_since", "sessions_left", "cut")
INSIDER_KEYS = ("ticker", "filer", "role", "usd", "trade_date", "filed")
DUE_KEYS = ("item", "on", "days", "today", "kind", "thesis_name", "state")
DUE_INTS = ("catalysts", "deadlines", "passed_since", "passed_total", "cards_without_calls")


def _not_fresh(src: dict[str, Any], out: dict[str, Any]) -> None:
    """A card whose last pass did not ingest every name cleanly keeps ``ingest_fresh: false`` (omitted otherwise)."""
    if src.get("ingest_fresh") is False:
        out["ingest_fresh"] = False


def _cards_not_fresh(payload: dict[str, Any]) -> list[str | None]:
    """The names of the cards (changed cards, then positions) the API marks ``ingest_fresh: false``, in its order."""
    names: list[str | None] = []
    rows = (payload.get("last_night") or {}).get("cards") or []
    for r in [*rows, *(payload.get("positions") or [])]:
        if r.get("ingest_fresh") is False and r.get("thesis_name") not in names:
            names.append(r.get("thesis_name"))
    return names[:NOT_FRESH_SHOWN]


def _headline(h: dict[str, Any] | None) -> dict[str, Any] | None:
    if h is None:
        return None
    out = pick(h, HEADLINE_KEYS)
    put_cut(out, "label", h.get("label"), 120)
    return out


def _card(c: dict[str, Any]) -> dict[str, Any]:
    out = pick(c, CARD_KEYS)
    out["headline"] = _headline(c.get("headline"))
    out["line_count"] = len(c.get("lines") or [])
    _not_fresh(c, out)
    return out


def _card_full(c: dict[str, Any]) -> dict[str, Any]:
    out = pick(c, CARD_KEYS)
    out["lines"] = [pick(line, LINE_KEYS) for line in c.get("lines") or []]
    _not_fresh(c, out)
    return out


def _tape_row(r: dict[str, Any]) -> dict[str, Any]:
    out = pick(r, TAPE_KEYS)
    last = r.get("last_session") or {}
    since = r.get("since_arm") or {}
    out["last_session"] = {"ret_pct": last.get("ret_pct"), "bench_pct": last.get("bench_pct")}
    out["since_arm"] = {"ret_pct": since.get("ret_pct"), "iwm_pct": since.get("iwm_pct")}
    return out


def _insider_row(r: dict[str, Any], *, summarized: bool) -> dict[str, Any]:
    out = pick(r, INSIDER_KEYS)
    out["card_count"] = len(r.get("cards") or [])
    if summarized:
        out["trigger_count"] = len(r.get("triggers") or [])
    put_cut(out, "headline", r.get("headline"), 100)
    return out


def _due_item(i: dict[str, Any]) -> dict[str, Any]:
    out = pick(i, DUE_KEYS)
    put_cut(out, "label", i.get("label"), 80)
    return out


def _paged(
    reply: dict[str, Any],
    path: tuple[str, ...],
    req: Request,
    *,
    extra: list[TrimRule] | None = None,
) -> dict[str, Any]:
    rules = [TrimRule(path, min_keep=1, start=req.cursor, hint="cursor")]
    return fit(reply, BUDGET, rules + (extra or []))


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    last = payload.get("last_night") or {}
    cards = last.get("cards") or []
    tape = payload.get("tape") or {}
    insider = payload.get("insider") or {}
    due = payload.get("due") or {}
    cov = coverage(  # counts ride at the top of the body, so not twice
        payload,
        {
            "no_baseline": {
                "last_night": last.get("no_baseline"),
                "insider": insider.get("no_baseline"),
            },
            **({"cards_not_fresh": nf} if (nf := _cards_not_fresh(payload)) else {}),
        },
        skip=("counts",) if req.section is None else (),
    )
    reply = header(SLUG, payload, req, sections=SECTIONS, cov=cov, narrowed=narrowed(req))
    if req.section == "last_night":
        if req.thesis is not None:
            cards = [c for c in cards if c.get("thesis_id") == req.thesis.get("id")]
        reply["cards_total"] = len(cards)
        reply["cards"] = [_card_full(c) for c in cards[req.cursor :]]
        return _paged(reply, ("cards",), req)
    if req.section == "tape":
        rows = tape.get("rows") or []
        reply["rows_total"] = len(rows)
        reply["rows"] = [_tape_row(r) for r in rows[req.cursor :]]
        return _paged(reply, ("rows",), req)
    if req.section == "insider":
        rows = insider.get("rows") or []
        reply["rows_total"] = len(rows)
        reply["rows"] = [_insider_row(r, summarized=True) for r in rows[req.cursor :]]
        return _paged(reply, ("rows",), req)
    if req.section == "due":
        items = due.get("items") or []
        reply["items_total"] = len(items)
        reply["items"] = [_due_item(i) for i in items[req.cursor :]]
        return _paged(reply, ("items",), req)
    if req.section is not None:  # the server validates sections; this guards a direct call
        raise ViewError(f"Unknown section {req.section!r}.")
    if req.thesis is not None:
        cards = [c for c in cards if c.get("thesis_id") == req.thesis.get("id")]
    reply.update(
        {
            "counts": payload.get("counts"),
            "nights": payload.get("nights"),
            "forming": payload.get("forming"),
            "positions": payload.get("positions") or [],
            "last_night": {
                "cards_with_change": last.get("cards_with_change"),
                "lines": last.get("lines"),
                "cards_total": len(cards),
                "cards": [_card(c) for c in cards[req.cursor : req.cursor + CARDS_SHOWN]],
            },
            "tape": {
                "session": tape.get("session"),
                "previous_session": tape.get("previous_session"),
                "iwm_pct": tape.get("iwm_pct"),
                "counts": tape.get("counts"),
                "rows_total": len(tape.get("rows") or []),
                "rows": [_tape_row(r) for r in (tape.get("rows") or [])[:TAPE_SHOWN]],
                # the API lists rises first, so the falls sit at its tail: show both ends, never re-sorted
                "tail": [
                    _tape_row(r)
                    for r in (tape.get("rows") or [])[
                        max(TAPE_SHOWN, len(tape.get("rows") or []) - TAPE_SHOWN) :
                    ]
                ],
            },
            "insider": {
                "span_start": insider.get("span_start"),
                "span_end": insider.get("span_end"),
                "counts": insider.get("counts"),
                "rows_total": len(insider.get("rows") or []),
                "rows": [
                    _insider_row(r, summarized=False)
                    for r in (insider.get("rows") or [])[:INSIDER_SHOWN]
                ],
            },
            "due": {
                "start": due.get("start"),
                "end": due.get("end"),
                **{k: due.get(k) for k in DUE_INTS},
                "items_total": len(due.get("items") or []),
                "items": [_due_item(i) for i in (due.get("items") or [])[:DUE_SHOWN]],
            },
        }
    )
    return fit(
        reply,
        BUDGET,
        [
            TrimRule(("last_night", "cards"), min_keep=1, start=req.cursor, hint="cursor"),
            TrimRule(("tape", "tail"), min_keep=0, hint="section"),
            TrimRule(("tape", "rows"), min_keep=0, hint="section"),
            TrimRule(("due", "items"), min_keep=0, hint="section"),
            TrimRule(("insider", "rows"), min_keep=0, hint="section"),
            TrimRule(("positions",), min_keep=0, hint="thesis"),
        ],
    )
