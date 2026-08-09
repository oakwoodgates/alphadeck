// Workbench display helpers — turn the wire figures into the chips/labels the operator reads.
// All values are DATA-DERIVED (the scoring engine, Slice 3); nothing here invents a number.

import type { ProvenanceOut, ScoredFigureOut } from "../api/hooks";

// the business-type labels live in the shared util/format (the Cockpit board uses them too); re-exported
// here so the Workbench / DDRail call sites import them alongside the other display helpers.
export { businessTypeLabel, supersectorLabel } from "../util/format";

/** The business-type leaves the re-tag select offers (the finalize rail's set control) — mirrors
 *  domain BusinessType; labels via businessTypeLabel. Display order: the operator-ratified taxonomy. */
export const BUSINESS_TYPES = [
  "miner",
  "bank",
  "utilities",
  "oil_gas",
  "semiconductors",
  "biotech_pharma",
  "medical_devices",
  "healthcare_services",
  "software_it",
  "finance_brokers",
  "real_estate",
  "industrials_machinery",
  "chemicals_materials",
  "comms_media",
  "transportation",
  "consumer_retail",
  "business_services",
  "spac",
  "other",
] as const;

/** A collision-prone SIGNAL acronym term (single all-caps token, letters+digits, ≥2 chars — HBM,
 *  DRAM). Used by the low-quality junk-tell set (`junkTells.ts`). Deliberately the simple v1 rule
 *  (NAND-style real-word acronyms count too) — judged on live data, tweaked after. */
export const isAcronymTerm = (term: string): boolean => /^[A-Z][A-Z0-9]{1,9}$/.test(term.trim());

/** The country bucket for the Country filter, derived from the DISPLAYED origin string (the output of
 *  backend `securities/origin.resolve_origin`): "US" → us; any other place string (Shanghai, Cayman
 *  Islands, London…) → foreign; null/empty (no chip) → unknown. As accurate as the origin chip — it
 *  classifies exactly what the chip shows. View-only display identity; never a call input (#3/#4). */
export type CountryClass = "us" | "foreign" | "unknown";
export const countryClass = (origin: string | null | undefined): CountryClass => {
  const o = (origin ?? "").trim();
  if (!o) return "unknown";
  return o.toUpperCase() === "US" ? "us" : "foreign";
};

/** The filer REGIME name behind a foreign form (the wire `foreign_filer_form`, derived server-side): 40-F →
 *  "Canadian MJDS", 20-F → "FPI" (foreign private issuer), anything else / null → null. Display-only identity
 *  — these regimes are §16-EXEMPT, which is WHY the name files no Form 4 and the insider signal is
 *  structurally unavailable. Never a call input (#3/#4). */
export const filerRegime = (form: string | null | undefined): string | null => {
  const f = (form ?? "").trim().toUpperCase();
  if (f === "40-F") return "Canadian MJDS";
  if (f === "20-F") return "FPI";
  return null;
};

/** Compose the origin string with the foreign-filer tell for the identity cell: a §16-exempt foreign filer
 *  (`form` set) reads "{origin} · {form} · no Form 4" — or "{form} · no Form 4" when the origin is unknown; a
 *  domestic / unknown filer (`form` null) reads its origin UNCHANGED (possibly null → the cell abstains). The
 *  one-line who-and-why; display-only, never a call input. Passing a null origin yields the bare
 *  "{form} · no Form 4" (the ChainEditor's second chip, where origin is already its own chip). */
export const originWithFiler = (
  origin: string | null | undefined,
  form: string | null | undefined,
): string | null => {
  const o = (origin ?? "").trim() || null;
  const f = (form ?? "").trim() || null;
  if (!f) return o; // domestic / unknown filer — origin unchanged (honest abstain when also unknown)
  const filer = `${f} · no Form 4`;
  return o ? `${o} · ${filer}` : filer;
};

/** The insider-signal N/A label for a foreign filer: "N/A — foreign filer (40-F), no Form 4 (§16-exempt)".
 *  The honest "structurally unavailable, not quiet" label shown where an insider-flow reading would otherwise
 *  sit (the NamePanel Indicators + the CallCard sub-note). Display-only. */
