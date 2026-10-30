import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTERS,
  HORIZONS,
  LIMITS,
  MIN_N,
  NONE_KEY,
  POOLED,
  UNPINNED,
  benchOf,
  capWords,
  cellMembers,
  cellStats,
  cellView,
  chipGroups,
  editionWords,
  filtersActive,
  fmtRate,
  fmtRet,
  fmtUsd,
  liquidityWords,
  measureWords,
  observations,
  quantile,
  rowMatches,
  sliceKey,
  spanValue,
  spanView,
  tableRows,
  toneOf,
  workLines,
  type Obs,
} from "../recordView";
import { EXCESS_12, SERVER, cap, entry, maturing, payload, row, span } from "./fixtures";

// The Track Record's pure helpers. The cell arithmetic mirrors the backend's (dashboards/track_record.py); the SAME
// hand-computed vectors are pinned in backend/tests/dashboards/test_track_record.py, and the server's own opening
// table (a response produced by the backend) is reproduced here from the rows alone.

const obsOf = (values: number[], bench = 0.25): Obs[] => values.map((v) => ({ ret: v + bench, bench }));
const near = (a: number | null, b: number | null) =>
  a == null || b == null ? expect(a).toBe(b) : expect(a).toBeCloseTo(b, 12);

describe("quantile — linear interpolation between order statistics", () => {
  it("is the backend's rule on the same vectors", () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(quantile([1, 2, 3, 4], 0.25)).toBeCloseTo(1.75, 12);
    expect(quantile([1, 2, 3, 4], 0.75)).toBeCloseTo(3.25, 12);
    expect(quantile([7], 0.25)).toBe(7);
    expect(quantile([1, 2, 3], 0.5)).toBe(2);
    expect(() => quantile([], 0.5)).toThrow();
  });
});

describe("cellStats — n, the median, the quartiles and how often it held", () => {
  it("states them on the twelve-value vector the backend pins", () => {
    const c = cellStats(obsOf(EXCESS_12), "iwm");
    expect(c.n).toBe(12);
    expect(c.tooSmall).toBe(false);
    expect(c.median).toBeCloseTo(0.025, 12); // the mean of the 6th and 7th values
    expect(c.q1).toBeCloseTo(-0.005, 12);
    expect(c.q3).toBeCloseTo(0.0525, 12);
    expect(c.held).toBe(8); // the excess of exactly 0.00 did not hold: strictly above zero
    expect(c.hitRate).toBeCloseTo(8 / 12, 12);
    expect(c.nameMedian).toBeCloseTo(0.275, 12);
    expect(c.benchMedian).toBeCloseTo(0.25, 12);
    expect(c.noComparison).toBe(0);
  });

  it("calls a cell under ten episodes too small to read, and ten is readable", () => {
    expect(MIN_N).toBe(10);
    const nine = cellStats(obsOf(EXCESS_12.slice(0, 9)), "iwm");
    const ten = cellStats(obsOf(EXCESS_12.slice(0, 10)), "iwm");
    expect([nine.n, nine.tooSmall]).toEqual([9, true]);
    expect([ten.n, ten.tooSmall]).toEqual([10, false]);
  });

  it("leaves a cell with no episode blank, not too small and never zero", () => {
    const c = cellStats([], "iwm");
    expect(c).toMatchObject({ n: 0, held: 0, tooSmall: false, median: null, hitRate: null, q1: null, q3: null });
  });

  it("reads the raw return under raw, and a missing benchmark does not drop the span", () => {
    const c = cellStats(
      [
        { ret: 0.1, bench: null },
        { ret: -0.02, bench: null },
        { ret: 0.03, bench: null },
      ],
      "raw",
    );
    expect([c.n, c.held]).toEqual([3, 2]);
    expect(c.median).toBeCloseTo(0.03, 12);
    expect(c.benchMedian).toBeNull();
  });

  it("counts the spans a missing comparison left out apart from the cell", () => {
    const c = cellStats(
      [
        { ret: 0.1, bench: 0.04 },
        { ret: 0.02, bench: null },
        { ret: -0.01, bench: 0.0 },
      ],
      "spy",
    );
    expect([c.n, c.noComparison, c.held]).toEqual([2, 1, 1]);
    expect(c.median).toBeCloseTo((0.06 + -0.01) / 2, 12);
  });
});

