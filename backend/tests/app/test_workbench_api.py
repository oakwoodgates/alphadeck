from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.main import app
from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype, TermTier
from domain.thesis import BasketMember, Segment, TermSetEntry, Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.revenue_mix import ingest_revenue_mix
from repositories import thesis_repo


def _scored_thesis(db, security_id) -> uuid.UUID:
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="reactors",
        mix_pct=100,
        source="10-k-business-description",
        source_ref="10-K-biz",
        event_date=date(2025, 12, 31),
    )
    ingest_cash_burn(
        db,
        security_id,
        cash_usd=500_000_000,
        quarterly_burn_usd=25_000_000,
        source="10-q",
        source_ref="10-Q",
        event_date=date(2026, 3, 31),
    )
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Small-scale nuclear",
        narrative="AI power demand + the SMR build-out.",
        segments=[Segment(label="reactors", descriptor="catalyst-rich")],
        basket=[
            BasketMember(
                ticker="DEVCO",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
                segment="reactors",
            )
        ],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis.id


def test_scored_endpoint_serves_meters_on_real_data(client, db, security_id):
    tid = _scored_thesis(db, security_id)
    r = client.get(f"/workbench/theses/{tid}/scored", params={"asof": "2026-06-02"})

    assert r.status_code == 200
    body = r.json()
    assert body["thesis_id"] == str(tid)
    assert [s["label"] for s in body["segments"]] == ["reactors"]
    assert len(body["members"]) == 1
    m = body["members"][0]
    assert m["ticker"] == "DEVCO" and m["segment"] == "reactors"
    assert m["purity"]["pips"] == 4 and m["purity"]["value"] == 100.0  # pure-play
    assert m["runway"]["pips"] == 4  # 500M / (25M/3) = 60 months
    assert m["dilution"]["pips"] is None  # no convert fact -> "—", not a fake 0
    assert m["fit"] == "pure-play"
    assert (
        m["purity"]["provenance"][0]["ref"] == "10-K-biz"
    )  # "behind the scores" traces to the filing


def test_promote_creates_incubating_thesis_on_the_board(client, security_id):
    payload = {
        "name": "Nuclear (promoted)",
        "narrative": "AI power demand + SMR build-out.",
        "ticker": None,
        "segments": [{"label": "reactors", "descriptor": "catalyst-rich"}],
        "basket": [
            {
                "ticker": "DEVCO",
                "role": "the name",
                "archetype": "high_beta",
                "security_id": str(security_id),
                "segment": "reactors",
                "authored_by": "operator_set",
            }
        ],
    }
    r = client.post("/workbench/theses", json=payload)
    assert r.status_code == 200
    tid = r.json()["id"]
    assert [s["label"] for s in r.json()["segments"]] == ["reactors"]
    # it now shows on the Board (GET /theses) and the chain persisted (GET /theses/{id})
    assert any(t["id"] == tid for t in client.get("/theses").json())
    detail = client.get(f"/theses/{tid}").json()
    assert detail["basket"][0]["segment"] == "reactors"
    assert detail["basket"][0]["authored_by"] == "operator_set"


def test_promote_rejects_orphan_segment_placement(client, security_id):
    """A name placed in a link that isn't in the chain -> 422 (the Slice-1 validator, surfaced by the API)."""
    payload = {
        "name": "bad",
        "narrative": "x",
        "ticker": None,
        "segments": [{"label": "reactors"}],
        "basket": [
            {
                "ticker": "DEVCO",
                "role": "r",
                "archetype": "leader",
                "security_id": str(security_id),
                "segment": "fuel",  # not in segments
            }
        ],
    }
    assert client.post("/workbench/theses", json=payload).status_code == 422


def test_promote_preserves_a_persisted_term_set(client, db):
    """LOAD-BEARING — the invisible-wipe seam. Produce a term set, then a promote whose request OMITS term_set
    must NOT blank it (a wiped set is indistinguishable from never-produced, and the next draft would 503 with
    no clue why). The stored set SURVIVES — `upsert` structurally cannot write the column."""
    from domain.enums import TermTier
    from domain.thesis import TermSetEntry
    from repositories import thesis_repo

    created = client.post(
        "/workbench/theses",
        json={
            "name": "psy",
            "narrative": "psychedelic therapy",
            "ticker": None,
            "segments": [],
            "basket": [],
        },
    )
    tid = created.json()["id"]
    assert created.json()["term_set"] == []  # born empty — no producer has run yet

    # produce a term set out-of-band (the /terms producer endpoint lands in T2; the repo writer stands in here)
    thesis_repo.set_term_set(
        db, uuid.UUID(tid), [TermSetEntry(term="psilocybin", tier=TermTier.SIGNAL)]
    )
    db.commit()

    # a SECOND promote (a narrative edit) whose request OMITS term_set — the exact wipe scenario
    r = client.post(
        "/workbench/theses",
        json={
            "id": tid,
            "name": "psy",
            "narrative": "psychedelic therapy — edited",
            "ticker": None,
            "segments": [],
            "basket": [],
        },
    )
    assert r.status_code == 200

    detail = client.get(f"/theses/{tid}").json()
    assert detail["narrative"] == "psychedelic therapy — edited"  # the edit landed
    assert [e["term"] for e in detail["term_set"]] == [
        "psilocybin"
    ]  # the term set SURVIVED the promote


def test_produce_terms_endpoint_persists_and_is_regenerable(client, db):
    """POST /terms produces (keyword-gen PROPOSES -> the deterministic guard TIERS) + PERSISTS, returns the
    stored split for inspection, and a re-POST REPLACES it (the inspect-and-tune loop). Option 3: no keyword-gen
    term is SIGNAL — survivors are BROAD, junk is DROPPED. The load-bearing precision behavior, end to end.
    """
    from app.deps import get_keyword_client

    tid = client.post(
        "/workbench/theses",
        json={
            "name": "psy",
            "narrative": "psychedelic therapy",
            "ticker": None,
            "segments": [],
            "basket": [],
        },
    ).json()["id"]

    # fake keyword-gen putting compounds + junk in its SIGNAL tier -> the guard discards the split entirely
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": ["psilocybin", "MDMA", "clinical trial"], "broad": ["psychedelic"]}
    )
    r = client.post(f"/workbench/theses/{tid}/terms")
    assert r.status_code == 200
    tiers = {e["term"]: e["tier"] for e in r.json()["term_set"]}
    assert tiers["psilocybin"] == "broad" and tiers["psychedelic"] == "broad"  # never SIGNAL
    assert all(t == "broad" for t in tiers.values())  # no keyword-gen term is SIGNAL (seeds-only)
    assert (
        "MDMA" not in tiers and "clinical trial" not in tiers
    )  # guard dropped both (collision abbrev + generic)
    # persisted: a fresh GET shows the same stored set
    assert {e["term"]: e["tier"] for e in client.get(f"/theses/{tid}").json()["term_set"]} == tiers

    # REGENERABLE: a re-POST with a different proposal REPLACES the set (not appends)
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": ["ibogaine"], "broad": []}
    )
    r2 = client.post(f"/workbench/theses/{tid}/terms")
    assert [e["term"] for e in r2.json()["term_set"]] == ["ibogaine"]  # superseded the prior set


