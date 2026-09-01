import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// CHERRY-PICK (PR-3): pick-mode — the load-mode choice (lane defaults + the operator override), the
// Recommended pile (divert / pick / send-back / re-draft merge), and the bulk "✓ sign off all picked".
// The network boundary is mocked exactly like ChainEditor.test.tsx; useChainDraft is the REAL hook.
const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  putExcl: vi.fn(async () => ({})),
  start: vi.fn(),
  produce: vi.fn(),
  edit: vi.fn(),
  recommend: vi.fn(),
  jobData: undefined as unknown,
  jobIsError: false,
}));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useResolveEtf: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  usePromoteThesis: () => ({ mutate: h.mutate, reset: vi.fn(), isPending: false, isError: false, error: null }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useStartDraft: () => ({ mutateAsync: h.start, isPending: false }),
  useDraftJobStatus: () => ({ data: h.jobData, isError: h.jobIsError }),
  useProduceTerms: () => ({ mutate: h.produce, data: undefined, isPending: false, isError: false, error: null }),
  useEditTerms: () => ({ mutate: h.edit, isPending: false, isError: false, error: null }),
  usePutExclusions: () => ({ mutateAsync: h.putExcl, isPending: false, isError: false, error: null }),
  useRecommendTiers: () => ({ mutate: h.recommend, isPending: false, isError: false, error: null }),
  useThesisRuns: () => ({ data: [], isError: false }),
  useLoadThesisRun: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { ChainEditor } from "../ChainEditor";
// the REAL session codec — the restored-pile case enters through the same seam the app restores through
import { clearedRestore, deserialize, SCHEMA_VERSION, serialize } from "../triageSession";

// An established one-name thesis (OKLO is in the saved spine at mount — the frozen Basket).
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
      conviction: null,
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

// a SIGNAL seed so the ⚡ quick-draft lane is enabled
const thesisWithTerms = {
  ...flatThesis,
  term_set: [{ term: "psilocybin", tier: "signal", authored_by: "operator_set", source: "seed" }],
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const draft = (placements: unknown[], segments: unknown[] = [{ label: "reactors", descriptor: null }]) =>
  ({ thesis_id: "t1", segments, placements }) as any;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mockDraft(result: any) {
  h.start.mockResolvedValue({ job_id: "j1", status: "running" });
  h.jobData = { job_id: "j1", status: "done", result, error: null };
}

const placed = (ticker: string, sid: string, segment = "reactors", over: Record<string, unknown> = {}) => ({
  name: `${ticker} Co`,
  ticker,
  prose: `${ticker} prose`,
  segment,
  status: "placed",
  security_id: sid,
  candidates: [],
  matched_terms: ["psilocybin"],
  ...over,
});

const VERIFY_ALKS = {
  name: "Alkermes plc",
  ticker: "ALKS",
  prose: "ketamine-adjacent CNS pipeline",
  segment: "reactors",
  status: "verify",
  security_id: "s-alks",
  candidates: [],
  matched_terms: ["ketamine"],
};

const rowOf = (ticker: string): HTMLElement =>
  screen.getByLabelText(`include ${ticker}`).closest(".nmrow") as HTMLElement;
const saveBody = () => h.mutate.mock.calls[0][0] as { basket: Record<string, unknown>[] };
const pickMode = () => screen.getByLabelText("draft load mode");

beforeEach(() => {
  h.mutate.mockReset();
  h.putExcl.mockClear();
  h.start.mockReset();
  h.produce.mockReset();
  h.edit.mockReset();
  h.recommend.mockReset();
  h.jobData = undefined;
  h.jobIsError = false;
});

describe("ChainEditor — pick-mode routing (the load-mode choice)", () => {
  it("pick-mode ON (explicit): new placed names land in the Recommended pile, the basket gains NOTHING, the established row survives", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
    mockDraft(draft([placed("SMR", "s-smr")]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.selectOptions(pickMode(), "pick"); // the explicit choice wins for the FULL lane too
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // the new name is a PENDING recommendation, not a member — the pile row, with its provenance + prose
    expect(await screen.findByLabelText("pick SMR")).toBeInTheDocument();
    expect(screen.queryByLabelText("include SMR")).not.toBeInTheDocument();
    expect(screen.getByText("SMR prose")).toBeInTheDocument();
    expect(screen.getByText(/matched psilocybin/)).toBeInTheDocument();
    // THE WIPE-TRAP COUSIN: the established member is untouched by a pick-mode load
    expect(screen.getByLabelText("include OKLO")).toBeInTheDocument();
    expect(screen.getByLabelText("include OKLO").closest(".wb-basket")).not.toBeNull();

    // Save carries the basket only — the pile is working state, never silently promoted
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO"]);
  });

  it("pick-mode OFF (the untouched FULL lane): byte-identical to today — auto-load, no pile section", async () => {
    const user = userEvent.setup();
    mockDraft(draft([placed("SMR", "s-smr")]));
    const { container } = render(
      <ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(await screen.findByLabelText("include SMR")).toBeInTheDocument(); // auto-loaded, as ever
    expect(screen.queryByLabelText("pick SMR")).not.toBeInTheDocument();
    expect(container.querySelector(".wb-recommended")).toBeNull(); // an empty pile never renders (#3)
  });

  it("the ⚡ quick lane defaults pick-mode ON (the operator-decided pairing)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([placed("SMR", "s-smr")]));
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Quick draft \(seeds only\)/ }));

    expect(h.start).toHaveBeenCalledWith({ scope: "seeds_only" }); // wire behavior untouched
    expect(await screen.findByLabelText("pick SMR")).toBeInTheDocument();
    expect(screen.queryByLabelText("include SMR")).not.toBeInTheDocument();
  });

  it("an explicit 'auto-load all' overrides the ⚡ lane default — the quick draft auto-loads", async () => {
    const user = userEvent.setup();
    mockDraft(draft([placed("SMR", "s-smr")]));
    render(<ChainEditor asof="2026-06-08" thesis={thesisWithTerms} onDone={vi.fn()} />);

    await user.selectOptions(pickMode(), "load");
    await user.click(screen.getByRole("button", { name: /Quick draft \(seeds only\)/ }));

    expect(await screen.findByLabelText("include SMR")).toBeInTheDocument();
    expect(screen.queryByLabelText("pick SMR")).not.toBeInTheDocument();
  });

  it("pick-mode re-roll/park: a NON-established drafted member takes loadDraft's existing path while new names divert", async () => {
    const user = userEvent.setup();
    // D1 in pick-mode: SMR + GEV recommended → pick BOTH (they become system_drafted members)
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev")]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.selectOptions(pickMode(), "pick");
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await user.click(await screen.findByLabelText("pick SMR"));
    await user.click(screen.getByLabelText("pick GEV"));

    // D2 (still pick-mode): re-places SMR only, adds CCJ. The picked-but-no-longer-placed GEV must PARK
    // to Discovered (loadDraft's existing rule — a stale segment is a lie), SMR re-rolls in place, the
    // established OKLO is untouched, and only the genuinely-NEW CCJ diverts to the pile.
    h.jobData = {
      job_id: "j1",
      status: "done",
      result: draft([placed("SMR", "s-smr", "reactors", { prose: "SMR v2" }), placed("CCJ", "s-ccj")]),
      error: null,
    };
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    expect(await screen.findByLabelText("pick CCJ")).toBeInTheDocument(); // the new name → the pile
    expect(screen.queryByLabelText("include CCJ")).not.toBeInTheDocument();
    expect(screen.getByLabelText("include SMR")).toBeInTheDocument(); // re-rolled member stays a member
    expect(screen.getByLabelText("thesis-fit for SMR")).toHaveValue("SMR v2"); // …with the fresh prose
    const gevChips = Array.from(rowOf("GEV").querySelectorAll(".recchip")).map((e) => e.textContent);
    expect(gevChips).toEqual(["Discovered (unsorted)"]); // parked, never dropped (#9)
    expect(screen.getByLabelText("include OKLO")).toBeInTheDocument(); // established — untouched
  });
});

