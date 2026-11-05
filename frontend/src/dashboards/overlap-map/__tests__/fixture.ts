import type { OverlapCompanyOut, OverlapMapOut } from "../useOverlapMap";

// A hand-built payload (synthetic names and tickers only). Five cards:
//   0 Alpha (AAA BBB CCC DDD) · 1 Beta (BBB CCC DDD EEE) · 2 Gamma (DDD FFF GGG) · 3 Delta (GGG HHH) · 4 Solo (SOL)
// Pairs: Alpha-Beta share 3 (BBB CCC DDD); Alpha-Gamma 1 (DDD); Beta-Gamma 1 (DDD); Gamma-Delta 1 (GGG).
// Solo shares nothing, so it has no position (the strip).

const co = (
  n: number,
  ticker: string,
  supersector: string | null,
  business_type: string | null,
  rows: [number, (string | null)[]][],
): OverlapCompanyOut => ({
  security_id: `sec-${n}`,
  ticker,
  name: `${ticker} Corp`,
  supersector,
  business_type,
  rows: rows.map(([card, links]) => ({ card, ticker, links, silent: false, context_role: null })),
});

export const COMPANIES: OverlapCompanyOut[] = [
  co(0, "AAA", "technology", "software_it", [[0, ["Core"]]]),
  co(1, "BBB", "technology", "software_it", [[0, ["Core"]], [1, ["Edge"]]]),
  co(2, "CCC", "materials", "miner", [[0, ["Core", "Edge"]], [1, ["Core"]]]),
  co(3, "DDD", "materials", "miner", [[0, ["Core"]], [1, ["Core"]], [2, ["Core"]]]),
  co(4, "EEE", null, null, [[1, ["Core"]]]),
  co(5, "FFF", "healthcare", "biotech_pharma", [[2, ["Core"]]]),
  co(6, "GGG", "other", "other", [[2, ["Edge"]], [3, ["Core"]]]),
  co(7, "HHH", "technology", "semiconductors", [[3, ["Core"]]]),
  co(8, "SOL", null, null, [[4, [null]]]),
];

export function overlapFixture(over: Partial<OverlapMapOut> = {}): OverlapMapOut {
  return {
    asof: "2026-11-04",
    include_silent: false,
    clock: "record",
    box_w: 1000,
    box_h: 640,
    live_cards: 6,
    cards_counted: 5,
    cards_not_yet_created: 1,
    roster_fallback_cards: 0,
    basket_rows: 20,
    silent_rows_left_out: 3,
    unresolved_rows: 1,
    companies_in_view: 9,
    companies_shared: 4,
    cards: [
      { thesis_id: "t-alpha", name: "Alpha", companies: 4, x: 200, y: 200, r: 40, shares_nothing: false },
      { thesis_id: "t-beta", name: "Beta", companies: 4, x: 320, y: 200, r: 40, shares_nothing: false },
      { thesis_id: "t-gamma", name: "Gamma", companies: 3, x: 260, y: 320, r: 30, shares_nothing: false },
      { thesis_id: "t-delta", name: "Delta", companies: 2, x: 260, y: 420, r: 20, shares_nothing: false },
      { thesis_id: "t-solo", name: "Solo", companies: 1, x: null, y: null, r: 7, shares_nothing: true },
    ],
    pairs: [
      { a: 0, b: 1, shared: 3 },
      { a: 0, b: 2, shared: 1 },
      { a: 1, b: 2, shared: 1 },
      { a: 2, b: 3, shared: 1 },
    ],
    groups: [
      { threshold: 1, sizes: [4], no_line: 1 },
      { threshold: 2, sizes: [2], no_line: 3 },
      { threshold: 3, sizes: [2], no_line: 3 },
      { threshold: 4, sizes: [], no_line: 5 },
      { threshold: 5, sizes: [], no_line: 5 },
    ],
    companies: COMPANIES,
    cache: "computed",
    compute_ms: 900,
    ...over,
  };
}
