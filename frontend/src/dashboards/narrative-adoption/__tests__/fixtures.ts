import type {
  AdoptionCardsOut,
  AdoptionCountsOut,
  AdoptionMonthOut,
  AdoptionOut,
  AdoptionOverlayOut,
  AdoptionRunOut,
  AdoptionTermOut,
} from "../useNarrativeAdoption";

// Hand-built Narrative Adoption payloads for the layout, view and component tests (no server).

/** The `n` calendar months ending with `last` (a first day), oldest first, each as its first day. */
export function monthsEnding(last: string, n = 36): string[] {
  let y = Number(last.slice(0, 4));
  let m = Number(last.slice(5, 7));
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    out.push(`${y}-${String(m).padStart(2, "0")}-01`);
    m -= 1;
    if (m === 0) {
      m = 12;
      y -= 1;
    }
  }
  return out.reverse();
}

/** 2023-10-01 .. 2026-09-01: the axis at an as-of of 2026-09-30. */
export const AXIS = monthsEnding("2026-09-01");
export const IDX = (month: string): number => AXIS.indexOf(month);

export const absent = (month: string, over: Partial<AdoptionMonthOut> = {}): AdoptionMonthOut => ({
  month,
  closed: true,
  counted: false,
  terms_counted: 0,
  documents: null,
  reports: null,
  events: null,
  issuance: null,
  other: null,
  filers: null,
  filers_sampled: false,
  new_filers: null,
  read_early: false,
  top_terms: [],
  ...over,
});

export const counted = (
  month: string,
  groups: { reports?: number; events?: number; issuance?: number; other?: number },
  over: Partial<AdoptionMonthOut> = {},
): AdoptionMonthOut => {
  const reports = groups.reports ?? 0;
  const events = groups.events ?? 0;
  const issuance = groups.issuance ?? 0;
  const other = groups.other ?? 0;
  return {
    month,
    closed: true,
    counted: true,
    terms_counted: 2,
    documents: reports + events + issuance + other,
    reports,
    events,
    issuance,
    other,
    filers: 0,
    filers_sampled: false,
    new_filers: null,
    read_early: false,
    top_terms: [],
    ...over,
  };
};

/** June: three groups (no issuance), filers exact, no earlier month. July: a stored zero. August: absent (a failed
 *  month). September: all four groups, filers "at least", partial terms, read before the month closed. Every other
 *  month is absent. */
export const MONTHS: AdoptionMonthOut[] = AXIS.map((m) => {
  switch (m) {
    case "2026-06-01":
      return counted(m, { reports: 10, events: 2, other: 1 }, { filers: 5, top_terms: [{ term: "alpha", documents: 9 }, { term: "beta", documents: 4 }] });
    case "2026-07-01":
      return counted(m, {}, { filers: 0, new_filers: 0 });
    case "2026-09-01":
      return counted(
        m,
        { reports: 30, events: 8, issuance: 12, other: 4 },
        {
          filers: 20,
          filers_sampled: true,
          new_filers: 6,
          read_early: true,
          top_terms: [
            { term: "alpha", documents: 40 },
            { term: "beta", documents: 14 },
          ],
        },
      );
    default:
      return absent(m);
  }
});

export const term = (name: string, over: Partial<AdoptionTermOut> = {}): AdoptionTermOut => ({
  term: name,
  counted: true,
  months_stored: 3,
  last_month_documents: 0,
  last_3_documents: 0,
  months_last_3: 3,
  last_12_documents: 0,
  months_last_12: 3,
  filers: 0,
  filers_sampled: false,
  first_month: null,
  first_at_start: false,
  ...over,
});

export const TERMS: AdoptionTermOut[] = [
  term("alpha", {
    last_month_documents: 40,
    last_3_documents: 49,
    months_last_3: 2,
    last_12_documents: 55,
    months_last_12: 3,
    filers: 15,
    filers_sampled: true,
    first_month: "2026-06-01",
    first_at_start: true,
  }),
  term("beta", {
    last_month_documents: 14,
    last_3_documents: 18,
    months_last_3: 3,
    last_12_documents: 18,
    months_last_12: 3,
    filers: 6,
    first_month: "2026-07-01",
    first_at_start: false,
  }),
  term("gamma", {
    counted: false,
    months_stored: 0,
    last_month_documents: null,
    last_3_documents: null,
    months_last_3: 0,
    last_12_documents: null,
    months_last_12: 0,
    filers: null,
  }),
];

/** Weekly points from 2023-09-29 (the session before the axis) to the as-of: the card from 100 up 1.5 a point, SPY up 1. */
function weekly(): string[] {
  const out: string[] = [];
  let t = Date.parse("2023-09-29T00:00:00Z");
  const end = Date.parse("2026-09-30T00:00:00Z");
  while (t <= end) {
    out.push(new Date(t).toISOString().slice(0, 10));
    t += 7 * 86_400_000;
  }
  return out;
}
const DATES = weekly();

export const overlay = (over: Partial<AdoptionOverlayOut> = {}): AdoptionOverlayOut => ({
  dates: DATES,
  card: DATES.map((_, i) => 100 + 1.5 * i),
  spy: DATES.map((_, i) => 100 + i),
  card_reason: null,
  spy_reason: null,
  session_source: "benchmarks",
  names: 3,
  priced: 2,
  unresolved: 0,
  silent_left_out: 1,
  flagged: [{ ticker: "BBB", reason: "mixed-scale price history" }],
  ...over,
});

export const counts = (over: Partial<AdoptionCountsOut> = {}): AdoptionCountsOut => ({
  signal_terms: 3,
  terms_counted: 2,
  terms_not_counted: 1,
  broad_terms_left_out: 4,
  retired_terms: 1,
  months_total: 36,
  months_counted: 3,
  months_complete: 0,
  months_sampled: 1,
  months_read_early: 1,
  months_not_closed: 0,
  last_counted_month: "2026-09-01",
  ...over,
});

export const RUN: AdoptionRunOut = {
  schedule: "weekly",
  run_kind: "cron",
  status: "finished",
  finished_on: "2026-10-24",
  months_from: "2026-09",
  months_to: "2026-10",
  requests: 1492,
  failed_requests: 3,
  deferred_months: 46,
  card_failed_requests: 1,
  card_deferred_months: 7,
};

export const CARDS: AdoptionCardsOut = {
  asof: "2026-09-30",
  live_cards: 4,
  not_yet_created: 1,
  cards: [
    { thesis_id: "t-a", name: "Card A", links: 4 },
    { thesis_id: "t-b", name: "Card B", links: 3 },
    { thesis_id: "t-c", name: "Card C", links: 2 },
  ],
  cache: "hit",
  compute_ms: 0,
};

export const payload = (over: Partial<AdoptionOut> = {}): AdoptionOut => ({
  asof: "2026-09-30",
  thesis_id: "t-a",
  thesis_name: "Card A",
  clock: "public",
  roster_fallback: false,
  months_window: 36,
  months: MONTHS,
  terms: TERMS,
  overlay: overlay(),
  counts: counts(),
  last_run: RUN,
  cache: "computed",
  compute_ms: 4200,
  ...over,
});
