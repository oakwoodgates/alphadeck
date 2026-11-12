"""Bad dates step 3 — the ``insider_row_held`` data flag (display only, never a call input): a company with at
least one held insider row OWED a steward decision carries it; it is absent otherwise. Judged by the read's own
ladder over the read's own inputs on the view's clock, on REAL filings (``tests/held_fixtures.py``).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from db.session import DEFAULT_TENANT_ID
from domain.data_flags import IN_DOUBT, INSIDER, INSIDER_HOLD_OWED
from domain.market_time import known_at_for_asof
from pipeline.data_flags import read_flags
from repositories import insider_hold_repo
from tests import held_fixtures as H

LATE = "0000899243-21-012557"


def _flags(db, sid, asof: date, *, clock: str = "record", known_at: datetime | None = None):
    got = read_flags(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_ids=[sid],
        tickers={sid: "X"},
        asof=asof,
        known_at=known_at,
        clock=clock,
        stale_days=10,
    )
    return [f for f in got[sid].flags if f.kind == INSIDER_HOLD_OWED]


def test_an_owed_hold_flags_its_company_and_reaches_the_insider_cells_its_filing_can_touch(db):
    sid = H.seed(db, "late")
    (flag,) = _flags(db, sid, date(2021, 4, 1))
    assert flag.level == IN_DOUBT and flag.affects == (INSIDER,)
    assert flag.columns == ("ins_30d", "ins_90d")  # filed 2021-03-18: inside both windows
    assert LATE in flag.reason and "3 insider row(s)" in flag.reason
    assert flag.since == date(2021, 3, 18)
    assert flag.evidence[0].url.endswith("/1425450/000089924321012557/")
    assert "ratifies a date or dismisses" in flag.clears
    # 60 days on: only the 90-day window can still hold the trade; a year on: the panel only, no cell
    assert _flags(db, sid, date(2021, 5, 17))[0].columns == ("ins_90d",)
    assert _flags(db, sid, date(2022, 6, 1))[0].columns == ()


def test_no_flag_when_nothing_is_owed(db):
    placed = H.seed(db, "on_time_a")  # placed by the rule
    corrected = H.seed(db, "amended_impossible_year")  # the filer's own 4/A
    clean = H.seed(db, "signed_ahead")  # nothing held
    for sid in (placed, corrected, clean):
        assert _flags(db, sid, date(2024, 1, 2)) == []


def test_the_flag_clears_when_every_owed_row_is_decided(db):
    sid = H.seed(db, "late")
    for it in insider_hold_repo.items(db, security_ids=[sid]):
        insider_hold_repo.dismiss(db, it.id, "not a trade")
    db.commit()
    assert _flags(db, sid, date(2021, 4, 1)) == []


def test_a_history_view_is_judged_on_the_public_clock(db):
    """Before the filing was public there is nothing to flag; a decision made today does not clear a past
    night's flag (it counts from when it was made)."""
    sid = H.seed(db, "late")
    night = date(2021, 3, 17)
    assert _flags(db, sid, night, clock="public", known_at=known_at_for_asof(night, H.FAR)) == []
    for it in insider_hold_repo.items(db, security_ids=[sid]):
        insider_hold_repo.dismiss(db, it.id, "not a trade")
    db.commit()
    past = date(2021, 4, 1)
    (flag,) = _flags(db, sid, past, clock="public", known_at=known_at_for_asof(past, H.FAR))
    assert flag.columns == ("ins_30d", "ins_90d")
    now = datetime.now(timezone.utc)
    assert _flags(db, sid, past, clock="public", known_at=now) == []
