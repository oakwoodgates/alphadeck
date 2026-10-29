import { businessTypeLabel, supersectorLabel } from "../../util/format";
import type { GroupBy, LeadersInsiderGroupOut, LeadersNameOut, ListKey } from "./useLeaders";

// The Leaders board's pure view helpers (docs/dashboards/leaders.md): list labels, value formats and each
// table's sort. Every rule (the thresholds, who is on which list, the no-reading counts, the insider groups) is
// computed server-side; this only orders and words it. Kept in this folder rather than shared with the other
// dashboards', so each page deletes cleanly (docs/DASHBOARDS.md rule 8).

/** The lists, in page order: the tab label, the quiet chip on the several-lists section, and the header of
 *  the list's own value column. */
export const LISTS: readonly { key: ListKey; label: string; chip: string; value: string; title: string }[] =
  [
    {
      key: "near_high",
      label: "Near 52w high",
      chip: "52w high",
      value: "% off 52w high",
      title: "the % off the 52-week closing high, and the high's print date",
    },
    {
      key: "rs_leader",
      label: "RS leader",
      chip: "RS",
      value: "RS 13w high vs",
      title: "the benchmarks the relative strength is at a 13-week high against",
    },
    {
      key: "coiling",
      label: "Coiling",
      chip: "coiling",
      value: "Contraction",
      title: "the newest base segment's range as a share of the oldest's",
    },
    {
      key: "insider",
      label: "Insider buying",
      chip: "insider",
      value: "Buys/buyers 30d",
      title: "open-market buys / distinct buyers over the last 30 days",
    },
    {
      key: "volume",
      label: "Volume",
      chip: "volume",
      value: "RVOL 20D",
      title: "the as-of bar's volume over the mean of the 20 bars before it",
    },
  ];

export const listMeta = (key: ListKey) => LISTS.find((l) => l.key === key) ?? LISTS[0];

export const GROUP_BY_OPTIONS: readonly { value: GroupBy; label: string }[] = [
  { value: "link", label: "Link" },
  { value: "business_type", label: "Business type" },
  { value: "sector", label: "Sector" },
  { value: "card", label: "Card" },
];

/** A group's label in the current view. */
export function groupLabel(g: LeadersInsiderGroupOut, groupBy: GroupBy): string {
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

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function fmtRatio(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(2)}×`;
}

/** The vcp contraction as a share of the base start ("42%"). */
export function fmtContraction(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export function fmtYesNo(v: boolean | null | undefined): string {
  return v == null ? "—" : v ? "yes" : "no";
}

export const pct = (share: number) => `${Math.round(share * 100)}%`;

// --- sorting ---------------------------------------------------------------------------------------------

export type SortDir = "asc" | "desc";
export type Sort<C extends string> = { col: C; dir: SortDir };

export type NameCol =
  | "ticker"
  | "lists"
  | "ret_7d"
  | "ret_30d"
  | "above_50d"
  | "rvol20"
  | "pct_off_high"
  | "rs_vs"
  | "contraction"
  | "insider";

/** Each list's own value column (the Volume list's own value IS the RVOL 20D column). */
export const VALUE_COL: Record<ListKey, NameCol> = {
  near_high: "pct_off_high",
  rs_leader: "rs_vs",
  coiling: "contraction",
  insider: "insider",
  volume: "rvol20",
};

/** Each table's first order, the server's default: the several-lists by the number of lists (then the 30D
 *  return); near-high closest first; RS by the 30D return; coiling tightest first; insider by buys (then
 *  buyers); volume by RVOL 20D. */
export const SEVERAL_SORT: Sort<NameCol> = { col: "lists", dir: "desc" };
export const LIST_SORT: Record<ListKey, Sort<NameCol>> = {
  near_high: { col: "pct_off_high", dir: "desc" },
  rs_leader: { col: "ret_30d", dir: "desc" },
  coiling: { col: "contraction", dir: "asc" },
  insider: { col: "insider", dir: "desc" },
  volume: { col: "rvol20", dir: "desc" },
};

/** The direction a column starts in on its first click: the ticker A→Z, the contraction tightest first,
 *  everything else highest first. */
export function nameFirstDir(col: NameCol): SortDir {
  return col === "ticker" || col === "contraction" ? "asc" : "desc";
}

function nameValue(n: LeadersNameOut, col: NameCol): number | string | null {
  switch (col) {
    case "ticker":
      return n.ticker.toLocaleLowerCase();
    case "lists":
      return n.lists.length;
    case "above_50d":
      return n.above_50d == null ? null : n.above_50d ? 1 : 0;
    case "rs_vs":
      return n.rs_vs.length || null;
    case "insider":
      return n.buys_30d ?? null;
    default:
      return n[col] ?? null;
  }
}

/** The tie-break a column carries before the ticker: the several-lists count by the 30D return, buys by
 *  buyers. */
function nameThen(n: LeadersNameOut, col: NameCol): number | null {
  if (col === "lists") return n.ret_30d ?? null;
  if (col === "insider") return n.buyers_30d ?? null;
  return null;
}

/** A value comparison with "—" last in BOTH directions; 0 on a tie. */
function cmp(a: number | string | null, b: number | string | null, sign: number): number {
  if (a == null || b == null) {
    if (a == null && b != null) return 1;
    if (b == null && a != null) return -1;
    return 0;
  }
  if (a === b) return 0;
  return (a < b ? -1 : 1) * sign;
}

export function sortNames(names: readonly LeadersNameOut[], sort: Sort<NameCol>): LeadersNameOut[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...names].sort(
    (a, b) =>
      cmp(nameValue(a, sort.col), nameValue(b, sort.col), sign) ||
      cmp(nameThen(a, sort.col), nameThen(b, sort.col), sign) ||
      a.ticker.localeCompare(b.ticker) ||
      a.security_id.localeCompare(b.security_id),
  );
}

export type GroupCol = "label" | "with_buys" | "share" | "buys";
export const GROUP_SORT: Sort<GroupCol> = { col: "with_buys", dir: "desc" };
export const groupFirstDir = (col: GroupCol): SortDir => (col === "label" ? "asc" : "desc");

export function sortGroups(
  groups: readonly LeadersInsiderGroupOut[],
  sort: Sort<GroupCol>,
  groupBy: GroupBy,
): LeadersInsiderGroupOut[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  const label = (g: LeadersInsiderGroupOut) => groupLabel(g, groupBy).toLocaleLowerCase();
  const value = (g: LeadersInsiderGroupOut): number | string =>
    sort.col === "label" ? label(g) : g[sort.col];
  return [...groups].sort(
    (a, b) =>
      cmp(value(a), value(b), sign) ||
      (sort.col === "with_buys" ? cmp(a.share, b.share, sign) : 0) ||
      cmp(label(a), label(b), 1) ||
      cmp(a.key, b.key, 1),
  );
}

/** The next sort after a header click: the active column flips; a new column starts in its first direction. */
export function nextSort<C extends string>(
  current: Sort<C>,
  col: C,
  firstDir: (c: C) => SortDir,
): Sort<C> {
  if (current.col === col) return { col, dir: current.dir === "desc" ? "asc" : "desc" };
  return { col, dir: firstDir(col) };
}
