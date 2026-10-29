import { describe, expect, it } from "vitest";

import {
  COLUMNS,
  DEFAULT_FILTERS,
  GAP_SHORT,
  GAP_WORDS,
  capCell,
  capTitle,
  chipLinks,
  chipWords,
  companyMatches,
  controlsFor,
  coverageLines,
  coverageTitle,
  coverageWords,
  filtersActive,
  firstKey,
  fmtShares,
  fmtUsd,
  groupFigures,
  groupLabel,
  insiderCell,
  nextSort,
  optionsFor,
  resolveScope,
  revenueCell,
  rosterWords,
  runwayCell,
  scopeWords,
  sideCell,
  sortCompanies,
  sortValue,
  spyCell,
  tagWords,
  unresolvedSummary,
  workLines,
  yoyCell,
  type Filters,
} from "../tableView";
import { contextRoleTag } from "../../../workbench/format";
import {
  AAA,
  BBB,
  CCC,
  COMPANIES,
  COVERAGE,
  DDD,
  GROUP,
  MIXED,
  cap,
  capBlank,
  company,
  insider,
  num,
  payload,
  runway,
  scopesPayload,
  spy,
} from "./fixtures";

// The Peer Table's pure view helpers: the scope pickers, the sort (a blank is last in either direction, never
// zero), the filters (and the controls that cannot discriminate), the cells' words, the group rows (one priced name
// is a value with "1 name", never a median), the coverage and the work lines.

const tickers = (rows: readonly { ticker: string }[]) => rows.map((r) => r.ticker);

describe("the scope pickers", () => {
  const scopes = scopesPayload();

  it("lists each kind's picks in the server's order with the companies on each", () => {
    expect(optionsFor("business_type", scopes).map((o) => [o.key, o.label, o.companies])).toEqual([
      ["miner", "miner", 4],
      ["bank", "bank", 1],
      ["unclassified", "Unclassified", 1],
    ]);
    expect(optionsFor("supersector", scopes).map((o) => o.label)).toEqual(["Materials", "Financials", "Unclassified"]);
    expect(optionsFor("card", scopes).map((o) => [o.key, o.label, o.hint])).toEqual([
      ["t-batt", "Batteries", "1 link today"],
      ["t-grid", "Grid", "2 links today"],
    ]);
    expect(optionsFor("all", scopes)).toEqual([]);
  });

  it("opens a business type on the server's default leaf and the others on their first pick", () => {
    expect(firstKey("business_type", scopes)).toBe("miner");
    expect(firstKey("business_type", scopesPayload({ default_scope: null, default_key: null }))).toBe("miner");
    expect(firstKey("supersector", scopes)).toBe("materials");
    expect(firstKey("card", scopes)).toBe("t-batt");
    expect(firstKey("all", scopes)).toBeNull();
  });

  it("keeps the operator's pick while it is still pickable and falls back to the default when it is not", () => {
    expect(resolveScope({ kind: "card", key: "t-grid" }, scopes)).toEqual({ kind: "card", key: "t-grid" });
    expect(resolveScope({ kind: "all", key: null }, scopes)).toEqual({ kind: "all", key: null });
    expect(resolveScope({ kind: "business_type", key: "bank" }, scopes)).toEqual({ kind: "business_type", key: "bank" });
    // a leaf that is not on the rosters at this as-of
    expect(resolveScope({ kind: "business_type", key: "utilities" }, scopes)).toEqual({
      kind: "business_type",
      key: "miner",
    });
    expect(resolveScope({ kind: "card", key: "gone" }, scopes)).toEqual({ kind: "business_type", key: "miner" });
    expect(resolveScope(null, scopes)).toEqual({ kind: "business_type", key: "miner" });
  });

  it("reads everything when there is no default leaf but there are companies, and nothing when there are none", () => {
    const noDefault = scopesPayload({ default_scope: null, default_key: null, business_types: [] });
    expect(resolveScope(null, noDefault)).toEqual({ kind: "all", key: null });
    expect(resolveScope(null, scopesPayload({ default_scope: null, default_key: null, companies: 0 }))).toBeNull();
  });

  it("words the scope", () => {
    expect(scopeWords({ scope: "business_type", key: "biotech_pharma", scope_name: null })).toBe(
      "Business type: biotech/pharma",
    );
    expect(scopeWords({ scope: "business_type", key: "unclassified", scope_name: null })).toBe(
      "Business type: Unclassified",
    );
    expect(scopeWords({ scope: "supersector", key: "energy_utilities", scope_name: null })).toBe(
      "Supersector: Energy & Utilities",
    );
    expect(scopeWords({ scope: "card", key: "t-grid", scope_name: "Grid" })).toBe("Card: Grid");
    expect(scopeWords({ scope: "all", key: null, scope_name: null })).toBe("All live cards");
  });
});

