"""``get_triage``: what is left to ratify on a card (``GET /workbench/theses/{id}/scored``, dated; compute-on-read).

The default reply keeps the funnel per segment, the counts and the ONE paged list of member rows in the API's order
(never sorted). ``section="member"`` reads one name in full with every meter's provenance; ``segment``,
``unconfirmed`` and ``silent`` page the member rows that match. A silent segment is recognized by its FIXED label
(``domain.thesis``): a comparison, not a judgment. Nothing here is a call input: the meters are the operator's to
ratify. Every value is copied from the API's JSON; this module counts entries, filters rows on a copied field and cuts
strings.
"""

from __future__ import annotations

from typing import Any

from domain.thesis import SILENT_LABELS

from ...budget import TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, pick, put_cut

SECTIONS = (("member", "t"), ("segment", "segment"), ("unconfirmed", None), ("silent", None))
METERS = ("purity", "runway", "catalysts", "dilution", "market_cap")
PIP_METERS = ("purity", "runway", "catalysts", "dilution")

# the fields the views copy (a drift guard in the tests checks each against the API's wire model)
IDENTITY_KEYS = (
    "security_id",
    "ticker",
    "name",
    "sector",
    "exchange",
    "category",
    "origin",
    "foreign_filer_form",
    "price_symbol",
    "business_type",
    "business_supersector",
    "business_type_override",
    "royalty",
    "instrument_kind",
    "segment",
)
ROW_KEYS = (
    "ticker",
    "name",
    "segment",
    "business_type",
    "instrument_kind",
    "fit",
    "unconfirmed_estimates",
    "thin_price_history",
    "price_feed_limitation",
)
FIGURE_KEYS = ("pips", "value", "provenance")
PROVENANCE_KEYS = ("source", "ref", "url", "detail")
SEGMENT_KEYS = ("label", "descriptor")
BODY_KEYS = ("segments", "members")

NAME_CUT = 30
DESCRIPTOR_CUT = 80
DETAIL_CUT = 200


def is_silent(label: str | None) -> bool:
    return label in SILENT_LABELS


def _sources(m: dict[str, Any]) -> list[str]:
    """The distinct provenance source strings across the meters, in first-seen order."""
    seen: list[str] = []
    for meter in METERS:
        for p in (m.get(meter) or {}).get("provenance") or []:
            source = p.get("source")
            if source is not None and source not in seen:
                seen.append(source)
    return seen


