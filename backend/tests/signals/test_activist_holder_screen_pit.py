"""The 13D HOLDER SCREEN (option A, operator-approved 2026-10-28) through the REAL point-in-time views — the
Postgres ``PointInTimeData`` on the PUBLIC clock and the replay mirror exported on the same clock.

What only a database can prove: the insider half reads the subject's Form 4 rows through the view's own gate, so
a CEO whose first Form 4 is accepted AFTER his 13D is invisible to a history view until that acceptance (#1, the
public clock — the 13D fires), and from then on the 13D is screened (it stops) — and the backtest mirror, built
from the same store, gives the same answer on every night (replay parity). The rows are labeled test values (no
real filing's CIK or accession).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.market_time import known_at_for_asof
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals import activist_stake
from signals.base import PointInTimeData
from signals.horizons import call_bounds

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_INGESTED = datetime(
    2026, 9, 1, tzinfo=timezone.utc
)  # stored late — public long before (the public clock)
_FILED = date(2026, 6, 1)  # the 13D's filing date
_ACCEPTED = datetime(2026, 6, 8, 21, 0, tzinfo=timezone.utc)  # his first Form 4, 17:00 ET on 06-08
_ON = DEFAULT_CONFIG.model_copy(update={"activist_affiliate_screen": True})
_OFF = DEFAULT_CONFIG.model_copy(update={"activist_affiliate_screen": False})


def _seed(db, sid, *, role="Chairman and CEO") -> None:
    append_fact(
        db,
        "fact_activist_stake",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "form": "SCHEDULE 13D",
            "filer_cik": "0001900001",
            "filer_name": "John B. Wood",
            "pct_owned": 9.1,
            "accession": "0001900001-26-000013",
            "filed": _FILED,
            "source_ref": "https://www.sec.gov/Archives/edgar/data/1/0001900001-26-000013-index.htm",
            "valid_from": _FILED,
            "recorded_at": _INGESTED,
        },
    )
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": "Wood John B",
            "insider_role": role,
            "rpt_owner_cik": "1900001",  # the Form 4 parser stores CIKs unpadded
            "txn_code": "A",  # an award — the screen reads the role, never the code
            "shares": 50_000.0,
            "price": 0.0,
            "usd": 0.0,
            "accession": "0001900001-26-000004",
            "txn_seq": 0,
            "valid_from": date(2026, 6, 3),  # the award, dated after the 13D
            "accepted": _ACCEPTED,
            "recorded_at": _INGESTED,
        },
    )
    db.commit()


def _live(db, asof, sid):
    return PointInTimeData(
        db,
        asof=asof,
        known_at=known_at_for_asof(asof, _PIN),
        basket=[sid],
        bounds=call_bounds(_ON),
        clock="public",
    )


_NIGHTS = (date(2026, 6, 5), date(2026, 6, 8), date(2026, 6, 30))


def test_a_ceo_whose_form4_is_public_after_his_13d_fires_then_stops_on_the_public_clock(
    db, security_id
):
    _seed(db, security_id)
    before, on_accept, later = (
        activist_stake.detect(_live(db, d, security_id), security_id, d, _ON) for d in _NIGHTS
    )
    # 06-05: the 13D is public, his Form 4 is not — the platform cannot know he is the CEO: it fires
    assert before is not None and before.fired and before.asof == _FILED
    # 06-08 (end of that market day — the Form 4 was accepted at 17:00 ET) and after: screened, stays screened
    assert on_accept is None and later is None
    # the screen OFF: fires on every night, exactly as before the screen
    for d in _NIGHTS:
        off = activist_stake.detect(_live(db, d, security_id), security_id, d, _OFF)
        assert off is not None and off.asof == _FILED


def test_a_ten_pct_owner_only_role_keeps_firing_on_the_public_clock(db, security_id):
    _seed(db, security_id, role="10% owner")
    for d in _NIGHTS:
        e = activist_stake.detect(_live(db, d, security_id), security_id, d, _ON)
        assert e is not None and e.fired, d


def test_the_replay_mirror_screens_identically(db, security_id, tmp_path):
    """Replay parity: the backtest's public mirror reads the same rows, so the same detector gives the same
    output — byte for byte — on every night."""
    _seed(db, security_id)
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        for d in _NIGHTS:
            known_at = known_at_for_asof(d, _PIN)
            rep = ReplayPointInTimeData(
                con, asof=d, known_at=known_at, basket=[security_id], bounds=call_bounds(_ON)
            )
            live = _live(db, d, security_id)
            for cfg in (_ON, _OFF):
                a = activist_stake.detect(live, security_id, d, cfg)
                b = activist_stake.detect(rep, security_id, d, cfg)
                assert (a is None) == (b is None), (d, cfg.activist_affiliate_screen)
                if a is not None:
                    assert a.model_dump_json() == b.model_dump_json()
    finally:
        con.close()
