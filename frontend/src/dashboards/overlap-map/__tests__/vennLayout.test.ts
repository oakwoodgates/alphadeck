import { describe, expect, it } from "vitest";

import {
  MAX_RADIUS,
  MIN_LABEL_AREA,
  distanceForOverlap,
  lensArea,
  overlapsOf,
  regionWords,
  vennLayout,
  vennRegions,
  type VennSet,
} from "../vennLayout";

// The Venn geometry (docs/dashboards/overlap-map.md): two sets are exact (the lens area matches the shared count),
// three are approximate (but their region COUNTS are exact), a subset draws inside its parent, disjoint sets draw
// apart, and past 50x the small circle keeps a minimum radius.

const ids = (prefix: string, from: number, to: number): string[] =>
  Array.from({ length: to - from }, (_, i) => `${prefix}${from + i}`);

const set = (key: string, members: string[]): VennSet => ({ key, label: key.toUpperCase(), members });

/** The unit of area the layout draws one member as: the biggest set fills a circle of MAX_RADIUS. */
const unitFor = (biggest: number) => (Math.PI * MAX_RADIUS * MAX_RADIUS) / biggest;

describe("the exact two-set geometry", () => {
  it("solves the distance so the lens matches the shared count within 1%", () => {
    for (const [a, b, shared] of [
      [100, 60, 25],
      [100, 100, 50],
      [80, 30, 5],
      [50, 49, 40],
    ] as const) {
      const A = ids("a", 0, a);
      const B = [...A.slice(0, shared), ...ids("b", 0, b - shared)];
      const lay = vennLayout(overlapsOf([set("a", A), set("b", B)]));
      const [c0, c1] = lay.circles;
      const d = Math.hypot(c0.cx - c1.cx, c0.cy - c1.cy);
      const target = shared * unitFor(Math.max(a, b));
      expect(Math.abs(lensArea(c0.r, c1.r, d) - target) / target).toBeLessThan(0.01);
      // each circle's area is proportional to its set
      expect((Math.PI * c0.r * c0.r) / unitFor(Math.max(a, b))).toBeCloseTo(a, 5);
      expect((Math.PI * c1.r * c1.r) / unitFor(Math.max(a, b))).toBeCloseTo(b, 5);
      expect(lay.minRadiusApplied).toBe(false);
    }
  });

  it("bisection returns the touching distance for no overlap and the subset distance for a full one", () => {
    expect(distanceForOverlap(10, 6, 0)).toBe(16);
    expect(distanceForOverlap(10, 6, Math.PI * 36)).toBe(4);
    const d = distanceForOverlap(10, 6, 50);
    expect(d).toBeGreaterThan(4);
    expect(d).toBeLessThan(16);
    expect(lensArea(10, 6, d)).toBeCloseTo(50, 3);
  });

  it("draws a subset inside its parent", () => {
    const A = ids("a", 0, 40);
    const lay = vennLayout(overlapsOf([set("a", A), set("b", A.slice(0, 10))]));
    const [big, small] = lay.circles;
    expect(Math.hypot(big.cx - small.cx, big.cy - small.cy) + small.r).toBeLessThanOrEqual(big.r + 1e-6);
    const regions = vennRegions([set("a", A), set("b", A.slice(0, 10))]);
    expect(regions.map((r) => [r.mask, r.count])).toEqual([
      [1, 30],
      [2, 0],
      [3, 10],
    ]);
  });

  it("draws disjoint sets apart", () => {
    const lay = vennLayout(overlapsOf([set("a", ids("a", 0, 20)), set("b", ids("b", 0, 20))]));
    const [c0, c1] = lay.circles;
    expect(Math.hypot(c0.cx - c1.cx, c0.cy - c1.cy)).toBeGreaterThan(c0.r + c1.r);
    expect(lensArea(c0.r, c1.r, Math.hypot(c0.cx - c1.cx, c0.cy - c1.cy))).toBe(0);
  });

  it("keeps a minimum radius for a set more than 50 times smaller, and says so", () => {
    const A = ids("a", 0, 600);
    const lay = vennLayout(overlapsOf([set("a", A), set("b", A.slice(0, 3))]));
    expect(lay.minRadiusApplied).toBe(true);
    expect(lay.circles[0].r).toBeCloseTo(MAX_RADIUS, 6);
    expect(lay.circles[1].r).toBeCloseTo(MAX_RADIUS / Math.sqrt(50), 6);
    // at exactly 50x the natural radius is the minimum: nothing is grown
    const even = vennLayout(overlapsOf([set("a", ids("a", 0, 500)), set("b", ids("b", 0, 10))]));
    expect(even.minRadiusApplied).toBe(false);
  });

  it("draws an empty set at the minimum radius without calling it grown", () => {
    const lay = vennLayout(overlapsOf([set("a", ids("a", 0, 5)), set("b", [])]));
    expect(lay.circles[1].r).toBeGreaterThan(0);
    expect(lay.minRadiusApplied).toBe(false);
  });
});

