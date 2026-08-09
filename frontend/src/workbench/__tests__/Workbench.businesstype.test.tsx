import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// Business-Type M1 — the rail's RE-TAG surface. The SIC maps auto-derive the leaf (shown with its
// basis, #10); the operator overrules per name through the per-security endpoint (NOT the promote —
// master-level identity), and a standing override shows "your tag · revert" (the visible inverse,
// WB #1). An unclassified name says so honestly and is still taggable.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string, over: Record<string, unknown>) => ({
    security_id: sid, ticker, name: null, sector: null, exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null, null), runway: fig(null, null), catalysts: fig(0, 0),
    dilution: fig(null, null), market_cap: fig(null, null), fit: "unrated",
    unconfirmed_estimates: 0,
    ...over,
  });
  const member = (ticker: string, sid: string) => ({
    ticker, role: "r", security_id: sid, segment: null, authored_by: "operator_set" as const,
    thesis_fit: null,
  });
  const basket = [member("OKLO", "s-oklo"), member("UROY", "s-uroy"), member("QMEM", "s-qmem")];
  const members = [
    // derived leaf, no override -> the quiet "from SIC" basis
    scoredRow("s-oklo", "OKLO", {
      name: "Oklo Inc.", sector: "Electric Services",
      business_type: "utilities", business_supersector: "energy_utilities",
    }),
    // a STANDING re-tag (override) + the royalty overlay riding beside the leaf
    scoredRow("s-uroy", "UROY", {
      name: "Uranium Royalty Corp.", sector: "Commodity Contracts Brokers & Dealers",
      business_type: "miner", business_supersector: "materials",
      business_type_override: "miner", royalty: true,
    }),
    // un-enriched: no sector -> unclassified (honest; still taggable)
    scoredRow("s-qmem", "QMEM", {}),
  ];
  const thesis = {
    id: "t-nuke", name: "Nuclear", narrative: "n", ticker: null, segments: [], basket,
    evidence: [], catalysts: [], kill_criteria: [], position: null,
  };
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments: [], members };
  return { thesis, scored };
});

const h = vi.hoisted(() => ({ retag: vi.fn(), promote: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: h.retag, isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.promote, mutateAsync: vi.fn(), reset: vi.fn(),
    isPending: false, isError: false, isSuccess: false, error: null,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () => render(<Workbench asof="2026-06-08" />);

const railChip = (c: HTMLElement) => c.querySelector(".dd-head .btype");

describe("Workbench — the business-type re-tag (Business-Type M1)", () => {
  it("shows the derived leaf with its 'from SIC' basis, and re-tags through the endpoint (not promote)", async () => {
    const user = userEvent.setup();
    const { container } = renderWb();
    // OKLO is the default rail selection: derived utilities, no standing override
    expect(railChip(container)?.textContent).toBe("utilities");
    expect(railChip(container)?.className).toContain("bt-energy_utilities");
    expect(screen.getByText("from SIC")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("set business type for OKLO"), "semiconductors");
    expect(h.retag).toHaveBeenCalledWith({ securityId: "s-oklo", businessType: "semiconductors" });
    expect(h.promote).not.toHaveBeenCalled(); // master-level identity — never a spine write
  });

  it("a standing override reads 'your tag' with the one-click revert (WB #1), overlay riding beside", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.click(screen.getByRole("button", { name: /^UROY/ })); // select the re-tagged name
    const revert = screen.getByRole("button", { name: "revert UROY to the SIC-derived type" });
    expect(revert).toHaveTextContent("your tag · revert");
    expect(screen.queryByText("from SIC")).toBeNull(); // the basis flips to the override marker
    // the royalty overlay co-exists with the leaf (derive-only)
    expect(screen.getByTitle(/royalty\/streaming/)).toBeInTheDocument();

    await user.click(revert);
    expect(h.retag).toHaveBeenCalledWith({ securityId: "s-uroy", businessType: null }); // null = clear
  });

  it("an un-enriched name reads 'unclassified' honestly — and the set control still offers a tag", async () => {
    const user = userEvent.setup();
    const { container } = renderWb();
    await user.click(screen.getByRole("button", { name: /^QMEM/ }));
    expect(railChip(container)?.textContent).toBe("unclassified");
    await user.selectOptions(screen.getByLabelText("set business type for QMEM"), "spac");
    expect(h.retag).toHaveBeenCalledWith({ securityId: "s-qmem", businessType: "spac" });
  });
});
