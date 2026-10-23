"""Editions S6 — "what changed" between two editions, the PURE core (``scoreboard.diff.diff_thesis`` /
``count``) over hand-built snapshot timelines: no database, no clock, a fake scorer.

What it pins: a company-night is listed only when the armed state differs, or — armed in both — the verdict,
grades or exit-by differ; a night with no page reads that edition's last page before it (a gap is never a
change); consecutive identical changes fold into one run; episodes pair on (company, arm date), then
"moved" over overlapping windows, else appeared / vanished, and an unpaired arm on a night only one edition
covers is "outside" (counted, never a change); the comparison ignores every page after ``through``; ``cause``
stays null; the headline counts add up.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from domain.enums import Grade, State, Verdict
from replay.schema import CallSnapshot, Episode, MemberRow, Outcome
from scoreboard.diff import count, diff_thesis

TID = uuid.UUID(int=0xD1)
A = uuid.UUID(int=0xA1)
B = uuid.UUID(int=0xB1)
C = uuid.UUID(int=0xC1)


def d(day: int) -> date:
    return date(2026, 7, 1) + timedelta(days=day)


def armed(
    sid: uuid.UUID,
    *,
    exit_by: date | None = None,
    entry: Grade = Grade.CORE,
    verdict: Verdict = Verdict.CORE_ENTRY,
) -> MemberRow:
    return MemberRow(
        security_id=sid,
        tier="armed",
        verdict=verdict,
        entry_grade=entry,
        conviction_grade=Grade.CORE,
        exit_by=exit_by or d(60),
    )


def watch(sid: uuid.UUID) -> MemberRow:
    return MemberRow(security_id=sid, tier="watch", verdict=Verdict.WATCHING)


def page(day: int, *members: MemberRow, state: State | None = None) -> CallSnapshot:
    any_armed = any(m.tier == "armed" for m in members)
    return CallSnapshot(
        thesis_id=TID,
        asof=d(day),
        state=state or (State.ARMED if any_armed else State.WARMING),
        verdict=Verdict.CORE_ENTRY if any_armed else Verdict.NOT_YET,
        members=list(members),
    )


def scorer(returns: dict[tuple[uuid.UUID, date], float] | None = None):
    """A fake scorer: the forward return keyed by (company, arm date), default 0.10; exit_by shifts it so a
    matched pair with a different horizon scores differently (as the real tape would)."""
    calls: list[Episode] = []

    def score(e: Episode) -> Outcome:
        calls.append(e)
        base = (returns or {}).get((e.security_id, e.arm_date), 0.10)
        bump = ((e.exit_by - d(60)).days / 100.0) if e.exit_by else 0.0
        return Outcome(
            thesis_id=e.thesis_id,
            security_id=e.security_id,
            is_headline=e.is_headline,
            arm_date=e.arm_date,
            exit_by=e.exit_by,
            forward_return=base + bump,
        )

    score.calls = calls  # type: ignore[attr-defined]
    return score


def _company(diff, sid):
    return next(c for c in diff.companies if c.security_id == sid)


def test_identical_editions_list_nothing_and_count_the_unchanged():
    tl = [page(0, watch(A)), page(1, armed(A)), page(2, armed(A)), page(3, watch(A))]
    out = diff_thesis(TID, tl, list(tl), through=d(3), score=scorer())
    assert out.companies == []
    assert (out.first_night, out.last_night, out.n_nights) == (d(0), d(3), 4)
    assert out.n_matched == 1 and out.n_companies_unchanged == 1
    assert out.n_state_nights == 0 and out.before_only == [] and out.after_only == []
    c = count([out])
    assert c.n_theses == 1 and c.n_theses_changed == 0 and c.n_company_nights_changed == 0


def test_a_company_armed_in_one_edition_only_is_listed_per_night_and_folded_into_one_run():
    before = [page(0), page(1), page(2), page(3), page(4)]
    after = [page(0), page(1, armed(B)), page(2, armed(B)), page(3, armed(B)), page(4)]
    out = diff_thesis(TID, before, after, through=d(4), score=scorer())
    co = _company(out, B)
    assert [n.night for n in co.nights] == [d(1), d(2), d(3)]
    assert all(n.changed == ["armed"] and n.cause is None for n in co.nights)
    assert all(not n.before.armed and n.after.armed for n in co.nights)
    # an un-armed read carries no call fields; the armed read carries the call
    first = co.nights[0]
    assert (first.before.verdict, first.before.exit_by) == (None, None)
    assert (first.after.verdict, first.after.entry_grade) == ("core_entry", "core")
    [run] = co.runs
    assert (run.first, run.last, run.n_nights, run.changed) == (d(1), d(3), 3, ["armed"])
    # the episode appeared in the later edition only
    [ep] = co.episodes
    assert ep.change == "appeared" and ep.before is None and ep.cause is None
    assert ep.after.arm_date == d(1) and ep.after.dearm_date == d(4)
    assert ep.after.forward_return == 0.10
    assert out.n_state_nights == 3  # warming vs armed on those three nights
    c = count([out])
    assert (c.n_company_nights_changed, c.n_armed_after_only, c.n_armed_before_only) == (3, 3, 0)
    assert (c.n_episodes_appeared, c.n_theses_changed, c.n_companies_changed) == (1, 1, 1)


def test_both_armed_with_a_different_grade_lists_the_fields_and_splits_runs_on_a_new_value():
    before = [page(i, armed(A)) for i in range(4)]
    after = [
        page(0, armed(A)),
        page(1, armed(A, entry=Grade.FLIP)),
        page(2, armed(A, entry=Grade.FLIP)),
        page(3, armed(A, entry=Grade.FLIP, exit_by=d(62))),
    ]
    out = diff_thesis(TID, before, after, through=d(3), score=scorer())
    co = _company(out, A)
    assert [(n.night, n.changed) for n in co.nights] == [
        (d(1), ["entry_grade"]),
        (d(2), ["entry_grade"]),
        (d(3), ["entry_grade", "exit_by"]),
    ]
    assert [(r.first, r.last, r.n_nights) for r in co.runs] == [(d(1), d(2), 2), (d(3), d(3), 1)]
    assert co.episodes == []  # the arm itself is the same run, captured identically AT the arm
    assert count([out]).n_fields_changed == 3


def test_a_night_with_no_page_reads_the_editions_last_page_so_a_gap_is_never_a_change():
    # the record skipped night 2 (a cron gap); the re-run has it. The record's night-1 page stands.
    before = [page(0, armed(A)), page(1, armed(A)), page(3, armed(A))]
    after = [page(i, armed(A)) for i in range(4)]
    out = diff_thesis(TID, before, after, through=d(3), score=scorer())
    assert out.companies == [] and out.n_nights == 4
    # ...and when the carried page DIFFERS, the night is listed with the page it was read from
    after2 = [page(0, armed(A)), page(1, armed(A)), page(2), page(3, armed(A))]
    out2 = diff_thesis(TID, before, after2, through=d(3), score=scorer())
    [n] = _company(out2, A).nights
    assert (n.night, n.before.page, n.after.page, n.changed) == (d(2), d(1), d(2), ["armed"])


def test_runs_break_on_a_compared_night_in_between():
    # armed only in the later edition on nights 1 and 3; night 2 agrees -> two runs, never one
    before = [page(i) for i in range(5)]
    after = [page(0), page(1, armed(B)), page(2), page(3, armed(B)), page(4)]
    co = _company(diff_thesis(TID, before, after, through=d(4), score=scorer()), B)
    assert [(r.first, r.last) for r in co.runs] == [(d(1), d(1)), (d(3), d(3))]


def test_episodes_pair_matched_moved_appeared_and_vanished():
    # A: the same arm date, but the later edition's horizon is longer -> a matched pair that differs
    # B: armed day 2 before, day 4 after (overlapping windows) -> moved, two days later
    # C: armed only before (vanished)
    before = [
        page(0),
        page(1, armed(A)),
        page(2, armed(A), armed(B)),
        page(3, armed(A), armed(B), armed(C)),
        page(4, armed(A), armed(B), armed(C)),
        page(5),
    ]
    after = [
        page(0),
        page(1, armed(A, exit_by=d(65))),
        page(2, armed(A, exit_by=d(65))),
        page(3, armed(A, exit_by=d(65))),
        page(4, armed(A, exit_by=d(65)), armed(B)),
        page(5),
    ]
    score = scorer({(B, d(2)): -0.05, (B, d(4)): 0.08})
    out = diff_thesis(TID, before, after, through=d(5), score=score)
    a_eps = _company(out, A).episodes
    [m] = a_eps
    assert m.change == "matched" and m.arm_shift_days == 0
    assert m.differs == ["exit_by", "forward_return"]
    assert round(m.return_delta, 6) == 0.05  # the 5-day longer horizon, on the fake tape
    [mv] = _company(out, B).episodes
    assert (mv.change, mv.before.arm_date, mv.after.arm_date, mv.arm_shift_days) == (
        "moved",
        d(2),
        d(4),
        2,
    )
    assert round(mv.return_delta, 6) == 0.13
    [v] = _company(out, C).episodes
    assert v.change == "vanished" and v.after is None and v.before.arm_date == d(3)
    c = count([out])
    assert (c.n_episodes_matched, c.n_episodes_matched_changed) == (1, 1)
    assert (c.n_episodes_moved, c.n_episodes_vanished, c.n_episodes_appeared) == (1, 1, 0)


def test_a_matched_episode_that_is_identical_is_counted_not_listed():
    tl = [page(0), page(1, armed(A)), page(2, armed(A)), page(3)]
    out = diff_thesis(TID, tl, list(tl), through=d(3), score=scorer())
    assert out.companies == [] and out.n_matched == 1
    assert count([out]).n_episodes_matched_changed == 0


def test_far_apart_arms_of_one_company_are_not_moved():
    # the windows never overlap (exit-by 3 days after each arm), so one vanished + one appeared
    before = [page(0), page(1, armed(A, exit_by=d(4))), page(2)] + [page(i) for i in range(3, 20)]
    after = [page(i) for i in range(0, 15)] + [page(15, armed(A, exit_by=d(18)))]
    after += [page(i) for i in range(16, 20)]
    eps = _company(diff_thesis(TID, before, after, through=d(19), score=scorer()), A).episodes
    assert sorted(e.change for e in eps) == ["appeared", "vanished"]


def test_nights_only_one_edition_covers_are_spans_and_their_arms_are_outside():
    # the later edition began 3 nights earlier (the record had not begun) and armed A there
    before = [page(3, armed(A)), page(4, armed(A)), page(5)]
    after = [page(0), page(1, armed(B)), page(2), page(3, armed(A)), page(4, armed(A)), page(5)]
    out = diff_thesis(TID, before, after, through=d(5), score=scorer())
    assert (out.first_night, out.last_night, out.n_nights) == (d(3), d(5), 3)
    assert out.before_only == []
    assert [(s.first, s.last, s.n_nights) for s in out.after_only] == [(d(0), d(2), 3)]
    assert out.n_outside == 1  # B's arm on night 1: the earlier edition could not have made it
    assert out.companies == []  # A matches; B is not a change
    assert count([out]).n_episodes_outside == 1


def test_an_arm_straddling_the_window_start_pairs_as_moved_and_says_the_earlier_began_mid_arm():
    # the record began on night 3 with A already armed (its first page); the re-run armed A on night 1
    before = [page(3, armed(A)), page(4, armed(A)), page(5)]
    after = [page(0), page(1, armed(A)), page(2, armed(A)), page(3, armed(A))]
    after += [page(4, armed(A)), page(5)]
    [mv] = _company(diff_thesis(TID, before, after, through=d(5), score=scorer()), A).episodes
    assert mv.change == "moved" and mv.arm_shift_days == -2
    assert mv.before.began_mid_arm is True and mv.after.began_mid_arm is False


def test_an_unpaired_run_that_armed_before_the_window_but_reaches_into_it_is_a_change():
    # the record began on night 3 and never arms A; the re-run armed A on night 1 and holds it to night 4
    before = [page(3), page(4), page(5)]
    after = [page(0), page(1, armed(A)), page(2, armed(A)), page(3, armed(A)), page(4, armed(A))]
    after += [page(5)]
    out = diff_thesis(TID, before, after, through=d(5), score=scorer())
    co = _company(out, A)
    [ep] = co.episodes
    assert (ep.change, ep.armed_before_window, ep.after.arm_date) == ("appeared", True, d(1))
    assert [n.night for n in co.nights] == [d(3), d(4)]  # the nights both cover say the same thing
    assert out.n_outside == 0  # it reaches the compared nights, so it is a change, not "outside"
    assert count([out]).n_episodes_appeared == 1


def test_a_field_that_did_not_change_never_splits_a_run():
    # armed only in the later edition, whose exit-by moves night to night: still ONE "armed" run
    before = [page(i) for i in range(4)]
    after = [page(0)] + [page(i, armed(B, exit_by=d(60 + i))) for i in (1, 2, 3)]
    [run] = _company(diff_thesis(TID, before, after, through=d(3), score=scorer()), B).runs
    assert (run.first, run.last, run.n_nights, run.changed) == (d(1), d(3), 3, ["armed"])


def test_one_edition_with_no_page_for_the_thesis_compares_nothing():
    after = [page(0, armed(A)), page(1, armed(A))]
    out = diff_thesis(TID, [], after, through=d(1), score=scorer())
    assert (out.first_night, out.n_nights, out.companies) == (None, 0, [])
    assert [(s.first, s.last) for s in out.after_only] == [(d(0), d(1))]
    assert out.n_outside == 1


def test_every_page_after_through_is_ignored():
    # the later edition arms C on night 4, after the comparison's last night (3): never seen, never scored
    before = [page(i) for i in range(4)]
    after = [page(i) for i in range(4)] + [page(4, armed(C)), page(5, armed(C))]
    score = scorer()
    out = diff_thesis(TID, before, after, through=d(3), score=score)
    assert out.companies == [] and out.last_night == d(3) and out.after_only == []
    assert score.calls == []  # type: ignore[attr-defined]
    # through one night later, the same arm is a change
    out2 = diff_thesis(TID, before + [page(4)], after, through=d(4), score=scorer())
    assert [e.change for e in _company(out2, C).episodes] == ["appeared"]


def test_a_watch_row_is_not_an_arm():
    before = [page(0, watch(A)), page(1, watch(A))]
    after = [page(0), page(1)]
    out = diff_thesis(TID, before, after, through=d(1), score=scorer())
    assert out.companies == [] and out.n_companies_unchanged == 0
