import type {
  HotBoardOut,
  HotCapOut,
  HotCellOut,
  HotClocksOut,
  HotRowOut,
  HotSinceArmOut,
  HotTapeOut,
  HotTriggerOut,
} from "../useHotBoard";

// Hand-built Hot Board payloads shared by the view-helper and component tests (the wire shape the backend test
// pins). Every card, company and ticker is a neutral fixture name.

export const ASOF = "2026-10-28";
export const MIXED = "mixed-scale price history";

export const cell = (value: number | null, note: string | null = null): HotCellOut => ({ value, note });

export const tape = (over: Partial<HotTapeOut> = {}): HotTapeOut => ({
  flag: null,
  rvol20: cell(1.5),
  vs_50d: cell(4.2),
  off_high: cell(-3.1),
  vs_iwm: cell(2.0),
  rs_leading: false,
  rs_leading_vs: [],
  ret_7d: cell(1.1),
  ret_30d: cell(6.5),
  buys_30d: 0,
  buyers_30d: 0,
  adv_usd_20d: cell(2_500_000),
  spark: [null, 10, 11, 12],
  ...over,
});

export const flaggedTape = (reason = MIXED): HotTapeOut =>
  tape({
    flag: reason,
    rvol20: cell(null, `flagged: ${reason}`),
    vs_50d: cell(null, `flagged: ${reason}`),
    off_high: cell(null, `flagged: ${reason}`),
    vs_iwm: cell(null, `flagged: ${reason}`),
    ret_7d: cell(null, `flagged: ${reason}`),
    ret_30d: cell(null, `flagged: ${reason}`),
    adv_usd_20d: cell(null, `flagged: ${reason}`),
    spark: [],
  });

export const cap = (value: number | null, tier: HotCapOut["tier"], over: Partial<HotCapOut> = {}): HotCapOut => ({
  value,
  tier,
  gap: null,
  note: null,
  close: 25,
  close_date: ASOF,
  shares: 20_000_000,
  shares_date: "2026-05-10",
  ads_ratio: null,
  split_factor: null,
  ...over,
});

export const clocks = (over: Partial<HotClocksOut> = {}): HotClocksOut => ({
  arm_night: "2026-10-26",
  arm_reconstructed: false,
  sessions_since: 2,
  arm_until: "2026-10-30",
  sessions_left: 2,
  closed_ago: null,
  exit_by: "2026-12-01",
  days_to_exit: 34,
  ...over,
});

export const since = (over: Partial<HotSinceArmOut> = {}): HotSinceArmOut => ({
  entry_date: "2026-10-26",
  entry_close: 10,
  last_date: ASOF,
  last_close: 11,
  ret_pct: 10,
  iwm_pct: 1.5,
  gap: null,
  ...over,
});

export const trigger = (over: Partial<HotTriggerOut> = {}): HotTriggerOut => ({
  label: "Insider cluster buy",
  kind: "insider",
  grade: "core",
  event_date: "2026-10-20",
  sources: [{ source: "form4", ref: "0001234567-26-000001", url: "https://www.sec.gov/Archives/edgar/data/9999999/000123456726000001/" }],
  ...over,
});

const CARD_A = "00000000-0000-4000-8000-00000000000a";
const CARD_B = "00000000-0000-4000-8000-00000000000b";
const CARD_C = "00000000-0000-4000-8000-00000000000c";
export { CARD_A, CARD_B, CARD_C };

