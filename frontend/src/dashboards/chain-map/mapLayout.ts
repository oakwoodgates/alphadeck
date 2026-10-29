import type { ChainMapColumnOut, ChainMapEdgeOut, ChainMapNodeOut, ChainMapRailOut } from "./useChainMap";

// The Chain Map's pure layout (docs/dashboards/chain-map.md): where every column, node, rail, bracket and edge
// sits, computed from the page's payload and two options. No graph library: the picture is plain SVG shapes at
// the positions below. Every node is the same size and every column the same width (the only visual weight is
// the count of nodes in a column). Everything here is in ONE global frame; the page draws it as three SVGs
// (the left rail, the chain, the right rail), each showing its slice of the frame as its viewBox, so the rails
// stay in view while the chain scrolls sideways between them. On a narrow screen the same three slices stack:
// the left rail above the chain, the right rail below it.

export const NODE_W = 136;
export const NODE_H = 44;
export const NODE_GAP = 6;
export const STEP = NODE_H + NODE_GAP;
export const COL_PAD = 6;
export const COL_W = NODE_W + 2 * COL_PAD;
export const COL_GAP = 8;
export const CHAIN_PAD = 10;
export const HEAD_H = 54;
export const PAD = 12;
export const TOP = 8;
/** Wide: the width of the gap an edge crosses, from a rail node to the chain's bracket, bracket included. */
export const EDGE_W = 84;
/** The bracket's bar sits this far inside its rail zone, from the chain's edge. */
export const BRACKET_OFFSET = 10;
export const BRACKET_TICK = 7;
/** Stacked: the width of the rail zones (they never scroll on a phone; the chain does). */
export const STACK_W = 340;

const BODY_TOP = TOP + HEAD_H + 6;

export type Mode = "wide" | "stacked";
export type ZoneId = "left" | "chain" | "right";
export type Side = "left" | "right";
export type Rect = { x: number; y: number; w: number; h: number };
export type Point = { x: number; y: number };

/** The payload parts the layout reads. */
export type MapShape = {
  columns: readonly ChainMapColumnOut[];
  rails: readonly ChainMapRailOut[];
  edges: readonly ChainMapEdgeOut[];
};
export type LayoutOptions = {
  mode: Mode;
  /** Draw the quiet Context and Bench columns (the untagged silent names). */
  showUntagged: boolean;
};

export type PlacedNode = Rect & {
  key: string;
  node: ChainMapNodeOut;
  zone: ZoneId;
  /** The keyboard group it belongs to: its column's key, or `rail:left` / `rail:right`. */
  group: string;
};
export type PlacedColumn = Rect & {
  key: string;
  kind: ChainMapColumnOut["kind"];
  label: string;
  descriptor: string | null;
  count: number;
};
/** A rail's heading: its label at (x, y), its name count on the line under it (y of that line: `countY`). */
export type PlacedRailHead = { side: Side; label: string; count: number; x: number; y: number; countY: number };
export type PlacedEdge = {
  nodeKey: string;
  role: ChainMapEdgeOut["role"];
  side: Side;
  /** A counter-case edge is dashed. */
  dashed: boolean;
  /** The cubic curve, as an SVG path. */
  d: string;
  start: Point;
  /** Where it lands on the bracket (the page draws a small end mark here). */
  end: Point;
};
export type EdgeLabel = { role: ChainMapEdgeOut["role"]; side: Side; x: number; y: number };
export type Bracket = { side: Side; d: string };
export type Group = { id: string; keys: string[] };

export type MapLayout = {
  mode: Mode;
  width: number;
  height: number;
  /** The slices of the frame, one SVG each; a rail with no names has no zone. */
  zones: { left: Rect | null; chain: Rect; right: Rect | null };
  columns: PlacedColumn[];
  nodes: PlacedNode[];
  rails: PlacedRailHead[];
  edges: PlacedEdge[];
  /** One role label per role per rail, wide layout only (a stacked rail node carries its own role tag). */
  edgeLabels: EdgeLabel[];
  brackets: Bracket[];
  /** The keyboard groups in order: the left rail, the columns left to right, the right rail (empty ones omitted). */
  groups: Group[];
};

// --- small pure helpers --------------------------------------------------------------------------------------

const r1 = (n: number) => Math.round(n * 10) / 10;

/** A cubic curve as an SVG path. */
export function cubic(p0: Point, p1: Point, p2: Point, p3: Point): string {
  return `M${r1(p0.x)} ${r1(p0.y)} C${r1(p1.x)} ${r1(p1.y)}, ${r1(p2.x)} ${r1(p2.y)}, ${r1(p3.x)} ${r1(p3.y)}`;
}

