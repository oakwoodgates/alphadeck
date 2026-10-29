import { describe, expect, it } from "vitest";

import {
  DEFAULT_SORT,
  fmtPos,
  fmtRatio,
  groupLabel,
  nextSort,
  sortGroups,
  sortNames,
  sortValue,
  splitWords,
} from "../landingView";
import type { LandingGroupOut, LandingNameOut, LandingSplitOut } from "../useLanding";

// The Landing's pure view helpers: the split's words, the formats, and the sorts ("—" last in both
// directions, an accumulation count "—" only when no name has a quadrant reading).

const split = (over: Partial<LandingSplitOut> = {}): LandingSplitOut => ({
  accumulation: 0,
  thin_rally: 0,
  distribution: 0,
  rest: 0,
  flat: 0,
  unread: 0,
  ...over,
});

const group = (key: string, pos7: number | null, s: LandingSplitOut): LandingGroupOut => ({
  key,
  link: key,
  thesis_id: "t",
  thesis_name: "Card",
  business_type: null,
  supersector: null,
  names: 3,
  priced: 3,
  windows: [
    { key: "7d", range_median: pos7, range_n: 3, split: s, up_down_median: null, up_down_n: 0 },
  ],
  members: [],
});

const name = (ticker: string, pos7: number | null): LandingNameOut => ({
  security_id: ticker,
  ticker,
  cards: [],
  priced: pos7 != null,
  not_priced: pos7 == null ? "no_tape" : null,
  last_bar: null,
  windows: [{ key: "7d", ret: null, range_pos: pos7, vol_ratio: null, quadrant: null, up_down: null }],
});

describe("landingView", () => {
  it("words the split in the quadrant order, zeros out, — when nothing read", () => {
    expect(splitWords(split({ rest: 1, accumulation: 3, thin_rally: 1 }))).toBe("3 accum · 1 thin · 1 rest");
    expect(splitWords(split({ distribution: 2, flat: 1 }))).toBe("2 dist · 1 flat");
    expect(splitWords(split({ unread: 4 }))).toBe("—");
  });

  it("formats a position and a ratio, — for none", () => {
    expect(fmtPos(62.4)).toBe("62%");
    expect(fmtPos(null)).toBe("—");
    expect(fmtRatio(1.2)).toBe("1.20×");
    expect(fmtRatio(undefined)).toBe("—");
  });

  it("an accumulation count of 0 is a value; a group with no quadrant reading is —", () => {
    expect(sortValue(group("a", 50, split({ rest: 3 })), "7d:accumulation", "link")).toBe(0);
    expect(sortValue(group("a", 50, split({ unread: 3 })), "7d:accumulation", "link")).toBeNull();
    expect(sortValue(group("a", null, split()), "7d:position", "link")).toBeNull();
    expect(groupLabel(group("Turbines", 1, split()), "link")).toBe("Turbines · Card");
  });

  it("sorts groups with — last in both directions, ties by label", () => {
    const gs = [
      group("b", 40, split({ accumulation: 1 })),
      group("none", null, split()),
      group("a", 40, split({ accumulation: 2 })),
      group("c", 90, split({ accumulation: 2 })),
    ];
    expect(sortGroups(gs, DEFAULT_SORT, "link").map((g) => g.key)).toEqual(["c", "a", "b", "none"]);
    expect(sortGroups(gs, { col: "7d:position", dir: "asc" }, "link").map((g) => g.key)).toEqual([
      "a",
      "b",
      "c",
      "none",
    ]);
    expect(sortGroups(gs, { col: "7d:accumulation", dir: "desc" }, "link").map((g) => g.key)).toEqual([
      "a",
      "c",
      "b",
      "none",
    ]);
  });

  it("sorts a group's names by the sorted window's position, — last", () => {
    const ns = [name("MID", 50), name("NONE", null), name("HI", 90), name("LO", 10)];
    expect(sortNames(ns, DEFAULT_SORT).map((n) => n.ticker)).toEqual(["HI", "MID", "LO", "NONE"]);
    expect(sortNames(ns, { col: "7d:position", dir: "asc" }).map((n) => n.ticker)).toEqual([
      "LO",
      "MID",
      "HI",
      "NONE",
    ]);
    // a non-window sort reads the 7D, highest first
    expect(sortNames(ns, { col: "label", dir: "asc" }).map((n) => n.ticker)).toEqual(["HI", "MID", "LO", "NONE"]);
  });

  it("a click flips the active column; a new column starts highest first, the label A→Z", () => {
    expect(nextSort(DEFAULT_SORT, "7d:position")).toEqual({ col: "7d:position", dir: "asc" });
    expect(nextSort(DEFAULT_SORT, "30d:accumulation")).toEqual({ col: "30d:accumulation", dir: "desc" });
    expect(nextSort(DEFAULT_SORT, "label")).toEqual({ col: "label", dir: "asc" });
  });
});
