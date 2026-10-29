"""The data flags' READER (split plan S3): one function per security, as of the view's day, on the view's clock.

The model and its pure rules are ``domain/data_flags.py``; this module reads the view and composes the six
kinds (and the one quiet marker) from the monitors that already exist — it adds no detector of its own:

- ``possible_unconfirmed_split`` — the S5 split-night hold's own rule (``domain/split_hold.py``: ``hold_for``
  over the view's last bars and the split list as the view knows it — what ``signals.split_hold.find_holds``
  reads for the call, so the flag and the card's held line agree).
- ``split_needs_review`` — the split list (``PointInTimeData.split_events``).
- ``mixed_scale_history`` — T4's pure detectors (``pipeline.split_basis.tape_findings`` / ``flap_findings``)
  over the view's WHOLE stored tape (the nightly scan reads 430 days; a stale tail older than that — JAGX's
  2024-09 bars the repair refused — is still a tape that mixes scales, and the flag says which cells it reaches,
  usually none). Flaps are a property of OUR stored versions, so they are read on the LIVE view only, and the
  S2 follow-up applies: a flapping bar whose newest version sits on the split list's scale is SETTLED
  (``settled_flaps``) — the repaired names' old flip history draws nothing. A name whose flap has bars left
  then gets T4's name-level settle check (``split_basis.settle_flap`` — the rule behind Admin's quiet
  "settled" line): when no night's view reads two bases, its flap draws nothing either.
- ``price_feed_stopped`` — ``pipeline.tape_health.is_tape_stale`` on the view's newest bar, read as renamed /
  closed / a plain feed gap by the nightly classifier's carried inventory when the view is today's (a history
  view never borrows today's EDGAR reading — no lookahead, #1).
- ``market_cap_share_count`` — the market cap's own inputs (``workbench.scoring._market_cap``): the newest
  share count and the newest bar, against the splits the list has NOT applied.
- ``fundamentals_not_current`` — ``pipeline.fundamentals_lag`` over the EDGAR cache on disk (the company's
  cached submissions index and companyfacts; no request), on the LIVE view only: the cached copy is today's,
  so a history view cannot say when SEC's data caught up (no lookahead, #1). A company still onboarding is
  not judged.
- the ``short_price_history`` marker — ``domain.data_flags.short_history_note`` over the view's tape.

Nothing here writes, and nothing on the call path imports it (``tests/domain/test_data_flags.py``).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from domain.data_flags import (
    BAR,
    FEED_STOPPED,
    FUNDAMENTALS_LAG,
    IN_DOUBT,
    INSIDER,
    KNOWN_WRONG,
    MARKET_CAP,
    MIXED_SCALE,
    MKTCAP_SHARES,
    PRICE,
    SPLIT_HOLD,
    SPLIT_REVIEW,
    STEP,
    VOLUME,
    DataFlag,
    Evidence,
    Mark,
    MemberFlags,
    SplitLabel,
    all_cells,
    cells_for,
    md,
    short_history_note,
    sort_flags,
)
from domain.market_time import market_tz
from domain.split_hold import COVER_SESSIONS, Hold, hold_for, nearby
from domain.splits import (
    LIVE_STATUSES,
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_AUTO,
    VIA_OPERATOR,
    VIA_POLYGON_TAPE,
    VIA_SEC_TAPE,
    ListedSplit,
    direction,
    is_applicable,
    listed_from_row,
    session_distance,
)
from ingest.prices.split_normalize import VENDOR_NOISE
from pipeline.fundamentals_lag import FundamentalsLag, read_lag
from pipeline.split_basis import (
    CLIFF,
    FLAP,
    FLAP_RECENT_DAYS,
    ISLAND,
    K_MIN,
    BarVersion,
    Finding,
    TapeBar,
    flap_findings,
    read_public_tapes,
    settle_flap,
    tape_findings,
    view_lookback_days,
    window_days,
)
from pipeline.tape_health import is_tape_stale
from signals.split_hold import HOLD_LOOKBACK_DAYS  # the hold's own read window — one definition

# How near a list row's first session must sit to a hold's step to be "the same split" (the hold's own
# ``COVER_SESSIONS``): a needs-review row the hold already names is not flagged twice.
NEAR_SESSIONS = COVER_SESSIONS

# The repair tool's provenance (``pipeline.repair_split_basis``: KIND / KIND_REVOKE / its log dir) — local
# copies so this read path does not import the repair tool's ingest graph; pinned equal by test.
REPAIR_KIND = "repair_split_basis"
REPAIR_REVOKE_KIND = "repair_split_basis_revoke"
DEFAULT_REPAIRS_DIR = Path(__file__).resolve().parents[2] / "data" / "repairs"

_CONFIRMED_BY = {
    VIA_AUTO: "the SEC filing and Massive.com agree",
    VIA_SEC_TAPE: "the SEC filing and the price tape agree",
    VIA_POLYGON_TAPE: "Massive.com and the price tape agree",
    VIA_OPERATOR: "you confirmed it",
}


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _day(v: Any) -> date:
    return (
        v
        if isinstance(v, date) and not isinstance(v, datetime)
        else date.fromisoformat(str(v)[:10])
    )


def _fmt_k(k: float) -> str:
    return f"{k:.0f}" if k >= 10 else f"{k:.1f}"


# --- the stopped-feed reading the nightly made (the carried stale inventory) ------------------------------


@dataclass(frozen=True)
class StopReading:
    """The nightly classifier's reading of one stopped price tape (``pipeline.tape_health.StaleTape`` as the
    run artifact carries it): its edge, and closed / renamed / acknowledged."""

    edge: date | None
    closed_at: date | None = None
    renamed: bool = False
    edgar_tickers: tuple[str, ...] | None = None
    acknowledged: bool = False
    asof: date | None = None  # the pass's as-of (the reading is today's EDGAR, judged that night)


def stop_readings(doc: Mapping[str, Any] | None) -> dict[UUID, StopReading]:
    """``security_id -> StopReading`` from a carried stale inventory (``cron_run_log.carried_inventory(...)
    .as_doc()`` — the SAME carry-forward the Admin price-feed panel renders). Price rows only; fail-open per
    row (a malformed row is skipped, never a raise)."""
    out: dict[UUID, StopReading] = {}
    if not doc:
        return out
    try:
        pass_asof = _day(doc["asof"]) if doc.get("asof") else None
    except ValueError:
        pass_asof = None
    for t in doc.get("theses") or []:
        if not isinstance(t, Mapping):
            continue
        for s in t.get("tape_stale") or []:
            try:
                if not isinstance(s, Mapping) or (s.get("kind") or "price") != "price":
                    continue
                sid = UUID(str(s["security_id"]))
                if sid in out:
                    continue
                listed = s.get("edgar_tickers")
                out[sid] = StopReading(
                    edge=_day(s["edge"]) if s.get("edge") else None,
                    closed_at=_day(s["closed_at"]) if s.get("closed_at") else None,
                    renamed=bool(s.get("renamed")),
                    edgar_tickers=(
                        tuple(str(x) for x in listed) if isinstance(listed, list) else None
                    ),
                    acknowledged=bool(s.get("acknowledged")),
                    asof=pass_asof,
                )
            except Exception:  # noqa: BLE001 — skip a malformed row, never blank the flags
                continue
    return out


# --- history corrected: the repair tool's provenance, and the price leg's first-night application ---------


@dataclass(frozen=True)
class Correction:
    """Alpha Deck re-scaled the vendor's bars before one listed split: when, how many, and why (the repair's
    per-bar labels — ``raw`` = the vendor had not adjusted them)."""

    on: date
    bars: int
    labels: tuple[str, ...] = ()
    source: str = "repair"


_CORRECTIONS_CACHE: dict[str, Any] = {"key": None, "value": {}}


def repair_corrections(base_dir: Path | None = None) -> dict[tuple[str, date], Correction]:
    """``(security_id, key_date) -> Correction`` from every split-basis repair provenance file under
    ``data/repairs`` that was not revoked: a split counts as corrected when the repair re-scaled at least one
    bar dated before its first session. Cached on the directory's (name, mtime) listing; a missing directory
    or an unreadable file reads as no correction (fail-open — a display read)."""
    run_dir = base_dir or DEFAULT_REPAIRS_DIR
    if not run_dir.exists():
        return {}
    files = sorted(run_dir.glob("*split-basis*.json"))
    key = (str(run_dir), tuple((p.name, p.stat().st_mtime_ns) for p in files))
    if _CORRECTIONS_CACHE["key"] == key:
        return _CORRECTIONS_CACHE["value"]
    applies: list[dict] = []
    revoked: set[str] = set()
    for p in files:
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(blob, dict):
            continue
        if blob.get("kind") == REPAIR_REVOKE_KIND and blob.get("revokes"):
            revoked.add(str(blob["revokes"]))
        elif blob.get("kind") == REPAIR_KIND and blob.get("recorded_at"):
            applies.append(blob)
    out: dict[tuple[str, date], Correction] = {}
    for blob in applies:
        if str(blob["recorded_at"]) in revoked:
            continue
        try:
            on = datetime.fromisoformat(str(blob["recorded_at"])).astimezone(market_tz()).date()
        except ValueError:
            continue
        for comp in blob.get("companies") or []:
            if not isinstance(comp, dict) or not comp.get("security_id"):
                continue
            splits: list[tuple[date, date, float]] = []
            for s in comp.get("splits") or []:
                try:
                    splits.append(
                        (_day(s["key_date"]), _day(s["effective_date"]), float(s["factor"]))
                    )
                except (KeyError, ValueError, TypeError):
                    continue
            hits: dict[date, list[str]] = defaultdict(list)
            for b in comp.get("bars") or []:
                if not isinstance(b, dict) or not b.get("d"):
                    continue
                for kd in _corrected_splits(_day(b["d"]), str(b.get("factor") or ""), splits):
                    hits[kd].append(str(b.get("label") or ""))
            for kd, labels in hits.items():
                out[(str(comp["security_id"]), kd)] = Correction(
                    on=on, bars=len(labels), labels=tuple(sorted({x for x in labels if x}))
                )
    _CORRECTIONS_CACHE["key"], _CORRECTIONS_CACHE["value"] = key, out
    return out


def _factor_of(text: str) -> float | None:
    """The repair's per-bar correction ("x16", "x4/25", "x1.0441") as a number, or None."""
    t = text.strip().lstrip("x")
    try:
        if "/" in t:
            a, b = t.split("/", 1)
            return float(a) / float(b)
        return float(t)
    except (ValueError, ZeroDivisionError):
        return None


