"""V2 W6c: R5 in an EDITIONS re-run. The editions engine re-runs every thesis-night over a public-clock mirror cut
after the entry was saved (``editions.engine.assemble_night``, the per-night seam the trial and the publish use):
the nights before the entry's night are not armed by it, whatever its typed date; the entry's night is. And the
engine's card equals the live history view's card on every one of those nights (the editions engine still agrees
with the live path).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from db.session import DEFAULT_TENANT_ID
from domain.enums import Grade, Kind, State
from domain.market_time import serve_view
from editions.engine import assemble_night, open_mirror
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import NUCLEAR_THESIS_ID, SMR_ID, seed_nuclear
from repositories import thesis_repo
from repositories.calls_repo import _canonical
from repositories.thesis_catalyst_repo import entry_night
from tests.editions.test_engine import _cut
from tests.thesis_catalyst_fixtures import insert_version

ET = ZoneInfo("America/New_York")


def _smr_catalyst(card) -> list:
    out = [t for t in card.triggers_fired if t.security_id == SMR_ID and t.kind is Kind.CATALYST]
    for m in [*card.armed_members, *card.watch_members]:
        out += [t for t in m.triggers if t.security_id == SMR_ID and t.kind is Kind.CATALYST]
    return out


def test_an_editions_rerun_counts_the_entry_from_its_night_only(db, tmp_path):
    seed_nuclear(db)
    saved = datetime(2026, 6, 2, 10, tzinfo=ET)  # Tuesday 10:00 ET: Tuesday's night
    insert_version(
        db,
        NUCLEAR_THESIS_ID,
        uuid.uuid4(),
        at=saved,
        valid_from=entry_night(saved),
        security_ids=[SMR_ID],
        grade="core",
    )
    db.commit()
    kb = _cut(db, tmp_path / "mirror")
    thesis = thesis_repo.get(db, NUCLEAR_THESIS_ID, workspace=DEFAULT_TENANT_ID)
    db.commit()
    con = open_mirror(tmp_path / "mirror")
    try:
        cards = {
            night: assemble_night(con, thesis, night, known_by=kb)
            for night in (date(2026, 5, 29), date(2026, 6, 1), date(2026, 6, 2))
        }
    finally:
        con.close()
    assert _smr_catalyst(cards[date(2026, 5, 29)]) == []
    assert _smr_catalyst(cards[date(2026, 6, 1)]) == []
    tue = cards[date(2026, 6, 2)]
    assert tue.state is State.ARMED
    assert _smr_catalyst(tue)[0].grade is Grade.CORE
    for night, card in cards.items():
        known_at, clock = serve_view(night)
        pg = call_for_thesis(
            db, NUCLEAR_THESIS_ID, night, known_at=known_at, clock=clock, record=False
        )
        db.commit()
        assert _canonical(card) == _canonical(pg), night
