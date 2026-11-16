// The ONE front-end rule for the currency a cash/burn figure is labeled in (operator decision D4; the extractor
// correctness follow-up's GAP 3). Its backend twin is `backend/domain/cash_currency.py` (`currency_symbol`): the same
// map and the same rule, so a screen and a server-written sentence (the Filing Stream row, the pace caveat) agree.
//
// A cash/burn value's `cash_usd` / `quarterly_burn_usd` / `investments_usd` hold its statement's OWN currency (the
// column names are historical, D4 (a)): an annual foreign filer reports in CNY, JPY, TWD, ... The value is NEVER
// converted (the runway is a ratio, so the months are right as they are); only the LABEL names the currency.
//
// - a stored ISO code -> its symbol (`CN¥`, `¥`, `NT$`, ...), or the code itself when no symbol is listed; `USD` -> `$`;
// - NO currency -> "?" ONLY for a value from the annual statements (its currency was never established, so never "$");
// - NO currency otherwise (a quarterly 10-Q/10-K value stored before the currency was recorded, a V1 row) -> "$".

/** A small static ISO→symbol map, falling back to the raw ISO code (honest, never an invented symbol). Currencies
 *  that share the "$" glyph carry their prefix (NT$, C$, S$, HK$, A$, R$): never a bare "$" for a non-USD value. */
const CURRENCY_SYMBOL: Record<string, string> = {
  TWD: "NT$",
  EUR: "€",
  CAD: "C$",
  CHF: "CHF",
  GBP: "£",
  INR: "₹",
  CNY: "CN¥",
  JPY: "¥",
  SGD: "S$",
  HKD: "HK$",
  KRW: "₩",
  AUD: "A$",
  ZAR: "R",
  BRL: "R$",
  ILS: "₪",
};

/** The annual path's flag for a currency it could NOT determine (a bare "$", or headers that disagree). */
export const CURRENCY_UNREAD_FLAG = "currency-unread";

/** The source (and a candidate's flag) the annual-statements path stamps on a cash/burn value (a 20-F / 40-F). */
export const ANNUAL_STATEMENTS_SOURCE = "annual-statements";

/** The currency a CANDIDATE's figures are labeled in: its own ISO code, or "?" when it comes from the annual
 *  statements with no currency (the extractor flags it `currency-unread`) — an unread currency never renders as "$".
 *  Null otherwise (a quarterly 10-Q/10-K candidate: "$"). */
export function labelCurrency(
  currency: string | null | undefined,
  flags: readonly string[] | null | undefined,
): string | null {
  if (currency) return currency;
  return (flags ?? []).some((f) => f === CURRENCY_UNREAD_FLAG || f === ANNUAL_STATEMENTS_SOURCE) ? "?" : null;
}

/** The currency a STORED cash/burn value's figures are labeled in (migration 0208): its stored ISO code; "?" for an
 *  annual-statements value stored without one; null (= "$") for a quarterly value or a V1 row. */
export function storedCurrency(v: { currency?: string | null; source?: string | null }): string | null {
  return labelCurrency(v.currency, v.source === ANNUAL_STATEMENTS_SOURCE ? [CURRENCY_UNREAD_FLAG] : []);
}

/** The symbol a label prints as: `$` for USD or no label, the native symbol (or the raw ISO code) for any other
 *  currency, "?" passed through for an unread one. */
export function cashSymbol(currency: string | null | undefined): string {
  if (!currency || currency === "USD") return "$";
  return CURRENCY_SYMBOL[currency] ?? currency;
}
