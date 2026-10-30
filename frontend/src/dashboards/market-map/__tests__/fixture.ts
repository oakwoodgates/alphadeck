import type { MapCompany, MapMembership, MarketMapOut } from "../useMarketMap";

// Hand-built Market Map payloads for the tests (a shared builder, not a test file).

export const WINDOW_KEYS = ["ret_1d", "ret_7d", "ret_30d", "ret_90d", "ytd", "ret_1y"] as const;

export const company = (
  id: string,
  ticker: string,
  over: Partial<MapCompany> = {},
  r1d: number | null = 1,
): MapCompany => ({
  security_id: id,
  ticker,
  name: `${ticker} Inc`,
  business_type: "utilities",
  supersector: "energy_utilities",
  silent_only: false,
  returns: { ret_1d: r1d, ret_7d: 2, ret_30d: 3, ret_90d: 4, ytd: 5, ret_1y: 6 },
  gaps: {},
  priced: true,
  cap: 1_000_000_000,
  cap_tier: "small",
  cap_source: "workbench",
  cap_gap: null,
  cap_note: null,
  dollar_volume: 5_000_000,
  dollar_volume_note: null,
  ...over,
});

export const mem = (
  sid: string,
  tid: string,
  link: string | null,
  over: Partial<MapMembership> = {},
): MapMembership => ({
  security_id: sid,
  thesis_id: tid,
  ticker: over.ticker ?? sid.toUpperCase(),
  link,
  silent: false,
  ...over,
});

export const payload = (over: Partial<MarketMapOut> = {}): MarketMapOut => ({
  asof: "2026-06-01",
  live_cards: 2,
  cards_counted: 2,
  cards_not_yet_created: 0,
  roster_fallback_cards: 0,
  basket_rows: 0,
  silent_rows: 0,
  unresolved_rows: 0,
  unresolved_silent_rows: 0,
  companies_total: 0,
  cards: [
    { thesis_id: "t-grid", name: "Grid" },
    { thesis_id: "t-fuel", name: "Fuel" },
  ],
  memberships: [],
  companies: [],
  flagged: [],
  spy: { ret_1d: 0.5, ret_7d: 1, ret_30d: 2, ret_90d: 3, ytd: 4, ret_1y: 5 },
  iwm: null,
  windows: [...WINDOW_KEYS],
  dollar_volume_bars: 20,
  cache: "computed",
  compute_ms: 1200,
  ...over,
});

/**
 * Two cards. AAA sits on Grid (Turbines AND Cables) and on Fuel (Core); BBB and CCC on Grid / Turbines; DDD and EEE
 * on Fuel / Core. CTX is Context on Grid only. Caps: AAA 8, BBB 4, CCC 2, DDD 1, EEE none (in $B), so EEE is the
 * no-cap company; the 1d returns are 1, 3, -2, 5, null (EEE has no tape).
 */
export function twoCards(): MarketMapOut {
  const companies = [
    company("aaa", "AAA", { cap: 8e9, cap_tier: "small", supersector: "technology", business_type: "semiconductors" }, 1),
    company("bbb", "BBB", { cap: 4e9 }, 3),
    company("ccc", "CCC", { cap: 2e9 }, -2),
    company("ddd", "DDD", { cap: 1e9 }, 5),
    company(
      "eee",
      "EEE",
      {
        cap: null,
        cap_tier: null,
        cap_source: null,
        cap_gap: "no_shares",
        priced: false,
        returns: { ret_1d: null, ret_7d: null, ret_30d: null, ret_90d: null, ytd: null, ret_1y: null },
        gaps: { ret_1d: "no price bars at the as-of", ret_90d: "61 of 91 bars" },
        dollar_volume: null,
        dollar_volume_note: "no price bars at the as-of",
        supersector: null,
        business_type: null,
      },
      null,
    ),
    company("ctx", "CTX", { silent_only: true, cap: 3e9 }, 0.5),
  ];
  const memberships = [
    mem("aaa", "t-grid", "Turbines", { ticker: "AAA" }),
    mem("aaa", "t-grid", "Cables", { ticker: "AAA" }),
    mem("aaa", "t-fuel", "Core", { ticker: "AAA.F" }),
    mem("bbb", "t-grid", "Turbines", { ticker: "BBB" }),
    mem("ccc", "t-grid", "Turbines", { ticker: "CCC" }),
    mem("ddd", "t-fuel", "Core", { ticker: "DDD" }),
    mem("eee", "t-fuel", "Core", { ticker: "EEE" }),
    mem("ctx", "t-grid", "Context", { ticker: "CTX", silent: true }),
  ];
  return payload({ companies, memberships, basket_rows: 8, silent_rows: 1, companies_total: 6 });
}
