"""PLATFORM VALUES (V2 W2a) — the pure rules for a company's share count and cash/burn: the ONE pick, who set a
value, the value's state, the correction rule and the machine's AUTO rule. No database, no I/O, no clock: every
date is a parameter (``docs/PLATFORM_VALUES.md``).

A platform value is one value per company for everyone (V2): it lives in the data tenant (``domain.scopes``), set
by the machine from a clean filing (``ratified_by='auto'``) or by the steward (``ratified_by=<email>``); a V1 row
carried over says ``operator``. Migration 0206 fixes that vocabulary on every new row.

THE ONE PICK (approved decision D1 (a)). The as-of read returns one row per filing (``source_ref``; the newest
version of each). ``pick_current`` chooses the value shown among them: the latest ``valid_from`` wins; on a date
tie the newest WRITE wins (``recorded_at``, then ``id``); a row dated after ``not_after`` is never picked. Every
reader of ``fact_shares_outstanding`` / ``fact_cash_burn`` that shows "the value" calls it
(``tests/dashboards/test_one_pick.py`` fails on a reader with its own rule). Before V2 each reader kept the first
row on a date tie, and rows arrive sorted by ``source_ref``, so the alphabetically first URL won.

THE STATE (``derive_state``): no value yet (with the reason) / from the filing / awaiting the steward / verified,
plus a newer candidate awaiting the steward beside a shown value (``pending``). A candidate is NEVER the value:
``DerivedState.meter_value`` is the picked fact or None, so "awaiting the steward" can never reach a meter.

Display only: neither table feeds a signal or the call (``signals/base.py``'s detector protocol has no accessor
for them), so nothing here can move a call, a verdict or a grade.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal, NamedTuple

FactType = Literal["shares_outstanding", "cash_burn"]

# The two platform value types, in display order, and the fact table each lives in.
FACT_TYPES: tuple[FactType, ...] = ("shares_outstanding", "cash_burn")
TABLE_OF: dict[FactType, str] = {
    "shares_outstanding": "fact_shares_outstanding",
    "cash_burn": "fact_cash_burn",
}


class ValueState(StrEnum):
    """The state of one platform value (the W2 plan's Q1 table)."""

    NO_VALUE = "no_value"  # nothing to show; ``reason`` says why
    FROM_FILING = "from_filing"  # the machine applied a clean filing value (``auto`` / NULL)
    AWAITING_STEWARD = "awaiting_steward"  # no value; a flagged candidate waits for the steward
    VERIFIED = "verified"  # the steward set it, or a V1 row carried over (``operator``)


Ratifier = Literal["machine", "carried", "steward"]

# The ``ratified_by`` vocabulary (migration 0206): the machine's word, the V1 word, and a steward's email (the
# same pattern as the database check: one ``@``, no whitespace).
MACHINE = "auto"
CARRIED = "operator"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")

# The reasons a value is missing (``DerivedState.reason`` when the state is ``no_value``). A "nothing" candidate's
# own reason (no-annual-filing, cover-not-located, cash-generative, ...) is passed through as it is.
NOT_EXTRACTED = (
    "not-extracted"  # no value and no candidate: the extractor has not run (or found no company)
)
DISMISSED = "dismissed"  # the newest candidate was dismissed by the steward
NOT_SHOWN = (
    "not-shown"  # defensive: a standing approve / correct whose fact the read does not return
)

# The decision actions that close a candidate while they stand (``platform_value_decision.action``).
CLOSING_ACTIONS: frozenset[str] = frozenset({"approve", "correct", "dismiss"})


def is_steward_email(ratified_by: str | None) -> bool:
    """Is ``ratified_by`` a steward's email (the vocabulary's third word)?"""
    return ratified_by is not None and _EMAIL.match(ratified_by) is not None


def ratifier_kind(ratified_by: str | None) -> Ratifier:
    """Who set a value: ``auto`` or NULL -> ``machine``; an email -> ``steward``; ``operator`` (and any other V1
    free text a shell once stored, which 0206's NOT VALID check leaves in place) -> ``carried``."""
    if ratified_by is None or ratified_by == MACHINE:
        return "machine"
    if is_steward_email(ratified_by):
        return "steward"
    return "carried"


# --- THE ONE PICK -----------------------------------------------------------------------------------------------


def pick_key(row: Mapping[str, Any]) -> tuple:
    """The pick's sort key (D1 (a)): ``(valid_from, the write instant, id)``.

    The write instant is the store's own: ``ingested_at`` on a public-clock row (the history read rewrites
    ``recorded_at`` to the public instant and keeps the write there), else ``recorded_at``. A row that does not
    carry a column sorts below one that does (a reader that keeps only the columns it shows still picks by date).
    The id compares as its canonical text, which orders UUIDs the way Postgres does."""
    written = row.get("ingested_at") or row.get("recorded_at")
    rid = row.get("id")
    return (
        row["valid_from"],
        (0,) if written is None else (1, written),
        "" if rid is None else str(rid),
    )


def pick_current(
    rows: Iterable[Mapping[str, Any]], *, not_after: date | None = None
) -> Mapping[str, Any] | None:
    """THE value shown among a company's per-filing rows (D1 (a)), or None for no rows.

    The latest ``valid_from`` wins; on a date tie the newest write wins (``recorded_at``, then ``id``:
    ``pick_key``). With ``not_after`` (today's market date for a live read), a row dated after it is never picked.
    The rows are the as-of read's (one per ``source_ref``, set-aside versions already skipped). Returns the row
    itself (identity kept), never a copy."""
    eligible = [r for r in rows if not_after is None or r["valid_from"] <= not_after]
    if not eligible:
        return None
    return max(eligible, key=pick_key)


# --- THE STATE --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedState:
    """One platform value's state (``derive_state``).

    ``value`` is the picked fact (or None); ``ratifier`` who set it (None with no value). ``awaiting`` is the
    flagged candidate waiting for the steward when there is no value; ``pending`` a NEWER undecided flagged
    candidate beside a shown value. ``reason`` is set only for ``no_value``. ``pinned``: the steward asked to
    review every future filing value of this company."""

    state: ValueState
    reason: str | None
    ratifier: Ratifier | None
    value: Mapping[str, Any] | None
    awaiting: Mapping[str, Any] | None
    pending: Mapping[str, Any] | None
    pinned: bool

    @property
    def meter_value(self) -> Mapping[str, Any] | None:
        """The only value a meter may use: the picked fact. A candidate (``awaiting`` / ``pending``) is never
        returned here — "awaiting the steward" is not used by meters, and this is where that is enforced.
        """
        if self.state in (ValueState.FROM_FILING, ValueState.VERIFIED):
            return self.value
        return None


def derive_state(
    *,
    picked: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
    candidate_decision: str | None,
    pinned: bool,
) -> DerivedState:
    """The state of one value (the W2 plan's Q1 table).

    ``picked`` = ``pick_current`` over the data tenant's per-filing rows (what the meters show); ``candidate`` =
    the NEWEST candidate for the company and type (``outcome`` / ``reason`` / ``event_date``), or None;
    ``candidate_decision`` = the action of the STANDING decision that names it (``approve`` / ``correct`` /
    ``dismiss``), or None when it is undecided (never decided, or its decision was undone); ``pinned`` = the
    newest pin event is ``pin``.

    - a value: ``from_filing`` (``auto`` / NULL) or ``verified`` (``operator`` = carried, an email = steward); plus
      ``pending`` = the candidate when it is an undecided flag dated after the value's ``valid_from``;
    - no value, no candidate: ``no_value`` / ``not-extracted``;
    - no value, a "nothing" candidate: ``no_value`` / its own reason;
    - no value, an undecided flag: ``awaiting_steward``;
    - no value, a dismissed flag: ``no_value`` / ``dismissed``."""
    undecided_flag = (
        candidate is not None
        and candidate.get("outcome") == "flag"
        and candidate_decision not in CLOSING_ACTIONS
    )
    if picked is not None:
        kind = ratifier_kind(picked.get("ratified_by"))
        pending = None
        if undecided_flag and candidate.get("event_date") is not None:
            if candidate["event_date"] > picked["valid_from"]:
                pending = candidate
        state = ValueState.FROM_FILING if kind == "machine" else ValueState.VERIFIED
        return DerivedState(state, None, kind, picked, None, pending, pinned)
    if candidate is None:
        return DerivedState(ValueState.NO_VALUE, NOT_EXTRACTED, None, None, None, None, pinned)
    if candidate.get("outcome") == "nothing":
        reason = candidate.get("reason") or NOT_EXTRACTED
        return DerivedState(ValueState.NO_VALUE, reason, None, None, None, None, pinned)
    if undecided_flag:
        return DerivedState(ValueState.AWAITING_STEWARD, None, None, None, candidate, None, pinned)
    reason = DISMISSED if candidate_decision == "dismiss" else NOT_SHOWN
    return DerivedState(ValueState.NO_VALUE, reason, None, None, None, None, pinned)


# --- THE CORRECTION RULE (Q4, D1 (a)) ---------------------------------------------------------------------------


class CorrectionVerdict(NamedTuple):
    """Would a steward's new value be shown? ``reason``: ``wins`` / ``after_today`` / ``older_than_shown``;
    ``blocking`` = the row that would still be shown instead (``older_than_shown`` only)."""

    wins: bool
    blocking: Mapping[str, Any] | None
    reason: Literal["wins", "after_today", "older_than_shown"]


def correction_wins(
    winners: Iterable[Mapping[str, Any]], new_row: Mapping[str, Any], today: date
) -> CorrectionVerdict:
    """Would ``new_row`` (``source_ref`` + ``valid_from``) be the value shown once written?

    ``winners`` = the per-filing rows the meter reads now (record clock, as of ``today``). The new row is the
    NEWEST write (under the company lock no other writer runs), so it replaces the winner of its own filing and,
    by ``pick_current``'s key, is picked exactly when its date is on or after every OTHER filing's date (a date
    tie goes to the newer write: D1 (a)). A date after ``today`` is never shown (``after_today``). This is the
    same key every meter picks with, so a correction that passes cannot be silently ignored."""
    if new_row["valid_from"] > today:
        return CorrectionVerdict(False, None, "after_today")
    others = [w for w in winners if w.get("source_ref") != new_row.get("source_ref")]
    best = pick_current(others, not_after=today)
    if best is not None and best["valid_from"] > new_row["valid_from"]:
        return CorrectionVerdict(False, best, "older_than_shown")
    return CorrectionVerdict(True, None, "wins")


# --- THE MACHINE'S AUTO RULE (Q6, D3 (a)) -----------------------------------------------------------------------


class MachineDecision(NamedTuple):
    """What the machine does with one clean AUTO value: ``apply`` (write a fact ``auto``), ``candidate`` (write a
    flagged candidate carrying ``flags``) or ``skip`` (write nothing). ``reason``: ``pinned`` (candidate),
    ``already_on_file`` / ``older_than_on_file`` / ``after_today`` (skip), None (apply)."""

    action: Literal["apply", "candidate", "skip"]
    reason: str | None
    flags: tuple[str, ...]


def machine_action(
    *,
    source_ref: str,
    event_date: date,
    stored_refs: Collection[str],
    shown: Mapping[str, Any] | None,
    pinned: bool,
    today: date,
) -> MachineDecision:
    """The AUTO rule (the W2 plan's Q6; D3 (a)), in order:

    1. a value dated after ``today`` is never written (it could never be shown): skip ``after_today``;
    2. a pinned company: a flagged candidate with flag ``pinned``, never a fact;
    3. a filing with ANY stored version (``stored_refs`` includes set-aside versions, so a filing whose approve the
       steward undid is never re-applied): skip ``already_on_file``;
    4. a date on or before the shown value's (``shown`` = ``pick_current``'s row): skip ``older_than_on_file``;
    5. otherwise apply, whoever set the shown value (a strictly newer filing replaces a verified one: D3 (a)).
    """
    if event_date > today:
        return MachineDecision("skip", "after_today", ())
    if pinned:
        return MachineDecision("candidate", "pinned", ("pinned",))
    if source_ref in stored_refs:
        return MachineDecision("skip", "already_on_file", ())
    if shown is not None and event_date <= shown["valid_from"]:
        return MachineDecision("skip", "older_than_on_file", ())
    return MachineDecision("apply", None, ())


__all__ = [
    "FactType",
    "FACT_TYPES",
    "TABLE_OF",
    "ValueState",
    "Ratifier",
    "MACHINE",
    "CARRIED",
    "NOT_EXTRACTED",
    "DISMISSED",
    "NOT_SHOWN",
    "CLOSING_ACTIONS",
    "is_steward_email",
    "ratifier_kind",
    "pick_key",
    "pick_current",
    "DerivedState",
    "derive_state",
    "CorrectionVerdict",
    "correction_wins",
    "MachineDecision",
    "machine_action",
]
