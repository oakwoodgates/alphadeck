"""The R8 RE-DERIVATION (``repositories.insider_amendment_repo.rederive_plan`` / ``rederive`` and the
``pipeline.rederive_amendment_matches`` CLI) on Postgres, on REAL filings (``tests/form4a_fixtures.py`` — CZR).

THE STORED STATE BEING REPAIRED is what prod's first live night (2026-10-20) wrote: each resolvable 4/A applied by
the SYSTEM with no row match (``replaced_rows`` NULL — the whole original), because the row match did not exist
yet. It is reproduced here by ingesting the real documents with the row match switched off (``_pre_row_match`` —
the code prod ran), then the re-derivation re-records them. Every assertion COUNTS THE TABLE (the as-of read
dedups, so a duplicate append would hide behind a correct read), and the PIT test pins a read before the
re-derivation's stamp: it must see exactly what the system held then (#1).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import domain.insider_amendments as ia
from db.bitemporal import as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from pipeline import rederive_amendment_matches as RD
from repositories import insider_amendment_repo as repo
from tests.form4a_fixtures import ingest_amendment, ingest_originals, make_security

T = DEFAULT_TENANT_ID
FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
ASOF = date(2026, 10, 21)
ORIGINAL = "0000899243-17-011774"
EARLY = "0000899243-17-012327"  # the merger-day A line restated as 33,118 shares
LATE = "0000899243-17-014616"  # the 2017-05-01 withholding restated as 2,859 shares


def _count(db) -> int:
    db.rollback()  # a fresh snapshot: another session (the CLI's own connection) may have committed
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment_decision")
        return cur.fetchone()["n"]


def _decisions(db, sid) -> list[dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_decision WHERE security_id = %s ORDER BY recorded_at, id",
            (sid,),
        )
        return cur.fetchall()


def _rows(db, sid, known_at: datetime) -> list[dict]:
    return as_of(db, "fact_insider_txn", security_id=sid, asof=ASOF, known_at=known_at, tenant_id=T)


def _shape(rows: list[dict]) -> list[tuple]:
    return sorted(
        (r["accession"], r["txn_seq"], r["txn_code"], float(r["shares"]))
        for r in rows
        if r.get("amends_accession", r["accession"]) == ORIGINAL or r["accession"] == ORIGINAL
    )


def _legacy_czr(db, monkeypatch) -> tuple:
    """CZR as prod holds it: the real Form 4, then the two real 4/As linked and applied by the system with NO
    row match (``match_for`` off — the first live night's code), both recorded a day ago."""
    sid = make_security(db, "CZR")
    ingest_originals(db, sid, "CZR")
    t1 = db_now(db) - timedelta(days=1)
    with monkeypatch.context() as m:
        m.setattr(
            ia, "match_for", lambda *a, **k: None
        )  # _pre_row_match: what prod ran on 2026-10-20
        for acc in (EARLY, LATE):
            out = ingest_amendment(db, sid, acc, recorded_at=t1)
            assert (out.link.disposition, out.link.original_accessions) == ("partial", (ORIGINAL,))
    return sid, t1


def test_czrs_legacy_whole_decisions_are_rederived_append_only_and_an_earlier_read_is_unchanged(
    db, monkeypatch
):
    """Prod's CZR state: both partials applied whole (``system:no-signal-effect``, NULL), so the later one
    replaced the WHOLE merger-day Form 4 and superseded the earlier one — the read held one line of the 12. The
    re-derivation revokes both legacy decisions and records each with its row match, stamped now: a read pinned
    before sees the one line, a read after sees the ten restored original lines, the 33,118 restatement and the
    2,859 withholding."""
    sid, t1 = _legacy_czr(db, monkeypatch)
    legacy = _decisions(
        db, sid
    )  # both at t1: their (recorded_at, id) order is the uuids' — sort to compare
    assert sorted(
        (d["amendment_accession"], d["action"], d["basis"], d["replaced_rows"]) for d in legacy
    ) == [
        (EARLY, "apply", "system:no-signal-effect", None),
        (LATE, "apply", "system:no-signal-effect", None),
    ]
    before = _rows(db, sid, FAR)
    assert _shape(before) == [(LATE, 0, "F", 2859.0)]
    n0 = _count(db)

    plan = repo.rederive_plan(db)
    db.rollback()
    assert plan.considered == 2
    assert (plan.unchanged, plan.unresolved, plan.operator_held, plan.lost) == ([], [], [], [])
    got = {i.accession: i for i in plan.items}
    assert set(got) == {EARLY, LATE}
    assert got[EARLY].replaced_rows == () and len(got[EARLY].kept_rows) == 11
    assert got[LATE].replaced_rows == (f"{ORIGINAL}#3",)
    assert {(i.basis, i.signal_effect) for i in plan.items} == {("system:no-signal-effect", False)}
    assert len(plan.gained) == 11

    res = repo.rederive(db, plan)
    db.commit()
    assert (res.revokes, res.decisions, res.skipped) == (2, 2, ())
    assert _count(db) == n0 + 4  # COUNT THE TABLE: one revoke + one apply per legacy decision

    rows = _decisions(db, sid)
    assert rows[:2] == legacy  # never rewritten
    new = rows[2:]
    revokes = sorted((d for d in new if d["action"] == "revoke"), key=lambda d: str(d["revokes"]))
    assert sorted(str(d["revokes"]) for d in revokes) == sorted(str(d["id"]) for d in legacy)
    assert {d["basis"] for d in revokes} == {"system:rederive"}
    applies = {d["amendment_accession"]: d for d in new if d["action"] == "apply"}
    assert applies[EARLY]["replaced_rows"] == []
    assert applies[LATE]["replaced_rows"] == [f"{ORIGINAL}#3"]
    assert {d["basis"] for d in applies.values()} == {"system:no-signal-effect"}
    assert {d["detail"]["via"] for d in applies.values()} == {"rederive"}
    stamp = new[0]["recorded_at"]
    assert {d["recorded_at"] for d in new} == {stamp} and stamp > t1

    after = _rows(db, sid, FAR)
    original_lines = [(ORIGINAL, i) for i in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10)]
    assert [(a, s) for a, s, _, _ in _shape(after)] == sorted(
        original_lines + [(EARLY, 0), (LATE, 0)]
    )
    assert (EARLY, 0, "A", 33118.0) in _shape(after)
    # PIT: pinned between the legacy decisions and the re-derivation, the read is EXACTLY what the system held
    assert _rows(db, sid, stamp - timedelta(microseconds=1)) == before
    # the review still reads both as resolved by the system — the system's own revoke is not the operator's
    found = repo.items(db, security_ids=[sid], asof=ASOF)
    db.rollback()
    assert {(i.accession, i.review, i.system_basis) for i in found} == {
        (EARLY, "auto", "system:no-signal-effect"),
        (LATE, "auto", "system:no-signal-effect"),
    }


