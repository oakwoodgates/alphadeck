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
An open entry (needs review, announced) carries the API's ``first_bar`` (the first stored bar: a split on or before it
adjusts no tape) and ``shares_rescaled`` (the share count a confirm puts on the new scale) in every reply, when stated.
The brief ``polygon`` block carries Massive.com's ``adjustment_type`` label when the API states one (W6-1).
F1-4: ``unplaced`` is the API's SEC filings about a split the list could not place (newest filing first), each with
``listed_near`` (the split on the list it most likely concerns, as the API states it) and the steward's standing
dismissal, if any. The default reply counts them (``unplaced_on_list``: ``listed_near`` cites the filing or states its
ratio) and briefs the first ``UNPLACED_SHOWN``, dropped before any needs-review row when the reply is too large;
``section=unplaced`` pages the listed then the dismissed ones; ``one`` adds the ticker's, in full.
F8-3: an open entry carries the API's ``same_ratio_listed`` when stated (the other splits on the list of its exact
ratio within 180 days of its first session, nearest first, at most two), each brief like ``listed_near`` in every
reply but ``one``, which carries it whole: display only, which one went ahead is the steward's.
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
    ("unplaced", None),
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
UNPLACED_SHOWN = 5

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
UNPLACED_BRIEF_KEYS = (
    "ticker",
    "name",
    "accession",
    "form",
    "filed",
    "reason",
    "ratio_label",
    "source",
    "seen_on",
)
# the brief ``listed_near`` (and each brief ``same_ratio_listed`` entry, F8-3): the version id and key date ride
# section=one's full row only
NEAR_BRIEF_KEYS = ("effective_date", "label", "status", "same_ratio", "cites_filing", "days_from")
# W2-8: what a confirm would touch, as the API states it on an open entry (copied when stated, never derived)
HINT_KEYS = ("first_bar", "shares_rescaled")


def _hints(e: dict[str, Any]) -> dict[str, Any]:
    return {k: e[k] for k in HINT_KEYS if e.get(k) is not None}


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
        else {
            "execution_date": poly.get("execution_date"),
            "ratio_label": poly.get("ratio_label"),
            # W6-1: Massive.com's own label, copied when stated
            **({"adjustment_type": poly["adjustment_type"]} if poly.get("adjustment_type") else {}),
        }
    )
    out["confirm_options"] = [o.get("label") for o in e.get("confirm_options") or []]
    out["actions"] = e.get("actions") or []
    # the steward's cited values (D3): the document and the tape override
    if e.get("cited_url") is not None:
        out["cited_url"] = e.get("cited_url")
        out["tape_override"] = e.get("tape_override")
    out.update(_hints(e))
    # F8-3: the other listed splits of its exact ratio near it, as the API states them (an open entry, when any)
    same = e.get("same_ratio_listed")
    if same:
        out["same_ratio_listed"] = [{k: s.get(k) for k in NEAR_BRIEF_KEYS} for s in same]
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


def _brief_unplaced(u: dict[str, Any]) -> dict[str, Any]:
    out = {k: u.get(k) for k in UNPLACED_BRIEF_KEYS}
    near = u.get("listed_near")
    out["listed_near"] = None if near is None else {k: near.get(k) for k in NEAR_BRIEF_KEYS}
    out.update(_passage(u, PASSAGE_BRIEF))
    if u.get("dismissed_at") is not None or u.get("dismissal_id") is not None:
        out["dismissed_at"] = u.get("dismissed_at")
        reason = u.get("dismissed_reason")
        out["dismissed_reason"] = None if reason is None else reason[:REASON_BRIEF]
    return out


def _full_unplaced(u: dict[str, Any]) -> dict[str, Any]:
    return {**u, **_passage(u, PASSAGE_FULL)}


def _on_list(u: dict[str, Any]) -> bool:
    """The API's ``listed_near`` cites this filing or is the exact ratio it states (copied flags, never derived)."""
    near = u.get("listed_near") or {}
    return bool(near.get("cites_filing")) or near.get("same_ratio") is True


def _fit_default(reply: dict[str, Any]) -> dict[str, Any]:
    """The default reply within the budget, the quiet unplaced briefs cut first: the needs-review rows and the jumps
    are fitted without them (their cursor as before); only when every one of those fits do the unplaced briefs take
    what room is left (a cut names ``unplaced``, the cursor section=unplaced continues from)."""
    loud = fit(
        {**reply, "unplaced": []},
        BUDGET,
        [
            TrimRule(("needs_review",), min_keep=1, hint="section=needs_review"),
            TrimRule(("jumps",), min_keep=0, hint="section=jumps"),
        ],
    )
    if loud["next_cursor"] is not None or not reply["unplaced"]:
        return loud
    return fit(reply, BUDGET, [TrimRule(("unplaced",), min_keep=0, hint="section=unplaced")])


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
    unplaced = payload.get("unplaced") or []
    unplaced_gone = payload.get("unplaced_dismissed") or []
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
            "unplaced": len(unplaced),
            "unplaced_dismissed": len(unplaced_gone),
            "unplaced_on_list": sum(1 for u in unplaced if _on_list(u)),
        }
        if payload.get("unlisted_jumps_error") is not None:
            reply["unlisted_jumps_error"] = payload.get("unlisted_jumps_error")
        if payload.get("unplaced_error") is not None:
            reply["unplaced_error"] = payload.get("unplaced_error")
        reply["needs_review"] = [_brief_entry(e, "needs_review") for e in lists["needs_review"]]
        reply["jumps"] = [_brief_jump(j) for j in unlisted[:JUMPS_SHOWN]]
        reply["unplaced"] = [_brief_unplaced(u) for u in unplaced[:UNPLACED_SHOWN]]
        reply["sections"] = dict(SECTIONS)
        return _fit_default(reply)
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
    if req.section == "unplaced":
        reply["section"] = "unplaced"
        return paged(
            reply,
            [
                ("unplaced", [_brief_unplaced(u) for u in unplaced]),
                ("unplaced_dismissed", [_brief_unplaced(u) for u in unplaced_gone]),
            ],
            req.cursor,
            "unplaced",
        )
    if req.section == "one":
        ticker = (req.t or "").strip().upper()
        entries = [_full_entry(e) for rows in lists.values() for e in rows if _matches(ticker, e)]
        jumps = [j for j in [*unlisted, *dismissed] if (j.get("ticker") or "").upper() == ticker]
        filings = [
            _full_unplaced(u)
            for u in [*unplaced, *unplaced_gone]
            if (u.get("ticker") or "").upper() == ticker
        ]
        reply["section"] = "one"
        reply["t"] = req.t
        reply["found"] = len(entries)
        return paged(
            reply,
            [("entries", entries), ("jumps", jumps), ("unplaced", filings)],
            req.cursor,
            "one",
        )
    raise ViewError(f"Unknown section {req.section!r}.")
