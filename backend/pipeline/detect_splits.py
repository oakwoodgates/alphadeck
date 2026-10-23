"""``pipeline.detect_splits`` — split plan S4: find NEW splits every night and write them to the split list.

S1 (``pipeline/backfill_splits.py``) filled the list once from Polygon's split reference, each split checked
against the company's own SEC filing. This module keeps it filled. ONE rule decides, S1's (``domain/splits.py``):
a split is auto-confirmed only when two independent sources agree on the ratio (exactly) and the first session
(exactly); everything else is ``needs_review`` for the operator (the Admin "Splits" panel). Nothing is ever
UPDATEd or deleted: a change is a new version; a revoke is the operator's.

THE SOURCES, per basket company:

1. **SEC filings** — the 8-Ks the nightly stores with items 3.03 / 5.03 / 7.01 / 8.01 (``fact_corporate_event``;
   S1's ``SPLIT_8K_ITEMS``) and, from
   the company's submissions index (already fetched by the Form 4 leg), its 6-Ks — the foreign filers (CPOP,
   MI, MPU, REAX, SMX, PBM) announce there — and Reg A forms. Each filing is read ONCE as its full submission
   (``forms/*``, immutable, cached forever) through the pass's EDGAR client — its declared User-Agent, its rate
   limit and the GLOBAL SEC budget (``ingest/sec_budget.py``) — and read by S1's deterministic extractor
   (``ingest/edgar/split_filing.py``): fixed patterns, the LOCATED passage kept verbatim. No model (#3).
2. **Polygon's split reference** — ONE date-filtered request a night for the whole market
   (``execution_date >= asof - POLYGON_LOOKBACK_DAYS``; announced future splits included), on the free-tier
   budget the fund-shares leg shares (``ingest/polygon_splits.py``). With no ``POLYGON_API_KEY`` this source is
   OFF and the report says so in one line; the SEC and tape sources still run.
3. **The price tape, as a CONFIRMING source only** (``domain.splits.with_tape``): a split ONE documentary source
   states (Polygon's row with no SEC filing disagreeing, or an SEC filing stating the first trading session)
   is confirmed when the stored tape steps by about its ratio exactly on that session, the volume moving the
   other way — read on each bar as the vendor FIRST served it. The tolerance is MEASURED (``TAPE_*``). The tape
   never confirms a split alone, never arbitrates two sources that disagree, never confirms a forward split.

THE DECISIONS, per company (``detect_security``): each Polygon row is decided EXACTLY as S1 decides it
(``backfill_splits.decide_polygon_group``); an SEC filing read in the window that no Polygon row explains is an
SEC-only candidate (``domain.splits.decide_sec_only``); then the tape. A filing that mentions a split the list
cannot place (no ratio, no date, an extractor abstain) is listed on the run artifact (``unplaced``), never
dropped silently (#9).

THE PLAN against the stored list (``plan_against``): a split is found by S1's ``match_stored``. A new one is
appended. A stored one is appended to ONLY when the system wrote its newest version, it is still OPEN
(``needs_review`` / ``announced``), tonight's evidence covers every source it already carries (a source missing
tonight — Polygon off, a filing unread — never downgrades it), and the content changed: a second source arrived,
the tape confirmed it, or an announced split's first session came (announced -> confirmed). A confirmed,
rejected or revoked split, and anything the operator decided, is never touched; when tonight's evidence
differs from such a split it is listed on the artifact (``kept``), not written.

WHERE IT RUNS (``pipeline/ingest_thesis._ingest_one``): per company, right after its 8-K and 13D legs and
BEFORE its price leg — so a split filed today, with a second source, is on the list before that company's
price leg (S2 applies it on its first night) and before tonight's call. A company held in several theses is
checked once a pass. The Polygon request is made once, before the per-thesis loop (``SplitDetector.start``).
Fail-open: a fault is rolled back, recorded on the run artifact (``split_detect``) and printed; it never costs a
name its ingest or the night its record.

    python -m pipeline.detect_splits                       # dry run over the basket union (read-only)
    python -m pipeline.detect_splits --apply               # append (holds the call-of-record lock)
    python -m pipeline.detect_splits --sec-days 90 --polygon-days 90 --ticker BTOC --no-live
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

import psycopg

from domain.market_time import market_today, market_tz
from domain.splits import (
    STATUS_ANNOUNCED,
    STATUS_NEEDS_REVIEW,
    PolygonEvidence,
    SecEvidence,
    SplitDecision,
    TapeEvidence,
    cluster_sec,
    decide,
    decide_sec_only,
    ratio_label,
    related,
    same_ratio,
    with_tape,
)
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.polygon_splits import PolygonSplitsError, PolygonSplitSource, default_split_source
from pipeline.backfill_splits import (
    Member,
    SecReader,
    _conflicting,
    _group,
    decide_polygon_group,
    match_polygon,
    match_stored,
    union_members,
)
from repositories import split_repo
from repositories.split_repo import (
    ACTION_DETECT,
    AUTHOR_DETECT,
    AUTHOR_OPERATOR,
    SplitVersion,
    content_of,
    decision_values,
)

_log = logging.getLogger("alphadeck.detect_splits")

# The ONE Polygon request a night: every split with an execution date on/after asof - this (Polygon lists
# announced future splits too). 30 days covers a split whose first session was this month and whose second
# source (a late 8-K, the tape on the next night) arrives later — MEASURED: 16 of the list's 137 SEC filings
# came after the split, the latest 7 days after (KULR).
POLYGON_LOOKBACK_DAYS = 30
# SEC-only candidates: split filings FILED in the last this many days are re-read every night, so a candidate
# keeps being re-decided until its second source (Polygon, or the tape the night after its first session)
# arrives. MEASURED: 103 of the list's 137 SEC filings came at least a day before the split; the earliest
# confirming one 2026 ran ~6 weeks ahead. Each filing is fetched once ever (immutable cache).
SEC_LOOKBACK_DAYS = 60
# a stored bar this many calendar days before a first session is the farthest "prior bar" the tape reads
TAPE_PRIOR_MAX_DAYS = 14

APPEND = "append"
UNCHANGED = "unchanged"
KEPT = "kept"

EXIT_REFUSED = 2


# --- the tape ----------------------------------------------------------------------------------------------


def tape_at(
    conn: psycopg.Connection, *, tenant_id: UUID, security_id: UUID, session: date
) -> TapeEvidence | None:
    """The stored tape at ``session``: that bar and the stored bar before it (within ``TAPE_PRIOR_MAX_DAYS``),
    each its FIRST-SERVED version (the earliest recorded — the vendor's as-traded print, before any later
    re-basing). ``None`` when either bar is missing or carries no positive close. A READ, never a write.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (d) d, close, volume, recorded_at FROM fact_price_eod "
            "WHERE tenant_id = %s AND security_id = %s AND d BETWEEN %s AND %s "
            "ORDER BY d, recorded_at, id",
            (tenant_id, security_id, session - timedelta(days=TAPE_PRIOR_MAX_DAYS), session),
        )
        rows = {r["d"]: r for r in cur.fetchall()}
    bar = rows.get(session)
    prior_days = [d for d in rows if d < session]
    if bar is None or not prior_days:
        return None
    prior = rows[max(prior_days)]
    if not bar["close"] or not prior["close"] or bar["close"] <= 0 or prior["close"] <= 0:
        return None

    def _f(v: Any) -> float | None:
        return None if v is None else float(v)

    return TapeEvidence(
        session=session,
        prior=prior["d"],
        prior_close=float(prior["close"]),
        close=float(bar["close"]),
        prior_volume=_f(prior["volume"]),
        volume=_f(bar["volume"]),
        prior_served_before=prior["recorded_at"].astimezone(market_tz()).date() < session,
    )


