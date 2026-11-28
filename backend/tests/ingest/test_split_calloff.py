"""The call-off reader (F10 P1, ``ingest/edgar/split_calloff.py``): one sentence in, a call-off cue or nothing
out, with every word it reads in ``WORDING``.

The sentences are the filings' own, MEASURED 2026-11-28 on prod's cache
(``docs/temp/framework-completion-evidence/f10-measure``: ``hits_table.md`` and ``c2_sample.md`` name them,
``cls_full.jsonl`` holds the full sentence the measurement classified), except those marked "constructed".
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from pathlib import Path

import pytest

from ingest.edgar import split_calloff
from ingest.edgar.split_calloff import WORDING, CallOffCue, read_sentence

# AIRI's 8-K 0001213900-26-122096 (2026-11-19): it calls off the listed 1-for-5
AIRI = (
    "On November 19, 2026, Air Industries Group, announced that it would not be effecting the "
    "reverse stock split (the “Reverse Stock Split”) of its authorized, issued and outstanding "
    "shares of common stock, par value $0.001 per share, at a ratio of 1-for-5 (the “Reverse "
    "Stock Split”) in connection with the Company’s proposed merger with Tenax Aerospace "
    "Acquisition, LLC (“Tenax”) that had been announced on November 10, 2026."
)
# SNDL's 6-K 0001171843-26-006673 (EX-99.1): the consolidation resolution failed, so it will not proceed
SNDL = (
    "Accordingly, the Company will not proceed with the proposed share consolidation under the "
    "authority sought at the Meeting."
)
# IPSC's 8-K 0001104659-26-006236: no ratio stated
IPSC = (
    "As a result, the Company does not intend to effect the reverse stock split as approved by "
    "the stockholders at the Special Meeting."
)
# BNBX's 8-K 0001104659-26-082893
BNBX = (
    "On June 1, 2026, the Company informed the Panel that it would no longer be pursuing a "
    "reverse stock split to regain compliance with the $1.00 bid price requirement but instead "
    "the Company asked for an additional 60-day extension to organically regain compliance with "
    "the $1.00 bid price requirement as it made announcements in connection with the strategic "
    "review."
)
# ELBM's 6-K 0001171843-26-003616 (EX-99.1)
ELBM = (
    "On October 15, 2025, Shareholders approved a reverse split; however, that authorization has "
    "since expired and is no longer available to the Company."
)
# ATCH's 8-K 0001104659-25-001831: a failed vote (on a new 1-for-3; its listed 1-for-60 went ahead)
ATCH = "The Reverse Stock Split Proposal was not approved."
# SENS's 8-K 0001104659-25-002855: a meeting cancelled for a split it then did not do (a call-off)
SENS = (
    "On January 8, 2025, the Company announced its decision to cancel the Special Meeting "
    "of Stockholders, which had been scheduled for January 10, 2025, for the purpose of "
    "seeking stockholder approval of a proposed reverse stock split within the range of "
    "25:1 to 50:1."
)
# KNWN's 8-K 0001654954-25-001790 (EX-99.1): the same “decision to cancel the Special Meeting”, for a split
# that had already taken effect (the 1-for-40 listed on prod): no call-off
KNWN = (
    "The Company’s decision to cancel the Special Meeting comes after the Company "
    "announced a 1 for 40 reverse stock split of its authorized and issued and "
    "outstanding shares of common stock on February 18, 2025, which became effective "
    "February 19, 2025, and did not require the approval of stockholders."
)

# modal boilerplate (``c2_sample.md``): 6-K 0001213900-26-057079 (EX-99.1), "may … abandon"
MAY_ABANDON = (
    "Thus, subject to shareholder approval, the Board, at its discretion, may effect the Future "
    "Share Consolidations or abandon it and effect no Future Share Consolidation if it further "
    "determines that such action is not in the best interests of the Company and our "
    "shareholders."
)
# 6-K 0001493152-26-016362 (EX-99.2): a definite-looking cue under "may" and "if"
MAY_APPROVE = (
    "Although the Company’s shareholders may approve the Reverse Split, the Company will not "
    "effect the Reverse Split if the Board of Directors does not deem it to be in the best "
    "interests of the Company and its shareholders."
)
# the same exhibit: a definite-looking cue under "If … not approved"
IF_NOT_APPROVED = (
    "Potential Effects of Non-Approval of this Item If this Item No. 1 is not approved by the "
    "shareholders, the Reverse Split will not be implemented."
)
# constructed: no sentence of the 9,317 the measurement read says "will not be effected unless"
EFFECTED_UNLESS = "The Reverse Stock Split will not be effected unless the stockholders approve the Charter Amendment."
# 8-K 0001213900-26-028332 (``c2_sample.md``): a condition after the cue
EFFECTIVE_UNTIL = (
    "The Reverse Stock Split will not become effective until at least 20 calendar days after the "
    "mailing of the definitive Information Statement (the “ Waiting Period ”)."
)
# PBM's 6-K 0001213900-26-009093 (EX-99.1): a postponement, which is not read
PBM = (
    "As previously disclosed, the Company’s Board of Directors approved a reverse stock split at "
    "a ratio of 1-for-6.25, which was intended to become effective at the opening of the market "
    "on January 20, 2026, however, following further internal consideration, the Company "
    "yesterday announced that it had elected to delay the effectiveness of the reverse stock "
    "split to a later date."
)
# WETO's 6-K 0001213900-26-087326 (EX-99.1): an adjournment, which is not read
WETO = (
    "Given the outstanding nature of the filings relating to the Share Consolidation, the chair "
    "of that meeting presented a proposal to shareholders to pass an ordinary resolution "
    "adjourning that meeting indefinitely, which was approved."
)
# constructed: a call-off that never names a split (a recall limit the measurement recorded)
NO_SPLIT_NAMED = "Proposal 2 was withdrawn."


@pytest.mark.parametrize(
    ("text", "pattern", "cue_words"),
    [
        (AIRI, "neg_active", "would not be effecting the reverse stock split"),
        (SNDL, "neg_active", "will not proceed with the proposed share consolidation"),
        (IPSC, "not_intend", "does not intend to effect the reverse stock split"),
        (BNBX, "wont", "would no longer be pursuing a reverse stock split"),
        (ELBM, "expired", "authorization has since expired"),
    ],
    ids=["AIRI", "SNDL", "IPSC-no-ratio", "BNBX", "ELBM-expired"],
)
def test_a_definite_decision_reads_decision(text: str, pattern: str, cue_words: str) -> None:
    cue = read_sentence(text)
    assert isinstance(cue, CallOffCue)
    assert (cue.kind, cue.pattern, text[cue.span[0] : cue.span[1]]) == (
        "decision",
        pattern,
        cue_words,
    )


def test_a_failed_vote_reads_failed_vote() -> None:
    cue = read_sentence(ATCH)
    assert cue is not None
    assert (cue.kind, cue.pattern, ATCH[cue.span[0] : cue.span[1]]) == (
        "failed_vote",
        "not_approved",
        "Split Proposal was not approved",
    )


@pytest.mark.parametrize(
    "text",
    [
        MAY_ABANDON,
        MAY_APPROVE,
        IF_NOT_APPROVED,
        EFFECTED_UNLESS,
        EFFECTIVE_UNTIL,
        PBM,
        WETO,
        NO_SPLIT_NAMED,
    ],
    ids=[
        "modal-may-abandon",
        "modal-may-approve",
        "modal-if-not-approved",
        "condition-effected-unless",
        "condition-effective-until",
        "PBM-postponement",
        "WETO-adjournment",
        "no-split-named",
    ],
)
def test_boilerplate_conditions_and_postponements_read_nothing(text: str) -> None:
    assert read_sentence(text) is None


def _without_guard(guard: str) -> split_calloff.CalloffWording:
    """``WORDING`` with one guard taken off every pattern."""
    kinds = tuple(
        (
            kind,
            tuple(
                (name, source, tuple(g for g in guards if g != guard))
                for name, source, guards in patterns
            ),
        )
        for kind, patterns in WORDING.kinds
    )
    return dataclasses.replace(WORDING, kinds=kinds)


@pytest.mark.parametrize(
    ("text", "guard", "pattern"),
    [
        (IF_NOT_APPROVED, "modal_before", "neg_passive"),
        (EFFECTED_UNLESS, "condition_after", "neg_passive"),
        (EFFECTIVE_UNTIL, "condition_after", "neg_passive"),
        (KNWN, "completed_split", "decided_to_abandon"),
    ],
    ids=["modal-before", "condition-unless", "condition-until", "completed-KNWN"],
)
def test_the_guard_is_what_sets_the_sentence_aside(text: str, guard: str, pattern: str) -> None:
    assert read_sentence(text) is None
    cue = read_sentence(text, _without_guard(guard))
    assert cue is not None and (cue.kind, cue.pattern) == ("decision", pattern)


def test_a_meeting_cancelled_for_a_split_already_done_calls_nothing_off() -> None:
    """SENS and KNWN say “decision to cancel the Special Meeting” alike: SENS's split was then not done (a
    call-off), KNWN's had become effective the day before (none)."""
    cue = read_sentence(SENS)
    assert cue is not None
    assert (cue.kind, cue.pattern, SENS[cue.span[0] : cue.span[1]]) == (
        "decision",
        "decided_to_abandon",
        "decision to cancel",
    )
    assert read_sentence(KNWN) is None


