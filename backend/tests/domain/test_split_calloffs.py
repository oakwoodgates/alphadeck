"""F10 P2: the ONE call-off matcher (``domain/split_calloffs``) and the evidence it reads (``domain.splits.CallOff``
on ``SecEvidence.calloff``). Pure, so every input is stated here. The two shapes are the listed splits the measure's
R1 flagged over 37,812 cached split filings (``docs/temp/framework-completion-evidence/f10-measure``):
AIRI (right: the company called its 1-for-5 off the day before its first session) and ATCH (wrong: a failed vote on
a NEW proposal, filed days after its listed 1-for-60 took effect).

The warning and the demote are the same function: the warning under the rule the settings give (``any`` ratio,
first session + 30 days, the measured forms), the demote under ``demote_rule`` (the same stated ratio, filed on or
before the first session).
"""

from __future__ import annotations

import ast
import dataclasses
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from domain import split_calloffs
from domain.split_calloffs import (
    CALLED_OFF_PASSAGE_MAX,
    RATIO_ANY,
    RATIO_SAME,
    CalloffRule,
    called_off,
    calls_off,
    demote_rule,
    demoting,
    refuse_called_off,
    states_ratio,
)
from domain.splits import (
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_REVOKED,
    VIA_AUTO,
    VIA_POLYGON_TAPE,
    VIA_SEC_TAPE,
    CallOff,
    PolygonEvidence,
    SecEvidence,
    TapeEvidence,
    cluster_sec,
    decide,
    decide_sec_only,
    with_tape,
)

FORMS = ("8-K", "8-K/A", "6-K", "6-K/A")  # the measured forms (plan §4, ``split_calloff_forms``)
WARN = CalloffRule(forms=FORMS, after_days=30, ratio=RATIO_ANY)  # R1: the warning's defaults
SAME = CalloffRule(forms=FORMS, after_days=30, ratio=RATIO_SAME)  # R2
DEMOTE = demote_rule(FORMS)
URL = "https://www.sec.gov/Archives/edgar/data/x/y/z-index.htm"

# AIRI: listed 1-for-5 (5 -> 1), first session 2026-11-20, announced by an 8-K filed 2026-11-10; its 8-K
# 0001213900-26-122096 (filed 2026-11-19) calls it off (``f10-measure/hits_table.md``)
AIRI = dict(
    ratio_from=Decimal(5),
    ratio_to=Decimal(1),
    first_session=date(2026, 11, 20),
    announced_on=date(2026, 11, 10),
)
AIRI_PASSAGE = (
    "On November 19, 2026, Air Industries Group, announced that it would not be effecting the reverse stock split "
    "(the “Reverse Stock Split”) of its authorized, issued and outstanding shares of common stock, par value $0.001 "
    "per share, at a ratio of 1-for-5"
)
# ATCH: listed 1-for-60 (60 -> 1) with no SEC filing on the row (announced_on = its valid_from, a Massive.com-only
# row's first session), first session 2025-01-02; its 8-K 0001104659-25-001831 (filed 2025-01-08) reports a failed
# vote on a NEW 1-for-3 range proposal in a sentence that states no ratio
ATCH = dict(
    ratio_from=Decimal(60),
    ratio_to=Decimal(1),
    first_session=date(2025, 1, 2),
    announced_on=date(2025, 1, 2),
)
ATCH_PASSAGE = "The Reverse Stock Split Proposal was not approved."


def airi_8k(**kw) -> SecEvidence:
    """122096 as the reader will carry it (P4): the call-off with its one stated ratio."""
    s = SecEvidence(
        accession="0001213900-26-122096",
        form="8-K",
        filed=date(2026, 11, 19),
        url=URL,
        abstain_reason="called off: decision (1-for-5 stated)",
        about_a_split=True,
        calloff=CallOff(
            kind="decision",
            ratio_from=Decimal(5),
            ratio_to=Decimal(1),
            passage=AIRI_PASSAGE,
            document="8-K",
        ),
    )
    return replace(s, **kw)


