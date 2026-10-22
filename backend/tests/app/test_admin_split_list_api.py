"""The Admin "Splits" panel API (split plan S1), end to end over a real DB filled by the REAL backfill
(``pipeline.backfill_splits`` on the fixture Polygon pages and filings, cache-only). Every action is
exercised WITH its inverse, and each step COUNTS THE TABLE (one version per action — nothing updated,
nothing deleted)."""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.client import EdgarClient
from pipeline import backfill_splits as bf
from repositories import split_repo
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