def _corrected_splits(
    d: date, factor_text: str, splits: Sequence[tuple[date, date, float]]
) -> list[date]:
    """Which listed splits (key dates) one re-scaled bar was corrected for: the NEWEST splits after it whose
    factors multiply to the bar's correction (a raw bar lacks the newest splits; a doubled bar carries them
    twice: the inverse), else — a mis-factored bar, or no product that matches — the first split after it.
    """
    after = sorted((s for s in splits if s[1] > d), key=lambda s: s[1], reverse=True)
    if not after:
        return []
    f = _factor_of(factor_text)
    if f is not None and f > 0:
        p = 1.0
        for i, s in enumerate(after):
            p *= s[2]
            if abs(abs(math.log(p)) - abs(math.log(f))) < 0.01:
                return [x[0] for x in after[: i + 1]]
    return [after[-1][0]]


def first_night_corrections(
    payloads: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, date], Correction]:
    """``(security_id, first_session) -> Correction`` from the run artifacts' ``split_reads`` (S2): the price
    leg re-scaled a split's pre-split history on the split's first night ("1-for-200 reverse split
    2026-10-26"). Keyed by the split's FIRST SESSION (the label carries no key date — they agree on every
    list row except an operator re-date). Only the payloads handed in (the newest passes)."""
    out: dict[tuple[str, date], Correction] = {}
    for doc in payloads:
        if doc.get("mode") != "live" or not isinstance(doc.get("split_reads"), Mapping):
            continue
        try:
            on = _day(doc["asof"])
        except (KeyError, ValueError):
            continue
        for sid, v in doc["split_reads"].items():
            if not isinstance(v, Mapping):
                continue
            n = int((v.get("reasons") or {}).get("first_night") or 0)
            for lab in v.get("first_night") or []:
                try:
                    eff = date.fromisoformat(str(lab).rsplit(" ", 1)[-1])
                except ValueError:
                    continue
                out.setdefault(
                    (str(sid), eff),
                    Correction(on=on, bars=n, labels=("raw",), source="first_night"),
                )
    return out


