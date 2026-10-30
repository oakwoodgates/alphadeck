import { describe, expect, it } from "vitest";

import {
  COLOR_END,
  DEFAULT_OPTS,
  HEADER_H,
  MIN_PRICED,
  WINDOWS,
  benchReturn,
  buildTree,
  clickAction,
  colorStep,
  compareAvailability,
  flattenTiles,
  groupMedian,
  headerTitle,
  layoutTree,
  legendSteps,
  nextSort,
  rawWeight,
  resolveZoom,
  shown,
  sortRows,
  stepClass,
  tableRows,
  tileText,
  type TreeNode,
  type ViewOpts,
} from "../mapView";
import type { Rect } from "../squarify";
import { company, mem, payload, twoCards } from "./fixture";

const opts = (over: Partial<ViewOpts> = {}): ViewOpts => ({ ...DEFAULT_OPTS, ...over });

const labels = (nodes: readonly TreeNode[]) => nodes.map((n) => n.label);
const find = (nodes: readonly TreeNode[], label: string) => nodes.find((n) => n.label === label)!;
const tickers = (nodes: readonly TreeNode[]) =>
  flattenTiles(nodes)
    .map((t) => t.node.label)
    .sort();

// ---------------------------------------------------------------------------------------------------------
// grouping: a company appears once per group it belongs to
// ---------------------------------------------------------------------------------------------------------

describe("the hierarchy per Group-by", () => {
  const data = twoCards();

  it("Card > Link: once per card-and-link membership", () => {
    const { roots, tiles } = buildTree(data, opts({ groupBy: "card_link" }));
    expect(labels(roots).sort()).toEqual(["Fuel", "Grid"]);
    const grid = find(roots, "Grid");
    expect(labels(grid.children).sort()).toEqual(["Cables", "Turbines"]);
    expect(tickers(find(grid.children, "Turbines").children)).toEqual(["AAA", "BBB", "CCC"]);
    expect(tickers(find(grid.children, "Cables").children)).toEqual(["AAA"]);
    // AAA is drawn on Grid/Turbines, Grid/Cables and Fuel/Core: three tiles; plus BBB, CCC, DDD, EEE
    expect(tiles).toBe(7);
    expect(flattenTiles(roots).filter((t) => t.node.label === "AAA")).toHaveLength(3);
  });

  it("Card: once per card, however many links", () => {
    const { roots, tiles } = buildTree(data, opts({ groupBy: "card" }));
    expect(tickers(find(roots, "Grid").children)).toEqual(["AAA", "BBB", "CCC"]);
    expect(tickers(find(roots, "Fuel").children)).toEqual(["AAA", "DDD", "EEE"]);
    expect(tiles).toBe(6);
  });

  it("Sector > Business type: once in total, boxes inside boxes", () => {
    const { roots, tiles } = buildTree(data, opts({ groupBy: "sector_type" }));
    expect(labels(roots).sort()).toEqual(["Energy & Utilities", "Technology", "Unclassified"]);
    const tech = find(roots, "Technology");
    expect(labels(tech.children)).toEqual(["semis"]);
    expect(tickers(tech.children)).toEqual(["AAA"]);
    expect(labels(find(roots, "Unclassified").children)).toEqual(["Unclassified"]);
    expect(tiles).toBe(5);
  });

  it("Sector, Business type and Cap band: once in total", () => {
    for (const groupBy of ["sector", "type", "cap"] as const) {
      const { roots, tiles } = buildTree(data, opts({ groupBy }));
      expect(tickers(roots)).toEqual(["AAA", "BBB", "CCC", "DDD", "EEE"]);
      expect(tiles).toBe(5);
    }
    const bands = buildTree(data, opts({ groupBy: "cap" })).roots;
    expect(find(bands, "No cap").children.map((n) => n.label)).toEqual(["EEE"]);
    expect(labels(bands)).toContain("Small · $300M to $2B");
  });

  it("All companies: one flat map with no boxes", () => {
    const { roots, tiles } = buildTree(data, opts({ groupBy: "all" }));
    expect(roots.every((n) => n.kind === "tile")).toBe(true);
    expect(tiles).toBe(5);
  });

  it("a duplicate membership row never draws a second tile", () => {
    const dup = payload({
      companies: [company("aaa", "AAA")],
      memberships: [mem("aaa", "t-grid", "Core"), mem("aaa", "t-grid", "Core")],
    });
    expect(buildTree(dup, opts()).tiles).toBe(1);
    expect(buildTree(dup, opts({ groupBy: "card" })).tiles).toBe(1);
  });

  it("an unnamed link is Unsegmented", () => {
    const d = payload({ companies: [company("aaa", "AAA")], memberships: [mem("aaa", "t-grid", null)] });
    expect(labels(buildTree(d, opts()).roots[0].children)).toEqual(["Unsegmented"]);
  });
});

