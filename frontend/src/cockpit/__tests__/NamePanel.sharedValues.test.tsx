import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The Cockpit name panel (V2 W5e): "N unconfirmed estimate(s)" is gone; under the meters, ONLY when it applies, quiet
// lines say what a blank meter is waiting on, from the user's read of the shared values. The panel gets the company's
// read as a prop (the Cockpit reads the card once), so it needs no hook of its own.
vi.mock("../../api/hooks", () => ({
  useFundLaunches: () => ({ data: undefined, isLoading: false, error: null }),
  useDecisions: () => ({ data: [] }),
}));

import type { ScoredMemberOut } from "../../api/hooks";
import { ClockProvider } from "../../clock";
import {
  cashAwaiting,
  company,
  noValue,
  sharesFromFiling,
} from "../../workbench/__tests__/userValues.fixtures";
import { bucketDef, type BucketRow } from "../buckets";
import { NamePanel } from "../NamePanel";

const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });

const scored = (over: Partial<ScoredMemberOut> = {}): ScoredMemberOut =>
  ({
    security_id: "s-acme",
    ticker: "ACME",
    name: "Acme Corp",
    sector: "Uranium",
    exchange: "NYSE",
    category: null,
    origin: "US",
    foreign_filer_form: null,
    instrument_kind: "equity",
    segment: "reactors",
    purity: fig(2, 38),
    runway: fig(3, 20),
    catalysts: fig(0, 0),
    dilution: fig(null, null),
    market_cap: fig(null, 8e9),
    fit: "",
    unconfirmed_estimates: 0,
    ...over,
  }) as unknown as ScoredMemberOut;

const rowFor = (sc: ScoredMemberOut): BucketRow => ({
  member: {
    ticker: sc.ticker ?? "ACME",
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

const TODAY = "2026-07-30";

function renderPanel(
  sc: ScoredMemberOut,
  sharedValues: unknown,
  opts: { asof?: string } = {},
) {
  return render(
    <ClockProvider value={{ currentNight: TODAY, marketToday: TODAY, actionDay: null, failed: false }}>
      <NamePanel
        row={rowFor(sc)}
        def={bucketDef("quiet")}
        card={undefined}
        thesisId="t1"
        position={null}
        display={null}
        asof={opts.asof ?? TODAY}
        onClose={vi.fn()}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        sharedValues={sharedValues as any}
      />
    </ClockProvider>,
  );
}

const lines = (c: HTMLElement) => c.querySelector(".np-shared");

describe("NamePanel — quiet lines under the meters (V2 W5e)", () => {
  it("no 'unconfirmed estimate(s)' line, even when the wire still carries a count", () => {
    renderPanel(scored({ unconfirmed_estimates: 1 }), company(sharesFromFiling(), noValue("cash_burn")));
    expect(screen.queryByText(/unconfirmed estimate/)).not.toBeInTheDocument();
  });

  it("a value awaiting the steward: 'Share count: awaiting the steward'", () => {
    const awaiting = { ...cashAwaiting(), fact_type: "shares_outstanding" };
    renderPanel(scored(), company(awaiting, sharesFromFiling({ fact_type: "cash_burn" })));
    expect(screen.getByText("Share count: awaiting the steward")).toBeInTheDocument();
  });

  it("no cash value yet: 'Cash: no value yet (the platform has not read the filings)'", () => {
    renderPanel(scored(), company(sharesFromFiling(), noValue("cash_burn")));
    expect(
      screen.getByText("Cash: no value yet (the platform has not read the filings)"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Share count:/)).not.toBeInTheDocument();
  });

  it("another reason is said in the server's own words, lower-cased", () => {
    renderPanel(
      scored(),
      company(
        noValue("shares_outstanding", {
          reason: "no-annual-filing",
          label: "No value yet: no 10-K, 10-Q, 20-F or 40-F on EDGAR",
        }),
        sharesFromFiling({ fact_type: "cash_burn" }),
      ),
    );
    expect(
      screen.getByText("Share count: no value yet (no 10-K, 10-Q, 20-F or 40-F on EDGAR)"),
    ).toBeInTheDocument();
  });

  it("a cash-generative company gets no line (it is not a gap)", () => {
    renderPanel(
      scored(),
      company(
        sharesFromFiling(),
        noValue("cash_burn", {
          reason: "cash-generative",
          label: "Runway does not apply: the company generated cash",
        }),
      ),
    );
    expect(screen.queryByText(/^Cash:/)).not.toBeInTheDocument();
  });

  it("an applied value with a newer filing waiting says so, quietly", () => {
    renderPanel(
      scored(),
      company(
        sharesFromFiling({
          pending: { event_date: "2026-12-31", line: "A newer filing (dated Dec 31, 2026) is awaiting the steward" },
        }),
        sharesFromFiling({ fact_type: "cash_burn" }),
      ),
    );
    expect(
      screen.getByText("Share count: a newer filing (dated Dec 31, 2026) is awaiting the steward"),
    ).toBeInTheDocument();
  });

  it("a blank purity: 'Purity: not set yet (yours to set in the Workbench)'; a set purity: no line", () => {
    const { unmount } = renderPanel(
      scored({ purity: fig(null, null) }),
      company(sharesFromFiling(), sharesFromFiling({ fact_type: "cash_burn" })),
    );
    expect(screen.getByText("Purity: not set yet (yours to set in the Workbench)")).toBeInTheDocument();
    unmount();
    const { container } = renderPanel(
      scored(),
      company(sharesFromFiling(), sharesFromFiling({ fact_type: "cash_burn" })),
    );
    expect(screen.queryByText(/^Purity:/)).not.toBeInTheDocument();
    expect(lines(container)).toBeNull(); // nothing applies: the block itself is absent
  });

  it("before the read lands, only the purity line can apply (never a guess about the other two)", () => {
    renderPanel(scored({ purity: fig(null, null) }), undefined);
    expect(screen.getByText(/^Purity: not set yet/)).toBeInTheDocument();
    expect(screen.queryByText(/^Share count:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Cash:/)).not.toBeInTheDocument();
  });

  it("an ETF sleeve gets no purity line (it has no purity)", () => {
    renderPanel(scored({ purity: fig(null, null), instrument_kind: "etf" } as never), undefined);
    expect(screen.queryByText(/^Purity:/)).not.toBeInTheDocument();
  });

  it("a dated (as-of) view says nothing of today's state: the lines are for the current view only", () => {
    const { container } = renderPanel(
      scored({ purity: fig(null, null) }),
      company(noValue("shares_outstanding"), noValue("cash_burn")),
      { asof: "2026-06-01" },
    );
    expect(lines(container)).toBeNull();
    expect(screen.queryByText(/^Cash:|^Share count:|^Purity:/)).not.toBeInTheDocument();
  });

  it("the lines are gray text, not alerts", () => {
    const { container } = renderPanel(scored(), company(noValue("shares_outstanding"), noValue("cash_burn")));
    const block = lines(container) as HTMLElement;
    expect(block).not.toBeNull();
    expect(block.querySelector('[role="alert"], .np-thinflag, .flag')).toBeNull();
  });
});
