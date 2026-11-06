"""``lookup_ticker``: everything the platform holds on one company (``GET /dashboards/ticker-lookup/{id}``).

The default reply keeps who the company is, the cards it sits on (its link and ``context_role`` per card, the
recorded call in effect there), the triggers the recorded calls show on it (each source's ``detail`` dropped), its
tape figures (the sparkline series dropped), the next five catalysts of its cards, the operator's decisions and a
filings summary. ``section="filings"`` lists the filing rows newest first, 25 to a page. ``cache`` and
``compute_ms`` pass through (a cold read is slow: the cost is visible). Every value is copied from the API's JSON;
``count_by_form`` only counts the listed rows.
"""

from __future__ import annotations

from typing import Any, Literal

from ..budget import TrimRule, fit

BUDGET = 8_000
NEXT_CATALYSTS = 5
NEWEST_FILINGS = 5
FILINGS_PAGE = 25
DETAIL_LINE_LIMIT = 300

Section = Literal["filings"]


def _cards(cards: dict[str, Any]) -> dict[str, Any]:
    return {
        "live_cards": cards.get("live_cards"),
        "counted": cards.get("counted"),
        "rows": [
            {
                "thesis_id": r.get("thesis_id"),
                "thesis_name": r.get("thesis_name"),
                "segments": [link.get("link") for link in r.get("links") or []],
                "silent": r.get("silent"),
                "context_role": r.get("context_role"),
                "signed_off": r.get("signed_off"),
                "state": r.get("state"),
                "verdict": r.get("verdict"),
                "recorded_on": r.get("recorded_on"),
                "reconstructed": r.get("reconstructed"),
                "place": r.get("place"),
            }
            for r in cards.get("rows") or []
        ],
        "archived": cards.get("archived"),
        "excluded": cards.get("excluded"),
    }


def _event(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "thesis_id": e.get("thesis_id"),
        "recorded_on": e.get("recorded_on"),
        "reconstructed": e.get("reconstructed"),
        "event_date": e.get("event_date"),
        "role": e.get("role"),
        "kind": e.get("kind"),
        "grade": e.get("grade"),
        "label": e.get("label"),
        "turns": e.get("turns"),
        "live": e.get("live"),
        "sources": [
            {"source": s.get("source"), "ref": s.get("ref"), "url": s.get("url")}
            for s in e.get("sources") or []
        ],
    }


def _tape(tape: dict[str, Any]) -> dict[str, Any]:
    sparkline = tape.get("sparkline") or {}
    return {
        "figures": [
            {
                "key": f.get("key"),
                "label": f.get("label"),
                "value": f.get("value"),
                "unit": f.get("unit"),
                "note": f.get("note"),
                "tone": f.get("tone"),
            }
            for f in tape.get("figures") or []
        ],
        "leading": tape.get("leading"),
        "coiling": tape.get("coiling"),
        "coiling_detail": tape.get("coiling_detail"),
        "headline_notes": tape.get("headline_notes"),
        "figures_blank": tape.get("figures_blank"),
        "sparkline_bars": sparkline.get("bars"),
        "window_end": tape.get("window_end"),
    }


def _filing_rows(filings: dict[str, Any]) -> list[dict[str, Any]]:
    """Every listed filing row, newest first (the API's day order, then its row order)."""
    return [row for day in filings.get("days") or [] for row in day.get("rows") or []]


def _filing(row: dict[str, Any], *, full: bool) -> dict[str, Any]:
    out = {
        "date": row.get("date"),
        "form": row.get("form"),
        "kind": row.get("kind"),
        "headline": row.get("headline"),
        "url": row.get("url"),
    }
    if full:
        lines = row.get("detail") or []
        out["detail"] = [line[:DETAIL_LINE_LIMIT] for line in lines]
        if any(len(line) > DETAIL_LINE_LIMIT for line in lines):
            out["detail_cut_at"] = DETAIL_LINE_LIMIT
        out.update(
            {
                "accession": row.get("accession"),
                "tags": row.get("tags"),
                "routine": row.get("routine"),
                "usd": row.get("usd"),
            }
        )
    return out


def view_ticker(
    page: dict[str, Any],
    *,
    resolved: dict[str, Any],
    asof: str,
    section: Section | None = None,
    cursor: int = 0,
) -> dict[str, Any]:
    filings = page.get("filings") or {}
    rows = _filing_rows(filings)
    reply: dict[str, Any] = {
        "asof": asof,
        "source": "live",
        "clock": page.get("clock"),
        "matched": resolved.get("matched"),
        "cache": page.get("cache"),
        "compute_ms": page.get("compute_ms"),
    }
    if section == "filings":
        reply.update(
            {
                "section": "filings",
                "identity": {
                    "security_id": (page.get("identity") or {}).get("security_id"),
                    "ticker": (page.get("identity") or {}).get("ticker"),
                    "name": (page.get("identity") or {}).get("name"),
                },
                "read_days": filings.get("read_days"),
                "filings_total": len(rows),
                "filings": [_filing(r, full=True) for r in rows[cursor : cursor + FILINGS_PAGE]],
            }
        )
        end = cursor + min(FILINGS_PAGE, max(0, len(rows) - cursor))
        reply["next_cursor"] = end if end < len(rows) else None
        reply["next_cursor_for"] = "filings" if end < len(rows) else None
        return fit(reply, BUDGET, [TrimRule(("filings",), min_keep=1, start=cursor)])
    triggers = page.get("triggers") or {}
    events = triggers.get("events") or []
    upcoming = [c for c in page.get("card_catalysts") or [] if not c.get("passed")]
    count_by_form: dict[str, int] = {}
    for r in rows:
        count_by_form[r.get("form")] = count_by_form.get(r.get("form"), 0) + 1
    reply.update(
        {
            "identity": page.get("identity"),
            "cards": _cards(page.get("cards") or {}),
            "triggers": {
                "window_start": triggers.get("window_start"),
                "cards_read": triggers.get("cards_read"),
                "nights_read": triggers.get("nights_read"),
                "events_total": len(events),
                "events": [_event(e) for e in events[cursor:]],
            },
            "tape": _tape(page.get("tape") or {}),
            "card_catalysts_upcoming": len(upcoming),
            "card_catalysts": [
                {
                    "thesis_id": c.get("thesis_id"),
                    "label": c.get("label"),
                    "kind": c.get("kind"),
                    "when_date": c.get("when_date"),
                    "when_label": c.get("when_label"),
                }
                for c in upcoming[:NEXT_CATALYSTS]
            ],
            "decisions": page.get("decisions"),
            "decisions_voided": page.get("decisions_voided"),
            "filings": {
                "read_days": filings.get("read_days"),
                "rows": filings.get("rows"),
                "listed": len(rows),
                "count_by_form": count_by_form,
                "newest": [_filing(r, full=False) for r in rows[:NEWEST_FILINGS]],
            },
            "sections": ["filings"],
        }
    )
    return fit(reply, BUDGET, [TrimRule(("triggers", "events"), min_keep=0, start=cursor)])
