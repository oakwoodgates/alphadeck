"""The Admin "Splits" panel API (split plan S1), end to end over a real DB filled by the REAL backfill
(``pipeline.backfill_splits`` on the fixture Polygon pages and filings, cache-only). Every action is
exercised WITH its inverse, and each step COUNTS THE TABLE (one version per action — nothing updated,
nothing deleted)."""

from __future__ import annotations

import uuid
from datetime import date

from app.routers import admin_splits
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.client import EdgarClient
from pipeline import backfill_splits as bf
from repositories import split_repo
from tests.split_801_fixtures import ENHA_8K, OLD_ITEMS, enha_polygon, seed_enha
from tests.split_801_fixtures import SINCE as SINCE_8_01
from tests.split_fixtures import edgar_cache, polygon_source, seed_union

BASE = "/admin/splits"


def _fill(db, tmp_path):
    seed_union(db)
    polygon, _ = polygon_source(tmp_path)
    bf.backfill_splits(
        db,
        polygon=polygon,
        edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        since=date(2025, 8, 1),
        asof=date(2026, 10, 20),
        apply=True,
    )


def _find(body: dict, ticker: str, key_date: str) -> tuple[str, dict]:
    for bucket in ("needs_review", "announced", "confirmed", "set_aside"):
        for s in body[bucket]:
            if s["ticker"] == ticker and s["key_date"] == key_date:
                return bucket, s
    raise AssertionError(f"{ticker} {key_date} not listed")


def test_the_panel_is_loud_only_where_one_source_cannot_settle_it(client, db, tmp_path):
    _fill(db, tmp_path)
    body = client.get(BASE).json()
    assert body["total"] == 7
    assert [s["ticker"] for s in body["confirmed"]] == [
        "BTOC",
        "GGRP",
        "LRHC",
        "OPAD",
    ]  # newest first
    assert {(s["ticker"], s["key_date"]) for s in body["needs_review"]} == {
        ("MI", "2026-05-18"),
        ("LRHC", "2026-04-20"),
        ("LRHC", "2026-01-26"),
    }
    assert body["announced"] == [] and body["set_aside"] == []
    _, btoc = _find(body, "BTOC", "2026-10-15")
    assert btoc["label"] == "1-for-16 reverse split" and btoc["price_factor"] == "16"
    assert btoc["decided_by"] == "auto: SEC + Polygon"
    assert btoc["sec"]["accession"] == "0001213900-26-108468"
    assert "Beginning with the opening of trading on October 15, 2026" in btoc["sec"]["passage"]
    assert btoc["polygon"]["split_from"] == "16" and btoc["actions"] == ["revoke"]
    _, mi = _find(body, "MI", "2026-05-18")
    assert mi["sec"]["abstain_reason"].startswith("only an authorized range")
    assert [o["source"] for o in mi["confirm_options"]] == [
        "polygon"
    ]  # the SEC side states no ratio
    assert mi["actions"] == ["confirm:polygon", "reject"]
    # the Admin status carries the loud count
    assert client.get("/admin/status").json()["splits_needs_review"] == 3


