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
