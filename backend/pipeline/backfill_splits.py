"""``pipeline.backfill_splits`` — split plan S1: fill the split list from Polygon's split reference, every
split checked against the company's own SEC filing.

WHAT IT DOES, for the ACTIVE basket union (``pipeline/split_basis.py``'s ``_UNION`` — every resolved member of
every non-archived thesis):

1. **Polygon** — one whole-market pull of the split reference since ``--since`` (default 2024-10-01, the
   stored two-year tape), paced on the shared free-tier budget (``ingest/polygon_splits.py``). The request
   carries a date only. Rows are matched to members by ticker or vendor price symbol.
2. **SEC** — for each matched split, the filings in ``[execution - 120 days, execution + 10 days]``:
   - 8-K filers: the ``fact_corporate_event`` rows the nightly already stores, with item 3.03 and/or 5.03;
   - 6-K / Reg A / any filer: the company's submissions index (through the EDGAR client's cache) for
     6-K, 1-A, 1-U and 8-K (with those items) filings in the window.
   Each is read ONCE as its full submission (``forms/<accession>/<accession>.txt`` — immutable, cached
   forever) through the existing EDGAR client — its rate limit (``SEC_RATE_PER_SEC``, well under SEC's 10/s),
   its declared User-Agent, its cache — and the pure extractor (``ingest/edgar/split_filing.py``) reads the
   ratio, the first session and the located passage. The request carries a CIK and an accession only.
3. **Match** — ``domain.splits.decide``: confirmed (``auto:sec+polygon``) only when both sources agree
   exactly on the ratio and on the date; announced when that agreement points to a future session; every
   other case ``needs_review`` with both sources' evidence on the row. Never a guessed value.
4. **Plan** against the stored list: a split not on it yet is APPENDED; one whose newest version the
   backfill wrote and whose content is unchanged is left alone (``unchanged``); one whose evidence changed
   gets a new backfill version; one whose newest version the OPERATOR wrote is never touched
   (``operator decision stands``). Stored splits this run did not produce stay exactly as they are.

SAFETY (the ``pipeline/repair_benchmark_bars.py`` conventions):

- **Dry-run is the DEFAULT**: a READ-ONLY connection, no lock, no DB row. It prints every split with both
  sources, the verdict, a passage snippet and what ``--apply`` would append. (It does fill the shared
  caches: immutable filing documents, today's Polygon pages — so the ``--apply`` that follows reads the same
  inputs.)
- ``--apply`` holds the call-of-record lock (``pipeline/run_lock.py``; exit 75 when held), appends every
  planned version in ONE transaction, and verifies the TABLE grew by exactly the plan (``count(*)`` before
  and after) before it commits.
- **Idempotent**: a re-run on the same inputs plans 0 appends.
- **The off switch**: with no ``POLYGON_API_KEY`` it fetches nothing and writes nothing.

    python -m pipeline.backfill_splits                      # dry run (default)
    python -m pipeline.backfill_splits --apply              # append the planned versions
    python -m pipeline.backfill_splits --ticker BTOC --ticker SMX   # only these members
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg

from domain.market_time import market_today
from domain.settings import get_settings
from domain.splits import (
    SOURCE_POLYGON,
    STATUS_NEEDS_REVIEW,
    STATUSES,
    PolygonEvidence,
    SecEvidence,
    SplitDecision,
    decide,
    ratio_label,
    same_ratio,
)
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.edgar.form8k import filing_index_url
from ingest.edgar.split_filing import extract_split, submission_documents
from ingest.edgar.submissions import fetch_submissions, parse_item_codes
from ingest.polygon_splits import PolygonSplitsError, PolygonSplitSource, default_split_source
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock
from pipeline.split_basis import _UNION
from repositories import split_repo
from repositories.split_repo import AUTHOR_BACKFILL, SplitVersion, content_of, decision_values

_log = logging.getLogger("alphadeck.backfill_splits")

DEFAULT_SINCE = date(2024, 10, 1)
WINDOW_BEFORE_DAYS = 120
WINDOW_AFTER_DAYS = 10
# the 8-K items a split is reported under: 3.03 (material modification to rights of security holders) and
# 5.03 (amendments to articles) — every real split 8-K in tests/fixtures/splits carries 5.03
SPLIT_8K_ITEMS = frozenset({"3.03", "5.03"})
_8K_FORMS = frozenset({"8-K", "8-K/A"})
SEC_FORMS = frozenset({"6-K", "6-K/A", "1-A", "1-A/A", "1-A POS", "1-U", *_8K_FORMS})
SEC_RATE_PER_SEC = 4.0

APPEND = "append"
UNCHANGED = "unchanged"
OPERATOR_KEPT = "operator_kept"

EXIT_REFUSED = 2


@dataclass(frozen=True)
class Member:
    """One security of the active basket union, with the identity the two sources key on."""

    tenant_id: UUID
    security_id: UUID
    ticker: str | None
    price_symbol: str | None
    cik: str | None


@dataclass(frozen=True)
class FilingRef:
    accession: str
    form: str
    filed: date
    items: tuple[str, ...] | None


@dataclass
class PlannedSplit:
    """One split this run produced, the stored newest version it was compared to, and the outcome."""

    member: Member
    decision: SplitDecision
    stored: SplitVersion | None
    action: str
    changed: tuple[str, ...] = ()
    filings_read: int = 0
    filings_unread: int = 0

    @property
    def values(self) -> dict[str, Any]:
        return decision_values(self.decision)


@dataclass
class BackfillResult:
    since: date
    asof: date
    members: int = 0
    polygon_rows: int = 0
    polygon_pages: int = 0
    polygon_skipped: int = 0
    polygon_matched: int = 0
    planned: list[PlannedSplit] = field(default_factory=list)
    stored_not_seen: int = 0
    sec_live_fetches: int = 0
    submissions_failed: dict[str, str] = field(default_factory=dict)
    applied: bool = False
    appended: int = 0
    rows_before: int | None = None
    rows_after: int | None = None
    recorded_at: datetime | None = None

    def count(self, action: str) -> int:
        return sum(1 for p in self.planned if p.action == action)

    def by_status(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for p in self.planned:
            out[p.decision.status] += 1
        return out


# --- scope ---------------------------------------------------------------------------------------------------


def union_members(
    conn: psycopg.Connection, *, tickers: Iterable[str] | None = None
) -> list[Member]:
    """The active basket union (the split-basis monitor's own query), with each security's identity."""
    with conn.cursor() as cur:
        cur.execute(
            f"WITH u AS ({_UNION}) "  # noqa: S608 — a module literal
            "SELECT u.tenant_id, u.security_id, sm.ticker, sm.price_symbol, sm.cik "
            "FROM u JOIN security_master sm ON sm.id = u.security_id ORDER BY sm.ticker, u.security_id"
        )
        rows = cur.fetchall()
    want = {t.strip().upper() for t in tickers} if tickers else None
    out = []
    for r in rows:
        m = Member(
            tenant_id=r["tenant_id"],
            security_id=r["security_id"],
            ticker=r["ticker"],
            price_symbol=r["price_symbol"],
            cik=r["cik"],
        )
        if want is not None and not (
            {(m.ticker or "").upper(), (m.price_symbol or "").upper()} & want
        ):
            continue
        out.append(m)
    return out


def match_polygon(
    rows: Iterable[PolygonEvidence], members: Iterable[Member]
) -> dict[Member, list[PolygonEvidence]]:
    """Polygon rows per member, matched by the member's ticker OR its vendor price symbol (GGRP prices as
    BTLN, and Polygon lists the split under BTLN)."""
    by_symbol: dict[str, list[Member]] = defaultdict(list)
    for m in members:
        for sym in {(m.ticker or "").upper(), (m.price_symbol or "").upper()} - {""}:
            by_symbol[sym].append(m)
    out: dict[Member, list[PolygonEvidence]] = defaultdict(list)
    for r in rows:
        for m in by_symbol.get(r.ticker.upper(), []):
            out[m].append(r)
    return dict(out)


# --- the SEC side --------------------------------------------------------------------------------------------


def _window(execution: date) -> tuple[date, date]:
    return execution - timedelta(days=WINDOW_BEFORE_DAYS), execution + timedelta(
        days=WINDOW_AFTER_DAYS
    )


def _corporate_events(conn: psycopg.Connection, m: Member, lo: date, hi: date) -> list[FilingRef]:
    """The 8-Ks the nightly stored for this security in the window, newest version per accession, carrying a
    split item."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (accession) accession, form, items, filed FROM fact_corporate_event "
            "WHERE tenant_id = %s AND security_id = %s AND filed BETWEEN %s AND %s "
            "ORDER BY accession, recorded_at DESC, id DESC",
            (m.tenant_id, m.security_id, lo, hi),
        )
        rows = cur.fetchall()
    return [
        FilingRef(
            accession=r["accession"],
            form=r["form"],
            filed=r["filed"],
            items=tuple(r["items"]) if r["items"] is not None else None,
        )
        for r in rows
        if r["items"] and SPLIT_8K_ITEMS & set(r["items"])
    ]


