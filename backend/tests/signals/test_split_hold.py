"""The split-night hold (split plan S5, ``docs/CALL_LOGIC.md`` §2b) — GOLDEN on REAL split nights.

Every bar below is a real stored version (``tests/fixtures/splits/s5``, with its real ``recorded_at``) and every
split row the list's own. Each card is a HISTORY view (``serve_view``: the PUBLIC clock, #1 — the operator's
rule of 2026-10-21) through the real funnel (``call_for_thesis`` -> ``pipeline.core.assemble_from_pit``). The
ONLY thing a golden varies is whether the company's split row is on the list:

- **Held** — the four split nights that carried fake armed breakouts on the record (Q10 §5: GAME 08-25, NXL
  09-01, MDCX 10-09 — its 10-08 step bar first arrived re-scaled that night — and VRME 10-01) with the split
  NOT on the list: the tape reads two scales, the price-path triggers read only the bars before the step, and
  the card says why. Without the hold the same view fires the +several-hundred-percent breakout.
- **Not held** — the same rows with the split confirmed on the list: the history view reads one scale, there
  is no step, and nothing is held.
- **Never held** — PTIX 2025-05-19 (x3.7 on x1,239 volume) and JAGX 2026-09-22 / 09-23 (x12.9 on x383 volume,
  then a fall on falling volume): genuine big moves, the volume moving WITH the price.
- **A forward split** — ZCSH's real 1-for-3 (Polygon lists it, still needs review): the tape steps down to a
  third on rising volume and the hold names Polygon's row.
- **The escalation** — three sessions on, the hold has released GAME's step; with no split listed it is on the
  operator's "is this real?" list, and with the split listed it is not.
"""

from __future__ import annotations

from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import Kind
from domain.market_time import serve_view
from pipeline.call_for_thesis import call_for_thesis, load_call_inputs
from pipeline.core import assemble_from_pit
from pipeline.split_jumps import scan_unlisted_jumps
from signals import registered_detectors, split_hold
from tests import split_s5_fixtures as fx


def _view(db, tid, asof):
    known_at, clock = serve_view(asof)
    assert clock == "public"  # a past asof: a history view
    return load_call_inputs(db, tid, asof, known_at=known_at, clock=clock)


def _card(db, tid, asof):
    known_at, clock = serve_view(asof)
    return call_for_thesis(db, tid, asof, known_at=known_at, clock=clock, record=False)


def _seeded(db, tk, *, listed):
    sid = fx.seed(db, tk, listed=listed)
    return sid, fx.seed_thesis(db, [(tk, sid)])


def _held_lines(card, tk):
    return [m for m in card.missing if f" on {tk} held: " in m]


def _breakouts(card, sid, since):
    return [
        t
        for t in card.triggers_fired
        if t.security_id == sid and t.kind is Kind.TECHNICAL_BREAKOUT and t.event_date >= since
    ]


# night, ticker, the step the history view reads that night (prior bar -> first new-scale bar)
HELD = [
    pytest.param(date(2026, 8, 25), "GAME", date(2026, 8, 21), date(2026, 8, 24), id="GAME-08-25"),
    pytest.param(date(2026, 9, 1), "NXL", date(2026, 8, 27), date(2026, 8, 28), id="NXL-09-01"),
    pytest.param(date(2026, 10, 9), "MDCX", date(2026, 10, 7), date(2026, 10, 8), id="MDCX-10-09"),
    pytest.param(date(2026, 10, 1), "VRME", date(2026, 9, 28), date(2026, 9, 29), id="VRME-10-01"),
]


@pytest.mark.parametrize("night, tk, prior, step_day", HELD)
def test_held_on_a_real_split_night_when_the_split_is_not_on_the_list(
    db, monkeypatch, night, tk, prior, step_day
):
    sid, tid = _seeded(db, tk, listed=False)
    thesis, pit = _view(db, tid, night)
    holds = split_hold.find_holds(pit, thesis, night)
    assert set(holds) == {sid}
    step = holds[sid].step
    assert (step.prior_d, step.d) == (prior, step_day) and step.up and step.step >= 3

    card = assemble_from_pit(pit, thesis, night)
    (line,) = _held_lines(card, tk)
    assert line.endswith(
        f"held: possible unconfirmed split (the price {step.describe()} against the volume; no split "
        "filing found yet)"
    )
    assert line.startswith("Breakout")  # the fake breakout is what the hold withheld
    assert (
        _breakouts(card, sid, step_day) == []
    )  # nothing that reads across the step is on the card

    # the SAME view without the hold fires the fake: a breakout at/after the step reading the split as a move
    monkeypatch.setattr(split_hold, "find_holds", lambda *a, **k: {})
    fake = _breakouts(assemble_from_pit(pit, thesis, night), sid, step_day)
    assert (
        fake and max(t.sources[0].detail["ret"] for t in fake) >= 2.0
    )  # +200% or more: the split, not a move


