"""``pipeline.detect_splits`` — split plan S4: find NEW splits every night and write them to the split list.

S1 (``pipeline/backfill_splits.py``) filled the list once from Massive.com's split reference, each split checked
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
2. **Massive.com's split reference** — ONE date-filtered request a night for the whole market
   (``execution_date >= asof - POLYGON_LOOKBACK_DAYS``; announced future splits included), on the free-tier
   budget the fund-shares leg shares (``ingest/polygon_splits.py``). With no ``MASSIVE_API_KEY`` this source is
   OFF and the report says so in one line; the SEC and tape sources still run.
3. **The price tape, as a CONFIRMING source only** (``domain.splits.with_tape``): a split ONE documentary source
   states (Massive.com's row with no SEC filing disagreeing, or an SEC filing stating the first trading session)
   is confirmed when the stored tape steps by about its ratio exactly on that session, the volume moving the
   other way — read on each bar as the vendor FIRST served it. The tolerance is MEASURED (``TAPE_*``). The tape
   never confirms a split alone, never arbitrates two sources that disagree, never confirms a forward split.

THE DECISIONS, per company (``detect_security``): each Massive.com row is decided EXACTLY as S1 decides it
(``backfill_splits.decide_polygon_group`` — the targeted wide-form reader included: a row whose window holds no
agreeing filing gets one look at the company's periodic reports / ADR registrations / prospectuses /
information statements, each read once ever); an SEC filing read in the window that no Massive.com row explains is
an SEC-only candidate (``domain.splits.decide_sec_only``); then the tape. Each filing is read as S1 reads it
(``backfill_splits.screen_of``: a 6-K or a charter 8-K through the restatement screen, which only removes — a
6-K restating an old split never becomes a new candidate at its old date), knowing the company's splits already
CONFIRMED on the list (``backfill_splits.known_for``: a ratio equal to one, stated as done, is that past split's
whatever its age). A filing that mentions a split the list cannot place (no ratio, no date, an extractor abstain)
is listed on the run artifact (``unplaced``) with its link, passage and any ratio, never dropped silently (#9).
The CLI's ``--record`` keeps a long-window run's list the same way (``pipeline/split_unplaced.py``, the one
reader of both). F8-2 (decision D-F8b, dark: ``split_sec_restate_days``, 0 = off): a later filing restating a
candidate's ratio within that many days joins it, the latest filing's date supersedes, and such a candidate is never
put to the tape. F3b (dark: ``split_legal_date_next_session``, False = off): an SEC-only candidate's legal date that
is no session is planned on the next session, and the tape is read there.

THE PLAN against the stored list (``plan_against``): a split is found by S1's ``match_stored``. A new one is
appended. A stored one is appended to ONLY when the system wrote its newest version, it is still OPEN
(``needs_review`` / ``announced``, or set aside by the system as a distribution — D2, ``split_repo.
is_auto_set_aside``: a confirming filing that arrives later still confirms it, and one that mentions a split sends it
back to review), tonight's evidence covers every source it already carries (a source missing tonight — Massive.com
off, a filing unread — never downgrades it), and the content changed: a second source arrived, the tape confirmed
it, an announced split's first session came (announced -> confirmed), or D2 set it aside. A confirmed split, one
the operator rejected or revoked, and anything the operator decided (a restore included), is never touched; when
tonight's evidence differs from such a split it is listed on the artifact (``kept``), not written. A later decision
tonight about a split an earlier one claimed is no news when it states the same ratio and first session; one stating
others is another filing's, never dropped silently: listed on ``kept`` and, on a split new tonight, named on its
note. It decides nothing.

THE CALL-OFFS (F10): every filing a company's check read (any window, placed or not) whose text calls a split off
(``SecEvidence.calloff``, the split reader's) is listed on the run artifact (``calloffs``, ``CallOffFiling``) and on
a ``--record``; the one reader (``pipeline/split_unplaced.py``) serves them for the warning, which matches them
against the list when it is read (``domain.split_calloffs``, under ``calloff_rule``: the ``split_calloff_*``
settings). After the per-company checks the nightly matches tonight's rows the same way (``check_called_off``):
each listed split a filing calls off is one report line, and a new one pages once. Nothing here decides on them.

WHERE IT RUNS (``pipeline/ingest_thesis._ingest_one``): per company, right after its 8-K and 13D legs and
BEFORE its price leg — so a split filed today, with a second source, is on the list before that company's
price leg (S2 applies it on its first night) and before tonight's call. A company held in several theses is
checked once a pass. The Massive.com request is made once, before the per-thesis loop (``SplitDetector.start``).
Fail-open: a fault is rolled back, recorded on the run artifact (``split_detect``) and printed; it never costs a
name its ingest or the night its record.

    python -m pipeline.detect_splits                       # dry run over the basket union (read-only)
    python -m pipeline.detect_splits --apply               # append (holds the call-of-record lock)
    python -m pipeline.detect_splits --sec-days 90 --polygon-days 90 --ticker BTOC --no-live
    python -m pipeline.detect_splits --sec-days 800 --polygon-days 800 --record   # keep its unplaced list
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

import psycopg

from domain.market_time import market_today, market_tz
from domain.settings import get_settings
from domain.split_calloffs import CalloffRule, called_off
from domain.splits import (
    AUTO_VIA_LABELS,
    PAST_RATIO_ONLY,
    SET_ASIDE,
    SOURCE_POLYGON,
    SOURCE_SEC,
    STATUS_ANNOUNCED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    PolygonEvidence,
    SecEvidence,
    SplitDecision,
    TapeEvidence,
    cluster_sec,
    decide,
    decide_sec_only,
    ratio_label,
    related,
    restated,
    same_ratio,
    with_tape,
)
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.polygon_splits import PolygonSplitsError, PolygonSplitSource, default_split_source
from pipeline.backfill_splits import (
    ConfirmedSplit,
    Member,
    SecReader,
    _conflicting,
    _group,
    confirmed_of,
    confirmed_splits,
    decide_polygon_group,
    distribution_cap,
    distribution_label_overrides_abstain,
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

# The ONE Massive.com request a night: every split with an execution date on/after asof - this (Massive.com lists
# announced future splits too). 30 days covers a split whose first session was this month and whose second
# source (a late 8-K, the tape on the next night) arrives later — MEASURED: 16 of the list's 137 SEC filings
# came after the split, the latest 7 days after (KULR).
POLYGON_LOOKBACK_DAYS = 30
# SEC-only candidates: split filings FILED in the last this many days are re-read every night, so a candidate
# keeps being re-decided until its second source (Massive.com, or the tape the night after its first session)
# arrives. MEASURED: 103 of the list's 137 SEC filings came at least a day before the split; the earliest
# confirming one 2026 ran ~6 weeks ahead. Each filing is fetched once ever (immutable cache).
SEC_LOOKBACK_DAYS = 60
# a stored bar this many calendar days before a first session is the farthest "prior bar" the tape reads
TAPE_PRIOR_MAX_DAYS = 14

APPEND = "append"
UNCHANGED = "unchanged"
KEPT = "kept"
# why a later decision tonight about a split an earlier one claimed is KEPT: the same values (no news, never on the
# artifact's ``kept``), or other values (another filing's: on ``kept``, and on a new split's note)
ALREADY_PLANNED = "already planned tonight"
ANOTHER_FILING = "another filing tonight states other values"

EXIT_REFUSED = 2
# an unplaced filing's passage on the run artifact (and a check's record) is cut to this many characters
UNPLACED_PASSAGE_MAX = 600


# --- the tape ----------------------------------------------------------------------------------------------


def tape_at(
    conn: psycopg.Connection, *, tenant_id: UUID, security_id: UUID, session: date
) -> TapeEvidence | None:
    """The stored tape at ``session``: that bar and the stored bar before it (within ``TAPE_PRIOR_MAX_DAYS``),
    each its FIRST-SERVED version (the earliest recorded — the vendor's as-traded print, before any later
    re-basing). A version Massive.com FILLED (``price_bar_fill``, W6-3b) is never the vendor's print and never
    split evidence (decision W6-c): it is skipped, so a date only a fill holds reads as no bar. ``None`` when
    either bar is missing or carries no positive close. A READ, never a write.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (p.d) p.d, p.close, p.volume, p.recorded_at FROM fact_price_eod p "
            "WHERE p.tenant_id = %s AND p.security_id = %s AND p.d BETWEEN %s AND %s "
            "AND NOT EXISTS (SELECT 1 FROM price_bar_fill f WHERE f.tenant_id = p.tenant_id "
            "AND f.security_id = p.security_id AND f.d = p.d AND f.bar_recorded_at = p.recorded_at) "
            "ORDER BY p.d, p.recorded_at, p.id",
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


