import type { AdoptionMonthOut, AdoptionOverlayOut } from "./useNarrativeAdoption";

// The Narrative Adoption page's pure layout (docs/dashboards/narrative-adoption.md): where every bar, line point and
// tick of the three stacked panels sits, from the page's payload. No chart library: the picture is plain SVG shapes
// at the positions below. All three panels share ONE x axis, one slot per calendar month (36 of them, the last the
// as-of's month), so a month reads straight down through the documents, the filers and the tape. Every number comes
// from the server; this file only places it.

/** The frame every panel shares: the plot runs from `left` to `w - right`. */
export const FRAME = { w: 960, left: 46, right: 10 } as const;
export const PLOT_W = FRAME.w - FRAME.left - FRAME.right;

export type Panel = { h: number; top: number; bottom: number };
export const DOC_PANEL: Panel = { h: 168, top: 10, bottom: 6 };
export const FILER_PANEL: Panel = { h: 128, top: 10, bottom: 6 };
export const INDEX_PANEL: Panel = { h: 156, top: 10, bottom: 22 };

/** The four form groups, bottom of the stack first. */
export const GROUPS = ["reports", "events", "issuance", "other"] as const;
export type Group = (typeof GROUPS)[number];

const r1 = (n: number) => Math.round(n * 10) / 10;

// --- the shared month axis -----------------------------------------------------------------------------------

export const slotWidth = (n: number): number => PLOT_W / Math.max(1, n);
/** The left edge of month slot `i` of `n`. */
export const slotX = (i: number, n: number): number => r1(FRAME.left + i * slotWidth(n));
/** The center of month slot `i` of `n`. */
export const slotCenter = (i: number, n: number): number => r1(FRAME.left + (i + 0.5) * slotWidth(n));
export const plotHeight = (p: Panel): number => p.h - p.top - p.bottom;
export const baseY = (p: Panel): number => p.h - p.bottom;

/** A y-axis maximum that is a round number at or above `v` (1, 1.5, 2, 3, 4, 5, 6, 8, 10 times a power of ten);
 *  1 when there is nothing above zero, so an empty panel never divides by zero. */
export function niceMax(v: number): number {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  const step = [1, 1.5, 2, 3, 4, 5, 6, 8, 10].find((s) => f <= s + 1e-9) ?? 10;
  return Math.round(step * p * 1e6) / 1e6;
}

/** The index of the month slot a date (YYYY-MM-DD) sits in, or -1. `axis` = the months' first days. */
export const monthIndexOf = (axis: readonly string[], iso: string): number => {
  const ym = iso.slice(0, 7);
  return axis.findIndex((m) => m.slice(0, 7) === ym);
};

const daysInMonth = (y: number, m: number): number => new Date(Date.UTC(y, m, 0)).getUTCDate();

/** The x of a session date: inside its month's slot, by the day of the month, so a daily line sits exactly over the
 *  monthly bars. `null` = the date is off the axis. */
export function xOfDate(axis: readonly string[], iso: string): number | null {
  const i = monthIndexOf(axis, iso);
  if (i < 0) return null;
  const dim = daysInMonth(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)));
  const day = Number(iso.slice(8, 10));
  return r1(slotX(i, axis.length) + slotWidth(axis.length) * ((day - 0.5) / dim));
}

export type AxisLabel = { x: number; text: string; anchor: "start" | "middle" | "end"; year: boolean };

/** The labels under the last panel: the first and last month in full, and each January's year in between (one
 *  that would touch an end label is skipped). Text is the month's first day; the view words it. */
export function axisLabels(axis: readonly string[]): AxisLabel[] {
  const n = axis.length;
  if (n === 0) return [];
  const out: AxisLabel[] = [{ x: slotX(0, n), text: axis[0], anchor: "start", year: false }];
  for (let i = 3; i < n - 3; i++) {
    if (axis[i].slice(5, 7) === "01") out.push({ x: slotX(i, n), text: axis[i], anchor: "middle", year: true });
  }
  if (n > 1) out.push({ x: r1(FRAME.left + PLOT_W), text: axis[n - 1], anchor: "end", year: false });
  return out;
}

