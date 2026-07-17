import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the promote (save) mutation, the resolver typeahead, and the narrative→chain
// drafter. The draft logic (useChainDraft) is the REAL hook — exercised through the editor UI.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  putExcl: vi.fn(async () => ({})), // #7: the exclusion PUT rides every Save (mutateAsync resolves)
  start: vi.fn(),
  produce: vi.fn(),
  edit: vi.fn(),
  recommend: vi.fn(),
  produceData: undefined as any,
  jobData: undefined as any,
  jobIsError: false,
}));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
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
  // #7: the durable exclusion set — Save persists the pruning through this PUT before the promote
  usePutExclusions: () => ({
    mutateAsync: h.putExcl,
    isPending: false,
    isError: false,
    error: null,
  }),
  // the tier RECOMMENDER (#10): mutate(undefined, {onSuccess}) — the test drives onSuccess with canned recs
  useRecommendTiers: () => ({ mutate: h.recommend, isPending: false, isError: false, error: null }),
  // the run-loader picker: no saved runs here → RunPicker self-hides (its own suite covers its behavior)
  useThesisRuns: () => ({ data: [], isError: false }),
  useLoadThesisRun: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
}));

const exportSpy = vi.hoisted(() => vi.fn());
vi.mock("../../util/exportNames", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../util/exportNames")>();
  return { ...mod, exportKeptNames: exportSpy };
});

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
      conviction: null, // the API returns null for an unweighted member (unset ≠ 0)
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
  h.putExcl.mockClear();
  h.start.mockReset();
  h.produce.mockReset();
  h.edit.mockReset();
  h.recommend.mockReset();
  h.produceData = undefined;
  h.jobData = undefined;
  h.jobIsError = false;
  exportSpy.mockReset();
});

describe("ChainEditor — authoring", () => {
  it("decomposes a flat basket: add a link, then save the full draft", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    h.mutate.mockImplementation((_body: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );

    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={onDone} />);

    await user.type(screen.getByLabelText("new link label"), "reactors");
    await user.click(screen.getByRole("button", { name: "+ link" }));
    expect(screen.getByLabelText("link 1 label")).toHaveValue("reactors");
    // the seg dropdown now lists the new link (the seg control is UI-only — placement lands when the backend
    // emits segments; only "— remove —" is wired)
    expect(screen.getByLabelText("segment for OKLO")).toHaveTextContent("reactors");

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(h.mutate).toHaveBeenCalledTimes(1);
    const body = h.mutate.mock.calls[0][0] as {
      segments: unknown[];
      basket: Record<string, unknown>[];
    };
    expect(body.segments).toEqual([{ label: "reactors", descriptor: null }]);
    expect(body.basket).toHaveLength(1);
    expect(body.basket[0]).toMatchObject({ ticker: "OKLO" });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(true); // D — a saved exit tells the parent to surface the re-entry note
  });

  it("D: Done exits WITHOUT the saved signal (onDone(false)) — only a successful Save sends true", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={onDone} />);
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalledWith(false);
    expect(h.mutate).not.toHaveBeenCalled();
  });

  it("Clear: renders only when onStartOver is provided, and clicking it invokes the reset", async () => {
    const user = userEvent.setup();
    // absent by default (no session-owning parent) — the button is opt-in
    const { unmount } = render(
      <ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    unmount();

    const onStartOver = vi.fn();
    render(
      <ChainEditor
        asof="2026-06-08"
        thesis={flatThesis}
        onDone={vi.fn()}
        onStartOver={onStartOver}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onStartOver).toHaveBeenCalledTimes(1);
  });

  it("adds a name via the resolver typeahead (search → pick → classify → add), CIK shown", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.type(screen.getByLabelText("search securities"), "cc");
    const match = await screen.findByRole("button", { name: /CCJ/ });
    expect(match).toHaveTextContent("CIK 0001"); // the homonym tell is surfaced
    await user.click(match);
    // item F: NO archetype control at placement — the pick is ticker + role only (the rail decides later)
    expect(screen.queryByLabelText("archetype")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("role"), "the uranium anchor");
    await user.click(screen.getByRole("button", { name: "add to basket" }));

    expect(screen.getByLabelText("segment for CCJ")).toBeInTheDocument(); // landed in the PLACED bucket
  });

  it("re-segments a name via the wired seg dropdown (item 7: placeMember, no '— remove —')", async () => {
    const user = userEvent.setup();
    const withSegs = {
      ...flatThesis,
      segments: [
        { label: "reactors", descriptor: null },
        { label: "fuel", descriptor: null },
      ],
    };
    render(<ChainEditor asof="2026-06-08" thesis={withSegs} onDone={vi.fn()} />);
    const seg = screen.getByLabelText("segment for OKLO") as HTMLSelectElement;
    await user.selectOptions(seg, "fuel"); // selecting a link re-places the name
    expect((screen.getByLabelText("segment for OKLO") as HTMLSelectElement).value).toBe("fuel");
    expect(screen.queryByText("— remove —")).not.toBeInTheDocument(); // remove dropped from the dropdown
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
    render(<ChainEditor asof="2026-06-08" thesis={withSegs} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "move a later" }));
    expect(
      screen.getAllByLabelText(/^link \d+ label$/).map((i) => (i as HTMLInputElement).value),
    ).toEqual(["b", "a"]);

    await user.click(screen.getByRole("button", { name: "remove a" }));
    expect(screen.getByLabelText("segment for OKLO")).toHaveValue(""); // un-placed -> "— segment —"
  });
});

