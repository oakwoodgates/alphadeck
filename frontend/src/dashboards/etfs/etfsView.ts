import type {
  EtfCompareRowOut,
  EtfReadingOut,
  EtfTrendRowOut,
  EtfWindow,
} from "./useEtfs";

// The ETFs page's pure view helpers: the window and sort options, the row orders, the number words and the
// geometry the inline-SVG minis and the log bars share. Every number is computed server-side; this file only
// orders, formats and draws it.

export const WINDOW_OPTIONS: readonly { value: EtfWindow; label: string }[] = [
  { value: "1w", label: "1W" },
  { value: "1m", label: "1M" },
  { value: "3m", label: "3M" },
];
export const DEFAULT_WINDOW: EtfWindow = "1m";

export const windowLabel = (w: EtfWindow): string =>
  WINDOW_OPTIONS.find((o) => o.value === w)?.label ?? w;

// --- numbers as words ----------------------------------------------------------------------------------------

const MINUS = "−";

const sign = (v: number): string => (v > 0 ? "+" : v < 0 ? MINUS : "");

/** A signed percent ("+4.2%", "−1.0%"); "—" with no number. */
export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${sign(Number(v.toFixed(digits)))}${Math.abs(v).toFixed(digits)}%`;
}

/** A signed difference in percentage points ("+39.5 pts"). */
export function fmtPts(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${sign(Number(v.toFixed(1)))}${Math.abs(v).toFixed(1)} pts`;
}

