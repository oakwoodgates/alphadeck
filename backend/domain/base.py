from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Base for every domain schema.

    Strict by default (``extra="forbid"``): an unknown field is an error, never silently dropped —
    a typo'd detector field (e.g. ``half_life`` for ``alpha_half_life_days``) must fail loudly, not
    quietly null the value. The core schemas are the backend↔frontend contract (CLAUDE.md).
    """

    model_config = ConfigDict(extra="forbid")
