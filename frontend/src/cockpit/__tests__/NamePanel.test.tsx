import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// NamePanel reads the per-name decision log via useDecisions — the only hook in its render tree (Meter,
// TriggerRow, DisplaySignalsSection use none). Mock it to an empty log so the panel renders standalone.
vi.mock("../../api/hooks", () => ({
  useDecisions: () => ({ data: [] }),
}));

import type { ScoredMemberOut } from "../../api/hooks";
import { bucketDef, type BucketRow } from "../buckets";
import { NamePanel } from "../NamePanel";

const fig = { pips: null, value: null, provenance: [] };

// A minimal scored member — the identity block + the meters the panel reads; cast (the generated type
// carries many more fields). `foreign_filer_form` is the new field under test.
const scored = (over: Partial<ScoredMemberOut>): ScoredMemberOut =>
  ({
    security_id: "s-ccj",
    ticker: "CCJ",
    name: "Cameco Corp",
    sector: "Uranium",
    exchange: "NYSE",
    category: null,
    origin: "Canada",
    foreign_filer_form: null,
    
    
    segment: "reactors",
    purity: fig,
    runway: fig,
    catalysts: fig,
    dilution: fig,
    market_cap: fig,
    fit: "",
    unconfirmed_estimates: 0,
    ...over,
  }) as unknown as ScoredMemberOut;

const rowFor = (sc: ScoredMemberOut): BucketRow => ({
  member: {
    ticker: sc.ticker ?? "CCJ",
    role: "leader",
    
    security_id: sc.security_id,
    segment: "reactors",
    conviction: null,
    authored_by: "operator_set",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any,
  ordinal: 0,
  call: null,
  scored: sc,
  bucket: "quiet",
});

function renderPanel(sc: ScoredMemberOut) {
  return render(
    <NamePanel
      row={rowFor(sc)}
      def={bucketDef("quiet")}
      card={undefined}
      thesisId="t1"
      position={null}
      display={null}
      asof="2026-07-30"
      onClose={vi.fn()}
    />,
  );
}

describe("NamePanel — the foreign-filer explainability tell", () => {
  it("composes the Origin cell with the filer tell and injects the insider N/A for a foreign filer", () => {
    renderPanel(scored({ origin: "Canada", foreign_filer_form: "40-F" }));
    // the Origin identity cell reads the composed who-and-why in one line
    expect(screen.getByText("Canada · 40-F · no Form 4")).toBeInTheDocument();
    // the Indicators section carries the structural insider N/A (threaded from foreign_filer_form)
    expect(
      screen.getByText("N/A — foreign filer (40-F), no Form 4 (§16-exempt)"),
    ).toBeInTheDocument();
  });

  it("composes the bare '{form} · no Form 4' when origin is unknown", () => {
    renderPanel(scored({ origin: null, foreign_filer_form: "20-F" }));
    expect(screen.getByText("20-F · no Form 4")).toBeInTheDocument();
    expect(
      screen.getByText("N/A — foreign filer (20-F), no Form 4 (§16-exempt)"),
    ).toBeInTheDocument();
  });

  it("leaves a domestic name untouched — origin as-is, no filer tell, no insider N/A", () => {
    renderPanel(scored({ origin: "US", foreign_filer_form: null }));
    // the Origin cell shows the plain origin (never a guessed regime)
    const cells = [...document.querySelectorAll(".np-idgrid .cell")];
    const originCell = cells.find((c) => c.querySelector(".k")?.textContent === "Origin");
    expect(originCell?.querySelector(".v")?.textContent).toBe("US");
    // no foreign-filer content anywhere on the panel
    expect(screen.queryByText(/no Form 4/)).toBeNull();
  });
});

describe("NamePanel — the resolved price-symbol note (honest loudness)", () => {
  const pricedCell = () =>
    [...document.querySelectorAll(".np-idgrid .cell")].find(
      (c) => c.querySelector(".k")?.textContent === "Priced under",
    );

  it("shows a 'Priced under {symbol}' cell ONLY when a resolved vendor symbol is set", () => {
    renderPanel(scored({ ticker: "FDCT", price_symbol: "FDCTD" } as Partial<ScoredMemberOut>));
    expect(pricedCell()?.querySelector(".v")?.textContent).toBe("FDCTD");
  });

  it("renders NO 'Priced under' cell for a name priced under its canonical ticker (no '—' row)", () => {
    renderPanel(scored({ ticker: "CCJ", price_symbol: null } as Partial<ScoredMemberOut>));
    expect(pricedCell()).toBeUndefined(); // the healthy common case is silent, not a blank cell
  });
});

describe("NamePanel — the #1 thin-history data-health flag (honest loudness)", () => {
  it("shows the thin-history warning ONLY when the tape is starved", () => {
    renderPanel(scored({ thin_price_history: true } as Partial<ScoredMemberOut>));
    expect(screen.getByText(/thin price history/)).toBeInTheDocument();
  });

  it("renders no warning for a name with a full year of tape", () => {
    renderPanel(scored({ thin_price_history: false } as Partial<ScoredMemberOut>));
    expect(screen.queryByText(/thin price history/)).toBeNull();
  });
});
