"""MISSING CLOSES — "did every company get tonight's closing price?" (operator decision, 2026-10-23).

The Scoreboard's edition pages are written the same night, right after the nightly pass, so a company whose
close for the session had not arrived by then is paged without it. In the operator's words: "an admin flag
should fire if no closing price was found for a company on a day".

WHAT PROD LOOKS LIKE (MEASURED read-only on prod, sessions 2026-09-21..10-22, the 679 tracked basket
companies): 14–26 companies (~3%) have no bar for the session at the end of that night's pass; nearly all
arrive with the NEXT nightly pull (median ~24 h later, ~72 h over a weekend — the nightly is the only price
pull); almost none never arrive; and it is the SAME companies — 22 are late on 11+ of ~20 nights (mostly OTC,
a known vendor issue: ``docs/FEED_LOOP.md`` §Known feed limitations), 6 on 3–10 nights, 649 never. A flag
that shouted every night would be noise (#7, WB#3), so THE LOUDNESS RULE is part of the contract:

- **QUIET, every night:** one line — "21 of 679 companies had no close for Oct 23 at the night's run" — that
  expands to the tickers, each with its usual-late marker (late on most of its recent nights) and, for a
  company the master lists on the OTC venue, "OTC · known issue".
- **LOUD, only for a close STILL missing after the next session's pass:** "2 companies still have no close for
  Oct 21: X, Y". It persists until the bar arrives (then it is recorded QUIETLY as "late, arrived" with how
  late) or the tape goes stale (then the stale-tape flag, ``pipeline/tape_health.py``, takes over).
- **Pages: none.** It rides the run result, the run artifact and the Admin page, never the notifier.

WHAT THIS MODULE IS, AND IS NOT (the stale-tape monitor's discipline):

- **A MONITOR, not a signal.** It imports nothing from ``calls/`` or ``signals/`` and neither imports it. It
  cannot fire, arm, veto or grade anything (#3/#4), and it is never on the CallCard (a day-varying card field
  would flap ``record_if_changed``).
- **The judgment is PURE** (``judge``): no I/O, no clock — the session, the pass's start instant and every
  stored-bar fact are parameters. The FACTS come from the pass's own per-name results (``NameResult.tape_edge``
  read after the price leg, the leg's errors, the onboarding marks, tonight's stale inventory) plus one
  read-only DB read (``read_facts``: the benchmark bar for the session, the carried pairs' stored bars, the
  active basket union, the members' venue). ``run_missing_closes`` is the fail-open nightly unit.

THE RULES:

1. **Was the session a session?** D (the pass's as-of) is a session when SPY or IWM has a stored bar for D.
   If neither does — a market holiday, or a pass that ran before the session settled (D2 withholds an
   unsettled bar) — NOTHING is judged for D. A vendor-wide outage is the benchmark alarm's job (G4), not this.
2. **The missing set:** every live basket company (a member some thesis walked tonight, with a price tape)
   whose tape edge after its price leg is BEFORE D, with the reason when known: ``fetch_error`` (the price leg
   failed — its message kept), ``no_bar`` (the leg ran clean; the vendor simply had no close for D yet), or
   ``not_ingested`` (another writer held the company, so this pass did not run its legs). Deduped across
   theses (a name in N theses is one company).
3. **Not counted:** a company in tonight's stale / closed / renamed / unpriceable price inventory belongs to
   that flag (no double flag); a company still ONBOARDING (X1) is not judged; a member with no bar at all and
   no stale row (the stale monitor disabled) has no tape to judge. The counts ride the artifact (``excluded``).
4. **Recovery:** the pass re-checks the previous evaluated pass's open entries against the STORED bars (the
   exact (company, session) bar — a later bar arriving does not close an older hole). Arrived → "late,
   arrived" with ``lag_h`` (hours from the pass that first found it missing to the bar's first
   ``recorded_at`` — our own bookkeeping of when the feed delivered, never a history gate, #1). Absent → still
   missing; LOUD once a session pass for a LATER session has re-checked it, and loud thereafter. Resolved
   quietly (``resolved``) when the tape went stale (``why: stale`` — the stale flag has it) or the company is
   no longer in any active basket (``why: left_basket``).
5. **The usual-late marker:** late at the night's run on MORE THAN HALF of its last ``USUAL_LATE_WINDOW``
   judged sessions (tonight included), once at least ``USUAL_LATE_MIN_NIGHTS`` sessions are on record — read
   from the carried artifacts. MEASURED on prod's 20 sessions this marks exactly the 22 chronic names (11+ of
   ~20) and none of the 6 occasional ones (3–10).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg

from domain.feed_kinds import PRICE, feed_kind

# The carried history the usual-late marker reads: the newest N judged sessions, tonight's included.
# 20 matches the MEASURED window (2026-09-21..10-22, ~20 sessions) the chronic/occasional split came from.
USUAL_LATE_WINDOW = 20
# ...and the floor before anyone is marked: on a fresh history every name missing tonight would otherwise
# read "late on 1 of 1 nights" — usually late off one night.
USUAL_LATE_MIN_NIGHTS = 5

REASON_NO_BAR = "no_bar"
REASON_FETCH_ERROR = "fetch_error"
REASON_NOT_INGESTED = "not_ingested"

RESOLVED_STALE = "stale"
RESOLVED_LEFT = "left_basket"

# The master's per-instrument venue for the over-the-counter market (``securities/sec_tickers.py``: the SEC's
# exchange field reads "OTC"); the vendor's own codes (PNK = OTC Pink, OQB = OTCQB, OQX = OTCQX) are accepted
# too should one ever reach the master. Display only — the known-issue marker, never a rule input.
_OTC_VENUES = frozenset({"OTC", "PNK", "OQB", "OQX"})

_ERROR_CLIP = 200


def is_otc(exchange: str | None) -> bool:
    """Does the master list this company on the OTC venue? (``None`` / blank = unknown = not OTC.)"""
    if not exchange or not exchange.strip():
        return False
    v = exchange.strip().upper()
    return v in _OTC_VENUES or v.startswith("OTC")


def fmt_day(d: date) -> str:
    """``Oct 23`` — the operator's wording for a session (the editions line's form; no zero pad)."""
    return f"{d:%b} {d.day}"


def label_of(ticker: str | None, security_id: UUID | str) -> str:
    """The ticker, else the security id — a ticker-less company is shown by id, never dropped (#9)."""
    return ticker or str(security_id)


@dataclass(frozen=True)
class MissingClose:
    """One company with no stored close for ``session``.

    ``first_seen_at`` is the start of the pass that first found it missing (the lag's origin); ``checks`` how
    many later passes re-checked it and still found no bar (0 = found tonight); ``loud`` whether a pass for a
    LATER session has re-checked it (the loud rule — once loud, loud until resolved). ``edge`` is the newest
    stored bar at this pass. ``late_nights`` / ``judged_nights`` / ``usual_late`` are the usual-late marker;
    ``otc`` marks a company the master lists on the OTC venue (the known issue)."""

    security_id: UUID
    ticker: str | None
    session: date
    edge: date | None
    reason: str
    first_seen_at: datetime
    error: str | None = None
    checks: int = 0
    loud: bool = False
    late_nights: int = 0
    judged_nights: int = 0
    usual_late: bool = False
    otc: bool = False

    @property
    def label(self) -> str:
        return label_of(self.ticker, self.security_id)

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "session": self.session.isoformat(),
            "edge": self.edge.isoformat() if self.edge else None,
            "reason": self.reason,
            "error": self.error,
            "first_seen_at": self.first_seen_at.isoformat(),
            "checks": self.checks,
            "loud": self.loud,
            "late_nights": self.late_nights,
            "judged_nights": self.judged_nights,
            "usual_late": self.usual_late,
            "otc": self.otc,
        }