/** The slots where a faint vertical rule marks a January (never the first slot). */
export const januarySlots = (axis: readonly string[]): number[] =>
  axis.flatMap((m, i) => (i > 0 && m.slice(5, 7) === "01" ? [i] : []));

// --- panel 1: documents, stacked by form group ---------------------------------------------------------------

type MonthNumbers = Pick<
  AdoptionMonthOut,
  "counted" | "documents" | "reports" | "events" | "issuance" | "other"
> &
  Partial<Pick<AdoptionMonthOut, "closed" | "read_early" | "terms_counted">>;

export type BarMode = "counts" | "share";
export type BarLayoutOptions = {
  /** Which form groups are stacked (default: all four). */
  groups?: ReadonlySet<Group>;
  /** "counts" = raw document counts; "share" = 0–100% composition per counted non-zero month. */
  mode?: BarMode;
  /** The card's SIGNAL term count; when provided, bars resting on fewer terms are marked partial. */
  signalTerms?: number;
};

export type Segment = { group: Group; y: number; h: number };
export type PlacedBar = {
  /** Its place in the axis (the cursor's month). */
  index: number;
  x: number;
  w: number;
  total: number;
  /** Top y of the highest drawn segment (or baseY when zero). */
  topY: number;
  /** Counted, and no document matched: a stored zero, drawn as a baseline tick (an absent month draws nothing). */
  zero: boolean;
  /** Counted before month close or resting on fewer than all SIGNAL terms. */
  partial: boolean;
  segments: Segment[];
};
export type UnclosedSlot = { index: number; x: number; w: number };
export type BarChart = {
  mode: BarMode;
  max: number;
  bars: PlacedBar[];
  unclosed: UnclosedSlot[];
  baseY: number;
  topY: number;
  ticks: { v: number; y: number }[];
};

/** The stacked bars: a bar per COUNTED month on one y scale (0 to a round maximum, or 0–100% in share mode),
 *  each active form group a segment from the baseline up in the order of GROUPS. An absent month (not counted)
 *  has no bar at all (an unclosed current month is noted as an outline slot), and a stored zero has a baseline tick only. */
export function layoutBars(
  months: readonly MonthNumbers[],
  panel: Panel = DOC_PANEL,
  options?: BarLayoutOptions,
): BarChart {
  const n = months.length;
  const ph = plotHeight(panel);
  const base = baseY(panel);
  const mode: BarMode = options?.mode ?? "counts";
  const active = options?.groups ? GROUPS.filter((g) => options.groups!.has(g)) : [...GROUPS];
  const allGroupsActive = active.length === GROUPS.length;
  const monthTotal = (m: MonthNumbers) =>
    allGroupsActive ? (m.documents ?? 0) : active.reduce((acc, g) => acc + (m[g] ?? 0), 0);
  const max = mode === "share" ? 100 : niceMax(Math.max(0, ...months.map((m) => (m.counted ? monthTotal(m) : 0))));
  const sw = slotWidth(n);
  const bars: PlacedBar[] = [];
  const unclosed: UnclosedSlot[] = [];
  months.forEach((m, index) => {
    const x = r1(slotX(index, n) + 1);
    const w = r1(Math.max(1.5, sw - 2));
    if (!m.counted) {
      if (m.closed === false) unclosed.push({ index, x, w });
      return;
    }
    const total = monthTotal(m);
    const denom = mode === "share" ? (total > 0 ? total : 1) : max;
    const segments: Segment[] = [];
    let cum = 0;
    let prevY = base;
    for (const group of active) {
      const v = m[group] ?? 0;
      cum += v;
      const frac = mode === "share" ? (total > 0 ? cum / denom : 0) : cum / max;
      const y = r1(base - frac * ph);
      if (v > 0) segments.push({ group, y, h: r1(prevY - y) });
      prevY = y;
    }
    const partial =
      Boolean(m.read_early) ||
      (options?.signalTerms != null && options.signalTerms > 0 && (m.terms_counted ?? options.signalTerms) < options.signalTerms);
    bars.push({
      index,
      x,
      w,
      total,
      topY: segments.length > 0 ? segments[segments.length - 1].y : base,
      zero: total === 0,
      partial,
      segments,
    });
  });
  const tickVals = mode === "share" ? [0, 50, 100] : [0, max / 2, max];
  return {
    mode,
    max,
    bars,
    unclosed,
    baseY: base,
    topY: panel.top,
    ticks: tickVals.map((v) => ({ v, y: r1(base - (v / max) * ph) })),
  };
}

