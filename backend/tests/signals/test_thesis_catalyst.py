"""V2 W6c: the PURE half of the thesis-catalyst rule (``signals/thesis_catalyst.py`` + the catalyst and theme
detectors it feeds). No database: a fake point-in-time view stands in for both engines.

What is pinned here (the signed rules, ``docs/CALL_LOGIC.md`` §3c):
- R2: only a counting version counts; the stored member list normalizes the same from either engine.
- R3: an entry naming members is, on each named member, the SAME event a platform company catalyst produces
  (``Kind.CATALYST``, the detector's own core / flip scores), and nothing on a member it does not name.
- R4 / N1 (a): an entry naming no member never reaches a per-member read; it is the theme conviction's input.
- R6 / N3 (a): liveness runs from the entry night to "relevant until" (capped by the catalyst cap dial), else the
  catalyst default; a whole-card entry uses the theme dial, uncapped.
- R8 / N6 (a) / N7 (a): the stronger counts (core over flip, then the later night); a full tie goes to the
  platform; the loser rides as a second, muted "also live, not counted" source and changes nothing else.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role
from signals import catalyst_conviction, theme_conviction, thesis_catalyst
from signals.catalyst_conviction import _CORE_SCORE, _FLIP_SCORE

SID = uuid.UUID(int=0xA1)
OTHER = uuid.UUID(int=0xB2)
TID = uuid.UUID(int=0x7777)
ENTRY = uuid.UUID(int=0xE1)
ENTRY2 = uuid.UUID(int=0xE2)
NIGHT = date(2026, 6, 2)


def _row(
    entry: uuid.UUID = ENTRY,
    *,
    members=(SID,),
    grade: str = "core",
    valid_from: date = NIGHT,
    relevant_until: date | None = None,
    counts: bool = True,
    source: str = "thesis_catalyst",
    citation: str = "https://example.com/award",
    label: str = "an operator entry",
    as_strings: bool = True,
) -> dict:
    """A stored version as either engine returns it: the mirror's list of uuid strings (default) or a
    Postgres list of ``UUID``."""
    return {
        "entry_id": str(entry) if as_strings else entry,
        "counts": counts,
        "security_ids": [str(m) for m in members] if as_strings else list(members),
        "grade": grade,
        "label": label,
        "citation": citation,
        "relevant_until": relevant_until,
        "source": source,
        "valid_from": valid_from,
    }


def _platform(
    grade: str = "flip", valid_from: date = date(2026, 5, 18), ref: str = "DENE0009589"
) -> dict:
    """A ``fact_catalyst`` row as the per-security read returns it (a DOE award)."""
    return {
        "grade": grade,
        "label": "DOE award",
        "source": "doe_usaspending",
        "source_ref": ref,
        "valid_from": valid_from,
        "horizon_end": None,
        "catalyst_type": None,
    }


class _Pit:
    """A pure fake view: platform catalysts per security and the card's stored versions."""

    def __init__(self, platform: dict | None = None, rows: list[dict] | None = None) -> None:
        self.asof = NIGHT
        self._platform = platform or {}
        self._rows = rows or []

    def catalyst_facts(self, security_id):
        return list(self._platform.get(security_id, []))

    def thesis_catalyst_facts(self, thesis_id):
        return list(self._rows)


def _view(platform=None, rows=()):
    inner = _Pit(platform, list(rows))
    return thesis_catalyst.ThesisCatalystView(
        inner, thesis_catalyst.counting_entries(inner.thesis_catalyst_facts(TID))
    )


# --- R2: what counts -----------------------------------------------------------------------------------------


def test_only_counting_versions_count_and_members_normalize_from_either_engine():
    rows = [
        _row(ENTRY, as_strings=True),
        _row(ENTRY2, counts=False),
        _row(uuid.UUID(int=0xE3), as_strings=False),
    ]
    out = thesis_catalyst.counting_entries(rows)
    assert len(out) == 2
    assert all(e["security_ids"] == frozenset({SID}) for e in out)


def test_a_switched_off_entry_reaches_no_member():
    view = _view(rows=[_row(counts=False)])
    assert view.catalyst_facts(SID) == []
    assert catalyst_conviction.detect(view, SID, NIGHT) is None


# --- R3: an entry naming members -----------------------------------------------------------------------------


