import { describe, expect, it } from "vitest";

import {
  AUTHORITY_LABEL,
  AUTHORITY_MEANS,
  AUTHORITY_ORDER,
  AUTHORITY_TAG,
  DEFAULT_FILTERS,
  KIND_LABEL,
  KIND_ORDER,
  byWords,
  countOf,
  countRows,
  filterCards,
  filterDays,
  filterStanding,
  filtersActive,
  fmtDay,
  fmtDayHeader,
  nightWords,
  rowMatches,
  traceWords,
  unresolvedWords,
  windowStart,
  workLines,
  type Filters,
} from "../ledgerView";
import type { EvidenceLedgerOut, EvidenceLedgerRowOut, EvidenceLedgerTalliesOut } from "../useEvidenceLedger";

// The Evidence Ledger's pure helpers: the classes' and kinds' words (a class is a label of WHO, never a
// judgment), every client-side filter (class, kind, company, EDGAR link, date window, "Counting names only"),
// the operator row's byline, the trace sentence and the show-the-work lines.

const ASOF = "2026-10-28";

const row = (over: Partial<EvidenceLedgerRowOut> = {}): EvidenceLedgerRowOut => ({
  key: "r",
  authority: "system",
  kind: "trigger",
  label: "3 insiders bought",
  date: "2026-10-20",
  security_id: "s-OKLO",
  ticker: "OKLO",
  form: "insider",
  silent: null,
  role: null,
  by: null,
  on: null,
  sources: [],
  detail: [],
  edgar: false,
  reconstructed: false,
  ...over,
});

const filters = (over: Partial<Filters> = {}): Filters => ({ ...DEFAULT_FILTERS, ...over });

describe("the words", () => {
  it("name every class and kind the server can send, in the server's order", () => {
    expect(AUTHORITY_ORDER).toEqual(["operator", "system", "automatic", "card"]);
    expect(AUTHORITY_ORDER.map((a) => AUTHORITY_TAG[a])).toEqual(["your", "computed", "stored", "card"]);
    expect(AUTHORITY_ORDER.map((a) => AUTHORITY_LABEL[a])).toEqual(["Your", "Computed", "Stored", "Card"]);
    for (const a of AUTHORITY_ORDER) expect(AUTHORITY_MEANS[a].length).toBeGreaterThan(10);
    expect(KIND_ORDER).toHaveLength(8);
    for (const k of KIND_ORDER) expect(KIND_LABEL[k]).toBeTruthy();
  });

  it("word a class as who put the row there, never as a verdict on it", () => {
    const words = [...AUTHORITY_ORDER.map((a) => AUTHORITY_MEANS[a]), ...KIND_ORDER.map((k) => KIND_LABEL[k])].join(" ");
    expect(words).not.toMatch(/\b(verified|trusted|reliable|weak|strong|good|bad|best|should|must)\b/i);
  });

  it("formats days with their year and the day header with its weekday", () => {
    expect(fmtDay("2026-10-28")).toBe("Oct 28, 2026");
    expect(fmtDay(null)).toBe("—");
    expect(fmtDay("not-a-date")).toBe("not-a-date");
    expect(fmtDayHeader("2026-10-28")).toBe("Wed, Oct 28, 2026");
    expect(fmtDayHeader("nope")).toBe("nope");
  });

  it("starts a window N days before the as-of", () => {
    expect(windowStart("2026-10-28", 30)).toBe("2026-09-28");
    expect(windowStart("2026-03-01", 90)).toBe("2025-12-01");
    expect(windowStart("2026-10-28", 365)).toBe("2025-10-28");
  });
});

describe("byWords", () => {
  it("words who and when, and says so when the day was not recorded", () => {
    expect(byWords({ kind: "ratified", by: "operator", on: "2026-10-20" })).toBe("ratified by operator · Oct 20, 2026");
    expect(byWords({ kind: "role", by: "operator", on: "2026-10-01" })).toBe("set by operator · Oct 1, 2026");
    expect(byWords({ kind: "role", by: "operator", on: null })).toBe("set by operator · day not recorded");
    expect(byWords({ kind: "trigger", by: null, on: null })).toBeNull();
  });
});

