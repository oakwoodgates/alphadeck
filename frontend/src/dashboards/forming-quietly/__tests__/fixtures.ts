import type {
  FormingAdoptionOut,
  FormingBreadthOut,
  FormingQuietOut,
  FormingQuietlyOut,
  FormingRowOut,
} from "../useFormingQuietly";

// Hand-built Forming Quietly payloads shared by the view-helper and component tests (the wire shape the backend test
// pins). Every card, company and ticker is a neutral fixture name.

export const ASOF = "2026-10-28";
export const MIXED = "mixed-scale price history";

export const CARD_A = "00000000-0000-4000-8000-00000000000a";
export const CARD_B = "00000000-0000-4000-8000-00000000000b";
export const CARD_C = "00000000-0000-4000-8000-00000000000c";
export const CARD_D = "00000000-0000-4000-8000-00000000000d";
export const SEC = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

export const quiet = (over: Partial<FormingQuietOut> = {}): FormingQuietOut => ({
  names: 12,
  read: 11,
  unresolved: 0,
  flagged: [],
  insider: [],
  no_form4: 2,
  coiling: [],
  coiling_no_reading: 1,
  leading: [],
  leading_no_reading: 0,
  breadth: { above: 5, counted: 10, thin: 1, pct: 50, delta_pts: 10, thrust: false } as FormingBreadthOut,
  lit: false,
  ...over,
});

export const adoption = (over: Partial<FormingAdoptionOut> = {}): FormingAdoptionOut => ({
  counted: true,
  percent: 38.4,
  recent: 412,
  prior: 298,
  recent_from: "2026-07-01",
  recent_to: "2026-09-01",
  prior_from: "2026-04-01",
  prior_to: "2026-06-01",
  terms_signal: 5,
  terms_counted: 5,
  terms_partial: 0,
  terms_unstored: 0,
  months_stored: 6,
  latest_partial: false,
  ...over,
});

export const notCounted = (over: Partial<FormingAdoptionOut> = {}): FormingAdoptionOut =>
  adoption({
    counted: false,
    percent: null,
    recent: null,
    prior: null,
    recent_from: null,
    recent_to: null,
    prior_from: null,
    prior_to: null,
    terms_counted: 0,
    months_stored: 3,
    ...over,
  });

export const row = (over: Partial<FormingRowOut> = {}): FormingRowOut => ({
  key: CARD_A,
  thesis_id: CARD_A,
  thesis_name: "Card A",
  state: "warming",
  recorded_on: "2026-10-27",
  run_start: "2026-10-25",
  run_nights: 3,
  days_in_state: 3,
  run_reconstructed: false,
  key_conviction: {
    turned: true,
    label: "Conviction",
    detail: "A conviction entry trigger fired — the 'why now is real'.",
  },
  key_confirmation: { turned: false, label: "Confirmation", detail: "No confirmation yet." },
  names: [
    {
      security_id: SEC(1),
      ticker: "AAA",
      triggers: [
        {
          label: "insider cluster",
          kind: "insider",
          grade: "core",
          event_date: "2026-10-22",
          sources: [
            { source: "form4", ref: "0001234567-26-000001", url: "https://www.sec.gov/Archives/edgar/data/1/000123456726000001/" },
            { source: "price", ref: "price:AAA:2026-10-22", url: null },
          ],
        },
      ],
    },
  ],
  missing: ["Volume-confirmed breakout (the confirmation key)", "Risk must clear before arming: dilution overhang"],
  watch: [{ security_id: SEC(2), ticker: "BBB" }],
  risks: [{ label: "convertible overhang", kind: "dilution_risk", security_id: SEC(1), ticker: "AAA" }],
  quiet: quiet({
    lit: true,
    insider: [
      { security_id: SEC(1), ticker: "AAA", buys_30d: 3, buyers_30d: 2, cluster: true },
      { security_id: SEC(3), ticker: "CCC", buys_30d: 1, buyers_30d: 1, cluster: false },
    ],
    coiling: [{ security_id: SEC(4), ticker: "DDD", contraction: 0.33 }],
    leading: [{ security_id: SEC(5), ticker: "EEE", vs: ["SPY", "IWM"] }],
  }),
  adoption: adoption(),
  ...over,
});

/** Card A Warming (run 10-25), Card B Warming (run 10-27), Card C Incubating lit (insider), Card D Incubating
 *  with no quiet signal and a smaller adoption slope. In the server's default order: B, A, C, D. */
export const rows = (): FormingRowOut[] => [
  row({
    key: CARD_B,
    thesis_id: CARD_B,
    thesis_name: "Card B",
    run_start: "2026-10-27",
    days_in_state: 1,
    run_nights: 2,
    names: [],
    watch: [],
    risks: [],
    missing: ["Conviction trigger (e.g. insider cluster / structural catalyst)"],
    key_conviction: { turned: false, label: "Conviction", detail: "No conviction trigger yet." },
    key_confirmation: { turned: true, label: "Confirmation", detail: "A volume-confirmed breakout." },
    quiet: quiet({ lit: false }),
    adoption: notCounted(),
  }),
  row(),
  row({
    key: CARD_C,
    thesis_id: CARD_C,
    thesis_name: "Card C",
    state: "incubating",
    run_start: "2026-10-01",
    days_in_state: 27,
    run_nights: 20,
    names: [],
    watch: [],
    risks: [],
    missing: ["Conviction trigger (e.g. insider cluster / structural catalyst)", "Volume-confirmed breakout (the confirmation key)"],
    key_conviction: { turned: false, label: "Conviction", detail: "No conviction trigger yet." },
    quiet: quiet({
      lit: true,
      insider: [{ security_id: SEC(6), ticker: "FFF", buys_30d: 2, buyers_30d: 2, cluster: true }],
      coiling: [],
      leading: [],
    }),
    adoption: adoption({ percent: -12.4, recent: 87, prior: 99 }),
  }),
  row({
    key: CARD_D,
    thesis_id: CARD_D,
    thesis_name: "Card D",
    state: "incubating",
    run_start: "2026-09-15",
    days_in_state: 43,
    run_nights: 30,
    run_reconstructed: true,
    names: [],
    watch: [],
    risks: [],
    missing: ["Conviction trigger (e.g. insider cluster / structural catalyst)"],
    key_conviction: { turned: false, label: "Conviction", detail: "No conviction trigger yet." },
    quiet: quiet({ lit: false, breadth: null, names: 3, read: 3 }),
    adoption: adoption({ percent: null, recent: 12, prior: 0 }),
  }),
];

export const payload = (over: Partial<FormingQuietlyOut> = {}): FormingQuietlyOut => ({
  asof: ASOF,
  clock: "public",
  live_cards: 9,
  cards_counted: 8,
  cards_not_yet_created: 1,
  roster_fallback_cards: 0,
  cards_armed: 2,
  cards_warming: 2,
  cards_managing: 1,
  cards_incubating: 2,
  cards_incubating_lit: 1,
  cards_no_call: 1,
  cards_with_term_months: 3,
  cards_without_term_months: 1,
  names_read: 40,
  names_flagged: 1,
  members: ["insider_flow_90d", "vcp", "relative_strength"],
  rows: rows(),
  flagged: [{ security_id: SEC(9), ticker: "FLG", reason: MIXED }],
  cache: "computed",
  compute_ms: 1234,
  ...over,
});
