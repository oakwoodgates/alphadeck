"""THE AUTOMATIC NEW-EDITION CUT (editions S7) — when the Scoreboard gets a new edition, and why.

An edition is frozen at its cut and then grows one page a night (``editions.nightly``). It is REPLACED by a new
edition only when one of two things happened since its cut — and late data alone never starts anything:

(a) **the rules changed**: the running engine's fingerprint (``editions.fingerprint``) differs from the last
    CHECKED one for the current edition E — E's own, or the one the latest ``trial_no_change`` event on E
    recorded (so a change that moved nothing is tried once, never nightly). A fingerprint made by an earlier
    fingerprint VERSION counts as a change: it cannot be compared, so the first trial after a version bump runs
    and records the current one.
(b) **a data repair ran on purpose**: a ``data_repair`` journal row (migration 0065, written by every repair
    CLI's ``--apply`` / ``--revoke``) applied after E's cut — or after the last ``trial_no_change`` — that
    changed at least one row. A run that found nothing to do is journaled, and starts nothing.

No event: no trial, no rows. With an event, THE TRIAL:

- **rules (P)**: the current engine re-runs E's ``rerun`` pages — exactly the nights E's publish computed — at
  ``known_by = E's own cut``, with E's frozen join dates and E's frozen thesis bodies (the kill criteria and
  catalysts a card shows; an edition published before S7 froze them reads today's, and its comparison then
  leaves out the two fields they feed, ``counter_case`` and ``catalyst_surface``, and says so). That is the
  data E was saved from, held fixed: the edition's OWN freeze point, the same ``--known-by`` path that
  re-computes an earlier cut exactly — not a replay of history on our store clock (invariant #1; nothing else
  here reads one). P is compared with E's stored pages card by card (canonical equality, money to the cent —
  ``editions.selfcheck.cards_match``). E's NIGHTLY and CATCH-UP pages are NOT re-tested: each was computed at
  its own night's cut, so there is no single cut to hold their data at (a named limitation; a change on one of
  those nights is attributed ``not_separated``).
- **repair (N)**: N — the new edition itself, below — is compared with E's pages on every night E covers; a
  repair's rows are stamped after E's cut, so P could never see them. A repair trial cannot separate the
  repair from data that arrived late; the label says so.

THE DECISION: P differs -> cut (rule change). A repair pending and N differs on a night E covers -> cut (data
repair). Otherwise -> ``trial_no_change`` on E (the fingerprint, the rule-log ids and the repairs it checked),
and tonight's page is appended as usual. A DELETING repair (dedup, activist, impossible dates) is visible to P
too, so when one rides the same trial as a rules change, P's differences are ``not_separated`` and nothing
pages. At most one cut per market day; never a second edition at the same cut. A cut waits (writes nothing)
while a member of N's rosters still has history loading (S5's hold, applied to a whole edition).

THE CUT: N = the current engine at ``known_by = now`` over every thesis from each company's join date —
publish's own machinery (``editions.run.finish_run`` + ``store_edition``): ``rerun`` pages, the join dates E
froze carried over (an exact snapshot date for a company E never had), the thesis bodies frozen with it. ONE
transaction stores N (number E+1, parent E, the trial's summary in ``reason.trial``, the proven changes in
``edition_cause``), appends E's ``closed`` event and inks E (``ink_running_at_close``). E stays viewable; the
Scoreboard's default (the newest) moves to N on its own. LABELS: "rule change: <declared rule labels>" (the
rule-log entries in code that E does not carry, ``domain.rule_log``) · "unexplained change" (no declared rule:
published marked, and ``notify_health`` pages) · "data repair: <summaries>" — each followed by "includes data
that arrived since Edition E". A quiet Slack line (``Notifier.notify_edition``) says a new edition exists.

Fail-open inside the nightly job (a trial fault is reported and tonight's page still goes on E), under its
lock (id 4) and its refusal while the call-of-record lock is held (re-probed before the write). DRY-RUN unless
``apply``. The ``calls`` table is only ever read.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG
from domain.market_time import market_tz
from domain.rule_log import RuleEntry, declared_since, rule_ids
from editions.engine import prepare, run_jobs
from editions.entry import Entry
from editions.fingerprint import FINGERPRINT_VERSION, engine_fingerprint, fingerprint_version
from editions.run import finish_run, load_bodies, load_run, publish_plan, store_edition
from editions.selfcheck import card_differences, cards_match
from notify import EditionCutEvent, HealthEvent, Notifier, get_notifier
from replay.schema import CallSnapshot
from repositories import data_repair_repo, editions_repo
from repositories.data_repair_repo import RepairRow
from repositories.editions_repo import Edition
from scoreboard.diff import count, diff_thesis

# the trial re-runs a whole edition (MEASURED on dev 2026-10-23: 659 pages, ~1.2 s a page on one worker); the
# nightly job runs it on more workers than its own nightly pages need (the box is idle after the pass)
TRIAL_WORKERS = 4
# the two card fields a thesis BODY feeds — left out of a rules comparison against an edition whose bodies
# were not frozen (published before S7), whose P can only read today's body
BODY_FIELDS = ("counter_case", "catalyst_surface")
_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "editions" / "runs"


class CutRefused(Exception):
    """The cut did not run (a lock, or another reason); nothing was written."""


# --- the events ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Checked:
    """What E was last checked against: the fingerprint (and its version), the rule-log ids, and the instant
    up to which journaled repairs were taken into account."""

    fingerprint: str
    version: int
    rule_ids: frozenset[str]
    repairs_through: datetime


def checked_state(conn: psycopg.Connection, edition: Edition) -> Checked:
    """E's own fingerprint, rule ids and cut, then each ``trial_no_change`` on E in order: the latest
    fingerprint wins, the rule ids accumulate, the repairs mark moves forward."""
    engine = (edition.reason or {}).get("engine") or {}
    fp, ver = edition.engine_fingerprint, fingerprint_version(engine)
    ids = set((edition.reason or {}).get("rule_log") or [])
    through = edition.data_known_by
    for ev in editions_repo.events(conn, edition.id, kind="trial_no_change"):
        eng = ev.detail.get("engine") or {}
        if eng.get("fingerprint"):
            fp, ver = eng["fingerprint"], fingerprint_version(eng)
        ids |= set(ev.detail.get("rule_log") or [])
        if ev.detail.get("repairs_through"):
            through = max(through, datetime.fromisoformat(ev.detail["repairs_through"]))
    return Checked(fp, ver, frozenset(ids), through)


@dataclass
class Events:
    edition: Edition
    engine: dict[str, Any]
    checked: Checked
    rules: bool
    rules_why: str | None
    declared: list[RuleEntry]
    repairs: list[RepairRow]  # pending, changed rows: these start a trial
    seen: list[RepairRow]  # every pending journal row (a zero-change run included)

    @property
    def any(self) -> bool:
        return self.rules or bool(self.repairs)

    @property
    def deleting(self) -> bool:
        return any(r.deletes for r in self.repairs)

    def as_json(self) -> dict[str, Any]:
        return {
            "rules": self.rules,
            "rules_why": self.rules_why,
            "checked_fingerprint": self.checked.fingerprint,
            "checked_version": self.checked.version,
            "engine_fingerprint": self.engine["fingerprint"],
            "engine_version": self.engine.get("fingerprint_version"),
            "declared": [e.id for e in self.declared],
            "repairs": [r.as_json() for r in self.repairs],
            "repairs_seen": len(self.seen),
            "repairs_through": self.checked.repairs_through.isoformat(),
        }


def detect(
    conn: psycopg.Connection,
    edition: Edition,
    *,
    engine: dict[str, Any] | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    through: datetime | None = None,
) -> Events:
    """The events since E's last check (the module docstring). Reads only. ``through`` caps the journal at
    the job's own cut (a repair applied after it is tomorrow's)."""
    engine = engine or engine_fingerprint(DEFAULT_CONFIG)
    checked = checked_state(conn, edition)
    why = None
    if checked.version != engine.get("fingerprint_version", FINGERPRINT_VERSION):
        why = (
            f"the stored fingerprint was made by fingerprint version {checked.version}; the engine's is "
            f"version {engine.get('fingerprint_version', FINGERPRINT_VERSION)}"
        )
    elif checked.fingerprint != engine["fingerprint"]:
        why = "the engine fingerprint changed"
    seen = data_repair_repo.pending(
        conn, tenant_id=tenant_id, after=checked.repairs_through, through=through
    )
    return Events(
        edition=edition,
        engine=engine,
        checked=checked,
        rules=why is not None,
        rules_why=why,
        declared=declared_since(checked.rule_ids),
        repairs=[r for r in seen if r.rows_changed > 0],
        seen=seen,
    )


def one_per_night_refusal(edition: Edition, now: datetime) -> str | None:
    """At most one new edition per market day: refused when E itself was published today (market time)."""
    tz = market_tz()
    if edition.created_at.astimezone(tz).date() == now.astimezone(tz).date():
        return (
            f"Edition {edition.number} was published today; at most one new edition per night — the next "
            "run tries again"
        )
    return None


# --- the comparisons ----------------------------------------------------------------------------------------


@dataclass
class PageDiff:
    """Stored pages against a run's cards: how many compared, which differ (and in which top-level fields),
    which the run has no card for, and how many matched only within the one-cent money tolerance."""

    compared: int = 0
    differ: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    within_cent: int = 0
    left_out: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.differ or self.missing)

    @property
    def n_pages_changed(self) -> int:
        return len(self.differ) + len(self.missing)

    def as_json(self, limit: int = 50) -> dict[str, Any]:
        return {
            "compared": self.compared,
            "pages_changed": self.n_pages_changed,
            "differ": self.differ[:limit],
            "missing": self.missing[:limit],
            "within_cent": self.within_cent,
            "left_out": list(self.left_out),
        }


def _blank(card: CallCard, fields: Sequence[str]) -> CallCard:
    if not fields:
        return card
    defaults = {"counter_case": "", "catalyst_surface": []}
    return card.model_copy(update={f: defaults[f] for f in fields})


def compare_pages(
    out: PageDiff,
    thesis_id: UUID,
    stored: Sequence[CallCard],
    computed: Sequence[CallCard],
    *,
    left_out: Sequence[str] = (),
) -> None:
    """Add one thesis's comparison to ``out``: every STORED page against the run's card for the same night."""
    mine = {c.asof: c for c in computed}
    for page in stored:
        out.compared += 1
        card = mine.get(page.asof)
        if card is None:
            out.missing.append({"thesis_id": str(thesis_id), "night": page.asof.isoformat()})
            continue
        a, b = _blank(card, left_out), _blank(page, left_out)
        same, within = cards_match(a, b)
        if same:
            out.within_cent += int(within)
        else:
            out.differ.append(
                {
                    "thesis_id": str(thesis_id),
                    "night": page.asof.isoformat(),
                    "fields": card_differences(a, b),
                }
            )


def _no_score(_episode: Any) -> None:
    return None  # the counts and the proven set compare substance, never a score


def proven_changes(
    thesis_id: UUID,
    stored: Sequence[CallCard],
    computed: Sequence[CallCard],
    *,
    cause: str,
) -> list[dict[str, Any]]:
    """The changed company-nights and episodes between stored pages and a run's cards over the SAME nights
    (``scoreboard.diff.diff_thesis`` — the comparison the "what changed" view reads), as ``edition_cause``
    rows carrying ``cause``. Substance only: a card that differs only in text has no row here."""
    if not stored:
        return []
    through = max(c.asof for c in stored)
    d = diff_thesis(
        thesis_id,
        [CallSnapshot.from_card(c) for c in stored],
        [CallSnapshot.from_card(c) for c in computed],
        through=through,
        score=_no_score,
    )
    rows: dict[tuple, dict[str, Any]] = {}
    for co in d.companies:
        for n in co.nights:
            key = (co.security_id, n.night, None)
            rows[key] = {
                "thesis_id": thesis_id,
                "security_id": co.security_id,
                "night": n.night,
                "cause": cause,
            }
        for ep in co.episodes:
            for side in (ep.before, ep.after):
                if side is not None:
                    key = (co.security_id, None, side.arm_date)
                    rows[key] = {
                        "thesis_id": thesis_id,
                        "security_id": co.security_id,
                        "arm_date": side.arm_date,
                        "cause": cause,
                    }
    return list(rows.values())


def frozen_entries(conn: psycopg.Connection, edition_id: UUID) -> dict[UUID, dict[UUID, Entry]]:
    """E's frozen join dates as engine entries."""
    return {
        tid: {
            sid: Entry(j.thesis_id, j.security_id, j.entry_date, j.estimated, j.method, j.evidence)
            for sid, j in by.items()
        }
        for tid, by in editions_repo.entries_by_thesis(conn, edition_id).items()
    }