def run_reads() -> tuple[dict[UUID, StopReading], dict[tuple[str, date], Correction]]:
    """``(readings, corrections)`` for ``read_flags``: the nightly's readings the flags borrow on TODAY's view
    (read from the run artifacts on disk, the same carry-forward the Admin price-feed panel renders) and the
    "history corrected" record (the repair tool's provenance + the price leg's first-night application).
    Fail-open: no artifacts -> neither. ONE definition for every caller of ``read_flags`` that serves a view
    (the ``/data-flags`` routes, the dashboards' ``price_flags``)."""
    # lazy: the run-artifact module is the nightly's writer; keep this reader's import graph unchanged
    from pipeline.cron_run_log import carried_inventory, list_run_logs

    logs = list_run_logs(limit=20)
    inv = carried_inventory(logs, evaluated_key="tape_evaluated")
    readings = stop_readings(inv.as_doc() if inv else None)
    corrections = {**first_night_corrections(logs), **repair_corrections()}
    return readings, corrections


# --- the pure composition --------------------------------------------------------------------------------


def _bar_dates(bars: Sequence[Mapping[str, Any]]) -> list[date]:
    """The view's priced bar dates, ascending (a bar without a positive close is no reading)."""
    return [_day(b["d"]) for b in bars if (c := _num(b.get("close"))) is not None and c > 0]


def _first_bar_on_or_after(dates: Sequence[date], d: date) -> date | None:
    return next((x for x in dates if x >= d), None)


def _sec_evidence(row: Mapping[str, Any]) -> list[Evidence]:
    out: list[Evidence] = []
    if row.get("sec_accession") or row.get("sec_filed"):
        form = row.get("sec_form") or "filing"
        filed = row.get("sec_filed")
        out.append(
            Evidence(
                label=f"SEC {form}" + (f" filed {_day(filed).isoformat()}" if filed else ""),
                url=row.get("sec_url"),
            )
        )
    if row.get("polygon_id"):
        ex = row.get("polygon_execution_date")
        out.append(
            Evidence(
                label="Massive.com split reference"
                + (f" (first session {_day(ex).isoformat()})" if ex else "")
            )
        )
    return out


def _sources(row: Mapping[str, Any]) -> str:
    sec = bool(row.get("sec_accession") or row.get("sec_filed"))
    poly = bool(row.get("polygon_id"))
    if sec and poly:
        return "the SEC filing and Massive.com disagree"
    if sec:
        return f"only an SEC {row.get('sec_form') or 'filing'} states it"
    if poly:
        return "only Massive.com lists it"
    return "one source only"


def hold_flag(
    hold: Hold, bar_dates: Sequence[date], asof: date, near_rows: Sequence[Mapping]
) -> DataFlag:
    """The S5 hold as a flag: in doubt across the step (price + volume), the call affected."""
    step = hold.step
    marks = [Mark(step.d, STEP)]
    evidence: list[Evidence] = []
    for r in near_rows:
        evidence += _sec_evidence(r)
    reason = hold.reason[:1].upper() + hold.reason[1:]
    return DataFlag(
        kind=SPLIT_HOLD,
        level=IN_DOUBT,
        reason=f"{reason}. The price triggers on this name are held on the call card while it stands.",
        clears=(
            "Clears when a second source confirms the split (the history is put on one scale the next "
            "night), or three sessions after the step; a step no source ever lists then goes to Admin → "
            "Splits."
        ),
        since=step.d,
        affects=(PRICE, VOLUME),
        columns=cells_for(marks, bar_dates, asof, (PRICE, VOLUME)),
        dates=tuple(d for d in bar_dates if d >= step.d),
        evidence=tuple(evidence),
        call_affected=True,
    )


