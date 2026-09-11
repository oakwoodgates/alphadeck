from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from db.session import DEFAULT_TENANT_ID
from replay.metrics import compute_metrics
from replay.schema import Episode
from replay.scoring import score_episode
from scoreboard.prices import PgRealizedPrices
from tests.scoreboard.helpers import bar

# ``truncated`` = the episode's horizon extends past the end of THIS NAME's price tape
# (``exit_by > tape_edge``), read within the reader's own as-of cap. One condition, where the old
# ``exit_date < exit_by`` was four wearing one name: still running, a non-trading-day exit_by, a dead
# tape, and a stale per-name ingest it could not see at all.
#
# The fixtures below never name a holiday or a weekday. That is the point of the rule: the tape
# answers the calendar question by itself, so a test for it only ever has to omit a bar.


def _reader(db, cap, known_at=None):
    return PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap, known_at=known_at)


def _market(db, d: date, *, close: float = 50.0) -> uuid.UUID:
    """A bar on some OTHER name, so the tenant's MARKET edge can sit past a starved name's own.

    Every fixture here that expects ``tape_behind_market`` needs one: the market edge is a max over
    the whole tenant, so in a single-name fixture the name IS the market and the second leg can never
    be satisfied. That is a property of the rule rather than a workaround -- a name can only be
    "behind the market" if there is a market to be behind, and a lone security is its own market."""
    other = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (other, DEFAULT_TENANT_ID, f"MKT{other.hex[:5].upper()}", other.hex[:10], "2026-01-01"),
        )
    db.commit()
    bar(db, other, d, close)
    return other


def _episode(security_id, arm: date, exit_by: date) -> Episode:
    return Episode(
        thesis_id=uuid.uuid4(),
        security_id=security_id,
        is_headline=True,
        arm_date=arm,
        last_armed_date=exit_by,
        dearm_date=None,
        close_reason="window_end",
        exit_by=exit_by,
    )


# --- T1 / T2: the two false-positive classes, 20 of 20 matured fires on the real record -------------


def test_weekend_exit_by_is_not_truncated(db, security_id):
    """``exit_by`` on a Saturday, with the tape running Mon-Fri before and after. Nothing was missed:
    there is no Saturday close and no later bar will ever change the measured number, so the horizon
    WAS covered and the flag must be false. 17 of the 20 matured fires on the record are this shape.

    FAILS BEFORE THE FIX (``exit_date`` 2026-06-12 < ``exit_by`` 2026-06-13 read as truncated)."""
    for d, px in [(8, 100.0), (9, 101.0), (10, 103.0), (11, 102.0), (12, 105.0)]:  # Mon-Fri
        bar(db, security_id, date(2026, 6, d), px)
    bar(db, security_id, date(2026, 6, 15), 106.0)  # the following Monday — the tape lives on

    out = score_episode(
        _episode(security_id, date(2026, 6, 8), date(2026, 6, 13)),  # exit_by = Saturday
        _reader(db, cap=date(2026, 7, 1)),
    )
    assert out.truncated is False
    assert out.exit_date == date(2026, 6, 12)  # the Friday — still the measurement anchor
    assert out.exit_close == 105.0 and out.forward_return == pytest.approx(0.05)


def test_a_missing_bar_inside_the_week_is_not_truncated_either(db, security_id):
    """The holiday class, proved WITHOUT a calendar. ``exit_by`` falls on a Monday that simply has no
    bar, and the tape resumes on the Tuesday. The fixture never says "holiday" — it just omits a bar,
    which is the whole argument for asking the tape instead of importing a US market calendar that
    nothing would fail on when it went stale. 3 of the 20 matured fires are this shape (Labor Day).

    FAILS BEFORE THE FIX."""
    bar(db, security_id, date(2026, 9, 3), 100.0)
    bar(db, security_id, date(2026, 9, 4), 104.0)  # Friday
    # 2026-09-07 — no bar at all
    bar(db, security_id, date(2026, 9, 8), 106.0)  # the tape resumes
    bar(db, security_id, date(2026, 9, 9), 107.0)

    out = score_episode(
        _episode(security_id, date(2026, 9, 3), date(2026, 9, 7)),
        _reader(db, cap=date(2026, 9, 30)),
    )
    assert out.truncated is False
    assert out.exit_date == date(2026, 9, 4)


# --- T3 / T4: the flag's real job, and the running case that must not move ---------------------------


