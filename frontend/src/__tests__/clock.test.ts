import { describe, expect, it } from "vitest";

import { browserClock, countdownOrigin, type AppClock } from "../clock";
import { dayCount, daysFrom } from "../util/format";

// F2 — the day a COUNTDOWN counts from. The frontend mirror of the backend's `countdown_origin`
// (domain/market_time.py, pinned in tests/domain/test_market_time.py): the ACTION day on a live view, the as-of on
// a past one. Tuesday 2026-10-27 09:00: the current night is Monday 10-26, the action day Tuesday.

const TUE_MORNING: AppClock = {
  currentNight: "2026-10-26",
  marketToday: "2026-10-27",
  actionDay: "2026-10-27",
  failed: false,
};

describe("countdownOrigin", () => {
  it("is the action day for the current night and every later as-of, the as-of itself before it", () => {
    expect(countdownOrigin("2026-10-26", TUE_MORNING)).toBe("2026-10-27"); // the default view
    expect(countdownOrigin("2026-10-27", TUE_MORNING)).toBe("2026-10-27"); // today
    expect(countdownOrigin("2026-10-28", TUE_MORNING)).toBe("2026-10-27"); // a future as-of is live too
    expect(countdownOrigin("2026-10-23", TUE_MORNING)).toBe("2026-10-23"); // a past view: unchanged
  });

  it("reads Tuesday's catalyst 'today' and Monday's 'passed' from Monday's night on Tuesday morning", () => {
    const origin = countdownOrigin("2026-10-26", TUE_MORNING);
    expect(daysFrom(origin, "2026-10-27")).toBe(0); // today
    expect(dayCount(0)).toBe("today");
    expect(daysFrom(origin, "2026-10-26")).toBe(-1); // passed
    expect(daysFrom(origin, "2026-10-28")).toBe(1); // in 1 day (2 counted from the night)
    // a past view counts from its own as-of
    const past = countdownOrigin("2026-10-23", TUE_MORNING);
    expect(daysFrom(past, "2026-10-24")).toBe(1);
  });

  it("follows the weekend: Friday's night is acted on Monday", () => {
    const sat: AppClock = { currentNight: "2026-10-30", marketToday: "2026-10-31", actionDay: "2026-11-02", failed: false };
    expect(countdownOrigin("2026-10-30", sat)).toBe("2026-11-02");
    expect(countdownOrigin("2026-10-29", sat)).toBe("2026-10-29");
  });

  it("falls back to the as-of when the action day is unknown (the server's clock could not be read)", () => {
    const failed = browserClock(true);
    expect(failed.actionDay).toBeNull();
    expect(countdownOrigin(failed.currentNight, failed)).toBe(failed.currentNight);
    expect(countdownOrigin("2026-10-26", { ...TUE_MORNING, actionDay: null })).toBe("2026-10-26");
  });
});

describe("dayCount", () => {
  it("words a countdown's days: 'today' at zero, else 'Nd'", () => {
    expect(dayCount(0)).toBe("today");
    expect(dayCount(1)).toBe("1d");
    expect(dayCount(34)).toBe("34d");
  });
});
