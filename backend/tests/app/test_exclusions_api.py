"""Excluded-name permanence (#7). The operator's NO is durable: the exclusion set (with the optional
"rejected because X") persists per thesis, survives a narrative-edit promote STRUCTURALLY (upsert
never names the table — the term_set guard's fourth application), and rides ThesisDetail so the
editor can seed its greyed state on the next session/re-draft. THE #9 LINE, stated as a test-adjacent
fact: nothing in discovery/classify reads this table — the editor greys; nothing filters."""

from __future__ import annotations

import uuid

from db.session import DEFAULT_TENANT_ID
from domain.thesis import Thesis
from repositories import thesis_repo


def _thesis(db) -> Thesis:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="exclusions",
        narrative="x",
        segments=[],
        basket=[],
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t


def test_exclusions_roundtrip_with_reasons(client, db, security_id):
    t = _thesis(db)
    r = client.put(
        f"/theses/{t.id}/exclusions",
        json=[
            {
                "security_id": str(security_id),
                "ticker": "JUNK",
                "reason": "acronym collision — not a memory name",
            }
        ],
    )
    assert r.status_code == 200
    got = r.json()["exclusions"]
    assert len(got) == 1
    assert got[0]["security_id"] == str(security_id)
    assert got[0]["ticker"] == "JUNK"
    assert got[0]["reason"] == "acronym collision — not a memory name"

    # the detail read (the editor's load path) carries it too
    detail = client.get(f"/theses/{t.id}").json()
    assert [e["ticker"] for e in detail["exclusions"]] == ["JUNK"]


def test_exclusions_full_replace_and_reinclude_drops(client, db, security_id):
    t = _thesis(db)
    client.put(
        f"/theses/{t.id}/exclusions",
        json=[{"security_id": str(security_id), "ticker": "JUNK", "reason": None}],
    )
    # the operator re-includes (the editor sends the set WITHOUT it) — the NO is withdrawn
    r = client.put(f"/theses/{t.id}/exclusions", json=[])
    assert r.status_code == 200 and r.json()["exclusions"] == []


def test_a_narrative_edit_promote_cannot_wipe_the_pruning(client, db, security_id):
    """The wipe-guard, fourth application: the promote payload has no exclusions field and the
    upsert never touches the table — a narrative edit keeps the operator's NO intact."""
    t = _thesis(db)
    client.put(
        f"/theses/{t.id}/exclusions",
        json=[{"security_id": str(security_id), "ticker": "JUNK", "reason": "off-thesis"}],
    )
    r = client.post(
        "/workbench/theses",
        json={"id": str(t.id), "name": t.name, "narrative": "edited", "basket": [], "segments": []},
    )
    assert r.status_code == 200
    detail = client.get(f"/theses/{t.id}").json()
    assert detail["narrative"] == "edited"
    assert [e["reason"] for e in detail["exclusions"]] == ["off-thesis"]  # SURVIVED


def test_unknown_security_is_rejected_fail_closed(client, db):
    """Bound #2 (the promote route's own guard, applied here): a caller-supplied id that isn't in
    this tenant's master 404s BEFORE any write — never a junk row behind an FK error."""
    t = _thesis(db)
    r = client.put(
        f"/theses/{t.id}/exclusions",
        json=[{"security_id": str(uuid.uuid4()), "ticker": "GHOST", "reason": None}],
    )
    assert r.status_code == 404
    assert client.get(f"/theses/{t.id}").json()["exclusions"] == []  # nothing written
