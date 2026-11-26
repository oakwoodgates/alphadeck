"""``get_insider_holds``: the steward's Held insider rows panel (``GET /admin/insider-holds``, undated, read live).

A held row is a Form 4 transaction the ingest held because the filing's own date cannot be true. The API buckets
each one with the same ladder the insider read runs: ``owed`` (the one loud bucket: no decision, not placed by the
rule), ``placed_by_rule``, ``filer_corrected``, ``decided``, ``dismissed`` and ``amendment_row``. The default reply
keeps the counts, the buckets in the backend's words and the owed rows (brief), trimmed to the budget with the
cursor to read on; a bucket section pages that bucket's brief rows in the API's order; ``one`` with a ticker reads
that ticker's rows in full, every bucket, with each row's history of decisions.

Every value is copied from the API's JSON: the view never sorts, derives a date or decides whether a suggested date
is right (``suggested_date``, ``agrees``, ``suggestion_basis``, ``suggestion_words`` and the filing's window
``window_from`` / ``window_to`` are the API's own reading of the filing's fields, ``copies`` its other securities'
holds of the same row, and the dates "as filed" are the filing's text verbatim: evidence, never a correction). A
decision's ``decided_by`` is the steward's user id and display name, never an email (the API's own rule).
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, pick, put_cut

BUCKETS = (
    "owed",
    "placed_by_rule",
    "filer_corrected",
    "decided",
    "dismissed",
    "amendment_row",
)
SECTIONS = tuple((b, None) for b in BUCKETS) + (("one", "t"),)

T_RULE = "t narrows section=one only."

OWED_WHY_BRIEF = 200
SUGGESTION_WORDS_BRIEF = 200
WHY_BRIEF = 200
DECISION_REASON_BRIEF = 200
FULL_CUT = 600
DECISION_REASON_FULL = 300
HISTORY_SHOWN = 10

BRIEF_KEYS = (
    "hold_id",
    "ticker",
    "company_name",
    "accession",
    "txn_seq",
    "form",
    "reason_words",
    "row",
    "date_as_filed",
    "period_of_report_as_filed",
    "signature_date_as_filed",
    "filed",
    "market_date",
    "suggested_date",
    "agrees",
    "suggestion_basis",
    "window_from",
    "window_to",
    "copies",
    "filing_url",
)
DECISION_KEYS = ("id", "action", "ratified_date", "recorded_at", "decided_by")


def _decision(d: dict[str, Any] | None, cut: int) -> dict[str, Any] | None:
    """One steward decision: its fields copied, the reason cut with its full length beside it."""
    if d is None:
        return None
    out = pick(d, DECISION_KEYS)
    put_cut(out, "reason", d.get("reason"), cut)
    return out


def _brief(h: dict[str, Any], bucket: str) -> dict[str, Any]:
    out = pick(h, BRIEF_KEYS)
    put_cut(out, "suggestion_words", h.get("suggestion_words"), SUGGESTION_WORDS_BRIEF)
    if bucket == "owed":
        put_cut(out, "owed_why", h.get("owed_why"), OWED_WHY_BRIEF)
    elif bucket == "filer_corrected":
        put_cut(out, "why", h.get("why"), WHY_BRIEF)
    elif bucket == "placed_by_rule":
        out["filing_night"] = h.get("filing_night")
    elif bucket in ("decided", "dismissed"):
        out["decision"] = _decision(h.get("decision"), DECISION_REASON_BRIEF)
    return out


def _full(h: dict[str, Any]) -> dict[str, Any]:
    """The whole row: every field the API carries, the long strings cut, the history capped (its total beside it)."""
    out = {
        k: v
        for k, v in h.items()
        if k not in ("owed_why", "why", "suggestion_words", "decision", "history")
    }
    put_cut(out, "owed_why", h.get("owed_why"), FULL_CUT)
    put_cut(out, "suggestion_words", h.get("suggestion_words"), FULL_CUT)
    put_cut(out, "why", h.get("why"), FULL_CUT)
    out["decision"] = _decision(h.get("decision"), DECISION_REASON_FULL)
    history = h.get("history") or []
    out["history_total"] = len(history)
    out["history"] = [_decision(d, DECISION_REASON_FULL) for d in history[:HISTORY_SHOWN]]
    return out


def _matches(ticker: str, h: dict[str, Any]) -> bool:
    return (h.get("ticker") or "").upper() == ticker


def view(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    if req.t is not None and req.section != "one":
        raise ViewError(T_RULE)
    lists = {b: payload.get(b) or [] for b in BUCKETS}
    reply = header(req)
    if req.section is None:
        reply["counts"] = payload.get("counts")
        reply["total"] = payload.get("total")
        reply["bucket_words"] = payload.get("bucket_words")
        reply["owed"] = [_brief(h, "owed") for h in lists["owed"]]
        reply["sections"] = dict(SECTIONS)
        return fit(reply, BUDGET, [TrimRule(("owed",), min_keep=1, hint="section=owed")])
    if req.section in BUCKETS:
        rows = lists[req.section]
        reply["section"] = req.section
        reply["total"] = len(rows)
        reply[req.section] = [_brief(h, req.section) for h in rows[req.cursor :]]
        reply["sections"] = dict(SECTIONS)
        return fit(
            reply,
            BUDGET,
            [TrimRule((req.section,), min_keep=1, start=req.cursor, hint="cursor")],
        )
    if req.section == "one":
        ticker = (req.t or "").strip().upper()
        found = [h for rows in lists.values() for h in rows if _matches(ticker, h)]
        reply["section"] = "one"
        reply["t"] = req.t
        reply["found"] = len(found)
        reply["holds"] = [_full(h) for h in found[req.cursor :]]
        reply["sections"] = dict(SECTIONS)
        return fit(
            reply, BUDGET, [TrimRule(("holds",), min_keep=1, start=req.cursor, hint="cursor")]
        )
    raise ViewError(f"Unknown section {req.section!r}.")
