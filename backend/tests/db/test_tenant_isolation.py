"""The poison-row isolation proof for the TWO TENANT SCOPES (docs/TENANT_SCOPES.md, INVARIANTS §5).

Every table carries ``tenant_id``, and ``domain.scopes`` names which scope each one lives in: the shared DATA tenant
(the master + every ingested fact) or a user's WORKSPACE (the thesis spine, the record, the ratified judgments of
the four MIXED scoring-fact tables). These DB-backed tests certify — structurally, the same discipline-plus-test
posture as the harness's lookahead boundary — that:

- (a) a workspace's ratified fact is INVISIBLE to another workspace, while a DATA fact is visible to both;
- (b) an empty workspace re-derives to an honest ``Incubating`` from DATA facts alone (never a crash), and another
  workspace's ratified rows never reach it;
- (c) a workspace thesis arms off DATA facts + its own ratified rows and cites only accessions it can see — a
  second workspace naming the same company shares the arm the DATA facts produce, never the first workspace's
  ratified rows;
- (d) the call of record lands in the thesis's workspace; the smoke runs the whole cut on the identical path;
- every DATA read surface (the master, the 8-K / 13D tapes, the prefetch, the split list, the onboarding queue,
  the worklists) reads the data tenant and never a stray row stored under a workspace — the tenant filter stays
  load-bearing.

**No workspace here is ``…0001``** (the data tenant): with the data tenant equal to a user's workspace, a missed
scope filter reads the right rows by accident and the proof proves nothing. Isolation is discipline + this test,
not RLS (``security_master.id`` and every ``security_id`` FK carry no tenant).

DB-backed; skips when Postgres is unreachable (the shared ``db`` fixture). The two workspace ids are FIXED (the
``db`` fixture keeps ``tenant`` rows; ``new_workspace`` is ON CONFLICT DO NOTHING), so re-runs never accrete.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from db.bitemporal import append_fact, as_of, as_of_many, as_of_thesis
from domain.config import DEFAULT_CONFIG
from domain.enums import CatalystType, Grade, State, Verdict
from domain.scopes import DATA_TENANT_ID
from domain.thesis import BasketMember, Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.catalyst import ingest_catalyst
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from pipeline.call_for_thesis import call_for_thesis
from repositories import thesis_repo
from securities import master
from signals.base import PointInTimeData
from signals.horizons import call_bounds
from tests.workspaces import new_workspace
from workbench.chain_draft import (
    PlacementStatus,
    ProposedPlacement,
    ProposedSegment,
    resolve_placements,
)
from workbench.scoring import score_member

DATA = DATA_TENANT_ID
# two user WORKSPACES — neither is the data tenant (the trailing 'ad' / 'ae' are readable markers)
W = uuid.UUID("00000000-0000-0000-0000-0000000000ad")
V = uuid.UUID("00000000-0000-0000-0000-0000000000ae")

_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_KNOWN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_ARM_ASOF = date(
    2026, 6, 1
)  # the proven HIMS breakout date (co-located with the Wells cluster buy)


def _workspaces(db) -> None:
    new_workspace(db, "workspace W", workspace_id=W)
    new_workspace(db, "workspace V", workspace_id=V)


def _security(db, tenant_id: uuid.UUID = DATA, *, ticker: str = "HIMS", cik: str = "0001773751"):
    """Insert one security_master row under ``tenant_id`` (the DATA tenant unless a test plants a stray row
    under a workspace) and return its id."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, tenant_id, ticker, cik, date(2026, 1, 1)),
        )
    return sid


def _arm_facts(db, security_id: uuid.UUID, accession: str, tenant_id: uuid.UUID = DATA) -> None:
    """Ingest the proven HIMS arm fixtures (the Wells cluster buy + the breakout bars) as the FEED does: under
    the DATA tenant, with a named ``accession`` so a call's provenance shows which rows it read."""
    ingest_form4(
        db,
        security_id,
        (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        accession,
        tenant_id=tenant_id,
    )
    ingest_prices(
        db,
        security_id,
        parse_yahoo_chart(
            json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
        ),
        tenant_id=tenant_id,
    )


def _thesis(security_id: uuid.UUID, workspace: uuid.UUID, name: str) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        tenant_id=workspace,
        name=name,
        narrative="Insider conviction; waiting for the market to confirm.",
        ticker="HIMS",
        basket=[BasketMember(ticker="HIMS", role="the name", security_id=security_id)],
    )


