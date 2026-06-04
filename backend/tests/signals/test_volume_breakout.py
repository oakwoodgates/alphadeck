from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind
from ingest.prices.eod_loader import parse_stooq_csv
from signals import volume_breakout

SID = uuid4()
_BARS = parse_stooq_csv(
    (Path(__file__).resolve().parent.parent / "fixtures" / "prices" / "DEVCO.csv").read_text(
        encoding="utf-8"
    )
)


def test_breakout_fires_on_new_high_and_volume():
    ev = volume_breakout.score(_BARS, SID, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.kind is Kind.TECHNICAL_BREAKOUT and ev.grade is Grade.CORE


def test_no_breakout_without_the_breakout_bar():
    # drop the breakout bar -> the base never gets cleared
    assert volume_breakout.score(_BARS[:-1], SID, date(2026, 5, 29), DEFAULT_CONFIG) is None


def test_not_enough_bars():
    assert volume_breakout.score(_BARS[:3], SID, date(2026, 5, 20), DEFAULT_CONFIG) is None