describe("the sort", () => {
  it("starts a column in its own first direction and flips it on a second click", () => {
    expect(nextSort(null, "company")).toEqual({ key: "company", dir: "asc" });
    expect(nextSort(null, "cap")).toEqual({ key: "cap", dir: "desc" });
    expect(nextSort(null, "runway")).toEqual({ key: "runway", dir: "asc" });
    expect(nextSort({ key: "cap", dir: "desc" }, "cap")).toEqual({ key: "cap", dir: "asc" });
    expect(nextSort({ key: "cap", dir: "asc" }, "revenue")).toEqual({ key: "revenue", dir: "desc" });
  });

  it("keeps the order given when there is no sort (the group order, names A to Z)", () => {
    expect(tickers(sortCompanies(COMPANIES, null, "business_type"))).toEqual(["AAA", "BBB", "CCC", "DDD"]);
  });

  it("sorts a number column and puts a blank last in either direction, never read as zero", () => {
    // caps: AAA $250M, BBB $1B; CCC and DDD have none
    expect(tickers(sortCompanies(COMPANIES, { key: "cap", dir: "desc" }, "business_type"))).toEqual([
      "BBB",
      "AAA",
      "CCC",
      "DDD",
    ]);
    expect(tickers(sortCompanies(COMPANIES, { key: "cap", dir: "asc" }, "business_type"))).toEqual([
      "AAA",
      "BBB",
      "CCC",
      "DDD",
    ]);
    // the stated zero sorts as a number: a stated -10.0 YoY is above a blank, below a stated +20.0
    const asc = sortCompanies(COMPANIES, { key: "yoy", dir: "asc" }, "business_type");
    expect(tickers(asc)).toEqual(["CCC", "AAA", "DDD", "BBB"]); // -10, +20, +50, then the blank (BBB: no prior year)
    const desc = sortCompanies(COMPANIES, { key: "yoy", dir: "desc" }, "business_type");
    expect(tickers(desc)).toEqual(["DDD", "AAA", "CCC", "BBB"]);
  });

  it("sorts a flagged price cell as a blank, last in both directions", () => {
    for (const dir of ["asc", "desc"] as const) {
      const got = sortCompanies(COMPANIES, { key: "off_high", dir }, "business_type");
      expect(tickers(got)).toEqual(["AAA", "BBB", "CCC", "DDD"]); // the one stated value, then the blanks by ticker
    }
    expect(sortValue(DDD, "sma50", "business_type")).toBeNull();
    expect(sortValue(BBB, "sma200", "business_type")).toBeNull(); // short history: blank, not zero
  });

  it("sorts a cash-positive runway as the longest, a blank last", () => {
    const asc = tickers(sortCompanies(COMPANIES, { key: "runway", dir: "asc" }, "business_type"));
    expect(asc).toEqual(["DDD", "AAA", "BBB", "CCC"]); // 6.0, 12.0, cash-positive, then the blank
    const desc = tickers(sortCompanies(COMPANIES, { key: "runway", dir: "desc" }, "business_type"));
    expect(desc).toEqual(["BBB", "AAA", "DDD", "CCC"]);
    expect(sortValue(BBB, "runway", "business_type")).toBe(Number.POSITIVE_INFINITY);
  });

  it("sorts insider buys by dollars, a stated zero below any buy and above a blank", () => {
    const desc = tickers(sortCompanies(COMPANIES, { key: "insider", dir: "desc" }, "business_type"));
    expect(desc).toEqual(["AAA", "BBB", "CCC", "DDD"]); // $1.25M, $0 (stated), then the two with no Form 4 by ticker
    const asc = tickers(sortCompanies(COMPANIES, { key: "insider", dir: "asc" }, "business_type"));
    expect(asc).toEqual(["BBB", "AAA", "CCC", "DDD"]);
  });

  it("sorts text columns A to Z case-insensitively with ties by ticker, and the cards by count (links in a card scope)", () => {
    expect(tickers(sortCompanies(COMPANIES, { key: "company", dir: "desc" }, "business_type"))[0]).toBe("DDD");
    // BBB sits on two cards; the others on one: the most cards first
    expect(tickers(sortCompanies(COMPANIES, { key: "cards", dir: "desc" }, "business_type"))[0]).toBe("BBB");
    expect(sortValue(AAA, "cards", "business_type")).toBe(1);
    expect(sortValue(AAA, "cards", "card")).toBe("turbines");
    expect(sortValue(CCC, "business_type", "business_type")).toBe("miner");
    expect(sortValue(company("x", { business_type: null }), "business_type", "all")).toBeNull();
  });

  it("is stable and repeatable: ties fall to the ticker, then the key", () => {
    const twins = [company("b", { ticker: "SAME" }), company("a", { ticker: "SAME" })];
    expect(sortCompanies(twins, { key: "cap", dir: "desc" }, "all").map((c) => c.key)).toEqual(["a", "b"]);
  });
});

