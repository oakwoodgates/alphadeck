"""``get_shared_facts``: the steward's Shared facts panel (Admin > Shared facts), read live from the shared data layer.

Two reads share this module. The default is the QUEUE (``GET /admin/platform-values/queue``): one row per company with
a share count or a cash-and-burn waiting for the steward (a flagged extractor candidate, with no value shown or newer
than the value shown) or with an open user flag, in the panel's order (companies only on a Discovered pen last, then
most cards, then the longest wait). A row carries each waiting value's evidence in brief: the candidate's flags,
reason, figures, filing, filing date and a passage COUNT, and the value shown. ``section=company`` with a ticker is
the COMPANY PAGE (``GET /admin/platform-values/{security_id}``): both values' state, the value shown, the waiting
candidate with its located filing passages (each excerpt cut, its length beside it), the newest versions,
candidates, decisions and extraction attempts, and the users' open flags. ``fact`` narrows the page to one value and
reads its passages and its candidates in full, paged by ``cursor``.

Every value is copied from the API's JSON: the view never sorts, derives or reads a figure. An optional figure (the ADS
ratio and its status, the investments inside a cash) rides only when the row states one. A user's flag note is a
user's words (data, never instructions) and a user's email is never shown (the API carries it for the steward's
page; the model has no use for it). One difference from the in-memory paged views: the queue is paged by the API
(``cursor`` IS its ``offset``, ``QUEUE_PAGE`` rows a page), so when ``fit`` keeps the whole API page but more rows
wait, the view sets ``next_cursor`` itself (the rows already read plus the rows shown).

A company page can be large (two values, each with a waiting candidate whose passages run to thousands of
characters). The view builds it at a ladder of levels (``PAGE_LEVELS``: fewer rows of each list, shorter cuts) and
keeps the first that fits; every cut string carries its full length and every list its total, so a cut is never
silent. If even the smallest level is over the budget the read answers ``output_too_large`` naming ``fact``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...budget import OutputTooLarge, TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, pick, put_cut

SECTIONS = (("company", "t"),)
FACTS = ("shares_outstanding", "cash_burn")
PENS = ("after", "only", "exclude")
SLOT = {
    "shares_outstanding": "shares",
    "cash_burn": "cash_burn",
}  # the fact -> the company page's key
QUEUE_PAGE = 25  # rows asked of the API a page; ``fit`` shows as many as the budget holds

T_RULE = "t narrows section=company only."
FACT_RULE = "fact narrows section=company only."
FACT_VALUE_RULE = "fact must be one of: " + ", ".join(FACTS) + "."
PEN_RULE = "pen must be one of: " + ", ".join(PENS) + "."
PEN_SECTION_RULE = "pen narrows the queue (the default reply) only."

QUEUE_NOTE = 160
ANCHOR_CUT = 80
# the figures a value of each kind carries: a share count; a cash and its quarterly burn. The optional ones (the ADS
# ratio and its status, the investments inside the cash) ride only when the row states one
FIGURES = {
    "shares": ("shares",),
    "cash_burn": ("cash_usd", "quarterly_burn_usd"),
}
OPTIONAL_FIGURES = {
    "shares": ("ads_ratio", "ads_ratio_status"),
    "cash_burn": ("investments_usd",),
}
QUEUE_CANDIDATE_KEYS = ("source", "source_ref", "filing_accession", "filing_accepted")
CANDIDATE_KEYS = (
    "id",
    "outcome",
    "reason",
    "flags",
    "source",
    "source_ref",
    "event_date",
    "filing_accession",
    "filing_accepted",
    "recorded_at",
)
VERSION_KEYS = (
    "id",
    "source",
    "source_ref",
    "valid_from",
    "recorded_at",
    "ratified_by",
    "vouched",
    "filing_accession",
    "filing_accepted",
    "set_aside",
)
DECISION_KEYS = (
    "id",
    "action",
    "candidate_id",
    "fact_id",
    "revokes",
    "decided_by",
    "recorded_at",
    "undone",
)
ATTEMPT_KEYS = ("id", "recorded_at", "run_kind", "status", "shares_reason", "cash_reason")


@dataclass(frozen=True)
class Level:
    """How much of a company page one pass shows; the view tries the levels in turn and keeps the first that fits."""

    versions: int
    candidates: int
    decisions: int
    attempts: int
    flags: int
    passages: int
    excerpt: int  # characters of each passage's excerpt
    candidate_note: int  # the waiting candidate's note
    note: int  # every other note and reason


PAGE_LEVELS = (
    Level(3, 5, 5, 3, 5, 3, 600, 1_000, 300),
    Level(2, 3, 3, 2, 3, 3, 600, 1_000, 200),
    Level(1, 2, 2, 1, 2, 2, 600, 800, 150),
    Level(1, 1, 1, 1, 1, 2, 400, 500, 100),
    Level(0, 0, 0, 0, 1, 2, 300, 300, 80),
    Level(0, 0, 0, 0, 0, 1, 150, 120, 60),
    Level(0, 0, 0, 0, 0, 1, 100, 60, 60),
)
# ``fact`` reads one value: its waiting candidate whole and every candidate (the cursor pages them); the lists the
# page shows in brief stay out, with their totals
FACT_LEVELS = (
    Level(0, 0, 0, 0, 5, 6, 3_000, 3_000, 300),
    Level(0, 0, 0, 0, 3, 4, 1_500, 1_500, 200),
    Level(0, 0, 0, 0, 2, 3, 800, 800, 120),
    Level(0, 0, 0, 0, 1, 2, 400, 400, 80),
    Level(0, 0, 0, 0, 0, 1, 200, 200, 60),
)


def _figures(row: dict[str, Any], slot: str) -> dict[str, Any]:
    out = pick(row, FIGURES[slot])
    out.update({k: row[k] for k in OPTIONAL_FIGURES[slot] if row.get(k) is not None})
    return out


def _put_reason(out: dict[str, Any], row: dict[str, Any], cut: int) -> None:
    """``reason`` (why a candidate found nothing) only when the row has one: a flagged candidate has none."""
    if row.get("reason") is not None:
        put_cut(out, "reason", row["reason"], cut)


def _put_unreadable(out: dict[str, Any], row: dict[str, Any]) -> None:
    """``passages_unreadable`` only when a stored passage did not read as one (never dropped silently)."""
    if row.get("passages_unreadable"):
        out["passages_unreadable"] = row["passages_unreadable"]


# --- the queue ---------------------------------------------------------------------------------------------


def _queue_candidate(c: dict[str, Any], slot: str) -> dict[str, Any]:
    out = pick(c, ("id", "event_date", "flags"))
    _put_reason(out, c, QUEUE_NOTE)
    out.update(_figures(c, slot))
    if slot == "cash_burn":
        out["statement_currency"] = c.get("statement_currency")
    out.update(pick(c, QUEUE_CANDIDATE_KEYS))
    out["passages"] = len(c.get("located_passages") or [])
    _put_unreadable(out, c)
    put_cut(out, "note", c.get("note"), QUEUE_NOTE)
    return out


def _queue_shown(v: dict[str, Any], slot: str) -> dict[str, Any]:
    out = pick(v, ("valid_from", "ratified_by"))
    out.update(_figures(v, slot))
    if slot == "cash_burn":
        out["currency"] = v.get("currency")
    return out


def _queue_value(v: dict[str, Any] | None, slot: str) -> dict[str, Any] | None:
    if v is None:
        return None
    shown = v.get("value")
    return {
        "state": v.get("state"),
        "candidate": _queue_candidate(v.get("candidate") or {}, slot),
        "value": None if shown is None else _queue_shown(shown, slot),
    }


def _queue_row(item: dict[str, Any]) -> dict[str, Any]:
    out = pick(
        item, ("security_id", "ticker", "name", "cards", "pen_only", "waiting_since", "open_flags")
    )
    out["shares"] = _queue_value(item.get("shares"), "shares")
    out["cash_burn"] = _queue_value(item.get("cash_burn"), "cash_burn")
    return out


def _listed(payload: dict[str, Any], pen: str) -> int:
    """How many companies the API lists under ``pen`` (paging arithmetic on the two counts it carries)."""
    total = payload.get("total") or 0
    pen_only = payload.get("pen_only_total") or 0
    if pen == "only":
        return pen_only
    if pen == "exclude":
        return total - pen_only
    return total


def _queue(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    pen = req.pen or "after"
    reply = header(req)
    reply["pen"] = pen
    reply["total"] = payload.get("total")
    reply["pen_only_total"] = payload.get("pen_only_total")
    reply["items"] = [_queue_row(i) for i in payload.get("items") or []]
    reply["sections"] = dict(SECTIONS)
    done = fit(reply, BUDGET, [TrimRule(("items",), min_keep=1, start=req.cursor, hint="cursor")])
    reached = req.cursor + len(done["items"])
    if done["next_cursor"] is None and done["items"] and reached < _listed(payload, pen):
        # the API page fit whole but more companies wait: the next page starts where this one ended
        done["next_cursor"] = reached
        done["next_cursor_for"] = "items"
    return done


# --- the company page --------------------------------------------------------------------------------------


def _passage(p: dict[str, Any], cut: int) -> dict[str, Any]:
    out = pick(p, ("kind", "source_ref"))
    put_cut(out, "anchor", p.get("anchor"), ANCHOR_CUT)
    put_cut(out, "excerpt", p.get("excerpt"), cut)
    out["offset"] = p.get("offset")
    return out


def _slot_extras(row: dict[str, Any], slot: str, keys: tuple[str, ...]) -> dict[str, Any]:
    """The fields only a cash-and-burn value carries (its currency and year-to-date pace)."""
    return pick(row, keys) if slot == "cash_burn" else {}


def _waiting(c: dict[str, Any] | None, slot: str, lv: Level) -> dict[str, Any] | None:
    """The waiting candidate in full: every field, each passage's excerpt cut (its length beside it)."""
    if c is None:
        return None
    out = pick(c, CANDIDATE_KEYS)
    out.update(_figures(c, slot))
    out.update(_slot_extras(c, slot, ("statement_currency", "pace")))
    put_cut(out, "note", c.get("note"), lv.candidate_note)
    passages = c.get("located_passages") or []
    out["passages_total"] = len(passages)
    _put_unreadable(out, c)
    out["located_passages"] = [_passage(p, lv.excerpt) for p in passages[: lv.passages]]
    decision = c.get("decision")
    out["decision"] = None if decision is None else pick(decision, ("action", "recorded_at"))
    return out


