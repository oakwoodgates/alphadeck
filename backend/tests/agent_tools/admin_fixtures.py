"""Synthetic API payloads for the steward tools (``GET /admin/splits`` and ``GET /admin/status``).

Every name is synthetic (``TK01``..``TK08``, ``Company 01``..); no card, company or ticker from the live store. Each
payload is built as a dict, VALIDATED through the API's own wire model (so a fixture cannot drift from the contract) and
dumped to JSON exactly as FastAPI serves it.
"""

from __future__ import annotations

from typing import Any

from app.routers.admin_splits import AdminSplitListOut
from app.schemas_api import AdminStatusOut
from tests.agent_tools.fixtures import text, uid

ASOF = "2026-11-06"
PASSAGE_CHARS = 700  # over the brief row's 300-character cut and under the full row's 1,500


def _dump(model: Any, payload: Any) -> Any:
    return model.model_validate(payload).model_dump(mode="json")


def _sec(n: int, *, abstain: str | None = None) -> dict[str, Any]:
    ref = f"0001{n:06d}-26-000010"
    return {
        "accession": ref,
        "form": "8-K",
        "filed": "2026-10-20",
        "url": f"https://www.sec.gov/Archives/edgar/data/{n + 1000}/{ref.replace('-', '')}/{ref}-index.htm",
        "passage": text(PASSAGE_CHARS, n),
        "document": f"{ref}.txt",
        "effective_date": None if abstain else "2026-11-02",
        "date_kind": None if abstain else "trading",
        "ratio_from": None if abstain else "1",
        "ratio_to": None if abstain else "10",
        "ratio_label": None if abstain else "1-for-10",
        "abstain_reason": abstain,
    }


def _polygon(n: int, ticker: str) -> dict[str, Any]:
    return {
        "id": f"synthetic-{n}",
        "ticker": ticker,
        "execution_date": "2026-11-02",
        "split_from": "1",
        "split_to": "10",
        "ratio_label": "1-for-10",
    }


def _entry(n: int, status: str, **over: Any) -> dict[str, Any]:
    ticker = f"TK{n:02d}"
    base: dict[str, Any] = {
        "version_id": uid("split-version", n, status),
        "security_id": uid("split-sec", n),
        "ticker": ticker,
        "name": f"Company {n:02d}",
        "key_date": f"2026-11-0{n % 9 + 1}",
        "effective_date": "2026-11-02",
        "ratio_from": "1",
        "ratio_to": "10",
        "price_factor": "0.1",
        "direction": "forward",
        "label": "1-for-10",
        "status": status,
        "confirmed_via": None,
        "value_source": "sec",
        "author": "system",
        "action": "append",
        "recorded_at": "2026-11-03T12:00:00Z",
        "decided_by": "auto: SEC + Massive.com",
        "valid_from": "2026-11-02",
        "note": None,
        "sec": _sec(n),
        "polygon": _polygon(n, ticker),
        "confirm_options": [],
        "actions": ["reject"],
        "restore_to": None,
    }
    base.update(over)
    return base


def _option(source: str, label: str) -> dict[str, Any]:
    return {
        "source": source,
        "ratio_from": "1",
        "ratio_to": "10",
        "effective_date": "2026-11-02",
        "label": label,
    }


def needs_review_row(n: int, **over: Any) -> dict[str, Any]:
    """One more needs-review row (a different synthetic ticker), for the budget test."""
    fields: dict[str, Any] = {
        "decided_by": "needs review",
        "confirm_options": [_option("sec", "SEC: 1-for-10")],
        "actions": ["confirm_sec", "reject"],
        **over,
    }
    return _entry(n, "needs_review", **fields)


def _jump(n: int, *, dismissed: bool = False, after: int = 3) -> dict[str, Any]:
    return {
        "security_id": uid("split-sec", n),
        "ticker": f"TK{n:02d}",
        "prior_date": "2026-10-28",
        "step_date": "2026-10-29",
        "prior_close": 2.0,
        "close": 20.5,
        "prior_volume": 900000.0,
        "volume": 80000.0,
        "step": 10.25,
        "label": "x10.2 step",
        "sessions_since": 5,
        "after": [
            {"d": f"2026-11-0{k + 1}", "close": 20.0 + k, "volume": 70000.0 + k}
            for k in range(after)
        ],
        "dismissal_id": uid("dismissal", n) if dismissed else None,
        "dismissed_at": "2026-11-04T15:00:00Z" if dismissed else None,
        "dismissed_reason": ("Real move: " + text(160, n)) if dismissed else None,
    }


def splits(extra_needs_review: int = 0) -> dict[str, Any]:
    """The split list: 3 needs-review (TK01 two options; TK02 the extractor abstained; TK03 Massive.com only),
    1 announced (TK04), 3 confirmed (TK05, TK01 again, TK06), 1 set aside (TK07, with a long note and a restore
    target), 1 unlisted jump (TK08, three bars after) and 1 dismissed jump (TK06)."""
    needs = [
        needs_review_row(
            1,
            confirm_options=[
                _option("sec", "SEC: 1-for-10"),
                _option("polygon", "Massive.com: 1-for-10"),
            ],
            actions=["confirm_sec", "confirm_polygon", "reject"],
        ),
        needs_review_row(
            2,
            sec=_sec(2, abstain="two ratios in the passage"),
            confirm_options=[_option("polygon", "Massive.com: 1-for-10")],
        ),
        needs_review_row(3, sec=None),
    ]
    needs += [needs_review_row(20 + k) for k in range(extra_needs_review)]
    set_aside = _entry(
        7,
        "rejected",
        decided_by="you, 11-04",
        note="Rejected: " + text(300, 3),
        actions=["restore"],
        restore_to="needs_review",
    )
    body = {
        "needs_review": needs,
        "announced": [_entry(4, "announced", decided_by="auto: SEC + Massive.com")],
        "confirmed": [
            _entry(5, "confirmed", confirmed_via="auto"),
            _entry(1, "confirmed", key_date="2026-06-01", confirmed_via="auto"),
            _entry(6, "confirmed", confirmed_via="auto"),
        ],
        "set_aside": [set_aside],
        "unlisted_jumps": [_jump(8)],
        "dismissed_jumps": [_jump(6, dismissed=True, after=1)],
        "unlisted_jumps_error": None,
    }
    body["total"] = sum(
        len(body[b]) for b in ("needs_review", "announced", "confirmed", "set_aside")
    )
    return _dump(AdminSplitListOut, body)


