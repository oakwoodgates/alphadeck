"""Repair: backfill each stored insider transaction's FILER TEXT (``ownership_nature``, ``txn_footnotes``,
``filing_natures`` — migration 0055) onto the rows a reader can act on, so an internal transfer stored before the
capture is screened and a private purchase gets its ledger note.

THE DEFECTS. (1) An internal transfer — James J. Kim's AMKR shares "distributed" to his children's Sujoda
Investments, LP, 869,565 sh @ $21.85 on 2025-02-24 — reads as a buy plus a sale. The read-time screen
(``domain/insider_internal_transfers.py``) links the two legs on the filings' own statement of whose shares they
report (``filing_natures``: Susan Y. Kim's purchase filing holds shares "By James J. Kim 2024 GRAT"), which the
stored rows never carried. (2) The Event Ledger's private-purchase note (``domain/insider_private_purchases.py``)
locates its passage in the footnotes the transaction cites (``txn_footnotes``), likewise never stored. The ingest
captures all three on new filings; this tool records them on the history.

WHICH ROWS — and why that set suffices:

1. TRANSFER CANDIDATES — every code-P / code-S row on a (security, date, shares, price) signature that holds a
   buy AND a sale of DIFFERENT reporting owners (Form 4 rows and 4/A rows alike). The screen's named-holding link
   reads ``filing_natures`` on exactly those rows; a signature whose buy and sale share ONE owner is linked on
   identity alone and needs no text. MEASURED on prod 2026-10-21 (read-only): 124 Form 4 rows (P 58 · S 66) and
   2 rows of two 4/A records.
2. PRIVATE-PURCHASE CANDIDATES — every code-P row whose OWN footnotes the note's phrase list locates
   (``private_purchase_note``). Found by re-parsing every code-P row's cached filing (26,573 Form 4 rows + 549 4/A
   rows on prod); only the rows the note can read are versioned.

THE PLAN, MEASURED 2026-10-21 (this tool's dry run against prod, read-only, over a copy of the cron's forms cache):
10,613 filings re-parsed -> 203 Form 4 rows (transfer 122, private purchase 86 — 5 both) + 1 4/A record WOULD be
versioned; 255 referenced filings are not in prod's cache (507 of their candidate rows are 4/A code-P rows, 2 are
transfer candidates) and stay bare; 0 unparseable, 0 mismatched, 1 whose filing adds nothing.

A version also fills the 0054 ownership evidence (``direct_indirect``, ``shares_owned_after``) where the stored row
lacks it — the screen's co-report rule reads ``direct_indirect`` — so one version carries all of it. A row whose
latest version already carries the capture (``filing_natures`` not NULL) is skipped; a candidate whose filing
gives it nothing new (no nature, no footnote, no indirect holding, evidence already present) is not versioned.

MECHANISM — re-parse the CACHED filing (``forms/<accession>/<doc>``, the immutable cache class; no SEC request)
with the SAME ``parse_form4`` the ingest uses, matched to each stored row by ``txn_seq`` and VERIFIED on date +
code + shares (a mismatch is printed and the row left as it is).

- ``fact_insider_txn`` — an append-only NEW VERSION of each target row: every column copied verbatim, only the
  captured columns set, ``supersedes`` = the old id, ``recorded_at`` = the database ``now()`` (never backdated).
  The as-of read serves it for any ``known_at`` at/after the run; a replay pinned earlier still reads what the
  system held then (#1).
- ``insider_amendment`` — a NEW VERSION of the 4/A record (every column verbatim) whose Table I rows carry the
  captured keys; the resolver reads the newest version.

RULES (the 0054 tool's, unchanged): NULL-only + idempotent (a re-run appends nothing — count the TABLE, not the
read); safe on what it cannot do (an uncached / unparseable filing, or a mismatched row, stays bare — and a bare
row is never screened); keep-not-delete; dry-run by default on a READ-ONLY connection; ``--apply`` writes under
the call-of-record lock (``pipeline/run_lock.py``; exit 75 if a daily pass or backfill holds it) and verifies the
appended counts.

    python -m pipeline.repair_insider_filer_text                    # DRY-RUN, the whole tape
    python -m pipeline.repair_insider_filer_text --ticker AMKR      # DRY-RUN, one member
    python -m pipeline.repair_insider_filer_text --apply            # write (the operator, after a backup)
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID, connect, database_url
from domain.insider_private_purchases import private_purchase_note
from ingest.edgar.form4 import parse_form4
from pipeline.backfill_aff10b5_1 import cached_form_doc
from pipeline.repair_adr_insider_misattribution import _require_security_scoped_natural_key
from pipeline.repair_insider_ownership import _JSONB_COLS, _redact, _same_txn, _tickers
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "edgar_cache"
TEXT = ("ownership_nature", "txn_footnotes", "filing_natures")
EVIDENCE = ("direct_indirect", "shares_owned_after")
TRANSFER, PRIVATE = "transfer", "private_purchase"

# Every latest-version Form 4 row of code P / S and every code-P / S row of the newest version of each 4/A record,
# with its owner (the CIK, leading zeros dropped, else the normalized name) and whether it already carries the
# capture. ``transfer`` marks the rows on a signature holding a P and an S of different owners.
_CANDIDATES = """
WITH latest AS (
    SELECT DISTINCT ON (tenant_id, security_id, accession, insider_name, valid_from, txn_seq)
           id, security_id, accession, insider_name, valid_from, txn_seq, txn_code, shares, price,
           rpt_owner_cik, filing_natures IS NOT NULL AS has_text
    FROM fact_insider_txn
    WHERE tenant_id = %(t)s AND txn_code IN ('P', 'S') {scope}
    ORDER BY tenant_id, security_id, accession, insider_name, valid_from, txn_seq, recorded_at DESC, id DESC
), am AS (
    SELECT DISTINCT ON (security_id, accession) id, security_id, accession, rows
    FROM insider_amendment
    WHERE tenant_id = %(t)s {scope}
    ORDER BY security_id, accession, recorded_at DESC, id DESC
), am_rows AS (
    SELECT am.id, am.security_id, am.accession, x->>'insider_name' AS insider_name,
           left(x->>'txn_date', 10)::date AS valid_from, (x->>'txn_seq')::int AS txn_seq,
           x->>'txn_code' AS txn_code, (x->>'shares')::numeric AS shares, (x->>'price')::numeric AS price,
           x->>'rpt_owner_cik' AS rpt_owner_cik, (x ? 'filing_natures') AS has_text
    FROM am CROSS JOIN LATERAL jsonb_array_elements(am.rows) AS x
    WHERE x->>'txn_date' IS NOT NULL AND x->>'txn_code' IN ('P', 'S')
), allrows AS (
    SELECT 'fact' AS src, id, security_id, accession, valid_from, txn_seq, txn_code, shares, price, has_text,
           COALESCE(NULLIF(ltrim(rpt_owner_cik, '0'), ''),
                    lower(regexp_replace(btrim(coalesce(insider_name, '')), '\\s+', ' ', 'g'))) AS owner
    FROM latest
    UNION ALL
    SELECT 'amendment', id, security_id, accession, valid_from, txn_seq, txn_code, shares, price, has_text,
           COALESCE(NULLIF(ltrim(rpt_owner_cik, '0'), ''),
                    lower(regexp_replace(btrim(coalesce(insider_name, '')), '\\s+', ' ', 'g')))
    FROM am_rows
), sig AS (
    SELECT security_id, valid_from, shares, price
    FROM allrows
    WHERE shares > 0
    GROUP BY security_id, valid_from, shares, price
    HAVING bool_or(txn_code = 'P') AND bool_or(txn_code = 'S') AND count(DISTINCT owner) > 1
)
SELECT a.src, a.id, a.security_id, a.accession, a.txn_seq, a.txn_code, a.valid_from, a.shares, a.price,
       a.has_text, (s.security_id IS NOT NULL) AS transfer