describe("the filters", () => {
  const f = (over: Partial<Filters>): Filters => ({ ...DEFAULT_FILTERS, ...over });
  const pass = (over: Partial<Filters>) => COMPANIES.filter((c) => companyMatches(c, f(over))).map((c) => c.ticker);

  it("passes everything by default and knows when one is active", () => {
    expect(pass({})).toEqual(["AAA", "BBB", "CCC", "DDD"]);
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    expect(filtersActive(f({ tiers: ["micro"] }))).toBe(true);
    expect(filtersActive(f({ query: "  " }))).toBe(false);
    expect(filtersActive(f({ query: "a" }))).toBe(true);
  });

  it("filters by cap tier, several at once, with the companies that have no cap findable", () => {
    expect(pass({ tiers: ["micro"] })).toEqual(["AAA"]);
    expect(pass({ tiers: ["micro", "small"] })).toEqual(["AAA", "BBB"]);
    expect(pass({ tiers: ["none"] })).toEqual(["CCC", "DDD"]);
    expect(pass({ tiers: ["large"] })).toEqual([]);
  });

  it("leaves out the silent-only names, the names without revenue and the names without a Workbench fact", () => {
    expect(pass({ countingOnly: true })).toEqual(["AAA", "BBB", "CCC"]);
    const noRev = company("zzz", { workbench_facts: true });
    expect(companyMatches(noRev, f({ withRevenue: true }))).toBe(false);
    expect(pass({ withRevenue: true })).toEqual(["AAA", "BBB", "CCC", "DDD"]);
    expect(pass({ withFact: true })).toEqual(["AAA", "BBB"]);
  });

  it("finds a ticker or a name, case-insensitively", () => {
    expect(pass({ query: "bol" })).toEqual(["BBB"]);
    expect(pass({ query: "CC" })).toEqual(["CCC"]);
    expect(pass({ query: "co" })).toEqual(["CCC"]); // "Cobalt Co"
    expect(pass({ query: "nonesuch" })).toEqual([]);
  });

  it("renders only the controls that can tell the companies apart", () => {
    const c = controlsFor(COMPANIES);
    expect(c.tiers).toEqual(["micro", "small", "none"]);
    expect(c).toMatchObject({ countingOnly: true, withRevenue: false, withFact: true, query: true });
    const one = controlsFor([AAA]);
    expect(one).toMatchObject({ tiers: ["micro"], countingOnly: false, withRevenue: false, withFact: false, query: false });
    const mixed = controlsFor([AAA, company("none", {})]);
    expect(mixed.withRevenue).toBe(true); // one states revenue and one does not
  });
});

