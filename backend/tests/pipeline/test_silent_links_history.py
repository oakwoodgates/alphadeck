"""Context and Bench through the DATABASE: history on the public clock, and the nightly record.

The seam test (``test_silent_links_seam.py``) proves the filter on an in-memory view. These prove the parts only
Postgres can: that a past night is judged on the placement the operator had made BY THEN (the roster snapshot's
``segment``, read at the view's ``known_at``), so moving a company into Context never rewrites an earlier call
and never leaks backward (#1); and that the nightly call-of-record stays idempotent with silent names on the
roster, counting the ``calls`` table rather than the read.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg.types.json import Json

from db.session import DEFAULT_TENANT_ID
from domain.enums import State
from domain.market_time import known_at_for_asof
from domain.thesis import BasketMember, Segment, Thesis
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from pipeline import daily
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo, thesis_repo
from tests.form4a_fixtures import as_issuer

_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_WELLS_ACCESSION = "0001773751-26-000086"
_WELLS_ACCEPTED = datetime(
    2026, 5, 28, 21, 0, tzinfo=timezone.utc
)  # 17:00 ET, two sessions after the 05-26 buy (the Form 4 deadline)

# HIMS (the seed) arms on 2026-06-01 and stays armed through its 10-day entry window, so X is a name whose
# facts visibly change the card whenever it votes.
_ARMED_DAY = date(2026, 6, 1)
_LATER_DAY = date(2026, 6, 4)  # the last bar in the seed tape
_S1 = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)  # X placed in the chain
_S2 = datetime(2026, 6, 3, 12, tzinfo=timezone.utc)  # X moved to Context
_AFTER_S2 = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _seed_x(db, security_id) -> None:
    """The real HIMS facts onto the generic security: a senior open-market buy + the price tape."""
    as_issuer(db, security_id, "0001773751")
    ingest_form4(
        db,
        security_id,
        (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
        accepted=_WELLS_ACCEPTED,  # its PUBLIC instant: a history view counts the buy from here, not from ingest
    )
    ingest_prices(
        db,
        security_id,
        parse_yahoo_chart(
            json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
        ),
    )
    db.commit()


def _quiet_security(db) -> uuid.UUID:
    """A second company with no facts at all: a voting name that never moves the card."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "QUIET", "0007654321", "2026-01-01"),
        )
    db.commit()
    return sid


def _member(ticker: str, sid: uuid.UUID, segment: str | None) -> BasketMember:
    return BasketMember(ticker=ticker, role="r", security_id=sid, segment=segment)


def _snapshot_json(ordinal: int, m: BasketMember) -> dict:
    return thesis_repo._member_to_snapshot(ordinal, m)


def _thesis_with_history(
    db, name: str, history: list[tuple[datetime, list[BasketMember]]]
) -> uuid.UUID:
    """Persist a thesis, then REPLACE its roster history with exactly the given snapshots (the repo stamps
    one at ``now()`` on upsert, which would sit after every instant this test cares about)."""
    final = history[-1][1]
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="n",
        segments=[Segment(label="Chain")],
        basket=final,
    )
    thesis_repo.upsert(db, t)
    with db.cursor() as cur:
        cur.execute("DELETE FROM basket_snapshot WHERE thesis_id = %s", (t.id,))
        for taken_at, roster in history:
            members = [_snapshot_json(i, m) for i, m in enumerate(roster)]
            cur.execute(
                "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
                "VALUES (%s, %s, %s, %s, %s, md5(%s::text))",
                (uuid.uuid4(), DEFAULT_TENANT_ID, t.id, taken_at, Json(members), Json(members)),
            )
    db.commit()
    return t.id


def _public(db, tid, asof, known_at):
    return call_for_thesis(db, tid, asof, known_at=known_at, clock="public", record=False)


def _shape(card):
    """A card without its thesis id, so cards of two different theses can be compared."""
    return card.model_dump(exclude={"thesis_id"})


def test_a_past_night_is_judged_on_the_placement_the_operator_had_made_by_then(db, security_id):
    _seed_x(db, security_id)
    y = _quiet_security(db)
    chain_then_context = _thesis_with_history(
        db,
        "X moves to Context",
        [
            (_S1, [_member("DEVCO", security_id, "Chain"), _member("QUIET", y, "Chain")]),
            (_S2, [_member("DEVCO", security_id, "Context"), _member("QUIET", y, "Chain")]),
        ],
    )
    always_chain = _thesis_with_history(
        db,
        "X stays in the chain (control)",
        [(_S1, [_member("DEVCO", security_id, "Chain"), _member("QUIET", y, "Chain")])],
    )
    never_x = _thesis_with_history(
        db, "X never there (control)", [(_S1, [_member("QUIET", y, "Chain")])]
    )

    # known_at BETWEEN the two snapshots: roster S1 -> X votes, and its breakout + insider buy arm the card
    before = _public(db, chain_then_context, _ARMED_DAY, known_at_for_asof(_ARMED_DAY))
    assert known_at_for_asof(_ARMED_DAY) < _S2
    assert before.state is State.ARMED and before.armed_security_id == security_id
    assert _shape(before) == _shape(
        _public(db, always_chain, _ARMED_DAY, known_at_for_asof(_ARMED_DAY))
    )

    # known_at AFTER the second snapshot (same as-of): roster S2 -> X is in Context, the facts are unchanged,
    # and the card is the card for a thesis that never had X
    after = _public(db, chain_then_context, _ARMED_DAY, _AFTER_S2)
    assert after.state is not State.ARMED and after.armed_security_id is None
    assert security_id not in {m.security_id for m in (*after.armed_members, *after.watch_members)}
    assert not after.triggers_fired
    assert _shape(after) == _shape(_public(db, never_x, _ARMED_DAY, _AFTER_S2))


