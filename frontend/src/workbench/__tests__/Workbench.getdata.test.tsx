import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// PR-C — gate 2 of the three-gate TRIAGE flow: the scored row's per-name "⇣ get data" opt-in. The control
// fires the EXISTING per-name extract (mark = the click; cost visible per name), shares the FactsPanel's
// query (same key → fetched candidates render in the rail instantly), fails visibly per name, and
// disappears once the member has confirmed fundamentals. The funnel line counts confirmed-data coverage.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const members = [
    // WARM: has a confirmed fact (purity) → NO get-data control; counts toward the funnel
    { security_id: "s-warm", ticker: "WARM", archetype: "leader", archetype_hint: null, segment: null, purity: fig(4, 100), runway: fig(4, null), catalysts: fig(0, 0), dilution: fig(null, null), market_cap: fig(null, 1e10), fit: "pure-play" },
    // COLD: the honest cold path — nothing confirmed → the get-data control renders
    { security_id: "s-cold", ticker: "COLD", archetype: null, archetype_hint: null, segment: null, purity: fig(null, null), runway: fig(null, null), catalysts: fig(0, 0), dilution: fig(null, null), market_cap: fig(null, null), fit: "unscored" },
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

const h = vi.hoisted(() => ({
  refetch: vi.fn(),
  extract: {} as Record<string, { data?: unknown; error?: unknown; isFetching?: boolean }>,
  calls: [] as [string, string | undefined][],
}));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: [{ id: "t1", name: "N", ticker: null, basket_size: 2, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  // ONE extract mock for BOTH observers (the row control + the rail's FactsPanel). h.calls captures the
  // (securityId, thesisId) pair each caller used — the cache-key contract the instant-sharing rests on.
  useExtract: (sid: string, tid?: string) => {
    h.calls.push([sid, tid]);
    const s = h.extract[sid] ?? {};
    return { data: s.data, error: s.error ?? null, isFetching: s.isFetching ?? false, refetch: h.refetch };
  },
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

const railTicker = () => document.querySelector(".dd-head .tk")?.textContent;

describe("Workbench — the per-name get-data opt-in (gate 2 of the three-gate flow)", () => {
  beforeEach(() => {
    h.refetch.mockReset();
    h.extract = {};
    h.calls = [];
  });

  it("renders ⇣ get data ONLY on names without confirmed fundamentals; the funnel counts coverage", () => {
    renderWb();
    expect(screen.getByRole("button", { name: "get data for COLD" })).toBeInTheDocument();
    // WARM already carries a confirmed fact — no control (the spend would be redundant noise)
    expect(screen.queryByRole("button", { name: "get data for WARM" })).not.toBeInTheDocument();
    // the funnel, visible: gate 2→3 progress over the whole basket
    expect(screen.getByText(/data confirmed on 1 of 2/)).toBeInTheDocument();
  });

  it("the click fires THAT name's extraction only, and does NOT change the rail selection", async () => {
    const user = userEvent.setup();
    renderWb();
    expect(railTicker()).toBe("WARM"); // the first member is selected by default
    await user.click(screen.getByRole("button", { name: "get data for COLD" }));
    expect(h.refetch).toHaveBeenCalledTimes(1); // one name, one deliberate spend
    expect(railTicker()).toBe("WARM"); // getting data is not opening — selection untouched
  });

  it("while fetching, the row says so (a per-name spinner state, not a global one)", () => {
    h.extract["s-cold"] = { isFetching: true };
    renderWb();
    expect(screen.getByText("extracting…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "get data for COLD" })).not.toBeInTheDocument();
  });

  it("fetched candidates flip the control to '✓ data ready — ratify', which OPENS the name", async () => {
    const user = userEvent.setup();
    h.extract["s-cold"] = { data: [] }; // the shared query holds this name's candidates
    renderWb();
    await user.click(screen.getByRole("button", { name: /data ready for COLD/ }));
    expect(railTicker()).toBe("COLD"); // ready → open → ratify in the rail
  });

  it("a per-name failure is visible + retryable, never silent", async () => {
    const user = userEvent.setup();
    h.extract["s-cold"] = { error: { detail: "EDGAR unreachable" } };
    renderWb();
    const retry = screen.getByRole("button", { name: "retry get data for COLD" });
    expect(retry).toHaveAttribute("title", expect.stringContaining("EDGAR unreachable"));
    await user.click(retry);
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("the row control and the rail's FactsPanel address ONE query (identical key args)", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.click(screen.getByRole("button", { name: "COLD" })); // select → the rail mounts COLD's FactsPanel
    const pairs = h.calls.filter(([sid]) => sid === "s-cold").map(([sid, tid]) => `${sid}|${tid}`);
    // every observer of this name used the SAME (securityId, thesisId) — the cache-sharing contract —
    // and post-select there are at least two observers (the row + the rail's panel)
    expect(new Set(pairs)).toEqual(new Set(["s-cold|t1"]));
    expect(pairs.length).toBeGreaterThanOrEqual(2);
  });
});
