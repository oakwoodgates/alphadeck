import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the promote (save) mutation, the resolver typeahead, and the narrative→chain
// drafter. The draft logic (useChainDraft) is the REAL hook — exercised through the editor UI.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  start: vi.fn(),
  produce: vi.fn(),
  edit: vi.fn(),
  produceData: undefined as any,
  jobData: undefined as any,
  jobIsError: false,
}));

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
  // the drafter is a KICK-OFF + POLL job now: start returns a job_id; the status query returns h.jobData. A
  // test sets both via mockDraft() (done) or directly (failed / lost).
  useStartDraft: () => ({ mutateAsync: h.start, isPending: false }),
  useDraftJobStatus: () => ({ data: h.jobData, isError: h.jobIsError }),
  // the term-set producer: the test sets h.produceData to simulate a produced split; mutate records the POST
  useProduceTerms: () => ({
    mutate: h.produce,
    data: h.produceData,
    isPending: false,
    isError: false,
    error: null,
  }),
  // the manual term-set save (no LLM): mutate records the PUT body (the full edited set)
  useEditTerms: () => ({ mutate: h.edit, isPending: false, isError: false, error: null }),
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
  term_set: [] as { term: string; tier: string; authored_by: string; source: string | null }[],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

// A drafted chain the job would return (the ChainDraftOut result) — one PLACED name in one segment, unless
// overridden.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const draft = (placements: unknown[], segments: unknown[] = [{ label: "reactors", descriptor: null }]) =>
  ({ thesis_id: "t1", segments, placements }) as any;

// Wire the kick-off + poll so a draft completes: start resolves a job_id, the status query reports done + result.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mockDraft(result: any) {
  h.start.mockResolvedValue({ job_id: "j1", status: "running" });
  h.jobData = { job_id: "j1", status: "done", result, error: null };
}

const PLACED_SMR = {
  name: "NuScale Power",
  ticker: "SMR",
  prose: "the only NRC-approved SMR designer",
  segment: "reactors",
  status: "placed",
  security_id: "s-smr",
  candidates: [],
  matched_terms: ["psilocybin"],
};

const VERIFY_ALKS = {
  name: "Alkermes plc",
  ticker: "ALKS",
  prose: "ketamine-adjacent CNS pipeline",
  segment: "therapeutics",
  status: "verify",
  security_id: "s-alks",
  candidates: [],
  matched_terms: ["ketamine"],
};

