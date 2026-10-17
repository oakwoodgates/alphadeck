"""Per-(thesis, night) COVERAGE and the ON-TIME window — one home for "does that night have its record?"
(M1 · M2 · V1).

``docs/temp/cron-correctness-plan-2026-10-16.md`` §2 criterion 1: every scheduled weekday has an on-time
call-of-record **per active thesis** (recorded after ``RUN_AT`` and before the next EDGAR open, 06:00 ET),
judged per (thesis, night), never per night. The per-night read this replaces asked "did ANY row for the
as-of land after the cutoff" (``calls_repo.recorded_asof_stamps``, one ``MAX(recorded_at)`` across every
thesis) — so MEASURED on 2026-10-12, a pass killed at ~22:42 with 4 of 12 theses recorded read as a fully
covered night, and ``/admin/status`` said healthy for ~18 h.

Three readers share this module, so each word has ONE definition:
- the V1 self-audit's ``on_time_rows`` check (``pipeline/self_audit.py``) — tonight's night, end of pass;
- the Admin hole check (``app/routers/admin.py``) — every scheduled night in the recent window, listing a
  night that is not fully covered as **partial** ("4 of 12 theses");
- the M2 catch-up resume (``pipeline/daily.run_daily``) — a ``--catch-up`` pass for night D skips every
  thesis that already has an ON-TIME row for D (``on_time_theses``).

THE WORDS:

- **The on-time window** (``on_time_window`` / ``is_on_time``, the ONE helper M2 and the late-row policy L1
  share): ``[RUN_AT on D, 06:00 market time on the next weekday after D)`` — from the scheduled fire to the
  next EDGAR open (EDGAR accepts filings from 06:00 ET; anything recorded later can have read a filing the
  night could not have). A Friday night's window runs to Monday 06:00 (the trigger's Mon-Fri calendar —
  holidays are not modeled, the ``pipeline/schedule.py`` choice).
- **Late** (``is_late`` — L1, operator Q5 = A) = a non-reconstructed row recorded at or after the window's
  END. The SCOREBOARD never lets one define an episode (``scoreboard/late.py``: dropped before the per-as-of
  dedup, so an on-time row for the night wins and a late-only night reads as a cron gap — reversible by
  ``Settings.scoreboard_exclude_late`` / ``?include_late=``), and a Slack push made late says so
  (``night_timing``, N1).
- **Covered** (the V1 audit; the base of the Admin check) = the thesis has a NON-reconstructed row for D
  recorded at or after D's ``RUN_AT`` — WITHOUT the upper bound: a late catch-up row still COVERS the night
  here (the pass did record it — ``describe`` names it "recorded late"); L1 decides only how the Scoreboard
  SCORES such a row, not whether the Admin hole check calls the night recorded. A ``reconstructed`` row
  (``pipeline.backfill``) never COVERS — it is reported, never the night's record; a row recorded BEFORE the
  cutoff (a pre-open "Run daily now") never covers — it ran on the prior session's bars.
- **Resolved** (the Admin partial check — operator decision 2026-10-16) = covered, OR the thesis's gap was
  filled by a ``reconstructed`` row. The backfill IS the operator's decision that resolves that hole, so a
  backfilled night reads QUIET (not partial, not an alarm — a quiet ``reconstructed`` count keeps it
  visible), and a NEW miss stands out. Only an ``unresolved`` thesis — no row after the cutoff and no
  reconstruction either — makes a night partial. The V1 audit keeps the stricter ``covered``: it judges the
  night its own pass just ran, where no backfill exists.
- **On time** (the M2 skip; reported beside covered) = a non-reconstructed row INSIDE the on-time window.
- **Expected** = the thesis was in the night's roster: CREATED before D's cutoff, and not ARCHIVED before it
  (``archived_at`` NULL, or at/after the cutoff). The creation rule is ``pipeline.backfill
  .thesis_existed_on``'s, tightened from the calendar day to the cutoff INSTANT, because the question here
  is whether the scheduled pass could have seen the thesis: one created at 23:00 was never in the 22:30
  pass's walk, and the calendar-day rule would list its night as partial for as long as it sat in the
  window. The archive rule is point-in-time on purpose: a thesis archived AFTER a night it had no row for
  still leaves that night partial — archiving must not hide a hole. KNOWN LIMIT: ``archived_at`` is one
  stamp, not a history, so a thesis archived and later RESTORED (stamp back to NULL) reads as expected on
  the nights it sat archived.
- **Counted in the table** (``calls_repo.thesis_night_stamps`` — every row, never the deduped as-of read
  and never a pass's own results): a pass that believes it recorded is exactly what this must not trust.

KNOWN SHAPE (inherited from the V1 check, reported, not changed here): ``record_if_changed`` appends no row
when the night's card equals one already logged for the same as-of. A thesis whose only row for the night is
a pre-cutoff daytime one, and whose post-close card came out identical, therefore reads NOT covered.

Pure judgment (``judge_coverage`` / ``on_time_theses``) + thin DB reads; read-only, no clock read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Literal
from uuid import UUID

import psycopg

from pipeline.schedule import is_scheduled_day
from repositories import calls_repo, thesis_repo

# EDGAR's filing day opens at 06:00 ET (it accepts 06:00-22:00) — the on-time window's upper bound.
EDGAR_OPEN = time(6, 0)


def night_cutoff(asof: date, run_at: time, tz: tzinfo) -> datetime:
    """That night's ``RUN_AT`` in market time — the on-time bar (the G2c covered-day cutoff)."""
    return datetime.combine(asof, run_at, tzinfo=tz)


