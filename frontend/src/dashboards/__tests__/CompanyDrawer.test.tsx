import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { lazy, useEffect } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ClockProvider, browserClock } from "../../clock";
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

const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });

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
          headline: { direction: "up", text: "50d over 200d · rising", detail: "" },
          chips: [{ label: "50d", value: "$105.00" }],
          events: [],
          basis: "Daily closes",
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

describe("DashboardShell CompanyDrawer", () => {
  beforeEach(() => {
    apiGet.mockReset();
  });

  it("opens full NamePanel in Drawer on company click and leaves thesis clicks navigating to Cockpit", async () => {
    apiGet.mockResolvedValue({ data: makePayload(), error: undefined, response: { status: 200 } });
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

    // Full NamePanel sections are rendered (nothing cut off)
    expect(within(dialog).getByText(/CORE entry/i)).toBeTruthy();
    expect(within(dialog).getByText("88%")).toBeTruthy();
    expect(within(dialog).getByText("52-week breakout on RYTM")).toBeTruthy();
    expect(within(dialog).getByText("Fast breakdown on RYTM")).toBeTruthy();
    expect(within(dialog).getByText(/Rhythm Pharmaceuticals treats rare genetic obesity/)).toBeTruthy();
    expect(within(dialog).getByText("Scoring snapshot")).toBeTruthy();
    expect(within(dialog).getByText("Decision log · this name")).toBeTruthy();
    expect(within(dialog).getByText(/starter entry/)).toBeTruthy();

    // Multi-card switcher renders both cards and switches in place
    const switcher = within(dialog).getByRole("group", { name: "Switch thesis card" });
    expect(within(switcher).getByRole("button", { name: "Beyond GLP-1: Obesity & Peptide Drugs" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const secondCard = within(switcher).getByRole("button", { name: "Rare Genetic Disease Platforms" });
    apiGet.mockResolvedValueOnce({
      data: makePayload({
        thesis_id: "t-rare",
        thesis_name: "Rare Genetic Disease Platforms",
      }),
      error: undefined,
      response: { status: 200 },
    });
    fireEvent.click(secondCard);
    await waitFor(() => {
      expect(apiGet).toHaveBeenLastCalledWith("/theses/{thesis_id}/member-panel", {
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
    apiGet.mockResolvedValue({
      data: makePayload({
        cards: [
          {
            thesis_id: "t-glp",
            thesis_name: "Beyond GLP-1: Obesity & Peptide Drugs",
            ticker: "RYTM",
            archived: false,
          },
        ],
      }),
      error: undefined,
      response: { status: 200 },
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

    // Open in Cockpit closes drawer and calls parent onSelectThesis
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
