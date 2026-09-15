// Presentation helpers shared by the Cockpit + CallCard.

export const STATE_CLASS: Record<string, string> = {
  incubating: "incub",
  warming: "warm",
  armed: "armed",
  managing: "manage",
};

export const STATE_LABEL: Record<string, string> = {
  incubating: "Incubating",
  warming: "Warming",
  armed: "Armed",
  managing: "Managing",
};

export const CALL_HEAD: Record<string, string> = {
  incubating: "Watch · the gate",
  warming: "Readiness · the gate",
  armed: "The Call",
  managing: "Position",
};

const VERDICT_LABEL: Record<string, string> = {
  watching: "Watching",
  not_yet: "Not yet",
  flip_only: "FLIP only",
  starter_entry: "STARTER entry",
  core_entry: "CORE entry",
  managing: "Managing",
};

export function verdictLabel(v: string): string {
  return VERDICT_LABEL[v] ?? v;
}

export function gradeClass(g: string | null | undefined): string {
  return g === "core" ? "core" : g === "flip" ? "flip" : "";
}

const BUSINESS_TYPE_LABEL: Record<string, string> = {
  miner: "miner",
  bank: "bank",
  utilities: "utilities",
  oil_gas: "oil & gas",
  semiconductors: "semis",
  biotech_pharma: "biotech/pharma",
  medical_devices: "med devices",
  healthcare_services: "healthcare svcs",
  software_it: "software/IT",
  finance_brokers: "finance & brokers",
  real_estate: "real estate",
  industrials_machinery: "industrials",
  chemicals_materials: "chemicals",
  comms_media: "comms/media",
  transportation: "transport",
  consumer_retail: "consumer",
  business_services: "biz services",
  spac: "SPAC",
  other: "other",
};

/** Business-type LEAF → its display label (the cockpit Type chip / the NamePanel identity cell).
 *  Derived server-side from the SIC maps (`securities/business_type/`); display identity, never a
 *  call input. Null/undefined = unclassified (un-enriched) → render sites guard and show "—". */
export function businessTypeLabel(businessType: string | null | undefined): string {
  return businessType ? (BUSINESS_TYPE_LABEL[businessType] ?? businessType) : "—";
}

const SUPERSECTOR_LABEL: Record<string, string> = {
  healthcare: "Healthcare",
  financials: "Financials",
  technology: "Technology",
  industrials: "Industrials",
  consumer_comms: "Consumer & Comms",
  materials: "Materials",
  energy_utilities: "Energy & Utilities",
  real_estate: "Real Estate",
  other: "Other",
};

/** Business-type SUPER-SECTOR → its display label (the cockpit's "watch through the types" group
 *  headers — "are the utilities moving?"). Same identity discipline as the leaf label. */
export function supersectorLabel(supersector: string | null | undefined): string {
  return supersector ? (SUPERSECTOR_LABEL[supersector] ?? supersector) : "Unclassified";
}

/** A single-name thesis shows its ticker; a multi-name theme shows a basket marker (never a bare "—",
 *  which reads as missing data). */
export function tickerLabel(ticker: string | null | undefined, basketSize?: number | null): string {
  if (ticker) return ticker;
  if (basketSize && basketSize > 1) return `◇ ${basketSize}`;
  return "◇";
}

/** Accent CSS variable for a lifecycle state (the confidence bar, the ticker, etc.). */
export function accentVar(stateClass: string): string {
  return stateClass === "incub" ? "--txt-3" : `--${stateClass}`;
}

/** Whole days from `asof` to a target ISO date (negative = past); null if no/invalid target. */
export function daysFrom(asof: string, target: string | null | undefined): number | null {
  if (!target) return null;
  const a = Date.parse(`${asof}T00:00:00Z`);
  const t = Date.parse(`${target}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(t)) return null;
  return Math.round((t - a) / 86_400_000);
}

export function fmtDate(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** Newest-first comparator for trigger/risk rows by ISO (`YYYY-MM-DD`) event date; dateless rows sort
 *  last. String compare == chronological for ISO dates. Array.sort is stable, so equal dates keep the
 *  backend order (grouped by name, conviction-before-confirmation). */
export function byEventDateDesc(
  a: { event_date?: string | null },
  b: { event_date?: string | null },
): number {
  return (b.event_date ?? "").localeCompare(a.event_date ?? "");
}

/** Today's date as YYYY-MM-DD in the user's LOCAL timezone — the default as-of on load. Built from the local
 *  Y/M/D (not toISOString(), which is UTC and can land a day off near midnight). */
export function todayISO(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** F11 — the past-as-of gate: true when `asof` (YYYY-MM-DD) is strictly before today (local).
 *  ISO date strings compare lexically == chronologically (byEventDateDesc above leans on the same
 *  fact), so a plain string compare avoids a timezone-sensitive Date parse. Today itself, and any
 *  date beyond it, read false — only a genuine past date is a recompute; the live view carries no
 *  caveat (honest loudness #7 / WB#3: the note marks the exception, not the rule). */
export function isPastAsof(asof: string): boolean {
  return asof < todayISO();
}

/** F11 — the ONE wording for "you're looking at a recompute, not the record" (Board + Cockpit call
 *  views). Deliberately basis-agnostic: it never claims the roster or facts are period-accurate
 *  (untrue for a pre-F12 fallback date) — it only flags that this is a recompute, not the immutable
 *  nightly calls-of-record row the Scoreboard scores. */
export function recomputeNote(asof: string): string {
  return `Recomputed as of ${fmtDate(asof)} — not the recorded call`;
}

/** The tooltip paired with `recomputeNote` everywhere it renders. */
export const RECOMPUTE_TOOLTIP =
  "Re-derived from data known as of this date; the recorded call-of-record is the Scoreboard entry.";
