import { describe, expect, it } from "vitest";

import { businessTypeLabel } from "../../../util/format";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  NO_TYPE,
  ROLE_ORDER,
  ROLE_TAB_LABEL,
  businessTypeOptions,
  cardNames,
  catalystWords,
  chipWords,
  daysWords,
  filingWords,
  filterRows,
  filtersActive,
  fmtPct,
  fmtPp,
  fmtShortDay,
  fmtSigned,
  inTab,
  nextSort,
  rosterWords,
  sortRows,
  tabCounts,
  tapeCell,
  tone,
  unresolvedWords,
  workLines,
  type Sort,
} from "../tableView";
import { AAA, EDGAR, MIXED, ROWS, XXX, YYY_COUNTER, YYY_PAYER, ZZZ, card, payload, row, tape } from "./fixtures";

// The Read-Throughs' pure helpers: the tabs (a role with no rows is a 0, never missing), the filters, the sorts
// (a blank value is last in either direction), each cell's words and each tape gap's reason, and the work lines.

const tickers = (rows: readonly { ticker: string; role: string }[]) => rows.map((r) => `${r.ticker}:${r.role}`);
const SPY = { ret_7d: 1.5, ret_30d: 4, end: "2026-10-28" };

describe("the tabs", () => {
  it("lists the roles in the Chain Wave's order, each labeled as a group", () => {
    expect(ROLE_ORDER).toEqual(["payer", "counter_case", "direct_read", "participant", "financier", "benchmark"]);
    expect(ROLE_ORDER.map((r) => ROLE_TAB_LABEL[r])).toEqual([
      "Payers",
      "Counter-cases",
      "Direct reads",
      "Participants",
      "Financiers",
      "Benchmarks",
    ]);
  });

  it("counts every role, a role with no row as 0, never missing", () => {
    expect(tabCounts(ROWS)).toEqual({
      all: 5,
      payer: 2,
      counter_case: 1,
      direct_read: 1,
      participant: 0,
      financier: 1,
      benchmark: 0,
    });
    expect(tabCounts([]).all).toBe(0);
    expect(inTab(ROWS, "payer").map((r) => r.key)).toEqual(["xxx-payer", "yyy-payer"]);
    expect(inTab(ROWS, "all")).toHaveLength(5);
    expect(inTab(ROWS, "benchmark")).toEqual([]);
  });
});

describe("the filters", () => {
  it("start open, and report when one is narrower than the default", () => {
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    expect(filtersActive({ ...DEFAULT_FILTERS, card: " " })).toBe(false);
    expect(filtersActive({ ...DEFAULT_FILTERS, card: "gr" })).toBe(true);
    expect(filtersActive({ ...DEFAULT_FILTERS, businessType: NO_TYPE })).toBe(true);
    expect(filtersActive({ ...DEFAULT_FILTERS, withinDays: 30 })).toBe(true);
    expect(filterRows(ROWS, DEFAULT_FILTERS)).toHaveLength(5);
  });

  it("matches a card by a substring of any chip's card name, any case", () => {
    const batteries = filterRows(ROWS, { ...DEFAULT_FILTERS, card: "BATT" });
    expect(tickers(batteries)).toEqual(["XXX:payer", "AAA:financier", "YYY:payer"]);
    // a row on two cards matches either
    expect(filterRows([XXX], { ...DEFAULT_FILTERS, card: "grid" })).toHaveLength(1);
    expect(filterRows(ROWS, { ...DEFAULT_FILTERS, card: "nonesuch" })).toEqual([]);
  });

  it("matches a business type, and the rows with none by their own key", () => {
    expect(tickers(filterRows(ROWS, { ...DEFAULT_FILTERS, businessType: "utilities" }))).toEqual([
      "YYY:counter_case",
      "YYY:payer",
    ]);
    expect(tickers(filterRows(ROWS, { ...DEFAULT_FILTERS, businessType: NO_TYPE }))).toEqual(["ZZZ:direct_read"]);
  });

  it("keeps only the rows with a filing within N days; a row with none never passes a window", () => {
    expect(tickers(filterRows(ROWS, { ...DEFAULT_FILTERS, withinDays: 30 }))).toEqual(["YYY:counter_case", "XXX:payer"]);
    expect(tickers(filterRows(ROWS, { ...DEFAULT_FILTERS, withinDays: 90 }))).toEqual([
      "YYY:counter_case",
      "XXX:payer",
      "ZZZ:direct_read",
    ]);
    // 0 = any, filed or not
    expect(filterRows(ROWS, { ...DEFAULT_FILTERS, withinDays: 0 })).toHaveLength(5);
  });

  it("combines them", () => {
    const got = filterRows(ROWS, { card: "batt", businessType: "utilities", withinDays: 0 });
    expect(got.map((r) => r.key)).toEqual(["yyy-payer"]);
  });

  it("offers the business types the rows hold, A to Z, Unclassified last, with their counts", () => {
    const opts = businessTypeOptions(ROWS);
    expect(opts.map((o) => [o.value, o.rows])).toEqual([
      ["bank", 1],
      ["biotech_pharma", 1],
      ["utilities", 2],
      [NO_TYPE, 1],
    ]);
    expect(opts[opts.length - 1].label).toBe("Unclassified");
    expect(opts.find((o) => o.value === "utilities")?.label).toBe(businessTypeLabel("utilities"));
  });

  it("offers the card names the rows sit on, A to Z, each once", () => {
    expect(cardNames(ROWS)).toEqual(["Batteries", "Grid"]);
  });
});

