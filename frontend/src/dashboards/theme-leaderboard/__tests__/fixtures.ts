import type {
  LeaderboardAdoptionOut,
  LeaderboardBreadthOut,
  LeaderboardCapMixOut,
  LeaderboardCardOut,
  LeaderboardInsidersOut,
  LeaderboardKeysOut,
  LeaderboardMedianOut,
  LeaderboardOut,
  LeaderboardTapeOut,
} from "../useThemeLeaderboard";

// Hand-built Theme Leaderboard payloads shared by the view-helper and component tests (the wire shape the backend
// test pins). Every card, company and ticker is a neutral fixture name.

export const ASOF = "2026-10-20";
export const MIXED = "mixed-scale price history";

export const tape = (over: Partial<LeaderboardTapeOut> = {}): LeaderboardTapeOut => ({
  value: 3,
  median: 4.5,
  k: 6,
  gap: null,
  ...over,
});

export const median = (value: number | null = 1.2, k = 9): LeaderboardMedianOut => ({ median: value, k });

export const breadth = (over: Partial<LeaderboardBreadthOut> = {}): LeaderboardBreadthOut => ({
  pct: 60,
  above: 6,
  counted: 10,
  thin: 0,
  above_200: 4,
  n_200: 8,
  ...over,
});

export const keys = (over: Partial<LeaderboardKeysOut> = {}): LeaderboardKeysOut => ({
  turned: 1,
  conviction: true,
  confirmation: false,
  ...over,
});

export const adoption = (over: Partial<LeaderboardAdoptionOut> = {}): LeaderboardAdoptionOut => ({
  slope_pct: 50,
  recent_docs: 150,
  prior_docs: 100,
  recent_from: "2026-07-01",
  recent_to: "2026-09-01",
  prior_from: "2026-04-01",
  prior_to: "2026-06-01",
  terms: 4,
  terms_counted: 4,
  gap: null,
  ...over,
});

export const insiders = (over: Partial<LeaderboardInsidersOut> = {}): LeaderboardInsidersOut => ({
  buyers: 2,
  read: 8,
  names: [{ name: "AAA", buyers: 2 }],
  ...over,
});

export const capMix = (over: Partial<LeaderboardCapMixOut> = {}): LeaderboardCapMixOut => ({
  nano: 1,
  micro: 2,
  small: 4,
  mid: 2,
  large: 0,
  blank: 1,
  ...over,
});

export const CARD_A = "00000000-0000-4000-8000-00000000000a";
export const CARD_B = "00000000-0000-4000-8000-00000000000b";
export const CARD_C = "00000000-0000-4000-8000-00000000000c";
export const CARD_D = "00000000-0000-4000-8000-00000000000d";