@pytest.mark.parametrize("night, tk, prior, step_day", HELD)
def test_not_held_when_the_split_was_already_confirmed_on_the_list(db, night, tk, prior, step_day):
    sid, tid = _seeded(db, tk, listed=True)
    (row,) = fx.split_rows(tk)
    assert row["status"] == "confirmed"  # the list's own status (auto: SEC + Polygon)
    thesis, pit = _view(db, tid, night)
    assert split_hold.find_holds(pit, thesis, night) == {}
    card = assemble_from_pit(pit, thesis, night)
    assert _held_lines(card, tk) == []
    # one scale: no breakout reads the split as a +200% move
    assert all(t.sources[0].detail["ret"] < 2.0 for t in _breakouts(card, sid, prior))


@pytest.mark.parametrize(
    "night, tk",
    [
        pytest.param(date(2025, 5, 19), "PTIX", id="PTIX-2025-05-19"),
        pytest.param(date(2026, 9, 22), "JAGX", id="JAGX-09-22"),
        pytest.param(date(2026, 9, 23), "JAGX", id="JAGX-09-23"),
    ],
)
@pytest.mark.parametrize("listed", [True, False], ids=["listed", "unlisted"])
def test_never_held_on_a_genuine_big_move(db, night, tk, listed):
    sid, tid = _seeded(db, tk, listed=listed)
    thesis, pit = _view(db, tid, night)
    bars = pit.price_history(sid, lookback_days=10)
    closes = [float(b["close"]) for b in bars]
    assert max(b / a for a, b in zip(closes, closes[1:])) >= 3  # a real x3+ day is on this tape
    assert split_hold.find_holds(pit, thesis, night) == {}
    assert _held_lines(assemble_from_pit(pit, thesis, night), tk) == []


def test_a_real_forward_split_is_held_and_names_polygons_row_once_it_is_public(db):
    """ZCSH's 1-for-3 (Polygon: first session 09-30; the vendor stepped on 09-29) — still needs review, so never
    in force: the step down to a third on rising volume holds both nights. On 09-29 the list's row is not yet
    PUBLIC (its first source is Polygon's 09-30 first session — the public clock, #1), so the card says no
    filing was found; on 09-30 it names Polygon's row."""
    sid, tid = _seeded(db, "ZCSH", listed=True)
    reasons = {}
    for night in (date(2026, 9, 29), date(2026, 9, 30)):
        thesis, pit = _view(db, tid, night)
        holds = split_hold.find_holds(pit, thesis, night)
        assert set(holds) == {sid}
        h = holds[sid]
        assert not h.step.up and h.step.d == date(2026, 9, 29) and h.step.step <= 1 / 3
        reasons[night] = h.reason
    assert reasons == {
        date(2026, 9, 29): (
            "possible unconfirmed split (the price stepped down to 1/3.1 on 09-29 against the volume; no "
            "split filing found yet)"
        ),
        date(2026, 9, 30): (
            "possible unconfirmed 3-for-1 forward split (listed by Polygon; waiting for a second source)"
        ),
    }


def test_the_escalation_three_sessions_on(db):
    """GAME 08-28: the hold released its 08-24 step (sessions 08-25, 08-26, 08-27 passed). Unlisted, the step
    is on the operator's "is this real?" list; listed, it is not (and the view reads one scale anyway).
    """
    asof = date(2026, 8, 28)
    sid, tid = _seeded(db, "GAME", listed=False)
    thesis, pit = _view(db, tid, asof)
    assert split_hold.find_holds(pit, thesis, asof) == {}  # released: no longer on the cards
    (j,) = scan_unlisted_jumps(db, asof=asof, tenant_id=DEFAULT_TENANT_ID)
    assert j.security_id == sid and j.ticker == "GAME"
    assert (j.step.prior_d, j.step.d, j.sessions_since) == (date(2026, 8, 21), date(2026, 8, 24), 4)
    assert j.label == f"GAME {j.step.describe()}"
    assert [d for d, _c, _v in j.after] == [
        date(2026, 8, 25),
        date(2026, 8, 26),
        date(2026, 8, 27),
        date(2026, 8, 28),
    ]


