import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// D — Save-Chain re-entry legibility: a SAVED exit from the chain editor surfaces the "your saved basket
// is editable on return" note on the scored view; a plain Done/Discard exit doesn't, and re-entering the
// editor clears it. The mock spans Workbench + the (real) ChainEditor it mounts.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const basket = [
    {
      ticker: "OKLO",
      role: "r",
      archetype: "high_beta",
      security_id: "s-oklo",
      segment: "reactors",
      conviction: null,
      authored_by: "operator_set",
      thesis_fit: null,
    },
  ];
  const segments = [{ label: "reactors", descriptor: null }];
  const members = [
    {
      security_id: "s-oklo",
      ticker: "OKLO",
      archetype: "high_beta",
      segment: "reactors",
      purity: fig(4, 100),
      runway: fig(4, null),
      catalysts: fig(1, 1),
      dilution: fig(null, null),
      market_cap: fig(null, 1e10),
      fit: "pure-play",
    },
  ];
  const thesis = {
    id: "t-nuke",
    name: "Small modular nuclear",
    narrative: "AI power demand.",
    ticker: null,
    segments,
    basket,
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
    term_set: [] as unknown[],
  };
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments, members };
  return { thesis, scored };
});

const h = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({
    data: [{ id: "t-nuke", name: "Small modular nuclear", ticker: null, basket_size: 1, narrative: "x" }],
  }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.mutate,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useStartDraft: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDraftJobStatus: () => ({ data: undefined, isError: false }),
  // the run-loader picker (no saved runs here → RunPicker self-hides; its own suite covers it)
  useThesisRuns: () => ({ data: [], isError: false }),
  useLoadThesisRun: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useProduceTerms: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEditTerms: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useRecommendTiers: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — Save-Chain re-entry legibility (D)", () => {
  beforeEach(() => {
    h.mutate.mockReset();
    // the editor's Save resolves: the mutation fires its onSuccess (the saved exit)
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
  });

  it("a saved exit surfaces the note; re-entering the editor clears it", async () => {
    const user = userEvent.setup();
    renderWb();

    await user.click(screen.getByRole("button", { name: /edit the chain/i }));
    await user.click(screen.getByRole("button", { name: "Save chain" }));

    // back on the scored view, the reversibility of Save is SAID, with honest scope
    expect(await screen.findByText(/Chain saved/)).toBeInTheDocument();
    expect(screen.getByText(/editing your saved basket/)).toBeInTheDocument();

    // the note refers to the exit that just happened — re-entering clears it
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));
    expect(screen.queryByText(/Chain saved/)).not.toBeInTheDocument();
  });

  it("a Done (no save) exit shows NO note", async () => {
    const user = userEvent.setup();
    renderWb();

    await user.click(screen.getByRole("button", { name: /edit the chain/i }));
    await user.click(screen.getByRole("button", { name: "Done" }));

    expect(h.mutate).not.toHaveBeenCalled();
    expect(screen.queryByText(/Chain saved/)).not.toBeInTheDocument();
  });
});
