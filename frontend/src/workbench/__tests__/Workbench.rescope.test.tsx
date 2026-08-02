import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// S3 (re-scope) — the maintenance loop, end to end across Workbench + the REAL ChainEditor it mounts:
//   1. the stale-session age-gate (an old autosave stops silently driving the editor — choice, never
//      auto-delete) and the fresh-session resumed badge;
//   2. the ⟳ Re-scope action: confirm → session delete → a fresh-from-THESIS remount (the WHOLE saved
//      Basket frozen, the candidate pile empty) → exactly ONE auto-fired draft kick-off;
//   3. post-draft: old buckets gone, fresh verify pile in, established rows frozen, `matched` repopulated;
//   4. #9: a saved member matching NO current term survives the re-scope and rides the Save payload.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const segments = [{ label: "reactors", descriptor: null }];
  // the SAVED spine: one operator-set member + one still-drafted member — "the whole Basket" means BOTH.
  // Each carries S1's persisted surfaced_terms (the frozen at-entry provenance S2 renders).
  const basket = [
    {
      ticker: "OKLO",
      role: "—",
      archetype: null,
      security_id: "s-oklo",
      segment: "reactors",
      thesis_fit: "F0",
      conviction: null,
      surfaced_terms: ["psilocybin"],
      authored_by: "operator_set",
    },
    {
      ticker: "GEV",
      role: "—",
      archetype: null,
      security_id: "s-gev",
      segment: "reactors",
      thesis_fit: null,
      conviction: null,
      surfaced_terms: ["uranium"],
      authored_by: "system_drafted",
    },
  ];
  const member = (sid: string, ticker: string) => ({
    security_id: sid,
    ticker,
    archetype: null,
    segment: "reactors",
    purity: fig(null, null),
    runway: fig(null, null),
    catalysts: fig(null, null),
    dilution: fig(null, null),
    market_cap: fig(null, null),
    fit: "",
  });
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
  const scored = {
    thesis_id: "t-nuke",
    asof: "2026-06-08",
    segments,
    members: [member("s-oklo", "OKLO"), member("s-gev", "GEV")],
  };
  return { thesis, scored };
});

