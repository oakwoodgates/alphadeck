"""WHERE a Key-1 conviction came from — computed by a screen, or attached to a real actor.

The call machinery already knows a conviction's KIND. It does not know its PROVENANCE CLASS, and the two
are not the same question. ``Kind.CATALYST`` is emitted by three different detectors:

- ``catalyst_conviction`` — an operator-RATIFIED ``fact_catalyst`` row (a person decided this matters);
- ``corporate_catalyst`` — an 8-K item code (the issuer DISCLOSED a specific event);
- ``revenue_acceleration`` — a SCREEN over reported quarterly revenue (nobody said anything; a rule fired).

Those are different claims about the world, and the difference is the whole of H1: a screen tells you what
the business did last quarter, not who is buying it now. A fundamentals screen is broad by nature — it
covers 13-27% of each large basket — which is why the computed screen, not the insider buy, is the single
largest source of arms on the record (465 of 969 armed member-nights vs 403).

**Classified by PROVENANCE SOURCE, never by label prefix.** The tempting shortcut is to match the two
computed detectors' label prefixes and call everything else ratified, but the ratified catalyst's label is
``fact["label"]`` — free operator text with no prefix at all — so the match would have to run the other
way round, on copy. Copy gets edited; a CallCard expression reword shipped this quarter. ``Provenance.source``
is a structured constant set by the detector, it already rides the event, and it needs no schema change.

**Nothing here reads ``CallConfig``.** This module answers "what class is this event", the assembler decides
what to do about it. That keeps the detector/assembler dial partition clean: a classifier that took a cfg
would put a policy decision inside ``signals/``.
"""

from __future__ import annotations

from domain.signal import SignalEvent

# The provenance sources that mean "a rule fired over reported numbers" rather than "someone acted" or
# "someone ratified". ONE entry today: the XBRL fundamentals screen behind `revenue_acceleration`.
#
# Deliberately NOT here:
#   "form4"  — a named insider spent their own money        (an actor)
#   "13d"    — a named holder crossed 5% with intent        (an actor)
#   "8-k"    — the issuer disclosed a specific event        (a disclosure, not a screen)
#   ratified fact sources ("doe_usaspending", "ratified", …) — the operator decided  (a ratification)
COMPUTED_CONVICTION_SOURCES: frozenset[str] = frozenset({"xbrl"})


def is_computed_conviction(event: SignalEvent) -> bool:
    """Was this event produced by a SCREEN over reported numbers?

    True only when the event carries provenance and EVERY source is a computed one. "Every", not "any", is
    the conservative direction for a classifier whose one consumer makes arming HARDER: a mixed-provenance
    conviction (a screen corroborated by a filing) is treated as not-purely-computed, so the stricter rule
    does not reach it. An event with no provenance cannot be a fired trigger at all (``SignalEvent``'s own
    validator refuses one), so the empty case is unreachable and reads False rather than vacuously True.
    """
    sources = {p.source for p in event.provenance}
    return bool(sources) and sources <= COMPUTED_CONVICTION_SOURCES
