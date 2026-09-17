"""F4 — the replay harness resolves each thesis's ROSTER point-in-time, per session.

Until now the harness replayed every T on the LIVE basket: it took a Thesis loaded by ``list_all``
(a plain ``SELECT * FROM basket_member``, no time axis) and reused it for the whole sweep. A member added
after T was still replayed at T — the same lookahead the record's F12 fix closed for serve / cron /
backfill, left open in the lab. ``basket_snapshot`` (0043) and ``thesis_repo.get_asof`` already existed, so
this was a WIRING gap.

READING (B), the operator's decision, is what these tests pin: the roster clock is PER-T,
``known_at = min(pin, known_at_for_asof(T))`` — the same market-day cap the serve path uses. The
alternative ("resolve the roster once, at the run's pin") would have been INERT for the shipped Scoreboard
panel, which pins ``now``: ``get_asof(now)`` is today's roster by construction, so the bug would have
survived the fix. ``test_a_member_added_by_a_later_snapshot_is_ABSENT_at_an_earlier_T`` is the test that
tells the two readings apart — it passes only under (B).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from psycopg.types.json import Json

from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from pipeline.seed import UNH_SECURITY_ID, UNH_THESIS_ID, seed_unh
from replay.export import export_snapshot
from replay.harness import ReplayResult, RosterSource, replay_all, replay_thesis_with_roster
from replay.pit import connect_mirror
from repositories import thesis_repo

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(2026, 6, 1)


def _snapshot(db, thesis_id, *, taken_at: datetime, members: list[dict]) -> None:
    """Append one ``basket_snapshot`` row by hand, so a test can place a roster change at a chosen instant
    (the repo writes them on promote, which no replay test performs)."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
            "VALUES (%s, %s, %s, %s, %s, md5(%s::text))",
            (
                uuid.uuid4(),
                DEFAULT_TENANT_ID,
                thesis_id,
                taken_at,
                Json(members),
                Json(members),
            ),
        )
    db.commit()


def _member(ordinal: int, ticker: str, security_id) -> dict:
    """One roster entry in the snapshot's jsonb shape (the key set ``_row_to_basket_member`` reads)."""
    return {
        "ordinal": ordinal,
        "ticker": ticker,
        "role": "core",
        "security_id": str(security_id),
        "detail": None,
        "segment": None,
        "thesis_fit": None,
        "conviction": None,
        "surfaced_terms": [],
        "authored_by": "system_drafted",
        "signed_off": False,
    }


def _securities_seen(snaps) -> set:
    """Every member SECURITY the replayed calls surfaced, across the whole timeline.

    ``MemberRow`` keys on ``security_id`` and a snapshot carries only the ARMED and WATCH members — so the
    observable "was this name in the basket at T" has to use a security that can actually reach one of
    those tiers. That is why these tests move UNH (the one richly-seeded arc) in and out of the roster
    rather than adding a factless stranger, which would be invisible either way and would make the test
    pass for the wrong reason."""
    return {m.security_id for s in snaps for m in s.members}


