"""Backfill: HOLD the Form 4 rows an earlier ingest dropped because their transaction date cannot be true.

THE GAP. Until migration 0082 the ingest printed such a row and stored it nowhere (``ingest.edgar.form4``); the
repair before it (``pipeline.repair_impossible_txn_dates``) deleted the ones already stored. So a filing that
stored its good rows and dropped a bad one left no trace of the bad one. The ingest now writes a refused row to
``insider_txn_hold``, but it never re-reads a filing it already stored — this tool does that re-read, once.

WHAT IT DOES. For every accession stored in ``fact_insider_txn`` it re-reads the CACHED Form 4 document
(``forms/<accession>/<doc>`` under the EDGAR cache on disk — never the network) with the ingest's own parser
and the ingest's own rule (``parse_form4`` + ``held_rows``, behind the same other-issuer screen), and plans a
hold for each row the rule refuses that has no hold yet. An accession with no cached document is counted and
listed as "not cached", never guessed.

``--accessions A,B,...`` limits the run to those accessions. It also accepts an accession with NO stored row —
a filing whose every row the rule refuses stores nothing — placing it on the company ``--ticker`` names, else
on the company whose master CIK is the filing's issuer CIK. One it cannot place is listed and skipped. So is an
un-stored filing that still has a row the ingest would STORE: holding its bad row here would mark the filing
"already handled" (``existing_accessions``) and the nightly would never store its good rows (#9) — the nightly
reads that filing itself and holds the bad row in the same transaction.

A hold is NOT a fact: nothing on the call path, no signal and no dashboard reads ``insider_txn_hold``, and no
corrected date is invented (#3). The nightly's ``insider_holds`` block names each new hold once
(``pipeline/insider_hold_scan.py``).

SAFETY.
- **Dry-run is the default**: it prints one line per planned hold (ticker, accession, row number, person, code,
  shares, price, the date as filed, the filing's period of report and signature date, the reason), then a
  summary by reason. Nothing is written.
- **The scan takes no lock** and holds no database connection: the worklist is read up front, then the scan is
  file reads only (it is long — prod stores about 424,000 accessions — and prints its progress every 50,000).
- ``--apply`` takes the REPAIR lock (call-of-record + the fact-writer gate; exit 75 when a pass, a backfill or
  another repair holds it) only for the write: ``INSERT ... ON CONFLICT DO NOTHING`` in ONE transaction, the
  table counted before and after. A count that disagrees with the rows written ROLLS BACK and exits non-zero.
  One ``data_repair`` journal row. Idempotent: a second ``--apply`` writes nothing.

    python -m pipeline.backfill_insider_holds                              # dry-run, every stored accession
    python -m pipeline.backfill_insider_holds --accessions A,B             # dry-run, only these
    python -m pipeline.backfill_insider_holds --accessions A --ticker T    # an un-stored filing, placed on T
    python -m pipeline.backfill_insider_holds --apply                      # write (the operator, after a backup)
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import connect, database_url
from domain.scopes import DATA_TENANT_ID
from ingest.edgar.form4 import (
    HOLD_FORM_4,
    HoldEvidence,
    _norm_cik,
    held_rows,
    hold_evidence,
    hold_row,
    other_issuer_cik,
    parse_form4,
    write_hold,
)
from pipeline.backfill_aff10b5_1 import cached_form_doc
from pipeline.repair_insider_ownership import _redact
from pipeline.run_lock import repair_lock_or_exit
from repositories import data_repair_repo

TOOL = "backfill_insider_holds"
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "edgar_cache"
PROGRESS_EVERY = 50_000


@dataclass(frozen=True)
class Company:
    """One master row, as the scan needs it."""

    security_id: UUID
    ticker: str | None
    cik: str | None


@dataclass
class PlannedHold:
    """One row the rule refuses that has no hold yet — everything the dry-run prints and ``--apply`` writes."""

    company: Company
    accession: str
    txn_seq: int
    reason: str  # the rule's code
    row: dict[str, Any]  # the row as ``parse_form4`` returned it
    evidence: HoldEvidence
    filed: date | None = None  # the filing date, when the filing index holds it
    accepted: datetime | None = None  # the acceptance the ingest stored on the filing's good rows

    @property
    def date_as_filed(self) -> str | None:
        dates = self.evidence.txn_dates
        return dates[self.txn_seq] if self.txn_seq < len(dates) else None

    @property
    def line(self) -> str:
        r = self.row
        return (
            f"  {self.company.ticker or self.company.security_id} {self.accession} row {self.txn_seq} "
            f"{r.get('insider_name') or '?'} ({r.get('txn_code') or '?'}) shares {_n(r.get('shares'))} "
            f"price {_n(r.get('price'))}: date as filed {self.date_as_filed or '?'} · period of report "
            f"{self.evidence.period_of_report or '?'} · signed {self.evidence.signature_date or '?'} · "
            f"{self.reason}"
        )


def _n(v: Any) -> str:
    return "?" if v is None else f"{v:g}" if isinstance(v, float) else str(v)


@dataclass
class Plan:
    """One scan's full accounting — printed so the operator counts every filing, not only the holds."""

    scanned: int = 0  # (company, accession) pairs read
    planned: list[PlannedHold] = field(default_factory=list)
    already_held: int = 0  # rows the rule refuses that already have a hold (a re-run)
    not_cached: list[str] = field(default_factory=list)
    unparseable: list[str] = field(default_factory=list)
    other_issuer: list[str] = field(
        default_factory=list
    )  # about another company: nothing stored or held
    unplaced: list[str] = field(
        default_factory=list
    )  # an un-stored accession with no company to put it on
    left_to_ingest: list[str] = field(
        default_factory=list
    )  # un-stored, with a row the ingest would store

    @property
    def by_reason(self) -> Counter:
        return Counter(p.reason for p in self.planned)

    @property
    def filings(self) -> int:
        return len({(p.company.security_id, p.accession) for p in self.planned})

    def as_json(self) -> dict[str, Any]:
        """The whole accounting (``--report-json``): every list in full, where the printed report clips."""
        return {
            "scanned": self.scanned,
            "already_held": self.already_held,
            "by_reason": dict(self.by_reason),
            "planned": [
                {
                    "ticker": p.company.ticker,
                    "security_id": str(p.company.security_id),
                    "accession": p.accession,
                    "txn_seq": p.txn_seq,
                    "reason": p.reason,
                    "row": hold_row(p.row, p.date_as_filed),
                    "period_of_report_as_filed": p.evidence.period_of_report,
                    "signature_date_as_filed": p.evidence.signature_date,
                    "filed": p.filed,
                    "accepted": p.accepted,
                }
                for p in self.planned
            ],
            "not_cached": self.not_cached,
            "unparseable": self.unparseable,
            "other_issuer": self.other_issuer,
            "unplaced": self.unplaced,
            "left_to_ingest": self.left_to_ingest,
        }