describe("Context and Bench", () => {
  const data = twoCards();

  it("are out by default: no silent row, and a Context-only company is not in view", () => {
    for (const groupBy of ["card_link", "card", "sector", "all"] as const) {
      const t = buildTree(data, opts({ groupBy }));
      expect(tickers(t.roots)).not.toContain("CTX");
    }
    expect(buildTree(data, opts()).companies).toBe(5);
  });

  it("the toggle adds them as their own link box in Card > Link, a card member in Card, a company elsewhere", () => {
    const link = buildTree(data, opts({ includeSilent: true })).roots;
    expect(labels(find(link, "Grid").children).sort()).toEqual(["Cables", "Context", "Turbines"]);
    expect(tickers(find(link, "Grid").children)).toContain("CTX");
    const card = buildTree(data, opts({ includeSilent: true, groupBy: "card" })).roots;
    expect(tickers(find(card, "Grid").children)).toContain("CTX");
    const flat = buildTree(data, opts({ includeSilent: true, groupBy: "all" }));
    expect(tickers(flat.roots)).toContain("CTX");
    expect(flat.companies).toBe(6);
  });

  it("a company with a voting row and a silent row is in view either way, and its silent row only when asked", () => {
    const d = payload({
      companies: [company("aaa", "AAA")],
      memberships: [mem("aaa", "t-grid", "Core"), mem("aaa", "t-fuel", "Bench", { silent: true })],
    });
    expect(tickers(buildTree(d, opts({ groupBy: "card" })).roots)).toEqual(["AAA"]);
    expect(tickers(buildTree(d, opts({ groupBy: "card", includeSilent: true })).roots)).toEqual(["AAA", "AAA"]);
  });
});

// ---------------------------------------------------------------------------------------------------------
// weights
// ---------------------------------------------------------------------------------------------------------