@dataclass(frozen=True)
class TapeBar:
    """One stored session of a tape window: the bar as the vendor FIRST served it (``close`` / ``volume``: the
    version ``tape_at`` reads, the as-traded print) and its NEWEST stored close (the store's scale today).
    ``filled`` (W6-3b): the vendor never served this session — the bar shown is Massive.com's fill
    (``price_bar_fill``), which ``tape_at`` never reads as evidence.
    """

    d: date
    close: float | None
    volume: float | None
    newest_close: float | None
    filled: bool = False

    @property
    def restated(self) -> bool:
        """The newest close differs from the first-served one (re-based later: by the vendor, or by the split
        list's own rescale of the stored tape)."""
        return self.newest_close != self.close


def tape_window(
    conn: psycopg.Connection, *, tenant_id: UUID, security_id: UUID, around: date, sessions: int
) -> list[TapeBar]:
    """The stored tape around ``around`` (platform fixes W5-1): up to ``sessions`` stored dates before it and up to
    ``sessions`` from it on (``around`` itself first, when stored), oldest first — fewer where the tape ends. Each
    date's FIRST-SERVED version (the ``tape_at`` pick: a Massive.com fill skipped) beside its newest; a date only
    a fill holds shows the fill, marked ``filled``. A READ, never a write.
    """
    with conn.cursor() as cur:
        cur.execute(
            "WITH days AS ("
            "(SELECT DISTINCT d FROM fact_price_eod WHERE tenant_id = %(t)s AND security_id = %(s)s "
            "AND d < %(a)s ORDER BY d DESC LIMIT %(n)s) UNION ALL "
            "(SELECT DISTINCT d FROM fact_price_eod WHERE tenant_id = %(t)s AND security_id = %(s)s "
            "AND d >= %(a)s ORDER BY d LIMIT %(n)s)), "
            "versions AS (SELECT p.d, p.close, p.volume, (b.id IS NOT NULL) AS filled, "
            "row_number() OVER (PARTITION BY p.d ORDER BY (b.id IS NOT NULL), p.recorded_at, p.id) "
            "AS first_rank, "
            "row_number() OVER (PARTITION BY p.d ORDER BY p.recorded_at DESC, p.id DESC) AS newest_rank "
            "FROM fact_price_eod p JOIN days ON days.d = p.d "
            "LEFT JOIN price_bar_fill b ON b.tenant_id = p.tenant_id AND b.security_id = p.security_id "
            "AND b.d = p.d AND b.bar_recorded_at = p.recorded_at "
            "WHERE p.tenant_id = %(t)s AND p.security_id = %(s)s) "
            "SELECT f.d, f.close, f.volume, f.filled, n.close AS newest_close FROM versions f "
            "JOIN versions n ON n.d = f.d AND n.newest_rank = 1 WHERE f.first_rank = 1 ORDER BY f.d",
            {"t": tenant_id, "s": security_id, "a": around, "n": sessions},
        )
        rows = cur.fetchall()

    def _f(v: Any) -> float | None:
        return None if v is None else float(v)

    return [
        TapeBar(
            d=r["d"],
            close=_f(r["close"]),
            volume=_f(r["volume"]),
            newest_close=_f(r["newest_close"]),
            filled=bool(r["filled"]),
        )
        for r in rows
    ]


