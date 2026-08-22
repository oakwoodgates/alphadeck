from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.deps import get_current_tenant
from app.main import app
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from scoreboard.prices import PgRealizedPrices
from tests.scoreboard.helpers import bar, persist_thesis


def ohlcv_bar(db, security_id, d, o, h, low, c, v):
    """Seed a FULL OHLCV bar (the shared ``bar`` helper writes close-only)."""
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "d": d,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": v,
            "valid_from": d,
        },
    )
    db.commit()


def insider_buy(
    db,
    security_id,
    *,
    accession,
    valid_from,
    recorded_at,
    accepted=None,
    insider_name="A Buyer",
    role="CEO",
    txn_code="P",
    shares=1000.0,
    price=50.0,
    usd=50_000.0,
    aff_10b5_1=None,
    txn_seq=0,
    rpt_owner_cik=None,
    issuer_cik=None,
    issuer_name=None,
    security_title=None,
    issuer_foreign_symbol=None,
):
    """Seed one ``fact_insider_txn`` row with an EXPLICIT recorded_at (the disclosure axis is the
    whole point of the no-lookahead-on-insider test — the shared ``bar`` helper is close-only prices).
    The identity columns (migration 0024) feed the S2c self-filing character; the title/foreign-symbol
    columns (0041) feed the Slice B sell screen's foreign-ordinary bucket.
    """
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "insider_name": insider_name,
            "insider_role": role,
            "txn_code": txn_code,
            "shares": shares,
            "price": price,
            "usd": usd,
            "accession": accession,
            "valid_from": valid_from,
            "recorded_at": recorded_at,
            "accepted": accepted,
            "aff_10b5_1": aff_10b5_1,
            "txn_seq": txn_seq,
            "rpt_owner_cik": rpt_owner_cik,
            "issuer_cik": issuer_cik,
            "issuer_name": issuer_name,
            "security_title": security_title,
            "issuer_foreign_symbol": issuer_foreign_symbol,
        },
    )
    db.commit()


def corp_event(db, security_id, *, accession, filed, recorded_at, items, form="8-K"):
    """Seed one ``fact_corporate_event`` row (migration 0038) with an EXPLICIT recorded_at — the
    knowability gate is the load-bearing Slice B assertion. ``items=None`` = not-yet-resolved."""
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "form": form,
            "items": items,
            "accession": accession,
            "filed": filed,
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
            "valid_from": filed,  # = filed — the 0038 knowability rule
            "recorded_at": recorded_at,
        },
    )
    db.commit()


def activist(
    db,
    security_id,
    *,
    accession,
    filed,
    recorded_at,
    form,
    filer_cik=None,
    filer_name=None,
    pct_owned=None,
):
    """Seed one ``fact_activist_stake`` row (migration 0039) with an EXPLICIT recorded_at. Null
    filer identity / pct is the honest unresolved shape (#9 — the row must still ride)."""
    append_fact(
        db,
        "fact_activist_stake",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "form": form,
            "filer_cik": filer_cik,
            "filer_name": filer_name,
            "pct_owned": pct_owned,
            "accession": accession,
            "filed": filed,
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
            "valid_from": filed,  # = filed — the 0039 knowability rule (never the in-doc event date)
            "recorded_at": recorded_at,
        },
    )
    db.commit()


# GET /scoreboard/price-window — the drawer sparkline's on-demand read (Slice 3). The load-bearing
# test is the no-lookahead point-in-time one: the series is capped at asof SERVER-SIDE (cap=asof on
# the valid axis), so a future `end` can never widen it past the as-of. Plus tenant isolation → 404,
# the single-arm-bar 1-point series, and the null-close skip.

_START = "2026-06-01"
_END = "2026-09-01"  # a FUTURE exit_by relative to every asof below — the cap must still bite


def _get(client, thesis_id, security_id, asof, *, start=_START, end=_END):
    return client.get(
        "/scoreboard/price-window",
        params={
            "thesis_id": str(thesis_id),
            "security_id": str(security_id),
            "start": start,
            "end": end,
            "asof": asof,
        },
    )