def test_a_dead_tape_is_still_truncated(db, security_id):
    """The class the flag was always documented for and had never once fired on: the tape stops and
    the horizon runs past its end. PASSES BEFORE THE FIX — a regression guard, stated as one. It pins
    that correcting the 20 false positives did not quietly delete the feature."""
    bar(db, security_id, date(2026, 7, 13), 100.0)
    bar(db, security_id, date(2026, 7, 20), 92.0)  # ...and the tape stops here
    _market(db, date(2026, 8, 28))  # the rest of the market keeps printing

    out = score_episode(
        _episode(security_id, date(2026, 7, 13), date(2026, 8, 15)),
        _reader(db, cap=date(2026, 9, 1)),
    )
    assert out.truncated is True
    assert out.tape_behind_market is True  # the market printed past 08-15; this name did not
    assert out.exit_date == date(2026, 7, 20)
    assert out.forward_return is not None  # a realized-looking number, correctly caveated


def test_an_immature_episode_is_still_truncated(db, security_id):
    """A horizon beyond the as-of. PASSES BEFORE THE FIX — a regression guard. It is load-bearing in a
    way a "just make it false more often" fix would break: `moveNote`, `peakTimingPhrase` and the
    chart's "last bar" exit marker all anchor their phrasing on this staying true while an episode
    runs. It holds under the new rule for a DIFFERENT reason than before — the as-of caps the tape
    edge too, so a running episode's horizon is past its (capped) tape edge by construction.

    This is also the test that fails if the market leg is ever folded INTO ``truncated`` rather than
    carried beside it: ``market_edge <= asof < exit_by`` for every running episode, so the merged
    version reads false here and the three phrasing sites start calling a running episode's last bar
    its horizon. ``tape_behind_market`` is the flag that correctly stays false."""
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 5), 110.0)
    bar(db, security_id, date(2026, 6, 20), 200.0)  # past the cap, and must stay invisible
    _market(db, date(2026, 6, 9))

    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 7, 1)),
        _reader(db, cap=date(2026, 6, 10)),
    )
    assert out.truncated is True
    assert out.tape_behind_market is False  # nothing has printed past 07-01 — it has not happened
    assert out.exit_date == date(2026, 6, 5)


# --- T5: no-lookahead on the NEW read ----------------------------------------------------------------


def test_tape_edge_respects_the_asof_cap(db, security_id):
    """THE test in this file. A bar exists at 2026-08-20, past the cap of 2026-08-01; the tape as of
    that cap stops at 2026-07-20 and the horizon is 2026-08-15. The out-of-cap bar must not be allowed
    to prove the horizon was covered.

    Its "before" state, measured rather than assumed: the FIRST half (``truncated is True`` under the
    2026-08-01 cap) PASSES TRIVIALLY under the old rule, which never touched the tape at all — and
    that is exactly why it has to exist after, since an implementation reaching for an uncapped
    ``max(d)``, or hand-writing an ``ORDER BY d DESC LIMIT 1`` and forgetting a cap, passes every
    other assertion in this file while quietly breaking invariant #1 in a change that looks like a
    display fix. The SECOND half — the same episode reading false once the cap moves past the bar —
    FAILS under the old rule, and it is what makes the first half worth anything: without it, a
    ``tape_edge`` that returned a constant ``None`` would satisfy the assertion above.

    Mirrors ``test_scoreboard_asof_scrub_is_point_in_time``'s discipline; the transaction axis gets the
    same treatment below."""
    bar(db, security_id, date(2026, 7, 13), 100.0)
    bar(db, security_id, date(2026, 7, 20), 92.0)
    bar(db, security_id, date(2026, 8, 20), 130.0)  # beyond the cap — the leak this catches

    reader = _reader(db, cap=date(2026, 8, 1))
    assert reader.tape_edge(security_id, date(2026, 7, 13)) == date(2026, 7, 20)

    out = score_episode(_episode(security_id, date(2026, 7, 13), date(2026, 8, 15)), reader)
    assert out.truncated is True

    # ...and with the cap moved past that bar, the SAME episode reads false — proving the assertion
    # above is the cap doing the work, not the fixture happening to lack a bar.
    later = _reader(db, cap=date(2026, 9, 1))
    assert later.tape_edge(security_id, date(2026, 7, 13)) == date(2026, 8, 20)
    assert (
        score_episode(_episode(security_id, date(2026, 7, 13), date(2026, 8, 15)), later).truncated
        is False
    )


