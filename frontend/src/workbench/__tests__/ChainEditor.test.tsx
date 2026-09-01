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
  // the surface-ETF sleeve input (below AddName) — inert here; its own suite covers the flow
  useResolveEtf: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
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
// the REAL session codec — the seeds-only badge's old-blob cases enter through the same seam the app restores through
import { clearedRestore, deserialize, SCHEMA_VERSION, serialize } from "../triageSession";

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

      security_id: "s-oklo",
      segment: null,
      conviction: null, // the API returns null for an unweighted member (unset ≠ 0)
      // the post-S1 member shape: honest authorship (a description is a model draft until edited)
      // + the sign-off marker (false = included-but-not-endorsed, the ladder's middle rung)
      authored_by: "system_drafted",
      signed_off: false,
    },
  ],
  evidence: [],
  catalysts: [],
  kill_criteria: [],
  position: null,
  term_set: [] as { term: string; tier: string; authored_by: string; source: string | null }[],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

// S1 row helpers: a placed NAME's row is located by its include checkbox (the one control every row —
// included or excluded — keeps); the name's recommended link(s) are READ-ONLY .recchip spans inside it.
const rowOf = (ticker: string): HTMLElement =>
  screen.getByLabelText(`include ${ticker}`).closest(".nmrow") as HTMLElement;
const chipsOf = (ticker: string): string[] =>
  Array.from(rowOf(ticker).querySelectorAll(".recchip")).map((e) => e.textContent ?? "");

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
    // adding a LINK places no name (S1: per-name segment sorting lives on triage) — the unplaced
    // OKLO row shows NO recommended-link chips (the honest abstain)
    expect(chipsOf("OKLO")).toEqual([]);

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

  it("adds a name via the resolver typeahead — AUTO-signed-off, its description a model draft (locked decision 1)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.type(screen.getByLabelText("search securities"), "cc");
    const match = await screen.findByRole("button", { name: /CCJ/ });
    expect(match).toHaveTextContent("CIK 0001"); // the homonym tell is surfaced
    await user.click(match);
    await user.type(screen.getByLabelText("role"), "the uranium anchor");
    await user.click(screen.getByRole("button", { name: "add to basket" }));

    expect(screen.getByLabelText("include CCJ")).toBeInTheDocument(); // landed in the PLACED bucket
    // a hand-add IS the endorsement: it enters at the ladder's top rung (auto sign-off)…
    expect(screen.getByRole("button", { name: "withdraw sign-off CCJ" })).toBeInTheDocument();
    // …but its description is honestly a MODEL DRAFT until typed — no false "your words" (nothing
    // typed yet → no label at all on the empty description)
    expect(within(rowOf("CCJ")).queryByText("your words")).not.toBeInTheDocument();
  });

  it("S1: the row carries READ-ONLY link chips — no seg dropdown, no conviction control", () => {
    const withSegs = {
      ...flatThesis,
      segments: [
        { label: "reactors", descriptor: null },
        { label: "fuel", descriptor: null },
      ],
      basket: [{ ...flatThesis.basket[0], segment: "reactors" }],
    };
    render(<ChainEditor asof="2026-06-08" thesis={withSegs} onDone={vi.fn()} />);
    // the drafted link renders as a read-only chip (an LLM recommendation, not an editor)
    expect(chipsOf("OKLO")).toEqual(["reactors"]);
    // the segment dropdown and the conviction select are GONE from this surface (they move to triage)
    expect(screen.queryByLabelText("segment for OKLO")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("conviction for OKLO")).not.toBeInTheDocument();
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
    expect(chipsOf("OKLO")).toEqual([]); // un-placed — the name stays, its chip goes
    expect(screen.getByLabelText("include OKLO")).toBeInTheDocument(); // never dropped (#9)
  });
});

