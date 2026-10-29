import { businessTypeLabel, supersectorLabel } from "../../util/format";
import type {
  GroupBy,
  RotationGroupOut,
  RotationMapOut,
  RotationNameOut,
  RotationWindowOut,
} from "./useRotationMap";

// The Rotation Map's pure view helpers (docs/dashboards/rotation-map.md): labels, the vs-SPY shift, value
// formats and the column sort. Every number is computed server-side; this only orders and words it.

/** The Cockpit's own return windows and labels (the trailing_returns member's keys). */
export const WINDOWS = [
  { key: "ret_1d", label: "1d" },
  { key: "ret_7d", label: "7d" },
  { key: "ret_30d", label: "30d" },
  { key: "ret_90d", label: "90d" },
  { key: "ret_1y", label: "1Y" },
] as const;
export type WindowKey = (typeof WINDOWS)[number]["key"];

export type SortCol =
  | "label"
  | "names"
  | WindowKey
  | "above_50d"
  | "rvol20"
  | "golden"
  | "death"
  | "rs_leaders";
export type SortDir = "asc" | "desc";
export type SortState = { col: SortCol; dir: SortDir };

export const DEFAULT_SORT: SortState = { col: "ret_7d", dir: "desc" };

export const GROUP_BY_OPTIONS: readonly { value: GroupBy; label: string }[] = [
  { value: "link", label: "Link" },
  { value: "business_type", label: "Business type" },
  { value: "sector", label: "Sector" },
  { value: "card", label: "Card" },
];

/** A group's row label in the current view. */
export function groupLabel(g: RotationGroupOut, groupBy: GroupBy): string {
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

export function windowOf(g: RotationGroupOut, key: string): RotationWindowOut | undefined {
  return g.windows.find((w) => w.key === key);
}

/** A return as the page shows it: raw, or minus SPY's own return for that window (null when either is). */
export function shown(
  value: number | null | undefined,
  key: string,
  vsSpy: boolean,
  spy: RotationMapOut["spy"],
): number | null {
  if (value == null) return null;
  if (!vsSpy) return value;
  const s = spy?.[key];
  return s == null ? null : value - s;
}

export function fmtPct(v: number | null): string {
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

export function fmtRatio(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(2)}×`;
}

/** The column's sort value; null = "—" (sorts last in both directions). A share over fewer than
 *  `minPriced` names is no group read (the thin-group rule), so it sorts with the "—"s. */
export function sortValue(
  g: RotationGroupOut,
  col: SortCol,
  groupBy: GroupBy,
  minPriced: number,
): number | string | null {
  switch (col) {
    case "label":
      return groupLabel(g, groupBy).toLocaleLowerCase();
    case "names":
      return g.names;
    case "above_50d":
      return g.above_50d_n >= minPriced ? g.above_50d / g.above_50d_n : null;
    case "rvol20":
      return g.rvol20_median ?? null;
    case "golden":
      return g.golden;
    case "death":
      return g.death;
    case "rs_leaders":
      return g.rs_leaders;
    default:
      return windowOf(g, col)?.median ?? null;
  }
}

/** The groups in the column's order: "—" last in BOTH directions, ties by label. The vs-SPY shift is one
 *  number per column, so it never changes an order (the raw median sorts for both). */
export function sortGroups(
  groups: readonly RotationGroupOut[],
  sort: SortState,
  groupBy: GroupBy,
  minPriced: number,
): RotationGroupOut[] {
  const label = (g: RotationGroupOut) => groupLabel(g, groupBy).toLocaleLowerCase();
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...groups].sort((a, b) => {
    const va = sortValue(a, sort.col, groupBy, minPriced);
    const vb = sortValue(b, sort.col, groupBy, minPriced);
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

/** A group's names, by the active return window when one is sorted (else the 7d), "—" last. */
export function sortNames(
  names: readonly RotationNameOut[],
  sort: SortState,
): RotationNameOut[] {
  const key = WINDOWS.some((w) => w.key === sort.col) ? sort.col : "ret_7d";
  const sign = WINDOWS.some((w) => w.key === sort.col) && sort.dir === "asc" ? 1 : -1;
  return [...names].sort((a, b) => {
    const va = a.returns[key] ?? null;
    const vb = b.returns[key] ?? null;
    if (va == null || vb == null) {
      if (va == null && vb != null) return 1;
      if (vb == null && va != null) return -1;
    } else if (va !== vb) {
      return (va < vb ? -1 : 1) * sign;
    }
    return a.ticker.localeCompare(b.ticker);
  });
}

/** The next sort after a header click: the active column flips; a new column starts highest first (the
 *  label column A→Z). */
export function nextSort(current: SortState, col: SortCol): SortState {
  if (current.col === col) return { col, dir: current.dir === "desc" ? "asc" : "desc" };
  return { col, dir: col === "label" ? "asc" : "desc" };
}