def _ratified_catalyst(db, security_id, workspace, ref: str) -> None:
    """A workspace's own ratified catalyst (a MIXED-table judgment, written under the workspace)."""
    ingest_catalyst(
        db,
        security_id,
        catalyst_type=CatalystType.CONTRACT,
        grade=Grade.CORE,
        label=f"a contract {workspace} ratified",
        source="ratified",
        source_ref=ref,
        event_date=date(2026, 5, 1),
        ratified_by="operator",
        tenant_id=workspace,
    )


def _pit(db, workspace, **kw) -> PointInTimeData:
    return PointInTimeData(
        db, asof=_ARM_ASOF, known_at=_KNOWN, data_tenant=DATA, workspace=workspace, **kw
    )


def _refs(card) -> list[str]:
    return [p.ref for t in card.triggers_fired for p in t.sources]


def _insider(db, tenant_id, security_id, accession) -> None:
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": tenant_id,
            "security_id": security_id,
            "accession": accession,
            "insider_name": "An Insider",
            "txn_code": "P",
            "valid_from": date(2026, 5, 20),
        },
    )


# ---------------------------------------------------------------------------------------------------------
# (a) Read isolation, both scope axes.
# ---------------------------------------------------------------------------------------------------------
def test_read_isolation_both_axes(db):
    """A DATA fact is visible to every workspace; a workspace's own rows — a thesis-scoped fact, a ratified
    MIXED fact — are visible to that workspace ONLY, and a row stored under a workspace never enters a DATA
    read. Proves the tenant filter is load-bearing on ``as_of`` AND ``as_of_thesis`` AND the PIT, not just the
    scope id."""
    _workspaces(db)
    sid = _security(db)
    # security-scoped DATA axis: the feed's row, plus a stray under W (never the feed's shape)
    _insider(db, DATA, sid, "DATA-0001")
    _insider(db, W, sid, "W-STRAY-0001")
    for ws in (W, V):
        assert [r["accession"] for r in _pit(db, ws).insider_txns(sid)] == ["DATA-0001"]
    assert [
        r["accession"]
        for r in as_of(
            db, "fact_insider_txn", security_id=sid, asof=_ARM_ASOF, known_at=_KNOWN, tenant_id=DATA
        )
    ] == ["DATA-0001"]

    # thesis-scoped WORKSPACE axis: W's theme conviction is W's only
    w_thesis = _thesis(sid, W, "W theme")
    thesis_repo.upsert(db, w_thesis)
    append_fact(
        db,
        "fact_theme_conviction",
        {
            "tenant_id": W,
            "thesis_id": w_thesis.id,
            "grade": "flip",
            "label": "W theme conviction",
            "source": "ratified",
            "source_ref": "W-THEME-0001",
            "valid_from": date(2026, 5, 1),
        },
    )
    assert [r["source_ref"] for r in _pit(db, W).theme_conviction_facts(w_thesis.id)] == [
        "W-THEME-0001"
    ]
    assert _pit(db, V).theme_conviction_facts(w_thesis.id) == []
    for other in (V, DATA):
        assert (
            as_of_thesis(
                db,
                "fact_theme_conviction",
                thesis_id=w_thesis.id,
                asof=_ARM_ASOF,
                known_at=_KNOWN,
                tenant_id=other,
            )
            == []
        )

    # the MIXED axis: W's ratified purity is invisible to V
    ingest_revenue_mix(
        db,
        sid,
        segment_label="telehealth",
        mix_pct=90,
        source="ratified",
        source_ref="W-MIX",
        event_date=date(2026, 1, 1),
        ratified_by="operator",
        tenant_id=W,
    )
    assert [r["source_ref"] for r in _pit(db, W).revenue_mix_facts(sid)] == ["W-MIX"]
    assert _pit(db, V).revenue_mix_facts(sid) == []


