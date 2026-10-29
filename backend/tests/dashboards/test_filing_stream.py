"""Filing Stream (``docs/dashboards/filing-stream.md``) and the shared filing reader it introduces
(``dashboards/filings.py``): each normalizer on hand-built rows (every field, the grouping by accession, the YoY
rule, the tags, NULL vs empty filer text), the EDGAR URL builder, the 4/A fold, the roster / links / roles, the
window, the day order and the fold counts; the router off one cached read with its 404; and, DB-backed, a fact
published after a past as-of never reaching the stream (an 8-K, a Form 4 and a 4/A), with the 4/A's record-clock
gate."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import filing_stream as router_mod
from dashboards.baskets import CardChoice, CardsRead, Membership, card_choices
from dashboards.cache import CACHE
from dashboards.filing_stream import (
    READ_DAYS,
    WINDOWS,
    StreamRow,
    companies_of,
    counts_of,
    filing_stream,
    group_by_day,
    in_window,
    link_options,
    roles_set,
    sort_key,
    stream_inputs,
    stream_rows,
)
from dashboards.filings import (
    KIND_ORDER,
    SEC_FILING_KINDS,
    STANDARD_8K_ITEMS,
    TAG_10B5_1,
    TAG_FILER_UNRESOLVED,
    TAG_INTERNAL_TRANSFER,
    TAG_ISSUER_SELF,
    TAG_ITEMS_PENDING,
    TAG_NEEDS_REVIEW,
    TAG_RE_REPORTED,
    FilingRow,
    Who,
    convert_overhang_pct,
    filing_index_url,
    fmt_compact,
    is_issuer_self,
    item_words,
    merge_4a,
    prior_year_row,
    revenue_form,
    rows_for_company,
    rows_from_amendments,
    rows_from_catalysts,
    rows_from_corporate_events,
    rows_from_dilution,
    rows_from_filing_index,
    rows_from_insider_txns,
    rows_from_ratified,
    rows_from_revenue,
    rows_from_splits,
    rows_from_stakes,
    source_url,
    split_public_date,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.insider_internal_transfers import INTERNAL_TRANSFER
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.corporate_events import ITEM_LABELS

UTC = timezone.utc
SID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
W = Who(SID, "ACME", "0000876543", "Acme Corp")
ACC = "0000876543-26-000010"  # an 18-digit accession, with its dashes
ASOF = date(2026, 10, 28)


def utc(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------
# the EDGAR URL
# ---------------------------------------------------------------------------------------------------------


def test_filing_index_url_uses_the_cik_as_an_int_and_the_accession_without_dashes():
    assert (
        filing_index_url("0000876543", "0000876543-26-000010")
        == "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/"
    )
    assert filing_index_url(" 876543 ", "000087654326000010") == (
        "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/"
    )


@pytest.mark.parametrize(
    "cik,acc",
    [
        (None, ACC),
        ("", ACC),
        ("0000876543", None),
        ("0000876543", ""),
        ("0000876543", "not-an-accession"),  # never a guessed link
        ("0000876543", "0000876543-26-00001"),  # 17 digits
        ("abc", ACC),  # no digits in the CIK
    ],
)
def test_filing_index_url_is_none_when_a_part_is_missing_or_malformed(cik, acc):
    assert filing_index_url(cik, acc) is None


def test_source_url_takes_a_url_as_is_an_accession_through_the_index_and_nothing_else():
    assert source_url("https://example.com/award/1", "0000876543") == "https://example.com/award/1"
    assert source_url(ACC, "0000876543") == filing_index_url("0000876543", ACC)
    assert source_url("DOE-AWARD-77", "0000876543") is None  # an award id is no link
    assert source_url("javascript:alert(1)", "0000876543") is None  # only http(s)
    assert source_url(None, "0000876543") is None
    assert source_url(ACC, None) is None  # no CIK to build with


# ---------------------------------------------------------------------------------------------------------
# 8-K
# ---------------------------------------------------------------------------------------------------------


def event(**over):
    base = {
        "form": "8-K",
        "items": ["5.02", "9.01"],
        "accession": ACC,
        "filed": date(2026, 10, 20),
        "valid_from": date(2026, 10, 20),
        "source_ref": "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/",
    }
    return {**base, **over}


def test_an_8k_row_labels_every_standard_item_and_leaves_an_unknown_one_as_the_bare_code():
    (r,) = rows_from_corporate_events([event()], W)
    assert (r.kind, r.form, r.date, r.accession) == ("8k", "8-K", date(2026, 10, 20), ACC)
    assert r.headline == (
        "8-K · 5.02 officer/director departure or appointment · 9.01 financial statements and exhibits"
    )
    assert r.detail == (
        "Item 5.02 officer/director departure or appointment",
        "Item 9.01 financial statements and exhibits",
    )
    assert r.url == "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/"
    assert (r.security_id, r.ticker, r.tags, r.routine, r.usd) == (SID, "ACME", (), False, None)
    assert item_words("3.02").startswith("3.02 unregistered")
    # a code in neither table is shown as the bare code, never a guessed label
    (u,) = rows_from_corporate_events([event(items=["5.02", "10.01"])], W)
    assert u.headline == "8-K · 5.02 officer/director departure or appointment · 10.01"


def test_the_standard_8k_items_have_display_labels_and_the_signal_modules_own_words_win():
    # every item of Form 8-K, sections 1-9 (SEC's numbering): a label, never the bare code
    standard = [
        *(f"1.0{i}" for i in range(1, 6)),
        *(f"2.0{i}" for i in range(1, 7)),
        *(f"3.0{i}" for i in range(1, 4)),
        *(f"4.0{i}" for i in range(1, 3)),
        *(f"5.0{i}" for i in range(1, 9)),
        *(f"6.0{i}" for i in range(1, 6)),
        "7.01",
        "8.01",
        "9.01",
    ]
    assert sorted(STANDARD_8K_ITEMS) == sorted(standard)
    for code in standard:
        assert item_words(code).startswith(f"{code} ") and len(item_words(code)) > len(code) + 1
    assert item_words("2.02") == "2.02 results of operations and financial condition"
    assert item_words("7.01") == "7.01 Regulation FD disclosure"
    assert item_words("8.01") == "8.01 other events"
    # the seven items the call-side module knows keep ITS wording (the table only fills the rest)
    for code, label in ITEM_LABELS.items():
        assert item_words(code) == f"{code} {label}"


def test_an_8k_with_unresolved_items_says_so_and_never_invents_a_label():
    (r,) = rows_from_corporate_events([event(items=None, form="8-K/A", source_ref="x")], W)
    assert r.form == "8-K/A" and r.headline == "8-K/A · items not yet resolved"
    assert r.tags == (TAG_ITEMS_PENDING,)
    # an empty item list is "captured, none stated", not "unresolved"
    (e,) = rows_from_corporate_events([event(items=[])], W)
    assert e.tags == () and "no item codes stated" in e.headline


def test_an_8k_with_no_stored_source_builds_its_link_from_the_cik_and_accession():
    (r,) = rows_from_corporate_events([event(source_ref=None)], W)
    assert r.url == filing_index_url("0000876543", ACC)
    (n,) = rows_from_corporate_events([event(source_ref=None)], Who(SID, "ACME", None))
    assert n.url is None  # no CIK, no link


# ---------------------------------------------------------------------------------------------------------
# Form 4
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
        "accession": ACC,
        "valid_from": date(2026, 10, 1),  # the TRADE date: never the stream's date
        "accepted": utc(2026, 10, 2, 21, 0),  # 17:00 ET on Oct 2
        "recorded_at": utc(2026, 10, 3),
        "issuer_cik": "0000876543",
        "issuer_name": "Acme Corp",
        "rpt_owner_cik": "0001111111",
        "aff_10b5_1": None,
        "direct_indirect": None,
        "shares_owned_after": None,
        "ownership_nature": None,
        "txn_footnotes": None,
        "filing_natures": None,
    }
    return {**base, **over}


def test_form4_rows_group_by_accession_and_sum_by_code_with_the_plans_words():
    other = "0000876543-26-000011"
    rows = [
        txn("P", 7000, 4.00),
        txn("P", 5000, 4.24),
        txn("S", 3000, 5.00),
        txn("S", 400, 6.00, accession=other, accepted=utc(2026, 10, 9, 14, 0)),
    ]
    got = rows_from_insider_txns(rows, W)
    assert [r.accession for r in got] == [ACC, other]
    buy, sale = got
    assert buy.kind == "form4" and buy.form == "4" and buy.date == date(2026, 10, 2)
    assert buy.headline == (
        "Jane Roe (CEO): P purchase 12,000 sh @ $4.10 · $49,200; S sale 3,000 sh @ $5.00 · $15,000"
    )
    assert buy.usd == pytest.approx(34_200.0)  # purchases minus sales
    assert buy.routine is False  # it holds a code-P purchase
    assert buy.url == filing_index_url("0000876543", ACC)
    assert [d.split(" · ")[1] for d in buy.detail[:3]] == ["P", "P", "S"]
    assert buy.detail[0] == "2026-10-01 · P · 7,000 sh @ $4.00 · $28,000"
    # a sale alone is routine, and its net is negative
    assert sale.routine is True and sale.usd == pytest.approx(-2_400.0)
    assert sale.date == date(2026, 10, 9)


def test_form4_codes_are_labeled_in_the_plans_order_and_others_by_their_code():
    rows = [txn("J", 10, 1.0), txn("F", 50, 2.0), txn("M", 100, 3.0), txn("P", 20, 4.0)]
    (r,) = rows_from_insider_txns(rows, W)
    parts = [
        p.split(" ")[0] + " " + p.split(" ")[1] for p in r.headline.split(": ", 1)[1].split("; ")
    ]
    assert parts == ["P purchase", "M option", "F tax", "J 10"]
    assert "A grant/award" in rows_from_insider_txns([txn("A")], W)[0].headline
    assert "G gift" in rows_from_insider_txns([txn("G")], W)[0].headline


def test_form4_is_dated_by_sec_acceptance_in_market_time_not_by_utc_or_the_trade_date():
    # 02:00 UTC on Oct 3 is 22:00 ET on Oct 2
    (r,) = rows_from_insider_txns([txn(accepted=utc(2026, 10, 3, 2, 0))], W)
    assert r.date == date(2026, 10, 2)
    # a filing with no acceptance counts from the instant we first held it (the public clock's own rule)
    (n,) = rows_from_insider_txns([txn(accepted=None, recorded_at=utc(2026, 10, 6, 15, 0))], W)
    assert n.date == date(2026, 10, 6)
    # nothing to date it by: left out, never guessed
    assert rows_from_insider_txns([txn(accepted=None, recorded_at=None)], W) == []


def test_form4_issuer_self_filing_by_cik_with_leading_zeros_and_by_name():
    by_cik = txn(rpt_owner_cik="876543", issuer_cik="0000876543", insider_name="Someone Else")
    assert is_issuer_self(by_cik, None)
    (a,) = rows_from_insider_txns([by_cik], W)
    assert TAG_ISSUER_SELF in a.tags
    # the name fallback: the row's own issuer_name, else the master's, a trailing period ignored
    by_name = txn(insider_name="ACME CORP.", rpt_owner_cik=None, issuer_cik=None, issuer_name=None)
    (b,) = rows_from_insider_txns([by_name], W)
    assert TAG_ISSUER_SELF in b.tags
    # a person, and a row with no identity at all, are not tagged (a missing field only ever keeps the row plain)
    assert rows_from_insider_txns([txn()], W)[0].tags == ()
    bare = txn(insider_name=None, rpt_owner_cik=None, issuer_cik=None, issuer_name=None)
    assert rows_from_insider_txns([bare], W)[0].tags == ()
    assert not is_issuer_self(bare, "Acme Corp")


def test_form4_internal_transfer_tag_and_a_purchase_leg_is_never_routine():
    leg = txn(
        "P",
        **{
            INTERNAL_TRANSFER: {
                "with": ("Sujoda Investments, LP",),
                "evidence": (),
                "co_report": False,
            }
        },
    )
    (r,) = rows_from_insider_txns([leg], W)
    assert TAG_INTERNAL_TRANSFER in r.tags
    assert r.detail[0].endswith("internal transfer with Sujoda Investments, LP")
    assert r.routine is False  # the plan: a filing holding a code-P purchase is never routine
    (s,) = rows_from_insider_txns(
        [txn("S", **{INTERNAL_TRANSFER: {"with": (), "evidence": ()}})], W
    )
    assert TAG_INTERNAL_TRANSFER in s.tags and s.routine is True


@pytest.mark.parametrize("flag,tagged", [(True, True), (False, False), (None, False)])
def test_form4_10b5_1_tag_only_on_an_explicit_true(flag, tagged):
    (r,) = rows_from_insider_txns([txn(aff_10b5_1=flag)], W)
    assert (TAG_10B5_1 in r.tags) is tagged


def test_form4_filer_text_null_is_not_captured_and_an_empty_list_is_none_stated():
    (none,) = rows_from_insider_txns([txn()], W)  # every text column NULL
    assert not any(
        "Footnote" in d or "Indirect holdings" in d or "Ownership nature" in d for d in none.detail
    )
    (empty,) = rows_from_insider_txns([txn(txn_footnotes=[], filing_natures=[])], W)
    assert "Footnotes: none stated" in empty.detail
    assert "Indirect holdings the filing states: none stated" in empty.detail
    (said,) = rows_from_insider_txns(
        [
            txn(
                ownership_nature="By Spouse",
                txn_footnotes=["Held  by the\nreporting person's spouse."],
                filing_natures=["By Spouse", "By Trust"],
            )
        ],
        W,
    )
    assert "Ownership nature: By Spouse" in said.detail
    assert "Footnote: Held by the reporting person's spouse." in said.detail
    assert "Indirect holdings the filing states: By Spouse; By Trust" in said.detail


def test_form4_detail_carries_ownership_and_the_post_trade_holding():
    (r,) = rows_from_insider_txns([txn(direct_indirect="I", shares_owned_after=250_000)], W)
    assert (
        r.detail[0]
        == "2026-10-01 · P · 1,000 sh @ $4.00 · $4,000 · indirect · 250,000 sh owned after"
    )


def test_form4_names_a_joint_report_and_more_than_one_owner():
    (r,) = rows_from_insider_txns([txn(joint_reporters=("Jane Roe", "Roe Family Trust"))], W)
    assert "Also reported by: Roe Family Trust" in r.detail
    (m,) = rows_from_insider_txns([txn(), txn(insider_name="John Doe")], W)
    assert m.headline.startswith("Jane Roe (CEO) and 1 more:")
    (anon,) = rows_from_insider_txns([txn(insider_name=None, insider_role=None)], W)
    assert anon.headline.startswith("reporting owner not named:")


def test_form4_sums_do_not_invent_what_the_row_does_not_state():
    (r,) = rows_from_insider_txns([txn("P", shares=None, price=None, usd=None)], W)
    assert r.headline == "Jane Roe (CEO): P purchase"
    # a price below a dollar keeps four places
    (c,) = rows_from_insider_txns([txn("P", 100_000, 0.0412)], W)
    assert "@ $0.0412" in c.headline


# ---------------------------------------------------------------------------------------------------------
# Form 4/A
# ---------------------------------------------------------------------------------------------------------

A_ACC = "0000876543-26-000020"


def amendment(**over):
    base = {
        "accession": A_ACC,
        "issuer_cik": "0000876543",
        "owners": [{"cik": "0001111111", "name": "Jane Roe"}],
        "date_of_original_submission": date(2026, 10, 2),
        "remarks": "Corrects  the price\nin row 2.",
        "rows": [{"txn_code": "P"}, {"txn_code": "S"}],
        "filed": date(2026, 10, 22),
        "accepted": utc(2026, 10, 22, 20, 0),
        "recorded_at": utc(2026, 10, 23),
        "day": date(2026, 10, 22),
    }
    return {**base, **over}


def test_a_4a_is_a_filing_shown_as_filed_never_resolved_against_its_original():
    (r,) = rows_from_amendments([amendment()], W)
    assert (r.kind, r.form, r.date, r.accession) == ("form4a", "4/A", date(2026, 10, 22), A_ACC)
    assert r.headline == "4/A by Jane Roe amends the Form 4 filed 2026-10-02"
    assert r.detail == (
        "Filer's remarks: Corrects the price in row 2.",
        "2 transaction rows as filed",
    )
    assert r.url == filing_index_url("0000876543", A_ACC) and r.routine is False
    (u,) = rows_from_amendments(
        [amendment(date_of_original_submission=None, owners=[], remarks=None, rows=[{}])], W
    )
    assert u.headline == "4/A amends an earlier Form 4"
    assert u.detail == ("1 transaction row as filed",)
    two = rows_from_amendments([amendment(owners=[{"name": "A"}, {"name": "B"}, {"cik": "9"}])], W)[
        0
    ]
    assert two.headline.startswith("4/A by A, B amends")


def test_rows_an_applied_4a_put_on_the_form4_tape_fold_into_its_record_row():
    applied = [
        txn(
            "P",
            2000,
            3.0,
            accession=A_ACC,
            accepted=utc(2026, 10, 22, 20, 0),
            amends_accession="0000876543-26-000009",
        )
    ]
    (tape,) = rows_from_insider_txns(applied, W)
    assert (tape.kind, tape.form, tape.routine) == ("form4a", "4/A", False)
    assert "applied 4/A" in tape.tags
    assert tape.detail[0] == (
        "Rows the system took from this 4/A, which amends accession 0000876543-26-000009"
    )
    (record,) = rows_from_amendments([amendment()], W)
    (merged,) = merge_4a([tape], [record])
    assert merged.headline == record.headline and merged.date == record.date
    assert merged.detail == record.detail + tape.detail
    assert merged.usd == pytest.approx(6000.0) and "applied 4/A" in merged.tags
    # no record in view: the tape group stays a row of its own (a filing is never dropped)
    assert merge_4a([tape], []) == [tape]
    # a record with no applied rows stays as filed
    assert merge_4a([], [record]) == [record]


# ---------------------------------------------------------------------------------------------------------
# 13D / 13G
# ---------------------------------------------------------------------------------------------------------


def stake(**over):
    base = {
        "form": "SC 13D",
        "filer_name": "Big Fund LP",
        "filer_cik": "0000555555",
        "pct_owned": Decimal("7.2"),
        "accession": "0000555555-26-000003",
        "filed": date(2026, 10, 15),
        "valid_from": date(2026, 10, 15),
        "source_ref": "https://www.sec.gov/Archives/edgar/data/876543/000055555526000003/",
    }
    return {**base, **over}


def test_a_stake_row_names_the_filer_and_the_percent_and_flags_an_unresolved_filer():
    (r,) = rows_from_stakes([stake()], W)
    assert (r.kind, r.form, r.date) == ("stake", "SC 13D", date(2026, 10, 15))
    assert r.headline == "SC 13D · Big Fund LP · 7.2%" and r.tags == ()
    assert r.url.endswith("/000055555526000003/")
    (u,) = rows_from_stakes([stake(filer_name=None, pct_owned=None, form="SC 13G/A")], W)
    assert u.headline == "SC 13G/A · filer not named" and u.tags == (TAG_FILER_UNRESOLVED,)


# ---------------------------------------------------------------------------------------------------------
# revenue
# ---------------------------------------------------------------------------------------------------------


def rev(period_end, value, accession, filed, fp="Q2", basis="native", **over):
    base = {
        "metric_key": "revenue",
        "period_end": period_end,
        "fiscal_period": fp,
        "value": Decimal(str(value)),
        "basis": basis,
        "accession": accession,
        "valid_from": filed,
        "knowable_from": filed,
        "knowable_accession": accession,
    }
    return {**base, **over}


PRIOR = rev(date(2025, 6, 30), 100_000_000, "0000876543-25-000030", date(2025, 8, 5))
NOW = rev(date(2026, 6, 30), 134_000_000, "0000876543-26-000030", date(2026, 8, 5))


def test_a_revenue_print_carries_its_yoy_only_when_the_prior_year_row_is_in_the_read():
    got = {r.accession: r for r in rows_from_revenue([PRIOR, NOW], W)}
    assert got[NOW["accession"]].headline == (
        "revenue, period ending 2026-06-30: $134.0M (native) · +34% YoY"
    )
    assert got[NOW["accession"]].form == "10-Q" and got[NOW["accession"]].date == date(2026, 8, 5)
    # the prior-year print has no row a year earlier in the read: no YoY
    assert "YoY" not in got[PRIOR["accession"]].headline
    # a small change keeps one decimal, a fall carries its sign
    small = rev(date(2026, 6, 30), 103_400_000, "0000876543-26-000031", date(2026, 8, 6))
    assert rows_from_revenue([PRIOR, small], W)[1].headline.endswith("+3.4% YoY")
    fall = rev(date(2026, 6, 30), 60_000_000, "0000876543-26-000032", date(2026, 8, 6))
    assert rows_from_revenue([PRIOR, fall], W)[1].headline.endswith("-40% YoY")


def test_the_yoy_rule_tolerates_a_52_53_week_year_and_refuses_a_missing_or_zero_prior():
    wk = rev(date(2026, 6, 27), 120_000_000, "0000876543-26-000033", date(2026, 8, 5))
    prior_wk = rev(date(2025, 6, 28), 100_000_000, "0000876543-25-000033", date(2025, 8, 5))
    assert prior_year_row(wk, [prior_wk, wk]) is prior_wk
    assert rows_from_revenue([prior_wk, wk], W)[1].headline.endswith("+20% YoY")
    far = rev(
        date(2025, 6, 1), 100_000_000, "0000876543-25-000034", date(2025, 8, 5)
    )  # a month off
    assert prior_year_row(wk, [far, wk]) is None
    zero = rev(date(2025, 6, 30), 0, "0000876543-25-000035", date(2025, 8, 5))
    assert "YoY" not in rows_from_revenue([zero, NOW], W)[1].headline
    assert "YoY" not in rows_from_revenue([NOW], W)[0].headline


def test_revenue_form_is_the_10k_for_a_fiscal_q4_and_the_10q_otherwise():
    assert revenue_form("Q4") == "10-K" and revenue_form("FY") == "10-K"
    assert revenue_form("Q1") == "10-Q" and revenue_form("Q3") == "10-Q"
    assert revenue_form(None) == "10-Q / 10-K"
    q4 = rev(
        date(2025, 12, 31),
        90_000_000,
        "0000876543-26-000005",
        date(2026, 2, 20),
        fp="Q4",
        basis="derived",
    )
    (r,) = rows_from_revenue([q4], W)
    assert r.form == "10-K" and "(derived)" in r.headline


def test_two_periods_in_one_filing_make_one_row_headed_by_the_latest():
    older = rev(date(2026, 3, 31), 90_000_000, "0000876543-26-000030", date(2026, 8, 5), fp="Q1")
    got = rows_from_revenue([older, NOW], W)
    assert len(got) == 1
    assert got[0].headline.startswith("revenue, period ending 2026-06-30:")
    assert got[0].detail == ("Also states period ending 2026-03-31: $90.0M (native)",)


def test_a_re_reported_revenue_row_is_tagged_with_its_first_knowable_date():
    again = rev(
        date(2025, 6, 30),
        100_000_000,
        "0000876543-26-000030",
        date(2026, 8, 5),
        knowable_from=date(2025, 8, 5),
        knowable_accession="0000876543-25-000030",
    )
    (r,) = rows_from_revenue([again], W)
    assert r.tags == (TAG_RE_REPORTED,) and r.date == date(2026, 8, 5)
    assert r.detail == ("First knowable 2025-08-05 in accession 0000876543-25-000030",)
    assert (
        rows_from_revenue(
            [rev(date(2026, 6, 30), 5, "0000876543-26-000040", date(2026, 8, 5), metric_key="eps")],
            W,
        )
        == []
    )


# ---------------------------------------------------------------------------------------------------------
# splits, dilution, catalysts, ratified facts
# ---------------------------------------------------------------------------------------------------------


def split(**over):
    base = {
        "ratio_from": Decimal("10"),
        "ratio_to": Decimal("1"),
        "effective_date": date(2026, 10, 15),
        "valid_from": date(2026, 10, 12),
        "recorded_at": utc(2026, 10, 9, 4, 0),  # the public instant: 00:00 ET on Oct 9
        "status": "confirmed",
        "confirmed_via": "auto:sec+polygon",
        "sec_accession": "0000876543-26-000050",
        "sec_form": "8-K",
        "sec_filed": date(2026, 10, 9),
        "sec_ratio_from": Decimal("10"),
        "sec_ratio_to": Decimal("1"),
        "sec_url": "https://www.sec.gov/Archives/edgar/data/876543/000087654326000050/",
        "polygon_split_from": Decimal("10"),
        "polygon_split_to": Decimal("1"),
        "polygon_execution_date": date(2026, 10, 15),
    }
    return {**base, **over}


def test_a_split_row_words_the_ratio_and_dates_it_by_its_own_publication_per_clock():
    (pub,) = rows_from_splits([split()], W, clock="public")
    assert pub.kind == "split" and pub.form == "split"
    assert pub.date == date(2026, 10, 9)  # the public instant, not the effective date
    assert pub.headline == "1-for-10 reverse split, effective 2026-10-15"
    assert pub.detail == (
        "Status as stored: confirmed (auto:sec+polygon)",
        "SEC 8-K 0000876543-26-000050 filed 2026-10-09, 1-for-10 reverse split",
        "Massive.com: 1-for-10 reverse split, first session 2026-10-15",
    )
    assert pub.url == split()["sec_url"] and pub.tags == ()
    # the live record clock: recorded_at is only when we stored it, so the row's own earlier date stands
    (live,) = rows_from_splits([split()], W, clock="record")
    assert live.date == date(2026, 10, 12)
    assert split_public_date({"effective_date": date(2026, 10, 15)}, "record") == date(2026, 10, 15)
    assert split_public_date({}, "record") is None


def test_a_split_that_needs_review_says_so_and_every_status_is_shown_as_stored():
    (r,) = rows_from_splits([split(status="needs_review", confirmed_via=None)], W, clock="public")
    assert r.tags == (TAG_NEEDS_REVIEW,) and r.detail[0] == "Status as stored: needs_review"
    assert rows_from_splits([split(status="rejected")], W, clock="public")[0].tags == ("rejected",)
    assert rows_from_splits([split(status="announced")], W, clock="public")[0].tags == ()
    fwd = rows_from_splits([split(ratio_from=1, ratio_to=2)], W, clock="public")[0]
    assert fwd.headline.startswith("2-for-1 forward split")


def dilution(**over):
    base = {
        "instrument_kind": "convertible_notes",
        "accession": "0000876543-26-000060",
        "principal_total_usd": Decimal("350000000"),
        "shares_outstanding": Decimal("100000000"),
        "shares_outstanding_ref": "10-Q cover",
        "terms": {
            "conversion_rate": 12.5,
            "coupon_pct": 2.5,
            "maturity_date": "2031-06-01",
            "conversion_price_usd": 80.0,
        },
        "valid_from": date(2026, 6, 1),
    }
    return {**base, **over}


def test_a_dilution_row_states_the_overhang_formula_and_only_when_both_halves_are_stored():
    # 350,000,000 / 1,000 x 12.5 = 4,375,000 as-converted shares on a 100,000,000 basis
    assert convert_overhang_pct(dilution()) == pytest.approx(4.375)
    (r,) = rows_from_dilution([dilution()], W)
    assert r.kind == "dilution" and r.date == date(2026, 6, 1) and r.usd == 350_000_000
    assert (
        r.headline
        == "convertible notes: $350.0M principal · 4.4% of shares outstanding as converted"
    )
    assert r.detail[0] == "coupon 2.5% · matures 2031-06-01 · conversion price $80.00"
    assert (
        r.detail[1] == "Shares-outstanding basis stored with the offering: 100,000,000 (10-Q cover)"
    )
    assert r.detail[2].startswith("As-converted shares = principal")
    assert r.url == filing_index_url("0000876543", "0000876543-26-000060")
    for hole in ({"shares_outstanding": None}, {"terms": {}}, {"shares_outstanding": 0}):
        assert convert_overhang_pct(dilution(**hole)) is None
    (n,) = rows_from_dilution([dilution(shares_outstanding=None)], W)
    assert n.headline == "convertible notes: $350.0M principal"


def test_a_ratified_catalyst_is_dated_by_its_event_date_and_links_only_a_real_source():
    f = {
        "catalyst_type": "regulatory",
        "grade": "core",
        "label": "FDA approval decision",
        "source": "ratified",
        "source_ref": "https://www.fda.gov/x",
        "ratified_by": "operator",
        "valid_from": date(2026, 10, 10),
        "horizon_end": date(2026, 12, 1),
    }
    (r,) = rows_from_catalysts([f], W)
    assert (r.kind, r.form, r.date) == ("catalyst", "catalyst", date(2026, 10, 10))
    assert r.headline == "regulatory: FDA approval decision" and r.url == "https://www.fda.gov/x"
    assert r.detail == (
        "Type: regulatory",
        "Grade: core",
        "Signal horizon ends 2026-12-01",
        "Source: ratified, ratified by operator",
    )
    (a,) = rows_from_catalysts(
        [{**f, "source_ref": "0000876543-26-000070", "horizon_end": None}], W
    )
    assert a.accession == "0000876543-26-000070" and a.url == filing_index_url(
        "0000876543", a.accession
    )
    (n,) = rows_from_catalysts([{**f, "source_ref": "DOE-AWARD-77", "ratified_by": None}], W)
    assert n.url is None and n.accession is None


def test_a_catalyst_nobody_ratified_is_not_worded_as_ratified_but_keeps_its_event_date():
    f = {
        "catalyst_type": "contract_award",
        "label": "DOE award",
        "source": "doe_usaspending",
        "source_ref": "DOE-AWARD-77",
        "valid_from": date(2026, 10, 10),
    }
    (feed,) = rows_from_catalysts([{**f, "ratified_by": None}], W)
    (auto,) = rows_from_catalysts([{**f, "ratified_by": "auto"}], W)
    (op,) = rows_from_catalysts([{**f, "ratified_by": "operator"}], W)
    assert feed.detail[-2:] == ("Source: doe_usaspending", "No operator ratification stored")
    assert auto.detail[-2:] == (
        "Source: doe_usaspending",
        "Applied automatically, not operator-ratified",
    )
    assert (
        op.detail[-1] == "Source: doe_usaspending, ratified by operator"
    )  # unchanged, no extra line
    for r in (feed, auto, op):  # still a catalyst on its event date, whoever ratified it
        assert (r.kind, r.form, r.date) == ("catalyst", "catalyst", date(2026, 10, 10))
    assert not any("ratified by" in line for line in (*feed.detail, *auto.detail))


def test_ratified_facts_are_dated_by_the_ratification_and_worded_by_what_they_state():
    stamped = utc(2026, 10, 21, 3, 0)  # 23:00 ET on Oct 20
    cash = {
        "cash_usd": Decimal("41200000"),
        "quarterly_burn_usd": Decimal("8100000"),
        "source": "ratified",
        "source_ref": "https://x.test/10q",
        "ratified_by": "operator",
        "valid_from": date(2026, 6, 30),
        "recorded_at": stamped,
    }
    shares = {**cash, "shares": Decimal("84200000")}
    mix = {**cash, "segment_label": "nuclear", "mix_pct": Decimal("61")}
    cash_pos = {**cash, "quarterly_burn_usd": Decimal("-2000000")}
    got = rows_from_ratified([cash, cash_pos], [shares], [mix], W)
    assert [r.headline for r in got] == [
        "cash $41.2M · burn $8.1M/qtr",
        "cash $41.2M · cash-positive quarter",
        "shares outstanding 84.2M",
        "revenue mix: nuclear 61%",
    ]
    assert all(r.kind == "ratified" and r.form == "ratified fact" for r in got)
    assert all(r.date == date(2026, 10, 20) for r in got)  # the ratification, in market time
    assert got[0].detail == (
        "Period or cover date: 2026-06-30",
        "Source: ratified, ratified by operator",
    )
    assert got[0].url == "https://x.test/10q"
    # a ratifier other than "operator" is still a ratifier; only empty and "auto" are not
    (other,) = rows_from_ratified([], [{**shares, "ratified_by": "jane"}], [], W)
    assert (other.form, other.date) == ("ratified fact", date(2026, 10, 20))
    assert other.detail[-1] == "Source: ratified, ratified by jane"


def stored_shares(**over) -> dict:
    """A 10-Q cover figure the machine applied: ``ratified_by`` auto, the cover date in ``valid_from``, stored
    on a later day (an ingest batch)."""
    return {
        "shares": Decimal("540600000"),
        "source": "10-q-cover",
        "source_ref": "0000876543-26-000044",
        "ratified_by": "auto",
        "valid_from": date(2026, 8, 1),
        "recorded_at": utc(2026, 10, 27, 14, 0),
        **over,
    }


def test_a_fact_nobody_ratified_is_a_stored_fact_dated_by_its_cover_date_not_its_store_day():
    (auto,) = rows_from_ratified([], [stored_shares()], [], W)
    (feed,) = rows_from_ratified([], [stored_shares(ratified_by=None)], [], W)
    for r in (auto, feed):
        # the wire kind stays "ratified"; the form says what it is
        assert (r.kind, r.form) == ("ratified", "stored fact")
        assert r.date == date(2026, 8, 1)  # the cover date, not the Oct 27 batch
        assert r.headline == "10-q-cover: shares outstanding 540.6M"  # led by the source as stored
        assert r.detail[0] == "Stored on 2026-10-27"
        assert r.detail[1] == "Source: 10-q-cover"  # no "ratified by" for a row nobody ratified
        assert r.accession == "0000876543-26-000044"
        assert r.url == filing_index_url("0000876543", "0000876543-26-000044")
    assert auto.detail[-1] == "Applied automatically, not operator-ratified"
    assert feed.detail[-1] == "No operator ratification stored"
    assert len(auto.detail) == 3  # store day, source, ratification: nothing else claimed
    (upper,) = rows_from_ratified([], [stored_shares(ratified_by=" AUTO ")], [], W)
    assert upper.form == "stored fact"  # the machine marker, however it is cased


def test_a_stored_fact_reads_the_same_in_all_three_tables_and_a_ratified_one_beside_it_does_not_change():
    stamped = utc(2026, 10, 21, 3, 0)  # 23:00 ET on Oct 20
    auto = {
        "cash_usd": Decimal("41200000"),
        "quarterly_burn_usd": Decimal("8100000"),
        "segment_label": "nuclear",
        "mix_pct": Decimal("61"),
        "source": "10-q-cover",
        "ratified_by": "auto",
        "valid_from": date(2026, 6, 30),
        "recorded_at": stamped,
    }
    got = rows_from_ratified([auto], [], [auto], W)
    assert [(r.form, r.date, r.headline) for r in got] == [
        ("stored fact", date(2026, 6, 30), "10-q-cover: cash $41.2M · burn $8.1M/qtr"),
        ("stored fact", date(2026, 6, 30), "10-q-cover: revenue mix: nuclear 61%"),
    ]
    both = rows_from_ratified([auto, {**auto, "ratified_by": "operator"}], [], [], W)
    assert [(r.form, r.date) for r in both] == [
        ("stored fact", date(2026, 6, 30)),
        (
            "ratified fact",
            date(2026, 10, 20),
        ),  # the operator's own, dated by the ratification as before
    ]


def test_a_stored_fact_with_gaps_is_still_a_row_and_says_what_it_does_not_know():
    # no source stated: no lead on the headline
    (bare,) = rows_from_ratified([], [stored_shares(source=None)], [], W)
    assert bare.headline == "shares outstanding 540.6M"
    assert bare.detail[1] == "Source: not stated"
    # no cover date: it falls back to the day it was stored, and says so
    (no_cover,) = rows_from_ratified([], [stored_shares(valid_from=None)], [], W)
    assert no_cover.date == date(2026, 10, 27)
    assert no_cover.detail[:2] == (
        "Stored on 2026-10-27",
        "No cover or filing date stated, so it is dated by the day it was stored",
    )
    # no store stamp: the cover date still dates it, and no store day is made up
    (no_stamp,) = rows_from_ratified([], [stored_shares(recorded_at=None)], [], W)
    assert no_stamp.date == date(2026, 8, 1)
    assert not any(line.startswith("Stored on") for line in no_stamp.detail)
    # neither date: nothing to date it by, so no row (never a guessed day)
    assert rows_from_ratified([], [stored_shares(valid_from=None, recorded_at=None)], [], W) == []
    # an operator-ratified fact with no store stamp is still not a row (nothing to date it by)
    assert (
        rows_from_ratified([], [stored_shares(ratified_by="operator", recorded_at=None)], [], W)
        == []
    )


def test_the_company_rows_carry_both_a_stored_and_a_ratified_fact_with_their_own_forms():
    pit = StubPit(
        events=[],
        insider=[],
        stakes=[],
        fundamentals=[],
        splits=[],
        dilution=[],
        catalysts=[],
        cash=[
            {
                "cash_usd": 5,
                "quarterly_burn_usd": 1,
                "source": "ratified",
                "ratified_by": "operator",
                "valid_from": date(2026, 6, 30),
                "recorded_at": utc(2026, 10, 20, 12),
            },
            {
                "cash_usd": 6,
                "quarterly_burn_usd": 1,
                "source": "10-q-cover",
                "ratified_by": "auto",
                "valid_from": date(2026, 6, 30),
                "recorded_at": utc(2026, 10, 27, 12),
            },
        ],
    )
    rows = rows_for_company(pit, W, clock="public")
    assert sorted((r.form, r.date) for r in rows) == [
        ("ratified fact", date(2026, 10, 20)),
        ("stored fact", date(2026, 6, 30)),
    ]
    assert {r.kind for r in rows} == {"ratified"}


# ---------------------------------------------------------------------------------------------------------
# one company, every source
# ---------------------------------------------------------------------------------------------------------


class StubPit(SimpleNamespace):
    """The accessors ``PointInTimeData`` serves, as plain lists."""

    def corporate_event_facts(self, sid):
        return self.events

    def insider_txns(self, sid):
        return self.insider

    def activist_stake_facts(self, sid):
        return self.stakes

    def fundamentals_facts(self, sid):
        return self.fundamentals

    def split_events(self, sid):
        return self.splits

    def dilution_facts(self, sid):
        return self.dilution

    def catalyst_facts(self, sid):
        return self.catalysts

    def cash_burn_facts(self, sid):
        return self.cash

    def shares_outstanding_facts(self, sid):
        return []

    def revenue_mix_facts(self, sid):
        return []

    def filing_index(self, sid):
        return getattr(self, "index", [])


def test_every_source_reaches_the_company_rows_with_the_4a_folded_once():
    pit = StubPit(
        events=[event()],
        insider=[
            txn(),
            txn(
                accession=A_ACC,
                accepted=utc(2026, 10, 22, 20, 0),
                amends_accession="0000876543-26-000009",
            ),
        ],
        stakes=[stake()],
        fundamentals=[PRIOR, NOW],
        splits=[split()],
        dilution=[dilution()],
        catalysts=[],
        cash=[],
    )
    rows = rows_for_company(pit, W, clock="public", amendments=[amendment()])
    kinds = sorted(r.kind for r in rows)
    assert kinds == sorted(
        ["8k", "stake", "form4", "form4a", "revenue", "revenue", "split", "dilution"]
    )
    assert [r.accession for r in rows if r.kind == "form4a"] == [A_ACC]  # one 4/A row, not two
    assert set(KIND_ORDER) >= set(kinds)


# ---------------------------------------------------------------------------------------------------------
# the EDGAR filing index (migration 0070): every other form, each filing once
# ---------------------------------------------------------------------------------------------------------

IDX_ACC = "0000876543-26-000050"
F4_ACC = "0000876543-26-000060"


def index_row(**over):
    base = {
        "form": "S-1",
        "accession": IDX_ACC,
        "filed": date(2026, 10, 21),
        "valid_from": date(2026, 10, 21),
        "primary_doc": "s1.htm",
        "accepted": None,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/876543/000087654326000050/{IDX_ACC}-index.htm",
    }
    return {**base, **over}


def test_an_index_row_is_its_form_and_primary_document_with_its_edgar_link():
    (r,) = rows_from_filing_index([index_row()], W)
    assert (r.kind, r.form, r.date, r.headline) == (
        "filing",
        "S-1",
        date(2026, 10, 21),
        "S-1 · s1.htm",
    )
    assert r.url == index_row()["source_ref"] and r.accession == IDX_ACC
    assert r.detail == () and r.tags == () and r.routine is False and r.usd is None
    # EDGAR left the document blank: just the form; no stored link: the EDGAR index built from the accession
    (bare,) = rows_from_filing_index(
        [index_row(form="424B5", primary_doc=None, source_ref=None)], W
    )
    assert bare.headline == "424B5" and bare.url == filing_index_url(W.cik, IDX_ACC)
    assert rows_from_filing_index([index_row(filed=None, valid_from=None)], W) == []  # undatable
    # the kind sits right after the revenue print and counts as an SEC filing (the "last filing" picks)
    assert KIND_ORDER.index("filing") == KIND_ORDER.index("revenue") + 1
    assert "filing" in SEC_FILING_KINDS


def test_each_filing_shows_ONCE_an_index_row_a_richer_row_carries_is_dropped():
    """An index row whose accession an 8-K, a Form 4, a 13D/G or a revenue print already carries is dropped
    (compared as stored, whitespace removed); a filing nothing else shows (the S-1) stays."""
    pit = StubPit(
        events=[event()],  # ACC
        insider=[txn(accession=F4_ACC)],
        stakes=[stake()],  # 0000555555-26-000003
        fundamentals=[NOW],  # 0000876543-26-000030
        splits=[],
        dilution=[],
        catalysts=[],
        cash=[],
        index=[
            index_row(form="8-K", accession=ACC),
            index_row(form="4", accession=f"  {F4_ACC} "),
            index_row(form="SC 13D", accession="0000555555-26-000003"),
            index_row(form="10-Q", accession="0000876543-26-000030"),
            index_row(),  # the lone S-1
        ],
    )
    rows = rows_for_company(pit, W, clock="public")
    assert [(r.form, r.accession) for r in rows if r.kind == "filing"] == [("S-1", IDX_ACC)]
    assert sorted(r.kind for r in rows if r.kind != "filing") == sorted(
        ["8k", "form4", "stake", "revenue"]
    )
    # with nothing richer, every index row is its own row
    alone = StubPit(
        events=[],
        insider=[],
        stakes=[],
        fundamentals=[],
        splits=[],
        dilution=[],
        catalysts=[],
        cash=[],
        index=[index_row(form="8-K", accession=ACC), index_row()],
    )
    assert sorted(r.form for r in rows_for_company(alone, W, clock="public")) == ["8-K", "S-1"]


def test_an_index_row_is_in_the_window_from_its_filing_date_and_never_after_the_asof():
    days = 90
    rows = rows_from_filing_index(
        [
            index_row(accession="0000876543-26-000071", filed=ASOF - timedelta(days=days)),
            index_row(accession="0000876543-26-000072", filed=ASOF - timedelta(days=days + 1)),
            index_row(accession="0000876543-26-000073", filed=ASOF),
            index_row(accession="0000876543-26-000074", filed=ASOF + timedelta(days=1)),
        ],
        W,
    )
    kept = in_window(
        [StreamRow(str(i), r, ("Core",), None, False) for i, r in enumerate(rows)], ASOF, days
    )
    assert sorted(r.row.accession for r in kept) == [
        "0000876543-26-000071",
        "0000876543-26-000073",
    ]


# ---------------------------------------------------------------------------------------------------------
# the roster, links, roles, window, day order, fold counts (pure)
# ---------------------------------------------------------------------------------------------------------

T1 = uuid.uuid4()
SX, SY, SZ = (uuid.uuid4() for _ in range(3))


def ms(ticker, sid, segment="Turbines", role=None, tid=T1) -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name="Grid",
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
        context_role=role,
    )


def fr(sid, ticker, kind, d, **over) -> FilingRow:
    base = dict(
        security_id=sid,
        ticker=ticker,
        kind=kind,
        form={"form4": "4"}.get(kind, kind),
        date=d,
        headline=f"{ticker} {kind}",
    )
    return FilingRow(**{**base, **over})


SEGMENTS = ["Turbines", "Cables", CONTEXT_LABEL, BENCH_LABEL]
ROSTER = [
    ms("XXX", SX, "Turbines"),
    ms("XXX", SX, "Cables"),  # the same company in two links
    ms("YYY", SY, CONTEXT_LABEL, role="payer"),
    ms("ZZZ", None, None),  # no resolved security, no link
    ms("OLD", uuid.uuid4(), "Old wires"),
]


def test_companies_are_once_each_with_their_links_folded_and_silent_ones_marked():
    cs = companies_of(ROSTER)
    assert [c.ticker for c in cs] == ["XXX", "YYY", "ZZZ", "OLD"]
    x, y, z, _ = cs
    assert x.links == ("Turbines", "Cables") and x.silent is False and x.role is None
    assert y.links == (CONTEXT_LABEL,) and y.silent is True and y.role == "payer"
    assert z.key == "ticker:ZZZ" and z.security_id is None and z.links == (None,)


def test_link_options_are_the_chain_in_order_then_old_links_no_link_then_context_and_bench():
    opts = link_options(SEGMENTS, companies_of(ROSTER))
    assert [(o.label, o.kind, o.names) for o in opts] == [
        ("Turbines", "link", 1),
        ("Cables", "link", 1),
        ("Old wires", "old_link", 1),
        ("No link", "no_link", 1),
        (CONTEXT_LABEL, "silent", 1),  # Bench has no company: not offered
    ]
    # a card's real link nobody sits in is still offered (with no names)
    assert link_options(["A", "B"], companies_of([ms("XXX", SX, "A")]))[1].names == 0


def test_roles_set_follow_the_enums_order():
    cs = companies_of([ms("A", SX, role="financier"), ms("B", SY, role="payer"), ms("C", SZ)])
    assert roles_set(cs) == ("payer", "financier")
    assert set(ContextRole) >= {ContextRole(r) for r in roles_set(cs)}


def test_the_stream_is_newest_day_first_then_kind_then_ticker():
    d1, d2 = date(2026, 10, 27), date(2026, 10, 20)
    rows = [
        fr(SX, "XXX", "form4", d1, routine=True),
        fr(SY, "YYY", "8k", d2),
        fr(SX, "XXX", "8k", d1),
        fr(SY, "YYY", "form4a", d1),
        fr(SY, "YYY", "stake", d1),
        fr(SX, "XXX", "ratified", d1),
        fr(SY, "YYY", "8k", d1),
    ]
    cs = companies_of([ms("XXX", SX), ms("YYY", SY, CONTEXT_LABEL)])
    got = stream_rows(
        {SX: [rows[0], rows[2], rows[5]], SY: [rows[1], rows[3], rows[4], rows[6]]}, cs
    )
    assert [(r.row.date, r.row.kind, r.row.ticker) for r in got] == [
        (d1, "8k", "XXX"),
        (d1, "8k", "YYY"),
        (d1, "stake", "YYY"),
        (d1, "form4", "XXX"),
        (d1, "form4a", "YYY"),
        (d1, "ratified", "XXX"),
        (d2, "8k", "YYY"),
    ]
    assert len({r.key for r in got}) == len(got)
    assert got[1].silent is True and got[0].silent is False
    # a row of a company not on the roster is never guessed at
    assert stream_rows({uuid.uuid4(): [rows[0]]}, cs) == ()
    assert sort_key(rows[0]) < sort_key(rows[1])  # newer sorts first


def test_the_window_is_inclusive_at_both_ends_and_never_reaches_past_the_asof():
    cs = companies_of([ms("XXX", SX)])
    days = [ASOF + timedelta(days=1), ASOF, ASOF - timedelta(days=30), ASOF - timedelta(days=31)]
    rows = stream_rows({SX: [fr(SX, "XXX", "8k", d) for d in days]}, cs)
    kept = in_window(rows, ASOF, 30)
    assert [r.row.date for r in kept] == [ASOF, ASOF - timedelta(days=30)]


def test_days_group_newest_first_and_count_the_rows_the_fold_covers():
    cs = companies_of([ms("XXX", SX)])
    d1, d2 = date(2026, 10, 27), date(2026, 10, 20)
    rows = stream_rows(
        {
            SX: [
                fr(SX, "XXX", "form4", d1, routine=True, accession="a"),
                fr(SX, "XXX", "form4", d1, routine=False, accession="b"),
                fr(SX, "XXX", "8k", d2),
            ]
        },
        cs,
    )
    days = group_by_day(rows)
    assert [(d.date, len(d.rows), d.routine) for d in days] == [(d1, 2, 1), (d2, 1, 0)]


def test_counts_cover_every_row_folded_or_not_and_every_kind_even_at_zero():
    cs = companies_of([ms("XXX", SX)])
    rows = stream_rows(
        {
            SX: [
                fr(SX, "XXX", "form4", ASOF, routine=True, accession="a"),
                fr(SX, "XXX", "form4", ASOF, routine=False, accession="b"),
                fr(SX, "XXX", "form4", ASOF, routine=True, accession="c"),
                fr(SX, "XXX", "8k", ASOF),
            ]
        },
        cs,
    )
    c = counts_of(rows)
    assert (c.rows, c.form4_purchases, c.routine) == (4, 1, 2)
    assert [k.kind for k in c.by_kind] == list(KIND_ORDER)
    assert {k.kind: k.rows for k in c.by_kind}["form4"] == 3
    assert {k.kind: k.rows for k in c.by_kind}["split"] == 0


def card_inputs():
    rows = {
        SX: [fr(SX, "XXX", "8k", ASOF), fr(SX, "XXX", "8k", ASOF - timedelta(days=100))],
        SY: [fr(SY, "YYY", "form4", ASOF - timedelta(days=5), routine=True)],
    }
    return stream_inputs(
        thesis_id=T1,
        thesis_name="Grid",
        segments=SEGMENTS,
        memberships=ROSTER,
        rows_by_sid=rows,
        roster_fallback=True,
    )


def test_the_card_inputs_count_the_roster_and_list_the_unresolved_names():
    inputs = card_inputs()
    assert (inputs.basket_rows, inputs.silent_rows, inputs.links_today) == (5, 1, 2)
    assert inputs.unresolved == ("ZZZ",)  # counted and listed, never in the stream
    assert inputs.roster_fallback is True and inputs.roles == ("payer",)
    assert len(inputs.rows) == 3


def test_a_window_slices_the_cached_rows_and_lists_a_name_with_none():
    inputs = card_inputs()
    p30 = filing_stream(inputs, asof=ASOF, days=30)
    p365 = filing_stream(inputs, asof=ASOF, days=365)
    assert p30.counts.rows == 2 and p365.counts.rows == 3
    assert p30.window_start == ASOF - timedelta(days=30)
    by = {n.company.ticker: n.rows for n in p30.names}
    assert by == {"XXX": 1, "YYY": 1, "ZZZ": 0, "OLD": 0}  # every roster name is listed
    assert [d.date for d in p365.groups] == [
        ASOF,
        ASOF - timedelta(days=5),
        ASOF - timedelta(days=100),
    ]
    with pytest.raises(ValueError):
        filing_stream(inputs, asof=ASOF, days=45)
    assert WINDOWS == (30, 90, 365) and READ_DAYS == 365


# ---------------------------------------------------------------------------------------------------------
# the lifted picker
# ---------------------------------------------------------------------------------------------------------


def test_card_choices_are_a_to_z_with_their_real_link_count_and_chain_wave_re_exports_them():
    from app.routers.dashboards import chain_wave as chain_router
    from dashboards import baskets
    from dashboards import chain_wave as chain_compute

    def card(name: str, links: list[str]) -> Thesis:
        return Thesis(
            id=uuid.uuid4(),
            name=name,
            narrative="x",
            segments=[Segment(label=s) for s in [*links, CONTEXT_LABEL, BENCH_LABEL]],
        )

    got = card_choices([card("grid", ["A", "B"]), card("Batteries", ["A"]), card("copper", [])])
    assert [(c.name, c.links) for c in got] == [("Batteries", 1), ("copper", 0), ("grid", 2)]
    # the Chain Wave keeps every moved name under its old name
    assert chain_compute.card_choices is baskets.card_choices
    assert chain_compute.CardChoice is baskets.CardChoice is CardChoice
    assert chain_compute.link_count is baskets.link_count
    assert chain_router.CardsRead is baskets.CardsRead is CardsRead
    assert chain_router.read_cards is baskets.picker_cards


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy reads stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0, "card_reads": 0}
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
        return card_inputs()

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_cards", fake_cards)
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_every_window_off_one_cached_read_on_the_requests_view(stub_client):
    client, seen = stub_client
    url = f"/dashboards/filing-stream/{T1}"
    body = client.get(url, params={"asof": "2026-10-28"}).json()
    assert body["cache"] == "computed" and body["days"] == 90 and body["windows"] == [30, 90, 365]
    assert body["clock"] == "public" and body["window_start"] == "2026-07-30"
    thesis_id, asof, known_at, clock = seen["view"]
    assert thesis_id == T1 and asof == ASOF and clock == "public" and known_at is not None
    for days, rows in ((30, 2), (365, 3), (90, 2)):
        again = client.get(url, params={"asof": "2026-10-28", "days": days}).json()
        assert again["cache"] == "hit" and again["days"] == days and again["counts"]["rows"] == rows
    assert seen["reads"] == 1 and seen["card_reads"] == 1

    client.get(url, params={"asof": market_today().isoformat()})
    assert seen["view"][2:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(
        f"/dashboards/filing-stream/{T1}", params={"asof": "2026-10-28", "days": 365}
    ).json()
    assert (body["thesis_name"], body["roster_fallback"], body["unresolved"]) == (
        "Grid",
        True,
        ["ZZZ"],
    )
    assert (body["basket_rows"], body["silent_rows"], body["links_today"]) == (5, 1, 2)
    assert [d["date"] for d in body["groups"]] == ["2026-10-28", "2026-10-23", "2026-07-20"]
    form4 = body["groups"][1]
    assert form4["routine"] == 1
    row = form4["rows"][0]
    assert (row["kind"], row["form"], row["ticker"], row["links"], row["role"], row["silent"]) == (
        "form4",
        "4",
        "YYY",
        [CONTEXT_LABEL],
        "payer",
        True,
    )
    assert (
        row["routine"] is True and row["tags"] == [] and row["detail"] == [] and row["url"] is None
    )
    assert [(k["kind"], k["rows"]) for k in body["counts"]["by_kind"]][:3] == [
        ("8k", 2),
        ("stake", 0),
        ("form4", 1),
    ]
    assert body["counts"]["routine"] == 1 and body["counts"]["form4_purchases"] == 0
    assert [link["label"] for link in body["links"]] == [
        "Turbines",
        "Cables",
        "Old wires",
        "No link",
        CONTEXT_LABEL,
    ]
    assert body["roles"] == ["payer"]
    assert [(n["ticker"], n["rows"]) for n in body["names"]] == [
        ("XXX", 2),
        ("YYY", 1),
        ("ZZZ", 0),
        ("OLD", 0),
    ]
    assert body["names"][2]["security_id"] is None and body["names"][2]["links"] == [None]


def test_the_router_404s_a_card_not_counted_at_the_asof(stub_client):
    client, seen = stub_client
    r = client.get(f"/dashboards/filing-stream/{uuid.uuid4()}", params={"asof": "2026-10-28"})
    assert r.status_code == 404 and seen["reads"] == 0


def test_the_router_rejects_an_unknown_window(stub_client):
    client, _ = stub_client
    r = client.get(f"/dashboards/filing-stream/{T1}", params={"asof": "2026-10-28", "days": 45})
    assert r.status_code == 422
    assert [int(d) for d in router_mod.FilingStreamDays] == list(WINDOWS)


def test_the_picker_list_is_cached(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/filing-stream/cards", params={"asof": "2026-10-28"}).json()
    assert [c["name"] for c in body["cards"]] == [
        "Grid",
        "Batteries",
    ]  # the stub's own order, as read
    assert (body["live_cards"], body["not_yet_created"], body["cache"]) == (3, 1, "computed")
    again = client.get("/dashboards/filing-stream/cards", params={"asof": "2026-10-28"}).json()
    assert again["cache"] == "hit" and seen["card_reads"] == 1


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a fact published after a past as-of never reaches the stream (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a past as-of
LATER = date(2026, 6, 12)  # a later past as-of, after every "late" fact below
INGESTED = utc(
    2026, 9, 1
)  # our own ingest instant, after both as-ofs: the public clock must not care
CIK = "0000876543"
EARLY_8K, LATE_8K = "0000876543-26-000101", "0000876543-26-000102"
EARLY_F4, LATE_F4 = "0000876543-26-000111", "0000876543-26-000112"
EARLY_4A, LATE_4A, HELD_4A = "0000876543-26-000121", "0000876543-26-000122", "0000876543-26-000123"


def _security(db, ticker: str, cik: str | None) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, 'equity', %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    return sid


def _amendment(db, sid: UUID, accession: str, filed: date, recorded_at: datetime) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO insider_amendment (tenant_id, security_id, accession, issuer_cik, owners, "
            "date_of_original_submission, remarks, rows, filed, accepted, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                DEFAULT_TENANT_ID,
                sid,
                accession,
                CIK,
                Json([{"cik": "0001111111", "name": "Jane Roe"}]),
                date(2026, 6, 1),
                f"remarks {accession}",
                Json([{"txn_code": "P"}]),
                filed,
                datetime.combine(filed, time(20, 0), tzinfo=UTC),
                recorded_at,
            ),
        )


def _seed_card(db) -> tuple[UUID, UUID]:
    sid = _security(db, "FSA", CIK)
    for acc, filed in ((EARLY_8K, date(2026, 6, 3)), (LATE_8K, date(2026, 6, 10))):
        append_fact(
            db,
            "fact_corporate_event",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                "form": "8-K",
                "items": ["5.02", "9.01"],
                "accession": acc,
                "filed": filed,
                "source_ref": f"https://www.sec.gov/Archives/edgar/data/876543/{acc.replace('-', '')}/",
                "valid_from": filed,
                "recorded_at": INGESTED,
            },
        )
    # Form 4s: the TRADE dates (valid_from) precede the acceptances; the late one is accepted after PAST
    for acc, traded, accepted in (
        (EARLY_F4, date(2026, 6, 2), utc(2026, 6, 3, 21, 0)),
        (LATE_F4, date(2026, 6, 9), utc(2026, 6, 10, 21, 0)),
    ):
        append_fact(
            db,
            "fact_insider_txn",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                "insider_name": "Jane Roe",
                "insider_role": "CEO",
                "txn_code": "P",
                "shares": 1000,
                "price": 4.0,
                "usd": 4000,
                "accession": acc,
                "txn_seq": 0,
                "valid_from": traded,
                "accepted": accepted,
                "recorded_at": INGESTED,
            },
        )
    _amendment(db, sid, EARLY_4A, date(2026, 6, 4), INGESTED)
    _amendment(db, sid, LATE_4A, date(2026, 6, 10), INGESTED)
    # filed long before the as-of but only RECORDED a day from now: the public clock does not read
    # recorded_at, the live record clock does
    _amendment(db, sid, HELD_4A, date(2026, 6, 4), datetime.now(UTC) + timedelta(days=1))
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Filing Stream fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="FSA", role="the name", security_id=sid, segment="Core")],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()
    return thesis.id, sid


def _accessions(body: dict, kind: str) -> list[str]:
    return sorted(r["accession"] for g in body["groups"] for r in g["rows"] if r["kind"] == kind)


def test_a_fact_published_after_a_past_asof_never_reaches_the_stream(db, pinned_today):
    """An 8-K, a Form 4 and a 4/A, each with an early copy (public by the earlier as-of) and a late one (public
    only after it). All six were INGESTED after both as-ofs: the public clock judges them on their own
    publication (filing date / SEC acceptance), never on our ingest, so the early ones are visible at the earlier
    as-of and the late ones appear only at the later one. A 4/A recorded in the future is visible on the public
    clock (filed in time) and hidden on the live record clock."""
    thesis_id, sid = _seed_card(db)
    url = f"/dashboards/filing-stream/{thesis_id}"
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        cards = client.get(
            "/dashboards/filing-stream/cards", params={"asof": PAST.isoformat()}
        ).json()
        past = client.get(url, params={"asof": PAST.isoformat(), "days": 365}).json()
        later = client.get(url, params={"asof": LATER.isoformat(), "days": 365}).json()
        live = client.get(url, params={"asof": pinned_today.isoformat(), "days": 365}).json()
        missing = client.get(
            f"/dashboards/filing-stream/{uuid.uuid4()}", params={"asof": PAST.isoformat()}
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    assert (
        any(c["thesis_id"] == str(thesis_id) for c in cards["cards"]) and missing.status_code == 404
    )
    assert (past["clock"], later["clock"], live["clock"]) == ("public", "public", "record")

    # the earlier as-of: only what was public by the end of June 5
    assert _accessions(past, "8k") == [EARLY_8K]
    assert _accessions(past, "form4") == [EARLY_F4]
    assert _accessions(past, "form4a") == sorted([EARLY_4A, HELD_4A])
    # the later as-of: everything filed by June 12
    assert _accessions(later, "8k") == sorted([EARLY_8K, LATE_8K])
    assert _accessions(later, "form4") == sorted([EARLY_F4, LATE_F4])
    assert _accessions(later, "form4a") == sorted([EARLY_4A, LATE_4A, HELD_4A])
    # the live record clock: the 4/A recorded in the future is not held yet
    assert _accessions(live, "form4a") == sorted([EARLY_4A, LATE_4A])
    assert _accessions(live, "8k") == sorted([EARLY_8K, LATE_8K])

    # nothing is dated after its own as-of, and the Form 4 sorts under its ACCEPTANCE (June 3), not its trade date
    for body, asof in ((past, PAST), (later, LATER)):
        assert all(g["date"] <= asof.isoformat() for g in body["groups"])
    f4 = next(r for g in past["groups"] for r in g["rows"] if r["kind"] == "form4")
    assert f4["date"] == "2026-06-03"
    assert (
        f4["url"] == f"https://www.sec.gov/Archives/edgar/data/876543/{EARLY_F4.replace('-', '')}/"
    )
    assert f4["headline"] == "Jane Roe (CEO): P purchase 1,000 sh @ $4.00 · $4,000"
    e8 = next(r for g in past["groups"] for r in g["rows"] if r["kind"] == "8k")
    assert e8["headline"] == (
        "8-K · 5.02 officer/director departure or appointment · 9.01 financial statements and exhibits"
    )
    a4 = next(r for g in past["groups"] for r in g["rows"] if r["accession"] == EARLY_4A)
    assert a4["headline"] == "4/A by Jane Roe amends the Form 4 filed 2026-06-01"
    assert (a4["ticker"], a4["links"], a4["silent"]) == ("FSA", ["Core"], False)
    # the roster note: no snapshot by the as-of, so today's roster stands in (counted, said)
    assert past["roster_fallback"] is True and past["unresolved"] == []


EARLY_S1, LATE_424 = "0000876543-26-000131", "0000876543-26-000132"


def test_an_index_filing_published_after_a_past_asof_never_reaches_the_stream(db, pinned_today):
    """The filing index (0070) on the same card: an S-1 filed June 2 is visible at the earlier as-of, a 424B5
    filed June 10 only at the later one — every row INGESTED after both as-ofs (the public clock reads the
    filing date, never our ingest). The index row for the stored 8-K is never shown: that filing is its 8-K row.
    """
    from ingest.edgar.filing_index import ingest_filing_index

    thesis_id, sid = _seed_card(db)
    rows = [
        {"form": "S-1", "accession": EARLY_S1, "primary_doc": "s1.htm", "filed": "2026-06-02"},
        {"form": "424B5", "accession": LATE_424, "primary_doc": "", "filed": "2026-06-10"},
        {"form": "8-K", "accession": EARLY_8K, "primary_doc": "k.htm", "filed": "2026-06-03"},
    ]
    ingest_filing_index(db, sid, CIK, rows, recorded_at=INGESTED)
    db.commit()
    url = f"/dashboards/filing-stream/{thesis_id}"
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get(url, params={"asof": PAST.isoformat(), "days": 365}).json()
        later = client.get(url, params={"asof": LATER.isoformat(), "days": 365}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    assert _accessions(past, "filing") == [EARLY_S1]
    assert _accessions(later, "filing") == sorted([EARLY_S1, LATE_424])
    assert _accessions(later, "8k") == sorted([EARLY_8K, LATE_8K])  # the 8-K shows as its 8-K row
    s1 = next(r for g in past["groups"] for r in g["rows"] if r["kind"] == "filing")
    assert (s1["form"], s1["headline"], s1["date"]) == ("S-1", "S-1 · s1.htm", "2026-06-02")
    assert s1["url"].endswith(f"/{EARLY_S1}-index.htm")
    p424 = next(r for g in later["groups"] for r in g["rows"] if r["accession"] == LATE_424)
    assert p424["headline"] == "424B5"
    by_kind = {c["kind"]: c["rows"] for c in later["counts"]["by_kind"]}
    assert by_kind["filing"] == 2


def test_the_4a_select_applies_the_two_clock_gate_on_its_own(db):
    """``read_amendments`` called DIRECTLY (no window filter after it): on the PUBLIC clock the filing must be
    dated by the as-of and ``recorded_at`` is never read (the 4/A recorded in the future is in); on the RECORD
    clock it must also have been recorded by ``known_at`` (it is out). The window's lower bound rides in the
    SELECT too, and the newest stored version of an accession is the one read."""
    from dashboards.filings import read_amendments
    from domain.market_time import known_at_for_asof

    _, sid = _seed_card(db)
    start = PAST - timedelta(days=365)

    def got(asof, clock, known_at, *, from_=start):
        out = read_amendments(db, [sid], asof, known_at, clock, start=from_)
        return sorted(r["accession"] for r in out.get(sid, []))

    assert got(PAST, "public", known_at_for_asof(PAST)) == sorted([EARLY_4A, HELD_4A])
    assert got(LATER, "public", known_at_for_asof(LATER)) == sorted([EARLY_4A, LATE_4A, HELD_4A])
    # the record clock: HELD_4A was recorded a day from now, so it is not held at a known_at of today
    now = datetime.now(UTC)
    assert got(LATER, "record", now) == sorted([EARLY_4A, LATE_4A])
    assert got(LATER, "record", None) == sorted([EARLY_4A, LATE_4A])  # the DB clock stands in
    # the lower bound: a window that starts after the early 4/A leaves it out (and nothing else)
    assert got(LATER, "public", known_at_for_asof(LATER), from_=date(2026, 6, 5)) == [LATE_4A]
    # the row carries what the page needs
    (row,) = [
        r
        for r in read_amendments(db, [sid], PAST, None, "public", start=start)[sid]
        if r["accession"] == EARLY_4A
    ]
    assert (row["filed"], row["day"], row["owners"][0]["name"]) == (
        date(2026, 6, 4),
        date(2026, 6, 4),
        "Jane Roe",
    )
    assert row["date_of_original_submission"] == date(2026, 6, 1) and len(row["rows"]) == 1


def test_a_4a_with_no_filing_date_is_dated_by_its_acceptance_then_by_when_we_first_held_it(db):
    """Never dropped, never early: ``filed`` first, else the acceptance in market time, else ``recorded_at``."""
    from dashboards.filings import read_amendments

    sid = _security(db, "FSB", CIK)
    stamps = {
        "0000876543-26-000201": (None, utc(2026, 6, 3, 2, 0), utc(2026, 9, 1)),  # 22:00 ET on Jun 2
        "0000876543-26-000202": (None, None, utc(2026, 6, 3, 15, 0)),  # nothing but when we held it
    }
    with db.cursor() as cur:
        for acc, (filed, accepted, recorded) in stamps.items():
            cur.execute(
                "INSERT INTO insider_amendment (tenant_id, security_id, accession, filed, accepted, "
                "recorded_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, sid, acc, filed, accepted, recorded),
            )
    db.commit()
    start = PAST - timedelta(days=30)
    out = read_amendments(db, [sid], PAST, None, "public", start=start)
    assert {r["accession"]: r["day"] for r in out[sid]} == {
        "0000876543-26-000201": date(2026, 6, 2),
        "0000876543-26-000202": date(2026, 6, 3),
    }
    # an earlier as-of sees only the first
    early = read_amendments(db, [sid], date(2026, 6, 2), None, "public", start=start)
    assert [r["accession"] for r in early[sid]] == ["0000876543-26-000201"]


def test_the_newest_stored_version_of_a_4a_is_the_one_read(db):
    from dashboards.filings import read_amendments

    sid = _security(db, "FSC", CIK)
    acc = "0000876543-26-000301"
    for remarks, recorded in (("first parse", utc(2026, 6, 6)), ("best parse", utc(2026, 9, 2))):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO insider_amendment (tenant_id, security_id, accession, remarks, filed, "
                "recorded_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, sid, acc, remarks, date(2026, 6, 4), recorded),
            )
    db.commit()
    out = read_amendments(db, [sid], PAST, None, "public", start=PAST - timedelta(days=30))
    assert [r["remarks"] for r in out[sid]] == [
        "best parse"
    ]  # a re-parse shares the filing's instant
    assert read_amendments(db, [], PAST, None, "public", start=PAST) == {}


def test_read_filing_rows_threads_the_view_into_every_source_and_names_every_company(db):
    from dashboards.filings import read_filing_rows
    from domain.market_time import known_at_for_asof

    _, sid = _seed_card(db)
    other = _security(db, "FSZ", None)  # a company with no filings and no CIK
    db.commit()
    got = read_filing_rows(
        db, [sid, other], PAST, known_at_for_asof(PAST), "public", days=365, tickers={sid: "CARD"}
    )
    assert set(got) == {sid, other} and got[other] == ()  # every company has an entry
    rows = got[sid]
    assert {r.ticker for r in rows} == {"CARD"}  # the card's own ticker, not the master's
    assert sorted(r.accession for r in rows if r.kind == "8k") == [EARLY_8K]
    assert sorted(r.accession for r in rows if r.kind == "form4") == [EARLY_F4]
    assert all(r.date <= PAST for r in rows)
    assert read_filing_rows(db, [], PAST, None, "public", days=30) == {}


# ---------------------------------------------------------------------------------------------------------
# real-shaped rows: a stored row can state less than the plan assumed (found on dev with a prod copy)
# ---------------------------------------------------------------------------------------------------------


def test_a_price_against_a_zero_share_count_states_no_mean_price_instead_of_dividing_by_zero():
    """The first 500 on dev: a real code-P row with ``shares == 0`` and a price made the shares-weighted mean
    price divide by a zero weight. A mean needs a positive weight; without one the price is left out.
    """
    (zero,) = rows_from_insider_txns([txn("P", shares=0, price=4.0)], W)
    assert zero.headline == "Jane Roe (CEO): P purchase 0 sh"
    assert (
        zero.detail[0] == "2026-10-01 · P · 0 sh @ $4.00 · $0"
    )  # the row's own stated numbers, as stated
    assert zero.routine is False and zero.usd == 0.0
    # every row of the filing states zero shares
    (all_zero,) = rows_from_insider_txns([txn("S", 0, 5.0), txn("S", 0, 6.0)], W)
    assert all_zero.headline == "Jane Roe (CEO): S sale 0 sh" and all_zero.routine is True
    # a zero-share row beside a real one adds no weight: the mean is the real row's price
    (mixed,) = rows_from_insider_txns([txn("P", 0, 9.0), txn("P", 1000, 5.0)], W)
    assert mixed.headline == "Jane Roe (CEO): P purchase 1,000 sh @ $5.00 · $5,000"


def test_a_form4_row_may_state_shares_or_dollars_alone_and_the_phrase_says_only_what_it_states():
    (usd_only,) = rows_from_insider_txns([txn("P", shares=None, price=None, usd=1234)], W)
    assert usd_only.headline == "Jane Roe (CEO): P purchase · $1,234" and usd_only.usd == 1234.0
    (priced_only,) = rows_from_insider_txns([txn("P", shares=None, price=4.0, usd=None)], W)
    assert priced_only.headline == "Jane Roe (CEO): P purchase" and priced_only.usd is None
    (shares_only,) = rows_from_insider_txns([txn("P", shares=500, price=None, usd=None)], W)
    assert shares_only.headline == "Jane Roe (CEO): P purchase 500 sh" and shares_only.usd is None
    # a stated price of exactly zero (a grant) reads $0.00, not $0.0000
    (grant,) = rows_from_insider_txns([txn("A", 5000, 0.0)], W)
    assert grant.headline == "Jane Roe (CEO): A grant/award 5,000 sh @ $0.00"


def test_a_non_finite_stored_number_is_read_as_not_stated():
    for bad in (float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity"), "abc", True):
        (r,) = rows_from_insider_txns([txn("P", shares=bad, price=bad, usd=bad)], W)
        assert r.headline == "Jane Roe (CEO): P purchase" and r.usd is None


def test_a_filings_instants_may_mix_aware_and_naive_stamps():
    naive = datetime(2026, 10, 3, 2, 0)  # read as UTC: 22:00 ET on Oct 2
    (r,) = rows_from_insider_txns(
        [txn(accepted=naive), txn(accepted=utc(2026, 10, 9, 14, 0), txn_seq=1)], W
    )
    assert r.date == date(2026, 10, 2)
    (n,) = rows_from_insider_txns([txn(accepted=None, recorded_at=datetime(2026, 10, 6, 15, 0))], W)
    assert n.date == date(2026, 10, 6)


@pytest.mark.parametrize("shares", [None, 0, Decimal("0"), 7, float("nan"), Decimal("NaN")])
@pytest.mark.parametrize("price", [None, 0, 0.0, 4.5, float("inf")])
@pytest.mark.parametrize("usd", [None, 0, 100, Decimal("NaN")])
@pytest.mark.parametrize("code", ["P", "S", "J", None])
def test_no_form4_row_shape_can_raise(shares, price, usd, code):
    """Every combination of stated, zero, missing and non-finite numbers builds a row (the summary's arithmetic
    is total over what a row can hold); a bug that raises must stay visible, so nothing here is caught.
    """
    row = {**txn("P"), "txn_code": code, "shares": shares, "price": price, "usd": usd}
    rows = rows_from_insider_txns([row], W)
    assert len(rows) == 1 and rows[0].headline.startswith("Jane Roe (CEO):")


# a row that states nothing but its accession (or nothing at all) goes through EVERY normalizer
BARE_ROWS = [{}, {"accession": ACC}, {"accession": ACC, "valid_from": None, "filed": None}]


@pytest.mark.parametrize("bare", BARE_ROWS)
def test_a_row_that_states_nothing_but_its_accession_never_raises_in_any_normalizer(bare):
    assert isinstance(rows_from_corporate_events([bare], W), list)
    assert isinstance(rows_from_insider_txns([bare], W), list)
    assert isinstance(rows_from_amendments([bare], W), list)
    assert isinstance(rows_from_stakes([bare], W), list)
    assert isinstance(rows_from_revenue([{**bare, "metric_key": "revenue"}], W), list)
    assert isinstance(rows_from_splits([bare], W, clock="public"), list)
    assert isinstance(rows_from_splits([bare], W, clock="record"), list)
    assert isinstance(rows_from_dilution([bare], W), list)
    assert isinstance(rows_from_catalysts([bare], W), list)
    assert isinstance(rows_from_ratified([bare], [bare], [bare], W), list)
    assert isinstance(rows_from_filing_index([bare], W), list)
    pit = StubPit(
        events=[bare], insider=[bare], stakes=[bare], fundamentals=[bare], splits=[bare],
        dilution=[bare], catalysts=[bare], cash=[bare], index=[bare],
    )  # fmt: skip
    assert isinstance(rows_for_company(pit, W, clock="public", amendments=[bare]), list)


def test_rows_that_can_be_dated_but_state_nothing_else_still_make_a_row():
    d = date(2026, 10, 1)
    (ev,) = rows_from_corporate_events([{"accession": ACC, "filed": d}], W)
    assert ev.headline == "8-K · items not yet resolved" and ev.tags == (TAG_ITEMS_PENDING,)
    (st,) = rows_from_stakes([{"accession": ACC, "filed": d}], W)
    assert st.headline == "13D/G · filer not named" and st.tags == (TAG_FILER_UNRESOLVED,)
    (am,) = rows_from_amendments([{"accession": A_ACC, "day": d}], W)
    assert am.headline == "4/A amends an earlier Form 4" and am.detail == (
        "0 transaction rows as filed",
    )
    (di,) = rows_from_dilution([{"accession": ACC, "valid_from": d}], W)
    assert di.headline == "dilution" and di.usd is None
    (ca,) = rows_from_catalysts([{"valid_from": d}], W)
    assert ca.headline == "catalyst: no label stated" and ca.detail == (
        "Type: catalyst",
        "Source: not stated",
        "No operator ratification stored",
    )
    # no ratified_by: a stored fact, dated by the store day when it states no cover date
    (ra,) = rows_from_ratified(
        [{"cash_usd": 5, "quarterly_burn_usd": 1, "recorded_at": utc(2026, 10, 1, 12)}], [], [], W
    )
    assert (ra.form, ra.date) == ("stored fact", date(2026, 10, 1))
    assert ra.headline == "cash $5 · burn $1/qtr"
    assert ra.detail == (
        "Stored on 2026-10-01",
        "No cover or filing date stated, so it is dated by the day it was stored",
        "Source: not stated",
        "No operator ratification stored",
    )
    (op,) = rows_from_ratified(
        [
            {
                "cash_usd": 5,
                "quarterly_burn_usd": 1,
                "ratified_by": "operator",
                "recorded_at": utc(2026, 10, 1, 12),
            }
        ],
        [],
        [],
        W,
    )
    assert (op.form, op.detail) == ("ratified fact", ("Source: ratified by operator",))


def test_the_year_over_year_and_overhang_rules_refuse_a_zero_or_missing_basis():
    zero_prior = rev(date(2025, 6, 30), 0, "0000876543-25-000036", date(2025, 8, 5))
    nan_prior = rev(date(2025, 6, 30), Decimal("NaN"), "0000876543-25-000037", date(2025, 8, 5))
    assert "YoY" not in rows_from_revenue([zero_prior, NOW], W)[1].headline
    got = rows_from_revenue(
        [nan_prior, NOW], W
    )  # a NaN value states no number: that row is not a row
    assert [r.accession for r in got] == [NOW["accession"]] and "YoY" not in got[0].headline
    for hole in (
        {"principal_total_usd": 0},
        {"principal_total_usd": Decimal("NaN")},
        {"shares_outstanding": Decimal("NaN")},
        {"terms": {"conversion_rate": 0}},
        {"terms": {"conversion_rate": "abc"}},
        {"terms": None},
    ):
        assert convert_overhang_pct(dilution(**hole)) is None
        assert len(rows_from_dilution([dilution(**hole)], W)) == 1


@pytest.mark.parametrize(
    "value,words",
    [
        (0, "$0"),
        (999.4, "$999"),
        (999.5, "$1.0K"),  # a whole number that rounds to 1,000 moves up
        (1_000, "$1.0K"),
        (845_000, "$845.0K"),
        (999_949, "$999.9K"),
        (999_950, "$1.0M"),
        (41_200_000, "$41.2M"),
        (999_949_999, "$999.9M"),
        (999_950_000, "$1.0B"),
        (1_260_000_000, "$1.3B"),
        (2_000_000_000_000, "$2,000.0B"),  # past the top unit the figure stays in billions
        (-999_950, "-$1.0M"),
        (-999_949, "-$999.9K"),
    ],
)
def test_compact_figures_round_to_one_decimal_and_roll_to_the_next_unit_at_1000(value, words):
    """THE ROLLOVER RULE: a figure is rounded to what is shown (a whole number below a thousand, one decimal above)
    and one that rounds up to 1,000 moves to the next unit, so 999,949 is "$999.9K" and 999,950 is "$1.0M",
    never "$1000.0K"."""
    assert fmt_compact(value) == words
    assert fmt_compact(abs(value), dollars=False) == words.lstrip("-").replace("$", "")


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a real zero-share Form 4 row reaches the stream instead of a 500
# ---------------------------------------------------------------------------------------------------------


def test_a_zero_share_form4_row_reaches_the_stream_instead_of_a_500(db):
    thesis_id, sid = _seed_card(db)
    zero_acc = "0000876543-26-000131"
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": "Zed Zero",
            "insider_role": "Director",
            "txn_code": "P",
            "shares": 0,
            "price": 4.0,
            "usd": 0,
            "accession": zero_acc,
            "txn_seq": 0,
            "valid_from": date(2026, 6, 2),
            "accepted": utc(2026, 6, 3, 21, 0),
            "recorded_at": INGESTED,
        },
    )
    db.commit()
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get(
            f"/dashboards/filing-stream/{thesis_id}", params={"asof": PAST.isoformat(), "days": 365}
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200
    row = next(x for g in r.json()["groups"] for x in g["rows"] if x["accession"] == zero_acc)
    assert row["headline"] == "Zed Zero (Director): P purchase 0 sh"
    assert row["kind"] == "form4" and row["routine"] is False
