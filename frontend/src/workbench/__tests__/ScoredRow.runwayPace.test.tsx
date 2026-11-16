import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// V2 extractor correctness PR 3: on the Workbench's scored row the runway meter's hover reads the runway at last
// quarter's pace (with the year-to-date pace when there is one), and a quiet "uneven quarter" mark appears ONLY
// when the pace caveat applies (honest loudness: the minority, never every row). The pips never change.
vi.mock("../../api/hooks", () => ({
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { ScoredRow } from "../ScoredRow";

const CAVEAT = "Last quarter's burn ($17.1M) is well below the year-to-date pace ($69.9M a quarter): runway reads 9.1 months at last quarter's pace and 2.2 months at the year-to-date pace.";
const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
const runway = (detail: Record<string, unknown>) => ({
  pips: 1,
  value: 9.1,
  provenance: [{ source: "10-q", ref: "https://www.sec.gov/x.htm", detail }],
});
const member = (rw: unknown) => ({
  security_id: "s-up",
  ticker: "UP",
  name: "Wheels Up",
  instrument_kind: "equity",
  segment: null,
  purity: fig(null, null),
  runway: rw,
  catalysts: fig(null, null),
  dilution: fig(null, null),
  market_cap: fig(null, null),
  fit: "",
  unconfirmed_estimates: 0,
});

function renderRow(rw: unknown) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return render(<ScoredRow member={member(rw) as any} selected={false} onSelect={() => {}} />);
}

describe("ScoredRow — the runway at last quarter's pace", () => {
  it("marks an uneven quarter quietly, the caveat on hover", () => {
    renderRow(runway({ ytd_quarterly_burn_usd: 69_943_025, ytd_runway_months: 2.2, pace_caveat: CAVEAT }));
    const mark = screen.getByTestId("wb-runway-pace");
    expect(mark).toHaveTextContent("uneven quarter");
    expect(mark).toHaveAttribute("title", CAVEAT);
    expect(screen.getByTestId("wb-meter-runway")).toHaveAttribute(
      "title",
      "Runway 9.1 mo at last quarter's pace. Year-to-date pace: $69.9M burn a quarter, 2.2 mo of runway",
    );
  });

  it("has no mark on a steady quarter or a value without a pace", () => {
    const { unmount } = renderRow(runway({ ytd_quarterly_burn_usd: 88_779_250, ytd_runway_months: 18.6 }));
    expect(screen.queryByTestId("wb-runway-pace")).toBeNull();
    unmount();
    renderRow(runway({}));
    expect(screen.queryByTestId("wb-runway-pace")).toBeNull();
    expect(screen.getByTestId("wb-meter-runway")).toHaveAttribute("title", "Runway 9.1 mo at last quarter's pace");
  });
});
