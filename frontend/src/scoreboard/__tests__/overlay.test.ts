import { describe, expect, it } from "vitest";

import type { InsiderBuyOut, PriceBar, ScoreboardEpisodeOut, TriggerRefOut } from "../../api/hooks";
import {
  buildOverlayEvents,
  closeOnDate,
  defaultVisibleRange,
  insiderSetAside,
  legendEntries,
  overlayTooltip,
  stackChips,
  triggerLinks,
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
    ingested: "2026-06-01", // == disclosed by default -> a single "disclosed" line (the two-clock default)
    character: "open_market",
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

  // A1: a trigger chip sits at its OWN fire date (`event_date` — the SignalEvent's valid time) when the
  // loaded window can show it, and falls back to the arm otherwise. The arm linkage moves to the tooltip.
  it("places a trigger at its event_date when that date is inside the loaded window", () => {
    const trig = {
      label: "insider cluster",
      kind: "insider",
      event_date: "2026-05-20",
    } as TriggerRefOut;
    const events = buildOverlayEvents(ep({ arm_date: "2026-06-01", triggers_at_arm: [trig] }), [], BARS);
    // the trigger now PRECEDES its own arm — the numbering is a per-render identity, so a lower
    // number for the earlier fact is correct, not a regression
    expect(events.map((e) => [e.n, e.family, e.date])).toEqual([
      [1, "trigger", "2026-05-20"],
      [2, "lifecycle", "2026-06-01"], // armed
    ]);
    const t = events[0];
    expect(t.family === "trigger" && t.armDate).toBe("2026-06-01"); // the arm linkage rides along
    // the guide-line anchors on ITS date's close, not the arm's (05-20 → the 05-15 bar, 100)
    expect(t.closeThatDay).toBe(100);
  });

  it("falls back to the arm date when event_date predates the first loaded bar (no honest x)", () => {
    const trig = { label: "old fire", kind: "insider", event_date: "2026-01-02" } as TriggerRefOut;
    const events = buildOverlayEvents(ep({ arm_date: "2026-06-01", triggers_at_arm: [trig] }), [], BARS);
    // clamping it onto 2026-05-15 would assert a bar that never saw the fire — it rides at the arm
    expect(events.map((e) => [e.family, e.date])).toEqual([
      ["lifecycle", "2026-06-01"], // armed keeps the insertion tiebreak
      ["trigger", "2026-06-01"],
    ]);
  });

  it("falls back to the arm date when event_date is null (the pre-A1 wire shape)", () => {
    const trig = { label: "no date", kind: "insider", event_date: null } as TriggerRefOut;
    const events = buildOverlayEvents(ep({ arm_date: "2026-06-01", triggers_at_arm: [trig] }), [], BARS);
    expect(events.find((e) => e.family === "trigger")?.date).toBe("2026-06-01");
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
        ingested: "2026-07-01", // == disclosed here -> just the one disclosed line (two-clock cases tested below)
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
      buy: buy({ d: "2026-01-15", disclosed: "2026-01-15", ingested: "2026-01-15" }),
    });
    expect(t.lines.some((l) => l.startsWith("stock"))).toBe(false);
    expect(t.lines.some((l) => l.startsWith("disclosed"))).toBe(false); // same-day disclosure
    expect(t.lines.some((l) => l.startsWith("ingested"))).toBe(false); // and same-day ingest
  });

  // The two-clock disclosure model (the MRVL fix — #6/#9): `disclosed` = the real SEC acceptance date;
  // `ingested` = our recorded_at, a SECOND line only when it differs; a null `disclosed` falls back to the
  // "ingested" line only. Every render case, including the real cited MRVL example.
  const tip = (over: Partial<InsiderBuyOut>) =>
    overlayTooltip({
      n: 1,
      family: "insider",
      date: "2025-09-25",
      closeThatDay: null,
      pctVsNow: null,
      buy: buy({ d: "2025-09-25", ...over }),
    });

  it("(a) ingested == disclosed → the 'disclosed' line only", () => {
    const lines = tip({ disclosed: "2025-09-27", ingested: "2025-09-27" }).lines;
    expect(lines).toContain("disclosed 2d later (2025-09-27)");
    expect(lines.some((l) => l.startsWith("ingested"))).toBe(false);
  });

  it("(b) ingested != disclosed → BOTH lines (the real MRVL case, accession 0001628280-25-042718)", () => {
    // txn 2025-09-25 · disclosed ~2d (the plan's cited acceptance illustration) · re-ingested 2026-08-17
    const lines = tip({ disclosed: "2025-09-27", ingested: "2026-08-17" }).lines;
    expect(lines).toContain("disclosed 2d later (2025-09-27)"); // the honest ~2d disclosure
    expect(lines).toContain("ingested 326d later (2026-08-17)"); // the re-ingest lag, beside it, not instead
  });

  it("(c) disclosed == null → the 'ingested' line only (#9 fallback — honest at every rollout stage)", () => {
    const lines = tip({ disclosed: null, ingested: "2026-08-17" }).lines;
    expect(lines.some((l) => l.startsWith("disclosed"))).toBe(false);
    expect(lines).toContain("ingested 326d later (2026-08-17)");
  });

  it("insider: a 10b5-1 plan is noted — ONLY on an explicit true (tri-state)", () => {
    const tip = (aff: boolean | null) =>
      overlayTooltip({
        n: 1,
        family: "insider",
        date: "2026-01-15",
        closeThatDay: null,
        pctVsNow: null,
        buy: buy({ aff_10b5_1: aff }),
      });
    expect(tip(true).lines).toContain("10b5-1 plan (pre-scheduled)");
    // null (the pre-Dec-2022 unknown) and an explicit false are NEVER asserted "planned" (#9)
    expect(tip(null).lines.some((l) => l.includes("10b5-1"))).toBe(false);
    expect(tip(false).lines.some((l) => l.includes("10b5-1"))).toBe(false);
  });

  // S2c: the character line — why the buy did/didn't count (#6). Open-market is the unbadged norm;
  // the exceptions carry the operator-approved terse copy (honest loudness).
  it("insider: each non-open-market character gets its one terse line; open_market stays unbadged", () => {
    const tip = (character: InsiderBuyOut["character"]) =>
      overlayTooltip({
        n: 1,
        family: "insider",
        date: "2026-01-15",
        closeThatDay: null,
        pctVsNow: null,
        buy: buy({ character }),
      });
    expect(tip("self_filing").lines).toContain("issuer self-filing (not a personal buy)");
    expect(tip("primary_market").lines).toContain("primary-market (offer-price, set aside)");
    expect(tip("implausible").lines).toContain("implausible $ (bad source data, set aside)");
    const norm = tip("open_market").lines;
    expect(norm.some((l) => l.includes("set aside") || l.includes("self-filing"))).toBe(false);
  });

  it("insider: the character line coexists with the 10b5-1 note (a planned self-filing shows both)", () => {
    const t = overlayTooltip({
      n: 1,
      family: "insider",
      date: "2026-01-15",
      closeThatDay: null,
      pctVsNow: null,
      buy: buy({ character: "self_filing", aff_10b5_1: true }),
    });
    expect(t.lines).toContain("issuer self-filing (not a personal buy)");
    expect(t.lines).toContain("10b5-1 plan (pre-scheduled)");
  });

  // A1: the trigger card carries WHY (kind/ticker), HOW STRONG (grade), WHEN IT FED THE CALL (only when
  // the chip sits away from the arm), and WHAT IT RESTS ON (the capped per-source provenance refs).
  const trigTip = (trigger: Partial<TriggerRefOut>, date = "2026-06-01", armDate = "2026-06-01") =>
    overlayTooltip({
      n: 1,
      family: "trigger",
      date,
      armDate,
      closeThatDay: 100,
      trigger: { label: "3 insiders bought $2.1M", kind: "insider", ...trigger } as TriggerRefOut,
    });

  it("trigger: the label is the title, kind + ticker the source line", () => {
    const t = trigTip({ ticker: "IBM" });
    expect(t.title).toBe("3 insiders bought $2.1M");
    expect(t.lines).toEqual(["insider · IBM"]);
  });

  it("trigger: the grade rides as its own line when the wire carries one", () => {
    expect(trigTip({ ticker: "IBM", grade: "core" }).lines).toEqual(["insider · IBM", "grade core"]);
    expect(trigTip({ ticker: "IBM", grade: null }).lines).toEqual(["insider · IBM"]);
  });

  it("trigger: the arm linkage shows ONLY when the chip's date differs from the arm (#7)", () => {
    // fired 05-20, armed 06-01 → the chip sits away from the arm, so the linkage is real information
    expect(trigTip({ ticker: "IBM" }, "2026-05-20", "2026-06-01").lines).toContain(
      "→ fed the 2026-06-01 arm",
    );
    // at the arm the line would restate the chip's own x on every trigger — silence instead
    expect(
      trigTip({ ticker: "IBM" }, "2026-06-01", "2026-06-01").lines.some((l) => l.includes("fed the")),
    ).toBe(false);
  });

  // A1 review: in the FALLBACK case (event_date before the first loaded bar → the chip rides at the arm)
  // the linkage line is silent by the rule above, so the true fire date has nowhere else to appear —
  // name it explicitly rather than let a recorded fact vanish from the UI (#6/#9).
  it("trigger: names the true fire date when the chip fell back to the arm (before the loaded window)", () => {
    const lines = trigTip({ ticker: "IBM", event_date: "2026-01-02" }, "2026-06-01", "2026-06-01").lines;
    expect(lines).toContain("fired 2026-01-02 (before the loaded window)");
    expect(lines.some((l) => l.includes("fed the"))).toBe(false); // the chip IS at the arm — still silent
  });

  it("trigger: no fire-date line when the chip sits at its own event_date (it would restate the x)", () => {
    // fired 05-20 and drawn at 05-20 → the linkage carries the arm; the fire date is the chip's own x
    const at = trigTip({ ticker: "IBM", event_date: "2026-05-20" }, "2026-05-20", "2026-06-01").lines;
    expect(at.some((l) => l.startsWith("fired "))).toBe(false);
    expect(at).toContain("→ fed the 2026-06-01 arm");
    // and a null event_date (the pre-A1 wire shape) never invents one
    expect(
      trigTip({ ticker: "IBM", event_date: null }, "2026-06-01", "2026-06-01").lines.some((l) =>
        l.startsWith("fired "),
      ),
    ).toBe(false);
  });

  it("trigger: per-source provenance lines, capped at 2 — the remainder stays VISIBLE as '+N more'", () => {
    const src = (ref: string) => ({ source: "form4", ref, url: null, detail: {} });
    const one = trigTip({ sources: [src("0001-a")] }).lines;
    expect(one).toContain("form4: 0001-a");
    expect(one.some((l) => l.includes("more source"))).toBe(false); // nothing hidden → no "+N"

    const four = trigTip({ sources: [src("a"), src("b"), src("c"), src("d")] }).lines;
    expect(four.filter((l) => l.startsWith("form4: "))).toEqual(["form4: a", "form4: b"]);
    expect(four).toContain("+2 more sources"); // never a silent drop (#9)
    expect(trigTip({ sources: [src("a"), src("b"), src("c")] }).lines).toContain("+1 more source");
  });

  it("trigger: no sources on the wire → no provenance lines at all (never an empty shell)", () => {
    expect(trigTip({ ticker: "IBM", sources: [] }).lines).toEqual(["insider · IBM"]);
  });

  it("lifecycle: armed / de-armed(reason) / exit-by titles", () => {
    const lc = (kind: "armed" | "dearmed" | "exit_by", closeReason?: string) =>
      overlayTooltip({ n: 1, family: "lifecycle", date: "2026-06-01", closeThatDay: 100, kind, closeReason });
    expect(lc("armed").title).toBe("armed");
    expect(lc("dearmed", "aged out").title).toBe("de-armed (aged out)");
    expect(lc("exit_by").title).toBe("exit-by (horizon)");
  });

  it("lifecycle: a de-arm TOKEN is humanized in the title; an unknown reason rides raw (A1)", () => {
    const lc = (closeReason: string) =>
      overlayTooltip({
        n: 1,
        family: "lifecycle",
        date: "2026-06-01",
        closeThatDay: 100,
        kind: "dearmed",
        closeReason,
      });
    expect(lc("conviction_aged_out").title).toBe("de-armed (conviction aged out (past exit-by))");
    expect(lc("arm_until_lapsed").title).toBe("de-armed (entry window lapsed)");
    expect(lc("something_new").title).toBe("de-armed (something_new)"); // additive-safe, never dropped
  });
});

