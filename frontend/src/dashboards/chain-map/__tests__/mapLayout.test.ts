import { describe, expect, it } from "vitest";

import {
  BRACKET_OFFSET,
  CHAIN_PAD,
  COL_GAP,
  COL_W,
  EDGE_W,
  NODE_GAP,
  NODE_H,
  NODE_W,
  PAD,
  STACK_W,
  STEP,
  clip,
  cubic,
  cubicMid,
  layoutMap,
  moveFocus,
  sideGlyph,
  type Group,
  type MapShape,
} from "../mapLayout";
import { COLUMNS, EDGES, RAILS, column, edgesOf, node, rail } from "./fixtures";

// The Chain Map's pure layout on hand-built payloads: equal column widths and node sizes, the three zones of the
// wide frame and the three stacked ones, the brackets, one edge per rail node landing on its bracket at distinct,
// ordered points, the Context/Bench columns only with the toggle, the keyboard groups and focus moves.

const three = ["A", "B", "C"].map((t) => node(t, { key: `a-${t}` }));
const one = [node("D", { key: "b-D" })];

/** Two real columns (3 and 1 names), a left rail of 2 and a right rail of 1, all hand-placed. */
function shape(): MapShape {
  const left = rail("left", [
    node("PAY", { key: "rail:left|PAY", role: "payer" }),
    node("FIN", { key: "rail:left|FIN", role: "financier" }),
  ]);
  const right = rail("right", [node("CTR", { key: "rail:right|CTR", role: "counter_case" })]);
  return {
    columns: [column("link:One", "link", "One", three), column("link:Two", "link", "Two", one)],
    rails: [left, right],
    edges: edgesOf([left, right]),
  };
}

describe("the wide layout with no rails", () => {
  const data: MapShape = { ...shape(), rails: [rail("left", []), rail("right", [])], edges: [] };
  const l = layoutMap(data, { mode: "wide", showUntagged: false });

  it("puts equal-width columns side by side, the chain at the left edge with no rail zones", () => {
    expect(l.zones.left).toBeNull();
    expect(l.zones.right).toBeNull();
    expect(l.columns.map((c) => [c.key, c.x, c.w])).toEqual([
      ["link:One", CHAIN_PAD, COL_W],
      ["link:Two", CHAIN_PAD + COL_W + COL_GAP, COL_W],
    ]);
    // two columns: 2 × 148 + 8 + 2 × 10
    expect(l.zones.chain).toEqual({ x: 0, y: 0, w: 324, h: l.height });
    expect(l.width).toBe(324);
  });

  it("stacks every node at one size, one step apart, from the same top", () => {
    expect(l.nodes.every((n) => n.w === NODE_W && n.h === NODE_H)).toBe(true);
    expect(STEP).toBe(NODE_H + NODE_GAP);
    const first = l.nodes.filter((n) => n.group === "link:One");
    expect(first.map((n) => [n.x, n.y])).toEqual([
      [16, 68],
      [16, 118],
      [16, 168],
    ]);
    expect(l.nodes.find((n) => n.key === "b-D")).toMatchObject({ x: 172, y: 68 });
    // three rows: the last node ends at 68 + 3 × 50 − 6 = 212, and the frame closes PAD below it
    expect(l.height).toBe(212 + PAD);
    // every column is as tall as the tallest, so the columns read as columns
    expect(new Set(l.columns.map((c) => c.h)).size).toBe(1);
    expect(l.columns.map((c) => c.count)).toEqual([3, 1]);
    expect(l.edges).toEqual([]);
    expect(l.brackets).toEqual([]);
  });
});

