import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ClockProvider } from "../../clock";
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

// every useScoreboard call's arguments, so a test can see which annotated-night READING was asked for
const scoreboardCalls: unknown[][] = [];
// every drawer price-window call's arguments (editions S4: the edition rides it)
const windowCalls: unknown[][] = [];

// Editions S4 — the switcher's list. The default is the record alone (nothing published), so every test
// above the editions block sees the page exactly as before editions.
const RECORD_EDITION = {
  number: 0,
  label: "the original record",
  causes: [],
  cut: null,
  first_night: "2026-07-10",
  last_night: "2026-07-11",
  n_pages: null,
  n_entries: null,
  n_entries_estimated: null,
  engine_fingerprint: null,
  config_hash: null,
  code_sha: null,
  published_at: null,
};
const editionsFx: { data: unknown } = { data: { default: 0, editions: [RECORD_EDITION] } };
// Editions S6 — the "what changed" comparison: its fixture and every call's (from, to, enabled)
const diffFx: { data: unknown; isLoading: boolean; error: unknown } = { data: null, isLoading: false, error: null };
const diffCalls: unknown[][] = [];

vi.mock("../../api/hooks", () => ({
  useScoreboard: (...args: unknown[]) => {
    scoreboardCalls.push(args);
    return fx;
  },
  useScoreboardEditions: () => editionsFx,
  useEditionDiff: (...args: unknown[]) => {
    diffCalls.push(args);
    return diffFx;
  },
  // the drawer's sparkline hook — stubbed no-data so the scorecard renders its quiet "no price path"
  // line (and never reaches real lightweight-charts) when a test opens the drawer
  useEpisodePriceWindow: (...args: unknown[]) => {
    windowCalls.push(args);
    return { data: undefined, isLoading: false, isError: false };
  },
  // Slice B lifted the Cockpit identity + signal-headline reads into the drawer's scorecard — stub them
  // no-data so the ledger's strip degrades to "—" (and no ledger renders without a price window anyway)
  useWorkbenchScored: () => ({ data: undefined, isLoading: false, isError: false }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, isError: false }),
  useThesisDataFlags: () => ({ data: undefined, isLoading: false, error: null }),
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
    // D3b — no phantom-arm (annotated) nights either; the server's default reading
    annotated_policy: "excluded",
    annotated_nights: [],
    // L1 — no late-recorded nights either; the server's default reading
    late_policy: "excluded",
    late_nights: [],
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

  it("2a + F1: the server's current night is the live view, so its freshness line shows though it is before today", () => {
    // Monday morning: the default as-of is Friday's night (the server's clock), today is Monday
    Object.assign(fx, {
      data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, today: "2026-07-13" } },
      isLoading: false,
      error: null,
    });
    const server = { currentNight: "2026-07-10", marketToday: "2026-07-13", actionDay: "2026-07-13", failed: false };
    const { container, rerender } = render(
      <ClockProvider value={server}>
        <Scoreboard asof="2026-07-10" onSelect={vi.fn()} />
      </ClockProvider>,
    );
    expect(container.querySelector(".sb-fresh")?.textContent).toContain("record current");
    // a day before the night is a past view: suppressed
    rerender(
      <ClockProvider value={server}>
        <Scoreboard asof="2026-07-09" onSelect={vi.fn()} />
      </ClockProvider>,
    );
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

  it("the phantom-arm line renders only when a night is annotated, names the nights, and toggles the reading", () => {
    renderBoard(); // no annotated nights: no line, no toggle (a control that can't discriminate)
    expect(screen.queryByText(/phantom-arm/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "include annotated nights" })).not.toBeInTheDocument();

    const night = (asof: string) => ({
      thesis_id: "t1",
      thesis_name: "Nuclear Buildout",
      asof,
      reasons: ["stale_comparative_revenue"],
      rules: ["d3a-first-knowability@abc"],
      n_rows: 1,
    });
    const nights = [night("2026-10-14"), night("2026-10-15")];
    scoreboardCalls.length = 0;
    renderBoard({ data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, annotated_nights: nights } } });
    const line = screen.getByText(/2 phantom-arm nights set aside · not scored/);
    expect(line.closest("div")).toHaveAttribute(
      "title",
      expect.stringContaining("Nuclear Buildout 2026-10-14, Nuclear Buildout 2026-10-15"),
    );
    // the first render asks for the server's default readings (no override sent: annotated, late)
    expect(scoreboardCalls[0]).toEqual(["2026-07-11", true, null, null, null]);
    const toggle = screen.getByRole("button", { name: "include annotated nights" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    // one click asks for the INCLUDE reading (the late reading stays the server's)
    expect(scoreboardCalls[scoreboardCalls.length - 1]).toEqual(["2026-07-11", true, true, null, null]);

    // the include reading says so, and the toggle reads pressed
    renderBoard({
      data: {
        ...PAYLOAD,
        summary: { ...PAYLOAD.summary, annotated_nights: [nights[0]], annotated_policy: "included" },
      },
    });
    expect(screen.getByText(/1 phantom-arm night included in this reading · scored/)).toBeInTheDocument();
  });

  it("the late-recorded line renders only when a night was recorded late, names the nights, and toggles the reading", () => {
    renderBoard(); // no late nights: no line, no toggle (a control that can't discriminate)
    expect(screen.queryByText(/late-recorded/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "include late nights" })).not.toBeInTheDocument();

    // the measured 2026-10-12 shape: AI Memory's on-time 22:42 row re-versioned at 17:04 the next day
    // (an earlier row is scored instead), Modern Defense recorded ONLY by the catch-up (a gap)
    const night = (thesis_name: string, earlier: string | null) => ({
      thesis_id: thesis_name,
      thesis_name,
      asof: "2026-10-12",
      recorded_at: "2026-10-13T21:04:00Z", // 17:04 ET
      edgar_open: "2026-10-13T06:00:00-04:00",
      n_rows: 1,
      earlier_recorded_at: earlier,
      reason: "recorded after the next EDGAR open",
    });
    const nights = [night("AI Memory & Storage", "2026-10-13T02:42:00Z"), night("Modern Defense", null)];
    scoreboardCalls.length = 0;
    renderBoard({ data: { ...PAYLOAD, summary: { ...PAYLOAD.summary, late_nights: nights } } });
    const line = screen.getByText(/2 late-recorded nights · late rows set aside/);
    const title = line.closest("div")?.getAttribute("title") ?? "";
    expect(title).toContain("AI Memory & Storage 2026-10-12 (+11 h; an earlier row scored instead)");
    expect(title).toContain("Modern Defense 2026-10-12 (+11 h; no earlier row: a gap)");
    expect(scoreboardCalls[0]).toEqual(["2026-07-11", true, null, null, null]);
    const toggle = screen.getByRole("button", { name: "include late nights" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    // one click asks for the INCLUDE-late reading (the annotated reading stays the server's)
    expect(scoreboardCalls[scoreboardCalls.length - 1]).toEqual(["2026-07-11", true, null, true, null]);

    // the include reading says so, and the toggle reads pressed
    renderBoard({
      data: {
        ...PAYLOAD,
        summary: { ...PAYLOAD.summary, late_nights: [nights[0]], late_policy: "included" },
      },
    });
    expect(screen.getByText(/1 late-recorded night · late rows scored in this reading/)).toBeInTheDocument();
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

  // -------- the status filter chips: the ledger's first true hide-from-view control ---------------

  const OP_RUNNING = {
    action: "took",
    decision_id: "d-held",
    decision_date: "2026-07-12",
    thesis_level: false,
    entry_price: 30,
    entry_inferred: false,
    exit_inferred: false,
    running: true,
    operator_return: 0.05,
  };
  const OP_CLOSED = { ...OP_RUNNING, decision_id: "d-done", running: false, operator_return: 0.1 };

  // One group holding every row kind the chips discriminate between, and a second whose only row
  // matches nothing — the case that proves a filtered-out group keeps its heading.
  const FILTER_PAYLOAD = {
    ...PAYLOAD,
    theses: [
      {
        ...PAYLOAD.theses[0],
        name: "Live group",
        episodes: [
          { ...EP, security_id: "s-open", ticker: "OPENN" }, // open · no decision logged
          {
            ...SCORED_EP,
            security_id: "s-held",
            ticker: "HELD",
            status: "open",
            matured: false,
            dearm_date: null,
            operator: OP_RUNNING, // open · taken and still held
          },
          { ...SCORED_EP, security_id: "s-done", ticker: "DONE", operator: OP_CLOSED }, // closed-out take
        ],
        operator_spans: PAYLOAD.theses[1].operator_spans, // a RUNNING off-record span (ticker J)
      },
      {
        ...PAYLOAD.theses[2],
        name: "Quiet group",
        archived: false,
        episodes: [{ ...SCORED_EP, security_id: "s-cold", ticker: "COLD" }], // closed, no decision
        operator_spans: [],
      },
    ],
  };

  it("the chips start OFF — the whole ledger, no Clear, no count line (never the default)", () => {
    const { container } = renderBoard({ data: FILTER_PAYLOAD });
    expect(ledgerShape(container)).toEqual([
      "group:Live group",
      "row:OPENN",
      "row:HELD",
      "row:DONE",
      "row:J",
      "group:Quiet group",
      "row:COLD",
    ]);
    expect(container.querySelector(".sb-filterline")).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    expect(screen.getByRole("button", { name: "Open 2" })).toHaveAttribute("aria-pressed", "false");
  });

  // Each chip's number is taken over the whole record (both groups, folded or not), so it answers
  // "how many are there" rather than "how many can I see".
  it("each chip carries its own live count over the whole record", () => {
    renderBoard({ data: FILTER_PAYLOAD });
    expect(screen.getByRole("button", { name: "Open 2" })).toBeInTheDocument(); // OPENN + HELD
    expect(screen.getByRole("button", { name: "In position 2" })).toBeInTheDocument(); // HELD + the span
    expect(screen.getByRole("button", { name: "Needs an answer 1" })).toBeInTheDocument(); // OPENN
  });

  it("a chip removes rows, reports what it hid, and every group keeps its heading", () => {
    const { container } = renderBoard({ data: FILTER_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Open 2" }));

    // only the open episodes survive — the closed-out take, the span and the other group's row are gone
    expect(ledgerShape(container)).toEqual([
      "group:Live group",
      "row:OPENN",
      "row:HELD",
      "group:Quiet group",
    ]);
    expect(screen.getByRole("button", { name: "Open 2" })).toHaveAttribute("aria-pressed", "true");

    // the receipt: nothing vanished silently
    expect(container.querySelector(".sb-filterline")?.textContent).toBe(
      "showing 2 of 5 rows · 3 hidden by filter",
    );
    // the emptied group still says what it is holding back, and its heading declares N of M
    expect(screen.getByText("no rows match the filter · 1 hidden")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Live group/ }).textContent).toContain("· 2 of 4");
    expect(screen.getByRole("button", { name: /Quiet group/ }).textContent).toContain("· 0 of 1");
  });

  // Two chips are a UNION: open ∪ in-position is the live picture, and the off-record span comes back
  // because a running take is a position even though it is not an arm.
  it("two chips union rather than intersect", () => {
    const { container } = renderBoard({ data: FILTER_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Open 2" }));
    fireEvent.click(screen.getByRole("button", { name: "In position 2" }));
    expect(ledgerShape(container)).toEqual([
      "group:Live group",
      "row:OPENN",
      "row:HELD",
      "row:J",
      "group:Quiet group",
    ]);
    expect(container.querySelector(".sb-filterline")?.textContent).toBe(
      "showing 3 of 5 rows · 2 hidden by filter",
    );
  });

  it("Clear restores the full ledger in one click, and takes its own affordance with it", () => {
    const { container } = renderBoard({ data: FILTER_PAYLOAD });
    const before = ledgerShape(container);
    fireEvent.click(screen.getByRole("button", { name: "Needs an answer 1" }));
    expect(ledgerShape(container)).toEqual(["group:Live group", "row:OPENN", "group:Quiet group"]);

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(ledgerShape(container)).toEqual(before);
    expect(container.querySelector(".sb-filterline")).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    expect(screen.getByRole("button", { name: "Open 2" })).toHaveAttribute("aria-pressed", "false");
    // the heading is back to its plain count too
    expect(screen.getByRole("button", { name: /Live group/ }).textContent).toContain("· 4");
  });

  // Pressing the same chip again is its own inverse — the operator never needs Clear to undo one press.
  it("a chip is its own inverse", () => {
    const { container } = renderBoard({ data: FILTER_PAYLOAD });
    const before = ledgerShape(container);
    fireEvent.click(screen.getByRole("button", { name: "Open 2" }));
    expect(ledgerShape(container)).not.toEqual(before);
    fireEvent.click(screen.getByRole("button", { name: "Open 2" }));
    expect(ledgerShape(container)).toEqual(before);
  });

  // "There are none" is an answer. A chip that vanished at zero would leave the operator unsure the
  // question was ever asked, so it stays put, dim and inert, wearing its 0.
  it("a chip matching nothing renders disabled with its 0 rather than disappearing", () => {
    const noUnanswered = {
      ...FILTER_PAYLOAD,
      theses: [
        {
          ...FILTER_PAYLOAD.theses[0],
          episodes: FILTER_PAYLOAD.theses[0].episodes.filter((e) => e.ticker !== "OPENN"),
        },
        FILTER_PAYLOAD.theses[1],
      ],
    };
    const { container } = renderBoard({ data: noUnanswered });
    const chip = screen.getByRole("button", { name: "Needs an answer 0" });
    expect(chip).toBeDisabled();
    fireEvent.click(chip);
    expect(container.querySelector(".sb-filterline")).toBeNull(); // inert — nothing was filtered
    expect(screen.getByRole("button", { name: "Open 1" })).toBeEnabled();
  });

  // Folding is the operator's own explicit state; a filter must not reach into it. A folded group
  // with matches still declares them in its heading.
  it("filtering leaves fold state alone — a folded group still declares its matches", () => {
    const { container } = renderBoard({ data: FILTER_PAYLOAD });
    const group = () => screen.getByRole("button", { name: /Live group/ });
    fireEvent.click(group()); // fold it closed
    expect(group()).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: "Open 2" }));
    expect(group()).toHaveAttribute("aria-expanded", "false"); // the filter did not re-open it
    expect(group().textContent).toContain("· 2 of 4"); // …and it still says what it holds
    // the chip count is unmoved by the fold — it counts the record, not the visible rows
    expect(screen.getByRole("button", { name: "Open 2" })).toBeInTheDocument();
    expect(container.querySelector(".sb-filterline")?.textContent).toBe(
      "showing 2 of 5 rows · 3 hidden by filter",
    );
  });

  // Filter first, then sort: the sorters keep their "same length in, same length out" contract, so
  // the two controls compose instead of one swallowing the other.
  it("a filter and a sort compose — the surviving rows still re-rank within their group", () => {
    const { container } = renderBoard({ data: FILTER_PAYLOAD });
    fireEvent.click(screen.getByRole("button", { name: "Open 2" }));
    expect(ledgerShape(container)).toEqual([
      "group:Live group",
      "row:OPENN",
      "row:HELD",
      "group:Quiet group",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Name" })); // desc by ticker: OPENN > HELD
    expect(ledgerShape(container)).toEqual([
      "group:Live group",
      "row:OPENN",
      "row:HELD",
      "group:Quiet group",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Name" })); // asc — the filtered pair flips
    expect(ledgerShape(container)).toEqual([
      "group:Live group",
      "row:HELD",
      "row:OPENN",
      "group:Quiet group",
    ]);
    // the sort moved rows around; it never put a filtered-out one back
    expect(container.querySelector(".sb-filterline")?.textContent).toBe(
      "showing 2 of 5 rows · 3 hidden by filter",
    );
  });
});

// --- Editions S4: the switcher, the label, the hidden record-only lines, the drawer's edition --------------

const EDITION_1 = {
  number: 1,
  label: "first re-run",
  causes: ["initial"],
  cut: "2026-10-22T21:59:07Z",
  first_night: "2026-06-10",
  last_night: "2026-10-21",
  n_pages: 659,
  n_entries: 727,
  n_entries_estimated: 727,
  engine_fingerprint: "a".repeat(64),
  config_hash: "c".repeat(64),
  code_sha: null,
  published_at: "2026-10-22T22:04:00Z",
};

// an Edition 1 read: the record-only lists are filled here ON PURPOSE (the server sends them empty for a
// re-run) so the test proves the PAGE hides those lines for an edition, not merely that they were empty
const RERUN_PAYLOAD = {
  ...PAYLOAD,
  edition: EDITION_1,
  summary: {
    ...PAYLOAD.summary,
    banner: "EDITION 1 · FIRST RE-RUN — today's rules re-run from each company's join date…",
    n_ingest_flagged: 1,
    reconstructed_nights: ["2026-07-10"],
    annotated_policy: "n/a",
    annotated_nights: [
      { thesis_id: "t-hims", thesis_name: "HIMS", asof: "2026-07-10", reasons: ["x"], rules: ["r"], n_rows: 1 },
    ],
    late_policy: "n/a",
    late_nights: [
      {
        thesis_id: "t-hims",
        thesis_name: "HIMS",
        asof: "2026-07-10",
        recorded_at: "2026-07-13T13:00:00Z",
        edgar_open: "2026-07-13T10:00:00Z",
        n_rows: 1,
        earlier_recorded_at: null,
        reason: "recorded after the next EDGAR open",
      },
    ],
  },
  theses: [
    {
      ...PAYLOAD.theses[0],
      episodes: [
        {
          ...EP,
          ingest_flagged: true, // never shown on a re-run, whatever the wire says
          run_identity_note: "Edition 1 · first re-run · engine aaaaaaaa · policy cccccccc · cut 2026-10-22",
          arm_run_kind: "rerun",
          edition_join: {
            entry_date: "2026-07-10",
            estimated: true,
            method: "first_pull",
            armed_on_join_night: true,
            note:
              "join date estimated · 2026-07-10 (first data pull) · armed on its join night, so the true " +
              "arm date is unknowable — kept out of the metrics",
          },
        },
      ],
    },
  ],
};

function renderEditions(
  data: unknown,
  {
    edition = null,
    editions = [RECORD_EDITION, EDITION_1],
  }: { edition?: number | null; editions?: unknown[] } = {},
) {
  Object.assign(fx, { data, isLoading: false, error: null });
  editionsFx.data = { default: 0, editions };
  const onEditionChange = vi.fn();
  const utils = render(
    <Scoreboard asof="2026-07-11" onSelect={vi.fn()} edition={edition} onEditionChange={onEditionChange} />,
  );
  return { onEditionChange, ...utils };
}

describe("Scoreboard — editions", () => {
  afterEach(() => {
    editionsFx.data = { default: 0, editions: [RECORD_EDITION] };
  });

  it("no switcher until a second edition exists — the page reads as before editions", () => {
    renderEditions({ ...PAYLOAD, edition: RECORD_EDITION }, { editions: [RECORD_EDITION] });
    expect(screen.queryByRole("group", { name: "edition" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Edition 0/)).not.toBeInTheDocument();
  });

  it("the switcher labels both editions, marks the SERVED one, and a click picks an edition", () => {
    const { onEditionChange } = renderEditions({ ...PAYLOAD, edition: RECORD_EDITION });
    const group = screen.getByRole("group", { name: "edition" });
    const record = within(group).getByRole("button", { name: "Edition 0 · the original record" });
    const rerun = within(group).getByRole("button", { name: "Edition 1 · first re-run · cut Oct 22" });
    expect(record).toHaveAttribute("aria-pressed", "true"); // the default read served Edition 0
    expect(rerun).toHaveAttribute("aria-pressed", "false");
    expect(rerun.getAttribute("title")).toMatch(/727 of 727 join dates estimated/);
    fireEvent.click(rerun);
    expect(onEditionChange).toHaveBeenCalledWith(1);
  });

  it("the edition asked for rides the Scoreboard read (null = the server's default is not sent)", () => {
    scoreboardCalls.length = 0;
    renderEditions(RERUN_PAYLOAD, { edition: 1 });
    expect(scoreboardCalls[0]).toEqual(["2026-07-11", true, null, null, 1]);
    cleanup();
    scoreboardCalls.length = 0;
    renderEditions({ ...PAYLOAD, edition: RECORD_EDITION });
    expect(scoreboardCalls[0]).toEqual(["2026-07-11", true, null, null, null]);
  });

  it("Edition 1 hides the reconstructed, annotated and late lines, their toggles, and the INGEST badge", () => {
    renderEditions(RERUN_PAYLOAD, { edition: 1 });
    const group = screen.getByRole("group", { name: "edition" });
    expect(within(group).getByRole("button", { name: /Edition 1/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/EDITION 1 · FIRST RE-RUN/)).toBeInTheDocument();
    expect(screen.getByText(/no reconstructed, annotated or late nights in a re-run/)).toBeInTheDocument();
    expect(screen.queryByText(/reconstructed by a backfill/)).not.toBeInTheDocument();
    expect(screen.queryByText(/phantom-arm/)).not.toBeInTheDocument();
    expect(screen.queryByText(/late-recorded/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "include annotated nights" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "include late nights" })).not.toBeInTheDocument();
    expect(screen.queryByText("INGEST")).not.toBeInTheDocument();
    expect(screen.queryByText(/ingest-flagged/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Edition return/ })).toBeInTheDocument();
    // the CENSORED mark stays (it marks the exception) and says G12's reason on hover
    expect(screen.getByText("CENSORED").getAttribute("title")).toMatch(/estimated join night/);
  });

  it("the same lines DO render on Edition 0 (the record) — the hide is the edition's, not the data's", () => {
    renderEditions({
      ...RERUN_PAYLOAD,
      edition: RECORD_EDITION,
      summary: { ...RERUN_PAYLOAD.summary, annotated_policy: "excluded", late_policy: "excluded" },
    });
    expect(screen.getByText(/reconstructed by a backfill/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "include annotated nights" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "include late nights" })).toBeInTheDocument();
    expect(screen.getAllByText("INGEST").length).toBeGreaterThan(0);
  });

  it("Edition 1's drawer: the join-date note, the edition caption, no INGEST, the edition on its window", () => {
    windowCalls.length = 0;
    const { container } = renderEditions(RERUN_PAYLOAD, { edition: 1 });
    fireEvent.click(screen.getByText("awaiting first bar").closest("tr")!);
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(within(panel).getByText(/join date estimated · 2026-07-10 \(first data pull\)/)).toBeInTheDocument();
    expect(within(panel).getByText(/Edition 1 · first re-run · engine aaaaaaaa/)).toBeInTheDocument();
    expect(within(panel).queryByText("INGEST")).not.toBeInTheDocument();
    expect(within(panel).getByText("The move")).toBeInTheDocument(); // the scorecard mounted, chart slot too
    expect(windowCalls.length).toBeGreaterThan(0);
    const [args] = windowCalls[windowCalls.length - 1] as [{ edition: number | null }];
    expect(args.edition).toBe(1);
  });
});

// --- Editions S6: the quiet "what changed" link and the comparison it opens -------------------------------

const DIFF = {
  before: RECORD_EDITION,
  after: EDITION_1,
  through: "2026-10-21",
  first_night: "2026-07-10",
  counts: {
    n_theses: 2,
    n_theses_changed: 1,
    n_companies_changed: 1,
    n_thesis_nights: 150,
    n_state_nights: 3,
    n_company_nights_changed: 3,
    n_armed_before_only: 0,
    n_armed_after_only: 3,
    n_fields_changed: 0,
    n_episodes_matched: 4,
    n_episodes_matched_changed: 0,
    n_episodes_moved: 0,
    n_episodes_appeared: 1,
    n_episodes_vanished: 0,
    n_episodes_outside: 2,
  },
  theses: [
    {
      thesis_id: "t-hims",
      name: "HIMS — insider conviction",
      archived: false,
      first_night: "2026-07-10",
      last_night: "2026-10-21",
      n_nights: 75,
      n_state_nights: 3,
      before_only: [],
      after_only: [{ first: "2026-06-10", last: "2026-07-09", n_nights: 20 }],
      n_outside: 2,
      n_matched: 4,
      n_companies_unchanged: 0,
      error: null,
      companies: [
        {
          security_id: "s1",
          ticker: "HIMS",
          company_name: "Hims & Hers Health",
          nights: [],
          runs: [
            {
              first: "2026-07-13",
              last: "2026-07-15",
              n_nights: 3,
              changed: ["armed"],
              before: { page: "2026-07-13", armed: false },
              after: { page: "2026-07-13", armed: true, verdict: "core_entry", entry_grade: "core" },
            },
          ],
          episodes: [
            {
              change: "appeared",
              before: null,
              after: {
                arm_date: "2026-07-13",
                last_armed_date: "2026-07-15",
                dearm_date: "2026-07-16",
                close_reason: "arm_until_lapsed",
                began_mid_arm: false,
                forward_return: 0.042,
              },
              differs: [],
              cause: null,
            },
          ],
        },
      ],
    },
    {
      thesis_id: "t-unh",
      name: "UNH — insider cluster",
      archived: false,
      first_night: "2026-07-10",
      last_night: "2026-10-21",
      n_nights: 75,
      n_state_nights: 0,
      before_only: [],
      after_only: [],
      n_outside: 0,
      n_matched: 0,
      n_companies_unchanged: 0,
      error: null,
      companies: [],
    },
  ],
};

describe("Scoreboard — what changed", () => {
  afterEach(() => {
    editionsFx.data = { default: 0, editions: [RECORD_EDITION] };
    diffFx.data = null;
    diffCalls.length = 0;
  });

  it("no link with a single edition, nor on Edition 0 (nothing comes before the record)", () => {
    renderEditions({ ...PAYLOAD, edition: RECORD_EDITION }, { editions: [RECORD_EDITION] });
    expect(screen.queryByRole("button", { name: /what changed/ })).not.toBeInTheDocument();
    cleanup();
    renderEditions({ ...PAYLOAD, edition: RECORD_EDITION });
    expect(screen.getByRole("group", { name: "edition" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /what changed/ })).not.toBeInTheDocument();
  });

  it("on Edition 1 a quiet link compares it with Edition 0, fetched only once opened", () => {
    diffFx.data = DIFF;
    const { container } = renderEditions(RERUN_PAYLOAD, { edition: 1 });
    const link = screen.getByRole("button", { name: "what changed since Edition 0" });
    expect(diffCalls).toEqual([]); // nothing fetched until asked for
    fireEvent.click(link);
    expect(diffCalls[diffCalls.length - 1]).toEqual([0, 1, true]);
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(within(panel).getByText(/Edition 0 → Edition 1/)).toBeInTheDocument();
    // the counts on top, in plain English
    expect(within(panel).getByText("1 of 2 theses changed · 1 company")).toBeInTheDocument();
    expect(within(panel).getByText(/3 company-nights differ: 3 armed in Edition 1 only/)).toBeInTheDocument();
    // thesis -> company, the episode and the folded night run said in words
    expect(within(panel).getByRole("heading", { name: "HIMS — insider conviction" })).toBeInTheDocument();
    expect(within(panel).getByText("Hims & Hers Health")).toBeInTheDocument();
    expect(
      within(panel).getByText("Edition 1 arms it Jul 13 (de-armed Jul 16); Edition 0 did not · score +4.2%"),
    ).toBeInTheDocument();
    expect(within(panel).getByText("Jul 13 – Jul 15 · 3 nights — armed in Edition 1 only")).toBeInTheDocument();
    expect(
      within(panel).getByText(
        "Edition 1 alone covers Jun 10 – Jul 9 · 20 nights · 2 episodes armed there, not compared",
      ),
    ).toBeInTheDocument();
    // nothing vanishes: the unchanged thesis is listed and says so
    expect(within(panel).getByRole("heading", { name: "UNH — insider cluster" })).toBeInTheDocument();
    expect(within(panel).getByText("no change")).toBeInTheDocument();
    // no alarm colors anywhere in the comparison
    expect(panel.querySelector(".wc .neg, .wc .pos, .wc .sb-stale")).toBeNull();
    // reversible: closing returns to the ledger, untouched
    fireEvent.click(within(panel).getByRole("button", { name: "close drawer" }));
    expect(container.querySelector(".drawer-panel")).toBeNull();
    expect(screen.getByRole("button", { name: "what changed since Edition 0" })).toBeInTheDocument();
  });

  it("names each change's cause when the later edition was cut from the earlier one (S7)", () => {
    const withCauses = JSON.parse(JSON.stringify(DIFF));
    withCauses.causes = true;
    const co = withCauses.theses[0].companies[0];
    co.episodes = co.episodes.map((e: Record<string, unknown>) => ({ ...e, cause: "rule_change" }));
    co.runs = co.runs.map((r: Record<string, unknown>) => ({ ...r, cause: "late_data" }));
    diffFx.data = withCauses;
    const { container } = renderEditions(RERUN_PAYLOAD, { edition: 1 });
    fireEvent.click(screen.getByRole("button", { name: "what changed since Edition 0" }));
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(within(panel).getByText(/each change names its cause, from the trial that cut Edition 1/)).toBeInTheDocument();
    expect(within(panel).getByText(/cause: the declared rule change/)).toBeInTheDocument();
    expect(within(panel).getByText(/cause: data that arrived after Edition 0 was cut/)).toBeInTheDocument();
  });
});

describe("Scoreboard — the automatic cut's notice (editions S7)", () => {
  const EDITION_2 = {
    ...EDITION_1,
    number: 2,
    label: "rule change: insider window · includes data that arrived since Edition 1",
    causes: ["rule_change"],
    cut: "2026-10-24T03:00:00Z",
  };
  const NOTICE = {
    number: 2,
    previous: 1,
    label: EDITION_2.label,
    line: "Edition 2 · rule change: insider window · includes data that arrived since Edition 1 · 37 company-nights changed",
    published_at: "2026-10-24T03:05:00Z",
    n_company_nights_changed: 37,
    unexplained: false,
  };

  afterEach(() => {
    editionsFx.data = { default: 0, editions: [RECORD_EDITION] };
    diffFx.data = null;
    diffCalls.length = 0;
  });

  it("one quiet line with a link that compares the new edition with the one it replaced", () => {
    diffFx.data = DIFF;
    Object.assign(fx, { data: RERUN_PAYLOAD, isLoading: false, error: null });
    editionsFx.data = { default: 2, editions: [RECORD_EDITION, EDITION_1, EDITION_2], notice: NOTICE };
    const { container } = render(<Scoreboard asof="2026-07-11" onSelect={vi.fn()} edition={1} />);
    const line = screen.getByTestId("sb-cut-notice");
    expect(line.textContent).toBe(`${NOTICE.line} · see what changed`);
    expect(line.className).toContain("sb-freshline");
    expect(line.className).not.toMatch(/sb-stale|blink/);
    fireEvent.click(within(line).getByRole("button", { name: "see what changed" }));
    expect(diffCalls[diffCalls.length - 1]).toEqual([1, 2, true]);
    const panel = container.querySelector(".drawer-panel") as HTMLElement;
    expect(within(panel).getByText(/Edition 1 → Edition 2/)).toBeInTheDocument();
  });

  it("renders nothing without a notice (no cut in the last seven days, or an older backend)", () => {
    renderEditions(RERUN_PAYLOAD, { edition: 1 });
    expect(screen.queryByTestId("sb-cut-notice")).toBeNull();
  });
});
