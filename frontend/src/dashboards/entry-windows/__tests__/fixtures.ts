import type {
  EntryWindowsCountsOut,
  EntryWindowsMemberClockOut,
  EntryWindowsMemberOut,
  EntryWindowsOut,
  EntryWindowsRowOut,
  EntryWindowsSinceOut,
} from "../useEntryWindows";

// Fixtures for the Entry Windows tests: a Tuesday as-of and a handful of cards covering every cell of the page.

export const ASOF = "2026-10-27";

export const since = (over: Partial<EntryWindowsSinceOut> = {}): EntryWindowsSinceOut => ({
  names: 2,
  priced: 2,
  flagged: 0,
  no_bar: 0,
  above: 1,
  median: 4.5,
  iwm: 1.0,
  spy: -2.0,
  ...over,
});

export const member = (over: Partial<EntryWindowsMemberOut> = {}): EntryWindowsMemberOut => ({
  security_id: "s-aaa",
  ticker: "AAA",
  arm_night: "2026-10-20",
  entry_on: "2026-10-20",
  since: 4.5,
  above: true,
  flag: null,
  triggers: [
    {
      label: "8-day breakout",
      kind: "technical_breakout",
      grade: "core",
      event_date: "2026-10-19",
      sources: [
        { source: "form4", ref: "0001234567-26-000123", url: "https://www.sec.gov/Archives/edgar/data/123456/000123456726000123/" },
        { source: "price", ref: "price:AAA:2026-10-19", url: null },
      ],
    },
  ],
  ...over,
});

/** A flagged armed name's own clock (the quiet default: neither flag, no entry figure). */
export const memberClock = (over: Partial<EntryWindowsMemberClockOut> = {}): EntryWindowsMemberClockOut => ({
  security_id: "s-aaa",
  ticker: "AAA",
  headline: false,
  entry_window_closing: false,
  lapsing: false,
  arm_until: null,
  sessions_left: null,
  closed_ago: null,
  ...over,
});

export const row = (over: Partial<EntryWindowsRowOut> = {}): EntryWindowsRowOut => ({
  thesis_id: "t-a",
  thesis_name: "Card A",
  state: "armed",
  arm_night: "2026-10-20",
  in_effect: "2026-10-23",
  reconstructed: false,
  reconstructed_nights: 0,
  run_nights: 2,
  arm_until: "2026-10-30",
  exit_by: "2026-11-14",
  confidence: 0.62,
  entry_window_closing: false,
  lapsing: false,
  sessions_into: 5,
  sessions_left: 3,
  closed_ago: null,
  hold_days: 18,
  member_clocks: [],
  catalysts: [
    { label: "readout", kind: "clinical_readout", when_date: "2026-10-29", past: false },
    { label: "earnings", kind: "earnings", when_date: "2026-10-22", past: true },
  ],
  members: [member(), member({ security_id: "s-bbb", ticker: "BBB", arm_night: "2026-10-23", since: -1, above: false, triggers: [] })],
  since: since(),
  spark: {
    dates: ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"],
    line: [100, 101, 103, 102, 104, 104.5],
    iwm: [100, 100.2, 100.4, 100.6, 100.8, 101],
  },
  ...over,
});

/** Card B: one session left, the card's own flag set. */
export const CLOSING = row({
  thesis_id: "t-b",
  thesis_name: "Card B",
  arm_night: "2026-10-23",
  arm_until: "2026-10-28",
  sessions_left: 1,
  sessions_into: 2,
  entry_window_closing: true,
  lapsing: true,
  member_clocks: [
    memberClock({
      security_id: "s-ccc",
      ticker: "CCC",
      headline: true,
      entry_window_closing: true,
      lapsing: true,
      arm_until: "2026-10-28",
      sessions_left: 1,
    }),
  ],
  catalysts: [],
  members: [member({ security_id: "s-ccc", ticker: "CCC", arm_night: "2026-10-23" })],
  spark: null,
});
/** Card P: the headline name's window runs to Fri 10-30 (3 sessions left); its second name's own window ends Wed
 *  10-28 (1 session left) and its hold clock is closing. The accent is the second name's. */
export const PAIR = row({
  thesis_id: "t-p",
  thesis_name: "Card P",
  arm_until: "2026-10-30",
  sessions_left: 3,
  entry_window_closing: true,
  lapsing: true,
  member_clocks: [
    memberClock({
      security_id: "s-bbb",
      ticker: "BBB",
      headline: false,
      entry_window_closing: true,
      lapsing: true,
      arm_until: "2026-10-28",
      sessions_left: 1,
    }),
  ],
});
/** Card C: the entry window closed three sessions ago. */
export const CLOSED = row({
  thesis_id: "t-c",
  thesis_name: "Card C",
  arm_night: "2026-10-14",
  arm_until: "2026-10-22",
  sessions_left: null,
  closed_ago: 3,
  sessions_into: 9,
  hold_days: -2,
  exit_by: "2026-10-25",
  reconstructed: true,
  catalysts: [],
  members: [member({ security_id: "s-ddd", ticker: "DDD", flag: "mixed-scale price history", since: null, above: null })],
  since: since({ names: 1, priced: 0, flagged: 1, above: 0, median: null }),
  spark: null,
});
/** Card D: no clock at all. */
export const NO_CLOCKS = row({
  thesis_id: "t-d",
  thesis_name: "Card D",
  arm_until: null,
  exit_by: null,
  sessions_left: null,
  closed_ago: null,
  hold_days: null,
  confidence: null,
  catalysts: [],
  members: [],
  since: since({ names: 0, priced: 0, above: 0, median: null, iwm: null, spy: null }),
  spark: null,
});
/** Card M: a Managing card. */
export const MANAGING = row({
  thesis_id: "t-m",
  thesis_name: "Card M",
  state: "managing",
  arm_night: "2026-10-15",
  arm_until: null,
  sessions_left: null,
  catalysts: [],
  members: [member({ security_id: "s-mmm", ticker: "MMM", arm_night: "2026-10-15" })],
  spark: null,
});

export const counts = (over: Partial<EntryWindowsCountsOut> = {}): EntryWindowsCountsOut => ({
  live_cards: 9,
  cards_counted: 8,
  cards_not_yet_created: 1,
  armed: 4,
  managing: 1,
  managing_without_arm_night: 0,
  warming: 2,
  incubating: 0,
  no_call: 1,
  reconstructed_arm_nights: 1,
  armed_names: 5,
  priced_names: 4,
  unpriced_names: 1,
  ...over,
});

export const payload = (over: Partial<EntryWindowsOut> = {}): EntryWindowsOut => ({
  asof: ASOF,
  clock: "public",
  axis_start: "2026-10-07",
  axis_end: "2026-11-14",
  session_source: "benchmarks",
  calendar_sessions: 253,
  calendar_last: ASOF,
  counts: counts(),
  flagged: [{ security_id: "s-ddd", ticker: "DDD", reason: "mixed-scale price history" }],
  rows: [row(), CLOSING, CLOSED, NO_CLOCKS, MANAGING],
  cache: "computed",
  compute_ms: 1200,
  ...over,
});
