import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The sleeve's DD-rail dossier (ETF Sleeve, Slice 2a/2b): the N-PORT pull fires ONLY on the explicit "pull
// holdings" button (the cost thread) — never on mount/selection — and the answer renders as the three-bucket
// partition (held / available / unresolved) + the vintage-labeled filing link. Slice 2b adds the include
// button on AVAILABLE rows (in master, not in basket) + its reversible remove. The hook is mocked at the
// module seam; `h.holdings` is swapped per test to walk the states. (format.ts imports only TYPES from hooks,
// so mocking just useEtfHoldings is safe — format's real formatters still run.)
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
  useEtfHoldings: () => h.holdings,
}));

import { SleeveRail } from "../SleeveRail";

const onInclude = vi.fn();
const onRemove = vi.fn();

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
    // a fund has no shares fact -> the price-only branch (value null, price provenance) -> "$41.23"
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

function renderSleeve(opts?: { basketSids?: Set<string>; includePending?: boolean }) {
  return render(
    <SleeveRail
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      member={fundMember() as any}
      thesisId="t1"
      asof="2026-07-26"
      basketSids={opts?.basketSids ?? new Set()}
      onInclude={onInclude}
      onRemove={onRemove}
      includePending={opts?.includePending ?? false}
    />,
  );
}

describe("SleeveRail — the fund sleeve's DD-rail holdings", () => {
  beforeEach(() => {
    h.refetch.mockClear();
    onInclude.mockClear();
    onRemove.mockClear();
    h.holdings.data = undefined;
    h.holdings.error = null;
    h.holdings.isFetching = false;
  });

  it("shows the sleeve identity + price, never firing the pull on mount", () => {
    renderSleeve();
    expect(screen.getByText("ETF sleeve")).toBeInTheDocument(); // archLabel('fund')
    expect(screen.getByText("Global X Lithium & Battery Tech ETF")).toBeInTheDocument();
    expect(screen.getByText("$41.23")).toBeInTheDocument(); // sleevePriceLabel off the price provenance
    // the cost thread: mount/selection is FREE — the button is present, the pull hasn't fired
    expect(h.refetch).not.toHaveBeenCalled();
    expect(screen.getByText("⌾ pull holdings")).toBeInTheDocument();
  });

  it("the deliberate pull-holdings click spends", () => {
    renderSleeve();
    fireEvent.click(screen.getByText("⌾ pull holdings"));
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("renders the three-bucket partition + the vintage-labeled filing link once pulled", () => {
    h.holdings.data = holdingsPayload;
    renderSleeve();
    // cached data renders straight into the rail (no click needed)
    expect(
      screen.getByText(/3 holdings · 1 in basket · 1 in master · 1 unresolved/),
    ).toBeInTheDocument();
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
    // Slice 2b: the include button is on the AVAILABLE row ONLY (not held / not unresolved) — honest loudness
    expect(screen.getAllByText("+ include")).toHaveLength(1);
  });

  it("an available holding's + include fires onInclude(securityId, ticker)", () => {
    h.holdings.data = holdingsPayload;
    renderSleeve(); // basketSids empty -> TSLA is includable
    fireEvent.click(screen.getByText("+ include"));
    expect(onInclude).toHaveBeenCalledWith("s-tsla", "TSLA");
    expect(onRemove).not.toHaveBeenCalled();
  });

  it("an available holding already in the basket shows ✓ included; clicking removes it (the inverse, #1)", () => {
    h.holdings.data = holdingsPayload;
    renderSleeve({ basketSids: new Set(["s-tsla"]) }); // TSLA now in the live basket
    expect(screen.queryByText("+ include")).not.toBeInTheDocument();
    const included = screen.getByText("✓ included");
    fireEvent.click(included);
    expect(onRemove).toHaveBeenCalledWith("s-tsla");
    expect(onInclude).not.toHaveBeenCalled();
  });

  it("a failed pull is a visible retry, and the retry re-spends", () => {
    h.holdings.error = { detail: "SEC unreachable" };
    renderSleeve();
    const retry = screen.getByText("⚠ retry holdings");
    fireEvent.click(retry);
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });
});