describe("the cells' words", () => {
  it("shows the cap as its tier and the figure, the product on hover", () => {
    const c = capCell(AAA.cap);
    expect(c.text).toBe("micro · $250M");
    expect(c.title).toBe("approximate: latest close $25.00 (Oct 28, 2026) × 10.0M shares on file (as of May 10, 2026) = $250M");
    expect(capCell(BBB.cap).text).toBe("small · $1.0B");
  });

  it("puts the ADS ratio and the split scaling in the cap's hover when they were applied", () => {
    const t = capTitle(cap(200_000_000, "micro", { ads_ratio: 5, split_factor: 0.0625, shares: 160_000_000 }));
    expect(t).toContain("÷ 5 ordinary shares per ADS");
    expect(t).toContain("× 0.0625 to put the count on the close's split scale");
  });

  it("says why a cap is blank, in the cell: awaiting shares, awaiting price, ADS ratio unread, flagged", () => {
    expect(capCell(capBlank("no_shares")).text).toBe("awaiting shares");
    expect(capCell(capBlank("no_price")).text).toBe("awaiting price");
    expect(capCell(capBlank("ads_unread")).text).toBe("ADS ratio unread");
    const flagged = capCell(capBlank("flagged", MIXED));
    expect(flagged.text).toBe("flagged");
    expect(flagged.gray).toBe(true);
    expect(flagged.title).toContain(MIXED);
    expect(capCell(capBlank("no_shares")).title).toBe(`Blank: ${GAP_WORDS.no_shares}.`);
  });

  it("shows revenue, YoY with its prior, runway in months or cash-positive", () => {
    expect(revenueCell(AAA.revenue).text).toBe("$120.0M");
    expect(revenueCell(AAA.revenue).title).toContain("Jun 30, 2026");
    expect(revenueCell(CCC.revenue).text).toBe("$90.0M");
    expect(revenueCell(company("x").revenue).text).toBe("—");
    const y = yoyCell(AAA.yoy);
    expect([y.text, y.tone]).toEqual(["+20.0%", "pos"]);
    expect(y.title).toContain("$100.0M for the period ended Jun 30, 2025");
    expect([yoyCell(CCC.yoy).text, yoyCell(CCC.yoy).tone]).toEqual(["-10.0%", "neg"]);
    expect(yoyCell(BBB.yoy).text).toBe("—");
    expect(yoyCell(BBB.yoy).title).toContain("no row for the same period a year earlier");
    expect(runwayCell(AAA.runway).text).toBe("12.0 mo");
    expect(runwayCell(AAA.runway).title).toContain("cash $12.0M, burn $3.0M per quarter");
    expect(runwayCell(BBB.runway).text).toBe("cash-positive");
    expect(runwayCell(CCC.runway).text).toBe("—");
  });

  it("shows the 50d and 200d as marks with the percent on hover and a blank with its reason", () => {
    expect([sideCell(AAA.sma50, 50).text, sideCell(AAA.sma50, 50).tone]).toEqual(["▲", "pos"]);
    expect(sideCell(AAA.sma50, 50).title).toBe("+5.2% above the 50-day average");
    expect([sideCell(BBB.sma50, 50).text, sideCell(BBB.sma50, 50).tone]).toEqual(["▼", "neg"]);
    expect(sideCell(num(0), 200)).toMatchObject({ text: "●", tone: null, title: "0.0% at the 200-day average" });
    const short = sideCell(BBB.sma200, 200);
    expect(short.text).toBe("—");
    expect(short.title).toContain("n/a: 120/200 bars");
    expect(sideCell(DDD.sma50, 50).gray).toBe(true);
  });

  it("shows vs SPY in percentage points with both returns on hover, and each gap in words", () => {
    const c = spyCell(AAA.vs_spy);
    expect([c.text, c.tone]).toEqual(["+6.0", "pos"]);
    expect(c.title).toBe("30 trading days to Oct 28, 2026: the company +10.0% minus SPY +4.0% = +6.0 pp");
    const own = spyCell(spy(null, { gap: "ends_differ", ret_pct: 2, last_close: "2026-10-23" }));
    expect(own.text).toBe("—");
    expect(own.title).toContain(GAP_WORDS.ends_differ);
    expect(own.title).toContain("Its own 30-trading-day return is +2.0%");
    expect(spyCell(DDD.vs_spy)).toMatchObject({ text: "flagged", gray: true });
  });

  it("shows insider buys as a count and dollars, a stated zero quietly, and a blank when no Form 4 is ingested", () => {
    const c = insiderCell(AAA.insider);
    expect(c.text).toBe("3 · $1.3M");
    expect(c.title).toContain("3 open-market buys in the 90-day window, 2 buyers");
    expect(insiderCell(insider({ buys: 1, usd: 50_000, buyers: 1, gap: null })).title).toContain("1 open-market buy ");
    const zero = insiderCell(BBB.insider);
    expect([zero.text, zero.gray]).toEqual(["0", true]);
    expect(zero.title).toContain("none was an open-market buy");
    const none = insiderCell(CCC.insider);
    expect(none.text).toBe("—");
    expect(none.title).toBe(`Blank: ${GAP_WORDS.no_form4}.`);
  });

  it("formats dollars and share counts compactly", () => {
    expect(fmtUsd(13_500_000)).toBe("$13.5M");
    expect(fmtUsd(1_200_000_000)).toBe("$1.20B");
    expect(fmtUsd(4_100_000_000_000)).toBe("$4.10T");
    expect(fmtUsd(412_000)).toBe("$412K");
    expect(fmtUsd(950)).toBe("$950");
    expect(fmtUsd(-3_000_000)).toBe("-$3.0M");
    expect(fmtUsd(null)).toBe("—");
    expect(fmtShares(10_000_000)).toBe("10.0M");
    expect(fmtShares(1_500_000_000)).toBe("1.50B");
    expect(fmtShares(850)).toBe("850");
  });

  it("has a short and a long word for every gap the server can send", () => {
    const gaps = Object.keys(GAP_WORDS);
    expect(Object.keys(GAP_SHORT).sort()).toEqual(gaps.sort());
    for (const g of gaps) expect(GAP_WORDS[g as keyof typeof GAP_WORDS].length).toBeGreaterThan(10);
  });
});

