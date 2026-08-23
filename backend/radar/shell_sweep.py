"""The SPAC shell sweep — facts-only blank-check enrichment for the master's un-enriched SPAC-
structured names.

The coverage gap it closes: the triage TYPE=blank-check flag and the SPAC Radar both classify via
``sector == 'Blank Checks'`` (SIC 6770's verbatim EDGAR ``sicDescription``, exact-string), but a
SPAC-structured CIK that is in the master with a NULL ``sector`` never flags and never joins the
radar's known-shell set — so its 8-K/proxy/25 events are dropped (8-K is a KNOWN-ONLY form; the
radar's lazy accretion only resolves merger-specific filers). The sweep pulls those CIKs' SEC SIC
so the genuinely-6770 ones flag correctly, on a recurring, bounded, incremental basis.

THE GOVERNING INVARIANT (#3, facts-only): the units-``U`` / "Acquisition Corp" pattern is used
ONLY to select which CIKs to fetch — the SEC ``sicDescription`` is the SOLE authority for
shell-or-not. The sweep enriches (stores the SEC SIC verbatim, via the existing
``enrich_for_ciks`` -> ``master.enrich`` writer); the existing exact-string ``spacClass``
classifier does the flagging. Nothing name/units-derived is ever stored or surfaced as a
classification: a 6770 candidate flags; an operating "...Acquisition Corp" (the FACT II shape,
SIC 3728) enriches to its real sector and correctly never flags. Liberal selector (#9 recall —
a false-positive candidate costs one submissions fetch, never a wrong fact), factual gate.

Deterministic end-to-end: no LLM anywhere. Fail-visible: a per-CIK fetch/parse/write fault is
recorded in the result's ``errors`` and the CIK stays un-enriched (re-selected next run), never
silently dropped (#9). Never imports ``calls/`` — identity is display/selection context, not a
trigger (the AST guard in ``tests/radar/test_shell_sweep.py`` pins this structurally).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.client import EdgarClient
from radar import repo
from securities import master
from workbench.enrichment import enrich_for_ciks


@dataclass
class ShellSweepResult:
    """One sweep pass's receipt. Loudness marks the exception: ``admitted`` (the coverage gap
    closing) and ``flipped`` (a de-SPAC caught — it stops flagging) are CIK lists; the common
    quiet outcomes (``other`` — an operating "...Acquisition Corp" enriched to its real SIC;
    ``reconfirmed`` — a re-swept shell still 6770) are counts."""

    candidates: int = 0  # selected this run, pre-cap (un-enriched + known shells when reenrich)
    attempted: int = 0  # after the cap
    enriched: int = 0
    admitted: list[str] = field(default_factory=list)  # NULL sector -> Blank Checks
    flipped: list[str] = field(default_factory=list)  # Blank Checks -> other (reenrich only)
    other: int = 0  # NULL sector -> a non-6770 SIC (the FACT II shape; correct + quiet)
    reconfirmed: int = 0  # Blank Checks -> still Blank Checks (reenrich; quiet)
    skipped: int = 0  # per-CIK faults; == len(errors)
    remaining: int = 0  # candidates beyond the cap (self-continues next run)
    errors: list[str] = field(default_factory=list)
    edgar_fetches: int = 0

    @property
    def summary(self) -> str:
        parts = [
            f"{self.candidates} candidates",
            f"{self.enriched} enriched (+{len(self.admitted)} Blank Checks, {self.other} other)",
            f"{self.edgar_fetches} EDGAR fetches",
        ]
        if self.reconfirmed:
            parts.append(f"{self.reconfirmed} reconfirmed")
        if self.flipped:
            parts.append(f"{len(self.flipped)} de-SPAC'd (no longer flag)")
        if self.remaining:
            parts.append(f"remaining={self.remaining} (capped — self-continues next run)")
        if self.errors:
            parts.append(f"{len(self.errors)} ERRORS")
        return " · ".join(parts)


def select_unenriched_candidates(
    conn: psycopg.Connection, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> list[str]:
    """CIKs that are SPAC-STRUCTURED (a units-``U`` ticker sibling, or a name matching
    "acquisition corp") and UN-ENRICHED (no non-NULL ``sector`` on ANY of the CIK's rows) — the
    fetch selector, never a classifier. Liberal by design (#9): precision comes from the SEC SIC
    downstream. Incremental by construction: once a CIK is enriched (to 6770 or anything else) it
    drops out, so the first run does the catch-up and steady state is a handful a night. Ordered
    by CIK so the cap defers a deterministic tail.

    (No ``valid_to`` filter, deliberately: nothing ever sets it on the master — identity rows
    UPDATE in place, and every sibling master read omits it too.)"""
    with conn.cursor() as cur:
        cur.execute(
            """WITH cur AS (
                   SELECT cik, ticker, name, sector FROM security_master
                   WHERE tenant_id = %s AND cik IS NOT NULL
               ),
               units AS (
                   SELECT DISTINCT m1.cik FROM cur m1
                   JOIN cur m2 ON m2.cik = m1.cik AND m2.ticker = m1.ticker || 'U'
                   WHERE m1.ticker IS NOT NULL
               ),
               acq  AS (SELECT DISTINCT cik FROM cur WHERE name ~* 'acquisition corp'),
               cand AS (SELECT cik FROM units UNION SELECT cik FROM acq),
               sect AS (SELECT cik, max(sector) AS sector FROM cur GROUP BY cik)
               SELECT c.cik FROM cand c JOIN sect s USING (cik)
               WHERE s.sector IS NULL
               ORDER BY c.cik""",
            (tenant_id,),
        )
        return [row["cik"] for row in cur.fetchall()]


def select_known_shells_lru(
    conn: psycopg.Connection, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> list[str]:
    """The re-enrich pool: every CIK currently enriched ``Blank Checks`` (the radar's known-shell
    set), LEAST-RECENTLY-ENRICHED FIRST. The ordering is load-bearing: re-enrich is NOT
    incremental (a re-confirmed shell stays a candidate), so a capped pass ordered any other way
    would redo the same first-N forever and starve the tail — LRU makes a capped weekly re-sweep
    round-robin the set instead. ``NULLS FIRST`` re-checks seeded-but-never-enriched rows soonest.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT cik FROM (
                   SELECT cik, min(enriched_at) AS oldest
                   FROM security_master
                   WHERE tenant_id = %s AND sector = %s AND cik IS NOT NULL
                   GROUP BY cik
               ) shells ORDER BY oldest ASC NULLS FIRST, cik""",
            (tenant_id, repo.BLANK_CHECKS),
        )
        return [row["cik"] for row in cur.fetchall()]


def run_shell_sweep(
    conn: psycopg.Connection,
    *,
    allow_live: bool = True,
    cap: int = 300,
    reenrich: bool = False,
    user_agent: str | None = None,
    edgar_client: EdgarClient | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> ShellSweepResult:
    """One sweep pass: select -> enrich (the existing ``enrich_for_ciks`` writer, per-CIK
    isolated) -> classify the outcome for the receipt. ``reenrich=True`` ADDS the current
    known-shell set (LRU-first) after the un-enriched candidates — the un-enriched coverage gap
    is never starved behind re-checks — so a de-SPAC'd shell's SIC flip is caught and it stops
    flagging. ``cap`` bounds the run (the cost thread: bounded, shrinking, logged — a capped run
    reports ``remaining`` and self-continues next run, since enriched CIKs drop out of the
    selector and the re-enrich pool rotates by ``enriched_at``).

    Commits per CIK (inside ``enrich_for_ciks``) — a crashed catch-up keeps its progress, and a
    same-night consumer (the radar's ``known_shell_ciks`` read) sees completed enrichments. The
    caller may pass its own ``edgar_client`` (tests: a fixture-cache client with
    ``allow_live=False``). Idempotent: ``master.enrich`` UPDATEs in place, so a re-run grows no
    table (count-the-table safe)."""
    client = edgar_client or EdgarClient(allow_live=allow_live, user_agent=user_agent)
    result = ShellSweepResult()

    unenriched = select_unenriched_candidates(conn, tenant_id=tenant_id)
    shell_pool = select_known_shells_lru(conn, tenant_id=tenant_id) if reenrich else []
    shell_set = set(shell_pool)
    # disjoint by construction (NULL vs non-NULL sector); dedup defensively, order preserved
    cands: list[str] = []
    seen: set[str] = set()
    for cik in [*unenriched, *shell_pool]:
        if cik not in seen:
            seen.add(cik)
            cands.append(cik)

    result.candidates = len(cands)
    to_sweep = cands[: max(cap, 0)]
    result.attempted = len(to_sweep)
    result.remaining = len(cands) - len(to_sweep)

    # canonical row per CIK (identity is company-level — one submissions doc enriches the
    # ``is_primary`` row, the row every CIK->id read resolves to; the enrich_identity pattern)
    sid_by_cik = master.ids_for_ciks(conn, to_sweep, tenant_id=tenant_id)

    for cik in to_sweep:
        sid = sid_by_cik.get(cik)
        if sid is None:  # can't happen for selector-sourced CIKs — loud, never silent (#9)
            result.skipped += 1
            result.errors.append(f"CIK {cik}: no canonical master id resolved")
            continue
        # one-entry map per candidate: the shared writer's semantics unchanged (genuine-doc
        # guard, commit-per-CIK), but the outcome is attributable to THIS cik for the receipt
        counts = enrich_for_ciks(conn, client, {cik: sid}, tenant_id=tenant_id)
        if counts["enriched"]:
            result.enriched += 1
            sec = master.get(conn, sid, tenant_id=tenant_id)
            now_blank = sec is not None and sec.sector == repo.BLANK_CHECKS
            if cik in shell_set:  # was Blank Checks before this run (the re-enrich pool)
                if now_blank:
                    result.reconfirmed += 1
                else:
                    result.flipped.append(cik)
            else:  # was un-enriched (NULL sector)
                if now_blank:
                    result.admitted.append(cik)
                else:
                    result.other += 1
        else:
            result.skipped += 1
            result.errors.append(
                f"CIK {cik}: enrich skipped (fetch/parse/write fault — "
                "left un-enriched, retried next run)"
            )

    result.edgar_fetches = client.live_fetches
    return result