# --- the short database reads (before and after the scan) -------------------------------------------------


def stored_pairs(
    conn: psycopg.Connection, tenant_id: UUID, accessions: Sequence[str] | None
) -> list[tuple[UUID, str]]:
    """Every ``(security, accession)`` stored in ``fact_insider_txn`` (optionally only ``accessions``), in
    accession order."""
    scope = "" if accessions is None else "AND accession = ANY(%(accs)s)"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT security_id, accession FROM fact_insider_txn "  # noqa: S608 — a fixed clause
            f"WHERE tenant_id = %(t)s {scope} ORDER BY accession, security_id",
            {"t": tenant_id, "accs": list(accessions) if accessions is not None else None},
        )
        return [(r["security_id"], r["accession"]) for r in cur.fetchall()]


def held_keys(conn: psycopg.Connection, tenant_id: UUID) -> set[tuple[UUID, str, int]]:
    """``(security, accession, txn_seq)`` of every Form 4 hold already written."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, accession, txn_seq FROM insider_txn_hold "
            "WHERE tenant_id = %s AND form = %s",
            (tenant_id, HOLD_FORM_4),
        )
        return {(r["security_id"], r["accession"], r["txn_seq"]) for r in cur.fetchall()}


def companies(conn: psycopg.Connection, tenant_id: UUID) -> dict[UUID, Company]:
    """The tenant's master rows (id, ticker, CIK)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker, cik FROM security_master WHERE tenant_id = %s", (tenant_id,)
        )
        return {r["id"]: Company(r["id"], r["ticker"], r["cik"]) for r in cur.fetchall()}