def test_produce_terms_seeds_are_operator_signal_and_preserved_on_regenerate(client, db):
    """Seeds anchor the SIGNAL set (the recall guarantor vs keyword-gen non-determinism): supplied seeds persist
    as OPERATOR_SET SIGNAL, and a REGENERATE (re-POST, no body) PRESERVES them while RE-ROLLING the LLM-proposed
    terms — the convergent inspect-tune loop, never dropping an anchored compound."""
    from app.deps import get_keyword_client

    tid = client.post(
        "/workbench/theses",
        json={
            "name": "psy",
            "narrative": "psychedelic therapy",
            "ticker": None,
            "segments": [],
            "basket": [],
        },
    ).json()["id"]

    # first production: operator seeds + a keyword-gen proposal
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": ["psychedelic"], "broad": []}
    )
    r1 = client.post(f"/workbench/theses/{tid}/terms", json={"seeds": ["psilocybin", "ibogaine"]})
    e1 = {x["term"]: (x["tier"], x["authored_by"]) for x in r1.json()["term_set"]}
    assert e1["psilocybin"] == ("signal", "operator_set")  # seeds anchored as operator SIGNAL
    assert e1["ibogaine"] == ("signal", "operator_set")
    assert e1["psychedelic"] == ("broad", "system_drafted")  # LLM-proposed -> BROAD, never SIGNAL

    # regenerate with NO body + a DIFFERENT proposal: seeds PRESERVED, LLM RE-ROLLED
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": ["entactogen"], "broad": []}
    )
    e2 = {
        x["term"]: (x["tier"], x["authored_by"])
        for x in client.post(f"/workbench/theses/{tid}/terms").json()["term_set"]
    }
    assert e2["psilocybin"] == ("signal", "operator_set")  # PRESERVED across regenerate (no body)
    assert e2["ibogaine"] == ("signal", "operator_set")
    assert (
        "entactogen" in e2 and "psychedelic" not in e2
    )  # the LLM half re-rolled (new in, old out)


def test_edit_terms_saves_directly_and_restamps_authorship(client, db):
    """PUT /terms/edit SAVES the operator's edited set directly (no LLM) and re-stamps authorship by diffing the
    stored set: an UNTOUCHED system_drafted BROAD keeps its authorship (stays re-rollable); a PROMOTE/DEMOTE
    becomes operator_edited (origin source preserved); an ADD becomes operator_set; a REMOVE drops. A fresh GET
    shows the saved set (full-set replace via the narrow set_term_set)."""
    from app.deps import get_keyword_client

    tid = client.post(
        "/workbench/theses",
        json={
            "name": "psy",
            "narrative": "psychedelic therapy",
            "ticker": None,
            "segments": [],
            "basket": [],
        },
    ).json()["id"]
    # seed psilocybin (operator SIGNAL) + two keyword-gen BROAD (ketamine, ibogaine)
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": [], "broad": ["ketamine", "ibogaine"]}
    )
    client.post(f"/workbench/theses/{tid}/terms", json={"seeds": ["psilocybin"]})

    # operator edits: keep psilocybin SIGNAL (untouched seed), promote ketamine->SIGNAL, leave ibogaine BROAD
    # (untouched system_drafted — proves that branch survives the save), add 5-MeO-DMT (digits allowed).
    r = client.put(
        f"/workbench/theses/{tid}/terms/edit",
        json={
            "terms": [
                {"term": "psilocybin", "tier": "signal"},
                {"term": "ketamine", "tier": "signal"},  # promote
                {"term": "ibogaine", "tier": "broad"},  # untouched system_drafted
                {
                    "term": "5-MeO-DMT",
                    "tier": "signal",
                },  # add (digits allowed — #3 bans a numeric FACT)
            ]
        },
    )
    assert r.status_code == 200
    by = {e["term"]: (e["tier"], e["authored_by"]) for e in r.json()["term_set"]}
    assert by["psilocybin"] == ("signal", "operator_set")  # untouched seed
    assert by["ketamine"] == ("signal", "operator_edited")  # promoted
    assert by["ibogaine"] == ("broad", "system_drafted")  # untouched -> still re-rollable
    assert by["5-MeO-DMT"] == ("signal", "operator_set")  # added (digits allowed)
    # persisted: a fresh GET shows the saved set
    assert {
        e["term"]: (e["tier"], e["authored_by"])
        for e in client.get(f"/theses/{tid}").json()["term_set"]
    } == by


def test_edit_terms_runs_no_llm(client, db):
    """STRUCTURAL: the save path resolves NO LLM dependency. We override get_keyword_client to RAISE; because
    edit_terms doesn't depend on it, FastAPI never instantiates it and the PUT still 200s — the LLM is out of
    the save path (mirrors LLM-out-of-promote)."""
    from app.deps import get_keyword_client

    tid = client.post(
        "/workbench/theses",
        json={"name": "psy", "narrative": "x", "ticker": None, "segments": [], "basket": []},
    ).json()["id"]

    def _boom():
        raise AssertionError("the keyword LLM must NOT be resolved on the save path")

    app.dependency_overrides[get_keyword_client] = _boom
    r = client.put(
        f"/workbench/theses/{tid}/terms/edit",
        json={"terms": [{"term": "psilocybin", "tier": "signal"}]},
    )
    assert r.status_code == 200
    assert [e["term"] for e in r.json()["term_set"]] == ["psilocybin"]


def test_edit_terms_422_on_duplicate_and_empty(client, db):
    tid = client.post(
        "/workbench/theses",
        json={"name": "psy", "narrative": "x", "ticker": None, "segments": [], "basket": []},
    ).json()["id"]
    dup = client.put(
        f"/workbench/theses/{tid}/terms/edit",
        json={
            "terms": [
                {"term": "psilocybin", "tier": "signal"},
                {"term": "Psilocybin", "tier": "broad"},
            ]
        },
    )
    assert dup.status_code == 422 and "duplicate" in dup.json()["detail"]
    empty = client.put(
        f"/workbench/theses/{tid}/terms/edit", json={"terms": [{"term": "   ", "tier": "signal"}]}
    )
    assert empty.status_code == 422