def test_a_rerun_plans_nothing_and_appends_nothing(db, monkeypatch):
    _legacy_czr(db, monkeypatch)
    repo.rederive(db, repo.rederive_plan(db))
    db.commit()
    n1 = _count(db)
    again = repo.rederive_plan(db)
    db.rollback()
    assert again.considered == 0 and again.items == [] and again.gained == []
    res = repo.rederive(db, again)
    db.commit()
    assert (res.revokes, res.decisions) == (0, 0)
    assert _count(db) == n1


def test_an_operator_verdict_is_never_touched_and_the_other_amendment_still_rederives(
    db, monkeypatch
):
    """The operator KEEPS the original against the later 4/A: its verdict stands over the system's apply, so
    that amendment is no legacy decision any more (nothing touches it); the earlier one is still applied whole
    and its row match changes the read, so it alone is re-derived."""
    sid, _ = _legacy_czr(db, monkeypatch)
    keep = repo.decide(db, sid, LATE, "keep", reason="mine", basis="operator:keep")
    db.commit()
    n0 = _count(db)
    plan = repo.rederive_plan(db)
    db.rollback()
    assert [i.accession for i in plan.items] == [EARLY] and plan.considered == 1
    repo.rederive(db, plan)
    db.commit()
    assert _count(db) == n0 + 2
    late = [d for d in _decisions(db, sid) if d["amendment_accession"] == LATE]
    assert [d["id"] for d in late if d["basis"].startswith("operator:")] == [keep["id"]]
    assert not any(
        d["action"] == "revoke" for d in late
    )  # the operator's keep and the system's apply stand
    (it,) = [i for i in repo.items(db, security_ids=[sid], asof=ASOF) if i.accession == LATE]
    db.rollback()
    assert it.review == "decided"