describe("the filters", () => {
  it("pass every row by default", () => {
    expect(rowMatches(row(), DEFAULT_FILTERS, ASOF)).toBe(true);
    expect(rowMatches(row({ date: null, ticker: null, authority: "card", kind: "kill" }), DEFAULT_FILTERS, ASOF)).toBe(true);
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
  });

  it("filter by class and by kind", () => {
    const noSystem = filters({ authorities: new Set(["operator", "automatic", "card"]) });
    expect(rowMatches(row(), noSystem, ASOF)).toBe(false);
    expect(rowMatches(row({ authority: "operator" }), noSystem, ASOF)).toBe(true);
    const onlyKills = filters({ kinds: new Set(["kill"]) });
    expect(rowMatches(row(), onlyKills, ASOF)).toBe(false);
    expect(rowMatches(row({ kind: "kill" }), onlyKills, ASOF)).toBe(true);
    expect(filtersActive(noSystem) && filtersActive(onlyKills)).toBe(true);
  });

  it("match a company by ticker text, and never match a row that has no company", () => {
    expect(rowMatches(row(), filters({ name: "okl" }), ASOF)).toBe(true);
    expect(rowMatches(row(), filters({ name: "smr" }), ASOF)).toBe(false);
    expect(rowMatches(row({ ticker: null }), filters({ name: "okl" }), ASOF)).toBe(false);
    expect(filtersActive(filters({ name: " " }))).toBe(false);
    expect(filtersActive(filters({ name: "o" }))).toBe(true);
  });

  it("keep only the rows with an EDGAR link, and leave out Context and Bench on request", () => {
    expect(rowMatches(row({ edgar: false }), filters({ edgarOnly: true }), ASOF)).toBe(false);
    expect(rowMatches(row({ edgar: true }), filters({ edgarOnly: true }), ASOF)).toBe(true);
    expect(rowMatches(row({ silent: "Context" }), filters({ countingOnly: true }), ASOF)).toBe(false);
    expect(rowMatches(row({ silent: null }), filters({ countingOnly: true }), ASOF)).toBe(true);
  });

  it("cut dated rows older than the window, never a standing row and never a row dated after the as-of", () => {
    const f = filters({ window: 30 });
    expect(rowMatches(row({ date: "2026-09-28" }), f, ASOF)).toBe(true); // the first day of the window
    expect(rowMatches(row({ date: "2026-09-27" }), f, ASOF)).toBe(false);
    expect(rowMatches(row({ date: "2026-11-20" }), f, ASOF)).toBe(true);
    expect(rowMatches(row({ date: null }), f, ASOF)).toBe(true);
    expect(rowMatches(row({ date: "2020-01-01" }), filters({ window: "all" }), ASOF)).toBe(true);
    expect(filtersActive(f)).toBe(true);
  });

  it("filter the days and the standing list, dropping a day left empty", () => {
    const days = [
      { date: "2026-10-27", rows: [row({ key: "a" })] },
      { date: "2026-10-20", rows: [row({ key: "b", authority: "operator" }), row({ key: "c" })] },
    ];
    const f = filters({ authorities: new Set(["operator"]) });
    const got = filterDays(days, f, ASOF);
    expect(got.map((d) => [d.date, d.rows.map((r) => r.key)])).toEqual([["2026-10-20", ["b"]]]);
    expect(countRows(got)).toBe(1);
    expect(countRows(filterDays(days, DEFAULT_FILTERS, ASOF))).toBe(3);
    expect(filterStanding([row({ key: "s", date: null }), row({ key: "t", date: null, authority: "card" })], filters({ authorities: new Set(["card"]) }), ASOF).map((r) => r.key)).toEqual(["t"]);
  });

  it("filter the picker by name, any case", () => {
    const cards = [
      { thesis_id: "1", name: "Copper", links: 4 },
      { thesis_id: "2", name: "Nuclear", links: 2 },
    ];
    expect(filterCards(cards, " NUC ").map((c) => c.name)).toEqual(["Nuclear"]);
    expect(filterCards(cards, "")).toHaveLength(2);
  });
});

const tallies = (over: Partial<EvidenceLedgerTalliesOut> = {}): EvidenceLedgerTalliesOut => ({
  rows: 10,
  by_authority: [
    { key: "operator", rows: 2 },
    { key: "system", rows: 3 },
    { key: "automatic", rows: 1 },
    { key: "card", rows: 4 },
  ],
  by_kind: [
    { key: "trigger", rows: 2 },
    { key: "risk_signal", rows: 1 },
    { key: "ratified", rows: 2 },
    { key: "catalyst_fact", rows: 0 },
    { key: "card_catalyst", rows: 2 },
    { key: "kill", rows: 1 },
    { key: "evidence", rows: 1 },
    { key: "role", rows: 1 },
  ],
  signals: 3,
  trace_accession: 2,
  trace_price: 1,
  trace_other: 0,
  trace_none: 0,
  facts_ratified: 1,
  facts_stored: 1,
  catalysts_dated: 1,
  catalysts_undated: 1,
  roles_dated: 0,
  roles_undated: 1,
  ...over,
});

