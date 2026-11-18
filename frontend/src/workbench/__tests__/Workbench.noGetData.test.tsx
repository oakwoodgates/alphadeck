import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

// V2 W5e: the Workbench has no get-data chore anywhere: not on a row, not on the section bar, not in the rail. A
// share count and cash arrive from the platform (the read-only Shared values block); purity is the user's task.
// This renders the whole screen with the real rows, section bar and rail, and searches all of it.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const member = (sid: string, ticker: string, segment: string) => ({
    security_id: sid,
    ticker,
    name: `${ticker} Corp`,
    segment,
    instrument_kind: "equity",
    purity: fig(null, null),
    runway: fig(null, null),
    catalysts: fig(0, 0),
    dilution: fig(null, null),
    market_cap: fig(null, null),
    fit: "unscored",
    unconfirmed_estimates: 0,
  });
  const segments = [
    { label: "DRAM & HBM Makers", descriptor: null },
    { label: "NAND & Storage", descriptor: null },
  ];
  const members = [
    member("s-mu", "MU", "DRAM & HBM Makers"),
    member("s-mram", "MRAM", "DRAM & HBM Makers"),
    member("s-sndk", "SNDK", "NAND & Storage"),
  ];
  const thesis = {
    id: "t1",
    name: "AI Memory",
    narrative: "n",
    ticker: null,
    segments,
    basket: [],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
    term_set: [] as unknown[],
  };
  const scored = { thesis_id: "t1", asof: "2026-06-08", segments, members };
  const noValue = (fact_type: string) => ({
    fact_type,
    state: "no_value",
    reason: "not-extracted",
    label: "No value yet: the platform has not read this company's filings yet",
    set_by: null,
    value: null,
    annual_line: null,
    pending: null,
    awaiting: null,
    my_flag: null,
  });
  const values = (sid: string) => ({
    security_id: sid,
    ticker: null,
    name: null,
    shares: noValue("shares_outstanding"),
    cash_burn: noValue("cash_burn"),
  });
  return { thesis, scored, values };
});

const none = { mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false, error: null };

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: () => {}, isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t1", name: "AI Memory", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  // the user's reads of the shared values and the flag mutations
  useUserThesisValues: () => ({
    data: { thesis_id: "t1", members: ["s-mu", "s-mram", "s-sndk"].map(fx.values) },
    isLoading: false,
    error: null,
  }),
  useUserCompanyValues: (sid: string | undefined) => ({ data: sid ? fx.values(sid) : undefined, isLoading: false, error: null }),
  useRaiseFlag: () => none,
  useWithdrawFlag: () => none,
}));

import { Workbench } from "../Workbench";

const GET_DATA = /get data|data ready|retry get data|⇣/i;

describe("Workbench — no get-data control exists anywhere", () => {
  it("not on the rows, not on the section bar, not in the rail", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter>
        <Workbench asof="2026-06-08" />
      </MemoryRouter>,
    );

    // rows + section bar
    expect(screen.getAllByTestId("wb-meter-runway").length).toBeGreaterThan(0); // the rows are there
    expect(screen.queryByRole("button", { name: GET_DATA })).not.toBeInTheDocument();
    expect(container.querySelector(".wb-getdata")).toBeNull();
    expect(container.querySelector(".wb-section-data")).toBeNull();
    expect(screen.queryByText(GET_DATA)).not.toBeInTheDocument();

    // the rail, once a name is open
    await user.click(screen.getByRole("button", { name: /^MU/ }));
    const rail = container.querySelector(".ddcard") as HTMLElement;
    expect(rail).not.toBeNull();
    expect(within(rail).queryByRole("button", { name: GET_DATA })).not.toBeInTheDocument();
    expect(within(rail).queryByText(GET_DATA)).not.toBeInTheDocument();
    expect(within(rail).getByText("Shared values")).toBeInTheDocument();
    expect(within(rail).getByText("Purity (yours to set)")).toBeInTheDocument();
    expect(within(rail).queryByText(/Extract & ratify the facts/)).not.toBeInTheDocument();
    expect(within(rail).getByRole("button", { name: /Extract from filings/ })).toBeInTheDocument(); // purity's own
    // the whole screen, once more
    expect(document.body.textContent).not.toMatch(GET_DATA);
  });

  it("the many companies with 'no value yet' read quiet: no error, no alert, no warning mark", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter>
        <Workbench asof="2026-06-08" />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: /^MU/ }));
    const rail = container.querySelector(".ddcard") as HTMLElement;
    expect(within(rail).getAllByText(/No value yet: the platform has not read this company's filings yet\./)).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(rail.querySelector(".sv-block .err, .sv-block .loud, .sv-block .warn")).toBeNull();
    expect(container.querySelector(".wb-stale-shares")).toBeNull();
  });
});
