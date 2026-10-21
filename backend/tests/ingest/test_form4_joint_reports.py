"""JOINT REPORTS on REAL filings — the verification table of the joint-report rule (operator-approved 2026-10-21;
``domain/insider_joint_reports.py``). Fixtures + provenance: ``tests/joint_report_fixtures.py``.

Each case stores the real filings exactly the way the nightly legs do (``ingest_form4`` / ``ingest_form4a`` — the
4/A linked and resolved by the system) and reads them back through the ONE as-of seam every reader uses, then
through the readers themselves: the two insider detectors, the Cockpit's insider-flow display member and the
Scoreboard ledger overlays. "Before" is the same read with the collapse switched off — the double count the
system served until now — so every case shows the defect AND the fix on the same real rows.

  INFQ  Maverick + Singer collapse; the 4/A pair -000507 / -000508 counts once ($95.2M -> $47.6M); the seller
        count drops (2 -> 1) — on these filings alone the sell cluster no longer qualifies
  AMKR  five filers -> one party: $19.0M not $95.0M (2025-02-24), $9.65M not $48.2M (2025-08-01); CORE kept
  SMCI  Liu (direct) survives her husband's "By Spouse" report; his CEO role keeps the sale senior
  OKLO  each spouse's own sale survives the other's report: two sellers, each sale once
  ALAB  the co-founders' identical sales stay two (both direct)
  TSM   two VPs' ESPP-trust buys stay two (different holdings)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

import pytest

import domain.insider_amendments as insider_amendments
from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade
from domain.insider_joint_reports import JOINT_REPORTERS
from ingest.edgar.form4 import ingest_form4, parse_form4
from scoreboard.overlays import episode_insider_buys, episode_insider_sells
from signals import insider_conviction, insider_sell
from signals.base import PointInTimeData
from signals.display import insider_flow
from tests.joint_report_fixtures import FILINGS, make_security, seed, xml

FAR = datetime(
    2100, 1, 1, tzinfo=timezone.utc
)  # "everything recorded so far" — the valid-time axis only
MAVERICK, SINGER = "MAVERICK CAPITAL LTD", "SINGER DAVID B"


@pytest.fixture
def uncollapsed(monkeypatch):
    """The read with the collapse switched OFF — what the system served before the fix (the double count)."""

    def _off():
        monkeypatch.setattr(insider_amendments, "collapse_joint_reports", lambda rows: rows)

    return _off


def _read(db, sid, asof: date) -> list[dict]:
    return PointInTimeData(db, asof=asof, known_at=FAR).insider_txns(sid)


def _usd(rows) -> float:
    return sum(float(r["usd"] or 0) for r in rows)


def _owners(xml_text: str) -> list[str]:
    return [
        o.findtext("reportingOwnerId/rptOwnerName")
        for o in ET.fromstring(xml_text).findall("reportingOwner")
    ]


# --- the parser: one row per transaction, and the evidence -----------------------------------------------


def test_a_joint_filing_stores_each_transaction_once(db):
    """Three reporting owners, seventeen transactions, seventeen rows, ONE insider — the within-filing double
    count cannot arise (COUNT THE TABLE). A future change that records a joint filing's co-owners must not fan
    the rows out per owner."""
    doc = xml("0001012975-26-000481")
    assert _owners(doc) == [MAVERICK, "MAVERICK CAPITAL MANAGEMENT LLC", "AINSLIE LEE S III"]
    rows = parse_form4(doc)
    assert len(rows) == len(
        ET.fromstring(doc).findall("nonDerivativeTable/nonDerivativeTransaction")
    )
    assert len(rows) == 17
    assert {(r["insider_name"], r["rpt_owner_cik"]) for r in rows} == {(MAVERICK, "934639")}
    sid = make_security(db, "INFQ")
    assert ingest_form4(db, sid, doc, "0001012975-26-000481", member_cik="0002007825") == (17, 0)
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n, count(DISTINCT insider_name) AS owners, "
            "count(*) FILTER (WHERE direct_indirect = 'I' AND shares_owned_after IS NOT NULL) AS evidenced "
            "FROM fact_insider_txn"
        )
        assert cur.fetchone() == {"n": 17, "owners": 1, "evidenced": 17}


def test_the_parser_captures_the_ownership_evidence_verbatim():
    """``direct_indirect`` + ``shares_owned_after`` as the documents state them: Sara Liu's sale is her own (D),
    her husband reports the SAME sale through her (I) — the same 337,506 shares left afterwards."""
    (liu,) = parse_form4(xml("0001392941-26-000015"))
    (liang,) = parse_form4(xml("0001392942-26-000014"))
    assert (liu["direct_indirect"], liu["shares_owned_after"]) == ("D", 337506.0)
    assert (liang["direct_indirect"], liang["shares_owned_after"]) == ("I", 337506.0)
    assert "By Spouse" in xml("0001392942-26-000014")
    (vp1,) = parse_form4(xml("0001046179-26-000692"))
    (vp2,) = parse_form4(xml("0001046179-26-000714"))
    assert (vp1["shares_owned_after"], vp2["shares_owned_after"]) == (338.0, 199.0)


# --- INFQ — the reported symptom --------------------------------------------------------------------------


def test_infq_maverick_and_singer_count_once(db, uncollapsed):
    sid = seed(db, "INFQ")  # -000481 / -000486 / -000492 / -000493 + the 4/As -000507 / -000508
    asof = date(2026, 6, 5)
    after = _read(db, sid, asof)
    uncollapsed()
    before = _read(db, sid, asof)

    def sales(rows, d=None):
        return [r for r in rows if r["txn_code"] == "S" and (d is None or r["valid_from"] == d)]

    # BEFORE — every one of Singer's sales stored beside Maverick's; the 4/A pair adds 05-27 twice
    assert {r["insider_name"] for r in sales(before)} == {MAVERICK, SINGER}
    assert len(sales(before)) == 17 + 12 + 4 + 4
    may27 = date(2026, 5, 27)
    assert round(_usd(sales(before, may27))) == 95_204_806  # "$95.2M across 2 insiders"
    # AFTER — one party, each sale once; the 4/A pair counts once
    assert {r["insider_name"] for r in sales(after)} == {MAVERICK}
    assert len(sales(after)) == 17 + 4
    assert round(_usd(sales(after, may27))) == 47_602_403
    # what leaves the count: Singer's twelve 05-21/22 sales ($181.6M) + his copy of the 4/A sales ($47.6M)
    assert _usd(sales(before)) - _usd(sales(after)) == pytest.approx(181_595_286.54 + 47_602_403.14)
    survivors = [r for r in sales(after) if r.get(JOINT_REPORTERS)]
    assert len(survivors) == 12 + 4  # Singer's twelve + the four 4/A sales
    assert {r[JOINT_REPORTERS] for r in survivors} == {(MAVERICK, SINGER)}
    # Maverick's five sales Singer never reported stand alone, uncollapsed
    assert sum(1 for r in sales(after) if not r.get(JOINT_REPORTERS)) == 5

    # the SELL detector: before, "2 insiders" — after, one seller is not a cluster (no fire on these filings)
    fired = insider_sell.score(before, sid, asof, DEFAULT_CONFIG)
    assert fired is not None and fired.label.startswith("2 insiders incl. senior officer sold")
    assert insider_sell.score(after, sid, asof, DEFAULT_CONFIG) is None


def test_infq_every_reader_sees_it_once(db):
    """The readers past the detectors: the Cockpit's insider-flow member and the Scoreboard sell ledger."""
    sid = seed(db, "INFQ")
    asof = date(2026, 6, 5)
    pit = PointInTimeData(db, asof=asof, known_at=FAR)
    flow = insider_flow.display(pit, sid, asof)
    metrics = {m.key: m.value for m in flow.metrics}
    assert metrics["sell_count"] == 17 + 4
    assert "counted once: MAVERICK CAPITAL LTD / SINGER DAVID B (joint report)" in flow.basis.note
    ledger = episode_insider_sells(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        start=date(2026, 5, 1),
        end=asof,
        asof=asof,
        known_at=FAR,
        day_lows={},
    )
    assert len(ledger) == 17 + 4
    assert {s["insider_name"] for s in ledger} == {
        MAVERICK,
        "MAVERICK CAPITAL LTD / SINGER DAVID B (joint report)",
    }


