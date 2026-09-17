from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, NamedTuple
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from repositories.mappers import call_to_row, row_to_call


def append(
    conn: psycopg.Connection,
    card: CallCard,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    *,
    ingest_fresh: bool | None = None,
    ingest_errors: int | None = None,
    reconstructed: bool = False,
    config_hash: str | None = None,
    code_sha: str | None = None,
    run_kind: str | None = None,
) -> UUID:
    """Append an assembled CallCard to the write-only accountability log, under ``tenant_id`` (the call of
    record lands in the thesis's tenant). NOT the read path — the API recomputes the card live from facts.
    The caller owns the transaction (commit/rollback).

    ``tenant_id`` defaults to the demo tenant (a test/seed convenience, like the ingest fns); the production
    write path (``call_for_thesis``) always passes ``thesis.tenant_id`` explicitly, so the call of record can
    never land in the wrong tenant on that path.

    ``ingest_fresh`` / ``ingest_errors`` are the run's INGEST-HEALTH provenance (cron R2b, migration 0023):
    was every name's back-half ingest clean, and how many errored. PROVENANCE only — the scoring reads never
    branch on them; they are stamped SEPARATELY from the card (never inside it, or a stale->fresh flip would
    fake a change in ``_canonical``). ``None`` = not supplied (a manual/legacy append).

    ``reconstructed`` (migration 0042) marks a row written by ``pipeline.backfill`` — a reconstruction of a
    missed night, never the nightly record. Provenance too, off the card for the same reason; the cron and
    every manual append leave the default ``False``. Its ONE reader is the Scoreboard's record path, which
    EXCLUDES such rows (``latest_for_thesis(include_reconstructed=False)``) — a reconstructed row never
    defines an episode boundary.

    ``config_hash`` / ``code_sha`` / ``run_kind`` (migration 0044) are the RUN IDENTITY: which policy, which
    code, and which kind of run produced this row. Provenance too, off the card for the same reason, and the
    third stamp on the same keyword path — so the record can finally tell "the facts changed" from "the
    policy or the code changed" (MEASURED: the two largest arm bursts in the record were weekend MANUAL runs
    on deploy days). ``config_hash`` is ``domain.config.config_hash(cfg)`` for the cfg the assembler actually
    ran with; ``code_sha`` is ``Settings.image_sha`` — ``None`` when unknown, NEVER fabricated; ``run_kind``
    is ``cron`` | ``manual`` | ``backfill`` (a DB CHECK pins the set). All three ``None`` = a legacy/manual
    append that did not stamp.
    """
    row = call_to_row(card, tenant_id)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO calls
                   (tenant_id, thesis_id, asof, state, verdict, card, ingest_fresh, ingest_errors,
                    reconstructed, config_hash, code_sha, run_kind)
               VALUES (%(tenant_id)s, %(thesis_id)s, %(asof)s, %(state)s, %(verdict)s, %(card)s,
                       %(ingest_fresh)s, %(ingest_errors)s, %(reconstructed)s,
                       %(config_hash)s, %(code_sha)s, %(run_kind)s)
               RETURNING id""",
            {
                **row,
                "card": Json(row["card"]),
                "ingest_fresh": ingest_fresh,
                "ingest_errors": ingest_errors,
                "reconstructed": reconstructed,
                "config_hash": config_hash,
                "code_sha": code_sha,
                "run_kind": run_kind,
            },
        )
        return cur.fetchone()["id"]


def _canonical(card: CallCard) -> str:
    """A deterministic, ORDER-INDEPENDENT serialization for the change-compare.

    The CallCard has lists whose order is not load-bearing for "did the call change" (triggers, members,
    a member's own triggers, provenance) — a pure reorder must NOT read as a change, or `record_if_changed`
    would re-append every run. So this recursively sorts dict keys AND list elements, and rounds floats so
    jsonb/IEEE repr noise (e.g. a `Provenance.detail` number round-tripped through jsonb) can't fake a diff.
    (A genuinely meaningful reorder — e.g. a member rank change — is always accompanied by a changed field,
    so sorting cannot mask it.)"""

    def norm(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: norm(x[k]) for k in sorted(x)}
        if isinstance(x, list):
            return sorted(
                (norm(e) for e in x), key=lambda e: json.dumps(e, sort_keys=True, default=str)
            )
        if isinstance(x, float):
            return round(x, 9)
        return x

    return json.dumps(norm(card.model_dump(mode="json")), sort_keys=True, default=str)


def record_if_changed(
    conn: psycopg.Connection,
    card: CallCard,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    *,
    ingest_fresh: bool | None = None,
    ingest_errors: int | None = None,
    reconstructed: bool = False,
    config_hash: str | None = None,
    code_sha: str | None = None,
    run_kind: str | None = None,
) -> bool:
    """Append the call-of-record for ``(thesis, card.asof)`` ONLY if none exists for that as-of yet, or the
    latest logged one differs in substance (a canonical, order-independent compare). Returns ``True`` iff it
    appended. The caller owns the transaction.

    This is the cron's idempotent writer: a same-day re-run on UNCHANGED facts appends NOTHING (the table
    does not grow), while a GENUINE change (state / verdict / confidence / exit_by / provenance / members)
    appends EXACTLY ONE new versioned row (latest-append-per-asof wins on read). It is the only correct path
    because the ``calls`` log is immutable (the ``no_update`` trigger) and its ``(thesis_id, asof)`` index is
    non-unique — so an UPSERT is impossible; we read-compare-then-conditionally-append.

    ``ingest_fresh`` / ``ingest_errors`` (R2b) ride the WRITE only — they are NOT in ``_canonical``, so a
    stale->fresh flip on an otherwise-identical card does NOT append a spurious row (freshness is provenance
    of the run, not a change in the call). The stamp is the ingest health of the run that FIRST recorded this
    card version; a later re-run producing the identical card doesn't re-stamp (there's no new row).

    ``reconstructed`` rides the write the same way (off the card, out of the compare): the backfill passes
    ``True``; the compare still runs against the latest row for the as-of WHATEVER its marker, so a
    backfill re-run with the same pin appends zero rows, and a nightly row already on the night keeps a
    faithful reconstruction from appending a duplicate.

    ``config_hash`` / ``code_sha`` / ``run_kind`` (0044) ride the WRITE the same way, and that is the whole
    guarantee: they are NOT in ``_canonical``, so a dial change (or a deploy, or a Sunday manual run) on an
    otherwise-identical card appends NOTHING — the first dial edit does not re-record every thesis — and an
    unchanged config never suppresses a genuinely changed card. The stamp therefore belongs to the run that
    FIRST recorded this card version, exactly like ``ingest_fresh``; a later run producing the identical card
    leaves the earlier row's identity in place because there is no new row to stamp.
    """
    prior = next((c for c in latest_for_thesis(conn, card.thesis_id) if c.asof == card.asof), None)
    if prior is not None and _canonical(prior) == _canonical(card):
        return False
    append(
        conn,
        card,
        tenant_id,
        ingest_fresh=ingest_fresh,
        ingest_errors=ingest_errors,
        reconstructed=reconstructed,
        config_hash=config_hash,
        code_sha=code_sha,
        run_kind=run_kind,
    )
    return True


def record_edge(conn: psycopg.Connection) -> date | None:
    """The record's EDGE — the latest call-of-record ``asof`` across every thesis (all tenants: the cron
    walks them all), or ``None`` when the log is empty (the record has never begun). THE dead-man's
    signal for the admin freshness read: ``record_if_changed`` always appends the FIRST row at a new
    as-of (its prior-compare matches on the same ``asof``), so every completed daily pass advances this
    even on an all-quiet night — a stuck edge means the cron is not completing. Read-only.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(asof) AS edge FROM calls")
        return cur.fetchone()["edge"]


