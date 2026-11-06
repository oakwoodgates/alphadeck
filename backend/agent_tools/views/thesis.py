"""``get_thesis``: the thesis spine from ``GET /theses/{id}`` (current metadata, not dated).

Members are grouped under their link (a member's ``segment`` is its link label), in the thesis's link order: every
real link in ``segments`` order, then any label not in ``segments`` (the Discovered pen, in first-seen order), then
the three silent links (Context, ETFs, Bench, read from ``domain.thesis``) whose names never count toward the call.
``cursor`` counts members across the links in that order, so a long basket continues where the reply stopped;
each link states ``member_count`` (all of its members, on this page or not).

Naming guard: a member's ``context_role`` is the operator's tag for the company's part on this card; the member's
free-text ``role`` prose is not shown (and neither is ever called "role" alone).
"""

from __future__ import annotations

from typing import Any, Literal

from domain.thesis import SILENT_LABELS

from ..budget import TrimRule, cut_text, fit

BUDGET_SUMMARY = 8_000
BUDGET_FULL = 24_000
NARRATIVE_LIMIT = 600
DESCRIPTOR_LIMIT = 120

Detail = Literal["summary", "full"]


def _ordered_labels(detail: dict[str, Any]) -> list[str | None]:
    silent = set(SILENT_LABELS)
    real = [s.get("label") for s in detail.get("segments") or [] if s.get("label") not in silent]
    seen = set(real)
    extra: list[str | None] = []
    for m in detail.get("basket") or []:
        label = m.get("segment")
        if label not in seen and label not in silent:
            seen.add(label)
            extra.append(label)
    return [*real, *extra, *SILENT_LABELS]


def view_thesis(
    detail: dict[str, Any],
    *,
    mode: Detail = "summary",
    cursor: int = 0,
    archived: bool | None = None,
) -> dict[str, Any]:
    full = mode == "full"
    descriptors = {s.get("label"): s.get("descriptor") for s in detail.get("segments") or []}
    basket = detail.get("basket") or []
    silent = set(SILENT_LABELS)
    segments: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for label in _ordered_labels(detail):
        group = [m for m in basket if m.get("segment") == label]
        if label in silent and not group and label not in descriptors:
            continue
        descriptor = descriptors.get(label)
        seg: dict[str, Any] = {"label": label, "silent": label in silent}
        if full:
            seg["descriptor"] = descriptor
        elif label in silent:
            # a silent link's descriptor is the fixed domain text ("never counts toward the call"): ``silent``
            # says it; detail=full shows the text
            seg["descriptor"] = None
        else:
            seg["descriptor"] = (descriptor or "")[:DESCRIPTOR_LIMIT] or None
            if descriptor and len(descriptor) > DESCRIPTOR_LIMIT:
                seg["descriptor_total_chars"] = len(descriptor)
        seg["member_count"] = len(group)
        segments.append(seg)
        for m in group:
            row = {
                "segment": label,
                "ticker": m.get("ticker"),
                "context_role": m.get("context_role"),
                "conviction": m.get("conviction"),
                "signed_off": m.get("signed_off"),
                "authored_by": m.get("authored_by"),
            }
            if full:
                row["thesis_fit"] = m.get("thesis_fit")
                row["detail"] = m.get("detail")
            members.append(row)
    terms = detail.get("term_set") or []
    narrative = detail.get("narrative") or ""
    reply: dict[str, Any] = {
        "asof": None,
        "source": "live",
        "detail": mode,
        "id": detail.get("id"),
        "name": detail.get("name"),
        "ticker": detail.get("ticker"),
        "archived": archived,
        "narrative": (
            {"text": narrative, "total_chars": len(narrative)}
            if full
            else cut_text(narrative, NARRATIVE_LIMIT)
        ),
        "segments": segments,
        "members_total": len(members),
        "members": members[cursor:],
        "catalysts": [
            {
                "label": c.get("label"),
                "kind": c.get("kind"),
                "when_date": c.get("when_date"),
                "when_label": c.get("when_label"),
            }
            for c in detail.get("catalysts") or []
        ],
        "kill_criteria": [k.get("text") for k in detail.get("kill_criteria") or []],
        "term_set": (
            [{"term": t.get("term"), "tier": t.get("tier")} for t in terms]
            if full
            else {
                "signal": [t.get("term") for t in terms if t.get("tier") == "signal"],
                "broad_count": sum(1 for t in terms if t.get("tier") == "broad"),
            }
        ),
        "position": detail.get("position"),
        "exclusions_count": len(detail.get("exclusions") or []),
    }
    budget = BUDGET_FULL if full else BUDGET_SUMMARY
    return fit(reply, budget, [TrimRule(("members",), min_keep=0, start=cursor)], render=_grouped)


def _grouped(working: dict[str, Any]) -> dict[str, Any]:
    """The final shape: this page's members under their links (the working reply keeps them flat for ``fit``)."""
    out = {k: v for k, v in working.items() if k != "members"}
    by_label: dict[Any, list[dict[str, Any]]] = {}
    for m in working["members"]:
        row = {k: v for k, v in m.items() if k != "segment"}
        by_label.setdefault(m["segment"], []).append(row)
    out["segments"] = [
        {**seg, "members": by_label.get(seg["label"], [])} for seg in working["segments"]
    ]
    return out
