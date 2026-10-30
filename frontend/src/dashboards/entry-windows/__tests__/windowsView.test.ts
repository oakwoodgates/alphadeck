import { describe, expect, it } from "vitest";

import {
  catalystWords,
  fmtDay,
  fmtPct,
  holdWords,
  intoWords,
  kindWords,
  leftWords,
  cardHoldClosing,
  memberEntryLines,
  memberHoldLines,
  memberSinceWords,
  rowWords,
  shownRows,
  sinceCell,
  sortRows,
  tone,
  triggerWords,
  workLine,
} from "../windowsView";
import { CLOSED, CLOSING, MANAGING, NO_CLOCKS, PAIR, counts, member, memberClock, payload, row, since } from "./fixtures";

// The page's words and orders, pure. Neutral wording: where the as-of sits and what the closes did.

describe("formats", () => {
  it("words a date without its year and a return with one decimal and a sign", () => {
    expect(fmtDay("2026-10-28")).toBe("Oct 28");
    expect(fmtDay(null)).toBe("—");
    expect(fmtPct(3.24)).toBe("+3.2%");
    expect(fmtPct(-1.449)).toBe("-1.4%");
    expect(fmtPct(0)).toBe("0.0%");
    expect(fmtPct(-0.04)).toBe("0.0%"); // rounds to zero: no "-0.0%"
    expect(fmtPct(null)).toBe("—");
  });

  it("gives the sign tone from the value as shown", () => {
    expect(tone(2)).toBe("pos");
    expect(tone(-2)).toBe("neg");
    expect(tone(0.04)).toBe("");
    expect(tone(null)).toBe("");
  });
});

describe("the clock cells", () => {
  it("counts sessions into the window", () => {
    expect(intoWords(row({ sessions_into: 5 }))).toBe("5");
    expect(intoWords(row({ sessions_into: null }))).toBe("—");
  });

  it("words the entry window: sessions left while open, closed N sessions ago after, its last day between", () => {
    expect(leftWords(row({ sessions_left: 3 }))).toBe("3 sessions left");
    expect(leftWords(row({ sessions_left: 1 }))).toBe("1 session left");
    expect(leftWords(CLOSED)).toBe("entry window closed 3 sessions ago");
    expect(leftWords(row({ sessions_left: null, closed_ago: 1 }))).toBe("entry window closed 1 session ago");
    expect(leftWords(row({ sessions_left: null, closed_ago: 0 }))).toBe("last day of the entry window");
    expect(leftWords(NO_CLOCKS)).toBe("no entry clock");
  });

  it("words a closing name's own entry window under the card's figure, but not the card's own clock", () => {
    expect(memberEntryLines(PAIR)).toEqual(["BBB · 1 session left · to Oct 28"]);
    // the headline's window IS the card's figure: nothing repeats it
    expect(memberEntryLines(CLOSING)).toEqual([]);
    expect(memberEntryLines(row())).toEqual([]);
    // the same rule as the card's figure: a last day and a closed window
    const clock = (over: Parameters<typeof memberClock>[0]) =>
      row({ member_clocks: [memberClock({ entry_window_closing: true, arm_until: "2026-10-27", ...over })] });
    expect(memberEntryLines(clock({ sessions_left: null, closed_ago: 0 }))).toEqual(["AAA · last day of the entry window · to Oct 27"]);
    expect(memberEntryLines(clock({ arm_until: "2026-10-23", sessions_left: null, closed_ago: 2 }))).toEqual([
      "AAA · entry window closed 2 sessions ago · to Oct 23",
    ]);
    expect(memberEntryLines(clock({ ticker: null, sessions_left: 2 }))).toEqual(["? · 2 sessions left · to Oct 27"]);
    // a name flagged only for its hold clock draws no entry line
    expect(memberEntryLines(row({ member_clocks: [memberClock({ lapsing: true })] }))).toEqual([]);
  });

  it("names a name whose hold clock is closing unless the card's own figure already says so", () => {
    expect(cardHoldClosing(CLOSING)).toBe(true);
    expect(memberHoldLines(CLOSING)).toEqual([]);
    expect(cardHoldClosing(PAIR)).toBe(false);
    expect(memberHoldLines(PAIR)).toEqual(["BBB · hold clock closing"]);
    expect(memberHoldLines(row())).toEqual([]);
    const both = row({
      member_clocks: [memberClock({ headline: true, lapsing: true }), memberClock({ security_id: "s-bbb", ticker: "BBB", lapsing: true })],
    });
    expect(memberHoldLines(both)).toEqual([]); // the card's tag already says it
  });

  it("words the hold horizon: days while running, passed after, no clock without one", () => {
    expect(holdWords(row({ hold_days: 18 }))).toBe("18 days");
    expect(holdWords(row({ hold_days: 1 }))).toBe("1 day");
    expect(holdWords(row({ hold_days: 0 }))).toBe("0 days");
    expect(holdWords(CLOSED)).toBe("hold horizon passed");
    expect(holdWords(NO_CLOCKS)).toBe("no hold clock");
  });
});

