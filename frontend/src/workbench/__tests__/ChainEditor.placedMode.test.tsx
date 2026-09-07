import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, type MockInstance, vi } from "vitest";

// THE PLACED MODE (Research ⇄ Pick) — the PLACED section's gesture toggle, through the editor UI: the
// polarity in both modes, THE ROUND-TRIP (a pick Save → restore through the real codec → pick more), the
// two resets (sign-off preserved, the saved Basket never emptied, persisted NOs restored), the arrival
// rules, the cross-mode tag, the pick-mode bulk bar, and the #9 spine. The network boundary is mocked
// exactly like ChainEditor.cherrypick.test.tsx; useChainDraft + the session codec are REAL.
const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  putExcl: vi.fn(async () => ({})),
  // the session PUT — one shared spy so the Save-time DIRECT write (the debounce-race fix) is assertable
  putSession: vi.fn(),
  start: vi.fn(),
  produce: vi.fn(),
  edit: vi.fn(),
  recommend: vi.fn(),
  jobData: undefined as unknown,
  jobIsError: false,
}));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: h.putSession, isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useResolveEtf: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  usePromoteThesis: () => ({ mutate: h.mutate, reset: vi.fn(), isPending: false, isError: false, error: null }),
  // the resolver typeahead (the hand-add): any non-empty query surfaces one match
  useResolveSecurities: (q: string) => ({
    data: q?.trim() ? [{ security_id: "s-ccj", ticker: "CCJ", name: "Cameco", cik: "0001" }] : [],
    isFetching: false,
  }),
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
// the REAL session codec — the round-trip restores through the same seam the app restores through
import { deserialize, SCHEMA_VERSION } from "../triageSession";

const est = (ticker: string, sid: string, over: Record<string, unknown> = {}) => ({
  ticker,
  role: "—",
  security_id: sid,
  segment: null,
  thesis_fit: null,
  conviction: null,
  authored_by: "system_drafted",
  signed_off: false,
  ...over,
});