def test_price_window_no_lookahead_caps_at_asof(client, db, security_id):
    """Invariant #1, the required point-in-time test: seed a window whose exit_by is in the future;
    an early asof returns FEWER bars, NO bar with d > asof ever appears, and the series is exactly
    what the scorer's asof-capped reader returns."""
    thesis = persist_thesis(db, security_id)
    for d, c in [
        (date(2026, 6, 1), 100.0),
        (date(2026, 6, 8), 104.0),
        (date(2026, 6, 15), 102.0),
        (date(2026, 6, 22), 108.0),
        (date(2026, 7, 1), 111.0),
        (date(2026, 7, 15), 115.0),
        (date(2026, 8, 1), 120.0),  # beyond BOTH asofs — must never surface at an earlier asof
    ]:
        bar(db, security_id, d, c)

    early = _get(client, thesis.id, security_id, "2026-06-15")
    late = _get(client, thesis.id, security_id, "2026-07-15")
    assert early.status_code == 200 and late.status_code == 200
    eb, lb = early.json()["bars"], late.json()["bars"]

    # (a) the earlier asof returns strictly FEWER bars — the valid-time cap bites
    assert len(eb) == 3 and len(lb) == 6
    assert len(eb) < len(lb)
    # (b) NO bar past the as-of, whatever `end` the client passed (end is 2026-09-01, far future).
    #     ISO date strings compare chronologically.
    assert all(b["d"] <= "2026-06-15" for b in eb)
    assert all(b["d"] <= "2026-07-15" for b in lb)
    assert eb[-1]["d"] == "2026-06-15" and lb[-1]["d"] == "2026-07-15"
    assert "2026-08-01" not in [b["d"] for b in lb]  # the beyond-cap bar never leaks
    # (c) the series MATCHES the closes the scorer would use — a reader built exactly as the endpoint
    #     builds it (cap=asof, the thesis's tenant, known_at defaulted to now — the scorer's config).
    reader = PgRealizedPrices(db, tenant_id=thesis.tenant_id, cap=date(2026, 7, 15))
    expected = [
        (d.isoformat(), c)
        for d, c in reader.closes_between(security_id, date(2026, 6, 1), date(2026, 9, 1))
    ]
    assert [(b["d"], b["close"]) for b in lb] == expected

    # the echoed contract (provenance + the request shape)
    body = late.json()
    assert body["thesis_id"] == str(thesis.id)
    assert body["security_id"] == str(security_id)
    assert body["start"] == _START and body["end"] == _END and body["asof"] == "2026-07-15"
    assert body["source"] == "fact_price_eod"


def test_price_window_other_tenant_thesis_404(client, db, security_id):
    """Tenant isolation: a thesis the deployment tenant can't see is a 404 (get_thesis_or_404 loads by
    id only — the current-tenant guard is what enforces isolation on this direct-fetch endpoint)."""
    thesis = persist_thesis(db, security_id)  # tenant = the default
    bar(db, security_id, date(2026, 6, 1), 100.0)
    # flip the deployment's current tenant to a DIFFERENT one → the thesis is not visible
    app.dependency_overrides[get_current_tenant] = lambda: uuid.uuid4()
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 404


def test_price_window_unknown_thesis_404(client, db, security_id):
    """An unknown thesis id → 404 (mirrors get_thesis_or_404)."""
    r = _get(client, uuid.uuid4(), security_id, "2026-07-15")
    assert r.status_code == 404


def test_price_window_single_arm_bar_one_point(client, db, security_id):
    """Only the arm-day bar has landed → a 1-bar series (the honest thin-data case the FE draws
    as 'no price path yet', never a fake line). A close-only bar surfaces null OHL/volume, never
    invented values; sma50/sma200 are null (one bar is far short of either window — the honest gap).
    """
    thesis = persist_thesis(db, security_id)
    bar(db, security_id, date(2026, 6, 1), 100.0)  # the arm-day bar only (close-only)
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 200
    assert r.json()["bars"] == [
        {
            "d": "2026-06-01",
            "open": None,
            "high": None,
            "low": None,
            "close": 100.0,
            "volume": None,
            "sma50": None,
            "sma200": None,
        }
    ]
    assert r.json()["insider_buys"] == []  # nothing ingested — an honest empty overlay


def test_price_window_full_ohlcv_bar_rides_the_wire(client, db, security_id):
    """A full OHLCV bar carries every field straight from fact_price_eod (the line still draws close;
    open/high/low/volume ride the wire for a later candlestick — no second contract change)."""
    thesis = persist_thesis(db, security_id)
    ohlcv_bar(db, security_id, date(2026, 6, 1), 99.5, 103.0, 98.0, 101.25, 1_250_000.0)
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 200
    assert r.json()["bars"] == [
        {
            "d": "2026-06-01",
            "open": 99.5,
            "high": 103.0,
            "low": 98.0,
            "close": 101.25,
            "volume": 1250000.0,
            "sma50": None,
            "sma200": None,
        }
    ]


def test_price_window_null_close_skipped(client, db, security_id):
    """A day with no close is skipped — never a null-close bar in the series (parity with the scorer)."""
    thesis = persist_thesis(db, security_id)
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 2), None)  # no close — dropped
    bar(db, security_id, date(2026, 6, 3), 103.0)
    r = _get(client, thesis.id, security_id, "2026-07-15")
    assert r.status_code == 200
    bars = r.json()["bars"]
    assert [b["d"] for b in bars] == ["2026-06-01", "2026-06-03"]
    assert all(b["close"] is not None for b in bars)


