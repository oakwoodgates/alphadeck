import { describe, expect, it } from "vitest";

import type {
  DisplaySignal,
  EpisodeOperatorOut,
  InsiderBuyOut,
  MemberDisplaySignalsOut,
  PriceBar,
  ScoreboardEpisodeOut,
  TriggerRefOut,
} from "../../api/hooks";
import {
  buildOverlayEvents,
  closeOnDate,
  defaultVisibleRange,
  episodeMarkers,
  insiderSetAside,
  legendEntries,
  nearestBarDate,
  overlayTooltip,
  stackChips,
  tapeSignals,
  triggerLinks,
  volumeData,
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

function opDecision(over: Partial<EpisodeOperatorOut> = {}): EpisodeOperatorOut {
  return {
    action: "took",
    decision_id: "d1",
    decision_date: "2026-06-10",
    reason: null,
    thesis_level: false,
    entry_price: 12.34,
    entry_inferred: false,
    exit_price: null,
    exit_inferred: false,
    exit_date: null,
    running: false,
    operator_return: null,
    ...over,
  };
}

// A3: a display signal + the canonical dated cross its backend member emits (the LATEST flip per key).
function dsig(
  kind: string,
  events: { key: string; label: string; date: string; direction?: "up" | "down" }[] = [],
): DisplaySignal {
  return {
    kind,
    label: kind,
    headline: null,
    metrics: [],
    events,
    basis: { source: "fact_price_eod", params: {} },
  } as unknown as DisplaySignal;
}
const GOLDEN = {
  key: "golden_cross",
  label: "golden cross: 50d crossed above 200d",
  date: "2026-06-10",
  direction: "up" as const,
};

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

describe("nearestBarDate — the binary-search chip anchor (Slice B perf)", () => {
  // The behavioral contract is "identical to the linear scan it replaced": the nearest bar by
  // calendar distance, the EARLIER bar on a tie (the old strict-< first-min kept the first bar seen).
  const linearReference = (bars: { d: string }[], iso: string): string | null => {
    if (bars.length === 0) return null;
    let best = bars[0].d;
    let bestDiff = Infinity;
    const t = Date.parse(iso);
    for (const b of bars) {
      const diff = Math.abs(Date.parse(b.d) - t);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = b.d;
      }
    }
    return best;
  };

  it("matches the linear reference on every probe — exact hits, gaps, ties, out-of-range", () => {
    const bars = [
      bar("2026-05-01", 1),
      bar("2026-05-04", 1), // a weekend gap
      bar("2026-05-05", 1),
      bar("2026-06-01", 1), // a long gap (halt / thin history)
      bar("2026-06-02", 1),
      bar("2026-06-30", 1),
    ];
    const probes = [
      "2026-04-01", // before the first bar → clamps to it
      "2026-05-01", // exact hit, first bar
      "2026-05-02", // nearer 05-01 than 05-04
      "2026-05-03", // nearer 05-04
      "2026-05-05", // exact hit, mid
      "2026-05-18", // mid-gap TIE (17d each way) → the earlier bar
      "2026-05-20", // nearer 06-01
      "2026-06-16", // TIE between 06-02 and 06-30 (14d each) → the earlier bar
      "2026-06-30", // exact hit, last bar
      "2026-07-15", // after the last bar → clamps to it
    ];
    for (const p of probes) expect(nearestBarDate(bars, p)).toBe(linearReference(bars, p));
  });

  it("prefers the EARLIER bar on an equidistant tie (the linear scan's behavior, preserved)", () => {
    const bars = [bar("2026-06-01", 1), bar("2026-06-05", 1)];
    expect(nearestBarDate(bars, "2026-06-03")).toBe("2026-06-01"); // 2d either way → earlier wins
  });

  it("clamps outside the range and is null with no bars", () => {
    const bars = [bar("2026-06-01", 1), bar("2026-06-02", 1)];
    expect(nearestBarDate(bars, "2025-01-01")).toBe("2026-06-01");
    expect(nearestBarDate(bars, "2027-01-01")).toBe("2026-06-02");
    expect(nearestBarDate([], "2026-06-01")).toBeNull();
  });

  it("agrees with the reference on a single-bar series (every probe maps to it)", () => {
    const bars = [bar("2026-06-15", 1)];
    for (const p of ["2026-01-01", "2026-06-15", "2026-12-31"])
      expect(nearestBarDate(bars, p)).toBe("2026-06-15");
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

  it("names the operator family — last in the order, only when a decision chip exists (A2)", () => {
    const bars = [bar("2026-05-15", 100), bar("2026-06-01", 104), bar("2026-06-30", 112)];
    const withOp = buildOverlayEvents(ep({ arm_date: "2026-06-01", operator: opDecision() }), [], bars);
    expect(legendEntries(withOp).map((l) => l.family)).toEqual(["lifecycle", "operator"]);
    expect(legendEntries(withOp).at(-1)?.label).toBe("operator");
    const without = buildOverlayEvents(ep({ arm_date: "2026-06-01" }), [], bars);
    expect(legendEntries(without).some((l) => l.family === "operator")).toBe(false);
  });

  it("names 'tape signal' LAST of all — and only when a tape chip actually drew (A3)", () => {
    const bars = [bar("2026-05-15", 100), bar("2026-06-01", 104), bar("2026-06-30", 112)];
    const e = ep({ arm_date: "2026-06-01", operator: opDecision({ decision_date: "2026-06-01" }) });
    const withTape = buildOverlayEvents(e, [], bars, {
      signals: [dsig("sma_position", [GOLDEN])],
      asof: "2026-07-15",
    });
    expect(legendEntries(withTape).map((l) => l.family)).toEqual(["lifecycle", "operator", "signal"]);
    expect(legendEntries(withTape).at(-1)).toMatchObject({ label: "tape signal", cls: "ov-signal" });
    // a signal whose only event was DROPPED (pre-window) names nothing — a legend entry for a family
    // with zero drawn chips is exactly the noise #7 forbids
    const dropped = buildOverlayEvents(e, [], bars, {
      signals: [dsig("sma_position", [{ ...GOLDEN, date: "2026-01-02" }])],
      asof: "2026-07-15",
    });
    expect(legendEntries(dropped).some((l) => l.family === "signal")).toBe(false);
  });
});

// -------- Slice A2: the operator chip family — a recorded decision joins the numbered universe --------
describe("buildOverlayEvents + overlayTooltip — the operator family (A2)", () => {
  const BARS = [
    bar("2026-05-15", 100),
    bar("2026-06-01", 104),
    bar("2026-06-10", 107),
    bar("2026-06-20", 110),
    bar("2026-06-30", 112),
  ];

  it("numbers the decision chronologically among the families, at its decision_date", () => {
    const events = buildOverlayEvents(
      ep({ arm_date: "2026-06-01", dearm_date: "2026-06-20", operator: opDecision() }),
      [buy({ d: "2026-05-15" })],
      BARS,
    );
    expect(events.map((e) => [e.n, e.family, e.date])).toEqual([
      [1, "insider", "2026-05-15"],
      [2, "lifecycle", "2026-06-01"], // armed
      [3, "operator", "2026-06-10"], // the decision, between the arm and the de-arm
      [4, "lifecycle", "2026-06-20"], // dearmed
    ]);
    const op = events[2];
    expect(op.family === "operator" && op.closeThatDay).toBe(107); // the guide-line anchor
  });

  it("a same-day decision sits after the buys and before the de-arm (insertion tiebreak)", () => {
    const events = buildOverlayEvents(
      ep({
        arm_date: "2026-06-01",
        dearm_date: "2026-06-20",
        operator: opDecision({ decision_date: "2026-06-20" }),
      }),
      [],
      BARS,
    );
    expect(events.map((e) => e.family)).toEqual(["lifecycle", "operator", "lifecycle"]);
  });

  it("a decision past the last drawn bar loses its chip (the tape rule — it rides Lens 4 + the row)", () => {
    const events = buildOverlayEvents(
      ep({ arm_date: "2026-06-01", operator: opDecision({ decision_date: "2026-08-01" }) }),
      [],
      BARS,
    );
    expect(events.some((e) => e.family === "operator")).toBe(false);
  });

  const tip = (over: Partial<EpisodeOperatorOut>) =>
    overlayTooltip({
      n: 3,
      family: "operator",
      date: "2026-06-10",
      closeThatDay: 107,
      op: opDecision(over),
    });

  it("took: the fill price, labeled for what it IS — a logged fill vs an inferred close", () => {
    expect(tip({}).title).toBe("operator");
    expect(tip({}).lines).toEqual(["took @ $12.34 (logged fill)"]);
    expect(tip({ entry_inferred: true }).lines[0]).toBe("took @ $12.34 (close, inferred)");
    expect(tip({ entry_price: null }).lines).toEqual(["took"]); // no price logged → no invented one (#6)
  });

  it("took: the operator's own exit and the running/realized return ride as lines", () => {
    const lines = tip({
      exit_price: 14,
      exit_date: "2026-07-01",
      exit_inferred: true,
      operator_return: 0.08,
      running: true,
    }).lines;
    expect(lines).toContain("exited 2026-07-01 @ $14.00 (close, inferred)");
    expect(lines).toContain("running +8.0%");
    // a realized (non-running) return drops the label
    expect(tip({ operator_return: 0.123 }).lines).toContain("+12.3%");
  });

  it("passed: the reason rides verbatim; a thesis-level decision says so", () => {
    expect(tip({ action: "passed" }).lines).toEqual(["passed"]);
    expect(tip({ action: "passed", reason: "too extended" }).lines).toEqual(["passed", "too extended"]);
    expect(tip({ thesis_level: true }).lines).toContain("thesis-level decision");
    expect(tip({}).lines).not.toContain("thesis-level decision"); // only on explicit true
  });
});

// -------- Slice A2: the un-numbered outcome markers (derived points, never chips) ---------------------
describe("episodeMarkers — entry/exit/peak trace to wire fields, gated on the forward bar", () => {
  const BARS = [
    bar("2026-05-15", 100),
    bar("2026-06-01", 104),
    bar("2026-06-10", 107),
    bar("2026-06-20", 110),
  ];
  const MATURED = {
    arm_date: "2026-06-01",
    entry_close: 104,
    exit_close: 110,
    exit_date: "2026-06-20",
    peak_date: "2026-06-10",
    truncated: false,
    insufficient_prices: false,
  };

  it("a matured episode yields entry/peak/exit, ascending, with the right glyph vocabulary", () => {
    expect(episodeMarkers(ep(MATURED), BARS)).toEqual([
      { kind: "entry", time: "2026-06-01", position: "belowBar", shape: "arrowUp", text: "entry" },
      { kind: "peak", time: "2026-06-10", position: "aboveBar", shape: "circle", text: "peak" },
      { kind: "exit", time: "2026-06-20", position: "aboveBar", shape: "arrowDown", text: "exit" },
    ]);
  });

  it("snaps a non-bar date to the latest bar ≤ it (the closeOnDate convention)", () => {
    const m = episodeMarkers(ep({ ...MATURED, arm_date: "2026-06-02", peak_date: "2026-06-14" }), BARS);
    expect(m.find((x) => x.kind === "entry")?.time).toBe("2026-06-01");
    expect(m.find((x) => x.kind === "peak")?.time).toBe("2026-06-10");
  });

  it("gates exit + peak on the degenerate-forward-bar guard — a just-armed episode marks entry only", () => {
    // exit_date === arm_date: the single-bar case (a false round-trip); insufficient_prices: no bar at all
    const single = ep({ ...MATURED, exit_date: "2026-06-01", peak_date: "2026-06-01" });
    expect(episodeMarkers(single, BARS).map((m) => m.kind)).toEqual(["entry"]);
    const noBar = ep({ ...MATURED, insufficient_prices: true });
    expect(episodeMarkers(noBar, BARS).map((m) => m.kind)).toEqual(["entry"]);
  });

  it("an exit on a TRUNCATED episode reads 'last bar' — the measurement edge, not an exit", () => {
    const m = episodeMarkers(ep({ ...MATURED, truncated: true }), BARS);
    expect(m.find((x) => x.kind === "exit")?.text).toBe("last bar");
  });

  it("never invents a point: a missing wire field or a date before the first bar drops that marker (#6)", () => {
    expect(episodeMarkers(ep({ ...MATURED, entry_close: null }), BARS).map((m) => m.kind)).toEqual([
      "peak",
      "exit",
    ]);
    expect(episodeMarkers(ep({ ...MATURED, peak_date: "2026-05-01" }), BARS).map((m) => m.kind)).toEqual(
      ["entry", "exit"], // the peak predates the first loaded bar → no honest x, dropped not clamped
    );
    expect(episodeMarkers(ep(MATURED), [])).toEqual([]); // no bars → nothing to anchor on
  });
});

describe("volumeData — null-volume bars are skipped, never zero-invented (#6)", () => {
  it("maps only the bars that carry a volume", () => {
    expect(
      volumeData([
        { d: "2026-06-01", volume: 1000 },
        { d: "2026-06-02", volume: null }, // a close-only free-EOD bar → a gap, not a 0
        { d: "2026-06-03", volume: 2500 },
      ]),
    ).toEqual([
      { time: "2026-06-01", value: 1000 },
      { time: "2026-06-03", value: 2500 },
    ]);
  });

  it("is empty when no bar carries a volume", () => {
    expect(volumeData([{ d: "2026-06-01", volume: null }])).toEqual([]);
  });
});

// -------- Slice A3: the display-signal ("tape signal") family — computed context, not recorded history --
describe("tapeSignals — only the two dated-event kinds become chips (the allow-list)", () => {
  it("keeps sma_position + relative_strength and drops every other kind", () => {
    const member = {
      security_id: "s1",
      signals: [
        dsig("sma_position"),
        dsig("trailing_returns"),
        dsig("relative_strength"),
        dsig("range_52w"),
        dsig("rvol"),
      ],
    } as unknown as MemberDisplaySignalsOut;
    expect(tapeSignals(member).map((s) => s.kind)).toEqual(["sma_position", "relative_strength"]);
  });

  it("EXCLUDES insider_flow_90d — its buys/sells already ARE the insider family (no double-chipping)", () => {
    const member = {
      signals: [
        dsig("insider_flow_90d", [{ key: "last_buy", label: "last insider buy", date: "2026-06-10" }]),
        dsig("sma_position", [GOLDEN]),
      ],
    } as unknown as MemberDisplaySignalsOut;
    expect(tapeSignals(member).map((s) => s.kind)).toEqual(["sma_position"]);
  });

  it("an UNREGISTERED new display kind stays off the chart until someone opts it in (#7)", () => {
    // the mirror of signalHeadlines' zero-FE-change framework: a headline renders by default, a CHIP
    // does not — a chip is a claim on the price path, so silence is the safe default for a new member
    const member = { signals: [dsig("brand_new", [GOLDEN])] } as unknown as MemberDisplaySignalsOut;
    expect(tapeSignals(member)).toEqual([]);
  });

  it("a null member / a member with no signals → nothing", () => {
    expect(tapeSignals(null)).toEqual([]);
    expect(tapeSignals({ signals: [] } as unknown as MemberDisplaySignalsOut)).toEqual([]);
  });
});

describe("buildOverlayEvents — the tape family joins the numbering (A3)", () => {
  const BARS = [
    bar("2026-05-15", 100),
    bar("2026-06-01", 104),
    bar("2026-06-10", 107),
    bar("2026-06-30", 112),
  ];
  const ASOF = "2026-07-15";
  const build = (signals: DisplaySignal[], over: Partial<ScoreboardEpisodeOut> = {}, buys = [] as InsiderBuyOut[]) =>
    buildOverlayEvents(ep({ arm_date: "2026-06-01", ...over }), buys, BARS, { signals, asof: ASOF });

  it("numbers a tape event chronologically among the recorded families", () => {
    const events = build([dsig("sma_position", [GOLDEN])], {}, [buy({ d: "2026-05-15" })]);
    expect(events.map((e) => [e.n, e.family, e.date])).toEqual([
      [1, "insider", "2026-05-15"],
      [2, "lifecycle", "2026-06-01"], // armed
      [3, "signal", "2026-06-10"], // the golden cross, in its true place on the tape
    ]);
    const s = events[2];
    expect(s.family === "signal" && s.signalKind).toBe("sma_position");
    expect(s.family === "signal" && s.event.label).toBe("golden cross: 50d crossed above 200d");
    expect(s.family === "signal" && s.asof).toBe(ASOF); // the read's as-of rides with the chip
    expect(s.closeThatDay).toBe(107); // the guide-line anchor, like any family
  });

  it("a same-day tape read sorts BEHIND the recorded fact it shares a date with (#7)", () => {
    const onArm = { ...GOLDEN, date: "2026-06-01" };
    const events = build([dsig("sma_position", [onArm])]);
    expect(events.map((e) => e.family)).toEqual(["lifecycle", "signal"]); // armed first, always
  });

  it("DROPS a cross that predates the first loaded bar — never clamped onto a bar that never saw it", () => {
    // the asymmetry vs a trigger (which falls BACK to the arm): a trigger is call EVIDENCE with a real
    // anchor; a tape read has none, and a "latest flip" drawn at the left edge would read as a fresh flip
    const events = build([dsig("sma_position", [{ ...GOLDEN, date: "2026-01-02" }])]);
    expect(events.some((e) => e.family === "signal")).toBe(false);
    expect(events.map((e) => e.date)).toEqual(["2026-06-01"]); // nothing landed on the first bar either
  });

  it("keeps a cross ON the first loaded bar (the boundary is `< first`, not `<=`)", () => {
    const events = build([dsig("sma_position", [{ ...GOLDEN, date: "2026-05-15" }])]);
    expect(events.map((e) => [e.family, e.date])).toEqual([
      ["signal", "2026-05-15"],
      ["lifecycle", "2026-06-01"],
    ]);
  });

  it("drops a tape event past the last drawn bar too (the shared right-edge rule)", () => {
    const events = build([dsig("relative_strength", [{ ...GOLDEN, date: "2026-08-01" }])]);
    expect(events.some((e) => e.family === "signal")).toBe(false);
  });

  it("flattens EVERY qualifying signal's events, in one interleaved numbering", () => {
    const events = build([
      dsig("sma_position", [GOLDEN, { key: "cross_sma50", label: "price crossed above 50d SMA", date: "2026-05-20" }]),
      dsig("relative_strength", [
        { key: "rs_high_spy", label: "RS vs SPY at a 52-week high", date: "2026-06-30", direction: "up" },
      ]),
    ]);
    expect(events.map((e) => [e.n, e.family, e.date])).toEqual([
      [1, "signal", "2026-05-20"],
      [2, "lifecycle", "2026-06-01"],
      [3, "signal", "2026-06-10"],
      [4, "signal", "2026-06-30"],
    ]);
  });

  it("no tape argument at all → the recorded numbering is byte-identical to before A3", () => {
    const args = [ep({ arm_date: "2026-06-01" }), [buy({ d: "2026-05-15" })], BARS] as const;
    expect(buildOverlayEvents(...args).map((e) => [e.n, e.family])).toEqual([
      [1, "insider"],
      [2, "lifecycle"],
    ]);
    // and an EMPTY signal list is the same thing — never an invented chip
    expect(buildOverlayEvents(...args, { signals: [], asof: ASOF })).toEqual(buildOverlayEvents(...args));
  });
});

describe("overlayTooltip — the tape chip's epistemics (A3)", () => {
  const tip = (signalKind: string, label = "golden cross: 50d crossed above 200d") =>
    overlayTooltip({
      n: 3,
      family: "signal",
      date: "2026-06-10",
      closeThatDay: 107,
      signalKind,
      asof: "2026-07-15",
      event: { key: "golden_cross", label, date: "2026-06-10", direction: "up" },
    });

  it("titles with the event's own label and always dates the READ, not the record (#6)", () => {
    const t = tip("sma_position");
    expect(t.title).toBe("golden cross: 50d crossed above 200d");
    // these are re-derived at the drawer's asof on every open — never recorded call history
    expect(t.lines[0]).toBe("display-only tape read · derived as-of 2026-07-15");
  });

  it("names the latest-flip-only limit on sma_position — the missing older cross is CONTRACT, not a bug", () => {
    expect(tip("sma_position").lines).toEqual([
      "display-only tape read · derived as-of 2026-07-15",
      "most recent flip only — earlier crosses not shown",
    ]);
  });

  it("stays silent about flips on relative_strength — an RS-high print is not a flip (it would be a lie)", () => {
    const t = tip("relative_strength", "RS vs SPY at a 52-week high");
    expect(t.title).toBe("RS vs SPY at a 52-week high");
    expect(t.lines).toEqual(["display-only tape read · derived as-of 2026-07-15"]);
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
