import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ledgerColCount } from "../rows";
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
  // Slice B lifted the Cockpit identity + signal-headline reads into the drawer's scorecard — stub them
  // no-data so the ledger's strip degrades to "—" (and no ledger renders without a price window anyway)
  useWorkbenchScored: () => ({ data: undefined, isLoading: false, isError: false }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, isError: false }),
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
  tape_behind_market: false,
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
    // no backfill-reconstructed nights on this ledger (the quiet default)
    reconstructed_nights: [],
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
  dearm_date: "2026-08-18",
  exit_date: "2026-08-20",
  forward_return: 0.123,
  peak_return: 0.204,
  peak_date: "2026-08-10",
  // the adverse side: this episode was 5.6% underwater on closes (8.4% intraday) before it finished
  // +12.3% — the shape the ledger could not previously distinguish from a straight-line winner
  trough_return: -0.056,
  trough_date: "2026-07-28",
  intraday_high_return: 0.231,
  intraday_high_date: "2026-08-11",
  intraday_low_return: -0.084,
  intraday_low_date: "2026-07-27",
  path: [10, 12, 11, 14, 13],
  dearm_index: 3,
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

  it("the ↗ icon drills into the Cockpit — carrying the NAME for ?name=, without opening the drawer", () => {
    const { container, onSelect } = renderBoard();
    // an episode's ↗ icon: thesis id + its ticker as the name key (the row opens the scorecard instead)
    fireEvent.click(screen.getByRole("button", { name: "open HIMS in the cockpit" }));
    expect(onSelect).toHaveBeenCalledWith("t-hims", "HIMS");
    expect(container.querySelector(".drawer-panel")).toBeNull(); // the icon stops the bubble — no drawer
    // a span row has no scorecard — its row click still deep-links the Cockpit (same contract)
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
    // ticker null → the ↗ aria-label reads "this name"; onSelect still carries the security_id
    fireEvent.click(screen.getByRole("button", { name: "open this name in the cockpit" }));
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

  it("the reconstructed-nights line renders ONCE, only when non-empty, with the dates on hover", () => {
    renderBoard(); // the fixture has no reconstructed nights
    expect(screen.queryByText(/reconstructed by a backfill/)).not.toBeInTheDocument();
    renderBoard({
      data: {
        ...PAYLOAD,
        summary: { ...PAYLOAD.summary, reconstructed_nights: ["2026-07-08", "2026-07-09"] },
      },
    });
    const lines = screen.getAllByText(/2 nights reconstructed by a backfill · not scored/);
    expect(lines).toHaveLength(1); // one quiet line for the whole ledger, never a per-row chip
    expect(lines[0]).toHaveAttribute("title", expect.stringContaining("2026-07-08, 2026-07-09"));
    renderBoard({
      data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, reconstructed_nights: ["2026-07-08"] } },
    });
    expect(screen.getByText(/1 night reconstructed by a backfill · not scored/)).toBeInTheDocument();
  });

  it("a row click opens the scorecard drawer WITHOUT navigating to the Cockpit", () => {
    const { container, onSelect } = renderBoard();
    expect(container.querySelector(".drawer-panel")).toBeNull(); // closed by default
    fireEvent.click(screen.getByText("awaiting first bar").closest("tr")!);
    // the row opens the drawer; the Cockpit jump is the ↗ icon's job, not the row's
    expect(onSelect).not.toHaveBeenCalled();
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(panel).not.toBeNull();
    expect(within(panel).getByText("The move")).toBeInTheDocument(); // the scorecard mounted
    expect(panel.querySelector(".drawer-title")?.textContent).toContain("HIMS"); // drawer title
  });

  it("the drawer is reversible: ✕, backdrop, and Escape each close it, the ledger untouched", () => {
    const { container } = renderBoard();
    const open = () => fireEvent.click(screen.getByText("awaiting first bar").closest("tr")!);
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
    fireEvent.click(screen.getByText("awaiting first bar").closest("tr")!);
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
    expect(screen.getByText("Peak")).toBeInTheDocument();
    expect(screen.queryByText("Past peak")).not.toBeInTheDocument();
    expect(screen.queryByText("Worst")).not.toBeInTheDocument();
    expect(screen.queryByText("Path")).not.toBeInTheDocument();
    // the wick columns are a TIMING lens — Summary keeps the close figure alone
    expect(screen.queryByText("Peak high")).not.toBeInTheDocument();
    expect(screen.queryByText("Worst low")).not.toBeInTheDocument();
    // Armed / De-armed are SHARED — the split rides both views, so it is asserted in both
    expect(screen.getByText("Armed")).toBeInTheDocument();
    expect(screen.getByText("De-armed")).toBeInTheDocument();

    // flip to Timing → the timing headers appear, the summary-only ones are gone
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    expect(screen.getByText("Peak")).toBeInTheDocument();
    expect(screen.getByText("Peak high")).toBeInTheDocument();
    expect(screen.getByText("Worst")).toBeInTheDocument();
    expect(screen.getByText("Worst low")).toBeInTheDocument();
    expect(screen.getByText("Past peak")).toBeInTheDocument();
    expect(screen.getByText("Path")).toBeInTheDocument();
    expect(screen.getByText("Armed")).toBeInTheDocument();
    expect(screen.getByText("De-armed")).toBeInTheDocument();
    expect(screen.queryByText("Why")).not.toBeInTheDocument();
    expect(screen.queryByText("Exit-by")).not.toBeInTheDocument();
    expect(screen.queryByText("Operator")).not.toBeInTheDocument();

    // reversible: flip back to Summary
    fireEvent.click(screen.getByRole("button", { name: "Summary" }));
    expect(screen.getByText("Why")).toBeInTheDocument();
    expect(screen.getByText("Peak")).toBeInTheDocument();
    expect(screen.queryByText("Peak high")).not.toBeInTheDocument();
  });

  // The Armed cell used to read "Aug 7 → Aug 11" — two dates, two meanings, one cell that could be
  // neither scanned down nor sorted on. They are two measurements, so they are two columns, and the
  // censored marker stays with the ARM date because it is the arm that is unknowable.
  it("the arm and the de-arm are two columns — an open episode dashes the de-arm", () => {
    renderBoard({ data: TIMING_PAYLOAD });
    const closed = screen.getByText("MATR").closest("tr")!;
    const closedCells = [...closed.querySelectorAll("td")];
    expect(closedCells[1].textContent).toBe("Jul 10"); // the arm, alone — no "→" tail
    expect(closedCells[2].textContent).toBe("Aug 18"); // the de-arm, its own column
    expect(closed.textContent).not.toContain("→");

    // HIMS is still armed: no de-arm exists, so the cell says "—" rather than guessing a date
    const open = screen.getByText("HIMS").closest("tr")!;
    const openCells = [...open.querySelectorAll("td")];
    expect(openCells[1].textContent).toBe("Jul 10*"); // censored marker rides the ARM
    expect(openCells[2].textContent).toBe("—");
    expect(openCells[1].querySelector(".sb-cen")).not.toBeNull();
  });

  it("Slice 2: Timing renders the scored episode's path, return and all four excursions", () => {
    renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    const row = screen.getByText("MATR").closest("tr")!;
    expect(within(row).getByText("+12.3%")).toBeInTheDocument(); // forward_return
    expect(within(row).getByText("+20.4%")).toBeInTheDocument(); // peak_return (close)
    // the adverse side: this row finished +12.3% but was 5.6% underwater on the way — the thing the
    // ledger could not previously say, and the whole reason the Worst column exists
    expect(within(row).getByText("-5.6%")).toBeInTheDocument(); // trough_return (close)
    // the wick pair now has COLUMNS of its own rather than riding the close cells' hovers
    expect(within(row).getByText("+23.1%")).toBeInTheDocument(); // intraday_high_return
    expect(within(row).getByText("-8.4%")).toBeInTheDocument(); // intraday_low_return
    expect(within(row).getByText("7d")).toBeInTheDocument(); // exit_vs_peak_days
    // the shape behind those numbers, with its de-arm marked
    expect(row.querySelector(".sb-path svg")).not.toBeNull();
    expect(row.querySelector(".sb-path line.sb-spark-dearm")).not.toBeNull();
    // Summary shows the Why chip + the operator cell for the same episode
    fireEvent.click(screen.getByRole("button", { name: "Summary" }));
    const srow = screen.getByText("MATR").closest("tr")!;
    expect(within(srow).getByText("technical_breakout")).toBeInTheDocument(); // Why
    expect(within(srow).getByText("+20.4%")).toBeInTheDocument(); // Peak (the realized high, now in Summary too)
    expect(within(srow).getByText("no decision logged")).toBeInTheDocument(); // Operator
  });

  // Four columns mean four hovers, and the whole point of promoting the wicks out of the close
  // cells' titles is that no column restates its neighbor any more.
  it("each excursion cell's hover describes only its OWN figure", () => {
    renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    const row = screen.getByText("MATR").closest("tr")!;
    const cells = [...row.querySelectorAll("td")];
    // Timing order: Name · Armed · De-armed · Path · Return · Peak · Peak high · Worst · Worst low · …
    const [peak, peakHigh, worst, worstLow] = [cells[5], cells[6], cells[7], cells[8]].map(
      (td) => td.getAttribute("title") ?? "",
    );
    expect(peak).toContain("maximum favorable excursion (MFE)");
    expect(worst).toContain("maximum adverse excursion (MAE)");
    expect(peakHigh).toContain("intraday high");
    expect(worstLow).toContain("intraday low");
    // no close hover mentions a wick, and no wick hover mentions an excursion
    expect(peak).not.toContain("intraday");
    expect(worst).not.toContain("intraday");
    expect(peakHigh).not.toContain("excursion");
    expect(worstLow).not.toContain("excursion");
  });

  it("Slice 2: honest loudness — an awaiting episode dashes Peak / Worst / Past peak (never a false 0)", () => {
    renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    // HIMS is still-awaiting (insufficient_prices) — its timing cells read "—", not "0.0%" / "0d".
    // Worst matters most here: a degenerate 0.0% would read as "it never went against you", which is
    // the OPPOSITE of unknown. (A real 0.0% WITH a forward bar is kept — 24% of the record.)
    const row = screen.getByText("HIMS").closest("tr")!;
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(3); // Peak + Worst + Past peak
    expect(within(row).queryByText("0.0%")).not.toBeInTheDocument();
    expect(within(row).queryByText("0d")).not.toBeInTheDocument();
    // and with no bars there is no path to draw — the cell dashes rather than inventing a line
    expect(row.querySelector(".sb-path svg")).toBeNull();
  });

  it("Slice 2: a row click still opens the scorecard drawer in Timing view (Slice 1 intact)", () => {
    const { container, onSelect } = renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    expect(container.querySelector(".drawer-panel")).toBeNull();
    fireEvent.click(screen.getByText("MATR").closest("tr")!);
    expect(onSelect).not.toHaveBeenCalled(); // the row opens the drawer, not the Cockpit
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(panel).not.toBeNull();
    expect(within(panel).getByText("The move")).toBeInTheDocument(); // the scorecard mounted
  });

  it("Slice 2: the ↗ icon still deep-links to the Cockpit in Timing view", () => {
    const { onSelect } = renderBoard({ data: TIMING_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    fireEvent.click(screen.getByRole("button", { name: "open MATR in the cockpit" }));
    expect(onSelect).toHaveBeenCalledWith("t-hims", "MATR");
  });

  // The row carries tabIndex=0 + onClick and nothing else, so a keyboard user could FOCUS a row and
  // then find that Enter did nothing — a drill-down reachable only with a mouse. Enter and Space now
  // do what the click does; an unrelated key still must not open anything.
  it.each([
    ["Enter", true],
    [" ", true],
    ["a", false],
  ] as const)("a focused ledger row opens the scorecard on %s", (key, opens) => {
    const { container } = renderBoard({ data: TIMING_PAYLOAD });
    const row = screen.getByText("MATR").closest("tr")!;
    expect(row).toHaveAttribute("tabindex", "0");
    expect(container.querySelector(".drawer-panel")).toBeNull();
    fireEvent.keyDown(row, { key });
    expect(container.querySelector(".drawer-panel") != null).toBe(opens);
  });

  // -------- sortable columns: a within-group re-order, never a flattening ------------------------

  /** The ledger's rows in DOM order, as `group:<name>` / `row:<ticker>` — enough to see both the
   *  ranking and the grouping in one assertion. */
  function ledgerShape(container: HTMLElement): string[] {
    const table = container.querySelector("table.sb-ledger")!;
    return [...table.querySelectorAll("tbody tr")].flatMap((tr) => {
      if (tr.classList.contains("grp")) return [`group:${tr.querySelector(".lbl")?.textContent}`];
      if (tr.classList.contains("sb-row")) {
        return [`row:${(tr.querySelector("td.tk") as HTMLElement).textContent?.replace("↗", "")}`];
      }
      return [];
    });
  }

  // Two episodes in ONE group, ranked opposite ways round by Peak vs Name — so a real re-order is
  // distinguishable from "the fixture happened to be in that order already".
  const SORT_PAYLOAD = {
    ...PAYLOAD,
    theses: [
      {
        ...PAYLOAD.theses[0],
        name: "Group one",
        episodes: [
          { ...SCORED_EP, security_id: "s-a", ticker: "AAA", peak_return: 0.05 },
          { ...SCORED_EP, security_id: "s-b", ticker: "BBB", peak_return: 0.9 },
        ],
        operator_spans: [],
      },
      { ...PAYLOAD.theses[2], name: "Group two", archived: false },
    ],
  };

  it("a header sort re-orders rows WITHIN each group and never moves the groups", () => {
    const { container } = renderBoard({ data: SORT_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    const before = ledgerShape(container);
    expect(before).toEqual(["group:Group one", "row:AAA", "row:BBB", "group:Group two", "row:J"]);

    // desc on Peak: BBB (+90.0%) outranks AAA (+5.0%) — inside its own group, the spine intact
    fireEvent.click(screen.getByRole("button", { name: "Peak" }));
    expect(ledgerShape(container)).toEqual([
      "group:Group one",
      "row:BBB",
      "row:AAA",
      "group:Group two",
      "row:J",
    ]);

    // the cycle is reversible: asc flips the pair, a third click restores the record's own order
    fireEvent.click(screen.getByRole("button", { name: "Peak" }));
    expect(ledgerShape(container).slice(1, 3)).toEqual(["row:AAA", "row:BBB"]);
    fireEvent.click(screen.getByRole("button", { name: "Peak" }));
    expect(ledgerShape(container)).toEqual(before);
  });

  it("the sorted header announces itself, with the arrow hidden from the accessible name", () => {
    const { container } = renderBoard({ data: SORT_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    const th = () => screen.getByRole("button", { name: "Peak" }).closest("th")!;
    expect(th()).toHaveAttribute("aria-sort", "none");
    fireEvent.click(screen.getByRole("button", { name: "Peak" }));
    expect(th()).toHaveAttribute("aria-sort", "descending");
    // the direction arrow rendered, and the header's accessible NAME is still exactly the label
    expect(container.querySelector("th .th-arrow")).not.toBeNull();
    expect(container.querySelector("th .th-arrow")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "Peak" })).toBeInTheDocument();
  });

  it("Path and Status are not sortable — a shape and a badge set have nothing to rank on", () => {
    renderBoard({ data: SORT_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    expect(screen.queryByRole("button", { name: "Path" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Status" })).toBeNull();
    expect(screen.getByText("Path").tagName).toBe("TH");
    expect(screen.getByText("Status").tagName).toBe("TH");
  });

  it("a sort is a re-order, never a filter: an unmeasurable row sinks but stays on the ledger", () => {
    // HIMS has no forward bar, so every timing cell is "—" — absent, not small. It must end up last
    // in BOTH directions and must never disappear (#9 / interaction principle #2).
    const { container } = renderBoard({
      data: {
        ...PAYLOAD,
        theses: [
          {
            ...PAYLOAD.theses[0],
            episodes: [EP, { ...SCORED_EP, security_id: "s-b", ticker: "BBB" }],
            operator_spans: [],
          },
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Timing" }));
    fireEvent.click(screen.getByRole("button", { name: "Peak" })); // desc
    expect(ledgerShape(container)).toEqual(["group:HIMS — insider conviction", "row:BBB", "row:HIMS"]);
    fireEvent.click(screen.getByRole("button", { name: "Peak" })); // asc — the dash is STILL last
    expect(ledgerShape(container)).toEqual(["group:HIMS — insider conviction", "row:BBB", "row:HIMS"]);
  });

  // A column added to LedgerHead but not to EVERY row body shifts that row's cells one column off
  // their headers — silently, because a short <tr> just renders narrow. That is exactly how the Peak
  // column landed: the head, the episode row and `ledgerColCount` gained it; `SpanRow` did not, so an
  // override's "took …" text sat under the Peak header for three commits. Pin the whole table rather
  // than one row, so the next column has to be added everywhere: every body row must account for
  // exactly as many columns as the head declares, with colSpan doing the accounting for the
  // full-width group / note rows. The fixture carries all four row kinds.
  it.each(["Summary", "Timing"] as const)(
    "every %s body row spans exactly the columns the head declares",
    (viewName) => {
      const { container } = renderBoard();
      fireEvent.click(screen.getByRole("button", { name: viewName }));
      const table = container.querySelector("table.sb-ledger")!;
      const cols = table.querySelectorAll("thead th").length;
      expect(cols).toBe(ledgerColCount(viewName === "Timing" ? "timing" : "summary"));
      // the row kinds this fixture must actually exercise (an empty ledger would pass vacuously)
      expect(container.querySelector("tr.sb-row")).not.toBeNull();
      expect(container.querySelector("tr.sb-span")).not.toBeNull();
      expect(container.querySelector("tr.grp")).not.toBeNull();
      for (const tr of table.querySelectorAll("tbody tr")) {
        const spanned = [...tr.children].reduce(
          (n, td) => n + ((td as HTMLTableCellElement).colSpan || 1),
          0,
        );
        // compare as an object so a failure names the offending row instead of just "7 !== 8"
        expect({ row: tr.className, spanned }).toEqual({ row: tr.className, spanned: cols });
      }
    },
  );
});
