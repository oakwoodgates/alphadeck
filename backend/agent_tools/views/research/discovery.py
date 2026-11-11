"""``get_discovery``: what discovery found for a card (``GET /workbench/theses/{id}/runs`` then ``/runs/{run_id}``;
``section="terms"`` also reads the thesis for its term set; undated).

A slice (``status`` and/or ``segment``) first page carries the run's id, ``written_at`` and the slice's own counts
instead of the whole report (``section="report"`` reads it). The default reply keeps the run's report (the #9 visibility: failed, capped and empty terms whole), the placements counted
by status, source and segment, and the ONE paged list of placements in the run's order, narrowed by ``status`` and/or
``segment``. VERIFY and off-thesis are recommendations, never deletions. A card with no saved run answers with an empty
``runs`` list and a note, never an error. Every value is copied from the API's JSON; this module counts entries,
filters rows on a copied field and cuts strings.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, pick, put_cut

SECTIONS = (("placement", "t"), ("ambiguous", None), ("report", None), ("terms", None))
STATUSES = ("placed", "verify", "ambiguous", "absent")
SOURCES = ("edgar", "off_universe")
NO_RUN_NOTE = "no saved draft run for this card"

# the fields the views copy (a drift guard in the tests checks each against the API's wire model)
RUN_KEYS = ("run_id", "written_at", "placement_count", "segment_count")
PLACEMENT_KEYS = (
    "name",
    "ticker",
    "prose",
    "segment",
    "status",
    "security_id",
    "candidates",
    "matched_terms",
    "discovery_source",
    "sector",
    "exchange",
    "listing_status",
    "category",
    "origin",
    "business_type",
    "royalty",
    "off_thesis",
)
CANDIDATE_KEYS = ("security_id", "ticker", "name", "cik")
SEGMENT_KEYS = ("label", "descriptor")
REPORT_KEYS = (
    "coverage",
    "capped_terms",
    "empty_terms",
    "tail_sweep",
    "narration_needed",
    "narration_filled",
    "scope",
)
COVERAGE_KEYS = ("pages_ok", "pages_attempted", "failed_terms")
TERM_KEYS = ("term", "tier", "authored_by", "source")
BODY_KEYS = ("segments", "placements", "report")
THESIS_KEYS = ("term_set",)

RUNS_SHOWN = 5
NAME_CUT = 30
DESCRIPTOR_CUT = 80
TERMS_SHOWN = 3


def _runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [pick(r, RUN_KEYS) for r in runs[:RUNS_SHOWN]]


def _report(run: dict[str, Any]) -> dict[str, Any] | None:
    """The run's honesty report with the coverage flattened in (``scope`` is added by the caller)."""
    report = run.get("report")
    if report is None:
        return None
    coverage = report.get("coverage") or {}
    return {
        "pages_ok": coverage.get("pages_ok"),
        "pages_attempted": coverage.get("pages_attempted"),
        "failed_terms": list(coverage.get("failed_terms") or []),
        "capped_terms": list(report.get("capped_terms") or []),
        "empty_terms": list(report.get("empty_terms") or []),
        "tail_sweep": report.get("tail_sweep"),
        "narration_needed": report.get("narration_needed"),
        "narration_filled": report.get("narration_filled"),
    }


def _by_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {s: sum(1 for p in rows if p.get("status") == s) for s in STATUSES}


