import { describe, expect, it } from "vitest";

import {
  agreesUnderBandRule,
  fmtDelta,
  fmtMetric,
  plateauLine,
  readSweep,
  statusLine,
  unmeasurableLine,
} from "../sweep";

// The sweep's reader and its copy. One rule dominates: a sweep shows a CURVE, never a winner — so the
// reader preserves the backend's order, marks only the plateau, and the null result ("no plateau") is
// stated plainly instead of being dressed up as a best point.

const point = (over: Record<string, unknown> = {}) => ({
  dials: { insider_core_alpha_liveness_days: 180 },
  run_ids: ["r1-w1", "r1-w2"],
  config_short: "abcd1234",
  n_episodes: 12,
  n_scored: 9,
  metric: 0.04,
  delta_vs_baseline: 0.01,
  window_deltas: [0.01, 0.02],
  sign_agreement: true,
  is_baseline: false,
  runs_shared: false,
  ...over,
});

const sweep = (over: Record<string, unknown> = {}) => ({
  clock: "public",
  dial_names: ["insider_core_alpha_liveness_days"],
  metric_name: "arm_timing_forward_return_median",
  window_start: "2025-09-01",
  window_end: "2026-09-14",
  windows: [
    ["2025-09-01", "2025-10-12"],
    ["2025-10-13", "2025-11-23"],
  ],
  concurrency: 2,
  pass_id: "20260918T031500Z-public-h5",
  mirror_hash: "c".repeat(64),
  baseline_config_short: "abcd1234",
  points: [point({ run_id: "r1" }), point({ run_id: "r2" }), point({ run_id: "r3" })],
  plateau: [0, 1],
  banner: "a curve, not a winner",
  ...over,
});

describe("readSweep", () => {
  it("keeps the backend's point ORDER — never re-sorted by outcome", () => {
    const v = readSweep(
      sweep({
        points: [
          point({ run_ids: ["low"], metric: -0.2 }),
          point({ run_ids: ["high"], metric: 0.9 }),
          point({ run_ids: ["mid"], metric: 0.1 }),
        ],
        plateau: [],
      }),
    );
    expect(v?.points.map((p) => p.runIds[0])).toEqual(["low", "high", "mid"]);
  });

  it("marks membership of the PLATEAU and nothing else", () => {
    const v = readSweep(sweep());
    expect(v?.points.map((p) => p.inPlateau)).toEqual([true, true, false]);
    expect(v?.plateauWidth).toBe(2);
  });

  it("carries the fact axis the one shared mirror was exported on", () => {
    // A record sweep and a public sweep are two experiments; reading them as one series is the mistake
    // this field exists to prevent, so it has to survive onto the curve and not just onto each run.
    expect(readSweep(sweep())?.clock).toBe("public");
  });

  it("reads a curve written before the axis was recorded as the record clock", () => {
    // Not "unknown": the record clock is all the exporter could produce at the time.
    expect(readSweep(sweep({ clock: undefined }))?.clock).toBe("record");
  });

  it("tolerates a sweep written by an older engine", () => {
    // Untyped pass-through on the wire (the sweep module imports the run writer, so its models cannot
    // be published into the lean contract). Absence degrades; it never throws.
    const v = readSweep({ points: [{ run_id: "r1" }] });
    expect(v?.points[0].metric).toBeNull();
    expect(v?.points[0].dials).toEqual([]);
    expect(v?.plateauWidth).toBe(0);
  });

  it("renders a PRE-SPLIT curve rather than reading it as empty", () => {
    // A curve written before S1 carries `run_id` and `subwindow_deltas`. Falling back to them is what
    // keeps an older artifact readable — the sweep payload is untyped on the wire precisely so an old
    // one still renders instead of failing validation.
    const v = readSweep({
      points: [{ run_id: "old-1", subwindow_deltas: [0.01, -0.02] }],
      plateau: [],
    });
    expect(v?.points[0].runIds).toEqual(["old-1"]);
    expect(v?.points[0].windowDeltas).toEqual([0.01, -0.02]);
    expect(v?.windows).toEqual([]);
  });

  it("carries the windows, the concurrency and the pass id", () => {
    // The windows are what the per-point deltas line up against, index for index; the concurrency is
    // recorded because a saturated box measures contention as well as dials.
    const v = readSweep(sweep())!;
    expect(v.windows).toEqual([
      { start: "2025-09-01", end: "2025-10-12" },
      { start: "2025-10-13", end: "2025-11-23" },
    ]);
    expect(v.points[0].windowDeltas).toHaveLength(v.windows.length);
    expect(v.concurrency).toBe(2);
    expect(v.passId).toBe("20260918T031500Z-public-h5");
  });

  it("marks a point whose runs are SHARED with another", () => {
    // Two dial settings that resolve to one config are one measurement cited twice. Unmarked, a shared
    // baseline would read as an independent confirmation sitting next to itself.
    const v = readSweep(sweep({ points: [point({ runs_shared: true }), point()] }))!;
    expect(v.points.map((p) => p.runsShared)).toEqual([true, false]);
  });

  it("is null for a payload that is not a sweep at all", () => {
    expect(readSweep(null)).toBeNull();
    expect(readSweep("nope")).toBeNull();
  });
});

