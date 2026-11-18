"""V2 W5c: the steward's side of users' flags (``/admin/platform-values``), proven with REAL sign-ins.

Built WITHOUT the suite's signed-in override (the ``world`` pattern of ``tests/app/test_workspace_isolation.py``):
three provisioned workspaces, none of them the data tenant, real users and real session cookies:

- A (a plain user, W_A) and B (a plain user, W_B) each own a card holding company X; B's card also holds Y;
- S (the steward, W_S) owns no card.

What is pinned:

- a plain user gets 403 on close, reopen, the company read and the queue, and nothing is written;
- CLOSE AS FIXED links the steward's standing approve or correct of the SAME company and value type, checked
  inside the close transaction while holding the company write lock, so an undo of that decision and the close
  serialize (two real threads on two connections); a decision of another company or value type, a dismissal, an
  undone decision or none answers with a fixed sentence and writes nothing; a decision undone LATER leaves the
  closed row as it was and the company read says the linked decision was since undone;
- CLOSE AS KEPT needs a note that is not blank after trimming (422 for spaces only); close twice is 409;
- REOPEN appends a new open row naming the closed one and the steward who reopened it; it refuses an open flag, an
  already reopened one, a user-withdrawn one and a value the user has flagged again since, each with a fixed
  sentence and nothing written; it sends no Slack and spends no allowance; reopen then close again keeps both
  closes in the history;
- the company read carries the company's flags (open ones and a page of closed ones), each with the flagging
  user's email (decision B4 (a)), what was current when flagged, the outcome, the closer, the linked decision and
  whether it can be reopened (and why not);
- PRIVACY: a plain user's ``GET /value-flags`` and ``GET /platform-values/{id}`` never gain the steward's
  identity, another user's flag or any count; after the steward closes A's flag, A sees the outcome and note, B
  sees nothing.

Every count is a count of the table. DB-backed (the shared ``db`` fixture). No network: ``notify.post_text`` is
patched in every test.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

import notify
from app import allowance, auth
from app.deps import get_conn
from app.errors import VALUE_FLAG_NOT_FOUND, VALUE_FLAG_SENTENCES
from app.main import app
from dashboards.cache import CACHE
from db import write_lock
from db.session import connect
from domain.settings import get_settings
from platform_values import flags as flags_module
from tests.app.test_workbench_api import _insert_security
from tests.auth_fixtures import sign_out
from tests.thread_support import BARRIER_TIMEOUT_S, join_all

PASSWORD = "correct horse battery"
BASE = "/admin/platform-values"
HOOK = "https://hooks.slack.example/T0/B0/steward-flags"
REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
S_EMAIL = "s@flagsteward.test"


def _workspace(db, label: str) -> uuid.UUID:
    wid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace')", (wid, label)
        )
    return wid


def _client(email: str) -> TestClient:
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


def _promote(c: TestClient, name: str, members: list[tuple[str, uuid.UUID]]) -> None:
    r = c.post(
        "/workbench/theses",
        json={
            "name": name,
            "narrative": f"{name}: a synthetic narrative for the steward's flag proof",
            "basket": [
                {"ticker": t, "role": "the name", "security_id": str(sid)} for t, sid in members
            ],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def slack(monkeypatch) -> list[str]:
    sent: list[str] = []

    def fake(url, text, *, timeout=None):
        sent.append(text)
        return True, "Slack answered 200"

    monkeypatch.setattr(notify, "post_text", fake)
    return sent


@pytest.fixture
def world(db, security_id, pinned_today, monkeypatch, slack):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")  # the auth tables are not in the shared truncate
    auth.reset_throttle()
    for var in (
        "ALPHADECK_SYSTEM_TOKEN",
        "ALPHADECK_COOKIE_SECURE",
        "ALPHADECK_ALLOWANCE_FLAG_PER_DAY",
        "SLACK_WEBHOOK_URL",
        "ALPHADECK_PUBLIC_ORIGIN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALPHADECK_STEWARD_SLACK_WEBHOOK_URL", HOOK)
    get_settings.cache_clear()
    CACHE.clear()
    w_a, w_b, w_s = (_workspace(db, f"steward flag proof {x}") for x in ("A", "B", "S"))
    for email, wid, role in (
        ("a@flagsteward.test", w_a, "user"),
        ("b@flagsteward.test", w_b, "user"),
        (S_EMAIL, w_s, "super_admin"),
    ):
        auth.create_user(
            db, email=email, display_name=email[0], password=PASSWORD, role=role, workspace_id=wid
        )
    db.commit()
    y = _insert_security(db, "YCO", name="Y Company", cik="0000000555")
    a, b = _client("a@flagsteward.test"), _client("b@flagsteward.test")
    _promote(a, f"A card {uuid.uuid4().hex[:8]}", [("DEVCO", security_id)])
    _promote(b, f"B card {uuid.uuid4().hex[:8]}", [("DEVCO", security_id), ("YCO", y)])
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT id, email FROM app_user")
        ids = {r["email"]: r["id"] for r in cur.fetchall()}
    db.rollback()
    yield {
        "asof": pinned_today,
        "x": security_id,
        "y": y,
        "w_a": w_a,
        "w_b": w_b,
        "a": a,
        "b": b,
        "s": _client(S_EMAIL),
        "a_id": ids["a@flagsteward.test"],
        "b_id": ids["b@flagsteward.test"],
    }
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()


# --- helpers ------------------------------------------------------------------------------------------------------


def _raise(c: TestClient, sid, value_type: str = "shares_outstanding", note=None) -> str:
    body = {"security_id": str(sid), "value_type": value_type}
    if note is not None:
        body["note"] = note
    r = c.post("/value-flags", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _correct(world, sid, fact_type: str = "shares_outstanding", *, days: int = 30) -> str:
    """A steward's correct of (sid, fact_type) through the route; answers its decision id."""
    body: dict = {
        "fact_type": fact_type,
        "source_ref": REF,
        "event_date": (world["asof"] - timedelta(days=days)).isoformat(),
    }
    if fact_type == "shares_outstanding":
        body["shares"] = 41_000_000
    else:
        body.update(cash_usd=30_000_000, quarterly_burn_usd=3_000_000)
    r = world["s"].post(f"{BASE}/{sid}/correct", json=body)
    assert r.status_code == 200, r.text
    key = "shares" if fact_type == "shares_outstanding" else "cash_burn"
    return r.json()[key]["decisions"][0]["id"]