# ---------------------------------------------------------------------------------------------------------
# (b) An empty workspace -> honest Incubating off DATA facts alone (never a crash, never another's rows).
# ---------------------------------------------------------------------------------------------------------
def test_empty_workspace_is_honest_incubating(db):
    """W holds a card on a company with NO data facts; V ratified a core catalyst on the SAME company. W's call
    re-derives to ``Incubating`` with no triggers — V's ratified judgment never reaches W's card — while V's
    own read sees it (the poison row is real)."""
    _workspaces(db)
    sid = _security(db)
    _ratified_catalyst(db, sid, V, "CAT-V-0001")
    v_thesis = _thesis(sid, V, "V HIMS")
    w_thesis = _thesis(sid, W, "W HIMS")
    thesis_repo.upsert(db, v_thesis)
    thesis_repo.upsert(db, w_thesis)

    assert [r["source_ref"] for r in _pit(db, V).catalyst_facts(sid)] == ["CAT-V-0001"]
    assert "CAT-V-0001" in _refs(
        call_for_thesis(db, v_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False)
    )
    card = call_for_thesis(db, w_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False)
    assert card.state is State.INCUBATING
    assert card.triggers_fired == []


# ---------------------------------------------------------------------------------------------------------
# (c) A workspace call arms off DATA facts + its OWN ratified rows; it cites only what it can see.
# ---------------------------------------------------------------------------------------------------------
def test_workspace_call_arms_off_data_and_cites_only_what_it_sees(db):
    """The DATA facts (the Wells cluster buy + the breakout) arm BOTH workspaces' cards on the same company —
    shared data, one arm — and each cites the data accession. V's ratified catalyst rides V's card and never
    W's: the card-level isolation proof, not just the row-level one."""
    _workspaces(db)
    sid = _security(db)
    _arm_facts(db, sid, "DATA-WELLS-0001")
    _ratified_catalyst(db, sid, V, "CAT-V-0001")
    w_thesis = _thesis(sid, W, "W HIMS")
    v_thesis = _thesis(sid, V, "V HIMS")
    thesis_repo.upsert(db, w_thesis)
    thesis_repo.upsert(db, v_thesis)

    w_card = call_for_thesis(db, w_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False)
    v_card = call_for_thesis(db, v_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=False)
    assert w_card.state is State.ARMED and v_card.state is State.ARMED
    assert w_card.armed_security_id == sid and v_card.armed_security_id == sid
    assert "DATA-WELLS-0001" in _refs(w_card) and "DATA-WELLS-0001" in _refs(v_card)
    assert "CAT-V-0001" in _refs(v_card)
    assert "CAT-V-0001" not in _refs(w_card)  # V's ratified row never leaks into W's call


# ---------------------------------------------------------------------------------------------------------
# (d) The call of record lands in the thesis's workspace.
# ---------------------------------------------------------------------------------------------------------
def test_call_of_record_writes_the_thesis_workspace(db):
    """A ``record=True`` call appends the call of record under the THESIS's workspace — never the data tenant
    whose facts it read (a call is workspace data, ``domain.scopes``)."""
    _workspaces(db)
    sid = _security(db)
    _arm_facts(db, sid, "DATA-WELLS-0001")
    w_thesis = _thesis(sid, W, "W HIMS")
    v_thesis = _thesis(sid, V, "V HIMS")
    thesis_repo.upsert(db, w_thesis)
    thesis_repo.upsert(db, v_thesis)

    call_for_thesis(db, w_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=True)
    call_for_thesis(db, v_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=True)

    with db.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, tenant_id FROM calls WHERE thesis_id = ANY(%s)",
            ([w_thesis.id, v_thesis.id],),
        )
        landed = {(r["thesis_id"], r["tenant_id"]) for r in cur.fetchall()}
        cur.execute("SELECT count(*) AS n FROM calls WHERE tenant_id = %s", (DATA,))
        under_data = cur.fetchone()["n"]
    assert landed == {(w_thesis.id, W), (v_thesis.id, V)}
    assert under_data == 0


