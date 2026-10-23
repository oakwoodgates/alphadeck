"""``pipeline.set_aside_fundamentals`` — set aside the stored revenue rows #417's data-quality screen would have
refused, so every read skips them (migration 0062). Reversible; the rows are kept.

WHAT IT REPAIRS (the #417 report's A8). The revenue-label fix added a data-quality screen to the fundamentals
ingest (``ingest.fundamentals._label_series``): three row shapes no genuine quarter can have are refused —

- ``fy_total_in_quarter`` — a fiscal-year total under a quarter context (VICR's FY2025 10-K tags Q3-2025 as the
  year's $407.7M);
- ``year_in_q4_context`` — an annual report's Q4-context row standing in for the year (LHX's FY2025);
- ``q4_restated_fy_basis`` — a derived Q4 (FY − 9-month) across an FY restatement (LFMD's $5.5M "Q4").

``fact_fundamentals`` is append-only, so the rows the OLD ingest stored before the screen existed are still
read. This CLI finds them by running #417's OWN screen — the same function, unchanged — over each company's
cached companyfacts, under every revenue label (``REVENUE_CONCEPTS``: the old ingest stored the first label
present, the new one the current label), and matching each rejected row to the STORED version it describes:
same company, period, filing (``valid_from``), accession, basis and exact value. A stored row that names its tag
(``source = companyfacts:<concept>``) is matched only against that tag's screen.

NO LOOKAHEAD — the A2 rule, inherited, not re-implemented. The screen judges each row only against rows filed on
or before that row's own filing, so a set-aside version is exactly one the ingest screen would have refused on
the day it was filed; the read therefore skips it at EVERY asof (``db.set_aside``). Nothing a later filing says
decides whether an earlier public quarter exists.

RECALL (#9). A stored number that a DIFFERENT revenue label's screened series keeps — the same period, filing
and value, genuine under that label — is never set aside: it is reported as a CONFLICT and left in the read.
A company whose companyfacts is not cached cannot be judged: it is reported, and nothing of it is set aside.

WHAT IT WRITES — one ``fundamentals_set_aside`` row per version (``action = 'set_aside'``): the screen's reason
code, the rule version (``fundamentals-quality-screen@<image sha>``), the author (``system:set_aside_fundamentals``),
``recorded_at`` = the database instant, and the evidence (value, the figure it was judged against, the label,
whether a same-filing re-parse already outranks it). The fact rows are never touched.

SAFETY (the ``reingest_fundamentals`` / ``annotate_phantom_arms`` shape):

- **Dry-run is the DEFAULT.** Its connection is READ ONLY, it never goes to the network (cache only, always),
  and it prints every version it would set aside: company, period, value, filing, reason.
- **The repair lock.** ``--apply`` holds the REPAIR lock (``pipeline/run_lock.repair_lock_or_exit``: the
  call-of-record lock, then the fact-writer gate) for the whole run — exit 75 when a nightly pass, a backfill or
  a draining company writer holds it — counts the set-aside table before and after inside ONE transaction, and
  commits only when the difference equals the plan.
- **Idempotent.** A version this rule FAMILY already judged — active or revoked — is never judged again, so a
  second ``--apply`` appends zero (count the table) and an operator's revoke sticks across re-runs and images.
- **Reversible.** ``--revoke <id> ...`` / ``--revoke-all`` (both need ``--reason``; dry-run unless ``--apply``)
  append a ``revoke`` row per set-aside; the read then sees the version again.
- **Provenance.** ``--apply`` (and an applied revoke) writes one JSON under ``data/repairs/`` (fail-open).
- Run ``--apply`` on prod only after a labeled backup, from the deployed image, outside 21:30-00:00 ET.

    python -m pipeline.set_aside_fundamentals                             # dry run, every stored company
    python -m pipeline.set_aside_fundamentals --ticker TSNDF,FRHC         # named companies only
    python -m pipeline.set_aside_fundamentals --apply                     # append the set-asides
    python -m pipeline.set_aside_fundamentals --revoke <id> --reason "..." --apply
    python -m pipeline.set_aside_fundamentals --revoke-all --reason "..." --apply
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
from domain.settings import get_settings
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import REVENUE_CONCEPTS
from ingest.fundamentals import (
    REVENUE_METRIC,
    SCREEN_RESTATED_FY_Q4,
    _label_series,
    _stampable_usd_duration_rows,
)
from pipeline.reingest_fundamentals import CACHED, NO_CIK, active_securities, load_companyfacts
from pipeline.run_lock import repair_lock_or_exit
from repositories import data_repair_repo
from repositories import fundamentals_set_aside_repo as repo

_log = logging.getLogger("alphadeck.set_aside_fundamentals")

RULE_FAMILY = "fundamentals-quality-screen"
REVOKE_RULE_FAMILY = "operator-revoke"
AUTHOR = "system:set_aside_fundamentals"
REVOKE_AUTHOR = "operator:revoke"

# The status of a matched version under this rule family.
NEW = "new"  # never judged: --apply sets it aside
ACTIVE = "active"  # already set aside (an earlier --apply)
REVOKED = "revoked"  # set aside once and revoked: left as the operator decided

# Provenance lives beside the other repairs' (the repo's gitignored data/ == the container's /data).
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "repairs"

# A screened row's key: (period_end, filed, accession, basis, value) — exactly the stored version's columns
# (period_end, valid_from, accession, basis, value). A kept point's key: (period_end, filed, value).
_Key = tuple[date, date, str, str, float]
_KeptKey = tuple[date, date, float]


def rule_identity() -> str:
    """The rule version stamped on every row: the family + the code that judged (``<family>@<image sha>``)."""
    return f"{RULE_FAMILY}@{get_settings().image_sha or 'unknown'}"


@dataclass(frozen=True)
class ScreenHit:
    """One revenue row #417's screen rejects under one label (``ingest.fundamentals.ScreenedRow`` + its label
    and the basis the old ingest stored it with)."""

    reason: str
    period_end: date
    filed: date
    accession: str
    basis: str  # 'derived' for q4_restated_fy_basis (FY − 9-month), else 'native'
    value: float
    reference: float  # the figure the screen judged it against (the FY total / the 9-month YTD)
    label: str  # the us-gaap concept whose rows the screen judged


def screen_hits(
    companyfacts: dict[str, Any],
) -> tuple[dict[str, dict[_Key, ScreenHit]], dict[str, set[_KeptKey]]]:
    """PURE: #417's own screen under EVERY revenue label -> (per label: the rows it rejects, keyed like a stored
    version; per label: the points it keeps). ``_label_series`` is called unchanged, so each judgment uses only
    rows filed on or before the judged row's own filing (the A2 rule) — never a later filing."""
    facts = companyfacts.get("facts", {})
    hits: dict[str, dict[_Key, ScreenHit]] = {}
    kept: dict[str, set[_KeptKey]] = {}
    for tax, concept in REVENUE_CONCEPTS:
        rows = _stampable_usd_duration_rows(facts.get(tax, {}).get(concept))
        if not rows:
            continue
        points, screened = _label_series(rows)
        kept[concept] = {(p.period_end, p.filed, float(p.value)) for p in points}
        mine: dict[_Key, ScreenHit] = {}
        for x in screened:
            basis = "derived" if x.reason == SCREEN_RESTATED_FY_Q4 else "native"
            key = (x.period_end, x.filed, x.accession, basis, float(x.value))
            mine.setdefault(
                key,
                ScreenHit(
                    x.reason,
                    x.period_end,
                    x.filed,
                    x.accession,
                    basis,
                    float(x.value),
                    float(x.reference),
                    concept,
                ),
            )
        hits[concept] = mine
    return hits, kept