// --- panel 2: filers and new filers --------------------------------------------------------------------------

type MonthFilers = Pick<AdoptionMonthOut, "counted" | "filers" | "filers_sampled" | "new_filers">;

export type LinePoint = { index: number; x: number; y: number; value: number; ring: boolean };
export type Line = { points: LinePoint[]; path: string };
export type FilerChart = {
  max: number;
  filers: Line;
  newFilers: Line;
  /** Every drawn month reads "at least": the ring would mark them all, so it is not drawn (it would say nothing). */
  allSampled: boolean;
  baseY: number;
  ticks: { v: number; y: number }[];
};

const pathOf = (points: readonly LinePoint[]): string =>
  points
    .map((p, i) => `${i === 0 || p.index !== points[i - 1].index + 1 ? "M" : "L"}${p.x} ${p.y}`)
    .join(" ");

/** The two filer lines on one y scale. Each is broken at a month with no value (never joined across a gap, never
 *  dropped to zero). A month whose filers read "at least" (any contributing term was sampled) is ringed on both
 *  lines, unless every drawn month is. */
export function layoutFilers(months: readonly MonthFilers[], panel: Panel = FILER_PANEL): FilerChart {
  const n = months.length;
  const ph = plotHeight(panel);
  const base = baseY(panel);
  const values = months.flatMap((m) => [m.filers, m.new_filers].filter((v): v is number => v != null));
  const max = niceMax(Math.max(0, ...values));
  const counted = months.filter((m) => m.counted && m.filers != null);
  const allSampled = counted.length > 0 && counted.every((m) => m.filers_sampled);
  const build = (pick: (m: MonthFilers) => number | null): Line => {
    const points: LinePoint[] = [];
    months.forEach((m, index) => {
      const v = pick(m);
      if (!m.counted || v == null) return;
      points.push({
        index,
        x: slotCenter(index, n),
        y: r1(base - (v / max) * ph),
        value: v,
        ring: m.filers_sampled && !allSampled,
      });
    });
    return { points, path: pathOf(points) };
  };
  return {
    max,
    filers: build((m) => m.filers),
    newFilers: build((m) => m.new_filers),
    allSampled,
    baseY: base,
    ticks: [0, max / 2, max].map((v) => ({ v, y: r1(base - (v / max) * ph) })),
  };
}

// --- panel 3: the card's line against SPY --------------------------------------------------------------------

export type Domain = readonly [number, number];

/** The y range over the lines drawn plus the 100 they start from, padded so a line never touches the edge. */
export function yDomain(lines: readonly (readonly number[] | null | undefined)[]): Domain {
  let lo = 100;
  let hi = 100;
  for (const line of lines)
    for (const v of line ?? []) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  const pad = Math.max((hi - lo) * 0.06, 0.5);
  return [lo - pad, hi + pad];
}

export const yAt = (v: number, [lo, hi]: Domain, panel: Panel): number =>
  r1(panel.top + ((hi - v) / (hi - lo)) * plotHeight(panel));

export type OverlayPoint = { x: number; y: number; value: number };
export type OverlayChart = {
  domain: Domain;
  card: string | null;
  spy: string | null;
  cardEnd: OverlayPoint | null;
  spyEnd: OverlayPoint | null;
  baseline: number;
  ticks: { v: number; y: number }[];
};

function pathOver(
  dates: readonly string[],
  values: readonly number[],
  axis: readonly string[],
  domain: Domain,
  panel: Panel,
): { path: string; end: OverlayPoint | null } {
  const pts: string[] = [];
  let end: OverlayPoint | null = null;
  dates.forEach((d, i) => {
    const x = xOfDate(axis, d);
    const v = values[i];
    if (x == null || v == null) return;
    const y = yAt(v, domain, panel);
    pts.push(`${pts.length === 0 ? "M" : "L"}${x} ${y}`);
    end = { x, y, value: v };
  });
  return { path: pts.join(" "), end };
}

