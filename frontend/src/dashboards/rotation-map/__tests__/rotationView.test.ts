import { describe, expect, it } from "vitest";

import { nextSort, shown, sortGroups, sortNames } from "../rotationView";
import type { RotationGroupOut, RotationNameOut } from "../useRotationMap";

// The pure view helpers: "—" sorts last in both directions on every column, a share over fewer than
// min_priced names counts as "—", the vs-SPY shift, and the header click's next sort.

const g = (key: string, over: Partial<RotationGroupOut> = {}): RotationGroupOut => ({
  key,
  link: key,
  thesis_id: "t",
  thesis_name: "Card",
  business_type: null,
  supersector: null,
  names: 3,
  windows: [],
  above_50d: 0,
  above_50d_n: 0,
  rvol20_median: null,
  rvol20_n: 0,
  golden: 0,
  death: 0,
  rs_leaders: 0,
  rs_n: 0,
  members: [],
  ...over,
});

const keys = (rows: RotationGroupOut[]) => rows.map((r) => r.key);

describe("sortGroups", () => {
  it("puts a thin share (fewer than min_priced names read) with the — rows, last both ways", () => {
    const rows = [
      g("a", { above_50d: 1, above_50d_n: 1 }), // 100% of one name: no group read
      g("b", { above_50d: 3, above_50d_n: 4 }),
      g("c", { above_50d: 1, above_50d_n: 4 }),
      g("d"),
    ];
    expect(keys(sortGroups(rows, { col: "above_50d", dir: "desc" }, "link", 3))).toEqual([
      "b",
      "c",
      "a",
      "d",
    ]);
    expect(keys(sortGroups(rows, { col: "above_50d", dir: "asc" }, "link", 3))).toEqual([
      "c",
      "b",
      "a",
      "d",
    ]);
  });

  it("an RVOL median of null sorts last both ways; ties fall back to the label", () => {
    const rows = [g("z", { rvol20_median: 2 }), g("m"), g("a", { rvol20_median: 2 }), g("b", { rvol20_median: 1 })];
    expect(keys(sortGroups(rows, { col: "rvol20", dir: "desc" }, "link", 3))).toEqual(["a", "z", "b", "m"]);
    expect(keys(sortGroups(rows, { col: "rvol20", dir: "asc" }, "link", 3))).toEqual(["b", "a", "z", "m"]);
  });
});

describe("sortNames", () => {
  const n = (ticker: string, r7: number | null, r1: number | null = null): RotationNameOut => ({
    security_id: ticker,
    ticker,
    cards: [],
    returns: { ret_1d: r1, ret_7d: r7 },
    above_50d: null,
    rvol20: null,
    cross: null,
    cross_date: null,
    rs_leading: null,
  });

  it("follows the active return window, else the 7d; — last", () => {
    const names = [n("A", 1, 5), n("B", null, 9), n("C", 3, null)];
    expect(sortNames(names, { col: "golden", dir: "asc" }).map((x) => x.ticker)).toEqual(["C", "A", "B"]);
    expect(sortNames(names, { col: "ret_1d", dir: "asc" }).map((x) => x.ticker)).toEqual(["A", "B", "C"]);
  });
});

describe("shown / nextSort", () => {
  it("vs SPY subtracts SPY's return for the window; a missing side reads —", () => {
    const spy = { ret_7d: 1.5, ret_1y: null };
    expect(shown(4, "ret_7d", false, spy)).toBe(4);
    expect(shown(4, "ret_7d", true, spy)).toBe(2.5);
    expect(shown(4, "ret_1y", true, spy)).toBeNull();
    expect(shown(null, "ret_7d", true, spy)).toBeNull();
  });

  it("the active column flips; a new one starts highest first, the label A to Z", () => {
    expect(nextSort({ col: "ret_7d", dir: "desc" }, "ret_7d")).toEqual({ col: "ret_7d", dir: "asc" });
    expect(nextSort({ col: "ret_7d", dir: "asc" }, "names")).toEqual({ col: "names", dir: "desc" });
    expect(nextSort({ col: "ret_7d", dir: "desc" }, "label")).toEqual({ col: "label", dir: "asc" });
  });
});