FROM allrows a
LEFT JOIN sig s
  ON s.security_id = a.security_id AND s.valid_from = a.valid_from AND s.shares = a.shares
 AND s.price IS NOT DISTINCT FROM a.price
WHERE a.txn_code = 'P' OR s.security_id IS NOT NULL
"""


@dataclass
class RepairResult:
    """One run's full accounting — printed so the operator counts the TABLES, not just the read."""

    applied: bool
    transfer_candidates: int = 0  # rows on a cross-owner P/S signature (both tables)
    p_rows_examined: int = 0  # code-P rows whose footnotes were read for a private-purchase passage
    already_captured: int = 0  # carrying the capture — skipped
    fact_rows_before: int = 0
    fact_rows_after: int = 0
    amendment_rows_before: int = 0
    amendment_rows_after: int = 0
    fact_rows_versioned: int = 0
    amendment_records_versioned: int = 0
    accessions: int = 0
    not_cached: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    seq_mismatch: list[str] = field(
        default_factory=list
    )  # "<acc>#<seq>" — date/code/shares disagree
    nothing_to_add: int = 0  # a transfer candidate whose filing adds no text and no evidence
    by_reason: Counter = field(
        default_factory=Counter
    )  # fact rows versioned per reason (transfer / note)
    by_security: Counter = field(default_factory=Counter)  # ticker -> rows + records versioned

    def as_json(self) -> dict[str, Any]:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}
        out["by_reason"] = dict(self.by_reason)
        out["by_security"] = dict(self.by_security)
        return out