@dataclass(frozen=True)
class ArrivedClose:
    """A previously missing close whose bar is now stored — "late, arrived". ``arrived_at`` is the bar's FIRST
    ``recorded_at`` (when our store first held it); ``lag_h`` the hours from ``first_seen_at`` to it.
    """

    security_id: UUID
    ticker: str | None
    session: date
    first_seen_at: datetime
    arrived_at: datetime
    lag_h: float
    checks: int = 0
    otc: bool = False

    @property
    def label(self) -> str:
        return label_of(self.ticker, self.security_id)

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "session": self.session.isoformat(),
            "first_seen_at": self.first_seen_at.isoformat(),
            "arrived_at": self.arrived_at.isoformat(),
            "lag_h": self.lag_h,
            "checks": self.checks,
            "otc": self.otc,
        }


@dataclass(frozen=True)
class ResolvedClose:
    """A missing close that left this flag without arriving: ``why`` = ``stale`` (the tape went stale — the
    stale-tape flag has the company now) or ``left_basket`` (no active basket holds it any more)."""

    security_id: UUID
    ticker: str | None
    session: date
    first_seen_at: datetime
    why: str

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "session": self.session.isoformat(),
            "first_seen_at": self.first_seen_at.isoformat(),
            "why": self.why,
        }


@dataclass(frozen=True)
class Candidate:
    """One company the pass walked tonight, as this check sees it (``candidates_of``)."""

    security_id: UUID
    ticker: str | None
    edge: date | None
    reason: str
    error: str | None = None


