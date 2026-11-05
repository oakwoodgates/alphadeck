// The Venn geometry of the Overlap Map (docs/dashboards/overlap-map.md), written here so the page adds no
// dependency (like the Market Map's squarify.ts). PURE: set sizes and pairwise shared counts in, circles out.
//
// Circle AREA is proportional to the set's size. Two sets are EXACT: the distance between the centers is solved by
// bisection so the lens (the circles' overlap) has the area of the shared count. Three sets are APPROXIMATE: each
// pair's distance is solved the same way, then the three circles are placed as a triangle (clamped onto a line when
// the three distances cannot close). A subset draws inside its parent; disjoint sets draw apart. When one set is more
// than 50 times another, the small circle keeps a minimum radius (`minRadiusApplied`). The COUNTS in every region are
// never read off the geometry: `vennRegions` counts them from the sets themselves, so they are exact.

export type Circle = { cx: number; cy: number; r: number };

/** One set of the diagram: a stable key, a label and its member ids (each once). */
export type VennSet = { key: string; label: string; members: readonly string[] };

export type VennRegion = {
  /** Bit i set = inside set i. 1..3 for two sets, 1..7 for three. */
  mask: number;
  /** The members in exactly this region (sorted). */
  ids: string[];
  count: number;
  /** "In A and B, not C". */
  words: string;
};

export type VennLayout = {
  circles: Circle[];
  /** The box that holds every circle, with a margin: draw it as the SVG viewBox. */
  width: number;
  height: number;
  /** A small circle was grown to the minimum radius (one set more than 50 times another). */
  minRadiusApplied: boolean;
  /** Where each region's count sits, by mask (the mean of the region's sampled area). */
  anchors: Record<number, { x: number; y: number }>;
  /** Each region's area, in the drawing's own units (sampled): a count only fits inside a region this big. */
  areas: Record<number, number>;
};

/** A region at least this big (drawing units squared) holds its count; a smaller one is listed under the diagram. */
export const MIN_LABEL_AREA = 650;

/** The largest circle's radius, in the drawing's own units. */
export const MAX_RADIUS = 100;
/** One set more than this many times another keeps the smaller circle at a minimum radius. */
export const MIN_RADIUS_RATIO = 50;
const DISJOINT_GAP = 14;
const PAD = 14;

/** The area of the lens where circles of radius r1 and r2 overlap with their centers d apart. */
export function lensArea(r1: number, r2: number, d: number): number {
  if (d >= r1 + r2) return 0;
  const small = Math.min(r1, r2);
  if (d <= Math.abs(r1 - r2)) return Math.PI * small * small;
  const a1 = Math.acos(clamp1((d * d + r1 * r1 - r2 * r2) / (2 * d * r1)));
  const a2 = Math.acos(clamp1((d * d + r2 * r2 - r1 * r1) / (2 * d * r2)));
  const k = Math.sqrt(Math.max(0, (-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2)));
  return r1 * r1 * a1 + r2 * r2 * a2 - 0.5 * k;
}

const clamp1 = (v: number) => Math.max(-1, Math.min(1, v));

/**
 * The center distance at which circles of radius r1 and r2 overlap by `target` area, by bisection (the lens area
 * only shrinks as the circles move apart). A target of 0 or less gives touching circles (r1 + r2); a target the
 * smaller circle cannot exceed gives the subset distance (|r1 - r2|).
 */
