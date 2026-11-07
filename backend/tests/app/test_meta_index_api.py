"""The meta index card switch over the API (DB tests).

``PUT /theses/{id}/meta-index`` is the flag's ONLY writer (``thesis_repo.set_meta_index``): ``upsert`` never names
the column, so a full-replace promote re-send can never flip it — the ``archived_at`` structural guard, applied
again. On, none of the card's names count toward the call (the live call path reads the flag through the mapper);
off, it is a normal card again on the next read. Nothing is deleted either way.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from db.session import DEFAULT_TENANT_ID
from domain.scopes import WORKSPACE_ANY
from domain.thesis import BasketMember, Segment, Thesis
from repositories import thesis_repo
from tests.editions.fixtures import seed_big_buy, seed_tape


def _thesis(db, security_id) -> Thesis:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Meta index card",
        narrative="A broad list for watching where money moves.",
        segments=[Segment(label="Makers")],
        basket=[
            BasketMember(ticker="DEVCO", role="the name", security_id=security_id, segment="Makers")
        ],
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t


def _flag(client, tid) -> bool:
    return client.get(f"/theses/{tid}").json()["meta_index"]


# --- the route --------------------------------------------------------------------------------------------


def test_the_switch_defaults_off_on_the_list_and_the_detail(client, db, security_id):
    t = _thesis(db, security_id)
    (item,) = client.get("/theses").json()
    assert item["meta_index"] is False
    assert _flag(client, t.id) is False


def test_set_on_then_off_through_the_route(client, db, security_id):
    t = _thesis(db, security_id)

    r = client.put(f"/theses/{t.id}/meta-index", json={"meta_index": True})
    assert r.status_code == 200
    assert r.json()["meta_index"] is True and r.json()["id"] == str(t.id)
    assert _flag(client, t.id) is True
    assert client.get("/theses").json()[0]["meta_index"] is True

    r = client.put(f"/theses/{t.id}/meta-index", json={"meta_index": False})
    assert r.status_code == 200 and r.json()["meta_index"] is False
    assert _flag(client, t.id) is False
    # nothing was deleted either way: the card's basket and links are whole
    detail = client.get(f"/theses/{t.id}").json()
    assert [m["ticker"] for m in detail["basket"]] == ["DEVCO"]
    assert detail["segments"][0]["label"] == "Makers"


def test_the_switch_is_idempotent(client, db, security_id):
    t = _thesis(db, security_id)
    for _ in range(2):
        assert (
            client.put(f"/theses/{t.id}/meta-index", json={"meta_index": True}).status_code == 200
        )
    assert _flag(client, t.id) is True


def test_an_unknown_card_is_404(client, db):
    r = client.put(f"/theses/{uuid.uuid4()}/meta-index", json={"meta_index": True})
    assert r.status_code == 404


def test_the_body_must_carry_the_flag(client, db, security_id):
    t = _thesis(db, security_id)
    assert client.put(f"/theses/{t.id}/meta-index", json={}).status_code == 422
    assert _flag(client, t.id) is False


# --- WIPE-SAFETY: a full re-send never flips it --------------------------------------------------------------


def test_a_full_replace_promote_resend_never_flips_the_switch(client, db, security_id):
    """THE test that matters most. The Workbench re-sends the whole card on every Save (name, narrative, basket,
    segments) and the payload never carries the switch: the promote builds a fresh ``Thesis`` (switch off) and
    ``upsert``s it. The switch must survive — and the promote's response must echo the STORED value.
    """
    t = _thesis(db, security_id)
    client.put(f"/theses/{t.id}/meta-index", json={"meta_index": True})
    detail = client.get(f"/theses/{t.id}").json()

    resend = {
        "id": str(t.id),
        "name": detail["name"],
        "narrative": "edited narrative",
        "ticker": detail["ticker"],
        "basket": detail["basket"],
        "segments": detail["segments"],
    }
    assert "meta_index" not in resend
    r = client.post("/workbench/theses", json=resend)
    assert r.status_code == 200, r.text
    assert (
        r.json()["meta_index"] is True
    )  # the response echoes the stored switch, never the payload default

    assert _flag(client, t.id) is True
    assert client.get("/theses").json()[0]["meta_index"] is True
    assert (
        client.get(f"/theses/{t.id}").json()["narrative"] == "edited narrative"
    )  # the edit landed


def test_a_resend_that_echoes_the_detail_with_meta_index_false_still_cannot_flip_it(
    client, db, security_id
):
    """A client that echoes the whole detail back — ``meta_index: false`` included — is ignored on that key:
    the promote payload has no such field and ``upsert`` never names the column."""
    t = _thesis(db, security_id)
    client.put(f"/theses/{t.id}/meta-index", json={"meta_index": True})
    detail = client.get(f"/theses/{t.id}").json()
    echoed = {**detail, "meta_index": False, "narrative": "echoed"}
    assert client.post("/workbench/theses", json=echoed).status_code == 200
    assert _flag(client, t.id) is True


def test_upsert_never_names_the_column(db, security_id):
    """The structural guard at the repository: a promote-shaped ``upsert`` of a FRESH ``Thesis`` (switch off by
    default) leaves a stored ``true`` alone; only ``set_meta_index`` writes it."""
    t = _thesis(db, security_id)
    thesis_repo.set_meta_index(db, t.id, True)
    db.commit()
    thesis_repo.upsert(db, t.model_copy(update={"narrative": "re-saved", "meta_index": False}))
    db.commit()
    got = thesis_repo.get(db, t.id, workspace=WORKSPACE_ANY)
    assert got.meta_index is True and got.narrative == "re-saved"
    # the history read carries it too (card state, read as it is now)
    asof = thesis_repo.get_asof(
        db, t.id, datetime(2000, 1, 1, tzinfo=timezone.utc), workspace=WORKSPACE_ANY
    )
    assert asof is not None and asof.meta_index is True
    db.commit()


# --- the live call reads it ---------------------------------------------------------------------------------


def test_the_live_call_of_a_meta_index_card_counts_none_of_its_names(client, db, security_id):
    """Through the real serve path (``GET /theses/{id}/call`` -> ``call_for_thesis`` -> the one seam): a big
    open-market buy warms the card on its public night when the switch is off (the non-vacuity twin); on, the
    same night is Incubating with no trigger; off again, it warms again — reversible, nothing remembered.
    """
    t = _thesis(db, security_id)
    seed_tape(db, security_id)
    seed_big_buy(db, security_id)  # accepted 06-17 after the close: public that night
    db.commit()
    night = date(2026, 6, 17).isoformat()

    def call():
        r = client.get(f"/theses/{t.id}/call", params={"asof": night})
        assert r.status_code == 200, r.text
        return r.json()

    off = call()
    assert off["state"] == "warming" and off["key_conviction"]["turned"] is True

    client.put(f"/theses/{t.id}/meta-index", json={"meta_index": True})
    on = call()
    assert on["state"] == "incubating"
    assert on["triggers_fired"] == [] and on["armed_members"] == [] and on["watch_members"] == []
    assert on["key_conviction"]["turned"] is False

    client.put(f"/theses/{t.id}/meta-index", json={"meta_index": False})
    assert call() == off
