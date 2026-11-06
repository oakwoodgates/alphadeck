"""``forming-quietly``: the warming and incubating cards: where each stands, its names with triggers, its missing
conditions, risks and the quiet tape (``GET /dashboards/forming-quietly``, dated; display only).

A row is about 38K on the wire, so the default reply keeps only each card's counts, its first eight tickers, the heads of
its first two missing conditions and the quiet-tape counts; the leading rows are the ONE paged list. Each ``section``
(``names``, ``risks``, ``missing``, ``watch``, ``quiet``, ``adoption``) needs ``thesis`` and returns that card's part
in full, paged where it is a list. Every value is copied from the API's JSON; entries are only counted and strings cut
(with their full length).
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, cut_text, fit
from .common import BUDGET, Request, ViewError, coverage, header, narrowed, pick, put_cut

SLUG = "forming-quietly"
SECTIONS = ("names", "risks", "missing", "watch", "quiet", "adoption")
NAMES_SHOWN = 8
MISSING_SHOWN = 2
QUIET_LIST_SHOWN = 25

ROW_KEYS = ("thesis_name", "state", "recorded_on", "run_start", "run_nights", "days_in_state")
BREADTH_KEYS = ("above", "counted", "thin", "pct", "delta_pts", "thrust")
ADOPTION_KEYS = ("percent", "recent", "prior")
QUIET_INTS = ("names", "read", "above_200")
TRIGGER_KEYS = ("kind", "event_date")


def _by_kind(risks: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in risks:
        out[r.get("kind")] = out.get(r.get("kind"), 0) + 1
    return out


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = pick(r, ROW_KEYS)
    out["keys"] = {
        "conviction": (r.get("key_conviction") or {}).get("turned"),
        "confirmation": (r.get("key_confirmation") or {}).get("turned"),
    }
    names = r.get("names") or []
    out["name_count"] = len(names)
    out["names"] = [n.get("ticker") for n in names[:NAMES_SHOWN]]
    missing = r.get("missing") or []
    out["missing_count"] = len(missing)
    out["missing"] = [cut_text(text, 160) for text in missing[:MISSING_SHOWN]]
    out["watch_count"] = len(r.get("watch") or [])
    risks = r.get("risks") or []
    out["risk_count"] = len(risks)
    out["risks_by_kind"] = _by_kind(risks)
    quiet = r.get("quiet") or {}
    q = pick(quiet, QUIET_INTS)
    q["coiling"] = len(quiet.get("coiling") or [])
    q["leading"] = len(quiet.get("leading") or [])
    q["breadth"] = pick(quiet["breadth"], BREADTH_KEYS) if quiet.get("breadth") else None
    out["quiet"] = q
    out["adoption"] = pick(r.get("adoption") or {}, ADOPTION_KEYS)
    return out


def _name(n: dict[str, Any]) -> dict[str, Any]:
    triggers = []
    for t in n.get("triggers") or []:
        entry = pick(t, TRIGGER_KEYS)
        put_cut(entry, "label", t.get("label"), 80)
        triggers.append(entry)
    return {"ticker": n.get("ticker"), "triggers": triggers}


def _risk(r: dict[str, Any]) -> dict[str, Any]:
    out = {"ticker": r.get("ticker"), "kind": r.get("kind")}
    put_cut(out, "label", r.get("label"), 120)
    return out


def _quiet_full(q: dict[str, Any]) -> tuple[dict[str, Any], list[TrimRule]]:
    out = {k: v for k, v in q.items() if k not in ("flagged", "insider", "coiling", "leading")}
    rules = []
    for key in ("flagged", "insider", "coiling", "leading"):
        items = q.get(key) or []
        out[f"{key}_total"] = len(items)
        out[key] = items[:QUIET_LIST_SHOWN]
        rules.append(TrimRule(("quiet", key), min_keep=0, hint="section"))
    return out, rules


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    rows = payload.get("rows") or []
    if req.thesis is not None:
        rows = [r for r in rows if r.get("thesis_id") == req.thesis.get("id")]
    reply = header(
        SLUG, payload, req, sections=SECTIONS, cov=coverage(payload), narrowed=narrowed(req)
    )
    if req.section is not None:
        if req.section not in SECTIONS:
            raise ViewError(f"Unknown section {req.section!r}.")
        if req.thesis is None:
            raise ViewError(f"section={req.section} needs thesis (the part of one card).")
        card = rows[0] if rows else {}
        reply["card_in_forming"] = bool(rows)
        if req.section == "names":
            items = card.get("names") or []
            reply["names_total"] = len(items)
            reply["names"] = [_name(n) for n in items[req.cursor :]]
            path = ("names",)
        elif req.section == "risks":
            items = card.get("risks") or []
            reply["risks_total"] = len(items)
            reply["risks"] = [_risk(r) for r in items[req.cursor :]]
            path = ("risks",)
        elif req.section == "missing":
            items = card.get("missing") or []
            reply["missing_total"] = len(items)
            reply["missing"] = list(items[req.cursor :])
            path = ("missing",)
        elif req.section == "watch":
            items = card.get("watch") or []
            reply["watch_total"] = len(items)
            reply["watch"] = [w.get("ticker") for w in items[req.cursor :]]
            path = ("watch",)
        elif req.section == "quiet":
            reply["quiet"], rules = _quiet_full(card.get("quiet") or {})
            return fit(reply, BUDGET, rules)
        else:
            reply["adoption"] = card.get("adoption")
            return fit(reply, BUDGET, [])
        return fit(reply, BUDGET, [TrimRule(path, min_keep=1, start=req.cursor, hint="cursor")])
    reply["rows_total"] = len(rows)
    reply["rows"] = [_row(r) for r in rows[req.cursor :]]
    return fit(reply, BUDGET, [TrimRule(("rows",), min_keep=1, start=req.cursor, hint="cursor")])
