"""``pipeline.backfill_splits`` end to end on a real DB, over the REAL Polygon pages and the REAL filings and
submissions index in ``tests/fixtures/splits`` (cache-only: no network). Every idempotency claim COUNTS THE
TABLE (``count(*)``), never the read — the read dedups, so a duplicate append would hide behind it.

The basket (``tests/split_fixtures.UNION``): BTOC / LRHC / OPAD / GGRP (8-K filers, with the item rows the
nightly stores) and MI (a 6-K filer, read from its submissions index)."""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.splits import (
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    VIA_AUTO,
    is_applicable,
)
from ingest.edgar.client import EdgarClient
from ingest.edgar.submissions import parse_item_codes
from pipeline import backfill_splits as bf
from repositories import split_history_repo, split_repo
from tests.split_801_fixtures import (
    ENHA_8K,
    ENHA_FIRST_SESSION,
    ENHA_POLYGON_DAY,
    OLD_ITEMS,
    enha_polygon,
    seed_enha,
    seed_member,
)
from tests.split_801_fixtures import SINCE as SINCE_8_01
from tests.split_fixtures import (
    FX,
    edgar_cache,
    list_polygon_source,
    polygon_source,
    seed_union,
)

SINCE = date(2025, 8, 1)  # the fixture pages' own query
ASOF = date(2026, 10, 20)


def _run(db, tmp_path, *, apply: bool, asof: date = ASOF, tickers=None):
    polygon, calls = polygon_source(tmp_path)
    edgar = EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False)
    return (
        bf.backfill_splits(
            db,
            polygon=polygon,
            edgar=edgar,
            since=SINCE,
            asof=asof,
            apply=apply,
            allow_live=True,  # the fake Polygon socket; the EDGAR client is cache-only
            tickers=tickers,
        ),
        calls,
    )


def _by(result) -> dict[tuple[str, date], bf.PlannedSplit]:
    return {(p.member.ticker, p.decision.key_date): p for p in result.planned}


def test_the_dry_run_plans_every_split_and_writes_nothing(db, tmp_path):
    seed_union(db)
    before = split_repo.count_rows(db)
    result, calls = _run(db, tmp_path, apply=False)
    db.rollback()
    assert split_repo.count_rows(db) == before == 0  # a dry run writes NOTHING
    plan = _by(result)
    # BTOC / OPAD / LRHC 09-08 / GGRP (priced as BTLN): the 8-K and Polygon agree exactly -> auto-confirmed
    for key in [
        ("BTOC", date(2026, 10, 15)),
        ("OPAD", date(2026, 6, 9)),
        ("LRHC", date(2026, 9, 8)),
        ("GGRP", date(2026, 9, 28)),
    ]:
        p = plan[key]
        assert (p.decision.status, p.decision.confirmed_via) == (STATUS_CONFIRMED, VIA_AUTO), key
        assert p.action == bf.APPEND
    assert plan[("GGRP", date(2026, 9, 28))].decision.polygon.ticker == "BTLN"
    assert plan[("LRHC", date(2026, 9, 8))].decision.ratio_from == Decimal(6)
    # LRHC's two earlier Polygon rows: no stored 8-K near them and its index is not cached -> review
    for d in (date(2026, 1, 26), date(2026, 4, 20)):
        p = plan[("LRHC", d)]
        assert p.decision.status == STATUS_NEEDS_REVIEW and p.decision.sec is None
        assert "filing index could not be read" in p.decision.note
    # MI: its window holds the range-only proxy (abstain) + 3 6-Ks not in the cache -> review, both counted
    mi = plan[("MI", date(2026, 5, 18))]
    assert mi.decision.status == STATUS_NEEDS_REVIEW
    assert mi.decision.sec.accession == "0001213900-26-046153"
    assert (mi.filings_read, mi.filings_unread) == (3, 3)
    assert result.count(bf.APPEND) == len(result.planned) == 7
    # Polygon was asked for dates only
    assert all("ticker" not in u for u in calls)


def test_apply_appends_the_plan_and_a_rerun_appends_nothing(db, tmp_path):
    seed_union(db)
    first, _ = _run(db, tmp_path, apply=True)
    assert first.applied and first.appended == 7
    assert (first.rows_before, first.rows_after) == (0, 7)
    assert split_repo.count_rows(db) == 7
    # one version per split, all stamped by the DB in the one transaction
    stored = split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID)
    assert len(stored) == 7 and len({v.recorded_at for v in stored}) == 1
    assert all(v.author == split_repo.AUTHOR_BACKFILL for v in stored)
    # the re-run: the same inputs -> 0 appended; the TABLE is unchanged
    again, _ = _run(db, tmp_path, apply=True)
    assert again.appended == 0 and again.count(bf.UNCHANGED) == 7
    assert split_repo.count_rows(db) == 7


