"""The re-version pass (source-strategy Option A, operator pick 2026-07-11) — restated bars get a
new VERSION, the store stays append-only, and replay integrity holds.

The problem it closes: Yahoo re-bases the WHOLE history on a split while the ingest is incremental,
so a name splitting mid-thesis accumulated mixed-basis stored bars (a false cliff for the breakout
detector; a mis-graded volume gate). The pass compares the fresh pull's OVERLAP with the stored
latest-per-date and appends a new version where they differ beyond float noise — price AND volume.

The headline tests: the simulated-split regression (the stored series SNAPS to the new basis in one
pass, and a re-run appends ZERO — COUNT the table, not the read), and the bitemporal crown jewel: a
replay pinned to a ``known_at`` BEFORE the re-version still sees the OLD basis (the correction is
transaction-time honest — history is amended forward, never rewritten).
"""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.prices.eod_loader import ingest_prices, stored_bars
from ingest.prices.ingest_security import ingest_bars_for_security
from signals.base import PointInTimeData

D1, D2, D3, D4 = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)


def _bar(d: date, close: float, volume: float) -> dict:
    return {"d": d, "open": close, "high": close, "low": close, "close": close, "volume": volume}


class _Source:
    """A canned PriceSource — the seam makes the re-version pass testable without a network."""

    def __init__(self, bars: list[dict]):
        self.bars = bars

    def get_bars(self, ticker: str, *, allow_live: bool = True, force_refresh: bool = False):
        return self.bars


def _sec(security_id) -> Security:
    return Security(
        id=security_id, tenant_id=DEFAULT_TENANT_ID, ticker="SPLT", name="Splitter Corp"
    )


def _count(db, security_id) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (security_id,)
        )
        return cur.fetchone()["n"]


def test_a_rebased_series_reversions_and_snaps_then_rerun_appends_zero(db, security_id):
    """THE SIMULATED-SPLIT REGRESSION. Stored: three old-basis bars (close 100, vol 1k). The source
    re-bases 10:1 (close 10, vol 10k) and adds a new day. One pass: the tail appends AND the three
    restated bars re-version; the deduped read snaps to the NEW basis for every date. A second pass
    over the same series appends ZERO rows — count the table, never just the read."""
    ingest_prices(db, security_id, [_bar(D1, 100, 1000), _bar(D2, 102, 1100), _bar(D3, 104, 1200)])
    db.commit()

    rebased = [
        _bar(D1, 10.0, 10_000),
        _bar(D2, 10.2, 11_000),
        _bar(D3, 10.4, 12_000),
        _bar(D4, 10.6, 13_000),
    ]
    res = ingest_bars_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_Source(rebased)
    )
    db.commit()
    assert res.appended == 1 and res.reversioned == 3
    assert _count(db, security_id) == 3 + 4  # the old versions STAY (append-only) + the new basis

    snapped = stored_bars(db, security_id)
    assert float(snapped[D1]["close"]) == 10.0 and float(snapped[D1]["volume"]) == 10_000
    assert float(snapped[D3]["close"]) == 10.4  # every overlap date snapped, not just the first

    res2 = ingest_bars_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_Source(rebased)
    )
    db.commit()
    assert res2.appended == 0 and res2.reversioned == 0
    assert _count(db, security_id) == 7  # COUNT the table: idempotent


def test_an_unchanged_overlap_reversions_nothing_and_float_noise_is_not_a_restatement(
    db, security_id
):
    ingest_prices(db, security_id, [_bar(D1, 100, 1000), _bar(D2, 102, 1100)])
    db.commit()
    same_plus_noise = [
        _bar(D1, 100.0 + 1e-12, 1000),  # repr jitter, NOT a restatement
        _bar(D2, 102.0, 1100),
        _bar(D3, 104.0, 1200),  # the genuine incremental tail
    ]
    res = ingest_bars_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_Source(same_plus_noise)
    )
    db.commit()
    assert res.appended == 1 and res.reversioned == 0
    assert _count(db, security_id) == 3


def test_a_volume_only_restatement_reversions(db, security_id):
    """The D2 lesson lives on: the volume-confirmation gate reads volume — a re-base that moved
    volume but (hypothetically) not close must still re-version."""
    ingest_prices(db, security_id, [_bar(D1, 100, 1000)])
    db.commit()
    res = ingest_bars_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        source=_Source([_bar(D1, 100, 10_000)]),
    )
    db.commit()
    assert res.reversioned == 1
    assert float(stored_bars(db, security_id)[D1]["volume"]) == 10_000


def test_a_backfilled_hole_in_the_overlap_is_stored(db, security_id):
    """An unseen date INSIDE the overlap (the source grew history backward) is new information —
    stored through the same pass, counted with the exceptional path."""
    ingest_prices(db, security_id, [_bar(D1, 100, 1000), _bar(D3, 104, 1200)])
    db.commit()
    res = ingest_bars_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        source=_Source([_bar(D1, 100, 1000), _bar(D2, 102, 1100), _bar(D3, 104, 1200)]),
    )
    db.commit()
    assert res.appended == 0 and res.reversioned == 1
    assert D2 in stored_bars(db, security_id)


def test_replay_before_the_reversion_still_sees_the_old_basis(db, security_id):
    """THE BITEMPORAL CROWN JEWEL: the re-version amends history FORWARD (a new recorded_at), so a
    replay pinned to a known_at BEFORE the correction sees the series as it was believed then — the
    old basis. No-lookahead's mirror image: no look-BACK rewriting either."""
    ingest_prices(db, security_id, [_bar(D1, 100, 1000)])
    db.commit()
    # The pin must come from the clock that stamps recorded_at — Postgres — never the host: the
    # margin to the next transaction is sub-millisecond, so any momentary host<->container skew
    # puts a host-clock pin AT/AFTER the correction and the replay sees it. clock_timestamp(),
    # not now(), for the current instant (now() is the txn start), and commit, so the
    # correction's transaction — whose now() stamps recorded_at — begins strictly later.
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        before_fix = cur.fetchone()["t"]
    db.commit()

    ingest_bars_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        source=_Source([_bar(D1, 10.0, 10_000)]),  # the split re-base lands AFTER before_fix
    )
    db.commit()

    then = PointInTimeData(db, asof=D4, known_at=before_fix, tenant_id=DEFAULT_TENANT_ID)
    now = PointInTimeData(db, asof=D4, tenant_id=DEFAULT_TENANT_ID)
    assert float(then.price_history(security_id)[-1]["close"]) == 100.0  # as it was believed
    assert float(now.price_history(security_id)[-1]["close"]) == 10.0  # as it is known now


def test_a_ticker_less_security_contributes_nothing(db):
    res = ingest_bars_for_security(
        db,
        Security(id=uuid.uuid4(), tenant_id=DEFAULT_TENANT_ID, ticker=None, name="Unlisted"),
        tenant_id=DEFAULT_TENANT_ID,
        source=_Source([_bar(D1, 1, 1)]),
    )
    assert res.appended == 0 and res.reversioned == 0 and res.total == 0