@dataclass
class Candidate:
    """One STORED version the screen rejects — what ``--apply`` sets aside (when ``status == NEW``)."""

    fact_id: UUID
    tenant_id: UUID
    security_id: UUID
    ticker: str | None
    period_end: date
    valid_from: date
    value: float
    accession: str
    basis: str
    source: str
    supersedes: UUID | None
    hit: ScreenHit
    # a later-recorded version of the SAME filing exists, so the read already does not pick this one (the
    # #417 report's "neutralized by a same-filing supersession") — set aside anyway: one rule, every row
    outranked: bool = False
    status: str = NEW
    set_aside_id: UUID | None = None  # the existing set_aside row (ACTIVE / REVOKED)


@dataclass(frozen=True)
class Conflict:
    """A stored version one label's screen rejects but ANOTHER label keeps with the same period, filing and
    value: genuine under that label, so never set aside (recall, #9) — reported, left in the read.
    """

    fact_id: UUID
    ticker: str | None
    period_end: date
    valid_from: date
    value: float
    reason: str
    screened_under: str
    kept_under: tuple[str, ...]


def _label_of(source: str) -> str | None:
    """``companyfacts:<concept>`` -> the concept a stored row names (``None`` for the old ingest's rows)."""
    return source.split(":", 1)[1] if source.startswith("companyfacts:") else None