describe("ChainEditor — draft from narrative (S5 5c)", () => {
  it("loads a PLACED name as a drafted, accept-able placement with its prose", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(await screen.findByLabelText("segment for SMR")).toBeInTheDocument(); // landed in PLACED
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument(); // drafted -> accept-able
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue(
      "the only NRC-approved SMR designer",
    );
  });

  it("accept ⇄ un-accept is a reversible toggle (#1): accept relabels, un-accept relabels back, values kept", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");
    // set a field BEFORE accepting so we can prove un-accept keeps values (doesn't undo edits)
    await user.selectOptions(screen.getByLabelText("conviction for SMR"), "4");
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument(); // drafted

    await user.click(screen.getByRole("button", { name: "accept SMR" })); // → operator_set
    // the button does NOT disappear — it relabels to its visible inverse
    expect(screen.queryByRole("button", { name: "accept SMR" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "un-accept SMR" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "un-accept SMR" })); // → back to system_drafted
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument(); // round-tripped
    // un-accept flips authorship only — the conviction set earlier survives
    expect((screen.getByLabelText("conviction for SMR") as HTMLSelectElement).value).toBe("4");
  });

  it("editing a drafted name's prose flips it to operator_edited, and it can still be un-accepted (edits kept)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    const prose = await screen.findByLabelText("thesis-fit for SMR");
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument(); // drafted
    await user.type(prose, " — refined"); // → operator_edited

    // an edited name is owned → the toggle offers un-accept (not accept)
    expect(screen.queryByRole("button", { name: "accept SMR" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "un-accept SMR" })).toBeInTheDocument();

    // un-accepting hands it back to the drafter (re-rollable) but KEEPS the edited prose
    await user.click(screen.getByRole("button", { name: "un-accept SMR" }));
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument();
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue(
      "the only NRC-approved SMR designer — refined",
    );
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    // NOT auto-placed — it sits in the COULDN'T RESOLVE drawer behind a "pick CIK…" affordance
    expect(screen.queryByLabelText("segment for LEU")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /pick CIK for Centrus/ }));
    const pick = await screen.findByRole("button", { name: /LEU/ }); // the candidate (with its CIK) appears
    expect(pick).toHaveTextContent("CIK 0001065059");

    await user.click(pick); // the explicit pick commits the exact security_id
    expect(screen.getByLabelText("segment for LEU")).toBeInTheDocument(); // now a placed member
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    // NOT auto-placed (single broad keyword -> lower confidence) — in the TO REVIEW bucket, not yet a member
    expect(screen.queryByLabelText("segment for ALKS")).not.toBeInTheDocument();
    expect(await screen.findByText("Alkermes plc")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "add ALKS" })); // check-to-add promotes it
    expect(screen.getByLabelText("segment for ALKS")).toBeInTheDocument(); // now a placed member
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByText("Kairos Power")).toBeInTheDocument(); // shown in COULDN'T RESOLVE…
    expect(screen.queryByLabelText("segment for KAIROS")).not.toBeInTheDocument(); // …never placed
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");
    expect(screen.getByText("← psilocybin")).toBeInTheDocument(); // placed row prov (from the display-only stash)
    expect(screen.getByText(/matched ketamine/)).toBeInTheDocument(); // to-review row prov (p.matched_terms)
  });

  it("renders the off-universe pill on off_universe names (PLACED + ABSENT, orthogonal to status), never on an edgar name", async () => {
    const user = userEvent.setup();
    const PLACED_OFF = {
      name: "Korea Electric Power",
      ticker: "KEP",
      prose: "the utility building the reactors",
      segment: "reactors",
      status: "placed",
      security_id: "s-kep",
      candidates: [],
      matched_terms: [], // off-universe → no discovery term surfaced it
      discovery_source: "off_universe",
    };
    const ABSENT_OFF = {
      name: "Some Foreign GmbH",
      ticker: "ZZZZ",
      prose: "no US listing",
      segment: "reactors",
      status: "absent",
      security_id: null,
      candidates: [],
      matched_terms: [],
      discovery_source: "off_universe",
    };
    // PLACED_SMR matched an EDGAR CIK (discovery_source defaults "edgar") → it must show NO pill.
    mockDraft(draft([PLACED_SMR, PLACED_OFF, ABSENT_OFF]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for KEP"); // the off_universe name landed in PLACED (the win-signal)

    // the pill rides BOTH the PLACED (KEP) and the absent (ZZZZ) buckets — orthogonal to placement status
    // (scoped to `.pill` — "off-universe" is also a find-bar filter toggle button)
    expect(screen.getAllByText("off-universe", { selector: ".pill" })).toHaveLength(2);
    // honest label: it names the observation ("off the deterministic universe"), never the mechanism
    expect(screen.getAllByText("off-universe", { selector: ".pill" })[0]).toHaveAttribute(
      "title",
      expect.stringContaining("off the deterministic universe"),
    );
    // the edgar name (SMR) shows no pill — provenance never over-claims a sweep contribution
    const smrRow = screen.getByLabelText("segment for SMR").closest(".nmrow") as HTMLElement;
    expect(within(smrRow).queryByText("off-universe")).not.toBeInTheDocument();
  });

  it("renders machine-parsed sector / exchange chips on a placed name (Slice 2 enrichment, display-only)", async () => {
    const user = userEvent.setup();
    const PLACED_ENRICHED = {
      name: "Cameco",
      ticker: "CCJ",
      prose: "uranium miner",
      segment: "reactors",
      status: "placed",
      security_id: "s-ccj-x",
      candidates: [],
      matched_terms: ["nuclear"],
      discovery_source: "edgar",
      sector: "Metal Mining",
      exchange: "NYSE",
      listing_status: "active",
    };
    mockDraft(draft([PLACED_ENRICHED]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for CCJ");
    expect(screen.getByText("Metal Mining")).toBeInTheDocument(); // sector chip (bridged by security_id)
    expect(screen.getByText("NYSE")).toBeInTheDocument(); // exchange chip
    // an actively-listed name shows NO not-listed flag
    expect(screen.queryByText(/no current listing found in EDGAR/)).not.toBeInTheDocument();
  });

  it("a name gated for no current listing reads as a hedged 'not listed' pick, never 'delisted' (Slice 2 gate)", async () => {
    const user = userEvent.setup();
    const AMBIGUOUS_UNLISTED = {
      name: "Defunct Reactors Inc.",
      ticker: "DEAD",
      prose: "",
      segment: "reactors",
      status: "ambiguous", // the gate downgraded an inactive PLACED name to a frictionless pick
      security_id: null,
      candidates: [
        { security_id: "s-dead", ticker: "DEAD", name: "Defunct Reactors Inc.", cik: "0000000001" },
      ],
      matched_terms: [],
      discovery_source: "edgar",
      sector: "Electric Services",
      exchange: null,
      listing_status: "inactive",
    };
    mockDraft(draft([AMBIGUOUS_UNLISTED]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByText("Defunct Reactors Inc.")).toBeInTheDocument();
    // the hedged pill + note — a GUESS, never a "delisted" verdict (#9); the redomicile note is suppressed
    expect(screen.getByText("not listed")).toBeInTheDocument();
    expect(screen.getByText(/no current listing found in EDGAR/)).toBeInTheDocument();
    expect(screen.queryByText(/delisted/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/redomicile/)).not.toBeInTheDocument();
    // the frictionless rescue: a "place anyway…" action (not "pick CIK…")
    expect(screen.getByRole("button", { name: /place Defunct Reactors/ })).toBeInTheDocument();
    expect(screen.getByText("Electric Services")).toBeInTheDocument(); // sector chip still rides the row
  });

  it("an empty draft (fail-open) leaves the editor unchanged", async () => {
    const user = userEvent.setup();
    mockDraft(draft([], []));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByText(/draft was lost/i)).toBeInTheDocument();
  });

  it("a 409 (a draft already running) is shown, not retried", async () => {
    const user = userEvent.setup();
    h.start.mockRejectedValue({ detail: "a draft is already running for this thesis" });
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByText(/already running/)).toBeInTheDocument();
    expect(h.start).toHaveBeenCalledTimes(1); // no auto-retry of the expensive kick-off
  });
});

describe("ChainEditor — reversibility (Workbench interaction principles)", () => {
  it("add ⇄ send-back round-trips a To-Review name (#2); a draft-placed name has NO send-back", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    mockDraft(
      draft(
        [PLACED_SMR, VERIFY_ALKS],
        [
          { label: "reactors", descriptor: null },
          { label: "therapeutics", descriptor: null },
        ],
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // ALKS sits in To-Review; check-to-add → it leaves To-Review and lands in PLACED
    await user.click(await screen.findByRole("checkbox", { name: "add ALKS" }));
    expect(screen.getByLabelText("segment for ALKS")).toBeInTheDocument();

    // a draft-PLACED name (SMR) never came from To-Review → it gets no send-back (the control marks the exception)
    expect(
      screen.queryByRole("button", { name: "send SMR back to review" }),
    ).not.toBeInTheDocument();

    // the visible inverse of add: send ALKS back → removed from the basket, reappears in To-Review (re-addable)
    await user.click(screen.getByRole("button", { name: "send ALKS back to review" }));
    expect(screen.queryByLabelText("segment for ALKS")).not.toBeInTheDocument(); // gone from PLACED
    expect(screen.getByRole("checkbox", { name: "add ALKS" })).toBeInTheDocument(); // back in To-Review

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
    expect(body.basket.find((m) => m.ticker === "ALKS")).toBeUndefined(); // Save no longer carries it
  });

  it("a re-draft preserves operator-authored names, re-rolls drafted, orphans-to-Discovered, adds new (#3)", async () => {
    const user = userEvent.setup();
    const placed = (
      ticker: string,
      security_id: string,
      segment: string,
      prose: string,
    ) => ({ name: ticker, ticker, prose, segment, status: "placed", security_id, candidates: [], matched_terms: [] });
    const D1 = draft(
      [
        placed("SMR", "s-smr", "reactors", "P1"),
        placed("GEV", "s-gev", "turbines", "G1"),
        placed("LOTTO", "s-lotto", "lotto", "L1"),
      ],
      [
        { label: "reactors", descriptor: null },
        { label: "turbines", descriptor: null },
        { label: "lotto", descriptor: null },
      ],
    );
    // draft 2: SMR moves segment, GEV would move (but is accepted), LOTTO is gone, CCJ is new
    const D2 = draft(
      [
        placed("SMR", "s-smr", "smr-reactors", "P2"),
        placed("GEV", "s-gev", "power", "G2"),
        placed("CCJ", "s-ccj-new", "mining", "C1"),
      ],
      [
        { label: "smr-reactors", descriptor: null },
        { label: "power", descriptor: null },
        { label: "mining", descriptor: null },
      ],
    );

    mockDraft(D1);
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");

    // accept GEV → operator_set; the re-roll must NOT clobber it
    await user.click(screen.getByRole("button", { name: "accept GEV" }));

    // re-draft with the different result (swap the polled job result, then click Draft again)
    h.jobData = { job_id: "j1", status: "done", result: D2, error: null };
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for CCJ"); // the brand-new name appeared

    // operator_set is untouched — GEV keeps its accepted segment (not draft-2's "power") and stays owned
    expect((screen.getByLabelText("segment for GEV") as HTMLSelectElement).value).toBe("turbines");
    expect(screen.getByRole("button", { name: "un-accept GEV" })).toBeInTheDocument();
    // a still-placed drafted name is RE-ROLLED to the fresh segment
    expect((screen.getByLabelText("segment for SMR") as HTMLSelectElement).value).toBe(
      "smr-reactors",
    );
    // a drafted name the new draft no longer places is parked in Discovered (no stale segment)
    expect((screen.getByLabelText("segment for LOTTO") as HTMLSelectElement).value).toBe(
      "Discovered",
    );
    // the new name landed as a drafted, accept-able placement
    expect(screen.getByRole("button", { name: "accept CCJ" })).toBeInTheDocument();
    // OKLO (pre-existing operator_set, in neither draft) is untouched and still owned
    expect(screen.getByRole("button", { name: "un-accept OKLO" })).toBeInTheDocument();
  });
});

describe("ChainEditor — placed-row polish (R1/R2/R3)", () => {
  it("R1: the accept toggle right-aligns at the END of the controls row, sharing it with SEG/CONV", () => {
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />); // OKLO is operator_set → an "un-accept" toggle
    const acceptBtn = screen.getByRole("button", { name: "un-accept OKLO" });
    const segSel = screen.getByLabelText("segment for OKLO");
    // the action lives in the row-actions group…
    expect(acceptBtn.closest(".rowactions")).not.toBeNull();
    // …which sits INSIDE the same controls (.ctls) row as SEG/CONV (the second line)
    expect(acceptBtn.closest(".ctls")).not.toBeNull();
    expect(acceptBtn.closest(".ctls")).toBe(segSel.closest(".ctls"));
  });

  it("item F: the row has NO archetype editor — a SET archetype shows as a read-only chip", () => {
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />); // OKLO carries a stored "high_beta"
    // no select — the archetype is decided ONCE, on the finalize rail, never at placement
    expect(screen.queryByLabelText("archetype for OKLO")).not.toBeInTheDocument();
    // the stored value still SHOWS (read-only chip) — a re-opened finalized basket keeps its context
    expect(screen.getByText("high-beta")).toBeInTheDocument();
  });

  it("R2: the thesis-fit box auto-sizes (rows=1, not a fixed 3) and still edits", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    const ta = screen.getByLabelText("thesis-fit for OKLO") as HTMLTextAreaElement;
    expect(ta.tagName).toBe("TEXTAREA");
    expect(ta).toHaveAttribute("rows", "1"); // auto-sizing min (was a fixed rows=3)
    await user.type(ta, "one of the majors");
    expect(ta.value).toBe("one of the majors"); // edits round-trip through editProse
  });

  it("R3: excluding a name collapses its detail to a stub; re-including restores it, authorship untouched", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");
    // baseline: the drafted SMR shows its full detail
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument();

    // exclude SMR → its prose, controls, and accept collapse; the checkbox + an "excluded" stub remain (#9)
    await user.click(screen.getByLabelText("include SMR"));
    expect(screen.queryByLabelText("thesis-fit for SMR")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("segment for SMR")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "accept SMR" })).not.toBeInTheDocument();
    expect(screen.getByText("excluded", { selector: ".wb-exc-tag" })).toBeInTheDocument();
    expect(screen.getByLabelText("include SMR")).toBeInTheDocument(); // re-includable in one click

    // re-check restores everything, and authorship was NEVER touched (still drafted → "accept", not "un-accept")
    await user.click(screen.getByLabelText("include SMR"));
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument();
  });

  it("R5: the Placed and To Review sections collapse (open by default), the header + count stay", async () => {
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR"); // Placed is open by default

    // collapse Placed → its rows hide, but the header (a button, with its count) stays for re-expand
    await user.click(screen.getByRole("button", { name: /Placed/ }));
    expect(screen.queryByLabelText("segment for SMR")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Placed/ })).toBeInTheDocument();
    // re-open restores the list
    await user.click(screen.getByRole("button", { name: /Placed/ }));
    expect(screen.getByLabelText("segment for SMR")).toBeInTheDocument();

    // To Review collapses independently (its keeper hides)
    expect(screen.getByText("Alkermes plc")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /To review/ }));
    expect(screen.queryByText("Alkermes plc")).not.toBeInTheDocument();
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

