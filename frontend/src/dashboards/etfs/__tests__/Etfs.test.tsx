import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Etfs from "../Etfs";
import type {
  EtfCompareRowOut,
  EtfFlowRowOut,
  EtfSizeRowOut,
  EtfTrendRowOut,
  EtfsOut,
} from "../useEtfs";

// The ETFs body with its hook stubbed: five panels that stay on the page, the window switch threading into the
// read, panel 1's default order and sort options, a card and a fund opening the Cockpit (the fund with its ticker),
// flow bars only for a real flow, a filling window's words, the flagged-names note, and the quiet empty sentences.
// The hook's request has its own test.

const h = vi.hoisted(() => ({ useEtfs: vi.fn() }));
vi.mock("../useEtfs", () => ({ useEtfs: h.useEtfs }));

const reading = (value: number | null, note: string | null = null) => ({ value, note });
const NONE = reading(null, "n/a: 9/30 sampled days");

const cmp = (id: string, card: string, over: Partial<EtfCompareRowOut> = {}): EtfCompareRowOut => ({
  thesis_id: `t-${card}`,
  thesis_name: card,
  security_id: id,
  ticker: id.toUpperCase(),
  fund_name: "Synthetic Fund",
  ret: 5,
  companies_ret: 3,
  diff: 2,
  companies: 4,
  priced: 3,
  unresolved: 1,
  sessions_flat: 0,
  bars: 22,
  bars_needed: 22,
  note: null,
  fund_points: [100, 103, 105],
  company_points: [100, 102, 103],
  ...over,
});

const refs = (...names: string[]) => names.map((n) => ({ thesis_id: `t-${n}`, thesis_name: n, ticker: "X" }));

const flow = (id: string, over: Partial<EtfFlowRowOut> = {}): EtfFlowRowOut => ({
  security_id: id,
  ticker: id.toUpperCase(),
  name: null,
  cards: refs("Alpha"),
  flow_1w_usd: reading(1_000_000),
  flow_1w_pct: reading(1.5),
  flow_1m_usd: reading(4_000_000),
  flow_1m_pct: reading(6),
  filling_1w: false,
  filling_1m: false,
  samples: 40,
  first_sample: "2026-04-01",
  latest_sample: "2026-10-28",
  source: "fund-site",
  ...over,
});

const size = (id: string, over: Partial<EtfSizeRowOut> = {}): EtfSizeRowOut => ({
  security_id: id,
  ticker: id.toUpperCase(),
  name: null,
  cards: refs("Alpha"),
  shares: 1e6,
  sample_date: "2026-10-28",
  size_usd: 2.5e8,
  size_note: null,
  adv_usd_20d: reading(3e6),
  ...over,
});

const trend = (id: string, over: Partial<EtfTrendRowOut> = {}): EtfTrendRowOut => ({
  security_id: id,
  ticker: id.toUpperCase(),
  name: null,
  cards: refs("Alpha"),
  ret_30d: reading(4.2),
  vs_50d: reading(-1),
  vs_200d: reading(null, "n/a: 140/200 bars"),
  rvol20: reading(1.25),
  range_pos: reading(62),
  ...over,
});

const payload = (over: Partial<EtfsOut> = {}): EtfsOut => ({
  asof: "2026-10-28",
  window: "1m",
  sessions: 2,
  dates: ["2026-10-26", "2026-10-27", "2026-10-28"],
  session_source: "benchmarks",
  live_cards: 4,
  cards_counted: 4,
  cards_not_yet_created: 0,
  roster_fallback_cards: 0,
  funds: 2,
  companies: 9,
  iwm: { available: true, reason: null, ret: 1.5 },
  compare: [cmp("fnda", "Alpha", { diff: 2 }), cmp("fndb", "Beta", { diff: 9 })],
  flows: [flow("fnda"), flow("fndb", { flow_1m_usd: reading(-8_000_000), flow_1m_pct: reading(-3) })],
  sizes: [size("fnda"), size("fndb", { size_usd: null, size_note: "no shares sample yet", shares: null })],
  trends: [trend("fndb"), trend("fnda")],
  coverage: {
    cards_counted: 4,
    cards_with_fund: 2,
    funds: 2,
    without: [
      { thesis_id: "t-Gamma", thesis_name: "Gamma" },
      { thesis_id: "t-Delta", thesis_name: "Delta" },
    ],
    multi: [{ security_id: "fnda", ticker: "FNDA", name: null, cards: refs("Alpha", "Beta") }],
  },
  flagged_funds: [],
  flagged_companies: [],
  window_sessions: { "1w": 5, "1m": 21, "3m": 63 },
  cache: "hit",
  compute_ms: 900,
  ...over,
});