const s = vi.hoisted(() => ({
  // the triage-session GET's data — tests point it at an envelope (stale / fresh) or {session:null}
  sessionData: { session: null } as unknown,
  del: vi.fn(),
  start: vi.fn(),
  jobData: undefined as unknown,
  promote: vi.fn(),
  putExcl: vi.fn(async () => ({})),
}));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: s.sessionData, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: s.del }),
  // the surface-ETF sleeve input (below AddName in the ChainEditor) — inert here
  useResolveEtf: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  // #7: the exclusion PUT rides every ChainEditor Save (mutateAsync must resolve)
  usePutExclusions: () => ({ mutateAsync: s.putExcl, isPending: false, isError: false, error: null }),
  useTheses: () => ({
    data: [{ id: "t-nuke", name: "Small modular nuclear", ticker: null, basket_size: 2, narrative: "x" }],
  }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: s.promote,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  // the section-data runner + the per-name price pull (inert here; their own suites cover them)
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  // the drafter job: kick-off records via s.start; the poll reads s.jobData (same idiom as the editor suite)
  useStartDraft: () => ({ mutateAsync: s.start, isPending: false }),
  useDraftJobStatus: () => ({ data: s.jobData, isError: false }),
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

const daysAgo = (d: number) => new Date(Date.now() - d * 24 * 60 * 60 * 1000).toISOString();

// a member as it rides the SESSION blob's draft.basket (the autosaved working state)
const smem = (ticker: string, sid: string, over: Record<string, unknown> = {}) => ({
  ticker,
  role: "—",
  archetype: null,
  security_id: sid,
  segment: "reactors",
  thesis_fit: null,
  conviction: null,
  surfaced_terms: [],
  authored_by: "system_drafted",
  ...over,
});

// a To-Review (verify) placement, for the session blob's editor buckets and for draft results
const verifyP = (name: string, ticker: string, sid: string, terms: string[]) => ({
  name,
  ticker,
  prose: "",
  segment: "reactors",
  status: "verify",
  security_id: sid,
  candidates: [],
  matched_terms: terms,
});

// a stored session envelope (schema v1) — `state` leans on deserialize's per-field defaults, so only the
// cells a test asserts on need to be present (draft basket + the To-Review verify bucket)
const envelope = (updatedAt: string, basket: unknown[], verify: unknown[] = []) => ({
  session: {
    thesis_id: "t-nuke",
    schema_version: 1,
    updated_at: updatedAt,
    state: {
      hook: {
        draft: { segments: [{ label: "reactors", descriptor: null }], basket },
        excluded: [],
        reasons: {},
        reasonsDirty: false,
      },
      editor: { verify },
    },
  },
});

// the re-scoped draft's result: OKLO re-matches under the CURRENT (refined) terms with a fresh segment +
// prose (which must NOT land — it's established), one fresh To-Review candidate. GEV matches NOTHING (#9).
const rescopeResult = {
  thesis_id: "t-nuke",
  segments: [{ label: "reactors", descriptor: null }],
  placements: [
    {
      name: "Oklo Inc.",
      ticker: "OKLO",
      prose: "P-NEW",
      segment: "smr-new",
      status: "placed",
      security_id: "s-oklo",
      candidates: [],
      matched_terms: ["mdma"],
    },
    verifyP("Fresh Verify Co", "FVC", "s-fvc", ["mdma"]),
  ],
};

beforeEach(() => {
  s.del.mockReset();
  s.start.mockReset();
  s.promote.mockReset();
  s.putExcl.mockClear();
  s.sessionData = { session: null };
  s.jobData = undefined;
  // the delete nulls the restore data (the hook's cache-null behavior) and reports success — so the
  // parent's onSuccess remount seeds fresh, exactly like the real mutation
  s.del.mockImplementation((_vars?: unknown, opts?: { onSuccess?: () => void }) => {
    s.sessionData = { session: null };
    opts?.onSuccess?.();
  });
});

describe("Workbench — S3: the stale-session age-gate + the resumed badge", () => {
  it("a STALE autosave (>3d) gates the mount behind a choice — never a silent restore, never an auto-delete", async () => {
    const user = userEvent.setup();
    s.sessionData = envelope(daysAgo(15), [smem("OKLO", "s-oklo"), smem("STALE", "s-stale")]);
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));

    // the choice panel renders; the editor did NOT silently mount off the old prune
    expect(screen.getByText(/Autosaved working session from/)).toBeInTheDocument();
    expect(screen.getByText(/15 days old/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save chain" })).not.toBeInTheDocument();
    expect(s.del).not.toHaveBeenCalled(); // expiry never deletes — only the operator's click below does

    // "Start from the saved Basket" → the session is discarded and the editor mounts from the THESIS
    await user.click(screen.getByRole("button", { name: "Start from the saved Basket" }));
    expect(s.del).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Save chain" })).toBeInTheDocument();
    // the FULL saved Basket is up top (both spine members, frozen); the stale candidate is nowhere
    expect(screen.getByLabelText("segment for OKLO").closest(".wb-basket")).not.toBeNull();
    expect(screen.getByLabelText("segment for GEV").closest(".wb-basket")).not.toBeNull();
    expect(screen.queryByLabelText("segment for STALE")).not.toBeInTheDocument();
  });

  it("Resume mounts the stale session anyway (the operator's call) — badged as session-driven", async () => {
    const user = userEvent.setup();
    s.sessionData = envelope(daysAgo(15), [smem("OKLO", "s-oklo"), smem("STALE", "s-stale")]);
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));

    await user.click(screen.getByRole("button", { name: "Resume" }));
    expect(s.del).not.toHaveBeenCalled(); // resuming destroys nothing
    // the restored prune is live (its candidate renders) and the badge says the state is session-driven
    expect(await screen.findByLabelText("segment for STALE")).toBeInTheDocument();
    expect(screen.getByText("resumed autosave · 15d ago")).toBeInTheDocument();
  });

  it("a FRESH autosave (<3d) restores silently — the resumed badge is the visible session-driven tell", async () => {
    const user = userEvent.setup();
    s.sessionData = envelope(daysAgo(1), [smem("OKLO", "s-oklo"), smem("ZZZ", "s-zzz")]);
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));

    // no gate: the editor mounted straight off the session (today's behavior below the threshold)
    expect(screen.queryByText(/Autosaved working session from/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("segment for ZZZ")).toBeInTheDocument();
    // …and the session predates GEV, so the spine member is absent from the editor — EXACTLY the
    // 159-vs-160 state the badge exists to make visible
    expect(screen.queryByLabelText("segment for GEV")).not.toBeInTheDocument();
    expect(screen.getByText("resumed autosave · 1d ago")).toBeInTheDocument();
  });
});