def test_an_operator_decision_is_never_overwritten(db, tmp_path):
    seed_union(db)
    _run(db, tmp_path, apply=True)
    mi = next(
        v for v in split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID) if v.ticker == "MI"
    )
    split_repo.reject(db, mi.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert split_repo.count_rows(db) == 8
    again, _ = _run(db, tmp_path, apply=True)
    assert again.appended == 0
    assert _by(again)[("MI", date(2026, 5, 18))].action == bf.OPERATOR_KEPT
    assert split_repo.count_rows(db) == 8
    now = {v.ticker: v for v in split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID)}
    assert now["MI"].status == STATUS_REJECTED


def test_new_knowledge_appends_a_new_version(db, tmp_path):
    """Run the night before BTOC's split (it is ANNOUNCED), then on the 20th (it took effect): the second run
    appends exactly that one change, as a new version — the first stays on the table."""
    seed_union(db)
    early, _ = _run(db, tmp_path, apply=True, asof=date(2026, 10, 14))
    assert _by(early)[("BTOC", date(2026, 10, 15))].decision.status == "announced"
    late, _ = _run(db, tmp_path, apply=True)
    p = _by(late)[("BTOC", date(2026, 10, 15))]
    assert late.appended == 1 and p.changed == ("status", "note")
    assert split_repo.count_rows(db) == 8
    hist = split_repo.history(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=p.member.security_id,
        key_date=p.decision.key_date,
    )
    assert [v.status for v in hist] == ["announced", "confirmed"]


def test_a_ticker_filter_scopes_the_run(db, tmp_path):
    seed_union(db)
    result, _ = _run(db, tmp_path, apply=False, tickers=["btoc"])
    assert {p.member.ticker for p in result.planned} == {"BTOC"} and result.members == 1


def test_the_report_names_both_sources_and_the_verdict(db, tmp_path):
    seed_union(db)
    result, _ = _run(db, tmp_path, apply=False)
    buf = io.StringIO()
    bf.report(result, out=buf)
    text = buf.getvalue()
    assert (
        "BTOC  1-for-16 reverse split  first session 2026-10-15  CONFIRMED (auto:sec+polygon)"
        in text
    )
    assert "SEC 8-K 0001213900-26-108468 filed 2026-10-09: 16 -> 1, trading 2026-10-15" in text
    assert "SUMMARY: would append 7" in text


def test_no_polygon_key_is_the_off_switch(monkeypatch, capsys):
    monkeypatch.setattr(bf, "default_split_source", lambda: None)
    bf.main([])
    assert "OFF — no POLYGON_API_KEY" in capsys.readouterr().out


def test_a_future_since_is_refused(monkeypatch):
    monkeypatch.setattr(bf, "default_split_source", lambda: object())
    with pytest.raises(SystemExit) as e:
        bf.main(["--since", "2099-01-01"])
    assert e.value.code == bf.EXIT_REFUSED


# --- Item 8.01 / 7.01 8-Ks (2026-10-23): ENHA, the split the reader missed ----------------------------------


def _enha_run(db, tmp_path, *, apply: bool, asof: date):
    return bf.backfill_splits(
        db,
        polygon=enha_polygon(tmp_path, today=asof),
        edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        since=SINCE_8_01,
        asof=asof,
        apply=apply,
        allow_live=True,  # the fake Polygon socket; the EDGAR client is cache-only
        tickers=["ENHA"],
    )


def _enha_as_prod_holds_it(db, tmp_path, monkeypatch) -> None:
    """The version prod holds since 2026-10-20: the backfill, reading only items 3.03 / 5.03 (and no wide forms
    — the targeted reader came on 2026-10-24), saw none of ENHA's 8-Ks — Polygon's 1-for-10 on 10-28, needs
    review, no SEC side."""
    with monkeypatch.context() as m:
        m.setattr(bf, "SPLIT_8K_ITEMS", OLD_ITEMS)
        m.setattr(bf, "WIDE_FORMS", frozenset())
        r = _enha_run(db, tmp_path, apply=True, asof=date(2026, 10, 20))
    (p,) = r.planned
    assert (p.decision.status, p.decision.sec, p.filings_read) == (STATUS_NEEDS_REVIEW, None, 0)
    assert p.decision.note == (
        "Polygon only — no SEC filing read in the window states this split (0 filing(s) read)."
    )


