"""``pipeline.repair_filing_dates`` — give STORED filings their public date (F7) and share counts theirs (F9).

THE RULE (``2026-11-filings-dated-by-acceptance``, ``domain/rule_log.py``). A filing counts from when it appeared
on EDGAR. An 8-K, 13D/G or 10-Q / 10-K submitted after 17:30 ET carries the NEXT business day's ``filingDate``
(Regulation S-T 13(a)(2)) although EDGAR disseminated it that evening, so the ingest now stores SEC's trusted
acceptance (``accepted``) and dates the row by its market-time date (``ingest.edgar.acceptance
.resolve_public_acceptance`` / ``public_date``). This CLI does the same for the rows stored before the rule, and
gives each stored share count the acceptance of the filing whose cover carries it (``ingest.shares
.count_publication``, F9). Every judgment is the ingest's own function — IMPORTED, never re-stated.

WHAT IT READS — CACHE ONLY, always: the EDGAR cache on disk (each company's submissions index and its cached older
pages, a filing's cached header, companyfacts for the share counts). It never makes an SEC request (``--cache-dir``
points it at another cache, e.g. a copy of a stack's volume). A filing the cache cannot place is listed, never
guessed — and keeps its filing date (late, never early).

WHAT IT WRITES (``--apply``) — appended VERSIONS, never an UPDATE (every table carries ``no_update``), each
``recorded_at`` = now:

- **8-K / 13D/G** (``fact_corporate_event`` / ``fact_activist_stake``): per filing whose latest version carries no
  acceptance and whose trusted acceptance falls on an EARLIER market day than its stored ``valid_from`` — one new
  version: the latest version's columns, ``accepted`` set, ``valid_from`` = the acceptance's market-time date. The
  table's public instant is the FACT's newest acceptance (``db.public_clock``), so the older version moves with
  it. ``--same-day`` also stores the acceptance on a filing whose day does not change (its night is unchanged).
- **revenue** (``fact_fundamentals``, ``metric_key = 'revenue'``): per FILING (security, period, accession) whose
  acceptance falls on an earlier day than its stored ``valid_from`` — one TWIN at the acceptance date (the
  filing's current reading: its latest version not already set aside; ``accepted`` set, ``supersedes`` NULL), and
  every version of the filing at the old date SET ASIDE (``fundamentals_set_aside``, rule family
  ``filings-dated-by-acceptance`` — the rows are kept, never deleted; the family-less data-quality reads leave it
  out; no revoke is offered, because revoking it alone would put the filing back at two dates), so one filing is
  never read at two dates (a lingering old-date version would sort after the twin and win the latest-filing pick).
  A same-day acceptance writes nothing (the filing's key cannot carry a second plain version on one date).
- **share counts** (``fact_shares_outstanding``, F9): per fact whose latest version carries no publication and
  whose count + date is a companyfacts cover row — one restating version (same count, date, citation,
  ratification; ``filing_accession`` / ``filing_accepted`` set; ``supersedes`` = the version it restates). The
  public clock then counts the fact from that filing (the running max over the fact's versions).

SAFETY (the ``repair_shares_cover_dates`` / ``backfill_accepted`` shape):

- **Dry-run is the DEFAULT.** Its connection is READ ONLY; it prints a summary by table, form and year (``--verbose``
  prints every row) and, with ``--report-json PATH``, writes every decision.
- **The repair lock.** ``--apply`` holds the REPAIR lock (``pipeline/run_lock.repair_lock_or_exit``: the
  call-of-record lock, then the fact-writer gate) — exit 75 when a nightly pass, a backfill or a draining company
  writer holds it — counts every table it writes before and after inside ONE transaction, and commits only when
  each difference equals the plan.
- **Idempotent.** A filing whose latest version carries an acceptance (or a revenue filing any of whose versions
  does) and a share fact whose latest version carries a publication need nothing, so a second ``--apply`` appends
  zero (count the tables).
- **Provenance + journal.** ``--apply`` writes one JSON under ``data/repairs/`` (fail-open) and one ``data_repair``
  journal row, so the editions cut sees the repair.
- Run ``--apply`` on prod only after a labeled backup, from the deployed image, outside 21:30-00:00 ET.

    python -m pipeline.repair_filing_dates                         # dry run (read-only, cache only)
    python -m pipeline.repair_filing_dates --tables 8k,13d         # one or more of 8k,13d,revenue,shares
    python -m pipeline.repair_filing_dates --apply                 # append the re-dated versions
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import connect
from db.set_aside import active_ids_sql
from domain.settings import get_settings
from ingest.edgar.acceptance import (
    EDGAR_TZ,
    FILING_DATE_ROLL,
    SOURCE_FILED,
    cached_header_acceptance,
    cached_listings,
    parse_filing_date,
    public_date,
    resolve_public_acceptance,
)
from ingest.edgar.client import EdgarClient
from ingest.fundamentals import REVENUE_METRIC
from ingest.shares import cik_of_source_ref, count_publication, cover_rows
from pipeline.run_lock import repair_lock_or_exit
from repositories import data_repair_repo
from repositories import fundamentals_set_aside_repo as set_aside_repo

_log = logging.getLogger("alphadeck.repair_filing_dates")

TOOL = "repair_filing_dates"
RULE_ID = "2026-11-filings-dated-by-acceptance"
REDATE_REASON = "redated_by_acceptance"
AUTHOR = f"system:{TOOL}"

_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "repairs"

# the table groups this CLI repairs (``--tables``)
EIGHT_K = "8k"
THIRTEEN_D = "13d"
REVENUE = "revenue"
SHARES = "shares"
GROUPS = (EIGHT_K, THIRTEEN_D, REVENUE, SHARES)
TABLE_OF = {
    EIGHT_K: "fact_corporate_event",
    THIRTEEN_D: "fact_activist_stake",
    REVENUE: "fact_fundamentals",
    SHARES: "fact_shares_outstanding",
}

# A row's decision.
MOVE = "move"  # the acceptance falls on an earlier market day: --apply appends the re-dated version
SAME_DAY = (
    "same_day"  # the acceptance is on the stored day: written only with --same-day (8-K / 13D/G)
)
DATED = "dated"  # already carries an acceptance / publication (an earlier --apply, or the ingest): nothing to do
UNTRUSTED = "untrusted"  # the listed time fails the trust test and no cached header passes: the filing date stays
NOT_LISTED = (
    "not_listed"  # the accession is not in the cached submissions index: the filing date stays
)
OTHER_FILING = "other_filing"  # the listing's filing date is not the row's (dated by a later filing): unchanged
LATER = "later"  # the acceptance's day is AFTER the stored day — never written (it would date the row later)
SET_ASIDE = "set_aside"  # revenue: every version of the filing is already set aside — nothing read to re-date
COLLISION = "collision"  # revenue: another filing of the period is stored on the acceptance date — left, listed
PUBLISH = "publish"  # shares: the count is a cover row — --apply appends the restating version
NO_COVER = (
    "no_cover_row"  # shares: the count + date is no cover row — the ratification instant stands
)
NOT_JUDGED = (
    "not_judged"  # shares: the company's companyfacts is not cached (or no CIK) — listed, unchanged
)
WRITES = (MOVE, PUBLISH)


@dataclass
class Row:
    """One decision: a filing (8-K / 13D/G / revenue) or a share fact, and what ``--apply`` does with it."""

    group: str
    status: str
    tenant_id: UUID
    security_id: UUID
    ticker: str | None
    form: str
    key: str  # the accession (filings) or the source_ref (share counts)
    stored_from: date | None  # the stored valid_from (the latest version's / the filing's)
    filed: date | None = None
    listed: str | None = None  # SEC's raw listed acceptance, as cached
    accepted: datetime | None = None  # the trusted acceptance (or the count's publication)
    source: str | None = None  # where the acceptance came from (listed / header / bound ...)
    new_from: date | None = None
    latest: dict[str, Any] | None = None  # the version re-dated / restated
    old_versions: list[UUID] = field(default_factory=list)  # revenue: the versions set aside
    publication_accession: str | None = None  # shares
    multi_version: bool = False  # shares: the fact has more than one version
    evening: bool = False  # the acceptance is 17:30-22:00 ET (the roll window)
    appended_id: UUID | None = None
    set_aside_ids: list[UUID] = field(default_factory=list)


@dataclass
class RepairResult:
    rows: list[Row] = field(default_factory=list)
    groups: tuple[str, ...] = GROUPS
    same_day: bool = False
    applied: bool = False
    counts_before: dict[str, int] = field(default_factory=dict)
    counts_after: dict[str, int] = field(default_factory=dict)
    versions_scanned: dict[str, int] = field(default_factory=dict)

    def planned(self, group: str) -> list[Row]:
        writes = {MOVE, PUBLISH} | (
            {SAME_DAY} if self.same_day and group in (EIGHT_K, THIRTEEN_D) else set()
        )
        return [r for r in self.rows if r.group == group and r.status in writes]


# --- the cache (on disk only) ------------------------------------------------------------------------------


class Cache:
    """The EDGAR cache ON DISK, memoized per company: the submissions listing (``cached_listings`` — the index and
    its cached older pages), companyfacts' cover rows, a filing's cached header. Never a request."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self._listings: dict[int, dict[str, tuple[str, str]]] = {}
        self._covers: dict[int, list[dict[str, Any]] | None] = {}

    def listings(self, cik: str | int | None) -> dict[str, tuple[str, str]]:
        if not cik:
            return {}
        k = int(cik)
        if k not in self._listings:
            self._listings[k] = cached_listings(self.cache_dir, k)
        return self._listings[k]

    def covers(self, cik: str | int | None) -> list[dict[str, Any]] | None:
        if not cik:
            return None
        k = int(cik)
        if k not in self._covers:
            try:
                doc = json.loads(
                    (self.cache_dir / "companyfacts" / f"CIK{k:010d}.json").read_text(
                        encoding="utf-8"
                    )
                )
                self._covers[k] = cover_rows(doc)
            except (OSError, ValueError):
                self._covers[k] = None
        return self._covers[k]


