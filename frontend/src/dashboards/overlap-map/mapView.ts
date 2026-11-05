import { sectorLabel } from "./sectorGrid";
import type { OverlapMapOut, OverlapPairOut } from "./useOverlapMap";

// The Overlap Map's pure view helpers (docs/dashboards/overlap-map.md): the threshold's lines, the hover words, the
// groups line, the shared-company dots, the click panel, keyboard moves between bubbles, the Venn's pickable sets
// and the quiet line. Every count and every position (the cards, the pairs, the groups at each threshold, the
// layout) is computed server-side; this only picks, words and places marks.

/** Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in. */
export const ROSTER_PIT_FROM = "2026-09-15";

export const THRESHOLDS = [1, 2, 3, 4, 5] as const;
export const DEFAULT_THRESHOLD = 3;

const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** "a, b and c". */
export const joinWords = (xs: readonly string[]): string =>
  xs.length <= 1 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

// --- the lines -------------------------------------------------------------------------------------------------

/** The pairs a line is drawn for at `threshold`: sharing at least that many companies. A pair always has both cards
 *  placed (a card that shares anything is placed). The threshold only hides lines: no bubble moves. */
export function visiblePairs(d: OverlapMapOut, threshold: number): OverlapPairOut[] {
  return d.pairs.filter((p) => p.shared >= threshold && d.cards[p.a].x != null && d.cards[p.b].x != null);
}

/** Companies on either card (each once). */
export function unionSize(d: OverlapMapOut, p: OverlapPairOut): number {
  return d.cards[p.a].companies + d.cards[p.b].companies - p.shared;
}

/** The shared names as a whole percent of the two cards' combined names. */
export function sharePct(d: OverlapMapOut, p: OverlapPairOut): number {
  const u = unionSize(d, p);
  return u > 0 ? Math.round((100 * p.shared) / u) : 0;
}

/** The line's stroke width: grows with the shared count, capped. */
export function lineWidth(shared: number): number {
  return Math.min(1 + 0.6 * shared, 7);
}

export function pairWords(d: OverlapMapOut, p: OverlapPairOut): string {
  return `${d.cards[p.a].name} · ${d.cards[p.b].name}: ${plural(p.shared, "shared name", "shared names")} · ${sharePct(d, p)}% of their combined names`;
}

/** The quiet groups line for a threshold: counts only, no group names. */
export function groupsWords(d: OverlapMapOut, threshold: number): string {
  const g = d.groups.find((x) => x.threshold === threshold);
  if (!g) return "";
  const head = `At ${threshold}+ shared names: `;
  const groups =
    g.sizes.length === 0
      ? "no two cards share that many"
      : g.sizes.length === 1
        ? `one group of ${g.sizes[0]} cards`
        : `groups of ${joinWords(g.sizes.map(String))} cards`;
  return `${head}${groups}; ${plural(g.no_line, "card", "cards")} with no line`;
}

// --- a bubble's neighbors and the shared-company dots ------------------------------------------------------------

export type Neighbor = {
  card: number;
  shared: number;
  /** Indexes into the payload's `companies`, by ticker. */
  companies: number[];
};

/** "Shares names with": every other card sharing a company with `card`, most shared first (ties by name). */
export function neighborsOf(d: OverlapMapOut, card: number): Neighbor[] {
  const by = new Map<number, number[]>();
  d.companies.forEach((co, idx) => {
    if (!co.rows.some((r) => r.card === card)) return;
    for (const r of co.rows) {
      if (r.card === card) continue;
      by.set(r.card, [...(by.get(r.card) ?? []), idx]);
    }
  });
  return [...by.entries()]
    .map(([c, companies]) => ({ card: c, shared: companies.length, companies }))
    .sort(
      (a, b) =>
        b.shared - a.shared ||
        d.cards[a.card].name.localeCompare(d.cards[b.card].name, "en", { sensitivity: "base" }) ||
        a.card - b.card,
    );
}

export type Dot = {
  /** Index into the payload's `companies`. */
  company: number;
  x: number;
  y: number;
  /** The cards it sits on (indexes, ascending). */
  cards: number[];
};

const GOLDEN_ANGLE = 2.399963229728653;

/**
 * A dot for each company on 2+ cards, at the center of its cards' bubbles (a company on two cards: the midpoint
 * between them). Dots with the same set of cards spread on a short deterministic spiral around that center, the first
 * one exactly on it, in the companies' order.
 */
export function sharedDots(d: OverlapMapOut): Dot[] {
  const seen = new Map<string, number>();
  const out: Dot[] = [];
  d.companies.forEach((co, idx) => {
    const cards = [...new Set(co.rows.map((r) => r.card))].sort((a, b) => a - b);
    if (cards.length < 2) return;
    const placed = cards.map((c) => d.cards[c]);
    if (placed.some((c) => c.x == null || c.y == null)) return;
    const cx = placed.reduce((s, c) => s + (c.x as number), 0) / placed.length;
    const cy = placed.reduce((s, c) => s + (c.y as number), 0) / placed.length;
    const key = cards.join(",");
    const k = seen.get(key) ?? 0;
    seen.set(key, k + 1);
    const rad = 4 * Math.sqrt(k);
    out.push({ company: idx, x: cx + rad * Math.cos(k * GOLDEN_ANGLE), y: cy + rad * Math.sin(k * GOLDEN_ANGLE), cards });
  });
  return out;
}

