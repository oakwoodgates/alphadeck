from __future__ import annotations

from datetime import date, datetime, timezone

from domain.enums import Grade, State, Verdict
from ingest.edgar.form4 import parse_form4
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import _SEED_DATA, _UNH_FORM4S, UNH_SECURITY_ID, UNH_THESIS_ID, seed_unh

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_unh_form4_parse_oracle():
    """The five committed Form 4s parse to the verified May-2025 cluster (the parse ORACLE): five
    distinct insiders incl. CEO Hemsley + CFO Rex, all code P (open-market), ~$31.6M total. No
    model-sourced numbers — a deterministic parse of the real filings."""
    buys = []
    for _, fname in _UNH_FORM4S:
        buys += [
            t
            for t in parse_form4((_SEED_DATA / "edgar" / fname).read_text(encoding="utf-8"))
            if t["txn_code"] == "P"
        ]
    assert len(buys) == 5  # one open-market buy per insider
    assert len({t["insider_name"] for t in buys}) == 5  # five distinct insiders
    assert round(sum(t["usd"] for t in buys)) == 31_607_700
    names = " ".join(t["insider_name"].upper() for t in buys)
    assert "HEMSLEY" in names and "REX" in names  # the CEO + the CFO anchor the cluster
    assert max(t["txn_date"] for t in buys) == date(2025, 5, 16)  # the cluster's fire date


def test_unh_warms_in_may_then_arms_a_starter_at_the_weak_close_august_breakout(db):
    """The flagship 'right but early -> armed at confirmation' arc on REAL data. The CEO-led CORE
    cluster (mid-May 2025) warms; the platform withholds the go-signal through the summer slide; then
    the August volume-backed breakout confirms what the insiders saw ~3 months early and the call ARMS.
    But that breakout CLOSED WEAK (close_strength 0.119), so v1 grade-down (go-honest, 2026-08-15) demotes
    the confirmation to flip and UNH arms as a STARTER — the honest read: a weak-close breakout is a
    quick-trade, not a durable core hold, however strong the conviction behind it. Only possible because
    the conviction liveness window is graded (core ~ multi-month); the old flat 18-day clock would have
    forgotten the cluster long before the breakout.
    """
    seed_unh(db)
    db.commit()

    # mid-May: the CORE cluster is in (conviction), but no breakout yet -> WARMING (no confidence bar)
    may = call_for_thesis(db, UNH_THESIS_ID, date(2025, 5, 20), known_at=_KNOWN, record=False)
    assert may.state is State.WARMING
    assert may.key_conviction.turned and not may.key_confirmation.turned
    assert (
        may.conviction_grade is Grade.CORE
    )  # the multi-insider cluster path (not HIMS's single buy)
    assert may.confidence is None  # a not-yet card shows no confidence bar

    # August: the volume-backed breakout co-locates with the still-live conviction -> ARMED. But the
    # breakout CLOSED WEAK (close_strength 0.119), so v1 grade-down demotes the confirmation core -> flip,
    # and UNH arms as a STARTER (enter small) on its strong insider conviction — not a core hold.
    aug = call_for_thesis(db, UNH_THESIS_ID, date(2025, 8, 18), known_at=_KNOWN, record=False)
    assert aug.state is State.ARMED
    assert aug.verdict is Verdict.STARTER_ENTRY  # weak-close breakout -> starter, not core
    assert aug.conviction_grade is Grade.CORE  # the insider cluster is untouched ...
    assert aug.confirmation_grade is Grade.FLIP  # ... but the weak close demoted the confirmation
    assert aug.entry_grade is Grade.FLIP  # so the entry grade is the weaker key
    assert aug.armed_security_id == UNH_SECURITY_ID
    assert aug.confidence is not None
    refs = [p.ref for t in aug.triggers_fired for p in t.sources]
    assert any(
        r.startswith("0000731766-25-") for r in refs
    )  # the real Form 4 provenance rides along

    # at the board's default date (2026) the 2025 case has aged out -> Incubating (quiet, not a live tile)
    today = call_for_thesis(db, UNH_THESIS_ID, date(2026, 6, 1), known_at=_KNOWN, record=False)
    assert today.state is State.INCUBATING
