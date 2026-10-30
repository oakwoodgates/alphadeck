import { describe, expect, it } from "vitest";

import {
  LIMITS,
  capTitle,
  capWords,
  compactUsd,
  countFilings,
  daysBefore,
  figureTitle,
  fillWords,
  firedWords,
  fmtFigure,
  foldEntries,
  foldWords,
  keyWords,
  notListedWords,
  placeWords,
  ratifiedWords,
  roleTag,
  sourceLabel,
  sparkPaths,
  stateWords,
  triggerCounts,
  usdHint,
  weightWords,
  windowDays,
  workLines,
} from "../lookupView";
import { DAYS, FIGURES, cardRow, filing, payload, riskTrigger, trigger } from "./fixtures";

// The pure words and folds of the Ticker Lookup: every figure and count arrives computed, these format and fold it.

describe("figures", () => {
  it("formats each unit, a sign on a toned figure, and n/a for a figure the member could not state", () => {
    expect(fmtFigure({ key: "k", unit: "pct", tone: "pos", value: 4.25 })).toBe("+4.3%");
    expect(fmtFigure({ key: "k", unit: "pct", tone: "neg", value: -3.44 })).toBe("-3.4%");
    expect(fmtFigure({ key: "k", unit: "pct", tone: null, value: -8.1 })).toBe("-8.1%");
    expect(fmtFigure({ key: "k", unit: "usd", tone: "pos", value: 55_000 })).toBe("+$55.0K");
    expect(fmtFigure({ key: "k", unit: "usd", tone: null, value: 2_500_000 })).toBe("$2.5M");
    expect(fmtFigure({ key: "k", unit: "price", tone: null, value: 12.5 })).toBe("$12.50");
    expect(fmtFigure({ key: "k", unit: "price", tone: null, value: 0.4321 })).toBe("$0.4321");
    expect(fmtFigure({ key: "k", unit: "count", tone: null, value: 2 })).toBe("2");
    expect(fmtFigure({ key: "rvol", unit: "ratio", tone: null, value: 1.8 })).toBe("1.80×");
    expect(fmtFigure({ key: "rs_spy", unit: "ratio", tone: null, value: 0.1234 })).toBe("0.1234");
    expect(fmtFigure({ key: "k", unit: "pct", tone: null, value: null })).toBe("n/a");
  });

  it("rolls a figure up to the next unit at 1,000 (never $1000.0K)", () => {
    expect(compactUsd(999_949)).toBe("$999.9K");
    expect(compactUsd(999_950)).toBe("$1.0M");
    expect(compactUsd(-49_200)).toBe("-$49.2K");
    expect(compactUsd(350)).toBe("$350");
  });

  it("words the hover with the exact value, the level it is measured from and the member's note", () => {
    expect(figureTitle(FIGURES[5])).toBe("off 52w high · -8.1 · from $13.60 on Sep 2, 2026");
    expect(figureTitle(FIGURES[2])).toBe("vs 200d · n/a: 120/200 bars");
  });
});

describe("the market cap", () => {
  const cap = payload().identity.cap;
  it("is labeled approximate with its tier, or says why it is not stated", () => {
    expect(capWords(cap)).toBe("≈ $125.0M · micro");
    expect(capWords({ ...cap, value: null, tier: null, gap: "no_shares" })).toBe("not stated: awaiting shares");
    expect(capWords({ ...cap, value: null, tier: null, gap: "flagged" })).toBe(
      "not stated: withheld, a data flag reaches its price",
    );
    expect(capWords({ ...cap, value: null, tier: null, gap: "weird" })).toBe("not stated: weird");
  });
  it("shows its inputs on hover", () => {
    expect(capTitle({ ...cap, ads_ratio: 2, split_factor: 0.5, note: "n" })).toBe(
      "Approximate: the latest close times the latest shares fact. · close $12.50 (Oct 28, 2026) · 10,000,000 shares (Sep 1, 2026) · ÷ 2 ordinary per ADS · split scale ×0.5 · n",
    );
  });
});

