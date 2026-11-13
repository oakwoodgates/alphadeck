"""The request input limits, route by route (security hardening PR 3, docs/AUTH.md §Request limits).

For each class of field and each collection: a value AT its limit is accepted and one PAST it is a 422 that names
the field and the limit, and writes nothing. Plus: trimming is the only change made to a value; numbers are finite and
ranged; request dates and ``asof`` reads sit in their windows; the flag explainer's body is bounded; the triage-session
blob stays opaque but bounded in size and depth; the login checks its limits before any hash; the security search
matches ``%`` and ``_`` literally.

At-limit cases for the big collections (3,000 basket rows, 5,000 exclusions) are checked on the request model itself,
where building them costs nothing; their over-limit cases go through the route.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app import auth
from app.main import app
from app.schemas_api import (
    BasketMemberIn,
    CatalystList,
    EditTermsRequest,
    ExclusionList,
    ExtractedFactIn,
    KillCriterionList,
    ProduceTermsRequest,
    PromoteThesisRequest,
)
from domain import text_types as tt
from domain.market_time import market_today
from tests.app.test_hostile_input import _snapshot

DATA = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def card(client, db) -> dict[str, str]:
    """A card on the signed-in steward's workspace with one company, and a second company in the master."""
    sid, other = uuid.uuid4(), uuid.uuid4()
    with db.cursor() as cur:
        for s, t in ((sid, "LIMT"), (other, "LIMO")):
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
                "VALUES (%s, %s, %s, %s, '0000999555', '2026-01-01')",
                (s, DATA, t, f"{t} Inc"),
            )
    db.commit()
    r = client.post(
        "/workbench/theses",
        json={
            "name": "Limits card",
            "narrative": "a narrative",
            "basket": [{"ticker": "LIMT", "role": "r", "security_id": str(sid)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    return {"thesis_id": r.json()["id"], "security_id": str(sid), "other": str(other)}


def _refused(r, loc: tuple, words: str) -> None:
    """A 422 from the request model whose list names ``loc`` with a message containing ``words``."""
    assert r.status_code == 422, (r.status_code, r.text[:300])
    detail = r.json()["detail"]
    assert isinstance(detail, list), detail
    hits = [e for e in detail if tuple(e["loc"]) == loc]
    assert hits, (loc, [e["loc"] for e in detail])
    assert words in hits[0]["msg"], hits[0]["msg"]
    assert "input" not in hits[0]  # PR 2: the caller's value is never echoed


# --- text classes, at and past the limit ---------------------------------------------------------------------------


def _promote(c, card, **over) -> Any:
    body = {
        "id": card["thesis_id"],
        "name": "Limits card",
        "narrative": "a narrative",
        "basket": [{"ticker": "LIMT", "role": "r", "security_id": card["security_id"]}],
        "segments": [],
    }
    return c.post("/workbench/theses", json={**body, **over})


def _member(card, **over) -> dict:
    return {"ticker": "LIMT", "role": "r", "security_id": card["security_id"], **over}


@pytest.mark.parametrize(
    ("over", "loc", "limit"),
    [
        (lambda n: {"name": "a" * n}, ("body", "name"), tt.LINE_MAX),
        (lambda n: {"narrative": "a" * n}, ("body", "narrative"), tt.NARRATIVE_MAX),
        (lambda n: {"ticker": "a" * n}, ("body", "ticker"), tt.LINE_MAX),
    ],
    ids=["line", "narrative", "ticker-label"],
)
def test_promote_text_at_and_past_its_limit(client, db, card, over, loc, limit):
    assert _promote(client, card, **over(limit)).status_code == 200
    before = _snapshot(db)
    _refused(_promote(client, card, **over(limit + 1)), loc, f"at most {limit} characters")
    assert _snapshot(db) == before


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("role", tt.LINE_MAX),
        ("detail", tt.LINE_MAX),
        ("thesis_fit", tt.PROSE_MAX),
    ],
)
def test_basket_row_text_at_and_past_its_limit(client, db, card, field, limit):
    assert _promote(client, card, basket=[_member(card, **{field: "a" * limit})]).status_code == 200
    before = _snapshot(db)
    r = _promote(client, card, basket=[_member(card, **{field: "a" * (limit + 1)})])
    _refused(r, ("body", "basket", 0, field), f"at most {limit} characters")
    assert _snapshot(db) == before


def test_segment_text_at_and_past_its_limit(client, db, card):
    ok = [{"label": "a" * tt.LINE_MAX, "descriptor": "d" * tt.PROSE_MAX}]
    assert _promote(client, card, segments=ok).status_code == 200
    before = _snapshot(db)
    r = _promote(client, card, segments=[{"label": "a" * (tt.LINE_MAX + 1)}])
    _refused(r, ("body", "segments", 0, "label"), f"at most {tt.LINE_MAX} characters")
    r = _promote(client, card, segments=[{"label": "L", "descriptor": "d" * (tt.PROSE_MAX + 1)}])
    _refused(r, ("body", "segments", 0, "descriptor"), f"at most {tt.PROSE_MAX} characters")
    assert _snapshot(db) == before


def test_surfaced_terms_at_and_past_their_limits(client, db, card):
    ok = ["t" * tt.TERM_MAX] * tt.SURFACED_TERMS_MAX
    assert _promote(client, card, basket=[_member(card, surfaced_terms=ok)]).status_code == 200
    before = _snapshot(db)
    r = _promote(client, card, basket=[_member(card, surfaced_terms=["t" * (tt.TERM_MAX + 1)])])
    _refused(r, ("body", "basket", 0, "surfaced_terms", 0), f"at most {tt.TERM_MAX} characters")
    r = _promote(
        client, card, basket=[_member(card, surfaced_terms=["t"] * (tt.SURFACED_TERMS_MAX + 1))]
    )
    _refused(r, ("body", "basket", 0, "surfaced_terms"), f"at most {tt.SURFACED_TERMS_MAX} items")
    assert _snapshot(db) == before


def test_kill_criterion_prose_at_and_past_its_limit(client, db, card):
    url = f"/theses/{card['thesis_id']}/kill-criteria"
    assert client.put(url, json=[{"text": "k" * tt.PROSE_MAX}]).status_code == 200
    before = _snapshot(db)
    _refused(
        client.put(url, json=[{"text": "k" * (tt.PROSE_MAX + 1)}]),
        ("body", 0, "text"),
        f"at most {tt.PROSE_MAX} characters",
    )
    assert _snapshot(db) == before


def test_catalyst_lines_and_dates(client, db, card):
    url = f"/theses/{card['thesis_id']}/catalysts"
    today = market_today()
    ok = {
        "label": "c" * tt.LINE_MAX,
        "kind": "k" * tt.LINE_MAX,
        "when_label": "w" * tt.LINE_MAX,
        "when_date": (today + timedelta(days=366)).isoformat(),
    }
    assert client.put(url, json=[ok]).status_code == 200
    before = _snapshot(db)
    for field in ("label", "kind", "when_label"):
        _refused(
            client.put(url, json=[{**ok, field: "x" * (tt.LINE_MAX + 1)}]),
            ("body", 0, field),
            f"at most {tt.LINE_MAX} characters",
        )
    _refused(
        client.put(url, json=[{**ok, "when_date": "1989-12-31"}]),
        ("body", 0, "when_date"),
        "1990-01-01",
    )
    _refused(
        client.put(url, json=[{**ok, "when_date": (today + timedelta(days=367)).isoformat()}]),
        ("body", 0, "when_date"),
        "366 days after today",
    )
    assert _snapshot(db) == before


def test_exclusion_text_at_and_past_its_limit(client, db, card):
    url = f"/theses/{card['thesis_id']}/exclusions"
    ok = {"security_id": card["other"], "ticker": "t" * tt.LINE_MAX, "reason": "r" * tt.PROSE_MAX}
    assert client.put(url, json=[ok]).status_code == 200
    before = _snapshot(db)
    _refused(
        client.put(url, json=[{**ok, "reason": "r" * (tt.PROSE_MAX + 1)}]),
        ("body", 0, "reason"),
        f"at most {tt.PROSE_MAX} characters",
    )
    _refused(
        client.put(url, json=[{**ok, "ticker": "t" * (tt.LINE_MAX + 1)}]),
        ("body", 0, "ticker"),
        f"at most {tt.LINE_MAX} characters",
    )
    assert _snapshot(db) == before


def test_term_at_and_past_its_limit(client, db, card):
    url = f"/workbench/theses/{card['thesis_id']}/terms/edit"
    assert (
        client.put(url, json={"terms": [{"term": "t" * tt.TERM_MAX, "tier": "signal"}]}).status_code
        == 200
    )
    before = _snapshot(db)
    _refused(
        client.put(url, json={"terms": [{"term": "t" * (tt.TERM_MAX + 1), "tier": "signal"}]}),
        ("body", "terms", 0, "term"),
        f"at most {tt.TERM_MAX} characters",
    )
    assert _snapshot(db) == before


# --- collections: at the limit on the model, past it through the route ------------------------------------------


def _ok_model(tp: Any, value: Any) -> None:
    TypeAdapter(tp).validate_python(value)


def _bad_model(tp: Any, value: Any, limit: int) -> None:
    with pytest.raises(ValidationError) as exc:
        TypeAdapter(tp).validate_python(value)
    assert f"at most {limit} items" in str(exc.value)


def _promote_body(**over) -> dict:
    return {"name": "n", "narrative": "n", "basket": [], "segments": [], **over}


COLLECTIONS = [
    (
        "basket",
        PromoteThesisRequest,
        lambda n: _promote_body(basket=[{"ticker": "T", "role": "r"}] * n),
        tt.BASKET_MAX,
    ),
    (
        "segments",
        PromoteThesisRequest,
        lambda n: _promote_body(segments=[{"label": f"s{i}"} for i in range(n)]),
        tt.SEGMENTS_MAX,
    ),
    (
        "identity_overrides",
        PromoteThesisRequest,
        lambda n: _promote_body(identity_overrides=[str(uuid.uuid4()) for _ in range(n)]),
        tt.IDENTITY_OVERRIDES_MAX,
    ),
    (
        "surfaced_terms",
        BasketMemberIn,
        lambda n: {"ticker": "T", "role": "r", "surfaced_terms": ["t"] * n},
        tt.SURFACED_TERMS_MAX,
    ),
    (
        "terms",
        EditTermsRequest,
        lambda n: {"terms": [{"term": f"t{i}", "tier": "broad"} for i in range(n)]},
        tt.TERMS_MAX,
    ),
    ("seeds", ProduceTermsRequest, lambda n: {"seeds": ["s"] * n}, tt.SEEDS_MAX),
    ("catalysts", CatalystList, lambda n: [{"label": "c"}] * n, tt.CATALYSTS_MAX),
    ("kill_criteria", KillCriterionList, lambda n: [{"text": "k"}] * n, tt.KILL_CRITERIA_MAX),
    (
        "exclusions",
        ExclusionList,
        lambda n: [{"security_id": str(uuid.uuid4())} for _ in range(n)],
        tt.EXCLUSIONS_MAX,
    ),
]


@pytest.mark.parametrize(
    ("tp", "make", "limit"), [c[1:] for c in COLLECTIONS], ids=[c[0] for c in COLLECTIONS]
)
def test_each_collection_at_and_past_its_limit_on_the_model(tp, make, limit):
    _ok_model(tp, make(limit))
    _bad_model(tp, make(limit + 1), limit)


def test_each_collection_past_its_limit_through_its_route(client, db, card):
    tid = card["thesis_id"]
    before = _snapshot(db)
    _refused(
        _promote(client, card, basket=[_member(card)] * (tt.BASKET_MAX + 1)),
        ("body", "basket"),
        f"at most {tt.BASKET_MAX} items",
    )
    _refused(
        _promote(client, card, segments=[{"label": f"s{i}"} for i in range(tt.SEGMENTS_MAX + 1)]),
        ("body", "segments"),
        f"at most {tt.SEGMENTS_MAX} items",
    )
    _refused(
        _promote(
            client,
            card,
            identity_overrides=[str(uuid.uuid4()) for _ in range(tt.IDENTITY_OVERRIDES_MAX + 1)],
        ),
        ("body", "identity_overrides"),
        f"at most {tt.IDENTITY_OVERRIDES_MAX} items",
    )
    _refused(
        client.put(
            f"/workbench/theses/{tid}/terms/edit",
            json={"terms": [{"term": f"t{i}", "tier": "broad"} for i in range(tt.TERMS_MAX + 1)]},
        ),
        ("body", "terms"),
        f"at most {tt.TERMS_MAX} items",
    )
    _refused(
        client.post(f"/workbench/theses/{tid}/terms", json={"seeds": ["s"] * (tt.SEEDS_MAX + 1)}),
        ("body", "seeds"),
        f"at most {tt.SEEDS_MAX} items",
    )
    _refused(
        client.put(f"/theses/{tid}/catalysts", json=[{"label": "c"}] * (tt.CATALYSTS_MAX + 1)),
        ("body",),
        f"at most {tt.CATALYSTS_MAX} items",
    )
    _refused(
        client.put(
            f"/theses/{tid}/kill-criteria", json=[{"text": "k"}] * (tt.KILL_CRITERIA_MAX + 1)
        ),
        ("body",),
        f"at most {tt.KILL_CRITERIA_MAX} items",
    )
    _refused(
        client.put(
            f"/theses/{tid}/exclusions",
            json=[{"security_id": card["other"]}] * (tt.EXCLUSIONS_MAX + 1),
        ),
        ("body",),
        f"at most {tt.EXCLUSIONS_MAX} items",
    )
    assert _snapshot(db) == before


# --- trimming is the only alteration -------------------------------------------------------------------------------


def test_surrounding_whitespace_is_trimmed_and_nothing_else_changes(client, db, card):
    r = _promote(
        client,
        card,
        name="  Mixed Case — é  ",
        narrative="\n  First line.\r\n\tSecond line.  \n",
        basket=[_member(card, role="  the role  ", thesis_fit=" fit\nmore ")],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Mixed Case — é"
    assert body["narrative"] == "First line.\r\n\tSecond line."
    assert body["basket"][0]["role"] == "the role"
    assert body["basket"][0]["thesis_fit"] == "fit\nmore"


@pytest.mark.parametrize("bad", ["a\x00b", "a\x01b", "a\x1b[31mb", "a\x7fb"])
def test_control_characters_are_refused_by_name(client, db, card, bad):
    before = _snapshot(db)
    _refused(_promote(client, card, name=bad), ("body", "name"), "control character")
    _refused(_promote(client, card, narrative=bad), ("body", "narrative"), "control character")
    _refused(
        _promote(client, card, basket=[_member(card, role=bad)]),
        ("body", "basket", 0, "role"),
        "control character",
    )
    assert _snapshot(db) == before


def test_a_one_line_label_refuses_a_line_break(client, db, card):
    _refused(_promote(client, card, name="two\nlines"), ("body", "name"), "control character")


# --- numbers -------------------------------------------------------------------------------------------------------


def _fact(card, **over) -> dict:
    return {
        "fact_type": "shares_outstanding",
        "security_id": card["security_id"],
        "source": "10-q",
        "source_ref": "https://www.sec.gov/Archives/edgar/data/1/x.htm",
        "event_date": "2026-06-30",
        "shares": 1_000_000,
        **over,
    }


@pytest.mark.parametrize(
    ("over", "loc", "words"),
    [
        ({"shares": 0}, ("body", "shares_outstanding", "shares"), "greater than 0"),
        ({"shares": 1.0001e13}, ("body", "shares_outstanding", "shares"), "less than or equal to"),
        (
            {"ads_ratio": 0},
            ("body", "shares_outstanding", "ads_ratio"),
            "greater than or equal to 1",
        ),
        (
            {"ads_ratio": 1_000_001},
            ("body", "shares_outstanding", "ads_ratio"),
            "less than or equal to 1000000",
        ),
        (
            {"estimate": 1.0001e15},
            ("body", "shares_outstanding", "estimate"),
            "less than or equal to",
        ),
        (
            {"source": "s" * (tt.LINE_MAX + 1)},
            ("body", "shares_outstanding", "source"),
            f"at most {tt.LINE_MAX} characters",
        ),
        (
            {"source_ref": "u" * (tt.URL_MAX + 1)},
            ("body", "shares_outstanding", "source_ref"),
            f"at most {tt.URL_MAX} characters",
        ),
        (
            {"note": "n" * (tt.PROSE_MAX + 1)},
            ("body", "shares_outstanding", "note"),
            f"at most {tt.PROSE_MAX} characters",
        ),
        ({"event_date": "1989-12-31"}, ("body", "shares_outstanding", "event_date"), "1990-01-01"),
        (
            {"fact_type": "revenue_mix", "segment_label": "s", "mix_pct": 100.01},
            ("body", "revenue_mix", "mix_pct"),
            "less than or equal to 100",
        ),
        (
            {"fact_type": "revenue_mix", "segment_label": "s", "mix_pct": -0.01},
            ("body", "revenue_mix", "mix_pct"),
            "greater than or equal to 0",
        ),
        (
            {"fact_type": "cash_burn", "cash_usd": -1.0001e15, "quarterly_burn_usd": 0},
            ("body", "cash_burn", "cash_usd"),
            "greater than or equal to",
        ),
        (
            {"fact_type": "cash_burn", "cash_usd": 1, "quarterly_burn_usd": 1.0001e15},
            ("body", "cash_burn", "quarterly_burn_usd"),
            "less than or equal to",
        ),
    ],
)
def test_ratify_numbers_text_and_dates_past_their_limits(client, db, card, over, loc, words):
    before = _snapshot(db)
    _refused(client.post("/workbench/facts", json=_fact(card, **over)), loc, words)
    assert _snapshot(db) == before


def test_ratify_values_at_their_limits_are_accepted(client, card):
    at = {
        "shares": 1e13,
        "ads_ratio": 1_000_000,
        "ads_ratio_status": "known",
        "estimate": 1e13,
        "source": "s" * tt.LINE_MAX,
        "source_ref": "https://x/" + "u" * (tt.URL_MAX - 10),
        "note": "n" * tt.PROSE_MAX,
        "event_date": "1990-01-01",
    }
    r = client.post("/workbench/facts", json=_fact(card, **at))
    assert r.status_code == 200, r.text[:300]
    r = client.post(
        "/workbench/facts",
        json=_fact(card, fact_type="revenue_mix", segment_label="s" * tt.LINE_MAX, mix_pct=100),
    )
    assert r.status_code == 200, r.text[:300]
    r = client.post(
        "/workbench/facts",
        json=_fact(card, fact_type="cash_burn", cash_usd=-1e15, quarterly_burn_usd=1e15),
    )
    assert r.status_code == 200, r.text[:300]


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_numbers_are_refused(client, db, card, raw):
    before = _snapshot(db)
    body = json.dumps(_fact(card, shares=0.5)).replace("0.5", raw)
    r = client.post("/workbench/facts", content=body, headers={"content-type": "application/json"})
    _refused(r, ("body", "shares_outstanding", "shares"), "finite")
    assert _snapshot(db) == before


def test_decision_amounts_and_date(client, db, card):
    url = f"/theses/{card['thesis_id']}/decisions"
    base = {"action": "pass", "decision_date": "2026-06-30", "security_id": card["security_id"]}
    before = _snapshot(db)
    for field in ("shares", "price"):
        _refused(
            client.post(url, json={**base, field: -0.01}),
            ("body", field),
            "greater than or equal to 0",
        )
        _refused(
            client.post(url, json={**base, field: 1.0001e13}),
            ("body", field),
            "less than or equal to",
        )
    _refused(
        client.post(url, json={**base, "reason": "r" * (tt.PROSE_MAX + 1)}),
        ("body", "reason"),
        f"at most {tt.PROSE_MAX} characters",
    )
    _refused(
        client.post(url, json={**base, "decision_date": "1989-12-31"}),
        ("body", "decision_date"),
        "1990-01-01",
    )
    assert _snapshot(db) == before
    r = client.post(
        url,
        json={
            **base,
            "decision_date": "1990-01-01",
            "shares": 1e13,
            "price": 0,
            "reason": "r" * tt.PROSE_MAX,
        },
    )
    assert r.status_code == 200, r.text[:300]
    # the route's own rule still stands inside the window: no decision dated after today
    tomorrow = (market_today() + timedelta(days=1)).isoformat()
    r = client.post(url, json={**base, "decision_date": tomorrow})
    assert r.status_code == 422 and "future" in r.json()["detail"]


# --- asof reads ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/scoreboard", "/dashboards/hot-board", "/data-flags", "/theses/{tid}/call"],
)
def test_asof_window_on_reads(client, card, path):
    path = path.format(tid=card["thesis_id"])
    today = market_today()
    assert client.get(path, params={"asof": "2000-01-01"}).status_code == 200
    assert (
        client.get(path, params={"asof": (today + timedelta(days=7)).isoformat()}).status_code
        == 200
    )
    _refused(client.get(path, params={"asof": "1999-12-31"}), ("query", "asof"), "2000-01-01")
    _refused(
        client.get(path, params={"asof": (today + timedelta(days=8)).isoformat()}),
        ("query", "asof"),
        "7 days after today",
    )
    _refused(client.get(path, params={"asof": "9999-12-31"}), ("query", "asof"), "2000-01-01")


# --- the flag explainer --------------------------------------------------------------------------------------------


def _candidate(**over) -> dict:
    return {
        "fact_type": "cash_burn",
        "tier": "flag",
        "source": "10-q-cashflow",
        "source_ref": "https://sec.gov/x#p1",
        "event_date": "2026-03-31",
        "cash_usd": 890_000_000,
        "quarterly_burn_usd": 314_678_000,
        "flags": ["possible-one-time"],
        "located_passages": [
            {
                "kind": "cash-flow-line",
                "source_ref": "https://sec.gov/x#p1",
                "anchor": "a",
                "excerpt": "e",
            }
        ],
        **over,
    }


def _passage(excerpt: str) -> dict:
    return {"kind": "k", "source_ref": "https://sec.gov/x", "anchor": "a", "excerpt": excerpt}


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_explain_at_its_limits_is_answered(client, no_llm):
    body = _candidate(
        located_passages=[_passage("e" * tt.EXCERPT_MAX)] * tt.EXPLAIN_PASSAGES_MAX,
        flags=["f"] * tt.EXPLAIN_FLAGS_MAX,
        note="n" * tt.PROSE_MAX,
    )
    r = client.post("/workbench/facts/explain", json=body)
    assert r.status_code == 200, r.text[:300]


@pytest.mark.parametrize(
    ("over", "loc", "words"),
    [
        (
            {"located_passages": [_passage("e")] * (tt.EXPLAIN_PASSAGES_MAX + 1)},
            ("body", "located_passages"),
            f"at most {tt.EXPLAIN_PASSAGES_MAX} items",
        ),
        (
            {"located_passages": [_passage("e" * (tt.EXCERPT_MAX + 1))]},
            ("body", "located_passages", 0, "excerpt"),
            f"at most {tt.EXCERPT_MAX} characters",
        ),
        (
            {"flags": ["f"] * (tt.EXPLAIN_FLAGS_MAX + 1)},
            ("body", "flags"),
            f"at most {tt.EXPLAIN_FLAGS_MAX} items",
        ),
        (
            {"note": "n" * (tt.PROSE_MAX + 1)},
            ("body", "note"),
            f"at most {tt.PROSE_MAX} characters",
        ),
        ({"source_ref": "u\x00"}, ("body", "source_ref"), "control character"),
    ],
)
def test_explain_past_its_limits_is_refused(client, no_llm, over, loc, words):
    _refused(client.post("/workbench/facts/explain", json=_candidate(**over)), loc, words)


def test_explain_refuses_a_non_finite_number(client, no_llm):
    body = json.dumps(_candidate(cash_usd=0.5)).replace("0.5", "NaN")
    r = client.post(
        "/workbench/facts/explain", content=body, headers={"content-type": "application/json"}
    )
    _refused(r, ("body", "cash_usd"), "finite")


def test_extract_responses_are_never_held_to_the_request_limits():
    """The extract RESPONSE is the domain ``ExtractedFact`` (built from SEC text): an excerpt over the request limit
    still serializes; only the explain REQUEST (``ExtractedFactIn``) is bounded."""
    from domain.extraction import ExtractedFact, LocatedPassage

    long = "e" * (tt.EXCERPT_MAX * 3)
    fact = ExtractedFact(
        fact_type="cash_burn",
        tier="flag",
        source="s",
        source_ref="r",
        event_date=date(1985, 1, 1),
        located_passages=[LocatedPassage(kind="k", source_ref="r", anchor="a", excerpt=long)] * 30,
        flags=["f"] * 40,
    )
    assert len(fact.model_dump()["located_passages"]) == 30
    with pytest.raises(ValidationError):
        ExtractedFactIn.model_validate(fact.model_dump())


# --- the triage-session blob ---------------------------------------------------------------------------------------


def _nest(depth: int) -> dict:
    out: dict = {}
    for _ in range(depth - 1):
        out = {"k": out}
    return out


def test_triage_state_is_opaque_and_its_strings_are_not_inspected(client, card):
    url = f"/workbench/theses/{card['thesis_id']}/triage-session"
    state = {"a": "\x00\x01 any text \u202e", "nested": _nest(tt.STATE_MAX_DEPTH - 1)}
    r = client.put(url, json={"schema_version": 3, "state": state})
    assert r.status_code == 200, r.text[:300]
    assert client.get(url).json()["session"]["state"] == state


def test_triage_state_nesting_past_the_limit_is_refused(client, card, triage_sessions_dir):
    url = f"/workbench/theses/{card['thesis_id']}/triage-session"
    _refused(
        client.put(url, json={"schema_version": 3, "state": _nest(tt.STATE_MAX_DEPTH + 1)}),
        ("body", "state"),
        f"deeper than {tt.STATE_MAX_DEPTH} levels",
    )
    assert not any(triage_sessions_dir.rglob("*.json"))


def test_triage_body_past_the_size_limit_is_refused(client, card, triage_sessions_dir):
    url = f"/workbench/theses/{card['thesis_id']}/triage-session"
    blob = json.dumps({"schema_version": 3, "state": {"x": "a" * tt.STATE_MAX_BYTES}})
    r = client.put(url, content=blob, headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text[:300]
    assert "25 MB" in r.json()["detail"]
    assert not any(triage_sessions_dir.rglob("*.json"))


# --- the login checks its limits before any hash ------------------------------------------------------------------


@pytest.fixture
def hashes(monkeypatch, db):
    from app.deps import get_conn
    from tests.auth_fixtures import sign_out

    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    auth.reset_throttle()
    calls: list[str] = []
    real_verify, real_dummy = auth.verify_password, auth._dummy_verify

    def verify(h, p):
        calls.append("verify")
        return real_verify(h, p)

    def dummy(p):
        calls.append("dummy")
        return real_dummy(p)

    monkeypatch.setattr(auth, "verify_password", verify)
    monkeypatch.setattr(auth, "_dummy_verify", dummy)
    yield calls
    auth.reset_throttle()


@pytest.mark.parametrize(
    ("email", "password", "loc", "words"),
    [
        (
            "nobody@x.test",
            "p" * (tt.PASSWORD_MAX + 1),
            ("body", "password"),
            "at most 1024 characters",
        ),
        (
            "nobody@x.test",
            "p" * (tt.PASSWORD_MIN - 1),
            ("body", "password"),
            "at least 12 characters",
        ),
        ("e" * (tt.EMAIL_MAX + 1), "p" * 20, ("body", "email"), "at most 254 characters"),
        ("a\x00@x.test", "p" * 20, ("body", "email"), "control character"),
    ],
)
def test_login_limits_refuse_before_any_hash(client, hashes, email, password, loc, words):
    _refused(client.post("/auth/login", json={"email": email, "password": password}), loc, words)
    assert hashes == []


def test_login_at_its_limits_still_reaches_the_hash(client, hashes):
    r = client.post(
        "/auth/login",
        json={"email": "e" * (tt.EMAIL_MAX - 7) + "@x.test", "password": "p" * tt.PASSWORD_MAX},
    )
    assert r.status_code == 401
    assert hashes[0] == "dummy"  # the unknown-email branch's hash runs, after the limits


# --- the security search -------------------------------------------------------------------------------------------


def test_security_search_q_limit(client, card):
    assert (
        client.get("/workbench/securities", params={"q": "q" * tt.SEARCH_Q_MAX}).status_code == 200
    )
    _refused(
        client.get("/workbench/securities", params={"q": "q" * (tt.SEARCH_Q_MAX + 1)}),
        ("query", "q"),
        f"at most {tt.SEARCH_Q_MAX} characters",
    )


def test_security_search_matches_wildcards_literally(client, db):
    with db.cursor() as cur:
        for t, name in (("AB_C", "Under Score Co"), ("ABXC", "Plain Co"), ("PCT", "100% Pure Inc")):
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) "
                "VALUES (%s, %s, %s, %s, '2026-01-01')",
                (uuid.uuid4(), DATA, t, name),
            )
    db.commit()

    def tickers(q: str) -> set[str]:
        r = client.get("/workbench/securities", params={"q": q, "limit": 50})
        assert r.status_code == 200, r.text
        return {m["ticker"] for m in r.json()}

    assert tickers("B_C") == {"AB_C"}  # "_" is not "any one character"
    assert tickers("%") == {"PCT"}  # "%" matches only a literal percent sign
    assert tickers("\\") == set()
    assert tickers("0% P") == {"PCT"}


# --- the 422 the frontend shows -----------------------------------------------------------------------------------


def test_a_limit_422_carries_the_field_and_a_plain_message(client, card):
    r = _promote(client, card, name="a" * (tt.LINE_MAX + 1))
    first = r.json()["detail"][0]
    assert first["loc"] == ["body", "name"]
    assert first["msg"] == f"String should have at most {tt.LINE_MAX} characters"