def _stamp_filing_facts(
    conn: psycopg.Connection, tenant_id: UUID, planned: list[PlannedHold]
) -> None:
    """Fill each planned hold's ``accepted`` (the acceptance the ingest stored on the filing's good rows — the
    newest version) and ``filed`` (the filing index's date for the accession). Either stays ``None`` when the
    store does not hold it: never guessed."""
    if not planned:
        return
    accs = sorted({p.accession for p in planned})
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (security_id, accession) security_id, accession, accepted "
            "FROM fact_insider_txn WHERE tenant_id = %s AND accession = ANY(%s) "
            "ORDER BY security_id, accession, recorded_at DESC, id DESC",
            (tenant_id, accs),
        )
        accepted = {(r["security_id"], r["accession"]): r["accepted"] for r in cur.fetchall()}
        cur.execute(
            "SELECT DISTINCT ON (security_id, accession) security_id, accession, filed "
            "FROM fact_filing_index WHERE tenant_id = %s AND accession = ANY(%s) "
            "ORDER BY security_id, accession, recorded_at DESC, id DESC",
            (tenant_id, accs),
        )
        filed = {(r["security_id"], r["accession"]): r["filed"] for r in cur.fetchall()}
    for p in planned:
        key = (p.company.security_id, p.accession)
        p.accepted = accepted.get(key)
        p.filed = filed.get(key)


# --- the scan (file reads only: no database, no network) ---------------------------------------------------


def _read(cache_dir: Path, accession: str, plan: Plan, log: Callable[[str], None]) -> str | None:
    doc = cached_form_doc(cache_dir, accession)
    if doc is None:
        plan.not_cached.append(accession)
        return None
    try:
        return doc.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        plan.unparseable.append(accession)
        log(
            f"  warn: {accession} unreadable ({e.__class__.__name__}: {e}) — nothing planned for it"
        )
        return None


def _plan_filing(
    plan: Plan,
    company: Company,
    accession: str,
    xml: str,
    held: set[tuple[UUID, str, int]],
    *,
    stored: bool,
    log: Callable[[str], None],
) -> None:
    """Plan one filing's holds under the ingest's own reading: ``parse_form4`` and ``held_rows``, behind the
    other-issuer screen. The screen is checked only for a filing with a refused row (it changes nothing for
    any other), which spares the long scan one parse per document."""
    try:
        rows = parse_form4(xml)
        refused = held_rows(rows, accession)
        if not refused:
            return
        if other_issuer_cik(xml, company.cik) is not None:
            # about another company: the ingest stores nothing for it, so it holds nothing
            plan.other_issuer.append(accession)
            return
        evidence = hold_evidence(xml)
    except (ET.ParseError, ValueError) as e:
        plan.unparseable.append(accession)
        log(
            f"  warn: {accession} unparseable ({e.__class__.__name__}: {e}) — nothing planned for it"
        )
        return
    if not stored:
        # an un-stored filing may be held here only when the ingest would store NONE of its rows (#9)
        storable = [
            i for i, t in enumerate(rows) if t.get("txn_date") is not None and i not in refused
        ]
        if storable:
            plan.left_to_ingest.append(accession)
            return
    for seq, reason in sorted(refused.items()):
        if (company.security_id, accession, seq) in held:
            plan.already_held += 1
            continue
        plan.planned.append(PlannedHold(company, accession, seq, reason, rows[seq], evidence))


def _issuer_cik(xml: str) -> str | None:
    try:
        return _norm_cik(ET.fromstring(xml).findtext("issuer/issuerCik"))
    except ET.ParseError:
        return None


