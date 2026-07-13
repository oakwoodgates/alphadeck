"""Built-in signal detectors.

Importing the package loads the four current per-security detectors in their historical pipeline order;
each module registers its own descriptor. Registration is explicit and deterministic without turning this
greenfield seam into a dynamic plugin framework.
"""

from signals.registry import registered_detectors

# Registration order is observable in CallCard trigger/risk lists and must remain behavior-stable.
# isort: off
from signals import insider_conviction as insider_conviction
from signals import catalyst_conviction as catalyst_conviction
from signals import volume_breakout as volume_breakout
from signals import dilution_clock as dilution_clock

# isort: on

__all__ = [
    "catalyst_conviction",
    "dilution_clock",
    "insider_conviction",
    "registered_detectors",
    "volume_breakout",
]
