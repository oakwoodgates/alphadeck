import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The sleeve's holdings drawer (ETF Sleeve, Slice 2a): the N-PORT pull fires ONLY on the deliberate
// drawer toggle (the cost thread) — never on render — and the answer renders as the three-bucket
// partition (held / available / unresolved) + the vintage-labeled filing link. Hooks are mocked at the
// module seam (the ScoredRow.fund.test idiom); `h.holdings` is swapped per test to walk the states.
const h = vi.hoisted(() => {
  const refetch = vi.fn(async () => ({}));
  return {
    refetch,
    holdings: {
      data: undefined as unknown,
      error: null as unknown,
      isFetching: false,
      refetch,
    },
  };
});
vi.mock("../../api/hooks", () => ({
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => h.holdings,
}));

import { ScoredRow } from "../ScoredRow";

const fig = (pips: number | null, value: number | null, provenance: unknown[] = []) => ({
  pips,
  value,
  provenance,
});

function fundMember() {
  return {
    security_id: "s-lit",
    ticker: "LIT",
    name: "Global X Lithium & Battery Tech ETF",
    archetype: "fund",
    archetype_hint: null,
    segment: null,
    purity: fig(null, null),
    runway: fig(null, null),
    catalysts: fig(null, null),
    dilution: fig(null, null),
    market_cap: fig(null, null, [
      { source: "price", ref: "price:2026-06-10", url: null, detail: { close: 41.23 } },
    ]),
    fit: "",
    unconfirmed_estimates: 0,
  };
}

const holdingsPayload = {
  report_date: "2026-04-30",
  source_ref:
    "https://www.sec.gov/Archives/edgar/data/1432353/000204825126005686/0002048251-26-005686-index.htm",
  holdings_count: 3,
  held: [
    {
      name: "KRATOS DEFENSE & SECURITY SOLUTIONS INC",
      ticker: "KTOS",
      cusip: "50077B207",
      isin: null,
      pct_val: 1.05,
      val_usd: 68148601.3,
      security_id: "s-ktos",
    },
  ],
  available: [
    {
      name: "TESLA INC",
      ticker: "TSLA",
      cusip: "88160R101",
      isin: null,
      pct_val: 9.74,
      val_usd: 1.0,
      security_id: "s-tsla",
    },
  ],
  unresolved: [
    {
      name: "RIO TINTO PLC",
      ticker: null,
      cusip: "767204100",
      isin: "US7672041008",
      pct_val: 19.5,
      val_usd: 2.0,
      security_id: null,
    },
  ],
};

function renderFund() {
  return render(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <ScoredRow member={fundMember() as any} selected={false} onSelect={() => {}} />,
  );
}

describe("ScoredRow — the fund sleeve's holdings drawer", () => {
  beforeEach(() => {
    h.refetch.mockClear();
    h.holdings.data = undefined;
    h.holdings.error = null;
    h.holdings.isFetching = false;
  });

  it("never fires the pull on render — only the deliberate toggle click spends", () => {
    renderFund();
    expect(h.refetch).not.toHaveBeenCalled(); // the cost thread: render is free
    fireEvent.click(screen.getByText("› holdings"));
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("a re-open re-shows the cached answer without re-spending", () => {
    h.holdings.data = holdingsPayload;
    renderFund();
    fireEvent.click(screen.getByText("› holdings")); // open — data already cached
    fireEvent.click(screen.getByText("⌄ holdings")); // close
    fireEvent.click(screen.getByText("› holdings")); // re-open
    expect(h.refetch).not.toHaveBeenCalled();
  });

  it("renders the three-bucket partition + the vintage-labeled filing link", () => {
    h.holdings.data = holdingsPayload;
    renderFund();
    fireEvent.click(screen.getByText("› holdings"));
    // the counts summary — the partition sums to the holdings count
    expect(screen.getByText(/3 holdings · 1 in basket · 1 in master · 1 unresolved/)).toBeInTheDocument();
    // the provenance link carries the vintage (#6 + #1's label)
    const link = screen.getByRole("link", { name: /N-PORT as-of 2026-04-30/ });
    expect(link).toHaveAttribute("href", holdingsPayload.source_ref);
    // held ✓ / available / unresolved — the unresolved row still shows identity via name+CUSIP
    expect(screen.getByText(/already in your basket/i)).toBeInTheDocument();
    expect(screen.getByText("KTOS")).toBeInTheDocument();
    expect(screen.getByText(/not in this basket/i)).toBeInTheDocument();
    expect(screen.getByText("TSLA")).toBeInTheDocument();
    expect(screen.getByText(/unresolved — no master match/i)).toBeInTheDocument();
    expect(screen.getByText("RIO TINTO PLC")).toBeInTheDocument();
    expect(screen.getByText("CUSIP 767204100")).toBeInTheDocument();
  });

  it("a failed pull is a visible retry, and the retry re-spends", () => {
    h.holdings.error = { detail: "SEC unreachable" };
    renderFund();
    fireEvent.click(screen.getByText("› holdings"));
    const retry = screen.getByText("⚠ retry holdings");
    fireEvent.click(retry);
    // open (data undefined -> refetch) + the explicit retry
    expect(h.refetch).toHaveBeenCalledTimes(2);
  });
});
