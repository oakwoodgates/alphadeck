import { describe, expect, it } from "vitest";

import type { DisplaySignal } from "../../api/hooks";
import { bucketDef, type BucketRow } from "../buckets";
import {
  countFilterCandidates,
  DEFAULT_BASKET_FILTER,
  hasInsiderBuys,
  isActionableBucket,
  isFilterActive,
  isHotRvol,
  rowMatchesFilter,
} from "../basketFilter";
import type { RowSignals } from "../sortBasket";

const makeRow = (ticker: string, bucket: BucketRow["bucket"], over: Partial<BucketRow> = {}): BucketRow => ({
  ordinal: 0,
  bucket,
  call: null,
  scored: null,
  member: {
    ticker,
    role: "core",
    security_id: `s-${ticker.toLowerCase()}`,
    segment: null,
    detail: null,
    authored_by: "operator_set",
  } as BucketRow["member"],
  ...over,
});

const emptySigs: RowSignals = { sma: null, trail: null, rvol: null, insider: null };

describe("basketFilter — pure view filter helpers", () => {
  it("treats the default filter as inactive and flags any set field as active", () => {
    expect(isFilterActive(DEFAULT_BASKET_FILTER)).toBe(false);
    expect(isFilterActive({ ...DEFAULT_BASKET_FILTER, query: "  " })).toBe(false);
    expect(isFilterActive({ ...DEFAULT_BASKET_FILTER, query: "uu" })).toBe(true);
    expect(isFilterActive({ ...DEFAULT_BASKET_FILTER, actionableOnly: true })).toBe(true);
    expect(isFilterActive({ ...DEFAULT_BASKET_FILTER, insiderBuysOnly: true })).toBe(true);
    expect(isFilterActive({ ...DEFAULT_BASKET_FILTER, hotRvolOnly: true })).toBe(true);
    expect(isFilterActive({ ...DEFAULT_BASKET_FILTER, hideSilent: true })).toBe(true);
  });

  it("classifies actionable vs quiet/silent buckets", () => {
    for (const b of ["managing", "armed", "lapsing", "theme_armed", "warming", "watch"] as const) {
      expect(isActionableBucket(b)).toBe(true);
    }
    for (const b of ["quiet", "context", "etfs", "bench"] as const) {
      expect(isActionableBucket(b)).toBe(false);
    }
  });

  it("reads wire thresholds for RVOL without hardcoding", () => {
    const hot20 = {
      kind: "rvol",
      label: "RVOL",
      metrics: [
        { key: "rvol", label: "8d", value: 1.2, unit: "ratio", note: null },
        { key: "rvol20", label: "20d", value: 2.5, unit: "ratio", note: null },
      ],
      basis: { source: "bars", params: { loud_mult: 2.0, loud_mult_20: 2.2 } },
    } as unknown as DisplaySignal;
    const cold = {
      ...hot20,
      metrics: [
        { key: "rvol", label: "8d", value: 1.1, unit: "ratio", note: null },
        { key: "rvol20", label: "20d", value: 1.4, unit: "ratio", note: null },
      ],
    } as unknown as DisplaySignal;
    expect(isHotRvol(hot20)).toBe(true);
    expect(isHotRvol(cold)).toBe(false);
    expect(isHotRvol(null)).toBe(false);
  });

  it("detects 90-day insider buys and counts candidates cleanly", () => {
    const ins = {
      kind: "insider_flow_90d",
      label: "Insider",
      metrics: [{ key: "buy_count", label: "buys", value: 2, unit: "count", note: null }],
      basis: { source: "form4", params: {} },
    } as unknown as DisplaySignal;
    expect(hasInsiderBuys(ins)).toBe(true);
    expect(hasInsiderBuys(null)).toBe(false);

    const r1 = makeRow("J", "armed");
    const r2 = makeRow("URA", "etfs", { member: { ...makeRow("URA", "etfs").member, segment: "ETFs" } });
    const rows = [
      { row: r1, def: bucketDef("armed") },
      { row: r2, def: bucketDef("etfs") },
    ];
    const counts = countFilterCandidates(rows, (r) =>
      r.member.ticker === "J" ? { ...emptySigs, insider: ins } : emptySigs,
    );
    expect(counts).toEqual({ total: 2, actionable: 1, insiderBuys: 1, hotRvol: 0, silent: 1 });
    expect(rowMatchesFilter(r2, bucketDef("etfs"), emptySigs, { ...DEFAULT_BASKET_FILTER, hideSilent: true })).toBe(
      false,
    );
  });
});
