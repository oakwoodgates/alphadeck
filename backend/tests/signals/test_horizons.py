"""The horizon registry (``signals/horizons.py``) — Board/Cockpit perf PR-1b, proof obligation 4.

A read bound that rests on a comment silently truncates the day a reader outgrows it. So: every
registered detector / display member (and every thesis-level reader) DECLARES the tables it reads with
its max horizon; the PIT's bound is DERIVED; and this file is what fails when a reader has no
declaration, reads a table it didn't declare, or asks for a longer window than it declared. The derived
numbers are PINNED so a dial change is review-visible, never a silent widening or narrowing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from db.bitemporal import _FACT_IDENTITY
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role
from pipeline.seed import NUCLEAR_THESIS_ID, SMR_ID
from repositories import thesis_repo
from signals import laggard, registered_detectors, theme_conviction
from signals.base import Detector, PointInTimeData
from signals.common import fired_signal, source_provenance
from signals.display import (
    registered_display_members,
    relative_strength,
    sma,
    theme_breadth,
)
from signals.display.base import DisplayMember
from signals.horizons import (
    BOUNDED_TABLES,
    MARGIN_DAYS,
    call_bounds,
    call_horizons,
    derive_bounds,
    display_bounds,
    display_horizons,
)
from tests.pit_fixtures import PIN, SECS, seed_all, seed_benchmark_bars
from workbench import scoring

# Every master switch ON, so every detector actually READS in the recording run below (a switched-off
# detector touches nothing and would pass the subset check vacuously).
ALL_ON = DEFAULT_CONFIG.model_copy(
    update={
        "breakdown_dearm_enabled": True,
        "insider_sell_enabled": True,
        "corporate_catalyst_enabled": True,
        "corporate_risk_enabled": True,
        "share_creep_enabled": True,
        "activist_stake_enabled": True,
    }
)

# Readers that call ``price_history`` with NO lookback yet declare a FINITE price horizon — each one
# justified beside its declaration (the bars feed only a per-day map for rows the reader already
# windows by date). Any other WINDOWED accessor read bare must declare None. (An accessor with no
# window parameter at all — ``insider_txns`` and every other fact read — can only be read bare; there
# the declaration IS the reader's derived number, which is the registry's whole point.)
NO_WINDOW_PARAM = "n/a"  # the recorder's marker for an accessor that has no lookback parameter
JUSTIFIED_BARE_READS = {
    ("insider_conviction", "fact_price_eod"),
    ("insider_sell", "fact_price_eod"),
    ("insider_flow_90d", "fact_price_eod"),
    ("etf_flow", "fact_price_eod"),
}


# --- the declarations exist and are well-formed ------------------------------------------------------


def _thesis_level_declarations() -> dict[str, Mapping[str, int | None]]:
    return {
        "laggard": laggard.HORIZONS(DEFAULT_CONFIG),
        "theme_conviction": theme_conviction.HORIZONS(DEFAULT_CONFIG),
        "theme_breadth": theme_breadth.HORIZONS,
        "sector_rs": relative_strength.SECTOR_RS_HORIZONS,
        "workbench.scoring": scoring.HORIZONS,
    }


def test_every_registered_reader_declares_known_tables():
    decls = {d.name: d.horizons(DEFAULT_CONFIG) for d in registered_detectors()}
    decls |= {m.name: m.horizons for m in registered_display_members()}
    decls |= _thesis_level_declarations()
    for name, decl in decls.items():
        assert isinstance(decl, Mapping) and decl, f"{name}: an empty/missing declaration"
        assert set(decl) <= set(_FACT_IDENTITY), f"{name}: unknown table in {set(decl)}"
        for table, days in decl.items():
            assert days is None or (isinstance(days, int) and days > 0), (name, table, days)


def test_a_reader_without_a_declaration_cannot_be_constructed():
    with pytest.raises(TypeError):
        Detector(name="x", detect=lambda pit, sid, asof, cfg: None)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        DisplayMember(name="x", compute=lambda pit, sid, asof: None)  # type: ignore[call-arg]


# --- the derivation, pinned -----------------------------------------------------------------------------


def test_derived_bounds_are_pinned_to_the_dials():
    """The numbers the three PITs run with today — spelled out from the dials so a reviewer sees WHERE
    each comes from. Change a dial and this test names the new bound (review-visible by design)."""
    cfg = DEFAULT_CONFIG
    assert MARGIN_DAYS == 30 and BOUNDED_TABLES == {"fact_price_eod", "fact_insider_txn"}
    insider_call = (
        max(cfg.insider_core_alpha_liveness_days, cfg.insider_flip_alpha_liveness_days)
        + cfg.insider_cluster_window_days
    )
    price_call = max(
        cfg.breakout_52w_lookback_days,
        cfg.breakdown_core_lookback_days,
        cfg.laggard_lookback_days,
        cfg.breakout_lookback_days,
        insider_call,
    )
    assert call_bounds(cfg) == {
        "fact_insider_txn": insider_call + MARGIN_DAYS,
        "fact_price_eod": price_call + MARGIN_DAYS,
    }
    assert call_bounds(cfg) == {"fact_insider_txn": 240, "fact_price_eod": 460}
    assert display_bounds() == {
        "fact_insider_txn": None,  # insider_flow_90d declares None (its '—' vs '0/0' semantics)
        "fact_price_eod": sma.LOOKBACK_DAYS + MARGIN_DAYS,
    }
    assert display_bounds() == {"fact_insider_txn": None, "fact_price_eod": 630}
    assert scoring.scored_bounds() == {"fact_insider_txn": None, "fact_price_eod": None}


def test_a_widened_dial_widens_the_bound():
    wider = DEFAULT_CONFIG.model_copy(update={"insider_core_alpha_liveness_days": 400})
    assert call_bounds(wider)["fact_insider_txn"] == 400 + 30 + MARGIN_DAYS
    wider = DEFAULT_CONFIG.model_copy(update={"breakout_52w_lookback_days": 900})
    assert call_bounds(wider)["fact_price_eod"] == 900 + MARGIN_DAYS


def test_derive_bounds_rules():
    price, insider = "fact_price_eod", "fact_insider_txn"
    # max + margin over the readers that declare a table
    assert derive_bounds([{price: 100}, {price: 250}]) == {insider: None, price: 250 + MARGIN_DAYS}
    # ANY None unbounds the table for that PIT
    assert derive_bounds([{price: 100}, {price: None}]) == {insider: None, price: None}
    # a table nobody declares stays unbounded; an empty registry is fully unbounded
    assert derive_bounds([]) == {insider: None, price: None}
    # a numeric declaration on an UNBOUNDED table is inert (documents the need, never floors it)
    assert derive_bounds([{"fact_catalyst": 10}]) == {insider: None, price: None}
    # a typo'd table fails loud — never quietly "unbounded"
    with pytest.raises(ValueError, match="unknown fact table"):
        derive_bounds([{"fact_prices": 10}])


def test_every_bounded_table_has_at_least_one_declaring_reader_on_each_pit():
    """The guard is not vacuous: someone on the call PIT declares each bounded table, and the display
    PIT's insider read is unbounded BECAUSE a reader declares None, not because nobody declares it.
    """
    call = call_horizons(DEFAULT_CONFIG)
    for table in BOUNDED_TABLES:
        assert any(table in d and d[table] is not None for d in call), table
    disp = display_horizons()
    assert any(d.get("fact_insider_txn", 0) is None and "fact_insider_txn" in d for d in disp)
    assert any("fact_price_eod" in d and d["fact_price_eod"] is not None for d in disp)


# --- the recording PIT: every reader touches only the tables it declared -------------------------------


class _RecordingPIT:
    """Wraps the REAL point-in-time view and records every ``(table, lookback_days)`` a reader asks for
    (``NO_WINDOW_PARAM`` for an accessor with no lookback parameter). Identity reads
    (``security_name`` / ``security_cik``) are not fact tables and are not recorded."""

    _TABLE = {
        "insider_txns": "fact_insider_txn",
        "dilution_facts": "fact_dilution",
        "catalyst_facts": "fact_catalyst",
        "fundamentals_facts": "fact_fundamentals",
        "corporate_event_facts": "fact_corporate_event",
        "activist_stake_facts": "fact_activist_stake",
        "revenue_mix_facts": "fact_revenue_mix",
        "shares_outstanding_facts": "fact_shares_outstanding",
        "cash_burn_facts": "fact_cash_burn",
        "fund_shares": "fact_fund_shares",
        "theme_conviction_facts": "fact_theme_conviction",
    }

    def __init__(self, inner: PointInTimeData) -> None:
        self._inner = inner
        self.touched: list[tuple[str, int | None]] = []

    @property
    def asof(self):
        return self._inner.asof

    @property
    def known_at(self):
        return self._inner.known_at

    @property
    def tenant_id(self):
        return self._inner.tenant_id

    def price_history(self, security_id, lookback_days=None):
        self.touched.append(("fact_price_eod", lookback_days))
        return self._inner.price_history(security_id, lookback_days)

    def benchmark_prices(self, symbol, lookback_days=None):
        self.touched.append(("fact_price_eod", lookback_days))
        return self._inner.benchmark_prices(symbol, lookback_days)

    def security_name(self, security_id):
        return self._inner.security_name(security_id)

    def security_cik(self, security_id):
        return self._inner.security_cik(security_id)

    def __getattr__(self, name):
        table = self._TABLE.get(name)
        if table is None:
            raise AttributeError(name)

        def accessor(scope_id):
            self.touched.append((table, NO_WINDOW_PARAM))
            return getattr(self._inner, name)(scope_id)

        return accessor


def _check(name: str, decl: Mapping[str, int | None], touched: list[tuple[str, int | None]]):
    tables = {t for t, _ in touched}
    assert tables, f"{name} read nothing — the check would be vacuous"
    assert tables <= set(decl), f"{name} read {tables - set(decl)} without declaring it"
    for table, lookback in touched:
        declared = decl[table]
        if declared is None or lookback == NO_WINDOW_PARAM:
            continue  # unbounded / no window parameter to compare against
        if lookback is not None:
            assert (
                declared >= lookback
            ), f"{name} asked {table} for {lookback} d, declared {declared}"
        else:
            assert (name, table) in JUSTIFIED_BARE_READS, (
                f"{name} reads {table} with no lookback but declares a finite {declared} d horizon — "
                "declare None, or justify the finite horizon and add it to JUSTIFIED_BARE_READS"
            )


def test_every_reader_touches_only_the_tables_it_declared(db):
    seed_all(db)
    asof = date(2026, 6, 5)
    seed_benchmark_bars(db, asof)
    thesis = thesis_repo.get(db, NUCLEAR_THESIS_ID)

    def fresh():
        return _RecordingPIT(PointInTimeData(db, asof=asof, known_at=PIN))

    touched_by_call: set[str] = set()
    for detector in registered_detectors():
        rec = fresh()
        for sid in SECS:
            detector(rec, sid, asof, ALL_ON)
        _check(detector.name, detector.horizons(ALL_ON), rec.touched)
        touched_by_call |= {t for t, _ in rec.touched}

    rec = fresh()
    leader = fired_signal(
        detector="volume_breakout",
        security_id=SMR_ID,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=Grade.CORE,
        score=0.8,
        label="a leader breakout, so the laggard scan actually runs",
        asof=asof,
        provenance=[source_provenance("price", f"price:{SMR_ID}:{asof.isoformat()}")],
        alpha_liveness_days=10,
    )
    laggard.detect(rec, thesis, [leader], asof, ALL_ON)
    _check("laggard", laggard.HORIZONS(ALL_ON), rec.touched)
    touched_by_call |= {t for t, _ in rec.touched}

    rec = fresh()
    theme_conviction.detect_fact(rec, NUCLEAR_THESIS_ID, asof, ALL_ON)
    _check("theme_conviction", theme_conviction.HORIZONS(ALL_ON), rec.touched)
    # the call-side guard covers both bounded tables (not vacuous)
    assert BOUNDED_TABLES <= touched_by_call

    for member in registered_display_members():
        rec = fresh()
        for sid in SECS:
            member(rec, sid, asof)
        _check(member.name, member.horizons, rec.touched)

    rec = fresh()
    theme_breadth.breadth_for(rec, SECS)
    _check("theme_breadth", theme_breadth.HORIZONS, rec.touched)
    rec = fresh()
    relative_strength.sector_rs_for(rec, SECS, {sid: None for sid in SECS})
    _check("sector_rs", relative_strength.SECTOR_RS_HORIZONS, rec.touched)

    rec = fresh()
    for member in thesis.basket:
        scoring.score_member(rec, member, ALL_ON)  # type: ignore[arg-type]
    _check("workbench.scoring", scoring.HORIZONS, rec.touched)
