"""The year-to-date PACE stored as figures (migration 0209; operator decision D3's sub-decision: a column).

``fact_cash_burn`` and ``extraction_candidate`` gain ``ytd_quarterly_burn_usd`` (numeric, burn sign: positive =
burning) and ``ytd_burn_days`` (integer). NULL = not recorded. They ride every write path exactly as 0208's currency
and investment part do, set on the INSERT of a new version only:

- the machine applies an AUTO value -> the fact carries the pace (an idempotent re-run writes nothing: COUNT THE
  TABLE);
- the machine records a FLAG -> the candidate carries it; the steward APPROVES -> the fact carries it;
- the steward CORRECTS -> the fact carries what the steward states; a correction that does not restate the pace
  stores NULL AND drops the pace from the note it keeps, so no reader re-applies an old pace to the corrected burn;
- an UNDO falls back to the earlier version with ITS pace.

Readers (``domain.burn_pace.stored_pace``) read the columns; a row written before 0209 (NULL columns) falls back to the
note's machine-written segment so nothing regresses. The caveat is still computed on read and never changes a tier.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest

from domain import burn_pace
from domain.burn_pace import YtdPace, pace_segment
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from domain.scopes import DATA_TENANT_ID
from platform_values import machine, state, steward
from platform_values.steward import PlatformValueInvalid
from repositories import platform_value_repo as repo

STEWARD = "steward@example.test"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/9/0001-10q-2025q2.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/9/0002-10q-2025q3.htm"
D1, D2 = date(2025, 6, 30), date(2025, 9, 30)

# UP's shape: a 17.1M quarter against a 69.9M-a-quarter year to date (272 days)
_YTD = YtdPace(burn_usd=208_345_000.0, start="2025-01-01", end="2025-09-30")
_NOTE = f"cash+equivalents 51,800,000; quarterly burn = operating cash use [{pace_segment(_YTD)}; cited filing: x]."


def _company(db, ticker: str = "PACE") -> UUID:
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


def _shown(db, sid: UUID) -> dict:
    db.rollback()
    got = state.read_state(db, sid, "cash_burn")
    db.rollback()
    assert got.value is not None
    return got.value


def _pace_of(row: dict) -> tuple:
    q = row["ytd_quarterly_burn_usd"]
    return (None if q is None else float(q), row["ytd_burn_days"])


def _cash_fact(tier: Tier = Tier.AUTO, **kw) -> ExtractedFact:
    values = {
        "cash_usd": 51_800_000.0,
        "quarterly_burn_usd": 17_100_000.0,
        "ytd_quarterly_burn_usd": 69_943_025.0,
        "ytd_burn_days": 272,
        **kw,
    }
    return ExtractedFact(
        fact_type="cash_burn",
        tier=tier,
        source="10-q",
        source_ref=NEW_REF,
        event_date=D2,
        note=_NOTE,
        **values,
    )


def _record(db, sid: UUID, fact: ExtractedFact):
    return machine.record_extraction(db, sid, ExtractionResult(facts=[fact]), wait_s=0)


# --- the machine writer ------------------------------------------------------------------------------------------


def test_machine_apply_stores_the_pace_once(db):
    sid = _company(db)
    fact = _cash_fact()
    out = _record(db, sid, fact)["cash_burn"]
    assert out.applied  # a divergent pace is a caveat, never a flag: the AUTO value is applied
    shown = _shown(db, sid)
    assert shown["id"] == out.fact_id
    assert _pace_of(shown) == (69_943_025.0, 272)
    assert _count(db, "fact_cash_burn", sid) == 1
    again = _record(db, sid, fact)["cash_burn"]  # the same filing again: nothing written
    assert (again.applied, again.reason) == (False, "already_on_file")
    assert _count(db, "fact_cash_burn", sid) == 1


def test_machine_apply_without_a_pace_stores_none(db):
    sid = _company(db)
    _record(db, sid, _cash_fact(ytd_quarterly_burn_usd=None, ytd_burn_days=None))
    assert _pace_of(_shown(db, sid)) == (None, None)


def test_machine_flag_carries_the_pace_onto_the_candidate_once(db):
    sid = _company(db)
    fact = _cash_fact(Tier.FLAG, flags=["stale-cash"])
    out = _record(db, sid, fact)["cash_burn"]
    assert (out.reason, out.written) == ("flagged", True)
    db.rollback()
    cand = repo.latest_candidate(db, sid, "cash_burn")
    db.rollback()
    assert _pace_of(cand) == (69_943_025.0, 272)
    assert _record(db, sid, fact)["cash_burn"].written is False  # the same result: one candidate
    changed = _record(db, sid, fact.model_copy(update={"ytd_quarterly_burn_usd": 70_000_000.0}))
    assert changed["cash_burn"].written is True  # another pace is another result


def test_machine_flag_then_approve_stores_the_pace(db):
    sid = _company(db)
    cid = _record(db, sid, _cash_fact(Tier.FLAG, flags=["stale-cash"]))["cash_burn"].candidate_id
    out = steward.approve(db, cid, by=STEWARD)
    shown = _shown(db, sid)
    assert shown["id"] == out.fact_id
    assert _pace_of(shown) == (69_943_025.0, 272)
    assert _count(db, "fact_cash_burn", sid) == 1


# --- the steward's correction ------------------------------------------------------------------------------------


def _correct(db, sid: UUID, *, note: str | None = _NOTE, ref: str = NEW_REF, **parts):
    return steward.correct(
        db,
        sid,
        "cash_burn",
        {"cash_usd": 51_800_000.0, "quarterly_burn_usd": 60_000_000.0, **parts},
        source_ref=ref,
        event_date=D2,
        note=note,
        by=STEWARD,
    )


def test_correct_stores_a_restated_pace(db):
    sid = _company(db)
    _correct(db, sid, ytd_quarterly_burn_usd=69_943_025.0, ytd_burn_days=272)
    shown = _shown(db, sid)
    assert _pace_of(shown) == (69_943_025.0, 272)
    pace = burn_pace.stored_pace(shown)
    assert pace is not None and pace.ytd_quarterly_burn_usd == 69_943_025


def test_correct_without_the_pace_stores_none_and_does_not_inherit_the_notes_pace(db):
    """The previous builder's finding: a correction that keeps the old note would re-apply the OLD year-to-date
    pace to the corrected burn. The new version stores NULL pace columns and its note no longer carries the pace.
    """
    sid = _company(db)
    _correct(db, sid)  # the steward's note is the extractor's, with its pace segment
    shown = _shown(db, sid)
    assert _pace_of(shown) == (None, None)
    assert "year-to-date pace" not in (shown["note"] or "")
    assert burn_pace.stored_pace(shown) is None
    assert shown["note"].startswith("cash+equivalents 51,800,000")  # the rest of the note is kept


@pytest.mark.parametrize(
    "parts",
    [
        {"ytd_quarterly_burn_usd": 1.0},  # a pace without its span
        {"ytd_burn_days": 90},  # a span without its pace
        {"ytd_quarterly_burn_usd": 1.0, "ytd_burn_days": 0},
        {"ytd_quarterly_burn_usd": float("nan"), "ytd_burn_days": 90},
    ],
)
def test_correct_refuses_half_a_pace(db, parts):
    sid = _company(db)
    with pytest.raises(PlatformValueInvalid) as exc:
        _correct(db, sid, **parts)
    assert exc.value.kind == "bad_values"
    assert _count(db, "fact_cash_burn", sid) == 0


def test_undo_restores_the_earlier_versions_pace(db):
    sid = _company(db)
    first = _correct(db, sid, ytd_quarterly_burn_usd=1_000_000.0, ytd_burn_days=181)
    second = _correct(db, sid)  # same filing, no pace: a new version
    assert _pace_of(_shown(db, sid)) == (None, None)
    steward.undo(db, second.decision_id, by=STEWARD)
    shown = _shown(db, sid)
    assert (shown["id"], *_pace_of(shown)) == (first.fact_id, 1_000_000.0, 181)
    assert _count(db, "fact_cash_burn", sid) == 2  # set aside, never deleted


# --- the one reader ----------------------------------------------------------------------------------------------


def _row(**kw) -> dict:
    return {
        "cash_usd": 51_800_000.0,
        "quarterly_burn_usd": 17_100_000.0,
        "note": None,
        "ytd_quarterly_burn_usd": None,
        "ytd_burn_days": None,
        **kw,
    }


def test_the_reader_uses_the_columns():
    p = burn_pace.stored_pace(_row(ytd_quarterly_burn_usd=69_943_025, ytd_burn_days=272))
    assert p is not None
    assert (p.ytd_quarterly_burn_usd, p.ytd_days, p.ytd_runway_months) == (69_943_025, 272, 2.2)
    assert p.caveat is not None and "well below the year-to-date pace" in p.caveat


def test_the_columns_win_over_the_note():
    p = burn_pace.stored_pace(
        _row(note=_NOTE, ytd_quarterly_burn_usd=20_000_000, ytd_burn_days=181)
    )
    assert p is not None and (p.ytd_quarterly_burn_usd, p.ytd_days, p.caveat) == (
        20_000_000,
        181,
        None,
    )


def test_a_row_from_before_0209_falls_back_to_the_note():
    p = burn_pace.stored_pace(_row(note=_NOTE))
    assert p is not None and (p.ytd_quarterly_burn_usd, p.ytd_days) == (69_943_025, 272)


def test_no_pace_anywhere_reads_none():
    assert burn_pace.stored_pace(_row()) is None
    assert (
        burn_pace.stored_pace(
            _row(quarterly_burn_usd=None, ytd_quarterly_burn_usd=1.0, ytd_burn_days=90)
        )
        is None
    )


# --- every runway screen reads the stored pace -------------------------------------------------------------------


def _stored_row(**kw) -> dict:
    """A fact version written after 0209: the pace in its columns, a note with no pace segment."""
    from datetime import datetime, timezone

    return {
        "id": uuid4(),
        "source": "10-q",
        "source_ref": NEW_REF,
        "ratified_by": "auto",
        "valid_from": D2,
        "recorded_at": datetime(2025, 11, 7, 12, tzinfo=timezone.utc),
        **_row(note="cash+equivalents; quarterly burn = operating cash use.", **kw),
    }


def test_every_runway_screen_reads_the_pace_columns():
    from app.routers import admin_platform_values as apv
    from dashboards.filings import Who, rows_from_ratified
    from dashboards.peer_table import runway_cell
    from domain.config import DEFAULT_CONFIG
    from workbench import scoring

    row = _stored_row(ytd_quarterly_burn_usd=69_943_025, ytd_burn_days=272)

    class _Pit:
        def cash_burn_facts(self, sid):
            return [row]

    detail = scoring._runway(_Pit(), uuid4(), DEFAULT_CONFIG).provenance[0].detail
    assert (detail["ytd_quarterly_burn_usd"], detail["ytd_burn_days"]) == (69_943_025, 272)
    assert "pace_caveat" in detail
    assert runway_cell(row).ytd_quarterly_burn_usd == 69_943_025
    (r,) = rows_from_ratified(
        [row], [], [], Who(security_id=uuid4(), ticker="UP", name="Up", cik=None)
    )
    assert "year-to-date pace burn $69.9M/qtr" in r.headline
    pace = apv._pace(row)
    assert pace is not None and pace.ytd_days == 272