# --- the rules trial (P) -------------------------------------------------------------------------------------


@dataclass
class RulesTrial:
    diff: PageDiff
    tested: dict[UUID, tuple[date, date]]
    bodies_frozen: bool
    known_by: datetime
    n_theses: int
    timings: dict[str, float] = field(default_factory=dict)
    # filled by ``run_trial`` once the cause is known (the proven company-nights / episodes)
    stored_by_thesis: dict[UUID, list[CallCard]] = field(default_factory=dict, repr=False)
    cards_by_thesis: dict[UUID, list[CallCard]] = field(default_factory=dict, repr=False)

    def proven(self, cause: str) -> list[dict[str, Any]]:
        return [
            row
            for tid in sorted(self.stored_by_thesis, key=str)
            for row in proven_changes(
                tid, self.stored_by_thesis[tid], self.cards_by_thesis.get(tid, []), cause=cause
            )
        ]

    def as_json(self) -> dict[str, Any]:
        return {
            "known_by": self.known_by.isoformat(),
            "theses": self.n_theses,
            "bodies": (
                "frozen" if self.bodies_frozen else "today's (the edition predates frozen bodies)"
            ),
            "pages": self.diff.as_json(),
            "timings": self.timings,
        }


def rules_trial(
    conn: psycopg.Connection,
    edition: Edition,
    *,
    work_dir: Path,
    workers: int = 1,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> RulesTrial:
    """P: the current engine over E's re-run nights at E's own cut, with E's join dates and thesis bodies
    (the module docstring). Writes only ``work_dir`` (removed after)."""
    t0 = time.perf_counter()
    stored = editions_repo.rerun_pages(conn, edition.id)
    bodies = editions_repo.frozen_bodies(conn, edition.id)
    frozen = frozen_entries(conn, edition.id)
    conn.commit()
    nights = {tid: {c.asof for c in cs} for tid, cs in stored.items() if cs}
    tested = {tid: (min(ns), max(ns)) for tid, ns in nights.items()}
    trial = RulesTrial(
        diff=PageDiff(left_out=() if bodies is not None else BODY_FIELDS),
        tested=tested,
        bodies_frozen=bodies is not None,
        known_by=edition.data_known_by,
        n_theses=len(nights),
        stored_by_thesis=stored,
    )
    if not nights:
        return trial
    mirror = work_dir / "rules-mirror"
    try:
        prep = prepare(
            conn,
            mirror,
            end=max(d for ns in nights.values() for d in ns),
            known_by=edition.data_known_by,
            tenant_id=tenant_id,
            frozen=frozen,
            nights=nights,
            bodies=bodies,
        )
        trial.timings["prepare_s"] = round(time.perf_counter() - t0, 1)
        t1 = time.perf_counter()
        cards = run_jobs(prep.cut.path, prep.jobs, cfg=DEFAULT_CONFIG, workers=workers)
        trial.timings["rerun_s"] = round(time.perf_counter() - t1, 1)
    finally:
        shutil.rmtree(mirror, ignore_errors=True)
    trial.cards_by_thesis = cards
    for tid in sorted(stored, key=str):
        compare_pages(
            trial.diff, tid, stored[tid], cards.get(tid, []), left_out=trial.diff.left_out
        )
    trial.timings["total_s"] = round(time.perf_counter() - t0, 1)
    return trial


# --- the new edition (N) -------------------------------------------------------------------------------------


@dataclass
class NewRun:
    """N, computed (not yet stored): its run directory, cards and join dates, and its comparison with E."""

    out: Path
    meta: dict[str, Any] = field(default_factory=dict)
    cards: dict[UUID, list[CallCard]] = field(default_factory=dict, repr=False)
    entries: list[Entry] = field(default_factory=list, repr=False)
    versus: PageDiff = field(default_factory=PageDiff)
    counts: dict[str, int] = field(default_factory=dict)
    loading: list[dict[str, str]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def held(self) -> bool:
        return bool(self.loading)

    def as_json(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.out),
            "known_by": self.meta.get("known_by"),
            "end": self.meta.get("end"),
            "n_pages": self.meta.get("n_pages"),
            "n_theses": self.meta.get("n_theses"),
            "versus_previous": self.versus.as_json(),
            "counts": self.counts,
            "loading": self.loading,
            "timings": self.timings,
        }


def _loading(conn: psycopg.Connection, tenant_id: UUID) -> dict[UUID, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, status FROM security_onboarding "
            "WHERE tenant_id = %s AND status <> 'done'",
            (tenant_id,),
        )
        return {r["security_id"]: r["status"] for r in cur.fetchall()}


def new_run(
    conn: psycopg.Connection,
    edition: Edition,
    *,
    out: Path,
    end: date | None,
    known_by: datetime,
    workers: int = 1,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    keep_mirror: bool = False,
) -> NewRun:
    """N: publish's own run (``editions.run.prepare`` + ``finish_run``) at ``known_by`` with E's join dates
    carried over, into ``out``; then compared with every page E holds. When a member of N's rosters still has
    history loading, the run stops after the read (``loading`` says who) — the cut waits."""
    run = NewRun(out=out)
    t0 = time.perf_counter()
    frozen = frozen_entries(conn, edition.id)
    loading = _loading(conn, tenant_id)
    conn.commit()
    out.mkdir(parents=True, exist_ok=False)
    prep = prepare(
        conn, out / "mirror", end=end, known_by=known_by, tenant_id=tenant_id, frozen=frozen
    )
    run.timings["prepare_s"] = round(time.perf_counter() - t0, 1)
    members = {
        m.security_id: m.ticker
        for j in prep.jobs
        for n in j.nights
        for m in n.basket
        if m.security_id is not None
    }
    run.loading = [
        {"security_id": str(s), "ticker": members[s] or str(s), "status": loading[s]}
        for s in sorted(members, key=lambda u: u.int)
        if s in loading
    ]
    if run.loading:
        shutil.rmtree(out / "mirror", ignore_errors=True)
        return run
    t1 = time.perf_counter()
    finish_run(
        conn,
        out,
        prep,
        workers=workers,
        tenant_id=tenant_id,
        keep_mirror=keep_mirror,
        timings=dict(run.timings),
    )
    run.timings["run_s"] = round(time.perf_counter() - t1, 1)
    run.meta, run.cards, run.entries = load_run(out)
    # N against every page E holds (its re-run, nightly and catch-up pages), and the headline counts
    t1 = time.perf_counter()
    theses = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT thesis_id FROM edition_call WHERE edition_id = %s", (edition.id,)
        )
        tids = sorted((r["thesis_id"] for r in cur.fetchall()), key=str)
    through = edition.last_night
    for tid in tids:
        stored = editions_repo.pages_for_thesis(conn, edition.id, tid, upto=date.max)
        mine = run.cards.get(tid, [])
        compare_pages(run.versus, tid, stored, mine)
        if through is not None:
            theses.append(
                diff_thesis(
                    tid,
                    [CallSnapshot.from_card(c) for c in stored],
                    [CallSnapshot.from_card(c) for c in mine],
                    through=through,
                    score=_no_score,
                )
            )
    conn.commit()
    c = count(theses)
    run.counts = {
        "n_company_nights_changed": c.n_company_nights_changed,
        "n_companies_changed": c.n_companies_changed,
        "n_theses_changed": c.n_theses_changed,
        "n_episodes_appeared": c.n_episodes_appeared,
        "n_episodes_vanished": c.n_episodes_vanished,
        "n_episodes_moved": c.n_episodes_moved,
    }
    run.timings["compare_s"] = round(time.perf_counter() - t1, 1)
    return run