# --- AMKR — five filers, one purchase -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asof", "d", "usd"),
    [
        (date(2025, 3, 3), date(2025, 2, 24), 18_999_995.25),  # 869,565 sh @ $21.85
        (date(2025, 8, 8), date(2025, 8, 1), 9_648_719.65),  # 441,589 sh @ $21.85
    ],
)
def test_amkr_five_filers_are_one_party(db, uncollapsed, asof, d, usd):
    sid = seed(db, "AMKR")
    after = [r for r in _read(db, sid, asof) if r["txn_code"] == "P" and r["valid_from"] == d]
    uncollapsed()
    before = [r for r in _read(db, sid, asof) if r["txn_code"] == "P" and r["valid_from"] == d]
    assert len(before) == 5 and _usd(before) == pytest.approx(5 * usd)
    (one,) = after
    assert float(one["usd"]) == pytest.approx(usd)
    assert one["insider_name"] == "SUJODA Investments, LP"  # the DIRECT holder survives
    assert set(one[JOINT_REPORTERS]) == {
        "SUJODA Investments, LP",
        "Sujoda Management, LLC",
        "KIM SUSAN Y",
        "KIM DAVID D",
        "KIM JOHN T",
    }

    buy_before = insider_conviction.score(before, sid, asof, DEFAULT_CONFIG)
    buy_after = insider_conviction.score(after, sid, asof, DEFAULT_CONFIG)
    assert buy_before.label.startswith("5 insiders incl. senior officer bought")
    assert buy_after.label.startswith(f"1 insider incl. senior officer bought ${usd:,.0f}")
    assert "(joint report)" in buy_after.label
    # counted once, never demoted: a director co-reported it, and one $19M / $9.6M senior buy is still CORE
    assert buy_before.grade == buy_after.grade == Grade.CORE
    # every filer's filing stays cited (the work shown, #6)
    assert {p.ref for p in buy_after.provenance} == {p.ref for p in buy_before.provenance}
    assert len({p.ref for p in buy_after.provenance}) == 5