def record_first(conn: psycopg.Connection) -> date | None:
    """The record's FIRST as-of — ``MIN(asof)`` across every thesis (``record_edge``'s mirror), or
    ``None`` when the log is empty. The hole-aware freshness read's lower bound: a night before the
    record began is pre-history, never a "missed" run. Read-only."""
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(asof) AS first FROM calls")
        return cur.fetchone()["first"]


def recorded_asof_stamps(conn: psycopg.Connection, *, since: date) -> dict[date, datetime]:
    """``asof -> MAX(recorded_at)`` for every as-of with at least one call-of-record on or after ``since``
    (all tenants, every thesis — the cron walks them all). The hole-aware freshness read's input.

    Replaces the older ``recorded_asofs`` (a bare set of dates), because mere EXISTENCE of a row turned out
    to be too weak a test for "that night ran" (G2c): a pre-open Admin "Run daily now" at 09:15 writes a row
    for today's as-of from the PRIOR session's bars, and the hole check counted it, so a night whose 22:30
    pass then failed read as covered. The caller compares each stamp against that night's ``RUN_AT`` in
    market time and treats only a row recorded at/after it as covering the night.

    **MAX is the right aggregate**: ``MAX(recorded_at) >= cutoff`` is exactly "SOME row for this as-of was
    recorded at or after the cutoff", which is the question — a daytime row plus a proper night row makes the
    night covered, and the daytime row alone does not.

    Value-free by design (the repository discipline): it returns stamps and judges nothing. The cutoff needs
    ``RUN_AT`` and the market timezone — deploy config — which live in the router, so the comparison lives
    there too and this read stays testable without either. A ``reconstructed`` row (``pipeline.backfill``) is
    counted like any other, and its ``recorded_at`` is the reconstruction instant, so a backfilled night
    reads as covered — unchanged from before, and deliberate: freshness asks whether the log advanced.
    Read-only, bounded by ``since`` (the window's first scheduled day)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT asof, max(recorded_at) AS recorded_at FROM calls "
            "WHERE asof >= %s GROUP BY asof",
            (since,),
        )
        return {r["asof"]: r["recorded_at"] for r in cur.fetchall()}


def list_for_thesis(conn: psycopg.Connection, thesis_id: UUID) -> list[CallCard]:
    """Every logged card for a thesis, oldest first — the full append-only history (accountability
    inspection), never the serve path (the API recomputes from facts).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT card FROM calls WHERE thesis_id = %s ORDER BY seq", (thesis_id,))
        return [row_to_call(r) for r in cur.fetchall()]


