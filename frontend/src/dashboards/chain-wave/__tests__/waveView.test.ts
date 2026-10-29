import { describe, expect, it } from "vitest";

import type { ChainWaveWeekOut } from "../useChainWave";
import {
  SATURATE_PP,
  cellTone,
  filterCards,
  fmtPp,
  gapWords,
  readoutWords,
  rowLabel,
  spyBar,
  weekHeaders,
  weekWords,
} from "../waveView";

// The Chain Wave's pure view helpers: the muted diverging tone (symmetric, saturating at ±10 pp, halved on a thin
// row), SPY's bar, the week words and header, the readout's words, the role rows' labels and the picker's filter.

const week = (monday: string, over: Partial<ChainWaveWeekOut> = {}): ChainWaveWeekOut => ({
  monday,
  first_session: monday,
  close: monday,
  sessions: 5,
  to_date: false,
  ...over,
});

describe("cellTone", () => {
  it("is symmetric and saturates at ±10 pp", () => {
    expect(SATURATE_PP).toBe(10);
    expect(cellTone(5, false)).toEqual({ tone: "pos", strength: 0.5 });
    expect(cellTone(-5, false)).toEqual({ tone: "neg", strength: 0.5 });
    expect(cellTone(25, false)).toEqual({ tone: "pos", strength: 1 });
    expect(cellTone(-25, false)).toEqual({ tone: "neg", strength: 1 });
  });

  it("halves a thin row's strength, and a missing value is a gap, a zero flat", () => {
    expect(cellTone(10, true)).toEqual({ tone: "pos", strength: 0.5 });
    expect(cellTone(null, false)).toEqual({ tone: "gap", strength: 0 });
    expect(cellTone(0, false)).toEqual({ tone: "flat", strength: 0 });
  });
});

describe("the words", () => {
  it("words SPY's bar, the percentage points and each gap", () => {
    expect(spyBar(2)).toEqual({ dir: "up", share: 0.4 });
    expect(spyBar(-30)).toEqual({ dir: "down", share: 1 });
    expect(spyBar(null)).toBeNull();
    expect(fmtPp(1.25)).toBe("+1.3 pp");
    expect(fmtPp(-0.04)).toBe("-0.0 pp");
    expect(fmtPp(null)).toBe("—");
    expect(gapWords("no_spy")).toBe("SPY had no return that week");
    expect(gapWords("no_return")).toBe("no name had a daily return that week");
    expect(gapWords(null)).toBeNull();
  });

  it("words a week's dates, to date or with no session", () => {
    expect(weekWords(week("2026-10-19", { close: "2026-10-23" }))).toBe("Oct 19, 2026 – Oct 23, 2026");
    expect(weekWords(week("2026-10-26", { close: "2026-10-28", to_date: true }))).toBe(
      "Oct 26, 2026 – Oct 28, 2026 (week to date)",
    );
    expect(weekWords(week("2026-11-23", { first_session: null, close: null, sessions: 0 }))).toBe(
      "week of Nov 23, 2026, no session",
    );
  });

  it("labels the month where it changes and the to-date last week", () => {
    const weeks = [
      week("2026-09-21"),
      week("2026-09-28"),
      week("2026-10-05"),
      week("2026-10-12"),
      week("2026-10-19", { to_date: true }),
    ];
    expect(weekHeaders(weeks)).toEqual(["Sep", "", "Oct", "", "wk to date"]);
  });

  it("the readout is numbers only, in neutral words", () => {
    expect(readoutWords({ weeks: 4, start: "2026-10-02", end: "2026-10-28", payers: 3.21, counting: null })).toBe(
      "Last 4 weeks vs SPY: payers +3.2 pp · counting names —",
    );
  });

  it("labels the role rows as groups, every other row by its label", () => {
    expect(rowLabel({ kind: "role", label: "payer", role: "payer" })).toBe("Payers");
    expect(rowLabel({ kind: "role", label: "direct_read", role: "direct_read" })).toBe("Direct reads");
    expect(rowLabel({ kind: "link", label: "Reactors", role: null })).toBe("Reactors");
  });
});

describe("filterCards", () => {
  const cards = [
    { thesis_id: "a", name: "AI power", links: 3 },
    { thesis_id: "b", name: "Copper", links: 2 },
    { thesis_id: "c", name: "Grid power", links: 4 },
  ];
  it("keeps the server's order and matches any case", () => {
    expect(filterCards(cards, "POWER").map((c) => c.thesis_id)).toEqual(["a", "c"]);
    expect(filterCards(cards, "  ").map((c) => c.thesis_id)).toEqual(["a", "b", "c"]);
  });
});