describe("fmtDelta / fmtMetric", () => {
  it("signs a delta in percentage POINTS and a level in percent", () => {
    expect(fmtDelta(0.014)).toBe("+1.4pp");
    expect(fmtDelta(-0.014)).toBe("-1.4pp");
    expect(fmtMetric(0.041)).toBe("+4.1%");
  });

  it("dashes an unknown figure rather than printing a zero", () => {
    expect(fmtDelta(null)).toBe("—");
    expect(fmtMetric(null)).toBe("—");
  });
});

describe("plateauLine", () => {
  it("states the NULL RESULT plainly when no band formed", () => {
    // The most likely outcome of a sweep, and the one a research surface must not dress up.
    const v = readSweep(sweep({ plateau: [1] }))!;
    expect(plateauLine(v)).toContain("No plateau");
    expect(plateauLine(v)).toContain("noise until it has");
  });

  it("names a real band by its WIDTH and its cross-WINDOW agreement, never by a best point", () => {
    const v = readSweep(sweep())!;
    const line = plateauLine(v);
    expect(line).toContain("2 points wide");
    expect(line).toContain("windows");
    expect(line.toLowerCase()).not.toContain("best");
    expect(line.toLowerCase()).not.toContain("optimal");
  });

  it("says so when the sweep has no points at all", () => {
    const v = readSweep(sweep({ points: [], plateau: [] }))!;
    expect(plateauLine(v)).toBe("No points in this sweep.");
  });

  // A1 — WHICH agreement rule the band is keyed on. The rule changed on 2026-09-18 (strict: every
  // window moved the same way, none unchanged and none unmeasurable), the older field stays on every
  // point, and a curve must never be described with the counts of a rule it was not keyed on.

  it("names the rule the band was keyed on, in words", () => {
    const strict = readSweep(sweep({ plateau_rule: "strict_sign_agreement" }))!;
    expect(plateauLine(strict)).toContain("STRICT agreement");
    const old = readSweep(sweep({ plateau_rule: "sign_agreement" }))!;
    expect(plateauLine(old)).toContain("pre-2026-09-18");
  });

  it("counts agreement by the BAND's rule, not by whichever field is handy", () => {
    // Both points agree under the old rule and neither does under the strict one. A band keyed on
    // strict must not borrow the other rule's count.
    const raw = sweep({
      plateau_rule: "strict_sign_agreement",
      points: [
        point({ sign_agreement: true, strict_sign_agreement: false }),
        point({ sign_agreement: true, strict_sign_agreement: false }),
      ],
      plateau: [0, 1],
    });
    expect(plateauLine(readSweep(raw)!)).toContain("0 of them");
    const under_old = readSweep({ ...raw, plateau_rule: "sign_agreement" })!;
    expect(plateauLine(under_old)).toContain("2 of them");
  });

  it("reads a curve written BEFORE the field as one keyed on the older rule — a fact, not an unknown", () => {
    const v = readSweep(sweep())!;
    expect(v.plateauRule).toBe("sign_agreement");
    expect(agreesUnderBandRule(v, v.points[0])).toBe(true); // it only carries `sign_agreement`
  });
});

