import { describe, expect, it } from "vitest";

import {
  ageWords,
  barTitle,
  barWords,
  basisWords,
  companyTitle,
  compactUsd,
  emptyWords,
  filingLine,
  filterCards,
  fmtDay,
  fmtShort,
  lastPrintWords,
  lastYoyWords,
  pctWords,
  plural,
  pointWords,
  quarterWords,
  setAsideWords,
  workLine,
} from "../multiplesView";
import { AAA, BBB, CARDS, DDD, EEE, OLD, UNR, bar, counts, payload } from "./fixtures";

// Every string a person reads on the page that is built from the payload: neutral, no verdict, and the
// arithmetic of the figures (rounding, rollover, signs).

describe("figures", () => {
  it("says a calendar quarter the way people do", () => {
    expect(quarterWords("2026-Q2")).toBe("Q2 2026");
    expect(quarterWords("2023-Q4")).toBe("Q4 2023");
    expect(quarterWords("odd")).toBe("odd");
  });

  it("writes a dollar figure in one compact number, rolling over at a thousand of its unit", () => {
    expect(compactUsd(1_600_000_000)).toBe("$1.6B");
    expect(compactUsd(41_200_000)).toBe("$41.2M");
    expect(compactUsd(845_000)).toBe("$845K");
    expect(compactUsd(845)).toBe("$845");
    expect(compactUsd(0)).toBe("$0");
    expect(compactUsd(999_400)).toBe("$999K");
    expect(compactUsd(999_500)).toBe("$1.0M"); // 999.5 would round to 1000K: the next unit
    expect(compactUsd(999_949)).toBe("$1.0M");
    expect(compactUsd(999_950_000)).toBe("$1.0B");
    expect(compactUsd(-12_300_000)).toBe("-$12.3M");
  });

  it("writes a change with its sign: a whole number from ten percent up, one decimal below", () => {
    expect([34, 3.4, -40, -3.04, 150, 10, 9.96].map(pctWords)).toEqual([
      "+34%",
      "+3.4%",
      "-40%",
      "-3.0%",
      "+150%",
      "+10%",
      "+10.0%",
    ]);
    expect(pctWords(0)).toBe("0.0%");
    expect(pctWords(0.04)).toBe("0.0%");
    expect(pctWords(-0.04)).toBe("0.0%");
  });

  it("dates with and without the year", () => {
    expect(fmtDay("2026-08-07")).toBe("Aug 7, 2026");
    expect(fmtDay(null)).toBe("—");
    expect(fmtShort("2026-08-07", "2026-10-28")).toBe("Aug 7");
    expect(fmtShort("2025-08-07", "2026-10-28")).toBe("Aug 7, 2025");
  });

  it("says how many days before the as-of", () => {
    expect([0, 1, 83, 1346].map(ageWords)).toEqual([
      "on the as-of day",
      "1 day before the as-of",
      "83 days before the as-of",
      "1,346 days before the as-of",
    ]);
    expect(plural(1, "name", "names")).toBe("1 name");
    expect(plural(2, "name", "names")).toBe("2 names");
  });
});

describe("a bar and its filing", () => {
  const b = bar("2026-Q2", 180_000_000, { yoy: 20, prior_period_end: "2025-06-30" });

  it("words the print: quarter, period end, value, basis and the change on the year before", () => {
    expect(barWords(b)).toBe(
      "Q2 2026: period ending Jun 30, 2026 · $180.0M · native · +20% on the same period a year earlier (ending Jun 30, 2025)",
    );
    expect(barWords(bar("2026-Q1", 5_000_000))).toContain("no row a year earlier in the read, so no change");
    expect(barWords(bar("2025-Q4", 5_000_000, { basis: "derived" }))).toContain(
      "derived (the fiscal year minus nine months)",
    );
    expect(basisWords("native")).toBe("native");
    expect(basisWords("basis not stated")).toBe("basis not stated");
  });

  it("dates the print by its filing with the accession, and says when a re-report was first knowable earlier", () => {
    expect(filingLine(b)).toBe("dated by its filing: Aug 9, 2026 · accession 0000876544-26-080901");
    const again = bar("2025-Q2", 1, {
      filed: "2026-08-07",
      first_knowable: "2025-08-08",
      first_knowable_accession: "0000876544-25-080801",
    });
    expect(filingLine(again)).toContain("first knowable Aug 8, 2025 in accession 0000876544-25-080801 (re-reported)");
    expect(filingLine({ ...b, accession: null })).toBe("dated by its filing: Aug 9, 2026");
    expect(barTitle(b)).toContain("accession 0000876544-26-080901");
  });

  it("words the last print and its change on the year before", () => {
    const lp = { quarter: "2026-Q2", period_end: "2026-06-30", filed: "2026-08-07", days_before: 83, yoy: 34 };
    expect(lastPrintWords(lp, "2026-10-28")).toBe("Q2 2026 · filed Aug 7 · 83 days before the as-of");
    expect(lastYoyWords(lp)).toBe("+34% on the year before");
    expect(lastYoyWords({ ...lp, yoy: null })).toBe("no change shown (no row a year earlier)");
    expect(lastPrintWords({ ...lp, filed: "2025-12-30" }, "2026-10-28")).toContain("filed Dec 30, 2025");
  });

  it("words a median point, and says why a quarter has none", () => {
    expect(pointWords({ quarter: "2026-Q2", median: 35, n: 2 }, 3)).toBe(
      "Q2 2026: median +35% (median of 2 names)",
    );
    expect(pointWords({ quarter: "2026-Q2", median: 20, n: 1 }, 1)).toBe("Q2 2026: +20% (1 name)");
    expect(pointWords({ quarter: "2025-Q1", median: null, n: 1 }, 3)).toBe(
      "Q1 2025: not drawn (1 name has a change that quarter; a median needs two)",
    );
    expect(pointWords({ quarter: "2025-Q1", median: null, n: 0 }, 3)).toContain("0 names have a change");
  });
});

