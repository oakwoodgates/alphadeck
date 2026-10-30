import { describe, expect, it } from "vitest";

import { todayISO } from "../../../util/format";
import {
  SECTIONS,
  afterNightNote,
  asofParam,
  buyerWords,
  clockWords,
  dayWord,
  dueWhat,
  dueWhen,
  filedWords,
  fillWords,
  formingWords,
  headlineWords,
  holdWords,
  insiderEmpty,
  killTitle,
  killWords,
  lastSessionFig,
  lineWords,
  loggedFillWords,
  nameKey,
  nightsWords,
  passedWords,
  plural,
  riskWords,
  sectionHref,
  signClass,
  signed,
  sinceArmFig,
  sinceFillFig,
  sizeWords,
  tapeNote,
  tapeRows,
  toneClass,
  triggerWords,
  workLines,
} from "../digestView";
import { ASOF, PREV, SEC_A, TAPE_ROWS, buy, card, due, emptyPayload, line, payload, position, tapeRow } from "./fixtures";

// The Daily Digest's pure view: the section list and its links, the words of every line and the tape's cut. Every rule
// (the grouping, the tone, the cut, the returns) is the server's; this only words and selects what the payload carries.

describe("sections and links", () => {
  it("lists the six sections in the plan's order with the full pages their headers open", () => {
    expect(SECTIONS.map((s) => [s.key, s.links.map((l) => l.slug)])).toEqual([
      ["last_night", ["what-changed"]],
      ["positions", []],
      ["due", ["catalyst-calendar"]],
      ["insider", ["insider-tracker"]],
      ["tape", ["hot-board", "entry-windows"]],
      ["forming", ["forming-quietly"]],
    ]);
  });

  it("carries ?asof= for a day other than today and keeps a live visit's URL clean", () => {
    expect(sectionHref("what-changed", "2026-09-17")).toBe("/dashboards/what-changed?asof=2026-09-17");
    expect(asofParam(todayISO())).toBeNull();
    expect(sectionHref("hot-board", todayISO())).toBe("/dashboards/hot-board");
  });

  it("words a day with its weekday so a Monday visibly reads Friday", () => {
    expect(dayWord("2026-09-18")).toBe("Fri, Sep 18");
    expect(dayWord(null)).toBe("—");
    expect(plural(1, "card", "cards")).toBe("1 card");
    expect(plural(1234, "card", "cards")).toBe("1,234 cards");
  });
});

describe("the night pair", () => {
  const n = { asof: ASOF, latest: ASOF, previous: PREV, sessions_between: 1, days_after_night: 0 };

  it("says the night against the one before, with the sessions between", () => {
    expect(nightsWords(n)).toBe("The night of Thu, Sep 17, against Wed, Sep 16 · 1 session between.");
    expect(nightsWords({ ...n, sessions_between: 2 })).toContain("2 sessions between");
    expect(nightsWords({ ...n, previous: null, sessions_between: null })).toBe("The night of Thu, Sep 17 · no earlier night to compare.");
    expect(nightsWords({ ...n, latest: null, previous: null, sessions_between: null, days_after_night: null })).toBe("No night is recorded by this date.");
  });

  it("notes the as-of only when it is past the night", () => {
    expect(afterNightNote(n)).toBeNull();
    expect(afterNightNote({ ...n, asof: "2026-09-18", days_after_night: 1 })).toBe("Fri, Sep 18 is 1 day past that night. Nothing newer is recorded, so the page repeats it.");
    expect(afterNightNote({ ...n, latest: null, days_after_night: null })).toBeNull();
  });
});