def atch_8k(**kw) -> SecEvidence:
    """001831: a failed vote, no ratio in the call-off sentence; the filing itself reads the 1-for-60."""
    s = SecEvidence(
        accession="0001104659-25-001831",
        form="8-K",
        filed=date(2025, 1, 8),
        url=URL,
        ratio_from=Decimal(60),
        ratio_to=Decimal(1),
        effective_date=date(2024, 12, 31),
        date_kind="legal",
        about_a_split=True,
        calloff=CallOff(kind="failed_vote", passage=ATCH_PASSAGE, document="8-K"),
    )
    return replace(s, **kw)


def with_calloff_ratio(s: SecEvidence, ratio_from: int | None, ratio_to: int | None) -> SecEvidence:
    assert s.calloff is not None
    rf = None if ratio_from is None else Decimal(ratio_from)
    rt = None if ratio_to is None else Decimal(ratio_to)
    return replace(s, calloff=replace(s.calloff, ratio_from=rf, ratio_to=rt))


# --- the two measured shapes -------------------------------------------------------------------------------


def test_the_airi_shape_warns_and_demotes():
    s = airi_8k()
    assert calls_off(s, **AIRI, rule=WARN)
    assert calls_off(s, **AIRI, rule=SAME)
    # the same stated ratio, filed the day before the first session
    assert calls_off(s, **AIRI, rule=DEMOTE)
    assert called_off([s], **AIRI, rule=WARN) == [s]
    assert called_off([s], **AIRI, rule=DEMOTE) == [s]


def test_the_atch_shape_warns_but_never_demotes():
    s = atch_8k()
    assert calls_off(s, **ATCH, rule=WARN)  # R1's wrong flag: a warning costs the steward a glance
    assert not calls_off(s, **ATCH, rule=SAME)  # the call-off states no ratio
    assert not calls_off(s, **ATCH, rule=DEMOTE)
    # each of the demote's two conditions refuses it alone: no ratio, even on the first session itself ...
    assert not calls_off(s, **{**ATCH, "first_session": s.filed}, rule=DEMOTE)
    # ... and the listed ratio stated, but filed after the first session
    same = with_calloff_ratio(s, 60, 1)
    assert calls_off(same, **ATCH, rule=SAME)
    assert not calls_off(same, **ATCH, rule=DEMOTE)
    assert called_off([s], **ATCH, rule=DEMOTE) == []


def test_a_different_stated_ratio_warns_under_any_but_not_under_same():
    # ATCH's failed proposal was a 1-for-3: had its call-off sentence stated it, it is still not the listed 1-for-60
    s = with_calloff_ratio(atch_8k(), 3, 1)
    assert calls_off(s, **ATCH, rule=WARN)
    assert not calls_off(s, **ATCH, rule=SAME)
    # and before AIRI's first session, a call-off of another ratio never demotes it
    other = with_calloff_ratio(airi_8k(), 10, 1)
    assert calls_off(other, **AIRI, rule=WARN)
    assert not calls_off(other, **AIRI, rule=DEMOTE)


def test_same_compares_exact_fractions():
    assert calls_off(with_calloff_ratio(airi_8k(), 10, 2), **AIRI, rule=SAME)  # 10 -> 2 is 5 -> 1
    s = replace(airi_8k(), calloff=replace(airi_8k().calloff, ratio_from=Decimal("5.01")))
    assert not calls_off(s, **AIRI, rule=SAME)


def test_one_side_of_a_ratio_is_no_stated_ratio():
    s = with_calloff_ratio(airi_8k(), 5, None)
    assert not s.calloff.has_ratio
    assert calls_off(s, **AIRI, rule=WARN)
    assert not calls_off(s, **AIRI, rule=SAME)


