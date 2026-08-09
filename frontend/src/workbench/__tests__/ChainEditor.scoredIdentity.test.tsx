import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The identity-lifecycle READ (the (c) fork): the editor's identity baseline is the LIVE scored join
// (`scoredById` → `idFor`), so the Country/Exchange filters and the IdentityChips work on a SAVED thesis
// opened with NO draft and NO restored session — the exact operator scenario #241 was blocked on (the
// draft/session `identity` map used to be the ONLY source, dead on a fresh open). D4 pins the precedence:
// when both sources carry a value, the live join WINS (it reads the same master rows the draft wrote, so a
// standalone `pipeline.enrich_identity` backfill self-heals the editor with no re-draft); a field the join
// lacks falls back to the draft-time map (no regression while the join catches up).
const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  putExcl: vi.fn(async () => ({})),
  start: vi.fn(),
  produce: vi.fn(),
  edit: vi.fn(),
  recommend: vi.fn(),
  produceData: undefined as unknown,
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
  useProduceTerms: () => ({ mutate: h.produce, data: h.produceData, isPending: false, isError: false, error: null }),
  useEditTerms: () => ({ mutate: h.edit, isPending: false, isError: false, error: null }),
  usePutExclusions: () => ({ mutateAsync: h.putExcl, isPending: false, isError: false, error: null }),
  useRecommendTiers: () => ({ mutate: h.recommend, isPending: false, isError: false, error: null }),
  useThesisRuns: () => ({ data: [], isError: false }),
  useLoadThesisRun: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { ChainEditor } from "../ChainEditor";