# --- one company -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unplaced:
    """An SEC filing that talks about a split the list could not place (no ratio, no date, an abstain)."""

    security_id: UUID
    ticker: str | None
    accession: str
    form: str
    filed: date
    reason: str

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "accession": self.accession,
            "form": self.form,
            "filed": self.filed.isoformat(),
            "reason": self.reason,
        }


@dataclass
class SecurityDetection:
    """What tonight's evidence says about ONE company: its decisions (Polygon-keyed first, then SEC-only),
    the filings it could not place, and the reads."""

    member: Member
    decisions: list[SplitDecision] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)
    filings_read: int = 0
    filings_unread: int = 0


# an abstain that is no news: a filing restating a PAST split (LGHL's interim results) or with no ratio at all
_NOT_NEWS = ("only a past split's ratio is stated",)


def detect_security(
    conn: psycopg.Connection,
    reader: SecReader,
    m: Member,
    polygon_rows: Iterable[PolygonEvidence],
    *,
    asof: date,
    sec_since: date,
) -> SecurityDetection:
    """Every split tonight's evidence states for ONE company (the module docstring). Reads only."""
    out = SecurityDetection(member=m)
    absorbed: set[str] = set()
    for group in _group(list(polygon_rows)):
        d, read, unread = decide_polygon_group(conn, reader, m, group, asof=asof)
        absorbed.update(s.accession for s in read if related(s, group[0]))
        # the tape settles a Polygon-only row only when nothing else could disagree: no conflicting Polygon
        # rows, every SEC filing in its window read, and the company's index readable
        settled_alone = (
            not (len(group) > 1 and _conflicting(group))
            and unread == 0
            and (not m.cik or reader.submissions.get(m.cik) is not None)
        )
        if d.status == STATUS_NEEDS_REVIEW and settled_alone:
            d = with_tape(
                d,
                tape_at(
                    conn, tenant_id=m.tenant_id, security_id=m.security_id, session=d.effective_date
                ),
            )
        out.decisions.append(d)
    # the SEC side on its own: the split filings filed in the lookback that no Polygon row explains
    refs = reader.refs(conn, m, sec_since, asof)
    read, unread = reader.read(m, refs)
    # each filing counted once, whichever window read it
    seen = reader.read_here
    out.filings_read = sum(1 for a in seen if reader.evidence[a] is not None)
    out.filings_unread = sum(1 for a in seen if reader.evidence[a] is None)
    fresh = [s for s in read if s.accession not in absorbed]
    for g in cluster_sec(fresh):
        d = decide_sec_only(g, asof=asof)
        if d.effective_date <= asof:
            d = with_tape(
                d,
                tape_at(
                    conn, tenant_id=m.tenant_id, security_id=m.security_id, session=d.effective_date
                ),
            )
        out.decisions.append(d)
    for s in fresh:
        if s.has_ratio and s.effective_date is not None:
            continue
        if not s.about_a_split or (s.abstain_reason or "") in _NOT_NEWS:
            continue
        why = s.abstain_reason or s.date_note or "no first session stated"
        if s.has_ratio:
            why = f"{ratio_label(s.ratio_from, s.ratio_to)} stated, but {why}"  # type: ignore[arg-type]
        out.unplaced.append(Unplaced(m.security_id, m.ticker, s.accession, s.form, s.filed, why))
    return out


