import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const exportSpy = vi.hoisted(() => vi.fn());

const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const members = [
    {
      security_id: "s-warm",
      ticker: "WARM",
      name: "Warm Holdings",
      archetype: "leader",
      archetype_hint: null,
      segment: null,
      purity: fig(4, 100),
      runway: fig(4, null),
      catalysts: fig(0, 0),
      dilution: fig(null, null),
      market_cap: fig(null, 1e10),
      fit: "pure-play",
    },
    {
      security_id: "s-cold",
      ticker: "COLD",
      name: "Cold Storage",
      archetype: null,
      archetype_hint: null,
      segment: null,
      purity: fig(null, null),
      runway: fig(null, null),
      catalysts: fig(0, 0),
      dilution: fig(null, null),
      market_cap: fig(null, null),
      fit: "unscored",
    },
  ];
  const thesis = {
    id: "t1",
    name: "N",
    narrative: "n",
    ticker: null,
    segments: [],
    basket: [],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
    term_set: [] as unknown[],
  };
  const scored = { thesis_id: "t1", asof: "2026-06-08", segments: [], members };
  return { thesis, scored };
});

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: [{ id: "t1", name: "N", ticker: null, basket_size: 2, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

vi.mock("../../util/exportNames", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../util/exportNames")>();
  return { ...mod, exportKeptNames: exportSpy };
});

import { Workbench } from "../Workbench";

describe("Workbench — shortlist export", () => {
  beforeEach(() => {
    exportSpy.mockReset();
  });

  it("exports the full kept shortlist with ticker and name", async () => {
    const user = userEvent.setup();
    render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

    await user.click(screen.getByRole("button", { name: "export 2 shortlist names" }));

    expect(exportSpy).toHaveBeenCalledWith({
      thesisName: "N",
      stage: "shortlist",
      asof: "2026-06-08",
      rows: [
        { ticker: "WARM", name: "Warm Holdings" },
        { ticker: "COLD", name: "Cold Storage" },
      ],
    });
  });
});