export const insiderNaLabel = (form: string | null | undefined): string => {
  const f = (form ?? "").trim() || "foreign form";
  return `N/A — foreign filer (${f}), no Form 4 (§16-exempt)`;
};

/** The exchange bucket for the Exchange filter. The security master carries only five values ever
 *  (measured: Nasdaq / NYSE / OTC / CBOE / empty): NYSE|Nasdaq → main; OTC → otc; empty → unknown;
 *  anything else (CBOE, or a future value) → other. Case-insensitive. View-only; never a call input. */
export type ExchangeClass = "main" | "otc" | "other" | "unknown";
export const exchangeClass = (exchange: string | null | undefined): ExchangeClass => {
  const x = (exchange ?? "").trim();
  if (!x) return "unknown";
  const u = x.toUpperCase();
  if (u === "NYSE" || u === "NASDAQ") return "main";
  if (u === "OTC") return "otc";
  return "other";
};

/** The blank-check (SPAC) bucket for the Type filter + the shell chip tint, derived from the stored
 *  sector: EDGAR's `sicDescription` lands verbatim in `sector`, and SIC 6770's official description is
 *  literally "Blank Checks" — so an enriched blank-check row self-identifies with zero new ingest. A
 *  shell is worthless to the operator until it announces a deal, so the editor quiets it (collapsed
 *  drawer, chip tint), never drops it (#9). Case-insensitive exact match; any other sector → other;
 *  no sector loaded (un-enriched / off-universe) → unknown — never a guessed shell. View-only display
 *  identity; never a call input (#3/#4). */
export type SpacClass = "spac" | "other" | "unknown";
export const spacClass = (sector: string | null | undefined): SpacClass => {
  const s = (sector ?? "").trim();
  if (!s) return "unknown";
  return s.toUpperCase() === "BLANK CHECKS" ? "spac" : "other";
};

/** Gate-3 readiness (ONE rule, three surfaces): does the scored member carry ANY confirmed SURFACE fact
 *  (purity / runway / market cap)? Catalysts + dilution come from the feeds/converts, not the extract, so
 *  they don't count. Shared by the editor's fundamentals badge, the scored row's "get data" control, and
 *  the funnel line — so "has data" can never mean three different things. A ratified shares fact COUNTS
 *  even before a price exists (market_cap.value needs both; a confirmed fact is confirmed data — the
 *  funnel must move when the operator ratifies, or the confirm reads as a no-op). Non-operator
 *  provenance (price bars, the awaiting-note) doesn't count. */
export const memberHasFundamentals = (m: {
  purity?: { pips?: number | null } | null;
  runway?: { pips?: number | null } | null;
  market_cap?: { value?: number | null; provenance?: { source: string }[] | null } | null;
}): boolean =>
  m.purity?.pips != null ||
  m.runway?.pips != null ||
  m.market_cap?.value != null ||
  (m.market_cap?.provenance ?? []).some((p) => p.source !== "price" && p.source !== "computed");

/** A human message from a thrown API error (FastAPI `{detail}`); a safe fallback otherwise. */
export function errText(e: unknown): string {
  const d = (e as { detail?: unknown } | null)?.detail;
  return typeof d === "string" ? d : "the request was rejected";
}

/** The ratified values ON FILE for one fact type, recovered from the scored read (the meters'
 *  provenance `detail` — the DB-backed surface). The extract endpoint is deliberately DB-free, so on
 *  re-entry it re-offers the ORIGINAL candidate — the panel visibly "reverted" a saved purity to the
 *  stale LLM rec even though the meter was correct. Presence of the object = a ratified fact exists;
 *  each field is best-effort (a fact ratified before the detail threading carries none — the tag still
 *  renders, the inputs fall back to the candidate). */
export interface OnFileFact {
  mix_pct?: number;
  segment_label?: string;
  shares?: number;
  cash_usd?: number;
  quarterly_burn_usd?: number;
  note?: string;
  // WHO put it on file. Read for ONE branch: `"auto"` -> the machine applied an AUTO parse and no human
  // vouched for it, so the panel says "auto-applied — confirm or override". Anything else (incl. the legacy
  // `"operator"`) stays a neutral "on file": ~108 legacy rows are the OLD ceremonial AUTO confirm, so
  // claiming "operator confirmed" off this field would assert a check that never happened.
  ratified_by?: string;
}
export type OnFileMap = Partial<Record<string, OnFileFact>>;

