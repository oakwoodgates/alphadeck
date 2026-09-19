import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Backtest } from "../Backtest";

// The /backtest page. What is pinned here is the READING ORDER and the absences, because both are the
// design: the five labels are backend-authored and rendered verbatim, the pooled view comes first, the
// per-thesis ledger is folded away below it, and a stack with no run store says one quiet line rather
// than rendering an empty shell or an error.
//
// No canvas assertions anywhere — the drawer mounts the scorecard WITHOUT an asof, so the lazy price
// chart never loads (and asserting on a canvas is how the Scoreboard's chart tests went green while
// showing nothing).

const runs: { data: unknown; isLoading: boolean; error: unknown } = {
  data: null,
  isLoading: false,
  error: null,
};
const run: { data: unknown; isLoading: boolean; error: unknown } = {
  data: null,
  isLoading: false,
  error: null,
};
const sweep: { data: unknown; isLoading: boolean; error: unknown } = {
  data: null,
  isLoading: false,
  error: null,
};
const sweeps: { data: unknown; isLoading: boolean; error: unknown } = {
  data: null,
  isLoading: false,
  error: null,
};

vi.mock("../../api/hooks", () => ({
  useBacktestRuns: () => runs,
  useBacktestRun: () => run,
  useBacktestSweep: () => sweep,
  useBacktestSweeps: () => sweeps,
  // the scorecard's live reads — never reached here (no asof), stubbed so the import resolves
  useEpisodePriceWindow: () => ({ data: undefined }),
  useDisplaySignals: () => ({ data: undefined }),
  useWorkbenchScored: () => ({ data: undefined }),
}));

const LABELS = [
  "public clock — facts enter when they became public, not when this system ingested them",
  "counterfactual universe — these baskets were authored in 2026 over names that had already moved",
  "survivorship — the roster is today's, not history's",
  "adjusted closes — splits and dividends are folded into the tape",
  "a recompute, never the record; an operator-ratified fact is modeled as public on its announcement date, so the ratification itself is hindsight",
];

const EPISODE = {
  thesis_id: "t1",
  security_id: "s1",
  ticker: "DEVCO",
  company_name: "Dev Co",
  is_headline: true,
  theme_armed: false,
  arm_date: "2026-01-05",
  dearm_date: null,
  close_reason: "window_end",
  status: "open",
  matured: true,
  censored_start: false,
  triggers_at_arm: [],
  risk_events: [],
  transitions: [],
  exit_by: "2026-03-05",
  forward_return: 0.12,
  peak_return: 0.2,
  path: [100, 112],
  truncated: false,
  tape_behind_market: false,
  insufficient_prices: false,
  operator: null,
};

