"""``hot-board``: the candidate names by heat, each with its triggers, clocks, return since arm, tape and cap
(``GET /dashboards/hot-board``, dated, setting ``rows``; display only).

The API's row order IS the heat order: it is copied, never re-sorted. The default reply keeps the candidate counts and
the leading rows (the ONE paged list; the rest continues by cursor), each with its first two triggers and the tape
figures (the sparkline series dropped). ``section="row"`` with ``thesis`` returns that card's rows in full: every
trigger with its sources and the sparkline. Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, Request, ViewError, coverage, header, narrowed, pick, put_cut

SLUG = "hot-board"
SECTIONS = ("row",)
TRIGGERS_SHOWN = 2

ROW_KEYS = (
    "ticker",
    "kind",
    "verdict",
    "entry_grade",
    "keys",
    "risk_kinds",
    "clocks",
    "entry_window_closing",
    "lapsing",
    "thesis_name",
)
TRIGGER_KEYS = ("kind", "event_date")
SINCE_ARM_KEYS = ("ret_pct", "iwm_pct", "gap")
CAP_KEYS = ("value", "tier", "gap")
TAPE_KEYS = (
    "flag",
    "rvol20",
    "vs_50d",
    "off_high",
    "vs_iwm",
    "rs_leading",
    "rs_leading_vs",
    "ret_7d",
    "ret_30d",
    "buys_30d",
    "buyers_30d",
    "adv_usd_20d",
)
BODY_KEYS = ("candidate_counts", "rows_loaded", "benchmark")


def _trigger(t: dict[str, Any], *, full: bool) -> dict[str, Any]:
    out = pick(t, TRIGGER_KEYS)
    if full:
        out["label"] = t.get("label")
    else:
        put_cut(out, "label", t.get("label"), 80)
    if full:
        out["grade"] = t.get("grade")
        out["sources"] = [pick(s, ("source", "ref", "url")) for s in t.get("sources") or []]
    return out


def _row(r: dict[str, Any], *, full: bool) -> dict[str, Any]:
    out = pick(r, ROW_KEYS)
    if full:
        out["thesis_id"] = r.get("thesis_id")
    put_cut(out, "name", r.get("name"), 40)
    out["also_armed_count"] = len(r.get("also_armed") or [])
    out["setup_strength"] = r.get("confidence")  # the wire field is confidence
    triggers = r.get("triggers") or []
    out["trigger_count"] = len(triggers)
    out["triggers"] = [
        _trigger(t, full=full) for t in (triggers if full else triggers[:TRIGGERS_SHOWN])
    ]
    out["since_arm"] = pick(r.get("since_arm") or {}, SINCE_ARM_KEYS)
    tape = pick(r.get("tape") or {}, TAPE_KEYS)
    # a cell is its bare value, or {value, note} when the API gives a note
    for key, cell in tape.items():
        if isinstance(cell, dict) and set(cell) == {"value", "note"} and cell["note"] is None:
            tape[key] = cell["value"]
    if full:
        tape["spark"] = (r.get("tape") or {}).get("spark")
        out["also_armed"] = [pick(c, ("thesis_name",)) for c in r.get("also_armed") or []]
    out["tape"] = tape
    out["cap"] = pick(r.get("cap") or {}, CAP_KEYS)
    return out


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    rows = payload.get("rows") or []
    if req.thesis is not None:
        rows = [r for r in rows if r.get("thesis_id") == req.thesis.get("id")]
    reply = header(
        SLUG, payload, req, sections=SECTIONS, cov=coverage(payload), narrowed=narrowed(req)
    )
    if req.section == "row":
        if req.thesis is None:
            raise ViewError("section=row needs thesis (a card's rows in full).")
        reply["rows_total"] = len(rows)
        reply["rows"] = [_row(r, full=True) for r in rows[req.cursor :]]
        return fit(
            reply, BUDGET, [TrimRule(("rows",), min_keep=1, start=req.cursor, hint="cursor")]
        )
    if req.section is not None:
        raise ViewError(f"Unknown section {req.section!r}.")
    reply.update({k: payload.get(k) for k in BODY_KEYS})
    reply["rows_total"] = len(rows)
    reply["rows"] = [_row(r, full=False) for r in rows[req.cursor :]]
    return fit(reply, BUDGET, [TrimRule(("rows",), min_keep=1, start=req.cursor, hint="cursor")])
