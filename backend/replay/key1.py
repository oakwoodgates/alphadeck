from __future__ import annotations

from domain.call import TriggerRef
from domain.enums import Kind

# WHICH KEY-1 turned the lock — the breadth field that makes 82% of arm episodes distinguishable from
# the 18% that armed alone, and the slice H1 is asked in.
#
# The hard part is that ``Kind.CATALYST`` is emitted by THREE detectors making three different claims:
# an operator RATIFIED a commitment, the issuer DISCLOSED an 8-K item, or a SCREEN fired over reported
# revenue. Those are not the same evidence, and the whole of H1 is the difference between them.
#
# CLASSIFIED BY ``Provenance.source``, never by a label prefix. The tempting shortcut is to match the two
# computed detectors' label text, but the RATIFIED catalyst's label is ``fact["label"]`` — free operator
# prose with no prefix at all — so the match would have to run the other way round, on copy. Copy gets
# edited (a CallCard expression reword shipped this quarter), and a reworded label silently reclassifying
# a year of episodes is exactly the kind of quiet wrongness this field exists to prevent.
#
# ``TriggerRef`` carries ``kind`` and ``sources`` and no detector name, so this reads only what is already
# on the recorded card — no schema, enum or card change.

_CATALYST_BY_SOURCE: dict[str, str] = {
    "xbrl": "revenue_accel",  # signals/revenue_acceleration.py — a screen over reported revenue
    "8-k": "corporate_catalyst",  # signals/corporate_catalyst.py — an SEC item code
}

# Precedence for the SINGLE-valued slice, strongest evidentiary claim first: a named actor who spent their
# own money, then a named holder declaring intent, then an operator ratification, then a disclosed event,
# then a screen, then the thesis-level fallback. An arm carrying BOTH an insider buy and a revenue
# re-acceleration bins as "insider" — which is the reading §4.2's measurement needs ("of the arms carrying
# revenue re-acceleration and NO insider buy..."). ``key1_sources`` keeps the full set, because 70 armed
# member-nights on the record carry two, and collapsing them to one would lose that.
_PRECEDENCE: tuple[str, ...] = (
    "insider",
    "activist",
    "ratified_catalyst",
    "corporate_catalyst",
    "revenue_accel",
    "theme",
)


def trigger_source(trigger: TriggerRef) -> str | None:
    """One conviction trigger's Key-1 source label, or ``None`` when it is not a Key-1 kind."""
    if trigger.kind is Kind.INSIDER:
        return "insider"
    if trigger.kind is Kind.ACTIVIST_STAKE:
        return "activist"
    if trigger.kind is Kind.THEME_CONVICTION:
        return "theme"
    if trigger.kind is Kind.CATALYST:
        for p in trigger.sources:
            if p.source in _CATALYST_BY_SOURCE:
                return _CATALYST_BY_SOURCE[p.source]
        # every other source on a CATALYST is an operator-ratified fact_catalyst row, whose ``source`` is
        # the fact's own ("doe_usaspending", "ratified", ...) and is therefore an open set
        return "ratified_catalyst"
    return None


def key1_sources(triggers: list[TriggerRef]) -> list[str]:
    """Every Key-1 source present on this member's arm-date evidence, in precedence order."""
    found = {s for t in triggers if (s := trigger_source(t)) is not None}
    return [s for s in _PRECEDENCE if s in found]


def key1_source(triggers: list[TriggerRef]) -> str | None:
    """The single source to slice on — the strongest claim present. ``None`` when no Key-1 fired."""
    ordered = key1_sources(triggers)
    return ordered[0] if ordered else None


def co_arm_bucket(newly_armed_that_session: int) -> str:
    """The §4.3 breadth bucket, keyed on the SIZE OF THE GROUP that armed together — not on the co-arm
    count excluding self. The distinction matters and is easy to get backwards: ``"alone"`` is a group of
    one, so ``"2-3"`` begins at a group of two (one co-arm), which is why the published buckets are
    contiguous with no gap at a single co-arm."""
    n = max(newly_armed_that_session, 1)
    if n == 1:
        return "alone"
    if n <= 3:
        return "2-3"
    if n <= 7:
        return "4-7"
    return "8+"
