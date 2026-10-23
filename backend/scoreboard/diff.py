"""WHAT CHANGED between two Scoreboard editions (editions S6) — computed on read, never stored.

Two editions are compared thesis by thesis, on the nights BOTH cover, through the comparison's last night
(the earlier edition's last night — exactly that whenever the later edition has reached it, which is the
designed case; ``edition_diff`` takes the earlier of the two last nights, so a later edition that has not
reached the earlier one's last night yet is never charged with nights it has no page for). Two readings come
out of it, and both are substance only (state, the armed set, verdict, grades, exit-by), never card text:

- **per company-night** — on each compared night, is the company ARMED in each edition, and on a night both
  arm it, do the verdict, entry grade, conviction grade or exit-by differ? A night with no page reads that
  edition's last page before it: the Scoreboard's own rule (the record last spoke on the prior row), so a cron
  gap in the record is never a change. Only changed company-nights are listed.
- **per episode** — each edition's arm episodes (``replay.episodes.derive_episodes``, the Scoreboard's own
  derivation) paired on (thesis, company, arm date): ``matched`` (the same arm date; listed only when the run
  differs — its de-arm, exit-by, verdict, grades or score), ``moved`` (no same-date partner, but the same
  company armed in both editions over overlapping windows; paired nearest arm date first), ``appeared`` /
  ``vanished`` (only in the later / earlier edition). An unpaired run lying WHOLLY on nights the other edition
  does not cover (before it began for that thesis, or after its last page) is ``outside``: counted, never called
  a change — the other edition could not have made that call; one that armed before the compared nights but runs
  into them is a change, flagged ``armed_before_window``. Every episode carries its score (``forward_return``,
  arm -> exit-by on realized closes through the last night); a paired one carries the delta.

``cause`` exists on every change and stays ``None`` here. Slice S7 (the automatic cut) fills it — a rule
change, a data repair, data that arrived late — from its trial arms; a cause is never guessed.

Pure core (``diff_thesis``, ``count``) over ascending ``CallSnapshot`` lists and a scorer; the one database
seam is ``edition_diff``. Read-only end to end: nothing here writes, and an edition is read through
``scoreboard.record``'s timeline sources (Edition 0 through its HONEST default read — reconstructed rows never,
annotated and late rows under the server's default policies — the same read the Scoreboard scores).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Sequence
from datetime import date
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel

from replay.episodes import derive_episodes
from replay.schema import CallSnapshot, Episode, MemberRow, Outcome
from replay.scoring import score_episode
from repositories import thesis_repo
from repositories.editions_repo import Edition
from scoreboard.prices import PgRealizedPrices, market_tape_edge
from scoreboard.record import (
    SNAPSHOT_UNREAD_CARD_KEYS,
    EditionSource,
    RecordSource,
    TimelineSource,
)

# the per-member fields a company-night compares on a night BOTH editions arm the company
NIGHT_FIELDS = ("verdict", "entry_grade", "conviction_grade", "exit_by")
# the episode fields a matched pair compares (its arm date is equal by construction; the last armed page is
# left out on purpose — a cron gap moves it without the run changing, and the de-arm date already says when
# the run ended)
EPISODE_FIELDS = (
    "dearm_date",
    "exit_by",
    "verdict",
    "entry_grade",
    "conviction_grade",
)

EpisodeChange = Literal["matched", "moved", "appeared", "vanished"]
Scorer = Callable[[Episode], Outcome | None]


class NightRead(BaseModel):
    """One company's read on one night in one edition. ``page`` = the page it comes from: the night itself, or
    the edition's last page before it when it has none that night. The four call fields are filled only on an
    ARMED night (a company that is not armed has no call to grade)."""

    page: date
    armed: bool
    verdict: str | None = None
    entry_grade: str | None = None
    conviction_grade: str | None = None
    exit_by: date | None = None


class CompanyNight(BaseModel):
    """One changed company-night. ``changed`` = ``["armed"]`` when exactly one edition arms the company, else
    the ``NIGHT_FIELDS`` that differ on a night both arm it."""

    night: date
    before: NightRead
    after: NightRead
    changed: list[str]
    cause: str | None = None


class EpisodeSide(BaseModel):
    """One edition's arm episode, scored. ``began_mid_arm`` = armed on that edition's FIRST page for the thesis
    (the true arm date is unknowable — the Scoreboard's ``censored_start``)."""

    arm_date: date
    last_armed_date: date
    dearm_date: date | None = None
    close_reason: str
    exit_by: date | None = None
    verdict: str | None = None
    entry_grade: str | None = None
    conviction_grade: str | None = None
    began_mid_arm: bool = False
    forward_return: float | None = None


class EpisodeDiff(BaseModel):
    """One episode change. ``matched`` / ``moved`` carry both sides; ``appeared`` only ``after``, ``vanished``
    only ``before``. ``arm_shift_days`` = after.arm_date - before.arm_date (calendar days; 0 when matched).
    ``differs`` (a matched pair) = the ``EPISODE_FIELDS`` that differ, plus ``forward_return`` when the scores
    do. ``return_delta`` = after - before, when both are scored. ``armed_before_window`` (an appeared /
    vanished run) = it armed before the first compared night — on a night the other edition does not cover —
    and is listed because its run reaches into the nights both cover."""

    change: EpisodeChange
    before: EpisodeSide | None = None
    after: EpisodeSide | None = None
    arm_shift_days: int | None = None
    differs: list[str] = []
    return_delta: float | None = None
    armed_before_window: bool = False
    cause: str | None = None


class NightRun(BaseModel):
    """Consecutive COMPARED nights (no compared night between them left out) on which one company's change
    is the same — the same ``changed`` fields with the same values of THOSE fields on both sides — so a run of
    30 nights reads as one line. ``before`` / ``after`` = the reads on the run's first night. Derived from
    ``nights``."""

    first: date
    last: date
    n_nights: int
    changed: list[str]
    before: NightRead
    after: NightRead


class CompanyDiff(BaseModel):
    security_id: UUID
    nights: list[CompanyNight] = []
    runs: list[NightRun] = []
    episodes: list[EpisodeDiff] = []


class NightSpan(BaseModel):
    """A run of one edition's pages the other edition does not cover (``n_nights`` pages)."""

    first: date
    last: date
    n_nights: int


class ThesisDiff(BaseModel):
    """One thesis's differences. ``first_night`` / ``last_night`` = the nights compared — the ones BOTH
    editions cover for this thesis (``None`` when they share none). ``before_only`` / ``after_only`` = the
    spans of pages only one edition holds (before the other began, or after its last page), with
    ``n_outside`` the unpaired runs lying wholly there: counted, never compared. ``n_state_nights`` = compared
    nights on which the thesis's own state (Incubating / Warming / Armed / Managing) differs."""

    thesis_id: UUID
    # display identity, filled by ``edition_diff`` (the pure core leaves the defaults)
    name: str = ""
    archived: bool = False
    tenant_id: UUID | None = None
    first_night: date | None = None
    last_night: date | None = None
    n_nights: int = 0
    n_state_nights: int = 0
    before_only: list[NightSpan] = []
    after_only: list[NightSpan] = []
    n_outside: int = 0
    n_matched: int = 0
    n_companies_unchanged: int = 0
    companies: list[CompanyDiff] = []
    error: str | None = None


class DiffCounts(BaseModel):
    """The headline: how much moved, over every thesis."""

    n_theses: int = 0
    n_theses_changed: int = 0
    n_companies_changed: int = 0
    n_thesis_nights: int = 0
    n_state_nights: int = 0
    n_company_nights_changed: int = 0
    n_armed_before_only: int = 0
    n_armed_after_only: int = 0
    n_fields_changed: int = 0
    n_episodes_matched: int = 0
    n_episodes_matched_changed: int = 0
    n_episodes_moved: int = 0
    n_episodes_appeared: int = 0
    n_episodes_vanished: int = 0
    n_episodes_outside: int = 0


# --- the pure core ----------------------------------------------------------------------------------------


def _members(snap: CallSnapshot) -> dict[UUID, MemberRow]:
    """The snapshot's member rows by security; an armed row wins over a watch row for the same company."""
    out: dict[UUID, MemberRow] = {}
    for m in snap.members:
        if m.security_id not in out or m.tier == "armed":
            out[m.security_id] = m
    return out


def _value(x: object) -> object:
    return getattr(x, "value", x)


def _read(snap: CallSnapshot, row: MemberRow | None) -> NightRead:
    if row is None or row.tier != "armed":
        return NightRead(page=snap.asof, armed=False)
    return NightRead(
        page=snap.asof,
        armed=True,
        verdict=_value(row.verdict),
        entry_grade=_value(row.entry_grade),
        conviction_grade=_value(row.conviction_grade),
        exit_by=row.exit_by,
    )


def _spans(dates: Sequence[date], first: date | None, last: date | None) -> list[NightSpan]:
    """The pages of ``dates`` outside ``[first, last]`` as at most two spans: before the window, after it
    (all of them, one span, when there is no window)."""
    head = [d for d in dates if first is None or d < first]
    tail = [d for d in dates if last is not None and d > last]
    return [NightSpan(first=g[0], last=g[-1], n_nights=len(g)) for g in (head, tail) if g]


def _carry(
    snaps: Sequence[CallSnapshot], dates: Sequence[date], night: date
) -> CallSnapshot | None:
    """The page that stands on ``night``: the last one dated on or before it (``None`` = not begun)."""
    i = bisect_right(dates, night)
    return snaps[i - 1] if i else None


def _signature(n: CompanyNight) -> tuple:
    """What makes two nights "the same change": the changed fields and THEIR values on both sides — a field
    that did not change (an armed-only night's day-to-day exit-by, say) never splits a run."""
    return (
        tuple(n.changed),
        tuple(getattr(n.before, f) for f in n.changed),
        tuple(getattr(n.after, f) for f in n.changed),
    )


def _runs(changed: Sequence[CompanyNight], position: dict[date, int]) -> list[NightRun]:
    """Group one company's changed nights into runs: consecutive in the thesis's compared nights
    (``position``) AND the same change on both sides."""
    out: list[NightRun] = []
    prev: CompanyNight | None = None
    for n in changed:
        if (
            prev is not None
            and position[n.night] == position[prev.night] + 1
            and _signature(n) == _signature(prev)
        ):
            run = out[-1]
            out[-1] = run.model_copy(update={"last": n.night, "n_nights": run.n_nights + 1})
        else:
            out.append(
                NightRun(
                    first=n.night,
                    last=n.night,
                    n_nights=1,
                    changed=list(n.changed),
                    before=n.before,
                    after=n.after,
                )
            )
        prev = n
    return out


def _span_end(e: Episode) -> date:
    return max(d for d in (e.exit_by, e.last_armed_date) if d is not None)


def _overlap(a: Episode, b: Episode) -> bool:
    return a.arm_date <= _span_end(b) and b.arm_date <= _span_end(a)


def _side(e: Episode, first_page: date | None, score: Scorer) -> EpisodeSide:
    out = score(e)
    return EpisodeSide(
        arm_date=e.arm_date,
        last_armed_date=e.last_armed_date,
        dearm_date=e.dearm_date,
        close_reason=e.close_reason,
        exit_by=e.exit_by,
        verdict=_value(e.verdict),
        entry_grade=_value(e.entry_grade),
        conviction_grade=_value(e.conviction_grade),
        began_mid_arm=first_page is not None and e.arm_date == first_page,
        forward_return=out.forward_return if out is not None else None,
    )


def _delta(b: EpisodeSide, a: EpisodeSide) -> float | None:
    if b.forward_return is None or a.forward_return is None:
        return None
    return a.forward_return - b.forward_return


def _pair(
    before: list[Episode], after: list[Episode]
) -> tuple[
    list[tuple[Episode, Episode]], list[tuple[Episode, Episode]], list[Episode], list[Episode]
]:
    """(matched, moved, before-only, after-only) for ONE company. Matched = the same arm date; moved = the
    remaining pairs whose windows (arm date -> exit-by) overlap, nearest arm date first, each episode used once.
    """
    by_arm = {e.arm_date: e for e in after}
    matched = [(b, by_arm[b.arm_date]) for b in before if b.arm_date in by_arm]
    taken_b = {b.arm_date for b, _ in matched}
    rest_b = [b for b in before if b.arm_date not in taken_b]
    rest_a = [a for a in after if a.arm_date not in taken_b]
    candidates = sorted(
        (abs((a.arm_date - b.arm_date).days), b.arm_date, a.arm_date, i, j)
        for i, b in enumerate(rest_b)
        for j, a in enumerate(rest_a)
        if _overlap(b, a)
    )
    used_b: set[int] = set()
    used_a: set[int] = set()
    moved: list[tuple[Episode, Episode]] = []
    for *_, i, j in candidates:
        if i in used_b or j in used_a:
            continue
        used_b.add(i)
        used_a.add(j)
        moved.append((rest_b[i], rest_a[j]))
    only_b = [b for i, b in enumerate(rest_b) if i not in used_b]
    only_a = [a for j, a in enumerate(rest_a) if j not in used_a]
    return matched, moved, only_b, only_a


def diff_thesis(
    thesis_id: UUID,
    before: Sequence[CallSnapshot],
    after: Sequence[CallSnapshot],
    *,
    through: date,
    score: Scorer,
) -> ThesisDiff:
    """One thesis's differences between the BEFORE (earlier) and AFTER (later) edition's timelines, every page
    after ``through`` ignored. Pure: the scorer is the only reach outside the arguments."""
    b_snaps = sorted((s for s in before if s.asof <= through), key=lambda s: s.asof)
    a_snaps = sorted((s for s in after if s.asof <= through), key=lambda s: s.asof)
    b_dates = [s.asof for s in b_snaps]
    a_dates = [s.asof for s in a_snaps]
    b_first = b_dates[0] if b_dates else None
    a_first = a_dates[0] if a_dates else None
    out = ThesisDiff(thesis_id=thesis_id)

    # The compared window = the nights BOTH editions cover for this thesis: from the later first page to the
    # earlier last page. A page outside it (before the other edition began, or after its last page) is that
    # edition's alone — reported as a span, never compared, never a change.
    first: date | None = None
    last: date | None = None
    if b_dates and a_dates and max(b_dates[0], a_dates[0]) <= min(b_dates[-1], a_dates[-1]):
        first, last = max(b_dates[0], a_dates[0]), min(b_dates[-1], a_dates[-1])
        out.first_night, out.last_night = first, last
    out.before_only = _spans(b_dates, first, last)
    out.after_only = _spans(a_dates, first, last)

    def inside(d: date) -> bool:
        return first is not None and first <= d <= last  # type: ignore[operator]

    def reaches(e: Episode) -> bool:
        """Does any of the run (arm -> last armed page) lie on the compared nights?"""
        return first is not None and e.arm_date <= last and e.last_armed_date >= first  # type: ignore[operator]

    # --- per company-night ----------------------------------------------------------------------------------
    nights_by_sid: dict[UUID, list[CompanyNight]] = {}
    armed_seen: set[UUID] = set()
    # each compared night's index — what makes two nights "consecutive" for the runs
    position: dict[date, int] = {}
    if first is not None:
        b_idx = {s.asof: _members(s) for s in b_snaps}
        a_idx = {s.asof: _members(s) for s in a_snaps}
        nights = sorted({d for d in (*b_dates, *a_dates) if inside(d)})
        position = {d: i for i, d in enumerate(nights)}
        out.n_nights = len(nights)
        for night in nights:
            bs, as_ = _carry(b_snaps, b_dates, night), _carry(a_snaps, a_dates, night)
            assert bs is not None and as_ is not None  # both have begun by ``first``
            if bs.state != as_.state:
                out.n_state_nights += 1
            bm, am = b_idx[bs.asof], a_idx[as_.asof]
            for sid in {
                *(k for k, m in bm.items() if m.tier == "armed"),
                *(k for k, m in am.items() if m.tier == "armed"),
            }:
                armed_seen.add(sid)
                br, ar = _read(bs, bm.get(sid)), _read(as_, am.get(sid))
                if br.armed != ar.armed:
                    changed = ["armed"]
                elif br.armed:
                    changed = [f for f in NIGHT_FIELDS if getattr(br, f) != getattr(ar, f)]
                else:
                    changed = []
                if changed:
                    nights_by_sid.setdefault(sid, []).append(
                        CompanyNight(night=night, before=br, after=ar, changed=changed)
                    )

    # --- per episode ------------------------------------------------------------------------------------------
    b_eps: dict[UUID, list[Episode]] = {}
    a_eps: dict[UUID, list[Episode]] = {}
    for e in derive_episodes(b_snaps):
        b_eps.setdefault(e.security_id, []).append(e)
    for e in derive_episodes(a_snaps):
        a_eps.setdefault(e.security_id, []).append(e)
    eps_by_sid: dict[UUID, list[EpisodeDiff]] = {}
    for sid in sorted({*b_eps, *a_eps}, key=lambda u: u.int):
        matched, moved, only_b, only_a = _pair(b_eps.get(sid, []), a_eps.get(sid, []))
        changes: list[EpisodeDiff] = []
        for b, a in matched:
            bs, as_ = _side(b, b_first, score), _side(a, a_first, score)
            differs = [f for f in EPISODE_FIELDS if getattr(bs, f) != getattr(as_, f)]
            if bs.forward_return != as_.forward_return:
                differs.append("forward_return")
            out.n_matched += 1
            if differs:
                changes.append(
                    EpisodeDiff(
                        change="matched",
                        before=bs,
                        after=as_,
                        arm_shift_days=0,
                        differs=differs,
                        return_delta=_delta(bs, as_),
                    )
                )
        for b, a in moved:
            bs, as_ = _side(b, b_first, score), _side(a, a_first, score)
            changes.append(
                EpisodeDiff(
                    change="moved",
                    before=bs,
                    after=as_,
                    arm_shift_days=(a.arm_date - b.arm_date).days,
                    return_delta=_delta(bs, as_),
                )
            )
        # an unpaired run is a change when ANY of it lies on the compared nights (one that armed before the
        # window and runs into it says so); a run wholly outside them is counted, never called a change
        for b in only_b:
            if reaches(b):
                changes.append(
                    EpisodeDiff(
                        change="vanished",
                        before=_side(b, b_first, score),
                        armed_before_window=b.arm_date < first,  # type: ignore[operator]
                    )
                )
            else:
                out.n_outside += 1  # armed only on nights the later edition does not cover
        for a in only_a:
            if reaches(a):
                changes.append(
                    EpisodeDiff(
                        change="appeared",
                        after=_side(a, a_first, score),
                        armed_before_window=a.arm_date < first,  # type: ignore[operator]
                    )
                )
            else:
                out.n_outside += 1  # armed only on nights the earlier edition does not cover
        if changes:
            changes.sort(key=lambda c: (c.before or c.after).arm_date)  # type: ignore[union-attr]
            eps_by_sid[sid] = changes

    changed_sids = sorted({*nights_by_sid, *eps_by_sid}, key=lambda u: u.int)
    out.companies = [
        CompanyDiff(
            security_id=sid,
            nights=nights_by_sid.get(sid, []),
            runs=_runs(nights_by_sid.get(sid, []), position),
            episodes=eps_by_sid.get(sid, []),
        )
        for sid in changed_sids
    ]
    # a company armed on some compared night in either edition, with nothing listed: the receipt that the
    # list above is the exception, not the whole basket (a company armed only outside the window is counted
    # in ``n_outside`` instead)
    out.n_companies_unchanged = len(armed_seen - set(changed_sids))
    return out


def count(theses: Sequence[ThesisDiff]) -> DiffCounts:
    """The headline counts over every thesis's differences."""
    c = DiffCounts(n_theses=len(theses))
    for t in theses:
        if t.companies or t.n_state_nights:
            c.n_theses_changed += 1
        c.n_companies_changed += len(t.companies)
        c.n_thesis_nights += t.n_nights
        c.n_state_nights += t.n_state_nights
        c.n_episodes_matched += t.n_matched
        c.n_episodes_outside += t.n_outside
        for co in t.companies:
            for n in co.nights:
                c.n_company_nights_changed += 1
                if n.changed == ["armed"]:
                    if n.before.armed:
                        c.n_armed_before_only += 1
                    else:
                        c.n_armed_after_only += 1
                else:
                    c.n_fields_changed += 1
            for e in co.episodes:
                if e.change == "matched":
                    c.n_episodes_matched_changed += 1
                elif e.change == "moved":
                    c.n_episodes_moved += 1
                elif e.change == "appeared":
                    c.n_episodes_appeared += 1
                else:
                    c.n_episodes_vanished += 1
    return c


# --- the database seam ------------------------------------------------------------------------------------


class MemoPrices:
    """``PgRealizedPrices`` with each security's tape read ONCE: the first touch loads every bar the reader
    can see (``d <= cap``, ``recorded_at <= known_at``, the latest version per day, null closes skipped —
    the reader's own ``bars_between`` over the whole tape), and every method answers from that list with
    the same date filter the per-call query applies. Same rows, same answers (a test pins the parity); it
    exists because a comparison scores hundreds of episodes over a few hundred names, and the per-call
    reader would issue several queries per episode (MEASURED on dev: 815 score calls, 3.7 s)."""

    def __init__(self, inner: PgRealizedPrices) -> None:
        self.inner = inner
        self._bars: dict[UUID, list[dict]] = {}

    def _tape(self, security_id: UUID) -> list[dict]:
        if security_id not in self._bars:
            self._bars[security_id] = self.inner.bars_between(security_id, date.min, self.inner.cap)
        return self._bars[security_id]

    def first_close_on_or_after(self, security_id: UUID, d: date) -> tuple[date, float] | None:
        return next(((b["d"], b["close"]) for b in self._tape(security_id) if b["d"] >= d), None)

    def last_close_through(self, security_id: UUID, through: date) -> tuple[date, float] | None:
        rows = [(b["d"], b["close"]) for b in self._tape(security_id) if b["d"] <= through]
        return rows[-1] if rows else None

    def closes_between(self, security_id: UUID, start: date, end: date) -> list[tuple[date, float]]:
        return [(b["d"], b["close"]) for b in self._tape(security_id) if start <= b["d"] <= end]

    def bars_between(self, security_id: UUID, start: date, end: date) -> list[dict]:
        return [dict(b) for b in self._tape(security_id) if start <= b["d"] <= end]

    def tape_edge(self, security_id: UUID, on_or_after: date) -> date | None:
        rows = [b["d"] for b in self._tape(security_id) if b["d"] >= on_or_after]
        return rows[-1] if rows else None

    def market_tape_edge(self, security_id: UUID) -> date | None:
        return self.inner.market_tape_edge(security_id)


class EditionDiff(BaseModel):
    through: date | None = None
    first_night: date | None = None
    counts: DiffCounts = DiffCounts()
    theses: list[ThesisDiff] = []


def _source(edition: Edition | None) -> TimelineSource:
    # Edition 0 through its HONEST default read: reconstructed rows never; annotated `exclude` and late rows
    # under the server's default policies (None = the setting) — the read the default Scoreboard scores
    return EditionSource(edition) if edition is not None else RecordSource()


def edition_diff(
    conn: psycopg.Connection,
    *,
    before: Edition | None,
    after: Edition | None,
    through: date | None,
) -> EditionDiff:
    """Compare two editions (``None`` = Edition 0, the record) over every thesis, archived included (the
    Scoreboard's default roster), through ``through`` (``None`` = an edition with no night: nothing to
    compare). Scores read the Scoreboard's own asof-capped price reader, capped at ``through``. Per-thesis
    fault isolation, like ``scoreboard_records``: an unreadable page is a visible ``error``, never a 500.
    """
    result = EditionDiff(through=through)
    if through is None:
        return result
    src_b, src_a = _source(before), _source(after)
    # ONE reader per tenant for the whole comparison (the market edge is a per-tenant scan, and a company in
    # two theses keeps its tape loaded), pinned once like the Scoreboard's per-request readers
    readers: dict[UUID, MemoPrices] = {}
    for thesis in thesis_repo.list_all(conn, include_archived=True):
        tenant = thesis.tenant_id
        if tenant not in readers:
            edge = market_tape_edge(conn, tenant_id=tenant, cap=through)
            readers[tenant] = MemoPrices(
                PgRealizedPrices(conn, tenant_id=tenant, cap=through, market_edge=edge)
            )
        prices = readers[tenant]
        ident = {
            "name": thesis.name,
            "archived": thesis.archived_at is not None,
            "tenant_id": tenant,
        }
        try:
            # the snapshot timeline only: the card keys it never reads stay in Postgres
            lean = {"drop_card_keys": SNAPSHOT_UNREAD_CARD_KEYS}
            b = src_b.read(conn, thesis.id, through, **lean).snaps
            a = src_a.read(conn, thesis.id, through, **lean).snaps
            one = diff_thesis(
                thesis.id, b, a, through=through, score=lambda e: score_episode(e, prices)
            )
            result.theses.append(one.model_copy(update=ident))
        except Exception as e:  # noqa: BLE001 — one thesis's bad page never blanks the comparison
            result.theses.append(
                ThesisDiff(thesis_id=thesis.id, error=f"{type(e).__name__}: {e}", **ident)
            )
    firsts = [t.first_night for t in result.theses if t.first_night is not None]
    result.first_night = min(firsts) if firsts else None
    result.counts = count(result.theses)
    return result