describe("ChainEditor — the pile: pick ⇄ send-back (reversibility #1)", () => {
  it("pick creates the addVerify member shape (system_drafted / unsigned / surfaced_terms / the drafted segment)", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
    mockDraft(draft([placed("SMR", "s-smr")]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.selectOptions(pickMode(), "pick");
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    await user.click(await screen.findByLabelText("pick SMR"));
    expect(screen.getByLabelText("include SMR")).toBeInTheDocument(); // now a placed member
    expect(screen.queryByLabelText("pick SMR")).not.toBeInTheDocument(); // and out of the pile
    // pick = INCLUDED, not endorsed — the sign-off offer + the honest "model draft" label
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();
    expect(within(rowOf("SMR")).getByText("model draft")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.find((m) => m.ticker === "SMR")).toMatchObject({
      security_id: "s-smr",
      segment: "reactors",
      thesis_fit: "SMR prose",
      surfaced_terms: ["psilocybin"],
      authored_by: "system_drafted",
      signed_off: false,
      conviction: null,
    });
  });

  it("a multi-link recommendation is ONE pile row and picks into N membership rows (the S1 shape)", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
    mockDraft(
      draft(
        [placed("SMR", "s-smr", "reactors"), placed("SMR", "s-smr", "fuel")],
        [
          { label: "reactors", descriptor: null },
          { label: "fuel", descriptor: null },
        ],
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.selectOptions(pickMode(), "pick");
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));

    // ONE row, both recommended links as chips
    const pick = await screen.findByLabelText("pick SMR");
    expect(screen.getAllByLabelText(/^pick /)).toHaveLength(1);
    const pileRow = pick.closest(".nmrow") as HTMLElement;
    expect(Array.from(pileRow.querySelectorAll(".recchip")).map((e) => e.textContent)).toEqual([
      "reactors",
      "fuel",
    ]);

    await user.click(pick); // one pick → BOTH membership rows
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const smrRows = saveBody().basket.filter((m) => m.ticker === "SMR");
    expect(smrRows.map((m) => m.segment)).toEqual(["reactors", "fuel"]);
  });

  it("send-back restores the pile row exactly as it was and removes the member (the visible inverse)", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
    mockDraft(draft([placed("SMR", "s-smr")]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.selectOptions(pickMode(), "pick");
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await user.click(await screen.findByLabelText("pick SMR"));

    // a pile-picked row carries the pile inverse, not To-Review's
    expect(screen.queryByRole("button", { name: "send SMR back to review" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "send SMR back to recommended" }));

    expect(screen.queryByLabelText("include SMR")).not.toBeInTheDocument(); // out of the basket
    expect(screen.getByLabelText("pick SMR")).toBeInTheDocument(); // back in the pile, re-pickable
    expect(screen.getByText("SMR prose")).toBeInTheDocument(); // exactly as it was

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    expect(saveBody().basket.find((m) => m.ticker === "SMR")).toBeUndefined();
  });

  it("re-draft MERGE: a picked name never re-enters the pile; a pending row dedups/updates; an un-re-placed pending row stays visible", async () => {
    const user = userEvent.setup();
    // D1: SMR + GEV + LOTTO all recommended
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev"), placed("LOTTO", "s-lotto")]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.selectOptions(pickMode(), "pick");
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await user.click(await screen.findByLabelText("pick SMR")); // pick ONE of the three

    // D2: re-places SMR (already picked) + GEV (still pending); LOTTO is gone from the result
    h.jobData = {
      job_id: "j1",
      status: "done",
      result: draft([placed("SMR", "s-smr"), placed("GEV", "s-gev", "reactors", { prose: "GEV v2" })]),
      error: null,
    };
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByText("GEV v2"); // the pending row UPDATED to the latest recommendation

    expect(screen.queryByLabelText("pick SMR")).not.toBeInTheDocument(); // picked → never duplicated back
    expect(screen.getByLabelText("include SMR")).toBeInTheDocument(); // still a member
    expect(screen.getAllByLabelText("pick GEV")).toHaveLength(1); // deduped by security_id
    expect(screen.getByLabelText("pick LOTTO")).toBeInTheDocument(); // pending + un-re-placed → stays (#2)
  });
});

describe("ChainEditor — bulk '✓ sign off all picked' (feature 2)", () => {
  it("renders ONLY when it discriminates, stamps exactly the origin-tracked picked set, then hides", async () => {
    const user = userEvent.setup();
    // the untouched FULL lane: SMR auto-loads (draft-placed, NOT picked); ALKS sits in To-Review
    mockDraft(draft([placed("SMR", "s-smr"), VERIFY_ALKS]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("include SMR");

    // no picked member yet → the control does not render (honest loudness #3)
    expect(screen.queryByRole("button", { name: /sign off all picked/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "add ALKS" })); // the To-Review pick gesture
    await user.click(screen.getByRole("button", { name: "✓ sign off all picked (1)" }));

    // exactly the picked set: ALKS stamped; the auto-loaded SMR and the established OKLO untouched
    expect(screen.getByRole("button", { name: "withdraw sign-off ALKS" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "sign off SMR" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "sign off OKLO" })).toBeInTheDocument();
    // nothing left to stamp → the control hides again
    expect(screen.queryByRole("button", { name: /sign off all picked/ })).not.toBeInTheDocument();
  });

  it("never touches an EXCLUDED picked member (excluded wins on the ladder)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev")]));
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.selectOptions(pickMode(), "pick");
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await user.click(await screen.findByLabelText("pick SMR"));
    await user.click(screen.getByLabelText("pick GEV"));

    await user.click(screen.getByLabelText("include GEV")); // exclude one of the two picks
    await user.click(screen.getByRole("button", { name: "✓ sign off all picked (1)" }));

    expect(screen.getByRole("button", { name: "withdraw sign-off SMR" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("include GEV")); // re-include to inspect the excluded one
    expect(screen.getByRole("button", { name: "sign off GEV" })).toBeInTheDocument(); // untouched
  });

  it("co-mutates ALL of a picked name's multi-membership rows (one stamp per NAME)", async () => {
    const user = userEvent.setup();
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
    mockDraft(
      draft(
        [placed("SMR", "s-smr", "reactors"), placed("SMR", "s-smr", "fuel")],
        [
          { label: "reactors", descriptor: null },
          { label: "fuel", descriptor: null },
        ],
      ),
    );
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.selectOptions(pickMode(), "pick");
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await user.click(await screen.findByLabelText("pick SMR"));
    await user.click(screen.getByRole("button", { name: "✓ sign off all picked (1)" }));

    await user.click(screen.getByRole("button", { name: "Save chain" }));
    const smrRows = saveBody().basket.filter((m) => m.ticker === "SMR");
    expect(smrRows).toHaveLength(2);
    expect(smrRows.every((m) => m.signed_off === true)).toBe(true); // both rows flipped together
  });
});

