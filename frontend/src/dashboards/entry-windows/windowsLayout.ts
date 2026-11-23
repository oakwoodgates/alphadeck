import type { EntryWindowsRowOut, EntryWindowsSparkOut } from "./useEntryWindows";

// The Entry Windows' geometry (docs/dashboards/entry-windows.md): PURE, so the picture is testable without a DOM.
// One date axis for the whole page; every row's picture is an SVG of the same width on that axis, so the as-of
// line, the arm nights and the windows line up down the page. A row draws, top to bottom: two thin stacked bars
// from the arm night (the entry window to `arm_until`, the hold horizon to `exit_by`), the catalyst ticks, and
// the sparkline of the armed names against IWM. Every number is the server's; this only places them.

export const PIC_W = 440;
export const PAD_L = 8;
export const PAD_R = 14;
export const BAR_H = 6;
export const ENTRY_Y = 3;
export const HOLD_Y = 11;
export const TICK_Y = 25;
export const TICK_R = 3;
export const SPARK_TOP = 33;
export const SPARK_H = 15;
export const ROW_H = 52;
export const AXIS_H = 32;

const DAY_MS = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Days since 1970-01-01 of a YYYY-MM-DD date (UTC, so no clock or zone is involved). */
export const dayNumber = (iso: string): number => {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
};

const isoOf = (n: number): string => new Date(n * DAY_MS).toISOString().slice(0, 10);

// --- the axis ----------------------------------------------------------------------------------------------------

export type Axis = {
  /** First and last day number of the axis. */
  start: number;
  end: number;
  width: number;
  /** The x of a date, clamped to the axis. */
  x: (iso: string) => number;
};

export function makeAxis(startIso: string, endIso: string, width = PIC_W): Axis {
  const start = dayNumber(startIso);
  const end = Math.max(dayNumber(endIso), start + 1);
  const inner = width - PAD_L - PAD_R;
  return {
    start,
    end,
    width,
    x: (iso) => {
      const t = (dayNumber(iso) - start) / (end - start);
      return PAD_L + Math.min(Math.max(t, 0), 1) * inner;
    },
  };
}

/** Date bounds covering ONLY the currently visible rows plus `asof`, so hidden Managing cards armed months ago
 *  do not stretch the axis and crush short Armed entry windows. Falls back to the server bounds when empty. */
export function visibleAxisBounds(
  rows: readonly EntryWindowsRowOut[],
  asof: string,
  fallbackStart: string | null,
  fallbackEnd: string | null,
): { start: string; end: string } | null {
  if (rows.length === 0) {
    return fallbackStart && fallbackEnd ? { start: fallbackStart, end: fallbackEnd } : null;
  }
  let lo = asof;
  let hi = asof;
  const take = (iso: string | null | undefined) => {
    if (!iso) return;
    if (iso < lo) lo = iso;
    if (iso > hi) hi = iso;
  };
  for (const r of rows) {
    take(r.arm_night);
    take(r.arm_until);
    take(r.exit_by);
    for (const c of r.catalysts) take(c.when_date);
    for (const mc of r.member_clocks ?? []) {
      take(mc.arm_until);
    }
    if (r.spark && r.spark.dates.length > 0) {
      take(r.spark.dates[0]);
      take(r.spark.dates[r.spark.dates.length - 1]);
    }
  }
  return { start: lo, end: hi };
}

/** Thin axis labels so adjacent labels never collide when a span has many monthly ticks. */
export function spacedAxisTicks(axis: Axis, minGapPx = 42): AxisTick[] {
  const raw = axisTicks(axis);
  const out: AxisTick[] = [];
  let lastX = -Infinity;
  for (const t of raw) {
    if (t.x - lastX >= minGapPx) {
      out.push(t);
      lastX = t.x;
    }
  }
  return out;
}

export type AxisTick = { iso: string; x: number; label: string };

/** The date labels along the axis: Mondays for a span up to ten weeks, month starts to about a year, quarter starts
 *  beyond. */
export function axisTicks(axis: Axis): AxisTick[] {
  const span = axis.end - axis.start;
  const out: AxisTick[] = [];
  for (let n = axis.start; n <= axis.end; n++) {
    const dt = new Date(n * DAY_MS);
    const dow = dt.getUTCDay();
    const dom = dt.getUTCDate();
    const mon = dt.getUTCMonth();
    const hit = span <= 70 ? dow === 1 : span <= 400 ? dom === 1 : dom === 1 && mon % 3 === 0;
    if (!hit) continue;
    const iso = isoOf(n);
    out.push({ iso, x: axis.x(iso), label: `${MONTHS[mon]} ${dom}` });
  }
  return out;
}

