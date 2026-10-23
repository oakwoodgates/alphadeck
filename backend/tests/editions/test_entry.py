"""S1 — join dates. The rule (operator, 2026-10-19/20): exact from a basket snapshot; for the 0043 deploy-seed
members, the night we first pulled the company's data, never before the thesis existed — with the own-batch
refinement for a company pulled earlier for another thesis, the card proof, and the seed cap. And every
input is read as known at the watermark, so the join dates an edition is cut with cannot see the future.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from editions.entry import (
    Snapshot,
    ThesisRoster,
    derive_all,
    estimate_entries,
    load_first_cards,
    load_first_pulls,
    load_rosters,
)
from tests.scoreboard.helpers import persist_thesis

NY = ZoneInfo("America/New_York")
T = uuid.UUID(int=0x7001)
A, B, C, D = (uuid.UUID(int=0xA000 + i) for i in range(4))


def _at(d: date, hh: int = 22, mm: int = 45) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=NY)


def _members(*sids) -> tuple[dict, ...]:
    return tuple({"ordinal": i, "security_id": str(s)} for i, s in enumerate(sids))


def _roster(*, created: date, snapshots=(), seed_index: int | None = 0, live=()) -> ThesisRoster:
    snaps = tuple(snapshots)
    return ThesisRoster(
        thesis_id=T,
        tenant_id=DEFAULT_TENANT_ID,
        created_at=_at(created, 12, 0),
        archived_at=None,
        snapshots=snaps,
        live_security_ids=frozenset(live),
        seed=snaps[seed_index] if (snaps and seed_index is not None) else None,
    )


SEED = Snapshot(_at(date(2026, 9, 15), 8, 36), _members(A, B, C))


def test_a_company_added_by_a_later_snapshot_joins_EXACTLY_when_it_was_taken():
    later = Snapshot(_at(date(2026, 9, 29), 14, 0), _members(A, B, C, D))
    es = estimate_entries(
        _roster(created=date(2026, 6, 1), snapshots=[SEED, later]),
        first_pulls={A: _at(date(2026, 6, 2))},
        first_cards={},
    )
    assert es[D].entry_date == date(2026, 9, 29)
    assert es[D].estimated is False and es[D].method == "snapshot"
    assert es[A].estimated is True  # a seed member is always an estimate


def test_option_A_first_pull_never_before_creation():
    es = estimate_entries(
        _roster(created=date(2026, 6, 1), snapshots=[SEED]),
        first_pulls={A: _at(date(2026, 6, 10)), B: _at(date(2026, 6, 20))},
        first_cards={},
    )
    assert (es[A].entry_date, es[A].method) == (date(2026, 6, 10), "first_pull")
    assert (es[B].entry_date, es[B].method) == (date(2026, 6, 20), "first_pull")
    assert (es[C].entry_date, es[C].method) == (date(2026, 6, 1), "no_pull")


def test_a_company_pulled_for_ANOTHER_thesis_takes_this_thesis_own_batch_date():
    """Decision 4: the clamp to creation would put it weeks before this thesis's own companies arrived."""
    es = estimate_entries(
        _roster(created=date(2026, 8, 22), snapshots=[SEED]),
        first_pulls={
            A: _at(date(2026, 8, 6)),  # pulled for an older thesis
            B: _at(date(2026, 9, 1)),  # this thesis's own companies arrive 09-01
            C: _at(date(2026, 9, 3)),
        },
        first_cards={},
    )
    assert (es[A].entry_date, es[A].method) == (date(2026, 9, 1), "own_batch")
    assert es[A].evidence["own_batch"] == "2026-09-01"


def test_all_shared_falls_back_to_thesis_creation():
    es = estimate_entries(
        _roster(created=date(2026, 9, 2), snapshots=[SEED]),
        first_pulls={s: _at(date(2026, 8, 1)) for s in (A, B, C)},
        first_cards={},
    )
    assert {e.method for e in es.values()} == {"thesis_created"}
    assert {e.entry_date for e in es.values()} == {date(2026, 9, 2)}


def test_a_recorded_card_proves_presence_EARLIER():
    es = estimate_entries(
        _roster(created=date(2026, 8, 22), snapshots=[SEED]),
        first_pulls={A: _at(date(2026, 8, 6)), B: _at(date(2026, 9, 1))},
        first_cards={A: date(2026, 8, 25)},
    )
    assert (es[A].entry_date, es[A].method) == (date(2026, 8, 25), "own_batch+card")


def test_a_card_never_pushes_a_join_date_LATER():
    es = estimate_entries(
        _roster(created=date(2026, 6, 1), snapshots=[SEED]),
        first_pulls={A: _at(date(2026, 6, 10))},
        first_cards={A: date(2026, 7, 1)},
    )
    assert es[A].entry_date == date(2026, 6, 10)


