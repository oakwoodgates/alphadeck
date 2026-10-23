from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, TriggerRef
from domain.config import short_hash
from domain.enums import Kind, State
from domain.market_time import market_tz
from domain.settings import get_settings
from domain.thesis import Thesis
from replay.episodes import derive_episodes
from replay.schema import CallSnapshot, Episode, MemberRow, Outcome
from replay.scoring import score_episode
from repositories import calls_repo, decisions_repo, editions_repo, thesis_repo
from repositories.call_annotations_repo import CallAnnotation
from repositories.editions_repo import Edition, InkedOutcome, JoinDate
from scoreboard import provenance
from scoreboard.decisions import attach_operator_track
from scoreboard.late import late_call_ids, resolve_exclude_late
from scoreboard.prices import PgRealizedPrices, market_tape_edge
from scoreboard.schema import (
    EditionJoin,
    ScoreboardResult,
    ScoredEpisode,
    ThesisRecord,
    Transition,
)

# The record read + episode derivation. The scoring source is the CALLS LOG (what the platform
# actually said), never a recompute: ``latest_for_thesis`` -> ascending ``CallSnapshot``s ->
# ``derive_episodes`` (replay's, as-is) -> ``score_episode`` against asof-capped realized closes.
# A day with no row means the record last spoke on the prior row (weekends / cron gaps) — episode
# boundaries stay exact because a membership change always recorded a row that day.
#
# HONEST ROWS ONLY. That assumption holds for the NIGHTLY record; it is exactly what a reconstructed
# row (``pipeline.backfill``, ``calls.reconstructed``, migration 0042) breaks: a reconstruction ran on
# TODAY's basket (``basket_member`` is full-replace — the roster on a past night is unknowable) for a
# thesis that may not have existed yet, so it can never be shown honest. THE RULE: a reconstructed row
# never defines an episode boundary. ``thesis_timeline`` reads with ``include_reconstructed=False`` —
# the filter runs BEFORE the per-as-of dedup, so every downstream consumer (``derive_episodes``,
# ``score_episode``, the metrics, the arm-day trigger enrichment, ``_dearm_detail``, the provenance
# stamp) sees ONE honest list, and a night that carries both a nightly row and a later reconstruction
# scores the nightly row. Reconstructed rows stay in the log (reversible; the evidence the backfill
# happened) and are REPORTED — the summary names the nights it has nothing honest for (every row on
# the night reconstructed; a mixed night is scored from its honest rows) — not scored. MEASURED when the
# rule landed (dev copy, 2026-09-12): of 106 episode starts that sat on a reconstructed night, 48
# survive with a later (honest) arm date, 56 drop (armed only on reconstructed rows), 2 drop (the
# thesis did not exist). A gap left by the exclusion reads exactly like a cron gap: the record last
# spoke on the prior HONEST row.
#
# ANNOTATED ROWS — the SECOND filter, and a POLICY rather than a fact (D3b, migration 0045). An honest row
# can still be WRONG: before D3a the fundamentals read dated a re-reported comparative quarter at its later
# filing, so some nightly arms rested only on a year-old revenue inflection (phantom arms). The operator
# decided (Q2) to set them aside REVERSIBLY: ``pipeline.annotate_phantom_arms`` appends an ``exclude``
# annotation beside each such row (``call_annotation`` — the row itself is never touched), and under the
# policy (``Settings.scoreboard_exclude_annotated``, default on; the request's ``include_annotated``
# overrides it) every record read below ALSO drops rows carrying an active ``exclude`` — at the same place
# and the same way as the reconstructed filter: inside the WHERE, BEFORE the per-as-of dedup, applied to
# ``latest_for_thesis`` AND the ingest-health and run-identity peer reads so all three still describe ONE
# winning row. Consequences, identical to the reconstructed rule: an excluded night reads like a cron gap
# (the record last spoke on the prior row), and a night with an excluded row and another honest row scores
# the other row. Flipping the policy (or revoking the annotation) re-scores the record exactly as before
# D3b; nothing is deleted. ``note`` annotations never filter — they ride the episode drawer as a caption.
#
# LATE ROWS — the THIRD filter, a POLICY over a DERIVED fact (L1, operator Q5 = A; ``scoreboard/late.py``).
# A non-reconstructed row recorded at/after its night's on-time window closed (the next weekday's 06:00 ET
# EDGAR open, ``pipeline.night_coverage.is_late``) can have read a filing SEC accepted after the night —
# MEASURED on the 2026-10-12 boot catch-up. Under the policy (``Settings.scoreboard_exclude_late``, default
# on; the request's ``include_late`` overrides it) every record read below ALSO drops the thesis's late rows
# — once more at the same place and the same way: inside the WHERE, BEFORE the per-as-of dedup, on all four
# peer reads. So an on-time row for the night wins over a later late re-version, and a late-only night reads
# like a cron gap. The three filters compose by AND: a row is scored only if it is not reconstructed, not
# annotated `exclude` (under that policy), and not late (under this one). Lateness is derived from
# ``recorded_at`` on every read — nothing is stored, so flipping the policy re-scores exactly as before L1.