def test_no_escalation_when_the_split_is_listed(db):
    _seeded(db, "GAME", listed=True)
    assert scan_unlisted_jumps(db, asof=date(2026, 8, 28), tenant_id=DEFAULT_TENANT_ID) == ()


def test_the_hold_only_touches_the_held_member(db):
    """A basket with a held name and a genuine mover: only the held name's reading changes, and the genuine
    mover's triggers are the ones it would have had alone."""
    game, _ = _seeded(db, "GAME", listed=False)
    jagx = fx.seed(db, "JAGX", listed=True)
    tid = fx.seed_thesis(db, [("GAME", game), ("JAGX", jagx)], name="two names")
    night = date(2026, 8, 25)
    thesis, pit = _view(db, tid, night)
    assert set(split_hold.find_holds(pit, thesis, night)) == {game}
    card = _card(db, tid, night)
    assert [m for m in card.missing if " held: " in m and " on JAGX " in m] == []


@pytest.mark.parametrize("listed", [False, True], ids=["unlisted", "listed"])
def test_the_public_mirror_holds_exactly_like_postgres(db, tmp_path, listed):
    """ONE implementation: the backtest's public mirror (``export_snapshot(clock="public")``, DuckDB) runs the
    same seam over the same rows, so a replay holds the same night the same way — the same hold line, the
    same triggers."""
    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid, tid = _seeded(db, "GAME", listed=listed)
    night = date(2026, 8, 25)
    thesis, live = _view(db, tid, night)
    export_snapshot(db, tmp_path, clock="public")
    rep = ReplayPointInTimeData(connect_mirror(tmp_path), asof=night, known_at=live.known_at)
    a = assemble_from_pit(live, thesis, night)
    b = assemble_from_pit(rep, thesis, night)
    assert a.missing == b.missing
    assert bool(_held_lines(a, "GAME")) is (not listed)
    assert [(t.label, t.event_date) for t in a.triggers_fired] == [
        (t.label, t.event_date) for t in b.triggers_fired
    ]
    assert [(t.label, t.event_date) for t in a.risk_signals] == [
        (t.label, t.event_date) for t in b.risk_signals
    ]


def test_every_open_hold_has_its_line_and_co_members_share_one():
    """The lines' contract (direct inputs): a held member is on the card every night its hold is open — naming
    the triggers withheld, or "Price triggers" when none was — and co-members whose laggard confirmation read a
    held member's price share ONE line naming the held members."""
    import uuid

    from domain.split_hold import Hold, SplitStep
    from domain.thesis import BasketMember, Thesis

    a, b, c, d = (uuid.UUID(int=i) for i in (1, 2, 3, 4))
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="t",
        narrative="n",
        basket=[
            BasketMember(ticker=tk, role="m", security_id=s)
            for tk, s in (("SMTK", a), ("ARM", b), ("ALAB", c), ("BTAI", d))
        ],
    )
    step = SplitStep(date(2026, 8, 19), date(2026, 8, 20), 0.16, 5.15, 1_000_000, 50_000)
    holds = {a: Hold(step, "why A"), d: Hold(step, "why D")}
    held = {
        a: ["Breakout"],
        b: [split_hold.LAGGARD_TRIGGER],
        c: [split_hold.LAGGARD_TRIGGER],
        d: [],
    }
    assert split_hold.notes(thesis, holds, held) == [
        "Breakout on SMTK held: why A",
        "Price triggers on BTAI held: why D",
        "Laggard confirmation on ARM and ALAB held: it reads the price of SMTK and BTAI, held for a "
        "possible unconfirmed split",
    ]


def test_every_price_path_detector_declares_its_card_name():
    """A detector that reads the price path must declare ``price_trigger`` — or a new one would read across
    an unconfirmed split step unheld. The two insider detectors read the price tape only for a Form 4's
    day low (``split_hold.NOT_PRICE_PATH``): they fire on the filing, not on the path."""
    from domain.config import DEFAULT_CONFIG

    for det in registered_detectors():
        if "fact_price_eod" not in det.horizons(DEFAULT_CONFIG):
            assert det.price_trigger is None, det.name
            continue
        if det.name in split_hold.NOT_PRICE_PATH:
            assert det.price_trigger is None, det.name
            continue
        assert det.price_trigger, f"{det.name} reads the price path but declares no price_trigger"