def test_edit_terms_empty_list_clears_the_set(client, db):
    """An empty terms list clears the set (a visible operator choice) — the draft then 503s 'term set is empty'."""
    from app.deps import get_keyword_client

    tid = client.post(
        "/workbench/theses",
        json={"name": "psy", "narrative": "x", "ticker": None, "segments": [], "basket": []},
    ).json()["id"]
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": [], "broad": ["ketamine"]}
    )
    client.post(f"/workbench/theses/{tid}/terms")
    r = client.put(f"/workbench/theses/{tid}/terms/edit", json={"terms": []})
    assert r.status_code == 200 and r.json()["term_set"] == []
    assert client.get(f"/theses/{tid}").json()["term_set"] == []  # cleared


def test_produce_terms_preserves_operator_edited_on_regenerate(client, db):
    """END-TO-END #9 core: after the operator EDITS the set (a demotion + a promotion via PUT /terms/edit), a
    REGENERATE (re-POST /terms) preserves BOTH operator_edited entries VERBATIM (a demoted term stays BROAD, NOT
    re-promoted) while re-rolling only the system_drafted BROAD. Operator work is never silently lost on a
    re-roll."""
    from app.deps import get_keyword_client

    tid = client.post(
        "/workbench/theses",
        json={
            "name": "psy",
            "narrative": "psychedelic therapy",
            "ticker": None,
            "segments": [],
            "basket": [],
        },
    ).json()["id"]
    # produce: seed psilocybin (SIGNAL) + keyword-gen ketamine, ibogaine (BROAD)
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": [], "broad": ["ketamine", "ibogaine"]}
    )
    client.post(f"/workbench/theses/{tid}/terms", json={"seeds": ["psilocybin"]})
    # edit: DEMOTE psilocybin SIGNAL->BROAD, PROMOTE ketamine BROAD->SIGNAL, drop ibogaine
    client.put(
        f"/workbench/theses/{tid}/terms/edit",
        json={
            "terms": [
                {"term": "psilocybin", "tier": "broad"},
                {"term": "ketamine", "tier": "signal"},
            ]
        },
    )
    # regenerate with a DIFFERENT keyword-gen roll
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": [], "broad": ["entactogen"]}
    )
    e = {
        x["term"]: (x["tier"], x["authored_by"])
        for x in client.post(f"/workbench/theses/{tid}/terms").json()["term_set"]
    }
    assert e["psilocybin"] == (
        "broad",
        "operator_edited",
    )  # demotion SURVIVED (not re-promoted to SIGNAL)
    assert e["ketamine"] == ("signal", "operator_edited")  # promotion survived
    assert "entactogen" in e  # the system_drafted half re-rolled
    assert "ibogaine" not in e  # the dropped system_drafted term did not resurface this roll


# --- the tier RECOMMENDER (INVARIANT #10): the LLM recommends, the operator decides ---


def _seeded_term_thesis(client, db) -> str:
    """A thesis with a produced term set (psilocybin SIGNAL seed + ketamine/ibogaine system_drafted BROAD)."""
    from app.deps import get_keyword_client

    tid = client.post(
        "/workbench/theses",
        json={
            "name": "psy",
            "narrative": "psychedelic therapy",
            "ticker": None,
            "segments": [],
            "basket": [],
        },
    ).json()["id"]
    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(
        returns={"signal": [], "broad": ["ketamine", "ibogaine"]}
    )
    client.post(f"/workbench/theses/{tid}/terms", json={"seeds": ["psilocybin"]})
    return tid


def test_recommend_tiers_returns_recs_aligned_to_the_stored_set(client, db):
    """The recommender returns a tier + reason per term, aligned to the stored set (only terms the model
    returned, in the set's order). DISPLAY-ONLY — a separate wire type, never on ThesisDetail.term_set.
    """
    from app.deps import get_tier_rec_client

    tid = _seeded_term_thesis(client, db)
    app.dependency_overrides[get_tier_rec_client] = lambda: _FakeLLM(
        returns={
            "recommendations": [
                {
                    "term": "psilocybin",
                    "tier": "signal",
                    "reason": "a specific psychedelic compound",
                },
                {
                    "term": "ketamine",
                    "tier": "signal",
                    "reason": "discriminating dissociative compound",
                },
                {"term": "zzz-not-in-set", "tier": "broad", "reason": "ignored — not in the set"},
            ]
        }
    )
    r = client.post(f"/workbench/theses/{tid}/recommend-tiers")
    assert r.status_code == 200
    by = {x["term"]: x for x in r.json()}
    assert by["psilocybin"]["recommended_tier"] == "signal"
    assert (
        by["ketamine"]["recommended_tier"] == "signal"
    )  # OFFENSE: a BROAD term recommended SIGNAL
    assert by["ketamine"]["reason"] == "discriminating dissociative compound"
    assert "zzz-not-in-set" not in by  # only terms present in the stored set are returned


def test_recommend_tiers_persists_nothing(client, db):
    """THE #10 STRUCTURAL BOUND, test-enforced (like test_draft_endpoint_writes_nothing): a recommendation can
    NEVER become a persisted tier — the stored term_set is byte-identical before/after, and no authored_by
    moves. The endpoint calls no writer."""
    from app.deps import get_tier_rec_client

    tid = _seeded_term_thesis(client, db)
    before = client.get(f"/theses/{tid}").json()["term_set"]
    # the model recommends the OPPOSITE tier for every term — yet nothing is applied
    app.dependency_overrides[get_tier_rec_client] = lambda: _FakeLLM(
        returns={
            "recommendations": [
                {"term": "psilocybin", "tier": "broad", "reason": "x"},  # DEFENSE rec — NOT applied
                {"term": "ketamine", "tier": "signal", "reason": "y"},  # OFFENSE rec — NOT applied
                {"term": "ibogaine", "tier": "signal", "reason": "z"},
            ]
        }
    )
    assert client.post(f"/workbench/theses/{tid}/recommend-tiers").status_code == 200
    after = client.get(f"/theses/{tid}").json()["term_set"]
    assert after == before  # byte-identical: tiers + authored_by + source all unchanged