def test_an_item_8_01_8k_attaches_its_reading_to_the_listed_split(db, tmp_path, monkeypatch):
    """ENHA's Item 8.01 8-K states the 1-for-10 and its first trading session, 2026-10-29. Read now, it
    attaches to the split already listed (the backfill wrote its newest version, still needs review): ONE new
    version carrying the SEC side, Polygon's values kept until the operator decides — the two sources are one
    session apart, so review offers both. A re-run appends nothing (the TABLE is counted)."""
    sid = seed_enha(db)
    _enha_as_prod_holds_it(db, tmp_path, monkeypatch)
    assert split_repo.count_rows(db) == 1
    r = _enha_run(db, tmp_path, apply=True, asof=date(2026, 10, 23))
    (p,) = r.planned
    assert (p.action, r.appended, split_repo.count_rows(db)) == (bf.APPEND, 1, 2)
    assert {"sec_accession", "sec_effective_date", "sec_date_kind", "note"} <= set(p.changed)
    # the three 8.01 / 7.01 8-Ks in the window are read; only one states a split
    assert (p.filings_read, p.filings_unread) == (3, 0)
    (v,) = split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[sid])
    assert (v.key_date, v.status, v.value_source) == (
        ENHA_POLYGON_DAY,
        STATUS_NEEDS_REVIEW,
        "polygon",
    )
    assert (v.effective_date, v.ratio_from, v.ratio_to) == (
        ENHA_POLYGON_DAY,
        Decimal(10),
        Decimal(1),
    )
    assert (v.sec_accession, v.sec_form, v.sec_filed) == (ENHA_8K, "8-K", date(2026, 10, 19))
    assert (v.sec_effective_date, v.sec_date_kind) == (ENHA_FIRST_SESSION, "trading")
    assert (v.sec_ratio_from, v.sec_ratio_to) == (Decimal(10), Decimal(1))
    assert v.valid_from == date(2026, 10, 19)  # public from the 8-K, not from Polygon's date
    assert "the SEC filing states the first session 2026-10-29, Polygon 2026-10-28" in v.note
    assert split_repo.confirm_options(v) == ["sec", "polygon"]
    assert split_repo.confirm_values(v, "sec") == (Decimal(10), Decimal(1), ENHA_FIRST_SESSION)
    # the same inputs again: nothing appended, the TABLE does not grow
    again = _enha_run(db, tmp_path, apply=True, asof=date(2026, 10, 23))
    assert (again.appended, again.count(bf.UNCHANGED), split_repo.count_rows(db)) == (0, 1, 2)


@pytest.mark.parametrize("today", [date(2026, 10, 23), date(2026, 10, 28)])
def test_confirming_the_sec_values_lists_enha_announced_for_its_real_first_session(
    db, tmp_path, monkeypatch, today
):
    """The operator's "Confirm SEC values" on any day before 10-29 re-dates the split to the session the 8-K
    states and lands it ANNOUNCED (not in force until 10-29); the backfill never touches it again.
    """
    sid = seed_enha(db)
    _enha_as_prod_holds_it(db, tmp_path, monkeypatch)
    _enha_run(db, tmp_path, apply=True, asof=date(2026, 10, 23))
    (v,) = split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[sid])
    c = split_repo.confirm(db, v.id, source="sec", tenant_id=DEFAULT_TENANT_ID, today=today)
    db.commit()
    assert (c.key_date, c.effective_date, c.status) == (
        ENHA_POLYGON_DAY,
        ENHA_FIRST_SESSION,
        STATUS_ANNOUNCED,
    )
    assert (c.confirmed_via, c.value_source, c.author) == ("operator", "sec", "operator")
    assert not is_applicable(c.status, c.effective_date, on=date(2026, 10, 28))
    assert is_applicable(c.status, c.effective_date, on=ENHA_FIRST_SESSION)
    n = split_repo.count_rows(db)
    again = _enha_run(db, tmp_path, apply=True, asof=today)
    assert (again.appended, again.count(bf.OPERATOR_KEPT), split_repo.count_rows(db)) == (0, 1, n)