def test_states_ratio_is_the_one_exact_ratio_test():
    """Does a call-off state exactly the listed ratio (as fractions)? Public: the matcher's own ``same`` test, and
    the one any other reader of a call-off's ratio asks (one place per concern)."""
    c = airi_8k().calloff
    assert states_ratio(c, Decimal(5), Decimal(1))
    assert states_ratio(c, Decimal(10), Decimal(2))  # 10 -> 2 is 5 -> 1
    assert not states_ratio(c, Decimal("5.01"), Decimal(1))
    assert not states_ratio(c, Decimal(10), Decimal(1))
    assert not states_ratio(
        replace(c, ratio_to=None), Decimal(5), Decimal(1)
    )  # one side is no ratio
    assert not states_ratio(
        atch_8k().calloff, Decimal(60), Decimal(1)
    )  # the failed vote states none
    assert not states_ratio(None, Decimal(5), Decimal(1))  # no call-off at all


def test_the_matcher_reads_the_ratio_through_states_ratio(monkeypatch):
    asked: list[tuple] = []
    real = split_calloffs.states_ratio

    def spy(c, ratio_from, ratio_to):
        asked.append((c, ratio_from, ratio_to))
        return real(c, ratio_from, ratio_to)

    monkeypatch.setattr(split_calloffs, "states_ratio", spy)
    s = airi_8k()
    assert calls_off(s, **AIRI, rule=SAME)
    assert asked == [(s.calloff, Decimal(5), Decimal(1))]
    asked.clear()
    assert called_off([s], **AIRI, rule=WARN) == [
        s
    ]  # ``any`` matches without it; the order reads it
    assert asked == [(s.calloff, Decimal(5), Decimal(1))]
    assert not hasattr(split_calloffs, "_states_ratio")  # one name for the one test


# --- the window, the forms, the call-off itself -----------------------------------------------------------


def test_the_window_runs_from_the_announcement_to_first_session_plus_after_days_both_ends_in():
    first, announced = AIRI["first_session"], AIRI["announced_on"]
    for filed, warn in [
        (announced - timedelta(days=1), False),  # before the split was announced: an earlier plan
        (announced, True),
        (first, True),
        (first + timedelta(days=30), True),
        (first + timedelta(days=31), False),  # after first session + 30: a later plan
    ]:
        assert calls_off(airi_8k(filed=filed), **AIRI, rule=WARN) is warn, filed
    # the demote's window ends at the first session itself
    assert calls_off(airi_8k(filed=first), **AIRI, rule=DEMOTE)
    assert not calls_off(airi_8k(filed=first + timedelta(days=1)), **AIRI, rule=DEMOTE)
    assert not calls_off(airi_8k(filed=announced - timedelta(days=1)), **AIRI, rule=DEMOTE)
    # the reach is the rule's
    late = airi_8k(filed=first + timedelta(days=31))
    assert calls_off(late, **AIRI, rule=replace(WARN, after_days=31))


def test_only_the_rules_forms_count():
    assert not calls_off(airi_8k(form="10-Q"), **AIRI, rule=WARN)
    assert not calls_off(airi_8k(form="10-Q"), **AIRI, rule=DEMOTE)
    assert not calls_off(airi_8k(form="DEF 14A"), **AIRI, rule=WARN)
    for form in FORMS:
        assert calls_off(airi_8k(form=form), **AIRI, rule=WARN), form
    # the forms are the rule's (a setting), never the matcher's
    assert calls_off(airi_8k(form="10-Q"), **AIRI, rule=replace(WARN, forms=("10-Q",)))
    assert not calls_off(airi_8k(), **AIRI, rule=replace(WARN, forms=("10-Q",)))


def test_a_filing_without_a_call_off_never_matches():
    # the filing states the listed split, in the window, on an 8-K: without call-off wording it calls nothing off
    s = airi_8k(calloff=None, ratio_from=Decimal(5), ratio_to=Decimal(1), abstain_reason=None)
    for rule in (WARN, SAME, DEMOTE):
        assert not calls_off(s, **AIRI, rule=rule)
        assert called_off([s], **AIRI, rule=rule) == []