def match_company(
    stored: Sequence[dict[str, Any]],
    companyfacts: dict[str, Any],
    *,
    ticker: str | None = None,
) -> tuple[list[Candidate], list[Conflict]]:
    """PURE: one company's stored revenue versions + its companyfacts -> (the versions the screen rejects, the
    conflicts left in the read). ``stored`` are ``fact_fundamentals`` rows (any order)."""
    hits, kept = screen_hits(companyfacts)
    # a version is OUTRANKED when a later version (recorded_at, id) of the same filing exists
    latest: dict[tuple[str, date, date], tuple[Any, str]] = {}
    for r in stored:
        k = (r["metric_key"], r["period_end"], r["valid_from"])
        order = (r["recorded_at"], str(r["id"]))
        if k not in latest or order > latest[k]:
            latest[k] = order
    out: list[Candidate] = []
    conflicts: list[Conflict] = []
    for r in stored:
        if r["metric_key"] != REVENUE_METRIC:
            continue
        value = float(r["value"])
        key = (r["period_end"], r["valid_from"], r["accession"], r["basis"], value)
        named = _label_of(r.get("source") or "")
        labels = [named] if named else list(hits)
        found = [hits[c][key] for c in labels if c in hits and key in hits[c]]
        if not found:
            continue
        hit = found[0]  # REVENUE_CONCEPTS order: deterministic
        kept_under = tuple(
            c for c, pts in kept.items() if (r["period_end"], r["valid_from"], value) in pts
        )
        if kept_under:
            conflicts.append(
                Conflict(
                    r["id"],
                    ticker,
                    r["period_end"],
                    r["valid_from"],
                    value,
                    hit.reason,
                    hit.label,
                    kept_under,
                )
            )
            continue
        k = (r["metric_key"], r["period_end"], r["valid_from"])
        out.append(
            Candidate(
                fact_id=r["id"],
                tenant_id=r["tenant_id"],
                security_id=r["security_id"],
                ticker=ticker,
                period_end=r["period_end"],
                valid_from=r["valid_from"],
                value=value,
                accession=r["accession"],
                basis=r["basis"],
                source=r.get("source") or "",
                supersedes=r.get("supersedes"),
                hit=hit,
                outranked=latest[k] != (r["recorded_at"], str(r["id"])),
            )
        )
    out.sort(key=lambda c: (c.period_end, c.valid_from))
    return out, conflicts


@dataclass
class CompanyScan:
    ticker: str | None
    security_id: UUID
    tenant_id: UUID
    cik: str | None
    source: str  # where its companyfacts came from (``reingest_fundamentals``'s vocabulary)
    in_active_basket: bool
    stored: int = 0  # stored revenue versions
    candidates: list[Candidate] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    error: str | None = None