let n = 0;
export const card = (over: Partial<LeaderboardCardOut> = {}): LeaderboardCardOut => {
  n += 1;
  return {
    thesis_id: `20000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    thesis_name: `Card ${n}`,
    state: "warming",
    recorded_asof: "2026-10-19",
    reconstructed: false,
    counting: 10,
    priced: 9,
    flagged: 1,
    tape: tape(),
    ret_7d: median(),
    breadth: breadth(),
    keys: keys(),
    adoption: adoption(),
    insiders: insiders(),
    rs_leaders: 2,
    rs_n: 9,
    cap_mix: capMix(),
    armed_names: null,
    ...over,
  };
};

/** The cards the component tests share. Among all four, only A and B have every part: A leads B on every one of
 *  them (A scores 100, B 27); C has no adoption reading and D has no recorded call, so neither has a score. */
export function sample(): LeaderboardCardOut[] {
  const a = card({
    thesis_id: CARD_A,
    thesis_name: "Card A",
    state: "armed",
    counting: 12,
    priced: 11,
    flagged: 1,
    tape: tape({ value: 8, median: 9, k: 11 }),
    ret_7d: median(2.4, 11),
    breadth: breadth({ pct: 80, above: 8, counted: 10, above_200: 4, n_200: 8 }),
    keys: keys({ turned: 2, conviction: true, confirmation: true }),
    adoption: adoption({ slope_pct: 100, recent_docs: 200, prior_docs: 100 }),
    insiders: insiders({ buyers: 5, read: 9, names: [{ name: "AAA", buyers: 3 }, { name: "BBB", buyers: 2 }] }),
    rs_leaders: 3,
    rs_n: 9,
    armed_names: 2,
  });
  const b = card({
    thesis_id: CARD_B,
    thesis_name: "Card B",
    state: "warming",
    counting: 10,
    priced: 10,
    flagged: 0,
    tape: tape({ value: 3, median: 4, k: 10 }),
    ret_7d: median(-0.8, 10),
    breadth: breadth({ pct: 40, above: 4, counted: 10, above_200: 3, n_200: 9 }),
    keys: keys({ turned: 1, conviction: true, confirmation: false }),
    adoption: adoption({ slope_pct: 10, recent_docs: 110, prior_docs: 100 }),
    insiders: insiders({ buyers: 1, read: 10, names: [{ name: "CCC", buyers: 1 }] }),
    rs_leaders: 0,
    rs_n: 10,
  });
  const c = card({
    thesis_id: CARD_C,
    thesis_name: "Card C",
    state: "incubating",
    counting: 8,
    priced: 8,
    flagged: 0,
    tape: tape({ value: -2, median: -1, k: 8 }),
    ret_7d: median(0.1, 8),
    breadth: breadth({ pct: 60, above: 5, counted: 8, above_200: 2, n_200: 8 }),
    keys: keys({ turned: 0, conviction: false, confirmation: false }),
    adoption: adoption({
      slope_pct: null,
      recent_docs: null,
      prior_docs: null,
      terms: 3,
      terms_counted: 0,
      gap: "not_counted_yet",
    }),
    insiders: insiders({ buyers: 3, read: 8, names: [{ name: "DDD", buyers: 3 }] }),
    rs_leaders: 1,
    rs_n: 8,
  });
  const d = card({
    thesis_id: CARD_D,
    thesis_name: "Card D",
    state: null,
    recorded_asof: null,
    counting: 4,
    priced: 2,
    flagged: 0,
    tape: tape({ value: null, median: null, k: 2, gap: "thin" }),
    ret_7d: median(null, 2),
    breadth: breadth({ pct: 20, above: 1, counted: 5, thin: 1, above_200: 0, n_200: 0 }),
    keys: keys({ turned: null, conviction: null, confirmation: null }),
    adoption: adoption({
      slope_pct: null,
      recent_docs: null,
      prior_docs: null,
      terms: 0,
      terms_counted: 0,
      gap: "no_terms",
    }),
    insiders: insiders({ buyers: null, read: 0, names: [] }),
    rs_leaders: 0,
    rs_n: 0,
    cap_mix: capMix({ nano: 0, micro: 0, small: 0, mid: 0, large: 0, blank: 4 }),
  });
  return [a, b, c, d];
}

export function payload(over: Partial<LeaderboardOut> = {}): LeaderboardOut {
  const cards = over.cards ?? sample();
  return {
    asof: ASOF,
    clock: "public",
    benchmark: "IWM",
    iwm_ret_30d: 1.5,
    live_cards: cards.length + 1,
    cards_counted: cards.length,
    cards_not_yet_created: 1,
    roster_fallback_cards: 0,
    basket_rows: 60,
    silent_rows_left_out: 7,
    unresolved_rows: 0,
    names: 34,
    names_flagged: 1,
    cards,
    flagged: [{ security_id: "10000000-0000-4000-8000-000000000001", ticker: "FLG", reason: MIXED }],
    min_priced: 3,
    recent_months: 3,
    prior_months: 3,
    cache: "computed",
    compute_ms: 41_250,
    ...over,
  };
}
