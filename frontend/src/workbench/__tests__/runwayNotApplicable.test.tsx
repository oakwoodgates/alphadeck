import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// V2 C1: a company a runway means nothing for (a bank, insurer, broker, exchange, asset manager, lender or fund
// trust). The scored read gives its runway no pips and a `runway:not-applicable` entry with the sentence; the row
// reads a plain "n/a" with the sentence on hover (never "—", never a warning), its cash stays on file.
vi.mock("../../api/hooks", () => ({
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { ScoredRow } from "../ScoredRow";
import { memberHasFundamentals, meterValueLabel, runwayNotApplicable, runwayTitle } from "../format";

const NOTE =
  "Runway does not apply to this kind of company (a bank, insurer, broker, exchange, asset manager, lender or fund trust): its cash moves with customer money, loans or fund flows, not with spending.";
const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
const na = {
  pips: null,
  value: null,
  provenance: [
    { source: "10-q", ref: "https://www.sec.gov/x.htm", detail: { cash_usd: 12_000_000, quarterly_burn_usd: 3_000_000 } },
    { source: "computed", ref: "runway:not-applicable", detail: { note: NOTE } },
  ],
};
const member = (rw: unknown) => ({
  security_id: "s-bank",
  ticker: "BANK",
  name: "A Bank",
  instrument_kind: "equity",
  segment: null,
  purity: fig(null, null),
  runway: rw,
  catalysts: fig(null, null),
  dilution: fig(null, null),
  market_cap: fig(null, null),
  fit: "unrated",
});

describe("runway does not apply", () => {
  it("the scored row reads n/a with the sentence on hover, no pace mark", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<ScoredRow member={member(na) as any} selected={false} onSelect={() => {}} />);
    const meter = screen.getByTestId("wb-meter-runway");
    expect(meter).toHaveTextContent("n/a");
    expect(meter).not.toHaveTextContent("—");
    expect(meter).toHaveAttribute("title", NOTE);
    expect(screen.queryByTestId("wb-runway-pace")).toBeNull();
  });

  it("the words: n/a, the sentence, and the cash still counts as on file", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const f = na as any;
    expect(runwayNotApplicable(f)).toBe(NOTE);
    expect(meterValueLabel("runway", f)).toBe("n/a");
    expect(runwayTitle(f)).toBe(NOTE);
    expect(memberHasFundamentals({ runway: f })).toBe(true);
    // unchanged for a runway that applies
    expect(runwayNotApplicable({ pips: 2, value: 9, provenance: [] })).toBeNull();
    expect(meterValueLabel("runway", { pips: 4, value: null, provenance: [] })).toBe("cash-generative last quarter");
    expect(memberHasFundamentals({ runway: { pips: null, provenance: [] } })).toBe(false);
  });
});
