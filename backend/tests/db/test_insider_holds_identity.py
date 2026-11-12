"""Rule ``2026-11-insider-rows-dated-after-filing`` changes NOTHING for a security with no hold: on every committed
Form 4/A fixture (``tests/form4a_fixtures.py`` — real filings, every disposition), the read on this branch equals
the read before the held-row step existed (main's seam: the 4/A resolution, the joint-report collapse, the
internal-transfer screen), row for row and value for value, on both clocks.
"""

from __future__ import annotations

from datetime import date

import pytest

from db import bitemporal, insider_resolution
from db.session import DEFAULT_TENANT_ID
from domain.insider_amendments import ResolutionInputs, resolve_insider_rows
from domain.insider_holds import resolve_holds
from domain.insider_internal_transfers import screen_internal_transfers
from domain.insider_joint_reports import collapse_joint_reports
from domain.market_time import known_at_for_asof
from tests import form4a_fixtures as F
from tests.held_fixtures import FAR

_ASOFS = (
    date(2012, 6, 1),
    date(2024, 12, 31),
    date(2025, 8, 15),
    date(2026, 6, 15),
    date(2026, 10, 20),
)


def _main_seam(inputs: ResolutionInputs, sid, rows, asof):
    """``ResolutionInputs.resolve`` as main has it (cbf9e191): no held-row step."""
    key = str(sid)
    resolved = resolve_insider_rows(
        rows,
        amendments=inputs.amendments.get(key, ()),
        links=inputs.links.get(key, ()),
        decisions=inputs.decisions.get(key, ()),
        own_ciks=inputs.own_ciks.get(key, ()),
        asof=asof,
        lower=None,
    )
    return screen_internal_transfers(collapse_joint_reports(resolved), resolved)


@pytest.mark.parametrize("clock", ["record", "public"])
def test_no_hold_reads_exactly_as_before_on_every_amendment_fixture(db, monkeypatch, clock):
    groups = sorted({f.key for f in F.FILINGS.values()})
    sids = [F.seed(db, g) for g in groups]
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_txn_hold")
        assert cur.fetchone()["n"] == 0  # the new condition holds no row of these filings
    real_load = insider_resolution.load_inputs
    real_resolve = ResolutionInputs.resolve
    compared = 0
    # the night's cap, and FAR (the fixtures are recorded now: a record-clock read sees them only then)
    pins = [(asof, k) for asof in _ASOFS for k in (known_at_for_asof(asof, FAR), FAR)]
    for asof, known_at in pins:
        for sid in sids:
            branch = bitemporal.as_of(
                db,
                "fact_insider_txn",
                security_id=sid,
                asof=asof,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
                clock=clock,
            )
            inputs = real_load(
                db, [sid], known_at=known_at, tenant_id=DEFAULT_TENANT_ID, clock=clock
            )
            assert not inputs.holds and not inputs.hold_decisions
            # the raw as-of rows (no resolution at all), then main's seam over them
            monkeypatch.setattr(
                ResolutionInputs, "resolve", lambda self, sid, rows, **k: list(rows)
            )
            raw = bitemporal.as_of(
                db,
                "fact_insider_txn",
                security_id=sid,
                asof=asof,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
                clock=clock,
            )
            monkeypatch.setattr(ResolutionInputs, "resolve", real_resolve)
            assert branch == _main_seam(inputs, sid, raw, asof)
            compared += len(branch)
    assert compared > 50  # not vacuous


def test_resolve_holds_returns_the_same_list_when_there_is_no_form_4_hold():
    rows = [{"accession": "a", "txn_seq": 0}]
    out = resolve_holds(
        rows,
        holds=[],
        hold_decisions=[],
        amendments=[],
        links=[],
        decisions=[],
        asof=date(2026, 1, 1),
        on_time_days=5,
    )
    assert out is rows
    # a 4/A hold is not applied by the read: still the same list
    out = resolve_holds(
        rows,
        holds=[{"form": "4/A", "accession": "a", "txn_seq": 0}],
        hold_decisions=[],
        amendments=[],
        links=[],
        decisions=[],
        asof=date(2026, 1, 1),
        on_time_days=5,
    )
    assert out is rows
