from __future__ import annotations

import uuid
from datetime import date

from app.main import app
from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Segment, Thesis
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
    ) -> None:
        self._returns = returns
        self._raises = raises
        self._research_returns = research_returns
        self._research_raises = research_raises
        self.calls: list[dict] = []
        self.research_calls: list[dict] = []

    def draft_structured(self, *, system, user, tool):
        self.calls.append({"system": system, "user": user, "tool": tool})
        if self._raises is not None:
            raise self._raises
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


# --- S5 5b: the narrative→chain draft endpoint (decompose -> resolve -> ChainDraftOut, response-only) ---


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


def _thesis_for_draft(db) -> uuid.UUID:
    """A persisted thesis with an EMPTY basket (so basket_member starts at 0 — the writes-nothing assertion
    is unambiguous)."""
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="nuclear",
        narrative="small modular nuclear is about to rip",
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t.id


def test_draft_endpoint_resolves_a_chain(client, db):
    """The wire: narrative -> decompose (faked) -> resolve_placements (5a) -> ChainDraftOut. A name in the
    master PLACES (exact ticker); a name not in the master is ABSENT — exact membership decides, the endpoint
    only composes."""
    from app.deps import get_decompose_client, get_research_client

    _insert_security(db, "OKLO", name="Oklo Inc.")
    tid = _thesis_for_draft(db)
    # override BOTH seams (no network): research returns nothing here, decompose is faked.
    app.dependency_overrides[get_research_client] = lambda: _FakeLLM(research_returns=None)
    app.dependency_overrides[get_decompose_client] = lambda: _FakeLLM(
        returns=_decomp(("Oklo", "OKLO"), ("Ghost Co", "ZZZZ"))
    )
    r = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert r.status_code == 200
    body = r.json()
    assert body["thesis_id"] == str(tid)
    assert [s["label"] for s in body["segments"]] == ["reactors"]
    by_name = {p["name"]: p for p in body["placements"]}
    assert by_name["Oklo"]["status"] == "placed" and by_name["Oklo"]["security_id"]
    assert by_name["Ghost Co"]["status"] == "absent" and by_name["Ghost Co"]["security_id"] is None


def test_draft_endpoint_writes_nothing(client, db):
    """RESPONSE-ONLY, TEST-ENFORCED (the endpoint HAS a read-only conn — to read the narrative + resolve — so
    "writes nothing" is THIS test, not absence-of-conn like the flag seam): drafting a chain persists ZERO
    fact_* rows AND adds ZERO basket_member rows. The operator's promote is the only writer."""
    from app.deps import get_decompose_client, get_research_client

    _insert_security(db, "OKLO", name="Oklo Inc.")
    tid = _thesis_for_draft(db)  # empty basket
    app.dependency_overrides[get_research_client] = lambda: _FakeLLM(research_returns=None)
    app.dependency_overrides[get_decompose_client] = lambda: _FakeLLM(
        returns=_decomp(("Oklo", "OKLO"))
    )
    assert client.post(f"/workbench/theses/{tid}/draft-chain").status_code == 200
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM basket_member WHERE thesis_id = %s", (tid,))
        assert cur.fetchone()["n"] == 0  # the draft persisted no placement
        for table in ("fact_cash_burn", "fact_shares_outstanding", "fact_revenue_mix"):
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed literal names
            assert cur.fetchone()["n"] == 0  # and no scoring fact


def test_draft_endpoint_failopen_never_5xx(client, db, monkeypatch):
    """No fake, no key: the REAL decompose client's offline gate (LLMUnavailable) is caught in
    decompose_narrative -> 200 with an EMPTY draft, NEVER a 5xx. Hand-authoring is untouched."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tid = _thesis_for_draft(db)
    r = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert r.status_code == 200  # NOT a 5xx
    body = r.json()
    assert body["thesis_id"] == str(tid)
    assert body["segments"] == [] and body["placements"] == []


def test_draft_endpoint_404_for_unknown_thesis(client):
    r = client.post(f"/workbench/theses/{uuid.uuid4()}/draft-chain")
    assert r.status_code == 404


def test_draft_endpoint_runs_research_then_decompose(client, db):
    """The two-step (Slice 1): the web-search research pass runs first, and its synthesis is threaded into the
    decompose call as CONTEXT. Both seams are faked (no network); we assert the research text reaches the
    decompose user message and the chain still resolves by exact membership."""
    from app.deps import get_decompose_client, get_research_client

    _insert_security(db, "OKLO", name="Oklo Inc.")
    tid = _thesis_for_draft(db)
    decompose = _FakeLLM(returns=_decomp(("Oklo", "OKLO")))
    app.dependency_overrides[get_research_client] = lambda: _FakeLLM(
        research_returns="Reactor developers: Oklo (OKLO) — lead SMR developer."
    )
    app.dependency_overrides[get_decompose_client] = lambda: decompose
    r = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert r.status_code == 200
    assert decompose.calls, "decompose was consulted"
    assert (
        "Oklo (OKLO)" in decompose.calls[0]["user"]
    )  # research threaded into the decompose prompt
    assert "Current research" in decompose.calls[0]["user"]
    by_name = {p["name"]: p for p in r.json()["placements"]}
    assert by_name["Oklo"]["status"] == "placed"


def test_draft_endpoint_research_failure_degrades_to_recall_only(client, db):
    """Fail-open refinement (Slice 1): if the RESEARCH pass fails (here it raises), the draft does NOT go empty
    — it degrades to the recall-only decompose (today's behavior). The decompose fake still runs with NO
    research context, and the chain resolves."""
    from app.deps import get_decompose_client, get_research_client

    _insert_security(db, "OKLO", name="Oklo Inc.")
    tid = _thesis_for_draft(db)
    decompose = _FakeLLM(returns=_decomp(("Oklo", "OKLO")))
    app.dependency_overrides[get_research_client] = lambda: _FakeLLM(
        research_raises=RuntimeError("web search down")
    )
    app.dependency_overrides[get_decompose_client] = lambda: decompose
    r = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert r.status_code == 200
    assert decompose.calls and "Current research" not in decompose.calls[0]["user"]  # recall-only
    by_name = {p["name"]: p for p in r.json()["placements"]}
    assert by_name["Oklo"]["status"] == "placed"  # the recall-only chain still resolves


def test_draft_endpoint_409_when_a_research_pass_is_already_running(client, db, monkeypatch):
    """The in-flight guard, surfaced: a draft for a thesis whose research pass is already running returns 409 —
    NOT a second (expensive) Opus call. We force the guard to fire by faking the runner to raise."""
    from app.deps import get_decompose_client, get_research_client
    from app.routers import workbench as wb
    from workbench.research_runner import ResearchInFlight

    _insert_security(db, "OKLO", name="Oklo Inc.")
    tid = _thesis_for_draft(db)
    app.dependency_overrides[get_research_client] = lambda: _FakeLLM(research_returns="x")
    app.dependency_overrides[get_decompose_client] = lambda: _FakeLLM(
        returns=_decomp(("Oklo", "OKLO"))
    )

    def _already_running(*args, **kwargs):
        raise ResearchInFlight(str(tid))

    monkeypatch.setattr(wb, "run_research", _already_running)
    r = client.post(f"/workbench/theses/{tid}/draft-chain")
    assert r.status_code == 409
    assert "already running" in r.json()["detail"]