export function distanceForOverlap(r1: number, r2: number, target: number): number {
  const small = Math.min(r1, r2);
  if (target <= 0) return r1 + r2;
  if (target >= Math.PI * small * small) return Math.abs(r1 - r2);
  let lo = Math.abs(r1 - r2);
  let hi = r1 + r2;
  for (let i = 0; i < 80; i++) {
    const mid = (lo + hi) / 2;
    if (lensArea(r1, r2, mid) > target) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

/** The size of each set's intersection with each other set: `shared[i][j]` for i < j. */
export type Overlaps = { sizes: number[]; shared: number[][] };

/** Sizes and pairwise intersections of the member lists. */
export function overlapsOf(sets: readonly VennSet[]): Overlaps {
  const members = sets.map((s) => new Set(s.members));
  const sizes = members.map((m) => m.size);
  const shared = members.map((a) =>
    members.map((b) => {
      let n = 0;
      for (const x of a) if (b.has(x)) n++;
      return n;
    }),
  );
  return { sizes, shared };
}

/**
 * The circles for two or three sets: radii from the sizes (area proportional), distances from the pairwise
 * intersections, the minimum radius past 50x, then the anchors of the regions' counts.
 */
export function vennLayout(o: Overlaps): VennLayout {
  const n = o.sizes.length;
  if (n < 2 || n > 3) throw new Error("a Venn diagram draws two or three sets");
  const biggest = Math.max(...o.sizes, 1);
  const unit = (Math.PI * MAX_RADIUS * MAX_RADIUS) / biggest; // area per member
  const rMin = MAX_RADIUS / Math.sqrt(MIN_RADIUS_RATIO);
  let minRadiusApplied = false;
  const radii = o.sizes.map((s) => {
    const r = Math.sqrt((s * unit) / Math.PI);
    if (r < rMin - 1e-9) {
      // an empty set still draws, but only a set that had to be grown by the 50x rule is flagged
      if (s > 0) minRadiusApplied = true;
      return rMin;
    }
    return Math.max(r, rMin);
  });

  const dist = (i: number, j: number): number => {
    const shared = o.shared[i][j];
    const d = distanceForOverlap(radii[i], radii[j], shared * unit);
    return shared <= 0 ? d + DISJOINT_GAP : d;
  };

  const centers: { cx: number; cy: number }[] = [];
  const d01 = dist(0, 1);
  centers.push({ cx: 0, cy: 0 }, { cx: d01, cy: 0 });
  if (n === 3) {
    const d02 = dist(0, 2);
    const d12 = dist(1, 2);
    let x: number;
    let y: number;
    if (d01 < 1e-9) {
      x = d02;
      y = 0;
    } else {
      x = (d01 * d01 + d02 * d02 - d12 * d12) / (2 * d01);
      const y2 = d02 * d02 - x * x;
      if (y2 >= 0) y = Math.sqrt(y2);
      else {
        // the three distances cannot close into a triangle: clamp the third circle onto the line
        y = 0;
        x = Math.max(-d02, Math.min(d02, x));
      }
    }
    centers.push({ cx: x, cy: y });
  }

  const raw = centers.map((c, i) => ({ ...c, r: radii[i] }));
  const left = Math.min(...raw.map((c) => c.cx - c.r));
  const right = Math.max(...raw.map((c) => c.cx + c.r));
  const top = Math.min(...raw.map((c) => c.cy - c.r));
  const bottom = Math.max(...raw.map((c) => c.cy + c.r));
  const circles = raw.map((c) => ({ cx: c.cx - left + PAD, cy: c.cy - top + PAD, r: c.r }));
  const width = right - left + 2 * PAD;
  const height = bottom - top + 2 * PAD;
  const { anchors, areas } = regionAnchors(circles, width, height);
  return { circles, width, height, minRadiusApplied, anchors, areas };
}

/** The mask of the circles a point lies inside (bit i = circle i). */
export function maskAt(circles: readonly Circle[], x: number, y: number): number {
  let m = 0;
  circles.forEach((c, i) => {
    if ((x - c.cx) ** 2 + (y - c.cy) ** 2 <= c.r * c.r) m |= 1 << i;
  });
  return m;
}

/** Each region's anchor: the mean of the sample points that fall in exactly that region (a fixed grid, so it is
 *  deterministic). A region too small to catch a sample falls back to its circles' middle. */
export function regionAnchors(
  circles: readonly Circle[],
  width: number,
  height: number,
): { anchors: Record<number, { x: number; y: number }>; areas: Record<number, number> } {
  const steps = 60;
  const acc = new Map<number, { x: number; y: number; n: number }>();
  for (let i = 0; i < steps; i++) {
    for (let j = 0; j < steps; j++) {
      const x = ((i + 0.5) / steps) * width;
      const y = ((j + 0.5) / steps) * height;
      const m = maskAt(circles, x, y);
      if (m === 0) continue;
      const a = acc.get(m) ?? { x: 0, y: 0, n: 0 };
      a.x += x;
      a.y += y;
      a.n += 1;
      acc.set(m, a);
    }
  }
  const out: Record<number, { x: number; y: number }> = {};
  const areas: Record<number, number> = {};
  for (let m = 1; m < 1 << circles.length; m++) {
    const a = acc.get(m);
    areas[m] = a ? (a.n / (steps * steps)) * width * height : 0;
    if (a && a.n > 0) {
      out[m] = { x: a.x / a.n, y: a.y / a.n };
      continue;
    }
    const inside = circles.filter((_, i) => m & (1 << i));
    out[m] = {
      x: inside.reduce((s, c) => s + c.cx, 0) / inside.length,
      y: inside.reduce((s, c) => s + c.cy, 0) / inside.length,
    };
  }
  return { anchors: out, areas };
}

const joinWords = (xs: readonly string[]): string =>
  xs.length <= 1 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/** "In A and B, not C" for a region of the diagram. */
export function regionWords(mask: number, labels: readonly string[]): string {
  const inside = labels.filter((_, i) => mask & (1 << i));
  const outside = labels.filter((_, i) => !(mask & (1 << i)));
  const head = `In ${joinWords(inside)}`;
  if (outside.length === 0) return head;
  return `${head}, not ${outside.length === 1 ? outside[0] : outside.join(" or ")}`;
}

/** EXACT region contents for two or three sets: every region (3 or 7), each with its members counted from the
 *  sets themselves, never from the geometry. */
export function vennRegions(sets: readonly VennSet[]): VennRegion[] {
  const n = sets.length;
  const member = sets.map((s) => new Set(s.members));
  const all = new Set<string>();
  for (const s of sets) for (const m of s.members) all.add(m);
  const byMask = new Map<number, string[]>();
  for (const id of all) {
    let mask = 0;
    member.forEach((m, i) => {
      if (m.has(id)) mask |= 1 << i;
    });
    byMask.set(mask, [...(byMask.get(mask) ?? []), id]);
  }
  const labels = sets.map((s) => s.label);
  const out: VennRegion[] = [];
  for (let mask = 1; mask < 1 << n; mask++) {
    const ids = (byMask.get(mask) ?? []).sort();
    out.push({ mask, ids, count: ids.length, words: regionWords(mask, labels) });
  }
  return out;
}
