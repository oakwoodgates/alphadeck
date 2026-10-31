"""Insider Buying (``docs/dashboards/insider-tracker.md``): the pure compute on hand-built Form 4 rows run through the
shared reader (a filing with a purchase is a row and a sales-only filing is not; what the page reads back off the
reader's words, pinned by a round trip so a change to the reader's wording fails loud; the filer and the role; the
role classes; the filing date and the "filed N days after the trade" note; the tags carried unchanged; the cluster
rule with its 30-day edge, the repeat buyer and the lead-in before the window; the trigger match by accession on the
recorded cards; the since span and IWM; a flagged name keeping its row; Context and Bench tagged; the counts; the
order; no aggregate), the router off one cached read, and DB-backed reads through the real point-in-time view: a
filing filed after a past as-of is never a row, a bar printed after it is never read, a night recorded after it is
never read, and Context and Bench names appear only behind the toggle.
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import insider_tracker as router_mod
from dashboards import filings as filings_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.filings import (
    ALL_KINDS,
    FORM4_KINDS,
    FilingRow,
    Who,
    fmt_price,
    fmt_shares,
    fmt_usd,
    merge_4a,
    rows_from_amendments,
    rows_from_insider_txns,
)
from dashboards.insider_tracker import (
    CLUSTER_WINDOW_DAYS,
    DEFAULT_WINDOW,
    WINDOWS,
    Counts,
    InsiderTracker,
    PurchaseRow,
    TrackerInputs,
    accession_key,
    buyer_key,
    cap_of,
    clusters,
    insider_tracker,
    purchase_of,
    purchase_security_ids,
    read_start,
    role_classes,
    trigger_hits,
    window_start,
)
from dashboards.leaders import CLUSTER_MIN_BUYERS
from dashboards.peer_table import CapCell
from dashboards.trigger_log import CardNights, Night, Scope
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Kind, State, Verdict
from domain.insider_internal_transfers import INTERNAL_TRANSFER
from domain.market_cap import CapTier
from domain.market_time import known_at_for_asof, market_today
from domain.signal import Provenance
from domain.thesis import BasketMember, Segment, Thesis
from domain.trading_days import is_scheduled_day
from ingest.edgar.filing_index import ingest_filing_index
from repositories import thesis_repo
from signals.base import PointInTimeData

UTC = timezone.utc
T = date(2026, 10, 28)  # the as-of (a Wednesday)
A, B, C = (uuid.UUID(int=n) for n in (0xA1, 0xB2, 0xC3))
TID = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
TID2 = uuid.UUID("00000000-0000-0000-0000-0000000000e2")
CIK = "0000876543"
CARD = "Card A"
MIXED = "mixed-scale price history"
W = Who(A, "AAA", CIK, "Alpha Corp")
WB = Who(B, "BBB", CIK, "Beta Corp")


def utc(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


def acc(n: int) -> str:
    return f"0000876543-26-{n:06d}"


# ---------------------------------------------------------------------------------------------------------
# builders: Form 4 rows through the REAL reader (the page never builds a FilingRow by hand)
# ---------------------------------------------------------------------------------------------------------


def txn(code="P", shares=1000, price=4.0, usd=None, **over):
    base = {
        "insider_name": "Jane Roe",
        "insider_role": "CEO",
        "txn_code": code,
        "shares": shares,
        "price": price,
        "usd": (
            usd if usd is not None else (shares * price if None not in (shares, price) else None)
        ),
        "accession": acc(1),
        "valid_from": d(1),  # the TRADE date
        "accepted": utc(2026, 10, 2, 21, 0),  # 17:00 ET: filed Oct 2
        "recorded_at": utc(2026, 10, 3),
        "issuer_cik": CIK,
        "issuer_name": "Alpha Corp",
        "rpt_owner_cik": "0001111111",
        "aff_10b5_1": None,
        "direct_indirect": None,
        "shares_owned_after": None,
        "ownership_nature": None,
        "txn_footnotes": None,
        "filing_natures": None,
    }
    return {**base, **over}


def form4(rows: list[dict], who: Who = W) -> FilingRow:
    (r,) = rows_from_insider_txns(rows, who)
    return r


def buy(
    n: int,
    filed: date,
    name: str = "Jane Roe",
    role: str | None = "CEO",
    shares: float = 1000,
    price: float = 4.0,
    traded: date | None = None,
    who: Who = W,
    **over,
) -> FilingRow:
    """One Form 4 holding one code-P row, accepted at 17:00 ET on ``filed``."""
    return form4(
        [
            txn(
                "P",
                shares,
                price,
                insider_name=name,
                insider_role=role,
                accession=acc(n),
                valid_from=traded or filed - timedelta(days=1),
                accepted=datetime.combine(filed, time(21, 0), tzinfo=UTC),
                **over,
            )
        ],
        who,
    )


def sale(n: int, filed: date, who: Who = W) -> FilingRow:
    return form4(
        [
            txn(
                "S",
                500,
                5.0,
                accession=acc(n),
                valid_from=filed - timedelta(days=1),
                accepted=datetime.combine(filed, time(21, 0), tzinfo=UTC),
            )
        ],
        who,
    )


def member(sid: UUID, ticker: str, tid: UUID = TID, name: str = CARD, segment="Core", silent=False):
    return Membership(tid, name, ticker, sid, segment, silent)


def trig(
    sid: UUID, accession: str | None, kind: Kind = Kind.INSIDER, on: date = d(1)
) -> TriggerRef:
    return TriggerRef(
        label="open-market buy",
        kind=kind,
        security_id=sid,
        event_date=on,
        sources=[Provenance(source="form4", ref=accession)] if accession else [],
    )


def cc(
    tid: UUID,
    on: date,
    triggers: tuple[TriggerRef, ...] = (),
    risks: tuple[TriggerRef, ...] = (),
    armed: tuple[MemberCall, ...] = (),
) -> CallCard:
    return CallCard(
        thesis_id=tid,
        asof=on,
        state=State.WARMING,
        verdict=Verdict.NOT_YET,
        expression="",
        key_conviction=KeyState(turned=False, label="Conviction"),
        key_confirmation=KeyState(turned=False, label="Confirmation"),
        triggers_fired=list(triggers),
        risk_signals=list(risks),
        armed_members=list(armed),
    )


def night(card: CallCard, seq: int = 1) -> Night:
    return Night(card.asof, seq, False, card)


def tracker(
    *filings: FilingRow,
    asof: date = T,
    window_days: int = 30,
    memberships: list[Membership] | None = None,
    nights: list[CardNights] | None = None,
    closes: dict | None = None,
    bench: dict | None = None,
    flagged: dict | None = None,
    caps: dict | None = None,
    include_silent: bool = False,
) -> InsiderTracker:
    by: dict[UUID, list[FilingRow]] = {}
    for f in filings:
        by.setdefault(f.security_id, []).append(f)
    mems = memberships if memberships is not None else [member(A, "AAA"), member(B, "BBB")]
    for m in mems:
        by.setdefault(m.security_id, [])
    return insider_tracker(
        TrackerInputs(
            asof=asof,
            window_days=window_days,
            include_silent=include_silent,
            scope=Scope(2, 0, 0),
            basket_rows=len(mems),
            silent_rows_left_out=0,
            unresolved_rows=0,
            memberships=mems,
            filings=by,
            nights=nights if nights is not None else [CardNights(TID, CARD, None, ())],
            closes=closes or {},
            bench=bench or {},
            flagged=flagged or {},
            caps=caps or {},
        )
    )


def only(t: InsiderTracker, n: int) -> PurchaseRow:
    return next(r for r in t.rows if r.accession == acc(n))


# ---------------------------------------------------------------------------------------------------------
# 1. a row is a Form 4 holding a purchase
# ---------------------------------------------------------------------------------------------------------


def test_a_filing_with_a_purchase_is_a_row_and_a_sales_only_filing_is_not():
    t = tracker(buy(1, d(20)), sale(2, d(21)), buy(3, d(22), name="Sam Poe", who=W))
    assert [r.accession for r in t.rows] == [
        acc(3),
        acc(1),
    ]  # the sale is no row, the buys newest first
    c = t.counts
    assert (c.filings_read, c.purchases, c.routine_folded, c.amendments_no_purchase) == (3, 2, 1, 0)


def test_a_filing_that_holds_a_purchase_and_a_sale_is_a_row_and_its_dollars_are_the_purchases():
    fr = form4([txn("P", 7000, 4.0), txn("P", 5000, 4.24), txn("S", 3000, 5.0)])
    assert fr.usd == pytest.approx(34_200.0)  # the reader's net: purchases minus sales
    row = tracker(fr).rows[0]
    assert row.usd == pytest.approx(
        49_200.0
    )  # the page lists the PURCHASES, never netted against sales
    assert row.shares == 12_000


def test_the_reader_folds_a_purchase_free_form4_as_routine_and_the_page_counts_it():
    sales = sale(1, d(20))
    assert sales.routine is True and purchase_of(sales) is None
    other = [txn("A", 100, 0.0, accession=acc(2)), txn("G", 50, 0.0, accession=acc(3))]
    for r in other:
        fr = form4([r])
        assert purchase_of(fr) is None  # a grant, a gift: not a purchase here
    assert tracker(sales).counts.routine_folded == 1


def test_only_form4_rows_can_be_a_purchase():
    from dashboards.filings import FilingRow as FR

    not_form4 = FR(security_id=A, ticker="AAA", kind="8k", form="8-K", date=d(20), headline="8-K")
    assert purchase_of(not_form4) is None


# ---------------------------------------------------------------------------------------------------------
# 2. what the page reads back off the reader's words (pinned: a reader change fails loud)
# ---------------------------------------------------------------------------------------------------------


def test_the_round_trip_from_the_readers_words_gives_the_filer_role_shares_price_dollars_and_trade_date():
    fr = form4([txn("P", 7000, 4.00, valid_from=d(1)), txn("P", 5000, 4.24, valid_from=d(2))])
    p = purchase_of(fr)
    assert p is not None
    assert (p.filer, p.role, p.co_filers) == ("Jane Roe", "CEO", 0)
    assert p.trade_date == d(1)  # the earliest purchase's trade date
    assert p.shares == 12_000
    assert p.price == pytest.approx(4.10)  # the reader's own shares-weighted mean
    assert p.usd == pytest.approx(49_200.0)
    # and the figures are the very ones the reader printed in its headline
    assert fmt_shares(p.shares) in fr.headline and fmt_price(p.price) in fr.headline
    assert fmt_usd(p.usd) in fr.headline


def test_the_round_trip_holds_for_fractional_shares_a_sub_dollar_price_and_a_thousands_separator():
    fr = form4([txn("P", 1234567.25, 0.5, usd=617_283.63)])
    p = purchase_of(fr)
    assert p is not None
    assert p.shares == pytest.approx(1_234_567.25)
    assert p.price == pytest.approx(0.5)
    assert p.usd == pytest.approx(617_284.0, abs=1.0)  # the reader prints whole dollars


def test_a_row_that_states_no_price_or_dollars_keeps_its_place_with_blanks():
    fr = form4([txn("P", 1000, None, usd=None)])
    p = purchase_of(fr)
    assert p is not None and p.shares == 1000
    assert p.price is None and p.usd is None  # never filled in
    fr = form4([txn("P", None, None, usd=None)])
    p = purchase_of(fr)
    assert p is not None and (p.shares, p.price, p.usd) == (None, None, None)


def test_a_zero_share_row_is_a_purchase_with_no_price_weight():
    fr = form4([txn("P", 0, 4.0, usd=0, insider_name="Zed Zero", insider_role="Director")])
    p = purchase_of(fr)
    assert p is not None and p.filer == "Zed Zero" and p.shares == 0
    assert p.price is None  # no weight: no mean to state
    assert p.usd == 0.0


def test_the_trade_date_is_blank_when_the_row_states_none():
    p = purchase_of(form4([txn("P", valid_from=None)]))
    assert p is not None and p.trade_date is None


@pytest.mark.parametrize(
    "name,role,filer,got_role,co",
    [
        ("Jane Roe", "CEO", "Jane Roe", "CEO", 0),
        ("Jane Roe", None, "Jane Roe", None, 0),
        ("Jane Roe", "Director, 10% owner", "Jane Roe", "Director, 10% owner", 0),
        (
            "Pat Quill",
            "Chief Financial Officer (Principal Financial Officer)",
            "Pat Quill",
            "Chief Financial Officer (Principal Financial Officer)",
            0,
        ),
        (None, "CEO", None, "CEO", 0),  # the reader's "reporting owner not named"
        # a name of its own parenthetical keeps it: the role is the LAST balanced group
        ("A & B Capital (GP) LP", "10% owner", "A & B Capital (GP) LP", "10% owner", 0),
        ("Lee: Kim", "VP (Sales)", "Lee: Kim", "VP (Sales)", 0),
        ("  Padded  Name ", "CFO", "Padded  Name", "CFO", 0),
    ],
)
def test_the_filer_and_the_role_come_from_the_headline(name, role, filer, got_role, co):
    p = purchase_of(form4([txn("P", insider_name=name, insider_role=role)]))
    assert p is not None
    assert (p.filer, p.role, p.co_filers) == (filer, got_role, co)


def test_a_filing_with_several_owners_names_the_first_and_counts_the_rest():
    fr = form4(
        [
            txn("P", 1000, 4.0, insider_name="Jane Roe", insider_role="Director"),
            txn("P", 500, 4.0, insider_name="Roe Family Trust", insider_role="10% owner"),
            txn("P", 100, 4.0, insider_name="Roe Holdings LLC", insider_role="10% owner"),
        ]
    )
    p = purchase_of(fr)
    assert p is not None
    assert (p.filer, p.role, p.co_filers) == ("Jane Roe", "Director", 2)
    assert p.shares == 1600
    assert "and 2 more" in fr.headline


def test_an_applied_4a_is_a_row_when_its_rows_hold_a_purchase_and_names_its_owners():
    """The 4/A record row (the filing as filed) leads; the rows an applied 4/A put on the tape fold into its detail,
    code-P lines among them. The record's headline names the owners and states no role."""
    tape = rows_from_insider_txns(
        [
            txn(
                "P",
                3000,
                2.0,
                accession=acc(9),
                amends_accession=acc(8),
                valid_from=d(10),
                accepted=utc(2026, 10, 14, 21, 0),
            )
        ],
        W,
    )
    assert [r.kind for r in tape] == ["form4a"]
    record = rows_from_amendments(
        [
            {
                "accession": acc(9),
                "day": d(14),
                "filed": d(14),
                "owners": [{"cik": "0001111111", "name": "Jane Roe"}],
                "date_of_original_submission": d(12),
                "remarks": None,
                "rows": [{"txn_code": "P"}],
                "issuer_cik": CIK,
            }
        ],
        W,
    )
    (merged,) = merge_4a(tape, record)
    assert merged.kind == "form4a" and merged.routine is False
    p = purchase_of(merged)
    assert p is not None
    assert (p.filer, p.role, p.shares, p.price, p.usd) == ("Jane Roe", None, 3000, 2.0, 6000.0)
    assert p.trade_date == d(10)
    t = tracker(merged)
    row = t.rows[0]
    assert (row.form, row.filed, row.filer) == ("4/A", d(14), "Jane Roe")
    assert "applied 4/A" in row.tags