/** The point halfway along a cubic (t = 0.5). */
export function cubicMid(p0: Point, p1: Point, p2: Point, p3: Point): Point {
  return { x: (p0.x + 3 * p1.x + 3 * p2.x + p3.x) / 8, y: (p0.y + 3 * p1.y + 3 * p2.y + p3.y) / 8 };
}

/** Text cut to `max` characters with an ellipsis (the full text rides in the tooltip). */
export function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/** The small mark for the 50-day side, centered on (cx, cy): a triangle up, a triangle down, or a flat bar. */
export function sideGlyph(side: "above" | "below" | "at", cx: number, cy: number): string {
  if (side === "above") return `M${cx} ${cy - 4}L${cx + 4} ${cy + 3}L${cx - 4} ${cy + 3}Z`;
  if (side === "below") return `M${cx} ${cy + 4}L${cx + 4} ${cy - 3}L${cx - 4} ${cy - 3}Z`;
  return `M${cx - 4} ${cy}H${cx + 4}`;
}

// --- the chain: columns and their nodes ---------------------------------------------------------------------

function chainWidth(n: number): number {
  return n === 0 ? 2 * CHAIN_PAD + COL_W : n * COL_W + (n - 1) * COL_GAP + 2 * CHAIN_PAD;
}

/** The columns and their nodes, from `x0` (the left edge of the first column) and `y0` (the frame's top). */
function placeChain(cols: readonly ChainMapColumnOut[], x0: number, y0: number) {
  const rows = Math.max(1, ...cols.map((c) => c.nodes.length));
  const bottom = y0 + BODY_TOP + rows * STEP - NODE_GAP;
  const columns: PlacedColumn[] = [];
  const nodes: PlacedNode[] = [];
  cols.forEach((c, i) => {
    const x = x0 + i * (COL_W + COL_GAP);
    columns.push({
      key: c.key,
      kind: c.kind,
      label: c.label,
      descriptor: c.descriptor,
      count: c.nodes.length,
      x,
      y: y0 + TOP,
      w: COL_W,
      h: bottom - (y0 + TOP) + 6,
    });
    c.nodes.forEach((node, j) =>
      nodes.push({
        key: node.key,
        node,
        zone: "chain",
        group: c.key,
        x: x + COL_PAD,
        y: y0 + BODY_TOP + j * STEP,
        w: NODE_W,
        h: NODE_H,
      }),
    );
  });
  return { columns, nodes, bottom, top: y0 + BODY_TOP };
}

function railOf(rails: readonly ChainMapRailOut[], side: Side): ChainMapRailOut {
  return rails.find((r) => r.side === side) ?? { side, label: "", nodes: [] };
}

function groupsOf(nodes: readonly PlacedNode[], order: readonly string[]): Group[] {
  return order
    .map((id) => ({ id, keys: nodes.filter((n) => n.group === id).map((n) => n.key) }))
    .filter((g) => g.keys.length > 0);
}

/** The edges of one rail, one per rail node that has one, in rail order; `route` supplies each node's curve. */
function edgesOf(
  rail: ChainMapRailOut,
  edges: readonly ChainMapEdgeOut[],
  route: (i: number, n: number, node: ChainMapNodeOut) => { start: Point; end: Point; d: string; mid: Point },
): (PlacedEdge & { mid: Point })[] {
  const byNode = new Map(edges.map((e) => [e.node_key, e]));
  const out: (PlacedEdge & { mid: Point })[] = [];
  rail.nodes.forEach((node, i) => {
    const edge = byNode.get(node.key);
    if (!edge) return;
    out.push({
      nodeKey: node.key,
      role: edge.role,
      side: rail.side,
      dashed: edge.role === "counter_case",
      ...route(i, rail.nodes.length, node),
    });
  });
  return out;
}