# --- Slice A: SMA context + the insider-buy overlay (both under the SAME asof discipline) ---


def _set_created_at(db, thesis_id, when: date) -> None:
    """Pin the thesis's created_at so the relevance floor (max(created_at−365d, first_bar)) is deterministic
    (the thesis table is mutable operational state — no append-only trigger)."""
    with db.cursor() as cur:
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", (when, thesis_id))
    db.commit()


def test_price_window_sma_warm_up_makes_the_left_edge_honest(client, db, security_id):
    """R1: the window starts at the RELEVANCE FLOOR (created_at−365d here), and the bar AT that floor
    already carries a non-null sma50 — the WARM-UP read BEHIND the floor supplied its prior closes (without
    it the left edge would be a false None). The value matches a hand-computed rolling mean; sma200 stays
    None (< 200 prior bars — the honest gap, never padded)."""
    thesis = persist_thesis(db, security_id)
    _set_created_at(db, thesis.id, date(2026, 6, 1))  # floor = created−365 = 2025-06-01
    base = date(2025, 4, 1)  # 61 bars of warm-up BEFORE the floor (2025-04-01 .. 2025-05-31)
    for i in range(
        90
    ):  # close_i = 100 + i on consecutive days from 2025-04-01 (through 2025-06-29)
        bar(db, security_id, base + timedelta(days=i), 100.0 + i)
    r = _get(client, thesis.id, security_id, "2026-07-15", start="2025-01-01", end="2026-09-01")
    assert r.status_code == 200
    body = r.json()
    assert (
        body["start"] == "2025-06-01"
    )  # the EFFECTIVE floor, echoed (not the requested 2025-01-01)
    bars = body["bars"]
    assert bars[0]["d"] == "2025-06-01"  # only [floor, end] returned (warm-up bars trimmed off)
    # 2025-06-01 is index 61 (closes 112..161) → sma50 = mean(112..161) = 136.5 (honest, from the warm-up)
    assert bars[0]["sma50"] == 136.5
    assert bars[0]["sma200"] is None  # < 200 prior bars
    # and the SMA tracks forward: the next bar (close 162) → mean(113..162) = 137.5
    assert bars[1]["sma50"] == 137.5


def test_price_window_universe_floor_excludes_a_pre_thesis_buy(client, db, security_id):
    """R1 relevance floor: a buy transacted BEFORE max(created_at−365d, first_bar) is off-story and excluded
    (a thesis born 2026-06 does not plot a 2020 buy) — NOT a recall cut, the event is genuinely pre-thesis.
    A buy inside the window survives. No price bars → the floor is created_at−365d."""
    thesis = persist_thesis(db, security_id)
    _set_created_at(db, thesis.id, date(2026, 6, 1))  # floor = 2025-06-01 (no bars → created−365)
    disc = datetime(2026, 6, 20, tzinfo=timezone.utc)
    insider_buy(
        db,
        security_id,
        accession="0000000008-26-000008",
        valid_from=date(2020, 1, 15),
        recorded_at=disc,
        insider_name="Ancient Buyer",
    )
    insider_buy(
        db,
        security_id,
        accession="0000000009-26-000009",
        valid_from=date(2025, 8, 1),
        recorded_at=disc,
        insider_name="Recent Buyer",
    )

    body = _get(client, thesis.id, security_id, "2026-07-15").json()
    assert body["start"] == "2025-06-01"  # the effective floor
    buys = body["insider_buys"]
    assert [b["insider_name"] for b in buys] == [
        "Recent Buyer"
    ]  # the 2020 buy is off-story, excluded
    assert all(b["d"] >= "2025-06-01" for b in buys)


