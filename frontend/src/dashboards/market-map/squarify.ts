// A squarified treemap (Bruls, Huizing and van Wijk, "Squarified Treemaps", 2000), written here so the Market Map
// adds no dependency. PURE: weights in, rectangles out. Each item's rectangle has area proportional to its weight
// inside `rect`; items are laid out in rows along the shorter side of the space left, each row grown while it
// keeps the worst aspect ratio from getting worse. Deterministic: ties on weight keep input order.

export type Rect = { x: number; y: number; w: number; h: number };

/** The worst aspect ratio of a row with total area `sum`, largest item `max`, smallest `min`, laid along `side`. */
function worstRatio(sum: number, max: number, min: number, side: number): number {
  const s2 = sum * sum;
  const side2 = side * side;
  return Math.max((side2 * max) / s2, s2 / (side2 * min));
}

/**
 * The rectangles for `weights` inside `rect`, in the SAME order as `weights`. A weight that is not a positive
 * finite number gets an empty rectangle at the origin of `rect` (it takes no room); the rest share the whole area
 * in proportion to their weights, so their areas sum to `rect.w * rect.h` and none overlaps another.
 */
export function squarify(weights: readonly number[], rect: Rect): Rect[] {
  const out: Rect[] = weights.map(() => ({ x: rect.x, y: rect.y, w: 0, h: 0 }));
  const order = weights
    .map((_, i) => i)
    .filter((i) => Number.isFinite(weights[i]) && weights[i] > 0)
    .sort((a, b) => weights[b] - weights[a] || a - b);
  const total = order.reduce((s, i) => s + weights[i], 0);
  if (order.length === 0 || !(rect.w > 0) || !(rect.h > 0) || !(total > 0)) return out;

  const scale = (rect.w * rect.h) / total; // area per unit of weight
  let { x, y, w, h } = rect;
  let i = 0;
  while (i < order.length) {
    const side = Math.min(w, h);
    const largest = weights[order[i]] * scale;
    // grow the row while the worst aspect ratio does not get worse
    let j = i;
    let sum = 0;
    let best = Infinity;
    while (j < order.length) {
      const area = weights[order[j]] * scale;
      const ratio = worstRatio(sum + area, largest, area, side);
      if (j > i && ratio > best) break;
      sum += area;
      best = ratio;
      j++;
    }
    if (w >= h) {
      // a column down the left edge, as wide as its area needs
      const colW = j >= order.length ? w : sum / h;
      let cy = y;
      for (let k = i; k < j; k++) {
        const ch = (weights[order[k]] * scale) / colW;
        out[order[k]] = { x, y: cy, w: colW, h: ch };
        cy += ch;
      }
      x += colW;
      w = Math.max(0, w - colW);
    } else {
      // a strip across the top edge, as tall as its area needs
      const rowH = j >= order.length ? h : sum / w;
      let cx = x;
      for (let k = i; k < j; k++) {
        const cw = (weights[order[k]] * scale) / rowH;
        out[order[k]] = { x: cx, y, w: cw, h: rowH };
        cx += cw;
      }
      y += rowH;
      h = Math.max(0, h - rowH);
    }
    i = j;
  }
  return out;
}
