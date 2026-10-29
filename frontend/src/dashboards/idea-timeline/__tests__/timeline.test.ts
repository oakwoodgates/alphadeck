import { describe, expect, it } from "vitest";

import { dayNum, monthTicks, pct, spanStyle, viewFor } from "../timeline";

// The Idea Timeline's layout helpers: the zoom windows, the clipped spans and the month ticks.

const DATA = { today: "2026-10-29", axis_start: "2026-07-10", axis_end: "2026-11-30" };

describe("viewFor", () => {
  it("All is the server's axis, with one day of room past its last date", () => {
    const v = viewFor("all", DATA);
    expect(v.start).toBe(dayNum("2026-07-10"));
    expect(v.end).toBe(dayNum("2026-12-01"));
    expect(v.today).toBe(dayNum("2026-10-29"));
  });

  it("a preset of N days is today - N to today + N, its tail capped by the axis end", () => {
    const v30 = viewFor("30", DATA);
    expect([v30.start, v30.end]).toEqual([dayNum("2026-09-29"), dayNum("2026-11-29")]);
    const v90 = viewFor("90", DATA);
    expect([v90.start, v90.end]).toEqual([dayNum("2026-07-31"), dayNum("2026-12-01")]);
  });

  it("an axis that ends today still leaves today's night on screen", () => {
    const v = viewFor("all", { today: "2026-10-29", axis_start: "2026-10-29", axis_end: "2026-10-29" });
    expect(v.end - v.start).toBe(1);
    expect(pct(v, v.today)).toBe(0);
  });
});

describe("spanStyle", () => {
  const v = viewFor("30", DATA);

  it("clips a span to the window and drops one wholly outside it", () => {
    expect(spanStyle(v, "2026-07-10", "2026-09-01")).toBeNull();
    const clipped = spanStyle(v, "2026-09-01", "2026-10-29");
    expect(clipped?.left).toBe("0%");
    expect(parseFloat(clipped?.width ?? "0")).toBeCloseTo((30 / 61) * 100, 5); // Sep 29 -> Oct 29 of a 61-day view
  });

  it("keeps a zero-length run (started tonight) as a point", () => {
    expect(spanStyle(v, "2026-10-29", "2026-10-29")?.width).toBe("0%");
  });
});

describe("monthTicks", () => {
  it("ticks the first of every month inside the window, January with its year", () => {
    const v = viewFor("all", { today: "2026-12-20", axis_start: "2026-11-15", axis_end: "2027-02-10" });
    expect(monthTicks(v).map((t) => t.label)).toEqual(["Dec", "Jan 2027", "Feb"]);
  });
});
