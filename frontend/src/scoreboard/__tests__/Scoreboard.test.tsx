import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Scoreboard } from "../Scoreboard";

// The ledger view over a fixture payload: groups + rows render, the marks are exceptions, the
// metrics strip stays quiet under the gate, archived folds closed (never dropped), and a row
// click drills into the Cockpit.

const fx: { data: unknown; isLoading: boolean; error: unknown } = {
  isLoading: false,
  error: null,
  data: null,
};

vi.mock("../../api/hooks", () => ({
  useScoreboard: () => fx,
  useScoreboardReplay: () => ({ data: null, isLoading: false, error: null }),
  // the drawer's sparkline hook — stubbed no-data so the scorecard renders its quiet "no price path"
  // line (and never reaches real lightweight-charts) when a test opens the drawer
  useEpisodePriceWindow: () => ({ data: undefined, isLoading: false, isError: false }),
}));

const EP = {
  thesis_id: "t-hims",
  security_id: "s1",
  ticker: "HIMS",
  is_headline: true,
  theme_armed: false,
  arm_date: "2026-07-10",
  dearm_date: null,
  close_reason: "window_end",
  status: "open",
  matured: false,
  censored_start: true,
  arm_ingest_fresh: null,
  freeze_era: false,
  thaw_lag_days: null,
  ingest_flagged: false,
  ingest_note: null,
  verdict: "core_entry",
  entry_grade: "core",
  conviction_grade: "core",
  confidence: 0.97,
  exit_by: "2026-11-22",
  arm_until: null,
  warm_date: null,
  triggers_at_arm: [
    { label: "1 insider bought $1.17M open-market", kind: "insider", grade: "core", ticker: "HIMS", sources: [] },
  ],
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
  insufficient_prices: true,
  operator: null,
};

const PAYLOAD = {
  asof: "2026-07-11",
  generated_at: "2026-07-11T12:00:00Z",
  summary: {
    n_theses: 3,
    n_with_record: 3,
    n_episodes: 1,
    n_open: 1,
    n_matured: 0,
    n_censored: 1,
    n_ingest_flagged: 0,
    n_eligible: 0,
    n_takes: 1,
    n_passes: 0,
    n_overrides: 1,
    n_voided: 0,
    // the maturity horizon (2e) — the open EP's exit_by lies ahead; no projection reachable
    next_maturity: "2026-11-22",
    n_maturing_30d: 0,
    projected_min_n_date: null,
    record_began: "2026-07-10",
    banner: "FORWARD RECORD, NOT A CLAIM — record began 2026-07-10; 0 episodes eligible…",
    min_n: 5,
    metrics: [
      { name: "arm_timing_forward_return", claim: "timing", n: 0, insufficient_n: true, summary: {}, detail: [], note: "" },
      { name: "false_arm_rate", claim: "precision", n: 0, insufficient_n: true, summary: {}, detail: [], note: "" },
    ],
    // record freshness (2a) — a current, live view by default (asof 2026-07-11 == today)
    record_edge: "2026-07-11",
    expected_asof: "2026-07-11",
    days_behind: 0,
    stale: false,
    today: "2026-07-11",
  },
  theses: [
    {
      thesis_id: "t-hims",
      name: "HIMS — insider conviction",
      ticker: "HIMS",
      basket_size: 1,
      archived: false,
      first_call_asof: "2026-07-10",
      last_call_asof: "2026-07-10",
      current_state: "armed",
      current_verdict: "core_entry",
      warming_since: null,
      episodes: [EP],
      operator_spans: [],
      decision_anomaly: null,
      record_error: null,
    },
    {
      thesis_id: "t-5b",
      name: "5b draft check",
      ticker: "J",
      basket_size: 1,
      archived: false,
      first_call_asof: "2026-07-10",
      last_call_asof: "2026-07-10",
      current_state: "incubating",
      current_verdict: "watching",
      warming_since: null,
      episodes: [],
      operator_spans: [
        {
          take_id: "d1",
          take_date: "2026-07-11",
          security_id: "s-j",
          ticker: "J",
          thesis_level: false,
          call_state_at_take: "incubating",
          call_verdict_at_take: "watching",
          override: true,
          close_id: null,
          close_date: null,
          running: true,
          entry_price: 125.0,
          entry_inferred: false,
          exit_price: 125.84,
          exit_inferred: true,
          exit_date: null,
          operator_return: 0.00672,
          reason: null,
        },
      ],
      decision_anomaly: null,
      record_error: null,
    },
    {
      thesis_id: "t-arch",
      name: "Nuclear #110",
      ticker: null,
      basket_size: 4,
      archived: true,
      first_call_asof: "2026-07-10",
      last_call_asof: "2026-07-10",
      current_state: "armed",
      current_verdict: "starter_entry",
      warming_since: null,
      episodes: [{ ...EP, thesis_id: "t-arch", ticker: "J" }],
      operator_spans: [],
      decision_anomaly: null,
      record_error: null,
    },
  ],
};