def resolve_exclude_annotated(exclude_annotated: bool | None = None) -> bool:
    """The Scoreboard's annotated-night read policy for ONE read (D3b): an explicit bool wins (the
    request's ``include_annotated`` override, inverted by the router), ``None`` = the configured
    ``Settings.scoreboard_exclude_annotated``. One resolution per request, threaded down, so every read in
    that request applies the same policy."""
    if exclude_annotated is None:
        return get_settings().scoreboard_exclude_annotated
    return exclude_annotated


def thesis_timeline(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    *,
    exclude_annotated: bool | None = None,
    exclude_late: bool | None = None,
    late_ids: frozenset[UUID] | None = None,
) -> tuple[list[CallSnapshot], dict[date, CallCard]]:
    """The thesis's HONEST call-of-record timeline up to ``asof``, ascending, plus the cards by as-of
    (for trigger enrichment). ``latest_for_thesis`` dedups to the final card per as-of — its own
    docstring: the read a scoreboard wants — with reconstructed rows EXCLUDED before that dedup (the
    header rule: a reconstructed row never defines an episode boundary), and — under the annotated-night
    policy (``resolve_exclude_annotated``) — rows carrying an active ``exclude`` annotation too, and — under
    the late-night policy (``resolve_exclude_late``) — the thesis's LATE rows too. ``late_ids`` = the late
    rows already judged by the caller (``derive_thesis_record`` reads them once for all four peer reads);
    ``None`` = judge them here when the policy excludes."""
    if late_ids is None:
        late_ids = (
            late_call_ids(conn, thesis_id) if resolve_exclude_late(exclude_late) else frozenset()
        )
    cards = [
        c
        for c in calls_repo.latest_for_thesis(
            conn,
            thesis_id,
            include_reconstructed=False,
            exclude_annotated=resolve_exclude_annotated(exclude_annotated),
            exclude_call_ids=late_ids,
        )
        if c.asof <= asof
    ]
    cards.reverse()  # newest-first -> ascending
    return [CallSnapshot.from_card(c) for c in cards], {c.asof: c for c in cards}


# --- THE TIMELINE SOURCE SEAM (editions S4) -----------------------------------------------------------------
# WHERE the episode timeline comes from. Everything ``derive_thesis_record`` does with a timeline — episodes,
# scoring on read, the trigger enrichment, the de-arm detail, the risk tape, the record trail, the operator
# track — is the same for every source. A source decides only what is source-specific:
#
# - ``RecordSource`` = EDITION 0, the original record: ``thesis_timeline`` over the calls log under the
#   request's annotated / late policies, plus the record's own provenance (ingest stamps, run identity,
#   annotations, thaw lags). Byte-for-byte the pre-editions read (a golden pins it).
# - ``EditionSource`` = a stored edition >= 1: its frozen ``edition_call`` pages, ascending — never a
#   ``calls`` row. A re-run has no reconstructed, annotated or late rows and no ingest history (every page is
#   computed the same way, on the PUBLIC clock), so those readings do not apply. Its provenance is the
#   edition itself (the engine fingerprint caption), and its censoring rule is G12's: an arm on the
#   company's own ESTIMATED join night has an unknowable true arm date.


@dataclass(frozen=True)
class Timeline:
    """One thesis's timeline from a source: ascending snapshots, the cards by as-of, and (Edition 0 only)
    the late row ids the record's peer reads drop."""

    snaps: list[CallSnapshot]
    cards_by_asof: dict[date, CallCard]
    late_ids: frozenset[UUID] = frozenset()


class RecordSource:
    """EDITION 0 — the calls log, read exactly as the Scoreboard always read it."""

    number = 0
    provenance = True

    def __init__(
        self, *, exclude_annotated: bool | None = None, exclude_late: bool | None = None
    ) -> None:
        self.exclude_annotated = resolve_exclude_annotated(exclude_annotated)
        self.exclude_late = resolve_exclude_late(exclude_late)

    def read(self, conn: psycopg.Connection, thesis_id: UUID, asof: date) -> Timeline:
        late = late_call_ids(conn, thesis_id) if self.exclude_late else frozenset()
        snaps, cards = thesis_timeline(
            conn, thesis_id, asof, exclude_annotated=self.exclude_annotated, late_ids=late
        )
        return Timeline(snaps, cards, late)


