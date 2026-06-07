from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype, State, Verdict
from domain.thesis import BasketMember, Thesis
from ingest.edgar.converts import clean_filing_text, ingest_convert_terms, parse_convert_terms
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo, thesis_repo

# The vertical slice, end to end through persistence: seed real HIMS facts + persist the thesis, then
# compute the CallCard from the stored thesis by re-deriving signals from the facts as-of.
_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_WELLS_ACCESSION = "0001773751-26-000086"


def _seed_hims_thesis(db, security_id) -> uuid.UUID:
    ingest_form4(
        db,
        security_id,
        (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
    )
    ingest_prices(
        db,
        security_id,
        parse_yahoo_chart(
            json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
        ),
    )
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
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
    thesis_repo.upsert(db, thesis)
    terms = parse_convert_terms(
        clean_filing_text((_SEED / "edgar" / "hims_converts_8k.htm").read_text(encoding="utf-8")),
        clean_filing_text(
            (_SEED / "edgar" / "hims_converts_pricing.htm").read_text(encoding="utf-8")
        ),
    )
    ingest_convert_terms(
        db,
        security_id,
        terms,
        accession="0001193125-26-234847",
        shares_outstanding=228_357_303,
        shares_outstanding_ref="0001773751-26-000076",
    )
    db.commit()
    return thesis.id


def test_call_for_thesis_warms_then_arms_and_logs(db, security_id):
    tid = _seed_hims_thesis(db, security_id)

    warming = call_for_thesis(db, tid, date(2026, 5, 28), known_at=_KNOWN)
    assert warming.state is State.WARMING
    assert warming.key_conviction.turned and not warming.key_confirmation.turned

    armed = call_for_thesis(db, tid, date(2026, 6, 1), known_at=_KNOWN)
    assert armed.state is State.ARMED
    assert armed.verdict is Verdict.STARTER_ENTRY
    assert armed.armed_security_id == security_id
    refs = [p.ref for t in armed.triggers_fired for p in t.sources]
    assert _WELLS_ACCESSION in refs  # the working Form 4 source link survives persistence
    # the real ~$402.5M convertible-notes overhang rides the counter-case (non-blocking, stays Armed)
    assert "convertible notes" in armed.counter_case.lower()
    assert "dilution" in armed.counter_case.lower()
    db.commit()

    # both computations were captured in the write-only accountability log (not the serve path)
    logged = calls_repo.list_for_thesis(db, tid)
    assert [c.state for c in logged] == [State.WARMING, State.ARMED]


def test_call_for_thesis_is_sticky_through_consolidation(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    # 06-02/06-03 print no new breakout, but the detector re-derives the 06-01 firing from facts ->
    # the served call stays Armed (no flicker), recomputed live at each asof.
    for asof in (date(2026, 6, 2), date(2026, 6, 3)):
        card = call_for_thesis(db, tid, asof, known_at=_KNOWN, record=False)
        assert card.state is State.ARMED, asof
        assert card.arm_until == date(2026, 6, 11)  # the 06-01 breakout + 10d liveness window


def test_call_for_thesis_unknown_thesis_raises(db):
    with pytest.raises(LookupError):
        call_for_thesis(db, uuid.uuid4(), date(2026, 6, 1), known_at=_KNOWN)
