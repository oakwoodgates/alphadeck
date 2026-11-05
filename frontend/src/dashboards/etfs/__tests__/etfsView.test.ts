import { describe, expect, it } from "vitest";

import {
  DEFAULT_COMPARE_SORT,
  DEFAULT_TREND_SORT,
  flowShare,
  fmtPct,
  fmtPts,
  fmtSignedUsd,
  fmtUsd,
  logShare,
  nextCompareSort,
  sortCompare,
  sortTrends,
} from "../etfsView";
import type { EtfCompareRowOut, EtfReadingOut, EtfTrendRowOut } from "../useEtfs";

const row = (id: string, card: string, over: Partial<EtfCompareRowOut> = {}): EtfCompareRowOut => ({
  thesis_id: `t-${card}`,
  thesis_name: card,
  security_id: id,
  ticker: id.toUpperCase(),
  fund_name: null,
  ret: 1,
  companies_ret: 1,
  diff: 0,
  companies: 3,
  priced: 3,
  unresolved: 0,
  sessions_flat: 0,
  bars: 22,
  bars_needed: 22,
  note: null,
  fund_points: [100, 101],
  company_points: [100, 101],
  ...over,
});

const reading = (value: number | null): EtfReadingOut => ({ value, note: value == null ? "n/a: 1/2 bars" : null });
const trend = (id: string, ret: number | null): EtfTrendRowOut => ({
  security_id: id,
  ticker: id.toUpperCase(),
  name: null,
  cards: [],
  ret_30d: reading(ret),
  vs_50d: reading(null),
  vs_200d: reading(null),
  rvol20: reading(null),
  range_pos: reading(null),
});

describe("number words", () => {
  it("shows a sign as + or − and a missing number as a dash", () => {
    expect(fmtPct(4.2)).toBe("+4.2%");
    expect(fmtPct(-1)).toBe("−1.0%");
    expect(fmtPct(0)).toBe("0.0%");
    expect(fmtPct(null)).toBe("—");
    expect(fmtPts(39.5)).toBe("+39.5 pts");
    expect(fmtPts(-0.04)).toBe("0.0 pts");
    expect(fmtUsd(1_250_000_000)).toBe("$1.3B");
    expect(fmtUsd(340_000)).toBe("$340K");
    expect(fmtSignedUsd(-2_400_000)).toBe("−$2.4M");
    expect(fmtSignedUsd(0)).toBe("$0");
  });
});

describe("panel 1 sorts", () => {
  const rows = [
    row("fnda", "Beta", { diff: 2 }),
    row("fndb", "Alpha", { diff: 9 }),
    row("fndc", "Gamma", { diff: null, ret: null }),
    row("fndd", "Delta", { diff: -3 }),
  ];

  it("defaults to fund minus companies, highest first, a row without it last", () => {
    expect(sortCompare(rows, DEFAULT_COMPARE_SORT).map((r) => r.security_id)).toEqual([
      "fndb",
      "fnda",
      "fndd",
      "fndc",
    ]);
  });

  it("keeps a row without the number last in both directions", () => {
    const asc = sortCompare(rows, { key: "diff", dir: "asc" });
    expect(asc.map((r) => r.security_id)).toEqual(["fndd", "fnda", "fndb", "fndc"]);
  });

  it("sorts by card A to Z, and picking it again reverses; a new number key starts highest first", () => {
    const byName = sortCompare(rows, { key: "name", dir: "asc" });
    expect(byName.map((r) => r.thesis_name)).toEqual(["Alpha", "Beta", "Delta", "Gamma"]);
    expect(nextCompareSort({ key: "name", dir: "asc" }, "name")).toEqual({ key: "name", dir: "desc" });
    expect(nextCompareSort(DEFAULT_COMPARE_SORT, "ret")).toEqual({ key: "ret", dir: "desc" });
    expect(nextCompareSort(DEFAULT_COMPARE_SORT, "name")).toEqual({ key: "name", dir: "asc" });
  });
});

describe("panel 4 sorts", () => {
  const rows = [trend("fndb", 3), trend("fnda", null), trend("fndc", 9)];

  it("defaults to the fund A to Z", () => {
    expect(sortTrends(rows, DEFAULT_TREND_SORT).map((r) => r.security_id)).toEqual(["fnda", "fndb", "fndc"]);
  });

  it("sorts by a column with a fund without the reading last both ways", () => {
    expect(sortTrends(rows, { key: "ret_30d", dir: "desc" }).map((r) => r.security_id)).toEqual([
      "fndc",
      "fndb",
      "fnda",
    ]);
    expect(sortTrends(rows, { key: "ret_30d", dir: "asc" }).map((r) => r.security_id)).toEqual([
      "fndb",
      "fndc",
      "fnda",
    ]);
  });
});

describe("bars", () => {
  it("scales sizes on a log track: the smallest a sliver, the largest the whole track, none for no number", () => {
    const all = [1e6, 1e8, 1e10, null];
    expect(logShare(1e10, all)).toBe(1);
    expect(logShare(1e6, all)).toBeCloseTo(0.06);
    const mid = logShare(1e8, all);
    expect(mid).toBeGreaterThan(0.4);
    expect(mid).toBeLessThan(0.7);
    expect(logShare(null, all)).toBeNull();
    expect(logShare(0, all)).toBeNull();
    expect(logShare(5, [5])).toBe(1);
  });

  it("sizes a flow bar against the largest absolute 1M flow, a true zero drawing nothing", () => {
    const all = [4_000_000, -8_000_000, 0, null];
    expect(flowShare(-8_000_000, all)).toBe(1);
    expect(flowShare(4_000_000, all)).toBe(0.5);
    expect(flowShare(0, all)).toBe(0);
    expect(flowShare(null, all)).toBeNull();
    expect(flowShare(0, [0, 0])).toBe(0);
  });
});
