"""The Slack PUSH POLICY (Q13, operator decision 2026-10-17: O3 + O4 + SPAC grouping) — pure, no DB, no I/O.

Inverse loudness (#7) used to live in one line: "push iff the move lands IN ``armed``". That made any verdict
change while a thesis STAYED armed read like a new arm — the loud red "ARMED" header — even when the setup got
WEAKER (MEASURED: Digital Asset Treasury Complex 2026-09-23, core_entry -> starter_entry with 4 -> 2 armed names;
Nuclear Buildout 2026-10-07, core_entry -> starter_entry with 4 -> 3). Loudness is reserved for a real arm, so
an armed -> armed move is now CLASSIFIED before it is formatted.

THE RULE (``classify``), over a ``TransitionEvent`` — emitted, as before, only when the state or verdict moved
against the call-of-record at or before the as-of (``pipeline.daily``'s F2 baseline):

- **Not into Armed** (de-arm, warming, incubating, managing) -> ``record_only``: logged, never pushed. UNCHANGED
  from before Q13 — a de-arm has never been pushed (it is a record, not a nag), and Incubating never nags.
- **Into Armed from any other state** -> ``armed``: LOUD, the existing "🔴 ARMED" push. UNCHANGED.
- **Armed -> Armed** compares two numbers read straight off the CallCard:
    * the thesis call's GRADE = ``CallCard.entry_grade`` (the weaker key, the one that drives the verdict:
      ``flip`` < ``core``; ``None`` ranks lowest), and
    * the ARMED-NAME COUNT = the distinct ``security_id``s in ``CallCard.armed_members``.
  Then:
    * something rose and nothing fell -> ``upgrade``: LOUD (the ARMED style, with an up marker and what improved);
    * something fell and nothing rose -> ``downgrade``: QUIET ("still armed, weaker: core -> flip; 4 -> 3 names");
    * one rose and the other fell (grade up but names down, or the reverse) -> ``mixed``: QUIET, both stated —
      a weaker side never rides a loud header;
    * neither moved (e.g. ``flip_only`` <-> ``starter_entry``: a validity-window change at the same grade) ->
      ``changed``: a QUIET note.

What this rule does NOT do: it never creates a push. A names-only change while the verdict holds still emits no
``TransitionEvent`` (that baseline is unchanged); a name that arms inside an already-armed thesis reaches the
operator through the pass-end DIGEST instead (O4 — ``DigestEvent``, one quiet message per pass, never a "nothing
new" ping). The grade and count are carried on the event by ``pipeline.push_policy`` (enrich in daily, the
notifier stays PURE — the #309 seam).
"""

from __future__ import annotations

from typing import Literal

PushKind = Literal["armed", "upgrade", "downgrade", "mixed", "changed", "record_only"]

# The two kinds that earn the loud header. Everything else armed -> armed is quiet; record_only never pushes.
LOUD_KINDS: frozenset[str] = frozenset({"armed", "upgrade"})
QUIET_KINDS: frozenset[str] = frozenset({"downgrade", "mixed", "changed"})

_ARMED = "armed"
_GRADE_RANK = {"flip": 1, "core": 2}


def grade_rank(grade: str | None) -> int:
    """``flip`` = 1 < ``core`` = 2; an absent or unknown grade ranks 0 (an armed card always carries one)."""
    return _GRADE_RANK.get(grade or "", 0)


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def grade_direction(from_grade: str | None, to_grade: str | None) -> int:
    """+1 the grade rose, -1 it fell, 0 it held."""
    return _sign(grade_rank(to_grade) - grade_rank(from_grade))


def names_direction(from_armed: int | None, to_armed: int | None) -> int:
    """+1 more armed names, -1 fewer, 0 the same — or unknown (a count the event does not carry never moves)."""
    if from_armed is None or to_armed is None:
        return 0
    return _sign(to_armed - from_armed)


def classify(
    *,
    from_state: str,
    to_state: str,
    from_grade: str | None = None,
    to_grade: str | None = None,
    from_armed: int | None = None,
    to_armed: int | None = None,
) -> PushKind:
    """The push class of one state/verdict move (the module docstring is the rule, in words)."""
    if to_state != _ARMED:
        return "record_only"
    if from_state != _ARMED:
        return "armed"
    g = grade_direction(from_grade, to_grade)
    n = names_direction(from_armed, to_armed)
    up = g > 0 or n > 0
    down = g < 0 or n < 0
    if up and down:
        return "mixed"
    if up:
        return "upgrade"
    if down:
        return "downgrade"
    return "changed"