// --- a row -------------------------------------------------------------------------------------------------------

export type Bar = { x: number; w: number };
export type PlacedTick = {
  x: number;
  /** Slight vertical offset when two catalysts fall within a few pixels of each other. */
  dy?: number;
  /** The catalyst's date is before the as-of: drawn hollow. */
  past: boolean;
  label: string;
  when: string;
  kind: string | null;
};
export type MemberClockTick = {
  x: number;
  ticker: string;
  until: string;
  closing: boolean;
};
export type PlacedSpark = {
  line: string;
  iwm: string | null;
  /** The y of the 100 base line, only when it falls inside the drawn range. */
  base: number | null;
  endX: number;
  endY: number;
  endPct: number;
};
export type RowLayout = {
  key: string;
  armX: number;
  asofX: number;
  /** From the arm night to `arm_until`; `null` = no entry clock (or one dated before the arm night). */
  entry: Bar | null;
  /** From the arm night to `exit_by`; `null` = no hold clock (or one dated before the arm night). */
  hold: Bar | null;
  ticks: PlacedTick[];
  memberTicks: MemberClockTick[];
  spark: PlacedSpark | null;
};

function bar(from: string, to: string | null, axis: Axis): Bar | null {
  if (to == null || dayNumber(to) < dayNumber(from)) return null;
  const x = axis.x(from);
  return { x, w: Math.max(axis.x(to) - x, 1) };
}

function path(points: readonly (readonly [number, number])[]): string {
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
}

function placeSpark(spark: EntryWindowsSparkOut, axis: Axis): PlacedSpark | null {
  if (spark.dates.length < 2 || spark.line.length !== spark.dates.length) return null;
  const all = [...spark.line, ...(spark.iwm ?? [])];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const y = (v: number) =>
    hi === lo ? SPARK_TOP + SPARK_H / 2 : SPARK_TOP + SPARK_H - ((v - lo) / (hi - lo)) * SPARK_H;
  const xs = spark.dates.map((d) => axis.x(d));
  const draw = (levels: readonly number[]) => path(levels.map((v, i) => [xs[i], y(v)] as const));
  const lastIdx = spark.line.length - 1;
  return {
    line: draw(spark.line),
    iwm: spark.iwm && spark.iwm.length === spark.dates.length ? draw(spark.iwm) : null,
    base: lo <= 100 && 100 <= hi && hi !== lo ? y(100) : null,
    endX: xs[lastIdx],
    endY: y(spark.line[lastIdx]),
    endPct: spark.line[lastIdx] - 100,
  };
}

export function layoutRow(row: EntryWindowsRowOut, axis: Axis, asof: string): RowLayout {
  const placedXs: number[] = [];
  const ticks: PlacedTick[] = row.catalysts.map((c) => {
    const x = axis.x(c.when_date);
    const crowded = placedXs.filter((px) => Math.abs(px - x) < 7).length;
    placedXs.push(x);
    const dy = crowded === 0 ? 0 : crowded % 2 === 1 ? -3 : 3;
    return {
      x,
      ...(dy !== 0 ? { dy } : {}),
      past: c.past,
      label: c.label,
      when: c.when_date,
      kind: c.kind,
    };
  });
  const memberTicks: MemberClockTick[] = (row.member_clocks ?? [])
    .filter((mc) => mc.arm_until != null && mc.arm_until !== row.arm_until)
    .map((mc) => ({
      x: axis.x(mc.arm_until!),
      ticker: mc.ticker ?? "?",
      until: mc.arm_until!,
      closing: mc.entry_window_closing,
    }));
  return {
    key: row.thesis_id,
    armX: axis.x(row.arm_night),
    asofX: axis.x(asof),
    entry: bar(row.arm_night, row.arm_until, axis),
    hold: bar(row.arm_night, row.exit_by, axis),
    ticks,
    memberTicks,
    spark: row.spark ? placeSpark(row.spark, axis) : null,
  };
}

// --- keyboard ----------------------------------------------------------------------------------------------------

export type RowDir = "up" | "down" | "home" | "end";

/** The row key a key press moves to (the list is one tab stop, the arrow keys step through it); the ends hold. */
export function moveRow(keys: readonly string[], current: string, dir: RowDir): string {
  if (keys.length === 0) return current;
  const i = keys.indexOf(current);
  if (dir === "home") return keys[0];
  if (dir === "end") return keys[keys.length - 1];
  if (i < 0) return keys[0];
  return keys[Math.min(Math.max(i + (dir === "down" ? 1 : -1), 0), keys.length - 1)];
}