@dataclass(frozen=True)
class Facts:
    """The DB facts the judgment reads (``read_facts``). ``benchmarks``: the benchmark tickers with a stored
    bar for the session; ``stored``: ``(security_id, session) -> first recorded_at`` for each carried pair whose
    bar is now stored; ``edges``: the newest stored bar per carried company; ``members``: the active basket
    union (``None`` = unread — nothing is dropped as left); ``exchanges``: the master venue per listed company.
    """

    benchmarks: tuple[str, ...] = ()
    stored: Mapping[tuple[UUID, date], datetime] = field(default_factory=dict)
    edges: Mapping[UUID, date | None] = field(default_factory=dict)
    members: Collection[UUID] | None = None
    exchanges: Mapping[UUID, str | None] = field(default_factory=dict)


@dataclass
class MissingCloses:
    """One pass's missing-close check — the run result's block and the artifact's ``missing_closes``.

    ``evaluated`` = the check ran (a live pass, no fault); ``is_session`` = the session had a benchmark close
    (else nothing was judged for it); ``judged`` = the companies judged for the session; ``missing`` = the
    session's missing closes (quiet); ``still_missing`` = OLDER sessions' closes still absent (``loud`` per
    row); ``arrived`` = closes that arrived since the previous check (quiet, with the lag); ``resolved`` =
    entries handed to the stale flag or dropped with the basket; ``excluded`` = the companies not judged and
    why (stale / onboarding / no_tape)."""

    evaluated: bool = False
    error: str | None = None
    session: date | None = None
    is_session: bool = False
    benchmarks: tuple[str, ...] = ()
    judged: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    missing: tuple[MissingClose, ...] = ()
    still_missing: tuple[MissingClose, ...] = ()
    arrived: tuple[ArrivedClose, ...] = ()
    resolved: tuple[ResolvedClose, ...] = ()
    duration_s: float | None = None

    @property
    def loud(self) -> tuple[MissingClose, ...]:
        return tuple(m for m in self.still_missing if m.loud)

    def payload(self) -> dict:
        """The artifact block (``cron_run_log.build_run_payload``). Readers use ``.get`` throughout: an
        artifact written before this key existed carries none and reads as "not checked"."""
        return {
            "missing_closes": {
                "evaluated": self.evaluated,
                "error": self.error,
                "session": self.session.isoformat() if self.session else None,
                "is_session": self.is_session,
                "benchmarks": list(self.benchmarks),
                "judged": self.judged,
                "excluded": dict(self.excluded),
                "missing": [m.to_json() for m in self.missing],
                "still_missing": [m.to_json() for m in self.still_missing],
                "arrived": [a.to_json() for a in self.arrived],
                "resolved": [r.to_json() for r in self.resolved],
                "usual_late_window": USUAL_LATE_WINDOW,
                "duration_s": self.duration_s,
            }
        }


# --- the facts off the pass's own results (pure) -----------------------------------------------------------