def test_a_4a_record_with_no_applied_purchase_row_is_counted_not_listed():
    record = rows_from_amendments(
        [
            {
                "accession": acc(9),
                "day": d(14),
                "filed": d(14),
                "owners": [{"name": "Jane Roe"}],
                "date_of_original_submission": d(12),
                "remarks": "typo in the footnote",
                "rows": [{"txn_code": "P"}],
                "issuer_cik": CIK,
            }
        ],
        W,
    )
    assert purchase_of(record[0]) is None  # the record alone states no transaction line
    t = tracker(record[0], buy(1, d(15)))
    assert [r.accession for r in t.rows] == [acc(1)]
    assert (t.counts.filings_read, t.counts.purchases, t.counts.amendments_no_purchase) == (2, 1, 1)


def test_a_4a_tape_group_with_no_record_row_in_view_stays_a_row_of_its_own():
    (tape,) = rows_from_insider_txns(
        [
            txn(
                "P",
                accession=acc(9),
                amends_accession=acc(8),
                accepted=utc(2026, 10, 14, 21, 0),
                insider_name="Pat Quill",
                insider_role="Director",
            )
        ],
        W,
    )
    p = purchase_of(tape)
    assert p is not None and (p.filer, p.role) == ("Pat Quill", "Director")


# ---------------------------------------------------------------------------------------------------------
# 3. role classes, buyer identity, accession keys
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,want",
    [
        ("CEO", ("officer",)),
        ("Chief Executive Officer", ("officer",)),
        ("SVP, Sales", ("officer",)),
        ("EVP and General Counsel", ("officer",)),
        ("President", ("officer",)),
        ("Director", ("director",)),
        ("Chairman of the Board", ("director",)),
        ("10% owner", ("ten_percent_owner",)),
        ("Director, 10% owner", ("director", "ten_percent_owner")),
        ("Director, Officer, 10% owner", ("officer", "director", "ten_percent_owner")),
        ("Officer", ("officer",)),
        ("Owner", ("other",)),
        ("Trustee", ("other",)),
        ("", ("other",)),
        (None, ("other",)),
    ],
)
def test_the_role_words_class_into_officer_director_ten_percent_owner_or_other(role, want):
    assert role_classes(role) == want


