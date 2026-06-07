from __future__ import annotations

from datetime import date, datetime, timezone

from domain.enums import CatalystType, Grade, State, Verdict
from ingest.catalyst import ingest_catalyst
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import NUCLEAR_THESIS_ID, OKLO_ID, seed_nuclear

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_ASOF = date(
    2026, 6, 5
)  # all four nuclear names have a live breakout here (see test_nuclear_thesis)


def test_a_ratified_catalyst_arms_a_theme_name_at_its_breakout(db):
    """The #10 unlock, on REAL nuclear data: the basket broke out sector-wide but had no conviction key,
    so it could only WARM. The operator ratifies a binding catalyst (a power-offtake deal) on OKLO via
    the bridge -> conviction co-locates with OKLO's live breakout -> OKLO ARMS -> the theme reaches a
    computed core_entry. The discipline holds: a catalyst alone still wouldn't arm without confirmation.
    """
    seed_nuclear(db)
    db.commit()

    # before: confirmation (breakouts) but no conviction -> Warming (the honest theme state today)
    before = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert before.state is State.WARMING
    assert before.key_confirmation.turned and not before.key_conviction.turned

    # the operator ratifies a binding catalyst on OKLO — a real, provenanced, append-only fact
    ingest_catalyst(
        db,
        OKLO_ID,
        catalyst_type=CatalystType.CONTRACT,
        grade=Grade.CORE,
        label="20-year power-offtake agreement with a hyperscaler",
        source="ratified",
        source_ref="https://example.com/oklo-ppa-8k",
        event_date=date(
            2026, 5, 15
        ),  # within the 365d core horizon, co-located with the 06-02 breakout
        ratified_by="operator",
    )
    db.commit()

    after = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert after.state is State.ARMED
    assert (
        after.armed_security_id == OKLO_ID
    )  # the catalyst armed the name it fired on (co-location)
    assert after.key_conviction.turned and after.key_confirmation.turned
    assert (
        after.verdict is Verdict.CORE_ENTRY
    )  # binding catalyst + volume-backed breakout = full entry
    assert after.confidence is not None
    # the catalyst's provenance rides the conviction trigger (show the work — never a model guess)
    refs = [p.ref for t in after.triggers_fired for p in t.sources]
    assert "https://example.com/oklo-ppa-8k" in refs
