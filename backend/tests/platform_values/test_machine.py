"""V2 W2b: the MACHINE WRITER (``platform_values.machine.record_extraction``), from real rows.

One function turns an extraction result into platform values: a clean AUTO value becomes a fact stamped ``auto``
in the data tenant; a flagged value and a "nothing found" become CANDIDATES (never facts, never read by a meter);
everything runs under the company write lock and re-reads what is on file under it. Each test COUNTS THE TABLES
(the as-of read de-duplicates, so a duplicate append would hide behind a correct read).

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). No network: results are built here.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from db.session import connect
from db.write_lock import CompanyLockBusy, acquire_company_lock
from domain.extraction import ExtractedFact, ExtractionResult, LocatedPassage, Tier
from domain.market_time import market_today
from domain.platform_values import ValueState
from domain.scopes import DATA_TENANT_ID
from ingest.edgar.annual_shares import extract_annual_shares
from ingest.shares import ingest_shares_outstanding
from platform_values import machine, state
from repositories import platform_value_repo as repo

STEWARD = "steward@example.test"
Q1_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
Q2_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
D1, D2 = date(2026, 3, 31), date(2026, 6, 30)
TABLES = ("fact_shares_outstanding", "fact_cash_burn", "extraction_candidate")


def _company(db, ticker: str = "MACH") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", "0000000007", date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _shares(
    ref: str = Q2_REF, d: date = D2, value: float | None = 30_000_000.0, tier=Tier.AUTO, **kw
):
    return ExtractedFact(
        fact_type="shares_outstanding",
        tier=tier,
        source="10-q-cover",
        source_ref=ref,
        event_date=d,
        value=value,
        note="Cover-page shares outstanding (single class).",
        **kw,
    )


def _cash(ref: str = Q2_REF, d: date = D2, tier=Tier.AUTO, **kw):
    values = {"cash_usd": 30_000_000.0, "quarterly_burn_usd": 3_000_000.0, **kw}
    return ExtractedFact(
        fact_type="cash_burn",
        tier=tier,
        source="10-q",
        source_ref=ref,
        event_date=d,
        note="Cash and equivalents; operating cash flow for the quarter.",
        **values,
    )


def _record(db, sid: UUID, result: ExtractionResult, **kw):
    kw.setdefault("wait_s", 0)
    return machine.record_extraction(db, sid, result, **kw)


def _counts(db, sid: UUID) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for table in TABLES:
            cur.execute(
                f"SELECT count(*) AS n FROM {table} WHERE security_id = %s",  # noqa: S608 — fixed names
                (sid,),
            )
            out[table] = cur.fetchone()["n"]
    db.rollback()
    return out


def _facts(db, table: str, sid: UUID) -> list[dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {table} WHERE security_id = %s ORDER BY recorded_at, id",  # noqa: S608
            (sid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return rows


def _candidates(db, sid: UUID, fact_type: str) -> list[dict]:
    db.rollback()
    rows = list(reversed(repo.candidates_for(db, sid, fact_type)))  # oldest first
    db.rollback()
    return rows


def _stamps(db, sid: UUID) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_change WHERE security_id = %s", (sid,))
        n = cur.fetchone()["n"]
    db.rollback()
    return n


# --- AUTO -> a fact stamped auto ---------------------------------------------------------------------------------


def test_auto_shares_with_nothing_on_file_is_applied_in_the_data_tenant_with_a_stamp(db):
    sid = _company(db)
    out = _record(db, sid, ExtractionResult(facts=[_shares(), _cash()]))
    assert out["shares_outstanding"].applied and out["shares_outstanding"].reason == "applied"
    (row,) = _facts(db, "fact_shares_outstanding", sid)
    assert (row["tenant_id"], row["ratified_by"], float(row["shares"]), row["valid_from"]) == (
        DATA_TENANT_ID,
        "auto",
        30_000_000.0,
        D2,
    )
    assert (row["id"], row["source"], row["vouched"]) == (
        out["shares_outstanding"].fact_id,
        "10-q-cover",
        None,
    )
    assert _stamps(db, sid) >= 1  # the company is stamped changed (committed with the write)


def test_auto_cash_is_applied_as_one_auto_row(db):
    sid = _company(db)
    out = _record(db, sid, ExtractionResult(facts=[_shares(), _cash()]))
    assert out["cash_burn"].applied and out["cash_burn"].reason == "applied"
    (row,) = _facts(db, "fact_cash_burn", sid)
    assert (row["tenant_id"], row["ratified_by"], row["source_ref"], row["valid_from"]) == (
        DATA_TENANT_ID,
        "auto",
        Q2_REF,
        D2,
    )
    assert (float(row["cash_usd"]), float(row["quarterly_burn_usd"])) == (30e6, 3e6)
    assert _counts(db, sid)["extraction_candidate"] == 0


def test_the_same_filing_again_writes_nothing(db):
    sid = _company(db)
    result = ExtractionResult(facts=[_shares(), _cash()])
    _record(db, sid, result)
    before = _counts(db, sid)
    out = _record(db, sid, result)
    assert _counts(db, sid) == before  # ZERO new rows in any of the three tables
    assert {ft: (o.applied, o.reason, o.written) for ft, o in out.items()} == {
        "shares_outstanding": (False, "already_on_file", False),
        "cash_burn": (False, "already_on_file", False),
    }


@pytest.mark.parametrize("d", [D1, D2], ids=["older", "equal"])
def test_an_older_or_equal_filing_is_not_applied(db, d):
    sid = _company(db)
    _record(db, sid, ExtractionResult(facts=[_shares()]))
    before = _counts(db, sid)
    out = _record(db, sid, ExtractionResult(facts=[_shares(ref=Q1_REF, d=d, value=1.0)]))
    assert (out["shares_outstanding"].applied, out["shares_outstanding"].reason) == (
        False,
        "older_than_on_file",
    )
    assert _counts(db, sid)["fact_shares_outstanding"] == before["fact_shares_outstanding"]


def test_a_newer_filing_is_applied_over_a_stewards_value(db):
    """D3 (a): a strictly newer filing replaces a verified value when the company is not pinned."""
    sid = _company(db)
    ingest_shares_outstanding(
        db,
        sid,
        shares=10_000_000,
        source="ratified",
        source_ref=Q1_REF,
        event_date=D1,
        ratified_by=STEWARD,
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()
    out = _record(db, sid, ExtractionResult(facts=[_shares()]))
    assert out["shares_outstanding"].applied
    assert _counts(db, sid)["fact_shares_outstanding"] == 2
    st = state.read_state(db, sid, "shares_outstanding")
    assert (st.state, st.value["source_ref"], st.ratifier) == (
        ValueState.FROM_FILING,
        Q2_REF,
        "machine",
    )


def test_a_pinned_company_gets_a_flagged_candidate_never_a_fact(db):
    sid = _company(db)
    repo.append_pin(
        db, security_id=sid, fact_type="shares_outstanding", action="pin", decided_by=STEWARD
    )
    db.commit()
    out = _record(db, sid, ExtractionResult(facts=[_shares(flags=["cover-after-filing"])]))
    o = out["shares_outstanding"]
    assert (o.applied, o.reason, o.fact_id) == (False, "pinned", None)
    assert _counts(db, sid)["fact_shares_outstanding"] == 0
    (cand,) = _candidates(db, sid, "shares_outstanding")
    assert (cand["id"], cand["outcome"], cand["flags"], float(cand["shares"])) == (
        o.candidate_id,
        "flag",
        ["cover-after-filing", "pinned"],
        30_000_000.0,
    )
    assert out["cash_burn"].reason == "nothing_found"  # cash is not pinned: its own outcome


def test_a_value_dated_after_today_is_never_written(db):
    """D1: a value dated after today could never be shown, so the machine never writes it."""
    sid = _company(db)
    later = market_today() + timedelta(days=3)
    out = _record(db, sid, ExtractionResult(facts=[_shares(d=later), _cash(d=later)]))
    assert {o.reason for o in out.values()} == {"after_today"}
    assert _counts(db, sid) == dict.fromkeys(TABLES, 0)


def test_an_undone_approves_filing_is_not_re_applied(db):
    """The machine reads EVERY stored version: a filing whose approve the steward undid stays undone."""
    sid = _company(db)
    cid = repo.append_candidate(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        outcome="flag",
        flags=["dual-class"],
        source="10-q-cover",
        source_ref=Q2_REF,
        event_date=D2,
        shares=30_000_000,
    )
    fid = ingest_shares_outstanding(
        db,
        sid,
        shares=30_000_000,
        source="10-q-cover",
        source_ref=Q2_REF,
        event_date=D2,
        ratified_by=STEWARD,
        tenant_id=DATA_TENANT_ID,
    )
    did = repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="approve",
        candidate_id=cid,
        fact_id=fid,
        decided_by=STEWARD,
    )
    db.commit()
    repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="undo",
        revokes=did,
        decided_by=STEWARD,
    )
    db.commit()
    before = _counts(db, sid)
    out = _record(db, sid, ExtractionResult(facts=[_shares()]))
    assert out["shares_outstanding"].reason == "already_on_file"
    assert _counts(db, sid)["fact_shares_outstanding"] == before["fact_shares_outstanding"]


# --- FLAG and "nothing" -> candidates, never facts ---------------------------------------------------------------


def test_a_flag_becomes_a_candidate_with_its_values_flags_and_passages(db):
    sid = _company(db)
    passage = LocatedPassage(
        kind="cover", source_ref=Q2_REF, anchor="outstanding", excerpt="Class A 10; Class B 20"
    )
    flagged = _shares(
        tier=Tier.FLAG,
        value=19_672_794.0,
        flags=["dual-class"],
        located_passages=[passage],
        ads_ratio=2,
        ads_ratio_status="known",
    )
    out = _record(db, sid, ExtractionResult(facts=[flagged]))
    o = out["shares_outstanding"]
    assert (o.applied, o.reason, o.fact_id, o.written) == (False, "flagged", None, True)
    (cand,) = _candidates(db, sid, "shares_outstanding")
    assert cand["id"] == o.candidate_id
    assert (cand["tenant_id"], cand["outcome"], cand["flags"]) == (
        DATA_TENANT_ID,
        "flag",
        ["dual-class"],
    )
    assert (cand["source_ref"], cand["event_date"], float(cand["shares"])) == (
        Q2_REF,
        D2,
        19_672_794.0,
    )
    assert (cand["ads_ratio"], cand["ads_ratio_status"], cand["source"]) == (
        2,
        "known",
        "10-q-cover",
    )
    assert cand["located_passages"][0]["excerpt"] == "Class A 10; Class B 20"


@pytest.mark.parametrize("fact_type", ["shares_outstanding", "cash_burn"])
def test_a_candidate_never_reaches_a_meter_or_an_as_of_read(db, fact_type):
    """A flagged value with a number in it is still not a value: the meters' own accessor returns nothing, the
    state's meter value is None, and no fact row exists."""
    sid = _company(db)
    fact = (
        _shares(tier=Tier.FLAG, flags=["dual-class"])
        if fact_type == "shares_outstanding"
        else _cash(tier=Tier.FLAG, flags=["ytd-raw"])
    )
    _record(db, sid, ExtractionResult(facts=[fact]))
    assert _counts(db, sid)["extraction_candidate"] == 2  # the flag + the other type's "nothing"
    assert state.winners(db, sid, fact_type) == []  # the meters' per-filing read
    st = state.read_state(db, sid, fact_type)
    assert (st.state, st.meter_value) == (ValueState.AWAITING_STEWARD, None)
    assert _counts(db, sid)["fact_shares_outstanding"] + _counts(db, sid)["fact_cash_burn"] == 0


