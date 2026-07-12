import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// A thesis whose basket has a `fund` member — the archetype Cockpit's old LOCAL ARCH_LABEL was missing, so it
// fell back to the raw key ("fund"). Tier 3 routes Cockpit through the shared archLabel(), which knows `fund`.
const fx = vi.hoisted(() => ({
  thesis: {
    id: "t-etf",
    name: "Uranium",
    narrative: "n",
    ticker: null,
    segments: [],
    basket: [
      {
        ticker: "URA",
        role: "core",
        archetype: "fund",
        security_id: "s-ura",
        detail: null,
        authored_by: "operator_set",
      },
    ],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  },
  // the Workbench scored read (Slice 3): computed market cap bridged by security_id onto the basket rows
  scored: { members: [{ security_id: "s-ura", market_cap: { value: 3.2e9 } }] },
}));

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  // the spine-list editors (A2) render inside the Cockpit sections — inert here
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

describe("Cockpit — basket archetype label (Tier-3 archLabel consolidation)", () => {
  it("renders a `fund` member as 'ETF sleeve' via the shared archLabel, not the raw key", () => {
    const { container } = render(
      <Cockpit
        thesisId="t-etf"
        asof="2026-06-20"
        onAsofChange={() => {}}
        onBack={() => {}}
        selectedName={null}
        onSelectName={() => {}}
      />,
    );
    // The chip's DOM text is "ETF sleeve" — the `.arch { text-transform: uppercase }` is visual-only, so we
    // assert on the textContent ("ETF sleeve"), NOT the rendered "ETF SLEEVE".
    const chip = container.querySelector(".arch.fund");
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toBe("ETF sleeve");
    expect(screen.getByText("ETF sleeve")).toBeInTheDocument();
    // The old incomplete-map fallback (`ARCH_LABEL[x] ?? x`) would have rendered the raw key "fund".
    expect(screen.queryByText("fund")).toBeNull();
  });

  it("surfaces computed market cap per basket row, bridged by security_id (Slice 3)", () => {
    render(
      <Cockpit
        thesisId="t-etf"
        asof="2026-06-20"
        onAsofChange={() => {}}
        onBack={() => {}}
        selectedName={null}
        onSelectName={() => {}}
      />,
    );
    expect(screen.getByText("Mkt cap")).toBeInTheDocument(); // the new column header
    expect(screen.getByText("$3.2B")).toBeInTheDocument(); // URA's computed cap (formatMarketCap(3.2e9))
  });
});