# --- the plan against the stored list ----------------------------------------------------------------------


@dataclass
class Planned:
    """One decision, the stored split it is about (``None`` = new), and what the plan does with it."""

    member: Member
    decision: SplitDecision
    stored: SplitVersion | None
    action: str
    reason: str = ""

    @property
    def values(self) -> dict[str, Any]:
        return decision_values(self.decision)


def _sources(d: SplitDecision) -> str:
    """The evidence a decision rests on, in the operator's words ("8-K + Polygon", "6-K + tape")."""
    via = d.confirmed_via or ""
    if via.endswith("+tape"):
        first = d.sec.form if via.startswith("auto:sec") and d.sec else "Polygon"
        return f"{first} + tape"
    parts = []
    if d.sec is not None and d.sec.has_ratio:
        parts.append(d.sec.form)
    if d.polygon is not None:
        parts.append("Polygon")
    return " + ".join(parts) or "no source"


def _stored_polygon(v: SplitVersion) -> PolygonEvidence | None:
    """The Polygon row a stored version carries, as evidence (``None`` when it carries none)."""
    if not (
        v.polygon_id
        and v.polygon_execution_date
        and v.polygon_split_from is not None
        and v.polygon_split_to is not None
    ):
        return None
    return PolygonEvidence(
        id=v.polygon_id,
        ticker=v.polygon_ticker or "",
        execution_date=v.polygon_execution_date,
        split_from=v.polygon_split_from,
        split_to=v.polygon_split_to,
    )


