"""``pipeline.backfill_splits`` end to end on a real DB, over the REAL Polygon pages and the REAL filings and
submissions index in ``tests/fixtures/splits`` (cache-only: no network). Every idempotency claim COUNTS THE
TABLE (``count(*)``), never the read — the read dedups, so a duplicate append would hide behind it.

The basket (``tests/split_fixtures.UNION``): BTOC / LRHC / OPAD / GGRP (8-K filers, with the item rows the
nightly stores) and MI (a 6-K filer, read from its submissions index)."""

from __future__ import annotations

import io
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
from pipeline import backfill_splits as bf
from repositories import split_repo
from tests.split_801_fixtures import (
    ENHA_8K,
    ENHA_FIRST_SESSION,
    ENHA_POLYGON_DAY,
    OLD_ITEMS,
    enha_polygon,
    seed_enha,
)
from tests.split_801_fixtures import SINCE as SINCE_8_01
from tests.split_fixtures import edgar_cache, polygon_source, seed_union

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
    """The version prod holds since 2026-10-20: the backfill, reading only items 3.03 / 5.03, saw none of
    ENHA's 8-Ks — Polygon's 1-for-10 on 10-28, needs review, no SEC side."""
    with monkeypatch.context() as m:
        m.setattr(bf, "SPLIT_8K_ITEMS", OLD_ITEMS)
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
