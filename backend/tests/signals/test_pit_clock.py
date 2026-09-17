from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from signals.base import PointInTimeData

# THE ONE-CLOCK RULE on the MAIN CALL PATH (``db/clock.py``).
#
# ``recorded_at`` is stamped by the column default ``now()`` -- the DATABASE's clock. The PIT's default
# ``known_at`` used to come from ``datetime.now()`` on the HOST, so ``db.bitemporal._as_of``'s gate
# (``recorded_at <= known_at``) compared two clocks across a margin MEASURED at ~1.5 ms. A host clock 2 ms
# behind the database hid a just-written fact in 84 of 100 replays. Skew drifts and a sleep/resume
# perturbs it, which is why the symptom comes and goes -- so it is injected here rather than waited for.
#
# Three tests, three different claims: the host clock cannot hide a fact; the bound still CAPS (so "never
# hides" is not satisfied by never bounding); and an explicitly supplied bound is still honored (the
# scrub-back and every replay pin depend on it).


class _LaggingHostClock(datetime):
    """A host clock five seconds behind the database -- real skew, made deterministic."""

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) - timedelta(seconds=5)


def _buy(security_id, *, accession, valid_from, usd, recorded_at=None):
    v = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "insider_name": "CEO",
        "txn_code": "P",
        "usd": usd,
        "accession": accession,
        "valid_from": valid_from,
    }
    if recorded_at is not None:
        v["recorded_at"] = recorded_at
    return v


def test_a_lagging_host_clock_cannot_hide_a_just_ingested_fact(db, security_id, monkeypatch):
    """The regression, on the path that matters. With the bound taken from the host clock this fails;
    taken from the clock that STAMPS ``recorded_at`` it cannot, because the host clock is no longer in
    the comparison at all."""
    monkeypatch.setattr("signals.base.datetime", _LaggingHostClock)
    append_fact(
        db,
        "fact_insider_txn",
        _buy(security_id, accession="a-1", valid_from=date(2026, 6, 1), usd=1),
    )
    db.commit()

    pit = PointInTimeData(db, asof=date(2026, 6, 30))
    assert [r["accession"] for r in pit.insider_txns(security_id)] == ["a-1"]


def test_the_default_bound_still_caps_a_later_write(db, security_id):
    """...and the fix did not turn the cap off. The bound is pinned at CONSTRUCTION, so a fact whose
    transaction BEGINS afterwards is invisible to that view -- the transaction-axis no-lookahead the bound
    exists for. Without this, "never hides a fact" could be satisfied by never bounding anything.

    The explicit ``commit()`` is load-bearing (the #359 precedent): ``recorded_at`` defaults to ``now()``,
    which is TRANSACTION-START, and reading the database clock opens a transaction on this same connection
    when none is open -- so without the commit the next write JOINS that transaction and inherits a
    ``recorded_at`` from before the clock was read. An artifact of one connection doing both jobs; on the
    serve path a connection holding a PIT does not write facts."""
    append_fact(
        db,
        "fact_insider_txn",
        _buy(security_id, accession="a-1", valid_from=date(2026, 6, 1), usd=1),
    )
    db.commit()
    pit = PointInTimeData(db, asof=date(2026, 6, 30))
    db.commit()  # close the transaction db_now() opened, so the next write starts strictly later
    append_fact(
        db,
        "fact_insider_txn",
        _buy(security_id, accession="a-2", valid_from=date(2026, 6, 2), usd=2),
    )
    db.commit()

    assert [r["accession"] for r in pit.insider_txns(security_id)] == ["a-1"]
    # ...and a view built now sees both: the row was there, it was the BOUND that excluded it
    fresh = PointInTimeData(db, asof=date(2026, 6, 30))
    assert len(fresh.insider_txns(security_id)) == 2


def test_an_explicit_known_at_is_still_honored(db, security_id):
    """The scrub-back (``serve_known_at``), the cron's pin, ``pipeline.backfill``'s pinned run and every
    replay pin supply their own bound. The database default must apply ONLY when none was given."""
    early = datetime(2026, 6, 5, tzinfo=timezone.utc)
    late = datetime(2026, 6, 20, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _buy(security_id, accession="a-1", valid_from=date(2026, 6, 1), usd=1, recorded_at=early),
    )
    append_fact(
        db,
        "fact_insider_txn",
        _buy(security_id, accession="a-2", valid_from=date(2026, 6, 2), usd=2, recorded_at=late),
    )
    db.commit()

    pinned = PointInTimeData(db, asof=date(2026, 6, 30), known_at=early)
    assert [r["accession"] for r in pinned.insider_txns(security_id)] == ["a-1"]
    assert pinned.known_at == early  # not replaced by the database clock


def test_the_bound_is_pinned_once_not_resolved_per_read(db, security_id):
    """One view, one bound: two accessors of the same PIT must answer against the same instant, or a
    long-running request could see a fact through one read and not another."""
    append_fact(
        db,
        "fact_insider_txn",
        _buy(security_id, accession="a-1", valid_from=date(2026, 6, 1), usd=1),
    )
    db.commit()
    pit = PointInTimeData(db, asof=date(2026, 6, 30))
    first = pit.known_at
    pit.insider_txns(security_id)
    pit.price_history(security_id)
    assert pit.known_at == first
