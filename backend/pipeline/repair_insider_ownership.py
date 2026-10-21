"""Repair: backfill each stored insider transaction's OWNERSHIP evidence (``direct_indirect`` +
``shares_owned_after``, migration 0054) so a JOINT REPORT stored before the capture is counted once too.

THE DEFECT (``domain/insider_joint_reports.py``). One trade reported in SEPARATE Form 4s by several reporting
persons (a fund and its partner — Maverick Capital + David Singer on INFQ; a spouse "By Spouse" — SMCI, OKLO;
five Kim-family filers on one AMKR purchase) was summed once per filer. The read-time rule collapses such a
trade on the filings' own evidence — the direct/indirect flag and the holding the trade left — which the stored
rows never carried. The ingest captures both on new filings; this tool records them on the history.

WHICH ROWS — and why that set suffices. Two facts about the rule fix it:

1. It can merge or link ONLY rows that share (security, date, code, shares, price) with a row of a DIFFERENT
   reporting owner in a DIFFERENT filing (its groups are keyed on those fields plus the post-trade holding and
   need >= 2 owners in >= 2 filings). A row with no such partner — a CANDIDATE GROUP — is never grouped, so its
   evidence is never read. Over the stored Form 4 rows AND the stored 4/A rows (a 4/A pair can report one trade
   twice: INFQ -000507 / -000508).
2. Every reader of the collapsed tape reads codes ``P`` / ``S`` only (``READER_CODES``: the detectors' declared
   codes plus the Cockpit flow member's and the Scoreboard ledger's). A row of another code matters only as a
   LINK — a fingerprint proving two filings report one holding — and a link can matter only to a P/S group whose
   filings it connects, possibly through a chain of other filings.

So the tool versions every candidate row whose filing lies in a CONNECTED COMPONENT (filings joined by any
candidate group, per security) that holds at least one P/S candidate row — every row any P/S merge, or any link
chain behind one, can read — and nothing else. MEASURED on prod 2026-10-21 (read-only, this code, the two
columns read as NULL): 50,635 candidate rows of every code (25,390 of the Form 4 rows grants, code ``A``), of
which 8,723 lie in reader-reachable components — the plan: 8,437 Form 4 rows (S 5,747 · P 814 · M 802 · C 324 ·
J 280 · D 191 · A 178 · G 68 · F 20 · X 13) and 128 4/A records, from 2,650 cached filings; none uncached,
unparseable, mismatched or evidence-less (+1.4% on the 599,068-row table). The exact plan prints on every run.
Rows whose latest version already carries evidence are skipped.

MECHANISM — re-parse the CACHED filing (``forms/<accession>/<doc>``, the immutable cache class; no SEC
request) with the SAME ``parse_form4`` the ingest uses, matched to each stored row by ``txn_seq`` and VERIFIED
on date + code + shares (a mismatch is printed and the row left as it is).

- ``fact_insider_txn`` — an append-only NEW VERSION of each target row: every column copied verbatim, only the
  two evidence columns set, ``supersedes`` = the old id, ``recorded_at`` = the database ``now()`` (never
  backdated). The as-of read serves it for any ``known_at`` at/after the run; a replay pinned earlier still
  reads the double count the system held then (#1, operator decision 4).
- ``insider_amendment`` — a NEW VERSION of the 4/A record (every column verbatim) whose Table I rows carry the
  two keys; the resolver reads the newest version.

RULES:
- **NULL-only + idempotent.** Targets latest versions without evidence; a row whose filing states none (both
  fields absent) is NOT re-versioned — so a re-run appends nothing. Count the TABLE, not the read.
- **Safe on what it cannot do.** An uncached / unparseable filing, or a row that fails the date/code/shares
  check, is left without evidence — and a row without evidence never merges, so it can only stay
  double-counted, never collapse wrongly. Every such case is counted and printed.
- **Keep-not-delete.** It never deletes or updates; it only appends versions.
- **Dry-run by default** on a READ-ONLY connection. ``--apply`` writes under the call-of-record lock
  (``pipeline/run_lock.py``; exit 75 if a daily pass or backfill holds it), then verifies the appended counts.

    python -m pipeline.repair_insider_ownership                    # DRY-RUN, the whole tape
    python -m pipeline.repair_insider_ownership --ticker INFQ      # DRY-RUN, one member
    python -m pipeline.repair_insider_ownership --apply            # write (the operator, after a backup)
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID, connect, database_url
from ingest.edgar.form4 import parse_form4
from pipeline.backfill_aff10b5_1 import cached_form_doc
from pipeline.repair_adr_insider_misattribution import _require_security_scoped_natural_key
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "edgar_cache"
EVIDENCE = ("direct_indirect", "shares_owned_after")


def reader_codes() -> frozenset[str]:
    """The Form 4 codes any reader of the collapsed tape reads: every call detector's declared ``txn_codes``
    (``signals.horizons.insider_signal_codes`` — today P and S) plus the Cockpit flow member's and the
    Scoreboard ledger's buys / sells (``signals.display.insider_flow.BUY`` / ``SELL``)."""
    from domain.config import DEFAULT_CONFIG
    from signals.display.insider_flow import BUY, SELL
    from signals.horizons import insider_signal_codes

    return frozenset(insider_signal_codes(DEFAULT_CONFIG)) | {BUY, SELL}