def _segments(run: dict[str, Any], placements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per segment in the run's order: its placements counted by status and how many are off-thesis. A label only the
    placements carry follows the run's own list with no descriptor."""
    labels: list[tuple[str, str | None]] = [
        (s.get("label"), s.get("descriptor")) for s in run.get("segments") or []
    ]
    known = {label for label, _ in labels}
    for p in placements:
        if p.get("segment") not in known:
            known.add(p.get("segment"))
            labels.append((p.get("segment"), None))
    out = []
    for label, descriptor in labels:
        rows = [p for p in placements if p.get("segment") == label]
        entry: dict[str, Any] = {"label": label}
        put_cut(entry, "descriptor", descriptor, DESCRIPTOR_CUT)
        entry["placements_total"] = len(rows)
        entry["by_status"] = _by_status(rows)
        entry["off_thesis"] = sum(1 for p in rows if p.get("off_thesis"))
        out.append(entry)
    return out


def _counts(placements: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "placements_total": len(placements),
        "by_status": _by_status(placements),
        "by_source": {
            s: sum(1 for p in placements if (p.get("discovery_source") or "edgar") == s)
            for s in SOURCES
        },
        "off_thesis": sum(1 for p in placements if p.get("off_thesis")),
        "with_candidates": sum(1 for p in placements if p.get("candidates")),
    }


def _row(p: dict[str, Any]) -> dict[str, Any]:
    terms = p.get("matched_terms") or []
    out: dict[str, Any] = {
        "name": (p.get("name") or "")[:NAME_CUT] if p.get("name") is not None else None,
        "ticker": p.get("ticker"),
        "segment": p.get("segment"),
        "status": p.get("status"),
        "matched_terms": terms[:TERMS_SHOWN],
        "matched_terms_total": len(terms),
    }
    if p.get("off_thesis"):
        out["off_thesis"] = True
    if p.get("discovery_source") == "off_universe":
        out["source"] = "off_universe"
    if p.get("status") == "ambiguous":
        out["candidates"] = len(p.get("candidates") or [])
    return out


def _candidate(c: dict[str, Any]) -> dict[str, Any]:
    return pick(c, CANDIDATE_KEYS)


def _full(p: dict[str, Any]) -> dict[str, Any]:
    out = pick(p, PLACEMENT_KEYS)
    out["candidates"] = [_candidate(c) for c in p.get("candidates") or []]
    out["matched_terms"] = list(p.get("matched_terms") or [])
    return out


def _ambiguous(p: dict[str, Any]) -> dict[str, Any]:
    terms = p.get("matched_terms") or []
    return {
        "name": p.get("name"),
        "ticker": p.get("ticker"),
        "segment": p.get("segment"),
        "matched_terms": terms[:TERMS_SHOWN],
        "matched_terms_total": len(terms),
        "candidates": [_candidate(c) for c in p.get("candidates") or []],
    }


def _term_hits(placements: list[dict[str, Any]], term: str) -> int:
    return sum(1 for p in placements if term in (p.get("matched_terms") or []))


def _narrowed(
    placements: list[dict[str, Any]], run: dict[str, Any], req: ResearchRequest
) -> list[dict[str, Any]]:
    rows = placements
    if req.status is not None:
        rows = [p for p in rows if p.get("status") == req.status]
    if req.segment is not None:
        labels = [s.get("label") for s in run.get("segments") or []]
        labels += [p.get("segment") for p in placements if p.get("segment") not in labels]
        label = next((x for x in labels if x == req.segment), None)
        if label is None:
            folded = req.segment.strip().casefold()
            label = next((x for x in labels if (x or "").casefold() == folded), None)
        if label is None:
            raise ViewError(
                f"No segment {req.segment!r} in this run; the labels are: "
                + ", ".join(str(x) for x in labels)
                + "."
            )
        rows = [p for p in rows if p.get("segment") == label]
    return rows


def view(
    runs: list[dict[str, Any]],
    run: dict[str, Any] | None,
    thesis: dict[str, Any] | None,
    req: ResearchRequest,
) -> dict[str, Any]:
    reply = header(req)
    if not runs or run is None:
        reply["runs"] = []
        reply["note"] = NO_RUN_NOTE
        reply["sections"] = dict(SECTIONS)
        return fit(reply, BUDGET, [])
    run_id = req.run or runs[0].get("run_id")
    summary = next((r for r in runs if r.get("run_id") == run_id), {"run_id": run_id})
    placements = run.get("placements") or []
    first_page = req.cursor == 0
    if req.section is None:
        rows = _narrowed(placements, run, req)
        narrowed = {k: v for k, v in (("status", req.status), ("segment", req.segment)) if v}
        if first_page:
            reply["runs_total"] = len(runs)
            reply["runs"] = _runs(runs)
            if narrowed:
                # a slice read: the run's identity and the slice's own counts, not the whole report (section=report)
                reply["run"] = {"run_id": run_id, "written_at": summary.get("written_at")}
                reply["counts"] = {"rows_total": len(rows), "by_status": _by_status(rows)}
                reply["more"] = (
                    "section=report for the run's report; no status or segment for the whole run"
                )
            else:
                reply["run"] = {
                    "run_id": run_id,
                    "written_at": summary.get("written_at"),
                    "scope": (run.get("report") or {}).get("scope"),
                    "report": _report(run),
                }
                reply["segments"] = _segments(run, placements)
                reply["counts"] = _counts(placements)
        else:
            reply["run"] = {"run_id": run_id}
        if narrowed:
            reply["narrowed"] = narrowed
        reply["placements_total"] = len(rows)
        reply["placements"] = [_row(p) for p in rows[req.cursor :]]
        reply["sections"] = dict(SECTIONS)
        return fit(
            reply, BUDGET, [TrimRule(("placements",), min_keep=1, start=req.cursor, hint="cursor")]
        )
    reply["run"] = {"run_id": run_id}
    reply["section"] = req.section
    reply["sections"] = dict(SECTIONS)
    if req.section == "placement":
        wanted = (req.t or "").strip().casefold()
        found = [
            p
            for p in placements
            if (p.get("ticker") or "").casefold() == wanted
            or (p.get("name") or "").casefold() == wanted
        ]
        if not found:
            raise ViewError(f"No placement with ticker or name {req.t!r} in this run.")
        reply["placements_total"] = len(found)
        reply["placements"] = [_full(p) for p in found[req.cursor :]]
        key = "placements"
    elif req.section == "ambiguous":
        rows = [p for p in placements if p.get("candidates")]
        reply["placements_total"] = len(rows)
        reply["placements"] = [_ambiguous(p) for p in rows[req.cursor :]]
        key = "placements"
    elif req.section == "report":
        report = _report(run) or {}
        report["scope"] = (run.get("report") or {}).get("scope")
        reply["report"] = report
        reply["term_hits"] = {
            kind: [
                {"term": t, "placements": _term_hits(placements, t)}
                for t in report.get(field) or []
            ]
            for kind, field in (
                ("capped", "capped_terms"),
                ("empty", "empty_terms"),
                ("failed", "failed_terms"),
            )
        }
        key = None
    elif req.section == "terms":
        report = _report(run) or {}
        capped, empty, failed = (
            set(report.get(k) or []) for k in ("capped_terms", "empty_terms", "failed_terms")
        )
        term_set = (thesis or {}).get("term_set") or []
        rows = []
        for entry in term_set[req.cursor :]:
            row = pick(entry, TERM_KEYS)
            row["placements"] = _term_hits(placements, entry.get("term"))
            for flag, members in (("capped", capped), ("empty", empty), ("failed", failed)):
                if entry.get("term") in members:
                    row[flag] = True
            rows.append(row)
        reply["terms_total"] = len(term_set)
        reply["terms"] = rows
        key = "terms"
    else:
        raise ViewError(f"Unknown section {req.section!r}.")
    if key is None:
        return fit(reply, BUDGET, [])
    return fit(reply, BUDGET, [TrimRule((key,), min_keep=1, start=req.cursor, hint="cursor")])