def test_a_later_night_after_the_move_is_silent_and_an_earlier_one_is_not_rewritten(
    db, security_id
):
    """The scrub-back the Board does: each as-of reads the roster at the end of THAT market day. The night after
    the move (06-04) no longer counts X, while the control that never moved still arms on the very same facts;
    and the night before the move (06-01) is exactly what it was."""
    _seed_x(db, security_id)
    y = _quiet_security(db)
    moved = _thesis_with_history(
        db,
        "X moves to Context",
        [
            (_S1, [_member("DEVCO", security_id, "Chain"), _member("QUIET", y, "Chain")]),
            (_S2, [_member("DEVCO", security_id, "Context"), _member("QUIET", y, "Chain")]),
        ],
    )
    control = _thesis_with_history(
        db,
        "X never moves (control)",
        [(_S1, [_member("DEVCO", security_id, "Chain"), _member("QUIET", y, "Chain")])],
    )

    later = known_at_for_asof(_LATER_DAY)
    assert later > _S2
    assert (
        _public(db, control, _LATER_DAY, later).state is State.ARMED
    )  # the facts DO arm that night
    silent = _public(db, moved, _LATER_DAY, later)
    assert (
        silent.state is not State.ARMED and not silent.armed_members and not silent.triggers_fired
    )

    earlier = _public(db, moved, _ARMED_DAY, known_at_for_asof(_ARMED_DAY))
    assert earlier.state is State.ARMED and earlier.armed_security_id == security_id


def test_history_is_judged_on_the_snapshot_not_the_live_roster(db, security_id):
    """The live ``basket_member`` rows say X is in Context, but a night BEFORE the move reads the snapshot in
    force then (X in the chain) — the live roster is only the pre-snapshot fallback."""
    _seed_x(db, security_id)
    y = _quiet_security(db)
    tid = _thesis_with_history(
        db,
        "live says Context, history says Chain",
        [
            (_S1, [_member("DEVCO", security_id, "Chain"), _member("QUIET", y, "Chain")]),
            (_S2, [_member("DEVCO", security_id, "Context"), _member("QUIET", y, "Chain")]),
        ],
    )
    with db.cursor() as cur:
        cur.execute(
            "SELECT segment FROM basket_member WHERE thesis_id = %s AND ticker = 'DEVCO'", (tid,)
        )
        assert cur.fetchone()["segment"] == "Context"  # the live row
    card = _public(db, tid, _ARMED_DAY, known_at_for_asof(_ARMED_DAY))
    assert card.state is State.ARMED and card.armed_security_id == security_id


# --- the nightly call-of-record --------------------------------------------------------------------------

_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)
_NIGHT = date(
    2026, 6, 10
)  # within the sample Form 4's (2026-06-01) flip liveness: a voting X would WARM


class _FakeLock:
    def release(self):
        pass


def _calls(db, tid):
    return calls_repo.list_for_thesis(db, tid)  # the full append-only history, every row


def _roster(db, tid, members: list[BasketMember]) -> None:
    """Re-save the thesis with this roster (what a promote does: full-replace + one snapshot if it changed)."""
    t = thesis_repo.get(db, tid)
    thesis_repo.upsert(db, t.model_copy(update={"basket": members}))
    db.commit()


def test_the_nightly_record_is_idempotent_with_silent_names_on_the_roster(
    db, security_id, monkeypatch
):
    """Count the ``calls`` table, not the read. A thesis whose only fact-bearing name is in Context records
    ONE row on the first pass (an Incubating card: X's fresh Form 4 does not warm it) and ZERO on an
    identical re-run; moving X into the chain changes the call and appends exactly one more row; moving it
    back changes it again — one more — and a further identical re-run appends nothing."""
    monkeypatch.setattr(daily, "acquire_pass_lock", lambda **kw: _FakeLock())
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])
    y = _quiet_security(db)
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")  # a senior open-market buy, 06-01
    db.commit()
    tid = _thesis_with_history(
        db,
        "Nightly with a silent name",
        [(_S1, [_member("DEVCO", security_id, "Context"), _member("QUIET", y, "Chain")])],
    )
    in_chain = [_member("DEVCO", security_id, "Chain"), _member("QUIET", y, "Chain")]
    in_context = [_member("DEVCO", security_id, "Context"), _member("QUIET", y, "Chain")]

    daily.run_daily(db, asof=_NIGHT, allow_live=True)
    assert len(_calls(db, tid)) == 1
    assert (
        calls_repo.latest_for_thesis(db, tid)[0].state is State.INCUBATING
    )  # X's buy did not warm it

    daily.run_daily(db, asof=_NIGHT, allow_live=True)  # an identical re-run
    assert len(_calls(db, tid)) == 1  # COUNT the table: still one row

    _roster(db, tid, in_chain)  # the operator moves X into the chain
    daily.run_daily(db, asof=_NIGHT, allow_live=True)
    assert len(_calls(db, tid)) == 2
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.WARMING  # now X's buy counts

    daily.run_daily(db, asof=_NIGHT, allow_live=True)
    assert len(_calls(db, tid)) == 2

    _roster(db, tid, in_context)  # ...and back to Context
    daily.run_daily(db, asof=_NIGHT, allow_live=True)
    assert len(_calls(db, tid)) == 3
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.INCUBATING
    daily.run_daily(db, asof=_NIGHT, allow_live=True)
    assert len(_calls(db, tid)) == 3