def relevant(cands: list[dict[str, Any]], codes: frozenset[str]) -> list[dict[str, Any]]:
    """The candidate rows whose filing lies in a connected component (filings joined by a candidate group, per
    security) holding a candidate row of a reader code — see the module docstring for why that suffices.
    """
    parent: dict[tuple[Any, str], tuple[Any, str]] = {}

    def find(x: tuple[Any, str]) -> tuple[Any, str]:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    groups: dict[tuple[Any, ...], list[tuple[Any, str]]] = {}
    for r in cands:
        node = (r["security_id"], str(r["accession"]))
        find(node)
        key = (r["security_id"], r["valid_from"], r["txn_code"], r["shares"], r["price"])
        groups.setdefault(key, []).append(node)
    for nodes in groups.values():
        for n in nodes[1:]:
            a, b = find(nodes[0]), find(n)
            if a != b:
                parent[a] = b
    hot = {find((r["security_id"], str(r["accession"]))) for r in cands if r["txn_code"] in codes}
    return [r for r in cands if find((r["security_id"], str(r["accession"]))) in hot]


# The candidate rows (see the module docstring): every latest-version Form 4 row and every row of the newest
# version of each 4/A record, grouped on (security, date, code, shares, price) — kept when the group spans >= 2
# reporting owners in >= 2 filings. The owner is the CIK (leading zeros dropped), else the normalized name.
_CANDIDATES = """
WITH latest AS (
    SELECT DISTINCT ON (tenant_id, security_id, accession, insider_name, valid_from, txn_seq)
           id, security_id, accession, insider_name, valid_from, txn_seq, txn_code, shares, price,
           rpt_owner_cik, direct_indirect, shares_owned_after
    FROM fact_insider_txn
    WHERE tenant_id = %(t)s {scope}
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
           x->>'rpt_owner_cik' AS rpt_owner_cik, (x ? 'direct_indirect') AS has_evidence
    FROM am CROSS JOIN LATERAL jsonb_array_elements(am.rows) AS x
    WHERE x->>'txn_date' IS NOT NULL
), allrows AS (
    SELECT 'fact' AS src, id, security_id, accession, valid_from, txn_seq, txn_code, shares, price,
           COALESCE(NULLIF(ltrim(rpt_owner_cik, '0'), ''),
                    lower(regexp_replace(btrim(coalesce(insider_name, '')), '\\s+', ' ', 'g'))) AS owner,
           (direct_indirect IS NOT NULL OR shares_owned_after IS NOT NULL) AS has_evidence
    FROM latest
    UNION ALL
    SELECT 'amendment', id, security_id, accession, valid_from, txn_seq, txn_code, shares, price,
           COALESCE(NULLIF(ltrim(rpt_owner_cik, '0'), ''),
                    lower(regexp_replace(btrim(coalesce(insider_name, '')), '\\s+', ' ', 'g'))),
           has_evidence
    FROM am_rows
), grp AS (
    SELECT security_id, valid_from, txn_code, shares, price
    FROM allrows
    WHERE shares > 0
    GROUP BY security_id, valid_from, txn_code, shares, price
    HAVING count(DISTINCT owner) > 1 AND count(DISTINCT accession) > 1
)
SELECT DISTINCT a.src, a.id, a.security_id, a.accession, a.txn_seq, a.txn_code, a.has_evidence,
       a.valid_from, a.shares, a.price
FROM allrows a
JOIN grp g
  ON g.security_id = a.security_id AND g.valid_from = a.valid_from AND g.txn_code = a.txn_code
 AND g.shares = a.shares AND g.price IS NOT DISTINCT FROM a.price
"""


