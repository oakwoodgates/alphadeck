"""Rule ``2026-11-amendment-linker-sees-held-rows`` — the Form 4/A linker reads an original's HELD rows
(``insider_txn_hold``) as rows of that filing, on REAL filings (``tests/held_fixtures.py``,
``tests/fixtures/edgar/held/``): the eight amendments of prod (MEASURED 2026-11-11) whose original holds a row
whose date cannot be true.

Five were linked wrong by the linker that saw only stored rows (the 4/A corrects only the held row, but the link
took other, real rows out of the read); three were right (the 4/A restates every row) and must stay exactly as
they are. Each case is checked two ways:

(a) THE INGEST PATH — the original ingested (its bad row held), then the 4/A, linked by today's linker: the
    insider read returns exactly the expected rows, each trade once and every real row present, on both clocks
    and on the replay mirror;
(b) THE RE-LINK PATH — prod's state rebuilt (an after-filing row stored at its written date by the older ingest
    beside its hold, an impossible-year row held only; the 4/A linked over the STORED rows alone, exactly the view
    the old linker had), then ``pipeline.relink_amendments``' plan and apply: the five wrong ones are re-linked
    (rule ``sees-held-rows``) and read right afterwards, a second apply writes nothing (the link and decision
    tables COUNTED), and the three right ones plan nothing.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

import ingest.edgar.form4 as form4_mod
from db.session import DEFAULT_TENANT_ID
from domain.insider_amendments import RELINK_HELD, with_held_rows
from domain.market_time import known_at_for_asof
from ingest.edgar.form4 import (
    HOLD_AFTER_FILING,
    HOLD_FORM_4,
    filing_held_rows,
    hold_evidence,
    ingest_form4,
    parse_form4,
    write_hold,
)
from ingest.edgar.form4a import ingest_form4a, stored_rows_by_accession
from pipeline import relink_amendments as RL
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories import insider_amendment_repo as repo
from signals.base import PointInTimeData
from tests import held_fixtures as H
from tests.db.test_insider_holds_read import _assert_equal

T = DEFAULT_TENANT_ID
FAR = H.FAR


def _cap(asof: date) -> datetime:
    return known_at_for_asof(asof, FAR)


def _read(db, sid, asof: date, clock: str) -> list[dict[str, Any]]:
    known_at = _cap(asof) if clock == "public" else FAR
    return PointInTimeData(db, asof=asof, known_at=known_at, clock=clock, workspace=T).insider_txns(
        sid
    )


def _shape(rows) -> list[tuple]:
    """Each returned row as (date, code, shares, price, the filing it reads from, how a held row is dated)."""
    return sorted(
        (
            r["valid_from"],
            r["txn_code"],
            float(r["shares"]),
            round(float(r["price"] or 0), 4),
            r["accession"],
            r.get("date_basis"),
        )
        for r in rows
    )


def _both(db, sid, asof: date) -> list[tuple]:
    pub, rec = _shape(_read(db, sid, asof, "public")), _shape(_read(db, sid, asof, "record"))
    assert pub == rec
    return pub


def _count(db, table: str, sid=None) -> int:
    db.rollback()
    with db.cursor() as cur:
        if sid is None:
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed table names
        else:
            cur.execute(
                f"SELECT count(*) AS n FROM {table} WHERE security_id = %s",  # noqa: S608
                (sid,),
            )
        return cur.fetchone()["n"]


# --- the eight cases ---------------------------------------------------------------------------------------


def _orig(group: str) -> str:
    (acc,) = H.of_group(group, "4")
    return acc


def _amend(group: str) -> str:
    (acc,) = H.of_group(group, "4/A")
    return acc


def _stored(group: str, seqs, rows) -> list[tuple]:
    """The original's rows ``seqs`` as the read returns them stored: (date, code, shares, price, accession)."""
    o = _orig(group)
    parsed = parse_form4(H.xml(o))
    return [
        (
            parsed[i]["txn_date"],
            parsed[i]["txn_code"],
            float(parsed[i]["shares"]),
            round(float(parsed[i]["price"] or 0), 4),
            o,
            None,
        )
        for i in seqs
    ] + [(d, c, s, p, _amend(group), None) for d, c, s, p in rows]