describe("tableRows — the tiers in size order, unpinned, then pooled LAST", () => {
  const rows = SERVER.rows;

  it("lists every tier (empty or not), then unpinned, then the pooled row last", () => {
    const t = tableRows(rows, "tier", "arm_night", "iwm");
    expect(t.map((r) => r.key)).toEqual(["nano", "micro", "small", "mid", "large", "unpinned", POOLED]);
    expect(t.at(-1)?.label).toBe("all tiers (pooled)");
    expect(t.find((r) => r.key === "nano")?.episodes).toBe(0);
    const liq = tableRows(rows, "liquidity", "arm_night", "iwm");
    expect(liq.map((r) => r.key)).toEqual(["under_1m", "1m_10m", "10m_plus", "unpinned", POOLED]);
    expect(liq.at(-1)?.label).toBe("all liquidity bands (pooled)");
  });

  it("puts a left-out episode in no row, the pooled one included", () => {
    const t = tableRows(rows, "tier", "arm_night", "iwm");
    expect(rows).toHaveLength(20);
    expect(t.find((r) => r.key === POOLED)?.episodes).toBe(17);
    expect(t.find((r) => r.key === POOLED)?.members.every((r) => r.left_out == null)).toBe(true);
  });

  it("reproduces the server's own opening table (the next-session entry) from the rows alone, cell by cell", () => {
    const t = tableRows(rows, "tier", "next_session", "iwm");
    expect(t.map((r) => r.key)).toEqual(SERVER.opening.map((r) => r.key));
    for (const srv of SERVER.opening) {
      const mine = t.find((r) => r.key === srv.key)!;
      expect(mine.episodes, srv.key).toBe(srv.episodes);
      for (const h of HORIZONS) {
        const a = mine.cells[h.key];
        const b = srv.cells[h.key];
        expect([a.n, a.held, a.tooSmall, a.noComparison], `${srv.key} ${h.key}`).toEqual([
          b.n,
          b.held,
          b.too_small,
          b.no_comparison,
        ]);
        near(a.hitRate, b.hit_rate);
        near(a.median, b.median);
        near(a.q1, b.q1);
        near(a.q3, b.q3);
        near(a.nameMedian, b.name_median);
        near(a.benchMedian, b.bench_median);
      }
    }
  });

  it("reads the picked entry rule: the next-session entry has its own closes", () => {
    const small = (e: "arm_night" | "next_session") =>
      tableRows(rows, "tier", e, "iwm").find((r) => r.key === "small")!.cells.h20;
    expect(small("arm_night").median).toBeCloseTo(0.025, 12);
    expect(small("next_session").median).toBeCloseTo(-0.025, 12); // every next-session return is 5 points lower
    expect(small("next_session").held).toBe(3); // 0.06, 0.08 and 0.12 stay above zero
    expect(small("next_session").n).toBe(12);
  });

  it("reads the picked comparison", () => {
    const small = (c: "iwm" | "spy" | "raw") => tableRows(rows, "tier", "arm_night", c).find((r) => r.key === "small")!.cells.h20;
    expect(small("iwm").median).toBeCloseTo(0.025, 12);
    expect(small("spy").median).toBeCloseTo(0.175, 12); // the names' 0.275 minus SPY's 0.10
    expect(small("spy").held).toBe(12);
    expect(small("raw").median).toBeCloseTo(0.275, 12);
    expect(small("raw").benchMedian).toBeNull();
  });

  it("counts a path that ended or a horizon that has not arrived as no episode in the cell", () => {
    const t = tableRows(rows, "tier", "arm_night", "iwm");
    expect(t.find((r) => r.key === "mid")?.episodes).toBe(1);
    expect(t.find((r) => r.key === "mid")?.cells.h20.n).toBe(0); // its 20-session path ended
    expect(t.find((r) => r.key === "small")?.cells.h60.n).toBe(0); // nothing has reached 60 sessions
  });

  it("lists exactly the episodes a cell rests on", () => {
    const small = tableRows(rows, "tier", "arm_night", "iwm").find((r) => r.key === "small")!;
    expect(cellMembers(small.members, "arm_night", "h20", "iwm")).toHaveLength(12);
    expect(cellMembers(small.members, "arm_night", "h60", "iwm")).toHaveLength(0);
    const noBench = row({ arm_night: entry({ h20: span({ iwm: null }) }) });
    expect(cellMembers([noBench], "arm_night", "h20", "iwm")).toHaveLength(0);
    expect(cellMembers([noBench], "arm_night", "h20", "raw")).toHaveLength(1);
  });
});