# constructed: one per completion phrase and pattern the guard reaches, each read as a call-off without it
COMPLETED = (
    (
        "perfect_passive",
        "The Special Meeting has been cancelled, as the reverse stock split took effect on "
        "March 3, 2025 without a stockholder vote.",
    ),
    (
        "active_past",
        "The Board cancelled the special meeting, since the 1-for-20 reverse stock split was "
        "effected on May 1, 2026 by the Board alone.",
    ),
    (
        "decided_not",
        "The Company determined not to proceed with the Special Meeting, as its share "
        "consolidation has been effected without a shareholder vote.",
    ),
    (
        "noun",
        "The withdrawal of the Reverse Stock Split Proposal follows the reverse stock split, "
        "which became effective on June 2, 2026.",
    ),
)


@pytest.mark.parametrize(("pattern", "text"), COMPLETED, ids=[c[0] for c in COMPLETED])
def test_a_split_stated_as_done_is_not_called_off(pattern: str, text: str) -> None:
    assert read_sentence(text) is None
    cue = read_sentence(text, _without_guard("completed_split"))
    assert cue is not None and (cue.kind, cue.pattern) == ("decision", pattern)


# constructed: a cue whose object is the split itself, or a failed vote, beside a split already done is about
# another split, and stays read
ANOTHER_SPLIT = (
    (
        "decision",
        "neg_active",
        "Although its 1-for-10 reverse split became effective in 2024, the Company will not "
        "effect the reverse split approved at the 2026 meeting.",
    ),
    (
        "failed_vote",
        "not_approved",
        "The Reverse Stock Split Proposal was not approved, and the 1-for-60 reverse split, which "
        "became effective on December 31, 2024, stands.",
    ),
)