def test_the_seed_caps_an_estimate_that_would_land_after_it():
    es = estimate_entries(
        _roster(created=date(2026, 6, 1), snapshots=[SEED]),
        first_pulls={A: _at(date(2026, 10, 1))},  # data first pulled after the seed proved presence
        first_cards={},
    )
    assert es[A].entry_date == date(2026, 9, 15)
    assert es[A].method.endswith("+seed_cap")


def test_no_snapshot_at_all_estimates_the_live_basket():
    es = estimate_entries(
        _roster(created=date(2026, 6, 1), snapshots=(), live=(A,)),
        first_pulls={A: _at(date(2026, 6, 3))},
        first_cards={},
    )
    assert es[A].entry_date == date(2026, 6, 3) and es[A].estimated


def test_a_thesis_created_after_the_snapshot_table_is_all_exact():
    first = Snapshot(_at(date(2026, 10, 1), 15, 0), _members(A, B))
    es = estimate_entries(
        _roster(created=date(2026, 10, 1), snapshots=[first], seed_index=None),
        first_pulls={A: _at(date(2026, 8, 1))},
        first_cards={},
    )
    assert {e.method for e in es.values()} == {"snapshot"}
    assert {e.entry_date for e in es.values()} == {date(2026, 10, 1)}


# --- the reads honor the watermark (no lookahead, #1) -------------------------------------------------


def _bar(db, sid, d: date, recorded_at: datetime) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "close": 10.0,
            "valid_from": d,
            "recorded_at": recorded_at,
        },
    )


def test_first_pull_ignores_rows_recorded_after_the_watermark_and_backdated_backfill(
    db, security_id
):
    kb = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _bar(db, security_id, date(2024, 1, 2), datetime(2024, 1, 2, tzinfo=timezone.utc))  # backdated
    _bar(db, security_id, date(2026, 6, 10), datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc))
    _bar(db, security_id, date(2026, 5, 1), datetime(2026, 8, 1, tzinfo=timezone.utc))  # after kb
    db.commit()
    pulls = load_first_pulls(db, known_by=kb)
    assert pulls[(DEFAULT_TENANT_ID, security_id)] == datetime(
        2026, 6, 12, 2, 0, tzinfo=timezone.utc
    )
    # before the watermark reaches the real pull, the company has no pull at all
    assert (DEFAULT_TENANT_ID, security_id) not in load_first_pulls(
        db, known_by=datetime(2026, 6, 1, tzinfo=timezone.utc)
    )


def test_a_snapshot_taken_after_the_watermark_is_never_read(db, security_id):
    thesis = persist_thesis(db, security_id)  # upsert writes a snapshot at now()
    rosters = load_rosters(db, known_by=datetime.now(timezone.utc) - timedelta(days=1), cutoff=None)
    assert thesis.id not in {r.thesis_id for r in rosters}  # the thesis itself did not exist yet
    rosters = load_rosters(db, known_by=datetime.now(timezone.utc), cutoff=None)
    (mine,) = [r for r in rosters if r.thesis_id == thesis.id]
    assert len(mine.snapshots) == 1 and mine.seed is None


def _card_row(db, thesis_id, asof: date, sid, *, recorded_at: datetime, reconstructed=False):
    card = {
        "thesis_id": str(thesis_id),
        "asof": asof.isoformat(),
        "watch_members": [{"security_id": str(sid)}],
    }
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, reconstructed, recorded_at) "
            "VALUES (%s, %s, %s, 'warming', 'watching', %s, %s, %s)",
            (DEFAULT_TENANT_ID, thesis_id, asof, Json(card), reconstructed, recorded_at),
        )
    db.commit()


def test_first_cards_are_honest_rows_known_by_the_watermark(db, security_id):
    thesis = persist_thesis(db, security_id)
    _card_row(
        db,
        thesis.id,
        date(2026, 7, 1),
        security_id,
        recorded_at=_at(date(2026, 7, 1)),
        reconstructed=True,
    )
    _card_row(db, thesis.id, date(2026, 7, 6), security_id, recorded_at=_at(date(2026, 7, 6)))
    _card_row(
        db, thesis.id, date(2026, 7, 2), security_id, recorded_at=_at(date(2026, 9, 1))
    )  # late
    cards = load_first_cards(db, known_by=_at(date(2026, 8, 1)))
    assert cards[(thesis.id, security_id)] == date(2026, 7, 6)


def test_derive_all_runs_end_to_end(db, security_id):
    thesis = persist_thesis(db, security_id)
    _bar(db, security_id, date(2026, 6, 10), datetime.now(timezone.utc))
    db.commit()
    _, entries = derive_all(db, known_by=datetime.now(timezone.utc))
    e = entries[thesis.id][security_id]
    # created after the snapshot table existed -> exact, from the upsert's own snapshot
    assert e.estimated is False and e.method == "snapshot"