def test_recommend_tiers_failopen_no_key(client, db, monkeypatch):
    """No key: the real client's offline gate is caught inside recommend_tiers -> the endpoint returns 200 []
    (the chips render with no recommendation), never a 5xx."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tid = _seeded_term_thesis(client, db)
    r = client.post(f"/workbench/theses/{tid}/recommend-tiers")
    assert r.status_code == 200 and r.json() == []


def test_recommend_tiers_404_for_unknown_thesis(client):
    assert client.post(f"/workbench/theses/{uuid.uuid4()}/recommend-tiers").status_code == 404


def _insert_security(db, ticker, *, name=None, cik=None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def test_securities_search_serves_the_master(client, db):
    """The authoring typeahead (Slice 4b): the resolver surfaces exact master rows for the operator to pick
    — a discovery net (INVARIANT #2), never a guess. No match -> []."""
    oklo = _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    _insert_security(db, "LEU", name="Centrus Energy Corp.")
    hits = client.get("/workbench/securities", params={"q": "OK"}).json()
    assert [h["ticker"] for h in hits] == ["OKLO"]
    assert hits[0]["security_id"] == str(oklo) and hits[0]["cik"] == "0001849056"
    assert client.get("/workbench/securities", params={"q": "ZZZ"}).json() == []


def test_extract_endpoint_serves_candidates(client, security_id, monkeypatch):
    """The extract route resolves the security's CIK, runs the extractor, and serves the candidate facts
    (the extraction LOGIC is covered by the offline golden test; this covers the route + CIK resolution +
    the wire shape). The live SEC fetch is monkeypatched so the test stays offline."""
    from app.routers import workbench as wb
    from domain.extraction import ExtractedFact, LocatedPassage, Tier

    fake = [
        ExtractedFact(
            fact_type="cash_burn",
            tier=Tier.FLAG,
            source="10-q",
            source_ref="https://sec.gov/x.htm",
            event_date=date(2026, 3, 31),
            cash_usd=1_000.0,
            quarterly_burn_usd=314_678_000.0,
            flags=["possible-one-time"],
            located_passages=[
                LocatedPassage(
                    kind="cash-flow",
                    source_ref="https://sec.gov/x.htm",
                    anchor="264,195",
                    excerpt="… accrued (264,195) …",
                )
            ],
        )
    ]
    monkeypatch.setattr(wb, "extract_for_security", lambda client, cik: fake)
    r = client.get(f"/workbench/securities/{security_id}/extract")
    assert r.status_code == 200
    f = r.json()[0]
    assert f["fact_type"] == "cash_burn" and f["tier"] == "flag"
    assert f["flags"] == ["possible-one-time"]
    assert f["located_passages"][0]["anchor"] == "264,195"


def test_extract_endpoint_404_without_cik(client, db):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s,%s,%s,%s,%s)",
            (sid, DEFAULT_TENANT_ID, "NOCIK", None, date(2026, 1, 1)),
        )
    db.commit()
    assert client.get(f"/workbench/securities/{sid}/extract").status_code == 404


def test_promote_honors_authorship_from_the_body(client, security_id):
    """Promote HONORS `authored_by` (it no longer coerces to operator_set, now that the S5 drafter's own
    path exists): an S5-drafted placement the operator keeps stays `system_drafted`, an edited one
    `operator_edited`, a hand-authored one `operator_set` — the seam round-trips so the badge + the eventual
    ratify can tell drafted from operator-set. An out-of-enum value is rejected at the schema boundary.
    """

    def _payload(authored_by):
        return {
            "name": "Nuclear",
            "narrative": "x",
            "ticker": None,
            "segments": [{"label": "reactors"}],
            "basket": [
                {
                    "ticker": "DEVCO",
                    "role": "r",
                    "archetype": "leader",
                    "security_id": str(security_id),
                    "segment": "reactors",
                    "authored_by": authored_by,
                }
            ],
        }

    for authored_by in ("system_drafted", "operator_edited", "operator_set"):
        tid = client.post("/workbench/theses", json=_payload(authored_by)).json()["id"]
        detail = client.get(f"/theses/{tid}").json()
        assert detail["basket"][0]["authored_by"] == authored_by  # honored, not coerced
    # an out-of-enum authorship is a 422 at parse time (Pydantic validates against the enum)
    assert client.post("/workbench/theses", json=_payload("robot")).status_code == 422


def test_promote_rejects_a_security_not_in_this_tenants_master(client):
    """Bound #2 at the single writer (relocated here now that the S5 drafter returns a draft and writes
    nothing): a placed `security_id` that isn't an EXACT member of this tenant's master fails closed — a
    hallucinated / foreign id never reaches the spine. Distinct from the orphan-segment 422: the chain is
    consistent here; the SECURITY is the problem (mirrors the ratify write-side check → 404)."""
    payload = {
        "name": "Nuclear",
        "narrative": "x",
        "ticker": None,
        "segments": [{"label": "reactors"}],
        "basket": [
            {
                "ticker": "GHOST",
                "role": "r",
                "archetype": "leader",
                "security_id": str(uuid.uuid4()),  # not in this tenant's master
                "segment": "reactors",
            }
        ],
    }
    r = client.post("/workbench/theses", json=payload)
    assert r.status_code == 404
    assert "not in this tenant's master" in r.json()["detail"]


def test_promote_persists_thesis_fit(client, security_id):
    """The thesis-fit prose round-trips the spine (draft -> promote -> re-read): a basket member's
    `thesis_fit` (the "why it sits here" reasoning) persists ALONGSIDE its `authored_by`. This is the column
    5c's UI promotes the drafted prose into; it's kept distinct from `detail` (the live "met" cell).
    """
    payload = {
        "name": "Nuclear",
        "narrative": "x",
        "ticker": None,
        "segments": [{"label": "reactors"}],
        "basket": [
            {
                "ticker": "DEVCO",
                "role": "r",
                "archetype": "leader",
                "security_id": str(security_id),
                "segment": "reactors",
                "thesis_fit": "the only NRC-approved SMR designer in the US",
                "authored_by": "system_drafted",
            }
        ],
    }
    tid = client.post("/workbench/theses", json=payload).json()["id"]
    member = client.get(f"/theses/{tid}").json()["basket"][0]
    assert member["thesis_fit"] == "the only NRC-approved SMR designer in the US"
    assert member["authored_by"] == "system_drafted"  # honored, and the prose rides alongside it


# --- hybrid-2a: ratify a scoring fact (the first fact-WRITE) ---


def _thesis_with(db, security_id) -> uuid.UUID:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="nuclear",
        narrative="x",
        segments=[Segment(label="reactors")],
        basket=[
            BasketMember(
                ticker="DEVCO",
                role="r",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
                segment="reactors",
            )
        ],
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t.id