def test_a_buyer_is_the_filers_name_casefolded_with_whitespace_collapsed():
    assert buyer_key("  Jane   ROE ") == buyer_key("jane roe") == "jane roe"
    assert buyer_key(None) is None and buyer_key("   ") is None


def test_an_accession_compares_without_its_dashes_or_spaces():
    assert accession_key("0000876543-26-000010") == accession_key(" 000087654326000010 ")
    assert accession_key(None) is None and accession_key("") is None


# ---------------------------------------------------------------------------------------------------------
# 4. dating: the FILING date, with the trade date beside it
# ---------------------------------------------------------------------------------------------------------


def test_a_row_is_dated_by_its_filing_not_its_trade_and_says_how_late():
    row = tracker(buy(1, d(20), traded=d(14))).rows[0]
    assert (row.filed, row.trade_date, row.filed_days_after) == (d(20), d(14), 6)
    assert row.week == d(19)  # the Monday of the filing's week


def test_a_filing_on_the_trade_day_or_with_no_trade_date_carries_no_note():
    assert tracker(buy(1, d(20), traded=d(20))).rows[0].filed_days_after is None
    nodate = form4([txn("P", valid_from=None, accession=acc(2), accepted=utc(2026, 10, 20, 21, 0))])
    row = tracker(nodate).rows[0]
    assert (row.trade_date, row.filed_days_after) == (None, None)
    # a trade dated after the filing (bad data) is shown plainly, never a negative lag
    later = buy(3, d(20), traded=d(22))
    assert tracker(later).rows[0].filed_days_after is None


def test_the_window_is_by_filing_date_inclusive_at_both_ends():
    start = window_start(T, 30)
    assert start == d(28, 9)
    t = tracker(
        buy(1, start - timedelta(days=1), name="One"),
        buy(2, start, name="Two"),
        buy(3, T, name="Three"),
    )
    assert [r.accession for r in t.rows] == [acc(3), acc(2)]
    assert (t.window_start, t.window_days) == (start, 30)
    assert read_start(T, 30) == start - timedelta(days=CLUSTER_WINDOW_DAYS)


# ---------------------------------------------------------------------------------------------------------
# 5. the tags are the reader's, carried unchanged
# ---------------------------------------------------------------------------------------------------------


def test_the_tags_the_reader_put_on_the_row_ride_along_unchanged():
    issuer_self = form4([txn("P", rpt_owner_cik=CIK, accession=acc(1))])
    plan = form4([txn("P", aff_10b5_1=True, accession=acc(2), accepted=utc(2026, 10, 5, 21, 0))])
    transfer = form4(
        [
            txn(
                "P",
                accession=acc(3),
                accepted=utc(2026, 10, 6, 21, 0),
                direct_indirect="I",
                shares_owned_after=5000.5,
                **{
                    INTERNAL_TRANSFER: {
                        "with": ("Roe Family Holdings LP",),
                        "evidence": (),
                        "co_report": False,
                    }
                },
            )
        ]
    )
    t = tracker(issuer_self, plan, transfer)
    assert only(t, 1).tags == issuer_self.tags == ("issuer self-filing",)
    assert only(t, 2).tags == plan.tags == ("10b5-1 plan",)
    assert only(t, 3).tags == transfer.tags == ("internal transfer",)
    # the words are the reader's own, untouched
    assert only(t, 1).headline == issuer_self.headline and only(t, 1).detail == issuer_self.detail
    assert only(t, 1).url == issuer_self.url
    # the trailing parts of the transaction line (indirect, owned after, the transfer) do not disturb the figures
    assert transfer.detail[0].endswith("internal transfer with Roe Family Holdings LP")
    assert (only(t, 3).shares, only(t, 3).price, only(t, 3).usd) == (1000, 4.0, 4000.0)


def test_a_row_with_no_tag_carries_none():
    assert tracker(buy(1, d(20))).rows[0].tags == ()


# ---------------------------------------------------------------------------------------------------------
# 6. the cluster
# ---------------------------------------------------------------------------------------------------------


def c_of(*buys: tuple[UUID, date, str | None]) -> list:
    return clusters([(sid, filed, buyer_key(n), n) for sid, filed, n in buys])


def test_the_cluster_rule_is_the_leaders_rule():
    assert CLUSTER_MIN_BUYERS == 2 and CLUSTER_WINDOW_DAYS == 30


def test_two_distinct_buyers_within_30_days_are_a_cluster_and_each_names_the_other():
    x, y = c_of((A, d(1, 9), "Ann"), (A, d(1), "Bob"))  # Sep 1 and Oct 1: exactly 30 days
    assert x is not None and y is not None
    assert (x.buyers, [o.name for o in x.others]) == (2, ["Bob"])
    assert (y.buyers, [o.name for o in y.others]) == (2, ["Ann"])
    assert x.others[0].filed == d(1)


def test_buyers_31_days_apart_are_not_a_cluster():
    assert c_of((A, d(1, 9), "Ann"), (A, d(2), "Bob")) == [None, None]  # Sep 1 and Oct 2


def test_the_same_person_twice_is_not_a_cluster_whatever_the_spelling():
    assert c_of((A, d(1), "Ann Lee"), (A, d(9), "ann  LEE")) == [None, None]


def test_a_third_buyer_counts_for_the_row_in_the_middle_only_within_30_days_of_it():
    ann, bob, cy = c_of((A, d(1, 9), "Ann"), (A, d(1), "Bob"), (A, d(2), "Cy"))
    assert (
        ann is not None and [o.name for o in ann.others] == ["Bob"] and ann.buyers == 2
    )  # Cy is 31 away
    assert bob is not None and bob.buyers == 3
    assert sorted(o.name for o in bob.others) == ["Ann", "Cy"]
    assert cy is not None and cy.buyers == 2  # Ann is 31 away from Cy


def test_a_cluster_is_per_name_and_a_row_with_no_named_buyer_has_none():
    out = c_of((A, d(1), "Ann"), (B, d(2), "Bob"), (A, d(3), None), (A, d(4), "Cy"))
    assert out[1] is None  # Bob alone on B
    assert out[2] is None  # no named buyer
    assert out[0] is not None and out[0].buyers == 2  # Ann and Cy on A; the unnamed filer is nobody


def test_a_repeat_buy_does_not_inflate_the_count():
    rows = c_of((A, d(1), "Ann"), (A, d(2), "Ann"), (A, d(3), "Bob"))
    assert rows[0] is not None and rows[0].buyers == 2
    assert [o.name for o in rows[0].others] == ["Bob"]