const dnum = (d: Record<string, unknown> | undefined, k: string): number | undefined =>
  typeof d?.[k] === "number" ? (d[k] as number) : undefined;
const dstr = (d: Record<string, unknown> | undefined, k: string): string | undefined =>
  typeof d?.[k] === "string" ? (d[k] as string) : undefined;

/** Build the per-fact-type on-file map from a scored member. Purity/runway ride their single fact
 *  provenance; shares is the market-cap entry that is neither the price leg nor the awaiting-note
 *  (the same predicate as memberHasFundamentals — non-operator provenance never counts as on file). */
export function onFileValues(m: {
  purity: ScoredFigureOut;
  runway: ScoredFigureOut;
  market_cap: ScoredFigureOut;
}): OnFileMap {
  const map: OnFileMap = {};
  const pu = m.purity.provenance[0];
  if (pu) {
    map.revenue_mix = {
      mix_pct: m.purity.value ?? dnum(pu.detail, "mix_pct"),
      segment_label: dstr(pu.detail, "segment_label"),
      note: dstr(pu.detail, "note"),
    };
  }
  const sh = m.market_cap.provenance.find((p) => p.source !== "price" && p.source !== "computed");
  if (sh) {
    map.shares_outstanding = {
      shares: dnum(sh.detail, "shares"),
      note: dstr(sh.detail, "note"),
      ratified_by: dstr(sh.detail, "ratified_by"),
    };
  }
  const cb = m.runway.provenance[0];
  if (cb) {
    map.cash_burn = {
      cash_usd: dnum(cb.detail, "cash_usd"),
      quarterly_burn_usd: dnum(cb.detail, "quarterly_burn_usd"),
      note: dstr(cb.detail, "note"),
    };
  }
  return map;
}

/** The as-of (cover) date of the ratified share count behind the market cap — threaded on the market-cap
 *  shares provenance (`shares_asof`). Undefined when no shares fact is on file. */
export function sharesAsof(m: { market_cap: ScoredFigureOut }): string | undefined {
  const sh = m.market_cap.provenance.find((p) => p.source !== "price" && p.source !== "computed");
  return sh ? dstr(sh.detail, "shares_asof") : undefined;
}

/** A STALE-SHARES warning: the rounded age (in months) of the share count's cover date IFF it is more than
 *  ~6 months before the as-of, else null. Why it exists (the ENDV finding): the AUTO-shares auto-apply
 *  faithfully reproduces whatever the latest filing's cover says — even a 2.5-year-old one for a delinquent
 *  filer — with no age signal, yielding a plausible-but-WRONG market cap on a name the operator doesn't know
 *  (the eye catches an absurd cap, not a merely old one). Honest loudness (#7): returns null for a current
 *  count (the common case) — the flag lights ONLY for the rare stale one. Threshold ~6 months = ≥2 missed
 *  quarterly filings; a company filing 10-Qs on schedule never trips it. Display-only; touches no signal. */
export function staleSharesMonths(
  sharesAsofDate: string | undefined,
  asof: string,
): number | null {
  if (!sharesAsofDate) return null;
  const cover = Date.parse(sharesAsofDate);
  const at = Date.parse(asof);
  if (Number.isNaN(cover) || Number.isNaN(at)) return null;
  const months = (at - cover) / (1000 * 60 * 60 * 24 * 30.44);
  return months > 6 ? Math.round(months) : null;
}

/** Market cap (a figure, not a meter) → "$8.0B" / "$600M"; "—" when either price or shares is missing
 *  (null value), never a fake "$0". */