describe("ChainEditor — TRIAGE include-controls (the prune)", () => {
  // a drafted SMR added alongside the operator-owned OKLO → a two-name basket to prune
  const saveBody = () => h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
  const withOnSuccess = () =>
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );

  it("every name is INCLUDED by default (#9): Save sends the whole basket", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");

    expect(screen.getByLabelText("include OKLO")).toBeChecked();
    expect(screen.getByLabelText("include SMR")).toBeChecked();
    expect(screen.getByText("· 2 of 2 included")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket).toHaveLength(2);
  });

  it("unchecking a name EXCLUDES it from Save, but leaves it visible (re-includable)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");

    await user.click(screen.getByLabelText("include SMR")); // exclude SMR
    expect(screen.getByLabelText("include SMR")).not.toBeChecked();
    expect(screen.getByText("· 1 of 2 included")).toBeInTheDocument();
    // still VISIBLE (#9 — never a silent drop): the checkbox stays + an "excluded" stub shows, but the
    // DETAIL collapses (R3) — its controls hide, the row recedes
    expect(screen.getByText("excluded", { selector: ".wb-exc-tag" })).toBeInTheDocument();
    expect(screen.queryByLabelText("segment for SMR")).not.toBeInTheDocument(); // detail hidden while excluded
    // re-check restores the full detail — nothing lost
    await user.click(screen.getByLabelText("include SMR"));
    expect(screen.getByLabelText("segment for SMR")).toBeInTheDocument();
    await user.click(screen.getByLabelText("include SMR")); // exclude again for the save assertion

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const b = saveBody().basket;
    expect(b).toHaveLength(1);
    expect(b[0]).toMatchObject({ ticker: "OKLO" });
  });

  it("'clear un-accepted' excludes drafted names, keeps operator-owned, and never touches authorship", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([PLACED_SMR])); // SMR loads system_drafted; OKLO is operator_set
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");

    await user.click(screen.getByRole("button", { name: /clear un-accepted/ }));
    expect(screen.getByLabelText("include SMR")).not.toBeChecked(); // drafted → excluded
    expect(screen.getByLabelText("include OKLO")).toBeChecked(); // operator-owned → kept
    // authorship is UNTOUCHED — R3 hides the accept affordance while excluded, so re-include to inspect: SMR is
    // still drafted (its accept, not un-accept, remains); only its include changed
    await user.click(screen.getByLabelText("include SMR")); // re-include to inspect authorship
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("include SMR")); // exclude again for the save assertion

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO"]);
  });

  it("exports only included placed names", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");

    await user.click(screen.getByLabelText("include SMR"));
    await user.click(screen.getByRole("button", { name: "export 1 included names" }));

    expect(exportSpy).toHaveBeenCalledWith({
      thesisName: "Nuclear",
      stage: "triage",
      asof: "2026-06-08",
      rows: [{ ticker: "OKLO", name: null }],
    });
  });

  it("exclude-all then Save confirms the empty-basket wipe; include-all restores", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false); // operator cancels the wipe
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "exclude all" }));
    expect(screen.getByText("· 0 of 1 included")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(h.mutate).not.toHaveBeenCalled(); // cancelled → nothing persisted

    await user.click(screen.getByRole("button", { name: "include all" }));
    expect(screen.getByText("· 1 of 1 included")).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("item 1: the fundamentals badge shows only once it DISCRIMINATES; else a clean header hint", () => {
    // ≥1 name has confirmed fundamentals → the per-row badge earns its place (it now discriminates)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const scored: any = { "s-oklo": { purity: { pips: 3, value: 80, provenance: [] } } };
    const { unmount } = render(
      <ChainEditor thesis={flatThesis} onDone={vi.fn()} scoredById={scored} />,
    );
    expect(screen.getByText("✓ fundamentals")).toBeInTheDocument();
    expect(screen.queryByText(/Surface your shortlist/)).not.toBeInTheDocument();
    unmount();

    // nothing surfaced → NO per-row badge (it'd be true of every row = noise), just the quiet header hint
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    expect(screen.queryByText("needs SURFACE")).not.toBeInTheDocument();
    expect(screen.queryByText("✓ fundamentals")).not.toBeInTheDocument();
    expect(screen.getByText(/Surface your shortlist/)).toBeInTheDocument();
  });
});

