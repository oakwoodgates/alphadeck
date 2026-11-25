"""``get_splits``: the steward's split list (``GET /admin/splits``, undated, read live from the shared data layer).

``review_reason`` (the API's: sources_differ, extractor_abstained or one_source) rides every needs-review row,
``counts.needs_review_by_reason`` counts it and ``reason=`` narrows ``section=needs_review`` to one value.
The default reply keeps the counts, the whole needs-review list (the panel's loud one) and the first unlisted jumps;
a section pages one bucket in the API's order; ``one`` with a ticker reads that ticker's every entry and jump in full,
each open entry (needs review, announced) with ``tape``: the API's stored window around its first session (``GET
/admin/splits/{version_id}/tape``, read by the server for at most ``TAPE_READS`` entries a call, from the cursor on;
``tape_error`` names a read that failed).
Every value is copied from the API's JSON: the view never sorts, derives a date or a ratio, or decides whether the two
sources agree (the model reads the two side by side; ``confirm_options`` is the API's statement of what can be
confirmed). ``name`` is the shared master's company name, which the Admin panel shows. A split the steward confirmed
with cited values (``value_source`` ``steward``) carries the API's ``cited_url`` and ``tape_override`` in every reply.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, paged

SECTIONS = (
    ("needs_review", None),
    ("announced", None),
    ("confirmed", None),
    ("set_aside", None),
    ("jumps", None),
    ("one", "t"),
)
BUCKETS = ("needs_review", "announced", "confirmed", "set_aside")
# why a needs-review split waits: the API's own ``review_reason`` (the view copies and counts it, never derives it)
REASONS = ("sources_differ", "extractor_abstained", "one_source")
REASON_RULE = "reason must be one of: " + ", ".join(REASONS) + "."

T_RULE = "t narrows section=one only."
REASON_SECTION_RULE = "reason narrows section=needs_review only."
# section=one: the open entries whose stored tape window the server reads, at most this many a call
TAPE_BUCKETS = ("needs_review", "announced")
TAPE_READS = 3
PASSAGE_BRIEF = 300
PASSAGE_FULL = 1_500
NOTE_BRIEF = 200
REASON_BRIEF = 120
JUMPS_SHOWN = 5

SEC_BRIEF_KEYS = ("form", "filed", "date_kind", "effective_date", "ratio_label", "abstain_reason")
ENTRY_BRIEF_KEYS = (
    "ticker",
    "name",
    "key_date",
    "effective_date",
    "label",
    "status",
    "direction",
    "decided_by",
    "review_reason",
)
JUMP_BRIEF_KEYS = (
    "ticker",
    "prior_date",
    "step_date",
    "prior_close",
    "close",
    "step",
    "label",
    "sessions_since",
)


def _passage(sec: dict[str, Any], limit: int) -> dict[str, Any]:
    """The SEC block with its located passage cut at ``limit`` and the full length beside it."""
    text = sec.get("passage")
    return {
        "passage": None if text is None else text[:limit],
        "passage_chars": None if text is None else len(text),
    }


def _brief_entry(e: dict[str, Any], bucket: str) -> dict[str, Any]:
    out = {k: e.get(k) for k in ENTRY_BRIEF_KEYS}
    sec = e.get("sec")
    out["sec"] = (
        None
        if sec is None
        else {**{k: sec.get(k) for k in SEC_BRIEF_KEYS}, **_passage(sec, PASSAGE_BRIEF)}
    )
    poly = e.get("polygon")
    out["polygon"] = (
        None
        if poly is None
        else {"execution_date": poly.get("execution_date"), "ratio_label": poly.get("ratio_label")}
    )
    out["confirm_options"] = [o.get("label") for o in e.get("confirm_options") or []]
    out["actions"] = e.get("actions") or []
    # the steward's cited values (D3): the document and the tape override
    if e.get("cited_url") is not None:
        out["cited_url"] = e.get("cited_url")
        out["tape_override"] = e.get("tape_override")
    if bucket == "set_aside":
        out["restore_to"] = e.get("restore_to")
        note = e.get("note")
        out["note"] = None if note is None else note[:NOTE_BRIEF]
        if note is not None and len(note) > NOTE_BRIEF:
            out["note_total_chars"] = len(note)
    return out


def _full_entry(e: dict[str, Any]) -> dict[str, Any]:
    out = dict(e)  # ``tape`` / ``tape_error`` (section=one's open entries) ride whole
    sec = e.get("sec")
    if sec is not None:
        out["sec"] = {**sec, **_passage(sec, PASSAGE_FULL)}
    return out


def _brief_jump(j: dict[str, Any]) -> dict[str, Any]:
    out = {k: j.get(k) for k in JUMP_BRIEF_KEYS}
    if j.get("dismissed_at") is not None or j.get("dismissal_id") is not None:
        out["dismissed_at"] = j.get("dismissed_at")
        reason = j.get("dismissed_reason")
        out["dismissed_reason"] = None if reason is None else reason[:REASON_BRIEF]
    return out


def _matches(ticker: str, e: dict[str, Any]) -> bool:
    master = e.get("ticker")
    if master is None:
        master = (e.get("polygon") or {}).get("ticker")
    return master is not None and master.upper() == ticker


def tape_targets(payload: dict[str, Any], t: str, cursor: int = 0) -> list[dict[str, Any]]:
    """The open entries (needs review, then announced) of ticker ``t`` whose tape window section=one attaches, from
    ``cursor`` on, at most ``TAPE_READS``. Open entries come first in section=one's order, so the cursor indexes
    them directly: a later page reads the ones an earlier page did not show."""
    ticker = t.strip().upper()
    found = [e for b in TAPE_BUCKETS for e in payload.get(b) or [] if _matches(ticker, e)]
    return found[cursor:][:TAPE_READS]


def view(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    if req.t is not None and req.section != "one":
        raise ViewError(T_RULE)
    if req.reason is not None:
        if req.reason not in REASONS:
            raise ViewError(REASON_RULE)
        if req.section != "needs_review":
            raise ViewError(REASON_SECTION_RULE)
    lists = {b: payload.get(b) or [] for b in BUCKETS}
    unlisted = payload.get("unlisted_jumps") or []
    dismissed = payload.get("dismissed_jumps") or []
    reply = header(req)
    if req.section is None:
        reply["counts"] = {
            **{b: len(rows) for b, rows in lists.items()},
            "unlisted_jumps": len(unlisted),
            "dismissed_jumps": len(dismissed),
            "total": payload.get("total"),
            "needs_review_by_reason": {
                r: sum(1 for e in lists["needs_review"] if e.get("review_reason") == r)
                for r in REASONS
            },
        }
        if payload.get("unlisted_jumps_error") is not None:
            reply["unlisted_jumps_error"] = payload.get("unlisted_jumps_error")
        reply["needs_review"] = [_brief_entry(e, "needs_review") for e in lists["needs_review"]]
        reply["jumps"] = [_brief_jump(j) for j in unlisted[:JUMPS_SHOWN]]
        reply["sections"] = dict(SECTIONS)
        return fit(
            reply,
            BUDGET,
            [
                TrimRule(("needs_review",), min_keep=1, hint="section=needs_review"),
                TrimRule(("jumps",), min_keep=0, hint="section=jumps"),
            ],
        )
    if req.section in BUCKETS:
        rows = lists[req.section]
        if req.reason is not None:
            rows = [e for e in rows if e.get("review_reason") == req.reason]
            reply["narrowed"] = {"reason": req.reason}
        reply["section"] = req.section
        reply["total"] = len(rows)
        reply[req.section] = [_brief_entry(e, req.section) for e in rows[req.cursor :]]
        reply["sections"] = dict(SECTIONS)
        return fit(
            reply,
            BUDGET,
            [TrimRule((req.section,), min_keep=1, start=req.cursor, hint="cursor")],
        )
    if req.section == "jumps":
        reply["section"] = "jumps"
        return paged(
            reply,
            [
                ("unlisted", [_brief_jump(j) for j in unlisted]),
                ("dismissed", [_brief_jump(j) for j in dismissed]),
            ],
            req.cursor,
            "jumps",
        )
    if req.section == "one":
        ticker = (req.t or "").strip().upper()
        entries = [_full_entry(e) for rows in lists.values() for e in rows if _matches(ticker, e)]
        jumps = [j for j in [*unlisted, *dismissed] if (j.get("ticker") or "").upper() == ticker]
        reply["section"] = "one"
        reply["t"] = req.t
        reply["found"] = len(entries)
        return paged(reply, [("entries", entries), ("jumps", jumps)], req.cursor, "one")
    raise ViewError(f"Unknown section {req.section!r}.")