export function formatMarketCap(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1e12) return `$${(value / 1e12).toFixed(1)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `$${Math.round(value / 1e6)}M`;
  return `$${Math.round(value).toLocaleString()}`;
}

/** The `fund` sleeve's latest close (ETF Sleeve, Slice 1) — read off the market-cap figure's PRICE
 *  provenance. A fund carries a price but NO shares fact, so `_market_cap` (scoring.py) returns value=null
 *  with a price-source provenance whose `detail.close` is the last as-of bar. "—" until a price bar is
 *  ingested. Display-only: price is CONTEXT, never a signal (#4/#6). */
export function sleevePriceLabel(m: { market_cap: ScoredFigureOut }): string {
  const priceProv = m.market_cap.provenance.find((p) => p.source === "price");
  const close = priceProv ? dnum(priceProv.detail, "close") : undefined;
  return close == null ? "—" : `$${close.toFixed(2)}`;
}

/** The "behind the scores" value headline for a meter — the real computed figure, honestly. A null
 *  value is meter-specific: runway null = cash-generative (top pip, no months figure); dilution null
 *  = no convert data ("—"). Market cap with ONE input on file says which half is missing — a bare "—"
 *  over a ratified shares fact read as "the confirm did nothing" (the gate-3 finding). */
export function meterValueLabel(meter: string, figure: ScoredFigureOut): string {
  const v = figure.value;
  switch (meter) {
    case "purity":
      return v == null ? "—" : `${v}%`;
    case "runway":
      return v == null ? "cash-generative" : `${v} mo`;
    case "catalysts":
      return `${v ?? 0} live`;
    case "dilution":
      return v == null ? "no convert data" : `${v}% overhang`;
    case "market cap": {
      if (v == null && figure.provenance.length > 0) {
        // the ADS-ratio withheld state (spec §10): BOTH inputs are on file but the ordinary-per-ADS
        // ratio couldn't be read — the cap is withheld, not missing; say which nothing this is.
        if (figure.provenance.some((p) => p.ref === "market-cap:ads-ratio-unread"))
          return "ADS ratio unread · cap withheld";
        const hasPrice = figure.provenance.some((p) => p.source === "price");
        const hasShares = figure.provenance.some(
          (p) => p.source !== "price" && p.source !== "computed",
        );
        if (hasShares && !hasPrice) return "shares on file · needs price";
        if (hasPrice && !hasShares) return "price on file · needs shares";
      }
      return formatMarketCap(v);
    }
    default:
      return v == null ? "—" : `${v}`;
  }
}

const SOURCE_LABEL: Record<string, string> = {
  "10-k-segment": "rev segment (10-K)",
  "10-k-business-description": "rev mix (10-K)",
  "10-q": "cash/burn (10-Q)",
  "10-q-cover": "shares (10-Q cover)",
  "10-k-cover": "shares (10-K cover)",
  "annual-cover": "shares (20-F/40-F cover)",
  "annual-statements": "cash/burn (20-F/40-F statements)",
  "10-k": "10-K",
  "8-k": "8-K",
  form4: "Form 4",
  doe_usaspending: "DOE award",
  xbrl: "XBRL",
  price: "price",
};

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source;
}

function isHttpUrl(s: string): boolean {
  return s.startsWith("http://") || s.startsWith("https://");
}

/** A provenance chip: a short, sourced pointer "behind the scores". Clickable to its source when
 *  resolvable — the wire's `url` (filing sources with a known issuer CIK) OR a `ref` that is itself a
 *  full EDGAR/award URL (the seed stores full filing URLs in `ref`). Price/computed refs stay plain.
 *  The full ref always rides the `title`. */
export interface ProvChip {
  text: string;
  url: string | null;
  title: string;
}

export function provChip(p: ProvenanceOut): ProvChip {
  if (p.source === "price") {
    const date = p.ref.startsWith("price:") ? p.ref.slice("price:".length) : p.ref;
    return { text: `price · ${date}`, url: p.url ?? null, title: p.ref };
  }
  const url = p.url ?? (isHttpUrl(p.ref) ? p.ref : null);
  return { text: sourceLabel(p.source), url, title: p.ref };
}

/** The "why" note behind a figure (the payoff: e.g. the recurring-vs-one-time burn composition, the
 *  cash-runway basis) — surfaced as a readable line, never crammed into a chip. */
export function provNotes(provenance: ProvenanceOut[]): string[] {
  const notes: string[] = [];
  for (const p of provenance) {
    const note = p.detail?.note;
    if (typeof note === "string" && note && !notes.includes(note)) notes.push(note);
  }
  return notes;
}