// a NEW thesis — nothing established, no persisted NOs
const emptyThesis = {
  id: "t1",
  name: "Nuclear",
  narrative: "AI power.",
  ticker: null,
  segments: [] as { label: string; descriptor: string | null }[],
  basket: [] as Record<string, unknown>[],
  evidence: [],
  catalysts: [],
  kill_criteria: [],
  position: null,
  term_set: [] as { term: string; tier: string; authored_by: string; source: string | null }[],
  exclusions: [] as { security_id: string; ticker: string | null; reason: string | null }[],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;
// an established one-name thesis (OKLO is in the saved spine at mount — the frozen Basket)
const flatThesis = { ...emptyThesis, basket: [est("OKLO", "s-oklo")] };

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

const box = (ticker: string) => screen.getByLabelText(`include ${ticker}`);
const rowOf = (ticker: string): HTMLElement => box(ticker).closest(".nmrow") as HTMLElement;
const modeBtn = (name: "Research" | "Pick") => screen.getByRole("button", { name });
const draftBtn = () => screen.getByRole("button", { name: /Draft from narrative/ });
const saveBtn = () => screen.getByRole("button", { name: "Save chain" });
type Body = { basket: Record<string, unknown>[]; segments: unknown[] };
const saveBody = (): Body => h.mutate.mock.calls[0][0] as Body;
const exclBody = () =>
  h.putExcl.mock.calls[0][0] as { security_id: string; ticker: string | null; reason: string | null }[];
const withOnSuccess = () =>
  h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
// kick off a draft in the FULL lane (auto-load — the untouched load mode) and wait for a placed name to land
const draftAndWait = async (user: ReturnType<typeof userEvent.setup>, ticker: string) => {
  await user.click(draftBtn());
  await screen.findByLabelText(`include ${ticker}`);
};

let confirmSpy: MockInstance<(message?: string) => boolean>;
beforeEach(() => {
  h.mutate.mockReset();
  h.putExcl.mockClear();
  h.putSession.mockClear();
  h.start.mockReset();
  h.produce.mockReset();
  h.edit.mockReset();
  h.recommend.mockReset();
  h.jobData = undefined;
  h.jobIsError = false;
  confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => confirmSpy.mockRestore());

describe("ChainEditor — placed mode: the polarity", () => {
  it("research (the default): checked + open; unchecking collapses to the excluded stub (greyed + struck, why-input)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev")]));
    render(<ChainEditor asof="2026-06-08" thesis={emptyThesis} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");

    expect(modeBtn("Research")).toHaveAttribute("aria-pressed", "true");
    expect(modeBtn("Pick")).toHaveAttribute("aria-pressed", "false");
    expect(box("SMR")).toBeChecked();
    expect(rowOf("SMR")).not.toHaveClass("excluded");
    expect(rowOf("SMR")).not.toHaveClass("picked");
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument(); // open
    expect(screen.getByText("· 2 of 2 included")).toBeInTheDocument();

    await user.click(box("SMR"));
    expect(rowOf("SMR")).toHaveClass("excluded"); // today's stub, byte-identical
    expect(rowOf("SMR")).not.toHaveClass("picked");
    expect(screen.getByLabelText("why excluded SMR")).toBeInTheDocument();
    expect(screen.queryByLabelText("thesis-fit for SMR")).not.toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled(); // no mode switch, no confirm
  });

  it("pick: un-picked + open; checking picks + collapses to the picked stub (no strikethrough class, a `picked` tag, sign-off still reachable)", async () => {
    const user = userEvent.setup();
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev")]));
    render(<ChainEditor asof="2026-06-08" thesis={emptyThesis} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");

    await user.click(modeBtn("Pick"));
    expect(confirmSpy).toHaveBeenCalledTimes(1); // both working names would flip → confirmed
    expect(modeBtn("Pick")).toHaveAttribute("aria-pressed", "true");
    expect(box("SMR")).not.toBeChecked(); // available
    expect(rowOf("SMR")).not.toHaveClass("excluded");
    expect(rowOf("SMR")).not.toHaveClass("picked");
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument(); // open
    expect(screen.getByText("· 0 of 2 picked")).toBeInTheDocument();

    await user.click(box("SMR")); // PICK it
    expect(box("SMR")).toBeChecked();
    expect(rowOf("SMR")).toHaveClass("picked");
    expect(rowOf("SMR")).not.toHaveClass("excluded"); // never struck — it's IN
    expect(within(rowOf("SMR")).getByText("picked", { selector: ".wb-pick-tag" })).toBeInTheDocument();
    expect(screen.queryByLabelText("thesis-fit for SMR")).not.toBeInTheDocument(); // collapsed
    expect(screen.queryByLabelText("why excluded SMR")).not.toBeInTheDocument(); // pick writes no NO — nothing to explain
    expect(screen.getByText("· 1 of 2 picked")).toBeInTheDocument();

    // the stub keeps its actions: sign off / withdraw on the collapsed row (#1)
    await user.click(within(rowOf("SMR")).getByRole("button", { name: "sign off SMR" }));
    expect(within(rowOf("SMR")).getByRole("button", { name: "withdraw sign-off SMR" })).toBeInTheDocument();
    expect(rowOf("SMR")).toHaveClass("picked");
    // RULING 4: un-picking a signed-off name is allowed — it opens, still signed off
    await user.click(box("SMR"));
    expect(box("SMR")).not.toBeChecked();
    expect(rowOf("SMR")).not.toHaveClass("picked");
    expect(screen.getByRole("button", { name: "withdraw sign-off SMR" })).toBeInTheDocument();
  });
});

