"""The Admin "Price feed repairs" surface (T2 + Q8 + T3), end to end through the API over a real DB and a
run artifact written by the REAL writer (``write_cron_run_log``) into the conftest-redirected tmp home.

The pass's inventory is the measured prod night (``tests/fixtures/prices/price_feed_repairs_2026_10.json``):
HLLK renamed with a splice-verified successor SDCO, WONDF never priced, GTLS closed. Every operator action
is exercised WITH its inverse, and each inverse restores the prior state exactly (counting the tables).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.routers import admin_price_feed
from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult
from pipeline.ingest_thesis import NameResult
from pipeline.price_feed import ExpectedPriceMiss
from pipeline.rename_probe import RenameProbe, RenameRecommendation, judge
from pipeline.tape_health import StaleTape
from tests import price_feed_fixtures as fx

_OCT16 = date(2026, 10, 16)


def _night(db, *, probe_error: str | None = None):
    """Seed the three names and write the measured night's artifact. Returns their ids + the thesis ids."""
    hllk = fx.insert_security(db, "HLLK")
    wondf = fx.insert_security(db, "WONDF", with_tape=False)
    gtls = fx.insert_security(db, "GTLS")
    md, ee, nb = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    wondf_err = fx.probe_error("WONDF", "WONDF").split(": ", 1)[1]
    gtls_err = fx.probe_error("GTLS", "GTLS").split(": ", 1)[1]
    sdco = judge("SDCO", fx.probe_bars("HLLK", "SDCO"), fx.stored_closes("HLLK"))
    rec = RenameRecommendation(
        security_id=hllk,
        ticker="HLLK",
        edge=fx.last_bar("HLLK"),
        edgar_tickers=("SDCO",),
        candidates=(sdco,),
        edgar_name="SDR Drone, Inc.",
        former_names=fx.former("HLLK"),
        name_change_filings=fx.name_changes("HLLK"),
    )
    results = [
        ThesisRunResult(
            thesis_id=md,
            name="Modern Defense",
            recorded=True,
            tape_stale=(
                StaleTape("HLLK", hllk, fx.last_bar("HLLK"), renamed=True, edgar_tickers=("SDCO",)),
            ),
        ),
        ThesisRunResult(
            thesis_id=ee,
            name="The Everything Exchange",
            recorded=True,
            ingested=[
                NameResult(
                    "WONDF",
                    wondf,
                    0,
                    0,
                    f"price: {wondf_err}",
                    leg_errors=(("price", wondf_err),),
                    price_not_found=True,
                )
            ],
            tape_stale=(StaleTape("WONDF", wondf, None, edgar_tickers=()),),
        ),
        ThesisRunResult(
            thesis_id=nb,
            name="Nuclear Buildout",
            recorded=True,
            tape_stale=(
                StaleTape(
                    "GTLS", gtls, fx.last_bar("GTLS"), closed_at=date(2026, 7, 16), edgar_tickers=()
                ),
            ),
            expected_price_misses=(ExpectedPriceMiss(gtls, "GTLS", "closed", gtls_err),),
        ),
    ]
    at = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)
    write_cron_run_log(
        results,
        asof=_OCT16,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        tape_evaluated=True,
        tape_stale_days=5,
        rename_probe=RenameProbe(
            evaluated=probe_error is None,
            rows=(rec,) if probe_error is None else (),
            error=probe_error,
            fetches=1,
        ),
    )
    return {"hllk": hllk, "wondf": wondf, "gtls": gtls, "md": md, "ee": ee}


def _panel(client) -> dict:
    return client.get("/admin/status").json()["price_repairs"]


def _master(db, sid) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "SELECT name, price_symbol, price_symbol_basis FROM security_master WHERE id = %s",
            (sid,),
        )
        return dict(cur.fetchone())


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed table names
        return cur.fetchone()["n"]


def test_the_panel_shows_the_measured_night_renamed_unpriceable_and_closed(client, db):
    ids = _night(db)

    p = _panel(client)

    assert p["asof"] == "2026-10-16" and p["probe_evaluated"] is True and p["probe_error"] is None
    (r,) = p["renamed"]
    assert r["ticker"] == "HLLK" and r["edge"] == "2026-09-24" and r["recommended"] == "SDCO"
    assert r["candidates"][0] == {
        "symbol": "SDCO",
        "verified": True,
        "overlap": 30,
        "ratio": 1.0,
        "bars": len(fx.probe_bars("HLLK", "SDCO")),
        "last_bar": "2026-10-15",
        "error": None,
    }
    assert r["edgar_name"] == "SDR Drone, Inc."
    assert r["former_names"][0]["name"] == "HALLMARK VENTURE GROUP, INC."
    assert r["name_change_filings"][0]["filed"] == "2026-07-16"
    assert r["theses"] == [{"id": str(ids["md"]), "name": "Modern Defense"}]
    assert r["change"] is None and r["price_symbol"] is None
    (u,) = p["unpriceable"]
    assert u["ticker"] == "WONDF" and u["edgar_tickers"] == []
    assert u["vendor_error"].startswith("Client error '404 Not Found'")
    assert u["theses"] == [{"id": str(ids["ee"]), "name": "The Everything Exchange"}]
    (c,) = p["closed"]
    assert c["ticker"] == "GTLS" and c["closed_at"] == "2026-07-16"
    assert p["acknowledged"] == [] and p["applied"] == [] and p["expected_misses"] == 1


