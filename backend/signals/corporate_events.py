"""Shared helpers for the 8-K item-code family (Band 03 S3) — used by BOTH corporate detectors.

``corporate_catalyst`` (trigger side) and ``corporate_risk`` (risk side) read the same
``fact_corporate_event`` tape through the same policy map (``CallConfig.corporate_event_items``);
this module holds the one item-matching walk and the display labels so the two sides cannot drift.
(Unlike the insider buy/sell pair — which deliberately duplicate-with-pointer because they must not
couple — these two ship as one family over one table; sharing is the point.)

Deterministic throughout (#3): item-code set membership + date arithmetic, nothing else.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from domain.config import CallConfig, CorporateEventItemPolicy
from domain.enums import Role
from signals.common import entry_signal_is_live

# Display names for the SEC's 8-K item codes — PRESENTATION strings for labels (#6 "show the work"),
# never policy: firing/grading/scoring read only the policy map. Codes outside this dict label as
# the bare code (honest, never a guess).
ITEM_LABELS: dict[str, str] = {
    "1.01": "material definitive agreement",  # still a valid tape item (display name kept); no longer fires
    "5.02": "officer/director departure or appointment",
    "3.01": "listing-deficiency notice",
    "3.02": "unregistered equity sales (dilution)",
    "4.01": "auditor change",
    "4.02": "non-reliance on prior financials (restatement)",
    "1.03": "bankruptcy or receivership",
}


def item_label(item: str) -> str:
    return ITEM_LABELS.get(item, "corporate event")


def live_policy_items(
    facts: list[dict[str, Any]],
    asof: date,
    cfg: CallConfig,
    role: Role,
) -> list[tuple[dict[str, Any], str, CorporateEventItemPolicy]]:
    """The as-of-LIVE (fact, item, policy) triples for one side of the policy map.

    A fact contributes one triple per policy-mapped item it carries whose per-item liveness window
    (anchored on the filing date — ``valid_from = filed``) still covers ``asof``; unmapped items and
    NULL (unresolved) items contribute nothing — they stay on the tape (#9), the cut just doesn't
    read them. ``valid_from <= asof`` is already guaranteed by the as-of read; re-checked here so a
    caller feeding raw rows (the pure ``score`` tests) gets the same honesty. Output order is the
    input fact order (the as-of read's); callers pick deterministically via explicit sort keys.
    """
    out: list[tuple[dict[str, Any], str, CorporateEventItemPolicy]] = []
    for f in facts:
        if f["valid_from"] > asof:
            continue
        for item in f.get("items") or []:
            policy = cfg.corporate_event_items.get(item)
            if policy is None or policy.role is not role:
                continue
            if entry_signal_is_live(f["valid_from"], policy.liveness_days, asof):
                out.append((f, item, policy))
    return out
