"""The stored STATEMENT CURRENCY and INVESTMENT PART of a cash/burn platform value (migration 0208; the extractor
correctness plan's PR 6 and PR 1b, operator decisions D1 (d) and D4).

``fact_cash_burn.currency`` (NULL = not recorded, never invented as USD) and ``fact_cash_burn.investments_usd`` /
``extraction_candidate.investments_usd`` (NULL = no split recorded) ride every write path, set on the INSERT of a
new version only:

- the machine applies an AUTO value -> the fact carries both;
- the machine records a FLAG -> the candidate carries both; the steward APPROVES it -> the fact carries both (the
  reported bug: the currency was dropped on approve and screens showed "$");
- the steward CORRECTS -> the fact carries what the steward states (validated: an ISO code, a non-negative part no
  larger than the cash);
- an UNDO falls back to the earlier version with ITS values.

Each test counts the TABLES where it matters (an idempotent re-run writes nothing). DB-backed (the shared ``db``
fixture); a steward action commits on its own connection, so every seed is committed first.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import psycopg
import pytest

from domain.extraction import ExtractedFact, ExtractionResult, Tier
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from platform_values import machine, state, steward
from platform_values.steward import PlatformValueInvalid
from repositories import platform_value_repo as repo

STEWARD = "steward@example.test"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/9/0001-20f-2024.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/9/0002-20f-2025.htm"
D1, D2 = date(2025, 3, 31), date(2025, 12, 31)


def _company(db, ticker: str = "CURR") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", "0000000009", date(2020, 1, 1)),
        )
    db.commit()
    return sid


def _count(db, table: str, sid: UUID) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table} WHERE security_id = %s",  # noqa: S608 — fixed names
            (sid,),
        )
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def _cash_candidates(db, sid: UUID) -> int:
    """How many cash/burn candidates the company has (the shares type writes its own "nothing" ones)."""
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM extraction_candidate WHERE security_id = %s AND fact_type = 'cash_burn'",
            (sid,),
        )
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def _shown(db, sid: UUID) -> dict:
    db.rollback()
    got = state.read_state(db, sid, "cash_burn")
    db.rollback()
    assert got.value is not None
    return got.value


def _cash_flag(db, sid: UUID, *, d: date = D2, ref: str = NEW_REF, **extra) -> UUID:
    cid = repo.append_candidate(
        db,
        security_id=sid,
        fact_type="cash_burn",
        outcome="flag",
        flags=["annual-statements"],
        source="20-f",
        source_ref=ref,
        event_date=d,
        cash_usd=7_000_000_000,
        quarterly_burn_usd=-400_000_000,
        **extra,
    )
    db.commit()
    return cid


# --- the reported bug: approve drops the currency ----------------------------------------------------------------


def test_approve_keeps_the_candidates_currency(db):
    """The reported bug: a CNY candidate approved was stored with no currency, so every screen printed "$"."""
    sid = _company(db)
    cid = _cash_flag(db, sid, statement_currency="CNY")
    out = steward.approve(db, cid, by=STEWARD)
    shown = _shown(db, sid)
    assert shown["id"] == out.fact_id
    assert shown.get("currency") == "CNY"


def test_approve_keeps_the_candidates_currency_and_investment_part(db):
    sid = _company(db)
    cid = _cash_flag(db, sid, statement_currency="CNY", investments_usd=2_500_000_000)
    out = steward.approve(db, cid, by=STEWARD)
    shown = _shown(db, sid)
    assert shown["id"] == out.fact_id
    assert shown["currency"] == "CNY"
    assert float(shown["investments_usd"]) == 2_500_000_000


def test_approve_keeps_an_absent_currency_absent(db):
    """A candidate whose currency the extractor could not establish (``currency-unread``) is approved with NO
    currency: NULL means not recorded, never 'USD'."""
    sid = _company(db)
    cid = _cash_flag(db, sid)
    steward.approve(db, cid, by=STEWARD)
    shown = _shown(db, sid)
    assert (shown["currency"], shown["investments_usd"]) == (None, None)


def test_approve_refuses_a_candidate_part_larger_than_its_cash(db):
    sid = _company(db)
    cid = _cash_flag(db, sid, statement_currency="CNY", investments_usd=8_000_000_000)
    before = _count(db, "fact_cash_burn", sid)
    with pytest.raises(PlatformValueInvalid) as exc:
        steward.approve(db, cid, by=STEWARD)
    assert exc.value.kind == "bad_values"
    assert _count(db, "fact_cash_burn", sid) == before


# --- the machine writer ------------------------------------------------------------------------------------------


def _cash_fact(tier: Tier = Tier.AUTO, **kw) -> ExtractedFact:
    values = {"cash_usd": 30_000_000.0, "quarterly_burn_usd": 3_000_000.0, **kw}
    return ExtractedFact(
        fact_type="cash_burn",
        tier=tier,
        source="10-q",
        source_ref=NEW_REF,
        event_date=D2,
        note="Cash and short-term investments; operating cash flow for the quarter.",
        **values,
    )


def _record(db, sid: UUID, fact: ExtractedFact):
    return machine.record_extraction(db, sid, ExtractionResult(facts=[fact]), wait_s=0)


def test_machine_apply_stores_the_currency_and_investment_part_once(db):
    sid = _company(db)
    fact = _cash_fact(statement_currency="USD", investments_usd=12_000_000.0)
    out = _record(db, sid, fact)["cash_burn"]
    assert out.applied
    shown = _shown(db, sid)
    assert (shown["id"], shown["currency"], float(shown["investments_usd"])) == (
        out.fact_id,
        "USD",
        12_000_000.0,
    )
    assert _count(db, "fact_cash_burn", sid) == 1
    again = _record(db, sid, fact)["cash_burn"]  # the same filing again: nothing written
    assert (again.applied, again.reason) == (False, "already_on_file")
    assert _count(db, "fact_cash_burn", sid) == 1


def test_machine_apply_without_a_currency_stores_none(db):
    """The periodic path sets no currency: the fact records none (never an invented 'USD')."""
    sid = _company(db)
    _record(db, sid, _cash_fact())
    shown = _shown(db, sid)
    assert (shown["currency"], shown["investments_usd"]) == (None, None)


def test_machine_flag_carries_the_investment_part_onto_the_candidate_once(db):
    sid = _company(db)
    fact = _cash_fact(
        Tier.FLAG, flags=["annual-statements"], statement_currency="JPY", investments_usd=5e6
    )
    out = _record(db, sid, fact)["cash_burn"]
    assert (out.reason, out.written) == ("flagged", True)
    db.rollback()
    cand = repo.latest_candidate(db, sid, "cash_burn")
    db.rollback()
    assert (cand["statement_currency"], float(cand["investments_usd"])) == ("JPY", 5e6)
    assert _cash_candidates(db, sid) == 1
    again = _record(db, sid, fact)["cash_burn"]  # the same result: the newest candidate repeats it
    assert again.written is False
    assert _cash_candidates(db, sid) == 1
    changed = _record(db, sid, fact.model_copy(update={"investments_usd": 6e6}))["cash_burn"]
    assert changed.written is True  # another investment part is another result
    assert _cash_candidates(db, sid) == 2


def test_machine_flag_then_approve_stores_both_end_to_end(db):
    sid = _company(db)
    fact = _cash_fact(
        Tier.FLAG, flags=["annual-statements"], statement_currency="CNY", investments_usd=4e6
    )
    cid = _record(db, sid, fact)["cash_burn"].candidate_id
    steward.approve(db, cid, by=STEWARD)
    shown = _shown(db, sid)
    assert (shown["currency"], float(shown["investments_usd"])) == ("CNY", 4e6)
    assert _count(db, "fact_cash_burn", sid) == 1


# --- the steward's correction ------------------------------------------------------------------------------------


def _correct(db, sid: UUID, *, ref: str = NEW_REF, d: date = D2, **parts):
    return steward.correct(
        db,
        sid,
        "cash_burn",
        {"cash_usd": 7_000_000.0, "quarterly_burn_usd": 1_000_000.0, **parts},
        source_ref=ref,
        event_date=d,
        by=STEWARD,
    )


def test_correct_stores_the_stated_currency_and_investment_part(db):
    sid = _company(db)
    out = _correct(db, sid, currency="CAD", investments_usd=2_000_000.0)
    shown = _shown(db, sid)
    assert (shown["id"], shown["currency"], float(shown["investments_usd"])) == (
        out.fact_id,
        "CAD",
        2_000_000.0,
    )


def test_correct_without_the_parts_records_none(db):
    sid = _company(db)
    _correct(db, sid)
    shown = _shown(db, sid)
    assert (shown["currency"], shown["investments_usd"]) == (None, None)


@pytest.mark.parametrize(
    "parts",
    [
        {"currency": "usd"},  # not capitals
        {"currency": "US"},
        {"currency": "US$"},
        {"investments_usd": -1.0},
        {"investments_usd": 7_000_001.0},  # above the cash
    ],
)
def test_correct_refuses_a_bad_currency_or_part(db, parts):
    sid = _company(db)
    with pytest.raises(PlatformValueInvalid) as exc:
        _correct(db, sid, **parts)
    assert exc.value.kind == "bad_values"
    assert _count(db, "fact_cash_burn", sid) == 0


def test_a_part_equal_to_the_cash_is_allowed(db):
    sid = _company(db)
    _correct(db, sid, investments_usd=7_000_000.0)
    assert float(_shown(db, sid)["investments_usd"]) == 7_000_000.0


# --- undo, bitemporal --------------------------------------------------------------------------------------------


def test_undo_restores_the_earlier_versions_currency_and_part(db):
    sid = _company(db)
    first = _correct(db, sid, currency="EUR", investments_usd=1_000_000.0)
    second = _correct(
        db, sid, currency="CHF", investments_usd=3_000_000.0
    )  # same filing: a new version
    assert _shown(db, sid)["id"] == second.fact_id
    steward.undo(db, second.decision_id, by=STEWARD)
    shown = _shown(db, sid)
    assert (shown["id"], shown["currency"], float(shown["investments_usd"])) == (
        first.fact_id,
        "EUR",
        1_000_000.0,
    )
    assert _count(db, "fact_cash_burn", sid) == 2  # the undone version is set aside, never deleted


def test_an_older_row_keeps_null_and_a_new_version_does_not_rewrite_it(db):
    """A V1-style row (written before 0208, no currency or part) stays NULL; a later version is a new row."""
    sid = _company(db)
    old = ingest_cash_burn(
        db,
        sid,
        cash_usd=5_000_000.0,
        quarterly_burn_usd=1_000_000.0,
        source="10-q",
        source_ref=OLD_REF,
        event_date=D1,
        ratified_by="operator",
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()
    _correct(db, sid, currency="GBP", investments_usd=1.0)
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT currency, investments_usd FROM fact_cash_burn WHERE id = %s", (old,))
        row = cur.fetchone()
    db.rollback()
    assert (row["currency"], row["investments_usd"]) == (None, None)


def test_the_table_refuses_a_currency_that_is_not_an_iso_code(db):
    sid = _company(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        ingest_cash_burn(
            db,
            sid,
            cash_usd=1.0,
            quarterly_burn_usd=1.0,
            source="ratified",
            source_ref=NEW_REF,
            event_date=D2,
            currency="usd",
            tenant_id=DATA_TENANT_ID,
        )
    db.rollback()