@pytest.mark.parametrize(
    ("kind", "pattern", "text"), ANOTHER_SPLIT, ids=[c[1] for c in ANOTHER_SPLIT]
)
def test_a_done_split_beside_a_direct_cue_or_a_failed_vote_sets_nothing_aside(
    kind: str, pattern: str, text: str
) -> None:
    cue = read_sentence(text)
    assert cue is not None and (cue.kind, cue.pattern) == (kind, pattern)


def test_the_completed_split_guard_reaches_the_patterns_with_no_split_object() -> None:
    """The guard rides on the decision patterns whose words do not make the split the cue's object (a meeting, a
    proposal, an amendment, an authorization, or no object): there the sentence's split may be only the one
    already done."""
    carried = [
        name
        for _, patterns in WORDING.kinds
        for name, _, guards in patterns
        if "completed_split" in guards
    ]
    assert carried == [
        "decided_not",
        "decided_to_abandon",
        "perfect_passive",
        "active_past",
        "no_longer",
        "noun",
        "expired",
    ]


def test_a_replaced_wording_changes_airis_reading() -> None:
    assert read_sentence(AIRI) is not None
    no_neg_active = dataclasses.replace(
        WORDING,
        kinds=tuple(
            (kind, tuple(p for p in patterns if p[0] != "neg_active"))
            for kind, patterns in WORDING.kinds
        ),
    )
    assert read_sentence(AIRI, no_neg_active) is None
    # one fragment edit reaches every pattern built on it: the execution verbs without "effect"
    fragments = tuple(
        (name, value.replace("effect(?:ed|ing)?|", "")) for name, value in WORDING.fragments
    )
    assert fragments != WORDING.fragments
    assert read_sentence(AIRI, dataclasses.replace(WORDING, fragments=fragments)) is None
    # the module's own wording is untouched, and is the default
    assert read_sentence(AIRI) == read_sentence(AIRI, WORDING)
    assert read_sentence(AIRI) is not None
    assert inspect.signature(read_sentence).parameters["wording"].default is WORDING


