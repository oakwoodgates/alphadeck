"""EDITION 0 IS TODAY'S READ, BYTE FOR BYTE — a golden taken BEFORE the editions read path landed (S4).

The Scoreboard's default view is Edition 0, the original record: the calls log read exactly as it always was
(the three set-aside readings, the provenance flags, the run identity, the metrics). Editions S4 adds a
timeline-source seam under ``scoreboard/record.py``; this test pins that Edition 0 through the seam returns the
SAME JSON the endpoint returned before the seam existed.

The golden (``tests/fixtures/scoreboard/edition0_golden.json``) was written from the pre-S4 code by running
this file with ``ALPHADECK_WRITE_GOLDEN=1``. Only three things are dropped before the compare: ``generated_at``
(the read's own wall-clock stamp), the ``edition`` block S4 adds (asserted to name Edition 0; the rest of it
is tested in ``test_scoreboard_editions_api.py``) and the per-episode ``edition_join`` S4 adds (asserted
null: the record has no join dates). Every pre-existing key must match exactly. Everything the seed touches
carries a FIXED id and stamp, and the router's clock seam is pinned, so the payload is deterministic.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.routers import scoreboard
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from repositories import call_annotations_repo
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "scoreboard" / "edition0_golden.json"
WRITE = os.environ.get("ALPHADECK_WRITE_GOLDEN") == "1"

S1 = uuid.UUID("00000000-0000-0000-0000-00000000e0a1")
S2 = uuid.UUID("00000000-0000-0000-0000-00000000e0a2")
T1 = uuid.UUID("00000000-0000-0000-0000-00000000e0b1")
T2 = uuid.UUID("00000000-0000-0000-0000-00000000e0b2")
ASOF = "2026-07-31"
# a Friday evening, after RUN_AT: the record freshness fields read off this pinned clock
NOW = datetime(2026, 7, 31, 23, 0, tzinfo=timezone.utc)


def _security(db, sid: uuid.UUID, ticker: str, cik: str, name: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, name, "2026-01-01"),
        )
    db.commit()


def _call_id(db, thesis_id: uuid.UUID, asof: date) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM calls WHERE thesis_id = %s AND asof = %s ORDER BY seq LIMIT 1",
            (thesis_id, asof),
        )
        return cur.fetchone()["id"]


def seed(db) -> None:
    """Two theses. T1: a warming row then five matured arm cycles (enough to clear MIN_N), stamped with a run
    identity and ingest health. T2: an annotated phantom-arm night, a late re-version, a reconstructed night
    and an open arm — every set-aside reading has something to say."""
    _security(db, S1, "GOLDA", "0000000101", "Golden A Inc.")
    _security(db, S2, "GOLDB", "0000000102", "Golden B Corp.")
    t1 = persist_thesis(db, S1, thesis_id=T1)
    t2 = persist_thesis(db, S2, thesis_id=T2)

    warm = [
        insider_event(security_id=S1, liveness=3).model_copy(update={"asof": date(2026, 5, 29)})
    ]
    record_day(
        db, t1, warm, date(2026, 5, 29), config_hash="cfgA", code_sha="sha1", run_kind="cron"
    )
    closes = [(100.0, 105.0), (100.0, 98.0), (100.0, 110.0), (100.0, 99.0), (100.0, 104.0)]
    for i, (entry, exit_) in enumerate(closes):
        fire = date(2026, 6, 1) + timedelta(days=7 * i)
        conv, conf = keys_fired(S1, fire, conv_liveness=3, conf_liveness=3)
        record_day(
            db,
            t1,
            [conv, conf],
            fire,
            ingest_fresh=(i != 2),
            ingest_errors=0 if i != 2 else 3,
            config_hash="cfgA",
            code_sha="sha1",
            run_kind="cron",
        )
        record_day(db, t1, [conv], fire + timedelta(days=4), config_hash="cfgA", run_kind="cron")
        bar(db, S1, fire, entry, recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
        bar(
            db,
            S1,
            fire + timedelta(days=2),
            exit_,
            recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

    conv, conf = keys_fired(S2, date(2026, 7, 9), conv_liveness=120, conf_liveness=120)
    record_day(db, t2, [conv], date(2026, 7, 9))  # warming: the next arm is not censored
    record_day(db, t2, [conv, conf], date(2026, 7, 10))  # annotated below (phantom arm)
    late_at = datetime(2026, 7, 14, 13, 0, tzinfo=timezone.utc)  # 09:00 ET the next Tuesday
    record_day(db, t2, [conv], date(2026, 7, 13))  # on time: warming
    record_day(db, t2, [conv, conf], date(2026, 7, 13), recorded_at=late_at)  # the late re-version
    record_day(db, t2, [conv, conf], date(2026, 7, 14), reconstructed=True)  # a backfilled night
    record_day(db, t2, [conv, conf], date(2026, 7, 15), run_kind="manual", code_sha="sha2")
    record_day(db, t2, [conv, conf], date(2026, 7, 16))
    for d, c in ((date(2026, 7, 10), 50.0), (date(2026, 7, 15), 52.0), (date(2026, 7, 16), 53.5)):
        bar(db, S2, d, c, recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    call_annotations_repo.append_annotation(
        db,
        _call_id(db, T2, date(2026, 7, 10)),
        action="exclude",
        reason="stale_comparative_revenue",
        rule="d3a-first-knowability@golden",
    )
    db.commit()
    # one insider buy on the drawer's tape, accepted the day it traded, stored a week later
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": S2,
            "insider_name": "Gold Buyer",
            "insider_role": "CEO",
            "txn_code": "P",
            "shares": 1000.0,
            "price": 50.0,
            "usd": 50_000.0,
            "accession": "0000000000-26-000777",
            "txn_seq": 0,
            "valid_from": date(2026, 7, 15),
            "accepted": datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc),
            "recorded_at": datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc),
        },
    )
    db.commit()


def payloads(client, edition: dict[str, str] | None = None) -> dict[str, object]:
    """Every read the golden pins: the three Scoreboard readings and one drawer price window. ``edition``
    adds S4's explicit ``edition=0`` to every request (it must change nothing)."""
    ed = edition or {}
    out: dict[str, object] = {}
    for name, extra in (
        ("default", {}),
        ("include_annotated", {"include_annotated": "true"}),
        ("include_late", {"include_late": "true"}),
    ):
        r = client.get("/scoreboard", params={"asof": ASOF, **extra, **ed})
        assert r.status_code == 200, r.text
        body = r.json()
        body.pop("generated_at")
        block = body.pop("edition", None)  # S4's new block — absent before it
        if block is not None:  # once S4 lands, the default read IS Edition 0
            assert (block["number"], block["label"]) == (0, "the original record")
        for t in body["theses"]:
            for ep in t["episodes"]:
                # S4's per-episode join date: an edition-only field, null on the record
                assert ep.pop("edition_join", None) is None
        out[name] = body
    r = client.get(
        "/scoreboard/price-window",
        params={
            "thesis_id": str(T2),
            "security_id": str(S2),
            "start": "2026-07-10",
            "end": "2026-11-07",
            "asof": ASOF,
            **ed,
        },
    )
    assert r.status_code == 200, r.text
    out["price_window"] = r.json()
    return out


def test_edition_0_returns_exactly_the_pre_editions_json(client, db, monkeypatch):
    monkeypatch.setattr(scoreboard, "_now", lambda: NOW)
    seed(db)
    got = payloads(client)
    if WRITE:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"golden written to {GOLDEN}")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == want
    # S4: naming Edition 0 explicitly is the same read
    assert payloads(client, {"edition": "0"}) == want
