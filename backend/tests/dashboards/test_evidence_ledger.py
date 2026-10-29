"""Evidence Ledger (``docs/dashboards/evidence-ledger.md``): the authority class for every source, a trigger's
provenance rows (an accession against a price reference, the EDGAR link), the recorded card in effect, a
reconstructed night marked, the dated and standing rows, the role dates, the tallies on hand-built rows; the router
off one cached read with its 404 and the card's own entries read live; and, DB-backed, a call recorded after a past
as-of never being the card in effect, a fact ratified after it never being a row, and a roster snapshot taken after
it never dating a role."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import evidence_ledger as router_mod
from dashboards.baskets import CardChoice, CardsRead, Membership
from dashboards.cache import CACHE
from dashboards.evidence_ledger import (
    AUTHORITY_ORDER,
    KIND_ORDER,
    ROLE_WORDS,
    CardEntries,
    LedgerRow,
    RecordedNight,
    RosterSnapshot,
    Source,
    detail_lines,
    entry_rows,
    evidence_ledger,
    fact_authority,
    fact_rows,
    is_edgar,
    ledger_base,
    role_rows,
    role_starts,
    roster_companies,
    sort_key,
    tallies_of,
    trace_of,
    trigger_rows,
)
from dashboards.filings import (
    FilingRow,
    Who,
    filing_index_url,
    rows_from_catalysts,
    rows_from_ratified,
)
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, TriggerRef
from domain.enums import ContextRole, Grade, Kind, State, Verdict
from domain.market_time import market_today
from domain.signal import Provenance
from domain.thesis import (
    BENCH_LABEL,
    CONTEXT_LABEL,
    BasketMember,
    Catalyst,
    Evidence,
    KillCriterion,
    Segment,
    Thesis,
)
from ingest.cash_burn import ingest_cash_burn
from repositories import thesis_repo
from repositories.thesis_repo import _member_to_snapshot

UTC = timezone.utc
ASOF = date(2026, 10, 28)
SID_A, SID_B, SID_C = (uuid.UUID(int=n) for n in (0xA1, 0xB2, 0xC3))
T1 = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
CIK = "0000876543"
ACC = "0000876543-26-000010"
ACC2 = "0000876543-26-000011"
W = Who(SID_A, "AAA", CIK, "Acme Corp")


def utc(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def prov(source: str, ref: str, **detail) -> Provenance:
    return Provenance(source=source, ref=ref, detail=detail)


def trig(
    sid: UUID,
    label: str,
    kind: Kind = Kind.INSIDER,
    on: date | None = date(2026, 10, 20),
    grade: Grade | None = Grade.CORE,
    sources: list[Provenance] | None = None,
) -> TriggerRef:
    return TriggerRef(
        label=label, kind=kind, grade=grade, event_date=on, security_id=sid, sources=sources or []
    )


def card(*, triggers=(), risks=(), asof: date = ASOF, tid: UUID = T1) -> CallCard:
    return CallCard(
        thesis_id=tid,
        asof=asof,
        state=State.ARMED,
        verdict=Verdict.CORE_ENTRY,
        expression="",
        key_conviction=KeyState(turned=True, label="Conviction"),
        key_confirmation=KeyState(turned=True, label="Confirmation"),
        triggers_fired=list(triggers),
        risk_signals=list(risks),
    )


def night(c: CallCard, *, reconstructed: bool = False) -> RecordedNight:
    return RecordedNight(c.asof, reconstructed, c)


def member(
    ticker: str,
    sid: UUID | None,
    segment: str | None = "Core",
    *,
    silent: bool = False,
    role: str | None = None,
) -> Membership:
    return Membership(T1, "Grid", ticker, sid, segment, silent, role)


TICKERS = {SID_A: "AAA", SID_B: "BBB"}
CIKS = {SID_A: CIK, SID_B: None}


def fact(**over) -> dict:
    return {
        "cash_usd": Decimal("41200000"),
        "quarterly_burn_usd": Decimal("8100000"),
        "source": "ratified",
        "source_ref": ACC,
        "ratified_by": "operator",
        "valid_from": date(2026, 6, 30),
        "recorded_at": utc(2026, 10, 21, 3, 0),  # 23:00 ET on Oct 20
        **over,
    }


# ---------------------------------------------------------------------------------------------------------
# the authority class: one rule per source, WHO put the row there
# ---------------------------------------------------------------------------------------------------------


def test_a_ratified_fact_is_operator_and_names_who_and_when():
    (r,) = rows_from_ratified([fact()], [], [], W)
    assert (r.form, r.ratifier, r.ratified_on) == ("ratified fact", "operator", date(2026, 10, 20))
    assert fact_authority(r) == "operator"
    (row,) = fact_rows({SID_A: [r]}, {})
    assert (row.authority, row.kind, row.by, row.on, row.date) == (
        "operator",
        "ratified",
        "operator",
        date(2026, 10, 20),
        date(2026, 10, 20),
    )


@pytest.mark.parametrize("who", ["auto", " AUTO ", None, ""])
def test_a_fact_the_machine_or_a_feed_stored_is_automatic_and_names_no_ratifier(who):
    (r,) = rows_from_ratified([fact(ratified_by=who)], [], [], W)
    assert (r.form, r.ratifier, r.ratified_on) == ("stored fact", None, None)
    (row,) = fact_rows({SID_A: [r]}, {})
    assert (row.authority, row.kind, row.by, row.on) == ("automatic", "ratified", None, None)
    assert row.date == date(2026, 6, 30)  # dated by the cover date, as the reader gives it


def test_a_catalyst_fact_follows_the_same_rule_and_keeps_its_event_date():
    base = {
        "catalyst_type": "regulatory",
        "label": "FDA decision",
        "source": "ratified",
        "source_ref": "https://www.fda.gov/x",
        "valid_from": date(2026, 10, 10),
        "recorded_at": utc(2026, 10, 12, 15, 0),
    }
    (op,) = rows_from_catalysts([{**base, "ratified_by": "operator"}], W)
    (feed,) = rows_from_catalysts([{**base, "ratified_by": None}], W)
    (auto,) = rows_from_catalysts([{**base, "ratified_by": "auto"}], W)
    assert (op.ratifier, op.ratified_on) == ("operator", date(2026, 10, 12))
    assert (feed.ratifier, auto.ratifier) == (None, None)
    rows = fact_rows({SID_A: [op, feed, auto]}, {})
    assert [(r.authority, r.kind, r.date) for r in rows] == [
        ("operator", "catalyst_fact", date(2026, 10, 10)),
        ("automatic", "catalyst_fact", date(2026, 10, 10)),
        ("automatic", "catalyst_fact", date(2026, 10, 10)),
    ]
    assert rows[0].sources == (Source("catalyst", None, "https://www.fda.gov/x"),)
    assert not rows[0].edgar  # a stored URL that is not an SEC page is a link, not an EDGAR link


def test_the_ledger_takes_only_the_ratified_and_catalyst_kinds_of_the_shared_reader():
    other = FilingRow(SID_A, "AAA", "8k", "8-K", date(2026, 10, 1), "an 8-K")
    form4 = FilingRow(SID_A, "AAA", "form4", "4", date(2026, 10, 2), "a Form 4")
    (r,) = rows_from_ratified([fact()], [], [], W)
    assert [x.kind for x in fact_rows({SID_A: [other, r, form4]}, {})] == ["ratified"]


def test_trigger_and_risk_rows_are_system_and_the_card_entries_are_card():
    c = card(
        triggers=[trig(SID_A, "insider cluster buy")],
        risks=[trig(SID_A, "dilution overhang", Kind.DILUTION_RISK, grade=None)],
    )
    rows = trigger_rows(night(c), tickers=TICKERS, ciks=CIKS, silent={})
    assert [(r.authority, r.kind) for r in rows] == [
        ("system", "trigger"),
        ("system", "risk_signal"),
    ]
    entries = CardEntries(
        (Catalyst(id=uuid.uuid4(), label="vote"),),
        (KillCriterion(id=uuid.uuid4(), text="loses the contract"),),
        (Evidence(id=uuid.uuid4(), kind="8-K", label="award", ref="https://x.test/a"),),
    )
    assert {r.authority for r in entry_rows(entries, ASOF)} == {"card"}


def test_a_role_row_is_operator_and_the_four_classes_are_the_whole_set():
    (r,) = role_rows(roster_companies([member("AAA", SID_A, role="payer")]), {})
    assert (r.authority, r.kind, r.by) == ("operator", "role", "operator")
    assert AUTHORITY_ORDER == ("operator", "system", "automatic", "card")
    assert set(ROLE_WORDS) == {r.value for r in ContextRole}  # a role added to the enum fails here


# ---------------------------------------------------------------------------------------------------------
# a trigger's provenance rows
# ---------------------------------------------------------------------------------------------------------


def test_a_trigger_row_carries_each_source_with_an_edgar_link_for_an_accession_and_text_for_a_price():
    c = card(
        triggers=[
            trig(
                SID_A,
                "3 insiders bought",
                sources=[
                    prov("form4", ACC, shares=4000, net_usd=16000.0, buyers=["Roe", "Doe"]),
                    prov("form4", ACC2),
                ],
            ),
            trig(
                SID_A,
                "8-day breakout",
                Kind.TECHNICAL_BREAKOUT,
                on=date(2026, 10, 27),
                grade=Grade.FLIP,
                sources=[prov("price", f"price:{SID_A}:2026-10-27", close=12.5, rvol=2.1)],
            ),
        ]
    )
    insider, breakout = trigger_rows(night(c), tickers=TICKERS, ciks=CIKS, silent={})
    assert (insider.ticker, insider.form, insider.date) == ("AAA", "insider", date(2026, 10, 20))
    assert [(s.source, s.ref, s.url) for s in insider.sources] == [
        ("form4", ACC, filing_index_url(CIK, ACC)),
        ("form4", ACC2, filing_index_url(CIK, ACC2)),
    ]
    assert insider.sources[0].detail == ("buyers: Roe, Doe", "net_usd: 16000.0", "shares: 4000")
    assert insider.edgar and trace_of(insider) == "accession"
    assert insider.detail[:3] == (
        "Signal kind: insider",
        "Grade: core",
        "Event date: 2026-10-20",
    )
    # a price reference is shown as text: no link, no EDGAR, and the trace says so
    (price,) = breakout.sources
    assert (price.source, price.ref, price.url) == ("price", f"price:{SID_A}:2026-10-27", None)
    assert price.detail == ("close: 12.5", "rvol: 2.1")
    assert not breakout.edgar and trace_of(breakout) == "price"
    assert (breakout.form, breakout.date) == ("technical breakout", date(2026, 10, 27))


def test_an_accession_with_no_known_cik_still_traces_to_an_accession_but_carries_no_guessed_link():
    c = card(triggers=[trig(SID_B, "buy", sources=[prov("form4", ACC)])])
    (r,) = trigger_rows(night(c), tickers=TICKERS, ciks=CIKS, silent={})
    assert r.sources[0].url is None and not r.edgar and trace_of(r) == "accession"


def test_every_trace_class_and_a_trigger_with_no_source_or_no_date():
    def one(**kw) -> LedgerRow:
        (r,) = trigger_rows(
            night(card(triggers=[trig(SID_A, "x", **kw)])), tickers={}, ciks={}, silent={}
        )
        return r

    assert trace_of(one(sources=[prov("xbrl", ACC)])) == "accession"
    assert trace_of(one(sources=[prov("catalyst", "DOE-AWARD-77")])) == "other"
    assert trace_of(one(sources=[prov("price", "price:x:y"), prov("form4", ACC)])) == "accession"
    bare = one(sources=[], on=None)
    assert trace_of(bare) == "none" and bare.date is None and "No source stated" in bare.detail
    assert "No event date stated" in bare.detail
    assert bare.ticker is None  # a security the master does not name: no ticker is made up


def test_a_risk_signal_row_says_it_never_turns_a_key_and_a_silent_company_is_tagged():
    c = card(
        risks=[trig(SID_A, "overhang", Kind.DILUTION_RISK, grade=None, sources=[prov("8-k", ACC)])]
    )
    (r,) = trigger_rows(night(c), tickers=TICKERS, ciks=CIKS, silent={SID_A: "Context"})
    assert r.kind == "risk_signal" and r.silent == "Context"
    assert r.detail[0] == "Risk signal (feeds the counter-case, never turns a key)"
    assert not any(line.startswith("Grade") for line in r.detail)


def test_detail_lines_are_sorted_as_stored_skip_the_unstated_and_word_booleans_lists_and_maps():
    got = detail_lines(
        {
            "zeta": 1,
            "alpha": None,
            "flag": True,
            "off": False,
            "names": ["a", "b"],
            "nested": {"y": 1, "x": [1, 2]},
            "ratio": 0.3412,
        }
    )
    assert got == (
        "flag: yes",
        "names: a, b",
        'nested: {"x": [1, 2], "y": 1}',
        "off: no",
        "ratio: 0.3412",
        "zeta: 1",
    )
    assert detail_lines({}) == ()


def test_is_edgar_is_the_sec_host_only():
    assert is_edgar("https://www.sec.gov/Archives/edgar/data/1/2/")
    assert is_edgar("https://sec.gov/x")
    assert not is_edgar("https://www.fda.gov/x") and not is_edgar("https://notsec.gov.evil.test/")
    assert not is_edgar(None) and not is_edgar("")


# ---------------------------------------------------------------------------------------------------------
# a reconstructed night is marked
# ---------------------------------------------------------------------------------------------------------


def test_a_reconstructed_nights_rows_are_marked_reported_never_scored():
    c = card(triggers=[trig(SID_A, "buy", sources=[prov("form4", ACC)])], asof=date(2026, 10, 27))
    (honest,) = trigger_rows(night(c), tickers=TICKERS, ciks=CIKS, silent={})
    (rebuilt,) = trigger_rows(night(c, reconstructed=True), tickers=TICKERS, ciks=CIKS, silent={})
    assert not honest.reconstructed and "From a reconstructed night" not in " ".join(honest.detail)
    assert rebuilt.reconstructed
    assert "From a reconstructed night: reported, never scored" in rebuilt.detail
    assert "Recorded in the call for 2026-10-27" in rebuilt.detail


# ---------------------------------------------------------------------------------------------------------
# the card's own entries, and the standing section
# ---------------------------------------------------------------------------------------------------------


def entries() -> CardEntries:
    return CardEntries(
        catalysts=(
            Catalyst(
                id=uuid.uuid4(), label="past readout", kind="clinical", when_date=date(2026, 9, 1)
            ),
            Catalyst(
                id=uuid.uuid4(),
                label="next vote",
                when_date=date(2026, 11, 20),
                when_label="mid-Nov",
            ),
            Catalyst(id=uuid.uuid4(), label="someday", when_label="~3wk"),
            Catalyst(id=uuid.uuid4(), label="bare"),
        ),
        kills=(KillCriterion(id=uuid.uuid4(), text="loses the contract"),),
        evidence=(
            Evidence(
                id=uuid.uuid4(),
                kind="8-K",
                label="award",
                ref="https://x.test/a",
                date_label="Oct 2026",
            ),
            Evidence(id=uuid.uuid4(), kind="FORM 4", label="a buy", ref=ACC),
        ),
    )


def test_catalysts_with_a_date_are_dated_and_the_rest_are_standing_with_the_after_asof_one_said():
    rows = {r.label: r for r in entry_rows(entries(), ASOF)}
    assert rows["past readout"].date == date(2026, 9, 1) and rows["past readout"].form == "clinical"
    assert rows["past readout"].detail == ("Dated 2026-09-01",)
    assert rows["next vote"].detail == ("Dated 2026-11-20, after the as-of", "When: mid-Nov")
    assert rows["someday"].date is None and rows["someday"].detail == (
        "No date stated",
        "When: ~3wk",
    )
    assert rows["bare"].detail == ("No date stated",)
    assert rows["loses the contract"].date is None and rows["loses the contract"].kind == "kill"


def test_an_evidence_link_links_a_stored_url_and_shows_an_accession_alone_as_text():
    rows = {r.label: r for r in entry_rows(entries(), ASOF)}
    award, buy = rows["award"], rows["a buy"]
    assert award.sources == (Source("8-K", "https://x.test/a", "https://x.test/a"),)
    assert award.detail == ("Dated: Oct 2026",) and not award.edgar
    # an accession names no company here: no CIK, so no link is built (never a guessed one)
    assert buy.sources == (Source("FORM 4", ACC, None),) and buy.date is None and not buy.edgar


def test_the_standing_section_holds_every_undated_row_and_the_dated_list_keeps_the_rest_newest_first():
    c = card(
        triggers=[
            trig(SID_A, "older buy", on=date(2026, 10, 10), sources=[prov("form4", ACC)]),
            trig(SID_A, "newer buy", on=date(2026, 10, 25), sources=[prov("form4", ACC2)]),
            trig(SID_A, "no date", on=None),
        ]
    )
    base = ledger_base(
        thesis_id=T1,
        thesis_name="Grid",
        memberships=[member("AAA", SID_A, role="payer")],
        night=night(c),
        tickers=TICKERS,
        ciks=CIKS,
        rows_by_sid={},
        snapshots=[],
    )
    page = evidence_ledger(base, entries(), asof=ASOF)
    assert [r.label for r in page.standing] == [
        "loses the contract",  # the card's own undated entries first, in a fixed kind order
        "someday",
        "bare",
        "award",
        "a buy",
        "AAA is tagged payer on this card",  # a role no snapshot dates
        "no date",  # a trigger with no date is never given one
    ]
    assert [(g.date, [r.label for r in g.rows]) for g in page.groups] == [
        (date(2026, 11, 20), ["next vote"]),
        (date(2026, 10, 25), ["newer buy"]),
        (date(2026, 10, 10), ["older buy"]),
        (date(2026, 9, 1), ["past readout"]),
    ]
    keys = [r.key for r in page.standing] + [r.key for g in page.groups for r in g.rows]
    assert len(keys) == len(set(keys)) == 11
    # every dated row is a row; nothing was dropped on the way
    assert page.tallies.rows == 11


def test_a_day_orders_its_rows_by_kind_then_ticker_then_label():
    day = date(2026, 10, 20)
    rows = [
        LedgerRow("card", "card_catalyst", "z", date=day),
        LedgerRow("system", "risk_signal", "b", date=day, ticker="AAA"),
        LedgerRow("system", "trigger", "m", date=day, ticker="BBB"),
        LedgerRow("system", "trigger", "m", date=day, ticker="AAA"),
        LedgerRow("operator", "ratified", "r", date=day, ticker="AAA"),
    ]
    got = sorted(rows, key=sort_key)
    assert [(r.kind, r.ticker) for r in got] == [
        ("trigger", "AAA"),
        ("trigger", "BBB"),
        ("risk_signal", "AAA"),
        ("ratified", "AAA"),
        ("card_catalyst", None),
    ]
    assert tuple(KIND_ORDER[:2]) == ("trigger", "risk_signal")


# ---------------------------------------------------------------------------------------------------------
# the roster, and the role dates
# ---------------------------------------------------------------------------------------------------------


def snap(day: date, *members: tuple[UUID, str]) -> RosterSnapshot:
    return RosterSnapshot(
        datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC),
        [{"security_id": str(s), "context_role": r} for s, r in members],
    )


def test_a_role_is_dated_by_the_first_snapshot_of_the_unbroken_run_that_carries_it():
    got = role_starts(
        [
            snap(date(2026, 9, 15), (SID_B, "payer")),
            snap(date(2026, 10, 1), (SID_A, "payer"), (SID_B, "payer")),
            snap(date(2026, 10, 10), (SID_A, "payer"), (SID_B, "payer")),
        ]
    )
    assert got == {
        str(SID_A): ("payer", date(2026, 10, 1)),
        str(SID_B): ("payer", date(2026, 9, 15)),
    }


def test_a_changed_or_dropped_role_starts_its_run_again():
    snaps = [
        snap(date(2026, 9, 15), (SID_A, "payer"), (SID_B, "payer")),
        snap(date(2026, 10, 1), (SID_A, "counter_case")),  # A changed role, B dropped it
        snap(date(2026, 10, 10), (SID_A, "counter_case"), (SID_B, "payer")),
    ]
    got = role_starts(snaps)
    assert got[str(SID_A)] == ("counter_case", date(2026, 10, 1))
    assert got[str(SID_B)] == ("payer", date(2026, 10, 10))  # back after a gap: a new run
    # no snapshot at all, or a last one without the role: nothing to date
    assert role_starts([]) == {}
    assert str(SID_A) not in role_starts([*snaps, snap(date(2026, 10, 20))])


def test_a_role_snapshot_instant_is_read_in_market_time():
    late = RosterSnapshot(
        utc(2026, 10, 21, 3, 0), [{"security_id": str(SID_A), "context_role": "payer"}]
    )
    assert role_starts([late])[str(SID_A)][1] == date(2026, 10, 20)  # 23:00 ET on Oct 20


def test_role_rows_are_dated_only_when_the_run_carries_the_companys_current_role():
    companies = roster_companies(
        [
            member("AAA", SID_A, role="payer"),
            member("BBB", SID_B, role="financier"),
            member("CCC", SID_C),  # no role: no row
            member("ZZZ", None, role="benchmark"),  # no resolved security: undated, still a row
        ]
    )
    rows = role_rows(
        companies,
        {str(SID_A): ("payer", date(2026, 10, 1)), str(SID_B): ("payer", date(2026, 9, 1))},
    )
    got = {r.ticker: r for r in rows}
    assert set(got) == {"AAA", "BBB", "ZZZ"}
    assert got["AAA"].date == date(2026, 10, 1) and got["AAA"].on == date(2026, 10, 1)
    assert got["AAA"].detail == ("First carried by the roster snapshot of 2026-10-01",)
    assert got["AAA"].role == "payer" and got["AAA"].label == "AAA is tagged payer on this card"
    # BBB's run is for a different role: not its date
    assert got["BBB"].date is None and got["BBB"].detail == ("No roster snapshot dates this role",)
    assert got["ZZZ"].date is None and got["ZZZ"].security_id is None


def test_companies_are_once_each_with_their_role_and_silent_label_and_the_unresolved_are_counted():
    base = ledger_base(
        thesis_id=T1,
        thesis_name="Grid",
        memberships=[
            member("AAA", SID_A, "Core"),
            member("AAA", SID_A, "Cables", role="payer"),  # a second row of one company
            member("BBB", SID_B, CONTEXT_LABEL, silent=True),
            member("CCC", SID_C, BENCH_LABEL, silent=True),
            member("ZZZ", None, "Core"),
            member("ZZZ", None, "Cables"),
        ],
        night=None,
        tickers=TICKERS,
        ciks=CIKS,
        rows_by_sid={},
        snapshots=[],
        roster_fallback=True,
    )
    assert [(c.ticker, c.role, c.silent) for c in base.companies] == [
        ("AAA", "payer", None),
        ("BBB", None, "Context"),
        ("CCC", None, "Bench"),
        ("ZZZ", None, None),
    ]
    assert (base.basket_rows, base.silent_rows, base.unresolved, base.roster_fallback) == (
        6,
        2,
        ("ZZZ",),
        True,
    )
    assert base.night is None and not base.night_unreadable


def test_a_fact_of_a_silent_company_carries_its_tag():
    (r,) = rows_from_ratified([fact()], [], [], W)
    (row,) = fact_rows({SID_A: [r]}, {SID_A: "Bench"})
    assert row.silent == "Bench" and row.ticker == "AAA"


# ---------------------------------------------------------------------------------------------------------
# the tallies, on hand-built rows
# ---------------------------------------------------------------------------------------------------------


def test_tallies_count_every_row_by_class_and_kind_and_say_how_the_signals_trace():
    d = date(2026, 10, 1)
    rows = [
        LedgerRow("system", "trigger", "t1", date=d, sources=(Source("form4", ACC, None),)),
        LedgerRow("system", "trigger", "t2", date=d, sources=(Source("price", "price:x:y", None),)),
        LedgerRow(
            "system", "risk_signal", "r1", date=d, sources=(Source("catalyst", "DOE-77", None),)
        ),
        LedgerRow("system", "risk_signal", "r2", date=d),
        LedgerRow("operator", "ratified", "f1", date=d),
        LedgerRow("automatic", "ratified", "f2", date=d),
        LedgerRow("automatic", "catalyst_fact", "f3", date=d),
        LedgerRow("operator", "catalyst_fact", "f4", date=d),
        LedgerRow("card", "card_catalyst", "c1", date=d),
        LedgerRow("card", "card_catalyst", "c2"),
        LedgerRow("card", "kill", "k1"),
        LedgerRow("card", "evidence", "e1"),
        LedgerRow("operator", "role", "o1", date=d),
        LedgerRow("operator", "role", "o2"),
    ]
    t = tallies_of(rows)
    assert t.rows == 14
    assert [(c.key, c.rows) for c in t.by_authority] == [
        ("operator", 4),
        ("system", 4),
        ("automatic", 2),
        ("card", 4),
    ]
    assert {c.key: c.rows for c in t.by_kind} == {
        "trigger": 2,
        "risk_signal": 2,
        "ratified": 2,
        "catalyst_fact": 2,
        "card_catalyst": 2,
        "kill": 1,
        "evidence": 1,
        "role": 2,
    }
    assert (t.signals, t.trace_accession, t.trace_price, t.trace_other, t.trace_none) == (
        4,
        1,
        1,
        1,
        1,
    )
    assert (t.facts_ratified, t.facts_stored) == (2, 2)
    assert (t.catalysts_dated, t.catalysts_undated, t.roles_dated, t.roles_undated) == (1, 1, 1, 1)
    assert sum(c.rows for c in t.by_authority) == sum(c.rows for c in t.by_kind) == t.rows


def test_tallies_of_nothing_are_zeros_for_every_class_and_kind():
    t = tallies_of([])
    assert t.rows == 0 and [c.key for c in t.by_authority] == list(AUTHORITY_ORDER)
    assert [c.key for c in t.by_kind] == list(KIND_ORDER) and not any(c.rows for c in t.by_kind)


def test_a_name_with_no_row_is_still_listed_with_a_zero():
    c = card(triggers=[trig(SID_A, "buy", sources=[prov("form4", ACC)])])
    base = ledger_base(
        thesis_id=T1,
        thesis_name="Grid",
        memberships=[member("AAA", SID_A), member("BBB", SID_B), member("ZZZ", None)],
        night=night(c),
        tickers=TICKERS,
        ciks=CIKS,
        rows_by_sid={},
        snapshots=[],
    )
    page = evidence_ledger(base, CardEntries(), asof=ASOF)
    assert [(n.company.ticker, n.rows) for n in page.names] == [("AAA", 1), ("BBB", 0), ("ZZZ", 0)]


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy reads stubbed)
# ---------------------------------------------------------------------------------------------------------


def stub_base() -> router_mod.LedgerBase:
    c = card(
        triggers=[trig(SID_A, "3 insiders bought", sources=[prov("form4", ACC, shares=4000)])],
        asof=date(2026, 10, 27),
    )
    (op,) = rows_from_ratified([fact()], [], [], W)
    return ledger_base(
        thesis_id=T1,
        thesis_name="Grid",
        memberships=[
            member("AAA", SID_A, "Core", role="payer"),
            member("BBB", SID_B, CONTEXT_LABEL, silent=True),
            member("ZZZ", None, "Core"),
        ],
        night=night(c, reconstructed=True),
        tickers=TICKERS,
        ciks=CIKS,
        rows_by_sid={SID_A: [op]},
        snapshots=[snap(date(2026, 10, 1), (SID_A, "payer"))],
        roster_fallback=True,
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {
        "reads": 0,
        "card_reads": 0,
        "live": CardEntries(kills=(KillCriterion(id=uuid.uuid4(), text="first"),)),
    }
    other = uuid.uuid4()

    def fake_cards(conn, asof):
        seen["card_reads"] += 1
        return CardsRead(
            live_cards=3,
            not_yet_created=1,
            cards=(CardChoice(T1, "Grid", 2), CardChoice(other, "Batteries", 1)),
        )

    def fake_read(conn, thesis_id, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (thesis_id, asof, known_at, clock)
        return stub_base()

    def fake_get(conn, thesis_id):
        seen["live_reads"] = seen.get("live_reads", 0) + 1
        e = seen["live"]
        return SimpleNamespace(catalysts=e.catalysts, kill_criteria=e.kills, evidence=e.evidence)

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_cards", fake_cards)
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(router_mod.thesis_repo, "get", fake_get)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_one_cached_heavy_read_on_the_requests_view_and_reads_the_entries_live(
    stub_client,
):
    client, seen = stub_client
    url = f"/dashboards/evidence-ledger/{T1}"
    first = client.get(url, params={"asof": "2026-10-28"}).json()
    assert first["cache"] == "computed" and first["clock"] == "public"
    thesis_id, asof, known_at, clock = seen["view"]
    assert thesis_id == T1 and asof == ASOF and clock == "public" and known_at is not None
    assert [r["label"] for r in first["standing"] if r["kind"] == "kill"] == ["first"]

    # the card's own entries are NOT in the cache: an edit shows on the next request, the heavy read is a hit
    seen["live"] = CardEntries(kills=(KillCriterion(id=uuid.uuid4(), text="second"),))
    again = client.get(url, params={"asof": "2026-10-28"}).json()
    assert again["cache"] == "hit" and seen["reads"] == 1 and seen["card_reads"] == 1
    assert [r["label"] for r in again["standing"] if r["kind"] == "kill"] == ["second"]

    client.get(url, params={"asof": market_today().isoformat()})
    assert seen["view"][2:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(f"/dashboards/evidence-ledger/{T1}", params={"asof": "2026-10-28"}).json()
    assert (body["thesis_name"], body["roster_fallback"], body["unresolved"]) == (
        "Grid",
        True,
        ["ZZZ"],
    )
    assert (body["basket_rows"], body["silent_rows"], body["facts_days"]) == (3, 1, 365)
    assert body["night"] == {"asof": "2026-10-27", "reconstructed": True}
    assert body["night_unreadable"] is False
    dated = {r["kind"]: r for g in body["groups"] for r in g["rows"]}
    trigger, fact_row = dated["trigger"], dated["ratified"]
    assert (trigger["authority"], trigger["ticker"], trigger["form"], trigger["date"]) == (
        "system",
        "AAA",
        "insider",
        "2026-10-20",
    )
    assert trigger["reconstructed"] is True and trigger["edgar"] is True
    assert trigger["sources"][0] == {
        "source": "form4",
        "ref": ACC,
        "url": filing_index_url(CIK, ACC),
        "detail": ["shares: 4000"],
    }
    assert (fact_row["authority"], fact_row["by"], fact_row["on"], fact_row["date"]) == (
        "operator",
        "operator",
        "2026-10-20",
        "2026-10-20",
    )
    role = dated["role"]
    assert (role["authority"], role["role"], role["date"]) == ("operator", "payer", "2026-10-01")
    assert [(n["ticker"], n["rows"], n["silent"], n["role"]) for n in body["names"]] == [
        ("AAA", 3, None, "payer"),
        ("BBB", 0, "Context", None),
        ("ZZZ", 0, None, None),
    ]
    t = body["tallies"]
    assert t["rows"] == 4 and [(c["key"], c["rows"]) for c in t["by_authority"]] == [
        ("operator", 2),
        ("system", 1),
        ("automatic", 0),
        ("card", 1),
    ]
    assert (t["signals"], t["trace_accession"], t["facts_ratified"], t["roles_dated"]) == (
        1,
        1,
        1,
        1,
    )
    assert body["standing"][0]["kind"] == "kill"


def test_the_router_404s_a_card_not_counted_at_the_asof(stub_client):
    client, seen = stub_client
    r = client.get(f"/dashboards/evidence-ledger/{uuid.uuid4()}", params={"asof": "2026-10-28"})
    assert r.status_code == 404 and seen["reads"] == 0


def test_the_picker_list_is_cached(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/evidence-ledger/cards", params={"asof": "2026-10-28"}).json()
    assert [c["name"] for c in body["cards"]] == [
        "Grid",
        "Batteries",
    ]  # the stub's own order, as read
    assert (body["live_cards"], body["not_yet_created"], body["cache"]) == (3, 1, "computed")
    again = client.get("/dashboards/evidence-ledger/cards", params={"asof": "2026-10-28"}).json()
    assert again["cache"] == "hit" and seen["card_reads"] == 1


def test_a_card_that_is_a_picker_row_but_vanished_is_a_404_not_a_500(stub_client, monkeypatch):
    client, _ = stub_client
    monkeypatch.setattr(router_mod.thesis_repo, "get", lambda conn, tid: None)
    r = client.get(f"/dashboards/evidence-ledger/{T1}", params={"asof": "2026-10-28"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real reads, no lookahead (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a past as-of
LATER = date(2026, 6, 12)  # a later past as-of, after every "late" row below
EARLY_F4, LATE_F4, NIGHT_F4 = "0000876543-26-000111", "0000876543-26-000112", "0000876543-26-000113"
EARLY_10Q, LATE_10Q, STORED_10Q = (
    "0000876543-26-000121",
    "0000876543-26-000122",
    "0000876543-26-000123",
)


def _security(db, ticker: str, cik: str | None) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, 'equity', %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    return sid


def _insert_call(db, tid: UUID, c: CallCard, *, reconstructed: bool = False) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, reconstructed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                DEFAULT_TENANT_ID,
                tid,
                c.asof,
                c.state.value,
                c.verdict.value,
                Json(c.model_dump(mode="json")),
                reconstructed,
            ),
        )


def _seed_card(db) -> tuple[UUID, UUID]:
    sid = _security(db, "LDA", CIK)
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Ledger fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(
                ticker="LDA",
                role="the name",
                security_id=sid,
                segment="Core",
                context_role=ContextRole.PAYER,
            )
        ],
        evidence=[Evidence(id=uuid.uuid4(), kind="8-K", label="award", ref="https://x.test/a")],
    )
    thesis_repo.upsert(db, thesis)
    thesis_repo.set_catalysts(
        db,
        thesis.id,
        [Catalyst(id=uuid.uuid4(), label="vote", when_date=date(2026, 7, 1))],
        tenant_id=DEFAULT_TENANT_ID,
    )
    thesis_repo.set_kill_criteria(
        db,
        thesis.id,
        [KillCriterion(id=uuid.uuid4(), text="loses the contract")],
        tenant_id=DEFAULT_TENANT_ID,
    )
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()
    return thesis.id, sid


def _get(db, thesis_id: UUID, *asofs: date) -> list[dict]:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        out = []
        for asof in asofs:
            r = client.get(
                f"/dashboards/evidence-ledger/{thesis_id}", params={"asof": asof.isoformat()}
            )
            assert r.status_code == 200, r.text
            out.append(r.json())
        return out
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()


def _rows(body: dict, kind: str) -> list[dict]:
    return [r for g in body["groups"] for r in g["rows"] if r["kind"] == kind] + [
        r for r in body["standing"] if r["kind"] == kind
    ]


def _labels(body: dict, kind: str) -> list[str]:
    return sorted(r["label"] for r in _rows(body, kind))


def test_a_call_recorded_after_a_past_asof_is_not_the_card_in_effect(db, pinned_today):
    """Three nights in the log: an early one, a later one with TWO rows (the higher seq stands) and a
    reconstructed one still later. The card in effect at a past as-of is the last row on or before it, so the
    later nights' triggers never reach an earlier view, the whole card is replaced (never merged) and a
    reconstructed night is marked. The live as-of reads the newest row."""
    thesis_id, sid = _seed_card(db)

    def night_card(asof: date, label: str, acc: str, on: date) -> CallCard:
        return card(
            triggers=[trig(sid, label, on=on, sources=[prov("form4", acc, shares=1000)])],
            asof=asof,
            tid=thesis_id,
        )

    _insert_call(
        db, thesis_id, night_card(date(2026, 6, 3), "early buy", EARLY_F4, date(2026, 6, 2))
    )
    _insert_call(
        db,
        thesis_id,
        night_card(date(2026, 6, 10), "first row of the night", LATE_F4, date(2026, 6, 9)),
    )
    _insert_call(
        db, thesis_id, night_card(date(2026, 6, 10), "later buy", LATE_F4, date(2026, 6, 9))
    )
    _insert_call(
        db,
        thesis_id,
        night_card(date(2026, 6, 11), "rebuilt buy", NIGHT_F4, date(2026, 6, 10)),
        reconstructed=True,
    )
    db.commit()

    before_any, past, mid, later, live = _get(
        db, thesis_id, date(2026, 6, 2), PAST, date(2026, 6, 10), LATER, pinned_today
    )
    assert (past["clock"], later["clock"], live["clock"]) == ("public", "public", "record")

    # nothing recorded by the first as-of: no night, no trigger rows, and the page says so (never an error)
    assert before_any["night"] is None and _rows(before_any, "trigger") == []
    assert before_any["night_unreadable"] is False

    assert past["night"] == {"asof": "2026-06-03", "reconstructed": False}
    assert _labels(past, "trigger") == ["early buy"]
    # the night with two rows: the highest seq is the card in effect
    assert mid["night"]["asof"] == "2026-06-10" and _labels(mid, "trigger") == ["later buy"]
    # the reconstructed night replaces the earlier card whole, and its rows say so
    assert later["night"] == {"asof": "2026-06-11", "reconstructed": True}
    (rebuilt,) = _rows(later, "trigger")
    assert (rebuilt["label"], rebuilt["reconstructed"]) == ("rebuilt buy", True)
    assert "From a reconstructed night: reported, never scored" in rebuilt["detail"]
    assert [r["label"] for r in _rows(live, "trigger")] == ["rebuilt buy"]

    # the trigger's source: the accession links to EDGAR through the issuer's CIK
    (early,) = _rows(past, "trigger")
    assert early["ticker"] == "LDA" and early["authority"] == "system" and early["edgar"] is True
    assert early["sources"][0]["url"] == filing_index_url(CIK, EARLY_F4)
    assert early["sources"][0]["detail"] == ["shares: 1000"]
    assert past["tallies"]["trace_accession"] == 1 and past["tallies"]["signals"] == 1

    # the card's own entries load live whatever the as-of, marked as the card's
    for body in (past, later):
        assert {
            r["label"]: r["authority"] for r in _rows(body, "kill") + _rows(body, "card_catalyst")
        } == {
            "loses the contract": "card",
            "vote": "card",
        }


def test_a_row_the_log_holds_but_the_model_cannot_read_is_said_not_raised(db):
    thesis_id, _ = _seed_card(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card) "
            "VALUES (%s, %s, %s, 'armed', 'core_entry', %s)",
            (DEFAULT_TENANT_ID, thesis_id, date(2026, 6, 3), Json({"unexpected": 1})),
        )
    db.commit()
    (body,) = _get(db, thesis_id, PAST)
    assert body["night"] is None and body["night_unreadable"] is True
    assert _rows(body, "trigger") == []


def test_a_fact_ratified_after_a_past_asof_is_not_a_row(db, pinned_today):
    """Two facts a person ratified (one by the early as-of, one after it) and a stored fact nobody ratified. The
    public clock counts a ratification from the instant it was made: the late one is not a row at the early
    as-of, appears at the later one, and the stored fact is `automatic` whenever it shows. All three were
    recorded BEFORE today, so the live record clock reads every one."""
    thesis_id, sid = _seed_card(db)
    for acc, ratified_at, ratified_by, cover in (
        (EARLY_10Q, utc(2026, 6, 3, 21, 0), "operator", date(2026, 3, 31)),
        (LATE_10Q, utc(2026, 6, 10, 21, 0), "operator", date(2026, 3, 31)),
        (STORED_10Q, utc(2026, 5, 25, 14, 0), "auto", date(2026, 5, 20)),
    ):
        ingest_cash_burn(
            db,
            sid,
            cash_usd=40_000_000,
            quarterly_burn_usd=8_000_000,
            source="10-q-cover" if ratified_by == "auto" else "ratified",
            source_ref=acc,
            event_date=cover,
            ratified_by=ratified_by,
            recorded_at=ratified_at,
        )
    db.commit()

    past, later, live = _get(db, thesis_id, PAST, LATER, pinned_today)

    def facts(body: dict) -> dict[str, tuple[str, str | None, str]]:
        return {
            r["sources"][0]["ref"]: (r["authority"], r["by"], r["date"])
            for r in _rows(body, "ratified")
        }

    assert facts(past) == {
        EARLY_10Q: ("operator", "operator", "2026-06-03"),
        STORED_10Q: ("automatic", None, "2026-05-20"),
    }
    assert facts(later) == {
        EARLY_10Q: ("operator", "operator", "2026-06-03"),
        LATE_10Q: ("operator", "operator", "2026-06-10"),
        STORED_10Q: ("automatic", None, "2026-05-20"),
    }
    assert set(facts(live)) == {EARLY_10Q, LATE_10Q, STORED_10Q}
    # nothing is dated after its own as-of
    for body, asof in ((past, PAST), (later, LATER)):
        assert all(r["date"] <= asof.isoformat() for r in _rows(body, "ratified"))
    # a ratified fact names who and when; the source link is the filing index through the issuer's CIK
    early = next(r for r in _rows(past, "ratified") if r["sources"][0]["ref"] == EARLY_10Q)
    assert (early["form"], early["on"], early["edgar"]) == ("ratified fact", "2026-06-03", True)
    assert early["sources"][0]["url"] == filing_index_url(CIK, EARLY_10Q)
    stored = next(r for r in _rows(past, "ratified") if r["sources"][0]["ref"] == STORED_10Q)
    assert (stored["form"], stored["by"], stored["on"]) == ("stored fact", None, None)
    assert past["tallies"]["facts_ratified"] == 1 and past["tallies"]["facts_stored"] == 1
    assert later["tallies"]["facts_ratified"] == 2


def test_a_roster_snapshot_taken_after_the_asof_never_dates_a_role(db, pinned_today):
    """The roster history: payer from June 3; counter-case from June 10. A past as-of reads the roster the latest
    snapshot by then held and dates the role by the run that carries it; a snapshot taken after the as-of is
    never read, and a role that changed starts its run again."""
    thesis_id, sid = _seed_card(db)

    def roster(role: ContextRole) -> list[dict]:
        m = BasketMember(
            ticker="LDA", role="the name", security_id=sid, segment="Core", context_role=role
        )
        return [_member_to_snapshot(0, m)]

    with db.cursor() as cur:
        cur.execute("DELETE FROM basket_snapshot WHERE thesis_id = %s", (thesis_id,))
        for taken_at, role in (
            (utc(2026, 6, 3, 15, 0), ContextRole.PAYER),
            (
                utc(2026, 6, 4, 15, 0),
                ContextRole.PAYER,
            ),  # an unchanged roster does not restart the run
            (utc(2026, 6, 10, 15, 0), ContextRole.COUNTER_CASE),
        ):
            members = roster(role)
            cur.execute(
                "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
                "VALUES (%s, %s, %s, %s, %s, md5(%s::text))",
                (
                    uuid.uuid4(),
                    DEFAULT_TENANT_ID,
                    thesis_id,
                    taken_at,
                    Json(members),
                    Json(members),
                ),
            )
    db.commit()

    before, past, later = _get(db, thesis_id, date(2026, 6, 2), PAST, LATER)
    # before the first snapshot: today's roster stands in (said), and no snapshot dates the role
    assert before["roster_fallback"] is True
    (r0,) = _rows(before, "role")
    assert r0["date"] is None and r0["detail"] == ["No roster snapshot dates this role"]
    # the early as-of: the payer run began June 3; the June 10 snapshot is not read
    assert past["roster_fallback"] is False
    (r1,) = _rows(past, "role")
    assert (r1["role"], r1["date"], r1["by"]) == ("payer", "2026-06-03", "operator")
    # the later as-of: the roster is the June 10 one, and the new role's run starts there
    (r2,) = _rows(later, "role")
    assert (r2["role"], r2["date"]) == ("counter_case", "2026-06-10")
    assert past["tallies"]["roles_dated"] == 1 and before["tallies"]["roles_undated"] == 1