def test_amkr_the_scoreboard_buy_ledger_shows_one_buy(db):
    sid = seed(db, "AMKR")
    buys = episode_insider_buys(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        start=date(2025, 2, 1),
        end=date(2025, 3, 3),
        asof=date(2025, 3, 3),
        known_at=FAR,
        day_lows={},
    )
    assert len(buys) == 1
    assert buys[0]["insider_name"].endswith("(joint report)")


# --- SMCI / OKLO — spouses -----------------------------------------------------------------------------------


def test_smci_the_wife_survives_her_husbands_by_spouse_report(db, uncollapsed):
    sid = seed(db, "SMCI")
    asof = date(2026, 10, 9)
    after = [r for r in _read(db, sid, asof) if r["valid_from"] == date(2026, 10, 7)]
    uncollapsed()
    before = [r for r in _read(db, sid, asof) if r["valid_from"] == date(2026, 10, 7)]
    assert {r["insider_name"] for r in before} == {"Liu Liang Chiu-Chu Sara", "Liang Charles"}
    (one,) = after
    assert (one["insider_name"], one["direct_indirect"]) == ("Liu Liang Chiu-Chu Sara", "D")
    assert one[JOINT_REPORTERS] == ("Liu Liang Chiu-Chu Sara", "Liang Charles")
    assert "President and CEO" in one["joint_roles"]  # his role rides: the sale stays senior


