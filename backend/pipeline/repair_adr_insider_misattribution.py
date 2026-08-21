"""One-time repair: backfill ``security_title`` + ``issuer_foreign_symbol`` onto the stored insider tape so
the ADR/dual-listed screen works on HISTORY too — KEEP-NOT-DELETE (internal ref S2c).

THE DEFECT (root-caused + fixed at ingest in ``ingest/edgar/form4.py``): a foreign/dual-listed issuer files
ONE Form 4 stream under ONE CIK covering TWO instruments — its US ADR (TSM) and its home-market ordinary
shares (2330.TW, ESPP/LTI buys). ``parse_form4`` never read the per-transaction ``<securityTitle>``, so every
row was stamped with the single (ADR) ``security_id`` and the home-market ordinary buys landed on the ADR's
tape as if they were personal conviction in the ADR we hold (#3). Migration 0041 adds ``security_title`` +
``issuer_foreign_symbol``; the incremental ingest (``existing_accessions`` skips stored filings) captures both
on NEWLY-ingested filings only, so every pre-fix row is frozen NULL and the screen cannot see them.

WHY RE-VERSION, NOT DELETE. Unlike the activist self-filed repair (a mis-ROUTED write — the row never
belonged on that subject, and the true subject's own feed carries it correctly), an ADR-tape ordinary row is
a real Form 4 fact that IS about this issuer's CIK; the ORDINARY shares have no other feed in the system, so
a deleted row is the only copy lost. The honest repair is to RECORD what the filing said (the title + the
foreign symbol) so the deterministic screen can set it aside on READ — the row stays on the tape and in the
display flow; only the CALL detectors screen it. Append-only re-version (the table's own correction
discipline, the ``no_update`` trigger): a NEW VERSION of each target row, every column copied VERBATIM,
ONLY ``security_title`` + ``issuer_foreign_symbol`` set from the re-parse, ``supersedes`` linking the old id,
``recorded_at`` left to the DB ``now()`` — NEVER backdated (the demo-rebuild lesson). The shared as-of read
serves the repaired title for any ``known_at`` >= the run; a replay pinned earlier still sees NULL.

MECHANISM — re-parse the CACHED filing (``forms/<accession>/<doc>`` — the immutable cache class), the same
mechanism as ``pipeline.backfill_aff10b5_1``. ``security_title`` is PER-TRANSACTION, so it is matched to each
stored row by ``txn_seq`` (the row's position within the filing — exactly the index the ingest assigned from
``enumerate(parse_form4(xml))``). ``issuer_foreign_symbol`` is filing-level, identical on every row.

RULES:
- **NULL-only.** Targets latest-version rows with ``security_title IS NULL``. A repaired row's latest version
  is non-NULL, so a re-run targets nothing (idempotent — count the TABLE, not the read).
- **KEEP-NOT-DELETE.** This tool NEVER deletes; it only appends corrected versions.
- **Scoped, TSM-first.** ``--ticker``/``--cik`` scopes to one issuer's master row(s); ``--all`` widens to the
  whole tape. Default is ``--ticker TSM``. A scope that resolves to no master row is a loud no-op.
- **Cache-only, structurally.** Filings are read straight off disk under ``<cache>/forms/`` — no network.
- **Fail-visible per filing.** One uncached / unparseable filing is skipped-and-counted, never aborts; the
  target rows stay NULL (KEPT — recall-safe #9) and a re-run re-attempts them.
- **Dry-run by default.** ``--apply`` writes; without it the full read + parse runs and reports what WOULD
  change, touching nothing.

    python -m pipeline.repair_adr_insider_misattribution                 # DRY-RUN, TSM (default)
    python -m pipeline.repair_adr_insider_misattribution --ticker TSM --apply
    python -m pipeline.repair_adr_insider_misattribution --all           # DRY-RUN, whole tape

DO NOT run ``--apply`` against prod from a build — the main loop runs it on prod AFTER a backup, and a data
repair is reviewed against the LIVE natural-key constraint first (the 0037 lesson). The build validates it on
the auto-derived test DB only.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID, database_url
from ingest.edgar.form4 import parse_form4
from pipeline.backfill_aff10b5_1 import cached_form_doc

# The runtime cache home (mirrors ingest.edgar.client._DEFAULT_CACHE / backfill_aff10b5_1._DEFAULT_CACHE).
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "edgar_cache"

# The latest version per logical insider fact (the same grain the as-of read dedups on, the 0037
# per-security key). Optionally scoped to a set of security_ids ({scope}).
_LATEST_TARGETS = """
    SELECT * FROM (
        SELECT DISTINCT ON (tenant_id, security_id, accession, insider_name, valid_from, txn_seq) *
        FROM fact_insider_txn
        WHERE tenant_id = %(tenant)s {scope}
        ORDER BY tenant_id, security_id, accession, insider_name, valid_from, txn_seq,
                 recorded_at DESC, id DESC
    ) latest
    WHERE security_title IS NULL
