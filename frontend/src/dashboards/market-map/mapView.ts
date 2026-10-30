import { businessTypeLabel, supersectorLabel } from "../../util/format";
import { squarify, type Rect } from "./squarify";
import type { MapCompany, MapMembership, MarketMapOut } from "./useMarketMap";

// The Market Map's pure view logic (docs/dashboards/market-map.md): the hierarchy for each Group-by with its
// dedup rules, the weights (and the no-cap smallest-weight rule), the group medians with the floor of 3, the
// fixed color scale, the benchmark subtraction, the layout of the nested boxes, the zoom path and the table
// rows. The server ships READINGS (companies, memberships, benchmarks); everything here is arithmetic over them,
// so every control switches without a refetch. Display only: nothing here is a score or a call input.

// --- the controls -------------------------------------------------------------------------------------------

/** The six windows, in the order the controls show them (the Cockpit's five trailing returns, in trading bars,
 *  plus the year to date). The keys are the wire's `returns` keys. */
export const WINDOWS = [
  { key: "ret_1d", label: "1d" },
  { key: "ret_7d", label: "7d" },
  { key: "ret_30d", label: "30d" },
  { key: "ret_90d", label: "90d" },
  { key: "ytd", label: "YTD" },
  { key: "ret_1y", label: "1Y" },
] as const;
export type WindowKey = (typeof WINDOWS)[number]["key"];
export const DEFAULT_WINDOW: WindowKey = "ret_1d";

export const windowLabel = (k: WindowKey): string => WINDOWS.find((w) => w.key === k)?.label ?? k;

export const GROUP_BYS = [
  { value: "card_link", label: "Card › Link" },
  { value: "card", label: "Card" },
  { value: "sector_type", label: "Sector › Business type" },
  { value: "sector", label: "Sector" },
  { value: "type", label: "Business type" },
  { value: "cap", label: "Cap band" },
  { value: "all", label: "All companies" },
] as const;
export type GroupBy = (typeof GROUP_BYS)[number]["value"];

export type Compare = "none" | "spy" | "iwm";
export const COMPARES: readonly { value: Compare; label: string }[] = [
  { value: "none", label: "Nothing" },
  { value: "spy", label: "SPY" },
  { value: "iwm", label: "IWM" },
];

export type SizeBy = "cap" | "dollar_volume" | "equal";
export const SIZE_BYS: readonly { value: SizeBy; label: string }[] = [
  { value: "cap", label: "Market cap" },
  { value: "dollar_volume", label: "Dollar volume 20D" },
  { value: "equal", label: "Equal" },
];

export type TileMode = "companies" | "groups";

export type ViewOpts = {
  groupBy: GroupBy;
  window: WindowKey;
  compare: Compare;
  sizeBy: SizeBy;
  tiles: TileMode;
  includeSilent: boolean;
};

export const DEFAULT_OPTS: ViewOpts = {
  groupBy: "card_link",
  window: DEFAULT_WINDOW,
  compare: "none",
  sizeBy: "cap",
  tiles: "companies",
  includeSilent: false,
};

/** Fewer priced names than this = no group median (the Rotation Map's floor, `MIN_PRICED`). */
export const MIN_PRICED = 3;

/** The fixed cap bands (CLAUDE.md), smallest first, then the companies with no cap. */
export const CAP_BANDS = [
  { key: "nano", label: "Nano · under $50M" },
  { key: "micro", label: "Micro · $50M to $300M" },
  { key: "small", label: "Small · $300M to $2B" },
  { key: "mid", label: "Mid · $2B to $10B" },
  { key: "large", label: "Large · $10B and up" },
  { key: "none", label: "No cap" },
] as const;

export function bandLabel(tier: string | null | undefined): string {
  return CAP_BANDS.find((b) => b.key === (tier ?? "none"))?.label ?? "No cap";
}

// --- the benchmark subtraction ------------------------------------------------------------------------------