# --- the labels ---------------------------------------------------------------------------------------------

_SUMMARY_CAP = 3


def repair_words(repairs: Sequence[RepairRow]) -> str:
    """ "data repair: <summary>; <summary> (+N more)" — the journal's own one-line summaries."""
    parts = [r.summary.strip().rstrip(".") for r in repairs[:_SUMMARY_CAP]]
    more = len(repairs) - _SUMMARY_CAP
    return "data repair: " + "; ".join(parts) + (f" (+{more} more)" if more > 0 else "")


def compose_label(
    *,
    previous: int,
    rules_proven: bool,
    rules_unseparated: bool,
    declared: Sequence[RuleEntry],
    repairs: Sequence[RepairRow],
    repair_cut: bool,
) -> str:
    parts: list[str] = []
    rule = (
        "rule change: " + "; ".join(e.label for e in declared) if declared else "unexplained change"
    )
    if rules_proven:
        parts.append(rule)
    if repair_cut or rules_unseparated:
        parts.append(repair_words(repairs))
    if rules_unseparated:
        parts.append(
            rule
            if declared
            else "the rules changed too, with no declared rule (not separated from the repair)"
        )
    parts.append(f"includes data that arrived since Edition {previous}")
    return " · ".join(parts)


# --- the run ------------------------------------------------------------------------------------------------


