"""``get_triage``: a card's meters and what is left on it (``GET /workbench/theses/{id}/scored``, dated;
compute-on-read), with each member's two SHARED values in their state's words (``GET /platform-values/theses/{id}``,
current; V2 W5d, the W5 plan's Q9).

The default reply keeps the funnel per segment, the counts and the ONE paged list of member rows in the API's order
(never sorted). Each row carries ``values``: the share count's and the cash's ``state`` and ``words`` (the API's
``label``, from ``platform_values.words``; a newer filing awaiting the steward rides as ``pending``), never a figure
the steward has not checked (approved B1 (b)) and never who set it beyond a kind. They are the CURRENT state, not as of
the asof (``values_asof`` says so). ``section="member"`` reads one name in full with every meter's provenance;
``segment``, ``awaiting`` (a shared value awaiting the steward or missing), ``needs_purity`` (no purity yet: the
user's own to set) and ``silent`` page the member rows that match. A silent segment is recognized by its FIXED label
(``domain.thesis``): a comparison, not a judgment. Nothing here is a call input. A shared value is set by the platform
and the steward; a user who thinks one is wrong flags it in the Workbench; purity is the user's own. Every value is
copied from the API's JSON; this module counts entries, filters rows on a copied field and cuts strings.
"""

from __future__ import annotations

from typing import Any

from domain.thesis import SILENT_LABELS

from ...budget import TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, pick, put_cut

SECTIONS = (
    ("member", "t"),
    ("segment", "segment"),
    ("awaiting", None),
    ("needs_purity", None),
    ("silent", None),
)
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
    "thin_price_history",
    "price_feed_limitation",
)
FIGURE_KEYS = ("pips", "value", "provenance")
PROVENANCE_KEYS = ("source", "ref", "url", "detail")
SEGMENT_KEYS = ("label", "descriptor")
BODY_KEYS = ("segments", "members")
# the user read's keys the views copy (``GET /platform-values/theses/{id}``; a drift guard checks them)
VALUES_BODY_KEYS = ("members",)
VALUES_MEMBER_KEYS = ("security_id", "shares", "cash_burn")
VALUE_KEYS = ("state", "reason", "label", "pending")
# a shared value that is not on a meter: awaiting the steward, or no value yet (a reason other than "runway does not
# apply": a cash-generative company has nothing missing)
OPEN_STATES = ("awaiting_steward", "no_value")
NOT_MISSING_REASONS = ("cash-generative",)
VALUES_ASOF = "current: the shared values' state today, not as of asof"

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


def _is_open(v: dict[str, Any]) -> bool:
    """A shared value not on a meter: awaiting the steward, or no value yet (not "runway does not apply")."""
    return v.get("state") in OPEN_STATES and v.get("reason") not in NOT_MISSING_REASONS


