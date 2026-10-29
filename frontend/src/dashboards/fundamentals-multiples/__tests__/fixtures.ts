import type {
  MultiplesBarOut,
  MultiplesCardsOut,
  MultiplesCompanyOut,
  MultiplesCountsOut,
  MultiplesMedianOut,
  MultiplesOut,
  MultiplesRowOut,
} from "../useFundamentalsMultiples";

// Hand-built Fundamentals Multiples payloads for the layout, view and component tests (no server).

/** Twelve calendar quarters, oldest first, ending 2026-Q2. */
export const AXIS = [
  "2023-Q3",
  "2023-Q4",
  "2024-Q1",
  "2024-Q2",
  "2024-Q3",
  "2024-Q4",
  "2025-Q1",
  "2025-Q2",
  "2025-Q3",
  "2025-Q4",
  "2026-Q1",
  "2026-Q2",
];

const QUARTER_END = ["03-31", "06-30", "09-30", "12-31"];

/** The period end of a calendar quarter ("2026-Q2" -> "2026-06-30"). */
export function quarterEnd(q: string): string {
  const [y, n] = q.split("-Q");
  return `${y}-${QUARTER_END[Number(n) - 1]}`;
}

export function plusDays(iso: string, days: number): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);
}

export const bar = (q: string, value: number, over: Partial<MultiplesBarOut> = {}): MultiplesBarOut => {
  const end = quarterEnd(q);
  const filed = plusDays(end, 40);
  const acc = `0000876544-${filed.slice(2, 4)}-${filed.slice(5, 7)}${filed.slice(8, 10)}01`;
  return {
    quarter: q,
    period_end: end,
    value,
    basis: "native",
    fiscal_period: `Q${q.slice(-1)}`,
    fiscal_year: Number(q.slice(0, 4)),
    filed,
    accession: acc,
    url: `https://www.sec.gov/Archives/edgar/data/876544/${acc.replaceAll("-", "")}/`,
    first_knowable: null,
    first_knowable_accession: null,
    yoy: null,
    prior_period_end: null,
    ...over,
  };
};

export const company = (
  ticker: string,
  bars: MultiplesBarOut[],
  over: Partial<MultiplesCompanyOut> = {},
): MultiplesCompanyOut => {
  const last = bars[bars.length - 1];
  return {
    company_key: `s-${ticker}`,
    ticker,
    name: `${ticker} Corp`,
    security_id: `s-${ticker}`,
    silent: false,
    state: bars.length === 0 ? "none" : bars.length < 5 ? "thin" : "series",
    periods: bars.length,
    bars,
    last_print: last
      ? { quarter: last.quarter, period_end: last.period_end, filed: last.filed, days_before: 5, yoy: last.yoy }
      : null,
    set_aside: 0,
    ...over,
  };
};

/** AAA: a bar for every axis quarter; dots from 2024-Q1 (+20%), one negative (2024-Q3), one off the scale (the
 *  last, +150%); a derived quarter (2023-Q4) and a re-report (2025-Q2, first knowable a year earlier). */
