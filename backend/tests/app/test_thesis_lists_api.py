"""Catalyst + kill-criteria authoring (slice A1) — the spine lists get their write surfaces.

Three properties pinned: (1) the PUT endpoints author the catalyst SURFACE + kill criteria and the
call consumes them (the counter-case stops reading "no documented counter-case"); (2) THE WIPE-TRAP,
third instance — a narrative edit (the promote payload, which never carries these lists) can NEVER
wipe them, STRUCTURALLY (upsert no longer touches the two child tables; ``set_catalysts`` /
``set_kill_criteria`` are the sole writers — the ``set_term_set`` guard, not a read-merge); and
(3) the ratify union's new ``catalyst`` variant writes a per-security CONVICTION fact (the Key-1
arming path) with a REQUIRED citation — an uncited catalyst is a bare operator claim, not evidence.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Segment, Thesis
from repositories import thesis_repo

TODAY = date.today()


def _thesis(db, security_id=None) -> Thesis:
    basket = (
        [
            BasketMember(
                ticker="DEVCO",
                role="r",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
                segment="reactors",
            )
        ]
        if security_id
        else []
    )
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="lists authoring",
        narrative="x",
        segments=[Segment(label="reactors")] if security_id else [],
        basket=basket,
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t


def test_put_catalysts_and_kill_criteria_author_the_spine(client, db):
    t = _thesis(db)
    r = client.put(
        f"/theses/{t.id}/catalysts",
        json=[
            {"label": "MU FQ4 earnings", "kind": "earnings", "when_date": "2026-09-24"},
            {"label": "HBM4 capacity announcements", "when_label": "H2"},
        ],
    )
    assert r.status_code == 200
    got = r.json()["catalysts"]
    assert [c["label"] for c in got] == ["MU FQ4 earnings", "HBM4 capacity announcements"]
    assert got[0]["when_date"] == "2026-09-24" and got[1]["when_label"] == "H2"

    r = client.put(
        f"/theses/{t.id}/kill-criteria",
        json=[{"text": "DRAM contract prices roll over two consecutive quarters"}],
    )
    assert r.status_code == 200
    assert [k["text"] for k in r.json()["kill_criteria"]] == [
        "DRAM contract prices roll over two consecutive quarters"
    ]

    # the call CONSUMES them: the counter-case now documents the kill criterion (no more
    # "no documented counter-case" on an authored thesis)
    card = client.get(f"/theses/{t.id}/call", params={"asof": str(TODAY)}).json()
    assert "Kill criteria: DRAM contract prices roll over" in card["counter_case"]


def test_narrative_edit_can_never_wipe_the_authored_lists(client, db):
    """THE WIPE-TRAP regression (third instance): the promote payload owns name/narrative/basket/
    segments and never carries catalysts/kill-criteria — before this slice, the upsert full-replaced
    both child tables from the constructed thesis's EMPTY defaults, so the first narrative edit after
    authoring would have silently wiped them. Now structural: upsert doesn't touch the tables."""
    t = _thesis(db)
    client.put(f"/theses/{t.id}/catalysts", json=[{"label": "NRC license decision"}])
    client.put(f"/theses/{t.id}/kill-criteria", json=[{"text": "license denied"}])

    # the M1b narrative-edit shape: same id, edited narrative, basket+segments resent — NO list fields
    r = client.post(
        "/workbench/theses",
        json={
            "id": str(t.id),
            "name": t.name,
            "narrative": "edited narrative",
            "basket": [],
            "segments": [],
        },
    )
    assert r.status_code == 200

    detail = client.get(f"/theses/{t.id}").json()
    assert detail["narrative"] == "edited narrative"
    assert [c["label"] for c in detail["catalysts"]] == ["NRC license decision"]  # SURVIVED
    assert [k["text"] for k in detail["kill_criteria"]] == ["license denied"]  # SURVIVED


def test_put_replaces_the_whole_list_and_empty_clears(client, db):
    t = _thesis(db)
    client.put(f"/theses/{t.id}/catalysts", json=[{"label": "a"}, {"label": "b"}])
    r = client.put(f"/theses/{t.id}/catalysts", json=[{"label": "only"}])
    assert [c["label"] for c in r.json()["catalysts"]] == ["only"]  # replace, not append
    r = client.put(f"/theses/{t.id}/catalysts", json=[])
    assert r.json()["catalysts"] == []  # the operator's own explicit clear is allowed


def test_ratify_catalyst_writes_the_conviction_fact_that_turns_key_one(client, db, security_id):
    """The arming path: a hand-authored, CITED catalyst fact is a Key-1 conviction the call engine
    consumes — the thesis leaves Incubating on the next read (no extractor, no LLM: the operator
    authored a verifiable event with its citation; the platform times it)."""
    t = _thesis(db, security_id=security_id)
    card = client.get(f"/theses/{t.id}/call", params={"asof": str(TODAY)}).json()
    assert card["state"] == "incubating" and card["key_conviction"]["turned"] is False

    r = client.post(
        "/workbench/facts",
        json={
            "fact_type": "catalyst",
            "security_id": str(security_id),
            "catalyst_type": "contract",
            "grade": "core",
            "label": "10-year offtake agreement signed",
            "source": "ratified",
            "source_ref": "https://example.com/pr/offtake",
            "event_date": str(TODAY - timedelta(days=2)),
        },
    )
    assert r.status_code == 200 and r.json()["fact_type"] == "catalyst"

    card = client.get(f"/theses/{t.id}/call", params={"asof": str(TODAY)}).json()
    assert card["key_conviction"]["turned"] is True  # the authored catalyst IS the conviction key
    assert card["state"] != "incubating"
    assert any("offtake" in (tr.get("label") or "") for tr in card["triggers_fired"])


def test_ratify_catalyst_requires_its_citation(client, db, security_id):
    r = client.post(
        "/workbench/facts",
        json={
            "fact_type": "catalyst",
            "security_id": str(security_id),
            "catalyst_type": "earnings",
            "grade": "flip",
            "label": "uncited claim",
            "source": "ratified",
            "source_ref": "   ",
            "event_date": str(TODAY),
        },
    )
    assert r.status_code == 422
    assert "citation" in r.json()["detail"]
