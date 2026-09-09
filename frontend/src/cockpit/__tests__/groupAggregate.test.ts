import { describe, expect, it } from "vitest";

import type { BasketMember, DisplaySignal } from "../../api/hooks";
import type { BucketRow } from "../buckets";
import { AGG_MIN_PRICED, AGG_RETURN_KEY, groupMoving, median } from "../groupAggregate";

// The pure aggregate behind the group header's moving line. The load-bearing checks: the statistic
// is the MEDIAN (a single blowup does not swing it the way a mean would — asserted median ≠ mean on a
// skewed set), it reads the 7d key ONLY, N is the FULL group (an unpriced row counts in N and not in
// the median), and under three priced rows there is NO median (null → "—"), never a fabricated 0.

// --- fixture builders (loose partials cast to the wire types — test-only, the sortBasket precedent) --
function row(ticker: string): { row: BucketRow } {
  return {
    row: {
      member: {
        ticker,
        role: "core",
        authored_by: "operator_set",
        security_id: `s-${ticker}`,
      } as BasketMember,
      ordinal: 0,
      call: null,
      scored: null,
      bucket: "quiet",
    },
  };
}

function trail(metrics: { key: string; value: number | null }[]): DisplaySignal {
  return {
    kind: "trailing_returns",
    label: "Trailing returns",
    metrics: metrics.map((m) => ({
      key: m.key,
      label: m.key,
      value: m.value,
      unit: "pct",
      tone: null,
      note: null,
    })),
    basis: {
      source: "fact_price_eod",
      params: {},
      bars_used: null,
      window_start: null,
      window_end: null,
      note: null,
    },
  } as DisplaySignal;
}

/** A group: one row per ticker + a `trailFor` resolving each to its trail signal (absent → null). */
function group(spec: Record<string, DisplaySignal | null>) {
  const rows = Object.keys(spec).map(row);
  const trailFor = (r: BucketRow) => spec[r.member.ticker] ?? null;
  return { rows, trailFor };
}
const ret7 = (v: number | null) => trail([{ key: AGG_RETURN_KEY, value: v }]);
const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;

describe("median", () => {
  it("is the middle value on an odd count, the mean of the two middles on an even one", () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([7])).toBe(7);
    expect(median([-6, -1, 40])).toBe(-1); // unsorted, signed
  });

  it("is null on an empty list and never mutates its input", () => {
    expect(median([])).toBeNull();
    const xs = [3, 1, 2];
    median(xs);
    expect(xs).toEqual([3, 1, 2]);
  });
});

describe("groupMoving", () => {
  it("reads the 7d column's own key", () => {
    expect(AGG_RETURN_KEY).toBe("ret_7d");
  });

  it("is the MEDIAN over the priced rows — a single blowup does not swing it the way a mean would", () => {
    const { rows, trailFor } = group({
      A: ret7(-6),
      B: ret7(-1),
      C: ret7(2),
      D: ret7(3.5),
      E: ret7(40), // the blowup
    });
    const stat = groupMoving(rows, trailFor);
    expect(stat).toEqual({ n: 5, priced: 5, median: 2 });
    // the mean of the same set is +7.7 — hauled up by E's +40; the header must never read that
    const m = mean([-6, -1, 2, 3.5, 40]);
    expect(m).toBeCloseTo(7.7, 5);
    expect(stat.median).not.toBe(m);
    // and the load-bearing direction: four −1%s and one +40% is a group that is NOT moving up
    const skew = group({ A: ret7(-1), B: ret7(-1), C: ret7(-1), D: ret7(-1), E: ret7(40) });
    expect(groupMoving(skew.rows, skew.trailFor).median).toBe(-1);
    expect(mean([-1, -1, -1, -1, 40])).toBeGreaterThan(0);
  });

  it("counts an unpriced row in N but leaves it out of the median — no member, no 7d key, a null 7d", () => {
    const { rows, trailFor } = group({
      A: ret7(1),
      B: ret7(2),
      C: ret7(3),
      D: null, // no trailing member at all
      E: trail([{ key: "ret_30d", value: 99 }]), // a 30d only — the 7d key is absent
      F: ret7(null), // thin history: the wire's honest null
    });
    expect(groupMoving(rows, trailFor)).toEqual({ n: 6, priced: 3, median: 2 });
  });

  it("reads the 7d key ONLY — a group priced on 30d alone has no median (the one-window rule)", () => {
    const only30 = trail([
      { key: "ret_30d", value: 12 },
      { key: "ret_1d", value: 1 },
    ]);
    const { rows, trailFor } = group({ A: only30, B: only30, C: only30, D: only30 });
    expect(groupMoving(rows, trailFor)).toEqual({ n: 4, priced: 0, median: null });
  });

  it("withholds the median under three priced rows — null, never a fabricated 0.0", () => {
    expect(AGG_MIN_PRICED).toBe(3);
    const none = group({ A: null, B: null });
    expect(groupMoving(none.rows, none.trailFor)).toEqual({ n: 2, priced: 0, median: null });
    const one = group({ A: ret7(5), B: null, C: null, D: null });
    expect(groupMoving(one.rows, one.trailFor)).toEqual({ n: 4, priced: 1, median: null });
    const two = group({ A: ret7(5), B: ret7(-5), C: null });
    expect(groupMoving(two.rows, two.trailFor)).toEqual({ n: 3, priced: 2, median: null });
    // exactly three priced is the floor — computed from here up
    const three = group({ A: ret7(5), B: ret7(-5), C: ret7(0.5), D: null });
    expect(groupMoving(three.rows, three.trailFor)).toEqual({ n: 4, priced: 3, median: 0.5 });
  });

  it("is a read: the rows come back untouched, in their incoming order (no re-order, no drop)", () => {
    const { rows, trailFor } = group({ Z: ret7(9), A: ret7(-9), M: ret7(0) });
    const before = rows.map((r) => r.row.member.ticker);
    groupMoving(rows, trailFor);
    expect(rows.map((r) => r.row.member.ticker)).toEqual(before);
    expect(rows).toHaveLength(3);
  });

  it("is empty-safe: zero rows reads n 0 / no median", () => {
    expect(groupMoving([], () => null)).toEqual({ n: 0, priced: 0, median: null });
  });
});