/** One label per role per rail, at the middle of the middle edge of that role's run. */
function labelsOf(placed: readonly (PlacedEdge & { mid: Point })[]): EdgeLabel[] {
  const out: EdgeLabel[] = [];
  const seen = new Set<string>();
  for (const e of placed) {
    const key = `${e.side}|${e.role}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const run = placed.filter((p) => p.side === e.side && p.role === e.role);
    const mid = run[Math.floor((run.length - 1) / 2)].mid;
    out.push({ role: e.role, side: e.side, x: r1(mid.x), y: r1(mid.y - 4) });
  }
  return out;
}

const strip = ({ mid: _mid, ...edge }: PlacedEdge & { mid: Point }): PlacedEdge => edge;

// --- the two layouts -----------------------------------------------------------------------------------------

function wide(cols: readonly ChainMapColumnOut[], data: MapShape): MapLayout {
  const L = railOf(data.rails, "left");
  const R = railOf(data.rails, "right");
  const hasL = L.nodes.length > 0;
  const hasR = R.nodes.length > 0;
  const lw = hasL ? PAD + NODE_W + EDGE_W : 0;
  const cw = chainWidth(cols.length);
  const rw = hasR ? EDGE_W + NODE_W + PAD : 0;
  const chain = placeChain(cols, lw + CHAIN_PAD, 0);
  const railBottom = (n: number) => BODY_TOP + n * STEP - NODE_GAP;
  const height =
    Math.max(chain.bottom, hasL ? railBottom(L.nodes.length) : 0, hasR ? railBottom(R.nodes.length) : 0) + PAD;
  const y1 = chain.top;
  const y2 = chain.bottom;

  const nodes: PlacedNode[] = [...chain.nodes];
  const heads: PlacedRailHead[] = [];
  const brackets: Bracket[] = [];
  const placed: (PlacedEdge & { mid: Point })[] = [];

  const rail = (r: ChainMapRailOut, side: Side, nodeX: number, barX: number) => {
    r.nodes.forEach((node, i) =>
      nodes.push({ key: node.key, node, zone: side, group: `rail:${side}`, x: nodeX, y: BODY_TOP + i * STEP, w: NODE_W, h: NODE_H }),
    );
    heads.push({ side, label: r.label, count: r.nodes.length, x: nodeX, y: TOP + 14, countY: TOP + 27 });
    const tick = side === "left" ? BRACKET_TICK : -BRACKET_TICK;
    brackets.push({ side, d: `M${barX + tick} ${y1}H${barX}V${y2}H${barX + tick}` });
    placed.push(
      ...edgesOf(r, data.edges, (i, n) => {
        const start = { x: side === "left" ? nodeX + NODE_W : nodeX, y: BODY_TOP + i * STEP + NODE_H / 2 };
        const end = { x: barX, y: y1 + ((i + 0.5) / n) * (y2 - y1) };
        const dx = end.x - start.x;
        const c1 = { x: start.x + dx / 2, y: start.y };
        const c2 = { x: end.x - dx / 2, y: end.y };
        return { start, end, d: cubic(start, c1, c2, end), mid: cubicMid(start, c1, c2, end) };
      }),
    );
  };
  if (hasL) rail(L, "left", PAD, lw - BRACKET_OFFSET);
  if (hasR) rail(R, "right", lw + cw + EDGE_W, lw + cw + BRACKET_OFFSET);

  return {
    mode: "wide",
    width: lw + cw + rw,
    height,
    zones: {
      left: hasL ? { x: 0, y: 0, w: lw, h: height } : null,
      chain: { x: lw, y: 0, w: cw, h: height },
      right: hasR ? { x: lw + cw, y: 0, w: rw, h: height } : null,
    },
    columns: chain.columns,
    nodes,
    rails: heads,
    edges: placed.map(strip),
    edgeLabels: labelsOf(placed),
    brackets,
    groups: groupsOf(nodes, ["rail:left", ...cols.map((c) => c.key), "rail:right"]),
  };
}

function stacked(cols: readonly ChainMapColumnOut[], data: MapShape): MapLayout {
  const L = railOf(data.rails, "left");
  const R = railOf(data.rails, "right");
  const hasL = L.nodes.length > 0;
  const hasR = R.nodes.length > 0;
  const cw = chainWidth(cols.length);
  const bx1 = PAD;
  const bx2 = STACK_W - PAD;
  // edges leave a rail node on its right and land on the bracket to the right of the node column
  const lo = PAD + NODE_W + 16;
  const hi = bx2 - 6;

  // the left rail above the chain: a vertical list, its bracket under it
  const headY = 16;
  const topNodes = 42;
  const topBottom = hasL ? topNodes + L.nodes.length * STEP - NODE_GAP : 0;
  const topBar = topBottom + 16;
  const topH = hasL ? topBar + BRACKET_TICK + 6 : 0;

  const chain = placeChain(cols, CHAIN_PAD, topH);
  const chainH = chain.bottom + PAD - topH;

  // the right rail below the chain: its bracket over it
  const botY0 = topH + chainH;
  const botBar = botY0 + 12;
  const botNodes = botBar + 44;
  const botH = hasR ? botNodes + R.nodes.length * STEP - NODE_GAP + PAD - botY0 : 0;

  const nodes: PlacedNode[] = [...chain.nodes];
  const heads: PlacedRailHead[] = [];
  const brackets: Bracket[] = [];
  const placed: (PlacedEdge & { mid: Point })[] = [];

  if (hasL) {
    L.nodes.forEach((node, i) =>
      nodes.push({ key: node.key, node, zone: "left", group: "rail:left", x: PAD, y: topNodes + i * STEP, w: NODE_W, h: NODE_H }),
    );
    heads.push({ side: "left", label: L.label, count: L.nodes.length, x: PAD, y: headY, countY: headY + 13 });
    brackets.push({ side: "left", d: `M${bx1} ${topBar + BRACKET_TICK}V${topBar}H${bx2}V${topBar + BRACKET_TICK}` });
    placed.push(
      ...edgesOf(L, data.edges, (i, n) => {
        const start = { x: PAD + NODE_W, y: topNodes + i * STEP + NODE_H / 2 };
        // the top node lands furthest right, so the curves nest and never cross
        const end = { x: hi - ((i + 0.5) / n) * (hi - lo), y: topBar };
        const c1 = { x: start.x + (end.x - start.x) * 0.6, y: start.y };
        const c2 = { x: end.x, y: end.y - (end.y - start.y) * 0.5 };
        return { start, end, d: cubic(start, c1, c2, end), mid: cubicMid(start, c1, c2, end) };
      }),
    );
  }
  if (hasR) {
    R.nodes.forEach((node, i) =>
      nodes.push({ key: node.key, node, zone: "right", group: "rail:right", x: PAD, y: botNodes + i * STEP, w: NODE_W, h: NODE_H }),
    );
    heads.push({ side: "right", label: R.label, count: R.nodes.length, x: PAD, y: botBar + 20, countY: botBar + 33 });
    brackets.push({ side: "right", d: `M${bx1} ${botBar - BRACKET_TICK}V${botBar}H${bx2}V${botBar - BRACKET_TICK}` });
    placed.push(
      ...edgesOf(R, data.edges, (i, n) => {
        const start = { x: PAD + NODE_W, y: botNodes + i * STEP + NODE_H / 2 };
        // the top node lands furthest left, so the curves nest and never cross
        const end = { x: lo + ((i + 0.5) / n) * (hi - lo), y: botBar };
        const c1 = { x: start.x + (end.x - start.x) * 0.6, y: start.y };
        const c2 = { x: end.x, y: end.y + (start.y - end.y) * 0.5 };
        return { start, end, d: cubic(start, c1, c2, end), mid: cubicMid(start, c1, c2, end) };
      }),
    );
  }

  return {
    mode: "stacked",
    width: Math.max(STACK_W, cw),
    height: topH + chainH + botH,
    zones: {
      left: hasL ? { x: 0, y: 0, w: STACK_W, h: topH } : null,
      chain: { x: 0, y: topH, w: cw, h: chainH },
      right: hasR ? { x: 0, y: botY0, w: STACK_W, h: botH } : null,
    },
    columns: chain.columns,
    nodes,
    rails: heads,
    edges: placed.map(strip),
    edgeLabels: [],
    brackets,
    groups: groupsOf(nodes, ["rail:left", ...cols.map((c) => c.key), "rail:right"]),
  };
}

/** The whole layout. The quiet Context and Bench columns are drawn only with `showUntagged`; every other column
 *  (a real link even with nobody in it, an old link, No link) always is. */
export function layoutMap(data: MapShape, opts: LayoutOptions): MapLayout {
  const cols = data.columns.filter((c) => c.kind !== "silent" || opts.showUntagged);
  return opts.mode === "wide" ? wide(cols, data) : stacked(cols, data);
}

// --- keyboard focus ------------------------------------------------------------------------------------------

export type Dir = "up" | "down" | "left" | "right";

/** Where an arrow key moves the focus: up and down walk the names of one group (a column or a rail); left and
 *  right step to the previous or next group, landing on the same row (or the group's last name). It stays put
 *  at an end, and a key it does not know falls back to the first name. */
export function moveFocus(groups: readonly Group[], current: string, dir: Dir): string {
  const gi = groups.findIndex((g) => g.keys.includes(current));
  if (gi < 0) return groups[0]?.keys[0] ?? current;
  const keys = groups[gi].keys;
  const ri = keys.indexOf(current);
  if (dir === "up") return keys[Math.max(0, ri - 1)];
  if (dir === "down") return keys[Math.min(keys.length - 1, ri + 1)];
  const next = groups[dir === "left" ? gi - 1 : gi + 1];
  if (!next) return current;
  return next.keys[Math.min(ri, next.keys.length - 1)];
}
