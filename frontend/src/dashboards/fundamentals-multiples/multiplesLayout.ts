import type {
  MultiplesBarOut,
  MultiplesCompanyOut,
  MultiplesMedianOut,
  MultiplesRowOut,
} from "./useFundamentalsMultiples";

// The Fundamentals Multiples' pure layout (docs/dashboards/fundamentals-multiples.md): where every bar, dot and
// median point of one small chart sits, from the page's payload and the card's shared axis. No chart library: the
// picture is plain SVG shapes at the positions below. Every chart is the same size and shares one x axis (one slot
// per calendar quarter), so a column of charts reads straight down; the dots share one fixed scale (-100% to
// +100%, a dot past it is pinned to the edge and marked as off the scale) so a name's dots compare with another's
// and with the median line. Bars are on each name's OWN scale (the top value is written above them).

export const CELL_W = 160;
export const CELL_H = 104;
export const PAD_X = 6;
/** The dot scale: percent change from -CAP to +CAP, a dot past it pinned to the edge and ringed. */
export const YOY_CAP = 100;
/** A name chart: the dot band (zero line at DOT_MID, dots within DOT_HALF of it), then the bars up from BASE_Y. */
export const DOT_MID = 20;
export const DOT_HALF = 14;
export const BARS_TOP = 42;
export const BASE_Y = 88;
export const LABEL_Y = 99;
/** A median chart: one dot band over the whole cell. */
export const MED_MID = 48;
export const MED_HALF = 34;

export type Tone = "pos" | "neg" | "flat";
export type Anchor = "start" | "end";
export type ChartLabel = { x: number; y: number; text: string; anchor: Anchor };

export type PlacedDot = {
  cx: number;
  cy: number;
  tone: Tone;
  /** Past the dot scale (above +100% or below -100%): pinned to the edge, drawn as a ring. */
  clipped: boolean;
  pct: number;
};

export type PlacedBar = {
  /** Its place in the company's `bars` (the keyboard's cursor). */
  index: number;
  /** The axis slot it sits in. */
  slot: number;
  bar: MultiplesBarOut;
  x: number;
  y: number;
  w: number;
  h: number;
  /** A derived quarter (the fiscal year minus nine months) is hatched; a native one is solid. */
  derived: boolean;
  /** The column the pointer hits: the bar's slot, full height. */
  hit: { x: number; w: number };
  dot: PlacedDot | null;
};

export type NameChart = {
  width: number;
  height: number;
  zeroY: number;
  baseY: number;
  bars: PlacedBar[];
  /** The top bar value, written above the bars (`null` = no bars). */
  top: { value: number; x: number; y: number } | null;
  /** The first and last quarter of the axis, under the bars (`null` = no axis). */
  labels: ChartLabel[];
};

export type PlacedPoint = PlacedDot & {
  /** Its place in the median's `points` (the keyboard's cursor is over the DRAWN ones only: see `index`). */
  index: number;
  quarter: string;
  n: number;
};

export type MedianChart = {
  width: number;
  height: number;
  zeroY: number;
  topY: number;
  bottomY: number;
  /** The drawn points only (a quarter with no median has none). */
  points: PlacedPoint[];
  /** One polyline per run of consecutive quarters with two or more drawn points. */
  paths: string[];
  labels: ChartLabel[];
};

// --- small pure helpers --------------------------------------------------------------------------------------

const r1 = (n: number) => Math.round(n * 10) / 10;

/** The tone of a change: its sign, the only color on the page. Zero (as it reads: 0.0%) is neutral. */
export function toneOf(pct: number): Tone {
  if (Math.abs(pct) < 0.05) return "flat";
  return pct > 0 ? "pos" : "neg";
}

/** The y of a percent change on a dot band centered on `mid` with `half` pixels each way; a change past
 *  +/-YOY_CAP is pinned to the edge and `clipped`. */
export function yoyY(pct: number, mid: number, half: number): { y: number; clipped: boolean } {
  const clipped = Math.abs(pct) > YOY_CAP;
  const c = Math.max(-YOY_CAP, Math.min(YOY_CAP, pct));
  return { y: r1(mid - (c / YOY_CAP) * half), clipped };
}

/** The width of one axis slot. */
export const slotWidth = (slots: number): number => (CELL_W - 2 * PAD_X) / Math.max(1, slots);

/** The x of the center of axis slot `i` of `slots`. */
export const slotCenter = (i: number, slots: number): number => r1(PAD_X + (i + 0.5) * slotWidth(slots));

function axisLabels(axis: readonly string[], y: number): ChartLabel[] {
  if (axis.length === 0) return [];
  const first: ChartLabel = { x: PAD_X, y, text: axis[0], anchor: "start" };
  if (axis.length === 1) return [first];
  return [first, { x: CELL_W - PAD_X, y, text: axis[axis.length - 1], anchor: "end" }];
}

function dotOf(pct: number, cx: number, mid: number, half: number): PlacedDot {
  const { y, clipped } = yoyY(pct, mid, half);
  return { cx, cy: y, tone: toneOf(pct), clipped, pct };
}

// --- one name -----------------------------------------------------------------------------------------------

/** A name's chart: its bars on its own scale up from the baseline (two bars in one calendar quarter share the
 *  slot, side by side), a dot above each bar that has a change on the year before, the top value and the axis'
 *  first and last quarter. A name with no bars is an empty frame (`bars` empty, `top` null). */