def _row(m: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ticker": m.get("ticker")}
    out["name"] = (m.get("name") or "")[:NAME_CUT] if m.get("name") is not None else None
    out["segment"] = m.get("segment")
    out["business_type"] = m.get("business_type")
    if m.get("instrument_kind") not in (None, "equity"):
        out["instrument_kind"] = m["instrument_kind"]
    out["fit"] = m.get("fit")
    out["unconfirmed_estimates"] = m.get("unconfirmed_estimates")
    meters: dict[str, Any] = {k: (m.get(k) or {}).get("pips") for k in PIP_METERS}
    meters["market_cap"] = (m.get("market_cap") or {}).get("value")
    out["meters"] = meters
    out["sources"] = _sources(m)
    if m.get("thin_price_history"):
        out["thin_price_history"] = True
    if m.get("price_feed_limitation"):
        out["price_feed_limitation"] = m["price_feed_limitation"]
    return out


def _detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """A provenance ``detail`` with its top-level strings cut at 200 (``<key>_total_chars`` rides beside a cut one)."""
    out: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if isinstance(value, str):
            put_cut(out, key, value, DETAIL_CUT)
        else:
            out[key] = value
    return out


def _figure(fig: dict[str, Any] | None) -> dict[str, Any]:
    fig = fig or {}
    return {
        "pips": fig.get("pips"),
        "value": fig.get("value"),
        "provenance": [
            {**pick(p, ("source", "ref", "url")), "detail": _detail(p.get("detail"))}
            for p in fig.get("provenance") or []
        ],
    }


def _full(m: dict[str, Any]) -> dict[str, Any]:
    out = pick(m, IDENTITY_KEYS)
    for meter in METERS:
        out[meter] = _figure(m.get(meter))
    out["fit"] = m.get("fit")
    out["unconfirmed_estimates"] = m.get("unconfirmed_estimates")
    out["thin_price_history"] = bool(m.get("thin_price_history"))
    out["price_feed_limitation"] = m.get("price_feed_limitation")
    return out


def _funnel(payload: dict[str, Any], members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per segment, in the API's order: how many members, how many are confirmed (no unconfirmed estimate) and how
    many are not. A label only members carry (a pen the API's segment list omits) follows with no descriptor.
    """
    labels: list[tuple[str, str | None]] = [
        (s.get("label"), s.get("descriptor")) for s in payload.get("segments") or []
    ]
    known = {label for label, _ in labels}
    for m in members:
        if m.get("segment") not in known:
            known.add(m.get("segment"))
            labels.append((m.get("segment"), None))
    out = []
    for label, descriptor in labels:
        rows = [m for m in members if m.get("segment") == label]
        unconfirmed = sum(1 for m in rows if (m.get("unconfirmed_estimates") or 0) > 0)
        entry: dict[str, Any] = {"label": label}
        put_cut(entry, "descriptor", descriptor, DESCRIPTOR_CUT)
        entry.update(
            {
                "silent": is_silent(label),
                "members_total": len(rows),
                "confirmed": sum(1 for m in rows if (m.get("unconfirmed_estimates") or 0) == 0),
                "unconfirmed": unconfirmed,
            }
        )
        out.append(entry)
    return out


def _counts(members: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "members_total": len(members),
        "silent_rows": sum(1 for m in members if is_silent(m.get("segment"))),
        "unconfirmed_members": sum(1 for m in members if (m.get("unconfirmed_estimates") or 0) > 0),
        "thin_price_history": sum(1 for m in members if m.get("thin_price_history")),
        "price_feed_limitation": sum(1 for m in members if m.get("price_feed_limitation")),
    }


def _segment_members(members: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    rows = [m for m in members if m.get("segment") == label]
    if rows:
        return rows
    folded = label.strip().casefold()
    rows = [m for m in members if (m.get("segment") or "").casefold() == folded]
    if rows:
        return rows
    raise ViewError(
        f"No segment {label!r} on this card; the labels are: "
        + ", ".join(sorted({str(m.get("segment")) for m in members}))
        + "."
    )


def _paged(
    reply: dict[str, Any], rows: list[dict[str, Any]], req: ResearchRequest, build=_row
) -> dict[str, Any]:
    reply["members_total"] = len(rows)
    reply["members"] = [build(m) for m in rows[req.cursor :]]
    reply["sections"] = dict(SECTIONS)
    return fit(reply, BUDGET, [TrimRule(("members",), min_keep=1, start=req.cursor, hint="cursor")])


def view(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    members = payload.get("members") or []
    reply = header(req)
    if req.section is None:
        if (
            req.cursor == 0
        ):  # the funnel and the counts ride the first page only: a page after it is rows
            reply["segments"] = _funnel(payload, members)
            reply["counts"] = _counts(members)
        shown = members
        if req.segment is not None:
            shown = _segment_members(members, req.segment)
            reply["narrowed"] = {"segment": req.segment}
        return _paged(reply, shown, req)
    reply["section"] = req.section
    if req.section == "member":
        wanted = (req.t or "").strip().casefold()
        found = [m for m in members if (m.get("ticker") or "").casefold() == wanted]
        if not found:
            raise ViewError(f"No member with ticker {req.t!r} on this card.")
        return _paged(reply, found, req, build=_full)
    if req.section == "segment":
        reply["narrowed"] = {"segment": req.segment}
        return _paged(reply, _segment_members(members, req.segment or ""), req)
    if req.section == "unconfirmed":
        rows = [m for m in members if (m.get("unconfirmed_estimates") or 0) > 0]
        return _paged(reply, rows, req)
    if req.section == "silent":
        return _paged(reply, [m for m in members if is_silent(m.get("segment"))], req)
    raise ViewError(f"Unknown section {req.section!r}.")
