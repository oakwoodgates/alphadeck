import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// PR-4 — the on-promote ingest indicator: a Save/promote whose response carries an `ingest` job ref
// renders the QUIET "fetching data for N new names…" note (polling the job), resolves to a quiet done
// note, turns into a VISIBLE failure line on a failed job, and renders NOTHING at all when no ingest
// was kicked (honest loudness). The mock spans Workbench + the (real) ChainEditor it mounts, so the
// editor-Save hand-up path (onDone carrying the ref past the editor unmount) is exercised too.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const basket = [
    {
      ticker: "OKLO",
      role: "r",
      security_id: "s-oklo",
      segment: "reactors",
      conviction: null,
      authored_by: "system_drafted",
      signed_off: false,
      thesis_fit: null,
    },
  ];
  const segments = [{ label: "reactors", descriptor: null }];
  const members = [
    {
      security_id: "s-oklo",
      ticker: "OKLO",
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

const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  // the LAST successful promote's response (drives Workbench's `promote.data?.ingest` effect)
  promoteData: undefined as unknown,
  // the ingest-job poll state IngestNote reads (set per test)
  jobQ: { data: undefined as unknown, isError: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: () => {}, isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useResolveEtf: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  usePutExclusions: () => ({
    mutateAsync: async () => ({}),
    isPending: false,
    isError: false,
    error: null,
  }),
  useTheses: () => ({
    data: [{ id: "t-nuke", name: "Small modular nuclear", ticker: null, basket_size: 1, narrative: "x" }],
  }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.mutate,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    data: h.promoteData,
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
  }),
  // PR-4: the ingest-job poll IngestNote runs — driven per test via h.jobQ
  useIngestJobStatus: () => h.jobQ,
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useStartDraft: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDraftJobStatus: () => ({ data: undefined, isError: false }),
  useThesisRuns: () => ({ data: [], isError: false }),
  useLoadThesisRun: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useProduceTerms: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEditTerms: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useRecommendTiers: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { Workbench } from "../Workbench";

const INGEST = { job_id: "ij1", new_members: 2 };

const renderWb = () => render(<Workbench asof="2026-06-08" />);

describe("Workbench — the on-promote ingest note (PR-4)", () => {
  beforeEach(() => {
    h.mutate.mockReset();
    h.promoteData = undefined;
    h.jobQ = { data: undefined, isError: false, error: null };
  });

  it("renders the quiet fetching note when the save response carries an ingest ref", () => {
    h.promoteData = { ...fx.thesis, ingest: INGEST };
    h.jobQ = { data: { job_id: "ij1", status: "running", result: null, error: null }, isError: false, error: null };
    renderWb();
    expect(screen.getByText(/Fetching data for 2 new names/)).toBeInTheDocument();
    expect(screen.queryByText(/failed/)).not.toBeInTheDocument();
  });

  it("resolves to a quiet done note when the job lands done", () => {
    h.promoteData = { ...fx.thesis, ingest: INGEST };
    h.jobQ = {
      data: { job_id: "ij1", status: "done", result: { members: 2, form4: 3, price_bars: 9, form8k: 0, sched13: 0, fund_shares: 0 }, error: null },
      isError: false,
      error: null,
    };
    renderWb();
    expect(screen.getByText(/Data fetched for 2 new names/)).toBeInTheDocument();
  });

  it("a failed job is a VISIBLE failure line carrying the cause (and the cron backstop)", () => {
    h.promoteData = { ...fx.thesis, ingest: INGEST };
    h.jobQ = {
      data: { job_id: "ij1", status: "failed", result: null, error: "1 of 2 names failed — X: price: boom" },
      isError: false,
      error: null,
    };
    renderWb();
    const line = screen.getByText(/Data fetch for 2 new names failed/);
    expect(line).toBeInTheDocument();
    expect(line.textContent).toContain("price: boom");
    expect(line.textContent).toMatch(/nightly cron/);
  });

  it("a lost job (poll 404) is a visible line too, naming the cron backstop", () => {
    h.promoteData = { ...fx.thesis, ingest: INGEST };
    h.jobQ = { data: undefined, isError: true, error: { detail: "ingest job not found" } };
    renderWb();
    const line = screen.getByText(/Lost track of the data fetch for 2 new names/);
    expect(line.textContent).toMatch(/nightly cron/);
  });

  it("renders NOTHING when the save kicked no ingest (ingest: null)", () => {
    h.promoteData = { ...fx.thesis, ingest: null };
    renderWb();
    expect(screen.queryByText(/Fetching data/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Data fetch/)).not.toBeInTheDocument();
  });

  it("an editor Save carrying an ingest ref surfaces the note after the editor exits (the onDone hand-up)", async () => {
    const user = userEvent.setup();
    // the editor's Save resolves WITH a response carrying the ingest ref — the mutation fires onSuccess(data)
    h.mutate.mockImplementation((_b: unknown, opts?: { onSuccess?: (data: unknown) => void }) =>
      opts?.onSuccess?.({ ...fx.thesis, ingest: INGEST }),
    );
    h.jobQ = { data: { job_id: "ij1", status: "running", result: null, error: null }, isError: false, error: null };
    renderWb();

    await user.click(screen.getByRole("button", { name: /edit the basket/i }));
    await user.click(screen.getByRole("button", { name: "Save chain" }));

    // back on the scored view: the saved-exit note AND the quiet ingest note
    expect(await screen.findByText(/Chain saved/)).toBeInTheDocument();
    expect(screen.getByText(/Fetching data for 2 new names/)).toBeInTheDocument();
  });
});
