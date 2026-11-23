import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { lazy, useEffect } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ClockProvider, browserClock } from "../../clock";
import { buildMemberOverlayEvents, visibleRangeForSpan } from "../../scoreboard/overlay";
import { DashboardShell } from "../DashboardShell";
import type { DashboardEntry, DashboardProps } from "../registry";

const apiGet = vi.hoisted(() => vi.fn());

vi.mock("../../api/client", () => ({
  api: {
    GET: apiGet,
  },
}));

vi.mock("../../AppHeader", () => ({
  AppHeader: () => <header data-testid="app-header" />,
}));

// Canvas-free stub for lightweight-charts inside jsdom — PriceSparkline's real overlay + ledger logic still runs.
vi.mock("../../scoreboard/PriceSparkline", () => ({
  PriceSparkline: ({
    bars,
    events,
    span,
  }: {
    bars: unknown[];
    events: { n: number }[];
    span?: string;
  }) => (
    <div data-testid="mock-sparkline" data-bars={bars.length} data-events={events.length} data-span={span ?? ""} />
  ),
}));

const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });

function makePriceWindow() {
  return {
    thesis_id: "t-glp",
    security_id: "s-rytm",
    start: "2025-11-22",
    end: "2026-11-22",
    asof: "2026-11-22",
    source: "fact_price_eod",
    bars: [
      { d: "2026-11-17", open: 108, high: 110, low: 107, close: 109.5, volume: 420000, sma50: 104, sma200: 95 },
      { d: "2026-11-18", open: 110, high: 115, low: 109, close: 114.2, volume: 680000, sma50: 104.5, sma200: 95.3 },
      { d: "2026-11-19", open: 113, high: 116, low: 112, close: 112.5, volume: 510000, sma50: 105, sma200: 95.6 },
      { d: "2026-11-20", open: 112, high: 114, low: 111, close: 112.56, volume: 490000, sma50: 105.4, sma200: 96 },
    ],
    insider_buys: [
      {
        d: "2026-11-18",
        disclosed: "2026-11-19",
        ingested: "2026-11-19",
        insider_name: "Jane Director",
        insider_role: "Director",
        shares: 5000,
        price: 114,
        usd: 570000,
        character: "open_market",
        internal_transfer_with: null,
        private_note: null,
        private_passage: null,
      },
    ],
    insider_sells: [],
    corporate_events: [],
    activist_stakes: [],
  };
}