class EditionSource:
    """A stored EDITION >= 1 — its frozen pages (``editions_repo.pages_for_thesis``), never the calls log."""

    provenance = False
    exclude_annotated = False  # nothing to set aside: the readings are "n/a" for a re-run
    exclude_late = False

    def __init__(self, edition: Edition) -> None:
        self.edition = edition
        self.number = edition.number

    def read(self, conn: psycopg.Connection, thesis_id: UUID, asof: date) -> Timeline:
        cards = editions_repo.pages_for_thesis(conn, self.edition.id, thesis_id, upto=asof)
        return Timeline([CallSnapshot.from_card(c) for c in cards], {c.asof: c for c in cards})

    def joins(self, conn: psycopg.Connection, thesis_id: UUID) -> dict[UUID, JoinDate]:
        return editions_repo.joins_for_thesis(conn, self.edition.id, thesis_id)

    def inks(
        self, conn: psycopg.Connection, thesis_id: UUID
    ) -> dict[tuple[UUID, date], InkedOutcome]:
        return editions_repo.outcomes_for_thesis(conn, self.edition.id, thesis_id)


TimelineSource = RecordSource | EditionSource

# The Outcome fields copied from the EPISODE (``replay.scoring._base_outcome``): an inked score froze only the
# price-derived numbers, so these still come from the pages on every read.
_EPISODE_OUTCOME_FIELDS = (
    "thesis_id",
    "security_id",
    "is_headline",
    "verdict",
    "entry_grade",
    "conviction_grade",
    "confidence",
    "theme_armed",
    "close_reason",
    "arm_date",
    "exit_by",
)


def inked_outcome(ink: InkedOutcome | None, ep: Episode, asof: date) -> Outcome | None:
    """THE INK (editions S5, migration 0064): an edition's FINAL score for this episode, when it applies at
    ``asof`` — else ``None`` and the caller scores on read, exactly as before the ink existed.

    - ``matured``: inked once the episode's signal window ENDED (``exit_by``); it applies at every as-of the
      window has ended by (``exit_by <= asof`` — the ``matured`` flag). Before that the episode is running
      and is scored on read, so a scrubbed-back view never shows a final number early (no lookahead, #1).
    - ``running_at_close``: inked when the edition closed mid-run, at its last night; it applies from that
      night on (an as-of inside the closed edition still scores on read — the stated G13 limitation).

    The ink froze only the price-derived numbers: the episode's own fields (close reason, grades) are taken
    from the pages again."""
    if ink is None:
        return None
    if ink.kind == "matured":
        if ep.exit_by is None or ep.exit_by > asof:
            return None
    elif asof < ink.night:
        return None
    return ink.outcome.model_copy(update={f: getattr(ep, f) for f in _EPISODE_OUTCOME_FIELDS})


# the join-date rule's words, for the drawer's note (an unknown token reads as itself)
_JOIN_METHOD_WORDS = {
    "snapshot": "basket snapshot",
    "first_pull": "first data pull",
    "own_batch": "its thesis's own first batch",
    "thesis_created": "thesis creation",
    "no_pull": "thesis creation, no data pulled",
}
_JOIN_SUFFIX_WORDS = {
    "card": "moved to the first recorded card naming it",
    "seed_cap": "capped at the 09-15 basket snapshot",
}


def join_method_words(method: str) -> str:
    base, *suffixes = method.split("+")
    words = [_JOIN_METHOD_WORDS.get(base, base.replace("_", " "))]
    words += [_JOIN_SUFFIX_WORDS.get(x, x.replace("_", " ")) for x in suffixes]
    return ", ".join(words)


def edition_join(join: JoinDate | None, arm_date: date) -> EditionJoin | None:
    """The episode's join date in an edition, and the drawer's note. The note renders only for an ESTIMATED
    join (an exact join is not an exception, #7); an arm ON an estimated join night says why it is censored.
    """
    if join is None:
        return None
    on_night = join.estimated and arm_date == join.entry_date
    note = None
    if join.estimated:
        note = f"join date estimated · {join.entry_date} ({join_method_words(join.method)})"
        if on_night:
            note += (
                " · armed on its join night, so the true arm date is unknowable — "
                "kept out of the metrics"
            )
    return EditionJoin(
        entry_date=join.entry_date,
        estimated=join.estimated,
        method=join.method,
        armed_on_join_night=on_night,
        note=note,
    )