describe("ChainEditor — Workbench FE polish (items 2–6)", () => {
  it("items 2+3: a placed row shows the company name (bridged) + the SEC filer-category chip", async () => {
    const user = userEvent.setup();
    const PLACED_ENRICHED = {
      name: "Micron Technology",
      ticker: "MU",
      prose: "DRAM / HBM maker",
      segment: "memory",
      status: "placed",
      security_id: "s-mu",
      candidates: [],
      matched_terms: ["HBM"],
      sector: "Semiconductors",
      exchange: "NASDAQ",
      category: "Large accelerated filer",
    };
    mockDraft(draft([PLACED_ENRICHED], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");
    expect(screen.getByText("Micron Technology")).toBeInTheDocument(); // item 2 — name bridged onto the row
    expect(screen.getByText("Large accelerated filer")).toBeInTheDocument(); // item 3 — category chip
  });

  const VKEEP = {
    name: "Micron",
    ticker: "MU",
    prose: "HBM/DRAM",
    segment: "memory",
    status: "verify",
    security_id: "s-mu",
    candidates: [],
    matched_terms: ["HBM"],
    off_thesis: false,
  };
  const VOFF = {
    name: "Kroger",
    ticker: "KR",
    prose: "no memory tie — boilerplate",
    segment: "Discovered",
    status: "verify",
    security_id: "s-kr",
    candidates: [],
    matched_terms: ["memory"],
    off_thesis: true,
  };
  const VNOTICK = {
    name: "Some Holdco LLC",
    ticker: null,
    prose: "financing sub",
    segment: "Discovered",
    status: "verify",
    security_id: "s-hc",
    candidates: [],
    matched_terms: ["storage"],
    off_thesis: false,
  };

  it("items 4+5: To Review surfaces keepers, collapses off-thesis (Low signal) + ticker-less (No listed ticker)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP, VOFF, VNOTICK], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // the keeper is SURFACED at the top (no per-row "recommend add" badge — it'd be true of every visible keeper)
    await screen.findByText("Micron");
    expect(screen.getByRole("checkbox", { name: "add MU" })).toBeEnabled(); // check-to-add is live for a keeper
    expect(screen.queryByText("recommend add")).not.toBeInTheDocument();
    // the off-thesis majority + the ticker-less names are QUIET + collapsed (not visible until expanded, #7/#9)
    expect(screen.queryByText("Kroger")).not.toBeInTheDocument();
    expect(screen.queryByText("Some Holdco LLC")).not.toBeInTheDocument();
    expect(screen.getByText("Low signal")).toBeInTheDocument();
    expect(screen.getByText("No listed ticker")).toBeInTheDocument();
    // the To review count is KEEPERS-ONLY — the two noise buckets are nested sub-drawers with their own counts
    expect(screen.getByRole("button", { name: /To review/ })).toHaveTextContent("· 1");
    // expand Low signal → the off-thesis name appears, still promotable (never dropped)
    await user.click(screen.getByText("Low signal"));
    expect(screen.getByText("Kroger")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "add KR" })).toBeInTheDocument();
    // expand No listed ticker → the ticker-less name shows, but its add is DISABLED (not directly investable)
    await user.click(screen.getByText("No listed ticker"));
    expect(screen.getByRole("checkbox", { name: "add Some Holdco LLC" })).toBeDisabled();
    // the Discovered-segment rows never read "recommend → Discovered" (a non-recommendation), but keep `matched`
    expect(screen.queryByText(/recommend → Discovered/)).not.toBeInTheDocument();
    expect(screen.getByText(/matched memory/)).toBeInTheDocument(); // Kroger's provenance still shows
  });

  it("a keeper's ✕ sets it aside (greyed stub) and toggles back on a second click (#1/#2)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("Micron");

    // not set aside: the identity chips / prose show and the ✕ reads "set aside MU"
    const setAside = screen.getByRole("button", { name: "set aside MU" });
    expect(screen.getByRole("checkbox", { name: "add MU" })).toBeEnabled();

    // click ✕ → the row greys to a stub: the "set aside" tag appears, add is disabled, the row stays VISIBLE
    await user.click(setAside);
    expect(screen.getByText("set aside")).toBeInTheDocument();
    expect(screen.getByText("Micron")).toBeInTheDocument(); // #2 keep-it-visible: never vanishes
    expect(screen.getByRole("checkbox", { name: "add MU" })).toBeDisabled();
    // the same button is now the inverse — restore
    const restore = screen.getByRole("button", { name: "restore MU" });
    expect(restore).toHaveAttribute("aria-pressed", "true");

    // click again → restored: the stub tag is gone, add is live again (#1 reversible)
    await user.click(restore);
    expect(screen.queryByText("set aside")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "add MU" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "set aside MU" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  // two placed names so the include filter bar renders (basket.length > 1)
  const toReviewThesis = {
    ...flatThesis,
    basket: [
      flatThesis.basket[0],
      {
        ticker: "CCJ",
        role: "—",
        archetype: "leader",
        security_id: "s-ccj",
        segment: "fuel",
        authored_by: "operator_set" as const,
        conviction: null,
      },
    ],
  };

  it("included filter hides a set-aside To Review row and updates counts", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP, VOFF], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={toReviewThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("Micron");

    await user.click(screen.getByText("Low signal"));
    await user.click(screen.getByRole("button", { name: "set aside KR" }));
    expect(screen.getByText("Kroger")).toBeInTheDocument(); // stub visible under "all"

    await user.selectOptions(screen.getByLabelText("filter by include"), "included");
    expect(screen.queryByText("Kroger")).not.toBeInTheDocument();
    expect(screen.getByText("Micron")).toBeInTheDocument();
    expect(screen.getByText("showing 2 of 2 placed · 1 of 2 to review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /To review/ })).toHaveTextContent("· 1");
  });

  it("excluded filter shows only set-aside To Review rows", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP, VOFF], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={toReviewThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("Micron");

    await user.click(screen.getByText("Low signal"));
    await user.click(screen.getByRole("button", { name: "set aside KR" }));
    await user.selectOptions(screen.getByLabelText("filter by include"), "excluded");

    expect(screen.getByText("Kroger")).toBeInTheDocument();
    expect(screen.queryByText("Micron")).not.toBeInTheDocument();
    expect(screen.getByText("showing 0 of 2 placed · 1 of 2 to review")).toBeInTheDocument();
  });

  it("clear filters restores set-aside To Review stubs under all", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP, VOFF], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={toReviewThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("Micron");

    await user.click(screen.getByText("Low signal"));
    await user.click(screen.getByRole("button", { name: "set aside KR" }));
    await user.selectOptions(screen.getByLabelText("filter by include"), "included");
    expect(screen.queryByText("Kroger")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "clear filters" }));
    expect(await screen.findByText("Kroger")).toBeInTheDocument();
    expect(screen.getByText("set aside")).toBeInTheDocument();
  });

  it("THE #9 SPINE: included filter hiding a set-aside To Review row does not change Save", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    mockDraft(draft([VKEEP, VOFF], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={toReviewThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("Micron");

    await user.click(screen.getByText("Low signal"));
    await user.click(screen.getByRole("button", { name: "set aside KR" }));
    await user.selectOptions(screen.getByLabelText("filter by include"), "included");

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
    expect(body.basket.map((m) => m.ticker).sort()).toEqual(["CCJ", "OKLO"]);
  });

  it("item 6: 'Discovered' is de-linked (unsorted tag) and the nudge prompts sorting", async () => {
    const user = userEvent.setup();
    const PLACED_DISC = {
      name: "Foo Corp",
      ticker: "FOO",
      prose: "x",
      segment: "Discovered",
      status: "placed",
      security_id: "s-foo",
      candidates: [],
      matched_terms: ["x"],
    };
    mockDraft(
      draft(
        [PLACED_DISC],
        [
          { label: "memory", descriptor: null },
          { label: "Discovered", descriptor: null },
        ],
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for FOO");
    expect(screen.getByText("not a link")).toBeInTheDocument(); // the de-link tag on the pen chip
    expect(screen.getByText("Unsorted")).toBeInTheDocument(); // the pen's region label (separate from the links)
    expect(screen.getByText(/sort keepers into a link/)).toBeInTheDocument(); // the nudge (1 name in Discovered)
    // the seg dropdown offers the real link "memory" so the operator can sort FOO out of Discovered
    expect(screen.getByLabelText("segment for FOO")).toHaveTextContent("memory");
  });

  it("B: the links editor is self-describing — header, description, and auto-width (no truncation)", () => {
    const withLong = {
      ...flatThesis,
      segments: [{ label: "DRAM & HBM Maker", descriptor: null }],
      basket: [{ ...flatThesis.basket[0], segment: "DRAM & HBM Maker" }],
    };
    render(<ChainEditor asof="2026-06-08" thesis={withLong} onDone={vi.fn()} />);
    expect(screen.getByText(/Value chain/)).toBeInTheDocument(); // the section title
    expect(screen.getByText(/links your basket decomposes into/)).toBeInTheDocument(); // the description
    // the label input auto-widths to its content (size = label length) — no fixed 130px truncation
    expect(screen.getByLabelText("link 1 label")).toHaveAttribute("size", "16");
  });

  it("C: the Placed section header reads 'Placed names'", () => {
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Placed names/ })).toBeInTheDocument();
  });

  it("D: To Review nests three sub-drawers under one master (collapsing the master hides all three)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP, VOFF, VNOTICK], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    // three nested sub-drawers, mirroring the Placed section: Keepers (open) · Low signal · No listed ticker
    await screen.findByText("Keepers");
    expect(screen.getByText("Low signal")).toBeInTheDocument();
    expect(screen.getByText("No listed ticker")).toBeInTheDocument();
    // collapsing the MASTER To review hides ALL THREE (they're children now, not top-level siblings)
    await user.click(screen.getByRole("button", { name: /To review/ }));
    expect(screen.queryByText("Keepers")).not.toBeInTheDocument();
    expect(screen.queryByText("Low signal")).not.toBeInTheDocument();
    expect(screen.queryByText("No listed ticker")).not.toBeInTheDocument();
  });
});