describe("ChainEditor — placed mode: THE ROUND-TRIP", () => {
  it("pick 2 of 3 → Save persists the 2, writes NO exclusion for the un-picked, writes the session directly → restore through the real codec → pick mode, 2 collapsed, 1 open → pick it → Save = 3", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev"), placed("CCJ", "s-ccj")]));
    const first = render(<ChainEditor asof="2026-06-08" thesis={emptyThesis} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");
    await user.click(modeBtn("Pick"));
    await user.click(box("SMR"));
    await user.click(box("GEV"));

    const sessionWritesBefore = h.putSession.mock.calls.length;
    await user.click(saveBtn());
    // the promote = exactly the picked rows; the exclusions PUT has NO entry for the un-picked CCJ
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["SMR", "GEV"]);
    expect(exclBody()).toEqual([]);
    // RULING 6: the session blob is written DIRECTLY at Save, before the promote, carrying the picks
    expect(h.putSession.mock.calls.length).toBeGreaterThan(sessionWritesBefore);
    const written = h.putSession.mock.calls[sessionWritesBefore][0] as {
      schema_version: number;
      state: { hook: { selected: string[]; placedMode: string } };
    };
    expect(h.putSession.mock.invocationCallOrder[sessionWritesBefore]).toBeLessThan(
      h.mutate.mock.invocationCallOrder[0],
    );
    expect(written.schema_version).toBe(SCHEMA_VERSION);
    expect(written.state.hook.placedMode).toBe("pick");
    expect([...written.state.hook.selected].sort()).toEqual(["s-gev", "s-smr"]);

    // RESTORE — the spine now carries the saved 2 (the promote body); the session restores through the codec
    const restored = deserialize({
      schema_version: written.schema_version,
      state: JSON.parse(JSON.stringify(written.state)),
    });
    if (restored.status !== "ok") throw new Error("restore failed");
    const savedThesis = { ...emptyThesis, basket: saveBody().basket, segments: saveBody().segments };
    first.unmount();
    h.mutate.mockClear();
    h.putExcl.mockClear();
    render(<ChainEditor asof="2026-06-08" thesis={savedThesis} onDone={vi.fn()} restored={restored} />);

    expect(modeBtn("Pick")).toHaveAttribute("aria-pressed", "true"); // the mode survived — no reset
    expect(rowOf("SMR")).toHaveClass("picked"); // the 2 saved names: picked, collapsed (in the Basket panel)
    expect(rowOf("GEV")).toHaveClass("picked");
    expect(rowOf("SMR").closest(".wb-basket")).not.toBeNull();
    expect(box("CCJ")).not.toBeChecked(); // the un-picked one: still available, open
    expect(rowOf("CCJ")).not.toHaveClass("picked");
    expect(rowOf("CCJ")).not.toHaveClass("excluded");
    expect(screen.getByLabelText("thesis-fit for CCJ")).toBeInTheDocument();
    expect(confirmSpy).toHaveBeenCalledTimes(1); // only the first mount's mode switch ever asked

    await user.click(box("CCJ"));
    await user.click(saveBtn());
    expect(saveBody().basket.map((m) => m.ticker).sort()).toEqual(["CCJ", "GEV", "SMR"]);
    expect(exclBody()).toEqual([]);
  });

  it("a research Save writes the session directly too (mode-agnostic — timing only, never semantics)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    const before = h.putSession.mock.calls.length;
    await user.click(saveBtn());
    expect(h.putSession.mock.calls.length).toBeGreaterThan(before);
    const written = h.putSession.mock.calls[before][0] as { state: { hook: { placedMode: string; excluded: string[] } } };
    expect(written.state.hook.placedMode).toBe("research");
    expect(written.state.hook.excluded).toEqual([]);
    expect(h.putSession.mock.invocationCallOrder[before]).toBeLessThan(h.mutate.mock.invocationCallOrder[0]);
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO"]); // the research Save itself, untouched
  });
});