def test_a_flagged_cash_value_with_missing_parts_is_a_candidate(db):
    sid = _company(db)
    fact = _cash(tier=Tier.FLAG, flags=["no-cashflow-column"], quarterly_burn_usd=None)
    out = _record(db, sid, ExtractionResult(facts=[fact]))
    assert out["cash_burn"].reason == "flagged"
    (cand,) = _candidates(db, sid, "cash_burn")
    assert (float(cand["cash_usd"]), cand["quarterly_burn_usd"], cand["flags"]) == (
        30e6,
        None,
        ["no-cashflow-column"],
    )


def test_an_auto_value_with_no_number_is_a_candidate_never_a_fact(db):
    """Defensive: an AUTO tier carrying no parsed number cannot be applied; the steward sees it."""
    sid = _company(db)
    out = _record(db, sid, ExtractionResult(facts=[_shares(value=None)]))
    assert out["shares_outstanding"].reason == "flagged"
    assert _counts(db, sid)["fact_shares_outstanding"] == 0


def test_no_annual_filing_is_a_nothing_candidate_for_both_types(db):
    sid = _company(db)
    out = _record(db, sid, ExtractionResult(facts=[], empty_reason="no-annual-filing"))
    assert {ft: (o.reason, o.written) for ft, o in out.items()} == {
        "shares_outstanding": ("nothing_found", True),
        "cash_burn": ("nothing_found", True),
    }
    for ft in ("shares_outstanding", "cash_burn"):
        (cand,) = _candidates(db, sid, ft)
        assert (cand["outcome"], cand["reason"], cand["source_ref"]) == (
            "nothing",
            "no-annual-filing",
            None,
        )
    assert state.read_state(db, sid, "cash_burn").reason == "no-annual-filing"


