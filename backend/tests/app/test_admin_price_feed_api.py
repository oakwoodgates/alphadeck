"""The Admin "Price feed repairs" surface (T2 + Q8 + T3), end to end through the API over a real DB and a
run artifact written by the REAL writer (``write_cron_run_log``) into the conftest-redirected tmp home.

The pass's inventory is the measured prod night (``tests/fixtures/prices/price_feed_repairs_2026_10.json``):
HLLK renamed with a splice-verified successor SDCO, WONDF never priced, GTLS closed. Every operator action
is exercised WITH its inverse, and each inverse restores the prior state exactly (counting the tables).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.routers import admin_price_feed
from db.session import DEFAULT_TENANT_ID
from domain.feed_kinds import StaleFeedLabel
from domain.settings import Settings
from ingest.edgar.submissions import Deregistration, DeregistrationEvent
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult
from pipeline.ingest_thesis import NameResult
from pipeline.price_feed import ExpectedPriceMiss
from pipeline.rename_probe import (
    RenameProbe,
    RenameRecommendation,
    UnpricedCandidate,
    UnpricedRecommendation,
    judge,
)
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
    return {"hllk": hllk, "wondf": wondf, "gtls": gtls, "md": md, "ee": ee, "nb": nb}


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
        "listing_class": None,  # W4-1: the class mark rides a never-priced name's candidates only
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
    # W4-1: EDGAR lists no other symbol for WONDF, so the probe never looked at it
    assert (u["probed"], u["candidates"], u["recommended"], u["member_class"]) == (
        False,
        [],
        None,
        None,
    )
    assert u["deregistered"] is None  # W4-2: no Form 15 rode WONDF's row
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


def test_a_renamed_row_CARRIED_across_a_catch_up_keeps_its_probe_and_stays_adoptable(client, db):
    """The panel reads the per-thesis carry-forward (``inventory_doc``): a later catch-up that SKIPPED
    Modern Defense (HLLK's thesis — its on-time row stood) probed nothing for HLLK, so HLLK's row is
    carried from the full night together with the probe of the pass that saw it — still recommended,
    still adoptable — while the panel reads as of the catch-up. Nuclear Buildout was skipped too (GTLS stays
    closed, carried); The Everything Exchange was covered and found nothing stale, so WONDF leaves.
    """
    ids = _night(db)
    at = datetime(2026, 10, 18, 13, 0, tzinfo=timezone.utc)
    write_cron_run_log(
        [
            ThesisRunResult(
                thesis_id=ids["md"], name="Modern Defense", skipped="on-time row stood"
            ),
            ThesisRunResult(thesis_id=ids["ee"], name="The Everything Exchange", recorded=False),
            ThesisRunResult(
                thesis_id=ids["nb"], name="Nuclear Buildout", skipped="on-time row stood"
            ),
        ],
        asof=date(2026, 10, 17),
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=5),
        catch_up=True,
        tape_evaluated=True,
        tape_stale_days=5,
        rename_probe=RenameProbe(evaluated=True, rows=(), fetches=0),
    )

    p = _panel(client)

    assert p["asof"] == "2026-10-17"  # as of the catch-up
    (r,) = p["renamed"]
    assert r["ticker"] == "HLLK" and r["probed"] is True and r["recommended"] == "SDCO"
    assert p["unpriceable"] == [] and [c["ticker"] for c in p["closed"]] == ["GTLS"]
    r = client.post(f"/admin/price-feed/{ids['hllk']}/adopt", json={"symbol": "SDCO"})
    assert r.status_code == 200, r.text


def test_the_status_read_still_WRITES_NOTHING_with_the_panel(client, db):
    ids = _night(db)
    client.post(f"/admin/price-feed/{ids['wondf']}/acknowledge").raise_for_status()
    counts = {
        t: _count(db, t) for t in ("price_symbol_change", "price_feed_ack", "security_master")
    }
    for _ in range(2):
        assert client.get("/admin/status").status_code == 200
    assert {t: _count(db, t) for t in counts} == counts


# --- 0056: the KNOWN FEED LIMITATIONS ("the vendor serves no history") ---------------------------------
# SCE-PG, REAL (tests/fixtures/prices/settle_repair_2026_07.json, read-only from prod 2026-10-21): the
# versions prod stored for 2026-08-24..08-31 with prod's own stamps, and the Yahoo chart the repair CLI
# pulled that evening at the nightly range — ONE bar, the current day. The master values are prod's.

_SETTLE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "settle_repair_2026_07.json"
    ).read_text(encoding="utf-8")
)
_SCE_PG_VENDOR = parse_yahoo_chart(_SETTLE["SCE-PG"]["vendor_chart"])
_WHY = (
    "Yahoo serves no history for SCE-PG: one bar (the current day) under every range; its tape builds "
    "one bar per night and missed nights are permanent holes"
)


def _sce_pg(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                sid,
                DEFAULT_TENANT_ID,
                "SCE-PG",
                "0000092103",
                "SOUTHERN CALIFORNIA EDISON Co",
                "2026-01-01",
            ),
        )
    for r in _SETTLE["SCE-PG"]["stored_prod"]:
        row = {**r, "d": date.fromisoformat(r["d"])}
        ingest_prices(db, sid, [row], recorded_at=datetime.fromisoformat(r["recorded_at"]))
    db.commit()
    return sid


def _vendor(monkeypatch, served: dict) -> _Source:
    src = _Source(served)
    monkeypatch.setattr(admin_price_feed, "_validation_source", lambda d: src)
    return src


def test_MARK_limited_history_with_ONE_vendor_fetch_then_UNDO_then_MARK_AGAIN(
    client, db, monkeypatch
):
    """The operator marks SCE-PG: ONE live vendor pull of its nightly symbol (the REAL one-bar chart) is the
    evidence, stored with the mark; the panel lists it under the known limitations — and NOTHING else on
    the panel moves (WONDF stays a loud unpriceable row, nothing is acknowledged). A double-click is one
    decision with no second fetch. Undo appends the inverse and the undone mark STAYS listed (inactive);
    marking it again is a NEW row. Count the table every step."""
    ids = _night(db)
    sce = _sce_pg(db)
    src = _vendor(monkeypatch, {"SCE-PG": _SCE_PG_VENDOR})

    r = client.post(f"/admin/price-feed/{sce}/limited-history", json={"reason": _WHY})
    assert r.status_code == 200, r.text
    mark = r.json()
    assert (mark["ticker"], mark["reason"], mark["basis"], mark["active"]) == (
        "SCE-PG",
        _WHY,
        "operator:limited-history",
        True,
    )
    assert (mark["vendor_symbol"], mark["vendor_range"], mark["vendor_bars"]) == ("SCE-PG", "2y", 1)
    assert mark["vendor_last_bar"] == _SCE_PG_VENDOR[0]["d"].isoformat()
    assert mark["tape_stopped"] is False and mark["revoked_at"] is None
    assert src.calls == [("SCE-PG", True, True)]  # ONE live, force-refreshed fetch
    assert _count(db, "price_feed_ack") == 1
    p = _panel(client)
    assert [(x["id"], x["active"]) for x in p["limitations"]] == [(mark["id"], True)]
    assert p["acknowledged"] == [] and [u["ticker"] for u in p["unpriceable"]] == ["WONDF"]

    again = client.post(f"/admin/price-feed/{sce}/limited-history", json={"reason": "again"})
    assert again.status_code == 200 and again.json()["id"] == mark["id"]
    assert len(src.calls) == 1 and _count(db, "price_feed_ack") == 1

    u = client.post(f"/admin/price-feed/limitations/{mark['id']}/revoke")
    assert u.status_code == 200, u.text
    assert u.json()["id"] == mark["id"] and u.json()["active"] is False
    assert u.json()["revoked_at"] is not None
    assert _count(db, "price_feed_ack") == 2  # the mark AND its revoke — nothing deleted
    assert [(x["id"], x["active"]) for x in _panel(client)["limitations"]] == [(mark["id"], False)]
    assert client.post(f"/admin/price-feed/limitations/{mark['id']}/revoke").status_code == 404

    re = client.post(f"/admin/price-feed/{sce}/limited-history", json={"reason": _WHY})
    assert re.status_code == 200 and re.json()["id"] != mark["id"] and re.json()["active"] is True
    assert _count(db, "price_feed_ack") == 3
    assert [(x["id"], x["active"]) for x in _panel(client)["limitations"]] == [
        (re.json()["id"], True)
    ]
    assert ids  # the measured night is on the page throughout


def test_a_mark_is_REFUSED_without_a_reason_on_a_feed_WITH_history_and_on_a_404(
    client, db, monkeypatch
):
    """The mark must not be a false label: a vendor that serves a real history (HLLK's successor SDCO —
    real bars) or none at all (WONDF's real 404 — that is an unpriceable feed, Acknowledge's job) is refused
    with 409; a blank reason is 422 with no fetch; an unknown security 404. Nothing is written."""
    ids = _night(db)
    src = _vendor(
        monkeypatch,
        {"SDCO": fx.probe_bars("HLLK", "SDCO"), "WONDF": fx.not_found("WONDF")},
    )
    with db.cursor() as cur:  # point HLLK's nightly symbol at SDCO, the feed WITH a history
        cur.execute(
            "UPDATE security_master SET price_symbol = 'SDCO' WHERE id = %s", (ids["hllk"],)
        )
    db.commit()

    for body in ({"reason": "   "}, {"reason": ""}, {}):
        r = client.post(f"/admin/price-feed/{ids['wondf']}/limited-history", json=body)
        assert r.status_code == 422, (body, r.text)
    assert src.calls == []

    r = client.post(f"/admin/price-feed/{ids['hllk']}/limited-history", json={"reason": "x"})
    assert r.status_code == 409 and "it has a history" in r.json()["detail"]
    r = client.post(f"/admin/price-feed/{ids['wondf']}/limited-history", json={"reason": "x"})
    assert r.status_code == 409 and "returned no bars" in r.json()["detail"]
    r = client.post(f"/admin/price-feed/{uuid.uuid4()}/limited-history", json={"reason": "x"})
    assert r.status_code == 404
    assert _count(db, "price_feed_ack") == 0 and _panel(client)["limitations"] == []


def test_a_mark_and_an_acknowledgment_never_revoke_as_each_other(client, db, monkeypatch):
    ids = _night(db)
    sce = _sce_pg(db)
    _vendor(monkeypatch, {"SCE-PG": _SCE_PG_VENDOR})
    mark = client.post(f"/admin/price-feed/{sce}/limited-history", json={"reason": _WHY}).json()
    ack = client.post(f"/admin/price-feed/{ids['wondf']}/acknowledge").json()

    assert client.post(f"/admin/price-feed/acks/{mark['id']}/revoke").status_code == 404
    assert client.post(f"/admin/price-feed/limitations/{ack['id']}/revoke").status_code == 404
    assert _count(db, "price_feed_ack") == 2


def test_a_marked_name_whose_tape_STOPPED_stays_a_LOUD_stale_row(client, db, monkeypatch):
    """The mark never hides a real problem: a later pass finds SCE-PG's tape STALE (its last stored bar,
    2026-08-31, is weeks behind) — it is a plain stale row in the freshness inventory (not closed, not
    acknowledged, in tonight's newly-stale list), and the limitation row itself says the tape stopped.
    """
    ids = _night(db)
    sce = _sce_pg(db)
    _vendor(monkeypatch, {"SCE-PG": _SCE_PG_VENDOR})
    client.post(
        f"/admin/price-feed/{sce}/limited-history", json={"reason": _WHY}
    ).raise_for_status()
    edge = date(2026, 8, 31)  # the newest bar in the real stored window
    at = datetime(2026, 10, 20, 2, 30, tzinfo=timezone.utc)
    write_cron_run_log(
        [
            ThesisRunResult(
                thesis_id=ids["nb"],
                name="Nuclear Buildout",
                recorded=True,
                tape_stale=(StaleTape("SCE-PG", sce, edge, edgar_tickers=()),),
            )
        ],
        asof=date(2026, 10, 19),
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        tape_evaluated=True,
        tape_stale_days=5,
        tape_stale_new=(StaleFeedLabel(kind="price", label="SCE-PG"),),
    )

    status = client.get("/admin/status").json()
    (row,) = [s for s in status["tape"]["stale"] if s["ticker"] == "SCE-PG"]
    assert row["edge"] == "2026-08-31" and row["closed_at"] is None
    assert row["acknowledged"] is False and row["unpriceable"] is False
    assert "SCE-PG" in status["tape"]["newly_stale"]
    (lim,) = status["price_repairs"]["limitations"]
    assert lim["tape_stopped"] is True and lim["tape_edge"] == "2026-08-31"


# --- W4-1: ADOPT on a NEVER-PRICED name, behind the explicit cross-class confirm ------------------------
# The shape (prod 2026-11-24, neutral labels per the fixture convention): an ADR member EDGAR files as
# "<name>/ADR", 404 at the vendor every night, while EDGAR's list for its CIK also carries the company's
# foreign ORDINARY line. Adopting the ordinary line prices the ADR card on another class (once a share count
# with a known ADS ratio exists, the cap is off by that ratio), so the API refuses it unless the request
# carries ``confirm_class`` and the setting ``price_feed_cross_class_adopt`` is "confirm". The probe result
# below is written by the REAL artifact writer; its bars are SYNTHETIC (no vendor is called).

_ADR_NAME = "SYNTHETIC METALS PLC/ADR"
_LISTED = ("TKAAY", "TKAAF", "TKAAN", "TKAAX")


def _never_priced_night(db) -> dict:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "TKAAY", "0009999901", _ADR_NAME, "2026-01-01"),
        )
    db.commit()
    thesis = uuid.uuid4()
    rec = UnpricedRecommendation(
        security_id=sid,
        ticker="TKAAY",
        edgar_tickers=_LISTED,
        edgar_name=_ADR_NAME,
        member_class="adr",
        candidates=(
            UnpricedCandidate(
                "TKAAF", bars=480, last_bar=date(2026, 10, 15), listing_class="ordinary"
            ),
            UnpricedCandidate("TKAAN", bars=30, last_bar=date(2026, 10, 15)),
            UnpricedCandidate("TKAAX", error="HTTPStatusError: Client error '404 Not Found'"),
        ),
    )
    at = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)
    write_cron_run_log(
        [
            ThesisRunResult(
                thesis_id=thesis,
                name="Synthetic Minerals",
                recorded=True,
                tape_stale=(StaleTape("TKAAY", sid, None, edgar_tickers=_LISTED),),
            )
        ],
        asof=_OCT16,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        tape_evaluated=True,
        tape_stale_days=5,
        rename_probe=RenameProbe(evaluated=True, unpriced=(rec,), fetches=3),
    )
    return {"sid": sid, "thesis": thesis}


def _detail(db, change_id) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT detail FROM price_symbol_change WHERE id = %s", (change_id,))
        return cur.fetchone()["detail"]


def test_the_panel_shows_a_never_priced_names_probed_candidates_with_their_class_marks(client, db):
    ids = _never_priced_night(db)

    (u,) = _panel(client)["unpriceable"]

    assert u["security_id"] == str(ids["sid"]) and u["ticker"] == "TKAAY"
    assert u["probed"] is True and u["member_class"] == "adr"
    assert u["recommended"] is None  # the ordinary line is never recommended for an ADR member
    assert u["edgar_tickers"] == list(_LISTED)
    assert u["candidates"][0] == {
        "symbol": "TKAAF",
        "verified": False,
        "overlap": 0,
        "ratio": None,
        "bars": 480,
        "last_bar": "2026-10-15",
        "error": None,
        "listing_class": "ordinary",
    }
    assert [c["symbol"] for c in u["candidates"]] == ["TKAAF", "TKAAN", "TKAAX"]


def test_a_CROSS_CLASS_adopt_is_refused_without_confirm_class_and_applied_with_it_then_UNDONE_exactly(
    client, db
):
    """The ordinary line for an ADR member: 409 without ``confirm_class`` (nothing written); with it, the
    existing Adopt writer sets the price symbol (basis operator:adopt) WITHOUT renaming the member (it was
    never renamed), the change records the probe, both class marks and the confirm; Undo restores the
    master exactly."""
    ids = _never_priced_night(db)
    sid = ids["sid"]
    before = _master(db, sid)

    r = client.post(f"/admin/price-feed/{sid}/adopt", json={"symbol": "tkaaf"})
    assert r.status_code == 409, r.text
    why = r.json()["detail"]
    assert "ordinary" in why and "ADR" in why and "confirm_class" in why
    assert _count(db, "price_symbol_change") == 0 and _master(db, sid) == before
    r = client.post(
        f"/admin/price-feed/{sid}/adopt", json={"symbol": "TKAAF", "confirm_class": False}
    )
    assert r.status_code == 409 and _count(db, "price_symbol_change") == 0

    ok = client.post(
        f"/admin/price-feed/{sid}/adopt", json={"symbol": "TKAAF", "confirm_class": True}
    )
    assert ok.status_code == 200, ok.text
    change = ok.json()
    assert change["action"] == "adopt" and change["basis"] == "operator:adopt"
    assert (change["prior_symbol"], change["new_symbol"]) == (None, "TKAAF")
    assert change["name_changed"] is False
    assert _master(db, sid) == {
        "name": _ADR_NAME,
        "price_symbol": "TKAAF",
        "price_symbol_basis": "operator:adopt",
    }
    detail = _detail(db, change["id"])
    assert detail["member_class"] == "adr" and detail["listing_class"] == "ordinary"
    assert detail["confirm_class"] is True and detail["cross_class"] is True
    assert detail["probe"]["symbol"] == "TKAAF" and detail["probe"]["bars"] == 480
    assert detail["edgar_tickers"] == list(_LISTED) and detail["pass_asof"] == "2026-10-16"
    row = _panel(client)["unpriceable"][0]
    assert row["change"]["id"] == change["id"] and row["change"]["undoable"] is True

    u = client.post(f"/admin/price-feed/changes/{change['id']}/undo")
    assert u.status_code == 200, u.text
    assert _master(db, sid) == before  # EXACT
    assert _count(db, "price_symbol_change") == 2


def test_the_REFUSE_setting_turns_the_cross_class_adopt_off_even_with_the_confirm(
    client, db, monkeypatch
):
    ids = _never_priced_night(db)
    monkeypatch.setattr(
        admin_price_feed,
        "get_settings",
        lambda: Settings(price_feed_cross_class_adopt="refuse"),
    )
    r = client.post(
        f"/admin/price-feed/{ids['sid']}/adopt", json={"symbol": "TKAAF", "confirm_class": True}
    )
    assert r.status_code == 409 and "price_feed_cross_class_adopt" in r.json()["detail"]
    assert _count(db, "price_symbol_change") == 0
    # a candidate whose class is not known to differ is still adoptable under "refuse"
    ok = client.post(f"/admin/price-feed/{ids['sid']}/adopt", json={"symbol": "TKAAN"})
    assert ok.status_code == 200, ok.text


def test_a_never_priced_adopt_needs_an_EDGAR_listed_symbol_the_probe_served_bars_for(client, db):
    """Adopt is the probe's ratification, never a typed symbol: a symbol EDGAR does not list, our own
    404ing ticker (listed, never probed) and a candidate whose fetch failed are each 409 pointing at Set
    price symbol. A candidate whose class is not known to differ needs no confirm."""
    ids = _never_priced_night(db)
    sid = ids["sid"]
    for symbol, needle in (
        ("AAPL", "not a symbol EDGAR lists"),
        ("TKAAY", "Set price symbol"),
        ("TKAAX", "Set price symbol"),
    ):
        r = client.post(
            f"/admin/price-feed/{sid}/adopt", json={"symbol": symbol, "confirm_class": True}
        )
        assert r.status_code == 409 and needle in r.json()["detail"], (symbol, r.text)
    assert _count(db, "price_symbol_change") == 0

    ok = client.post(f"/admin/price-feed/{sid}/adopt", json={"symbol": "TKAAN"})
    assert ok.status_code == 200, ok.text
    detail = _detail(db, ok.json()["id"])
    assert detail["cross_class"] is False and detail["confirm_class"] is False
    assert detail["listing_class"] is None and detail["member_class"] == "adr"


def test_the_RENAMED_adopt_is_unchanged_by_the_class_confirm(client, db):
    """The renamed path reads no class mark: SDCO adopts with or without ``confirm_class`` exactly as before
    (the display name refreshed from EDGAR's current entity name)."""
    ids = _night(db)
    r = client.post(
        f"/admin/price-feed/{ids['hllk']}/adopt", json={"symbol": "SDCO", "confirm_class": True}
    )
    assert r.status_code == 200, r.text
    assert _master(db, ids["hllk"])["name"] == "SDR Drone, Inc."
    assert "member_class" not in _detail(db, r.json()["id"])


# --- W4-2: a deregistered never-priced name carries the Form 15 and the 8-K events before it ---------------

_DEREG = Deregistration(
    form="15-12G",
    filed=date(2026, 5, 20),
    accession="0009999831-26-000004",
    primary_doc="f15.htm",
    cik="0009999831",
    events=(
        DeregistrationEvent(
            form="8-K15D5",
            filed=date(2026, 5, 20),
            accession="0009999831-26-000003",
            items=("1.03", "3.03", "5.01"),
        ),
        DeregistrationEvent(
            form="8-K", filed=date(2026, 5, 7), accession="0009999831-26-000002", items=("1.03",)
        ),
    ),
)


def _deregistered_night(db) -> tuple[uuid.UUID, Path]:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "TKBK", "0009999831", "SYNTHETIC CARE INC", "2026-01-01"),
        )
    db.commit()
    at = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)
    path = write_cron_run_log(
        [
            ThesisRunResult(
                thesis_id=uuid.uuid4(),
                name="Synthetic Care",
                recorded=True,
                tape_stale=(StaleTape("TKBK", sid, None, edgar_tickers=(), deregistered=_DEREG),),
            )
        ],
        asof=_OCT16,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        tape_evaluated=True,
        tape_stale_days=5,
        rename_probe=RenameProbe(evaluated=True),
    )
    return sid, path


