"""The hostile-input suite, first installment (security hardening PR 2, item 7).

One fixture of hostile values, parameterized over every route the database-layer sweep ranked most likely to 500 on
hostile input (``docs/temp/security-hardening/04-db-layer`` section (d)). Each case sends ONE hostile value in ONE
field, as a real signed-in plain user on their own workspace, and asserts:

- a status below 500 (``app.errors`` maps what reaches the database; a 5xx would be a crash, not an answer), and
- no row written: a checksum over every table these routes write is unchanged.

Cases that only REQUEST-MODEL BOUNDS can make pass (a length limit, no control characters, finite numbers, a date
range: security hardening PR 3) are marked expected-fail (strict) with the reason, so PR 3 turns them on by
deleting their entry in ``NEEDS_BOUNDS``.

Not covered here: the OAuth ``/authorize`` and ``/register`` routes (mounted only with ALPHADECK_PUBLIC_ORIGIN, and
the SDK's handler is not ours); the steward routes.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from domain.settings import get_settings
from tests.auth_fixtures import sign_out
from tests.workspaces import new_workspace

PASSWORD = "correct horse battery"
DATA = "00000000-0000-0000-0000-000000000001"
EMAIL = "hostile@example.test"
_WS = "hostile-input "

# --- the hostile values -----------------------------------------------------------------------------------------------

_RAW = "__raw_json__:"  # a value spliced into the JSON body verbatim (NaN, Infinity, 1e400 are not JSON strings)

STRINGS: dict[str, object] = {
    "nul": "a\x00b",
    "control": "a\x01\x07\x1b[31mb\rX\x7f",
    "1mb": "A" * 1_000_000,
}
NUMBERS: dict[str, object] = {
    "nan": _RAW + "NaN",
    "infinity": _RAW + "Infinity",
    "1e400": _RAW + "1e400",
}
DATES: dict[str, object] = {"year_0001": "0001-01-01", "year_9999": "9999-12-31"}
SOURCE_REF_3KB = secrets.token_hex(
    1536
)  # 3 KB, incompressible: past the btree row limit of its index
ADS_RATIO_ABOVE_INT4 = 2**31 + 5


def _body(payload: object) -> str:
    text = json.dumps(payload, allow_nan=True)
    for raw in ("NaN", "Infinity", "1e400"):
        text = text.replace(json.dumps(_RAW + raw), raw)
    return text


# --- the routes -------------------------------------------------------------------------------------------------------


@dataclass
class Ctx:
    client: TestClient
    db: object
    thesis_id: str
    security_id: str


@dataclass(frozen=True)
class Route:
    name: str
    method: str
    path: Callable[[Ctx], str]
    payload: Callable[
        [Ctx, object], object
    ]  # (ctx, hostile value) -> the JSON body (or the query params for GET)
    kinds: tuple[str, ...]  # which hostile families apply


def _fact(ctx: Ctx, **over) -> dict:
    base = {
        "fact_type": "shares_outstanding",
        "security_id": ctx.security_id,
        "source": "10-q",
        "source_ref": "https://www.sec.gov/Archives/edgar/data/1/x.htm",
        "event_date": "2026-06-30",
        "shares": 1000000,
    }
    return {**base, **over}


def _decision(ctx: Ctx, **over) -> dict:
    base = {
        "action": "take",
        "decision_date": "2026-06-30",
        "security_id": ctx.security_id,
        "shares": 10,
        "price": 5,
        "reason": "a reason",
    }
    return {**base, **over}


SLACK = "https://hooks.slack.com/services/"

ROUTES: list[Route] = [
    Route(
        "login.email",
        "post",
        lambda c: "/auth/login",
        lambda c, v: {"email": v, "password": "x" * 12},
        ("str",),
    ),
    Route(
        "securities.q", "get", lambda c: "/workbench/securities", lambda c, v: {"q": v}, ("query",)
    ),
    Route(
        "facts.source_ref",
        "post",
        lambda c: "/workbench/facts",
        lambda c, v: _fact(c, source_ref=v),
        ("str", "source_ref"),
    ),
    Route(
        "facts.note", "post", lambda c: "/workbench/facts", lambda c, v: _fact(c, note=v), ("str",)
    ),
    Route(
        "facts.shares",
        "post",
        lambda c: "/workbench/facts",
        lambda c, v: _fact(c, shares=v),
        ("num",),
    ),
    Route(
        "facts.event_date",
        "post",
        lambda c: "/workbench/facts",
        lambda c, v: _fact(c, event_date=v),
        ("date",),
    ),
    Route(
        "facts.ads_ratio",
        "post",
        lambda c: "/workbench/facts",
        lambda c, v: _fact(c, ads_ratio=v, ads_ratio_status="known"),
        ("ads",),
    ),
    Route(
        "profile.display_name",
        "put",
        lambda c: "/account/profile",
        lambda c, v: {"display_name": v, "email": EMAIL},
        ("str",),
    ),
    Route(
        "profile.email",
        "put",
        lambda c: "/account/profile",
        lambda c, v: {"display_name": "Hostile", "email": v},
        ("str",),
    ),
    Route(
        "slack_webhook.url",
        "put",
        lambda c: "/account/notifications/slack-webhook",
        lambda c, v: {"url": SLACK + v if isinstance(v, str) else v},
        ("str",),
    ),
    Route("tokens.name", "post", lambda c: "/account/tokens", lambda c, v: {"name": v}, ("str",)),
    Route(
        "promote.name",
        "post",
        lambda c: "/workbench/theses",
        lambda c, v: {"name": v, "narrative": "n", "basket": [], "segments": []},
        ("str",),
    ),
    Route(
        "promote.narrative",
        "post",
        lambda c: "/workbench/theses",
        lambda c, v: {"name": "Hostile card 2", "narrative": v, "basket": [], "segments": []},
        ("str",),
    ),
    Route(
        "catalysts.label",
        "put",
        lambda c: f"/theses/{c.thesis_id}/catalysts",
        lambda c, v: [{"label": v}],
        ("str",),
    ),
    Route(
        "catalysts.when_date",
        "put",
        lambda c: f"/theses/{c.thesis_id}/catalysts",
        lambda c, v: [{"label": "a catalyst", "when_date": v}],
        ("date",),
    ),
    Route(
        "kill_criteria.text",
        "put",
        lambda c: f"/theses/{c.thesis_id}/kill-criteria",
        lambda c, v: [{"text": v}],
        ("str",),
    ),
    Route(
        "exclusions.reason",
        "put",
        lambda c: f"/theses/{c.thesis_id}/exclusions",
        lambda c, v: [{"security_id": c.security_id, "reason": v}],
        ("str",),
    ),
    Route(
        "decisions.reason",
        "post",
        lambda c: f"/theses/{c.thesis_id}/decisions",
        lambda c, v: _decision(c, reason=v),
        ("str",),
    ),
    Route(
        "decisions.shares",
        "post",
        lambda c: f"/theses/{c.thesis_id}/decisions",
        lambda c, v: _decision(c, shares=v),
        ("num",),
    ),
    Route(
        "decisions.price",
        "post",
        lambda c: f"/theses/{c.thesis_id}/decisions",
        lambda c, v: _decision(c, price=v),
        ("num",),
    ),
    Route(
        "decisions.decision_date",
        "post",
        lambda c: f"/theses/{c.thesis_id}/decisions",
        lambda c, v: _decision(c, decision_date=v),
        ("date",),
    ),
    Route(
        "terms_edit.term",
        "put",
        lambda c: f"/workbench/theses/{c.thesis_id}/terms/edit",
        lambda c, v: {"terms": [{"term": v, "tier": "signal"}]},
        ("str",),
    ),
    Route(
        "spac_attach.cik",
        "post",
        lambda c: "/radar/spac/attach",
        lambda c, v: {"thesis_id": c.thesis_id, "cik": v},
        ("str",),
    ),
    Route(
        "spac_detach.cik",
        "post",
        lambda c: "/radar/spac/detach",
        lambda c, v: {"thesis_id": c.thesis_id, "cik": v},
        ("str",),
    ),
    Route(
        "funds_attach.symbol",
        "post",
        lambda c: "/radar/funds/attach",
        lambda c, v: {"thesis_id": c.thesis_id, "symbol": v},
        ("str",),
    ),
    Route(
        "funds_detach.symbol",
        "post",
        lambda c: "/radar/funds/detach",
        lambda c, v: {"thesis_id": c.thesis_id, "symbol": v},
        ("str",),
    ),
]


def _values(kind: str) -> dict[str, object]:
    if kind == "str":
        return STRINGS
    if (
        kind == "query"
    ):  # a URL cannot carry 1 MB (httpx refuses it; nginx answers 414): the longest it sends
        return {"nul": STRINGS["nul"], "control": STRINGS["control"], "60kb": "A" * 60_000}
    if kind == "num":
        return NUMBERS
    if kind == "date":
        return DATES
    if kind == "source_ref":
        return {"3kb": SOURCE_REF_3KB}
    if kind == "ads":
        return {"above_int4": ADS_RATIO_ABOVE_INT4}
    raise AssertionError(kind)


# Cases only request-model bounds (security hardening PR 3) can make pass: the value is a well-formed string,
# number or date the database accepts, so it is stored. PR 3 deletes each entry as its bound lands.
_UNBOUNDED_TEXT = (
    "stored as sent: needs a length limit and a control-character rule on the request model (PR 3)"
)
_NON_FINITE = (
    "stored as sent: needs finite numbers only (allow_inf_nan=False) on the request model (PR 3)"
)
_DATE_RANGE = "stored as sent: needs a date range on the request model (PR 3)"
NEEDS_BOUNDS: dict[str, str] = {
    "facts.source_ref-control": _UNBOUNDED_TEXT,
    "facts.note-control": _UNBOUNDED_TEXT,
    "facts.note-1mb": _UNBOUNDED_TEXT,
    "facts.shares-nan": _NON_FINITE,
    "facts.shares-infinity": _NON_FINITE,
    "facts.shares-1e400": _NON_FINITE,
    "facts.event_date-year_0001": _DATE_RANGE,
    "facts.event_date-year_9999": _DATE_RANGE,
    "profile.display_name-control": _UNBOUNDED_TEXT,
    "profile.display_name-1mb": _UNBOUNDED_TEXT,
    "slack_webhook.url-control": _UNBOUNDED_TEXT,
    "slack_webhook.url-1mb": _UNBOUNDED_TEXT,
    "tokens.name-control": _UNBOUNDED_TEXT,
    "promote.name-control": _UNBOUNDED_TEXT,
    "promote.name-1mb": _UNBOUNDED_TEXT,
    "promote.narrative-control": _UNBOUNDED_TEXT,
    "promote.narrative-1mb": _UNBOUNDED_TEXT,
    "catalysts.label-control": _UNBOUNDED_TEXT,
    "catalysts.label-1mb": _UNBOUNDED_TEXT,
    "catalysts.when_date-year_0001": _DATE_RANGE,
    "catalysts.when_date-year_9999": _DATE_RANGE,
    "kill_criteria.text-control": _UNBOUNDED_TEXT,
    "kill_criteria.text-1mb": _UNBOUNDED_TEXT,
    "exclusions.reason-control": _UNBOUNDED_TEXT,
    "exclusions.reason-1mb": _UNBOUNDED_TEXT,
    "decisions.reason-control": _UNBOUNDED_TEXT,
    "decisions.reason-1mb": _UNBOUNDED_TEXT,
    "decisions.shares-nan": _NON_FINITE,
    "decisions.shares-infinity": _NON_FINITE,
    "decisions.shares-1e400": _NON_FINITE,
    "decisions.price-nan": _NON_FINITE,
    "decisions.price-infinity": _NON_FINITE,
    "decisions.price-1e400": _NON_FINITE,
    "decisions.decision_date-year_0001": _DATE_RANGE,
    "terms_edit.term-control": _UNBOUNDED_TEXT,
    "terms_edit.term-1mb": _UNBOUNDED_TEXT,
}


def _cases():
    for route in ROUTES:
        for kind in route.kinds:
            for vid, value in _values(kind).items():
                case_id = f"{route.name}-{vid}"
                marks = (
                    [pytest.mark.xfail(reason=NEEDS_BOUNDS[case_id], strict=True)]
                    if case_id in NEEDS_BOUNDS
                    else []
                )
                yield pytest.param(route, value, id=case_id, marks=marks)


# --- the caller and the before/after proof ----------------------------------------------------------------------------

# Every table these routes write. Content checksums, so an UPDATE counts as a write too; ``user_session`` by count
# only (the gate touches ``last_seen_at`` on every request) and ``app_user`` on its identity columns.
_CHECKSUMS = [
    "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) AS h FROM {} t".format(tbl)
    for tbl in (
        "thesis",
        "basket_member",
        "basket_snapshot",
        "catalyst",
        "kill_criterion",
        "thesis_exclusion",
        "operator_decision",
        "fact_shares_outstanding",
        "api_token",
        "user_meta",
    )
] + [
    "SELECT md5(coalesce(string_agg(email || display_name || password_hash, '|' ORDER BY email), '')) "
    "AS h FROM app_user",
    "SELECT count(*)::text AS h FROM user_session",
]


def _snapshot(db) -> list[str]:
    db.rollback()
    out = []
    with db.cursor() as cur:
        for q in _CHECKSUMS:
            cur.execute(q)
            out.append(cur.fetchone()["h"])
    db.rollback()
    return out


@pytest.fixture
def ctx(db):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    auth.reset_throttle()
    get_settings.cache_clear()
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
        cur.execute(
            "DELETE FROM tenant t WHERE t.name LIKE %s AND NOT EXISTS (SELECT 1 FROM thesis WHERE tenant_id = t.id)",
            (_WS + "%",),
        )
    db.commit()
    ws = new_workspace(db, _WS + EMAIL)
    auth.create_user(db, email=EMAIL, display_name="Hostile", password=PASSWORD, workspace_id=ws)
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, 'HSTL', 'Hostile Test Inc', '0000999777', '2026-01-01')",
            (sid, DATA),
        )
    db.commit()
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    r = client.post(
        "/workbench/theses",
        json={
            "name": "Hostile card",
            "narrative": "a narrative",
            "basket": [{"ticker": "HSTL", "role": "r", "security_id": str(sid)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    yield Ctx(client=client, db=db, thesis_id=r.json()["id"], security_id=str(sid))
    app.dependency_overrides.clear()
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "TRUNCATE app_user CASCADE"
        )  # the workspace's own rows go with the next test's truncate
    db.commit()
    auth.reset_throttle()
    get_settings.cache_clear()


@pytest.mark.parametrize(("route", "value"), list(_cases()))
def test_hostile_value_is_answered_below_500_and_writes_nothing(ctx: Ctx, route: Route, value):
    before = _snapshot(ctx.db)
    payload = route.payload(ctx, value)
    if route.method == "get":
        r = ctx.client.get(route.path(ctx), params=payload)
    else:
        r = ctx.client.request(
            route.method.upper(),
            route.path(ctx),
            content=_body(payload),
            headers={"content-type": "application/json"},
        )
    assert r.status_code < 500, (r.status_code, r.text[:300])
    assert _snapshot(ctx.db) == before, (r.status_code, r.text[:300])
