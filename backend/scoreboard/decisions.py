from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from scoreboard.prices import PgRealizedPrices
from scoreboard.schema import EpisodeOperator, OperatorSpan, ScoredEpisode

# The operator track: the append-only decision log joined to the episodes it answered. The stance a
# span carries is the one FROZEN on the take row at logging time (``call_state``/``call_verdict`` —
# the record, not a recompute, is attribution's source). No delta/counterfactual math here (parked
# to v2 with the follow-blindly track): a took row shows the record's return and the operator's own
# side by side; a pass shows the episode's outcome next to the pass. Prices: a logged fill price
# always wins; a missing one falls back to the close (flagged ``inferred``, never silent); a
# thesis-level take (no name) stays UNPRICED — visible, never guessed onto a name.

_ARMED_STANCES = frozenset({"armed", "managing"})


def spans_and_passes(
    rows: list[dict[str, Any]], asof: date
) -> tuple[list[dict], list[dict], dict[str, int], str | None]:
    """Resolve the raw decision rows (newest-first, as ``decisions_repo.list_for_thesis`` returns
    them) into take→close SPANS + pass rows, as-of ``asof`` on the valid axis (``decision_date``;
    the transaction axis stays known_at=now, same posture as prices). Voided rows are excluded from
    all math and counted. Returns ``(spans, passes, counts, anomaly)`` — ``anomaly`` surfaces a
    log shape the API should have prevented (take-while-open / close-while-flat), never silently
    fixed. A span dict: ``{take: row, close: row|None}``."""
    voided = {r["voids"] for r in rows if r["action"] == "void" and r["voids"] is not None}
    live = sorted(
        (
            r
            for r in rows
            if r["action"] in ("take", "pass", "close")
            and r["id"] not in voided
            and r["decision_date"] <= asof
        ),
        key=lambda r: (r["decision_date"], r["seq"]),
    )
    counts = {
        "takes": sum(1 for r in live if r["action"] == "take"),
        "passes": sum(1 for r in live if r["action"] == "pass"),
        "voided": sum(1 for r in rows if r["id"] in voided and r["decision_date"] <= asof),
    }
    spans: list[dict] = []
    passes: list[dict] = []
    anomaly: str | None = None
    open_span: dict | None = None
    for r in live:
        if r["action"] == "pass":
            passes.append(r)
        elif r["action"] == "take":
            if open_span is not None:
                anomaly = f"take {r['id']} logged while a position was open (span kept, not fixed)"
            open_span = {"take": r, "close": None}
            spans.append(open_span)
        elif r["action"] == "close":
            if open_span is None:
                anomaly = f"close {r['id']} logged while flat (row ignored by the span pairing)"
            else:
                open_span["close"] = r
                open_span = None
    return spans, passes, counts, anomaly


def _priced(span: dict, prices: PgRealizedPrices, asof: date) -> dict[str, Any]:
    """The span's operator-side prices: the logged fill wins; a missing one is the close, flagged
    ``inferred`` (entry = first close on/after the take — blind-entry parity; exit = last close
    through the close date, or through ``asof`` while running). Thesis-level (no name): unpriced."""
    take, close = span["take"], span["close"]
    sid = take["security_id"]
    running = close is None
    out: dict[str, Any] = {
        "running": running,
        "entry_price": None,
        "entry_inferred": False,
        "exit_price": None,
        "exit_inferred": False,
        "exit_date": None if running else close["decision_date"],
        "operator_return": None,
    }
    if sid is None:
        return out  # thesis-level: visible, never guessed onto a name
    if take["price"] is not None:
        out["entry_price"] = float(take["price"])
    else:
        entry = prices.first_close_on_or_after(sid, take["decision_date"])
        if entry is not None:
            out["entry_price"], out["entry_inferred"] = entry[1], True
    if not running and close["price"] is not None:
        out["exit_price"] = float(close["price"])
    elif not running:
        exit_pt = prices.last_close_through(sid, close["decision_date"])
        if exit_pt is not None:
            out["exit_price"], out["exit_inferred"] = exit_pt[1], True
    else:
        exit_pt = prices.last_close_through(sid, asof)
        if exit_pt is not None:
            out["exit_price"], out["exit_inferred"] = exit_pt[1], True
    if out["entry_price"] and out["exit_price"] is not None:
        out["operator_return"] = out["exit_price"] / out["entry_price"] - 1
    return out


def _episode_window(e: ScoredEpisode, asof: date) -> tuple[date, date]:
    return e.episode.arm_date, e.episode.dearm_date or asof


def attach_operator_track(
    episodes: list[ScoredEpisode],
    rows: list[dict[str, Any]],
    prices: PgRealizedPrices,
    asof: date,
) -> tuple[list[OperatorSpan], dict[str, int], str | None]:
    """Join the decision log to the episodes it answered, mutating each episode's ``operator`` slot
    (the EARLIEST take-span answering the arm wins the slot; a pass fills it only when no take did;
    an armed episode nobody answered keeps None — the honest capture gap). Spans answering NO
    episode return as off-record ``OperatorSpan``s carrying the frozen stance — ``override`` when
    the platform's stance at the take was not armed/managing. Returns (off_record, counts, anomaly).
    """
    spans, passes, counts, anomaly = spans_and_passes(rows, asof)
    off_record: list[OperatorSpan] = []

    def matching_episode(sid: UUID | None, d: date, *, headline_ok: bool) -> ScoredEpisode | None:
        for e in episodes:
            start, end = _episode_window(e, asof)
            if not (start <= d <= end):
                continue
            if sid is not None and e.episode.security_id == sid:
                return e
            if sid is None and headline_ok and e.episode.is_headline:
                return e
        return None

    for span in spans:
        take = span["take"]
        ep = matching_episode(take["security_id"], take["decision_date"], headline_ok=False)
        priced = _priced(span, prices, asof)
        if ep is not None and ep.operator is None:  # earliest take answering the arm wins the slot
            ep.operator = EpisodeOperator(
                action="took",
                decision_id=take["id"],
                decision_date=take["decision_date"],
                reason=take["reason"],
                thesis_level=take["security_id"] is None,
                **priced,
            )
            continue
        stance = take["call_state"]
        off_record.append(
            OperatorSpan(
                take_id=take["id"],
                take_date=take["decision_date"],
                security_id=take["security_id"],
                thesis_level=take["security_id"] is None,
                call_state_at_take=stance,
                call_verdict_at_take=take["call_verdict"],
                override=stance is not None and stance not in _ARMED_STANCES,
                close_id=span["close"]["id"] if span["close"] else None,
                close_date=span["close"]["decision_date"] if span["close"] else None,
                reason=take["reason"],
                **priced,
            )
        )
    counts["overrides"] = sum(1 for s in off_record if s.override)

    for p in passes:
        ep = matching_episode(p["security_id"], p["decision_date"], headline_ok=True)
        if ep is not None and ep.operator is None:
            ep.operator = EpisodeOperator(
                action="passed",
                decision_id=p["id"],
                decision_date=p["decision_date"],
                reason=p["reason"],
                thesis_level=p["security_id"] is None,
            )
    return off_record, counts, anomaly
