import type {
  LookupCard,
  LookupDecision,
  LookupFigure,
  LookupFiling,
  LookupFilingDay,
  LookupTrigger,
  TickerLookupOut,
} from "../useTickerLookup";

// Hand-built payloads for the Ticker Lookup's tests: neutral names (Card A, Card B), one company (TLA).

export const SID = "sid-tla";
export const CARD_A = "card-a";
export const CARD_B = "card-b";
export const EDGAR = "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/";

const link = (label: string | null, over: Partial<LookupCard["links"][number]> = {}): LookupCard["links"][number] => ({
  link: label,
  thesis_fit: null,
  authored_by: "system_drafted",
  signed_off: false,
  weight: null,
  ...over,
});

export const cardRow = (over: Partial<LookupCard> = {}): LookupCard => ({
  thesis_id: CARD_A,
  thesis_name: "Card A",
  ticker: "TLA",
  links: [
    link("Core", { thesis_fit: "Supplies the key part.", weight: 3, signed_off: true }),
    link("Rest", { thesis_fit: "Also sells the service.", authored_by: "operator_edited", weight: 3 }),
  ],
  silent: null,
  context_role: null,
  signed_off: false,
  state: "armed",
  verdict: "core_entry",
  recorded_on: "2026-10-27",
  reconstructed: false,
  place: {
    kind: "armed",
    verdict: "core_entry",
    grade: "core",
    strength: 0.62,
    live_triggers: 1,
    turns: ["conviction"],
    live_risks: 0,
  },
  ...over,
});

export const silentRow = (): LookupCard =>
  cardRow({
    thesis_id: CARD_B,
    thesis_name: "Card B",
    links: [link("Context", { thesis_fit: "Buys the product." })],
    silent: "Context",
    context_role: "payer",
    state: "warming",
    verdict: "not_yet",
    place: { kind: "not_counted", verdict: null, grade: null, strength: null, live_triggers: 0, turns: [], live_risks: 0 },
  });

export const trigger = (over: Partial<LookupTrigger> = {}): LookupTrigger => ({
  thesis_id: CARD_A,
  thesis_name: "Card A",
  ticker: "TLA",
  recorded_on: "2026-10-26",
  reconstructed: false,
  event_date: "2026-10-23",
  recorded_after_days: 3,
  role: "entry_trigger",
  kind: "insider",
  grade: "core",
  label: "CEO bought 12,000 shares",
  turns: "conviction",
  sources: [{ source: "form4", ref: "0000876543-26-000010", url: EDGAR, detail: ["usd: 12000.0"] }],
  live: true,
  ...over,
});

export const riskTrigger = (): LookupTrigger =>
  trigger({
    recorded_on: "2026-10-01",
    event_date: "2026-09-30",
    recorded_after_days: 1,
    role: "risk_signal",
    kind: "dilution_risk",
    grade: null,
    label: "Convertible overhang",
    turns: null,
    sources: [{ source: "price", ref: "price:TLA:2026-09-30", url: null, detail: [] }],
    live: false,
  });

export const figure = (key: string, group: LookupFigure["group"], label: string, over: Partial<LookupFigure> = {}): LookupFigure => ({
  member: "m",
  key,
  group,
  label,
  value: 1,
  unit: "pct",
  note: null,
  tone: null,
  level: null,
  on: null,
  ...over,
});

export const FIGURES: LookupFigure[] = [
  figure("close", "price", "close", { value: 12.5, unit: "price" }),
  figure("pct_vs_fast", "price", "vs 50d", { value: 4.25, tone: "pos" }),
  figure("pct_vs_slow", "price", "vs 200d", { value: null, note: "n/a: 120/200 bars" }),
  figure("ret_1d", "returns", "1D", { value: 1.2, tone: "pos" }),
  figure("ret_7d", "returns", "7D", { value: -3.4, tone: "neg" }),
  figure("pct_off_52w_high", "range", "off 52w high", { value: -8.1, level: 13.6, on: "2026-09-02" }),
  figure("rvol", "volume", "RVOL 8", { value: 1.82, unit: "ratio" }),
  figure("adv_usd_20d", "volume", "$vol 20d", { value: 2_500_000, unit: "usd" }),
  figure("rs_spy", "strength", "RS vs SPY", { value: 0.1234, unit: "ratio" }),
  figure("buy_count", "insider", "buys 90d", { value: 2, unit: "count" }),
  figure("net_usd", "insider", "net $ 90d", { value: 55_000, unit: "usd", tone: "pos" }),
];

export const filing = (over: Partial<LookupFiling> = {}): LookupFiling => ({
  kind: "8k",
  form: "8-K",
  date: "2026-10-27",
  headline: "8-K · 5.02 officer/director departure or appointment",
  detail: ["Item 5.02 officer/director departure or appointment"],
  url: EDGAR,
  accession: "0000876543-26-000010",
  tags: [],
  routine: false,
  fold: null,
  usd: null,
  ratifier: null,
  ratified_on: null,
  ...over,
});

