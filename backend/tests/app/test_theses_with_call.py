"""``GET /theses?with_call=true&asof=``: the nightly RECORD in effect at the as-of, on the list (DB test).

The record in effect = the ``calls`` row with the greatest ``(asof, seq)`` on or before the as-of; ``null`` when
there is none; ``with_call`` needs ``asof``; and without ``with_call`` the response is exactly today's list (no
``call`` key), so the Board does not change.
"""

from __future__ import annotations

import uuid
from datetime import date

from calls.assembler import assemble_call
from domain.call import TriggerRef
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, State, Verdict
from domain.signal import Provenance
from repositories import calls_repo, thesis_repo
from tests.calls.factories import make_thesis

LEGACY_KEYS = {"id", "name", "ticker", "basket_size", "narrative", "archived"}


def _seed(db, security_id):
    with_rows = make_thesis(id=uuid.uuid4(), name="Thesis 01 synthetic", basket=[])
    no_rows = make_thesis(id=uuid.uuid4(), name="Thesis 02 synthetic", basket=[])
    thesis_repo.upsert(db, with_rows)
    thesis_repo.upsert(db, no_rows)
    base = assemble_call(with_rows, [], date(2026, 10, 1), DEFAULT_CONFIG)
    calls_repo.append(db, base)  # 10-01: incubating
    armed = base.model_copy(
        update={
            "asof": date(2026, 10, 5),
            "state": State.ARMED,
            "verdict": Verdict.STARTER_ENTRY,
            "conviction_grade": Grade.CORE,
            "confirmation_grade": Grade.FLIP,
            "entry_grade": Grade.FLIP,
            "confidence": 0.6,
            "exit_by": date(2026, 12, 1),
            "arm_until": date(2026, 10, 15),
            "armed_security_id": security_id,
            "missing": ["one condition"],
            "triggers_fired": [
                TriggerRef(
                    label="Synthetic insider buy",
                    kind=Kind.INSIDER,
                    grade=Grade.CORE,
                    event_date=date(2026, 10, 4),
                    security_id=security_id,
                    sources=[Provenance(source="form4", ref="0000000000-26-000001")],
                ),
                TriggerRef(
                    label="Synthetic breakout",
                    kind=Kind.TECHNICAL_BREAKOUT,
                    event_date=date(2026, 10, 5),
                    security_id=uuid.uuid4(),  # no master row: no ticker, never a guess
                ),
            ],
        }
    )
    calls_repo.append(db, armed.model_copy(update={"verdict": Verdict.NOT_YET}))  # superseded
    calls_repo.append(db, armed, reconstructed=True)  # same night, later seq: the record
    db.commit()
    return with_rows.id, no_rows.id


def _by_id(listing):
    return {item["id"]: item for item in listing}


def test_the_later_night_wins_at_a_later_asof_and_the_earlier_at_an_earlier_one(
    client, db, security_id
):
    tid, _ = _seed(db, security_id)
    later = _by_id(client.get("/theses?with_call=true&asof=2026-10-07").json())[str(tid)]["call"]
    assert later["record_asof"] == "2026-10-05"
    assert later["state"] == "armed" and later["verdict"] == "starter_entry"  # the later seq wins
    assert later["reconstructed"] is True
    assert (later["conviction_grade"], later["confirmation_grade"], later["entry_grade"]) == (
        "core",
        "flip",
        "flip",
    )
    assert later["confidence"] == 0.6
    assert (later["exit_by"], later["arm_until"]) == ("2026-12-01", "2026-10-15")
    assert later["armed_security_id"] == str(security_id)
    assert later["armed_ticker"] == "DEVCO"  # resolved through the master, like the live call
    assert (later["armed_count"], later["missing_count"]) == (0, 1)
    assert later["recorded_at"]
    assert later["triggers"] == [
        {
            "ticker": "DEVCO",  # resolved through the master in the list's one batch query
            "kind": "insider",
            "label": "Synthetic insider buy",
            "grade": "core",
            "event_date": "2026-10-04",
        },
        {
            "ticker": None,
            "kind": "technical_breakout",
            "label": "Synthetic breakout",
            "grade": None,
            "event_date": "2026-10-05",
        },
    ]

    earlier = _by_id(client.get("/theses?with_call=true&asof=2026-10-03").json())[str(tid)]["call"]
    assert earlier["record_asof"] == "2026-10-01" and earlier["state"] == "incubating"
    assert earlier["armed_ticker"] is None and earlier["triggers"] == []


def test_a_thesis_with_no_row_by_the_asof_carries_call_null(client, db, security_id):
    tid, bare = _seed(db, security_id)
    listing = _by_id(client.get("/theses?with_call=true&asof=2026-09-30").json())
    assert listing[str(tid)]["call"] is None  # rows exist, but none on or before 09-30
    assert listing[str(bare)]["call"] is None
    assert "call" in listing[str(bare)]


def test_with_call_without_asof_is_422(client, db, security_id):
    _seed(db, security_id)
    assert client.get("/theses?with_call=true").status_code == 422


def test_without_with_call_the_list_is_todays_response(client, db, security_id):
    _seed(db, security_id)
    for url in ("/theses", "/theses?include_archived=true", "/theses?asof=2026-10-07"):
        listing = client.get(url).json()
        assert listing and all(set(item) == LEGACY_KEYS for item in listing), url


def test_archive_and_unarchive_responses_keep_their_shape(client, db, security_id):
    tid, _ = _seed(db, security_id)
    archived = client.post(f"/theses/{tid}/archive").json()
    restored = client.post(f"/theses/{tid}/unarchive").json()
    assert set(archived) == set(restored) == LEGACY_KEYS


def test_the_list_read_writes_no_call_row(client, db, security_id):
    _seed(db, security_id)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        before = cur.fetchone()["n"]
    client.get("/theses?with_call=true&asof=2026-10-07")
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        assert cur.fetchone()["n"] == before == 3
