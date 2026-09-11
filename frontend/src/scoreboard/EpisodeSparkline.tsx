import type { ScoreboardEpisodeOut } from "../api/hooks";
import { SPARK_H, SPARK_W, sparkGeometry } from "../cockpit/DisplaySignalsSection";
import { pathTitle } from "./rows";

// The Timing view's Path cell — the SHAPE behind Return · Peak · Worst, which state three points on a
// line the ledger never showed. It reuses the Cockpit's `sparkGeometry` wholesale (pure, unit-tested,
// already handles the two-real-values floor and breaks rather than bridges a gap); only the plumbing is
// its own, because the Cockpit's `SparklineCell` is coupled to the DisplaySignal shape.
//
// VARIABLE length, which INVERTS the Cockpit's fixed-slot choice — and does so on purpose. The Cockpit
// pads every name to one shared 90-bar window, so shapes are comparable down the column. Scoreboard
// episodes share no window at all: each covers its own `[arm_date, exit_date]` (median 13 bars, max 42),
// and 75 of 131 securities carry more than one episode, so even two rows on the same name span different
// periods. Padding them to a common width would draw a 13-bar episode as a stub beside a 42-bar one and
// imply a shared time axis that does not exist — two episodes months apart would read as directly
// comparable. Filling the box says "this episode's own path over its own span", which is what Return,
// Peak and Past-peak on the same row already say. The cost is real and goes in the hover: a steeper line
// does NOT mean a faster move.
//
// The floor stays the Cockpit's — two real closes, because `sparkGeometry` enforces it and forking the
// floor between two sparklines in the same application is a worse inconsistency than the 13 two-bar
// episodes that draw a single straight segment. That segment is not dishonest: it is literally the two
// closes that exist.

/** The de-arm's x on the path: the same `i / (n − 1) · w` mapping `sparkGeometry` uses for its slots,
 *  so the mark lands on its bar rather than near it. Null when the de-arm has no place on this path. */
export function dearmX(pathLength: number, dearmIndex: number | null | undefined): number | null {
  if (dearmIndex == null || pathLength < 2) return null;
  if (dearmIndex < 0 || dearmIndex >= pathLength) return null;
  return Math.round(((dearmIndex / (pathLength - 1)) * SPARK_W) * 10) / 10;
}

/** One Timing-row Path cell. A NEUTRAL hairline in the muted text grey — no accent, because it must
 *  never conflate with the return green/red two cells to its right (#7). Below two closes the cell
 *  reads a muted "—" with the why on hover (a point is not a path). Not sortable: a shape, never a
 *  number the sort could read. */
export function EpisodeSparkline({ ep }: { ep: ScoreboardEpisodeOut }) {
  const path = ep.path ?? [];
  const geo = sparkGeometry(path);
  const title = pathTitle(ep);
  if (!geo)
    return (
      <span className="muted" title={title}>
        —
      </span>
    );
  const x = dearmX(path.length, ep.dearm_index);
  return (
    <span className="spark sb-spark" title={title}>
      <svg
        className="spark-svg"
        width={SPARK_W}
        height={SPARK_H}
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        role="img"
        aria-label={`episode price path, ${geo.bars} bars`}
      >
        {/* the de-arm as a full-height tick rather than a dot on the line: it marks a POINT IN TIME,
            which is what a de-arm is, and drawing it at full height needs no second value mapping. */}
        {x != null && <line className="sb-spark-dearm" x1={x} x2={x} y1={0} y2={SPARK_H} />}
        {geo.paths.map((d, i) => (
          <path key={i} d={d} />
        ))}
        {geo.dots.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={1} />
        ))}
      </svg>
    </span>
  );
}