describe("the cards", () => {
  it("words the weight (display only), the author, the role and the recorded state", () => {
    expect(weightWords([{ weight: 3 }, { weight: 3 }])).toBe("weight 3 of 5");
    expect(weightWords([{ weight: 4 }, { weight: 2 }])).toBe("weight 2 and 4 of 5");
    expect(weightWords([{ weight: null }])).toBeNull();
    expect(roleTag("counter_case")).toBe("counter-case");
    expect(roleTag("payer")).toBe("payer");
    expect(roleTag("odd")).toBe("odd");
    expect(stateWords(cardRow())).toBe("Armed · Oct 27, 2026");
    expect(stateWords(cardRow({ reconstructed: true }))).toBe("Armed · Oct 27, 2026 (reconstructed)");
    expect(stateWords(cardRow({ state: null, recorded_on: null }))).toBe("no recorded call yet");
  });

  it("words the place on the card, and only an armed one is the accent", () => {
    const base = { verdict: null, grade: null, strength: null, live_triggers: 0, turns: [], live_risks: 0 };
    expect(placeWords(cardRow().place)).toEqual({
      text: "armed · CORE entry · core · setup strength 62%",
      armed: true,
    });
    expect(placeWords({ ...base, kind: "watch" })).toEqual({ text: "watch", armed: false });
    expect(placeWords({ ...base, kind: "triggers", live_triggers: 2, turns: ["conviction", "confirmation"] })).toEqual({
      text: "2 live triggers · turns conviction and confirmation",
      armed: false,
    });
    expect(placeWords({ ...base, kind: "triggers", live_triggers: 1, turns: [], live_risks: 1 }).text).toBe(
      "1 live trigger · 1 risk signal",
    );
    expect(placeWords({ ...base, kind: "none" }).text).toBe("none");
    expect(placeWords({ ...base, kind: "not_counted" }).text).toBe("not counted");
    expect(placeWords({ ...base, kind: "no_record" }).text).toBe("no recorded call yet");
    expect(placeWords({ ...base, kind: "armed", verdict: "managing", grade: null }).text).toBe("armed · Managing");
  });
});

describe("the triggers", () => {
  it("words the key, the fire date and how late the record saw it", () => {
    expect(keyWords(trigger())).toBe("conviction");
    expect(keyWords(trigger({ turns: null }))).toBe("no key");
    expect(keyWords(riskTrigger())).toBe("risk");
    expect(firedWords(trigger())).toBe("Oct 23, 2026 · recorded 3 days after");
    expect(firedWords(trigger({ recorded_after_days: 1 }))).toBe("Oct 23, 2026 · recorded 1 day after");
    expect(firedWords(trigger({ recorded_after_days: null }))).toBe("Oct 23, 2026");
    expect(firedWords(trigger({ event_date: null, recorded_after_days: null }))).toBe("undated");
  });

  it("labels a source: EDGAR for a filing link, the bar date for a price reference, else the stored reference", () => {
    expect(sourceLabel({ source: "form4", ref: "x", url: "https://www.sec.gov/Archives/edgar/data/1/2/" })).toBe(
      "form4 · EDGAR",
    );
    expect(sourceLabel({ source: "doe", ref: "x", url: "https://example.com/award" })).toBe("doe");
    expect(sourceLabel({ source: "price", ref: "price:TLA:2026-09-30", url: null })).toBe("price · 2026-09-30");
    expect(sourceLabel({ source: "price", ref: "price:odd", url: null })).toBe("price:odd");
    expect(sourceLabel({ source: "doe", ref: "AWARD-7", url: null })).toBe("doe · AWARD-7");
  });

  it("counts entry, risk and live rows", () => {
    expect(triggerCounts([trigger(), riskTrigger()])).toEqual({ entry: 1, risk: 1, live: 1 });
  });
});

describe("the filings window and fold", () => {
  it("keeps the days from asof minus N through the asof", () => {
    expect(daysBefore("2026-10-28", 90)).toBe("2026-07-30");
    expect(windowDays(DAYS, "2026-10-28", 90).map((d) => d.date)).toEqual(["2026-10-27", "2026-10-20"]);
    expect(windowDays(DAYS, "2026-10-28", 365).map((d) => d.date)).toEqual(["2026-10-27", "2026-10-20", "2026-05-01"]);
    expect(windowDays(DAYS, "2026-10-26", 90).map((d) => d.date)).toEqual(["2026-10-20"]);
    expect(countFilings(windowDays(DAYS, "2026-10-28", 90))).toBe(3);
  });

  it("folds a day's routine rows into one line per fold group, or shows every row", () => {
    const day = DAYS[1];
    const folded = foldEntries(day.rows, false);
    expect(folded).toHaveLength(1);
    expect(folded[0].type).toBe("fold");
    expect(foldWords(folded[0] as Extract<(typeof folded)[number], { type: "fold" }>)).toBe(
      "2 routine Form 4 filings (none holds a purchase: sales, grants, exercises, withholding, other codes)",
    );
    expect(foldEntries(day.rows, true).map((e) => e.type)).toEqual(["row", "row"]);
    // a row that is not routine is never folded, and groups do not mix
    const mixed = [
      filing(),
      filing({ routine: true, fold: "prospectus", form: "424B2", headline: "424B2" }),
      filing({ routine: true, fold: "ownership", form: "144", headline: "144" }),
    ];
    expect(foldEntries(mixed, false).map((e) => (e.type === "row" ? "row" : e.group))).toEqual([
      "row",
      "prospectus",
      "ownership",
    ]);
    const own = foldEntries(mixed, false)[2] as Extract<(typeof folded)[number], { type: "fold" }>;
    expect(foldWords(own)).toBe("1 insider ownership filing (144 notices)");
  });

  it("words the dollar hint and who ratified a fact", () => {
    expect(usdHint({ kind: "form4", usd: 5000 })).toBe("+$5.0K net");
    expect(usdHint({ kind: "form4", usd: -5000 })).toBe("-$5.0K net");
    expect(usdHint({ kind: "dilution", usd: 402_500_000 })).toBe("$402.5M");
    expect(usdHint({ kind: "8k", usd: 1 })).toBeNull();
    expect(ratifiedWords({ ratifier: "operator", ratified_on: "2026-10-16" })).toBe("ratified by operator on Oct 16, 2026");
    expect(ratifiedWords({ ratifier: null, ratified_on: null })).toBeNull();
  });

  it("words the routine rows the cap left out", () => {
    expect(notListedWords({ not_listed: [] })).toBeNull();
    expect(notListedWords({ not_listed: [{ fold: "prospectus", rows: 12, older_than: "2026-08-01" }] })).toBe(
      "12 routine rows not listed (over the per-kind cap): 12 rows of prospectus on or before Aug 1, 2026",
    );
  });
});