describe("ChainEditor — the pile restores from a saved session (working state, never vanished)", () => {
  // wire a session through the REAL serialize → JSON → deserialize round-trip, as the app restores
  const restoredWith = (over: Record<string, unknown>) => {
    const seeded = clearedRestore([]);
    Object.assign(seeded.editor, over);
    const state = JSON.parse(JSON.stringify(serialize(seeded.hook, seeded.editor)));
    const result = deserialize({ schema_version: SCHEMA_VERSION, state });
    if (result.status !== "ok") throw new Error("restore failed");
    return result;
  };

  it("an unpicked pile + the explicit mode choice survive a restore; the pile renders on mount", () => {
    const restored = restoredWith({ recommended: [placed("SMR", "s-smr")], pickPref: true });
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} restored={restored} />);
    expect(screen.getByLabelText("pick SMR")).toBeInTheDocument(); // the pending recommendation survived
    expect(screen.getByLabelText("draft load mode")).toHaveValue("pick"); // the operator's choice held
  });

  it("an old blob (no cherry-pick fields) restores with an empty pile and the untouched lane default", () => {
    const seeded = clearedRestore([]);
    const state = JSON.parse(JSON.stringify(serialize(seeded.hook, seeded.editor)));
    delete state.editor.recommended;
    delete state.editor.recommendedOrigin;
    delete state.editor.pickPref;
    const result = deserialize({ schema_version: SCHEMA_VERSION, state });
    if (result.status !== "ok") throw new Error("restore failed");
    const { container } = render(
      <ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} restored={result} />,
    );
    expect(container.querySelector(".wb-recommended")).toBeNull(); // no pile invented
    expect(screen.getByLabelText("draft load mode")).toHaveValue("auto"); // no choice invented
  });
});
