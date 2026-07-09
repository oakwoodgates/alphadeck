import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The SECTION get-data button on the finalize screen: labeled with the ACTIVE section + its count, it
// hands the runner exactly the section's members (a slice of the shortlist, never the draft), shows the
// run state, renders the report quietly and the failures LOUDLY. The runner's own logic is covered in
// hooks.sectiondata.test — here a stateful fake drives the wiring.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const member = (sid: string, ticker: string, segment: string) => ({
    security_id: sid,
    ticker,
    archetype: null,
    archetype_hint: null,
    segment,
    purity: fig(null, null),
    runway: fig(null, null),
    catalysts: fig(0, 0),
    dilution: fig(null, null),
    market_cap: fig(null, null),
    fit: "unscored",
  });
  const segments = [
    { label: "DRAM & HBM Makers", descriptor: null },
    { label: "NAND & Storage", descriptor: null },
  ];
  const members = [
    member("s-mu", "MU", "DRAM & HBM Makers"),
    member("s-mram", "MRAM", "DRAM & HBM Makers"),
    member("s-sndk", "SNDK", "NAND & Storage"),
  ];
  const thesis = {
    id: "t1",
    name: "AI Memory",
    narrative: "n",
    ticker: null,
    segments,
    basket: [],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
    term_set: [] as unknown[],
  };
  const scored = { thesis_id: "t1", asof: "2026-06-08", segments, members };
  return { thesis, scored };
});

const h = vi.hoisted(() => ({
  run: vi.fn(),
  state: {
    running: false,
    report: null as null | {
      total: number;
      pricesOk: number;
      extractsOk: number;
      failures: { ticker: string; what: string }[];
    },
  },
}));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: [{ id: "t1", name: "AI Memory", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useSectionData: () => ({ run: h.run, running: h.state.running, report: h.state.report, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — the per-section get-data (the finalize screen's batch, bounded by the section)", () => {
  beforeEach(() => {
    h.run.mockReset();
    h.state = { running: false, report: null };
  });

  it("the button names the ACTIVE section + its count, and hands the runner exactly its members", async () => {
    const user = userEvent.setup();
    renderWb();
    // the first segment is active by default — 2 of the 3 names are in it
    const btn = screen.getByRole("button", { name: /get data — DRAM & HBM Makers \(2\)/ });
    await user.click(btn);
    expect(h.run).toHaveBeenCalledTimes(1);
    expect(h.run.mock.calls[0][0]).toEqual([
      { security_id: "s-mu", ticker: "MU" },
      { security_id: "s-mram", ticker: "MRAM" },
    ]); // the SECTION's members — never the whole basket, never the draft
  });

  it("while running, the button says so and is disabled (one deliberate spend at a time)", () => {
    h.state.running = true;
    renderWb();
    const btn = screen.getByRole("button", { name: /getting data for 2 names…/ });
    expect(btn).toBeDisabled();
  });

  it("the report renders quietly; failures render LOUD and named", () => {
    h.state.report = {
      total: 2,
      pricesOk: 1,
      extractsOk: 2,
      failures: [{ ticker: "MRAM", what: "price" }],
    };
    renderWb();
    expect(screen.getByText(/prices on 1 · candidates staged on 2 of 2/)).toBeInTheDocument();
    expect(screen.getByText(/⚑ failed: MRAM \(price\)/)).toBeInTheDocument();
  });
});
