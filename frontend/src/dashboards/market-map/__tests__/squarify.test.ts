import { describe, expect, it } from "vitest";

import { squarify, type Rect } from "../squarify";

// The squarified treemap: areas proportional to weights (within 1%), no overlap, everything inside the bounds,
// deterministic, input order preserved, and non-positive weights taking no room.

const BOUNDS: Rect = { x: 10, y: 20, w: 800, h: 500 };

/** A tiny seeded generator so the "random" cases are the same on every run. */
function lcg(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

const area = (r: Rect) => r.w * r.h;

function overlap(a: Rect, b: Rect): number {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

function sets(): number[][] {
  const rand = lcg(7);
  const long = Array.from({ length: 300 }, () => Math.exp(rand() * 9)); // a long, heavy-tailed list
  return [
    [1],
    [3, 1],
    [6, 6, 4, 3, 2, 2, 1],
    Array.from({ length: 25 }, (_, i) => i + 1),
    long,
    [1_000_000, 1, 1, 1, 1], // one giant and dust
  ];
}

describe("squarify", () => {
  it("gives each item an area proportional to its weight (within 1%) and uses the whole rectangle", () => {
    for (const weights of sets()) {
      const rects = squarify(weights, BOUNDS);
      const total = weights.reduce((a, b) => a + b, 0);
      for (let i = 0; i < weights.length; i++) {
        const want = (weights[i] / total) * area(BOUNDS);
        expect(Math.abs(area(rects[i]) - want) / want).toBeLessThan(0.01);
      }
      expect(rects.reduce((s, r) => s + area(r), 0)).toBeCloseTo(area(BOUNDS), 3);
    }
  });

  it("keeps every rectangle inside the bounds", () => {
    const eps = 1e-6;
    for (const weights of sets()) {
      for (const r of squarify(weights, BOUNDS)) {
        expect(r.x).toBeGreaterThanOrEqual(BOUNDS.x - eps);
        expect(r.y).toBeGreaterThanOrEqual(BOUNDS.y - eps);
        expect(r.x + r.w).toBeLessThanOrEqual(BOUNDS.x + BOUNDS.w + eps);
        expect(r.y + r.h).toBeLessThanOrEqual(BOUNDS.y + BOUNDS.h + eps);
      }
    }
  });

  it("never overlaps two rectangles", () => {
    for (const weights of sets().slice(0, 5)) {
      const rects = squarify(weights, BOUNDS);
      let worst = 0;
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) worst = Math.max(worst, overlap(rects[i], rects[j]));
      }
      expect(worst).toBeLessThan(1e-3);
    }
  });

  it("is deterministic and returns the rectangles in the input order", () => {
    const weights = [2, 9, 4, 4, 1];
    const a = squarify(weights, BOUNDS);
    const b = squarify([...weights], { ...BOUNDS });
    expect(a).toEqual(b);
    // the biggest weight (index 1) gets the biggest area; the tie (indexes 2 and 3) gets equal areas
    expect(area(a[1])).toBeGreaterThan(area(a[0]));
    expect(area(a[2])).toBeCloseTo(area(a[3]), 6);
  });

  it("keeps the aspect ratios reasonable for an ordinary list", () => {
    const rects = squarify([6, 6, 4, 3, 2, 2, 1], BOUNDS);
    for (const r of rects) expect(Math.max(r.w / r.h, r.h / r.w)).toBeLessThan(4);
  });

  it("fills the rectangle with one item and splits it by weight with two", () => {
    expect(squarify([5], BOUNDS)[0]).toEqual(BOUNDS);
    const [a, b] = squarify([3, 1], BOUNDS);
    expect(area(a) / area(b)).toBeCloseTo(3, 6);
  });

  it("gives a zero, negative or non-finite weight no room and shares the rest", () => {
    const rects = squarify([4, 0, -1, Number.NaN, 4], BOUNDS);
    for (const i of [1, 2, 3]) expect(area(rects[i])).toBe(0);
    expect(area(rects[0])).toBeCloseTo(area(BOUNDS) / 2, 6);
    expect(area(rects[4])).toBeCloseTo(area(BOUNDS) / 2, 6);
  });

  it("returns empty rectangles for no weights or a degenerate space", () => {
    expect(squarify([], BOUNDS)).toEqual([]);
    expect(squarify([1, 2], { x: 0, y: 0, w: 0, h: 100 }).every((r) => area(r) === 0)).toBe(true);
    expect(squarify([0, 0], BOUNDS).every((r) => area(r) === 0)).toBe(true);
  });
});
