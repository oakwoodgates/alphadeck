from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Thesis
from ingest.edgar.client import EdgarClient
from repositories import thesis_repo
from securities import figi, fund_tickers

# REAL SEC captures (2026-07-26) laid out as an EdgarClient cache dir — the endpoint runs its REAL
# resolve→locate→fetch→parse→cusip-map→overlap path against them, offline: LIT's series ATOM + two of
# its N-PORT docs (the newest same-day /A and an older quarter, for the asof leg), ARKK's ATOM + doc
# (the RARE ticker-bearing filer: 44/46), and SMH's ATOM + doc (the COMMON no-ticker shape — 26/26
# holdings ride CUSIP only, the case the CUSIP→ticker leg exists for). The OpenFIGI crosswalk is
# stubbed per test (the figi unit tests own its transport semantics).
_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_NPORT_CACHE = _FIXTURES / "edgar" / "nport_cache"


@pytest.fixture
def offline_sec(monkeypatch):
    """Point every outbound leg at fixtures/stubs: the MF fund file (fund_tickers' cache dir), the
    EDGAR client (the router's ``EdgarClient(allow_live=True)`` becomes a fixture-cache client —
    cache-first serves every key; ``allow_live=False`` proves nothing reaches the network), and the
    OpenFIGI CUSIP crosswalk (a recording fake). Yields a state dict: set ``state["map"]`` to the
    cusip→ticker answers a test wants; ``state["calls"]`` records exactly which CUSIPs the endpoint
    asked to resolve (the wiring assertion — only ticker-less, cusip-bearing holdings should)."""
    import app.routers.workbench as wb

    monkeypatch.setattr(fund_tickers, "_DEFAULT_CACHE", _FIXTURES / "sec")
    monkeypatch.setattr(
        wb, "EdgarClient", lambda **kw: EdgarClient(cache_dir=_NPORT_CACHE, allow_live=False)
    )
    state: dict = {"map": {}, "calls": []}

    def _fake_map_cusip(cusips, **kw):
        asked = list(cusips)
        state["calls"].append(asked)
        return {c: t for c, t in state["map"].items() if c in set(asked)}

    monkeypatch.setattr(figi, "map_cusip", _fake_map_cusip)
    return state


def _master_row(db, ticker: str, cik: str | None = None, kind: str = "equity") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, "2026-01-01"),
        )
    db.commit()
    return sid


def _sleeve(db, ticker: str) -> uuid.UUID:
    # the Slice-1 sleeve shape: cik=None (a fund-trust series has no operating CIK), kind 'etf'
    return _master_row(db, ticker, cik=None, kind="etf")


def _seed_thesis(db, members: list[BasketMember]) -> uuid.UUID:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="etf-holdings fixture",
        narrative="the sleeve's overlap test bed",
        ticker=members[0].ticker if members else None,
        basket=members,
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis.id


def _member(sid: uuid.UUID, ticker: str) -> BasketMember:
    return BasketMember(ticker=ticker, role="the name", archetype=Archetype.LEADER, security_id=sid)


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()["count"]


# --- the LIT end-to-end (the measured no-ticker filer) -----------------------------------------------


def test_lit_zero_crosswalk_coverage_is_all_unresolved_still_shown(client, db, offline_sec):
    """The #9 floor: Global X stamps NO ticker on equity holdings, and here the CUSIP crosswalk places
    nothing either (the stub's empty default = OpenFIGI declining everything) — every holding still
    SURFACES as unresolved, never dropped. Also pins the WIRING: the endpoint asks the crosswalk for
    exactly the ticker-less, CUSIP-bearing holdings (9 of LIT's 45 — the rest are foreign lines with
    cusip N/A, or the tickered repo line)."""
    sid = _sleeve(db, "LIT")
    r = client.get(f"/workbench/securities/{sid}/etf-holdings")
    assert r.status_code == 200
    body = r.json()
    # the locator picked the same-day /A (the correction), whose filing index is the provenance link
    assert body["source_ref"].endswith("0002048251-26-005686-index.htm")
    assert body["report_date"] == "2026-04-30"  # the vintage LABEL (quarter-end, ~60d lagged)
    assert body["holdings_count"] == 45
    assert body["held"] == [] and body["available"] == []
    assert len(body["unresolved"]) == 45
    # weight-sorted, heaviest first; identity rides name+CUSIP/ISIN where the filing carries them
    top = body["unresolved"][0]
    assert top["name"] == "RIO TINTO PLC"
    assert top["cusip"] == "767204100"
    assert top["security_id"] is None
    # the crosswalk wiring: one batch, exactly the 9 real-CUSIP ticker-less holdings — never the
    # tickered line, never a cusip-less foreign local
    assert len(offline_sec["calls"]) == 1
    assert set(offline_sec["calls"][0]) == {
        "88160R101",  # TESLA, INC.
        "767204100",  # RIO TINTO PLC (the ADR line)
        "29275Y102",  # ENERSYS
        "53681J103",  # Lithium Americas
        "73015G104",  # PMET Resources
        "853606101",  # Standard Lithium
        "826599102",  # Sigma Lithium
        "833635105",  # SQM
        "549498202",  # LUCID GROUP
    }