def values_by_company(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """``{security_id: {shares: {state, words[, pending]}, cash: {...}}}`` from the user read: the state and the
    API's own words (``label``), the pending line when a newer filing awaits the steward. No figure, no setter.
    A private ``_open`` key marks a company with a value awaiting the steward or missing (never copied out).
    """
    out: dict[str, dict[str, Any]] = {}
    for c in (payload or {}).get("members") or []:
        entry: dict[str, Any] = {}
        open_ = False
        for key, field in (("shares", "shares"), ("cash", "cash_burn")):
            v = c.get(field) or {}
            if not v:
                continue
            said: dict[str, Any] = {"state": v.get("state"), "words": v.get("label")}
            pending = v.get("pending") or {}
            if pending.get("line"):
                said["pending"] = pending["line"]
            entry[key] = said
            open_ = open_ or _is_open(v)
        if c.get("security_id") is not None:
            out[str(c["security_id"])] = {**entry, "_open": open_}
    return out


def _values_of(m: dict[str, Any], values: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    entry = (values or {}).get(str(m.get("security_id"))) or {}
    return {k: v for k, v in entry.items() if k != "_open"}


def awaits(m: dict[str, Any], values: dict[str, dict[str, Any]]) -> bool:
    """A member with a shared value awaiting the steward or missing (the user read's state)."""
    return bool((values.get(str(m.get("security_id"))) or {}).get("_open"))


def needs_purity(m: dict[str, Any]) -> bool:
    """No purity yet (no pip and no value): the user's own to set in the Workbench."""
    purity = m.get("purity") or {}
    return purity.get("pips") is None and purity.get("value") is None


def _row(m: dict[str, Any], values: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"ticker": m.get("ticker")}
    out["name"] = (m.get("name") or "")[:NAME_CUT] if m.get("name") is not None else None
    out["segment"] = m.get("segment")
    out["business_type"] = m.get("business_type")
    if m.get("instrument_kind") not in (None, "equity"):
        out["instrument_kind"] = m["instrument_kind"]
    out["fit"] = m.get("fit")
    meters: dict[str, Any] = {k: (m.get(k) or {}).get("pips") for k in PIP_METERS}
    meters["market_cap"] = (m.get("market_cap") or {}).get("value")
    out["meters"] = meters
    out["sources"] = _sources(m)
    said = _values_of(m, values)
    if said:
        out["values"] = said
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


def _full(m: dict[str, Any], values: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    out = pick(m, IDENTITY_KEYS)
    for meter in METERS:
        out[meter] = _figure(m.get(meter))
    out["fit"] = m.get("fit")
    said = _values_of(m, values)
    if said:
        out["values"] = said
    out["thin_price_history"] = bool(m.get("thin_price_history"))
    out["price_feed_limitation"] = m.get("price_feed_limitation")
    return out


def _funnel(
    payload: dict[str, Any], members: list[dict[str, Any]], values: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per segment, in the API's order: how many members, how many have a shared value awaiting the steward or
    missing (``awaiting``), how many have no purity yet (``needs_purity``). A label only members carry (a pen the
    API's segment list omits) follows with no descriptor.
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
        entry: dict[str, Any] = {"label": label}
        put_cut(entry, "descriptor", descriptor, DESCRIPTOR_CUT)
        entry.update(
            {
                "silent": is_silent(label),
                "members_total": len(rows),
                "awaiting": sum(1 for m in rows if awaits(m, values)),
                "needs_purity": sum(1 for m in rows if needs_purity(m)),
            }
        )
        out.append(entry)
    return out


def _counts(members: list[dict[str, Any]], values: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {
        "members_total": len(members),
        "silent_rows": sum(1 for m in members if is_silent(m.get("segment"))),
        "awaiting_members": sum(1 for m in members if awaits(m, values)),
        "needs_purity_members": sum(1 for m in members if needs_purity(m)),
        "thin_price_history": sum(1 for m in members if m.get("thin_price_history")),
        "price_feed_limitation": sum(1 for m in members if m.get("price_feed_limitation")),
    }


def _segment_members(
    payload: dict[str, Any], members: list[dict[str, Any]], label: str
) -> list[dict[str, Any]]:
    """The member rows of a link. The API's own segment list is matched FIRST (exact, then case-folded), so an empty
    link answers an empty list; then the labels only member rows carry (the Discovered pen)."""
    api_labels = [s.get("label") for s in payload.get("segments") or []]
    folded = label.strip().casefold()
    for matcher in (lambda x: x == label, lambda x: (x or "").casefold() == folded):
        for known in api_labels:
            if matcher(known):
                return [m for m in members if m.get("segment") == known]
        for m in members:
            if matcher(m.get("segment")):
                return [r for r in members if r.get("segment") == m.get("segment")]
    labels = list(api_labels)
    for m in members:
        if m.get("segment") not in labels:
            labels.append(m.get("segment"))
    raise ViewError(
        f"No segment {label!r} on this card; the labels are: " + ", ".join(map(str, labels)) + "."
    )


def _paged(
    reply: dict[str, Any],
    rows: list[dict[str, Any]],
    req: ResearchRequest,
    values: dict[str, dict[str, Any]],
    build=_row,
) -> dict[str, Any]:
    reply["members_total"] = len(rows)
    reply["members"] = [build(m, values) for m in rows[req.cursor :]]
    reply["sections"] = dict(SECTIONS)
    return fit(reply, BUDGET, [TrimRule(("members",), min_keep=1, start=req.cursor, hint="cursor")])


def view(
    payload: dict[str, Any], req: ResearchRequest, values_payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """``payload`` = the scored read; ``values_payload`` = the user read of the card's shared values (``None``: no
    ``values`` on the rows, the ``awaiting`` counts zero)."""
    members = payload.get("members") or []
    values = values_by_company(values_payload)
    reply = header(req)
    if values_payload is not None:
        reply["values_asof"] = VALUES_ASOF
    if req.section is None:
        if (
            req.cursor == 0
        ):  # the funnel and the counts ride the first page only: a page after it is rows
            reply["segments"] = _funnel(payload, members, values)
            reply["counts"] = _counts(members, values)
        shown = members
        if req.segment is not None:
            shown = _segment_members(payload, members, req.segment)
            reply["narrowed"] = {"segment": req.segment}
        return _paged(reply, shown, req, values)
    reply["section"] = req.section
    if req.section == "member":
        wanted = (req.t or "").strip().casefold()
        found = [m for m in members if (m.get("ticker") or "").casefold() == wanted]
        if not found:
            raise ViewError(f"No member with ticker {req.t!r} on this card.")
        return _paged(reply, found, req, values, build=_full)
    if req.section == "segment":
        reply["narrowed"] = {"segment": req.segment}
        return _paged(reply, _segment_members(payload, members, req.segment or ""), req, values)
    if req.section == "awaiting":
        return _paged(reply, [m for m in members if awaits(m, values)], req, values)
    if req.section == "needs_purity":
        return _paged(reply, [m for m in members if needs_purity(m)], req, values)
    if req.section == "silent":
        return _paged(reply, [m for m in members if is_silent(m.get("segment"))], req, values)
    raise ViewError(f"Unknown section {req.section!r}.")
