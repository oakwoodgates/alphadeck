"""``get_call``: the platform's call recomputed live at the as-of (``GET /theses/{id}/call``, read-only).

The default reply is the decision: state, verdict, grades, the two keys, the horizons, the armed name, every fired
trigger with its sources (each source's ``detail``, the computation, is left to ``section="triggers"``), the
missing conditions and short heads of the long prose. ``detail="brief"`` is the decision alone (each trigger
without its sources, only their count; the prose only as lengths) for a pass over many cards. ``section`` returns
the header plus ONE part in full.
Every value is copied from the API's JSON; ``risk_signals.by_kind`` and ``trigger_count`` only count entries.
"""

from __future__ import annotations

from typing import Any, Literal

from ..budget import TrimRule, cut_text, fit

BUDGET = 8_000
BRIEF_BUDGET = 3_500
BRIEF_MISSING_LIMIT = 100
BRIEF_LABEL_LIMIT = 40
SECTION_BUDGET = 24_000
COUNTER_CASE_LIMIT = 600
EXPRESSION_LIMIT = 300
MEMBERS_LIMIT = 10
CATALYSTS_LIMIT = 10
RISK_PAGE = 10
SECTIONS = ("triggers", "risk_signals", "counter_case", "members", "expression")

Section = Literal["triggers", "risk_signals", "counter_case", "members", "expression"]
Detail = Literal["brief", "standard"]


def _grades(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "conviction": obj.get("conviction_grade"),
        "confirmation": obj.get("confirmation_grade"),
        "entry": obj.get("entry_grade"),
    }


def _trigger(t: dict[str, Any], *, with_detail: bool) -> dict[str, Any]:
    sources = []
    for s in t.get("sources") or []:
        src = {"source": s.get("source"), "ref": s.get("ref"), "url": s.get("url")}
        if with_detail:
            src["detail"] = s.get("detail")
        sources.append(src)
    return {
        "ticker": t.get("ticker"),
        "kind": t.get("kind"),
        "label": t.get("label"),
        "grade": t.get("grade"),
        "event_date": t.get("event_date"),
        "sources": sources,
    }


def _trigger_line(t: dict[str, Any]) -> str:
    """One brief trigger as a line: ``TICKER kind grade event_date src=N label`` (label cut at 40 characters,
    ``...`` marks a cut). A 42-trigger card must fit twenty of them in the brief; as objects only eight did.
    """
    label = t.get("label") or ""
    cut = label[:BRIEF_LABEL_LIMIT] + ("..." if len(label) > BRIEF_LABEL_LIMIT else "")
    return (
        f"{t.get('ticker') or '-'} {t.get('kind')} {t.get('grade') or '-'} "
        f"{t.get('event_date') or '-'} src={len(t.get('sources') or [])} {cut}"
    )


def _key(k: dict[str, Any] | None) -> dict[str, Any] | None:
    if k is None:
        return None
    return {"turned": k.get("turned"), "label": k.get("label"), "detail": k.get("detail")}


def _header(call: dict[str, Any], asof: str, thesis: dict[str, Any]) -> dict[str, Any]:
    return {
        "asof": asof,
        "source": "live",
        "thesis": {"id": thesis.get("id"), "name": thesis.get("name")},
        "state": call.get("state"),
        "verdict": call.get("verdict"),
    }


def _armed(call: dict[str, Any]) -> dict[str, Any] | None:
    sid = call.get("armed_security_id")
    if sid is None:
        return None
    ticker = None
    for m in [*(call.get("armed_members") or []), *(call.get("triggers_fired") or [])]:
        if m.get("security_id") == sid and m.get("ticker"):
            ticker = m["ticker"]
            break
    return {"ticker": ticker, "security_id": sid}


def _member_brief(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": m.get("ticker"),
        "verdict": m.get("verdict"),
        "grades": _grades(m),
        "exit_by": m.get("exit_by"),
        "arm_until": m.get("arm_until"),
        "lapsing": m.get("lapsing"),
        "entry_window_closing": m.get("entry_window_closing"),
        "theme_armed": m.get("theme_armed"),
        "trigger_count": len(m.get("triggers") or []),
    }


def _member_full(m: dict[str, Any], group: str) -> dict[str, Any]:
    out = {"group": group, **_member_brief(m)}
    out.pop("trigger_count")
    out["setup_strength"] = m.get("confidence")
    out["triggers"] = [_trigger(t, with_detail=True) for t in m.get("triggers") or []]
    return out


def _by_kind(risks: list[dict[str, Any]]) -> dict[str, int]:
    by_kind: dict[str, int] = {}
    for r in risks:
        by_kind[r.get("kind")] = by_kind.get(r.get("kind"), 0) + 1
    return by_kind