export function layoutName(c: Pick<MultiplesCompanyOut, "bars">, axis: readonly string[]): NameChart {
  const slots = axis.length;
  const sw = slotWidth(slots);
  const barsH = BASE_Y - BARS_TOP;
  const max = Math.max(0, ...c.bars.map((b) => b.value));
  const scale = max > 0 ? max : 1;
  const inSlot = new Map<number, number>();
  const slotOf = c.bars.map((b) => axis.indexOf(b.quarter));
  slotOf.forEach((s) => inSlot.set(s, (inSlot.get(s) ?? 0) + 1));
  const seen = new Map<number, number>();
  const bars: PlacedBar[] = [];
  c.bars.forEach((bar, index) => {
    const slot = slotOf[index];
    if (slot < 0) return; // the server draws only bars on the axis; a stray one has no place
    const k = seen.get(slot) ?? 0;
    seen.set(slot, k + 1);
    const n = inSlot.get(slot) ?? 1;
    const inner = Math.max(2, sw - 2) / n;
    const slotX = PAD_X + slot * sw;
    const x = r1(slotX + 1 + k * inner);
    const w = r1(Math.max(1.5, inner - (n > 1 ? 0.5 : 0)));
    const h = bar.value > 0 ? r1(Math.max(1, (bar.value / scale) * barsH)) : 0;
    bars.push({
      index,
      slot,
      bar,
      x,
      y: r1(BASE_Y - h),
      w,
      h,
      derived: bar.basis === "derived",
      hit: { x: r1(slotX + k * (sw / n)), w: r1(sw / n) },
      dot: bar.yoy != null ? dotOf(bar.yoy, r1(x + w / 2), DOT_MID, DOT_HALF) : null,
    });
  });
  return {
    width: CELL_W,
    height: CELL_H,
    zeroY: DOT_MID,
    baseY: BASE_Y,
    bars,
    top: bars.length > 0 && max > 0 ? { value: max, x: PAD_X, y: BARS_TOP - 3 } : null,
    labels: axisLabels(axis, LABEL_Y),
  };
}

// --- a median line ------------------------------------------------------------------------------------------

/** A median line's chart: one dot per quarter that has a median, joined by a thin line across runs of consecutive
 *  quarters (a quarter with no median breaks the line), on the same scale as the name charts' dots. */
export function layoutMedian(m: Pick<MultiplesMedianOut, "points">, axis: readonly string[]): MedianChart {
  const slots = axis.length;
  const points: PlacedPoint[] = [];
  m.points.forEach((p, i) => {
    if (p.median == null) return;
    const slot = axis.indexOf(p.quarter);
    if (slot < 0) return;
    points.push({ ...dotOf(p.median, slotCenter(slot, slots), MED_MID, MED_HALF), index: i, quarter: p.quarter, n: p.n });
  });
  const paths: string[] = [];
  let run: PlacedPoint[] = [];
  const flush = () => {
    if (run.length >= 2) paths.push(run.map((p, i) => `${i === 0 ? "M" : "L"}${p.cx} ${p.cy}`).join(" "));
    run = [];
  };
  for (const p of points) {
    if (run.length > 0 && p.index !== run[run.length - 1].index + 1) flush();
    run.push(p);
  }
  flush();
  return {
    width: CELL_W,
    height: CELL_H,
    zeroY: MED_MID,
    topY: MED_MID - MED_HALF,
    bottomY: MED_MID + MED_HALF,
    points,
    paths,
    labels: axisLabels(axis, LABEL_Y),
  };
}

// --- keyboard focus ------------------------------------------------------------------------------------------

export type Dir = "up" | "down" | "left" | "right";
export type Group = { id: string; keys: string[] };

/** The key of a median cell (`card` = the card line). */
export const medianKey = (rowKey: string): string => `m|${rowKey}`;
/** The key of a name's chart in a row (a company in two links has two charts, so the row is in the key). */
export const nameKey = (rowKey: string, companyKey: string): string => `n|${rowKey}|${companyKey}`;

/** A row has a median cell when it is over at least one counting name (Context and Bench have none). */
export const hasMedian = (row: Pick<MultiplesRowOut, "median">): boolean => row.median != null && row.median.counting > 0;

/** The keyboard groups in reading order: the card line, then each shown row (its median cell first, then its names). */
export function groupsFor(rows: readonly MultiplesRowOut[], showSilent: boolean): Group[] {
  const groups: Group[] = [{ id: "card", keys: [medianKey("card")] }];
  for (const row of rows) {
    if (row.kind === "silent" && !showSilent) continue;
    const keys = [...(hasMedian(row) ? [medianKey(row.key)] : []), ...row.companies.map((ck) => nameKey(row.key, ck))];
    if (keys.length > 0) groups.push({ id: row.key, keys });
  }
  return groups;
}

/** Where an arrow key moves the focus: left and right step to the previous or next chart in reading order (across
 *  rows), up and down to the first chart of the previous or next row. It stays put at an end; a key the groups do
 *  not hold falls back to the first chart. */
export function moveFocus(groups: readonly Group[], current: string, dir: Dir): string {
  const flat = groups.flatMap((g) => g.keys);
  if (flat.length === 0) return current;
  const at = flat.indexOf(current);
  if (at < 0) return flat[0];
  if (dir === "left") return flat[Math.max(0, at - 1)];
  if (dir === "right") return flat[Math.min(flat.length - 1, at + 1)];
  const gi = groups.findIndex((g) => g.keys.includes(current));
  const next = groups[dir === "up" ? Math.max(0, gi - 1) : Math.min(groups.length - 1, gi + 1)];
  return next.keys[0];
}

/** Steps a chart's item cursor (a bar or a drawn median point) by `delta`, clamped to the items; no cursor yet
 *  starts at the newest. `null` when there are no items. */
export function stepItem(current: number | null, count: number, delta: -1 | 1): number | null {
  if (count <= 0) return null;
  if (current == null) return count - 1;
  return Math.max(0, Math.min(count - 1, current + delta));
}