describe("the filters — chips narrow every cell and never cross-tabulate", () => {
  const counted = SERVER.rows.filter((r) => r.left_out == null);

  it("passes everything by default and knows when one is on", () => {
    expect(counted.every((r) => rowMatches(r, DEFAULT_FILTERS))).toBe(true);
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    expect(filtersActive({ ...DEFAULT_FILTERS, grade: ["flip"] })).toBe(true);
  });

  it("filters on the headline name, the Key-1 source, the entry grade and the group size", () => {
    const n = (f: Partial<typeof DEFAULT_FILTERS>) => counted.filter((r) => rowMatches(r, { ...DEFAULT_FILTERS, ...f })).length;
    expect(n({ headline: true })).toBe(counted.filter((r) => r.is_headline).length);
    expect(n({ key1: ["activist"] })).toBe(5);
    expect(n({ key1: ["activist", "theme"] })).toBe(8); // within a group any picked value passes
    expect(n({ grade: ["flip"] })).toBe(4);
    expect(n({ coArm: ["2-3"] })).toBe(4);
    expect(n({ key1: ["activist"], coArm: ["2-3"] })).toBe(4); // across groups all must pass
    expect(n({ key1: ["theme"], coArm: ["2-3"] })).toBe(0);
  });

  it("filters a null attribute under its own value", () => {
    const r = row({ key1_source: null, entry_grade: null });
    expect(rowMatches(r, { ...DEFAULT_FILTERS, key1: [NONE_KEY] })).toBe(true);
    expect(rowMatches(r, { ...DEFAULT_FILTERS, grade: ["core"] })).toBe(false);
  });

  it("offers a chip only for a value some counted row carries, with how many do", () => {
    const g = chipGroups(counted);
    expect(g.key1.map((o) => [o.value, o.n])).toEqual([
      ["insider", 8],
      ["activist", 5],
      ["revenue_accel", 1],
      ["theme", 3],
    ]);
    expect(g.key1.map((o) => o.label)).toEqual(["insider", "activist", "revenue acceleration", "theme"]);
    expect(g.grade.map((o) => [o.value, o.n])).toEqual([
      ["flip", 4],
      ["core", 13],
    ]);
    expect(g.coArm.map((o) => [o.value, o.n])).toEqual([
      ["alone", 13],
      ["2-3", 4],
    ]);
    expect(g.coArm.map((o) => o.label)).toEqual(["armed alone", "group of 2-3"]);
  });

  it("puts a value that is unknown after the known ones and 'none stated' last", () => {
    const g = chipGroups([row({ key1_source: null }), row({ key1_source: "novel_source" }), row({ key1_source: "theme" })]);
    expect(g.key1.map((o) => o.value)).toEqual(["theme", "novel_source", NONE_KEY]);
    expect(g.key1.map((o) => o.label)).toEqual(["theme", "novel source", "none stated"]);
  });
});