describe("ChainEditor — the off-thesis flag (the narrator's opinion)", () => {
  const PLACED_OFFTHESIS = {
    name: "Kroger",
    ticker: "KR",
    prose: "no operational tie to the thesis — a single boilerplate mention of the theme",
    segment: "reactors",
    status: "placed",
    security_id: "s-kr",
    candidates: [],
    matched_terms: ["SMR"],
    off_thesis: true, // the narrator's opinion: a boilerplate term-collision
  };

  it("flags an off-thesis placement — it STAYS placed (#9), shows the ⚑ (no hard remove; uncheck to exclude)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_OFFTHESIS]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // NEVER dropped — the flagged name is a placed member (membership is deterministic, #2)
    const seg = await screen.findByLabelText("segment for KR");
    expect(screen.getByText(/model thinks off-thesis/)).toBeInTheDocument();
    expect(seg.closest(".nmrow")).toHaveClass("flagged"); // the amber tint lights up
    // no hard-remove button — the prune is the (reversible) include checkbox
    expect(screen.queryByRole("button", { name: "remove" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("include KR")).toBeChecked();
  });

  it("does NOT flag an on-thesis placement — fail-open, no off_thesis → no flag", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR])); // no off_thesis field on the placement
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");
    expect(screen.queryByText(/model thinks off-thesis/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "remove" })).not.toBeInTheDocument();
  });
});