def edition_identity_note(edition: Edition) -> str:
    """The drawer's provenance caption for an edition's episode — WHICH edition and engine computed it (the
    ``run_identity_note`` slot; backend-authored, so a hash is shortened in exactly one place)."""
    parts = [
        f"Edition {edition.number} · {edition.label}",
        f"engine {short_hash(edition.engine_fingerprint)}",
        f"policy {short_hash(edition.config_hash)}",
    ]
    if edition.code_sha:
        parts.append(f"code {short_hash(edition.code_sha)}")
    parts.append(f"cut {edition.data_known_by.astimezone(market_tz()).date().isoformat()}")
    return " · ".join(parts)


def _triggers_at_arm(card: CallCard | None, security_id: UUID) -> list[TriggerRef]:
    """The member's own fired evidence on the arm-date card (the WHY behind the arm). Falls back to
    the headline ``triggers_fired`` filtered by name (pre-M5 cards had no per-member triggers)."""
    if card is None:
        return []
    for m in card.armed_members:
        if m.security_id == security_id:
            return m.triggers
    return [t for t in card.triggers_fired if t.security_id == security_id]


def _dearm_detail(card: CallCard | None, security_id: UUID) -> str | None:
    """The composed WHY behind a ``dearmed_other`` close (Slice C1) — from the de-arm-day card, in
    priority order: the member's own fired risk labels (the risks that ended the run), then the
    card's first ``missing[]`` entry (the key that un-turned — deliberately BEFORE the state phrase:
    ``missing`` is non-empty exactly when a key is un-turned, so state-first would make it dead
    code), then the state fallback, then the member-only case (the thesis stayed Armed on another
    name — an armed card's ``missing`` is empty by construction), then None (no card recorded on
    the de-arm day — a record gap, said as silence, never guessed). Backend-authored copy — ONE
    authority for the "why" (the ``ingest_note`` precedent). The caller gates on
    ``close_reason == "dearmed_other"``; the other four tokens self-explain (#7)."""
    if card is None:
        return None
    risks = [r.label for r in card.risk_signals if r.security_id == security_id]
    if risks:
        return " · ".join(risks[:2])
    if card.missing:
        return f"now missing: {card.missing[0]}"
    if card.state in (State.WARMING, State.INCUBATING):
        return f"thesis fell back to {card.state.value.capitalize()}"
    if card.state is State.ARMED:
        return "left the armed set (thesis still Armed)"
    return None


def _risk_events(cards_by_asof: dict[date, CallCard], ep: Episode) -> list[TriggerRef]:
    """The member's risk-signal tape over the run (Slice C2): walk the recorded cards over the
    CLOSED interval ``[arm_date, dearm_date or last_armed_date]`` (arm day included — a risk live
    AT arm haircut the arm's own setup strength, and omitting it would show a clean tape for an arm
    the record itself discounted; de-arm day included — its risk is what ended the run, exactly what
    ``_dearm_detail`` reads; an open episode's ``last_armed_date`` IS the record edge), collect the
    risks attributed to the member, and DEDUPE by the raw ``(kind, event_date)`` — a live risk
    re-fires on every daily card under a stable fact-anchored event date. Different kinds on the
    same day are distinct (#9). A ``None`` event_date keys as ``(kind, None)`` — its re-fires are
    the same live risk — and is stamped with the FIRST card-asof it appeared on (a recorded fact:
    when the record first said it). Known, accepted limit: two DISTINCT same-kind risks sharing an
    event date on one member would collapse to one row — in practice the assembler emits one risk
    event per (kind, member) per card. Order: chronological by first appearance, wire order within
    a card."""
    walk_end = ep.dearm_date or ep.last_armed_date
    seen: set[tuple[Kind, date | None]] = set()
    out: list[TriggerRef] = []
    for asof in sorted(d for d in cards_by_asof if ep.arm_date <= d <= walk_end):
        for r in cards_by_asof[asof].risk_signals:
            if r.security_id != ep.security_id:
                continue
            key = (r.kind, r.event_date)
            if key in seen:
                continue
            seen.add(key)
            out.append(r if r.event_date is not None else r.model_copy(update={"event_date": asof}))
    return out


# The three per-member fields the record trail diffs (Slice C3) — the CALL-read of the member, and
# nothing that wobbles daily (confidence is deliberately out: a per-day float would spam a trail
# whose whole point is the rare, meaningful shift).
_TRANSITION_FIELDS = ("verdict", "entry_grade", "conviction_grade")


