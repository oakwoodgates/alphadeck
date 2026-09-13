"""F2 — the per-security fund-shares leg against the real DB (the `db` fixture). The headline is the
COUNT-THE-TABLE idempotency gate: a re-sample of the same (d, shares_out) appends ZERO rows — asserted
by counting the table, because the bitemporal read dedups, so a duplicate append would hide behind a
correct read while the table silently grows."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from domain.enums import InstrumentKind
from domain.security import Security
from ingest.funds.ingest_security import (
    FundSharesResult,
    fund_shares_applies,
    ingest_fund_shares_for_security,
    latest_shares_date,
)
from ingest.funds.source import FundSharesUnavailable

_D = date(2026, 7, 24)


class _StubSource:
    """A FundSharesSource stub returning a fixed snapshot (or None), counting its calls."""

    def __init__(self, snap):
        self.snap, self.calls = snap, 0

    def get_snapshot(self, ticker, *, allow_live=False, force_refresh=False):
        self.calls += 1
        return self.snap


def _snap(d=_D, shares=138771666.0, source="globalx"):
    return {
        "d": d,
        "shares_out": shares,
        "source": source,
        "source_ref": "https://www.globalxetfs.com/funds/ura",
    }


def _sec(security_id, *, ticker="URA", kind=InstrumentKind.ETF) -> Security:
    return Security(
        id=security_id, tenant_id=DEFAULT_TENANT_ID, ticker=ticker, instrument_kind=kind
    )


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) n FROM fact_fund_shares")
        return cur.fetchone()["n"]


def test_etf_member_samples_one_row_with_provenance(db, security_id):
    src = _StubSource(_snap())
    res = ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=src
    )
    db.commit()
    assert res == FundSharesResult(1, 0)
    assert _count(db) == 1
    rows = as_of(
        db,
        "fact_fund_shares",
        security_id=security_id,
        asof=date(2026, 7, 30),
        known_at=datetime(2100, 1, 1, tzinfo=timezone.utc),
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert len(rows) == 1
    assert float(rows[0]["shares_out"]) == 138771666.0
    assert rows[0]["d"] == _D and rows[0]["valid_from"] == _D  # the PAGE'S date, not "today" (#1)
    assert rows[0]["source"] == "globalx"  # which adapter produced it (#6)
    assert rows[0]["source_ref"].startswith("https://")


def test_resample_of_same_day_and_count_appends_zero_count_the_table(db, security_id):
    """THE idempotency gate: the same (d, shares_out) re-sampled appends NOTHING — the table count is
    the assertion, not the read."""
    src = _StubSource(_snap())
    ingest_fund_shares_for_security(db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=src)
    db.commit()
    before = _count(db)

    res = ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=src
    )
    db.commit()

    assert res == FundSharesResult(0, 0)
    assert _count(db) == before  # the TABLE did not grow


def test_changed_count_same_day_appends_a_new_version(db, security_id):
    """The re-version idiom: a restated count for an already-sampled d (the aggregator's rounded sample
    corrected by the issuer's exact one) is a NEW version — the read snaps to it, the history keeps both.
    """
    rounded = _StubSource(_snap(shares=138770000.0, source="stockanalysis"))
    exact = _StubSource(_snap(shares=138771666.0, source="globalx"))
    ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=rounded
    )
    db.commit()

    res = ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=exact
    )
    db.commit()

    assert res == FundSharesResult(0, 1)  # reversioned, not appended
    assert _count(db) == 2  # both versions kept (append-only)
    rows = as_of(
        db,
        "fact_fund_shares",
        security_id=security_id,
        asof=date(2026, 7, 30),
        known_at=datetime(2100, 1, 1, tzinfo=timezone.utc),
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert len(rows) == 1 and float(rows[0]["shares_out"]) == 138771666.0  # the latest version wins


def test_new_day_appends_alongside_the_prior_day(db, security_id):
    ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_StubSource(_snap())
    )
    db.commit()
    res = ingest_fund_shares_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        source=_StubSource(_snap(d=date(2026, 7, 27), shares=139000000.0)),
    )
    db.commit()
    assert res == FundSharesResult(1, 0)
    assert _count(db) == 2  # two days, two samples — the series accrues


def test_non_etf_member_is_a_no_op_that_never_touches_the_source(db, security_id):
    src = _StubSource(_snap())
    res = ingest_fund_shares_for_security(
        db, _sec(security_id, kind=InstrumentKind.EQUITY), tenant_id=DEFAULT_TENANT_ID, source=src
    )
    assert res == FundSharesResult(0, 0)
    assert src.calls == 0  # the gate short-circuits BEFORE the source (no network for equities)
    assert _count(db) == 0


def test_tickerless_etf_is_a_no_op(db, security_id):
    src = _StubSource(_snap())
    res = ingest_fund_shares_for_security(
        db, _sec(security_id, ticker=None), tenant_id=DEFAULT_TENANT_ID, source=src
    )
    assert res == FundSharesResult(0, 0) and src.calls == 0


def test_source_returning_none_raises_fail_visible(db, security_id):
    """A bare adapter's None (no coverage) is the same visible no-source condition the composite
    raises — never a quiet zero-sample success (#7/#9)."""
    with pytest.raises(FundSharesUnavailable, match="no samplable"):
        ingest_fund_shares_for_security(
            db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_StubSource(None)
        )


def test_writes_under_the_given_tenant(db, security_id):
    """Facts land under the caller's tenant (the thesis's), and a sample is invisible to a replay
    pinned before it was recorded (recorded_at = now, never backdated)."""
    ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_StubSource(_snap())
    )
    db.commit()
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    invisible = as_of(
        db,
        "fact_fund_shares",
        security_id=security_id,
        asof=date(2026, 7, 30),
        known_at=past,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert invisible == []  # ingested "now" → not knowable at a past transaction time (#1)

    with db.cursor() as cur:
        cur.execute("SELECT tenant_id FROM fact_fund_shares")
        assert {r["tenant_id"] for r in cur.fetchall()} == {uuid.UUID(str(DEFAULT_TENANT_ID))}


# --- F1: the leg's gate, extracted, and the sample-edge read the monitor judges ----------------------


def test_fund_shares_applies_is_the_LEGS_OWN_gate(db, security_id):
    """ONE definition with two readers: the leg's early return and the recency monitor's edge read. They
    must agree, or the monitor would either judge members the leg never samples (reporting every equity as
    a dead fund tape — its edge is None, and None means stale) or skip ones it does.

    Proved against the leg's actual behavior rather than asserted twice: where the predicate says False,
    the leg is a no-op that never touches the source."""
    equity = _sec(security_id, kind=InstrumentKind.EQUITY)
    tickerless = _sec(security_id, ticker=None)
    sleeve = _sec(security_id)

    assert fund_shares_applies(sleeve) is True
    assert fund_shares_applies(equity) is False
    assert fund_shares_applies(tickerless) is False

    for sec in (equity, tickerless):
        src = _StubSource(_snap())
        assert ingest_fund_shares_for_security(
            db, sec, tenant_id=DEFAULT_TENANT_ID, source=src
        ) == FundSharesResult(0, 0)
        assert src.calls == 0  # the source is never even constructed for these


def test_latest_shares_date_is_the_newest_SAMPLE_unaffected_by_re_versions(db, security_id):
    """The sample edge the monitor judges: a plain MAX over stored sample dates. ``None`` before anything
    is stored (a sampler that has never worked — the loudest version of the failure, not an exempt one),
    and a RESTATED count for a day already sampled is a new VERSION of that day, so it must not move the
    edge backwards or forwards."""
    assert latest_shares_date(db, security_id, tenant_id=DEFAULT_TENANT_ID) is None

    ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_StubSource(_snap())
    )
    db.commit()
    assert latest_shares_date(db, security_id, tenant_id=DEFAULT_TENANT_ID) == _D

    # a LATER sample moves the edge; a restatement of an earlier day does not move it back
    later = date(2026, 7, 27)
    ingest_fund_shares_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        source=_StubSource(_snap(d=later, shares=999.0)),
    )
    db.commit()
    assert latest_shares_date(db, security_id, tenant_id=DEFAULT_TENANT_ID) == later

    res = ingest_fund_shares_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        source=_StubSource(_snap(d=_D, shares=555.0)),  # the FIRST day, corrected
    )
    db.commit()
    assert res.reversioned == 1  # it really was a re-version
    assert latest_shares_date(db, security_id, tenant_id=DEFAULT_TENANT_ID) == later


def test_latest_shares_date_is_TENANT_scoped(db, security_id):
    """The same tenancy discipline as every other fact read: another tenant's samples are not this
    tenant's edge."""
    ingest_fund_shares_for_security(
        db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=_StubSource(_snap())
    )
    db.commit()
    other = uuid.UUID("00000000-0000-0000-0000-0000000002a0")
    assert latest_shares_date(db, security_id, tenant_id=other) is None