# --- one company -------------------------------------------------------------------------------------------


def _decimal_text(v: Decimal | None) -> str | None:
    return None if v is None else format(Decimal(v).normalize(), "f")


@dataclass(frozen=True)
class Unplaced:
    """An SEC filing that talks about a split the list could not place (no ratio, no date, an abstain), with
    what the extractor read from it (``SecEvidence``): the filing index link, the document and passage it
    located, the ratio when one is stated, and why it abstained or what the date said."""

    security_id: UUID
    ticker: str | None
    accession: str
    form: str
    filed: date
    reason: str
    url: str | None = None
    document: str | None = None
    passage: str | None = None
    ratio_from: Decimal | None = None
    ratio_to: Decimal | None = None
    abstain_reason: str | None = None
    date_note: str | None = None

    def to_json(self) -> dict:
        """The artifact row (a reader uses ``.get``: a row written before the evidence keys reads null)."""
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "accession": self.accession,
            "form": self.form,
            "filed": self.filed.isoformat(),
            "reason": self.reason,
            "url": self.url,
            "document": self.document,
            "passage": (self.passage or "")[:UNPLACED_PASSAGE_MAX] or None,
            "ratio_from": _decimal_text(self.ratio_from),
            "ratio_to": _decimal_text(self.ratio_to),
            "abstain_reason": self.abstain_reason,
            "date_note": self.date_note,
        }


@dataclass
class SecurityDetection:
    """What tonight's evidence says about ONE company: its decisions (Massive.com-keyed first, then SEC-only),
    the filings it could not place, and the reads."""

    member: Member
    decisions: list[SplitDecision] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)
    filings_read: int = 0
    filings_unread: int = 0


# an abstain that is no news: a filing restating a PAST split (LGHL's interim results) or with no ratio at all
_NOT_NEWS = (PAST_RATIO_ONLY,)


def sec_restate_days() -> int:
    """F8-2 (decision D-F8b, dark): the days within which a later SEC filing restating a split's ratio joins its
    SEC-only candidate (``domain.splits.cluster_sec``; the setting ``split_sec_restate_days``, 0 = off). Read once a
    pass beside ``distribution_cap`` and passed down: the pure rule reads no settings."""
    return get_settings().split_sec_restate_days


def legal_date_next_session() -> bool:
    """F3b (dark): does an SEC-only candidate's LEGAL date that is no session give way to the next session
    (``domain.splits.decide_sec_only``; the setting ``split_legal_date_next_session``, False = off)? Read once a
    pass beside ``sec_restate_days`` and passed down: the pure rule reads no settings."""
    return get_settings().split_legal_date_next_session


def calloff_rule() -> CalloffRule:
    """F10: THE one builder of the call-off rule (``domain.split_calloffs.CalloffRule``): which filings call off a
    listed split, from the settings ``split_calloff_forms``, ``split_calloff_after_days`` and
    ``split_calloff_ratio``. Each caller reads it once (``GET /admin/splits`` once a read; the nightly once a pass,
    in ``check_called_off``) and passes it down: the one matcher reads no settings."""
    s = get_settings()
    return CalloffRule(
        forms=tuple(s.split_calloff_forms.split(",")),
        after_days=s.split_calloff_after_days,
        ratio=s.split_calloff_ratio,
    )


def calloff_warn() -> bool:
    """F10: is the call-off warning on (the setting ``split_calloff_warn``; True = on, the shipped default)? Read
    beside ``calloff_rule``."""
    return get_settings().split_calloff_warn