const RUN = {
  available: true,
  run_id: "20260917-abcd1234-h5",
  labels: LABELS,
  manifest: {
    schema_version: 1,
    run_id: "20260917-abcd1234-h5",
    created_at: "2026-09-17T04:12:07+00:00",
    code_sha: "a".repeat(40),
    window_start: "2025-09-01",
    window_end: "2026-09-14",
    clock: "record",
    known_at_mode: "pin",
    pin: "2026-09-15T00:00:00+00:00",
    config_hash: "b".repeat(64),
    config_short: "bbbbbbbb",
    config_canonical_json: "{}",
    config: {},
    overlay_diff: { insider_core_alpha_liveness_days: { default: 180, run: 90 } },
    theses: [{ thesis_id: "t1", name: "A thesis", basket_size: 1, roster_hash: "x", roster_source: "live_fallback", fallback_days: 3, total_days: 3 }],
    mirror: { hash: "c".repeat(64), tables: {}, excluded_tables: [], blind_detectors: [] },
    workers: 1,
    null_draws: 50,
    null_seed: "seed",
    hypothesis: "H5: the exit_by horizon is the timing lever",
    decision_rule: "adopt only on a plateau with sign agreement",
    timings: {},
    n_episodes: 1,
    n_theses: 1,
  },
  pooled: {
    n_episodes: 1,
    n_scoreable: 1,
    null_draws: 50,
    null_seed: "seed",
    min_n: 5,
    banner: "POOLED ACROSS THESES — the unit is the ALGORITHM, never the thesis.",
    metrics: [
      {
        name: "arm_timing_forward_return",
        claim: "timing (the flaw patched)",
        actual: { n: 41, median: 0.041, mean: 0.05 },
        excess: { n: 41, median: 0.012, mean: 0.01 },
        vs_timing: { n: 2050, median: 0.009, mean: 0.01 },
        vs_name: { n: 2050, median: 0.02, mean: 0.02 },
        insufficient_n: false,
        note: "read the three together",
      },
    ],
    slices: [
      {
        key: {
          key1_source: "insider",
          confirmation_grade: "core",
          co_arm_bucket: "alone",
          close_reason: "window_end",
        },
        n: 19,
        actual: { n: 19, median: 0.05, mean: 0.05 },
        excess: { n: 19, median: 0.01, mean: 0.01 },
        vs_timing: { n: 950, median: 0.004, mean: 0.0 },
        vs_name: { n: 950, median: 0.02, mean: 0.02 },
        insufficient_n: false,
      },
    ],
    diagnostics: {
      n_episodes: 41,
      n_scoreable: 41,
      key1_source_mix: { insider: 30, theme: 11 },
      confirmation_grade_mix: { core: 41 },
      co_arm_bucket_mix: { alone: 8, "8+": 33 },
      close_reason_mix: { window_end: 41 },
      entry_grade_mix: { core: 41 },
      pct_armed_with_a_co_member: 0.82,
      widest_single_session_group: 13,
      timing_candidate_sessions: 250,
    },
  },
  episodes: [
    {
      thesis_id: "t1",
      security_id: "s1",
      arm_date: "2026-01-05",
      co_arm_count: 3,
      armed_count_that_night: 9,
      co_arm_bucket: "4-7",
      key1_source: "insider",
      key1_sources: ["insider"],
      confirmation_grade: "core",
    },
  ],
  ledger: {
    banner: "RUN LEDGER — this run's episodes, grouped by thesis. never the record. NOT a ranking.",
    min_n: 5,
    n_theses: 1,
    n_episodes: 1,
    n_censored: 0,
    n_eligible: 1,
    metrics: [],
    theses: [
      { thesis_id: "t1", name: "A thesis", ticker: null, basket_size: 1, episodes: [EPISODE] },
    ],
  },
};

const RUNS = {
  available: true,
  runs: [
    {
      run_id: "20260917-abcd1234-h5",
      created_at: "2026-09-17T04:12:07+00:00",
      hypothesis: "H5: the exit_by horizon is the timing lever",
      config_short: "bbbbbbbb",
      config_hash: "b".repeat(64),
      clock: "record",
      known_at_mode: "pin",
      window_start: "2025-09-01",
      window_end: "2026-09-14",
      n_theses: 1,
      n_episodes: 1,
      dials_moved: ["insider_core_alpha_liveness_days"],
    },
  ],
  dial_trials: { insider_core_alpha_liveness_days: 4 },
};

function renderPage(
  over: { runs?: unknown; run?: unknown; sweep?: unknown; sweeps?: unknown } = {},
) {
  Object.assign(runs, { data: "runs" in over ? over.runs : RUNS, isLoading: false, error: null });
  Object.assign(run, { data: "run" in over ? over.run : RUN, isLoading: false, error: null });
  Object.assign(sweep, { data: "sweep" in over ? over.sweep : null, isLoading: false, error: null });
  Object.assign(sweeps, {
    data: "sweeps" in over ? over.sweeps : null,
    isLoading: false,
    error: null,
  });
  const onSelect = vi.fn();
  const onSelectRun = vi.fn();
  const utils = render(
    <Backtest runId={null} onSelectRun={onSelectRun} onSelect={onSelect} />,
  );
  return { onSelect, onSelectRun, ...utils };
}

beforeEach(() => {
  Object.assign(runs, { data: null, isLoading: false, error: null });
  Object.assign(run, { data: null, isLoading: false, error: null });
  Object.assign(sweep, { data: null, isLoading: false, error: null });
  Object.assign(sweeps, { data: null, isLoading: false, error: null });
});

describe("Backtest — absence", () => {
  it("says ONE quiet line when the stack has no run store (prod's normal state)", () => {
    renderPage({ runs: { available: false, runs: [], dial_trials: {} }, run: null });
    expect(screen.getByText(/No backtest runs on this stack/)).toBeInTheDocument();
    // not an error, and not an empty shell pretending to be a page
    expect(screen.queryByText(/unreachable/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /Pooled analysis/ })).not.toBeInTheDocument();
  });

  it("distinguishes an empty store from a missing one", () => {
    renderPage({ runs: { available: true, runs: [], dial_trials: {} }, run: null });
    expect(screen.getByText(/holds no completed run yet/)).toBeInTheDocument();
  });

  it("reads a stale run link as 'not here' rather than as an error", () => {
    renderPage({ run: { available: false, run_id: "gone" } });
    expect(screen.getByText(/That run is not on this stack/)).toBeInTheDocument();
  });

  it("says a run predating the nulls has nothing to read its returns against", () => {
    renderPage({ run: { ...RUN, pooled: null } });
    expect(screen.getByText(/predates the null models/)).toBeInTheDocument();
  });
});