def test_price_window_insider_gate_keys_on_recorded_at_not_disclosure(client, db, security_id):
    """The event twin of the price no-lookahead test — BOTH axes, keyed on ``recorded_at``:
    - transaction axis: a buy transacted 06-05, DISCLOSED (accepted) 06-20 but INGESTED 07-01 is ABSENT
      at as-of 06-25 (already disclosed, but the system had not recorded it yet) and PRESENT at 07-05 — the
      no-lookahead gate keys on ``recorded_at`` (our ingest instant) like every fact table, NOT on
      ``accepted``: an early disclosure never opens the gate. When the buy IS visible its ``disclosed``
      field still reads the acceptance date (06-20) — the honest two-clock DISPLAY, off the gate.
    - valid axis: a buy transacted 08-15 (future vs both as-ofs) NEVER appears — no `valid_from > asof`.
    """
    thesis = persist_thesis(db, security_id)
    disc = datetime(
        2026, 6, 20, 12, 0, tzinfo=timezone.utc
    )  # the Form 4 is ACCEPTED (public) 06-20 — display-only
    ingest = datetime(
        2026, 7, 1, 12, 0, tzinfo=timezone.utc
    )  # we INGESTED it 11 days later — the gate
    insider_buy(
        db,
        security_id,
        accession="0000000001-26-000001",
        valid_from=date(2026, 6, 5),
        accepted=disc,  # the disclosure clock: the "disclosed" DISPLAY date, NEVER the gate
        recorded_at=ingest,  # the ingest clock — the transaction-time no-lookahead gate keys on THIS
    )
    # a FUTURE-transaction buy (recorded early) — the valid axis must hide it at both as-ofs
    insider_buy(
        db,
        security_id,
        accession="0000000002-26-000002",
        valid_from=date(2026, 8, 15),
        recorded_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        insider_name="Future Buyer",
    )

    early = _get(client, thesis.id, security_id, "2026-06-25").json()["insider_buys"]
    late = _get(client, thesis.id, security_id, "2026-07-05").json()["insider_buys"]

    assert (
        early == []
    )  # disclosed 06-20 but recorded_at 07-01 is after known_at(06-25) -> not held; 08-15 is future
    assert (
        len(late) == 1
    )  # by 07-05 the 06-05 buy IS recorded; the 08-15 buy still hasn't transacted
    (buy,) = late
    assert (
        buy["d"] == "2026-06-05" and buy["disclosed"] == "2026-06-20"
    )  # disclosed = accepted (display)
    assert (
        buy["ingested"] == "2026-07-01"
    )  # the second clock — shown because it differs from disclosed
    assert buy["insider_name"] == "A Buyer" and buy["usd"] == 50000.0
    # the load-bearing assertion: no returned buy is ever transacted after the as-of
    for asof, buys in (("2026-06-25", early), ("2026-07-05", late)):
        assert all(b["d"] <= asof for b in buys)


def test_price_window_insider_superseded_row_no_double_count(client, db, security_id):
    """A corrected insider txn (same natural key, later recorded_at) does NOT double-count — the latest
    version wins, exactly the bitemporal `as_of` dedup the scorer relies on."""
    thesis = persist_thesis(db, security_id)
    nk = dict(accession="0000000003-26-000003", valid_from=date(2026, 6, 5), insider_name="A Buyer")
    insider_buy(
        db,
        security_id,
        recorded_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        usd=50_000.0,
        shares=1000.0,
        **nk,
    )
    # the correction: same (accession, insider_name, valid_from, txn_seq), later recorded_at, new figures
    insider_buy(
        db,
        security_id,
        recorded_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        usd=60_000.0,
        shares=1200.0,
        **nk,
    )

    buys = _get(client, thesis.id, security_id, "2026-07-15").json()["insider_buys"]
    assert len(buys) == 1  # ONE logical fact, not two
    assert buys[0]["usd"] == 60_000.0 and buys[0]["shares"] == 1200.0  # the corrected version


def test_price_window_insider_open_market_screen_reconciles_with_the_panel(client, db, security_id):
    """S2c option (a): a below-the-day's-low code-P subscription is now PRESENT-and-labeled
    (``character="primary_market"``) instead of hidden — greyed on the FE, never dropped (WB #2 / #9).
    The COUNTED set (the non-set-aside subset) still matches the NamePanel's open-market definition, so
    a counted dot on the chart is a dot in the panel's net-flow. A code-S sell never appears, and a
    set-aside row transacted after the as-of never leaks (the axis caps bind every character)."""
    thesis = persist_thesis(db, security_id)
    ohlcv_bar(
        db, security_id, date(2026, 6, 5), 42.0, 46.0, 40.0, 45.0, 1_000.0
    )  # the day's low = 40
    ohlcv_bar(db, security_id, date(2026, 8, 1), 42.0, 46.0, 40.0, 45.0, 1_000.0)
    disc = datetime(2026, 6, 10, tzinfo=timezone.utc)
    # open-market: price 45 >= low*0.9 (36) → counted
    insider_buy(
        db,
        security_id,
        accession="0000000004-26-000004",
        valid_from=date(2026, 6, 5),
        recorded_at=disc,
        price=45.0,
        usd=45_000.0,
    )
    # offer-price subscription: price 30 < low*0.9 (36) → SET ASIDE, but on the wire (labeled)
    insider_buy(
        db,
        security_id,
        accession="0000000005-26-000005",
        valid_from=date(2026, 6, 5),
        recorded_at=disc,
        price=30.0,
        usd=30_000.0,
    )
    # a sell is not a buy — never an overlay chip
    insider_buy(
        db,
        security_id,
        accession="0000000006-26-000006",
        valid_from=date(2026, 6, 6),
        recorded_at=disc,
        txn_code="S",
        price=44.0,
        usd=44_000.0,
    )
    # a FUTURE-transacted set-aside (below the 8/1 low) — the valid-axis cap hides it at asof 7/15
    insider_buy(
        db,
        security_id,
        accession="0000000010-26-000010",
        valid_from=date(2026, 8, 1),
        recorded_at=disc,
        price=30.0,
        usd=30_000.0,
    )

    buys = _get(client, thesis.id, security_id, "2026-07-15").json()["insider_buys"]
    assert len(buys) == 2  # the counted buy AND the labeled set-aside — nothing vanishes (WB #2)
    by_usd = {b["usd"]: b for b in buys}
    assert by_usd[45_000.0]["character"] == "open_market"
    assert by_usd[30_000.0]["character"] == "primary_market"
    # the COUNTED set (non-set-aside) is exactly what the panel's open-market figure counts
    counted = [b for b in buys if b["character"] not in ("primary_market", "implausible")]
    assert [b["usd"] for b in counted] == [45_000.0]
    assert all(b["d"] <= "2026-07-15" for b in buys)  # the future set-aside never leaked


