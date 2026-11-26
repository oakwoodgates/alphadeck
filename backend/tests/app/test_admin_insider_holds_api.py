"""Bad dates step 3 — the steward's queue for HELD insider rows (``GET/POST /admin/insider-holds``), on REAL
filings (``tests/held_fixtures.py``; ``tests/fixtures/edgar/held/``): every hold in ONE bucket judged by the read's
own ladder, the suggested date, every decision with its Undo, the conflicts (404 / 409 / 422), steward-only
access, and the effect of a decision on what the insider read returns (on the PUBLIC clock: from when the
steward decided, never earlier).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg.types.json import Json

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.insider_holds import BUCKET_WORDS
from domain.market_time import known_at_for_asof
from ingest.edgar.form4 import ingest_form4
from signals.base import PointInTimeData
from tests import held_fixtures as H
from tests.app.test_auth_gate import _signed_in, anon  # noqa: F401 — the fixture is used below
from tests.auth_fixtures import TEST_STEWARD

BASE = "/admin/insider-holds"
BUCKETS = ("owed", "placed_by_rule", "filer_corrected", "decided", "dismissed", "amendment_row")
LATE = "0000899243-21-012557"
ON_TIME_A = "0001354488-15-000004"
IMPOSSIBLE = "0001140361-24-027755"
AMENDED = "0001179110-20-004028"
AMENDED_4A = "0001179110-20-004036"
AMENDED_IY = "0001628280-23-039558"
AMENDED_IY_4A = "0001628280-23-040161"
_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
# a real Form 4 with a leading-zero year (``tests/pipeline/test_insider_hold_scan.py``), ingested with NO
# filing date and NO acceptance: the hold has no suggestion and no ceiling a ratified date can be checked on
_BBAI = ("BBAI", "0001836981", "0001628280-24-010030", "form4_bbai_leading_zero_year.xml")


def _items(client) -> dict[tuple[str, int], dict]:
    r = client.get(BASE)
    assert r.status_code == 200, r.text
    body = r.json()
    out = {}
    for b in BUCKETS:
        assert body["counts"][b] == len(body[b])
        for it in body[b]:
            assert it["bucket"] == b
            out[(it["accession"], it["txn_seq"])] = it
    assert body["total"] == len(out)
    return out


# the signed-in steward of every app test (``tests.auth_fixtures.TEST_STEWARD``) as a decision records it
STEWARD = {"user_id": str(TEST_STEWARD.id), "display_name": TEST_STEWARD.display_name}


def _detail(db, decision_id) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT detail FROM insider_txn_hold_decision WHERE id = %s", (decision_id,))
        return cur.fetchone()["detail"]


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_txn_hold_decision")
        return cur.fetchone()["n"]


def _read(db, sid, asof: date, known_at: datetime, clock: str) -> list[tuple]:
    rows = PointInTimeData(
        db, asof=asof, known_at=known_at, clock=clock, workspace=DEFAULT_TENANT_ID
    ).insider_txns(sid)
    return sorted((r["valid_from"], float(r["shares"]), r.get("date_basis")) for r in rows)


def _bbai(db, *, filed: date | None = None) -> uuid.UUID:
    ticker, cik, accession, fname = _BBAI
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2020-01-01"),
        )
    ingest_form4(
        db,
        sid,
        (_FIX / fname).read_text(encoding="utf-8"),
        accession,
        member_cik=cik,
        filed=filed,
    )
    db.commit()
    return sid


# --- the buckets -------------------------------------------------------------------------------------------


def test_each_real_filing_sits_in_the_bucket_the_read_gives_it(client, db):
    for g in (
        "on_time_a",
        "late",
        "impossible_year",
        "amended_after_filing",
        "amended_impossible_year",
    ):
        H.seed(db, g)
    items = _items(client)

    # an on-time filing: placed by the rule, counted from its filing night
    for seq in (0, 1, 2):
        it = items[(ON_TIME_A, seq)]
        assert it["bucket"] == "placed_by_rule" and it["filing_night"] == "2015-01-02"
        assert it["date_as_filed"] == "2015-12-31" and it["decision"] is None
        assert it["market_date"] == "2015-01-02" and it["owed_why"] is None  # owed rows only
    # a late filing: owed, with a suggestion that agrees with the period of report
    late = items[(LATE, 0)]
    assert late["bucket"] == "owed" and late["filing_night"] is None
    assert (late["date_as_filed"], late["period_of_report_as_filed"]) == (
        "2021-11-16",
        "2020-11-16",
    )
    assert (late["suggested_date"], late["agrees"]) == ("2020-11-16", True)
    assert late["reason"] == "after_filing" and "after SEC accepted" in late["reason_words"]
    assert late["row"]["txn_code"] == "P" and late["row"]["shares"] == 800.0
    assert late["filing_url"].endswith("/1425450/000089924321012557/")
    assert late["ticker"] == H.TICKERS["late"] and late["company_name"] == "late (fixture)"
    # the filing's market date (the latest a ratified date may be) and why the rule did not place it
    assert late["market_date"] == "2021-03-18"
    assert late["owed_why"] == (
        "the filing is late: SEC accepted it 122 days after its period of report (2020-11-16, the earliest "
        f"trade it reports), past the {DEFAULT_CONFIG.insider_hold_on_time_days}-day on-time window, so its "
        "filing night is no stand-in for this row's date"
    )
    assert (late["suggestion_basis"], late["copies"]) == ("period_of_report", [])
    # the impossible year: on time -> placed; its suggestion is the written day in the filing's year
    iy = items[(IMPOSSIBLE, 0)]
    assert iy["bucket"] == "placed_by_rule" and iy["filing_night"] == "2024-05-28"
    assert (iy["date_as_filed"], iy["suggested_date"], iy["agrees"]) == (
        "2028-05-24",
        "2024-05-24",
        True,
    )
    assert iy["reason"] == "year_after_filing_year"
    # the filer's own 4/A carries the same trade: no action, with why
    for acc, amend in ((AMENDED, AMENDED_4A), (AMENDED_IY, AMENDED_IY_4A)):
        (fc,) = [v for (a, _), v in items.items() if a == acc]
        assert fc["bucket"] == "filer_corrected" and amend in fc["why"]
        assert fc["owed_why"] is None
    # owed first in the panel
    body = client.get(BASE).json()
    assert [it["accession"] for it in body["owed"]] == [LATE, LATE, LATE]
    # each bucket in the backend's own words (the panel copies none)
    assert body["bucket_words"] == {b: BUCKET_WORDS[b] for b in BUCKETS}


def test_a_held_amendment_row_is_listed_with_no_action(client, db):
    """No held Form 4/A row exists in the store or in a real fixture (the spec: "there are none in the store
    today"), so this is a DIRECT-INPUT contract: a 4/A hold row inserted by hand, never presented as a real
    filing. It lists as ``amendment_row`` and every decision on it is a 409."""
    sid = H.seed(db, "late")
    with db.cursor() as cur:
        cur.execute(
            'INSERT INTO insider_txn_hold (tenant_id, security_id, accession, txn_seq, form, reason, "row", '
            "filed) VALUES (%s, %s, %s, 0, '4/A', 'after_filing', %s, %s) RETURNING id",
            (
                DEFAULT_TENANT_ID,
                sid,
                "0000000000-21-000001",
                Json({"txn_code": "P", "shares": 1, "txn_date_as_filed": "2021-11-16"}),
                date(2021, 3, 19),
            ),
        )
        hid = cur.fetchone()["id"]
    db.commit()
    it = _items(client)[("0000000000-21-000001", 0)]
    assert it["bucket"] == "amendment_row" and it["form"] == "4/A"
    before = _count(db)
    assert client.post(f"{BASE}/{hid}/approve").status_code == 409
    assert client.post(f"{BASE}/{hid}/dismiss", json={"reason": "x"}).status_code == 409
    assert _count(db) == before


# --- approve / dismiss / revoke ----------------------------------------------------------------------------


def test_approve_counts_the_row_from_the_decision_on_and_revoke_restores_it(client, db):
    sid = H.seed(db, "late")
    hid = _items(client)[(LATE, 0)]["hold_id"]
    asof = date(2022, 6, 1)
    for clock in ("public", "record"):
        assert _read(db, sid, asof, H.FAR, clock) == []
    before = _count(db)

    r = client.post(f"{BASE}/{hid}/approve", json={"reason": "matches the period of report"})
    assert r.status_code == 200, r.text
    it = r.json()
    assert it["bucket"] == "decided" and it["decision"]["action"] == "approve"
    # no date sent: the suggestion is ratified
    assert it["decision"]["ratified_date"] == "2020-11-16"
    assert it["decision"]["basis"] == "steward:approve"
    assert it["decision"]["reason"] == "matches the period of report"
    assert _count(db) == before + 1
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_txn_hold_decision WHERE id = %s", (it["decision"]["id"],)
        )
        row = cur.fetchone()
    assert row["recorded_at"] > datetime.now(timezone.utc) - timedelta(
        minutes=5
    )  # now, never back-dated
    assert row["detail"]["date_as_filed"] == "2021-11-16"
    assert row["detail"]["period_of_report_as_filed"] == "2020-11-16"
    assert row["detail"]["suggested_date"] == "2020-11-16" and row["detail"]["agrees"] is True
    assert row["detail"]["ratified_from"] == "suggestion" and row["detail"]["accepted"]
    # WHO decided: the signed-in steward (id + display name, never an email), stored and returned
    assert row["detail"]["decided_by"] == STEWARD
    assert "email" not in row["detail"]["decided_by"]
    assert it["decision"]["decided_by"] == STEWARD
    assert [h["decided_by"] for h in it["history"]] == [STEWARD]
    decided = row["recorded_at"]

    # on the PUBLIC clock the decision counts from when it was made: a read before it still waits
    for clock in ("public", "record"):
        assert _read(db, sid, asof, decided - timedelta(seconds=1), clock) == []
        assert _read(db, sid, asof, H.FAR, clock) == [(date(2020, 11, 16), 800.0, "ratified")]
    # a past night's history view is never rewritten by today's decision
    night = known_at_for_asof(date(2022, 6, 1), decided)
    assert _read(db, sid, asof, night, "public") == []

    # a second decision while one stands: 409, nothing written
    assert client.post(f"{BASE}/{hid}/approve").status_code == 409
    assert client.post(f"{BASE}/{hid}/dismiss", json={"reason": "no"}).status_code == 409
    assert _count(db) == before + 1

    # Undo
    r = client.post(f"{BASE}/decisions/{it['decision']['id']}/revoke")
    assert r.status_code == 200, r.text
    assert r.json()["bucket"] == "owed" and r.json()["decision"] is None
    assert _count(db) == before + 2
    # the Undo is labeled too: who undid it rides the row's history (the stored revoke and the returned one)
    undo = r.json()["history"][-1]
    assert undo["action"] == "revoke" and undo["revokes"] == it["decision"]["id"]
    assert undo["decided_by"] == STEWARD and undo["basis"] == "steward:revoke"
    assert _detail(db, undo["id"])["decided_by"] == STEWARD
    assert _read(db, sid, asof, H.FAR, "public") == []
    # undoing it again, or undoing the Undo: 409
    assert client.post(f"{BASE}/decisions/{it['decision']['id']}/revoke").status_code == 409
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM insider_txn_hold_decision WHERE action = 'revoke' AND revokes = %s",
            (it["decision"]["id"],),
        )
        rev = cur.fetchone()["id"]
    assert client.post(f"{BASE}/decisions/{rev}/revoke").status_code == 409
    assert _count(db) == before + 2


def test_a_steward_date_and_a_dismiss_on_a_placed_row_and_their_undo(client, db):
    sid = H.seed(db, "on_time_a")
    item = _items(client)[(ON_TIME_A, 0)]
    hid, shares = item["hold_id"], item["row"]["shares"]
    asof = date(2016, 1, 4)
    placed = [(date(2015, 1, 2), s, "filing_night") for s in (300.0, 500.0, 500.0)]
    assert _read(db, sid, asof, H.FAR, "public") == placed

    # the steward outranks the rule: a ratified date of their own
    r = client.post(f"{BASE}/{hid}/approve", json={"ratified_date": "2014-12-31"})
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["ratified_date"] == "2014-12-31"
    got = _read(db, sid, asof, H.FAR, "public")
    assert (date(2014, 12, 31), shares, "ratified") in got and len(got) == 3
    d1 = r.json()["decision"]["id"]
    assert client.post(f"{BASE}/decisions/{d1}/revoke").status_code == 200
    assert _read(db, sid, asof, H.FAR, "public") == placed

    # dismiss: not a trade
    r = client.post(f"{BASE}/{hid}/dismiss", json={"reason": "a zero-share notice"})
    assert r.status_code == 200, r.text
    assert r.json()["bucket"] == "dismissed" and r.json()["decision"]["basis"] == "steward:dismiss"
    assert r.json()["decision"]["decided_by"] == STEWARD
    assert _detail(db, r.json()["decision"]["id"])["decided_by"] == STEWARD
    assert len(_read(db, sid, asof, H.FAR, "public")) == 2
    d2 = r.json()["decision"]["id"]
    r = client.post(f"{BASE}/decisions/{d2}/revoke", json={"reason": "it was a trade"})
    assert r.status_code == 200 and r.json()["bucket"] == "placed_by_rule"
    undo = r.json()["history"][-1]
    assert undo["action"] == "revoke" and undo["decided_by"] == STEWARD
    assert _detail(db, undo["id"])["decided_by"] == STEWARD
    assert _read(db, sid, asof, H.FAR, "public") == placed


def test_the_filers_correction_takes_no_steward_decision(client, db):
    H.seed(db, "amended_impossible_year")
    (fc,) = [v for (a, _), v in _items(client).items() if a == AMENDED_IY]
    before = _count(db)
    assert client.post(f"{BASE}/{fc['hold_id']}/approve").status_code == 409
    assert client.post(f"{BASE}/{fc['hold_id']}/dismiss", json={"reason": "x"}).status_code == 409
    assert _count(db) == before


# --- 404 / 422 -------------------------------------------------------------------------------------------


def test_unknown_ids_are_404(client, db):
    H.seed(db, "late")
    before = _count(db)
    nobody = uuid.uuid4()
    assert client.post(f"{BASE}/{nobody}/approve").status_code == 404
    assert client.post(f"{BASE}/{nobody}/dismiss", json={"reason": "x"}).status_code == 404
    assert client.post(f"{BASE}/decisions/{nobody}/revoke").status_code == 404
    assert _count(db) == before


def test_a_date_the_rule_refuses_is_422(client, db):
    H.seed(db, "late")
    hid = _items(client)[(LATE, 1)]["hold_id"]
    before = _count(db)
    for bad in (
        "2021-03-19",
        "2021-11-16",
        "1933-12-31",
    ):  # after the market date, the written one, pre-1934
        r = client.post(f"{BASE}/{hid}/approve", json={"ratified_date": bad})
        assert r.status_code == 422, (bad, r.text)
    # the market date itself is allowed (on or before)
    assert (
        client.post(f"{BASE}/{hid}/approve", json={"ratified_date": "2021-03-18"}).status_code
        == 200
    )
    assert _count(db) == before + 1


def _one_row_hold(db) -> uuid.UUID:
    """A DIRECT-INPUT hold (never presented as a real filing): a buy written 2015-09-14 on a filing accepted
    2015-09-11 with period of report 2015-08-27, and no other row stored or held: a ONE-ROW filing, whose
    suggestion is its period of report since rule W3-1 (the arithmetic's 2014-09-14 is before it).
    """
    sid = H.make_security(db, "late")
    with db.cursor() as cur:
        cur.execute(
            'INSERT INTO insider_txn_hold (tenant_id, security_id, accession, txn_seq, form, reason, "row", '
            "filed, accepted, period_of_report_as_filed) "
            "VALUES (%s, %s, %s, 0, '4', 'after_filing', %s, %s, %s, %s) RETURNING id",
            (
                DEFAULT_TENANT_ID,
                sid,
                "0000000000-15-000001",
                Json({"txn_code": "P", "shares": 10, "txn_date_as_filed": "2015-09-14"}),
                date(2015, 9, 11),
                datetime(2015, 9, 11, 20, 0, tzinfo=timezone.utc),
                "2015-08-27",
            ),
        )
        hid = cur.fetchone()["id"]
    db.commit()
    return hid


def test_a_weak_suggestion_is_never_ratified_by_default(client, db):
    """One click must never ratify a date the filing does not support: a one-row filing's period of report is
    suggested, NOT agreeing by default (decision W3-b; ``_one_row_hold``). A default approve is 422 and writes
    nothing (the table counted); the same date SENT is the steward's call."""
    hid = _one_row_hold(db)
    it = _items(client)[("0000000000-15-000001", 0)]
    assert it["bucket"] == "owed"
    # since W3-1 the period of report (it was the arithmetic's 2014-09-14, before that period)
    assert (it["suggested_date"], it["agrees"], it["market_date"]) == (
        "2015-08-27",
        False,
        "2015-09-11",
    )
    assert it["suggestion_basis"] == "period_of_report_one_row"
    before = _count(db)
    for body in (None, {}, {"reason": "looks right"}):
        r = client.post(f"{BASE}/{hid}/approve", json=body)
        assert r.status_code == 422, r.text
        assert "does not agree with the filing on its own" in r.json()["detail"]
    assert _count(db) == before
    assert _items(client)[("0000000000-15-000001", 0)]["bucket"] == "owed"
    # the steward may still send a date (here the period of report): their decision, not a default
    r = client.post(f"{BASE}/{hid}/approve", json={"ratified_date": "2015-08-27"})
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["ratified_date"] == "2015-08-27"
    assert _count(db) == before + 1


def test_the_setting_lets_one_click_ratify_a_one_row_filings_period_of_report(
    client, db, monkeypatch
):
    """``ALPHADECK_INSIDER_HOLD_ONE_ROW_POR_AGREES=true`` (decision W3-b's switch): the same one-row suggestion
    agrees, so Approve with no date ratifies it."""
    from domain.settings import get_settings

    hid = _one_row_hold(db)
    monkeypatch.setenv("ALPHADECK_INSIDER_HOLD_ONE_ROW_POR_AGREES", "true")
    get_settings.cache_clear()
    try:
        it = _items(client)[("0000000000-15-000001", 0)]
        assert (it["suggested_date"], it["agrees"]) == ("2015-08-27", True)
        r = client.post(f"{BASE}/{hid}/approve")
        assert r.status_code == 200, r.text
        assert r.json()["decision"]["ratified_date"] == "2015-08-27"
    finally:
        monkeypatch.delenv("ALPHADECK_INSIDER_HOLD_ONE_ROW_POR_AGREES")
        get_settings.cache_clear()


def test_a_dismiss_needs_a_reason(client, db):
    H.seed(db, "late")
    hid = _items(client)[(LATE, 2)]["hold_id"]
    before = _count(db)
    assert client.post(f"{BASE}/{hid}/dismiss", json={}).status_code == 422
    assert client.post(f"{BASE}/{hid}/dismiss", json={"reason": ""}).status_code == 422
    assert client.post(f"{BASE}/{hid}/dismiss", json={"reason": "   "}).status_code == 422
    assert _count(db) == before


def test_no_date_and_no_suggestion_or_no_filing_date_is_422(client, db):
    """BBAI's real Form 4, ingested with no filing date and no acceptance: nothing to suggest, and no ceiling
    any ratified date can be checked against."""
    _bbai(db)
    it = _items(client)[(_BBAI[2], 0)]
    assert it["bucket"] == "owed" and it["suggested_date"] is None and it["agrees"] is False
    before = _count(db)
    assert client.post(f"{BASE}/{it['hold_id']}/approve").status_code == 422
    r = client.post(f"{BASE}/{it['hold_id']}/approve", json={"ratified_date": "2023-03-23"})
    assert r.status_code == 422 and "no acceptance and no filing date" in r.json()["detail"]
    assert _count(db) == before


# --- W3-1: the suggestion reads the filing's own dates; withheld with its window; the copy -----------------


def test_a_00yy_hold_reads_its_filings_stored_rows_and_accept_ratifies_the_footnote_date(
    client, db
):
    """BBAI's real Form 4 (period of report and row 0 written ``0023-03-23``), ingested with its recorded filing
    date (2024-03-08, as the steward review read it; no acceptance): rows 1 and 2 are stored, row 0 is held. The
    suggestion reads the footnote row 0 cites ("On March 23, 2023, ...") and the period of report read as 2023, and
    the stored row 1 (2023-11-13) follows it in the filing's balances: it agrees, so Accept with no date ratifies
    it."""
    _bbai(db, filed=date(2024, 3, 8))
    it = _items(client)[(_BBAI[2], 0)]
    assert it["bucket"] == "owed"
    assert (it["suggested_date"], it["agrees"]) == ("2023-03-23", True)
    assert it["suggestion_basis"] == "footnote" and "footnote" in it["suggestion_words"]
    assert (it["window_from"], it["window_to"]) == ("2023-03-23", "2023-11-13")
    assert it["copies"] == []
    r = client.post(f"{BASE}/{it['hold_id']}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["ratified_date"] == "2023-03-23"
    detail = _detail(db, r.json()["decision"]["id"])
    assert detail["suggestion_basis"] == "footnote" and detail["ratified_from"] == "suggestion"


def test_a_suggestion_the_filings_balances_contradict_is_withheld_and_accept_needs_the_date(
    client, db
):
    """DIRECT INPUT shaped like a real owed row (its filing's dates, shares and balances, under a made-up
    accession): a grant written 2009-07-17 on a filing made 2009-07-13 (period of report 2007-12-01), whose balance
    (110,000 -> 250,000) follows the stored 2009-07-09 row. The arithmetic's 2008-07-17 is before that row: no
    suggestion, the filing's window instead, and Accept with no date is refused until a date is sent.
    """
    sid = H.make_security(db, "late")
    acc = "0000000000-09-000005"
    accepted = datetime(2009, 7, 13, 20, tzinfo=timezone.utc)
    for seq, day, shares, after in (
        (0, date(2007, 12, 1), 10000.0, 10000.0),
        (1, date(2009, 7, 9), 100000.0, 110000.0),
    ):
        append_fact(
            db,
            "fact_insider_txn",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                "accession": acc,
                "txn_seq": seq,
                "insider_name": "An Insider",
                "txn_code": "A",
                "shares": shares,
                "price": 0.0,
                "valid_from": day,
                "direct_indirect": "D",
                "shares_owned_after": after,
                "accepted": accepted,
            },
        )
    row = {
        "insider_name": "An Insider",
        "txn_code": "A",
        "shares": 140000.0,
        "price": 0.0,
        "acquired_disposed": "A",
        "direct_indirect": "D",
        "shares_owned_after": 250000.0,
        "txn_date_as_filed": "2009-07-17",
        "txn_footnotes": ["Restricted stock grant for services."],
    }
    with db.cursor() as cur:
        cur.execute(
            'INSERT INTO insider_txn_hold (tenant_id, security_id, accession, txn_seq, form, reason, "row", '
            "filed, accepted, period_of_report_as_filed) "
            "VALUES (%s, %s, %s, 2, '4', 'after_filing', %s, %s, %s, %s) RETURNING id",
            (DEFAULT_TENANT_ID, sid, acc, Json(row), date(2009, 7, 13), accepted, "2007-12-01"),
        )
        hid = cur.fetchone()["id"]
    db.commit()
    it = _items(client)[(acc, 2)]
    assert (it["suggested_date"], it["agrees"]) == (None, False)
    assert it["suggestion_basis"] is None
    assert (it["window_from"], it["window_to"]) == ("2009-07-09", "2009-07-13")
    assert it["suggestion_words"] == (
        "2008-07-17 is before the 2009-07-09 row this one follows in the filing's balances (110,000 -> 250,000 "
        "shares); the filing puts it between 2009-07-09 and 2009-07-13"
    )
    before = _count(db)
    r = client.post(f"{BASE}/{hid}/approve")
    assert r.status_code == 422, r.text
    assert (
        "send the date" in r.json()["detail"] and "2009-07-09 and 2009-07-13" in r.json()["detail"]
    )
    assert _count(db) == before
    r = client.post(f"{BASE}/{hid}/approve", json={"ratified_date": "2009-07-13"})
    assert r.status_code == 200, r.text
    assert _count(db) == before + 1


def test_a_filing_row_held_under_two_securities_names_its_copy_and_each_is_decided_alone(
    client, db
):
    """The committed two-security filing (``tests/held_fixtures.py``, ``NO_ACCEPTANCE_*``), stored under both of its
    issuer's securities WITH SEC's acceptance: row 1 (``0025-07-25``) is held twice. Each hold names the other as
    its copy; rows 0, 2 and 3 write 2025-07-25 and the period of report reads 2025, so the suggestion agrees. An
    Accept decides that hold only."""
    a = H.make_issuer_security(db, "HLDNA", H.NO_ACCEPTANCE_CIK)
    b = H.make_issuer_security(db, "HLDNB", H.NO_ACCEPTANCE_CIK)
    xml = (H.HELD_DIR / H.NO_ACCEPTANCE_FILE).read_text(encoding="utf-8")
    for sid in (a, b):
        ingest_form4(
            db,
            sid,
            xml,
            H.NO_ACCEPTANCE_ACCESSION,
            accepted=H.NO_ACCEPTANCE_ACCEPTED,
            filed=H.NO_ACCEPTANCE_FILED,
            member_cik=H.NO_ACCEPTANCE_CIK,
        )
    db.commit()
    body = client.get(BASE).json()
    held = {it["ticker"]: it for it in body["owed"]}
    assert set(held) == {"HLDNA", "HLDNB"}
    for mine, other in (("HLDNA", "HLDNB"), ("HLDNB", "HLDNA")):
        it = held[mine]
        assert (it["suggested_date"], it["agrees"]) == ("2025-07-25", True)
        assert it["suggestion_basis"] == "sibling_row"
        assert it["copies"] == [{"hold_id": held[other]["hold_id"], "ticker": other}]
        assert (it["window_from"], it["window_to"]) == ("2025-07-25", "2025-07-25")
    r = client.post(f"{BASE}/{held['HLDNA']['hold_id']}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["ratified_date"] == "2025-07-25"
    assert r.json()["copies"] == [{"hold_id": held["HLDNB"]["hold_id"], "ticker": "HLDNB"}]
    after = client.get(BASE).json()
    assert [it["ticker"] for it in after["owed"]] == ["HLDNB"]
    assert [it["ticker"] for it in after["decided"]] == ["HLDNA"]


# --- access, status -------------------------------------------------------------------------------------


def test_a_plain_user_is_refused_the_way_the_amendment_routes_refuse(anon, db):  # noqa: F811
    c = _signed_in(db, "plain-holds@test.invalid")
    nobody = uuid.uuid4()
    for method, path in (
        ("GET", BASE),
        ("POST", f"{BASE}/{nobody}/approve"),
        ("POST", f"{BASE}/{nobody}/dismiss"),
        ("POST", f"{BASE}/decisions/{nobody}/revoke"),
        ("GET", "/admin/insider-amendments"),
    ):
        assert c.request(method, path, json={"reason": "x"}).status_code == 403, path


def test_admin_status_counts_the_owed_rows(client, db):
    assert client.get("/admin/status").json()["insider_holds_owed"] == 0
    H.seed(db, "late")
    H.seed(db, "on_time_a")  # placed by the rule: not owed
    assert client.get("/admin/status").json()["insider_holds_owed"] == 3
    hid = _items(client)[(LATE, 0)]["hold_id"]
    assert client.post(f"{BASE}/{hid}/dismiss", json={"reason": "not a trade"}).status_code == 200
    assert client.get("/admin/status").json()["insider_holds_owed"] == 2


@pytest.mark.parametrize("group", ["late"])
def test_the_list_is_read_only(client, db, group):
    H.seed(db, group)
    before = _count(db)
    for _ in range(2):
        assert client.get(BASE).status_code == 200
    assert _count(db) == before
