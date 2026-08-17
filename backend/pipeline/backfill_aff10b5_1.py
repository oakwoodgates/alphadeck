"""Backfill ``fact_insider_txn.aff_10b5_1`` (the Rule 10b5-1 checkbox) from the EDGAR cache.

THE GAP: the checkbox capture landed with #195 (~2026-07-17), and the incremental Form 4 leg skips
already-stored accessions (``existing_accessions``), so every row ingested BEFORE the capture is frozen
at NULL — the value was always in the filing; we just failed to read it at ingest. This CLI repairs that
one column by re-parsing the CACHED filings (``forms/<accession>/<doc>`` — the immutable cache class)
with the SAME ``parse_form4`` the ingest uses.

MECHANISM — append-only re-version, the table's own correction discipline. ``fact_insider_txn`` carries
a live ``no_update`` trigger (0001: "a correction inserts a NEW row, never an UPDATE-in-place"), so the
repair appends a NEW VERSION of each corrected row: every column copied VERBATIM from the row it
supersedes, ONLY ``aff_10b5_1`` set from the re-parse, ``supersedes`` linking the old row's id, and
``recorded_at`` left to the DB's ``now()`` — NEVER backdated (transaction-time honesty; the
demo-rebuild lesson). The shared as-of read (``db.bitemporal._as_of``: latest version per natural key
by ``recorded_at DESC, id DESC``) therefore returns the corrected flag for any ``known_at`` >= the
backfill, while a replay pinned EARLIER still honestly sees NULL — exactly what the system knew then.

One filing's corrections can SPAN SECURITIES: the ingest skip (``existing_accessions``) is scoped per
(tenant, security), so an issuer held as two master rows stores the same Form 4 once per scope — two
LOGICAL facts, one per security, exactly as the as-of read keys them. A batch's appends share one
transaction-time ``now()``, so the natural-key constraint must carry ``security_id`` (migration 0037);
the pre-0037 key omitted it and a two-security filing aborted the prod run mid-batch on 2026-08-17
(UniqueViolation — two same-instant corrections, one constraint tuple). ``--execute`` therefore
FAILS LOUD up front on an unmigrated schema instead of collapsing mid-run.

RULES:
- **NULL-only.** Targets natural keys whose LATEST version has ``aff_10b5_1 IS NULL``. A captured
  (non-NULL) value is NEVER rewritten — a re-parse disagreement with a stored value is ``--verify``'s
  job to REPORT, never this tool's to fix.
- **Unknown stays unknown (#3).** A checkbox-absent filing (the pre-Dec-2022 norm) parses to ``None``
  and the row STAYS NULL — counted, never coerced to False. An uncached / unparseable filing likewise
  stays NULL and is counted (the residual the run reports).
- **Cache-only, structurally.** Filings are read straight off disk under ``<cache-dir>/forms/`` — no
  ``EdgarClient``, no network capability at all. ``forms/*`` is the immutable cache class (an
  accession's document never changes), so a disk read IS the filing.
- **Idempotent (count-the-table).** A corrected key's latest version is non-NULL after the run, so a
  re-run targets nothing and appends ZERO rows — assert ``count(*)`` before/after, not just the read.
- **Fail-visible per filing.** One bad cached doc is skipped-and-counted, never aborts the run; batch
  commits keep partial progress on a crash (a re-run resumes at the remaining NULLs).
- **Dry-run by default.** ``--execute`` writes; without it the full read+parse runs and reports what
  WOULD change, touching nothing.

    python -m pipeline.backfill_aff10b5_1                       # dry-run against $DATABASE_URL
    python -m pipeline.backfill_aff10b5_1 --execute             # write the corrections
    python -m pipeline.backfill_aff10b5_1 --verify              # independent re-parse cross-check
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from db.bitemporal import append_fact
from db.session import database_url
from ingest.edgar.form4 import parse_form4

# The runtime cache home (mirrors ingest.edgar.client._DEFAULT_CACHE — repo-root/data/edgar_cache
# locally, /data/edgar_cache in the containers via the appdata volume).
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "edgar_cache"

# The latest-version view of the table — the SAME grain the shared as-of read dedups on
# (db.bitemporal._FACT_IDENTITY['fact_insider_txn'] within its (tenant, security) scope), so "latest
# is NULL" here == "the as-of read would return NULL" there. Ties on recorded_at break by id DESC,
# mirroring _as_of exactly. Since 0037 the fact_insider_txn_natural_key constraint carries this same
# per-security grain, so a batch's same-instant corrections under two securities cannot collide.
_LATEST = (
    "SELECT DISTINCT ON (tenant_id, security_id, accession, insider_name, valid_from, txn_seq) * "
    "FROM fact_insider_txn {where} "
    "ORDER BY tenant_id, security_id, accession, insider_name, valid_from, txn_seq, "
    "recorded_at DESC, id DESC"
)

# Columns NOT copied onto a correction row: id (a new version gets a new id), recorded_at (DB now() —
# never backdated), supersedes (set to the corrected row's id), aff_10b5_1 (the correction itself).
# Everything else — including any column added after this tool was written — copies verbatim, so the
# ONLY delta between a row and its correction is the flag.
_NOT_COPIED = frozenset({"id", "recorded_at", "supersedes", "aff_10b5_1"})


@dataclass
class BackfillResult:
    """One run's full accounting — printed verbatim so the operator (and the verification gates) can
    count the table, not just trust the read."""

    executed: bool
    table_rows_before: int = 0
    table_rows_after: int = 0
    accessions_targeted: int = 0  # distinct accessions with >=1 latest-version-NULL row
    accessions_corrected: int = 0  # cached + parsed + checkbox present -> rows appended
    accessions_checkbox_absent: int = (
        0  # parsed clean, no <aff10b5One> -> stays NULL (pre-2023 norm)
    )
    accessions_not_cached: int = 0  # no forms/<accession>/ doc on disk -> stays NULL
    accessions_parse_error: int = (
        0  # unreadable/unparseable cached doc -> stays NULL, run continues
    )
    rows_to_true: int = 0
    rows_to_false: int = 0
    rows_residual_null: int = 0  # rows left NULL (absent / uncached / unparseable accessions)


@dataclass
class VerifyResult:
    """The independent cross-check: re-parse every cached filing behind the LATEST versions and compare
    against what is stored. ``mismatches`` non-empty is the STOP signal (exit 1)."""

    keys_total: int = 0  # latest-version natural keys seen
    keys_stored_nonnull: int = 0  # of those, carrying a stored True/False
    keys_compared: int = 0  # stored non-NULL AND cached+parseable -> actually compared
    keys_not_cached: int = 0
    keys_parse_error: int = 0
    keys_null_consistent: int = 0  # stored NULL, filing has no checkbox (consistent unknown)
    keys_null_but_correctable: int = 0  # stored NULL, filing HAS the checkbox (0 after a clean run)
    mismatches: list[tuple[str, str, bool | None, bool | None]] = field(default_factory=list)
    # (accession, insider_name, stored, parsed) — capped at print time, full count always reported


def _redact(url: str) -> str:
    """The DSN with its password masked — printed on every run so the target is explicit."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url)


