import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Leaders from "../Leaders";
import type { LeadersInsiderGroupOut, LeadersNameOut, LeadersOut } from "../useLeaders";

// The Leaders body with its one hook stubbed: the three sections, the several-lists table with its quiet list
// chips, the list tabs and their columns, a header click re-sorting, the insider cluster accent, the
// insider-cluster grouping selector and the silent toggle threading their params, the quiet lines, and a name
// or a card chip opening the Cockpit. The hook's request and the sorts have their own tests.

const h = vi.hoisted(() => ({ useLeaders: vi.fn() }));
vi.mock("../useLeaders", () => ({ useLeaders: h.useLeaders }));

const name = (ticker: string, over: Partial<LeadersNameOut> = {}): LeadersNameOut => ({
  security_id: `s-${ticker}`,
  ticker,
  name: `${ticker} Corp`,
  cards: [{ thesis_id: "t-grid", thesis_name: "Grid", ticker, links: ["Turbines"] }],
  lists: ["near_high"],
  pct_off_high: -1.0,
  high_52w: 40,
  high_date: "2026-10-20",
  range_bars: 260,
  rs_vs: [],
  contraction: null,
  buys_30d: 0,
  buyers_30d: 0,
  cluster: false,
  rvol20: 1.1,
  ret_7d: 1.0,
  ret_30d: 2.0,
  above_50d: true,
  ...over,
});

const NAMES: LeadersNameOut[] = [
  name("AAA", {
    lists: ["near_high", "rs_leader", "volume"],
    rs_vs: ["SPY"],
    rvol20: 2.4,
    ret_30d: 1.0,
    cards: [
      { thesis_id: "t-grid", thesis_name: "Grid", ticker: "AAA", links: ["Turbines", "Cables"] },
      { thesis_id: "t-nuke", thesis_name: "Nuclear", ticker: "AAA", links: ["Fuel"] },
    ],
  }),
  name("BBB", {
    lists: ["near_high", "insider"],
    pct_off_high: 0,
    buys_30d: 3,
    buyers_30d: 2,
    cluster: true,
    ret_30d: 12.0,
  }),
  name("CCC", { lists: ["near_high"], pct_off_high: -1.8, range_bars: 120, ret_30d: 30.0 }),
  name("DDD", { lists: ["insider"], buys_30d: 1, buyers_30d: 1, ret_30d: -3.0 }),
];

const group = (key: string, over: Partial<LeadersInsiderGroupOut> = {}): LeadersInsiderGroupOut => ({
  key,
  label: key,
  link: "Turbines",
  thesis_id: "t-grid",
  thesis_name: "Grid",
  business_type: null,
  supersector: null,
  names: 5,
  with_buys: 2,
  no_insider_data: 1,
  share: 0.4,
  buys: 4,
  members: [
    {
      security_id: "s-BBB",
      ticker: "BBB",
      name: "BBB Corp",
      cards: [{ thesis_id: "t-grid", thesis_name: "Grid", ticker: "BBB", links: ["Turbines"] }],
      buys_30d: 3,
      buyers_30d: 2,
      cluster: true,
    },
    {
      security_id: "s-DDD",
      ticker: "DDD",
      name: "DDD Corp",
      cards: [{ thesis_id: "t-grid", thesis_name: "Grid", ticker: "DDD", links: ["Turbines"] }],
      buys_30d: 1,
      buyers_30d: 1,
      cluster: false,
    },
  ],
  ...over,
});

const list = (key: LeadersOut["lists"][number]["key"], member: string, ids: string[], noReading: number) => ({
  key,
  member,
  security_ids: ids,
  no_reading: noReading,
});

const DATA: LeadersOut = {
  asof: "2026-10-28",
  group_by: "link",
  include_silent: false,
  live_cards: 62,
  cards_counted: 62,
  cards_not_yet_created: 0,
  roster_fallback_cards: 0,
  basket_rows: 1600,
  silent_rows_left_out: 40,
  unresolved_rows: 3,
  companies: 1150,
  names: NAMES,
  several: ["s-AAA", "s-BBB"],
  lists: [
    list("near_high", "range_52w", ["s-BBB", "s-AAA", "s-CCC"], 12),
    list("rs_leader", "relative_strength", ["s-AAA"], 40),
    list("coiling", "vcp", [], 30),
    list("insider", "insider_flow_90d", ["s-BBB", "s-DDD"], 200),
    list("volume", "rvol", ["s-AAA"], 15),
  ],
  insider_groups: [group(`link:t-grid:Turbines`)],
  near_high_pct: -2.0,
  rvol_loud: 1.5,
  insider_min_buys: 1,
  cluster_min_buyers: 2,
  group_min_names: 2,
  multi_min_lists: 2,
  full_year_bars: 245,
  cache: "hit",
  compute_ms: 52000,
};