S1, A1 = "0001628280-23-039558", "0001628280-23-040161"
S2 = "0001628280-23-035012"
S3 = "0000903651-05-000023"
S4 = "0001209191-21-010430"
S5 = "0001209191-18-051954"

# group -> (the asof the 4/A is public by, the rows the read returns then, the held rows a RIGHT link replaces —
# the old linker's wrong ``replaced_rows`` when it was wrong, else None)
WRONG: dict[str, dict[str, Any]] = {
    # row 0 stored, row 1 held (an impossible year); the 4/A corrects row 1 only
    "amended_impossible_year": {
        "asof": date(2023, 11, 29),
        "rows": _stored(
            "amended_impossible_year", [0], [(date(2023, 11, 17), "S", 15000.0, 17.7108)]
        ),
        "old": ("correction", None),
        "new": ("partial", [f"{S1}#1"]),
    },
    "impossible_year_second_of_two": {
        "asof": date(2024, 2, 1),
        "rows": _stored(
            "impossible_year_second_of_two", [0], [(date(2023, 10, 24), "S", 15000.0, 14.4508)]
        ),
        "old": ("correction", None),
        "new": ("partial", [f"{S2}#1"]),
    },
    # eighteen rows, row 7 held (after the filing); the old pick replaced row 13 (same person, code and shares)
    "after_filing_one_of_eighteen": {
        "asof": date(2005, 11, 30),
        "rows": _stored(
            "after_filing_one_of_eighteen",
            [i for i in range(18) if i != 7],
            [(date(2005, 11, 14), "S", 300.0, 3.37)],
        ),
        "old": ("partial", [f"{S3}#13"]),
        "new": ("partial", [f"{S3}#7"]),
    },
    # rows 6 and 7 held (after the filing); the old pick replaced rows 4 and 5 (the same-size gifts)
    "two_after_filing_gifts": {
        "asof": date(2021, 7, 21),
        "rows": _stored(
            "two_after_filing_gifts",
            [0, 1, 2, 3, 4, 5],
            [(date(2020, 12, 31), "G", 94350.465, 0.0)] * 2,
        ),
        "old": ("partial", [f"{S4}#4", f"{S4}#5"]),
        "new": ("partial", [f"{S4}#6", f"{S4}#7"]),
    },
    # row 0 held (an impossible year), rows 1 and 2 stored; the old match replaced rows 1 and 2
    "impossible_year_first_of_three": {
        "asof": date(2020, 3, 16),
        "rows": _stored(
            "impossible_year_first_of_three", [1, 2], [(date(2018, 9, 19), "S", 110000.0, 9.61)]
        ),
        "old": ("partial", [f"{S5}#1", f"{S5}#2"]),
        "new": ("partial", [f"{S5}#0"]),
    },
}

# the amendment restates every row: the whole original replaced, before and after
RIGHT: dict[str, dict[str, Any]] = {
    "amended_after_filing": {
        "asof": date(2020, 3, 23),
        "rows": _stored(
            "amended_after_filing",
            [],
            [
                (date(2020, 3, 18), "P", 2200.0, 68.184),
                (date(2020, 3, 20), "P", 800.0, 67.5),
                (date(2020, 3, 20), "P", 750.0, 69.0),
            ],
        ),
    },
    "after_filing_sale_beside_exercises": {
        "asof": date(2016, 3, 3),
        "rows": _stored(
            "after_filing_sale_beside_exercises",
            [],
            [
                (date(2015, 11, 2), "M", 10000.0, 19.1),
                (date(2015, 11, 2), "M", 10000.0, 12.6),
                (date(2015, 11, 2), "S", 20000.0, 49.5812),
            ],
        ),
    },
    "only_row_after_filing": {
        "asof": date(2022, 1, 6),
        "rows": _stored("only_row_after_filing", [], [(date(2021, 12, 30), "A", 1000.0, 0.0)]),
    },
}

ALL = {**WRONG, **RIGHT}


def _link(db, sid) -> dict[str, Any]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_link WHERE security_id = %s "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (sid,),
        )
        return cur.fetchone()