def cached_form_doc(cache_dir: Path, accession: str) -> Path | None:
    """The cached primary doc for ``accession`` (``forms/<accession>/<doc>`` — one file per filing;
    prefer ``.xml`` if several ever appear). ``None`` when the filing simply isn't cached."""
    d = cache_dir / "forms" / accession
    if not d.is_dir():
        return None
    files = sorted(p for p in d.iterdir() if p.is_file())
    if not files:
        return None
    xmls = [p for p in files if p.suffix.lower() == ".xml"]
    return (xmls or files)[0]


def filing_flag(path: Path) -> bool | None:
    """The filing's document-level 10b5-1 flag via the SAME ``parse_form4`` the ingest uses (tri-state;
    ``None`` = no checkbox). The flag is stamped identically on every parsed row; a filing that parses
    to zero rows (or, unreachably, mixed flags) is an anomaly — raise so the caller skips-and-counts.
    """
    txns = parse_form4(path.read_text(encoding="utf-8"))
    if not txns:
        raise ValueError("filing parsed to zero transactions (stored rows expected some)")
    flags = {t["aff_10b5_1"] for t in txns}
    if len(flags) != 1:  # pragma: no cover — parse_form4 stamps one document-level value
        raise ValueError(f"inconsistent per-row flags {flags!r}")
    return flags.pop()