describe("last night's words", () => {
  it("heads a card with its state move, a new card, or the state it stands in", () => {
    expect(headlineWords(card())).toBe("Warming → Armed");
    expect(headlineWords(card({ headline: line({ change: "new_card", to_state: "armed" }) }))).toBe("new card · Armed");
    expect(headlineWords(card({ headline: null, state: "managing" }))).toBe("Managing");
  });

  it("words each line in What Changed's neutral words", () => {
    const w = (over: Parameters<typeof line>[0]) => lineWords(line(over));
    expect(w({ change: "key_on", label: "Conviction" })).toBe("Conviction key turned on");
    expect(w({ change: "key_off", label: "Conviction" })).toBe("Conviction key turned off");
    expect(w({ change: "armed" })).toBe("newly armed");
    expect(w({ change: "no_longer_armed" })).toBe("no longer armed");
    expect(w({ change: "new_watch" })).toBe("new on watch");
    expect(w({ change: "trigger_new", label: "8-day breakout", signal_kind: "technical_breakout", on: "2026-09-10" })).toBe("fired · 8-day breakout · technical breakout · Sep 10");
    expect(w({ change: "trigger_gone", label: "8-day breakout", signal_kind: "technical_breakout", on: "2026-09-10" })).toBe("no longer firing · 8-day breakout · technical breakout · Sep 10");
    expect(w({ change: "risk_new", label: "note", signal_kind: "dilution_risk", on: null })).toBe("new risk signal · note · dilution risk");
    expect(w({ change: "trigger_redated", label: "breakout", signal_kind: "technical_breakout", previous_on: "2026-09-10", on: "2026-09-14" })).toBe("re-dated · breakout · technical breakout · Sep 10 → Sep 14");
    expect(w({ change: "entry_window", on: "2026-09-19", days: 2 })).toBe("entry window closes in 2 days · Sep 19");
    expect(w({ change: "entry_window", on: "2026-09-17", days: 0 })).toBe("entry window closes on the night · Sep 17");
    expect(w({ change: "exit_by", on: "2026-09-17" })).toBe("exit-by reached · Sep 17");
  });

  it("turns the server's tone into a class and opens a name by its ticker, else its security id", () => {
    expect(toneClass("accent")).toBe("dd-tone-accent");
    expect(toneClass("warm")).toBe("dd-tone-warm");
    expect(toneClass(null)).toBe("");
    expect(nameKey(line())).toBe("AAA");
    expect(nameKey(line({ ticker: null }))).toBe(SEC_A);
    expect(nameKey(line({ ticker: null, security_id: null }))).toBeNull();
  });
});

describe("returns", () => {
  it("signs a return and tones only a signed one", () => {
    expect(signed(1.2)).toBe("+1.20%");
    expect(signed(-0.5)).toBe("-0.50%");
    expect(signed(0)).toBe("0.00%");
    expect(signed(null)).toBe("—");
    expect([signClass(1), signClass(-1), signClass(0), signClass(null)]).toEqual(["dd-pos", "dd-neg", "", ""]);
  });
});

