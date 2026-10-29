import { businessTypeLabel, supersectorLabel } from "../../util/format";
import type {
  CrossClusterOut,
  CrossFirstMoverOut,
  CrossGroupOut,
  CrossNearGroupOut,
  CrossNearNameOut,
  CrossPostureOut,
  CrossTimelineRowOut,
  GroupBy,
} from "./useCrossRadar";

// The Cross Radar's pure view helpers (docs/dashboards/cross-radar.md): labels, value formats and each
// section's sort. Every number (the crosses, the cluster and first-mover rules, the near-cross band, the
// posture counts) is computed server-side; this only orders and words it. Kept in this folder rather than
// shared with the Rotation Map's, so either page deletes cleanly (docs/DASHBOARDS.md rule 8).

export const GROUP_BY_OPTIONS: readonly { value: GroupBy; label: string }[] = [
  { value: "link", label: "Link" },
  { value: "business_type", label: "Business type" },
  { value: "sector", label: "Sector" },
  { value: "card", label: "Card" },
];

/** A group's label in the current view. */
export function groupLabel(g: CrossGroupOut, groupBy: GroupBy): string {
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

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** "4 sessions ago"; the as-of's own bar reads "on the as-of session". */
export function sessionsAgo(n: number): string {
  return n === 0 ? "on the as-of session" : `${plural(n, "session", "sessions")} ago`;
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

/** A signed percent with two decimals ("-0.62%"); a gap's 5-session change in points ("+0.41 pts"). */
export function fmtGap(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}
export function fmtPts(v: number): string {
  return `${v > 0 ? "+" : ""}${v.toFixed(2)} pts`;
}

export const pct = (share: number) => `${Math.round(share * 100)}%`;

/** The posture bar's segments, in the member's own order, "other" last. */
export const POSTURES = [
  { key: "above_rising", label: "50d over 200d · rising" },
  { key: "above_falling", label: "50d over 200d · falling" },
  { key: "below_rising", label: "50d under 200d · rising" },
  { key: "below_falling", label: "50d under 200d · falling" },
  { key: "other", label: "other (partial, level or flat reading, or not priced)" },
] as const;
export type PostureKey = (typeof POSTURES)[number]["key"];

export function postureTotal(p: CrossPostureOut): number {
  return POSTURES.reduce((n, s) => n + p[s.key], 0);
}

// --- sorting: each section its own columns, the active one flips on a second click ----------------------

export type SortDir = "asc" | "desc";
export type Sort<C extends string> = { col: C; dir: SortDir };

/** The next sort after a header click: the active column flips; a new column starts in its natural order. */
export function nextSort<C extends string>(current: Sort<C>, col: C, firstDir: (c: C) => SortDir): Sort<C> {
  if (current.col === col) return { col, dir: current.dir === "desc" ? "asc" : "desc" };
  return { col, dir: firstDir(col) };
}

type Key = number | string;

function by<T>(
  rows: readonly T[],
  value: (r: T) => Key,
  dir: SortDir,
  tie: (a: T, b: T) => number,
): T[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va !== vb) return (va < vb ? -1 : 1) * sign;
    return tie(a, b);
  });
}

const cmp = (a: Key, b: Key) => (a < b ? -1 : a > b ? 1 : 0);
const labelOf = (g: CrossGroupOut, groupBy: GroupBy) => groupLabel(g, groupBy).toLocaleLowerCase();

export type ClusterCol = "label" | "count" | "share" | "within" | "rs_leaders";
export const CLUSTER_SORT: Sort<ClusterCol> = { col: "share", dir: "desc" };
export const clusterFirstDir = (c: ClusterCol): SortDir => (c === "label" || c === "within" ? "asc" : "desc");

/** Clusters: by the column, ties by share, then count, then label (the default order). */
export function sortClusters(rows: readonly CrossClusterOut[], sort: Sort<ClusterCol>, groupBy: GroupBy) {
  const value = (r: CrossClusterOut): Key =>
    sort.col === "label" ? labelOf(r.group, groupBy) : sort.col === "rs_leaders" ? r.rs_leaders : r[sort.col];
  return by(rows, value, sort.dir, (a, b) =>
    cmp(b.share, a.share) || cmp(b.count, a.count) || cmp(labelOf(a.group, groupBy), labelOf(b.group, groupBy)) ||
    cmp(a.direction, b.direction),
  );
}

export type MoverCol = "label" | "date" | "closing_in" | "already" | "not_yet";
export const MOVER_SORT: Sort<MoverCol> = { col: "date", dir: "desc" };
export const moverFirstDir = (c: MoverCol): SortDir => (c === "label" ? "asc" : "desc");

/** First movers: by the column, ties newest cross first, then label. */
export function sortMovers(rows: readonly CrossFirstMoverOut[], sort: Sort<MoverCol>, groupBy: GroupBy) {
  const value = (r: CrossFirstMoverOut): Key =>
    sort.col === "label" ? labelOf(r.group, groupBy) : sort.col === "date" ? r.leader.d : r[sort.col];
  return by(rows, value, sort.dir, (a, b) =>
    cmp(b.leader.d, a.leader.d) || cmp(labelOf(a.group, groupBy), labelOf(b.group, groupBy)) ||
    cmp(a.direction, b.direction),
  );
}

export type NearCol = "label" | "gap" | "change";
export const NEAR_SORT: Sort<NearCol> = { col: "gap", dir: "asc" };
export const nearFirstDir = (c: NearCol): SortDir => (c === "change" ? "desc" : "asc");

const nameValue = (n: CrossNearNameOut, col: NearCol): Key =>
  col === "change" ? Math.abs(n.change) : Math.abs(n.gap_pct);

/** The near-cross groups and their names: by the smallest gap (or the largest 5-session move, or label), the
 *  names inside each group by the same column (by gap under a label sort). */
export function sortNear(
  groups: readonly CrossNearGroupOut[],
  sort: Sort<NearCol>,
  groupBy: GroupBy,
): CrossNearGroupOut[] {
  const nameCol: NearCol = sort.col === "label" ? "gap" : sort.col;
  const nameDir: SortDir = sort.col === "label" ? "asc" : sort.dir;
  const withNames = groups.map((g) => ({
    ...g,
    names: by(g.names, (n) => nameValue(n, nameCol), nameDir, (a, b) => cmp(a.ticker, b.ticker)),
  }));
  const value = (g: CrossNearGroupOut): Key =>
    sort.col === "label" ? labelOf(g.group, groupBy) : nameValue(g.names[0], sort.col);
  return by(withNames, value, sort.dir, (a, b) => cmp(labelOf(a.group, groupBy), labelOf(b.group, groupBy)));
}

export type TimelineCol = "label" | "total" | "golden" | "death";
export const TIMELINE_SORT: Sort<TimelineCol> = { col: "total", dir: "desc" };
export const timelineFirstDir = (c: TimelineCol): SortDir => (c === "label" ? "asc" : "desc");

/** The timeline rows: by total crosses in the window (or golden, death, label), ties by label. */
export function sortTimeline(rows: readonly CrossTimelineRowOut[], sort: Sort<TimelineCol>, groupBy: GroupBy) {
  const value = (r: CrossTimelineRowOut): Key =>
    sort.col === "label"
      ? labelOf(r.group, groupBy)
      : sort.col === "total"
        ? r.golden + r.death
        : r[sort.col];
  return by(rows, value, sort.dir, (a, b) =>
    cmp(labelOf(a.group, groupBy), labelOf(b.group, groupBy)) || cmp(a.group.key, b.group.key),
  );
}