def next_edgar_open(asof: date, tz: tzinfo) -> datetime:
    """06:00 market time on the first scheduled weekday AFTER ``asof`` — the on-time window's end."""
    d = asof + timedelta(days=1)
    while not is_scheduled_day(d):
        d += timedelta(days=1)
    return datetime.combine(d, EDGAR_OPEN, tzinfo=tz)


def on_time_window(asof: date, run_at: time, tz: tzinfo) -> tuple[datetime, datetime]:
    """``[start, end)`` — a row for ``asof`` is ON TIME when recorded in it: from that night's ``RUN_AT`` to
    the next weekday's EDGAR open. THE shared definition (M2's catch-up skip; L1's ``is_late``)."""
    return night_cutoff(asof, run_at, tz), next_edgar_open(asof, tz)


def is_on_time(recorded_at: datetime, asof: date, *, run_at: time, tz: tzinfo) -> bool:
    """Was a row for ``asof`` recorded inside its on-time window? (Reconstructed-ness is the caller's.)"""
    start, end = on_time_window(asof, run_at, tz)
    return start <= recorded_at < end


def is_late(recorded_at: datetime, asof: date, *, tz: tzinfo) -> bool:
    """L1 (operator Q5 = A) — was a row for ``asof`` recorded AT OR AFTER the end of its on-time window, the
    next weekday's EDGAR open (``on_time_window``'s upper bound, ``next_edgar_open``)? Such a row can have
    read a filing SEC accepted after the night (MEASURED 2026-10-12: three of the boot catch-up's rows cite
    Form 4s accepted on 10-13). The lower bound plays no part: a row recorded BEFORE the night's ``RUN_AT``
    is a daytime row, not a late one. Reconstructed-ness is the caller's (a ``pipeline.backfill`` row is
    never judged late — it is already set aside on its own marker)."""
    return recorded_at >= next_edgar_open(asof, tz)


NightTiming = Literal["current", "past", "late"]


