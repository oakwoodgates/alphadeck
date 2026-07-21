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
#   B2 — the derived thaw marker. The lateness of the facts the arm actually CITED: max calendar-day
#        ingest lag (first ``recorded_at`` vs latest ``valid_from``) across the arm triggers' form4
#        accessions, derived from ``fact_insider_txn``'s bitemporal axes (migration 0023's comment:
#        stamped health is a property of the RUN; thaw lateness is DERIVABLE, so the Scoreboard
#        derives it). Distinguishes genuine in-window signal from thawed backlog.
#
# CRITICAL — the SCOREBOARD's honesty layer, NEVER a call/scoring input (the 0023 rule, extended):
# these flags are composed AFTER ``score_episode``, from reads the scoring path never sees. This
# module imports nothing from ``calls/``; nothing in ``calls/``/``pipeline/``/the write path imports
# it. A clean, a flagged, and a legacy-NULL episode all SCORE identically — the flags only
# segment/annotate (ledger-visible always; excluded from the aggregate metrics only).

# Max acceptable calendar days between an insider fact's event date and the platform FIRST learning
# it. A compliant Form 4 files <= 2 business days after the transaction (<= 4 calendar across a
# weekend) + <= 1 cron day + slack: beyond 7 is unambiguously not prompt filing-and-ingest, while
# the freeze cohort's thaw lags ran ~7-14d+ (the postmortem's thaw table). Deliberate consequence:
# an arm resting on facts backfilled at basket-add time also flags — same semantics (the arm's
# timing reflects when the platform LEARNED, not when the signal happened).
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
    """Per-accession ingest lag, calendar days: ``MIN(recorded_at)::date - MAX(valid_from)``.

    ``MIN(recorded_at)`` = when the platform FIRST learned the filing — a correction appended later
    must never shrink the lag. ``MAX(valid_from)`` = the filing's latest event date (a filing cannot
    predate its last txn — the conservative lag base). ``known_at`` threads the caller's
    read-consistency pin: a row recorded after it is not yet known, so it cannot contribute (an
    accession first learned after the pin is simply absent). Accessions with no fact rows are absent
    from the result — unknown, which degrades to un-flagged (B1 still covers the freeze cohort)."""
    if not accessions:
        return {}
    query = (
        "SELECT accession, (MIN(recorded_at)::date - MAX(valid_from)) AS lag_days "
        "FROM fact_insider_txn "
        "WHERE tenant_id = %(tenant_id)s AND accession = ANY(%(accessions)s)"
    )
    params: dict[str, object] = {"tenant_id": tenant_id, "accessions": accessions}
    if known_at is not None:
        query += " AND recorded_at <= %(known_at)s"
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
        notes.append(f"insider source ingested {thaw}d after its event date")

    return EpisodeProvenance(
        arm_ingest_fresh=fresh,
        freeze_era=freeze_era,
        thaw_lag_days=thaw,
        ingest_flagged=fresh is False or freeze_era or thawed_late,
        ingest_note=" · ".join(notes) if notes else None,
    )
