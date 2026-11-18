"""V2 W6: the CHARACTERIZATION golden for thesis catalysts (committed before any W6 change).

W6 puts a user's counting catalyst entries into the call. Until W6c turns the rule on, nothing may move:
and when it does, every card that has no counting entry (every card today) must stay byte for byte what it
was. This test pins that. It assembles the seeded HIMS, nuclear (its own catalysts, the DOE feed and the theme
conviction) and UNH cards through the ONE seam (``pipeline.core.assemble_from_pit``) at a fixed set of nights,
on the live view (record and public clock) and on an exported mirror (record and public), and compares each
card, serialized exactly as the calls log stores it (``repositories.mappers.call_to_row``:
``model_dump(mode="json")``), with the committed ``golden/w6_card_identity.json``.

The golden was generated from the pre-W6 tree. A later PR must pass it WITHOUT regenerating it; regenerating
is only for a deliberate, operator-signed change of what a call computes (``W6_GOLDEN_REGENERATE=1``).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.market_time import known_at_for_asof
from pipeline.call_for_thesis import load_call_inputs
from pipeline.core import assemble_from_pit
from pipeline.seed import (
    HIMS_THESIS_ID,
    NUCLEAR_THESIS_ID,
    UNH_THESIS_ID,
    seed_doe_catalysts,
    seed_hims,
    seed_leu_catalyst,
    seed_nuclear,
    seed_nuclear_catalyst,
    seed_nuclear_theme_conviction,
    seed_unh,
)
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories.mappers import call_to_row

GOLDEN = Path(__file__).parent / "golden" / "w6_card_identity.json"

# the record clock's read pin: after every seeded row (they are stamped at test time)
_PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)

# the seed's demo night, the theme conviction's stated date and its neighbors, the night it was recorded on
# the founding store and the next, and the theme horizon's end and its neighbors
NIGHTS: tuple[date, ...] = (
    date(2026, 1, 14),
    date(2026, 1, 15),
    date(2026, 1, 16),
    date(2026, 6, 2),  # the theme-armed SMR night (tests/pipeline/test_theme_arm.py)
    date(2026, 6, 5),
    date(2026, 6, 9),
    date(2026, 6, 10),
    date(2027, 1, 14),
    date(2027, 1, 15),
    date(2027, 1, 18),  # 2027-01-16 is a Saturday: the next weekday night
)
THESES = {"hims": HIMS_THESIS_ID, "nuclear": NUCLEAR_THESIS_ID, "unh": UNH_THESIS_ID}
CLOCKS = ("record", "public")


def _seed(db) -> None:
    seed_hims(db)
    seed_unh(db)
    seed_nuclear(db)
    seed_nuclear_catalyst(db)
    seed_leu_catalyst(db)
    seed_doe_catalysts(db)
    seed_nuclear_theme_conviction(db)
    db.commit()


def _known_at(clock: str, night: date) -> datetime:
    return _PIN if clock == "record" else known_at_for_asof(night, _PIN)


def _stored(card) -> dict:
    """The card exactly as the calls log stores it (``call_to_row``'s ``card`` column)."""
    return call_to_row(card)["card"]


def _cards(db, tmp_path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    mirrors = {}
    for clock in CLOCKS:
        path = tmp_path / f"mirror_{clock}"
        export_snapshot(db, path, clock=clock, workspace=DEFAULT_TENANT_ID)
        mirrors[clock] = connect_mirror(path)
    try:
        for name, thesis_id in THESES.items():
            for night in NIGHTS:
                for clock in CLOCKS:
                    known_at = _known_at(clock, night)
                    thesis, pit = load_call_inputs(
                        db, thesis_id, night, known_at=known_at, clock=clock
                    )
                    live = assemble_from_pit(pit, thesis, night, DEFAULT_CONFIG)
                    out[f"{name}|{night.isoformat()}|live|{clock}"] = _stored(live)
                    rep = ReplayPointInTimeData(
                        mirrors[clock], asof=night, known_at=known_at, workspace=DEFAULT_TENANT_ID
                    )
                    mirror = assemble_from_pit(rep, thesis, night, DEFAULT_CONFIG)
                    out[f"{name}|{night.isoformat()}|mirror|{clock}"] = _stored(mirror)
    finally:
        for con in mirrors.values():
            con.close()
    return out


def _bytes(card: dict) -> str:
    return json.dumps(card, sort_keys=True, ensure_ascii=False)


def test_every_seeded_card_is_byte_identical_to_the_pre_w6_golden(db, tmp_path):
    _seed(db)
    cards = _cards(db, tmp_path)
    if os.environ.get("W6_GOLDEN_REGENERATE") == "1":  # a deliberate, signed change only
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(cards, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted(cards), "the set of (card, night, engine, clock) cases moved"
    moved = [key for key in sorted(cards) if _bytes(cards[key]) != _bytes(golden[key])]
    assert not moved, f"{len(moved)} card(s) are no longer byte-identical: {moved[:6]}"


def test_the_golden_is_not_vacuous():
    """The golden pins real calls: at least one armed card, and the theme-armed nuclear member on the demo
    night (so a fold that lost the theme conviction cannot pass by comparing empty cards)."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(golden) == len(THESES) * len(NIGHTS) * len(CLOCKS) * 2
    states = {card["state"] for card in golden.values()}
    assert "armed" in {s.lower() for s in states}
    demo = golden["nuclear|2026-06-02|live|record"]
    assert any(m.get("theme_armed") for m in demo.get("armed_members", []))
