import { businessTypeLabel, supersectorLabel } from "../../util/format";
import type { GroupBy, Quadrant, QuadrantGroupOut } from "./useRotationQuadrant";

// The Rotation Quadrant's pure view helpers (docs/dashboards/rotation-quadrant.md): labels, value formats, the
// table's sorts and filter, and the SVG geometry of the plot. Every number (x, y, the quadrants, the tails,
// who counts) is computed server-side; this only orders, words and draws it.

export const NEUTRAL = 100;

/** The quadrants in the default order (a group turning up first), each with its rule. */
export const QUADRANTS: readonly { key: Quadrant; label: string; rule: string }[] = [
  { key: "improving", label: "Improving", rule: "x at or below 100, y above 100" },
  { key: "leading", label: "Leading", rule: "x above 100, y above 100" },
  { key: "weakening", label: "Weakening", rule: "x above 100, y at or below 100" },
  { key: "lagging", label: "Lagging", rule: "x at or below 100, y at or below 100" },
];

export function quadrantLabel(q: Quadrant | null | undefined): string {
  return q ? (QUADRANTS.find((x) => x.key === q)?.label ?? q) : "—";
}

const quadrantRank = (q: Quadrant): number => QUADRANTS.findIndex((x) => x.key === q);

export const GROUP_BY_OPTIONS: readonly { value: GroupBy; label: string }[] = [
  { value: "link", label: "Link" },
  { value: "business_type", label: "Business type" },
  { value: "sector", label: "Sector" },
  { value: "card", label: "Card" },
];

/** A group's label in the current view. */
export function groupLabel(g: QuadrantGroupOut, groupBy: GroupBy): string {
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

/** Why a group has no dot, in neutral words; null for a plotted one. */
export function statusWords(g: QuadrantGroupOut, minPriced: number): string | null {
  if (g.status === "too_small") return `fewer than ${minPriced} priced names`;
  if (g.status === "no_history") return "not enough history";
  return null;
}

export function fmtNum(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(2);
}

/** A date with its year ("Oct 28, 2026"): the as-of line reads across years. */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** The quadrant 4 weeks ago → now ("Lagging → Improving"); "—" for a side without one. */
export function moveWords(g: QuadrantGroupOut): string {
  return `${quadrantLabel(g.quadrant_ago)} → ${quadrantLabel(g.quadrant)}`;
}

/** Did the group change quadrant in the last 4 weeks? Only when both quadrants are known. */
export function changedQuadrant(g: QuadrantGroupOut): boolean {
  return g.quadrant != null && g.quadrant_ago != null && g.quadrant !== g.quadrant_ago;
}

// --- the table's sorts -------------------------------------------------------------------------------------

export type SortCol = "label" | "quadrant" | "quadrant_ago" | "x" | "y" | "names";
export type SortDir = "asc" | "desc";
export type SortState = { col: SortCol; dir: SortDir };

/** Quadrant (Improving, Leading, Weakening, Lagging), then x highest first: the server's default order. */
export const DEFAULT_SORT: SortState = { col: "quadrant", dir: "asc" };

/** The column's sort value; null = "—" (sorts last in both directions). */
export function sortValue(g: QuadrantGroupOut, col: SortCol, groupBy: GroupBy): number | string | null {
  switch (col) {
    case "label":
      return groupLabel(g, groupBy).toLocaleLowerCase();
    case "quadrant":
      return g.quadrant ? quadrantRank(g.quadrant) : null;
    case "quadrant_ago":
      return g.quadrant_ago ? quadrantRank(g.quadrant_ago) : null;
    case "x":
      return g.x ?? null;
    case "y":
      return g.y ?? null;
    case "names":
      return g.priced;
  }
}

/** The groups in the column's order: "—" last in BOTH directions; ties by x highest first, then by label. */
export function sortGroups(
  groups: readonly QuadrantGroupOut[],
  sort: SortState,
  groupBy: GroupBy,
): QuadrantGroupOut[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  const label = (g: QuadrantGroupOut) => groupLabel(g, groupBy).toLocaleLowerCase();
  return [...groups].sort((a, b) => {
    const va = sortValue(a, sort.col, groupBy);
    const vb = sortValue(b, sort.col, groupBy);
    if (va == null || vb == null) {
      if (va == null && vb != null) return 1;
      if (vb == null && va != null) return -1;
    } else if (va !== vb) {
      return (va < vb ? -1 : 1) * sign;
    }
    if (sort.col === "names" && a.names !== b.names) return (a.names - b.names) * sign;
    const xa = a.x ?? null;
    const xb = b.x ?? null;
    if (xa != null && xb != null && xa !== xb) return xb - xa;
    if (xa != null && xb == null) return -1;
    if (xb != null && xa == null) return 1;
    const la = label(a);
    const lb = label(b);
    if (la !== lb) return la < lb ? -1 : 1;
    return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
  });
}

/** The next sort after a click: the active column flips; a new column starts in its natural direction (the
 *  label A→Z, the quadrants in the default order, the numbers highest first). */
export function nextSort(current: SortState, col: SortCol): SortState {
  if (current.col === col) return { col, dir: current.dir === "desc" ? "asc" : "desc" };
  return { col, dir: col === "label" || col === "quadrant" || col === "quadrant_ago" ? "asc" : "desc" };
}

// --- the plot's geometry -------------------------------------------------------------------------------------

export type Pt = { x: number; y: number };

/** A plotted group's tail as drawn: the measurable points ending at today's dot (a gap ends the line), oldest
 *  first. */
export function tailPath(g: QuadrantGroupOut): Pt[] {
  const out: Pt[] = [];
  for (let i = g.tail.length - 1; i >= 0; i--) {
    const p = g.tail[i];
    if (p.x == null || p.y == null) break;
    out.push({ x: p.x, y: p.y });
  }
  return out.reverse();
}

/** The plot's half-widths around 100 on each axis: the farthest dot or tail point, with a margin, never less
 *  than `min` (so a quiet field does not blow up a hair's move). */
export type Domain = { hx: number; hy: number };

export function plotDomain(groups: readonly QuadrantGroupOut[], min = 1): Domain {
  let hx = 0;
  let hy = 0;
  for (const g of groups) {
    for (const p of tailPath(g)) {
      hx = Math.max(hx, Math.abs(p.x - NEUTRAL));
      hy = Math.max(hy, Math.abs(p.y - NEUTRAL));
    }
  }
  return { hx: Math.max(min, hx * 1.1), hy: Math.max(min, hy * 1.1) };
}

/** x in the plot area (`w` wide): 100 at the center. */
export function sx(v: number, d: Domain, w: number): number {
  return ((v - NEUTRAL + d.hx) / (2 * d.hx)) * w;
}

/** y in the plot area (`h` tall): 100 at the center, up = higher. */
export function sy(v: number, d: Domain, h: number): number {
  return h - ((v - NEUTRAL + d.hy) / (2 * d.hy)) * h;
}
