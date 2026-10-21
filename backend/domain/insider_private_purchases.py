"""PRIVATE PURCHASES — the Event Ledger's quiet note on a buy the filer says was not made on the open market.

THE OPERATOR'S DECISION 3 (2026-10-21). A private purchase between UNRELATED people — a director buying shares
from the founder (FTHM: "a private purchase ... between the Reporting Person and the Company's founder, Joshua
Harley"), a Stock Purchase Agreement between two holders (RDNW), a medical group buying a doctor's block (ASTH:
"These shares were purchased from David Schmidt.") — is REAL conviction and stays counted: it is NOT an internal
transfer (``domain/insider_internal_transfers.py`` screens only a buy and a sale between LINKED filers). The
Scoreboard's Event Ledger only says, quietly, on that buy's row: "private purchase from Joshua Harley — not on
the open market" (the FE owns the words; this module ships the located passage + the grounded counterparty).

THE TRIGGER IS THE FILER'S OWN WORDS — a located passage, never a model (#3). ``private_purchase_note`` reads the
footnotes the transaction itself cites (``txn_footnotes``, migration 0055) against a deterministic phrase list
(``PRIVATE_PHRASES``); a footnote that also says the trade was made on the open market, or in a public /
underwritten offering, is not a private purchase (``NOT_PRIVATE`` — "a private purchase in the open market",
ASLE), or in a plan's routine purchases (dividend reinvestment, an ESPP). MEASURED on prod 2026-10-21 (read-only,
THIS function over the 26,573 stored code-P rows' cached filings, every located passage read by eye): 86 rows (25
in the last two years, 6 inside the call's 240-day insider read; 2 are internal-transfer legs, whose own label
wins) — 38 of them name the issuer as the counterparty (PIPEs / stock purchase agreements), 6 a same-day seller.
The raw phrases without the FROM-party shape matched 2,730 rows, nearly all the weighted-average boilerplate
"purchased in multiple transactions at prices ranging from $X to $Y"; the first cut of this list also hit "acquired
from dividend reinvestment" (2) and "purchased from the Employee Stock Purchase Plan" (1), now excluded.

THE COUNTERPARTY is named only when the passage names it AND it is grounded: an owner who SOLD on this tape the
same day (Harley's own Form 4), else "the issuer" when the passage says the shares came from / were bought with /
under an agreement with the issuer. Otherwise the note carries no name — the passage itself rides beside it.

DISPLAY ONLY. Never an input to a call, a score or a screen; the detectors never read it. A row without the
capture (``txn_footnotes`` NULL — stored before 0055 and not repaired) simply has no note.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from domain.insider_internal_transfers import is_internal_transfer, names_owner
from domain.insider_joint_reports import JOINT_REPORTERS

BUY, SELL = "P", "S"

# The deterministic phrase list (case-insensitive), first match wins. "purchased/acquired/bought ... from" must be
# followed by a FROM-party — never a price ("from $7.97"), "from time to time" or "from the (open) market".
PRIVATE_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private purchase", re.compile(r"\bprivate(?:ly)?[\s-]+purchase", re.I)),
    ("privately negotiated", re.compile(r"\bprivately[\s-]+negotiated", re.I)),
    ("private transaction", re.compile(r"\bprivate[\s-]+(?:transaction|sale|transfer)", re.I)),
    ("stock purchase agreement", re.compile(r"\b(?:stock|share)\s+purchase\s+agreement", re.I)),
    (
        "purchased from",
        re.compile(
            r"\b(?:purchased|acquired|bought)\s+"
            r"(?:(?:an\s+aggregate\s+of\s+)?[\d,.]+\s+(?:shares|units)(?:\s+of\s+[^.;]{0,60}?)?\s+)?"
            r"(?:directly\s+)?from\s+(?!\$|time\s+to\s+time\b|the\s+open\s+market\b|the\s+market\b)",
            re.I,
        ),
    ),
)
# a passage that places the trade on the open market, in a public offering, or in a plan's routine purchases is not
# a private purchase — "acquired from dividend reinvestment" (NWE, D) and "purchased from the Employee Stock
# Purchase Plan" (LPTH) are the measured false hits the plan words exclude
NOT_PRIVATE = re.compile(
    r"\bopen[\s-]*market\b|\bpublic\s+offering\b|\bunderwrit|\binitial\s+public\b|\bIPO\b"
    r"|\bdirected\s+share\b|\brights\s+offering\b|\bat[\s-]+the[\s-]+market\b"
    r"|\bdividend\s+reinvestment\b|\bemployee\s+stock\s+purchase\b|\bESPP\b|\b401\s*\(k\)",
    re.I,
)
# "the issuer" as the counterparty: the shares came FROM it, or the agreement was WITH / BETWEEN it
_FROM_ISSUER = re.compile(
    r"\b(?:from|with)\s+the\s+issuer\b|\bbetween\s+the\s+issuer\b|\bthe\s+issuer\s+and\s+the\s+reporting\s+person\b"
    r"|\bbetween\s+(?:the\s+)?reporting\s+person\s+and\s+the\s+issuer\b",
    re.I,
)
ISSUER = "the issuer"


@dataclass(frozen=True)
class PrivatePurchaseNote:
    """The located passage (the filer's footnote, verbatim), the phrase that located it, and the grounded
    counterparty (``None`` when the passage names none we can ground)."""

    passage: str
    phrase: str
    counterparty: str | None


def _sellers_that_day(row: Mapping[str, Any], tape: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every owner who sold on this tape the buy's day (joint co-reporters included), in tape order, each once."""
    out: list[str] = []
    for r in tape:
        if r.get("txn_code") != SELL or r.get("valid_from") != row.get("valid_from"):
            continue
        for name in (r.get("insider_name"), *(r.get(JOINT_REPORTERS) or ())):
            if name and name not in out:
                out.append(str(name))
    return out


def private_purchase_note(
    row: Mapping[str, Any], tape: Iterable[Mapping[str, Any]] = ()
) -> PrivatePurchaseNote | None:
    """PURE — the private-purchase note for one code-P row, or ``None``.

    ``tape`` is the security's read (the sellers the counterparty may be grounded in). ``None`` for anything that
    is not a code-P row, for a screened internal-transfer leg (its own label says more), and for a row whose
    footnotes locate no passage."""
    if row.get("txn_code") != BUY or is_internal_transfer(row):
        return None
    sellers: list[str] | None = None
    for passage in row.get("txn_footnotes") or ():
        text = str(passage)
        if NOT_PRIVATE.search(text):
            continue
        phrase = next((label for label, pat in PRIVATE_PHRASES if pat.search(text)), None)
        if phrase is None:
            continue
        if sellers is None:
            sellers = _sellers_that_day(row, tape)
        named = next((s for s in sellers if names_owner(s, text)), None)
        if named is None and _FROM_ISSUER.search(text):
            named = ISSUER
        return PrivatePurchaseNote(passage=text, phrase=phrase, counterparty=named)
    return None
