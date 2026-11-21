"""V2 W5b: a user's FLAG on a platform value (``/value-flags``), proven with REAL sign-ins and adversarial users.

Built WITHOUT the suite's signed-in override (the ``world`` pattern of ``tests/app/test_workspace_isolation.py``):
three provisioned workspaces, none of them the data tenant, real users and real session cookies:

- A (a plain user, W_A) owns a card holding company X;
- B (a plain user, W_B) owns a card holding X and Y;
- S (the steward, W_S) owns no card.

What is pinned:

- the order of refusals: not signed in 401; the steward 403 (a fixed sentence); a company unknown OR not on any of
  the caller's own cards 404 with ONE identical body (it never says whether the company exists on anyone else's
  card, and it is answered before any flag or allowance read); an open flag by this user on this value 409; the
  daily allowance spent 429 (the allowance's standard message); a bad value type or an over-long note 422. A
  refused request writes nothing, spends nothing and sends nothing;
- the server records what was current (a request cannot name the user, the seen value or a close);
- ONE LOCK: two simultaneous identical requests make ONE row, ONE allowance unit and ONE Slack message; the loser
  gets 409 having spent nothing;
- withdraw: the owner only, an open flag only, never a refund; another user's flag id answers exactly like an
  unknown id;
- ``GET /value-flags`` lists the caller's own flags only, open first then the newest closed, paged;
- Slack: one message per raised flag, after the commit, saying "a user"; a Slack failure never changes the answer.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). No network: ``notify.post_text`` is
patched in every test.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

import notify
from app import allowance, auth
from app.deps import get_conn
from app.main import app
from dashboards.cache import CACHE
from db.session import connect
from domain.scopes import DATA_TENANT_ID
from domain.settings import Settings, get_settings
from domain.text_types import PROSE_MAX
from ingest.shares import ingest_shares_outstanding
from platform_values import flags as flags_module
from tests.app.test_workbench_api import _insert_security
from tests.auth_fixtures import sign_out
from tests.thread_support import BARRIER_TIMEOUT_S, join_all

PASSWORD = "correct horse battery"
BASE = "/value-flags"
HOOK = "https://hooks.slack.example/T0/B0/steward-flags"
NOT_ON_YOUR_CARDS = "This company is not on any of your cards. Add it to a card first."
STEWARD_SENTENCE = "The steward corrects values on the company page. A flag is for users."
ALREADY_OPEN = "You already have an open flag on this value. The steward will look at it."
NO_SUCH_FLAG = "There is no such flag."
ALREADY_CLOSED = "This flag is already closed."
REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q3.htm"


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


def _promote(c: TestClient, name: str, members: list[tuple[str, uuid.UUID]]) -> uuid.UUID:
    r = c.post(
        "/workbench/theses",
        json={
            "name": name,
            "narrative": f"{name}: a synthetic narrative for the flag proof",
            "basket": [
                {"ticker": t, "role": "the name", "security_id": str(sid)} for t, sid in members
            ],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


@pytest.fixture
def slack(monkeypatch) -> list[tuple[str, str, float | None]]:
    """Every Slack POST the flags make (``notify.post_text`` patched; nothing reaches the network)."""
    sent: list[tuple[str, str, float | None]] = []

    def fake(url, text, *, timeout=None):
        sent.append((url, text, timeout))
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
    w_a, w_b, w_s = (_workspace(db, f"flag proof {x}") for x in ("A", "B", "S"))
    for email, wid, role in (
        ("a@flag.test", w_a, "user"),
        ("b@flag.test", w_b, "user"),
        ("s@flag.test", w_s, "super_admin"),
    ):
        auth.create_user(
            db, email=email, display_name=email[0], password=PASSWORD, role=role, workspace_id=wid
        )
    db.commit()
    y = _insert_security(db, "YCO", name="Y Company", cik="0000000555")
    z = _insert_security(db, "ZCO", name="Z Company", cik="0000000556")  # on no card at all
    a, b = _client("a@flag.test"), _client("b@flag.test")
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
        "z": z,
        "w_a": w_a,
        "w_b": w_b,
        "a": a,
        "b": b,
        "s": _client("s@flag.test"),
        "a_id": ids["a@flag.test"],
        "b_id": ids["b@flag.test"],
        "s_id": ids["s@flag.test"],
    }
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()


def _limit(monkeypatch, n: int) -> None:
    monkeypatch.setenv("ALPHADECK_ALLOWANCE_FLAG_PER_DAY", str(n))
    get_settings.cache_clear()


def _body(sid, value_type: str = "shares_outstanding", **extra) -> dict:
    return {"security_id": str(sid), "value_type": value_type, **extra}


def _rows(db) -> list[dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM value_flag ORDER BY opened_at, id")
        rows = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return rows


def _used(db, uid) -> int:
    db.rollback()
    n = allowance.used(db, uid, "flag")
    db.rollback()
    return n


def _nothing_spent(db, world) -> None:
    assert _rows(db) == []
    for who in ("a_id", "b_id", "s_id"):
        assert _used(db, world[who]) == 0


# --- the order of refusals ----------------------------------------------------------------------------------------


def test_not_signed_in_is_401_everywhere(world, db, slack):
    anon = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert anon.post(BASE, json=_body(world["x"])).status_code == 401
    assert anon.get(BASE).status_code == 401
    assert anon.post(f"{BASE}/{uuid.uuid4()}/withdraw").status_code == 401
    _nothing_spent(db, world)
    assert slack == []


def test_the_steward_is_403_with_a_fixed_sentence(world, db, slack):
    s = world["s"]
    for body in (_body(world["x"]), _body(world["z"]), _body(uuid.uuid4())):
        r = s.post(BASE, json=body)
        assert (r.status_code, r.json()["detail"]) == (403, STEWARD_SENTENCE)
    r = s.post(f"{BASE}/{uuid.uuid4()}/withdraw")
    assert (r.status_code, r.json()["detail"]) == (403, STEWARD_SENTENCE)
    _nothing_spent(db, world)
    assert slack == []


def test_a_company_unknown_or_on_someone_elses_card_is_one_identical_404(world, db, slack):
    a = world["a"]
    # B has an open flag on Y: A must not be able to tell
    assert world["b"].post(BASE, json=_body(world["y"])).status_code == 201
    slack.clear()
    before = _rows(db)
    answers = [
        a.post(BASE, json=_body(sid, vt))
        for sid in (world["y"], world["z"], uuid.uuid4())  # B's card only; no card; no such company
        for vt in ("shares_outstanding", "cash_burn")
    ]
    assert {r.status_code for r in answers} == {404}
    assert {r.content for r in answers} == {answers[0].content}
    assert answers[0].json() == {"detail": NOT_ON_YOUR_CARDS}
    assert _rows(db) == before
    assert _used(db, world["a_id"]) == 0
    assert slack == []


def test_the_404_comes_before_any_allowance_read(world, db, monkeypatch, slack):
    """A's allowance is spent: a company off A's cards still answers the 404, not the 429."""
    _limit(monkeypatch, 1)
    assert world["a"].post(BASE, json=_body(world["x"])).status_code == 201
    r = world["a"].post(BASE, json=_body(world["y"]))
    assert (r.status_code, r.json()["detail"]) == (404, NOT_ON_YOUR_CARDS)


def test_an_open_flag_on_the_value_is_409_and_spends_nothing(world, db, slack):
    a = world["a"]
    assert a.post(BASE, json=_body(world["x"], note="first")).status_code == 201
    slack.clear()
    r = a.post(BASE, json=_body(world["x"], note="second"))
    assert (r.status_code, r.json()["detail"]) == (409, ALREADY_OPEN)
    assert len(_rows(db)) == 1 and _used(db, world["a_id"]) == 1
    assert slack == []


def test_the_spent_allowance_is_429_with_the_standard_message(world, db, monkeypatch, slack):
    _limit(monkeypatch, 2)
    a, x = world["a"], world["x"]
    first = a.post(BASE, json=_body(x)).json()
    assert a.post(BASE, json=_body(x, "cash_burn")).status_code == 201
    assert a.post(f"{BASE}/{first['id']}/withdraw").status_code == 200
    assert _used(db, world["a_id"]) == 2  # a withdrawal never refunds
    slack.clear()
    r = a.post(BASE, json=_body(x))  # a re-raise after the withdrawal needs a unit: none left
    assert r.status_code == 429
    assert r.json()["detail"] == allowance.over_message("flag")
    assert r.json()["detail"].startswith(
        "Daily allowance reached: 2 flags a day. It resets at midnight"
    )
    assert len(_rows(db)) == 2 and _used(db, world["a_id"]) == 2
    assert slack == []


def test_the_default_allowance_is_50_a_day(monkeypatch):
    monkeypatch.delenv("ALPHADECK_ALLOWANCE_FLAG_PER_DAY", raising=False)
    assert Settings().allowance_flag_per_day == 50


@pytest.mark.parametrize(
    "body",
    [
        {"value_type": "catalyst"},
        {"value_type": "revenue_mix"},
        {"value_type": None},
        {"note": "x" * (PROSE_MAX + 1)},
        {"note": "a NUL \x00 inside"},
        {"security_id": "not-a-uuid"},
    ],
)
def test_a_bad_request_is_422_and_spends_nothing(world, db, slack, body):
    r = world["a"].post(BASE, json={**_body(world["x"]), **body})
    assert r.status_code == 422, r.text
    _nothing_spent(db, world)
    assert slack == []


def test_a_note_at_the_limit_is_stored_whole(world, db):
    note = "y" * PROSE_MAX
    r = world["a"].post(BASE, json=_body(world["x"], note=note))
    assert r.status_code == 201 and r.json()["note"] == note


def test_a_note_is_returned_as_the_plain_string_it_was(world, db):
    """No HTML or markdown processing anywhere: tags, entities and link-like text come back exactly as typed (the
    screens render them as text)."""
    note = '<b>bold</b> <script>x()</script> [click](https://evil.example) &amp; "q" <!channel>'
    r = world["a"].post(BASE, json=_body(world["x"], note=note))
    assert r.status_code == 201 and r.json()["note"] == note
    assert world["a"].get(BASE).json()["items"][0]["note"] == note
    (row,) = _rows(db)
    assert row["note"] == note


# --- raising: the server decides what was current -----------------------------------------------------------------


def test_a_flag_is_stored_with_what_the_server_saw_and_one_unit_and_one_message(world, db, slack):
    x = world["x"]
    fact_id = ingest_shares_outstanding(
        db,
        x,
        shares=52_113_000,
        source="cover",
        source_ref=REF,
        event_date=world["asof"].replace(day=1),
        ratified_by="s@flag.test",  # a steward's value: the email must never reach Slack or the user
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()
    forged = {
        "user_id": str(world["b_id"]),
        "seen_fact_id": str(uuid.uuid4()),
        "seen_candidate_id": str(uuid.uuid4()),
        "closed_at": "2026-01-01T00:00:00Z",
        "outcome": "fixed",
        "reopens": str(uuid.uuid4()),
    }
    r = world["a"].post(BASE, json=_body(x, note="  the cover lists two classes  ", **forged))
    assert r.status_code == 201, r.text
    out = r.json()
    assert set(out) == {
        "id",
        "security_id",
        "ticker",
        "name",
        "value_type",
        "note",
        "opened_at",
        "closed_at",
        "outcome",
        "outcome_note",
        "current_when_flagged",
        "reopens",
    }
    assert (out["ticker"], out["value_type"], out["note"]) == (
        "DEVCO",
        "shares_outstanding",
        "the cover lists two classes",
    )
    assert (out["closed_at"], out["outcome"], out["reopens"]) == (None, None, None)
    assert out["current_when_flagged"] == "value"
    (row,) = _rows(db)
    assert (row["user_id"], row["seen_fact_id"], row["seen_candidate_id"]) == (
        world["a_id"],
        fact_id,
        None,
    )
    assert (row["closed_at"], row["reopens"]) == (None, None)
    assert _used(db, world["a_id"]) == 1
    ((url, text, timeout),) = slack
    assert (url, timeout) == (HOOK, 5.0)
    assert text.startswith("Flag from a user: DEVCO, share count\n")
    assert "Note: the cover lists two classes" in text
    for secret in ("a@flag.test", "s@flag.test", "@", str(world["a_id"]), str(world["w_a"])):
        assert secret not in text, secret
    assert "@" not in r.text  # the user's answer names no steward either


def test_the_slack_message_is_sent_after_the_row_is_committed(world, db, monkeypatch):
    seen: list[int] = []

    def fake(url, text, *, timeout=None):
        with (
            connect() as c,
            c.cursor() as cur,
        ):  # another connection: only committed rows are visible
            cur.execute("SELECT count(*) AS n FROM value_flag")
            seen.append(cur.fetchone()["n"])
        return True, "ok"

    monkeypatch.setattr(notify, "post_text", fake)
    assert world["a"].post(BASE, json=_body(world["x"])).status_code == 201
    assert seen == [1]


@pytest.mark.parametrize("failure", ["raises", "answers 500"])
def test_a_slack_failure_never_changes_the_answer_or_loses_the_row(world, db, monkeypatch, failure):
    def broken(url, text, *, timeout=None):
        if failure == "raises":
            raise RuntimeError("Slack is down")
        return False, "Slack answered 500"

    monkeypatch.setattr(notify, "post_text", broken)
    r = world["a"].post(BASE, json=_body(world["x"]))
    assert r.status_code == 201, r.text
    assert [f["id"] for f in world["a"].get(BASE).json()["items"]] == [r.json()["id"]]
    assert len(_rows(db)) == 1


def test_no_webhook_set_still_stores_the_flag(world, db, monkeypatch, slack):
    monkeypatch.delenv("ALPHADECK_STEWARD_SLACK_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    assert world["a"].post(BASE, json=_body(world["x"])).status_code == 201
    assert slack == [] and len(_rows(db)) == 1


def test_bs_flag_on_the_same_value_is_independent_of_as(world, db, slack):
    """A's open flag on X's share count neither blocks nor reveals itself to B (a per-user key)."""
    a, b, x = world["a"], world["b"], world["x"]
    ra = a.post(BASE, json=_body(x, note="A's private words"))
    rb = b.post(BASE, json=_body(x, note="B's own words"))
    assert (ra.status_code, rb.status_code) == (201, 201)
    assert (_used(db, world["a_id"]), _used(db, world["b_id"])) == (
        1,
        1,
    )  # one unit each, never shared
    assert rb.json()["current_when_flagged"] == ra.json()["current_when_flagged"]
    b_list = b.get(BASE)
    assert [f["id"] for f in b_list.json()["items"]] == [rb.json()["id"]]
    assert b_list.json()["total"] == 1
    for text in (rb.text, b_list.text):
        assert ra.json()["id"] not in text and "A's private words" not in text
    assert len(slack) == 2


# --- ONE LOCK: two simultaneous identical requests ----------------------------------------------------------------


def _own_connection() -> Iterator[psycopg.Connection]:
    """One connection PER REQUEST (the production shape), so the two requests really run side by side."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def test_two_simultaneous_identical_flags_make_one_row_one_unit_and_one_message(
    world, db, monkeypatch, slack
):
    """Both requests pass the own-card check, then meet at a barrier just before the per-user-and-value lock;
    only then does either take it. The first inserts and commits; the second, once it holds the lock, finds the
    open flag and answers 409 having charged nothing."""
    a1, a2 = _client("a@flag.test"), _client("a@flag.test")  # two sessions of one user
    db.commit()
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)
    real_lock = flags_module.lock_value

    def meet_then_lock(conn, user_id, security_id, value_type):
        barrier.wait()  # both requests are past every check made before the lock
        real_lock(conn, user_id, security_id, value_type)

    monkeypatch.setattr(flags_module, "lock_value", meet_then_lock)
    app.dependency_overrides[get_conn] = _own_connection
    answers: list = []

    def click(c: TestClient) -> None:
        answers.append(c.post(BASE, json=_body(world["x"], note="same words")))

    try:
        threads = [threading.Thread(target=click, args=(c,)) for c in (a1, a2)]
        for t in threads:
            t.start()
        join_all(threads, what="flag requests")
    finally:
        app.dependency_overrides[get_conn] = lambda: db

    assert sorted(r.status_code for r in answers) == [201, 409], [r.text for r in answers]
    (loser,) = [r for r in answers if r.status_code == 409]
    assert loser.json()["detail"] == ALREADY_OPEN
    assert len(_rows(db)) == 1  # count the table
    assert _used(db, world["a_id"]) == 1
    assert len(slack) == 1