describe("the wide layout with rails", () => {
  const data = shape();
  const l = layoutMap(data, { mode: "wide", showUntagged: false });
  const lw = PAD + NODE_W + EDGE_W; // 232
  const cw = 324;

  it("pins a rail zone on each side of the chain zone, the frame their sum", () => {
    expect(l.zones.left).toEqual({ x: 0, y: 0, w: lw, h: l.height });
    expect(l.zones.chain).toEqual({ x: lw, y: 0, w: cw, h: l.height });
    expect(l.zones.right).toEqual({ x: lw + cw, y: 0, w: EDGE_W + NODE_W + PAD, h: l.height });
    expect(l.width).toBe(lw + cw + EDGE_W + NODE_W + PAD);
    expect(l.width).toBe(788);
    // the chain's first column starts after the left zone and its padding
    expect(l.columns[0].x).toBe(lw + CHAIN_PAD);
  });

  it("starts the rails' nodes at the chain's top row, in the rails' own columns", () => {
    const byKey = Object.fromEntries(l.nodes.map((n) => [n.key, n]));
    expect(byKey["rail:left|PAY"]).toMatchObject({ x: PAD, y: 68, zone: "left", group: "rail:left" });
    expect(byKey["rail:left|FIN"]).toMatchObject({ x: PAD, y: 118 });
    expect(byKey["rail:right|CTR"]).toMatchObject({ x: lw + cw + EDGE_W, y: 68, zone: "right", group: "rail:right" });
    expect(l.nodes.filter((n) => n.zone === "chain")).toHaveLength(4);
    expect(l.rails.map((r) => [r.side, r.label, r.count, r.x])).toEqual([
      ["left", "what pays and funds", 2, PAD],
      ["right", "what reads across", 1, lw + cw + EDGE_W],
    ]);
  });

  it("draws a bracket on each side of the chain, as tall as the chain, ticks pointing in", () => {
    // the chain's names run from y 68 to 212
    expect(l.brackets).toEqual([
      { side: "left", d: `M${lw - BRACKET_OFFSET + 7} 68H${lw - BRACKET_OFFSET}V212H${lw - BRACKET_OFFSET + 7}` },
      {
        side: "right",
        d: `M${lw + cw + BRACKET_OFFSET - 7} 68H${lw + cw + BRACKET_OFFSET}V212H${lw + cw + BRACKET_OFFSET - 7}`,
      },
    ]);
  });

  it("gives every rail node ONE edge that lands on its bracket at its own point, in the node order", () => {
    expect(l.edges.map((e) => e.nodeKey)).toEqual(["rail:left|PAY", "rail:left|FIN", "rail:right|CTR"]);
    const [pay, fin, ctr] = l.edges;
    // the bracket spans y 68 → 212 (144): two edges land a quarter and three quarters down, one lands at the middle
    expect(pay.end).toEqual({ x: lw - BRACKET_OFFSET, y: 104 });
    expect(fin.end).toEqual({ x: lw - BRACKET_OFFSET, y: 176 });
    expect(ctr.end).toEqual({ x: lw + cw + BRACKET_OFFSET, y: 140 });
    // an edge leaves its node on the side facing the chain
    expect(pay.start).toEqual({ x: PAD + NODE_W, y: 68 + NODE_H / 2 });
    expect(fin.start).toEqual({ x: PAD + NODE_W, y: 118 + NODE_H / 2 });
    expect(ctr.start).toEqual({ x: lw + cw + EDGE_W, y: 68 + NODE_H / 2 });
    // distinct landings that keep the node order: the curves never cross
    expect(pay.end.y).toBeLessThan(fin.end.y);
    // a cubic path from the start to the end
    expect(pay.d).toBe(`M148 90 C185 90, 185 104, 222 104`);
    expect(ctr.d.startsWith("M")).toBe(true);
    expect(l.edges.map((e) => [e.role, e.side, e.dashed])).toEqual([
      ["payer", "left", false],
      ["financier", "left", false],
      ["counter_case", "right", true],
    ]);
  });

  it("labels each role once per rail, at the middle of the middle edge of its run", () => {
    expect(l.edgeLabels.map((x) => [x.role, x.side])).toEqual([
      ["payer", "left"],
      ["financier", "left"],
      ["counter_case", "right"],
    ]);
    const pay = l.edges[0];
    // the middle of the curve (t = 0.5) of a symmetric S-curve is halfway between its ends, 4 above the line
    expect(l.edgeLabels[0].x).toBe((pay.start.x + pay.end.x) / 2);
    expect(l.edgeLabels[0].y).toBe((pay.start.y + pay.end.y) / 2 - 4);
  });

  it("keeps the brackets chain-tall when a rail is taller than the chain, and makes the frame fit the rail", () => {
    const long = rail(
      "left",
      Array.from({ length: 6 }, (_, i) => node(`P${i}`, { key: `rail:left|P${i}`, role: "payer" })),
    );
    const d = layoutMap(
      { columns: [column("link:One", "link", "One", one)], rails: [long, rail("right", [])], edges: edgesOf([long]) },
      { mode: "wide", showUntagged: false },
    );
    // one chain row: the bracket is one node tall (68 → 112); six rail nodes: the frame is tall enough for them
    expect(d.brackets[0].d).toContain("68H");
    expect(d.brackets[0].d).toContain("V112H");
    expect(d.height).toBe(68 + 6 * STEP - NODE_GAP + PAD);
    expect(d.edges.every((e) => e.end.y >= 68 && e.end.y <= 112)).toBe(true);
    expect(new Set(d.edges.map((e) => e.end.y)).size).toBe(6);
    // a rail with no names has no zone, no bracket, no edge
    expect(d.zones.right).toBeNull();
    expect(d.brackets.map((b) => b.side)).toEqual(["left"]);
  });

  it("is a pure function of its input", () => {
    const frozen = JSON.stringify(data);
    expect(layoutMap(data, { mode: "wide", showUntagged: false })).toEqual(l);
    expect(JSON.stringify(data)).toBe(frozen);
  });
});

