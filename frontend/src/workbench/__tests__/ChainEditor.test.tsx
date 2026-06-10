import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the promote (save) mutation + the resolver typeahead. The draft logic
// (useChainDraft) is the REAL hook — exercised through the editor UI.
const h = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  usePromoteThesis: () => ({
    mutate: h.mutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
  // any non-empty query surfaces one match (a discovery net); the operator picks the exact row
  useResolveSecurities: (q: string) => ({
    data: q?.trim() ? [{ security_id: "s-ccj", ticker: "CCJ", name: "Cameco" }] : [],
    isFetching: false,
  }),
}));

import { ChainEditor } from "../ChainEditor";

const flatThesis = {
  id: "t1",
  name: "Nuclear",
  narrative: "AI power.",
  ticker: null,
  segments: [] as { label: string; descriptor: string | null }[],
  basket: [
    {
      ticker: "OKLO",
      role: "r",
      archetype: "high_beta",
      security_id: "s-oklo",
      segment: null,
      authored_by: "operator_set",
    },
  ],
  evidence: [],
  catalysts: [],
  kill_criteria: [],
  position: null,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

describe("ChainEditor — authoring", () => {
  it("decomposes a flat basket: add a link, place a name, then save the full draft", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    h.mutate.mockReset();
    h.mutate.mockImplementation((_body: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );

    render(<ChainEditor thesis={flatThesis} onDone={onDone} />);

    // add a link
    await user.type(screen.getByLabelText("new link label"), "reactors");
    await user.click(screen.getByRole("button", { name: "+ link" }));
    expect(screen.getByLabelText("link 1 label")).toHaveValue("reactors");

    // place OKLO into it
    await user.selectOptions(screen.getByLabelText("place OKLO"), "reactors");
    expect(screen.getByLabelText("place OKLO")).toHaveValue("reactors");

    // save -> the full edited draft is POSTed, and the editor exits
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(h.mutate).toHaveBeenCalledTimes(1);
    const body = h.mutate.mock.calls[0][0] as {
      segments: unknown[];
      basket: Record<string, unknown>[];
    };
    expect(body.segments).toEqual([{ label: "reactors", descriptor: null }]);
    expect(body.basket).toHaveLength(1);
    expect(body.basket[0]).toMatchObject({ ticker: "OKLO", segment: "reactors" });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("adds a name via the resolver typeahead (search → pick → classify → add)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.type(screen.getByLabelText("search securities"), "cc");
    await user.click(await screen.findByRole("button", { name: /CCJ/ }));
    await user.type(screen.getByLabelText("role"), "the uranium anchor");
    await user.click(screen.getByRole("button", { name: "add to basket" }));

    // a new member row exists for the added name
    expect(screen.getByLabelText("place CCJ")).toBeInTheDocument();
  });

  it("removes a name", async () => {
    const user = userEvent.setup();
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);
    expect(screen.getByText("OKLO")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "remove OKLO" }));
    expect(screen.queryByText("OKLO")).not.toBeInTheDocument();
  });

  it("reorders links and un-places a name when its link is removed", async () => {
    const user = userEvent.setup();
    const withSegs = {
      ...flatThesis,
      segments: [
        { label: "a", descriptor: null },
        { label: "b", descriptor: null },
      ],
      basket: [{ ...flatThesis.basket[0], segment: "a" }],
    };
    render(<ChainEditor thesis={withSegs} onDone={vi.fn()} />);

    // reorder: move "a" later -> [b, a]
    await user.click(screen.getByRole("button", { name: "move a later" }));
    expect(
      screen.getAllByLabelText(/^link \d+ label$/).map((i) => (i as HTMLInputElement).value),
    ).toEqual(["b", "a"]);

    // remove "a" -> its placed member (OKLO) falls back to unplaced (never orphaned)
    await user.click(screen.getByRole("button", { name: "remove a" }));
    expect(screen.getByLabelText("place OKLO")).toHaveValue("");
  });
});