def test_a_deregistered_never_priced_row_carries_the_form_15_and_the_events_with_SEC_item_titles(
    client, db
):
    """The row stays in the loud unpriceable list (remove is the recommended action, the steward's), and
    carries the Form 15 (form, date, accession, its EDGAR index link) and each 8-K event with SEC's titles
    for its items and its own link."""
    sid, _ = _deregistered_night(db)

    (u,) = _panel(client)["unpriceable"]

    assert u["security_id"] == str(sid) and u["edgar_tickers"] == []
    d = u["deregistered"]
    assert (d["form"], d["filed"], d["accession"]) == (
        "15-12G",
        "2026-05-20",
        "0009999831-26-000004",
    )
    assert d["filing_url"] == "https://www.sec.gov/Archives/edgar/data/9999831/000999983126000004/"
    first, second = d["events"]
    assert first == {
        "form": "8-K15D5",
        "filed": "2026-05-20",
        "accession": "0009999831-26-000003",
        "items": ["1.03", "3.03", "5.01"],
        "item_titles": [
            "Bankruptcy or Receivership",
            "Material Modification to Rights of Security Holders",
            "Changes in Control of Registrant",
        ],
        "filing_url": "https://www.sec.gov/Archives/edgar/data/9999831/000999983126000003/",
    }
    assert second["items"] == ["1.03"] and second["item_titles"] == ["Bankruptcy or Receivership"]


