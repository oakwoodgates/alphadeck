import type {
  DailyDigestOut,
  DigestBuyRowOut,
  DigestCardOut,
  DigestDueItemOut,
  DigestLineOut,
  DigestPositionOut,
  DigestTapeRowOut,
} from "../useDailyDigest";

// Neutral fixtures for the Daily Digest's tests: every card, company and ticker is a made-up name. The morning is a
// Thursday: the night of Sep 17 against Sep 16 (the as-of is the night itself).

export const ASOF = "2026-09-17";
export const PREV = "2026-09-16";

export const CARD_A = "00000000-0000-0000-0000-0000000000a1";
export const CARD_B = "00000000-0000-0000-0000-0000000000b2";
export const CARD_C = "00000000-0000-0000-0000-0000000000c3";
export const CARD_D = "00000000-0000-0000-0000-0000000000d4";
export const CARD_E = "00000000-0000-0000-0000-0000000000e5";
export const SEC_A = "00000000-0000-0000-0000-00000000aaa1";
export const SEC_B = "00000000-0000-0000-0000-00000000bbb2";
export const SEC_C = "00000000-0000-0000-0000-00000000ccc3";
export const SEC_H = "00000000-0000-0000-0000-00000000444a";

export function line(over: Partial<DigestLineOut> = {}): DigestLineOut {
  return {
    section: "names",
    change: "armed",
    security_id: SEC_A,
    ticker: "AAA",
    from_state: null,
    to_state: null,
    label: null,
    signal_kind: null,
    on: null,
    previous_on: null,
    days: null,
    tone: null,
    source: null,
    ...over,
  };
}

export function card(over: Partial<DigestCardOut> = {}): DigestCardOut {
  return {
    thesis_id: CARD_C,
    thesis_name: "Card C",
    tier: "armed",
    state: "armed",
    previous_state: "warming",
    reconstructed: false,
    ingest_fresh: true,
    headline: line({ section: "column_moves", change: "state", security_id: null, ticker: null, from_state: "warming", to_state: "armed", tone: "accent" }),
    lines: [],
    ...over,
  };
}

/** Five cards in the server's order: C entered Armed, D left Armed, E is Managing, B is Warming, A is Incubating. */
export const CARDS: DigestCardOut[] = [
  card({
    lines: [
      line({ change: "armed", tone: "accent" }),
      line({ section: "clocks", change: "entry_window", security_id: null, ticker: null, on: "2026-09-19", days: 2, tone: "accent" }),
    ],
  }),
  card({
    thesis_id: CARD_D,
    thesis_name: "Card D",
    state: "warming",
    previous_state: "armed",
    headline: line({ section: "column_moves", change: "state", security_id: null, ticker: null, from_state: "armed", to_state: "warming", tone: "warm" }),
    lines: [line({ change: "no_longer_armed", security_id: SEC_B, ticker: "BBB" })],
  }),
  card({
    thesis_id: CARD_E,
    thesis_name: "Card E",
    state: "managing",
    previous_state: "managing",
    headline: null,
    lines: [
      line({
        section: "triggers",
        change: "risk_new",
        security_id: SEC_C,
        ticker: "CCC",
        label: "going-concern note",
        signal_kind: "dilution_risk",
        on: "2026-09-16",
        source: { source: "form4", ref: "0000876543-26-000001", url: "https://www.sec.gov/Archives/edgar/data/876543/000087654326000001/" },
      }),
      line({ section: "clocks", change: "exit_by", security_id: null, ticker: null, on: "2026-09-17", tone: "warm" }),
    ],
  }),
  card({
    thesis_id: CARD_B,
    thesis_name: "Card B",
    tier: "warming",
    state: "warming",
    previous_state: "warming",
    headline: null,
    lines: [line({ section: "keys", change: "key_on", security_id: null, ticker: null, label: "Confirmation" })],
  }),
  card({
    thesis_id: CARD_A,
    thesis_name: "Card A",
    tier: "incubating",
    state: "incubating",
    previous_state: "warming",
    reconstructed: true,
    ingest_fresh: false,
    headline: line({ section: "column_moves", change: "state", security_id: null, ticker: null, from_state: "warming", to_state: "incubating" }),
    lines: [],
  }),
];