describe("ChainEditor — placed mode: the toggle is a reset (sign-off preserved)", () => {
  it("→ Research: every working name is re-checked, a persisted NO returns pre-greyed (with its why), sign-off preserved", async () => {
    const user = userEvent.setup();
    const t = { ...emptyThesis, exclusions: [{ security_id: "s-ccj", ticker: "CCJ", reason: "old no" }] };
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev"), placed("CCJ", "s-ccj")]));
    render(<ChainEditor asof="2026-06-08" thesis={t} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");
    expect(box("CCJ")).not.toBeChecked(); // research: the persisted NO arrives pre-greyed

    await user.click(modeBtn("Pick")); // SMR + GEV flip (CCJ: greyed → un-picked reads the same)
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "sign off GEV" })); // endorse on the open row ⇒ picked
    expect(box("GEV")).toBeChecked();
    expect(rowOf("GEV")).toHaveClass("picked");

    await user.click(modeBtn("Research")); // SMR flips back (GEV signed → checked either way; CCJ greyed either way)
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(modeBtn("Research")).toHaveAttribute("aria-pressed", "true");
    expect(box("SMR")).toBeChecked();
    expect(rowOf("SMR")).not.toHaveClass("picked");
    expect(rowOf("SMR")).not.toHaveClass("excluded");
    expect(box("GEV")).toBeChecked();
    expect(screen.getByRole("button", { name: "withdraw sign-off GEV" })).toBeInTheDocument(); // preserved
    expect(box("CCJ")).not.toBeChecked(); // the persisted NO restored — never a literal all-check
    expect(rowOf("CCJ")).toHaveClass("excluded");
    expect(screen.getByLabelText("why excluded CCJ")).toHaveValue("old no");
  });

  it("→ Pick on an established thesis: the Basket panel keeps its kept names (collapsed), a demoted established name is un-picked, a signed-off working name stays checked", async () => {
    const user = userEvent.setup();
    const twoEst = { ...emptyThesis, basket: [est("OKLO", "s-oklo"), est("CCJ", "s-ccj")] };
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev")]));
    render(<ChainEditor asof="2026-06-08" thesis={twoEst} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");
    await user.click(screen.getByRole("button", { name: "sign off GEV" }));
    await user.click(box("CCJ")); // demote an established name ("send down")
    expect(screen.getByText("· 1 of 2 kept")).toBeInTheDocument();

    await user.click(modeBtn("Pick")); // only SMR flips → confirmed once
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    // the saved Basket's kept name stays checked — mode entry never empties the saved basket
    expect(box("OKLO")).toBeChecked();
    expect(rowOf("OKLO").closest(".wb-basket")).not.toBeNull();
    expect(rowOf("OKLO")).toHaveClass("picked"); // panel rows follow pick polarity (collapsed)
    expect(screen.getByText("· 1 of 2 kept")).toBeInTheDocument();
    // the demoted established name: un-picked, open, in the working list — available, not excluded
    expect(box("CCJ")).not.toBeChecked();
    expect(rowOf("CCJ").closest(".wb-basket")).toBeNull();
    expect(rowOf("CCJ")).not.toHaveClass("excluded");
    expect(rowOf("CCJ")).not.toHaveClass("picked");
    // the undecided new name: un-picked + open; the signed-off new name: checked + collapsed
    expect(box("SMR")).not.toBeChecked();
    expect(box("GEV")).toBeChecked();
    expect(rowOf("GEV")).toHaveClass("picked");
    expect(within(rowOf("GEV")).getByRole("button", { name: "withdraw sign-off GEV" })).toBeInTheDocument();
    expect(screen.getByText(/uncheck to un-pick/)).toBeInTheDocument(); // the Basket header copy follows the mode
  });

  it("the confirm is CONDITIONAL (nothing changes → nothing asked); cancel keeps the mode; the active mode is a no-op", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);

    await user.click(modeBtn("Pick")); // OKLO is kept either way → no name flips
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(modeBtn("Pick")).toHaveAttribute("aria-pressed", "true");
    expect(box("OKLO")).toBeChecked();
    await user.click(modeBtn("Pick")); // the active mode: a no-op
    expect(confirmSpy).not.toHaveBeenCalled();
    await user.click(modeBtn("Research")); // back: still nothing flips
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(modeBtn("Research")).toHaveAttribute("aria-pressed", "true");

    mockDraft(draft([placed("SMR", "s-smr")]));
    await draftAndWait(user, "SMR"); // a working name arrives included (research)
    confirmSpy.mockReturnValue(false); // the operator cancels
    await user.click(modeBtn("Pick"));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(modeBtn("Research")).toHaveAttribute("aria-pressed", "true"); // mode unchanged
    expect(box("SMR")).toBeChecked();
  });
});