def test_a_buyer_listed_once_in_the_hover_at_the_filing_nearest_this_row():
    out = c_of((A, d(10), "Ann"), (A, d(1), "Bob"), (A, d(8), "Bob"))
    assert out[0] is not None
    assert [(o.name, o.filed) for o in out[0].others] == [("Bob", d(8))]


def test_the_page_marks_the_cluster_on_each_row_in_it():
    t = tracker(buy(1, d(1), name="Ann"), buy(2, d(9), name="Bob"), buy(3, d(10), name="Ann"))
    assert only(t, 1).cluster is not None and only(t, 1).cluster.buyers == 2
    assert only(t, 3).cluster is not None  # Ann's second buy is in the cluster with Bob
    assert t.counts.cluster_rows == 3 and t.counts.cluster_names == 1
    solo = tracker(buy(1, d(1), name="Ann"), buy(2, d(9), name="Ann"))
    assert all(r.cluster is None for r in solo.rows) and solo.counts.cluster_rows == 0


def test_a_row_at_the_windows_edge_counts_the_buyers_just_before_it_without_listing_them():
    start = window_start(T, 30)
    before = buy(1, start - timedelta(days=8), name="Ann")  # in the lead-in, outside the window
    inside = buy(2, start + timedelta(days=2), name="Bob")
    t = tracker(before, inside)
    assert [r.accession for r in t.rows] == [acc(2)]  # only the window's own rows are listed
    assert t.rows[0].cluster is not None
    assert [o.name for o in t.rows[0].cluster.others] == ["Ann"]
    assert (
        t.counts.filings_read == 1 and t.counts.purchases == 1
    )  # the lead-in is not counted as read


# ---------------------------------------------------------------------------------------------------------
# 7. became a trigger? (matched by accession on the recorded cards)
# ---------------------------------------------------------------------------------------------------------


def nights_for(tid: UUID, name: str, *cards: CallCard) -> CardNights:
    return CardNights(tid, name, None, tuple(night(c) for c in cards))


def test_a_filing_a_recorded_insider_trigger_carries_reads_trigger_on_that_card_and_another_does_not():
    cn = nights_for(
        TID,
        CARD,
        cc(TID, d(14), triggers=(trig(A, acc(1)),)),
        cc(TID, d(20), triggers=(trig(A, acc(1)),)),
    )
    t = tracker(buy(1, d(13)), buy(2, d(13), name="Sam Poe"), nights=[cn])
    got, other = only(t, 1), only(t, 2)
    assert [(h.thesis_name, h.recorded_on, h.live) for h in got.triggers] == [(CARD, d(14), True)]
    assert other.triggers == ()  # not a trigger: the page says nothing about why
    assert t.counts.trigger_rows == 1


def test_the_match_is_per_card_and_names_each_card_that_carries_it():
    one = nights_for(TID, CARD, cc(TID, d(14), triggers=(trig(A, acc(1)),)))
    two = nights_for(TID2, "Card B", cc(TID2, d(15), triggers=(trig(A, acc(1)),)))
    third = nights_for(uuid.uuid4(), "Card C", cc(TID, d(15), triggers=(trig(A, acc(2)),)))
    t = tracker(buy(1, d(13)), nights=[two, one, third])
    assert [(h.thesis_name, h.recorded_on) for h in only(t, 1).triggers] == [
        (CARD, d(14)),
        ("Card B", d(15)),
    ]  # by card name


def test_a_member_level_insider_trigger_matches_too():
    card = cc(TID, d(14), armed=(MemberCall(security_id=A, triggers=[trig(A, acc(1))]),))
    t = tracker(buy(1, d(13)), nights=[nights_for(TID, CARD, card)])
    assert [h.thesis_name for h in only(t, 1).triggers] == [CARD]


def test_only_an_insider_entry_trigger_on_that_name_matches():
    wrong_kind = trig(A, acc(1), kind=Kind.TECHNICAL_BREAKOUT)
    wrong_name = trig(B, acc(1))
    risk_side = trig(A, acc(1))  # an insider-kind ref in the risk list is not an entry trigger
    card = cc(TID, d(14), triggers=(wrong_kind, wrong_name), risks=(risk_side,))
    t = tracker(buy(1, d(13)), nights=[nights_for(TID, CARD, card)])
    assert only(t, 1).triggers == ()


def test_the_accession_matches_with_or_without_its_dashes():
    card = cc(TID, d(14), triggers=(trig(A, acc(1).replace("-", "")),))
    t = tracker(buy(1, d(13)), nights=[nights_for(TID, CARD, card)])
    assert len(only(t, 1).triggers) == 1


def test_a_trigger_that_aged_off_the_card_still_reads_as_a_trigger_and_says_it_is_off():
    """A flip trigger lives 18 days, so a 30-day window holds filings whose trigger has since left the card: they
    must not read as 'not a trigger'. ``live`` reads the newest night in the window (the card in effect).
    """
    cn = nights_for(
        TID, CARD, cc(TID, d(14), triggers=(trig(A, acc(1)),)), cc(TID, d(25), triggers=())
    )
    (hit,) = trigger_hits([cn])[(A, accession_key(acc(1)))]
    assert (hit.recorded_on, hit.live) == (d(14), False)


def test_when_the_card_in_effect_cannot_be_read_liveness_is_unknown():
    cn = CardNights(
        TID,
        CARD,
        None,
        (night(cc(TID, d(14), triggers=(trig(A, acc(1)),))), Night(d(20), 1, False, None)),
    )
    (hit,) = trigger_hits([cn])[(A, accession_key(acc(1)))]
    assert (hit.recorded_on, hit.live) == (d(14), None)
    t = tracker(buy(1, d(13)), nights=[cn])
    assert t.counts.nights_read == 2 and t.counts.nights_unreadable == 1


def test_a_card_with_no_night_in_the_window_matches_nothing():
    t = tracker(buy(1, d(13)), nights=[CardNights(TID, CARD, None, ())])
    assert only(t, 1).triggers == () and t.counts.nights_read == 0


# ---------------------------------------------------------------------------------------------------------
# 8. since the filing, against IWM; a flagged or unpriced name keeps its row
# ---------------------------------------------------------------------------------------------------------


def test_since_runs_from_the_first_close_on_or_after_the_filing_to_the_last_before_the_asof():
    closes = {A: {d(2): 10.0, d(5): 10.5, d(27): 12.0, d(28): 12.5, d(29): 99.0}}
    bench = {d(2): 100.0, d(5): 100.0, d(28): 105.0, d(29): 1.0}
    row = tracker(buy(1, d(4)), closes=closes, bench=bench).rows[0]  # Oct 4 is a Sunday
    s = row.since
    assert (s.status, s.entry_date, s.end_date) == ("priced", d(5), d(28))
    assert s.ret == pytest.approx(12.5 / 10.5 - 1.0)
    assert s.bench_ret == pytest.approx(105.0 / 100.0 - 1.0)  # IWM on the same two dates


def test_iwm_with_no_bar_on_one_of_the_dates_is_blank_never_a_shifted_span():
    row = tracker(buy(1, d(2)), closes={A: {d(2): 10.0, d(28): 11.0}}, bench={d(2): 100.0}).rows[0]
    assert row.since.status == "priced" and row.since.bench_ret is None


def test_a_name_with_no_bar_since_the_filing_is_unpriced_and_keeps_its_row():
    t = tracker(buy(1, d(20)), closes={A: {d(1): 10.0}})  # no bar on or after Oct 20
    assert t.rows[0].since.status == "unpriced" and t.counts.names_unpriced == 1
    t2 = tracker(buy(1, d(20)))  # no tape at all
    assert t2.rows[0].since.status == "unpriced"


