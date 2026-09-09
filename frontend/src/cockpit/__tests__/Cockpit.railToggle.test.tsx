import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

// The two behaviors behind the basket-table width fix. The scroller itself is pure layout (jsdom
// has none), so what's asserted here is the part that IS behavior:
//
//   1. The call-rail collapse — a view dial that hands the rail's fixed 380px to the table. It must
//      be REVERSIBLE in one click (interaction principle #1) and must HIDE, never unmount, so the
//      control keeps pointing at something real. The state itself is URL-owned (?rail=0, wired in
//      App's CockpitRoute and covered in App.routes) — here it rides a stand-in owner, so what is
//      under test is the button's contract with whoever holds the state.
//   2. The capped company name — the cap is CSS, but the full name must stay reachable on the
//      element's title (principle #2: pruning hides, it never vanishes).
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  return {
    thesis: {
      id: "t-def", name: "Modern Defense", narrative: "n", ticker: null, segments: [],
      basket: [{ ticker: "TPCS", role: "core", security_id: "s-tpcs", detail: null, authored_by: "operator_set" }],
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: {
      members: [
        {
          security_id: "s-tpcs", ticker: "TPCS",
          // long enough that the 170px cap bites — the reason the title has to carry the full text
          name: "TECHPRECISION CORPORATION OF DELAWARE",
          sector: "x", exchange: null, category: null,
          business_type: null, business_supersector: null, business_type_override: null,
          royalty: false, instrument_kind: "equity",
          purity: fig(null), runway: fig(null), catalysts: fig(null),
          dilution: fig(null), market_cap: fig(null), fit: "", unconfirmed_estimates: 0,
        },
      ],
    },
    display: { members: [] },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: fx.display, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

/** Stands in for App's CockpitRoute, which really holds the rail state in ?rail=. */
function Harness() {
  const [railOpen, setRailOpen] = useState(true);
  return (
    <Cockpit
      thesisId="t-def"
      asof="2026-09-08"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
      railOpen={railOpen}
      onRailChange={setRailOpen}
    />
  );
}

function renderCockpit() {
  return render(<Harness />);
}

const toggle = () => screen.getByRole("button", { name: "Call" });

describe("Cockpit — the call-rail collapse", () => {
  it("starts open: the rail is the default reading, not the collapsed state", () => {
    const { container } = renderCockpit();
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("#cp-rail")).not.toHaveAttribute("hidden");
    expect(container.querySelector(".cp-body")?.className).not.toContain("rail-closed");
  });

  it("collapses and comes BACK — the action has a visible inverse (#1)", () => {
    const { container } = renderCockpit();
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(container.querySelector(".cp-body")?.className).toContain("rail-closed");
    // hidden, never unmounted: aria-controls keeps pointing at a real element
    const rail = container.querySelector("#cp-rail");
    expect(rail).toBeInTheDocument();
    expect(rail).toHaveAttribute("hidden");
    expect(toggle()).toHaveAttribute("aria-controls", "cp-rail");

    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("#cp-rail")).not.toHaveAttribute("hidden");
    expect(container.querySelector(".cp-body")?.className).not.toContain("rail-closed");
  });
});

describe("Cockpit — the capped company name", () => {
  it("keeps the FULL name on the title — the cap hides, it never vanishes (#2)", () => {
    renderCockpit();
    const cell = screen.getByText("TECHPRECISION CORPORATION OF DELAWARE");
    expect(cell).toHaveAttribute("title", "TECHPRECISION CORPORATION OF DELAWARE");
    expect(cell.className).toContain("co-name");
  });
});