def test_the_item_words_come_from_SECs_titles_only_never_from_the_artifact(client, db):
    """An artifact edited to carry free text (a title, an unknown code, a bad accession) shows only the
    known codes with SEC's titles, and no link for an accession that is not one."""
    sid, path = _deregistered_night(db)
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    (row,) = doc["theses"][0]["tape_stale"]
    row["deregistered"]["events"][0]["items"] = ["1.03", "equity cancelled", "9.99"]
    row["deregistered"]["events"][0]["item_titles"] = ["all shares cancelled"]
    row["deregistered"]["events"][1]["accession"] = "javascript:alert(1)"
    Path(path).write_text(json.dumps(doc), encoding="utf-8")

    (u,) = _panel(client)["unpriceable"]

    first, second = u["deregistered"]["events"]
    assert first["items"] == ["1.03"] and first["item_titles"] == ["Bankruptcy or Receivership"]
    assert second["filing_url"] is None


# --- W6-4: Massive.com's reference evidence rides the stale rows (evidence, never a class) ------------------
#
# The measured shapes (probe of 2026-11-25): INDV renamed on EDGAR (SUPN) and delisted on Massive.com 11-03; TMRC
# never priced and 404 on Massive.com; ACLEW a plain stale row that Massive.com lists as an OTC WARRANT, active.


