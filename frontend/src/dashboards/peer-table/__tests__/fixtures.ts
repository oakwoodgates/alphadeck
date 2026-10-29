import type {
  Gap,
  PeerCapOut,
  PeerCardChipOut,
  PeerCellOut,
  PeerCompanyOut,
  PeerCoverageOut,
  PeerGroupOut,
  PeerGroupStatsOut,
  PeerInsiderOut,
  PeerPurityOut,
  PeerRevenueOut,
  PeerRunwayOut,
  PeerScopesOut,
  PeerSpyOut,
  PeerTableOut,
  PeerYoyOut,
} from "../usePeerTable";

// Hand-built Peer Table payloads shared by the view-helper and component tests (the wire shape the backend test
// pins: four miners, one of them Context-only with a flagged price history).

export const MIXED = "mixed-scale price history";

export const num = (value: number | null, gap: Gap | null = null, note: string | null = null): PeerCellOut => ({
  value,
  gap,
  note,
});
export const blank = (gap: Gap, note: string | null = null): PeerCellOut => num(null, gap, note);

export const cap = (value: number | null, tier: PeerCapOut["tier"], over: Partial<PeerCapOut> = {}): PeerCapOut => ({
  value,
  tier,
  gap: null,
  note: null,
  close: 25,
  close_date: "2026-10-28",
  shares: 10_000_000,
  shares_date: "2026-05-10",
  ads_ratio: null,
  split_factor: null,
  ...over,
});
export const capBlank = (gap: Gap, note: string | null = null): PeerCapOut =>
  cap(null, null, { gap, note, close: null, close_date: null, shares: null, shares_date: null });

export const revenue = (value: number | null, period_end = "2026-06-30", gap: Gap | null = null): PeerRevenueOut => ({
  value,
  period_end: value == null ? null : period_end,
  gap,
});
export const yoy = (value: number | null, gap: Gap | null = null): PeerYoyOut => ({
  value,
  prior_value: value == null ? null : 100_000_000,
  prior_period_end: value == null ? null : "2025-06-30",
  gap,
});
export const runway = (over: Partial<PeerRunwayOut> = {}): PeerRunwayOut => ({
  months: 12,
  cash_positive: false,
  cash_usd: 12_000_000,
  burn_usd: 3_000_000,
  as_of: "2026-05-10",
  gap: null,
  ...over,
});
export const purity = (value: number | null, segment: string | null = null, gap: Gap | null = null): PeerPurityOut => ({
  value,
  segment,
  gap,
});
export const spy = (value: number | null, over: Partial<PeerSpyOut> = {}): PeerSpyOut => ({
  value,
  ret_pct: value == null ? null : value + 4,
  spy_pct: value == null ? null : 4,
  last_close: "2026-10-28",
  gap: null,
  note: null,
  ...over,
});
export const insider = (over: Partial<PeerInsiderOut> = {}): PeerInsiderOut => ({
  buys: null,
  usd: null,
  buyers: null,
  gap: "no_form4",
  note: null,
  ...over,
});

export const chip = (over: Partial<PeerCardChipOut> = {}): PeerCardChipOut => ({
  thesis_id: "t-grid",
  thesis_name: "Grid",
  ticker: "AAA",
  links: ["Turbines"],
  silent: false,
  role: null,
  ...over,
});

const BLANK_FLAGGED = (): Pick<
  PeerCompanyOut,
  "off_high" | "sma50" | "sma200" | "rvol20" | "adv" | "vs_spy"
> => ({
  off_high: blank("flagged", MIXED),
  sma50: blank("flagged", MIXED),
  sma200: blank("flagged", MIXED),
  rvol20: blank("flagged", MIXED),
  adv: blank("flagged", MIXED),
  vs_spy: spy(null, { gap: "flagged", note: MIXED, ret_pct: null, spy_pct: null, last_close: null }),
});