describe("positions' words", () => {
  it("words the fill, and the logged fill only when shares and price were both logged", () => {
    expect(fillWords(position())).toBe("filled Sep 11 at $48.00");
    expect(fillWords(position({ logged_price: null }))).toBe("filled Sep 11 · no price logged");
    expect(fillWords(position({ has_position: false, opened_on: null }))).toBe("no position logged as of this date");
    expect(fillWords(position({ opened_on: null }))).toBe("fill date not logged at $48.00");
    expect(loggedFillWords(position())).toBe("logged fill 100 sh × $48.00 = $4,800");
    expect(loggedFillWords(position({ shares: null, fill_usd: null }))).toBeNull();
    expect(loggedFillWords(position({ logged_price: null, fill_usd: null }))).toBeNull();
  });

  it("says which start the return since the fill used, and why when the logged price was not used", () => {
    const logged = sinceFillFig(position().since_fill);
    expect(logged.text).toBe("+37.50%");
    expect(logged.sub).toBe("from the logged price");
    expect(logged.title).toBe("$48.00 (the logged fill price) to $66.00 (the close on Sep 17); IWM +3.96% over the same dates");
    const first = sinceFillFig({ ...position().since_fill, basis: "first_close", basis_price: 52, bench_pct: null, note: "a split lies between the fill and the as-of" });
    expect(first.sub).toBe("from the first close Sep 11 · a split lies between the fill and the as-of");
    expect(first.title).toContain("no IWM bar on one of those dates");
  });

  it("reads the reason where a return is blank, and the flag's reason for a left-out name", () => {
    const base = position().since_fill;
    expect(sinceFillFig({ ...base, status: "no_name", ret_pct: null, reason: "the position names no company" })).toMatchObject({ text: "—", sub: "the position names no company" });
    expect(sinceFillFig({ ...base, status: "flagged", ret_pct: null, reason: "mixed-scale price history" })).toMatchObject({ text: "—", sub: "left out: mixed-scale price history" });
    const ls = position().last_session;
    expect(lastSessionFig(ls)).toMatchObject({ text: "+10.00%", sub: "IWM +0.96%", cls: "dd-pos" });
    expect(lastSessionFig({ ...ls, status: "unpriced", ret_pct: null, reason: "no close on 2026-09-16" })).toMatchObject({ text: "—", sub: "no close on 2026-09-16" });
    expect(lastSessionFig({ ...ls, status: "flagged", ret_pct: null, reason: "mixed-scale price history" }).sub).toBe("left out: mixed-scale price history");
  });

  it("words the hold clock in sessions left or sessions since it passed", () => {
    expect(holdWords({ exit_by: "2026-09-22", sessions_left: 3, passed_sessions: null })).toBe("exit-by Sep 22 · 3 sessions left");
    expect(holdWords({ exit_by: "2026-09-18", sessions_left: 1, passed_sessions: null })).toBe("exit-by Sep 18 · 1 session left");
    expect(holdWords({ exit_by: "2026-09-17", sessions_left: 0, passed_sessions: null })).toBe("exit-by Sep 17 · its last day");
    expect(holdWords({ exit_by: "2026-09-15", sessions_left: null, passed_sessions: 2 })).toBe("exit-by Sep 15 · passed 2 sessions ago");
    expect(holdWords({ exit_by: "2026-09-16", sessions_left: null, passed_sessions: 0 })).toBe("exit-by Sep 16 · passed");
    expect(holdWords({ exit_by: null, sessions_left: null, passed_sessions: null })).toBe("no exit-by");
  });

  it("counts the risk signals with the new ones marked and the kill criteria with their words in the hover", () => {
    expect(riskWords(position())).toBe("2 risk signals · 1 new last night");
    expect(riskWords(position({ risk_new: 0 }))).toBe("2 risk signals");
    expect(riskWords(position({ risk_signals: 1, risk_new: 0 }))).toBe("1 risk signal");
    expect(riskWords(position({ risk_signals: 0, risk_new: 0 }))).toBe("no risk signals");
    expect(killWords(position())).toBe("2 kill criteria");
    expect(killWords(position({ kill_criteria: ["x"] }))).toBe("1 kill criterion");
    expect(killWords(position({ kill_criteria: [] }))).toBe("no kill criteria");
    expect(killTitle(position())).toBe("the license is lost\na second going-concern note");
    expect(killTitle(position({ kill_criteria: [] }))).toBeUndefined();
  });
});

describe("due this week's words", () => {
  it("marks today and counts days otherwise; words a deadline by what it is", () => {
    expect(dueWhen(due())).toBe("today");
    expect(dueWhen(due({ today: false, on: "2026-09-19", days: 2 }))).toBe("Sat, Sep 19 · in 2 days");
    expect(dueWhat(due())).toBe("Readout · regulatory");
    expect(dueWhat(due({ kind: null }))).toBe("Readout");
    expect(dueWhat(due({ item: "entry_window" }))).toBe("entry window ends");
    expect(dueWhat(due({ item: "exit_by" }))).toBe("exit-by");
  });

  it("counts the catalysts that passed since P, and says nothing without a P", () => {
    expect(passedWords(payload())).toBe("1 catalyst passed since Wed, Sep 16 and is not yet pruned (3 passed in all).");
    const two = payload();
    expect(passedWords({ ...two, due: { ...two.due, passed_since: 2 } })).toContain("2 catalysts passed since");
    expect(passedWords({ ...two, due: { ...two.due, passed_since: 2 } })).toContain("are not yet pruned");
    expect(passedWords({ ...two, due: { ...two.due, passed_since: null } })).toBeNull();
    expect(passedWords({ ...two, nights: { ...two.nights, previous: null } })).toBeNull();
  });
});

describe("insider buys' words", () => {
  it("words the buyer, the size, the filing and the trigger the way Insider Buying does", () => {
    expect(buyerWords(buy())).toBe("Jane Roe (CEO)");
    expect(buyerWords(buy({ role: null }))).toBe("Jane Roe");
    expect(buyerWords(buy({ co_filers: 2 }))).toBe("Jane Roe (CEO) and 2 more");
    expect(buyerWords(buy({ filer: null }))).toBe("filer not named");
    expect(sizeWords(buy())).toBe("1,000 sh @ $4.00 = $4,000");
    expect(sizeWords(buy({ price: null }))).toBe("1,000 sh = $4,000");
    expect(sizeWords(buy({ shares: null, price: null }))).toBe("$4,000");
    expect(sizeWords(buy({ shares: null, price: null, usd: null }))).toBe("size not stated");
    expect(filedWords(buy())).toBe("filed Sep 17 · traded Sep 16, filed 1 day after");
    expect(filedWords(buy({ filed_days_after: null }))).toBe("filed Sep 17");
    expect(triggerWords(buy())).toBe("became a trigger on Card C");
    expect(triggerWords(buy({ triggers: [] }))).toBe("not a trigger");
  });

  it("says what the empty section means", () => {
    expect(insiderEmpty(emptyPayload())).toBe("No insider buys filed since Wed, Sep 16.");
    const p = emptyPayload();
    expect(insiderEmpty({ ...p, insider: { ...p.insider, no_baseline: true } })).toBe("No earlier night to compare.");
  });
});

