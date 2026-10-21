"""The joint-report rule's CONTRACT on direct inputs (``domain/insider_joint_reports.py``) — each rule of the
operator's 2026-10-21 decision stated once, on the smallest rows that exercise it. The real filings behind the rule
are pinned in ``tests/ingest/test_form4_joint_reports.py``."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from domain.insider_joint_reports import (
    JOINT_ACCESSIONS,
    JOINT_REPORTERS,
    JOINT_ROLES,
    collapse_joint_reports,
    display_name,
    joint_accessions,
    joint_label,
    joint_labels,
    roles_of,
)

D0 = date(2026, 5, 21)


def _row(
    acc: str,
    owner: str,
    *,
    seq: int = 0,
    di: str | None = "I",
    after: float | None = 5000.0,
    shares: float = 1000.0,
    price: float | None = 10.0,
    code: str = "S",
    d: date = D0,
    accepted: datetime | None = None,
    role: str | None = "Director",
) -> dict:
    return {
        "accession": acc,
        "txn_seq": seq,
        "insider_name": owner,
        "rpt_owner_cik": None,
        "insider_role": role,
        "txn_code": code,
        "valid_from": d,
        "shares": shares,
        "price": price,
        "usd": shares * (price or 0.0),
        "direct_indirect": di,
        "shares_owned_after": after,
        "accepted": accepted or datetime(2026, 5, 22, tzinfo=timezone.utc),
        "recorded_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }


def test_a_trade_two_filers_reported_counts_once():
    rows = [
        _row("acc-fund", "FUND LP", accepted=datetime(2026, 5, 22, 1, tzinfo=timezone.utc)),
        _row("acc-partner", "PARTNER", accepted=datetime(2026, 5, 22, 2, tzinfo=timezone.utc)),
    ]
    out = collapse_joint_reports(rows)
    assert len(out) == 1
    (s,) = out
    assert s["insider_name"] == "FUND LP"  # the first to report it (both indirect)
    assert s[JOINT_REPORTERS] == ("FUND LP", "PARTNER")
    assert s[JOINT_ACCESSIONS] == ("acc-fund", "acc-partner")
    assert joint_label(s) == "FUND LP / PARTNER (joint report)"
    assert display_name(s) == "FUND LP / PARTNER (joint report)"


def test_nothing_to_collapse_returns_the_same_list_unchanged():
    rows = [_row("a", "X"), _row("b", "Y", after=7000.0)]  # different holdings: two positions
    assert collapse_joint_reports(rows) is rows
    assert collapse_joint_reports([]) == []


def test_a_row_without_evidence_never_merges():
    """SAFE ON MISSING EVIDENCE: either field NULL (a row stored before 0054, not yet repaired) — never grouped."""
    for bare in (dict(di=None), dict(after=None), dict(di="?")):
        rows = [_row("a", "X"), _row("b", "Y", **bare)]
        assert collapse_joint_reports(rows) is rows, bare


def test_two_direct_holders_are_never_merged():
    """ALAB's co-founders: identical sale, identical post-trade holding, each his OWN shares."""
    rows = [_row("a", "FOUNDER A", di="D"), _row("b", "FOUNDER B", di="D")]
    assert collapse_joint_reports(rows) is rows


def test_one_filing_never_collapses_against_itself():
    """Two identical rows of ONE filing are two lots (several funds selling the same lot size), never a joint
    report — the rule is across filings only."""
    rows = [_row("a", "X", seq=0), _row("a", "X", seq=1)]
    assert collapse_joint_reports(rows) is rows


def test_the_direct_holder_survives_a_spouse_report():
    rows = [
        _row("acc-husband", "HUSBAND", di="I", role="President and CEO"),
        _row("acc-wife", "WIFE", di="D", role="Director, 10% owner"),
    ]
    (s,) = collapse_joint_reports(rows)
    assert s["insider_name"] == "WIFE" and s["direct_indirect"] == "D"
    assert s[JOINT_REPORTERS] == ("WIFE", "HUSBAND")
    # counted once, never demoted: the co-reporter's role rides for the seniority test
    assert set(roles_of(s)) == {"Director, 10% owner", "President and CEO"}
    assert s[JOINT_ROLES] == ("Director, 10% owner", "President and CEO")