# ---------------------------------------------------------------------------------------------------------
# Smoke — a fresh workspace on the identical code path: provision -> the feed writes DATA -> an Armed call.
# ---------------------------------------------------------------------------------------------------------
def test_workspace_cut_smoke(db):
    """Provision a fresh workspace, let the feed write the company's facts under the DATA tenant (the proven
    cluster-buy + breakout arm), upsert the workspace's thesis, and re-derive an Armed call recorded in the
    workspace — with the shared master resolving the ticker under the DATA tenant and the workspace owning no
    master row of its own."""
    tid = new_workspace(db, "workspace W", workspace_id=W)
    assert tid == W
    sid = _security(db, ticker="HIMS", cik="0001773751")
    _arm_facts(db, sid, "DATA-SMOKE-0001")
    w_thesis = _thesis(sid, W, "HIMS — workspace W")
    thesis_repo.upsert(db, w_thesis)

    card = call_for_thesis(db, w_thesis.id, _ARM_ASOF, known_at=_KNOWN, record=True)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.STARTER_ENTRY
    assert "DATA-SMOKE-0001" in _refs(card)
    assert master.tickers_for(db, {sid}, tenant_id=DATA) == {sid: "HIMS"}
    assert master.tickers_for(db, {sid}, tenant_id=W) == {}  # a workspace owns no master


# ---------------------------------------------------------------------------------------------------------
# The MIXED scoring facts — a workspace's ratified rows are its own; the DATA row is everyone's.
# ---------------------------------------------------------------------------------------------------------
def test_scoring_facts_read_isolation(db):
    """The three scoring-fact accessors (revenue_mix / shares_outstanding / cash_burn) read DATA + the caller's
    workspace: W's ratified row and V's ratified row are each visible ONLY to their own workspace, while a
    DATA-scope row (a feed row) is visible to both."""
    _workspaces(db)
    sid = _security(db)
    common = dict(event_date=date(2026, 1, 1), source="ratified", ratified_by="operator")
    for ws, tag in ((W, "W"), (V, "V")):
        ingest_revenue_mix(
            db, sid, segment_label="x", mix_pct=90, source_ref=f"{tag}-MIX", tenant_id=ws, **common
        )
        ingest_shares_outstanding(
            db, sid, shares=228_000_000, source_ref=f"{tag}-SH", tenant_id=ws, **common
        )
        ingest_cash_burn(
            db,
            sid,
            cash_usd=1_000_000_000,
            quarterly_burn_usd=0,
            source_ref=f"{tag}-CB",
            tenant_id=ws,
            **common,
        )
    # a DATA-scope row (no ratifier — the feed's shape)
    ingest_shares_outstanding(
        db,
        sid,
        shares=141_000_000,
        source="10-q-cover",
        source_ref="DATA-SH",
        event_date=date(2026, 1, 1),
        tenant_id=DATA,
    )
    w_pit, v_pit = _pit(db, W), _pit(db, V)

    def refs(pit, accessor):
        return sorted(r["source_ref"] for r in getattr(pit, accessor)(sid))

    assert refs(w_pit, "revenue_mix_facts") == ["W-MIX"]
    assert refs(v_pit, "revenue_mix_facts") == ["V-MIX"]
    assert refs(w_pit, "shares_outstanding_facts") == ["DATA-SH", "W-SH"]
    assert refs(v_pit, "shares_outstanding_facts") == ["DATA-SH", "V-SH"]
    assert refs(w_pit, "cash_burn_facts") == ["W-CB"]
    assert refs(v_pit, "cash_burn_facts") == ["V-CB"]


def test_corporate_event_read_isolation(db):
    """The 8-K item-code tape is DATA: the feed's event is visible to every workspace, and a stray event stored
    under a workspace enters no read."""
    _workspaces(db)
    sid = _security(db)

    def _event(tenant_id, accession):
        return {
            "tenant_id": tenant_id,
            "security_id": sid,
            "form": "8-K",
            "items": ["4.02"],
            "accession": accession,
            "filed": date(2026, 5, 1),
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
            "valid_from": date(2026, 5, 1),
        }

    append_fact(db, "fact_corporate_event", _event(DATA, "DATA-8K-1"))
    append_fact(db, "fact_corporate_event", _event(W, "W-STRAY-8K-1"))
    for ws in (W, V):
        assert [r["accession"] for r in _pit(db, ws).corporate_event_facts(sid)] == ["DATA-8K-1"]