def _transitions(snaps: list[CallSnapshot], ep: Episode) -> list[Transition]:
    """The member's intra-run RECORD changes over ``[arm_date, last_armed_date]`` (Slice C3):
    consecutive-card diffs of verdict / entry_grade / conviction_grade on the member's own armed
    row. The first card is the baseline (no emission — the episode already carries the at-arm
    values); a change across a weekend/cron gap lands on the LATER card's asof (a recorded fact:
    when the record first said it). By ``derive_episodes`` construction the member is armed on
    every snap in the interval; a missing row is skipped defensively, never invented."""
    prev: MemberRow | None = None
    out: list[Transition] = []
    for s in snaps:
        if not (ep.arm_date <= s.asof <= ep.last_armed_date):
            continue
        row = next(
            (m for m in s.members if m.tier == "armed" and m.security_id == ep.security_id), None
        )
        if row is None:
            continue
        if prev is not None:
            for field in _TRANSITION_FIELDS:
                a, b = getattr(prev, field), getattr(row, field)
                if a != b:
                    out.append(
                        Transition(
                            asof=s.asof,
                            field=field,
                            from_value=a.value if a is not None else None,
                            to_value=b.value if b is not None else None,
                        )
                    )
        prev = row
    return out


# the reason tokens the annotator writes -> the caption's words (an unknown token reads as itself)
_REASON_WORDS = {"stale_comparative_revenue": "a stale-dated comparative revenue quarter"}


def _annotation_note(
    by_asof: dict[date, list[CallAnnotation]], ep: Episode, *, exclude_annotated: bool
) -> str | None:
    """The episode drawer's QUIET annotation caption (D3b) — composed from the ACTIVE annotations on the
    WINNING rows across the run ``[arm_date, dearm_date or last_armed_date]`` (``_risk_events``' closed
    interval), filtered to the ones that touch THIS member (``detail.affected_security_ids``; a row without
    that list is shown rather than guessed away). ``None`` when nothing applies — a clean episode carries
    no line (honest loudness). Two shapes:

    - a ``note``: the arm stands and is scored; the rule only changed its composition on that night.
    - an ``exclude`` on a reading that INCLUDES annotated nights: the night is a phantom arm the default
      reading sets aside — said so the operator knows this episode's number depends on the reading.

    Backend-authored copy, one authority (the ``ingest_note`` / ``run_identity_note`` precedent)."""
    walk_end = ep.dearm_date or ep.last_armed_date
    notes: list[tuple[date, CallAnnotation]] = []
    excluded: list[date] = []
    for d in sorted(x for x in by_asof if ep.arm_date <= x <= walk_end):
        for a in by_asof[d]:
            affected = (a.detail or {}).get("affected_security_ids")
            if isinstance(affected, list) and str(ep.security_id) not in {str(x) for x in affected}:
                continue
            if a.action == "exclude" and not exclude_annotated:
                excluded.append(d)
            elif a.action == "note":
                notes.append((d, a))
    parts: list[str] = []
    if excluded:
        days = ", ".join(str(d) for d in sorted(set(excluded)))
        parts.append(
            f"phantom-arm night(s) {days} — annotated exclude; scored here only because this reading "
            "includes annotated nights"
        )
    for d, a in notes:
        summary = (a.detail or {}).get("summary")
        why = _REASON_WORDS.get(a.reason, a.reason.replace("_", " "))
        parts.append(f"{d}: {summary or 'composition changed'} ({why}; still scored)")
    if not parts:
        return None
    return "annotated · " + " · ".join(parts)


def _warming_since(snaps: list[CallSnapshot]) -> date | None:
    """The start of the OPEN warming-with-conviction run at the record edge — the withheld window
    accruing right now (the honest launch-state signal for a thesis with zero episodes)."""
    since: date | None = None
    for s in reversed(snaps):
        if s.state is State.WARMING and s.conviction_grade is not None:
            since = s.asof
        else:
            break
    return since


