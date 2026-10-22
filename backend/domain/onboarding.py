"""X1 — the staged cold-ingest's one shared value type (``pipeline/onboarding.py``).

Kept in ``domain`` so the two readers of a nightly pass's onboarding treatment can share it without an import
cycle: ``pipeline.ingest_thesis`` (whose legs read only the window) and ``pipeline.fetch_ahead`` (which must
pre-fetch exactly — and only — what those legs will read).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class OnboardingWindow:
    """How a nightly pass treats ONE member whose history is still loading in the onboarding job: its job
    ``status`` (stamped on the result) and the ``filed_since`` date its per-filing legs read from — or,
    ``defer``, not walked at all tonight (past the pass's onboarding cap: no lock, no fetch, no write).
    """

    status: str
    filed_since: date
    defer: bool = False