def test_activist_stake_read_isolation(db):
    """The SC 13D/G ownership tape is DATA (the price-window overlay shares the same accessor): the feed's
    stake is visible to every workspace, and a stray stake stored under a workspace enters no read.
    """
    _workspaces(db)
    sid = _security(db)

    def _stake(tenant_id, accession):
        return {
            "tenant_id": tenant_id,
            "security_id": sid,
            "form": "SC 13D",
            "filer_cik": "0001234567",
            "filer_name": "Big Activist LP",
            "pct_owned": 7.5,
            "accession": accession,
            "filed": date(2026, 5, 1),
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
            "valid_from": date(2026, 5, 1),
        }

    append_fact(db, "fact_activist_stake", _stake(DATA, "DATA-13D-1"))
    append_fact(db, "fact_activist_stake", _stake(V, "V-STRAY-13D-1"))
    for ws in (W, V):
        assert [r["accession"] for r in _pit(db, ws).activist_stake_facts(sid)] == ["DATA-13D-1"]


def test_master_population_is_tenant_isolated(db):
    """The broadener (``populate_universe``) writes the tenant it is given — the DATA tenant on every real path
    (``pipeline.populate_master`` defaults to it). A populate under DATA creates no workspace row, and a stray
    row populated under a workspace is never read, mutated or duplicated by the DATA master."""
    _workspaces(db)
    rows = [("0001822966", "SMR", "NuScale Power", "NYSE")]
    master.populate_universe(db, rows, tenant_id=DATA)
    db.commit()
    assert set(master.ids_for_tickers(db, ["SMR"], tenant_id=DATA)) == {"SMR"}
    assert master.ids_for_tickers(db, ["SMR"], tenant_id=W) == {}  # a workspace owns no master

    master.populate_universe(db, rows, tenant_id=W)  # a stray (no real path writes it)
    db.commit()
    data_id = master.ids_for_tickers(db, ["SMR"], tenant_id=DATA)["SMR"]
    stray_id = master.ids_for_tickers(db, ["SMR"], tenant_id=W)["SMR"]
    assert data_id != stray_id
    master.populate_universe(db, rows, tenant_id=DATA)  # a re-populate leaves the stray untouched
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM security_master WHERE tenant_id = %s AND ticker = 'SMR'", (W,))
        assert [r["id"] for r in cur.fetchall()] == [stray_id]
        cur.execute(
            "SELECT id FROM security_master WHERE tenant_id = %s AND ticker = 'SMR'", (DATA,)
        )
        assert [r["id"] for r in cur.fetchall()] == [data_id]


def test_workbench_scored_read_is_tenant_isolated(db):
    """The Workbench scored READ (the scorer over the pit) reads DATA + the caller's workspace: W's ratified
    purity scores for W, V's for V, and a workspace with no ratification of its own scores "—" even though
    another workspace ratified the same company."""
    _workspaces(db)
    sid = _security(db)
    other = _security(db, ticker="OTHR", cik="0000000099")
    for ws, pct, ref in ((W, 100, "W"), (V, 50, "V")):
        ingest_revenue_mix(
            db,
            sid,
            segment_label="x",
            mix_pct=pct,
            source="10-k-segment",
            source_ref=ref,
            event_date=date(2026, 1, 1),
            ratified_by="operator",
            tenant_id=ws,
        )
    ingest_revenue_mix(
        db,
        other,
        segment_label="x",
        mix_pct=70,
        source="10-k-segment",
        source_ref="W-OTHER",
        event_date=date(2026, 1, 1),
        ratified_by="operator",
        tenant_id=W,
    )
    member = BasketMember(ticker="HIMS", role="r", security_id=sid)
    w_scored = score_member(_pit(db, W), member)
    v_scored = score_member(_pit(db, V), member)
    assert w_scored.purity.value == 100.0 and w_scored.purity.provenance[0].ref == "W"
    assert v_scored.purity.value == 50.0 and v_scored.purity.provenance[0].ref == "V"
    # cross: only W ratified OTHR -> V scores it "—"
    cross = score_member(_pit(db, V), BasketMember(ticker="OTHR", role="r", security_id=other))
    assert cross.purity.pips is None


def test_securities_search_is_tenant_isolated(db):
    """The Workbench add-a-name search reads the master it is given — the DATA master: a stray same-ticker row
    under a workspace is never returned by the DATA search, and a workspace search (a missed scope) finds only
    that stray, never the shared row."""
    _workspaces(db)
    data_sec = _security(db, ticker="OKLO")
    stray = _security(db, W, ticker="OKLO")
    assert [s.id for s in master.search(db, "OKLO", tenant_id=DATA)] == [data_sec]
    assert [s.id for s in master.search(db, "OKLO", tenant_id=W)] == [stray]


