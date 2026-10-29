import { describe, expect, it } from "vitest";

import {
  BARS_TOP,
  BASE_Y,
  CELL_W,
  DOT_HALF,
  DOT_MID,
  LABEL_Y,
  MED_HALF,
  MED_MID,
  PAD_X,
  groupsFor,
  hasMedian,
  layoutMedian,
  layoutName,
  medianKey,
  moveFocus,
  nameKey,
  slotCenter,
  slotWidth,
  stepItem,
  toneOf,
  yoyY,
} from "../multiplesLayout";
import { AAA, AXIS, BBB, DDD, EEE, OLD, ROWS, UNR, bar, median } from "./fixtures";

// The pure geometry of one small chart: where every bar, dot and median point sits, the shared fixed dot scale,
// the keyboard order. No DOM.

describe("the dot scale", () => {
  it("tones a change by its sign, zero (as it reads) neutral", () => {
    expect([toneOf(20), toneOf(-3), toneOf(0), toneOf(0.04), toneOf(-0.04), toneOf(0.2)]).toEqual([
      "pos",
      "neg",
      "flat",
      "flat",
      "flat",
      "pos",
    ]);
  });

  it("puts 0% on the zero line, +/-100% on the band's edges and anything past it on the edge, marked off the scale", () => {
    expect(yoyY(0, DOT_MID, DOT_HALF)).toEqual({ y: 20, clipped: false });
    expect(yoyY(100, DOT_MID, DOT_HALF)).toEqual({ y: 6, clipped: false });
    expect(yoyY(-100, DOT_MID, DOT_HALF)).toEqual({ y: 34, clipped: false });
    expect(yoyY(50, DOT_MID, DOT_HALF)).toEqual({ y: 13, clipped: false });
    expect(yoyY(250, DOT_MID, DOT_HALF)).toEqual({ y: 6, clipped: true });
    expect(yoyY(-100.5, DOT_MID, DOT_HALF)).toEqual({ y: 34, clipped: true });
    // a median chart uses the same scale over a taller band
    expect(yoyY(100, MED_MID, MED_HALF).y).toBe(MED_MID - MED_HALF);
  });

  it("gives every axis slot the same width and centers a mark in its slot", () => {
    expect(slotWidth(12)).toBeCloseTo(148 / 12, 6);
    expect(slotCenter(0, 12)).toBeCloseTo(PAD_X + 148 / 24, 1);
    expect(slotCenter(11, 12)).toBeCloseTo(CELL_W - PAD_X - 148 / 24, 1);
    expect(slotCenter(0, 1)).toBeCloseTo(CELL_W / 2, 1);
    expect(slotWidth(0)).toBe(148); // an empty axis never divides by zero
  });
});