"""

# Columns NOT copied onto a correction row: id (new version, new id), recorded_at (DB now() — never
# backdated), supersedes (set to the corrected row's id), and the two columns the correction itself sets.
# Everything else copies verbatim, so the ONLY delta is the two S2c fields.
_NOT_COPIED = frozenset(
    {"id", "recorded_at", "supersedes", "security_title", "issuer_foreign_symbol"}
)


@dataclass
class RepairResult:
    """One run's full accounting — printed verbatim so the operator can count the table, not just trust the
    read. ``by_security`` breaks the re-versioned rows out per scoped issuer."""

    applied: bool
    table_rows_before: int = 0
    table_rows_after: int = 0
    accessions_targeted: int = (
        0  # distinct accessions with >=1 latest-version NULL-title row in scope
    )
    accessions_repaired: int = 0  # cached + parsed + a title present -> rows appended
    accessions_not_cached: int = 0
    accessions_parse_error: int = 0
    rows_reversioned: int = 0  # target rows given a new version with the title/foreign symbol set
    rows_residual_null: int = (
        0  # target rows left NULL (uncached / unparseable / no title / seq gap)
    )
    by_security: list[dict] = field(default_factory=list)  # {ticker, security_id, rows}


def _redact(url: str) -> str:
    """The DSN with its password masked — printed on every run so the target is explicit."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url)


def _count(conn: psycopg.Connection, tenant_id) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn WHERE tenant_id = %s", (tenant_id,))
        return cur.fetchone()["n"]


def _require_security_scoped_natural_key(conn: psycopg.Connection) -> None:
    """FAIL LOUD, before the first write, unless ``fact_insider_txn_natural_key`` carries ``security_id``
    (migration 0037) — the same precondition the aff_10b5_1 backfill enforces. A batch's same-instant
    re-versions of one filing key held under two securities (one dual-listed issuer, two master rows)
    would otherwise collide on ``recorded_at`` (``now()`` is constant within a batch) and abort mid-run
    (the 2026-08-17 prod abort). Refusing up front turns that crash into an instruction."""
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
            "same-instant re-versions of one filing under two securities would collide mid-batch "
            "(the 2026-08-17 prod abort). Run `python -m db.migrate` first; refusing."
        )


def resolve_scope(
    conn: psycopg.Connection, tenant_id, *, ticker: str | None, cik: str | None, all_: bool
) -> tuple[list[dict] | None, str]:
    """Resolve the repair scope to a list of master rows ``[{id, ticker, cik}]`` (``--ticker``/``--cik``),
    or ``None`` for the whole tape (``--all``). Returns ``(rows_or_None, human_label)``. A ``--ticker`` /
    ``--cik`` that matches no master row returns ``([], label)`` — a loud, empty no-op (never a silent
    widen to the whole tape)."""
    if all_:
        return None, "ALL securities"
    with conn.cursor() as cur:
        if cik is not None:
            cur.execute(
                "SELECT id, ticker, cik FROM security_master WHERE tenant_id = %s AND cik = %s",
                (tenant_id, cik),
            )
            label = f"CIK {cik}"
        else:
            tk = (ticker or "").upper()
            cur.execute(
                "SELECT id, ticker, cik FROM security_master WHERE tenant_id = %s AND ticker = %s",
                (tenant_id, tk),
            )
            label = f"ticker {tk}"
        return cur.fetchall(), label