def test_tape_edge_respects_the_known_at_cap(db, security_id):
    """The TRANSACTION axis, which the valid-time test above cannot cover. A bar whose ``recorded_at``
    postdates ``known_at`` was not knowable at that moment and must not extend the tape edge — the same
    double cap ``_closes`` applies, inherited rather than re-implemented.

    CANNOT RUN before the fix — ``tape_edge`` does not exist. Its ``truncated`` assertion would have
    passed under the old rule for an unrelated reason (the exit bar precedes the horizon either way),
    so the ``tape_edge`` probes are the load-bearing half."""
    bar(
        db,
        security_id,
        date(2026, 7, 13),
        100.0,
        recorded_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )
    bar(
        db,
        security_id,
        date(2026, 7, 20),
        92.0,
        recorded_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    # a bar for an EARLIER-dated day, ingested late — visible on the valid axis, not yet on the
    # transaction axis at the known_at below
    bar(
        db,
        security_id,
        date(2026, 8, 12),
        95.0,
        recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    pinned = _reader(db, cap=date(2026, 9, 1), known_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert pinned.tape_edge(security_id, date(2026, 7, 13)) == date(2026, 7, 20)
    assert (
        score_episode(_episode(security_id, date(2026, 7, 13), date(2026, 8, 15)), pinned).truncated
        is True
    )

    now = _reader(db, cap=date(2026, 9, 1))
    assert now.tape_edge(security_id, date(2026, 7, 13)) == date(2026, 8, 12)


# --- the MARKET leg: tape_behind_market, the badge's loudness condition -------------------------------
#
# `truncated` states a fact about one name's tape. `tape_behind_market` adds "and the market printed
# past this horizon anyway", which is what makes the fact worth shouting. The four cases below are
# the ones the rule was designed against, plus the boundary and the no-lookahead guard.


def test_same_day_maturity_is_not_flagged(db, security_id):
    """THE transient this leg exists for. An episode matures TODAY and today's close does not exist
    until after the bell, so its horizon is trivially past every name's tape edge — on a completely
    healthy feed. Un-suppressed, the badge would flicker on same-day maturities for part of every
    single day, and a mark that fires on rows that are fine stops being believed, which is exactly
    how the previous rule died.

    `truncated` is still TRUE here and that is correct: the measurement genuinely has not reached the
    horizon yet. It is only the loudness that is withheld."""
    bar(db, security_id, date(2026, 9, 8), 100.0)
    bar(db, security_id, date(2026, 9, 10), 104.0)
    _market(db, date(2026, 9, 10))  # the market is fresh; nobody has printed 09-11 yet

    out = score_episode(
        _episode(security_id, date(2026, 9, 8), date(2026, 9, 11)),
        _reader(db, cap=date(2026, 9, 11)),
    )
    assert out.truncated is True  # the fact, unchanged
    assert out.tape_behind_market is False  # the badge, suppressed
    assert out.exit_date == date(2026, 9, 10)


def test_a_globally_stalled_feed_flags_nobody(db, security_id):
    """A frozen cron (or a dev stack with the cron off): the WHOLE tape stops days before the
    horizon, not just this name's. No single name is starved, so no single row should be marked —
    that alarm is the record-freshness line's, and 200 identical badges would be a feed-health
    warning wearing a per-name costume (#7).

    This is the shape MEASURED on dev, where the price feed stopped at 2026-09-08 while episodes
    matured on 09-11."""
    bar(db, security_id, date(2026, 8, 26), 100.0)
    bar(db, security_id, date(2026, 9, 8), 91.0)
    _market(db, date(2026, 9, 8))  # ...and so did everyone else

    out = score_episode(
        _episode(security_id, date(2026, 8, 26), date(2026, 9, 11)),
        _reader(db, cap=date(2026, 9, 11)),
    )
    assert out.truncated is True
    assert out.tape_behind_market is False


def test_a_dead_tape_still_flags_while_the_market_runs_on(db, security_id):
    """The REGRESSION that matters most: the new condition must not silence the real signal. This is
    the constructed dead-tape case, in the shape measured against the dev database (a name whose tape
    ends 2026-06-10 while the market runs to 2026-09-08).

    If this ever goes false, the whole flag is decorative."""
    for d in (date(2026, 4, 13), date(2026, 5, 15), date(2026, 6, 10)):
        bar(db, security_id, d, 100.0)
    _market(db, date(2026, 9, 8))

    out = score_episode(
        _episode(security_id, date(2026, 4, 13), date(2026, 7, 1)),
        _reader(db, cap=date(2026, 9, 10)),
    )
    assert out.truncated is True
    assert out.tape_behind_market is True
    assert out.exit_date == date(2026, 6, 10)


def test_a_starved_name_with_a_past_horizon_flags(db, security_id):
    """The other true positive: not dead, just behind. The name's tape stops 2026-07-16, the horizon
    is 2026-09-01, the market has reached 2026-09-10."""
    bar(db, security_id, date(2026, 6, 30), 100.0)
    bar(db, security_id, date(2026, 7, 16), 88.0)
    _market(db, date(2026, 9, 10))

    out = score_episode(
        _episode(security_id, date(2026, 6, 30), date(2026, 9, 1)),
        _reader(db, cap=date(2026, 9, 10)),
    )
    assert out.truncated is True and out.tape_behind_market is True


def test_market_edge_equal_to_exit_by_is_suppressed(db, security_id):
    """The BOUNDARY, pinned so the choice stays visible rather than accidental.

    The market leg is strict (``exit_by < market_edge``). A starved name whose horizon lands EXACTLY
    on the market's latest bar date is therefore suppressed for that day, even though the market did
    print on that horizon and this name did not — arguably a true positive withheld. It is not lost:
    the moment the market edge advances past the horizon (the next trading day) the row flags, as the
    second half of this test shows.

    The trade is one trading day of latency on a true positive, in exchange for immunity to the
    same-day flicker a ``<=`` would reintroduce during an ingest batch. Deliberate, and cheap to
    revisit -- it is this one comparison."""
    bar(db, security_id, date(2026, 6, 30), 100.0)
    bar(db, security_id, date(2026, 7, 16), 88.0)
    _market(db, date(2026, 9, 1))  # the market's last bar IS the horizon

    ep = _episode(security_id, date(2026, 6, 30), date(2026, 9, 1))
    out = score_episode(ep, _reader(db, cap=date(2026, 9, 10)))
    assert out.truncated is True
    assert out.tape_behind_market is False  # suppressed at the boundary

    _market(db, date(2026, 9, 2))  # one more trading day, and the same episode flags
    assert score_episode(ep, _reader(db, cap=date(2026, 9, 10))).tape_behind_market is True


def test_market_edge_respects_both_asof_caps(db, security_id):
    """No-lookahead on the NEW read, both axes — the sibling of the ``tape_edge`` cap tests, and
    needed for the same reason: the market edge is a fresh ``max(d)`` over the whole tenant, which is
    precisely the shape where a cap gets forgotten. A bar past the as-of, or one recorded after
    ``known_at``, must not be allowed to establish that the market printed past a horizon.

    Without this, an uncapped market read would make the badge fire on scrubbed-back views using
    knowledge from after the date being viewed. Every bar here carries an EXPLICIT ``recorded_at``,
    because the helper's default is wall-clock now and a pinned ``known_at`` would otherwise hide the
    whole fixture rather than the one bar under test."""
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    bar(db, security_id, date(2026, 6, 30), 100.0, recorded_at=t0)
    bar(
        db,
        security_id,
        date(2026, 7, 16),
        88.0,
        recorded_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    other = _market(db, date(2026, 8, 20))
    bar(db, other, date(2026, 8, 20), 50.0, recorded_at=datetime(2026, 8, 21, tzinfo=timezone.utc))

    # VALID axis: capped at 08-01, the 08-20 market bar is invisible, so the market edge is 07-16 and
    # an episode with a 08-01 horizon cannot be shown to be behind a market that has not got there.
    capped = _reader(db, cap=date(2026, 8, 1))
    assert capped.market_tape_edge(security_id) == date(2026, 7, 16)
    assert (
        score_episode(
            _episode(security_id, date(2026, 6, 30), date(2026, 8, 1)), capped
        ).tape_behind_market
        is False
    )
    # ...and with the cap moved past that bar, the SAME episode flags — the cap is doing the work
    later = _reader(db, cap=date(2026, 9, 10))
    assert later.market_tape_edge(security_id) == date(2026, 8, 20)
    assert (
        score_episode(
            _episode(security_id, date(2026, 6, 30), date(2026, 8, 1)), later
        ).tape_behind_market
        is True
    )

    # TRANSACTION axis: a market bar dated 09-09 but RECORDED 09-05 was not knowable on 09-01, so it
    # must not extend the market edge for a reader pinned there — while one pinned after it does see
    # it. BOTH readers pin `known_at` explicitly: leaving one to default to wall-clock `now()` would
    # make the assertion depend on the day the suite runs.
    bar(db, other, date(2026, 9, 9), 55.0, recorded_at=datetime(2026, 9, 5, tzinfo=timezone.utc))
    before = _reader(db, cap=date(2026, 9, 30), known_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    after = _reader(db, cap=date(2026, 9, 30), known_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    assert before.market_tape_edge(security_id) == date(2026, 8, 20)
    assert after.market_tape_edge(security_id) == date(2026, 9, 9)


def test_market_edge_is_read_once_per_reader_not_once_per_episode(db, security_id):
    """The market edge costs a seq scan (no ``(tenant_id, d)`` index), so it must not become a
    per-episode query — the Board/Cockpit per-row-read mistake. Two guards in one test: a reader
    resolves it at most once and caches it, and a reader handed a pre-resolved value never queries at
    all (the threaded request path, where it is resolved once per tenant in ``scoreboard_records``).
    """
    bar(db, security_id, date(2026, 6, 30), 100.0)
    _market(db, date(2026, 9, 8))

    # (a) resolved at most once: after the first call the connection is removed, so a second read
    # would raise rather than quietly re-query.
    lazy = _reader(db, cap=date(2026, 9, 10))
    first = lazy.market_tape_edge(security_id)
    assert first == date(2026, 9, 8)
    lazy.conn = None
    assert lazy.market_tape_edge(security_id) == first

    # (b) a pre-resolved value (the threaded request path) is used verbatim and never queried for
    threaded = PgRealizedPrices(
        db, tenant_id=DEFAULT_TENANT_ID, cap=date(2026, 9, 10), market_edge=date(2026, 1, 1)
    )
    threaded.conn = None
    assert threaded.market_tape_edge(security_id) == date(2026, 1, 1)


# --- T12: the inverted episode never reads "cleanly realized with no mark" ---------------------------


def test_the_inverted_episode_is_never_a_clean_realized_row(db, security_id):
    """The regression that makes the exit-read fix and this one a single change.

    Under the OLD ``truncated`` the two Sunday-window episodes were flagged (``exit_date`` 08-14 <
    ``exit_by`` 08-16), so the badge put SOME mark on a corrupt row. Under the tape-edge rule the tape
    runs well past 08-16, so ``truncated`` would go false — stripping the only mark off a backwards-
    measured return and presenting it as cleanly realized. Shipped alone, this change would have made
    that row look BETTER while leaving it wrong.

    It does not, because the exit-read fix (previous commit) means there is no return there to
    flatter. This test asserts the joint property directly: whatever the flag says, the row serves no
    signed return.

    FAILS without the exit-read fix — with EITHER version of ``truncated``, since the first assertion
    is about the return and not the flag. That is the honest statement of the dependency: this is the
    test that would not let the ``truncated`` change ship on its own."""
    bar(db, security_id, date(2026, 8, 14), 100.0)
    bar(db, security_id, date(2026, 8, 17), 96.0)
    bar(db, security_id, date(2026, 9, 8), 99.0)  # the tape is alive long past the horizon

    sunday = date(2026, 8, 16)
    out = score_episode(_episode(security_id, sunday, sunday), _reader(db, cap=date(2026, 9, 10)))

    assert out.forward_return is None and out.insufficient_prices is True
    # the badge's own guard is `matured && truncated && !insufficient_prices` — this row can never
    # satisfy it, and it can never present a number either. Both halves, together.
    assert not (out.truncated and not out.insufficient_prices)


# --- T7: the metrics population does not move, in EITHER direction ----------------------------------


def test_truncated_gates_no_metric(db, security_id):
    """``truncated`` is not a metric input and must not become one by accident (Option F by refactor
    rather than by decision). Asserted in both directions on one tape: the corrected weekend episode
    counts, and so does a genuinely dead-tape one — the flag changes what the row SAYS, never whether
    it is measured.

    Measured "before" state, split honestly because the two halves differ. The METRIC claim (both
    n == 2) PASSES BEFORE THE FIX — it is a behavioral pin against a future refactor wiring the flag
    into eligibility, and it was already true. The precondition line above it (that the two episodes
    disagree on the flag at all) FAILS before, because the old rule flagged both — which is the same
    finding from the other side: under the old rule there was no pair of episodes on one tape that
    the flag could tell apart."""
    for d, px in [(8, 100.0), (9, 101.0), (10, 103.0), (11, 102.0), (12, 105.0)]:
        bar(db, security_id, date(2026, 6, d), px)
    bar(db, security_id, date(2026, 6, 15), 106.0)
    reader = _reader(db, cap=date(2026, 7, 1))

    weekend = score_episode(_episode(security_id, date(2026, 6, 8), date(2026, 6, 13)), reader)
    dead = score_episode(_episode(security_id, date(2026, 6, 8), date(2026, 6, 30)), reader)
    assert weekend.truncated is False and dead.truncated is True  # the flag DOES differ...

    m = {r.name: r for r in compute_metrics([weekend, dead]).metrics}
    assert m["arm_timing_forward_return"].n == 2  # ...and the population does not
    assert m["false_arm_rate"].n == 2