def _massive_night(db) -> dict:
    from ingest.massive_reference import MassiveRef

    ids = {t: uuid.uuid4() for t in ("INDV", "TMRC", "ACLEW")}
    with db.cursor() as cur:
        for t, sid in ids.items():
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, t, f"SYNTHETIC {t}", "2026-01-01"),
            )
    db.commit()
    read = date(2026, 11, 25)
    rows = (
        StaleTape(
            "INDV",
            ids["INDV"],
            date(2026, 11, 2),
            renamed=True,
            edgar_tickers=("SUPN",),
            massive_ref=MassiveRef(
                "INDV", read, True, active=False, type="CS", delisted_utc=date(2026, 11, 3)
            ),
        ),
        StaleTape(
            "TMRC", ids["TMRC"], None, edgar_tickers=(), massive_ref=MassiveRef("TMRC", read, False)
        ),
        StaleTape(
            "ACLEW",
            ids["ACLEW"],
            date(2025, 10, 15),
            edgar_tickers=("ALCE", "ACLEW", "ADIS"),
            massive_ref=MassiveRef("ACLEW", read, True, active=True, type="WARRANT"),
        ),
    )
    at = datetime(2026, 11, 26, 3, 30, tzinfo=timezone.utc)
    path = write_cron_run_log(
        [ThesisRunResult(thesis_id=uuid.uuid4(), name="Thesis 01", recorded=True, tape_stale=rows)],
        asof=read,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=30),
        tape_evaluated=True,
        tape_stale_days=5,
        rename_probe=RenameProbe(evaluated=True),
    )
    return {**ids, "path": path}


