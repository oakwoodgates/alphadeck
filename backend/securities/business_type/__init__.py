"""Business-type resolution — WHAT a company DOES, derived on read from stored identity.

The MONITOR taxonomy (Business-Type M1): the master's raw ``sector`` (EDGAR ``sicDescription``,
stored verbatim) rolls up to a two-level characterization — a ``BusinessType`` LEAF ("miner",
"utilities", …) and its ``BusinessSupersector`` ("materials", "energy_utilities", …) — plus the
royalty/streaming OVERLAY (a company-NAME tell: UROY/RPRX-class royalty houses sit in unrelated SIC
buckets, so SIC alone cannot see them). The cockpit watches the basket through it ("are the
utilities moving?").

DERIVE-ON-READ by design (the ``securities/origin.py`` ladder's shape): no computed label is stored
on the master — the stored row keeps the raw evidence (``sector``), and the classification can be
re-tuned with zero re-enrich. The ONE stored exception is the operator's per-security re-tag
(``security_master.business_type``, migration 0033 — the ``price_symbol`` store-on-diff idiom),
which is passed in here as ``override`` and always wins.

THE DATA IS NOT IN THIS MODULE. The maps live beside it as operator-editable files (the
``llm/prompts/*.md`` pattern — see README.md in this folder):

- ``sic_map.csv``          — SIC description -> leaf (all live strings, grouped + commented)
- ``supersectors.csv``     — leaf -> super-sector (must be TOTAL over ``BusinessType``)
- ``royalty_patterns.txt`` — the overlay's company-name regexes
- ``overrides.csv``        — string-/ticker-level preference exceptions (e.g. ERII)

This module LOADS + VALIDATES them at import: an unknown leaf/super, a duplicate key, or a gap in
the super map raises immediately (fail LOUD on drift — a broken data file must never resolve
quietly). Precedence: DB override > ticker override > SIC-string override > the SIC map > ``OTHER``
(sector present but unmapped — the visible tail, #9) > ``None`` (no sector — unclassified, the
honest abstain; the same hedged honesty as the origin chip).

Machine-derived SURFACE identity, never a fact (#1/#3 govern numbers, not identity strings): never
enters a fact_* table, never feeds a number on a call card, never a call input, and NOT a display
signal (those live in ``signals/display/``) — this module is pure and imports nothing from the call
path. It TAGS, never filters (#9): every name still surfaces; the operator's re-tag is the decision
(#10 — the map recommends, the operator can overrule per name).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import NamedTuple

from domain.enums import BusinessSupersector, BusinessType

_DATA_DIR = Path(__file__).resolve().parent


def norm_sic(s: str) -> str:
    """Normalize a SIC description for matching: collapse ALL whitespace runs + casefold. EDGAR's own
    strings carry erratic doubled spaces ("Deep Sea Foreign Transportation of  Freight") and case
    quirks ("Computer & office Equipment"); normalizing BOTH sides means a human edit that reflows
    spacing or case in the CSV cannot silently break a match."""
    return " ".join(s.split()).casefold()


def _data_rows(path: Path, expected_header: list[str]) -> list[tuple[int, list[str]]]:
    """Read a commented CSV: skip blank/# lines, require ``expected_header`` as the first data row
    (a loud drift guard — a reordered/renamed column must fail here, not misload), and return the
    remaining rows with their 1-based line numbers for loud error context."""
    rows: list[tuple[int, list[str]]] = []
    with open(path, newline="", encoding="utf-8") as f:
        numbered = [
            (i, line)
            for i, line in enumerate(f, start=1)
            if line.strip() and not line.lstrip().startswith("#")
        ]
    for (i, _), parsed in zip(numbered, csv.reader(line for _, line in numbered)):
        rows.append((i, parsed))
    if not rows or rows[0][1] != expected_header:
        raise ValueError(
            f"{path.name}: expected header {expected_header!r} as the first non-comment row"
        )
    return rows[1:]


def load_sic_map(path: Path) -> tuple[dict[str, BusinessType], frozenset[str]]:
    """``sic_map.csv`` -> ({normalized SIC -> leaf}, {normalized royalty-by-SIC strings}).
    Validates every leaf against ``BusinessType`` and rejects duplicate (normalized) keys."""
    mapping: dict[str, BusinessType] = {}
    royalty: set[str] = set()
    for lineno, row in _data_rows(path, ["sic_description", "business_type", "royalty"]):
        if len(row) != 3:
            raise ValueError(f"{path.name}:{lineno}: expected 3 columns, got {len(row)}")
        raw, leaf_s, flag = row
        key = norm_sic(raw)
        if not key:
            raise ValueError(f"{path.name}:{lineno}: empty sic_description")
        if key in mapping:
            raise ValueError(f"{path.name}:{lineno}: duplicate sic_description {raw!r}")
        try:
            mapping[key] = BusinessType(leaf_s)
        except ValueError as exc:
            raise ValueError(
                f"{path.name}:{lineno}: unknown business_type {leaf_s!r} for {raw!r}"
            ) from exc
        if flag.strip():
            if flag.strip().lower() != "y":
                raise ValueError(f"{path.name}:{lineno}: royalty flag must be 'y' or empty")
            royalty.add(key)
    return mapping, frozenset(royalty)


def load_supersectors(path: Path) -> dict[BusinessType, BusinessSupersector]:
    """``supersectors.csv`` -> {leaf -> super}. Must be TOTAL over ``BusinessType`` (a leaf without a
    super would strand rows out of the two-level view) with no duplicates."""
    mapping: dict[BusinessType, BusinessSupersector] = {}
    for lineno, row in _data_rows(path, ["business_type", "supersector"]):
        if len(row) != 2:
            raise ValueError(f"{path.name}:{lineno}: expected 2 columns, got {len(row)}")
        leaf_s, super_s = row
        try:
            leaf = BusinessType(leaf_s)
        except ValueError as exc:
            raise ValueError(f"{path.name}:{lineno}: unknown business_type {leaf_s!r}") from exc
        if leaf in mapping:
            raise ValueError(f"{path.name}:{lineno}: duplicate business_type {leaf_s!r}")
        try:
            mapping[leaf] = BusinessSupersector(super_s)
        except ValueError as exc:
            raise ValueError(f"{path.name}:{lineno}: unknown supersector {super_s!r}") from exc
    missing = [leaf.value for leaf in BusinessType if leaf not in mapping]
    if missing:
        raise ValueError(f"{path.name}: no supersector for leaves {missing!r} (map must be total)")
    return mapping


def load_royalty_patterns(path: Path) -> list[re.Pattern[str]]:
    """``royalty_patterns.txt`` -> compiled case-insensitive regexes (one per line; # comments)."""
    patterns: list[re.Pattern[str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            patterns.append(re.compile(text, re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"{path.name}:{lineno}: bad regex {text!r}: {exc}") from exc
    return patterns


def load_overrides(
    path: Path,
) -> tuple[dict[str, BusinessType], dict[str, BusinessType]]:
    """``overrides.csv`` -> ({TICKER -> leaf}, {normalized SIC -> leaf}) — the preference layer.
    ``kind`` is ``ticker`` (one name, e.g. ERII) or ``sic`` (re-rule a whole SIC string without
    editing the main map). Validated like the main map; the note column is for humans."""
    by_ticker: dict[str, BusinessType] = {}
    by_sic: dict[str, BusinessType] = {}
    for lineno, row in _data_rows(path, ["kind", "key", "business_type", "note"]):
        if len(row) != 4:
            raise ValueError(f"{path.name}:{lineno}: expected 4 columns, got {len(row)}")
        kind, key, leaf_s, _note = row
        try:
            leaf = BusinessType(leaf_s)
        except ValueError as exc:
            raise ValueError(
                f"{path.name}:{lineno}: unknown business_type {leaf_s!r} for {key!r}"
            ) from exc
        if kind == "ticker":
            k = key.strip().upper()
            if not k or k in by_ticker:
                raise ValueError(f"{path.name}:{lineno}: empty or duplicate ticker {key!r}")
            by_ticker[k] = leaf
        elif kind == "sic":
            k = norm_sic(key)
            if not k or k in by_sic:
                raise ValueError(f"{path.name}:{lineno}: empty or duplicate sic key {key!r}")
            by_sic[k] = leaf
        else:
            raise ValueError(f"{path.name}:{lineno}: kind must be 'ticker' or 'sic', got {kind!r}")
    return by_ticker, by_sic


# --- loaded ONCE at import; any drift in the data files fails loudly right here -------------------
SUB_TYPE_BY_SIC, ROYALTY_SICS = load_sic_map(_DATA_DIR / "sic_map.csv")
SUPER_BY_SUB = load_supersectors(_DATA_DIR / "supersectors.csv")
ROYALTY_PATTERNS = load_royalty_patterns(_DATA_DIR / "royalty_patterns.txt")
TICKER_OVERRIDES, SIC_OVERRIDES = load_overrides(_DATA_DIR / "overrides.csv")


class BusinessTypeRead(NamedTuple):
    """One resolved read: the effective leaf + its super (both ``None`` = unclassified) and the
    royalty/streaming overlay (independent of the leaf — a royalty house keeps its industry leaf).
    """

    business_type: BusinessType | None
    supersector: BusinessSupersector | None
    royalty: bool


def resolve_business_type(
    *,
    sector: str | None,
    name: str | None = None,
    ticker: str | None = None,
    override: str | None = None,
) -> BusinessTypeRead:
    """The pure resolution. ``sector``/``name``/``ticker`` are the master row's stored identity;
    ``override`` is the operator's stored per-security re-tag (0033) and always wins. No I/O, no
    inference beyond the loaded maps — mapped, visibly ``OTHER``, or an honest ``None``.

    The royalty overlay derives from the NAME patterns or a royalty-by-SIC string, never from the
    leaf and never overridable (operator ruling: derive-only in v1) — measured over the full live
    master: 32 hits, all genuine royalty/streaming houses, zero false positives."""
    sec = norm_sic(sector) if sector else ""
    royalty = bool(sec and sec in ROYALTY_SICS) or bool(
        name and any(p.search(name) for p in ROYALTY_PATTERNS)
    )

    leaf: BusinessType | None = None
    if override is not None:
        try:
            leaf = BusinessType(override)
        except ValueError:
            # A stored override this enum no longer knows (a manual DB edit, or a since-removed
            # leaf). The write path validates, so this is out-of-contract data: fall through to
            # the derived read rather than 500-ing every scored row over one bad value.
            leaf = None
    if leaf is None and ticker:
        leaf = TICKER_OVERRIDES.get(ticker.strip().upper())
    if leaf is None and sec:
        leaf = SIC_OVERRIDES.get(sec) or SUB_TYPE_BY_SIC.get(sec) or BusinessType.OTHER
    return BusinessTypeRead(
        business_type=leaf,
        supersector=SUPER_BY_SUB[leaf] if leaf is not None else None,
        royalty=royalty,
    )
