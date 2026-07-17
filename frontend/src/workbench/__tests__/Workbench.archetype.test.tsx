import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Slice 4 — the #10 archetype recommendation. A derived default (market cap + purity) that DIFFERS from the
// current archetype shows pending in the DD rail; applying it persists ONE member as operator_edited (the
// operator decides). The rule ABSTAINS (no chip) when it has no hint, and stays quiet when it already agrees.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const basket = [
    { ticker: "OKLO", role: "r", archetype: "high_beta", security_id: "s-oklo", segment: null, authored_by: "operator_set", thesis_fit: "the only NRC-approved SMR designer — the pure-play anchor" },
    { ticker: "LEU", role: "r", archetype: "shovel", security_id: "s-leu", segment: null, authored_by: "operator_set", thesis_fit: null },
    { ticker: "CCJ", role: "r", archetype: "leader", security_id: "s-ccj", segment: null, authored_by: "operator_set", thesis_fit: null },
    // item F: a placed-but-not-finalized name — NO archetype (placement never characterizes)
    { ticker: "QMEM", role: "r", archetype: null, security_id: "s-qmem", segment: null, authored_by: "system_drafted", thesis_fit: null },
  ];
  const members = [
    // disagreement: high_beta now, the figures suggest leader -> a pending chip + a ✦ dot
    // (also carries the display identity — the name on the row, the chips + prose on the rail)
    { security_id: "s-oklo", ticker: "OKLO", name: "Oklo Inc.", sector: "Electric Services", exchange: "NYSE", category: "Non-accelerated filer", archetype: "high_beta", archetype_hint: "leader", segment: null, purity: fig(4, 100), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(null, null), market_cap: fig(null, 1.2e10), fit: "pure-play" },
    // abstain: no hint (relational / no facts) -> no chip, no dot
    { security_id: "s-leu", ticker: "LEU", archetype: "shovel", archetype_hint: null, segment: null, purity: fig(3, 77), runway: fig(4, 160), catalysts: fig(2, 1), dilution: fig(null, null), market_cap: fig(null, 3e9), fit: "core exposure" },
    // agreement: hint == archetype -> quiet (no chip, no dot)
    { security_id: "s-ccj", ticker: "CCJ", archetype: "leader", archetype_hint: "leader", segment: null, purity: fig(4, 100), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(null, null), market_cap: fig(null, 5e10), fit: "pure-play" },
    // item F: un-decided (null) with a computed hint — a PENDING decision (dot + rail set control)
    { security_id: "s-qmem", ticker: "QMEM", archetype: null, archetype_hint: "lotto", segment: null, purity: fig(2, 40), runway: fig(1, 6), catalysts: fig(0, 0), dilution: fig(null, null), market_cap: fig(null, 2e8), fit: "adjacent" },
  ];
  const thesis = {
    id: "t-nuke",
    name: "Nuclear",
    narrative: "n",
    ticker: null,
    segments: [],
    basket,
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  };
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments: [], members };
  return { thesis, scored, basket };
});

const h = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.mutate,
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
  // the AUTO-shares auto-confirm fired by get-data (inert here; its own suite covers it)
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — the #10 archetype recommendation (Slice 4)", () => {
  beforeEach(() => {
    h.mutate.mockReset();
  });

  it("shows the derived recommendation pending and applies it as operator_edited (one member, via promote)", async () => {
    const user = userEvent.setup();
    renderWb();
    // the DD rail (first member, OKLO, selected by default) shows the derived suggestion — pending, not applied
    expect(screen.getByTitle(/a recommendation, not a verdict/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /apply leader to OKLO/i }));

    expect(h.mutate).toHaveBeenCalledTimes(1);
    const payload = h.mutate.mock.calls[0][0];
    expect(payload.id).toBe("t-nuke"); // the same thesis, resent (the rest of the chain untouched)
    expect(payload.basket).toHaveLength(4);
    const oklo = payload.basket.find((b: { security_id: string }) => b.security_id === "s-oklo");
    expect(oklo.archetype).toBe("leader"); // the recommendation applied
    expect(oklo.authored_by).toBe("operator_edited"); // operator authority — never auto-applied (#10)
    // the other names are resent verbatim (a single-member edit, not a chain rewrite)
    const leu = payload.basket.find((b: { security_id: string }) => b.security_id === "s-leu");
    expect(leu.archetype).toBe("shovel");
  });

  it("the row says WHO (company name), and the rail carries identity chips + the thesis-fit prose", () => {
    renderWb();
    // the scored row: ticker + the company name (a ticker-only list is a memory quiz)
    expect(screen.getByRole("button", { name: /OKLO.*Oklo Inc\./ })).toBeInTheDocument();
    // the rail (OKLO selected by default): identity chips + the persisted thesis-fit prose
    expect(screen.getByText("Electric Services")).toBeInTheDocument();
    expect(screen.getByText("Non-accelerated filer")).toBeInTheDocument();
    expect(screen.getByText(/the only NRC-approved SMR designer/)).toBeInTheDocument();
    // a member with NO identity renders no empty chips (LEU carries none in this fixture)
  });

  it("only names with a PENDING decision carry the ✦ indicator — abstain and agreement stay quiet", () => {
    renderWb();
    // four scored names: OKLO (disagreement) + QMEM (unset-with-hint — item F's pending decision) show the
    // ✦ dot; LEU abstains (no hint), CCJ agrees (hint == archetype)
    expect(screen.getAllByText("✦")).toHaveLength(2);
  });

  it("item F: an unclassified member — the rail says so, the list stays quiet, the manual set decides", async () => {
    const user = userEvent.setup();
    renderWb();
    // the list row renders NO archetype chip for the unset name (quiet — the ✦ dot carries "pending"),
    // and the literal string "null"/"unset" never reaches the UI
    expect(screen.queryByText("null")).not.toBeInTheDocument();
    expect(screen.queryByText("unset")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /QMEM/ })); // select the un-decided member
    expect(screen.getByText("unclassified")).toBeInTheDocument(); // the rail states it honestly
    // the hint still recommends against an unset value (any non-null hint is a pending decision)…
    expect(screen.getByRole("button", { name: /apply lotto to QMEM/i })).toBeInTheDocument();
    // …and the manual set is the SAME single decision point — pick shovel (a relational call the
    // hint never guesses); it persists through the promote writer as operator_edited (#10)
    await user.selectOptions(screen.getByLabelText("set archetype for QMEM"), "shovel");
    expect(h.mutate).toHaveBeenCalledTimes(1);
    const payload = h.mutate.mock.calls[0][0];
    const qmem = payload.basket.find((b: { security_id: string }) => b.security_id === "s-qmem");
    expect(qmem.archetype).toBe("shovel");
    expect(qmem.authored_by).toBe("operator_edited");
    // the rest of the chain rides verbatim — including OKLO's stored archetype
    expect(payload.basket.find((b: { security_id: string }) => b.security_id === "s-oklo").archetype).toBe(
      "high_beta",
    );
  });
});
