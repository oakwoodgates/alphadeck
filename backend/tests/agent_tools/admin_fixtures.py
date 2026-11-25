"""Synthetic API payloads for the steward tools (``GET /admin/splits``, its tape window and ``GET /admin/status``).

Every name is synthetic (``TK01``..``TK08``, ``Company 01``..); no card, company or ticker from the live store. Each
payload is built as a dict, VALIDATED through the API's own wire model (so a fixture cannot drift from the contract) and
dumped to JSON exactly as FastAPI serves it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.routers.admin_insider_holds import AdminInsiderHoldsOut
from app.routers.admin_platform_values import CompanyValuesOut, PlatformQueueOut
from app.routers.admin_splits import AdminSplitListOut, AdminSplitTapeOut
from app.schemas_api import AdminStatusOut, OnboardingReadoutOut
from domain.insider_holds import reason_words
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
        "review_reason": None,
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


# W2-8: an open row's hints as the API states them (TREO's count: dated before the first session, so a confirm
# rescales it)
HINTS: dict[str, Any] = {
    "first_bar": "2025-03-14",
    "shares_rescaled": {"count": 13_524_682.0, "date": "2026-07-31"},
}


def needs_review_row(n: int, **over: Any) -> dict[str, Any]:
    """One more needs-review row (a different synthetic ticker), for the budget test. Like every open row it carries
    the API's two hints (W2-8): the first stored bar and the share count a confirm rescales."""
    fields: dict[str, Any] = {
        "decided_by": "needs review",
        "confirm_options": [_option("sec", "SEC: 1-for-10")],
        "actions": ["confirm_sec", "reject"],
        "review_reason": "one_source",
        **HINTS,
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
            review_reason="sources_differ",
        ),
        needs_review_row(
            2,
            sec=_sec(2, abstain="two ratios in the passage"),
            confirm_options=[_option("polygon", "Massive.com: 1-for-10")],
            review_reason="extractor_abstained",
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
        "announced": [_entry(4, "announced", decided_by="auto: SEC + Massive.com", **HINTS)],
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


SPLIT_BUCKETS = ("needs_review", "announced", "confirmed", "set_aside")
TAPE_SESSIONS = 5


def split_tape(version_id: str, around: str) -> dict[str, Any]:
    """``GET /admin/splits/{version_id}/tape`` (W5-1): five stored sessions either side of ``around`` (synthetic
    calendar days). The five before it were first served at about 20 and restated x0.1 (two closes each); from
    ``around`` on one version at about 2, so the step at ``around`` as first served reads about x0.1.
    """
    start = date.fromisoformat(around)
    bars = []
    for k in range(-TAPE_SESSIONS, TAPE_SESSIONS):
        before = k < 0
        close = round(20.0 + k / 10 if before else 2.0 + k / 100, 4)
        bars.append(
            {
                "d": (start + timedelta(days=k)).isoformat(),
                "close": close,
                "volume": 100_000.0 if before else 900_000.0,
                "newest_close": round(close / 10, 4) if before else close,
                "restated": before,
            }
        )
    prior, first = bars[TAPE_SESSIONS - 1], bars[TAPE_SESSIONS]
    body = {
        "version_id": version_id,
        "around": around,
        "sessions": TAPE_SESSIONS,
        "bars": bars,
        "prior_date": prior["d"],
        "step": first["close"] / prior["close"],
        "volume_ratio": first["volume"] / prior["volume"],
        "prior_served_before": True,
    }
    return _dump(AdminSplitTapeOut, body)


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
        "insider_holds_owed": 2,
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


# --- GET /admin/platform-values/queue and /admin/platform-values/{security_id} (Shared facts) ----------------------

# the longest note and the longest located-passage block MEASURED on the fork's candidates (2,034 characters each)
LONGEST_NOTE = 2_034
LONGEST_PASSAGE = 2_034
SHARED_SECURITY_ID = uid(
    "sec", "lookup"
)  # TK01 on the fake ticker resolver (``fixtures.ticker_resolve``)
STEWARD_EMAIL = "steward@example.test"
USER_EMAIL = "user01@example.test"


def _located(n: int, k: int, chars: int) -> dict[str, Any]:
    return {
        "kind": ("cover", "cash-flow", "balance-sheet")[k % 3],
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/{n + 1000}/{n:010d}26000020/doc{k}.htm",
        "anchor": "shares outstanding" if k % 2 == 0 else "net cash used in operating activities",
        "excerpt": text(chars, n + k),
        "offset": 1_200 * (k + 1),
    }


def _cand(
    n: int,
    fact: str,
    *,
    tag: str = "",
    note_chars: int = 400,
    passage_chars: int = 600,
    passages: int = 2,
    flags: tuple[str, ...] = ("stale_share_count",),
    outcome: str = "flag",
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One extractor candidate (``PlatformCandidateOut``) for company ``n``."""
    shares = fact == "shares_outstanding"
    row: dict[str, Any] = {
        "id": uid("cand", n, fact, tag),
        "security_id": uid("sec", "shared", n),
        "fact_type": fact,
        "outcome": outcome,
        "reason": None if outcome == "flag" else "no value in the filing",
        "flags": list(flags) if outcome == "flag" else [],
        "source": "10-q-cover" if shares else "10-q",
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/{n + 1000}/{n:010d}26000020/doc.htm",
        "event_date": "2026-10-30",
        "filing_accession": f"{n:010d}-26-000020",
        "filing_accepted": "2026-10-30T20:15:00Z",
        "note": text(note_chars, n) if note_chars else None,
        "located_passages": [_located(n, k, passage_chars) for k in range(passages)],
        "recorded_at": "2026-11-02T09:00:00Z",
        "decision": decision,
    }
    if shares:
        row.update({"shares": 10_000_000.0 + n, "ads_ratio": None, "ads_ratio_status": None})
    else:
        row.update(
            {
                "cash_usd": 4_000_000.0 + n,
                "quarterly_burn_usd": 750_000.0,
                "investments_usd": 1_000_000.0,
                "statement_currency": "USD",
            }
        )
    return row


def _version(n: int, fact: str, i: int, *, ratified_by: str = "auto") -> dict[str, Any]:
    shares = fact == "shares_outstanding"
    row: dict[str, Any] = {
        "id": uid("version", n, fact, i),
        "source": "10-q-cover" if shares else "10-q",
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/{n + 1000}/{n:010d}25000{i:03d}/doc.htm",
        "valid_from": f"2026-0{i % 9 + 1}-15",
        "recorded_at": f"2026-0{i % 9 + 1}-16T09:00:00Z",
        "ratified_by": ratified_by,
        "vouched": None,
        "note": text(120, i),
        "filing_accession": f"{n:010d}-25-{i:06d}",
        "filing_accepted": f"2026-0{i % 9 + 1}-15T20:00:00Z",
        "set_aside": False,
    }
    if shares:
        row.update({"shares": 9_000_000.0 + i, "ads_ratio": None, "ads_ratio_status": None})
    else:
        row.update(
            {
                "cash_usd": 3_000_000.0 + i,
                "quarterly_burn_usd": 600_000.0,
                "currency": "USD",
                "investments_usd": None,
            }
        )
    return row


def _queue_value(n: int, fact: str, **kw: Any) -> dict[str, Any]:
    """A queue row's waiting value: no value shown (awaiting_steward), or the shown value with a newer flag."""
    shown = kw.pop("shown", False)
    cand = _cand(n, fact, **kw)
    if not shown:
        return {"state": "awaiting_steward", "candidate": cand, "value": None}
    return {
        "state": "from_filing",
        "candidate": cand,
        "value": _version(n, fact, 1, ratified_by=STEWARD_EMAIL),
    }


def queue_item(
    n: int,
    *,
    pen_only: bool = False,
    cards: int = 3,
    shares: bool = True,
    cash: bool = True,
    shown: bool = False,
    open_flags: int = 0,
    **kw: Any,
) -> dict[str, Any]:
    """One company of the queue: a waiting share count and/or cash-and-burn (``PlatformQueueItemOut``)."""
    return {
        "security_id": uid("sec", "shared", n),
        "ticker": f"TK{n:02d}",
        "name": f"Company {n:02d}",
        "cards": 0 if pen_only else cards,
        "pen_only": pen_only,
        "waiting_since": f"2026-11-0{n % 9 + 1}T09:00:00Z",
        "shares": _queue_value(n, "shares_outstanding", shown=shown, **kw) if shares else None,
        "cash_burn": _queue_value(n, "cash_burn", shown=shown, **kw) if cash else None,
        "open_flags": open_flags,
    }


def queue_items(count: int = 6, pen_only: int = 2, **kw: Any) -> list[dict[str, Any]]:
    """The whole queue in the API's order: ``count`` companies, the last ``pen_only`` of them on a pen only.

    Company 2 waits on its share count alone, company 3 shows a value with a newer flag beside it and company 4
    carries two open user flags and nothing waiting."""
    out = []
    for n in range(1, count + 1):
        row_kw = dict(kw)
        if n == 2:
            row_kw["cash"] = False
        if n == 3:
            row_kw["shown"] = True
        item = queue_item(n, pen_only=n > count - pen_only, **row_kw)
        if n == 4:
            item.update({"shares": None, "cash_burn": None, "open_flags": 2})
        out.append(item)
    return out


def queue_page(
    items: list[dict[str, Any]], *, limit: int = 50, offset: int = 0, pen: str = "after"
) -> dict[str, Any]:
    """``GET /admin/platform-values/queue`` over ``items``: the API's ``pen`` filter and paging, its two totals."""
    pen_only_total = sum(1 for i in items if i["pen_only"])
    chosen = {
        "after": items,
        "only": [i for i in items if i["pen_only"]],
        "exclude": [i for i in items if not i["pen_only"]],
    }[pen]
    return _dump(
        PlatformQueueOut,
        {
            "items": chosen[offset : offset + limit],
            "total": len(items),
            "pen_only_total": pen_only_total,
        },
    )


def _decision(n: int, i: int, action: str, **over: Any) -> dict[str, Any]:
    return {
        "id": uid("decision", n, i),
        "action": action,
        "candidate_id": None,
        "fact_id": None,
        "revokes": None,
        "reason": text(90, i),
        "decided_by": STEWARD_EMAIL,
        "recorded_at": f"2026-10-{i % 27 + 1:02d}T10:00:00Z",
        "undone": False,
        **over,
    }


def _user_flag(n: int, i: int, fact: str, cand: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uid("flag", n, i),
        "value_type": fact,
        "user_email": USER_EMAIL,
        "note": "Synthetic user note: " + text(500, i),
        "opened_at": f"2026-11-0{i % 9 + 1}T12:00:00Z",
        "current_when_flagged": "awaiting",
        "seen_value": None,
        "seen_candidate": cand,
        "closed_at": None,
        "can_reopen": False,
    }


def company(
    n: int = 1,
    *,
    candidates: int = 6,
    versions: int = 4,
    decisions: int = 7,
    passages: int = 2,
    passage_chars: int = 600,
    note_chars: int = 400,
    open_flags: int = 2,
    attempts: int = 4,
) -> dict[str, Any]:
    """The company page of company ``n`` (``CompanyValuesOut``): its share count awaits the steward with no value
    shown, its cash-and-burn shows a value with a newer flag pending beside it and a pin; every user flag carries an
    email (the view drops it)."""
    kw = {"passages": passages, "passage_chars": passage_chars, "note_chars": note_chars}
    shares_wait = _cand(n, "shares_outstanding", tag="wait", **kw)
    cash_pending = _cand(n, "cash_burn", tag="pending", **kw)
    older = [
        _cand(n, "shares_outstanding", tag=f"old{k}", **{**kw, "passages": 1})
        for k in range(candidates - 1)
    ]
    cash_older = [
        _cand(
            n,
            "cash_burn",
            tag=f"old{k}",
            decision=_decision(n, k, "approve"),
            **{**kw, "passages": 1},
        )
        for k in range(candidates - 1)
    ]
    values = [_version(n, "cash_burn", i, ratified_by="auto") for i in range(versions)]
    flags = [
        _user_flag(n, i, "shares_outstanding" if i % 2 == 0 else "cash_burn", shares_wait)
        for i in range(open_flags)
    ]
    body = {
        "security_id": uid("sec", "lookup") if n == 1 else uid("sec", "shared", n),
        "ticker": f"TK{n:02d}",
        "name": f"Company {n:02d}",
        "shares": {
            "fact_type": "shares_outstanding",
            "state": "awaiting_steward",
            "reason": None,
            "ratifier": None,
            "pinned": False,
            "value": None,
            "awaiting": shares_wait,
            "pending": None,
            "versions": [],
            "candidates": [shares_wait, *older],
            "decisions": [],
            "pin": None,
        },
        "cash_burn": {
            "fact_type": "cash_burn",
            "state": "from_filing",
            "reason": None,
            "ratifier": "machine",
            "pinned": True,
            "value": values[-1] if values else None,
            "awaiting": None,
            "pending": cash_pending,
            "versions": values,
            "candidates": [cash_pending, *cash_older],
            "decisions": [
                _decision(n, i, "approve" if i % 2 else "dismiss") for i in range(decisions)
            ],
            "pin": {
                "action": "pin",
                "decided_by": STEWARD_EMAIL,
                "note": "Synthetic pin note.",
                "recorded_at": "2026-10-01T10:00:00Z",
            },
        },
        "attempts": [
            {
                "id": uid("attempt", n, i),
                "recorded_at": f"2026-11-0{i % 9 + 1}T03:00:00Z",
                "run_kind": "nightly",
                "status": "error" if i == 0 else "done",
                "shares_reason": None,
                "cash_reason": None,
                "error": text(200, i) if i == 0 else None,
                "detail": None,
            }
            for i in range(attempts)
        ],
        "flags": {
            "open": flags,
            "closed": [],
            "closed_total": 3,
            "closed_offset": 0,
            "closed_limit": 20,
        },
    }
    return _dump(CompanyValuesOut, body)


# --- GET /admin/insider-holds (Held insider rows) -----------------------------------------------------------------

HOLD_BUCKETS = (
    "owed",
    "placed_by_rule",
    "filer_corrected",
    "decided",
    "dismissed",
    "amendment_row",
)
HOLD_BUCKET_WORDS = {
    "owed": "owed your decision",
    "placed_by_rule": "placed by rule (counted from the filing night)",
    "filer_corrected": "corrected by the filer",
    "decided": "decided",
    "dismissed": "dismissed",
    "amendment_row": "held in a Form 4/A (cannot be applied yet)",
}


def _hold_decision(n: int, i: int, action: str, **over: Any) -> dict[str, Any]:
    return {
        "id": uid("hold-decision", n, i),
        "action": action,
        "ratified_date": "2026-10-20" if action == "approve" else None,
        "reason": "Synthetic reason: " + text(300, i),
        "recorded_at": f"2026-11-0{i % 9 + 1}T15:00:00Z",
        "basis": f"steward:{action}",
        "revokes": None,
        "decided_by": {"user_id": uid("steward"), "display_name": "Steward 01"},
        **over,
    }


def hold(n: int, bucket: str, *, ticker: int | None = None, seq: int = 1) -> dict[str, Any]:
    """One held Form 4 row of company ``ticker`` (default ``n``) in ``bucket`` (``AdminInsiderHoldOut``)."""
    company_n = ticker or n
    accession = f"{1_000_000_000 + n}-26-{n:06d}"
    reason = "after_filing"
    row: dict[str, Any] = {
        "hold_id": uid("hold", n, seq),
        "security_id": uid("sec", "hold", company_n),
        "ticker": f"TK{company_n:02d}",
        "company_name": f"Company {company_n:02d}",
        "accession": accession,
        "txn_seq": seq,
        "form": "4",
        "reason": reason,
        "reason_words": reason_words(reason),
        "row": {
            "insider_name": f"Insider {n:02d}",
            "insider_role": "Chief Executive Officer",
            "txn_code": "P",
            "shares": 1_000.0 + n,
            "price": 2.5,
            "usd": 2_500.0 + n,
        },
        "date_as_filed": "10/31/2027",
        "period_of_report_as_filed": "10/31/2026",
        "signature_date_as_filed": "11/02/2026",
        "filed": "2026-11-02",
        "accepted": "2026-11-02T21:30:00Z",
        "bucket": bucket,
        "why": None,
        "filing_night": None,
        "suggested_date": "2026-10-31",
        "agrees": True,
        "suggestion_basis": "period_of_report",
        "suggestion_words": "2026-10-31: the filing's period of report; " + text(700, n),
        "window_from": "2026-10-31",
        "window_to": "2026-11-02",
        "copies": [],
        "market_date": "2026-11-02",
        "owed_why": None,
        "decision": None,
        "history": [],
        "filing_url": f"https://www.sec.gov/Archives/edgar/data/{company_n + 1000}/{accession.replace('-', '')}/",
    }
    if bucket == "owed":
        row["owed_why"] = "Filed late, so the rule cannot place it: " + text(300, n)
    elif bucket == "placed_by_rule":
        row["filing_night"] = "2026-11-02"
    elif bucket == "filer_corrected":
        row["why"] = "The filer's Form 4/A replaced this row: " + text(300, n)
    elif bucket in ("decided", "dismissed"):
        action = "approve" if bucket == "decided" else "dismiss"
        first = _hold_decision(n, 1, action)
        row["decision"] = first
        row["history"] = [first]
    return row


def holds(
    owed: int = 3,
    placed: int = 2,
    filer: int = 1,
    decided: int = 1,
    dismissed: int = 1,
    amendment: int = 1,
) -> dict[str, Any]:
    """The Held insider rows panel: the buckets in the API's order, their counts and the backend's words."""
    sizes = dict(
        zip(HOLD_BUCKETS, (owed, placed, filer, decided, dismissed, amendment), strict=True)
    )
    n = 0
    body: dict[str, Any] = {}
    for bucket in HOLD_BUCKETS:
        rows = []
        for _ in range(sizes[bucket]):
            n += 1
            rows.append(hold(n, bucket))
        body[bucket] = rows
    body["counts"] = sizes
    body["bucket_words"] = dict(HOLD_BUCKET_WORDS)
    body["total"] = sum(sizes.values())
    return _dump(AdminInsiderHoldsOut, body)


def holds_on_one_ticker(rows: int = 18) -> dict[str, Any]:
    """``rows`` held rows all on TK01 (the MEASURED most on one company is 18), across the buckets: every fifth
    is decided and carries a two-step history."""
    sizes = dict.fromkeys(HOLD_BUCKETS, 0)
    body: dict[str, Any] = {b: [] for b in HOLD_BUCKETS}
    for k in range(rows):
        bucket = "decided" if k % 5 == 4 else "owed"
        row = hold(100 + k, bucket, ticker=1, seq=k + 1)
        if bucket == "decided":
            row["history"].append(
                _hold_decision(100 + k, 2, "revoke", revokes=row["decision"]["id"])
            )
        body[bucket].append(row)
        sizes[bucket] += 1
    body["counts"] = sizes
    body["bucket_words"] = dict(HOLD_BUCKET_WORDS)
    body["total"] = rows
    return _dump(AdminInsiderHoldsOut, body)


# --- GET /admin/onboarding (Onboarding) ---------------------------------------------------------------------------

ONBOARD_LEGS = ["price", "form4", "filings"]


def _onboard(i: int, status: str, **over: Any) -> dict[str, Any]:
    return {
        "security_id": uid("sec", "admin-onboard", i),
        "ticker": f"TK{i + 1:02d}",
        "name": f"Company {i + 1:02d} synthetic onboarding name",
        "status": status,
        "legs_done": over.pop("legs_done", ONBOARD_LEGS[:2]),
        "attempts": over.pop("attempts", 1),
        "last_error": over.pop("last_error", None),
        "source": "promote",
        "in_live_basket": True,
        "parked": False,
        "enqueued_at": f"{ASOF}T21:00:00Z",
        "last_attempt_at": over.pop("last_attempt_at", f"{ASOF}T21:30:00Z"),
        "finished_at": over.pop("finished_at", None),
    }


def admin_onboarding(pending: int = 116, failed: int = 3, recent: int = 20) -> dict[str, Any]:
    """The shared onboarding queue as ``GET /admin/onboarding`` serves it: ``failed`` failed names first, then one
    in progress, then the rest queued (the MEASURED fork has 116 pending names); ``recent`` done names.
    """
    names = [
        _onboard(i, "failed", attempts=3, last_error="Synthetic: " + text(210, i))
        for i in range(failed)
    ]
    names.append(_onboard(failed, "in_progress", legs_done=ONBOARD_LEGS[:1]))
    names += [
        _onboard(i, "queued", legs_done=[], attempts=0, last_attempt_at=None)
        for i in range(failed + 1, pending)
    ]
    done = [
        _onboard(
            500 + k,
            "done",
            legs_done=ONBOARD_LEGS,
            finished_at=f"{ASOF}T20:{k:02d}:00Z",
            last_error=text(120, k) if k == 3 else None,
        )
        for k in range(recent)
    ]
    return _dump(
        OnboardingReadoutOut,
        {
            "counts": {
                "queued": max(pending - failed - 1, 0),
                "in_progress": 1,
                "failed": failed,
                "done": 1_325,
                "parked": 0,
            },
            "pending": names[:pending],
            "recent_done": done,
            "last_run": {
                "started_at": f"{ASOF}T20:00:00Z",
                "finished_at": f"{ASOF}T20:20:00Z",
                "status": "ok",
                "run_kind": "onboarding",
                "stopped": "done",
                "detail": None,
                "counts": {"done": 20, "failed": failed, "requeued": 1},
            },
            "window": "refuses 21:30 to 23:30 ET",
            "stalled": None,
            "paused": None,
            "history_legs": list(ONBOARD_LEGS),
        },
    )


# --- GET /admin/status with a full Price feed repairs panel ---------------------------------------------------------


def _probe(symbol: str, *, verified: bool, error: str | None = None) -> dict[str, Any]:
    """One EDGAR-listed symbol as the splice probe judged it: verified = at least 5 shared dates within 1%."""
    return {
        "symbol": symbol,
        "verified": verified,
        "overlap": 0 if error else (250 if verified else 3),
        "ratio": None if error else (1.0 if verified else 0.62),
        "bars": 0 if error else 240,
        "last_bar": None if error else "2026-11-05",
        "error": error,
    }


def _repair_thesis(n: int) -> list[dict[str, Any]]:
    return [{"id": uid("thesis", "repair", n), "name": "Thesis 01"}]


def _renamed(n: int, *, verified: bool, probed: bool = True) -> dict[str, Any]:
    ticker = f"TK{n:02d}"
    candidates = [_probe(f"{ticker}N", verified=verified)] if probed else []
    if probed and not verified:
        candidates.append(_probe(f"{ticker}X", verified=False, error="vendor 404"))
    return {
        "security_id": uid("repair", "renamed", n),
        "ticker": ticker,
        "edge": "2026-10-30",
        "theses": _repair_thesis(n),
        "edgar_tickers": [f"{ticker}N"],
        "edgar_name": f"Company {n:02d} Renamed Inc.",
        "former_names": [
            {"name": f"Company {n:02d} Inc.", "since": "2019-01-01", "until": "2026-09-30"}
        ],
        "name_change_filings": [{"filed": "2026-10-02", "accession": f"{n:010d}-26-000031"}],
        "candidates": candidates,
        "recommended": candidates[0]["symbol"] if probed and verified else None,
        "probed": probed,
        "price_symbol": ticker,
        "change": None,
    }


def _unpriceable(n: int) -> dict[str, Any]:
    """A never-priced name. The first (``n == 20``) is an ADR member whose EDGAR list also carries a foreign
    ordinary line the never-priced probe fetched (W4-1): never recommended, the classes differ."""
    adr = n == 20
    candidates = [
        {
            **_probe("TKTAF", verified=False),
            "overlap": 0,
            "ratio": None,
            "listing_class": "ordinary",
        }
    ]
    return {
        "security_id": uid("repair", "unpriceable", n),
        "ticker": f"TK{n:02d}",
        "theses": _repair_thesis(n),
        "edgar_tickers": [f"TK{n:02d}", "TKTAF"] if adr else [] if n % 2 else [f"TK{n:02d}.TO"],
        "vendor_error": "404 Not Found: " + text(120, n),
        "price_symbol": None,
        "change": None,
        "member_class": "adr" if adr else None,
        "candidates": candidates if adr else [],
        "recommended": None,
        "probed": adr,
    }


def _successor(n: int, *, recommended: bool) -> dict[str, Any]:
    cik = f"{1_000_000 + n:010d}"
    new = f"{2_000_000 + n:010d}"
    candidates = [
        {
            "cik": new,
            "matched_on": [f"TK{n:02d}"],
            "entity_name": f"Company {n:02d} Holdings plc",
            "edgar_tickers": [f"TK{n:02d}"],
            "former_names": [],
            "succession_filings": [
                {
                    "form": "8-K12B",
                    "filed": "2026-09-20",
                    "accession": f"{n:010d}-26-000041",
                    "items": [],
                }
            ],
            "identity_read": True,
            "recent_foreign_form": "20-F",
            "files_domestic_forms": False,
            "incorporation": "L3",
            "error": None,
        }
    ]
    if not recommended:  # SEC's map names two registrants: shown, never adoptable
        candidates.append({**candidates[0], "cik": f"{3_000_000 + n:010d}", "identity_read": False})
    return {
        "security_id": uid("repair", "successor", n),
        "ticker": f"TK{n:02d}",
        "theses": _repair_thesis(n),
        "stored_cik": cik,
        "master_name": f"Company {n:02d} Inc.",
        "old_entity_name": f"Company {n:02d} Inc.",
        "old_former_names": [],
        "deregistrations": [
            {
                "form": "15-12B",
                "filed": "2026-09-15",
                "accession": f"{n:010d}-26-000040",
                "items": [],
            }
        ],
        "candidates": candidates,
        "recommended": new if recommended else None,
        "current_cik": cik,
        "change": None,
    }


def status_with_price_repairs(
    renamed: int = 3, unpriceable: int = 4, successor: int = 2, closed: int = 2
) -> dict[str, Any]:
    """``GET /admin/status`` with a full Price feed repairs panel: ``renamed`` names (the first has a verified
    successor symbol, the second a probe that verified none, the rest were not probed), ``unpriceable`` names never
    priced, ``successor`` members that re-filed under a new CIK (the first has one recommended, the second an
    ambiguous map), ``closed`` names that stopped trading, one acknowledged name and one known feed limitation.
    """
    body = status()
    body["price_repairs"] = {
        "asof": ASOF,
        "ran_at": "2026-11-06T03:56:00Z",
        "probe_evaluated": True,
        "probe_error": None,
        "renamed": [_renamed(n, verified=n == 1, probed=n <= 2) for n in range(1, renamed + 1)],
        "unpriceable": [_unpriceable(20 + n) for n in range(unpriceable)],
        "acknowledged": [
            {
                "id": uid("repair", "ack", 1),
                "security_id": uid("repair", "ack-security", 1),
                "ticker": "TK30",
                "action": "acknowledge",
                "reason": "Synthetic reason: kept unpriced.",
                "basis": "operator:acknowledge",
                "created_at": "2026-10-20T12:00:00Z",
                "still_unpriceable": True,
            }
        ],
        "closed": [
            {
                "security_id": uid("repair", "closed", n),
                "ticker": f"TK{40 + n}",
                "edge": "2026-09-30",
                "thesis": "Thesis 01",
                "kind": "price",
                "closed_at": "2026-10-01",
                "renamed": False,
                "edgar_tickers": [],
                "unpriceable": False,
                "acknowledged": False,
            }
            for n in range(closed)
        ],
        "applied": [],
        "expected_misses": closed + 1,
        "successor_asof": ASOF,
        "successor_evaluated": True,
        "successor_error": None,
        "successor": [_successor(50 + n, recommended=n == 0) for n in range(successor)],
        "cik_applied": [],
        "limitations": status()["price_repairs"]["limitations"],
    }
    return _dump(AdminStatusOut, body)