describe("the sorts", () => {
  it("defaults to the newest last filing first, no filing last (the server's order)", () => {
    expect(DEFAULT_SORT).toEqual({ key: "last_filing", dir: "desc" });
    expect(tickers(sortRows(ROWS, DEFAULT_SORT))).toEqual([
      "YYY:counter_case",
      "XXX:payer",
      "ZZZ:direct_read",
      "AAA:financier", // no filing: A to Z by ticker, a company's roles in the role order
      "YYY:payer",
    ]);
  });

  it("a click flips the same column and starts another in its own first direction", () => {
    expect(nextSort(DEFAULT_SORT, "last_filing")).toEqual({ key: "last_filing", dir: "asc" });
    expect(nextSort({ key: "last_filing", dir: "asc" }, "last_filing")).toEqual({ key: "last_filing", dir: "desc" });
    expect(nextSort(DEFAULT_SORT, "company")).toEqual({ key: "company", dir: "asc" });
    expect(nextSort(DEFAULT_SORT, "next_catalyst")).toEqual({ key: "next_catalyst", dir: "asc" }); // nearest first
    expect(nextSort(DEFAULT_SORT, "vs_spy_7d")).toEqual({ key: "vs_spy_7d", dir: "desc" }); // largest first
  });

  it("sorts every column", () => {
    const by = (key: Sort["key"], dir: Sort["dir"]) => tickers(sortRows(ROWS, { key, dir }));
    expect(by("company", "asc")).toEqual([
      "AAA:financier",
      "XXX:payer",
      "YYY:payer", // ties by the role order
      "YYY:counter_case",
      "ZZZ:direct_read",
    ]);
    expect(by("company", "desc")[0]).toBe("ZZZ:direct_read");
    expect(by("role", "asc")).toEqual([
      "XXX:payer",
      "YYY:payer",
      "YYY:counter_case",
      "ZZZ:direct_read",
      "AAA:financier",
    ]);
    // the first card's name: Batteries (AAA, XXX, YYY payer) before Grid (YYY counter, ZZZ)
    expect(by("cards", "asc").slice(0, 3)).toEqual(["AAA:financier", "XXX:payer", "YYY:payer"]);
    expect(by("business_type", "asc")).toEqual([
      "AAA:financier", // Banks
      "XXX:payer", // biotech
      "YYY:payer",
      "YYY:counter_case", // utilities
      "ZZZ:direct_read", // none: last
    ]);
    expect(by("last_filing", "asc").slice(0, 3)).toEqual(["ZZZ:direct_read", "XXX:payer", "YYY:counter_case"]);
    expect(by("next_catalyst", "asc")[0]).toBe("XXX:payer"); // the only dated one
    expect(by("vs_spy_7d", "desc").slice(0, 1)).toEqual(["XXX:payer"]);
    // the two with a 30-day number lead, largest first (+1.5 then -7.1); the three blanks trail by ticker
    expect(by("vs_spy_30d", "desc")).toEqual([
      "YYY:counter_case",
      "XXX:payer",
      "AAA:financier",
      "YYY:payer",
      "ZZZ:direct_read",
    ]);
    expect(by("vs_spy_30d", "asc").slice(0, 2)).toEqual(["XXX:payer", "YYY:counter_case"]);
  });

  it("puts a blank value last in either direction, never first", () => {
    for (const dir of ["asc", "desc"] as const) {
      const got = sortRows(ROWS, { key: "vs_spy_7d", dir });
      // XXX has the only 7-day number among the unflagged rows that have one; the blanks trail
      expect(got[0].key).toBe("xxx-payer");
      expect(got.slice(1).every((r) => r.tape.vs_spy_7d == null)).toBe(true);
      const fil = sortRows(ROWS, { key: "last_filing", dir });
      expect(fil.slice(-2).every((r) => r.last_filing == null)).toBe(true);
    }
  });

  it("does not change the input", () => {
    const before = ROWS.map((r) => r.key);
    sortRows(ROWS, { key: "company", dir: "desc" });
    expect(ROWS.map((r) => r.key)).toEqual(before);
  });
});

