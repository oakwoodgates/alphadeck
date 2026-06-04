from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.prices.eod_loader import parse_stooq_csv

_CSV = (Path(__file__).resolve().parent.parent / "fixtures" / "prices" / "DEVCO.csv").read_text(
    encoding="utf-8"
)


def test_parse_stooq_csv():
    rows = parse_stooq_csv(_CSV)
    assert len(rows) == 10
    assert rows[0]["d"] == date(2026, 5, 18)
    last = rows[-1]
    assert last["d"] == date(2026, 6, 1)
    assert last["close"] == 21.3
    assert last["volume"] == 2400000