describe("weights", () => {
  const data = twoCards();
  const flatTiles = (o: Partial<ViewOpts>) =>
    new Map(flattenTiles(buildTree(data, opts({ groupBy: "all", ...o })).roots).map((t) => [t.node.label, t.node]));

  it("size by market cap; a company with no cap is drawn at the smallest weight in view, dashed", () => {
    const t = flatTiles({});
    expect(t.get("AAA")!.weight).toBe(8e9);
    expect(t.get("DDD")!.weight).toBe(1e9);
    expect(t.get("EEE")!.weight).toBe(1e9); // the smallest cap in view
    expect(t.get("EEE")!.dashed).toBe(true);
    expect(t.get("DDD")!.dashed).toBe(false);
    const tree = buildTree(data, opts({ groupBy: "all" }));
    expect(tree.noWeight).toBe(1);
    expect(tree.minWeight).toBe(1e9);
  });

  it("the smallest weight is taken over the companies in view", () => {
    // the Context company's cap (3B) does not matter off, and a smaller one would on
    const d = twoCards();
    d.companies.find((c) => c.security_id === "ctx")!.cap = 5e8;
    expect(buildTree(d, opts({ groupBy: "all" })).minWeight).toBe(1e9);
    expect(buildTree(d, opts({ groupBy: "all", includeSilent: true })).minWeight).toBe(5e8);
  });

  it("when no company has a weight, every tile weighs 1 and is dashed", () => {
    const d = payload({
      companies: [company("a", "A", { cap: null }), company("b", "B", { cap: null })],
      memberships: [mem("a", "t-grid", "X"), mem("b", "t-grid", "X")],
    });
    const tiles = flattenTiles(buildTree(d, opts({ groupBy: "all" })).roots).map((t) => t.node);
    expect(tiles.map((n) => [n.weight, n.dashed])).toEqual([
      [1, true],
      [1, true],
    ]);
  });

  it("size by dollar volume uses the 20D figure, with the same smallest-weight rule", () => {
    const d = twoCards();
    d.companies.find((c) => c.security_id === "aaa")!.dollar_volume = 9e6;
    const t = new Map(
      flattenTiles(buildTree(d, opts({ groupBy: "all", sizeBy: "dollar_volume" })).roots).map((x) => [x.node.label, x.node]),
    );
    expect(t.get("AAA")!.weight).toBe(9e6);
    expect(t.get("BBB")!.weight).toBe(5e6);
    expect(t.get("EEE")!.dashed).toBe(true);
    expect(t.get("EEE")!.weight).toBe(5e6);
  });

  it("equal size weighs every tile 1 and dashes none", () => {
    const t = flatTiles({ sizeBy: "equal" });
    expect([...t.values()].every((n) => n.weight === 1 && !n.dashed)).toBe(true);
    expect(buildTree(data, opts({ sizeBy: "equal", groupBy: "all" })).noWeight).toBe(0);
  });

  it("a group box weighs the sum of its tiles", () => {
    const roots = buildTree(data, opts({ groupBy: "card" })).roots;
    expect(find(roots, "Grid").weight).toBe(8e9 + 4e9 + 2e9);
    expect(find(roots, "Fuel").weight).toBe(8e9 + 1e9 + 1e9);
  });

  it("rawWeight refuses a zero or negative figure", () => {
    expect(rawWeight(company("a", "A", { cap: 0 }), "cap")).toBeNull();
    expect(rawWeight(company("a", "A", { dollar_volume: -3 }), "dollar_volume")).toBeNull();
    expect(rawWeight(company("a", "A", { cap: null }), "equal")).toBe(1);
  });
});

// ---------------------------------------------------------------------------------------------------------
// medians
// ---------------------------------------------------------------------------------------------------------

describe("group medians", () => {
  it("need at least 3 priced names", () => {
    expect(MIN_PRICED).toBe(3);
    expect(groupMedian([1, 2])).toBeNull();
    expect(groupMedian([null, 4, undefined, 6])).toBeNull();
    expect(groupMedian([1, 9, 5])).toBe(5);
    expect(groupMedian([1, 3, 5, 100])).toBe(4); // the average of the two middle values
    expect(groupMedian([])).toBeNull();
  });

  it("a header shows its median only when 3 names are priced, counting each company once", () => {
    const data = twoCards();
    const grid = find(buildTree(data, opts({ groupBy: "card_link" })).roots, "Grid");
    // Grid's companies: AAA (in two links, counted once), BBB, CCC; 1d returns 1, 3, -2 -> median 1
    expect(grid.names).toBe(3);
    expect(grid.priced).toBe(3);
    expect(grid.value).toBe(1);
    // a link with one company has none; the Turbines link has AAA, BBB, CCC
    expect(find(grid.children, "Cables").value).toBeNull();
    expect(find(grid.children, "Turbines").value).toBe(1);
    // Fuel: AAA 1, DDD 5, EEE no tape -> 2 priced of 3 names: no median
    const fuel = find(buildTree(data, opts({ groupBy: "card_link" })).roots, "Fuel");
    expect([fuel.names, fuel.priced, fuel.value]).toEqual([3, 2, null]);
    expect(headerTitle(grid)).toBe("median of 3 priced names · 3 names");
    expect(headerTitle(fuel)).toBe("no median: 2 priced names, fewer than 3 · 3 names");
  });

  it("a name with no reading is left out of the median and the priced count, never zero-filled", () => {
    const d = payload({
      companies: [company("a", "A", {}, 4), company("b", "B", {}, 6), company("c", "C", {}, 8), company("d", "D", {}, null)],
      memberships: ["a", "b", "c", "d"].map((s) => mem(s, "t-grid", "X")),
    });
    const box = buildTree(d, opts({ groupBy: "card" })).roots[0];
    expect([box.names, box.priced, box.value]).toEqual([4, 3, 6]);
  });
});