def test_chain_resolution_is_tenant_isolated(db):
    """The narrative→chain resolver resolves against ONLY the master it is given: a ticker in the DATA master
    PLACES under DATA and is ABSENT under a workspace tenant (which owns no master)."""
    _workspaces(db)
    _security(db, ticker="OKLO")  # OKLO exists ONLY in the DATA master
    seg = [
        ProposedSegment(
            label="reactors", placements=[ProposedPlacement(name="Oklo", ticker="OKLO")]
        )
    ]
    assert resolve_placements(db, seg, tenant_id=DATA).placements[0].status is (
        PlacementStatus.PLACED
    )
    assert resolve_placements(db, seg, tenant_id=W).placements[0].status is (PlacementStatus.ABSENT)


def test_scoreboard_issuer_name_read_is_tenant_isolated(db):
    """The Scoreboard overlay's direct master name read is tenant-scoped: under the DATA tenant it reads the
    shared row's name; under a workspace tenant (a missed scope) it returns None — which merely disables the
    name fallback (recall-safe, never "self") — rather than any row's name."""
    from scoreboard.overlays import security_issuer_name

    _workspaces(db)
    sid = _security(db, ticker="DEVCO", cik="0000000042")
    with db.cursor() as cur:
        cur.execute("UPDATE security_master SET name = %s WHERE id = %s", ("Devco Inc", sid))
    assert security_issuer_name(db, DATA, sid) == "Devco Inc"
    assert security_issuer_name(db, W, sid) is None


def test_feed_limitation_read_is_tenant_isolated(db):
    """The known-feed-limitation read (``price_feed_repo.limited_history_marks``, 0056) is DATA: asked with
    the data row AND a stray workspace row (the poison row) under DATA it returns only the data mark.
    """
    from repositories import price_feed_repo

    _workspaces(db)
    data_sec = _security(db, ticker="SCE-PG", cik="0000092103")
    stray = _security(db, W, ticker="SCE-PG", cik="0000092103")
    for sid, why in ((data_sec, "DATA"), (stray, "STRAY")):
        price_feed_repo.mark_limited_history(db, sid, reason=why, basis="operator:limited-history")
    both = [data_sec, stray]
    data = price_feed_repo.limited_history_marks(db, both, tenant_id=DATA)
    assert {k: v.reason for k, v in data.items()} == {data_sec: "DATA"}  # never the stray mark
    assert {
        k: v.reason for k, v in price_feed_repo.limited_history_marks(db, both, tenant_id=W).items()
    } == {stray: "STRAY"}