function makePayload(overrides?: Partial<Record<string, unknown>>) {
  return {
    thesis_id: "t-glp",
    thesis_name: "Beyond GLP-1: Obesity & Peptide Drugs",
    asof: "2026-11-22",
    member: {
      ticker: "RYTM",
      role: "core",
      security_id: "s-rytm",
      detail: null,
      segment: "Peptide drugs beyond obesity",
      thesis_fit: "Rhythm Pharmaceuticals treats rare genetic obesity disorders.",
      conviction: 4,
      authored_by: "system_drafted",
    },
    position: null,
    card: {
      thesis_id: "t-glp",
      asof: "2026-11-22",
      state: "armed",
      verdict: "core_entry",
      conviction_grade: "core",
      confirmation_grade: "core",
      entry_grade: "core",
      expression: "CORE",
      exit_by: "2027-05-02",
      arm_until: "2027-01-02",
      catalyst_surface: [],
      confidence: 0.88,
      key_conviction: { turned: true, label: "Conviction", detail: "" },
      key_confirmation: { turned: true, label: "Confirmation", detail: "" },
      triggers_fired: [
        {
          label: "52-week breakout on RYTM",
          kind: "technical_breakout",
          grade: "core",
          ticker: "RYTM",
          event_date: "2026-11-18",
          sources: [],
        },
      ],
      risk_signals: [
        {
          label: "Fast breakdown on RYTM",
          kind: "technical_breakdown",
          grade: null,
          ticker: "RYTM",
          event_date: "2026-11-20",
          sources: [],
        },
      ],
      missing: [],
      counter_case: "",
      armed_members: [
        {
          security_id: "s-rytm",
          ticker: "RYTM",
          verdict: "core_entry",
          conviction_grade: "core",
          confirmation_grade: "core",
          entry_grade: "core",
          confidence: 0.88,
          exit_by: "2027-05-02",
          arm_until: "2027-01-02",
          lapsing: false,
          theme_armed: false,
          entry_window_closing: false,
          triggers: [
            {
              label: "52-week breakout on RYTM",
              kind: "technical_breakout",
              grade: "core",
              ticker: "RYTM",
              event_date: "2026-11-18",
              sources: [],
            },
          ],
        },
      ],
      watch_members: [],
    },
    scored: {
      security_id: "s-rytm",
      ticker: "RYTM",
      name: "RHYTHM PHARMACEUTICALS, INC.",
      cik: "0001649904",
      exchange: "Nasdaq",
      sector: "Pharmaceutical Preparations",
      category: "Large accelerated filer",
      origin: "US",
      foreign_filer_form: null,
      business_type: "biotech_pharma",
      business_supersector: "healthcare",
      business_type_override: false,
      royalty: false,
      instrument_kind: "equity",
      price_symbol: null,
      segment: "Peptide drugs beyond obesity",
      purity: fig(4, 95),
      runway: fig(3, 16.4),
      catalysts: fig(2, 1),
      dilution: fig(1, 4),
      market_cap: fig(null, 7_800_000_000),
      fit: "pure-play",
      thin_price_history: false,
      price_feed_limitation: null,
    },
    display: {
      security_id: "s-rytm",
      ticker: "RYTM",
      signals: [
        {
          kind: "sma_position",
          label: "Moving averages",
          headline: { key: "above_rising", glyph: "up", label: "50d over 200d · rising", detail: "" },
          metrics: [{ key: "close", label: "Close", value: 112.56, unit: "price" }],
          events: [],
          basis: { source: "fact_price_eod", window_end: "2026-11-20" },
        },
        {
          kind: "trailing_returns",
          label: "Trailing returns",
          headline: null,
          metrics: [
            { key: "ret_1d", label: "1d", value: -3.8, unit: "pct", tone: "neg" },
            { key: "ret_7d", label: "7d", value: 8.2, unit: "pct", tone: "pos" },
          ],
          events: [],
          basis: { source: "fact_price_eod" },
        },
      ],
    },
    data_flags: null,
    shared_values: null,
    decisions: [
      {
        id: "dec-1",
        action: "take",
        decision_date: "2026-11-19",
        security_id: "s-rytm",
        shares: 50,
        price: 112.5,
        reason: "starter entry",
        voids: null,
        call_state: "armed",
        call_verdict: "core_entry",
        recorded_at: "2026-11-19T16:00:00Z",
        voided: false,
      },
    ],
    cards: [
      {
        thesis_id: "t-glp",
        thesis_name: "Beyond GLP-1: Obesity & Peptide Drugs",
        ticker: "RYTM",
        archived: false,
      },
      {
        thesis_id: "t-rare",
        thesis_name: "Rare Genetic Disease Platforms",
        ticker: "RYTM",
        archived: false,
      },
    ],
    ...overrides,
  };
}

function mockMemberAndWindow(payloadOverrides?: Partial<Record<string, unknown>>) {
  const payload = makePayload(payloadOverrides);
  const priceWindow = makePriceWindow();
  apiGet.mockImplementation((path: string) => {
    if (path === "/scoreboard/price-window") {
      return Promise.resolve({ data: priceWindow, error: undefined, response: { status: 200 } });
    }
    return Promise.resolve({ data: payload, error: undefined, response: { status: 200 } });
  });
}

function makeEntry(
  Body: (props: DashboardProps) => JSX.Element,
  overrides?: Partial<DashboardEntry>,
): DashboardEntry {
  return {
    slug: "test-dash",
    order: 10,
    label: "Test Dashboard",
    description: "Test description.",
    status: "experimental",
    asofAware: true,
    component: lazy(() => Promise.resolve({ default: Body })),
    ...overrides,
  };
}

