import { describe, expect, it } from "vitest";

import {
  PAD_L,
  PAD_R,
  PIC_W,
  SPARK_H,
  SPARK_TOP,
  axisTicks,
  dayNumber,
  layoutRow,
  makeAxis,
  moveRow,
} from "../windowsLayout";
import { ASOF, CLOSED, NO_CLOCKS, row } from "./fixtures";

// The pure geometry: one axis for the page, the bars from the arm night, the ticks, the sparkline, the row keys.

describe("makeAxis", () => {
  const axis = makeAxis("2026-10-07", "2026-11-14");

  it("puts the first day at the left padding and the last at the right padding, linear between", () => {
    expect(axis.x("2026-10-07")).toBe(PAD_L);
    expect(axis.x("2026-11-14")).toBe(PIC_W - PAD_R);
    const span = dayNumber("2026-11-14") - dayNumber("2026-10-07");
    const mid = axis.x("2026-10-26");
    expect(mid).toBeCloseTo(PAD_L + ((dayNumber("2026-10-26") - dayNumber("2026-10-07")) / span) * (PIC_W - PAD_L - PAD_R), 6);
  });

  it("clamps a date off either end to the edge", () => {
    expect(axis.x("2026-09-01")).toBe(PAD_L);
    expect(axis.x("2027-01-01")).toBe(PIC_W - PAD_R);
  });

  it("survives a one-day (or inverted) span", () => {
    expect(makeAxis("2026-10-27", "2026-10-27").x("2026-10-27")).toBe(PAD_L);
    expect(Number.isFinite(makeAxis("2026-10-27", "2026-10-01").x("2026-10-27"))).toBe(true);
  });
});

describe("axisTicks", () => {
  it("labels Mondays over a short span", () => {
    const ticks = axisTicks(makeAxis("2026-10-07", "2026-11-14"));
    expect(ticks.map((t) => t.label)).toEqual(["Oct 12", "Oct 19", "Oct 26", "Nov 2", "Nov 9"]);
    expect(ticks.every((t) => new Date(`${t.iso}T00:00:00Z`).getUTCDay() === 1)).toBe(true);
  });

  it("labels month starts over a longer span and quarter starts beyond a year", () => {
    expect(axisTicks(makeAxis("2026-08-20", "2026-12-20")).map((t) => t.label)).toEqual(["Sep 1", "Oct 1", "Nov 1", "Dec 1"]);
    expect(axisTicks(makeAxis("2026-01-15", "2027-09-15")).map((t) => t.label)).toEqual(["Apr 1", "Jul 1", "Oct 1", "Jan 1", "Apr 1", "Jul 1"]);
  });
});

describe("layoutRow", () => {
  const axis = makeAxis("2026-10-07", "2026-11-14");

  it("draws both bars from the arm night, to arm_until and to exit_by", () => {
    const L = layoutRow(row(), axis, ASOF);
    expect(L.armX).toBe(axis.x("2026-10-20"));
    expect(L.entry).toEqual({ x: axis.x("2026-10-20"), w: axis.x("2026-10-30") - axis.x("2026-10-20") });
    expect(L.hold).toEqual({ x: axis.x("2026-10-20"), w: axis.x("2026-11-14") - axis.x("2026-10-20") });
    // the as-of line is the same x on every row
    expect(L.asofX).toBe(axis.x(ASOF));
    expect(layoutRow(CLOSED, axis, ASOF).asofX).toBe(L.asofX);
  });

  it("draws no entry bar for a card with no arm_until and no hold bar for one with no exit_by", () => {
    const L = layoutRow(row({ arm_until: null }), axis, ASOF);
    expect(L.entry).toBeNull();
    expect(L.hold).not.toBeNull();
    const none = layoutRow(NO_CLOCKS, axis, ASOF);
    expect(none.entry).toBeNull();
    expect(none.hold).toBeNull();
  });

  it("draws no bar for a clock dated before the arm night", () => {
    const L = layoutRow(row({ arm_until: "2026-10-19" }), axis, ASOF);
    expect(L.entry).toBeNull();
  });

  it("places the catalyst ticks by date, hollow ones flagged from the server's past", () => {
    const L = layoutRow(row(), axis, ASOF);
    expect(L.ticks.map((t) => [t.label, t.past, t.x])).toEqual([
      ["readout", false, axis.x("2026-10-29")],
      ["earnings", true, axis.x("2026-10-22")],
    ]);
  });

  it("scales both sparkline lines into one band and draws the 100 base line when it is in range", () => {
    const L = layoutRow(row(), axis, ASOF);
    expect(L.spark).not.toBeNull();
    const ys = [...(L.spark!.line + " " + L.spark!.iwm).matchAll(/[ML][\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));
    expect(ys.length).toBe(12);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(SPARK_TOP);
    expect(Math.max(...ys)).toBeLessThanOrEqual(SPARK_TOP + SPARK_H);
    // 100 is the lowest level of both lines, so the base line sits on the band's bottom
    expect(L.spark!.base).toBeCloseTo(SPARK_TOP + SPARK_H, 6);
    expect(L.spark!.line.startsWith("M")).toBe(true);
    expect(L.spark!.line.match(/L/g)?.length).toBe(5);
  });

  it("draws no IWM line when the payload has none, and a flat line mid-band", () => {
    const flat = row({ spark: { dates: ["2026-10-26", "2026-10-27"], line: [100, 100], iwm: null } });
    const L = layoutRow(flat, axis, ASOF);
    expect(L.spark!.iwm).toBeNull();
    expect(L.spark!.line).toBe(`M${axis.x("2026-10-26").toFixed(1)} ${(SPARK_TOP + SPARK_H / 2).toFixed(1)} L${axis.x("2026-10-27").toFixed(1)} ${(SPARK_TOP + SPARK_H / 2).toFixed(1)}`);
    expect(L.spark!.base).toBeNull();
  });

  it("draws nothing for a row with no sparkline", () => {
    expect(layoutRow(NO_CLOCKS, axis, ASOF).spark).toBeNull();
  });
});

describe("moveRow", () => {
  const keys = ["a", "b", "c"];
  it("steps down and up and holds at the ends", () => {
    expect(moveRow(keys, "a", "down")).toBe("b");
    expect(moveRow(keys, "c", "down")).toBe("c");
    expect(moveRow(keys, "b", "up")).toBe("a");
    expect(moveRow(keys, "a", "up")).toBe("a");
  });
  it("jumps to the ends and falls back to the first row for an unknown key", () => {
    expect(moveRow(keys, "b", "home")).toBe("a");
    expect(moveRow(keys, "b", "end")).toBe("c");
    expect(moveRow(keys, "zzz", "down")).toBe("a");
    expect(moveRow([], "a", "down")).toBe("a");
  });
});
