"""The fund launch radar's display API: the tape, one card's launches as of a night (point-in-time, invariant
#1), and the reversible add-to-ETFs pair. Cards, companies, funds and symbols are all made up; no network (the
resolve is the offline "already in the master" branch)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import Authorship, TermTier
from domain.scopes import WORKSPACE_ANY
from domain.thesis import BasketMember, Catalyst, KillCriterion, Segment, TermSetEntry, Thesis
from radar import fund_repo
from repositories import thesis_repo

ETFS = "ETFs"


@pytest.fixture(autouse=True)
def _clean_fund_tables(db):
    from app.routers import fund_radar

    fund_radar.clear_cache()
    with db.cursor() as cur:
        cur.execute("TRUNCATE fact_fund_filing, fact_fund_series, fact_fund_symbol")
    db.commit()


def _security(db, ticker, *, name=None, kind="equity") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, kind, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _card(db, name="Test Card A", *, with_company=False, extra_basket=()) -> Thesis:
    basket = list(extra_basket)
    segments = [Segment(label="Core")]
    if with_company:
        sid = _security(db, "ZRBX", name="Zorbex Group N.V.")
        basket.append(BasketMember(ticker="ZRBX", role="r", security_id=sid, segment="Core"))
    t = Thesis(
        id=uuid.uuid4(),
        name=name,
        narrative="x",
        tenant_id=DEFAULT_TENANT_ID,
        segments=segments,
        basket=basket,
    )
    thesis_repo.upsert(db, t)
    thesis_repo.set_term_set(
        db,
        t.id,
        [
            TermSetEntry(term="uranium", tier=TermTier.SIGNAL),
            TermSetEntry(term="nuclear", tier=TermTier.BROAD),
        ],
    )
    db.commit()
    return thesis_repo.get(db, t.id, workspace=WORKSPACE_ANY)


def _filing(db, acc, form, day, *, trust="Test Trust", private=False, funds=(), stored_at=None):
    """Store one filing and its fund rows: ``funds`` = (series_id, name, kind) triples. ``stored_at`` stamps the
    series rows' ``recorded_at`` (when we first learned of them); default is now."""
    fund_repo.record_filing_if_changed(
        db,
        fund_repo.FundFilingRow(
            accession=acc,
            form=form,
            filer_ciks=["0000000001"],
            filer_name=trust,
            accepted_at=None,
            filing_date=day,
            effectiveness_date=None,
            private_to_public=private,
            released_on=day if private else None,
            public_date=day,
            source_ref=f"https://example.test/{acc}",
        ),
    )
    for sid, name, kind in funds:
        if stored_at is not None:
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO fact_fund_series
                       (tenant_id, accession, series_key, series_id, series_name, kind, classes,
                        valid_from, recorded_at)
                       VALUES (%s, %s, %s, %s, %s, %s, '[]'::jsonb, %s, %s)""",
                    (DEFAULT_TENANT_ID, acc, sid, sid, name, kind, day, stored_at),
                )
            continue
        fund_repo.record_series_if_changed(
            db,
            fund_repo.FundSeriesRow(
                accession=acc,
                series_key=sid,
                series_id=sid,
                series_name=name,
                kind=kind,
                classes=[],
                public_date=day,
            ),
        )
    db.commit()


def _symbol(db, sid, symbol, source, night):
    fund_repo.record_symbol_if_new(db, fund_repo.SymbolRow(sid, "C1", symbol, source, night))
    db.commit()


def _count(db, table):
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test-only, fixed names
        return cur.fetchone()["n"]


# --- GET /radar/funds -------------------------------------------------------------------------------


def _seed_tape(db, today):
    _card(db, with_company=True)
    _filing(
        db,
        "A-1",
        "485APOS",
        today - timedelta(days=5),
        funds=[
            ("S1", "Test Uranium ETF", "new"),
            ("S2", "Test Nuclear Power ETF", "new"),
            ("S3", "Unrelated Bond ETF", "new"),
        ],
    )
    _filing(
        db,
        "A-2",
        "485APOS",
        today - timedelta(days=3),
        trust="Other Trust",
        funds=[("S4", "Test 2X Long ZRBX Daily ETF", "new")],
    )
    _filing(
        db, "A-3", "485APOS", today - timedelta(days=2), funds=[("S5", "Plain Bond ETF", "new")]
    )
    _filing(
        db,
        "A-4",
        "485APOS",
        today - timedelta(days=1),
        private=True,
        funds=[("S6", "Test Uranium Old ETF", "new")],
    )
    # a fund filed outside the window never shows
    _filing(
        db,
        "A-5",
        "485APOS",
        today - timedelta(days=400),
        funds=[("S7", "Test Uranium Ancient ETF", "new")],
    )


def test_tape_one_row_per_filing_with_whole_window_counts(client, db, pinned_today):
    _seed_tape(db, pinned_today)
    body = client.get("/radar/funds").json()

    # matched filings only by default, newest first: A-2 (name hit) then A-1 (signal + broad)
    assert [f["accession"] for f in body["filings"]] == ["A-2", "A-1"]
    a1 = body["filings"][1]
    assert (a1["form"], a1["trust"], a1["private_to_public"]) == ("485APOS", "Test Trust", False)
    assert a1["url"].endswith("A-1-index.htm") or a1["url"] == "https://example.test/A-1"
    funds = {f["name"]: f for f in a1["funds"]}
    assert len(funds) == 3  # the whole filing, matched or not
    uranium = funds["Test Uranium ETF"]
    assert uranium["stage"]["current"] == "filed" and uranium["stage"]["effective"] is None
    (m,) = uranium["matches"]
    assert (m["thesis_name"], m["tier"], m["signal_terms"]) == (
        "Test Card A",
        "signal",
        ["uranium"],
    )
    (mb,) = funds["Test Nuclear Power ETF"]["matches"]
    assert (mb["tier"], mb["broad_terms"], mb["signal_terms"]) == ("broad", ["nuclear"], [])
    assert funds["Unrelated Bond ETF"]["matches"] == []
    (nh,) = body["filings"][0]["funds"][0]["matches"]
    assert nh["tier"] is None and nh["name_hits"][0]["ticker"] == "ZRBX"
    assert nh["name_hits"][0]["link"] == "Core" and nh["name_hits"][0]["via"] == "ticker"

    # the counts cover the WHOLE window: 5 funds shown (A-1 x3, A-2, A-3), 3 matched, 1 private held back
    c = body["counts"]
    assert (c["filings"], c["funds"], c["matched_funds"], c["unmatched_funds"]) == (3, 5, 3, 2)
    assert (c["private_held_back_funds"], c["private_held_back_filings"]) == (1, 1)


def test_tape_toggles_show_the_rest_and_the_private_filings(client, db, pinned_today):
    _seed_tape(db, pinned_today)
    allrows = client.get("/radar/funds", params={"matched_only": "false"}).json()
    assert [f["accession"] for f in allrows["filings"]] == ["A-3", "A-2", "A-1"]
    assert allrows["counts"]["filings"] == 3  # counts do not move with the toggle
    priv = client.get(
        "/radar/funds", params={"matched_only": "false", "include_private": "true"}
    ).json()
    assert [f["accession"] for f in priv["filings"]] == ["A-4", "A-3", "A-2", "A-1"]
    assert priv["filings"][0]["private_to_public"] is True
    assert priv["counts"]["private_held_back_funds"] == 0
    assert priv["counts"]["funds"] == 6


# --- GET /theses/{id}/fund-launches (point in time) ---------------------------------------------------


def test_fund_launches_stage_uses_only_facts_public_by_asof(client, db):
    t = _card(db)
    d0 = date(2026, 6, 1)
    _filing(db, "F-1", "485APOS", d0, funds=[("S1", "Test Uranium ETF", "new")])
    _filing(
        db,
        "F-2",
        "485BPOS",
        d0 + timedelta(days=10),
        funds=[("S1", "Test Uranium ETF", "existing")],
    )
    _symbol(db, "S1", "TSTX", "sec_ticker_file", d0 + timedelta(days=12))
    _symbol(db, "S1", "TSTX", "nasdaq_directory", d0 + timedelta(days=14))

    def stage(asof):
        body = client.get(f"/theses/{t.id}/fund-launches", params={"asof": str(asof)}).json()
        (filing,) = body["filings"]
        (fund,) = filing["funds"]
        return fund["stage"], fund["symbols"]

    s, syms = stage(d0 + timedelta(days=5))  # before the 485BPOS and any symbol
    assert (s["current"], s["effective"], s["ticker"], s["listed"]) == ("filed", None, None, None)
    assert syms == []
    s, syms = stage(d0 + timedelta(days=11))  # effective, no ticker yet
    assert (s["current"], s["effective"], s["ticker"]) == ("effective", "2026-06-11", None)
    s, syms = stage(d0 + timedelta(days=13))  # a ticker, not yet listed
    assert (s["current"], s["ticker"], s["listed"], syms) == (
        "ticker",
        "2026-06-13",
        None,
        ["TSTX"],
    )
    s, _ = stage(d0 + timedelta(days=20))
    assert (s["current"], s["listed"]) == ("listed", "2026-06-15")


def test_fund_launches_window_asof_counts_and_private(client, db):
    t = _card(db, with_company=True)
    asof = date(2026, 8, 1)
    _filing(
        db,
        "G-1",
        "485APOS",
        asof - timedelta(days=10),
        funds=[
            ("S1", "Test Uranium ETF", "new"),
            ("S2", "Test Uranium Miners ETF", "new"),
            ("S9", "Unrelated Bond ETF", "new"),
        ],
    )
    _filing(
        db,
        "G-2",
        "485APOS",
        asof - timedelta(days=5),
        trust="Other Trust",
        funds=[("S3", "Test Nuclear Power ETF", "new")],
    )
    _filing(
        db,
        "G-3",
        "485APOS",
        asof - timedelta(days=4),
        trust="Third Trust",
        funds=[("S4", "Test 2X Long ZRBX Daily ETF", "new")],
    )
    _filing(
        db,
        "G-4",
        "485APOS",
        asof + timedelta(days=1),
        funds=[("S5", "Test Uranium Later ETF", "new")],
    )
    _filing(
        db,
        "G-5",
        "485APOS",
        asof - timedelta(days=200),
        funds=[("S6", "Test Uranium Early ETF", "new")],
    )
    _filing(
        db,
        "G-6",
        "485APOS",
        asof - timedelta(days=3),
        private=True,
        funds=[("S7", "Test Uranium Old ETF", "new")],
    )

    body = client.get(
        f"/theses/{t.id}/fund-launches", params={"asof": str(asof), "days": 90}
    ).json()
    assert body["asof"] == "2026-08-01"
    assert [f["accession"] for f in body["filings"]] == [
        "G-3",
        "G-2",
        "G-1",
    ]  # no later, old or private
    g1 = body["filings"][2]
    assert sorted(f["name"] for f in g1["funds"]) == [
        "Test Uranium ETF",
        "Test Uranium Miners ETF",
    ]  # matched only
    c = body["counts"]
    # by filing and by sponsor, never by fund: G-1 has two funds but is one filing
    assert (c["filings"], c["sponsors"], c["funds"]) == (3, 3, 4)
    assert (c["signal_filings"], c["broad_filings"], c["name_filings"]) == (1, 1, 1)


def test_fund_launches_in_etfs_flag_and_404(client, db):
    held = _security(db, "TSTX", kind="etf")
    t = _card(
        db,
        extra_basket=[
            BasketMember(ticker="TSTX", role="ETF sleeve", security_id=held, segment=ETFS)
        ],
    )
    asof = date(2026, 8, 1)
    _filing(
        db,
        "H-1",
        "485APOS",
        asof - timedelta(days=9),
        funds=[("S1", "Test Uranium ETF", "new"), ("S2", "Test Uranium Two ETF", "new")],
    )
    _symbol(db, "S1", "TSTX", "sec_ticker_file", asof - timedelta(days=2))
    _symbol(db, "S2", "TSTY", "sec_ticker_file", asof - timedelta(days=2))
    (filing,) = client.get(f"/theses/{t.id}/fund-launches", params={"asof": str(asof)}).json()[
        "filings"
    ]
    assert {f["name"]: f["in_etfs"] for f in filing["funds"]} == {
        "Test Uranium ETF": True,
        "Test Uranium Two ETF": False,
    }
    assert client.get(f"/theses/{uuid.uuid4()}/fund-launches").status_code == 404


# --- the per-card window: ANY stage date inside it, and the start-night rule --------------------------

ASOF = date(2026, 11, 4)


LONG_AGO = datetime(
    2026, 9, 5, 16, 0, tzinfo=timezone.utc
)  # 60 days before ASOF: stored long before any symbol


def _old_fund(db, *, symbols=(), stored_at=LONG_AGO):
    """A fund filed 200 days before ASOF (first stored at ``stored_at``), plus an unrelated series."""
    _filing(
        db,
        "W-1",
        "485APOS",
        ASOF - timedelta(days=200),
        funds=[("S1", "Test Uranium ETF", "new"), ("S9", "Unrelated Bond ETF", "new")],
        stored_at=stored_at,
    )
    for sid, sym, source, ago in symbols:
        _symbol(db, sid, sym, source, ASOF - timedelta(days=ago))


def _launches(client, t, asof=ASOF):
    return client.get(f"/theses/{t.id}/fund-launches", params={"asof": str(asof)}).json()


def test_a_fund_filed_long_ago_shows_when_its_listing_is_first_seen_in_the_window(client, db):
    t = _card(db)
    _old_fund(
        db,
        symbols=[
            ("S9", "TSTZ", "sec_ticker_file", 30),  # the start night: the radar began reading here
            ("S9", "TSTZ", "nasdaq_directory", 30),
            ("S1", "TSTX", "sec_ticker_file", 12),
            ("S1", "TSTX", "nasdaq_directory", 10),  # first seen 10 days ago, not the start night
        ],
    )
    body = _launches(client, t)
    (filing,) = body["filings"]
    (fund,) = filing["funds"]
    assert fund["name"] == "Test Uranium ETF" and fund["symbols"] == ["TSTX"]
    assert fund["stage"]["current"] == "listed" and fund["stage"]["filed"] == "2026-04-18"
    assert fund["in_etfs"] is False  # so the screen offers "+ add to ETFs"
    assert body["symbol_history_since"] == str(ASOF - timedelta(days=30))
    assert body["counts"]["filings"] == 1


def test_a_symbol_first_seen_the_night_the_series_was_stored_is_not_activity(client, db):
    t = _card(db)
    night = ASOF - timedelta(days=20)  # 2026-10-15
    _old_fund(
        db,
        stored_at=datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc),  # 11:00 ET on the night
        symbols=[("S1", "TSTX", "sec_ticker_file", 20), ("S1", "TSTX", "nasdaq_directory", 20)],
    )
    body = _launches(client, t)
    assert body["filings"] == [] and body["counts"]["filings"] == 0
    assert body["symbol_history_since"] == str(night)  # still reported as before


def test_a_symbol_first_seen_30_nights_after_the_series_was_stored_is_activity(client, db):
    t = _card(db)
    _old_fund(
        db,
        stored_at=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        symbols=[("S1", "TSTX", "sec_ticker_file", 5), ("S1", "TSTX", "nasdaq_directory", 5)],
    )  # stored 2026-09-25, symbols first seen 2026-10-30: 35 nights later
    (filing,) = _launches(client, t)["filings"]
    (fund,) = filing["funds"]
    assert fund["stage"]["current"] == "listed" and fund["symbols"] == ["TSTX"]
    assert fund["in_etfs"] is False  # so the screen offers "+ add to ETFs"


def test_a_backfilled_old_fund_whose_symbol_is_first_seen_the_backfill_night_is_not_activity(
    client, db
):
    t = _card(db)
    m = ASOF - timedelta(
        days=3
    )  # the backfill night M (2026-11-01); the fund was filed 200 days earlier
    _old_fund(
        db,
        stored_at=datetime(2026, 11, 1, 22, 40, tzinfo=timezone.utc),  # 17:40 ET on M
        symbols=[("S1", "TSTX", "sec_ticker_file", 3), ("S1", "TSTX", "nasdaq_directory", 3)],
    )
    assert _launches(client, t)["filings"] == []
    assert m == date(2026, 11, 1)


def test_a_pass_that_crosses_midnight_does_not_make_its_own_symbols_activity(client, db):
    t = _card(db)
    # symbols stamped with the pass's night N (2026-10-30); the rows were stored at 00:30 ET on N+1
    _old_fund(
        db,
        stored_at=datetime(2026, 10, 31, 4, 30, tzinfo=timezone.utc),  # 00:30 EDT, 2026-10-31
        symbols=[("S1", "TSTX", "sec_ticker_file", 5), ("S1", "TSTX", "nasdaq_directory", 5)],
    )
    assert _launches(client, t)["filings"] == []


def test_a_listing_first_seen_after_asof_stays_invisible(client, db):
    t = _card(db)
    _old_fund(
        db,
        symbols=[
            ("S9", "TSTZ", "sec_ticker_file", 30),
            ("S9", "TSTZ", "nasdaq_directory", 30),
            ("S1", "TSTX", "sec_ticker_file", -3),  # seen 3 nights AFTER asof
        ],
    )
    body = _launches(client, t)
    assert body["filings"] == []  # nothing else of this fund falls in the window
    assert _launches(client, t, ASOF + timedelta(days=5))["filings"][0]["funds"][0]["symbols"] == [
        "TSTX"
    ]


def test_going_effective_inside_the_window_counts_and_filings_order_by_newest_activity(client, db):
    t = _card(db)
    _old_fund(db)
    _filing(
        db,
        "W-2",
        "485BPOS",
        ASOF - timedelta(days=5),
        funds=[("S1", "Test Uranium ETF", "existing")],
    )
    _filing(
        db,
        "W-3",
        "485APOS",
        ASOF - timedelta(days=20),
        funds=[("S2", "Test Nuclear Power ETF", "new")],
    )
    body = _launches(client, t)
    # the old fund went effective 5 days ago: newest activity, so it leads the newer-FILED one
    assert [f["accession"] for f in body["filings"]] == ["W-1", "W-3"]
    old = body["filings"][0]["funds"][0]
    assert old["stage"]["current"] == "effective" and old["stage"]["effective"] == "2026-10-30"
    assert body["symbol_history_since"] is None


# --- POST /radar/funds/attach | detach ----------------------------------------------------------------


def _rich_card(db) -> Thesis:
    """A card carrying every field a careless rewrite would wipe."""
    co = _security(db, "ACMQ", name="Acme Quartz Inc")
    t = _card(
        db,
        extra_basket=[
            BasketMember(
                ticker="ACMQ",
                role="r",
                security_id=co,
                segment="Core",
                context_role="payer",
                signed_off=True,
                conviction=3,
                authored_by=Authorship.OPERATOR_EDITED,
                thesis_fit="why it sits here",
            )
        ],
    )
    # catalysts and kill criteria have their own narrow writers (upsert never touches them)
    thesis_repo.set_catalysts(
        db,
        t.id,
        [Catalyst(id=uuid.uuid4(), label="a catalyst", kind="regulatory")],
        tenant_id=DEFAULT_TENANT_ID,
    )
    thesis_repo.set_kill_criteria(
        db,
        t.id,
        [KillCriterion(id=uuid.uuid4(), text="a kill criterion")],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    return thesis_repo.get(db, t.id, workspace=WORKSPACE_ANY)


def _tables(db):
    return {
        t: _count(db, t)
        for t in ("basket_member", "catalyst", "kill_criterion", "security_master", "thesis")
    }


def test_attach_adds_one_etfs_row_idempotently_and_detach_reverses_it(client, db):
    _security(db, "TSTX", name="Test Wrapper ETF")  # offline: the resolve marks the existing row
    t = _rich_card(db)
    before = _tables(db)
    stored = thesis_repo.get(db, t.id, workspace=WORKSPACE_ANY)

    r = client.post("/radar/funds/attach", json={"thesis_id": str(t.id), "symbol": "tstx"}).json()
    assert r["added"] is True and r["already"] is False and r["ticker"] == "TSTX"
    after = _tables(db)
    assert after["basket_member"] == before["basket_member"] + 1  # exactly one row
    assert {k: v for k, v in after.items() if k != "basket_member"} == {
        k: v for k, v in before.items() if k != "basket_member"
    }

    now = thesis_repo.get(db, t.id, workspace=WORKSPACE_ANY)
    (new,) = [m for m in now.basket if m.ticker == "TSTX"]
    assert (new.role, new.segment, new.authored_by, new.signed_off, new.conviction) == (
        "ETF sleeve",
        ETFS,
        Authorship.SYSTEM_DRAFTED,
        True,
        None,
    )
    # the wipe trap: everything else on the card is untouched
    old = [m for m in now.basket if m.ticker != "TSTX"]
    assert [m.model_dump() for m in old] == [m.model_dump() for m in stored.basket]
    assert [c.label for c in now.catalysts] == ["a catalyst"]
    assert [k.text for k in now.kill_criteria] == ["a kill criterion"]
    assert [(e.term, e.tier) for e in now.term_set] == [(e.term, e.tier) for e in stored.term_set]
    assert [s.label for s in now.segments][-3:] == ["Context", "ETFs", "Bench"]
    with db.cursor() as cur:
        cur.execute("SELECT instrument_kind FROM security_master WHERE ticker = 'TSTX'")
        assert cur.fetchone()["instrument_kind"] == "etf"

    # idempotent: a second attach writes nothing
    r2 = client.post("/radar/funds/attach", json={"thesis_id": str(t.id), "symbol": "TSTX"}).json()
    assert r2["already"] is True and r2["added"] is False
    assert _tables(db) == after

    # detach removes only that ETFs row; a second detach is a no-op
    d = client.post("/radar/funds/detach", json={"thesis_id": str(t.id), "symbol": "TSTX"}).json()
    assert d["removed"] is True
    assert _tables(db) == before
    d2 = client.post("/radar/funds/detach", json={"thesis_id": str(t.id), "symbol": "TSTX"}).json()
    assert d2["removed"] is False
    assert _tables(db) == before


def test_attach_refuses_a_fund_already_on_the_card_in_another_link(client, db):
    sid = _security(db, "TSTX", name="Test Wrapper ETF", kind="etf")
    t = _card(
        db,
        extra_basket=[BasketMember(ticker="TSTX", role="r", security_id=sid, segment="Core")],
    )
    before = _tables(db)
    r = client.post("/radar/funds/attach", json={"thesis_id": str(t.id), "symbol": "TSTX"})
    assert r.status_code == 422 and "TSTX" in r.json()["detail"]
    assert _tables(db) == before  # nothing written


def test_attach_detach_404_and_blank_symbol(client, db):
    t = _card(db)
    body = {"thesis_id": str(uuid.uuid4()), "symbol": "TSTX"}
    assert client.post("/radar/funds/attach", json=body).status_code == 404
    assert client.post("/radar/funds/detach", json=body).status_code == 404
    blank = {"thesis_id": str(t.id), "symbol": "  "}
    assert client.post("/radar/funds/attach", json=blank).status_code == 422
    # detaching a symbol the master has never seen is a quiet no-op
    ok = {"thesis_id": str(t.id), "symbol": "NOPE"}
    assert client.post("/radar/funds/detach", json=ok).json()["removed"] is False