describe("the slice keys", () => {
  it("bands a row by its cap tier or its liquidity, unpinned when it has none", () => {
    const r = row();
    expect([sliceKey(r, "tier"), sliceKey(r, "liquidity")]).toEqual(["small", "1m_10m"]);
    const bare = row({ cap: cap({ tier: null, value: null, gap: "no_shares" }), liquidity: null });
    expect([sliceKey(bare, "tier"), sliceKey(bare, "liquidity")]).toEqual([UNPINNED, UNPINNED]);
    expect(sliceKey(row({ cap: null }), "tier")).toBe(UNPINNED);
  });
});

describe("spans and observations", () => {
  it("reads the comparison's return and the value a cell takes", () => {
    const s = span({ ret: 0.1, iwm: 0.04, spy: 0.01 });
    expect([benchOf(s, "iwm"), benchOf(s, "spy"), benchOf(s, "raw")]).toEqual([0.04, 0.01, null]);
    expect(spanValue(s, "iwm")).toBeCloseTo(0.06, 12);
    expect(spanValue(s, "spy")).toBeCloseTo(0.09, 12);
    expect(spanValue(s, "raw")).toBe(0.1);
    expect(spanValue(span({ iwm: null }), "iwm")).toBeNull();
    expect(spanValue(maturing(), "raw")).toBeNull();
    expect(spanValue(undefined, "raw")).toBeNull();
  });

  it("takes only the counted spans as observations", () => {
    const rows = [row(), row({ arm_night: entry({ h20: maturing() }) }), row({ arm_night: entry() })];
    expect(observations(rows, "arm_night", "h20", "iwm")).toEqual([{ ret: 0.1, bench: 0.04 }]);
    expect(observations(rows, "arm_night", "h5", "iwm")).toEqual([]);
  });
});

