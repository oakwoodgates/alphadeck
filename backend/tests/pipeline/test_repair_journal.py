"""S7 — every repair CLI that rewrites FACT history journals itself: exactly ONE ``data_repair`` row per
``--apply`` (``--execute`` for the two backfills) and per applied ``--revoke``, ZERO on a dry run — COUNTED AT
THE TABLE.

These are WIRING tests: each tool's core is stubbed (its own tests cover what it writes), and the CLI's real
``main`` runs — its real locks and connection on the test DB — so what is proven is that the journal row is
written by the CLI, on the apply path only, carrying the tool's own count. The nightly editions job reads
these rows (``editions.cut``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from repositories import data_repair_repo


def _rows(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT tool, action, rows_changed, deletes, provenance FROM data_repair ORDER BY seq"
        )
        out = cur.fetchall()
    db.commit()
    return out


def _quiet(monkeypatch, module, *names):
    for n in names:
        monkeypatch.setattr(module, n, lambda *a, **k: None)


# --- each tool: (stub its core, the dry-run argv, the apply argv, the expected journal row) -----------------


def _benchmark_bars(monkeypatch, tmp_path):
    from pipeline import repair_benchmark_bars as m

    res = SimpleNamespace(mode=m.MODE_BENCHMARKS, appended=2, count=lambda a: 0, fetch_errors={})
    monkeypatch.setattr(m, "repair_benchmark_bars", lambda conn, dates, apply: res)
    monkeypatch.setattr(m, "build_payload", lambda r: {})
    monkeypatch.setattr(m, "write_payload", lambda p: tmp_path / "bars.json")
    _quiet(monkeypatch, m, "report")
    return (
        m,
        ["--dates", "2026-08-25"],
        ["--dates", "2026-08-25", "--apply"],
        ("repair_benchmark_bars", 2),
    )


def _dedup(monkeypatch, tmp_path):
    from pipeline import dedup_identical_versions as m

    cands = SimpleNamespace(identical_copies=3, referenced_ids=[], deletable_ids=[1, 2, 3])
    monkeypatch.setattr(m, "apply_table", lambda conn, table, tenant_id=None: cands)
    monkeypatch.setattr(m, "build_report", lambda conn, table, tenant_id=None: None)
    monkeypatch.setattr(m, "row_count", lambda *a, **k: 0)
    monkeypatch.setattr(m, "table_size", lambda *a, **k: 0)
    _quiet(monkeypatch, m, "vacuum_analyze", "_print_apply", "_print_report")
    argv = ["--tables", "fact_price_eod"]
    return m, argv, [*argv, "--apply"], ("dedup_identical_versions", 3)


def _insider_ownership(monkeypatch, tmp_path):
    from pipeline import repair_insider_ownership as m

    res = SimpleNamespace(fact_rows_versioned=3, amendment_records_versioned=1, as_json=lambda: {})
    monkeypatch.setattr(m, "run_repair", lambda conn, **k: res)
    _quiet(monkeypatch, m, "report")
    argv = ["--cache-dir", str(tmp_path)]
    return m, argv, [*argv, "--apply"], ("repair_insider_ownership", 4)


def _filer_text(monkeypatch, tmp_path):
    from pipeline import repair_insider_filer_text as m

    res = SimpleNamespace(fact_rows_versioned=2, amendment_records_versioned=0, as_json=lambda: {})
    monkeypatch.setattr(m, "run_repair", lambda conn, **k: res)
    _quiet(monkeypatch, m, "report")
    argv = ["--cache-dir", str(tmp_path)]
    return m, argv, [*argv, "--apply"], ("repair_insider_filer_text", 2)


def _relink(monkeypatch, tmp_path):
    from pipeline import relink_amendments as m

    monkeypatch.setattr(m.repo, "relink_plan", lambda conn, index: SimpleNamespace())
    monkeypatch.setattr(
        m.repo,
        "relink",
        lambda conn, plan: SimpleNamespace(links=0, revokes=0, decisions=0, skipped=[]),
    )
    _quiet(monkeypatch, m, "report")
    return m, [], ["--apply"], ("relink_amendments", 0)


def _rederive(monkeypatch, tmp_path):
    from pipeline import rederive_amendment_matches as m

    monkeypatch.setattr(m.repo, "rederive_plan", lambda conn: SimpleNamespace())
    monkeypatch.setattr(
        m.repo, "rederive", lambda conn, plan: SimpleNamespace(revokes=0, decisions=0, skipped=[])
    )
    _quiet(monkeypatch, m, "report")
    return m, [], ["--apply"], ("rederive_amendment_matches", 0)


def _set_aside(monkeypatch, tmp_path):
    from pipeline import set_aside_fundamentals as m

    res = SimpleNamespace(appended=[], rows_before=0, rows_after=0)

    def fake_apply(conn, result):
        result.appended = [(uuid.uuid4(), None), (uuid.uuid4(), None)]
        return result

    monkeypatch.setattr(m, "scan", lambda conn, tickers: res)
    monkeypatch.setattr(m, "apply", fake_apply)
    monkeypatch.setattr(m, "build_payload", lambda r: {})
    monkeypatch.setattr(m, "write_payload", lambda p: tmp_path / "set-aside.json")
    _quiet(monkeypatch, m, "report")
    return m, [], ["--apply"], ("set_aside_fundamentals", 2)


def _split_basis(monkeypatch, tmp_path):
    from pipeline import repair_split_basis as m

    plans = [SimpleNamespace(status=m.REPAIR, label="BTOC", changes=[1])]
    monkeypatch.setattr(m, "plan_all", lambda conn, **k: plans)
    monkeypatch.setattr(
        m,
        "apply_plans",
        lambda conn, p: m.ApplyResult(
            recorded_at=datetime.now(timezone.utc), appended=5, companies=["BTOC"]
        ),
    )
    monkeypatch.setattr(m, "build_payload", lambda *a, **k: {})
    monkeypatch.setattr(m, "write_payload", lambda *a, **k: tmp_path / "split.json")
    _quiet(monkeypatch, m, "report")
    argv = ["--ticker", "BTOC"]
    return m, argv, [*argv, "--apply"], ("repair_split_basis", 5)


def _reingest(monkeypatch, tmp_path):
    from pipeline import reingest_fundamentals as m

    res = SimpleNamespace(appended=4, planned=4, companies=[])
    monkeypatch.setattr(m, "reingest_fundamentals", lambda conn, **k: res)
    monkeypatch.setattr(m, "build_payload", lambda r: {})
    monkeypatch.setattr(m, "write_payload", lambda p: tmp_path / "reingest.json")
    _quiet(monkeypatch, m, "report")
    return m, [], ["--apply"], ("reingest_fundamentals", 4)


def _backfill_splits(monkeypatch, tmp_path):
    from pipeline import backfill_splits as m

    monkeypatch.setattr(m, "default_split_source", lambda: object())
    monkeypatch.setattr(m, "backfill_splits", lambda conn, **k: SimpleNamespace(appended=2))
    _quiet(monkeypatch, m, "report")
    argv = ["--no-live"]
    return m, argv, [*argv, "--apply"], ("backfill_splits", 2)


def _activist(monkeypatch, tmp_path):
    from pipeline import repair_activist_misattribution as m

    res = SimpleNamespace(rows_deleted=3, filings_targeted=1)
    monkeypatch.setattr(m, "run_repair", lambda conn, **k: res)
    _quiet(monkeypatch, m, "_print")
    return m, [], ["--apply"], ("repair_activist_misattribution", 3)


def _adr(monkeypatch, tmp_path):
    from pipeline import repair_adr_insider_misattribution as m

    monkeypatch.setattr(m, "resolve_scope", lambda *a, **k: (None, "ALL securities"))
    res = SimpleNamespace(rows_reversioned=2, accessions_repaired=1)
    monkeypatch.setattr(m, "run_repair", lambda conn, **k: res)
    _quiet(monkeypatch, m, "_print")
    argv = ["--all", "--cache-dir", str(tmp_path)]
    return m, argv, [*argv, "--apply"], ("repair_adr_insider_misattribution", 2)


def _impossible(monkeypatch, tmp_path):
    from pipeline import repair_impossible_txn_dates as m

    rows = [
        SimpleNamespace(
            security_id=uuid.uuid4(),
            accession="a",
            insider_name="n",
            valid_from=date(2026, 1, 1),
            txn_seq=0,
        )
    ]
    monkeypatch.setattr(m, "find_impossible_rows", lambda conn: rows)
    monkeypatch.setattr(m, "_class_of", lambda r: "a year after the filing")
    monkeypatch.setattr(m, "delete_rows", lambda conn, r: len(r))
    _quiet(monkeypatch, m, "_print_rows")
    return m, [], ["--apply"], ("repair_impossible_txn_dates", 1)


def _accepted(monkeypatch, tmp_path):
    from pipeline import backfill_accepted as m

    res = SimpleNamespace(rows_corrected=5, rows_residual_null=1)
    monkeypatch.setattr(m, "run_backfill", lambda conn, **k: res)
    _quiet(monkeypatch, m, "_print_backfill")
    return m, [], ["--execute"], ("backfill_accepted", 5)


def _aff10b5_1(monkeypatch, tmp_path):
    from pipeline import backfill_aff10b5_1 as m

    res = SimpleNamespace(rows_to_true=2, rows_to_false=1, rows_residual_null=0)
    monkeypatch.setattr(m, "run_backfill", lambda conn, **k: res)
    _quiet(monkeypatch, m, "_print_backfill")
    argv = ["--cache-dir", str(tmp_path)]
    return m, argv, [*argv, "--execute"], ("backfill_aff10b5_1", 3)


TOOLS = {
    "repair_benchmark_bars": _benchmark_bars,
    "dedup_identical_versions": _dedup,
    "repair_insider_ownership": _insider_ownership,
    "repair_insider_filer_text": _filer_text,
    "relink_amendments": _relink,
    "rederive_amendment_matches": _rederive,
    "set_aside_fundamentals": _set_aside,
    "repair_split_basis": _split_basis,
    "reingest_fundamentals": _reingest,
    "backfill_splits": _backfill_splits,
    "repair_activist_misattribution": _activist,
    "repair_adr_insider_misattribution": _adr,
    "repair_impossible_txn_dates": _impossible,
    "backfill_accepted": _accepted,
    "backfill_aff10b5_1": _aff10b5_1,
}
DELETES = {
    "dedup_identical_versions",
    "repair_activist_misattribution",
    "repair_impossible_txn_dates",
}


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_one_journal_row_per_apply_and_none_on_a_dry_run(name, db, tmp_path, monkeypatch):
    module, dry, apply, (tool, changed) = TOOLS[name](monkeypatch, tmp_path)
    module.main(dry)
    assert _rows(db) == []  # a dry run journals nothing
    module.main(apply)
    rows = _rows(db)
    assert len(rows) == 1, rows
    (row,) = rows
    assert (row["tool"], row["action"], row["rows_changed"]) == (tool, "apply", changed)
    assert row["deletes"] is (name in DELETES)
    module.main(apply)  # a second apply is a second run: a second row
    assert len(_rows(db)) == 2


def test_set_aside_fundamentals_journals_an_applied_revoke_only(db, tmp_path, monkeypatch):
    from pipeline import set_aside_fundamentals as m

    target = uuid.uuid4()
    monkeypatch.setattr(
        m, "revoke", lambda conn, ids, *, reason, apply: [uuid.uuid4() for _ in ids]
    )
    monkeypatch.setattr(m, "write_payload", lambda p: tmp_path / "revoke.json")
    m.main(["--revoke", str(target), "--reason", "the screen was wrong"])  # dry run
    assert _rows(db) == []
    m.main(["--revoke", str(target), "--reason", "the screen was wrong", "--apply"])
    (row,) = _rows(db)
    assert (row["tool"], row["action"], row["rows_changed"]) == (
        "set_aside_fundamentals",
        "revoke",
        1,
    )
    assert row["provenance"] == str(tmp_path / "revoke.json")


def test_repair_split_basis_journals_a_revoke(db, tmp_path, monkeypatch):
    from pipeline import repair_split_basis as m

    stamp = datetime(2026, 10, 20, 15, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(
        m, "find_provenance", lambda s: (tmp_path / "p.json", {"recorded_at": stamp.isoformat()})
    )
    monkeypatch.setattr(
        m,
        "revoke",
        lambda conn, prov: m.ApplyResult(
            recorded_at=datetime.now(timezone.utc), appended=7, companies=["OPTT"]
        ),
    )
    monkeypatch.setattr(m, "write_payload", lambda *a, **k: tmp_path / "revoke.json")
    m.main(["--revoke", stamp.isoformat()])
    (row,) = _rows(db)
    assert (row["tool"], row["action"], row["rows_changed"]) == ("repair_split_basis", "revoke", 7)


def test_a_refused_apply_journals_nothing(db, tmp_path, monkeypatch):
    """A run the tool itself refused wrote nothing, so it is not journaled (the split repair's gate)."""
    from pipeline import repair_split_basis as m

    monkeypatch.setattr(
        m, "plan_all", lambda conn, **k: [SimpleNamespace(status=m.REFUSED, label="BTOC")]
    )
    _quiet(monkeypatch, m, "report")
    with pytest.raises(SystemExit):
        m.main(["--ticker", "BTOC", "--apply"])
    assert _rows(db) == []


def test_the_journal_reads_back_as_pending_for_the_editions_job(db, tmp_path, monkeypatch):
    from db.session import DEFAULT_TENANT_ID

    module, _, apply, _ = TOOLS["repair_split_basis"](monkeypatch, tmp_path)
    module.main(apply)
    (row,) = data_repair_repo.pending(
        db, tenant_id=DEFAULT_TENANT_ID, after=datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    db.commit()
    assert row.tool == "repair_split_basis" and row.rows_changed == 5
    assert row.summary.startswith("re-scaled 5 stored price bar(s) onto the split list's scale")
    assert Path(row.provenance or "").name == "split.json"