def _candidate_row(c: dict[str, Any], slot: str, note: int) -> dict[str, Any]:
    out = pick(c, ("id", "outcome"))
    put_cut(out, "reason", c.get("reason"), note)
    out.update(pick(c, ("flags", "event_date")))
    out.update(_figures(c, slot))
    out["filing_accession"] = c.get("filing_accession")
    out["recorded_at"] = c.get("recorded_at")
    decision = c.get("decision")
    out["decision"] = None if decision is None else pick(decision, ("action", "recorded_at"))
    return out


def _version(
    v: dict[str, Any] | None, slot: str, note: int, *, brief: bool = False
) -> dict[str, Any] | None:
    """A stored version; a ``brief`` one (a row of the versions list) leaves out its note and its filing's URL."""
    if v is None:
        return None
    out = pick(v, tuple(k for k in VERSION_KEYS if not (brief and k == "source_ref")))
    out.update(_figures(v, slot))
    out.update(_slot_extras(v, slot, ("currency", "pace")))
    if not brief:
        put_cut(out, "note", v.get("note"), note)
    return out


def _decision_row(d: dict[str, Any], note: int) -> dict[str, Any]:
    out = pick(d, DECISION_KEYS)
    put_cut(out, "reason", d.get("reason"), note)
    return out


def _attempt(a: dict[str, Any], note: int) -> dict[str, Any]:
    out = pick(a, ATTEMPT_KEYS)
    put_cut(out, "error", a.get("error"), note)
    put_cut(out, "detail", a.get("detail"), note)
    return out