def test_ratify_cash_burn_writes_and_rederives_runway(client, db, security_id):
    """The loop: ratifying the RECURRING burn (the operator's composition, not the raw) writes the fact and
    the runway meter re-derives. cash 1B / (50.483M/3) ~ 59 months -> 4 pips; the raw 314.678M would be 1.
    """
    tid = _thesis_with(db, security_id)
    m0 = client.get(f"/workbench/theses/{tid}/scored", params={"asof": "2026-06-02"}).json()[
        "members"
    ][0]
    assert m0["runway"]["pips"] is None  # no cash_burn fact yet -> "—"
    r = client.post(
        "/workbench/facts",
        json={
            "fact_type": "cash_burn",
            "security_id": str(security_id),
            "source": "10-q",
            "source_ref": "https://www.sec.gov/smr.htm",
            "event_date": "2026-03-31",
            "note": "recurring — the ENTRA1 settlement backed out",
            "cash_usd": 1_000_000_000,
            "quarterly_burn_usd": 50_483_000,
        },
    )
    assert r.status_code == 200 and r.json()["fact_type"] == "cash_burn"
    m1 = client.get(f"/workbench/theses/{tid}/scored", params={"asof": "2026-06-02"}).json()[
        "members"
    ][0]
    assert m1["runway"]["pips"] == 4  # the recurring burn -> a comfortable runway
    with db.cursor() as cur:
        cur.execute(
            "SELECT ratified_by, source FROM fact_cash_burn WHERE security_id=%s",
            (security_id,),
        )
        row = cur.fetchone()
    assert row["ratified_by"] == "operator" and row["source"] == "10-q"  # stamped + basis preserved


def test_ratify_revenue_mix_preserves_the_basis_source(client, db, security_id):
    """`source` is the candidate's BASIS (10-k-segment), NOT flattened to 'ratified' — the DD-rail basis
    provenance (the chip) stays honest."""
    tid = _thesis_with(db, security_id)
    client.post(
        "/workbench/facts",
        json={
            "fact_type": "revenue_mix",
            "security_id": str(security_id),
            "source": "10-k-segment",
            "source_ref": "https://www.sec.gov/10k.htm",
            "event_date": "2025-12-31",
            "segment_label": "nuclear",
            "mix_pct": 100,
        },
    )
    m = client.get(f"/workbench/theses/{tid}/scored", params={"asof": "2026-06-02"}).json()[
        "members"
    ][0]
    assert m["purity"]["pips"] == 4  # 100% -> 4 pips
    assert m["purity"]["provenance"][0]["source"] == "10-k-segment"  # the basis, preserved


def test_ratify_rejects_security_not_in_tenant(client):
    """Write-side tenant discipline: a security_id not in THIS tenant's master fails closed (no junk fact)."""
    r = client.post(
        "/workbench/facts",
        json={
            "fact_type": "shares_outstanding",
            "security_id": str(uuid.uuid4()),
            "source": "10-q-cover",
            "source_ref": "https://www.sec.gov/x.htm",
            "event_date": "2026-03-31",
            "shares": 1_000_000,
        },
    )
    assert r.status_code == 404


def test_ratify_missing_field_is_422(client, security_id):
    """The discriminated union validates per-type required fields — cash_burn without quarterly_burn_usd."""
    r = client.post(
        "/workbench/facts",
        json={
            "fact_type": "cash_burn",
            "security_id": str(security_id),
            "source": "10-q",
            "source_ref": "https://www.sec.gov/x.htm",
            "event_date": "2026-03-31",
            "cash_usd": 1_000_000,
        },
    )
    assert r.status_code == 422


# --- M4b: the FLAG-explanation drafter (the LLM seam) — a display aid that never becomes a fact ---


class _FakeLLM:
    """A stand-in for the live ``LLMClient`` (no network, no key) — returns/raises what the test wants. Supports
    the forced-tool ``draft_structured`` (flag + decompose) AND the auto-tool ``research`` (Slice 1), and
    records each call so a test can assert the research→decompose wiring."""

    def __init__(
        self,
        *,
        returns=None,
        raises: Exception | None = None,
        research_returns=None,
        research_raises: Exception | None = None,
        narrate_returns=None,
    ) -> None:
        self._returns = returns
        self._raises = raises
        self._research_returns = research_returns
        self._research_raises = research_raises
        self._narrate_returns = (
            narrate_returns  # returned when the NARRATE tool is used (else _returns)
        )
        self.calls: list[dict] = []
        self.research_calls: list[dict] = []

    def draft_structured(self, *, system, user, tool):
        self.calls.append({"system": system, "user": user, "tool": tool})
        if self._raises is not None:
            raise self._raises
        # the same decompose client serves BOTH the organizer (draft_value_chain) and the prose-fill
        # (narrate_placements); switch on the tool so a test can drive each independently.
        if tool.get("name") == "narrate_placements" and self._narrate_returns is not None:
            return self._narrate_returns
        return self._returns

    def research(self, *, system, user, tool):
        self.research_calls.append({"system": system, "user": user, "tool": tool})
        if self._research_raises is not None:
            raise self._research_raises
        return self._research_returns


def _flag_candidate() -> dict:
    """A FLAG cash_burn candidate as the FE sends it back (the ExtractedFact it got from extract)."""
    return {
        "fact_type": "cash_burn",
        "tier": "flag",
        "source": "10-q-cashflow",
        "source_ref": "https://sec.gov/smr-10q#p1",
        "event_date": "2026-03-31",
        "cash_usd": 890_000_000,
        "quarterly_burn_usd": 314_678_000,
        "flags": ["possible-one-time"],
        "located_passages": [
            {
                "kind": "cash-flow-line",
                "source_ref": "https://sec.gov/smr-10q#p1",
                "anchor": "264,195",
                "excerpt": "Partnership milestone payment of 264,195 in operating cash use.",
            }
        ],
    }


def test_explain_endpoint_drafts_for_a_flag_candidate(client):
    from app.deps import get_llm_client

    fake = _FakeLLM(
        returns={
            "explanation": "The cash use includes a one-time ~$264M milestone; recurring is lower.",
            "grounded": True,
        }
    )
    app.dependency_overrides[get_llm_client] = lambda: fake
    r = client.post("/workbench/facts/explain", json=_flag_candidate())
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] is True and "milestone" in body["explanation"]