export const company = (key: string, over: Partial<PeerCompanyOut> = {}): PeerCompanyOut => ({
  key,
  security_id: `s-${key}`,
  ticker: key.toUpperCase(),
  name: null,
  business_type: "miner",
  supersector: "materials",
  royalty: false,
  cards: [chip({ ticker: key.toUpperCase() })],
  roles: [],
  silent_only: false,
  rows: 1,
  workbench_facts: false,
  flag: null,
  cap: capBlank("no_price"),
  revenue: revenue(null, undefined, "no_revenue"),
  yoy: yoy(null, "no_revenue"),
  runway: runway({ months: null, cash_usd: null, burn_usd: null, as_of: null, gap: "no_burn" }),
  overhang: blank("no_convert"),
  purity: purity(null, null, "no_mix"),
  off_high: blank("no_price"),
  sma50: blank("no_price"),
  sma200: blank("no_price"),
  rvol20: blank("no_price"),
  adv: blank("no_price"),
  vs_spy: spy(null, { gap: "no_price", last_close: null }),
  insider: insider(),
  ...over,
});

// AAA: a micro cap of $250M, above both averages, +6.0 vs SPY, revenue +20% YoY, 12 months of runway, 3 buys
export const AAA = company("aaa", {
  name: "Acme Mining",
  workbench_facts: true,
  cap: cap(250_000_000, "micro"),
  revenue: revenue(120_000_000),
  yoy: yoy(20),
  runway: runway(),
  overhang: num(6.0),
  purity: purity(61.5, "nuclear"),
  off_high: num(-10.5),
  sma50: num(5.2),
  sma200: num(8.1),
  rvol20: num(1.4),
  adv: num(1_200_000),
  vs_spy: spy(6),
  insider: insider({ buys: 3, usd: 1_250_000, buyers: 2, gap: null }),
});

// BBB: a $1B small cap on two cards (a payer on one), below its 50d, no 200d reading, cash-positive, a stated zero
export const BBB = company("bbb", {
  name: "Bolt Metals",
  cards: [
    chip({ thesis_id: "t-batt", thesis_name: "Batteries", ticker: "BBB", links: ["Cells"], role: "payer" }),
    chip({ ticker: "BBB", links: ["Turbines", "Cables"] }),
  ],
  roles: ["payer"],
  rows: 3,
  workbench_facts: true,
  cap: cap(1_000_000_000, "small", { close: 50, shares: 20_000_000 }),
  revenue: revenue(50_000_000),
  yoy: yoy(null, "no_prior_year"),
  runway: runway({ months: null, cash_positive: true }),
  sma50: num(-3),
  sma200: blank("no_reading", "n/a: 120/200 bars"),
  vs_spy: spy(-6),
  insider: insider({ buys: 0, usd: 0, buyers: 0, gap: null }),
});

// CCC: no shares (no cap), +1.0 vs SPY, revenue -10%, no cash/burn fact, no Form 4
export const CCC = company("ccc", {
  name: "Cobalt Co",
  business_type: "miner",
  cards: [chip({ ticker: "CCC", links: ["Cables"] })],
  cap: capBlank("no_shares"),
  revenue: revenue(90_000_000),
  yoy: yoy(-10),
  sma50: num(1),
  sma200: num(-2),
  rvol20: num(0.8),
  adv: num(300_000),
  vs_spy: spy(1),
});

// DDD: Context-only, a flagged price history, revenue +50%, 6 months of runway
export const DDD = company("ddd", {
  name: "Delta Dig",
  cards: [chip({ ticker: "DDD", links: ["Context"], silent: true })],
  silent_only: true,
  flag: MIXED,
  cap: capBlank("flagged", MIXED),
  revenue: revenue(150_000_000),
  yoy: yoy(50),
  runway: runway({ months: 6, cash_usd: 6_000_000 }),
  ...BLANK_FLAGGED(),
});

export const COMPANIES = [AAA, BBB, CCC, DDD];

const stats = (over: Partial<PeerGroupStatsOut> = {}): PeerGroupStatsOut => ({
  names: 4,
  cap: { value: 625_000_000, n: 2 },
  above_50d: 2,
  priced_50d: 3,
  vs_spy: { value: 1, n: 3 },
  yoy: { value: 20, n: 3 },
  runway: 3,
  ...over,
});