def _submission_filings(submissions: dict[str, Any], lo: date, hi: date) -> list[FilingRef]:
    """The company's own filings in the window that can announce a split (``SEC_FORMS``; an 8-K only with a
    split item, or with its items not resolved yet)."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    out = []
    for i, f in enumerate(forms):
        if f not in SEC_FORMS:
            continue
        try:
            filed = date.fromisoformat(dates[i])
        except (IndexError, ValueError):
            continue
        if not lo <= filed <= hi:
            continue
        codes = parse_item_codes(items[i] if i < len(items) else None)
        if f in _8K_FORMS and codes is not None and not (SPLIT_8K_ITEMS & set(codes)):
            continue
        out.append(
            FilingRef(
                accession=accns[i], form=f, filed=filed, items=tuple(codes) if codes else None
            )
        )
    return out


def _tolerable(e: Exception) -> bool:
    """One filing's (or one company's index) failure, skip-and-count: not cached under ``--no-live``, a fetch
    that still fails after the polite retries, an unreadable document. Anything else (a missing User-Agent, a
    DB error) is systemic and aborts."""
    if isinstance(e, (CacheMiss, ValueError, UnicodeDecodeError)):
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return False
    return isinstance(e, httpx.HTTPError)


def _filing_url(cik: str, accession: str) -> str:
    nodash = accession.replace("-", "")
    return f"{get_settings().sec_archives_base}/{int(cik)}/{nodash}/{accession}.txt"


def read_filing(client: EdgarClient, cik: str, ref: FilingRef) -> SecEvidence:
    """Fetch one filing's full submission through the EDGAR client and extract its split statement."""
    raw = client.get_text(
        _filing_url(cik, ref.accession), f"forms/{ref.accession}/{ref.accession}.txt"
    )
    x = extract_split(submission_documents(raw))
    return SecEvidence(
        accession=ref.accession,
        form=ref.form,
        filed=ref.filed,
        url=filing_index_url(cik, ref.accession),
        ratio_from=x.ratio_from,
        ratio_to=x.ratio_to,
        effective_date=x.effective_date,
        date_kind=x.date_kind,
        passage=x.passage,
        document=x.document,
        abstain_reason=x.abstain_reason,
        date_note=x.date_note,
        about_a_split=x.about_a_split,
    )