def review_flags(
    split_rows: Sequence[Mapping[str, Any]],
    bar_dates: Sequence[date],
    asof: date,
    *,
    skip: Callable[[Mapping[str, Any]], bool] = lambda r: False,
) -> list[DataFlag]:
    """A needs-review split whose first session the view has reached: not applied, so the numbers across it are
    in doubt. One flag per such split; a split whose date no cell reads still yields its flag (columns empty).
    """
    out: list[DataFlag] = []
    for r in split_rows:
        if r.get("status") != STATUS_NEEDS_REVIEW or skip(r):
            continue
        s = listed_from_row(r)
        last = bar_dates[-1] if bar_dates else asof
        if s.effective_date > min(asof, last):
            continue  # its first session is still ahead: nothing it could put in doubt yet
        marks = [Mark(s.effective_date, STEP)]
        groups = (PRICE, VOLUME, INSIDER)
        first = _first_bar_on_or_after(bar_dates, s.effective_date)
        k = float(s.factor)
        small = abs(math.log(k)) < math.log(1 + VENDOR_NOISE)
        columns = () if small else cells_for(marks, bar_dates, asof, groups)
        reason = (
            f"Split needs review: a {s.label} listed for {s.effective_date.isoformat()} "
            f"({_sources(r)}). It is not applied until it is confirmed, so prices across that day "
            "may sit on two scales."
        )
        if small:
            reason += (
                f" Its factor (×{k:.4g}) is within the vendor's own noise, so no number on the row moves "
                "by more than that."
            )
        elif not columns:
            reason += " No column on this row reads across it (history only)."
        out.append(
            DataFlag(
                kind=SPLIT_REVIEW,
                level=IN_DOUBT,
                reason=reason,
                clears="Clears when the split is confirmed or rejected in Admin → Splits.",
                since=s.effective_date,
                affects=groups,
                columns=columns,
                dates=(first,) if first else (),
                evidence=tuple(_sec_evidence(r)),
            )
        )
    return out


def settled_flaps(
    flap_dates: Iterable[date],
    tape: Sequence[TapeBar],
    listed: Sequence[ListedSplit],
    *,
    asof: date,
    unsettled: Iterable[date] = (),
) -> set[date]:
    """The S2 follow-up (split plan section 12a): the flapping bars whose NEWEST version sits on the split
    list's scale — SETTLED, so the old flip history draws no mixed-scale flag. A bar is settled when (1) a
    split on the list in force on ``asof`` has its first session AFTER it (the flip is that split's, and the
    price leg keeps the bar on the list's scale every night since S2), (2) the tape's island / cliff shapes do
    not touch it (``unsettled``), and (3) its newest close sits within T4's ``K_MIN`` of an adjacent bar's
    newest close (no basis step at the bar itself). Pure."""
    live = [s for s in listed if is_applicable(s.status, s.effective_date, on=asof)]
    bad = set(unsettled)
    by_d = {b.d: i for i, b in enumerate(tape)}
    out: set[date] = set()
    for d in flap_dates:
        if d in bad or not any(s.effective_date > d for s in live):
            continue
        i = by_d.get(d)
        if i is None or tape[i].close is None or tape[i].close <= 0:
            continue
        c = tape[i].close
        near = [
            tape[j].close
            for j in (i - 1, i + 1)
            if 0 <= j < len(tape) and tape[j].close is not None and tape[j].close > 0
        ]
        if any(abs(math.log(c / n)) < math.log(K_MIN) for n in near):
            out.add(d)
    return out


def remaining_flaps(
    versions: Mapping[date, Sequence[BarVersion]],
    tape: Sequence[TapeBar],
    listed: Sequence[ListedSplit],
    *,
    asof: date,
    shaped: Iterable[date] = (),
) -> tuple[list[Finding], list[Finding]]:
    """``(flaps, left)``: T4's flap findings on the live view's re-versioned bars, and each one minus its
    S2-settled bars (``settled_flaps``) — only the ones with bars LEFT. Pure; ``member_flags`` draws ``left``,
    and ``read_flags`` runs T4's name-level settle check (``split_basis.settle_flap``) on a name only when
    something is left (the cost bound: no extra read for a name the S2 rule already settles)."""
    flaps = flap_findings(versions, flap_since=asof - timedelta(days=FLAP_RECENT_DAYS))
    bad = set(shaped)
    left: list[Finding] = []
    for f in flaps:
        settled = settled_flaps(f.dates, tape, listed, asof=asof, unsettled=bad)
        rest = tuple(d for d in f.dates if d not in settled)
        if rest:
            left.append(Finding(FLAP, rest, f.factor))
    return flaps, left


def mixed_scale_flag(
    findings: Sequence[Finding], bar_dates: Sequence[date], asof: date
) -> DataFlag | None:
    """T4's island / cliff / flap findings on one tape as ONE flag (the reason lists each shape)."""
    if not findings:
        return None
    marks: list[Mark] = []
    parts: list[str] = []
    dates: set[date] = set()
    for f in sorted(findings, key=lambda f: f.dates[0] if f.dates else date.min):
        if not f.dates:
            continue
        k = _fmt_k(f.factor)
        if f.kind == ISLAND:
            marks += [Mark(d, BAR) for d in f.dates]
            dates.update(f.dates)
            span = (
                f.dates[0].isoformat()
                if len(f.dates) == 1
                else f"{f.dates[0].isoformat()} to {f.dates[-1].isoformat()}"
            )
            parts.append(
                f"{len(f.dates)} bar(s) ({span}) sit on another scale than the bars around them (×{k})"
            )
        elif f.kind == CLIFF:
            marks.append(Mark(f.dates[0], STEP))
            dates.add(f.dates[0])
            parts.append(
                f"the prices change scale by about ×{k} at {f.dates[0].isoformat()} (the price stepped up, "
                "the volume down, with no split on the list there)"
            )
        elif f.kind == FLAP:
            marks += [Mark(d, BAR) for d in f.dates]
            dates.update(f.dates)
            parts.append(
                f"{len(f.dates)} bar(s) keep switching scale between nightly pulls (×{k}; the newest "
                f"{f.dates[-1].isoformat()})"
            )
    if not marks:
        return None
    groups = (PRICE, VOLUME, INSIDER)
    columns = cells_for(marks, bar_dates, asof, groups)
    reason = "Mixed-scale price history: " + "; ".join(parts) + "."
    if not columns:
        reason += " No column on this row reads those bars (history only)."
    return DataFlag(
        kind=MIXED_SCALE,
        level=IN_DOUBT,
        reason=reason,
        clears=(
            "Clears when the split behind it is on the list and the stored history is repaired "
            "(Admin → Splits, then the split repair), or, for bars that keep switching, once the vendor "
            f"settles for {FLAP_RECENT_DAYS} days."
        ),
        since=min(m.d for m in marks),
        affects=groups,
        columns=columns,
        dates=tuple(sorted(dates)),
    )