def test_called_off_returns_the_matches_same_ratio_first():
    ratio_less = airi_8k(accession="0000000000-26-000001", filed=date(2026, 11, 12))
    ratio_less = with_calloff_ratio(ratio_less, None, None)
    other_ratio = airi_8k(accession="0000000000-26-000002", filed=date(2026, 11, 11))
    other_ratio = with_calloff_ratio(other_ratio, 10, 1)
    same_late = airi_8k(accession="0000000000-26-000004", filed=date(2026, 11, 25))
    same_early = airi_8k(accession="0000000000-26-000003", filed=date(2026, 11, 19))
    quarterly = airi_8k(accession="0000000000-26-000005", form="10-Q")
    plain = airi_8k(accession="0000000000-26-000006", calloff=None)
    filings = [plain, ratio_less, same_late, quarterly, other_ratio, same_early]
    # same ratio first, then by filing date (and accession)
    warned = [same_early, same_late, other_ratio, ratio_less]
    assert called_off(filings, **AIRI, rule=WARN) == warned
    assert called_off(filings, **AIRI, rule=SAME) == [same_early, same_late]
    # 11-25 is after the first session
    assert called_off(filings, **AIRI, rule=DEMOTE) == [same_early]
    assert called_off([], **AIRI, rule=WARN) == []
    # a tie on the date is ordered by accession, whatever the input order
    twin = replace(same_early, accession="0000000000-26-000000")
    assert called_off([same_early, twin], **AIRI, rule=SAME) == [twin, same_early]


# --- the rule ------------------------------------------------------------------------------------------------


def test_the_demote_is_the_same_function_with_a_fixed_rule():
    assert DEMOTE == CalloffRule(forms=FORMS, after_days=0, ratio=RATIO_SAME)
    assert demote_rule(["6-K"]).forms == ("6-K",)  # the forms stay the caller's (the setting)


def test_a_rule_refuses_what_it_cannot_mean():
    with pytest.raises(ValueError):
        CalloffRule(forms=FORMS, after_days=30, ratio="close")
    with pytest.raises(ValueError):
        CalloffRule(forms=FORMS, after_days=-1, ratio=RATIO_ANY)
    with pytest.raises(TypeError):
        # a bare string is not a list of forms
        CalloffRule(forms="8-K", after_days=30, ratio=RATIO_ANY)
    assert CalloffRule(forms=["8-K"], after_days=0, ratio=RATIO_ANY).forms == ("8-K",)


def test_the_matcher_is_pure_and_reads_no_settings():
    """No clock, no settings, no I/O (like ``cluster_sec``): its one first-party import is ``domain.splits``."""
    path = Path(__file__).resolve().parents[2] / "domain" / "split_calloffs.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    first_party = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        first_party |= {
            n for n in names if n.split(".")[0] in ("domain", "db", "ingest", "pipeline", "app")
        }
    assert first_party == {"domain.splits"}
    calls = {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
    }
    assert not calls & {"date.today", "datetime.now", "datetime.utcnow", "time.time"}


# --- the evidence type: without a call-off, today's ---------------------------------------------------------

TODAYS_FIELDS = (
    "accession",
    "form",
    "filed",
    "url",
    "ratio_from",
    "ratio_to",
    "effective_date",
    "date_kind",
    "passage",
    "document",
    "abstain_reason",
    "date_note",
    "about_a_split",
)


