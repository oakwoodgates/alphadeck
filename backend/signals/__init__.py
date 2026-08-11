"""Built-in signal detectors.

Importing the package loads the current per-security detectors in their historical pipeline order; each
module registers its own descriptor. Registration is explicit and deterministic without turning this
greenfield seam into a dynamic plugin framework.
"""

from signals.registry import registered_detectors

# Registration order is observable in CallCard trigger/risk lists and must remain behavior-stable. New
# detectors append LAST (revenue_acceleration, §2.2) so the existing four keep their order — the addition
# only ever adds a trigger row on names that carry the new fact family; it never reorders an existing card.
# isort: off
from signals import insider_conviction as insider_conviction
from signals import catalyst_conviction as catalyst_conviction
from signals import volume_breakout as volume_breakout
from signals import dilution_clock as dilution_clock
from signals import revenue_acceleration as revenue_acceleration

# isort: on

__all__ = [
    "catalyst_conviction",
    "dilution_clock",
    "insider_conviction",
    "registered_detectors",
    "revenue_acceleration",
    "volume_breakout",
]
