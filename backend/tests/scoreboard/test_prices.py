from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from db.session import DEFAULT_TENANT_ID
from scoreboard.prices import PgRealizedPrices
from tests.scoreboard.helpers import bar

# The Postgres twin of replay's RealizedPrices: latest recorded version per (security_id, d),
# capped at the request asof on the valid axis and at known_at on the transaction axis.


def _reader(db, cap, known_at=None):
    return PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap, known_at=known_at)


def test_reversioned_bar_latest_version_wins(db, security_id):
    t1 = datetime(2026, 6, 2, 12, tzinfo=timezone.utc)
    t2 = t1 + timedelta(days=1)
    bar(db, security_id, date(2026, 6, 1), 100.0, recorded_at=t1)
    bar(db, security_id, date(2026, 6, 1), 105.0, recorded_at=t2)  # the restated bar

    r = _reader(db, cap=date(2026, 12, 31))
    assert r.last_close_through(security_id, date(2026, 6, 30)) == (date(2026, 6, 1), 105.0)

    # pinned BEFORE the restatement: the original version is what was known
    r_pinned = _reader(db, cap=date(2026, 12, 31), known_at=t1)
    assert r_pinned.last_close_through(security_id, date(2026, 6, 30)) == (date(2026, 6, 1), 100.0)


def test_cap_excludes_later_days_on_every_method(db, security_id):
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 5), 110.0)
    bar(db, security_id, date(2026, 6, 20), 200.0)  # beyond the cap

    r = _reader(db, cap=date(2026, 6, 10))
    assert r.last_close_through(security_id, date(2026, 7, 1)) == (date(2026, 6, 5), 110.0)
    assert r.first_close_on_or_after(security_id, date(2026, 6, 15)) is None
    assert r.closes_between(security_id, date(2026, 6, 1), date(2026, 7, 1)) == [
        (date(2026, 6, 1), 100.0),
        (date(2026, 6, 5), 110.0),
    ]


def test_null_close_skipped_and_ordering(db, security_id):
    bar(db, security_id, date(2026, 6, 2), None)  # a bar with no close (parity: DuckDB twin skips)
    bar(db, security_id, date(2026, 6, 3), 103.0)
    bar(db, security_id, date(2026, 6, 1), 101.0)

    r = _reader(db, cap=date(2026, 6, 30))
    assert r.first_close_on_or_after(security_id, date(2026, 6, 2)) == (date(2026, 6, 3), 103.0)
    assert r.closes_between(security_id, date(2026, 6, 1), date(2026, 6, 30)) == [
        (date(2026, 6, 1), 101.0),
        (date(2026, 6, 3), 103.0),
    ]
