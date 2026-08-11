import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Discovery cleanup S1 — the Workbench basket chips' HONEST authorship tag (the second lying surface):
// the tag reads who wrote the member's DESCRIPTION. "your words" renders ONLY for operator_edited (the
// operator actually changed the text); system_drafted AND the retired legacy operator_set both read
// "model draft" — the old "operator" label claimed authorship the operator never exercised.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const basket = [
    { ticker: "AAA", role: "r", security_id: "s-a", segment: null, authored_by: "system_drafted", thesis_fit: "drafted words", signed_off: false },
    { ticker: "BBB", role: "r", security_id: "s-b", segment: null, authored_by: "operator_edited", thesis_fit: "my words", signed_off: true },
    // the legacy value a pre-migration row could still carry — degrades honestly, never "operator"
    { ticker: "CCC", role: "r", security_id: "s-c", segment: null, authored_by: "operator_set", thesis_fit: "seed words", signed_off: false },
  ];
  const member = (sid: string, ticker: string) => ({
    security_id: sid,
    ticker,
    name: ticker,
    segment: null,
    purity: fig(null, null),
    runway: fig(null, null),
    catalysts: fig(null, null),
    dilution: fig(null, null),
    market_cap: fig(null, null),
    fit: "",
  });
  const thesis = {
    id: "t-a",
    name: "Authorship",
    narrative: "n",
    ticker: null,
    segments: [],
    basket,
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  };
  return {
    thesis,
    scored: {
      thesis_id: "t-a",
      asof: "2026-08-09",
      segments: [],
      members: [member("s-a", "AAA"), member("s-b", "BBB"), member("s-c", "CCC")],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: () => {}, isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-a", name: "Authorship", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
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

describe("Workbench — the basket chips' honest authorship tag (S1)", () => {
  it("reads 'your words' ONLY for operator_edited; drafted AND legacy operator_set read 'model draft'", () => {
    render(<Workbench asof="2026-08-09" onAsofChange={() => {}} onBack={() => {}} />);
    // exactly ONE member (BBB, operator_edited) earned "your words"
    expect(screen.getAllByText("your words", { selector: ".wb-author" })).toHaveLength(1);
    // the drafted member AND the legacy operator_set member both read "model draft"
    expect(screen.getAllByText("model draft", { selector: ".wb-author" })).toHaveLength(2);
    // the old lie is gone: nothing renders the bare "operator" tag anymore
    expect(screen.queryByText("operator", { selector: ".wb-author" })).toBeNull();
  });
});