def test_lit_cusip_crosswalk_resolves_held_and_available(client, db, offline_sec):
    """The overlap upgrade on the SAME no-ticker filing: with the crosswalk placing TSLA + RIO, a
    basket name reads HELD and a master name AVAILABLE — and unresolved SHRINKS 45 → 43 (vs the
    ticker-only path's 45). A matched holding SURFACES its resolved ticker (so the drawer shows what
    it matched to — load-bearing for an AVAILABLE name you're missing); the FILING's own CUSIP rides
    alongside as provenance, and an unmatched holding keeps ``ticker=None``.
    """
    sleeve = _sleeve(db, "LIT")
    tsla = _master_row(db, "TSLA", cik="0001318605")
    _master_row(db, "RIO", cik="0000863064")
    tid = _seed_thesis(db, [_member(tsla, "TSLA")])
    offline_sec["map"] = {"88160R101": "TSLA", "767204100": "RIO"}
    r = client.get(f"/workbench/securities/{sleeve}/etf-holdings", params={"thesis_id": str(tid)})
    assert r.status_code == 200
    body = r.json()
    assert body["holdings_count"] == 45
    [held] = body["held"]
    assert held["name"] == "TESLA, INC."
    assert held["security_id"] == str(tsla)
    assert (
        held["ticker"] == "TSLA" and held["cusip"] == "88160R101"
    )  # matched -> the resolved ticker surfaces; the filing's CUSIP rides alongside as provenance
    [avail] = body["available"]
    assert avail["name"] == "RIO TINTO PLC"
    assert len(body["unresolved"]) == 43  # shrank from 45 — and still nothing dropped:
    assert (
        len(body["held"]) + len(body["available"]) + len(body["unresolved"])
        == body["holdings_count"]
    )


def test_recall_buckets_always_partition_the_holdings(client, db, offline_sec):
    """#9 wire-level: held+available+unresolved == holdings_count, whatever the match coverage."""
    sid = _sleeve(db, "LIT")
    body = client.get(f"/workbench/securities/{sid}/etf-holdings").json()
    assert (
        len(body["held"]) + len(body["available"]) + len(body["unresolved"])
        == body["holdings_count"]
        == 45
    )


# --- the ARKK end-to-end (the ticker-bearing filer: the buckets do real work) ------------------------


def test_arkk_buckets_held_vs_available_vs_unresolved(client, db, offline_sec):
    sleeve = _sleeve(db, "ARKK")
    ktos = _master_row(db, "KTOS", cik="0001069258")  # in the basket -> held
    _master_row(db, "PLTR", cik="0001321655")  # in the master, not the basket -> available
    tid = _seed_thesis(db, [_member(ktos, "KTOS")])
    r = client.get(f"/workbench/securities/{sleeve}/etf-holdings", params={"thesis_id": str(tid)})
    assert r.status_code == 200
    body = r.json()
    assert body["holdings_count"] == 46
    held = {h["ticker"]: h for h in body["held"]}
    avail = {h["ticker"]: h for h in body["available"]}
    assert list(held) == ["KTOS"] and held["KTOS"]["security_id"] == str(ktos)
    assert list(avail) == ["PLTR"] and avail["PLTR"]["security_id"] is not None
    assert len(body["unresolved"]) == 44  # everything else SHOWN, not dropped (#9)
    assert body["report_date"] == "2026-04-30"


