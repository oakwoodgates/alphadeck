import { describe, expect, it } from "vitest";

import {
  DOC_PANEL,
  FILER_PANEL,
  FRAME,
  INDEX_PANEL,
  PLOT_W,
  apartTicks,
  axisLabels,
  baseY,
  januarySlots,
  lastCountedIndex,
  lastOnOrBefore,
  layoutBars,
  layoutFilers,
  layoutOverlay,
  monthEnd,
  monthIndexOf,
  niceMax,
  plotHeight,
  slotCenter,
  slotWidth,
  slotX,
  stepMonth,
  xOfDate,
  yAt,
  yDomain,
} from "../adoptionLayout";
import { AXIS, IDX, MONTHS, absent, counted, monthsEnding, overlay } from "./fixtures";

// The pure geometry of the three panels: one shared month axis, the stacked bars, the two filer lines broken at
// gaps, the overlay over the same axis, the keyboard's month cursor. No DOM.

describe("the shared month axis", () => {
  it("gives every month the same slot and keeps the whole axis inside the plot", () => {
    expect(PLOT_W).toBe(FRAME.w - FRAME.left - FRAME.right);
    expect(slotWidth(36)).toBeCloseTo(PLOT_W / 36, 6);
    expect(slotX(0, 36)).toBe(FRAME.left);
    expect(slotX(36, 36)).toBeCloseTo(FRAME.left + PLOT_W, 0);
    expect(slotCenter(0, 36)).toBeCloseTo(FRAME.left + PLOT_W / 72, 1);
    expect(slotWidth(0)).toBe(PLOT_W); // an empty axis never divides by zero
  });

  it("finds a month's slot and places a daily date inside it by the day of the month", () => {
    expect(monthIndexOf(AXIS, "2026-09-15")).toBe(35);
    expect(monthIndexOf(AXIS, "2023-09-29")).toBe(-1); // before the axis
    const first = xOfDate(AXIS, "2026-09-01") as number;
    const mid = xOfDate(AXIS, "2026-09-15") as number;
    const last = xOfDate(AXIS, "2026-09-30") as number;
    expect(first).toBeGreaterThanOrEqual(slotX(35, 36));
    expect(first).toBeLessThan(mid);
    expect(mid).toBeLessThan(last);
    expect(last).toBeLessThanOrEqual(slotX(35, 36) + slotWidth(36));
    // the same day lands in the same fraction of a 28-day and a 31-day month
    const feb = monthsEnding("2026-02-01", 2);
    expect(xOfDate(feb, "2026-02-14")! - slotX(1, 2)).toBeCloseTo((slotWidth(2) * 13.5) / 28, 1);
    expect(xOfDate(AXIS, "2023-09-29")).toBeNull();
  });

  it("rounds an axis maximum up to a round number and never to zero", () => {
    expect([0, -3, 1, 1.2, 7, 50, 412, 600, 601, 1000, 1001].map(niceMax)).toEqual([
      1, 1, 1, 1.5, 8, 50, 500, 600, 800, 1000, 1500,
    ]);
  });

  it("labels the first and last month and each January between, skipping one that would touch an end", () => {
    const labels = axisLabels(AXIS);
    expect(labels[0]).toMatchObject({ text: "2023-10-01", anchor: "start", year: false });
    expect(labels[labels.length - 1]).toMatchObject({ text: "2026-09-01", anchor: "end", year: false });
    expect(labels.filter((l) => l.year).map((l) => l.text)).toEqual(["2024-01-01", "2025-01-01", "2026-01-01"]);
    expect(januarySlots(AXIS)).toEqual([3, 15, 27]);
    // an axis that starts in January (or ends in one) draws no year label on top of an end label
    const jan = axisLabels(monthsEnding("2026-01-01", 6));
    expect(jan.filter((l) => l.year)).toHaveLength(0);
    expect(axisLabels([])).toEqual([]);
  });
});