def _count(conn: psycopg.Connection, table: str, tenant_id: UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table} WHERE tenant_id = %s", (tenant_id,)
        )  # noqa: S608
        return cur.fetchone()["n"]


def candidates(
    conn: psycopg.Connection, tenant_id: UUID, security_ids: list[UUID] | None = None
) -> list[dict[str, Any]]:
    """Every candidate row (module docstring): ``{src, id, security_id, accession, txn_seq, txn_code, valid_from,
    shares, price, has_text, transfer}`` — ``src`` ``'fact'`` (a ``fact_insider_txn`` latest version) or
    ``'amendment'`` (a row of the newest version of a 4/A record, ``id`` = that record's id); every code-P row,
    plus every code-S row on a transfer signature."""
    scope = "" if security_ids is None else "AND security_id = ANY(%(sids)s)"
    with conn.cursor() as cur:
        cur.execute(_CANDIDATES.format(scope=scope), {"t": tenant_id, "sids": security_ids})
        return cur.fetchall()


def _captured(parsed: dict[str, Any]) -> dict[str, Any]:
    return {k: parsed.get(k) for k in TEXT}


def _reasons(cand: dict[str, Any], parsed: dict[str, Any]) -> list[str]:
    """Why this row is versioned: a transfer candidate, and/or a code-P row whose footnotes locate a passage."""
    out = [TRANSFER] if cand["transfer"] else []
    probe = {"txn_code": cand["txn_code"], "txn_footnotes": parsed.get("txn_footnotes")}
    if cand["txn_code"] == "P" and private_purchase_note(probe) is not None:
        out.append(PRIVATE)
    return out


def _gains(row: dict[str, Any], parsed: dict[str, Any]) -> bool:
    """Does the version add anything? (Some text, or ownership evidence the row lacks.)"""
    text = (
        parsed.get("ownership_nature")
        or parsed.get("txn_footnotes")
        or parsed.get("filing_natures")
    )
    evidence = any(row.get(k) is None and parsed.get(k) is not None for k in EVIDENCE)
    return bool(text) or evidence


def run_repair(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    cache_dir: Path = _DEFAULT_CACHE,
    security_ids: list[UUID] | None = None,
    apply: bool,
    log=print,
) -> RepairResult:
    """Version the target rows with their filer text (``apply``), or report what WOULD change. The caller owns the
    transaction on a dry run (rolls back); ``apply`` commits per 200 filings and at the end."""
    if not (cache_dir / "forms").is_dir():
        raise SystemExit(f"no forms/ under cache dir {cache_dir} — wrong --cache-dir? refusing")
    if apply:
        _require_security_scoped_natural_key(conn)
    res = RepairResult(applied=apply)
    res.fact_rows_before = _count(conn, "fact_insider_txn", tenant_id)
    res.amendment_rows_before = _count(conn, "insider_amendment", tenant_id)
    every = candidates(conn, tenant_id, security_ids)
    res.transfer_candidates = sum(1 for r in every if r["transfer"])
    tickers = _tickers(conn, {r["security_id"] for r in every})

    by_acc: dict[str, list[dict[str, Any]]] = {}
    for r in every:
        if r["has_text"]:
            res.already_captured += 1
            continue
        if r["txn_code"] == "P":
            res.p_rows_examined += 1
        by_acc.setdefault(r["accession"], []).append(r)
    accessions = sorted(by_acc)
    res.accessions = len(accessions)

    for i, acc in enumerate(accessions):
        doc = cached_form_doc(cache_dir, acc)
        if doc is None:
            res.not_cached.append(acc)
            continue
        try:
            parsed = parse_form4(doc.read_text(encoding="utf-8"))
        except (ET.ParseError, ValueError, OSError, UnicodeDecodeError) as e:
            res.parse_errors.append(acc)
            log(f"  warn: {acc} unparseable ({e.__class__.__name__}: {e}) — left without the text")
            continue
        by_seq = dict(enumerate(parsed))  # txn_seq -> the ingest's own index
        fact = [c for c in by_acc[acc] if c["src"] == "fact"]
        if fact:
            _version_fact_rows(conn, res, acc, fact, by_seq, tickers, apply, log)
        for rec_id in sorted({c["id"] for c in by_acc[acc] if c["src"] == "amendment"}):
            cands = [c for c in by_acc[acc] if c["src"] == "amendment" and c["id"] == rec_id]
            _version_amendment(conn, res, acc, rec_id, cands, by_seq, tickers, apply, log)
        if apply and i % 200 == 199:
            conn.commit()  # per batch: a crash keeps progress; a re-run resumes at what is still bare
    if apply:
        conn.commit()
    res.fact_rows_after = _count(conn, "fact_insider_txn", tenant_id)
    res.amendment_rows_after = _count(conn, "insider_amendment", tenant_id)
    if apply:
        appended = (
            res.fact_rows_after - res.fact_rows_before,
            res.amendment_rows_after - res.amendment_rows_before,
        )
        if appended != (res.fact_rows_versioned, res.amendment_records_versioned):
            raise SystemExit(
                f"COUNT MISMATCH: appended {appended} rows, planned "
                f"{(res.fact_rows_versioned, res.amendment_records_versioned)} — investigate before re-running"
            )
    return res