describe("ChainEditor — placed mode: arrivals", () => {
  it("a bulk draft arrival is undecided (open); explicit arrivals — a To-Review add, a hand-add, a pile pick — enter picked (collapsed)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    render(<ChainEditor asof="2026-06-08" thesis={flatThesis} onDone={vi.fn()} />);
    await user.click(modeBtn("Pick")); // no working names → no confirm
    expect(confirmSpy).not.toHaveBeenCalled();

    // the bulk arrival (FULL lane, auto-load): SMR lands open + un-picked; ALKS sits in To-Review
    mockDraft(draft([placed("SMR", "s-smr"), VERIFY_ALKS]));
    await draftAndWait(user, "SMR");
    expect(box("SMR")).not.toBeChecked();
    expect(rowOf("SMR")).not.toHaveClass("picked");
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument();

    // the To-Review add → picked + collapsed
    await user.click(screen.getByRole("checkbox", { name: "add ALKS" }));
    expect(box("ALKS")).toBeChecked();
    expect(rowOf("ALKS")).toHaveClass("picked");
    expect(within(rowOf("ALKS")).getByRole("button", { name: "send ALKS back to review" })).toBeInTheDocument(); // the inverse stays reachable

    // the hand-add (auto-signed-off) → picked + collapsed
    await user.type(screen.getByLabelText("search securities"), "cam");
    await user.click(screen.getByRole("button", { name: /CCJ/ }));
    await user.click(screen.getByRole("button", { name: "add to basket" }));
    expect(box("CCJ")).toBeChecked();
    expect(rowOf("CCJ")).toHaveClass("picked");
    expect(within(rowOf("CCJ")).getByRole("button", { name: "withdraw sign-off CCJ" })).toBeInTheDocument();

    // the Recommended-pile pick (the LOAD mode "start empty — pick keepers") → picked + collapsed
    await user.selectOptions(screen.getByLabelText("draft load mode"), "pick");
    h.jobData = { job_id: "j1", status: "done", result: draft([placed("GEV", "s-gev")]), error: null };
    await user.click(draftBtn());
    await user.click(await screen.findByLabelText("pick GEV"));
    expect(box("GEV")).toBeChecked();
    expect(rowOf("GEV")).toHaveClass("picked");
    expect(within(rowOf("GEV")).getByRole("button", { name: "send GEV back to recommended" })).toBeInTheDocument();

    // the bulk sign-off targets selected − established − signed_off: ALKS + GEV (CCJ is already endorsed)
    expect(screen.getByRole("button", { name: "✓ sign off all picked (2)" })).toBeInTheDocument();

    await user.click(saveBtn());
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["OKLO", "ALKS", "CCJ", "GEV"]); // SMR un-picked → not saved
    expect(exclBody()).toEqual([]); // …and NOT excluded either
  });
});

describe("ChainEditor — placed mode: the cross-mode tag", () => {
  const withNo = {
    ...emptyThesis,
    exclusions: [{ security_id: "s-smr", ticker: "SMR", reason: "junk" }],
  };

  it("a persisted research NO renders open + un-picked with the tag; leaving it un-picked carries the NO forward verbatim", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev")]));
    render(<ChainEditor asof="2026-06-08" thesis={withNo} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");
    expect(rowOf("SMR")).toHaveClass("excluded"); // research: pre-greyed

    await user.click(modeBtn("Pick")); // only GEV flips (SMR: greyed → un-picked reads the same)
    expect(box("SMR")).not.toBeChecked();
    expect(rowOf("SMR")).not.toHaveClass("excluded"); // never pre-greyed into a mode that has no exclusions
    expect(rowOf("SMR")).not.toHaveClass("picked");
    expect(within(rowOf("SMR")).getByText("excluded in research")).toBeInTheDocument(); // keep-visible (#2)
    expect(screen.getByLabelText("thesis-fit for SMR")).toBeInTheDocument(); // open
    expect(within(rowOf("GEV")).queryByText("excluded in research")).not.toBeInTheDocument();

    await user.click(box("GEV"));
    await user.click(saveBtn());
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["GEV"]);
    expect(exclBody()).toEqual([{ security_id: "s-smr", ticker: "SMR", reason: "junk" }]); // verbatim, no new NO
  });

  it("picking it withdraws the NO on Save (the tag leaves with the open row)", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev")]));
    render(<ChainEditor asof="2026-06-08" thesis={withNo} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");
    await user.click(modeBtn("Pick"));

    await user.click(box("SMR")); // PICK the persisted-NO name
    expect(rowOf("SMR")).toHaveClass("picked");
    expect(within(rowOf("SMR")).queryByText("excluded in research")).not.toBeInTheDocument();
    await user.click(saveBtn());
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["SMR"]);
    expect(exclBody()).toEqual([]); // the NO withdrawn; GEV un-picked → no NO written for it
  });
});