/** The benchmark's own return for one window, or null (no benchmark chosen, no tape, or no reading there). */
export function benchReturn(
  data: Pick<MarketMapOut, "spy" | "iwm">,
  compare: Compare,
  window: WindowKey,
): number | null {
  if (compare === "none") return null;
  const b = compare === "spy" ? data.spy : data.iwm;
  return b?.[window] ?? null;
}

/** Can the chosen benchmark be subtracted for this window? A quiet reason when it cannot (the control is
 *  disabled with that reason; an active choice that stops being available falls back to raw). */
export function compareAvailability(
  data: Pick<MarketMapOut, "spy" | "iwm">,
  compare: Compare,
  window: WindowKey,
): { ok: boolean; reason: string | null } {
  if (compare === "none") return { ok: true, reason: null };
  const name = compare === "spy" ? "SPY" : "IWM";
  const b = compare === "spy" ? data.spy : data.iwm;
  if (b == null) return { ok: false, reason: `no ${name} tape at this as-of` };
  if (b[window] == null) return { ok: false, reason: `${name} has no ${windowLabel(window)} reading` };
  return { ok: true, reason: null };
}

/** A return as the page shows it: raw when `bench` is undefined, else minus the benchmark's own return for the
 *  same window (null when either is missing: never a raw figure passed off as a relative one). */
export function shown(raw: number | null | undefined, bench: number | null | undefined): number | null {
  if (raw == null) return null;
  if (bench === undefined) return raw;
  return bench === null ? null : raw - bench;
}

// --- the color scale ----------------------------------------------------------------------------------------

/** The move that is full red / full green for each window: fixed per window, so a color means the same move from
 *  one day to the next (a quiet day is a gray map). */
export const COLOR_END: Record<WindowKey, number> = {
  ret_1d: 3,
  ret_7d: 8,
  ret_30d: 15,
  ret_90d: 25,
  ytd: 40,
  ret_1y: 50,
};

/** The nine-step bucket of a return: -4 (full red) .. 0 (gray) .. +4 (full green), the nearest of the end, 3/4,
 *  1/2 and 1/4 of it on each side and 0; beyond the end stays the end. null = no reading. */
export function colorStep(value: number | null | undefined, window: WindowKey): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  const s = Math.round((value / COLOR_END[window]) * 4);
  return Math.max(-4, Math.min(4, s)) || 0; // `|| 0` folds -0 into 0
}

/** The CSS class of a step (`market-map.css` mixes the app's --pos / --neg toward a neutral gray); `mm-c-none`
 *  is the hatched no-reading tile, never the flat gray of a 0% move. */
export function stepClass(step: number | null): string {
  if (step == null) return "mm-c-none";
  if (step === 0) return "mm-c-0";
  return `mm-c-${step > 0 ? "p" : "n"}${Math.abs(step)}`;
}

/** The legend: the nine steps with the return each stands for in this window. */
export function legendSteps(window: WindowKey): { step: number; value: number; label: string }[] {
  const end = COLOR_END[window];
  return [-4, -3, -2, -1, 0, 1, 2, 3, 4].map((step) => {
    const value = (step / 4) * end;
    return { step, value, label: fmtStep(value) };
  });
}

function fmtStep(v: number): string {
  const body = Number(Math.abs(v).toFixed(2)).toString();
  return v === 0 ? "0%" : `${v > 0 ? "+" : "-"}${body}%`;
}