def test_without_thesis_id_matches_land_available(client, db, offline_sec):
    sleeve = _sleeve(db, "ARKK")
    _master_row(db, "KTOS", cik="0001069258")
    body = client.get(f"/workbench/securities/{sleeve}/etf-holdings").json()
    assert body["held"] == []
    assert [h["ticker"] for h in body["available"]] == ["KTOS"]


# --- the SMH end-to-end (the COMMON shape: every holding CUSIP-only — the crosswalk carries it all) ---


def test_smh_cusip_only_filing_resolves_the_overlap(client, db, offline_sec):
    """The operator's dev scenario, pinned offline: VanEck's SMH stamps ZERO tickers (26/26 holdings
    ride CUSIP only — NVIDIA is 67066G104 at ~19.6%), so the whole overlap rests on the CUSIP→ticker
    leg. With NVDA + TSM in the basket and AVGO in the master: held carries both (weight-sorted,
    NVIDIA first), Broadcom reads available, and the unmapped rest stays SHOWN (#9)."""
    sleeve = _sleeve(db, "SMH")
    nvda = _master_row(db, "NVDA", cik="0001045810")
    tsm = _master_row(db, "TSM", cik="0001046179")
    _master_row(db, "AVGO", cik="0001730168")
    tid = _seed_thesis(db, [_member(nvda, "NVDA"), _member(tsm, "TSM")])
    offline_sec["map"] = {
        "67066G104": "NVDA",  # NVIDIA Corp, 19.64%
        "874039100": "TSM",  # Taiwan Semiconductor (ADR), 11.84%
        "11135F101": "AVGO",  # Broadcom Inc, 7.79%
    }
    r = client.get(f"/workbench/securities/{sleeve}/etf-holdings", params={"thesis_id": str(tid)})
    assert r.status_code == 200
    body = r.json()
    assert body["source_ref"].endswith("0001410368-26-054882-index.htm")
    assert body["report_date"] == "2026-03-31"
    assert body["holdings_count"] == 26
    assert [(h["name"], h["security_id"]) for h in body["held"]] == [
        ("NVIDIA Corp", str(nvda)),  # 19.64% — heaviest first
        ("Taiwan Semiconductor Manufacturing Co Ltd", str(tsm)),  # 11.84%
    ]
    assert [h["name"] for h in body["available"]] == ["Broadcom Inc"]
    assert len(body["unresolved"]) == 23  # the unmapped rest — shown, never dropped
    # the crosswalk was asked for ALL 26 (every holding is ticker-less with a real CUSIP)
    assert len(offline_sec["calls"]) == 1 and len(offline_sec["calls"][0]) == 26


# --- the fund internals (AUM + composition) ride the same pull -------------------------------------


def test_fund_internals_surface_on_the_wire(client, db, offline_sec):
    """AUM + the gross/liabilities composition come off the SAME parsed N-PORT (no extra fetch) —
    surfaced for the sleeve dossier (#6). The LIT /A the locator picks: net $2.13B off gross $2.14B.
    """
    sid = _sleeve(db, "LIT")
    body = client.get(f"/workbench/securities/{sid}/etf-holdings").json()
    assert body["net_assets"] == pytest.approx(2134236727.85)
    assert body["total_assets"] == pytest.approx(2141779737.60)
    assert body["total_liabs"] == pytest.approx(7543009.75)
    # net = gross − liabilities, straight through from the filing
    assert body["net_assets"] == pytest.approx(body["total_assets"] - body["total_liabs"])


# --- no-lookahead (#1) -------------------------------------------------------------------------------


def test_asof_selects_the_filing_knowable_then_and_labels_its_vintage(client, db, offline_sec):
    """asof 2026-01-15: the 2026-06-29 filings didn't exist yet — the latest KNOWABLE N-PORT is the
    2025-12-29 one, and the response is labeled with ITS report period (2025-10-31 <= asof)."""
    sid = _sleeve(db, "LIT")
    r = client.get(f"/workbench/securities/{sid}/etf-holdings", params={"asof": "2026-01-15"})
    assert r.status_code == 200
    body = r.json()
    assert body["source_ref"].endswith("0002048251-25-003776-index.htm")
    assert body["report_date"] == "2025-10-31"
    assert body["holdings_count"] == 40