describe("Backtest — the five labels", () => {
  it("renders the BACKEND's label strings verbatim, composing nothing", () => {
    renderPage();
    const list = screen.getByRole("list", { name: /What this surface is, and is not/ });
    const items = [...list.querySelectorAll("li")].map((li) => li.textContent);
    expect(items).toEqual(LABELS);
  });

  it("carries the hindsight-of-ratification caveat, not just the easy three", () => {
    renderPage();
    expect(screen.getByText(/the ratification itself is hindsight/)).toBeInTheDocument();
  });
});

describe("Backtest — reading order", () => {
  it("puts the POOLED panel above the per-thesis ledger", () => {
    // Per-thesis outcomes are the leaderboard trap (#4): the algorithm-level view is what the page
    // opens on, and the thesis rows are something the reader has to go and get.
    const { container } = renderPage();
    const text = container.textContent ?? "";
    const pooledAt = text.indexOf("Pooled — the algorithm");
    const ledgerAt = text.indexOf("episodes — by thesis");
    expect(pooledAt).toBeGreaterThan(-1);
    expect(ledgerAt).toBeGreaterThan(-1);
    expect(pooledAt).toBeLessThan(ledgerAt);
  });

  it("leaves the per-thesis ledger COLLAPSED, and a closed panel still declares its counts", () => {
    renderPage();
    const head = screen.getByRole("button", { name: /This run's episodes/ });
    expect(head).toHaveAttribute("aria-expanded", "false");
    expect(head.textContent).toContain("not a ranking");
    expect(screen.queryByText("A thesis")).not.toBeInTheDocument();
  });

  it("opens the ledger on demand and renders the episode row through the shared components", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /This run's episodes/ }));
    expect(screen.getByText("A thesis")).toBeInTheDocument();
    expect(screen.getByText("DEVCO")).toBeInTheDocument();
    expect(screen.getByText("+12.0%")).toBeInTheDocument();
  });
});

describe("Backtest — the pooled panel", () => {
  it("shows both nulls and the excess BESIDE the actual, never behind a hover", () => {
    renderPage();
    const region = screen.getByRole("region", { name: /Pooled analysis/ });
    const text = region.textContent ?? "";
    for (const label of ["actual", "vs equal-weight basket", "vs timing null", "vs name null"]) {
      expect(text).toContain(label);
    }
    expect(text).toContain("+4.1%"); // actual
    expect(text).toContain("+0.9%"); // vs timing
  });

  it("renders a PRE-B report exactly as it always did — no permanent em dash column", () => {
    // This fixture carries no basket median, which is what every run before B looks like. A column
    // that is always "—" is noise, so the pair appears only where there is a pair.
    renderPage();
    const text = screen.getByRole("region", { name: /Pooled analysis/ }).textContent ?? "";
    expect(text).not.toContain("vs the typical name");
    expect(text).not.toContain("TWO EXCESS FIGURES");
  });

  it("names NO thesis anywhere in the pooled block — by construction, not by convention", () => {
    // The payload carries no thesis identifier at all (a backend test walks it); this is the surface
    // half of the same rule, so a field that started carrying one would fail here too.
    renderPage();
    const region = screen.getByRole("region", { name: /Pooled analysis/ });
    expect(region.textContent?.toLowerCase()).not.toContain("a thesis");
    expect(region.innerHTML).not.toContain("t1");
  });

  it("renders the firing diagnostics with no outcome in them", () => {
    renderPage();
    const region = screen.getByRole("region", { name: /Pooled analysis/ });
    expect(region.textContent).toContain("82%");
    expect(region.textContent).toContain("widest single session");
  });
});

describe("Backtest — the manifest", () => {
  it("shows which dials moved, and how many runs have moved each one", () => {
    renderPage();
    // Scoped to the manifest card: the dial name now also appears in the run picker's arm header (C),
    // so an unscoped query would match both. This test is about the manifest, so it looks there.
    const card = screen.getByRole("region", { name: /What produced this run/ });
    expect(within(card).getByText("insider_core_alpha_liveness_days")).toBeInTheDocument();
    expect(within(card).getByText("4")).toBeInTheDocument(); // the trial count for that dial
  });

  it("shows the pre-registration when there is one", () => {
    renderPage();
    expect(screen.getByText(/the exit_by horizon is the timing lever/)).toBeInTheDocument();
    expect(screen.getByText(/Pre-registered/)).toBeInTheDocument();
  });

  it("omits the pre-registration block entirely on a bare exploratory run", () => {
    // An empty "Hypothesis: —" invites reading one in after the fact.
    renderPage({ run: { ...RUN, manifest: { ...RUN.manifest, hypothesis: null } } });
    expect(screen.queryByText(/Pre-registered/)).not.toBeInTheDocument();
  });

  it("says what the run could not see", () => {
    renderPage();
    expect(screen.getByText(/No fact table was excluded/)).toBeInTheDocument();
  });
});

describe("Backtest — the sweep", () => {
  it("is absent until a sweep has run", () => {
    renderPage();
    expect(screen.queryByRole("region", { name: /Dial sweep/ })).not.toBeInTheDocument();
  });

  it("names a band, never a winner", () => {
    renderPage({
      sweep: {
        available: true,
        labels: LABELS,
        sweep: {
          dial_names: ["insider_core_alpha_liveness_days"],
          window_start: "2025-09-01",
          window_end: "2026-09-14",
          subwindows: 2,
          mirror_hash: "c".repeat(64),
          baseline_config_short: "bbbbbbbb",
          points: [
            { run_id: "r1", dials: { d: 90 }, metric: 0.01, sign_agreement: true },
            { run_id: "r2", dials: { d: 180 }, metric: 0.09, sign_agreement: true },
          ],
          plateau: [0, 1],
          banner: "a curve, not a winner",
        },
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /Dial sweep/ }));
    const region = screen.getByRole("region", { name: /Dial sweep/ });
    // The loud VERDICT distills the plateau result — a band by width, never a best point (issue #3).
    expect(region.textContent).toContain("Band: 2 settings wide");
    // The TABLE is where a ranking would show up — the backend's own banner is allowed to say the
    // word ("a curve, not a winner"), and scanning the whole region would catch that disclaimer
    // rather than the thing it disclaims.
    const table = (region.querySelector("table")?.textContent ?? "").toLowerCase();
    expect(table).not.toBe("");
    for (const forbidden of ["winner", "optimal", "argmax", "best", "rank"]) {
      expect(table).not.toContain(forbidden);
    }
    // ...and the points are in the sweep's own order, not re-sorted by the metric
    const rows = [...region.querySelectorAll("tbody tr")].map((r) => r.textContent ?? "");
    expect(rows[0]).toContain("d=90");
    expect(rows[1]).toContain("d=180");
  });

  it("folds the methodology behind 'How to read this' — default collapsed, nothing deleted", () => {
    renderPage({
      sweep: {
        available: true,
        labels: LABELS,
        sweep: {
          dial_names: ["insider_core_alpha_liveness_days"],
          window_start: "2025-09-01",
          window_end: "2026-09-14",
          subwindows: 2,
          mirror_hash: "c".repeat(64),
          baseline_config_short: "bbbbbbbb",
          points: [
            { run_id: "r1", dials: { d: 90 }, metric: 0.01, sign_agreement: true },
            { run_id: "r2", dials: { d: 180 }, metric: 0.09, sign_agreement: true },
          ],
          plateau: [0, 1],
          banner: "a curve, not a winner",
        },
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /Dial sweep/ }));
    const region = screen.getByRole("region", { name: /Dial sweep/ });
    // Collapsed by default: the backend banner and the full plateau sentence are one click away.
    expect(region.textContent).not.toContain("a curve, not a winner");
    expect(region.textContent).not.toContain("A plateau 2 points wide");
    // ...and one click reveals them verbatim — the guardrail caveats are folded, never deleted.
    fireEvent.click(screen.getByRole("button", { name: /How to read this/ }));
    const after = screen.getByRole("region", { name: /Dial sweep/ }).textContent ?? "";
    expect(after).toContain("a curve, not a winner"); // the backend banner, rendered verbatim
    expect(after).toContain("A plateau 2 points wide"); // the full methodology sentence
  });
});

// D — A PASS'S CURVES, AND A REGISTRY THAT STAYS READABLE AFTER ONE.
//
// A windowed pass writes dozens of runs and one curve per dial. `sweep.json` holds only the last curve,
// so five of six were unreachable the instant the sixth landed; and a flat registry of 225 rows with the
// smoke runs scattered through it is not a control anyone can use. Grouping is NEVER filtering: every row
// is on screen, the groups only decide what is expanded.

const CURVE_REF = {
  pass_id: "20260918T032952Z-public-h5-phase-1",
  dial: "insider_core_alpha_liveness_days",
  dial_names: ["insider_core_alpha_liveness_days"],
  metric_slice: "",
  hypothesis: "H5: the conviction-side horizons are the timing lever",
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
  pass_curves: ["insider_core_alpha_liveness_days"],
};

describe("Backtest — the curve switcher (D)", () => {
  it("lists a pass's curves and says which rule each band was keyed on", () => {
    renderPage({
      sweeps: {
        available: true,
        sweeps: [CURVE_REF, { ...CURVE_REF, dial: "activist_13d_liveness_days", plateau_width: 1 }],
        labels: [],
      },
    });
    const text = screen.getByRole("group", { name: /Sweep curves/ }).textContent ?? "";
    expect(text).toContain("insider_core_alpha_liveness_days");
    expect(text).toContain("activist_13d_liveness_days");
    expect(text).toContain("keyed on strict_sign_agreement");
    expect(text).toContain("band 2 wide");
    expect(text).toContain("no band");
  });

  it("marks a SLICED curve so it can never be mistaken for the pooled one", () => {
    renderPage({
      sweeps: {
        available: true,
        sweeps: [{ ...CURVE_REF, metric_slice: "key1_source=ratified_catalyst" }],
        labels: [],
      },
    });
    expect(screen.getByRole("group", { name: /Sweep curves/ }).textContent).toContain(
      "key1_source=ratified_catalyst",
    );
  });

  it("renders no switcher at all when the store holds no kept curve", () => {
    renderPage({ sweeps: { available: true, sweeps: [], labels: [] } });
    expect(screen.queryByRole("group", { name: /Sweep curves/ })).not.toBeInTheDocument();
  });

  it("collapses a CALIBRATION pass and opens the real one", () => {
    renderPage({
      sweeps: {
        available: true,
        sweeps: [
          { ...CURVE_REF, pass_id: "20260918T032128Z-public-smoke-a", hypothesis: "smoke A" },
          CURVE_REF,
        ],
        labels: [],
      },
    });
    const region = screen.getByRole("group", { name: /Sweep curves/ });
    const heads = [...region.querySelectorAll("button[aria-expanded]")];
    const smoke = heads.find((h) => h.textContent?.includes("smoke-a"));
    const real = heads.find((h) => h.textContent?.includes("phase-1"));
    expect(smoke?.getAttribute("aria-expanded")).toBe("false");
    expect(real?.getAttribute("aria-expanded")).toBe("true");
    expect(smoke?.textContent).toContain("1 curve");
    expect(smoke?.textContent).toContain("calibration / smoke");
  });
});

describe("Backtest — the run picker groups by pass (D)", () => {
  const runOf = (over: Record<string, unknown>) => ({ ...RUNS.runs[0], ...over });
  const twoPasses = {
    available: true,
    runs: [
      runOf({ run_id: "r-real", pass_id: "20260918T032952Z-public-h5-phase-1" }),
      runOf({
        run_id: "r-smoke",
        pass_id: "20260918T032128Z-public-smoke-a",
        hypothesis: "smoke",
      }),
    ],
    dial_trials: {},
  };

  it("opens the newest REAL pass and collapses the smokes, counting both", () => {
    renderPage({ runs: twoPasses });
    const region = screen.getByRole("group", { name: "Runs" });
    const heads = [...region.querySelectorAll("button[aria-expanded]")];
    const real = heads.find((h) => h.textContent?.includes("phase-1"));
    const smoke = heads.find((h) => h.textContent?.includes("smoke-a"));
    expect(real?.getAttribute("aria-expanded")).toBe("true");
    expect(smoke?.getAttribute("aria-expanded")).toBe("false");
    expect(smoke?.textContent).toContain("1 run");
    expect(screen.queryByText("r-smoke")).not.toBeInTheDocument();
    expect(screen.getByText("r-real")).toBeInTheDocument();
  });

  it("expands a collapsed pass in one click, and nothing was ever dropped", () => {
    renderPage({ runs: twoPasses });
    const region = screen.getByRole("group", { name: "Runs" });
    const smoke = [...region.querySelectorAll("button[aria-expanded]")].find((h) =>
      h.textContent?.includes("smoke-a"),
    );
    fireEvent.click(smoke as Element);
    expect(screen.getByText("r-smoke")).toBeInTheDocument();
  });
});