def test_a_flagged_name_keeps_its_row_and_is_never_priced():
    t = tracker(
        buy(1, d(2)),
        buy(2, d(2), who=WB, name="Kim Lee"),
        closes={A: {d(2): 10.0, d(28): 20.0}, B: {d(2): 5.0, d(28): 6.0}},
        flagged={A: MIXED},
    )
    a, b = only(t, 1), only(t, 2)
    assert (a.since.status, a.since.reason, a.since.ret) == ("flagged", MIXED, None)
    assert b.since.status == "priced"
    assert [(n.ticker, n.reason) for n in t.flagged] == [("AAA", MIXED)]
    assert len(t.rows) == 2 and t.counts.names_flagged == 1


def test_a_non_positive_close_is_not_a_bar():
    row = tracker(buy(1, d(2)), closes={A: {d(2): 0.0, d(3): 10.0, d(28): 11.0}}).rows[0]
    assert row.since.entry_date == d(3)


# ---------------------------------------------------------------------------------------------------------
# 9. cap and tier
# ---------------------------------------------------------------------------------------------------------


def test_the_cap_and_its_tier_ride_each_row_and_a_blank_names_its_reason():
    caps = {
        A: CapCell(value=250_000_000.0, tier=CapTier.SMALL),
        B: CapCell(gap="no_shares", note=None),
    }
    t = tracker(buy(1, d(20)), buy(2, d(20), who=WB, name="Kim Lee"), caps=caps)
    a, b = only(t, 1), only(t, 2)
    assert (a.cap.value, a.cap.tier, a.cap.gap) == (250_000_000.0, "small", None)
    assert (b.cap.value, b.cap.tier, b.cap.gap) == (None, None, "no_shares")
    assert t.counts.cap_blank == 1
    assert cap_of(CapCell(gap="flagged", note=MIXED)).note == MIXED


# ---------------------------------------------------------------------------------------------------------
# 10. Context and Bench are tagged, the cards the name sits on ride the row
# ---------------------------------------------------------------------------------------------------------


def test_a_context_only_name_is_tagged_and_a_voting_name_is_not():
    mems = [
        member(A, "AAA"),
        member(B, "BBB", segment="Context", silent=True),
        member(B, "BBB2", tid=TID2, name="Card B", segment="Core"),  # voting on another card
    ]
    t = tracker(
        buy(1, d(20)), buy(2, d(20), who=WB, name="Kim Lee"), memberships=mems, include_silent=True
    )
    a, b = only(t, 1), only(t, 2)
    assert a.silent_only is False and [c.silent for c in a.cards] == [None]
    assert b.silent_only is False  # on a second card it votes
    assert {c.thesis_name: c.silent for c in b.cards} == {CARD: "Context", "Card B": None}
    solo = tracker(
        buy(2, d(20), who=WB, name="Kim Lee"),
        memberships=[member(B, "BBB", segment="Bench", silent=True)],
        include_silent=True,
    )
    assert solo.rows[0].silent_only is True and solo.rows[0].cards[0].silent == "Bench"
    assert solo.include_silent is True


def test_a_name_on_two_cards_shows_the_ticker_most_cards_use_and_each_cards_own():
    mems = [
        member(A, "AAA"),
        member(A, "AAA", tid=TID2, name="Card B"),
        member(A, "AAA.OLD", tid=uuid.uuid4(), name="Card C"),
    ]
    row = tracker(buy(1, d(20)), memberships=mems).rows[0]
    assert row.ticker == "AAA"
    assert sorted((c.thesis_name, c.ticker) for c in row.cards) == [
        ("Card A", "AAA"),
        ("Card B", "AAA"),
        ("Card C", "AAA.OLD"),
    ]


# ---------------------------------------------------------------------------------------------------------
# 11. the counts, the order, no aggregate
# ---------------------------------------------------------------------------------------------------------


def test_the_counts_say_what_was_read_and_listed():
    nameless = form4(
        [txn("P", insider_name=None, accession=acc(4), accepted=utc(2026, 10, 21, 21, 0))]
    )
    t = tracker(
        buy(1, d(20), name="Ann"),
        buy(2, d(21), name="Bob"),
        sale(3, d(22)),
        nameless,
        buy(5, d(23), who=WB, name="Kim Lee"),
    )
    c = t.counts
    assert (c.names_read, c.names_with_purchases) == (2, 2)
    assert (c.filings_read, c.purchases, c.routine_folded, c.amendments_no_purchase) == (5, 4, 1, 0)
    assert c.filings_read == c.purchases + c.routine_folded + c.amendments_no_purchase
    assert c.filer_unresolved == 1
    assert nameless.headline.startswith("reporting owner not named")
    assert next(r for r in t.rows if r.accession == acc(4)).filer is None
    assert (c.cards_counted, c.live_cards, c.not_yet_created, c.roster_fallback) == (1, 2, 0, 0)


def test_the_rows_are_newest_filing_first_then_dollar_size_then_ticker():
    t = tracker(
        buy(1, d(20), name="Ann", shares=100),
        buy(2, d(20), name="Bob", shares=5000),
        buy(3, d(21), name="Cy", shares=1),
        buy(4, d(20), name="Di", shares=100, who=WB),
    )
    assert [r.accession for r in t.rows] == [
        acc(3),
        acc(2),
        acc(1),
        acc(4),
    ]  # Oct 21; then $; then ticker


def test_every_row_has_its_own_key_and_a_week():
    t = tracker(buy(1, d(20), name="Ann"), buy(2, d(20), name="Bob"))
    assert len({r.key for r in t.rows}) == 2 and all(r.week == d(19) for r in t.rows)


def test_purchase_security_ids_are_the_names_with_a_purchase_in_the_window_only():
    old = buy(1, window_start(T, 30) - timedelta(days=3))
    new = buy(2, d(20), who=WB, name="Kim Lee")
    by = {A: [old], B: [new]}
    assert purchase_security_ids(by, T, 30) == [B]
    assert purchase_security_ids(by, T, 90) == sorted([A, B], key=str)


def test_the_page_states_no_aggregate():
    """Rule 6: a median move 'after insider buys' is an event-outcome statistic, sliced by market-cap tier on the
    Track Record page. This page lists: its payload holds counts of what was read and never a statistic over the
    rows' returns or dollars."""
    banned = (
        "median",
        "mean",
        "avg",
        "average",
        "hit_rate",
        "win_rate",
        "pooled",
        "excess",
        "stdev",
        "total_usd",
        "sum_usd",
    )
    names = [f.name for cls in (Counts, InsiderTracker, PurchaseRow) for f in fields(cls)]
    for model in (
        router_mod.InsiderTrackerOut,
        router_mod.InsiderTrackerCountsOut,
        router_mod.InsiderTrackerRowOut,
        router_mod.InsiderTrackerSinceOut,
    ):
        names += list(model.model_fields)
    assert not [n for n in names if any(b in n for b in banned)]
    import dashboards.insider_tracker as compute

    source = open(compute.__file__, encoding="utf-8").read()
    for word in ("statistics", "fmean", "median(", "numpy", "polars", "import math"):
        assert word not in source


# ---------------------------------------------------------------------------------------------------------
# 12. the router (pure: the heavy read stubbed; it threads serve_view and caches one read)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a Friday: the past as-of
LATER = date(2026, 6, 19)  # a later past as-of, after every "late" row below


