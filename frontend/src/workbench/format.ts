// Workbench display helpers — turn the wire figures into the chips/labels the operator reads.
// All values are DATA-DERIVED (the scoring engine, Slice 3); nothing here invents a number.

import type { ProvenanceOut, ScoredFigureOut } from "../api/hooks";

// archLabel + its ARCH_LABEL map moved to the shared util/format (the Cockpit board uses it too); re-exported
// here so the Workbench / ChainEditor call sites import it unchanged.
export { archLabel } from "../util/format";

/** The basket-member archetypes the operator classifies a name as (the add-a-name form). */
export const ARCHETYPES = ["leader", "high_beta", "lotto", "shovel", "adjacent", "fund"] as const;

/** The collision-lens predicate: a term that is a single all-caps token (letters+digits, ≥2 chars — HBM,
 *  DRAM) is collision-prone: it matches tickers, fund names, and boilerplate that carry the LETTERS without
 *  any of the words that would confirm the meaning. Deliberately the simple v1 rule (NAND-style real-word
 *  acronyms count too) — judged on live data, tweaked after. */
export const isAcronymTerm = (term: string): boolean => /^[A-Z][A-Z0-9]{1,9}$/.test(term.trim());

/** A human message from a thrown API error (FastAPI `{detail}`); a safe fallback otherwise. */
export function errText(e: unknown): string {
  const d = (e as { detail?: unknown } | null)?.detail;
  return typeof d === "string" ? d : "the request was rejected";
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

/** The "behind the scores" value headline for a meter — the real computed figure, honestly. A null
 *  value is meter-specific: runway null = cash-generative (top pip, no months figure); dilution null
 *  = no convert data ("—"). */
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
    case "market cap":
      return formatMarketCap(v);
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
