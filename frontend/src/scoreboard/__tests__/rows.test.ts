import { describe, expect, it } from "vitest";

import type { ScoreboardEpisodeOut, ScoreboardMetricOut, ScoreboardThesisOut } from "../../api/hooks";
import {
  episodeBadges,
  fmtReturn,
  gateMetrics,
  groupCount,
  groupHint,
  groupToneClass,
  metricHeadline,
  operatorLine,
  returnLabel,
} from "../rows";

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

function metric(over: Partial<ScoreboardMetricOut> = {}): ScoreboardMetricOut {
  return {
    name: "arm_timing_forward_return",
    claim: "timing",
    n: 0,
    insufficient_n: true,
    summary: {},
    detail: [],
    note: "",
    ...over,
  } as ScoreboardMetricOut;
}

describe("fmtReturn", () => {
  it("signs and tones both directions, dash when unknowable", () => {
    expect(fmtReturn(0.052)).toEqual({ text: "+5.2%", cls: "pos" });
    expect(fmtReturn(-0.021)).toEqual({ text: "-2.1%", cls: "neg" });
    expect(fmtReturn(0)).toEqual({ text: "0.0%", cls: "" });
    expect(fmtReturn(null)).toEqual({ text: "—", cls: "" });
  });
});

describe("returnLabel — a return is labeled for what it IS", () => {
  it("realized only once closed AND matured", () => {
    expect(returnLabel(ep({ status: "closed", matured: true }))).toBe("realized");
  });
  it("running while open or immature", () => {
    expect(returnLabel(ep({ status: "open", matured: false }))).toBe("running");
    expect(returnLabel(ep({ status: "closed", matured: false }))).toBe("running");
  });
  it("a day-1 arm with no bar yet says so", () => {
    expect(returnLabel(ep({ insufficient_prices: true }))).toBe("awaiting first bar");
  });
});

describe("episodeBadges — marks are exceptions, not constants", () => {
  it("open + censored episode carries both marks", () => {
    const labels = episodeBadges(ep({ status: "open", censored_start: true })).map((b) => b.label);
    expect(labels).toContain("OPEN");
    expect(labels).toContain("CENSORED");
    expect(labels).not.toContain("MATURED");
  });
  it("a closed matured un-censored episode carries only MATURED", () => {
    const labels = episodeBadges(
      ep({ status: "closed", matured: true, censored_start: false }),
    ).map((b) => b.label);
    expect(labels).toEqual(["MATURED"]);
  });
});

describe("operatorLine", () => {
  it("no decision logged is the honest gap, not an error", () => {
    expect(operatorLine(ep()).kind).toBe("none");
    expect(operatorLine(ep()).text).toBe("no decision logged");
  });
  it("a took row carries the return and the inferred flag", () => {
    const line = operatorLine(
      ep({
        operator: {
          action: "took",
          decision_id: "d1",
          decision_date: "2026-07-11",
          reason: null,
          thesis_level: false,
          entry_price: 100,
          entry_inferred: false,
          exit_price: 108,
          exit_inferred: true,
          exit_date: null,
          running: true,
          operator_return: 0.08,
        },
      }),
    );
    expect(line.kind).toBe("took");
    expect(line.text).toContain("took 2026-07-11 @ 100");
    expect(line.text).toContain("running");
    expect(line.ret?.text).toBe("+8.0%");
    expect(line.inferred).toBe(true);
  });
  it("a pass carries no prices", () => {
    const line = operatorLine(
      ep({
        operator: {
          action: "passed",
          decision_id: "d2",
          decision_date: "2026-07-11",
          reason: "too extended",
          thesis_level: false,
          entry_price: null,
          entry_inferred: false,
          exit_price: null,
          exit_inferred: false,
          exit_date: null,
          running: false,
          operator_return: null,
        },
      }),
    );
    expect(line.kind).toBe("passed");
    expect(line.ret).toBeNull();
  });
});

describe("gateMetrics — the gate itself is the information", () => {
  it("all-insufficient collapses to ONE quiet line", () => {
    const { shown, gatedLine } = gateMetrics(
      [metric({ n: 3 }), metric({ name: "false_arm_rate", n: 2 })],
      5,
    );
    expect(shown).toEqual([]);
    expect(gatedLine).toBe("2 of 2 metrics await n ≥ 5 (largest today: n=3)");
  });
  it("a sufficient metric renders; the rest stay gated", () => {
    const ok = metric({ n: 7, insufficient_n: false, summary: { median: 0.031 } });
    const { shown, gatedLine } = gateMetrics([ok, metric({ name: "x", n: 1 })], 5);
    expect(shown).toEqual([ok]);
    expect(gatedLine).toContain("1 of 2");
  });
  it("nothing gated → no line at all (a constant marker is noise)", () => {
    const ok = metric({ n: 7, insufficient_n: false });
    expect(gateMetrics([ok], 5).gatedLine).toBeNull();
  });
});

describe("metricHeadline", () => {
  it("prefers the median and formats it as a return", () => {
    expect(metricHeadline(metric({ n: 7, summary: { median: 0.031 } }))).toBe(
      "median +3.1% · n=7",
    );
  });
  it("falls back to n when the summary has no known key", () => {
    expect(metricHeadline(metric({ n: 4, summary: {} }))).toBe("n=4");
  });
});

function thesis(over: Partial<ScoreboardThesisOut> = {}): ScoreboardThesisOut {
  return {
    thesis_id: "t1",
    name: "HIMS",
    ticker: "HIMS",
    basket_size: 1,
    archived: false,
    first_call_asof: "2026-07-10",
    last_call_asof: "2026-07-11",
    current_state: "armed",
    current_verdict: "core_entry",
    warming_since: null,
    episodes: [],
    operator_spans: [],
    decision_anomaly: null,
    record_error: null,
    ...over,
  } as ScoreboardThesisOut;
}

describe("groupHint / groupToneClass / groupCount", () => {
  it("shows the record span, and the warming accrual at zero episodes", () => {
    expect(groupHint(thesis())).toBe("record 2026-07-10 → 2026-07-11");
    expect(
      groupHint(thesis({ warming_since: "2026-07-10", first_call_asof: "2026-07-10", last_call_asof: "2026-07-10" })),
    ).toBe("record 2026-07-10 · warming since 2026-07-10");
    expect(groupHint(thesis({ first_call_asof: null }))).toBe("no call-of-record yet");
  });
  it("tone: open episode → armed; warming edge → warm; else quiet", () => {
    expect(groupToneClass(thesis({ episodes: [ep()] }))).toBe("sbg-armed");
    expect(groupToneClass(thesis({ current_state: "warming" }))).toBe("sbg-warm");
    expect(groupToneClass(thesis())).toBe("sbg-quiet");
  });
  it("count = episodes + off-record spans", () => {
    expect(groupCount(thesis({ episodes: [ep(), ep()] }))).toBe(2);
  });
});