def test_sec_evidence_without_a_call_off_equals_todays():
    names = tuple(f.name for f in dataclasses.fields(SecEvidence))
    # the one new field, last: positional calls are unchanged
    assert names == TODAYS_FIELDS + ("calloff",)
    s = SecEvidence("a", "8-K", date(2026, 11, 19), URL, Decimal(5), Decimal(1))
    assert s.calloff is None
    assert s == replace(s, calloff=None) and hash(s) == hash(replace(s, calloff=None))
    assert s != replace(s, calloff=CallOff(kind="decision"))
    c = CallOff(kind="failed_vote")
    assert (c.ratio_from, c.ratio_to, c.passage, c.document) == (None, None, "", None)
    assert not c.has_ratio
    assert CallOff(kind="decision", ratio_from=Decimal(5), ratio_to=Decimal(1)).has_ratio


def test_no_split_rule_reads_the_call_off():
    """The call-off is evidence beside the reading: ``decide``, ``cluster_sec``, ``decide_sec_only`` and
    ``with_tape`` decide the same with or without one (only the carried evidence differs)."""
    poly = PolygonEvidence(
        id="p",
        ticker="AIRI",
        execution_date=date(2026, 11, 20),
        split_from=Decimal(5),
        split_to=Decimal(1),
    )
    stating = SecEvidence(
        accession="0001213900-26-118055",
        form="8-K",
        filed=date(2026, 11, 10),
        url=URL,
        ratio_from=Decimal(5),
        ratio_to=Decimal(1),
        effective_date=date(2026, 11, 20),
        date_kind="trading",
        about_a_split=True,
    )
    plain = [stating, airi_8k(calloff=None), atch_8k(calloff=None)]
    marked = [replace(s, calloff=CallOff(kind="decision", passage="x")) for s in plain]

    def bare(d):
        return replace(d, sec=None if d.sec is None else replace(d.sec, calloff=None))

    for asof in (date(2026, 11, 19), date(2026, 11, 21)):
        assert bare(decide(poly, marked, asof=asof)) == decide(poly, plain, asof=asof)
        assert bare(decide(poly, marked[1:], asof=asof)) == decide(poly, plain[1:], asof=asof)
    unmarked = [[replace(s, calloff=None) for s in g] for g in cluster_sec(marked)]
    assert unmarked == cluster_sec(plain)
    d_marked = decide_sec_only([marked[0]], asof=date(2026, 11, 19))
    d_plain = decide_sec_only([plain[0]], asof=date(2026, 11, 19))
    assert bare(d_marked) == d_plain
    tape = TapeEvidence(  # a stated 1-for-5 step on falling volume (an input, not AIRI's tape)
        session=date(2026, 11, 20),
        prior=date(2026, 11, 19),
        prior_close=2.0,
        close=10.0,
        prior_volume=1000.0,
        volume=300.0,
        prior_served_before=True,
    )
    assert bare(with_tape(d_marked, tape)) == with_tape(d_plain, tape)


# --- P9: the demote at decision time (``refuse_called_off``; the switch, ``split_calloff_demote``, is the caller's) --
#
# The same matcher under ``demote_rule``: a decision that would be announced or confirmed goes to review when a filing
# read for it calls it off (the same stated ratio, filed from its announcement to its first session). Its note names
# the filing. Nothing else in the decision moves.

AIRI_ROW = PolygonEvidence(
    id="E-AIRI-2026-11-20",
    ticker="AIRI",
    execution_date=date(2026, 11, 20),
    split_from=Decimal(5),
    split_to=Decimal(1),
    adjustment_type="reverse_split",
)
ATCH_ROW = PolygonEvidence(
    id="E-ATCH-2025-01-02",
    ticker="ATCH",
    execution_date=date(2025, 1, 2),
    split_from=Decimal(60),
    split_to=Decimal(1),
    adjustment_type="reverse_split",
)
# a stated 1-for-5 step on falling volume on AIRI's first session (an input, not AIRI's tape: it never stepped)
STEP_TAPE = TapeEvidence(
    session=date(2026, 11, 20),
    prior=date(2026, 11, 19),
    prior_close=2.0,
    close=10.0,
    prior_volume=1000.0,
    volume=300.0,
    prior_served_before=True,
)