def detect_security(
    conn: psycopg.Connection,
    reader: SecReader,
    m: Member,
    polygon_rows: Iterable[PolygonEvidence],
    *,
    asof: date,
    sec_since: date,
    distribution_max_factor: Decimal | None,
    label_overrides_abstain: bool,
    confirmed: tuple[ConfirmedSplit, ...] | None = None,
    restate_days: int = 0,
    next_session: bool = False,
) -> SecurityDetection:
    """Every split tonight's evidence states for ONE company (the module docstring). Reads only.
    ``distribution_max_factor``: D2's bound (``backfill_splits.distribution_cap``); ``label_overrides_abstain``:
    W6-2's label rule (``backfill_splits.distribution_label_overrides_abstain``). ``confirmed``: the company's
    splits confirmed on the list (``backfill_splits.confirmed_of``; read here when not given) — every filing is
    read knowing them. ``restate_days``: F8-2's rule (``sec_restate_days``; 0 = off): a candidate its filings
    restate with dates more than two sessions apart is never put to the tape. ``next_session``: F3b's rule
    (``legal_date_next_session``; False = off): a legal date that is no session is planned on the next session.
    """
    out = SecurityDetection(member=m)
    if confirmed is None:
        confirmed = confirmed_splits(conn, m)
    absorbed: set[str] = set()
    for group in _group(list(polygon_rows)):
        g = decide_polygon_group(
            conn,
            reader,
            m,
            group,
            asof=asof,
            distribution_max_factor=distribution_max_factor,
            label_overrides_abstain=label_overrides_abstain,
            confirmed=confirmed,
        )
        d = g.decision
        absorbed.update(s.accession for s in g.read if related(s, group[0]))
        # the tape settles a Massive.com-only row only when nothing else could disagree: no conflicting Massive.com
        # rows, every SEC filing in its window read, and the company's index readable (the targeted reader's
        # wide forms are a confirmation search: one that agrees has already settled the row, one unread is no
        # disagreement)
        settled_alone = (
            not (len(group) > 1 and _conflicting(group))
            and g.unread == 0
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
    # the SEC side on its own: the split filings filed in the lookback that no Massive.com row explains
    refs = reader.refs(conn, m, sec_since, asof)
    read, unread = reader.read(m, refs, confirmed=confirmed)
    # each filing counted once, whichever window read it
    seen = reader.read_here
    out.filings_read = sum(1 for a in seen if reader.evidence[a] is not None)
    out.filings_unread = sum(1 for a in seen if reader.evidence[a] is None)
    fresh = [s for s in read if s.accession not in absorbed]
    for g in cluster_sec(fresh, restate_days=restate_days):
        d = decide_sec_only(g, asof=asof, restate_days=restate_days, next_session=next_session)
        # F8-2: the tape never picks between the company's own dates (``restated``: never true at 0)
        if d.effective_date <= asof and not restated(g, restate_days=restate_days):
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
        out.unplaced.append(
            Unplaced(
                m.security_id,
                m.ticker,
                s.accession,
                s.form,
                s.filed,
                why,
                url=s.url,
                document=s.document,
                passage=s.passage,
                ratio_from=s.ratio_from,
                ratio_to=s.ratio_to,
                abstain_reason=s.abstain_reason,
                date_note=s.date_note,
            )
        )
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
    """The evidence a decision rests on, in the operator's words ("8-K + Massive.com", "6-K + tape")."""
    via = d.confirmed_via or ""
    if via.endswith("+tape"):
        first = d.sec.form if via.startswith("auto:sec") and d.sec else "Massive.com"
        return f"{first} + tape"
    parts = []
    if d.sec is not None and d.sec.has_ratio:
        parts.append(d.sec.form)
    if d.polygon is not None:
        parts.append("Massive.com")
    return " + ".join(parts) or "no source"


def _stored_polygon(v: SplitVersion) -> PolygonEvidence | None:
    """The Massive.com row a stored version carries, as evidence (``None`` when it carries none)."""
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
        adjustment_type=v.polygon_adjustment_type,  # W6-1: the label it was stored with (NULL before 0216)
    )


# What makes a new version of an open split worth appending: its status / values / pairing, or its EVIDENCE
# (which filing, which Massive.com row and what each states) — and a tape reading it did not carry before. A note
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


def _same_values(d: SplitDecision, v: SplitVersion | SplitDecision) -> bool:
    """Does tonight's decision state what the stored split (or an earlier decision tonight) holds: the ratio
    exactly, the first session?"""
    return d.effective_date == v.effective_date and same_ratio(
        d.ratio_from, d.ratio_to, v.ratio_from, v.ratio_to
    )


def plan_against(
    detection: SecurityDetection,
    stored: Iterable[SplitVersion],
    *,
    asof: date,
    distribution_max_factor: Decimal | None,
    label_overrides_abstain: bool,
) -> list[Planned]:
    """Each decision against the stored list (the module docstring's rule). Pure: no I/O.

    One refinement of the rule: a filing that arrives AFTER its split's Massive.com row left tonight's pull (a late
    8-K, filed weeks after the split — FDCT's came 47 days later, outside S1's window) is decided against the
    Massive.com row the stored split already carries, by S1's own ``decide`` (with D2's ``distribution_max_factor``
    and W6-2's ``label_overrides_abstain``) — so the two sources still meet.
    """
    vs = list(stored)
    # each split a decision tonight claimed -> where that decision's plan is in ``out``
    claimed: dict[date, int] = {}
    out: list[Planned] = []
    for d in detection.decisions:
        v = match_stored(d, vs)
        if v is None:
            out.append(Planned(detection.member, d, None, APPEND, "new"))
            # a later decision about the same (new) split matches this one, never lists it twice
            vs.append(_as_version(detection.member, d))
            claimed[d.key_date] = len(out) - 1
            continue
        if d.polygon is None and d.sec is not None and d.sec.has_ratio:
            poly = _stored_polygon(v)
            if poly is not None:
                d = decide(
                    poly,
                    [d.sec],
                    asof=asof,
                    distribution_max_factor=distribution_max_factor,
                    label_overrides_abstain=label_overrides_abstain,
                )
        d = replace(d, key_date=v.key_date)
        if v.key_date in claimed:
            # an earlier decision tonight claimed this split: the same values are no news; other values are
            # another filing's, never dropped silently. It decides nothing: the split keeps the first decision's
            # values, and a listed one is never re-versioned for it (a note alone is not ``_material``).
            i = claimed[v.key_date]
            first = out[i]
            if _same_values(d, first.decision):
                out.append(Planned(detection.member, d, v, KEPT, ALREADY_PLANNED))
                continue
            if first.action == APPEND and first.stored is None:
                note = f"{first.decision.note} {_another_says(d, first.decision)}"
                out[i] = replace(first, decision=replace(first.decision, note=note))
            out.append(Planned(detection.member, d, v, KEPT, ANOTHER_FILING))
            continue
        claimed[v.key_date] = len(out)
        if content_of(decision_values(d)) == v.content():
            out.append(Planned(detection.member, d, v, UNCHANGED))
            continue
        why = None
        # open: waiting for review or its first session — or set aside by the system (D2), which a later filing
        # can still confirm or send back to review
        still_open = v.status in (
            STATUS_NEEDS_REVIEW,
            STATUS_ANNOUNCED,
        ) or split_repo.is_auto_set_aside(v)
        if v.author == AUTHOR_OPERATOR:
            why = "the operator's decision stands"
        elif not still_open:
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