def sample_tracker(window_days: int = 30, include_silent: bool = False) -> InsiderTracker:
    """Two purchases on one name (a cluster), one tied to a recorded trigger, priced on a two-bar tape."""
    cn = nights_for(TID, CARD, cc(TID, d(27), triggers=(trig(A, acc(1)),)))
    return tracker(
        buy(1, d(22), name="Ann", role="Director", shares=1000, price=10.0, traded=d(19)),
        buy(2, d(27), name="Bob", role="CEO", shares=500, price=11.0),
        sale(3, d(26)),
        asof=T,
        window_days=window_days,
        include_silent=include_silent,
        nights=[cn],
        closes={A: {d(22): 10.0, d(27): 10.5, T: 11.0}},
        bench={d(22): 100.0, T: 101.0},
        caps={A: CapCell(value=120_000_000.0, tier=CapTier.MICRO)},
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": []}

    def fake_read(conn, asof, window_days, include_silent, known_at, clock):
        seen["reads"].append((asof, window_days, include_silent, known_at, clock))
        return sample_tracker(window_days, include_silent)

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_tracker", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_repeat_requests_off_one_cached_read(stub_client):
    client, seen = stub_client
    url = "/dashboards/insider-tracker"
    first = client.get(url, params={"asof": PAST.isoformat()}).json()
    assert first["cache"] == "computed" and first["window_days"] == 30 == DEFAULT_WINDOW
    assert first["clock"] == "public" and first["include_silent"] is False
    assert client.get(url, params={"asof": PAST.isoformat()}).json()["cache"] == "hit"
    assert len(seen["reads"]) == 1
    asof, window, silent, known_at, clock = seen["reads"][0]
    assert (asof, window, silent, clock) == (PAST, 30, False, "public") and known_at is not None

    # another window and the Context-and-Bench toggle are each their own read; the live as-of is the live view
    client.get(url, params={"asof": PAST.isoformat(), "days": 90})
    client.get(url, params={"asof": PAST.isoformat(), "silent": "true"})
    assert [(r[1], r[2]) for r in seen["reads"]] == [(30, False), (90, False), (30, True)]
    client.get(url, params={"asof": market_today().isoformat()})
    assert seen["reads"][-1][3:5] == (None, "record")


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/insider-tracker", params={"asof": "2026-10-28"}).json()
    assert [r["filer"] for r in body["rows"]] == ["Bob", "Ann"]  # newest filing first
    bob, ann = body["rows"]
    assert (ann["filed"], ann["trade_date"], ann["filed_days_after"], ann["week"]) == (
        "2026-10-22",
        "2026-10-19",
        3,
        "2026-10-19",
    )
    assert (ann["role"], ann["role_classes"], ann["co_filers"]) == ("Director", ["director"], 0)
    assert (ann["shares"], ann["price"], ann["usd"]) == (1000.0, 10.0, 10_000.0)
    assert ann["cluster"]["buyers"] == 2 and ann["cluster"]["others"] == [
        {"name": "Bob", "filed": "2026-10-27"}
    ]
    assert bob["cluster"]["buyers"] == 2
    assert ann["triggers"] == [
        {"thesis_id": str(TID), "thesis_name": CARD, "recorded_on": "2026-10-27", "live": True}
    ]
    assert bob["triggers"] == []
    assert ann["since"]["status"] == "priced" and ann["since"]["ret"] == pytest.approx(0.1)
    assert ann["since"]["bench_ret"] == pytest.approx(0.01)
    assert ann["cap"] == {"value": 120_000_000.0, "tier": "micro", "gap": None, "note": None}
    assert ann["cards"] == [
        {"thesis_id": str(TID), "thesis_name": CARD, "ticker": "AAA", "silent": None}
    ]
    assert ann["url"].startswith("https://www.sec.gov/Archives/edgar/data/876543/")
    assert ann["headline"].startswith("Ann (Director): P purchase 1,000 sh @ $10.00")
    c = body["counts"]
    assert (c["filings_read"], c["purchases"], c["routine_folded"]) == (3, 2, 1)
    assert (c["cluster_rows"], c["trigger_rows"]) == (2, 1)
    assert body["window_start"] == "2026-09-28" and body["flagged"] == []
    assert isinstance(body["compute_ms"], int)


def test_the_router_rejects_an_unknown_window_and_a_missing_asof(stub_client):
    client, seen = stub_client
    assert (
        client.get(
            "/dashboards/insider-tracker", params={"asof": "2026-10-28", "days": 45}
        ).status_code
        == 422
    )
    assert client.get("/dashboards/insider-tracker").status_code == 422
    assert [int(w) for w in router_mod.InsiderTrackerDays] == list(WINDOWS)
    assert seen["reads"] == []


# ---------------------------------------------------------------------------------------------------------
# 13. DB-backed: the real point-in-time view, the real calls log, the real bars (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------


def weekdays(start: date, end: date) -> list[date]:
    """The sessions in ``[start, end]``: Mon-Fri minus NYSE closures (a vendor serves no holiday bar, F5)."""
    out, cur = [], start
    while cur <= end:
        if is_scheduled_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


SESSION_DAYS = weekdays(date(2026, 3, 2), LATER)
INGESTED = utc(
    2026, 9, 1, 18, 13
)  # our own ingest instant, after every as-of: the public clock must not care
E1, E2, L1, S1, K1 = (acc(n) for n in (101, 102, 103, 104, 105))


def _security(db, ticker: str, cik: str | None, kind: str = "equity") -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, "2026-01-01"),
        )
    return sid


def _bar(db, sid: UUID, day: date, close: float, recorded_at: datetime) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": day,
            "close": close,
            "volume": 1000.0,
            "valid_from": day,
            "recorded_at": recorded_at,
        },
    )


def _form4(db, sid, accession, name, role, code, filed: date, traded: date, shares=1000, price=4.0):
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": name,
            "insider_role": role,
            "txn_code": code,
            "shares": shares,
            "price": price,
            "usd": shares * price,
            "accession": accession,
            "txn_seq": 0,
            "valid_from": traded,
            "accepted": datetime.combine(filed, time(21, 0), tzinfo=UTC),
            "recorded_at": INGESTED,
        },
    )


def _insert_call(db, tid: UUID, card: CallCard) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, reconstructed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                DEFAULT_TENANT_ID,
                tid,
                card.asof,
                card.state.value,
                card.verdict.value,
                Json(card.model_dump(mode="json")),
                False,
            ),
        )


class World:
    """One card on two voting names and one Context name, bars through ``LATER`` for all and IWM, a shares fact for
    one, Form 4s filed before and after ``PAST``, and a two-night log."""

    def __init__(self, db) -> None:
        self.sa = _security(db, "AAA", CIK)
        self.sb = _security(db, "BBB", CIK)
        self.sc = _security(db, "CCC", CIK)
        iwm = _security(db, "IWM", None, kind="etf")
        self.iwm = iwm
        # a bar per weekday, each public at its own session, running through LATER (so bars AFTER the past as-of
        # exist in the store and must stay invisible to it)
        self.close = {
            self.sa: {s: 10.0 + 0.1 * i for i, s in enumerate(SESSION_DAYS)},
            self.sb: {s: 20.0 + 0.2 * i for i, s in enumerate(SESSION_DAYS)},
            self.sc: {s: 5.0 + 0.05 * i for i, s in enumerate(SESSION_DAYS)},
            iwm: {s: 100.0 + 0.5 * i for i, s in enumerate(SESSION_DAYS)},
        }
        for sid, closes in self.close.items():
            for day, close in closes.items():
                _bar(db, sid, day, close, datetime.combine(day, time.min, tzinfo=UTC))
        append_fact(
            db,
            "fact_shares_outstanding",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": self.sa,
                "shares": 10_000_000,
                "source": "10-q",
                "source_ref": "x",
                "valid_from": date(2026, 3, 1),
                "recorded_at": utc(2026, 3, 2),  # ratified in March: public by every as-of below
            },
        )
        # Form 4s: filed (accepted, 17:00 ET) before PAST, after it, and a sales-only one
        _form4(db, self.sa, E1, "Jane Roe", "CEO", "P", date(2026, 6, 2), date(2026, 6, 1))
        _form4(db, self.sa, L1, "Sam Poe", "Director", "P", date(2026, 6, 10), date(2026, 6, 9))
        _form4(db, self.sa, S1, "Jane Roe", "CEO", "S", date(2026, 6, 3), date(2026, 6, 2))
        _form4(
            db,
            self.sb,
            E2,
            "Kim Lee",
            "10% owner",
            "P",
            date(2026, 6, 4),
            date(2026, 6, 2),
            200,
            25.0,
        )
        _form4(db, self.sc, K1, "Cy Dunn", "Officer", "P", date(2026, 6, 3), date(2026, 6, 2))
        self.tid = self._card(
            db,
            [
                BasketMember(ticker="AAA", role="x", security_id=self.sa, segment="Core"),
                BasketMember(ticker="BBB", role="x", security_id=self.sb, segment="Core"),
                BasketMember(ticker="CCC", role="x", security_id=self.sc, segment="Context"),
            ],
        )
        # the log: E1 became a trigger on June 3; L1 on June 11 (a night AFTER the past as-of)
        _insert_call(db, self.tid, cc(self.tid, date(2026, 6, 3), triggers=(trig(self.sa, E1),)))
        _insert_call(db, self.tid, cc(self.tid, date(2026, 6, 11), triggers=(trig(self.sa, L1),)))
        db.commit()

    @staticmethod
    def _card(db, members: list[BasketMember]) -> UUID:
        thesis = Thesis(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            name=CARD,
            narrative="x",
            segments=[Segment(label="Core")],
            basket=members,
        )
        thesis_repo.upsert(db, thesis)
        with (
            db.cursor() as cur
        ):  # the card existed on the as-of (a fixture backdate, not an app write)
            cur.execute(
                "UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id)
            )
        db.commit()
        return thesis.id