def test_cover_not_located_names_the_cover_for_shares_and_the_runway_reason_for_cash(db):
    sid = _company(db)
    result = ExtractionResult(
        facts=[], empty_reason="cover-not-located", runway_empty_reason="statements-not-located"
    )
    _record(db, sid, result)
    assert _candidates(db, sid, "shares_outstanding")[0]["reason"] == "cover-not-located"
    assert _candidates(db, sid, "cash_burn")[0]["reason"] == "statements-not-located"


def test_cash_generative_is_a_cash_nothing_candidate_and_shares_go_their_own_way(db):
    sid = _company(db)
    flagged = _shares(tier=Tier.FLAG, flags=["stale-cover"])
    out = _record(db, sid, ExtractionResult(facts=[flagged], runway_empty_reason="cash-generative"))
    assert (out["cash_burn"].reason, out["shares_outstanding"].reason) == (
        "nothing_found",
        "flagged",
    )
    (cash,) = _candidates(db, sid, "cash_burn")
    assert (cash["outcome"], cash["reason"]) == ("nothing", "cash-generative")
    (shares,) = _candidates(db, sid, "shares_outstanding")
    assert shares["outcome"] == "flag"


def test_no_candidate_and_no_reason_is_a_nothing_candidate_no_candidate(db):
    sid = _company(db)
    _record(
        db, sid, ExtractionResult(facts=[_shares()])
    )  # a 10-Q filer whose cash read gave nothing
    (cash,) = _candidates(db, sid, "cash_burn")
    assert (cash["outcome"], cash["reason"]) == ("nothing", "no-candidate")