def stopped_flag(
    edge: date | None,
    *,
    asof: date,
    stale_days: int,
    reading: StopReading | None,
    ticker: str | None,
) -> DataFlag | None:
    """A stopped price tape (``tape_health.is_tape_stale``): the "today" numbers are not today's, so they are
    known wrong. ``reading`` (the nightly classifier's, only on today's view) names WHY: renamed, closed, or
    an unpriceable name the operator acknowledged (quiet: no flag)."""
    if not is_tape_stale(edge, asof=asof, stale_days=stale_days):
        return None
    if reading is not None and reading.edge != edge:
        reading = None  # the nightly read a different stop: say only what the tape shows
    if edge is None:
        if reading is not None and reading.acknowledged:
            return None  # the operator's "keep it, unpriced" — quiet, still listed on Admin
        reason = "No price on file: the vendor serves no series for this name."
        clears = "Clears when a price symbol the vendor serves is set (Admin → Price feed repairs)."
    elif reading is not None and reading.renamed:
        listed = ", ".join(reading.edgar_tickers or ()) or "another symbol"
        reason = (
            f"Price feed stopped {edge.isoformat()}: renamed (EDGAR now lists {listed}); the vendor's "
            f"series under {ticker or 'this symbol'} ended."
        )
        clears = (
            "Clears when the price symbol points at the new listing (Admin → Price feed repairs)."
        )
    elif reading is not None and reading.closed_at is not None and reading.closed_at <= asof:
        reason = (
            f"Stopped trading {edge.isoformat()}: a delisting form was filed "
            f"{reading.closed_at.isoformat()}. The last prices are final, not today's."
        )
        clears = "Does not clear: the company stopped trading."
    else:
        reason = (
            f"Price feed stopped: no price since {edge.isoformat()} ({(asof - edge).days} days), so these "
            "are not today's numbers."
        )
        clears = (
            "Clears when the vendor serves new bars, or the price symbol is fixed (Admin → Price feed "
            "repairs)."
        )
    groups = (PRICE, VOLUME, MARKET_CAP)
    return DataFlag(
        kind=FEED_STOPPED,
        level=KNOWN_WRONG,
        reason=reason,
        clears=clears,
        since=edge,
        affects=groups,
        columns=all_cells(groups),
    )


def market_cap_flag(
    shares: Mapping[str, Any] | None,
    last_bar: date | None,
    split_rows: Sequence[Mapping[str, Any]],
    *,
    asof: date,
    steps: Sequence[tuple[date, float, str]] = (),
) -> DataFlag | None:
    """The market cap's share count against the price it is multiplied by (``workbench.scoring._market_cap``):

    - the ADS ratio could not be read → the cap is WITHHELD (known wrong — it already reads "—"; the flag says
      why on the Cockpit);
    - the count is dated before a split the list has NOT applied (``needs_review``), or before an unlisted
      split-shaped step (``steps``: the hold's step, a T4 cliff), with the newest close after it → in doubt:
      the cap may be off by that factor. A split IN FORCE is applied by the cap itself (S2) and flags nothing.
    """
    if not shares:
        return None
    sh_date = _day(shares["valid_from"])
    status = shares.get("ads_ratio_status")
    ratio = shares.get("ads_ratio")
    groups = (MARKET_CAP,)
    if last_bar is not None and (
        status == "unread" or (status == "known" and not (ratio and float(ratio) >= 1))
    ):
        return DataFlag(
            kind=MKTCAP_SHARES,
            level=KNOWN_WRONG,
            reason=(
                "Market cap withheld: this name trades as an ADR, and the ordinary shares per ADS could not "
                "be read from its filing, so the cap is not guessed at 1:1."
            ),
            clears="Clears when a share count with a known ratio is ratified (Workbench).",
            since=sh_date,
            affects=groups,
            columns=all_cells(groups),
        )
    if last_bar is None:
        return None
    hits: list[tuple[date, str]] = []
    for r in split_rows:
        if r.get("status") != STATUS_NEEDS_REVIEW:
            continue
        s = listed_from_row(r)
        if abs(math.log(float(s.factor))) < math.log(1 + VENDOR_NOISE):
            continue  # a factor within the vendor's own noise cannot put the cap in doubt
        if sh_date < s.effective_date <= last_bar:
            hits.append(
                (
                    s.effective_date,
                    f"the {s.label} on {s.effective_date.isoformat()} (needs review)",
                )
            )
    for d, k, what in steps:
        if sh_date < d <= last_bar:
            hits.append((d, f"{what} at {d.isoformat()} (about ×{_fmt_k(k)})"))
    if not hits:
        return None
    hits.sort()
    return DataFlag(
        kind=MKTCAP_SHARES,
        level=IN_DOUBT,
        reason=(
            f"Market cap in doubt: the share count is from {sh_date.isoformat()}, before "
            + "; ".join(h[1] for h in hits)
            + ", which the split list has not applied, so the cap may be off by that factor."
        ),
        clears="Clears when the split is confirmed (the count is then scaled) or a newer share count is filed.",
        since=hits[0][0],
        affects=groups,
        columns=all_cells(groups),
    )


