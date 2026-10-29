import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SharedNames from "../SharedNames";
import type { SharedNameCardRefOut, SharedNamesOut } from "../useSharedNames";

// The Shared Names body with its one hook stubbed: the table, the "opposite sides" marker and filter, the
// pair click-to-filter and its clear, the counting toggle threading its param, and a chip opening the Cockpit.
// The hook's own request shape has its own test (useSharedNames.test.tsx).

const h = vi.hoisted(() => ({
  useSharedNames: vi.fn(),
}));
vi.mock("../useSharedNames", () => ({ useSharedNames: h.useSharedNames }));

const COAL: SharedNameCardRefOut = { thesis_id: "t-coal", thesis_name: "Coal" };
const GRID: SharedNameCardRefOut = { thesis_id: "t-grid", thesis_name: "Grid" };
const NUC: SharedNameCardRefOut = { thesis_id: "t-nuc", thesis_name: "Nuclear" };

const chip = (
  card: SharedNameCardRefOut,
  ticker: string,
  over: Partial<SharedNamesOut["names"][number]["cards"][number]> = {},
) => ({
  ...card,
  ticker,
  links: ["Core"],
  silent_link: null,
  context_role: null,
  counts: true,
  ...over,
});

const DATA: SharedNamesOut = {
  counting_only: false,
  live_cards: 5,
  basket_rows: 40,
  silent_rows_left_out: 0,
  unresolved_rows: 2,
  companies: 30,
  names: [
    {
      security_id: "s-aaa",
      ticker: "AAA",
      name: "Alpha Corp",
      card_count: 3,
      opposite_sides: true,
      cards: [
        chip(COAL, "AAA", {
          links: [],
          silent_link: "Context",
          context_role: "counter_case",
          counts: false,
        }),
        chip(GRID, "AAA", { links: ["Turbines", "Cables"] }),
        chip(NUC, "AAA", { links: [null], context_role: "payer" }),
      ],
    },
    {
      security_id: "s-bbb",
      ticker: "BBB",
      name: null,
      card_count: 2,
      opposite_sides: false,
      cards: [
        chip(GRID, "BBB"),
        chip(NUC, "BBB", { links: [], silent_link: "Bench", counts: false }),
      ],
    },
    {
      security_id: "s-ccc",
      ticker: "CCC",
      name: "Cee Co",
      card_count: 2,
      opposite_sides: false,
      cards: [chip(COAL, "CCC"), chip(GRID, "CCC")],
    },
  ],
  pairs: [
    {
      card_a: COAL,
      card_b: GRID,
      shared_count: 2,
      tickers: ["AAA", "CCC"],
      security_ids: ["s-aaa", "s-ccc"],
    },
    {
      card_a: GRID,
      card_b: NUC,
      shared_count: 2,
      tickers: ["AAA", "BBB"],
      security_ids: ["s-aaa", "s-bbb"],
    },
    { card_a: COAL, card_b: NUC, shared_count: 1, tickers: ["AAA"], security_ids: ["s-aaa"] },
  ],
};

const query = (data: SharedNamesOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () =>
  render(<SharedNames asof="2026-10-29" onSelectThesis={onSelectThesis} />);

/** The main table's ticker column, top to bottom. */
const tickers = () => {
  const table = screen.getAllByRole("table")[0];
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[0].firstChild?.textContent);
};
const rowOf = (ticker: string) => {
  const table = screen.getAllByRole("table")[0];
  const row = within(table)
    .getAllByRole("row")
    .find((r) => within(r).queryAllByRole("cell")[0]?.firstChild?.textContent === ticker);
  if (!row) throw new Error(`no row for ${ticker}`);
  return row;
};

