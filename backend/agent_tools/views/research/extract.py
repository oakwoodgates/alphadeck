"""``get_extract``: the extractor's three candidate scoring facts for one company (``GET /workbench/securities/{id}/extract``,
undated; ``?thesis_id=`` only when the caller gave a card).

The read SPENDS: up to 4 EDGAR requests when the filings are not cached and, with a card, one LLM call (the grounded
purity proposal). The candidates are the extractor's LOCATIONS, not facts: the operator ratifies in the Workbench. The
extract is DB-free, so a candidate does not know whether its fact is already on file. The default reply keeps each
fact with at most two passages, every excerpt cut at 300 characters with its full length beside it; ``section="passages"``
with ``fact`` reads one fact's passages in full. Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, pick, put_cut

SECTIONS = (("passages", "fact"),)
FACTS = ("revenue_mix", "shares_outstanding", "cash_burn")

# the fields the views copy (a drift guard in the tests checks each against the API's wire model)
FACT_KEYS = (
    "fact_type",
    "tier",
    "source",
    "source_ref",
    "event_date",
    "note",
    "value",
    "cash_usd",
    "quarterly_burn_usd",
    "statement_currency",
    "flags",
    "located_passages",
    "estimate_source",
    "ads_ratio",
    "ads_ratio_status",
)
PASSAGE_KEYS = ("kind", "source_ref", "anchor", "excerpt", "offset")
BODY_KEYS = ("facts", "empty_reason", "runway_empty_reason")
IDENTITY_KEYS = ("security_id", "ticker", "name")

NOTE_CUT = 200
ANCHOR_CUT = 80
EXCERPT_CUT = 300
PASSAGES_SHOWN = 2
# present only when the extractor set them
OPTIONAL = ("statement_currency", "estimate_source", "ads_ratio", "ads_ratio_status")


def _passage(p: dict[str, Any], *, full: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": p.get("kind"), "source_ref": p.get("source_ref")}
    if full:
        out["anchor"] = p.get("anchor")
        out["excerpt"] = p.get("excerpt")
    else:
        put_cut(out, "anchor", p.get("anchor"), ANCHOR_CUT)
        put_cut(out, "excerpt", p.get("excerpt"), EXCERPT_CUT)
    out["offset"] = p.get("offset")
    return out


def _fact(f: dict[str, Any]) -> dict[str, Any]:
    passages = f.get("located_passages") or []
    out: dict[str, Any] = pick(
        f, ("fact_type", "tier", "source", "source_ref", "event_date", "value")
    )
    out["cash_usd"] = f.get("cash_usd")
    out["quarterly_burn_usd"] = f.get("quarterly_burn_usd")
    put_cut(out, "note", f.get("note"), NOTE_CUT)
    out["flags"] = list(f.get("flags") or [])
    for key in OPTIONAL:
        if f.get(key) is not None:
            out[key] = f[key]
    out["passages_total"] = len(passages)
    out["passages"] = [_passage(p, full=False) for p in passages[:PASSAGES_SHOWN]]
    if len(passages) > PASSAGES_SHOWN:
        out["more"] = f"section=passages fact={f.get('fact_type')}"
    return out


def view(payload: dict[str, Any], identity: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    facts = payload.get("facts") or []
    reply = header(req)
    reply["identity"] = pick(identity, IDENTITY_KEYS)
    reply["thesis_scoped"] = req.thesis is not None
    if req.section is None:
        reply["facts"] = [_fact(f) for f in facts]
        reply["empty_reason"] = payload.get("empty_reason")
        reply["runway_empty_reason"] = payload.get("runway_empty_reason")
        reply["sections"] = dict(SECTIONS)
        return fit(reply, BUDGET, [])
    if req.section != "passages":
        raise ViewError(f"Unknown section {req.section!r}.")
    chosen = next((f for f in facts if f.get("fact_type") == req.fact), None)
    if chosen is None:
        held = ", ".join(str(f.get("fact_type")) for f in facts) or "no fact"
        raise ViewError(f"This extract holds no {req.fact} fact; it holds: {held}.")
    passages = chosen.get("located_passages") or []
    reply["section"] = "passages"
    reply["fact"] = req.fact
    reply["passages_total"] = len(passages)
    reply["passages"] = [_passage(p, full=True) for p in passages[req.cursor :]]
    reply["sections"] = dict(SECTIONS)
    return fit(
        reply, BUDGET, [TrimRule(("passages",), min_keep=1, start=req.cursor, hint="cursor")]
    )
