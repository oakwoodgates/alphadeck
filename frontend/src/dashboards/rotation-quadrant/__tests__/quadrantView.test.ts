import { describe, expect, it } from "vitest";

import {
  DEFAULT_SORT,
  changedQuadrant,
  fmtNum,
  groupLabel,
  moveWords,
  nextSort,
  plotDomain,
  sortGroups,
  statusWords,
  sx,
  sy,
  tailPath,
} from "../quadrantView";
import type { QuadrantGroupOut, QuadrantTailPointOut } from "../useRotationQuadrant";

// The Rotation Quadrant's pure view helpers: the labels and words, the default order (quadrant, then x) with "—"
// last both ways, the changed-quadrant filter, the drawn tail, and the plot's geometry centered on 100.

const pt = (x: number | null, y: number | null, session: string | null = "2026-10-28"): QuadrantTailPointOut => ({
  session,
  x,
  y,
  quadrant: null,
});

const group = (key: string, over: Partial<QuadrantGroupOut> = {}): QuadrantGroupOut => ({
  key,
  link: key,
  thesis_id: "t",
  thesis_name: "Card",
  business_type: null,
  supersector: null,
  names: 4,
  priced: 4,
  status: "plotted",
  x: 100,
  y: 100,
  quadrant: "lagging",
  quadrant_ago: "lagging",
  tail: [],
  sessions_flat: 0,
  members: [],
  flagged: [],
  ...over,
});

const keys = (gs: QuadrantGroupOut[]) => gs.map((g) => g.key);

describe("quadrantView", () => {
  it("words a group's label, its status and its move", () => {
    const g = group("Turbines", { quadrant: "improving", quadrant_ago: "lagging" });
    expect(groupLabel(g, "link")).toBe("Turbines · Card");
    expect(groupLabel(group("x", { link: null }), "link")).toBe("Unsegmented · Card");
    expect(groupLabel(group("x", { business_type: null }), "business_type")).toBe("Unclassified");
    expect(groupLabel(group("x", { supersector: null }), "sector")).toBe("Unclassified");
    expect(moveWords(g)).toBe("Lagging → Improving");
    expect(moveWords(group("x", { quadrant_ago: null }))).toBe("— → Lagging");
    expect(statusWords(g, 3)).toBeNull();
    expect(statusWords(group("x", { status: "too_small" }), 3)).toBe("fewer than 3 priced names");
    expect(statusWords(group("x", { status: "no_history" }), 3)).toBe("not enough history");
    expect(fmtNum(101.234)).toBe("101.23");
    expect(fmtNum(null)).toBe("—");
  });

  it("sorts by quadrant (improving, leading, weakening, lagging), then x highest first, unmeasured last", () => {
    const gs = [
      group("lag", { quadrant: "lagging", x: 98 }),
      group("small", { status: "too_small", quadrant: null, x: null, y: null }),
      group("lead-lo", { quadrant: "leading", x: 101 }),
      group("weak", { quadrant: "weakening", x: 102 }),
      group("imp-hi", { quadrant: "improving", x: 99.9 }),
      group("lead-hi", { quadrant: "leading", x: 105 }),
      group("imp-lo", { quadrant: "improving", x: 95 }),
    ];
    expect(keys(sortGroups(gs, DEFAULT_SORT, "link"))).toEqual([
      "imp-hi",
      "imp-lo",
      "lead-hi",
      "lead-lo",
      "weak",
      "lag",
      "small",
    ]);
    // reversed: the quadrants flip, "—" stays last
    expect(keys(sortGroups(gs, { col: "quadrant", dir: "desc" }, "link"))).toEqual([
      "lag",
      "weak",
      "lead-hi",
      "lead-lo",
      "imp-hi",
      "imp-lo",
      "small",
    ]);
    expect(keys(sortGroups(gs, { col: "x", dir: "desc" }, "link"))[0]).toBe("lead-hi");
    expect(keys(sortGroups(gs, { col: "x", dir: "asc" }, "link")).at(-1)).toBe("small");
  });

  it("starts each column in its natural direction and flips on a second click", () => {
    expect(nextSort(DEFAULT_SORT, "x")).toEqual({ col: "x", dir: "desc" });
    expect(nextSort(DEFAULT_SORT, "label")).toEqual({ col: "label", dir: "asc" });
    expect(nextSort(DEFAULT_SORT, "quadrant_ago")).toEqual({ col: "quadrant_ago", dir: "asc" });
    expect(nextSort(DEFAULT_SORT, "quadrant")).toEqual({ col: "quadrant", dir: "desc" });
  });

  it("counts a change of quadrant only when both quadrants are known", () => {
    expect(changedQuadrant(group("a", { quadrant: "improving", quadrant_ago: "lagging" }))).toBe(true);
    expect(changedQuadrant(group("b", { quadrant: "lagging", quadrant_ago: "lagging" }))).toBe(false);
    expect(changedQuadrant(group("c", { quadrant: "lagging", quadrant_ago: null }))).toBe(false);
    expect(changedQuadrant(group("d", { quadrant: null, quadrant_ago: "lagging" }))).toBe(false);
  });

  it("draws the tail's measurable points ending at today's dot, a gap ending the line", () => {
    const g = group("a", {
      tail: [pt(97, 99), pt(null, null), pt(98, 100), pt(99, 101), pt(100.5, 101.5)],
    });
    expect(tailPath(g)).toEqual([
      { x: 98, y: 100 },
      { x: 99, y: 101 },
      { x: 100.5, y: 101.5 },
    ]);
    expect(tailPath(group("b", { tail: [pt(99, 99), pt(99, null)] }))).toEqual([]);
  });

  it("centers the plot on 100, symmetric, wide enough for every dot and tail point", () => {
    const gs = [
      group("a", { tail: [pt(96, 100), pt(98, 101)] }),
      group("b", { tail: [pt(101, 99), pt(102, 97)] }),
    ];
    const d = plotDomain(gs);
    expect(d.hx).toBeCloseTo(4.4); // |96 - 100| × 1.1
    expect(d.hy).toBeCloseTo(3.3); // |97 - 100| × 1.1
    expect(sx(100, d, 200)).toBeCloseTo(100);
    expect(sy(100, d, 100)).toBeCloseTo(50);
    expect(sy(100 + d.hy, d, 100)).toBeCloseTo(0); // up is higher
    expect(plotDomain([group("q", { tail: [pt(100.1, 99.95)] })])).toEqual({ hx: 1, hy: 1 }); // a floor
  });
});