def _version_fact_rows(
    conn: psycopg.Connection,
    res: RepairResult,
    acc: str,
    cands: list[dict[str, Any]],
    by_seq: dict[int, dict[str, Any]],
    tickers: dict[UUID, str],
    apply: bool,
    log,
) -> None:
    cand_by_id = {c["id"]: c for c in cands}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM fact_insider_txn WHERE id = ANY(%s) ORDER BY txn_seq, id",
            (list(cand_by_id),),
        )
        rows = cur.fetchall()
    for row in rows:
        p = by_seq.get(row["txn_seq"])
        if p is None or not _same_txn(p, row["valid_from"], row["txn_code"], row["shares"]):
            res.seq_mismatch.append(f"{acc}#{row['txn_seq']}")
            log(f"  warn: {acc}#{row['txn_seq']} does not match the cached filing — left as it is")
            continue
        reasons = _reasons(cand_by_id[row["id"]], p)
        if not reasons:
            continue  # a code-P row whose footnotes locate no passage and that is no transfer leg
        if not _gains(row, p):
            res.nothing_to_add += 1
            continue
        if apply:
            values = {
                k: v for k, v in row.items() if k not in {"id", "recorded_at", "supersedes", *TEXT}
            }
            values.update(_captured(p))
            for k in EVIDENCE:  # fill the 0054 evidence only where the stored row lacks it
                if values.get(k) is None:
                    values[k] = p.get(k)
            values["supersedes"] = row["id"]
            append_fact(conn, "fact_insider_txn", values)
        res.fact_rows_versioned += 1
        for reason in reasons:
            res.by_reason[reason] += 1
        res.by_security[tickers.get(row["security_id"], str(row["security_id"]))] += 1


def _version_amendment(
    conn: psycopg.Connection,
    res: RepairResult,
    acc: str,
    rec_id: UUID,
    cands: list[dict[str, Any]],
    by_seq: dict[int, dict[str, Any]],
    tickers: dict[UUID, str],
    apply: bool,
    log,
) -> None:
    """A 4/A record is versioned WHOLE (every Table I row gains its text) when one of its candidate rows is a
    target; one row that disagrees with the cached filing leaves the record as stored (never half-repaired).
    """
    want = False
    for c in cands:
        p = by_seq.get(c["txn_seq"])
        if p is not None and _reasons(c, p):
            want = True
    if not want:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment WHERE id = %s", (rec_id,))
        rec = cur.fetchone()
    new_rows: list[dict[str, Any]] = []
    for x in rec["rows"] or []:
        p = by_seq.get(x.get("txn_seq"))
        if p is None or not _same_txn(p, x.get("txn_date"), x.get("txn_code"), x.get("shares")):
            res.seq_mismatch.append(f"{acc}#{x.get('txn_seq')}")
            log(
                f"  warn: 4/A {acc}#{x.get('txn_seq')} does not match the cached filing — record left as it is"
            )
            return
        out = {**x, **_captured(p)}
        for k in EVIDENCE:
            if out.get(k) is None:
                out[k] = p.get(k)
        new_rows.append(out)
    if apply:
        cols = [c for c in rec if c not in ("id", "recorded_at")]
        values = [Json(new_rows) if c == "rows" else _jsonb(c, rec[c]) for c in cols]
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO insider_amendment ({', '.join(cols)}) "  # noqa: S608 — the table's own columns
                f"VALUES ({', '.join(['%s'] * len(cols))})",
                values,
            )
    res.amendment_records_versioned += 1
    res.by_security[tickers.get(rec["security_id"], str(rec["security_id"]))] += 1


