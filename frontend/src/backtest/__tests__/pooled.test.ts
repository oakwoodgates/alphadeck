import { describe, expect, it } from "vitest";

import {
  fmtN,
  fmtPct,
  fmtStat,
  metricColumns,
  mixRows,
  sortedSlices,
  statTone,
  timingNullCaveat,
} from "../pooled";

// The pooled panel's pure display logic. The properties under test are the honesty rules: nothing is
// derived that the backend did not compute, an absent statistic reads as unknown rather than as zero,
// and a slice below the gate is marked rather than dropped.

const stat = (n: number, median: number | null) => ({ n, median, mean: median });

describe("fmtStat / statTone", () => {
  it("formats a median as a signed percentage", () => {
    expect(fmtStat(stat(41, 0.0412))).toBe("+4.1%");
    expect(fmtStat(stat(41, -0.0412))).toBe("-4.1%");
  });

  it("renders an EMPTY statistic as unknown, never as a flat zero", () => {
    // n=0 with a null median is "we could not measure this", which is a different claim from "it
    // came out at zero" — and the second one would read as a finding.
    expect(fmtStat(stat(0, null))).toBe("—");
    expect(fmtStat(undefined)).toBe("—");
    expect(statTone(stat(0, null))).toBe("");
  });

  it("keeps a REAL zero, because a median of zero is a measurement", () => {
    expect(fmtStat(stat(12, 0))).toBe("0.0%");
    expect(statTone(stat(12, 0))).toBe("");
  });

  it("tones a positive and a negative median differently", () => {
    expect(statTone(stat(5, 0.01))).toBe("pos");
    expect(statTone(stat(5, -0.01))).toBe("neg");
  });

  it("sizes every figure — a bare number is never rendered", () => {
    expect(fmtN(stat(41, 0.1))).toBe("n=41");
    expect(fmtN(stat(0, null))).toBe("");
  });
});

describe("metricColumns", () => {
  const metric = {
    name: "arm_timing_forward_return",
    claim: "timing",
    actual: stat(41, 0.04),
    excess: stat(41, 0.01),
    vs_timing: stat(2050, 0.009),
    vs_name: stat(2050, 0.02),
    insufficient_n: false,
    note: "",
  };

  it("puts the actual first and its three readings beside it", () => {
    expect(metricColumns(metric).map((c) => c.id)).toEqual([
      "actual",
      "excess",
      "vs_timing",
      "vs_name",
    ]);
  });

  it("derives NO gap figure of its own", () => {
    // The subtraction actual − null is the number a reader would quote, and a difference of two
    // medians has no n and no dispersion. The four figures are rendered; the comparison is the
    // reader's, exactly as the backend-authored banner instructs.
    const values = metricColumns(metric).map((c) => c.stat.median);
    expect(values).toEqual([0.04, 0.01, 0.009, 0.02]);
    expect(metricColumns(metric)).toHaveLength(4);
  });

  it("explains each column in words rather than assuming the reader knows the jargon", () => {
    for (const c of metricColumns(metric)) expect(c.title.length).toBeGreaterThan(40);
  });
});

describe("sortedSlices", () => {
  const s = (n: number, key: Record<string, string>) => ({
    key,
    n,
    actual: stat(n, 0.1),
    excess: stat(n, 0.1),
    vs_timing: stat(n, 0.1),
    vs_name: stat(n, 0.1),
    insufficient_n: n < 5,
  });

  it("orders by sample size, largest first — the rows that can carry weight are read first", () => {
    const out = sortedSlices([s(2, { a: "x" }), s(19, { a: "y" }), s(7, { a: "z" })]);
    expect(out.map((x) => x.n)).toEqual([19, 7, 2]);
  });

  it("SORTS, never filters — a gated slice keeps its place in the list", () => {
    // "we looked and there were three" is a finding; a row that disappeared would read as none.
    const out = sortedSlices([s(3, { a: "x" }), s(19, { a: "y" })]);
    expect(out).toHaveLength(2);
    expect(out.some((x) => x.insufficient_n)).toBe(true);
  });
});

describe("mixRows", () => {
  it("orders by count and reports each share of the TOTAL episodes", () => {
    const rows = mixRows({ insider: 40, theme: 10 }, 100);
    expect(rows.map((r) => r.label)).toEqual(["insider", "theme"]);
    expect(rows[0].pct).toBeCloseTo(0.4);
  });

  it("does NOT renormalize a partial mix to 100%", () => {
    // A mix that does not cover every episode must not pretend it does — the shares are of the run,
    // not of the mix.
    const rows = mixRows({ insider: 1 }, 10);
    expect(rows[0].pct).toBeCloseTo(0.1);
  });

  it("renders nothing rather than dividing by zero on an empty run", () => {
    expect(mixRows(undefined, 0)).toEqual([]);
    expect(mixRows({ a: 1 }, 0)[0].pct).toBeNull();
  });
});

describe("fmtPct", () => {
  it("is '—' for an unknown share, never 'NaN%'", () => {
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(undefined)).toBe("—");
    expect(fmtPct(0.8231)).toBe("82%");
  });
});

describe("timingNullCaveat", () => {
  it("warns when the timing null had almost nothing to draw from", () => {
    // The silent failure this exists to catch: on a short window every draw prices a near-empty
    // forward window, so vs_timing lands on top of the actual and reads as "ties with chance".
    const line = timingNullCaveat(4, 12);
    expect(line).toContain("4 sessions");
    expect(line).toContain("not that the timing is worthless");
  });

  it("is SILENT on a healthy window — the line marks an exception, not every run", () => {
    expect(timingNullCaveat(250, 120)).toBeNull();
  });

  it("is silent when there were no episodes at all — nothing to caveat", () => {
    expect(timingNullCaveat(0, 0)).toBeNull();
  });
});