def test_the_identical_result_twice_records_one_candidate(db):
    sid = _company(db)
    result = ExtractionResult(
        facts=[_shares(tier=Tier.FLAG, flags=["dual-class"])],
        runway_empty_reason="cash-generative",
    )
    first = _record(db, sid, result)
    before = _counts(db, sid)
    second = _record(db, sid, result)
    assert _counts(db, sid) == before
    for ft in ("shares_outstanding", "cash_burn"):
        assert (second[ft].written, second[ft].candidate_id) == (False, first[ft].candidate_id)


def test_a_changed_result_records_a_new_candidate(db):
    sid = _company(db)
    _record(db, sid, ExtractionResult(facts=[_shares(tier=Tier.FLAG, flags=["dual-class"])]))
    _record(db, sid, ExtractionResult(facts=[_shares(tier=Tier.FLAG, flags=["stale-cover"])]))
    assert [c["flags"] for c in _candidates(db, sid, "shares_outstanding")] == [
        ["dual-class"],
        ["stale-cover"],
    ]


# --- the machine never sources a number from a model -------------------------------------------------------------


def test_a_model_estimate_is_never_applied_or_recorded(db):
    """Only the deterministic extractor's own parse is written: a fact carrying an ``estimate_source`` (a model's
    proposal) is ignored, as if the extractor had found nothing."""
    sid = _company(db)
    out = _record(db, sid, ExtractionResult(facts=[_shares(estimate_source="llm_proposed")]))
    assert out["shares_outstanding"].reason == "nothing_found"
    assert _counts(db, sid)["fact_shares_outstanding"] == 0
    (cand,) = _candidates(db, sid, "shares_outstanding")
    assert (cand["outcome"], cand["shares"]) == ("nothing", None)


def test_the_writer_imports_no_model_code():
    src = (Path(machine.__file__)).read_text(encoding="utf-8")
    assert "llm" not in {
        line.split()[1].split(".")[0]
        for line in src.splitlines()
        if line.startswith(("import ", "from "))
    }


