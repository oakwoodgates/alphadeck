"""Identity coherence (the misbind class, S3) — ONE definition of shown-vs-bound disagreement, shared by
the promote write-guard and the read-side audit. The taxonomy is the load-bearing part: cross-company is
the KLAC↔LRCX / SIMO↔MXL class; sibling is the same-CIK line class (alignable); label-drift is the
MNMD→DFTX rename class (a label nothing current answers to)."""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from securities import coherence
from securities.coherence import CoherenceKind


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


def _one(db, shown, sid):
    (f,) = coherence.classify_members(db, [(shown, sid)], tenant_id=DEFAULT_TENANT_ID)
    return f


def test_agreeing_pair_is_ok_and_carries_bound_identity(db):
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    f = _one(db, "MXL", mxl)
    assert f.kind is CoherenceKind.OK
    assert (f.bound_ticker, f.bound_cik) == ("MXL", "0001288469")
    assert _one(db, "mxl", mxl).kind is CoherenceKind.OK  # case never manufactures a mismatch


def test_cross_company_names_both_sides(db):
    """THE misbind: SIMO's label riding MXL's security_id. The finding must say BOTH identities — a
    mismatch is only actionable when the operator can see who the row actually is (#6)."""
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    _insert(db, "SIMO", name="Silicon Motion Technology CORP", cik="0001329394")
    f = _one(db, "SIMO", mxl)
    assert f.kind is CoherenceKind.CROSS_COMPANY
    assert (f.bound_ticker, f.bound_name, f.bound_cik) == (
        "MXL",
        "MAXLINEAR, INC",
        "0001288469",
    )
    assert "0001329394" in f.detail  # the shown ticker's real owner, named


def test_sibling_same_cik_different_line(db):
    asml = _insert(db, "ASML", name="ASML HOLDING NV", cik="0000937966")
    _insert(db, "ASMLF", name="ASML HOLDING NV", cik="0000937966")
    f = _one(db, "ASMLF", asml)
    assert f.kind is CoherenceKind.SIBLING  # right company, another line's label — alignable
    assert f.bound_ticker == "ASML"


def test_label_drift_when_shown_matches_no_current_row(db):
    """The MNMD→DFTX class: the bound row's identity moved with the SEC file; the shown label answers to
    nothing current. Distinct from cross-company — no OTHER company claims the label."""
    dftx = _insert(db, "DFTX", name="Definium Therapeutics, Inc.", cik="0001813814")
    f = _one(db, "MNMD", dftx)
    assert f.kind is CoherenceKind.LABEL_DRIFT
    assert f.bound_ticker == "DFTX"


def test_unbound_missing_row_and_no_shown(db):
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    assert _one(db, "MXL", None).kind is CoherenceKind.UNBOUND  # nothing bound — not a question
    assert _one(db, "MXL", uuid.uuid4()).kind is CoherenceKind.MISSING_ROW  # exists() territory
    assert _one(db, None, mxl).kind is CoherenceKind.OK  # nothing shown — nothing to disagree with
    assert _one(db, "", mxl).kind is CoherenceKind.OK


def test_batch_preserves_order_and_mixes_kinds(db):
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    simo = _insert(db, "SIMO", name="Silicon Motion Technology CORP", cik="0001329394")
    findings = coherence.classify_members(
        db,
        [("SIMO", mxl), ("SIMO", simo), (None, None), ("GONE", mxl)],
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert [f.kind for f in findings] == [
        CoherenceKind.CROSS_COMPANY,
        CoherenceKind.OK,
        CoherenceKind.UNBOUND,
        CoherenceKind.LABEL_DRIFT,
    ]
