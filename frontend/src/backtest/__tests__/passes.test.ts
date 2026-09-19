import { describe, expect, it } from "vitest";

import {
  armLabel,
  defaultOpen,
  groupArms,
  groupLabel,
  groupRuns,
  groupSweeps,
  looksLikeCalibration,
  STANDALONE,
} from "../passes";

// Grouping a registry by pass. Two rules carry all of it, and both are about what grouping is NOT.
//
// It is not a filter: every run and every curve stays in the output, and the groups only decide what is
// expanded. The registry's job is counting trials, so a row that vanished would be an experiment nobody
// could count against themselves.
//
// It is not a judgment: the calibration split reads the operator's own pre-registration text, which could
// be worded any way at all. That is acceptable only because a miss means the pass renders in the main
// list — the honest failure direction — rather than being hidden.

const run = (over: Record<string, unknown> = {}) =>
  ({
    run_id: "r1",
    created_at: "2026-09-18T03:30:45+00:00",
    config_short: "abcd1234",
    config_hash: "a".repeat(64),
    clock: "public",
    known_at_mode: "lockstep",
    window_start: "2025-09-01",
    window_end: "2026-09-14",
    n_theses: 12,
    n_episodes: 300,
    dials_moved: [],
    pass_id: "20260918T032952Z-public-h5-phase-1",
    hypothesis: "H5: the conviction-side horizons are the timing lever",
    ...over,
  }) as never;

const curve = (over: Record<string, unknown> = {}) =>
  ({
    pass_id: "20260918T032952Z-public-h5-phase-1",
    dial: "insider_core_alpha_liveness_days",
    dial_names: ["insider_core_alpha_liveness_days"],
    metric_slice: "",
    hypothesis: "H5",
    decision_rule: "a plateau, never a point",
    clock: "public",
    window_start: "2025-09-01",
    window_end: "2026-09-14",
    n_windows: 9,
    n_points: 5,
    plateau_width: 2,
    plateau_rule: "strict_sign_agreement",
    mirror_hash: "a".repeat(64),
    mirror_reused: false,
    pass_curves: [],
    ...over,
  }) as never;