describe("ChainEditor — the placed board partitions (C-B + G)", () => {
  const saveBody = () => h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
  const withOnSuccess = () =>
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
  // a thesis whose SIGNAL set carries a collision-prone acronym (HBM) next to a broad corroborator
  const hbmThesis = {
    ...flatThesis,
    term_set: [
      { term: "HBM", tier: "signal", authored_by: "operator_set", source: "seed" },
      { term: "memory", tier: "broad", authored_by: "system_drafted", source: "keyword_gen" },
    ],
  };
  const P_CLEAN = {
    name: "Micron Technology",
    ticker: "MU",
    prose: "HBM + DRAM maker",
    segment: "memory",
    status: "placed",
    security_id: "s-mu",
    candidates: [],
    matched_terms: ["HBM", "memory"], // the acronym PLUS a corroborator → a real name, never clustered
  };
  const P_FLAGGED = {
    name: "Kroger",
    ticker: "KR",
    prose: "boilerplate mention",
    segment: "memory",
    status: "placed",
    security_id: "s-kr",
    candidates: [],
    matched_terms: ["memory"], // a sole match but a BROAD term → not the acronym lens
    off_thesis: true,
  };
  const P_COLLISION = {
    name: "Hudbay Minerals",
    ticker: "HBM",
    prose: "a copper miner — the ticker collided with the term",
    segment: "memory",
    status: "placed",
    security_id: "s-hbm",
    candidates: [],
    matched_terms: ["HBM"], // the letters, none of the words
  };
  const MEM_SEG = [{ label: "memory", descriptor: null }];

  it("stays FLAT when the partition doesn't discriminate (no flags, no low-quality)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([P_CLEAN], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");
    expect(screen.queryByLabelText("toggle Placed")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("toggle Placed, flagged")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("toggle Placed, low quality")).not.toBeInTheDocument();
  });

  it("C-B: partitions flagged names into 'Placed, flagged' — independent collapse, ONE membership on Save", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([P_CLEAN, P_FLAGGED], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");

    // both groups render, open by default — nothing hidden by the split itself
    expect(screen.getByLabelText("toggle Placed")).toBeInTheDocument();
    expect(screen.getByLabelText("toggle Placed, flagged")).toBeInTheDocument();
    expect(screen.getByText("Kroger")).toBeInTheDocument();

    // the flagged group collapses INDEPENDENTLY — Kroger hides, Micron stays
    await user.click(screen.getByLabelText("toggle Placed, flagged"));
    expect(screen.queryByText("Kroger")).not.toBeInTheDocument();
    expect(screen.getByText("Micron Technology")).toBeInTheDocument();
    await user.click(screen.getByLabelText("toggle Placed, flagged")); // re-open

    // ONE membership: excluding inside the flagged group is the same include state Save reads
    await user.click(screen.getByLabelText("include KR"));
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO", "MU"]);
  });

  it("G: sole-acronym without model flag stays in Placed (not low quality)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([P_CLEAN, P_COLLISION], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");

    // no low-quality group — the LLM didn't flag Hudbay, so the tell alone doesn't demote
    expect(screen.queryByLabelText("toggle Placed, low quality")).not.toBeInTheDocument();
    expect(screen.getByText("Hudbay Minerals")).toBeInTheDocument();
    expect(screen.getByText("Micron Technology")).toBeInTheDocument();
  });

  it("G: model-flagged + junk tell clusters into 'Placed, low quality' (collapsed); exclude-all clears reversibly", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([P_CLEAN, { ...P_COLLISION, off_thesis: true }], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");

    // clustered + COLLAPSED by default: the header shows, the row doesn't (a cluster to visit, not a wall)
    expect(screen.getByLabelText("toggle Placed, low quality")).toBeInTheDocument();
    expect(screen.queryByText("Hudbay Minerals")).not.toBeInTheDocument();
    // Micron matched the acronym PLUS a corroborator → never clustered (visible in Placed)
    expect(screen.getByText("Micron Technology")).toBeInTheDocument();

    await user.click(screen.getByLabelText("toggle Placed, low quality"));
    expect(screen.getByText("Hudbay Minerals")).toBeInTheDocument();

    // exclude-all: greyed in place (visible + re-includable, #9); Save never sees it
    await user.click(screen.getByRole("button", { name: /exclude all 1/ }));
    expect(screen.getByLabelText("include HBM")).not.toBeChecked();
    expect(screen.getByText("Hudbay Minerals")).toBeInTheDocument(); // set aside ≠ vanished
    expect(screen.getByText("excluded", { selector: ".wb-exc-tag" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("include HBM")); // the visible inverse
    expect(screen.getByLabelText("include HBM")).toBeChecked();
    await user.click(screen.getByLabelText("include HBM")); // exclude again for the save assertion

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO", "MU"]);
  });

  it("item F: a drafted name carries NO archetype through Save (null — never a placement default)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([P_CLEAN], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const mu = saveBody().basket.find((b) => b.ticker === "MU");
    expect(mu?.archetype).toBeNull(); // un-decided rides the wire as null — the finalize rail decides later
  });

  it("G precedence: off-thesis + junk tell lands in low-quality group, not flagged", async () => {
    const user = userEvent.setup();
    mockDraft(draft([P_CLEAN, { ...P_COLLISION, off_thesis: true }], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");
    expect(screen.getByLabelText("toggle Placed, low quality")).toBeInTheDocument();
    expect(screen.queryByLabelText("toggle Placed, flagged")).not.toBeInTheDocument();
  });

  it("G: BlackRock Trust name pattern + model flag → low quality", async () => {
    const user = userEvent.setup();
    const P_FUND = {
      name: "BlackRock Multi-Asset Income Trust",
      ticker: "BME",
      prose: "a fund, not a memory name",
      segment: "memory",
      status: "placed",
      security_id: "s-bme",
      candidates: [],
      matched_terms: ["memory"],
      off_thesis: true,
    };
    mockDraft(draft([P_CLEAN, P_FUND], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for MU");
    expect(screen.getByLabelText("toggle Placed, low quality")).toBeInTheDocument();
    expect(screen.queryByLabelText("toggle Placed, flagged")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("toggle Placed, low quality"));
    expect(screen.getByText("BlackRock Multi-Asset Income Trust")).toBeInTheDocument();
  });
});

describe("ChainEditor — TRIAGE conviction/size", () => {
  const saveBody = () => h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };

  it("sets a per-name conviction (1–5); Save carries the number", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    const conv = screen.getByLabelText("conviction for OKLO") as HTMLSelectElement;
    expect(conv.value).toBe(""); // unset by default
    await user.selectOptions(conv, "4");
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket[0]).toMatchObject({ ticker: "OKLO", conviction: 4 });
  });

  it("unset stays NULL (never 0) — an unweighted name reads '—' and saves null", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    // leave conviction untouched → the option shows the "—" placeholder, and Save carries null (not 0)
    expect((screen.getByLabelText("conviction for OKLO") as HTMLSelectElement).value).toBe("");
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket[0].conviction).toBeNull();
  });

  it("setting conviction is ORTHOGONAL to authorship — a drafted name keeps its accept", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for SMR");
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument(); // drafted

    await user.selectOptions(screen.getByLabelText("conviction for SMR"), "5");
    // weighting a drafted name does NOT consume its accept (unlike editing archetype/prose) — still "accept"
    expect(screen.getByRole("button", { name: "accept SMR" })).toBeInTheDocument();
  });
});

