import { describe, expect, it } from "vitest";

import {
  CLUSTER_SORT,
  MOVER_SORT,
  NEAR_SORT,
  TIMELINE_SORT,
  clusterFirstDir,
  fmtGap,
  fmtPts,
  groupLabel,
  nextSort,
  sessionsAgo,
  sortClusters,
  sortMovers,
  sortNear,
  sortTimeline,
} from "../crossView";
import type {
  CrossClusterOut,
  CrossFirstMoverOut,
  CrossGroupOut,
  CrossNearGroupOut,
  CrossNearNameOut,
  CrossTimelineRowOut,
} from "../useCrossRadar";

// The Cross Radar's view helpers: labels per view, value words, and each section's sort (default order, a
// header flip, ties by label). Every number is the server's.

const group = (link: string, over: Partial<CrossGroupOut> = {}): CrossGroupOut => ({
  key: `link:t:${link}`,
  label: `${link} · Grid`,
  link,
  thesis_id: "t-grid",
  thesis_name: "Grid",
  business_type: null,
  supersector: null,
  names: 4,
  priced: 4,
  not_priced: 0,
  ...over,
});

const cluster = (link: string, count: number, priced: number): CrossClusterOut => ({
  group: group(link),
  direction: "golden",
  count,
  priced,
  share: count / priced,
  within: 3,
  rs_leaders: 0,
  names: [],
});

const leader = (d: string) => ({
  security_id: `s-${d}`,
  ticker: "LEAD",
  cards: [],
  d,
  sessions_ago: 1,
  rs_leading: null,
});

const mover = (link: string, d: string, closing = 0): CrossFirstMoverOut => ({
  group: group(link),
  direction: "golden",
  leader: leader(d),
  rest: 3,
  closing_in: closing,
  already: 0,
  not_yet: 3 - closing,
});

const near = (ticker: string, gap: number, change: number): CrossNearNameOut => ({
  security_id: `s-${ticker}`,
  ticker,
  cards: [],
  toward: gap < 0 ? "golden" : "death",
  gap_pct: gap,
  gap_pct_then: gap - change,
  change,
});

const tl = (link: string, golden: number, death: number): CrossTimelineRowOut => ({
  group: group(link),
  golden,
  death,
  price_up: 0,
  price_down: 0,
  posture: { above_rising: 0, above_falling: 0, below_rising: 0, below_falling: 0, other: 0 },
  members: [],
});

describe("labels and words", () => {
  it("labels a group per view, Unclassified / Unsegmented for the gaps", () => {
    expect(groupLabel(group("Turbines"), "link")).toBe("Turbines · Grid");
    expect(groupLabel(group("", { link: null }), "link")).toBe("Unsegmented · Grid");
    expect(groupLabel(group("x"), "card")).toBe("Grid");
    expect(groupLabel(group("x", { supersector: "energy_utilities" }), "sector")).toBe("Energy & Utilities");
    expect(groupLabel(group("x"), "business_type")).toBe("Unclassified");
  });

  it("words sessions, gaps and points", () => {
    expect(sessionsAgo(0)).toBe("on the as-of session");
    expect(sessionsAgo(1)).toBe("1 session ago");
    expect(sessionsAgo(4)).toBe("4 sessions ago");
    expect(fmtGap(-0.6)).toBe("-0.60%");
    expect(fmtGap(1.25)).toBe("+1.25%");
    expect(fmtGap(null)).toBe("—");
    expect(fmtPts(0.41)).toBe("+0.41 pts");
  });
});

describe("sorts", () => {
  it("clusters: share, then count, then label; a header flips", () => {
    const rows = [cluster("A", 2, 4), cluster("B", 2, 2), cluster("C", 4, 8), cluster("D", 3, 3)];
    expect(sortClusters(rows, CLUSTER_SORT, "link").map((c) => c.group.link)).toEqual(["D", "B", "C", "A"]);
    const byCount = nextSort(CLUSTER_SORT, "count", clusterFirstDir);
    expect(byCount).toEqual({ col: "count", dir: "desc" });
    expect(sortClusters(rows, byCount, "link").map((c) => c.group.link)).toEqual(["C", "D", "B", "A"]);
    expect(nextSort(byCount, "count", clusterFirstDir)).toEqual({ col: "count", dir: "asc" });
    expect(nextSort(CLUSTER_SORT, "label", clusterFirstDir)).toEqual({ col: "label", dir: "asc" });
  });

  it("first movers: newest cross first", () => {
    const rows = [mover("Old", "2026-10-01"), mover("New", "2026-10-27"), mover("Mid", "2026-10-15", 2)];
    expect(sortMovers(rows, MOVER_SORT, "link").map((f) => f.group.link)).toEqual(["New", "Mid", "Old"]);
    expect(sortMovers(rows, { col: "closing_in", dir: "desc" }, "link")[0].group.link).toBe("Mid");
  });

  it("near cross: the smallest gap first, names inside each group by the same column", () => {
    const groups: CrossNearGroupOut[] = [
      { group: group("A"), names: [near("A1", -1.5, 0.2), near("A2", 0.4, -0.9)] },
      { group: group("B"), names: [near("B1", -0.2, 0.1)] },
    ];
    const sorted = sortNear(groups, NEAR_SORT, "link");
    expect(sorted.map((g) => g.group.link)).toEqual(["B", "A"]);
    expect(sorted[1].names.map((n) => n.ticker)).toEqual(["A2", "A1"]);
    const byChange = sortNear(groups, { col: "change", dir: "desc" }, "link");
    expect(byChange.map((g) => g.group.link)).toEqual(["A", "B"]);
  });

  it("timeline: total crosses, then label", () => {
    const rows = [tl("Quiet", 0, 0), tl("b", 1, 1), tl("a", 2, 0), tl("Busy", 3, 2)];
    expect(sortTimeline(rows, TIMELINE_SORT, "link").map((r) => r.group.link)).toEqual(["Busy", "a", "b", "Quiet"]);
    expect(sortTimeline(rows, { col: "death", dir: "desc" }, "link")[0].group.link).toBe("Busy");
    expect(sortTimeline(rows, { col: "label", dir: "asc" }, "link").map((r) => r.group.link)).toEqual([
      "a",
      "b",
      "Busy",
      "Quiet",
    ]);
  });
});
