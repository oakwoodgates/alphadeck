from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from replay.metrics import compute_metrics
from replay.schema import Episode
from replay.scoring import score_episode
from scoreboard.prices import PgRealizedPrices
from tests.scoreboard.helpers import bar

# The EXIT read (A1). ``score_episode`` takes the episode's exit from the last bar of its SCORED
# WINDOW ``[arm_date, exit_by]`` — not, as it once did, from the last bar anywhere <= exit_by, a read
# unbounded BELOW. The two formulations agree on every episode whose window holds a bar; they diverge
# only when the last bar <= exit_by precedes arm_date, and there the old read paired a later entry
# with an earlier exit and produced a return measured BACKWARDS in time.
#
# That shape is real, not hypothetical: two episodes on the live record arm on a Sunday with exit_by
# the same Sunday (``market_today()`` does no weekend skip by design, so a Sunday backfill records a
# Sunday as-of and ``derive_episodes`` takes card as-ofs as episode boundaries). One of the two was
# inside ``arm_timing_forward_return`` / ``false_arm_rate`` as an adverse arm.


def _reader(db, cap, known_at=None):
    return PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap, known_at=known_at)


def _episode(security_id, arm: date, exit_by: date, *, dearm: date | None = None) -> Episode:
    return Episode(
        thesis_id=uuid.uuid4(),
        security_id=security_id,
        is_headline=True,
        arm_date=arm,
        last_armed_date=dearm or exit_by,
        dearm_date=dearm,
        close_reason="window_end" if dearm is None else "dearmed_other",
        exit_by=exit_by,
    )


# --- T10: the inverted window produces NO signed return ----------------------------------------------


def test_inverted_window_is_not_scored(db, security_id):
    """The real instance: arm_date and exit_by are the SAME Sunday, so the scored window contains no
    trading day at all. The entry read (unbounded above) still finds Monday's close; the old exit read
    (unbounded below) found the PRECEDING Friday's, and the quotient of the two was a signed return
    running backwards through time — +4.17% here, where the name actually fell.

    FAILS BEFORE THE FIX, which produced exactly that number. The point of the assertion is not that
    some particular figure is wrong: it is that no signed return is produced AT ALL from a window with
    nothing in it."""
    bar(db, security_id, date(2026, 8, 14), 100.0)  # Friday — the old exit
    bar(db, security_id, date(2026, 8, 17), 96.0)  # Monday — the entry

    sunday = date(2026, 8, 16)
    out = score_episode(
        _episode(security_id, sunday, sunday, dearm=date(2026, 8, 17)),
        _reader(db, cap=date(2026, 9, 10)),
    )

    assert out.insufficient_prices is True
    assert out.forward_return is None
    assert out.exit_close is None and out.exit_date is None
    # the entry is still reported — it is a real close, and the row should say what it knows (#6)
    assert out.entry_close == 96.0
    # and the specific corruption, pinned by its shape rather than its value: nothing on this Outcome
    # may pair an exit that precedes its own entry.
    assert out.exit_date is None or out.exit_date >= out.arm_date


def test_inverted_window_leaves_the_metrics(db, security_id):
    """The corrupt observation reached the metrics through ``_ok()`` (which admits any Outcome with a
    non-null ``forward_return``). With no ``forward_return`` it no longer can. This is the whole of the
    measured metrics movement — one deleted backwards observation, never a re-weighting.

    FAILS BEFORE THE FIX (the episode counted, and counted as an adverse arm)."""
    bar(db, security_id, date(2026, 8, 14), 100.0)
    bar(db, security_id, date(2026, 8, 17), 96.0)

    sunday = date(2026, 8, 16)
    out = score_episode(_episode(security_id, sunday, sunday), _reader(db, cap=date(2026, 9, 10)))
    m = {r.name: r for r in compute_metrics([out]).metrics}
    assert m["arm_timing_forward_return"].n == 0
    assert m["false_arm_rate"].n == 0


# --- T11: the exit IS the window's last bar — surgical, one shape only -------------------------------


def test_exit_is_the_windows_last_bar_not_the_last_bar_anywhere(db, security_id):
    """The two halves of the surgical claim, on one tape.

    (a) A NORMAL episode, where both formulations agree: the last bar <= exit_by is inside the window,
    so the exit is unchanged. PASSES BEFORE THE FIX — a regression guard, stated as one.

    (b) The SAME tape, scored over a window that holds no bar while the tape RESUMES after exit_by —
    the only shape in which the two reads can differ. The entry read (unbounded above) finds 06-15;
    the old exit read (unbounded below) found 06-09, six days EARLIER, and divided one by the other.
    FAILS BEFORE THE FIX, where it returned a signed -16.5%.

    The gap between the two halves is the whole claim: one tape, two episodes, and only the one whose
    window is empty moves."""
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 5), 110.0)
    bar(db, security_id, date(2026, 6, 9), 121.0)
    bar(db, security_id, date(2026, 6, 15), 145.0)  # the tape resumes AFTER (b)'s horizon
    reader = _reader(db, cap=date(2026, 6, 30))

    # (a) the window holds bars; the last bar <= exit_by (06-09) is one of them
    normal = score_episode(_episode(security_id, date(2026, 6, 1), date(2026, 6, 12)), reader)
    assert normal.exit_date == date(2026, 6, 9) and normal.exit_close == 121.0
    assert normal.forward_return == pytest.approx(0.21)
    assert normal.insufficient_prices is False

    # (b) the window [06-11, 06-12] is empty, but a later bar exists — so the entry read still finds
    # one and only the EXIT read decides whether a return is manufactured out of nothing
    inverted = score_episode(_episode(security_id, date(2026, 6, 11), date(2026, 6, 12)), reader)
    assert reader.first_close_on_or_after(security_id, date(2026, 6, 11)) == (
        date(2026, 6, 15),
        145.0,
    )
    assert reader.last_close_through(security_id, date(2026, 6, 12)) == (date(2026, 6, 9), 121.0)
    assert inverted.entry_close == 145.0  # the entry is real and still reported
    assert inverted.exit_date is None  # ...but the 06-09 bar is NOT this episode's exit
    assert inverted.forward_return is None and inverted.insufficient_prices is True


def test_a_single_bar_window_is_its_own_entry_and_exit(db, security_id):
    """The degenerate-but-valid boundary between T10 and T11: exactly one bar in the window. It is both
    the entry and the exit, the return is a real (if mechanical) 0.0%, and nothing is insufficient —
    the empty-window branch must not swallow it. PASSES BEFORE THE FIX; it pins that the new
    ``if not window`` guard is a test for EMPTY, never for short."""
    bar(db, security_id, date(2026, 6, 1), 100.0)
    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 3)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    assert out.insufficient_prices is False
    assert out.entry_close == 100.0 and out.exit_close == 100.0
    assert out.exit_date == date(2026, 6, 1) == out.arm_date
    assert out.forward_return == 0.0
