"""Read-only display signals — per-name computed indicators for the panel, OFF the call path.

Importing the package loads the members in their display (render) order; each module registers its
own descriptor. Registration is explicit and deterministic without turning this seam into a dynamic
plugin framework — the same discipline as the detector registry it sits beside, with the opposite
authority: a display member can never arm, veto, or grade. See ``docs/DISPLAY_SIGNALS.md``.
"""

from signals.display.registry import registered_display_members

# Registration order is the panel's render order and must remain behavior-stable.
# isort: off
from signals.display import sma as sma

# isort: on

__all__ = [
    "registered_display_members",
    "sma",
]
