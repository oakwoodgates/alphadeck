"""The identity audit CLI (S3) — the standing 'how many more are there' answer. DB-backed (the `db`
fixture); the CLI opens its own connection to the same test DB (the daily/draft-jobs pattern). The
draft-run sweep reads a tmp dir shaped like the real write-only log (placement dicts with string ids).
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from pipeline import audit_identity


def _insert(db, ticker, *, name=None, cik=None):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _thesis_with_member(db, ticker, sid):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "Semis", "n"),
        )
        cur.execute(
            "INSERT INTO basket_member "
            "(id, tenant_id, thesis_id, ordinal, ticker, role, archetype, security_id) "
            "VALUES (%s, %s, %s, 0, %s, 'r', 'leader', %s)",
            (uuid.uuid4(), DEFAULT_TENANT_ID, tid, ticker, sid),
        )
    db.commit()
    return tid


def test_audit_clean_exits_zero(db, capsys):
    """A coherent world: master has no multi-row CIKs, the spine member's label matches its bound row, no
    draft dir passed — three OK lines, exit 0 (the cron/CI gate's green path)."""
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    _thesis_with_member(db, "MXL", mxl)
    audit_identity.main([])  # no SystemExit -> exit 0
    out = capsys.readouterr().out
    assert "master: OK" in out and "0 need review" in out and "AUDIT: clean" in out


def test_audit_finds_cross_company_and_unresolved_and_exits_nonzero(db, tmp_path, capsys):
    """The dirty world: a draft-run log holding the SIMO-label-on-MXL-id misbind (cross-company, the
    KLAC/LRCX class) and the MNMD-on-DFTX drifted label (unresolved review item). Both are PRINTED with
    both identities, counted once each despite the log repeating them (re-drafts), and the process exits
    1 — an audit that finds damage is never a green build."""
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    _insert(db, "SIMO", name="Silicon Motion Technology CORP", cik="0001329394")
    dftx = _insert(db, "DFTX", name="Definium Therapeutics, Inc.", cik="0001813814")
    _thesis_with_member(db, "MXL", mxl)  # the spine itself stays clean

    run = {
        "placements": [
            # the misbind, twice (a re-draft repeats it) -> ONE distinct review line
            {"name": "Silicon Motion Technology CORP", "ticker": "SIMO", "security_id": str(mxl)},
            {"name": "Silicon Motion Technology CORP", "ticker": "SIMO", "security_id": str(mxl)},
            # the MNMD->DFTX drifted label -> unresolved (no current row carries MNMD)
            {"name": "Mind Medicine (MindMed) Inc.", "ticker": "MNMD", "security_id": str(dftx)},
            # a clean row
            {"name": "MAXLINEAR, INC", "ticker": "MXL", "security_id": str(mxl)},
        ]
    }
    (tmp_path / "run1.json").write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        audit_identity.main(["--draft-runs", str(tmp_path)])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert out.count("DRAFT CROSS_COMPANY") == 1  # deduped: one distinct item, printed once
    assert "SIMO" in out and "MAXLINEAR" in out and "0001288469" in out  # both identities named
    assert out.count("DRAFT UNRESOLVED label") == 1
    assert "MNMD" in out and "DFTX" in out
    assert "spine: 1 bound members, 0 need review" in out  # the spine sweep stays clean here


def test_audit_flags_a_dirty_spine_member(db, capsys):
    """Pre-guard damage on the SPINE (a persisted misbound member) is the audit's loudest case — printed
    with the owning thesis and both identities, and the run exits 1."""
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    _insert(db, "SIMO", name="Silicon Motion Technology CORP", cik="0001329394")
    _thesis_with_member(db, "SIMO", mxl)  # the misbind, persisted
    with pytest.raises(SystemExit):
        audit_identity.main([])
    out = capsys.readouterr().out
    assert "SPINE CROSS_COMPANY" in out and "'Semis'" in out
    assert "1 need review" in out