def scan(
    pairs: Sequence[tuple[UUID, str]],
    *,
    unstored: Sequence[str] = (),
    place_on: Company | None = None,
    master: dict[UUID, Company],
    held: set[tuple[UUID, str, int]],
    cache_dir: Path,
    log: Callable[[str], None] = print,
    progress_every: int = PROGRESS_EVERY,
) -> Plan:
    """Re-read every stored ``(security, accession)`` pair's cached document, and each ``unstored`` accession
    (a filing with no stored row — placed on ``place_on``, else on the one master row whose CIK is the
    filing's issuer CIK). Pure file reads: no database, no network."""
    if not (cache_dir / "forms").is_dir():
        raise SystemExit(f"no forms/ under cache dir {cache_dir} — wrong --cache-dir? refusing")
    plan = Plan()
    for sid, accession in pairs:
        plan.scanned += 1
        if plan.scanned % progress_every == 0:
            log(f"  ... {plan.scanned:,} of {len(pairs):,} stored accessions read")
        xml = _read(cache_dir, accession, plan, log)
        if xml is None:
            continue
        company = master.get(sid) or Company(sid, None, None)
        _plan_filing(plan, company, accession, xml, held, stored=True, log=log)
    by_cik: dict[str, list[Company]] = {}
    for c in master.values():
        key = _norm_cik(c.cik)
        if key:
            by_cik.setdefault(key, []).append(c)
    for accession in unstored:
        plan.scanned += 1
        xml = _read(cache_dir, accession, plan, log)
        if xml is None:
            continue
        company = place_on
        if company is None:
            matches = by_cik.get(_issuer_cik(xml) or "", [])
            company = matches[0] if len(matches) == 1 else None
        if company is None:
            plan.unplaced.append(accession)
            continue
        _plan_filing(plan, company, accession, xml, held, stored=False, log=log)
    return plan


# --- the write ---------------------------------------------------------------------------------------------


def _count(conn: psycopg.Connection, tenant_id: UUID) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_txn_hold WHERE tenant_id = %s", (tenant_id,))
        return cur.fetchone()["n"]


def apply_plan(conn: psycopg.Connection, tenant_id: UUID, planned: Sequence[PlannedHold]) -> int:
    """Write the planned holds in ONE transaction (uncommitted — the caller commits) and verify the table
    grew by exactly the rows written. A row another writer held since the scan is an ``ON CONFLICT DO
    NOTHING`` no-op, not a mismatch. Returns the rows written; on a count mismatch it ROLLS BACK and exits 1.
    """
    before = _count(conn, tenant_id)
    written = 0
    for p in planned:
        if write_hold(
            conn,
            tenant_id=tenant_id,
            security_id=p.company.security_id,
            accession=p.accession,
            txn_seq=p.txn_seq,
            form=HOLD_FORM_4,
            reason=p.reason,
            row=p.row,
            evidence=p.evidence,
            filed=p.filed,
            accepted=p.accepted,
        ):
            written += 1
    after = _count(conn, tenant_id)
    if after - before != written:
        conn.rollback()
        print(
            f"\nABORTED: the writes reported {written} row(s) but insider_txn_hold grew by "
            f"{after - before} ({before} -> {after}) — ROLLED BACK, nothing written"
        )
        raise SystemExit(1)
    return written


# --- the report --------------------------------------------------------------------------------------------


def _listed(label: str, items: Sequence[str], log: Callable[[str], None], show: int = 40) -> None:
    if not items:
        return
    more = f" … and {len(items) - show} more" if len(items) > show else ""
    log(f"  {label}: {len(items)} — {', '.join(items[:show])}{more}")


def report(
    plan: Plan, *, applied: bool, written: int = 0, log: Callable[[str], None] = print
) -> None:
    mode = "APPLY" if applied else "DRY-RUN (nothing written)"
    log(f"\n=== backfill insider holds — {mode} ===")
    for p in plan.planned:
        log(p.line)
    verb = "held" if applied else "to hold"
    n = written if applied else len(plan.planned)
    log(
        f"\nSUMMARY: {plan.scanned:,} accession(s) read · {n} row(s) {verb} in {plan.filings} filing(s) · "
        f"{plan.already_held} already held"
    )
    for reason, count in sorted(plan.by_reason.items()):
        log(f"  {reason}: {count}")
    _listed("not cached (nothing planned, never guessed)", plan.not_cached, log)
    _listed("unparseable (nothing planned)", plan.unparseable, log)
    _listed("about another issuer (the ingest stores nothing for these)", plan.other_issuer, log)
    _listed("could not be placed on a company (skipped; try --ticker)", plan.unplaced, log)
    _listed(
        "not stored yet and holding rows the ingest would store (left to the ingest, which holds the "
        "bad row itself)",
        plan.left_to_ingest,
        log,
    )
    if applied and written != len(plan.planned):
        log(
            f"  {len(plan.planned) - written} planned row(s) were already held by the time of the write "
            "(another writer held them since the scan)"
        )
    if not applied:
        log("  rerun with --apply to write the holds (the operator, after a backup)")