def test_price_window_insider_self_filing_labeled_by_cik_and_by_name(client, db, security_id):
    """S2c: a self-filing is labeled ``self_filing`` via BOTH recognizers — CIK equality (canonical;
    the KYOCERA/Roivant shape) and the name fallback against the security-master name (pre-capture
    rows). It is labeled, NOT set aside (the panel's net-flow still counts it — the re-base is
    deferred, decision 3), and a real buy beside it is never over-excluded (#9)."""
    thesis = persist_thesis(db, security_id)
    with db.cursor() as cur:  # the fixture master row has no name — set one for the name fallback
        cur.execute(
            "UPDATE security_master SET name = %s WHERE id = %s", ("Devco Inc", security_id)
        )
    db.commit()
    disc = datetime(2026, 6, 20, tzinfo=timezone.utc)
    # (a) CIK equality — zero-padding normalized; the fixture master CIK is irrelevant (row-level match)
    insider_buy(
        db,
        security_id,
        accession="0000000011-26-000011",
        valid_from=date(2026, 6, 3),
        recorded_at=disc,
        insider_name="Devco Holdings KK",
        rpt_owner_cik="1234567",
        issuer_cik="0001234567",
        usd=690_000.0,
    )
    # (b) the name fallback: no CIKs on the row; filer name == the master name (casefold + trailing '.')
    insider_buy(
        db,
        security_id,
        accession="0000000012-26-000012",
        valid_from=date(2026, 6, 4),
        recorded_at=disc,
        insider_name="DEVCO INC.",
        usd=350_000.0,
    )
    # a genuine personal buy beside them stays open_market (the screen never over-excludes, #9)
    insider_buy(
        db,
        security_id,
        accession="0000000013-26-000013",
        valid_from=date(2026, 6, 5),
        recorded_at=disc,
        insider_name="Jane Doe",
        usd=50_000.0,
    )

    buys = _get(client, thesis.id, security_id, "2026-07-15").json()["insider_buys"]
    assert [b["character"] for b in buys] == ["self_filing", "self_filing", "open_market"]
    assert len(buys) == 3  # the self-filings are labeled, still visible, never dropped


def test_price_window_insider_implausible_row_surfaces_greyed_not_hidden(client, db, security_id):
    """S2c option (a): the physically-impossible row (the CNBX $2T shape) used to vanish from the
    overlay; now it rides labeled ``implausible`` (set aside on the FE) — more visible, never dropped.
    """
    thesis = persist_thesis(db, security_id)
    disc = datetime(2026, 6, 20, tzinfo=timezone.utc)
    insider_buy(
        db,
        security_id,
        accession="0000000014-26-000014",
        valid_from=date(2026, 6, 5),
        recorded_at=disc,
        insider_name="MILLS THOMAS E",
        shares=20_000_000.0,
        price=100_000.0,
        usd=2_000_000_000_000.0,
    )
    (buy,) = _get(client, thesis.id, security_id, "2026-07-15").json()["insider_buys"]
    assert buy["character"] == "implausible"


def test_price_window_insider_carries_the_10b5_1_role_and_character(client, db, security_id):
    """Provenance rides every chip (invariant #6): the 10b5-1 plan flag, the role, and the S2c
    ``character`` reach the wire so the tooltip can state them — an automatic-plan buy is not the same
    conviction signal, and a planned buy's character stays ``open_market`` (the flag rides BESIDE the
    character, tri-state). NB this planned buy is a CONSTRUCTED row — the ``aff_10b5_1=True`` BUY
    population query on real dev data is owed on the dev stack (see the S2c PR)."""
    thesis = persist_thesis(db, security_id)
    insider_buy(
        db,
        security_id,
        accession="0000000007-26-000007",
        valid_from=date(2026, 6, 5),
        recorded_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        role="CFO",
        aff_10b5_1=True,
    )
    (buy,) = _get(client, thesis.id, security_id, "2026-07-15").json()["insider_buys"]
    assert buy["insider_role"] == "CFO" and buy["aff_10b5_1"] is True
    assert (
        buy["character"] == "open_market"
    )  # planned ≠ set aside; no day low → never "primary_market"