/** Does the card's name fit on its bubble (about 5.6 units a character at the map's type size)? Otherwise it
 *  shows on hover or focus. */
export function nameFits(name: string, r: number): boolean {
  return name.length * 5.6 <= 2 * r * 0.88;
}

export const LINE_HEIGHT = 1.15;
const CHAR_W = 0.56; // an average glyph's width, in font sizes
export const LABEL_MAX_FONT = 13;
export const LABEL_MIN_FONT = 9;
export const LABEL_MAX_LINES = 3;
/** A bubble shows its name only where a line holds at least this many characters at the smallest font. */
export const LABEL_MIN_CHARS = 8;

export type LabelFit = { lines: string[]; fontSize: number };

/** Greedy word wrap into at most `maxLines` lines of `cpl` characters; the last line ends in an ellipsis when the
 *  name does not fit (a word longer than a line is cut). Returns whether it all fit. */
function wrap(name: string, cpl: number, maxLines: number): { lines: string[]; full: boolean } {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let cur = "";
  let i = 0;
  for (; i < words.length; i++) {
    const w = words[i];
    const next = cur ? `${cur} ${w}` : w;
    if (next.length <= cpl) {
      cur = next;
      continue;
    }
    if (cur) {
      if (lines.length + 1 >= maxLines) break;
      lines.push(cur);
      cur = "";
      i--; // retry this word on a fresh line
      continue;
    }
    break; // one word longer than a line
  }
  const rest = words.slice(i).join(" ");
  if (i >= words.length) {
    lines.push(cur);
    return { lines, full: true };
  }
  const tail = cur ? `${cur} ${rest}` : rest;
  lines.push(tail.length > cpl ? `${tail.slice(0, Math.max(1, cpl - 1)).trimEnd()}…` : tail);
  return { lines, full: false };
}

/** Characters one line holds for `lines` lines of `font`: the block's corner must stay inside the circle. */
function charsPerLine(r: number, font: number, lines: number): number {
  const half = (lines * font * LINE_HEIGHT) / 2;
  if (half >= r) return 0;
  const width = 2 * Math.sqrt(r * r - half * half) * 0.92;
  return Math.floor(width / (CHAR_W * font));
}

/**
 * How a card's name sits inside its bubble (radius `r`, in the map's units: the box is 1,000 wide): wrapped at word
 * boundaries to up to three lines, the font as large as fits (13 down to a floor of 9), an ellipsis on the last line
 * when the name overflows. `null` when no line holds `LABEL_MIN_CHARS` characters at the floor: that bubble shows its
 * name on hover or focus instead. Pure.
 */
export function labelFit(name: string, r: number): LabelFit | null {
  for (let f = LABEL_MAX_FONT; f >= LABEL_MIN_FONT; f -= 0.5) {
    for (let n = 1; n <= LABEL_MAX_LINES; n++) {
      const cpl = charsPerLine(r, f, n);
      if (cpl < 1) continue;
      const got = wrap(name, cpl, n);
      if (got.full) return { lines: got.lines, fontSize: f };
    }
  }
  for (let n = LABEL_MAX_LINES; n >= 1; n--) {
    const cpl = charsPerLine(r, LABEL_MIN_FONT, n);
    if (cpl >= LABEL_MIN_CHARS) return { lines: wrap(name, cpl, n).lines, fontSize: LABEL_MIN_FONT };
  }
  return null;
}

export type ArrowKey = "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight";

/** The bubble an arrow key moves to from `from`: the nearest placed bubble in that direction (the off-axis
 *  distance counts double); `from` stays when none lies that way. */
export function moveFocus(d: OverlapMapOut, from: number, key: ArrowKey): number {
  const a = d.cards[from];
  if (a.x == null || a.y == null) return from;
  const dir = key === "ArrowRight" ? [1, 0] : key === "ArrowLeft" ? [-1, 0] : key === "ArrowDown" ? [0, 1] : [0, -1];
  let best = from;
  let bestScore = Number.POSITIVE_INFINITY;
  d.cards.forEach((c, i) => {
    if (i === from || c.x == null || c.y == null) return;
    const dx = c.x - a.x!;
    const dy = c.y - a.y!;
    const along = dx * dir[0] + dy * dir[1];
    if (along <= 0) return;
    const across = Math.abs(dx * dir[1] - dy * dir[0]);
    const score = along + 2 * across;
    if (score < bestScore) {
      bestScore = score;
      best = i;
    }
  });
  return best;
}

// --- the Venn's pickable sets ----------------------------------------------------------------------------------

