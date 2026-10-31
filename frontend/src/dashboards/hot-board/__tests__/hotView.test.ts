import { describe, expect, it } from "vitest";

import { heatByKey } from "../heat";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  DEFAULT_TOGGLES,
  armNightCell,
  capCell,
  exitCell,
  filterControls,
  filtersActive,
  fmtPct,
  fmtPp,
  fmtUsd,
  heatText,
  heatTitle,
  insiderCell,
  keysWords,
  leadingMark,
  leftCell,
  nextSort,
  noReadingLine,
  riskCell,
  rowMatches,
  readLine,
  rowsOnShow,
  signTone,
  sinceArmCells,
  sortRows,
  sparkPath,
  strengthWords,
  tapeCells,
  toggleCounts,
  triggerLine,
  verdictWords,
  workLines,
} from "../hotView";
import { ASOF, CARD_B, MIXED, armedRead, cap, cell, clocks, countsOf, payload, row, sample, since, tape, trigger } from "./fixtures";

// The Hot Board's pure view helpers: the toggles and filters, the sorts (a blank last in either direction), and the
// words and numbers of every cell. Every figure is the server's; these only pick, order and word them.

describe("formatting", () => {
  it("signs a figure, shows a blank as a dash", () => {
    expect(fmtPct(3.04)).toBe("+3.0%");
    expect(fmtPct(-7.15)).toBe("-7.2%");
    expect(fmtPct(0)).toBe("0.0%");
    expect(fmtPp(2)).toBe("+2.0 pp");
    expect(fmtPct(null)).toBe("—");
    expect(fmtUsd(1_200_000_000)).toBe("$1.20B");
    expect(fmtUsd(null)).toBe("—");
    expect([signTone(1), signTone(-1), signTone(0), signTone(null)]).toEqual(["pos", "neg", "", ""]);
  });
});

describe("which rows are shown", () => {
  const s = sample();
  const rows = Object.values(s);

  it("shows the armed rows by default and counts what each toggle adds", () => {
    expect(rowsOnShow(rows, DEFAULT_TOGGLES).map((r) => r.ticker)).toEqual(["AAA", "BBB", "CCC", "FLG"]);
    expect(toggleCounts(countsOf(rows))).toEqual({ warming: 1, watch: 1, managing: 1 });
  });

  it("adds a kind when its toggle is on", () => {
    expect(rowsOnShow(rows, { ...DEFAULT_TOGGLES, warming: true }).map((r) => r.ticker)).toEqual(["AAA", "BBB", "CCC", "FLG", "WRM"]);
    expect(rowsOnShow(rows, { warming: true, watch: true, managing: true }).map((r) => r.ticker)).toHaveLength(7);
  });
});

describe("the filters", () => {
  const s = sample();
  const rows = rowsOnShow(Object.values(s), DEFAULT_TOGGLES);

  it("passes everything by default", () => {
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    expect(rows.every((r) => rowMatches(r, DEFAULT_FILTERS))).toBe(true);
  });

  it("filters by grade, trigger kind, card, cap tier and an open entry window", () => {
    const pick = (f: Partial<typeof DEFAULT_FILTERS>) => rows.filter((r) => rowMatches(r, { ...DEFAULT_FILTERS, ...f })).map((r) => r.ticker);
    expect(pick({ grades: ["flip"] })).toEqual(["BBB"]);
    expect(pick({ triggerKinds: ["technical_breakout"] })).toEqual(["BBB"]);
    expect(pick({ card: CARD_B })).toEqual(["BBB", "CCC"]);
    expect(pick({ tiers: ["large"] })).toEqual(["CCC"]);
    expect(pick({ tiers: ["none"] })).toEqual(["FLG"]);
    // AAA and FLG have 2 sessions left; BBB's window closed; CCC has no entry clock
    expect(pick({ openOnly: true })).toEqual(["AAA", "FLG"]);
    expect(filtersActive({ ...DEFAULT_FILTERS, openOnly: true })).toBe(true);
  });

  it("offers only the controls that can discriminate", () => {
    const all = filterControls(rows);
    expect(all.grades).toEqual(["flip", "core"]);
    expect(all.cards.map((c) => c.name)).toEqual(["Card A", "Card B"]);
    expect(all.tiers).toEqual(["small", "large", "none"]);
    expect(all.openOnly).toBe(true);
    // one grade, one card, one trigger kind, one tier and every window open: nothing to choose, nothing renders
    const same = [row(), row()];
    expect(filterControls(same)).toEqual({ grades: [], triggerKinds: [], cards: [], tiers: [], openOnly: false });
  });
});