def _brief(call: dict[str, Any], reply: dict[str, Any], cursor: int) -> dict[str, Any]:
    triggers = call.get("triggers_fired") or []
    risks = call.get("risk_signals") or []
    reply.update(
        {
            "detail": "brief",
            "grades": _grades(call),
            "setup_strength": call.get("confidence"),
            "keys": {
                "conviction": {"turned": (call.get("key_conviction") or {}).get("turned")},
                "confirmation": {"turned": (call.get("key_confirmation") or {}).get("turned")},
            },
            "exit_by": call.get("exit_by"),
            "arm_until": call.get("arm_until"),
            "armed": _armed(call),
            "triggers_fired_total": len(triggers),
            "triggers_fired": [_trigger_line(t) for t in triggers[cursor:]],
            "missing": [cut_text(m, BRIEF_MISSING_LIMIT) for m in call.get("missing") or []],
            "risk_signals": {"count": len(risks), "by_kind": _by_kind(risks)},
            "counter_case": {"total_chars": len(call.get("counter_case") or "")},
            "catalyst_surface_total": len(call.get("catalyst_surface") or []),
            "sections": list(SECTIONS),
        }
    )
    return fit(reply, BRIEF_BUDGET, [TrimRule(("triggers_fired",), min_keep=0, start=cursor)])


def view_call(
    call: dict[str, Any],
    *,
    asof: str,
    thesis: dict[str, Any],
    section: Section | None = None,
    detail: Detail = "standard",
    cursor: int = 0,
) -> dict[str, Any]:
    reply = _header(call, asof, thesis)
    if section is None and detail == "brief":
        return _brief(call, reply, cursor)
    if section is None:
        armed_members = call.get("armed_members") or []
        risks = call.get("risk_signals") or []
        by_kind = _by_kind(risks)
        catalysts = call.get("catalyst_surface") or []
        triggers = call.get("triggers_fired") or []
        reply.update(
            {
                "grades": _grades(call),
                "setup_strength": call.get("confidence"),
                "keys": {
                    "conviction": _key(call.get("key_conviction")),
                    "confirmation": _key(call.get("key_confirmation")),
                },
                "exit_by": call.get("exit_by"),
                "arm_until": call.get("arm_until"),
                "armed": _armed(call),
                "armed_members_total": len(armed_members),
                "armed_members": [_member_brief(m) for m in armed_members[:MEMBERS_LIMIT]],
                "watch_members": [m.get("ticker") for m in call.get("watch_members") or []],
                "missing": list(call.get("missing") or []),
                "counter_case": cut_text(call.get("counter_case"), COUNTER_CASE_LIMIT),
                "risk_signals": {"count": len(risks), "by_kind": by_kind},
                "expression": cut_text(call.get("expression"), EXPRESSION_LIMIT),
                "safe_sleeve": call.get("safe_sleeve"),
                "catalyst_surface_total": len(catalysts),
                "catalyst_surface": [
                    {
                        "label": c.get("label"),
                        "kind": c.get("kind"),
                        "when_date": c.get("when_date"),
                    }
                    for c in catalysts[:CATALYSTS_LIMIT]
                ],
                "triggers_fired_total": len(triggers),
                "triggers_fired": [_trigger(t, with_detail=False) for t in triggers[cursor:]],
                "sections": list(SECTIONS),
            }
        )
        return fit(reply, BUDGET, [TrimRule(("triggers_fired",), min_keep=0, start=cursor)])
    reply["section"] = section
    if section == "triggers":
        triggers = call.get("triggers_fired") or []
        reply["triggers_fired_total"] = len(triggers)
        reply["triggers_fired"] = [_trigger(t, with_detail=True) for t in triggers[cursor:]]
        rules = [TrimRule(("triggers_fired",), min_keep=1, start=cursor)]
    elif section == "risk_signals":
        risks = call.get("risk_signals") or []
        page = risks[cursor : cursor + RISK_PAGE]
        reply["risk_signals_total"] = len(risks)
        reply["risk_signals"] = [_trigger(r, with_detail=True) for r in page]
        rules = [TrimRule(("risk_signals",), min_keep=1, start=cursor)]
        end = cursor + len(page)
        reply["next_cursor"] = end if end < len(risks) else None
        reply["next_cursor_for"] = "risk_signals" if end < len(risks) else None
    elif section == "counter_case":
        reply["counter_case"] = call.get("counter_case") or ""
        rules = []
    elif section == "members":
        members = [_member_full(m, "armed") for m in call.get("armed_members") or []] + [
            _member_full(m, "watch") for m in call.get("watch_members") or []
        ]
        reply["members_total"] = len(members)
        reply["members"] = members[cursor:]
        rules = [TrimRule(("members",), min_keep=1, start=cursor)]
    else:
        reply["expression"] = call.get("expression") or ""
        rules = []
    return fit(reply, SECTION_BUDGET, rules)
