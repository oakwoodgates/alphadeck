"""The ONE backend rule for the currency a stored cash/burn figure is printed in (operator decision D4; the extractor
correctness follow-up's GAP 3, with the operator-approved rule for a missing currency).

A cash/burn value's ``cash_usd`` / ``quarterly_burn_usd`` / ``investments_usd`` / ``ytd_quarterly_burn_usd`` hold its
statement's own currency (an annual foreign filer reports in CNY, JPY, CAD, ...): the column names are historical
(D4 (a)). Nothing is ever converted; only the LABEL names the currency. The runway in months is a ratio and needs none.

``currency_symbol`` is the rule every backend printer calls (the Filing Stream row, the pace caveat on the scored
meter, the Peer Table cell and the steward page):

- a stored ISO code -> its symbol (``CN¥``, ``¥``, ``C$``, ``NT$``, ...); a code without a known symbol prints as
  the code itself (``MXN``), never a ``$``; ``USD`` -> ``$``;
- NO currency -> ``?`` ONLY when the value came from the annual statements (its ``source`` is
  ``annual-statements``, or a candidate's flags say ``annual-statements`` / ``currency-unread``): its currency was
  never established, so it is never shown as dollars;
- NO currency otherwise (a quarterly 10-Q/10-K value stored before the currency was recorded, a V1 row) -> ``$``:
  the periodic path reads companyfacts' USD unit.

The front end's twin is ``frontend/src/components/currency.ts`` (``labelCurrency`` / ``storedCurrency`` /
``cashSymbol``): the same map and the same rule. Display only: never a call input.
"""

from __future__ import annotations

from collections.abc import Iterable

# The source the annual-statements runway path stamps on a value (``ingest.edgar.annual_runway``), and the flags an
# annual candidate carries (``domain.extraction.ExtractedFact.flags``).
ANNUAL_STATEMENTS = "annual-statements"
CURRENCY_UNREAD_FLAG = "currency-unread"
# What a figure whose currency was never established is labeled with (never "$").
UNREAD_SYMBOL = "?"

# ISO code -> the symbol a person reads. Currencies that share the "$" glyph carry their prefix (S$, HK$, A$, R$,
# C$, NT$): never a bare "$" for a non-USD value. A code not listed prints as itself.
_SYMBOL: dict[str, str] = {
    "USD": "$",
    "TWD": "NT$",
    "EUR": "€",
    "CAD": "C$",
    "CHF": "CHF",
    "GBP": "£",
    "INR": "₹",
    "CNY": "CN¥",
    "JPY": "¥",
    "SGD": "S$",
    "HKD": "HK$",
    "KRW": "₩",
    "AUD": "A$",
    "ZAR": "R",
    "BRL": "R$",
    "ILS": "₪",
}


def currency_symbol(
    currency: str | None,
    *,
    source: str | None = None,
    flags: Iterable[str] | None = None,
) -> str:
    """The label a stored cash/burn value's figures are printed with (the module docstring has the rule).

    ``currency`` is the fact's ``currency`` (or a candidate's ``statement_currency``); ``source`` the row's source;
    ``flags`` a candidate's flags. Never converts, never guesses ``$`` for an annual value without a currency.
    """
    if currency:
        return _SYMBOL.get(currency, currency)
    annual = source == ANNUAL_STATEMENTS or any(
        f in (ANNUAL_STATEMENTS, CURRENCY_UNREAD_FLAG) for f in (flags or ())
    )
    return UNREAD_SYMBOL if annual else "$"


__all__ = ["ANNUAL_STATEMENTS", "CURRENCY_UNREAD_FLAG", "UNREAD_SYMBOL", "currency_symbol"]