def _is_evening(at: datetime | None) -> bool:
    if at is None:
        return False
    t = at.astimezone(EDGAR_TZ).time()
    return FILING_DATE_ROLL <= t <= datetime.strptime("22:00", "%H:%M").time()


def judge_filing(
    cache: Cache, cik: str | int | None, accession: str, filed: date, stored_from: date
) -> tuple[str, datetime | None, str | None, str | None, date | None]:
    """``(status, accepted, source, listed, new valid_from)`` for one stored filing — the ingest's own rule
    (``resolve_public_acceptance``) over the cached listing (which must carry the row's filing date) and the
    cached header."""
    listing = cache.listings(cik).get(accession)
    if listing is None:
        return NOT_LISTED, None, None, None, None
    if parse_filing_date(listing[0]) != filed:
        return OTHER_FILING, None, None, listing[1], None
    res = resolve_public_acceptance(
        listing[1], filed, lambda: cached_header_acceptance(cache.cache_dir, accession)
    )
    if res.at is None or res.source == SOURCE_FILED:
        return UNTRUSTED, None, res.source, listing[1], None
    new = public_date(res.at, filed)
    if new == stored_from:
        return SAME_DAY, res.at, res.source, listing[1], new
    if new > stored_from:
        return LATER, res.at, res.source, listing[1], new
    return MOVE, res.at, res.source, listing[1], new