describe("triggerLinks — the clickable subset of a trigger's provenance (A1)", () => {
  const trig = (sources: unknown[]) => ({ label: "l", kind: "insider", sources } as TriggerRefOut);
  const p = (over: Record<string, unknown>) => ({ source: "form4", ref: "acc-1", url: null, detail: {}, ...over });

  it("links the server-resolved url, labeled by its source", () => {
    expect(triggerLinks(trig([p({ url: "https://sec.gov/x-index.htm" })]))).toEqual([
      { label: "form4", url: "https://sec.gov/x-index.htm" },
    ]);
  });

  it("falls back to a ref that is ITSELF an http(s) URL", () => {
    expect(triggerLinks(trig([p({ ref: "http://example.com/f" })]))).toEqual([
      { label: "form4", url: "http://example.com/f" },
    ]);
  });

  it("never fabricates an href from a non-URL ref — an accession/metric key is text, not a link (#6)", () => {
    expect(triggerLinks(trig([p({ ref: "0001628280-25-042718" })]))).toEqual([]);
    expect(triggerLinks(trig([p({ source: "fact_price_eod", ref: "close" })]))).toEqual([]);
    // and a hostile scheme is not an http(s) URL either
    expect(triggerLinks(trig([p({ url: "javascript:alert(1)" })]))).toEqual([]);
  });

  it("shares the tooltip's 2-source cap, so the row's anchors and the card's lines never disagree", () => {
    const many = trig([1, 2, 3].map((i) => p({ url: `https://sec.gov/${i}` })));
    expect(triggerLinks(many).map((l) => l.url)).toEqual(["https://sec.gov/1", "https://sec.gov/2"]);
  });

  it("a trigger with no sources links nothing", () => {
    expect(triggerLinks({ label: "l", kind: "insider" } as TriggerRefOut)).toEqual([]);
  });
});

