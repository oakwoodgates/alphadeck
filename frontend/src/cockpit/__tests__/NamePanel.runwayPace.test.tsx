import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// V2 extractor correctness PR 3: the Cockpit name panel's scoring snapshot reads the runway at last quarter's pace,
// the year-to-date pace beside it, and the caveat only when last quarter is far off that pace.
vi.mock("../../api/hooks", () => ({
  useFundLaunches: () => ({ data: undefined, isLoading: false, error: null }),
  useDecisions: () => ({ data: [] }),
}));

import type { ScoredMemberOut } from "../../api/hooks";
import { bucketDef, type BucketRow } from "../buckets";
import { NamePanel } from "../NamePanel";

const CAVEAT = "Last quarter's burn ($17.1M) is well below the year-to-date pace ($69.9M a quarter): runway reads 9.1 months at last quarter's pace and 2.2 months at the year-to-date pace.";
const fig = { pips: null, value: null, provenance: [] };
const scored = (runway: unknown): ScoredMemberOut =>
  ({
    security_id: "s-up",
    ticker: "UP",
    name: "Wheels Up",
    sector: null,
    exchange: null,
    category: null,
    origin: null,
    foreign_filer_form: null,
    segment: null,
    purity: fig,
    runway,
    catalysts: fig,
    dilution: fig,
    market_cap: fig,
    fit: "",
    unconfirmed_estimates: 0,
  }) as unknown as ScoredMemberOut;

const rowFor = (sc: ScoredMemberOut): BucketRow => ({
  member: {
    ticker: "UP",
    role: "leader",
    security_id: sc.security_id,
    segment: null,
    conviction: null,
    authored_by: "operator_set",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any,
  ordinal: 0,
  call: null,
  scored: sc,
  bucket: "quiet",
});

const renderPanel = (runway: unknown) =>
  render(
    <NamePanel
      row={rowFor(scored(runway))}
      def={bucketDef("quiet")}
      card={undefined}
      thesisId="t1"
      position={null}
      display={null}
      asof="2026-11-15"
      onClose={vi.fn()}
    />,
  );

const runway = (detail: Record<string, unknown>) => ({
  pips: 1,
  value: 9.1,
  provenance: [{ source: "10-q", ref: "https://www.sec.gov/x.htm", detail }],
});

describe("NamePanel — the runway at last quarter's pace", () => {
  it("shows the label, the year-to-date pace and the caveat", () => {
    renderPanel(runway({ ytd_quarterly_burn_usd: 69_943_025, ytd_runway_months: 2.2, pace_caveat: CAVEAT }));
    expect(screen.getByText("9.1 mo at last quarter's pace")).toBeInTheDocument();
    expect(screen.getByTestId("np-runway-pace")).toHaveTextContent(
      "Year-to-date pace: $69.9M burn a quarter, 2.2 mo of runway",
    );
    expect(screen.getByTestId("np-runway-caveat")).toHaveTextContent(CAVEAT);
  });

  it("speaks a yuan value's own currency and its split (GAP 3)", () => {
    render(
      <NamePanel
        row={rowFor(
          scored({
            pips: 4,
            value: 72,
            provenance: [
              {
                source: "annual-statements",
                ref: "https://www.sec.gov/y.htm",
                detail: {
                  cash_usd: 1_200_000_000,
                  quarterly_burn_usd: 50_000_000,
                  currency: "CNY",
                  investments_usd: 300_000_000,
                  ytd_quarterly_burn_usd: 40_000_000,
                },
              },
            ],
          }),
        )}
        def={bucketDef("quiet")}
        card={undefined}
        thesisId="t1"
        position={null}
        display={null}
        asof="2026-11-15"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("np-runway-cash")).toHaveTextContent(
      "Cash CN¥1.2B, of which short-term investments CN¥300.0M; burn CN¥50.0M last quarter",
    );
    expect(screen.getByTestId("np-runway-pace")).toHaveTextContent("Year-to-date pace: CN¥40.0M burn a quarter");
  });

  it("shows no caveat for a steady quarter and nothing more without a pace", () => {
    const { unmount } = renderPanel(runway({ ytd_quarterly_burn_usd: 88_779_250, ytd_runway_months: 18.6 }));
    expect(screen.getByTestId("np-runway-pace")).toBeInTheDocument();
    expect(screen.queryByTestId("np-runway-caveat")).toBeNull();
    unmount();
    renderPanel(runway({}));
    expect(screen.queryByTestId("np-runway-pace")).toBeNull();
  });
});
