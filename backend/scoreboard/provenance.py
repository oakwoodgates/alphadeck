from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

from domain.call import TriggerRef

# The Scoreboard's record-PROVENANCE derivation (2d) — the honesty layer over the accruing record:
# did an episode's ARM rest on trustworthy ingest? Three mechanisms, one rollup:
#
#   A  — the run stamp. The arm-date call row's ``ingest_fresh``/``ingest_errors`` (migration 0023,
#        stamped by the cron since R2b): an explicit False = the arm rested on a PARTIAL ingest.
#        ``None`` = legacy/manual append — never coerced to a judgement (0023's own rule).
#   B1 — the freeze-era window. Arms made inside the 2026-07 EDGAR cache freeze
#        (``docs/POSTMORTEM_CRON_FREEZE_2026-07.md``): the harm B2 cannot see — an arm resting on
#        promptly-ingested OLDER facts while the frozen ``submissions`` index hid the newer filings.
#   B2 — the derived DISCLOSURE-lateness marker. How late the facts the arm CITED became public: max
#        calendar-day disclosure lag (first ``COALESCE(accepted, recorded_at)`` vs latest ``valid_from``)
#        across the arm triggers' form4 accessions, derived from ``fact_insider_txn``'s bitemporal axes.
#        LOCKED DECISION 3 (the MRVL two-clock fix): metrics eligibility keys on PUBLIC KNOWABILITY
#        (SEC acceptance = ``accepted``), NOT our fetch timing — a Form 4 accepted ~2d after the txn is
#        promptly knowable, so it no longer false-flags as "ingested late" just because the demo was
#        re-ingested in 2026. While ``accepted`` is NULL (pre-backfill) COALESCE falls back to
#        ``recorded_at`` — byte-identical to the old ingest-lag behavior, so the rollout is progressive.
#        The pure INGEST-lag freeze signal is carried by B1's window + A's run stamp (and the
#        cron-health / dead-man's-switch layer), not this metrics gate.
#
# CRITICAL — the SCOREBOARD's honesty layer, NEVER a call/scoring input (the 0023 rule, extended):
# these flags are composed AFTER ``score_episode``, from reads the scoring path never sees. This
# module imports nothing from ``calls/``; nothing in ``calls/``/``pipeline/``/the write path imports
# it. A clean, a flagged, and a legacy-NULL episode all SCORE identically — the flags only
# segment/annotate (ledger-visible always; excluded from the aggregate metrics only).

# Max acceptable calendar days between an insider fact's event date and its PUBLIC DISCLOSURE (the SEC
# acceptance date, ``accepted``). A compliant Form 4 is accepted <= 2 business days after the transaction
# (<= 4 calendar across a weekend): beyond 7 is unambiguously not a promptly-disclosed filing. LOCKED
# DECISION 3 (MRVL two-clock fix): this keys on disclosure via ``COALESCE(accepted, recorded_at)``, NOT our
# ingest timing — so a Form 4 accepted ~2d after the txn but re-ingested months later no longer false-flags
# (the demo-rebuild "ingested 326d" case). While ``accepted`` is NULL (pre-backfill) COALESCE falls back to
# ``recorded_at`` (the prior ingest-lag semantics), so the rollout is progressive. The freeze cohort stays
# covered by B1's hardcoded window + A's run stamp; a pure ingest-lag freeze belongs to the cron-health layer.
THAW_LAG_DAYS = 7

# The 2026-07 EDGAR cache-freeze window, INCLUSIVE both ends (docs/POSTMORTEM_CRON_FREEZE_2026-07.md:
# the record began 2026-07-10 already frozen; R1/#196's key-classed 12h TTL landed 2026-07-17).
# Tenant-independent — the cache freeze was process-wide.
FREEZE_WINDOW = (date(2026, 7, 10), date(2026, 7, 17))


@dataclass(frozen=True)
class EpisodeProvenance:
    """The five per-episode provenance fields (additive on ``ScoredEpisode``)."""

    arm_ingest_fresh: bool | None  # A, raw stamp — None = legacy/unknown, never a judgement
    freeze_era: bool  # B1 — arm_date inside FREEZE_WINDOW
    thaw_lag_days: int | None  # B2 — None = no form4 sources / no fact rows (unknown)
    ingest_flagged: bool  # the rollup the badge + the metric-exclusion read
    ingest_note: str | None  # the composed human "why" (invariant #6) — None when clean