def _reconstructed_clause(include_reconstructed: bool) -> str:
    """The one honest filter (migration 0042): with ``include_reconstructed=False`` a row written by
    ``pipeline.backfill`` is dropped BEFORE the per-as-of dedup, so on a night that carries both a
    nightly row and a later reconstruction the NIGHTLY row wins — never a silent swap to the
    reconstruction. Applied inside the WHERE, so DISTINCT ON never sees the excluded rows."""
    return "" if include_reconstructed else " AND NOT reconstructed"


def latest_for_thesis(
    conn: psycopg.Connection, thesis_id: UUID, *, include_reconstructed: bool = True
) -> list[CallCard]:
    """The call of record at each ``asof`` — one row per as-of, the latest append wins (a re-run after
    a fact correction supersedes the earlier row), newest as-of first. This is the deduped read a
    scoreboard wants; ``list_for_thesis`` keeps the full history. Never the serve path.

    ``include_reconstructed`` (default ``True``: every row, the behavior every existing caller relies
    on — the cron's transition compare, the backfill's prior-row report, ``record_if_changed``'s
    idempotency compare, the decision log's stance-at-logging). The Scoreboard's record path passes
    ``False``: a reconstructed row (``pipeline.backfill``, 0042) ran on today's basket for a thesis
    that may not have existed, so it never defines an episode boundary — it is reported, not scored.
    The filter runs BEFORE the dedup (see ``_reconstructed_clause``).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (asof) card FROM calls WHERE thesis_id = %s"
            + _reconstructed_clause(include_reconstructed)
            + " ORDER BY asof DESC, seq DESC",
            (thesis_id,),
        )
        return [row_to_call(r) for r in cur.fetchall()]


def reconstructed_asofs(conn: psycopg.Connection, *, upto: date) -> list[date]:
    """Every as-of on or before ``upto`` for which EVERY row is reconstructed (``pipeline.backfill``,
    0042) — no honest (nightly / manual) row shares the night — ledger-wide (all tenants, every thesis:
    the same scope as ``record_edge`` / ``recorded_asof_stamps``), ascending. The Scoreboard banner's list:
    the nights the record path has NOTHING honest for. A night that carries both a reconstruction and
    an honest row is NOT listed — the record path scored it from the honest row(s) (MEASURED on prod,
    2026-09-09: 8 reconstructed + 17 honest rows, 13 episodes arming honestly, yet the first cut named
    it "not scored"). Said once and quietly, never per row (with the filter in place a reconstructed
    row produces no ledger row). Capped at ``upto`` so a scrubbed-back view names only nights it can
    see. Read-only."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT asof FROM calls WHERE asof <= %s "
            "GROUP BY asof HAVING bool_and(reconstructed) ORDER BY asof",
            (upto,),
        )
        return [r["asof"] for r in cur.fetchall()]


