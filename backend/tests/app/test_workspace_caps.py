"""Workspace caps (V2 W7c, docs/AUTH.md §Workspace caps): live cards, distinct companies on live cards, and search
terms per card, enforced by ONE rule (``app.workspace_caps``) at every route that adds one.

The rule: a request is refused (409, a plain sentence naming the cap, the count and the limit) only when a count would
end ABOVE its cap AND above the count before the request. So a workspace already over a cap keeps working: it reads,
edits, removes, archives and re-promotes; only an addition past the cap is refused. A refusal writes nothing and drops
nothing (recall is sacred): the whole request is refused. NULL caps never refuse.

Built WITHOUT the suite's signed-in override: real users signed in through ``POST /auth/login`` (the
``test_user_fetches`` pattern). No test reaches a vendor: the fast-lane kick and the keyword model are faked.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import CurrentUser
from app.deps import get_conn, get_keyword_client
from app.main import app
from app.schemas_api import PromoteThesisRequest
from db.session import connect
from domain.enums import Authorship, TermTier
from domain.settings import get_settings
from domain.thesis import TermSetEntry
from repositories import thesis_repo
from tests.app.test_user_fetches import (
    PASSWORD,
    _attach_fund,
    _attach_spac,
    _client,
    _fund,
    _security,
    _workspace,
    auth_user_id,
)
from tests.auth_fixtures import sign_out
from tests.thread_support import BARRIER_TIMEOUT_S, join_all


class _Job:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


@pytest.fixture
def kicked(monkeypatch):
    """The fast-lane kick faked (no background ingest, no vendor); every kick recorded. The lane's plan is real."""
    from app.routers import workbench as wb

    kicks: list[set] = []

    def kick(tid, ids, tenant_id=None):
        kicks.append(set(ids))
        return _Job(f"job-{len(kicks)}")

    monkeypatch.setattr(wb, "_kick_member_ingest", kick)
    return kicks


