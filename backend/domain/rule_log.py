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
RULE_LOG: tuple[RuleEntry, ...] = ()


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
