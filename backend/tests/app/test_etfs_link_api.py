"""The ETFs link at the promote write (``POST /workbench/theses``): funds only, and every fund there.

ETFs is the third fixed silent link (Context, ETFs, Bench). A fund is a member whose master row is
``instrument_kind='etf'``. The promote refuses — with a 422 and one plain sentence per offender, writing NOTHING
and never moving or dropping a row — a fund in any other link or in none, a company that is not a fund in ETFs,
and an ETFs row with no ``security_id``. The "silent or voting, never both" and "listed more than once" rules
cover ETFs like the other two. A re-send of the stored basket (the wipe-trap) keeps the ETFs rows and their
context role. Tickers here are made up (the funds are the repo's fixture funds).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID

_SEGS = [{"label": "Reactors"}, {"label": "Discovered"}]


def _security(db, ticker: str, *, kind: str = "equity", cik: str | None = None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _row(ticker: str, sid: uuid.UUID | None, segment: str | None, **kw) -> dict:
    d = {
        "ticker": ticker,
        "role": "r",
        "security_id": str(sid) if sid is not None else None,
        "segment": segment,
        "authored_by": "system_drafted",
    }
    d.update(kw)
    return d


def _promote(client, basket: list[dict], *, tid: str | None = None, narrative: str = "x"):
    return client.post(
        "/workbench/theses",
        json={
            "id": tid,
            "name": "ETFs link",
            "narrative": narrative,
            "ticker": None,
            "segments": _SEGS,
            "basket": basket,
        },
    )


def _count(db, tid: str, table: str = "basket_member") -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table} WHERE thesis_id = %s", (tid,))
        return cur.fetchone()["n"]


@pytest.fixture
def ids(db) -> dict[str, uuid.UUID]:
    return {
        "URA": _security(db, "URA", kind="etf"),
        "LIT": _security(db, "LIT", kind="etf"),
        "QZRA": _security(db, "QZRA", cik="0009990001"),
        "QZRB": _security(db, "QZRB", cik="0009990002"),
    }


def _saved(client, db, ids) -> str:
    """A saved card: one company in a real link, one fund in ETFs (with a context role)."""
    ok = _promote(
        client,
        [
            _row("QZRA", ids["QZRA"], "Reactors"),
            _row("URA", ids["URA"], "ETFs", context_role="benchmark"),
        ],
    )
    assert ok.status_code == 200, ok.text
    return ok.json()["id"]


def test_a_fund_in_etfs_saves_and_reads_back(client, db, ids):
    tid = _saved(client, db, ids)
    got = {m["ticker"]: m for m in client.get(f"/theses/{tid}").json()["basket"]}
    assert got["URA"]["segment"] == "ETFs" and got["URA"]["context_role"] == "benchmark"
    assert got["QZRA"]["segment"] == "Reactors"
    assert [s["label"] for s in client.get(f"/theses/{tid}").json()["segments"]][-3:] == [
        "Context",
        "ETFs",
        "Bench",
    ]


def test_a_hand_typed_etfs_label_on_a_fund_is_respelled(client, db, ids):
    r = _promote(client, [_row("LIT", ids["LIT"], " etfs ")])
    assert r.status_code == 200
    assert [m["segment"] for m in r.json()["basket"]] == ["ETFs"]


@pytest.mark.parametrize(
    ("segment", "where"),
    [
        ("Reactors", "Reactors"),
        ("Discovered", "Discovered"),
        ("Context", "Context"),
        (None, "no link"),
    ],
)
def test_a_fund_anywhere_but_etfs_is_refused_and_nothing_is_written(
    client, db, ids, segment, where
):
    tid = _saved(client, db, ids)
    before = (_count(db, tid), _count(db, tid, "basket_snapshot"))
    bad = _promote(
        client,
        [_row("QZRA", ids["QZRA"], "Reactors"), _row("URA", ids["URA"], segment)],
        tid=tid,
    )
    assert bad.status_code == 422
    detail = bad.json()["detail"]
    assert detail.startswith(f"URA is a fund but is in {where}")
    assert "ETFs holds funds only, and every fund goes in ETFs. Move it." in detail
    assert (_count(db, tid), _count(db, tid, "basket_snapshot")) == before  # nothing written
    stored = {m["ticker"]: m["segment"] for m in client.get(f"/theses/{tid}").json()["basket"]}
    assert stored == {"QZRA": "Reactors", "URA": "ETFs"}


def test_a_company_that_is_not_a_fund_in_etfs_is_refused(client, db, ids):
    bad = _promote(client, [_row("QZRB", ids["QZRB"], "ETFs")])
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("QZRB is not a fund but is in ETFs")


def test_an_etfs_row_with_no_security_is_refused(client, db, ids):
    bad = _promote(client, [_row("NOSEC", None, "ETFs")])
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith(
        "NOSEC is in ETFs but is not matched to a security, so it can't be shown to be a fund"
    )


def test_every_offender_is_named_in_one_refusal(client, db, ids):
    bad = _promote(
        client,
        [
            _row("URA", ids["URA"], None),
            _row("QZRB", ids["QZRB"], "ETFs"),
            _row("LIT", ids["LIT"], "ETFs"),  # fine
        ],
    )
    assert bad.status_code == 422
    detail = bad.json()["detail"]
    assert "URA is a fund but is in no link" in detail
    assert "QZRB is not a fund but is in ETFs" in detail
    assert "LIT" not in detail
    assert detail.endswith("Move each.")


def test_silent_or_voting_never_both_covers_etfs(client, db, ids):
    bad = _promote(client, [_row("URA", ids["URA"], "ETFs"), _row("URA", ids["URA"], "Reactors")])
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("URA is in ETFs and also in another link")
    assert "never both" in bad.json()["detail"]


def test_a_fund_listed_twice_in_etfs_is_refused(client, db, ids):
    bad = _promote(client, [_row("URA", ids["URA"], "ETFs"), _row("URA", ids["URA"], "ETFs")])
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("URA is listed more than once in ETFs")


def test_the_wipe_trap_a_resend_of_the_stored_basket_keeps_the_etfs_rows_and_their_role(
    client, db, ids
):
    """The editor's narrative edit re-sends the STORED basket: the ETFs row and its context role survive, and
    COUNT THE TABLE — no row appears or vanishes, and an unchanged roster appends no snapshot."""
    tid = _saved(client, db, ids)
    before = (_count(db, tid), _count(db, tid, "basket_snapshot"))
    stored = client.get(f"/theses/{tid}").json()
    again = client.post(
        "/workbench/theses",
        json={
            "id": tid,
            "name": stored["name"],
            "narrative": "an edited narrative",
            "ticker": stored["ticker"],
            "segments": stored["segments"],
            "basket": stored["basket"],
        },
    )
    assert again.status_code == 200, again.text
    assert (_count(db, tid), _count(db, tid, "basket_snapshot")) == before
    got = {m["ticker"]: m for m in client.get(f"/theses/{tid}").json()["basket"]}
    assert got["URA"]["segment"] == "ETFs" and got["URA"]["context_role"] == "benchmark"


def test_removing_a_fund_from_the_card_is_an_ordinary_promote(client, db, ids):
    """Reversible (WB#1): the inverse of adding a fund is the same full-replace promote without it."""
    tid = _saved(client, db, ids)
    r = _promote(client, [_row("QZRA", ids["QZRA"], "Reactors")], tid=tid)
    assert r.status_code == 200
    assert [m["ticker"] for m in client.get(f"/theses/{tid}").json()["basket"]] == ["QZRA"]


def test_the_company_search_says_which_matches_are_funds(client, db, ids):
    """The Add-a-name typeahead carries each match's ``instrument_kind`` (the field was already on the wire,
    always "equity"), so the editor can file a picked fund into ETFs."""
    hits = {h["ticker"]: h for h in client.get("/workbench/securities", params={"q": "URA"}).json()}
    assert hits["URA"]["instrument_kind"] == "etf"
    hits = {h["ticker"]: h for h in client.get("/workbench/securities", params={"q": "QZR"}).json()}
    assert {t: h["instrument_kind"] for t, h in hits.items()} == {
        "QZRA": "equity",
        "QZRB": "equity",
    }