# --- Slice B: three more dated event families through the SAME window + knowability gate ---
# Per family the load-bearing case is the two-axis no-lookahead one: a row RECORDED after
# known_at_for_asof(asof) is invisible on a scrubbed-back asof (the recorded_at gate), and a row
# whose event date is past the asof never appears (the valid axis) — rows seeded at EXPLICIT
# recorded_at values, mirroring the insider-buy gate test above.


def test_price_window_sell_gate_keys_on_recorded_at(client, db, security_id):
    """The sell twin of the insider no-lookahead test — BOTH axes, keyed on ``recorded_at``:
    a sale transacted 06-05 but INGESTED 07-01 is ABSENT at as-of 06-25 and PRESENT at 07-05; a
    FUTURE-transacted sale (recorded early) never appears. When visible the sale carries the
    two-clock fields and its wire ``character`` (``kept`` — no screen trips here)."""
    thesis = persist_thesis(db, security_id)
    disc = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)  # accepted — display-only, never gates
    ingest = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)  # recorded_at — THE gate
    insider_buy(
        db,
        security_id,
        accession="0000000101-26-000101",
        valid_from=date(2026, 6, 5),
        recorded_at=ingest,
        accepted=disc,
        txn_code="S",
        insider_name="A Seller",
    )
    insider_buy(
        db,
        security_id,
        accession="0000000102-26-000102",
        valid_from=date(2026, 8, 15),  # future vs both as-ofs — the valid axis must hide it
        recorded_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        txn_code="S",
        insider_name="Future Seller",
    )

    early = _get(client, thesis.id, security_id, "2026-06-25").json()
    late = _get(client, thesis.id, security_id, "2026-07-05").json()
    assert early["insider_sells"] == []  # recorded 07-01 > known_at(06-25) → not held yet
    (sell,) = late["insider_sells"]
    assert sell["d"] == "2026-06-05" and sell["insider_name"] == "A Seller"
    assert sell["disclosed"] == "2026-06-20" and sell["ingested"] == "2026-07-01"  # two clocks
    assert sell["character"] == "kept"
    # a code-S row is never a BUY chip (and vice versa — the family split is by txn_code)
    assert early["insider_buys"] == [] and late["insider_buys"] == []
    for asof, body in (("2026-06-25", early), ("2026-07-05", late)):
        assert all(s["d"] <= asof for s in body["insider_sells"])  # never past the as-of


def test_price_window_sell_characters_ride_the_wire(client, db, security_id):
    """Every sell-screen bucket reaches the wire, mapped to the contract vocabulary (``self`` →
    ``self_filing``, ``foreign`` → ``foreign_ordinary``), and NOTHING vanishes (WB #2 / #9): six
    sales — one per bucket — all ride, each with its deterministic attribution. A code-P buy beside
    them stays in ``insider_buys`` only."""
    thesis = persist_thesis(db, security_id)
    with db.cursor() as cur:  # the master name feeds the self-filing name fallback
        cur.execute(
            "UPDATE security_master SET name = %s WHERE id = %s", ("Devco Inc", security_id)
        )
    db.commit()
    ohlcv_bar(db, security_id, date(2026, 6, 5), 42.0, 46.0, 40.0, 45.0, 1_000.0)  # day low = 40
    disc = datetime(2026, 6, 20, tzinfo=timezone.utc)

    def sell(accession, name, **kw):
        insider_buy(
            db,
            security_id,
            accession=accession,
            valid_from=date(2026, 6, 5),
            recorded_at=disc,
            txn_code="S",
            insider_name=name,
            **kw,
        )

    sell("0000000103-26-000103", "Jane Doe", price=45.0, usd=45_000.0)  # kept
    sell("0000000104-26-000104", "Plan Seller", price=45.0, aff_10b5_1=True)  # planned
    sell(  # issuer self-filing via CIK equality (zero-padding normalized)
        "0000000105-26-000105",
        "Devco Holdings KK",
        price=45.0,
        rpt_owner_cik="1234567",
        issuer_cik="0001234567",
    )
    sell("0000000106-26-000106", "Discount Seller", price=30.0)  # below 40 × 0.9 → below_low
    sell("0000000107-26-000107", "Fat Finger", price=100_000.0, usd=2_000_000_000_000.0)
    sell(  # a home-market ordinary line mis-filed on the ADR's tape (S2c)
        "0000000108-26-000108",
        "Foreign Seller",
        price=45.0,
        security_title="Common Shares (2330.TW)",
        issuer_foreign_symbol="2330.TW",
    )
    # a code-P buy beside them — the family split is by txn_code, never a shared pool
    insider_buy(
        db,
        security_id,
        accession="0000000109-26-000109",
        valid_from=date(2026, 6, 5),
        recorded_at=disc,
        price=45.0,
    )

    body = _get(client, thesis.id, security_id, "2026-07-15").json()
    sells = body["insider_sells"]
    assert len(sells) == 6  # every screened sale rides — greyed on the FE, never dropped
    by_name = {s["insider_name"]: s["character"] for s in sells}
    assert by_name == {
        "Jane Doe": "kept",
        "Plan Seller": "planned",
        "Devco Holdings KK": "self_filing",  # wire-mapped from the screen's "self"
        "Discount Seller": "below_low",
        "Fat Finger": "implausible",
        "Foreign Seller": "foreign_ordinary",  # wire-mapped from the screen's "foreign"
    }
    assert [b["insider_name"] for b in body["insider_buys"]] == ["A Buyer"]  # the P row only