def _count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        return cur.fetchone()["n"]


def _target_accessions(conn: psycopg.Connection) -> list[str]:
    """Distinct accessions with at least one latest-version-NULL row — the repair worklist."""
    q = (
        "SELECT DISTINCT accession FROM (" + _LATEST.format(where="") + ") latest "
        "WHERE aff_10b5_1 IS NULL ORDER BY accession"
    )
    with conn.cursor() as cur:
        cur.execute(q)
        return [r["accession"] for r in cur.fetchall()]


def _latest_rows_for(conn: psycopg.Connection, accessions: list[str]) -> list[dict]:
    """The latest-version rows under ``accessions`` (ALL of them, NULL or not — the caller filters), so
    correction targeting and verification share one read."""
    q = "SELECT * FROM (" + _LATEST.format(where="WHERE accession = ANY(%s)") + ") latest"
    with conn.cursor() as cur:
        cur.execute(q, (accessions,))
        return cur.fetchall()


_TOLERATED = (ET.ParseError, ValueError, OSError, UnicodeDecodeError)


def _require_security_scoped_natural_key(conn: psycopg.Connection) -> None:
    """FAIL LOUD, before the first write, unless ``fact_insider_txn_natural_key`` carries
    ``security_id`` (migration 0037).

    One logical insider fact is (tenant, SECURITY, accession, insider, valid_from, txn_seq) — the grain
    the as-of read keys on and ``_LATEST`` targets. The pre-0037 constraint omitted ``security_id``, so
    two same-instant corrections of one filing key held under two securities (one issuer, two master
    rows) collided on ``recorded_at`` — ``now()`` is constant within a batch transaction — and aborted
    the prod run mid-batch (2026-08-17). Refusing up front turns that crash into an instruction."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_constraint c "
            "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
            "WHERE c.conname = 'fact_insider_txn_natural_key' "
            "AND c.conrelid = 'fact_insider_txn'::regclass"
        )
        cols = {r["attname"] for r in cur.fetchall()}
    if "security_id" not in cols:
        raise SystemExit(
            "fact_insider_txn_natural_key lacks security_id (migration 0037 not applied) — two "
            "same-instant corrections of one filing under two securities would collide mid-batch "
            "(the 2026-08-17 prod abort). Run `python -m db.migrate` first; refusing."
        )


def run_backfill(
    conn: psycopg.Connection,
    *,
    cache_dir: Path,
    execute: bool,
    limit: int | None = None,
    batch_size: int = 200,
    log=print,
) -> BackfillResult:
    """The repair pass (see the module docstring for the rules). ``execute=False`` performs the full
    read + parse and reports what WOULD change, appending nothing."""
    forms = cache_dir / "forms"
    if not forms.is_dir():
        # FAIL LOUD: without this, a mis-mounted cache would "complete" with everything counted
        # not-cached — a run that looks done and repaired nothing.
        raise SystemExit(f"no forms/ under cache dir {cache_dir} — wrong --cache-dir? refusing")
    if execute:  # write-path precondition (0037); a dry-run reads fine on any schema
        _require_security_scoped_natural_key(conn)
    res = BackfillResult(executed=execute)
    res.table_rows_before = _count(conn)
    accessions = _target_accessions(conn)
    if limit is not None:
        accessions = accessions[:limit]
    res.accessions_targeted = len(accessions)
    log(f"targets: {len(accessions)} accessions with a latest-version NULL flag")

    done = 0
    for i in range(0, len(accessions), batch_size):
        chunk = accessions[i : i + batch_size]
        rows = [r for r in _latest_rows_for(conn, chunk) if r["aff_10b5_1"] is None]
        by_acc: dict[str, list[dict]] = {}
        for r in rows:
            by_acc.setdefault(r["accession"], []).append(r)
        for acc in chunk:
            targets = by_acc.get(acc, [])
            if not targets:  # corrected by an earlier batch/run in the meantime — nothing to do
                continue
            doc = cached_form_doc(cache_dir, acc)
            if doc is None:
                res.accessions_not_cached += 1
                res.rows_residual_null += len(targets)
                continue
            try:
                flag = filing_flag(doc)
            except _TOLERATED as e:  # one bad filing: skipped-and-counted, never an abort
                res.accessions_parse_error += 1
                res.rows_residual_null += len(targets)
                log(f"  warn: {acc} unparseable ({e.__class__.__name__}: {e}) — left NULL")
                continue
            if flag is None:  # no checkbox in the filing — unknown stays unknown (#3)
                res.accessions_checkbox_absent += 1
                res.rows_residual_null += len(targets)
                continue
            res.accessions_corrected += 1
            for row in targets:
                if execute:
                    values = {k: v for k, v in row.items() if k not in _NOT_COPIED}
                    values["aff_10b5_1"] = flag
                    values["supersedes"] = row["id"]  # provenance: which version this corrects
                    append_fact(conn, "fact_insider_txn", values)
                if flag:
                    res.rows_to_true += 1
                else:
                    res.rows_to_false += 1
        if execute:
            conn.commit()  # per-batch: a crash keeps progress; the re-run resumes at remaining NULLs
        done += len(chunk)
        if done % 5000 < batch_size or done == len(accessions):
            log(f"  ... {done}/{len(accessions)} accessions checked")
    res.table_rows_after = _count(conn)
    return res


def run_verify(
    conn: psycopg.Connection,
    *,
    cache_dir: Path,
    batch_size: int = 500,
    log=print,
) -> VerifyResult:
    """The independent cross-check: for EVERY latest-version key, re-parse the cached filing and compare
    the stored flag against the parser's. Reads only — writes nothing. A stored non-NULL value that
    disagrees with the re-parse (including a value where the filing has NO checkbox) is a MISMATCH.
    """
    forms = cache_dir / "forms"
    if not forms.is_dir():
        raise SystemExit(f"no forms/ under cache dir {cache_dir} — wrong --cache-dir? refusing")
    res = VerifyResult()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT accession FROM fact_insider_txn ORDER BY accession")
        accessions = [r["accession"] for r in cur.fetchall()]
    log(f"verify: {len(accessions)} distinct accessions")
    done = 0
    for i in range(0, len(accessions), batch_size):
        chunk = accessions[i : i + batch_size]
        by_acc: dict[str, list[dict]] = {}
        for r in _latest_rows_for(conn, chunk):
            by_acc.setdefault(r["accession"], []).append(r)
        for acc in chunk:
            rows = by_acc.get(acc, [])
            res.keys_total += len(rows)
            stored_nonnull = [r for r in rows if r["aff_10b5_1"] is not None]
            res.keys_stored_nonnull += len(stored_nonnull)
            doc = cached_form_doc(cache_dir, acc)
            if doc is None:
                res.keys_not_cached += len(rows)
                continue
            try:
                parsed = filing_flag(doc)
            except _TOLERATED:
                res.keys_parse_error += len(rows)
                continue
            for r in rows:
                stored = r["aff_10b5_1"]
                if stored is None:
                    if parsed is None:
                        res.keys_null_consistent += 1
                    else:
                        res.keys_null_but_correctable += 1
                    continue
                res.keys_compared += 1
                if stored is not parsed:
                    res.mismatches.append((acc, r["insider_name"] or "?", stored, parsed))
        done += len(chunk)
        if done % 10000 < batch_size or done == len(accessions):
            log(f"  ... {done}/{len(accessions)} accessions verified")
    return res


def _print_backfill(res: BackfillResult) -> None:
    mode = "EXECUTE" if res.executed else "DRY-RUN (nothing written)"
    print(f"\n=== backfill aff_10b5_1 — {mode} ===")
    print(f"  table rows before : {res.table_rows_before}")
    print(f"  table rows after  : {res.table_rows_after}")
    print(f"  rows appended     : {res.table_rows_after - res.table_rows_before}")
    print(f"  accessions targeted        : {res.accessions_targeted}")
    print(f"  accessions corrected       : {res.accessions_corrected}")
    print(
        f"  accessions checkbox-absent : {res.accessions_checkbox_absent} (stay NULL — pre-2023 norm)"
    )
    print(f"  accessions not cached      : {res.accessions_not_cached} (stay NULL)")
    print(f"  accessions parse-error     : {res.accessions_parse_error} (stay NULL)")
    print(f"  rows NULL -> True  : {res.rows_to_true}")
    print(f"  rows NULL -> False : {res.rows_to_false}")
    print(f"  rows residual NULL : {res.rows_residual_null}")


def _print_verify(res: VerifyResult) -> None:
    print("\n=== verify aff_10b5_1 — independent re-parse cross-check (read-only) ===")
    print(f"  latest-version keys        : {res.keys_total}")
    print(f"  stored non-NULL            : {res.keys_stored_nonnull}")
    print(f"  compared (cached+parsed)   : {res.keys_compared}")
    print(f"  not cached                 : {res.keys_not_cached}")
    print(f"  parse errors               : {res.keys_parse_error}")
    print(f"  NULL, filing has no box    : {res.keys_null_consistent} (consistent unknown)")
    print(
        f"  NULL but correctable       : {res.keys_null_but_correctable} (0 expected after a run)"
    )
    print(f"  MISMATCHES                 : {len(res.mismatches)}")
    for acc, who, stored, parsed in res.mismatches[:20]:
        print(f"    MISMATCH {acc} ({who}): stored={stored} parsed={parsed}")
    if len(res.mismatches) > 20:
        print(f"    ... and {len(res.mismatches) - 20} more")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill fact_insider_txn.aff_10b5_1 from the EDGAR cache (append-only "
        "re-version; NULL-only; dry-run by default)."
    )
    p.add_argument("--execute", action="store_true", help="write corrections (default: dry-run)")
    p.add_argument(
        "--verify",
        action="store_true",
        help="independent re-parse cross-check instead of the backfill (read-only; exit 1 on any "
        "mismatch)",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=_DEFAULT_CACHE,
        help=f"EDGAR cache root containing forms/ (default {_DEFAULT_CACHE})",
    )
    p.add_argument(
        "--database-url",
        default=None,
        help="explicit target DSN (default: $DATABASE_URL, else the dev default)",
    )
    p.add_argument(
        "--limit", type=int, default=None, help="cap the accessions processed (spot runs)"
    )
    args = p.parse_args(argv)

    url = args.database_url or database_url()
    print(f"target DB : {_redact(url)}")
    print(f"cache dir : {args.cache_dir}")
    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            print(f"connected : current_database() = {cur.fetchone()['db']}")
        if args.verify:
            vres = run_verify(conn, cache_dir=args.cache_dir)
            _print_verify(vres)
            if vres.mismatches:
                raise SystemExit(1)  # the STOP signal — a stored value the parser disagrees with
        else:
            bres = run_backfill(
                conn, cache_dir=args.cache_dir, execute=args.execute, limit=args.limit
            )
            _print_backfill(bres)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