export const AAA = company(
  "AAA",
  AXIS.map((q, i) =>
    bar(q, 100 + 10 * i, {
      yoy: i < 2 ? null : i === 4 ? -30 : i === 11 ? 150 : 20,
      prior_period_end: i < 2 ? null : quarterEnd(AXIS[Math.max(0, i - 4)]),
      basis: i === 1 ? "derived" : "native",
      ...(i === 7 ? { first_knowable: "2025-08-08", first_knowable_accession: "0000876544-25-080801" } : {}),
    }),
  ),
  { periods: 14, set_aside: 0 },
);
/** BBB: four quarters, so thin; one period set aside. */
export const BBB = company(
  "BBB",
  [
    bar("2025-Q1", 50),
    bar("2025-Q2", 50),
    bar("2026-Q1", 60, { yoy: 20, prior_period_end: "2025-03-31" }),
    bar("2026-Q2", 75, { yoy: 50, prior_period_end: "2025-06-30" }),
  ],
  { set_aside: 1 },
);
export const CCC = company(
  "CCC",
  AXIS.slice(2).map((q, i) => bar(q, 10 + i, { yoy: i >= 4 ? 25 : null })),
);
/** UNR: a row with no resolved security. */
export const UNR = company("UNR", [], {
  company_key: "ticker:UNR",
  name: null,
  security_id: null,
  state: "unresolved",
  periods: 0,
});
/** EEE: resolved, no revenue stored. */
export const EEE = company("EEE", []);
/** OLD: a series that ends before the axis (its last print is two years old). */
export const OLD = company("OLD", [], {
  state: "stale",
  periods: 8,
  last_print: { quarter: "2022-Q4", period_end: "2022-12-31", filed: "2023-02-09", days_before: 1346, yoy: null },
});
/** DDD: a Context name with a series (silent). */
export const DDD = company(
  "DDD",
  AXIS.slice(4).map((q, i) => bar(q, 1 + i, { yoy: 900 })),
  { silent: true },
);

export const median = (
  counting: number,
  drawn: Record<string, [number, number]> = {},
  axis: string[] = AXIS,
): MultiplesMedianOut => ({
  counting,
  points: axis.map((q) => ({ quarter: q, median: drawn[q]?.[0] ?? null, n: drawn[q]?.[1] ?? 0 })),
});

export const row = (
  key: string,
  kind: MultiplesRowOut["kind"],
  label: string,
  companies: MultiplesCompanyOut[],
  med: MultiplesMedianOut | null,
  descriptor: string | null = null,
): MultiplesRowOut => ({
  key,
  kind,
  label,
  descriptor,
  companies: companies.map((c) => c.company_key),
  median: med,
});

export const ROWS: MultiplesRowOut[] = [
  row(
    "link:Turbines",
    "link",
    "Turbines",
    [AAA, BBB, UNR],
    median(3, { "2026-Q1": [20, 2], "2026-Q2": [35, 2], "2025-Q4": [20, 1] }),
    "make the power",
  ),
  row("link:Cables", "link", "Cables", [CCC, AAA, EEE], median(3, { "2025-Q4": [22.5, 2], "2026-Q1": [22.5, 2] })),
  row("link:Empty link", "link", "Empty link", [], median(0)),
  row("old:Old wires", "old_link", "Old wires", [OLD], median(1)),
  row("silent:Context", "silent", "Context", [DDD], null),
];

export const CARDS: MultiplesCardsOut = {
  asof: "2026-10-28",
  live_cards: 4,
  not_yet_created: 1,
  cards: [
    { thesis_id: "t-copper", name: "Copper", links: 4 },
    { thesis_id: "t-grid", name: "Grid", links: 3 },
    { thesis_id: "t-nuke", name: "Nuclear", links: 2 },
  ],
  cache: "hit",
  compute_ms: 0,
};

export const counts = (over: Partial<MultiplesCountsOut> = {}): MultiplesCountsOut => ({
  links_today: 3,
  old_links: 1,
  roster_rows: 9,
  resolved_rows: 8,
  unresolved_rows: 1,
  silent_rows: 1,
  companies: 7,
  counting: 6,
  silent_companies: 1,
  with_series: 5,
  without_series: 2,
  thin: 1,
  stale: 1,
  set_aside: 1,
  ...over,
});

export const payload = (over: Partial<MultiplesOut> = {}): MultiplesOut => ({
  asof: "2026-08-14",
  thesis_id: "t-grid",
  thesis_name: "Grid",
  clock: "public",
  roster_fallback: false,
  axis: AXIS,
  card_line: median(6, { "2025-Q4": [22.5, 2], "2026-Q1": [20, 3], "2026-Q2": [50, 3] }),
  rows: ROWS,
  companies: [AAA, BBB, CCC, UNR, EEE, OLD, DDD],
  counts: counts(),
  quarters: 12,
  thin_below: 5,
  min_median_names: 2,
  cache: "computed",
  compute_ms: 4200,
  ...over,
});