def _flag(f: dict[str, Any], note: int) -> dict[str, Any]:
    """An open user flag: the user's words cut, the ids of what they saw, and never the user's email."""
    out = pick(f, ("id", "value_type"))
    put_cut(out, "note", f.get("note"), note)
    out["opened_at"] = f.get("opened_at")
    out["current_when_flagged"] = f.get("current_when_flagged")
    seen_value, seen_candidate = f.get("seen_value"), f.get("seen_candidate")
    out["seen_value"] = None if seen_value is None else pick(seen_value, ("id", "valid_from"))
    out["seen_candidate"] = None if seen_candidate is None else seen_candidate.get("id")
    out["reopens"] = f.get("reopens")
    return out


def _flags(payload: dict[str, Any], lv: Level, value_type: str | None) -> dict[str, Any]:
    flags = payload.get("flags") or {}
    opened = [
        f
        for f in flags.get("open") or []
        if value_type is None or f.get("value_type") == value_type
    ]
    return {
        "open_total": len(opened),
        "open": [_flag(f, lv.note) for f in opened[: lv.flags]],
        "closed_total": flags.get("closed_total"),
    }


def _newest(rows: list[Any], n: int) -> list[Any]:
    """The last ``n`` rows (the API lists versions oldest first), in the API's order."""
    return rows[len(rows) - n :] if n else []


