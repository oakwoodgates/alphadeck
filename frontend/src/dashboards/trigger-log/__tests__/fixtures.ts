import type { TriggerLogOut, TriggerLogRowOut } from "../useTriggerLog";

// Hand-built payloads for the Trigger Log's tests: the shape the server returns, no more.

export const ACC = "0000876543-26-000010";
export const EDGAR = "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/";

export const row = (key: string, over: Partial<TriggerLogRowOut> = {}): TriggerLogRowOut => ({
  key,
  thesis_id: "t-a",
  thesis_name: "Card A",
  security_id: "s-aaa",
  ticker: "AAA",
  role: "entry_trigger",
  kind: "insider",
  grade: "core",
  turns: "conviction",
  label: "Open-market buy, 1,000 sh",
  event_date: "2026-10-22",
  recorded_on: "2026-10-27",
  week: "2026-10-26",
  recorded_days_after: 5,
  reconstructed: false,
  from_state: "warming",
  to_state: "armed",
  new_card: false,
  live: true,
  newest: false,
  sessions_since: 1,
  since: { status: "priced", reason: null, entry_date: "2026-10-27", end_date: "2026-10-28", ret: 0.1, bench_ret: 0.01 },
  sources: [{ source: "form4", ref: ACC, url: EDGAR, bar_date: null, detail: ["shares: 1000"] }],
  ...over,
});

/** Four rows over two cards and two weeks: a fresh insider buy recorded on the as-of's own night (new card), a
 *  breakout that moved a state, a reconstructed risk signal on a flagged name, and an unpriced laggard on a name
 *  with no ticker. */
export const R1 = row("r1", {
  recorded_on: "2026-10-28",
  newest: true,
  from_state: null,
  to_state: "warming",
  new_card: true,
  recorded_days_after: 6,
  since: { status: "priced", reason: null, entry_date: "2026-10-28", end_date: "2026-10-28", ret: 0, bench_ret: 0 },
  sessions_since: 0,
});
export const R2 = row("r2", {
  security_id: "s-bbb",
  ticker: "BBB",
  kind: "technical_breakout",
  grade: "flip",
  turns: "confirmation",
  label: "Volume-backed breakout: close 12.00 cleared the 8-day high",
  event_date: "2026-10-27",
  recorded_days_after: null,
  since: { status: "priced", reason: null, entry_date: "2026-10-27", end_date: "2026-10-28", ret: -0.042, bench_ret: 0.01 },
  sources: [{ source: "price", ref: "price:s-bbb:2026-10-27", url: null, bar_date: "2026-10-27", detail: [] }],
});
export const R3 = row("r3", {
  thesis_id: "t-b",
  thesis_name: "Card B",
  security_id: "s-ccc",
  ticker: "CCC",
  role: "risk_signal",
  kind: "dilution_risk",
  grade: null,
  turns: null,
  label: "Convertible note overhang",
  event_date: "2026-10-21",
  recorded_on: "2026-10-21",
  week: "2026-10-19",
  recorded_days_after: null,
  reconstructed: true,
  from_state: "armed",
  to_state: "armed",
  live: false,
  sessions_since: null,
  since: { status: "flagged", reason: "mixed-scale price history", entry_date: null, end_date: null, ret: null, bench_ret: null },
  sources: [],
});
export const R4 = row("r4", {
  thesis_id: "t-b",
  thesis_name: "Card B",
  security_id: "s-ddd",
  ticker: null,
  kind: "laggard",
  grade: "flip",
  turns: "confirmation",
  label: "Laggard rotation: 4% over 20d trails the basket median",
  event_date: null,
  recorded_on: "2026-10-20",
  week: "2026-10-19",
  recorded_days_after: null,
  from_state: "warming",
  to_state: "warming",
  live: null,
  sessions_since: null,
  since: { status: "unpriced", reason: null, entry_date: null, end_date: null, ret: null, bench_ret: null },
  sources: [{ source: "price", ref: "price:s-ddd:2026-10-19", url: null, bar_date: "2026-10-19", detail: ["return: 0.04"] }],
});

export const ROWS = [R1, R2, R3, R4];

export const page = (rows: TriggerLogOut["rows"] = ROWS, over: Partial<TriggerLogOut> = {}): TriggerLogOut => ({
  asof: "2026-10-28",
  window_days: 90,
  window_start: "2026-07-30",
  clock: "public",
  rows,
  counts: {
    cards_counted: 3,
    cards_with_nights: 2,
    cards_quiet: 1,
    cards_no_call: 0,
    live_cards: 4,
    not_yet_created: 1,
    roster_fallback: 0,
    nights_read: 9,
    nights_reconstructed: 1,
    nights_unreadable: 0,
    entry_rows: rows.filter((r) => r.role === "entry_trigger").length,
    risk_rows: rows.filter((r) => r.role === "risk_signal").length,
    carried_live: 2,
    names_unpriced: 1,
    names_flagged: 1,
    rows_no_ticker: rows.filter((r) => r.ticker == null).length,
  },
  sessions: { source: "benchmarks", count: 63, first: "2026-07-30", last: "2026-10-28" },
  unpriced: [{ security_id: "s-ddd", ticker: null, reason: null }],
  flagged: [{ security_id: "s-ccc", ticker: "CCC", reason: "mixed-scale price history" }],
  cache: "computed",
  compute_ms: 412,
  ...over,
});
