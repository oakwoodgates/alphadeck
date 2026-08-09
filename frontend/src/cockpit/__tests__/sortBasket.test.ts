import { describe, expect, it } from "vitest";

import type {
  BasketMember,
  DisplaySignal,
  MemberCallOut,
  ScoredMemberOut,
} from "../../api/hooks";
import type { BucketRow } from "../buckets";
import {
  compareRows,
  nextSort,
  sortKey,
  sortRenderRows,
  type RowSignals,
  type SortState,
} from "../sortBasket";

// The pure sort machinery behind the sortable basket columns. The load-bearing checks: each column
// reads the RIGHT value off the SAME access path the cell renders from; a "—" cell is an ABSENT
// (null) key, never a number; nulls-last holds in BOTH directions; the insider key is buyers-first;
// and the render-row sorter drops nothing (a re-order, never a filter) with a stable tie-break.

// --- fixture builders (loose partials cast to the wire types — test-only) -----------------------
function row(
  over: {
    ordinal?: number;
    ticker?: string;
    sid?: string | null;
    scored?: Partial<ScoredMemberOut> | null;
    call?: Partial<MemberCallOut> | null;
  } = {},
): BucketRow {
  const sid = over.sid === undefined ? `s-${over.ticker ?? "x"}` : over.sid;
  return {
    member: {
      ticker: over.ticker ?? "AAA",
      role: "core",
      authored_by: "operator_set",
      security_id: sid,
    } as BasketMember,
    ordinal: over.ordinal ?? 0,
    call: (over.call ?? null) as MemberCallOut | null,
    scored: (over.scored ?? null) as ScoredMemberOut | null,
    bucket: "quiet",
  };
}

function sig(kind: string, metrics: { key: string; value: number | null }[]): DisplaySignal {
  return {
    kind,
    label: kind,
    metrics: metrics.map((m) => ({
      key: m.key,
      label: m.key,
      value: m.value,
      unit: "count",
      tone: null,
      note: null,
    })),
    basis: {
      source: "x",
      params: {},
      bars_used: null,
      window_start: null,
      window_end: null,
      note: null,
    },
  } as DisplaySignal;
}

const NO_SIGNALS: RowSignals = { sma: null, trail: null, rvol: null, insider: null };
const ctx = (r: BucketRow, signals: Partial<RowSignals> = {}) => ({
  row: r,
  signals: { ...NO_SIGNALS, ...signals },
});
const scoredCap = (over: Partial<ScoredMemberOut>): Partial<ScoredMemberOut> => ({
  market_cap: { pips: null, value: null, provenance: [] },
  ...over,
});

describe("sortKey — per-column value access (the crux)", () => {
  it("reads a return window off the trailing member by its metric key", () => {
    const t = sig("trailing_returns", [
      { key: "ret_30d", value: -21.96 },
      { key: "ret_1y", value: 128.4 },
    ]);
    const r = row({ ticker: "OKLO" });
    expect(sortKey("ret_30d", ctx(r, { trail: t }))).toEqual([-21.96]);
    expect(sortKey("ret_1y", ctx(r, { trail: t }))).toEqual([128.4]);
  });

  it("reads SMA off pct_vs_slow, and RVOL|8 / RVOL|20 off rvol / rvol20", () => {
    const r = row({ ticker: "HIMS" });
    const smaSig = sig("sma_position", [{ key: "pct_vs_slow", value: 4.2 }]);
    const rvolSig = sig("rvol", [
      { key: "rvol", value: 2.1 },
      { key: "rvol20", value: 1.4 },
    ]);
    expect(sortKey("sma", ctx(r, { sma: smaSig }))).toEqual([4.2]);
    expect(sortKey("rvol8", ctx(r, { rvol: rvolSig }))).toEqual([2.1]);
    expect(sortKey("rvol20", ctx(r, { rvol: rvolSig }))).toEqual([1.4]);
  });

  it("is null for an absent metric (a '—' cell → nulls-last)", () => {
    const t = sig("trailing_returns", [{ key: "ret_90d", value: null }]);
    const r = row({ ticker: "FISN" });
    expect(sortKey("ret_90d", ctx(r, { trail: t }))).toBeNull(); // value null
    expect(sortKey("ret_1y", ctx(r, { trail: t }))).toBeNull(); // metric absent
    expect(sortKey("rvol8", ctx(r))).toBeNull(); // no rvol member at all
  });

  it("insider key is [buyers, buys] (breadth-first) and null when there are no buys", () => {
    const ins = sig("insider_flow_90d", [
      { key: "buy_count_30d", value: 8 },
      { key: "distinct_buyers_30d", value: 3 },
      { key: "buy_count", value: 0 },
      { key: "distinct_buyers", value: 0 },
    ]);
    const r = row({ ticker: "HIMS" });
    expect(sortKey("ins_30d", ctx(r, { insider: ins }))).toEqual([3, 8]); // buyers primary, buys secondary
    expect(sortKey("ins_90d", ctx(r, { insider: ins }))).toBeNull(); // 0 buys → the cell's "—" → null
  });

  it("reads mktcap, exit_by (as ms), ticker/name (lowercased) and type (super order + leaf)", () => {
    const r = row({
      ticker: "VST",
      scored: scoredCap({
        name: "Vistra",
        business_supersector: "energy_utilities",
        business_type: "utilities",
        market_cap: { pips: null, value: 5e9, provenance: [] },
      }),
      call: { exit_by: "2026-12-08" } as MemberCallOut,
    });
    expect(sortKey("mktcap", ctx(r))).toEqual([5e9]);
    expect(sortKey("ticker", ctx(r))).toEqual(["vst"]);
    expect(sortKey("name", ctx(r))).toEqual(["vistra"]);
    // energy_utilities leads SUPERSECTOR_ORDER (index 0); the leaf label is the tiebreak
    expect(sortKey("type", ctx(r))).toEqual([0, "utilities"]);
    expect(sortKey("exit_by", ctx(r))).toEqual([Date.parse("2026-12-08T00:00:00Z")]);
  });

  it("type/name/exit_by/mktcap are null when the underlying field is absent", () => {
    const bare = row({ ticker: "ZZZ", scored: scoredCap({}) }); // no name, no super, cap value null
    expect(sortKey("type", ctx(bare))).toBeNull(); // no super-sector (ETF sleeve / un-enriched)
    expect(sortKey("name", ctx(bare))).toBeNull();
    expect(sortKey("mktcap", ctx(bare))).toBeNull();
    expect(sortKey("exit_by", ctx(bare))).toBeNull(); // no member call
  });
});

