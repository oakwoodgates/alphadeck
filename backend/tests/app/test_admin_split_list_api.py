"""The Admin "Splits" panel API (split plan S1), end to end over a real DB filled by the REAL backfill
(``pipeline.backfill_splits`` on the fixture Massive.com pages and filings, cache-only). Every action is
exercised WITH its inverse, and each step COUNTS THE TABLE (one version per action — nothing updated,
nothing deleted). The steward's confirm with CITED values (D3, platform fixes W2-1) runs on POM's shape, the
tape window around a split's first session (W5-1) on GNS's, and the split filings the list could not place, with
the steward's Dismiss (framework completion F1-3), on an artifact and a record seeded in a tmp dir.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import admin_splits
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from ingest.edgar.client import EdgarClient
from pipeline import backfill_splits as bf
from pipeline import cron_run_log, split_unplaced
from pipeline.detect_splits import SplitDetection, Unplaced
from repositories import split_repo
from tests.pipeline.test_split_unplaced import AIRI_8K, AIRI_DOC, AIRI_PASSAGE, _airi
from tests.split_801_fixtures import ENHA_8K, OLD_ITEMS, enha_polygon, seed_enha
from tests.split_801_fixtures import SINCE as SINCE_8_01
from tests.split_fixtures import edgar_cache, polygon_source, seed_union, v1_source

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
        "unplaced": [],  # F1-3 — no run artifact, no check record
        "unplaced_dismissed": [],
        "unplaced_runs": [],
        "unplaced_error": None,
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


# --- D2 (platform fixes W2-3): a distribution the nightly set aside, and the steward's restore ---------------


def test_a_distribution_set_aside_automatically_is_quiet_and_restores_to_review(
    client, db, tmp_path
):
    from tests.pipeline.test_detect_splits import _detect_scco
    from tests.split_801_fixtures import seed_member

    seed_member(db, "SCCO", None, [])
    _detect_scco(db, tmp_path, asof=date(2026, 11, 5))
    body = client.get(BASE).json()
    assert (
        body["needs_review"] == []
        and client.get("/admin/status").json()["splits_needs_review"] == 0
    )
    bucket, scco = _find(body, "SCCO", "2026-11-03")
    assert bucket == "set_aside"
    assert (scco["status"], scco["decided_by"], scco["restore_to"], scco["actions"]) == (
        "rejected",
        "set aside automatically: a distribution",
        "needs_review",
        ["restore"],
    )
    assert scco["label"] == "1.0106-for-1 forward split" and scco["value_source"] == "polygon"
    assert scco["note"].startswith(
        "Set aside automatically: a 1.0106-for-1 forward 'split' listed by Massive.com only, under x1.5"
    )
    n = split_repo.count_rows(db)
    back = client.post(f"{BASE}/{scco['version_id']}/restore")
    assert back.status_code == 200, back.text
    b = back.json()
    assert (b["status"], b["actions"]) == ("needs_review", ["confirm:polygon", "reject"])
    assert b["decided_by"].startswith("restored by you, ") and b["review_reason"] == "one_source"
    assert split_repo.count_rows(db) == n + 1
    assert client.get("/admin/status").json()["splits_needs_review"] == 1


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


def test_the_tape_window_marks_a_massive_fill_and_the_step_never_reads_it(client, db):
    """W6-3b: the first session's bar recorded as Massive.com's fill (``price_bar_fill``) is shown, marked ``filled``,
    and the step at it reads nothing (a fill is never split evidence)."""
    version_id = _seed_gns(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO price_bar_fill (tenant_id, security_id, d, bar_recorded_at, source, vendor_symbol, "
            "raw_close, raw_volume, factor, reason) SELECT tenant_id, security_id, d, recorded_at, 'massive', "
            "'GNS', close, volume, 1, 'split_window' FROM fact_price_eod WHERE d = %s AND close = 0.499",
            (GNS_SESSION,),
        )
    db.commit()
    w = client.get(f"{BASE}/{version_id}/tape").json()
    bars = {b["d"]: b for b in w["bars"]}
    assert (bars["2026-11-20"]["close"], bars["2026-11-20"]["filled"]) == (0.499, True)
    assert not any(b["filled"] for d, b in bars.items() if d != "2026-11-20")
    assert (w["prior_date"], w["step"]) == (None, None)


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


def test_massives_label_rides_the_polygon_block(client, db, tmp_path):
    """W6-1: a list the v1 reader filled carries Massive.com's label beside its ratio (evidence: the verdicts are the
    v3 fill's, checked in ``test_backfill_splits``); a row stored without one shows null."""
    seed_union(db)
    polygon, _ = v1_source(tmp_path)
    bf.backfill_splits(
        db,
        polygon=polygon,
        edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        since=date(2025, 8, 1),
        asof=date(2026, 10, 20),
        apply=True,
    )
    body = client.get(BASE).json()
    _, btoc = _find(body, "BTOC", "2026-10-15")
    assert btoc["polygon"]["adjustment_type"] == "reverse_split"
    assert btoc["polygon"]["split_from"] == "16" and btoc["decided_by"] == "auto: SEC + Massive.com"
    _, mi = _find(body, "MI", "2026-05-18")
    assert (mi["status"], mi["polygon"]["adjustment_type"]) == ("needs_review", "reverse_split")


def test_a_row_stored_without_a_label_shows_null(client, db, tmp_path):
    _fill(db, tmp_path)
    _, btoc = _find(client.get(BASE).json(), "BTOC", "2026-10-15")
    assert "adjustment_type" in btoc["polygon"] and btoc["polygon"]["adjustment_type"] is None


# --- F1-3: split filings the list could not place, and the steward's Dismiss (framework completion) ------------

NIGHTLY_NAME = "20261126T033001Z.json"
NIGHTLY_STARTED = "2026-11-26T03:30:01.884083+00:00"
CHECK_STARTED = datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc)
NO_DATE = "no first-session or effective date stated"
PBK_6K = "0001493152-26-052687"  # a listed split cites it
PBK_URL = "https://www.sec.gov/Archives/edgar/data/1915657/000149315226052687/"
PBK_PASSAGE = "the Company effected a 1-for-20 reverse split of its ordinary shares"
VRM_8K = "0001193125-25-000195"  # filed before the nightly's window: the long-window check's
WLDS_8K = "0001213900-26-104321"  # states only an authorized range
AVX_8K = "0001104659-26-100001"  # states a 1-for-8 and a past date only
META_8K = "0001628280-26-066700"  # a row written before rows carried their link and ratio
META_INDEX = "https://www.sec.gov/Archives/edgar/data/1326801/000162828026066700/"
DISMISS_DEFAULT = "not a split to list, said the steward"