describe("panel 1: the stacked bars", () => {
  const ph = plotHeight(DOC_PANEL);
  const base = baseY(DOC_PANEL);

  it("stacks the form groups from the baseline up, each a share of one scale with a round maximum", () => {
    const chart = layoutBars([counted("2026-09-01", { reports: 30, events: 10, issuance: 10 })]);
    expect(chart.max).toBe(50);
    const [bar] = chart.bars;
    expect(bar.total).toBe(50);
    expect(bar.segments.map((s) => s.group)).toEqual(["reports", "events", "issuance"]); // other is 0: no segment
    const [r, e, i] = bar.segments;
    expect(r.h).toBeCloseTo((30 / 50) * ph, 1);
    expect(e.h).toBeCloseTo((10 / 50) * ph, 1);
    expect(i.h).toBeCloseTo((10 / 50) * ph, 1);
    // the segments meet: each one's top is the next one's bottom, and the stack tops out at the total
    expect(r.y + r.h).toBeCloseTo(base, 1);
    expect(e.y + e.h).toBeCloseTo(r.y, 1);
    expect(i.y + i.h).toBeCloseTo(e.y, 1);
    expect(i.y).toBeCloseTo(DOC_PANEL.top, 1);
  });

  it("draws no bar for an absent month, a baseline tick for a stored zero, and shares one scale across months", () => {
    const chart = layoutBars(MONTHS);
    expect(chart.bars.map((b) => b.index)).toEqual([IDX("2026-06-01"), IDX("2026-07-01"), IDX("2026-09-01")]);
    expect(chart.bars.map((b) => b.zero)).toEqual([false, true, false]);
    expect(chart.bars[1].segments).toEqual([]);
    expect(chart.max).toBe(60); // niceMax(54)
    const june = chart.bars[0];
    const sep = chart.bars[2];
    expect(sep.segments.reduce((t, s) => t + s.h, 0)).toBeCloseTo((54 / 60) * ph, 0);
    expect(june.segments.reduce((t, s) => t + s.h, 0)).toBeCloseTo((13 / 60) * ph, 0);
  });

  it("puts a bar inside its month's slot and ticks the axis at 0, half and the maximum", () => {
    const chart = layoutBars([absent("2026-08-01"), counted("2026-09-01", { reports: 4 })]);
    const [bar] = chart.bars;
    expect(bar.index).toBe(1);
    expect(bar.x).toBeGreaterThan(slotX(1, 2));
    expect(bar.x + bar.w).toBeLessThanOrEqual(slotX(1, 2) + slotWidth(2));
    expect(chart.ticks.map((t) => t.v)).toEqual([0, 2, 4]);
    expect(chart.ticks[0].y).toBe(base);
  });

  it("an axis with nothing counted draws no bar and still has a scale", () => {
    const chart = layoutBars(AXIS.map((m) => absent(m)));
    expect(chart.bars).toEqual([]);
    expect(chart.max).toBe(1);
  });
});

describe("panel 2: the filer lines", () => {
  const f = (month: string, filers: number | null, over = {}) =>
    counted(month, { reports: 1 }, { filers, new_filers: null, ...over });

  it("breaks a line at a month with no value and never joins across it or drops it to zero", () => {
    const months = [f("2026-06-01", 5), absent("2026-07-01"), f("2026-08-01", 8), f("2026-09-01", 4)];
    const chart = layoutFilers(months);
    expect(chart.filers.points.map((p) => p.index)).toEqual([0, 2, 3]);
    expect(chart.filers.path.match(/M/g)).toHaveLength(2); // two runs
    expect(chart.filers.path.match(/L/g)).toHaveLength(1);
    expect(chart.max).toBe(8);
    expect(chart.filers.points[1].y).toBeLessThan(chart.filers.points[0].y); // 8 is higher than 5
  });

  it("draws the new-filers line only where a month has one, on the same scale", () => {
    const months = [f("2026-06-01", 5, { new_filers: null }), f("2026-07-01", 6, { new_filers: 2 }), f("2026-08-01", 7, { new_filers: 3 })];
    const chart = layoutFilers(months);
    expect(chart.newFilers.points.map((p) => [p.index, p.value])).toEqual([
      [1, 2],
      [2, 3],
    ]);
    expect(chart.max).toBe(8);
    expect(chart.newFilers.points[0].y).toBeGreaterThan(chart.filers.points[1].y); // 2 sits lower than 6
  });

  it("rings the months that read 'at least', unless every month does (a mark true of all says nothing)", () => {
    const some = layoutFilers([f("2026-06-01", 5, { filers_sampled: true }), f("2026-07-01", 6)]);
    expect(some.allSampled).toBe(false);
    expect(some.filers.points.map((p) => p.ring)).toEqual([true, false]);
    const all = layoutFilers([f("2026-06-01", 5, { filers_sampled: true }), f("2026-07-01", 6, { filers_sampled: true })]);
    expect(all.allSampled).toBe(true);
    expect(all.filers.points.every((p) => !p.ring)).toBe(true);
    expect(layoutFilers([absent("2026-06-01")]).allSampled).toBe(false);
  });

  it("an axis with no filers has empty lines and a scale", () => {
    const chart = layoutFilers(AXIS.map((m) => absent(m)));
    expect(chart.filers.path).toBe("");
    expect(chart.newFilers.points).toEqual([]);
    expect(chart.max).toBe(1);
    expect(chart.baseY).toBe(baseY(FILER_PANEL));
  });
});

