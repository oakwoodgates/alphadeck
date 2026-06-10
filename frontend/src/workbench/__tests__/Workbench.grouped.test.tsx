import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// A synthetic SEGMENTED scored thesis — the render path the flat seed never exercises (the S4 coverage
// debt). Built in vi.hoisted so the mock factory (hoisted above imports) can reference it.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const members = [
    { security_id: "s-oklo", ticker: "OKLO", archetype: "high_beta", segment: "reactors", purity: fig(4, 100), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(null, null), market_cap: fig(null, 1e10), fit: "pure-play" },
    { security_id: "s-smr", ticker: "SMR", archetype: "leader", segment: "reactors", purity: fig(4, 100), runway: fig(4, 60), catalysts: fig(0, 0), dilution: fig(null, null), market_cap: fig(null, 3e9), fit: "pure-play" },
    { security_id: "s-leu", ticker: "LEU", archetype: "shovel", segment: "fuel", purity: fig(3, 77), runway: fig(4, 160), catalysts: fig(2, 1), dilution: fig(null, null), market_cap: fig(null, 3e9), fit: "core exposure" },
  ];
  const segments = [
    { label: "reactors", descriptor: "catalyst-rich" },
    { label: "fuel", descriptor: null },
  ];
  const scored = { thesis_id: "t1", asof: "2026-06-08", segments, members };
  const thesis = {
    id: "t1",
    name: "Small-scale nuclear",
    narrative: "AI power demand + the SMR build-out.",
    ticker: null,
    segments,
    basket: members.map((m) => ({
      ticker: m.ticker,
      role: "r",
      archetype: m.archetype,
      security_id: m.security_id,
      segment: m.segment,
      authored_by: "operator_set",
    })),
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  };
  return { scored, thesis };
});

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: [{ id: "t1", name: "Small-scale nuclear", ticker: null, basket_size: 3, narrative: "x" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
}));

import { Workbench } from "../Workbench";

describe("Workbench grouped render (the S4 coverage debt)", () => {
  const tickersIn = (root: HTMLElement) =>
    Array.from(root.querySelectorAll(".nmrow .tk")).map((e) => e.textContent);
  const segButtons = (root: HTMLElement) =>
    Array.from(root.querySelectorAll<HTMLButtonElement>(".chain .seg"));

  it("renders the chain hero's links and filters the scored rows by the selected link", async () => {
    const { container } = render(
      <Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />,
    );

    // the value-chain hero renders BOTH links, in order
    expect(segButtons(container).map((b) => b.querySelector(".sn")?.textContent)).toEqual([
      "reactors",
      "fuel",
    ]);

    // default = the first link (reactors): its two names show in the scored rows; fuel's name does not
    expect(tickersIn(container)).toEqual(["OKLO", "SMR"]);

    // select the second link -> the scored rows filter to fuel's one name
    const fuel = segButtons(container).find((b) => b.textContent?.includes("fuel"));
    await userEvent.click(fuel!);
    expect(tickersIn(container)).toEqual(["LEU"]);
  });
});