# --- the lock -----------------------------------------------------------------------------------------------------


def test_a_held_company_lock_is_busy_and_nothing_is_written(db):
    sid = _company(db)
    other = connect()
    try:
        hold = acquire_company_lock(other, sid, wait_s=0)
        with pytest.raises(CompanyLockBusy):
            _record(db, sid, ExtractionResult(facts=[_shares(), _cash()]), wait_s=0.2)
        assert _counts(db, sid) == dict.fromkeys(TABLES, 0)
        hold.release()
        assert _record(db, sid, ExtractionResult(facts=[_shares()]))["shares_outstanding"].applied
    finally:
        other.close()


def test_the_writes_run_under_the_lock_and_it_is_free_afterward(db, monkeypatch):
    sid = _company(db)
    other = connect()
    seen: list[bool] = []
    real = machine.ingest_shares_outstanding

    def probe(*a, **kw):
        try:
            acquire_company_lock(other, sid, wait_s=0).release()
            seen.append(False)
        except CompanyLockBusy:
            seen.append(True)
        return real(*a, **kw)

    monkeypatch.setattr(machine, "ingest_shares_outstanding", probe)
    try:
        _record(db, sid, ExtractionResult(facts=[_shares()]))
        assert seen == [True]  # held while writing
        acquire_company_lock(other, sid, wait_s=0).release()  # and released once done
    finally:
        other.close()


def test_a_failed_write_leaves_nothing_behind(db, monkeypatch):
    """Both types are written in one transaction: a fault on the second rolls back the first."""
    sid = _company(db)

    def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(machine, "ingest_cash_burn", boom)
    with pytest.raises(RuntimeError):
        _record(db, sid, ExtractionResult(facts=[_shares(), _cash()]))
    assert _counts(db, sid) == dict.fromkeys(TABLES, 0)


# --- the annual share-count rule (PR 5): an annual-only count within the cap is applied ---------------------------
#
# A 20-F / 40-F cover count is always a FLAG from the extractor. When its ONLY flags are the annual ones
# (``annual-cover``, ``stale-cover``) and it is no older than ``annual_shares_auto_max_age_days`` (400), the machine
# applies it like an AUTO value: stamped ``auto``, state "from the filing", its real as-of date and ADS ratio kept,
# and the same pin / already-on-file / older-than-on-file / after-today rules. Anything else still queues. The
# counts come from the real cover fixtures (TSM: ratio 5; ARM: ratio 1) read by the real annual extractor.

ANNUAL_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "annual"
TSM_REF = "https://www.sec.gov/Archives/edgar/data/1046179/tsm-20f.htm"
ARM_REF = "https://www.sec.gov/Archives/edgar/data/1973239/arm-20f.htm"


def _annual(fname: str, ref: str, age_days: int, today: date, *, has_f6: bool) -> ExtractedFact:
    """The real annual cover extractor over a fixture cover, its period of report ``age_days`` before ``today``
    (no companyfacts: the cover alone, so the only flags are the annual ones)."""
    (fact,) = extract_annual_shares(
        None,
        (ANNUAL_FX / fname).read_text(encoding="utf-8"),
        annual_ref=ref,
        annual_form="20-F",
        report_date=today - timedelta(days=age_days),
        today=today,
        has_f6_filing=has_f6,
    )
    return fact


def _tsm(age_days: int, today: date) -> ExtractedFact:
    return _annual("TSM-20f.txt", TSM_REF, age_days, today, has_f6=False)


