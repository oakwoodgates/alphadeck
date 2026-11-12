"""``get_admin_status``: the steward's Admin status (``GET /admin/status``, undated, read live).

The default reply is the verdict and the counts: the cron verdict and its alarms, the record's edge, the newest
finished pass and any unfinished one, the newest backup, the identity pair, the one-line jobs and the panel counts. A
section pages one panel's rows whole, in the API's order. Every value is copied from the API's JSON; a thesis outside
the caller's workspace already arrives as ``workspace <8 hex> . thesis <8 hex>`` and is never resolved here. A health
readout: it never feeds a call.
"""

from __future__ import annotations

from typing import Any

from ...budget import fit
from .common import BUDGET, ResearchRequest, ViewError, head, header, paged

SECTIONS = (
    ("tape", None),
    ("price_repairs", None),
    ("split_defects", None),
    ("missing_closes", None),
    ("record", None),
    ("run", None),
)

RECORD_KEYS = ("edge", "today", "expected_asof", "days_behind", "stale", "reason", "missed")
RUN_KEYS = (
    "ran_at",
    "finished_at",
    "duration_s",
    "asof",
    "mode",
    "run_kind",
    "theses",
    "appended",
    "unchanged",
    "withheld",
    "errored",
    "transitions",
    "healthy",
    "catch_up",
    "code_sha",
)
SCALARS = (
    "splits_needs_review",
    "insider_holds_owed",
    "fundamentals_set_aside",
    "data_tenant_theses",
    "oauth_clients",
)
LINES = {
    "editions_nightly": ("line", "attention"),
    "missing_closes": ("line", "loud_lines"),
    "editions_cut": ("line", "attention"),
    "master_refresh": ("line", "attention"),
}
PRICE_REPAIR_LISTS = (
    "renamed",
    "unpriceable",
    "acknowledged",
    "closed",
    "applied",
    "successor",
    "cik_applied",
    "limitations",
)
MISSED_SHOWN = 10
NEWLY_STALE_SHOWN = 10
PROBLEMS_SHOWN = 3


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: row.get(k) for k in keys}


def _audit(audit: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The self-audit as counts: how many checks, how many FAILED (``ok`` false; null = not applicable) and the
    failed checks' names."""
    checks = audit or []
    failed = [c for c in checks if c.get("ok") is False]
    return {
        "checks": len(checks),
        "failed": len(failed),
        "failed_checks": [c.get("check") for c in failed],
    }


def _record(record: dict[str, Any]) -> dict[str, Any]:
    out = _pick(record, RECORD_KEYS)
    head(out, "missed_asofs", record.get("missed_asofs"), MISSED_SHOWN)
    out["window_days"] = record.get("window_days")
    for key in ("partial_nights", "reconstructed_nights", "late_only_nights"):
        out[key] = len(record.get(key) or [])
    return out


def _last_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    out = _pick(run, RUN_KEYS)
    head(out, "problems", run.get("problems"), PROBLEMS_SHOWN)
    out["audit"] = _audit(run.get("audit"))
    return out


def _default(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    reply = header(req)
    reply["cron"] = payload.get("cron")
    reply["record"] = _record(payload.get("record") or {})
    reply["last_run"] = _last_run(payload.get("last_run"))
    reply["unfinished_run"] = payload.get("unfinished_run")
    reply["backend_sha"] = payload.get("backend_sha")
    reply["cron_sha"] = payload.get("cron_sha")
    reply["last_backup"] = payload.get("last_backup")
    tape = payload.get("tape")
    if tape is None:
        reply["tape"] = None
    else:
        out = _pick(tape, ("asof", "ran_at", "stale_days", "fund_shares_stale_days"))
        out["stale"] = len(tape.get("stale") or [])
        head(out, "newly_stale", tape.get("newly_stale"), NEWLY_STALE_SHOWN)
        reply["tape"] = out
    defects = payload.get("split_defects")
    if defects is None:
        reply["split_defects"] = None
    else:
        out = _pick(defects, ("asof", "ran_at", "window_days"))
        out["defects"] = len(defects.get("defects") or [])
        out["settled"] = len(defects.get("settled") or [])
        out["newly_flagged"] = defects.get("newly_flagged")
        out["error"] = defects.get("error")
        reply["split_defects"] = out
    repairs = payload.get("price_repairs")
    if repairs is None:
        reply["price_repairs"] = None
    else:
        out = {"asof": repairs.get("asof")}
        for key in ("renamed", "unpriceable", "successor", "limitations", "closed"):
            out[key] = len(repairs.get(key) or [])
        reply["price_repairs"] = out
    lines: dict[str, Any] = {}
    for name, keys in LINES.items():
        line = payload.get(name)
        if line is not None:
            lines[name] = _pick(line, keys)
    reply["lines"] = lines
    for key in SCALARS:
        reply[key] = payload.get(key)
    reply["sections"] = dict(SECTIONS)
    return fit(reply, BUDGET, [])


def _scalars(panel: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return _pick(panel, keys)


def view(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    section = req.section
    if section is None:
        return _default(payload, req)
    reply = header(req)
    reply["section"] = section
    if section == "run":
        reply["last_run"] = payload.get("last_run")
        reply["unfinished_run"] = payload.get("unfinished_run")
        return fit(reply, BUDGET, [])
    if section == "record":
        record = payload.get("record") or {}
        reply.update(_pick(record, (*RECORD_KEYS, "window_days")))
        lists = [
            (k, record.get(k) or [])
            for k in ("partial_nights", "reconstructed_nights", "late_only_nights")
        ]
        lists.append(("daytime_only_asofs", record.get("daytime_only_asofs") or []))
        return paged(reply, lists, req.cursor, section)
    panel = payload.get(section)
    if section not in ("tape", "price_repairs", "split_defects", "missing_closes"):
        raise ViewError(f"Unknown section {section!r}.")
    if panel is None:
        reply["present"] = (
            False  # the panel has not been produced yet (a quiet state, never a failure)
        )
        return fit(reply, BUDGET, [])
    if section == "tape":
        reply.update(
            _scalars(
                panel, ("asof", "ran_at", "stale_days", "fund_shares_stale_days", "newly_stale")
            )
        )
        lists = [("stale", panel.get("stale") or [])]
    elif section == "price_repairs":
        reply.update(
            _scalars(
                panel,
                (
                    "asof",
                    "ran_at",
                    "probe_evaluated",
                    "probe_error",
                    "expected_misses",
                    "successor_asof",
                    "successor_evaluated",
                    "successor_error",
                ),
            )
        )
        lists = [(k, panel.get(k) or []) for k in PRICE_REPAIR_LISTS]
    elif section == "split_defects":
        reply.update(_scalars(panel, ("asof", "ran_at", "window_days", "newly_flagged", "error")))
        lists = [("defects", panel.get("defects") or []), ("settled", panel.get("settled") or [])]
    else:
        reply.update(
            _scalars(
                panel,
                (
                    "asof",
                    "ran_at",
                    "session",
                    "is_session",
                    "judged",
                    "line",
                    "loud_lines",
                    "error",
                ),
            )
        )
        lists = [
            ("missing", panel.get("missing") or []),
            ("still_missing", panel.get("still_missing") or []),
            ("arrived", panel.get("arrived") or []),
        ]
    return paged(reply, lists, req.cursor, section)