describe("ChainEditor — TRIAGE sort/filter (the find)", () => {
  // a 3-name basket spanning archetypes / segments / authorship — enough to sort + filter (the bar shows for >1)
  const triageThesis = {
    ...flatThesis,
    segments: [
      { label: "reactors", descriptor: null },
      { label: "fuel", descriptor: null },
    ],
    basket: [
      { ticker: "OKLO", role: "—", archetype: "high_beta", security_id: "s-oklo", segment: "reactors", authored_by: "operator_set" },
      { ticker: "CCJ", role: "—", archetype: "leader", security_id: "s-ccj", segment: "fuel", authored_by: "system_drafted" },
      { ticker: "BWXT", role: "—", archetype: "shovel", security_id: "s-bwxt", segment: "reactors", authored_by: "operator_set" },
    ],
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
  const tickerOrder = (c: HTMLElement) =>
    Array.from(c.querySelectorAll(".wb-results .nmrow .tk")).map((e) => e.textContent);

  it("sorts the placed list by name (a view-only reorder)", async () => {
    const user = userEvent.setup();
    const { container } = render(<ChainEditor asof="2026-06-08" thesis={triageThesis} onDone={vi.fn()} />);
    expect(tickerOrder(container)).toEqual(["OKLO", "CCJ", "BWXT"]); // draft order
    await user.selectOptions(screen.getByLabelText("sort placed names"), "name");
    expect(tickerOrder(container)).toEqual(["BWXT", "CCJ", "OKLO"]); // A→Z
  });

  it("filters the view by archetype and reports the shown count", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={triageThesis} onDone={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("filter by archetype"), "leader");
    expect(screen.getByLabelText("segment for CCJ")).toBeInTheDocument();
    expect(screen.queryByLabelText("segment for OKLO")).not.toBeInTheDocument(); // hidden
    expect(screen.getByText("showing 1 of 3 placed")).toBeInTheDocument();
  });

  it("THE #9 SPINE: the VIEW never changes what Save persists — a filtered-out, included name still saves", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    render(<ChainEditor asof="2026-06-08" thesis={triageThesis} onDone={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("filter by archetype"), "leader"); // hides OKLO + BWXT
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
    // all three persist — the filter hides, only exclude drops (basket − excluded, over the whole draft)
    expect(body.basket.map((m) => m.ticker).sort()).toEqual(["BWXT", "CCJ", "OKLO"]);
  });

  it("clear filters restores the full view (#9 — a hidden name is one click from visible)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={triageThesis} onDone={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("filter by archetype"), "leader");
    expect(screen.queryByLabelText("segment for OKLO")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "clear filters" }));
    expect(screen.getByLabelText("segment for OKLO")).toBeInTheDocument();
    expect(screen.getByText("showing 3 of 3 placed")).toBeInTheDocument();
  });

  it("compact collapses the thesis-fit prose editors (they return when toggled off)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={triageThesis} onDone={vi.fn()} />);
    expect(screen.getByLabelText("thesis-fit for OKLO")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "compact" }));
    expect(screen.queryByLabelText("thesis-fit for OKLO")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "compact" }));
    expect(screen.getByLabelText("thesis-fit for OKLO")).toBeInTheDocument();
  });
});

describe("ChainEditor — term set produce + edit", () => {
  it("the Produce button POSTs /terms (the LLM writer seam the operator triggers)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Produce term set/ }));
    expect(h.produce).toHaveBeenCalledTimes(1);
  });

  it("the term-set drawer is open by default and collapses on click (counts stay in the header)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Regenerate term set/ })).toBeInTheDocument(); // open by default
    expect(screen.getByText("1 signal · 1 broad")).toBeInTheDocument(); // psilocybin signal + ketamine broad
    await user.click(screen.getByRole("button", { name: /Term set/ })); // collapse
    expect(screen.queryByRole("button", { name: /Regenerate term set/ })).not.toBeInTheDocument();
    expect(screen.queryByText("psilocybin")).not.toBeInTheDocument(); // body hidden
    expect(screen.getByText("1 signal · 1 broad")).toBeInTheDocument(); // …but the header counts remain
  });

  it("displays the stored SIGNAL/BROAD split with provenance + per-term edit controls", () => {
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
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
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />); // empty set
    await user.type(screen.getByPlaceholderText(/add a seed/i), "ibogaine");
    await user.click(screen.getByRole("button", { name: /Add seed/ }));
    expect(h.edit).toHaveBeenCalledTimes(1);
    expect(h.edit.mock.calls[0][0]).toEqual([{ term: "ibogaine", tier: "signal" }]);
  });

  it("remove drops the term from the PUT body (curate junk)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    // remove ketamine (the BROAD) — one of two terms, so no clear-confirm fires
    const ketamineRow = screen.getByText("ketamine").closest("li") as HTMLElement;
    await user.click(within(ketamineRow).getByRole("button", { name: "×" }));
    expect(h.edit.mock.calls[0][0]).toEqual([{ term: "psilocybin", tier: "signal" }]);
  });

  it("demote/promote toggles the tier in the PUT body (re-tier → operator_edited server-side)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
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
    render(<ChainEditor asof="2026-06-08" thesis={oneTerm} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "×" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(h.edit).not.toHaveBeenCalled(); // cancelled → no save, the set is preserved
    confirmSpy.mockRestore();
  });
});

describe("ChainEditor — tier recommendations (INVARIANT #10)", () => {
  it("the Recommend button is absent on an empty set and fires once when present", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />); // empty set
    expect(screen.queryByRole("button", { name: /Recommend tiers/ })).not.toBeInTheDocument();
    unmount();
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Recommend tiers/ }));
    expect(h.recommend).toHaveBeenCalledTimes(1);
  });

  it("shows LOUD DEFENSE + OFFENSE recommendations with their reasons", async () => {
    const user = userEvent.setup();
    h.recommend.mockImplementation((_u: unknown, opts?: { onSuccess?: (rs: unknown) => void }) =>
      opts?.onSuccess?.([
        { term: "psilocybin", recommended_tier: "broad", reason: "marketed comparator, not unique" }, // DEFENSE
        { term: "ketamine", recommended_tier: "signal", reason: "discriminating dissociative" }, // OFFENSE
      ]),
    );
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Recommend tiers/ }));
    // DEFENSE on the operator's SIGNAL seed; OFFENSE on the system_drafted BROAD term — both loud, with reasons
    expect(screen.getByText(/↓ recommend BROAD — marketed comparator/)).toBeInTheDocument();
    expect(screen.getByText(/↑ recommend SIGNAL — discriminating dissociative/)).toBeInTheDocument();
  });

  it("shows a QUIET ✓ marker for an agreement (engine fired + concurred), reason on hover", async () => {
    const user = userEvent.setup();
    h.recommend.mockImplementation((_u: unknown, opts?: { onSuccess?: (rs: unknown) => void }) =>
      opts?.onSuccess?.([
        { term: "psilocybin", recommended_tier: "signal", reason: "a specific compound" }, // agrees with the seed
      ]),
    );
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Recommend tiers/ }));
    const marker = screen.getByText("✓ signal");
    expect(marker).toBeInTheDocument(); // present, not hidden in v1
    expect(marker).toHaveAttribute("title", "a specific compound"); // reason quiet (on hover)
  });

  it("adopting an OFFENSE rec via the existing toggle fires editTerms AND keeps a '✦ adopted' trace", async () => {
    const user = userEvent.setup();
    h.recommend.mockImplementation((_u: unknown, opts?: { onSuccess?: (rs: unknown) => void }) =>
      opts?.onSuccess?.([
        { term: "ketamine", recommended_tier: "signal", reason: "discriminating dissociative" },
      ]),
    );
    // the confirm IS the existing toggle: editTerms.mutate(onSuccess: adopt) — simulate the server flipping it
    h.edit.mockImplementation((terms: unknown, opts?: { onSuccess?: (t: unknown) => void }) =>
      opts?.onSuccess?.({
        ...thesisWithTerms,
        term_set: [
          { term: "psilocybin", tier: "signal", authored_by: "operator_set", source: "seed" },
          { term: "ketamine", tier: "signal", authored_by: "operator_edited", source: "keyword_gen" },
        ],
      }),
    );
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Recommend tiers/ }));
    expect(screen.getByText(/↑ recommend SIGNAL/)).toBeInTheDocument(); // loud OFFENSE before adoption
    await user.click(screen.getByRole("button", { name: /↑ signal/ })); // confirm via the EXISTING toggle
    expect(h.edit).toHaveBeenCalledTimes(1); // the operator's click is the only writer (operator_edited)
    // ketamine flipped SIGNAL (now agrees) but keeps the adopted trace; the disagreement resolved
    expect(await screen.findByText("✦ adopted")).toBeInTheDocument();
    expect(screen.queryByText(/recommend SIGNAL/)).not.toBeInTheDocument();
  });
});

// --- the honest-discovery slice: the draft status strip + the ⚠ capped chip marker ---

