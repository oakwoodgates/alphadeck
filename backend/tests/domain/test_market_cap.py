"""The market-cap bands (``domain/market_cap.py``): every edge, the blank cases, and a drift guard against the
table in ``CLAUDE.md`` the module mirrors. Pure: no DB."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from domain.market_cap import CAP_TIER_FLOORS, CapTier, cap_tier

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("usd", "tier"),
    [
        # nano: < $50M
        (0.01, CapTier.NANO),
        (1.0, CapTier.NANO),
        (49_999_999.99, CapTier.NANO),
        # micro: $50M - $300M ($50M exactly is micro)
        (50_000_000, CapTier.MICRO),
        (50_000_000.01, CapTier.MICRO),
        (299_999_999.99, CapTier.MICRO),
        # small: $300M - $2B ($300M exactly is small)
        (300_000_000, CapTier.SMALL),
        (1_999_999_999.99, CapTier.SMALL),
        # mid: $2B - $10B ($2B exactly is mid)
        (2_000_000_000, CapTier.MID),
        (9_999_999_999.99, CapTier.MID),
        # large: >= $10B ($10B exactly is large)
        (10_000_000_000, CapTier.LARGE),
        (3_000_000_000_000, CapTier.LARGE),
    ],
)
def test_every_edge_of_every_band(usd, tier):
    assert cap_tier(usd) is tier


def test_a_cap_on_a_boundary_belongs_to_the_band_that_starts_there():
    for band, floor in CAP_TIER_FLOORS:
        assert cap_tier(floor) is band
        assert cap_tier(floor - 0.01) is not band


@pytest.mark.parametrize(
    "bad", [None, 0, 0.0, -1.0, -50_000_000, float("nan"), float("inf"), float("-inf")]
)
def test_no_cap_to_band_gets_no_tier_never_nano(bad):
    assert cap_tier(bad) is None


def test_it_takes_a_decimal_or_an_int_as_a_postgres_numeric_arrives():
    assert cap_tier(Decimal("299999999.99")) is CapTier.MICRO
    assert cap_tier(Decimal("300000000")) is CapTier.SMALL
    assert cap_tier(2_000_000_000) is CapTier.MID


def test_the_bands_are_five_in_order_smallest_first():
    assert [t.value for t in CapTier] == ["nano", "micro", "small", "mid", "large"]
    assert [t for t, _ in CAP_TIER_FLOORS] == list(CapTier)[1:]
    floors = [f for _, f in CAP_TIER_FLOORS]
    assert floors == sorted(floors) and len(set(floors)) == len(floors)


def _usd(text: str) -> float:
    m = re.fullmatch(r"\$(\d+(?:\.\d+)?)([MB])", text.strip())
    assert m, text
    return float(m.group(1)) * {"M": 1e6, "B": 1e9}[m.group(2)]


def test_the_bands_mirror_the_table_in_claude_md():
    """The line ``CLAUDE.md`` states ("nano < $50M · micro $50M–$300M · ...") and the code agree on every bound; if
    the operator moves a band, this fails until the module follows (they are defined once, here)."""
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "**nano**" in ln and "**large**" in ln)
    nano = re.search(r"\*\*nano\*\* < (\$\d+[MB])", line)
    ranges = re.findall(r"\*\*(micro|small|mid)\*\* (\$\d+[MB])–(\$\d+[MB])", line)
    large = re.search(r"\*\*large\*\* ≥ (\$\d+[MB])", line)
    assert nano and large and [r[0] for r in ranges] == ["micro", "small", "mid"]
    floors = {"micro": _usd(nano.group(1))}
    for (name, low, high), nxt in zip(ranges, ["small", "mid", "large"]):
        assert _usd(low) == floors[name]
        floors[nxt] = _usd(high)
    assert floors["large"] == _usd(large.group(1))
    assert {t.value: f for t, f in CAP_TIER_FLOORS} == floors