describe("ChainEditor — placed mode: the bulk bar", () => {
  it("in Pick the research-only prunes don't render (incl. the low-quality exclude-all); Export + the mode note stay; they return in Research", async () => {
    const user = userEvent.setup();
    // BLK: model-flagged off-thesis AND a junk tell (the "blackrock"+"trust" name pair) → the low-quality lens
    mockDraft(
      draft([
        placed("SMR", "s-smr"),
        placed("BLK", "s-blk", "reactors", { name: "BlackRock Trust", off_thesis: true }),
      ]),
    );
    render(<ChainEditor asof="2026-06-08" thesis={emptyThesis} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");
    await user.click(screen.getByRole("button", { name: "toggle Placed, low quality" })); // open the drawer

    // research: the prunes render
    expect(screen.getByRole("button", { name: "include all new" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "exclude all new" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "clear not signed-off" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "exclude all 1" })).toBeInTheDocument();
    expect(screen.getByText("Only included names are saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "export 2 included names" })).toBeInTheDocument();

    await user.click(modeBtn("Pick"));
    expect(screen.queryByRole("button", { name: "include all new" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "exclude all new" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "clear not signed-off" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "exclude all 1" })).not.toBeInTheDocument();
    expect(
      screen.getByText("Only picked names are saved — un-picked names stay available, never excluded."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "export 0 picked names" })).toBeInTheDocument(); // mode-aware, still there
    await user.click(box("SMR"));
    expect(screen.getByRole("button", { name: "export 1 picked names" })).toBeInTheDocument();

    await user.click(modeBtn("Research"));
    expect(screen.getByRole("button", { name: "include all new" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "exclude all 1" })).toBeInTheDocument();
    expect(screen.getByText("Only included names are saved.")).toBeInTheDocument();
  });
});

describe("ChainEditor — placed mode: the #9 spine", () => {
  it("a filtered-out PICKED name still saves (the view hides, only the pick decides); the include filter's labels follow the mode", async () => {
    const user = userEvent.setup();
    withOnSuccess();
    mockDraft(draft([placed("SMR", "s-smr"), placed("GEV", "s-gev"), placed("CCJ", "s-ccj")]));
    render(<ChainEditor asof="2026-06-08" thesis={emptyThesis} onDone={vi.fn()} />);
    await draftAndWait(user, "SMR");
    await user.click(modeBtn("Pick"));
    await user.click(box("SMR"));
    await user.click(box("GEV"));

    const filter = screen.getByLabelText("filter by include");
    const labels = Array.from((filter as HTMLSelectElement).options).map((o) => o.textContent);
    expect(labels).toEqual(["all", "picked", "not picked"]); // values unchanged, labels mode-aware
    await user.selectOptions(filter, "excluded"); // "not picked" — hides the two picks from VIEW
    expect(screen.queryByLabelText("include SMR")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("include GEV")).not.toBeInTheDocument();
    expect(box("CCJ")).toBeInTheDocument();

    await user.click(saveBtn());
    expect(saveBody().basket.map((m) => m.ticker)).toEqual(["SMR", "GEV"]); // hidden, still saved
    expect(exclBody()).toEqual([]);
  });
});
