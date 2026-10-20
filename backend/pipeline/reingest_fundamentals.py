"""``pipeline.reingest_fundamentals`` — re-parse every active basket company's companyfacts under the CURRENT
revenue-label rule and append the versions the old first-present rule never stored.

WHAT IT REPAIRS. Until 2026-10-20 the fundamentals ingest kept the FIRST revenue label present
(``us-gaap:Revenues`` before the two ``RevenueFromContractWithCustomer*`` tags). Most filers moved off
``Revenues`` around ASC 606 (~2018), so a company kept only its dead series: MEASURED on prod (read-only,
2026-10-20), 163 of 495 active basket companies with revenue had a revenue series ending more than a year
before their share counts (AMD's stopped at 2017-09-30, HON's in 2012, NEE's in 2013), and the Key-1
``revenue_acceleration`` trigger could not fire on their real recent quarters. ``ingest.fundamentals
.select_revenue`` now picks the company's CURRENT label.

THE NIGHTLY WOULD DO THIS ON ITS OWN. Its fundamentals leg re-parses every basket company's CACHED
companyfacts each pass (the Q17 gate decides only whether to re-PULL), so the first nightly after the fix
deploys appends exactly these versions. This CLI is the same parse run deliberately: a dry run to see every
company's change first, then an ``--apply`` at a moment the operator chooses (before the Edition 1 re-run),
under the lock, counted.

WHAT IT WRITES — exactly what the nightly's own parse would (``ingest.fundamentals.plan_versions``, the writer
``store_quarters`` uses), never anything else:

- the current label's quarters not yet stored, each stamped ``valid_from = recorded_at = its filing date``
  (R17, the stamp every one of the table's rows carries — never ``now()``; see "Why the filing stamp");
- where a stored old-label number sits on a filing the current label reports differently, the current
  label's number as a version of that same filing, one microsecond later (it is what the read picks there)
  — unless the stored number is one the current label itself reports for that period later (kept, so it
  stays dated at the filing that first made it public);
- any missing share-count version the same parse yields (normally none: the nightly keeps those current).

Never a row the data-quality screen rejects (a fiscal-year total under a quarter context; a derived Q4 across
a restatement — ``ingest.fundamentals.ScreenedRow``): each is counted and PRINTED, dry run and apply alike.

Nothing is updated or deleted (the table is append-only). The trigger's rule is untouched; it reads the
right numbers.

WHY THE FILING STAMP, NOT ``now()``. The fundamentals table stamps both clocks at the filing date by design
(migration 0036, R17), and the first-knowability read (D3a, ``db.bitemporal.version_knowable_date``) dates a
version at ``max(valid_from, the market date of recorded_at)``. Stamped ``now()``, every re-ingested quarter
— years of history — would read as first knowable TODAY, and ``revenue_acceleration`` would cite an
inflection years old as fresh for 180 days. MEASURED on the dev copy (2026-10-20, the 647 active companies
with cached companyfacts): with the filing stamp, 97 carry a live event today (71 before; 27 gained, 1 lost —
KOPN's, a fake the subset guard removed); stamped ``now()``, 212 would (141 gained — 117 of them citing a
quarter the filing stamp does not fire on today: phantoms). And the nightly
writes these same rows with the filing stamp, so the CLI must stamp them identically or the store would
depend on which ran first.

SAFETY (the ``repair_benchmark_bars`` / ``backfill`` shape):

- **Dry-run is the DEFAULT.** Its connection is READ ONLY and it never goes to the network: a company whose
  companyfacts is not cached is reported (``--apply`` fetches it) and a known 404 is reported as such. It
  prints, per company: the label chosen (and the old rule's), the versions to add, the stored revenue series
  end and the series end after, and every screened row (reason, quarter, filing, value vs its reference).
- **The cache first.** ``--apply`` reads each company's companyfacts from the EDGAR cache where it is cached
  (whatever its age — the nightly's Q17 gate keeps it current, and this CLI never refreshes it) and fetches
  only an uncached one, through the ordinary client (declared User-Agent, rate limit). A company with a cached
  404 marker is left to the nightly's gate. ``--no-live`` makes ``--apply`` cache-only too.
- **One transaction, counted.** ``--apply`` holds the call-of-record lock (``pipeline/run_lock.py``) for the
  whole run — exit 75 when a nightly pass or a backfill holds it — counts ``fact_fundamentals`` before and
  after inside ONE transaction, and commits only when the difference equals the planned versions.
- **Idempotent.** A second run plans and appends zero (count the table).
- **Provenance.** ``--apply`` writes one JSON under ``data/repairs/`` (fail-open).
- Run ``--apply`` on prod only after a labeled backup, from the deployed image, outside 21:30-00:00 ET.

    python -m pipeline.reingest_fundamentals                      # dry run, every active basket company
    python -m pipeline.reingest_fundamentals --thesis <uuid>      # one thesis's basket
    python -m pipeline.reingest_fundamentals --ticker AMD,HON     # named companies only
    python -m pipeline.reingest_fundamentals --apply              # append (fetches only uncached documents)
    python -m pipeline.reingest_fundamentals --apply --no-live    # append from the cache alone
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import connect
from domain.security import Security
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import REVENUE_CONCEPTS, companyfacts_url
from ingest.fundamentals import (
    REVENUE_METRIC,
    PlannedVersion,
    ScreenedRow,
    append_planned,
    extract_share_points,
    held_versions,
    plan_versions,
    select_revenue,
)
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock
from repositories import thesis_repo
from securities import master

_log = logging.getLogger("alphadeck.reingest_fundamentals")

RULE = "fundamentals-current-revenue-label"

# Where a company's companyfacts came from on this run.
CACHED = "cached"
FETCHED = "fetched"
NOT_CACHED = "not_cached"  # dry run / --no-live: not on disk, not fetched
KNOWN_404 = "known_404"  # a cached 404 marker: data.sec.gov serves this company no companyfacts
FAILED = "failed"  # the fetch raised
NO_CIK = "no_cik"

# Provenance lives beside the other repairs' (the repo's gitignored data/ == the container's /data).
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "repairs"


@dataclass
class CompanyPlan:
    """One security: where its document came from, the labels, and the versions to append."""

    ticker: str | None
    security_id: UUID
    tenant_id: UUID
    cik: str | None
    source: str
    error: str | None = None
    label: str | None = None  # the current label (``select_revenue``)
    old_label: str | None = None  # the first-present rule's label (what was stored)
    labels: dict[str, tuple[int, date, date]] = field(default_factory=dict)
    old_end: date | None = None  # the stored revenue series' last period end
    new_end: date | None = None  # ...after these versions
    planned: list[PlannedVersion] = field(default_factory=list)
    # the current label's rows the data-quality screen kept out of the series (never stored)
    screened: list[ScreenedRow] = field(default_factory=list)

    def count(self, *, revenue: bool, supersedes: bool | None = None) -> int:
        return sum(
            1
            for v in self.planned
            if (v.point.metric_key == REVENUE_METRIC) == revenue
            and (supersedes is None or (v.supersedes_value is not None) == supersedes)
        )


@dataclass
class ReingestResult:
    applied: bool
    companies: list[CompanyPlan] = field(default_factory=list)
    rows_before: int | None = None
    rows_after: int | None = None
    appended: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def planned(self) -> int:
        return sum(len(c.planned) for c in self.companies)


# The tags the OLD rule knew: the first three ``REVENUE_CONCEPTS`` (the two appended on 2026-10-20 were never
# read before, so nothing was ever stored from them).
_OLD_RULE_CONCEPTS = REVENUE_CONCEPTS[:3]


def first_present_label(companyfacts: dict[str, Any]) -> str | None:
    """The label the OLD rule stored — the first of its three tags with any stampable USD duration row,
    whether or not it yields a quarter. Reported beside the new choice, never used to store."""
    facts = companyfacts.get("facts", {})
    for tax, concept in _OLD_RULE_CONCEPTS:
        rows = (facts.get(tax, {}).get(concept) or {}).get("units", {}).get("USD", [])
        if any(
            r.get("start")
            and r.get("end")
            and r.get("val") is not None
            and r.get("filed")
            and r.get("accn")
            for r in rows
        ):
            return concept
    return None


def active_securities(
    conn: psycopg.Connection, *, thesis_id: UUID | None = None, tickers: Sequence[str] = ()
) -> list[Security]:
    """Every resolved basket member of every non-archived thesis (or one thesis) — the nightly's scope
    (``pipeline.ingest_fundamentals``) — de-duplicated by security; ``tickers`` narrows it."""
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis {thesis_id} not found")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)
    wanted = {t.upper() for t in tickers}
    seen: set[UUID] = set()
    out: list[Security] = []
    for thesis in theses:
        for m in thesis.basket:
            if m.security_id is None or m.security_id in seen:
                continue
            seen.add(m.security_id)
            sec = master.get(conn, m.security_id, tenant_id=thesis.tenant_id)
            if sec is None or (wanted and (sec.ticker or "").upper() not in wanted):
                continue
            out.append(sec)
    return out


def _cache_key(cik: str) -> str:
    return f"companyfacts/CIK{int(cik):010d}.json"


def load_companyfacts(
    client: EdgarClient, cik: str, *, fetch: bool
) -> tuple[dict[str, Any] | None, str, str | None]:
    """(document, source, error). The cache wins whatever its age; an uncached document is fetched only
    when ``fetch`` (and never one with a cached 404 marker — the nightly's gate re-asks those)."""
    key = _cache_key(cik)
    path = client.cache_dir / key
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), CACHED, None
    if path.with_name(path.name + ".404").exists():
        return None, KNOWN_404, "data.sec.gov returned 404 (cached marker)"
    if not fetch:
        return None, NOT_CACHED, None
    try:
        return client.get_json(companyfacts_url(cik), key), FETCHED, None
    except CacheMiss as e:
        return None, NOT_CACHED, str(e)
    except Exception as e:  # noqa: BLE001 — fail-visible: reported, the company is skipped
        return None, FAILED, f"{type(e).__name__}: {e}"


