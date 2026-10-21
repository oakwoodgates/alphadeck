"""The R8 linker fix's RE-LINK (``repositories.insider_amendment_repo.relink_plan`` / ``relink`` and the
``pipeline.relink_amendments`` CLI) on Postgres, on REAL filings (``tests/form4a_fixtures.py``).

THE STORED STATE BEING REPAIRED is what prod holds: the 4/A linked by the PRE-FIX cascade, which had neither the
order test nor the row tie-break. It is reproduced here by linking the real documents with those two rules
switched off (``_pre_fix`` — exactly the code prod ran), then the fixed cascade re-links it. Every assertion
COUNTS THE TABLES (the as-of read dedups, so a duplicate append would hide behind a correct read), and the PIT
tests pin a read before the re-link's stamp: it must see exactly what the system held then (#1).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import domain.insider_amendments as ia
from db.bitemporal import as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.insider_amendments import RELINK_ORDER, RELINK_TIE_BREAK
from ingest.edgar.form4a import ingest_form4a
from pipeline import relink_amendments as RL
from repositories import insider_amendment_repo as repo
from tests.form4a_fixtures import (
    CIKS,
    FILINGS,
    accepted_by_accession,
    filed_by_accession,
    ingest_amendment,
    ingest_originals,
    make_security,
    xml,
)

T = DEFAULT_TENANT_ID
FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
IEHC = "IEHC-MISLINK"
IEHC_4A = "0001174947-26-000336"
SKYT_4A = "0001856680-26-000016"


def _index(key: str):
    """The linker's filing index for one fixture group: the real submissions rows (filing date + acceptance)."""
    return lambda _sid, _cik: (filed_by_accession(key), accepted_by_accession(key))


def _count(db, table: str) -> int:
    db.rollback()  # a fresh snapshot: another session (the CLI's own connection) may have committed
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed table names
        return cur.fetchone()["n"]


def _rows(db, sid, asof: date, known_at: datetime) -> list[dict]:
    return as_of(db, "fact_insider_txn", security_id=sid, asof=asof, known_at=known_at, tenant_id=T)


def _pre_fix(monkeypatch) -> None:
    """The cascade prod ran before the fix: no order test, no row tie-break."""
    monkeypatch.setattr(ia, "made_public_after", lambda *a, **k: False)
    monkeypatch.setattr(ia, "row_tie_break", lambda *a, **k: None)


def _store_pre_fix(db, monkeypatch, key: str, accession: str, *, at: datetime):
    """Store the real originals, then the real 4/A linked by the PRE-FIX cascade at ``at`` (prod's state)."""
    sid = make_security(db, key)
    ingest_originals(db, sid, key)
    f = FILINGS[accession]
    with monkeypatch.context() as m:
        _pre_fix(m)
        out = ingest_form4a(
            db,
            sid,
            xml(accession),
            accession,
            member_cik=f.cik,
            filed_by_accession=filed_by_accession(key),
            accepted_by_accession=accepted_by_accession(key),
            filed=f.filed,
            accepted=f.accepted,
            recorded_at=at,
        )
    db.commit()
    return sid, out


def _decisions(db, sid) -> list[dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_decision WHERE security_id = %s ORDER BY recorded_at, id",
            (sid,),
        )
        return cur.fetchall()


# --- IEHC: the mis-link prod holds ---------------------------------------------------------------------------


