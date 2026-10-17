"""SUCCESSOR-ISSUER evidence from EDGAR (T7) — pure readers over a submissions JSON, plus SEC's ticker map.

THE CASE (MEASURED on prod, 2026-10-17). A company can re-register under a NEW CIK and keep its ticker: a
redomicile or a holding-company reorganization makes a new legal entity the SUCCESSOR ISSUER under Exchange
Act Rule 12g-3 (it files an **8-K12B**, or an 8-K12G3, as its first report), and the predecessor then
deregisters (a **15-12B / 15-12G / 15-15D**). UROY moved from CIK 0001711570 to 0002143673 (8-K12B
2026-07-28, Rule 12g-3(a)); REAX from 0001862461 to 0002136387 (8-K12B 2026-08-24, Rule 12g-3(c)). Every
filing leg reads ``fetch_submissions(client, sec.cik)``, so a member whose master row still holds the dead
CIK silently stops receiving its Form 4s, 8-Ks and 13D/Gs.

This module only READS: which filings on a submissions doc are succession / deregistration EVIDENCE, and
which CIK SEC's own ticker map assigns a symbol to. The judgment (is this member a successor case?) lives in
``pipeline/successor_cik.py``; the operator ratifies every re-point (#3). Deterministic, no LLM, no number.

A separate module on purpose: ``submissions.py`` is shared with other in-flight work, and nothing here
changes what any existing leg reads. The succession FORM SET itself lives in ``domain/edgar_forms.py``
(``SUCCESSION_8K_FORMS``), shared with the 8-K tape: since 2026-10-17 (operator decision)
``form8k_filings`` STORES these reports on the corporate-event tape too, and the event detectors skip them
by form — their items describe the succession itself (``docs/CORPORATE_EVENTS.md`` §succession reports).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from domain.edgar_forms import SUCCESSION_8K_FORMS
from domain.settings import get_settings
from ingest.edgar.client import EdgarClient
from ingest.edgar.submissions import parse_item_codes

# The SUCCESSOR's first reports (8-K12B / 8-K12G3 / 8-K15D5 + amendments) — the ONE set, defined in
# ``domain/edgar_forms.py`` and shared with the 8-K tape (stored there, skipped by the event detectors).
# Here it is display evidence only — never a gate (the rule needs none of them).
SUCCESSION_FORMS = SUCCESSION_8K_FORMS
# The PREDECESSOR's deregistration: 15-12B (exchange-listed class), 15-12G (12(g) class), 15-15D (the
# 15(d) reporting suspension). Amendments ride along. Evidence only.
DEREGISTRATION_FORMS = frozenset({"15-12B", "15-12B/A", "15-12G", "15-12G/A", "15-15D", "15-15D/A"})

# SEC's ticker map, fetched through the polite cached ``EdgarClient`` under a MUTABLE key class (any prefix
# but ``forms/`` re-fetches on the client's TTL — the recurring pass's five minutes — so the nightly read is
# tonight's map, never the forever-cached copy ``securities/sec_tickers`` keeps for the populate path).
TICKER_MAP_CACHE_KEY = "files/company_tickers_exchange.json"


@dataclass(frozen=True)
class FilingRef:
    """One filing named as evidence: its form, FILING date, accession and (an 8-K-family form) its item
    codes as EDGAR resolved them (``()`` = none / not resolved)."""

    form: str
    filed: date
    accession: str
    items: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "form": self.form,
            "filed": self.filed.isoformat(),
            "accession": self.accession,
            "items": list(self.items),
        }


def _filings(submissions: dict[str, Any], forms: frozenset[str]) -> tuple[FilingRef, ...]:
    """Every filing of ``forms`` in the ``recent`` window, newest first. A row with a blank or malformed
    date is skipped (never guessed); a sparse doc reads as none."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    names = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    items = recent.get("items") or []
    out: list[FilingRef] = []
    for i, form in enumerate(names):
        if form not in forms:
            continue
        try:
            filed = date.fromisoformat(dates[i] if i < len(dates) else "")
        except (TypeError, ValueError):
            continue
        out.append(
            FilingRef(
                form=str(form),
                filed=filed,
                accession=str(accns[i]) if i < len(accns) else "",
                items=tuple(parse_item_codes(items[i] if i < len(items) else None) or ()),
            )
        )
    out.sort(key=lambda f: f.filed, reverse=True)
    return tuple(out)


def succession_filings(submissions: dict[str, Any]) -> tuple[FilingRef, ...]:
    """The successor-issuer reports (``SUCCESSION_FORMS``) on a submissions doc, newest first."""
    return _filings(submissions, SUCCESSION_FORMS)


def deregistration_filings(submissions: dict[str, Any]) -> tuple[FilingRef, ...]:
    """The deregistration filings (``DEREGISTRATION_FORMS``) on a submissions doc, newest first."""
    return _filings(submissions, DEREGISTRATION_FORMS)


def _cik10(v: object) -> str | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f"{n:010d}" if n > 0 else None


def parse_ticker_map(raw: dict[str, Any]) -> dict[str, frozenset[str]]:
    """``SYMBOL -> {10-digit CIK, ...}`` from SEC's ticker map, in either published shape: the EXCHANGE
    variant (``{fields, data}`` rows — ``company_tickers_exchange.json``, what the repo's settings point
    at) or the plain ``company_tickers.json`` (``{"0": {cik_str, ticker, title}, ...}``). Symbols are
    upper-cased; a row without a ticker or a CIK is skipped (exact mappings only, never a fuzzy guess). A
    set, because nothing stops the file listing one symbol under two CIKs — the caller must see both.
    """
    out: dict[str, set[str]] = {}

    def _add(ticker: object, cik: object) -> None:
        sym = str(ticker or "").strip().upper()
        c = _cik10(cik)
        if sym and c:
            out.setdefault(sym, set()).add(c)

    if isinstance(raw.get("fields"), list) and isinstance(raw.get("data"), list):
        idx = {f: i for i, f in enumerate(raw["fields"])}
        if "cik" in idx and "ticker" in idx:
            for row in raw["data"]:
                if isinstance(row, list) and len(row) > max(idx["cik"], idx["ticker"]):
                    _add(row[idx["ticker"]], row[idx["cik"]])
    else:
        for row in raw.values():
            if isinstance(row, dict):
                _add(row.get("ticker"), row.get("cik_str", row.get("cik")))
    return {k: frozenset(v) for k, v in out.items()}


def fetch_ticker_map(client: EdgarClient) -> dict[str, frozenset[str]]:
    """SEC's ticker map through the polite cached client (declared User-Agent, rate limit, key-classed
    TTL) — ONE request when the cached copy is past the client's TTL, none otherwise."""
    raw = client.get_json(get_settings().sec_company_tickers_url, TICKER_MAP_CACHE_KEY)
    return parse_ticker_map(raw)