describe("sinceCell", () => {
  it("states the median with its n, both benchmarks and how many sit above the arm-night close", () => {
    expect(sinceCell(since())).toEqual({
      main: "+4.5%",
      tone: "pos",
      basis: "median of 2",
      above: "1 of 2 above the arm-night close",
      iwm: "IWM +1.0%",
      spy: "SPY -2.0%",
      unpriced: null,
    });
  });

  it("counts the unpriced names against the full n and never shows a median for none", () => {
    const c = sinceCell(since({ names: 4, priced: 2, flagged: 1, no_bar: 1 }));
    expect(c.unpriced).toBe("1 flagged, 1 without a bar (of 4)");
    const none = sinceCell(since({ names: 1, priced: 0, flagged: 1, above: 0, median: null, iwm: null, spy: null }));
    expect(none.main).toBe("—");
    expect(none.basis).toBe("no priced name");
    expect(none.above).toBeNull();
    expect(none.iwm).toBe("IWM —");
  });
});

describe("the fold", () => {
  it("words a trigger with its kind, grade, label and fire date", () => {
    const t = member().triggers[0];
    expect(triggerWords(t)).toBe("technical breakout · core · 8-day breakout · fired Oct 19");
    expect(triggerWords({ ...t, grade: null, event_date: null })).toBe("technical breakout · 8-day breakout · no fire date stated");
    expect(kindWords("etf_launch")).toBe("etf launch");
  });

  it("words a member's return, a flagged name's reason, or the lack of a bar", () => {
    expect(memberSinceWords(member())).toBe("+4.5% from the Oct 20 close · above its arm-night close");
    expect(memberSinceWords(member({ since: -1, above: false }))).toBe("-1.0% from the Oct 20 close · not above its arm-night close");
    expect(memberSinceWords(member({ flag: "mixed-scale price history", since: null }))).toBe("left out: mixed-scale price history");
    expect(memberSinceWords(member({ since: null, above: null }))).toBe("no bar since its arm night");
  });

  it("words a catalyst with its date and whether it is still ahead", () => {
    expect(catalystWords({ label: "readout", kind: "clinical_readout", when_date: "2026-10-29", past: false })).toBe("Oct 29 · readout · clinical_readout · still ahead");
    expect(catalystWords({ label: "earnings", kind: null, when_date: "2026-10-22", past: true })).toBe("Oct 22 · earnings");
  });

  it("words the readout for a row with both clocks and for one with none", () => {
    expect(rowWords(row())).toBe("Card A: Armed since Oct 20 · 5 sessions in · entry window to Oct 30: 3 sessions left · hold horizon to Nov 14: 18 days");
    expect(rowWords(NO_CLOCKS)).toBe("Card D: Armed since Oct 20 · 5 sessions in · no entry clock · no hold clock");
    expect(rowWords(MANAGING)).toContain("Managing; armed since Oct 15");
    expect(rowWords(CLOSED)).toContain("(a reconstructed night)");
  });

  it("never says early, late or chase", () => {
    const text = [rowWords(row()), rowWords(CLOSED), leftWords(CLOSED), workLine(payload(), false)].join(" ");
    expect(text).not.toMatch(/\b(early|late|chase)\b/i);
  });
});