const query = (data: LeadersOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () => render(<Leaders asof="2026-10-28" onSelectThesis={onSelectThesis} />);

/** A table's tickers, top to bottom. */
const tickers = (table: string) =>
  within(screen.getByRole("table", { name: table }))
    .getAllByRole("row")
    .slice(1)
    .map((r) => r.querySelector(".ld-tk")?.textContent);

beforeEach(() => {
  h.useLeaders.mockReset();
  h.useLeaders.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("Leaders — the sections", () => {
  it("renders the three sections, several lists on top", () => {
    renderPage();
    const titles = screen.getAllByRole("heading", { level: 2 }).map((x) => x.textContent);
    expect(titles).toEqual(["On several lists", "Lists", "Insider clusters by group"]);
  });

  it("lists the names on two or more lists, by the number of lists, with quiet list chips", () => {
    renderPage();
    expect(tickers("On several lists")).toEqual(["AAA", "BBB"]);
    const aaa = within(screen.getByRole("table", { name: "On several lists" })).getAllByRole("row")[1];
    expect(within(aaa).getByText("52w high")).toHaveClass("ld-list-chip");
    expect(within(aaa).getByText("RS")).toBeInTheDocument();
    expect(within(aaa).getByText("volume")).toBeInTheDocument();
    expect(within(aaa).getByText("AAA Corp")).toBeInTheDocument();
    // one row per company, a chip per card with its links there
    expect(within(aaa).getByRole("button", { name: "Grid · Turbines, Cables" })).toBeInTheDocument();
    expect(within(aaa).getByRole("button", { name: "Nuclear · Fuel" })).toBeInTheDocument();
  });

  it("a header click re-sorts, and a second click flips it", async () => {
    renderPage();
    const table = screen.getByRole("table", { name: "On several lists" });
    await userEvent.click(within(table).getByRole("button", { name: "30D return" }));
    expect(tickers("On several lists")).toEqual(["BBB", "AAA"]);
    expect(within(table).getByRole("columnheader", { name: /30D return/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    await userEvent.click(within(table).getByRole("button", { name: /30D return/ }));
    expect(tickers("On several lists")).toEqual(["AAA", "BBB"]);
  });

  it("shows the work and each member's no-reading count on quiet lines", () => {
    renderPage();
    expect(
      screen.getByText(
        "62 live cards counted · 1,150 companies · Context and Bench left out (40 basket rows) · 3 basket rows " +
          "without a resolved security not counted · near high: within 2% of the 52w high · volume: RVOL 20D ≥ 1.5 · " +
          "insider cluster: 2+ buyers in 30d · insider group: 2+ names with buys · cached · as of Oct 28, 2026",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "no reading: range_52w 12 · relative_strength 40 · vcp 30 · insider_flow_90d 200 · rvol 15",
      ),
    ).toBeInTheDocument();
  });

  it("says so while the first load computes, on an error, and when today's roster stands in", () => {
    h.useLeaders.mockReturnValue(query(undefined, { isLoading: true }));
    const { unmount } = renderPage();
    expect(screen.getByText(/the first load after a run can take a minute/)).toBeInTheDocument();
    unmount();
    h.useLeaders.mockReturnValue(query(undefined, { error: new Error("x") }));
    const second = renderPage();
    expect(screen.getByText("Could not load the Leaders board.")).toBeInTheDocument();
    second.unmount();
    h.useLeaders.mockReturnValue(query({ ...DATA, cache: "computed", roster_fallback_cards: 9 }));
    renderPage();
    expect(screen.getByText(/computed in 52 s/)).toBeInTheDocument();
    expect(screen.getByText(/Today's roster stands in for 9 cards/)).toBeInTheDocument();
  });

  it("says so quietly when a section is empty", () => {
    h.useLeaders.mockReturnValue(query({ ...DATA, several: [], insider_groups: [] }));
    renderPage();
    expect(screen.getByText("No name is on 2 or more lists at this as-of.")).toBeInTheDocument();
    expect(
      screen.getByText("No group has 2 or more names with insider buys at this as-of."),
    ).toBeInTheDocument();
  });
});

describe("Leaders — the list tabs", () => {
  it("opens on Near 52w high with each tab's count, in the server's order, with the % off and the high date", () => {
    renderPage();
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Near 52w high3", "RS leader1", "Coiling0", "Insider buying2", "Volume1"]);
    expect(screen.getByRole("tab", { name: /Near 52w high/ })).toHaveAttribute("aria-selected", "true");
    expect(tickers("Near 52w high")).toEqual(["BBB", "AAA", "CCC"]);
    const table = screen.getByRole("table", { name: "Near 52w high" });
    expect(within(table).getByRole("columnheader", { name: /% off 52w high/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    const ccc = within(table).getAllByRole("row")[3];
    expect(within(ccc).getByText("-1.8%")).toBeInTheDocument();
    expect(within(ccc).getByText("120 bars")).toBeInTheDocument(); // a sub-year range says so
    expect(screen.getByText(/12 companies had no reading/)).toBeInTheDocument();
  });

  it("switches tabs, each with its own value column; the insider cluster reads leader-blue", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: /Insider buying/ }));
    expect(screen.getByRole("tab", { name: /Insider buying/ })).toHaveAttribute("aria-selected", "true");
    expect(tickers("Insider buying")).toEqual(["BBB", "DDD"]);
    const table = screen.getByRole("table", { name: "Insider buying" });
    expect(within(table).getByRole("columnheader", { name: /Buys\/buyers 30d/ })).toBeInTheDocument();
    expect(within(table).getByText("3/2")).toHaveClass("ld-ins", "cluster");
    expect(within(table).getByText("1/1")).not.toHaveClass("cluster");

    await userEvent.click(screen.getByRole("tab", { name: /RS leader/ }));
    expect(within(screen.getByRole("table", { name: "RS leader" })).getByText("SPY")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Volume/ }));
    const vol = screen.getByRole("table", { name: "Volume" });
    // the Volume list's own value IS the RVOL 20D column: shown once
    expect(within(vol).getAllByRole("columnheader", { name: /RVOL 20D/ })).toHaveLength(1);
    expect(within(vol).getByText("2.40×")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Coiling/ }));
    expect(screen.getByText("No names on this list at this as-of.")).toBeInTheDocument();
  });

  it("each tab keeps its own sort", async () => {
    renderPage();
    const near = () => screen.getByRole("table", { name: "Near 52w high" });
    await userEvent.click(within(near()).getByRole("button", { name: /30D return/ }));
    expect(tickers("Near 52w high")).toEqual(["CCC", "BBB", "AAA"]);
    await userEvent.click(screen.getByRole("tab", { name: /Insider buying/ }));
    expect(tickers("Insider buying")).toEqual(["BBB", "DDD"]);
    await userEvent.click(screen.getByRole("tab", { name: /Near 52w high/ }));
    expect(tickers("Near 52w high")).toEqual(["CCC", "BBB", "AAA"]);
  });
});

describe("Leaders — insider clusters, toggles and clicks", () => {
  it("shows a group's share, its names with buys/buyers and the ones without insider data", () => {
    renderPage();
    const table = screen.getByRole("table", { name: "Insider clusters by group" });
    const row = within(table).getAllByRole("row")[1];
    expect(within(row).getByText("Turbines · Grid")).toBeInTheDocument();
    expect(within(row).getByText("2 of 5")).toBeInTheDocument();
    expect(within(row).getByText("1 without insider data")).toBeInTheDocument();
    expect(within(row).getByText("40%")).toBeInTheDocument();
    expect(within(row).getByText("3/2")).toHaveClass("cluster");
  });

  it("the grouping selector asks the hook for that grouping, and the silent toggle threads its param", async () => {
    renderPage();
    expect(h.useLeaders).toHaveBeenLastCalledWith("2026-10-28", "link", false);
    const sel = screen.getByRole("group", { name: "Group by" });
    await userEvent.click(within(sel).getByRole("button", { name: "Sector" }));
    expect(h.useLeaders).toHaveBeenLastCalledWith("2026-10-28", "sector", false);
    await userEvent.click(within(sel).getByRole("button", { name: "Card" }));
    expect(h.useLeaders).toHaveBeenLastCalledWith("2026-10-28", "card", false);
    await userEvent.click(screen.getByRole("checkbox", { name: "Show Context and Bench" }));
    expect(h.useLeaders).toHaveBeenLastCalledWith("2026-10-28", "card", true);
  });

  it("a group label follows the server's view", () => {
    h.useLeaders.mockReturnValue(
      query({ ...DATA, group_by: "card", insider_groups: [group("card:t-grid", { link: null })] }),
    );
    renderPage();
    const table = screen.getByRole("table", { name: "Insider clusters by group" });
    expect(within(table).getByRole("columnheader", { name: /Card/ })).toBeInTheDocument();
    expect(within(table).getByText("Grid")).toBeInTheDocument();
  });

  it("a ticker opens its first card's Cockpit on it; a card chip opens that card", async () => {
    renderPage();
    const table = screen.getByRole("table", { name: "On several lists" });
    await userEvent.click(within(table).getByRole("button", { name: "AAA" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "AAA");
    await userEvent.click(within(table).getByRole("button", { name: "Nuclear · Fuel" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "AAA");
  });

  it("a name in an insider group opens its card", async () => {
    renderPage();
    const table = screen.getByRole("table", { name: "Insider clusters by group" });
    await userEvent.click(within(table).getByRole("button", { name: /DDD/ }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "DDD");
  });
});