def test_explain_endpoint_is_fail_open_never_5xx(client, monkeypatch):
    """No fake, no key: the REAL client's offline gate (LLMUnavailable) is caught -> 200 + grounded:false.
    Fail-open by contract — the facts panel works exactly as today."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/workbench/facts/explain", json=_flag_candidate())
    assert r.status_code == 200  # NOT a 502/500
    assert r.json() == {"explanation": "", "grounded": False}


def test_explaining_writes_no_fact(client, db):
    """THE BOUND: a grounded explanation that even names a figure creates ZERO scoring facts — the explain
    endpoint takes no DB connection and rides a separate rail (the ratified number can only come from the
    operator's /facts field). The candidate payload carries no security_id at all."""
    from app.deps import get_llm_client

    fake = _FakeLLM(
        returns={
            "explanation": "Strip the 264,195 milestone and recurring is lower.",
            "grounded": True,
        }
    )
    app.dependency_overrides[get_llm_client] = lambda: fake
    assert client.post("/workbench/facts/explain", json=_flag_candidate()).status_code == 200
    with db.cursor() as cur:
        for table in ("fact_cash_burn", "fact_shares_outstanding", "fact_revenue_mix"):
            cur.execute(
                f"SELECT count(*) AS n FROM {table}"
            )  # noqa: S608 — fixed literal table names
            assert cur.fetchone()["n"] == 0  # explaining persisted nothing


def test_explanation_has_no_path_into_a_ratified_fact():
    """The structural half of the bound: no ratify variant has a field an explanation could ride in on
    (no 'explanation'/'grounded'/'draft'). Pure schema guard — a regression here would re-open the rail.
    """
    from app.schemas_api import RatifyCashBurn, RatifyRevenueMix, RatifyShares

    forbidden = {"explanation", "grounded", "draft", "drafted"}
    for model in (RatifyRevenueMix, RatifyShares, RatifyCashBurn):
        assert forbidden.isdisjoint(model.model_fields)


# --- S5/Slice 4b: the EDGAR-first draft endpoint (discovery -> tail-sweep -> organizer -> reconcile) ---


class _FakeEfts:
    """Canned EFTS pages by cache_key (``efts/{kw}_{from}.json``); an unknown key -> an empty page. ``raises``
    makes every page fetch fail (every page fails after retries -> discover() -> DiscoveryDegraded).
    """

    def __init__(self, pages: dict, *, raises: bool = False) -> None:
        self.pages = pages
        self.raises = raises

    def get_json(self, url, cache_key):
        if self.raises:
            raise RuntimeError("EFTS unreachable")
        return self.pages.get(cache_key, {"hits": {"total": {"value": 0}, "hits": []}})


def _efts_page(*rows: tuple[str, str]) -> dict:
    """An EFTS page: each row is ``(cik, display_name)``."""
    return {
        "hits": {
            "total": {"value": len(rows)},
            "hits": [{"_source": {"ciks": [c], "display_names": [d]}} for c, d in rows],
        }
    }


def _decomp(*placements: tuple[str, str]) -> dict:
    """A fake decompose tool-output: one segment 'reactors' with the given (name, ticker) placements."""
    return {
        "segments": [
            {
                "label": "reactors",
                "placements": [
                    {"name": n, "ticker": t, "prose": "why it sits here"} for n, t in placements
                ],
            }
        ]
    }


def _thesis_for_draft(
    db, *, terms: tuple[str, ...] = ("nuclear",), broad: tuple[str, ...] = ()
) -> uuid.UUID:
    """A persisted thesis with an EMPTY basket (so basket_member starts at 0 — the writes-nothing assertion is
    unambiguous) and a stored term set (discovery READS it since T3 — ``terms=()`` produces NO term set, the
    not-ready state). ``terms`` are SIGNAL seeds, ``broad`` are BROAD terms (a CIK hitting only a broad term ->
    VERIFY). Default seed ``nuclear`` matches the EFTS ``efts/nuclear_0.json`` pages below.
    """
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="nuclear",
        narrative="small modular nuclear is about to rip",
    )
    thesis_repo.upsert(db, t)
    entries = [TermSetEntry(term=x, tier=TermTier.SIGNAL) for x in terms] + [
        TermSetEntry(term=x, tier=TermTier.BROAD) for x in broad
    ]
    if entries:
        thesis_repo.set_term_set(db, t.id, entries)
    db.commit()
    return t.id


def _override_draft(*, edgar=None, research=None, decompose=None):
    """Override the three draft LLM/EFTS seams (the ``client`` fixture clears overrides after the test). Since T3
    the draft path no longer calls keyword-gen — discovery reads the thesis's stored term set. Defaults: an empty
    EFTS, no tail-sweep, an empty decompose.
    """
    from app.deps import get_decompose_client, get_edgar_client, get_research_client

    app.dependency_overrides[get_edgar_client] = lambda: edgar or _FakeEfts({})
    app.dependency_overrides[get_research_client] = lambda: research or _FakeLLM(
        research_returns=None
    )
    app.dependency_overrides[get_decompose_client] = lambda: decompose or _FakeLLM(returns=None)


@pytest.fixture(autouse=True)
def _inline_draft_jobs(monkeypatch):
    """Run draft jobs INLINE (synchronously) so a kicked-off draft is terminal by the time the 202 returns — no
    thread-timing flakiness, no race with the test-DB teardown. Reset the in-process registry per test. (The
    thunk still opens its OWN ``connect()`` to ``alphadeck_test`` and sees the helpers' COMMITTED rows — exactly
    the prod path, minus the thread.)"""
    from workbench import draft_jobs

    draft_jobs.reset_state()
    monkeypatch.setattr(
        draft_jobs, "_DEFAULT_EXECUTOR", lambda job, run: draft_jobs._run_job(job, run)
    )
    yield
    draft_jobs.reset_state()


def _draft(client, tid) -> dict:
    """Kick off the draft (202 + job_id) then poll once — the inline executor makes the job terminal before the
    202 returns, so a single poll is conclusive. Returns the poll body ({status, result, error})."""
    started = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    polled = client.get(f"/workbench/theses/{tid}/draft-chain/jobs/{job_id}")
    assert polled.status_code == 200, polled.text
    return polled.json()


def test_draft_endpoint_resolves_via_discovery(client, db):
    """The EDGAR-first wire: the stored term set -> EFTS discovery (a CIK in the master) -> organizer decompose
    -> reconcile by CIK -> PLACED with that CIK's id. An off-universe name the organizer adds falls to the master
    resolver -> ABSENT. Exact membership decides; the endpoint only composes."""
    oklo = _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)  # seed term "nuclear" -> EFTS efts/nuclear_0.json
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"))}
    )
    _override_draft(
        edgar=edgar,
        decompose=_FakeLLM(returns=_decomp(("Oklo Inc.", "OKLO"), ("Ghost Co", "ZZZZ"))),
    )
    body = _draft(client, tid)
    assert body["status"] == "done"
    result = body["result"]
    assert result["thesis_id"] == str(tid)
    by_name = {p["name"]: p for p in result["placements"]}
    assert by_name["Oklo Inc."]["status"] == "placed"
    assert by_name["Oklo Inc."]["security_id"] == str(oklo)  # PLACED by its EDGAR CIK
    assert by_name["Oklo Inc."]["matched_terms"] == [
        "nuclear"
    ]  # provenance: the term that surfaced it (#9)
    assert by_name["Oklo Inc."]["discovery_source"] == "edgar"  # matched an EDGAR-discovered CIK
    assert by_name["Ghost Co"]["status"] == "absent"  # off-universe -> master resolver
    assert by_name["Ghost Co"]["matched_terms"] == []  # off-universe -> no discovery term
    # the tail-sweep provenance rides the response: a name matching no discovered CIK is "off_universe"
    assert by_name["Ghost Co"]["discovery_source"] == "off_universe"