// A SCORED episode (real forward bars → a peak to judge) for the Timing view (Slice 2). The base EP
// above is still-awaiting (insufficient_prices), so it exercises the honest "—" path; this one carries
// the computed timing lens.
const SCORED_EP = {
  ...EP,
  security_id: "s-matr",
  ticker: "MATR",
  status: "closed",
  matured: true,
  censored_start: false,
  insufficient_prices: false,
  exit_date: "2026-08-20",
  forward_return: 0.123,
  peak_return: 0.204,
  peak_date: "2026-08-10",
  exit_vs_peak_days: 7,
  triggers_at_arm: [
    { label: "50d breakout", kind: "technical_breakout", grade: "flip", ticker: "MATR", sources: [] },
  ],
};

// one thesis, two episodes: the scored MATR + the still-awaiting HIMS — lets Timing assert both the
// computed lens AND the honest "—" for an episode with no forward bar yet.
const TIMING_PAYLOAD = {
  ...PAYLOAD,
  theses: [
    {
      ...PAYLOAD.theses[0],
      name: "Timing check",
      episodes: [SCORED_EP, EP],
      operator_spans: [],
    },
  ],
};

function renderBoard(over: Partial<typeof fx> = {}) {
  Object.assign(fx, { data: PAYLOAD, isLoading: false, error: null }, over);
  const onSelect = vi.fn();
  const utils = render(
    <Scoreboard
      asof="2026-07-11"
      onAsofChange={() => {}}
      onBack={() => {}}
      onOpenWorkbench={() => {}}
      onSelect={onSelect}
    />,
  );
  return { onSelect, ...utils };
}

