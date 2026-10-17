"""§2.2 fundamentals backfill CLI — pull each basket member's quarterly REVENUE series + the Band 03 S4
SHARES-OUTSTANDING series (SEC companyfacts — both ride the same single pull per name) into
``fact_fundamentals`` with honest historical knowability.

companyfacts returns the FULL multi-year history in one pull, each period carrying its own ``filed``/``accn``,
so a single run reconstructs every historical quarter stamped at its TRUE filing date (R17 — never today).
This is the multi-year backfill the acceleration backtest needs. Incremental + idempotent: a re-run appends
ZERO rows for already-stored (security, metric, period_end, filed) versions (count-the-table guarded).

    python -m pipeline.ingest_fundamentals                 # bare = every (non-archived) thesis's basket
    python -m pipeline.ingest_fundamentals --thesis <uuid> # one thesis
    python -m pipeline.ingest_fundamentals --no-live        # cache-only (no network)

Fail-visible: each name runs in its own try; a failure (e.g. companyfacts 404 for a name data.sec.gov
doesn't serve) is captured into that name's result and the run continues. A security appearing in two
baskets is pulled ONCE (companyfacts is per-CIK). Advisory back-half data — never an operator-ratified fact,
but a DETERMINISTIC parse (#3): the number is XBRL, never model-sourced.

Q17 — THE PULL IS GATED ON NEW FILINGS. This leg builds a RECURRING client, so a cached companyfacts is
re-pulled only when the company's submissions index shows a new XBRL-bearing filing since the last pull, or
after the weekly backstop (``ingest/edgar/companyfacts_gate.py``); a known 404 is re-asked on the same
terms (``known_absent`` on the result, counted quietly instead of an ERROR line every night). A name the gate
leaves alone still RE-PARSES its cached document and appends any version not yet stored, so nothing already
on disk is skipped.

THE NIGHTLY PATH RUNS PER THESIS (Q17 zero-lag): ``pipeline.daily.run_daily`` calls
``ingest_fundamentals_for_securities`` for each thesis right AFTER that thesis's ``ingest_thesis`` re-pulled
its members' submissions indexes, and BEFORE its call is assembled — so the gate reads TONIGHT's index and a
quarter filed today reaches tonight's call. One ``PassMemo`` spans the pass: a security already handled is
skipped, and a company (CIK) is pulled at most ONCE (a second share class reads the copy this pass just
wrote; a failed company repeats its error without a second request) — the 24 h grace would otherwise make a
name held by two theses due twice in one night.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from db.session import connect
from domain.security import Security
from ingest.edgar.client import RECURRING_CACHE_TTL_S, CachedNotFound, EdgarClient
from ingest.fundamentals import ingest_fundamentals_for_security
from repositories import thesis_repo
from securities import master


@dataclass
class NameResult:
    """Per-name outcome — what was appended, what was already stored (idempotency), and any captured error.
    ``known_absent`` marks an error that is the NEGATIVE CACHE answering (X8): data.sec.gov already said 404
    for this company and nothing it filed since says to re-ask — a known "has no companyfacts", not news.
    """

    ticker: str | None
    security_id: UUID
    appended: int = 0
    skipped: int = 0
    error: str | None = None
    known_absent: bool = False


@dataclass
class PassMemo:
    """What one pass has already done (Q17 zero-lag): the securities it handled, and per company (CIK) the
    outcome of its ONE pull — ``None`` = pulled (the cache now holds tonight's copy), or ``(error,
    known_absent)`` to repeat for a sibling security without asking again."""

    securities: set[UUID] = field(default_factory=set)
    companies: dict[int, tuple[str, bool] | None] = field(default_factory=dict)


def ingest_fundamentals_for_securities(
    conn: psycopg.Connection,
    securities: Iterable[Security],
    *,
    client: EdgarClient,
    tenant_id: UUID,
    memo: PassMemo | None = None,
) -> list[NameResult]:
    """Refresh + store the fundamentals series for each security, per-name fail-visible and committing
    per name (the caller owns nothing). ``memo`` makes a whole pass pull each company at most once (see the
    module docstring); a security the memo already holds is skipped (its facts were stored this pass).
    """
    memo = memo if memo is not None else PassMemo()
    results: list[NameResult] = []
    for sec in securities:
        if sec.id in memo.securities:
            continue  # already handled this pass (a name held by two theses)
        memo.securities.add(sec.id)
        cik = int(sec.cik) if sec.cik else None
        reader = client
        if cik is not None and cik in memo.companies:
            failed = memo.companies[cik]
            if failed is not None:  # this company already failed this pass — never a second request
                results.append(
                    NameResult(sec.ticker, sec.id, error=failed[0], known_absent=failed[1])
                )
                continue
            # pulled earlier this pass: read the copy just written, never the network again
            reader = EdgarClient(cache_dir=client.cache_dir, allow_live=False)
        try:
            res = ingest_fundamentals_for_security(conn, sec, client=reader, tenant_id=tenant_id)
            conn.commit()
            if cik is not None:
                memo.companies.setdefault(cik, None)
            results.append(
                NameResult(sec.ticker, sec.id, appended=res.appended, skipped=res.skipped)
            )
        except Exception as e:  # noqa: BLE001 — fail-visible: capture, roll back, keep going
            conn.rollback()
            absent = isinstance(e, CachedNotFound)
            if cik is not None:
                memo.companies.setdefault(cik, (str(e), absent))
            results.append(NameResult(sec.ticker, sec.id, error=str(e), known_absent=absent))
    return results


def ingest_fundamentals(
    conn: psycopg.Connection,
    *,
    thesis_id: UUID | None = None,
    allow_live: bool = True,
    user_agent: str | None = None,
    edgar_client: EdgarClient | None = None,
) -> list[NameResult]:
    """Ingest the quarterly revenue series for each resolved basket member of ``thesis_id`` (or, bare, EVERY
    non-archived thesis). Securities are de-duplicated across baskets (one companyfacts pull per CIK).
    Per-name fail-visible + committing on success; the caller owns nothing (this commits per name).
    """
    # Q17 (2026-10-17) — this leg is now a RECURRING client like every other nightly leg (it used to be the
    # one deliberate exception, keeping the 12h interactive TTL). That did not save bandwidth: by 22:30 the
    # 12h clock had always expired, so every basket name's companyfacts was re-pulled every night (MEASURED
    # 680 documents, 1.18 GB) to pick up the ~38 that had filed something. On a recurring client the
    # ``companyfacts/`` key class is FILING-GATED instead (``ingest/edgar/companyfacts_gate.py``): re-pulled
    # when uncached, when the cached submissions index shows a new XBRL-bearing filing since the last pull,
    # or after a weekly backstop. The gate lives on the key class in the client, so nothing here threads a
    # flag; the cost of a client built WITHOUT the recurring dial is the old bandwidth, never staleness.
    # The CLI (``python -m pipeline.ingest_fundamentals``) is this same leg run by hand, so it is gated too;
    # a name not cached yet (a new basket member) is always pulled.
    client = edgar_client or EdgarClient(
        allow_live=allow_live, user_agent=user_agent, cache_ttl_s=RECURRING_CACHE_TTL_S
    )
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis {thesis_id} not found")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)

    memo = PassMemo()
    results: list[NameResult] = []
    for thesis in theses:
        secs: list[Security] = []
        for m in thesis.basket:
            if m.security_id is None or m.security_id in memo.securities:
                continue  # unresolved placement, or a name already pulled from another basket
            sec = master.get(conn, m.security_id, tenant_id=thesis.tenant_id)
            if sec is None:
                memo.securities.add(m.security_id)
                results.append(NameResult(m.ticker, m.security_id, error="not in tenant master"))
                continue
            secs.append(sec)
        results += ingest_fundamentals_for_securities(
            conn, secs, client=client, tenant_id=thesis.tenant_id, memo=memo
        )
    return results


def _report(results: list[NameResult]) -> int:
    """Print a per-name summary; return the number that errored (the process exit signal). A known-absent
    name (the X8 negative cache) still counts as errored here — this CLI reports every name it walked.
    """
    total_new = sum(r.appended for r in results)
    errored = [r for r in results if r.error]
    for r in results:
        if r.error:
            print(f"  {r.ticker or r.security_id}: ERROR: {r.error}")
        else:
            # skips (already-stored versions) surface only when nonzero — loudness marks the exception
            sk = f", {r.skipped} already stored" if r.skipped else ""
            print(f"  {r.ticker or r.security_id}: +{r.appended} quarters{sk}")
    print(f"done: {len(results)} names, +{total_new} quarterly facts, {len(errored)} errored")
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill the §2.2 quarterly revenue series (companyfacts) into fact_fundamentals."
    )
    p.add_argument("--thesis", default=None, help="thesis id (uuid); omit to run EVERY basket")
    p.add_argument(
        "--no-live",
        action="store_true",
        help="cache-only (no network); else live (needs ALPHADECK_USER_AGENT)",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        results = ingest_fundamentals(
            conn,
            thesis_id=UUID(args.thesis) if args.thesis else None,
            allow_live=not args.no_live,
        )
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a wrapper, non-silently


if __name__ == "__main__":
    main()
