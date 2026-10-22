"""R8-A — the GUARD on the amendment classifier's signal code set (operator-approved 2026-10-18).

A Form 4/A that changes no row a CALL signal reads resolves itself, so the set of codes the signals read must
never be narrower than what they really read. It is DERIVED (``signals.horizons.insider_signal_codes``) from
each registered detector's declared ``txn_codes``; these tests make the declaration impossible to get wrong:

- every detector that declares ``fact_insider_txn`` in its horizons must declare its codes;
- a BEHAVIORAL probe feeds every registered detector (every master switch on) a strong, recent, senior cluster
  in EACH SEC Form 4 transaction code, and fails if a detector reacts to a code it did not declare — so a
  future detector reading another code fails here until it declares it (which widens the set automatically);
- each declared code really fires its detector on the probe (the probe is not vacuous).

The probe rows are a contract test's direct inputs, not data about any company."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from domain.config import DEFAULT_CONFIG
from signals import registered_detectors
from signals.base import Detector
from signals.horizons import INSIDER_TABLE, insider_signal_codes

# SEC Form 4 Table I transaction codes (General Instructions, item 8)
SEC_TXN_CODES = (
    "P",
    "S",
    "V",
    "A",
    "D",
    "F",
    "I",
    "M",
    "C",
    "E",
    "H",
    "O",
    "X",
    "G",
    "L",
    "W",
    "Z",
    "J",
    "K",
    "U",
)
ASOF = date(2026, 10, 16)
ROLES = ("Chief Executive Officer", "Chief Financial Officer", "Director")


def _all_switches_on():
    update = {
        name: True
        for name, value in DEFAULT_CONFIG.model_dump().items()
        if name.endswith("_enabled") and isinstance(value, bool)
    }
    return DEFAULT_CONFIG.model_copy(update=update)


CFG = _all_switches_on()


def _probe_rows(code: str, sid: UUID) -> list[dict]:
    rows = []
    for i, role in enumerate(ROLES):
        for k in range(3):
            rows.append(
                {
                    "id": uuid4(),
                    "security_id": sid,
                    "insider_name": f"Insider {i}",
                    "insider_role": role,
                    "txn_code": code,
                    "shares": 10_000.0,
                    "price": 50.0,
                    "usd": 500_000.0,
                    "accession": f"0000000000-26-{i:03d}{k:03d}",
                    "valid_from": ASOF - timedelta(days=1 + k),
                    "txn_seq": k,
                    "aff_10b5_1": False,
                    "issuer_cik": "1000",
                    "issuer_name": "Probe Corp",
                    "rpt_owner_cik": f"200{i}",
                    "security_title": "Common Stock",
                    "issuer_foreign_symbol": None,
                    "recorded_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
                    "accepted": None,
                }
            )
    return rows


class _ProbePIT:
    """A point-in-time view holding ONLY the probe's insider rows (every other read is empty)."""

    def __init__(self, rows: list[dict]) -> None:
        self.asof = ASOF
        self.known_at = datetime(2026, 10, 17, tzinfo=timezone.utc)
        self.tenant_id = uuid4()
        self._rows = rows

    def insider_txns(self, security_id):
        return list(self._rows)

    def price_history(self, security_id, lookback_days=None):
        return []

    def dilution_facts(self, security_id):
        return []

    def catalyst_facts(self, security_id):
        return []

    def fundamentals_facts(self, security_id):
        return []

    def corporate_event_facts(self, security_id):
        return []

    def activist_stake_facts(self, security_id):
        return []

    def theme_conviction_facts(self, thesis_id):
        return []

    def security_name(self, security_id):
        return "Probe Corp"

    def security_cik(self, security_id):
        return "1000"

    def split_events(self, security_id):
        return []  # no split listed (S2)


def test_every_insider_reader_declares_its_codes_and_the_set_is_derived_from_them():
    readers = [d for d in registered_detectors() if INSIDER_TABLE in d.horizons(DEFAULT_CONFIG)]
    assert readers, "no registered detector reads fact_insider_txn — the guard would be vacuous"
    for d in readers:
        assert d.txn_codes, f"{d.name} reads {INSIDER_TABLE} without declaring txn_codes"
    derived = insider_signal_codes(DEFAULT_CONFIG)
    assert set(derived) == set().union(*(d.txn_codes for d in readers))
    for d in readers:  # the horizon per code is the detector's own declaration
        for code in d.txn_codes:
            assert (
                derived[code] is None or derived[code] >= d.horizons(DEFAULT_CONFIG)[INSIDER_TABLE]
            )