@pytest.mark.slow  # one UNH sweep, the cheap seed — sibling of test_unh_arc_replays_*
@pytest.mark.timeout(300)
def test_a_member_added_by_a_later_snapshot_is_ABSENT_at_an_earlier_T(db, tmp_path):
    """THE test that proves reading (B), and the one that fails under (A).

    Two snapshots for one thesis: the original roster taken BEFORE the window, and a second one — carrying
    an extra member — taken in the MIDDLE of it. Under a per-T clock the extra name is invisible on every
    session before that instant and present on every session after. Under a per-RUN clock (roster resolved
    once at the pin, which sits after both) it would be present on every session, including the first.
    """
    seed_unh(db)
    db.commit()
    thesis = thesis_repo.get(db, UNH_THESIS_ID)
    live = [_member(i, m.ticker, m.security_id) for i, m in enumerate(thesis.basket)]
    assert any(m.security_id == UNH_SECURITY_ID for m in thesis.basket)

    # UNH is the member ADDED mid-window: the early roster holds only a factless placeholder, the later
    # one holds the real (seeded) name. UNH is the added member precisely because it is the one that can
    # reach an armed/watch tier and therefore be SEEN — see `_securities_seen`.
    before_window = datetime(2025, 1, 1, tzinfo=timezone.utc)
    added_at = datetime(2025, 9, 1, 23, 59, tzinfo=timezone.utc)  # mid-window, after the UNH arm
    placeholder = _member(0, "PLACEHOLDER", uuid.uuid4())
    _snapshot(db, UNH_THESIS_ID, taken_at=before_window, members=[placeholder])
    _snapshot(db, UNH_THESIS_ID, taken_at=added_at, members=[placeholder, *live])

    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        snaps, source = replay_thesis_with_roster(
            con, thesis, start=_START, end=_END, known_at=_PIN, conn=db
        )
    finally:
        con.close()

    assert snaps, "the UNH window has trading sessions"
    cutoff = added_at.date()
    early = [s for s in snaps if s.asof < cutoff]
    late = [s for s in snaps if s.asof > cutoff]
    assert (
        early and late
    ), "the window must straddle the roster change for this test to mean anything"

    assert UNH_SECURITY_ID not in _securities_seen(early), (
        "a member added mid-window must be INVISIBLE at every earlier session — seeing it is the "
        "lookahead F4 closes, and is exactly what a per-RUN roster clock would produce"
    )
    assert UNH_SECURITY_ID in _securities_seen(
        late
    )  # ...and present once it really was in the basket
    # every session resolved a real snapshot, so nothing fell back
    assert source == RosterSource(source="snapshot", fallback_days=0, total_days=len(snaps))


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_pre_snapshot_fallback_is_TAKEN_and_LABELED(db, tmp_path):
    """A window that predates the snapshot table cannot produce a historical roster — no wiring can fix
    that, and replaying such a thesis with an EMPTY basket would be far worse (#9). So the live roster
    stands in, and the run SAYS SO: the fallback is counted per session and named in one sentence.

    The honest residual of F4 in production, stated as a test rather than a footnote: ``basket_snapshot``
    history begins 2026-09-15, so every session before that takes this path today."""
    seed_unh(db)
    db.commit()
    thesis = thesis_repo.get(db, UNH_THESIS_ID)
    # a snapshot that exists only AFTER the window — so no session has a qualifying one
    _snapshot(
        db,
        UNH_THESIS_ID,
        taken_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
        members=[_member(i, m.ticker, m.security_id) for i, m in enumerate(thesis.basket)],
    )

    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        snaps, source = replay_thesis_with_roster(
            con, thesis, start=_START, end=_END, known_at=_PIN, conn=db
        )
    finally:
        con.close()

    assert source.source == "live_fallback"
    assert source.fallback_days == source.total_days == len(snaps)
    assert snaps and _securities_seen(
        snaps
    ), "the fallback REPLAYS — it never blanks the basket (#9)"

    result = ReplayResult(timelines={UNH_THESIS_ID: snaps}, roster_sources={UNH_THESIS_ID: source})
    note = result.note()
    assert note is not None and "TODAY's basket" in note and str(len(snaps)) in note
    assert result.fallback_theses == 1


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_snapshot_taken_AFTER_the_roster_known_at_is_NEVER_read(db, tmp_path):
    """No lookahead on the roster axis (#1) — the mirror of ``test_pit_lookahead``'s transaction-time test,
    applied to membership. The later snapshot sits after the RUN's pin as well as after every session, so
    neither clock can reach it; the sweep must use the earlier roster throughout rather than the newest
    one that happens to exist."""
    seed_unh(db)
    db.commit()
    thesis = thesis_repo.get(db, UNH_THESIS_ID)
    original = [_member(i, m.ticker, m.security_id) for i, m in enumerate(thesis.basket)]
    future_only = [_member(0, "PLACEHOLDER", uuid.uuid4())]  # a roster WITHOUT the seeded name
    _snapshot(
        db, UNH_THESIS_ID, taken_at=datetime(2025, 1, 1, tzinfo=timezone.utc), members=original
    )
    _snapshot(
        db,
        UNH_THESIS_ID,
        taken_at=_PIN.replace(year=2028),  # strictly after the pin AND after every session
        members=future_only,
    )

    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        snaps, source = replay_thesis_with_roster(
            con, thesis, start=_START, end=_END, known_at=_PIN, conn=db
        )
    finally:
        con.close()

    # the unreachable future roster DROPS the seeded name; if it were read, UNH would vanish from the
    # timeline. It is present throughout, so only the earlier snapshot was ever used.
    assert UNH_SECURITY_ID in _securities_seen(snaps)
    assert source.fallback_days == 0  # the EARLIER snapshot qualified at every session


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_current_snapshot_at_pin_now_reproduces_the_PRE_F4_timeline(db, tmp_path):
    """THE REGRESSION GUARD. The shipped Scoreboard panel pins ``now`` over theses whose only snapshot is
    the deploy seed's (taken at migration time, i.e. after the whole replay window). F4 must not change
    what that panel shows — the fallback path has to reproduce the pre-F4 timeline exactly, or the fix
    would silently re-cut every episode on the live surface.

    Compared against a sweep with ``conn=None``, which IS the pre-F4 code path (the passed thesis's live
    roster, every session), value-for-value over the whole timeline."""
    seed_unh(db)
    db.commit()
    thesis = thesis_repo.get(db, UNH_THESIS_ID)
    _snapshot(
        db,
        UNH_THESIS_ID,
        taken_at=datetime.now(timezone.utc),  # the deploy-seed shape: taken today
        members=[_member(i, m.ticker, m.security_id) for i, m in enumerate(thesis.basket)],
    )

    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        pre_f4, _ = replay_thesis_with_roster(
            con, thesis, start=_START, end=_END, known_at=_PIN, conn=None
        )
        with_f4, source = replay_thesis_with_roster(
            con, thesis, start=_START, end=_END, known_at=_PIN, conn=db
        )
    finally:
        con.close()

    assert pre_f4, "the guard needs a non-empty timeline to be meaningful"
    assert [s.model_dump(mode="json") for s in with_f4] == [
        s.model_dump(mode="json") for s in pre_f4
    ], "F4 must not change what the live panel shows for a thesis with no historical snapshot"
    # ...and it says WHY it matched: the snapshot is newer than every session, so every day fell back
    assert source.source == "live_fallback"


