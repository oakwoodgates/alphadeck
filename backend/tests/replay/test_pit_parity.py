from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from db.bitemporal import _FACT_IDENTITY
from pipeline.seed import (
    HIMS_SECURITY_ID,
    HIMS_THESIS_ID,
    LEU_ID,
    NNE_ID,
    NUCLEAR_THESIS_ID,
    OKLO_ID,
    SMR_ID,
    UNH_SECURITY_ID,
    UNH_THESIS_ID,
    seed_hims,
    seed_leu_catalyst,
    seed_nuclear,
    seed_nuclear_catalyst,
    seed_nuclear_theme_conviction,
    seed_unh,
)
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals.base import PointInTimeData

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _canon(v):
    """Canonicalize a value so the Postgres (Decimal/UUID/datetime/dict) and the DuckDB mirror
    (float/str/datetime/dict) representations compare equal — the call path floats numerics anyway, so
    this normalizer only governs the parity ASSERTION, never widens or drops a key."""
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, default=str)
    return v


def _match(live_rows, replay_rows, identity):
    """Assert the live and replay accessor returns are equal: same identity set, the live row's keys are a
    SUBSET of the replay row's (a dropped column FAILS the gate), and every shared key matches normalized.
    """

    def key(r):
        return tuple(_canon(r[c]) for c in identity)

    live_by = {key(r): r for r in live_rows}
    rep_by = {key(r): r for r in replay_rows}
    assert set(live_by) == set(rep_by), f"identity sets differ: {set(live_by) ^ set(rep_by)}"
    for k, lr in live_by.items():
        rr = rep_by[k]
        assert set(lr) <= set(rr), f"replay dropped columns: {set(lr) - set(rr)}"
        for col in lr:
            assert _canon(lr[col]) == _canon(
                rr[col]
            ), f"{identity}={k} col {col}: {lr[col]!r} != {rr[col]!r}"


def _seed_all(db):
    seed_hims(db)  # insider (Wells) + prices + dilution (converts)
    seed_unh(db)  # insider cluster + prices
    seed_nuclear(db)  # prices for the basket
    seed_nuclear_catalyst(db)  # OKLO catalyst
    seed_leu_catalyst(db)  # LEU catalyst
    seed_nuclear_theme_conviction(db)  # the nuclear theme conviction (thesis-scoped)
    db.commit()


def test_replay_pit_matches_postgres_as_of(db, tmp_path):
    """THE TRUST GATE. Across all five fact tables, every seeded scope, and a sweep of as-ofs, the
    DuckDB/Parquet mirror's accessor returns equal the live Postgres ``PointInTimeData`` accessor returns
    (after the value normalizer). This is what lets DuckDB do the fast sweeps while provably reproducing
    the SoR's as-of result the detectors consume."""
    _seed_all(db)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        secs = [HIMS_SECURITY_ID, UNH_SECURITY_ID, SMR_ID, OKLO_ID, NNE_ID, LEU_ID]
        theses = [HIMS_THESIS_ID, UNH_THESIS_ID, NUCLEAR_THESIS_ID]
        for asof in (date(2025, 8, 15), date(2026, 4, 1), date(2026, 6, 5)):
            live = PointInTimeData(db, asof=asof, known_at=_PIN)
            rep = ReplayPointInTimeData(con, asof=asof, known_at=_PIN)
            for sid in secs:
                _match(
                    live.insider_txns(sid),
                    rep.insider_txns(sid),
                    _FACT_IDENTITY["fact_insider_txn"],
                )
                _match(
                    live.price_history(sid),
                    rep.price_history(sid),
                    _FACT_IDENTITY["fact_price_eod"],
                )
                _match(
                    live.price_history(sid, 120),
                    rep.price_history(sid, 120),
                    _FACT_IDENTITY["fact_price_eod"],
                )
                _match(
                    live.dilution_facts(sid),
                    rep.dilution_facts(sid),
                    _FACT_IDENTITY["fact_dilution"],
                )
                _match(
                    live.catalyst_facts(sid),
                    rep.catalyst_facts(sid),
                    _FACT_IDENTITY["fact_catalyst"],
                )
            for tid in theses:
                _match(
                    live.theme_conviction_facts(tid),
                    rep.theme_conviction_facts(tid),
                    _FACT_IDENTITY["fact_theme_conviction"],
                )
    finally:
        con.close()
