// Fixtures for the user's read of the shared values (`GET /platform-values/...`, V2 W5e): one builder per state, shaped
// like the wire (`UserValueOut`). The words in `label` are the server's (`platform_values/words.py`).

export const SID = "00000000-0000-0000-0000-0000000000a1";

type Loose = Record<string, unknown>;

const FILING = "https://www.sec.gov/Archives/edgar/data/1/000000000026000001/q3.htm";

export const sharesFromFiling = (over: Loose = {}) => ({
  fact_type: "shares_outstanding",
  state: "from_filing",
  reason: null,
  label: "From the filing",
  set_by: "machine",
  value: {
    source: "10-q-cover",
    source_url: FILING,
    valid_from: "2026-09-30",
    shares: 52113000,
  },
  annual_line: null,
  pending: null,
  awaiting: null,
  my_flag: null,
  ...over,
});

export const sharesAnnual = (over: Loose = {}) =>
  sharesFromFiling({
    value: {
      source: "annual-cover",
      source_url: FILING,
      valid_from: "2026-03-31",
      shares: 143310556,
    },
    annual_line: "annual figure from the 20-F or 40-F cover, as of Mar 31, 2026",
    ...over,
  });

export const cashVerified = (over: Loose = {}) => ({
  fact_type: "cash_burn",
  state: "verified",
  reason: null,
  label: "Verified by the steward",
  set_by: "steward",
  value: {
    source: "10-q-cashflow",
    source_url: FILING,
    valid_from: "2026-09-30",
    cash_usd: 890000000,
    investments_usd: 120000000,
    quarterly_burn_usd: 31400000,
    currency: "USD",
    currency_symbol: "$",
    pace: { ytd_quarterly_burn_usd: 29000000, ytd_days: 273, ytd_runway_months: 9.2, caveat: null },
  },
  annual_line: null,
  pending: null,
  awaiting: null,
  my_flag: null,
  ...over,
});

export const cashAwaiting = (over: Loose = {}) => ({
  fact_type: "cash_burn",
  state: "awaiting_steward",
  reason: null,
  label: "Awaiting the steward",
  set_by: null,
  value: null,
  annual_line: null,
  pending: null,
  awaiting: {
    event_date: "2026-07-26",
    source_url: FILING,
    line: "The steward has not checked the cash and burn read from the filing dated Jul 26, 2026. Runway is not shown until the steward checks it.",
    located_passages: [
      {
        kind: "balance-sheet",
        anchor: "Total cash and short-term investments",
        excerpt: "Cash and short-term investments 69,343 (in millions) and burn text",
        url: FILING,
      },
    ],
    passages_unreadable: 0,
  },
  my_flag: null,
  ...over,
});

export const noValue = (factType: "shares_outstanding" | "cash_burn", over: Loose = {}) => ({
  fact_type: factType,
  state: "no_value",
  reason: "not-extracted",
  label: "No value yet: the platform has not read this company's filings yet",
  set_by: null,
  value: null,
  annual_line: null,
  pending: null,
  awaiting: null,
  my_flag: null,
  ...over,
});

export const company = (shares: Loose, cash_burn: Loose, over: Loose = {}) => ({
  security_id: SID,
  ticker: "ACME",
  name: "Acme Corp",
  shares,
  cash_burn,
  ...over,
});

export const openFlag = (over: Loose = {}) => ({
  id: "f0000000-0000-0000-0000-000000000001",
  note: null,
  opened_at: "2026-11-16T15:00:00Z",
  closed_at: null,
  outcome: null,
  outcome_note: null,
  ...over,
});