def test_the_iehc_mislink_is_relinked_append_only_and_an_earlier_read_is_unchanged(db, monkeypatch):
    """Prod's IEHC 4/A is linked to ``-000338`` (filed the same day, accepted 49 s AFTER it) as a restatement,
    and the sweep applied it (``system:no-signal-effect``, replacing nothing) — so the read carries the 4/A's
    M / F rows BESIDE the July 2025 originals they duplicate. The re-link appends a link to ``-25-000979``
    (clerical) and the system's revoke of its stale decision, both stamped now: a read pinned before sees the old
    state, a read after sees the July 2025 rows once."""
    t1 = db_now(db) - timedelta(days=1)
    sid, out = _store_pre_fix(db, monkeypatch, IEHC, IEHC_4A, at=t1)
    assert (out.link.original_accessions, out.link.disposition) == (
        ("0001174947-26-000338",),
        "restatement",
    )
    (sys_apply,) = _decisions(db, sid)
    assert (sys_apply["action"], sys_apply["basis"], sys_apply["replaced_rows"]) == (
        "apply",
        "system:no-signal-effect",
        [],
    )
    asof = date(2026, 10, 20)
    before_rows = _rows(db, sid, asof, FAR)
    dup = [r for r in before_rows if r["accession"] == IEHC_4A]
    assert sorted((r["txn_code"], float(r["shares"])) for r in dup) == [
        ("F", 29190.0),
        ("M", 46217.0),
    ]
    links0, decs0 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")

    plan = repo.relink_plan(db, index=_index(IEHC))
    db.rollback()
    (item,) = plan.items
    assert (item.accession, item.rule) == (IEHC_4A, RELINK_ORDER)
    assert item.revoke is not None and item.revoke["id"] == sys_apply["id"]
    res = repo.relink(db, plan)
    db.commit()
    assert (res.links, res.revokes, res.decisions) == (1, 1, 0)
    # COUNT THE TABLES: exactly one link version and one decision (the revoke) appended
    assert _count(db, "insider_amendment_link") == links0 + 1
    assert _count(db, "insider_amendment_decision") == decs0 + 1

    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_link WHERE security_id = %s ORDER BY recorded_at",
            (sid,),
        )
        old, new = cur.fetchall()
    assert new["original_accessions"] == ["0001174947-25-000979"]
    assert (new["method"], new["disposition"], new["status"]) == (
        "owner_txn_date",
        "clerical",
        "inert",
    )
    assert new["detail"]["relink"]["rule"] == RELINK_ORDER
    assert new["detail"]["excluded_later"] == ["0001174947-26-000338"]
    assert old["original_accessions"] == ["0001174947-26-000338"]  # never rewritten
    revoke = _decisions(db, sid)[-1]
    assert (revoke["action"], revoke["revokes"], revoke["basis"]) == (
        "revoke",
        sys_apply["id"],
        "system:relink",
    )
    stamp = new["recorded_at"]
    assert revoke["recorded_at"] == stamp and stamp > t1

    after_rows = _rows(db, sid, asof, FAR)
    assert not [r for r in after_rows if r["accession"] == IEHC_4A]  # clerical: nothing emitted
    assert len(after_rows) == len(before_rows) - 2
    # PIT: pinned between the old link and the re-link, the read is EXACTLY what the system held then
    assert _rows(db, sid, asof, stamp - timedelta(microseconds=1)) == before_rows
    # and the review reads it as inert — the system's own revoke is not the operator taking it back
    (it,) = repo.items(db, security_ids=[sid], asof=asof)
    db.rollback()
    assert (it.review, it.needs_operator) == ("inert", False)


def test_the_fixed_ingest_links_iehc_right_and_leaves_the_relink_nothing_to_do(db):
    """A NEW 4/A links under the fixed cascade at ingest (the leg threads the index's acceptance): IEHC's goes
    to its July 2025 original as clerical — no decision, no emitted duplicate — and a re-link plan is empty.
    """
    sid = make_security(db, IEHC)
    ingest_originals(db, sid, IEHC)
    before = _rows(db, sid, date(2026, 10, 20), FAR)
    out = ingest_amendment(db, sid, IEHC_4A)
    assert (out.link.original_accessions, out.link.disposition) == (
        ("0001174947-25-000979",),
        "clerical",
    )
    assert out.link.excluded_later == ("0001174947-26-000338",)
    assert _decisions(db, sid) == []
    assert _rows(db, sid, date(2026, 10, 20), FAR) == before
    plan = repo.relink_plan(db, index=_index(IEHC))
    db.rollback()
    assert plan.items == [] and plan.drift == [] and plan.considered == 1


def test_a_rerun_relinks_nothing_and_appends_nothing(db, monkeypatch):
    sid, _ = _store_pre_fix(db, monkeypatch, IEHC, IEHC_4A, at=db_now(db) - timedelta(days=1))
    repo.relink(db, repo.relink_plan(db, index=_index(IEHC)))
    db.commit()
    links1, decs1 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")
    again = repo.relink_plan(db, index=_index(IEHC))
    db.rollback()
    assert again.items == [] and again.drift == [] and again.considered == 1
    res = repo.relink(db, again)
    db.commit()
    assert (res.links, res.revokes, res.decisions) == (0, 0, 0)
    assert _count(db, "insider_amendment_link") == links1
    assert _count(db, "insider_amendment_decision") == decs1


def test_an_amendment_the_operator_decided_is_never_relinked(db, monkeypatch):
    sid, _ = _store_pre_fix(db, monkeypatch, IEHC, IEHC_4A, at=db_now(db) - timedelta(days=1))
    repo.decide(db, sid, IEHC_4A, "keep", reason="mine", basis="operator:keep")
    db.commit()
    links0, decs0 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")
    plan = repo.relink_plan(db, index=_index(IEHC))
    db.rollback()
    assert plan.items == [] and [i.accession for i in plan.operator_held] == [IEHC_4A]
    repo.relink(db, plan)
    db.commit()
    assert _count(db, "insider_amendment_link") == links0
    assert _count(db, "insider_amendment_decision") == decs0


def test_a_security_with_no_filing_index_is_not_judged(db, monkeypatch):
    _store_pre_fix(db, monkeypatch, IEHC, IEHC_4A, at=db_now(db) - timedelta(days=1))
    plan = repo.relink_plan(db, index=lambda _sid, _cik: None)
    db.rollback()
    assert plan.items == [] and plan.considered == 0 and len(plan.no_index) == 1


# --- SKYT: the ambiguous link the row tie-break settles --------------------------------------------------------