@pytest.mark.parametrize("code", SEC_TXN_CODES)
def test_no_detector_reacts_to_a_code_it_did_not_declare(code):
    sid = uuid4()
    pit = _ProbePIT(_probe_rows(code, sid))
    for det in registered_detectors():
        fired = det(pit, sid, ASOF, CFG) is not None
        if fired:
            assert code in det.txn_codes, (
                f"detector {det.name!r} reacts to Form 4 code {code!r} but declares {sorted(det.txn_codes)} — "
                "declare it (txn_codes) so the amendment classifier counts those rows as signal rows"
            )


def test_each_declared_code_really_fires_its_detector():
    for det in registered_detectors():
        for code in det.txn_codes:
            sid = uuid4()
            assert (
                det(_ProbePIT(_probe_rows(code, sid)), sid, ASOF, CFG) is not None
            ), f"the probe does not fire {det.name!r} on its declared code {code!r} — the guard is vacuous"


def test_a_reader_without_declared_codes_is_a_registry_error(monkeypatch):
    import signals.horizons as horizons

    blind = Detector(
        name="blind_reader",
        detect=lambda pit, sid, asof, cfg: None,
        horizons=lambda cfg: {INSIDER_TABLE: 30},
    )
    monkeypatch.setattr(horizons, "registered_detectors", lambda: (blind,))
    with pytest.raises(ValueError, match="declares no txn_codes"):
        horizons.insider_signal_codes(DEFAULT_CONFIG)


# --- R8-B — the review horizon's DERIVATION GUARD ----------------------------------------------------------


def test_the_review_horizon_is_the_backtest_window_plus_the_longest_insider_read():
    """The dial's default is DERIVED, never hand-typed: the backtest window plus the longest horizon any call
    detector declares on fact_insider_txn. Pinned at today's config (730 + P's 210 = 940) so a dial change is
    review-visible, and stated as the formula so a widened detector widens it with no edit."""
    from signals.horizons import BACKTEST_WINDOW_DAYS, insider_amendment_review_horizon

    reads = insider_signal_codes(DEFAULT_CONFIG)
    assert reads and all(v is not None for v in reads.values())
    assert insider_amendment_review_horizon(DEFAULT_CONFIG) == BACKTEST_WINDOW_DAYS + max(
        reads.values()
    )
    assert insider_amendment_review_horizon(DEFAULT_CONFIG) == 940


def test_the_backtest_window_is_the_price_tapes_two_year_range():
    """BACKTEST_WINDOW_DAYS states the member price tape's window (no bars -> no call before it). A wider tape
    (a new DEFAULT_RANGE) must widen the review horizon too — this fails until both move together.
    """
    from ingest.prices.source import DEFAULT_RANGE
    from signals.horizons import BACKTEST_WINDOW_DAYS

    assert (DEFAULT_RANGE, BACKTEST_WINDOW_DAYS) == ("2y", 730)


def test_the_review_horizon_tracks_the_detectors_and_an_unbounded_read_unbounds_it(monkeypatch):
    import signals.horizons as horizons

    wide = Detector(
        name="wide_reader",
        detect=lambda pit, sid, asof, cfg: None,
        horizons=lambda cfg: {INSIDER_TABLE: 400},
        txn_codes=frozenset({"P"}),
    )
    monkeypatch.setattr(horizons, "registered_detectors", lambda: (wide,))
    assert horizons.insider_amendment_review_horizon(DEFAULT_CONFIG) == 730 + 400
    forever = Detector(
        name="forever_reader",
        detect=lambda pit, sid, asof, cfg: None,
        horizons=lambda cfg: {INSIDER_TABLE: None},
        txn_codes=frozenset({"S"}),
    )
    monkeypatch.setattr(horizons, "registered_detectors", lambda: (wide, forever))
    assert horizons.insider_amendment_review_horizon(DEFAULT_CONFIG) is None


def test_the_setting_pins_the_review_horizon_else_it_is_derived(monkeypatch):
    from domain import settings as settings_mod
    from repositories import insider_amendment_repo

    base = settings_mod.get_settings()

    def _with(days):
        pinned = base.model_copy(update={"insider_amendment_review_horizon_days": days})
        monkeypatch.setattr(settings_mod, "get_settings", lambda: pinned)

    _with(None)
    assert insider_amendment_repo.review_horizon_days() == 940  # unset: derived
    _with(365)
    assert insider_amendment_repo.review_horizon_days() == 365
    _with(-5)
    assert insider_amendment_repo.review_horizon_days() == 0