def airi_announcing(**kw) -> SecEvidence:
    """AIRI's 8-K announcing the 1-for-5, first trading session 2026-11-20 (``test_no_split_rule_reads_the_call_off``)."""
    s = SecEvidence(
        accession="0001213900-26-118055",
        form="8-K",
        filed=date(2026, 11, 10),
        url=URL,
        ratio_from=Decimal(5),
        ratio_to=Decimal(1),
        effective_date=date(2026, 11, 20),
        date_kind="trading",
        about_a_split=True,
    )
    return replace(s, **kw)


def _cut(passage: str) -> str:
    return passage[: CALLED_OFF_PASSAGE_MAX - 1].rstrip() + "\u2026"


AIRI_CALLED_OFF = (
    f"Called off: 8-K 0001213900-26-122096 (filed 2026-11-19) \u2014 {_cut(AIRI_PASSAGE)}"
)


def _only_review_moved(refused, d) -> bool:
    """Nothing but the status, the confirmation and the note moved; the note only gained the call-off."""
    rest = replace(refused, status=d.status, confirmed_via=d.confirmed_via, note=d.note)
    return (
        rest == d
        and refused.status == STATUS_NEEDS_REVIEW
        and refused.confirmed_via is None
        and refused.note.startswith(d.note + " ")
    )


def test_the_airi_shape_decided_on_its_first_session_night_goes_to_review_with_the_call_off_named():
    read = [airi_announcing(), airi_8k()]
    d = decide(AIRI_ROW, read, asof=date(2026, 11, 20))
    assert (d.status, d.confirmed_via, d.sec) == (STATUS_CONFIRMED, VIA_AUTO, read[0])  # today
    refused = refuse_called_off(d, read, FORMS)
    assert _only_review_moved(refused, d)
    assert refused.note == f"{d.note} {AIRI_CALLED_OFF}"
    assert len(_cut(AIRI_PASSAGE)) == CALLED_OFF_PASSAGE_MAX == 200
    # the night it was filed, before its first session: announced, refused the same way
    early = decide(AIRI_ROW, read, asof=date(2026, 11, 19))
    assert early.status == STATUS_ANNOUNCED
    assert refuse_called_off(early, read, FORMS).note == f"{early.note} {AIRI_CALLED_OFF}"
    assert _only_review_moved(refuse_called_off(early, read, FORMS), early)


def test_the_atch_shape_is_never_refused():
    """A failed vote stating no ratio, filed six days after the first session: confirmed it stays."""
    s = atch_8k()
    d = decide(ATCH_ROW, [s], asof=date(2025, 1, 8))
    assert (d.status, d.sec) == (STATUS_CONFIRMED, s)
    assert refuse_called_off(d, [s], FORMS) is d
    # the listed ratio stated, but filed after the first session: still never
    same = with_calloff_ratio(s, 60, 1)
    listed = decide(ATCH_ROW, [same], asof=date(2025, 1, 8))
    assert listed.status == STATUS_CONFIRMED
    assert refuse_called_off(listed, [same], FORMS) is listed


def test_the_tape_routes_are_refused_too():
    """Massive.com + tape (the call-off is the row's only filing, an abstain) and SEC + tape (the announcing 8-K's
    first trading session): each confirmed by the tape today, each refused."""
    poly_only = decide(AIRI_ROW, [airi_8k()], asof=date(2026, 11, 20))
    assert (poly_only.status, poly_only.sec) == (STATUS_NEEDS_REVIEW, airi_8k())
    by_tape = with_tape(poly_only, STEP_TAPE)
    assert (by_tape.status, by_tape.confirmed_via) == (STATUS_CONFIRMED, VIA_POLYGON_TAPE)
    refused = refuse_called_off(by_tape, [airi_8k()], FORMS)
    assert _only_review_moved(refused, by_tape) and refused.note.endswith(AIRI_CALLED_OFF)
    sec_only = with_tape(decide_sec_only([airi_announcing()], asof=date(2026, 11, 20)), STEP_TAPE)
    assert (sec_only.status, sec_only.confirmed_via) == (STATUS_CONFIRMED, VIA_SEC_TAPE)
    refused = refuse_called_off(sec_only, [airi_announcing(), airi_8k()], FORMS)
    assert _only_review_moved(refused, sec_only) and refused.note.endswith(AIRI_CALLED_OFF)