def _standing_replaced(db, sid) -> list[str] | None:
    """The rows the standing system apply replaces (``None`` = the whole original)."""
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT d.* FROM insider_amendment_decision d WHERE d.security_id = %s AND d.action = 'apply' "
            "AND NOT EXISTS (SELECT 1 FROM insider_amendment_decision r WHERE r.revokes = d.id)",
            (sid,),
        )
        (d,) = cur.fetchall()
        return d["replaced_rows"]


# --- (a) the ingest path -----------------------------------------------------------------------------------


@pytest.mark.parametrize("group", sorted(ALL))
def test_the_ingest_links_against_the_held_rows_and_the_read_counts_every_trade_once(db, group):
    case = ALL[group]
    sid = H.seed(db, group)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_txn_hold WHERE security_id = %s", (sid,))
        assert cur.fetchone()["n"] >= 1  # the original's bad row is held
    rows = _both(db, sid, case["asof"])
    assert rows == sorted(case["rows"])
    # each trade once: no held row survives beside the amendment's row
    assert not any(r[5] for r in rows)
    link = _link(db, sid)
    assert link["original_accessions"] == [_orig(group)]
    if group in WRONG:
        disposition, replaced = case["new"]
        assert link["disposition"] == disposition
        assert sorted(_standing_replaced(db, sid)) == sorted(replaced)
    else:
        assert link["disposition"] == "correction" and _standing_replaced(db, sid) is None


def test_a_filing_whose_every_row_is_held_is_still_found_as_the_original(db):
    sid = H.seed(db, "only_row_after_filing", amendments=False)
    assert _count(db, "fact_insider_txn", sid) == 0  # nothing stored: its one row is held
    view = stored_rows_by_accession(db, sid, tenant_id=T)
    (row,) = view[_orig("only_row_after_filing")]
    assert row["held"] is True and row["valid_from"] is None
    assert row["txn_date_as_filed"] == "2022-12-30"
    db.rollback()
    for acc in H.of_group("only_row_after_filing", "4/A"):
        H.ingest(db, sid, acc)
    link = _link(db, sid)
    assert (link["method"], link["disposition"], link["original_accessions"]) == (
        "orig_date_owner",
        "correction",
        [_orig("only_row_after_filing")],
    )


@pytest.mark.parametrize("clock", ["record", "public"])
def test_the_mirror_reads_the_eight_cases_exactly_like_postgres(db, tmp_path, clock):
    sids = {g: H.seed(db, g) for g in sorted(ALL)}
    export_snapshot(db, tmp_path, clock=clock, workspace=T)
    con = connect_mirror(tmp_path)
    checked = 0
    for g, sid in sids.items():
        asof = ALL[g]["asof"]
        for known_at in (_cap(asof), FAR):
            live = PointInTimeData(db, asof=asof, known_at=known_at, clock=clock, workspace=T)
            rep = ReplayPointInTimeData(con, asof=asof, known_at=known_at, workspace=T)
            lr, rr = live.insider_txns(sid), rep.insider_txns(sid)
            _assert_equal(lr, rr)
            checked += bool(lr)
    assert checked >= len(ALL)


# --- (b) the re-link path: prod's state, re-linked ----------------------------------------------------------


def _ingest_original_as_prod(db, sid, group: str) -> None:
    """The original as prod holds it: a row dated after its filing STORED at its written date (the older ingest
    stored it) with its hold beside it (the hold backfill); an impossible-year row held only (never stored).
    """
    acc = _orig(group)
    f = H.FILINGS[acc]
    xml = H.xml(acc)
    real = filing_held_rows

    def older(rows, accession, xml_, accepted):
        return {
            k: v for k, v in real(rows, accession, xml_, accepted).items() if v != HOLD_AFTER_FILING
        }

    orig = form4_mod.filing_held_rows
    form4_mod.filing_held_rows = older
    try:
        ingest_form4(
            db,
            sid,
            xml,
            acc,
            accepted=f.accepted,
            recorded_at=f.accepted + H.INGEST_LAG,
            member_cik=H.CIKS[group],
            filed=f.filed,
        )
    finally:
        form4_mod.filing_held_rows = orig
    rows = parse_form4(xml)
    evidence = hold_evidence(xml)
    for seq, reason in real(rows, acc, xml, f.accepted).items():
        if reason == HOLD_AFTER_FILING:
            write_hold(
                db,
                tenant_id=T,
                security_id=sid,
                accession=acc,
                txn_seq=seq,
                form=HOLD_FORM_4,
                reason=reason,
                row=rows[seq],
                evidence=evidence,
                filed=f.filed,
                accepted=f.accepted,
                recorded_at=f.accepted + H.INGEST_LAG,
            )
    db.commit()