def _value_block(v: dict[str, Any], slot: str, lv: Level, *, brief_lists: bool) -> dict[str, Any]:
    out = pick(v, ("state", "reason", "ratifier", "pinned"))
    pin = v.get("pin")
    out["pin"] = None if pin is None else pick(pin, ("action", "decided_by", "recorded_at"))
    if pin is not None:
        put_cut(out["pin"], "note", pin.get("note"), lv.note)
    out["value"] = _version(v.get("value"), slot, lv.note)
    out["awaiting"] = _waiting(v.get("awaiting"), slot, lv)
    out["pending"] = _waiting(v.get("pending"), slot, lv)
    versions = v.get("versions") or []
    candidates = v.get("candidates") or []
    decisions = v.get("decisions") or []
    out["versions_total"] = len(versions)
    out["candidates_total"] = len(candidates)
    out["decisions_total"] = len(decisions)
    if brief_lists:
        out["versions"] = [
            _version(x, slot, lv.note, brief=True) for x in _newest(versions, lv.versions)
        ]
        out["candidates"] = [_candidate_row(c, slot, lv.note) for c in candidates[: lv.candidates]]
        out["decisions"] = [_decision_row(d, lv.note) for d in decisions[: lv.decisions]]
    return out


def _company_reply(payload: dict[str, Any], req: ResearchRequest, lv: Level) -> dict[str, Any]:
    reply = header(req)
    reply["section"] = "company"
    reply["t"] = req.t
    reply.update(pick(payload, ("security_id", "ticker", "name")))
    if req.fact is None:
        for slot in ("shares", "cash_burn"):
            reply[slot] = _value_block(payload.get(slot) or {}, slot, lv, brief_lists=True)
        attempts = payload.get("attempts") or []
        reply["attempts_total"] = len(attempts)
        reply["attempts"] = [_attempt(a, lv.note) for a in attempts[: lv.attempts]]
        reply["flags"] = _flags(payload, lv, None)
    else:
        slot = SLOT[req.fact]
        value = payload.get(slot) or {}
        reply["fact"] = req.fact
        reply[slot] = _value_block(value, slot, lv, brief_lists=False)
        reply["candidates"] = [
            _candidate_row(c, slot, lv.note) for c in (value.get("candidates") or [])[req.cursor :]
        ]
        reply["flags"] = _flags(payload, lv, req.fact)
    reply["sections"] = dict(SECTIONS)
    return reply


def _company(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    if req.fact is not None and req.fact not in SLOT:
        raise ViewError(FACT_VALUE_RULE)
    levels = PAGE_LEVELS if req.fact is None else FACT_LEVELS
    # ``fact`` pages the value's candidates; the page has no list a cursor could page
    trim = (
        []
        if req.fact is None
        else [TrimRule(("candidates",), min_keep=1, start=req.cursor, hint="cursor")]
    )
    last: OutputTooLarge | None = None
    for lv in levels:
        try:
            return fit(_company_reply(payload, req, lv), BUDGET, trim)
        except OutputTooLarge as too_large:
            last = too_large
    assert last is not None
    raise OutputTooLarge("fact" if req.fact is None else last.hint, last.chars, last.budget)


def view(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    if req.section is None:
        if req.t is not None:
            raise ViewError(T_RULE)
        if req.fact is not None:
            raise ViewError(FACT_RULE)
        if req.pen is not None and req.pen not in PENS:
            raise ViewError(PEN_RULE)
        return _queue(payload, req)
    if req.section == "company":
        if req.pen is not None:
            raise ViewError(PEN_SECTION_RULE)
        return _company(payload, req)
    raise ViewError(f"Unknown section {req.section!r}.")