@dataclass
class SetAsideResult:
    applied: bool
    rule: str
    companies: list[CompanyScan] = field(default_factory=list)
    rows_before: int | None = None
    rows_after: int | None = None
    appended: list[tuple[UUID, Candidate]] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def candidates(self, status: str | None = None) -> list[Candidate]:
        return [
            c
            for co in self.companies
            for c in co.candidates
            if status is None or c.status == status
        ]


def stored_companies(conn: psycopg.Connection, tickers: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Every security holding a stored revenue version — the whole store, not only today's baskets (an
    archived thesis or a backtest replays the others) — optionally narrowed to ``tickers``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT f.tenant_id, f.security_id, m.ticker, m.cik FROM fact_fundamentals f "
            "JOIN security_master m ON m.id = f.security_id WHERE f.metric_key = %s "
            "ORDER BY m.ticker, f.security_id",
            (REVENUE_METRIC,),
        )
        rows = cur.fetchall()
    wanted = {t.upper() for t in tickers}
    return [r for r in rows if not wanted or (r["ticker"] or "").upper() in wanted]


def stored_revenue(
    conn: psycopg.Connection, security_id: UUID, tenant_id: UUID
) -> list[dict[str, Any]]:
    """Every stored revenue version of one security (all of them — the set-aside read is not applied here: the
    screen judges what is STORED)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, tenant_id, security_id, metric_key, period_end, valid_from, value, accession, basis, "
            "source, supersedes, recorded_at FROM fact_fundamentals "
            "WHERE tenant_id = %s AND security_id = %s AND metric_key = %s ORDER BY period_end, recorded_at, id",
            (tenant_id, security_id, REVENUE_METRIC),
        )
        return cur.fetchall()


def _count_rows(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fundamentals_set_aside")
        return cur.fetchone()["n"]


def scan(
    conn: psycopg.Connection,
    *,
    client: EdgarClient | None = None,
    tickers: Sequence[str] = (),
) -> SetAsideResult:
    """Find every stored version the screen rejects (READ-ONLY; cache only, never the network). Each candidate
    carries its status under this rule family (``NEW`` / ``ACTIVE`` / ``REVOKED``)."""
    result = SetAsideResult(
        applied=False, rule=rule_identity(), started_at=datetime.now(timezone.utc)
    )
    client = client or EdgarClient(allow_live=False, cache_ttl_s=math.inf)
    in_basket = {s.id for s in active_securities(conn)}
    judged: dict[UUID, repo.SetAside] = {}
    for s in repo.all_for_rule_family(conn, RULE_FAMILY):
        judged[s.fact_id] = s  # oldest first: the newest judgment of a version wins
    active_ids = {s.id for s in repo.active(conn, family=RULE_FAMILY)}
    docs: dict[str, tuple[dict[str, Any] | None, str, str | None]] = {}
    for co in stored_companies(conn, tickers):
        scanned = CompanyScan(
            ticker=co["ticker"],
            security_id=co["security_id"],
            tenant_id=co["tenant_id"],
            cik=co["cik"],
            source=NO_CIK,
            in_active_basket=co["security_id"] in in_basket,
        )
        result.companies.append(scanned)
        stored = stored_revenue(conn, co["security_id"], co["tenant_id"])
        scanned.stored = len(stored)
        if not co["cik"]:
            continue
        if co["cik"] not in docs:
            docs[co["cik"]] = load_companyfacts(client, co["cik"], fetch=False)
        doc, source, err = docs[co["cik"]]
        scanned.source, scanned.error = source, err
        if doc is None:
            continue
        scanned.candidates, scanned.conflicts = match_company(stored, doc, ticker=co["ticker"])
        for c in scanned.candidates:
            prior = judged.get(c.fact_id)
            if prior is not None:
                c.status = ACTIVE if prior.id in active_ids else REVOKED
                c.set_aside_id = prior.id
    return result


def _detail(c: Candidate) -> dict[str, Any]:
    """The evidence stored on the set_aside row (and in the provenance)."""
    return {
        "ticker": c.ticker,
        "value": c.value,
        "reference": c.hit.reference,
        "accession": c.accession,
        "basis": c.basis,
        "label": c.hit.label,
        "stored_source": c.source,
        "screened_filed": c.hit.filed.isoformat(),
        "outranked_by_same_filing_reparse": c.outranked,
    }


def apply(conn: psycopg.Connection, result: SetAsideResult) -> SetAsideResult:
    """Append a set_aside for every ``NEW`` candidate in ONE transaction that counts the set-aside table before
    and after and commits only when the difference equals the plan (a mismatch rolls back and raises). The
    caller owns the connection and the lock."""
    todo = result.candidates(NEW)
    try:
        result.rows_before = _count_rows(conn)
        for c in todo:
            sid = repo.append_set_aside(
                conn,
                c.fact_id,
                reason=c.hit.reason,
                rule=result.rule,
                author=AUTHOR,
                detail=_detail(c),
            )
            result.appended.append((sid, c))
        result.rows_after = _count_rows(conn)
        if result.rows_after - result.rows_before != len(todo):
            raise RuntimeError(
                f"row-count mismatch (planned {len(todo)}, table {result.rows_before} -> "
                f"{result.rows_after}) — ROLLED BACK, nothing written"
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        result.appended = []
        raise
    result.applied = True
    result.finished_at = datetime.now(timezone.utc)
    return result


def revoke(
    conn: psycopg.Connection, ids: Sequence[UUID], *, reason: str, apply: bool
) -> list[UUID]:
    """Append one ``revoke`` per set_aside id (each must be ACTIVE — ``LookupError`` otherwise), in ONE
    transaction, counted; ``apply=False`` rolls back. Returns the revoke rows' ids."""
    rule = f"{REVOKE_RULE_FAMILY}@{get_settings().image_sha or 'unknown'}"
    try:
        before = _count_rows(conn)
        new = [repo.revoke(conn, i, reason=reason, rule=rule, author=REVOKE_AUTHOR) for i in ids]
        if _count_rows(conn) - before != len(ids):
            raise RuntimeError("revoke count mismatch — ROLLED BACK, nothing written")
        if apply:
            conn.commit()
        else:
            conn.rollback()
    except BaseException:
        conn.rollback()
        raise
    return new