describe("the decisions", () => {
  it("words the operator's own fill as logged", () => {
    expect(fillWords({ shares: 100, price: 10.5 })).toBe("100 sh @ $10.50");
    expect(fillWords({ shares: 1500, price: null })).toBe("1,500 sh");
    expect(fillWords({ shares: null, price: null })).toBe("—");
  });
});

describe("the sparkline", () => {
  const spark = { dates: ["a", "b", "c", "d"], stock: [100, 110, null, 105], iwm: [100, 95, 96, 97], bars: 4, iwm_bars: 4, note: null };
  it("draws two paths on one scale, breaks a line at a missing bar and never draws a lone point", () => {
    const p = sparkPaths(spark, 100, 50, 0);
    expect(p.iwm.startsWith("M0.0 ")).toBe(true);
    expect(p.iwm.match(/M/g)).toHaveLength(1);
    // the company has a bar, a bar, a gap, a bar: one segment of two points, and the last point alone draws nothing
    expect(p.stock.match(/M/g)).toHaveLength(1);
    expect(p.stock.match(/L/g)).toHaveLength(1);
    // the highest value (110) sits at the top of the shared scale, the lowest (95) at the bottom
    expect(p.stock).toContain("L33.3 0.0");
    expect(p.iwm).toContain("L33.3 50.0");
  });
  it("is empty with nothing to draw", () => {
    expect(sparkPaths({ ...spark, stock: [null, null, null, null], iwm: [null, null, null, null] }, 100, 50)).toEqual({
      stock: "",
      iwm: "",
    });
    expect(sparkPaths({ ...spark, stock: [100, null, null, null], iwm: [null, null, null, null] }, 100, 50).stock).toBe("");
  });
});

describe("the work lines", () => {
  it("state the as-of and clock, the cards, the triggers, the tape, the filings and the flags", () => {
    const lines = workLines(payload(), 3, 90);
    expect(lines[0]).toBe("as of Oct 28, 2026 · as it was public by Oct 28, 2026 · computed in 0.9 s, first load since the last run or basket edit");
    expect(lines[1]).toBe(
      "4 cards counted (of 5 live; 1 created after the as-of) · on 2 (1 in Context or Bench) · 1 archived · excluded from 1",
    );
    expect(lines[2]).toBe(
      "2 triggers listed since Oct 28, 2025 (1 entry, 1 risk, 1 live) · from 14 recorded nights on 2 cards (1 reconstructed)",
    );
    expect(lines[3]).toBe("9 of 10 display members read · 1 figure with no value");
    expect(lines[4]).toBe("3 filings in the last 90 days · 4 in the 365-day read · 1 catalyst fact under Catalysts");
    expect(lines[5]).toBe("no data flag reaches its price readings");
    const live = workLines(payload({ clock: "record", cache: "hit" }), 0, 365);
    expect(live[0]).toBe("as of Oct 28, 2026 · live read · cached");
  });

  it("names a data flag, a roster fallback and a trigger first recorded before the window", () => {
    const base = payload();
    const lines = workLines(
      payload({
        identity: { ...base.identity, flag: "mixed-scale price history" },
        cards: { ...base.cards, roster_fallback: 2 },
        triggers: { ...base.triggers, before_window_live: 3 },
      }),
      3,
      90,
    );
    expect(lines[1]).toContain("2 with today's roster standing in");
    expect(lines[2]).toContain("3 live triggers first recorded before the window not listed");
    expect(lines[5]).toBe("data flag on its price readings: mixed-scale price history");
  });

  it("states the page's limits", () => {
    expect(LIMITS.length).toBeGreaterThanOrEqual(4);
    expect(LIMITS.join(" ")).toMatch(/One company at a time/);
    expect(LIMITS.join(" ")).toMatch(/approximate/);
  });
});