def test_oklo_each_spouses_own_sale_survives(db, uncollapsed):
    sid = seed(db, "OKLO")
    asof = date(2026, 10, 2)
    after = [r for r in _read(db, sid, asof) if r["valid_from"] == date(2026, 10, 1)]
    uncollapsed()
    before = [r for r in _read(db, sid, asof) if r["valid_from"] == date(2026, 10, 1)]
    assert len(before) == 8 and len(after) == 4  # four sales, each stored twice
    assert _usd(after) == pytest.approx(_usd(before) / 2)
    assert all(r.get(JOINT_REPORTERS) for r in after)
    # each spouse's OWN sale survives as the holder's own report (direct) — two sellers, not one
    own = {(r["insider_name"], float(r["price"])) for r in after if r["direct_indirect"] == "D"}
    assert own == {("DeWitte Jacob", 36.19), ("Cochran Caroline", 36.64)}
    # the two GRAT sales both spouses report as INDIRECT keep the first filer (DeWitte, 00:01:57 vs 00:02:15)
    # and name both — an entity's sale, counted once under one consistent reporter
    grat = [r for r in after if r["direct_indirect"] == "I"]
    assert {r["insider_name"] for r in grat} == {"DeWitte Jacob"} and len(grat) == 2
    assert {r["insider_name"] for r in after} == {"DeWitte Jacob", "Cochran Caroline"}


# --- the negatives -------------------------------------------------------------------------------------------


def test_alab_two_direct_holders_stay_two_trades(db):
    """Separate filings by different insiders, same size, same day, same price — and even the same post-trade
    holding: each sold his OWN vested shares (both direct). Two trades, two sellers, both counted.
    """
    sid = seed(db, "ALAB")
    rows = _read(db, sid, date(2025, 8, 18))
    assert len(rows) == 4
    assert {r["insider_name"] for r in rows} == {"Mohan Jitendra", "Gajendra Sanjay"}
    assert {r["direct_indirect"] for r in rows} == {"D"}
    assert not any(r.get(JOINT_REPORTERS) for r in rows)
    assert "sell to cover" in xml("0001736297-25-000115")
    each = sum(r["usd"] for r in parse_form4(xml("0001736297-25-000115")))
    assert _usd(rows) == pytest.approx(2 * each)


def test_tsm_espp_trust_buys_with_different_holdings_stay_two(db):
    sid = seed(db, "TSM")
    rows = [r for r in _read(db, sid, date(2026, 10, 8)) if r["txn_code"] == "P"]
    assert len(rows) == 2
    assert {r["insider_name"] for r in rows} == {"Hwang Yuan-Ko", "Wu Yi-Huang"}
    assert not any(r.get(JOINT_REPORTERS) for r in rows)


def test_the_as_of_read_and_the_basket_read_agree(db):
    """The two Postgres read shapes (per-security ``as_of``, basket ``as_of_many`` via the PIT prefetch) run the
    SAME collapse."""
    sids = [seed(db, t) for t in ("INFQ", "AMKR", "OKLO")]
    asof = date(2026, 10, 9)
    pit = PointInTimeData(db, asof=asof, known_at=FAR, basket=sids)
    for sid in sids:
        scoped = as_of(
            db,
            "fact_insider_txn",
            security_id=sid,
            asof=asof,
            known_at=FAR,
            tenant_id=DEFAULT_TENANT_ID,
        )
        assert sorted((r["accession"], r["txn_seq"]) for r in pit.insider_txns(sid)) == sorted(
            (r["accession"], r["txn_seq"]) for r in scoped
        )
    assert {f.ticker for f in FILINGS.values()} >= {"INFQ", "AMKR", "OKLO"}