function renderShell(entry: DashboardEntry, onSelectThesis = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const clock = { ...browserClock(false), currentNight: "2026-11-22", actionDay: "2026-11-23", marketToday: "2026-11-22" };
  return {
    onSelectThesis,
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ClockProvider value={clock}>
            <DashboardShell
              entry={entry}
              asof="2026-11-22"
              onAsofChange={vi.fn()}
              onSelectThesis={onSelectThesis}
            />
          </ClockProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

describe("DashboardShell CompanyDrawer (Phase 2 redesigned dossier)", () => {
  beforeEach(() => {
    apiGet.mockReset();
  });

  it("opens redesigned company dossier with chart, range pills, event ledger, tape strip, and call details", async () => {
    mockMemberAndWindow();
    const onEscapeBehind = vi.fn();

    function FakeDashboard({ onSelectThesis }: DashboardProps) {
      useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
          if (e.key === "Escape") onEscapeBehind();
        };
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
      }, []);
      return (
        <div>
          <button type="button" onClick={() => onSelectThesis("t-glp", "RYTM")}>
            RYTM
          </button>
          <button type="button" onClick={() => onSelectThesis("t-glp")}>
            Beyond GLP-1
          </button>
        </div>
      );
    }

    const { onSelectThesis } = renderShell(makeEntry(FakeDashboard));

    // Thesis-only click still goes straight to the Cockpit
    fireEvent.click(await screen.findByRole("button", { name: "Beyond GLP-1" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-glp", undefined);
    expect(screen.queryByRole("dialog")).toBeNull();

    onSelectThesis.mockClear();

    // Company click opens the Drawer instead of navigating
    fireEvent.click(screen.getByRole("button", { name: "RYTM" }));
    expect(onSelectThesis).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByText("RHYTHM PHARMACEUTICALS, INC.")).toBeTruthy();
    });

    // Core call, tape strip, triggers, risks, thesis fit, scoring, decision log
    expect(within(dialog).getByText(/CORE entry/i)).toBeTruthy();
    expect(within(dialog).getByText("88%")).toBeTruthy();
    const tapeStrip = within(dialog).getByRole("region", { name: "Tape snapshot" });
    expect(within(tapeStrip).getByText("+8.2%")).toBeTruthy();
    expect(within(tapeStrip).getByText("-3.8%")).toBeTruthy();
    expect(within(dialog).getByText("52-week breakout on RYTM")).toBeTruthy();
    expect(within(dialog).getByText("Fast breakdown on RYTM")).toBeTruthy();
    expect(within(dialog).getByText(/Rhythm Pharmaceuticals treats rare genetic obesity/)).toBeTruthy();
    expect(within(dialog).getByText("Scoring snapshot")).toBeTruthy();
    expect(within(dialog).getByText("Decision log · this name")).toBeTruthy();

    // Phase 2: chart + range pills + event ledger wired with trigger, risk, insider buy, and decision
    const chart = await within(dialog).findByTestId("mock-sparkline");
    expect(chart).toHaveAttribute("data-bars", "4");
    expect(chart).toHaveAttribute("data-events", "4");
    expect(chart).toHaveAttribute("data-span", "6M");

    fireEvent.click(within(dialog).getByRole("button", { name: "1Y" }));
    expect(chart).toHaveAttribute("data-span", "1Y");

    expect(within(dialog).getByText("Event ledger")).toBeTruthy();
    expect(within(dialog).getByText(/Jane Director/)).toBeTruthy();
    const indicatorsDetails = dialog.querySelector("details.cd-details");
    expect(indicatorsDetails).toHaveAttribute("open");

    // Multi-card switcher renders both cards and switches in place
    const switcher = within(dialog).getByRole("group", { name: "Switch thesis card" });
    expect(within(switcher).getByRole("button", { name: "Beyond GLP-1: Obesity & Peptide Drugs" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const secondCard = within(switcher).getByRole("button", { name: "Rare Genetic Disease Platforms" });
    fireEvent.click(secondCard);
    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith("/theses/{thesis_id}/member-panel", {
        params: {
          path: { thesis_id: "t-rare" },
          query: { name: "s-rytm", asof: "2026-11-22" },
        },
      });
    });

    // Escape closes the drawer without firing underlying document Escape listeners (e.g. MarketMap)
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(onEscapeBehind).not.toHaveBeenCalled();
  });

  it("navigates to Cockpit from the Drawer's Open in Cockpit action and hides switcher on single-card companies", async () => {
    mockMemberAndWindow({
      cards: [
        {
          thesis_id: "t-glp",
          thesis_name: "Beyond GLP-1: Obesity & Peptide Drugs",
          ticker: "RYTM",
          archived: false,
        },
      ],
    });

    function FakeDashboard({ onSelectThesis }: DashboardProps) {
      return (
        <button type="button" onClick={() => onSelectThesis("t-glp", "RYTM")}>
          RYTM
        </button>
      );
    }

    const { onSelectThesis } = renderShell(makeEntry(FakeDashboard));
    fireEvent.click(await screen.findByRole("button", { name: "RYTM" }));

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByText("RHYTHM PHARMACEUTICALS, INC.")).toBeTruthy();
    });

    // Single card -> quiet label, no switcher group (WB#3)
    expect(within(dialog).queryByRole("group", { name: "Switch thesis card" })).toBeNull();
    expect(within(dialog).getByText("Beyond GLP-1: Obesity & Peptide Drugs")).toBeTruthy();

    fireEvent.click(within(dialog).getByRole("button", { name: /Open in Cockpit/i }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-glp", "RYTM");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("bypasses the overlay when openCompanyInCockpit is set (Board, Ticker Lookup)", async () => {
    function FakeBoard({ onSelectThesis }: DashboardProps) {
      return (
        <button type="button" onClick={() => onSelectThesis("t-glp", "RYTM")}>
          RYTM
        </button>
      );
    }

    const { onSelectThesis } = renderShell(makeEntry(FakeBoard, { openCompanyInCockpit: true }));
    fireEvent.click(await screen.findByRole("button", { name: "RYTM" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-glp", "RYTM");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("degrades honestly on 404 when a company is no longer on the card's current basket", async () => {
    apiGet.mockResolvedValue({ data: undefined, error: { detail: "not found" }, response: { status: 404 } });

    function FakeHistory({ onSelectThesis }: DashboardProps) {
      return (
        <button type="button" onClick={() => onSelectThesis("t-glp", "OLDCO")}>
          OLDCO
        </button>
      );
    }

    const { onSelectThesis } = renderShell(makeEntry(FakeHistory));
    fireEvent.click(await screen.findByRole("button", { name: "OLDCO" }));

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByText(/not on this card's current basket/i)).toBeTruthy();
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /Open card in Cockpit/i }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-glp", null);
  });
});

describe("buildMemberOverlayEvents and visibleRangeForSpan", () => {
  it("numbers triggers, risks, insider buys, and non-voided decisions chronologically and skips voided or future rows", () => {
    const bars = [
      { d: "2026-11-17", close: 10 },
      { d: "2026-11-18", close: 11 },
      { d: "2026-11-19", close: 12 },
    ];
    const events = buildMemberOverlayEvents({
      bars,
      triggers: [
        { label: "Breakout", kind: "technical_breakout", grade: "core", ticker: "X", event_date: "2026-11-18", sources: [] },
        { label: "Too old", kind: "insider", grade: "core", ticker: "X", event_date: "2026-01-01", sources: [] },
      ],
      risks: [
        { label: "Breakdown", kind: "technical_breakdown", grade: null, ticker: "X", event_date: "2026-11-19", sources: [] },
      ],
      decisions: [
        { id: "d1", action: "take", decision_date: "2026-11-17", security_id: "s1", shares: 10, price: 10, reason: "go", voids: null, call_state: null, call_verdict: null, recorded_at: "2026-11-17T12:00:00Z", voided: false },
        { id: "d2", action: "take", decision_date: "2026-11-18", security_id: "s1", shares: 10, price: 11, reason: "voided", voids: null, call_state: null, call_verdict: null, recorded_at: "2026-11-18T12:00:00Z", voided: true },
      ],
    });
    expect(events.map((e) => [e.n, e.family, e.date])).toEqual([
      [1, "operator", "2026-11-17"],
      [2, "trigger", "2026-11-18"],
      [3, "risk", "2026-11-19"],
    ]);
  });

  it("computes trailing trading-day ranges and returns null for ALL", () => {
    const bars = Array.from({ length: 100 }, (_, i) => ({
      d: `2026-01-${String(i + 1).padStart(2, "0")}`,
      close: 10 + i,
    }));
    expect(visibleRangeForSpan(bars, "ALL")).toBeNull();
    expect(visibleRangeForSpan(bars, "3M")).toEqual({
      from: bars[100 - 1 - 63].d,
      to: bars[99].d,
    });
  });
});
