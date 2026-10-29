import { businessTypeLabel, supersectorLabel } from "../../util/format";
import type {
  GroupBy,
  LandingGroupOut,
  LandingGroupWindowOut,
  LandingNameOut,
  LandingNameWindowOut,
  LandingSplitOut,
  Quadrant,
} from "./useLanding";

// The Price & Volume Landing's pure view helpers (docs/dashboards/landing.md): labels, value formats, the
// quadrant split's words and the sorts. Every number is computed server-side; this only orders and words it.

/** The windows, in trading bars (the payload's own `windows` carry the bar counts). */
export const WINDOWS = [
  { key: "1d", label: "1D" },
  { key: "7d", label: "7D" },
  { key: "30d", label: "30D" },
  { key: "90d", label: "90D" },
] as const;
export type WindowKey = (typeof WINDOWS)[number]["key"];

/** A sort: the row label, the name count, or a window's median position / accumulation count. */
export type SortCol = "label" | "names" | `${WindowKey}:position` | `${WindowKey}:accumulation`;
export type SortDir = "asc" | "desc";
export type SortState = { col: SortCol; dir: SortDir };

export const DEFAULT_SORT: SortState = { col: "7d:position", dir: "desc" };

export const GROUP_BY_OPTIONS: readonly { value: GroupBy; label: string }[] = [
  { value: "link", label: "Link" },
  { value: "business_type", label: "Business type" },
  { value: "sector", label: "Sector" },
  { value: "card", label: "Card" },
];

/** The quadrants in display order, each with its short split word and its full name. Only accumulation and
 *  distribution carry a (quiet) tone; the rest stay gray. */
export const QUADRANTS: readonly { key: Quadrant; short: string; full: string; toned: boolean }[] = [
  { key: "accumulation", short: "accum", full: "accumulation: up on rising volume", toned: true },
  { key: "thin_rally", short: "thin", full: "thin rally: up, volume not rising", toned: false },
  { key: "distribution", short: "dist", full: "distribution: down on rising volume", toned: true },
  { key: "rest", short: "rest", full: "rest: down, volume not rising", toned: false },
  { key: "flat", short: "flat", full: "flat: an unchanged close", toned: false },
];

export function quadrantOf(key: Quadrant) {
  return QUADRANTS.find((q) => q.key === key)!;
}

/** A group's row label in the current view. */
export function groupLabel(g: LandingGroupOut, groupBy: GroupBy): string {
  switch (groupBy) {
    case "link":
      return `${g.link ?? "Unsegmented"} · ${g.thesis_name ?? ""}`;
    case "card":
      return g.thesis_name ?? "";
    case "business_type":
      return g.business_type ? businessTypeLabel(g.business_type) : "Unclassified";
    case "sector":
      return supersectorLabel(g.supersector);
  }
}

export function groupWindow(g: LandingGroupOut, key: string): LandingGroupWindowOut | undefined {
  return g.windows.find((w) => w.key === key);
}

export function nameWindow(m: LandingNameOut, key: string): LandingNameWindowOut | undefined {
  return m.windows.find((w) => w.key === key);
}

/** The split as counts, zeros left out ("3 accum · 1 thin · 1 rest"), in the quadrant order. */
export function splitParts(s: LandingSplitOut): { key: Quadrant; n: number; short: string; toned: boolean }[] {
  return QUADRANTS.filter((q) => s[q.key] > 0).map((q) => ({
    key: q.key,
    n: s[q.key],
    short: q.short,
    toned: q.toned,
  }));
}

export function splitWords(s: LandingSplitOut): string {
  const parts = splitParts(s);
  return parts.length === 0 ? "—" : parts.map((p) => `${p.n} ${p.short}`).join(" · ");
}

/** Names with a quadrant reading in the window. */
export function splitRead(s: LandingSplitOut): number {
  return s.accumulation + s.thin_rally + s.distribution + s.rest + s.flat;
}

export function fmtPos(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v)}%`;
}

export function fmtRatio(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(2)}×`;
}

export function fmtRet(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** A date with its year ("Oct 28, 2026"): the as-of line reads across years. */
export function fmtDay(d: string): string {
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

function windowCol(col: SortCol): { win: WindowKey; by: "position" | "accumulation" } | null {
  const [win, by] = col.split(":");
  if (by !== "position" && by !== "accumulation") return null;
  return { win: win as WindowKey, by };
}

/** The column's sort value; null = "—" (sorts last in both directions). An accumulation count is "—" only
 *  when no name in the group has a quadrant reading in that window. */
export function sortValue(g: LandingGroupOut, col: SortCol, groupBy: GroupBy): number | string | null {
  if (col === "label") return groupLabel(g, groupBy).toLocaleLowerCase();
  if (col === "names") return g.names;
  const wc = windowCol(col);
  const w = wc ? groupWindow(g, wc.win) : undefined;
  if (!wc || !w) return null;
  if (wc.by === "position") return w.range_median ?? null;
  return splitRead(w.split) > 0 ? w.split.accumulation : null;
}

/** The groups in the column's order: "—" last in BOTH directions, ties by label. */
export function sortGroups(
  groups: readonly LandingGroupOut[],
  sort: SortState,
  groupBy: GroupBy,
): LandingGroupOut[] {
  const label = (g: LandingGroupOut) => groupLabel(g, groupBy).toLocaleLowerCase();
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...groups].sort((a, b) => {
    const va = sortValue(a, sort.col, groupBy);
    const vb = sortValue(b, sort.col, groupBy);
    if (va == null || vb == null) {
      if (va == null && vb != null) return 1;
      if (vb == null && va != null) return -1;
    } else if (va !== vb) {
      return (va < vb ? -1 : 1) * sign;
    }
    const la = label(a);
    const lb = label(b);
    if (la !== lb) return la < lb ? -1 : 1;
    return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
  });
}

/** A group's names by their own position in the sorted window (the 7D when the sort is not a window's),
 *  highest first unless that window sorts ascending; "—" last, ties by ticker. */
export function sortNames(names: readonly LandingNameOut[], sort: SortState): LandingNameOut[] {
  const wc = windowCol(sort.col);
  const key = wc ? wc.win : "7d";
  const sign = wc && sort.dir === "asc" ? 1 : -1;
  return [...names].sort((a, b) => {
    const va = nameWindow(a, key)?.range_pos ?? null;
    const vb = nameWindow(b, key)?.range_pos ?? null;
    if (va == null || vb == null) {
      if (va == null && vb != null) return 1;
      if (vb == null && va != null) return -1;
    } else if (va !== vb) {
      return (va < vb ? -1 : 1) * sign;
    }
    return a.ticker.localeCompare(b.ticker);
  });
}

/** The next sort after a click: the active column flips; a new column starts highest first (the label
 *  column A→Z). */
export function nextSort(current: SortState, col: SortCol): SortState {
  if (current.col === col) return { col, dir: current.dir === "desc" ? "asc" : "desc" };
  return { col, dir: col === "label" ? "asc" : "desc" };
}
