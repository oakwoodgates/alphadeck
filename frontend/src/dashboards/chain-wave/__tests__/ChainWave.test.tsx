import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ChainWave from "../ChainWave";
import type {
  ChainWaveCardsOut,
  ChainWaveCellOut,
  ChainWaveMemberOut,
  ChainWaveOut,
  ChainWaveRowOut,
  ChainWaveWeekOut,
} from "../useChainWave";

// The Chain Wave body with its two hooks stubbed: the rows in chain order (an old link marked, the counting names
// and role rows below), the Context/Bench toggle threading and its rows last in the chain, a row expanding into
// its companies (a ticker chip opening the Cockpit on that name, a flagged one left out with its reason), the
// readout only with payers, the picker (search + select) switching cards, the card name opening the Cockpit, the
// window threading, a cell's hover readout, and the slow first-load message. The hooks' requests have their own
// test.

const h = vi.hoisted(() => ({ useChainWave: vi.fn(), useChainWaveCards: vi.fn() }));
vi.mock("../useChainWave", () => ({ useChainWave: h.useChainWave, useChainWaveCards: h.useChainWaveCards }));

const WEEKS: ChainWaveWeekOut[] = [
  ["2026-10-05", "2026-10-09"],
  ["2026-10-12", "2026-10-16"],
  ["2026-10-19", "2026-10-23"],
  ["2026-10-26", "2026-10-28"], // the as-of's week, to date
].map(([monday, close], i) => ({
  monday,
  first_session: monday,
  close,
  sessions: i === 3 ? 3 : 5,
  to_date: i === 3,
}));

const cell = (diff: number | null, over: Partial<ChainWaveCellOut> = {}): ChainWaveCellOut => ({
  diff,
  ret: diff == null ? null : diff + 1,
  spy: 1,
  names: diff == null ? 0 : 2,
  gap: diff == null ? "not_started" : null,
  ...over,
});

const member = (ticker: string, over: Partial<ChainWaveMemberOut> = {}): ChainWaveMemberOut => ({
  key: `s-${ticker}`,
  ticker,
  security_id: `s-${ticker}`,
  flag: null,
  priced: true,
  cells: [cell(1), cell(2), cell(-1), cell(0.5)],
  ...over,
});

const row = (key: string, kind: ChainWaveRowOut["kind"], label: string, over: Partial<ChainWaveRowOut> = {}): ChainWaveRowOut => ({
  key,
  kind,
  label,
  role: null,
  names: 3,
  priced: 3,
  thin: false,
  cells: [cell(2.5), cell(-4), cell(12), cell(null)],
  members: [],
  ...over,
});

const ROWS: ChainWaveRowOut[] = [
  row("link:Reactors", "link", "Reactors", {
    members: [
      member("OKLO"),
      member("SMR"),
      member("RBOT", { flag: "mixed-scale price history", priced: false, cells: [] }),
    ],
    names: 3,
    priced: 2,
    thin: true,
  }),
  row("link:Fuel", "link", "Fuel"),
  row("old:Old wires", "old_link", "Old wires", { names: 1, priced: 1, thin: true }),
  row("no_link", "no_link", "No link", { names: 1, priced: 0, thin: true }),
  row("counting", "counting", "Counting names", { names: 8, priced: 7 }),
  row("role:payer", "role", "payer", { role: "payer", names: 2, priced: 2, thin: true, members: [member("MSFT")] }),
  row("role:counter_case", "role", "counter_case", { role: "counter_case", names: 1, priced: 1, thin: true }),
];

const SILENT: ChainWaveRowOut[] = [
  row("silent:Context", "silent", "Context", { names: 2, priced: 2, thin: true }),
  row("silent:Bench", "silent", "Bench", { names: 0, priced: 0, thin: true }),
];

const payload = (over: Partial<ChainWaveOut> = {}): ChainWaveOut => ({
  asof: "2026-10-28",
  thesis_id: "t-nuke",
  thesis_name: "Nuclear",
  weeks: 26,
  include_silent: false,
  roster_fallback: false,
  links_today: 2,
  basket_rows: 12,
  silent_rows: 2,
  unresolved_rows: 0,
  sessions: 270,
  first_session: "2025-10-20",
  last_session: "2026-10-28",
  session_source: "benchmarks",
  spy_available: true,
  week_columns: WEEKS,
  spy: [cell(null, { ret: 1.2, gap: null }), cell(null, { ret: -0.8, gap: null }), cell(null, { ret: 0.1, gap: null }), cell(null, { ret: 0.4, gap: null })],
  rows: ROWS,
  readout: { weeks: 4, start: "2026-10-02", end: "2026-10-28", payers: 3.21, counting: -1.04 },
  flagged: [{ security_id: "s-RBOT", ticker: "RBOT", reason: "mixed-scale price history" }],
  roles_set: 2,
  windows: [13, 26, 52],
  thin_below: 3,
  readout_weeks: 4,
  cache: "hit",
  compute_ms: 800,
  ...over,
});