def _get(db, asof: date, days: int = 30, silent: bool | None = None) -> dict:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    params: dict = {"asof": asof.isoformat(), "days": days}
    if silent is not None:
        params["silent"] = str(silent).lower()
    try:
        r = TestClient(app).get("/dashboards/insider-tracker", params=params)
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _accs(body: dict) -> list[str]:
    return sorted(r["accession"] for r in body["rows"])


def test_db_a_filing_filed_after_the_asof_is_not_a_row(db):
    """L1 was ingested long after both as-ofs and filed June 10: the June 5 view never lists it (and its buyer
    forms no cluster), the June 19 view does. The sales-only filing is a row at neither."""
    World(db)
    past = _get(db, PAST)
    assert _accs(past) == [E1, E2]  # the Context name's filing is behind the toggle
    assert all(r["filed"] <= PAST.isoformat() for r in past["rows"])
    assert (past["clock"], past["window_start"]) == ("public", "2026-05-06")
    e1 = next(r for r in past["rows"] if r["accession"] == E1)
    assert e1["cluster"] is None and e1["filed"] == "2026-06-02"
    c = past["counts"]
    assert (c["filings_read"], c["purchases"], c["routine_folded"]) == (3, 2, 1)

    later = _get(db, LATER)
    assert _accs(later) == [E1, E2, L1]
    e1 = next(r for r in later["rows"] if r["accession"] == E1)
    assert e1["cluster"]["buyers"] == 2 and e1["cluster"]["others"][0]["name"] == "Sam Poe"
    assert e1["filer"] == "Jane Roe" and e1["role"] == "CEO" and e1["role_classes"] == ["officer"]
    assert (e1["trade_date"], e1["filed_days_after"]) == ("2026-06-01", 1)
    assert S1 not in _accs(later)


def test_db_a_bar_printed_after_the_asof_is_not_read(db):
    """Bars exist through June 18 (06-19 is Juneteenth, no session), and the as-of bar of AAA is re-versioned at double AFTER the as-of's day: the
    June 5 view prices every row on the ORIGINAL bars ending June 5, IWM on the same two dates, and the cap rests
    on the as-of close."""
    w = World(db)
    _bar(db, w.sa, PAST, w.close[w.sa][PAST] * 2, INGESTED + timedelta(days=7))
    db.commit()
    body = _get(db, PAST)
    e1 = next(r for r in body["rows"] if r["accession"] == E1)
    s = e1["since"]
    assert s["status"] == "priced" and (s["entry_date"], s["end_date"]) == (
        "2026-06-02",
        "2026-06-05",
    )
    assert s["ret"] == pytest.approx(w.close[w.sa][PAST] / w.close[w.sa][date(2026, 6, 2)] - 1.0)
    assert s["bench_ret"] == pytest.approx(
        w.close[w.iwm][PAST] / w.close[w.iwm][date(2026, 6, 2)] - 1.0
    )
    assert e1["cap"]["value"] == pytest.approx(w.close[w.sa][PAST] * 10_000_000, rel=1e-6)
    assert e1["cap"]["tier"] == "micro" and e1["cap"]["gap"] is None

    # a later as-of reads the bars public by then — LATER (06-19) is Juneteenth, no session (F5), so the
    # newest bar on or before it is Thu 06-18
    later = next(r for r in _get(db, LATER)["rows"] if r["accession"] == E1)
    assert later["since"]["end_date"] == "2026-06-18"
    assert later["since"]["ret"] == pytest.approx(
        w.close[w.sa][date(2026, 6, 18)] / w.close[w.sa][date(2026, 6, 2)] - 1.0
    )


def test_db_a_name_with_no_shares_fact_keeps_its_row_with_a_blank_cap(db):
    World(db)
    e2 = next(r for r in _get(db, PAST)["rows"] if r["accession"] == E2)
    assert e2["cap"]["value"] is None and e2["cap"]["gap"] == "no_shares"
    assert e2["since"]["status"] == "priced"


def test_db_a_night_recorded_after_the_asof_is_not_read(db):
    """E1 became a trigger on the June 3 night; L1 only on June 11, after the June 5 as-of. At June 5 E1 reads a
    trigger on the card and is still on it; at June 19 the June 11 card no longer carries E1 (it is off the card)
    and L1 is on it."""
    w = World(db)
    past = _get(db, PAST)
    e1 = next(r for r in past["rows"] if r["accession"] == E1)
    assert e1["triggers"] == [
        {"thesis_id": str(w.tid), "thesis_name": CARD, "recorded_on": "2026-06-03", "live": True}
    ]
    assert next(r for r in past["rows"] if r["accession"] == E2)["triggers"] == []
    assert past["counts"]["nights_read"] == 1 and past["counts"]["trigger_rows"] == 1

    later = _get(db, LATER)
    by = {r["accession"]: r for r in later["rows"]}
    assert [(h["recorded_on"], h["live"]) for h in by[E1]["triggers"]] == [("2026-06-03", False)]
    assert [(h["recorded_on"], h["live"]) for h in by[L1]["triggers"]] == [("2026-06-11", True)]
    assert later["counts"]["nights_read"] == 2 and later["counts"]["trigger_rows"] == 2


def test_db_context_and_bench_names_appear_only_behind_the_toggle_and_are_tagged(db):
    w = World(db)
    off = _get(db, PAST)
    assert K1 not in _accs(off)
    assert off["include_silent"] is False and off["counts"]["silent_rows_left_out"] == 1
    on = _get(db, PAST, silent=True)
    assert K1 in _accs(on) and on["include_silent"] is True
    cc_row = next(r for r in on["rows"] if r["accession"] == K1)
    assert cc_row["silent_only"] is True
    assert cc_row["cards"] == [
        {"thesis_id": str(w.tid), "thesis_name": CARD, "ticker": "CCC", "silent": "Context"}
    ]
    assert on["counts"]["silent_rows_left_out"] == 0
    voting = next(r for r in on["rows"] if r["accession"] == E1)
    assert voting["silent_only"] is False and voting["cards"][0]["silent"] is None


def test_db_a_flagged_name_keeps_its_row_and_its_bars_are_never_read(db, monkeypatch):
    w = World(db)
    flags_seen: list = []
    real = router_mod.read_tape

    def spy(pit, sid, flag):
        flags_seen.append((sid, flag))
        return real(pit, sid, flag)

    monkeypatch.setattr(router_mod, "price_flags", lambda *a, **k: {w.sb: MIXED})
    monkeypatch.setattr(router_mod, "read_tape", spy)
    body = _get(db, PAST)
    by = {r["accession"]: r for r in body["rows"]}
    assert (by[E2]["since"]["status"], by[E2]["since"]["reason"]) == ("flagged", MIXED)
    assert (by[E2]["cap"]["gap"], by[E2]["cap"]["note"]) == ("flagged", MIXED)
    assert by[E1]["since"]["status"] == "priced"
    assert [(n["ticker"], n["reason"]) for n in body["flagged"]] == [("BBB", MIXED)]
    assert (w.sb, MIXED) in flags_seen and (w.sa, None) in flags_seen
    assert len(body["rows"]) == 2  # no row dropped