beforeEach(() => {
  h.useSharedNames.mockReset();
  h.useSharedNames.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("Shared Names — the table", () => {
  it("renders one row per company in the payload's order, with its count, name and chips", () => {
    renderPage();
    expect(tickers()).toEqual(["AAA", "BBB", "CCC"]);
    expect(screen.getByRole("columnheader", { name: /On the most cards/ })).toBeInTheDocument();

    const aaa = rowOf("AAA");
    expect(within(aaa).getByText("Alpha Corp")).toBeInTheDocument();
    expect(within(aaa).getByText("3")).toBeInTheDocument();
    // the several links of one card sit on its one chip; no link reads "Unsegmented" like the Cockpit lens
    expect(within(aaa).getByRole("button", { name: /^Grid/ })).toHaveTextContent(
      "GridTurbines, Cables",
    );
    expect(within(aaa).getByRole("button", { name: /^Nuclear/ })).toHaveTextContent("Unsegmented");
    // a Context membership carries the quiet lowercase tag; the context role is its own tag
    const coal = within(aaa).getByRole("button", { name: /^Coal/ });
    expect(within(coal).getByText("context")).toBeInTheDocument();
    expect(within(coal).getByText("counter-case")).toBeInTheDocument();
    expect(coal).toHaveClass("quiet");
    expect(within(aaa).getByText("payer")).toBeInTheDocument();

    expect(within(rowOf("BBB")).getByText("bench")).toBeInTheDocument();
    expect(within(rowOf("BBB")).getByText("—")).toBeInTheDocument(); // no master name
  });

  it("shows the work in one quiet line, every number off the payload", () => {
    renderPage();
    expect(
      screen.getByText(
        "5 live cards · 3 companies on 2+ cards · matched by security · Context and Bench included · " +
          "2 basket rows without a resolved security not matched",
      ),
    ).toBeInTheDocument();
  });

  it("a chip opens that card's Cockpit with the company selected", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(within(rowOf("AAA")).getByRole("button", { name: /^Grid/ }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid", "AAA");
  });

  it("says so honestly when nothing is shared, while loading, and on an error", () => {
    h.useSharedNames.mockReturnValue(query({ ...DATA, names: [], pairs: [] }));
    const { unmount } = renderPage();
    expect(screen.getByText("No company sits on more than one live card.")).toBeInTheDocument();
    expect(screen.getByText("No two live cards share a company.")).toBeInTheDocument();
    unmount();

    h.useSharedNames.mockReturnValue(query(undefined, { isLoading: true }));
    renderPage().unmount();
    h.useSharedNames.mockReturnValue(query(undefined, { error: new Error("boom") }));
    renderPage();
    expect(screen.getByText("Could not load Shared Names.")).toBeInTheDocument();
  });
});

describe("Shared Names — opposite sides", () => {
  it("marks only that row, with a hover naming the cards on each side", () => {
    renderPage();
    const mark = within(rowOf("AAA")).getByText("opposite sides");
    expect(mark).toHaveAttribute("title", "Counts on: Grid, Nuclear · Counter-case on: Coal");
    expect(within(rowOf("BBB")).queryByText("opposite sides")).toBeNull();
    expect(within(rowOf("CCC")).queryByText("opposite sides")).toBeNull();
  });

  it("the filter shows only those rows, and its second click brings the rest back", async () => {
    const user = userEvent.setup();
    renderPage();
    const toggle = screen.getByRole("button", { name: "Opposite sides only · 1" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(tickers()).toEqual(["AAA"]);
    expect(screen.getByText("· showing 1 of 3")).toBeInTheDocument();

    await user.click(toggle);
    expect(tickers()).toEqual(["AAA", "BBB", "CCC"]);
  });

  it("the filter control does not render when no row is on opposite sides", () => {
    h.useSharedNames.mockReturnValue(
      query({ ...DATA, names: DATA.names.map((n) => ({ ...n, opposite_sides: false })) }),
    );
    renderPage();
    expect(screen.queryByRole("button", { name: /Opposite sides only/ })).toBeNull();
  });
});

describe("Shared Names — card pairs", () => {
  it("lists every pair with its count and tickers", () => {
    renderPage();
    const pairs = screen.getAllByRole("table")[1];
    const rows = within(pairs).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      "CoalGrid2AAA · CCC",
      "GridNuclear2AAA · BBB",
      "CoalNuclear1AAA",
    ]);
  });

  it("clicking a pair narrows the table to its companies; clear brings them all back", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText("AAA · CCC"));

    expect(tickers()).toEqual(["AAA", "CCC"]);
    expect(screen.getByText("Shared by Coal and Grid · 2 companies")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "clear" }));
    expect(tickers()).toEqual(["AAA", "BBB", "CCC"]);
    expect(screen.queryByText(/Shared by/)).toBeNull();
  });

  it("a second click on the active pair clears it too, and filters combine", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText("AAA · BBB"));
    expect(tickers()).toEqual(["AAA", "BBB"]);

    await user.click(screen.getByRole("button", { name: "Opposite sides only · 1" }));
    expect(tickers()).toEqual(["AAA"]);
    await user.click(screen.getByRole("button", { name: "Opposite sides only · 1" }));

    await user.click(screen.getByText("AAA · BBB"));
    expect(tickers()).toEqual(["AAA", "BBB", "CCC"]);
  });
});

describe("Shared Names — counting names only", () => {
  it("is off by default and its toggle asks the hook for the counting_only view", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(h.useSharedNames).toHaveBeenLastCalledWith(false);
    const box = screen.getByRole("checkbox", { name: "Counting names only" });
    expect(box).not.toBeChecked();

    h.useSharedNames.mockReturnValue(
      query({ ...DATA, counting_only: true, silent_rows_left_out: 3 }),
    );
    await user.click(box);
    expect(box).toBeChecked();
    expect(h.useSharedNames).toHaveBeenLastCalledWith(true);
    expect(screen.getByText(/Context and Bench left out \(3 basket rows\)/)).toBeInTheDocument();

    await user.click(box);
    expect(h.useSharedNames).toHaveBeenLastCalledWith(false);
  });
});