export type SetKind = "card" | "link" | "supersector" | "business_type";

export const SET_KIND_LABEL: Record<SetKind, string> = {
  card: "Cards",
  link: "Links on a card",
  supersector: "Supersectors",
  business_type: "Business types",
};
export const SET_KINDS: readonly SetKind[] = ["card", "link", "supersector", "business_type"];

export type SetDef = {
  key: string;
  kind: SetKind;
  label: string;
  /** Security ids in the set, each once. */
  members: string[];
  /** The card's index for a card or link set. */
  card?: number;
};

export const cardSetKey = (d: OverlapMapOut, card: number) => `card:${d.cards[card].thesis_id}`;

/** Every set the picker offers, grouped by kind (the order of `SET_KINDS`), A to Z inside a kind. */
export function setDefs(d: OverlapMapOut): SetDef[] {
  const out: SetDef[] = [];
  d.cards.forEach((c, i) => {
    out.push({
      key: cardSetKey(d, i),
      kind: "card",
      label: c.name,
      members: d.companies.filter((co) => co.rows.some((r) => r.card === i)).map((co) => co.security_id),
      card: i,
    });
  });
  const links = new Map<string, SetDef>();
  d.companies.forEach((co) => {
    for (const r of co.rows) {
      for (const link of r.links) {
        const key = `link:${d.cards[r.card].thesis_id}:${link ?? ""}`;
        const def = links.get(key) ?? {
          key,
          kind: "link" as const,
          label: `${link ?? "Unsegmented"} · ${d.cards[r.card].name}`,
          members: [],
          card: r.card,
        };
        def.members.push(co.security_id);
        links.set(key, def);
      }
    }
  });
  out.push(...links.values());
  for (const [kind, field] of [
    ["supersector", "supersector"],
    ["business_type", "business_type"],
  ] as const) {
    const by = new Map<string, SetDef>();
    for (const co of d.companies) {
      const v = co[field] ?? "";
      const key = `${kind}:${v}`;
      const def = by.get(key) ?? { key, kind, label: sectorLabel(v, kind), members: [] };
      def.members.push(co.security_id);
      by.set(key, def);
    }
    out.push(...by.values());
  }
  const rank = (k: SetKind) => SET_KINDS.indexOf(k);
  return out.sort(
    (a, b) => rank(a.kind) - rank(b.kind) || a.label.localeCompare(b.label, "en", { sensitivity: "base" }) || a.key.localeCompare(b.key),
  );
}

/** The default Venn: the pair of cards sharing the most names (the lower card indexes on a tie); the first two
 *  cards when no pair shares a name; nothing with fewer than two cards. */
export function defaultVenn(d: OverlapMapOut): string[] {
  if (d.cards.length < 2) return [];
  let best: OverlapPairOut | null = null;
  for (const p of d.pairs) if (!best || p.shared > best.shared) best = p;
  const [a, b] = best ? [best.a, best.b] : [0, 1];
  return [cardSetKey(d, a), cardSetKey(d, b)];
}

/** The pair of a line click or an "Open in Venn". */
export const pairVenn = (d: OverlapMapOut, a: number, b: number): string[] => [cardSetKey(d, a), cardSetKey(d, b)];

/** The picks that still exist in `defs`, in order; the default pair when fewer than two remain. */
export function validPicks(d: OverlapMapOut, defs: readonly SetDef[], picks: readonly string[]): string[] {
  const have = new Set(defs.map((s) => s.key));
  const kept = picks.filter((k) => have.has(k)).slice(0, 3);
  return kept.length >= 2 ? kept : defaultVenn(d);
}

// --- the quiet line ---------------------------------------------------------------------------------------------

function cacheWords(d: OverlapMapOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work. */
export function workLine(d: OverlapMapOut): string {
  return [
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "card", "cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "card", "cards")} counted`,
    plural(d.basket_rows, "basket row", "basket rows"),
    d.include_silent
      ? "Context, ETFs and Bench rows included"
      : `Context, ETFs and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`,
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security not matched`,
    `${plural(d.companies_in_view, "company", "companies")} in view, ${d.companies_shared.toLocaleString("en-US")} on 2+ cards`,
    `${plural(d.pairs.length, "pair", "pairs")} of cards sharing a name`,
    "Sector and business type come from today's master",
    d.roster_fallback_cards > 0
      ? `${plural(d.roster_fallback_cards, "card", "cards")} with no roster snapshot by the as-of: today's roster stands in`
      : null,
    d.asof < ROSTER_PIT_FROM ? `rosters are point-in-time only from ${fmtDay(ROSTER_PIT_FROM)}; before that today's roster stands in` : null,
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The known limits, always on the page. */
export const LIMITS_LINE = [
  "Matching is by security, so two share classes of one company count as two",
  "positions are our own layout: closer means more shared names, not a measured distance",
  "Venn areas are approximate for three sets; the counts are exact",
  "structure only: no prices, no calls",
].join(" · ");