def ingest_health_for_thesis(
    conn: psycopg.Connection, thesis_id: UUID, *, include_reconstructed: bool = True
) -> dict[date, tuple[bool | None, int | None]]:
    """The R2b ingest-health stamp of the WINNING row per as-of: asof -> ``(ingest_fresh,
    ingest_errors)`` (migration 0023). The IDENTICAL dedup as ``latest_for_thesis`` (latest append
    per as-of wins), so the stamp read here belongs to the same row as the scored card — the health
    of the run that FIRST recorded that card version. ``(None, None)`` = a legacy/manual append,
    never coerced to a judgment. The stamps live deliberately OFF the card (a freshness field IN it
    would fake a change in ``_canonical``), which is why this is a separate, narrow peer read — its
    only consumer is the Scoreboard's provenance layer; the as-of/scoring reads never branch on it.
    ``include_reconstructed`` mirrors ``latest_for_thesis`` exactly, and the Scoreboard passes the SAME
    value to both — so the stamp read here is the stamp of the row that was actually scored, never
    a reconstruction's NULL stamp standing in for a nightly row's.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (asof) asof, ingest_fresh, ingest_errors FROM calls "
            "WHERE thesis_id = %s"
            + _reconstructed_clause(include_reconstructed)
            + " ORDER BY asof DESC, seq DESC",
            (thesis_id,),
        )
        return {r["asof"]: (r["ingest_fresh"], r["ingest_errors"]) for r in cur.fetchall()}


class RunIdentity(NamedTuple):
    """Which policy, which code, and which kind of run wrote one call-of-record row (migration 0044).
    All three ``None`` = a legacy row that predates the stamp — never coerced to a judgment."""

    config_hash: str | None
    code_sha: str | None
    run_kind: str | None


def run_identity_for_thesis(
    conn: psycopg.Connection, thesis_id: UUID, *, include_reconstructed: bool = True
) -> dict[date, RunIdentity]:
    """The RUN IDENTITY of the WINNING row per as-of: asof -> ``(config_hash, code_sha, run_kind)``
    (migration 0044) — the policy + code + run-kind fingerprint of the run that recorded that card version.

    **MUST stay on the same winning-row rule as ``ingest_health_for_thesis``** — the identical
    ``DISTINCT ON (asof) ... WHERE thesis_id = %s [AND NOT reconstructed] ORDER BY asof DESC, seq DESC`` —
    and its ``include_reconstructed`` mirrors that read exactly. The Scoreboard passes the SAME value to
    ``latest_for_thesis``, ``ingest_health_for_thesis`` and this one, so all three describe ONE row; a
    divergence here would caption a scored card with a different run's fingerprint, which is worse than no
    caption at all. A test asserts the two reads pick the same ``seq``, not merely plausible values.

    A separate narrow peer read for the same reason ``ingest_health_for_thesis`` is one: the stamps live
    deliberately OFF the card (a field IN it would fake a change in ``_canonical``), so they cannot ride
    ``latest_for_thesis``'s ``CallCard``. Its only consumer is the Scoreboard's provenance layer; no scoring
    or as-of read branches on it. Read-only.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (asof) asof, config_hash, code_sha, run_kind FROM calls "
            "WHERE thesis_id = %s"
            + _reconstructed_clause(include_reconstructed)
            + " ORDER BY asof DESC, seq DESC",
            (thesis_id,),
        )
        return {
            r["asof"]: RunIdentity(r["config_hash"], r["code_sha"], r["run_kind"])
            for r in cur.fetchall()
        }
