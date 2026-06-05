from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind
from ingest.prices.eod_loader import parse_yahoo_chart
from signals import volume_breakout

SID = uuid4()
# Real HIMS EOD (the M3 target), pulled live and committed for a reproducible, offline real-data test.
_BARS = parse_yahoo_chart(
    json.loads(
        (
            Path(__file__).resolve().parent.parent / "fixtures" / "prices" / "HIMS.yahoo.json"
        ).read_text(encoding="utf-8")
    )
)


def _through(d: date) -> list[dict]:
    return [b for b in _BARS if b["d"] <= d]


def test_breakout_off_before_confirmation():
    # 2026-05-28: Wells's open-market buy is known, but the momentum thrust hasn't confirmed (ret10d ~5%)
    assert (
        volume_breakout.score(_through(date(2026, 5, 28)), SID, date(2026, 5, 28), DEFAULT_CONFIG)
        is None
    )


def test_breakout_fires_on_the_momentum_move():
    # 2026-06-01: new short-term closing high + a >=8% 10-day thrust -> Key 2 arms (the real HIMS move)
    ev = volume_breakout.score(_through(date(2026, 6, 1)), SID, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.kind is Kind.TECHNICAL_BREAKOUT and ev.grade is Grade.CORE


def test_not_enough_bars():
    assert volume_breakout.score(_BARS[:3], SID, date(2026, 1, 1), DEFAULT_CONFIG) is None