def fundamentals_flag(lag: FundamentalsLag | None) -> DataFlag | None:
    """SEC's data is behind a filed report (``pipeline.fundamentals_lag.judge``): in doubt, reaching no Cockpit
    cell (no column reads fundamentals) — the NamePanel and Admin's quiet list say it. It clears on its own:
    the reading is the cached copy the nightly re-pulls once SEC's data carries the report."""
    if lag is None:
        return None
    filed = f"the {lag.form} filed {md(lag.filed)}"
    if lag.last_period is not None:
        reason = f"Fundamentals end at {md(lag.last_period)}; {filed} isn't in SEC's data yet."
    else:
        reason = (
            "SEC's data carries none of this company's recent 10-Q / 10-K reports; "
            f"{filed} isn't in it yet."
        )
    return DataFlag(
        kind=FUNDAMENTALS_LAG,
        level=IN_DOUBT,
        reason=reason,
        clears=(
            f"Clears on its own when SEC's data carries the {lag.form} for the period ending "
            f"{md(lag.period)} (read from the cached copy the nightly refreshes; no request)."
        ),
        since=lag.filed,
        evidence=(
            Evidence(label=f"SEC {lag.form} filed {md(lag.filed)}", url=lag.filing_url),
            Evidence(label="SEC's data (companyfacts)", url=lag.companyfacts_url),
        ),
    )


def split_labels(
    security_id: UUID,
    split_rows: Sequence[Mapping[str, Any]],
    *,
    asof: date,
    corrections: Mapping[tuple[str, date], Correction] | None = None,
    bar_dates: Sequence[date] = (),
) -> list[SplitLabel]:
    """The company's split rows for the NamePanel and the chart, oldest first: every LIVE row (confirmed,
    announced, needs review) — a rejected / revoked row is not a split and is left to Admin's set-aside list.
    ``corrected_on`` only when Alpha Deck re-scaled the vendor's bars before it, and only once the view has
    reached that day (no lookahead). ``bars_ago`` places the first bar at the new price on ``bar_dates``.
    """
    corr = corrections or {}
    out: list[SplitLabel] = []
    for r in sorted(split_rows, key=lambda r: _day(r["effective_date"])):
        if r.get("status") not in (*LIVE_STATUSES, STATUS_NEEDS_REVIEW):
            continue
        s = listed_from_row(r)
        first = next((i for i, d in enumerate(bar_dates) if d >= s.effective_date), None)
        c = corr.get((str(security_id), s.key_date)) or corr.get(
            (str(security_id), s.effective_date)
        )
        if c is not None and c.on > asof:
            c = None
        note = None
        if c is not None:
            if "raw" in c.labels:
                note = (
                    f"the vendor had not adjusted {c.bars} bar(s) before it; Alpha Deck re-scaled them "
                    "from the listed ratio"
                )
            else:
                note = f"Alpha Deck re-scaled {c.bars} bar(s) before it onto the listed ratio"
        via = r.get("confirmed_via")
        filed = r.get("sec_filed")
        out.append(
            SplitLabel(
                key_date=s.key_date,
                effective_date=s.effective_date,
                label=s.label,
                factor=float(s.factor),
                direction=direction(s.ratio_from, s.ratio_to),
                status=s.status,
                confirmed_via=via,
                confirmed_by=(
                    confirmed_by(via) if s.status in (STATUS_CONFIRMED, STATUS_ANNOUNCED) else None
                ),
                value_source=r.get("value_source"),
                sec_form=r.get("sec_form"),
                sec_filed=_day(filed) if filed else None,
                sec_url=r.get("sec_url"),
                polygon=bool(r.get("polygon_id")),
                note=r.get("note"),
                took_effect=s.effective_date <= asof
                and s.status in (STATUS_CONFIRMED, STATUS_ANNOUNCED),
                corrected_on=c.on if c else None,
                corrected_note=note,
                version_id=s.version_id,
                bars_ago=None if first is None else len(bar_dates) - 1 - first,
            )
        )
    return out


def confirmed_by(via: str | None) -> str | None:
    """How a split was confirmed, in the operator's words (None when it is not)."""
    return _CONFIRMED_BY.get(via or "")