describe("the sorts", () => {
  const rows = rowsOnShow(Object.values(sample()), { warming: true, watch: true, managing: true });
  const heats = heatByKey(rows);
  const order = (key: Parameters<typeof nextSort>[1], dir: "asc" | "desc") => sortRows(rows, { key, dir }, heats).map((r) => r.ticker);

  it("starts on heat, descending, with an unscored row last", () => {
    expect(DEFAULT_SORT).toEqual({ key: "heat", dir: "desc" });
    const got = sortRows(rows, DEFAULT_SORT, heats);
    const scored = got.filter((r) => heats.get(r.key)?.score != null);
    expect(got.slice(0, scored.length)).toEqual(scored);
    const scores = scored.map((r) => heats.get(r.key)?.score as number);
    expect(scores).toEqual([...scores].sort((a, b) => b - a));
    // the flagged, warming and watch rows have no heat: they sort after every scored row
    expect(got.slice(scored.length).map((r) => r.ticker).sort()).toEqual(["FLG", "WRM", "WTC"]);
  });

  it("puts a blank last in either direction", () => {
    expect(order("rvol", "desc").at(-1)).toBe("FLG");
    expect(order("rvol", "asc").at(-1)).toBe("FLG");
    expect(order("cap", "asc").slice(-1)).toEqual(["FLG"]);
    expect(order("ret", "desc").slice(-3).sort()).toEqual(["FLG", "WRM", "WTC"]);
  });

  it("sorts text A to Z and a figure biggest first on a column's first click", () => {
    expect(nextSort(DEFAULT_SORT, "ticker")).toEqual({ key: "ticker", dir: "asc" });
    expect(nextSort(DEFAULT_SORT, "rvol")).toEqual({ key: "rvol", dir: "desc" });
    expect(nextSort({ key: "rvol", dir: "desc" }, "rvol")).toEqual({ key: "rvol", dir: "asc" });
    expect(order("ticker", "asc")).toEqual(["AAA", "BBB", "CCC", "FLG", "MNG", "WRM", "WTC"]);
    // WRM and WTC tie on 1.5: the tie goes by ticker
    expect(order("rvol", "desc").slice(0, 3)).toEqual(["AAA", "MNG", "WRM"]);
  });

  it("sorts an entry window by sessions left, a closed one below every open one", () => {
    const left = order("left", "desc");
    // AAA / FLG / MNG-less: open windows first (2 left), BBB closed 2 ago, then the blanks
    expect(left.indexOf("AAA")).toBeLessThan(left.indexOf("BBB"));
    expect(left.indexOf("BBB")).toBeLessThan(left.indexOf("CCC"));
  });

  it("breaks a tie by ticker", () => {
    const a = row({ ticker: "ZZZ", tape: tape({ rvol20: cell(1) }) });
    const b = row({ ticker: "AAA", tape: tape({ rvol20: cell(1) }) });
    expect(sortRows([a, b], { key: "rvol", dir: "desc" }, new Map()).map((r) => r.ticker)).toEqual(["AAA", "ZZZ"]);
  });
});