export const GROUP: PeerGroupOut = {
  key: "type:miner",
  label: "miner",
  kind: "business_type",
  depth: 0,
  members: COMPANIES.map((c) => c.key),
  stats: stats(),
  stats_counting: stats({ names: 3, yoy: { value: 5, n: 2 }, runway: 2 }),
};

const cov = (column: PeerCoverageOut["column"], stated: number, total: number, blanks: PeerCoverageOut["blanks"] = []): PeerCoverageOut => ({
  column,
  stated,
  total,
  blanks,
});

export const COVERAGE: PeerCoverageOut[] = [
  cov("cap", 2, 4, [
    { gap: "flagged", n: 1 },
    { gap: "no_shares", n: 1 },
  ]),
  cov("revenue", 4, 4),
  cov("yoy", 3, 4, [{ gap: "no_prior_year", n: 1 }]),
  cov("runway", 3, 4, [{ gap: "no_burn", n: 1 }]),
  cov("overhang", 1, 4, [{ gap: "no_convert", n: 3 }]),
  cov("purity", 1, 4, [{ gap: "no_mix", n: 3 }]),
  cov("off_high", 1, 4, [
    { gap: "flagged", n: 1 },
    { gap: "no_price", n: 2 },
  ]),
  cov("sma50", 3, 4, [{ gap: "flagged", n: 1 }]),
  cov("sma200", 2, 4, [
    { gap: "flagged", n: 1 },
    { gap: "no_reading", n: 1 },
  ]),
  cov("rvol20", 2, 4, [
    { gap: "flagged", n: 1 },
    { gap: "no_price", n: 1 },
  ]),
  cov("adv", 2, 4, [
    { gap: "flagged", n: 1 },
    { gap: "no_price", n: 1 },
  ]),
  cov("vs_spy", 3, 4, [{ gap: "flagged", n: 1 }]),
  cov("insider", 2, 4, [{ gap: "no_form4", n: 2 }]),
];

export const COVERAGE_COUNTING: PeerCoverageOut[] = COVERAGE.map((c) =>
  c.column === "cap" ? cov("cap", 2, 3, [{ gap: "no_shares", n: 1 }]) : { ...c, total: 3 },
);

export const COLUMN_KEYS = COVERAGE.map((c) => c.column);

export const payload = (over: Partial<PeerTableOut> = {}): PeerTableOut => ({
  asof: "2026-10-28",
  clock: "public",
  scope: "business_type",
  key: "miner",
  scope_name: null,
  live_cards: 4,
  cards_counted: 2,
  cards_not_yet_created: 1,
  roster_fallback_cards: 0,
  scope_cards: 2,
  rows: 6,
  silent_only: 1,
  companies: COMPANIES,
  groups: [GROUP],
  unresolved: [],
  unresolved_rows: 0,
  flagged: [{ security_id: "s-ddd", ticker: "DDD", reason: MIXED }],
  coverage: COVERAGE,
  coverage_counting: COVERAGE_COUNTING,
  spy: { ret_30d: 4, end: "2026-10-28" },
  benchmark: "SPY",
  columns: COLUMN_KEYS,
  cache: "hit",
  compute_ms: 900,
  ...over,
});

export const scopesPayload = (over: Partial<PeerScopesOut> = {}): PeerScopesOut => ({
  asof: "2026-10-28",
  clock: "public",
  live_cards: 4,
  cards_counted: 2,
  cards_not_yet_created: 1,
  roster_fallback_cards: 0,
  companies: 6,
  unresolved_rows: 0,
  business_types: [
    { key: "miner", companies: 4 },
    { key: "bank", companies: 1 },
    { key: "unclassified", companies: 1 },
  ],
  supersectors: [
    { key: "materials", companies: 4 },
    { key: "financials", companies: 1 },
    { key: "unclassified", companies: 1 },
  ],
  cards: [
    { thesis_id: "t-batt", name: "Batteries", links: 1, companies: 1 },
    { thesis_id: "t-grid", name: "Grid", links: 2, companies: 4 },
  ],
  default_scope: "business_type",
  default_key: "miner",
  cache: "hit",
  compute_ms: 40,
  ...over,
});