describe("Workbench — S3: the ⟳ Re-scope action", () => {
  it("confirm → session delete → a fresh-from-THESIS remount (whole Basket frozen, pile empty) → ONE draft kick-off", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    s.sessionData = envelope(
      daysAgo(1),
      [smem("OKLO", "s-oklo"), smem("ZZZ", "s-zzz")],
      [verifyP("Old Verify Co", "OVC", "s-ovc", ["x"])],
    );
    s.start.mockResolvedValue({ job_id: "j1", status: "running" });
    s.jobData = undefined; // the kicked-off draft stays pending — we assert the remounted SEED state
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));
    expect(screen.getByLabelText("segment for ZZZ")).toBeInTheDocument(); // the stale candidate pile is live

    await user.click(screen.getByRole("button", { name: "⟳ Re-scope" }));
    // the confirm names the inverse and the kept-frozen Basket (reversibility said out loud)
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(String(confirmSpy.mock.calls[0][0])).toMatch(/saved Basket \(2 names\) is kept frozen/);
    expect(s.del).toHaveBeenCalledTimes(1); // the autosaved prune is the ONLY thing discarded

    // the remount seeds from the THESIS: the WHOLE saved Basket (operator_set AND system_drafted)
    // freezes into the Basket panel; the working pile + To-Review buckets start empty
    const gev = await screen.findByLabelText("segment for GEV");
    expect(gev.closest(".wb-basket")).not.toBeNull();
    expect(screen.getByLabelText("segment for OKLO").closest(".wb-basket")).not.toBeNull();
    expect(screen.queryByLabelText("segment for ZZZ")).not.toBeInTheDocument();
    expect(screen.queryByText("Old Verify Co")).not.toBeInTheDocument();
    expect(screen.getByText(/no new names — draft from the narrative/)).toBeInTheDocument();
    expect(screen.queryByText(/resumed autosave/)).not.toBeInTheDocument(); // spine-seeded, not a restore
    // …and the discovery refresh kicked off EXACTLY ONCE
    await waitFor(() => expect(s.start).toHaveBeenCalledTimes(1));
    confirmSpy.mockRestore();
  });

  it("a cancelled confirm changes NOTHING — no delete, no remount, no draft", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    s.sessionData = envelope(daysAgo(1), [smem("OKLO", "s-oklo"), smem("ZZZ", "s-zzz")]);
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));

    await user.click(screen.getByRole("button", { name: "⟳ Re-scope" }));
    expect(s.del).not.toHaveBeenCalled();
    expect(s.start).not.toHaveBeenCalled();
    expect(screen.getByLabelText("segment for ZZZ")).toBeInTheDocument(); // the prune is untouched
    confirmSpy.mockRestore();
  });

  it("post-draft: old buckets gone, fresh verify pile in, established rows FROZEN, matched repopulates the also-now diff", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    s.sessionData = envelope(
      daysAgo(1),
      [smem("OKLO", "s-oklo"), smem("ZZZ", "s-zzz")],
      [verifyP("Old Verify Co", "OVC", "s-ovc", ["x"])],
    );
    s.start.mockResolvedValue({ job_id: "j1", status: "running" });
    s.jobData = { job_id: "j1", status: "done", result: rescopeResult, error: null };
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));
    await user.click(screen.getByRole("button", { name: "⟳ Re-scope" }));

    // the fresh candidate pile arrives (To Review); the old buckets never re-appear
    expect(await screen.findByText("Fresh Verify Co")).toBeInTheDocument();
    expect(screen.queryByText("Old Verify Co")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("segment for ZZZ")).not.toBeInTheDocument();

    // the established row is byte-identical to the SPINE — the re-match's fresh segment/prose never landed
    expect((screen.getByLabelText("segment for OKLO") as HTMLSelectElement).value).toBe("reactors");
    expect(screen.getByLabelText("thesis-fit for OKLO")).toHaveValue("F0");
    expect(screen.getByLabelText("segment for OKLO").closest(".wb-basket")).not.toBeNull();
    // the frozen seed-term record is intact, and the repopulated `matched` feeds S2's also-now diff
    expect(screen.getByText("⚓ seeded by: psilocybin")).toBeInTheDocument();
    expect(screen.getByText("+ also matches now: mdma")).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("#9: a saved Basket member matching NO current term survives the re-scope AND rides the Save payload", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    // the autosaved prune predates GEV entirely — the re-scope must still start from the SAVED Basket
    s.sessionData = envelope(daysAgo(1), [smem("OKLO", "s-oklo")]);
    s.start.mockResolvedValue({ job_id: "j1", status: "running" });
    s.jobData = { job_id: "j1", status: "done", result: rescopeResult, error: null }; // GEV matches nothing
    renderWb();
    await user.click(screen.getByRole("button", { name: /edit the chain/i }));
    await user.click(screen.getByRole("button", { name: "⟳ Re-scope" }));
    await screen.findByText("Fresh Verify Co"); // the re-scoped draft landed

    // GEV — system_drafted, zero current-term matches, absent from the draft — is STILL in the frozen Basket
    expect(screen.getByLabelText("segment for GEV").closest(".wb-basket")).not.toBeNull();
    expect(screen.getByText("⚓ seeded by: uranium")).toBeInTheDocument(); // its frozen provenance intact

    // …and Save persists it: the promote body carries BOTH saved members (nothing silently dropped)
    await user.click(screen.getByRole("button", { name: "Save chain" }));
    await waitFor(() => expect(s.promote).toHaveBeenCalledTimes(1));
    const body = s.promote.mock.calls[0][0] as { basket: { ticker: string }[] };
    expect(body.basket.map((m) => m.ticker).sort()).toEqual(["GEV", "OKLO"]);
    confirmSpy.mockRestore();
  });
});