def test_price_window_relevance_floor_applies_to_all_three_families(client, db, security_id):
    """R1's relevance floor bounds the NEW families exactly as it bounds buys: a pre-floor sale /
    8-K / 13D (all genuinely pre-thesis — NOT a recall cut) is excluded; the in-window sibling of
    each survives."""
    thesis = persist_thesis(db, security_id)
    _set_created_at(db, thesis.id, date(2026, 6, 1))  # floor = 2025-06-01 (no bars → created−365)
    rec = datetime(2026, 6, 20, tzinfo=timezone.utc)
    insider_buy(
        db,
        security_id,
        accession="0000000110-26-000110",
        valid_from=date(2020, 1, 15),
        recorded_at=rec,
        txn_code="S",
        insider_name="Ancient Seller",
    )
    insider_buy(
        db,
        security_id,
        accession="0000000111-26-000111",
        valid_from=date(2025, 8, 1),
        recorded_at=rec,
        txn_code="S",
        insider_name="Recent Seller",
    )
    corp_event(
        db,
        security_id,
        accession="0000000112-26-000112",
        filed=date(2020, 2, 1),
        recorded_at=rec,
        items=["2.02"],
    )
    corp_event(
        db,
        security_id,
        accession="0000000113-26-000113",
        filed=date(2025, 9, 1),
        recorded_at=rec,
        items=["2.02"],
    )
    activist(
        db,
        security_id,
        accession="0000000114-26-000114",
        filed=date(2020, 3, 1),
        recorded_at=rec,
        form="SC 13D",
    )
    activist(
        db,
        security_id,
        accession="0000000115-26-000115",
        filed=date(2025, 10, 1),
        recorded_at=rec,
        form="SC 13D",
    )

    body = _get(client, thesis.id, security_id, "2026-07-15").json()
    assert body["start"] == "2025-06-01"  # the effective floor
    assert [s["insider_name"] for s in body["insider_sells"]] == ["Recent Seller"]
    assert [e["d"] for e in body["corporate_events"]] == ["2025-09-01"]
    assert [s["d"] for s in body["activist_stakes"]] == ["2025-10-01"]


def test_price_window_corporate_events_gate_keys_on_recorded_at(client, db, security_id):
    """The 8-K twin of the no-lookahead test: filed 06-05 but INGESTED 07-01 → absent at as-of
    06-25, present at 07-05 with form/items/url/ingested; a future-FILED 8-K (recorded early) never
    appears at either as-of."""
    thesis = persist_thesis(db, security_id)
    corp_event(
        db,
        security_id,
        accession="0000000116-26-000116",
        filed=date(2026, 6, 5),
        recorded_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        items=["2.02", "9.01"],
    )
    corp_event(
        db,
        security_id,
        accession="0000000117-26-000117",
        filed=date(2026, 8, 15),  # future vs both as-ofs
        recorded_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        items=["1.01"],
    )

    early = _get(client, thesis.id, security_id, "2026-06-25").json()["corporate_events"]
    late = _get(client, thesis.id, security_id, "2026-07-05").json()["corporate_events"]
    assert early == []  # recorded 07-01 > known_at(06-25); the 08-15 filing is future
    (ev,) = late
    assert ev["d"] == "2026-06-05" and ev["form"] == "8-K"
    assert ev["items"] == ["2.02", "9.01"]
    assert ev["url"].endswith("0000000116-26-000116-index.htm")  # the EDGAR index provenance (#6)
    assert ev["ingested"] == "2026-07-01"