def test_the_roster_clock_is_the_MARKET_day_cap_never_the_bare_pin():
    """The formula itself, stated once so a refactor cannot quietly swap it for ``known_at=pin``.

    ``known_at_for_asof(T, now=pin)`` IS ``min(pin, end of T's market day)`` — the same definition the
    serve path's scrub-back uses (INVARIANTS #4), not a second one invented here. Pure: no DB, no replay.
    """
    session = date(2025, 8, 15)
    far_pin = datetime(2027, 1, 1, tzinfo=timezone.utc)
    near_pin = datetime(2025, 8, 15, 6, 0, tzinfo=timezone.utc)  # BEFORE that day ends

    capped = known_at_for_asof(session, far_pin)
    assert capped < far_pin, "a far pin must be capped down to the session's own market day"
    assert capped.date() >= session  # the market day ends the evening of T (ET -> next UTC day)
    # a pin INSIDE the day wins over the day-end cap — the run's determinism pin is never exceeded
    assert known_at_for_asof(session, near_pin) == near_pin


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_replay_all_returns_the_timelines_AND_the_roster_labels(db, tmp_path):
    """``replay_all`` hands back a named pair, not a bare dict: every consumer that takes the timelines has
    the roster provenance in the same object and cannot drop it on the floor by accident."""
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        result = replay_all(db, con, start=_START, end=_END, known_at=_PIN)
    finally:
        con.close()

    assert isinstance(result, ReplayResult)
    assert set(result.timelines) == set(result.roster_sources)  # one label per replayed thesis
    assert UNH_THESIS_ID in result.timelines
    assert result.timelines[UNH_THESIS_ID]
    # the seeded thesis has no snapshot at all -> the fallback, counted and named
    assert result.roster_sources[UNH_THESIS_ID].source == "live_fallback"
    assert result.note() is not None
    assert UNH_SECURITY_ID  # the seed's ids are imported and real (guards a stale import)


