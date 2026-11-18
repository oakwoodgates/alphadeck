"""V2 W5d: no response a PLAIN USER can receive carries the steward's email (or any user's) as the setter of a value.

Real sign-ins (the ``world`` fixture of ``tests/app/test_platform_values_user_api.py``: plain users A and B, the
steward S, each in their own workspace). On a company on A's card: a share count and a cash/burn value the steward
verified (``ratified_by`` = S's email, the data tenant), a company catalyst a person ratified (the data tenant), and
A's OWN purity, ratified through A's own session (``fact_revenue_mix`` in W_A, stamped with A's email). Every read A
can make of them answers with no ``@`` at all, and with the right words:

- the scored read (``GET /workbench/theses/{id}/scored``): each provenance says ``set_by`` ``steward`` (the share
  count, the cash) or ``you`` (A's purity), never ``ratified_by``; no ``unconfirmed_estimates`` (the plan's Q8);
- the Filing Stream (``GET /dashboards/filing-stream/{id}``): the steward's rows are "verified value" rows reading
  "Verified by the steward on <day>"; A's purity row is still A's own ("Ratified by you on <day>");
- Ticker Lookup (``GET /dashboards/ticker-lookup/{id}``): ``set_by`` on every row, never ``ratifier``;
- the Evidence Ledger (``GET /dashboards/evidence-ledger/{id}``): the steward's rows are class ``steward`` by
  ``steward``; A's purity stays class ``operator`` by ``you``, and the tallies count each in its own class.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from domain.enums import Grade
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.catalyst import ingest_catalyst
from ingest.shares import ingest_shares_outstanding
from platform_values import words
from tests.app import test_platform_values_user_api as pv

# the real sign-in world of the user read's tests (plain users A and B, the steward S), reused as is
world = pv.world
STEWARD_EMAIL, _card, _company = pv.STEWARD_EMAIL, pv._card, pv._company

REF = "https://www.sec.gov/Archives/edgar/data/7/000000000726000044/0000000007-26-000044-index.htm"


def _seed(world, db) -> tuple[uuid.UUID, uuid.UUID]:
    """A company on A's card with the steward's share count, cash and catalyst, and A's own purity."""
    asof = world["asof"]
    sid = _company(db, "STW")
    tid = _card(world, db, "a", [sid])
    ingest_shares_outstanding(
        db,
        sid,
        shares=52_113_000,
        source="10-q-cover",
        source_ref=REF,
        event_date=asof - timedelta(days=30),
        ratified_by=STEWARD_EMAIL,
        tenant_id=DATA_TENANT_ID,
    )
    ingest_cash_burn(
        db,
        sid,
        cash_usd=890_000_000,
        quarterly_burn_usd=31_400_000,
        source="10-Q",
        source_ref=REF,
        event_date=asof - timedelta(days=30),
        ratified_by=STEWARD_EMAIL,
        tenant_id=DATA_TENANT_ID,
    )
    ingest_catalyst(
        db,
        sid,
        catalyst_type="regulatory",
        grade=Grade.CORE,
        label="a decision date",
        source="ratified",
        source_ref=REF,
        event_date=asof - timedelta(days=5),
        ratified_by=STEWARD_EMAIL,
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()
    r = world["a"].post(  # A's own purity, through A's own session: stamped with A's email, in W_A
        "/workbench/facts",
        json={
            "fact_type": "revenue_mix",
            "security_id": str(sid),
            "source": "10-k-segment",
            "source_ref": REF,
            "event_date": (asof - timedelta(days=40)).isoformat(),
            "segment_label": "core",
            "mix_pct": 80,
        },
    )
    assert r.status_code == 200, r.text
    db.rollback()
    return sid, tid


def _get(c, path: str, asof) -> dict:
    r = c.get(path, params={"asof": asof.isoformat()})
    assert r.status_code == 200, r.text
    assert "@" not in r.text, path  # no email of anyone, anywhere in the answer
    return r.json()


def test_the_scored_read_says_who_set_a_value_as_a_kind_and_drops_the_unconfirmed_count(world, db):
    sid, tid = _seed(world, db)
    body = _get(world["a"], f"/workbench/theses/{tid}/scored", world["asof"])
    (m,) = body["members"]
    assert "unconfirmed_estimates" not in m
    details = {
        meter: [
            p["detail"] for p in m[meter]["provenance"] if p["source"] not in ("computed", "price")
        ]
        for meter in ("purity", "runway", "market_cap")
    }
    assert [d.get("set_by") for d in details["runway"]] == ["steward"]
    assert [d.get("set_by") for d in details["market_cap"]] == ["steward"]
    assert [d.get("set_by") for d in details["purity"]] == ["you"]  # A's own: never the steward's
    assert all("ratified_by" not in d for ds in details.values() for d in ds)


def test_the_filing_stream_words_the_stewards_rows_and_keeps_the_users_purity_hers(world, db):
    sid, tid = _seed(world, db)
    body = _get(world["a"], f"/dashboards/filing-stream/{tid}", world["asof"])
    rows = [r for d in body["groups"] for r in d["rows"] if r["kind"] in ("ratified", "catalyst")]
    on = words.day_words(world["asof"])  # the rows were stored today
    shares = next(r for r in rows if "shares outstanding" in r["headline"])
    cash = next(r for r in rows if r["headline"].startswith("cash"))
    mix = next(r for r in rows if "revenue mix" in r["headline"])
    cat = next(r for r in rows if r["kind"] == "catalyst")
    for r in (shares, cash):
        assert r["form"] == "verified value"
        assert r["detail"][-1] == f"Verified by the steward on {on}"
    assert mix["form"] == "ratified fact"
    assert mix["detail"][-1] == f"Ratified by you on {on}"
    assert cat["detail"][-1] == f"Verified by the steward on {on}"


def test_ticker_lookup_names_the_setter_as_a_kind(world, db):
    sid, tid = _seed(world, db)
    body = _get(world["a"], f"/dashboards/ticker-lookup/{sid}", world["asof"])
    rows = [r for d in body["filings"]["days"] for r in d["rows"]] + body["catalyst_facts"]
    assert rows and all("ratifier" not in r for r in rows)
    by = {
        r["headline"].split(" ")[0]: r["set_by"]
        for r in rows
        if r["kind"] in ("ratified", "catalyst")
    }
    assert by["shares"] == "steward" and by["cash"] == "steward" and by["revenue"] == "you"
    assert [r["set_by"] for r in body["catalyst_facts"]] == ["steward"]


def test_the_evidence_ledger_classes_the_stewards_rows_apart_from_the_users_own(world, db):
    sid, tid = _seed(world, db)
    body = _get(world["a"], f"/dashboards/evidence-ledger/{tid}", world["asof"])
    rows = [r for d in body["groups"] for r in d["rows"]] + body["standing"]
    facts = [r for r in rows if r["kind"] in ("ratified", "catalyst_fact")]
    got = sorted((r["label"].split(" ")[0], r["authority"], r["by"]) for r in facts)
    assert got == [
        ("cash", "steward", "steward"),
        ("regulatory:", "steward", "steward"),
        ("revenue", "operator", "you"),
        ("shares", "steward", "steward"),
    ]
    t = body["tallies"]
    assert (t["facts_ratified"], t["facts_verified"], t["facts_stored"]) == (1, 3, 0)
    assert [c["key"] for c in t["by_authority"]] == [
        "operator",
        "steward",
        "system",
        "automatic",
        "card",
    ]
