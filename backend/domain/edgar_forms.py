"""EDGAR 8-K-family FORM TYPES — the one place the 8-K tape's stored set and its succession subset live.

Two readers, one list:

- **the ingest** (``ingest/edgar/submissions.form8k_filings``) STORES every form in ``CORPORATE_EVENT_FORMS``
  on the ``fact_corporate_event`` tape (#9 — a succession report exists and is evidence a person should be
  able to see); and
- **the event detectors** (``signals/corporate_events.event_facts``, which both ``corporate_risk`` and
  ``corporate_catalyst`` read through, live and in replay) SKIP every row whose form is in
  ``SUCCESSION_8K_FORMS`` — STORED, fired by nothing.

The T7 successor-CIK reader (``ingest/edgar/succession.py``) names the same set as its succession evidence.

**Why succession reports fire nothing (operator decision, 2026-10-17).** A successor issuer's first report
under Exchange Act Rule 12g-3 (an **8-K12B** for a 12(b) registrant, an **8-K12G3** for a 12(g) one) or Rule
15d-5 (an **8-K15D5**) carries ordinary 8-K item codes, but there they describe the REORGANIZATION ITSELF:
3.01 is the listing TRANSFER (not a deficiency), 3.02 / 3.03 the share exchange, 5.02 the officers carrying
over, 5.03 the new charter. MEASURED on the two real filings: UROY's 8-K12B (2026-07-28) carries 1.01, 2.01,
2.02, 3.01, 3.02, 3.03, 5.01, 5.02, 5.03, 7.01, 9.01; REAX's (2026-08-24) 1.01, 2.01, 2.03, 3.01, 3.03, 5.01,
5.02, 5.03, 8.01, 9.01. Read as ordinary events they would fire the live ``corporate_risk`` (3.01 / 3.02 at
0.50) and feed the parked ``corporate_catalyst`` (5.02). The cut is by FORM, never by item: a plain 8-K
carrying the same items still fires exactly as before.

Value-free shared vocabulary (the ``domain/`` convention — ``feed_kinds.py``, ``enums.py``): no I/O, no
settings, importable from every layer without inverting a dependency (the pure detectors need no ingest
import to read it). Amendments ride along with their original form.
"""

from __future__ import annotations

# The ORDINARY current report and its amendment — what the 8-K tape stored before 2026-10-17, and the forms
# the event detectors read.
ORDINARY_8K_FORMS: frozenset[str] = frozenset({"8-K", "8-K/A"})

# The SUCCESSOR's first reports. 8-K12B = succession to a 12(b) (exchange-listed) registrant; 8-K12G3 =
# succession under Rule 12g-3 for a 12(g) registrant; 8-K15D5 = succession to a 15(d) reporting obligation
# (Rule 15d-5). Amendments ride along. STORED on the tape, SKIPPED by every event detector.
SUCCESSION_8K_FORMS: frozenset[str] = frozenset(
    {"8-K12B", "8-K12B/A", "8-K12G3", "8-K12G3/A", "8-K15D5", "8-K15D5/A"}
)

# Every form the 8-K item-code tape (``fact_corporate_event``) stores.
CORPORATE_EVENT_FORMS: frozenset[str] = ORDINARY_8K_FORMS | SUCCESSION_8K_FORMS


def is_succession_8k(form: str | None) -> bool:
    """True for a successor issuer's report (``SUCCESSION_8K_FORMS``) — a row the event detectors skip.
    ``None`` / an unknown string is NOT a succession report (it reads as an ordinary row; the stored
    ``form`` column is NOT NULL, so ``None`` only reaches here from a hand-built dict)."""
    return form in SUCCESSION_8K_FORMS