describe("the tape's words and cut", () => {
  it("shows the flagged rises and falls by default and every row on request, naming what the toggle adds", () => {
    const t = payload().tape;
    const off = tapeRows(t, false);
    expect(off.shown.map((r) => r.ticker)).toEqual(["RS1", "RS2", "FL1"]);
    expect(off.extra).toBe(2);
    const on = tapeRows(t, true);
    expect(on.shown.map((r) => r.ticker)).toEqual(TAPE_ROWS.map((r) => r.ticker));
    expect(on.extra).toBe(2); // the count stays, so the toggle can name what it adds and what it hides
    // every row cut: nothing for the toggle to add
    expect(tapeRows({ ...t, rows: [t.rows[0], t.rows[3]] }, false).extra).toBe(0);
  });

  it("words the note with n priced names, the span and IWM", () => {
    expect(tapeNote(payload().tape)).toBe("Largest last-session rises and falls among 4 priced names · Wed, Sep 16 to Thu, Sep 17 · IWM +0.96%");
    const t = payload().tape;
    expect(tapeNote({ ...t, iwm_pct: null })).not.toContain("IWM");
  });

  it("words the two clocks", () => {
    expect(clockWords(tapeRow())).toBe("2 in · 3 left");
    expect(clockWords(tapeRow({ sessions_left: null, closed_ago: 2 }))).toBe("2 in · window closed 2 sessions ago");
    expect(clockWords(tapeRow({ sessions_left: null, closed_ago: null }))).toBe("2 in");
    expect(clockWords(tapeRow({ sessions_since: null, sessions_left: null }))).toBe("—");
  });

  it("words the move since the arm night against IWM, and why it is blank", () => {
    expect(sinceArmFig(tapeRow())).toMatchObject({ text: "+6.12%", sub: "IWM +1.94%", cls: "dd-pos" });
    const blank = (gap: "no_arm_night" | "flagged" | "before_window" | "no_bar") =>
      sinceArmFig(tapeRow({ since_arm: { entry_date: null, entry_close: null, last_date: null, last_close: null, ret_pct: null, iwm_pct: null, gap } }));
    expect(blank("no_bar").sub).toBe("no close since the arm night");
    expect(blank("no_arm_night").sub).toBe("no arm night");
    expect(blank("before_window").sub).toBe("arm night before the price window");
    expect(blank("flagged").sub).toBe("left out: flagged price history");
  });
});

describe("forming and the work lines", () => {
  it("words the counts and the key turned last night", () => {
    expect(formingWords(payload().forming)).toEqual({ warming: "2 Warming cards", incubating: "40 Incubating cards", turned: "key turned last night:" });
    expect(formingWords({ warming: 1, incubating: 1, key_turned: [] })).toEqual({ warming: "1 Warming card", incubating: "1 Incubating card", turned: null });
  });

  it("shows the work, every count off the payload", () => {
    const text = workLines(payload()).join("\n");
    expect(text).toContain("As of Thu, Sep 17 · the public clock");
    expect(text).toContain("48 cards counted of 50 live · 2 created after the as-of");
    expect(text).toContain("45 recorded on the night · 2 carried from an earlier night · 1 with no call yet");
    expect(text).toContain("computed · 1,234 ms");
    const live = workLines(payload({ clock: "record", cache: "hit", compute_ms: 7, counts: { ...payload().counts, not_yet_created: 0, roster_fallback: 3 } })).join("\n");
    expect(live).toContain("the live record");
    expect(live).toContain("3 on today's roster (no snapshot by then)");
    expect(live).toContain("from the cache · 7 ms");
    const none = workLines(payload({ insider: { ...payload().insider, no_baseline: true } })).join("\n");
    expect(none).toContain("insider buys: no earlier night, so no span");
  });
});