@dataclass
class RepairResult:
    """One run's full accounting — printed so the operator counts the TABLES, not just the read."""

    applied: bool
    candidate_rows: int = 0  # rows in a cross-filing group (both tables, every code)
    reader_relevant_rows: int = (
        0  # of those, in a component a P/S reader can reach — the set versioned
    )
    candidate_rows_with_evidence: int = 0  # already carrying it — skipped
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
    no_evidence: int = 0  # the filing states neither field for the row — left as it is
    by_code: Counter = field(default_factory=Counter)  # fact rows versioned per txn code
    by_security: Counter = field(
        default_factory=Counter
    )  # (ticker or id) -> rows + records versioned

    def as_json(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "candidate_rows": self.candidate_rows,
            "reader_relevant_rows": self.reader_relevant_rows,
            "candidate_rows_with_evidence": self.candidate_rows_with_evidence,
            "fact_rows_before": self.fact_rows_before,
            "fact_rows_after": self.fact_rows_after,
            "amendment_rows_before": self.amendment_rows_before,
            "amendment_rows_after": self.amendment_rows_after,
            "fact_rows_versioned": self.fact_rows_versioned,
            "amendment_records_versioned": self.amendment_records_versioned,
            "accessions": self.accessions,
            "not_cached": self.not_cached,
            "parse_errors": self.parse_errors,
            "seq_mismatch": self.seq_mismatch,
            "no_evidence": self.no_evidence,
            "by_code": dict(self.by_code),
            "by_security": dict(self.by_security),
        }


def _redact(url: str) -> str:
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url)


def _count(conn: psycopg.Connection, table: str, tenant_id: UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table} WHERE tenant_id = %s", (tenant_id,)
        )  # noqa: S608
        return cur.fetchone()["n"]


def candidates(
    conn: psycopg.Connection, tenant_id: UUID, security_ids: list[UUID] | None = None
) -> list[dict[str, Any]]:
    """The candidate rows (module docstring) — ``{src, id, security_id, accession, txn_seq, txn_code,
    has_evidence}``; ``src`` = ``'fact'`` (a ``fact_insider_txn`` latest version) or ``'amendment'`` (a row of
    the newest version of a 4/A record, ``id`` = that record's id)."""
    scope = "" if security_ids is None else "AND security_id = ANY(%(sids)s)"
    with conn.cursor() as cur:
        cur.execute(_CANDIDATES.format(scope=scope), {"t": tenant_id, "sids": security_ids})
        return cur.fetchall()


def _same_txn(parsed: dict[str, Any], valid_from: Any, code: Any, shares: Any) -> bool:
    """The re-parsed transaction at this ``txn_seq`` is the stored one: same date, code and share count."""
    if parsed.get("txn_code") != code or parsed.get("txn_date") is None:
        return False
    d = valid_from if isinstance(valid_from, date) else date.fromisoformat(str(valid_from)[:10])
    if parsed["txn_date"] != d:
        return False
    a, b = parsed.get("shares"), shares
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-6


def _evidence(parsed: dict[str, Any]) -> dict[str, Any]:
    return {k: parsed.get(k) for k in EVIDENCE}


def run_repair(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    cache_dir: Path = _DEFAULT_CACHE,
    security_ids: list[UUID] | None = None,
    apply: bool,
    log=print,
) -> RepairResult:
    """Version the candidate rows with their ownership evidence (``apply``), or report what WOULD change.
    The caller owns the transaction on a dry run (rolls back); ``apply`` commits per 200 filings and at the end.
    """
    if not (cache_dir / "forms").is_dir():
        raise SystemExit(f"no forms/ under cache dir {cache_dir} — wrong --cache-dir? refusing")
    if apply:
        _require_security_scoped_natural_key(conn)
    res = RepairResult(applied=apply)
    res.fact_rows_before = _count(conn, "fact_insider_txn", tenant_id)
    res.amendment_rows_before = _count(conn, "insider_amendment", tenant_id)
    every = candidates(conn, tenant_id, security_ids)
    res.candidate_rows = len(every)
    cand = relevant(every, reader_codes())
    res.reader_relevant_rows = len(cand)
    tickers = _tickers(conn, {r["security_id"] for r in cand})

    fact_ids: dict[str, set[UUID]] = {}  # accession -> fact row ids to version
    amendment_ids: dict[str, set[UUID]] = {}  # accession -> 4/A record ids to version
    for r in cand:
        if r["has_evidence"]:
            res.candidate_rows_with_evidence += 1
            continue
        bucket = fact_ids if r["src"] == "fact" else amendment_ids
        bucket.setdefault(r["accession"], set()).add(r["id"])
    accessions = sorted(set(fact_ids) | set(amendment_ids))
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
            log(f"  warn: {acc} unparseable ({e.__class__.__name__}: {e}) — left without evidence")
            continue
        by_seq = dict(enumerate(parsed))  # txn_seq -> the ingest's own index
        if fact_ids.get(acc):
            _version_fact_rows(conn, res, acc, sorted(fact_ids[acc]), by_seq, tickers, apply, log)
        for rec_id in sorted(amendment_ids.get(acc, ())):
            _version_amendment(conn, res, acc, rec_id, by_seq, tickers, apply, log)
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
    ids: list[UUID],
    by_seq: dict[int, dict[str, Any]],
    tickers: dict[UUID, str],
    apply: bool,
    log,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM fact_insider_txn WHERE id = ANY(%s) ORDER BY txn_seq, id", (ids,)
        )
        rows = cur.fetchall()
    for row in rows:
        p = by_seq.get(row["txn_seq"])
        if p is None or not _same_txn(p, row["valid_from"], row["txn_code"], row["shares"]):
            res.seq_mismatch.append(f"{acc}#{row['txn_seq']}")
            log(f"  warn: {acc}#{row['txn_seq']} does not match the cached filing — left as it is")
            continue
        ev = _evidence(p)
        if all(v is None for v in ev.values()):
            res.no_evidence += 1
            continue
        if apply:
            values = {
                k: v
                for k, v in row.items()
                if k not in {"id", "recorded_at", "supersedes", *EVIDENCE}
            }
            values.update(ev)
            values["supersedes"] = row["id"]
            append_fact(conn, "fact_insider_txn", values)
        res.fact_rows_versioned += 1
        res.by_code[row["txn_code"] or "?"] += 1
        res.by_security[tickers.get(row["security_id"], str(row["security_id"]))] += 1