def night_timing(asof: date, at: datetime, *, tz: tzinfo) -> NightTiming:
    """N1 — how a record made AT ``at`` for night ``asof`` relates to that night, for a push's label:

    - ``"late"``    — at/after the night's on-time window closed (``is_late``): a catch-up or ``--late-ok``
      pass recording the night after the next EDGAR open (the Scoreboard sets such a row aside, L1);
    - ``"past"``    — still on time, but ``asof`` is no longer today in market time (a pass that ran past
      midnight, a pre-open catch-up): worth naming the night the push is about;
    - ``"current"`` — tonight's night, the normal case (no label).

    PURE (``at`` and ``tz`` injected)."""
    if is_late(at, asof, tz=tz):
        return "late"
    if asof < at.astimezone(tz).date():
        return "past"
    return "current"


@dataclass(frozen=True)
class ThesisSpan:
    """When a thesis existed: ``created_at`` (aware) and ``archived_at`` (aware, ``None`` = live)."""

    thesis_id: UUID
    created_at: datetime
    archived_at: datetime | None = None

    def expected_at(self, cutoff: datetime) -> bool:
        """Was this thesis in the roster of the pass due at ``cutoff``? Created before it, and not archived
        before it (see the module docstring for why each side is what it is)."""
        if self.created_at >= cutoff:
            return False
        return self.archived_at is None or self.archived_at >= cutoff


@dataclass(frozen=True)
class CallStamp:
    """One ``calls`` row, reduced to what coverage reads — ``calls_repo.thesis_night_stamps``."""

    thesis_id: UUID
    asof: date
    reconstructed: bool
    recorded_at: datetime


@dataclass(frozen=True)
class NightCoverage:
    """One scheduled night, judged per thesis. ``expected`` = the theses the night's pass was due to
    record; ``covered`` ⊆ ``expected`` = those with a non-reconstructed row at/after the cutoff (late rows
    included); ``on_time`` ⊆ ``covered`` = those with one inside the on-time window. ``reconstructed_only``
    / ``daytime_only`` explain some of the ``missing`` ones (a backfilled thesis; a thesis whose only live
    row predates the cutoff). ``any_post_cutoff`` = SOME thesis (expected or not) has a non-reconstructed
    row at/after the cutoff — evidence a pass recorded the night at all."""

    asof: date
    expected: frozenset[UUID]
    covered: frozenset[UUID]
    on_time: frozenset[UUID] = frozenset()
    reconstructed_only: frozenset[UUID] = frozenset()
    daytime_only: frozenset[UUID] = frozenset()
    any_post_cutoff: bool = False

    @property
    def missing(self) -> frozenset[UUID]:
        return self.expected - self.covered

    @property
    def complete(self) -> bool:
        """Every expected thesis is covered. A night with NO expected thesis is vacuously complete — there
        was nothing to record (a night before every current thesis existed)."""
        return not self.missing

    @property
    def unresolved(self) -> frozenset[UUID]:
        """The missing theses a backfill has NOT resolved — no post-cutoff row and no reconstructed row. The
        Admin partial check's set (a reconstructed-only thesis is the operator's resolved hole)."""
        return self.missing - self.reconstructed_only

    @property
    def resolved(self) -> bool:
        """No unresolved thesis: every expected thesis is covered or was backfilled. The Admin verdict's
        "this night is fine" — quiet even when some of it is reconstructed."""
        return not self.unresolved

    def describe(self) -> str:
        """``"4 of 12 theses"`` plus what explains the gap, when anything does — counts only, never a thesis
        name (the line rides the Admin detail and may ride a page)."""
        out = f"{len(self.covered)} of {len(self.expected)} theses"
        bits = []
        if self.reconstructed_only:
            bits.append(f"{len(self.reconstructed_only)} reconstructed only")
        if self.daytime_only:
            bits.append(f"{len(self.daytime_only)} with a daytime row only")
        late = len(self.covered - self.on_time)
        if late:
            bits.append(f"{late} recorded late")
        return out + (f" ({', '.join(bits)})" if bits else "")


