import { fireEvent, render, screen } from "@testing-library/react";
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
    n_eligible: 0,
    n_takes: 1,
    n_passes: 0,
    n_overrides: 1,
    n_voided: 0,
    record_began: "2026-07-10",
    banner: "FORWARD RECORD, NOT A CLAIM — record began 2026-07-10; 0 episodes eligible…",
    min_n: 5,
    metrics: [
      { name: "arm_timing_forward_return", claim: "timing", n: 0, insufficient_n: true, summary: {}, detail: [], note: "" },
      { name: "false_arm_rate", claim: "precision", n: 0, insufficient_n: true, summary: {}, detail: [], note: "" },
    ],
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
});
