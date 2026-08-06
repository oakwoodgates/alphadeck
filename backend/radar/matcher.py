"""The DA-filing term matcher (slice 2) — deterministic, word-boundary matching of every thesis's
persisted term set against a filing's text. The one thing discovery never had: a LOCAL text matcher
(EFTS matches at the index; this matches the document we already fetched). #3-safe by construction:
regex over cleaned text, never a model's reading.

Collision discipline (the ``assign_tier`` lesson — "IND" must not match inside "INDEX"):
- every term matches at word boundaries;
- an acronym-style single token (``[A-Z][A-Z0-9]{1,9}`` — HBM, DRAM, S-4-ish tickers) matches
  CASE-SENSITIVELY (the all-caps form is the signal; "dram shop" is not DRAM);
- a phrase matches case-insensitively with whitespace-flexible gaps (filings wrap lines).
"""

from __future__ import annotations

import re

from domain.enums import TermTier
from domain.settings import get_settings
from domain.thesis import TermSetEntry
from ingest.edgar.client import EdgarClient
from ingest.edgar.converts import clean_filing_text

# Caps: the full-submission .txt of an S-4 can run tens of MB (financial statements + exhibits).
# We cap the RAW read and the CLEANED text; a capped doc is flagged (`truncated`) and the flag is
# persisted on the match row — no silent caps (a truncation must never read as "matched nothing").
_MAX_RAW = 6_000_000
_MAX_TEXT = 2_000_000

_ACRONYM = re.compile(r"[A-Z][A-Z0-9]{1,9}\Z")


def compile_term(term: str) -> re.Pattern[str]:
    t = term.strip()
    if _ACRONYM.fullmatch(t):
        return re.compile(rf"\b{re.escape(t)}\b")
    body = r"\s+".join(re.escape(w) for w in t.split())
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


def match_term_set(text: str, term_set: list[TermSetEntry]) -> tuple[list[str], list[str]]:
    """Match one cleaned text against one thesis's tiered term set → (signal_hits, broad_hits),
    each the sorted matched term STRINGS (display provenance, the matched_terms idiom)."""
    signal: list[str] = []
    broad: list[str] = []
    for entry in term_set:
        if compile_term(entry.term).search(text):
            (signal if entry.tier is TermTier.SIGNAL else broad).append(entry.term)
    return sorted(set(signal)), sorted(set(broad))


def fetch_filing_text(client: EdgarClient, cik: str, accession: str) -> tuple[str, bool]:
    """The filing's FULL-SUBMISSION text (all documents + exhibits — the press release with the
    theme language rides as an exhibit), cleaned for matching. Immutable-cached under ``forms/``
    (an accession's content never changes). Returns (text, truncated)."""
    url = f"{get_settings().sec_archives_base}/{int(cik)}/{accession}.txt"
    raw = client.get_text(url, f"forms/{accession}/full.txt")
    truncated = len(raw) > _MAX_RAW
    text = clean_filing_text(raw[:_MAX_RAW])
    if len(text) > _MAX_TEXT:
        text, truncated = text[:_MAX_TEXT], True
    return text, truncated