beforeEach(() => {
  h.mutate.mockReset();
  h.start.mockReset();
  h.produce.mockReset();
  h.edit.mockReset();
  h.produceData = undefined;
  h.jobData = undefined;
  h.jobIsError = false;
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
    mockDraft(draft([PLACED_SMR]));
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
    mockDraft(draft([PLACED_SMR]));
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
    mockDraft(draft([PLACED_SMR]));
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
    mockDraft(
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
    mockDraft(
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
    mockDraft(
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

  it("surfaces the matched discovery term(s) on a placed row AND a verify row (provenance, #9)", async () => {
    const user = userEvent.setup();
    mockDraft(
      draft(
        [PLACED_SMR, VERIFY_ALKS],
        [
          { label: "reactors", descriptor: null },
          { label: "therapeutics", descriptor: null },
        ],
      ),
    );
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("place SMR");
    expect(screen.getByText("← psilocybin")).toBeInTheDocument(); // placed row (from the display-only stash)
    expect(screen.getByText("← ketamine")).toBeInTheDocument(); // verify row (from p.matched_terms)
  });

  it("an empty draft (fail-open) leaves the editor unchanged", async () => {
    const user = userEvent.setup();
    mockDraft(draft([], []));
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);
    expect(screen.getByText("OKLO")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(await screen.findByText(/drafter returned nothing/)).toBeInTheDocument(); // honest note (done-empty)
    expect(screen.getByText("OKLO")).toBeInTheDocument(); // unchanged
    expect(screen.queryByText("drafted")).not.toBeInTheDocument(); // nothing loaded
  });

  it("a FAILED job shows the operator-facing error (discovery not ready), loads no draft", async () => {
    const user = userEvent.setup();
    h.start.mockResolvedValue({ job_id: "j1", status: "running" });
    h.jobData = { job_id: "j1", status: "failed", result: null, error: "term set is empty" };
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    const toast = await screen.findByText(/Couldn't draft/); // the error toast (unique prefix)
    expect(toast).toHaveTextContent("term set is empty"); // visible failure (#9), no spinner
    expect(screen.queryByText("drafted")).not.toBeInTheDocument();
  });

  it("a LOST job (404 / server restart) shows a visible failure, never an infinite spinner", async () => {
    const user = userEvent.setup();
    h.start.mockResolvedValue({ job_id: "j1", status: "running" });
    h.jobData = undefined;
    h.jobIsError = true; // the poll 404s
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByText(/draft was lost/i)).toBeInTheDocument();
  });

  it("a 409 (a draft already running) is shown, not retried", async () => {
    const user = userEvent.setup();
    h.start.mockRejectedValue({ detail: "a draft is already running for this thesis" });
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByText(/already running/)).toBeInTheDocument();
    expect(h.start).toHaveBeenCalledTimes(1); // no auto-retry of the expensive kick-off
  });
});

// A thesis carrying a stored term set (the editor seeds its working set from the prop on load).
const thesisWithTerms = {
  ...flatThesis,
  term_set: [
    { term: "psilocybin", tier: "signal", authored_by: "operator_set", source: "seed" },
    { term: "ketamine", tier: "broad", authored_by: "system_drafted", source: "keyword_gen" },
  ],
};

describe("ChainEditor — term set produce + edit", () => {
  it("the Produce button POSTs /terms (the LLM writer seam the operator triggers)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Produce term set/ }));
    expect(h.produce).toHaveBeenCalledTimes(1);
  });

  it("displays the stored SIGNAL/BROAD split with provenance + per-term edit controls", () => {
    render(<ChainEditor thesis={thesisWithTerms} onDone={vi.fn()} />);
    expect(screen.getByText("psilocybin")).toBeInTheDocument(); // SIGNAL (a seed)
    expect(screen.getByText("ketamine")).toBeInTheDocument(); // BROAD (proposed)
    expect(screen.getByText("seed")).toBeInTheDocument(); // operator provenance, surfaced
    expect(screen.getByRole("button", { name: /Regenerate term set/ })).toBeInTheDocument();
    // the edit surface is live now: a demote on the SIGNAL, a promote on the BROAD, a remove on each
    expect(screen.getByRole("button", { name: /↓ broad/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /↑ signal/ })).toBeInTheDocument();
  });

  it("add-seed PUTs the new compound as SIGNAL (the new-thesis entry path)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor thesis={flatThesis} onDone={vi.fn()} />); // empty set
    await user.type(screen.getByPlaceholderText(/add a seed/i), "ibogaine");
    await user.click(screen.getByRole("button", { name: /Add seed/ }));
    expect(h.edit).toHaveBeenCalledTimes(1);
    expect(h.edit.mock.calls[0][0]).toEqual([{ term: "ibogaine", tier: "signal" }]);
  });

  it("remove drops the term from the PUT body (curate junk)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor thesis={thesisWithTerms} onDone={vi.fn()} />);
    // remove ketamine (the BROAD) — one of two terms, so no clear-confirm fires
    const ketamineRow = screen.getByText("ketamine").closest("li") as HTMLElement;
    await user.click(within(ketamineRow).getByRole("button", { name: "×" }));
    expect(h.edit.mock.calls[0][0]).toEqual([{ term: "psilocybin", tier: "signal" }]);
  });

  it("demote/promote toggles the tier in the PUT body (re-tier → operator_edited server-side)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /↑ signal/ })); // promote ketamine
    expect(h.edit.mock.calls[0][0]).toEqual([
      { term: "psilocybin", tier: "signal" },
      { term: "ketamine", tier: "signal" }, // flipped broad -> signal
    ]);
  });

  it("removing the LAST term confirms before clearing (deliberate empty → draft 503s)", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false); // operator cancels
    const oneTerm = {
      ...flatThesis,
      term_set: [{ term: "psilocybin", tier: "signal", authored_by: "operator_set", source: "seed" }],
    };
    render(<ChainEditor thesis={oneTerm} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "×" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(h.edit).not.toHaveBeenCalled(); // cancelled → no save, the set is preserved
    confirmSpy.mockRestore();
  });
});
