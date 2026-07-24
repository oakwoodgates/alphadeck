import { describe, expect, it } from "vitest";

import type { ScoreboardEpisodeOut } from "../../api/hooks";
import {
  edgeLens,
  fmtPrice,
  givebackPhrase,
  horizonLens,
  moveNote,
  noForwardBar,
  peakTimingPhrase,
  setupStrengthPct,
} from "../scorecard";

// Pure display logic for the four timing lenses — which lens renders given which fields, the
// honest-loudness hiding, and the exact phrasings. Mirrors rows.test.ts.

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    ticker: "DEVCO",
    is_headline: true,
    theme_armed: false,
    arm_date: "2026-07-10",
    dearm_date: null,
    close_reason: "window_end",
    status: "open",
    matured: false,
    censored_start: false,
    arm_ingest_fresh: null,
    freeze_era: false,
    thaw_lag_days: null,
    ingest_flagged: false,
    ingest_note: null,
    verdict: "core_entry",
    entry_grade: "core",
    conviction_grade: "core",
    confidence: 0.9,
    exit_by: "2026-11-22",
    arm_until: null,
    warm_date: null,
    triggers_at_arm: [],
    entry_close: null,
    exit_close: null,
    exit_date: null,
    forward_return: null,
    arm_until_return: null,
    warm_return: null,
    peak_return: null,
    peak_date: null,
    exit_vs_peak_days: null,
    truncated: false,
    insufficient_prices: false,
    operator: null,
    ...over,
  } as ScoreboardEpisodeOut;
}

describe("fmtPrice", () => {
  it("prices in the operator idiom ($ + 2dp), a dash when unknowable", () => {
    expect(fmtPrice(132.4)).toBe("$132.40");
    expect(fmtPrice(9)).toBe("$9.00");
    expect(fmtPrice(0)).toBe("$0.00");
    expect(fmtPrice(null)).toBe("—");
    expect(fmtPrice(undefined)).toBe("—");
  });
});

describe("noForwardBar — the degenerate single-/zero-bar guard", () => {
  it("true for a single-bar arm (exit_date === arm_date) and for no bar at all", () => {
    expect(noForwardBar(ep({ exit_date: "2026-07-10" }))).toBe(true); // == arm_date
    expect(noForwardBar(ep({ insufficient_prices: true }))).toBe(true); // no bar at all
  });
  it("false once a real forward bar has landed", () => {
    expect(noForwardBar(ep({ exit_date: "2026-07-13" }))).toBe(false); // a forward bar
    expect(noForwardBar(ep({ exit_date: null }))).toBe(false); // no exit yet, not single-bar
  });
});

describe("moveNote — the honest price state (Lens 1)", () => {
  it("no forward bar (nothing to score) takes priority over truncated", () => {
    expect(moveNote(ep({ insufficient_prices: true, truncated: true }))).toBe(
      "awaiting the first forward bar — nothing to score yet",
    );
  });
  it("a single-bar arm gets the same note (degenerate 0.0%, not a move)", () => {
    expect(moveNote(ep({ exit_date: "2026-07-10" }))).toBe(
      "awaiting the first forward bar — nothing to score yet",
    );
  });
  it("truncated says it's measured short of the horizon (a bar HAS landed)", () => {
    expect(moveNote(ep({ truncated: true, exit_date: "2026-07-13" }))).toBe(
      "measured to the last bar ≤ as-of — the horizon isn't reached yet",
    );
  });
  it("a settled move needs no note", () => {
    expect(moveNote(ep({ status: "closed", matured: true, exit_date: "2026-11-22" }))).toBeNull();
  });
});

describe("peakTimingPhrase (Lens 2)", () => {
  it("matured (default): n>0 → closed after; n==0 → at; n<0 → before the peak", () => {
    expect(peakTimingPhrase(12)).toBe("horizon closed 12d after the peak");
    expect(peakTimingPhrase(0)).toBe("horizon closed at the peak");
    expect(peakTimingPhrase(-3)).toBe("horizon closes 3d before the peak");
  });
  it("truncated: 'last bar …' — the horizon hasn't closed, only the measurement (same value)", () => {
    expect(peakTimingPhrase(12, true)).toBe("last bar 12d after the peak");
    expect(peakTimingPhrase(0, true)).toBe("last bar at the peak");
  });
  it("null when the gap is unknowable — no phrase at all", () => {
    expect(peakTimingPhrase(null)).toBeNull();
    expect(peakTimingPhrase(undefined, true)).toBeNull();
  });
});

