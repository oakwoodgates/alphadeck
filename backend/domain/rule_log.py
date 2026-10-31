"""THE RULE LOG — the declared changes to the call logic, in order (editions S7).

The Scoreboard's automatic cut (``editions.cut``) starts a trial when the engine fingerprint changes
(``editions.fingerprint``). When the trial shows the change moved history, a new edition is cut, and its label
comes from HERE: every entry present in this list but not yet carried by the current edition is a DECLARED
rule change, and the new edition reads "rule change: ‹label›". A change nobody declared is published marked
"unexplained change" and pages the operator — so a PR that changes call logic declares itself here.

HOW TO DECLARE A RULE CHANGE (in the same PR as the change):

1. Append ONE ``RuleEntry`` to ``RULE_LOG`` — at the END, never in the middle, never edited or removed later
   (an edition stores the ids it was cut with; an id that vanished or changed meaning would mislabel it):
   - ``id``: short, unique, stable, kebab-case, e.g. ``"2026-11-insider-cluster-window"``;
   - ``date``: the day the PR merges;
   - ``label``: a few plain words the Scoreboard and Slack show after "rule change:", e.g. "insider cluster
     window 30 -> 45 days";
   - ``description``: one plain-English sentence on what changed and why.
2. Nothing else: the next nightly editions run (or ``python -m editions.run cut``) runs the trial and cuts —
   or, when the change moves no card, records ``trial_no_change`` and the entry rides the next edition.

A rule-only edit here does not start a trial by itself: this file is left out of the engine fingerprint
(``editions.fingerprint.EXCLUDED_SOURCES``) — it records changes, it is not logic. The log starts empty on
purpose: nothing earlier is backfilled (Edition 1 was cut before the log existed and carries no ids).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RuleEntry:
    """One declared change to the call logic."""

    id: str
    date: date
    label: str
    description: str


# Append-only. See the module docstring for how to declare a change.
RULE_LOG: tuple[RuleEntry, ...] = (
    RuleEntry(
        id="2026-10-silent-links",
        date=date(2026, 10, 26),
        label="Context and Bench links never count toward the call",
        description=(
            "A company the operator places in a thesis's Context or Bench link stays visible but is "
            "removed from the call's inputs, so its signals never warm, arm or veto the card."
        ),
    ),
    RuleEntry(
        id="2026-10-13d-holder-screen",
        date=date(2026, 10, 28),
        label="13Ds from control holders and insiders no longer fire",
        description=(
            "A Schedule 13D whose filer holds 50% or more of the class, or is an officer or director of "
            "the company on its own Form 4s, stays visible but no longer fires the activist-stake "
            "conviction, because a parent, founder or CEO is not an outside party committing new capital."
        ),
    ),
    RuleEntry(
        id="2026-11-market-holiday-calendar",
        date=date(2026, 10, 31),
        label="market holidays are not sessions",
        description=(
            "The session calendar now skips the NYSE's full-day closures, so the nightly no longer runs or "
            "pages on a market holiday, entry-window and split session counts no longer count one, and a "
            "price bar dated a holiday is neither stored nor read."
        ),
    ),
    RuleEntry(
        id="2026-11-entry-window-next-session",
        date=date(2026, 10, 31),
        label="a trigger counts only while the next session is inside its window",
        description=(
            "A trigger is live on a night only while the first market session after that night falls on or "
            "before its window's last day, so no card shows an arm, a warm-up or a risk on a night whose "
            "next session is already outside the window."
        ),
    ),
    RuleEntry(
        id="2026-11-filings-dated-by-acceptance",
        date=date(2026, 11, 2),
        label="8-Ks, 13D/Gs and revenue count from when they appeared on EDGAR",
        description=(
            "An 8-K, Schedule 13D/G or 10-Q/10-K revenue filing now counts from the market day SEC accepted "
            "it, not from its filing date, so a filing submitted after 5:30 pm ET (which SEC dates the next "
            "business day) counts on the night it appeared, on the live call and on every history view."
        ),
    ),
)


def _log(log: tuple[RuleEntry, ...] | None) -> tuple[RuleEntry, ...]:
    return RULE_LOG if log is None else log  # read at call time, never frozen into a default


def rule_ids(log: tuple[RuleEntry, ...] | None = None) -> list[str]:
    """Every declared entry's id, in log order — what an edition stores as "cut with"."""
    return [e.id for e in _log(log)]


def declared_since(
    carried: Collection[str], log: tuple[RuleEntry, ...] | None = None
) -> list[RuleEntry]:
    """The entries in ``log`` the current edition does not carry yet (``carried`` = the ids it was cut with,
    plus the ids any ``trial_no_change`` on it recorded) — the declared changes a cut now would be labeled
    with, in log order."""
    seen = set(carried)
    return [e for e in _log(log) if e.id not in seen]


def validate(log: tuple[RuleEntry, ...] | None = None) -> None:
    """Every id unique and non-blank, every label non-blank — the shape the editions store relies on."""
    ids = [e.id for e in _log(log)]
    if len(ids) != len(set(ids)):
        raise ValueError("RULE_LOG ids must be unique")
    for e in _log(log):
        if not e.id.strip() or not e.label.strip() or not e.description.strip():
            raise ValueError(f"RULE_LOG entry {e.id!r} needs an id, a label and a description")
