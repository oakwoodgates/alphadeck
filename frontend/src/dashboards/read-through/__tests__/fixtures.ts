import type { ReadThroughCardOut, ReadThroughOut, ReadThroughRowOut, ReadThroughTapeOut } from "../useReadThrough";

// Hand-built Read-Throughs payloads shared by the view-helper and component tests.

export const EDGAR = "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/";
export const MIXED = "mixed-scale price history";

export const card = (over: Partial<ReadThroughCardOut> = {}): ReadThroughCardOut => ({
  thesis_id: "t-grid",
  thesis_name: "Grid",
  ticker: "XXX",
  links: ["Turbines"],
  silent: false,
  ...over,
});

export const TAPE_OK: ReadThroughTapeOut = {
  ret_7d: 6.25,
  ret_30d: -3.1,
  vs_spy_7d: 4.75,
  vs_spy_30d: -7.1,
  last_close: "2026-10-28",
  gap: null,
  reason: null,
};

export const tape = (over: Partial<ReadThroughTapeOut> = {}): ReadThroughTapeOut => ({ ...TAPE_OK, ...over });

export const row = (key: string, over: Partial<ReadThroughRowOut> = {}): ReadThroughRowOut => ({
  key,
  security_id: `s-${key}`,
  ticker: "XXX",
  name: null,
  role: "payer",
  cards: [card()],
  business_type: null,
  last_filing: null,
  next_catalyst: null,
  tape: TAPE_OK,
  ...over,
});

// XXX: a payer on two cards (one silent), the newest-but-one filing, a catalyst, a plain tape
export const XXX = row("xxx-payer", {
  ticker: "XXX",
  name: "Xylo Corp",
  role: "payer",
  business_type: "biotech_pharma",
  cards: [
    card({ thesis_id: "t-batt", thesis_name: "Batteries", ticker: "XXX", links: ["Context"], silent: true }),
    card({ thesis_id: "t-grid", thesis_name: "Grid", ticker: "XXX", links: ["Turbines", "Cables"] }),
  ],
  last_filing: {
    kind: "8k",
    form: "8-K",
    date: "2026-10-20",
    days_ago: 8,
    headline: "8-K · 2.02 results of operations and financial condition",
    url: EDGAR,
    accession: "0000876543-26-000010",
  },
  next_catalyst: {
    thesis_id: "t-grid",
    thesis_name: "Grid",
    catalyst_id: "c-1",
    label: "Q3 earnings",
    kind: "earnings",
    when_date: "2026-11-12",
    when_label: "Nov 12",
    days: 15,
  },
});

// YYY: a counter-case on Grid (Context), the newest filing, the 7-day window short
export const YYY_COUNTER = row("yyy-counter", {
  ticker: "YYY",
  name: "Yond Inc",
  role: "counter_case",
  business_type: "utilities",
  cards: [card({ ticker: "YYY", links: ["Context"], silent: true })],
  last_filing: {
    kind: "form4",
    form: "4",
    date: "2026-10-27",
    days_ago: 1,
    headline: "Jane Roe (CEO): S sale 3,000 sh @ $5.00 · -$15,000",
    url: EDGAR,
    accession: "0000876543-26-000011",
  },
  tape: tape({ ret_7d: null, vs_spy_7d: null, ret_30d: 5.5, vs_spy_30d: 1.5 }),
});

// YYY again: the same company, tagged payer on another card (Bench), no filing, a flagged price history
export const YYY_PAYER = row("yyy-payer", {
  ticker: "YYY",
  name: "Yond Inc",
  role: "payer",
  business_type: "utilities",
  cards: [card({ thesis_id: "t-batt", thesis_name: "Batteries", ticker: "YYY", links: ["Bench"], silent: true })],
  tape: tape({
    ret_7d: null,
    ret_30d: null,
    vs_spy_7d: null,
    vs_spy_30d: null,
    last_close: null,
    gap: "flagged",
    reason: MIXED,
  }),
});

// ZZZ: a direct read, an old revenue print with no link, no business type, a last close that is not SPY's
export const ZZZ = row("zzz-direct", {
  ticker: "ZZZ",
  name: "Zed Holdings",
  role: "direct_read",
  business_type: null,
  cards: [card({ ticker: "ZZZ", links: [null] })],
  last_filing: {
    kind: "revenue",
    form: "10-Q",
    date: "2026-09-01",
    days_ago: 57,
    headline: "revenue $41.2M for the quarter ended 2026-06-30",
    url: null,
    accession: null,
  },
  tape: tape({ vs_spy_7d: null, vs_spy_30d: null, last_close: "2026-10-23", gap: "ends_differ", ret_7d: 2, ret_30d: 3 }),
});

// AAA: a financier with no filing and no tape
export const AAA = row("aaa-financier", {
  ticker: "AAA",
  name: null,
  role: "financier",
  business_type: "bank",
  cards: [card({ thesis_id: "t-batt", thesis_name: "Batteries", ticker: "AAA", links: ["Cells"] })],
  tape: tape({ ret_7d: null, ret_30d: null, vs_spy_7d: null, vs_spy_30d: null, last_close: null, gap: "no_tape" }),
});

export const ROWS = [YYY_COUNTER, XXX, ZZZ, AAA, YYY_PAYER];

export const payload = (over: Partial<ReadThroughOut> = {}): ReadThroughOut => ({
  asof: "2026-10-28",
  clock: "public",
  live_cards: 4,
  cards_counted: 3,
  cards_not_yet_created: 1,
  roster_fallback_cards: 0,
  cards_with_roles: 2,
  cards_without_roles: [{ thesis_id: "t-quiet", thesis_name: "Quiet" }],
  role_tags: 6,
  companies: 4,
  by_role: [
    { role: "payer", rows: 2 },
    { role: "counter_case", rows: 1 },
    { role: "direct_read", rows: 1 },
    { role: "participant", rows: 0 },
    { role: "financier", rows: 1 },
    { role: "benchmark", rows: 0 },
  ],
  rows: ROWS,
  rows_with_filing: 3,
  unresolved: [],
  unresolved_rows: 0,
  flagged: [{ security_id: "s-yyy", ticker: "YYY", reason: MIXED }],
  tape_ends_differ: 1,
  spy: { ret_7d: 1.5, ret_30d: 4, end: "2026-10-28" },
  benchmark: "SPY",
  tape_windows: [7, 30],
  filing_days: 365,
  cache: "hit",
  compute_ms: 900,
  ...over,
});