function setup(data: EtfsOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useEtfs.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<Etfs asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const panel = (id: string) => screen.getByTestId(`et-panel-${id}`);

beforeEach(() => h.useEtfs.mockReset());

describe("Etfs", () => {
  it("renders all five panels with their counts and the quiet show-the-work line", () => {
    setup();
    for (const id of ["compare", "flow", "size", "trend", "coverage"]) expect(panel(id)).toBeInTheDocument();
    expect(within(panel("compare")).getByText("2 pairs")).toBeInTheDocument();
    expect(within(panel("coverage")).getByText("2 of 4 cards")).toBeInTheDocument();
    const line = screen.getByText(/4 live cards counted/);
    expect(line.textContent).toContain("2 funds found (a fund = a row whose security is an ETF, in any link)");
    expect(line.textContent).toContain("Context and Bench names are not a card's companies");
    expect(line.textContent).toContain("as of Oct 28, 2026");
    expect(line.textContent).toContain("cached");
  });

  it("orders panel 1 by fund minus companies, highest first, and shows the numbers and IWM", () => {
    setup();
    const rows = within(panel("compare")).getAllByTestId("et-compare-row");
    expect(rows.map((r) => within(r).getAllByRole("button")[0].textContent)).toEqual(["Beta", "Alpha"]);
    expect(within(rows[0]).getByText(/fund minus companies \+9\.0 pts/)).toBeInTheDocument();
    expect(within(rows[0]).getByText("3/4 priced")).toBeInTheDocument();
    expect(within(panel("compare")).getByText(/IWM \+1\.5% over 1M/)).toBeInTheDocument();
  });

  it("threads the window and re-sorts panel 1 from its buttons", async () => {
    const user = userEvent.setup();
    setup();
    expect(h.useEtfs).toHaveBeenLastCalledWith("2026-10-28", "1m");
    await user.click(screen.getByRole("button", { name: "3M" }));
    expect(h.useEtfs).toHaveBeenLastCalledWith("2026-10-28", "3m");
    await user.click(screen.getByRole("button", { name: /Card A→Z/ }));
    const rows = within(panel("compare")).getAllByTestId("et-compare-row");
    expect(rows.map((r) => within(r).getAllByRole("button")[0].textContent)).toEqual(["Alpha", "Beta"]);
  });

  it("opens the card on a card name and the card's Cockpit at the fund on a fund ticker", async () => {
    const user = userEvent.setup();
    const { onSelectThesis } = setup();
    const row = within(panel("compare")).getAllByTestId("et-compare-row")[1]; // Alpha
    await user.click(within(row).getByRole("button", { name: "Alpha" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-Alpha");
    await user.click(within(row).getByRole("button", { name: "FNDA" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-Alpha", "FNDA");
    await user.click(within(panel("coverage")).getByRole("button", { name: "Gamma" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-Gamma");
  });

  it("draws a flow bar only for a real flow, green right for inflow and red left for outflow", () => {
    setup(
      payload({
        flows: [
          flow("fnda"),
          flow("fndb", { flow_1m_usd: reading(-8_000_000), flow_1m_pct: reading(-3) }),
          flow("fndc", { flow_1m_usd: reading(0), flow_1m_pct: reading(0), flow_1w_usd: reading(0), flow_1w_pct: reading(0) }),
        ],
      }),
    );
    const rows = within(panel("flow")).getAllByTestId("et-flow-row");
    expect(within(rows[0]).getByTestId("et-flow-bar")).toHaveClass("pos");
    expect(within(rows[1]).getByTestId("et-flow-bar")).toHaveClass("neg");
    expect(within(rows[2]).queryByTestId("et-flow-bar")).toBeNull();
    expect(within(rows[0]).getByText(/1M \+\$4\.0M \(\+6\.0% of shares\)/)).toBeInTheDocument();
    expect(within(rows[1]).getByText(/1M −\$8\.0M/)).toBeInTheDocument();
  });

  it("keeps a row with one flow number and says only '1M filling' for the window still filling", () => {
    setup(
      payload({
        flows: [flow("fnda", { flow_1m_usd: NONE, flow_1m_pct: NONE, filling_1m: true })],
      }),
    );
    const row = within(panel("flow")).getByTestId("et-flow-row");
    expect(within(row).getByText("1M filling")).toBeInTheDocument();
    expect(within(row).getByText(/1W \+\$1\.0M/)).toBeInTheDocument();
    expect(within(row).queryByTestId("et-flow-bar")).toBeNull();
    expect(screen.queryByText(/sampled days/)).toBeNull();
    expect(screen.queryByTestId("et-filling")).toBeNull();
  });

  it("groups funds with no flow number yet into one quiet line per first-sample date, each ticker a button", async () => {
    const user = userEvent.setup();
    const none = (id: string, first: string) =>
      flow(id, {
        flow_1w_usd: NONE, flow_1w_pct: NONE, flow_1m_usd: NONE, flow_1m_pct: NONE,
        filling_1w: true, filling_1m: true, first_sample: first, cards: refs("Alpha"),
      });
    const { onSelectThesis } = setup(
      payload({ flows: [flow("fnda"), none("fndb", "2026-11-04"), none("fndc", "2026-11-04"), none("fndd", "2026-11-10")] }),
    );
    expect(within(panel("flow")).getByText("4 funds")).toBeInTheDocument(); // the count still counts every fund
    expect(within(panel("flow")).getAllByTestId("et-flow-row")).toHaveLength(1);
    const lines = within(panel("flow")).getAllByTestId("et-filling");
    expect(lines).toHaveLength(2);
    expect(lines[0].textContent).toContain(
      "Still filling, sampled since Nov 4, 2026: 1W reads from about Nov 11, 1M from about Dec 4 · 2 funds: FNDB, FNDC",
    );
    expect(lines[1].textContent).toContain("sampled since Nov 10, 2026: 1W reads from about Nov 17, 1M from about Dec 10 · 1 fund: FNDD");
    await user.click(within(lines[0]).getByRole("button", { name: "FNDC" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-Alpha", "X");
  });

  it("keeps each empty panel on the page with a quiet sentence", () => {
    setup(
      payload({
        funds: 0,
        compare: [],
        flows: [],
        sizes: [],
        trends: [],
        coverage: { cards_counted: 2, cards_with_fund: 0, funds: 0, without: [], multi: [] },
      }),
    );
    for (const id of ["compare", "flow", "size", "trend", "coverage"]) expect(panel(id)).toBeInTheDocument();
    expect(within(panel("compare")).getByText("No card has a fund at this as-of.")).toBeInTheDocument();
    expect(within(panel("flow")).getByText("No fund to show.")).toBeInTheDocument();
    expect(within(panel("coverage")).getByText("No fund sits on more than one card.")).toBeInTheDocument();
  });

  it("says when no fund has a shares sample yet", () => {
    setup(payload({ flows: [flow("fnda", { samples: 0, latest_sample: null, first_sample: null, flow_1w_usd: reading(null, "no data"), flow_1m_usd: reading(null, "no data") })] }));
    expect(within(panel("flow")).getByText("No fund has a shares sample yet.")).toBeInTheDocument();
    expect(within(panel("flow")).getByText("no shares sample yet")).toBeInTheDocument();
  });

  it("lists the names left out for a flagged price history, never silently gone", async () => {
    const user = userEvent.setup();
    setup(
      payload({
        flagged_funds: [{ security_id: "fndz", ticker: "FNDZ", reason: "mixed-scale price history" }],
        flagged_companies: [{ security_id: "coz", ticker: "COZ1", reason: "price feed stopped" }],
      }),
    );
    // panel 1 names both; panels 2 to 4 name the funds
    const note = within(panel("compare")).getByText("2 names left out for a flagged price history");
    await user.click(note);
    expect(within(panel("compare")).getByText("COZ1")).toBeInTheDocument();
    expect(within(panel("flow")).getByText("1 name left out for a flagged price history")).toBeInTheDocument();
    expect(within(panel("size")).getByText("1 name left out for a flagged price history")).toBeInTheDocument();
    expect(within(panel("trend")).getByText("1 name left out for a flagged price history")).toBeInTheDocument();
  });

  it("shows no flagged note when no name is left out, and the trend table sorts by a column", async () => {
    const user = userEvent.setup();
    setup();
    expect(screen.queryByText(/left out for a flagged price history/)).toBeNull();
    const order = () =>
      within(panel("trend"))
        .getAllByTestId("et-trend-row")
        .map((r) => r.querySelector(".et-tk")?.textContent);
    expect(order()).toEqual(["FNDA", "FNDB"]); // the fund A to Z, whatever order the data came in
    const col = within(panel("trend")).getByRole("button", { name: /30d/ });
    await user.click(col);
    expect(col).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the loading and error states", () => {
    h.useEtfs.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    const { unmount } = render(<Etfs asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/Reading the funds/)).toBeInTheDocument();
    unmount();
    h.useEtfs.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    render(<Etfs asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the ETFs page.")).toBeInTheDocument();
  });
});