// The run's honesty report (ChainDraftOut.report). Healthy defaults; a test overrides one dimension.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const healthyReport = (over: Record<string, unknown> = {}): any => ({
  coverage: { pages_ok: 40, pages_attempted: 40, failed_terms: [] as string[] },
  capped_terms: [] as string[],
  tail_sweep: "ran",
  narration_needed: 5,
  narration_filled: 5,
  ...over,
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const draftWithReport = (placements: unknown[], report: unknown, segments?: unknown[]): any => ({
  ...(segments === undefined ? draft(placements) : draft(placements, segments)),
  report,
});

describe("ChainEditor — the draft status strip (the run's honesty report)", () => {
  it("a healthy report renders ONE quiet line — counts, coverage, sweep, narration — and NO loud block", async () => {
    const user = userEvent.setup();
    mockDraft(draftWithReport([PLACED_SMR], healthyReport()));
    const { container } = render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    const strip = await screen.findByText(/Draft complete —/);
    expect(strip).toHaveTextContent("1 placed");
    expect(strip).toHaveTextContent("coverage 40/40");
    expect(strip).toHaveTextContent("sweep ran");
    expect(strip).toHaveTextContent("narration 5/5");
    expect(container.querySelector(".wb-draft-strip.loud")).toBeNull(); // quiet at 100% healthy
    expect(screen.queryByText(/completed with gaps/)).not.toBeInTheDocument();
  });

  it("missing EFTS pages render LOUD and NAME the failed terms (#9 rule 2 — the gap is on screen)", async () => {
    const user = userEvent.setup();
    mockDraft(
      draftWithReport(
        [PLACED_SMR],
        healthyReport({
          coverage: { pages_ok: 37, pages_attempted: 40, failed_terms: ["esketamine", "ibogaine"] },
        }),
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await screen.findByText(/completed with gaps/);
    expect(screen.getByText(/EFTS coverage 37\/40/)).toBeInTheDocument();
    expect(screen.getByText(/esketamine, ibogaine/)).toBeInTheDocument(); // the terms are NAMED
  });

  it("a FAILED tail-sweep is loud (a lost foreign/ADR tail, no longer indistinguishable from none)", async () => {
    const user = userEvent.setup();
    mockDraft(draftWithReport([PLACED_SMR], healthyReport({ tail_sweep: "failed" })));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await screen.findByText(/completed with gaps/);
    expect(screen.getByText(/Tail-sweep failed/)).toBeInTheDocument();
  });

  it("a SKIPPED sweep stays QUIET with the no-key label (the operator's own config, never alarmed)", async () => {
    const user = userEvent.setup();
    mockDraft(draftWithReport([PLACED_SMR], healthyReport({ tail_sweep: "skipped" })));
    const { container } = render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    const strip = await screen.findByText(/Draft complete —/);
    expect(strip).toHaveTextContent("sweep skipped (no key)");
    expect(container.querySelector(".wb-draft-strip.loud")).toBeNull();
  });

  it("a narration shortfall is loud with the M-of-N count", async () => {
    const user = userEvent.setup();
    mockDraft(draftWithReport([PLACED_SMR], healthyReport({ narration_filled: 3 })));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await screen.findByText(/completed with gaps/);
    expect(screen.getByText(/Narration 3 of 5/)).toBeInTheDocument();
  });

  it("no report -> no strip (a pre-slice result renders exactly as before)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR])); // no report field
    const { container } = render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await screen.findByLabelText("segment for SMR"); // the draft itself loaded
    expect(screen.queryByText(/Draft complete —/)).not.toBeInTheDocument();
    expect(container.querySelector(".wb-draft-strip")).toBeNull();
  });

  it("done-but-EMPTY shows BOTH the returned-nothing note AND the strip — the ambiguity resolved", async () => {
    const user = userEvent.setup();
    mockDraft(
      draftWithReport([], healthyReport({ narration_needed: 0, narration_filled: 0 }), []),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    const strip = await screen.findByText(/Draft complete —/);
    expect(strip).toHaveTextContent("0 placed");
    expect(strip).toHaveTextContent("coverage 40/40"); // enumeration was FINE — the theme is just empty
    expect(screen.getByText(/The drafter returned nothing/)).toBeInTheDocument();
  });

  it("the ⚠ capped marker lands on the MATCHING term chip only, and the strip names the term", async () => {
    const user = userEvent.setup();
    mockDraft(draftWithReport([PLACED_SMR], healthyReport({ capped_terms: ["psilocybin"] })));
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await screen.findByText(/completed with gaps/); // a capped term IS a gap -> loud
    expect(screen.getByText(/Hit-capped: psilocybin/)).toBeInTheDocument();
    const capped = screen.getAllByText("⚠ capped");
    expect(capped).toHaveLength(1); // psilocybin's chip only — ketamine carries no marker
    expect(capped[0].closest("li")).toHaveTextContent("psilocybin");
  });
});

describe("ChainEditor — #7 excluded-name permanence (the durable NO)", () => {
  const member = (ticker: string, sid: string) => ({
    ticker,
    role: "r",
    archetype: null,
    security_id: sid,
    segment: null,
    thesis_fit: null,
    conviction: null,
    authored_by: "operator_set",
  });
  const saveBody = () => h.mutate.mock.calls[0][0] as { basket: { ticker: string }[] };

  it("seeds from the persisted set: a rejected name arrives pre-greyed with its reason — and NOT dirty", () => {
    const t = {
      ...flatThesis,
      basket: [member("SMR", "s-smr")],
      exclusions: [{ security_id: "s-smr", ticker: "SMR", reason: "junk acronym" }],
    };
    render(<ChainEditor asof="2026-06-08" thesis={t as never} onDone={vi.fn()} />);
    expect(screen.getByText("excluded", { selector: ".wb-exc-tag" })).toBeInTheDocument();
    expect(screen.getByLabelText("include SMR")).not.toBeChecked(); // pre-greyed, one click back (#9)
    expect(screen.getByLabelText("why excluded SMR")).toHaveValue("junk acronym");
    expect(screen.queryByText("unsaved")).toBeNull(); // a clean load is NOT a dirty edit
  });

  it("Save PUTs the pruning: session NO + reason, carry-forward of the unseen NO, withdrawn NO dropped", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    const t = {
      ...flatThesis,
      basket: [member("SMR", "s-smr"), member("LEU", "s-leu")],
      exclusions: [
        { security_id: "s-leu", ticker: "LEU", reason: "old no" }, // re-included below → WITHDRAWN
        { security_id: "s-gone", ticker: "GONE", reason: "never resurfaced" }, // carried forward
      ],
    };
    render(<ChainEditor asof="2026-06-08" thesis={t as never} onDone={vi.fn()} />);

    await user.click(screen.getByLabelText("include LEU")); // withdraw the old NO (re-include)
    await user.click(screen.getByLabelText("include SMR")); // a fresh NO...
    await user.type(screen.getByLabelText("why excluded SMR"), "off-thesis"); // ...with its why

    await user.click(screen.getByRole("button", { name: "Save chain" }));

    expect(h.putExcl).toHaveBeenCalledTimes(1);
    const list = h.putExcl.mock.calls[0][0] as {
      security_id: string;
      ticker: string | null;
      reason: string | null;
    }[];
    expect(list).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ security_id: "s-smr", reason: "off-thesis" }),
        expect.objectContaining({ security_id: "s-gone", reason: "never resurfaced" }),
      ]),
    );
    expect(list.find((e) => e.security_id === "s-leu")).toBeUndefined(); // the withdrawn NO is gone
    // and the promote still receives ONLY the included subset (LEU back in, SMR pruned)
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["LEU"]);
  });
});