describe("sortRows", () => {
  const rows = [row(), CLOSING, CLOSED, NO_CLOCKS, MANAGING];
  const names = (key: Parameters<typeof sortRows>[1]) => sortRows(rows, key).map((r) => r.thesis_name);

  it("sorts by sessions left: open windows soonest first, then closed ones most recently closed first, then no clock", () => {
    const more = [row({ thesis_id: "t-x", thesis_name: "Card X", sessions_left: null, closed_ago: 1, arm_until: "2026-10-26" }), ...rows];
    expect(sortRows(more, "left").map((r) => r.thesis_name)).toEqual(["Card B", "Card A", "Card X", "Card C", "Card D", "Card M"]);
  });

  it("sorts by arm night, newest first", () => {
    expect(names("armed")).toEqual(["Card B", "Card A", "Card D", "Card M", "Card C"]);
  });

  it("sorts by days to exit_by: running soonest first, passed ones most recent first, no clock last", () => {
    const soon = row({ thesis_id: "t-s", thesis_name: "Card S", hold_days: 4 });
    expect(sortRows([...rows, soon], "hold").map((r) => r.thesis_name)).toEqual(["Card S", "Card A", "Card B", "Card M", "Card C", "Card D"]);
  });

  it("sorts by the median since the arm night, largest first, rows with no median last", () => {
    const up = row({ thesis_id: "t-u", thesis_name: "Card U", since: since({ median: 9 }) });
    const down = row({ thesis_id: "t-v", thesis_name: "Card V", since: since({ median: -3 }) });
    expect(sortRows([down, CLOSED, up, row()], "since").map((r) => r.thesis_name)).toEqual(["Card U", "Card A", "Card V", "Card C"]);
  });

  it("is a stable copy that ends every order in the card's name", () => {
    const tie = [row({ thesis_id: "t-2", thesis_name: "Zed" }), row({ thesis_id: "t-1", thesis_name: "Alpha" })];
    expect(sortRows(tie, "left").map((r) => r.thesis_name)).toEqual(["Alpha", "Zed"]);
    expect(tie[0].thesis_name).toBe("Zed"); // the input is untouched
  });
});

describe("shownRows", () => {
  it("hides the Managing rows until asked", () => {
    const rows = [row(), MANAGING];
    expect(shownRows(rows, false).map((r) => r.thesis_name)).toEqual(["Card A"]);
    expect(shownRows(rows, true)).toHaveLength(2);
  });
});

describe("workLine", () => {
  it("shows the work: the as-of and clock, the card counts, the names, the calendar, the entry rule and the cache", () => {
    const line = workLine(payload(), false);
    expect(line).toContain("As of Oct 27: what was public by the end of that date");
    expect(line).toContain("8 cards counted (1 created after the as-of left out)");
    expect(line).toContain("4 Armed rows, 1 Managing hidden, 2 Warming, 0 Incubating, 1 no call yet");
    expect(line).toContain("5 armed names: 4 priced, 1 unpriced");
    expect(line).toContain("1 arm night reconstructed (reported, never scored)");
    expect(line).toContain("sessions counted on IWM's bars to Oct 27");
    expect(line).toContain("entry rule: the first close on or after the arm night, as traded");
    expect(line).toContain("computed in 1.2 s");
  });

  it("says Managing is shown when the toggle is on, and the live read for a record clock, and a cache hit", () => {
    const line = workLine(payload({ clock: "record", cache: "hit" }), true);
    expect(line).toContain("1 Managing shown");
    expect(line).toContain("the live read");
    expect(line).toContain("cached");
  });

  it("counts a Managing card with no Armed night and omits the reconstructed clause when there are none", () => {
    const line = workLine(payload({ counts: counts({ managing_without_arm_night: 2, reconstructed_arm_nights: 0 }) }), false);
    expect(line).toContain("2 Managing with no Armed night recorded (no window to draw)");
    expect(line).not.toContain("reconstructed");
  });

  it("names the calendar when IWM has no bars", () => {
    expect(workLine(payload({ session_source: "companies" }), false)).toContain("the armed names' bars (IWM has none)");
    expect(workLine(payload({ session_source: "none", calendar_last: null }), false)).toContain("sessions counted on no bars");
  });
});
