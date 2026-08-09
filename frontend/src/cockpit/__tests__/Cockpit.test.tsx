import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// A thesis whose basket has an ETF sleeve member — the Type cell keys on the SCORED row's
// instrument_kind (a fund has no SIC, so the business-type maps abstain; the instrument is the label).
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
  // the Workbench scored read (Slice 3): computed market cap + identity bridged by security_id
  scored: { members: [{ security_id: "s-ura", ticker: "URA", name: "Global X Uranium ETF", instrument_kind: "etf", market_cap: { value: 3.2e9 } }] },
}));

const exportSpy = vi.hoisted(() => vi.fn());
vi.mock("../../util/exportNames", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../util/exportNames")>();
  return { ...mod, exportKeptNames: exportSpy };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, error: null }),
  // the spine-list editors (A2) render inside the Cockpit sections — inert here
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

describe("Cockpit — the basket Type cell (Business-Type M1)", () => {
  beforeEach(() => {
    exportSpy.mockReset();
  });

  it("exports the board basket with ticker and name", async () => {
    const user = userEvent.setup();
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

    await user.click(screen.getByRole("button", { name: "export 1 board names" }));

    expect(exportSpy).toHaveBeenCalledWith({
      thesisName: "Uranium",
      stage: "board",
      asof: "2026-06-20",
      rows: [{ ticker: "URA", name: "Global X Uranium ETF" }],
    });
  });

  it("renders an `instrument_kind: etf` member as 'ETF sleeve' in the Type cell", () => {
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
    // The chip's DOM text is "ETF sleeve" — the `.btype { text-transform: uppercase }` is visual-only,
    // so we assert on the textContent ("ETF sleeve"), NOT the rendered "ETF SLEEVE".
    const chip = container.querySelector(".btype.bt-etf");
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toBe("ETF sleeve");
    expect(screen.getByText("ETF sleeve")).toBeInTheDocument();
    // the sleeve label keys on the instrument, never a raw enum key leaking to the UI
    expect(screen.queryByText("etf")).toBeNull();
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