# What makes a new version of an open split worth appending: its status / values / pairing, or its EVIDENCE
# (which filing, which Polygon row and what each states) — and a tape reading it did not carry before. A note
# phrased differently over the same evidence is not news (the S1 path and the late-filing path word it apart).
_MATERIAL = (
    "effective_date",
    "ratio_from",
    "ratio_to",
    "status",
    "confirmed_via",
    "value_source",
    "sec_accession",
    "sec_effective_date",
    "sec_date_kind",
    "sec_ratio_from",
    "sec_ratio_to",
    "polygon_id",
    "polygon_execution_date",
    "polygon_split_from",
    "polygon_split_to",
)
_TAPE_MARK = "Tape as first served"


def _material(d: SplitDecision, v: SplitVersion) -> bool:
    new = decision_values(d)
    if any(
        content_of({c: new.get(c)})[split_repo.CONTENT_COLUMNS.index(c)]
        != content_of({c: getattr(v, c)})[split_repo.CONTENT_COLUMNS.index(c)]
        for c in _MATERIAL
    ):
        return True
    return _TAPE_MARK in d.note and _TAPE_MARK not in (v.note or "")


def _same_values(d: SplitDecision, v: SplitVersion) -> bool:
    """Does tonight's decision state what the stored split already holds (ratio exactly, first session)?"""
    return d.effective_date == v.effective_date and same_ratio(
        d.ratio_from, d.ratio_to, v.ratio_from, v.ratio_to
    )


def plan_against(
    detection: SecurityDetection, stored: Iterable[SplitVersion], *, asof: date
) -> list[Planned]:
    """Each decision against the stored list (the module docstring's rule). Pure: no I/O.

    One refinement of the rule: a filing that arrives AFTER its split's Polygon row left tonight's pull (a late
    8-K, filed weeks after the split — FDCT's came 47 days later, outside S1's window) is decided against the
    Polygon row the stored split already carries, by S1's own ``decide`` — so the two sources still meet.
    """
    vs = list(stored)
    claimed: set[date] = set()
    out: list[Planned] = []
    for d in detection.decisions:
        v = match_stored(d, vs)
        if v is None:
            out.append(Planned(detection.member, d, None, APPEND, "new"))
            # a later decision about the same (new) split matches this one, never lists it twice
            vs.append(_as_version(detection.member, d))
            claimed.add(d.key_date)
            continue
        if d.polygon is None and d.sec is not None and d.sec.has_ratio:
            poly = _stored_polygon(v)
            if poly is not None:
                d = decide(poly, [d.sec], asof=asof)
        d = replace(d, key_date=v.key_date)
        if v.key_date in claimed:
            out.append(Planned(detection.member, d, v, KEPT, "already planned tonight"))
            continue
        claimed.add(v.key_date)
        if content_of(decision_values(d)) == v.content():
            out.append(Planned(detection.member, d, v, UNCHANGED))
            continue
        why = None
        if v.author == AUTHOR_OPERATOR:
            why = "the operator's decision stands"
        elif v.status not in (STATUS_NEEDS_REVIEW, STATUS_ANNOUNCED):
            why = f"{v.status}: settled, the operator's to change"
        elif (v.polygon_id and d.polygon is None) or (v.sec_accession and d.sec is None):
            why = "a source the listed split carries is missing tonight"
        if why is None:
            if _material(d, v):
                out.append(Planned(detection.member, d, v, APPEND, "updated"))
            else:
                # only the note's wording differs (another path phrased the same evidence): not a version
                out.append(Planned(detection.member, d, v, UNCHANGED, "same evidence"))
        elif _same_values(d, v):
            # tonight's (narrower) evidence states what the list already holds: nothing to tell
            out.append(Planned(detection.member, d, v, UNCHANGED, why))
        else:
            out.append(Planned(detection.member, d, v, KEPT, why))
    return out