def _price_error(x: Any) -> str | None:
    for leg, msg in getattr(x, "leg_errors", ()) or ():
        if leg == "price":
            return " ".join(str(msg).split())[:_ERROR_CLIP]
    return None


def candidates_of(
    results: Iterable[Any],
) -> tuple[dict[UUID, Candidate], dict[str, int], set[UUID]]:
    """Tonight's judged companies off the per-thesis results: ``(candidates, excluded counts, stale ids)``.

    Deduped across theses by security — the NEWEST walk wins the edge (bars only append, so a later walk's
    edge is the company's edge after every price leg tonight) and the reason. Excluded, in this order: a
    company in tonight's PRICE stale inventory (any classification — closed, renamed, unpriceable,
    acknowledged: that flag owns it), a company still ONBOARDING (X1 — windowed or deferred), and a member
    with no stored bar at all (no tape to judge). ``stale ids`` are returned for the carried entries' hand-off.
    """
    walked: dict[UUID, Any] = {}
    onboarding: set[UUID] = set()
    stale: set[UUID] = set()
    for r in results:
        for s in getattr(r, "tape_stale", ()) or ():
            if feed_kind(getattr(s, "kind", None)).key == PRICE.key:
                stale.add(s.security_id)
        for x in getattr(r, "ingested", ()) or ():
            sid = getattr(x, "security_id", None)
            if sid is None:
                continue
            if getattr(x, "onboarding", None) or getattr(x, "onboarding_deferred", False):
                onboarding.add(sid)
            prev = walked.get(sid)
            # the newest walk wins; an edge never moves backwards (a later walk only appends)
            if prev is None or (getattr(x, "tape_edge", None) or date.min) >= (
                getattr(prev, "tape_edge", None) or date.min
            ):
                walked[sid] = x
    excluded = {"stale": 0, "onboarding": 0, "no_tape": 0}
    out: dict[UUID, Candidate] = {}
    for sid, x in walked.items():
        if sid in stale:
            excluded["stale"] += 1
            continue
        if sid in onboarding:
            excluded["onboarding"] += 1
            continue
        edge = getattr(x, "tape_edge", None)
        if edge is None:
            excluded["no_tape"] += 1
            continue
        err = _price_error(x)
        if getattr(x, "lock_busy", None):
            reason = REASON_NOT_INGESTED
        elif err is not None:
            reason = REASON_FETCH_ERROR
        else:
            reason = REASON_NO_BAR
        out[sid] = Candidate(sid, getattr(x, "ticker", None), edge, reason, err)
    return out, excluded, stale


# --- the carried artifacts (pure) ------------------------------------------------------------------------


def evaluated_blocks(payloads: Sequence[Mapping]) -> list[Mapping]:
    """The ``missing_closes`` blocks of every FINISHED, LIVE pass that evaluated the check, newest-first
    (``payloads`` are ``cron_run_log.list_run_logs``, newest-first). A ``--no-live`` pass, a killed pass's
    start artifact and an artifact written before the check existed are skipped. Fail-open per artifact.
    """
    from pipeline.cron_run_log import is_finished  # lazy: the artifact module imports this one

    out: list[Mapping] = []
    for doc in payloads:
        if not isinstance(doc, Mapping) or doc.get("mode") != "live" or not is_finished(doc):
            continue
        block = doc.get("missing_closes")
        if isinstance(block, Mapping) and block.get("evaluated"):
            out.append(block)
    return out


def _sid(v: object) -> UUID | None:
    try:
        return UUID(str(v))
    except (TypeError, ValueError):
        return None