describe("ChainEditor — draft from narrative (S5 5c)", () => {
  it("loads a PLACED name honestly: model-draft description, sign-off offered, prose editable", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(await screen.findByLabelText("include SMR")).toBeInTheDocument(); // landed in PLACED
    // the confidence ladder: included (system-recommended), NOT yet endorsed → the sign-off offer
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue(
      "the only NRC-approved SMR designer",
    );
    // HONEST authorship: the drafted description is labeled the model's, never "your words"
    expect(within(rowOf("SMR")).getByText("model draft")).toBeInTheDocument();
    expect(within(rowOf("SMR")).queryByText("your words")).not.toBeInTheDocument();
    // and the drafted link rides as a read-only chip
    expect(chipsOf("SMR")).toEqual(["reactors"]);
  });

  it("sign off ⇄ withdraw is a reversible toggle (#1) that NEVER touches authorship — the label stays honest", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    await user.click(screen.getByRole("button", { name: "sign off SMR" })); // endorse the NAME
    // the button does NOT disappear — it relabels to its visible inverse
    expect(screen.queryByRole("button", { name: "sign off SMR" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "withdraw sign-off SMR" })).toBeInTheDocument();
    // THE HONEST-AUTHORSHIP CORE: sign-off endorsed the NAME — the words are still the model's
    expect(within(rowOf("SMR")).getByText("model draft")).toBeInTheDocument();
    expect(within(rowOf("SMR")).queryByText("your words")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "withdraw sign-off SMR" })); // the inverse
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument(); // round-tripped
    // nothing else moved: the prose and the chip survived both toggles
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue(
      "the only NRC-approved SMR designer",
    );
    expect(chipsOf("SMR")).toEqual(["reactors"]);
  });

  it("editing the description is the ONE act that makes it \"your words\" — and it does NOT auto-sign-off", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    const prose = await screen.findByLabelText("thesis-fit for SMR");
    expect(within(rowOf("SMR")).getByText("model draft")).toBeInTheDocument(); // drafted
    await user.type(prose, " — refined"); // → operator_edited

    // the label flips to the truth: the operator changed the text
    expect(within(rowOf("SMR")).getByText("your words")).toBeInTheDocument();
    expect(within(rowOf("SMR")).queryByText("model draft")).not.toBeInTheDocument();
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue(
      "the only NRC-approved SMR designer — refined",
    );
    // writing the note did NOT endorse the name — sign-off stays a separate act (the ladder)
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();
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
    expect(screen.queryByLabelText("include LEU")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /pick CIK for Centrus/ }));
    const pick = await screen.findByRole("button", { name: /LEU/ }); // the candidate (with its CIK) appears
    expect(pick).toHaveTextContent("CIK 0001065059");

    await user.click(pick); // the explicit pick commits the exact security_id
    expect(screen.getByLabelText("include LEU")).toBeInTheDocument(); // now a placed member
    // the pick resolved IDENTITY, not endorsement — the name enters NOT signed off (the ladder)
    expect(screen.getByRole("button", { name: "sign off LEU" })).toBeInTheDocument();
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
    expect(screen.queryByLabelText("include ALKS")).not.toBeInTheDocument();
    expect(await screen.findByText("Alkermes plc")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "add ALKS" })); // check-to-add promotes it
    expect(screen.getByLabelText("include ALKS")).toBeInTheDocument(); // now a placed member
    // add = INCLUDED (the middle rung); the endorsement stays the operator's separate act
    expect(screen.getByRole("button", { name: "sign off ALKS" })).toBeInTheDocument();
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
    expect(screen.queryByLabelText("include KAIROS")).not.toBeInTheDocument(); // …never placed
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
    await screen.findByLabelText("include SMR");
    // S2: a just-added member's frozen seed terms (captured at entry) EQUAL its current matches, so the
    // frozen ⚓ line renders ALONE — no duplicated "also matches now" (the degenerate no-diff case)
    expect(screen.getByText("⚓ seeded by: psilocybin")).toBeInTheDocument(); // placed row prov
    expect(screen.queryByText(/also matches now/)).not.toBeInTheDocument();
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
    await screen.findByLabelText("include KEP"); // the off_universe name landed in PLACED (the win-signal)

    // the pill rides BOTH the PLACED (KEP) and the absent (ZZZZ) buckets — orthogonal to placement status
    // (scoped to `.pill` — "off-universe" is also a find-bar filter toggle button)
    expect(screen.getAllByText("off-universe", { selector: ".pill" })).toHaveLength(2);
    // honest label: it names the observation ("off the deterministic universe"), never the mechanism
    expect(screen.getAllByText("off-universe", { selector: ".pill" })[0]).toHaveAttribute(
      "title",
      expect.stringContaining("off the deterministic universe"),
    );
    // the edgar name (SMR) shows no pill — provenance never over-claims a sweep contribution
    expect(within(rowOf("SMR")).queryByText("off-universe")).not.toBeInTheDocument();
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
    await screen.findByLabelText("include CCJ");
    expect(screen.getByText("Metal Mining")).toBeInTheDocument(); // sector chip (bridged by security_id)
    expect(screen.getByText("NYSE")).toBeInTheDocument(); // exchange chip
    // an actively-listed name shows NO not-listed flag
    expect(screen.queryByText(/no current listing found in EDGAR/)).not.toBeInTheDocument();
  });

  it("renders the origin chip on a placed foreign name — and NOTHING when origin is unknown (honest abstain)", async () => {
    const user = userEvent.setup();
    // the China-ADR shape: the backend derived "Shanghai" (business country null -> city fallback)
    const PLACED_FOREIGN = {
      name: "NIO Inc.",
      ticker: "NIO",
      prose: "EV maker",
      segment: "reactors",
      status: "placed",
      security_id: "s-nio",
      candidates: [],
      matched_terms: [],
      discovery_source: "edgar",
      sector: "Motor Vehicles",
      exchange: "NYSE",
      listing_status: "active",
      origin: "Shanghai",
    };
    // an un-enriched name: origin null -> NO origin chip (never a guessed/defaulted origin)
    const PLACED_UNKNOWN = {
      name: "Mystery Co",
      ticker: "MYST",
      prose: "sparse filer",
      segment: "reactors",
      status: "placed",
      security_id: "s-myst",
      candidates: [],
      matched_terms: [],
      discovery_source: "edgar",
      sector: null,
      exchange: null,
      listing_status: null,
      origin: null,
    };
    mockDraft(draft([PLACED_FOREIGN, PLACED_UNKNOWN]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include NIO");
    // the foreign name is PRESENT (origin TAGS, never filters — #9) with the origin chip + its basis on hover
    const chip = screen.getByText("Shanghai");
    expect(chip).toBeInTheDocument();
    expect(chip.getAttribute("title")).toMatch(/business address|incorporation/);
    // the unknown-origin name is present too, with NO origin chip anywhere in its row
    const mystRow = rowOf("MYST");
    expect(mystRow).toBeTruthy();
    expect(within(mystRow).queryByTitle(/origin — business address/)).not.toBeInTheDocument();
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
    expect(screen.getByLabelText("include ALKS")).toBeInTheDocument();

    // a draft-PLACED name (SMR) never came from To-Review → it gets no send-back (the control marks the exception)
    expect(
      screen.queryByRole("button", { name: "send SMR back to review" }),
    ).not.toBeInTheDocument();

    // the visible inverse of add: send ALKS back → removed from the basket, reappears in To-Review (re-addable)
    await user.click(screen.getByRole("button", { name: "send ALKS back to review" }));
    expect(screen.queryByLabelText("include ALKS")).not.toBeInTheDocument(); // gone from PLACED
    expect(screen.getByRole("checkbox", { name: "add ALKS" })).toBeInTheDocument(); // back in To-Review

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
    expect(body.basket.find((m) => m.ticker === "ALKS")).toBeUndefined(); // Save no longer carries it
  });

  it("a re-draft pins EDITED descriptions, carries sign-off, re-rolls drafted, parks-to-Discovered, adds new (#3)", async () => {
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
    // draft 2: SMR moves segment, GEV would move (but its description was edited), LOTTO is gone, CCJ is new
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
    await screen.findByLabelText("include SMR");

    // EDIT GEV's description (→ operator_edited — the ONE pin) and SIGN OFF SMR (a marker, not a pin)
    await user.type(screen.getByLabelText("thesis-fit for GEV"), " — mine");
    await user.click(screen.getByRole("button", { name: "sign off SMR" }));

    // re-draft with the different result (swap the polled job result, then click Draft again)
    h.jobData = { job_id: "j1", status: "done", result: D2, error: null };
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include CCJ"); // the brand-new name appeared

    // the EDITED name is pinned — GEV keeps its link (not draft-2's "power") and its words stay yours
    expect(chipsOf("GEV")).toEqual(["turbines"]);
    expect(screen.getByLabelText("thesis-fit for GEV")).toHaveValue("G1 — mine");
    expect(within(rowOf("GEV")).getByText("your words")).toBeInTheDocument();
    // a still-placed drafted name is re-rolled, but "smr-reactors" is a fabricated link on the additive chain
    // (draft 1 already built the chain), so it files into the Discovered pen rather than inventing the link…
    expect(chipsOf("SMR")).toEqual(["Discovered (unsorted)"]);
    // …and locked decision 4: the sign-off flag CARRIED across the re-roll (it endorses the NAME)
    expect(screen.getByRole("button", { name: "withdraw sign-off SMR" })).toBeInTheDocument();
    // a drafted name the new draft no longer places is parked in Discovered (no stale segment)
    expect(chipsOf("LOTTO")).toEqual(["Discovered (unsorted)"]);
    // the new name landed as a drafted, sign-off-able placement (included, not endorsed)
    expect(screen.getByRole("button", { name: "sign off CCJ" })).toBeInTheDocument();
    // OKLO (established — in the saved thesis at mount, in neither draft) is untouched and still present
    expect(screen.getByLabelText("include OKLO")).toBeInTheDocument();
    expect(chipsOf("OKLO")).toEqual([]);
  });
});