def member_flags(
    security_id: UUID,
    ticker: str | None,
    *,
    bars: Sequence[Mapping[str, Any]],
    split_rows: Sequence[Mapping[str, Any]],
    asof: date,
    stale_days: int,
    versions: Mapping[date, Sequence[BarVersion]] | None = None,
    shares: Mapping[str, Any] | None = None,
    reading: StopReading | None = None,
    corrections: Mapping[tuple[str, date], Correction] | None = None,
    flaps_settled: bool = False,
    fundamentals_lag: FundamentalsLag | None = None,
) -> MemberFlags:
    """Every flag and quiet marker on ONE security as of ``asof`` — pure over the view's reads.

    ``bars`` = the view's whole price tape through ``asof`` (ascending); ``split_rows`` = the split list as
    the view knows it; ``versions`` = the bar version history for the flap test (the LIVE view only; ``None``
    on a history view); ``shares`` = the newest share-count fact; ``reading`` = the nightly classifier's read
    of a stopped tape (today's view only); ``flaps_settled`` = T4's name-level settle check found no reader
    sees this name's flapping history on two bases (``split_basis.settle_flap``, run by ``read_flags``) — the
    same rule that moves it to Admin's quiet settled line, so a flap Admin calls settled draws no ⚑ here.
    ``fundamentals_lag`` = SEC's data behind a filed report (``pipeline.fundamentals_lag``, today's view only).
    """
    bar_dates = _bar_dates(bars)
    flags: list[DataFlag] = []

    # 1. the S5 split-night hold — its own rule over its own bars window (what ``signals.split_hold.
    #    find_holds`` reads for the call: the view's last HOLD_LOOKBACK_DAYS of bars + its split list), so the
    #    flag and the card's held line can never disagree
    hold: Hold | None = None
    if bar_dates:
        cutoff = asof - timedelta(days=HOLD_LOOKBACK_DAYS)
        recent = [b for b in bars if _day(b["d"]) >= cutoff]
        hold = hold_for(recent, split_rows, asof=asof)
        if hold is not None:
            near_rows = list(nearby(hold.step, split_rows))
            flags.append(hold_flag(hold, bar_dates, asof, near_rows))

    # 2. needs-review splits the view has reached (the one a hold already names is not flagged twice)
    def named_by_hold(r: Mapping[str, Any]) -> bool:
        if hold is None:
            return False
        return abs(session_distance(_day(r["effective_date"]), hold.step.d)) <= NEAR_SESSIONS

    flags += review_flags(split_rows, bar_dates, asof, skip=named_by_hold)

    # 3. mixed-scale history: T4 over the whole tape; flaps (live only) minus the settled bars — none at all
    #    when T4's name-level settle check found no reader sees them on two bases
    tape = [TapeBar(_day(b["d"]), _num(b.get("close")), _num(b.get("volume"))) for b in bars]
    findings = tape_findings(tape)
    if versions and not flaps_settled:
        listed = [listed_from_row(r) for r in split_rows]
        shaped = {d for f in findings for d in f.dates}
        findings += remaining_flaps(versions, tape, listed, asof=asof, shaped=shaped)[1]
    mixed = mixed_scale_flag(findings, bar_dates, asof)
    if mixed is not None:
        flags.append(mixed)

    # 4. a stopped feed
    stopped = stopped_flag(
        bar_dates[-1] if bar_dates else None,
        asof=asof,
        stale_days=stale_days,
        reading=reading,
        ticker=ticker,
    )
    if stopped is not None:
        flags.append(stopped)

    # 5. the market cap's share count (not on a stopped feed: that flag already covers the cap)
    if stopped is None:
        steps: list[tuple[date, float, str]] = []
        if hold is not None:
            steps.append(
                (hold.step.d, max(hold.step.step, 1 / hold.step.step), "a split-shaped step")
            )
        steps += [
            (f.dates[0], f.factor, "an unlisted split-shaped step")
            for f in findings
            if f.kind == CLIFF
        ]
        cap = market_cap_flag(
            shares, bar_dates[-1] if bar_dates else None, split_rows, asof=asof, steps=steps
        )
        if cap is not None:
            flags.append(cap)

    # 6. SEC's data behind a filed report (no Cockpit cell reads fundamentals: panel + Admin only)
    lagging = fundamentals_flag(fundamentals_lag)
    if lagging is not None:
        flags.append(lagging)

    # the quiet marker: information, not a defect (never a row mark)
    note = short_history_note(bar_dates, asof)

    return MemberFlags(
        security_id=security_id,
        ticker=ticker,
        flags=tuple(sort_flags(flags)),
        splits=tuple(
            split_labels(
                security_id, split_rows, asof=asof, corrections=corrections, bar_dates=bar_dates
            )
        ),
        notes=(note,) if note is not None else (),
    )


# --- the read --------------------------------------------------------------------------------------------

_VERSIONS_SQL = """
    WITH multi AS (
        SELECT p.security_id, p.d
          FROM fact_price_eod p
         WHERE p.tenant_id = %(tenant_id)s AND p.security_id = ANY(%(ids)s) AND p.d <= %(asof)s
         GROUP BY p.security_id, p.d
        HAVING count(*) > 1 AND max(p.recorded_at) >= %(flap_since)s
    )
    SELECT p.security_id, p.d, p.recorded_at, p.close
      FROM fact_price_eod p
      JOIN multi m ON m.security_id = p.security_id AND m.d = p.d
     WHERE p.tenant_id = %(tenant_id)s
     ORDER BY p.security_id, p.d, p.recorded_at, p.id
"""


