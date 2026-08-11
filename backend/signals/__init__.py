"""Built-in signal detectors.

Importing the package loads the current per-security detectors in their historical pipeline order; each
module registers its own descriptor. Registration is explicit and deterministic without turning this
greenfield seam into a dynamic plugin framework.
"""

from signals.registry import registered_detectors

# Registration order is observable in CallCard trigger/risk lists and must remain behavior-stable. New
# detectors append LAST (revenue_acceleration §2.2, then breakout_52w §2.3) so the existing detectors keep
# their order — the addition only ever adds a trigger row on names that carry the new signal; it never
# reorders an existing card. breakout_52w emits kind=TECHNICAL_BREAKOUT (a variant), so a name can fire it
# ALONGSIDE volume_breakout; the assembler takes the STRONGEST co-firing confirmation grade (call_grade=max).
# isort: off
from signals import insider_conviction as insider_conviction
from signals import catalyst_conviction as catalyst_conviction
from signals import volume_breakout as volume_breakout
from signals import dilution_clock as dilution_clock
from signals import revenue_acceleration as revenue_acceleration
from signals import breakout_52w as breakout_52w

# isort: on

__all__ = [
    "breakout_52w",
    "catalyst_conviction",
    "dilution_clock",
    "insider_conviction",
    "registered_detectors",
    "revenue_acceleration",
    "volume_breakout",
]