def _as_version(m: Member, d: SplitDecision) -> SplitVersion:
    """A planned new split as a stored-version stand-in, so the rest of the plan matches against it."""
    vals = decision_values(d)
    return SplitVersion(
        id=UUID(int=0),
        tenant_id=m.tenant_id,
        security_id=m.security_id,
        key_date=d.key_date,
        price_factor=d.ratio_from / d.ratio_to,
        action=ACTION_DETECT,
        author=AUTHOR_DETECT,
        recorded_at=datetime.min,
        **vals,
    )


# --- the pass ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detected:
    """One version the detector appended (the run artifact row and the report line)."""

    security_id: UUID
    ticker: str | None
    key_date: date
    effective_date: date
    label: str
    status: str
    confirmed_via: str | None
    sources: str
    verb: str  # "new" | "updated"
    note: str

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "key_date": self.key_date.isoformat(),
            "effective_date": self.effective_date.isoformat(),
            "label": self.label,
            "status": self.status,
            "confirmed_via": self.confirmed_via,
            "sources": self.sources,
            "verb": self.verb,
            "note": self.note,
        }

    @property
    def line(self) -> str:
        who = self.ticker or str(self.security_id)
        what = "needs review" if self.status == STATUS_NEEDS_REVIEW else self.status
        again = "; updated" if self.verb == "updated" else ""
        return f"{who} {self.label} {what} ({self.sources}{again})"


@dataclass
class SplitDetection:
    """One pass's detection outcome — what rides the run artifact (``payload``) and the report (one line)."""

    evaluated: bool = False
    polygon: str = "not run"
    polygon_rows: int = 0
    polygon_live_pages: int = 0
    checked: int = 0
    filings_read: int = 0
    filings_unread: int = 0
    appended: list[Detected] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)
    kept: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def payload(self) -> dict:
        """The run artifact's ``split_detect`` block (readers use ``.get``: an older artifact lacks it)."""
        return {
            "split_detect": {
                "evaluated": self.evaluated,
                "polygon": self.polygon,
                "polygon_rows": self.polygon_rows,
                "polygon_live_pages": self.polygon_live_pages,
                "checked": self.checked,
                "filings_read": self.filings_read,
                "filings_unread": self.filings_unread,
                "appended": [a.to_json() for a in self.appended],
                "unplaced": [u.to_json() for u in self.unplaced],
                "kept": list(self.kept),
                "errors": list(self.errors),
                "duration_s": round(self.duration_s, 3),
            }
        }

    def report_lines(self) -> list[str]:
        """ONE quiet line on a normal night (nothing at all when nothing ran and nothing is off): the new
        list rows, the review count, the unplaced count; a separate line when Polygon is off or failed, and
        one per fault. Never a page (#7) — a needs-review split shows on the Admin "Splits" panel.
        """
        if not self.evaluated:
            return []
        lines: list[str] = []
        if self.polygon != "on":
            lines.append(
                f"splits: Polygon {self.polygon} — SEC + tape only, nothing confirms on Polygon"
            )
        parts = []
        if self.appended:
            shown = "; ".join(a.line for a in self.appended[:8])
            more = f"; +{len(self.appended) - 8} more" if len(self.appended) > 8 else ""
            parts.append(f"{len(self.appended)} list row(s) — {shown}{more}")
        else:
            parts.append("no new list rows")
        if self.unplaced:
            parts.append(f"{len(self.unplaced)} split filing(s) not placed (run artifact)")
        if self.kept:
            parts.append(
                f"{len(self.kept)} listed split(s) left as decided though tonight's evidence differs"
            )
        lines.append(
            f"splits: {'; '.join(parts)} · {self.checked} companies, {self.filings_read} filing(s) read"
            + (f", {self.filings_unread} unread" if self.filings_unread else "")
        )
        lines.extend(f"  splits ERROR: {e}" for e in self.errors)
        return lines


