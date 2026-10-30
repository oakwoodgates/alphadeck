import { describe, expect, it } from "vitest";

import {
  GROUP_FORMS,
  GROUP_LABEL,
  NOT_COUNTED,
  filersWords,
  filterCards,
  firstWords,
  fmtDay,
  fmtMonth,
  groupWords,
  monthReadout,
  overlayWords,
  plural,
  runWords,
  windowWords,
  workLine,
} from "../adoptionView";
import { AXIS, CARDS, MONTHS, RUN, TERMS, absent, counted, counts, overlay, payload, term } from "./fixtures";

// The page's pure words: neutral (what the data shows, never a verdict), every number off the payload.

const SEP = MONTHS[AXIS.indexOf("2026-09-01")];
const JUN = MONTHS[AXIS.indexOf("2026-06-01")];
const AUG = MONTHS[AXIS.indexOf("2026-08-01")];
const data = payload();

describe("dates and counts", () => {
  it("words a day with its year and a month without a day, from a first day or a 'YYYY-MM' prefix", () => {
    expect(fmtDay("2026-10-24")).toBe("Oct 24, 2026");
    expect(fmtMonth("2026-09-01")).toBe("Sep 2026");
    expect(fmtMonth("2026-09")).toBe("Sep 2026");
    expect([fmtDay(null), fmtMonth(undefined), fmtMonth("")]).toEqual(["—", "—", "—"]);
  });

  it("pluralizes and groups thousands", () => {
    expect([plural(1, "term", "terms"), plural(0, "term", "terms"), plural(1492, "request", "requests")]).toEqual([
      "1 term",
      "0 terms",
      "1,492 requests",
    ]);
  });

  it("filters the picker by name in any case and keeps the server's order", () => {
    expect(filterCards(CARDS.cards, "  CARD b").map((c) => c.name)).toEqual(["Card B"]);
    expect(filterCards(CARDS.cards, "").map((c) => c.name)).toEqual(["Card A", "Card B", "Card C"]);
    expect(filterCards(CARDS.cards, "zzz")).toEqual([]);
  });
});

describe("the form groups", () => {
  it("names the four groups and what each holds", () => {
    expect(GROUP_LABEL).toEqual({ reports: "Reports", events: "8-Ks", issuance: "Issuance", other: "Other" });
    expect(GROUP_FORMS.issuance).toContain("424B");
    expect(groupWords(SEP)).toBe("Reports 30 · 8-Ks 8 · Issuance 12 · Other 4");
  });
});

describe("the month readout", () => {
  it("words a counted month: documents by group, the top terms, the filers and what limits them", () => {
    const lines = monthReadout(SEP, data);
    expect(lines[0]).toBe("Sep 2026: 54 documents (Reports 30 · 8-Ks 8 · Issuance 12 · Other 4) · summed over 2 terms");
    expect(lines[1]).toBe("most documents: alpha 40 · beta 14");
    expect(lines[2]).toBe("filers: at least 20 (a term showed only part of its filers) · 6 new");
    expect(lines[3]).toBe(
      "only 2 of 3 terms stored for this month: the sums are a floor · counted before the month closed: partial until the next weekly run",
    );
    expect(lines[4]).toMatch(/^the tape on Sep 2[0-9], 2026 · card line [\d.]+ · SPY [\d.]+ · both 100 on Sep 29, 2023$/);
  });

  it("says a first month has no earlier month to compare with, and reads exact filers plainly", () => {
    const lines = monthReadout(JUN, payload({ counts: counts({ signal_terms: 2 }) }));
    expect(lines[2]).toBe("filers: 5 · no earlier month to compare with");
    expect(lines.join(" ")).not.toContain("floor");
    expect(lines.join(" ")).not.toContain("before the month closed");
  });

  it("words a stored zero as a zero and an absent month as absent, never zero", () => {
    const zero = monthReadout(MONTHS[AXIS.indexOf("2026-07-01")], data);
    expect(zero[0]).toContain("0 documents");
    expect(zero[1]).toBeDefined();
    const gap = monthReadout(AUG, data);
    expect(gap[0]).toBe("Aug 2026: no count stored for this month (a failed or deferred month is left blank, never shown as zero)");
    expect(gap[0]).not.toMatch(/\b0 documents\b/);
  });

  it("words the as-of's own month as not closed yet", () => {
    const open = absent("2026-10-01", { closed: false });
    expect(monthReadout(open, data)[0]).toBe("Oct 2026: the month had not closed at the as-of, so it is not counted yet");
  });

  it("reads the tape at a month's last session, capped by the as-of, or says there is none", () => {
    const ov = overlay({ dates: ["2026-08-28", "2026-09-04", "2026-09-30"], card: [100, 104, 108], spy: [100, 101, 102] });
    expect(overlayWords(ov, "2026-09-01", "2026-09-30")).toBe(
      "the tape on Sep 30, 2026 · card line 108.0 · SPY 102.0 · both 100 on Aug 28, 2026",
    );
    expect(overlayWords(ov, "2026-08-01", "2026-09-30")).toContain("the tape on Aug 28, 2026 · card line 100.0");
    expect(overlayWords(ov, "2026-09-01", "2026-09-10")).toContain("the tape on Sep 4, 2026");
    expect(overlayWords(ov, "2026-07-01", "2026-09-30")).toBe("the tape: no session yet (the lines start Aug 28, 2026)");
    expect(overlayWords(overlay({ dates: [], card: null, spy: null }), "2026-09-01", "2026-09-30")).toBe("the tape: no line to read");
    expect(overlayWords(overlay({ card: null }), "2026-09-01", "2026-09-30")).toContain("no card line");
    expect(overlayWords(overlay({ spy: null }), "2026-09-01", "2026-09-30")).toContain("no SPY line");
  });
});