describe("Scoreboard", () => {
  it("renders the banner, counts, and the ONE quiet gated-metrics line (no metric cards)", () => {
    renderBoard();
    expect(screen.getByText(/FORWARD RECORD, NOT A CLAIM/)).toBeInTheDocument();
    expect(screen.getByText("1 episodes")).toBeInTheDocument();
    expect(screen.getByText("1 overrides")).toBeInTheDocument();
    expect(screen.getByText(/2 of 2 metrics await n ≥ 5/)).toBeInTheDocument();
    expect(screen.queryByText("arm timing forward return")).not.toBeInTheDocument();
  });

  it("renders the censored open episode with its marks and the honest operator gap", () => {
    renderBoard();
    expect(screen.getAllByText("OPEN").length).toBeGreaterThan(0);
    expect(screen.getAllByText("CENSORED").length).toBeGreaterThan(0);
    expect(screen.getAllByText("awaiting first bar").length).toBeGreaterThan(0);
    expect(screen.getAllByText("no decision logged").length).toBeGreaterThan(0);
    expect(screen.getAllByText("insider").length).toBeGreaterThan(0); // the WHY chip
  });

  it("renders the override span with its frozen stance and running return", () => {
    renderBoard();
    expect(screen.getByText("OVERRIDE")).toBeInTheDocument();
    expect(screen.getByText(/platform said watching/)).toBeInTheDocument();
    expect(screen.getByText("+0.7%")).toBeInTheDocument();
  });

  it("folds archived groups closed by default — present with the count, never dropped", () => {
    renderBoard();
    const archived = screen.getByRole("button", { name: /Nuclear #110/ });
    expect(archived).toHaveAttribute("aria-expanded", "false");
    expect(archived.textContent).toContain("· 1"); // the count stays visible while folded
    // its episode row is not rendered until opened
    expect(screen.getAllByText(/awaiting first bar/).length).toBe(1);
    fireEvent.click(archived);
    expect(screen.getAllByText(/awaiting first bar/).length).toBe(2);
  });

  it("drills into the Cockpit on row click — carrying the clicked NAME for the ?name= deep link", () => {
    const { onSelect } = renderBoard();
    // an episode row: thesis id + its ticker as the name key
    fireEvent.click(screen.getByText("awaiting first bar").closest("tr")!);
    expect(onSelect).toHaveBeenCalledWith("t-hims", "HIMS");
    // a span row: same contract (this one has a name)
    fireEvent.click(screen.getByText(/platform said watching/).closest("tr")!);
    expect(onSelect).toHaveBeenCalledWith("t-5b", "J");
  });

  it("falls back to security_id as the name key when the episode's ticker is unresolved", () => {
    const { onSelect } = renderBoard({
      data: {
        ...PAYLOAD,
        theses: [{ ...PAYLOAD.theses[0], episodes: [{ ...EP, ticker: null }] }],
      },
    });
    fireEvent.click(screen.getByText("awaiting first bar").closest("tr")!);
    expect(onSelect).toHaveBeenCalledWith("t-hims", "s1");
  });

  it("renders the honest empty state when the record has nothing yet", () => {
    renderBoard({
      data: {
        ...PAYLOAD,
        summary: { ...PAYLOAD.summary, n_episodes: 0, n_takes: 0, n_overrides: 0, n_censored: 0, n_open: 0 },
        theses: [],
      },
    });
    expect(screen.getByText(/No arm episodes on the record yet/)).toBeInTheDocument();
  });

  it("surfaces a record error visibly inside its group", () => {
    renderBoard({
      data: {
        ...PAYLOAD,
        theses: [
          { ...PAYLOAD.theses[1], operator_spans: [], record_error: "ValidationError: bogus_key" },
        ],
      },
    });
    expect(screen.getByText(/record error: ValidationError/)).toBeInTheDocument();
  });

  it("2a: shows the quiet 'record current' freshness line on the live view", () => {
    const { container } = renderBoard(); // asof 2026-07-11 == today, stale false
    const line = container.querySelector(".sb-fresh");
    expect(line?.textContent).toContain("record current");
    expect(line?.textContent).toContain("2026-07-11");
    expect(container.querySelector(".sb-stale")).toBeNull();
  });

  it("2a: goes loud when the record is stale — 'N expected run(s) behind'", () => {
    const { container } = renderBoard({
      data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, stale: true, days_behind: 2 } },
    });
    const line = container.querySelector(".sb-stale");
    expect(line?.textContent).toContain("expected run(s) behind");
    expect(line?.textContent).toMatch(/· 2 expected/); // the days_behind count
    expect(container.querySelector(".sb-fresh")).toBeNull();
  });

  it("2a: a never-begun record reads quiet, not an alarm", () => {
    const { container } = renderBoard({
      data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, record_edge: null } },
    });
    expect(container.querySelector(".sb-fresh")?.textContent).toMatch(/hasn.t begun/);
    expect(container.querySelector(".sb-stale")).toBeNull();
  });

  it("2a: suppresses the freshness line on a scrubbed-past view (asof < today)", () => {
    // even a STALE record is hidden when viewing the past — staleness is a "now" fact (decision #2)
    const { container } = renderBoard({
      data: {
        ...PAYLOAD,
        summary: { ...PAYLOAD.summary, today: "2026-07-20", stale: true, days_behind: 1 },
      },
    });
    // asof 2026-07-11 < today 2026-07-20 → neither tone renders
    expect(container.querySelector(".sb-fresh, .sb-stale")).toBeNull();
  });

  it("2e: renders the maturity-horizon countdown beside the metrics gate", () => {
    const { container } = renderBoard({
      data: {
        ...PAYLOAD,
        summary: {
          ...PAYLOAD.summary,
          next_maturity: "2026-07-18",
          n_maturing_30d: 5,
          projected_min_n_date: "2026-08-31",
        },
      },
    });
    const line = container.querySelector(".sb-horizon");
    expect(line?.textContent).toBe(
      "next episode matures 2026-07-18 · 5 mature within 30d · first metric could clear n ≥ 5 around 2026-08-31",
    );
    expect(line?.getAttribute("title")).toContain("projection over currently-recorded episodes");
  });

  it("2e: no horizon line when nothing lies ahead (null next_maturity)", () => {
    const { container } = renderBoard({
      data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, next_maturity: null } },
    });
    expect(container.querySelector(".sb-horizon")).toBeNull();
  });

  it("2d: the ingest-flagged count rides only when > 0 (honest loudness)", () => {
    renderBoard(); // the fixture's n_ingest_flagged is 0
    expect(screen.queryByText(/ingest-flagged/)).not.toBeInTheDocument();
    renderBoard({
      data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, n_ingest_flagged: 1 } },
    });
    expect(screen.getByText("1 ingest-flagged")).toBeInTheDocument();
  });

  it("the ⤢ control opens the scorecard drawer WITHOUT navigating to the Cockpit", () => {
    const { container, onSelect } = renderBoard();
    expect(container.querySelector(".drawer-panel")).toBeNull(); // closed by default
    fireEvent.click(screen.getByRole("button", { name: "open scorecard for HIMS" }));
    // the distinct affordance stopped the bubble — the row's click-to-Cockpit never fired
    expect(onSelect).not.toHaveBeenCalled();
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(panel).not.toBeNull();
    expect(within(panel).getByText("The move")).toBeInTheDocument(); // the scorecard mounted
    expect(panel.querySelector(".drawer-title")?.textContent).toContain("HIMS"); // drawer title
  });

  it("the drawer is reversible: ✕, backdrop, and Escape each close it, the ledger untouched", () => {
    const { container } = renderBoard();
    const open = () =>
      fireEvent.click(screen.getByRole("button", { name: "open scorecard for HIMS" }));
    const isOpen = () => container.querySelector(".drawer-panel") != null;

    open();
    fireEvent.click(screen.getByRole("button", { name: "close drawer" }));
    expect(isOpen()).toBe(false);

    open();
    fireEvent.click(container.querySelector(".drawer-backdrop") as HTMLElement);
    expect(isOpen()).toBe(false);

    open();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(isOpen()).toBe(false);

    // the ledger survives every exit — the table element never went away
    expect(container.querySelector("table.sb-ledger")).not.toBeNull();
  });

  it("the expand toggle flips the drawer's full-width class and back", () => {
    const { container } = renderBoard();
    fireEvent.click(screen.getByRole("button", { name: "open scorecard for HIMS" }));
    expect(container.querySelector(".drawer-panel.expanded")).toBeNull(); // default width
    const toggle = screen.getByRole("button", { name: "expand drawer to full width" });
    fireEvent.click(toggle);
    expect(container.querySelector(".drawer-panel.expanded")).not.toBeNull();
    // the label flips with the state → collapse it back
    fireEvent.click(screen.getByRole("button", { name: "collapse drawer" }));
    expect(container.querySelector(".drawer-panel.expanded")).toBeNull();
  });

  // -------- Slice 2: the Summary | Timing view toggle --------------------------------------------

  it("Slice 2: the toggle swaps the ledger's middle columns, reversibly", () => {
    renderBoard({ data: TIMING_PAYLOAD });
    // Summary (default): summary-only headers present, timing ones absent
    expect(screen.getByText("Why")).toBeInTheDocument();
    expect(screen.getByText("Exit-by")).toBeInTheDocument();
    expect(screen.getByText("Operator")).toBeInTheDocument();
    expect(screen.queryByText("Peak")).not.toBeInTheDocument();
    expect(screen.queryByText("Past peak")).not.toBeInTheDocument();

    // flip to Timing → the timing headers appear, the summary-only ones are gone
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    expect(screen.getByText("Peak")).toBeInTheDocument();
    expect(screen.getByText("Past peak")).toBeInTheDocument();
    expect(screen.queryByText("Why")).not.toBeInTheDocument();
    expect(screen.queryByText("Exit-by")).not.toBeInTheDocument();
    expect(screen.queryByText("Operator")).not.toBeInTheDocument();

    // reversible: flip back to Summary
    fireEvent.click(screen.getByRole("button", { name: "Summary" }));
    expect(screen.getByText("Why")).toBeInTheDocument();
    expect(screen.queryByText("Peak")).not.toBeInTheDocument();
  });

  it("Slice 2: Timing view renders a scored episode's Return / Peak / Past peak", () => {
    renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    const row = screen.getByText("MATR").closest("tr")!;
    expect(within(row).getByText("+12.3%")).toBeInTheDocument(); // forward_return
    expect(within(row).getByText("+20.4%")).toBeInTheDocument(); // peak_return
    expect(within(row).getByText("7d")).toBeInTheDocument(); // exit_vs_peak_days
    // Summary shows the Why chip + the operator cell for the same episode
    fireEvent.click(screen.getByRole("button", { name: "Summary" }));
    const srow = screen.getByText("MATR").closest("tr")!;
    expect(within(srow).getByText("technical_breakout")).toBeInTheDocument(); // Why
    expect(within(srow).getByText("no decision logged")).toBeInTheDocument(); // Operator
  });

  it("Slice 2: honest loudness — an awaiting episode dashes Peak / Past peak (never a false 0)", () => {
    renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    // HIMS is still-awaiting (insufficient_prices) — its timing cells read "—", not "0.0%" / "0d"
    const row = screen.getByText("HIMS").closest("tr")!;
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(2); // Peak + Past peak (+ Return)
    expect(within(row).queryByText("0.0%")).not.toBeInTheDocument();
    expect(within(row).queryByText("0d")).not.toBeInTheDocument();
  });

  it("Slice 2: the ⤢ still opens the scorecard drawer in Timing view (Slice 1 intact)", () => {
    const { container, onSelect } = renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    expect(container.querySelector(".drawer-panel")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "open scorecard for MATR" }));
    expect(onSelect).not.toHaveBeenCalled(); // the ⤢ stops the bubble — no Cockpit nav
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(panel).not.toBeNull();
    expect(within(panel).getByText("The move")).toBeInTheDocument(); // the scorecard mounted
  });

  it("Slice 2: the row click still deep-links to the Cockpit in Timing view", () => {
    const { onSelect } = renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    fireEvent.click(screen.getByText("MATR").closest("tr")!);
    expect(onSelect).toHaveBeenCalledWith("t-hims", "MATR");
  });
});