def test_the_repair_rows_and_the_tape_rows_carry_massive_coms_reference_evidence(client, db):
    _massive_night(db)
    body = client.get("/admin/status").json()
    repairs = body["price_repairs"]

    (indv,) = repairs["renamed"]
    assert indv["massive_ref"] == {
        "symbol": "INDV",
        "read_on": "2026-11-25",
        "listed": True,
        "active": False,
        "type": "CS",
        "delisted_utc": "2026-11-03",
        "traded_on": None,
    }
    (tmrc,) = repairs["unpriceable"]
    assert tmrc["massive_ref"]["listed"] is False and tmrc["massive_ref"]["type"] is None
    # the plain stale row lives on the tape panel (the vendor symbol is its repair): the evidence rides there
    stale = {r["ticker"]: r for r in body["tape"]["stale"]}
    assert stale["ACLEW"]["massive_ref"]["type"] == "WARRANT"
    assert stale["ACLEW"]["massive_ref"]["active"] is True
    assert stale["INDV"]["massive_ref"]["delisted_utc"] == "2026-11-03"
    # a field, never a class: the rows sit in the lists they sit in without it
    assert stale["ACLEW"]["renamed"] is False and stale["ACLEW"]["closed_at"] is None
    assert tmrc["deregistered"] is None


def test_an_older_or_malformed_massive_block_reads_null_and_never_blanks_the_panel(client, db):
    night = _massive_night(db)
    path = Path(night["path"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    indv, tmrc, aclew = doc["theses"][0]["tape_stale"]
    del indv["massive_ref"]  # an artifact written before W6-4
    tmrc["massive_ref"] = {"symbol": "../TMRC", "listed": "no"}  # not a symbol: dropped
    aclew["massive_ref"] = "WARRANT"
    path.write_text(json.dumps(doc), encoding="utf-8")

    body = client.get("/admin/status").json()
    repairs = body["price_repairs"]
    assert repairs["renamed"][0]["massive_ref"] is None
    assert repairs["unpriceable"][0]["massive_ref"] is None
    assert all(r["massive_ref"] is None for r in body["tape"]["stale"])
    assert {r["ticker"] for r in body["tape"]["stale"]} == {"INDV", "TMRC", "ACLEW"}