# --- provenance + report -----------------------------------------------------------------------------------


def _iso(x: date | datetime | None) -> str | None:
    return x.isoformat() if x is not None else None


def _row(c: Candidate) -> dict[str, Any]:
    return {
        "fact_id": str(c.fact_id),
        "security_id": str(c.security_id),
        "ticker": c.ticker,
        "period_end": _iso(c.period_end),
        "filed": _iso(c.valid_from),
        "value": c.value,
        "reference": c.hit.reference,
        "accession": c.accession,
        "basis": c.basis,
        "reason": c.hit.reason,
        "label": c.hit.label,
        "outranked_by_same_filing_reparse": c.outranked,
        "status": c.status,
        "set_aside_id": str(c.set_aside_id) if c.set_aside_id else None,
    }


def build_payload(result: SetAsideResult) -> dict[str, Any]:
    """The provenance payload — PURE (no I/O)."""
    return {
        "kind": "set_aside_fundamentals",
        "rule": result.rule,
        "author": AUTHOR,
        "applied": result.applied,
        "started_at": _iso(result.started_at),
        "finished_at": _iso(result.finished_at),
        "set_aside_rows_before": result.rows_before,
        "set_aside_rows_after": result.rows_after,
        "appended": [{"set_aside_id": str(sid), **_row(c)} for sid, c in result.appended],
        "candidates": [_row(c) for c in result.candidates()],
        "conflicts": [
            {
                "fact_id": str(x.fact_id),
                "ticker": x.ticker,
                "period_end": _iso(x.period_end),
                "filed": _iso(x.valid_from),
                "value": x.value,
                "reason": x.reason,
                "screened_under": x.screened_under,
                "kept_under": list(x.kept_under),
            }
            for co in result.companies
            for x in co.conflicts
        ],
        "not_judged": [
            {"ticker": co.ticker, "security_id": str(co.security_id), "source": co.source}
            for co in result.companies
            if co.source != CACHED
        ],
    }