def test_an_undefined_guard_or_fragment_is_refused() -> None:
    bad_guard = dataclasses.replace(
        WORDING, kinds=(("decision", (("x", "split", ("no_such_guard",)),)),)
    )
    with pytest.raises(ValueError, match="no_such_guard"):
        read_sentence("The split.", bad_guard)
    bad_fragment = dataclasses.replace(
        WORDING, kinds=(("decision", (("x", "{no_such_fragment}", ()),)),)
    )
    with pytest.raises(ValueError, match="no_such_fragment"):
        read_sentence("The split.", bad_fragment)


def _leaves(value: object):
    if isinstance(value, tuple):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


def test_the_block_holds_only_str_int_and_tuples() -> None:
    assert dataclasses.is_dataclass(WORDING)
    assert type(WORDING).__dataclass_params__.frozen  # type: ignore[attr-defined]
    for f in dataclasses.fields(WORDING):
        for leaf in _leaves(getattr(WORDING, f.name)):
            assert type(leaf) in (str, int), (f.name, leaf)


# the issuers the measurement named (``hits_table.md``)
TICKERS = (
    "AIRI ATCH SENS SGRX CNTM IPSC BNBX AGPU ELBM SNDL SLXN INVZ STEM PBM APUS FOFO WETO VXRT ARAY SKYA RVSN "
    "MNDR CYCU USBC OPEN"
).split()
NAMES = (
    "Air Industries",
    "Tenax",
    "AtlasClear",
    "Senseonics",
    "Bit Origin",
    "ConnectM",
    "Century Therapeutics",
    "BNB Plus",
    "Predictive Oncology",
    "Electra",
    "Silexion",
    "Innoviz",
    "Psyence",
    "Apimeds",
    "Hang Feng",
    "Wetour",
    "Vaxart",
    "Accuray",
    "Sharps",
    "Rail Vision",
    "Mobile-health",
    "Cycurion",
    "Know Labs",
    "Opendoor",
)


def test_the_block_names_no_issuer() -> None:
    text = "\n".join(
        leaf
        for f in dataclasses.fields(WORDING)
        for leaf in _leaves(getattr(WORDING, f.name))
        if isinstance(leaf, str)
    )
    for ticker in TICKERS:
        assert not re.search(rf"\b{ticker}\b", text), ticker
    for name in NAMES:
        assert not re.search(rf"\b{re.escape(name)}\b", text, re.I), name
    # nor any other: a ticker is written in capitals and a name capitalized, while the block is matched
    # case-insensitively and needs no capital letter (beyond a regex escape and the share class "ADS")
    assert not re.search(r"[A-Z]", re.sub(r"\\[A-Za-z]", "", text).replace("ADS", ""))


def test_kinds_rank_strongest_first_and_names_are_unique() -> None:
    assert [kind for kind, _ in WORDING.kinds] == ["decision", "failed_vote"]
    names = [name for _, patterns in WORDING.kinds for name, _, _ in patterns]
    assert len(names) == len(set(names))


