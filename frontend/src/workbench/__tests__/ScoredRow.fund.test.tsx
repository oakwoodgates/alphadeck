import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// ScoredRow's `fund` branch (ETF Sleeve, Slice 1): a sleeve renders "ETF sleeve" + its PRICE (context, not a
// signal), NOT the four equity meters or the get-data control. The three data hooks ScoredRow calls are inert
// for a fund (the branch returns before using them) — mocked to no-ops so no QueryClient is needed.
vi.mock("../../api/hooks", () => ({
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { ScoredRow } from "../ScoredRow";

const fig = (pips: number | null, value: number | null, provenance: unknown[] = []) => ({
  pips,
  value,
  provenance,
});

function fundMember(overrides: Record<string, unknown> = {}) {
  return {
    security_id: "s-lit",
    ticker: "LIT",
    name: "Global X Lithium & Battery Tech ETF",
    instrument_kind: "etf",
    segment: null,
    purity: fig(null, null),
    runway: fig(null, null),
    catalysts: fig(null, null),
    dilution: fig(null, null),
    // a fund has no shares fact -> _market_cap returns the price-only branch (value null, price provenance)
    market_cap: fig(null, null, [
      { source: "price", ref: "price:2026-06-10", url: null, detail: { close: 41.23 } },
    ]),
    fit: "",
    unconfirmed_estimates: 0,
    ...overrides,
  };
}

describe("ScoredRow — the `fund` sleeve branch", () => {
  it("renders the ETF sleeve label + its price, and none of the equity meters / get-data", () => {
    render(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <ScoredRow member={fundMember() as any} selected={false} onSelect={() => {}} />,
    );
    expect(screen.getByText("ETF sleeve")).toBeInTheDocument(); // archLabel('fund')
    expect(screen.getByText(/sleeve price/i)).toBeInTheDocument();
    expect(screen.getByText("$41.23")).toBeInTheDocument(); // the close off the price provenance

    // NOT a scored equity: no meters, no get-data, no "mkt cap" figure
    expect(screen.queryByText("purity")).not.toBeInTheDocument();
    expect(screen.queryByText("runway")).not.toBeInTheDocument();
    expect(screen.queryByText("catalysts")).not.toBeInTheDocument();
    expect(screen.queryByText("dilution")).not.toBeInTheDocument();
    expect(screen.queryByText(/get data/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/mkt cap/i)).not.toBeInTheDocument();
  });

  it("shows an honest em-dash (never $0) for a sleeve with no price bar yet", () => {
    render(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <ScoredRow member={fundMember({ market_cap: fig(null, null, []) }) as any} selected={false} onSelect={() => {}} />,
    );
    expect(screen.getByText("ETF sleeve")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