@pytest.fixture(autouse=True)
def unplaced_home(tmp_path, monkeypatch):
    """Both homes the unplaced list is read from (``pipeline.split_unplaced``), in a per-test tmp dir for EVERY
    test here: the panel reads them on each GET, so a dev machine's real ``data/cron_runs`` or
    ``data/split_unplaced`` never leaks into an assertion. Returns ``(runs, records)``."""
    runs, records = tmp_path / "cron_runs", tmp_path / "split_unplaced"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", runs)
    monkeypatch.setattr(split_unplaced, "UNPLACED_DIR", records)
    return runs, records


def _company(db, ticker: str, cik: str | None, name: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, name, date(2020, 1, 1)),
        )
    db.commit()
    return sid


def _listed(
    db,
    sid: uuid.UUID,
    first_session: date,
    ratio_from: int,
    ratio_to: int,
    *,
    status: str = "confirmed",
    accession: str | None = None,
    sec_filed: date | None = None,
) -> str:
    """One split on the list (a nightly decision; listed on its first session, its SEC filing filed on ``sec_filed``);
    returns its version id. Commits."""
    v = split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=first_session,
        values={
            "effective_date": first_session,
            "ratio_from": ratio_from,
            "ratio_to": ratio_to,
            "status": status,
            "confirmed_via": "auto:sec+polygon" if status == "confirmed" else None,
            "value_source": "sec",
            "sec_accession": accession,
            "sec_filed": sec_filed,
            "valid_from": first_session,
        },
        action=split_repo.ACTION_DETECT,
        author=split_repo.AUTHOR_DETECT,
    )
    db.commit()
    return str(v.id)


def _row(
    sid: uuid.UUID, ticker: str, accession: str, form: str, filed: str, reason: str, **ev
) -> dict:
    """An artifact row as ``Unplaced.to_json`` writes it; with no evidence keys, as written before F1-1."""
    return {
        "security_id": str(sid),
        "ticker": ticker,
        "accession": accession,
        "form": form,
        "filed": filed,
        "reason": reason,
        **ev,
    }


def _nightly(
    runs: Path,
    rows: list[dict],
    *,
    name: str = NIGHTLY_NAME,
    asof: str = "2026-11-25",
    calloffs: list[dict] | None = None,
) -> None:
    """A finished live nightly artifact whose split detection ran, listing ``rows`` as unplaced (and ``calloffs``
    as the filings it read with call-off wording, F10; no key when ``None``, as written before them).
    """
    runs.mkdir(parents=True, exist_ok=True)
    doc = {
        "status": "finished",
        "mode": "live",
        "asof": asof,
        "started_at": NIGHTLY_STARTED,
        "split_detect": {"evaluated": True, "unplaced": rows},
    }
    if calloffs is not None:
        doc["split_detect"]["calloffs"] = calloffs
    (runs / name).write_text(json.dumps(doc), encoding="utf-8")


def _seed_unplaced(db, home) -> dict[str, str]:
    """Five companies, one per ``listed_near`` branch, with their unplaced filings on the nightly's artifact
    (2026-11-25) and the long-window check's record (2026-11-27, VRM's: filed before the nightly's window).
    Returns the security ids by ticker and the listed versions the hints should name."""
    runs, _ = home
    pbk = _company(db, "PBK", "1915657", "PBK Holdings")
    vrm = _company(db, "VRM", "1580864", "Vroom")
    wlds = _company(db, "WLDS", "1887673", "Wearable Devices")
    avx = _company(db, "AVX", None, "AVX Corp")  # no CIK on file
    meta = _company(db, "META", "0001326801", "Meta Platforms")
    ids = {"PBK": str(pbk), "VRM": str(vrm), "WLDS": str(wlds), "AVX": str(avx), "META": str(meta)}
    # 1. cites: the split citing the filing wins over a nearer one of the same ratio
    ids["pbk_cites"] = _listed(
        db, pbk, date(2026, 11, 30), 20, 1, status="needs_review", accession=PBK_6K
    )
    _listed(db, pbk, date(2026, 11, 18), 20, 1, accession="0001493152-26-040001")
    # 2. the same exact ratio at any distance (151 days on), over a nearer split of another ratio and a farther
    # one of the same ratio
    ids["vrm_same"] = _listed(db, vrm, date(2025, 6, 2), 5, 1)
    _listed(db, vrm, date(2023, 1, 3), 5, 1)
    _listed(db, vrm, date(2025, 1, 10), 1, 2)
    # 3. near: no ratio stated, the nearest within 120 days; and a ratio no listed split shares, the nearest
    # within 120 days, before the filing
    ids["wlds_near"] = _listed(db, wlds, date(2026, 12, 15), 10, 1, status="needs_review")
    _listed(db, wlds, date(2026, 3, 2), 10, 1)
    ids["avx_near"] = _listed(db, avx, date(2026, 10, 1), 10, 1)
    # 4. none: its only listed split is 233 days before the filing
    _listed(db, meta, date(2026, 3, 2), 4, 1)
    _nightly(
        runs,
        [
            _row(
                pbk,
                "PBK",
                PBK_6K,
                "6-K",
                "2026-11-17",
                f"1-for-20 reverse split stated, but {NO_DATE}",
                url=PBK_URL,
                document="form6-k.htm",
                passage=PBK_PASSAGE,
                ratio_from="20",
                ratio_to="1",
                abstain_reason=None,
                date_note=NO_DATE,
            ),
            _row(
                wlds,
                "WLDS",
                WLDS_8K,
                "8-K",
                "2026-11-25",
                "only an authorized range of reverse split ratios stated",
                abstain_reason="only an authorized range of reverse split ratios stated",
            ),
            _row(
                avx,
                "AVX",
                AVX_8K,
                "8-K",
                "2026-10-20",
                "1-for-8 reverse split stated, but only a past date",
                ratio_from="8",
                ratio_to="1",
            ),
            _row(
                meta,
                "META",
                META_8K,
                "8-K",
                "2026-10-21",
                f"2-for-1 forward split stated, but {NO_DATE}",
            ),
        ],
    )
    split_unplaced.record_check(
        SplitDetection(
            evaluated=True,
            unplaced=[
                Unplaced(
                    vrm,
                    "VRM",
                    VRM_8K,
                    "8-K",
                    date(2025, 1, 2),
                    f"1-for-5 reverse split stated, but {NO_DATE}",
                    ratio_from=Decimal(5),
                    ratio_to=Decimal(1),
                    date_note=NO_DATE,
                )
            ],
        ),
        started_at=CHECK_STARTED,
        finished_at=CHECK_STARTED.replace(minute=40),
        asof=date(2026, 11, 27),
        sec_days=800,
        polygon_days=800,
    )
    return ids


def _filing_dismissals(db) -> int:
    db.rollback()  # a fresh snapshot: the route committed on its own connection
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM split_filing_dismissal")
        return cur.fetchone()["n"]