describe("compareRows — nulls-last, both directions", () => {
  it("sinks an absent value to the bottom in BOTH asc and desc", () => {
    const has = ctx(row({ ordinal: 0, ticker: "A" }), {
      trail: sig("trailing_returns", [{ key: "ret_30d", value: 5 }]),
    });
    const none = ctx(row({ ordinal: 1, ticker: "B" })); // no trail → null key
    for (const dir of ["asc", "desc"] as const) {
      const s: SortState = { col: "ret_30d", dir };
      expect(compareRows(has, none, s)).toBeLessThan(0); // present sorts before absent
      expect(compareRows(none, has, s)).toBeGreaterThan(0); // absent always after present
    }
  });

  it("returns 0 for two absent values (stable → default order kept)", () => {
    const a = ctx(row({ ordinal: 0 }));
    const b = ctx(row({ ordinal: 1 }));
    expect(compareRows(a, b, { col: "rvol8", dir: "desc" })).toBe(0);
  });

  it("flips a present-vs-present comparison with direction, but never the null handling", () => {
    const hi = ctx(row({ ticker: "HI" }), {
      trail: sig("trailing_returns", [{ key: "ret_30d", value: 20 }]),
    });
    const lo = ctx(row({ ticker: "LO" }), {
      trail: sig("trailing_returns", [{ key: "ret_30d", value: 5 }]),
    });
    expect(compareRows(hi, lo, { col: "ret_30d", dir: "desc" })).toBeLessThan(0); // 20 before 5
    expect(compareRows(hi, lo, { col: "ret_30d", dir: "asc" })).toBeGreaterThan(0); // 5 before 20
  });
});

describe("sortRenderRows — ranks within a group, drops nothing, stable", () => {
  it("ranks desc with nulls last and preserves row count (a re-order, never a filter)", () => {
    const data = [
      { row: row({ ordinal: 0, ticker: "A" }), v: 5 },
      { row: row({ ordinal: 1, ticker: "B" }), v: null as number | null },
      { row: row({ ordinal: 2, ticker: "C" }), v: 20 },
      { row: row({ ordinal: 3, ticker: "D" }), v: null as number | null },
    ];
    const byOrd = new Map(data.map((d) => [d.row.ordinal, d.v]));
    const signalsFor = (r: BucketRow): RowSignals => {
      const v = byOrd.get(r.ordinal);
      return {
        ...NO_SIGNALS,
        trail:
          v == null ? null : sig("trailing_returns", [{ key: "ret_30d", value: v }]),
      };
    };
    const out = sortRenderRows(data, { col: "ret_30d", dir: "desc" }, signalsFor);
    expect(out).toHaveLength(4); // nothing dropped
    // 20, 5, then the two nulls LAST in their default (ordinal) order — stable tie-break
    expect(out.map((d) => d.row.member.ticker)).toEqual(["C", "A", "B", "D"]);
  });
});

describe("nextSort — the 3-state click cycle", () => {
  it("cycles a column desc → asc → off, and a fresh column starts desc", () => {
    expect(nextSort(null, "ret_30d")).toEqual({ col: "ret_30d", dir: "desc" });
    expect(nextSort({ col: "ret_30d", dir: "desc" }, "ret_30d")).toEqual({
      col: "ret_30d",
      dir: "asc",
    });
    expect(nextSort({ col: "ret_30d", dir: "asc" }, "ret_30d")).toBeNull(); // off = restore default
    // switching to a different column starts fresh at desc
    expect(nextSort({ col: "ret_30d", dir: "asc" }, "mktcap")).toEqual({
      col: "mktcap",
      dir: "desc",
    });
  });
});