describe("the columns", () => {
  it("draws the Context and Bench columns only with the toggle; links, old links and No link always", () => {
    const data: MapShape = {
      columns: [
        column("link:A", "link", "A", one),
        column("link:Empty", "link", "Empty", []),
        column("old:Old", "old_link", "Old", [node("O", { key: "o" })]),
        column("no_link", "no_link", "No link", [node("N", { key: "n" })]),
        column("silent:Context", "silent", "Context", [node("C", { key: "c" })]),
        column("silent:Bench", "silent", "Bench", [node("B", { key: "bn" })]),
      ],
      rails: [rail("left", []), rail("right", [])],
      edges: [],
    };
    const off = layoutMap(data, { mode: "wide", showUntagged: false });
    const on = layoutMap(data, { mode: "wide", showUntagged: true });
    expect(off.columns.map((c) => c.key)).toEqual(["link:A", "link:Empty", "old:Old", "no_link"]);
    expect(on.columns.map((c) => c.key)).toEqual([
      "link:A",
      "link:Empty",
      "old:Old",
      "no_link",
      "silent:Context",
      "silent:Bench",
    ]);
    // an empty real link is a column with 0 names and the same width
    expect(off.columns[1]).toMatchObject({ count: 0, w: COL_W });
    expect(off.nodes.map((n) => n.key)).toEqual(["b-D", "o", "n"]);
    expect(on.nodes.map((n) => n.key)).toEqual(["b-D", "o", "n", "c", "bn"]);
    expect(on.width).toBeGreaterThan(off.width);
  });

  it("is a single empty column wide when the card has no links and no names", () => {
    const l = layoutMap({ columns: [], rails: [], edges: [] }, { mode: "wide", showUntagged: false });
    expect(l.columns).toEqual([]);
    expect(l.nodes).toEqual([]);
    expect(l.zones.chain.w).toBe(2 * CHAIN_PAD + COL_W);
    expect(l.groups).toEqual([]);
  });

  it("lays out the page's own payload: four columns, four rail names, four edges", () => {
    const l = layoutMap({ columns: COLUMNS, rails: RAILS, edges: EDGES }, { mode: "wide", showUntagged: false });
    expect(l.columns.map((c) => c.key)).toEqual(["link:Reactors", "link:Fuel", "old:Old wires"]);
    expect(l.edges).toHaveLength(4);
    expect(l.edges.filter((e) => e.dashed).map((e) => e.nodeKey)).toEqual(["k-CNTR"]);
    expect(layoutMap({ columns: COLUMNS, rails: RAILS, edges: EDGES }, { mode: "wide", showUntagged: true }).columns).toHaveLength(4);
  });

  it("draws no edge for a rail node that has none", () => {
    const l = layoutMap({ ...shape(), edges: [] }, { mode: "wide", showUntagged: false });
    expect(l.edges).toEqual([]);
    expect(l.nodes.filter((n) => n.zone !== "chain")).toHaveLength(3);
  });
});