def write_payload(payload: dict[str, Any], base_dir: Path | None = None) -> Path | None:
    """Write the provenance JSON to ``<base>/<utc-timestamp>-set-aside-fundamentals.json``; return the path, or
    ``None`` fail-open (the committed rows ARE the repair; a provenance fault is logged, never raised).
    """
    try:
        run_dir = base_dir or _DEFAULT_LOG_DIR
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = run_dir / f"{stamp}-set-aside-fundamentals.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract
        _log.exception("set-aside provenance write failed (fail-open — the rows are committed)")
        return None


def report(result: SetAsideResult) -> None:
    """Every matched version (one line each), every conflict, every company that could not be judged, then the
    summary."""
    for co in result.companies:
        name = co.ticker or str(co.security_id)
        if co.source != CACHED:
            if co.source != NO_CIK or co.stored:
                print(
                    f"  {name}: not judged — companyfacts {co.source}"
                    + (f" ({co.error})" if co.error else "")
                )
            continue
        for c in co.candidates:
            tags = [c.hit.reason, f"label {c.hit.label}"]
            if c.outranked:
                tags.append("already outranked by a same-filing re-parse")
            if c.status == ACTIVE:
                tags.append("already set aside")
            elif c.status == REVOKED:
                tags.append("revoked by the operator — left in the read")
            print(
                f"  {name}: quarter ending {_iso(c.period_end)} · value {c.value:,.0f} "
                f"(vs {c.hit.reference:,.0f}) · filed {_iso(c.valid_from)} ({c.accession}) · "
                + " · ".join(tags)
            )
        for x in co.conflicts:
            print(
                f"  {name}: CONFLICT kept — quarter ending {_iso(x.period_end)} · value {x.value:,.0f} · filed "
                f"{_iso(x.valid_from)} · {x.reason} under {x.screened_under}, kept under "
                f"{', '.join(x.kept_under)}"
            )
    new = result.candidates(NEW)
    allc = result.candidates()
    companies = {c.security_id for c in allc}
    by_source: dict[str, int] = {}
    for co in result.companies:
        by_source[co.source] = by_source.get(co.source, 0) + 1
    sources = ", ".join(f"{k} {v}" for k, v in sorted(by_source.items()))
    reasons: dict[str, int] = {}
    for c in allc:
        reasons[c.hit.reason] = reasons.get(c.hit.reason, 0) + 1
    verb = "set aside" if result.applied else "would set aside"
    n = len(result.appended) if result.applied else len(new)
    basket = {c.security_id for c in allc if _in_basket(result, c.security_id)}
    print(
        f"SUMMARY: {len(result.companies)} securities with stored revenue ({sources}) · "
        f"{len(allc)} stored version(s) the screen rejects in {len(companies)} companies "
        f"({', '.join(f'{k} {v}' for k, v in sorted(reasons.items())) or 'none'}; "
        f"{sum(1 for c in allc if c.outranked)} already outranked by a same-filing re-parse; "
        f"{len(basket)} companies in an active basket) · {verb} {n} · "
        f"{len(result.candidates(ACTIVE))} already set aside · {len(result.candidates(REVOKED))} revoked (left) · "
        f"{sum(len(co.conflicts) for co in result.companies)} conflict(s) kept · set-aside rows "
        f"{result.rows_before} -> {result.rows_after}"
    )


def _in_basket(result: SetAsideResult, security_id: UUID) -> bool:
    return any(co.in_active_basket for co in result.companies if co.security_id == security_id)


# --- the CLI ---------------------------------------------------------------------------------------------