class SplitDetector:
    """The pass's detector: the Polygon pull (made once, ``start``), the per-company check the ingest calls
    (``check``), and the outcome (``result``). One per pass; a company is checked once."""

    def __init__(
        self,
        *,
        asof: date,
        polygon_rows: list[PolygonEvidence] | None,
        polygon_status: str,
        sec_days: int = SEC_LOOKBACK_DAYS,
        apply: bool = True,
    ) -> None:
        self.asof = asof
        self.sec_since = asof - timedelta(days=sec_days)
        self.polygon_rows = polygon_rows or []
        self.apply = apply
        self.result = SplitDetection(
            evaluated=True,
            polygon=polygon_status,
            polygon_rows=len(self.polygon_rows),
        )
        self._done: set[UUID] = set()
        self._evidence: dict[str, SecEvidence | None] = {}
        self.planned: list[Planned] = []
        self.detections: list[SecurityDetection] = []

    @classmethod
    def start(
        cls,
        *,
        asof: date,
        allow_live: bool,
        polygon_days: int = POLYGON_LOOKBACK_DAYS,
        sec_days: int = SEC_LOOKBACK_DAYS,
        source: PolygonSplitSource | None = None,
        apply: bool = True,
    ) -> SplitDetector:
        """The ONE Polygon request of the pass (fail-open: an off / failed / uncached source is a status on
        the outcome, and the SEC + tape sources still run)."""
        t0 = perf_counter()
        polygon = source if source is not None else default_split_source()
        rows: list[PolygonEvidence] | None = None
        live_pages = 0
        if polygon is None:
            status = "off (no POLYGON_API_KEY)"
        else:
            try:
                pull = polygon.splits_since(
                    asof - timedelta(days=polygon_days), allow_live=allow_live
                )
                rows, live_pages, status = pull.rows, pull.live_pages, "on"
                if pull.skipped:
                    status = f"on ({pull.skipped} unreadable row(s) skipped)"
            except CacheMiss:
                status = "not cached for today (a --no-live pass)"
            except PolygonSplitsError as e:
                status = f"failed: {e}"[:200]
            except Exception as e:  # noqa: BLE001 — a source fault never costs the pass
                status = f"failed: {type(e).__name__}: {e}"[:200]
        det = cls(
            asof=asof, polygon_rows=rows, polygon_status=status, sec_days=sec_days, apply=apply
        )
        det.result.polygon_live_pages = live_pages
        det.result.duration_s += perf_counter() - t0
        return det

    def _rows_for(self, m: Member) -> list[PolygonEvidence]:
        return match_polygon(self.polygon_rows, [m]).get(m, [])

    def check(self, conn: psycopg.Connection, client: Any, sec: Any, *, tenant_id: UUID) -> int:
        """Detect + plan + (when ``apply``) append for ONE company (``sec`` = its master row). Returns the
        versions appended. The CALLER owns the transaction (the ingest commits it, or rolls back on a raise);
        a raise here is the caller's to record (``fault``)."""
        if sec.id in self._done:
            return 0
        self._done.add(sec.id)
        t0 = perf_counter()
        m = Member(
            tenant_id=tenant_id,
            security_id=sec.id,
            ticker=sec.ticker,
            price_symbol=getattr(sec, "price_symbol", None),
            cik=sec.cik,
        )
        reader = SecReader(client, memo_dir=_memo_dir(client))
        reader.evidence = self._evidence  # each filing read once a pass, whichever company reads it
        try:
            det = detect_security(
                conn, reader, m, self._rows_for(m), asof=self.asof, sec_since=self.sec_since
            )
            self.detections.append(det)
            self.result.checked += 1
            self.result.filings_read += det.filings_read
            self.result.filings_unread += det.filings_unread
            self.result.unplaced.extend(det.unplaced)
            stored = split_repo.latest_versions(conn, tenant_id=tenant_id, security_ids=[sec.id])
            plan = plan_against(det, stored, asof=self.asof)
            self.planned.extend(plan)
            for p in plan:
                if p.action == KEPT and p.reason != "already planned tonight":
                    self.result.kept.append(
                        {
                            "security_id": str(sec.id),
                            "ticker": sec.ticker,
                            "key_date": p.stored.key_date.isoformat() if p.stored else None,
                            "status": p.stored.status if p.stored else None,
                            "reason": p.reason,
                            "tonight": p.decision.note[:400],
                        }
                    )
            todo = [p for p in plan if p.action == APPEND]
            if not self.apply or not todo:
                return 0
            before = _count(conn, tenant_id, sec.id)
            for p in todo:
                split_repo.append_version(
                    conn,
                    tenant_id=tenant_id,
                    security_id=sec.id,
                    key_date=p.decision.key_date,
                    values=p.values,
                    action=ACTION_DETECT,
                    author=AUTHOR_DETECT,
                )
            after = _count(conn, tenant_id, sec.id)
            if after - before != len(todo):
                raise RuntimeError(
                    f"the split list grew by {after - before} rows for {sec.ticker}, the plan was {len(todo)}"
                )
            for p in todo:
                d = p.decision
                self.result.appended.append(
                    Detected(
                        security_id=sec.id,
                        ticker=sec.ticker,
                        key_date=d.key_date,
                        effective_date=d.effective_date,
                        label=ratio_label(d.ratio_from, d.ratio_to),
                        status=d.status,
                        confirmed_via=d.confirmed_via,
                        sources=_sources(d),
                        verb=p.reason,
                        note=d.note[:600],
                    )
                )
            return len(todo)
        finally:
            self.result.duration_s += perf_counter() - t0

    def fault(self, sec: Any, exc: BaseException) -> None:
        """Record a check that raised (the caller rolled it back): on the artifact and the report, never a name
        error — a detection fault never makes the call's ingest stale."""
        msg = " ".join(str(exc).split())[:200]
        self.result.errors.append(
            f"{getattr(sec, 'ticker', None) or getattr(sec, 'id', '?')}: {type(exc).__name__}: {msg}"
        )
        _log.warning("split detection failed for %s: %s", getattr(sec, "ticker", None), msg)