// ---------------------------------------------------------------------------------------------------------
// color
// ---------------------------------------------------------------------------------------------------------

describe("the fixed color scale", () => {
  it("has the plan's end for each window", () => {
    expect(COLOR_END).toEqual({ ret_1d: 3, ret_7d: 8, ret_30d: 15, ret_90d: 25, ytd: 40, ret_1y: 50 });
    expect(WINDOWS.map((w) => w.key)).toEqual(["ret_1d", "ret_7d", "ret_30d", "ret_90d", "ytd", "ret_1y"]);
  });

  it("buckets a return into nine steps and stays at the end beyond it", () => {
    expect(colorStep(0, "ret_1d")).toBe(0);
    expect(colorStep(3, "ret_1d")).toBe(4);
    expect(colorStep(30, "ret_1d")).toBe(4);
    expect(colorStep(-3, "ret_1d")).toBe(-4);
    expect(colorStep(-99, "ret_1d")).toBe(-4);
    expect(colorStep(0.75, "ret_1d")).toBe(1); // 1/4 of the end
    expect(colorStep(1.5, "ret_1d")).toBe(2);
    expect(colorStep(2.25, "ret_1d")).toBe(3);
    expect(colorStep(0.3, "ret_1d")).toBe(0); // a quiet move stays gray
    // the same move reads differently in a longer window
    expect(colorStep(3, "ret_90d")).toBe(0);
    expect(colorStep(25, "ret_90d")).toBe(4);
    expect(colorStep(-20, "ytd")).toBe(-2);
  });

  it("a flat 0% is gray (never -0), and no reading is its own step", () => {
    expect(Object.is(colorStep(-0.01, "ret_1d"), 0)).toBe(true);
    expect(colorStep(null, "ret_1d")).toBeNull();
    expect(colorStep(undefined, "ret_7d")).toBeNull();
    expect(colorStep(Number.NaN, "ret_7d")).toBeNull();
    expect(stepClass(null)).toBe("mm-c-none");
    expect(stepClass(0)).toBe("mm-c-0");
    expect(stepClass(2)).toBe("mm-c-p2");
    expect(stepClass(-4)).toBe("mm-c-n4");
  });

  it("the legend lists the nine steps with their values for the window", () => {
    const l = legendSteps("ret_7d");
    expect(l.map((s) => s.step)).toEqual([-4, -3, -2, -1, 0, 1, 2, 3, 4]);
    expect(l.map((s) => s.label)).toEqual(["-8%", "-6%", "-4%", "-2%", "0%", "+2%", "+4%", "+6%", "+8%"]);
    expect(legendSteps("ret_1d").map((s) => s.label)).toContain("+0.75%");
  });
});

// ---------------------------------------------------------------------------------------------------------
// the benchmark
// ---------------------------------------------------------------------------------------------------------