def read_flags(
    conn: Any,
    *,
    tenant_id: UUID,
    security_ids: Sequence[UUID],
    tickers: Mapping[UUID, str | None],
    asof: date,
    known_at: datetime | None,
    clock: str,
    stale_days: int,
    readings: Mapping[UUID, StopReading] | None = None,
    corrections: Mapping[tuple[str, date], Correction] | None = None,
    edgar_cache_dir: Path | None = None,
) -> dict[UUID, MemberFlags]:
    """Every requested security's ``MemberFlags`` as of ``asof`` on the view's clock (``serve_view``): ONE
    point-in-time view over the whole scope (each fact table read in one batched query), plus — on the live
    view only — one query for the recently re-versioned bars the flap test reads and one for the identities
    the fundamentals reading needs (``fundamentals_lags``, over the EDGAR cache on disk). SELECTs only.
    """
    from signals.base import (
        PointInTimeData,
    )  # the concrete PIT (the read path's, never the call's view)

    ids = list(dict.fromkeys(security_ids))
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
        basket=ids,
        bounds=None,
        clock=clock,  # type: ignore[arg-type]
    )
    live = clock == "record"
    versions: dict[UUID, dict[date, list[BarVersion]]] = defaultdict(lambda: defaultdict(list))
    if live and ids:
        with conn.cursor() as cur:
            cur.execute(
                _VERSIONS_SQL,
                {
                    "tenant_id": tenant_id,
                    "ids": ids,
                    "asof": asof,
                    "flap_since": asof - timedelta(days=FLAP_RECENT_DAYS),
                },
            )
            for r in cur.fetchall():
                versions[r["security_id"]][r["d"]].append(
                    BarVersion(r["recorded_at"], _num(r["close"]))
                )
    settled_names = _settled_flap_names(conn, pit, versions, tenant_id=tenant_id, asof=asof)
    lags = (
        fundamentals_lags(conn, tenant_id=tenant_id, ids=ids, asof=asof, cache_dir=edgar_cache_dir)
        if live
        else {}
    )
    out: dict[UUID, MemberFlags] = {}
    for sid in ids:
        shares_facts = pit.shares_outstanding_facts(sid)
        shares = max(shares_facts, key=lambda f: f["valid_from"]) if shares_facts else None
        reading = (readings or {}).get(sid) if live else None
        out[sid] = member_flags(
            sid,
            tickers.get(sid),
            bars=pit.price_history(sid),
            split_rows=pit.split_events(sid),
            asof=asof,
            stale_days=stale_days,
            versions=versions.get(sid) if live else None,
            shares=shares,
            reading=reading,
            corrections=corrections,
            flaps_settled=sid in settled_names,
            fundamentals_lag=lags.get(sid),
        )
    return out


# A company still loading its history (X1, ``security_onboarding``) is not judged: MEASURED 2026-10-24, 65 of the
# 73 basket companies missing their latest report from OUR store were queued there. ``pipeline.onboarding``'s
# own constant, copied so this read path does not import the onboarding job's graph; pinned equal by test.
ONBOARDING_DONE = "done"

_IDENTITY_SQL = """
    SELECT sm.id, sm.cik, sm.instrument_kind,
           (SELECT so.status FROM security_onboarding so
             WHERE so.tenant_id = sm.tenant_id AND so.security_id = sm.id) AS onboarding
      FROM security_master sm
     WHERE sm.tenant_id = %(tenant_id)s AND sm.id = ANY(%(ids)s)
"""


def fundamentals_lags(
    conn: Any,
    *,
    tenant_id: UUID,
    ids: Sequence[UUID],
    asof: date,
    cache_dir: Path | None = None,
) -> dict[UUID, FundamentalsLag]:
    """``security_id -> FundamentalsLag`` for the companies SEC's data is behind (``pipeline.fundamentals_lag``),
    read from the EDGAR cache on disk — never a request. Judged: a company with a CIK, not a fund
    (``instrument_kind = 'etf'``), and not still onboarding (a ``security_onboarding`` row that is not done;
    no row = a legacy name, judged). Fail-open per company: an unreadable file is no flag."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(_IDENTITY_SQL, {"tenant_id": tenant_id, "ids": list(ids)})
        rows = cur.fetchall()
    out: dict[UUID, FundamentalsLag] = {}
    for r in rows:
        cik = r["cik"]
        if not cik or r["instrument_kind"] == "etf":
            continue
        if r["onboarding"] is not None and r["onboarding"] != ONBOARDING_DONE:
            continue
        try:
            lag = read_lag(cik, asof=asof, cache_dir=cache_dir)
        except Exception:  # noqa: BLE001 — a bad file is no flag, never a failed read of every flag
            continue
        if lag is not None:
            out[r["id"]] = lag
    return out


def _settled_flap_names(
    conn: Any,
    pit: Any,
    versions: Mapping[UUID, Mapping[date, Sequence[BarVersion]]],
    *,
    tenant_id: UUID,
    asof: date,
) -> set[UUID]:
    """The live-view names whose flap T4's settle rule SETTLES (``split_basis.settle_flap`` — the newest
    tape has no break and no night's history view reads two bases): the rule Admin's quiet settled line
    uses, so the two surfaces agree. Judged only for a name with flap bars LEFT after the S2 per-bar rule
    (``remaining_flaps``) — any other name draws no flap anyway — in ONE read of their public-clock versions
    (``split_basis.read_public_tapes``). Empty on a history view (no ``versions``). SELECTs only."""
    need: dict[UUID, tuple[date, ...]] = {}
    for sid, vs in versions.items():
        if not vs:
            continue
        bars = pit.price_history(sid)
        tape = [TapeBar(_day(b["d"]), _num(b.get("close")), _num(b.get("volume"))) for b in bars]
        shaped = {d for f in tape_findings(tape) for d in f.dates}
        listed = [listed_from_row(r) for r in pit.split_events(sid)]
        flaps, left = remaining_flaps(vs, tape, listed, asof=asof, shaped=shaped)
        if left:
            need[sid] = tuple(sorted({d for f in flaps for d in f.dates}))
    if not need:
        return set()
    span, back = window_days(), view_lookback_days()
    tapes = read_public_tapes(
        conn,
        tenant_id=tenant_id,
        security_ids=list(need),
        asof=asof,
        since=asof - timedelta(days=span + back),
    )
    out: set[UUID] = set()
    for sid, dates in need.items():
        t = tapes.get(sid)
        if t is None:
            continue  # nothing to judge on is never settled
        verdict = settle_flap(
            dates,
            t.versions,
            asof=asof,
            window=span,
            lookback=back,
            split_rows=pit.split_events(sid),
            store_through=t.store_through,
        )
        if verdict.settled:
            out.add(sid)
    return out
