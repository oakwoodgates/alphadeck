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


def _bbai(db) -> uuid.UUID:
    ticker, cik, accession, fname = _BBAI
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2020-01-01"),
        )
    ingest_form4(db, sid, (_FIX / fname).read_text(encoding="utf-8"), accession, member_cik=cik)
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
        "the filing was made 122 days after its period of report (2020-11-16), more than the on-time "
        f"window of {DEFAULT_CONFIG.insider_hold_on_time_days} days"
    )
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


def test_a_weak_suggestion_is_never_ratified_by_default(client, db):
    """One click must never ratify a date the filing contradicts. No real fixture carries a suggestion that
    disagrees with its period of report, so this is a DIRECT-INPUT contract: a hold inserted by hand (never
    presented as a real filing), shaped like a prod row the panel showed — a buy written 2015-09-14 on a filing
    accepted 2015-09-11 with period of report 2015-08-27, whose suggestion 2014-09-14 is before that period.
    A default approve is 422 and writes nothing (the table counted); the same date SENT is the steward's call.
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
    it = _items(client)[("0000000000-15-000001", 0)]
    assert it["bucket"] == "owed"
    assert (it["suggested_date"], it["agrees"], it["market_date"]) == (
        "2014-09-14",
        False,
        "2015-09-11",
    )
    before = _count(db)
    for body in (None, {}, {"reason": "looks right"}):
        r = client.post(f"{BASE}/{hid}/approve", json=body)
        assert r.status_code == 422, r.text
        assert "does not agree with the filing's period of report" in r.json()["detail"]
    assert _count(db) == before
    assert _items(client)[("0000000000-15-000001", 0)]["bucket"] == "owed"
    # the steward may still send a date (here the period of report): their decision, not a default
    r = client.post(f"{BASE}/{hid}/approve", json={"ratified_date": "2015-08-27"})
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["ratified_date"] == "2015-08-27"
    assert _count(db) == before + 1


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