def test_draft_endpoint_writes_nothing(client, db):
    """RESPONSE-ONLY, TEST-ENFORCED (the endpoint HAS a read-only conn — to read the narrative + resolve — so
    "writes nothing" is THIS test, not absence-of-conn like the flag seam): drafting a chain persists ZERO
    fact_* rows AND adds ZERO basket_member rows. The operator's promote is the only writer."""
    _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)  # empty basket; term "nuclear" -> EFTS places OKLO by CIK
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"))}
    )
    _override_draft(edgar=edgar, decompose=_FakeLLM(returns=_decomp(("Oklo Inc.", "OKLO"))))
    assert _draft(client, tid)["status"] == "done"
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM basket_member WHERE thesis_id = %s", (tid,))
        assert cur.fetchone()["n"] == 0  # the draft persisted no placement
        for table in ("fact_cash_burn", "fact_shares_outstanding", "fact_revenue_mix"):
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed literal names
            assert cur.fetchone()["n"] == 0  # and no scoring fact


def test_draft_endpoint_failopen_never_5xx(client, db, monkeypatch):
    """No key: the LLM seams' offline gates fail open — tail-sweep -> None, decompose (LLMUnavailable) -> empty
    layout — yet discovery is FREE + deterministic (it reads the stored term set + a faked EFTS), so the draft
    is 200 with the discovered name surfaced in 'Discovered', NEVER a 5xx. (EFTS is faked to avoid live network;
    the real research/decompose clients exercise the no-key path.)"""
    from app.deps import get_edgar_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    oklo = _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)  # stored term "nuclear"
    app.dependency_overrides[get_edgar_client] = lambda: _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"))}
    )
    body = _draft(client, tid)
    assert (
        body["status"] == "done"
    )  # NOT failed — the LLM seams failed open, discovery carried the draft
    result = body["result"]
    assert result["thesis_id"] == str(tid)
    by_name = {p["name"]: p for p in result["placements"]}
    assert by_name["Oklo Inc."]["security_id"] == str(oklo)  # discovered + placed despite no LLM


def test_draft_endpoint_404_for_unknown_thesis(client):
    r = client.post(f"/workbench/theses/{uuid.uuid4()}/draft-chain")
    assert r.status_code == 404


def test_draft_endpoint_threads_discovery_and_sweep_into_decompose(client, db):
    """The EDGAR names AND the directed tail-sweep synthesis are both threaded into the organizer decompose as
    CONTEXT (the model ORGANIZES, never enumerates), and the tail-sweep receives the already-found list so it
    looks for what's MISSING."""
    _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"))}
    )
    research = _FakeLLM(research_returns="Foreign tail: Nuclear ADR Co (NADR).")
    decompose = _FakeLLM(returns=_decomp(("Oklo Inc.", "OKLO")))
    _override_draft(edgar=edgar, research=research, decompose=decompose)
    assert _draft(client, tid)["status"] == "done"
    user = decompose.calls[0]["user"]
    assert "Current research" in user
    assert "Oklo Inc." in user and "(OKLO)" in user  # the EDGAR name+ticker reached the organizer
    assert "Nuclear ADR Co" in user  # the tail-sweep synthesis threaded in
    assert "Oklo Inc." in research.research_calls[0]["user"]  # found list given to the sweep


def test_draft_endpoint_tail_sweep_failure_still_drafts_on_edgar_context(client, db):
    """Fail-open: if the tail-sweep RAISES, the draft does NOT go empty — the EDGAR discovery context survives,
    the organizer runs on it, and the chain resolves by CIK. (Only the tail-sweep is the expensive call.)
    """
    oklo = _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"))}
    )
    decompose = _FakeLLM(returns=_decomp(("Oklo Inc.", "OKLO")))
    _override_draft(
        edgar=edgar,
        research=_FakeLLM(research_raises=RuntimeError("web search down")),
        decompose=decompose,
    )
    body = _draft(client, tid)
    assert body["status"] == "done"
    user = decompose.calls[0]["user"]
    assert (
        "Current research" in user and "Oklo Inc." in user
    )  # EDGAR context survived the sweep failure
    by_name = {p["name"]: p for p in body["result"]["placements"]}
    assert by_name["Oklo Inc."]["status"] == "placed" and by_name["Oklo Inc."][
        "security_id"
    ] == str(oklo)


def test_draft_endpoint_dropped_discovered_name_surfaces(client, db):
    """End-to-end per-CIK completeness: EFTS finds two in-master names; the organizer arranges only ONE; the
    dropped one STILL appears (in 'Discovered', by its CIK). The deterministic layer owns completeness. AND the
    reconciler-appended name (no organizer prose) gets thesis-fit prose from the fail-open narration step, plus
    its matched discovery term as provenance (#9).
    """
    _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    smr = _insert_security(db, "SMR", name="NuScale Power Corporation", cik="0001822966")
    tid = _thesis_for_draft(db)
    edgar = _FakeEfts(
        {
            "efts/nuclear_0.json": _efts_page(
                ("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"),
                ("0001822966", "NuScale Power Corporation  (SMR)  (CIK 0001822966)"),
            )
        }
    )
    _override_draft(
        edgar=edgar,
        decompose=_FakeLLM(
            returns=_decomp(
                ("Oklo Inc.", "OKLO")
            ),  # SMR dropped by the organizer (no prose for it)
            narrate_returns={  # the prose-fill narrates the reconciler-appended name (by ref — NuScale is #1)
                "placements": [{"ref": 1, "prose": "the only NRC-approved SMR designer"}]
            },
        ),
    )
    out = _draft(client, tid)
    assert out["status"] == "done"
    body = out["result"]
    by_name = {p["name"]: p for p in body["placements"]}
    assert by_name["Oklo Inc."]["status"] == "placed"
    assert by_name["Oklo Inc."]["matched_terms"] == [
        "nuclear"
    ]  # provenance on the organizer-matched name
    nuscale = by_name["NuScale Power Corporation"]
    assert nuscale["status"] == "placed"  # dropped by the organizer, surfaced by reconciliation
    assert nuscale["segment"] == "Discovered" and nuscale["security_id"] == str(smr)
    assert nuscale["matched_terms"] == ["nuclear"]  # provenance on the reconciler-appended name
    assert (
        nuscale["prose"] == "the only NRC-approved SMR designer"
    )  # prose filled by narration (Bug 2)
    assert "Discovered" in [s["label"] for s in body["segments"]]


