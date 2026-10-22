"""The notify seam (slice C) — the PIPE is built; the DELIVERY is deferred (operator call, 2026-07-11).

``pipeline.daily`` detects MATERIAL TRANSITIONS — the state or verdict changed vs the PRIOR as-of's
call-of-record — and emits each through a ``Notifier``. "Material" is deliberately this compare and
nothing subtler: trigger churn, provenance reorders, and confidence drift all version the calls log
via ``record_if_changed`` without being transitions; a state/verdict move is the thing an operator
would want to be woken for (the calls-log material-change question, answered where it bites).

v1 ships ONE adapter: ``LogNotifier`` — a loud log line + the transitions block in the daily
summary. No email / push / webhook until the operator picks a channel; when they do, it is one new
adapter behind ``get_notifier()`` and zero rework in the cron. Inverse loudness (#7) lives in the
ADAPTER: the log notifier records every transition (a log is a record, not a nag); the push adapter
(``SlackNotifier``) pushes by the Q13 PUSH POLICY (``notify.policy``, operator 2026-10-17): entering Armed
and an armed -> armed UPGRADE are loud; an armed -> armed downgrade / mixed / other change is a QUIET push;
names newly armed inside an already-armed thesis ride ONE quiet digest per pass (``DigestEvent``); a de-arm
or any other quiet move is recorded only. A SPAC announcement is ONE push listing every matched thesis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from uuid import UUID

from domain.feed_kinds import StaleFeedLabel, stale_feed_bits
from domain.insider_amendments import page_text
from domain.settings import get_settings
from notify.policy import LOUD_KINDS, PushKind, classify, grade_direction, names_direction

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArmedName:
    """One armed basket member, resolved for the notification — ticker + the distinct entry-trigger
    kinds that armed it. Raw Kind.value strings; the formatter maps them to friendly short labels.
    """

    ticker: str | None
    trigger_kinds: tuple[str, ...] = ()
    grade: str | None = None  # entry_grade.value (FLIP/CORE) — carried for future richer formats
    # Q13 review — NAMES, NEVER COUNTS (#9): a name with no ticker is still listed, by its company name from
    # the security master; with neither, by its security id. Every push and the digest show ``display``.
    name: str | None = None
    security_id: UUID | None = None

    @property
    def display(self) -> str:
        return name_label(self.ticker, self.name, self.security_id)


def name_label(ticker: str | None, name: str | None, security_id: UUID | None) -> str:
    """How a push NAMES a company — never collapsed into a count (#9, operator 2026-10-17): its ticker; else
    ``"<company name> (no ticker)"`` from the security master; else ``"<security id> (no ticker)"`` (the
    master read failed, or holds no row) — the one identity left, still listed, never dropped."""
    if ticker:
        return ticker
    if name:
        return f"{name} (no ticker)"
    if security_id is not None:
        return f"{security_id} (no ticker)"
    return "an unidentified name (no ticker)"


# the digest's mark on a RE-ARM (a name armed in this thesis before, de-armed, and armed again); a first-time
# arm is unmarked
REARM = "(re-arm)"

Timing = Literal["current", "past", "late"]


def _timing_note(timing: Timing, asof: date) -> str:
    """N1 — ``"(asof 2026-10-12, recorded late)"`` / ``"(asof 2026-10-12)"`` for a past or late night; ``""``
    for tonight's (the push reads exactly as before N1). Shared by the transition push and the digest.
    """
    if timing == "late":
        return f"(asof {asof}, recorded late)"
    if timing == "past":
        return f"(asof {asof})"
    return ""


@dataclass(frozen=True)
class TransitionEvent:
    """One thesis's state/verdict move between consecutive calls-of-record."""

    thesis_id: UUID
    thesis_name: str
    asof: date
    from_state: str
    to_state: str
    from_verdict: str
    to_verdict: str
    armed: tuple[ArmedName, ...] = ()  # populated only on a → armed transition; empty otherwise
    # N1 — WHEN the push is made relative to its night (``pipeline.night_coverage.night_timing``): "current"
    # = tonight's night (no label, the normal case); "past" = a past as-of still inside its on-time window
    # (a pass that ran past midnight); "late" = recorded after the night's next EDGAR open (a catch-up or a
    # --late-ok pass — the Scoreboard sets such a row aside, L1). Defaulted, so every existing construction
    # reads as tonight's, unchanged.
    timing: Timing = "current"
    # Q13 / O3 — the two numbers the push policy reads (``notify.policy.classify``), off the PRIOR and the NEW
    # CallCard: the thesis call's grade (``CallCard.entry_grade``) and the count of distinct armed names
    # (``CallCard.armed_members``). Filled by ``pipeline.push_policy``; defaulted, so an older construction
    # classifies as before Q13 (into armed = loud, anything else = record only; armed -> armed = "changed").
    from_grade: str | None = None
    to_grade: str | None = None
    from_armed: int | None = None
    to_armed: int | None = None
    # Q13 — the card-carried REASONS a quiet armed -> armed push states (a downgrade / mixed move): the names
    # no longer armed, each with the de-arm signal the new card carries for it ("MSTR (structural break)"),
    # and the confirmation key's own detail when its grade moved. Deterministic text off the card, never an
    # LLM's (#3); empty when the card carries no reason — then the push says only what moved.
    reasons: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        arrow = f"{self.from_state} → {self.to_state}"
        if self.from_verdict != self.to_verdict:
            arrow += f" ({self.from_verdict} → {self.to_verdict})"
        return f"{self.thesis_name}: {arrow}"

    @property
    def timing_note(self) -> str:
        """``"(asof 2026-10-12, recorded late)"`` / ``"(asof 2026-10-12)"`` — the night a past or late push
        is about; ``""`` for tonight's (the push reads exactly as before N1)."""
        return _timing_note(self.timing, self.asof)

    @property
    def push_kind(self) -> PushKind:
        """The Q13 push class (``notify.policy`` — the rule, documented there)."""
        return classify(
            from_state=self.from_state,
            to_state=self.to_state,
            from_grade=self.from_grade,
            to_grade=self.to_grade,
            from_armed=self.from_armed,
            to_armed=self.to_armed,
        )

    @property
    def loud(self) -> bool:
        """Entering Armed or an armed -> armed upgrade: the only moves that earn the loud header (#7)."""
        return self.push_kind in LOUD_KINDS


@dataclass(frozen=True)
class DigestThesis:
    """One already-armed thesis's names that NEWLY armed tonight (O4), each resolved like the armed push's
    names (ticker + distinct trigger kinds; a name with no ticker is LISTED by its company name — never
    counted, never dropped, #9). ``names`` = the FIRST-TIME arms (never armed in this thesis on any earlier
    honest recorded row), ``rearms`` = the RE-ARMS (armed before, de-armed, armed again — the flapping
    names); each in the card's rank order, and a line always lists the first-time arms first."""

    thesis_id: UUID
    thesis_name: str
    names: tuple[ArmedName, ...]
    rearms: tuple[ArmedName, ...] = ()

    @property
    def all_names(self) -> tuple[ArmedName, ...]:
        return (*self.names, *self.rearms)


@dataclass(frozen=True)
class DigestEvent:
    """O4 — the pass-end QUIET digest: the names that newly armed inside theses that were ALREADY Armed and
    stayed Armed, grouped by thesis, first-time arms before re-arms (``DigestThesis``). ONE per live pass and
    ONLY when there is at least one name (never a "nothing new" ping). A name already listed on the same
    pass's thesis-level push for that thesis (an upgrade push lists every armed name) is not repeated here.
    A catch-up or late pass carries N1's timing label, exactly like a transition push."""

    asof: date
    theses: tuple[DigestThesis, ...]
    timing: Timing = "current"

    @property
    def timing_note(self) -> str:
        return _timing_note(self.timing, self.asof)

    @property
    def name_count(self) -> int:
        return sum(len(t.all_names) for t in self.theses)

    @property
    def label(self) -> str:
        parts = [
            f"{t.thesis_name}: "
            + ", ".join([n.display for n in t.names] + [f"{n.display} {REARM}" for n in t.rearms])
            for t in self.theses
        ]
        note = f" {self.timing_note}" if self.timing_note else ""
        return f"newly armed in already-armed theses{note}: " + " · ".join(parts)


@dataclass(frozen=True)
class SpacThesisMatch:
    """One thesis whose term set hit an announcing company's DA-class filing (the provenance, #6)."""

    thesis_id: UUID
    thesis_name: str
    accession: str
    signal_terms: tuple[str, ...] = ()  # SIGNAL-tier term strings that hit the DA doc
    broad_terms: tuple[str, ...] = ()  # BROAD-tier term strings that hit
    url: str | None = None  # this match's DA filing link (usually the announcement's own)


@dataclass(frozen=True)
class SpacStatusEvent:
    """One SPAC shell's deal-state transition INTO ``announced`` (a DA), for a company whose DA-class
    filing MATCHED at least one thesis's term set. ANNOUNCED-ONLY by design (the operator's loudness call):
    a deal announcement is the actionable milestone; ``terminated`` / ``completed`` moves are not paged.
    The trigger is the deterministic ``radar.state.deal_state`` derive (#3 — never an LLM), and a page is
    emitted only for a transitioned company that matched, so loudness marks the exception (#7).

    Q13 — ONE event per ANNOUNCEMENT (the transitioned CIK), listing EVERY matched thesis in ``matches``
    (sorted by thesis name). It used to be one event per (CIK, thesis): MEASURED over the radar tape through
    2026-10-16, 221 pages for 136 announcements."""

    cik: str
    company_name: str
    ticker: str | None
    accession: str  # the announcement's lead DA filing (the one most matched theses cite)
    filed: date
    matches: tuple[SpacThesisMatch, ...] = ()
    url: str | None = None  # the lead DA filing's EDGAR link (#6 — every page traces to its filing)

    @property
    def thesis_names(self) -> tuple[str, ...]:
        return tuple(m.thesis_name for m in self.matches)

    @property
    def label(self) -> str:
        who = self.ticker or self.company_name or self.cik
        return f"{who} → announced (matches {', '.join(self.thesis_names)})"


@dataclass(frozen=True)
class HealthEvent:
    """The cron's RUN-LEVEL health, emitted once per run — but ONLY when something is notable (R4). A silent
    daily job is a daily job you don't have: the R1 freeze went 11+ days undetected because the operator was
    the monitoring system. This is the page. It fires on a FREEZE (a live run in which no EDGAR fetch
    COMPLETED across the theses it processed — the cache never refreshed, or every pull failed; O5 — or in
    which fewer than the floor of its EDGAR-eligible companies got fresh SEC data: the ratio floor), on
    WITHHELD calls, or on thesis ERRORS; a healthy run emits nothing (``assess_health`` returns None) —
    loudness marks the exception.

    WITHHELD is split by its ACTUAL reason, because they are not the same news: ``withheld_failure`` (a TOTAL
    ingest failure — the ingest raised or every name errored) is a real alarm; ``withheld_no_live`` (a manual
    ``--no-live`` cache-only run, which the scheduled cron never does) is BENIGN and labeled so — a page must
    not cry "failure" when someone just ran a dev pass by hand.

    G4 — the BENCHMARK refresh counts ride here too. The SPY/IWM tape is a SHARED call-logic input
    (``benchmark_rs``), refreshed by a fail-open passenger leg whose faults used to reach stdout only: the
    night still produced calls, computed against a stale tape, and nothing said so. Two counts, because they
    are different news: ``benchmark_errors`` = individual benchmark pulls that failed (a partly stale tape),
    ``benchmark_leg_failed`` = the leg raised before producing any result (no refresh at all).
    """

    asof: date
    theses: int
    withheld_no_live: int  # benign: a manual --no-live run (the cron never passes --no-live)
    withheld_failure: (
        int  # real alarm: a total ingest failure (the ingest raised / every name errored)
    )
    errored: int
    edgar_fetches: int  # EDGAR pulls ATTEMPTED across the roster (the artifact's `edgar_fetches`)
    # live + processed theses + ZERO completed EDGAR fetches, or too few companies fresh (the ratio floor)
    frozen: bool
    # G4: benchmark-refresh faults — real alarms (the calls that night read a stale shared input)
    benchmark_errors: int = 0
    benchmark_leg_failed: bool = False
    # O5 — what the freeze verdict READ: the COMPLETED EDGAR fetches (2xx bodies) across the theses the pass
    # processed, and how many theses that was (a --catch-up's skipped ones excluded). None = the verdict was
    # judged by the pre-O5 rule (attempts over the whole roster): a crash page, or an Admin re-derivation of
    # an artifact written before O5.
    edgar_fetches_completed: int | None = None
    freeze_theses: int | None = None
    # THE RATIO FLOOR (``pipeline/edgar_coverage.py``) — what the floor READ: the distinct EDGAR-eligible
    # companies the processed theses ingested, how many got fresh SEC data (>= 1 completed fetch) anywhere in
    # the pass, and the floor the pass was judged under (0.5 = page below 50%). None = the floor was not judged
    # (a crash page; an Admin re-derivation of an artifact written before the floor existed).
    edgar_companies: int | None = None
    edgar_companies_fresh: int | None = None
    freeze_completed_floor: float | None = None
    # G5a/F1: names whose monitored FEED went stale since the previous evaluated pass — each entry a
    # (kind, label) pair, so the page can group by feed and give each its own repair advice (a price tape's
    # fix is not a fund-shares tape's). NEWLY stale only — a known-dead feed must not re-page nightly. It
    # pages, but it is NOT a cron alarm: the run worked, the FEED has a gap to repair (see the label).
    tape_stale_new: tuple[StaleFeedLabel, ...] = ()
    # H3 — the pass itself DIED: an unhandled exception escaped ``run_daily_pass`` (the DB unreachable at
    # connect, a bug outside the per-thesis isolation). The exception's type + a clipped first line — no
    # artifact was written, so this page is the only record the pass leaves. The loudest alarm; it rides
    # alone (the counts above are all zero: nothing was assessed).
    crashed: str | None = None
    # O1 — names whose ingest LEG started failing tonight (absent from the previous evaluated pass's
    # errors), each a ``"<ticker or id> (<leg>)"`` label — never a thesis name. NEWLY failing only, like
    # ``tape_stale_new``: the known-failing names (a renamed / closed / never-priced name errors every
    # night) collapse to a quiet count in the printed report and must not re-page forever. A name error is
    # not a cron fault (the thesis still recorded; ``ingest_fresh`` marks the row), so the Admin verdict
    # renders it with the benign marker.
    name_errors_new: tuple[str, ...] = ()
    # V1 — the nightly self-audit's FAILED checks, each ``"<check>: <detail>"`` (counts / ids, never thesis
    # names). A real alarm: the audit failing means the pass did not leave the night the record it claims.
    audit_failed: tuple[str, ...] = ()
    # T4 — split-basis defects (``pipeline/split_basis.py``) NEW since the previous evaluated pass, each a
    # ``"<ticker or id> <kind> x<factor> (<date>)"`` label. A vendor TAPE defect, flag only — the call is
    # unchanged — so it is benign-class like a stale feed: it pages once, never re-pages a known defect,
    # and never makes the Admin verdict unhealthy.
    split_defects_new: tuple[str, ...] = ()
    # T7 — members whose company now files under a NEW CIK (``pipeline/successor_cik.py``), NEW since the
    # previous evaluated pass, each a ``"<ticker> -> CIK <successor> (<8-K12B date>)"`` label. A data gap,
    # not a cron fault (the filing legs read the dead CIK; the operator's Adopt repairs it), so it is
    # benign-class like a stale feed: it pages once and never makes the Admin verdict unhealthy.
    successor_cik_new: tuple[str, ...] = ()
    # R8-A / R8-B — Form 4/A amendments NEW since the previous evaluated pass and OWED the operator's decision
    # (``pipeline/insider_amendment_scan.py`` — since R8-B only what the system cannot resolve: an ambiguous
    # link touching a buy/sell inside the review horizon): ``insider_amendments_new`` NAMES every one inside
    # tonight's signal windows (a ``"<ticker> <accession>: <disposition> — ..."`` label each, never collapsed
    # into a count) and then the others up to the page's cap; ``insider_amendments_more`` COUNTS the rest.
    # Benign-class (the run did its job; the decision is the operator's): it pages once, never re-pages a known
    # one, and never makes the Admin verdict unhealthy. A SYSTEM application (the filer's own correction, or
    # one with no signal effect) never pages.
    insider_amendments_new: tuple[str, ...] = ()
    insider_amendments_more: int = 0

    @property
    def withheld(self) -> int:
        return self.withheld_no_live + self.withheld_failure

    @property
    def frozen_line(self) -> str:
        """The FROZEN problem line, shared by the page (``label``) and the Admin history
        (``app/routers/admin.py::_problems``). O5 — when the verdict read COMPLETED fetches and pulls were
        ATTEMPTED, it says so: "0 of 255 completed" is an EDGAR feed that delivered nothing (a blocked
        User-Agent, an outage), not a cache that never reached out, and the two are repaired differently.

        THE RATIO FLOOR — when some EDGAR data DID arrive (completed > 0) the freeze can only be the floor's
        (too few companies got fresh data), so the line says that in plain words, with the counts it read.
        """
        n = self.freeze_theses if self.freeze_theses is not None else self.theses
        if (
            self.edgar_fetches_completed
            and self.edgar_companies
            and self.edgar_companies_fresh is not None
            and self.freeze_completed_floor is not None
        ):
            fresh, eligible = self.edgar_companies_fresh, self.edgar_companies
            return (
                f"FROZEN — the SEC feed looks starved: only {fresh} of {eligible} companies "
                f"({fresh / eligible:.1%}) got fresh SEC data this pass — below the "
                f"{self.freeze_completed_floor:.0%} floor"
            )
        if self.edgar_fetches_completed is not None and self.edgar_fetches:
            return (
                f"FROZEN — 0 of {self.edgar_fetches} EDGAR fetches COMPLETED across {n} theses "
                "(every pull failed — no EDGAR data reached the pass)"
            )
        return f"FROZEN — 0 EDGAR fetches across {n} theses (the cache never refreshed)"

    @property
    def label(self) -> str:
        bits: list[str] = []
        # alarms first, benign last — the reader should see the real problem before the dev-run note
        if self.crashed:
            bits.append(
                f"PASS CRASHED — {self.crashed} (no run artifact; the night may have NO record — "
                "check the run history, then re-run or backfill)"
            )
        if self.frozen:
            bits.append(self.frozen_line)
        if self.withheld_failure:
            bits.append(f"{self.withheld_failure} call(s) WITHHELD — TOTAL INGEST FAILURE")
        if self.errored:
            bits.append(f"{self.errored} thesis error(s)")
        # G4 — the shared-input alarms: the leg dying outright and individual pulls failing are distinct
        if self.benchmark_leg_failed:
            bits.append("BENCHMARK REFRESH LEG FAILED — the SPY/IWM tape did not refresh tonight")
        if self.benchmark_errors:
            bits.append(
                f"{self.benchmark_errors} benchmark refresh error(s) — benchmark_rs read a stale tape"
            )
        # V1 — the self-audit is an alarm (the night's record is not what the pass claims)
        if self.audit_failed:
            bits.append(f"SELF-AUDIT FAILED — {'; '.join(self.audit_failed)}")
        # O1 — benign-class: the thesis recorded; a name's leg needs a look (see the run artifact)
        if self.name_errors_new:
            bits.append(
                f"{len(self.name_errors_new)} name(s) NEWLY failing to ingest: "
                f"{', '.join(self.name_errors_new)} — the thesis still recorded, not a cron error"
            )
        if self.withheld_no_live:
            bits.append(
                f"{self.withheld_no_live} call(s) withheld — no-live (a cache-only run, not an error)"
            )
        # G5a/F1 — a FEED gap, not a cron fault, so it sits with the benign note and names what to do.
        # ONE line per feed kind, each with its own count, names and repair (a price tape's fix is the
        # vendor symbol; a fund-shares tape's is not), built by the shared `stale_feed_bits` so this page
        # and the Admin run row cannot drift — they used to keep two hand-written copies in step.
        # Why it pages at all: every price-driven detector for a dead price tape is dark until it resumes
        # (no breakout, no SMA flip, no RVOL — and no price-based de-arm), and a dead fund-shares sample
        # takes the sleeve's flow read quiet with it.
        bits.extend(f"{bit} — not a cron error" for bit in stale_feed_bits(self.tape_stale_new))
        # T4 — a price tape that mixes split bases (a half-adjusted island, an unadjusted reverse split, a
        # vendor flapping a bar's basis): a feed defect the operator should see before trusting that name's
        # price triggers. Flag only — nothing was changed in the call.
        if self.split_defects_new:
            bits.append(
                f"{len(self.split_defects_new)} NEW split-basis defect(s) on price tapes: "
                f"{', '.join(self.split_defects_new)} — flag only (the call is unchanged), not a cron error"
            )
        # T7 — the filing legs read a dead CIK for these names (Form 4 / 8-K / 13D/G missed until adopted)
        if self.successor_cik_new:
            bits.append(
                f"{len(self.successor_cik_new)} name(s) now file under a NEW CIK (a successor issuer — the "
                f"filing legs read the dead one): {', '.join(self.successor_cik_new)} — Adopt on the Admin "
                "page, not a cron error"
            )
        # R8-A / R8-B — a Form 4/A owed the operator's decision (an ambiguous one: pick the original it amends)
        if self.insider_amendments_new or self.insider_amendments_more:
            n = len(self.insider_amendments_new) + self.insider_amendments_more
            bits.append(
                f"{n} Form 4/A amendment(s) NEW and owed your decision: "
                f"{page_text(self.insider_amendments_new, self.insider_amendments_more)} — decide on the "
                "Admin Insider amendments panel, not a cron error"
            )
        return f"cron {self.asof}: " + " · ".join(bits)


class Notifier(Protocol):
    """The delivery seam — an adapter per channel; the cron never knows which one it's holding."""

    def notify(self, event: TransitionEvent) -> None: ...  # pragma: no cover — a Protocol

    def notify_health(self, event: HealthEvent) -> None: ...  # pragma: no cover — a Protocol

    def notify_spac_status(
        self, event: SpacStatusEvent
    ) -> None: ...  # pragma: no cover — a Protocol

    def notify_digest(self, event: DigestEvent) -> None: ...  # pragma: no cover — a Protocol


class LogNotifier:
    """v1: the transition is RECORDED loudly, delivered nowhere (the deferred-delivery adapter)."""

    def notify(self, event: TransitionEvent) -> None:
        # N1 — a late night's log record says so too
        late = ", recorded late" if event.timing == "late" else ""
        # Q13 — an armed -> armed move's record names its push class (the rest read as before)
        kind = event.push_kind
        tag = f" [{kind}]" if event.from_state == event.to_state == "armed" else ""
        _log.warning("TRANSITION %s%s (asof %s%s)", event.label, tag, event.asof, late)

    def notify_health(self, event: HealthEvent) -> None:
        # a run-health page is a RECORD (a bad cron night, logged loud); delivery is the Slack adapter's job
        _log.error("CRON HEALTH %s", event.label)

    def notify_spac_status(self, event: SpacStatusEvent) -> None:
        # a SPAC → announced page is a RECORD (logged loud); delivery is the Slack adapter's job
        _log.warning("SPAC ANNOUNCED %s (filed %s)", event.label, event.filed)

    def notify_digest(self, event: DigestEvent) -> None:
        # O4 — the pass's newly-armed-names digest is a RECORD too (an empty one is never emitted)
        _log.warning("DIGEST %s", event.label)


_KIND_LABELS = {
    "insider": "insider buy",
    "technical_breakout": "breakout",
    "catalyst": "catalyst",
    "laggard": "laggard",
    "squeeze": "squeeze",
    "activist_stake": "activist",
    "etf_launch": "ETF launch",
    "etf_flow": "ETF flow",
    "theme_conviction": "theme",
}


def _kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind.replace("_", " "))


