import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

const fx = vi.hoisted(() => ({
  thesis: {
    id: "t-nuke",
    name: "Small-scale nuclear",
    narrative: "Power-hungry data centers lift nuclear supply.",
    ticker: null,
    segments: [{ label: "Miners", descriptor: null }],
    basket: [
      { ticker: "J", role: "core", security_id: "s-j", segment: "Miners", detail: null, authored_by: "operator_set" },
      { ticker: "UUUU", role: "core", security_id: "s-uuuu", segment: "Miners", detail: null, authored_by: "operator_set" },
      { ticker: "NNE", role: "core", security_id: "s-nne", segment: "Miners", detail: null, authored_by: "operator_set" },
      { ticker: "URA", role: "fund", security_id: "s-ura", segment: "ETFs", detail: null, authored_by: "operator_set" },
      { ticker: "CCJ", role: "core", security_id: "s-ccj", segment: "Miners", detail: null, authored_by: "operator_set" },
      { ticker: "SMR", role: "core", security_id: "s-smr", segment: "Miners", detail: null, authored_by: "operator_set" },
    ],
    evidence: [],
    catalysts: [
      { id: "c-1", label: "NRC design approval", when_date: "2026-07-20", when_label: null, kind: "regulatory" },
    ],
    kill_criteria: [{ id: "k-1", text: "Capex pause by hyperscalers" }],
    position: { entry_price: 100, current_price: 115, opened_on: "2026-07-01", security_id: "s-j" },
  },
  call: {
    thesis_id: "t-nuke",
    asof: "2026-07-11",
    state: "managing",
    verdict: "managing",
    conviction_grade: "core",
    confirmation_grade: "core",
    entry_grade: "core",
    expression: "Managing open position",
    exit_by: "2026-11-11",
    arm_until: "2026-07-17",
    catalyst_surface: [],
    confidence: 0.72,
    key_conviction: { turned: true, label: "Conviction", detail: "" },
    key_confirmation: { turned: true, label: "Confirmation", detail: "" },
    triggers_fired: [
      { label: "insider cluster on UUUU", kind: "insider", grade: "core", ticker: "UUUU", sources: [] },
    ],
    risk_signals: [
      { label: "dilution risk on UUUU", kind: "dilution_risk", grade: null, ticker: "UUUU", sources: [] },
    ],
    missing: [],
    counter_case: "",
    armed_members: [
      {
        security_id: "s-j", ticker: "J", verdict: "managing", conviction_grade: "core",
        confirmation_grade: "core", entry_grade: "core", confidence: 0.72, exit_by: "2026-11-11",
        arm_until: "2026-07-17", lapsing: false, entry_window_closing: false, theme_armed: false, triggers: [],
      },
      {
        security_id: "s-uuuu", ticker: "UUUU", verdict: "starter_entry", conviction_grade: "core",
        confirmation_grade: "flip", entry_grade: "flip", confidence: 0.55, exit_by: "2026-10-10",
        arm_until: "2026-07-17", lapsing: false, entry_window_closing: false, theme_armed: false, triggers: [],
      },
    ],
    watch_members: [
      {
        security_id: "s-nne", ticker: "NNE", verdict: null, conviction_grade: null,
        confirmation_grade: "core", entry_grade: null, confidence: null, exit_by: null,
        arm_until: "2026-07-21", lapsing: false, entry_window_closing: false, theme_armed: false, triggers: [],
      },
    ],
  },
  scored: {
    members: [
      { security_id: "s-j", name: "Jacobs Solutions", market_cap: { value: 16e9 }, business_type: "industrials_machinery", business_supersector: "industrials", purity: { pips: null, value: null, provenance: [] }, runway: { pips: null, value: null, provenance: [] }, catalysts: { pips: null, value: null, provenance: [] }, dilution: { pips: null, value: null, provenance: [] } },
      { security_id: "s-uuuu", name: "Energy Fuels", market_cap: { value: 1.2e9 }, business_type: "miner", business_supersector: "materials", purity: { pips: null, value: null, provenance: [] }, runway: { pips: null, value: null, provenance: [] }, catalysts: { pips: null, value: null, provenance: [] }, dilution: { pips: null, value: null, provenance: [] } },
      { security_id: "s-nne", name: "Nano Nuclear", market_cap: { value: 6e8 }, business_type: "utilities", business_supersector: "energy_utilities", purity: { pips: null, value: null, provenance: [] }, runway: { pips: null, value: null, provenance: [] }, catalysts: { pips: null, value: null, provenance: [] }, dilution: { pips: null, value: null, provenance: [] } },
      { security_id: "s-ura", name: "Global X Uranium ETF", instrument_kind: "etf", market_cap: { value: 3e9 }, purity: { pips: null, value: null, provenance: [] }, runway: { pips: null, value: null, provenance: [] }, catalysts: { pips: null, value: null, provenance: [] }, dilution: { pips: null, value: null, provenance: [] } },
      { security_id: "s-ccj", name: "Cameco", market_cap: { value: 20e9 }, business_type: "miner", business_supersector: "materials", purity: { pips: null, value: null, provenance: [] }, runway: { pips: null, value: null, provenance: [] }, catalysts: { pips: null, value: null, provenance: [] }, dilution: { pips: null, value: null, provenance: [] } },
      { security_id: "s-smr", name: "NuScale Power", market_cap: { value: 4e9 }, business_type: "utilities", business_supersector: "energy_utilities", purity: { pips: null, value: null, provenance: [] }, runway: { pips: null, value: null, provenance: [] }, catalysts: { pips: null, value: null, provenance: [] }, dilution: { pips: null, value: null, provenance: [] } },
    ],
  },
  display: {
    members: [
      {
        security_id: "s-uuuu",
        ticker: "UUUU",
        signals: [
          {
            kind: "rvol",
            label: "RVOL",
            metrics: [{ key: "rvol", label: "8d", value: 2.8, unit: "ratio", note: null }],
            basis: { source: "bars", params: { loud_mult: 2.0 }, bars_used: 9 },
          },
          {
            kind: "insider_flow_90d",
            label: "Insider",
            metrics: [
              { key: "buy_count", label: "buys", value: 3, unit: "count", note: null },
              { key: "distinct_buyers", label: "buyers", value: 2, unit: "count", note: null },
            ],
            basis: { source: "form4", params: {} },
          },
        ],
      },
    ],
  },
}));