# --- 2026-10-24: how each filing is read, and the targeted wide-form reader ---------------------------------


@pytest.mark.parametrize(
    ("form", "items", "screen"),
    [
        ("6-K", None, bf.SCREEN_RESTATED),
        ("6-K/A", None, bf.SCREEN_RESTATED),
        ("8-K", ("3.03", "5.03", "9.01"), bf.SCREEN_RESTATED),  # a charter 8-K (MEDS's)
        ("8-K", None, bf.SCREEN_RESTATED),  # items not resolved yet
        ("8-K", ("8.01", "9.01"), bf.SCREEN_CATCH_ALL),  # unchanged since 2026-10-23
        ("8-K/A", ("7.01", "9.01"), bf.SCREEN_CATCH_ALL),
        ("1-A/A", None, bf.SCREEN_NONE),
        ("10-Q", None, bf.SCREEN_WIDE),
        ("F-6 POS", None, bf.SCREEN_WIDE),
        ("DEF 14C", None, bf.SCREEN_WIDE),
    ],
)
def test_each_filing_is_read_its_way(form, items, screen):
    assert bf.screen_of(bf.FilingRef("a", form, date(2026, 1, 1), items)) == screen


DFNS_CIK = "0001787518"
DFNS_10Q = "0001185185-26-003604"  # filed 2026-08-18: 1-for-125, first session 2026-07-20
LIST_ASOF = date(2026, 10, 24)


def _list_run(db, tmp_path, cache, *, tickers, apply=False):
    return bf.backfill_splits(
        db,
        polygon=list_polygon_source(tmp_path, today=LIST_ASOF),
        edgar=EdgarClient(cache_dir=cache, allow_live=False),
        since=date(2025, 3, 1),
        asof=LIST_ASOF,
        apply=apply,
        allow_live=True,  # the fake Polygon socket; the EDGAR client is cache-only
        tickers=tickers,
    )


def _dfns_8ks() -> list[tuple[str, str, date, list]]:
    """DFNS's 8-Ks in the S1 window of its 2026-07-20 split, as its REAL submissions index lists them."""
    subs = json.loads((FX / "submissions" / f"CIK{DFNS_CIK}.json").read_text(encoding="utf-8"))
    r = subs["filings"]["recent"]
    out = []
    for i, form in enumerate(r["form"]):
        filed = date.fromisoformat(r["filingDate"][i])
        if form == "8-K" and date(2026, 3, 22) <= filed <= date(2026, 7, 30):
            out.append(
                (r["accessionNumber"][i], form, filed, parse_item_codes(r["items"][i]) or [])
            )
    return out


def test_dfns_is_confirmed_by_the_8k_that_revised_its_ratio(db, tmp_path):
    """DFNS: Polygon lists 1-for-125 on 2026-07-20. Its 8-K of 07-13 states 1-for-50 (superseded); the 8-K of
    07-16 raised it to 1-for-125 and restated the 1-for-50 beside it, so the reader abstained ("two ratios") and
    the list carried the 07-13 filing, disagreeing. It now reads the revised ratio: the two sources agree.
    """
    seed_member(db, "DFNS", DFNS_CIK, _dfns_8ks())
    r = _list_run(db, tmp_path, edgar_cache(tmp_path), tickers=["DFNS"])
    (p,) = r.planned
    d = p.decision
    assert (d.status, d.confirmed_via) == (STATUS_CONFIRMED, VIA_AUTO)
    assert (d.ratio_from, d.ratio_to, d.effective_date) == (
        Decimal(125),
        Decimal(1),
        date(2026, 7, 20),
    )
    assert (d.sec.accession, d.sec.form) == ("0001213900-26-078551", "8-K")
    assert d.valid_from == date(2026, 7, 16)  # public from the filing
    assert p.wide_read == 0  # its own window agreed: the wide forms are never read