def status() -> dict[str, Any]:
    """The Admin status: a healthy verdict with one alarm, two missed nights, a last run with four problems and
    two audit rows (one failed), a tape with two stale rows and one newly stale, three splits needing review.
    """
    stale = [
        {
            "security_id": uid("stale", k),
            "ticker": f"TK{k:02d}",
            "edge": "2026-10-30",
            "thesis": "workspace 1a2b3c4d · thesis 5e6f7a8b",
            "kind": "price",
        }
        for k in (1, 2)
    ]
    body = {
        "record": {
            "edge": ASOF,
            "today": "2026-11-09",
            "expected_asof": ASOF,
            "days_behind": 0,
            "stale": False,
            "reason": "current",
            "missed": 2,
            "missed_asofs": ["2026-10-14", "2026-10-15"],
            "daytime_only_asofs": ["2026-10-15"],
            "window_days": 20,
            "partial_nights": [
                {
                    "asof": "2026-10-20",
                    "covered": 4,
                    "expected": 6,
                    "reconstructed_only": 0,
                    "daytime_only": 0,
                    "late": 0,
                }
            ],
            "reconstructed_nights": [],
            "late_only_nights": [],
        },
        "last_run": {
            "ran_at": "2026-11-06T03:30:00Z",
            "finished_at": "2026-11-06T04:10:00Z",
            "duration_s": 2400.0,
            "asof": ASOF,
            "mode": "live",
            "theses": 12,
            "appended": 3,
            "unchanged": 9,
            "withheld": 0,
            "errored": 0,
            "transitions": 1,
            "edgar_fetches": 800,
            "healthy": True,
            "problems": [f"Synthetic problem {k}: " + text(60, k) for k in range(4)],
            "catch_up": False,
            "code_sha": "abc1234",
            "run_kind": "cron",
            "audit": [
                {"check": "on_time_row", "ok": True, "detail": "ok"},
                {"check": "benchmark_tape", "ok": False, "detail": "stale by one session"},
            ],
        },
        "cron": {
            "status": "healthy",
            "detail": "The last pass was clean.",
            "alarms": ["Synthetic alarm: backup is old."],
        },
        "last_backup": {
            "name": "alphadeck_2026-11-06.dump",
            "bytes": 123456,
            "created_at": "2026-11-06T05:00:00Z",
            "labeled": False,
        },
        "tape": {
            "asof": ASOF,
            "ran_at": "2026-11-06T03:50:00Z",
            "stale_days": 3,
            "fund_shares_stale_days": 10,
            "stale": stale,
            "newly_stale": ["TK01 price"],
        },
        "backend_sha": "abc1234",
        "cron_sha": "abc1234",
        "unfinished_run": None,
        "split_defects": {
            "asof": ASOF,
            "ran_at": "2026-11-06T03:55:00Z",
            "window_days": 30,
            "defects": [],
            "settled": [],
            "newly_flagged": [],
            "error": None,
        },
        "price_repairs": {
            "asof": ASOF,
            "ran_at": "2026-11-06T03:56:00Z",
            "probe_evaluated": True,
            "renamed": [
                {
                    "security_id": uid("pr", 1),
                    "ticker": "TK03",
                    "edge": "2026-10-30",
                    "thesis": "Thesis 01",
                    "successor_ticker": "TK03N",
                }
            ],
            "unpriceable": [
                {
                    "security_id": uid("pr", 2),
                    "ticker": "TK04",
                    "edge": None,
                    "thesis": "Thesis 01",
                    "reason": "no vendor symbol",
                }
            ],
            "limitations": [
                {
                    "id": uid("pr-limit", 3),
                    "security_id": uid("pr", 3),
                    "ticker": "TK05",
                    "reason": "synthetic limitation",
                    "basis": "vendor",
                    "created_at": "2026-11-01T12:00:00Z",
                }
            ],
        },
        "splits_needs_review": 3,
        "fundamentals_set_aside": 0,
        "editions_nightly": {
            "line": "Editions: tonight's pages appended.",
            "ran_at": "2026-11-06T04:20:00Z",
            "night": ASOF,
            "attention": False,
        },
        "missing_closes": {
            "asof": ASOF,
            "ran_at": "2026-11-06T03:58:00Z",
            "session": ASOF,
            "is_session": True,
            "judged": 300,
            "line": "All closes present.",
            "missing": [],
            "still_missing": [],
            "loud_lines": [],
            "arrived": [],
            "error": None,
        },
        "editions_cut": {
            "line": "Edition 2 published.",
            "published_at": "2026-11-05T04:00:00Z",
            "number": 2,
            "attention": False,
        },
        "master_refresh": {
            "line": "Master refreshed.",
            "ran_at": "2026-11-01T14:00:00Z",
            "attention": False,
        },
        "data_tenant_theses": 0,
        "oauth_clients": {"static": 2, "dcr": 1},
    }
    return _dump(AdminStatusOut, body)
