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
_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_BARS = parse_yahoo_chart(
    json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
)


def _through(d: date) -> list[dict]:
    return [b for b in _BARS if b["d"] <= d]


def test_breakout_off_before_confirmation():
    # 2026-05-28: Wells's open-market buy is known, but the momentum thrust hasn't confirmed (ret10d ~5%)
    assert (
        volume_breakout.score(_through(date(2026, 5, 28)), SID, date(2026, 5, 28), DEFAULT_CONFIG)
        is None
    )


def test_breakout_fires_momentum_only_on_hims():
    # 2026-06-01: the price breakout fires, but HIMS ran ~0.9x volume -> MOMENTUM-ONLY (flip), not volume-backed
    ev = volume_breakout.score(_through(date(2026, 6, 1)), SID, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.fired and ev.kind is Kind.TECHNICAL_BREAKOUT
    assert ev.grade is Grade.FLIP
    assert ev.provenance[0].detail["volume_backed"] is False


def test_not_enough_bars():
    assert volume_breakout.score(_BARS[:3], SID, date(2026, 1, 1), DEFAULT_CONFIG) is None


def test_breakout_stays_reported_through_consolidation():
    # 2026-06-03 is a consolidation bar (not a new high), but the 06-01 breakout is still inside its
    # alpha half-life -> the detector reports it stamped with its OWN bar date (06-01), no flicker.
    ev = volume_breakout.score(_through(date(2026, 6, 3)), SID, date(2026, 6, 3), DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.asof == date(2026, 6, 1)  # the breakout's bar date, not the query asof
    assert ev.provenance[0].ref == f"price:{SID}:2026-06-01"


def test_decayed_breakout_is_not_resurrected():
    # The prior breakout was 2026-04-20; by 2026-05-15 it is well past its alpha half-life, so the
    # freshness-bounded scan does not resurrect it -> None (no stale confirmation).
    assert (
        volume_breakout.score(_through(date(2026, 5, 15)), SID, date(2026, 5, 15), DEFAULT_CONFIG)
        is None
    )
