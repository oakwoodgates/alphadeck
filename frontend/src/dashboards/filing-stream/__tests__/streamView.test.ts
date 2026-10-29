import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTERS,
  KIND_LABEL,
  KIND_ORDER,
  compactUsd,
  countRows,
  filterCards,
  filterDays,
  filtersActive,
  fmtDay,
  fmtDayHeader,
  foldEntries,
  foldWords,
  rowMatches,
  unresolvedWords,
  usdHint,
  workLines,
  type Filters,
  type StreamKind,
} from "../streamView";
import type {
  FilingStreamDayOut,
  FilingStreamLinkOut,
  FilingStreamOut,
  FilingStreamRowOut,
} from "../useFilingStream";

// The Filing Stream's pure view helpers: the kinds' words, the client-side filters (kind, link, name, role,
// "Counting names only"), the fold of a day's routine Form 4s (nothing removed, one line), the dollar hint, the
// day header, the picker's filter and the show-the-work lines (every row counted, folded or not).

const row = (over: Partial<FilingStreamRowOut> = {}): FilingStreamRowOut => ({
  key: "0",
  security_id: "s-xxx",
  ticker: "XXX",
  kind: "8k",
  form: "8-K",
  date: "2026-10-28",
  headline: "8-K · 9.01",
  detail: [],
  url: null,
  accession: null,
  tags: [],
  routine: false,
  usd: null,
  links: ["Reactors"],
  role: null,
  silent: false,
  ...over,
});

const LINKS: FilingStreamLinkOut[] = [
  { key: "link:Reactors", label: "Reactors", kind: "link", names: 2 },
  { key: "link:Fuel", label: "Fuel", kind: "link", names: 1 },
  { key: "no_link", label: "No link", kind: "no_link", names: 1 },
  { key: "silent:Context", label: "Context", kind: "silent", names: 1 },
];

const filters = (over: Partial<Filters> = {}): Filters => ({ ...DEFAULT_FILTERS, ...over });

describe("the kinds", () => {
  it("lists every kind in a day's order, each with a label", () => {
    expect(KIND_ORDER).toEqual(["8k", "stake", "form4", "form4a", "revenue", "split", "dilution", "catalyst", "ratified"]);
    expect(KIND_ORDER.map((k) => KIND_LABEL[k])).toEqual([
      "8-K",
      "13D/G",
      "Form 4",
      "4/A",
      "Revenue",
      "Split",
      "Dilution",
      "Catalyst",
      "Ratified fact",
    ]);
  });
});

describe("rowMatches", () => {
  it("passes every row on the defaults", () => {
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    for (const k of KIND_ORDER) expect(rowMatches(row({ kind: k }), DEFAULT_FILTERS, LINKS)).toBe(true);
  });

  it("filters by kind", () => {
    const f = filters({ kinds: new Set<StreamKind>(["form4"]) });
    expect(filtersActive(f)).toBe(true);
    expect(rowMatches(row({ kind: "form4" }), f, LINKS)).toBe(true);
    expect(rowMatches(row({ kind: "8k" }), f, LINKS)).toBe(false);
  });

  it("filters by link, a company in two links matching both, and 'No link' matching a company in none", () => {
    const both = row({ links: ["Reactors", "Fuel"] });
    expect(rowMatches(both, filters({ link: "link:Fuel" }), LINKS)).toBe(true);
    expect(rowMatches(both, filters({ link: "link:Reactors" }), LINKS)).toBe(true);
    expect(rowMatches(row({ links: ["Reactors"] }), filters({ link: "link:Fuel" }), LINKS)).toBe(false);
    expect(rowMatches(row({ links: [null] }), filters({ link: "no_link" }), LINKS)).toBe(true);
    expect(rowMatches(row({ links: ["Reactors"] }), filters({ link: "no_link" }), LINKS)).toBe(false);
    expect(rowMatches(row({ links: ["Context"], silent: true }), filters({ link: "silent:Context" }), LINKS)).toBe(true);
    // a link key the card no longer offers (a card switch) filters nothing out
    expect(rowMatches(row(), filters({ link: "link:Gone" }), LINKS)).toBe(true);
  });

  it("filters by ticker (any case, a part of it), by role, and 'Counting names only'", () => {
    expect(rowMatches(row({ ticker: "OKLO" }), filters({ name: "kl" }), LINKS)).toBe(true);
    expect(rowMatches(row({ ticker: "OKLO" }), filters({ name: " SMR " }), LINKS)).toBe(false);
    expect(rowMatches(row({ role: "payer" }), filters({ role: "payer" }), LINKS)).toBe(true);
    expect(rowMatches(row({ role: null }), filters({ role: "payer" }), LINKS)).toBe(false);
    expect(rowMatches(row({ silent: true }), filters({ countingOnly: true }), LINKS)).toBe(false);
    expect(rowMatches(row({ silent: false }), filters({ countingOnly: true }), LINKS)).toBe(true);
    // the fold toggle is not a row filter: it hides nothing
    expect(filtersActive(filters({ showRoutine: true }))).toBe(false);
  });
});

