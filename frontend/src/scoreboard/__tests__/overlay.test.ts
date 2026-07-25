import { describe, expect, it } from "vitest";

import type { InsiderBuyOut, PriceBar, ScoreboardEpisodeOut, TriggerRefOut } from "../../api/hooks";
import {
  buildOverlayEvents,
  closeOnDate,
  defaultVisibleRange,
  legendEntries,
  overlayTooltip,
  stackChips,
} from "../overlay";

// Pure overlay logic — the parts a canvas can't render in jsdom (numbering, tooltip content, legend, the
// default visible range, close-on-date, collision-stacking). The imperative coordinate positioning +
// pan/zoom are live-verified by eye.

function buy(over: Partial<InsiderBuyOut> = {}): InsiderBuyOut {
  return {
    d: "2026-06-01",
    insider_name: "A Buyer",
    insider_role: "CEO",
    shares: 1000,
    usd: 50000,
    aff_10b5_1: false,
    disclosed: "2026-06-01",
    ...over,
  };
}

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    arm_date: "2026-06-01",
    warm_date: null,
    dearm_date: null,
    exit_by: null,
    close_reason: "",
    triggers_at_arm: [],
    ...over,
  } as unknown as ScoreboardEpisodeOut;
}

function bar(d: string, close: number): Pick<PriceBar, "d" | "close"> {
  return { d, close };
}

describe("defaultVisibleRange — the episode by default (R2)", () => {
  it("is [arm − 130 trading days, last bar]; clamps to the first bar when history is short", () => {
    const bars = Array.from({ length: 60 }, (_, i) => bar(`2026-01-${String(i + 1).padStart(2, "0")}`, 100));
    // arm at index 40; 40 − 130 < 0 → from clamps to the first bar
    const short = defaultVisibleRange(bars, bars[40].d);
    expect(short).toEqual({ from: bars[0].d, to: bars[59].d });
  });

  it("goes back exactly 130 trading days when there's room", () => {
    const bars = Array.from({ length: 200 }, (_, i) => bar(`d${String(i).padStart(3, "0")}`, 100));
    const r = defaultVisibleRange(bars, bars[180].d); // arm at index 180 → from = index 50
    expect(r).toEqual({ from: bars[50].d, to: bars[199].d });
  });

  it("is null with no bars", () => {
    expect(defaultVisibleRange([], "2026-06-01")).toBeNull();
  });
});

describe("closeOnDate — the market close on/around a date", () => {
  const bars = [bar("2026-06-01", 100), bar("2026-06-03", 102), bar("2026-06-08", 107)];
  it("returns the latest bar with d <= the date (weekend maps to the prior close)", () => {
    expect(closeOnDate(bars, "2026-06-02")).toBe(100);
    expect(closeOnDate(bars, "2026-06-03")).toBe(102);
    expect(closeOnDate(bars, "2026-06-30")).toBe(107);
  });
  it("is null before the first bar", () => {
    expect(closeOnDate(bars, "2026-05-31")).toBeNull();
  });
});

describe("buildOverlayEvents — stable, unified chronological 1..N (R1)", () => {
  // one loaded universe = all bars [floor, last]; the numbers are the same regardless of the visible range
  const BARS = [
    bar("2026-05-15", 100),
    bar("2026-05-25", 102),
    bar("2026-06-01", 104),
    bar("2026-06-10", 107),
    bar("2026-06-20", 110),
    bar("2026-06-30", 112),
  ];

  it("numbers every family in date order once (armed before its same-day triggers)", () => {
    const trig = { label: "insider cluster", kind: "insider" } as TriggerRefOut;
    const events = buildOverlayEvents(
      ep({
        warm_date: "2026-05-20",
        arm_date: "2026-06-01",
        dearm_date: "2026-06-20",
        close_reason: "aged out",
        exit_by: "2026-06-25",
        triggers_at_arm: [trig],
      }),
      [buy({ d: "2026-05-25" }), buy({ d: "2026-06-10" })],
      BARS,
    );
    expect(events.map((e) => [e.n, e.family, e.date])).toEqual([
      [1, "lifecycle", "2026-05-20"], // warmed
      [2, "insider", "2026-05-25"],
      [3, "lifecycle", "2026-06-01"], // armed — before the same-day trigger (insertion tiebreak)
      [4, "trigger", "2026-06-01"],
      [5, "insider", "2026-06-10"],
      [6, "lifecycle", "2026-06-20"], // dearmed
      [7, "lifecycle", "2026-06-25"], // exit-by
    ]);
  });

  it("is deterministic + unified — the numbers do not depend on any visible window (the R1 bug)", () => {
    const args = [
      ep({ arm_date: "2026-06-01" }),
      [buy({ d: "2026-05-25" }), buy({ d: "2026-06-10" })],
      BARS,
    ] as const;
    const a = buildOverlayEvents(...args);
    const b = buildOverlayEvents(...args);
    expect(a.map((e) => e.n)).toEqual(b.map((e) => e.n)); // same universe → same numbers, always
    // the 06-10 insider is #3 (unified across families: warmed?none, insider 05-25 #1, armed #2, insider #3)
    expect(a.find((e) => e.family === "insider" && e.date === "2026-06-10")?.n).toBe(3);
  });

  it("drops an event past the last drawn bar (a still-future exit-by horizon)", () => {
    const events = buildOverlayEvents(ep({ exit_by: "2026-08-01" }), [], BARS);
    expect(events.some((e) => e.date === "2026-08-01")).toBe(false); // beyond last (2026-06-30)
    expect(events.map((e) => e.family)).toEqual(["lifecycle"]); // just the armed anchor
  });

  it("attaches the market close that day + the return vs now to each insider event (R3)", () => {
    const events = buildOverlayEvents(ep(), [buy({ d: "2026-06-10" })], BARS);
    const ins = events.find((e) => e.family === "insider");
    expect(ins?.family === "insider" && ins.closeThatDay).toBe(107); // close on 2026-06-10
    // pctVsNow = (last 112 − 107) / 107
    expect(ins?.family === "insider" ? ins.pctVsNow : null).toBeCloseTo((112 - 107) / 107, 5);
  });
});