vi.mock("../../api/hooks", () => ({
  useUserCompanyValues: () => ({ data: undefined, isLoading: false, error: null }),
  useUserThesisValues: () => ({ data: undefined, isLoading: false, error: null }),
  useRaiseFlag: () => ({ mutate: () => {}, reset: () => {}, isPending: false, isError: false, error: null }),
  useWithdrawFlag: () => ({ mutate: () => {}, reset: () => {}, isPending: false, isError: false, error: null }),
  useFundLaunches: () => ({ data: undefined, isLoading: false, error: null }),
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: fx.call, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: fx.display, isLoading: false, error: null }),
  useThesisDataFlags: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
  usePostDecision: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  useMemberPanel: () => ({ data: undefined, isLoading: false, isPending: false, isError: false, isPlaceholderData: false }),
  useEpisodePriceWindow: () => ({ data: undefined, isLoading: false, isError: false }),
}));

import type { ThesisSummary } from "../../api/hooks";
import { ClockProvider } from "../../clock";
import { Cockpit } from "../Cockpit";

const THESES: ThesisSummary[] = [
  { id: "t-nuke", name: "Small-scale nuclear", ticker: null, basket_size: 6, archived: false, meta_index: false } as ThesisSummary,
  { id: "t-grid", name: "Grid Storage", ticker: null, basket_size: 10, archived: false, meta_index: false } as ThesisSummary,
];

function Harness({ onSwitch = () => {} }: { onSwitch?: (id: string) => void }) {
  const [name, setName] = useState<string | null>(null);
  const [rail, setRail] = useState(true);
  return (
    <ClockProvider value={{ currentNight: "2026-07-11", marketToday: "2026-07-11", actionDay: "2026-07-13", failed: false }}>
      <Cockpit
        thesisId="t-nuke"
        theses={THESES}
        onSwitchThesis={onSwitch}
        asof="2026-07-11"
        onAsofChange={() => {}}
        selectedName={name}
        onSelectName={setName}
        railOpen={rail}
        onRailChange={setRail}
      />
    </ClockProvider>
  );
}