describe("grouping is never filtering", () => {
  it("keeps EVERY run, however many passes there are", () => {
    const runs = [
      run({ run_id: "a" }),
      run({ run_id: "b" }),
      run({ run_id: "c", pass_id: "20260101T000000Z-public-old" }),
      run({ run_id: "d", pass_id: null }),
    ];
    const groups = groupRuns(runs);
    const ids = groups.flatMap((g) => g.items.map((r) => r.run_id));
    expect(ids.sort()).toEqual(["a", "b", "c", "d"]);
    expect(groups.map((g) => g.items.length).reduce((x, y) => x + y, 0)).toBe(runs.length);
  });

  it("gives a run with no pass a home rather than dropping it", () => {
    const groups = groupRuns([run({ run_id: "lone", pass_id: null })]);
    expect(groups[0].passId).toBe(STANDALONE);
    expect(groups[0].calibration).toBe(false); // a standalone run is not a smoke
  });

  it("keeps every curve too, including one dial read twice", () => {
    const groups = groupSweeps([
      curve(),
      curve({ metric_slice: "key1_source=ratified_catalyst" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].items.map((c) => c.metric_slice).sort()).toEqual([
      "",
      "key1_source=ratified_catalyst",
    ]);
  });
});

describe("newest pass first", () => {
  it("orders by the pass id, which begins with its own UTC timestamp", () => {
    const groups = groupRuns([
      run({ pass_id: "20260101T000000Z-public-old" }),
      run({ pass_id: "20260918T032952Z-public-h5-phase-1" }),
      run({ pass_id: "20260301T000000Z-public-mid" }),
    ]);
    expect(groups.map((g) => g.passId)).toEqual([
      "20260918T032952Z-public-h5-phase-1",
      "20260301T000000Z-public-mid",
      "20260101T000000Z-public-old",
    ]);
  });

  it("sorts standalone runs last — they are not a pass", () => {
    const groups = groupRuns([run({ pass_id: null }), run({ pass_id: "20260918T000000Z-p" })]);
    expect(groups.map((g) => g.passId)).toEqual(["20260918T000000Z-p", STANDALONE]);
  });
});

describe("the calibration heuristic", () => {
  it("recognizes a smoke by its pass id or by its own text", () => {
    expect(looksLikeCalibration("20260918T032128Z-public-smoke-a", "")).toBe(true);
    expect(looksLikeCalibration("20260918T000000Z-public-p", "a calibration pass")).toBe(true);
    expect(looksLikeCalibration("20260918T000000Z-public-p", "Dry run before phase 1")).toBe(true);
  });

  it("does NOT claim a real pass is one", () => {
    expect(
      looksLikeCalibration(
        "20260918T032952Z-public-h5-phase-1",
        "H5: the conviction-side horizons are the timing lever",
      ),
    ).toBe(false);
    // A word merely CONTAINING a calibration word (e.g. "recalibrate") is not a smoke — word-boundary
    // matched, so this real pass is not collapsed.
    expect(
      looksLikeCalibration("20260918T034500Z-public-h9", "we recalibrate the exit horizons"),
    ).toBe(false);
  });
});

describe("what opens by default", () => {
  it("opens the newest REAL pass, not simply the newest one", () => {
    // The smokes run immediately BEFORE the pass they calibrate, so keying on recency alone would open
    // the smoke and collapse the experiment — exactly backwards.
    const groups = groupRuns([
      run({ pass_id: "20260918T033000Z-public-smoke-b", hypothesis: "smoke B" }),
      run({ pass_id: "20260918T032952Z-public-h5-phase-1" }),
    ]);
    expect(defaultOpen(groups)).toBe("20260918T032952Z-public-h5-phase-1");
  });

  it("opens the newest calibration when a store holds nothing else", () => {
    const groups = groupRuns([
      run({ pass_id: "20260918T032128Z-public-smoke-a", hypothesis: "smoke A" }),
      run({ pass_id: "20260917T000000Z-public-smoke-0", hypothesis: "smoke 0" }),
    ]);
    expect(defaultOpen(groups)).toBe("20260918T032128Z-public-smoke-a");
  });

  it("opens nothing when there is nothing", () => {
    expect(defaultOpen([])).toBeNull();
  });
});

describe("the group header", () => {
  it("says what the group IS — a count, never an outcome", () => {
    const [g] = groupRuns([run(), run({ run_id: "r2" })]);
    const label = groupLabel(g, "run");
    expect(label).toContain("20260918T032952Z-public-h5-phase-1");
    expect(label).toContain("2 runs");
    for (const forbidden of ["best", "winner", "top", "%"]) {
      expect(label.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("marks a calibration group so a reader knows why it is closed", () => {
    const [g] = groupRuns([run({ pass_id: "20260918T032128Z-public-smoke-a" })]);
    expect(groupLabel(g, "run")).toContain("calibration / smoke");
  });
});

// ARMS WITHIN A PASS (C). A pass's runs are a policy-arm × window matrix; grouping by config_hash recovers
// the arms, and the arm names the policy (its dials and their values) once, so the rows carry only the
// window. Same discipline as the pass grouping: it splits, it never filters, and it names what a run IS.

describe("arms within a pass", () => {
  it("splits a pass's runs by config_hash", () => {
    const arms = groupArms([
      run({ run_id: "b1", config_hash: "h-base", config_short: "base0000", dials_moved: [], dial_values: {} }),
      run({ run_id: "v1", config_hash: "h-var", config_short: "var00001", dials_moved: ["d"], dial_values: { d: 90 } }),
      run({
        run_id: "v2",
        config_hash: "h-var",
        config_short: "var00001",
        dials_moved: ["d"],
        dial_values: { d: 90 },
        window_start: "2024-01-01",
      }),
    ]);
    expect(arms).toHaveLength(2);
    const variant = arms.find((a) => a.configHash === "h-var")!;
    expect(variant.runs.map((r) => r.run_id)).toEqual(["v1", "v2"]); // both, newest window first
  });

  it("puts the baseline arm first, then variants by config_short", () => {
    const arms = groupArms([
      run({ config_hash: "h-z", config_short: "zzzzzzzz", dials_moved: ["d"], dial_values: { d: 1 } }),
      run({ config_hash: "h-base", config_short: "aaaaaaaa", dials_moved: [], dial_values: {} }),
      run({ config_hash: "h-m", config_short: "mmmmmmmm", dials_moved: ["d"], dial_values: { d: 2 } }),
    ]);
    expect(arms.map((a) => a.configShort)).toEqual(["aaaaaaaa", "mmmmmmmm", "zzzzzzzz"]);
    expect(arms[0].dialsMoved).toEqual([]); // the baseline leads, whatever its config_short sorts to
  });

  it("sorts an arm's runs newest window first", () => {
    const arms = groupArms([
      run({ run_id: "old", window_start: "2024-01-01" }),
      run({ run_id: "new", window_start: "2025-09-01" }),
    ]);
    expect(arms[0].runs.map((r) => r.run_id)).toEqual(["new", "old"]);
  });

  it("labels the baseline as production dials, never with an outcome", () => {
    const [arm] = groupArms([run({ dials_moved: [], dial_values: {} })]);
    expect(armLabel(arm)).toBe("baseline · production dials");
  });

  it("labels a variant with its dial VALUES, from dial_values", () => {
    const [arm] = groupArms([
      run({
        dials_moved: ["insider_core_alpha_liveness_days"],
        dial_values: { insider_core_alpha_liveness_days: 90 },
      }),
    ]);
    expect(armLabel(arm)).toBe("insider_core_alpha_liveness_days = 90");
  });

  it("falls back to the bare dial name when the value is absent (a pre-backfill run)", () => {
    const [arm] = groupArms([
      run({ dials_moved: ["insider_core_alpha_liveness_days"], dial_values: {} }),
    ]);
    expect(armLabel(arm)).toBe("insider_core_alpha_liveness_days");
  });

  it("takes an arm's values from whichever run carries them, in a mixed store", () => {
    // Before the operator runs the backfill, a config can have an old value-less row beside a new one that
    // carries its value; the arm should still be labeled from the run that has it.
    const arms = groupArms([
      run({
        run_id: "pre",
        config_hash: "h",
        config_short: "s",
        dials_moved: ["d"],
        dial_values: {},
        window_start: "2025-01-01",
      }),
      run({
        run_id: "post",
        config_hash: "h",
        config_short: "s",
        dials_moved: ["d"],
        dial_values: { d: 90 },
        window_start: "2024-01-01",
      }),
    ]);
    expect(arms).toHaveLength(1);
    expect(armLabel(arms[0])).toBe("d = 90");
  });

  it("formats a null value as 'none' and an array by join", () => {
    const [a] = groupArms([run({ dials_moved: ["x"], dial_values: { x: null } })]);
    expect(armLabel(a)).toBe("x = none");
    const [b] = groupArms([run({ dials_moved: ["y"], dial_values: { y: ["core", "flip"] } })]);
    expect(armLabel(b)).toBe("y = core, flip");
  });
});