def _link_4a_as_prod(db, sid, group: str) -> None:
    """The 4/A linked by the OLD linker: over the stored rows alone (``with_holds=False``)."""
    acc = _amend(group)
    f = H.FILINGS[acc]
    originals = H.of_group(group, "4")
    ingest_form4a(
        db,
        sid,
        H.xml(acc),
        acc,
        member_cik=H.CIKS[group],
        filed_by_accession={a: H.FILINGS[a].filed for a in originals},
        accepted_by_accession={a: H.FILINGS[a].accepted for a in originals},
        filed=f.filed,
        accepted=f.accepted,
        recorded_at=f.accepted + H.INGEST_LAG,
        stored=stored_rows_by_accession(db, sid, tenant_id=T, with_holds=False),
    )
    db.commit()


def _seed_as_prod(db, group: str):
    sid = H.make_security(db, group)
    _ingest_original_as_prod(db, sid, group)
    _link_4a_as_prod(db, sid, group)
    return sid


def _index(group: str):
    originals = H.of_group(group, "4")
    return lambda _sid, _cik: (
        {a: H.FILINGS[a].filed for a in originals},
        {a: H.FILINGS[a].accepted for a in originals},
    )


@pytest.mark.parametrize("group", sorted(WRONG))
def test_a_wrong_link_is_relinked_against_the_held_rows_once(db, group):
    case = WRONG[group]
    sid = _seed_as_prod(db, group)
    old_disp, old_replaced = case["old"]
    assert _link(db, sid)["disposition"] == old_disp
    replaced = _standing_replaced(db, sid)
    assert (sorted(replaced) if replaced is not None else None) == old_replaced
    before = _both(db, sid, case["asof"])
    # the old link takes real rows out of the read: the original's rows its decision replaces (all of them for a
    # whole correction), the held row's trade still counted once, from the amendment
    if old_replaced is None:
        lost = [x for x in case["rows"] if x[4] == _orig(group)]
    else:
        lost = _stored(group, [int(k.split("#")[1]) for k in old_replaced], [])
    expected_before = list(case["rows"])
    for x in lost:
        expected_before.remove(x)
    assert lost and before == sorted(expected_before)
    links0, decs0 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")
    stamps0 = _count(db, "security_change", sid)

    plan = repo.relink_plan(db, index=_index(group))
    db.rollback()
    (item,) = plan.items
    assert (item.accession, item.rule) == (_amend(group), RELINK_HELD)
    assert plan.drift == [] and plan.operator_held == []
    new_disp, new_replaced = case["new"]
    assert item.new.disposition == new_disp
    assert sorted(item.new.replaced_rows or ()) == new_replaced
    res = repo.relink(db, plan)
    db.commit()
    # a new link version, the system's revoke of its old decision, the system's decision against the held rows
    assert (res.links, res.revokes, res.decisions) == (1, 1, 1)
    assert _count(db, "insider_amendment_link") == links0 + 1
    assert _count(db, "insider_amendment_decision") == decs0 + 2
    assert _count(db, "security_change", sid) > stamps0  # the company's change stamp moved
    assert sorted(_standing_replaced(db, sid)) == new_replaced
    assert _link(db, sid)["detail"]["relink"]["rule"] == RELINK_HELD
    # the read now returns every real row and each trade once
    assert _both(db, sid, case["asof"]) == sorted(case["rows"])

    # idempotent: a second plan finds nothing and a second apply writes nothing
    links1, decs1 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")
    again = repo.relink_plan(db, index=_index(group))
    db.rollback()
    assert again.items == [] and again.drift == []
    res2 = repo.relink(db, again)
    db.commit()
    assert (res2.links, res2.revokes, res2.decisions) == (0, 0, 0)
    assert _count(db, "insider_amendment_link") == links1
    assert _count(db, "insider_amendment_decision") == decs1


