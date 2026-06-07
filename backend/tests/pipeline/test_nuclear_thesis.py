from __future__ import annotations

from datetime import date, datetime, timezone

from domain.enums import Kind, State, Verdict
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import NUCLEAR_THESIS_ID, seed_nuclear

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_nuclear_is_an_honest_warming_thesis(db):
    """The small-scale-nuclear basket broke out sector-wide (2026-06-02) on REAL EOD, but has no
    insider conviction — so the platform WARMS without arming, and says the missing key is conviction
    (not a breakout). The discipline working: a sector move alone is not a reason to act.
    """
    seed_nuclear(db)
    db.commit()

    card = call_for_thesis(db, NUCLEAR_THESIS_ID, date(2026, 6, 5), known_at=_KNOWN, record=False)

    assert card.state is State.WARMING
    assert card.verdict is Verdict.NOT_YET
    assert card.armed_security_id is None
    # a not-yet theme card carries NO confidence bar — four breakouts across four names must not
    # noisy-OR into a false "high" (the 100%-on-the-Warming-card render bug this basket exposed)
    assert card.confidence is None
    assert card.key_confirmation.turned  # the sector breakout is in (confirmation key)
    assert not card.key_conviction.turned  # but there's no insider-conviction key
    assert any("conviction" in m.lower() for m in card.missing)
    assert "conviction" in card.expression.lower()  # honest, not the HIMS "hold for a breakout"
    # the real breakouts fired across the basket names
    assert any(t.kind is Kind.TECHNICAL_BREAKOUT for t in card.triggers_fired)