def test_skyts_stored_ambiguous_link_is_settled_and_applied_by_the_system(db, monkeypatch):
    """Prod holds SKYT's 4/A as AMBIGUOUS (owed). The re-link appends the tie-break's link to ``-000013`` and the
    system's ``apply`` against it (only the restated sale replaced) — no revoke: nothing was decided before.
    """
    sid, out = _store_pre_fix(db, monkeypatch, "SKYT", SKYT_4A, at=db_now(db) - timedelta(days=1))
    assert out.link.disposition == "ambiguous" and _decisions(db, sid) == []
    (owed,) = repo.items(db, security_ids=[sid], asof=date(2026, 10, 20))
    assert owed.review == "owed"
    links0, decs0 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")
    plan = repo.relink_plan(db, index=_index("SKYT"))
    db.rollback()
    (item,) = plan.items
    assert (item.rule, item.revoke, item.new.original_accessions) == (
        RELINK_TIE_BREAK,
        None,
        ("0001856680-26-000013",),
    )
    res = repo.relink(db, plan)
    db.commit()
    assert (res.links, res.revokes, res.decisions) == (1, 0, 1)
    assert _count(db, "insider_amendment_link") == links0 + 1
    assert _count(db, "insider_amendment_decision") == decs0 + 1
    (d,) = _decisions(db, sid)
    assert (d["action"], d["basis"], d["replaced_rows"], d["detail"]["via"]) == (
        "apply",
        "system:filer-correction",
        ["0001856680-26-000013#2"],
        "relink",
    )
    (done,) = repo.items(db, security_ids=[sid], asof=date(2026, 10, 20))
    db.rollback()
    assert (done.review, done.effective.original_accessions) == (
        "auto",
        ("0001856680-26-000013",),
    )


# --- the CLI ---------------------------------------------------------------------------------------------------


def _submissions_cache(tmp_path, key: str):
    """A cache dir holding the company's submissions index for ``key`` — the REAL FILINGS rows (filing date +
    acceptance, as cached) in the index's own shape; the CLI reads it cache-only."""
    rows = [(a, f) for a, f in sorted(FILINGS.items()) if f.key == key]
    recent = {
        "accessionNumber": [a for a, _ in rows],
        "form": [f.form for _, f in rows],
        "filingDate": [f.filed.isoformat() for _, f in rows],
        "acceptanceDateTime": [f.accepted.strftime("%Y-%m-%dT%H:%M:%S.000Z") for _, f in rows],
        "primaryDocument": ["ownership.xml" for _ in rows],
        "reportDate": ["" for _ in rows],
    }
    d = tmp_path / "edgar_cache" / "submissions"
    d.mkdir(parents=True)
    (d / f"CIK{int(CIKS[key]):010d}.json").write_text(
        json.dumps({"filings": {"recent": recent}}), encoding="utf-8"
    )
    return tmp_path / "edgar_cache"


def test_the_cli_dry_runs_read_only_then_applies_once(db, monkeypatch, tmp_path, capsys):
    sid, _ = _store_pre_fix(db, monkeypatch, IEHC, IEHC_4A, at=db_now(db) - timedelta(days=1))
    cache = _submissions_cache(tmp_path, IEHC)
    seen: dict = {}
    real = repo.relink_plan

    def _spy(conn, **kw):
        seen["read_only"] = conn.read_only
        return real(conn, **kw)

    monkeypatch.setattr(RL.repo, "relink_plan", _spy)
    links0, decs0 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")

    RL.main(["--cache-dir", str(cache)])
    out = capsys.readouterr().out
    assert seen["read_only"] is True  # the dry run's connection physically cannot write
    assert "re-link 1 (1 made public after the amendment, 0 row tie-break)" in out
    assert IEHC_4A in out and "DRY-RUN" in out
    assert _count(db, "insider_amendment_link") == links0
    assert _count(db, "insider_amendment_decision") == decs0

    RL.main(["--cache-dir", str(cache), "--apply"])
    out = capsys.readouterr().out
    assert seen["read_only"] is False
    assert "APPLIED: 1 link version(s), 1 system revoke(s), 0 system decision(s)" in out
    assert _count(db, "insider_amendment_link") == links0 + 1
    assert _count(db, "insider_amendment_decision") == decs0 + 1

    RL.main(["--cache-dir", str(cache), "--apply"])  # idempotent: nothing left to move
    assert "APPLIED: 0 link version(s)" in capsys.readouterr().out
    assert _count(db, "insider_amendment_link") == links0 + 1
    assert _count(db, "insider_amendment_decision") == decs0 + 1


def test_the_cli_refuses_while_a_pass_holds_the_lock(db, monkeypatch, tmp_path, capsys):
    from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock

    _store_pre_fix(db, monkeypatch, IEHC, IEHC_4A, at=db_now(db) - timedelta(days=1))
    cache = _submissions_cache(tmp_path, IEHC)
    links0 = _count(db, "insider_amendment_link")
    held = acquire_pass_lock(holder="test")
    try:
        with pytest.raises(SystemExit) as exc:
            RL.main(["--cache-dir", str(cache), "--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING
    finally:
        held.release()
    assert _count(db, "insider_amendment_link") == links0