def _another_says(d: SplitDecision, planned: SplitDecision) -> str:
    """What a split new tonight gains on its note for another decision tonight stating other values: that filing
    (or Massive.com row) and what it states, in the notes' words. Evidence text: no call reads a split's note.
    """
    what = (
        ""
        if same_ratio(d.ratio_from, d.ratio_to, planned.ratio_from, planned.ratio_to)
        else f"a {ratio_label(d.ratio_from, d.ratio_to)}, "
    )
    s = d.sec
    if s is None or d.value_source == SOURCE_POLYGON:
        return f"Another Massive.com row states {what}first session {d.effective_date.isoformat()}."
    when = (
        "the effective date"
        if d.value_source == SOURCE_SEC and s.date_kind != "trading"
        else "first session"
    )
    return (
        f"Another SEC filing ({s.form} {s.accession}, filed {s.filed.isoformat()}) states {what}{when} "
        f"{d.effective_date.isoformat()}."
    )


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
        what = {
            STATUS_NEEDS_REVIEW: "needs review",
            STATUS_REJECTED: "set aside as a distribution",  # D2: the only status a system writes as rejected
        }.get(self.status, self.status)
        again = "; updated" if self.verb == "updated" else ""
        return f"{who} {self.label} {what} ({self.sources}{again})"


@dataclass(frozen=True)
class CallOffFiling:
    """F10: a filing read tonight whose text calls a split off (``SecEvidence.calloff``), placed or not: one row of
    the run artifact's ``calloffs`` (and of a check's record). Evidence only: the warning matches it against the list
    when the list is read (``pipeline.split_unplaced``, the one reader; ``domain.split_calloffs``, the one matcher).
    ``kind``, the ratio, ``document`` and ``passage`` are the call-off's own (``domain.splits.CallOff``: the one
    ratio its call-off sentences state, else none), never the filing's reading."""

    security_id: UUID
    ticker: str | None
    accession: str
    form: str
    filed: date
    kind: str
    url: str | None = None
    ratio_from: Decimal | None = None
    ratio_to: Decimal | None = None
    document: str | None = None
    passage: str | None = None

    @classmethod
    def of(cls, m: Member, s: SecEvidence) -> CallOffFiling:
        """The row of filing ``s``, read for company ``m``; ``s`` carries a call-off."""
        c = s.calloff
        if c is None:
            raise ValueError(f"{s.accession} carries no call-off")
        return cls(
            security_id=m.security_id,
            ticker=m.ticker,
            accession=s.accession,
            form=s.form,
            filed=s.filed,
            kind=c.kind,
            url=s.url or None,
            ratio_from=c.ratio_from,
            ratio_to=c.ratio_to,
            document=c.document,
            passage=c.passage or None,
        )

    def to_json(self) -> dict:
        """The artifact row (``pipeline.split_unplaced.CALLOFF_KEYS``; the passage cut like an unplaced one's)."""
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "accession": self.accession,
            "form": self.form,
            "filed": self.filed.isoformat(),
            "url": self.url,
            "kind": self.kind,
            "ratio_from": _decimal_text(self.ratio_from),
            "ratio_to": _decimal_text(self.ratio_to),
            "document": self.document,
            "passage": (self.passage or "")[:UNPLACED_PASSAGE_MAX] or None,
        }


def calloffs_read(m: Member, reader: SecReader) -> list[CallOffFiling]:
    """The call-off rows of one company's check: each filing ``reader`` read for it (``read_here``: the Massive.com
    rows' windows, the wide forms and the SEC lookback alike) whose reading carries a call-off, placed, absorbed or
    unplaced; each accession once (a second reading, knowing a confirmed split, is the same filing), in accession
    order. A read, nothing else."""
    out: dict[str, CallOffFiling] = {}
    for key in sorted(reader.read_here):
        s = reader.evidence.get(key)
        if s is not None and s.calloff is not None and s.accession not in out:
            out[s.accession] = CallOffFiling.of(m, s)
    return list(out.values())


@dataclass(frozen=True)
class CalledOffSplit:
    """F10: a listed split (the list's newest version, live or open) that a filing the nightly read calls off, by
    the one matcher (``called_off_splits``): one row of the run artifact's ``split_detect.called_off``, one report
    line, and the page while it is new. ``ratio``, ``status`` and ``first_session`` are the LISTED split's; ``accession``,
    ``form`` and ``filed`` the filing's. Display only: nothing is decided on it."""

    security_id: UUID
    ticker: str | None
    key_date: date
    first_session: date
    ratio: str
    status: str
    accession: str
    form: str
    filed: date

    @property
    def key(self) -> str:
        """The page-once key (``cron_run_log.previous_called_off``): the listed split and the filing, never its
        status, so a later decision on the split (a revoke, a demote) is no new page."""
        return f"{self.security_id}:{self.key_date.isoformat()}:{self.accession}"

    @property
    def label(self) -> str:
        """THE words: the report line's, the page's and the Admin run row's (they cannot drift)."""
        who = self.ticker or str(self.security_id)
        return (
            f"{who} {self.ratio} ({self.status.replace('_', ' ')}, first session "
            f"{self.first_session.isoformat()}): called off by {self.form} {self.accession} "
            f"(filed {self.filed.isoformat()})"
        )

    def to_json(self) -> dict:
        """The artifact row (``called_off_keys_of`` / ``called_off_new_labels_of`` read it back)."""
        return {
            "key": self.key,
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "key_date": self.key_date.isoformat(),
            "first_session": self.first_session.isoformat(),
            "ratio": self.ratio,
            "status": self.status,
            "accession": self.accession,
            "form": self.form,
            "filed": self.filed.isoformat(),
            "label": self.label,
        }