def test_db_the_window_reaches_back_by_filing_date_and_ninety_days_reads_more(db):
    World(db)
    old = datetime.combine(date(2026, 4, 1), time(21, 0), tzinfo=UTC)
    sid = _security(db, "DDD", CIK)
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": "Old Hand",
            "insider_role": "CFO",
            "txn_code": "P",
            "shares": 10,
            "price": 1.0,
            "usd": 10.0,
            "accession": acc(900),
            "txn_seq": 0,
            "valid_from": date(2026, 3, 31),
            "accepted": old,
            "recorded_at": INGESTED,
        },
    )
    World._card(db, [BasketMember(ticker="DDD", role="x", security_id=sid, segment="Core")])
    narrow = _get(db, PAST, days=30)
    wide = _get(db, PAST, days=90)
    assert acc(900) not in _accs(narrow) and acc(900) in _accs(wide)
    assert (wide["window_days"], wide["window_start"]) == (90, "2026-03-07")
    assert {E1, E2} <= set(_accs(wide))


# ---------------------------------------------------------------------------------------------------------
# 14. DB-backed: the page reads the Form 4 kinds ALONE (the reader's ``kinds=``), and that changes no row
# ---------------------------------------------------------------------------------------------------------

EIGHT_K, AMEND_EARLY, AMEND_LATE, S1_FORM = (acc(n) for n in (201, 202, 203, 204))
OTHER_KINDS = {"8k", "filing"}  # what a full read returns beside the Form 4 family on this fixture


def _other_kinds(db, w: World) -> None:
    """On AAA: an 8-K, a stored 4/A record filed before PAST and one filed after it, and an S-1 in the EDGAR filing
    index. The kinds a full read returns beside the Form 4s (and the 4/A is itself a Form 4 family row).
    """
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": w.sa,
            "form": "8-K",
            "items": ["5.02", "9.01"],
            "accession": EIGHT_K,
            "filed": date(2026, 6, 3),
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/876543/{EIGHT_K.replace('-', '')}/",
            "valid_from": date(2026, 6, 3),
            "recorded_at": INGESTED,
        },
    )
    for accession, filed in ((AMEND_EARLY, date(2026, 6, 4)), (AMEND_LATE, date(2026, 6, 10))):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO insider_amendment (tenant_id, security_id, accession, issuer_cik, owners, "
                "date_of_original_submission, remarks, rows, filed, accepted, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    DEFAULT_TENANT_ID,
                    w.sa,
                    accession,
                    CIK,
                    Json([{"cik": "0001111111", "name": "Jane Roe"}]),
                    date(2026, 6, 1),
                    f"remarks {accession}",
                    Json([{"txn_code": "S"}]),
                    filed,
                    datetime.combine(filed, time(20, 0), tzinfo=UTC),
                    INGESTED,
                ),
            )
    ingest_filing_index(
        db,
        w.sa,
        CIK,
        [{"form": "S-1", "accession": S1_FORM, "primary_doc": "s1.htm", "filed": "2026-06-05"}],
        recorded_at=INGESTED,
    )
    db.commit()


@pytest.mark.parametrize("asof", [PAST, LATER])
def test_db_the_narrowed_reader_returns_the_full_reads_form4_rows_and_no_other_kind(db, asof):
    """The same names, as-of and clock through the real point-in-time view: the Form 4 kinds alone are exactly the
    full read's Form 4 and 4/A rows (the 4/A gate unchanged: the later 4/A appears only at the later as-of), no row
    of any other kind comes back, and naming nothing or every kind is the full read."""
    w = World(db)
    _other_kinds(db, w)
    sids = [w.sa, w.sb, w.sc]
    args = (db, sids, asof, known_at_for_asof(asof), "public")
    full = filings_mod.read_filing_rows(*args, days=120)
    narrow = filings_mod.read_filing_rows(*args, days=120, kinds=FORM4_KINDS)

    assert set(narrow) == set(full) == set(sids)  # every company has an entry either way
    for sid in sids:
        assert narrow[sid] == tuple(r for r in full[sid] if r.kind in FORM4_KINDS)
    assert OTHER_KINDS | FORM4_KINDS <= {r.kind for rs in full.values() for r in rs}  # not vacuous
    assert {r.kind for rs in narrow.values() for r in rs} == FORM4_KINDS
    four_a = sorted(r.accession for r in narrow[w.sa] if r.kind == "form4a")
    assert four_a == ([AMEND_EARLY] if asof == PAST else [AMEND_EARLY, AMEND_LATE])

    # the default is unchanged
    assert filings_mod.read_filing_rows(*args, days=120, kinds=None) == full
    assert filings_mod.read_filing_rows(*args, days=120, kinds=ALL_KINDS) == full


def test_db_the_narrowed_reader_never_loads_another_kinds_table_or_runs_the_4a_select_without_form4a(
    db, monkeypatch
):
    w = World(db)
    _other_kinds(db, w)
    tables: list[str] = []
    amendment_selects: list[int] = []
    real_rows, real_splits = PointInTimeData._rows, PointInTimeData._split_rows
    real_amendments = filings_mod.read_amendments

    def rows_spy(self, table, security_id):
        tables.append(table)
        return real_rows(self, table, security_id)

    def splits_spy(self, security_id):
        tables.append("split list")
        return real_splits(self, security_id)

    def amendments_spy(*a, **k):
        amendment_selects.append(1)
        return real_amendments(*a, **k)

    monkeypatch.setattr(PointInTimeData, "_rows", rows_spy)
    monkeypatch.setattr(PointInTimeData, "_split_rows", splits_spy)
    monkeypatch.setattr(filings_mod, "read_amendments", amendments_spy)
    args = (db, [w.sa, w.sb, w.sc], LATER, known_at_for_asof(LATER), "public")

    filings_mod.read_filing_rows(*args, days=120, kinds=FORM4_KINDS)
    assert set(tables) == {"fact_insider_txn"} and len(amendment_selects) == 1

    tables.clear()
    amendment_selects.clear()
    form4 = filings_mod.read_filing_rows(*args, days=120, kinds={"form4"})
    assert set(tables) == {"fact_insider_txn"} and amendment_selects == []
    assert {r.kind for rs in form4.values() for r in rs} == {"form4"}

    # the full read loads every kind's table, so the spy is not blind
    tables.clear()
    amendment_selects.clear()
    filings_mod.read_filing_rows(*args, days=120)
    assert {
        "fact_insider_txn",
        "fact_corporate_event",
        "fact_activist_stake",
        "fact_fundamentals",
        "fact_dilution",
        "fact_catalyst",
        "fact_cash_burn",
        "fact_shares_outstanding",
        "fact_revenue_mix",
        "fact_filing_index",
        "split list",
    } <= set(tables) and len(amendment_selects) == 1


def test_db_the_page_asks_the_reader_for_the_form4_kinds_and_serves_the_page_a_full_read_would(
    db, monkeypatch
):
    w = World(db)
    _other_kinds(db, w)
    real = router_mod.read_filing_rows
    asked: list = []

    def narrowed(*a, **k):
        asked.append(k.get("kinds"))
        return real(*a, **k)

    def full(*a, kinds=None, **k):  # the same call with the narrowing taken off
        asked.append("full")
        return real(*a, **k)

    monkeypatch.setattr(router_mod, "read_filing_rows", narrowed)
    got = _get(db, LATER, silent=True)
    monkeypatch.setattr(router_mod, "read_filing_rows", full)
    want = _get(db, LATER, silent=True)

    assert asked == [FORM4_KINDS, "full"]
    assert got["rows"] and {E1, E2, L1, K1} <= set(_accs(got))  # not vacuous

    def keep(body: dict) -> dict:  # the run's own state (cache status, timing) is not the page
        return {k: v for k, v in body.items() if k not in ("cache", "compute_ms")}

    assert keep(got) == keep(want)  # every row, count and note identical