# --- withdraw -----------------------------------------------------------------------------------------------------


def test_withdraw_is_the_owners_and_only_once(world, db, slack):
    a = world["a"]
    fid = a.post(BASE, json=_body(world["x"])).json()["id"]
    slack.clear()
    r = a.post(f"{BASE}/{fid}/withdraw")
    assert r.status_code == 200, r.text
    assert (r.json()["outcome"], r.json()["outcome_note"]) == ("withdrawn", None)
    assert r.json()["closed_at"] is not None
    (row,) = _rows(db)
    assert (row["outcome"], row["closed_by"]) == ("withdrawn", "user")
    r = a.post(f"{BASE}/{fid}/withdraw")
    assert (r.status_code, r.json()["detail"]) == (409, ALREADY_CLOSED)
    assert slack == []  # no message on a withdrawal
    assert _used(db, world["a_id"]) == 1  # never refunded


def test_another_users_flag_id_is_exactly_like_an_unknown_id(world, db):
    fid = world["a"].post(BASE, json=_body(world["x"], note="A's private words")).json()["id"]
    theirs = world["b"].post(f"{BASE}/{fid}/withdraw")
    unknown = world["b"].post(f"{BASE}/{uuid.uuid4()}/withdraw")
    assert (theirs.status_code, unknown.status_code) == (404, 404)
    assert theirs.content == unknown.content
    assert theirs.json() == {"detail": NO_SUCH_FLAG}
    (row,) = _rows(db)
    assert row["closed_at"] is None  # A's flag is untouched


