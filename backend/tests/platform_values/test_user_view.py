"""V2 W5a: the user's read of platform values (``platform_values.user_view.values_for``) and its answer
(``app.routers.platform_values.company_out``), from real rows.

- each of the four states, with the user words and who set it as a KIND (never an email: a steward-verified
  value's serialized answer holds no ``@``);
- approved B1 (b): a value awaiting the steward carries its state, the filing's URL and date and the located
  passages, and NO figure, for a share count and for cash (the serialized answer is scanned for every figure);
- a shown value with a NEWER flagged candidate keeps the shown value and adds the pending line (date and URL, no
  figure); an OLDER candidate adds nothing;
- an annual-cover share count carries the annual line; a cash value its composition, currency (the one rule in
  ``domain.cash_currency``) and pace (``domain.burn_pace.stored_pace``);
- a ``source_ref`` that is not a safe URL is ``null`` on the wire; an accession resolves to the EDGAR index;
- ``my_flag``: the caller's OWN open flag on the value, else their newest closed one; never another user's;
- batched: 1 company and 30 run the same number of SQL statements (counted on this test's connection only).

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Dates come from ``pinned_today`` (the
market clock read inside the test, never at import).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
import pytest

from app import auth
from app.routers.platform_values import company_out
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.shares import ingest_shares_outstanding
from platform_values import user_view
from repositories import platform_value_repo as repo
from tests.workspaces import new_workspace

STEWARD = "steward@example.test"
CIK = "0000000007"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
T0 = datetime(2026, 1, 2, tzinfo=timezone.utc)

# the parked figures of B1's test: none may reach the user's answer in any form
PARKED_SHARES = 143_310_556
PARKED_CASH = 69_343_000_000
PARKED_INVESTMENTS = 46_900_000_000
PARKED_BURN = 1_234_567
PARKED_PACE = 2_345_678


def _company(db, ticker: str = "USER") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, '2026-01-01')",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", CIK),
        )
    db.commit()
    return sid


def _user(db, email: str) -> UUID:
    uid = auth.create_user(
        db,
        email=email,
        display_name=email,
        password="correct horse battery",
        workspace_id=new_workspace(db, f"user view {email}"),
    )
    db.commit()
    return uid


@pytest.fixture
def users(db):
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")  # the auth tables are not in the shared truncate
    db.commit()
    return {k: _user(db, f"{k}@uv.test") for k in ("a", "b", "c")}


def _shares(db, sid, d, *, by="auto", ref=OLD_REF, source="10-q-cover", count=10_000_000):
    ingest_shares_outstanding(
        db,
        sid,
        shares=count,
        source=source,
        source_ref=ref,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()


def _cash(db, sid, d, *, by="auto", ref=OLD_REF, **extra):
    ingest_cash_burn(
        db,
        sid,
        cash_usd=extra.pop("cash_usd", 890_000_000),
        quarterly_burn_usd=extra.pop("quarterly_burn_usd", 31_400_000),
        source=extra.pop("source", "10-Q"),
        source_ref=ref,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
        **extra,
    )
    db.commit()


def _flagged(db, sid, d, fact_type="shares_outstanding", *, ref=NEW_REF, passages=None):
    values = (
        {"shares": PARKED_SHARES, "ads_ratio": 2, "ads_ratio_status": "known"}
        if fact_type == "shares_outstanding"
        else {
            "cash_usd": PARKED_CASH,
            "quarterly_burn_usd": PARKED_BURN,
            "investments_usd": PARKED_INVESTMENTS,
            "statement_currency": "USD",
            "ytd_quarterly_burn_usd": PARKED_PACE,
            "ytd_burn_days": 182,
        }
    )
    cid = repo.append_candidate(
        db,
        security_id=sid,
        fact_type=fact_type,
        outcome="flag",
        flags=["multi-value-cover", "investments-unreconciled"],
        source="10-q-cover" if fact_type == "shares_outstanding" else "10-Q",
        source_ref=ref,
        event_date=d,
        filing_accepted=datetime.combine(d + timedelta(days=30), datetime.min.time(), timezone.utc),
        note=f"parked {PARKED_SHARES} {PARKED_CASH} by {STEWARD}",
        located_passages=(
            passages
            if passages is not None
            else [
                {
                    "kind": "cover",
                    "source_ref": ref,
                    "anchor": "outstanding",
                    "excerpt": "Indicate the number of shares outstanding of each class of common stock.",
                }
            ]
        ),
        **values,
    )
    db.commit()
    return cid


def _nothing(db, sid, reason, fact_type="cash_burn"):
    repo.append_candidate(
        db, security_id=sid, fact_type=fact_type, outcome="nothing", reason=reason
    )
    db.commit()


def _read(db, sid, user_id):
    db.commit()
    got = user_view.values_for(db, [sid], user_id=user_id)
    db.rollback()
    return got[sid]


def _json(cv) -> str:
    return company_out(cv).model_dump_json()


# --- the four states ----------------------------------------------------------------------------------------


def test_no_value_yet_with_its_reason(db, users, pinned_today):
    sid = _company(db)
    _nothing(db, sid, "cash-generative")
    cv = _read(db, sid, users["a"])
    out = json.loads(_json(cv))
    assert out["ticker"] == "USER" and out["name"] == "USER Corp"
    assert out["security_id"] == str(sid)
    assert out["shares"] == {
        "fact_type": "shares_outstanding",
        "state": "no_value",
        "reason": "not-extracted",
        "label": "No value yet: the platform has not read this company's filings yet",
        "set_by": None,
        "value": None,
        "annual_line": None,
        "pending": None,
        "awaiting": None,
        "my_flag": None,
    }
    assert (out["cash_burn"]["state"], out["cash_burn"]["reason"], out["cash_burn"]["label"]) == (
        "no_value",
        "cash-generative",
        "Runway does not apply: the company generated cash",
    )


def test_from_the_filing(db, users, pinned_today):
    sid = _company(db)
    d = pinned_today - timedelta(days=40)
    _shares(db, sid, d)
    out = json.loads(_json(_read(db, sid, users["a"])))["shares"]
    assert (out["state"], out["set_by"], out["label"]) == (
        "from_filing",
        "machine",
        "From the filing",
    )
    assert out["value"]["shares"] == 10_000_000
    assert out["value"]["valid_from"] == d.isoformat()
    assert out["value"]["source_url"] == OLD_REF
    assert out["annual_line"] is None and out["pending"] is None and out["awaiting"] is None


@pytest.mark.parametrize(
    ("by", "kind", "label"),
    [
        (STEWARD, "steward", "Verified by the steward"),
        ("operator", "carried", "Verified (carried over from V1)"),
    ],
)
def test_verified_says_who_by_kind_and_never_the_email(db, users, pinned_today, by, kind, label):
    sid = _company(db)
    d = pinned_today - timedelta(days=40)
    _shares(db, sid, d, by=by)
    _cash(db, sid, d, by=by, note=f"corrected by {STEWARD}")
    text = _json(_read(db, sid, users["a"]))
    out = json.loads(text)
    for key in ("shares", "cash_burn"):
        assert (out[key]["state"], out[key]["set_by"], out[key]["label"]) == (
            "verified",
            kind,
            label,
        )
    assert "@" not in text


def test_awaiting_the_steward_carries_no_figure_for_shares_or_cash(db, users, pinned_today):
    """Approved B1 (b): the state, the filing's URL and date and the located passage; no candidate figure, for a
    share count and for cash, anywhere in the serialized answer."""
    sid = _company(db)
    d = pinned_today - timedelta(days=10)
    _flagged(db, sid, d, "shares_outstanding")
    _flagged(db, sid, d, "cash_burn")
    text = _json(_read(db, sid, users["a"]))
    out = json.loads(text)
    for key, ft in (("shares", "shares_outstanding"), ("cash_burn", "cash_burn")):
        v = out[key]
        assert (v["state"], v["label"], v["value"], v["set_by"]) == (
            "awaiting_steward",
            "Awaiting the steward",
            None,
            None,
        )
        aw = v["awaiting"]
        assert aw["event_date"] == d.isoformat()
        assert aw["source_url"] == NEW_REF
        assert aw["filing_accepted"] is not None
        assert aw["line"].startswith("The steward has not checked the ")
        assert [p["excerpt"] for p in aw["located_passages"]] == [
            "Indicate the number of shares outstanding of each class of common stock."
        ]
        assert aw["located_passages"][0]["url"] == NEW_REF
        assert set(aw) == {
            "event_date",
            "filing_accepted",
            "source_url",
            "line",
            "located_passages",
            "passages_unreadable",
        }
    for figure in (PARKED_SHARES, PARKED_CASH, PARKED_INVESTMENTS, PARKED_BURN, PARKED_PACE):
        for form in (str(figure), f"{figure:,}", f"{float(figure)}", f"{figure:.4e}"[:6]):
            assert form not in text, form
    assert (
        "multi-value-cover" not in text and "investments-unreconciled" not in text
    )  # no flag names
    assert "@" not in text  # the candidate's note is never copied


def test_a_passage_that_does_not_read_is_counted_not_dropped(db, users, pinned_today):
    sid = _company(db)
    _flagged(db, sid, pinned_today - timedelta(days=10), passages=[{"text": "old shape"}])
    aw = json.loads(_json(_read(db, sid, users["a"])))["shares"]["awaiting"]
    assert (aw["located_passages"], aw["passages_unreadable"]) == ([], 1)


# --- the pending line --------------------------------------------------------------------------------------


def test_a_newer_flag_keeps_the_value_and_adds_the_pending_line(db, users, pinned_today):
    sid = _company(db)
    old, new = pinned_today - timedelta(days=40), pinned_today - timedelta(days=10)
    _shares(db, sid, old)
    _cash(db, sid, old)
    _flagged(db, sid, new, "shares_outstanding")
    _flagged(db, sid, new, "cash_burn")
    text = _json(_read(db, sid, users["a"]))
    out = json.loads(text)
    for key in ("shares", "cash_burn"):
        v = out[key]
        assert (v["state"], v["awaiting"]) == ("from_filing", None)
        assert v["value"]["valid_from"] == old.isoformat()  # the applied value is kept
        assert v["pending"]["event_date"] == new.isoformat()
        assert v["pending"]["source_url"] == NEW_REF
        assert v["pending"]["line"].startswith("A newer filing (dated ")
        assert set(v["pending"]) == {"event_date", "filing_accepted", "source_url", "line"}
    assert out["shares"]["value"]["shares"] == 10_000_000
    for figure in (PARKED_SHARES, PARKED_CASH, PARKED_INVESTMENTS, PARKED_BURN, PARKED_PACE):
        assert str(figure) not in text and f"{figure:,}" not in text


def test_an_older_flag_adds_no_pending_line(db, users, pinned_today):
    sid = _company(db)
    _shares(db, sid, pinned_today - timedelta(days=10))
    _flagged(db, sid, pinned_today - timedelta(days=40))
    v = json.loads(_json(_read(db, sid, users["a"])))["shares"]
    assert (v["state"], v["pending"], v["awaiting"]) == ("from_filing", None, None)


# --- the annual line, the cash composition, currency and pace ------------------------------------------------


def test_an_annual_cover_count_carries_the_annual_line(db, users, pinned_today):
    sid = _company(db)
    d = pinned_today - timedelta(days=200)
    _shares(db, sid, d, source="annual-cover")
    v = json.loads(_json(_read(db, sid, users["a"])))["shares"]
    assert v["value"]["source"] == "annual-cover"
    assert v["annual_line"].startswith("annual figure from the 20-F or 40-F cover, as of ")
    assert v["annual_line"].endswith(f", {d.year}")


def test_the_cash_value_carries_its_composition_currency_and_pace(db, users, pinned_today):
    sid = _company(db)
    d = pinned_today - timedelta(days=40)
    _cash(
        db,
        sid,
        d,
        cash_usd=890_000_000,
        quarterly_burn_usd=31_400_000,
        currency="EUR",
        investments_usd=120_000_000,
        ytd_quarterly_burn_usd=12_000_000,
        ytd_burn_days=273,
    )
    v = json.loads(_json(_read(db, sid, users["a"])))["cash_burn"]["value"]
    assert (v["cash_usd"], v["quarterly_burn_usd"], v["investments_usd"]) == (
        890_000_000,
        31_400_000,
        120_000_000,
    )
    assert (v["currency"], v["currency_symbol"]) == ("EUR", "€")
    assert v["pace"]["ytd_quarterly_burn_usd"] == 12_000_000 and v["pace"]["ytd_days"] == 273
    assert v["pace"]["caveat"] is not None and "€" in v["pace"]["caveat"]
    assert v["shares"] is None


@pytest.mark.parametrize(
    ("currency", "source", "symbol"),
    [(None, "annual-statements", "?"), (None, "10-Q", "$"), ("USD", "10-Q", "$")],
)
def test_the_currency_label_follows_the_one_rule(db, users, pinned_today, currency, source, symbol):
    sid = _company(db)
    _cash(db, sid, pinned_today - timedelta(days=40), currency=currency, source=source)
    v = json.loads(_json(_read(db, sid, users["a"])))["cash_burn"]["value"]
    assert (v["currency"], v["currency_symbol"]) == (currency, symbol)


# --- the URL rule --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "url"),
    [
        ("javascript:alert(1)", None),
        ("https://user:pass@evil.example/x", None),
        ("a free-text reference", None),
        ("0000000007-26-000123", "https://www.sec.gov/Archives/edgar/data/7/000000000726000123/"),
    ],
)
def test_a_source_ref_that_is_not_a_safe_url_is_null(db, users, pinned_today, ref, url):
    sid = _company(db)
    d = pinned_today - timedelta(days=40)
    _shares(db, sid, d, ref=ref)
    _cash(db, sid, d)
    _flagged(
        db,
        sid,
        pinned_today - timedelta(days=10),
        "cash_burn",
        ref=ref,
        passages=[{"kind": "cover", "source_ref": ref, "anchor": "a", "excerpt": "e"}],
    )
    out = json.loads(_json(_read(db, sid, users["a"])))
    assert out["shares"]["value"]["source_url"] == url
    assert out["cash_burn"]["pending"]["source_url"] == url


def test_a_hostile_passage_url_is_null(db, users, pinned_today):
    sid = _company(db)
    _flagged(
        db,
        sid,
        pinned_today - timedelta(days=10),
        passages=[{"kind": "cover", "source_ref": "javascript:x", "anchor": "a", "excerpt": "e"}],
    )
    aw = json.loads(_json(_read(db, sid, users["a"])))["shares"]["awaiting"]
    assert aw["located_passages"][0]["url"] is None


# --- my_flag: the caller's own, never another user's -------------------------------------------------------


def _flag_row(
    db, uid, sid, value_type, *, note, opened, closed=None, outcome=None, outcome_note=None, ref=""
):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO value_flag (user_id, security_id, value_type, value_ref, note, opened_at, closed_at, "
            "outcome, outcome_note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (uid, sid, value_type, ref, note, opened, closed, outcome, outcome_note),
        )
        fid = cur.fetchone()["id"]
    db.commit()
    return fid


def _scene_two_users_flag_the_same_value(db, users, sid):
    """A: an older closed flag and an OPEN one on the share count. B: two closed flags on the SAME share count
    (B's newest closed is the answer), and a catalyst flag on the company (never a share count's).
    """
    t = T0
    a_old = _flag_row(
        db,
        users["a"],
        sid,
        "shares_outstanding",
        note="A old",
        opened=t,
        closed=t + timedelta(days=1),
        outcome="kept",
        outcome_note="kept for A",
    )
    a_open = _flag_row(
        db, users["a"], sid, "shares_outstanding", note="A open", opened=t + timedelta(days=2)
    )
    b_older = _flag_row(
        db,
        users["b"],
        sid,
        "shares_outstanding",
        note="B older",
        opened=t,
        closed=t + timedelta(days=3),
        outcome="withdrawn",
    )
    b_newer = _flag_row(
        db,
        users["b"],
        sid,
        "shares_outstanding",
        note="B newer",
        opened=t + timedelta(days=4),
        closed=t + timedelta(days=5),
        outcome="fixed",
        outcome_note="fixed for B",
    )
    _flag_row(
        db,
        users["b"],
        sid,
        "catalyst",
        note="B catalyst",
        opened=t + timedelta(days=6),
        ref="cat-1",
    )
    return {"a_old": a_old, "a_open": a_open, "b_older": b_older, "b_newer": b_newer}


def test_my_flag_is_my_open_one_else_my_newest_closed_and_never_another_users(
    db, users, pinned_today
):
    sid = _company(db)
    ids = _scene_two_users_flag_the_same_value(db, users, sid)
    a = json.loads(_json(_read(db, sid, users["a"])))
    b = json.loads(_json(_read(db, sid, users["b"])))
    c = json.loads(_json(_read(db, sid, users["c"])))
    assert a["shares"]["my_flag"]["id"] == str(ids["a_open"])
    assert a["shares"]["my_flag"]["note"] == "A open"
    assert (a["shares"]["my_flag"]["closed_at"], a["shares"]["my_flag"]["outcome"]) == (None, None)
    assert b["shares"]["my_flag"]["id"] == str(ids["b_newer"])
    assert (b["shares"]["my_flag"]["outcome"], b["shares"]["my_flag"]["outcome_note"]) == (
        "fixed",
        "fixed for B",
    )
    assert set(b["shares"]["my_flag"]) == {
        "id",
        "note",
        "opened_at",
        "closed_at",
        "outcome",
        "outcome_note",
    }
    assert c["shares"]["my_flag"] is None
    for out in (a, b, c):
        assert out["cash_burn"]["my_flag"] is None  # a flag on the share count is not the cash's
    a_text, b_text = json.dumps(a), json.dumps(b)
    for word in (
        "B older",
        "B newer",
        "fixed for B",
        "B catalyst",
        str(ids["b_newer"]),
        str(ids["b_older"]),
    ):
        assert word not in a_text, word
    for word in ("A old", "A open", "kept for A", str(ids["a_open"]), str(ids["a_old"])):
        assert word not in b_text, word


def test_my_flag_is_filtered_by_the_user_in_the_sql(db, users, pinned_today, monkeypatch):
    """The flag query names the caller's id in its parameters (never a read of every user's flags, then a filter
    in Python)."""
    sid = _company(db)
    _scene_two_users_flag_the_same_value(db, users, sid)
    seen: list[tuple[str, object]] = []
    real = psycopg.Cursor.execute

    def spy(self, query, params=None, *args, **kwargs):
        if self.connection is db and "value_flag" in str(query):
            seen.append((str(query), params))
        return real(self, query, params, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", spy)
    _read(db, sid, users["a"])
    assert len(seen) == 1
    query, params = seen[0]
    assert "user_id = %s" in " ".join(query.split())
    assert users["a"] in params and users["b"] not in params


# --- batched --------------------------------------------------------------------------------------------------


def _count_statements(db, monkeypatch) -> list[str]:
    seen: list[str] = []
    real = psycopg.Cursor.execute

    def counting(self, query, *args, **kwargs):
        if self.connection is db:  # this test's own connection only
            seen.append(str(query)[:60])
        return real(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", counting)
    return seen


def test_one_company_and_thirty_run_the_same_number_of_statements(
    db, users, pinned_today, monkeypatch
):
    old, new = pinned_today - timedelta(days=40), pinned_today - timedelta(days=10)
    ids = []
    for n in range(30):
        sid = _company(db, f"B{n}")
        _shares(db, sid, old)
        _cash(db, sid, old)
        _flagged(db, sid, new, "shares_outstanding")
        _nothing(db, sid, "no-candidate")
        _flag_row(db, users["a"], sid, "cash_burn", note="n", opened=T0)
        ids.append(sid)
    db.commit()
    user_view.values_for(db, ids[:1], user_id=users["a"])  # warm-up
    db.rollback()
    seen = _count_statements(db, monkeypatch)
    one = user_view.values_for(db, ids[:1], user_id=users["a"])
    few = len(seen)
    db.rollback()
    seen.clear()
    many = user_view.values_for(db, ids, user_id=users["a"])
    db.rollback()
    assert (len(one), len(many)) == (1, 30)
    assert len(seen) == few, seen
    assert list(many) == ids  # the caller's order is kept


def test_an_unknown_company_is_absent(db, users, pinned_today):
    sid = _company(db)
    got = user_view.values_for(db, [uuid4(), sid], user_id=users["a"])
    assert list(got) == [sid]
    assert user_view.values_for(db, [], user_id=users["a"]) == {}
