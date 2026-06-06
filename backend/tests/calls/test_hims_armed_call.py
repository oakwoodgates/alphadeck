from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Archetype, Grade, State, Verdict
from domain.thesis import BasketMember, Thesis
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from signals import insider_conviction, volume_breakout
from signals.base import PointInTimeData

# M3 Checkpoint A: the computed Armed call on REAL HIMS data (committed fixtures), proven in a test
# before any UI. David Wells' $1.17M open-market buy (Key 1, CORE) + the 2026-06-01 momentum breakout
# (Key 2, momentum-only) -> an honest Armed core_entry: reduced confidence + a volume-gap counter-case.
_F = Path(__file__).resolve().parent.parent / "fixtures"
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_WELLS_ACCESSION = "0001773751-26-000086"


def _seed_hims(db, security_id):
    ingest_form4(
        db,
        security_id,
        (_F / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
    )
    bars = parse_yahoo_chart(
        json.loads((_F / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
    )
    ingest_prices(db, security_id, bars)


def _thesis(security_id) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        name="HIMS — insider conviction",
        narrative="A director bought ~$1.2M open-market off the lows; watching for confirmation.",
        ticker="HIMS",
        basket=[
            BasketMember(
                ticker="HIMS",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
            )
        ],
    )


def _call_asof(db, security_id, asof: date):
    pit = PointInTimeData(db, asof=asof, known_at=_KNOWN)
    events = [
        e
        for e in (
            insider_conviction.detect(pit, security_id, asof),
            volume_breakout.detect(pit, security_id, asof),
        )
        if e
    ]
    return assemble_call(_thesis(security_id), events, asof, DEFAULT_CONFIG)


def test_hims_warming_before_the_breakout(db, security_id):
    _seed_hims(db, security_id)
    card = _call_asof(db, security_id, date(2026, 5, 28))
    assert card.state is State.WARMING
    assert card.key_conviction.turned and not card.key_confirmation.turned


def test_hims_armed_core_entry_is_honest_on_real_data(db, security_id):
    _seed_hims(db, security_id)
    card = _call_asof(db, security_id, date(2026, 6, 1))

    assert card.state is State.ARMED
    # the headline matches the action: a core THESIS but a STARTER entry (volume hasn't confirmed) —
    # NOT a bare core_entry the operator would over-commit to
    assert card.verdict is Verdict.STARTER_ENTRY
    assert card.conviction_grade is Grade.CORE
    assert card.entry_grade is Grade.FLIP
    assert card.key_conviction.turned and card.key_confirmation.turned

    # honest: the confirmation is momentum-only -> reduced confidence + the volume-gap counter-case
    assert card.confidence <= DEFAULT_CONFIG.momentum_only_confidence_cap
    assert "momentum-only" in card.counter_case.lower() and "volume" in card.counter_case.lower()

    # the real Form 4 accession rides the conviction trigger's provenance (the working source link)
    refs = [p.ref for t in card.triggers_fired for p in t.sources]
    assert _WELLS_ACCESSION in refs
    assert card.exit_by == date(2026, 6, 13)  # hold clock: Wells buy 05-26 + 18d (§9)
    assert card.arm_until == date(2026, 6, 11)  # entry window: 06-01 breakout + 10d
    assert card.armed_security_id == security_id  # conviction + confirmation co-located on HIMS


def test_hims_armed_stays_sticky_through_consolidation(db, security_id):
    # The end-to-end no-flicker test (deferred from Pre-M3a until detectors re-derive from facts):
    # on 06-02/06-03 no NEW breakout prints, but the detector reports the 06-01 breakout (still inside
    # its half-life) stamped event_date=06-01 -> the call stays ARMED, no flicker, recomputed live.
    _seed_hims(db, security_id)
    for asof in (date(2026, 6, 2), date(2026, 6, 3)):
        card = _call_asof(db, security_id, asof)
        assert card.state is State.ARMED, asof
        assert card.arm_until == date(2026, 6, 11)  # the 06-01 breakout + 10d half-life
        assert card.armed_security_id == security_id