@pytest.fixture
def ws(db, kicked, monkeypatch):
    """A plain user P on workspace ``w`` (caps NULL until a test sets them)."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    w = _workspace(db, "caps P")
    auth.create_user(db, email="p@caps.test", display_name="P", password=PASSWORD, workspace_id=w)
    db.commit()
    yield {"p": _client("p@caps.test"), "w": w, "p_id": auth_user_id(db, "p@caps.test")}
    app.dependency_overrides.clear()
    _set_caps(db, w)  # the shared fixture keeps tenant rows: leave no capped workspace behind
    auth.reset_throttle()
    get_settings.cache_clear()


def _set_caps(db, w, *, cards=None, companies=None, terms=None) -> None:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE tenant SET cap_live_cards = %s, cap_companies = %s, cap_terms_per_card = %s "
            "WHERE id = %s",
            (cards, companies, terms, w),
        )
    db.commit()


def _secs(db, n: int, prefix: str = "C") -> list[uuid.UUID]:
    return [_security(db, f"{prefix}{i:03d}", cik=None) for i in range(n)]


def _body(db, ids, *, card_id=None, name=None) -> dict:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT id, ticker FROM security_master WHERE id = ANY(%s)", (list(ids),))
        ticker = {r["id"]: r["ticker"] for r in cur.fetchall()}
    db.rollback()
    return {
        "id": str(card_id) if card_id else None,
        "name": name or f"Caps card {uuid.uuid4().hex[:8]}",
        "narrative": "a synthetic narrative for the caps proof",
        "basket": [
            {"ticker": ticker[sid], "role": "the name", "security_id": str(sid)} for sid in ids
        ],
        "segments": [],
    }


def _promote(c: TestClient, db, ids, **kw):
    return c.post("/workbench/theses", json=_body(db, ids, **kw))


def _card(c: TestClient, db, ids) -> uuid.UUID:
    r = _promote(c, db, ids)
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


def _snapshot(db, w, uid) -> dict:
    """Every row a refused request could have written, scoped to this workspace and its user."""
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM thesis WHERE tenant_id = %(w)s) AS theses, "
            "(SELECT count(*) FROM thesis WHERE tenant_id = %(w)s AND archived_at IS NULL) AS live, "
            "(SELECT count(*) FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            " WHERE t.tenant_id = %(w)s) AS members, "
            "(SELECT coalesce(sum(jsonb_array_length(coalesce(term_set, '[]'::jsonb))), 0) FROM thesis "
            " WHERE tenant_id = %(w)s) AS terms, "
            "(SELECT count(*) FROM security_onboarding) AS onboarding, "
            "(SELECT coalesce(sum(count), 0) FROM user_action_count WHERE user_id = %(u)s) AS allowance",
            {"w": w, "u": uid},
        )
        row = dict(cur.fetchone())
    db.rollback()
    return row


def _terms(n: int, prefix: str = "term") -> list[TermSetEntry]:
    return [
        TermSetEntry(
            term=f"{prefix} {i}", tier=TermTier.SIGNAL, authored_by=Authorship.OPERATOR_SET
        )
        for i in range(n)
    ]


def _store_terms(db, card, entries) -> None:
    db.rollback()
    thesis_repo.set_term_set(db, card, entries)
    db.commit()


def _archive(c, card):
    return c.post(f"/theses/{card}/archive")


def _unarchive(c, card):
    return c.post(f"/theses/{card}/unarchive")


# --- defaults for a new workspace ----------------------------------------------------------------------------------


@pytest.fixture
def users_conn(db):
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    db.commit()
    yield db
    db.rollback()
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
        cur.execute("DELETE FROM tenant WHERE name LIKE %s", ("%'s workspace",))
    db.commit()


def _caps_of(db, w) -> tuple:
    with db.cursor() as cur:
        cur.execute(
            "SELECT cap_live_cards, cap_companies, cap_terms_per_card FROM tenant WHERE id = %s",
            (w,),
        )
        r = cur.fetchone()
    return (r["cap_live_cards"], r["cap_companies"], r["cap_terms_per_card"])


def test_a_new_workspace_gets_the_default_caps(users_conn, monkeypatch):
    from pipeline import users

    for var in (
        "ALPHADECK_WORKSPACE_CAP_LIVE_CARDS_DEFAULT",
        "ALPHADECK_WORKSPACE_CAP_COMPANIES_DEFAULT",
        "ALPHADECK_WORKSPACE_CAP_TERMS_PER_CARD_DEFAULT",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    uid, _ = users.create_user(users_conn, email="new@caps.test", display_name="New")
    users_conn.commit()
    w = auth.find_user_by_email(users_conn, "new@caps.test")["workspace_id"]
    assert _caps_of(users_conn, w) == (25, 500, 200)


def test_a_zero_default_writes_no_cap(users_conn, monkeypatch):
    from pipeline import users

    monkeypatch.setenv("ALPHADECK_WORKSPACE_CAP_LIVE_CARDS_DEFAULT", "0")
    monkeypatch.setenv("ALPHADECK_WORKSPACE_CAP_COMPANIES_DEFAULT", "7")
    monkeypatch.setenv("ALPHADECK_WORKSPACE_CAP_TERMS_PER_CARD_DEFAULT", "0")
    get_settings.cache_clear()
    try:
        users.create_user(users_conn, email="zero@caps.test", display_name="Zero")
        users_conn.commit()
        w = auth.find_user_by_email(users_conn, "zero@caps.test")["workspace_id"]
        assert _caps_of(users_conn, w) == (None, 7, None)
    finally:
        get_settings.cache_clear()


def test_attaching_a_user_to_an_existing_workspace_leaves_its_caps(users_conn):
    from pipeline import users

    w = _workspace(users_conn, "caps existing")
    users_conn.commit()
    users.create_user(users_conn, email="old@caps.test", display_name="Old", workspace_id=w)
    users_conn.commit()
    assert _caps_of(users_conn, w) == (None, None, None)


# --- live cards ----------------------------------------------------------------------------------------------------


def test_a_promote_creating_a_card_past_the_live_card_cap_is_409_and_writes_nothing(ws, db):
    p, w = ws["p"], ws["w"]
    ids = _secs(db, 3)
    _card(p, db, ids[:1])
    _card(p, db, ids[1:2])
    _set_caps(db, w, cards=2)
    before = _snapshot(db, w, ws["p_id"])
    r = _promote(p, db, ids[2:])
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "This workspace can have at most 2 live cards and has 2. Archive a card to start another."
    )
    assert _snapshot(db, w, ws["p_id"]) == before


def test_archived_cards_do_not_count(ws, db):
    p, w = ws["p"], ws["w"]
    ids = _secs(db, 3)
    a = _card(p, db, ids[:1])
    _card(p, db, ids[1:2])
    _set_caps(db, w, cards=2, companies=2)
    assert _archive(p, a).status_code == 200
    # one live card and one live company: a new card with a new company fits both caps
    assert _promote(p, db, ids[2:]).status_code == 200


def test_unarchive_past_the_live_card_cap_is_409(ws, db):
    p, w = ws["p"], ws["w"]
    ids = _secs(db, 3)
    a = _card(p, db, ids[:1])
    assert _archive(p, a).status_code == 200
    _card(p, db, ids[1:2])
    _card(p, db, ids[2:])
    _set_caps(db, w, cards=2)
    before = _snapshot(db, w, ws["p_id"])
    r = _unarchive(p, a)
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Restoring this card would make 3 live cards; this workspace's limit is 2. "
        "Archive another card first."
    )
    assert _snapshot(db, w, ws["p_id"]) == before


def test_unarchive_past_the_company_cap_is_409(ws, db):
    p, w = ws["p"], ws["w"]
    ids = _secs(db, 4)
    a = _card(p, db, ids[:2])
    assert _archive(p, a).status_code == 200
    _card(p, db, ids[2:])
    _set_caps(db, w, companies=3)
    r = _unarchive(p, a)
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Restoring this card would put 4 companies on your live cards; this workspace's limit is 3."
    )
    # a card whose companies are all on another live card restores at the cap: nothing new is added
    _set_caps(db, w, companies=2)
    b = _card(p, db, ids[2:3])
    assert _archive(p, b).status_code == 200
    assert _unarchive(p, b).status_code == 200


# --- companies -----------------------------------------------------------------------------------------------------


def test_a_promote_adding_companies_past_the_cap_is_409_naming_the_counts(ws, db):
    p, w = ws["p"], ws["w"]
    ids = _secs(db, 6)
    card = _card(p, db, ids[:3])
    _card(p, db, ids[2:4])  # ids[2] on both cards counts once: 4 distinct companies
    _set_caps(db, w, companies=4)
    before = _snapshot(db, w, ws["p_id"])
    r = _promote(p, db, ids, card_id=card)  # the first card grows to all six
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Saving would put 6 companies on your live cards; this workspace's limit is 4. "
        "Remove 2 or archive a card."
    )
    assert _snapshot(db, w, ws["p_id"]) == before
    # recall: nothing was dropped from the stored card
    assert {m.security_id for m in thesis_repo.get(db, card, workspace=w).basket} == set(ids[:3])
    db.rollback()


def test_a_new_card_past_the_company_cap_is_409_and_spends_no_allowance(ws, db, monkeypatch):
    p, w = ws["p"], ws["w"]
    monkeypatch.setenv("ALPHADECK_ALLOWANCE_PRICE_PULL_PER_DAY", "100")
    get_settings.cache_clear()
    ids = _secs(db, 3)
    _card(p, db, ids[:2])
    _set_caps(db, w, companies=2)
    before = _snapshot(db, w, ws["p_id"])
    r = _promote(p, db, ids[2:])
    assert r.status_code == 409
    assert "would put 3 companies" in r.json()["detail"]
    assert _snapshot(db, w, ws["p_id"]) == before


def test_an_over_cap_workspace_keeps_working(ws, db):
    """Already above every cap (the founding workspace's shape): reads, a narrative edit, a re-promote that keeps or
    shrinks the basket, a name swapped within the count, archive and restore-within-count all pass; only an
    addition past a cap is refused."""
    p, w = ws["p"], ws["w"]
    ids = _secs(db, 7)
    a = _card(p, db, ids[:3])
    b = _card(p, db, ids[3:5])
    c = _card(p, db, ids[5:6])
    _store_terms(db, a, _terms(5))
    _set_caps(db, w, cards=1, companies=2, terms=2)
    assert p.get("/theses").status_code == 200
    assert p.get(f"/theses/{a}").status_code == 200
    # the same basket, a new narrative: never refused
    body = _body(db, ids[:3], card_id=a, name="renamed")
    body["narrative"] = "an edited narrative"
    assert p.post("/workbench/theses", json=body).status_code == 200
    # a save that removes names passes
    assert _promote(p, db, ids[:2], card_id=a).status_code == 200
    # a name NEW to the workspace raises the count (5 -> 6) past the cap: refused
    r = _promote(p, db, [ids[0], ids[1], ids[6]], card_id=a)
    assert r.status_code == 409
    # swapping a name for one already on another live card keeps the count: passes
    assert _promote(p, db, [ids[0], ids[3]], card_id=a).status_code == 200
    # terms: removing passes, adding past the cap is refused
    keep = [{"term": f"term {i}", "tier": "signal"} for i in range(4)]
    assert p.put(f"/workbench/theses/{a}/terms/edit", json={"terms": keep}).status_code == 200
    more = keep + [{"term": "term new", "tier": "signal"}]
    r = p.put(f"/workbench/theses/{a}/terms/edit", json={"terms": more})
    assert r.status_code == 409
    # archive, then restore: back to the count it had, still over the card cap -> refused (3 > 1 and 3 > 2)
    assert _archive(p, c).status_code == 200
    assert _unarchive(p, c).status_code == 409
    # a restore of a card that is already live changes nothing and passes
    assert _unarchive(p, b).status_code == 200


def test_null_caps_never_refuse(ws, db):
    p = ws["p"]
    ids = _secs(db, 6)
    for i in range(3):
        _card(p, db, ids[i * 2 : i * 2 + 2])
    card = _card(p, db, ids)
    _store_terms(db, card, _terms(30))
    more = [{"term": f"term {i}", "tier": "signal"} for i in range(40)]
    assert p.put(f"/workbench/theses/{card}/terms/edit", json={"terms": more}).status_code == 200


# --- the fund and SPAC attaches ------------------------------------------------------------------------------------


def test_fund_and_spac_attach_respect_the_company_cap(ws, db):
    p, w = ws["p"], ws["w"]
    ids = _secs(db, 2)
    card = _card(p, db, ids)
    _fund(db)
    _security(db, "SPCX", cik="0004440001")
    _set_caps(db, w, companies=2)
    before = _snapshot(db, w, ws["p_id"])
    r = _attach_fund(p, card)
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Adding this fund would put 3 companies on your live cards; this workspace's limit is 2."
    )
    r = _attach_spac(p, card, "0004440001")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Adding this company would put 3 companies on your live cards; this workspace's limit is 2."
    )
    assert _snapshot(db, w, ws["p_id"]) == before
    # under the cap both pass
    _set_caps(db, w, companies=4)
    assert _attach_fund(p, card).status_code == 200
    assert _attach_spac(p, card, "0004440001").status_code == 200


def test_attaching_a_company_already_on_another_live_card_passes_at_the_cap(ws, db):
    p, w = ws["p"], ws["w"]
    sid = _security(db, "SPCX", cik="0004440001")
    ids = _secs(db, 1)
    _card(p, db, [sid])
    other = _card(p, db, ids)
    _set_caps(db, w, companies=2)
    r = _attach_spac(p, other, "0004440001")
    assert r.status_code == 200 and r.json()["added"] is True


# --- terms ---------------------------------------------------------------------------------------------------------


def test_edit_terms_past_the_cap_is_409(ws, db):
    p, w = ws["p"], ws["w"]
    card = _card(p, db, _secs(db, 1))
    _store_terms(db, card, _terms(2))
    _set_caps(db, w, terms=3)
    before = _snapshot(db, w, ws["p_id"])
    edits = [{"term": f"term {i}", "tier": "signal"} for i in range(5)]
    r = p.put(f"/workbench/theses/{card}/terms/edit", json={"terms": edits})
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "A card can carry at most 3 search terms; this one would have 5. Remove 2."
    )
    assert _snapshot(db, w, ws["p_id"]) == before
    assert (
        p.put(f"/workbench/theses/{card}/terms/edit", json={"terms": edits[:3]}).status_code == 200
    )


class _NeverCalled:
    def draft_structured(self, **kw):
        raise AssertionError("the keyword model was called")


class _Proposes:
    def __init__(self, broad: list[str]) -> None:
        self.broad = broad

    def draft_structured(self, **kw):
        return {"signal": [], "broad": self.broad}


def test_produce_terms_refuses_before_the_llm_when_the_operators_terms_exceed_the_cap(ws, db):
    p, w = ws["p"], ws["w"]
    card = _card(p, db, _secs(db, 1))
    _store_terms(db, card, _terms(3))
    _set_caps(db, w, terms=3)
    app.dependency_overrides[get_keyword_client] = lambda: _NeverCalled()
    before = _snapshot(db, w, ws["p_id"])
    r = p.post(f"/workbench/theses/{card}/terms", json={"seeds": ["psilocybin"]})
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "A card can carry at most 3 search terms; this one would have at least 4. Remove 1."
    )
    after = _snapshot(db, w, ws["p_id"])
    assert (
        after["allowance"] == before["allowance"] + 1
    )  # the term_run unit stays spent (allowance.py)
    assert {k: v for k, v in after.items() if k != "allowance"} == {
        k: v for k, v in before.items() if k != "allowance"
    }


def test_produce_terms_stores_nothing_when_the_generated_set_exceeds_the_cap(ws, db):
    p, w = ws["p"], ws["w"]
    card = _card(p, db, _secs(db, 1))
    _store_terms(db, card, _terms(2))
    _set_caps(db, w, terms=3)
    app.dependency_overrides[get_keyword_client] = lambda: _Proposes(
        ["psilocybin", "ibogaine", "psychedelic"]
    )
    r = p.post(f"/workbench/theses/{card}/terms")
    assert r.status_code == 409
    assert r.json()["detail"].startswith("A card can carry at most 3 search terms; this one would")
    db.rollback()
    assert [e.term for e in thesis_repo.get(db, card, workspace=w).term_set] == ["term 0", "term 1"]
    db.rollback()


# --- the race ------------------------------------------------------------------------------------------------------


def test_two_racing_promotes_cannot_both_pass_the_cap(ws, db):
    """Two new cards racing for the last live-card slot, each on its own connection through the promote route
    itself: the cap check and the write share one per-workspace lock, so exactly one passes."""
    from fastapi import HTTPException

    from app.routers.workbench import promote

    w, uid = ws["w"], ws["p_id"]
    _card(ws["p"], db, _secs(db, 1))
    _set_caps(db, w, cards=2)
    user = CurrentUser(
        id=uid,
        email="p@caps.test",
        display_name="P",
        role="user",
        workspace_id=w,
        principal="session",
    )
    outcomes: list[int] = []
    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)

    def worker(i: int) -> None:
        try:
            conn = connect()
            try:
                req = PromoteThesisRequest(
                    name=f"racer {i}", narrative="a race", basket=[], segments=[]
                )
                barrier.wait()
                try:
                    promote(req, conn=conn, tenant_id=w, user=user)
                    status = 200
                except HTTPException as exc:
                    conn.rollback()
                    status = exc.status_code
                with lock:
                    outcomes.append(status)
            finally:
                conn.close()
        except Exception as exc:  # a dropped connection or a broken barrier: report it, never hang
            barrier.abort()
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    join_all(threads, what="racing promotes")
    assert not errors, errors
    assert sorted(outcomes) == [200, 409]
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM thesis WHERE tenant_id = %s AND archived_at IS NULL", (w,)
        )
        assert cur.fetchone()["n"] == 2
    db.rollback()


# --- the rule itself -----------------------------------------------------------------------------------------------


def test_the_rule_refuses_only_a_rise_above_the_cap():
    from app import workspace_caps

    assert workspace_caps.refused(cap=None, before=10, after=99) is False
    assert workspace_caps.refused(cap=5, before=4, after=5) is False
    assert workspace_caps.refused(cap=5, before=5, after=6) is True
    assert workspace_caps.refused(cap=5, before=9, after=9) is False  # over the cap, not rising
    assert workspace_caps.refused(cap=5, before=9, after=8) is False
    assert workspace_caps.refused(cap=5, before=9, after=10) is True
    assert workspace_caps.refused(cap=0, before=0, after=1) is True