describe("the cell words", () => {
  it("words a card chip with its links, and says No link for none", () => {
    expect(chipWords(card({ links: ["Turbines", "Cables"] }))).toBe("Grid · Turbines, Cables");
    expect(chipWords(card({ links: [null] }))).toBe("Grid · No link");
    expect(chipWords(card({ links: ["Context"], silent: true }))).toBe("Grid · Context");
    expect(chipWords(card({ links: [] }))).toBe("Grid");
  });

  it("words the last filing on one line with the full line (and its distance from the as-of) for the hover", () => {
    const w = filingWords(XXX.last_filing!);
    expect(w.short).toBe("Oct 20 · 8-K · 2.02 results of operations and financial condition");
    expect(w.full).toBe(
      "Oct 20, 2026 (8 days before the as-of) · 8-K · 2.02 results of operations and financial condition",
    );
    expect(filingWords({ ...XXX.last_filing!, days_ago: 0 }).full).toContain("(on the as-of date)");
    expect(filingWords({ ...XXX.last_filing!, days_ago: 1 }).full).toContain("(1 day before the as-of)");
    // a headline that does not open with its form gets the form put in front; an 8-K/A is not said twice either
    expect(filingWords(YYY_COUNTER.last_filing!).short).toBe(
      "Oct 27 · 4 · Jane Roe (CEO): S sale 3,000 sh @ $5.00 · -$15,000",
    );
    expect(filingWords({ ...XXX.last_filing!, form: "8-K/A", headline: "8-K/A · items not yet resolved" }).short).toBe(
      "Oct 20 · 8-K/A · items not yet resolved",
    );
  });

  it("words the next catalyst with its date, days to go, card and label", () => {
    expect(catalystWords(XXX.next_catalyst!).short).toBe("Nov 12 · in 15 days · Q3 earnings · Grid");
    expect(catalystWords(XXX.next_catalyst!).full).toBe(
      "Nov 12, 2026 (Nov 12) · in 15 days · Q3 earnings (earnings) · on Grid",
    );
    expect(daysWords(0)).toBe("on the as-of date");
    expect(daysWords(1)).toBe("in 1 day");
    expect(fmtShortDay(null)).toBe("—");
  });

  it("formats signed numbers, percentages and the sign tint", () => {
    expect(fmtSigned(4.75)).toBe("+4.8");
    expect(fmtSigned(-7.1)).toBe("-7.1");
    expect(fmtSigned(0)).toBe("0.0");
    expect(fmtSigned(null)).toBe("—");
    expect(fmtPct(1.5)).toBe("+1.5%");
    expect(fmtPp(-7.1)).toBe("-7.1 pp");
    expect(fmtPp(undefined)).toBe("—");
    expect([tone(4), tone(-1), tone(0), tone(null)]).toEqual(["pos", "neg", null, null]);
  });
});

