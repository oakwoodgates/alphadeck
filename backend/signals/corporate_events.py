"""Shared helpers for the 8-K item-code family (Band 03 S3) — used by BOTH corporate detectors.

``corporate_catalyst`` (trigger side) and ``corporate_risk`` (risk side) read the same
``fact_corporate_event`` tape through the same policy map (``CallConfig.corporate_event_items``);
this module holds the one item-matching walk and the display labels so the two sides cannot drift.
(Unlike the insider buy/sell pair — which deliberately duplicate-with-pointer because they must not
couple — these two ship as one family over one table; sharing is the point.)

SUCCESSION REPORTS ARE STORED, NEVER READ (operator decision, 2026-10-17). The tape also stores a
successor issuer's 8-K12B / 8-K12G3 / 8-K15D5 (``domain/edgar_forms.SUCCESSION_8K_FORMS``, #9), whose
items describe the reorganization itself (3.01 = the listing transfer, 5.02 = the carried-over officers).
``event_facts`` drops them by FORM — never by item — and every detector over this tape reads through it
(``live_policy_items`` does, so both sides do), on the live and the replay engine alike (they share these
functions; only the row source differs). A future reader of this tape (the deferred cadence counter)
must read through ``event_facts`` too.

Deterministic throughout (#3): form / item-code set membership + date arithmetic, nothing else.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from domain.config import CallConfig, CorporateEventItemPolicy
from domain.edgar_forms import is_succession_8k
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


def filed_date(fact: dict[str, Any]) -> date:
    """The date a card's "(filed …)" words show for a filing fact (8-K, 13D/G): SEC's FILING date, the stored
    ``filed`` column — never the re-dated ``valid_from`` (F7 dates the fire by the acceptance's market day, so an
    evening filing fires the night before its filing date; the label keeps saying what SEC says). A row with no
    ``filed`` (a raw-row test input) falls back to ``valid_from`` — identical before F7."""
    return fact.get("filed") or fact["valid_from"]


def event_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tape rows an event detector may read: every row EXCEPT a succession report (by form —
    ``domain.edgar_forms.is_succession_8k``). Order-preserving; the dropped rows stay on the tape (#9)
    and on the display surfaces, they just never fire, arm, veto or ride a signal's provenance."""
    return [f for f in facts if not is_succession_8k(f.get("form"))]


def live_policy_items(
    facts: list[dict[str, Any]],
    asof: date,
    cfg: CallConfig,
    role: Role,
) -> list[tuple[dict[str, Any], str, CorporateEventItemPolicy]]:
    """The as-of-LIVE (fact, item, policy) triples for one side of the policy map.

    A fact contributes one triple per policy-mapped item it carries whose per-item liveness window
    (anchored on the PUBLIC date — ``valid_from``: since F7 the acceptance's market day, else ``filed``)
    still covers ``asof``; unmapped items and
    NULL (unresolved) items contribute nothing — they stay on the tape (#9), the cut just doesn't
    read them. A succession report (8-K12B / 8-K12G3 / 8-K15D5) contributes nothing either, whatever
    its items (``event_facts``). ``valid_from <= asof`` is already guaranteed by the as-of read;
    re-checked here so a caller feeding raw rows (the pure ``score`` tests) gets the same honesty.
    Output order is the input fact order (the as-of read's); callers pick deterministically via
    explicit sort keys.
    """
    out: list[tuple[dict[str, Any], str, CorporateEventItemPolicy]] = []
    for f in event_facts(facts):
        if f["valid_from"] > asof:
            continue
        for item in f.get("items") or []:
            policy = cfg.corporate_event_items.get(item)
            if policy is None or policy.role is not role:
                continue
            if entry_signal_is_live(f["valid_from"], policy.liveness_days, asof):
                out.append((f, item, policy))
    return out