def derive_thesis_record(
    conn: psycopg.Connection,
    thesis: Thesis,
    asof: date,
    *,
    known_at: datetime | None = None,
    market_edge: date | None = None,
    basket_size: int | None = None,
    exclude_annotated: bool | None = None,
    exclude_late: bool | None = None,
    source: TimelineSource | None = None,
) -> tuple[ThesisRecord, list[CallSnapshot]]:
    """One thesis's record scored as-of: episodes from the log, outcomes against asof-capped prices,
    plus the record-honesty flags. Returns the snapshots too (SB2 feeds them to the metric set).

    ``source`` (editions S4) is WHERE the timeline comes from: ``None`` = Edition 0, the calls log under the
    two read policies below (``RecordSource`` — exactly the pre-editions read); an ``EditionSource`` = a
    stored edition's frozen pages. Every step below is the same for both, except the three source-specific
    ones: the record provenance (Edition 0 only), ``censored_start`` (an edition censors an arm on the
    company's own ESTIMATED join night, G12) and the run-identity caption (an edition's names the edition
    and its engine fingerprint).
    ``exclude_annotated`` is the D3b read policy (``resolve_exclude_annotated``: None = the setting),
    resolved ONCE here and applied to every record read below, so the timeline, the ingest stamps, the run
    identity and the annotation caption all describe the same winning rows. ``exclude_late`` is the L1 read
    policy (``resolve_exclude_late``), resolved the same way; under it the thesis's late rows are judged
    ONCE here (``late_call_ids``) and the same ids are dropped from all four reads.

    - ``status``: open iff the run reached the record edge un-dearmed (``dearm_date is None``).
    - ``matured``: the episode's own ``exit_by`` elapsed (<= asof) — judged only at its deadline.
    - ``censored_start``: armed already on the thesis's FIRST recorded card — the record began
      mid-arm, the true arm date is unknowable; marked, never reconstructed (no backfill).
    - ``basket_size``: the roster count AS OF ``known_at`` (F12) — the caller resolves it once per
      thesis via ``thesis_repo.basket_size_asof`` (a count-only PIT read) and threads it here; None
      falls back to the LIVE ``thesis`` count (the direct-call default, e.g. unit tests). The live
      roster would misreport a past-as-of.
    """
    src: TimelineSource = (
        source
        if source is not None
        else RecordSource(exclude_annotated=exclude_annotated, exclude_late=exclude_late)
    )
    exclude = src.exclude_annotated
    timeline = src.read(conn, thesis.id, asof)
    snaps, cards_by_asof, late = timeline.snaps, timeline.cards_by_asof, timeline.late_ids
    record = ThesisRecord(
        thesis_id=thesis.id,
        tenant_id=thesis.tenant_id,
        name=thesis.name,
        ticker=thesis.ticker,
        basket_size=basket_size if basket_size is not None else len(thesis.basket),
        archived=thesis.archived_at is not None,
        first_call_asof=snaps[0].asof if snaps else None,
        last_call_asof=snaps[-1].asof if snaps else None,
        current_state=snaps[-1].state.value if snaps else None,
        current_verdict=snaps[-1].verdict.value if snaps else None,
        warming_since=_warming_since(snaps),
    )
    # tenant threading: the thesis's own tenant scopes every price read (never the default here).
    # ``market_edge`` is the REQUEST-level market tape edge, resolved once per tenant by the caller
    # and threaded through: it is the same answer for every thesis in a tenant and it costs a scan,
    # so a reader resolving its own would put that scan on all 12 theses. None = resolve lazily
    # (the single-thesis / standalone path).
    prices = PgRealizedPrices(
        conn,
        tenant_id=thesis.tenant_id,
        cap=asof,
        known_at=known_at,
        market_edge=market_edge,
    )
    if snaps:
        first_recorded = snaps[0].asof
        episodes = list(derive_episodes(snaps))
        triggers = [
            _triggers_at_arm(cards_by_asof.get(ep.arm_date), ep.security_id) for ep in episodes
        ]
        # The record's own provenance exists only for Edition 0 (``src.provenance``): an edition's pages
        # have no ingest stamps, run rows, annotations or thaw lags — they were all computed by one re-run.
        recorded = src.provenance and bool(episodes)
        # Record-provenance (2d) — composed AFTER scoring, from reads the scoring path never sees:
        # the winning arm-date rows' R2b stamps + ONE batched thaw-lag query over every cited form4
        # accession. The flags segment/annotate only; ``score_episode``'s inputs are untouched.
        # the SAME honest filter as thesis_timeline, so the stamp belongs to the row that was scored
        health = (
            calls_repo.ingest_health_for_thesis(
                conn,
                thesis.id,
                include_reconstructed=False,
                exclude_annotated=exclude,
                exclude_call_ids=late,
            )
            if recorded
            else {}
        )
        # F1/0044 — the arm-date row's RUN IDENTITY, read with the SAME include_reconstructed value (and
        # therefore the same winning row) as `health` and `thesis_timeline` above, so the caption on an
        # episode always describes the run that produced the card being scored.
        identity = (
            calls_repo.run_identity_for_thesis(
                conn,
                thesis.id,
                include_reconstructed=False,
                exclude_annotated=exclude,
                exclude_call_ids=late,
            )
            if recorded
            else {}
        )
        # D3b — the ACTIVE annotations on the SAME winning rows (the third peer read on the one rule),
        # for the drawer's quiet caption only; nothing scored reads it.
        annotations = (
            calls_repo.annotations_for_thesis(
                conn,
                thesis.id,
                include_reconstructed=False,
                exclude_annotated=exclude,
                exclude_call_ids=late,
            )
            if recorded
            else {}
        )
        lags = (
            provenance.thaw_lags(
                conn,
                provenance.form4_accessions([t for ts in triggers for t in ts]),
                tenant_id=thesis.tenant_id or DEFAULT_TENANT_ID,
                known_at=known_at,
            )
            if recorded
            else {}
        )
        # an edition's frozen join dates (G12's censoring + the drawer's note); Edition 0 has none
        joins = src.joins(conn, thesis.id) if isinstance(src, EditionSource) and episodes else {}
        # an edition's INKED scores (S5): a matured episode's final number, used in place of the on-read
        # score when it applies at this as-of (``inked_outcome``); Edition 0 is always scored on read
        inks = src.inks(conn, thesis.id) if isinstance(src, EditionSource) and episodes else {}
        for ep, trigs in zip(episodes, triggers):
            if isinstance(src, EditionSource):
                join = edition_join(joins.get(ep.security_id), ep.arm_date)
                specific = dict(
                    # G12: an arm on the company's own ESTIMATED join night has an unknowable true arm
                    # date; a company with no join row falls back to the record's rule (the first page)
                    censored_start=(
                        join.armed_on_join_night
                        if join is not None
                        else ep.arm_date == first_recorded
                    ),
                    arm_config_hash=src.edition.config_hash,
                    arm_code_sha=src.edition.code_sha,
                    arm_run_kind="rerun",
                    run_identity_note=edition_identity_note(src.edition),
                    edition_join=join,
                )
            else:
                prov = provenance.derive_episode_provenance(
                    ep.arm_date, trigs, health=health, lags=lags
                )
                ident = identity.get(ep.arm_date)
                specific = dict(
                    censored_start=ep.arm_date == first_recorded,
                    arm_ingest_fresh=prov.arm_ingest_fresh,
                    freeze_era=prov.freeze_era,
                    thaw_lag_days=prov.thaw_lag_days,
                    ingest_flagged=prov.ingest_flagged,
                    ingest_note=prov.ingest_note,
                    arm_config_hash=ident.config_hash if ident else None,
                    arm_code_sha=ident.code_sha if ident else None,
                    arm_run_kind=ident.run_kind if ident else None,
                    run_identity_note=provenance.run_identity_note(ident),
                    annotation_note=_annotation_note(annotations, ep, exclude_annotated=exclude),
                )
            inked = inked_outcome(inks.get((ep.security_id, ep.arm_date)), ep, asof)
            record.episodes.append(
                ScoredEpisode(
                    episode=ep,
                    outcome=inked if inked is not None else score_episode(ep, prices),
                    inked=inks[(ep.security_id, ep.arm_date)].kind if inked is not None else None,
                    status="open" if ep.dearm_date is None else "closed",
                    matured=ep.exit_by is not None and ep.exit_by <= asof,
                    **specific,
                    triggers_at_arm=trigs,
                    # Slice C — composed ONLY for the one opaque token (the others self-explain, #7);
                    # dict lookups over the cards already in hand, no new queries.
                    dearm_detail=(
                        _dearm_detail(cards_by_asof.get(ep.dearm_date), ep.security_id)
                        if ep.close_reason == "dearmed_other"
                        else None
                    ),
                    risk_events=_risk_events(cards_by_asof, ep),
                    transitions=_transitions(snaps, ep),
                )
            )

    # the operator track (SB3): the decision log joined to the episodes it answered — runs even
    # with no record yet (a decision can predate the first call-of-record; it rides off-record)
    rows = decisions_repo.list_for_thesis(conn, thesis.id, tenant_id=thesis.tenant_id)
    if rows:
        record.operator_spans, counts, record.decision_anomaly = attach_operator_track(
            record.episodes, rows, prices, asof
        )
        record.n_takes = counts["takes"]
        record.n_passes = counts["passes"]
        record.n_overrides = counts["overrides"]
        record.n_voided = counts["voided"]
    return record, snaps