describe("overlayTooltip — provenance-first, honest disclosure lag + price context", () => {
  it("insider: who, size, transaction date, disclosure lag, and the market price that day (R3)", () => {
    const t = overlayTooltip({
      n: 1,
      family: "insider",
      date: "2026-01-15",
      closeThatDay: 217,
      pctVsNow: -0.12,
      buy: buy({
        d: "2026-01-15",
        insider_name: "Jane Doe",
        insider_role: "CEO",
        shares: 10000,
        usd: 2_100_000,
        disclosed: "2026-07-01",
      }),
    });
    expect(t.title).toBe("Jane Doe (CEO)");
    expect(t.lines[0]).toBe("bought 10,000 sh @ $2.1M"); // the FILL (usd) — distinct from the market price
    expect(t.lines[1]).toBe("transacted 2026-01-15");
    expect(t.lines[2]).toBe("disclosed 167d later (2026-07-01)"); // Jan 15 → Jul 1 = 167 days
    expect(t.lines).toContain("stock $217 that day · −12% vs now"); // the market close + move since
  });

  it("insider: no price line when the close that day is unknown", () => {
    const t = overlayTooltip({
      n: 1,
      family: "insider",
      date: "2026-01-15",
      closeThatDay: null,
      pctVsNow: null,
      buy: buy({ d: "2026-01-15", disclosed: "2026-01-15" }),
    });
    expect(t.lines.some((l) => l.startsWith("stock"))).toBe(false);
    expect(t.lines.some((l) => l.startsWith("disclosed"))).toBe(false); // same-day disclosure
  });

  it("insider: a 10b5-1 plan is noted", () => {
    const t = overlayTooltip({
      n: 1,
      family: "insider",
      date: "2026-01-15",
      closeThatDay: null,
      pctVsNow: null,
      buy: buy({ aff_10b5_1: true }),
    });
    expect(t.lines).toContain("10b5-1 plan (pre-scheduled)");
  });

  it("trigger: the label is the title, kind + ticker the source line", () => {
    const t = overlayTooltip({
      n: 1,
      family: "trigger",
      date: "2026-06-01",
      closeThatDay: 100,
      trigger: { label: "3 insiders bought $2.1M", kind: "insider", ticker: "IBM" } as TriggerRefOut,
    });
    expect(t.title).toBe("3 insiders bought $2.1M");
    expect(t.lines).toEqual(["insider · IBM"]);
  });

  it("lifecycle: armed / de-armed(reason) / exit-by titles", () => {
    const lc = (kind: "armed" | "dearmed" | "exit_by", closeReason?: string) =>
      overlayTooltip({ n: 1, family: "lifecycle", date: "2026-06-01", closeThatDay: 100, kind, closeReason });
    expect(lc("armed").title).toBe("armed");
    expect(lc("dearmed", "aged out").title).toBe("de-armed (aged out)");
    expect(lc("exit_by").title).toBe("exit-by (horizon)");
  });
});

describe("legendEntries — present families only (honest loudness / #7)", () => {
  it("renders only the families in the events, in the fixed order", () => {
    const events = buildOverlayEvents(ep({ arm_date: "2026-06-01" }), [buy({ d: "2026-06-01" })], [
      bar("2026-05-15", 100),
      bar("2026-06-01", 104),
      bar("2026-06-30", 112),
    ]);
    // insider + lifecycle (armed), no trigger → the legend omits trigger
    expect(legendEntries(events).map((l) => l.family)).toEqual(["insider", "lifecycle"]);
  });

  it("is empty when there are no events", () => {
    expect(legendEntries([])).toEqual([]);
  });
});

describe("stackChips — never overlaps, never drops (#9)", () => {
  it("spreads out chips whose x are far apart onto one level", () => {
    const { placed, overflow } = stackChips(
      [
        { n: 1, x: 0, family: "insider" },
        { n: 2, x: 40, family: "insider" },
        { n: 3, x: 80, family: "insider" },
      ],
      { chipW: 20, maxLevels: 3 },
    );
    expect(overflow).toEqual([]);
    expect(placed.every((p) => p.level === 0)).toBe(true);
  });

  it("stacks colliding chips to distinct levels; a within-level pair never overlaps", () => {
    const { placed } = stackChips(
      [
        { n: 1, x: 10, family: "insider" },
        { n: 2, x: 12, family: "trigger" },
        { n: 3, x: 40, family: "insider" },
      ],
      { chipW: 20, maxLevels: 3 },
    );
    const byN = Object.fromEntries(placed.map((p) => [p.n, p]));
    expect([byN[1].level, byN[2].level, byN[3].level]).toEqual([0, 1, 0]);
    const lvl0 = placed.filter((p) => p.level === 0).sort((a, b) => a.x - b.x);
    for (let i = 1; i < lvl0.length; i++) expect(lvl0[i].x - lvl0[i - 1].x).toBeGreaterThanOrEqual(20);
  });

  it("spills past maxLevels into overflow — placed + overflow always equals the input (no drop)", () => {
    const items = [1, 2, 3, 4, 5].map((n) => ({ n, x: 5, family: "insider" as const }));
    const { placed, overflow } = stackChips(items, { chipW: 20, maxLevels: 3 });
    expect(placed.length).toBe(3);
    expect(overflow.length).toBe(2);
    expect(placed.length + overflow.length).toBe(items.length);
  });
});
