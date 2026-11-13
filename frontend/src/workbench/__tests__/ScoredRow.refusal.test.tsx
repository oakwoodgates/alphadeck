import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ScoredRow shows the API's fixed refusals on get data (security hardening PR 1b) as a visible line, worded as the
// API words them: the own-cards 404 on the extract, the daily-allowance 429 on the price pull. Any other failure
// keeps today's quiet retry control.
const h = vi.hoisted(() => ({
  extract: { data: undefined as unknown, error: null as unknown, isFetching: false },
  price: { isError: false, error: null as unknown },
}));
vi.mock("../../api/hooks", () => ({
  useExtract: () => ({ ...h.extract, refetch: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, ...h.price }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { ScoredRow } from "../ScoredRow";

const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
const member = {
  security_id: "s-oklo",
  ticker: "OKLO",
  name: "Oklo Inc.",
  instrument_kind: "equity",
  segment: null,
  purity: fig(null, null),
  runway: fig(null, null),
  catalysts: fig(null, null),
  dilution: fig(null, null),
  market_cap: fig(null, null),
  fit: "",
  unconfirmed_estimates: 0,
};

const NOT_ON = "This company is not on any of your cards. Add it to a card first.";
const LIMIT = "Daily allowance reached: 5,000 price pulls a day. It resets at midnight America/New_York time.";

function renderRow() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  render(<ScoredRow member={member as any} selected={false} onSelect={() => {}} />);
}

describe("ScoredRow — the fetch refusals", () => {
  beforeEach(() => {
    h.extract = { data: undefined, error: null, isFetching: false };
    h.price = { isError: false, error: null };
  });

  it("shows the own-cards 404 on the extract plainly", () => {
    h.extract = { data: undefined, error: { detail: NOT_ON }, isFetching: false };
    renderRow();
    expect(screen.getByText(NOT_ON)).toBeInTheDocument();
  });

  it("shows the daily-allowance 429 on the price pull plainly", () => {
    h.price = { isError: true, error: { detail: LIMIT } };
    renderRow();
    expect(screen.getByText(LIMIT)).toBeInTheDocument();
  });

  it("keeps any other failure in the quiet retry control", () => {
    h.extract = { data: undefined, error: { detail: "extraction failed: boom" }, isFetching: false };
    renderRow();
    expect(screen.queryByText(/extraction failed/)).not.toBeInTheDocument(); // only in the hover title
    expect(screen.getByText(/retry get data/)).toBeInTheDocument();
  });
});