def scoreboard_records(
    conn: psycopg.Connection,
    asof: date,
    *,
    include_archived: bool = True,
    known_at: datetime | None = None,
    exclude_annotated: bool | None = None,
    exclude_late: bool | None = None,
    edition: Edition | None = None,
) -> tuple[ScoreboardResult, dict[UUID, list[CallSnapshot]], dict[UUID, UUID]]:
    """Every thesis's record scored as-of (archived INCLUDED by default — the record is not erased
    by archiving; it just stops accruing). Per-thesis fault isolation: an unreadable historical card
    (the log outlives schema changes; ``DomainModel`` is ``extra="forbid"``) becomes a visible
    ``ThesisRecord.error``, never a raised 500 — siblings score unaffected.

    Returns ``(result, timelines, single_name_security)`` — the last is thesis_id -> its sole
    resolved member's security_id (replay's ``_single_name_security`` shape, computed here where the
    loaded theses are in scope), the unit the withheld-arm metric can price.

    ``exclude_annotated`` (D3b): the annotated-night policy, resolved ONCE for the whole request
    (``resolve_exclude_annotated``) and stamped on the result (``exclude_annotated``), so every thesis is
    read under the same reading and the response can say which one it is. ``exclude_late`` (L1): the
    late-night policy, resolved once and stamped (``exclude_late``) the same way.

    ``edition`` (editions S4): ``None`` = Edition 0, the record (the two policies above apply); a stored
    edition = its frozen pages for every thesis (``EditionSource``) — the two policies do not apply to a
    re-run and are stamped False (the summary reports them ``n/a``)."""
    source: TimelineSource
    if edition is None:
        exclude = resolve_exclude_annotated(exclude_annotated)
        exclude_late = resolve_exclude_late(exclude_late)
        source = RecordSource(exclude_annotated=exclude, exclude_late=exclude_late)
    else:
        exclude, exclude_late = False, False
        source = EditionSource(edition)
    result = ScoreboardResult(
        asof=asof,
        exclude_annotated=exclude,
        exclude_late=exclude_late,
        edition_number=source.number,
    )
    timelines: dict[UUID, list[CallSnapshot]] = {}
    single_name: dict[UUID, UUID] = {}
    # The market tape edge (``truncated``'s second leg) is per TENANT, not per thesis or per episode,
    # and it costs a seq scan. Resolve it at most once per tenant for the whole request and hand it
    # down; recomputing it per thesis would multiply that scan by the thesis count for an answer that
    # cannot differ between them.
    market_edges: dict[UUID, date | None] = {}
    for thesis in thesis_repo.list_all(conn, include_archived=include_archived):
        sids = [m.security_id for m in thesis.basket if m.security_id is not None]
        if len(sids) == 1:
            single_name[thesis.id] = sids[0]
        if thesis.tenant_id not in market_edges:
            market_edges[thesis.tenant_id] = market_tape_edge(
                conn, tenant_id=thesis.tenant_id, cap=asof, known_at=known_at
            )
        # F12 — the roster count AS OF known_at (basket_member is full-replace, so the live count would
        # misreport a past-as-of Scoreboard). A COUNT-ONLY snapshot read — NOT get_asof, which would
        # re-run the full per-thesis load (basket + children) just for a number, doubling the Scoreboard's
        # DB cost. live_fallback = the live count already in hand, covering the no-snapshot / pre-F12 case.
        pit_basket_size = thesis_repo.basket_size_asof(
            conn, thesis.id, known_at, live_fallback=len(thesis.basket)
        )
        try:
            record, snaps = derive_thesis_record(
                conn,
                thesis,
                asof,
                known_at=known_at,
                market_edge=market_edges[thesis.tenant_id],
                basket_size=pit_basket_size,
                exclude_annotated=exclude,
                exclude_late=exclude_late,
                source=source,
            )
            timelines[thesis.id] = snaps
        except Exception as e:  # noqa: BLE001 — one thesis's bad card never blanks the Scoreboard
            record = ThesisRecord(
                thesis_id=thesis.id,
                tenant_id=thesis.tenant_id,
                name=thesis.name,
                ticker=thesis.ticker,
                basket_size=pit_basket_size,
                archived=thesis.archived_at is not None,
                error=f"{type(e).__name__}: {e}",
            )
        result.theses.append(record)
    result.n_theses = len(result.theses)
    result.n_with_record = sum(1 for t in result.theses if t.first_call_asof is not None)
    result.n_episodes = sum(len(t.episodes) for t in result.theses)
    result.n_open = sum(1 for t in result.theses for e in t.episodes if e.status == "open")
    result.n_matured = sum(1 for t in result.theses for e in t.episodes if e.matured)
    result.n_censored = sum(1 for t in result.theses for e in t.episodes if e.censored_start)
    result.n_ingest_flagged = sum(1 for t in result.theses for e in t.episodes if e.ingest_flagged)
    result.n_takes = sum(t.n_takes for t in result.theses)
    result.n_passes = sum(t.n_passes for t in result.theses)
    result.n_overrides = sum(t.n_overrides for t in result.theses)
    result.n_voided = sum(t.n_voided for t in result.theses)
    return result, timelines, single_name