def _target_rows(conn: psycopg.Connection, tenant_id, security_ids: list | None) -> list[dict]:
    """The latest-version rows with ``security_title IS NULL`` in scope (all columns — copied verbatim on
    re-version). ``security_ids=None`` = the whole tape."""
    if security_ids is None:
        sql = _LATEST_TARGETS.format(scope="")
        params: dict = {"tenant": tenant_id}
    else:
        sql = _LATEST_TARGETS.format(scope="AND security_id = ANY(%(sids)s)")
        params = {"tenant": tenant_id, "sids": security_ids}
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _is_tolerable(e: Exception) -> bool:
    """A per-filing fetch/parse failure (skip-and-count), not a systemic one. Mirrors the ingest's
    tolerance: an unparseable cached doc (``ET.ParseError``/``ValueError``) or a read error
    (``OSError``/``UnicodeDecodeError``). DB errors re-raise (they are not one filing's fault)."""
    import xml.etree.ElementTree as ET

    return isinstance(e, (ET.ParseError, ValueError, OSError, UnicodeDecodeError))


def run_repair(
    conn: psycopg.Connection,
    *,
    tenant_id=DEFAULT_TENANT_ID,
    cache_dir: Path = _DEFAULT_CACHE,
    security_ids: list | None = None,
    tickers_by_id: dict | None = None,
    apply: bool,
    log=print,
) -> RepairResult:
    """Re-parse each in-scope filing's cached XML and (optionally) append a corrected version of every
    latest-version NULL-title row, setting ``security_title`` (by ``txn_seq``) + ``issuer_foreign_symbol``.
    ``apply=False`` computes + reports what WOULD change, touching nothing (the caller rolls back).
    Idempotent: a re-run finds no NULL-title target and appends zero. NEVER deletes."""
    forms = cache_dir / "forms"
    if not forms.is_dir():
        raise SystemExit(f"no forms/ under cache dir {cache_dir} — wrong --cache-dir? refusing")
    if apply:  # write-path precondition (0037); a dry-run reads fine on any schema
        _require_security_scoped_natural_key(conn)
    res = RepairResult(applied=apply)
    res.table_rows_before = _count(conn, tenant_id)
    targets = _target_rows(conn, tenant_id, security_ids)

    by_acc: dict[str, list[dict]] = {}
    for r in targets:
        by_acc.setdefault(r["accession"], []).append(r)
    res.accessions_targeted = len(by_acc)
    per_sid: dict = {}

    for i, (acc, rows) in enumerate(sorted(by_acc.items())):
        doc = cached_form_doc(cache_dir, acc)
        if doc is None:
            res.accessions_not_cached += 1
            res.rows_residual_null += len(rows)
            continue
        try:
            parsed = parse_form4(doc.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — one bad cached doc never aborts the run
            if not _is_tolerable(e):
                raise
            res.accessions_parse_error += 1
            res.rows_residual_null += len(rows)
            log(f"  warn: {acc} unparseable ({e.__class__.__name__}: {e}) — left NULL")
            continue
        by_seq = dict(enumerate(parsed))  # txn_seq -> parsed txn (the ingest's own index)
        repaired_here = 0
        for row in rows:
            p = by_seq.get(row["txn_seq"])
            title = p.get("security_title") if p else None
            if (
                title is None
            ):  # seq gap / a filing with no title for this row -> stays NULL, counted
                res.rows_residual_null += 1
                continue
            if apply:
                values = {k: v for k, v in row.items() if k not in _NOT_COPIED}
                values["security_title"] = title
                values["issuer_foreign_symbol"] = p.get("issuer_foreign_symbol")
                values["supersedes"] = row["id"]
                append_fact(conn, "fact_insider_txn", values)
            res.rows_reversioned += 1
            per_sid[row["security_id"]] = per_sid.get(row["security_id"], 0) + 1
            repaired_here += 1
        if repaired_here:
            res.accessions_repaired += 1
        if apply and (i % 200 == 199):
            conn.commit()  # per-batch: a crash keeps progress; a re-run resumes at remaining NULLs

    if apply:
        conn.commit()
    res.table_rows_after = _count(conn, tenant_id)
    res.by_security = [
        {"security_id": sid, "ticker": (tickers_by_id or {}).get(sid), "rows": n}
        for sid, n in sorted(per_sid.items(), key=lambda kv: -kv[1])
    ]
    return res


def _print(res: RepairResult, scope_label: str) -> None:
    mode = "APPLY (rows re-versioned)" if res.applied else "DRY-RUN (nothing written)"
    print(f"\n=== repair ADR/dual-listed insider mis-attribution — {mode} ===")
    print(f"  scope                    : {scope_label}")
    print(f"  table rows before        : {res.table_rows_before}")
    print(f"  table rows after         : {res.table_rows_after}")
    print(f"  rows appended            : {res.table_rows_after - res.table_rows_before}")
    verb = "re-versioned" if res.applied else "WOULD re-version"
    print(f"  rows {verb:16}: {res.rows_reversioned}")
    print(f"  accessions targeted      : {res.accessions_targeted}")
    print(f"  accessions repaired      : {res.accessions_repaired}")
    print(f"  accessions not cached    : {res.accessions_not_cached} (rows stay NULL — KEPT)")
    print(f"  accessions parse-error   : {res.accessions_parse_error} (rows stay NULL — KEPT)")
    print(f"  rows residual NULL       : {res.rows_residual_null}")
    print("  NB never deletes — every change is an append-only new version (KEEP-NOT-DELETE)")
    if res.by_security:
        print("  per-security (ticker · security_id · rows re-versioned):")
        for b in res.by_security:
            print(f"    {b['ticker'] or '?':8} {b['security_id']}  {b['rows']}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill security_title + issuer_foreign_symbol onto the stored insider tape "
        "(append-only re-version; NULL-only; scoped TSM-first; KEEP-NOT-DELETE; dry-run by default)."
    )
    p.add_argument("--apply", action="store_true", help="write the re-versions (default: dry-run)")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--ticker", default="TSM", help="scope to this master ticker (default: TSM)")
    scope.add_argument("--cik", default=None, help="scope to this master CIK instead of a ticker")
    scope.add_argument(
        "--all", action="store_true", help="widen to the WHOLE tape (all securities)"
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
        "--tenant", default=str(DEFAULT_TENANT_ID), help="tenant id to scope the repair to"
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
        # --ticker's default is "TSM"; an explicit --cik/--all overrides it via the mutually-exclusive group
        ticker = None if (args.cik or args.all) else args.ticker
        scope_rows, scope_label = resolve_scope(
            conn, args.tenant, ticker=ticker, cik=args.cik, all_=args.all
        )
        if scope_rows is not None and not scope_rows:
            print(f"\nscope {scope_label} matched NO master row — nothing to repair (loud no-op).")
            return
        security_ids = None if scope_rows is None else [r["id"] for r in scope_rows]
        tickers_by_id = None if scope_rows is None else {r["id"]: r["ticker"] for r in scope_rows}
        res = run_repair(
            conn,
            tenant_id=args.tenant,
            cache_dir=args.cache_dir,
            security_ids=security_ids,
            tickers_by_id=tickers_by_id,
            apply=args.apply,
        )
        _print(res, scope_label)
        if not args.apply:
            conn.rollback()  # a dry-run writes nothing
    finally:
        conn.close()


if __name__ == "__main__":
    main()
