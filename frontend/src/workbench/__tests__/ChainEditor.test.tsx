import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the promote (save) mutation, the resolver typeahead, and the narrative→chain
// drafter. The draft logic (useChainDraft) is the REAL hook — exercised through the editor UI.
const h = vi.hoisted(() => ({ mutate: vi.fn(), refetch: vi.fn() }));

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
    data: q?.trim() ? [{ security_id: "s-ccj", ticker: "CCJ", name: "Cameco", cik: "0001" }] : [],
    isFetching: false,
  }),
  // the drafter: the test drives refetch()'s resolved value per-case (an explicit "Draft from narrative")
  useDraftChain: () => ({ refetch: h.refetch, isFetching: false }),
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

// A drafted chain the endpoint would return — one PLACED name in one segment, unless overridden.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const draft = (placements: unknown[], segments: unknown[] = [{ label: "reactors", descriptor: null }]) =>
  ({ data: { thesis_id: "t1", segments, placements } }) as any;

const PLACED_SMR = {
  name: "NuScale Power",
  ticker: "SMR",
  prose: "the only NRC-approved SMR designer",
  segment: "reactors",
  status: "placed",
  security_id: "s-smr",
  candidates: [],
};

const VERIFY_ALKS = {
  name: "Alkermes plc",
  ticker: "ALKS",
  prose: "ketamine-adjacent CNS pipeline",
  segment: "therapeutics",
  status: "verify",
  security_id: "s-alks",
  candidates: [],
};

beforeEach(() => {
  h.mutate.mockReset();
  h.refetch.mockReset();
});

describe("ChainEditor — authoring", () => {
  it("decomposes a flat basket: add a link, place a name, then save the full draft", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    h.mutate.mockImplementation((_body: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );

    render(<ChainEditor thesis={flatThesis} onDone={onDone} />);

    await user.type(screen.getByLabelText("new link label"), "reactors");
    await user.click(screen.getByRole("button", { name: "+ link" }));
    expect(screen.getByLabelText("link 1 label")).toHaveValue("reactors");

    await user.selectOptions(screen.getByLabelText("place OKLO"), "reactors");
    expect(screen.getByLabelText("place OKLO")).toHaveValue("reactors");

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

  it("adds a name via the resolver typeahead (search → pick → classify → add), CIK shown", async () => {
    const user = userEvent.setup();
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.type(screen.getByLabelText("search securities"), "cc");
    const match = await screen.findByRole("button", { name: /CCJ/ });
    expect(match).toHaveTextContent("CIK 0001"); // the homonym tell is surfaced
    await user.click(match);
    await user.type(screen.getByLabelText("role"), "the uranium anchor");
    await user.click(screen.getByRole("button", { name: "add to basket" }));

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

    await user.click(screen.getByRole("button", { name: "move a later" }));
    expect(
      screen.getAllByLabelText(/^link \d+ label$/).map((i) => (i as HTMLInputElement).value),
    ).toEqual(["b", "a"]);

    await user.click(screen.getByRole("button", { name: "remove a" }));
    expect(screen.getByLabelText("place OKLO")).toHaveValue("");
  });
});

describe("ChainEditor — draft from narrative (S5 5c)", () => {
  it("loads a PLACED name as a drafted, badged placement with its prose", async () => {
    const user = userEvent.setup();
    h.refetch.mockResolvedValue(draft([PLACED_SMR]));
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(await screen.findByLabelText("place SMR")).toBeInTheDocument(); // auto-placed
    expect(screen.getByText("drafted")).toBeInTheDocument(); // badged drafted
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue(
      "the only NRC-approved SMR designer",
    );
  });

  it("accepting a drafted name flips it to operator_set", async () => {
    const user = userEvent.setup();
    h.refetch.mockResolvedValue(draft([PLACED_SMR]));
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("place SMR");
    expect(screen.getByText("drafted")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "accept SMR" }));
    expect(screen.queryByText("drafted")).not.toBeInTheDocument(); // no longer drafted
    expect(screen.getAllByText("operator").length).toBeGreaterThanOrEqual(2); // SMR + OKLO
  });

  it("editing a drafted name's prose flips it to operator_edited", async () => {
    const user = userEvent.setup();
    h.refetch.mockResolvedValue(draft([PLACED_SMR]));
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    const prose = await screen.findByLabelText("thesis-fit for SMR");
    await user.type(prose, " — refined");

    expect(screen.getByText("edited")).toBeInTheDocument(); // operator_edited
    expect(screen.queryByText("drafted")).not.toBeInTheDocument();
  });

  it("an AMBIGUOUS name enters the basket ONLY by an explicit pick (with the picked security_id + CIK)", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    h.refetch.mockResolvedValue(
      draft(
        [
          {
            name: "Centrus",
            ticker: null,
            prose: "HALEU supplier",
            segment: "fuel",
            status: "ambiguous",
            security_id: null,
            candidates: [
              { security_id: "s-leu", ticker: "LEU", name: "Centrus Energy Corp.", cik: "0001065059" },
            ],
          },
        ],
        [{ label: "fuel", descriptor: null }],
      ),
    );
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    // NOT auto-placed — it's a pick candidate (with its CIK), not a member row yet
    expect(screen.queryByLabelText("place LEU")).not.toBeInTheDocument();
    const pick = await screen.findByRole("button", { name: /LEU/ });
    expect(pick).toHaveTextContent("CIK 0001065059");

    await user.click(pick); // the explicit pick commits the exact security_id
    expect(screen.getByLabelText("place LEU")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
    expect(body.basket.find((m) => m.ticker === "LEU")).toMatchObject({ security_id: "s-leu" });
  });

  it("a VERIFY name is surfaced lower-confidence and enters the basket only by an explicit add", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    h.refetch.mockResolvedValue(
      draft([VERIFY_ALKS], [{ label: "therapeutics", descriptor: null }]),
    );
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    // NOT auto-placed (single broad keyword -> lower confidence) — shown, not yet a member row
    expect(screen.queryByLabelText("place ALKS")).not.toBeInTheDocument();
    expect(await screen.findByText("Alkermes plc")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "add ALKS" })); // the explicit confirm
    expect(screen.getByLabelText("place ALKS")).toBeInTheDocument(); // now a placed member
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
    expect(body.basket.find((m) => m.ticker === "ALKS")).toMatchObject({
      security_id: "s-alks",
      segment: "therapeutics",
    });
  });

  it("an ABSENT name is shown, never placed", async () => {
    const user = userEvent.setup();
    h.refetch.mockResolvedValue(
      draft(
        [
          {
            name: "Kairos Power",
            ticker: "KAIROS",
            prose: "not yet US-listed",
            segment: "reactors",
            status: "absent",
            security_id: null,
            candidates: [],
          },
        ],
        [],
      ),
    );
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByText("Kairos Power")).toBeInTheDocument(); // shown…
    expect(screen.queryByLabelText("place KAIROS")).not.toBeInTheDocument(); // …never placed
  });

  it("an empty draft (fail-open) leaves the editor unchanged", async () => {
    const user = userEvent.setup();
    h.refetch.mockResolvedValue(draft([], []));
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);
    expect(screen.getByText("OKLO")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(screen.getByText("OKLO")).toBeInTheDocument(); // unchanged
    expect(screen.getByText(/drafter returned nothing/)).toBeInTheDocument(); // honest fail-open note
    expect(screen.queryByText("drafted")).not.toBeInTheDocument(); // nothing loaded
  });
});