describe("insiderSetAside — the greyed-class helper (S2c option (a))", () => {
  it("is true ONLY for the set-aside characters; a self-filing is labeled but NOT set aside", () => {
    expect(insiderSetAside(buy({ character: "primary_market" }))).toBe(true);
    expect(insiderSetAside(buy({ character: "implausible" }))).toBe(true);
    // self-filing still counts in the panel's 90d net-flow (the re-base is deferred) — not greyed
    expect(insiderSetAside(buy({ character: "self_filing" }))).toBe(false);
    expect(insiderSetAside(buy({ character: "open_market" }))).toBe(false);
  });

  it("set-aside buys keep their place in the stable 1..N numbering (visible, never dropped — WB #2)", () => {
    const bars = [bar("2026-05-15", 100), bar("2026-06-01", 104), bar("2026-06-30", 112)];
    const withSetAside = buildOverlayEvents(
      ep({ arm_date: "2026-06-01" }),
      [buy({ d: "2026-05-25", character: "primary_market" }), buy({ d: "2026-06-10" })],
      bars,
    );
    // the set-aside buy numbers like any event (#1 here), and the rows after it keep their numbers
    expect(withSetAside.map((e) => [e.n, e.family, e.date])).toEqual([
      [1, "insider", "2026-05-25"], // the set-aside — numbered, not skipped
      [2, "lifecycle", "2026-06-01"],
      [3, "insider", "2026-06-10"],
    ]);
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