def test_an_annual_only_count_within_the_cap_is_applied_from_the_filing(db):
    today = market_today()
    fact = _tsm(300, today)
    assert (fact.tier, fact.source, sorted(fact.flags)) == (  # the premise: before PR 5 it queued
        Tier.FLAG,
        "annual-cover",
        ["annual-cover", "stale-cover"],
    )
    sid = _company(db, "TSMA")
    out = _record(db, sid, ExtractionResult(facts=[fact]), today=today)
    o = out["shares_outstanding"]
    assert (o.applied, o.reason, o.written) == (True, "applied", True)
    (row,) = _facts(db, "fact_shares_outstanding", sid)
    assert (row["id"], row["tenant_id"], row["ratified_by"], row["vouched"]) == (
        o.fact_id,
        DATA_TENANT_ID,
        "auto",
        None,
    )
    assert (row["source"], row["source_ref"], row["valid_from"], float(row["shares"])) == (
        "annual-cover",
        TSM_REF,
        today - timedelta(days=300),  # the count's own as-of date, never moved
        25_932_524_521.0,
    )
    assert (row["ads_ratio"], row["ads_ratio_status"]) == (5, "known")
    # the stored note says it is an annual figure, its as-of date, its ADS ratio and the flags it carried
    asof = (today - timedelta(days=300)).isoformat()
    assert f"20-F cover count 25,932,524,521 as of {asof}" in row["note"]
    assert "ADS ratio 5:1" in row["note"]
    assert "annual figure" in row["note"]
    assert "flags annual-cover, stale-cover" in row["note"]
    assert _candidates(db, sid, "shares_outstanding") == []
    st = state.read_state(db, sid, "shares_outstanding", asof=today)
    assert (st.state, st.ratifier, st.value["source"]) == (
        ValueState.FROM_FILING,
        "machine",
        "annual-cover",
    )


def test_an_annual_count_applied_twice_writes_one_fact(db):
    today = market_today()
    sid = _company(db, "TSMB")
    result = ExtractionResult(facts=[_tsm(300, today)], runway_empty_reason="cash-generative")
    _record(db, sid, result, today=today)
    before = _counts(db, sid)
    assert before["fact_shares_outstanding"] == 1
    out = _record(db, sid, result, today=today)
    assert _counts(db, sid) == before  # COUNT THE TABLE: one fact, no new candidate
    assert (out["shares_outstanding"].reason, out["shares_outstanding"].written) == (
        "already_on_file",
        False,
    )


def test_an_annual_one_to_one_count_is_applied_with_its_ratio(db):
    today = market_today()
    sid = _company(db, "ARMA")
    fact = _annual("ARM-20f.txt", ARM_REF, 200, today, has_f6=True)
    out = _record(db, sid, ExtractionResult(facts=[fact]), today=today)
    assert out["shares_outstanding"].applied
    (row,) = _facts(db, "fact_shares_outstanding", sid)
    assert (row["source"], row["ads_ratio"], row["ads_ratio_status"]) == (
        "annual-cover",
        1,
        "known",
    )


@pytest.mark.parametrize("age", [401, 1_415], ids=["401-days", "years-old"])
def test_an_annual_count_older_than_the_cap_waits_for_the_steward(db, age):
    today = market_today()
    sid = _company(db, "TSMC")
    out = _record(db, sid, ExtractionResult(facts=[_tsm(age, today)]), today=today)
    assert (out["shares_outstanding"].applied, out["shares_outstanding"].reason) == (
        False,
        "flagged",
    )
    assert _counts(db, sid)["fact_shares_outstanding"] == 0
    (cand,) = _candidates(db, sid, "shares_outstanding")
    assert (cand["outcome"], sorted(cand["flags"])) == ("flag", ["annual-cover", "stale-cover"])
    assert f"({age} days old)" in cand["note"]  # its age rides the note the steward reads
    st = state.read_state(db, sid, "shares_outstanding", asof=today)
    assert st.state == ValueState.AWAITING_STEWARD


@pytest.mark.parametrize(
    "extra",
    [
        "multi-value-cover",
        "dual-class",
        "ads-ratio-unread",
        "source-disagreement",
        "implausible-count",
        "cover-after-filing",
        "a-flag-nobody-has-named-yet",
    ],
)
def test_an_annual_count_with_any_other_flag_waits_for_the_steward(db, extra):
    today = market_today()
    sid = _company(db, "TSMD")
    fact = _tsm(300, today)
    fact = fact.model_copy(update={"flags": [*fact.flags, extra]})
    out = _record(db, sid, ExtractionResult(facts=[fact]), today=today)
    assert out["shares_outstanding"].reason == "flagged"
    assert _counts(db, sid)["fact_shares_outstanding"] == 0
    (cand,) = _candidates(db, sid, "shares_outstanding")
    assert extra in cand["flags"]