describe("panel 3: the overlay on the same axis", () => {
  it("includes 100 in the scale, pads it, and puts a higher value higher up", () => {
    const d = yDomain([[100, 110], [95]]);
    expect(d[0]).toBeLessThan(95);
    expect(d[1]).toBeGreaterThan(110);
    expect(yAt(110, d, INDEX_PANEL)).toBeLessThan(yAt(100, d, INDEX_PANEL));
    expect(yDomain([null, undefined])).toEqual([99.5, 100.5]);
  });

  it("draws each line over the daily dates at their month slots, and none for a line the server did not send", () => {
    const chart = layoutOverlay(overlay(), AXIS);
    expect(chart.card?.startsWith("M")).toBe(true);
    // the session before the axis (2023-09-29) is off the axis: its point is not drawn
    const dated = overlay().dates.filter((d) => xOfDate(AXIS, d) != null).length;
    expect(chart.card?.match(/[ML]/g)).toHaveLength(dated);
    expect(chart.spy?.match(/[ML]/g)).toHaveLength(dated);
    expect(layoutOverlay(overlay({ card: null }), AXIS).card).toBeNull();
    expect(layoutOverlay(overlay({ spy: null }), AXIS).spy).toBeNull();
    expect(layoutOverlay(overlay({ dates: [], card: null, spy: null }), AXIS).baseline).toBeGreaterThan(0);
    expect(chart.ticks.map((t) => t.v)).toContain(100);
  });

  it("keeps the overlay's tick labels apart: a bottom bound a hair under 100 is dropped, a wide scale keeps all three", () => {
    const rising = layoutOverlay(overlay({ card: [100, 140, 180], spy: [100, 110, 120], dates: ["2026-07-31", "2026-08-31", "2026-09-30"] }), AXIS);
    expect(rising.ticks.map((t) => t.v)).not.toContain(rising.domain[0]); // 92.x sits right under 100
    expect(rising.ticks.map((t) => t.v)).toEqual([100, rising.domain[1]]);
    const wide = layoutOverlay(overlay({ card: [100, 60, 160], spy: null, dates: ["2026-07-31", "2026-08-31", "2026-09-30"] }), AXIS);
    expect(wide.ticks.map((t) => t.v)).toEqual([wide.domain[0], 100, wide.domain[1]]);
    const gaps = wide.ticks.map((t) => t.y);
    expect(gaps[0]).toBeGreaterThan(gaps[1]); // lower value, lower on the page
    // a flat line has a narrow scale but wide gaps in pixels: all three stay; a bottom bound at 99.9 under a tall scale goes
    expect(apartTicks([99.5, 100.5], INDEX_PANEL).map((t) => t.v)).toEqual([99.5, 100, 100.5]);
    expect(apartTicks([99.9, 130], INDEX_PANEL).map((t) => t.v)).toEqual([100, 130]);
  });

  it("finds the last session on or before a day and the last day of a month", () => {
    const dates = ["2026-08-28", "2026-09-04", "2026-09-30"];
    expect(lastOnOrBefore(dates, "2026-09-03")).toBe(0);
    expect(lastOnOrBefore(dates, "2026-09-30")).toBe(2);
    expect(lastOnOrBefore(dates, "2026-08-27")).toBe(-1);
    expect(lastOnOrBefore([], "2026-08-27")).toBe(-1);
    expect([monthEnd("2024-02-01"), monthEnd("2026-02-01"), monthEnd("2026-10-01"), monthEnd("2026-04-01")]).toEqual([
      "2024-02-29",
      "2026-02-28",
      "2026-10-31",
      "2026-04-30",
    ]);
  });
});

describe("the keyboard's month cursor", () => {
  it("starts at the given month, then steps and stops at the ends", () => {
    expect(stepMonth(null, 36, -1, 35)).toBe(35);
    expect(stepMonth(35, 36, -1, 35)).toBe(34);
    expect(stepMonth(35, 36, 1, 35)).toBe(35);
    expect(stepMonth(0, 36, -1, 35)).toBe(0);
    expect(stepMonth(null, 0, 1, 5)).toBe(0);
    expect(stepMonth(null, 36, 1, 99)).toBe(35);
  });

  it("starts on the newest counted month, or the last month when none is counted", () => {
    expect(lastCountedIndex(MONTHS)).toBe(35);
    expect(lastCountedIndex(AXIS.map((m) => absent(m)))).toBe(35);
    expect(lastCountedIndex([])).toBe(0);
    expect(lastCountedIndex([counted("2026-06-01", {}), absent("2026-07-01")])).toBe(0);
  });
});