describe("cells", () => {
  const s = sample();

  it("words the verdict, and says which key a Warming name's trigger turned", () => {
    expect(verdictWords(s.aaa)).toBe("CORE entry");
    expect(verdictWords(s.bbb)).toBe("FLIP only");
    expect(verdictWords(s.warming)).toBe("conviction key");
    expect(verdictWords({ ...s.warming, keys: ["conviction", "confirmation"] })).toBe("conviction + confirmation key");
    expect(verdictWords(s.watch)).toBe("confirmation only");
    expect(verdictWords(s.managing)).toBe("Managing");
    expect(strengthWords(s.aaa)).toBe("0.62");
    expect(strengthWords(s.warming)).toBe("—");
  });

  it("lists the keys compactly and each trigger's label, kind, grade and fire date", () => {
    expect(keysWords(s.aaa)).toBe("insider");
    expect(keysWords(row({ triggers: [trigger(), trigger({ kind: "technical_breakout" }), trigger()] }))).toBe("insider · breakout");
    expect(keysWords(row({ triggers: [] }))).toBe("—");
    expect(triggerLine(trigger())).toBe("Insider cluster buy · insider · core · fired 2026-10-20");
    expect(triggerLine(trigger({ grade: null, event_date: null }))).toBe("Insider cluster buy · insider · no grade · fired no date stated");
  });

  it("marks the arm night and a reconstructed one", () => {
    expect(armNightCell(s.aaa).text).toBe("Oct 28");
    expect(armNightCell(row({ clocks: clocks({ arm_night: "2026-10-20", arm_reconstructed: true }) })).text).toBe("Oct 20 R");
    expect(armNightCell(s.warming).text).toBe("—");
  });

  it("puts the page's one accent on an entry window with at most two sessions left", () => {
    expect(leftCell(s.aaa)).toMatchObject({ text: "2", accent: true });
    expect(leftCell(row({ clocks: clocks({ sessions_left: 5 }) }))).toMatchObject({ text: "5", accent: false });
    expect(leftCell(s.bbb)).toMatchObject({ text: "closed 2 ago", gray: true });
    expect(leftCell(s.ccc)).toMatchObject({ text: "—", gray: true });
    // F3: a window ending on the as-of has no session left: the server sends it closed (0 since), not "0 left"
    const none = leftCell(row({ clocks: clocks({ sessions_left: null, closed_ago: 0, arm_until: ASOF }) }));
    expect(none).toMatchObject({ text: "closed", gray: true });
    expect(none.title).toContain("No session of the entry window");
  });

  it("words the days to the hold horizon and a horizon that passed", () => {
    expect(exitCell(s.aaa).text).toBe("34d");
    expect(exitCell(row({ clocks: clocks({ days_to_exit: -8 }) })).text).toBe("passed 8d");
    expect(exitCell(s.ccc).text).toBe("—");
  });

  it("reads a horizon on the origin day as 'today', and carries the date on a past view (F2)", () => {
    const ends = (days: number) => row({ clocks: clocks({ exit_by: "2026-12-01", days_to_exit: days }) });
    expect(exitCell(ends(0)).text).toBe("today"); // the server counts from the action day on a live view
    expect(exitCell(ends(-1)).text).toBe("passed 1d");
    expect(exitCell(ends(0), true).text).toBe("today · Dec 1");
    expect(exitCell(ends(34), true).text).toBe("34d · Dec 1");
    expect(exitCell(ends(-8), true).text).toBe("passed 8d · Dec 1");
    expect(exitCell(s.ccc, true).text).toBe("—"); // no hold clock: nothing to date
  });

  it("shows the return since the arm night with its sign, IWM's beside it, and why one is blank", () => {
    expect(sinceArmCells(s.aaa).ret).toMatchObject({ text: "0.0%", tone: "" });
    expect(sinceArmCells(s.bbb).ret).toMatchObject({ text: "-4.5%", tone: "neg" });
    expect(sinceArmCells(s.ccc)).toMatchObject({ ret: { text: "+22.5%", tone: "pos" }, iwm: { text: "+1.0%" } });
    expect(sinceArmCells(s.flagged).ret.title).toBe(`flagged: ${MIXED}`);
    expect(sinceArmCells(s.warming).ret.title).toBe("Not an armed row: no arm night");
    const noBar = row({ since_arm: since({ ret_pct: null, iwm_pct: null, gap: "no_bar" }) });
    expect(sinceArmCells(noBar).ret.title).toBe("No close on or after the arm night");
  });

  it("reads the tape off the row, and a flagged price history grays every price cell with its reason", () => {
    const t = tapeCells(s.aaa);
    expect(t.rvol).toMatchObject({ text: "3.1×" });
    expect(t.vs50).toMatchObject({ text: "+4.2%", tone: "pos" });
    expect(t.offHigh).toMatchObject({ text: "-3.1%", tone: "neg" });
    expect(t.vsIwm).toMatchObject({ text: "+8.0 pp", tone: "pos" });
    expect(t.adv.text).toBe("$2.5M");
    const f = tapeCells(s.flagged);
    for (const c of Object.values(f)) expect(c).toMatchObject({ text: "flagged", gray: true, title: `flagged: ${MIXED}` });
    const noReading = tapeCells(row({ tape: tape({ rvol20: cell(null, "n/a: 12/21 bars") }) }));
    expect(noReading.rvol).toMatchObject({ text: "—", title: "n/a: 12/21 bars", gray: true });
  });

  it("shows insider buys as buys/buyers, a stated zero and a blank apart", () => {
    expect(insiderCell(s.aaa).text).toBe("2/2");
    expect(insiderCell(s.bbb).text).toBe("0/0");
    expect(insiderCell(s.ccc)).toMatchObject({ text: "—", gray: true });
  });

  it("marks relative strength only when it is at a 13-week high", () => {
    expect(leadingMark(s.aaa)).toBe("Relative strength is at a 13-week high against IWM");
    expect(leadingMark(s.bbb)).toBeNull();
    expect(leadingMark(row({ tape: tape({ rs_leading: null }) }))).toBeNull();
  });

  it("words the approximate cap with its tier, and why a cap is blank", () => {
    expect(capCell(s.bbb).text).toBe("$1.20B small");
    expect(capCell(s.bbb).title).toContain("Approximate");
    expect(capCell(s.flagged)).toMatchObject({ text: "—", gray: true });
    expect(capCell(s.flagged).title).toContain("flagged");
    expect(capCell(row({ cap: cap(null, null, { gap: "no_shares" }) })).title).toBe("Approximate cap: awaiting shares");
  });

  it("counts a risk signal and names the kinds, and a name with none is a quiet dash", () => {
    const r = riskCell(row({ risk_kinds: ["breakdown", "dilution_risk", "breakdown"] }));
    expect(r.text).toBe("3");
    expect(r.title).toBe("3 risk signals on the card name this security: breakdown, dilution risk");
    expect(riskCell(row({ risk_kinds: [] }))).toMatchObject({ text: "—", gray: true });
  });
});