describe("the company cell", () => {
  it("words a chip and its links", () => {
    expect(chipWords(BBB.cards[1])).toBe("Grid · Turbines, Cables");
    expect(chipLinks({ links: [null, "Cables"] })).toBe("No link, Cables");
    expect(chipWords({ ...AAA.cards[0], links: [] })).toBe("Grid");
  });

  it("tags a silent-only company, else its roles as lowercase words, else nothing", () => {
    expect(tagWords(DDD, contextRoleTag)).toBe("silent on 1 card");
    expect(tagWords({ ...DDD, cards: [DDD.cards[0], DDD.cards[0]] }, contextRoleTag)).toBe("silent on 2 cards");
    expect(tagWords(BBB, contextRoleTag)).toBe("payer");
    expect(tagWords({ ...BBB, roles: ["payer", "counter_case"] }, contextRoleTag)).toBe("payer, counter-case");
    expect(tagWords(AAA, contextRoleTag)).toBe("");
  });
});

describe("the group rows", () => {
  it("words a group", () => {
    expect(groupLabel({ kind: "business_type", label: "biotech_pharma" })).toBe("biotech/pharma");
    expect(groupLabel({ kind: "business_type", label: "unclassified" })).toBe("Unclassified");
    expect(groupLabel({ kind: "supersector", label: "energy_utilities" })).toBe("Energy & Utilities");
    expect(groupLabel({ kind: "link", label: "Turbines" })).toBe("Turbines");
    expect(groupLabel({ kind: "no_link", label: "No link" })).toBe("No link");
  });

  it("carries each figure under its column with what it rests on", () => {
    const figs = groupFigures(GROUP.stats);
    expect(figs.cap).toEqual({
      text: "$625M",
      sub: "median of 2 priced",
      title: "Median approximate market cap of 2 names (priced), equal weight",
    });
    expect(figs.sma50).toMatchObject({ text: "2 of 3", sub: "priced" });
    expect(figs.sma50?.title).toBe("2 names are above the 50-day average, of the 3 names with a 50-day reading");
    expect(figs.vs_spy).toMatchObject({ text: "+1.0", sub: "median of 3 priced" });
    expect(figs.yoy).toMatchObject({ text: "+20.0%", sub: "median of 3 with a prior year" });
    expect(figs.runway).toMatchObject({ text: "3", sub: "with a figure" });
  });

  it("shows one priced name's value with '1 name', never a median label", () => {
    const one = groupFigures({
      ...GROUP.stats,
      cap: { value: 250_000_000, n: 1 },
      yoy: { value: 20, n: 1 },
      vs_spy: { value: 6, n: 1 },
    });
    expect(one.cap).toMatchObject({ text: "$250M", sub: "1 name" });
    expect(one.cap?.title).toBe("One name (priced): its approximate market cap, not a median");
    expect(one.yoy?.sub).toBe("1 name");
    expect(one.vs_spy?.sub).toBe("1 name");
    expect(JSON.stringify(one)).not.toContain("median of 1");
  });

  it("says nothing for a figure with no name behind it, never a zero", () => {
    const none = groupFigures({
      names: 2,
      cap: { value: null, n: 0 },
      above_50d: 0,
      priced_50d: 0,
      vs_spy: { value: null, n: 0 },
      yoy: { value: null, n: 0 },
      runway: 0,
    });
    expect(none).toEqual({});
  });
});

