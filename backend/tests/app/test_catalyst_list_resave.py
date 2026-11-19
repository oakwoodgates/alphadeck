"""A card's catalyst calendar can be saved again whatever it already holds.

``PUT /theses/{id}/catalysts`` replaces the whole list, so the editor re-sends every stored row. A stored row a
little outside the old request limits (a date more than a year ahead, a label over 200 characters) used to make
every save of that card's list fail with 422. The calendar's two bounds are wider (``CATALYST_DATE_AHEAD_DAYS``,
``CATALYST_LABEL_MAX``) and still bounded.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from domain import text_types as tt
from domain.market_time import market_today
from pipeline import seed
from tests.thesis_catalyst_fixtures import card


def _rows(db, thesis_id) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, label, when_date FROM catalyst WHERE thesis_id = %s ORDER BY ordinal",
            (thesis_id,),
        )
        return cur.fetchall()


def _store_row(db, thesis_id, *, label: str, when_date=None) -> None:
    """A stored calendar row, written directly (as old data is)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, coalesce(max(ordinal), -1) + 1 AS nxt FROM catalyst "
            "WHERE thesis_id = %s GROUP BY tenant_id",
            (thesis_id,),
        )
        row = cur.fetchone()
        cur.execute("SELECT gen_random_uuid() AS id")
        new_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO catalyst (id, tenant_id, thesis_id, ordinal, label, when_date) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (new_id, row["tenant_id"], thesis_id, row["nxt"], label, when_date),
        )
    db.commit()


def _resend(client, tid, extra: list[dict]):
    got = client.get(f"/theses/{tid}")
    assert got.status_code == 200, got.text
    return client.put(f"/theses/{tid}/catalysts", json=got.json()["catalysts"] + extra)


def test_a_stored_row_dated_over_a_year_ahead_does_not_block_a_save(client, db, pinned_today):
    tid, _sids, _entry = card(db)
    far = pinned_today + timedelta(days=700)
    _store_row(db, tid, label="Bridge ends unless extended again", when_date=far)
    before = _rows(db, tid)
    r = _resend(client, tid, [{"label": "a new row"}])
    assert r.status_code == 200, r.text
    after = _rows(db, tid)
    assert len(after) == len(before) + 1
    assert [(x["label"], x["when_date"]) for x in after[:-1]] == [
        (x["label"], x["when_date"]) for x in before
    ]
    assert after[-1]["label"] == "a new row"


def test_a_stored_227_character_label_does_not_block_a_save(client, db, pinned_today):
    tid, _sids, _entry = card(db)
    long_label = "L" * 227
    _store_row(db, tid, label=long_label)
    before = _rows(db, tid)
    r = _resend(client, tid, [{"label": "a new row"}])
    assert r.status_code == 200, r.text
    after = _rows(db, tid)
    assert len(after) == len(before) + 1
    assert long_label in [x["label"] for x in after]


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda today: {"when_date": today + timedelta(days=3_661)}, id="too_far"),
        pytest.param(lambda today: {"when_date": "1989-12-31"}, id="before_1990"),
        pytest.param(lambda today: {"label": "x" * 501}, id="label_501"),
    ],
)
def test_the_calendar_bounds_still_bound(client, db, pinned_today, build):
    tid, _sids, _entry = card(db)
    before = _rows(db, tid)
    row = {"label": "ok", **build(pinned_today)}
    r = client.put(f"/theses/{tid}/catalysts", json=[row])
    assert r.status_code == 422, r.text
    assert _rows(db, tid) == before


def test_the_edges_of_the_bounds_are_accepted(client, db, pinned_today):
    tid, _sids, _entry = card(db)
    r = client.put(
        f"/theses/{tid}/catalysts",
        json=[
            {"label": "x" * 500, "when_date": str(pinned_today + timedelta(days=3_660))},
            {"label": "old", "when_date": "1990-01-01"},
        ],
    )
    assert r.status_code == 200, r.text


def test_what_the_card_returns_is_what_the_save_accepts(client, db, pinned_today):
    """The round trip: every seeded card's ``catalysts`` from ``GET /theses/{id}``, sent back unchanged, is
    accepted and changes nothing (the nuclear card's theme row has a 227-character label)."""
    ids = [
        seed.seed_hims(db),
        seed.seed_nuclear(db),
        seed.seed_unh(db),
    ]
    seed.seed_nuclear_theme_conviction(db)
    db.commit()
    assert any(len(c["label"]) > tt.LINE_MAX for c in client.get(f"/theses/{ids[1]}").json()["catalysts"])
    for tid in ids:
        sent = client.get(f"/theses/{tid}").json()["catalysts"]
        before = _rows(db, tid)
        r = client.put(f"/theses/{tid}/catalysts", json=sent)
        assert r.status_code == 200, (tid, r.text)
        assert r.json()["catalysts"] == sent
        assert _rows(db, tid) == before



    assert market_today() == pinned_today
