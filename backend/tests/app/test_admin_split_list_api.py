"""The Admin "Splits" panel API (split plan S1), end to end over a real DB filled by the REAL backfill
(``pipeline.backfill_splits`` on the fixture Massive.com pages and filings, cache-only). Every action is
exercised WITH its inverse, and each step COUNTS THE TABLE (one version per action — nothing updated,
nothing deleted). The steward's confirm with CITED values (D3, platform fixes W2-1) runs on POM's shape, and the
tape window around a split's first session (W5-1) on GNS's.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from app.routers import admin_splits
from db.bitemporal import append_fact
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
    assert btoc["decided_by"] == "auto: SEC + Massive.com"
    assert btoc["sec"]["accession"] == "0001213900-26-108468"
    assert "Beginning with the opening of trading on October 15, 2026" in btoc["sec"]["passage"]
    assert btoc["polygon"]["split_from"] == "16" and btoc["actions"] == ["revoke"]
    _, mi = _find(body, "MI", "2026-05-18")
    assert mi["sec"]["abstain_reason"].startswith("only an authorized range")
    assert [o["source"] for o in mi["confirm_options"]] == [
        "polygon"
    ]  # the SEC side states no ratio
    assert mi["actions"] == ["confirm:polygon", "reject"]
    # why it waits: the SEC reading abstained (review_reason is for needs-review rows only)
    assert mi["review_reason"] == "extractor_abstained"
    assert btoc["review_reason"] is None
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
    assert back["decided_by"].startswith("auto: SEC + Massive.com · restored by you, ")


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
    """ENHA's card (2026-10-23). As prod holds it, Massive.com's 10-28 is the only value: one "Confirm" button,
    which would put the 10-28 bar on the wrong scale. Once the reader reads its Item 8.01 8-K, the card offers
    "Confirm SEC values" for the first session the 8-K states (10-29) beside Massive.com's, and confirming it on a
    day before 10-29 lists the split ANNOUNCED for 10-29 — one version, the table counted."""
    seed_enha(db)
    with monkeypatch.context() as m:
        m.setattr(bf, "SPLIT_8K_ITEMS", OLD_ITEMS)
        _enha_backfill(db, tmp_path, asof=date(2026, 10, 20))
    _, before = _find(client.get(BASE).json(), "ENHA", "2026-10-28")
    assert before["sec"] is None and before["actions"] == ["confirm:polygon", "reject"]
    assert before["review_reason"] == "one_source"  # only Massive.com states a ratio and a date
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
    assert card["review_reason"] == "sources_differ"  # two confirmable values
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
    # SplitList test renders exactly these. Friday 08-28's night runs to Monday 06:00 ET since F6, so the
    # vendor's Saturday 13:19 ET re-version of 08-27 (volume 497,800) counts for it — before F6 the night
    # ended at midnight and read the 23:16 ET version (497,773); 08-28's own re-version (Tuesday 00:31 ET)
    # stays out either way
    assert [(b.d, b.close, b.volume) for b in j.after] == [
        (date(2026, 8, 25), 3.65000009536743, 447300.0),
        (date(2026, 8, 26), 3.4300000667572, 210800.0),
        (date(2026, 8, 27), 3.22000002861023, 497800.0),
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


# --- D3: the steward confirms a needs-review split with CITED values (platform fixes W2-1, 2026-11-25) ----------

POM_6K = "0001213900-26-110501"
POM_SESSION = date(2026, 10, 21)
POM_CITED = "https://example.test/pom/6-k/0001213900-26-110501"
POM_NOTE = "ADR termination: each 10 ADSs became 3 ordinary shares"
TODAY = date(2026, 11, 25)


def _served(d: date) -> datetime:
    """The evening a bar version was stored (UTC)."""
    return datetime(d.year, d.month, d.day, 23, tzinfo=timezone.utc)


def _seed_pom(db, *, bars: bool = True) -> uuid.UUID:
    """POM's shape (synthetic closes). Its 6-K states a 1-for-10 consolidation of the ORDINARY shares, first session
    10-21, and the extractor reads 10 -> 1; but the ADR program ends, each 10 ADSs becoming 3 ordinary shares, so the
    ADS price factor is 10/3, which no source states. The stored tape, as FIRST served, steps x4.53 on 10-21 on
    falling volume: ln(4.53 / 3.33) = 0.31, inside ``TAPE_TOLERANCE`` (0.5); ln(10 / 4.53) = 0.79, outside. A later
    re-based version of the 10-20 bar (the vendor adjusting history) is stored too and never read. Commits.
    """
    sid = uuid.uuid4()
    rows = (
        [
            (date(2026, 10, 16), 1.04, 950_000, _served(date(2026, 10, 16))),
            (date(2026, 10, 20), 1.00, 1_000_000, _served(date(2026, 10, 20))),
            (POM_SESSION, 4.53, 300_000, _served(POM_SESSION)),
            (date(2026, 10, 20), 3.33, 300_000, _served(date(2026, 10, 23))),
        ]
        if bars
        else []
    )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "POM", date(2025, 1, 1)),
        )
        for d, close, volume, at in rows:
            cur.execute(
                "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, sid, d, close, volume, d, at),
            )
    db.commit()
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=POM_SESSION,
        values={
            "effective_date": POM_SESSION,
            "ratio_from": 10,
            "ratio_to": 1,
            "status": "needs_review",
            "value_source": "sec",
            "sec_accession": POM_6K,
            "sec_form": "6-K",
            "sec_filed": date(2026, 10, 14),
            "sec_effective_date": POM_SESSION,
            "sec_date_kind": "trading",
            "sec_ratio_from": 10,
            "sec_ratio_to": 1,
            "note": "SEC only; the tape does not confirm it.",
            "valid_from": date(2026, 10, 14),
        },
        action=split_repo.ACTION_DETECT,
        author=split_repo.AUTHOR_DETECT,
    )
    db.commit()
    return sid


def _pom_card(client) -> dict:
    return _find(client.get(BASE).json(), "POM", POM_SESSION.isoformat())[1]


def _cited(**over) -> dict:
    return {
        "ratio_from": "10",
        "ratio_to": "3",
        "first_session": POM_SESSION.isoformat(),
        "cited_url": POM_CITED,
        "note": POM_NOTE,
        **over,
    }


def _confirm_values(client, version_id: str, body: dict):
    return client.post(f"{BASE}/{version_id}/confirm-values", json=body)


def _stored(db, version_id: str) -> dict:
    db.rollback()  # a fresh snapshot: the route committed on its own connection
    with db.cursor() as cur:
        cur.execute(
            "SELECT value_source, confirmed_via, cited_url, tape_override, status, author, action "
            "FROM security_split WHERE id = %s",
            (version_id,),
        )
        return cur.fetchone()


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(admin_splits, "market_today", lambda: TODAY)


def test_the_steward_confirms_poms_cited_values_inside_the_tape_tolerance(client, db, pinned):
    """POM: 10 ADSs -> 3 shares, first session 10-21, a cited URL. The tape's x4.53 is inside the tolerance of
    10/3, so no override is needed: the split is confirmed with the steward's values (``value_source``
    ``steward``, ``confirmed_via`` ``operator``), the URL stored on the version — one version, the table counted.
    """
    _seed_pom(db)
    card = _pom_card(client)
    assert card["status"] == "needs_review" and card.get("cited_url") is None
    n = split_repo.count_rows(db)
    r = _confirm_values(client, card["version_id"], _cited())
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["status"], c["value_source"], c["confirmed_via"]) == (
        "confirmed",
        "steward",
        "operator",
    )
    assert (c["ratio_from"], c["ratio_to"], c["effective_date"], c["label"]) == (
        "10",
        "3",
        "2026-10-21",
        "3-for-10 reverse split",
    )
    assert c["cited_url"] == POM_CITED and c["tape_override"] is False and c["note"] == POM_NOTE
    assert c["decided_by"].startswith("you (cited values), ") and c["actions"] == ["revoke"]
    assert c["sec"]["accession"] == POM_6K  # the evidence the split carried rides on
    assert split_repo.count_rows(db) == n + 1
    stored = _stored(db, c["version_id"])
    assert (stored["value_source"], stored["cited_url"], stored["tape_override"]) == (
        "steward",
        POM_CITED,
        False,
    )
    assert (stored["author"], stored["action"]) == ("operator", "confirm")
    bucket, listed = _find(client.get(BASE).json(), "POM", "2026-10-21")
    assert bucket == "confirmed" and listed["cited_url"] == POM_CITED
    assert client.get("/admin/status").json()["splits_needs_review"] == 0


def test_cited_values_outside_the_tape_tolerance_need_the_override(client, db, pinned):
    """10 -> 1 (x10) against the tape's x4.53 (ln 2.21 = 0.79 > 0.5): refused (409, nothing written) — the server
    recomputes the step, whatever the form showed — unless the steward overrides; the override is stored.
    """
    _seed_pom(db)
    card = _pom_card(client)
    n = split_repo.count_rows(db)
    r = _confirm_values(client, card["version_id"], _cited(ratio_to="1"))
    assert r.status_code == 409 and "x4.53" in r.json()["detail"], r.text
    assert split_repo.count_rows(db) == n
    ok = _confirm_values(client, card["version_id"], _cited(ratio_to="1", override_tape=True))
    assert ok.status_code == 200, ok.text
    c = ok.json()
    assert (c["status"], c["value_source"], c["tape_override"], c["price_factor"]) == (
        "confirmed",
        "steward",
        True,
        "10",
    )
    assert _stored(db, c["version_id"])["tape_override"] is True
    assert split_repo.count_rows(db) == n + 1


def test_no_stored_bar_on_the_first_session_needs_the_override(client, db, pinned):
    _seed_pom(db, bars=False)
    card = _pom_card(client)
    r = _confirm_values(client, card["version_id"], _cited())
    assert r.status_code == 409 and "no stored bar on 2026-10-21" in r.json()["detail"], r.text
    ok = _confirm_values(client, card["version_id"], _cited(override_tape=True))
    assert ok.status_code == 200, ok.text
    assert ok.json()["tape_override"] is True


def test_a_stale_card_or_a_settled_split_is_refused(client, db, pinned):
    _seed_pom(db)
    card = _pom_card(client)
    c = _confirm_values(client, card["version_id"], _cited()).json()
    n = split_repo.count_rows(db)
    stale = _confirm_values(client, card["version_id"], _cited())
    assert stale.status_code == 409 and "changed since you looked" in stale.json()["detail"]
    settled = _confirm_values(client, c["version_id"], _cited())
    assert (
        settled.status_code == 409 and "only a split that needs review" in settled.json()["detail"]
    )
    assert _confirm_values(client, str(uuid.uuid4()), _cited()).status_code == 404
    assert split_repo.count_rows(db) == n  # a refusal writes nothing


def test_revoke_then_restore_keeps_the_cited_values(client, db, pinned):
    _seed_pom(db)
    card = _pom_card(client)
    n = split_repo.count_rows(db)
    c = _confirm_values(client, card["version_id"], _cited()).json()
    rv = client.post(f"{BASE}/{c['version_id']}/revoke").json()
    assert (rv["status"], rv["restore_to"], rv["value_source"], rv["cited_url"]) == (
        "revoked",
        "confirmed",
        "steward",
        POM_CITED,
    )
    back = client.post(f"{BASE}/{rv['version_id']}/restore").json()
    assert (back["status"], back["value_source"], back["ratio_to"], back["cited_url"]) == (
        "confirmed",
        "steward",
        "3",
        POM_CITED,
    )
    assert back["decided_by"].startswith("restored by you, ")
    assert split_repo.count_rows(db) == n + 3


def test_the_request_is_bounded_and_the_citation_is_required(client, db, pinned):
    _seed_pom(db)
    card = _pom_card(client)
    n = split_repo.count_rows(db)
    no_url = {k: v for k, v in _cited().items() if k != "cited_url"}
    for bad in (
        no_url,
        _cited(cited_url="http://example.test/pom"),  # https only
        _cited(cited_url="https://user:pw@example.test/pom"),
        _cited(cited_url="https://"),
        _cited(cited_url="https://example.test/" + "x" * 2_000),
        _cited(ratio_from="0"),
        _cited(ratio_to="-3"),
        _cited(ratio_from="100001"),
        _cited(ratio_from="1e3"),
        _cited(ratio_to="10"),  # 10 -> 10 is not a split
        _cited(note="x" * 501),
        _cited(first_session="1980-01-01"),
    ):
        assert _confirm_values(client, card["version_id"], bad).status_code == 422, bad
    assert split_repo.count_rows(db) == n


def test_the_tape_step_reads_the_first_served_bars_beside_the_entered_factor(client, db):
    """What the form shows beside the entered factor: the stored step at that session (each bar as first served —
    the re-based 3.33 is never read), the volume ratio and whether the factor is inside the tolerance. A read.
    """
    _seed_pom(db)
    card = _pom_card(client)
    url = f"{BASE}/{card['version_id']}/tape-step"
    n = split_repo.count_rows(db)
    q = {"first_session": "2026-10-21", "ratio_from": "10", "ratio_to": "3"}
    r = client.get(url, params=q)
    assert r.status_code == 200, r.text
    t = r.json()
    assert (t["first_session"], t["prior_date"], t["prior_close"], t["close"]) == (
        "2026-10-21",
        "2026-10-20",
        1.0,
        4.53,
    )
    assert t["step"] == pytest.approx(4.53) and t["volume_ratio"] == pytest.approx(0.3)
    assert t["price_factor"] == pytest.approx(10 / 3) and t["tolerance"] == 0.5
    assert t["step_low"] < 4.53 < t["step_high"]
    assert t["within_tolerance"] is True and t["reason"] is None
    far = client.get(url, params={**q, "ratio_to": "1"}).json()
    assert far["within_tolerance"] is False and "x4.53" in far["reason"]
    bare = client.get(url, params={"first_session": "2026-10-21"}).json()
    assert bare["step"] == pytest.approx(4.53) and bare["within_tolerance"] is None
    assert bare["price_factor"] is None
    gap = client.get(url, params={**q, "first_session": "2026-10-22"}).json()
    assert gap["step"] is None and gap["within_tolerance"] is False
    assert "no stored bar on 2026-10-22" in gap["reason"]
    missing = client.get(f"{BASE}/{uuid.uuid4()}/tape-step", params=q)
    assert missing.status_code == 404
    half = client.get(url, params={"first_session": "2026-10-21", "ratio_from": "10"})
    assert half.status_code == 422  # both sides of the ratio, or neither
    assert client.get(url, params={**q, "ratio_from": "0"}).status_code == 422
    assert client.get(url, params={**q, "first_session": "1980-01-01"}).status_code == 422
    assert split_repo.count_rows(db) == n


# --- the tape window (W5-1): GNS's shape -----------------------------------------------------------------------

GNS_SESSION = date(2026, 11, 20)  # a Friday; 11-21 / 11-22 are the weekend
GNS_HALT = (
    date(2026, 11, 12),
    date(2026, 11, 13),
    date(2026, 11, 16),
    date(2026, 11, 17),
    date(2026, 11, 18),
)


def _seed_gns(db) -> uuid.UUID:
    """GNS's shape (synthetic closes): a 1-for-10 reverse split, first session 11-20. The bars before it were first
    served on the old scale (the halt's 11-12..11-18 at 0.101 on no volume) and restated x10 after the split (a
    second version stored 11-24); the 11-19 bar was first served already on the new scale (the vendor adjusted a
    night early); the first session's bar arrived late (stored 11-23). Returns the split's version id. Commits.
    """
    sid = uuid.uuid4()
    restated = _served(date(2026, 11, 24))
    rows = [
        (date(2026, 11, 10), 0.12, 400_000, _served(date(2026, 11, 10))),
        (date(2026, 11, 10), 1.2, 40_000, restated),
        (date(2026, 11, 11), 0.11, 350_000, _served(date(2026, 11, 11))),
        (date(2026, 11, 11), 1.1, 35_000, restated),
        *[(d, 0.101, 0, _served(d)) for d in GNS_HALT],
        *[(d, 1.01, 0, restated) for d in GNS_HALT],
        (date(2026, 11, 19), 1.014, 0, _served(date(2026, 11, 19))),
        (GNS_SESSION, 0.499, 2_000_000, _served(date(2026, 11, 23))),
        (date(2026, 11, 23), 0.45, 900_000, _served(date(2026, 11, 23))),
        (date(2026, 11, 24), 0.41, 800_000, _served(date(2026, 11, 24))),
    ]
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "GNS", date(2025, 1, 1)),
        )
        for d, close, volume, at in rows:
            cur.execute(
                "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, sid, d, close, volume, d, at),
            )
    db.commit()
    seeded = split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=GNS_SESSION,
        values={
            "effective_date": GNS_SESSION,
            "ratio_from": 10,
            "ratio_to": 1,
            "status": "needs_review",
            "value_source": "polygon",
            "polygon_split_from": 10,
            "polygon_split_to": 1,
            "polygon_execution_date": GNS_SESSION,
            "valid_from": GNS_SESSION,
        },
        action=split_repo.ACTION_DETECT,
        author=split_repo.AUTHOR_DETECT,
    )
    db.commit()
    return seeded.id


def test_the_tape_window_shows_both_closes_either_side_of_the_first_session(client, db):
    """The stored sessions around the split's first session (its ``key_date`` by default), each bar as first
    served beside its newest version: the halt's bars show 0.101 as served and 1.01 restated, the 11-19 bar one
    version already on the new scale, so the step at 11-20 as first served is x0.49, not the split's x10. A read.
    """
    version_id = _seed_gns(db)
    n = split_repo.count_rows(db)
    r = client.get(f"{BASE}/{version_id}/tape")
    assert r.status_code == 200, r.text
    w = r.json()
    assert (w["version_id"], w["around"], w["sessions"]) == (str(version_id), "2026-11-20", 5)
    bars = {b["d"]: b for b in w["bars"]}
    assert list(bars) == [
        "2026-11-13",
        "2026-11-16",
        "2026-11-17",
        "2026-11-18",
        "2026-11-19",
        "2026-11-20",
        "2026-11-23",
        "2026-11-24",
    ]  # five stored sessions before it, then it and the two after (the tape ends there)
    halt = bars["2026-11-18"]
    assert (halt["close"], halt["volume"], halt["newest_close"], halt["restated"]) == (
        0.101,
        0,
        1.01,
        True,
    )
    early = bars["2026-11-19"]
    assert (early["close"], early["newest_close"], early["restated"]) == (1.014, 1.014, False)
    first = bars["2026-11-20"]
    assert (first["close"], first["volume"], first["restated"]) == (0.499, 2_000_000, False)
    # the step at the first session, as tape_at reads it
    assert w["prior_date"] == "2026-11-19" and w["step"] == pytest.approx(0.499 / 1.014)
    assert w["volume_ratio"] is None  # no volume on the prior bar
    assert w["prior_served_before"] is True
    assert split_repo.count_rows(db) == n


def test_the_tape_window_clips_at_the_tapes_ends_and_is_bounded(client, db):
    version_id = _seed_gns(db)
    url = f"{BASE}/{version_id}/tape"
    start = client.get(url, params={"around": "2026-11-11", "sessions": 3}).json()
    assert [b["d"] for b in start["bars"]] == [
        "2026-11-10",
        "2026-11-11",
        "2026-11-12",
        "2026-11-13",
    ]
    assert start["around"] == "2026-11-11" and start["step"] == pytest.approx(0.11 / 0.12)
    assert start["bars"][0]["newest_close"] == 1.2 and start["bars"][0]["restated"] is True
    one = client.get(url, params={"sessions": 1}).json()
    assert [b["d"] for b in one["bars"]] == ["2026-11-19", "2026-11-20"]
    every = client.get(url, params={"sessions": 10}).json()
    assert len(every["bars"]) == 11  # every stored session: eight before it, it and two after
    weekend = client.get(
        url, params={"around": "2026-11-21"}
    ).json()  # no bar on a Saturday: no step
    assert [b["d"] for b in weekend["bars"]][-3:] == ["2026-11-20", "2026-11-23", "2026-11-24"]
    assert weekend["step"] is None and weekend["prior_date"] is None
    for bad in (
        {"sessions": 0},
        {"sessions": 11},
        {"around": "1980-01-01"},
        {"around": "x"},
    ):
        assert client.get(url, params=bad).status_code == 422, bad
    assert client.get(f"{BASE}/{uuid.uuid4()}/tape").status_code == 404


# --- the panel's hints (W2-8): the first stored bar and the share count a confirm rescales: TREO's shape ---------

TREO_SESSION = date(2026, 8, 4)  # its split's first session, and its first stored bar
TREO_SHARES = 13_524_682
TREO_SHARES_DATE = date(2026, 7, 31)  # the cover's as-of: before the first session


def _shares(db, sid: uuid.UUID, count: int, day: date) -> None:
    """One platform share count (the Workbench's, the market cap's), stored by the machine a while back."""
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "shares": count,
            "source": "cover",
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{sid}-10q.htm",
            "valid_from": day,
            "ratified_by": "auto",
            "recorded_at": _served(day),
        },
    )


def _seed_treo(db) -> uuid.UUID:
    """TREO's shape (synthetic closes): a 1-for-4 reverse split whose first session is the company's FIRST stored
    bar (nothing before it on the tape to adjust), and a platform share count dated 07-31, before that session, so
    confirming the split divides it by 4. Commits."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "TREO", date(2025, 1, 1)),
        )
        for d, close in ((TREO_SESSION, 12.4), (date(2026, 8, 5), 11.9), (date(2026, 8, 6), 12.1)):
            cur.execute(
                "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, sid, d, close, 500_000, d, _served(d)),
            )
    _shares(db, sid, TREO_SHARES, TREO_SHARES_DATE)
    db.commit()
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=TREO_SESSION,
        values={
            "effective_date": TREO_SESSION,
            "ratio_from": 4,
            "ratio_to": 1,
            "status": "needs_review",
            "value_source": "polygon",
            "polygon_split_from": 4,
            "polygon_split_to": 1,
            "polygon_execution_date": TREO_SESSION,
            "valid_from": TREO_SESSION,
        },
        action=split_repo.ACTION_DETECT,
        author=split_repo.AUTHOR_DETECT,
    )
    db.commit()
    return sid


