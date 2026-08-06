"""The SPAC Radar API — the tape read (state derive + joins + match badges) and the reversible
attach/detach pair (#1: attach ⇄ detach round-trips; #10: the radar recommends, the operator's
click decides)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from db.session import DEFAULT_TENANT_ID
from domain.thesis import Thesis
from radar import repo
from repositories import thesis_repo

TODAY = date.today()


def _master(db, cik10: str, ticker: str | None, sector: str | None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, sector, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik10, sector, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _thesis(db, name: str = "Rainbow") -> Thesis:
    t = Thesis(id=uuid.uuid4(), name=name, narrative="x", tenant_id=DEFAULT_TENANT_ID)
    thesis_repo.upsert(db, t)
    db.commit()
    return t


def _event(db, cik10, sid, form, filed, accession, items=None, name="Shell Co"):
    repo.record_event_if_changed(
        db,
        repo.SpacEvent(
            cik=cik10,
            company_name=name,
            form=form,
            filed=filed,
            accession=accession,
            source_ref=f"https://example.test/{accession}",
            items=items,
            security_id=sid,
        ),
    )
    db.commit()


def test_tape_states_joins_and_badges(client, db):
    dead_sid = _master(db, "0000001111", "DEAD", "Blank Checks")
    live_sid = _master(db, "0000002222", "LIVE", "Blank Checks")
    t = _thesis(db)
    # CIK 1111: announced then terminated (the Rev 2 pair — the dead deal must read terminated)
    _event(db, "0000001111", dead_sid, "8-K", TODAY - timedelta(days=30), "acc-1101", ["1.01"])
    _event(db, "0000001111", dead_sid, "8-K", TODAY - timedelta(days=2), "acc-1102", ["1.02"])
    # CIK 2222: a 425 → announced, with a stored thesis match
    _event(db, "0000002222", live_sid, "425", TODAY - timedelta(days=1), "acc-2201")
    repo.record_match_if_changed(
        db,
        repo.SpacMatch(
            thesis_id=t.id,
            cik="0000002222",
            accession="acc-2201",
            matched_signal=["psilocybin"],
            matched_broad=["real-world assets"],
            truncated=False,
            source_ref="https://example.test/acc-2201.txt",
            filed=TODAY - timedelta(days=1),
        ),
    )
    db.commit()

    body = client.get("/radar/spac").json()
    assert body["shells_known"] == 2
    by_acc = {e["accession"]: e for e in body["events"]}
    assert set(by_acc) == {"acc-1101", "acc-1102", "acc-2201"}
    # newest first
    assert body["events"][0]["accession"] == "acc-2201"
    # per-CIK state rides EVERY row of that CIK (derived over full history)
    assert by_acc["acc-1101"]["deal_state"] == "terminated"
    assert by_acc["acc-1102"]["deal_state"] == "terminated"
    assert by_acc["acc-2201"]["deal_state"] == "announced"
    # master join + the match badge
    assert by_acc["acc-2201"]["ticker"] == "LIVE"
    (m,) = by_acc["acc-2201"]["matches"]
    assert m["thesis_name"] == "Rainbow"
    assert m["signal_terms"] == ["psilocybin"] and m["broad_terms"] == ["real-world assets"]
    assert by_acc["acc-2201"]["in_basket_of"] == []  # not attached yet


def test_attach_is_operator_set_idempotent_and_reversible(client, db):
    sid = _master(db, "0000002222", "LIVE", "Blank Checks")
    t = _thesis(db)
    _event(db, "0000002222", sid, "425", TODAY - timedelta(days=1), "acc-2201")
    repo.record_match_if_changed(
        db,
        repo.SpacMatch(
            thesis_id=t.id,
            cik="0000002222",
            accession="acc-2201",
            matched_signal=["psilocybin"],
            matched_broad=[],
            truncated=False,
            source_ref="u",
            filed=TODAY - timedelta(days=1),
        ),
    )
    db.commit()
    payload = {"thesis_id": str(t.id), "cik": "0000002222"}

    r = client.post("/radar/spac/attach", json=payload).json()
    assert r["added"] is True and r["ticker"] == "LIVE"
    loaded = thesis_repo.get(db, t.id)
    (member,) = loaded.basket
    assert member.security_id == sid
    assert member.archetype is None  # un-decided all the way through the spine (item F)
    assert member.authored_by.value == "operator_set"  # the CLICK is the operator's decision (#10)
    assert member.surfaced_terms == ["psilocybin"]  # provenance frozen from the stored match

    # idempotent second attach — no duplicate member
    r2 = client.post("/radar/spac/attach", json=payload).json()
    assert r2["already"] is True and r2["added"] is False
    assert len(thesis_repo.get(db, t.id).basket) == 1

    # the tape now shows the holding (the added-toggle's read)
    body = client.get("/radar/spac").json()
    assert body["events"][0]["in_basket_of"] == [str(t.id)]

    # detach = the visible inverse (#1) — back to the prior state
    r3 = client.post("/radar/spac/detach", json=payload).json()
    assert r3["removed"] is True
    assert thesis_repo.get(db, t.id).basket == []
    # detach again: idempotent no-op
    assert client.post("/radar/spac/detach", json=payload).json()["removed"] is False


def test_attach_rejects_unknown_cik_and_unlisted(client, db):
    t = _thesis(db)
    r = client.post("/radar/spac/attach", json={"thesis_id": str(t.id), "cik": "0000009999"})
    assert r.status_code == 422
    _master(db, "0000008888", None, "Blank Checks")  # a master row with NO ticker
    r2 = client.post("/radar/spac/attach", json={"thesis_id": str(t.id), "cik": "0000008888"})
    assert r2.status_code == 422