def test_an_amendment_the_operator_ever_decided_is_never_rederived(db, monkeypatch):
    """The operator kept the original and then UNDID it: the system's legacy apply is the standing verdict again,
    but the amendment is the operator's (anything they did stays theirs) — reported, never moved."""
    sid, _ = _legacy_czr(db, monkeypatch)
    keep = repo.decide(db, sid, LATE, "keep", reason="mine", basis="operator:keep")
    repo.revoke(db, keep["id"], reason="undo", basis="operator:undo")
    db.commit()
    n0 = _count(db)
    plan = repo.rederive_plan(db)
    db.rollback()
    assert [i.accession for i in plan.operator_held] == [LATE]
    # the later 4/A, still whole, supersedes the earlier one: re-deriving the earlier alone changes no read
    assert plan.items == [] and [i.accession for i in plan.unchanged] == [EARLY]
    repo.rederive(db, plan)
    db.commit()
    assert _count(db) == n0


# --- the CLI ---------------------------------------------------------------------------------------------------


def test_the_cli_dry_runs_read_only_then_applies_once(db, monkeypatch, capsys):
    _legacy_czr(db, monkeypatch)
    seen: dict = {}
    real = repo.rederive_plan

    def _spy(conn, **kw):
        seen["read_only"] = conn.read_only
        return real(conn, **kw)

    monkeypatch.setattr(RD.repo, "rederive_plan", _spy)
    n0 = _count(db)

    RD.main([])
    out = capsys.readouterr().out
    assert seen["read_only"] is True  # the dry run's connection physically cannot write
    assert "considered 2 legacy whole-original system decision(s)" in out
    assert "re-derive 2 (system:no-signal-effect 2;" in out
    assert (
        "rows the read GAINS (by original): 11 row(s) on 1 original(s) (1 via an amendment's rows"
        in out
    )
    assert EARLY in out and LATE in out and "DRY-RUN" in out
    assert _count(db) == n0

    RD.main(["--apply"])
    out = capsys.readouterr().out
    assert seen["read_only"] is False
    assert "APPLIED: 2 system revoke(s), 2 re-derived system decision(s)" in out
    assert _count(db) == n0 + 4

    RD.main(["--apply"])  # idempotent: nothing left to re-derive
    assert "APPLIED: 0 system revoke(s)" in capsys.readouterr().out
    assert _count(db) == n0 + 4


def test_the_cli_refuses_while_a_pass_holds_the_lock(db, monkeypatch, capsys):
    """W1: ``--apply`` takes the REPAIR lock, whose first half is the call-of-record lock — a pass (or a backfill,
    or another repair) holding it refuses the re-derivation at once: nothing written, exit 75."""
    from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock

    _legacy_czr(db, monkeypatch)
    n0 = _count(db)
    held = acquire_pass_lock(holder="test")
    try:
        with pytest.raises(SystemExit) as exc:
            RD.main(["--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING
    finally:
        held.release()
    assert "ALREADY RUNNING" in capsys.readouterr().out
    assert _count(db) == n0


def test_the_cli_refuses_while_company_writers_hold_the_fact_writer_gate(db, monkeypatch, capsys):
    """W1: the repair lock's second half is the fact-writer gate, EXCLUSIVE — a company writer mid-company (a
    Workbench / CLI ingest) holds it shared, so the re-derivation waits (bounded) and then refuses as
    ``FactWritersBusy``: nothing written, exit 75, the writer named."""
    from functools import partial

    from db.session import connect, name_session
    from db.write_lock import acquire_company_lock
    from pipeline import run_lock
    from pipeline.run_lock import EXIT_ALREADY_RUNNING

    sid, _ = _legacy_czr(db, monkeypatch)
    n0 = _count(db)
    monkeypatch.setattr(
        RD, "acquire_repair_lock", partial(run_lock.acquire_repair_lock, wait_s=0.3, poll_s=0.1)
    )
    writer = connect()
    try:
        name_session(writer, "alphadeck-writer:workbench-ingest")
        held = acquire_company_lock(writer, sid, wait_s=0)
        try:
            with pytest.raises(SystemExit) as exc:
                RD.main(["--apply"])
            assert exc.value.code == EXIT_ALREADY_RUNNING
        finally:
            held.release()
    finally:
        writer.close()
    out = capsys.readouterr().out
    assert "fact writers" in out and "alphadeck-writer:workbench-ingest" in out
    assert _count(db) == n0
    RD.main(["--apply"])  # the writer drained: the re-derivation runs
    assert "APPLIED: 2 system revoke(s), 2 re-derived system decision(s)" in capsys.readouterr().out
    assert _count(db) == n0 + 4