describe("the window status and the slice (A1)", () => {
  it("says a window held nothing to measure rather than calling it a zero", () => {
    const v = readSweep(
      sweep({
        points: [
          point({
            window_status: ["moved_up", "unmeasurable"],
            window_deltas: [0.02, null],
          }),
        ],
        plateau: [],
      }),
    )!;
    expect(statusLine(v.points[0])).toBe("up · nothing to measure");
    expect(fmtDelta(v.points[0].windowDeltas[1])).toBe("—"); // never rendered as 0.0
  });

  it("distinguishes a window that did not move from one that could not have", () => {
    const v = readSweep(
      sweep({ points: [point({ window_status: ["unchanged", "unmeasurable"] })], plateau: [] }),
    )!;
    expect(statusLine(v.points[0])).toBe("no change · nothing to measure");
  });

  it("carries the slice so a sliced curve can never be read against the pool", () => {
    const v = readSweep(sweep({ metric_slice: "key1_source=ratified_catalyst" }))!;
    expect(v.metricSlice).toBe("key1_source=ratified_catalyst");
    expect(readSweep(sweep())!.metricSlice).toBe("");
  });
});

// A2b — THE THIRD RULE, for a curve read on one family.
//
// Strict-over-all is unsatisfiable by construction when a family leaves windows empty, so a sliced pass
// keyed on it has an empty band before any data arrives. Strict-over-MEASURABLE asks the windows that
// could answer, and the count of the ones that could not rides the band's own sentence — "agrees across
// the seven windows that had episodes" and "agrees across nine" are different claims.

describe("strict over the measurable windows (A2b)", () => {
  const sliced = (over: Record<string, unknown> = {}) =>
    sweep({
      plateau_rule: "strict_measurable_agreement",
      metric_slice: "key1_source=ratified_catalyst",
      windows: [
        ["2025-09-01", "2025-10-12"],
        ["2025-10-13", "2025-11-23"],
        ["2025-11-24", "2026-01-04"],
      ],
      points: [
        point({
          sign_agreement: false,
          strict_sign_agreement: false,
          strict_measurable_agreement: true,
          n_unmeasurable: 1,
          window_status: ["moved_up", "moved_up", "unmeasurable"],
        }),
        point({
          sign_agreement: false,
          strict_sign_agreement: false,
          strict_measurable_agreement: true,
          n_unmeasurable: 1,
          window_status: ["moved_up", "moved_up", "unmeasurable"],
        }),
      ],
      plateau: [0, 1],
      ...over,
    });

  it("counts agreement by the third rule when the band is keyed on it", () => {
    const v = readSweep(sliced())!;
    expect(v.points.every((p) => agreesUnderBandRule(v, p))).toBe(true);
    expect(plateauLine(v)).toContain("2 of them");
    // ...and the same points under strict-over-all agree with nothing
    const strict = readSweep(sliced({ plateau_rule: "strict_sign_agreement" }))!;
    expect(strict.points.some((p) => agreesUnderBandRule(strict, p))).toBe(false);
  });

  it("names the rule in words a reader can check the band against", () => {
    expect(plateauLine(readSweep(sliced())!)).toContain("windows that could answer");
  });

  it("says on the BAND what the band could not see", () => {
    const line = unmeasurableLine(readSweep(sliced())!);
    expect(line).toContain("1 of 3 windows");
    expect(line).toContain("excluded from the agreement rather than counted as agreeing");
    expect(plateauLine(readSweep(sliced())!)).toContain("1 of 3 windows");
  });

  it("reports a RANGE when the band's points saw different amounts of nothing", () => {
    const v = readSweep(
      sliced({
        points: [
          point({ strict_measurable_agreement: true, n_unmeasurable: 1 }),
          point({ strict_measurable_agreement: true, n_unmeasurable: 2 }),
        ],
      }),
    )!;
    expect(unmeasurableLine(v)).toContain("1–2 of 3 windows");
  });

  it("says nothing at all when every window was measurable — loudness marks the exception", () => {
    const v = readSweep(
      sliced({
        points: [
          point({ strict_measurable_agreement: true, n_unmeasurable: 0 }),
          point({ strict_measurable_agreement: true, n_unmeasurable: 0 }),
        ],
      }),
    )!;
    expect(unmeasurableLine(v)).toBe("");
  });

  it("reads a curve with no third field as not agreeing under it, never as agreeing", () => {
    // an older curve carries neither the field nor the count; the honest default is "no"
    const v = readSweep(sweep({ plateau_rule: "strict_measurable_agreement" }))!;
    expect(v.points.every((p) => p.strictMeasurableAgreement)).toBe(false);
    expect(v.points.every((p) => p.nUnmeasurable === 0)).toBe(true);
  });
});
