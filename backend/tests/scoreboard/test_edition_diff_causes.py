"""S7 — "what changed" fills each change's ``cause`` from what the automatic cut's trial PROVED, never a guess.

Pure (``CauseSet`` + ``diff_thesis``, hand-built timelines, no DB): a company-night or an episode in the
proven set carries the stored cause; any other change carries what the trial's scope implies — ``late_data``
on a night the rules trial re-tested and found unchanged, ``not_separated`` on a night it did not re-test (a
nightly page), ``data_repair`` for a repair trial (a repair or late data, which it cannot separate). No cause
set (any other pair of editions): every cause stays null. A run of nights folds only nights with one cause.
"""

from __future__ import annotations

from repositories.editions_repo import CauseSet
from scoreboard.diff import diff_thesis
from tests.scoreboard.test_edition_diff import TID, A, armed, d, page, scorer, watch


def _causes(*, rules=True, repair=False, nights=(), episodes=(), tested=(0, 3)) -> CauseSet:
    return CauseSet(
        rules=rules,
        repair=repair,
        tested={TID: (d(tested[0]), d(tested[1]))} if tested else {},
        nights={(TID, A, d(n)): c for n, c in nights},
        episodes={(TID, A, d(n)): c for n, c in episodes},
    )


# E arms A on nights 1..5; N arms it on none of them — five changed company-nights, one vanished episode.
BEFORE = [page(0, watch(A)), *(page(n, armed(A)) for n in range(1, 6))]
AFTER = [page(n, watch(A)) for n in range(0, 6)]


def _diff(causes):
    return diff_thesis(TID, BEFORE, AFTER, through=d(5), score=scorer(), causes=causes)


def test_a_rules_trial_proves_its_set_and_the_scope_names_the_rest():
    t = _diff(
        _causes(nights=[(1, "rule_change"), (2, "rule_change")], episodes=[(1, "rule_change")])
    )
    (co,) = t.companies
    by = {n.night: n.cause for n in co.nights}
    assert by == {
        d(1): "rule_change",  # proven by the trial
        d(2): "rule_change",
        d(3): "late_data",  # re-tested and unchanged there: the data arrived after the cut
        d(4): "not_separated",  # a night the trial did not re-test (a nightly page)
        d(5): "not_separated",
    }
    (ep,) = co.episodes
    assert ep.change == "vanished" and ep.cause == "rule_change"
    # runs fold only nights with the same cause
    assert [(r.first, r.last, r.cause) for r in co.runs] == [
        (d(1), d(2), "rule_change"),
        (d(3), d(3), "late_data"),
        (d(4), d(5), "not_separated"),
    ]


def test_an_unexplained_or_unseparated_proof_keeps_its_own_word():
    t = _diff(_causes(nights=[(1, "unexplained"), (2, "not_separated")]))
    by = {n.night: n.cause for n in t.companies[0].nights}
    assert (by[d(1)], by[d(2)]) == ("unexplained", "not_separated")


def test_a_repair_trial_cannot_separate_the_repair_from_late_data():
    t = _diff(_causes(rules=False, repair=True, tested=None))
    (co,) = t.companies
    assert {n.cause for n in co.nights} == {"data_repair"}
    assert co.episodes[0].cause == "data_repair"
    assert [(r.first, r.last) for r in co.runs] == [(d(1), d(5))]  # one cause, one run


def test_a_trial_with_both_events_says_not_separated_where_it_did_not_re_test():
    t = _diff(_causes(rules=True, repair=True, nights=[(1, "rule_change")]))
    by = {n.night: n.cause for n in t.companies[0].nights}
    assert by[d(1)] == "rule_change"
    assert by[d(2)] == by[d(3)] == "data_repair"  # re-tested, not the rules: a repair or late data
    assert by[d(4)] == by[d(5)] == "not_separated"


def test_no_cause_set_leaves_every_cause_null():
    t = _diff(None)
    (co,) = t.companies
    assert {n.cause for n in co.nights} == {None}
    assert co.episodes[0].cause is None and {r.cause for r in co.runs} == {None}