const routineForm4 = (n: number): LookupFiling =>
  filing({
    kind: "form4",
    form: "4",
    date: "2026-10-20",
    headline: `Jane Roe (CEO): S sale ${n},000 sh @ $5.00`,
    detail: [],
    url: null,
    accession: `0000876543-26-0001${n}`,
    routine: true,
    fold: "form4",
    usd: -5000 * n,
  });

export const DAYS: LookupFilingDay[] = [
  { date: "2026-10-27", rows: [filing()] },
  { date: "2026-10-20", rows: [routineForm4(1), routineForm4(2)] },
  { date: "2026-05-01", rows: [filing({ date: "2026-05-01", headline: "8-K · 2.02 results", accession: "0000876543-26-000003" })] },
];

export const decision = (over: Partial<LookupDecision> = {}): LookupDecision => ({
  id: "d1",
  thesis_id: CARD_A,
  thesis_name: "Card A",
  action: "take",
  decision_date: "2026-10-10",
  shares: 100,
  price: 10.5,
  reason: "starter",
  voided: false,
  voids: null,
  ...over,
});

export const payload = (over: Partial<TickerLookupOut> = {}): TickerLookupOut => ({
  asof: "2026-10-28",
  clock: "public",
  identity: {
    security_id: SID,
    ticker: "TLA",
    name: "Tape A Corp",
    cik: "0000876543",
    edgar_url: "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000876543",
    exchange: "NASDAQ",
    business_type: "biotech_pharma",
    supersector: "healthcare",
    royalty: false,
    origin: "US",
    instrument_kind: "equity",
    price_symbol: null,
    former_tickers: [],
    cap: {
      value: 125_000_000,
      tier: "micro",
      gap: null,
      note: null,
      close: 12.5,
      close_date: "2026-10-28",
      shares: 10_000_000,
      shares_date: "2026-09-01",
      ads_ratio: null,
      split_factor: null,
    },
    adv_usd_20d: 2_500_000,
    adv_note: null,
    flag: null,
  },
  cards: {
    live_cards: 5,
    not_yet_created: 1,
    roster_fallback: 0,
    counted: 4,
    rows: [cardRow(), silentRow()],
    archived: [{ thesis_id: "card-old", thesis_name: "Card Old" }],
    excluded: [{ thesis_id: "card-x", thesis_name: "Card X", reason: "a rival, not a fit" }],
  },
  triggers: {
    window_start: "2025-10-28",
    events: [trigger(), riskTrigger()],
    cards_read: 2,
    nights_read: 14,
    reconstructed_nights: 1,
    before_window_live: 0,
  },
  tape: {
    figures: FIGURES,
    leading: [],
    coiling: false,
    coiling_detail: null,
    headline_notes: [],
    gaps: [{ member: "vcp", label: "Base (VCP)", note: "too few price bars for a base" }],
    members_read: 10,
    members_without: 1,
    figures_blank: 1,
    sparkline: {
      dates: ["2026-10-26", "2026-10-27", "2026-10-28"],
      stock: [100, 102, 101],
      iwm: [100, 99, null],
      bars: 3,
      iwm_bars: 2,
      note: null,
    },
    window_end: "2026-10-28",
  },
  filings: {
    read_days: 365,
    days: DAYS,
    rows: 4,
    by_kind: [
      { kind: "8k", rows: 2 },
      { kind: "form4", rows: 2 },
    ],
    routine: 2,
    not_listed: [],
  },
  catalyst_facts: [
    filing({
      kind: "catalyst",
      form: "catalyst",
      date: "2026-10-15",
      headline: "catalyst: license decision expected",
      detail: [],
      accession: null,
      ratifier: "operator",
      ratified_on: "2026-10-16",
    }),
  ],
  card_catalysts: [
    {
      thesis_id: CARD_A,
      thesis_name: "Card A",
      label: "Earlier vote",
      kind: null,
      when_date: "2026-10-01",
      when_label: null,
      passed: true,
    },
    {
      thesis_id: CARD_A,
      thesis_name: "Card A",
      label: "Trial readout",
      kind: "clinical",
      when_date: "2026-12-01",
      when_label: null,
      passed: false,
    },
    {
      thesis_id: CARD_A,
      thesis_name: "Card A",
      label: "Fuzzy window",
      kind: null,
      when_date: null,
      when_label: "~Q1",
      passed: false,
    },
  ],
  decisions: [
    decision({ id: "d3", action: "void", decision_date: "2026-10-12", shares: null, price: null, reason: "typo", voids: "d1" }),
    decision({ id: "d1", voided: true }),
  ],
  decisions_voided: 1,
  cache: "computed",
  compute_ms: 900,
  ...over,
});
