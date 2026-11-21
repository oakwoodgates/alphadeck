import { describe, expect, it } from "vitest";

import { ANNUAL_STATEMENTS_SOURCE, cashSymbol, labelCurrency, storedCurrency } from "../currency";

// The ONE front-end rule for the currency a cash/burn figure is labeled in (operator decision D4; the extractor
// correctness follow-up's GAP 3). Its backend twin is `domain/cash_currency.py` (`currency_symbol`): the same map and
// the same rule. A value with NO currency reads "?" only when it came from the annual statements; a quarterly or older
// row with none reads "$". Never a conversion.
describe("the currency label", () => {
  it("labels a stored value in its own currency", () => {
    expect(cashSymbol(storedCurrency({ currency: "CNY", source: ANNUAL_STATEMENTS_SOURCE }))).toBe("CN¥");
    expect(cashSymbol(storedCurrency({ currency: "USD", source: "10-q" }))).toBe("$");
    expect(cashSymbol(storedCurrency({ currency: "MXN", source: ANNUAL_STATEMENTS_SOURCE }))).toBe("MXN");
  });

  it("reads '?' only for an annual-statements value with no currency, '$' for a quarterly or older one", () => {
    expect(cashSymbol(storedCurrency({ currency: null, source: ANNUAL_STATEMENTS_SOURCE }))).toBe("?");
    expect(cashSymbol(storedCurrency({ currency: null, source: "10-q" }))).toBe("$");
    expect(cashSymbol(storedCurrency({}))).toBe("$");
  });

  it("labels a candidate by its statement currency and flags", () => {
    expect(cashSymbol(labelCurrency("JPY", ["annual-statements"]))).toBe("¥");
    expect(cashSymbol(labelCurrency(null, ["annual-statements", "currency-unread"]))).toBe("?");
    expect(cashSymbol(labelCurrency(null, []))).toBe("$");
  });
});
