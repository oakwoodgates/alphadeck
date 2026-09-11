import { describe, expect, it } from "vitest";

import type { OperatorSpanOut, ScoreboardEpisodeOut } from "../../api/hooks";
import {
  episodeSortKey,
  nextLedgerSort,
  sortEpisodes,
  sortSpans,
  spanSortKey,
  type LedgerSortColId,
} from "../sortLedger";

// The pure sort machinery behind the sortable ledger columns. The load-bearing checks, mirroring
// the Cockpit's own sort tests: each column reads the RIGHT value off the SAME access path (and
// behind the SAME dash guard) the cell renders from; a "—" cell is an ABSENT (null) key, never a
// number; nulls-last holds in BOTH directions; a span carries no platform lens; and the sorters
// drop nothing (a re-order, never a filter) with a stable tie-break.

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    ticker: "DEVCO",
    arm_date: "2026-07-10",
    dearm_date: null,
    close_reason: "window_end",
    status: "open",
    matured: false,
    censored_start: false,
    exit_date: "2026-08-20",
    forward_return: null,
    peak_return: null,
    peak_date: null,
    trough_return: null,
    trough_date: null,
    intraday_high_return: null,
    intraday_high_date: null,
    intraday_low_return: null,
    intraday_low_date: null,
    exit_vs_peak_days: null,
    truncated: false,
    insufficient_prices: false,
    operator: null,
    ...over,
  } as ScoreboardEpisodeOut;
}

function span(over: Partial<OperatorSpanOut> = {}): OperatorSpanOut {
  return {
    take_id: "d1",
    take_date: "2026-07-11",
    security_id: "s-j",
    ticker: "J",
    thesis_level: false,
    override: false,
    running: true,
    operator_return: 0.0672,
    ...over,
  } as OperatorSpanOut;
}

/** A fully-scored episode — every column has a real value to rank on. */
const scored = ep({
  ticker: "MATR",
  status: "closed",
  matured: true,
  dearm_date: "2026-08-18",
  forward_return: 0.123,
  peak_return: 0.204,
  trough_return: -0.056,
  intraday_high_return: 0.231,
  intraday_low_return: -0.084,
  exit_vs_peak_days: 7,
});

const ALL_COLS: LedgerSortColId[] = [
  "name",
  "armed",
  "dearmed",
  "ret",
  "peak",
  "peak_high",
  "worst",
  "worst_low",
  "past_peak",
];

describe("episodeSortKey — each column reads the value its CELL renders", () => {
  it("resolves every sortable column off a fully-scored episode", () => {
    expect(episodeSortKey("name", scored)).toEqual(["matr"]); // case-insensitive
    expect(episodeSortKey("armed", scored)).toEqual([Date.parse("2026-07-10T00:00:00Z")]);
    expect(episodeSortKey("dearmed", scored)).toEqual([Date.parse("2026-08-18T00:00:00Z")]);
    expect(episodeSortKey("ret", scored)).toEqual([0.123]);
    expect(episodeSortKey("peak", scored)).toEqual([0.204]);
    expect(episodeSortKey("peak_high", scored)).toEqual([0.231]);
    expect(episodeSortKey("worst", scored)).toEqual([-0.056]);
    expect(episodeSortKey("worst_low", scored)).toEqual([-0.084]);
    expect(episodeSortKey("past_peak", scored)).toEqual([7]);
  });

  it("a still-OPEN episode has no de-arm — absent, never 'someday'", () => {
    expect(episodeSortKey("dearmed", ep({ dearm_date: null }))).toBeNull();
    // ...while its arm date is a real value, so an open row still ranks on Armed
    expect(episodeSortKey("armed", ep({ dearm_date: null }))).not.toBeNull();
  });

  it("an unresolved ticker is ABSENT (the cell renders '—'), not an empty-string minimum", () => {
    expect(episodeSortKey("name", ep({ ticker: null }))).toBeNull();
  });

  it("no-forward-bar dashes the whole timing lens — the SAME guard the cells use", () => {
    // insufficient_prices: no bar at all. A degenerate 0.0%/0d must not rank as a measurement.
    const awaiting = ep({
      insufficient_prices: true,
      forward_return: 0,
      peak_return: 0,
      trough_return: 0,
      intraday_high_return: 0,
      intraday_low_return: 0,
      exit_vs_peak_days: 0,
    });
    for (const col of ["peak", "peak_high", "worst", "worst_low", "past_peak"] as const) {
      expect({ col, key: episodeSortKey(col, awaiting) }).toEqual({ col, key: null });
    }
    // the single-arm-day case is `awaitingForwardBar` — same dash on Return, same rule
    const singleBar = ep({ arm_date: "2026-07-10", exit_date: "2026-07-10", forward_return: 0 });
    expect(episodeSortKey("ret", singleBar)).toBeNull();
  });

  it("a real 0.0 IS a measurement and ranks — only the degenerate case is absent", () => {
    // 24% of the record never closed below entry; `trough_return: 0` is the answer, not ignorance
    expect(episodeSortKey("worst", ep({ trough_return: 0, exit_date: "2026-08-20" }))).toEqual([0]);
    expect(episodeSortKey("past_peak", ep({ exit_vs_peak_days: 0 }))).toEqual([0]);
  });

  it("a wick is independently absent — the all-or-nothing rule, per column", () => {
    // one bar missing a high nulls intraday_high_* entirely while the closes stay present: the
    // Peak cell shows a number and the Peak high cell shows "—", and the keys must agree
    const halfWick = ep({ peak_return: 0.2, intraday_high_return: null, trough_return: -0.05 });
    expect(episodeSortKey("peak", halfWick)).toEqual([0.2]);
    expect(episodeSortKey("peak_high", halfWick)).toBeNull();
  });
});