def _run_revoke(args: argparse.Namespace) -> None:
    if not args.reason or not args.reason.strip():
        raise SystemExit("--revoke / --revoke-all need --reason (the why is part of the record)")
    lock = repair_lock_or_exit(holder="set_aside_fundamentals") if args.apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply
            if args.revoke_all:
                ids = [s.id for s in repo.active(conn, family=RULE_FAMILY)]
            else:
                ids = [UUID(x) for x in args.revoke]
            print(
                f"revoke: {len(ids)} set-aside(s)"
                + ("" if args.apply else " · DRY-RUN (nothing written)")
            )
            for i in ids:
                print(f"  {i}")
            if not args.apply:
                conn.rollback()
                return
            new = revoke(conn, ids, reason=args.reason, apply=True)
            print(f"SUMMARY: revoked={len(new)}")
            payload = {
                "kind": "set_aside_fundamentals_revoke",
                "reason": args.reason,
                "author": REVOKE_AUTHOR,
                "revoked": [str(i) for i in ids],
                "revoke_rows": [str(i) for i in new],
                "at": datetime.now(timezone.utc).isoformat(),
            }
            provenance = write_payload(payload)
            print(f"provenance: {provenance}")
            # S7 — the data-repair journal: one row per applied --revoke, under the lock
            data_repair_repo.journal(
                conn,
                tool="set_aside_fundamentals",
                action="revoke",
                summary=f"revoked {len(new)} fundamentals set-aside(s) ({args.reason.strip()})",
                rows_changed=len(new),
                counts={"revoked": len(new)},
                provenance=str(provenance) if provenance else None,
            )
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Set aside the stored revenue rows #417's data-quality screen would have refused (an "
        "append-only, revocable fundamentals_set_aside row each; the fact rows are kept). Dry-run by default."
    )
    p.add_argument("--ticker", default=None, help="comma-separated tickers to limit the run to")
    p.add_argument(
        "--apply", action="store_true", help="write (default: dry-run, read-only, no network)"
    )
    p.add_argument("--revoke", nargs="+", default=None, help="revoke these set-aside id(s)")
    p.add_argument(
        "--revoke-all", action="store_true", help=f"revoke every active {RULE_FAMILY} set-aside"
    )
    p.add_argument("--reason", default=None, help="the why, required for a revoke")
    args = p.parse_args(argv)
    if args.revoke or args.revoke_all:
        _run_revoke(args)
        return
    tickers = [t.strip() for t in (args.ticker or "").split(",") if t.strip()]
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection, no network; nothing written)"
    print(f"set_aside_fundamentals · rule={rule_identity()} · {mode} · companyfacts: cache only")
    lock = repair_lock_or_exit(holder="set_aside_fundamentals") if args.apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            result = scan(conn, tickers=tickers)
            result.rows_before = result.rows_after = _count_rows(conn)
            conn.rollback()
            provenance = None
            if args.apply:
                apply(conn, result)
                provenance = write_payload(build_payload(result))
                # S7 — the data-repair journal: one row per --apply, under the lock
                data_repair_repo.journal(
                    conn,
                    tool="set_aside_fundamentals",
                    summary=(
                        f"set aside {len(result.appended)} stored revenue row(s) the data-quality "
                        "screen refuses"
                    ),
                    rows_changed=len(result.appended),
                    counts={"set_aside": len(result.appended)},
                    provenance=str(provenance) if provenance else None,
                )
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()
    report(result)
    if args.apply:
        print(f"provenance: {provenance}")


__all__ = [
    "RULE_FAMILY",
    "AUTHOR",
    "ScreenHit",
    "Candidate",
    "Conflict",
    "CompanyScan",
    "SetAsideResult",
    "rule_identity",
    "screen_hits",
    "match_company",
    "stored_companies",
    "stored_revenue",
    "scan",
    "apply",
    "revoke",
    "build_payload",
    "write_payload",
    "report",
    "main",
]


if __name__ == "__main__":
    main()