describe("the stacked layout", () => {
  const l = layoutMap(shape(), { mode: "stacked", showUntagged: false });

  it("stacks the left rail above the chain and the right rail below it, the frame their sum", () => {
    // left: nodes at 42 and 92, its bracket at 152, closed at 165; chain: 165 → 389; right: bracket at 401, nodes from 445
    expect(l.zones.left).toEqual({ x: 0, y: 0, w: STACK_W, h: 165 });
    expect(l.zones.chain).toEqual({ x: 0, y: 165, w: 324, h: 224 });
    expect(l.zones.right).toEqual({ x: 0, y: 389, w: STACK_W, h: 112 });
    expect(l.height).toBe(501);
    expect(l.width).toBe(STACK_W);
    expect(l.mode).toBe("stacked");
  });

  it("places the rail nodes in a list at the left and the chain under the left rail's zone", () => {
    const byKey = Object.fromEntries(l.nodes.map((n) => [n.key, n]));
    expect(byKey["rail:left|PAY"]).toMatchObject({ x: PAD, y: 42, zone: "left" });
    expect(byKey["rail:left|FIN"]).toMatchObject({ x: PAD, y: 92 });
    expect(byKey["rail:right|CTR"]).toMatchObject({ x: PAD, y: 445, zone: "right" });
    expect(l.columns[0]).toMatchObject({ x: CHAIN_PAD, y: 173 });
    expect(byKey["a-A"]).toMatchObject({ x: 16, y: 233 });
  });

  it("lands the edges on a horizontal bracket, nested so they never cross, with no edge labels", () => {
    expect(l.brackets).toEqual([
      { side: "left", d: "M12 159V152H328V159" },
      { side: "right", d: "M12 394V401H328V394" },
    ]);
    const [pay, fin, ctr] = l.edges;
    expect([pay.end.y, fin.end.y]).toEqual([152, 152]);
    expect(ctr.end.y).toBe(401);
    // the top node of the upper rail lands furthest right, the top node of the lower rail furthest left
    expect(pay.end.x).toBeCloseTo(282.5, 5);
    expect(fin.end.x).toBeCloseTo(203.5, 5);
    expect(pay.end.x).toBeGreaterThan(fin.end.x);
    expect(ctr.end.x).toBeCloseTo(243, 5);
    // every landing sits to the right of the node column, within the bracket
    expect([pay, fin, ctr].every((e) => e.end.x > PAD + NODE_W && e.end.x < STACK_W - PAD)).toBe(true);
    expect(pay.start).toEqual({ x: PAD + NODE_W, y: 64 });
    expect(l.edgeLabels).toEqual([]);
  });

  it("keeps the same keyboard groups as the wide layout", () => {
    expect(l.groups.map((g) => g.id)).toEqual(layoutMap(shape(), { mode: "wide", showUntagged: false }).groups.map((g) => g.id));
  });

  it("has no zone for a rail with no names, and is only the chain with none", () => {
    const bare = layoutMap({ ...shape(), rails: [rail("left", []), rail("right", [])], edges: [] }, { mode: "stacked", showUntagged: false });
    expect(bare.zones.left).toBeNull();
    expect(bare.zones.right).toBeNull();
    expect(bare.zones.chain.y).toBe(0);
    expect(bare.height).toBe(212 + PAD);
  });
});