describe("ChainEditor — placed-row polish (R1/R2/R3)", () => {
  it("R1: the sign-off toggle right-aligns in the row-actions group of the controls row", () => {
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />); // OKLO: not signed off → a "sign off" toggle
    const signBtn = screen.getByRole("button", { name: "sign off OKLO" });
    // the action lives in the row-actions group…
    expect(signBtn.closest(".rowactions")).not.toBeNull();
    // …which sits INSIDE the controls (.ctls) row — the second line, beside the link chips
    expect(signBtn.closest(".ctls")).not.toBeNull();
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

  it("R3 + the LADDER: excluding collapses the detail to a stub — sign-off is reachable ONLY while included", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");
    // baseline: the drafted SMR shows its full detail
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();

    // exclude SMR → its prose, chips and SIGN-OFF collapse (excluded wins on the ladder — an excluded
    // name cannot be endorsed); the checkbox + an "excluded" stub remain (#9)
    await user.click(screen.getByLabelText("include SMR"));
    expect(screen.queryByLabelText("thesis-fit for SMR")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "sign off SMR" })).not.toBeInTheDocument();
    expect(screen.getByText("excluded", { selector: ".wb-exc-tag" })).toBeInTheDocument();
    expect(screen.getByLabelText("include SMR")).toBeInTheDocument(); // re-includable in one click

    // re-check restores everything — and NEITHER authorship NOR the flag moved (still a model draft,
    // still not signed off: the exclude cycle changed include-state only)
    await user.click(screen.getByLabelText("include SMR"));
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();
    expect(within(rowOf("SMR")).getByText("model draft")).toBeInTheDocument();
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
    await screen.findByLabelText("include SMR"); // Placed is open by default

    // collapse Placed → its rows hide, but the header (a button, with its count) stays for re-expand
    await user.click(screen.getByRole("button", { name: /Placed/ }));
    expect(screen.queryByLabelText("include SMR")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Placed/ })).toBeInTheDocument();
    // re-open restores the list
    await user.click(screen.getByRole("button", { name: /Placed/ }));
    expect(screen.getByLabelText("include SMR")).toBeInTheDocument();

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
    await screen.findByLabelText("include SMR");

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
    await screen.findByLabelText("include SMR");

    await user.click(screen.getByLabelText("include SMR")); // exclude SMR
    expect(screen.getByLabelText("include SMR")).not.toBeChecked();
    expect(screen.getByText("· 1 of 2 included")).toBeInTheDocument();
    // still VISIBLE (#9 — never a silent drop): the checkbox stays + an "excluded" stub shows, but the
    // DETAIL collapses (R3) — its controls hide, the row recedes
    expect(screen.getByText("excluded", { selector: ".wb-exc-tag" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "sign off SMR" })).not.toBeInTheDocument(); // detail hidden while excluded
    // re-check restores the full detail — nothing lost
    await user.click(screen.getByLabelText("include SMR"));
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("include SMR")); // exclude again for the save assertion

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const b = saveBody().basket;
    expect(b).toHaveLength(1);
    expect(b[0]).toMatchObject({ ticker: "OKLO" });
  });

  it("'clear not signed-off' sweeps by the FLAG: un-endorsed new names excluded, established kept, nothing else touched", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([PLACED_SMR])); // SMR loads system_drafted + not signed off; OKLO is established
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    await user.click(screen.getByRole("button", { name: /clear not signed-off/ }));
    expect(screen.getByLabelText("include SMR")).not.toBeChecked(); // not signed off → excluded
    expect(screen.getByLabelText("include OKLO")).toBeChecked(); // established → never swept
    // the sweep keyed on the FLAG and touched nothing else — re-include to inspect: SMR is still not
    // signed off (the offer, not the withdraw) and still a model draft; only its include changed
    await user.click(screen.getByLabelText("include SMR")); // re-include to inspect
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();
    expect(within(rowOf("SMR")).getByText("model draft")).toBeInTheDocument();
    await user.click(screen.getByLabelText("include SMR")); // exclude again for the save assertion

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO"]);
  });

  it("'clear not signed-off' KEEPS a signed-off new name (the endorsed survive the sweep)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    await user.click(screen.getByRole("button", { name: "sign off SMR" })); // endorse it first
    await user.click(screen.getByRole("button", { name: /clear not signed-off/ }));
    expect(screen.getByLabelText("include SMR")).toBeChecked(); // endorsed → kept
  });

  it("exports only included placed names", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    await user.click(screen.getByLabelText("include SMR"));
    await user.click(screen.getByRole("button", { name: "export 1 included names" }));

    expect(exportSpy).toHaveBeenCalledWith({
      thesisName: "Nuclear",
      stage: "triage",
      asof: "2026-06-08",
      rows: [{ ticker: "OKLO", name: null }],
    });
  });

  it("exclude/include all NEW sweep the working set only; per-row demotion still reaches the empty-basket confirm", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false); // operator cancels the wipe
    mockDraft(draft([PLACED_SMR])); // a WORKING name next to the established OKLO
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    // working-scoped: exclude-all-new excludes the drafted SMR but never touches the established OKLO
    await user.click(screen.getByRole("button", { name: "exclude all new" }));
    expect(screen.getByText("· 1 of 2 included")).toBeInTheDocument();
    expect(screen.getByLabelText("include OKLO")).toBeChecked();

    // the empty basket stays reachable by PER-ROW demotion of the established name → Save confirms the wipe
    await user.click(screen.getByLabelText("include OKLO"));
    expect(screen.getByText("· 0 of 2 included")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(h.mutate).not.toHaveBeenCalled(); // cancelled → nothing persisted

    // include-all-new restores the working set — the demoted OKLO sits in working now, so it returns too
    await user.click(screen.getByRole("button", { name: "include all new" }));
    expect(screen.getByText("· 2 of 2 included")).toBeInTheDocument();
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
    await screen.findByLabelText("include MU");
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
  // off-thesis, but TWO discovery terms → the stronger keyword evidence → "Low signal" (2+ terms)
  const VOFF2 = {
    name: "Seagate",
    ticker: "STX",
    prose: "adjacent storage — weak thesis tie",
    segment: "Discovered",
    status: "verify",
    security_id: "s-stx",
    candidates: [],
    matched_terms: ["memory", "storage"],
    off_thesis: true,
  };
  // off-thesis with ZERO keyword provenance — an off-universe name the model surfaced on its own. Sorts to
  // the TOP of "Lowest signal" (weakest keyword-wise, but the model's own suggestion — worth the eyeball).
  const VOFFZERO = {
    name: "Ghost Storage Co",
    ticker: "GST",
    prose: "model-surfaced, no keyword hit",
    segment: "Discovered",
    status: "verify",
    security_id: "s-gst",
    candidates: [],
    matched_terms: [],
    off_thesis: true,
    discovery_source: "off_universe",
  };

  it("items 4+5: To Review surfaces keepers, splits off-thesis into Low signal (2+ terms) / Lowest signal (≤1) + ticker-less", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP, VOFF2, VOFF, VNOTICK], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // the keeper is SURFACED at the top (no per-row "recommend add" badge — it'd be true of every visible keeper)
    await screen.findByText("Micron");
    expect(screen.getByRole("checkbox", { name: "add MU" })).toBeEnabled(); // check-to-add is live for a keeper
    expect(screen.queryByText("recommend add")).not.toBeInTheDocument();
    // the off-thesis majority + the ticker-less names are QUIET + collapsed (not visible until expanded, #7/#9)
    expect(screen.queryByText("Seagate")).not.toBeInTheDocument();
    expect(screen.queryByText("Kroger")).not.toBeInTheDocument();
    expect(screen.queryByText("Some Holdco LLC")).not.toBeInTheDocument();
    // the off-thesis noise is now TWO drawers, split by keyword provenance
    expect(screen.getByText("Low signal")).toBeInTheDocument();
    expect(screen.getByText("Lowest signal")).toBeInTheDocument();
    expect(screen.getByText("No listed ticker")).toBeInTheDocument();
    // the To review count is KEEPERS-ONLY — the noise buckets are nested sub-drawers with their own counts
    expect(screen.getByRole("button", { name: /To review/ })).toHaveTextContent("· 1");
    // expand Low signal → the 2-term off-thesis name appears, still promotable (never dropped)
    await user.click(screen.getByText("Low signal"));
    expect(screen.getByText("Seagate")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "add STX" })).toBeInTheDocument();
    // expand Lowest signal → the single-term off-thesis name appears, still promotable
    await user.click(screen.getByText("Lowest signal"));
    expect(screen.getByText("Kroger")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "add KR" })).toBeInTheDocument();
    // expand No listed ticker → the ticker-less name shows, but its add is DISABLED (not directly investable)
    await user.click(screen.getByText("No listed ticker"));
    expect(screen.getByRole("checkbox", { name: "add Some Holdco LLC" })).toBeDisabled();
    // the Discovered-segment rows never read "recommend → Discovered" (a non-recommendation), but keep `matched`
    expect(screen.queryByText(/recommend → Discovered/)).not.toBeInTheDocument();
    expect(screen.getByText("matched memory")).toBeInTheDocument(); // Kroger's provenance (single term) still shows
  });

  it("a VERIFY candidate already in the basket is filtered from To-Review — no unselectable keeper (#3)", async () => {
    const user = userEvent.setup();
    // the re-draft re-surfaces OKLO (already the basket keeper) as a VERIFY hit, alongside a genuine new keeper
    const VERIFY_OKLO = { ...VERIFY_ALKS, name: "Oklo Inc", ticker: "OKLO", security_id: "s-oklo" };
    mockDraft(draft([VERIFY_ALKS, VERIFY_OKLO], [{ label: "therapeutics", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // the genuinely-new keeper surfaces with a live add…
    expect(await screen.findByRole("checkbox", { name: "add ALKS" })).toBeEnabled();
    // …but OKLO, already the basket member, is NOT re-offered as an unselectable keeper (a duplicate carrying no action)
    expect(screen.queryByRole("checkbox", { name: "add OKLO" })).not.toBeInTheDocument();
  });

  it("split ordering: a 0-term off-universe name sorts to the TOP of Lowest signal, above the single-term hits", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VOFF, VOFFZERO], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("Lowest signal");

    // no "Low signal" drawer — neither name has 2+ terms (honest loudness #7: an empty bucket doesn't render)
    expect(screen.queryByText("Low signal")).not.toBeInTheDocument();
    await user.click(screen.getByText("Lowest signal"));
    // the 0-term (off-universe, no keyword provenance) name comes BEFORE the single-term hit
    const zero = screen.getByText("Ghost Storage Co");
    const one = screen.getByText("Kroger");
    expect(zero.compareDocumentPosition(one) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

    await user.click(screen.getByText("Lowest signal"));
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

    await user.click(screen.getByText("Lowest signal"));
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

    await user.click(screen.getByText("Lowest signal"));
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

    await user.click(screen.getByText("Lowest signal"));
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
    await screen.findByLabelText("include FOO");
    expect(screen.getByText("not a link")).toBeInTheDocument(); // the de-link tag on the pen chip
    expect(screen.getByText("Unsorted")).toBeInTheDocument(); // the pen's region label (separate from the links)
    // the nudge is honest about THIS surface: the draft didn't arrange it; sorting lives on triage
    expect(screen.getByText(/didn't arrange it into a link/)).toBeInTheDocument();
    // the row's chip reads the pen state — read-only (no seg dropdown on this surface)
    expect(chipsOf("FOO")).toEqual(["Discovered (unsorted)"]);
    expect(screen.queryByLabelText("segment for FOO")).not.toBeInTheDocument();
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

  it("D: To Review nests its sub-drawers under one master (collapsing the master hides them all)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([VKEEP, VOFF2, VOFF, VNOTICK], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    // nested sub-drawers, mirroring the Placed section: Keepers (open) · Low signal · Lowest signal · No listed ticker
    await screen.findByText("Keepers");
    expect(screen.getByText("Low signal")).toBeInTheDocument();
    expect(screen.getByText("Lowest signal")).toBeInTheDocument();
    expect(screen.getByText("No listed ticker")).toBeInTheDocument();
    // collapsing the MASTER To review hides them ALL (they're children now, not top-level siblings)
    await user.click(screen.getByRole("button", { name: /To review/ }));
    expect(screen.queryByText("Keepers")).not.toBeInTheDocument();
    expect(screen.queryByText("Low signal")).not.toBeInTheDocument();
    expect(screen.queryByText("Lowest signal")).not.toBeInTheDocument();
    expect(screen.queryByText("No listed ticker")).not.toBeInTheDocument();
  });

  it("E: a ticker'd blank-check shell splits out of Keepers into its own collapsed drawer (still promotable)", async () => {
    const user = userEvent.setup();
    const VSPAC = {
      name: "Big Sky Growth Partners",
      ticker: "BSKY",
      prose: "a blank-check vehicle — the theme terms sit in its S-1 boilerplate",
      segment: "Discovered",
      status: "verify",
      security_id: "s-bsky",
      candidates: [],
      matched_terms: ["memory"],
      off_thesis: false,
      sector: "Blank Checks", // SIC 6770's description, verbatim — the deterministic shell tell
    };
    mockDraft(draft([VKEEP, VSPAC], [{ label: "memory", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // the shell lands in its own drawer, NOT Keepers — the signal slot stays honest
    await screen.findByText("Blank checks");
    expect(screen.getByRole("checkbox", { name: "add MU" })).toBeInTheDocument(); // the real keeper, open drawer
    expect(screen.queryByRole("checkbox", { name: "add BSKY" })).not.toBeInTheDocument(); // collapsed by default
    // the master To review headline stays keepers-only (the shell never inflates the signal count)
    expect(screen.getByRole("button", { name: /To review/ })).toHaveTextContent("· 1");
    // hidden, never dropped (#9): expand → the shell is fully promotable via the same check-to-add
    await user.click(screen.getByRole("button", { name: "toggle Blank checks" }));
    expect(screen.getByRole("checkbox", { name: "add BSKY" })).toBeInTheDocument();
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
    await screen.findByLabelText("include KR");
    expect(screen.getByText(/model thinks off-thesis/)).toBeInTheDocument();
    expect(rowOf("KR")).toHaveClass("flagged"); // the amber tint lights up
    // no hard-remove button — the prune is the (reversible) include checkbox
    expect(screen.queryByRole("button", { name: "remove" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("include KR")).toBeChecked();
  });

  it("does NOT flag an on-thesis placement — fail-open, no off_thesis → no flag", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR])); // no off_thesis field on the placement
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");
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
    await screen.findByLabelText("include MU");
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
    await screen.findByLabelText("include MU");

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

  it("C-B ordering: 'Placed, flagged' surfaces stronger keyword provenance first (matched-term count, desc)", async () => {
    const user = userEvent.setup();
    // two flagged (off-thesis but placed) names; neither a junk tell (broad terms). Emit the SINGLE-term name
    // FIRST so only the new count sort can move it BELOW the two-term name.
    const F_ONE = {
      name: "Beta Flag Co",
      ticker: "BFC",
      prose: "single broad hit",
      segment: "memory",
      status: "placed",
      security_id: "s-bfc",
      candidates: [],
      matched_terms: ["memory"],
      off_thesis: true,
    };
    const F_MULTI = {
      name: "Alpha Flag Co",
      ticker: "AFC",
      prose: "two hits — stronger provenance",
      segment: "memory",
      status: "placed",
      security_id: "s-afc",
      candidates: [],
      matched_terms: ["memory", "storage"],
      off_thesis: true,
    };
    mockDraft(draft([P_CLEAN, F_ONE, F_MULTI], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("toggle Placed, flagged");

    // the 2-term flagged name sorts ABOVE the 1-term one, regardless of emission order
    const multi = screen.getByText("Alpha Flag Co");
    const one = screen.getByText("Beta Flag Co");
    expect(multi.compareDocumentPosition(one) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("C-B ordering: an explicit sort (ticker) OVERRIDES the flagged group's term-count order", async () => {
    const user = userEvent.setup();
    // low ticker / single term vs high ticker / two terms — the two orderings disagree, so we can tell which wins
    const F_AAA_ONE = {
      name: "Aaa Flag Co",
      ticker: "AAA",
      prose: "single hit",
      segment: "memory",
      status: "placed",
      security_id: "s-aaa",
      candidates: [],
      matched_terms: ["memory"],
      off_thesis: true,
    };
    const F_ZZZ_MULTI = {
      name: "Zzz Flag Co",
      ticker: "ZZZ",
      prose: "two hits",
      segment: "memory",
      status: "placed",
      security_id: "s-zzz",
      candidates: [],
      matched_terms: ["memory", "storage"],
      off_thesis: true,
    };
    mockDraft(draft([P_CLEAN, F_AAA_ONE, F_ZZZ_MULTI], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("toggle Placed, flagged");

    // default (draft) order: term count wins → the 2-term ZZZ sorts ABOVE the 1-term AAA
    expect(
      screen.getByText("Zzz Flag Co").compareDocumentPosition(screen.getByText("Aaa Flag Co")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // pick "name" (ticker) sort → the dropdown wins: AAA sorts ABOVE ZZZ, term count no longer applies
    await user.selectOptions(screen.getByLabelText("sort placed names"), "name");
    expect(
      screen.getByText("Aaa Flag Co").compareDocumentPosition(screen.getByText("Zzz Flag Co")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // clearing filters resets the sort to draft → the term-count default is RESTORED (ZZZ back above AAA)
    await user.click(screen.getByRole("button", { name: "clear filters" }));
    expect(
      screen.getByText("Zzz Flag Co").compareDocumentPosition(screen.getByText("Aaa Flag Co")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("G: sole-acronym without model flag stays in Placed (not low quality)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([P_CLEAN, P_COLLISION], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include MU");

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
    await screen.findByLabelText("include MU");

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

  it("a drafted name carries NO archetype key through Save (the field is retired from the wire)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([P_CLEAN], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include MU");
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const mu = saveBody().basket.find((b) => b.ticker === "MU");
    expect(mu && "archetype" in mu).toBe(false); // retired: the spine carries no type field at all
  });

  it("G precedence: off-thesis + junk tell lands in low-quality group, not flagged", async () => {
    const user = userEvent.setup();
    mockDraft(draft([P_CLEAN, { ...P_COLLISION, off_thesis: true }], MEM_SEG));
    render(<ChainEditor asof="2026-06-08" thesis={hbmThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include MU");
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
    await screen.findByLabelText("include MU");
    expect(screen.getByLabelText("toggle Placed, low quality")).toBeInTheDocument();
    expect(screen.queryByLabelText("toggle Placed, flagged")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("toggle Placed, low quality"));
    expect(screen.getByText("BlackRock Multi-Asset Income Trust")).toBeInTheDocument();
  });
});

describe("ChainEditor — conviction is OFF this surface; the field survives Save (S1)", () => {
  const saveBody = () => h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
  const withOnSuccess = () =>
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );

  it("renders NO conviction control, and a STORED weight rides Save untouched (the field stays on the model)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    const weighted = {
      ...flatThesis,
      basket: [{ ...flatThesis.basket[0], conviction: 4 }],
    };
    render(<ChainEditor asof="2026-06-08" thesis={weighted} onDone={vi.fn()} />);
    expect(screen.queryByLabelText("conviction for OKLO")).not.toBeInTheDocument(); // control gone
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket[0]).toMatchObject({ ticker: "OKLO", conviction: 4 }); // value untouched
  });

  it("an unset weight stays NULL through Save (never 0)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket[0].conviction).toBeNull();
  });
});

describe("ChainEditor — multi-membership display + Save (S1: chips are REAL rows)", () => {
  const saveBody = () => h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };

  it("a name recommended into TWO links renders ONE row with TWO chips — and Save persists BOTH rows", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    mockDraft(
      draft(
        [
          { ...PLACED_SMR, segment: "reactors" },
          { ...PLACED_SMR, segment: "fuel", prose: "" },
        ],
        [
          { label: "reactors", descriptor: null },
          { label: "fuel", descriptor: null },
        ],
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    // ONE display row per NAME (one include checkbox), carrying a chip per recommended link
    expect(screen.getAllByLabelText("include SMR")).toHaveLength(1);
    expect(chipsOf("SMR")).toEqual(["reactors", "fuel"]);
    // the counts count NAMES, not rows (2 names: OKLO + SMR — not 3 rows)
    expect(screen.getByText("· 2 of 2 included")).toBeInTheDocument();

    // per-NAME actions co-mutate the whole name: one sign-off click endorses it once
    await user.click(screen.getByRole("button", { name: "sign off SMR" }));
    expect(screen.getByRole("button", { name: "withdraw sign-off SMR" })).toBeInTheDocument();

    // COUNT THE PAYLOAD: Save persists BOTH membership rows (same security_id, one per link)
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const smrRows = saveBody().basket.filter((m) => m.ticker === "SMR");
    expect(smrRows).toHaveLength(2);
    expect(smrRows.map((m) => m.segment)).toEqual(["reactors", "fuel"]);
    expect(smrRows.every((m) => m.security_id === "s-smr")).toBe(true);
    expect(smrRows.every((m) => m.signed_off === true)).toBe(true); // the per-name flag on every row
  });

  it("excluding a multi-membership name drops ALL its rows from Save (per-name include)", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    mockDraft(
      draft(
        [
          { ...PLACED_SMR, segment: "reactors" },
          { ...PLACED_SMR, segment: "fuel" },
        ],
        [
          { label: "reactors", descriptor: null },
          { label: "fuel", descriptor: null },
        ],
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    await user.click(screen.getByLabelText("include SMR")); // exclude the NAME (both rows)
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.filter((m) => m.ticker === "SMR")).toHaveLength(0);
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO"]);
  });
});

describe("ChainEditor — TRIAGE sort/filter (the find)", () => {
  // a 3-name basket spanning segments / authorship — enough to sort + filter (the bar shows for >1)
  const triageThesis = {
    ...flatThesis,
    segments: [
      { label: "reactors", descriptor: null },
      { label: "fuel", descriptor: null },
    ],
    basket: [
      { ticker: "OKLO", role: "—", security_id: "s-oklo", segment: "reactors", authored_by: "operator_set" },
      { ticker: "CCJ", role: "—", security_id: "s-ccj", segment: "fuel", authored_by: "system_drafted" },
      { ticker: "BWXT", role: "—", security_id: "s-bwxt", segment: "reactors", authored_by: "operator_set" },
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

  it("the Country + Exchange filters narrow BOTH the placed rows and the To-Review candidates", async () => {
    const user = userEvent.setup();
    const mk = (
      ticker: string,
      security_id: string,
      origin: string | null,
      exchange: string | null,
      status: "placed" | "verify",
    ) => ({
      name: ticker,
      ticker,
      prose: "x",
      segment: "reactors",
      status,
      security_id,
      candidates: [],
      matched_terms: [],
      discovery_source: "edgar",
      sector: "X",
      exchange,
      listing_status: "active",
      origin,
      off_thesis: false,
    });
    mockDraft(
      draft([
        mk("USCO", "s-us", "US", "Nasdaq", "placed"),
        mk("CNCO", "s-cn", "Shanghai", "NYSE", "placed"),
        mk("FGNV", "s-fv", "London", "NYSE", "verify"), // an on-thesis To-Review keeper, foreign
      ]),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // baseline: both placed names + the foreign To-Review keeper are present
    await screen.findByLabelText("include USCO");
    expect(screen.getByLabelText("include CNCO")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "add FGNV" })).toBeInTheDocument();

    // Country = foreign → the US placed name drops; the foreign placed + foreign To-Review names stay
    await user.selectOptions(screen.getByLabelText("filter by country"), "foreign");
    expect(screen.queryByLabelText("include USCO")).not.toBeInTheDocument();
    expect(screen.getByLabelText("include CNCO")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "add FGNV" })).toBeInTheDocument();

    // Country = US → the foreign placed name AND the foreign To-Review keeper both drop (spans both lists)
    await user.selectOptions(screen.getByLabelText("filter by country"), "us");
    expect(screen.getByLabelText("include USCO")).toBeInTheDocument();
    expect(screen.queryByLabelText("include CNCO")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "add FGNV" })).not.toBeInTheDocument();

    // Exchange = OTC → nothing here is OTC, so every placed row drops (view-only, reversible with clear)
    await user.selectOptions(screen.getByLabelText("filter by country"), "all");
    await user.selectOptions(screen.getByLabelText("filter by exchange"), "otc");
    expect(screen.queryByLabelText("include USCO")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("include CNCO")).not.toBeInTheDocument();
  });

  it("the Type filter narrows blank-check shells across placed + To-Review (view-only, reversible)", async () => {
    const user = userEvent.setup();
    const mk = (
      ticker: string,
      security_id: string,
      sector: string | null,
      status: "placed" | "verify",
    ) => ({
      name: ticker,
      ticker,
      prose: "x",
      segment: "reactors",
      status,
      security_id,
      candidates: [],
      matched_terms: [],
      discovery_source: "edgar",
      sector,
      exchange: "Nasdaq",
      listing_status: "active",
      origin: "US",
      off_thesis: false,
    });
    mockDraft(
      draft([
        mk("REAL", "s-real", "Pharmaceutical Preparations", "placed"),
        mk("SHEL", "s-shel", "Blank Checks", "placed"),
        mk("SHLV", "s-shlv", "Blank Checks", "verify"), // a ticker'd To-Review shell → the Blank checks drawer
      ]),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // baseline: both placed names render; the To-Review shell sits in its Blank checks drawer
    await screen.findByLabelText("include REAL");
    expect(screen.getByLabelText("include SHEL")).toBeInTheDocument();
    expect(screen.getByText("Blank checks")).toBeInTheDocument();

    // type = blank check → only the shells remain (the operating company drops from view)
    await user.selectOptions(screen.getByLabelText("filter by type"), "spac");
    expect(screen.queryByLabelText("include REAL")).not.toBeInTheDocument();
    expect(screen.getByLabelText("include SHEL")).toBeInTheDocument();

    // type = other → the shells drop from view, placed AND To-Review alike (the drawer empties away)
    await user.selectOptions(screen.getByLabelText("filter by type"), "other");
    expect(screen.getByLabelText("include REAL")).toBeInTheDocument();
    expect(screen.queryByLabelText("include SHEL")).not.toBeInTheDocument();
    expect(screen.queryByText("Blank checks")).not.toBeInTheDocument();

    // reversible: back to all → everything returns (view-only, Save untouched)
    await user.selectOptions(screen.getByLabelText("filter by type"), "");
    expect(screen.getByLabelText("include SHEL")).toBeInTheDocument();
  });

  it("THE #9 SPINE: the VIEW never changes what Save persists — a filtered-out, included name still saves", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );
    render(<ChainEditor asof="2026-06-08" thesis={triageThesis} onDone={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("filter by segment"), "fuel"); // hides OKLO + BWXT (reactors)
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
    // all three persist — the filter hides, only exclude drops (basket − excluded, over the whole draft)
    expect(body.basket.map((m) => m.ticker).sort()).toEqual(["BWXT", "CCJ", "OKLO"]);
  });

  it("clear filters restores the full view (#9 — a hidden name is one click from visible)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={triageThesis} onDone={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("filter by segment"), "fuel");
    expect(screen.queryByLabelText("include OKLO")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "clear filters" }));
    expect(screen.getByLabelText("include OKLO")).toBeInTheDocument();
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
  empty_terms: [] as string[],
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

    await screen.findByLabelText("include SMR"); // the draft itself loaded
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

  it("a dead SIGNAL seed renders the ∅ marker LOUD on its own chip, and the strip names it", async () => {
    const user = userEvent.setup();
    mockDraft(draftWithReport([PLACED_SMR], healthyReport({ empty_terms: ["psilocybin"] })));
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await screen.findByText(/completed with gaps/); // a dead seed IS a gap -> the strip goes loud
    expect(screen.getByText(/Zero EDGAR hits: psilocybin/)).toBeInTheDocument(); // the strip NAMES it
    const empty = screen.getAllByText("∅ no EDGAR hits");
    expect(empty).toHaveLength(1); // psilocybin's chip only — ketamine hit, so no marker
    expect(empty[0].closest("li")).toHaveTextContent("psilocybin"); // on the matching chip
    expect(empty[0]).toHaveClass("wb-empty-loud"); // a SIGNAL seed places alone -> a dead one is loud
  });

  it("dead-seed loudness SPLITS by tier — a SIGNAL seed is loud, a BROAD term is quiet", async () => {
    const user = userEvent.setup();
    mockDraft(
      draftWithReport([PLACED_SMR], healthyReport({ empty_terms: ["psilocybin", "ketamine"] })),
    );
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await screen.findByText(/completed with gaps/);
    const chips = screen.getAllByText("∅ no EDGAR hits");
    expect(chips).toHaveLength(2); // BOTH dead terms are flagged — nothing hidden (keep-it-visible)
    const chipFor = (term: string) =>
      chips.find((c) => c.closest("li")?.textContent?.includes(term))!;
    const signalChip = chipFor("psilocybin");
    const broadChip = chipFor("ketamine");
    expect(signalChip).toHaveClass("wb-empty-loud"); // SIGNAL seed -> loud (the high-stakes silent miss)
    expect(broadChip).toHaveClass("wb-empty-quiet"); // BROAD term -> quiet (corroboration-only)
    expect(signalChip.className).not.toEqual(broadChip.className); // the treatment DIFFERS by tier
  });
});

describe("ChainEditor — #7 excluded-name permanence (the durable NO)", () => {
  const member = (ticker: string, sid: string) => ({
    ticker,
    role: "r",
    
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

describe("ChainEditor — the Basket section (the additive editor)", () => {
  // an established (saved-spine) member — present in the thesis at mount, so the hook freezes it
  const est = (ticker: string, sid: string, over: Record<string, unknown> = {}) => ({
    ticker,
    role: "—",
    security_id: sid,
    segment: null,
    thesis_fit: null,
    conviction: null,
    authored_by: "operator_set",
    ...over,
  });
  const withOnSuccess = () =>
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    );

  it("(a) an established member renders in the frozen Basket panel; a drafted new name lands in working", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    // the panel renders (established names exist) with the established row inside it
    expect(screen.getByRole("button", { name: /the saved basket/ })).toBeInTheDocument();
    expect(screen.getByLabelText("include OKLO").closest(".wb-basket")).not.toBeNull();
    // nothing NEW yet — the working list says so honestly (not a filter artifact)
    expect(screen.getByText(/no new names — draft from the narrative/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    const smr = await screen.findByLabelText("include SMR");
    // the NEW name lands in the working partitions below — never in the frozen Basket
    expect(smr.closest(".wb-basket")).toBeNull();
    expect(screen.getByLabelText("include OKLO").closest(".wb-basket")).not.toBeNull();
    expect(screen.getByText("· 1 of 1 kept")).toBeInTheDocument(); // the panel's own count
    expect(screen.queryByText(/no new names — draft from the narrative/)).not.toBeInTheDocument();
  });

  it("(b) a re-draft leaves an un-endorsed ESTABLISHED member untouched — kept when re-placed, not parked when absent", async () => {
    const user = userEvent.setup();
    // an established member that is STILL system_drafted and NOT signed off (the operator saved without
    // endorsing) — the worst case: nothing but `established` protects it from the re-roll / the parking.
    const estDrafted = {
      ...flatThesis,
      segments: [{ label: "reactors", descriptor: null }],
      basket: [
        est("SMR", "s-smr", { segment: "reactors", thesis_fit: "P0", authored_by: "system_drafted" }),
      ],
    };
    // draft 1 RE-places SMR with a fresh segment + prose (an established member must NOT re-roll) and surfaces
    // one genuinely-new name — SMR itself is frozen so it shows no change; GEV is our signal the draft loaded
    mockDraft(
      draft(
        [
          { ...PLACED_SMR, segment: "smr-reactors", prose: "P2" },
          {
            name: "GE Vernova",
            ticker: "GEV",
            prose: "new",
            segment: "smr-reactors",
            status: "placed",
            security_id: "s-gev-1",
            candidates: [],
            matched_terms: [],
          },
        ],
        [{ label: "smr-reactors", descriptor: null }],
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={estDrafted as never} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include GEV"); // the NEW name arrived → the draft loaded

    expect(chipsOf("SMR")).toEqual(["reactors"]); // kept — the frozen member never re-rolled
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue("P0"); // prose kept — P2 never landed
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument(); // still un-endorsed
    // the new GEV lands in Discovered — the fabricated "smr-reactors" link is never invented (additive chain)
    expect(chipsOf("GEV")).toEqual(["Discovered (unsorted)"]);

    // draft 2 no longer places SMR at all — an established member is NOT parked to Discovered
    h.jobData = {
      job_id: "j1",
      status: "done",
      result: draft(
        [
          {
            ...PLACED_SMR,
            name: "Cameco",
            ticker: "CCJ",
            security_id: "s-ccj-n",
            segment: "mining",
            prose: "C1",
          },
        ],
        [{ label: "mining", descriptor: null }],
      ),
      error: null,
    };
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include CCJ");
    expect(chipsOf("SMR")).toEqual(["reactors"]); // untouched — never parked to Discovered
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue("P0");
  });

  it("(c) uncheck in the Basket sends the row down as an excluded stub; re-check restores; Save = kept ∪ new, demoted → exclusions PUT", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    const twoEst = { ...flatThesis, basket: [flatThesis.basket[0], est("CCJ", "s-ccj2")] };
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={twoEst as never} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");
    expect(screen.getByLabelText("include CCJ").closest(".wb-basket")).not.toBeNull();

    // uncheck CCJ in the Basket → it leaves the panel and reappears below as an excluded stub ("send down")
    await user.click(screen.getByLabelText("include CCJ"));
    const stub = screen.getByText("excluded", { selector: ".wb-exc-tag" });
    expect(stub.closest(".wb-basket")).toBeNull(); // below, in the working list — never vanished (#2)
    expect(screen.getByText("· 1 of 2 kept")).toBeInTheDocument(); // the panel count dropped

    // re-check → restored to the Basket panel (reversibility #1)
    await user.click(screen.getByLabelText("include CCJ"));
    expect(screen.getByLabelText("include CCJ").closest(".wb-basket")).not.toBeNull();

    // demote again and Save: the promote = Basket-kept ∪ new-included; the demoted name is absent from
    // the promote AND present in the exclusions PUT (the durable NO — existing prune semantics)
    await user.click(screen.getByLabelText("include CCJ"));
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: { ticker: string }[] };
    expect(body.basket.map((m) => m.ticker).sort()).toEqual(["OKLO", "SMR"]);
    const excl = h.putExcl.mock.calls[0][0] as { security_id: string }[];
    expect(excl.map((e) => e.security_id)).toContain("s-ccj2");
  });

  it("demoting EVERY established name keeps the Basket header with the all-demoted note (never vanishes)", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByLabelText("include OKLO"));
    expect(screen.getByRole("button", { name: /the saved basket/ })).toBeInTheDocument(); // header stays
    expect(screen.getByText(/all 1 demoted — re-check below to restore/)).toBeInTheDocument();
    // re-check the demoted row below → it returns to the panel, the note clears
    await user.click(screen.getByLabelText("include OKLO"));
    expect(screen.getByLabelText("include OKLO").closest(".wb-basket")).not.toBeNull();
    expect(screen.queryByText(/all 1 demoted/)).not.toBeInTheDocument();
  });

  it("(d) scoped bulk: 'clear not signed-off' skips an un-endorsed ESTABLISHED member, sweeps only new names", async () => {
    const user = userEvent.setup();
    const estDrafted = {
      ...flatThesis,
      basket: [est("GEV", "s-gev", { authored_by: "system_drafted", signed_off: false })],
    };
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={estDrafted as never} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    await user.click(screen.getByRole("button", { name: "clear not signed-off" }));
    expect(screen.getByLabelText("include SMR")).not.toBeChecked(); // the NEW un-endorsed name → excluded
    expect(screen.getByLabelText("include GEV")).toBeChecked(); // established (even un-endorsed) → untouched
  });

  it("(e) a NEW thesis (empty basket): no Basket panel, the first draft lands in working, Save graduates all included", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    const emptyThesis = { ...flatThesis, basket: [] };
    mockDraft(draft([PLACED_SMR]));
    const { container } = render(
      <ChainEditor asof="2026-06-08" thesis={emptyThesis as never} onDone={vi.fn()} />,
    );
    expect(container.querySelector(".wb-basket")).toBeNull(); // no established names → no panel

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    const smr = await screen.findByLabelText("include SMR");
    expect(smr.closest(".wb-basket")).toBeNull(); // everything is working — today's behavior, unchanged
    expect(container.querySelector(".wb-basket")).toBeNull(); // the draft does NOT conjure a panel

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const body = h.mutate.mock.calls[0][0] as { basket: { ticker: string }[] };
    expect(body.basket.map((m) => m.ticker)).toEqual(["SMR"]); // Save graduates all included
  });

  it("(g) the find bar filters BOTH lists — the sign-off filter narrows Basket rows and working rows alike", async () => {
    const user = userEvent.setup();
    const twoEst = {
      ...flatThesis,
      // the saved names are ENDORSED (signed off); the fresh draft name below is not
      basket: [est("OKLO", "s-oklo", { signed_off: true }), est("CCJ", "s-ccj2", { signed_off: true })],
    };
    mockDraft(draft([PLACED_SMR])); // a new drafted name (system_drafted, not signed off)
    render(<ChainEditor asof="2026-06-08" thesis={twoEst as never} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    await user.selectOptions(screen.getByLabelText("filter by sign-off"), "unsigned");
    expect(screen.getByLabelText("include SMR")).toBeInTheDocument(); // the un-endorsed working row kept
    expect(screen.queryByLabelText("include OKLO")).not.toBeInTheDocument(); // signed-off basket row filtered out
    expect(screen.queryByLabelText("include CCJ")).not.toBeInTheDocument(); // signed-off basket row filtered out
    // ONE whole-basket count across both lists (the denominator existing tests depend on)
    expect(screen.getByText("showing 1 of 3 placed")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("filter by sign-off"), "signed");
    expect(screen.queryByLabelText("include SMR")).not.toBeInTheDocument();
    expect(screen.getByLabelText("include OKLO")).toBeInTheDocument();
    expect(screen.getByText("showing 2 of 3 placed")).toBeInTheDocument();
  });
});

describe("ChainEditor — S2: frozen seed terms + also-matches-now (the re-scope display)", () => {
  // an established member CARRYING persisted `surfaced_terms` (the S1 wire field — frozen at Basket entry).
  // `matched` (the CURRENT run's display-only stash) starts empty until a draft lands.
  const estSeeded = (surfaced: string[]) => ({
    ...flatThesis,
    basket: [
      {
        ticker: "OKLO",
        role: "r",
        
        security_id: "s-oklo",
        segment: null,
        thesis_fit: null,
        conviction: null,
        surfaced_terms: surfaced,
        authored_by: "operator_set",
      },
    ],
  });
  // a draft placement RE-matching the established member under the current (possibly refined) term set —
  // loadDraft leaves the established row untouched, but applyDraft repopulates `matched` for it
  const rematch = (terms: string[]) => ({
    name: "Oklo Inc.",
    ticker: "OKLO",
    prose: "re-matched",
    segment: "reactors",
    status: "placed",
    security_id: "s-oklo",
    candidates: [],
    matched_terms: terms,
  });

  it("an established member with surfaced_terms and NO draft run shows the frozen line only (honest abstain)", () => {
    render(
      <ChainEditor asof="2026-06-08" thesis={estSeeded(["a", "b"]) as never} onDone={vi.fn()} />,
    );
    const line = screen.getByText("⚓ seeded by: a · b");
    expect(line).toBeInTheDocument();
    // the title explains the anchor: the at-entry discovery terms, frozen — term-set edits never change it
    expect(line).toHaveAttribute("title", expect.stringContaining("frozen at entry"));
    expect(line).toHaveAttribute("title", expect.stringContaining("entered the Basket"));
    // no current-run state → no also-now diff, and never the old single ← line for a seeded member
    expect(screen.queryByText(/also matches now/)).not.toBeInTheDocument();
    expect(screen.queryByText("← a · b")).not.toBeInTheDocument();
  });

  it("after a draft matching [b, c]: frozen stays a · b; also-now shows ONLY the new c (set difference)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([rematch(["b", "c"])], [{ label: "reactors", descriptor: null }]));
    render(
      <ChainEditor asof="2026-06-08" thesis={estSeeded(["a", "b"]) as never} onDone={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // the also-now line arrives with the draft: ONLY c ("b" is already frozen — never duplicated)
    expect(await screen.findByText("+ also matches now: c")).toBeInTheDocument();
    // the frozen record is untouched by the run — both lines visible, distinguished
    expect(screen.getByText("⚓ seeded by: a · b")).toBeInTheDocument();
    // and the old single ← line is gone for a seeded member
    expect(screen.queryByText("← b · c")).not.toBeInTheDocument();
  });

  it("a hand-added member (empty surfaced_terms) keeps today's single ← line off the current matches", async () => {
    const user = userEvent.setup();
    mockDraft(draft([rematch(["x"])], [{ label: "reactors", descriptor: null }]));
    render(<ChainEditor asof="2026-06-08" thesis={estSeeded([]) as never} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(await screen.findByText("← x")).toBeInTheDocument(); // unchanged semantics
    expect(screen.queryByText(/seeded by/)).not.toBeInTheDocument(); // no frozen record → no ⚓ line
    expect(screen.queryByText(/also matches now/)).not.toBeInTheDocument(); // the diff pairs with ⚓ only
  });

  it("ANTI-CHURN: the frozen line is byte-identical across re-drafts; only the also-now diff moves", async () => {
    const user = userEvent.setup();
    mockDraft(draft([rematch(["b", "c"])], [{ label: "reactors", descriptor: null }]));
    render(
      <ChainEditor asof="2026-06-08" thesis={estSeeded(["a", "b"]) as never} onDone={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("+ also matches now: c");
    const before = screen.getByText("⚓ seeded by: a · b").textContent;

    // a second draft under a REFINED term set — the member now matches an entirely different set
    h.jobData = {
      job_id: "j1",
      status: "done",
      result: draft([rematch(["z"])], [{ label: "reactors", descriptor: null }]),
      error: null,
    };
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("+ also matches now: z"); // the diff moved with the current terms…
    expect(screen.queryByText("+ also matches now: c")).not.toBeInTheDocument();
    // …and the frozen record did NOT — byte-identical across applyDraft (the whole point of S1+S2)
    expect(screen.getByText("⚓ seeded by: a · b").textContent).toBe(before);
  });
});

describe("ChainEditor — S3: re-scope (the button · the auto-draft · the resumed badge)", () => {
  it("⟳ Re-scope renders only when onRescope is provided (opt-in, like Clear); clicking invokes the parent", async () => {
    const user = userEvent.setup();
    // absent by default (no session-owning parent) — a test/un-sessioned render carries no re-scope
    const { unmount } = render(
      <ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "⟳ Re-scope" })).toBeNull();
    unmount();

    const onRescope = vi.fn();
    render(
      <ChainEditor
        asof="2026-06-08"
        thesis={flatThesis}
        onDone={vi.fn()}
        onRescope={onRescope}
      />,
    );
    await user.click(screen.getByRole("button", { name: "⟳ Re-scope" }));
    expect(onRescope).toHaveBeenCalledTimes(1); // the parent confirms + clears + remounts
  });

  it("⟳ Re-scope is disabled while a draft runs (one job at a time — the cost thread)", async () => {
    const user = userEvent.setup();
    // a kick-off that never reaches terminal: the job is running, no result yet → `drafting` stays true
    h.start.mockResolvedValue({ job_id: "j1", status: "running" });
    h.jobData = undefined;
    render(
      <ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} onRescope={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    expect(await screen.findByRole("button", { name: "⟳ Re-scope" })).toBeDisabled();
  });

  it("autoDraft fires the kick-off EXACTLY ONCE per mount — a rerender / prop drift never re-fires", async () => {
    mockDraft(draft([PLACED_SMR]));
    const { rerender } = render(
      <ChainEditor
        asof="2026-06-08"
        thesis={flatThesis}
        onDone={vi.fn()}
        onRescope={vi.fn()}
        autoDraft
      />,
    );
    // the mount's own kick-off completes through the SAME poll machinery — the result lands
    await screen.findByLabelText("include SMR");
    expect(h.start).toHaveBeenCalledTimes(1);
    // the parent's one-shot flag stays up for the whole edit session — a re-render must not re-fire
    rerender(
      <ChainEditor
        asof="2026-06-08"
        thesis={flatThesis}
        onDone={vi.fn()}
        onRescope={vi.fn()}
        autoDraft
      />,
    );
    expect(h.start).toHaveBeenCalledTimes(1); // the ref guard held
  });

  it("no autoDraft → a plain mount NEVER drafts on render (the rule, not the exception)", () => {
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} onRescope={vi.fn()} />);
    expect(h.start).not.toHaveBeenCalled();
  });

  it("the resumed-autosave badge renders on a restored mount with its age — and never on a fresh mount", () => {
    const twoDaysAgo = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
    const { unmount } = render(
      <ChainEditor
        asof="2026-06-08"
        thesis={flatThesis}
        onDone={vi.fn()}
        restoredUpdatedAt={twoDaysAgo}
      />,
    );
    const badge = screen.getByText("resumed autosave · 2d ago");
    expect(badge).toBeInTheDocument();
    // the title says what it means: this editor is session-driven, not spine-driven
    expect(badge).toHaveAttribute("title", expect.stringContaining("autosaved working session"));
    expect(badge).toHaveAttribute("title", expect.stringContaining("differ from the saved Basket"));
    unmount();

    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    expect(screen.queryByText(/resumed autosave/)).toBeNull(); // spine-seeded → no badge
  });
});

// --- the seeds-only fast lane (draft-scope PR-2): the quick-draft button + the scope badge ---

describe("ChainEditor — the quick draft (the seeds-only fast lane)", () => {
  it('kicks off the SAME job flow with { scope: "seeds_only" } and delivers the result', async () => {
    const user = userEvent.setup();
    mockDraft(
      draftWithReport([PLACED_SMR], healthyReport({ tail_sweep: "skipped", scope: "seeds_only" })),
    );
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Quick draft \(seeds only\)/ }));

    expect(h.start).toHaveBeenCalledTimes(1);
    expect(h.start).toHaveBeenCalledWith({ scope: "seeds_only" }); // the fast lane's kick-off payload
    await screen.findByLabelText("include SMR"); // the same kick-off + poll machinery lands the draft
  });

  it("the FULL button's kick-off is unchanged — no scope forwarded (the hook then posts NO body; see hooks.startDraft)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([PLACED_SMR]));
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(h.start).toHaveBeenCalledTimes(1);
    expect(h.start).toHaveBeenCalledWith(undefined); // exactly as today — the pre-scope wire shape holds
  });

  it("disables with the seed-terms tooltip when the term set has no SIGNAL entry, enables with one", () => {
    // flatThesis carries NO term set at all → nothing for a seeds-only run to enumerate
    const { unmount } = render(
      <ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />,
    );
    const btn = screen.getByRole("button", { name: /Quick draft \(seeds only\)/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "no SIGNAL seeds — seed terms in the drawer first");
    unmount();

    // one SIGNAL seed (psilocybin) → the fast lane opens
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Quick draft \(seeds only\)/ })).toBeEnabled();
  });

  it("a BROAD-only term set still disables it — broad terms are corroboration, not seeds", () => {
    const broadOnly = {
      ...flatThesis,
      term_set: [
        { term: "ketamine", tier: "broad", authored_by: "system_drafted", source: "keyword_gen" },
      ],
    };
    render(<ChainEditor asof="2026-06-08" thesis={broadOnly} onDone={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Quick draft \(seeds only\)/ })).toBeDisabled();
  });
});

describe("ChainEditor — the seeds-only scope badge (the strip's chosen-state line)", () => {
  // Build a RESTORED session through the REAL serialize → JSON → deserialize wire round-trip, carrying one
  // draftStatus — the old-blob and seeds_only badge cases enter through the same seam the app restores through.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const restoredWithReport = (report: any) => {
    const seeded = clearedRestore([]);
    seeded.editor.draftStatus = {
      counts: { placed: 1, verify: 0, ambiguous: 0, absent: 0 },
      report,
    };
    const state = JSON.parse(JSON.stringify(serialize(seeded.hook, seeded.editor)));
    const result = deserialize({ schema_version: SCHEMA_VERSION, state });
    if (result.status !== "ok") throw new Error("restore failed");
    return result;
  };

  it("a seeds_only report renders the persistent badge — and suppresses the redundant no-key sweep mention", async () => {
    const user = userEvent.setup();
    mockDraft(
      draftWithReport([PLACED_SMR], healthyReport({ tail_sweep: "skipped", scope: "seeds_only" })),
    );
    const { container } = render(
      <ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: /Quick draft \(seeds only\)/ }));

    const strip = await screen.findByText(/Draft complete —/);
    expect(
      screen.getByText(/Seeds-only draft — BROAD terms \+ tail-sweep not run/),
    ).toBeInTheDocument();
    // the badge IS the skip's explanation — the quiet "sweep skipped (no key)" summary mention would
    // double-report the same skip with the WRONG reason, so it's suppressed in the seeds_only case only
    expect(strip).not.toHaveTextContent("sweep skipped");
    // a CHOSEN state, not a fault: the mid-loudness .scoped line, never the ⚑ block
    expect(container.querySelector(".wb-draft-strip.scoped")).not.toBeNull();
    expect(container.querySelector(".wb-draft-strip.loud")).toBeNull();
  });

  it("the badge persists BESIDE the ⚑ block when the seeds_only run also has gaps", async () => {
    const user = userEvent.setup();
    mockDraft(
      draftWithReport(
        [PLACED_SMR],
        healthyReport({
          tail_sweep: "skipped",
          scope: "seeds_only",
          capped_terms: ["psilocybin"],
        }),
      ),
    );
    const { container } = render(
      <ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: /Quick draft \(seeds only\)/ }));

    await screen.findByText(/completed with gaps/); // the capped term stays loud, as ever
    expect(screen.getByText(/Seeds-only draft/)).toBeInTheDocument(); // the badge holds beside it
    expect(container.querySelector(".wb-draft-strip.scoped")).not.toBeNull();
  });

  it('scope "full" renders NO badge and keeps the sweep mention — exactly today\'s strip', async () => {
    const user = userEvent.setup();
    mockDraft(draftWithReport([PLACED_SMR], healthyReport({ scope: "full" })));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    const strip = await screen.findByText(/Draft complete —/);
    expect(strip).toHaveTextContent("sweep ran"); // the summary untouched outside seeds_only
    expect(screen.queryByText(/Seeds-only draft/)).toBeNull();
  });

  it("a restored OLD blob (report without scope) is badge-free; a seeds_only one round-trips WITH the badge", () => {
    // the old blob: healthyReport() predates `scope` entirely — restored, the strip renders as today
    const old = restoredWithReport(healthyReport());
    const { unmount } = render(
      <ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} restored={old} />,
    );
    expect(screen.getByText(/Draft complete —/)).toBeInTheDocument(); // the strip itself restored
    expect(screen.queryByText(/Seeds-only draft/)).toBeNull(); // no scope → no badge (never invented)
    unmount();

    // the round-trip: a seeds_only draftStatus serialized + restored keeps its badge (no SCHEMA_VERSION bump)
    const scoped = restoredWithReport(
      healthyReport({ tail_sweep: "skipped", scope: "seeds_only" }),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} restored={scoped} />);
    expect(screen.getByText(/Seeds-only draft/)).toBeInTheDocument();
  });
});