describe("Cockpit — operator trade-readiness improvements", () => {
  it("renders the open position bar with EOD unrealized move and opens the held ticker drawer on click", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness />);
    const pos = screen.getByRole("region", { name: "Open position" });
    expect(pos).toHaveTextContent("Position open");
    expect(pos).toHaveTextContent("entered Jul 1 @ $100.00");
    expect(pos).toHaveTextContent("EOD $115.00");
    expect(pos).toHaveTextContent("+15.0%");

    await user.click(screen.getByRole("button", { name: "J", exact: true }));
    expect(container.querySelector(".drawer-panel .np-tk")?.textContent).toBe("J");
  });

  it("surfaces the compact thesis pulse and marks only risky basket rows with a quiet risk indicator", () => {
    const { container } = render(<Harness />);
    const pulse = screen.getByLabelText("Thesis pulse");
    expect(pulse).toHaveTextContent("1 managing");
    expect(pulse).toHaveTextContent("1 armed");
    expect(pulse).toHaveTextContent("1 watch");
    expect(pulse).toHaveTextContent("NRC design approval");
    expect(pulse).toHaveTextContent("1 risk signal · 1 name");
    expect(pulse).toHaveTextContent("1 kill criterion");

    // Only UUUU carries an active risk signal -> only UUUU gets .risk-mark (WB#3)
    const marks = [...container.querySelectorAll("tr.bkt .risk-mark")];
    expect(marks).toHaveLength(1);
    expect(marks[0].closest("tr")?.querySelector("td.tk")?.textContent).toContain("UUUU");
    expect(marks[0]).toHaveAttribute("title", "Active risk signal: dilution risk on UUUU");
  });

  it("filters the basket reversibly (search, Active signals, Insider buys, High RVOL, Hide silent) and collapses/expands all groups", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness />);

    expect(container.querySelectorAll("tr.bkt")).toHaveLength(6);

    // Filter to Active signals (Managing + Armed + Watch = 3 of 6)
    await user.click(screen.getByRole("button", { name: /Active signals \(3\)/ }));
    expect(container.querySelectorAll("tr.bkt")).toHaveLength(3);
    expect(screen.getByText("showing 3 of 6")).toBeInTheDocument();

    // Clear restores every row (WB#1 / WB#2)
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(container.querySelectorAll("tr.bkt")).toHaveLength(6);

    // Filter by High RVOL (1 name: UUUU)
    await user.click(screen.getByRole("button", { name: /High RVOL \(1\)/ }));
    expect(container.querySelectorAll("tr.bkt")).toHaveLength(1);
    expect(container.querySelector("tr.bkt td.tk")?.textContent).toContain("UUUU");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    // Search by company name
    await user.type(screen.getByRole("searchbox", { name: "Filter basket" }), "cameco");
    expect(container.querySelectorAll("tr.bkt")).toHaveLength(1);
    expect(container.querySelector("tr.bkt td.tk")?.textContent).toBe("CCJ");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    // Collapse all / Expand all
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(container.querySelectorAll("tr.bkt.folded")).toHaveLength(6);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(container.querySelectorAll("tr.bkt.folded")).toHaveLength(0);
  });

  it("opens a company drawer when clicking a MemberMenu row or a CallCard trigger ticker", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness />);

    const uuuuButtons = screen.getAllByRole("button", { name: /UUUU/ });
    // First click MemberMenu row for UUUU
    const mmBtn = uuuuButtons.find((b) => b.classList.contains("mm-btn")) as HTMLElement;
    expect(mmBtn).toBeDefined();
    await user.click(mmBtn);
    expect(container.querySelector(".drawer-panel .np-tk")?.textContent).toBe("UUUU");

    // Clicking trigger ticker chip on CallCard also selects that name
    await user.click(screen.getByTitle("close (Esc)"));
    expect(container.querySelector(".drawer-panel")).toBeNull();
    const trgBtn = uuuuButtons.find((b) => b.classList.contains("trg-tk-btn")) as HTMLElement;
    expect(trgBtn).toBeDefined();
    await user.click(trgBtn);
    expect(container.querySelector(".drawer-panel .np-tk")?.textContent).toBe("UUUU");
  });

  it("supports keyboard shortcuts ([ / ] to step theses, r to toggle rail) outside form inputs", () => {
    const onSwitch = vi.fn();
    render(<Harness onSwitch={onSwitch} />);

    fireEvent.keyDown(window, { key: "]" });
    expect(onSwitch).toHaveBeenCalledWith("t-grid");

    const toggle = screen.getByRole("button", { name: "Call" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(window, { key: "r" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    // Ignored when typing inside search input
    const search = screen.getByRole("searchbox", { name: "Filter basket" });
    search.focus();
    fireEvent.keyDown(search, { key: "r" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});