def test_a_flag_raised_again_after_a_withdrawal_spends_a_unit_and_sends_a_message(world, db, slack):
    a = world["a"]
    fid = a.post(BASE, json=_body(world["x"])).json()["id"]
    assert a.post(f"{BASE}/{fid}/withdraw").status_code == 200
    r = a.post(BASE, json=_body(world["x"]))
    assert r.status_code == 201 and r.json()["id"] != fid
    assert _used(db, world["a_id"]) == 2
    assert len(slack) == 2


# --- mine ---------------------------------------------------------------------------------------------------------


def test_mine_lists_open_first_then_closed_paged(world, db):
    b = world["b"]
    first = b.post(BASE, json=_body(world["x"])).json()["id"]
    second = b.post(BASE, json=_body(world["y"])).json()["id"]
    assert b.post(f"{BASE}/{first}/withdraw").status_code == 200
    third = b.post(BASE, json=_body(world["x"], "cash_burn")).json()["id"]
    body = b.get(BASE).json()
    assert body["total"] == 3
    ids = [f["id"] for f in body["items"]]
    assert set(ids[:2]) == {second, third} and ids[2] == first  # the open ones, then the closed one
    page = b.get(BASE, params={"limit": 1, "offset": 2}).json()
    assert ([f["id"] for f in page["items"]], page["total"]) == ([first], 3)
    assert world["a"].get(BASE).json() == {"items": [], "total": 0}


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 100_001}]
)
def test_mine_bounds_its_page(world, params):
    assert world["a"].get(BASE, params=params).status_code == 422