@dataclass
class CutResult:
    """One cut check. ``status``: ``no_edition`` (nothing published) · ``no_event`` (no rule change, no
    repair: nothing ran, nothing written) · ``refused`` (one per night) · ``no_change`` (the trial found
    nothing; ``trial_no_change`` recorded on apply) · ``cut`` (a new edition; stored on apply) · ``deferred``
    (a cut is due but waits on history still loading) · ``failed`` (the trial raised — fail-open).
    """

    status: str
    mode: str
    edition: Edition | None = None
    events: Events | None = None
    rules: RulesTrial | None = None
    new: NewRun | None = None
    label: str | None = None
    causes: list[str] = field(default_factory=list)
    unexplained: bool = False
    new_number: int | None = None
    new_edition_id: UUID | None = None
    reason: str | None = None
    paged: bool = False
    notified: bool = False
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        return self.mode == "apply"

    def as_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "edition": self.edition.number if self.edition else None,
            "events": self.events.as_json() if self.events else None,
            "rules_trial": self.rules.as_json() if self.rules else None,
            "new_run": self.new.as_json() if self.new else None,
            "label": self.label,
            "causes": self.causes,
            "unexplained": self.unexplained,
            "new_edition": self.new_number,
            "new_edition_id": str(self.new_edition_id) if self.new_edition_id else None,
            "reason": self.reason,
            "paged": self.paged,
            "notified": self.notified,
            "timings": self.timings,
            "line": self.line(),
            "part": self.part(),
        }

    def part(self) -> str | None:
        """The phrase the nightly's Admin line carries (``None`` = nothing to say: no event)."""
        dry = "" if self.applied else " (dry run)"
        if self.status == "cut":
            n = (self.new.counts if self.new else {}).get("n_company_nights_changed", 0)
            verb = "cut" if self.applied else "would be cut (dry run)"
            return (
                f"Edition {self.new_number} {verb} · {self.label} · {n} company-night"
                f"{'s' if n != 1 else ''} changed"
            )
        if self.status == "no_change":
            what = []
            if self.events and self.events.rules:
                what.append("the rules")
            if self.events and self.events.repairs:
                what.append("a data repair")
            return f"trial{dry}: {' and '.join(what) or 'nothing'} changed no page — no new edition"
        if self.status == "deferred":
            return f"a new edition is due but waits: {self.reason}"
        if self.status == "refused":
            return f"no trial tonight: {self.reason}"
        if self.status == "failed":
            return f"the edition trial failed ({self.reason}) · tonight's pages go on as usual"
        return None

    def line(self) -> str:
        """The whole plain-English line (the manual CLI prints it)."""
        if self.status == "no_edition":
            return "Editions · nothing published yet, nothing to try"
        e = f"Edition {self.edition.number}" if self.edition else "Editions"
        part = self.part()
        if part is None:
            return f"{e} · no rule change and no data repair since its cut (or its last trial) — nothing to try"
        return part if self.status == "cut" else f"{e} · {part}"