let n = 0;
export const row = (over: Partial<HotRowOut> = {}): HotRowOut => {
  n += 1;
  const sid = `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
  return {
    key: `${CARD_A}:${sid}`,
    kind: "armed",
    thesis_id: CARD_A,
    thesis_name: "Card A",
    security_id: sid,
    ticker: `TK${n}`,
    name: `Company ${n}`,
    also_armed: [],
    verdict: "core_entry",
    entry_grade: "core",
    confidence: 0.62,
    keys: [],
    triggers: [trigger()],
    risk_kinds: [],
    clocks: clocks(),
    entry_window_closing: false,
    lapsing: false,
    since_arm: since(),
    tape: tape(),
    cap: cap(5e8, "small"),
    ...over,
  };
};

/** The sample the component tests share: three armed names on two cards (one also armed on the other card), a
 *  Warming name, a watch member, a Managing member and a flagged armed name. */
export function sample() {
  const aaa = row({
    ticker: "AAA",
    name: "Alpha Co",
    clocks: clocks({ sessions_since: 0, arm_night: ASOF }),
    since_arm: since({ ret_pct: 0, iwm_pct: 0 }),
    tape: tape({ rvol20: cell(3.1), vs_iwm: cell(8), rs_leading: true, rs_leading_vs: ["IWM"], buys_30d: 2, buyers_30d: 2 }),
    also_armed: [{ thesis_id: CARD_B, thesis_name: "Card B" }],
    entry_window_closing: true,
  });
  const bbb = row({
    ticker: "BBB",
    name: "Bravo Co",
    thesis_id: CARD_B,
    thesis_name: "Card B",
    entry_grade: "flip",
    verdict: "flip_only",
    confidence: 0.4,
    triggers: [trigger({ kind: "technical_breakout", label: "8-day breakout", grade: "flip", sources: [] })],
    clocks: clocks({ sessions_since: 5, arm_until: "2026-10-26", sessions_left: null, closed_ago: 2 }),
    since_arm: since({ ret_pct: -4.5, iwm_pct: 1.5 }),
    tape: tape({ rvol20: cell(0.8), vs_iwm: cell(-2) }),
    cap: cap(1.2e9, "small"),
  });
  const ccc = row({
    ticker: "CCC",
    name: "Charlie Co",
    thesis_id: CARD_B,
    thesis_name: "Card B",
    clocks: clocks({ sessions_since: 1, arm_until: null, sessions_left: null, exit_by: null, days_to_exit: null }),
    since_arm: since({ ret_pct: 22.5, iwm_pct: 1 }),
    tape: tape({ rvol20: cell(1.1), vs_iwm: cell(0.5), buys_30d: null, buyers_30d: null }),
    cap: cap(3e10, "large"),
  });
  const flagged = row({
    ticker: "FLG",
    name: "Flagged Co",
    clocks: clocks({ sessions_since: 3 }),
    since_arm: since({ ret_pct: null, iwm_pct: null, gap: "flagged", entry_date: null, entry_close: null, last_date: null, last_close: null }),
    tape: flaggedTape(),
    cap: cap(null, null, { gap: "flagged", note: MIXED, close: null, close_date: null, shares: null, shares_date: null }),
  });
  const warming = row({
    ticker: "WRM",
    kind: "warming",
    thesis_id: CARD_C,
    thesis_name: "Card C",
    verdict: null,
    entry_grade: null,
    confidence: null,
    keys: ["conviction"],
    clocks: clocks({ arm_night: null, sessions_since: null, arm_until: null, sessions_left: null, exit_by: null, days_to_exit: null }),
    since_arm: since({ ret_pct: null, iwm_pct: null, gap: "no_arm_night", entry_date: null, entry_close: null, last_date: null, last_close: null }),
  });
  const watch = row({
    ticker: "WTC",
    kind: "watch",
    verdict: null,
    entry_grade: null,
    confidence: null,
    triggers: [trigger({ kind: "technical_breakout", label: "8-day breakout", sources: [] })],
    clocks: clocks({ arm_night: null, sessions_since: null }),
    since_arm: since({ ret_pct: null, iwm_pct: null, gap: "no_arm_night", entry_date: null, entry_close: null, last_date: null, last_close: null }),
  });
  const managing = row({
    ticker: "MNG",
    kind: "managing",
    thesis_id: CARD_C,
    thesis_name: "Card C",
    verdict: "managing",
    clocks: clocks({ sessions_since: 9, arm_until: null, sessions_left: null }),
    since_arm: since({ ret_pct: 30, iwm_pct: 2 }),
    tape: tape({ rvol20: cell(2.2), vs_iwm: cell(1) }),
  });
  return { aaa, bbb, ccc, flagged, warming, watch, managing };
}

export function payload(rows: HotRowOut[] = Object.values(sample()), over: Partial<HotBoardOut> = {}): HotBoardOut {
  return {
    asof: ASOF,
    clock: "public",
    benchmark: "IWM",
    live_cards: 4,
    cards_counted: 4,
    cards_not_yet_created: 0,
    roster_fallback_cards: 0,
    cards_armed: 2,
    cards_warming: 1,
    cards_managing: 1,
    cards_incubating: 0,
    cards_no_call: 0,
    calendar_source: "benchmarks",
    calendar_sessions: 250,
    calendar_first: "2025-10-29",
    calendar_last: ASOF,
    price_window_start: "2025-07-25",
    no_ticker_rows: 0,
    rows,
    flagged: rows
      .filter((r) => r.tape.flag !== null)
      .map((r) => ({ security_id: r.security_id, ticker: r.ticker, reason: r.tape.flag as string })),
    cache: "computed",
    compute_ms: 1234,
    ...over,
  };
}