// --- formats ------------------------------------------------------------------------------------------------

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** USD, compact: $1.2B, $340M, $12K. */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(a >= 1e10 ? 1 : 2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export function fmtDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export const capSourceWords = (c: Pick<MapCompany, "cap_source">): string | null =>
  c.cap_source === "workbench"
    ? "share count ratified in the Workbench"
    : c.cap_source === "cover"
      ? "share count from the filing cover (not ratified)"
      : null;

// --- the median ----------------------------------------------------------------------------------------------

/** The median of the priced values, or null when fewer than `MIN_PRICED` are priced. */
export function groupMedian(values: readonly (number | null | undefined)[]): number | null {
  const v = values.filter((x): x is number => x != null && Number.isFinite(x)).sort((a, b) => a - b);
  if (v.length < MIN_PRICED) return null;
  const mid = v.length >> 1;
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

// --- the tree -----------------------------------------------------------------------------------------------

export type TreeNode = {
  /** Unique over the whole tree (parent key + own segment), stable across control changes that keep the path. */
  key: string;
  /** group = a box with a header strip holding its children; tile = one company; groupTile = a whole innermost
   *  group drawn as ONE tile (Tiles: Groups). */
  kind: "group" | "tile" | "groupTile";
  label: string;
  weight: number;
  children: TreeNode[];
  /** Distinct companies in the node, and how many have a return for the window. */
  names: number;
  priced: number;
  /** tile: the company's (relative) return; group / groupTile: the median over its priced companies (>= 3). */
  value: number | null;
  /** tile: drawn at the smallest weight in view because it has none (no cap / no dollar volume). */
  dashed: boolean;
  company?: MapCompany;
  /** The card the node sits under (the card views), for the click. */
  cardId?: string;
  /** groupTile: its companies, for the table's per-window medians. */
  members?: MapCompany[];
};

export type Tree = {
  roots: TreeNode[];
  /** Tiles drawn (company tiles, or group tiles in Tiles: Groups). */
  tiles: number;
  /** Distinct companies in view, and how many have a return for the window. */
  companies: number;
  priced: number;
  caps: { workbench: number; cover: number; none: number };
  /** Companies drawn at the smallest weight for want of one. */
  noWeight: number;
  /** The weight the no-weight companies are drawn at (the smallest in view). */
  minWeight: number;
};

type Path = { key: string; label: string; cardId?: string }[];
type Entry = { path: Path; company: MapCompany; cardId: string | null };

type Acc = { key: string; label: string; cardId?: string; kids: Map<string, Acc>; leaves: Entry[] };

const newAcc = (key: string, label: string, cardId?: string): Acc => ({
  key,
  label,
  cardId,
  kids: new Map(),
  leaves: [],
});

const compareLabel = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** The weight a company would have, or null when it has none (no cap, no dollar volume, a non-positive figure). */
export function rawWeight(c: MapCompany, sizeBy: SizeBy): number | null {
  if (sizeBy === "equal") return 1;
  const v = sizeBy === "cap" ? c.cap : c.dollar_volume;
  return v != null && Number.isFinite(v) && v > 0 ? v : null;
}

/** The companies and rows the options put in view (Context and Bench only when asked). */
export function inView(data: MarketMapOut, includeSilent: boolean) {
  const companies = data.companies.filter((c) => includeSilent || !c.silent_only);
  const ids = new Set(companies.map((c) => c.security_id));
  const memberships = data.memberships.filter(
    (m) => ids.has(m.security_id) && (includeSilent || !m.silent),
  );
  return { companies, memberships };
}

/** The companies left off the map for a flagged price history that are in view under the Context/Bench toggle: a
 *  flagged company with only Context or Bench rows is in view only when the toggle is on. The page's note lists
 *  these and the work line counts them, so the two always agree. */
export function flaggedInView(data: Pick<MarketMapOut, "flagged">, includeSilent: boolean) {
  return data.flagged.filter((f) => includeSilent || !f.silent_only);
}

function entriesFor(
  data: MarketMapOut,
  opts: ViewOpts,
  companies: MapCompany[],
  memberships: MapMembership[],
): Entry[] {
  const byId = new Map(companies.map((c) => [c.security_id, c]));
  const cardName = new Map(data.cards.map((c) => [c.thesis_id, c.name]));
  const out: Entry[] = [];
  const seen = new Set<string>();
  const cardSeg = (m: MapMembership) => ({
    key: `card:${m.thesis_id}`,
    label: cardName.get(m.thesis_id) ?? "Unnamed card",
    cardId: m.thesis_id,
  });
  switch (opts.groupBy) {
    case "card_link":
      // once per card-and-link membership
      for (const m of memberships) {
        const id = `${m.thesis_id}|${m.link ?? ""}|${m.security_id}`;
        const c = byId.get(m.security_id);
        if (!c || seen.has(id)) continue;
        seen.add(id);
        out.push({
          path: [cardSeg(m), { key: `link:${m.link ?? ""}`, label: m.link ?? "Unsegmented" }],
          company: c,
          cardId: m.thesis_id,
        });
      }
      return out;
    case "card":
      // once per card
      for (const m of memberships) {
        const id = `${m.thesis_id}|${m.security_id}`;
        const c = byId.get(m.security_id);
        if (!c || seen.has(id)) continue;
        seen.add(id);
        out.push({ path: [cardSeg(m)], company: c, cardId: m.thesis_id });
      }
      return out;
    default:
      // once in total
      for (const c of companies) {
        const ss = { key: `sector:${c.supersector ?? ""}`, label: supersectorLabel(c.supersector) };
        const ty = {
          key: `type:${c.business_type ?? ""}`,
          label: c.business_type ? businessTypeLabel(c.business_type) : "Unclassified",
        };
        const band = {
          key: `cap:${c.cap_tier ?? "none"}`,
          label: bandLabel(c.cap_tier),
        };
        const path: Path =
          opts.groupBy === "sector_type"
            ? [ss, ty]
            : opts.groupBy === "sector"
              ? [ss]
              : opts.groupBy === "type"
                ? [ty]
                : opts.groupBy === "cap"
                  ? [band]
                  : [];
        out.push({ path, company: c, cardId: null });
      }
      return out;
  }
}

/** The hierarchy for a Group-by, with every weight, median and count. A company appears once per group it
 *  belongs to: once per card-and-link in `card_link`, once per card in `card`, once in total otherwise. */
export function buildTree(data: MarketMapOut, opts: ViewOpts): Tree {
  const { companies, memberships } = inView(data, opts.includeSilent);
  const bench = opts.compare === "none" ? undefined : benchReturn(data, opts.compare, opts.window);
  const valueOf = (c: MapCompany): number | null => shown(c.returns[opts.window], bench);

  const weights = companies.map((c) => rawWeight(c, opts.sizeBy)).filter((w): w is number => w != null);
  const minWeight = weights.length ? Math.min(...weights) : 1;
  const weightOf = (c: MapCompany) => rawWeight(c, opts.sizeBy) ?? minWeight;

  // the nested accumulation
  const root = newAcc("", "");
  for (const e of entriesFor(data, opts, companies, memberships)) {
    let at = root;
    for (const seg of e.path) {
      let next = at.kids.get(seg.key);
      if (!next) {
        next = newAcc(`${at.key}/${seg.key}`, seg.label, seg.cardId ?? at.cardId);
        at.kids.set(seg.key, next);
      }
      at = next;
    }
    at.leaves.push(e);
  }

  const tileOf = (e: Entry, parent: Acc): TreeNode => ({
    key: `${parent.key}/co:${e.company.security_id}`,
    kind: "tile",
    label: e.company.ticker,
    weight: weightOf(e.company),
    children: [],
    names: 1,
    priced: valueOf(e.company) != null ? 1 : 0,
    value: valueOf(e.company),
    dashed: rawWeight(e.company, opts.sizeBy) == null,
    company: e.company,
    cardId: e.cardId ?? undefined,
  });

  /** A node and the (company id -> return) of its distinct companies, so a parent counts each once. */
  const convert = (acc: Acc, innermostIsTile: boolean): { node: TreeNode; ids: Map<string, number | null> } => {
    const ids = new Map<string, number | null>();
    const children: TreeNode[] = [];
    const isInnermost = acc.kids.size === 0;
    if (isInnermost && innermostIsTile && acc.key !== "") {
      const members = new Map(acc.leaves.map((e) => [e.company.security_id, e.company]));
      const values = [...members.values()].map(valueOf);
      for (const c of members.values()) ids.set(c.security_id, valueOf(c));
      const weight = acc.leaves.reduce((s, e) => s + weightOf(e.company), 0);
      const node: TreeNode = {
        key: acc.key,
        kind: "groupTile",
        label: acc.label,
        weight,
        children: [],
        names: members.size,
        priced: values.filter((v) => v != null).length,
        value: groupMedian(values),
        dashed: false,
        cardId: acc.cardId,
        members: [...members.values()],
      };
      return { node, ids };
    }
    for (const kid of acc.kids.values()) {
      const got = convert(kid, innermostIsTile);
      children.push(got.node);
      for (const [id, v] of got.ids) ids.set(id, v);
    }
    for (const e of acc.leaves) {
      const t = tileOf(e, acc);
      children.push(t);
      ids.set(e.company.security_id, t.value);
    }
    children.sort((a, b) => b.weight - a.weight || compareLabel(a.label, b.label) || compareLabel(a.key, b.key));
    const values = [...ids.values()];
    return {
      node: {
        key: acc.key,
        kind: "group",
        label: acc.label,
        weight: children.reduce((s, c) => s + c.weight, 0),
        children,
        names: ids.size,
        priced: values.filter((v) => v != null).length,
        value: groupMedian(values),
        dashed: false,
        cardId: acc.cardId,
      },
      ids,
    };
  };

  const groupTiles = opts.tiles === "groups";
  let roots: TreeNode[];
  if (opts.groupBy === "all") {
    if (groupTiles) {
      // one flat map: the whole set is the one innermost group
      const members = [...new Map(root.leaves.map((e) => [e.company.security_id, e.company])).values()];
      const values = members.map(valueOf);
      roots = members.length
        ? [
            {
              key: "all",
              kind: "groupTile",
              label: "All companies",
              weight: members.reduce((s, c) => s + weightOf(c), 0),
              children: [],
              names: members.length,
              priced: values.filter((v) => v != null).length,
              value: groupMedian(values),
              dashed: false,
              members,
            },
          ]
        : [];
    } else {
      roots = root.leaves.map((e) => tileOf(e, root));
    }
  } else {
    roots = [...root.kids.values()].map((k) => convert(k, groupTiles).node);
  }
  roots.sort((a, b) => b.weight - a.weight || compareLabel(a.label, b.label) || compareLabel(a.key, b.key));

  // the whole-view counts for the work line
  const distinct = new Map(companies.map((c) => [c.security_id, c]));
  const inTree = new Set<string>();
  const collect = (n: TreeNode) => {
    if (n.company) inTree.add(n.company.security_id);
    n.members?.forEach((m) => inTree.add(m.security_id));
    n.children.forEach(collect);
  };
  roots.forEach(collect);
  const drawn = [...inTree].map((id) => distinct.get(id)!).filter(Boolean);
  const caps = { workbench: 0, cover: 0, none: 0 };
  for (const c of drawn) {
    if (c.cap_source === "workbench") caps.workbench++;
    else if (c.cap_source === "cover") caps.cover++;
    else caps.none++;
  }
  let tiles = 0;
  const count = (n: TreeNode) => {
    if (n.kind !== "group") tiles++;
    n.children.forEach(count);
  };
  roots.forEach(count);
  return {
    roots,
    tiles,
    companies: drawn.length,
    priced: drawn.filter((c) => valueOf(c) != null).length,
    caps,
    noWeight: opts.sizeBy === "equal" ? 0 : drawn.filter((c) => rawWeight(c, opts.sizeBy) == null).length,
    minWeight,
  };
}

// --- the layout ---------------------------------------------------------------------------------------------

/** The header strip of a group box (px), the padding inside a box, and the smallest box that still shows one. */
export const HEADER_H = 18;
export const BOX_PAD = 2;
const MIN_HEADER_W = 36;

export type Placed = {
  node: TreeNode;
  rect: Rect;
  depth: number;
  /** group boxes only: the box is big enough to show its header strip. */
  header: boolean;
};

/** Lay the nodes out inside `rect`: siblings squarified by weight, a group box giving its children what is
 *  left under its header strip and inside its padding. Returns every group box and every tile, parents first. */
export function layoutTree(roots: readonly TreeNode[], rect: Rect): Placed[] {
  const out: Placed[] = [];
  const place = (nodes: readonly TreeNode[], r: Rect, depth: number) => {
    const rects = squarify(
      nodes.map((n) => n.weight),
      r,
    );
    nodes.forEach((n, i) => {
      const rc = rects[i];
      if (!(rc.w > 0) || !(rc.h > 0)) return;
      if (n.kind !== "group") {
        out.push({ node: n, rect: rc, depth, header: false });
        return;
      }
      const header = rc.h >= HEADER_H * 2 && rc.w >= MIN_HEADER_W;
      out.push({ node: n, rect: rc, depth, header });
      const top = header ? HEADER_H : BOX_PAD;
      place(n.children, {
        x: rc.x + BOX_PAD,
        y: rc.y + top,
        w: rc.w - 2 * BOX_PAD,
        h: rc.h - top - BOX_PAD,
      }, depth + 1);
    });
  };
  place(roots, rect, 0);
  return out;
}

// --- tile text ----------------------------------------------------------------------------------------------

export type TileText = { ticker: boolean; value: boolean; fontPx: number };

const MAX_FONT = 26;
const GLYPH = 0.62; // a mono glyph's width in em

/** What a tile of w x h px can say: ticker and return when both fit (the font scaling with the tile), the ticker
 *  alone on a smaller one, nothing on a tiny one (the hover still works). */
export function tileText(w: number, h: number, tickerLen: number, valueLen: number): TileText {
  const tk = Math.max(1, Math.min(tickerLen, 18));
  const two = Math.min(MAX_FONT, (h - 4) / 2.5, (w - 4) / (GLYPH * Math.max(tk, valueLen * 0.8)));
  if (two >= 8) return { ticker: true, value: true, fontPx: Math.floor(two * 10) / 10 };
  const one = Math.min(MAX_FONT, (h - 2) / 1.4, (w - 4) / (GLYPH * tk));
  if (one >= 7) return { ticker: true, value: false, fontPx: Math.floor(one * 10) / 10 };
  return { ticker: false, value: false, fontPx: 0 };
}

// --- the zoom ------------------------------------------------------------------------------------------------

export type Zoom = { chain: TreeNode[]; roots: TreeNode[] };

/** Follow `path` (node keys, outermost first) down the tree; the longest prefix that still resolves to GROUP
 *  nodes is the zoom, and the zoomed group is the only box on the map. A path the data no longer holds (a
 *  control changed the tree) simply resolves shorter. */
export function resolveZoom(roots: readonly TreeNode[], path: readonly string[]): Zoom {
  const chain: TreeNode[] = [];
  let level: readonly TreeNode[] = roots;
  for (const key of path) {
    const hit = level.find((n) => n.key === key && n.kind === "group");
    if (!hit) break;
    chain.push(hit);
    level = hit.children;
  }
  return { chain, roots: chain.length ? [chain[chain.length - 1]] : [...roots] };
}

/** A group header's hover words: what the median rests on. */
export function headerTitle(n: Pick<TreeNode, "names" | "priced" | "value">): string {
  const names = `${n.names.toLocaleString("en-US")} ${n.names === 1 ? "name" : "names"}`;
  return n.value != null
    ? `median of ${n.priced.toLocaleString("en-US")} priced ${n.priced === 1 ? "name" : "names"} · ${names}`
    : `no median: ${n.priced.toLocaleString("en-US")} priced ${n.priced === 1 ? "name" : "names"}, fewer than ${MIN_PRICED} · ${names}`;
}

// --- the table ----------------------------------------------------------------------------------------------

export type TableRow = {
  key: string;
  kind: "company" | "group";
  label: string;
  sub: string;
  group: string;
  values: Record<WindowKey, number | null>;
  cap: number | null;
  capTier: string | null;
  capSource: "workbench" | "cover" | null;
  dollarVolume: number | null;
  sector: string;
  businessType: string;
  cards: string;
  names: number;
  priced: number;
};

export type SortCol =
  | "label"
  | "group"
  | WindowKey
  | "cap"
  | "dollarVolume"
  | "sector"
  | "businessType"
  | "cards";
export type SortDir = "asc" | "desc";
export type SortState = { col: SortCol; dir: SortDir };

/** The cards and links each company sits on among the rows in view, for every company at once (one pass over the
 *  memberships; the table reads it per row). */
export function cardLinksBySecurity(data: MarketMapOut, includeSilent: boolean): Map<string, CardLink[]> {
  const names = new Map(data.cards.map((c) => [c.thesis_id, c.name]));
  const out = new Map<string, Map<string, CardLink>>();
  for (const m of data.memberships) {
    if (!includeSilent && m.silent) continue;
    const link = m.link ?? "Unsegmented";
    const cards = out.get(m.security_id) ?? new Map<string, CardLink>();
    const got = cards.get(m.thesis_id);
    if (got) {
      if (!got.links.includes(link)) got.links.push(link);
    } else {
      cards.set(m.thesis_id, {
        thesisId: m.thesis_id,
        card: names.get(m.thesis_id) ?? "Unnamed card",
        ticker: m.ticker,
        links: [link],
      });
    }
    out.set(m.security_id, cards);
  }
  const sorted = new Map<string, CardLink[]>();
  for (const [sid, cards] of out) {
    sorted.set(
      sid,
      [...cards.values()].sort((a, b) => compareLabel(a.card.toLowerCase(), b.card.toLowerCase())),
    );
  }
  return sorted;
}

/** The cards and links each company sits on, as words ("Grid (Turbines, Cables) · Fuel (Core)"). */
export function cardWords(data: MarketMapOut, includeSilent: boolean): Map<string, string> {
  const out = new Map<string, string>();
  for (const [sid, cards] of cardLinksBySecurity(data, includeSilent)) {
    out.set(sid, cards.map((c) => `${c.card} (${c.links.join(", ")})`).join(" · "));
  }
  return out;
}

/** Every tile under `nodes`, with the labels of the boxes it sits in. */
export function flattenTiles(
  nodes: readonly TreeNode[],
  trail: readonly string[] = [],
): { node: TreeNode; trail: string[] }[] {
  const out: { node: TreeNode; trail: string[] }[] = [];
  for (const n of nodes) {
    if (n.kind === "group") out.push(...flattenTiles(n.children, [...trail, n.label]));
    else out.push({ node: n, trail: [...trail] });
  }
  return out;
}

/** One row per tile in view: a company tile is the company (every window, the benchmark subtracted when one is
 *  set), a group tile is the group (its median per window over its priced names). */
export function tableRows(
  data: MarketMapOut,
  roots: readonly TreeNode[],
  opts: Pick<ViewOpts, "compare" | "includeSilent">,
): TableRow[] {
  const words = cardWords(data, opts.includeSilent);
  const benchFor = (w: WindowKey) => (opts.compare === "none" ? undefined : benchReturn(data, opts.compare, w));
  return flattenTiles(roots).map(({ node, trail }) => {
    const group = trail.join(" › ");
    if (node.kind === "groupTile") {
      const members = node.members ?? [];
      const values = Object.fromEntries(
        WINDOWS.map((w) => [w.key, groupMedian(members.map((c) => shown(c.returns[w.key], benchFor(w.key))))]),
      ) as Record<WindowKey, number | null>;
      return {
        key: node.key,
        kind: "group" as const,
        label: node.label,
        sub: `${node.names.toLocaleString("en-US")} ${node.names === 1 ? "company" : "companies"}`,
        group,
        values,
        cap: null,
        capTier: null,
        capSource: null,
        dollarVolume: null,
        sector: "",
        businessType: "",
        cards: "",
        names: node.names,
        priced: node.priced,
      };
    }
    const c = node.company!;
    const values = Object.fromEntries(
      WINDOWS.map((w) => [w.key, shown(c.returns[w.key], benchFor(w.key))]),
    ) as Record<WindowKey, number | null>;
    return {
      key: node.key,
      kind: "company" as const,
      label: c.ticker,
      sub: c.name ?? "",
      group,
      values,
      cap: c.cap,
      capTier: c.cap_tier,
      capSource: c.cap_source,
      dollarVolume: c.dollar_volume,
      sector: c.supersector ? supersectorLabel(c.supersector) : "Unclassified",
      businessType: c.business_type ? businessTypeLabel(c.business_type) : "Unclassified",
      cards: words.get(c.security_id) ?? "",
      names: 1,
      priced: node.priced,
    };
  });
}

export const DEFAULT_SORT = (window: WindowKey): SortState => ({ col: window, dir: "desc" });

/** A header click: the same column flips direction; a new column starts descending for the figures, ascending
 *  for the words. */
export function nextSort(prev: SortState, col: SortCol): SortState {
  if (prev.col === col) return { col, dir: prev.dir === "desc" ? "asc" : "desc" };
  const words = col === "label" || col === "group" || col === "sector" || col === "businessType" || col === "cards";
  return { col, dir: words ? "asc" : "desc" };
}

function cell(r: TableRow, col: SortCol): number | string | null {
  switch (col) {
    case "label":
      return r.label.toLowerCase();
    case "group":
      return r.group.toLowerCase();
    case "cap":
      return r.cap;
    case "dollarVolume":
      return r.dollarVolume;
    case "sector":
      return r.sector.toLowerCase() || null;
    case "businessType":
      return r.businessType.toLowerCase() || null;
    case "cards":
      return r.cards.toLowerCase() || null;
    default:
      return r.values[col];
  }
}

/** Sort the rows by a column; a row with no value sorts last both ways; ties by label then key. */
export function sortRows(rows: readonly TableRow[], sort: SortState): TableRow[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = cell(a, sort.col);
    const y = cell(b, sort.col);
    if (x == null && y == null) return compareLabel(a.label, b.label) || compareLabel(a.key, b.key);
    if (x == null) return 1;
    if (y == null) return -1;
    const c = typeof x === "number" && typeof y === "number" ? x - y : compareLabel(String(x), String(y));
    return c !== 0 ? sign * c : compareLabel(a.label, b.label) || compareLabel(a.key, b.key);
  });
}