@dataclass(frozen=True)
class EditionDriftPage(HealthEvent):
    """The automatic cut published an edition for an UNEXPLAINED change: the engine's output moved with no
    declared rule (``domain.rule_log``). Rides ``notify_health`` (Slack when configured, else the loud log
    line). Counts only — never a thesis name."""

    edition_number: int = 0
    previous: int = 0
    pages_changed: int = 0
    company_nights: int = 0

    @property
    def label(self) -> str:
        return (
            f"editions: Edition {self.edition_number} was cut for an UNEXPLAINED change — the call logic's "
            f"output moved with no declared rule ({self.pages_changed} re-run pages differ from Edition "
            f"{self.previous}, {self.company_nights} company-nights) — see data/edition_runs and "
            "domain/rule_log.py"
        )


def drift_page(asof: date, *, number: int, previous: int, pages: int, nights: int):
    return EditionDriftPage(
        asof=asof,
        theses=0,
        withheld_no_live=0,
        withheld_failure=0,
        errored=0,
        edgar_fetches=0,
        frozen=False,
        edition_number=number,
        previous=previous,
        pages_changed=pages,
        company_nights=nights,
    )


def run_cut(
    conn: psycopg.Connection,
    *,
    apply: bool,
    known_by: datetime,
    end: date | None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    work_dir: Path,
    runs_dir: Path | None = None,
    workers: int = TRIAL_WORKERS,
    notifier: Notifier | None = None,
    keep_mirror: bool = False,
    lock_probe=None,
    engine: dict[str, Any] | None = None,
) -> CutResult:
    """Detect, try, and (with ``apply``) cut or record no change — the module docstring. ``known_by`` = the
    job's cut (the database instant it started); ``end`` = the night N runs through (``None`` = the latest
    recorded night). ``lock_probe`` returns the call-of-record lock's holder (or ``None``) and is asked again
    just before the write. The caller holds the editions lock and owns ``work_dir``. Raises ``CutRefused``
    when the call-of-record lock is taken at write time (nothing written)."""
    mode = "apply" if apply else "dry_run"
    t0 = time.perf_counter()
    edition = editions_repo.current_edition(conn, tenant_id=tenant_id)
    conn.commit()
    if edition is None:
        return CutResult("no_edition", mode)
    events = detect(conn, edition, engine=engine, tenant_id=tenant_id, through=known_by)
    conn.commit()
    res = CutResult("no_event", mode, edition=edition, events=events)
    if not events.any:
        return res
    refused = one_per_night_refusal(edition, known_by)
    if refused:
        res.status, res.reason = "refused", refused
        return res

    # P — the rules trial
    if events.rules:
        res.rules = rules_trial(
            conn, edition, work_dir=work_dir, workers=workers, tenant_id=tenant_id
        )
    p_changed = bool(res.rules and res.rules.diff.changed)
    rules_proven = p_changed and not events.deleting
    rules_unseparated = p_changed and events.deleting
    proven_cause = (
        "not_separated"
        if rules_unseparated
        else ("rule_change" if events.declared else "unexplained")
    )
    # what P proved, taken NOW, so P's cards (an edition's worth) are freed before N runs
    cause_rows = res.rules.proven(proven_cause) if (res.rules and p_changed) else []
    if res.rules is not None:
        res.rules.stored_by_thesis, res.rules.cards_by_thesis = {}, {}

    # N — needed to compare a repair, or as the new edition
    if events.repairs or p_changed:
        out = (runs_dir or _RUNS_DIR) / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-cut"
        if not apply:
            out = work_dir / "new-edition"
        res.new = new_run(
            conn,
            edition,
            out=out,
            end=end,
            known_by=known_by,
            workers=workers,
            tenant_id=tenant_id,
            keep_mirror=keep_mirror,
        )
        if res.new.held:
            names = ", ".join(n["ticker"] for n in res.new.loading[:5])
            more = len(res.new.loading) - 5
            res.status = "deferred"
            res.reason = (
                f"history still loading for {len(res.new.loading)} compan"
                f"{'y' if len(res.new.loading) == 1 else 'ies'} ({names}{f' +{more}' if more > 0 else ''})"
            )
            if apply:
                shutil.rmtree(res.new.out, ignore_errors=True)
            res.timings["total_s"] = round(time.perf_counter() - t0, 1)
            return res
    repair_cut = bool(events.repairs and res.new and res.new.versus.changed)
    do_cut = p_changed or repair_cut

    if not do_cut:
        res.status = "no_change"
        if res.new is not None:  # N was a comparison only: no edition, so no evidence to keep
            shutil.rmtree(res.new.out, ignore_errors=True)
        if apply:
            _guard(lock_probe)
            try:
                editions_repo.append_event(
                    conn,
                    tenant_id=tenant_id,
                    edition_id=edition.id,
                    kind="trial_no_change",
                    detail={
                        "engine": events.engine,
                        "rule_log": rule_ids(),
                        "repairs_through": known_by.isoformat(),
                        "repairs": [r.as_json() for r in events.seen],
                        "rules_trial": res.rules.as_json() if res.rules else None,
                        "new_run": res.new.as_json() if res.new else None,
                    },
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        res.timings["total_s"] = round(time.perf_counter() - t0, 1)
        return res

    # the cut
    assert res.new is not None
    # the new edition is stamped with the engine identity this trial checked (the same value the run computed,
    # in one process; it is what the next night's check compares against)
    res.new.meta["engine"] = dict(events.engine)
    declared = events.declared
    res.unexplained = rules_proven and not declared
    res.label = compose_label(
        previous=edition.number,
        rules_proven=rules_proven,
        rules_unseparated=rules_unseparated,
        declared=declared,
        repairs=events.repairs,
        repair_cut=repair_cut,
    )
    causes: list[str] = []
    if p_changed:
        causes.append("rule_change" if declared else "code_drift")
    if repair_cut or rules_unseparated:
        causes.append("data_repair")
    res.causes = causes
    res.status = "cut"
    res.new_number = edition.number + 1
    if not apply:
        res.timings["total_s"] = round(time.perf_counter() - t0, 1)
        return res

    plan = publish_plan(
        conn,
        res.new.meta,
        res.new.cards,
        res.new.entries,
        tenant_id=tenant_id,
        number=res.new_number,
        label=res.label,
        causes=causes,
    )
    trial_summary = {
        "parent": str(edition.id),
        "previous": edition.number,
        "rules": events.rules,
        "repair": bool(events.repairs),
        "rules_proven": rules_proven,
        "rules_unseparated": rules_unseparated,
        "unexplained": res.unexplained,
        "declared": [e.id for e in declared],
        "repairs": [r.as_json() for r in events.repairs],
        "tested": {
            str(t): [a.isoformat(), b.isoformat()]
            for t, (a, b) in (res.rules.tested.items() if res.rules else [])
        },
        "n_pages_changed": res.new.versus.n_pages_changed,
        "n_rerun_pages_changed": res.rules.diff.n_pages_changed if res.rules else 0,
        "n_company_nights_changed": res.new.counts.get("n_company_nights_changed", 0),
        "n_proven": len(cause_rows),
        "events": events.as_json(),
    }
    try:
        _guard(lock_probe)
        refused = one_per_night_refusal(edition, known_by)
        now_current = editions_repo.current_edition(conn, tenant_id=tenant_id)
        if refused or now_current is None or now_current.id != edition.id:
            raise CutRefused(
                refused or f"Edition {edition.number} is no longer the current edition"
            )
        res.new_edition_id = store_edition(
            conn,
            tenant_id=tenant_id,
            plan=plan,
            meta=res.new.meta,
            out=res.new.out,
            parent=edition,
            bodies=load_bodies(res.new.out),
            reason_extra={"trial": trial_summary},
            event_extra={
                "trial": {
                    k: trial_summary[k]
                    for k in ("previous", "n_pages_changed", "n_company_nights_changed")
                },
                "rules_trial": res.rules.as_json() if res.rules else None,
                "versus_previous": res.new.versus.as_json(),
                "counts": res.new.counts,
            },
            cause_rows=cause_rows,
            close=edition,
        )
    except BaseException as exc:
        # nothing was stored: N's run directory is no edition's evidence, so it is not kept
        shutil.rmtree(res.new.out, ignore_errors=True)
        if isinstance(exc, editions_repo.EditionExists):
            raise CutRefused(str(exc)) from exc
        raise
    res.timings["total_s"] = round(time.perf_counter() - t0, 1)
    _announce(res, notifier, asof=end or known_by.date())
    return res


def _guard(lock_probe) -> None:
    holder = lock_probe() if lock_probe is not None else None
    if holder:
        raise CutRefused(
            f"a daily pass, backfill or repair took the call-of-record lock ({holder}); nothing written"
        )


def _announce(res: CutResult, notifier: Notifier | None, *, asof: date) -> None:
    """One quiet Slack line for a cut; an UNEXPLAINED change pages through ``notify_health`` instead.
    Fail-open: a notifier that raises never undoes the cut (the artifact says so)."""
    sink = notifier if notifier is not None else get_notifier()
    assert res.edition is not None and res.new is not None and res.new_number is not None
    nights = res.new.counts.get("n_company_nights_changed", 0)
    try:
        if res.unexplained:
            sink.notify_health(
                drift_page(
                    asof,
                    number=res.new_number,
                    previous=res.edition.number,
                    pages=res.rules.diff.n_pages_changed if res.rules else 0,
                    nights=nights,
                )
            )
            res.paged = True
        else:
            sink.notify_edition(
                EditionCutEvent(
                    number=res.new_number,
                    previous=res.edition.number,
                    edition_label=res.label or "",
                    company_nights_changed=nights,
                )
            )
            res.notified = True
    except Exception:  # noqa: BLE001 — a notice that fails never fails the cut
        res.reason = "the notifier raised (the cut is stored)"


def print_cut(r: CutResult) -> None:
    print(r.line())
    ev = r.events
    if ev is not None:
        print(
            f"  events: rules {'yes — ' + (ev.rules_why or '') if ev.rules else 'no'} · repairs "
            f"{len(ev.repairs)} that changed rows ({len(ev.seen)} journaled since "
            f"{ev.checked.repairs_through.isoformat()}) · declared rules "
            f"{[e.id for e in ev.declared] or 'none'}"
        )
    if r.rules is not None:
        d = r.rules.diff
        print(
            f"  rules trial (P at Edition {r.edition.number if r.edition else '?'}'s cut "
            f"{r.rules.known_by.isoformat()}): {d.compared} re-run pages over {r.rules.n_theses} "
            f"theses · {d.n_pages_changed} differ ({len(d.missing)} missing) · {d.within_cent} matched "
            f"within a cent · bodies {'frozen' if r.rules.bodies_frozen else 'today' + chr(39) + 's'}"
            f"{' (left out: ' + ', '.join(d.left_out) + ')' if d.left_out else ''} · timings "
            f"{r.rules.timings}"
        )
        for x in d.differ[:10]:
            print(f"    differs: thesis {x['thesis_id']} {x['night']} — {', '.join(x['fields'])}")
    if r.new is not None:
        v = r.new.versus
        print(
            f"  new edition run (N at {r.new.meta.get('known_by')}): {r.new.meta.get('n_pages')} pages · "
            f"versus Edition {r.edition.number if r.edition else '?'}: {v.compared} pages compared, "
            f"{v.n_pages_changed} differ · counts {r.new.counts} · timings {r.new.timings} · run dir "
            f"{r.new.out}"
        )
    if r.reason:
        print(f"  {r.reason}")
    if r.status == "cut" and r.applied:
        print(f"  PUBLISHED Edition {r.new_number} ({r.new_edition_id}) · causes {r.causes}")
    elif r.status == "cut":
        print("  DRY RUN — nothing written (re-run with --apply to cut)")


__all__ = [
    "BODY_FIELDS",
    "TRIAL_WORKERS",
    "Checked",
    "CutRefused",
    "CutResult",
    "EditionDriftPage",
    "Events",
    "PageDiff",
    "checked_state",
    "compare_pages",
    "compose_label",
    "detect",
    "new_run",
    "one_per_night_refusal",
    "print_cut",
    "proven_changes",
    "rules_trial",
    "run_cut",
]
