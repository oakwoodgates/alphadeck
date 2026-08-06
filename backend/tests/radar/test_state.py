"""Golden tests for the SPAC deal-state machine (radar/state.py) — the Rev 2 honesty rules:
announcement and termination are a PAIR (a dead deal never reads live), an unclassified 8-K
contributes NOTHING to state, and completion is terminal."""

from __future__ import annotations

from datetime import date

from radar.state import StateEvent, deal_state


def ev(d: str, form: str, items: list[str] | None = None, acc: str = "") -> StateEvent:
    return StateEvent(
        filed=date.fromisoformat(d),
        form=form,
        items=tuple(items) if items else None,
        accession=acc or f"{d}-{form}",
    )


def test_no_deal_markers_reads_searching():
    # an extension proxy and an items-UNKNOWN 8-K say nothing about a deal
    assert deal_state([ev("2026-07-01", "DEF 14A"), ev("2026-07-10", "8-K", None)]) == "searching"


def test_announce_markers():
    assert deal_state([ev("2026-07-01", "425")]) == "announced"
    assert deal_state([ev("2026-07-01", "8-K", ["1.01", "9.01"])]) == "announced"
    assert deal_state([ev("2026-07-01", "DEFM14A")]) == "announced"


def test_the_rev2_pair_announce_then_terminate_reads_terminated():
    events = [ev("2026-07-01", "8-K", ["1.01"]), ev("2026-07-20", "8-K", ["1.02"])]
    assert deal_state(events) == "terminated"


def test_terminate_without_prior_announce_stays_searching():
    # a 1.02 on some unrelated agreement while hunting — conservative, no deal claimed
    assert deal_state([ev("2026-07-01", "8-K", ["1.02"])]) == "searching"


def test_completion_is_terminal():
    events = [
        ev("2026-06-01", "425"),
        ev("2026-07-01", "8-K", ["2.01", "5.03"]),
        ev("2026-07-15", "425"),  # post-close comms churn never re-announces
        ev("2026-07-20", "8-K", ["1.01"]),  # ordinary-corporate agreement post-close
    ]
    assert deal_state(events) == "completed"


def test_reannounce_after_termination_is_deal_two():
    events = [
        ev("2026-05-01", "8-K", ["1.01"]),
        ev("2026-06-01", "8-K", ["1.02"]),
        ev("2026-07-01", "425"),
    ]
    assert deal_state(events) == "announced"


def test_unknown_items_between_markers_change_nothing():
    events = [ev("2026-07-01", "425"), ev("2026-07-05", "8-K", None)]
    assert deal_state(events) == "announced"


def test_order_independent_fold():
    events = [
        ev("2026-07-20", "8-K", ["1.02"], acc="b"),
        ev("2026-07-01", "8-K", ["1.01"], acc="a"),
    ]
    assert deal_state(events) == deal_state(list(reversed(events))) == "terminated"