describe("givebackPhrase (Lens 2)", () => {
  it("words the giveback only when the peak sat visibly above the exit", () => {
    expect(givebackPhrase(0.18, 0.12)).toBe("gave back from +18.0% to +12.0%");
  });
  it("no giveback when the exit met the peak (peak is the max — never below)", () => {
    expect(givebackPhrase(0.12, 0.12)).toBeNull();
    expect(givebackPhrase(0.12, 0.18)).toBeNull(); // defensive: never below the exit
  });
  it("no giveback when the two round identical (never 'from X to X')", () => {
    expect(givebackPhrase(0.1204, 0.1199)).toBeNull(); // both render +12.0%
  });
  it("null when either leg is missing", () => {
    expect(givebackPhrase(null, 0.12)).toBeNull();
    expect(givebackPhrase(0.18, null)).toBeNull();
  });
});

describe("horizonLens (Lens 2) — hidden without a realized peak", () => {
  it("null when peak_return or peak_date is missing (hide the whole lens)", () => {
    expect(horizonLens(ep({ peak_return: null, peak_date: "2026-08-01" }))).toBeNull();
    expect(horizonLens(ep({ peak_return: 0.18, peak_date: null }))).toBeNull();
  });
  it("assembles the peak, its timing, and the giveback when present", () => {
    const h = horizonLens(
      ep({ peak_return: 0.18, peak_date: "2026-08-01", exit_vs_peak_days: 12, forward_return: 0.12 }),
    );
    expect(h).not.toBeNull();
    expect(h!.peak).toEqual({ text: "+18.0%", cls: "pos" });
    expect(h!.timing).toBe("horizon closed 12d after the peak");
    expect(h!.giveback).toBe("gave back from +18.0% to +12.0%");
  });
  it("keeps the lens but drops the giveback when the exit held the peak", () => {
    const h = horizonLens(
      ep({ peak_return: 0.12, peak_date: "2026-08-01", exit_vs_peak_days: 0, forward_return: 0.12 }),
    );
    expect(h!.timing).toBe("horizon closed at the peak");
    expect(h!.giveback).toBeNull();
  });
  it("threads `truncated` into the timing — 'last bar …' for a running episode, 'horizon closed …' for a matured one", () => {
    const base = { peak_return: 0.18, peak_date: "2026-08-01", exit_vs_peak_days: 12, forward_return: 0.12 };
    expect(horizonLens(ep({ ...base, truncated: true }))!.timing).toBe(
      "last bar 12d after the peak",
    );
    expect(horizonLens(ep({ ...base, truncated: false, matured: true }))!.timing).toBe(
      "horizon closed 12d after the peak",
    );
  });
});

describe("edgeLens (Lens 3) — armed in time, or did the move happen during warming?", () => {
  it("no warm_date → one quiet line, never an empty stat", () => {
    const e = edgeLens(ep({ warm_date: null }));
    expect(e).toEqual({
      kind: "no_warm",
      line: "armed without a visible warm-up (conviction + confirmation co-fired)",
    });
  });
  it("warm return well above the armed return ⇒ armed late", () => {
    const e = edgeLens(ep({ warm_date: "2026-06-20", warm_return: 0.2, forward_return: 0.12 }));
    expect(e.kind).toBe("compared");
    if (e.kind === "compared") {
      expect(e.lead).toBe("much of the move happened during warming — armed late");
      expect(e.warm).toEqual({ text: "+20.0%", cls: "pos" });
      expect(e.forward).toEqual({ text: "+12.0%", cls: "pos" });
    }
  });
  it("armed return above the warm return ⇒ arming waited out the early chop", () => {
    const e = edgeLens(ep({ warm_date: "2026-06-20", warm_return: 0.04, forward_return: 0.12 }));
    if (e.kind === "compared") {
      expect(e.lead).toBe("the warming stretch gave nothing up — arming waited out the early chop");
    }
  });
  it("within the 0.5pp floor ⇒ in step (an honest neutral, not a manufactured verdict)", () => {
    const e = edgeLens(ep({ warm_date: "2026-06-20", warm_return: 0.122, forward_return: 0.12 }));
    if (e.kind === "compared") {
      expect(e.lead).toBe("little separates the warm-up from the arm — armed in step with the move");
    }
  });
  it("a warm-up with an unpriced leg says so, still not an empty stat", () => {
    const e = edgeLens(ep({ warm_date: "2026-06-20", warm_return: null, forward_return: null }));
    if (e.kind === "compared") {
      expect(e.lead).toBe("the warm-up leg isn't priced yet");
      expect(e.warm).toEqual({ text: "—", cls: "" });
    }
  });
});

describe("setupStrengthPct (Lens 4)", () => {
  it("rounds confidence to an integer percent, null when unset", () => {
    expect(setupStrengthPct(ep({ confidence: 0.9 }))).toBe(90);
    expect(setupStrengthPct(ep({ confidence: 0.544 }))).toBe(54);
    expect(setupStrengthPct(ep({ confidence: null }))).toBeNull();
  });
});