def test_draft_endpoint_narrates_verify_names_too(client, db):
    """VERIFY names are PROMOTABLE (the operator adds one -> it becomes a basket member carrying its draft-time
    prose), so they get narrated too — not just PLACED. A reconciler-appended VERIFY name (single broad hit) is
    filled by the prose step like a placed one."""
    _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    _insert_security(db, "GENCO", name="Generic Reactor Co", cik="0001000000")
    tid = _thesis_for_draft(
        db, terms=("nuclear",), broad=("reactor",)
    )  # GENCO hits only the broad term
    edgar = _FakeEfts(
        {
            "efts/nuclear_0.json": _efts_page(
                ("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)")
            ),
            "efts/reactor_0.json": _efts_page(
                ("0001000000", "Generic Reactor Co  (GENCO)  (CIK 0001000000)")
            ),
        }
    )
    _override_draft(
        edgar=edgar,
        decompose=_FakeLLM(
            returns=_decomp(
                ("Oklo Inc.", "OKLO")
            ),  # GENCO dropped by the organizer -> reconciled as VERIFY
            narrate_returns={"placements": [{"ref": 1, "prose": "reactor-component supplier"}]},
        ),
    )
    body = _draft(client, tid)["result"]
    genco = next(p for p in body["placements"] if p["name"] == "Generic Reactor Co")
    assert genco["status"] == "verify"  # single broad keyword -> lower-confidence tier
    assert (
        genco["prose"] == "reactor-component supplier"
    )  # narrated too (promotable -> needs reasoning)
    assert genco["matched_terms"] == ["reactor"]


def test_draft_endpoint_narration_failopen_leaves_prose_empty(client, db):
    """#9-safe fail-open: if the prose-fill narration RAISES, the reconciler-appended name keeps prose="" (never
    dropped, never a 5xx) — completeness is the deterministic layer's, prose is a best-effort display add.
    """
    _insert_security(db, "SMR", name="NuScale Power Corporation", cik="0001822966")
    tid = _thesis_for_draft(db)
    edgar = _FakeEfts(
        {
            "efts/nuclear_0.json": _efts_page(
                ("0001822966", "NuScale Power Corporation  (SMR)  (CIK 0001822966)")
            )
        }
    )
    # the organizer places nothing in-universe (Ghost is off-universe -> absent); SMR is reconciler-appended.
    # the decompose fake RAISES on every draft_structured -> both the organizer AND the narration fail open.
    _override_draft(edgar=edgar, decompose=_FakeLLM(raises=RuntimeError("LLM down")))
    body = _draft(client, tid)["result"]
    nuscale = next(p for p in body["placements"] if p["name"] == "NuScale Power Corporation")
    assert nuscale["status"] == "placed" and nuscale["prose"] == ""  # surfaced, prose empty, no 5xx
    assert nuscale["matched_terms"] == ["nuclear"]  # provenance still attached


def test_draft_endpoint_409_when_a_draft_is_already_running(client, db, monkeypatch):
    """The in-flight 409 guard, now at the JOB layer (one running draft per thesis): a second kick-off while a
    job is still running returns 409 — never a second (expensive) Opus pass. A no-op executor holds the first
    job 'running' so the thesis slot stays claimed."""
    from workbench import draft_jobs

    _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"))}
    )
    _override_draft(edgar=edgar, decompose=_FakeLLM(returns=_decomp(("Oklo", "OKLO"))))
    monkeypatch.setattr(
        draft_jobs, "_DEFAULT_EXECUTOR", lambda job, run: None
    )  # never runs -> stays running
    first = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert first.status_code == 202  # the slot is claimed
    second = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert second.status_code == 409  # the guard fires — no parallel Opus pass
    assert "already running" in second.json()["detail"]


def test_draft_failed_job_when_discovery_degraded(client, db):
    """COMPLETENESS-OR-FAIL end to end: the term set is present but EFTS pages all fail -> DiscoveryDegraded ->
    a VISIBLE *failed* job carrying "discovery unavailable" (the operator SEES it on the poll), NEVER a silent
    recall draft. (Discovery-not-ready moved from a synchronous 503 to a failed job in the async-draft slice.)
    """
    _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)
    _override_draft(
        edgar=_FakeEfts({}, raises=True),  # every EFTS page fails -> degraded
        decompose=_FakeLLM(
            returns=_decomp(("Oklo", "OKLO"))
        ),  # would have made a plausible recall draft
    )
    body = _draft(client, tid)
    assert body["status"] == "failed" and body["result"] is None
    assert "discovery unavailable" in body["error"]


def test_draft_failed_job_when_empty_despite_terms(client, db):
    """The term set enumerated terms but nothing placeable came back (the discovered CIK isn't in the master) ->
    against the populated master that is a BROKEN discovery -> a failed job, not a quiet recall fallback. The
    decompose fake would have produced a draft; the operator must not silently get it."""
    tid = _thesis_for_draft(
        db
    )  # NOTE: the discovered CIK is deliberately NOT inserted -> 0 placeable
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0009999999", "Ghost Co  (GHST)  (CIK 0009999999)"))}
    )
    _override_draft(edgar=edgar, decompose=_FakeLLM(returns=_decomp(("Ghost Co", "GHST"))))
    body = _draft(client, tid)
    assert body["status"] == "failed" and "discovery unavailable" in body["error"]


def test_draft_failed_job_when_no_term_set(client, db):
    """T3 readiness gate: a thesis with NO produced term set -> a failed job naming "term set is empty" (the
    not-ready state is VISIBLE on the poll), and EFTS is NEVER queried (discovery has nothing to read). Also the
    wipe-trap's last line: a blanked set would land here, not pass silently as an empty draft."""
    tid = _thesis_for_draft(db, terms=())  # no term set produced
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo  (OKLO)  (CIK ...)"))}
    )
    _override_draft(edgar=edgar, decompose=_FakeLLM(returns=_decomp(("Oklo", "OKLO"))))
    body = _draft(client, tid)
    assert body["status"] == "failed"
    assert "term set" in body["error"] and "empty" in body["error"]  # names the cause, not opaque


def test_draft_kickoff_returns_202_running_ref(client, db):
    """Kick-off returns a 202 + a job_id + status 'running' (the ref is always 'running' by contract — the
    result arrives on the poll), even though the inline test executor has already finished the job.
    """
    _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    tid = _thesis_for_draft(db)
    edgar = _FakeEfts(
        {"efts/nuclear_0.json": _efts_page(("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"))}
    )
    _override_draft(edgar=edgar, decompose=_FakeLLM(returns=_decomp(("Oklo Inc.", "OKLO"))))
    r = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert r.status_code == 202
    assert r.json()["status"] == "running" and r.json()["job_id"]


def test_draft_poll_404_for_unknown_job(client, db):
    """An unknown / expired / restart-wiped job_id -> 404 (the FE shows a visible 'draft was lost', never an
    infinite spinner)."""
    tid = _thesis_for_draft(db)
    r = client.get(f"/workbench/theses/{tid}/draft-chain/jobs/{uuid.uuid4().hex}")
    assert r.status_code == 404
