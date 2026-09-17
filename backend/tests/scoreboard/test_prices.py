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


# --- the ONE-CLOCK rule (the transaction-axis bound) ------------------------------------------------
# ``recorded_at`` is stamped by the column default ``now()`` -- the DATABASE's clock. The default
# ``known_at`` used to come from ``datetime.now()`` on the HOST, so the gate compared two clocks across a
# margin MEASURED at ~1.5 ms: a host clock 2 ms behind the database hid a just-written bar in 84 of 100
# replays, and the whole tests/scoreboard directory went from 103 passed to 8 failed under the same 2 ms.
# Skew drifts (a host sleep/resume perturbs it), which is why the symptom came and went. These two tests
# pin BOTH halves of the fix: the host clock can no longer hide a fact, and the bound still caps.


class _LaggingHostClock(datetime):
    """A host clock five seconds behind the database -- real skew, made deterministic."""

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) - timedelta(seconds=5)


def test_a_lagging_host_clock_cannot_hide_a_just_written_bar(db, security_id, monkeypatch):
    """The regression. With the bound taken from the host clock this fails; taken from the clock that
    STAMPS ``recorded_at`` it cannot, because the host clock is no longer in the comparison at all.
    """
    monkeypatch.setattr("scoreboard.prices.datetime", _LaggingHostClock)
    bar(db, security_id, date(2026, 6, 1), 101.0)

    r = _reader(db, cap=date(2026, 6, 30))
    assert r.closes_between(security_id, date(2026, 6, 1), date(2026, 6, 30)) == [
        (date(2026, 6, 1), 101.0)
    ]
    assert r.first_close_on_or_after(security_id, date(2026, 6, 1)) == (date(2026, 6, 1), 101.0)
    assert r.market_tape_edge(security_id) == date(2026, 6, 1)


def test_the_default_bound_still_caps_a_later_write(db, security_id):
    """...and the fix did not turn the cap off. The bound is pinned at CONSTRUCTION, so a bar whose
    transaction BEGINS afterwards is invisible to that reader -- the transaction-axis no-lookahead the
    bound exists for. Without this, "never hides a fact" could be satisfied by never bounding anything.

    The explicit ``commit()`` is load-bearing and is the #359 precedent ("a commit after reading the lower
    bound so the ingest's transaction begins strictly later"). ``recorded_at`` defaults to ``now()``, which
    is TRANSACTION-START time, and reading the database clock opens a transaction on this same connection
    when none is open -- so without the commit the next write JOINS that transaction and inherits a
    ``recorded_at`` from before the clock was read. That is an artifact of one connection doing both jobs,
    not of the cap: on the serve path the reader's connection never writes facts."""
    bar(db, security_id, date(2026, 6, 1), 101.0)
    r = _reader(db, cap=date(2026, 6, 30))
    db.commit()  # close the transaction db_now() opened, so the next write starts strictly later
    bar(db, security_id, date(2026, 6, 2), 102.0)  # recorded AFTER the reader pinned its bound

    assert r.closes_between(security_id, date(2026, 6, 1), date(2026, 6, 30)) == [
        (date(2026, 6, 1), 101.0)
    ]
    # a reader built now sees both -- the row is there, it was the BOUND that excluded it
    assert (
        len(
            _reader(db, cap=date(2026, 6, 30)).closes_between(
                security_id, date(2026, 6, 1), date(2026, 6, 30)
            )
        )
        == 2
    )
