"""CARD-level and DISPLAY-level equality: the prefetching, bounded point-in-time view produces the
byte-identical CallCard (``calls_repo._canonical``) and display body as the plain per-security view
(Board/Cockpit perf PR-1b, proof obligations 2 + 3).

Every case runs at ``known_at = PIN`` (sees every version) AND at ``MID`` — pinned between two
recorded versions of a HIMS bar and the Wells Form 4 (``tests/pit_fixtures.py``). The as-ofs include
the ones where a cluster is LIVE (HIMS 2026-06-01, UNH's May-2025 cluster + the August arm), so the
compare is over real, opinionated cards, not empties. DB-backed (skips without Postgres).
"""

from __future__ import annotations

import json
from datetime import date

import pytest

import signals.base as sb
from domain.config import DEFAULT_CONFIG
from domain.enums import State
from pipeline.call_for_thesis import call_for_thesis
from pipeline.core import assemble_from_pit
from pipeline.seed import HIMS_THESIS_ID, NNE_ID, NUCLEAR_THESIS_ID, UNH_THESIS_ID
from repositories import calls_repo, decisions_repo, thesis_repo
from signals.base import PointInTimeData
from signals.display import (
    insider_flow,
    registered_display_members,
    relative_strength,
    theme_breadth,
)
from signals.horizons import call_bounds, display_bounds
from tests.pit_fixtures import (
    MID,
    OLDCO_ID,
    PIN,
    SECS,
    add_mid_versions,
    seed_all,
    seed_benchmark_bars,
    seed_oldco,
)

# thesis -> the as-ofs to compare at (each arc's WARMING / ARMED / aged-out points)
CASES = [
    (HIMS_THESIS_ID, [date(2026, 5, 28), date(2026, 6, 1)]),
    (UNH_THESIS_ID, [date(2025, 5, 20), date(2025, 8, 18), date(2026, 6, 1)]),
    (NUCLEAR_THESIS_ID, [date(2026, 6, 5)]),
]
_KNOWN_ATS = pytest.mark.parametrize("known_at", [PIN, MID], ids=["pin-sees-all", "mid-version"])


def _plain_card(db, tid, asof, known_at):
    """The reference card: the SAME funnel steps as ``call_for_thesis`` but over a PIT with NO basket and
    NO bounds — the per-security read path, memo only."""
    thesis = thesis_repo.get(db, tid)
    thesis.position = decisions_repo.effective_position(db, thesis, asof=asof, known_at=known_at)
    pit = PointInTimeData(db, asof=asof, known_at=known_at, tenant_id=thesis.tenant_id)
    return assemble_from_pit(pit, thesis, asof, DEFAULT_CONFIG)


@_KNOWN_ATS
def test_call_cards_are_canonical_identical_with_prefetch_and_bounds(db, known_at):
    seed_all(db)
    add_mid_versions(db)
    for tid, asofs in CASES:
        for asof in asofs:
            fast = call_for_thesis(db, tid, asof, known_at=known_at, record=False)
            slow = _plain_card(db, tid, asof, known_at)
            assert calls_repo._canonical(fast) == calls_repo._canonical(slow), (tid, asof, known_at)
    # the seed arcs still hold through the funnel — the compare above was over real cards
    hims = call_for_thesis(db, HIMS_THESIS_ID, date(2026, 6, 1), known_at=known_at, record=False)
    assert hims.state is State.ARMED
    unh_may = call_for_thesis(db, UNH_THESIS_ID, date(2025, 5, 20), known_at=known_at, record=False)
    unh_aug = call_for_thesis(db, UNH_THESIS_ID, date(2025, 8, 18), known_at=known_at, record=False)
    assert unh_may.state is State.WARMING and unh_aug.state is State.ARMED