def test_an_entry_is_a_catalyst_conviction_on_each_named_member_only():
    view = _view(rows=[_row(grade="core")])
    ev = catalyst_conviction.detect(view, SID, NIGHT)
    assert ev is not None and ev.fired
    assert ev.kind is Kind.CATALYST and ev.role is Role.ENTRY_TRIGGER
    assert ev.grade is Grade.CORE and ev.score == _CORE_SCORE
    assert ev.asof == NIGHT  # it fires on its entry night
    (prov,) = ev.provenance
    assert prov.source == "thesis_catalyst" and prov.ref == "https://example.com/award"
    assert prov.detail == {"entry_id": str(ENTRY), "counts_from": NIGHT.isoformat()}
    assert catalyst_conviction.detect(view, OTHER, NIGHT) is None  # a member it does not name


def test_a_flip_entry_scores_the_flip_catalyst_score():
    ev = catalyst_conviction.detect(_view(rows=[_row(grade="flip")]), SID, NIGHT)
    assert ev is not None and ev.grade is Grade.FLIP and ev.score == _FLIP_SCORE


def test_the_view_leaves_the_platform_facts_untouched_and_first():
    plat = _platform()
    view = _view({SID: [plat]}, [_row()])
    facts = view.catalyst_facts(SID)
    assert facts[0] == plat and len(facts) == 2
    assert _view({SID: [plat]}).catalyst_facts(SID) == [plat]  # no entry: exactly the platform read


def test_the_view_delegates_everything_else():
    inner = _Pit(rows=[_row()])
    view = thesis_catalyst.ThesisCatalystView(inner, [])
    assert view.asof == NIGHT
    assert view.thesis_catalyst_facts(TID) == inner.thesis_catalyst_facts(TID)


# --- R4 / N1: an entry naming no member ----------------------------------------------------------------------


def test_a_whole_card_entry_never_reaches_a_member_read():
    view = _view(rows=[_row(members=(), grade="flip")])
    assert view.catalyst_facts(SID) == []


def test_a_whole_card_entry_is_the_theme_fact_the_broadcast_reads():
    row = _row(
        members=(),
        grade="flip",
        valid_from=date(2026, 1, 15),
        relevant_until=date(2027, 1, 15),
        source="ratified",
        citation="https://www.congress.gov/bill/118th-congress/senate-bill/1111",
        label="theme",
    )
    fact = theme_conviction.detect_fact(_Pit(rows=[row]), TID, NIGHT)
    assert fact == {
        "valid_from": date(2026, 1, 15),
        "horizon_end": date(2027, 1, 15),
        "grade": "flip",
        "label": "theme",
        "source": "ratified",
        "source_ref": "https://www.congress.gov/bill/118th-congress/senate-bill/1111",
    }
    assert theme_conviction.detect_fact(_Pit(rows=[_row()]), TID, NIGHT) is None  # names a member
    assert theme_conviction.detect_fact(_Pit(rows=[{**row, "counts": False}]), TID, NIGHT) is None


# --- R6 / N3: how long it stays live -------------------------------------------------------------------------


def test_liveness_runs_from_the_entry_night_to_relevant_until():
    until = NIGHT + timedelta(days=40)
    ev = catalyst_conviction.detect(_view(rows=[_row(relevant_until=until)]), SID, NIGHT)
    assert ev is not None and ev.alpha_liveness_days == 40
    later = until + timedelta(days=5)
    view = thesis_catalyst.ThesisCatalystView(
        _Pit(), thesis_catalyst.counting_entries([_row(relevant_until=until)])
    )
    assert catalyst_conviction.detect(view, SID, later) is None  # past relevant-until: not live


def test_with_no_relevant_until_the_catalyst_default_dial_applies():
    cfg = DEFAULT_CONFIG.model_copy(update={"catalyst_default_horizon_days": 10})
    view = _view(rows=[_row()])
    ev = catalyst_conviction.detect(view, SID, NIGHT, cfg)
    assert ev is not None and ev.alpha_liveness_days == 10
    assert catalyst_conviction.detect(view, SID, NIGHT + timedelta(days=30), cfg) is None


def test_the_cap_dial_applies_to_a_member_entry():
    cfg = DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": 20})
    view = _view(rows=[_row(relevant_until=NIGHT + timedelta(days=300))])
    ev = catalyst_conviction.detect(view, SID, NIGHT, cfg)
    assert ev is not None and ev.alpha_liveness_days == 20


