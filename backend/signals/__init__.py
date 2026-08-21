"""Built-in signal detectors.

Importing the package loads the current per-security detectors in their historical pipeline order; each
module registers its own descriptor. Registration is explicit and deterministic without turning this
greenfield seam into a dynamic plugin framework.
"""

from signals.registry import registered_detectors

# Registration order is observable in CallCard trigger/risk lists and must remain behavior-stable. New
# detectors append LAST (revenue_acceleration §2.2, breakout_52w §2.3, breakdown §2.5/§3.3, then
# insider_sell Band 03 S1, then the Band 03 S3 corporate pair) so the existing detectors keep their order —
# the addition only ever adds a trigger/risk row on names that carry the new signal; it never reorders an
# existing card. breakout_52w emits kind=TECHNICAL_BREAKOUT (a variant), so a name can fire it ALONGSIDE
# volume_breakout; the assembler takes the STRONGEST co-firing confirmation grade (call_grade=max).
# breakdown registers TWO RISK detectors (core + flip) — the grade-aware structural de-arm; both emit
# kind=BREAKDOWN carrying which grade they de-arm on SignalEvent.dearm_grade (breakdown_dearm_enabled:
# default ON since 2026-08-15). insider_sell is a RISK detector (kind=INSIDER_SELL, capped sub-veto;
# master switch default ON since 2026-08-19). corporate_catalyst (ENTRY, kind=CATALYST off the 8-K item
# tape) + corporate_risk (RISK, kind=CORPORATE_RISK) are the Band 03 S3 pair — EACH behind its OWN master
# switch, and the two now differ: corporate_risk is ON (2026-08-17) while corporate_catalyst stays OFF
# (parked — 5.02-only since the 1.01 demotion, so it emits nothing and registering it changes no card).
# share_creep (Band 03 S4) is a RISK detector off the quarterly XBRL shares series (kind=DILUTION_RISK —
# REUSED: realized issuance beside dilution_clock's potential convert overhang), switch default ON since
# 2026-08-19. activist_stake (Band 03 S5) is an ENTRY trigger (kind=ACTIVIST_STAKE — a new conviction
# kind: SC 13D originals fire CORE off the fact_activist_stake ownership tape; 13G/amendments never
# fire), appended LAST, switch default ON since 2026-08-20.
# isort: off
from signals import insider_conviction as insider_conviction
from signals import catalyst_conviction as catalyst_conviction
from signals import volume_breakout as volume_breakout
from signals import dilution_clock as dilution_clock
from signals import revenue_acceleration as revenue_acceleration
from signals import breakout_52w as breakout_52w
from signals import breakdown as breakdown
from signals import insider_sell as insider_sell
from signals import corporate_catalyst as corporate_catalyst
from signals import corporate_risk as corporate_risk
from signals import share_creep as share_creep
from signals import activist_stake as activist_stake

# isort: on

__all__ = [
    "activist_stake",
    "breakdown",
    "breakout_52w",
    "catalyst_conviction",
    "corporate_catalyst",
    "corporate_risk",
    "dilution_clock",
    "insider_conviction",
    "insider_sell",
    "registered_detectors",
    "revenue_acceleration",
    "share_creep",
    "volume_breakout",
]