def test_confirm_then_revoke_then_restore(client, db, tmp_path):
    _fill(db, tmp_path)
    _, mi = _find(client.get(BASE).json(), "MI", "2026-05-18")
    n = split_repo.count_rows(db)
    r = client.post(f"{BASE}/{mi['version_id']}/confirm", json={"source": "polygon"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["status"], c["confirmed_via"], c["value_source"]) == (
        "confirmed",
        "operator",
        "polygon",
    )
    assert c["decided_by"].startswith("you, ") and c["actions"] == ["revoke"]
    assert split_repo.count_rows(db) == n + 1
    assert client.get("/admin/status").json()["splits_needs_review"] == 2
    # the stale card (the version before the confirm) is refused, never applied
    assert client.post(f"{BASE}/{mi['version_id']}/reject").status_code == 409
    rv = client.post(f"{BASE}/{c['version_id']}/revoke").json()
    assert (
        rv["status"] == "revoked"
        and rv["restore_to"] == "confirmed"
        and rv["actions"] == ["restore"]
    )
    bucket, listed = _find(client.get(BASE).json(), "MI", "2026-05-18")
    assert bucket == "set_aside" and listed["version_id"] == rv["version_id"]  # kept, not dropped
    back = client.post(f"{BASE}/{rv['version_id']}/restore").json()
    assert back["status"] == "confirmed" and back["decided_by"].startswith("restored by you, ")
    assert split_repo.count_rows(db) == n + 3


def test_reject_then_restore_returns_it_to_review(client, db, tmp_path):
    _fill(db, tmp_path)
    _, lrhc = _find(client.get(BASE).json(), "LRHC", "2026-01-26")
    rj = client.post(f"{BASE}/{lrhc['version_id']}/reject").json()
    assert rj["status"] == "rejected" and rj["restore_to"] == "needs_review"
    assert client.get("/admin/status").json()["splits_needs_review"] == 2
    back = client.post(f"{BASE}/{rj['version_id']}/restore").json()
    assert back["status"] == "needs_review"
    assert client.get("/admin/status").json()["splits_needs_review"] == 3


def test_an_auto_confirmed_split_can_be_revoked_and_restored(client, db, tmp_path):
    _fill(db, tmp_path)
    _, btoc = _find(client.get(BASE).json(), "BTOC", "2026-10-15")
    rv = client.post(f"{BASE}/{btoc['version_id']}/revoke").json()
    assert rv["status"] == "revoked" and rv["confirmed_via"] == "auto:sec+polygon"
    back = client.post(f"{BASE}/{rv['version_id']}/restore").json()
    assert back["status"] == "confirmed" and back["confirmed_via"] == "auto:sec+polygon"
    assert back["decided_by"].startswith("auto: SEC + Polygon · restored by you, ")


def test_refusals(client, db, tmp_path):
    _fill(db, tmp_path)
    body = client.get(BASE).json()
    _, btoc = _find(body, "BTOC", "2026-10-15")
    _, mi = _find(body, "MI", "2026-05-18")
    n = split_repo.count_rows(db)
    assert client.post(f"{BASE}/{uuid.uuid4()}/reject").status_code == 404
    assert (
        client.post(f"{BASE}/{btoc['version_id']}/reject").status_code == 409
    )  # confirmed: revoke it
    r = client.post(f"{BASE}/{mi['version_id']}/confirm", json={"source": "sec"})
    assert r.status_code == 409 and "SEC states no complete ratio and date" in r.json()["detail"]
    assert (
        client.post(f"{BASE}/{mi['version_id']}/confirm", json={"source": "x"}).status_code == 422
    )
    assert client.post(f"{BASE}/{mi['version_id']}/restore").status_code == 409
    assert split_repo.count_rows(db) == n  # a refusal writes nothing


def _enha_backfill(db, tmp_path, *, asof: date) -> None:
    bf.backfill_splits(
        db,
        polygon=enha_polygon(tmp_path, today=asof),
        edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        since=SINCE_8_01,
        asof=asof,
        apply=True,
        tickers=["ENHA"],
    )


def test_enha_offers_its_real_first_session_once_the_8_01_8k_is_read(
    client, db, tmp_path, monkeypatch
):
    """ENHA's card (2026-10-23). As prod holds it, Polygon's 10-28 is the only value: one "Confirm" button,
    which would put the 10-28 bar on the wrong scale. Once the reader reads its Item 8.01 8-K, the card offers
    "Confirm SEC values" for the first session the 8-K states (10-29) beside Polygon's, and confirming it on a
    day before 10-29 lists the split ANNOUNCED for 10-29 — one version, the table counted."""
    seed_enha(db)
    with monkeypatch.context() as m:
        m.setattr(bf, "SPLIT_8K_ITEMS", OLD_ITEMS)
        _enha_backfill(db, tmp_path, asof=date(2026, 10, 20))
    _, before = _find(client.get(BASE).json(), "ENHA", "2026-10-28")
    assert before["sec"] is None and before["actions"] == ["confirm:polygon", "reject"]
    _enha_backfill(db, tmp_path, asof=date(2026, 10, 23))
    bucket, card = _find(client.get(BASE).json(), "ENHA", "2026-10-28")
    assert (bucket, card["effective_date"], card["value_source"]) == (
        "needs_review",
        "2026-10-28",
        "polygon",
    )
    sec = card["sec"]
    assert (sec["accession"], sec["effective_date"], sec["date_kind"], sec["ratio_label"]) == (
        ENHA_8K,
        "2026-10-29",
        "trading",
        "1-for-10 reverse split",
    )
    assert "at the opening of trading on October 29, 2026" in sec["passage"]
    assert [(o["source"], o["label"]) for o in card["confirm_options"]] == [
        ("sec", "1-for-10 reverse split, first session 2026-10-29"),
        ("polygon", "1-for-10 reverse split, first session 2026-10-28"),
    ]
    assert card["actions"] == ["confirm:sec", "confirm:polygon", "reject"]
    monkeypatch.setattr(admin_splits, "market_today", lambda: date(2026, 10, 28))
    n = split_repo.count_rows(db)
    r = client.post(f"{BASE}/{card['version_id']}/confirm", json={"source": "sec"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["key_date"], c["effective_date"], c["status"], c["value_source"]) == (
        "2026-10-28",
        "2026-10-29",
        "announced",
        "sec",
    )
    assert split_repo.count_rows(db) == n + 1
    bucket, listed = _find(client.get(BASE).json(), "ENHA", "2026-10-28")
    assert bucket == "announced" and listed["actions"] == ["reject"]  # its inverse is beside it


def test_an_empty_list_reads_empty(client, db):
    body = client.get(BASE).json()
    assert body == {
        "needs_review": [],
        "announced": [],
        "confirmed": [],
        "set_aside": [],
        "total": 0,
        "unlisted_jumps": [],  # S5 — no basket, no jump
        "unlisted_jumps_error": None,
        "dismissed_jumps": [],
    }
    assert client.get("/admin/status").json()["splits_needs_review"] == 0
    assert split_repo.count_needs_review(db, tenant_id=DEFAULT_TENANT_ID) == 0


# --- S5: split-shaped jumps with no split listed (the split-night hold's escalation) ----------------------


def test_the_panel_lists_a_released_split_shaped_jump_no_source_listed(db):
    """GAME's real 08-24 step (its 1-for-8 NOT on the list), three sessions on: the hold has released it and
    the panel asks "is this real?" — with the step and the bars after it. Listed, it would not be here
    (``tests/signals/test_split_hold.py``)."""
    from app.routers.admin_splits import splits_out
    from tests import split_s5_fixtures as s5

    sid = s5.seed(db, "GAME", listed=False)
    s5.seed_thesis(db, [("GAME", sid)])
    out = splits_out(db, tenant_id=DEFAULT_TENANT_ID, asof=date(2026, 8, 28))
    assert out.total == 0 and out.unlisted_jumps_error is None
    (j,) = out.unlisted_jumps
    assert (j.ticker, j.prior_date, j.step_date, j.sessions_since) == (
        "GAME",
        date(2026, 8, 21),
        date(2026, 8, 24),
        4,
    )
    assert j.label == "GAME stepped x7.5 on 08-24" and j.step > 3 and j.volume < j.prior_volume
    assert (j.prior_close, j.prior_volume, j.close, j.volume) == (
        0.421999990940094,
        1486400.0,
        3.16499996185303,
        553700.0,
    )
    # what a history view on 08-28 read next (each bar's newest version public by then) — the frontend's
    # SplitList test renders exactly these
    assert [(b.d, b.close, b.volume) for b in j.after] == [
        (date(2026, 8, 25), 3.65000009536743, 447300.0),
        (date(2026, 8, 26), 3.4300000667572, 210800.0),
        (date(2026, 8, 27), 3.22000002861023, 497773.0),
        (date(2026, 8, 28), 3.0, 212900.0),
    ]


# --- the operator's Dismiss of an "is this real?" card (2026-10-24) ---------------------------------------


def _dismissals(db) -> int:
    db.rollback()  # a fresh snapshot: the route committed on its own connection
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM split_jump_dismissal")
        return cur.fetchone()["n"]


def test_dismiss_hides_the_card_behind_the_dismissed_filter_and_undo_brings_it_back(
    client, db, monkeypatch
):
    """GAME's released 08-24 jump (asof 2026-08-28): Dismiss ("this was a real move") takes it off the list and
    lists it under ``dismissed_jumps`` with its reason — never deleted (WB#2); a second Dismiss is refused (409);
    Undo puts it back on the list (WB#1); a second Undo is refused (409). Each action appends ONE row — COUNT THE
    TABLE. The nightly line leaves a dismissed jump out (counted), and the split-night hold never reads it.
    """
    from db.session import connect
    from pipeline import split_jumps as sj
    from tests import split_s5_fixtures as s5

    monkeypatch.setattr(admin_splits, "market_today", lambda: date(2026, 8, 28))
    sid = s5.seed(db, "GAME", listed=False)
    s5.seed_thesis(db, [("GAME", sid)])
    body = client.get(BASE).json()
    assert [j["ticker"] for j in body["unlisted_jumps"]] == ["GAME"] and body[
        "dismissed_jumps"
    ] == []
    assert body["unlisted_jumps"][0]["dismissal_id"] is None
    assert _dismissals(db) == 0
    r = client.post(f"{BASE}/jumps/{sid}/2026-08-24/dismiss", json={"reason": "a real squeeze"})
    assert r.status_code == 200
    gone = r.json()
    assert gone["dismissed_reason"] == "a real squeeze" and gone["dismissal_id"]
    assert _dismissals(db) == 1
    body = client.get(BASE).json()
    assert body["unlisted_jumps"] == []
    assert [(j["ticker"], j["step_date"]) for j in body["dismissed_jumps"]] == [
        ("GAME", "2026-08-24")
    ]
    again = client.post(f"{BASE}/jumps/{sid}/2026-08-24/dismiss")
    assert again.status_code == 409 and _dismissals(db) == 1
    assert (
        client.post(f"{BASE}/jumps/{sid}/2026-08-21/dismiss").status_code == 404
    )  # not a listed jump
    scan = sj.run_jump_scan(
        asof=date(2026, 8, 28), allow_live=True, tenant_id=DEFAULT_TENANT_ID, connect=connect
    )
    assert scan.evaluated and scan.jumps == () and scan.dismissed == 1
    assert sj.report_lines(scan) == []  # the operator said it was real: no nightly line
    undo = client.post(f"{BASE}/jumps/dismissals/{gone['dismissal_id']}/undo")
    assert undo.status_code == 200 and undo.json()["dismissal_id"] is None
    assert _dismissals(db) == 2
    body = client.get(BASE).json()
    assert [j["ticker"] for j in body["unlisted_jumps"]] == ["GAME"] and body[
        "dismissed_jumps"
    ] == []
    assert client.post(f"{BASE}/jumps/dismissals/{gone['dismissal_id']}/undo").status_code == 409
    assert client.post(f"{BASE}/jumps/dismissals/{uuid.uuid4()}/undo").status_code == 404
    assert _dismissals(db) == 2


def test_a_dismissal_never_reaches_the_split_night_hold():
    """STRUCTURAL: the split-night hold (the signal and its pure rules) never names the dismissal log or its
    repository — the card's Dismiss is display, never a call input."""
    import inspect

    from domain import split_hold as hold_rules
    from signals import split_hold

    for mod in (split_hold, hold_rules):
        assert "split_jump_dismissal" not in inspect.getsource(mod)
        assert "split_jump_repo" not in inspect.getsource(mod)