def test_a_split_on_the_first_bar_says_so_and_names_the_share_count_a_confirm_rescales(
    client, db, pinned
):
    """TREO: its split's first session is its first stored bar (a confirm adjusts nothing on the tape), and the
    market cap's share count is dated before it (a confirm divides it by 4). POM, beside it: bars before its first
    session and a share count dated after it (already on the new scale: nothing to rescale). Display only: the
    confirm is unchanged, and a settled split carries neither hint.
    """
    _seed_treo(db)
    pom = _seed_pom(db)
    _shares(db, pom, 2_000_000, date(2026, 10, 30))
    db.commit()
    body = client.get(BASE).json()
    _, treo = _find(body, "TREO", TREO_SESSION.isoformat())
    assert treo["first_bar"] == "2026-08-04" == treo["effective_date"]
    assert treo["shares_rescaled"] == {"count": TREO_SHARES, "date": "2026-07-31"}
    _, pom_card = _find(body, "POM", POM_SESSION.isoformat())
    assert pom_card["first_bar"] == "2026-10-16" and pom_card["shares_rescaled"] is None
    n = split_repo.count_rows(db)
    r = client.post(f"{BASE}/{treo['version_id']}/confirm", json={"source": "polygon"})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["price_factor"]) == ("confirmed", "4")
    assert split_repo.count_rows(db) == n + 1  # one version, as before
    bucket, settled = _find(client.get(BASE).json(), "TREO", TREO_SESSION.isoformat())
    assert bucket == "confirmed"
    assert settled["first_bar"] is None and settled["shares_rescaled"] is None


def test_the_hints_are_read_once_for_the_whole_list(db, pinned, monkeypatch):
    """One read of the first bars and one of the share counts per list read, over every open split at once,
    never one per entry."""
    treo, pom = _seed_treo(db), _seed_pom(db)
    calls: dict[str, list[set]] = {"bars": [], "shares": []}
    first_bars, read_states = admin_splits.first_bars, admin_splits.read_states

    def bars_spy(conn, **kw):
        calls["bars"].append(set(kw["security_ids"]))
        return first_bars(conn, **kw)

    def shares_spy(conn, ids, fact_type, **kw):
        calls["shares"].append(set(ids))
        return read_states(conn, ids, fact_type, **kw)

    monkeypatch.setattr(admin_splits, "first_bars", bars_spy)
    monkeypatch.setattr(admin_splits, "read_states", shares_spy)
    out = admin_splits.splits_out(db, tenant_id=DEFAULT_TENANT_ID, asof=TODAY)
    assert calls == {"bars": [{treo, pom}], "shares": [{treo, pom}]}
    assert [e.first_bar for e in out.needs_review] == [date(2026, 10, 16), TREO_SESSION]