def test_only_a_live_decision_a_filing_calls_off_is_refused():
    read = [airi_announcing(), airi_8k()]
    d = decide(AIRI_ROW, read, asof=date(2026, 11, 20))
    for status in (STATUS_NEEDS_REVIEW, STATUS_REJECTED, STATUS_REVOKED):
        settled = replace(d, status=status, confirmed_via=None)
        assert refuse_called_off(settled, read, FORMS) is settled
    # no call-off read, another ratio called off, one filed after the first session, a form the caller left out
    for filings, forms in (
        ([airi_announcing()], FORMS),
        ([airi_announcing(), with_calloff_ratio(airi_8k(), 10, 1)], FORMS),
        ([airi_announcing(), airi_8k(filed=date(2026, 11, 21))], FORMS),
        ([airi_announcing(), with_calloff_ratio(airi_8k(), None, None)], FORMS),
        (read, ("6-K",)),
    ):
        assert refuse_called_off(d, filings, forms) is d


def test_the_demote_is_the_one_matcher_under_the_demote_rule(monkeypatch):
    """Never a second matcher: ``refuse_called_off`` asks ``called_off`` under ``demote_rule(forms)``, the decision
    mapped as its listed row will be (its ratio and first session, announced on its SEC filing's date, else its
    ``valid_from``; ``pipeline.detect_splits.listed_calloffs``)."""
    asked: list[dict] = []
    real = split_calloffs.called_off

    def spy(filings, **kw):
        asked.append(kw)
        return real(filings, **kw)

    monkeypatch.setattr(split_calloffs, "called_off", spy)
    read = [airi_announcing(), airi_8k()]
    d = decide(AIRI_ROW, read, asof=date(2026, 11, 20))
    assert demoting(d, read, FORMS) == [airi_8k()]
    assert refuse_called_off(d, read, FORMS).status == STATUS_NEEDS_REVIEW
    rule = dict(ratio_from=Decimal(5), ratio_to=Decimal(1), first_session=date(2026, 11, 20))
    assert asked == [{**rule, "announced_on": date(2026, 11, 10), "rule": DEMOTE}] * 2
    asked.clear()
    no_sec = replace(d, sec=None, valid_from=date(2026, 11, 18))
    assert demoting(no_sec, read, FORMS) == [airi_8k()]
    assert asked == [{**rule, "announced_on": date(2026, 11, 18), "rule": DEMOTE}]


def test_the_note_names_the_earliest_call_off_and_cuts_its_passage():
    read = [
        airi_announcing(),
        airi_8k(),
        airi_8k(accession="0001213900-26-120000", filed=date(2026, 11, 12)),
    ]
    d = decide(AIRI_ROW, read, asof=date(2026, 11, 20))
    refused = refuse_called_off(d, read, FORMS)
    assert refused.note.endswith(
        f"Called off: 8-K 0001213900-26-120000 (filed 2026-11-12) \u2014 {_cut(AIRI_PASSAGE)}"
    )
    short = airi_8k(calloff=replace(airi_8k().calloff, passage="  would not be\n effecting  it "))
    assert refuse_called_off(d, [short], FORMS).note.endswith(
        "Called off: 8-K 0001213900-26-122096 (filed 2026-11-19) \u2014 would not be effecting it"
    )
    bare = airi_8k(calloff=replace(airi_8k().calloff, passage=""))
    assert refuse_called_off(d, [bare], FORMS).note.endswith(
        "Called off: 8-K 0001213900-26-122096 (filed 2026-11-19)."
    )