export function position(over: Partial<DigestPositionOut> = {}): DigestPositionOut {
  return {
    thesis_id: CARD_E,
    thesis_name: "Card E",
    has_position: true,
    security_id: SEC_H,
    ticker: "HHH",
    opened_on: "2026-09-11",
    logged_price: 48,
    shares: 100,
    fill_usd: 4800,
    since_fill: {
      status: "priced",
      basis: "logged_price",
      basis_price: 48,
      basis_date: "2026-09-11",
      last_date: "2026-09-17",
      last_close: 66,
      ret_pct: 37.5,
      bench_pct: 3.96,
      reason: null,
      note: null,
    },
    last_session: { status: "priced", reason: null, session: ASOF, previous_session: PREV, ret_pct: 10, bench_pct: 0.96 },
    hold: { exit_by: "2026-09-22", sessions_left: 3, passed_sessions: null },
    risk_signals: 2,
    risk_new: 1,
    kill_criteria: ["the license is lost", "a second going-concern note"],
    reconstructed: false,
    ingest_fresh: true,
    ...over,
  };
}

export function due(over: Partial<DigestDueItemOut> = {}): DigestDueItemOut {
  return {
    item: "catalyst",
    on: ASOF,
    days: 0,
    today: true,
    label: "Readout",
    kind: "regulatory",
    when_label: null,
    thesis_id: CARD_C,
    thesis_name: "Card C",
    state: "armed",
    ...over,
  };
}

export function buy(over: Partial<DigestBuyRowOut> = {}): DigestBuyRowOut {
  return {
    key: "k1",
    security_id: SEC_A,
    ticker: "AAA",
    accession: "0000876543-26-000001",
    form: "4",
    url: "https://www.sec.gov/Archives/edgar/data/876543/000087654326000001/",
    filed: ASOF,
    trade_date: PREV,
    filed_days_after: 1,
    cards: [{ thesis_id: CARD_C, thesis_name: "Card C", ticker: "AAA" }],
    filer: "Jane Roe",
    role: "CEO",
    role_classes: ["officer"],
    co_filers: 0,
    shares: 1000,
    price: 4,
    usd: 4000,
    tags: [],
    headline: "Jane Roe (CEO): P purchase 1,000 sh @ $4.00",
    cluster: { buyers: 2, others: [{ name: "Sam Poe", filed: PREV }] },
    triggers: [{ thesis_id: CARD_C, thesis_name: "Card C", recorded_on: ASOF, live: true }],
    ...over,
  };
}

export function tapeRow(over: Partial<DigestTapeRowOut> = {}): DigestTapeRowOut {
  return {
    key: "c:a",
    thesis_id: CARD_C,
    thesis_name: "Card C",
    security_id: SEC_A,
    ticker: "AAA",
    arm_night: "2026-09-15",
    arm_reconstructed: false,
    sessions_since: 2,
    arm_until: "2026-09-22",
    sessions_left: 3,
    closed_ago: null,
    last_session: { status: "priced", reason: null, session: ASOF, previous_session: PREV, ret_pct: 4, bench_pct: 0.96 },
    since_arm: { entry_date: "2026-09-15", entry_close: 98, last_date: ASOF, last_close: 104, ret_pct: 6.12, iwm_pct: 1.94, gap: null },
    cut: "rise",
    ...over,
  };
}

/** Five armed rows: two cut rises, one cut fall, one unpriced and one middle row behind the toggle. */
export const TAPE_ROWS: DigestTapeRowOut[] = [
  tapeRow({ key: "r1", ticker: "RS1", last_session: { status: "priced", reason: null, session: ASOF, previous_session: PREV, ret_pct: 9, bench_pct: 0.96 }, cut: "rise" }),
  tapeRow({ key: "r2", ticker: "RS2", last_session: { status: "priced", reason: null, session: ASOF, previous_session: PREV, ret_pct: 4, bench_pct: 0.96 }, cut: "rise" }),
  tapeRow({ key: "m1", ticker: "MID", last_session: { status: "priced", reason: null, session: ASOF, previous_session: PREV, ret_pct: 0.5, bench_pct: 0.96 }, cut: null }),
  tapeRow({ key: "f1", ticker: "FL1", last_session: { status: "priced", reason: null, session: ASOF, previous_session: PREV, ret_pct: -3, bench_pct: 0.96 }, cut: "fall" }),
  tapeRow({
    key: "u1",
    ticker: "UNP",
    last_session: { status: "unpriced", reason: "no close on 2026-09-17", session: ASOF, previous_session: PREV, ret_pct: null, bench_pct: null },
    since_arm: { entry_date: null, entry_close: null, last_date: null, last_close: null, ret_pct: null, iwm_pct: null, gap: "no_bar" },
    cut: null,
  }),
];