def _version_amendment(
    conn: psycopg.Connection,
    res: RepairResult,
    acc: str,
    rec_id: UUID,
    by_seq: dict[int, dict[str, Any]],
    tickers: dict[UUID, str],
    apply: bool,
    log,
) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment WHERE id = %s", (rec_id,))
        rec = cur.fetchone()
    new_rows: list[dict[str, Any]] = []
    gained = False
    for x in rec["rows"] or []:
        p = by_seq.get(x.get("txn_seq"))
        if p is None or not _same_txn(p, x.get("txn_date"), x.get("txn_code"), x.get("shares")):
            res.seq_mismatch.append(f"{acc}#{x.get('txn_seq')}")
            log(
                f"  warn: 4/A {acc}#{x.get('txn_seq')} does not match the cached filing — record left as it is"
            )
            return  # one disagreeing row: the whole record stays as stored (never a half-repaired record)
        ev = _evidence(p)
        gained = gained or any(v is not None for v in ev.values())
        new_rows.append({**x, **ev})
    if not gained:
        res.no_evidence += 1
        return
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


_JSONB_COLS = frozenset({"owners", "footnotes", "rows"})


def _jsonb(col: str, v: Any) -> Any:
    return Json(v) if col in _JSONB_COLS else v


def _tickers(conn: psycopg.Connection, sids: set[UUID]) -> dict[UUID, str]:
    if not sids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, ticker FROM security_master WHERE id = ANY(%s)", (list(sids),))
        return {r["id"]: r["ticker"] for r in cur.fetchall() if r["ticker"]}


def report(res: RepairResult, scope: str) -> None:
    mode = "APPLY (versions appended)" if res.applied else "DRY-RUN (read-only; nothing written)"
    verb = "versioned" if res.applied else "WOULD version"
    print(f"\n=== repair insider ownership evidence — {mode} ===")
    print(f"  scope                          : {scope}")
    print(f"  candidate rows (cross-filing)  : {res.candidate_rows} (every code)")
    print(f"    in a P/S-reachable component : {res.reader_relevant_rows} (the set versioned)")
    print(f"    already carrying evidence    : {res.candidate_rows_with_evidence}")
    print(f"  filings to re-parse            : {res.accessions}")
    print(
        f"  fact_insider_txn rows {verb:13}: {res.fact_rows_versioned}  by code {dict(res.by_code)}"
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
    print(f"  the filing states no evidence  : {res.no_evidence}")
    print(
        "  NB a row left bare never merges — it can only stay double-counted, never collapse wrongly"
    )
    if res.by_security:
        top = ", ".join(f"{k} {v}" for k, v in res.by_security.most_common(25))
        print(f"  per security (top 25)          : {top}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill direct_indirect + shares_owned_after onto the stored insider rows that a joint "
        "report can touch (append-only versions; NULL-only; dry-run by default)."
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
    print(f"repair_insider_ownership · target {_redact(database_url())} · cache {args.cache_dir}")
    try:
        lock = acquire_pass_lock(holder="repair_insider_ownership") if args.apply else None
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
        args.report_json.write_text(json.dumps(res.as_json(), indent=2), encoding="utf-8")
        print(f"  accounting written to {args.report_json}")


if __name__ == "__main__":
    main()