def test_price_window_corporate_event_items_resolution_is_honest(client, db, security_id):
    """The items-resolve flow through the bitemporal read: v1 (items NULL) recorded 06-10, v2 (items
    resolved) recorded 06-20 — an as-of BETWEEN the two honestly reads ``items: null`` (what we held
    then), a later as-of reads the resolved codes, and there is never a double-count (latest version
    per accession). An off-policy item set rides too — NO server-side item cut."""
    thesis = persist_thesis(db, security_id)
    corp_event(
        db,
        security_id,
        accession="0000000118-26-000118",
        filed=date(2026, 6, 5),
        recorded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        items=None,  # v1 — not-yet-resolved
    )
    corp_event(
        db,
        security_id,
        accession="0000000118-26-000118",  # SAME accession — a re-version, not a new event
        filed=date(2026, 6, 5),
        recorded_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        items=["2.02", "9.01"],  # v2 — resolved
    )
    corp_event(  # an item outside any detector policy cut — still on the wire (#9)
        db,
        security_id,
        accession="0000000119-26-000119",
        filed=date(2026, 6, 6),
        recorded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        items=["7.01"],
    )

    mid = _get(client, thesis.id, security_id, "2026-06-15").json()["corporate_events"]
    late = _get(client, thesis.id, security_id, "2026-07-15").json()["corporate_events"]
    # at 06-15 only v1 was recorded: items honestly null (never the not-yet-known resolve)
    assert [(e["d"], e["items"]) for e in mid] == [
        ("2026-06-05", None),
        ("2026-06-06", ["7.01"]),
    ]
    # at 07-15 the resolve is held: ONE row (no double-count), the latest version's items
    assert [(e["d"], e["items"]) for e in late] == [
        ("2026-06-05", ["2.02", "9.01"]),
        ("2026-06-06", ["7.01"]),
    ]


def test_price_window_activist_gate_keys_on_recorded_at(client, db, security_id):
    """The 13D twin of the no-lookahead test: filed 06-05 but INGESTED 07-01 → absent at as-of
    06-25, present at 07-05; a future-filed 13D (recorded early) never appears."""
    thesis = persist_thesis(db, security_id)
    activist(
        db,
        security_id,
        accession="0000000120-26-000120",
        filed=date(2026, 6, 5),
        recorded_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        form="SCHEDULE 13D",
        filer_cik="0001234567",
        filer_name="Big Activist LP",
        pct_owned=7.5,
    )
    activist(
        db,
        security_id,
        accession="0000000121-26-000121",
        filed=date(2026, 8, 15),  # future vs both as-ofs
        recorded_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        form="SC 13D",
    )

    early = _get(client, thesis.id, security_id, "2026-06-25").json()["activist_stakes"]
    late = _get(client, thesis.id, security_id, "2026-07-05").json()["activist_stakes"]
    assert early == []
    (stake,) = late
    assert stake["d"] == "2026-06-05" and stake["form"] == "SCHEDULE 13D"
    assert stake["filer_name"] == "Big Activist LP" and stake["pct_owned"] == 7.5
    assert stake["ingested"] == "2026-07-01"


def test_price_window_activist_unresolved_identity_ships_null_not_dropped(client, db, security_id):
    """Invariant #9 on the wire: an old-era ``SC 13G`` whose filer identity / pct never resolved
    ships with nulls — the row RIDES, never dropped — beside a fully-resolved structured-era 13D.
    Both era form strings ride verbatim; the FE (not the server) greys the passive family."""
    thesis = persist_thesis(db, security_id)
    rec = datetime(2026, 6, 20, tzinfo=timezone.utc)
    activist(  # old-era passive row: identity fetch out of the bounded depth → all nulls
        db,
        security_id,
        accession="0000000122-26-000122",
        filed=date(2026, 6, 3),
        recorded_at=rec,
        form="SC 13G",
    )
    activist(
        db,
        security_id,
        accession="0000000123-26-000123",
        filed=date(2026, 6, 10),
        recorded_at=rec,
        form="SCHEDULE 13D",
        filer_cik="0009876543",
        filer_name="Engaged Capital",
        pct_owned=6.2,
    )

    stakes = _get(client, thesis.id, security_id, "2026-07-15").json()["activist_stakes"]
    assert [s["form"] for s in stakes] == ["SC 13G", "SCHEDULE 13D"]  # both eras, both families
    g, d = stakes
    assert g["filer_name"] is None and g["filer_cik"] is None and g["pct_owned"] is None  # kept
    assert g["url"].endswith("0000000122-26-000122-index.htm")  # provenance still rides (#6)
    assert d["filer_name"] == "Engaged Capital" and d["pct_owned"] == 6.2