describe("layoutName", () => {
  const chart = layoutName(AAA, AXIS);

  it("draws one bar per print, in axis order, inside the plot and never overlapping", () => {
    expect(chart.bars).toHaveLength(12);
    expect(chart.bars.map((b) => b.slot)).toEqual(AXIS.map((_, i) => i));
    expect(chart.bars.map((b) => b.index)).toEqual(AXIS.map((_, i) => i));
    for (let i = 0; i < chart.bars.length; i++) {
      const b = chart.bars[i];
      expect(b.x).toBeGreaterThanOrEqual(PAD_X);
      expect(b.x + b.w).toBeLessThanOrEqual(CELL_W - PAD_X + 0.01);
      expect(b.w).toBeGreaterThan(0);
      if (i > 0) expect(chart.bars[i - 1].x + chart.bars[i - 1].w).toBeLessThanOrEqual(b.x + 0.01);
    }
  });

  it("scales the bars to the name's own top value, up from the baseline", () => {
    // AAA's values run 100 .. 210: the newest bar is the full plot height, the first is 100/210 of it
    const plot = BASE_Y - BARS_TOP;
    const last = chart.bars[11];
    expect(last.h).toBeCloseTo(plot, 1);
    expect(last.y + last.h).toBeCloseTo(BASE_Y, 1);
    expect(chart.bars[0].h).toBeCloseTo((100 / 210) * plot, 1);
    expect(chart.top).toEqual({ value: 210, x: PAD_X, y: BARS_TOP - 3 });
    // another name has its own scale: BBB's biggest bar is also the full height
    const b = layoutName(BBB, AXIS);
    expect(Math.max(...b.bars.map((x) => x.h))).toBeCloseTo(plot, 1);
    expect(b.top?.value).toBe(75);
  });

  it("hatches a derived quarter and draws a native one solid", () => {
    expect(chart.bars.map((b) => b.derived)).toEqual(AXIS.map((_, i) => i === 1));
  });

  it("puts a dot over each bar that has a change, centered on it, toned by sign, pinned and ringed past the scale", () => {
    const dotted = chart.bars.filter((b) => b.dot);
    expect(dotted).toHaveLength(10); // the first two have no change
    for (const b of dotted) expect(b.dot?.cx).toBeCloseTo(b.x + b.w / 2, 0);
    expect(chart.bars[0].dot).toBeNull();
    expect(chart.bars[4].dot).toMatchObject({ tone: "neg", clipped: false, pct: -30 });
    expect(chart.bars[5].dot).toMatchObject({ tone: "pos", clipped: false });
    expect(chart.bars[5].dot?.cy).toBeCloseTo(DOT_MID - (20 / 100) * DOT_HALF, 1);
    expect(chart.bars[11].dot).toMatchObject({ tone: "pos", clipped: true, cy: DOT_MID - DOT_HALF });
    expect(chart.zeroY).toBe(DOT_MID);
  });

  it("labels the first and last quarter of the axis under the bars", () => {
    expect(chart.labels).toEqual([
      { x: PAD_X, y: LABEL_Y, text: "2023-Q3", anchor: "start" },
      { x: CELL_W - PAD_X, y: LABEL_Y, text: "2026-Q2", anchor: "end" },
    ]);
    expect(layoutName({ bars: [] }, ["2026-Q2"]).labels).toHaveLength(1);
    expect(layoutName({ bars: [] }, []).labels).toEqual([]);
  });

  it("leaves a quarter with no print an empty slot", () => {
    const b = layoutName(BBB, AXIS);
    expect(b.bars.map((x) => x.slot)).toEqual([6, 7, 10, 11]);
  });

  it("makes a name with no bars an empty frame, with no top value", () => {
    for (const c of [UNR, EEE, OLD]) {
      const e = layoutName(c, AXIS);
      expect(e.bars).toEqual([]);
      expect(e.top).toBeNull();
      expect(e.width).toBe(CELL_W);
    }
  });

  it("draws a zero or negative print as no bar height, and an all-zero name without dividing by zero", () => {
    const z = layoutName({ bars: [bar("2026-Q1", 0), bar("2026-Q2", -5)] }, AXIS);
    expect(z.bars.map((b) => b.h)).toEqual([0, 0]);
    expect(z.top).toBeNull();
    expect(z.bars.every((b) => Number.isFinite(b.y))).toBe(true);
    // a tiny positive print is still at least one pixel
    const tiny = layoutName({ bars: [bar("2026-Q1", 1), bar("2026-Q2", 1_000_000)] }, AXIS);
    expect(tiny.bars[0].h).toBe(1);
  });

  it("puts two prints of one calendar quarter side by side in the slot", () => {
    const two = layoutName({ bars: [bar("2026-Q1", 10), bar("2026-Q1", 20, { period_end: "2026-03-28" })] }, AXIS);
    expect(two.bars.map((b) => b.slot)).toEqual([10, 10]);
    const [a, b] = two.bars;
    expect(a.x + a.w).toBeLessThanOrEqual(b.x + 0.01);
    expect(a.w).toBeLessThan(slotWidth(12) / 2 + 0.01);
    expect(a.hit.x + a.hit.w).toBeCloseTo(b.hit.x, 0);
  });

  it("does not place a print whose quarter is not on the axis", () => {
    const stray = layoutName({ bars: [bar("2019-Q1", 10), bar("2026-Q2", 20)] }, AXIS);
    expect(stray.bars).toHaveLength(1);
    expect(stray.bars[0].index).toBe(1); // the cursor still indexes the company's own bars
  });
});