/** Compact dollars ("$1.2B", "$340K"); unsigned. */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(a / 1e9).toFixed(a >= 1e10 ? 0 : 1)}B`;
  if (a >= 1e6) return `$${(a / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`;
  if (a >= 1e3) return `$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K`;
  return `$${a.toFixed(0)}`;
}

/** Signed compact dollars ("+$1.2M", "−$340K"); a true zero reads "$0". */
export function fmtSignedUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  const s = fmtUsd(v);
  return v === 0 || s === "$0" ? "$0" : `${sign(v)}${s}`;
}

export const fmtRatio = (v: number | null | undefined): string => (v == null ? "—" : `${v.toFixed(2)}×`);
export const fmtRange = (v: number | null | undefined): string => (v == null ? "—" : `${v.toFixed(0)}`);

/** A date with its year ("Oct 28, 2026"). */
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

export const plural = (n: number, one: string, many: string): string =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** A reading's text, or "—" (its note rides the title). */
export const readingTitle = (r: EtfReadingOut): string | undefined => r.note ?? undefined;

// --- panel 1: the comparison rows ----------------------------------------------------------------------------

export type CompareSortKey = "diff" | "name" | "ret" | "companies_ret";
export type CompareSort = { key: CompareSortKey; dir: "asc" | "desc" };

export const COMPARE_SORT_OPTIONS: readonly { value: CompareSortKey; label: string }[] = [
  { value: "diff", label: "Fund minus companies" },
  { value: "name", label: "Card A→Z" },
  { value: "ret", label: "Fund return" },
  { value: "companies_ret", label: "Companies return" },
];

export const DEFAULT_COMPARE_SORT: CompareSort = { key: "diff", dir: "desc" };

/** Picking the active sort again flips it; a new one starts A→Z for the name, highest first for a number. */
export function nextCompareSort(s: CompareSort, key: CompareSortKey): CompareSort {
  if (s.key === key) return { key, dir: s.dir === "asc" ? "desc" : "asc" };
  return { key, dir: key === "name" ? "asc" : "desc" };
}

const byName = (a: EtfCompareRowOut, b: EtfCompareRowOut) =>
  a.thesis_name.localeCompare(b.thesis_name, "en-US", { sensitivity: "base" }) ||
  a.ticker.localeCompare(b.ticker, "en-US", { sensitivity: "base" }) ||
  a.security_id.localeCompare(b.security_id);

/** The rows in the chosen order. A row without the number ("—") sorts last both ways; ties by card, then fund. */
export function sortCompare(rows: readonly EtfCompareRowOut[], s: CompareSort): EtfCompareRowOut[] {
  const out = [...rows];
  if (s.key === "name") {
    out.sort((a, b) => (s.dir === "asc" ? byName(a, b) : byName(b, a)));
    return out;
  }
  const key = s.key === "diff" ? "diff" : s.key === "ret" ? "ret" : "companies_ret";
  out.sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    if (va == null && vb == null) return byName(a, b);
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va !== vb) return s.dir === "desc" ? vb - va : va - vb;
    return byName(a, b);
  });
  return out;
}

// --- panel 4: the trend table --------------------------------------------------------------------------------

export type TrendSortKey = "ticker" | "ret_30d" | "vs_50d" | "vs_200d" | "rvol20" | "range_pos";
export type TrendSort = { key: TrendSortKey; dir: "asc" | "desc" };

export const DEFAULT_TREND_SORT: TrendSort = { key: "ticker", dir: "asc" };

export function nextTrendSort(s: TrendSort, key: TrendSortKey): TrendSort {
  if (s.key === key) return { key, dir: s.dir === "asc" ? "desc" : "asc" };
  return { key, dir: key === "ticker" ? "asc" : "desc" };
}

const byTicker = (a: EtfTrendRowOut, b: EtfTrendRowOut) =>
  a.ticker.localeCompare(b.ticker, "en-US", { sensitivity: "base" }) ||
  a.security_id.localeCompare(b.security_id);

/** Fund A→Z by default, or any column; a fund without the reading sorts last both ways. */
export function sortTrends(rows: readonly EtfTrendRowOut[], s: TrendSort): EtfTrendRowOut[] {
  const out = [...rows];
  if (s.key === "ticker") {
    out.sort((a, b) => (s.dir === "asc" ? byTicker(a, b) : byTicker(b, a)));
    return out;
  }
  const key = s.key;
  out.sort((a, b) => {
    const va = a[key].value;
    const vb = b[key].value;
    if (va == null && vb == null) return byTicker(a, b);
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va !== vb) return s.dir === "desc" ? vb - va : va - vb;
    return byTicker(a, b);
  });
  return out;
}

// --- geometry ------------------------------------------------------------------------------------------------

export type Domain = readonly [number, number];

/** The y range over every line drawn plus the 100 baseline, padded so a line never touches the edge. */
export function yDomain(lines: readonly (readonly number[] | null | undefined)[]): Domain {
  let lo = 100;
  let hi = 100;
  for (const line of lines) {
    for (const v of line ?? []) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  const pad = Math.max((hi - lo) * 0.06, 0.5);
  return [lo - pad, hi + pad];
}

export const xAt = (i: number, n: number, width: number): number =>
  n <= 1 ? width / 2 : (i / (n - 1)) * width;

export const yAt = (v: number, [lo, hi]: Domain, height: number): number =>
  height - ((v - lo) / (hi - lo)) * height;

/** An SVG path through the points, left to right across ``width``. */
export function linePath(points: readonly number[], domain: Domain, width: number, height: number): string {
  return points
    .map(
      (v, i) =>
        `${i === 0 ? "M" : "L"}${xAt(i, points.length, width).toFixed(1)},${yAt(v, domain, height).toFixed(1)}`,
    )
    .join(" ");
}

/** A bar's share of its track (0 to 1) on a LOG scale over the values on the page: the smallest positive
 *  value gets a sliver (`FLOOR`), the largest the whole track. A missing or non-positive value draws no bar. */
const FLOOR = 0.06;
export function logShare(v: number | null | undefined, all: readonly (number | null | undefined)[]): number | null {
  if (v == null || v <= 0) return null;
  const positives = all.filter((x): x is number => x != null && x > 0);
  if (positives.length === 0) return null;
  const lo = Math.log10(Math.min(...positives));
  const hi = Math.log10(Math.max(...positives));
  if (hi === lo) return 1;
  return FLOOR + (1 - FLOOR) * ((Math.log10(v) - lo) / (hi - lo));
}

/** A flow bar's half-width share (0 to 1) of the track: linear in the largest absolute 1M dollars on the page. */
export function flowShare(v: number | null | undefined, all: readonly (number | null | undefined)[]): number | null {
  if (v == null) return null;
  const max = Math.max(0, ...all.map((x) => Math.abs(x ?? 0)));
  return max === 0 ? 0 : Math.abs(v) / max;
}

/** An ISO date plus calendar days (UTC), as an ISO date. */
export function addDays(iso: string, days: number): string {
  const t = Date.parse(`${iso}T00:00:00Z`);
  return Number.isNaN(t) ? iso : new Date(t + days * 86_400_000).toISOString().slice(0, 10);
}

/** A date without its year ("Nov 11"). */
export function fmtShortDay(d: string): string {
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