def plan_company(
    sec: Security,
    doc: dict[str, Any],
    held: dict,
    *,
    source: str,
) -> CompanyPlan:
    """PURE: one company's plan from its companyfacts and what the store holds for it."""
    sel = select_revenue(doc)
    planned, _skipped = plan_versions(held, sel.points + extract_share_points(doc))
    held_ends = [pe for (m, pe, _f) in held if m == REVENUE_METRIC]
    new_ends = held_ends + [
        v.point.period_end for v in planned if v.point.metric_key == REVENUE_METRIC
    ]
    return CompanyPlan(
        ticker=sec.ticker,
        security_id=sec.id,
        tenant_id=sec.tenant_id,
        cik=sec.cik,
        source=source,
        label=sel.concept,
        old_label=first_present_label(doc),
        labels=sel.labels,
        screened=sel.screened,
        old_end=max(held_ends, default=None),
        new_end=max(new_ends, default=None),
        planned=planned,
    )


def _count_rows(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_fundamentals")
        return cur.fetchone()["n"]


def reingest_fundamentals(
    conn: psycopg.Connection,
    *,
    apply: bool = False,
    fetch: bool = False,
    client: EdgarClient | None = None,
    thesis_id: UUID | None = None,
    tickers: Sequence[str] = (),
) -> ReingestResult:
    """Plan (and with ``apply``, append) the current-label versions for every active basket company.

    Documents are loaded BEFORE the write transaction (no transaction stays open across a fetch); the plan
    and the appends then run in ONE transaction that counts the table before and after and commits only
    when the difference equals the plan (a mismatch rolls back and raises). Everything else rolls back. The
    caller owns the connection (a dry run should hand in a READ ONLY one) and, for ``apply``, the lock.
    """
    started = datetime.now(timezone.utc)
    result = ReingestResult(applied=apply, started_at=started)
    # cache-first whatever the age (an infinite TTL): this CLI never refreshes a cached document
    client = client or EdgarClient(allow_live=fetch, cache_ttl_s=math.inf)
    secs = active_securities(conn, thesis_id=thesis_id, tickers=tickers)
    conn.rollback()  # no transaction stays open across the document loads

    docs: dict[str, tuple[dict[str, Any] | None, str, str | None]] = {}
    for sec in secs:
        if sec.cik and sec.cik not in docs:
            docs[sec.cik] = load_companyfacts(client, sec.cik, fetch=fetch)

    try:
        result.rows_before = _count_rows(conn)
        for sec in secs:
            if not sec.cik:
                result.companies.append(
                    CompanyPlan(sec.ticker, sec.id, sec.tenant_id, None, NO_CIK)
                )
                continue
            doc, source, err = docs[sec.cik]
            if doc is None:
                result.companies.append(
                    CompanyPlan(sec.ticker, sec.id, sec.tenant_id, sec.cik, source, error=err)
                )
                continue
            held = held_versions(conn, sec.id, sec.tenant_id)
            result.companies.append(plan_company(sec, doc, held, source=source))
        if apply and result.planned:
            for c in result.companies:
                if c.planned:
                    result.appended += append_planned(
                        conn, c.security_id, c.planned, tenant_id=c.tenant_id
                    )
            result.rows_after = _count_rows(conn)
            if (
                result.appended != result.planned
                or result.rows_after - result.rows_before != result.planned
            ):
                raise RuntimeError(
                    f"row-count mismatch (planned {result.planned}, wrote {result.appended}, table "
                    f"{result.rows_before} -> {result.rows_after}) — ROLLED BACK, nothing written"
                )
            conn.commit()
        else:
            result.rows_after = result.rows_before
            conn.rollback()
    except BaseException:
        conn.rollback()
        result.appended = 0
        raise
    result.finished_at = datetime.now(timezone.utc)
    return result


# --- provenance + report -----------------------------------------------------------------------------------


def _iso(x: date | datetime | None) -> str | None:
    return x.isoformat() if x is not None else None


def build_payload(result: ReingestResult) -> dict[str, Any]:
    """The provenance payload — PURE (no I/O)."""
    return {
        "kind": "reingest_fundamentals",
        "rule": f"{RULE}@{get_settings().image_sha or 'unknown'}",
        "applied": result.applied,
        "started_at": _iso(result.started_at),
        "finished_at": _iso(result.finished_at),
        "rows_before": result.rows_before,
        "rows_after": result.rows_after,
        "appended": result.appended,
        "companies": [
            {
                "ticker": c.ticker,
                "security_id": str(c.security_id),
                "cik": c.cik,
                "source": c.source,
                "error": c.error,
                "label": c.label,
                "old_label": c.old_label,
                "old_end": _iso(c.old_end),
                "new_end": _iso(c.new_end),
                "revenue_new_filings": c.count(revenue=True, supersedes=False),
                "revenue_supersessions": c.count(revenue=True, supersedes=True),
                "share_versions": c.count(revenue=False),
                "screened": [
                    {
                        "reason": x.reason,
                        "period_end": _iso(x.period_end),
                        "filed": _iso(x.filed),
                        "value": x.value,
                        "reference": x.reference,
                        "accession": x.accession,
                    }
                    for x in c.screened
                ],
                "superseded": [
                    {
                        "period_end": _iso(v.point.period_end),
                        "filed": _iso(v.point.filed),
                        "old": v.supersedes_value,
                        "new": v.point.value,
                        "accession": v.point.accession,
                    }
                    for v in c.planned
                    if v.supersedes_value is not None
                ],
            }
            for c in result.companies
            if c.planned or c.screened or c.error or c.source not in (CACHED, FETCHED)
        ],
    }


def write_payload(payload: dict[str, Any], base_dir: Path | None = None) -> Path | None:
    """Write the provenance JSON to ``<base>/<utc-timestamp>-reingest-fundamentals.json``; return the path,
    or ``None`` fail-open (the committed appends ARE the repair; a provenance fault is logged, never raised).
    """
    try:
        run_dir = base_dir or _DEFAULT_LOG_DIR
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = run_dir / f"{stamp}-reingest-fundamentals.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract
        _log.exception("reingest provenance write failed (fail-open — the appends are committed)")
        return None


def _short(label: str | None) -> str:
    if label is None:
        return "—"
    return {
        "Revenues": "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax": "RevenueFromContract…ExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax": "RevenueFromContract…IncludingAssessedTax",
        "RegulatedAndUnregulatedOperatingRevenue": "RegulatedAndUnregulatedOperatingRevenue",
        "RevenuesNetOfInterestExpense": "RevenuesNetOfInterestExpense",
    }.get(label, label)


def report(result: ReingestResult, *, verbose: bool = False) -> None:
    """One line per company with something to add (``verbose``: every company), then the summary."""
    for c in result.companies:
        name = c.ticker or str(c.security_id)
        if c.source not in (CACHED, FETCHED):
            why = {
                NOT_CACHED: "companyfacts not cached — --apply fetches it",
                KNOWN_404: "no companyfacts at SEC (cached 404) — left to the nightly's gate",
                FAILED: f"FETCH FAILED — {c.error}",
                NO_CIK: "no CIK — nothing to parse",
            }[c.source]
            print(f"  {name}: {why}")
            continue
        if not c.planned and not c.screened and not verbose:
            continue
        rev, sup = c.count(revenue=True, supersedes=False), c.count(revenue=True, supersedes=True)
        shares = c.count(revenue=False)
        switched = (
            f"{_short(c.old_label)} -> {_short(c.label)}"
            if c.old_label != c.label
            else _short(c.label)
        )
        extra = f", {sup} same-filing supersession(s)" if sup else ""
        extra += f", {shares} share version(s)" if shares else ""
        extra += f", {len(c.screened)} row(s) screened out" if c.screened else ""
        print(
            f"  {name}: label {switched} · +{rev} revenue version(s){extra} · series end "
            f"{_iso(c.old_end) or 'none'} -> {_iso(c.new_end) or 'none'}"
            + (" · fetched" if c.source == FETCHED else "")
        )
        for x in c.screened:  # every rejected row, printed — never silently dropped
            print(
                f"      screened {x.reason}: quarter ending {_iso(x.period_end)} filed "
                f"{_iso(x.filed)} value {x.value:,.0f} vs {x.reference:,.0f} ({x.accession})"
            )
    touched = [c for c in result.companies if c.planned]
    screened = sum(len(c.screened) for c in result.companies)
    by_source: dict[str, int] = {}
    for c in result.companies:
        by_source[c.source] = by_source.get(c.source, 0) + 1
    verb = "appended" if result.applied else "would append"
    n = result.appended if result.applied else result.planned
    rev = sum(c.count(revenue=True, supersedes=False) for c in result.companies)
    sup = sum(c.count(revenue=True, supersedes=True) for c in result.companies)
    shares = sum(c.count(revenue=False) for c in result.companies)
    sources = ", ".join(f"{k} {v}" for k, v in sorted(by_source.items()))
    print(
        f"SUMMARY: {len(result.companies)} securities ({sources}) · {len(touched)} with versions to add · "
        f"{verb} {n} (revenue {rev} + same-filing supersessions {sup} + share {shares}) · "
        f"screened out {screened} row(s) in {sum(1 for c in result.companies if c.screened)} "
        f"companies · fact_fundamentals rows {result.rows_before} -> {result.rows_after}"
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Re-parse every active basket company's companyfacts under the CURRENT revenue-label "
        "rule and append the versions the old first-present rule never stored (append-only, filing-stamped). "
        "Dry-run by default."
    )
    p.add_argument(
        "--thesis", default=None, help="one thesis id (uuid); omit for every active basket"
    )
    p.add_argument("--ticker", default=None, help="comma-separated tickers to limit the run to")
    p.add_argument(
        "--apply", action="store_true", help="write (default: dry-run, read-only, no network)"
    )
    p.add_argument(
        "--no-live",
        action="store_true",
        help="with --apply: never fetch — an uncached companyfacts is reported, not pulled",
    )
    p.add_argument("--verbose", action="store_true", help="print every company, not only changes")
    args = p.parse_args(argv)
    thesis_id = UUID(args.thesis) if args.thesis else None
    tickers = [t.strip() for t in (args.ticker or "").split(",") if t.strip()]
    fetch = args.apply and not args.no_live

    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection, no network; nothing written)"
    docs = "cache first, fetch only uncached" if fetch else "cache only"
    print(f"reingest_fundamentals · {mode} · companyfacts: {docs}")
    try:
        lock = acquire_pass_lock(holder="reingest") if args.apply else None
    except PassAlreadyRunning as exc:
        print(
            f"reingest: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            result = reingest_fundamentals(
                conn, apply=args.apply, fetch=fetch, thesis_id=thesis_id, tickers=tickers
            )
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()

    report(result, verbose=args.verbose)
    if args.apply:
        print(f"provenance: {write_payload(build_payload(result))}")
    if any(c.source == FAILED for c in result.companies):
        raise SystemExit(1)


__all__ = [
    "CompanyPlan",
    "ReingestResult",
    "active_securities",
    "first_present_label",
    "load_companyfacts",
    "plan_company",
    "reingest_fundamentals",
    "build_payload",
    "report",
    "main",
]


if __name__ == "__main__":
    main()
