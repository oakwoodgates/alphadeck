from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.enums import State
from replay.metrics import MIN_N, compute_metrics
from replay.schema import CallSnapshot
from scoreboard.prices import PgRealizedPrices
from scoreboard.record import scoreboard_records
from scoreboard.schema import ScoreboardResult, ScoreboardSummary

# The full assembly: the record walk + replay's claim-tied metric set over ELIGIBLE outcomes only —
# matured (the episode's own exit_by elapsed; a running return must never drift inside a metric
# before the claim's own deadline) AND non-censored (the record saw the arm) AND clean-ingest (2d:
# the arm's provenance is un-flagged — a freeze-era/partial/thawed-late arm stays ledger-visible but
# never shapes an aggregate). The timeline handed to the withheld-arm metric is censor-trimmed the
# same way: a warming run already open on the record's FIRST card has an unknowable start — dropped,
# never guessed (deliberate, named limitation: those warming-run timelines are NOT provenance-
# filtered — episodes are the provenance unit; see docs/SCOREBOARD.md). The banner keeps the whole
# layer honest: an instrument, not a claim, until n accrues.


class _RoutedPrices:
    """``compute_metrics`` takes ONE realized reader; tenants are per-thesis. Routes each read to
    the owning name's tenant (single-name sids — the withheld metric's whole universe)."""

    def __init__(
        self,
        conn: psycopg.Connection,
        sid_tenant: dict[UUID, UUID],
        cap: date,
        known_at: datetime | None,
    ) -> None:
        self._conn = conn
        self._sid_tenant = sid_tenant
        self._cap = cap
        self._known_at = known_at
        self._readers: dict[UUID, PgRealizedPrices] = {}

    def _for(self, security_id: UUID) -> PgRealizedPrices:
        tenant = self._sid_tenant.get(security_id, DEFAULT_TENANT_ID)
        if tenant not in self._readers:
            self._readers[tenant] = PgRealizedPrices(
                self._conn, tenant_id=tenant, cap=self._cap, known_at=self._known_at
            )
        return self._readers[tenant]

    def first_close_on_or_after(self, security_id: UUID, d: date):
        return self._for(security_id).first_close_on_or_after(security_id, d)

    def last_close_through(self, security_id: UUID, through: date):
        return self._for(security_id).last_close_through(security_id, through)

    def closes_between(self, security_id: UUID, start: date, end: date):
        return self._for(security_id).closes_between(security_id, start, end)


def _censor_leading_warming(snaps: list[CallSnapshot]) -> list[CallSnapshot]:
    """Drop a warming-with-conviction run that is already OPEN on the record's first card — its
    start is unknowable (the record began mid-warm), so the withheld metric never prices it.
    Later runs are untouched (they have a visible non-warming boundary before them)."""
    i = 0
    while (
        i < len(snaps) and snaps[i].state is State.WARMING and snaps[i].conviction_grade is not None
    ):
        i += 1
    return snaps[i:]


def assemble_scoreboard(
    conn: psycopg.Connection,
    *,
    asof: date,
    include_archived: bool = True,
    known_at: datetime | None = None,
) -> ScoreboardResult:
    """The Scoreboard, assembled: every thesis's scored record + the aggregate metric summary.
    Read-only end to end (compute-on-read; the walk and the metric pass write nothing)."""
    result, timelines, single_name = scoreboard_records(
        conn, asof, include_archived=include_archived, known_at=known_at
    )
    eligible = [
        e.outcome
        for t in result.theses
        for e in t.episodes
        if e.matured and not e.censored_start and not e.ingest_flagged
    ]
    trimmed = {tid: _censor_leading_warming(snaps) for tid, snaps in timelines.items()}
    tenant_by_thesis = {t.thesis_id: t.tenant_id for t in result.theses}
    sid_tenant = {
        sid: tenant_by_thesis.get(tid) or DEFAULT_TENANT_ID for tid, sid in single_name.items()
    }
    metrics = compute_metrics(
        eligible,
        timeline=trimmed,
        realized=_RoutedPrices(conn, sid_tenant, asof, known_at),
        single_name_security=single_name,
    )
    record_began = min(
        (t.first_call_asof for t in result.theses if t.first_call_asof), default=None
    )
    began = f"record began {record_began}" if record_began else "no call-of-record yet"
    # 2e — the maturity horizon: the countdown behind the mute gate, derived from episodes already
    # in hand (asof-pure — a scrubbed view's countdown is coherent from that asof). next_maturity /
    # n_maturing_30d are LEDGER-WIDE (open or closed, flagged or not: every episode is still judged
    # at its own deadline); the PROJECTION respects the eligibility rule — only non-censored,
    # non-flagged future maturities can advance the eligible pool toward MIN_N. A projection over
    # currently-recorded episodes, never a promise.
    future = sorted(
        e.episode.exit_by
        for t in result.theses
        for e in t.episodes
        if e.episode.exit_by is not None and e.episode.exit_by > asof
    )
    candidates = sorted(
        e.episode.exit_by
        for t in result.theses
        for e in t.episodes
        if e.episode.exit_by is not None
        and e.episode.exit_by > asof
        and not e.censored_start
        and not e.ingest_flagged
    )
    need = MIN_N - len(eligible)
    result.summary = ScoreboardSummary(
        banner=(
            f"FORWARD RECORD, NOT A CLAIM — {began}; {len(eligible)} episodes eligible for "
            f"metrics (matured + non-censored + clean-ingest; gate n<{MIN_N}); open, immature, "
            "censored, and ingest-flagged episodes are ledger-only."
        ),
        min_n=MIN_N,
        n_eligible=len(eligible),
        record_began=record_began,
        metrics=metrics.metrics,
        next_maturity=future[0] if future else None,
        n_maturing_30d=sum(1 for d in future if d <= asof + timedelta(days=30)),
        projected_min_n_date=candidates[need - 1] if 0 < need <= len(candidates) else None,
    )
    return result