def test_a_whole_card_entry_uses_the_theme_dial_with_no_cap():
    row = _row(members=(), grade="flip", relevant_until=NIGHT + timedelta(days=300))
    capped = DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": 20})
    later = NIGHT + timedelta(days=200)
    assert theme_conviction.detect_fact(_Pit(rows=[row]), TID, later, capped) is not None
    short = DEFAULT_CONFIG.model_copy(update={"theme_conviction_default_horizon_days": 10})
    open_ended = _row(members=(), grade="flip")
    assert theme_conviction.detect_fact(_Pit(rows=[open_ended]), TID, NIGHT, short) is not None
    assert (
        theme_conviction.detect_fact(
            _Pit(rows=[open_ended]), TID, NIGHT + timedelta(days=30), short
        )
        is None
    )


# --- R8 / N6 / N7: a platform catalyst and an entry on the same member ---------------------------------------


def _pick(platform: dict, row: dict):
    return catalyst_conviction.detect(_view({SID: [platform]}, [row]), SID, NIGHT)


def test_a_core_entry_beats_a_flip_platform_catalyst_and_shows_it_as_also_live():
    ev = _pick(_platform(grade="flip"), _row(grade="core"))
    assert ev.grade is Grade.CORE and ev.score == _CORE_SCORE
    assert [p.source for p in ev.provenance] == ["thesis_catalyst", "doe_usaspending"]
    also = ev.provenance[1]
    assert also.ref == "DENE0009589"
    assert also.detail == {"also_live": True, "counted": False, "label": "DOE award"}


def test_a_core_platform_catalyst_beats_a_flip_entry_which_changes_nothing_else():
    plat = _platform(grade="core")
    alone = catalyst_conviction.detect(_view({SID: [plat]}), SID, NIGHT)
    ev = _pick(plat, _row(grade="flip"))
    assert ev.provenance[0] == alone.provenance[0]
    assert ev.provenance[1].source == "thesis_catalyst"
    assert ev.provenance[1].detail == {
        "entry_id": str(ENTRY),
        "counts_from": NIGHT.isoformat(),
        "also_live": True,
        "counted": False,
        "label": "an operator entry",
    }
    # the loser never changes the score, the grade, the liveness, the label or the fire date
    assert ev.model_dump(exclude={"provenance"}) == alone.model_dump(exclude={"provenance"})


def test_on_the_same_grade_the_later_night_wins():
    ev = _pick(_platform(grade="flip", valid_from=NIGHT - timedelta(days=1)), _row(grade="flip"))
    assert ev.provenance[0].source == "thesis_catalyst"
    ev = _pick(
        _platform(grade="flip", valid_from=NIGHT),
        _row(grade="flip", valid_from=NIGHT - timedelta(days=1)),
    )
    assert ev.provenance[0].source == "doe_usaspending"


def test_a_full_tie_goes_to_the_platform():
    for grade in ("core", "flip"):
        ev = _pick(_platform(grade=grade, valid_from=NIGHT), _row(grade=grade, valid_from=NIGHT))
        assert ev.provenance[0].source == "doe_usaspending", grade
        assert ev.provenance[1].source == "thesis_catalyst"


def test_also_live_appears_only_across_origins():
    two_platform = catalyst_conviction.detect(
        _view({SID: [_platform(grade="flip"), _platform(grade="core", ref="X-2")]}), SID, NIGHT
    )
    assert len(two_platform.provenance) == 1
    two_entries = catalyst_conviction.detect(
        _view(rows=[_row(ENTRY, grade="flip"), _row(ENTRY2, grade="core")]), SID, NIGHT
    )
    assert len(two_entries.provenance) == 1 and two_entries.grade is Grade.CORE


def test_a_lapsed_loser_is_not_shown():
    old = _platform(grade="flip", valid_from=date(2020, 1, 1))  # 365-day default: long lapsed
    ev = _pick(old, _row(grade="core"))
    assert len(ev.provenance) == 1


def test_the_platform_only_pick_is_unchanged():
    """Byte identity for every card today: with no entry the event is exactly what ``score`` made before."""
    plat = [
        _platform(grade="flip"),
        _platform(grade="core", ref="X-2", valid_from=date(2026, 5, 1)),
    ]
    ev = catalyst_conviction.score(plat, SID, NIGHT)
    assert ev.grade is Grade.CORE and len(ev.provenance) == 1
    assert ev.provenance[0].ref == "X-2" and ev.provenance[0].detail == {}


def test_the_reader_declares_its_table_unbounded():
    assert thesis_catalyst.HORIZONS(DEFAULT_CONFIG) == {"fact_thesis_catalyst": None}