export function payload(over: Partial<DailyDigestOut> = {}): DailyDigestOut {
  return {
    asof: ASOF,
    clock: "public",
    nights: { asof: ASOF, latest: ASOF, previous: PREV, sessions_between: 1, days_after_night: 0 },
    last_night: { no_baseline: false, cards_with_change: CARDS.length, lines: 9, cards: CARDS },
    positions: [position()],
    due: {
      start: ASOF,
      end: "2026-09-20",
      items: [
        due(),
        due({ item: "entry_window", on: "2026-09-18", days: 1, today: false, label: "entry window ends", kind: null, thesis_id: CARD_D, thesis_name: "Card D", state: "armed" }),
        due({ item: "catalyst", on: "2026-09-20", days: 3, today: false, label: "Vote", kind: null, thesis_id: CARD_B, thesis_name: "Card B", state: "warming" }),
      ],
      catalysts: 2,
      deadlines: 1,
      passed_since: 1,
      passed_total: 3,
      cards_without_calls: 0,
    },
    insider: {
      no_baseline: false,
      span_start: PREV,
      span_end: ASOF,
      rows: [buy(), buy({ key: "k2", ticker: "BBB", accession: "0000876543-26-000002", filer: "Kim Lee", role: null, cluster: null, triggers: [], cards: [{ thesis_id: CARD_D, thesis_name: "Card D", ticker: "BBB" }], url: null, trade_date: ASOF, filed_days_after: null, shares: null, price: null, usd: null })],
      counts: {
        names_read: 12,
        filings_read: 4,
        purchases: 2,
        routine_folded: 2,
        amendments_no_purchase: 0,
        names_with_purchases: 2,
        filer_unresolved: 0,
        cluster_rows: 1,
        trigger_rows: 1,
        nights_read: 5,
        nights_unreadable: 0,
        silent_rows_left_out: 3,
        unresolved_rows: 0,
      },
    },
    tape: {
      session: ASOF,
      previous_session: PREV,
      iwm_pct: 0.96,
      calendar_source: "benchmarks",
      calendar_sessions: 200,
      rows: TAPE_ROWS,
      counts: { rows: 5, names: 5, names_priced: 4, names_unpriced: 1, names_flagged: 0, since_arm_priced: 4, since_arm_blank: 1, rises_shown: 2, falls_shown: 1 },
    },
    forming: { warming: 2, incubating: 40, key_turned: [{ thesis_id: CARD_B, thesis_name: "Card B", labels: ["Confirmation"] }] },
    counts: {
      cards_counted: 48,
      live_cards: 50,
      not_yet_created: 2,
      roster_fallback: 0,
      cards_recorded_on_night: 45,
      cards_carried: 2,
      cards_no_call: 1,
      cards_armed: 3,
      cards_warming: 2,
      cards_managing: 1,
      cards_incubating: 40,
      cards_ingest_not_fresh: 1,
      cards_reconstructed: 1,
    },
    flagged: [{ security_id: SEC_H, ticker: "HHH", reason: "mixed-scale price history" }],
    cache: "computed",
    compute_ms: 1234,
    ...over,
  };
}

/** The same page with every section empty: the quiet sentences. */
export function emptyPayload(over: Partial<DailyDigestOut> = {}): DailyDigestOut {
  const p = payload();
  return payload({
    last_night: { no_baseline: false, cards_with_change: 0, lines: 0, cards: [] },
    positions: [],
    due: { ...p.due, items: [], catalysts: 0, deadlines: 0, passed_since: 0, passed_total: 0 },
    insider: { ...p.insider, rows: [], counts: { ...p.insider.counts, purchases: 0, filings_read: 0 } },
    tape: { ...p.tape, rows: [], counts: { ...p.tape.counts, rows: 0, names: 0, names_priced: 0, names_unpriced: 0, since_arm_priced: 0, since_arm_blank: 0, rises_shown: 0, falls_shown: 0 } },
    forming: { warming: 0, incubating: 0, key_turned: [] },
    flagged: [],
    ...over,
  });
}
