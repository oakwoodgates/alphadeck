"""Thesis archive (slice B — board hygiene). Archive, NEVER delete: the thesis leaves the default
list and the daily cron's walk (its calls-of-record stop accumulating — the Scoreboard's data stays
clean), but the spine + calls log + decision log all stay, and unarchive restores it whole. The
column's sole writer is ``set_archived`` — ``upsert`` never names it, so a promote can neither
archive nor RESURRECT one (the term_set structural guard, applied again)."""

from __future__ import annotations

import uuid

from db.session import DEFAULT_TENANT_ID
from domain.thesis import Thesis
from pipeline.daily import run_daily
from repositories import thesis_repo


def _thesis(db, name: str) -> Thesis:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[],
        basket=[],
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t


def test_archive_hides_from_the_default_list_and_unarchive_restores(client, db):
    live = _thesis(db, "live one")
    parked = _thesis(db, "parked one")

    r = client.post(f"/theses/{parked.id}/archive")
    assert r.status_code == 200 and r.json()["archived"] is True

    names = [t["name"] for t in client.get("/theses").json()]
    assert "live one" in names and "parked one" not in names  # the default list skips it

    everything = client.get("/theses", params={"include_archived": "true"}).json()
    flags = {t["name"]: t["archived"] for t in everything}
    assert flags == {"live one": False, "parked one": True}  # visible + flagged, never vanished

    # archived != gone: the detail (spine, history) stays reachable
    assert client.get(f"/theses/{parked.id}").status_code == 200

    assert client.post(f"/theses/{parked.id}/unarchive").json()["archived"] is False
    assert "parked one" in [t["name"] for t in client.get("/theses").json()]
    assert live is not None  # (silence the linter's unused warning honestly)


def test_daily_cron_skips_archived_theses(client, db):
    """The hygiene payoff: an archived test basket stops accumulating calls-of-record — the walk
    (list_all's default) never visits it. COUNT the table, not just the run report."""
    live = _thesis(db, "walked")
    parked = _thesis(db, "skipped")
    client.post(f"/theses/{parked.id}/archive")

    results = run_daily(db, allow_live=False)
    assert [r.name for r in results] == ["walked"]  # the walk itself skipped the archived one

    with db.cursor() as cur:
        cur.execute("SELECT thesis_id FROM calls")
        logged = {r["thesis_id"] for r in cur.fetchall()}
    assert live.id in logged and parked.id not in logged


def test_promote_cannot_resurrect_an_archived_thesis(client, db):
    """The structural guard: ``upsert`` never names ``archived_at``, so a promote-shaped edit of an
    archived thesis (a stale tab, a workbench draft save) cannot silently put it back on the Board.
    """
    t = _thesis(db, "stays parked")
    client.post(f"/theses/{t.id}/archive")

    r = client.post(
        "/workbench/theses",
        json={"id": str(t.id), "name": t.name, "narrative": "edited", "basket": [], "segments": []},
    )
    assert r.status_code == 200

    assert [x["name"] for x in client.get("/theses").json()] == []  # still archived
    got = client.get("/theses", params={"include_archived": "true"}).json()
    assert (
        got[0]["archived"] is True and client.get(f"/theses/{t.id}").json()["narrative"] == "edited"
    )