// --- the click ----------------------------------------------------------------------------------------------

export type CardLink = { thesisId: string; card: string; ticker: string; links: string[] };

/** The cards a company sits on among the rows in view, each with the ticker it carries there (the Cockpit's
 *  `?name=` key) and its links, A to Z by card. */
export function companyCards(
  data: MarketMapOut,
  securityId: string,
  includeSilent: boolean,
): CardLink[] {
  const mine = { ...data, memberships: data.memberships.filter((m) => m.security_id === securityId) };
  return cardLinksBySecurity(mine, includeSilent).get(securityId) ?? [];
}

export type ClickAction =
  | { kind: "open"; thesisId: string; ticker: string | null }
  | { kind: "pin" };

/** What a click on a tile does. A company tile on ONE card opens the Cockpit on that name (always so in the
 *  card views, where the tile belongs to one card); a company on several cards in view pins its info box, whose
 *  card list is the set of Cockpit links. A group tile under a card opens that card; any other pins. */
export function clickAction(
  node: Pick<TreeNode, "kind" | "company" | "cardId">,
  data: MarketMapOut,
  includeSilent: boolean,
): ClickAction {
  if (node.kind === "groupTile") {
    return node.cardId ? { kind: "open", thesisId: node.cardId, ticker: null } : { kind: "pin" };
  }
  const company = node.company;
  if (!company) return { kind: "pin" };
  if (node.cardId) {
    const mine = companyCards(data, company.security_id, includeSilent).find((c) => c.thesisId === node.cardId);
    return { kind: "open", thesisId: node.cardId, ticker: mine?.ticker ?? company.ticker };
  }
  const cards = companyCards(data, company.security_id, includeSilent);
  if (cards.length === 1) return { kind: "open", thesisId: cards[0].thesisId, ticker: cards[0].ticker };
  return { kind: "pin" };
}
