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

const ARCH_LABEL: Record<string, string> = {
  leader: "leader",
  high_beta: "high-beta",
  lotto: "lotto",
  shovel: "shovel",
  adjacent: "adjacent",
  fund: "ETF sleeve",
};

/** Basket-member archetype → its display label (the `.arch` chip; CSS uppercases it visually). The single
 *  source of archetype labels, shared by the Cockpit board, the Workbench chips, and the DDRail picker.
 *  Null/undefined = not yet characterized (item F — placement never stamps a default) → "unset"; render
 *  sites usually guard and show nothing, this is the defensive fallback so "null" never reaches the UI. */
export function archLabel(archetype: string | null | undefined): string {
  return archetype ? (ARCH_LABEL[archetype] ?? archetype) : "unset";
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

/** Today's date as YYYY-MM-DD in the user's LOCAL timezone — the default as-of on load. Built from the local
 *  Y/M/D (not toISOString(), which is UTC and can land a day off near midnight). */
export function todayISO(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}
