import { describe, expect, it } from "vitest";

import {
  dayBefore,
  daysAway,
  fmtDay,
  fmtShort,
  isFiltered,
  keepGroup,
  keepItem,
  NO_FILTERS,
  stateCls,
  type Filters,
} from "../calendar";
import type { CatalystCalendarItemOut, CatalystCalendarUndatedGroupOut } from "../useCatalystCalendar";

// The Catalyst Calendar's pure helpers: the date wording and the filters. The buckets and the order are the
// server's (backend/tests/dashboards/test_catalyst_calendar.py); these only word and filter what is on screen.

const item = (over: Partial<CatalystCalendarItemOut> = {}): CatalystCalendarItemOut => ({
  item: "catalyst",
  on: "2026-11-04",
  days: 6,
  label: "Q3 earnings",
  kind: "earnings",
  when_label: null,
  catalyst_id: "c-1",
  thesis_id: "t-coal",
  thesis_name: "Coal",
  state: "warming",
  ...over,
});

const GROUP: CatalystCalendarUndatedGroupOut = {
  thesis_id: "t-coal",
  thesis_name: "Coal",
  state: null,
  count: 2,
  catalysts: [
    { catalyst_id: "u-1", label: "Phase 2 start", kind: "clinical_readout", when_label: "~Q3" },
    { catalyst_id: "u-2", label: "Index add", kind: null, when_label: null },
  ],
};

const f = (over: Partial<Filters>): Filters => ({ ...NO_FILTERS, ...over });

describe("the date wording", () => {
  it("days away: today, N days, N days ago", () => {
    expect(daysAway(0)).toBe("today");
    expect(daysAway(1)).toBe("1 day");
    expect(daysAway(30)).toBe("30 days");
    expect(daysAway(-1)).toBe("1 day ago");
    expect(daysAway(-58)).toBe("58 days ago");
  });

  it("a date carries its weekday, and its year only when it is not today's year", () => {
    expect(fmtDay("2026-11-01", "2026-10-29")).toBe("Sun, Nov 1");
    expect(fmtDay("2027-01-10", "2026-10-29")).toBe("Sun, Jan 10, 2027");
    expect(fmtShort("2026-11-08", "2026-10-29")).toBe("Nov 8");
    expect(fmtShort("2025-12-31", "2026-10-29")).toBe("Dec 31, 2025");
  });

  it("the day before a date crosses a month and a year", () => {
    expect(dayBefore("2027-01-28")).toBe("2027-01-27");
    expect(dayBefore("2027-01-01")).toBe("2026-12-31");
  });
});

describe("the filters", () => {
  it("no filter keeps everything", () => {
    expect(isFiltered(NO_FILTERS)).toBe(false);
    expect(keepItem(item(), NO_FILTERS)).toBe(true);
    expect(keepGroup(GROUP, NO_FILTERS)).toEqual(GROUP);
  });

  it("a kind narrows catalysts, with no kind its own choice, and never a deadline", () => {
    const earnings = f({ kinds: new Set(["earnings"]) });
    expect(keepItem(item(), earnings)).toBe(true);
    expect(keepItem(item({ kind: "regulatory" }), earnings)).toBe(false);
    expect(keepItem(item({ kind: null }), f({ kinds: new Set([""]) }))).toBe(true);
    expect(keepItem(item({ item: "exit_by", kind: null, catalyst_id: null }), earnings)).toBe(true);
    expect(keepGroup(GROUP, f({ kinds: new Set([""]) }))?.catalysts.map((c) => c.label)).toEqual([
      "Index add",
    ]);
  });

  it("the deadlines toggle drops only the deadlines", () => {
    const off = f({ showDeadlines: false });
    expect(keepItem(item({ item: "entry_window" }), off)).toBe(false);
    expect(keepItem(item({ item: "exit_by" }), off)).toBe(false);
    expect(keepItem(item(), off)).toBe(true);
    expect(isFiltered(off)).toBe(false);
  });

  it("a state keeps a card in that state; a card with no call is the 'none' state", () => {
    expect(keepItem(item(), f({ states: new Set(["warming"]) }))).toBe(true);
    expect(keepItem(item(), f({ states: new Set(["armed"]) }))).toBe(false);
    expect(keepItem(item({ state: null }), f({ states: new Set(["none"]) }))).toBe(true);
    expect(keepGroup(GROUP, f({ states: new Set(["armed"]) }))).toBeNull();
    expect(stateCls(null)).toBe("none");
    expect(stateCls("armed")).toBe("armed");
    expect(stateCls("incubating")).toBe("incub");
  });

  it("the text matches a card name or a label, case-insensitive, trimmed", () => {
    expect(keepItem(item(), f({ text: "  coal " }))).toBe(true);
    expect(keepItem(item(), f({ text: "Q3" }))).toBe(true);
    expect(keepItem(item(), f({ text: "vote" }))).toBe(false);
    // a card-name hit keeps every undated label; a label hit keeps that label, with the count to match
    expect(keepGroup(GROUP, f({ text: "COAL" }))?.count).toBe(2);
    const hit = keepGroup(GROUP, f({ text: "index" }));
    expect([hit?.count, hit?.catalysts.map((c) => c.label)]).toEqual([1, ["Index add"]]);
    expect(keepGroup(GROUP, f({ text: "nothing" }))).toBeNull();
  });
});