describe("the keyboard groups and focus moves", () => {
  const groups: Group[] = [
    { id: "rail:left", keys: ["L1", "L2"] },
    { id: "c1", keys: ["a", "b", "c"] },
    { id: "c2", keys: ["d"] },
    { id: "rail:right", keys: ["R1", "R2"] },
  ];

  it("orders the groups left rail, columns left to right, right rail, and omits empty ones", () => {
    const l = layoutMap(shape(), { mode: "wide", showUntagged: false });
    expect(l.groups.map((g) => [g.id, g.keys])).toEqual([
      ["rail:left", ["rail:left|PAY", "rail:left|FIN"]],
      ["link:One", ["a-A", "a-B", "a-C"]],
      ["link:Two", ["b-D"]],
      ["rail:right", ["rail:right|CTR"]],
    ]);
    const bare = layoutMap({ columns: [column("link:E", "link", "E", [])], rails: [], edges: [] }, { mode: "wide", showUntagged: false });
    expect(bare.groups).toEqual([]);
  });

  it("moves up and down inside a group and stops at its ends", () => {
    expect(moveFocus(groups, "b", "down")).toBe("c");
    expect(moveFocus(groups, "c", "down")).toBe("c");
    expect(moveFocus(groups, "b", "up")).toBe("a");
    expect(moveFocus(groups, "a", "up")).toBe("a");
  });

  it("steps to the neighboring group on the same row, or its last name, and stops at the ends", () => {
    expect(moveFocus(groups, "c", "right")).toBe("d"); // row 2 of a one-name column: its only name
    expect(moveFocus(groups, "b", "left")).toBe("L2");
    expect(moveFocus(groups, "a", "left")).toBe("L1");
    expect(moveFocus(groups, "L1", "left")).toBe("L1");
    expect(moveFocus(groups, "R2", "right")).toBe("R2");
    expect(moveFocus(groups, "d", "right")).toBe("R1");
  });

  it("falls back to the first name for an unknown key, and to the key itself with no groups", () => {
    expect(moveFocus(groups, "gone", "down")).toBe("L1");
    expect(moveFocus([], "x", "down")).toBe("x");
  });
});

describe("the small helpers", () => {
  it("clip cuts to the length with an ellipsis and leaves a fitting text alone", () => {
    expect(clip("Reactors", 8)).toBe("Reactors");
    expect(clip("Reactor developers", 8)).toBe("Reactor…");
    expect(clip("ab cdef", 3)).toBe("ab…");
    expect(clip("", 5)).toBe("");
  });

  it("draws the 50-day mark as a triangle up, a triangle down or a flat bar", () => {
    expect(sideGlyph("above", 10, 10)).toBe("M10 6L14 13L6 13Z");
    expect(sideGlyph("below", 10, 10)).toBe("M10 14L14 7L6 7Z");
    expect(sideGlyph("at", 10, 10)).toBe("M6 10H14");
  });

  it("finds the middle of a cubic on hand-computed numbers", () => {
    const a = { x: 0, y: 0 };
    const b = { x: 10, y: 0 };
    const c = { x: 0, y: 10 };
    const d = { x: 10, y: 10 };
    expect(cubicMid(a, b, c, d)).toEqual({ x: 5, y: 5 });
    expect(cubic(a, b, c, d)).toBe("M0 0 C10 0, 0 10, 10 10");
    // a straight line's middle is the middle of its ends
    expect(cubicMid({ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 }, { x: 3, y: 3 })).toEqual({ x: 1.5, y: 1.5 });
  });
});
