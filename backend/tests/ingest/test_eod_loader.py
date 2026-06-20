from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.prices.eod_loader import (
    ingest_prices,
    latest_bar_date,
    parse_stooq_csv,
    parse_yahoo_chart,
)

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


def test_parse_yahoo_chart():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1748908800, 1748995200, 1749081600],
                    "indicators": {
                        "quote": [
                            {
                                "open": [10.0, 11.0, None],  # a null bar is skipped
                                "high": [10.5, 12.0, None],
                                "low": [9.8, 10.9, None],
                                "close": [10.2, 11.8, None],
                                "volume": [1000, 2000, None],
                            }
                        ]
                    },
                }
            ]
        }
    }
    rows = parse_yahoo_chart(payload)
    assert len(rows) == 2  # the null close is dropped
    assert isinstance(rows[0]["d"], date) and rows[0]["d"] < rows[1]["d"]
    assert rows[0]["close"] == 10.2 and rows[1]["volume"] == 2000


def _bar(d: date) -> dict:
    return {"d": d, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0}


def test_latest_bar_date_none_then_max(db, security_id):
    assert latest_bar_date(db, security_id) is None  # nothing stored yet
    # insert out of order — the helper must return the MAX date, not the last inserted
    ingest_prices(
        db, security_id, [_bar(date(2026, 6, 15)), _bar(date(2026, 6, 17)), _bar(date(2026, 6, 16))]
    )
    db.commit()
    assert latest_bar_date(db, security_id) == date(2026, 6, 17)