describe("Compare to", () => {
  const data = twoCards();

  it("subtracts the benchmark's own return for the same window, everywhere", () => {
    expect(shown(3, 0.5)).toBe(2.5);
    expect(shown(null, 0.5)).toBeNull();
    expect(shown(3, undefined)).toBe(3); // raw
    expect(shown(3, null)).toBeNull(); // a benchmark without a reading: never a raw figure passed off as relative
    expect(benchReturn(data, "spy", "ytd")).toBe(4);
    expect(benchReturn(data, "none", "ytd")).toBeNull();

    const raw = new Map(flattenTiles(buildTree(data, opts({ groupBy: "all" })).roots).map((t) => [t.node.label, t.node.value]));
    const rel = new Map(
      flattenTiles(buildTree(data, opts({ groupBy: "all", compare: "spy" })).roots).map((t) => [t.node.label, t.node.value]),
    );
    expect(raw.get("BBB")).toBe(3);
    expect(rel.get("BBB")).toBe(2.5);
    expect(rel.get("CCC")).toBe(-2.5);
    expect(rel.get("EEE")).toBeNull(); // no tape stays no reading
    // the group median moves by the same subtraction
    const grid = find(buildTree(data, opts({ compare: "spy" })).roots, "Grid");
    expect(grid.value).toBe(0.5);
    // another window subtracts that window's benchmark return
    const ytd = flattenTiles(buildTree(data, opts({ groupBy: "all", compare: "spy", window: "ytd" })).roots);
    expect(ytd[0].node.value).toBe(5 - 4);
  });

  it("a benchmark with no tape, or no reading for the window, cannot be subtracted", () => {
    expect(compareAvailability(data, "none", "ret_1d")).toEqual({ ok: true, reason: null });
    expect(compareAvailability(data, "spy", "ret_1d").ok).toBe(true);
    expect(compareAvailability(data, "iwm", "ret_1d")).toEqual({ ok: false, reason: "no IWM tape at this as-of" });
    const thin = payload({ spy: { ret_1d: 1, ret_7d: 1, ret_30d: 1, ret_90d: 1, ytd: null, ret_1y: null } });
    expect(compareAvailability(thin, "spy", "ytd")).toEqual({ ok: false, reason: "SPY has no YTD reading" });
    // asked anyway, the tree shows no reading rather than a raw return
    const tree = buildTree(
      { ...thin, companies: [company("a", "A")], memberships: [mem("a", "t-grid", "X")] },
      opts({ groupBy: "all", compare: "spy", window: "ytd" }),
    );
    expect(flattenTiles(tree.roots)[0].node.value).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------------------
// Tiles: Groups
// ---------------------------------------------------------------------------------------------------------

describe("Tiles: Groups", () => {
  const data = twoCards();

  it("Card > Link: card boxes holding one tile per link, sized by the sum and colored by the median", () => {
    const tree = buildTree(data, opts({ tiles: "groups" }));
    expect(labels(tree.roots).sort()).toEqual(["Fuel", "Grid"]);
    const grid = find(tree.roots, "Grid");
    expect(grid.kind).toBe("group");
    expect(grid.children.every((n) => n.kind === "groupTile")).toBe(true);
    const turbines = find(grid.children, "Turbines");
    expect(turbines.weight).toBe(8e9 + 4e9 + 2e9);
    expect(turbines.value).toBe(1); // median of 1, 3, -2
    expect(turbines.members?.map((c) => c.ticker).sort()).toEqual(["AAA", "BBB", "CCC"]);
    expect(find(grid.children, "Cables").value).toBeNull(); // one company: below the floor
    expect(tree.tiles).toBe(3); // Turbines, Cables, Fuel/Core
  });

  it("Card: one tile per card", () => {
    const tree = buildTree(data, opts({ tiles: "groups", groupBy: "card" }));
    expect(tree.roots.every((n) => n.kind === "groupTile")).toBe(true);
    expect(tree.tiles).toBe(2);
    expect(find(tree.roots, "Grid").cardId).toBe("t-grid");
  });

  it("All companies: the whole set is one tile", () => {
    const tree = buildTree(data, opts({ tiles: "groups", groupBy: "all" }));
    expect(tree.tiles).toBe(1);
    expect(tree.roots[0].label).toBe("All companies");
    expect(tree.roots[0].names).toBe(5);
  });
});

// ---------------------------------------------------------------------------------------------------------
// the layout
// ---------------------------------------------------------------------------------------------------------

describe("layoutTree", () => {
  const ROOT: Rect = { x: 0, y: 0, w: 1000, h: 600 };
  const overlap = (a: Rect, b: Rect) => {
    const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    return w > 0.001 && h > 0.001 ? w * h : 0;
  };

  it("puts every tile inside the map and no two tiles on each other", () => {
    for (const groupBy of ["card_link", "card", "sector_type", "all"] as const) {
      const placed = layoutTree(buildTree(twoCards(), opts({ groupBy })).roots, ROOT);
      const tiles = placed.filter((p) => p.node.kind !== "group");
      expect(tiles.length).toBeGreaterThan(0);
      for (const t of tiles) {
        expect(t.rect.x).toBeGreaterThanOrEqual(-1e-6);
        expect(t.rect.y).toBeGreaterThanOrEqual(-1e-6);
        expect(t.rect.x + t.rect.w).toBeLessThanOrEqual(ROOT.w + 1e-6);
        expect(t.rect.y + t.rect.h).toBeLessThanOrEqual(ROOT.h + 1e-6);
      }
      for (let i = 0; i < tiles.length; i++) {
        for (let j = i + 1; j < tiles.length; j++) expect(overlap(tiles[i].rect, tiles[j].rect)).toBe(0);
      }
    }
  });

  it("keeps a group's tiles inside its box under the header strip", () => {
    const placed = layoutTree(buildTree(twoCards(), opts({ groupBy: "card" })).roots, ROOT);
    const grid = placed.find((p) => p.node.label === "Grid")!;
    expect(grid.header).toBe(true);
    for (const t of placed.filter((p) => p.node.kind === "tile" && p.node.cardId === "t-grid")) {
      expect(t.rect.y).toBeGreaterThanOrEqual(grid.rect.y + HEADER_H - 1e-6);
      expect(t.rect.x).toBeGreaterThanOrEqual(grid.rect.x);
      expect(t.rect.x + t.rect.w).toBeLessThanOrEqual(grid.rect.x + grid.rect.w + 1e-6);
    }
  });

  it("sizes sibling tiles by their weights", () => {
    const placed = layoutTree(buildTree(twoCards(), opts({ groupBy: "all" })).roots, ROOT);
    const area = (label: string) => {
      const r = placed.find((p) => p.node.label === label)!.rect;
      return r.w * r.h;
    };
    expect(area("AAA") / area("BBB")).toBeCloseTo(2, 2); // 8B : 4B
    expect(area("BBB") / area("CCC")).toBeCloseTo(2, 2);
    expect(area("DDD")).toBeCloseTo(area("EEE"), 4); // EEE is drawn at the smallest weight
  });

  it("shows no header on a box too small for one", () => {
    const placed = layoutTree(buildTree(twoCards(), opts({ groupBy: "card" })).roots, { x: 0, y: 0, w: 30, h: 30 });
    expect(placed.filter((p) => p.node.kind === "group").every((p) => !p.header)).toBe(true);
  });
});

describe("tileText", () => {
  it("scales with the tile: ticker and return, then ticker alone, then nothing", () => {
    const big = tileText(200, 120, 4, 6);
    expect(big).toMatchObject({ ticker: true, value: true });
    expect(big.fontPx).toBeGreaterThan(tileText(60, 40, 4, 6).fontPx);
    const mid = tileText(40, 16, 4, 6);
    expect(mid).toMatchObject({ ticker: true, value: false });
    expect(tileText(10, 8, 4, 6)).toEqual({ ticker: false, value: false, fontPx: 0 });
    expect(tileText(4000, 4000, 4, 6).fontPx).toBeLessThanOrEqual(26);
  });
});

// ---------------------------------------------------------------------------------------------------------
// zoom
// ---------------------------------------------------------------------------------------------------------

describe("zoom", () => {
  const roots = buildTree(twoCards(), opts()).roots;
  const grid = find(roots, "Grid");
  const turbines = find(grid.children, "Turbines");

  it("resolves a path of keys to a chain and makes the last group the only box", () => {
    expect(resolveZoom(roots, []).roots).toEqual(roots);
    const one = resolveZoom(roots, [grid.key]);
    expect(one.chain.map((n) => n.label)).toEqual(["Grid"]);
    expect(one.roots).toEqual([grid]);
    const two = resolveZoom(roots, [grid.key, turbines.key]);
    expect(two.chain.map((n) => n.label)).toEqual(["Grid", "Turbines"]);
    expect(two.roots).toEqual([turbines]);
  });

  it("a stale path, or a path into a tile, resolves to the longest prefix that still holds", () => {
    expect(resolveZoom(roots, ["card:gone"]).chain).toEqual([]);
    expect(resolveZoom(roots, [grid.key, "nope"]).chain.map((n) => n.label)).toEqual(["Grid"]);
    const tile = turbines.children[0];
    expect(resolveZoom(roots, [grid.key, turbines.key, tile.key]).chain.map((n) => n.label)).toEqual([
      "Grid",
      "Turbines",
    ]);
  });
});

// ---------------------------------------------------------------------------------------------------------
// the click rule
// ---------------------------------------------------------------------------------------------------------

describe("clickAction", () => {
  const data = twoCards();

  it("a tile in a card view opens that card's Cockpit on the ticker it carries there", () => {
    const tree = buildTree(data, opts({ groupBy: "card_link" }));
    const fuelAaa = flattenTiles(tree.roots).find((t) => t.trail[0] === "Fuel" && t.node.label === "AAA")!.node;
    expect(clickAction(fuelAaa, data, false)).toEqual({ kind: "open", thesisId: "t-fuel", ticker: "AAA.F" });
    const gridAaa = flattenTiles(tree.roots).find((t) => t.trail[0] === "Grid" && t.node.label === "AAA")!.node;
    expect(clickAction(gridAaa, data, false)).toEqual({ kind: "open", thesisId: "t-grid", ticker: "AAA" });
  });

  it("in a view with no card, a company on one card opens it and a company on several pins the info box", () => {
    const tree = buildTree(data, opts({ groupBy: "all" }));
    const tile = (label: string) => flattenTiles(tree.roots).find((t) => t.node.label === label)!.node;
    expect(clickAction(tile("BBB"), data, false)).toEqual({ kind: "open", thesisId: "t-grid", ticker: "BBB" });
    expect(clickAction(tile("AAA"), data, false)).toEqual({ kind: "pin" });
  });

  it("counts only the cards in view: a Context row on a second card does not make the company ambiguous", () => {
    const d = payload({
      companies: [company("a", "A")],
      memberships: [mem("a", "t-grid", "Core"), mem("a", "t-fuel", "Context", { silent: true })],
    });
    const node = flattenTiles(buildTree(d, opts({ groupBy: "all" })).roots)[0].node;
    expect(clickAction(node, d, false)).toEqual({ kind: "open", thesisId: "t-grid", ticker: "A" });
    expect(clickAction(node, d, true)).toEqual({ kind: "pin" });
  });

  it("a group tile under a card opens the card; one with no card pins", () => {
    const byCard = buildTree(data, opts({ tiles: "groups", groupBy: "card" })).roots[0];
    expect(clickAction(byCard, data, false)).toMatchObject({ kind: "open" });
    const bySector = buildTree(data, opts({ tiles: "groups", groupBy: "sector" })).roots[0];
    expect(clickAction(bySector, data, false)).toEqual({ kind: "pin" });
  });
});

// ---------------------------------------------------------------------------------------------------------
// the table
// ---------------------------------------------------------------------------------------------------------

describe("the table", () => {
  const data = twoCards();
  const rowsOf = (o: Partial<ViewOpts> = {}) => {
    const full = opts(o);
    return tableRows(data, buildTree(data, full).roots, full);
  };

  it("lists one row per tile with every window, the cap and the cards and links", () => {
    const rows = rowsOf();
    expect(rows).toHaveLength(7);
    const aaa = rows.filter((r) => r.label === "AAA");
    expect(aaa.map((r) => r.group).sort()).toEqual(["Fuel › Core", "Grid › Cables", "Grid › Turbines"]);
    const r = aaa[0];
    expect(Object.keys(r.values)).toEqual(WINDOWS.map((w) => w.key));
    expect(r.cap).toBe(8e9);
    expect(r.sector).toBe("Technology");
    expect(r.businessType).toBe("semis");
    expect(r.cards).toBe("Fuel (Core) · Grid (Turbines, Cables)");
    const eee = rows.find((x) => x.label === "EEE")!;
    expect([eee.cap, eee.sector, eee.values.ret_1d]).toEqual([null, "Unclassified", null]);
  });

  it("subtracts the benchmark in every window it has a reading for", () => {
    const rows = rowsOf({ compare: "spy" });
    const bbb = rows.find((r) => r.label === "BBB")!;
    expect(bbb.values.ret_1d).toBe(2.5);
    expect(bbb.values.ytd).toBe(1);
  });

  it("Tiles: Groups lists the group tiles with their medians per window", () => {
    const rows = rowsOf({ tiles: "groups" });
    const turb = rows.find((r) => r.label === "Turbines")!;
    expect(turb.kind).toBe("group");
    expect(turb.values.ret_1d).toBe(1);
    expect(turb.sub).toBe("3 companies");
    expect(rows.find((r) => r.label === "Cables")!.values.ret_1d).toBeNull();
  });

  it("sorts every column with no value last in both directions", () => {
    const rows = rowsOf({ groupBy: "all" });
    const order = (sort: Parameters<typeof sortRows>[1]) => sortRows(rows, sort).map((r) => r.label);
    expect(order({ col: "ret_1d", dir: "desc" })).toEqual(["DDD", "BBB", "AAA", "CCC", "EEE"]);
    expect(order({ col: "ret_1d", dir: "asc" })).toEqual(["CCC", "AAA", "BBB", "DDD", "EEE"]);
    expect(order({ col: "cap", dir: "desc" })).toEqual(["AAA", "BBB", "CCC", "DDD", "EEE"]);
    expect(order({ col: "cap", dir: "asc" })).toEqual(["DDD", "CCC", "BBB", "AAA", "EEE"]);
    expect(order({ col: "label", dir: "desc" })).toEqual(["EEE", "DDD", "CCC", "BBB", "AAA"]);
    expect(order({ col: "businessType", dir: "asc" })).toEqual(["AAA", "EEE", "BBB", "CCC", "DDD"]); // semis, unclassified, then utilities
  });

  it("a header click flips the same column and starts a new one in a sensible direction", () => {
    expect(nextSort({ col: "ret_1d", dir: "desc" }, "ret_1d")).toEqual({ col: "ret_1d", dir: "asc" });
    expect(nextSort({ col: "ret_1d", dir: "asc" }, "ret_1d")).toEqual({ col: "ret_1d", dir: "desc" });
    expect(nextSort({ col: "ret_1d", dir: "desc" }, "cap")).toEqual({ col: "cap", dir: "desc" });
    expect(nextSort({ col: "cap", dir: "desc" }, "label")).toEqual({ col: "label", dir: "asc" });
  });
});
