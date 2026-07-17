import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// PR-C — gate 2 of the three-gate TRIAGE flow: the scored row's per-name "⇣ get data" opt-in. The control
// fires the EXISTING per-name extract (mark = the click; cost visible per name), shares the FactsPanel's
// query (same key → fetched candidates render in the rail instantly), fails visibly per name, and
// disappears once the member has confirmed fundamentals. The funnel line counts confirmed-data coverage.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  // a ratified purity, as the scored read really returns it: pips + value + the fact's PROVENANCE. The
  // provenance is what marks a fact "on file" (`onFileValues` — the same predicate the FactsPanel uses),
  // so a fixture with `provenance: []` would look permanently unratified to the row control.
  const purityOnFile = {
    pips: 4,
    value: 100,
    provenance: [
      {
        source: "10-k-segment",
        ref: "https://sec.gov/warm-10k.htm",
        detail: { mix_pct: 100, segment_label: "memory", ratified_by: "operator" },
      },
    ],
  };
  const members = [
    // WARM: has a confirmed fact (purity) → NO get-data control; counts toward the funnel
    { security_id: "s-warm", ticker: "WARM", archetype: "leader", archetype_hint: null, segment: null, purity: purityOnFile, runway: fig(4, null), catalysts: fig(0, 0), dilution: fig(null, null), market_cap: fig(null, 1e10), fit: "pure-play" },
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
  pxMutate: vi.fn(),
  autoMutate: vi.fn(),
  extract: {} as Record<string, { data?: unknown; error?: unknown; isFetching?: boolean }>,
  calls: [] as [string, string | undefined][],
}));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t1", name: "N", ticker: null, basket_size: 2, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  // the section-data runner (inert; its own suites cover it) + the per-name price pull (CAPTURED —
  // the surgical get-data must pull the FULL per-name set: extraction AND the price bars)
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: h.pxMutate, isPending: false, isError: false, error: null }),
  // the AUTO-shares auto-confirm fired by get-data — CAPTURED (the ceremonial confirm, removed)
  useAutoConfirmShares: () => ({ mutate: h.autoMutate, isPending: false, isError: false, error: null }),
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
    h.pxMutate.mockReset();
    h.autoMutate.mockReset();
    // getData awaits refetch() and reads its candidates — the default resolves with none (no auto-apply)
    h.refetch.mockResolvedValue({ data: undefined });
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

  it("the click fires THAT name's extraction AND price pull, without changing the rail selection", async () => {
    const user = userEvent.setup();
    renderWb();
    expect(railTicker()).toBe("WARM"); // the first member is selected by default
    await user.click(screen.getByRole("button", { name: "get data for COLD" }));
    expect(h.refetch).toHaveBeenCalledTimes(1); // one name, one deliberate spend
    // the surgical option pulls the FULL per-name set — the decoupled price leg rides the same click
    expect(h.pxMutate).toHaveBeenCalledTimes(1);
    expect(h.pxMutate).toHaveBeenCalledWith("s-cold");
    expect(railTicker()).toBe("WARM"); // getting data is not opening — selection untouched
  });

  it("get-data auto-applies an AUTO (unflagged) shares count for that name", async () => {
    const user = userEvent.setup();
    h.refetch.mockResolvedValue({ data: [{ fact_type: "shares_outstanding", tier: "auto" }] });
    renderWb();
    await user.click(screen.getByRole("button", { name: "get data for COLD" }));
    // the confirm was ceremonial — nobody knows a share count by heart — so get-data applies it
    expect(h.autoMutate).toHaveBeenCalledTimes(1);
    expect(h.autoMutate).toHaveBeenCalledWith("s-cold");
  });

  it("get-data does NOT auto-apply a FLAGged shares count (the operator ratifies it)", async () => {
    const user = userEvent.setup();
    h.refetch.mockResolvedValue({
      data: [{ fact_type: "shares_outstanding", tier: "flag", flags: ["dual-class"] }],
    });
    renderWb();
    await user.click(screen.getByRole("button", { name: "get data for COLD" }));
    expect(h.autoMutate).not.toHaveBeenCalled(); // a class sum is a judgment, never the machine's
    expect(h.refetch).toHaveBeenCalledTimes(1); // the extract itself still ran
  });

  it("THE BUG FIX: a name with one confirmed fact still surfaces its UNRATIFIED candidates", () => {
    // WARM carries a confirmed purity, so the OLD rule ("ANY fact confirmed" -> hide) silenced the whole
    // name while shares + cash were still outstanding. Auto-applying shares would have INDUSTRIALIZED that:
    // every clean name self-confirms its shares and instantly goes quiet with two facts unratified. The
    // control now counts what is LEFT, using the same on-file predicate the FactsPanel does.
    h.extract["s-warm"] = {
      data: [
        { fact_type: "revenue_mix", tier: "human" }, // ON FILE (WARM's ratified purity)
        { fact_type: "shares_outstanding", tier: "auto" }, // outstanding
        { fact_type: "cash_burn", tier: "auto" }, // outstanding
      ],
    };
    renderWb();
    const ready = screen.getByRole("button", { name: /data ready for WARM/ });
    expect(ready).toHaveTextContent("ratify 2"); // NOT hidden, and honest about how many
    expect(ready).toHaveAttribute("title", expect.stringContaining("cash_burn"));
  });

  it("a fully-ratified name goes quiet — nothing left to ratify, no control", () => {
    // every fetched candidate has a fact on file (WARM's purity) -> the control correctly disappears
    h.extract["s-warm"] = { data: [{ fact_type: "revenue_mix", tier: "human" }] };
    renderWb();
    expect(screen.queryByRole("button", { name: /data ready for WARM/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "get data for WARM" })).not.toBeInTheDocument();
  });

  it("while fetching, the row says so (a per-name spinner state, not a global one)", () => {
    h.extract["s-cold"] = { isFetching: true };
    renderWb();
    expect(screen.getByText("extracting…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "get data for COLD" })).not.toBeInTheDocument();
  });

  it("fetched candidates flip the control to '✓ data ready — ratify', which OPENS the name", async () => {
    const user = userEvent.setup();
    h.extract["s-cold"] = { data: [{ fact_type: "shares_outstanding" }] }; // the query holds ≥1 candidate
    renderWb();
    await user.click(screen.getByRole("button", { name: /data ready for COLD/ }));
    expect(railTicker()).toBe("COLD"); // ready → open → ratify in the rail
  });

  it("an EMPTY extract shows the honest '— no 10-K/10-Q' state, never the false 'data ready'", () => {
    // a foreign 20-F/6-K issuer (e.g. SIMO) returns [] — fetched, but nothing the extractor covers
    h.extract["s-cold"] = { data: [] };
    renderWb();
    expect(screen.getByText(/no 10-K\/10-Q/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /data ready for COLD/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "get data for COLD" })).not.toBeInTheDocument();
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