def _name_tail(a: ArmedName, mark: str = "") -> str:
    """``"TICK insider buy, breakout"`` — a name (``display``: ticker, else company name) + an optional mark +
    its friendly trigger kinds, if any."""
    who = f"{a.display} {mark}" if mark else a.display
    if not a.trigger_kinds:
        return who
    return f"{who} {', '.join(_kind_label(k) for k in a.trigger_kinds)}"


def _improved(event: TransitionEvent) -> str:
    """What an armed -> armed UPGRADE improved, for its loud header: ``"now core"``, ``"+2 names"``, or both."""
    bits: list[str] = []
    if grade_direction(event.from_grade, event.to_grade) > 0:
        bits.append(f"now {event.to_grade}")
    if names_direction(event.from_armed, event.to_armed) > 0:
        n = (event.to_armed or 0) - (event.from_armed or 0)
        bits.append(f"+{n} name{'s' if n > 1 else ''}")
    return ", ".join(bits)


class SlackNotifier:
    """The loud DELIVERY channel: an incoming-webhook POST, gated by inverse loudness (#7).

    Two responsibilities, in order:

    1. **RECORD every transition** — delegates to a ``LogNotifier`` so the log line still fires for every move,
       exactly as when Slack is off (a log is a record, not a nag; the record stays regardless of delivery).
    2. **PUSH by the Q13 policy** (``notify.policy.classify``, the rule in words there): a move INTO ``armed``
       and an armed -> armed UPGRADE push LOUD (the 🔴 ARMED header); an armed -> armed downgrade, mixed move
       or other verdict change pushes QUIET (no red header, worded honestly — "still armed, weaker: ...");
       every move out of or short of armed (a de-arm, warming, incubating, managing) is a no-op for Slack,
       still logged by (1) — Incubating never nags.

    FAIL-OPEN BY CONTRACT: ``notify()`` NEVER raises. Its only caller (``pipeline.daily``) runs it *before*
    ``record_if_changed`` + ``commit`` inside a shared ``try/except`` that would ``rollback`` on any exception —
    so a raising notifier would DROP the call-of-record and falsely fail the thesis. The whole POST therefore
    sits inside a broad fail-open ``except`` (delivery is best-effort; the cron's job is the record). A Slack
    outage, bad URL, or network error is logged and swallowed — it can never corrupt the record. The same
    contract holds for every other method here.
    """

    def __init__(self) -> None:
        self._log_sink = LogNotifier()  # the record — every transition, independent of the push

    def notify(self, event: TransitionEvent) -> None:
        # (1) record every transition, always — before any push decision
        self._log_sink.notify(event)
        # (2) inverse loudness (#7), by the Q13 policy: record-only moves never push. Fail-open (the class
        # docstring): the class is computed inside the guard too, so not even a malformed event can raise.
        try:
            if event.push_kind == "record_only":
                return
            text = self._format(event)
        except Exception:  # noqa: BLE001 — a formatting fault never breaks the cron/record
            _log.warning(
                "slack notify could not format %s (fail-open, logged only)",
                event.label,
                exc_info=True,
            )
            return
        self._post(text, "notify", event.label)

    def notify_digest(self, event: DigestEvent) -> None:
        """O4 — the pass-end QUIET digest of names newly armed inside already-armed theses. Records first,
        then pushes; an EMPTY digest never pushes (no "nothing new" ping — the cron never builds one, and this
        guards the seam too). FAIL-OPEN like the others."""
        self._log_sink.notify_digest(event)
        try:
            if not event.theses:
                return
            text = self._format_digest(event)
        except Exception:  # noqa: BLE001 — a formatting fault never breaks the cron
            _log.warning("slack digest could not format (fail-open, logged only)", exc_info=True)
            return
        self._post(text, "digest", f"asof {event.asof}")

    @staticmethod
    def _post(text: str, what: str, subject: str) -> None:
        """The one best-effort webhook POST every push shares. NEVER raises: a Slack outage / bad URL /
        network error / 4xx-5xx is logged and swallowed, so it can never corrupt the record."""
        try:
            import httpx  # lazy (repo convention) — the package imports without a live HTTP client

            s = get_settings()
            url = s.slack_webhook_url
            if not url:  # defensive: no webhook => no push (the event is already logged)
                return
            resp = httpx.post(url, json={"text": text}, timeout=s.http_timeout_s)
            resp.raise_for_status()
        except Exception:  # noqa: BLE001 — best-effort delivery, never breaks the cron/record
            _log.warning(
                "slack %s failed for %s (fail-open, logged only)", what, subject, exc_info=True
            )

    def notify_health(self, event: HealthEvent) -> None:
        """R4 — the DURABLE page on a bad cron night. Unlike ``notify`` (which pushes only a→armed), EVERY
        health event pushes: it fires only when something is wrong (freeze / withheld / errored), so it is by
        construction the rare exception loudness is for. Records loud first (the log line survives regardless),
        then best-effort pushes. FAIL-OPEN, like ``notify`` — a Slack outage can never break the cron.
        """
        self._log_sink.notify_health(event)  # (1) record — always, before any push
        try:
            import httpx  # lazy (repo convention)

            s = get_settings()
            if not s.slack_webhook_url:
                return
            resp = httpx.post(
                s.slack_webhook_url,
                json={"text": f"🚨 {event.label}"},
                timeout=s.http_timeout_s,
            )
            resp.raise_for_status()
        except Exception:  # noqa: BLE001 — best-effort delivery, never breaks the cron
            _log.warning(
                "slack health notify failed for '%s' (fail-open, logged only)",
                event.label,
                exc_info=True,
            )

    def notify_spac_status(self, event: SpacStatusEvent) -> None:
        """A thesis-matched SPAC shell reached ``announced`` (a DA). ANNOUNCED-ONLY loudness (#7): the radar
        calls this ONLY on a →announced transition for a thesis-matched company (deterministic #3, never an
        LLM), ONCE per announcement however many theses it matched (Q13), so — like ``notify_health`` —
        every call is by construction the rare exception a push is for.
        Records loud first (the log line survives regardless of delivery), then best-effort pushes. FAIL-OPEN
        like the others: a Slack outage / bad URL / network error is logged and swallowed — this method must
        NEVER raise, so a delivery fault can never fail the radar leg or the cron that runs it."""
        self._log_sink.notify_spac_status(event)  # (1) record — always, before any push
        try:
            import httpx  # lazy (repo convention)

            s = get_settings()
            if not s.slack_webhook_url:  # no webhook => no push (the page is already logged above)
                return
            resp = httpx.post(
                s.slack_webhook_url,
                json={"text": self._format_spac(event)},
                timeout=s.http_timeout_s,
            )
            resp.raise_for_status()
        except Exception:  # noqa: BLE001 — best-effort delivery, never breaks the radar/cron
            _log.warning(
                "slack spac-status notify failed for %s (fail-open, logged only)",
                event.label,
                exc_info=True,
            )

    @staticmethod
    def _format(event: TransitionEvent) -> str:
        """The push text for one move, by its Q13 class. LOUD (entering Armed, an upgrade) = the compact
        company-level push: line 1 the thesis header, line 2 EVERY armed name, line 3 per-company its
        friendly trigger tail. A name with no ticker is listed by its company name ("Acme Uranium Corp (no
        ticker)") — never skipped, never counted (#9). It FALLS BACK to the thesis-only header line only when
        the enrichment produced no names at all (it failed open — a push never depends on it). An upgrade's
        header carries an explicit up marker and what improved ("ARMED ↑ now core, +2 names"). QUIET (a
        downgrade, a mixed move, any other change while armed) = ``_format_quiet``. N1: a push about a PAST or
        LATE night names it on the header (``timing_note``), so a catch-up's arm is never read as tonight's.
        """
        kind = event.push_kind
        if kind not in LOUD_KINDS:
            return SlackNotifier._format_quiet(event)
        if kind == "upgrade":
            head = (
                f"🔴 {event.thesis_name} — ARMED ↑ {_improved(event)} "
                f"({event.from_verdict} → {event.to_verdict})"
            )
        else:  # entering Armed — exactly the pre-Q13 header
            head = f"🔴 {event.thesis_name} — ARMED ({event.from_state} → {event.to_state})"
        if event.timing_note:
            head += f" {event.timing_note}"
        if not event.armed:
            return head  # thesis-only fallback (the enrichment failed open)
        lines = [head, f"{', '.join(a.display for a in event.armed)} armed"]
        tails = [_name_tail(a) for a in event.armed if a.trigger_kinds]
        if tails:
            lines.append(" · ".join(tails))
        return "\n".join(lines)

    @staticmethod
    def _format_quiet(event: TransitionEvent) -> str:
        """A QUIET armed -> armed push (#7 — loudness is reserved for a real arm): no emoji, no red header, no
        capitals, worded honestly. Line 1 says what moved — "still armed, weaker: core → flip; 4 → 3 names",
        "still armed, mixed: flip → core; 2 → 1 names" (BOTH sides of an opposite-direction move), or
        "still armed, changed" for a verdict move at the same grade and count — then the verdict arrow and
        any timing note. Line 2, only when the card carries one, the REASON: the names no longer armed, each
        with the de-arm signal on the new card, and the confirmation key's own detail when its grade moved.
        """
        kind = event.push_kind
        parts: list[str] = []
        if grade_direction(event.from_grade, event.to_grade):
            parts.append(f"{event.from_grade} → {event.to_grade}")
        if names_direction(event.from_armed, event.to_armed):
            parts.append(f"{event.from_armed} → {event.to_armed} names")
        word = {"downgrade": "weaker", "mixed": "mixed"}.get(kind, "changed")
        head = f"{event.thesis_name} — still armed, {word}"
        if parts:
            head += ": " + "; ".join(parts)
        head += f" ({event.from_verdict} → {event.to_verdict})"
        if event.timing_note:
            head += f" {event.timing_note}"
        if not event.reasons:
            return head
        return head + "\n" + "; ".join(event.reasons)

    @staticmethod
    def _format_digest(event: DigestEvent) -> str:
        """The QUIET digest: a plain header (+ N1's timing note), then one line per thesis in the compact #309
        shape — ``Thesis: TICK insider buy, breakout · TICK2 (re-arm) breakout``. The FIRST-TIME arms come
        first, unmarked; the RE-ARMS follow, marked "(re-arm)". Every name is LISTED — a name with no ticker
        by its company name, never a count (#9)."""
        head = "Newly armed in already-armed theses"
        if event.timing_note:
            head += f" {event.timing_note}"
        lines = [head]
        for t in event.theses:
            items = [_name_tail(n) for n in t.names] + [_name_tail(n, REARM) for n in t.rearms]
            lines.append(f"{t.thesis_name}: " + " · ".join(items))
        return "\n".join(lines)

    @staticmethod
    def _format_spac(event: SpacStatusEvent) -> str:
        """The compact SPAC-announced push, ONE per announcement. One matched thesis reads exactly as before
        Q13: line 1 the company + the thesis it matched, line 2 the matched terms (the provenance behind the
        match, #6), line 3 the DA filing link. Several matched theses: line 1 names them all, then one
        "<thesis> matched: ..." line each, then every distinct DA link the matches cite. American English.
        """
        who = f"{event.ticker} ({event.company_name})" if event.ticker else event.company_name
        lines = [f"🟢 {who} — SPAC deal ANNOUNCED · matches {', '.join(event.thesis_names)}"]
        single = len(event.matches) == 1
        for m in event.matches:
            terms = [*m.signal_terms, *m.broad_terms]
            if terms:
                lines.append(
                    ("" if single else f"{m.thesis_name} ") + "matched: " + ", ".join(terms)
                )
        urls = [event.url] if event.url else []
        urls += sorted({m.url for m in event.matches if m.url and m.url != event.url})
        lines.extend(urls)
        return "\n".join(lines)


def get_notifier() -> Notifier:
    """The configured notifier: the Slack adapter when ``SLACK_WEBHOOK_URL`` is set, else the LogNotifier
    fallback (the log record stays regardless). One env-selected branch — a future channel (Telegram, …) slots
    in here as one more same-shape adapter, nothing else changes."""
    if get_settings().slack_webhook_url:
        return SlackNotifier()
    return LogNotifier()
