"""The call-off reader (F10): does one sentence say a split was CALLED OFF? Pure and deterministic: fixed
patterns over the filing's own words, no model anywhere near it (invariant #3). It reads one sentence; which
filings and documents count, and what a call-off does to a listed split, are the caller's.

**Two kinds**, the stronger first:

- ``decision``: a definite statement that the split will not happen. AIRI's 8-K of 2026-11-19 "announced that
  it would not be effecting the reverse stock split … at a ratio of 1-for-5" (the listed 1-for-5 it called off);
  "will not proceed with the proposed share consolidation" (SNDL); "does not intend to effect the reverse stock
  split" (IPSC); "would no longer be pursuing a reverse stock split" (BNBX); "that authorization has since
  expired" (ELBM).
- ``failed_vote``: "The Reverse Stock Split Proposal was not approved." (ATCH).

A cue counts only within ``near`` characters of a split named in the same sentence, and never under one of its
guards: modal or conditional words before it in its own clause ("may", "if", "unless", "whether", "risk" …: a
board MAY abandon a split, IF it is not approved), a condition right after it ("will not become effective until
…"), an object that is ANY split rather than this one (a covenant: "will not effect any reverse split"), an
object that is shares or another instrument (fractional shares, warrants, notes), for a decision to abandon,
an object that is no split, meeting, proposal or amendment, and, for a cue whose object need not be the split (a
meeting, a proposal, an authorization, or none), a split the sentence states as already done: KNWN's “decision to
cancel the Special Meeting comes after the Company announced a 1 for 40 reverse stock split …, which became
effective February 19, 2025” calls nothing off, while SENS's “decision to cancel the Special Meeting” for a split
it then did not do is a call-off.

**Not read.** A POSTPONEMENT or adjournment ("elected to delay the effectiveness of the reverse stock split",
PBM; "adjourning that meeting indefinitely", WETO), a recorded decision: as a trigger it only added wrong flags
(5 of 6, postponements to dates already listed). And a call-off that never says "split" ("Proposal 2 was
withdrawn").

MEASURED 2026-11-28 on prod's cache (37,812 8-K / 6-K accessions, 2024-26,
``docs/temp/framework-completion-evidence/f10-measure``): the patterns and guards are its ``classify.py``'s,
which read 26 decision and 4 failed-vote sentences in 23 accessions (13 real call-offs, the rest restated
history, status lines and contract terms); of 50 random sentences it read as boilerplate, none was a call-off.
One guard came after it (F10 P1b), ``completed_split``: of those 30 it sets aside KNWN's alone (noise), and of the
measurement's 9,317 candidate and 7,977 sweep sentences nothing else
(``docs/temp/framework-completion-evidence/f10-p1b``).

**All the wording is in ``WORDING``**, data only (str, int and tuples); the logic below it reads nothing else,
and ``read_sentence`` takes the wording as a parameter. Changing what is read is an edit to the block, never to
the logic. It sits beside the split reader, whose patterns are module data too, and not in ``domain/``, which the
editions engine hashes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# (pattern name, regex source, the guards it is read under)
CuePattern = tuple[str, str, tuple[str, ...]]


@dataclass(frozen=True)
class CalloffWording:
    """Every word the call-off reader matches, and every reach, as data. Each source is matched
    case-insensitively; ``{name}`` in a pattern is the fragment of that name."""

    split_mention: str
    near: int
    fragments: tuple[tuple[str, str], ...]
    kinds: tuple[tuple[str, tuple[CuePattern, ...]], ...]
    clause_break: str
    modal_before: str
    modal_reach: int
    condition_after: str
    condition_reach: int
    generic_object: str
    shares_object: str
    shares_back: int
    shares_reach: int
    split_object: str
    split_object_reach: int
    completed_split: str


# ======================================== WORDING (data only) ========================================
WORDING = CalloffWording(
    # a split named in the sentence: a cue counts only within ``near`` characters of one
    split_mention=(
        r"\bsplit\b|\bsplit-|\bsplits\b|\b(?:share|stock)\s+consolidation|"
        r"\bconsolidat\w*\s+of\s+(?:\w+\s+){0,5}?(?:shares|stock)\b"
    ),
    near=250,
    # shared pieces of the patterns below, each written once
    fragments=(
        ("split_word", r"(?:split|consolidation)"),
        (
            "execute",
            r"(?:effect(?:ed|ing)?|implement(?:ed|ing)?|proceed(?:ed|ing)?\s+with|complet(?:e|ed|ing)|"
            r"consummat(?:e|ed|ing)|undertak(?:e|en|ing)|carr(?:y|ied|ying)\s+out|"
            r"go(?:ing)?\s+(?:forward|ahead)\s+with|mov(?:e|ing)\s+forward\s+with|pursu(?:e|ed|ing)|file|"
            r"fil(?:ed|ing))",
        ),
        ("same_clause", r"[^.;]{0,100}?"),
    ),
    # the kinds, strongest first; within a kind the first pattern that holds is the one named
    kinds=(
        (
            "decision",
            (
                (
                    "neg_active",
                    r"\b(?:will|would|did|does|do|is|are|was|were|has|have|had)\s+not\s+"
                    r"(?:be\s+|been\s+|now\s+)?{execute}\b{same_clause}\b{split_word}",
                    ("modal_before", "condition_after", "generic_object"),
                ),
                (
                    "wont",
                    r"\b(?:won't|will\s+no\s+longer|would\s+no\s+longer)\s+(?:be\s+)?"
                    r"{execute}{same_clause}{split_word}",
                    ("modal_before", "generic_object"),
                ),
                (
                    "neg_passive",
                    r"\b{split_word}[\w\s,\"“”()-]{0,80}?\b(?:will|would|was|were|has|have|had|is|are)\s+not\s+"
                    r"(?:be\s+|been\s+|yet\s+been\s+)?(?:effected|implemented|completed|consummated|"
                    r"undertaken|carried\s+out|pursued|proceed(?:ing)?|occur(?:ring)?|go(?:ing)?\s+forward|"
                    r"tak(?:e|ing)\s+effect|becom(?:e|ing)\s+effective|happen(?:ing)?)\b",
                    ("modal_before", "condition_after", "generic_object"),
                ),
                (
                    "decided_not",
                    r"\b(?:decided|determined|elected|resolved|chose|concluded|opted|decision)\s+"
                    r"(?:\w+\s+){0,4}?not\s+to\s+(?:effect|implement|proceed|pursue|move|go|complete|"
                    r"consummate|undertake|seek|file|carry|seek)",
                    ("modal_before", "completed_split"),
                ),
                (
                    "decided_to_abandon",
                    r"\b(?:decided|determined|elected|resolved|chose|agreed|concluded|opted|decision|"
                    r"approved)\s+(?:\w+\s+){0,2}?to\s+(?:abandon|withdraw|cancel|terminate|rescind|revoke|"
                    r"discontinue|forgo|forego|call\s+off)",
                    ("modal_before", "split_object", "completed_split"),
                ),
                (
                    "perfect_passive",
                    r"\b(?:split|consolidation|proposal|amendment|meeting)\b[\w\s\"“”()-]{0,80}?"
                    r"\b(?:has|have|had|was|were|is|are|been|being|will\s+be|would\s+be|shall\s+be)\s+"
                    r"(?:been\s+|now\s+|formally\s+|officially\s+|hereby\s+)?(?:abandoned|cancell?ed|withdrawn|"
                    r"terminated|rescinded|revoked|discontinued|called\s+off)\b",
                    ("modal_before", "shares_object", "completed_split"),
                ),
                (
                    "active_past",
                    r"\b(?:abandoned|cancell?ed|withdrew|withdrawn|terminated|rescinded|revoked|discontinued|"
                    r"called\s+off|abandons|withdraws|cancels|rescinds|revokes)\s+"
                    r"(?:the\s+|its\s+|our\s+|such\s+|their\s+)?(?:previously\s+(?:announced|approved|disclosed)\s+|"
                    r"proposed\s+|planned\s+|contemplated\s+)?(?:[\w\"“”()-]+\s+){0,4}?"
                    r"(?:split|consolidation|proposal|meeting)",
                    ("modal_before", "shares_object", "completed_split"),
                ),
                (
                    "not_intend",
                    r"\b(?:does|do|did)\s+not\s+(?:currently\s+|presently\s+|now\s+)?"
                    r"(?:intend|plan|expect|anticipate)\s+to\s+(?:effect|implement|proceed|pursue|complete|seek|"
                    r"move){same_clause}{split_word}",
                    ("modal_before", "condition_after", "generic_object"),
                ),
                (
                    "no_longer",
                    r"\bno\s+longer\s+(?:intends?|plans?|expects?|anticipates?|seeks?|seeking|pursu\w+|"
                    r"proceed\w*|consider\w*|wish\w*|contemplat\w*|going\s+to|be\s+effect\w*|be\s+implement\w*)",
                    ("modal_before", "completed_split"),
                ),
                (
                    "noun",
                    r"\b(?:abandonment|cancell?ation|withdrawal|termination|rescission|revocation)\s+of\s+"
                    r"(?:the\s+|its\s+|our\s+|such\s+)?(?:proposed\s+|planned\s+|previously\s+announced\s+|"
                    r"contemplated\s+)?(?:[\w\"“”()-]+\s+){0,3}?(?:split|consolidation|proposal)",
                    ("modal_before", "shares_object", "completed_split"),
                ),
                (
                    "expired",
                    r"\b(?:authori[sz]ation|approval|authority)\b[^.;]{0,60}?\b(?:has|have|had)?\s*"
                    r"(?:since\s+|now\s+)?(?:expired|lapsed)\b",
                    ("modal_before", "completed_split"),
                ),
            ),
        ),
        (
            "failed_vote",
            (
                (
                    "not_approved",
                    r"\b(?:split|consolidation|proposal|amendment)\b[^.;]{0,200}?"
                    r"\b(?:was|were|has|have|had|is|are)\s+not\s+(?:been\s+)?"
                    r"(?:approved|adopted|passed|ratified|obtained)",
                    ("modal_before", "condition_after"),
                ),
                (
                    "did_not_receive",
                    r"\b(?:did|does|do)\s+not\s+(?:receive|obtain|achieve|garner|get|pass|secure|attain|meet)\s+"
                    r"(?:the\s+|a\s+)?(?:requisite|required|sufficient|necessary|majority|enough|affirmative|"
                    r"vote|approval|threshold)",
                    ("modal_before",),
                ),
                (
                    "failed",
                    r"\bfailed\s+to\s+(?:pass|receive|obtain|achieve|garner|be\s+approved|secure|attain|carry|"
                    r"get|win)\b|\b(?:was|were)\s+(?:defeated|rejected|voted\s+down)\b|\binsufficient\s+votes\b|"
                    r"\bdid\s+not\s+pass\b|\bproposal\s+failed\b|"
                    r"\bnot\s+(?:been\s+)?approved\s+by\s+(?:the\s+)?(?:requisite|stockholders|shareholders|members)",
                    ("modal_before",),
                ),
            ),
        ),
    ),
    # guard modal_before: these words in the cue's own clause (the modal_reach characters before it, cut
    # after the last clause break; "5:00 p.m." is none) or in the cue itself
    clause_break=r";|:\s",
    modal_before=(
        r"\b(?:may|might|could|can|cannot|if|unless|whether|should|in\s+the\s+event|reserves?\s+the\s+right|"
        r"retains?\s+the|discretion|at\s+any\s+time|even\s+if|risks?|uncertaint\w*|forward-looking|possib\w*|"
        r"no\s+assurance|assurance|there\s+can\s+be|in\s+order\s+to|so\s+long\s+as|provided\s+that|unless|"
        r"in\s+case|to\s+the\s+extent|goal|aim|hope|would\s+then|in\s+which\s+case)\b"
    ),
    modal_reach=250,
    # guard condition_after: a condition in the condition_reach characters after the cue
    condition_after=(
        r"^[^.;]{0,120}?\b(?:unless|until|if|in\s+the\s+event|prior\s+to|absent|without|before|except)\b"
    ),
    condition_reach=160,
    # guard generic_object: the cue's object is any split, not a named one
    generic_object=(
        r"\bnot\s+(?:be\s+|been\s+|now\s+)?\w+(?:\s+with)?\s+(?:any|a|an|another|further|additional|such)\s|"
        r"\bany\s+(?:\w+\s+){0,5}?(?:split|consolidation)"
    ),
    # guard shares_object: shares or another instrument, read over the cue and the shares_back characters
    # before it (its last shares_reach characters)
    shares_object=(
        r"split\s+shares|fraction|certificat|\bADSs?\b|warrant|option|\bnotes?\b|interest|agreement|hole|"
        r"resolution|treasury"
    ),
    shares_back=60,
    shares_reach=150,
    # guard split_object: what is abandoned must be named in the split_object_reach characters after the cue
    split_object=r"split|consolidation|meeting|proposal|amendment",
    split_object_reach=60,
    # guard completed_split: anywhere in the sentence, a split followed in its clause (150 characters, no ";") by
    # words that say it is already done; only on the patterns whose object need not be the split itself (a meeting
    # cancelled after the split took effect calls nothing off). The forms are affirmative and adjacent: "was not
    # effected" or "had not become effective" is no completion
    completed_split=(
        r"\b(?:split|consolidation)\b[^;]{0,150}?\b(?:became\s+effective|(?:has|had)\s+become\s+effective|"
        r"took\s+effect|(?:was|were)\s+(?:effected|effective|completed)|(?:has|had)\s+been\s+(?:effected|completed))\b"
    ),
)
# ===================================== end of WORDING; logic below =====================================


@dataclass(frozen=True)
class CallOffCue:
    """What ``read_sentence`` found: the kind, the pattern's name in the wording, and where in the sentence
    the pattern matched (``text[span[0]:span[1]]``)."""

    kind: str
    pattern: str
    span: tuple[int, int]


_GUARDS = (
    "modal_before",
    "condition_after",
    "generic_object",
    "shares_object",
    "split_object",
    "completed_split",
)


@dataclass(frozen=True)
class _Cue:
    kind: str
    name: str
    rx: re.Pattern[str]
    guards: frozenset[str]


@dataclass(frozen=True)
class _Reader:
    split_mention: re.Pattern[str]
    cues: tuple[_Cue, ...]
    clause_break: re.Pattern[str]
    modal_before: re.Pattern[str]
    condition_after: re.Pattern[str]
    generic_object: re.Pattern[str]
    shares_object: re.Pattern[str]
    split_object: re.Pattern[str]
    completed_split: re.Pattern[str]


def _rx(source: str) -> re.Pattern[str]:
    return re.compile(source, re.IGNORECASE)


def _expand(name: str, source: str, fragments: tuple[tuple[str, str], ...]) -> str:
    """``source`` with each ``{fragment}`` replaced; a ``{name}`` the wording does not define is refused (a
    regex quantifier such as ``{0,80}`` starts with a digit)."""
    for fragment, value in fragments:
        source = source.replace("{" + fragment + "}", value)
    for i, ch in enumerate(source[:-1]):
        if ch == "{" and source[i + 1].isalpha():
            end = source.find("}", i)
            raise ValueError(f"call-off pattern {name!r}: undefined fragment {source[i:end + 1]}")
    return source


@lru_cache(maxsize=8)
def _reader(wording: CalloffWording) -> _Reader:
    cues = []
    for kind, patterns in wording.kinds:
        for name, source, guards in patterns:
            unknown = sorted(set(guards) - set(_GUARDS))
            if unknown:
                raise ValueError(f"call-off pattern {name!r}: unknown guard {', '.join(unknown)}")
            cues.append(
                _Cue(kind, name, _rx(_expand(name, source, wording.fragments)), frozenset(guards))
            )
    return _Reader(
        split_mention=_rx(wording.split_mention),
        cues=tuple(cues),
        clause_break=_rx(wording.clause_break),
        modal_before=_rx(wording.modal_before),
        condition_after=_rx(wording.condition_after),
        generic_object=_rx(wording.generic_object),
        shares_object=_rx(wording.shares_object),
        split_object=_rx(wording.split_object),
        completed_split=_rx(wording.completed_split),
    )


def _distance(mentions: list[tuple[int, int]], a: int, z: int) -> int:
    """Characters between the cue ``[a, z)`` and the nearest split mention (0 when they overlap)."""
    return min(0 if (s <= z and e >= a) else min(abs(s - z), abs(a - e)) for s, e in mentions)


def _set_aside(r: _Reader, w: CalloffWording, cue: _Cue, text: str, m: re.Match[str]) -> bool:
    """Whether one of the cue's guards holds."""
    a, z = m.span()
    if "modal_before" in cue.guards:
        before = text[max(0, a - w.modal_reach) : a]
        cuts = [b.end() for b in r.clause_break.finditer(before)]
        if cuts:
            before = before[cuts[-1] :]
        if r.modal_before.search(before) or r.modal_before.search(m.group(0)):
            return True
    if "condition_after" in cue.guards and r.condition_after.search(
        text[z : z + w.condition_reach]
    ):
        return True
    if "generic_object" in cue.guards and r.generic_object.search(m.group(0)):
        return True
    if "shares_object" in cue.guards:
        if r.shares_object.search(text[max(0, a - w.shares_back) : z][-w.shares_reach :]):
            return True
    if "completed_split" in cue.guards and r.completed_split.search(text):
        return True
    return "split_object" in cue.guards and not r.split_object.search(
        text[z : z + w.split_object_reach]
    )


def read_sentence(text: str, wording: CalloffWording = WORDING) -> CallOffCue | None:
    """The call-off one sentence states, or None: the first cue of the strongest kind that sits near a split
    mention and that none of its guards sets aside (the kinds and, within a kind, the patterns in the wording's
    order; a pattern's matches left to right)."""
    r = _reader(wording)
    mentions = [m.span() for m in r.split_mention.finditer(text)]
    if not mentions:
        return None
    for cue in r.cues:
        for m in cue.rx.finditer(text):
            if _distance(mentions, *m.span()) > wording.near:
                continue
            if _set_aside(r, wording, cue, text, m):
                continue
            return CallOffCue(cue.kind, cue.name, m.span())
    return None