def test_ADOPT_then_UNDO_restores_the_master_exactly(client, db):
    """The Adopt click: SDCO set through the existing writer (basis operator:adopt) and the display name
    refreshed to EDGAR's current entity name; the panel shows it as the row's standing change with its
    Undo. The Undo click: name, price symbol and basis back byte-for-byte; the log keeps both rows.
    """
    ids = _night(db)
    before = _master(db, ids["hllk"])

    r = client.post(f"/admin/price-feed/{ids['hllk']}/adopt", json={"symbol": "sdco"})
    assert r.status_code == 200, r.text
    change = r.json()
    assert change["action"] == "adopt" and change["basis"] == "operator:adopt"
    assert (change["prior_symbol"], change["new_symbol"]) == (None, "SDCO")
    assert _master(db, ids["hllk"]) == {
        "name": "SDR Drone, Inc.",
        "price_symbol": "SDCO",
        "price_symbol_basis": "operator:adopt",
    }
    row = _panel(client)["renamed"][0]
    assert row["change"]["id"] == change["id"] and row["change"]["undoable"] is True
    assert row["price_symbol"] == "SDCO"

    u = client.post(f"/admin/price-feed/changes/{change['id']}/undo")
    assert u.status_code == 200, u.text
    assert u.json()["action"] == "undo" and u.json()["basis"] == "operator:undo"
    assert _master(db, ids["hllk"]) == before  # EXACT
    assert _count(db, "price_symbol_change") == 2
    assert _panel(client)["renamed"][0]["change"] is None
    assert client.post(f"/admin/price-feed/changes/{change['id']}/undo").status_code == 409


def test_adopt_refuses_a_symbol_EDGAR_does_not_list_and_a_name_that_is_not_renamed(client, db):
    ids = _night(db)
    r = client.post(f"/admin/price-feed/{ids['hllk']}/adopt", json={"symbol": "AAPL"})
    assert r.status_code == 409 and "not a symbol EDGAR lists" in r.json()["detail"]
    r = client.post(f"/admin/price-feed/{ids['wondf']}/adopt", json={"symbol": "WNDR.TO"})
    assert r.status_code == 409 and "Set price symbol" in r.json()["detail"]
    assert (
        client.post(f"/admin/price-feed/{uuid.uuid4()}/adopt", json={"symbol": "X"}).status_code
        == 404
    )
    assert (
        _count(db, "price_symbol_change") == 0 and _master(db, ids["hllk"])["price_symbol"] is None
    )


class _Source:
    def __init__(self, served):
        self._served = served
        self.calls: list = []

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        self.calls.append((ticker, allow_live, force_refresh))
        got = self._served[ticker]
        if isinstance(got, Exception):
            raise got
        return got