describe("layoutMedian", () => {
  it("draws a dot for each quarter that has a median and joins a run of consecutive ones with a line", () => {
    const m = layoutMedian(median(3, { "2025-Q4": [20, 2], "2026-Q1": [30, 2], "2026-Q2": [50, 3] }), AXIS);
    expect(m.points.map((p) => p.quarter)).toEqual(["2025-Q4", "2026-Q1", "2026-Q2"]);
    expect(m.points.map((p) => p.index)).toEqual([9, 10, 11]);
    expect(m.points.map((p) => p.n)).toEqual([2, 2, 3]);
    expect(m.paths).toHaveLength(1);
    expect(m.paths[0].startsWith("M")).toBe(true);
    expect((m.paths[0].match(/L/g) ?? []).length).toBe(2);
    expect(m.points[1].cy).toBeCloseTo(MED_MID - 0.3 * MED_HALF, 1);
    expect(m.points[2].cy).toBeCloseTo(MED_MID - 0.5 * MED_HALF, 1);
    expect(m.points[0].cx).toBeCloseTo(slotCenter(9, 12), 1);
    expect(m.points.every((p) => p.tone === "pos" && !p.clipped)).toBe(true);
  });

  it("breaks the line at a quarter with no median and draws a lone point as a dot only", () => {
    const m = layoutMedian(median(2, { "2025-Q2": [10, 2], "2025-Q4": [-10, 2], "2026-Q1": [-20, 2] }), AXIS);
    expect(m.points).toHaveLength(3);
    expect(m.paths).toHaveLength(1); // only the Q4 -> Q1 run has two points
    expect(m.points[0].tone).toBe("pos");
    expect(m.points[1].tone).toBe("neg");
    const lone = layoutMedian(median(2, { "2026-Q2": [10, 2] }), AXIS);
    expect(lone.points).toHaveLength(1);
    expect(lone.paths).toEqual([]);
  });

  it("pins a median past the scale to the edge and rings it; a median line over nothing draws nothing", () => {
    const m = layoutMedian(median(2, { "2026-Q2": [400, 2] }), AXIS);
    expect(m.points[0]).toMatchObject({ clipped: true, cy: MED_MID - MED_HALF });
    expect(m.topY).toBe(MED_MID - MED_HALF);
    expect(m.bottomY).toBe(MED_MID + MED_HALF);
    expect(layoutMedian(median(0), AXIS).points).toEqual([]);
    expect(layoutMedian({ points: [] }, []).points).toEqual([]);
  });
});

describe("the keyboard", () => {
  it("lists the card line, then each shown row's median cell and names; Context and Bench only when shown", () => {
    const groups = groupsFor(ROWS, false);
    expect(groups.map((g) => g.id)).toEqual(["card", "link:Turbines", "link:Cables", "old:Old wires"]);
    expect(groups[0].keys).toEqual([medianKey("card")]);
    expect(groups[1].keys).toEqual([
      medianKey("link:Turbines"),
      nameKey("link:Turbines", "s-AAA"),
      nameKey("link:Turbines", "s-BBB"),
      nameKey("link:Turbines", "ticker:UNR"),
    ]);
    // a company in two links is a chart in each, so the row is in the key
    expect(groups[2].keys).toContain(nameKey("link:Cables", "s-AAA"));
    // an empty link has no cell at all
    expect(groups.some((g) => g.id === "link:Empty link")).toBe(false);
    const all = groupsFor(ROWS, true);
    expect(all[all.length - 1]).toEqual({ id: "silent:Context", keys: [nameKey("silent:Context", "s-DDD")] });
    expect(hasMedian(ROWS[0])).toBe(true);
    expect(hasMedian(ROWS[2])).toBe(false); // counting 0
    expect(hasMedian(ROWS[4])).toBe(false); // Context: no median
    expect(DDD.silent).toBe(true);
  });

  const groups = groupsFor(ROWS, false);
  const first = medianKey("card");
  const turbines = groups[1].keys;

  it("steps left and right through the charts in reading order, across rows, and stays put at the ends", () => {
    expect(moveFocus(groups, first, "right")).toBe(turbines[0]);
    expect(moveFocus(groups, turbines[0], "right")).toBe(turbines[1]);
    expect(moveFocus(groups, turbines[turbines.length - 1], "right")).toBe(groups[2].keys[0]);
    expect(moveFocus(groups, groups[2].keys[0], "left")).toBe(turbines[turbines.length - 1]);
    expect(moveFocus(groups, first, "left")).toBe(first);
    const lastKeys = groups[groups.length - 1].keys;
    const end = lastKeys[lastKeys.length - 1];
    expect(moveFocus(groups, end, "right")).toBe(end);
  });

  it("moves up and down to the first chart of the neighboring row, and stays put at the top and the bottom", () => {
    expect(moveFocus(groups, turbines[2], "down")).toBe(groups[2].keys[0]);
    expect(moveFocus(groups, groups[2].keys[1], "up")).toBe(turbines[0]);
    expect(moveFocus(groups, turbines[1], "up")).toBe(first);
    expect(moveFocus(groups, first, "up")).toBe(first);
    expect(moveFocus(groups, groups[groups.length - 1].keys[0], "down")).toBe(groups[groups.length - 1].keys[0]);
  });

  it("falls back to the first chart for a key it does not hold, and to the key itself with nothing to move to", () => {
    expect(moveFocus(groups, "gone", "right")).toBe(first);
    expect(moveFocus([], "x", "left")).toBe("x");
  });

  it("steps a chart's item cursor, clamped, starting at the newest", () => {
    expect(stepItem(null, 12, -1)).toBe(11);
    expect(stepItem(null, 12, 1)).toBe(11);
    expect(stepItem(5, 12, -1)).toBe(4);
    expect(stepItem(0, 12, -1)).toBe(0);
    expect(stepItem(11, 12, 1)).toBe(11);
    expect(stepItem(3, 0, 1)).toBeNull();
  });
});