def listed_calloffs(
    v: SplitVersion, filings: Sequence[SecEvidence], *, rule: CalloffRule
) -> list[SecEvidence]:
    """THE one matcher (``domain.split_calloffs.called_off``) against the listed split ``v`` (any status): its ratio
    and first session, announced on its SEC filing's date, else the day it was listed (a Massive.com-only split).
    The one mapping of a listed split onto the matcher: the nightly (``called_off_splits``) and ``GET
    /admin/splits`` (``app/routers/admin_splits.called_off_for``) both call it. PURE.
    """
    return called_off(
        filings,
        ratio_from=v.ratio_from,
        ratio_to=v.ratio_to,
        first_session=v.effective_date,
        announced_on=v.sec_filed or v.valid_from,
        rule=rule,
    )


def called_off_splits(
    calloffs: Iterable[CallOffFiling], versions: Iterable[SplitVersion], *, rule: CalloffRule
) -> list[CalledOffSplit]:
    """The listed splits still live or open among ``versions`` (confirmed, announced, needs review) that the
    call-off rows ``calloffs`` call off under ``rule``. A split the steward rejected or revoked
    (``domain.splits.SET_ASIDE``) is settled: never reported or paged (the panel still names it, any status). One
    row per (split, filing), each split matched against its own company's rows only, each row read back through the
    artifact row (``CallOffFiling.to_json``, ``split_unplaced.calloff_evidence``) exactly as ``GET /admin/splits``
    reads it, so the page and the panel agree. By ticker, then the split's key date; one split's filings in the
    matcher's order (its ratio stated first). PURE."""
    # lazy: split_unplaced reads the run artifacts
    from pipeline.split_unplaced import calloff_evidence

    by_security: dict[UUID, dict[str, CallOffFiling]] = defaultdict(dict)
    for c in calloffs:
        by_security[c.security_id].setdefault(c.accession, c)
    out: list[CalledOffSplit] = []
    for v in versions:
        own = by_security.get(v.security_id)
        if not own or v.status in SET_ASIDE:
            continue
        evidence = [calloff_evidence(c.to_json()) for c in own.values()]
        for s in listed_calloffs(v, evidence, rule=rule):
            c = own[s.accession]
            out.append(
                CalledOffSplit(
                    security_id=v.security_id,
                    ticker=v.ticker or c.ticker,
                    key_date=v.key_date,
                    first_session=v.effective_date,
                    ratio=ratio_label(v.ratio_from, v.ratio_to),
                    status=v.status,
                    accession=c.accession,
                    form=c.form,
                    filed=c.filed,
                )
            )
    # stable: a split's filings keep the matcher's order
    return sorted(out, key=lambda x: (x.ticker or "", str(x.security_id), x.key_date))