describe("the tape cells", () => {
  it("shows the signed difference to one decimal with the working in the hover", () => {
    const c = tapeCell(XXX.tape, "7d", SPY);
    expect(c).toMatchObject({ text: "+4.8", tone: "pos", gray: false });
    expect(c.title).toBe("7 trading days to Oct 28, 2026: the company +6.3% minus SPY +1.5% = +4.8 pp");
    const d = tapeCell(XXX.tape, "30d", SPY);
    expect(d).toMatchObject({ text: "-7.1", tone: "neg" });
    expect(d.title).toContain("30 trading days");
    expect(d.title).toContain("SPY +4.0%");
  });

  it("grays a flagged price history and names the reason; the number is never shown", () => {
    const c = tapeCell(YYY_PAYER.tape, "7d", SPY);
    expect(c).toMatchObject({ text: "flagged", tone: null, gray: true });
    expect(c.title).toContain(MIXED);
    expect(c.title).toContain("Left out");
    expect(tapeCell(YYY_PAYER.tape, "30d", SPY).gray).toBe(true);
  });

  it("is blank, with the reason in the hover, for no tape, no SPY, a different last close and a short window", () => {
    const none = tapeCell(AAA.tape, "7d", SPY);
    expect(none).toMatchObject({ text: "—", gray: false });
    expect(none.title).toContain("No price history");

    const noSpy = tapeCell(tape({ gap: "no_spy", vs_spy_7d: null }), "7d", null);
    expect(noSpy.text).toBe("—");
    expect(noSpy.title).toContain("SPY has no price history");
    expect(noSpy.title).toContain("+6.3%"); // the company's own return is still told

    const differ = tapeCell(ZZZ.tape, "30d", SPY);
    expect(differ.text).toBe("—");
    expect(differ.title).toContain("Oct 23, 2026");
    expect(differ.title).toContain("is not SPY's");
    expect(differ.title).toContain("Oct 28, 2026");

    const short = tapeCell(YYY_COUNTER.tape, "7d", SPY);
    expect(short.text).toBe("—");
    expect(short.title).toContain("Fewer than 8 closes");
    expect(tapeCell(YYY_COUNTER.tape, "30d", SPY).text).toBe("+1.5");
  });
});

describe("the work lines", () => {
  it("say the as-of and the clock, the cards, the roles, the counts, the tape and the cache", () => {
    const lines = workLines(payload(), 5);
    expect(lines[0]).toBe("as of Oct 28, 2026 · as it was public by Oct 28, 2026 · cached");
    expect(lines[1]).toBe("3 cards counted · 1 not yet created · 2 with at least one role · 1 with none");
    expect(lines[2]).toBe("6 roles set · 4 companies · 5 rows (one per company and role)");
    expect(lines[3]).toBe(
      "rows by role: Payers 2 · Counter-cases 1 · Direct reads 1 · Participants 0 · Financiers 1 · Benchmarks 0",
    );
    expect(lines[4]).toBe("3 of 5 rows have a filing-derived fact in the last 365 days");
    expect(lines[5]).toBe("tape: 7 and 30 trading days of closes, the company minus SPY (SPY +1.5% and +4.0%, last close Oct 28, 2026)");
    expect(lines[6]).toBe("1 row has a last close that is not SPY's, so its tape is not compared");
  });

  it("says how many rows are in view when a tab or a filter narrows the table", () => {
    expect(workLines(payload(), 2)[2]).toContain("2 in view");
    expect(workLines(payload(), 5)[2]).not.toContain("in view");
  });

  it("words the live read and a fresh compute", () => {
    const lines = workLines(payload({ clock: "record", cache: "computed", compute_ms: 4200 }), 5);
    expect(lines[0]).toBe("as of Oct 28, 2026 · live read · computed in 4.2 s — first load since the last run or basket edit");
  });

  it("says so when SPY has no tape, and leaves the differ line off when none differ", () => {
    const lines = workLines(payload({ spy: null, tape_ends_differ: 0 }), 5);
    expect(lines.some((l) => l.includes("SPY has no price history at this as-of"))).toBe(true);
    expect(lines.some((l) => l.includes("not SPY's"))).toBe(false);
  });

  it("lists the role rows with no resolved security, and a roster fallback, when there are any", () => {
    expect(unresolvedWords({ unresolved: [], unresolved_rows: 0 })).toBeNull();
    expect(unresolvedWords({ unresolved: ["ZZZ"], unresolved_rows: 1 })).toBe(
      "1 role row has no resolved security, so no filing or tape can be attached and it is not a row: ZZZ.",
    );
    expect(unresolvedWords({ unresolved: ["ZZZ", "QQQ"], unresolved_rows: 3 })).toContain("3 role rows have");
    expect(rosterWords(0)).toBeNull();
    expect(rosterWords(1)).toContain("1 counted card has no roster snapshot");
    expect(rosterWords(2)).toContain("2 counted cards have");
    expect(rosterWords(2)).toContain("Sep 15, 2026");
  });
});

describe("a row's fixtures stay honest", () => {
  it("builds a row with defaults a test can override", () => {
    const r = row("k", { ticker: "ABC", role: "benchmark" });
    expect(r).toMatchObject({ key: "k", ticker: "ABC", role: "benchmark", last_filing: null });
    expect(EDGAR).toContain("sec.gov");
  });
});
