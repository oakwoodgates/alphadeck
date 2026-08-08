"""Price-symbol resolution — WHICH vendor symbol a name is priced under, decided deterministically.

The OTC price-symbol fix's BRAIN: ``security_master.ticker`` is the SEC's canonical ticker (FDCT), but the
price vendor indexes the full history under a different symbol (FDCTD: 251 bars vs 16). The suffix rule is
impossible (FDCT->FDCTD, VREOD->VREOF, CURLD->CURLF), so the symbol is RESOLVED from a Yahoo symbol search,
never guessed (#3 — the LLM/heuristic never sources a symbol). This module is the PURE decider: it takes the
raw search payload(s) + pre-probed bar counts and returns a proposal (a symbol + a tier), NO I/O. The I/O
orchestration (search + history probe) lives in ``ingest/prices/resolve_symbol.py``; the write lives in
``securities/master.set_price_symbol``. Pure and import-light, like ``origin.py`` / ``filer_coverage.py`` —
it imports nothing from the ingest or call path, so it is structurally unable to touch a fact or a call.

The selection (all four gates required for AUTO):

1. **quoteType == EQUITY** — a mutualfund (FDCTX) / ETF / index share the ticker's stem but are not the
   operating equity; filtered out.
2. **US venue** — Yahoo ranks a foreign listing ABOVE the US one (search "Curaleaf" → ``CURA.TO`` scores
   higher than ``CURLF``), so NEVER take the top score. A US Yahoo symbol carries no dotted suffix; a foreign
   venue does (``.TO`` Toronto, ``.CN``/``.NE`` Canada, ``.F``/``.DE``/``.DU``/``.MU``/``.SG`` Germany, …) —
   rejected.
3. **exact normalized company-name match** to the master name — the resolution is the SAME issuer's US
   listing, so its name matches (legal-form suffixes normalized away; descriptive words kept, so two
   different companies never collapse).
4. **longer history confirms** — the resolved symbol must return MATERIALLY more bars than the canonical
   ticker (history confirms the pick; it never SELECTS it — the name/venue filters do the selecting).

Tiers (recall is sacred, #9 — a name is NEVER dropped, only classified):

- **AUTO** — exactly one US-EQUITY exact-name match WITH longer history → adopt it (the #2 self-heal writes it).
- **FLAG** — candidates exist but are ambiguous (more than one match) or unverified (a lone match without
  longer history) → surfaced for the operator to ``--adopt``, nothing auto-written.
- **NONE** — no usable US-EQUITY exact-name quotes (a genuinely-uncovered name like PMBHF) → keep the name,
  priced under its canonical ticker; the thin-history flag (#1) still marks it starved.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# How much MORE history the resolved symbol must carry to count as "longer history" (gate 4). A genuine
# resolution recovers the full multi-year tape (FDCTD 251 vs FDCT 16 = +235), so a modest floor cleanly
# separates it from same-day fetch jitter or a trivially-longer alias — below it, a lone match is FLAG
# (unverified), the operator confirms. ~a trading month.
_MIN_HISTORY_GAIN = 20

# Legal-form suffix tokens stripped in name normalization (gate 3). ONLY the corporate form — descriptive
# words (HOLDINGS, GROUP, PHARMACEUTICALS) are KEPT, so "ABC Holdings" and "ABC Group" never collapse to a
# false match. Matched case-insensitively, trailing occurrences peeled (e.g. "XYZ Co Ltd").
_LEGAL_SUFFIXES = frozenset(
    "INC INCORPORATED CORP CORPORATION CO COMPANY LTD LIMITED LLC LLP LP PLC "
    "SA NV AG AB ASA OYJ SE".split()
)


@dataclass(frozen=True)
class PriceSymbolProposal:
    """A resolution outcome: the ``tier`` (AUTO/FLAG/NONE), the ``proposed_symbol`` to adopt (AUTO) or the
    first/only candidate (FLAG; ``None`` for NONE), the human-readable ``why`` (provenance for the basis),
    and every US-EQUITY exact-name ``candidates`` considered (so a FLAG can list the ambiguous set).
    """

    tier: str  # "AUTO" | "FLAG" | "NONE"
    proposed_symbol: str | None
    why: str
    candidates: tuple[str, ...] = field(default_factory=tuple)


def normalize_company_name(name: str | None) -> str:
    """Normalize a company name for exact matching: upper-case, punctuation → space, collapse whitespace,
    drop a leading "THE", and peel trailing legal-form suffix tokens. "Curaleaf Holdings, Inc." and
    "Curaleaf Holdings Inc" both → "CURALEAF HOLDINGS"; "" for a blank input."""
    if not name:
        return ""
    tokens = re.sub(r"[^A-Za-z0-9]+", " ", name.upper()).split()
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def _is_us_symbol(symbol: str) -> bool:
    """A US Yahoo symbol carries no dotted foreign-venue suffix (``.TO``/``.CN``/``.F``/…). US class/derivative
    lines use a hyphen (BRK-B, KTTA-WT), never a dot — so "a dot means foreign" cleanly rejects the
    higher-ranked foreign listing (CURA.TO) while keeping FDCTD / CURLF / VREOF."""
    return bool(symbol) and "." not in symbol


def us_equity_name_matches(search: Mapping | None, name: str | None) -> list[str]:
    """The pure filter: from a Yahoo search payload, the symbols that are EQUITY + US-venue + an exact
    normalized name match to ``name`` — order-preserving, de-duplicated, upper-cased. Empty when the payload
    or name is missing, or nothing passes all three gates (never a fuzzy/top-score pick)."""
    quotes = (search or {}).get("quotes") or []
    target = normalize_company_name(name)
    if not target:
        return []
    out: list[str] = []
    for q in quotes:
        if str(q.get("quoteType") or "").upper() != "EQUITY":
            continue
        symbol = str(q.get("symbol") or "").strip().upper()
        if not _is_us_symbol(symbol):
            continue
        qname = q.get("shortname") or q.get("longname") or q.get("longName") or ""
        if normalize_company_name(qname) == target:
            out.append(symbol)
    return list(dict.fromkeys(out))  # de-dup, order preserved


def propose_price_symbol(
    *,
    ticker: str,
    name: str | None,
    ticker_search: Mapping | None,
    name_search: Mapping | None = None,
    canonical_bars: int = 0,
    candidate_bars: Mapping[str, int] | None = None,
) -> PriceSymbolProposal:
    """The pure decider. Filter the ticker search to US-EQUITY exact-name matches; on empty, fall back to the
    NAME search (VREOD's ticker-search is empty; "Vireo Growth" → VREOF). Drop any self-match (a candidate
    equal to the canonical ticker is not a resolution). Then:

    - 0 matches → NONE (keep the name, priced under its canonical ticker — #9).
    - >1 match → FLAG (ambiguous — the operator picks).
    - exactly 1 match ``C``:
        - ``candidate_bars[C] > canonical_bars + _MIN_HISTORY_GAIN`` → AUTO (longer history confirms).
        - else → FLAG (unverified — a lone match without materially longer history).

    ``canonical_bars`` / ``candidate_bars`` are the pre-probed history depths (``fetch_eod`` bar counts) — the
    confirmation basis; the orchestration fetches them, this only compares."""
    tkr = (ticker or "").upper()
    candidate_bars = candidate_bars or {}

    matches = us_equity_name_matches(ticker_search, name)
    via = "ticker search"
    if not matches:
        matches = us_equity_name_matches(name_search, name)
        via = "name search"
    matches = [m for m in matches if m != tkr]  # a self-match isn't a resolution

    if not matches:
        return PriceSymbolProposal(
            tier="NONE",
            proposed_symbol=None,
            why=f"no US-equity exact-name quote for {tkr!r} / {name!r} — kept under the canonical ticker",
        )
    if len(matches) > 1:
        return PriceSymbolProposal(
            tier="FLAG",
            proposed_symbol=matches[0],
            why=f"{len(matches)} US-equity name matches ({', '.join(matches)}) via {via} — operator picks",
            candidates=tuple(matches),
        )
    cand = matches[0]
    cand_bars = candidate_bars.get(cand, 0)
    if cand_bars > canonical_bars + _MIN_HISTORY_GAIN:
        return PriceSymbolProposal(
            tier="AUTO",
            proposed_symbol=cand,
            why=(
                f"{cand}: US-equity exact-name match via {via}, {cand_bars} bars vs {tkr} "
                f"{canonical_bars} (longer history)"
            ),
            candidates=(cand,),
        )
    return PriceSymbolProposal(
        tier="FLAG",
        proposed_symbol=cand,
        why=(
            f"{cand}: US-equity name match via {via}, but history not materially longer "
            f"({cand_bars} vs {canonical_bars}) — operator confirms"
        ),
        candidates=(cand,),
    )