def judge_coverage(
    nights: Iterable[date],
    spans: Iterable[ThesisSpan],
    stamps: Iterable[CallStamp],
    *,
    run_at: time,
    tz: tzinfo,
) -> dict[date, NightCoverage]:
    """PURE: each night in ``nights`` judged per thesis (the module docstring's rules)."""
    spans = list(spans)
    post: dict[date, set[UUID]] = {}
    on_time: dict[date, set[UUID]] = {}
    pre: dict[date, set[UUID]] = {}
    recon: dict[date, set[UUID]] = {}
    for s in stamps:
        if s.reconstructed:
            recon.setdefault(s.asof, set()).add(s.thesis_id)
            continue
        start, end = on_time_window(s.asof, run_at, tz)
        if s.recorded_at >= start:
            post.setdefault(s.asof, set()).add(s.thesis_id)
            if s.recorded_at < end:
                on_time.setdefault(s.asof, set()).add(s.thesis_id)
        else:
            pre.setdefault(s.asof, set()).add(s.thesis_id)
    out: dict[date, NightCoverage] = {}
    for d in nights:
        cutoff = night_cutoff(d, run_at, tz)
        expected = frozenset(sp.thesis_id for sp in spans if sp.expected_at(cutoff))
        covered = expected & post.get(d, set())
        missing = expected - covered
        out[d] = NightCoverage(
            asof=d,
            expected=expected,
            covered=frozenset(covered),
            on_time=frozenset(expected & on_time.get(d, set())),
            reconstructed_only=frozenset(missing & recon.get(d, set())),
            daytime_only=frozenset((missing & pre.get(d, set())) - recon.get(d, set())),
            any_post_cutoff=bool(post.get(d)),
        )
    return out


def on_time_theses(
    stamps: Iterable[CallStamp], asof: date, *, run_at: time, tz: tzinfo
) -> dict[UUID, datetime]:
    """PURE (M2): ``thesis_id -> its EARLIEST on-time recorded_at`` for ``asof`` — every thesis (roster or
    not) holding a NON-reconstructed row for the night inside the on-time window. A thesis whose only rows
    are late, daytime, or reconstructed is absent: a catch-up still owes it the night."""
    out: dict[UUID, datetime] = {}
    for s in stamps:
        if s.asof != asof or s.reconstructed:
            continue
        if is_on_time(s.recorded_at, asof, run_at=run_at, tz=tz):
            prev = out.get(s.thesis_id)
            if prev is None or s.recorded_at < prev:
                out[s.thesis_id] = s.recorded_at
    return out


def _spans(rows: Iterable[Mapping]) -> list[ThesisSpan]:
    return [
        ThesisSpan(thesis_id=r["id"], created_at=r["created_at"], archived_at=r["archived_at"])
        for r in rows
    ]


def _stamps(rows: Iterable[Mapping]) -> list[CallStamp]:
    return [
        CallStamp(
            thesis_id=r["thesis_id"],
            asof=r["asof"],
            reconstructed=bool(r["reconstructed"]),
            recorded_at=r["recorded_at"],
        )
        for r in rows
    ]


def read_night_coverage(
    conn: psycopg.Connection, nights: Sequence[date], *, run_at: time, tz: tzinfo
) -> dict[date, NightCoverage]:
    """Two read-only queries (the thesis roster + the ``calls`` rows for those nights), then
    ``judge_coverage``. ``{}`` for no nights. The caller owns the connection and its transaction."""
    nights = list(nights)
    if not nights:
        return {}
    spans = _spans(thesis_repo.lifespans(conn))
    stamps = _stamps(calls_repo.thesis_night_stamps(conn, asofs=nights))
    return judge_coverage(nights, spans, stamps, run_at=run_at, tz=tz)


def read_on_time_theses(
    conn: psycopg.Connection, asof: date, *, run_at: time, tz: tzinfo
) -> dict[UUID, datetime]:
    """One read-only query, then ``on_time_theses`` — the M2 catch-up's skip set for ``asof``."""
    stamps = _stamps(calls_repo.thesis_night_stamps(conn, asofs=[asof]))
    return on_time_theses(stamps, asof, run_at=run_at, tz=tz)