describe("the term table cells", () => {
  it("shows a window's sum with how many of its months are stored, and a dash for none", () => {
    expect(windowWords(49, 2, 3)).toBe("49 (2 of 3 months)");
    expect(windowWords(1234, 12, 12)).toBe("1,234");
    expect(windowWords(0, 3, 3)).toBe("0"); // a stored zero is a zero
    expect(windowWords(null, 0, 12)).toBe("—");
  });

  it("marks filers 'at least', and the first month 'or earlier' when it is the earliest stored month", () => {
    expect(filersWords({ filers: 15, filers_sampled: true })).toBe("at least 15");
    expect(filersWords({ filers: 6, filers_sampled: false })).toBe("6");
    expect(filersWords({ filers: null, filers_sampled: false })).toBe("—");
    expect(firstWords({ first_month: "2026-06-01", first_at_start: true })).toBe("Jun 2026 or earlier");
    expect(firstWords({ first_month: "2026-07-01", first_at_start: false })).toBe("Jul 2026");
    expect(firstWords({ first_month: null, first_at_start: false })).toBe("no document in the stored months");
    expect(NOT_COUNTED).toBe("not counted yet");
  });
});

describe("the job's run and the quiet line", () => {
  it("words the newest run with this card's share beside it", () => {
    expect(runWords(RUN)).toBe(
      "last count run Oct 24, 2026 (weekly, finished): Sep 2026 to Oct 2026, 1,492 requests · this card: 1 failed request, 7 deferred months, left blank",
    );
    expect(runWords({ ...RUN, card_failed_requests: 0, card_deferred_months: 0, months_from: "2026-10", months_to: "2026-10" })).toContain(
      "this card: nothing failed or deferred",
    );
    expect(runWords(null)).toBe("no run of the weekly count on record");
  });

  it("shows the work: terms, months stored, the run, the tape and the cache", () => {
    const line = workLine(data);
    expect(line).toContain("Card A as of Sep 30, 2026");
    expect(line).toContain("public clock");
    expect(line).toContain("2 SIGNAL terms counted of 3, 1 not counted yet");
    expect(line).toContain("4 BROAD terms left out");
    expect(line).toContain("1 stored term no longer in the card's term set, ignored");
    expect(line).toContain("months stored 3 of 36, 0 with every term");
    expect(line).toContain('filers read "at least" in 1 month');
    expect(line).toContain("1 month counted before they closed");
    expect(line).toContain("last count run Oct 24, 2026");
    expect(line).toContain("the tape: 2 of 3 names priced, 1 left out for a flagged price history, 1 Context or Bench row left out, equal weight, rebalanced daily");
    expect(line).toContain("computed in 4.2 s");
    expect(workLine(payload({ cache: "hit", clock: "record" }))).toMatch(/live read.*cached$/);
  });

  it("leaves out what is zero and says when the sessions came from the names' bars", () => {
    const line = workLine(
      payload({
        counts: counts({ terms_not_counted: 0, retired_terms: 0, months_sampled: 0, months_read_early: 0, months_counted: 0 }),
        overlay: overlay({ flagged: [], silent_left_out: 0, session_source: "companies" }),
        last_run: null,
      }),
    );
    expect(line).not.toContain("not counted yet");
    expect(line).not.toContain("no longer in the card's term set");
    expect(line).not.toContain("at least");
    expect(line).toContain("months stored 0 of 36");
    expect(line).not.toContain("with every term");
    expect(line).toContain("no run of the weekly count on record");
    expect(line).toContain("sessions from the names' bars (no SPY tape)");
    expect(line).not.toContain("left out for a flagged");
  });
});

describe("neutral wording (rule 3: what the data shows, never a verdict)", () => {
  it("no string the page builds says early or crowded", () => {
    const strings = [
      ...MONTHS.flatMap((m) => monthReadout(m, data)),
      ...monthReadout(absent("2026-10-01", { closed: false }), data),
      ...TERMS.flatMap((t) => [filersWords(t), firstWords(t), windowWords(t.last_3_documents, t.months_last_3, 3)]),
      ...Object.values(GROUP_LABEL),
      ...Object.values(GROUP_FORMS),
      workLine(data),
      runWords(RUN),
      NOT_COUNTED,
      groupWords(SEP),
      firstWords(term("x", { first_month: "2026-06-01", first_at_start: true })),
    ];
    for (const s of strings) expect(s, s).not.toMatch(/\b(early|crowded)\b/i);
  });
});
