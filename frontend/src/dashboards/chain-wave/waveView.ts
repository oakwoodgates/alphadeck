import type { components } from "../../api/types.gen";
import type {
  ChainWaveCardOut,
  ChainWaveCellOut,
  ChainWaveReadoutOut,
  ChainWaveRowOut,
  ChainWaveWeekOut,
  WaveWeeks,
} from "./useChainWave";

// The Chain Wave's pure view helpers (docs/dashboards/chain-wave.md): labels, value formats, the cell's muted
// diverging tone, the week header, the readout's words and the picker's filter. Every number (the cells, who
// counts, priced, thin, the readout) is computed server-side; this only words and shades it.

type ContextRole = components["schemas"]["ContextRole"];

export const WINDOW_OPTIONS: readonly { value: WaveWeeks; label: string }[] = [
  { value: 13, label: "13 weeks" },
  { value: 26, label: "26 weeks" },
  { value: 52, label: "52 weeks" },
];
export const DEFAULT_WEEKS: WaveWeeks = 26;

/** The cell's color saturates at this many percentage points either way. */
export const SATURATE_PP = 10;
/** SPY's own weekly bar reaches the cell's edge at this many percent either way (SPY's weeks are small). */
export const SPY_SATURATE_PCT = 5;
/** A thin row (fewer than 3 priced names) is drawn at this share of the strength. */
export const THIN_STRENGTH = 0.5;

/** A role row's label: the role's label (workbench/format's CONTEXT_ROLE_LABEL) as a group. Exhaustive against
 *  the generated enum, so a role added server-side fails the build here instead of rendering a blank. */
export const ROLE_ROW_LABEL: Record<ContextRole, string> = {
  payer: "Payers",
  counter_case: "Counter-case",
  direct_read: "Direct reads",
  participant: "Participants",
  financier: "Financiers",
  benchmark: "Benchmarks",
};

/** A row's label on the page. */
export function rowLabel(r: Pick<ChainWaveRowOut, "kind" | "label" | "role">): string {
  if (r.kind === "role" && r.role) return ROLE_ROW_LABEL[r.role];
  return r.label;
}

/** The muted diverging tone of a cell: its hue by the sign of `diff`, its strength by |diff| up to
 *  `SATURATE_PP`, halved on a thin row. "gap" = no value ("—"). */
export type Tone = { tone: "pos" | "neg" | "flat" | "gap"; strength: number };

export function cellTone(diff: number | null | undefined, thin: boolean): Tone {
  if (diff == null) return { tone: "gap", strength: 0 };
  const strength = Math.min(1, Math.abs(diff) / SATURATE_PP) * (thin ? THIN_STRENGTH : 1);
  if (diff === 0) return { tone: "flat", strength: 0 };
  return { tone: diff > 0 ? "pos" : "neg", strength };
}

/** SPY's own weekly return as a small bar: up or down from the middle, its height a share of half the cell. */
export function spyBar(ret: number | null | undefined): { dir: "up" | "down"; share: number } | null {
  if (ret == null) return null;
  return { dir: ret >= 0 ? "up" : "down", share: Math.min(1, Math.abs(ret) / SPY_SATURATE_PCT) };
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function fmtPp(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)} pp`;
}

const MONTH = (d: string) =>
  new Date(Date.parse(`${d}T00:00:00Z`)).toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** A week's dates: its first to its last session ("May 18, 2026 – May 22, 2026"), or its Monday when it held
 *  no session. */
export function weekWords(w: ChainWaveWeekOut): string {
  if (!w.first_session || !w.close) return `week of ${fmtDay(w.monday)}, no session`;
  const span = w.first_session === w.close ? fmtDay(w.close) : `${fmtDay(w.first_session)} – ${fmtDay(w.close)}`;
  return w.to_date ? `${span} (week to date)` : span;
}

/** The header text over each week column: the month where it changes, "wk to date" on a to-date last week,
 *  nothing elsewhere (each column carries its full dates in its title). */
export function weekHeaders(weeks: readonly ChainWaveWeekOut[]): string[] {
  return weeks.map((w, i) => {
    if (i === weeks.length - 1 && w.to_date) return "wk to date";
    const m = MONTH(w.monday);
    return i === 0 || MONTH(weeks[i - 1].monday) !== m ? m : "";
  });
}

/** Why a cell is "—", in neutral words. */
export function gapWords(gap: ChainWaveCellOut["gap"]): string | null {
  switch (gap) {
    case "no_session":
      return "no session that week";
    case "not_started":
      return "the line had not started by last week's close";
    case "no_return":
      return "no name had a daily return that week";
    case "no_spy":
      return "SPY had no return that week";
    default:
      return null;
  }
}

/** "Last 4 weeks vs SPY: payers +X.X pp · counting names +Y.Y pp": numbers only, neutral words. */
export function readoutWords(r: ChainWaveReadoutOut): string {
  return `Last ${r.weeks} weeks vs SPY: payers ${fmtPp(r.payers)} · counting names ${fmtPp(r.counting)}`;
}

/** The picker's filter: the cards whose name holds the query (any case), in the server's A→Z order. */
export function filterCards(cards: readonly ChainWaveCardOut[], query: string): ChainWaveCardOut[] {
  const q = query.trim().toLocaleLowerCase();
  return q ? cards.filter((c) => c.name.toLocaleLowerCase().includes(q)) : [...cards];
}

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