describe("region areas", () => {
  it("measures each region so a count too big for a small one can be listed instead", () => {
    const A = ids("a", 0, 100);
    const lay = vennLayout(overlapsOf([set("a", A), set("b", A.slice(0, 1).concat(ids("b", 0, 99)))]));
    expect(lay.areas[3]).toBeLessThan(MIN_LABEL_AREA); // a lens of one name in a hundred
    expect(lay.areas[1]).toBeGreaterThan(MIN_LABEL_AREA);
    expect(lay.areas[2]).toBeGreaterThan(MIN_LABEL_AREA);
    const apart = vennLayout(overlapsOf([set("a", ids("a", 0, 20)), set("b", ids("b", 0, 20))]));
    expect(apart.areas[3]).toBe(0);
  });
});

describe("three sets", () => {
  const A = ids("a", 0, 30);
  const B = [...A.slice(0, 10), ...ids("b", 0, 20)];
  const C = [...A.slice(5, 15), ...B.slice(8, 14), ...ids("c", 0, 5)];

  it("counts all 7 regions exactly, whatever the geometry", () => {
    const regions = vennRegions([set("a", A), set("b", B), set("c", C)]);
    expect(regions).toHaveLength(7);
    const total = new Set([...A, ...B, ...C]).size;
    expect(regions.reduce((s, r) => s + r.count, 0)).toBe(total);
    const a = new Set(A);
    const b = new Set(B);
    const c = new Set(C);
    for (const r of regions) {
      const want = [...new Set([...A, ...B, ...C])].filter(
        (x) => a.has(x) === !!(r.mask & 1) && b.has(x) === !!(r.mask & 2) && c.has(x) === !!(r.mask & 4),
      ).length;
      expect(r.count).toBe(want);
    }
  });

  it("places three circles inside a box with an anchor for every region", () => {
    const lay = vennLayout(overlapsOf([set("a", A), set("b", B), set("c", C)]));
    expect(lay.circles).toHaveLength(3);
    for (const c of lay.circles) {
      expect(c.cx - c.r).toBeGreaterThanOrEqual(0);
      expect(c.cy - c.r).toBeGreaterThanOrEqual(0);
      expect(c.cx + c.r).toBeLessThanOrEqual(lay.width + 1e-6);
      expect(c.cy + c.r).toBeLessThanOrEqual(lay.height + 1e-6);
    }
    expect(Object.keys(lay.anchors).map(Number).sort()).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("clamps three distances that cannot close into a triangle onto a line", () => {
    // A and B nearly equal, C overlapping A fully and B not at all: the pairwise distances cannot all hold
    const A2 = ids("a", 0, 40);
    const lay = vennLayout(overlapsOf([set("a", A2), set("b", A2.slice(0, 2).concat(ids("b", 0, 38))), set("c", A2.slice(2, 40))]));
    for (const c of lay.circles) {
      expect(Number.isFinite(c.cx) && Number.isFinite(c.cy) && c.r > 0).toBe(true);
    }
  });

  it("is deterministic", () => {
    const sets = [set("a", A), set("b", B), set("c", C)];
    expect(vennLayout(overlapsOf(sets))).toEqual(vennLayout(overlapsOf(sets)));
  });
});

describe("naming a region", () => {
  it("words each region in plain terms", () => {
    expect(regionWords(0b011, ["A", "B", "C"])).toBe("In A and B, not C");
    expect(regionWords(0b100, ["A", "B", "C"])).toBe("In C, not A or B");
    expect(regionWords(0b111, ["A", "B", "C"])).toBe("In A, B and C");
    expect(regionWords(0b01, ["A", "B"])).toBe("In A, not B");
    expect(regionWords(0b11, ["A", "B"])).toBe("In A and B");
  });
});