@pytest.mark.parametrize("group", sorted(RIGHT))
def test_a_right_link_plans_no_relink(db, group):
    sid = _seed_as_prod(db, group)
    link = _link(db, sid)
    assert link["disposition"] == "correction" and _standing_replaced(db, sid) is None
    assert _both(db, sid, RIGHT[group]["asof"]) == sorted(RIGHT[group]["rows"])
    plan = repo.relink_plan(db, index=_index(group))
    db.rollback()
    assert plan.items == [] and plan.drift == [] and plan.considered == 1


def test_an_amendment_the_operator_decided_is_never_relinked(db):
    group = "impossible_year_first_of_three"
    sid = _seed_as_prod(db, group)
    repo.decide(db, sid, _amend(group), "keep", reason="mine", basis="operator:keep")
    db.commit()
    links0, decs0 = _count(db, "insider_amendment_link"), _count(db, "insider_amendment_decision")
    plan = repo.relink_plan(db, index=_index(group))
    db.rollback()
    assert plan.items == [] and [i.accession for i in plan.operator_held] == [_amend(group)]
    repo.relink(db, plan)
    db.commit()
    assert _count(db, "insider_amendment_link") == links0
    assert _count(db, "insider_amendment_decision") == decs0


def test_the_dry_run_prints_old_and_new_rows_for_each_case(db, capsys):
    """The CLI's report over all eight prod-shaped cases: the five wrong ones planned with their old and new
    replaced rows, the three right ones not listed."""
    sids = {g: _seed_as_prod(db, g) for g in sorted(ALL)}
    by_sid = {sid: g for g, sid in sids.items()}
    plan = repo.relink_plan(db, index=lambda sid, _cik: _index(by_sid[sid])(sid, _cik))
    db.rollback()
    RL.report(plan)
    out = capsys.readouterr().out
    print(out)  # the dry run's own words, for the report (pytest -s)
    assert (
        f"re-link {len(WRONG)} (0 made public after the amendment, 0 row tie-break, 5 held rows)"
        in out
    )
    assert sorted(i.accession for i in plan.items) == sorted(_amend(g) for g in WRONG)
    for g in RIGHT:
        assert _amend(g) not in out
    assert "replaces whole  ==>  partial" in out  # a whole correction becomes a matched replacement


# --- the pure view -------------------------------------------------------------------------------------------


def test_the_view_counts_a_stored_row_that_has_a_hold_once_and_ignores_a_4a_hold():
    """A DIRECT-INPUT contract (no real instance needed): a stored row and a hold of the same accession#seq are
    ONE row (the held one); a Form 4/A hold is not a row of an original; a filing with no hold is unchanged.
    """
    stored = {
        "A": [
            {"accession": "A", "txn_seq": 0, "valid_from": date(2020, 1, 2), "txn_code": "P"},
            {"accession": "A", "txn_seq": 1, "valid_from": date(2021, 1, 2), "txn_code": "S"},
        ],
        "B": [{"accession": "B", "txn_seq": 0, "valid_from": date(2020, 1, 3), "txn_code": "P"}],
    }
    holds = [
        {
            "accession": "A",
            "txn_seq": 1,
            "form": "4",
            "row": {
                "txn_code": "S",
                "shares": 5.0,
                "price": 1.0,
                "txn_date_as_filed": "2021-01-02",
            },
            "accepted": None,
        },
        {"accession": "C", "txn_seq": 0, "form": "4/A", "row": {"txn_code": "P"}, "accepted": None},
    ]
    view = with_held_rows(stored, holds)
    assert [r.get("held", False) for r in view["A"]] == [False, True]
    assert view["A"][1]["valid_from"] is None and view["A"][1]["txn_date_as_filed"] == "2021-01-02"
    assert view["B"] == stored["B"] and "C" not in view