def _near(u: dict) -> tuple | None:
    n = u["listed_near"]
    if n is None:
        return None
    return (n["version_id"], n["same_ratio"], n["cites_filing"], n["days_from"])


def test_the_panel_lists_the_unplaced_filings_newest_first_with_the_listed_split_near_each(
    client, db, unplaced_home
):
    """The nightly's unplaced filings and the long-window check's, newest filing first, each with the split on the
    list it most likely talks about: the one citing it (PBK), else the nearest of its exact ratio at any distance
    (VRM), else the nearest within 120 days (WLDS states no ratio; AVX a ratio no listed split shares), else none
    (META). The link is the artifact's, else built from the company's CIK, never guessed. A read: nothing written.
    """
    ids = _seed_unplaced(db, unplaced_home)
    n = split_repo.count_rows(db)
    r = client.get(BASE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unplaced_error"] is None and body["unplaced_dismissed"] == []
    rows = body["unplaced"]
    assert [(u["ticker"], u["filed"], u["source"], u["seen_on"]) for u in rows] == [
        ("WLDS", "2026-11-25", "nightly", "2026-11-25"),
        ("PBK", "2026-11-17", "nightly", "2026-11-25"),
        ("META", "2026-10-21", "nightly", "2026-11-25"),
        ("AVX", "2026-10-20", "nightly", "2026-11-25"),
        ("VRM", "2025-01-02", "check", "2026-11-27"),
    ]
    by = {u["ticker"]: u for u in rows}
    assert by["PBK"]["listed_near"] == {
        "version_id": ids["pbk_cites"],
        "key_date": "2026-11-30",
        "effective_date": "2026-11-30",
        "label": "1-for-20 reverse split",
        "status": "needs_review",
        "same_ratio": True,
        "cites_filing": True,
        "days_from": 13,
    }
    assert _near(by["VRM"]) == (ids["vrm_same"], True, False, 151)
    assert _near(by["WLDS"]) == (ids["wlds_near"], None, False, 20)
    assert _near(by["AVX"]) == (ids["avx_near"], False, False, -19)
    assert by["AVX"]["listed_near"]["label"] == "1-for-10 reverse split"
    assert by["META"]["listed_near"] is None
    pbk = by["PBK"]
    assert (pbk["security_id"], pbk["name"], pbk["accession"], pbk["form"]) == (
        ids["PBK"],
        "PBK Holdings",
        PBK_6K,
        "6-K",
    )
    assert (pbk["url"], pbk["document"], pbk["passage"]) == (PBK_URL, "form6-k.htm", PBK_PASSAGE)
    assert (pbk["ratio_from"], pbk["ratio_to"], pbk["ratio_label"]) == (
        "20",
        "1",
        "1-for-20 reverse split",
    )
    assert pbk["reason"] == f"1-for-20 reverse split stated, but {NO_DATE}"
    assert by["WLDS"]["ratio_label"] is None and by["VRM"]["ratio_label"] == "1-for-5 reverse split"
    # a row written before rows carried their evidence: the link from the CIK, the rest null
    meta = by["META"]
    assert meta["url"] == META_INDEX
    assert (meta["document"], meta["passage"], meta["ratio_from"], meta["ratio_label"]) == (
        None,
    ) * 4
    assert by["AVX"]["url"] is None  # no link on the row and no CIK on file
    assert all(u["dismissal_id"] is None and u["dismissed_at"] is None for u in rows)
    runs = body["unplaced_runs"]
    assert [(x["source"], x["asof"], x["sec_days"], x["rows"]) for x in runs] == [
        ("nightly", "2026-11-25", None, 4),
        ("check", "2026-11-27", 800, 1),
    ]
    assert datetime.fromisoformat(runs[1]["started_at"]) == CHECK_STARTED
    assert split_repo.count_rows(db) == n and _filing_dismissals(db) == 0


def test_dismiss_moves_a_filing_to_the_dismissed_list_and_undo_brings_it_back(
    client, db, unplaced_home
):
    """Dismiss ("not a split to list") hides the filing behind the dismissed list with its reason, never deleted
    (WB#2); a second Dismiss is refused (409); a filing not on the list is 404 and a malformed accession 422; Undo
    puts it back (WB#1); a second Undo is 409, an unknown one 404. Each action appends ONE row: COUNT THE TABLE.
    """
    ids = _seed_unplaced(db, unplaced_home)
    meta = ids["META"]
    r = client.post(f"{BASE}/unplaced/{meta}/{META_8K}/dismiss")
    assert r.status_code == 200, r.text
    gone = r.json()
    assert (gone["ticker"], gone["accession"], gone["dismissed_reason"]) == (
        "META",
        META_8K,
        DISMISS_DEFAULT,
    )
    assert gone["dismissal_id"] and gone["dismissed_at"] and gone["url"] == META_INDEX
    assert _filing_dismissals(db) == 1
    with db.cursor() as cur:
        cur.execute("SELECT author, action, detail FROM split_filing_dismissal")
        stored = cur.fetchone()
    assert (stored["author"], stored["action"]) == ("operator:dismiss", "dismiss")
    assert (stored["detail"]["form"], stored["detail"]["filed"], stored["detail"]["source"]) == (
        "8-K",
        "2026-10-21",
        "nightly",
    )
    body = client.get(BASE).json()
    assert META_8K not in [u["accession"] for u in body["unplaced"]] and len(body["unplaced"]) == 4
    assert [(u["ticker"], u["dismissal_id"]) for u in body["unplaced_dismissed"]] == [
        ("META", gone["dismissal_id"])
    ]
    again = client.post(f"{BASE}/unplaced/{meta}/{META_8K}/dismiss", json={"reason": "twice"})
    assert again.status_code == 409, again.text
    assert client.post(f"{BASE}/unplaced/{meta}/{PBK_6K}/dismiss").status_code == 404
    assert client.post(f"{BASE}/unplaced/{uuid.uuid4()}/{META_8K}/dismiss").status_code == 404
    assert client.post(f"{BASE}/unplaced/{meta}/not-an-accession/dismiss").status_code == 422
    assert _filing_dismissals(db) == 1
    w = client.post(
        f"{BASE}/unplaced/{ids['WLDS']}/{WLDS_8K}/dismiss",
        json={"reason": "a share authorization, not a split"},
    )
    assert (
        w.status_code == 200
        and w.json()["dismissed_reason"] == "a share authorization, not a split"
    )
    undo = client.post(f"{BASE}/unplaced/dismissals/{gone['dismissal_id']}/undo")
    assert undo.status_code == 200, undo.text
    back = undo.json()
    assert (back["accession"], back["dismissal_id"], back["dismissed_reason"]) == (
        META_8K,
        None,
        None,
    )
    assert _filing_dismissals(db) == 3
    body = client.get(BASE).json()
    assert [u["ticker"] for u in body["unplaced"]] == ["PBK", "META", "AVX", "VRM"]
    assert [u["ticker"] for u in body["unplaced_dismissed"]] == ["WLDS"]
    assert client.post(f"{BASE}/unplaced/dismissals/{gone['dismissal_id']}/undo").status_code == 409
    assert client.post(f"{BASE}/unplaced/dismissals/{uuid.uuid4()}/undo").status_code == 404
    assert _filing_dismissals(db) == 3


def test_an_undo_after_the_filing_left_the_list_is_404_and_writes_nothing(
    client, db, unplaced_home
):
    """The next nightly no longer lists WLDS's filing (a later filing placed the split): its standing dismissal is
    listed nowhere, and its Undo is refused (404) with nothing written."""
    ids = _seed_unplaced(db, unplaced_home)
    d = client.post(f"{BASE}/unplaced/{ids['WLDS']}/{WLDS_8K}/dismiss").json()
    runs, _ = unplaced_home
    _nightly(runs, [], name="20261127T033001Z.json", asof="2026-11-26")
    body = client.get(BASE).json()
    assert [u["ticker"] for u in body["unplaced"]] == ["VRM"] and body["unplaced_dismissed"] == []
    n = _filing_dismissals(db)
    r = client.post(f"{BASE}/unplaced/dismissals/{d['dismissal_id']}/undo")
    assert r.status_code == 404, r.text
    assert _filing_dismissals(db) == n
    assert client.post(f"{BASE}/unplaced/{ids['WLDS']}/{WLDS_8K}/dismiss").status_code == 404


def test_an_unreadable_unplaced_read_is_shown_and_the_panel_still_renders(
    client, db, unplaced_home, monkeypatch
):
    """A corrupt newest artifact with nothing older to read: ``unplaced_error`` names it and the rest of the panel
    renders (200), as ``unlisted_jumps_error`` does. A read that raises is shown the same way."""
    runs, _ = unplaced_home
    runs.mkdir(parents=True)
    (runs / NIGHTLY_NAME).write_text('{"status": "finished", "split_de', encoding="utf-8")
    pbk = _company(db, "PBK", "1915657", "PBK Holdings")
    _listed(db, pbk, date(2026, 11, 30), 20, 1, status="needs_review")
    r = client.get(BASE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total"], len(body["needs_review"])) == (1, 1)
    assert (body["unplaced"], body["unplaced_runs"]) == ([], [])
    assert NIGHTLY_NAME in body["unplaced_error"]
    assert body["needs_review"][0]["called_off"] is None  # F10: nothing read, nothing named

    def boom(**_kw):
        raise OSError("the data volume is gone")

    monkeypatch.setattr(admin_splits, "read_unplaced", boom)
    body = client.get(BASE).json()
    assert body["unplaced_error"] == "OSError: the data volume is gone"
    assert (body["total"], body["unplaced"]) == (1, [])


def test_a_filing_dismissal_never_reaches_the_split_list_or_the_hold():
    """STRUCTURAL: the split detector and the split-night hold (the signal and its pure rules) never name the
    filing-dismissal log or its repository: a Dismiss is display, never a decision or call input."""
    import inspect

    from domain import split_hold as hold_rules
    from pipeline import detect_splits
    from signals import split_hold

    for mod in (split_hold, hold_rules, detect_splits):
        src = inspect.getsource(mod)
        assert "split_filing_dismissal" not in src, mod.__name__
        assert "split_filing_dismissal_repo" not in src, mod.__name__


# --- F2-1: the steward ADDS a split no source lists, from an unplaced filing or a jump (framework completion) ------

ADD_CITED = "https://example.test/vrm/8-k/0001193125-25-000195"
VRM_INDEX = "https://www.sec.gov/Archives/edgar/data/1580864/000119312525000195/"
GAME_CITED = "https://example.test/game/8-k"
ADDED_NOTE = "Added by the steward: no source lists this split."


def _add_body(security_id, **over) -> dict:
    """VRM's 1-for-5, first session 2025-01-15, as the steward types it (the filing's fields are the server's)."""
    return {
        "security_id": str(security_id),
        "ratio_from": "5",
        "ratio_to": "1",
        "first_session": "2025-01-15",
        "cited_url": ADD_CITED,
        **over,
    }


def test_the_steward_adds_a_split_from_an_unplaced_filing(client, db, unplaced_home, pinned):
    """VRM's 8-K (the long-window check's row: a 1-for-5 with no date) is on no list. The steward adds the split
    with cited values: no stored bar on that session, so it is refused (409, nothing written) until the steward
    overrides the tape. The row is the steward's (``value_source`` ``steward``, action ``add``), carries the filing
    the server read from the unplaced list (never the client's), and is public from the filing's day (the same
    ratio). On the next read the filing's ``listed_near`` cites it. One version: the table counted.
    """
    ids = _seed_unplaced(db, unplaced_home)
    n = split_repo.count_rows(db)
    body = _add_body(ids["VRM"], from_accession=VRM_8K)
    r = client.post(BASE, json=body)
    assert r.status_code == 409 and "no stored bar on 2025-01-15" in r.json()["detail"], r.text
    assert split_repo.count_rows(db) == n
    ok = client.post(BASE, json={**body, "override_tape": True})
    assert ok.status_code == 200, ok.text
    a = ok.json()
    assert (a["ticker"], a["key_date"], a["effective_date"], a["label"]) == (
        "VRM",
        "2025-01-15",
        "2025-01-15",
        "1-for-5 reverse split",
    )
    assert (a["status"], a["value_source"], a["confirmed_via"], a["author"], a["action"]) == (
        "confirmed",
        "steward",
        "operator",
        "operator",
        "add",
    )
    assert a["decided_by"].startswith("added by you (cited values), ") and a["actions"] == [
        "revoke"
    ]
    assert (a["cited_url"], a["tape_override"], a["valid_from"]) == (ADD_CITED, True, "2025-01-02")
    sec = a["sec"]
    assert (sec["accession"], sec["form"], sec["filed"], sec["url"], sec["ratio_label"]) == (
        VRM_8K,
        "8-K",
        "2025-01-02",
        VRM_INDEX,  # the check's row carries no link: the index built from the company's CIK
        "1-for-5 reverse split",
    )
    reason = f"1-for-5 reverse split stated, but {NO_DATE}"
    assert sec["abstain_reason"] == reason
    assert a["note"] == f"{ADDED_NOTE} From 8-K {VRM_8K} filed 2025-01-02: {reason}."
    assert split_repo.count_rows(db) == n + 1
    stored = _stored(db, a["version_id"])
    assert (stored["action"], stored["author"], stored["cited_url"]) == (
        "add",
        "operator",
        ADD_CITED,
    )
    listed = client.get(BASE).json()
    assert _find(listed, "VRM", "2025-01-15")[0] == "confirmed"
    (vrm,) = [u for u in listed["unplaced"] if u["ticker"] == "VRM"]
    assert _near(vrm) == (a["version_id"], True, True, 13)  # a listed split now cites the filing


def test_an_added_split_is_undone_by_revoke_and_returned_by_restore(
    client, db, unplaced_home, pinned
):
    ids = _seed_unplaced(db, unplaced_home)
    a = client.post(
        BASE, json=_add_body(ids["VRM"], from_accession=VRM_8K, override_tape=True)
    ).json()
    n = split_repo.count_rows(db)
    rv = client.post(f"{BASE}/{a['version_id']}/revoke").json()
    assert (rv["status"], rv["restore_to"], rv["value_source"], rv["cited_url"]) == (
        "revoked",
        "confirmed",
        "steward",
        ADD_CITED,
    )
    assert _find(client.get(BASE).json(), "VRM", "2025-01-15")[0] == "set_aside"  # kept, grayed
    back = client.post(f"{BASE}/{rv['version_id']}/restore").json()
    assert (back["status"], back["ratio_from"], back["cited_url"], back["valid_from"]) == (
        "confirmed",
        "5",
        ADD_CITED,
        "2025-01-02",
    )
    assert back["decided_by"].startswith("restored by you, ")
    assert split_repo.count_rows(db) == n + 2


def test_an_add_is_refused_for_a_split_already_listed_or_a_filing_not_on_the_list(
    client, db, unplaced_home, pinned
):
    """PBK's 1-for-20 is listed on 11-30 (a live split of the same ratio within 45 days: 409); WLDS's 12-15 is keyed
    already (409); a dismissed filing or one not on the unplaced list is refused (409), an unknown security is 404.
    Each refusal writes nothing."""
    ids = _seed_unplaced(db, unplaced_home)
    client.post(f"{BASE}/unplaced/{ids['META']}/{META_8K}/dismiss")
    n = split_repo.count_rows(db)
    pbk = _add_body(ids["PBK"], ratio_from="20", first_session="2026-12-01", from_accession=PBK_6K)
    r = client.post(BASE, json={**pbk, "override_tape": True})
    assert r.status_code == 409 and "1-for-20 reverse split" in r.json()["detail"], r.text
    wlds = _add_body(
        ids["WLDS"], ratio_from="20", first_session="2026-12-15", from_accession=WLDS_8K
    )
    r = client.post(BASE, json={**wlds, "override_tape": True})
    assert r.status_code == 409 and "2026-12-15" in r.json()["detail"], r.text
    meta = _add_body(ids["META"], ratio_from="1", ratio_to="2", from_accession=META_8K)
    r = client.post(BASE, json={**meta, "override_tape": True})
    assert r.status_code == 409 and "dismissed" in r.json()["detail"], r.text
    r = client.post(BASE, json={**meta, "from_accession": PBK_6K, "override_tape": True})
    assert r.status_code == 409 and "not on the unplaced list" in r.json()["detail"], r.text
    unknown = _add_body(uuid.uuid4(), from_accession=VRM_8K, override_tape=True)
    assert client.post(BASE, json=unknown).status_code == 404
    assert split_repo.count_rows(db) == n


def test_an_add_names_exactly_one_signal_and_is_bounded(client, db, unplaced_home, pinned):
    ids = _seed_unplaced(db, unplaced_home)
    n = split_repo.count_rows(db)
    vrm = _add_body(ids["VRM"], from_accession=VRM_8K, override_tape=True)
    no_url = {k: v for k, v in vrm.items() if k != "cited_url"}
    for bad in (
        {k: v for k, v in vrm.items() if k != "from_accession"},  # neither: no bare add (D-F2a)
        {**vrm, "from_jump": "2025-01-15"},  # both
        no_url,
        {**vrm, "cited_url": "http://example.test/vrm"},  # https only
        {**vrm, "ratio_to": "5"},  # 5 -> 5 is not a split
        {**vrm, "ratio_from": "0"},
        {**vrm, "ratio_from": "1e3"},
        {**vrm, "from_accession": "0001193125-25-00019"},  # not an accession number
        {**vrm, "first_session": "1980-01-01"},
        {**vrm, "note": "x" * 501},
        {k: v for k, v in vrm.items() if k != "security_id"},
    ):
        assert client.post(BASE, json=bad).status_code == 422, bad
    assert split_repo.count_rows(db) == n


def test_the_steward_adds_a_split_from_a_split_shaped_jump(client, db, monkeypatch):
    """GAME's real 08-24 step (its 1-for-8 NOT on the list), asof 08-28: the "is this real?" card. A dismissed jump
    is refused (409); undone, the steward adds the 1-for-8 the 8-K states: the stored tape's x7.5 is inside the
    tolerance of x8, so no override. The jump leaves the list (the split now covers it), and is not dismissed.
    """
    from tests import split_s5_fixtures as s5

    monkeypatch.setattr(admin_splits, "market_today", lambda: date(2026, 8, 28))
    sid = s5.seed(db, "GAME", listed=False)
    s5.seed_thesis(db, [("GAME", sid)])
    assert [j["ticker"] for j in client.get(BASE).json()["unlisted_jumps"]] == ["GAME"]
    q = {"first_session": "2026-08-24", "ratio_from": "8", "ratio_to": "1"}
    t = client.get(f"{BASE}/securities/{sid}/tape-step", params=q).json()
    assert t["step"] == pytest.approx(7.5, rel=1e-3) and t["within_tolerance"] is True
    body = {
        "security_id": str(sid),
        "ratio_from": "8",
        "ratio_to": "1",
        "first_session": "2026-08-24",
        "cited_url": GAME_CITED,
        "from_jump": "2026-08-24",
    }
    d = client.post(f"{BASE}/jumps/{sid}/2026-08-24/dismiss").json()
    n = split_repo.count_rows(db)
    r = client.post(BASE, json=body)
    assert r.status_code == 409 and "dismissed" in r.json()["detail"], r.text
    assert client.post(f"{BASE}/jumps/dismissals/{d['dismissal_id']}/undo").status_code == 200
    ok = client.post(BASE, json=body)
    assert ok.status_code == 200, ok.text
    a = ok.json()
    assert (a["ticker"], a["effective_date"], a["status"], a["value_source"], a["action"]) == (
        "GAME",
        "2026-08-24",
        "confirmed",
        "steward",
        "add",
    )
    assert (a["tape_override"], a["valid_from"], a["sec"], a["polygon"]) == (
        False,
        "2026-08-24",
        None,
        None,
    )
    assert a["note"] == f"{ADDED_NOTE} From the split-shaped jump on 2026-08-24, x7.5."
    assert split_repo.count_rows(db) == n + 1
    listed = client.get(BASE).json()
    assert listed["unlisted_jumps"] == [] and listed["dismissed_jumps"] == []
    assert _find(listed, "GAME", "2026-08-24")[0] == "confirmed"
    # the jump is no longer on the list: a second add from it is refused, as is one from a day with no jump
    assert client.post(BASE, json=body).status_code == 409
    other = client.post(
        BASE, json={**body, "first_session": "2026-08-21", "from_jump": "2026-08-21"}
    )
    assert other.status_code == 409 and "no split-shaped jump" in other.json()["detail"]
    assert split_repo.count_rows(db) == n + 1


def test_the_security_tape_step_is_the_version_tape_step_on_the_same_bars(client, db):
    """``GET /admin/splits/securities/{security_id}/tape-step`` (the add form's read) is the version route's reading
    keyed by security: the same answer on POM's bars for every query, 404 for an unknown security, 422 for half a
    ratio. A read."""
    sid = _seed_pom(db)
    card = _pom_card(client)
    n = split_repo.count_rows(db)
    q = {"first_session": "2026-10-21", "ratio_from": "10", "ratio_to": "3"}
    for params in (
        q,
        {**q, "ratio_to": "1"},
        {"first_session": "2026-10-21"},
        {**q, "first_session": "2026-10-22"},
    ):
        by_version = client.get(f"{BASE}/{card['version_id']}/tape-step", params=params)
        by_security = client.get(f"{BASE}/securities/{sid}/tape-step", params=params)
        assert by_security.status_code == 200, by_security.text
        assert by_security.json() == by_version.json(), params
    assert client.get(f"{BASE}/securities/{uuid.uuid4()}/tape-step", params=q).status_code == 404
    half = {"first_session": "2026-10-21", "ratio_from": "10"}
    assert client.get(f"{BASE}/securities/{sid}/tape-step", params=half).status_code == 422
    assert split_repo.count_rows(db) == n


# --- F8-3: an open split names a listed split of its exact ratio near it (display only) -------------------------

APUS_MARCH = date(2025, 3, 26)
APUS_JULY = date(2025, 7, 24)  # 120 days on
TRIO_8K = "0001104659-26-200001"


def _same(e: dict) -> list[tuple] | None:
    s = e["same_ratio_listed"]
    if s is None:
        return None
    return [(n["version_id"], n["status"], n["days_from"]) for n in s]


def test_apus_open_split_names_the_listed_split_of_its_ratio_120_days_away_either_way(client, db):
    """APUS's shape: a needs-review 1-for-10 on 03-26 and a confirmed 1-for-10 on 07-24 of the same company. The open
    March split names the July one, +120 days; the mirror (a confirmed March, a needs-review July) names March, -120.
    A settled split carries none, and once the steward rejects March neither APUS row does. Display only: the read
    writes nothing, and no rule merges the two (D-F8c: the steward's).
    """
    apus = _company(db, "APUS", None, "Apus Corp")
    mirror = _company(db, "APUM", None, "Apus Mirror")
    march = _listed(db, apus, APUS_MARCH, 10, 1, status="needs_review")
    july = _listed(db, apus, APUS_JULY, 10, 1)
    m_march = _listed(db, mirror, APUS_MARCH, 10, 1)
    _listed(db, mirror, APUS_JULY, 10, 1, status="needs_review")
    n = split_repo.count_rows(db)
    body = client.get(BASE).json()
    _, open_march = _find(body, "APUS", "2025-03-26")
    assert open_march["same_ratio_listed"] == [
        {
            "version_id": july,
            "key_date": "2025-07-24",
            "effective_date": "2025-07-24",
            "label": "1-for-10 reverse split",
            "status": "confirmed",
            "same_ratio": True,
            "cites_filing": False,
            "days_from": 120,
        }
    ]
    _, open_july = _find(body, "APUM", "2025-07-24")
    assert _same(open_july) == [(m_march, "confirmed", -120)]
    for ticker, d in (("APUS", "2025-07-24"), ("APUM", "2025-03-26")):
        bucket, settled = _find(body, ticker, d)
        assert bucket == "confirmed" and settled["same_ratio_listed"] is None
    assert split_repo.count_rows(db) == n  # a read
    r = client.post(f"{BASE}/{march}/reject")
    assert r.status_code == 200, r.text
    assert r.json()["same_ratio_listed"] is None
    body = client.get(BASE).json()
    assert _find(body, "APUS", "2025-03-26")[0] == "set_aside"
    rows = [_find(body, "APUS", d)[1] for d in ("2025-03-26", "2025-07-24")]
    assert [e["same_ratio_listed"] for e in rows] == [None, None]
    assert _same(_find(body, "APUM", "2025-07-24")[1]) == [(m_march, "confirmed", -120)]


def test_the_hint_reaches_180_days_names_only_the_exact_ratio_and_at_most_the_two_nearest(
    client, db, monkeypatch
):
    """An open split names another listed split of its exact ratio up to 180 days either side, of any status (a
    rejected one too), never one 181 days away or of another ratio; at most two, nearest first, the earlier first
    session on a tie. An announced split carries it like a needs-review one; one citing the same SEC filing says so.
    It is read from the list's versions already read: one read of the list.
    """
    far = _company(db, "FARX", None, "Far Corp")
    _listed(db, far, date(2025, 6, 2), 10, 1, status="needs_review")
    _listed(db, far, date(2025, 11, 30), 10, 1)  # 181 days on: past the window
    _listed(db, far, date(2025, 7, 1), 5, 1)  # another ratio, near
    edge = _listed(db, far, date(2024, 12, 4), 10, 1, status="rejected")  # 180 days before
    trio = _company(db, "TRIO", None, "Trio Corp")
    _listed(db, trio, date(2026, 6, 2), 10, 1, status="announced", accession=TRIO_8K)
    ten = _listed(db, trio, date(2026, 5, 23), 10, 1, accession=TRIO_8K)  # the same filing
    thirty = _listed(db, trio, date(2026, 5, 3), 10, 1)
    # +30 ties -30: the later first session of the two, third, left out
    _listed(db, trio, date(2026, 7, 2), 10, 1)
    body = client.get(BASE).json()
    _, open_far = _find(body, "FARX", "2025-06-02")
    assert _same(open_far) == [(edge, "rejected", -180)]
    assert open_far["same_ratio_listed"][0]["label"] == "1-for-10 reverse split"
    bucket, ahead = _find(body, "TRIO", "2026-06-02")
    assert bucket == "announced"
    assert _same(ahead) == [(ten, "confirmed", -10), (thirty, "confirmed", -30)]
    assert [s["cites_filing"] for s in ahead["same_ratio_listed"]] == [True, False]
    settled = [e for b in ("confirmed", "set_aside") for e in body[b]]
    assert len(settled) == 6 and all(e["same_ratio_listed"] is None for e in settled)
    reads: list[dict] = []
    latest = split_repo.latest_versions

    def spy(conn, **kw):
        reads.append(kw)
        return latest(conn, **kw)

    monkeypatch.setattr(split_repo, "latest_versions", spy)
    out = admin_splits.splits_out(db, tenant_id=DEFAULT_TENANT_ID, asof=TODAY)
    assert len(reads) == 1
    assert [len(e.same_ratio_listed or []) for e in (*out.needs_review, *out.announced)] == [1, 2]


def test_the_real_backfills_lrhc_splits_of_one_ratio_84_days_apart_name_each_other(
    client, db, tmp_path
):
    """On the real backfill Massive.com lists LRHC's 1-for-10 on 01-26 and again on 04-20, both waiting for review:
    each names the other, signed. LRHC's 1-for-6 on 09-08 is another ratio; no other split shares a ratio nearby.
    """
    _fill(db, tmp_path)
    body = client.get(BASE).json()
    _, jan = _find(body, "LRHC", "2026-01-26")
    _, apr = _find(body, "LRHC", "2026-04-20")
    assert _same(jan) == [(apr["version_id"], "needs_review", 84)]
    assert _same(apr) == [(jan["version_id"], "needs_review", -84)]
    _, mi = _find(body, "MI", "2026-05-18")
    assert mi["same_ratio_listed"] is None
    assert all(e["same_ratio_listed"] is None for e in body["confirmed"])


# --- F10: a filing that calls off a listed split, named on its entry (display only) ---------------------------

AIRI_CIK = "1009891"
AIRI_ANNOUNCED = date(2026, 11, 10)  # the 8-K that stated the 1-for-5 (its merger announcement)
AIRI_FIRST = date(2026, 11, 20)
AIRI_LISTED_8K = "0000000000-26-000010"  # a stand-in for the filing the listed split cites
VOTE_PASSAGE = "The Reverse Stock Split Proposal was not approved."
CALLOFF_VARS = (
    "ALPHADECK_SPLIT_CALLOFF_WARN",
    "ALPHADECK_SPLIT_CALLOFF_FORMS",
    "ALPHADECK_SPLIT_CALLOFF_AFTER_DAYS",
    "ALPHADECK_SPLIT_CALLOFF_RATIO",
)


@pytest.fixture
def calloff_env(monkeypatch):
    """The call-off settings at their shipped defaults, re-read by the app on the next request (``get_settings`` is
    cached); ``calloff_env(var, value)`` sets one."""
    for var in CALLOFF_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()

    def put(var: str, value: str) -> None:
        monkeypatch.setenv(var, value)
        get_settings.cache_clear()

    yield put
    get_settings.cache_clear()


def _airi_listed(db, *, status: str = "confirmed") -> tuple[uuid.UUID, str]:
    """AIRI's listed 1-for-5: announced 11-10, first session 11-20. Returns ``(security id, version id)``."""
    sid = _company(db, "AIRI", AIRI_CIK, "Air Industries Group")
    vid = _listed(
        db,
        sid,
        AIRI_FIRST,
        5,
        1,
        status=status,
        accession=AIRI_LISTED_8K,
        sec_filed=AIRI_ANNOUNCED,
    )
    return sid, vid


def _calloffs(e: dict) -> list[tuple] | None:
    c = e["called_off"]
    if c is None:
        return None
    return [(x["accession"], x["source"], x["days_from"]) for x in c]


def test_airis_listed_split_names_the_8k_that_called_it_off_the_day_before_its_first_session(
    client, db, unplaced_home, calloff_env, monkeypatch
):
    """AIRI's shape: a confirmed 1-for-5 (announced 11-10, first session 11-20) and the nightly's call-off row for
    its 8-K 0001213900-26-122096 (filed 11-19: "would not be effecting the reverse stock split ... 1-for-5"). The
    entry names it: the same ratio, one day before the first session, with its link, passage and the run that read
    it. Any status: revoked by the steward, it still says why. Another company's split of the same ratio and
    session names nothing. The run counts the call-offs it read; a call-off is no unplaced filing. A read: nothing
    written, the files read once.
    """
    runs, _ = unplaced_home
    sid, vid = _airi_listed(db)
    other = _company(db, "PBK", "1915657", "PBK Holdings")
    _listed(db, other, AIRI_FIRST, 5, 1, sec_filed=AIRI_ANNOUNCED)
    _nightly(runs, [], calloffs=[_airi(security_id=sid).to_json()])
    n = split_repo.count_rows(db)
    body = client.get(BASE).json()
    bucket, airi = _find(body, "AIRI", "2026-11-20")
    assert bucket == "confirmed"
    assert airi["called_off"] == [
        {
            "accession": AIRI_8K,
            "form": "8-K",
            "filed": "2026-11-19",
            "url": _airi().url,
            "kind": "decision",
            "ratio_label": "1-for-5 reverse split",
            "same_ratio": True,
            "days_from": -1,
            "document": AIRI_DOC,
            "passage": AIRI_PASSAGE,
            "source": "nightly",
            "seen_on": "2026-11-25",
        }
    ]
    assert _find(body, "PBK", "2026-11-20")[1]["called_off"] is None
    assert [(x["source"], x["rows"], x["calloffs"]) for x in body["unplaced_runs"]] == [
        ("nightly", 0, 1)
    ]
    assert body["unplaced"] == [] and body["unplaced_error"] is None
    assert split_repo.count_rows(db) == n  # a read
    reads: list[dict] = []
    real = admin_splits.read_unplaced

    def spy(**kw):
        reads.append(kw)
        return real(**kw)

    monkeypatch.setattr(admin_splits, "read_unplaced", spy)
    out = admin_splits.splits_out(db, tenant_id=DEFAULT_TENANT_ID, asof=TODAY)
    assert len(reads) == 1  # one read serves the unplaced list and the call-offs
    assert {e.ticker: len(e.called_off or []) for e in out.confirmed} == {"AIRI": 1, "PBK": 0}
    r = client.post(f"{BASE}/{vid}/revoke")
    assert r.status_code == 200, r.text
    bucket, revoked = _find(client.get(BASE).json(), "AIRI", "2026-11-20")
    assert bucket == "set_aside" and _calloffs(revoked) == [(AIRI_8K, "nightly", -1)]


def test_the_warning_switched_off_names_nothing(client, db, unplaced_home, calloff_env):
    """``ALPHADECK_SPLIT_CALLOFF_WARN=false``: no entry names a call-off; the run still counts what it read."""
    runs, _ = unplaced_home
    sid, _ = _airi_listed(db)
    _nightly(runs, [], calloffs=[_airi(security_id=sid).to_json()])
    assert _calloffs(_find(client.get(BASE).json(), "AIRI", "2026-11-20")[1]) == [
        (AIRI_8K, "nightly", -1)
    ]
    calloff_env("ALPHADECK_SPLIT_CALLOFF_WARN", "false")
    body = client.get(BASE).json()
    assert _find(body, "AIRI", "2026-11-20")[1]["called_off"] is None
    assert body["unplaced_runs"][0]["calloffs"] == 1


def test_a_calloff_counts_from_the_announcement_to_30_days_past_the_first_session_on_the_forms_set(
    client, db, unplaced_home, calloff_env
):
    """The rule is the settings' (``detect_splits.calloff_rule``), matched by the one matcher against each listed
    split: filed from its announcement (its SEC filing's date, else the day it was listed: a Massive.com-only split)
    to its first session + ``split_calloff_after_days`` (30), both ends in, on a ``split_calloff_forms`` form. Each
    split here is a 1-for-5 announced 10-10, first session 10-20. A call-off the long-window check read is named
    with that run. Outside the window, or on another form, the entry names nothing until the settings reach it.
    """
    runs, _ = unplaced_home
    first, announced = date(2026, 10, 20), date(2026, 10, 10)
    edge = _company(db, "EDGE", None, "Edge Corp")  # filed +30: in (read by the check)
    past = _company(db, "PAST", None, "Past Corp")  # filed +31: out
    early = _company(db, "ERLY", None, "Early Corp")  # filed the day before the announcement: out
    tenq = _company(db, "TENQ", None, "Tenq Corp")  # a 10-Q: not a form that counts
    poly = _company(db, "POLY", None, "Poly Corp")  # Massive.com only: announced = listed, 10-20
    for sid in (edge, past, early, tenq):
        _listed(db, sid, first, 5, 1, sec_filed=announced)
    _listed(db, poly, first, 5, 1)

    def row(sid, ticker, n, filed, form="8-K"):
        return _airi(
            security_id=sid,
            ticker=ticker,
            accession=f"0000000000-26-0000{n}",
            filed=filed,
            form=form,
        )

    _nightly(
        runs,
        [],
        calloffs=[
            row(past, "PAST", 31, date(2026, 11, 20)).to_json(),
            row(early, "ERLY", 32, date(2026, 10, 9)).to_json(),
            row(tenq, "TENQ", 33, date(2026, 10, 30), form="10-Q").to_json(),
            row(poly, "POLY", 34, date(2026, 10, 19)).to_json(),
            row(poly, "POLY", 35, date(2026, 10, 20)).to_json(),
        ],
    )
    split_unplaced.record_check(
        SplitDetection(evaluated=True, calloffs=[row(edge, "EDGE", 30, date(2026, 11, 19))]),
        started_at=CHECK_STARTED,
        finished_at=CHECK_STARTED.replace(minute=40),
        asof=date(2026, 11, 27),
        sec_days=800,
        polygon_days=800,
    )

    def named(body: dict) -> dict[str, list[tuple] | None]:
        return {
            t: _calloffs(_find(body, t, "2026-10-20")[1])
            for t in ("EDGE", "PAST", "ERLY", "TENQ", "POLY")
        }

    assert named(client.get(BASE).json()) == {
        "EDGE": [("0000000000-26-000030", "check", 30)],
        "PAST": None,
        "ERLY": None,
        "TENQ": None,
        "POLY": [("0000000000-26-000035", "nightly", 0)],
    }
    calloff_env("ALPHADECK_SPLIT_CALLOFF_AFTER_DAYS", "31")
    calloff_env("ALPHADECK_SPLIT_CALLOFF_FORMS", "8-K,10-Q")
    got = named(client.get(BASE).json())
    assert got["PAST"] == [("0000000000-26-000031", "nightly", 31)]
    assert got["TENQ"] == [("0000000000-26-000033", "nightly", 10)]
    assert (got["ERLY"], got["POLY"]) == (None, [("0000000000-26-000035", "nightly", 0)])


def test_a_calloff_stating_another_ratio_is_named_under_any_and_not_under_same(
    client, db, unplaced_home, calloff_env
):
    """``split_calloff_ratio``: ``any`` (R1, the default) names a call-off whatever ratio it states, or none, the
    one stating the listed ratio first (``same_ratio`` false for another ratio, null for none); ``same`` (R2) names
    only one stating the listed ratio."""
    runs, _ = unplaced_home
    sid, _ = _airi_listed(db)
    tenx = _company(db, "TENX", None, "Tenx Corp")
    _listed(db, tenx, AIRI_FIRST, 5, 1, sec_filed=AIRI_ANNOUNCED)
    vote = _airi(
        security_id=sid,
        accession="0000000000-26-000021",
        filed=date(2026, 11, 12),
        kind="failed_vote",
        ratio_from=None,
        ratio_to=None,
        document=None,
        passage=VOTE_PASSAGE,
    )
    ten = _airi(
        security_id=tenx, ticker="TENX", accession="0000000000-26-000022", ratio_from=Decimal(10)
    )
    _nightly(runs, [], calloffs=[vote.to_json(), _airi(security_id=sid).to_json(), ten.to_json()])
    body = client.get(BASE).json()
    airi = _find(body, "AIRI", "2026-11-20")[1]["called_off"]
    assert [
        (c["accession"], c["kind"], c["ratio_label"], c["same_ratio"], c["days_from"]) for c in airi
    ] == [
        (AIRI_8K, "decision", "1-for-5 reverse split", True, -1),  # the listed ratio first
        ("0000000000-26-000021", "failed_vote", None, None, -8),  # states none
    ]
    assert (airi[1]["passage"], airi[1]["document"]) == (VOTE_PASSAGE, None)
    (other,) = _find(body, "TENX", "2026-11-20")[1]["called_off"]
    assert (other["ratio_label"], other["same_ratio"]) == ("1-for-10 reverse split", False)
    calloff_env("ALPHADECK_SPLIT_CALLOFF_RATIO", "same")
    body = client.get(BASE).json()
    assert _calloffs(_find(body, "AIRI", "2026-11-20")[1]) == [(AIRI_8K, "nightly", -1)]
    assert _find(body, "TENX", "2026-11-20")[1]["called_off"] is None


def test_runs_written_before_calloffs_name_none_and_the_unplaced_list_is_unchanged(
    client, db, unplaced_home, calloff_env
):
    """A nightly artifact and a check record written before call-off rows existed (no ``calloffs`` key): every
    entry names none, each run counts 0, and the unplaced list reads as it did."""
    _seed_unplaced(db, unplaced_home)
    body = client.get(BASE).json()
    entries = [e for b in ("needs_review", "announced", "confirmed", "set_aside") for e in body[b]]
    assert len(entries) == 9 and all(e["called_off"] is None for e in entries)
    assert [(x["source"], x["rows"], x["calloffs"]) for x in body["unplaced_runs"]] == [
        ("nightly", 4, 0),
        ("check", 1, 0),
    ]
    assert len(body["unplaced"]) == 5 and body["unplaced_error"] is None
