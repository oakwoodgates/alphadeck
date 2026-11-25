"""``get_admin_onboarding``: the steward's Onboarding panel (``GET /admin/onboarding``, undated, read live).

One wire shape, one view. ``GET /admin/onboarding`` answers the same ``OnboardingReadoutOut`` the per-card read
(``GET /workbench/theses/{id}/onboarding``) does, only for the whole shared queue, so this module re-exports the
research view and its sections instead of copying them: the counts, the window the job yields to, the stalled and
paused notes, the last run, the first pending names and the latest done ones; ``section=pending`` and
``section=recent_done`` page the full lists in the API's order. In ``pending`` the API lists FAILED names first, then
the in-progress and queued ones. A failed name's Retry is on the Admin panel; this read changes nothing. The reply
carries no thesis (the queue is shared, not a card's).
"""

from __future__ import annotations

from ..research.onboarding import SECTIONS, view

__all__ = ["SECTIONS", "view"]