describe("filterDays", () => {
  const days: FilingStreamDayOut[] = [
    { date: "2026-10-28", routine: 1, rows: [row({ key: "1" }), row({ key: "2", kind: "form4", routine: true })] },
    { date: "2026-10-20", routine: 0, rows: [row({ key: "3", ticker: "SMR" })] },
  ];

  it("drops the rows that fail and a day left empty, recounting a day's routine rows", () => {
    const got = filterDays(days, filters({ name: "smr" }), LINKS);
    expect(got.map((d) => [d.date, d.rows.map((r) => r.key)])).toEqual([["2026-10-20", ["3"]]]);
    const kept = filterDays(days, filters({ kinds: new Set<StreamKind>(["form4"]) }), LINKS);
    expect(kept).toHaveLength(1);
    expect(kept[0].routine).toBe(1);
    expect(countRows(filterDays(days, DEFAULT_FILTERS, LINKS))).toBe(3);
  });
});

describe("foldEntries", () => {
  const rows = [
    row({ key: "a", kind: "8k" }),
    row({ key: "b", kind: "form4", routine: true }),
    row({ key: "c", kind: "form4", routine: false }),
    row({ key: "d", kind: "form4", routine: true }),
    row({ key: "e", kind: "form4a" }),
  ];

  it("collapses a day's routine rows into ONE line standing where the first was; nothing is removed", () => {
    const got = foldEntries(rows, false);
    expect(got.map((e) => (e.type === "row" ? e.row.key : `fold(${e.rows.map((r) => r.key).join("")})`))).toEqual([
      "a",
      "fold(bd)",
      "c",
      "e",
    ]);
  });

  it("shows every row in place when the routine ones are shown, and folds nothing when none is routine", () => {
    expect(foldEntries(rows, true).map((e) => e.type)).toEqual(["row", "row", "row", "row", "row"]);
    expect(foldEntries([rows[0], rows[2]], false).every((e) => e.type === "row")).toBe(true);
  });

  it("words the fold with its count and that none holds a purchase", () => {
    expect(foldWords(1)).toBe(
      "1 routine Form 4 filing (none holds a purchase: sales, grants, exercises, withholding, other codes)",
    );
    expect(foldWords(19)).toMatch(/^19 routine Form 4 filings \(none holds a purchase/);
  });
});

describe("the words", () => {
  it("words a date and a day header", () => {
    expect(fmtDay("2026-10-28")).toBe("Oct 28, 2026");
    expect(fmtDay(null)).toBe("—");
    expect(fmtDayHeader("2026-10-28")).toBe("Wed, Oct 28, 2026");
    expect(fmtDayHeader("junk")).toBe("junk");
  });

  it("words a compact dollar figure and the row's dollar hint (a Form 4's net, a dilution's principal, nothing else)", () => {
    expect(compactUsd(49_200)).toBe("$49.2K");
    expect(compactUsd(-2_400)).toBe("-$2.4K");
    expect(compactUsd(350_000_000)).toBe("$350.0M");
    expect(compactUsd(1_250_000_000)).toBe("$1.3B");
    expect(compactUsd(512)).toBe("$512");
    expect(usdHint({ kind: "form4", usd: 34_200 })).toBe("+$34.2K net");
    expect(usdHint({ kind: "form4", usd: -2_400 })).toBe("-$2.4K net");
    expect(usdHint({ kind: "form4a", usd: 6_000 })).toBe("+$6.0K net");
    expect(usdHint({ kind: "dilution", usd: 350_000_000 })).toBe("$350.0M");
    expect(usdHint({ kind: "form4", usd: null })).toBeNull();
    expect(usdHint({ kind: "revenue", usd: 5 })).toBeNull();
  });

  it.each([
    [0, "$0"],
    [999.4, "$999"],
    [999.5, "$1.0K"],
    [999_949, "$999.9K"],
    [999_950, "$1.0M"],
    [999_949_999, "$999.9M"],
    [999_950_000, "$1.0B"],
    [-999_949, "-$999.9K"],
    [-999_950, "-$1.0M"],
  ])(
    "rolls to the next unit once the shown figure rounds to 1,000 (one decimal above a thousand): %d is %s, never $1000.0K",
    (value, words) => {
      expect(compactUsd(value)).toBe(words);
    },
  );

  it("shows the dollar hint's rollover too: a $999,950 net sale is -$1.0M net, not -$1000.0K", () => {
    expect(usdHint({ kind: "form4", usd: -999_950 })).toBe("-$1.0M net");
    expect(usdHint({ kind: "form4", usd: 999_950 })).toBe("+$1.0M net");
    expect(usdHint({ kind: "form4", usd: -999_949 })).toBe("-$999.9K net");
    expect(usdHint({ kind: "dilution", usd: 999_950_000 })).toBe("$1.0B");
  });

  it("filters the picker by name (any case), in the server's order", () => {
    const cards = [
      { thesis_id: "1", name: "Copper", links: 4 },
      { thesis_id: "2", name: "Nuclear", links: 2 },
    ];
    expect(filterCards(cards, " NUC ").map((c) => c.name)).toEqual(["Nuclear"]);
    expect(filterCards(cards, "").map((c) => c.name)).toEqual(["Copper", "Nuclear"]);
  });

  it("lists the roster names that carry no facts, never silently dropping them", () => {
    expect(unresolvedWords([])).toBeNull();
    expect(unresolvedWords(["ZZZ"])).toBe(
      "1 roster name has no resolved security, so no filing can be attached to it: ZZZ.",
    );
    expect(unresolvedWords(["ZZZ", "QQQ"])).toMatch(/^2 roster names have no resolved security.*attached to them: ZZZ, QQQ\.$/);
  });
});

describe("workLines", () => {
  const data = (over: Partial<FilingStreamOut> = {}): FilingStreamOut => ({
    asof: "2026-10-28",
    thesis_id: "t",
    thesis_name: "Grid",
    days: 90,
    window_start: "2026-07-30",
    clock: "public",
    roster_fallback: false,
    links_today: 2,
    basket_rows: 57,
    silent_rows: 8,
    unresolved: ["ZZZ", "QQQ"],
    links: LINKS,
    roles: [],
    names: [
      { key: "a", ticker: "XXX", security_id: "a", links: ["Reactors"], role: null, silent: false, rows: 3 },
      { key: "b", ticker: "YYY", security_id: "b", links: ["Fuel"], role: null, silent: false, rows: 0 },
      { key: "ticker:ZZZ", ticker: "ZZZ", security_id: null, links: [null], role: null, silent: false, rows: 0 },
    ],
    groups: [],
    counts: {
      rows: 41,
      by_kind: [
        { kind: "8k", rows: 12 },
        { kind: "stake", rows: 2 },
        { kind: "form4", rows: 22 },
        { kind: "form4a", rows: 1 },
        { kind: "revenue", rows: 3 },
        { kind: "split", rows: 0 },
        { kind: "dilution", rows: 0 },
        { kind: "catalyst", rows: 1 },
        { kind: "ratified", rows: 0 },
      ],
      form4_purchases: 3,
      routine: 19,
    },
    windows: [30, 90, 365],
    cache: "hit",
    compute_ms: 900,
    ...over,
  });

  it("shows the work: the window and clock, the roster, and every kind's count with the purchases", () => {
    const [a, b, c] = workLines(data(), 41, 19, false);
    expect(a).toBe(
      "as of Oct 28, 2026 · window Jul 30, 2026 – Oct 28, 2026 (the last 90 days) · as it was public by Oct 28, 2026 · cached",
    );
    expect(b).toBe(
      "57 roster rows · 2 companies resolved · 2 without a resolved security · 8 in Context or Bench",
    );
    expect(c).toBe(
      "41 rows in the window: 12 8-Ks · 2 13D/G stakes · 22 Form 4s (3 hold a purchase) · 1 4/A · 3 revenue prints · 0 splits · 0 dilution rows · 1 catalyst · 0 ratified facts · 19 routine Form 4 rows folded (counted above)",
    );
  });

  it("says live for the record clock, notes the filters, and says how long a first load took", () => {
    const [a, , c] = workLines(data({ clock: "record", cache: "computed", compute_ms: 8400 }), 7, 0, true);
    expect(a).toContain("live read");
    expect(a).not.toContain("as it was public by");
    expect(a).toContain("computed in 8.4 s — first load since the last run or basket edit");
    expect(c).toContain("7 shown after the filters");
    expect(c).not.toContain("folded");
  });
});