def test_basket_prefetch_read_is_tenant_isolated(db):
    """The per-basket PREFETCH (``as_of_many`` + the basket-scoped ``PointInTimeData``) reads DATA tables under
    the data tenant only: a stray price bar / Form 4 stored under the workspace (the poison row, on the SAME
    security) never enters the batch read, the PIT accessors, or the identity batch."""
    _workspaces(db)
    sid = _security(db)
    bar = {"d": date(2026, 5, 29), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    ingest_prices(db, sid, [bar], tenant_id=DATA)
    ingest_prices(db, sid, [{**bar, "close": 99}], tenant_id=W)  # the stray
    xml = (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8")
    ingest_form4(db, sid, xml, "DATA-F4", tenant_id=DATA)
    ingest_form4(db, sid, xml, "W-STRAY-F4", tenant_id=W)
    db.commit()

    for table in ("fact_price_eod", "fact_insider_txn"):
        rows = as_of_many(
            db, table, security_ids=[sid], asof=_ARM_ASOF, known_at=_KNOWN, tenant_id=DATA
        )
        assert rows[sid] and {r["tenant_id"] for r in rows[sid]} == {DATA}

    pit = _pit(db, W, basket=[sid], bounds=call_bounds(DEFAULT_CONFIG))
    assert [float(b["close"]) for b in pit.price_history(sid)] == [1.0]
    assert {r["accession"] for r in pit.insider_txns(sid)} == {"DATA-F4"}
    assert pit.security_cik(sid) == "0001773751"  # the identity batch reads the DATA master
    stray = _security(db, W, ticker="STRY", cik="0000000123")  # a master row under the workspace
    assert _pit(db, W, basket=[sid, stray]).security_cik(stray) is None  # unknown to the PIT


def test_backfill_accepted_basket_only_worklist_is_tenant_isolated(db):
    """``pipeline.backfill_accepted --basket-only`` narrows its worklist through ``basket_member``: the DATA
    tenant's NULL-``accepted`` Form 4 rows of the companies ANY workspace holds in a basket — and never a stray
    row stored under a workspace tenant on the SAME security (``security_master.id`` carries no tenant, so only
    the filter stops it)."""
    from pipeline.backfill_accepted import _target_scopes

    _workspaces(db)
    sid = _security(db)
    xml = (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8")
    ingest_form4(db, sid, xml, "DATA-ACC", tenant_id=DATA)
    ingest_form4(db, sid, xml, "W-STRAY-ACC", tenant_id=W)
    thesis_repo.upsert(db, _thesis(sid, V, "V basket"))  # held in V's basket
    db.commit()

    scopes = _target_scopes(db, basket_only=True)
    assert (DATA, sid) in scopes  # the data rows of a company a workspace holds
    assert (W, sid) not in scopes and (V, sid) not in scopes


def test_the_onboarding_queue_is_the_data_tenants(db):
    """X1 — the onboarding queue is DATA: one history load per company platform-wide. A promote in a workspace
    queues under the data tenant; a workspace's readout reads the shared queue (never a stray row under a
    workspace); and a company another workspace's live basket holds is loaded for every workspace.
    """
    from pipeline import onboarding

    _workspaces(db)
    queued = _security(db, ticker="QUED", cik="0000000101")
    stray = _security(db, ticker="STRY", cik="0000000102")
    held = _security(db, ticker="HELD", cik="0000000103")
    onboarding.enqueue(db, [stray], tenant_id=W, source="cli")  # a stray queue row under W
    thesis_repo.upsert(db, _thesis(held, V, "V basket"))  # V's live basket holds HELD
    w_thesis = _thesis(queued, W, "W card")
    thesis_repo.upsert(db, w_thesis)
    plan = onboarding.plan_promote(db, {queued, held}, tenant_id=W, thesis_id=w_thesis.id)
    db.commit()

    assert set(plan.cold) == {queued}  # HELD is loaded (V's basket) -> not cold for W either
    status = onboarding.pending_status(db)
    assert status[(DATA, queued)] == "queued"  # keyed on the data tenant, not W
    assert (W, queued) not in status
    for ws in (W, V):
        assert [r.security_id for r in onboarding.readout(db, tenant_id=ws).pending] == [queued]
    assert onboarding.cold_ids(db, [held], tenant_id=DATA, thesis_id=None) == set()


def test_split_list_read_isolation_live_and_replay(db, tmp_path):
    """The SPLIT LIST is DATA, read on four paths — ``split_repo`` (both clocks), ``PointInTimeData.split_events``
    (scoped and basket-prefetched, both clocks) and the replay mirror (the export AND the reader). The poison
    row: a split stored under a WORKSPACE on the SAME ``security_id`` (``security_split.security_id`` carries no
    tenant). Every workspace's view reads only the data tenant's split, on every path."""
    from decimal import Decimal

    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror
    from repositories import split_repo

    _workspaces(db)
    sid = _security(db)

    def _split(tenant_id, key_date, ratio_from):
        split_repo.append_version(
            db,
            tenant_id=tenant_id,
            security_id=sid,
            key_date=key_date,
            values={
                "effective_date": key_date,
                "ratio_from": Decimal(ratio_from),
                "ratio_to": Decimal(1),
                "status": "confirmed",
                "confirmed_via": "operator",
                "value_source": "polygon",
                "valid_from": key_date,
            },
            action=split_repo.ACTION_BACKFILL,
            author=split_repo.AUTHOR_BACKFILL,
        )

    data_day, poison_day = date(2026, 5, 1), date(2026, 4, 1)
    _split(DATA, data_day, 10)
    _split(W, poison_day, 20)  # the poison: a workspace tenant, the same security
    ingest_prices(
        db,
        sid,
        [{"d": date(2026, 5, 29), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
        tenant_id=DATA,
    )
    db.commit()

    def days(rows):
        return [r["key_date"] for r in rows]

    rows = split_repo.as_of_rows(db, tenant_id=DATA, security_ids=[sid], known_at=_KNOWN)
    assert days(rows[sid]) == [data_day] and {r["tenant_id"] for r in rows[sid]} == {DATA}
    assert days(split_repo.current_rows(db, tenant_id=DATA, security_ids=[sid])[sid]) == [data_day]
    for clock in ("record", "public"):
        for basket in (None, [sid]):
            for ws in (W, V):
                pit = _pit(db, ws, basket=basket, clock=clock)
                assert days(pit.split_events(sid)) == [data_day]
        for ws in (W, V):
            out = tmp_path / f"{clock}-{ws}"
            export_snapshot(db, out, data_tenant=DATA, workspace=ws, clock=clock)
            con = connect_mirror(out)
            try:
                own = ReplayPointInTimeData(
                    con, asof=_ARM_ASOF, known_at=_KNOWN, data_tenant=DATA, workspace=ws
                )
                assert days(own.split_events(sid)) == [data_day]
            finally:
                con.close()


def test_the_split_jump_scan_reads_the_data_tenant(db):
    """S5 — the split-night hold's escalation read (``pipeline.split_jumps``: every workspace's live baskets
    over the DATA master + a public-clock PIT). GAME's real released step, seeded as DATA with its split
    unlisted and held in WORKSPACE W's basket, is found by the nightly's DATA scan; a scan named with a
    workspace tenant (a missed scope) reads no master and finds nothing."""
    from pipeline.split_jumps import scan_unlisted_jumps
    from tests import split_s5_fixtures as s5

    _workspaces(db)
    sid = s5.seed(db, "GAME", listed=False)  # the master + bars, under the data tenant
    w_thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=W,
        name="W split-night card",
        narrative="n",
        ticker="GAME",
        basket=[BasketMember(ticker="GAME", role="member", security_id=sid)],
    )
    thesis_repo.upsert(db, w_thesis)
    db.commit()
    asof = date(2026, 8, 28)
    assert [j.ticker for j in scan_unlisted_jumps(db, asof=asof, tenant_id=DATA)] == ["GAME"]
    assert scan_unlisted_jumps(db, asof=asof, tenant_id=W) == ()


def test_the_held_insider_rows_queue_reads_and_writes_the_data_tenant(db):
    """Bad dates step 3 — the steward's queue (``repositories.insider_hold_repo``) is DATA. The poison row: a
    held row stored under a WORKSPACE on the SAME ``security_id`` (a direct-input hold, never a real filing). The
    queue lists, counts and decides only the data tenant's row; the workspace row is unknown to it (a 404 at the
    route), and a decision is written under the data tenant."""
    import pytest
    from psycopg.types.json import Json

    from repositories import insider_hold_repo

    _workspaces(db)
    sid = _security(db, ticker="HOLD", cik="0000000201")
    ids = {}
    with db.cursor() as cur:
        for tenant, acc in ((DATA, "0000000201-21-000001"), (W, "0000000201-21-000002")):
            cur.execute(
                "INSERT INTO insider_txn_hold (tenant_id, security_id, accession, txn_seq, form, reason, "
                '"row", filed, period_of_report_as_filed) '
                "VALUES (%s, %s, %s, 0, '4', 'after_filing', %s, %s, %s) RETURNING id",
                (
                    tenant,
                    sid,
                    acc,
                    Json({"txn_code": "P", "shares": 10, "txn_date_as_filed": "2021-11-16"}),
                    date(2021, 3, 18),
                    "2020-11-16",
                ),
            )
            ids[tenant] = cur.fetchone()["id"]
    db.commit()

    assert [it.id for it in insider_hold_repo.items(db)] == [ids[DATA]]
    assert insider_hold_repo.count_owed(db) == 1
    assert [it.id for it in insider_hold_repo.items(db, tenant_id=W)] == [ids[W]]  # W's, only
    with pytest.raises(LookupError):
        insider_hold_repo.approve(db, ids[W], None, None)
    db.rollback()
    row = insider_hold_repo.approve(db, ids[DATA], None, None)
    db.commit()
    assert row["tenant_id"] == DATA and row["ratified_date"] == date(2020, 11, 16)
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM insider_txn_hold_decision WHERE tenant_id = %s", (W,)
        )
        assert cur.fetchone()["n"] == 0