def test_asof_before_any_filing_is_a_404_not_a_leak(client, db, offline_sec):
    sid = _sleeve(db, "LIT")
    r = client.get(f"/workbench/securities/{sid}/etf-holdings", params={"asof": "2020-01-01"})
    assert r.status_code == 404
    assert "as of 2020-01-01" in r.json()["detail"]


# --- response-only (#2): the endpoint writes NOTHING -------------------------------------------------


def test_etf_holdings_get_writes_nothing(client, db, offline_sec):
    """The ``test_draft_endpoint_writes_nothing`` family: a repeated holdings pull appends NO fact, NO
    basket member, and NO master row (the on-the-fly resolution is a locked decision — the trust CIK
    must never be back-filled into the master, where extract/companyfacts would misread it)."""
    sleeve = _sleeve(db, "ARKK")
    ktos = _master_row(db, "KTOS", cik="0001069258")
    tid = _seed_thesis(db, [_member(ktos, "KTOS")])
    before = (
        _count(db, "security_master"),
        _count(db, "basket_member"),
        _count(db, "fact_price_eod"),
        _count(db, "fact_shares_outstanding"),
    )
    for _ in range(2):  # a re-click is a pure re-read
        r = client.get(
            f"/workbench/securities/{sleeve}/etf-holdings", params={"thesis_id": str(tid)}
        )
        assert r.status_code == 200
    after = (
        _count(db, "security_master"),
        _count(db, "basket_member"),
        _count(db, "fact_price_eod"),
        _count(db, "fact_shares_outstanding"),
    )
    assert after == before
    # and the sleeve row itself is untouched — cik stays NULL (never the trust's)
    with db.cursor() as cur:
        cur.execute("SELECT cik FROM security_master WHERE id = %s", (sleeve,))
        assert cur.fetchone()["cik"] is None


# --- the failure surfaces (visible, never silent) ----------------------------------------------------


def test_unknown_security_404s(client, db, offline_sec):
    assert client.get(f"/workbench/securities/{uuid.uuid4()}/etf-holdings").status_code == 404


def test_not_a_fund_ticker_404s_with_the_mf_file_reason(client, db, offline_sec):
    sid = _master_row(db, "DEVCO", cik="0001234567")  # an operating company, not in the MF file
    r = client.get(f"/workbench/securities/{sid}/etf-holdings")
    assert r.status_code == 404
    assert "not in the SEC fund file" in r.json()["detail"]


def test_unknown_thesis_404s(client, db, offline_sec):
    sid = _sleeve(db, "LIT")
    r = client.get(
        f"/workbench/securities/{sid}/etf-holdings", params={"thesis_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "thesis not found"


def test_sec_trouble_is_a_visible_502_never_a_500(client, db, monkeypatch):
    import app.routers.workbench as wb

    monkeypatch.setattr(fund_tickers, "_DEFAULT_CACHE", _FIXTURES / "sec")

    class _Boom:
        def get_text(self, url: str, cache_key: str) -> str:
            raise RuntimeError("SEC unreachable")

    monkeypatch.setattr(wb, "EdgarClient", lambda **kw: _Boom())
    sid = _sleeve(db, "LIT")
    r = client.get(f"/workbench/securities/{sid}/etf-holdings")
    assert r.status_code == 502
    assert "SEC unreachable" in r.json()["detail"]


def test_openfigi_trouble_is_a_visible_502_never_a_silently_empty_overlap(
    client, db, offline_sec, monkeypatch
):
    """A whole-crosswalk failure must FAIL VISIBLY: degrading to ticker-only would render a no-ticker
    fund's overlap as 26 unresolved — indistinguishable from 'nothing matches', the silent-wrongness
    class. 502 with the reason instead."""

    def _boom(cusips, **kw):
        raise RuntimeError("OpenFIGI unreachable")

    monkeypatch.setattr(figi, "map_cusip", _boom)
    sid = _sleeve(db, "SMH")
    r = client.get(f"/workbench/securities/{sid}/etf-holdings")
    assert r.status_code == 502
    assert "CUSIP resolution failed" in r.json()["detail"]
    assert "OpenFIGI unreachable" in r.json()["detail"]