@dataclass
class SplitDetection:
    """One pass's detection outcome — what rides the run artifact (``payload``) and the report (one line)."""

    evaluated: bool = False
    polygon: str = "not run"
    polygon_rows: int = 0
    polygon_live_pages: int = 0
    # W6-1: rows Massive.com served with no known label (kept, label NULL; 0 on the v3 fallback)
    polygon_unlabeled: int = 0
    checked: int = 0
    filings_read: int = 0
    filings_unread: int = 0
    appended: list[Detected] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)
    # F10: the filings read with call-off wording, placed or not (``calloffs_read``)
    calloffs: list[CallOffFiling] = field(default_factory=list)
    kept: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    # F10: the nightly's call-off check (``check_called_off``): did it run (a live pass, the warning on), the listed
    # splits tonight's rows call off, those absent from the last checked pass (what pages, once), and why it failed
    called_off_evaluated: bool = False
    called_off: list[CalledOffSplit] = field(default_factory=list)
    called_off_new: list[CalledOffSplit] = field(default_factory=list)
    called_off_error: str | None = None

    def payload(self) -> dict:
        """The run artifact's ``split_detect`` block (readers use ``.get``: an older artifact lacks it)."""
        return {
            "split_detect": {
                "evaluated": self.evaluated,
                "polygon": self.polygon,
                "polygon_rows": self.polygon_rows,
                "polygon_live_pages": self.polygon_live_pages,
                "polygon_unlabeled": self.polygon_unlabeled,
                "checked": self.checked,
                "filings_read": self.filings_read,
                "filings_unread": self.filings_unread,
                "appended": [a.to_json() for a in self.appended],
                "unplaced": [u.to_json() for u in self.unplaced],
                **self.calloffs_payload(),
                **self.called_off_payload(),
                "kept": list(self.kept),
                "errors": list(self.errors),
                "duration_s": round(self.duration_s, 3),
            }
        }

    def calloffs_payload(self) -> dict:
        """F10: ``{"calloffs": [rows]}``, beside ``unplaced`` on the run artifact and on a check's record, or
        ``{}`` when no filing read carried a call-off: the block is then byte for byte what it was before (a reader
        uses ``.get``: absent reads as none)."""
        return {"calloffs": [c.to_json() for c in self.calloffs]} if self.calloffs else {}

    def called_off_payload(self) -> dict:
        """F10: the call-off check's keys, beside ``calloffs``, or ``{}`` when it did not run (a ``--no-live`` pass,
        the warning off): the block is then what the detection wrote, and a reader (``.get``) reads it unchecked —
        never a baseline. A failed check writes its error with ``called_off_evaluated`` false."""
        if not self.called_off_evaluated and self.called_off_error is None:
            return {}
        return {
            "called_off_evaluated": self.called_off_evaluated,
            "called_off_error": self.called_off_error,
            "called_off": [c.to_json() for c in self.called_off],
            "called_off_new": [c.to_json() for c in self.called_off_new],
        }

    @property
    def called_off_new_labels(self) -> tuple[str, ...]:
        """What pages (``HealthEvent.split_called_off_new``): the new call-offs' labels."""
        return tuple(c.label for c in self.called_off_new)

    def called_off_lines(self) -> list[str]:
        """F10: the call-off check's report lines: each NEW one LOUD (an uppercase header, one line each), the
        known ones ONE quiet line, a failed check LOUD; nothing when none is called off or it did not run.
        """
        if self.called_off_error:
            return [
                f"SPLIT CALL-OFF CHECK FAILED — {self.called_off_error} (tonight's call-off filings were not "
                "matched to the list)"
            ]
        if not self.called_off_evaluated or not self.called_off:
            return []
        lines: list[str] = []
        if self.called_off_new:
            lines.append(
                "SPLITS CALLED OFF — NEW: a filing read tonight calls off a listed split (nothing was decided "
                "on it; Admin Splits panel):"
            )
            lines.extend(f"  {c.label}" for c in self.called_off_new)
        fresh = {c.key for c in self.called_off_new}
        known = [c for c in self.called_off if c.key not in fresh]
        if known:
            lines.append(
                f"{len(known)} listed split(s) still called off by a filing, already told (Admin Splits "
                f"panel): {'; '.join(c.label for c in known)}"
            )
        return lines

    def report_lines(self) -> list[str]:
        """ONE quiet line on a normal night (nothing at all when nothing ran and nothing is off): the new
        list rows, the review count, the unplaced count; a separate line when Massive.com is off or failed, and
        one per fault. Never a page (#7) — a needs-review split shows on the Admin "Splits" panel. F10: then the
        call-off check's lines (``called_off_lines``), the one part that pages, once, through the health page.
        """
        if not self.evaluated:
            return []
        lines: list[str] = []
        if self.polygon != "on":
            lines.append(
                f"splits: Massive.com {self.polygon} — SEC + tape only, nothing confirms on Massive.com"
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
        lines.extend(self.called_off_lines())
        return lines


class SplitDetector:
    """The pass's detector: the Massive.com pull (made once, ``start``), the per-company check the ingest calls
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
        # every security's former tickers, read ONCE a pass on the first check (``former_tickers``)
        self._former: dict[UUID, tuple[str, ...]] | None = None
        # D2's bound, read once a pass (``None`` = the auto set-aside is off)
        self.distribution_max_factor = distribution_cap()
        # W6-2's label rule, read beside it
        self.label_overrides_abstain = distribution_label_overrides_abstain()
        # F8-2's restate rule (days; 0 = off, the shipped default), read beside them
        self.sec_restate_days = sec_restate_days()
        # F3b's next-session rule (False = off, the shipped default), read beside it
        self.legal_date_next_session = legal_date_next_session()
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
        """The ONE Massive.com request of the pass (fail-open: an off / failed / uncached source is a status on
        the outcome, and the SEC + tape sources still run)."""
        t0 = perf_counter()
        polygon = source if source is not None else default_split_source()
        rows: list[PolygonEvidence] | None = None
        live_pages = unlabeled = 0
        if polygon is None:
            status = "off (no MASSIVE_API_KEY)"
        else:
            try:
                pull = polygon.splits_since(
                    asof - timedelta(days=polygon_days), allow_live=allow_live
                )
                rows, live_pages, status = pull.rows, pull.live_pages, "on"
                unlabeled = pull.unlabeled
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
        det.result.polygon_unlabeled = unlabeled
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
        if self._former is None:
            self._former = former_tickers(conn)
        m = Member(
            tenant_id=tenant_id,
            security_id=sec.id,
            ticker=sec.ticker,
            price_symbol=getattr(sec, "price_symbol", None),
            cik=sec.cik,
            former_tickers=self._former.get(sec.id, ()),
        )
        reader = SecReader(client, memo_dir=_memo_dir(client))
        reader.evidence = self._evidence  # each filing read once a pass, whichever company reads it
        try:
            # the stored list first: its confirmed splits are the ones each filing is read knowing (a read only)
            stored = split_repo.latest_versions(conn, tenant_id=tenant_id, security_ids=[sec.id])
            det = detect_security(
                conn,
                reader,
                m,
                self._rows_for(m),
                asof=self.asof,
                sec_since=self.sec_since,
                distribution_max_factor=self.distribution_max_factor,
                label_overrides_abstain=self.label_overrides_abstain,
                confirmed=confirmed_of(stored),
                restate_days=self.sec_restate_days,
                next_session=self.legal_date_next_session,
            )
            self.detections.append(det)
            self.result.checked += 1
            self.result.filings_read += det.filings_read
            self.result.filings_unread += det.filings_unread
            self.result.unplaced.extend(det.unplaced)
            self.result.calloffs.extend(calloffs_read(m, reader))
            plan = plan_against(
                det,
                stored,
                asof=self.asof,
                distribution_max_factor=self.distribution_max_factor,
                label_overrides_abstain=self.label_overrides_abstain,
            )
            self.planned.extend(plan)
            for p in plan:
                if p.action == KEPT and p.reason != ALREADY_PLANNED:
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


# --- F10: the nightly's call-off check, after the per-company checks ------------------------------------------


def check_called_off(
    result: SplitDetection,
    *,
    allow_live: bool,
    tenant_id: UUID,
    connect: Callable[[], psycopg.Connection],
    previous: Callable[[], set[str] | None],
) -> SplitDetection:
    """F10: tonight's call-off rows (``result.calloffs``) against the list's newest versions of their companies, by
    the one matcher under the rule the settings set (``called_off_splits``, ``calloff_rule``), and the matches whose
    key is absent from the last checked pass (``previous``: ``cron_run_log.previous_called_off``; ``None`` = none
    checked yet, so every match is new) — what pages, once. Sets the ``called_off*`` fields on ``result``, returns
    it.

    Nothing runs (no line, no page, nothing on the artifact) on a ``--no-live`` pass, on a detection that did not
    run, or with the warning off (``split_calloff_warn``). A night that read no call-off opens no connection.
    FAIL-OPEN for the pass, LOUD for the operator: the settings read, the read-only connection and the baseline
    read sit in ONE try, so any fault (a partial settings stub, the database) is ``called_off_error``, one loud
    report line, never an exception into the cron. Display only: nothing is decided on a match.
    """
    if not allow_live or not result.evaluated:
        return result
    try:
        if not calloff_warn():
            return result
        rule = calloff_rule()
        found: list[CalledOffSplit] = []
        if result.calloffs:
            conn = connect()
            try:
                conn.read_only = True  # the check reads; structurally it cannot write a row
                versions = split_repo.latest_versions(
                    conn,
                    tenant_id=tenant_id,
                    security_ids=sorted({c.security_id for c in result.calloffs}),
                )
                conn.rollback()  # end the read-only transaction; nothing to keep
            finally:
                conn.close()
            found = called_off_splits(result.calloffs, versions, rule=rule)
        prior = previous() if found else set()
    except Exception as e:  # noqa: BLE001 — a check fault is reported, never a failed pass
        msg = " ".join(str(e).split())[:200]
        result.called_off_error = f"{type(e).__name__}: {msg}" if msg else type(e).__name__
        return result
    result.called_off_evaluated = True
    result.called_off = found
    result.called_off_new = [c for c in found if prior is None or c.key not in prior]
    return result


def called_off_keys_of(doc: Mapping) -> set[str]:
    """The keys of every listed split one artifact's check found called off (``split_detect.called_off``): what the
    next pass diffs against. WHICH artifact is the baseline is ``cron_run_log.previous_called_off``'s rule.
    Fail-open: a malformed row is skipped."""
    block = doc.get("split_detect")
    if not isinstance(block, Mapping):
        return set()
    return {
        str(r["key"])
        for r in block.get("called_off") or []
        if isinstance(r, Mapping) and r.get("key")
    }


def called_off_new_labels_of(doc: Mapping) -> tuple[str, ...]:
    """The labels one artifact's check paged (``split_detect.called_off_new``): the Admin history re-derives the
    run's page from these. ``()`` for an artifact without them; a malformed row is skipped."""
    block = doc.get("split_detect")
    if not isinstance(block, Mapping):
        return ()
    return tuple(
        str(r["label"])
        for r in block.get("called_off_new") or []
        if isinstance(r, Mapping) and r.get("label")
    )


def former_tickers(conn: psycopg.Connection) -> dict[UUID, tuple[str, ...]]:
    """Every security's FORMER tickers (``security_ticker_history``, 0066) — ONE read a pass, so a split Massive.com
    lists under the ticker a company traded as then still meets it (``Member.symbols``). Read-only.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, array_agg(DISTINCT old_ticker ORDER BY old_ticker) AS former "
            "FROM security_ticker_history GROUP BY security_id"
        )
        return {r["security_id"]: tuple(r["former"]) for r in cur.fetchall()}


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
        confirmation = AUTO_VIA_LABELS.get(d.confirmed_via or "", d.confirmed_via)
        via = f" via {confirmation}" if confirmation else ""
        w(
            f"  {p.member.ticker or p.member.security_id}  {ratio_label(d.ratio_from, d.ratio_to)}  first session "
            f"{d.effective_date.isoformat()}  {d.status.upper()}{via}  -> {p.action.upper()}"
            + (f" ({p.reason})" if p.reason else "")
            + "\n"
        )
        if d.sec is not None:
            w(f"      SEC {d.sec.form} {d.sec.accession} filed {d.sec.filed.isoformat()}\n")
        if d.polygon is not None:
            label = f" ({d.polygon.adjustment_type})" if d.polygon.adjustment_type else ""
            w(
                f"      Massive.com {d.polygon.ticker} {d.polygon.split_from} -> {d.polygon.split_to} on "
                f"{d.polygon.execution_date.isoformat()}{label}\n"
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
    p.add_argument(
        "--no-live", action="store_true", help="caches only (no SEC / Massive.com request)"
    )
    p.add_argument(
        "--record",
        action="store_true",
        help="keep this run's unplaced filings in data/split_unplaced, read beside the nightly's (the whole "
        "basket union only, so not with --ticker)",
    )
    args = p.parse_args(argv)
    if args.record and args.ticker:
        print(
            "REFUSED: --record keeps the whole basket's unplaced list; a --ticker run would replace it with "
            "part of one (nothing was written)"
        )
        raise SystemExit(EXIT_REFUSED)
    asof = args.asof or market_today()
    if asof > market_today():
        print(f"REFUSED: --asof {asof.isoformat()} is in the future (nothing was written)")
        raise SystemExit(EXIT_REFUSED)
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    if args.record:
        mode = mode.replace("nothing written", "no database write")
        mode += " · RECORD (its unplaced filings go to data/split_unplaced)"
    print(
        f"detect_splits · {mode} · asof {asof.isoformat()} · SEC {args.sec_days}d · Massive.com {args.polygon_days}d"
    )
    try:
        lock = acquire_pass_lock(holder="detect_splits") if args.apply else None
    except PassAlreadyRunning as exc:
        print(
            f"detect_splits: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    started_at = datetime.now(timezone.utc)
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
    finished_at = datetime.now(timezone.utc)
    print_plan(detector)
    if args.record:
        from pipeline.split_unplaced import record_check

        path = record_check(
            detector.result,
            started_at=started_at,
            finished_at=finished_at,
            asof=asof,
            sec_days=args.sec_days,
            polygon_days=args.polygon_days,
        )
        print(f"recorded {len(detector.result.unplaced)} unplaced filing(s): {path}")


if __name__ == "__main__":
    main()