describe("a name", () => {
  it("says what an empty frame holds", () => {
    expect(emptyWords(UNR)).toBe("no resolved security");
    expect(emptyWords(EEE)).toBe("no revenue series stored");
    expect(emptyWords(OLD)).toBe("no revenue in these quarters");
    expect(emptyWords(AAA)).toBe("");
  });

  it("names a chart for the accessibility tree: ticker, name, quarters, thin, last print, set-asides", () => {
    expect(companyTitle(AAA, "2026-08-14")).toBe(
      "AAA · AAA Corp · 12 quarters · last print Q2 2026 · filed Aug 9 · 5 days before the as-of",
    );
    const t = companyTitle(BBB, "2026-08-14");
    expect(t).toContain("4 quarters · thin: fewer than five quarters");
    expect(t).toContain("1 period set aside");
    expect(companyTitle(UNR, "2026-08-14")).toBe("UNR · no resolved security");
    expect(companyTitle(OLD, "2026-08-14")).toContain("no revenue in these quarters · last print Q4 2022");
    expect(companyTitle(DDD, "2026-08-14")).toContain("DDD · DDD Corp");
    expect(setAsideWords(1)).toBe("1 period set aside");
    expect(setAsideWords(3)).toBe("3 periods set aside");
  });

  it("filters the picker by any case, keeping the server's order", () => {
    expect(filterCards(CARDS.cards, "").map((c) => c.name)).toEqual(["Copper", "Grid", "Nuclear"]);
    expect(filterCards(CARDS.cards, "  NUC ").map((c) => c.name)).toEqual(["Nuclear"]);
    expect(filterCards(CARDS.cards, "zzz")).toEqual([]);
  });
});

describe("the work line", () => {
  it("states the card and the as-of, the clock, the roster, the series, the thin, the set-asides and the span", () => {
    const line = workLine(payload());
    expect(line).toContain("Grid as of Aug 14, 2026");
    expect(line).toContain("public clock");
    expect(line).toContain("9 roster rows (8 resolved, 1 without a resolved security, 1 in Context or Bench), 7 companies");
    expect(line).toContain("a revenue series for 5 of 7 names (2 without)");
    expect(line).toContain("1 name thin (fewer than 5 quarters)");
    expect(line).toContain("1 name whose series ends before these quarters");
    expect(line).toContain("1 revenue period set aside on this card's names");
    expect(line).toContain("quarters drawn: Q3 2023 to Q2 2026");
    expect(line).toContain("1 Context or Bench name never in a median");
    expect(line).toContain("computed in 4.2 s");
  });

  it("says when nothing is set aside, stale or silent, and what a cached or live read is", () => {
    const line = workLine(
      payload({ counts: counts({ stale: 0, set_aside: 0, silent_companies: 0 }), clock: "record", cache: "hit", axis: [] }),
    );
    expect(line).toContain("no revenue period set aside on this card's names");
    expect(line).toContain("no quarter to draw");
    expect(line).toContain("live read");
    expect(line).toContain("cached");
    expect(line).not.toContain("whose series ends");
    expect(line).not.toContain("never in a median");
  });
});