describe("the coverage", () => {
  it("words a column's coverage and its blanks by reason", () => {
    const cap = COVERAGE[0];
    expect(coverageWords(cap)).toBe("2 of 4");
    expect(coverageTitle(cap)).toBe("2 of 4 names state this. Blank: 1 flagged, 1 awaiting shares.");
    expect(coverageTitle(COVERAGE[1])).toBe("4 of 4 names state this.");
  });

  it("lists every column for the show-the-work block", () => {
    const lines = coverageLines(COVERAGE);
    expect(lines).toHaveLength(COVERAGE.length);
    expect(lines[0]).toBe("Cap: 2 of 4 — blank: 1 flagged, 1 awaiting shares");
    expect(lines[1]).toBe("Revenue: 4 of 4");
    expect(lines.find((l) => l.startsWith("50d"))).toBe("50d: 3 of 4 — blank: 1 flagged");
    // every coverage column has a header column to carry it
    for (const c of COVERAGE) expect(COLUMNS.some((d) => d.coverage === c.column)).toBe(true);
  });
});

describe("the work lines", () => {
  it("says what the table rests on", () => {
    const lines = workLines(payload(), 4, false);
    expect(lines[0]).toBe("as of Oct 28, 2026 · as it was public by Oct 28, 2026 · cached");
    expect(lines[1]).toBe("Business type: miner · 2 live cards counted · 1 not yet created · 2 cards hold a name in scope");
    expect(lines[2]).toBe("4 companies · 6 roster rows · 1 silent only (Context or Bench)");
    expect(lines.at(-1)).toMatch(/^30D vs SPY: 30 trading days of closes, the company minus SPY \(SPY \+4\.0%, last close Oct 28, 2026\)/);
  });

  it("counts what is in view, says the toggle changed the figures, and a live read and a fresh compute", () => {
    const lines = workLines(
      payload({ clock: "record", cache: "computed", compute_ms: 61_400, scope: "all", key: null }),
      2,
      true,
    );
    expect(lines[0]).toBe("as of Oct 28, 2026 · live read · computed in 61 s — first load since the last run or basket edit");
    expect(lines[1]).toBe("All live cards · 2 live cards counted · 1 not yet created · 2 cards hold a name in scope");
    expect(lines[2]).toContain("2 in view");
    expect(lines.some((l) => l.includes("count the names that count"))).toBe(true);
    expect(workLines(payload({ cache: "computed", compute_ms: 1500 }), 4, false)[0]).toContain("computed in 1.5 s");
  });

  it("says so when SPY has no history", () => {
    expect(workLines(payload({ spy: null }), 4, false).at(-1)).toBe(
      "30D vs SPY: SPY has no price history at this as-of, so no name can be compared",
    );
  });

  it("words the unresolved rows and the roster fallback, only when there are any", () => {
    expect(unresolvedSummary({ unresolved_rows: 0 })).toBeNull();
    expect(unresolvedSummary({ unresolved_rows: 1 })).toBe(
      "1 roster row has no resolved security, so no reading can be attached and it is not a row",
    );
    expect(unresolvedSummary({ unresolved_rows: 2 })).toContain("2 roster rows have no resolved security");
    expect(rosterWords(0)).toBeNull();
    expect(rosterWords(2)).toContain("2 counted cards have no roster snapshot by this as-of");
    expect(rosterWords(1)).toContain("stands in for it");
  });
});