def test_SET_SYMBOL_validates_with_ONE_vendor_fetch_then_sets_and_UNDOES_exactly(
    client, db, monkeypatch
):
    """The operator types a symbol for the unpriceable WONDF. A symbol the vendor 404s (the real WONDF
    error) is refused with 422 and NOTHING written; one that returns bars is set (basis operator:set), its
    validation recorded, and Undo restores the prior state exactly."""
    ids = _night(db)
    before = _master(db, ids["wondf"])
    src = _Source({"WONDF.X": fx.not_found("WONDF.X"), "SDCO": fx.probe_bars("HLLK", "SDCO")})
    dirs: list = []

    def _factory(d):
        dirs.append(d)
        return src

    monkeypatch.setattr(admin_price_feed, "_validation_source", _factory)

    bad = client.post(f"/admin/price-feed/{ids['wondf']}/set-symbol", json={"symbol": "wondf.x"})
    assert bad.status_code == 422 and "did not validate" in bad.json()["detail"]
    assert _count(db, "price_symbol_change") == 0 and _master(db, ids["wondf"]) == before

    # (SDCO's real bars stand in for "a symbol the vendor serves" — the validation needs bars to exist)
    ok = client.post(f"/admin/price-feed/{ids['wondf']}/set-symbol", json={"symbol": "SDCO"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["action"] == "set" and ok.json()["basis"] == "operator:set"
    assert _master(db, ids["wondf"])["price_symbol"] == "SDCO"
    assert src.calls == [
        ("WONDF.X", True, True),
        ("SDCO", True, True),
    ]  # ONE live fetch per attempt
    assert all(not d.exists() for d in dirs)  # throwaway caches, gone
    assert _panel(client)["unpriceable"][0]["change"]["new_symbol"] == "SDCO"

    client.post(f"/admin/price-feed/changes/{ok.json()['id']}/undo").raise_for_status()
    assert _master(db, ids["wondf"]) == before


def test_set_symbol_refuses_a_malformed_symbol_without_a_fetch(client, db, monkeypatch):
    ids = _night(db)
    monkeypatch.setattr(
        admin_price_feed,
        "_validation_source",
        lambda d: (_ for _ in ()).throw(AssertionError("no fetch for a malformed symbol")),
    )
    r = client.post(f"/admin/price-feed/{ids['wondf']}/set-symbol", json={"symbol": "WON DF;"})
    assert r.status_code == 422


def test_ACKNOWLEDGE_moves_the_name_to_the_quiet_list_and_REVOKE_brings_it_back(client, db):
    ids = _night(db)

    a = client.post(
        f"/admin/price-feed/{ids['wondf']}/acknowledge", json={"reason": "TSX-only 40-F filer"}
    )
    assert a.status_code == 200, a.text
    ack = a.json()
    assert ack["basis"] == "operator:acknowledge" and ack["reason"] == "TSX-only 40-F filer"
    p = _panel(client)
    assert p["unpriceable"] == []
    assert [(x["ticker"], x["still_unpriceable"]) for x in p["acknowledged"]] == [("WONDF", True)]
    # a double click is ONE decision
    again = client.post(f"/admin/price-feed/{ids['wondf']}/acknowledge")
    assert again.status_code == 200 and again.json()["id"] == ack["id"]
    assert _count(db, "price_feed_ack") == 1

    rv = client.post(f"/admin/price-feed/acks/{ack['id']}/revoke")
    assert rv.status_code == 200 and rv.json()["action"] == "revoke"
    p = _panel(client)
    assert [x["ticker"] for x in p["unpriceable"]] == ["WONDF"] and p["acknowledged"] == []
    assert _count(db, "price_feed_ack") == 2
    assert client.post(f"/admin/price-feed/acks/{ack['id']}/revoke").status_code == 404


def test_only_an_UNPRICEABLE_name_can_be_acknowledged(client, db):
    ids = _night(db)
    for sid in (ids["hllk"], ids["gtls"]):
        r = client.post(f"/admin/price-feed/{sid}/acknowledge")
        assert r.status_code == 409 and "not unpriceable" in r.json()["detail"]
    assert _count(db, "price_feed_ack") == 0


def test_an_applied_repair_stays_listed_with_its_Undo_after_the_name_leaves_the_inventory(
    client, db
):
    """WB#1: once the adopted tape heals, the name leaves the stale inventory — its inverse must not vanish
    with it. A newer artifact without HLLK still lists the standing Adopt (undoable) under `applied`.
    """
    ids = _night(db)
    change = client.post(f"/admin/price-feed/{ids['hllk']}/adopt", json={"symbol": "SDCO"}).json()
    at = datetime(2026, 10, 20, 2, 30, tzinfo=timezone.utc)
    write_cron_run_log(
        [ThesisRunResult(thesis_id=uuid.uuid4(), name="Modern Defense", recorded=True)],
        asof=date(2026, 10, 19),
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        tape_evaluated=True,
    )
    p = _panel(client)
    assert p["renamed"] == []
    assert [(x["id"], x["undoable"], x["ticker"]) for x in p["applied"]] == [
        (change["id"], True, "HLLK")
    ]


def test_a_failed_probe_shows_its_error_and_the_renamed_row_stays_listed(client, db):
    ids = _night(db, probe_error="OperationalError: server closed the connection")
    p = _panel(client)
    assert p["probe_error"] == "OperationalError: server closed the connection"
    (r,) = p["renamed"]
    assert r["security_id"] == str(ids["hllk"]) and r["probed"] is False and r["candidates"] == []


def test_the_status_read_still_WRITES_NOTHING_with_the_panel(client, db):
    ids = _night(db)
    client.post(f"/admin/price-feed/{ids['wondf']}/acknowledge").raise_for_status()
    counts = {
        t: _count(db, t) for t in ("price_symbol_change", "price_feed_ack", "security_master")
    }
    for _ in range(2):
        assert client.get("/admin/status").status_code == 200
    assert {t: _count(db, t) for t in counts} == counts