describe("traceWords", () => {
  it("says how many fired signals trace to an accession and what the rest trace to", () => {
    expect(traceWords(tallies())).toBe("2 of 3 fired signals trace to an EDGAR accession · 1 to a price reference only");
    expect(traceWords(tallies({ signals: 5, trace_accession: 1, trace_price: 1, trace_other: 2, trace_none: 1 }))).toBe(
      "1 of 5 fired signals trace to an EDGAR accession · 1 to a price reference only · 2 to another source · 1 states no source",
    );
    expect(traceWords(tallies({ signals: 1, trace_accession: 1, trace_price: 0 }))).toBe(
      "1 of 1 fired signal traces to an EDGAR accession",
    );
  });

  it("says nothing when no signal fired", () => {
    expect(traceWords(tallies({ signals: 0, trace_accession: 0, trace_price: 0 }))).toBeNull();
  });
});

describe("nightWords", () => {
  it("names the recorded night, a reconstructed one, a missing one and an unreadable one", () => {
    expect(nightWords({ night: { asof: "2026-10-27", reconstructed: false }, night_unreadable: false })).toBe(
      "triggers from the call recorded for Oct 27, 2026",
    );
    expect(nightWords({ night: { asof: "2026-10-27", reconstructed: true }, night_unreadable: false })).toBe(
      "triggers from the call recorded for Oct 27, 2026 (a reconstructed night: reported, never scored)",
    );
    expect(nightWords({ night: null, night_unreadable: false })).toBe("no call recorded by this as-of, so no trigger is listed");
    expect(nightWords({ night: null, night_unreadable: true })).toMatch(/could not be read, so no trigger is listed/);
  });
});

describe("countOf", () => {
  it("reads a tally by key, 0 when absent", () => {
    expect(countOf(tallies().by_authority, "card")).toBe(4);
    expect(countOf(tallies().by_authority, "nope")).toBe(0);
  });
});

describe("workLines", () => {
  const data = (over: Partial<EvidenceLedgerOut> = {}): EvidenceLedgerOut => ({
    asof: ASOF,
    thesis_id: "t-nuke",
    thesis_name: "Nuclear",
    clock: "public",
    roster_fallback: false,
    night: { asof: "2026-10-27", reconstructed: false },
    night_unreadable: false,
    basket_rows: 5,
    silent_rows: 1,
    unresolved: [],
    facts_days: 365,
    names: [
      { key: "s-OKLO", ticker: "OKLO", security_id: "s-OKLO", role: null, silent: null, rows: 3 },
      { key: "s-MSFT", ticker: "MSFT", security_id: "s-MSFT", role: "payer", silent: "Context", rows: 1 },
      { key: "ticker:ZZZ", ticker: "ZZZ", security_id: null, role: null, silent: null, rows: 0 },
    ],
    standing: [],
    groups: [],
    tallies: tallies(),
    cache: "hit",
    compute_ms: 800,
    ...over,
  });

  it("states the clock, the night, the rows by class, the kinds held and the roster", () => {
    const lines = workLines(data(), 10, false);
    expect(lines[0]).toBe(
      "as of Oct 28, 2026 · as it was public by Oct 28, 2026 · triggers from the call recorded for Oct 27, 2026 · cached",
    );
    expect(lines[1]).toBe("10 rows: 2 your · 3 computed · 1 stored · 4 card");
    expect(lines[2]).toBe(
      "1 ratified fact and 1 stored fact (the last 365 days) · 1 catalyst dated, 1 undated · 1 kill criterion · 1 evidence link · 1 context role (1 undated)",
    );
    expect(lines[3]).toBe("5 roster rows · 2 companies resolved · 0 without a resolved security · 1 in Context or Bench");
  });

  it("says the live read, the compute time of a cold load and what the filters left", () => {
    const lines = workLines(data({ clock: "record", cache: "computed", compute_ms: 4200 }), 4, true);
    expect(lines[0]).toContain("live read");
    expect(lines[0]).toContain("computed in 4.2 s — first load since the last run or basket edit");
    expect(lines[1]).toBe("10 rows: 2 your · 3 computed · 1 stored · 4 card · 4 shown after the filters");
  });

  it("counts every row whatever the filters leave", () => {
    expect(workLines(data(), 0, true)[1]).toContain("10 rows");
  });
});

describe("unresolvedWords", () => {
  it("lists the roster names that can carry no fact", () => {
    expect(unresolvedWords([])).toBeNull();
    expect(unresolvedWords(["ZZZ"])).toBe("1 roster name has no resolved security, so no stored fact can be attached to it: ZZZ.");
    expect(unresolvedWords(["ZZZ", "YYY"])).toBe("2 roster names have no resolved security, so no stored fact can be attached to them: ZZZ, YYY.");
  });
});