# --- the run -------------------------------------------------------------------------------------------------


def _group(rows: list[PolygonEvidence]) -> list[list[PolygonEvidence]]:
    by_day: dict[date, list[PolygonEvidence]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r.execution_date, r.id)):
        by_day[r.execution_date].append(r)
    return [by_day[d] for d in sorted(by_day)]


def _conflicting(group: list[PolygonEvidence]) -> bool:
    first = group[0]
    return any(
        not same_ratio(r.split_from, r.split_to, first.split_from, first.split_to)
        for r in group[1:]
    )


def backfill_splits(
    conn: psycopg.Connection,
    *,
    polygon: PolygonSplitSource,
    edgar: EdgarClient,
    since: date = DEFAULT_SINCE,
    asof: date | None = None,
    apply: bool = False,
    allow_live: bool = True,
    tickers: Iterable[str] | None = None,
) -> BackfillResult:
    """Plan (and with ``apply`` append) the split list — the module docstring. With ``apply`` the caller
    holds the run lock; this function commits its one transaction (or rolls it back and raises)."""
    asof = asof or market_today()
    result = BackfillResult(since=since, asof=asof)
    members = union_members(conn, tickers=tickers)
    result.members = len(members)
    pull = polygon.splits_since(since, allow_live=allow_live)
    result.polygon_rows, result.polygon_pages, result.polygon_skipped = (
        len(pull.rows),
        pull.pages,
        pull.skipped,
    )
    matched = match_polygon(pull.rows, members)
    result.polygon_matched = sum(len(v) for v in matched.values())

    fetches_before = edgar.live_fetches
    submissions: dict[str, dict[str, Any] | None] = {}
    evidence: dict[str, SecEvidence | None] = {}  # accession -> read (None = unread)
    planned: list[PlannedSplit] = []
    for m in sorted(matched, key=lambda m: ((m.ticker or ""), str(m.security_id))):
        if m.cik and m.cik not in submissions:
            try:
                submissions[m.cik] = fetch_submissions(edgar, m.cik)
            except Exception as e:
                if not _tolerable(e):
                    raise
                submissions[m.cik] = None
                result.submissions_failed[m.ticker or str(m.security_id)] = (
                    f"{type(e).__name__}: {e}"[:200]
                )
        for group in _group(matched[m]):
            poly = group[0]
            lo, hi = _window(poly.execution_date)
            refs: dict[str, FilingRef] = {}
            if m.cik:
                for r in _corporate_events(conn, m, lo, hi):
                    refs[r.accession] = r
                subs = submissions.get(m.cik)
                if subs is not None:
                    for r in _submission_filings(subs, lo, hi):
                        refs.setdefault(r.accession, r)
            read: list[SecEvidence] = []
            unread = 0
            for acc in sorted(refs):
                if acc not in evidence:
                    try:
                        evidence[acc] = read_filing(edgar, m.cik, refs[acc])  # type: ignore[arg-type]
                    except Exception as e:
                        if not _tolerable(e):
                            raise
                        _log.warning(
                            "split backfill: %s %s unread (%s)", m.ticker, acc, type(e).__name__
                        )
                        evidence[acc] = None
                ev = evidence[acc]
                if ev is None:
                    unread += 1
                else:
                    read.append(ev)
            d = decide(poly, read, asof=asof, unread=unread)
            if m.cik and submissions.get(m.cik) is None:
                d = replace(d, note=d.note + " The company's SEC filing index could not be read.")
            if not m.cik:
                d = replace(d, note=d.note + " No CIK on the security master — no SEC side.")
            if len(group) > 1 and _conflicting(group):
                listed = "; ".join(
                    f"{r.id}: {ratio_label(r.split_from, r.split_to)}" for r in group
                )
                d = replace(
                    d,
                    status=STATUS_NEEDS_REVIEW,
                    confirmed_via=None,
                    value_source=SOURCE_POLYGON,
                    note=d.note
                    + f" Polygon lists {len(group)} different ratios on this date ({listed}).",
                )
            planned.append(
                PlannedSplit(
                    member=m,
                    decision=d,
                    stored=None,
                    action=APPEND,
                    filings_read=len(read),
                    filings_unread=unread,
                )
            )
    result.sec_live_fetches = edgar.live_fetches - fetches_before

    # plan against the stored list (the newest version of each split, per tenant)
    stored: dict[tuple[UUID, date], SplitVersion] = {}
    for tenant in {m.tenant_id for m in members}:
        for v in split_repo.latest_versions(conn, tenant_id=tenant):
            stored[(v.security_id, v.key_date)] = v
    member_ids = {m.security_id for m in members}
    seen: set[tuple[UUID, date]] = set()
    for p in planned:
        key = (p.member.security_id, p.decision.key_date)
        seen.add(key)
        v = stored.get(key)
        p.stored = v
        if v is None:
            p.action = APPEND
        elif v.author != AUTHOR_BACKFILL:
            p.action = OPERATOR_KEPT
        else:
            new = content_of(p.values)
            old = v.content()
            if new == old:
                p.action = UNCHANGED
            else:
                p.action = APPEND
                p.changed = tuple(
                    c
                    for c, a, b in zip(split_repo.CONTENT_COLUMNS, new, old, strict=True)
                    if a != b
                )
    result.stored_not_seen = sum(
        1 for k in stored if k not in seen and (tickers is None or k[0] in member_ids)
    )
    result.planned = planned

    if not apply:
        return result
    todo = [p for p in planned if p.action == APPEND]
    try:
        before = split_repo.count_rows(conn)
        stamp: datetime | None = None
        for p in todo:
            v = split_repo.append_version(
                conn,
                tenant_id=p.member.tenant_id,
                security_id=p.member.security_id,
                key_date=p.decision.key_date,
                values=p.values,
                action=split_repo.ACTION_BACKFILL,
                author=AUTHOR_BACKFILL,
            )
            stamp = v.recorded_at
        after = split_repo.count_rows(conn)
        if after - before != len(todo):
            raise RuntimeError(
                f"the split list grew by {after - before} rows, the plan was {len(todo)} — rolled back"
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    result.applied = True
    result.appended = len(todo)
    result.rows_before, result.rows_after = before, after
    result.recorded_at = stamp
    return result


# --- the report ----------------------------------------------------------------------------------------------


def _snippet(s: str | None, n: int = 260) -> str:
    if not s:
        return "—"
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def report(result: BackfillResult, *, out: Any = None) -> None:
    w = (out or sys.stdout).write
    for p in sorted(
        result.planned,
        key=lambda p: (
            p.decision.status != STATUS_NEEDS_REVIEW,
            p.member.ticker or "",
            p.decision.key_date,
        ),
    ):
        d = p.decision
        via = f" ({d.confirmed_via})" if d.confirmed_via else ""
        verb = {
            APPEND: "APPEND" + (f" (changed: {', '.join(p.changed)})" if p.changed else " (new)"),
            UNCHANGED: "unchanged — nothing to append",
            OPERATOR_KEPT: f"operator decision stands ({p.stored.status if p.stored else '?'}) — untouched",
        }[p.action]
        w(
            f"  {p.member.ticker or p.member.security_id}  {ratio_label(d.ratio_from, d.ratio_to)}  first "
            f"session {d.effective_date.isoformat()}  {d.status.upper()}{via}  -> {verb}\n"
        )
        poly = d.polygon
        w(
            f"      Polygon {poly.ticker} {poly.id[:14]}…: {poly.split_from} -> {poly.split_to} on "
            f"{poly.execution_date.isoformat()}\n"
        )
        s = d.sec
        if s is not None:
            ratio = (
                f"{s.ratio_from} -> {s.ratio_to}"
                if s.has_ratio
                else f"ABSTAIN ({s.abstain_reason})"
            )
            when = (
                f"{s.date_kind} {s.effective_date.isoformat()}" if s.effective_date else "no date"
            )
            w(f"      SEC {s.form} {s.accession} filed {s.filed.isoformat()}: {ratio}, {when}\n")
            w(f"      passage: {_snippet(s.passage)}\n")
        else:
            w("      SEC: —\n")
        w(
            f"      filings read {p.filings_read}"
            + (f", unread {p.filings_unread}" if p.filings_unread else "")
            + f" · note: {_snippet(d.note, 400)}\n"
        )
    for who, err in result.submissions_failed.items():
        w(f"  {who}: SEC filing index unread — {err}\n")
    st = result.by_status()
    w(
        f"POLYGON: {result.polygon_rows} rows in {result.polygon_pages} page(s) since "
        f"{result.since.isoformat()} ({result.polygon_skipped} unreadable) · {result.polygon_matched} on the "
        f"{result.members} union member(s) -> {len(result.planned)} split(s)\n"
    )
    w(
        "STATUS: "
        + " · ".join(f"{k} {v}" for k, v in st.items())
        + f" · SEC live fetches {result.sec_live_fetches}\n"
    )
    verb = "appended" if result.applied else "would append"
    n = result.appended if result.applied else result.count(APPEND)
    tail = ""
    if result.applied:
        tail = f" · table {result.rows_before} -> {result.rows_after} rows" + (
            f" · recorded_at {result.recorded_at.isoformat()}" if result.recorded_at else ""
        )
    w(
        f"SUMMARY: {verb} {n} · unchanged {result.count(UNCHANGED)} · operator decisions kept "
        f"{result.count(OPERATOR_KEPT)} · stored splits not seen this run {result.stored_not_seen}{tail}\n"
    )


# --- the CLI -------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    from db.session import connect

    p = argparse.ArgumentParser(
        description="Split plan S1: fill the split list from Polygon's split reference, each split checked "
        "against its SEC filing (auto-confirmed only when both agree). Dry-run by default."
    )
    p.add_argument(
        "--since",
        type=date.fromisoformat,
        default=DEFAULT_SINCE,
        help=f"splits with an execution date on/after this day (default {DEFAULT_SINCE.isoformat()})",
    )
    p.add_argument(
        "--asof",
        type=date.fromisoformat,
        default=None,
        help="the market day 'announced' is judged against (default: today in market time)",
    )
    p.add_argument("--ticker", action="append", default=None, help="only this member (repeatable)")
    p.add_argument("--apply", action="store_true", help="append (default: dry run, read-only)")
    p.add_argument(
        "--no-live", action="store_true", help="read the caches only (no SEC / Polygon request)"
    )
    args = p.parse_args(argv)

    polygon = default_split_source()
    if polygon is None:
        print("backfill_splits: OFF — no POLYGON_API_KEY is set, so nothing was fetched or written")
        return
    if args.since > market_today():
        print(f"REFUSED: --since {args.since.isoformat()} is in the future (nothing was written)")
        raise SystemExit(EXIT_REFUSED)
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    print(
        f"backfill_splits · {mode} · since {args.since.isoformat()} · "
        f"{'cache only' if args.no_live else 'live SEC + Polygon'}"
    )
    try:
        lock = acquire_pass_lock(holder="backfill_splits") if args.apply else None
    except PassAlreadyRunning as exc:
        print(
            f"backfill_splits: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            edgar = EdgarClient(allow_live=not args.no_live, max_per_sec=SEC_RATE_PER_SEC)
            try:
                result = backfill_splits(
                    conn,
                    polygon=polygon,
                    edgar=edgar,
                    since=args.since,
                    asof=args.asof,
                    apply=args.apply,
                    allow_live=not args.no_live,
                    tickers=args.ticker,
                )
            except PolygonSplitsError as exc:
                print(f"ABORTED: {exc} — nothing was written")
                raise SystemExit(1) from None
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()
    report(result)


if __name__ == "__main__":
    main()