# --- the scans (read-only) ---------------------------------------------------------------------------------


def _fetch(conn: psycopg.Connection, query: str, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def scan_filings(conn: psycopg.Connection, group: str, cache: Cache, result: RepairResult) -> None:
    """8-K / 13D/G: the latest version of every stored filing, judged."""
    table = TABLE_OF[group]
    versions = _fetch(
        conn,
        f"SELECT count(*) AS n FROM {table}",  # noqa: S608 — a module constant
    )
    result.versions_scanned[group] = int(versions[0]["n"])
    rows = _fetch(
        conn,
        f"SELECT DISTINCT ON (t.tenant_id, t.security_id, t.accession) t.*, "  # noqa: S608
        "sm.ticker AS _ticker, sm.cik AS _cik "
        f"FROM {table} t LEFT JOIN security_master sm ON sm.id = t.security_id AND sm.tenant_id = t.tenant_id "
        "ORDER BY t.tenant_id, t.security_id, t.accession, t.recorded_at DESC, t.id DESC",
    )
    for r in rows:
        base = Row(
            group=group,
            status=DATED,
            tenant_id=r["tenant_id"],
            security_id=r["security_id"],
            ticker=r["_ticker"],
            form=r["form"],
            key=r["accession"],
            stored_from=r["valid_from"],
            filed=r["filed"],
            latest=r,
        )
        if r.get("accepted") is not None:
            base.accepted = r["accepted"]
            result.rows.append(base)
            continue
        cik = r["_cik"] or cik_of_source_ref(r.get("source_ref") or "")
        status, at, source, listed, new = judge_filing(
            cache, cik, r["accession"], r["filed"], r["valid_from"]
        )
        base.status, base.accepted, base.source, base.listed, base.new_from = (
            status,
            at,
            source,
            listed,
            new,
        )
        base.evening = _is_evening(at)
        result.rows.append(base)


def scan_revenue(conn: psycopg.Connection, cache: Cache, result: RepairResult) -> None:
    """Revenue: every stored version, grouped per FILING (security, period, accession), judged."""
    aside = {r["fact_id"] for r in _fetch(conn, active_ids_sql("fact_fundamentals"))}
    versions = _fetch(
        conn,
        "SELECT f.*, sm.ticker AS _ticker, sm.cik AS _cik FROM fact_fundamentals f "
        "LEFT JOIN security_master sm ON sm.id = f.security_id AND sm.tenant_id = f.tenant_id "
        "WHERE f.metric_key = %s ORDER BY f.tenant_id, f.security_id, f.period_end, f.accession, "
        "f.recorded_at, f.id",
        (REVENUE_METRIC,),
    )
    result.versions_scanned[REVENUE] = len(versions)
    # (tenant, security, period, valid_from) dates already holding a plain (supersedes NULL) version — the
    # filing key a twin must not collide with (migration 0060's UNIQUE NULLS NOT DISTINCT)
    plain_dates = {
        (v["tenant_id"], v["security_id"], v["period_end"], v["valid_from"])
        for v in versions
        if v.get("supersedes") is None
    }
    groups: dict[tuple[UUID, UUID, date, str], list[dict[str, Any]]] = {}
    for v in versions:
        groups.setdefault(
            (v["tenant_id"], v["security_id"], v["period_end"], v["accession"]), []
        ).append(v)
    for (tenant, sid, period, accession), vs in groups.items():
        latest = vs[-1]
        row = Row(
            group=REVENUE,
            status=DATED,
            tenant_id=tenant,
            security_id=sid,
            ticker=latest["_ticker"],
            form=f"revenue {period.isoformat()}",
            key=accession,
            stored_from=latest["valid_from"],
        )
        result.rows.append(row)
        carried = [v["accepted"] for v in vs if v.get("accepted") is not None]
        if carried:
            row.accepted = carried[-1]
            continue
        froms = {v["valid_from"] for v in vs}
        if len(froms) != 1:  # one filing stored at two dates before any acceptance: never guessed
            row.status = OTHER_FILING
            continue
        stored_from = next(iter(froms))
        row.filed = stored_from  # a revenue row's valid_from IS its filing date before the rule
        status, at, source, listed, new = judge_filing(
            cache, latest["_cik"], accession, stored_from, stored_from
        )
        row.status, row.accepted, row.source, row.listed, row.new_from = (
            status,
            at,
            source,
            listed,
            new,
        )
        row.evening = _is_evening(at)
        if status != MOVE:
            continue
        live = [v for v in vs if v["id"] not in aside]
        if not live:
            row.status = SET_ASIDE
            continue
        if (tenant, sid, period, new) in plain_dates:
            row.status = COLLISION
            continue
        row.latest = live[-1]  # the filing's current reading (latest recorded, not set aside)
        row.old_versions = [v["id"] for v in live]


def scan_shares(conn: psycopg.Connection, cache: Cache, result: RepairResult) -> None:
    """Share counts (F9): the latest version of every fact, judged against its company's cover rows."""
    versions = _fetch(
        conn,
        "SELECT f.*, sm.ticker AS _ticker, sm.cik AS _cik FROM fact_shares_outstanding f "
        "LEFT JOIN security_master sm ON sm.id = f.security_id AND sm.tenant_id = f.tenant_id "
        "ORDER BY f.tenant_id, f.source_ref, f.recorded_at, f.id",
    )
    result.versions_scanned[SHARES] = len(versions)
    facts: dict[tuple[UUID, str], list[dict[str, Any]]] = {}
    for v in versions:
        facts.setdefault((v["tenant_id"], v["source_ref"]), []).append(v)
    for (_tenant, ref), vs in facts.items():
        latest = vs[-1]
        row = Row(
            group=SHARES,
            status=DATED,
            tenant_id=latest["tenant_id"],
            security_id=latest["security_id"],
            ticker=latest["_ticker"],
            form=latest.get("source") or "",
            key=ref,
            stored_from=latest["valid_from"],
            latest=latest,
            multi_version=len(vs) > 1,
        )
        result.rows.append(row)
        if latest.get("filing_accepted") is not None:
            row.accepted = latest["filing_accepted"]
            row.publication_accession = latest.get("filing_accession")
            continue
        cik = latest["_cik"] or cik_of_source_ref(ref)
        rows = cache.covers(cik)
        if rows is None:
            row.status = NOT_JUDGED
            continue
        accession, at = count_publication(
            rows,
            cache.listings(cik),
            float(latest["shares"]),
            latest["valid_from"],
            cache.cache_dir,
        )
        if accession is None:
            row.status = NO_COVER
            continue
        row.status, row.accepted, row.publication_accession = PUBLISH, at, accession
        row.evening = _is_evening(at)


def scan(
    conn: psycopg.Connection,
    *,
    cache_dir: Path | None = None,
    groups: tuple[str, ...] = GROUPS,
    same_day: bool = False,
) -> RepairResult:
    """Judge every stored row of the chosen groups. Reads only; the caller owns the connection (a dry run hands in
    a READ ONLY one)."""
    cache = Cache(cache_dir or EdgarClient(allow_live=False).cache_dir)
    result = RepairResult(groups=groups, same_day=same_day)
    for g in groups:
        if g in (EIGHT_K, THIRTEEN_D):
            scan_filings(conn, g, cache, result)
        elif g == REVENUE:
            scan_revenue(conn, cache, result)
        elif g == SHARES:
            scan_shares(conn, cache, result)
    return result


# --- the write (one transaction, counted) ------------------------------------------------------------------

_SKIP_COLS = {"id", "recorded_at", "_ticker", "_cik"}


def _copy(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _SKIP_COLS}


def _count(conn: psycopg.Connection, table: str) -> int:
    return int(_fetch(conn, f"SELECT count(*) AS n FROM {table}")[0]["n"])  # noqa: S608


def apply(conn: psycopg.Connection, result: RepairResult) -> None:
    """Append every planned version (and, for revenue, the set-asides) in ONE transaction, counting each table
    before and after; commit only when every difference equals the plan, else roll back and raise. The caller
    holds the lock."""
    tables = {TABLE_OF[g] for g in result.groups} | (
        {"fundamentals_set_aside"} if REVENUE in result.groups else set()
    )
    before = {t: _count(conn, t) for t in sorted(tables)}
    expect = dict.fromkeys(before, 0)
    rule = f"{set_aside_repo.REDATE_FAMILY}@{get_settings().image_sha or 'unknown'}"
    try:
        for g in result.groups:
            table = TABLE_OF[g]
            for r in result.planned(g):
                assert r.latest is not None
                values = _copy(r.latest)
                if g == SHARES:
                    values["filing_accession"] = r.publication_accession
                    values["filing_accepted"] = r.accepted
                    values["supersedes"] = r.latest["id"]
                    values["vouched"] = r.latest.get("vouched")
                else:
                    values["accepted"] = r.accepted
                    values["valid_from"] = r.new_from
                    if g == REVENUE:
                        values["supersedes"] = None
                r.appended_id = append_fact(conn, table, values)
                expect[table] += 1
                if g == REVENUE:
                    for old in r.old_versions:
                        r.set_aside_ids.append(
                            set_aside_repo.append_set_aside(
                                conn,
                                old,
                                reason=REDATE_REASON,
                                rule=rule,
                                author=AUTHOR,
                                detail={
                                    "rule_log": RULE_ID,
                                    "twin": str(r.appended_id),
                                    "accession": r.key,
                                    "accepted": r.accepted.isoformat() if r.accepted else None,
                                    "acceptance_source": r.source,
                                    "old_valid_from": (
                                        r.stored_from.isoformat() if r.stored_from else None
                                    ),
                                    "new_valid_from": (
                                        r.new_from.isoformat() if r.new_from else None
                                    ),
                                },
                            )
                        )
                        expect["fundamentals_set_aside"] += 1
        after = {t: _count(conn, t) for t in before}
        bad = {
            t: (before[t], after[t], expect[t]) for t in before if after[t] - before[t] != expect[t]
        }
        if bad:
            raise RuntimeError(f"count check failed (before, after, planned): {bad} — ROLLED BACK")
    except BaseException:
        conn.rollback()
        for r in result.rows:
            r.appended_id = None
            r.set_aside_ids = []
        raise
    conn.commit()
    result.counts_before, result.counts_after, result.applied = before, after, True


# --- provenance + report -----------------------------------------------------------------------------------


def _iso(x: date | datetime | None) -> str | None:
    return x.isoformat() if x is not None else None


def _row_out(r: Row) -> dict[str, Any]:
    return {
        "group": r.group,
        "status": r.status,
        "ticker": r.ticker,
        "security_id": str(r.security_id),
        "form": r.form,
        "key": r.key,
        "stored_valid_from": _iso(r.stored_from),
        "filed": _iso(r.filed),
        "listed": r.listed,
        "accepted": _iso(r.accepted),
        "source": r.source,
        "new_valid_from": _iso(r.new_from),
        "publication_accession": r.publication_accession,
        "evening": r.evening,
        "multi_version": r.multi_version or None,
        "replaces": str(r.latest["id"]) if r.latest and r.status in WRITES + (SAME_DAY,) else None,
        "old_versions": [str(i) for i in r.old_versions] or None,
        "appended_id": str(r.appended_id) if r.appended_id else None,
        "set_aside_ids": [str(i) for i in r.set_aside_ids] or None,
    }


def summary(result: RepairResult) -> dict[str, Any]:
    """PURE: the counts by group x status, by group x form (8-K / 13D/G) or group x year of the stored date,
    and the evening (17:30-22:00 ET) acceptances among the resolved ones."""
    by_status: dict[str, Counter] = {g: Counter() for g in result.groups}
    by_form: dict[str, Counter] = {g: Counter() for g in result.groups}
    by_year: dict[str, Counter] = {g: Counter() for g in result.groups}
    evening: dict[str, Counter] = {g: Counter() for g in result.groups}
    for r in result.rows:
        by_status[r.group][r.status] += 1
        if r.status in WRITES:
            by_form[r.group][r.form if r.group != REVENUE else "revenue"] += 1
            by_year[r.group][str(r.stored_from.year) if r.stored_from else "?"] += 1
        if r.evening:
            evening[r.group][r.status] += 1
    return {
        "by_status": {g: dict(c) for g, c in by_status.items()},
        "writes_by_form": {g: dict(c) for g, c in by_form.items()},
        "writes_by_year": {g: dict(sorted(c.items())) for g, c in by_year.items()},
        "evening_acceptances": {g: dict(c) for g, c in evening.items()},
        "versions_scanned": result.versions_scanned,
    }


def build_payload(result: RepairResult) -> dict[str, Any]:
    """The provenance payload — PURE (no I/O)."""
    return {
        "kind": TOOL,
        "rule_log": RULE_ID,
        "at": datetime.now(timezone.utc).isoformat(),
        "applied": result.applied,
        "groups": list(result.groups),
        "same_day": result.same_day,
        "counts_before": result.counts_before,
        "counts_after": result.counts_after,
        "summary": summary(result),
        "rows": [_row_out(r) for r in result.rows if r.status != DATED],
    }


def write_payload(payload: dict[str, Any], base_dir: Path | None = None) -> Path | None:
    """Write the provenance JSON to ``<base>/<utc-timestamp>-repair-filing-dates.json``; return the path, or ``None``
    fail-open (the committed rows ARE the repair; a provenance fault is logged, never raised)."""
    base = base_dir or _DEFAULT_LOG_DIR
    try:
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = base / f"{stamp}-repair-filing-dates.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except OSError:
        _log.exception("provenance write failed (fail-open — the rows are committed)")
        return None


def report(result: RepairResult, *, verbose: bool = False, examples: int = 10) -> None:
    s = summary(result)
    for g in result.groups:
        rows = [r for r in result.rows if r.group == g and r.status != DATED]
        shown = rows if verbose else [r for r in rows if r.status in WRITES][:examples]
        print(f"\n--- {g} ({TABLE_OF[g]}) ---")
        for r in shown:
            tail = (
                f"{_iso(r.stored_from)} -> {_iso(r.new_from)} (accepted {_iso(r.accepted)}, {r.source})"
                if g != SHARES
                else f"count as of {_iso(r.stored_from)} public {_iso(r.accepted)} via {r.publication_accession}"
                + (" [multi-version fact]" if r.multi_version else "")
            )
            mark = f" [row {r.appended_id}]" if r.appended_id else ""
            print(f"  [{r.status}] {r.ticker or r.security_id} · {r.form} · {r.key} · {tail}{mark}")
        if not verbose and len([r for r in rows if r.status in WRITES]) > examples:
            print(
                f"  ... ({len([r for r in rows if r.status in WRITES]) - examples} more; --verbose lists all)"
            )
        print(f"  status: {s['by_status'][g]}")
        if s["writes_by_form"][g]:
            print(f"  to write by form: {s['writes_by_form'][g]}")
        if s["writes_by_year"][g]:
            print(f"  to write by year (stored date): {s['writes_by_year'][g]}")
        if s["evening_acceptances"][g]:
            print(
                f"  evening (17:30-22:00 ET) acceptances, by status: {s['evening_acceptances'][g]}"
            )
    verb = "appended" if result.applied else "would append"
    parts = []
    for g in result.groups:
        n = len(result.planned(g))
        extra = ""
        if g == REVENUE:
            extra = f" + set aside {sum(len(r.old_versions) for r in result.planned(g))} old-date version(s)"
        parts.append(f"{g}: {n}{extra}")
    tables = (
        " · ".join(
            f"{t} {result.counts_before[t]} -> {result.counts_after[t]}"
            for t in result.counts_before
        )
        if result.applied
        else "nothing written"
    )
    print(
        f"\nSUMMARY: scanned {result.versions_scanned} version(s) · {verb} — "
        + "; ".join(parts)
        + f" · {tables}"
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def _groups(raw: str) -> tuple[str, ...]:
    out = tuple(g.strip() for g in raw.split(",") if g.strip())
    bad = [g for g in out if g not in GROUPS]
    if bad or not out:
        raise argparse.ArgumentTypeError(f"--tables takes {','.join(GROUPS)}; got {raw!r}")
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Give stored 8-K / 13D/G / revenue rows their public date (SEC acceptance, F7) and share "
        "counts their filing's acceptance (F9). Dry-run by default (read-only, cache only); --apply appends "
        "versions under the repair lock."
    )
    p.add_argument("--apply", action="store_true", help="append the versions (default: dry-run)")
    p.add_argument(
        "--tables",
        type=_groups,
        default=GROUPS,
        help=f"comma list of {','.join(GROUPS)} (default all)",
    )
    p.add_argument(
        "--same-day",
        action="store_true",
        help="also store the acceptance on 8-K / 13D/G rows whose day does not change",
    )
    p.add_argument(
        "--cache-dir", type=Path, default=None, help="the EDGAR cache to read (default: the app's)"
    )
    p.add_argument("--verbose", action="store_true", help="print every row, not only examples")
    p.add_argument(
        "--report-json", type=Path, default=None, help="write every decision to this JSON (dry run)"
    )
    args = p.parse_args(argv)

    # W1 — a writing run excludes the nightly pass AND every company writer (pipeline.run_lock); exit 75 if busy
    lock = repair_lock_or_exit(holder=TOOL) if args.apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            with conn.cursor() as cur:
                cur.execute("SELECT current_database() AS db")
                print(f"target DB : {cur.fetchone()['db']}")
            result = scan(
                conn, cache_dir=args.cache_dir, groups=args.tables, same_day=args.same_day
            )
            if not args.apply:
                conn.rollback()
                print(f"\n=== {TOOL} — DRY-RUN (nothing written) ===")
                report(result, verbose=args.verbose)
                if args.report_json:
                    args.report_json.write_text(
                        json.dumps(build_payload(result), indent=2), encoding="utf-8"
                    )
                    print(f"report: {args.report_json}")
                return
            print(f"\n=== {TOOL} — APPLY ===")
            try:
                apply(conn, result)
            except RuntimeError as exc:
                print(f"ABORTED: {exc}")
                raise SystemExit(1) from None
            report(result, verbose=args.verbose)
            provenance = write_payload(build_payload(result))
            print(f"provenance: {provenance}")
            appended = sum(1 for r in result.rows if r.appended_id)
            set_aside = sum(len(r.set_aside_ids) for r in result.rows)
            # S7 — the data-repair journal: one row per --apply, under the lock
            data_repair_repo.journal(
                conn,
                tool=TOOL,
                summary=(
                    f"dated {appended} stored filing / share-count version(s) by SEC acceptance "
                    f"({set_aside} old-date revenue version(s) set aside)"
                ),
                rows_changed=appended + set_aside,
                counts={g: len(result.planned(g)) for g in result.groups}
                | {"appended": appended, "set_aside": set_aside},
                provenance=str(provenance) if provenance else None,
            )
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    main()