describe("spanSortKey — a logged take carries no platform lens", () => {
  it("ranks on its name and its take date only", () => {
    expect(spanSortKey("name", span())).toEqual(["j"]);
    expect(spanSortKey("armed", span())).toEqual([Date.parse("2026-07-11T00:00:00Z")]);
  });

  it("every platform column is ABSENT — including Return, which is the operator's, not the record's", () => {
    for (const col of ["dearmed", "ret", "peak", "peak_high", "worst", "worst_low", "past_peak"] as const) {
      expect({ col, key: spanSortKey(col, span()) }).toEqual({ col, key: null });
    }
  });

  it("a thesis-level span has no name to rank (the cell renders ◇)", () => {
    expect(spanSortKey("name", span({ ticker: null, thesis_level: true }))).toBeNull();
  });
});

describe("sortEpisodes — nulls last in BOTH directions, and nothing is ever dropped", () => {
  const a = ep({ security_id: "a", ticker: "AAA", peak_return: 0.1, exit_date: "2026-08-20" });
  const b = ep({ security_id: "b", ticker: "BBB", peak_return: 0.3, exit_date: "2026-08-20" });
  const absent = ep({ security_id: "z", ticker: "ZZZ", insufficient_prices: true });
  const rows = [a, absent, b];

  it("desc ranks high→low with the absent row last", () => {
    const out = sortEpisodes(rows, { col: "peak", dir: "desc" });
    expect(out.map((e) => e.ticker)).toEqual(["BBB", "AAA", "ZZZ"]);
  });

  it("asc ranks low→high with the absent row STILL last (a missing measurement is not a small one)", () => {
    const out = sortEpisodes(rows, { col: "peak", dir: "asc" });
    expect(out.map((e) => e.ticker)).toEqual(["AAA", "BBB", "ZZZ"]);
  });

  it("a re-order, never a filter — every column keeps the row count (#9)", () => {
    for (const col of ALL_COLS) {
      for (const dir of ["asc", "desc"] as const) {
        expect({ col, dir, n: sortEpisodes(rows, { col, dir }).length }).toEqual({
          col,
          dir,
          n: rows.length,
        });
      }
    }
  });

  it("a null sort is the record's own order, and the input array is never mutated", () => {
    const input = [...rows];
    expect(sortEpisodes(input, null).map((e) => e.ticker)).toEqual(["AAA", "ZZZ", "BBB"]);
    sortEpisodes(input, { col: "peak", dir: "desc" });
    expect(input.map((e) => e.ticker)).toEqual(["AAA", "ZZZ", "BBB"]); // untouched
  });

  it("equal / both-absent rows keep their incoming order (a stable tie-break)", () => {
    const z1 = ep({ security_id: "z1", ticker: "Z1", insufficient_prices: true });
    const z2 = ep({ security_id: "z2", ticker: "Z2", insufficient_prices: true });
    expect(sortEpisodes([z1, z2], { col: "peak", dir: "desc" }).map((e) => e.ticker)).toEqual([
      "Z1",
      "Z2",
    ]);
  });

  it("dates rank chronologically, not lexically by string luck", () => {
    const early = ep({ security_id: "e", ticker: "EARLY", arm_date: "2026-09-02" });
    const late = ep({ security_id: "l", ticker: "LATE", arm_date: "2026-10-01" });
    expect(
      sortEpisodes([early, late], { col: "armed", dir: "desc" }).map((e) => e.ticker),
    ).toEqual(["LATE", "EARLY"]);
  });
});

describe("sortSpans — spans re-rank among themselves", () => {
  const s1 = span({ take_id: "1", ticker: "CCC", take_date: "2026-07-11" });
  const s2 = span({ take_id: "2", ticker: "AAA", take_date: "2026-07-20" });

  it("ranks on name", () => {
    expect(sortSpans([s1, s2], { col: "name", dir: "asc" }).map((s) => s.ticker)).toEqual([
      "AAA",
      "CCC",
    ]);
  });

  it("a platform column leaves every span absent, so the logged order stands", () => {
    expect(sortSpans([s1, s2], { col: "peak", dir: "desc" }).map((s) => s.ticker)).toEqual([
      "CCC",
      "AAA",
    ]);
  });
});

describe("nextLedgerSort — the reversible 3-state cycle", () => {
  it("a fresh column starts desc, then asc, then off (back to the record's order)", () => {
    const first = nextLedgerSort(null, "peak");
    expect(first).toEqual({ col: "peak", dir: "desc" });
    const second = nextLedgerSort(first, "peak");
    expect(second).toEqual({ col: "peak", dir: "asc" });
    expect(nextLedgerSort(second, "peak")).toBeNull();
  });

  it("switching columns restarts at desc rather than inheriting the old direction", () => {
    expect(nextLedgerSort({ col: "peak", dir: "asc" }, "worst")).toEqual({
      col: "worst",
      dir: "desc",
    });
  });
});
