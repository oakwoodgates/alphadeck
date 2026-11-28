"""F10: does a filing call off a listed split? THE one matcher. Pure: no clock, no settings, no I/O (like
``domain.splits.cluster_sec``); the caller reads the settings once a pass and passes a ``CalloffRule``. The warning
and the demote are this same test under two rules.

A filing ``s`` calls off a listed split (``ratio_from`` -> ``ratio_to``, first session ``first_session``, public
since ``announced_on``) under ``rule`` when all of these hold:

1. it carries call-off wording (``s.calloff``: ``domain.splits.CallOff``, read by ``ingest/edgar/split_calloff``);
2. its form is one of ``rule.forms``;
3. it was filed in ``[announced_on, first_session + rule.after_days]``, both ends in: a call-off filed before the
   split was announced is about an earlier plan, one filed well after its first session about a later one;
4. the ratio rule passes: ``any`` (R1: a call-off that states no ratio counts too) or ``same`` (R2: the call-off
   states a ratio, exactly equal to the listed one as fractions, ``domain.splits.same_ratio``).

``announced_on`` is the caller's: the listed row's ``sec_filed``, else its ``valid_from`` (a Massive.com-only row).

MEASURED (``docs/temp/framework-completion-evidence/f10-measure``, 37,812 cached split filings, 2024-26):
R1 on 8-K/6-K with first session + 30 days flags two listed splits, one right (AIRI: "would not be effecting the
reverse stock split ... 1-for-5", filed the day before its first session) and one wrong (ATCH: a failed vote on a
NEW proposal, filed after its listed 1-for-60 took effect); R2 flags AIRI only. 10 of the 12 other real call-offs
state no ratio, which is why the warning's default is ``any``. One positive case: precision is anecdotal.

THE DEMOTE (``demote_rule``) is the same function with a fixed rule: the same stated ratio, filed on or before the
first session (``after_days`` 0; the report's R2 + R6). AIRI's call-off passes it; ATCH's never can.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from domain.splits import SecEvidence, same_ratio

RATIO_ANY = "any"
RATIO_SAME = "same"
RATIO_RULES = (RATIO_ANY, RATIO_SAME)

# the demote's fixed rule (``demote_rule``): a same-ratio call-off filed on or before the first session
DEMOTE_RATIO = RATIO_SAME
DEMOTE_AFTER_DAYS = 0


@dataclass(frozen=True)
class CalloffRule:
    """Which call-offs count: the filing's ``forms``, how many days past the first session the window reaches
    (``after_days``, 0 or more) and the ratio rule (``ratio``: ``any`` or ``same``, ``RATIO_RULES``). Forms
    compare exactly (``8-K/A`` is its own form)."""

    forms: tuple[str, ...]
    after_days: int
    ratio: str

    def __post_init__(self) -> None:
        if isinstance(self.forms, str):
            raise TypeError("forms is a sequence of form names, not one string")
        object.__setattr__(self, "forms", tuple(self.forms))
        if self.ratio not in RATIO_RULES:
            raise ValueError(f"ratio must be one of {RATIO_RULES}, not {self.ratio!r}")
        if self.after_days < 0:
            raise ValueError(f"after_days must be 0 or more, not {self.after_days}")


def demote_rule(forms: Iterable[str]) -> CalloffRule:
    """The demote's rule: ``forms`` are the caller's (the same setting as the warning's); the rest is fixed."""
    return CalloffRule(forms=tuple(forms), after_days=DEMOTE_AFTER_DAYS, ratio=DEMOTE_RATIO)


def _states_ratio(s: SecEvidence, ratio_from: Decimal, ratio_to: Decimal) -> bool:
    c = s.calloff
    return (
        c is not None
        and c.has_ratio
        and same_ratio(c.ratio_from, c.ratio_to, ratio_from, ratio_to)  # type: ignore[arg-type]
    )


def calls_off(
    s: SecEvidence,
    *,
    ratio_from: Decimal,
    ratio_to: Decimal,
    first_session: date,
    announced_on: date,
    rule: CalloffRule,
) -> bool:
    """Does filing ``s`` call off the listed split under ``rule`` (the module docstring's four conditions)?"""
    if s.calloff is None or s.form not in rule.forms:
        return False
    if not announced_on <= s.filed <= first_session + timedelta(days=rule.after_days):
        return False
    return rule.ratio == RATIO_ANY or _states_ratio(s, ratio_from, ratio_to)


def called_off(
    filings: Sequence[SecEvidence],
    *,
    ratio_from: Decimal,
    ratio_to: Decimal,
    first_session: date,
    announced_on: date,
    rule: CalloffRule,
) -> list[SecEvidence]:
    """The filings that call off the listed split (``calls_off``): those stating its ratio first, then by filing
    date and accession. Empty when none does."""
    hits = [
        s
        for s in filings
        if calls_off(
            s,
            ratio_from=ratio_from,
            ratio_to=ratio_to,
            first_session=first_session,
            announced_on=announced_on,
            rule=rule,
        )
    ]
    return sorted(
        hits, key=lambda s: (not _states_ratio(s, ratio_from, ratio_to), s.filed, s.accession)
    )