// A SAVED two-member thesis — the members are established spine rows; there is NO draft and NO session.
const savedThesis = {
  id: "t1",
  name: "Semis",
  narrative: "n",
  ticker: null,
  segments: [{ label: "chips", descriptor: null }],
  basket: [
    { ticker: "USCO", role: "r", security_id: "s-us", segment: "chips", conviction: null, authored_by: "operator_set" },
    { ticker: "CNCO", role: "r", security_id: "s-cn", segment: "chips", conviction: null, authored_by: "operator_set" },
  ],
  evidence: [],
  catalysts: [],
  kill_criteria: [],
  position: null,
  term_set: [],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

// A minimal scored member — only the identity block matters here; the meters may be absent (the editor
// reads them through optional chains). Cast: the generated type carries many more fields.
const sm = (identity: {
  name?: string;
  sector?: string | null;
  exchange?: string | null;
  category?: string | null;
  origin?: string | null;
  foreign_filer_form?: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
}): any => ({ purity: { pips: null, value: null, provenance: [] }, market_cap: { value: null, provenance: [] }, ...identity });

const scoredById = {
  "s-us": sm({ name: "US Co", sector: "Semiconductors", exchange: "Nasdaq", origin: "US" }),
  "s-cn": sm({ name: "CN Co", sector: "Semiconductors", exchange: "NYSE", origin: "Shanghai" }),
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const draft = (placements: unknown[], segments: unknown[] = []) =>
  ({ thesis_id: "t1", segments, placements }) as any;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mockDraft(result: any) {
  h.start.mockResolvedValue({ job_id: "j1", status: "running" });
  h.jobData = { job_id: "j1", status: "done", result, error: null };
}

beforeEach(() => {
  h.mutate.mockReset();
  h.start.mockReset();
  h.jobData = undefined;
  h.jobIsError = false;
});

describe("ChainEditor — identity from the scored join alone (the identity-lifecycle read)", () => {
  it("renders the identity chips on placed rows with NO draft and NO session — the join is the baseline", () => {
    render(
      <ChainEditor asof="2026-07-30" thesis={savedThesis} onDone={vi.fn()} scoredById={scoredById} />,
    );
    // the chips come from scoredById — the draft/session identity map is EMPTY in this render
    expect(screen.getByText("Shanghai", { selector: ".idchip" })).toBeInTheDocument();
    expect(screen.getByText("US", { selector: ".idchip" })).toBeInTheDocument();
    expect(screen.getByText("Nasdaq", { selector: ".idchip" })).toBeInTheDocument();
  });

  it("the Country + Exchange filters classify off the join alone (the #241-blocked scenario)", async () => {
    const user = userEvent.setup();
    render(
      <ChainEditor asof="2026-07-30" thesis={savedThesis} onDone={vi.fn()} scoredById={scoredById} />,
    );
    // baseline: both saved members render
    expect(screen.getByLabelText("segment for USCO")).toBeInTheDocument();
    expect(screen.getByLabelText("segment for CNCO")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("filter by country"), "foreign");
    expect(screen.queryByLabelText("segment for USCO")).not.toBeInTheDocument();
    expect(screen.getByLabelText("segment for CNCO")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("filter by country"), "us");
    expect(screen.getByLabelText("segment for USCO")).toBeInTheDocument();
    expect(screen.queryByLabelText("segment for CNCO")).not.toBeInTheDocument();

    // exchange spans the same join: main keeps both (Nasdaq + NYSE), otc hides both
    await user.selectOptions(screen.getByLabelText("filter by country"), "all");
    await user.selectOptions(screen.getByLabelText("filter by exchange"), "otc");
    expect(screen.queryByLabelText("segment for USCO")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("segment for CNCO")).not.toBeInTheDocument();
  });

  it("with NO scored row and NO map the filters classify unknown — kept under 'all', never dropped", async () => {
    const user = userEvent.setup();
    render(<ChainEditor asof="2026-07-30" thesis={savedThesis} onDone={vi.fn()} />);
    // no scoredById at all: both rows render (nothing classifies, nothing hides by default — #9)
    expect(screen.getByLabelText("segment for USCO")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("filter by country"), "unknown");
    expect(screen.getByLabelText("segment for USCO")).toBeInTheDocument();
    expect(screen.getByLabelText("segment for CNCO")).toBeInTheDocument();
  });

  it("D4: when BOTH sources carry a value the LIVE join wins (self-heals a stale draft-time entry)", async () => {
    const user = userEvent.setup();
    // the draft re-surfaces the established member carrying a STALE origin ("Shanghai"); the live
    // scored join says "US" (e.g. the master was corrected/backfilled after the session snapshotted)
    mockDraft(
      draft([
        {
          name: "US Co",
          ticker: "USCO",
          prose: "x",
          segment: "chips",
          status: "placed",
          security_id: "s-us",
          candidates: [],
          matched_terms: [],
          discovery_source: "edgar",
          sector: "Semiconductors",
          exchange: "Nasdaq",
          listing_status: "active",
          origin: "Shanghai", // the stale draft-time value the map will store
          off_thesis: false,
        },
      ]),
    );
    render(
      <ChainEditor asof="2026-07-30" thesis={savedThesis} onDone={vi.fn()} scoredById={scoredById} />,
    );
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for USCO");

    // the chip shows the JOIN's value, not the map's stale snapshot
    expect(screen.getByText("US", { selector: ".idchip" })).toBeInTheDocument();
    // and the filter classifies by the join: US keeps the row, foreign hides it
    await user.selectOptions(screen.getByLabelText("filter by country"), "us");
    expect(screen.getByLabelText("segment for USCO")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("filter by country"), "foreign");
    expect(screen.queryByLabelText("segment for USCO")).not.toBeInTheDocument();
  });

  it("renders the foreign-filer chip '{form} · no Form 4' off the join, abstains for a domestic name", () => {
    // s-cn is a §16-exempt foreign filer (a 40-F on file); s-us is domestic. The chip is ADDITIVE — it
    // rides BESIDE origin, never replaces it (origin is already its own chip).
    const scored = {
      "s-us": sm({ name: "US Co", sector: "Semiconductors", exchange: "Nasdaq", origin: "US" }),
      "s-cn": sm({
        name: "CN Co",
        sector: "Uranium",
        exchange: "NYSE",
        origin: "Canada",
        foreign_filer_form: "40-F",
      }),
    };
    render(
      <ChainEditor asof="2026-07-30" thesis={savedThesis} onDone={vi.fn()} scoredById={scored} />,
    );
    // the foreign filer gets the second chip; its origin chip still renders (additive)
    expect(screen.getByText("40-F · no Form 4", { selector: ".idchip" })).toBeInTheDocument();
    expect(screen.getByText("Canada", { selector: ".idchip" })).toBeInTheDocument();
    // exactly ONE filer chip across the basket — the US name abstains (honest, no guessed regime)
    expect(screen.queryAllByText(/no Form 4/)).toHaveLength(1);
  });

  it("a field the join LACKS falls back to the draft-time map (chips never regress mid-session)", async () => {
    const user = userEvent.setup();
    // the scored fetch predates this session's draft-time enrich: the join rows EXIST but carry no
    // identity yet; the fresh draft carried "Shanghai" for USCO — the map fills the join's gap
    const staleJoin = {
      "s-us": sm({ name: "US Co", sector: null, exchange: null, origin: null }),
      "s-cn": sm({ name: "CN Co", sector: null, exchange: null, origin: null }),
    };
    mockDraft(
      draft([
        {
          name: "US Co",
          ticker: "USCO",
          prose: "x",
          segment: "chips",
          status: "placed",
          security_id: "s-us",
          candidates: [],
          matched_terms: [],
          discovery_source: "edgar",
          sector: "Semiconductors",
          exchange: "Nasdaq",
          listing_status: "active",
          origin: "Shanghai",
          off_thesis: false,
        },
      ]),
    );
    render(
      <ChainEditor asof="2026-07-30" thesis={savedThesis} onDone={vi.fn()} scoredById={staleJoin} />,
    );
    await user.click(screen.getByRole("button", { name: /Draft from narrative/ }));
    await screen.findByLabelText("segment for USCO");
    // the join's null origin abstains → the map's fresh draft-time value shows
    expect(screen.getByText("Shanghai", { selector: ".idchip" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("filter by country"), "foreign");
    expect(screen.getByLabelText("segment for USCO")).toBeInTheDocument();
  });
});