describe("heat in words", () => {
  it("shows 0 to 100, or a dash with the reason", () => {
    expect(heatText({ fresh: 1, loud: 0.4, leading: 0.7, unextended: 0.8, score: 72.5 })).toBe("73");
    expect(heatText({ fresh: 1, loud: null, leading: 0.7, unextended: 0.8, score: null })).toBe("—");
    expect(heatText(undefined)).toBe("—");
    expect(heatTitle({ fresh: 0.9, loud: 0.4, leading: 0.7, unextended: 0.8, score: 70 })).toBe(
      "fresh 0.90 · loud 0.40 · leading 0.70 · unextended 0.80",
    );
    expect(heatTitle({ fresh: 0.9, loud: null, leading: 0.7, unextended: 0.8, score: null })).toContain("No heat");
  });
});

describe("the sparkline", () => {
  it("draws a path and breaks it on a blank slot", () => {
    expect(sparkPath([1, 2, 3], 72, 16)).toBe("M0.0 15.0L36.0 8.0L72.0 1.0");
    const broken = sparkPath([1, null, 3, 4], 72, 16) as string;
    expect(broken.match(/M/g)).toHaveLength(2);
  });
  it("draws nothing for fewer than two closes", () => {
    expect(sparkPath([null, null, 5], 72, 16)).toBeNull();
    expect(sparkPath([], 72, 16)).toBeNull();
  });
  it("is flat, not NaN, when every close is equal", () => {
    expect(sparkPath([4, 4, 4], 72, 16)).not.toContain("NaN");
  });
});