const CARDS: ChainWaveCardsOut = {
  asof: "2026-10-28",
  live_cards: 3,
  not_yet_created: 0,
  cards: [
    { thesis_id: "t-copper", name: "Copper", links: 4 },
    { thesis_id: "t-grid", name: "Grid", links: 5 },
    { thesis_id: "t-nuke", name: "Nuclear", links: 2 },
  ],
  cache: "hit",
  compute_ms: 10,
};

function setup(data: ChainWaveOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useChainWaveCards.mockReturnValue({ data: CARDS, isLoading: false, error: null });
  h.useChainWave.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<ChainWave asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

/** The labels of the grid's rows (not SPY, not the companies), in order. */
const rowLabels = () =>
  screen
    .getAllByRole("row")
    .filter((r) => r.classList.contains("cw-line-row"))
    .map((r) => r.querySelector(".cw-label-text")?.textContent);

const gridRow = (key: string) => screen.getAllByRole("row").find((r) => r.getAttribute("data-key") === key)!;

beforeEach(() => {
  h.useChainWave.mockReset();
  h.useChainWaveCards.mockReset();
});

describe("ChainWave", () => {
  it("draws SPY, then the chain in order, then the counting names and the role rows, payers first", () => {
    setup();
    expect(rowLabels()).toEqual([
      "Reactors",
      "Fuel",
      "Old wires",
      "No link",
      "Counting names",
      "Payers",
      "Counter-case",
    ]);
    const lines = screen.getAllByRole("row").slice(1); // after the header
    expect(lines[0]).toHaveClass("cw-line-spy");
    expect(within(gridRow("old:Old wires")).getByText("not a link today")).toBeInTheDocument();
    expect(within(gridRow("link:Reactors")).getByText("2/3")).toHaveAttribute("title", "names priced / names");
    expect(gridRow("link:Reactors")).toHaveClass("thin");
    expect(gridRow("link:Fuel")).not.toHaveClass("thin");
    // one cell per week; a gap cell shows "—", a value cell its muted tone
    const cells = within(gridRow("link:Fuel")).getAllByTestId("cw-cell");
    expect(cells).toHaveLength(4);
    expect(cells[0]).toHaveClass("pos");
    expect(cells[1]).toHaveClass("neg");
    expect(cells[3]).toHaveClass("gap");
    expect(cells[3]).toHaveTextContent("—");
    // the header labels the month and the to-date week
    expect(screen.getByText("wk to date")).toBeInTheDocument();
    expect(screen.getByText("Oct")).toBeInTheDocument();
  });

  it("threads the Context/Bench toggle and draws them as the last two chain rows", async () => {
    setup();
    expect(h.useChainWave).toHaveBeenLastCalledWith("2026-10-28", "t-copper", 26, false);
    await userEvent.click(screen.getByRole("checkbox", { name: /Show Context and Bench/ }));
    expect(h.useChainWave).toHaveBeenLastCalledWith("2026-10-28", "t-copper", 26, true);
  });

  it("draws Context and Bench last in the chain when the read includes them", () => {
    setup(payload({ include_silent: true, rows: [...ROWS.slice(0, 4), ...SILENT, ...ROWS.slice(4)] }));
    expect(rowLabels()).toEqual([
      "Reactors",
      "Fuel",
      "Old wires",
      "No link",
      "Context",
      "Bench",
      "Counting names",
      "Payers",
      "Counter-case",
    ]);
    expect(screen.getByText(/Context and Bench shown as the last two chain rows/)).toBeInTheDocument();
  });

  it("expands a row into its companies; a ticker chip opens the Cockpit on that name; a flagged one is left out", async () => {
    const { onSelectThesis } = setup();
    expect(screen.queryByTitle("Open Nuclear on OKLO")).toBeNull();
    const btn = within(gridRow("link:Reactors")).getByRole("button");
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(within(gridRow("link:Reactors|s-OKLO")).getAllByTestId("cw-cell")).toHaveLength(4);
    expect(within(gridRow("link:Reactors|s-RBOT")).getByText("left out: mixed-scale price history")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Open Nuclear on OKLO"));
    expect(onSelectThesis).toHaveBeenCalledWith("t-nuke", "OKLO");
    await userEvent.click(screen.getByTitle("Open Nuclear on RBOT"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "RBOT");
    await userEvent.click(btn);
    expect(screen.queryByTitle("Open Nuclear on OKLO")).toBeNull();
    // a role row expands too
    await userEvent.click(within(gridRow("role:payer")).getByRole("button"));
    await userEvent.click(screen.getByTitle("Open Nuclear on MSFT"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "MSFT");
  });

  it("shows the readout only when the card has payers", () => {
    const { unmount } = setup();
    expect(screen.getByTestId("cw-readout")).toHaveTextContent(
      "Last 4 weeks vs SPY: payers +3.2 pp · counting names -1.0 pp",
    );
    unmount();
    setup(payload({ readout: null, rows: ROWS.slice(0, 5), roles_set: 0 }));
    expect(screen.queryByTestId("cw-readout")).toBeNull();
    expect(screen.getByText(/No context roles are set on this card/)).toBeInTheDocument();
  });

  it("the picker searches and switches cards; the first A→Z is the default", async () => {
    setup();
    const select = screen.getByRole("combobox", { name: "Card" });
    expect(select).toHaveValue("t-copper");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links",
      "Grid · 5 links",
      "Nuclear · 2 links",
    ]);
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a card" }), "nuc");
    // the current card stays selectable while the search narrows the rest
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links",
      "Nuclear · 2 links",
    ]);
    await userEvent.selectOptions(select, "t-nuke");
    expect(h.useChainWave).toHaveBeenLastCalledWith("2026-10-28", "t-nuke", 26, false);
  });

  it("the card name opens the Cockpit on the card", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Nuclear" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-nuke");
  });

  it("threads the window", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "52 weeks" }));
    expect(h.useChainWave).toHaveBeenLastCalledWith("2026-10-28", "t-copper", 52, false);
    expect(screen.getByRole("button", { name: "52 weeks" })).toHaveAttribute("aria-pressed", "true");
  });

  it("hovering a cell shows its row, week, returns, difference and names contributing", async () => {
    setup();
    const focus = screen.getByTestId("cw-focus");
    expect(focus).toHaveTextContent("Hover a cell");
    await userEvent.hover(within(gridRow("link:Fuel")).getAllByTestId("cw-cell")[2]);
    expect(focus).toHaveTextContent(
      "Fuel · Oct 19, 2026 – Oct 23, 2026 · row +13.0% · SPY +1.0% · difference +12.0 pp · 2 names contributing",
    );
    await userEvent.hover(within(gridRow("link:Fuel")).getAllByTestId("cw-cell")[3]);
    expect(focus).toHaveTextContent("(week to date)");
    expect(focus).toHaveTextContent("the line had not started by last week's close");
  });

  it("keyboard focus moves along the grid with the arrow keys and drives the same readout", async () => {
    setup();
    const focus = screen.getByTestId("cw-focus");
    const spyCells = within(screen.getAllByRole("row")[1]).getAllByTestId("cw-cell");
    // one tab stop: the newest SPY week
    expect(spyCells.map((c) => c.getAttribute("tabindex"))).toEqual(["-1", "-1", "-1", "0"]);
    act(() => spyCells[3].focus());
    expect(focus).toHaveTextContent("SPY · Oct 26, 2026 – Oct 28, 2026 (week to date) · SPY +0.4%");
    await userEvent.keyboard("{ArrowLeft}");
    expect(spyCells[2]).toHaveFocus();
    expect(focus).toHaveTextContent("SPY · Oct 19, 2026 – Oct 23, 2026 · SPY +0.1%");
    await userEvent.keyboard("{ArrowDown}");
    expect(within(gridRow("link:Reactors")).getAllByTestId("cw-cell")[2]).toHaveFocus();
    expect(focus).toHaveTextContent("Reactors · Oct 19, 2026 – Oct 23, 2026 · row +13.0%");
  });

  it("shows the work, the roster note, the flagged names and the known limits on quiet lines", () => {
    setup(payload({ roster_fallback: true, asof: "2026-09-01", cache: "computed", compute_ms: 4200 }));
    const work = screen.getByText(/each cell = the row's weekly return minus SPY's/);
    expect(work).toHaveTextContent("26 weeks, Oct 5, 2026 → Oct 28, 2026");
    expect(work).toHaveTextContent("7 of 8 counting names priced");
    expect(work).toHaveTextContent("names with a flagged price history are left out (1 company)");
    expect(work).toHaveTextContent("Context and Bench left out of the chain (2 basket rows; the role rows still include them)");
    expect(work).toHaveTextContent("computed in 4.2 s");
    expect(work).toHaveTextContent("as of Sep 1, 2026");
    expect(screen.getByText(/Today's roster stands in for this card/)).toHaveTextContent("Sep 15, 2026");
    expect(screen.getByText(/1 name left out for a flagged price history/)).toBeInTheDocument();
    expect(screen.getByText(/Known limits/)).toHaveTextContent("Chain order is today's");
  });

  it("shows the slow first-load message while the first read computes", () => {
    setup(undefined, { isLoading: true });
    expect(screen.getByText(/Computing this card's weeks — the first load after a run or a basket edit/)).toBeInTheDocument();
  });
});