def _day(v: object) -> date | None:
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def _instant(v: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None else None


def open_entries(block: Mapping | None) -> list[MissingClose]:
    """The previous evaluated pass's OPEN entries — its ``missing`` plus its ``still_missing`` — as rows to
    re-check. A malformed row is skipped (fail-open); one with no usable first-seen instant is skipped too
    (its lag could not be measured honestly, and the next pass re-finds it if it is still missing).
    """
    if not block:
        return []
    out: list[MissingClose] = []
    for key in ("missing", "still_missing"):
        for m in block.get(key) or ():
            if not isinstance(m, Mapping):
                continue
            sid, session, seen = (
                _sid(m.get("security_id")),
                _day(m.get("session")),
                _instant(m.get("first_seen_at")),
            )
            if sid is None or session is None or seen is None:
                continue
            out.append(
                MissingClose(
                    security_id=sid,
                    ticker=m.get("ticker") if isinstance(m.get("ticker"), str) else None,
                    session=session,
                    edge=_day(m.get("edge")),
                    reason=str(m.get("reason") or REASON_NO_BAR),
                    first_seen_at=seen,
                    error=m.get("error") if isinstance(m.get("error"), str) else None,
                    checks=int(m.get("checks") or 0) if isinstance(m.get("checks"), int) else 0,
                    loud=bool(m.get("loud")),
                )
            )
    return out


def late_history(
    blocks: Sequence[Mapping], *, session: date, tonight: Collection[UUID] | None
) -> tuple[dict[UUID, int], int]:
    """``({security_id: late nights}, judged nights)`` over the newest ``USUAL_LATE_WINDOW`` judged sessions up
    to and including ``session`` — tonight's missing set (``tonight``) for ``session`` itself (``None`` = tonight
    judged nothing: a holiday is not a judged night), and for each earlier session the newest evaluated session
    pass's ``missing`` set. A session judged twice (a re-run) counts once. The denominator is per SESSION, not per company (an artifact records the flagged names, not
    every member), so a company added recently reads as late on fewer of its nights, never more."""
    sessions: dict[date, set[UUID]] = {session: set(tonight)} if tonight is not None else {}
    for b in blocks:  # newest-first: the first block seen for a session wins
        if not b.get("is_session"):
            continue
        d = _day(b.get("session"))
        if d is None or d > session or d in sessions or (tonight is not None and d == session):
            continue
        sessions[d] = {
            s
            for m in (b.get("missing") or ())
            if isinstance(m, Mapping) and (s := _sid(m.get("security_id"))) is not None
        }
    window = sorted(sessions, reverse=True)[:USUAL_LATE_WINDOW]
    late: dict[UUID, int] = {}
    for d in window:
        for sid in sessions[d]:
            late[sid] = late.get(sid, 0) + 1
    return late, len(window)


def is_usual_late(late_nights: int, judged_nights: int) -> bool:
    """Late on MORE THAN HALF of the judged nights, once there are enough of them to say so."""
    return judged_nights >= USUAL_LATE_MIN_NIGHTS and late_nights * 2 > judged_nights


def _lag_h(first_seen_at: datetime, arrived_at: datetime) -> float:
    return round((arrived_at - first_seen_at).total_seconds() / 3600.0, 1)


# --- the judgment (pure) ----------------------------------------------------------------------------------


def judge(
    *,
    session: date,
    candidates: Mapping[UUID, Candidate],
    excluded: Mapping[str, int],
    stale_ids: Collection[UUID],
    previous: Sequence[MissingClose],
    history: Sequence[Mapping],
    facts: Facts,
    now: datetime,
) -> MissingCloses:
    """THE RULE (module docstring), pure over its inputs. ``session`` = the pass's as-of; ``candidates`` /
    ``excluded`` / ``stale_ids`` = ``candidates_of`` tonight; ``previous`` = the previous evaluated pass's open
    entries (``open_entries``); ``history`` = the evaluated blocks newest-first (``evaluated_blocks``);
    ``facts`` = the DB read; ``now`` = this pass's start instant (a new entry's ``first_seen_at``).
    """
    is_session = bool(facts.benchmarks)
    exch = facts.exchanges

    # (1) re-check every carried entry against the STORED bar for its own session
    arrived: list[ArrivedClose] = []
    resolved: list[ResolvedClose] = []
    carried_same: dict[UUID, MissingClose] = {}  # entries for tonight's session, still absent
    carried_older: list[MissingClose] = []
    seen_pairs: set[tuple[UUID, date]] = set()
    for e in previous:
        pair = (e.security_id, e.session)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        got = facts.stored.get(pair)
        if got is not None:
            arrived.append(
                ArrivedClose(
                    security_id=e.security_id,
                    ticker=e.ticker,
                    session=e.session,
                    first_seen_at=e.first_seen_at,
                    arrived_at=got,
                    lag_h=_lag_h(e.first_seen_at, got),
                    checks=e.checks + 1,
                    otc=is_otc(exch.get(e.security_id)),
                )
            )
            continue
        if e.security_id in stale_ids:
            resolved.append(
                ResolvedClose(e.security_id, e.ticker, e.session, e.first_seen_at, RESOLVED_STALE)
            )
            continue
        if facts.members is not None and e.security_id not in facts.members:
            resolved.append(
                ResolvedClose(e.security_id, e.ticker, e.session, e.first_seen_at, RESOLVED_LEFT)
            )
            continue
        if e.session == session:
            carried_same[e.security_id] = e
            continue
        # still absent for an OLDER session: loud once a session pass for a later session re-checked it
        carried_older.append(
            MissingClose(
                security_id=e.security_id,
                ticker=e.ticker,
                session=e.session,
                edge=facts.edges.get(e.security_id, e.edge),
                reason=e.reason,
                first_seen_at=e.first_seen_at,
                error=e.error,
                checks=e.checks + 1,
                loud=e.loud or (is_session and session > e.session),
            )
        )

    # (2) tonight's missing set — judged only on a session
    judged = 0
    tonight: dict[UUID, MissingClose] = {}
    if is_session:
        judged = len(candidates)
        for sid, c in candidates.items():
            if c.edge is not None and c.edge >= session:
                continue
            earlier = carried_same.get(sid)
            tonight[sid] = MissingClose(
                security_id=sid,
                ticker=c.ticker,
                session=session,
                edge=c.edge,
                reason=c.reason,
                first_seen_at=earlier.first_seen_at if earlier else now,
                error=c.error,
                checks=(earlier.checks + 1) if earlier else 0,
            )
    # a same-session carried entry tonight's walk did not judge (a thesis a catch-up skipped) stays listed
    for sid, e in carried_same.items():
        if sid not in tonight and sid not in candidates:
            tonight[sid] = MissingClose(
                security_id=sid,
                ticker=e.ticker,
                session=e.session,
                edge=facts.edges.get(sid, e.edge),
                reason=e.reason,
                first_seen_at=e.first_seen_at,
                error=e.error,
                checks=e.checks + 1,
            )

    # (3) the usual-late marker + the OTC venue on every open row
    late, nights = late_history(
        history, session=session, tonight=tonight.keys() if is_session else None
    )

    def mark(m: MissingClose) -> MissingClose:
        n = late.get(m.security_id, 0)
        return replace(
            m,
            late_nights=n,
            judged_nights=nights,
            usual_late=is_usual_late(n, nights),
            otc=is_otc(exch.get(m.security_id)),
        )

    order = lambda m: (m.session, (m.ticker or "").upper(), str(m.security_id))  # noqa: E731
    return MissingCloses(
        evaluated=True,
        session=session,
        is_session=is_session,
        benchmarks=tuple(facts.benchmarks),
        judged=judged,
        excluded=dict(excluded),
        missing=tuple(sorted((mark(m) for m in tonight.values()), key=order)),
        still_missing=tuple(sorted((mark(m) for m in carried_older), key=order)),
        arrived=tuple(sorted(arrived, key=lambda a: (a.session, (a.ticker or "").upper()))),
        resolved=tuple(resolved),
    )


# --- the DB facts (one read-only connection) ---------------------------------------------------------------

# The ACTIVE basket union — every resolved member of every non-archived thesis: the same set the nightly walks
# (``thesis_repo.list_all`` skips archived theses; ``ingest_thesis`` skips unresolved members) and the split
# monitor scans (``pipeline/split_basis._UNION``).
_UNION_SQL = """
    SELECT DISTINCT bm.security_id
      FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id
     WHERE t.archived_at IS NULL AND bm.security_id IS NOT NULL
"""

_BENCH_SQL = """
    SELECT DISTINCT sm.ticker
      FROM fact_price_eod p JOIN security_master sm ON sm.id = p.security_id
     WHERE p.security_id = ANY(%(ids)s) AND p.d = %(d)s
"""

# the exact (company, session) bars, each with its FIRST recorded_at (when our store first held it)
_STORED_SQL = """
    SELECT p.security_id, p.d, min(p.recorded_at) AS first_at
      FROM fact_price_eod p
      JOIN unnest(%(ids)s::uuid[], %(ds)s::date[]) AS q(sid, d)
        ON q.sid = p.security_id AND q.d = p.d
     GROUP BY p.security_id, p.d
"""

_EDGES_SQL = """
    SELECT security_id, max(d) AS d FROM fact_price_eod
     WHERE security_id = ANY(%(ids)s) GROUP BY security_id
"""

_EXCHANGE_SQL = "SELECT id, exchange FROM security_master WHERE id = ANY(%(ids)s)"


def read_facts(
    conn: psycopg.Connection,
    *,
    session: date,
    tenant_id: UUID,
    carried: Sequence[MissingClose],
    listed: Collection[UUID],
) -> Facts:
    """The check's DB facts — SELECTs only (transaction control is the caller's). ``carried`` = the previous
    pass's open entries (their exact bars, current edges and membership are read); ``listed`` = every company
    the artifact may name tonight (its venue is read for the OTC marker)."""
    from securities.benchmarks import benchmark_ids  # lazy: keep the monitor's import graph narrow

    bench = benchmark_ids(conn, tenant_id=tenant_id)
    with conn.cursor() as cur:
        benchmarks: tuple[str, ...] = ()
        if bench:
            cur.execute(_BENCH_SQL, {"ids": list(bench.values()), "d": session})
            benchmarks = tuple(sorted(str(r["ticker"]) for r in cur.fetchall()))
        stored: dict[tuple[UUID, date], datetime] = {}
        edges: dict[UUID, date | None] = {}
        members: set[UUID] | None = None
        if carried:
            cur.execute(
                _STORED_SQL,
                {
                    "ids": [e.security_id for e in carried],
                    "ds": [e.session for e in carried],
                },
            )
            stored = {(r["security_id"], r["d"]): r["first_at"] for r in cur.fetchall()}
            cur.execute(_EDGES_SQL, {"ids": sorted({e.security_id for e in carried})})
            edges = {r["security_id"]: r["d"] for r in cur.fetchall()}
            cur.execute(_UNION_SQL)
            members = {r["security_id"] for r in cur.fetchall()}
        ids = sorted(set(listed) | {e.security_id for e in carried})
        exchanges: dict[UUID, str | None] = {}
        if ids:
            cur.execute(_EXCHANGE_SQL, {"ids": ids})
            exchanges = {r["id"]: r["exchange"] for r in cur.fetchall()}
    return Facts(
        benchmarks=benchmarks, stored=stored, edges=edges, members=members, exchanges=exchanges
    )


def _fault(e: BaseException) -> str:
    msg = " ".join(str(e).split())[:_ERROR_CLIP]
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def run_missing_closes(
    *,
    results: Sequence[Any],
    asof: date,
    allow_live: bool,
    started_at: datetime,
    tenant_id: UUID,
    connect: Callable[[], psycopg.Connection],
    payloads: Callable[[], Sequence[Mapping]],
    clock: Callable[[], float],
) -> MissingCloses:
    """The nightly unit: read the carried artifacts (``payloads`` — called BEFORE this pass's artifact is
    finalized, so it is never its own baseline), read the DB facts on its own ``read_only`` connection, judge.
    FAIL-OPEN for the pass, VISIBLE for the operator: any fault returns ``evaluated=False`` with the ``error``
    (the Admin page and the cron log say the check could not run) — never an exception into the cron. A
    ``--no-live`` pass does not evaluate (its edges are cache reads, not tonight's feed) and is never a
    baseline."""
    if not allow_live:
        return MissingCloses()
    t0 = clock()
    try:
        candidates, excluded, stale_ids = candidates_of(results)
        blocks = evaluated_blocks(payloads())
        previous = open_entries(blocks[0] if blocks else None)
        listed = [sid for sid, c in candidates.items() if c.edge is None or c.edge < asof]
        conn = connect()
        try:
            conn.read_only = True  # the monitor reads; structurally it cannot write a row
            facts = read_facts(
                conn, session=asof, tenant_id=tenant_id, carried=previous, listed=listed
            )
            conn.rollback()  # end the read-only transaction; nothing to keep
        finally:
            conn.close()
        out = judge(
            session=asof,
            candidates=candidates,
            excluded=excluded,
            stale_ids=stale_ids,
            previous=previous,
            history=blocks,
            facts=facts,
            now=started_at,
        )
    except Exception as e:  # noqa: BLE001 — a monitor fault is reported, never a failed pass
        return MissingCloses(session=asof, error=_fault(e), duration_s=round(clock() - t0, 3))
    out.duration_s = round(clock() - t0, 3)
    return out


# --- the words (the cron log + the Admin line) -------------------------------------------------------------


def _companies(n: int) -> str:
    return f"{n} company" if n == 1 else f"{n} companies"


def quiet_line(mc: MissingCloses | Mapping) -> str:
    """The QUIET nightly line, from a ``MissingCloses`` or its artifact block — e.g. "Closing prices · 21 of
    679 companies had no close for Oct 23 at the night's run (17 OTC, a known issue) · usually arrive with the
    next night's pull". ONE builder for the Admin page and the cron log."""
    block = mc.payload()["missing_closes"] if isinstance(mc, MissingCloses) else mc
    session = _day(block.get("session"))
    day = fmt_day(session) if session else "the session"
    if not block.get("is_session"):
        return (
            f"Closing prices · not judged for {day} — neither SPY nor IWM has a close for it (a market "
            "holiday, or the session had not settled at the run)"
        )
    missing = [m for m in (block.get("missing") or ()) if isinstance(m, Mapping)]
    judged = int(block.get("judged") or 0)
    if not missing:
        return f"Closing prices · all {judged} companies had a close for {day} at the night's run"
    otc = sum(1 for m in missing if m.get("otc"))
    otc_part = f" ({otc} OTC, a known issue)" if otc else ""
    return (
        f"Closing prices · {len(missing)} of {judged} companies had no close for {day} at the night's "
        f"run{otc_part} · usually arrive with the next night's pull"
    )


def loud_lines(mc: MissingCloses | Mapping) -> list[str]:
    """The LOUD lines — one per older session whose close is still missing after a later session's pass:
    "2 companies still have no close for Oct 21: X, Y". ``[]`` on the normal night."""
    block = mc.payload()["missing_closes"] if isinstance(mc, MissingCloses) else mc
    by_session: dict[date, list[str]] = {}
    for m in block.get("still_missing") or ():
        if not isinstance(m, Mapping) or not m.get("loud"):
            continue
        d = _day(m.get("session"))
        if d is None:
            continue
        by_session.setdefault(d, []).append(
            label_of(
                m.get("ticker") if isinstance(m.get("ticker"), str) else None, m.get("security_id")
            )
        )
    out = []
    for d in sorted(by_session):
        names = by_session[d]
        verb = "has" if len(names) == 1 else "have"
        out.append(
            f"{_companies(len(names))} still {verb} no close for {fmt_day(d)}: {', '.join(names)}"
        )
    return out


def report_lines(mc: MissingCloses) -> list[str]:
    """The cron's printed block (``pipeline.daily.main``): the loud still-missing lines UPPERCASE-headed, the
    nightly line and the arrivals lowercase (quiet), a failed check loud, nothing on a ``--no-live`` pass.
    """
    if mc.error:
        return [f"MISSING-CLOSE CHECK FAILED — {mc.error} (tonight's closes were not checked)"]
    if not mc.evaluated:
        return []
    out = [f"MISSING CLOSES — {line}" for line in loud_lines(mc)]
    q = quiet_line(mc)
    out.append(q[0].lower() + q[1:])
    if mc.arrived:
        lags = [a.lag_h for a in mc.arrived]
        out.append(
            f"late closes arrived: {len(mc.arrived)} (median {statistics.median(lags):.1f} h after the "
            "pass that found them missing)"
        )
    return out