describe("the work lines", () => {
  const s = sample();
  const rows = Object.values(s);

  it("counts the blanks per column and leaves a flagged row out of them", () => {
    expect(noReadingLine([s.aaa, s.bbb])).toBeNull();
    expect(noReadingLine([s.aaa, s.ccc])).toContain("insider 1");
    expect(noReadingLine([s.flagged])).toBeNull();
  });

  it("states the as-of, the clock, the cards by state, the rows, the flagged and the calendar", () => {
    const shown = rowsOnShow(rows, DEFAULT_TOGGLES);
    const lines = workLines(payload(rows), shown, toggleCounts(countsOf(rows)), DEFAULT_TOGGLES, 3);
    const text = lines.join("\n");
    expect(text).toContain(`as of ${ASOF}, read on the public clock`);
    expect(text).toContain("4 cards counted: 2 armed · 1 warming · 1 managing · 0 incubating · 0 no call yet");
    expect(text).toContain("4 rows shown; 3 with a heat, 1 without");
    expect(text).toContain("1 row with a flagged price history");
    expect(text).toContain("sessions are IWM's bars");
    expect(text).toContain("entry rule: the first close on or after the arm night");
    expect(text).toContain("computed · compute 1,234 ms");
  });

  it("says what the toggles add and counts the rebuilt arm nights", () => {
    const early = row({ since_arm: since({ ret_pct: null, iwm_pct: null, gap: "before_window" }) });
    const withRecon = [row({ clocks: clocks({ arm_reconstructed: true }) }), early, ...rows];
    const lines = workLines(
      payload(withRecon, { cache: "hit", roster_fallback_cards: 2, no_ticker_rows: 1 }),
      rowsOnShow(withRecon, { warming: true, watch: false, managing: false }),
      toggleCounts(countsOf(withRecon)),
      { warming: true, watch: false, managing: false },
      2,
    ).join("\n");
    expect(lines).toContain("(the armed names plus 1 warming)");
    expect(lines).toContain("1 arm night rebuilt by the backfill (R)");
    expect(lines).toContain("1 row with an arm night before the earliest price the view reads");
    expect(lines).toContain("2 cards with no roster snapshot by then");
    expect(lines).toContain("1 row whose security has no ticker");
    expect(lines).toContain("served from the cache");
  });

  it("says which read is loaded: the armed names alone with the others waiting behind their toggles, or every row", () => {
    const full = payload(rows);
    const armed = armedRead(full);
    const counts = toggleCounts(armed.candidate_counts);
    expect(readLine(armed, counts)).toBe(
      "read loaded: the armed names alone; 1 warming · 1 watch · 1 managing wait behind their toggles and load the first time one is used",
    );
    expect(readLine(full, toggleCounts(full.candidate_counts))).toBe(
      "read loaded: every candidate row (4 armed · 1 warming · 1 watch · 1 managing)",
    );
    // nothing waiting behind a toggle: it says so rather than promising rows
    const bare = armedRead(payload(rows.filter((r) => r.kind === "armed")));
    expect(readLine(bare, toggleCounts(bare.candidate_counts))).toBe(
      "read loaded: the armed names; no warming, watch or managing names are listed on the recorded cards",
    );
  });

  it("does not claim a checked toggle's rows are shown until the full read is loaded", () => {
    const armed = armedRead(payload(rows));
    const counts = toggleCounts(armed.candidate_counts);
    const on = { warming: true, watch: true, managing: true };
    const text = workLines(armed, rowsOnShow(armed.rows, on), counts, on, 3).join("\n");
    expect(text).toContain("4 rows shown;");
    expect(text).not.toContain("the armed names plus");
    expect(text).toContain("read loaded: the armed names alone");
  });
});