def test_a_wide_form_is_the_second_source_when_the_window_holds_none(db, tmp_path):
    """The targeted reader: with none of DFNS's 8-Ks readable (a reader that never saw them), its S1 window holds
    no agreement — so the company's wide forms filed [first session - 60, + 120] are read once, and its 10-Q
    (filed 08-18: "The Reverse Stock Split became effective …", 1-for-125, first session 07-20) agrees exactly:
    auto-confirmed by S1's own rule. The periodic report is read from its PRIMARY document alone."""
    seed_member(db, "DFNS", DFNS_CIK, [])
    cache = edgar_cache(tmp_path, only={DFNS_10Q}, submissions={DFNS_CIK})
    r = _list_run(db, tmp_path, cache, tickers=["DFNS"], apply=True)
    (p,) = r.planned
    d = p.decision
    assert (d.status, d.confirmed_via, d.sec.accession, d.sec.form) == (
        STATUS_CONFIRMED,
        VIA_AUTO,
        DFNS_10Q,
        "10-Q",
    )
    assert d.valid_from == date(
        2026, 7, 20
    )  # the 10-Q came after the first session: public from it
    assert "could not be read" in d.note  # the window's unread 8-Ks are named, never hidden
    assert (
        p.wide_read == 1 and p.wide_unread == 3
    )  # its three 424B3s in the window are not cached here
    assert (cache / "forms" / DFNS_10Q / "t3def10q063026.htm").exists()
    (v,) = split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID)
    assert (v.status, v.sec_form, v.sec_effective_date) == ("confirmed", "10-Q", date(2026, 7, 20))
    again = _list_run(db, tmp_path, cache, tickers=["DFNS"], apply=True)
    assert (again.appended, split_repo.count_rows(db)) == (0, 1)  # COUNT THE TABLE


def test_a_wide_form_that_does_not_agree_is_named_never_attached(db, tmp_path):
    """OFAL: Polygon lists 1-for-10 on 2026-07-31; no 8-K in its window is readable here. Its 10-K (filed 07-14)
    states the 1-for-10 its shareholders approved, undated (through the restatement screen: read bare it is a
    split on 2024-08-29, a reorganization's date). Not an agreement — the split stays Polygon-only for review,
    and the note names what the 10-K said; the 10-K is not attached as the split's SEC evidence."""
    seed_member(db, "OFAL", "0002036307", [])
    cache = edgar_cache(tmp_path, only={"0001493152-26-033093"}, submissions={"0002036307"})
    r = _list_run(db, tmp_path, cache, tickers=["OFAL"])
    (p,) = r.planned
    d = p.decision
    assert (d.status, d.sec, d.value_source) == (STATUS_NEEDS_REVIEW, None, "polygon")
    assert (
        "none agrees — 10-K 0001493152-26-033093 (filed 2026-07-14): 1-for-10 reverse split, no date"
        in d.note
    )


def test_mi_its_20f_restates_an_old_split_and_is_named_as_such(db, tmp_path):
    """MI (the S1 basket): its window holds only the range-only proxy. The targeted reader reads its 20-F
    (filed 2026-05-01), which restates the 2024 one-for-fifty — through the restatement screen a past split's
    ratio, so the review card keeps the proxy as its evidence and names the 20-F."""
    seed_union(db)
    result, _ = _run(db, tmp_path, apply=False, tickers=["MI"])
    (mi,) = result.planned
    assert mi.decision.status == STATUS_NEEDS_REVIEW
    assert mi.decision.sec.accession == "0001213900-26-046153"
    assert (mi.filings_read, mi.filings_unread, mi.wide_read, mi.wide_unread) == (3, 3, 1, 1)
    assert (
        "Wide forms read: 1 (1 unread), none agrees — 20-F 0001213900-26-050939 (filed 2026-05-01): no ratio "
        "(only a past split's ratio is stated), no date." in mi.decision.note
    )


def test_an_apply_from_the_full_history_marks_the_members_it_covered(db, tmp_path):
    """``backfill_splits --apply`` from 2024-10-01 (or earlier) records each member it covered as filled, so the
    newcomer job (``pipeline/split_history.py``) never re-fills them; a run from a later ``--since`` marks
    nothing (a partial history)."""
    ids = seed_union(db)
    polygon, _ = polygon_source(tmp_path)
    edgar = EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False)
    kw = {"polygon": polygon, "edgar": edgar, "asof": ASOF, "apply": True, "allow_live": True}
    bf.backfill_splits(db, since=SINCE, **kw)
    assert split_history_repo.count(db) == 0
    bf.backfill_splits(db, since=bf.DEFAULT_SINCE, **kw)
    marks = split_history_repo.get(db, tenant_id=DEFAULT_TENANT_ID, security_ids=ids.values())
    assert set(marks) == set(ids.values())
    assert {m.source for m in marks.values()} == {split_history_repo.SOURCE_CLI}
    assert all(m.complete for m in marks.values())
