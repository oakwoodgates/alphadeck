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
}));

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

describe("Cockpit — basket archetype label (Tier-3 archLabel consolidation)", () => {
  it("renders a `fund` member as 'ETF sleeve' via the shared archLabel, not the raw key", () => {
    const { container } = render(
      <Cockpit thesisId="t-etf" asof="2026-06-20" onAsofChange={() => {}} onBack={() => {}} />,
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
});
