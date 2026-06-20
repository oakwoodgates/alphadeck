from __future__ import annotations

from decimal import Decimal


def to_float(value: str | Decimal | float | None) -> float | None:
    """Coerce a DB numeric or a filing/CSV string to ``float``; ``None`` for missing (``None`` or "").

    Unifies three former local ``_to_float`` helpers (the price + Form 4 ingest parsers and the DB-row
    mapper). The tolerant ``not in (None, "")`` predicate preserves both prior behaviors: the ingest
    parsers' empty-string -> ``None`` and the mapper's ``None`` -> ``None`` (a ``Decimal``/``float`` is
    never ``== ""``, so the empty-string check is a no-op for the mapper's numeric inputs).
    """
    return float(value) if value not in (None, "") else None
