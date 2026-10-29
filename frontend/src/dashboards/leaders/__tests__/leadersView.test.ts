import { describe, expect, it } from "vitest";

import {
  GROUP_SORT,
  LIST_SORT,
  SEVERAL_SORT,
  fmtContraction,
  fmtPct,
  groupFirstDir,
  groupLabel,
  nameFirstDir,
  nextSort,
  sortGroups,
  sortNames,
} from "../leadersView";
import type { LeadersInsiderGroupOut, LeadersNameOut } from "../useLeaders";

// The Leaders view helpers: each table's sort ("—" last both ways, the several-lists tie by the 30D return,
// buys by buyers), the group labels per view, the formats and the header-click cycle.

const name = (ticker: string, over: Partial<LeadersNameOut> = {}): LeadersNameOut => ({
  security_id: `s-${ticker}`,
  ticker,
  name: null,
  cards: [],
  lists: ["near_high"],
  pct_off_high: -1.0,
  high_52w: null,
  high_date: null,
  range_bars: 260,
  rs_vs: [],
  contraction: null,
  buys_30d: null,
  buyers_30d: null,
  cluster: false,
  rvol20: null,
  ret_7d: null,
  ret_30d: null,
  above_50d: null,
  ...over,
});

const group = (key: string, over: Partial<LeadersInsiderGroupOut> = {}): LeadersInsiderGroupOut => ({
  key,
  label: key,
  link: key,
  thesis_id: "t",
  thesis_name: "Grid",
  business_type: null,
  supersector: null,
  names: 5,
  with_buys: 2,
  no_insider_data: 0,
  share: 0.4,
  buys: 2,
  members: [],
  ...over,
});

const tks = (names: LeadersNameOut[]) => names.map((n) => n.ticker);

describe("sortNames", () => {
  it("orders the several-lists table by the number of lists, then the 30D return, — last", () => {
    const names = [
      name("ONE", { lists: ["near_high", "volume"], ret_30d: 1 }),
      name("TWO", { lists: ["near_high", "volume", "insider"], ret_30d: -5 }),
      name("THREE", { lists: ["near_high", "coiling"], ret_30d: null }),
      name("FOUR", { lists: ["rs_leader", "coiling"], ret_30d: 9 }),
    ];
    expect(tks(sortNames(names, SEVERAL_SORT))).toEqual(["TWO", "FOUR", "ONE", "THREE"]);
  });

  it("puts a name with no value last in both directions", () => {
    const names = [name("A", { ret_7d: 1 }), name("B", { ret_7d: null }), name("C", { ret_7d: 3 })];
    expect(tks(sortNames(names, { col: "ret_7d", dir: "desc" }))).toEqual(["C", "A", "B"]);
    expect(tks(sortNames(names, { col: "ret_7d", dir: "asc" }))).toEqual(["A", "C", "B"]);
  });

  it("each list's default order: near-high closest first, coiling tightest first, insider buys then buyers", () => {
    const near = [name("A", { pct_off_high: -1.9 }), name("B", { pct_off_high: 0 }), name("C", { pct_off_high: -0.4 })];
    expect(tks(sortNames(near, LIST_SORT.near_high))).toEqual(["B", "C", "A"]);
    const coil = [name("A", { contraction: 0.5 }), name("B", { contraction: 0.2 }), name("C")];
    expect(tks(sortNames(coil, LIST_SORT.coiling))).toEqual(["B", "A", "C"]);
    const ins = [
      name("A", { buys_30d: 2, buyers_30d: 1 }),
      name("B", { buys_30d: 2, buyers_30d: 2 }),
      name("C", { buys_30d: 5, buyers_30d: 1 }),
    ];
    expect(tks(sortNames(ins, LIST_SORT.insider))).toEqual(["C", "B", "A"]);
  });

  it("sorts above-50d and the RS benchmarks, ties by ticker", () => {
    const names = [
      name("B", { above_50d: false, rs_vs: ["SPY"] }),
      name("A", { above_50d: true, rs_vs: ["SPY", "IWM"] }),
      name("C", { above_50d: null, rs_vs: ["IWM"] }),
    ];
    expect(tks(sortNames(names, { col: "above_50d", dir: "desc" }))).toEqual(["A", "B", "C"]);
    expect(tks(sortNames(names, { col: "rs_vs", dir: "desc" }))).toEqual(["A", "B", "C"]);
    expect(tks(sortNames(names, { col: "ticker", dir: "asc" }))).toEqual(["A", "B", "C"]);
  });
});

describe("sortGroups", () => {
  const groups = [
    group("Cables", { with_buys: 2, share: 1.0, buys: 3 }),
    group("Turbines", { with_buys: 3, share: 0.3, buys: 9 }),
    group("Fuel", { with_buys: 2, share: 0.4, buys: 2 }),
  ];
  it("defaults to names with buys, then the share", () => {
    expect(sortGroups(groups, GROUP_SORT, "link").map((g) => g.key)).toEqual(["Turbines", "Cables", "Fuel"]);
  });
  it("sorts by the share, the buys and the label", () => {
    expect(sortGroups(groups, { col: "share", dir: "desc" }, "link").map((g) => g.key)).toEqual([
      "Cables",
      "Fuel",
      "Turbines",
    ]);
    expect(sortGroups(groups, { col: "buys", dir: "asc" }, "link").map((g) => g.key)).toEqual([
      "Fuel",
      "Cables",
      "Turbines",
    ]);
    expect(sortGroups(groups, { col: "label", dir: "asc" }, "link").map((g) => g.key)).toEqual([
      "Cables",
      "Fuel",
      "Turbines",
    ]);
  });
});

describe("labels, formats and the header cycle", () => {
  it("words a group per view", () => {
    expect(groupLabel(group("Fuel"), "link")).toBe("Fuel · Grid");
    expect(groupLabel(group("x", { link: null }), "link")).toBe("Unsegmented · Grid");
    expect(groupLabel(group("x"), "card")).toBe("Grid");
    expect(groupLabel(group("x", { business_type: null }), "business_type")).toBe("Unclassified");
  });

  it("formats a percent, the contraction and a gap", () => {
    expect(fmtPct(-1.234)).toBe("-1.2%");
    expect(fmtPct(2)).toBe("+2.0%");
    expect(fmtPct(null)).toBe("—");
    expect(fmtContraction(0.424)).toBe("42%");
    expect(fmtContraction(null)).toBe("—");
  });

  it("a new column starts in its first direction; the active one flips", () => {
    expect(nextSort(SEVERAL_SORT, "ticker", nameFirstDir)).toEqual({ col: "ticker", dir: "asc" });
    expect(nextSort(SEVERAL_SORT, "contraction", nameFirstDir)).toEqual({ col: "contraction", dir: "asc" });
    expect(nextSort(SEVERAL_SORT, "ret_30d", nameFirstDir)).toEqual({ col: "ret_30d", dir: "desc" });
    expect(nextSort(SEVERAL_SORT, "lists", nameFirstDir)).toEqual({ col: "lists", dir: "asc" });
    expect(nextSort(GROUP_SORT, "label", groupFirstDir)).toEqual({ col: "label", dir: "asc" });
  });
});
