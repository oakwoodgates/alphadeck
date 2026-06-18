import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// M1a — the create-thesis front door. The promote spy + state live in vi.hoisted so the mock factory
// (hoisted above imports) returns ONE stable spy across renders, and a test can flip the promote state.
const h = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  reset: vi.fn(),
  promote: { isPending: false, isError: false, isSuccess: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  // zero theses — the create button must render even with an empty universe (the entry point)
  useTheses: () => ({ data: [] }),
  useThesis: () => ({ data: undefined }),
  useWorkbenchScored: () => ({ data: undefined, isLoading: false, error: null }),
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
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — create a thesis from a new narrative (M1a)", () => {
  beforeEach(() => {
    h.mutateAsync.mockReset();
    h.reset.mockReset();
    h.promote = { isPending: false, isError: false, isSuccess: false, error: null };
  });

  it("renders the New thesis button even with zero theses (the entry point)", () => {
    renderWb();
    expect(screen.getByRole("button", { name: /new thesis/i })).toBeInTheDocument();
  });

  it("create posts a null id with an empty basket/segments and a null ticker", async () => {
    h.mutateAsync.mockResolvedValue({ id: "t-new" });
    const user = userEvent.setup();
    renderWb();

    await user.click(screen.getByRole("button", { name: /new thesis/i }));
    await user.type(screen.getByLabelText("thesis name"), "Small modular nuclear");
    await user.type(
      screen.getByLabelText("thesis narrative"),
      "AI power demand drives an SMR build-out.",
    );
    await user.click(screen.getByRole("button", { name: /create thesis/i }));

    await waitFor(() => expect(h.mutateAsync).toHaveBeenCalledTimes(1));
    expect(h.mutateAsync).toHaveBeenCalledWith({
      id: null,
      name: "Small modular nuclear",
      narrative: "AI power demand drives an SMR build-out.",
      ticker: null,
      basket: [],
      segments: [],
    });
  });

  it("trims input and stays disabled until both fields are filled", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.click(screen.getByRole("button", { name: /new thesis/i }));

    const create = screen.getByRole("button", { name: /create thesis/i });
    expect(create).toBeDisabled(); // empty
    await user.type(screen.getByLabelText("thesis name"), "Nuclear");
    expect(create).toBeDisabled(); // name only — narrative still empty
    await user.type(screen.getByLabelText("thesis narrative"), "why now");
    expect(create).toBeEnabled();
  });

  it("surfaces a promote error inline and keeps the form open (nothing lost)", async () => {
    h.mutateAsync.mockRejectedValue({ detail: "name already exists" });
    h.promote.isError = true;
    h.promote.error = { detail: "name already exists" };
    const user = userEvent.setup();
    renderWb();

    await user.click(screen.getByRole("button", { name: /new thesis/i }));
    await user.type(screen.getByLabelText("thesis name"), "Dup");
    await user.type(screen.getByLabelText("thesis narrative"), "x");
    await user.click(screen.getByRole("button", { name: /create thesis/i }));

    // the error is shown, not swallowed — and the create form is still open (the inputs survive)
    expect(await screen.findByText(/Couldn't create — name already exists/i)).toBeInTheDocument();
    expect(screen.getByLabelText("thesis name")).toHaveValue("Dup");
  });
});