def test_the_funnel_threads_the_basket_into_the_pit(db, monkeypatch):
    """``call_for_thesis`` builds the PIT with the resolved basket + the derived call bounds: one batch
    query per fact table the detectors touch, ZERO per-security reads for a 4-name thesis."""
    seed_all(db)
    calls = {"many": 0, "one": 0}
    real_many, real_one = sb.as_of_many, sb.as_of

    def many(*a, **k):
        calls["many"] += 1
        return real_many(*a, **k)

    def one(*a, **k):
        calls["one"] += 1
        return real_one(*a, **k)

    monkeypatch.setattr(sb, "as_of_many", many)
    monkeypatch.setattr(sb, "as_of", one)
    call_for_thesis(db, NUCLEAR_THESIS_ID, date(2026, 6, 5), known_at=PIN, record=False)
    # insider, price, dilution, catalyst, fundamentals, corporate_event, activist_stake
    assert calls == {"many": 7, "one": 0}


def _display_body(pit, sids, asof) -> str:
    """The display route's body (members + theme breadth + the supersector RS rollup), canonical JSON."""
    members = {
        str(sid): [
            sig.model_dump(mode="json")
            for member in registered_display_members()
            if (sig := member(pit, sid, asof)) is not None
        ]
        for sid in sids
    }
    breadth = theme_breadth.breadth_for(pit, sids)
    rs = relative_strength.sector_rs_for(pit, sids, {sid: None for sid in sids})
    return json.dumps(
        {
            "members": members,
            "breadth": breadth.model_dump(mode="json") if breadth else None,
            "sector_rs": rs.model_dump(mode="json") if rs else None,
        },
        sort_keys=True,
        default=str,
    )


@_KNOWN_ATS
def test_display_body_is_identical_with_prefetch_and_bounds(db, known_at):
    """The registered members + ``theme_breadth`` + ``sector_rs_for`` over the seed names, a benchmark
    tape (the out-of-basket fallback), a name with NO Form 4 (NNE -> ``insider_flow_90d`` absent, the
    panel's '—') and a name whose ONLY Form 4 is older than any bound (OLDCO -> present, ``0/0``): the
    prefetching display PIT renders the byte-identical body, and both insider shapes survive."""
    seed_all(db)
    add_mid_versions(db)
    asof = date(2026, 6, 1)
    seed_benchmark_bars(db, asof)
    seed_oldco(db, asof)
    sids = SECS + [OLDCO_ID]
    fast = PointInTimeData(db, asof=asof, known_at=known_at, basket=sids, bounds=display_bounds())
    slow = PointInTimeData(db, asof=asof, known_at=known_at)
    body_fast, body_slow = _display_body(fast, sids, asof), _display_body(slow, sids, asof)
    assert body_fast == body_slow
    parsed = json.loads(body_fast)

    def kinds(sid):
        return {s["kind"] for s in parsed["members"][str(sid)]}

    assert "insider_flow_90d" not in kinds(NNE_ID)  # nothing ingested -> honestly absent
    assert "insider_flow_90d" in kinds(OLDCO_ID)  # rows on file, none in the window -> 0/0
    flow = next(s for s in parsed["members"][str(OLDCO_ID)] if s["kind"] == "insider_flow_90d")
    by_key = {m["key"]: m["value"] for m in flow["metrics"]}
    assert by_key["buy_count"] == 0.0 and by_key["sell_count"] == 0.0
    assert "relative_strength" in kinds(OLDCO_ID)  # the benchmark fallback path was exercised
    assert parsed["breadth"] is not None and parsed["sector_rs"] is not None


def test_why_the_display_pit_declares_the_insider_read_unbounded(db):
    """The negative that motivates ``insider_flow_90d``'s ``None``: under the CALL PIT's derived insider
    bound (240 d) the old-only name's rows fall below the floor and its ``0/0`` collapses into '—'.
    The display bounds keep the read unbounded, so the two shapes stay distinct."""
    seed_all(db)
    asof = date(2026, 6, 1)
    seed_oldco(db, asof)
    bounded = PointInTimeData(
        db, asof=asof, known_at=PIN, basket=[OLDCO_ID], bounds=call_bounds(DEFAULT_CONFIG)
    )
    assert bounded.insider_txns(OLDCO_ID) == []
    assert insider_flow.display(bounded, OLDCO_ID, asof) is None
    display_pit = PointInTimeData(
        db, asof=asof, known_at=PIN, basket=[OLDCO_ID], bounds=display_bounds()
    )
    assert len(display_pit.insider_txns(OLDCO_ID)) == 1
    assert insider_flow.display(display_pit, OLDCO_ID, asof) is not None
