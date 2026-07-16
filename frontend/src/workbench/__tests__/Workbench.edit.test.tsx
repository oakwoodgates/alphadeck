import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// M1b — editing the name/narrative must NOT wipe the operator's authored chain. The fixture is a
// thesis WITH a 2-name basket across 2 segments; the load-bearing test proves an edit RESENDS them.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const basket = [
    { ticker: "OKLO", role: "entry_trigger", archetype: "high_beta", security_id: "s-oklo", segment: "reactors", authored_by: "operator_set", thesis_fit: null },
    { ticker: "LEU", role: "entry_trigger", archetype: "shovel", security_id: "s-leu", segment: "fuel", authored_by: "operator_set", thesis_fit: null },
  ];
  const segments = [
    { label: "reactors", descriptor: "catalyst-rich" },
    { label: "fuel", descriptor: null },
  ];
  const members = [
    { security_id: "s-oklo", ticker: "OKLO", archetype: "high_beta", segment: "reactors", purity: fig(4, 100), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(null, null), market_cap: fig(null, 1e10), fit: "pure-play" },
    { security_id: "s-leu", ticker: "LEU", archetype: "shovel", segment: "fuel", purity: fig(3, 77), runway: fig(4, 160), catalysts: fig(2, 1), dilution: fig(null, null), market_cap: fig(null, 3e9), fit: "core exposure" },
  ];
  const thesis = {
    id: "t-nuke",
    name: "Small modular nuclear",
    narrative: "AI power demand and the SMR build-out.",
    ticker: null,
    segments,
    basket,
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  };
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments, members };
  return { thesis, scored, basket, segments };
});

const h = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  reset: vi.fn(),
  promote: { isPending: false, isError: false, isSuccess: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Small modular nuclear", ticker: null, basket_size: 2, narrative: "x" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutateAsync: h.mutateAsync,
    mutate: vi.fn(),
    reset: h.reset,
    isPending: h.promote.isPending,
    isError: h.promote.isError,
    isSuccess: h.promote.isSuccess,
    error: h.promote.error,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  // the section-data runner + the per-name price pull (inert here; their own suites cover them)
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — edit a thesis's narrative (M1b, the wipe-trap)", () => {
  beforeEach(() => {
    h.mutateAsync.mockReset();
    h.reset.mockReset();
    h.promote = { isPending: false, isError: false, isSuccess: false, error: null };
  });

  it("opens the edit form pre-filled with the current name + narrative", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit narrative/i }));
    expect(screen.getByLabelText("thesis name")).toHaveValue("Small modular nuclear");
    expect(screen.getByLabelText("thesis narrative")).toHaveValue(
      "AI power demand and the SMR build-out.",
    );
  });

  it("RESENDS the existing basket + segments on a narrative edit (the chain survives)", async () => {
    h.mutateAsync.mockResolvedValue({ id: "t-nuke" });
    const user = userEvent.setup();
    renderWb();

    await user.click(screen.getByRole("button", { name: /edit narrative/i }));
    const narrative = screen.getByLabelText("thesis narrative");
    await user.clear(narrative);
    await user.type(narrative, "Reactors plus the fuel cycle — the picks and shovels.");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(h.mutateAsync).toHaveBeenCalledTimes(1));
    // the update branch: SAME id, new narrative, and the EXISTING chain resent verbatim
    expect(h.mutateAsync).toHaveBeenCalledWith({
      id: "t-nuke",
      name: "Small modular nuclear",
      narrative: "Reactors plus the fuel cycle — the picks and shovels.",
      ticker: null,
      basket: fx.basket,
      segments: fx.segments,
    });
    // explicit wipe-trap guard: not the create branch (real id) and the chain is NOT emptied
    const payload = h.mutateAsync.mock.calls[0][0];
    expect(payload.id).toBe("t-nuke");
    expect(payload.basket).toHaveLength(2);
    expect(payload.segments).toHaveLength(2);
  });

  it("reassures that the chain is preserved while editing", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit narrative/i }));
    expect(screen.getByText(/touch your 2-name chain/i)).toBeInTheDocument();
  });

  it("Cancel closes the edit form without writing", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit narrative/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(h.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /edit narrative/i })).toBeInTheDocument();
  });
});