def test_an_annual_count_with_an_unread_ads_ratio_waits_for_the_steward(db):
    today = market_today()
    sid = _company(db, "TSME")
    fact = _tsm(300, today).model_copy(update={"ads_ratio": None, "ads_ratio_status": "unread"})
    out = _record(db, sid, ExtractionResult(facts=[fact]), today=today)
    assert out["shares_outstanding"].reason == "flagged"
    assert _counts(db, sid)["fact_shares_outstanding"] == 0


def test_a_pinned_companys_annual_count_waits_for_the_steward(db):
    today = market_today()
    sid = _company(db, "TSMF")
    repo.append_pin(
        db, security_id=sid, fact_type="shares_outstanding", action="pin", decided_by=STEWARD
    )
    db.commit()
    out = _record(db, sid, ExtractionResult(facts=[_tsm(300, today)]), today=today)
    o = out["shares_outstanding"]
    assert (o.applied, o.reason) == (False, "pinned")
    assert _counts(db, sid)["fact_shares_outstanding"] == 0
    (cand,) = _candidates(db, sid, "shares_outstanding")
    assert cand["flags"] == ["annual-cover", "stale-cover", "pinned"]


def test_an_annual_count_older_than_the_value_shown_is_skipped(db):
    today = market_today()
    sid = _company(db, "TSMG")
    ingest_shares_outstanding(
        db,
        sid,
        shares=26_000_000_000,
        source="ratified",
        source_ref=Q2_REF,
        event_date=today - timedelta(days=100),
        ratified_by=STEWARD,
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()
    before = _counts(db, sid)
    out = _record(db, sid, ExtractionResult(facts=[_tsm(300, today)]), today=today)
    assert out["shares_outstanding"].reason == "older_than_on_file"
    assert _counts(db, sid)["fact_shares_outstanding"] == before["fact_shares_outstanding"]


def test_stale_cover_on_a_quarterly_cover_still_waits_for_the_steward(db):
    """On a 10-Q cover ``stale-cover`` means the count predates the quarter's end: never the annual rule."""
    today = market_today()
    sid = _company(db, "QSTL")
    fact = _shares(d=today - timedelta(days=30), tier=Tier.FLAG, flags=["stale-cover"])
    out = _record(db, sid, ExtractionResult(facts=[fact]), today=today)
    assert out["shares_outstanding"].reason == "flagged"
    assert _counts(db, sid)["fact_shares_outstanding"] == 0


@pytest.mark.parametrize(
    "flags", [["annual-statements"], ["annual-cover", "stale-cover"]], ids=["real", "adversarial"]
)
def test_annual_cash_and_burn_still_waits_for_the_steward(db, flags):
    """The rule is for share counts only (operator decision): an annual cash and burn value stays queued."""
    today = market_today()
    sid = _company(db, "ACSH")
    cash = _cash(d=today - timedelta(days=100), tier=Tier.FLAG, flags=flags).model_copy(
        update={"source": "annual-statements", "statement_currency": "EUR"}
    )
    out = _record(db, sid, ExtractionResult(facts=[_tsm(300, today), cash]), today=today)
    assert out["shares_outstanding"].applied  # the share count beside it is applied
    assert (out["cash_burn"].applied, out["cash_burn"].reason) == (False, "flagged")
    assert _counts(db, sid)["fact_cash_burn"] == 0


def test_an_annual_count_queued_before_the_rule_leaves_the_queue_once_applied(db):
    """A candidate the machine recorded for the same filing before this rule existed stops waiting: the applied
    value has the same date, so the state drops the older-or-equal flag (no row is deleted; History keeps it).
    """
    today = market_today()
    sid = _company(db, "TSMH")
    fact = _tsm(300, today)
    repo.append_candidate(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        outcome="flag",
        flags=list(fact.flags),
        source=fact.source,
        source_ref=fact.source_ref,
        event_date=fact.event_date,
        shares=fact.value,
        ads_ratio=fact.ads_ratio,
        ads_ratio_status=fact.ads_ratio_status,
    )
    db.commit()
    assert sid in {a.security_id for a in state.awaiting(db, limit=None, asof=today)}
    assert _record(db, sid, ExtractionResult(facts=[fact]), today=today)[
        "shares_outstanding"
    ].applied
    assert sid not in {a.security_id for a in state.awaiting(db, limit=None, asof=today)}
    assert len(_candidates(db, sid, "shares_outstanding")) == 1  # kept, never deleted