def _memo_dir(client: Any) -> Path | None:
    """The extraction memo (``backfill_splits.read_filing``) sits beside the EDGAR cache, in the same volume."""
    cache = getattr(client, "cache_dir", None)
    return Path(cache).parent / "split_extract" if cache is not None else None


def _count(conn: psycopg.Connection, tenant_id: UUID, security_id: UUID) -> int:
    """``count(*)`` of the TABLE for one company (every version) — the append is verified against it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM security_split WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        return int(cur.fetchone()["n"])


# --- the CLI: the same check over the basket union, outside the nightly ----------------------------------


def run_over_union(
    conn: psycopg.Connection,
    detector: SplitDetector,
    edgar: EdgarClient,
    *,
    tickers: Iterable[str] | None = None,
) -> list[Member]:
    """Run ``detector.check`` over every member of the active basket union (``tickers`` narrows it), each
    company in its own transaction (committed only when the detector applies)."""
    members = union_members(conn, tickers=tickers)
    for m in members:
        sec = _MasterRow(id=m.security_id, ticker=m.ticker, price_symbol=m.price_symbol, cik=m.cik)
        try:
            detector.check(conn, edgar, sec, tenant_id=m.tenant_id)
            if detector.apply:
                conn.commit()
            else:
                conn.rollback()
        except Exception as e:  # noqa: BLE001 — one company's fault never stops the run
            conn.rollback()
            detector.fault(sec, e)
    return members


@dataclass(frozen=True)
class _MasterRow:
    id: UUID
    ticker: str | None
    price_symbol: str | None
    cik: str | None


def print_plan(detector: SplitDetector, *, out: Any = None) -> None:
    w = (out or sys.stdout).write
    by: dict[str, int] = defaultdict(int)
    for p in detector.planned:
        by[p.action] += 1
        if p.action == UNCHANGED:
            continue
        d = p.decision
        via = f" via {d.confirmed_via}" if d.confirmed_via else ""
        w(
            f"  {p.member.ticker or p.member.security_id}  {ratio_label(d.ratio_from, d.ratio_to)}  first session "
            f"{d.effective_date.isoformat()}  {d.status.upper()}{via}  -> {p.action.upper()}"
            + (f" ({p.reason})" if p.reason else "")
            + "\n"
        )
        if d.sec is not None:
            w(f"      SEC {d.sec.form} {d.sec.accession} filed {d.sec.filed.isoformat()}\n")
        if d.polygon is not None:
            w(
                f"      Polygon {d.polygon.ticker} {d.polygon.split_from} -> {d.polygon.split_to} on "
                f"{d.polygon.execution_date.isoformat()}\n"
            )
        w(f"      note: {' '.join(d.note.split())[:500]}\n")
    for u in detector.result.unplaced:
        w(f"  UNPLACED {u.ticker} {u.form} {u.accession} filed {u.filed.isoformat()}: {u.reason}\n")
    w(
        "PLAN: "
        + " · ".join(f"{k} {v}" for k, v in sorted(by.items()))
        + f" · unplaced {len(detector.result.unplaced)}\n"
    )
    for line in detector.result.report_lines():
        w(line + "\n")


def main(argv: list[str] | None = None) -> None:
    from db.session import connect
    from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock

    p = argparse.ArgumentParser(
        description="Split plan S4: the nightly split detection, run over the basket union by hand. "
        "Dry-run (read-only) by default."
    )
    p.add_argument(
        "--asof", type=date.fromisoformat, default=None, help="default: today (market time)"
    )
    p.add_argument("--sec-days", type=int, default=SEC_LOOKBACK_DAYS)
    p.add_argument("--polygon-days", type=int, default=POLYGON_LOOKBACK_DAYS)
    p.add_argument("--ticker", action="append", default=None, help="only this member (repeatable)")
    p.add_argument("--apply", action="store_true", help="append (default: dry run, read-only)")
    p.add_argument("--no-live", action="store_true", help="caches only (no SEC / Polygon request)")
    args = p.parse_args(argv)
    asof = args.asof or market_today()
    if asof > market_today():
        print(f"REFUSED: --asof {asof.isoformat()} is in the future (nothing was written)")
        raise SystemExit(EXIT_REFUSED)
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    print(
        f"detect_splits · {mode} · asof {asof.isoformat()} · SEC {args.sec_days}d · Polygon {args.polygon_days}d"
    )
    try:
        lock = acquire_pass_lock(holder="detect_splits") if args.apply else None
    except PassAlreadyRunning as exc:
        print(
            f"detect_splits: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    try:
        detector = SplitDetector.start(
            asof=asof,
            allow_live=not args.no_live,
            polygon_days=args.polygon_days,
            sec_days=args.sec_days,
            apply=args.apply,
        )
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            edgar = EdgarClient(allow_live=not args.no_live, max_per_sec=4.0)
            run_over_union(conn, detector, edgar, tickers=args.ticker)
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()
    print_plan(detector)


if __name__ == "__main__":
    main()
