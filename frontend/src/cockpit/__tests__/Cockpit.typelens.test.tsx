import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The business-type LENS (Business-Type M1): the same basket re-grouped by the scored super-sector.
// The load-bearing checks: rows land under their super headers, the un-scored name stays VISIBLE
// under Unclassified (#9 — a lens re-orders, never drops), the royalty overlay marks its row, each
// row keeps its own CALL-STATE dot (the call never disappears behind the lens), and the toggle is
// reversible (back to the call-state buckets).
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const scoredRow = (sid: string, name: string, over: Record<string, unknown>) => ({
    security_id: sid, name, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null, null), runway: fig(null, null), catalysts: fig(null, null),
    dilution: fig(null, null), market_cap: fig(null, null), fit: "", unconfirmed_estimates: 0,
    ...over,
  });
  return {
    thesis: {
      id: "t-mix",
      name: "Mixed theme",
      narrative: "n",
      ticker: null,
      segments: [],
      basket: [
        { ticker: "VST", role: "r", security_id: "s-vst", detail: null, authored_by: "operator_set" },
        { ticker: "UROY", role: "r", security_id: "s-uroy", detail: null, authored_by: "operator_set" },
        { ticker: "DARK", role: "r", security_id: "s-dark", detail: null, authored_by: "operator_set" },
      ],
      evidence: [],
      catalysts: [],
      kill_criteria: [],
      position: null,
    },
    // VST is ARMED (the call survives the lens); UROY/DARK are quiet
    call: {
      thesis_id: "t-mix", asof: "2026-07-11", state: "armed", verdict: "starter_entry",
      conviction_grade: "core", confirmation_grade: null, entry_grade: null, expression: "",
      exit_by: null, arm_until: null, catalyst_surface: [], confidence: null, actions: [],
      key_conviction: { turned: true, label: "Conviction", detail: "" },
      key_confirmation: { turned: false, label: "Confirmation", detail: "" },
      triggers_fired: [], missing: [], risk_signals: [], counter_case: [],
      armed_members: [
        {
          security_id: "s-vst", ticker: "VST", verdict: "starter_entry", conviction_grade: "core",
          confirmation_grade: null, entry_grade: null, confidence: null, exit_by: null,
          arm_until: null, lapsing: false, theme_armed: false, triggers: [],
        },
      ],
      watch_members: [],
    },
    scored: {
      members: [
        scoredRow("s-vst", "Vistra Corp", {
          business_type: "utilities", business_supersector: "energy_utilities",
        }),
        scoredRow("s-uroy", "Uranium Royalty Corp.", {
          business_type: "finance_brokers", business_supersector: "financials", royalty: true,
        }),
        // DARK: no scored identity fields at all -> the Unclassified group
      ],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: fx.call, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  // the armed card's decision rail (inert here — the lens test never acts)
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
  usePostDecision: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit() {
  return render(
    <Cockpit
      thesisId="t-mix"
      asof="2026-07-11"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
    />,
  );
}

describe("Cockpit — the business-type lens (Business-Type M1)", () => {
  it("re-groups the same rows by super-sector, keeps every row visible, keeps the call dots", () => {
    const { container } = renderCockpit();
    const headers = () =>
      [...container.querySelectorAll(".grp-h .lbl")].map((el) => el.textContent);
    // default lens: call-state buckets (the call is the product)
    expect(headers()).toEqual(["Armed", "Quiet"]);

    fireEvent.click(screen.getByRole("button", { name: "business type" }));

    // the super headers replace the buckets (strong-tape order; the visible tail LAST); every
    // basket row is still on screen
    expect(headers()).toEqual(["Energy & Utilities", "Financials", "Unclassified"]);
    expect(screen.getByText("VST")).toBeInTheDocument();
    expect(screen.getByText("UROY")).toBeInTheDocument();
    expect(screen.getByText("DARK")).toBeInTheDocument(); // un-scored — visible, never dropped

    // ...but each row's CALL-STATE dot survives (VST still reads armed via its dot title)
    const vstRow = screen.getByText("VST").closest("tr") as HTMLElement;
    expect(vstRow.className).toContain("bkt-armed");
    expect(vstRow.querySelector(".rowdot")?.getAttribute("title")).toBe("Armed");

    // the leaf chips + the royalty overlay marker
    expect(screen.getByText("utilities")).toBeInTheDocument();
    const uroyChip = container.querySelector(".btype.bt-financials");
    expect(uroyChip?.textContent).toContain("finance & brokers");
    expect(uroyChip?.querySelector(".bt-royalty")?.textContent).toBe("◈");

    // reversible: back to the call-state buckets, nothing lost
    fireEvent.click(screen.getByRole("button", { name: "call state" }));
    expect(headers()).toEqual(["Armed", "Quiet"]);
    expect(screen.getByText("DARK")).toBeInTheDocument();
  });
});
