"""Editions S6 — the two read shortcuts the "what changed" comparison takes are EXACT, not approximations:

- ``MemoPrices`` loads each company's tape once and answers every scorer method from it: the same answers as
  ``PgRealizedPrices`` (the Scoreboard's own reader) on a tape with a re-versioned bar, a null close and a
  bar past the cap.
- ``SNAPSHOT_UNREAD_CARD_KEYS`` projects the bulky card keys away in SQL: the rows chosen are the same and the
  snapshot timeline built from them is identical, for the record (Edition 0, its honest read) and for an
  edition's pages — while the full read still carries those keys (the Scoreboard's own path is unchanged).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from replay.scoring import score_episode
from repositories import editions_repo
from scoreboard.diff import MemoPrices
from scoreboard.prices import PgRealizedPrices
from scoreboard.record import SNAPSHOT_UNREAD_CARD_KEYS, EditionSource, RecordSource
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

CUT = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)


def _security(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, 'DRD', %s, '2026-01-01')",
            (sid, DEFAULT_TENANT_ID, f"{sid.int % 10**10:010d}"),
        )
    db.commit()
    return sid


def test_memo_prices_answer_exactly_like_the_scoreboards_reader(db):
    sid = _security(db)
    for d, c in ((date(2026, 7, 6), 10.0), (date(2026, 7, 7), 11.0), (date(2026, 7, 9), 12.0)):
        bar(db, sid, d, c, high=c + 1, low=c - 1)
    bar(db, sid, date(2026, 7, 8), None)  # a null close: skipped by both
    # a re-versioned bar, appended after the first version: the later version wins in both
    bar(db, sid, date(2026, 7, 7), 11.5)
    bar(db, sid, date(2026, 7, 14), 13.0)  # past the cap: invisible to both
    cap = date(2026, 7, 10)
    plain = PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap)
    memo = MemoPrices(PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap))
    days = [date(2026, 7, n) for n in range(4, 13)]
    for x in days:
        assert memo.first_close_on_or_after(sid, x) == plain.first_close_on_or_after(sid, x)
        assert memo.last_close_through(sid, x) == plain.last_close_through(sid, x)
        assert memo.tape_edge(sid, x) == plain.tape_edge(sid, x)
        for y in days:
            assert memo.closes_between(sid, x, y) == plain.closes_between(sid, x, y)
            assert memo.bars_between(sid, x, y) == plain.bars_between(sid, x, y)
    assert memo.market_tape_edge(sid) == plain.market_tape_edge(sid)
    assert memo.closes_between(sid, days[0], days[-1]) == [
        (date(2026, 7, 6), 10.0),
        (date(2026, 7, 7), 11.5),
        (date(2026, 7, 9), 12.0),
    ]
    unknown = uuid.uuid4()  # no tape at all
    assert memo.first_close_on_or_after(unknown, days[0]) is None
    assert (
        memo.tape_edge(unknown, days[0]) is None and memo.closes_between(unknown, *days[:2]) == []
    )


def test_a_scored_episode_is_identical_through_either_reader(db):
    sid = _security(db)
    t = persist_thesis(db, sid, thesis_id=uuid.uuid4())
    conv, conf = keys_fired(sid, date(2026, 7, 6), conv_liveness=5, conf_liveness=5)
    for d in (date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 8)):
        record_day(db, t, [conv, conf], d)
    for n, c in enumerate((10.0, 10.4, 9.8, 10.9, 11.2)):
        bar(db, sid, date(2026, 7, 6 + n), c, high=c + 0.5, low=c - 0.5)
    from replay.episodes import derive_episodes

    snaps = RecordSource().read(db, t.id, date(2026, 7, 10)).snaps
    [ep] = derive_episodes(snaps)
    cap = date(2026, 7, 10)
    plain = score_episode(ep, PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap))
    memo = score_episode(ep, MemoPrices(PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap)))
    assert memo == plain and plain.forward_return is not None


def test_the_lean_card_read_builds_the_same_timeline_for_the_record_and_an_edition(db):
    sid = _security(db)
    t = persist_thesis(db, sid, thesis_id=uuid.uuid4())
    conv, conf = keys_fired(sid, date(2026, 7, 9), conv_liveness=120, conf_liveness=120)
    warm = insider_event(security_id=sid, liveness=120).model_copy(
        update={"asof": date(2026, 7, 8)}
    )
    days = [
        (date(2026, 7, 8), [warm]),
        (date(2026, 7, 9), [conv, conf]),
        (date(2026, 7, 10), [conv, conf]),
    ]
    for d, ev in days:
        record_day(db, t, ev, d)
    record_day(db, t, [warm], date(2026, 7, 10), reconstructed=True)  # set aside either way
    editions_repo.publish_edition(
        db,
        tenant_id=t.tenant_id,
        number=1,
        label="first re-run",
        causes=["initial"],
        engine={"fingerprint": "f" * 64, "config_hash": "c" * 64, "code_sha": None},
        data_known_by=CUT,
        pages=[assemble_call(t, ev, d, DEFAULT_CONFIG) for d, ev in days],
        entries=[],
        computed_at=CUT,
    )
    db.commit()
    edition = editions_repo.get_by_number(db, tenant_id=t.tenant_id, number=1)
    upto = date(2026, 7, 13)
    for src in (RecordSource(), EditionSource(edition)):
        full = src.read(db, t.id, upto)
        lean = src.read(db, t.id, upto, drop_card_keys=SNAPSHOT_UNREAD_CARD_KEYS)
        assert lean.snaps == full.snaps and len(full.snaps) == 3
        # the projection really drops the keys (the full read still carries them: the Scoreboard needs them)
        armed_full = full.cards_by_asof[date(2026, 7, 9)]
        armed_lean = lean.cards_by_asof[date(2026, 7, 9)]
        assert armed_full.triggers_fired and armed_full.counter_case
        assert armed_lean.triggers_fired == [] and armed_lean.counter_case == ""
        assert armed_lean.risk_signals == []