def _jsonb(col: str, v: Any) -> Any:
    return Json(v) if col in _JSONB_COLS else v


def report(res: RepairResult, scope: str) -> None:
    mode = "APPLY (versions appended)" if res.applied else "DRY-RUN (read-only; nothing written)"
    verb = "versioned" if res.applied else "WOULD version"
    print(f"\n=== repair insider filer text — {mode} ===")
    print(f"  scope                          : {scope}")
    print(f"  transfer candidates (P/S rows) : {res.transfer_candidates} (both tables)")
    print(f"  code-P rows read for a note    : {res.p_rows_examined}")
    print(f"  already carrying the capture   : {res.already_captured}")
    print(f"  filings to re-parse            : {res.accessions}")
    print(
        f"  fact_insider_txn rows {verb:13}: {res.fact_rows_versioned}  by reason {dict(res.by_reason)}"
    )
    print(f"  insider_amendment records {verb:9}: {res.amendment_records_versioned}")
    print(
        f"  fact_insider_txn  rows before/after : {res.fact_rows_before} -> {res.fact_rows_after}"
    )
    print(
        f"  insider_amendment rows before/after : {res.amendment_rows_before} -> {res.amendment_rows_after}"
    )
    print(f"  not cached (left bare)         : {len(res.not_cached)} {res.not_cached[:10]}")
    print(f"  unparseable (left bare)        : {len(res.parse_errors)} {res.parse_errors[:10]}")
    print(f"  date/code/shares mismatch      : {len(res.seq_mismatch)} {res.seq_mismatch[:10]}")
    print(f"  filing adds nothing            : {res.nothing_to_add}")
    print("  NB a row left bare is never screened as a transfer — it can only stay counted")
    if res.by_security:
        top = ", ".join(f"{k} {v}" for k, v in res.by_security.most_common(25))
        print(f"  per security (top 25)          : {top}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill ownership_nature + txn_footnotes + filing_natures onto the stored insider rows an "
        "internal-transfer screen or a private-purchase note can read (append-only versions; NULL-only; dry-run "
        "by default)."
    )
    p.add_argument(
        "--apply", action="store_true", help="append the versions (default: dry run, read-only)"
    )
    p.add_argument("--ticker", action="append", default=None, help="only this member (repeatable)")
    p.add_argument(
        "--cache-dir", type=Path, default=_DEFAULT_CACHE, help="EDGAR cache root holding forms/"
    )
    p.add_argument(
        "--report-json", type=Path, default=None, help="also write the accounting to this file"
    )
    p.add_argument("--tenant", default=str(DEFAULT_TENANT_ID))
    args = p.parse_args(argv)
    tenant = UUID(args.tenant)
    print(f"repair_insider_filer_text · target {_redact(database_url())} · cache {args.cache_dir}")
    try:
        lock = acquire_pass_lock(holder="repair_insider_filer_text") if args.apply else None
    except PassAlreadyRunning as exc:
        print(f"ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})")
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            sids = None
            scope = "ALL securities"
            if args.ticker:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM security_master WHERE tenant_id = %s AND ticker = ANY(%s)",
                        (tenant, [t.upper() for t in args.ticker]),
                    )
                    sids = [r["id"] for r in cur.fetchall()]
                scope = (
                    f"tickers {', '.join(t.upper() for t in args.ticker)} ({len(sids)} master rows)"
                )
                if not sids:
                    print(f"scope {scope} matched NO master row — nothing to repair (loud no-op)")
                    return
            res = run_repair(
                conn,
                tenant_id=tenant,
                cache_dir=args.cache_dir,
                security_ids=sids,
                apply=args.apply,
            )
            if not args.apply:
                conn.rollback()
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()
    report(res, scope)
    if args.report_json is not None:
        args.report_json.write_text(
            json.dumps(res.as_json(), indent=2, default=str), encoding="utf-8"
        )
        print(f"  accounting written to {args.report_json}")


if __name__ == "__main__":
    main()
