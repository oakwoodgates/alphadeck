"""Tier-1 dedup — the unified ``to_float`` coercer (formerly three local ``_to_float`` helpers).

Locks the one genuine behavior-merge of the slice: the tolerant ``not in (None, "")`` predicate must
preserve BOTH former behaviors across the union of input types —
- the ingest parsers' empty-CSV/filing-string ``""`` -> ``None`` (the load-bearing tolerant case), and
- the DB-row mapper's ``None`` -> ``None`` for ``Decimal``/``float`` columns,
while never coercing a falsy-but-present ``0`` into "missing".
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.coerce import to_float


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),  # the ingest parsers' empty cell -> None (would ValueError under `is not None`)
        ("5.0", 5.0),
        ("-3.5", -3.5),
        ("0", 0.0),
        (Decimal("5"), 5.0),  # the DB-mapper path (numeric column)
        (Decimal("0"), 0.0),
        (0, 0.0),
        (0.0, 0.0),
        (2.5, 2.5),
    ],
)
def test_to_float(value, expected):
    assert to_float(value) == expected


def test_missing_is_none_present_zero_is_zero():
    """Missing (``None`` / ``""``) -> ``None``; a present ``0`` stays ``0.0`` (not folded into missing)."""
    assert to_float(None) is None
    assert to_float("") is None
    assert to_float(0) == 0.0
    assert to_float(0) is not None