def test_a_sold_out_row_merges_only_between_linked_filings():
    """A post-trade holding of 0 (or == shares) identifies nothing on its own: it collapses only when a
    fingerprinted row has already linked the SAME two filings."""
    sold_out = [_row("a", "X", after=0.0, seq=1), _row("b", "Y", after=0.0, seq=1)]
    from_zero = [_row("a", "X", after=1000.0, seq=2), _row("b", "Y", after=1000.0, seq=2)]
    assert collapse_joint_reports(sold_out) is sold_out
    assert collapse_joint_reports(from_zero) is from_zero
    fingerprint = [_row("a", "X", after=4784711.0, seq=0), _row("b", "Y", after=4784711.0, seq=0)]
    out = collapse_joint_reports(fingerprint + sold_out + from_zero)
    assert [(r["accession"], r["txn_seq"]) for r in out] == [("a", 0), ("a", 1), ("a", 2)]
    # a THIRD filing the fingerprint never touched stays out of the link
    other = [
        _row("a", "X", after=0.0, seq=3, d=date(2026, 5, 22)),
        _row("c", "Z", after=0.0, d=date(2026, 5, 22)),
    ]
    out = collapse_joint_reports(fingerprint + other)
    assert ("c", 0) in {(r["accession"], r["txn_seq"]) for r in out}


def test_every_group_of_one_joint_report_keeps_the_same_filer():
    """The party is counted once: the all-indirect survivor is chosen per OWNER-SET (whoever reported that set's
    trades first), not per group — so a later group where the other filer happened to file first still keeps X.
    """
    early = datetime(2026, 5, 22, 1, tzinfo=timezone.utc)
    late = datetime(2026, 5, 22, 3, tzinfo=timezone.utc)
    rows = [
        _row("a1", "X", after=100.0, accepted=early),
        _row("b1", "Y", after=100.0, accepted=late),
        _row("b2", "Y", after=200.0, shares=500.0, accepted=early.replace(hour=2)),
        _row("a2", "X", after=200.0, shares=500.0, accepted=late.replace(hour=4)),
    ]
    out = collapse_joint_reports(rows)
    assert {r["insider_name"] for r in out} == {"X"}
    assert len(out) == 2


def test_decimal_and_float_inputs_collapse_alike():
    """Postgres hands Decimal, the DuckDB mirror float — the rule compares rounded floats (PIT parity)."""
    a = _row("a", "X")
    b = {
        **_row("b", "Y"),
        "shares": Decimal("1000"),
        "price": Decimal("10.0000"),
        "shares_owned_after": Decimal("5000"),
    }
    assert len(collapse_joint_reports([a, b])) == 1


def test_the_inputs_are_never_mutated_and_order_is_kept():
    rows = [_row("z", "Q", after=9.0, shares=3.0), _row("a", "X"), _row("b", "Y")]
    snapshot = [dict(r) for r in rows]
    out = collapse_joint_reports(rows)
    assert rows == snapshot
    assert [r["accession"] for r in out] == ["z", "a"]
    assert JOINT_REPORTERS not in rows[1]


def test_the_cluster_helpers_name_and_cite_every_filing():
    rows = collapse_joint_reports(
        [_row("a", "X"), _row("b", "Y"), _row("c", "Z", after=1.0, shares=2.0)]
    )
    assert joint_labels(rows) == ["X / Y (joint report)"]
    assert joint_accessions(rows) == {"a", "b", "c"}  # the co-reporter's filing stays cited (#6)
    plain = _row("c", "Z")
    assert joint_label(plain) is None and display_name(plain) == "Z"
    # two spouses each surviving for their own sale: ONE label for the pair, whichever filer comes first
    spouses = collapse_joint_reports(
        [
            _row("h", "HUSBAND", di="D", after=10.0, shares=1.0),
            _row("w", "WIFE", di="I", after=10.0, shares=1.0),
            _row("w", "WIFE", di="D", after=20.0, shares=1.0, seq=1),
            _row("h", "HUSBAND", di="I", after=20.0, shares=1.0, seq=1),
        ]
    )
    assert len(spouses) == 2
    assert joint_labels(spouses) == ["HUSBAND / WIFE (joint report)"]
    assert roles_of({"insider_role": None}) == ()