def _close(world, fid, outcome: str, note=None, decision_id=None):
    body: dict = {"outcome": outcome}
    if note is not None:
        body["note"] = note
    if decision_id is not None:
        body["decision_id"] = str(decision_id)
    return world["s"].post(f"{BASE}/flags/{fid}/close", json=body)


def _rows(db) -> list[dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM value_flag ORDER BY opened_at, id")
        rows = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return rows


def _row(db, fid) -> dict:
    (row,) = [r for r in _rows(db) if str(r["id"]) == str(fid)]
    return row


def _used(db, uid) -> int:
    db.rollback()
    n = allowance.used(db, uid, "flag")
    db.rollback()
    return n


def _refused(r, status: int, kind: str) -> None:
    assert r.status_code == status, r.text
    assert r.json()["detail"].startswith(VALUE_FLAG_SENTENCES[kind]), r.text


def _company_flags(world, sid=None, **params) -> dict:
    r = world["s"].get(f"{BASE}/{sid or world['x']}", params=params)
    assert r.status_code == 200, r.text
    return r.json()["flags"]


# --- a plain user: 403 everywhere ---------------------------------------------------------------------------------


def test_a_plain_user_gets_403_on_every_steward_flag_route_and_nothing_changes(world, db, slack):
    fid = _raise(world["a"], world["x"], note="A's words")
    did = _correct(world, world["x"])
    slack.clear()
    before = _rows(db)
    calls = [
        ("get", f"{BASE}/queue", None),
        ("get", f"{BASE}/{world['x']}", None),
        ("post", f"{BASE}/flags/{fid}/close", {"outcome": "kept", "note": "x"}),
        ("post", f"{BASE}/flags/{fid}/close", {"outcome": "fixed", "decision_id": did}),
        ("post", f"{BASE}/flags/{fid}/reopen", None),
        ("post", f"{BASE}/flags/{uuid.uuid4()}/reopen", None),
    ]
    for who in ("a", "b"):
        for method, url, body in calls:
            r = getattr(world[who], method)(url, **({"json": body} if body is not None else {}))
            assert r.status_code == 403, f"{who} {method} {url}: {r.status_code} {r.text}"
            assert "A's words" not in r.text and "a@flagsteward.test" not in r.text
    assert _rows(db) == before
    assert slack == []


# --- close as kept ------------------------------------------------------------------------------------------------


def test_close_as_kept_shows_the_outcome_to_its_user_only(world, db, slack):
    a, b, x = world["a"], world["b"], world["x"]
    fid = _raise(a, x, note="A's private words")
    slack.clear()
    r = _close(world, fid, "kept", note="  The cover lists one class; the count is right.  ")
    assert r.status_code == 200, r.text
    (closed,) = r.json()["flags"]["closed"]
    assert closed["id"] == fid
    assert (closed["outcome"], closed["outcome_note"], closed["closed_by"]) == (
        "kept",
        "The cover lists one class; the count is right.",
        S_EMAIL,
    )
    assert (
        closed["user_email"] == "a@flagsteward.test"
    )  # decision B4 (a): the steward's page names the user
    assert r.json()["flags"]["open"] == []
    row = _row(db, fid)
    assert (row["outcome"], row["closed_by"], row["fixed_decision_id"]) == ("kept", S_EMAIL, None)
    assert slack == []  # no message on a close

    mine = a.get("/value-flags")
    assert mine.status_code == 200
    (item,) = mine.json()["items"]
    assert (item["outcome"], item["outcome_note"]) == (
        "kept",
        "The cover lists one class; the count is right.",
    )
    assert "closed_by" not in item
    read = a.get(f"/platform-values/{x}")
    assert read.status_code == 200, read.text
    assert read.json()["shares"]["my_flag"]["outcome"] == "kept"
    for text in (mine.text, read.text):
        assert S_EMAIL not in text and "closed_by" not in text

    b_mine = b.get("/value-flags")
    assert b_mine.json() == {"items": [], "total": 0}
    b_read = b.get(f"/platform-values/{x}")
    assert b_read.status_code == 200
    assert b_read.json()["shares"]["my_flag"] is None
    for text in (b_mine.text, b_read.text):
        for secret in (fid, "A's private words", "one class", S_EMAIL, "a@flagsteward.test"):
            assert secret not in text, secret


@pytest.mark.parametrize("note", [None, "", "   ", " \n\t "])
def test_close_as_kept_needs_a_note_that_is_not_blank(world, db, note):
    fid = _raise(world["a"], world["x"])
    _refused(_close(world, fid, "kept", note=note), 422, "blank_note")
    assert _row(db, fid)["closed_at"] is None


def test_close_twice_is_409_and_writes_nothing(world, db):
    fid = _raise(world["a"], world["x"])
    assert _close(world, fid, "kept", note="right").status_code == 200
    before = _rows(db)
    _refused(_close(world, fid, "kept", note="again"), 409, "already_closed")
    _refused(
        _close(world, fid, "fixed", decision_id=_correct(world, world["x"])), 409, "already_closed"
    )
    assert _rows(db) == before


def test_an_unknown_flag_is_404_with_one_sentence(world, db):
    for url, body in (
        (f"{BASE}/flags/{uuid.uuid4()}/close", {"outcome": "kept", "note": "x"}),
        (f"{BASE}/flags/{uuid.uuid4()}/reopen", None),
    ):
        r = world["s"].post(url, **({"json": body} if body else {}))
        assert r.status_code == 404 and r.json()["detail"].startswith(VALUE_FLAG_NOT_FOUND)
    assert _rows(db) == []


def test_a_note_is_returned_as_the_plain_string_it_was(world, db):
    note = '<b>kept</b> <script>x()</script> [see](https://evil.example) &amp; "q" <!channel>'
    user_note = "<i>user</i> https://evil.example/<x>"
    fid = _raise(world["a"], world["x"], note=user_note)
    r = _close(world, fid, "kept", note=note)
    assert r.status_code == 200, r.text
    (closed,) = r.json()["flags"]["closed"]
    assert (closed["outcome_note"], closed["note"]) == (note, user_note)
    (item,) = world["a"].get("/value-flags").json()["items"]
    assert (item["outcome_note"], item["note"]) == (note, user_note)


@pytest.mark.parametrize(
    "body",
    [
        {"outcome": "withdrawn", "note": "x"},
        {"outcome": "kept", "note": "x" * 2001},
        {"outcome": "kept", "note": "a NUL \x00 inside"},
        {"outcome": "fixed", "decision_id": "not-a-uuid"},
        {},
    ],
)
def test_a_bad_close_request_is_422_and_writes_nothing(world, db, body):
    fid = _raise(world["a"], world["x"])
    r = world["s"].post(f"{BASE}/flags/{fid}/close", json=body)
    assert r.status_code == 422, r.text
    assert _row(db, fid)["closed_at"] is None


# --- close as fixed -----------------------------------------------------------------------------------------------


def test_close_as_fixed_links_a_standing_correct_of_the_same_value(world, db):
    fid = _raise(world["a"], world["x"])
    did = _correct(world, world["x"])
    r = _close(world, fid, "fixed", decision_id=did)
    assert r.status_code == 200, r.text
    (closed,) = r.json()["flags"]["closed"]
    assert closed["outcome"] == "fixed" and closed["outcome_note"] is None
    assert closed["fixed_decision"]["id"] == did
    assert closed["fixed_decision"]["action"] == "correct"
    assert closed["fixed_decision_undone"] is False
    assert str(_row(db, fid)["fixed_decision_id"]) == did
    (item,) = world["a"].get("/value-flags").json()["items"]
    assert item["outcome"] == "fixed"
    assert S_EMAIL not in world["a"].get("/value-flags").text


def test_close_as_fixed_refuses_a_decision_that_does_not_fix_this_value(world, db):
    x, y = world["x"], world["y"]
    fid = _raise(world["a"], x)
    other_company = _correct(world, y)
    other_type = _correct(world, x, "cash_burn")
    undone = _correct(world, x, days=40)
    assert world["s"].post(f"{BASE}/decisions/{undone}/undo", json={}).status_code == 200
    before = _rows(db)
    for did in (other_company, other_type, undone, str(uuid.uuid4())):
        _refused(_close(world, fid, "fixed", decision_id=did), 409, "wrong_decision")
    _refused(_close(world, fid, "fixed"), 422, "fixed_needs_decision")
    _refused(
        _close(world, fid, "kept", note="x", decision_id=_correct(world, x, days=20)),
        422,
        "decision_on_kept",
    )
    after = _rows(db)
    assert after == before and _row(db, fid)["closed_at"] is None


def test_a_decision_undone_after_the_close_is_shown_as_since_undone(world, db):
    """The closed row is never rewritten (the closed-once trigger): the read says the linked decision was since
    undone, and the flag can be reopened."""
    fid = _raise(world["a"], world["x"])
    did = _correct(world, world["x"])
    assert _close(world, fid, "fixed", decision_id=did).status_code == 200
    stored = _row(db, fid)
    assert world["s"].post(f"{BASE}/decisions/{did}/undo", json={}).status_code == 200
    assert _row(db, fid) == stored  # untouched
    (closed,) = _company_flags(world)["closed"]
    assert closed["fixed_decision"]["id"] == did
    assert closed["fixed_decision"]["undone"] is True
    assert closed["fixed_decision_undone"] is True
    assert (closed["can_reopen"], closed["reopen_refusal"]) == (True, None)


# --- concurrency: a close as fixed against an undo of its decision ------------------------------------------------


def _own_connection() -> Iterator[psycopg.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def test_an_undo_cannot_slip_between_the_check_and_the_close(world, db, monkeypatch):
    """Two real threads on two connections. The close checks its decision (standing), then pauses until the undo
    has either finished or been made to wait for the company lock. The close holds that lock across its check and
    its write, so the undo waits; the close commits first; the undo then lands. When the undo answers, the flag is
    already closed: the close never commits after its decision was undone."""
    fid = _raise(world["a"], world["x"])
    did = _correct(world, world["x"])
    db.commit()
    checked = threading.Event()
    go = threading.Event()
    real_check = flags_module._check_fixing_decision
    real_try = write_lock._try_both

    def check_then_pause(conn, flag, decision_id):
        real_check(conn, flag, decision_id)
        checked.set()
        assert go.wait(BARRIER_TIMEOUT_S), "the undo neither finished nor waited"

    def try_and_tell(conn, security_id):
        got = real_try(conn, security_id)
        if checked.is_set() and not all(got):
            go.set()  # the undo is waiting for the company lock
        return got

    monkeypatch.setattr(flags_module, "_check_fixing_decision", check_then_pause)
    monkeypatch.setattr(write_lock, "_try_both", try_and_tell)
    app.dependency_overrides[get_conn] = _own_connection
    out: dict = {}

    def close() -> None:
        out["close"] = _close(world, fid, "fixed", decision_id=did)

    def undo() -> None:
        assert checked.wait(BARRIER_TIMEOUT_S)
        out["undo"] = world["s"].post(f"{BASE}/decisions/{did}/undo", json={})
        with connect() as c, c.cursor() as cur:  # committed state only
            cur.execute("SELECT closed_at FROM value_flag WHERE id = %s", (fid,))
            out["closed_when_undo_answered"] = cur.fetchone()["closed_at"] is not None
        go.set()

    try:
        threads = [threading.Thread(target=close), threading.Thread(target=undo)]
        for t in threads:
            t.start()
        join_all(threads, what="close and undo")
    finally:
        app.dependency_overrides[get_conn] = lambda: db
    assert out["close"].status_code == 200, out["close"].text
    assert out["undo"].status_code == 200, out["undo"].text
    assert out["closed_when_undo_answered"] is True
    row = _row(db, fid)
    assert (row["outcome"], str(row["fixed_decision_id"])) == ("fixed", did)
    (closed,) = _company_flags(world)["closed"]
    assert closed["fixed_decision_undone"] is True


def test_an_undo_first_refuses_the_close(world, db):
    fid = _raise(world["a"], world["x"])
    did = _correct(world, world["x"])
    assert world["s"].post(f"{BASE}/decisions/{did}/undo", json={}).status_code == 200
    _refused(_close(world, fid, "fixed", decision_id=did), 409, "wrong_decision")
    assert _row(db, fid)["closed_at"] is None


# --- reopen -------------------------------------------------------------------------------------------------------


def test_reopen_appends_a_new_open_row_and_spends_nothing(world, db, slack):
    a, x = world["a"], world["x"]
    fid = _raise(a, x, note="A's words")
    assert _close(world, fid, "kept", note="right").status_code == 200
    slack.clear()
    used = _used(db, world["a_id"])
    r = world["s"].post(f"{BASE}/flags/{fid}/reopen")
    assert r.status_code == 200, r.text
    got = r.json()["flags"]
    (opened,) = got["open"]
    (old,) = got["closed"]
    assert opened["id"] != fid and opened["reopens"] == fid
    assert (opened["reopened_by"], opened["user_email"], opened["note"]) == (
        S_EMAIL,
        "a@flagsteward.test",
        "A's words",
    )
    assert (old["id"], old["reopened_as"], old["can_reopen"]) == (fid, opened["id"], False)
    assert old["reopen_refusal"] == VALUE_FLAG_SENTENCES["already_reopened"]
    assert opened["can_reopen"] is False
    assert opened["reopen_refusal"] == VALUE_FLAG_SENTENCES["not_closed"]
    rows = _rows(db)
    assert len(rows) == 2  # count the table
    new = _row(db, opened["id"])
    assert (new["user_id"], new["reopened_by"], str(new["reopens"])) == (
        world["a_id"],
        S_EMAIL,
        fid,
    )
    assert slack == [] and _used(db, world["a_id"]) == used  # no Slack, no allowance
    mine = a.get("/value-flags")
    assert [i["id"] for i in mine.json()["items"]] == [opened["id"], fid]
    assert mine.json()["items"][0]["reopens"] == fid
    assert S_EMAIL not in mine.text and "reopened_by" not in mine.text


def test_reopen_then_close_again_keeps_both_closes(world, db):
    fid = _raise(world["a"], world["x"])
    assert _close(world, fid, "kept", note="first look").status_code == 200
    again = world["s"].post(f"{BASE}/flags/{fid}/reopen").json()["flags"]["open"][0]["id"]
    did = _correct(world, world["x"])
    r = _close(world, again, "fixed", decision_id=did)
    assert r.status_code == 200, r.text
    closed = r.json()["flags"]["closed"]
    assert {c["id"]: c["outcome"] for c in closed} == {fid: "kept", again: "fixed"}
    assert len(_rows(db)) == 2
    # the newest close can itself be reopened
    newest = next(c for c in closed if c["id"] == again)
    assert newest["can_reopen"] is True
    r = world["s"].post(f"{BASE}/flags/{again}/reopen")
    assert r.status_code == 200 and len(_rows(db)) == 3


def test_reopen_refusals_have_fixed_sentences_and_write_nothing(world, db):
    a, s, x = world["a"], world["s"], world["x"]
    open_one = _raise(a, x)
    _refused(s.post(f"{BASE}/flags/{open_one}/reopen"), 409, "not_closed")

    withdrawn = _raise(a, x, "cash_burn")
    assert a.post(f"/value-flags/{withdrawn}/withdraw").status_code == 200
    _refused(s.post(f"{BASE}/flags/{withdrawn}/reopen"), 409, "withdrawn_by_user")

    assert _close(world, open_one, "kept", note="right").status_code == 200
    assert s.post(f"{BASE}/flags/{open_one}/reopen").status_code == 200
    before = _rows(db)
    _refused(s.post(f"{BASE}/flags/{open_one}/reopen"), 409, "already_reopened")
    assert _rows(db) == before

    # the user's newer open flag on the value blocks a reopen of the older closed one
    newer_closed = next(r["id"] for r in before if r["reopens"] is not None)
    assert _close(world, newer_closed, "kept", note="still right").status_code == 200
    _raise(a, x, note="flagging again")
    before = _rows(db)
    _refused(s.post(f"{BASE}/flags/{newer_closed}/reopen"), 409, "user_has_open_flag")
    assert _rows(db) == before
    flagged = {f["id"]: f for f in _company_flags(world)["closed"]}
    assert (
        flagged[str(newer_closed)]["reopen_refusal"] == VALUE_FLAG_SENTENCES["user_has_open_flag"]
    )
    assert flagged[str(withdrawn)]["reopen_refusal"] == VALUE_FLAG_SENTENCES["withdrawn_by_user"]


# --- the company read ---------------------------------------------------------------------------------------------


def test_the_company_read_lists_flags_with_what_was_current_and_the_user(world, db):
    a, b, x = world["a"], world["b"], world["x"]
    did = _correct(world, x)  # a value shown: A's flag sees it
    fa = _raise(a, x, note="A thinks it is wrong")
    fb = _raise(b, x, "cash_burn")  # no value yet
    flags = _company_flags(world)
    assert [f["id"] for f in flags["open"]] == [fa, fb]  # oldest first
    by = {f["id"]: f for f in flags["open"]}
    assert (by[fa]["user_email"], by[fb]["user_email"]) == (
        "a@flagsteward.test",
        "b@flagsteward.test",
    )
    assert (by[fa]["value_type"], by[fa]["current_when_flagged"]) == ("shares_outstanding", "value")
    assert by[fa]["seen_value"]["shares"] == 41_000_000
    assert by[fa]["seen_value"]["source_ref"] == REF
    assert by[fa]["seen_candidate"] is None
    assert (by[fb]["current_when_flagged"], by[fb]["seen_value"], by[fb]["seen_candidate"]) == (
        "none",
        None,
        None,
    )
    assert by[fa]["closed_at"] is None and by[fa]["outcome"] is None
    assert (flags["closed_total"], flags["closed"]) == (0, [])
    assert did  # the decision exists; the flag saw its value


def test_closed_flags_are_paged_so_an_old_one_stays_reachable(world, db):
    a, x = world["a"], world["x"]
    closed = []
    for i in range(3):
        fid = _raise(a, x, note=f"round {i}")
        assert _close(world, fid, "kept", note=f"kept {i}").status_code == 200
        closed.append(fid)
    page = _company_flags(world, flags_limit=2)
    assert page["closed_total"] == 3 and len(page["closed"]) == 2
    rest = _company_flags(world, flags_limit=2, flags_offset=2)
    assert len(rest["closed"]) == 1
    seen = {f["id"] for f in page["closed"] + rest["closed"]}
    assert seen == set(closed)
    oldest = (page["closed"] + rest["closed"])[-1]
    assert oldest["id"] == closed[0] and oldest["can_reopen"] is True


@pytest.mark.parametrize(
    "params",
    [{"flags_limit": 0}, {"flags_limit": 101}, {"flags_offset": -1}, {"flags_offset": 100_001}],
)
def test_the_company_reads_flag_page_is_bounded(world, params):
    r = world["s"].get(f"{BASE}/{world['x']}", params=params)
    assert r.status_code == 422, r.text


def test_a_plain_users_answers_never_gain_another_users_flag_or_a_count(world, db):
    a, b, x = world["a"], world["b"], world["x"]
    fa = _raise(a, x, note="A's secret words")
    fb = _raise(b, x, note="B's own words")
    assert _close(world, fa, "kept", note="the steward's answer to A").status_code == 200
    a_texts = [a.get("/value-flags").text, a.get(f"/platform-values/{x}").text]
    b_texts = [b.get("/value-flags").text, b.get(f"/platform-values/{x}").text]
    for text in a_texts:
        for secret in (
            fb,
            "B's own words",
            "b@flagsteward.test",
            S_EMAIL,
            "open_flags",
            "user_email",
        ):
            assert secret not in text, secret
    for text in b_texts:
        for secret in (
            fa,
            "A's secret words",
            "the steward's answer",
            "a@flagsteward.test",
            S_EMAIL,
        ):
            assert secret not in text, secret
        assert "open_flags" not in text and "user_email" not in text
    assert b.get("/value-flags").json()["total"] == 1
