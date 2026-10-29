import type { IndexWindow, NarrativeCardOut } from "./useNarrativeIndex";

// The Narrative Index's pure view helpers: the window and sort options, the card order, the number words and
// the SVG geometry the mini and enlarged charts share. Every number is computed server-side; this file only
// orders, formats and draws it.

export const WINDOW_OPTIONS: readonly { value: IndexWindow; label: string }[] = [
  { value: "1y", label: "1Y" },
  { value: "90d", label: "90D" },
  { value: "30d", label: "30D" },
];

export const windowLabel = (w: IndexWindow): string =>
  WINDOW_OPTIONS.find((o) => o.value === w)?.label ?? w;

export type SortKey = "name" | "ret" | "vs_spy" | "since_created";
export type SortState = { key: SortKey; dir: "asc" | "desc" };

export const SORT_OPTIONS: readonly { value: SortKey; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "ret", label: "Window return" },
  { value: "vs_spy", label: "Return vs SPY" },
  { value: "since_created", label: "Since creation" },
];

export const DEFAULT_SORT: SortState = { key: "name", dir: "asc" };

/** Picking the active sort again flips it; a new one starts A→Z for the name, highest first for a number. */
export function nextSort(s: SortState, key: SortKey): SortState {
  if (s.key === key) return { key, dir: s.dir === "asc" ? "desc" : "asc" };
  return { key, dir: key === "name" ? "asc" : "desc" };
}

const byName = (a: NarrativeCardOut, b: NarrativeCardOut) =>
  a.thesis_name.localeCompare(b.thesis_name, "en-US", { sensitivity: "base" }) ||
  a.thesis_id.localeCompare(b.thesis_id);

/** The cards in the chosen order. A card without the number ("—") sorts last both ways; ties by name. */
export function sortCards(cards: readonly NarrativeCardOut[], s: SortState): NarrativeCardOut[] {
  const out = [...cards];
  if (s.key === "name") {
    out.sort((a, b) => (s.dir === "asc" ? byName(a, b) : byName(b, a)));
    return out;
  }
  const key = s.key;
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

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

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

/** The since-creation words for a card: its return, or why there is none. */
export function sinceWords(c: NarrativeCardOut): string {
  if (c.created_in_window) return `since created ${fmtPct(c.since_created)}`;
  if (c.created) return "created before the window";
  return "created —";
}

// --- the chart geometry --------------------------------------------------------------------------------------

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
export function linePath(
  points: readonly number[],
  domain: Domain,
  width: number,
  height: number,
  x0 = 0,
  y0 = 0,
): string {
  return points
    .map(
      (v, i) =>
        `${i === 0 ? "M" : "L"}${(x0 + xAt(i, points.length, width)).toFixed(1)},${(y0 + yAt(v, domain, height)).toFixed(1)}`,
    )
    .join(" ");
}

/** Where the creation marker sits: the index of the session "since creation" runs from, or null outside the
 *  window. */
export function createdIndex(dates: readonly string[], base: string | null | undefined): number | null {
  if (!base) return null;
  const i = dates.indexOf(base);
  return i < 0 ? null : i;
}