def form4_accessions(triggers: list[TriggerRef]) -> list[str]:
    """The form4 accessions cited by these triggers' provenance — B2's join key into
    ``fact_insider_txn`` (the insider detector stamps one ``Provenance(source="form4",
    ref=<accession>)`` per cluster accession). Sorted + deduped so callers batch deterministically.
    """
    return sorted({s.ref for t in triggers for s in t.sources if s.source == "form4" and s.ref})


def thaw_lags(
    conn: psycopg.Connection,
    accessions: list[str],
    *,
    tenant_id: UUID,
    known_at: datetime | None = None,
) -> dict[str, int]:
    """Per-accession DISCLOSURE lag, calendar days: ``MIN(COALESCE(accepted, recorded_at))::date -
    MAX(valid_from)`` (LOCKED DECISION 3 — keys on public knowability, not our fetch timing).

    ``MIN(COALESCE(accepted, recorded_at))`` = when the filing became publicly knowable (its SEC
    acceptance date, falling back to our ingest time while ``accepted`` is NULL — the progressive
    rollout, recall-safe #9); a correction appended later must never shrink it. ``MAX(valid_from)`` =
    the filing's latest event date (a filing cannot predate its last txn — the conservative lag base).
    ``known_at`` threads the caller's read-consistency pin on the SAME knowability expression as the
    as-of read: a row not yet knowable at the pin cannot contribute. Accessions with no fact rows are
    absent from the result — unknown, which degrades to un-flagged (B1 still covers the freeze cohort)."""
    if not accessions:
        return {}
    query = (
        "SELECT accession, (MIN(COALESCE(accepted, recorded_at))::date - MAX(valid_from)) AS lag_days "
        "FROM fact_insider_txn "
        "WHERE tenant_id = %(tenant_id)s AND accession = ANY(%(accessions)s)"
    )
    params: dict[str, object] = {"tenant_id": tenant_id, "accessions": accessions}
    if known_at is not None:
        query += " AND COALESCE(accepted, recorded_at) <= %(known_at)s"
        params["known_at"] = known_at
    query += " GROUP BY accession"
    with conn.cursor() as cur:
        cur.execute(query, params)
        return {r["accession"]: r["lag_days"] for r in cur.fetchall()}


def derive_episode_provenance(
    arm_date: date,
    triggers_at_arm: list[TriggerRef],
    *,
    health: dict[date, tuple[bool | None, int | None]],
    lags: dict[str, int],
) -> EpisodeProvenance:
    """Compose one episode's five provenance fields from the arm-date run stamp (A), the freeze
    window (B1), and the batched thaw-lag map (B2). Pure over its inputs — called AFTER scoring,
    never inside it. Each ``None`` degrades to un-flagged (unknown is not a judgement)."""
    fresh, errors = health.get(arm_date, (None, None))
    freeze_era = FREEZE_WINDOW[0] <= arm_date <= FREEZE_WINDOW[1]
    ep_lags = [lags[a] for a in form4_accessions(triggers_at_arm) if a in lags]
    thaw = max(ep_lags) if ep_lags else None
    thawed_late = thaw is not None and thaw > THAW_LAG_DAYS

    notes: list[str] = []
    if fresh is False:
        count = f" ({errors} name{'s' if errors != 1 else ''} errored)" if errors else ""
        notes.append(f"partial ingest on the arm-date run{count}")
    if freeze_era:
        notes.append("armed inside the 2026-07 EDGAR freeze window")
    if thawed_late:
        notes.append(f"insider source disclosed {thaw}d after its event date")

    return EpisodeProvenance(
        arm_ingest_fresh=fresh,
        freeze_era=freeze_era,
        thaw_lag_days=thaw,
        ingest_flagged=fresh is False or freeze_era or thawed_late,
        ingest_note=" · ".join(notes) if notes else None,
    )