def test_the_logic_holds_no_pattern_of_its_own() -> None:
    """Every regex the module runs comes from the block: no call into ``re`` takes a literal pattern."""
    tree = ast.parse(Path(split_calloff.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
    ]
    assert calls
    for call in calls:
        assert call.args and not isinstance(
            call.args[0], (ast.Constant, ast.JoinedStr)
        ), ast.unparse(call)


# Every C1 / C3 sentence of ``hits_table.md`` (30, 28 distinct) but KNWN's and ``SET_ASIDE``'s, with the kind and
# pattern the measurement's ``classify.py`` read (C1 = decision, C3 = failed_vote; its first cue of the strongest
# class). Real call-offs and noise alike: which filings count is the caller's (forms, documents, the window), not the
# sentence reader's. KNWN's (0001654954-25-001790 EX-99.1 #1, noise: a meeting cancelled for a split already
# effective) is read otherwise under ``completed_split`` (``KNWN`` above), the ``SET_ASIDE`` rows under the guard
# each names.
MEASURED = (
    (
        "0001104659-25-001831 8-K #30 / 0001104659-25-009894 424B3 #2790",
        "failed_vote",
        "not_approved",
        "The Reverse Stock Split Proposal was not approved.",
    ),
    (
        "0001104659-25-002855 8-K #7",
        "decision",
        "decided_to_abandon",
        (
            "On January 8, 2025, the Company announced its decision to cancel the Special Meeting "
            "of Stockholders, which had been scheduled for January 10, 2025, for the purpose of "
            "seeking stockholder approval of a proposed reverse stock split within the range of "
            "25:1 to 50:1."
        ),
    ),
    (
        "0001104659-25-002855 EX-99.1 #1",
        "decision",
        "decided_not",
        (
            "Recent Highlights & Accomplishments · Generated preliminary unaudited fourth quarter "
            "revenue of approximately $8.3 million, and preliminary unaudited full year 2024 "
            "total revenue of approximately $22.5 million · Received FDA approval for Eversense® "
            "365 Continuous Glucose Monitoring system and launched Eversense 365 with commercial "
            "partner, Ascensia. · Patient base increased 56% in 2024 over 2023 to approximately "
            "6,000 global patients · Reached a milestone of over a 1,000 annual U.S. Eversense "
            "prescribers during 2024 · Continued strong interest in Eversense 365, as evidenced "
            "by the following early U.S. launch performance indicators: o Direct to Consumer "
            "(DTC) leads have doubled in Q4 compared to the pre-launch third quarter o New "
            "patient shipments of approximately 600 in December were the highest monthly total in "
            "the Company’s history o The number of patients switching to Eversense from "
            "competitive CGM’s has increased 49% since Eversense 365 launch o 118% increase in "
            "patient referrals from physicians since launch · Following recent appreciation in "
            "Company’s share price, determined not to proceed with previously announced Special "
            "Meeting of stockholders to consider reverse stock split."
        ),
    ),
    (
        "0001104659-25-002855 EX-99.1 #10",
        "decision",
        "decided_not",
        (
            "Cancellation of Special Meeting of Stockholders The Company has determined not to "
            "proceed with the Special Meeting, scheduled for January 10, 2025, for the purpose of "
            "seeking shareholder approval of a proposed reverse stock split within the range of "
            "25:1 to 50:1."
        ),
    ),
    (
        "0001104659-25-002855 EX-99.1 #13",
        "decision",
        "decided_not",
        (
            "However, after assessing the pricing implications of a reverse split at the lowest "
            "end of the proposed range of stock splits, and taking into consideration shareholder "
            "feedback regarding conducting a reverse split of at this time, the Company "
            "determined not to proceed with the Special Meeting and the proposed reverse stock "
            "split."
        ),
    ),
    (
        "0001104659-25-123594 EX-99.1 #4",
        "decision",
        "neg_passive",
        (
            "These benefits will be timely and highly accretive as ConnectM continues with a "
            "laser-focus on execution: growing revenue, improving margins, and capitalizing on "
            "the vast market opportunities for our proprietary energy solutions.” The Board "
            "previously obtained stockholder approval in April 2025 for a narrower 1-for-5 to "
            "1-for-8 reverse split range, which was not implemented."
        ),
    ),
    (
        "0001104659-26-006236 8-K #10",
        "decision",
        "not_intend",
        (
            "As a result, the Company does not intend to effect the reverse stock split as "
            "approved by the stockholders at the Special Meeting."
        ),
    ),
    (
        "0001104659-26-082893 8-K #8",
        "decision",
        "wont",
        (
            "On June 1, 2026, the Company informed the Panel that it would no longer be pursuing "
            "a reverse stock split to regain compliance with the $1.00 bid price requirement but "
            "instead the Company asked for an additional 60-day extension to organically regain "
            "compliance with the $1.00 bid price requirement as it made announcements in "
            "connection with the strategic review."
        ),
    ),
    (
        "0001171843-25-006753 EX-99.1 #17",
        "decision",
        "decided_not",
        "Electra has elected not to proceed with the Reverse Split at this time.",
    ),
    (
        "0001171843-26-003616 EX-99.1 #291",
        "decision",
        "expired",
        (
            "On October 15, 2025, Shareholders approved a reverse split; however, that "
            "authorization has since expired and is no longer available to the Company."
        ),
    ),
    (
        "0001171843-26-006673 EX-99.1 #6",
        "decision",
        "neg_active",
        (
            "Accordingly, the Company will not proceed with the proposed share consolidation "
            "under the authority sought at the Meeting."
        ),
    ),
    (
        "0001178913-26-004155 EX-99.1 #157",
        "decision",
        "decided_not",
        (
            "For example, in December 2024 our shareholders authorized the Board to implement a "
            "similar reverse share split and share reduction, but the Board elected at the time "
            "not to implement such reverse share split."
        ),
    ),
    (
        "0001193125-25-152073 EX-4.3 #190",
        "decision",
        "neg_passive",
        (
            "If any dividend, distribution, stock split or stock combination of the type "
            "described in this Section 11(a)( i ) is declared or announced, but not so paid or "
            "made, then the Exercise Price shall be readjusted, effective as of the date on which "
            "it is determined that such dividend or distribution will not be paid or such stock "
            "split or stock combination will not be effected, to the Exercise Price that would "
            "then be in effect had such dividend, distribution, stock split or stock combination "
            "not been declared or announced, as the case may be."
        ),
    ),
    (
        "0001213900-26-122096 8-K #2",
        "decision",
        "neg_active",
        (
            "On November 19, 2026, Air Industries Group, announced that it would not be effecting "
            "the reverse stock split (the “Reverse Stock Split”) of its authorized, issued and "
            "outstanding shares of common stock, par value $0.001 per share, at a ratio of "
            "1-for-5 (the “Reverse Stock Split”) in connection with the Company’s proposed merger "
            "with Tenax Aerospace Acquisition, LLC (“Tenax”) that had been announced on November "
            "10, 2026."
        ),
    ),
    (
        "0001213900-26-122096 EX-99.1 #0",
        "decision",
        "neg_active",
        (
            "Exhibit 99.1 Nov 19, 2026 7:00 AM Eastern Standard Time Air Industries Group "
            "Announces Postponement of Reverse Stock Split and Dividend BAY SHORE, "
            "N.Y.--(BUSINESS WIRE)-- Air Industries Group (“Air Industries,” “AIR” or the "
            "“Company”) (NYSE American: AIRI), today announced that it would not be effecting the "
            "reverse stock split (the “Reverse Stock Split”) of its authorized, issued and "
            "outstanding shares of common stock, par value $0.001 per share, at a ratio of "
            "1-for-5 (the “Reverse Stock Split”) in connection with the Company’s proposed merger "
            "with Tenax Aerospace Acquisition, LLC (“Tenax”) that had been announced on November "
            "10, 2026."
        ),
    ),
    (
        "0001437749-25-027524 EX-99.1 #324",
        "decision",
        "neg_active",
        "And there was a time where we did not pursue the reverse stock split.",
    ),
    (
        "0001437749-26-024904 EX-10.1 #480",
        "failed_vote",
        "not_approved",
        (
            "The Company acknowledges and agrees that the Stockholder Approval and the Reverse "
            "Stock Split are conditions to the Closing and that, following the Closing, the "
            "Company shall not delay the filing, effectiveness or continued availability of any "
            "Registration Statement required to be filed pursuant to Section 4(w) on the basis "
            "that the Stockholder Approval, the Reverse Stock Split or any related corporate "
            "action, Principal Market approval, DTC process, Transfer Agent process or other "
            "ministerial or administrative action has not been obtained, completed or effected."
        ),
    ),
    (
        "0001628280-26-048496 EX-99.1 #9",
        "decision",
        "decided_not",
        (
            "As previously disclosed, Cycurion’s Board of Directors and management determined not "
            "to pursue a reverse stock split at this time."
        ),
    ),
    (
        "0001801169-25-000069 8-K #7",
        "decision",
        "decided_to_abandon",
        (
            "The Board of Directors (the “Board”) of the Company has decided to cancel the "
            "Special Meeting of Stockholders (the “Special Meeting), scheduled for August 27, "
            "2025, which was intended to consider two proposals related to a discretionary "
            "reverse stock split of the Company’s common stock."
        ),
    ),
    (
        "0001801169-25-000069 EX-99.1 #5",
        "decision",
        "decided_to_abandon",
        (
            "The Board of Directors of the Company has decided to cancel the Special Meeting of "
            "Stockholders, scheduled for August 27, 2025, which was intended to consider two "
            "proposals related to a discretionary reverse stock split of the Company’s common "
            "stock."
        ),
    ),
)


@pytest.mark.parametrize(
    ("kind", "pattern", "text"), [m[1:] for m in MEASURED], ids=[m[0] for m in MEASURED]
)
def test_every_measured_definite_sentence_reads_as_measured(
    kind: str, pattern: str, text: str
) -> None:
    cue = read_sentence(text)
    assert cue is not None and (cue.kind, cue.pattern) == (kind, pattern)


# The ``hits_table.md`` rows (all noise) that F10 P4's prod-cache replay found read as call-offs
# (``docs/temp/split-reader-misreads-evidence/f10p4/summary.md``), with the guard that now sets each aside and the
# kind and pattern the measurement read: a STATUS of a split not yet done (``not_yet_done``: "had not been effected",
# "has not yet been implemented as of the date", "had not become effective as of the date", "have not implemented",
# "was not implemented to date") and a proxy table's conditional ASSUMPTION ("assuming Proposal Five, Six and Seven
# are not approved", ``assumption``). Each text is the measurement's; the six P4 names are its call-off passages
# byte for byte (#1752, the same 20-F's, only the measurement read).
SET_ASIDE = (
    (
        "0001104659-25-018262 EX-99.1 #248",
        "assumption",
        "failed_vote",
        "not_approved",
        (
            "Before reverse After Reverse Split stock split 1-for-10 1-for- 100 1-for- 200 "
            "Ordinary Shares Authorized (assuming Proposal Five, Six and Seven are not approved) "
            "500,000,000 50,000,000 5,000,000 2 ,500,000 Ordinary Shares Authorized (assuming "
            "Proposal Five, Six and Seven are approved) 15,000,000,000 1,500,000,000 150,000,000 "
            "75,000,000 Par Value per Ordinary Share (assuming Proposal Five, Six and Seven are "
            "not approved) $ 0."
        ),
    ),
    (
        "0001104659-26-044467 HTML #1428",
        "not_yet_done",
        "decision",
        "neg_passive",
        (
            "As of the date of this filing, the reverse stock split authorized at the April 11, "
            "2025 special meeting had not been effected."
        ),
    ),
    (
        "0001213900-26-050939 HTML #493 / 0001213900-26-050939 HTML #839",
        "not_yet_done",
        "decision",
        "neg_passive",
        (
            "The only remaining item not yet effected is the share consolidation, for which the "
            "shareholders authorized the board of directors to determine the specific "
            "consolidation ratio (within the approved range) and the effective date, and which "
            "has not yet been implemented as of the date of this annual report."
        ),
    ),
    (
        "0001213900-26-050939 HTML #1752",
        "not_yet_done",
        "decision",
        "neg_passive",
        (
            "The authorized share consolidation has been approved but has not yet been "
            "implemented as of the date of this annual report."
        ),
    ),
    (
        "0001214659-26-010549 HTML #304",
        "not_yet_done",
        "decision",
        "neg_passive",
        (
            "Because the reverse stock split had not become effective as of the date these "
            "condensed consolidated financial statements were issued, the share and per-share "
            "amounts presented herein have not been adjusted to reflect the reverse stock split."
        ),
    ),
    (
        "0001493152-24-038695 EX-99.1 #5",
        "not_yet_done",
        "decision",
        "neg_active",
        (
            "Please note that we have not implemented the reverse stock split that was approved "
            "in July,” continued Mr. Hayes."
        ),
    ),
    (
        "0001493152-25-018461 EX-99.1 #112",
        "not_yet_done",
        "decision",
        "neg_passive",
        "Such reverse split was not implemented to date.",
    ),
)


@pytest.mark.parametrize(
    ("guard", "kind", "pattern", "text"),
    [s[1:] for s in SET_ASIDE],
    ids=[s[0] for s in SET_ASIDE],
)
def test_a_status_of_a_split_not_yet_done_or_an_assumption_is_no_call_off(
    guard: str, kind: str, pattern: str, text: str
) -> None:
    assert read_sentence(text) is None
    cue = read_sentence(text, _without_guard(guard))
    assert cue is not None and (cue.kind, cue.pattern) == (kind, pattern)


# constructed: one per status form and assumption cue the measured sentences above do not show, each read as a
# call-off without its guard
NOT_YET_CONSTRUCTED = (
    (
        "not_yet_done",
        "decision",
        "neg_passive",
        "The reverse stock split was not effected as of March 31, 2026.",
    ),
    (
        "not_yet_done",
        "decision",
        "neg_active",
        "The Company did not effect the reverse stock split as of the date of this annual report.",
    ),
    (
        "assumption",
        "failed_vote",
        "did_not_receive",
        "Assuming the Reverse Split Proposal does not receive the requisite vote, the number of "
        "authorized shares stays at 500,000,000.",
    ),
    (
        "assumption",
        "failed_vote",
        "failed",
        "The table below assumes that the Reverse Split Proposal failed to pass.",
    ),
)


@pytest.mark.parametrize(
    ("guard", "kind", "pattern", "text"),
    NOT_YET_CONSTRUCTED,
    ids=[f"{c[0]}-{c[2]}" for c in NOT_YET_CONSTRUCTED],
)
def test_each_status_form_and_assumption_cue_is_set_aside_by_its_guard(
    guard: str, kind: str, pattern: str, text: str
) -> None:
    assert read_sentence(text) is None
    cue = read_sentence(text, _without_guard(guard))
    assert cue is not None and (cue.kind, cue.pattern) == (kind, pattern)


# constructed controls: a decision dated "as of" (the date before the cue, the verb future) and a failed vote
# beside a past "assumed" (no conditional) stay read; the real "which was not implemented" (CNTM, a past
# authority the board then terminated) is in MEASURED and stays read too
STAYS_READ = (
    (
        "decision",
        "neg_active",
        "As of the date of this report, the Board has determined that the Company will not effect "
        "the reverse stock split.",
    ),
    (
        "failed_vote",
        "not_approved",
        "The Reverse Stock Split Proposal, which assumed a 1-for-10 ratio, was not approved.",
    ),
)


@pytest.mark.parametrize(
    ("kind", "pattern", "text"), STAYS_READ, ids=["dated-decision", "assumed-past"]
)
def test_a_dated_decision_and_a_past_assumed_stay_read(kind: str, pattern: str, text: str) -> None:
    cue = read_sentence(text)
    assert cue is not None and (cue.kind, cue.pattern) == (kind, pattern)


def test_the_status_guard_reaches_the_negations_and_the_assumption_guard_the_failed_votes() -> None:
    """``not_yet_done`` rides on the two patterns whose cue is a negated execution verb (``neg_active``,
    ``neg_passive``): a decision's other forms (``will no longer``, ``does not intend``, ``decided not to``) state
    no status. ``assumption`` rides on every failed-vote pattern."""

    def carried(guard: str) -> list[str]:
        return [
            name for _, patterns in WORDING.kinds for name, _, guards in patterns if guard in guards
        ]

    assert carried("not_yet_done") == ["neg_active", "neg_passive"]
    assert carried("assumption") == ["not_approved", "did_not_receive", "failed"]


def test_the_measured_rows_set_aside_and_knwn_are_the_whole_table() -> None:
    labels = [part for m in (*MEASURED, *SET_ASIDE) for part in m[0].split(" / ")]
    assert len(labels) + 1 == 30 and len(MEASURED) + len(SET_ASIDE) + 1 == 28
    assert len(SET_ASIDE) == 7 and len(MEASURED) == 20
    assert not any(label.startswith("0001654954-25-001790") for label in labels)
