from __future__ import annotations

from datetime import date, datetime, timezone

from domain.enums import Grade, State, Verdict
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import NUCLEAR_THESIS_ID, OKLO_ID, seed_nuclear, seed_nuclear_catalyst

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_ASOF = date(
    2026, 6, 5
)  # all four nuclear names have a live breakout here (see test_nuclear_thesis)


def test_a_ratified_catalyst_arms_the_theme_as_a_disciplined_starter(db):
    """The #10 unlock, on REAL nuclear data + the real OKLO DOE OTA: the basket broke out sector-wide but
    had no conviction key, so it could only WARM. The operator-ratified DOE Reactor Pilot Program OTA on
    OKLO (flip grade — provisional, but a multi-year horizon) co-locates with OKLO's live breakout ->
    OKLO ARMS as a disciplined STARTER (not a do-not-hold flip, because the conviction's HORIZON is long
    — the option-A fix). The discipline holds: the catalyst alone wouldn't arm without confirmation.
    """
    seed_nuclear(db)
    db.commit()

    # before ratification: confirmation (breakouts) but no conviction -> Warming (the honest theme state)
    before = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert before.state is State.WARMING
    assert before.key_confirmation.turned and not before.key_conviction.turned

    seed_nuclear_catalyst(db)  # the operator ratifies the OKLO DOE OTA via the bridge
    db.commit()

    after = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert after.state is State.ARMED
    assert (
        after.armed_security_id == OKLO_ID
    )  # the catalyst armed the name it fired on (co-location)
    assert after.key_conviction.turned and after.key_confirmation.turned
    # flip (provisional) conviction + long horizon -> a disciplined STARTER, NOT flip_only / do-not-hold
    assert after.verdict is Verdict.STARTER_ENTRY
    assert after.conviction_grade is Grade.FLIP
    assert "do not hold" not in after.expression.lower()
    assert after.confidence is not None
    # the specific DOE OTA (DENE0009589) rides the conviction trigger as provenance (show the work)
    refs = [p.ref for t in after.triggers_fired for p in t.sources]
    assert any("DENE0009589" in r for r in refs)