# --- a thesis that replayed NOTHING makes no roster claim ----------------------------------------------
#
# MEASURED on the staged dev artifact (window 2026-06-15 -> 07-09, 12 theses): the banner announced "10 of
# 12 theses recomputed on TODAY's basket" although NO snapshot existed in June, so every thesis that
# actually replayed had fallen back. The two it excused had no basket members at all: zero sessions ->
# `fallback_days == 0` -> the old rule read that as "snapshot" and the ratio quietly understated the
# fallback. A vacuous zero is not a clean run, and a denominator that counts theses which never ran is not
# a denominator.


def _sources(*specs) -> dict:
    return {uuid.uuid4(): RosterSource(*spec) for spec in specs}


def test_a_thesis_with_NO_members_reports_no_sessions_not_snapshot(db, tmp_path):
    """The real shape from the dev artifact, end to end: a thesis whose basket is empty sweeps no sessions
    and must say so. Claiming "snapshot" would assert a point-in-time roster that was never read."""
    seed_unh(db)
    db.commit()
    empty = thesis_repo.get(db, UNH_THESIS_ID)
    empty.basket = []  # the "DEV" / "Modern Defense Buildout" shape: promoted, never populated

    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        snaps, source = replay_thesis_with_roster(
            con, empty, start=_START, end=_END, known_at=_PIN, conn=db
        )
    finally:
        con.close()

    assert snaps == []
    assert source == RosterSource(source="no_sessions", fallback_days=0, total_days=0)
    assert (
        source.source != "snapshot"
    ), "a vacuous zero must never read as a clean point-in-time run"


def test_snapshot_REQUIRES_having_actually_replayed_something():
    """The rule, stated directly on the three shapes so a refactor cannot quietly restore the old one."""
    assert RosterSource("no_sessions", 0, 0).source == "no_sessions"
    # ...and the constructor's callers are pinned by the end-to-end tests above; here we pin the meaning:
    result = ReplayResult(
        timelines={},
        roster_sources=_sources(
            ("snapshot", 0, 12), ("live_fallback", 12, 12), ("no_sessions", 0, 0)
        ),
    )
    assert result.replayed_theses == 2  # the no-session thesis is NOT a denominator
    assert result.fallback_theses == 1


def test_the_note_excludes_no_session_theses_from_BOTH_halves():
    """The staged-dev regression, pinned: 10 fell back, 2 never ran. The honest sentence is about the 10."""
    result = ReplayResult(
        timelines={},
        roster_sources=_sources(
            *([("live_fallback", 12, 12)] * 10), ("no_sessions", 0, 0), ("no_sessions", 0, 0)
        ),
    )

    note = result.note()
    assert note is not None
    assert "all 10 replayed theses" in note
    assert "10 of 12" not in note and "of 12" not in note  # the bug's exact wording, gone
    assert "120 session(s)" in note  # the day count sums only the theses that ran


def test_the_note_says_N_of_M_when_only_SOME_replayed_theses_fell_back():
    """The mixed case still reads as a ratio — "all" is reserved for when it is literally all of them."""
    result = ReplayResult(
        timelines={},
        roster_sources=_sources(
            ("live_fallback", 3, 12), ("snapshot", 0, 12), ("no_sessions", 0, 0)
        ),
    )
    assert "1 of 2 replayed theses" in result.note()


def test_a_run_where_every_REPLAYED_thesis_had_a_snapshot_says_nothing():
    """Silence is the clean case, and a no-session thesis must not break it into a false alarm."""
    result = ReplayResult(
        timelines={}, roster_sources=_sources(("snapshot", 0, 12), ("no_sessions", 0, 0))
    )
    assert result.note() is None
    assert result.fallback_theses == 0