# --- the CLI -----------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Hold the Form 4 rows an earlier ingest dropped because their transaction date cannot be "
        "true: re-read each stored accession's CACHED document under the ingest's own rule and write the "
        "refused rows to insider_txn_hold. Dry-run by default; --apply writes."
    )
    p.add_argument("--apply", action="store_true", help="write the holds (default: dry-run)")
    p.add_argument(
        "--accessions",
        default=None,
        help="comma-separated accessions: only these (also accepts ones with no stored row)",
    )
    p.add_argument(
        "--ticker", default=None, help="the company an un-stored --accessions filing belongs to"
    )
    p.add_argument(
        "--cache-dir", type=Path, default=_DEFAULT_CACHE, help="EDGAR cache root holding forms/"
    )
    p.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="also write the whole accounting (every list in full) to this file",
    )
    p.add_argument("--tenant", default=str(DATA_TENANT_ID))
    args = p.parse_args(argv)
    tenant = UUID(args.tenant)
    wanted = (
        [a.strip() for a in args.accessions.split(",") if a.strip()] if args.accessions else None
    )
    if args.ticker and not wanted:
        raise SystemExit("--ticker places an un-stored filing: give it with --accessions")
    print(f"{TOOL} · target {_redact(database_url())} · cache {args.cache_dir}")

    # the short reads, then the connection is closed: the long scan is file reads only and takes no lock
    conn = connect()
    try:
        conn.read_only = True
        pairs = stored_pairs(conn, tenant, wanted)
        master = companies(conn, tenant)
        held = held_keys(conn, tenant)
        conn.rollback()
    finally:
        conn.close()
    stored_accessions = {a for _, a in pairs}
    unstored = [a for a in (wanted or []) if a not in stored_accessions]
    place_on: Company | None = None
    if args.ticker:
        named = [c for c in master.values() if (c.ticker or "").upper() == args.ticker.upper()]
        if len(named) != 1:
            raise SystemExit(
                f"--ticker {args.ticker} matches {len(named)} master row(s) — need exactly one"
            )
        place_on = named[0]
    scope = f"{len(wanted)} named accession(s)" if wanted else "every stored accession"
    print(f"scope: {scope} — {len(pairs):,} stored, {len(unstored)} with no stored row")
    plan = scan(
        pairs,
        unstored=unstored,
        place_on=place_on,
        master=master,
        held=held,
        cache_dir=args.cache_dir,
    )

    if not args.apply:
        conn = connect()
        try:
            conn.read_only = True
            _stamp_filing_facts(conn, tenant, plan.planned)
            conn.rollback()
        finally:
            conn.close()
        report(plan, applied=False)
        _write_report(args.report_json, plan)
        return

    # W1 — the write excludes the nightly pass AND every company writer (pipeline.run_lock); exit 75 if busy
    lock = repair_lock_or_exit(holder=TOOL)
    try:
        conn = connect()
        try:
            _stamp_filing_facts(conn, tenant, plan.planned)
            written = apply_plan(conn, tenant, plan.planned)
            conn.commit()
            report(plan, applied=True, written=written)
            # S7 — the data-repair journal: one row per --apply, under the lock (nothing is deleted)
            data_repair_repo.journal(
                conn,
                tool=TOOL,
                summary=(
                    f"held {written} insider row(s) whose transaction date cannot be true "
                    "(insider_txn_hold; no fact changed)"
                ),
                rows_changed=written,
                deletes=False,
                counts={"held": written, "filings": plan.filings, **dict(plan.by_reason)},
                tenant_id=tenant,
            )
        finally:
            conn.close()
    finally:
        lock.release()
    _write_report(args.report_json, plan)


def _write_report(path: Path | None, plan: Plan) -> None:
    if path is None:
        return
    path.write_text(json.dumps(plan.as_json(), indent=2, default=str), encoding="utf-8")
    print(f"  accounting written to {path}")


if __name__ == "__main__":
    main()