/** The two lines of the overlay on the shared month axis (`axis` = the months' first days), both from 100 at the
 *  first session. A line the server did not send is `null` (it says why in words). */
export function layoutOverlay(
  overlay: Pick<AdoptionOverlayOut, "dates" | "card" | "spy">,
  axis: readonly string[],
  panel: Panel = INDEX_PANEL,
): OverlayChart {
  const domain = yDomain([overlay.card, overlay.spy]);
  const card = overlay.card && overlay.card.length > 1 ? pathOver(overlay.dates, overlay.card, axis, domain, panel) : null;
  const spy = overlay.spy && overlay.spy.length > 1 ? pathOver(overlay.dates, overlay.spy, axis, domain, panel) : null;
  return {
    domain,
    card: card ? card.path : null,
    spy: spy ? spy.path : null,
    cardEnd: card ? card.end : null,
    spyEnd: spy ? spy.end : null,
    baseline: yAt(100, domain, panel),
    ticks: apartTicks(domain, panel),
  };
}

/** The overlay's card and SPY coordinates at a given calendar month (its last session on or before month end, capped at asof). */
export function overlayPointAtMonth(
  overlay: Pick<AdoptionOverlayOut, "dates" | "card" | "spy">,
  axis: readonly string[],
  monthFirst: string,
  asof: string,
  domain: Domain,
  panel: Panel = INDEX_PANEL,
): { card: OverlayPoint | null; spy: OverlayPoint | null } {
  if (overlay.dates.length === 0) return { card: null, spy: null };
  const end = monthEnd(monthFirst) < asof ? monthEnd(monthFirst) : asof;
  const idx = lastOnOrBefore(overlay.dates, end);
  if (idx < 0) return { card: null, spy: null };
  const x = xOfDate(axis, overlay.dates[idx]);
  if (x == null) return { card: null, spy: null };
  const cardVal = overlay.card?.[idx];
  const spyVal = overlay.spy?.[idx];
  return {
    card: cardVal != null ? { x, y: yAt(cardVal, domain, panel), value: cardVal } : null,
    spy: spyVal != null ? { x, y: yAt(spyVal, domain, panel), value: spyVal } : null,
  };
}

/** The y ticks of the overlay: 100 always, then the top and the bottom of the scale, each dropped when its label
 *  would sit on top of one already kept (a line that starts at 100 and only rises has its bottom bound a hair
 *  below it). Ascending by value. */
export function apartTicks(domain: Domain, panel: Panel, minGap = 13): { v: number; y: number }[] {
  const kept: { v: number; y: number }[] = [];
  for (const v of [100, domain[1], domain[0]]) {
    const y = yAt(v, domain, panel);
    if (kept.every((k) => Math.abs(k.y - y) >= minGap)) kept.push({ v, y });
  }
  return kept.sort((a, b) => a.v - b.v);
}

/** The index of the last session on or before `iso` (YYYY-MM-DD) in `dates` (ascending), or -1. */
export function lastOnOrBefore(dates: readonly string[], iso: string): number {
  let lo = 0;
  let hi = dates.length - 1;
  let at = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (dates[mid] <= iso) {
      at = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return at;
}

/** The last day of the month whose first day is `monthFirst` (YYYY-MM-DD). */
export function monthEnd(monthFirst: string): string {
  const y = Number(monthFirst.slice(0, 4));
  const mo = Number(monthFirst.slice(5, 7));
  return `${monthFirst.slice(0, 8)}${String(daysInMonth(y, mo)).padStart(2, "0")}`;
}

// --- keyboard ------------------------------------------------------------------------------------------------

/** Steps the month cursor by `delta` (clamped to the axis); with no cursor yet it starts at `start`. */
export function stepMonth(current: number | null, n: number, delta: -1 | 1, start: number): number {
  if (n <= 0) return 0;
  if (current == null) return Math.max(0, Math.min(n - 1, start));
  return Math.max(0, Math.min(n - 1, current + delta));
}

/** The newest counted month on the axis (where the keyboard cursor starts); the last month when none is. */
export function lastCountedIndex(months: readonly { counted: boolean }[]): number {
  for (let i = months.length - 1; i >= 0; i--) if (months[i].counted) return i;
  return Math.max(0, months.length - 1);
}