describe("the words", () => {
  it("formats a return with its sign, and a move that rounds to nothing carries none", () => {
    expect(fmtRet(0.06)).toBe("+6.0%");
    expect(fmtRet(-0.012)).toBe("−1.2%");
    expect(fmtRet(0)).toBe("0.0%");
    expect(fmtRet(0.00004)).toBe("0.0%");
    expect(fmtRet(-0.00004)).toBe("0.0%");
    expect(fmtRet(0.1234, 2)).toBe("+12.34%");
    expect(fmtRet(null)).toBe("");
    expect(fmtRate(8 / 12)).toBe("67%");
    expect(fmtRate(null)).toBe("");
  });

  it("formats dollars compactly", () => {
    expect([fmtUsd(576e6), fmtUsd(48.2e6), fmtUsd(1.2e9), fmtUsd(12e9), fmtUsd(350_000), fmtUsd(900)]).toEqual([
      "$576M",
      "$48.2M",
      "$1.2B",
      "$12B",
      "$350K",
      "$900",
    ]);
  });

  it("tones only a signed figure", () => {
    expect([toneOf(0.01), toneOf(-0.01), toneOf(0), toneOf(null)]).toEqual(["pos", "neg", "", ""]);
  });

  it("states the horizon, the comparison, n and how often it held in every readable cell's hover", () => {
    const h20 = HORIZONS.find((h) => h.key === "h20")!;
    const c = cellStats(obsOf(EXCESS_12), "iwm");
    const v = cellView(c, h20, "iwm", "arm_night", false);
    expect(v.text).toBe("+2.5%");
    expect(v.sub).toBe("8/12");
    expect(v.tone).toBe("pos");
    expect(v.gray).toBe(false);
    expect(v.clickable).toBe(true);
    for (const part of [
      "excess vs IWM",
      "20 sessions",
      "arm-night close entry",
      "median +2.5%",
      "n = 12",
      "held 8 of 12 (67%)",
      `interquartile range ${fmtRet(c.q1)} to ${fmtRet(c.q3)}`,
      "the names' median raw return +27.5%, IWM's +25.0%",
    ]) {
      expect(v.title, part).toContain(part);
    }
  });

  it("prints only the n and 'too small to read' for a thin cell, and its numbers grayed when asked", () => {
    const h5 = HORIZONS[0];
    const thin = cellStats(obsOf(EXCESS_12.slice(0, 4)), "iwm");
    const hidden = cellView(thin, h5, "iwm", "arm_night", false);
    expect([hidden.text, hidden.sub, hidden.gray, hidden.tone]).toEqual(["n 4", "too small to read", true, ""]);
    expect(hidden.title).toContain("n = 4 is under 10: too small to read");
    expect(hidden.title).not.toContain("median +");
    const shown = cellView(thin, h5, "iwm", "arm_night", true);
    expect(shown.gray).toBe(true);
    expect(shown.tone).toBe(""); // no sign tone on a number too thin to read
    expect(shown.text).toMatch(/^[+−]\d/);
    expect(shown.title).toContain("too small to read");
    expect(shown.title).toContain("n = 4");
  });

  it("says 'no matured episodes' for an empty cell and counts the spans a missing benchmark dropped", () => {
    const v = cellView(cellStats([{ ret: 0.1, bench: null }], "iwm"), HORIZONS[2], "iwm", "arm_night", false);
    expect(v.text).toBe("—");
    expect(v.clickable).toBe(false);
    expect(v.title).toContain("no matured episodes");
    expect(v.title).toContain("1 more had no IWM bar");
  });

  it("names the raw reading in the hover under raw", () => {
    const c = cellStats(obsOf(EXCESS_12), "raw");
    const v = cellView(c, HORIZONS[4], "raw", "next_session", false);
    expect(v.title).toContain("raw return");
    expect(v.title).toContain("next-session close entry");
    expect(v.title).toContain("the span to each call's own exit_by");
    expect(v.title).not.toContain("IWM");
    expect(measureWords("raw")).toBe("raw return");
    expect(measureWords("spy")).toBe("excess vs SPY");
  });

  it("words each span status in the episode list", () => {
    expect(spanView(maturing(), "iwm")).toMatchObject({ text: "maturing", gray: true });
    expect(spanView({ ...maturing(), status: "path_ended" }, "iwm")).toMatchObject({ text: "path ended", gray: true });
    expect(spanView({ ...maturing(), status: "none" }, "iwm")).toMatchObject({ text: "—", gray: true });
    expect(spanView(span({ iwm: null }), "iwm")).toMatchObject({ text: "no bar", gray: true });
    const ok = spanView(span(), "iwm");
    expect(ok).toMatchObject({ text: "+6.0%", tone: "pos", gray: false });
    expect(ok.title).toContain("IWM +4.0%");
    expect(spanView(span({ ret: -0.1, iwm: 0.0 }), "raw")).toMatchObject({ text: "−10.0%", tone: "neg" });
    expect(spanView(undefined, "iwm").text).toBe("");
  });

  it("words the cap at the event, approximate, and the reason a cap is unpinned", () => {
    const c = capWords(row());
    expect(c.text).toBe("small ~$576M");
    expect(c.title).toContain("as-traded close of 2026-06-02 ($14.40)");
    expect(c.title).toContain("40,000,000 shares dated 2026-05-12");
    const adr = capWords(row({ cap: cap({ ads_ratio: 5, split_factor: 2 }) }));
    expect(adr.title).toContain("divided by the ADS ratio 5");
    expect(adr.title).toContain("×2");
    const un = capWords(row({ cap: cap({ value: null, tier: null, gap: "ads_unread" }) }));
    expect(un.text).toBe(UNPINNED);
    expect(un.title).toContain("ordinary-shares-per-ADS ratio is unread");
    expect(capWords(row({ cap: null }))).toEqual({ text: "", title: "" });
  });

  it("words the liquidity band and its figure", () => {
    expect(liquidityWords(row({ liquidity: "10m_plus", adv_usd: 31_000_000 }))).toEqual({
      text: "$10M and over",
      title: "20-day average dollar volume before the arm date: $31.0M a day.",
    });
    expect(liquidityWords(row({ liquidity: null })).text).toBe(UNPINNED);
  });

  it("states the work: the as-of, the clock, the edition, what is in the cells and what is left out", () => {
    const lines = workLines(SERVER, "arm_night", "iwm");
    expect(lines[0]).toBe(
      "as of 2026-07-24 · public clock: only what was public by the end of that date · edition 0: the original record (the default; editions are paused) · annotated nights excluded · late nights excluded",
    );
    expect(lines[1]).toBe(
      "20 arm episodes on 2 cards · 17 in the cells · left out: 1 flagged price history, 1 censored start, 1 ingest flagged",
    );
    expect(lines[2]).toBe("1 with an unpinned cap · 1 with unpinned liquidity (counted, never dropped)");
    expect(lines[3]).toBe(
      "by horizon (arm-night close entry): 5 sessions 17 counted · 10 sessions 9 counted, 8 maturing · 20 sessions 16 counted, 1 path ended · 60 sessions 0 counted, 17 maturing · to exit_by 0 counted, 17 maturing",
    );
    expect(lines[4]).toContain("the arm night's close, the Scoreboard's own");
    expect(lines[5]).toBe("Comparison: IWM over the same two dates (the name's return minus its).");
    expect(lines[6]).toBe("Market clock: SPY's bar dates; a name whose bars stop more than 5 days behind it has ended.");
    expect(lines.at(-1)).toBe("computed · 1,234 ms");
  });

  it("names the edition in force, and for the record says why it is the default", () => {
    expect(editionWords(SERVER)).toBe("edition 0: the original record (the default; editions are paused)");
    const stored = payload({ edition: { number: 1, label: "first re-run" } });
    expect(editionWords(stored)).toBe("edition 1: first re-run");
    expect(workLines(stored, "arm_night", "iwm")[0]).toContain("· edition 1: first re-run · annotated");
  });

  it("states the entry rule and the comparison in force, a missing benchmark and a live read", () => {
    const lines = workLines(
      payload({ clock: "record", benchmarks_missing: ["IWM"], market_source: null, annotated_policy: "n/a", late_policy: "n/a", cache: "hit" }),
      "next_session",
      "raw",
    );
    expect(lines[0]).toBe("as of 2026-07-24 · live read · edition 0: the original record (the default; editions are paused)");
    expect(lines[3]).toContain("by horizon (next-session close entry)");
    expect(lines[4]).toContain("the next session's close");
    expect(lines[5]).toBe("Comparison: none shown (the raw return). IWM has no bar at this as-of, so its comparison is blank.");
    expect(lines[6]).toBe("Market clock: no SPY or IWM bar at this as-of, so no path can read ended.");
    expect(lines.at(-1)).toBe("cached read · 1,234 ms");
  });

  it("never says a result works, wins or deserves anything: only what happened, with the numbers", () => {
    const text = [
      ...workLines(SERVER, "arm_night", "iwm"),
      ...LIMITS,
      ...SERVER.rows.flatMap((r) => HORIZONS.map((h) => spanView(r.arm_night?.spans[h.key], "iwm").title)),
      ...tableRows(SERVER.rows, "tier", "arm_night", "iwm").flatMap((t) =>
        HORIZONS.map((h) => cellView(t.cells[h.key], h, "iwm", "arm_night", true).title),
      ),
    ].join("\n");
    expect(text).not.toMatch(/\bworks?\b|\bworked\b|outperform|underperform|\bbeat\b|\bwinn|\bgood\b|\bbad\b|\bbuy\b|\bsell\b|must/i);
  });
});
