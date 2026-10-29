import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FundamentalsMultiples from "../FundamentalsMultiples";
import type { MultiplesOut } from "../useFundamentalsMultiples";
import { CARDS, ROWS, counts, payload } from "./fixtures";

// The Fundamentals Multiples body with its two hooks stubbed: the card line first, then a section per link in
// chain order (an old link marked, Context behind its toggle), the small charts (a bar per print, a hatched
// derived one, a dot per change toned by sign and ringed off the scale), the empty frames that are counted, the
// median cells, the hover / focus readout with the filing behind a bar, a click opening the Cockpit, the arrow keys
// moving between charts (one tab stop), the picker and the quiet lines. The hooks' requests have their own test.

const h = vi.hoisted(() => ({ useFundamentalsMultiples: vi.fn(), useMultiplesCards: vi.fn() }));
vi.mock("../useFundamentalsMultiples", () => ({
  useFundamentalsMultiples: h.useFundamentalsMultiples,
  useMultiplesCards: h.useMultiplesCards,
}));

function setup(data: MultiplesOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useMultiplesCards.mockReturnValue({ data: CARDS, isLoading: false, error: null });
  h.useFundamentalsMultiples.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<FundamentalsMultiples asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const cellEl = (key: string) =>
  screen.getAllByTestId("fm-cell").find((c) => c.getAttribute("data-key") === key) as HTMLElement;
const cellKeys = () => screen.getAllByTestId("fm-cell").map((c) => c.getAttribute("data-key"));
const rowEl = (key: string) =>
  screen.getAllByTestId("fm-row").find((r) => r.getAttribute("data-key") === key) as HTMLElement;
const AAA_T = "n|link:Turbines|s-AAA";
const readout = () => screen.getByTestId("fm-readout");
const tabStops = () => screen.getAllByTestId("fm-cell").filter((c) => c.getAttribute("tabindex") === "0");

beforeEach(() => {
  h.useFundamentalsMultiples.mockReset();
  h.useMultiplesCards.mockReset();
});

describe("FundamentalsMultiples", () => {
  it("draws the card line first, then a section per link in chain order, then the old link", () => {
    setup();
    const card = screen.getByTestId("fm-card-line");
    const rows = screen.getAllByTestId("fm-row");
    expect(rows.map((r) => r.getAttribute("data-key"))).toEqual([
      "link:Turbines",
      "link:Cables",
      "link:Empty link",
      "old:Old wires",
    ]);
    expect(card.compareDocumentPosition(rows[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(rows[0]).getByRole("heading", { name: "Turbines" })).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("make the power"); // the descriptor
    expect(rows[0]).toHaveTextContent("3 names");
    expect(rows[3]).toHaveTextContent("not a link today");
    // a link with nobody in it is still a section, and says so
    expect(rows[2]).toHaveTextContent("Nobody sits in this link on the roster at this as-of.");
    expect(within(rows[2]).queryAllByTestId("fm-cell")).toHaveLength(0);
  });

  it("shows the Context and Bench rows only behind a toggle that counts them, and gives them no median", async () => {
    setup();
    expect(screen.getAllByTestId("fm-row").map((r) => r.getAttribute("data-key"))).not.toContain("silent:Context");
    const toggle = screen.getByRole("checkbox", { name: /Show Context and Bench \(1\)/ });
    expect(toggle).not.toBeChecked();
    expect(cellKeys()).not.toContain("n|silent:Context|s-DDD");
    await userEvent.click(toggle);
    expect(toggle).toBeChecked();
    const rows = screen.getAllByTestId("fm-row");
    expect(rows[rows.length - 1].getAttribute("data-key")).toBe("silent:Context");
    expect(rows[rows.length - 1]).toHaveTextContent("not in the call · never in a median");
    expect(cellKeys()).toContain("n|silent:Context|s-DDD");
    expect(cellKeys()).not.toContain("m|silent:Context"); // never a median over them
    // the card line and the link medians are the server's: the toggle does not move them
    expect(within(screen.getByTestId("fm-card-line")).getByText("median of 6 names")).toBeInTheDocument();
  });

  it("renders no toggle when no Context or Bench name is on the card (a control that cannot discriminate)", () => {
    setup(payload({ rows: ROWS.slice(0, 4), counts: counts({ silent_companies: 0 }) }));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("draws a name as a bar per print with a dot per change, toned by sign, hatched when derived", () => {
    setup();
    const aaa = cellEl(AAA_T);
    expect(within(aaa).getAllByTestId("fm-bar")).toHaveLength(12);
    expect(aaa.querySelectorAll(".fm-bar.derived")).toHaveLength(1); // 2023-Q4, the fiscal year minus nine months
    expect(aaa.querySelectorAll(".fm-bar:not(.derived)")).toHaveLength(11);
    expect(within(aaa).getAllByTestId("fm-dot")).toHaveLength(10); // the first two have no change on the year before
    expect(aaa.querySelectorAll(".fm-dot.neg")).toHaveLength(1);
    expect(aaa.querySelectorAll(".fm-dot.pos")).toHaveLength(9);
    expect(aaa.querySelectorAll(".fm-dot.clipped")).toHaveLength(1); // +150%, off the scale: a ring
    expect(within(aaa).getByText("$210")).toBeInTheDocument(); // the top value, on its own scale
    expect(within(aaa).getByText("Q3 2023")).toBeInTheDocument();
    expect(within(aaa).getByText("Q2 2026")).toBeInTheDocument();
    expect(aaa.getAttribute("aria-label")).toContain("AAA · AAA Corp · 12 quarters");
    // the hatch is defined once for every chart on the page
    expect(document.querySelectorAll("#fm-hatch")).toHaveLength(1);
  });

  it("says the last print under the chart: its quarter, the filing date, how long before the as-of, and its change", () => {
    setup();
    const aaa = cellEl(AAA_T);
    expect(within(aaa).getByTestId("fm-last")).toHaveTextContent("Q2 2026 · filed Aug 9 · 5 days before the as-of");
    expect(aaa).toHaveTextContent("+150% on the year before");
  });

  it("draws an empty frame for a name with no series, an unresolved row and a series that ends early, all counted", () => {
    setup();
    const unr = cellEl("n|link:Turbines|ticker:UNR");
    expect(within(unr).queryAllByTestId("fm-bar")).toHaveLength(0);
    expect(within(unr).getByText("no resolved security")).toBeInTheDocument();
    const eee = cellEl("n|link:Cables|s-EEE");
    expect(within(eee).getByText("no revenue series stored")).toBeInTheDocument();
    const old = cellEl("n|old:Old wires|s-OLD");
    expect(within(old).getByText("no revenue in these quarters")).toBeInTheDocument();
    expect(old).toHaveTextContent("Q4 2022 · filed Feb 9, 2023 · 1,346 days before the as-of"); // its last print is still stated
    expect(old.className).toContain("stale");
    // the counts say so
    expect(screen.getByText(/a revenue series for 5 of 7 names \(2 without\)/)).toBeInTheDocument();
    expect(screen.getByText(/1 name whose series ends before these quarters/)).toBeInTheDocument();
  });

  it("marks a thin name, and notes a set-aside period under its chart", () => {
    setup();
    const bbb = cellEl("n|link:Turbines|s-BBB");
    expect(within(bbb).getByText("thin")).toBeInTheDocument();
    expect(within(bbb).getAllByTestId("fm-bar")).toHaveLength(4);
    expect(bbb).toHaveTextContent("1 period set aside");
    expect(screen.getAllByText("thin")).toHaveLength(1);
    expect(within(cellEl(AAA_T)).queryByText("thin")).toBeNull();
    expect(cellEl(AAA_T)).not.toHaveTextContent("set aside");
  });

  it("draws a company that sits in two links as a chart in each", () => {
    setup();
    expect(cellKeys().filter((k) => k?.endsWith("|s-AAA"))).toEqual([AAA_T, "n|link:Cables|s-AAA"]);
  });

  it("puts a median cell first in each row that has counting names, and none in an empty link", () => {
    setup();
    const t = cellEl("m|link:Turbines");
    expect(t).toHaveTextContent("Link median");
    expect(t).toHaveTextContent("median of 3 names");
    expect(within(t).getAllByTestId("fm-point")).toHaveLength(3); // only the quarters with a median
    expect(t.querySelectorAll(".fm-line")).toHaveLength(1); // the three are consecutive: one line
    expect(t).toHaveTextContent("3 quarters drawn");
    expect(cellKeys().filter((k) => k?.startsWith("m|"))).toEqual([
      "m|card",
      "m|link:Turbines",
      "m|link:Cables",
      "m|old:Old wires",
    ]);
    expect(cellEl("m|old:Old wires")).toHaveTextContent("no quarter has a median");
    const card = cellEl("m|card");
    expect(card).toHaveTextContent("Grid");
    expect(card).toHaveTextContent("median of 6 names");
  });

  it("opens the Cockpit on a name's chip, on the card from its name, and on Enter over a chart", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(within(cellEl(AAA_T)).getByRole("button", { name: "AAA" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "AAA");
    await userEvent.click(screen.getByRole("button", { name: "Grid" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid");
    onSelectThesis.mockClear();
    act(() => cellEl("n|link:Turbines|s-BBB").focus());
    await userEvent.keyboard("{Enter}");
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "BBB");
    onSelectThesis.mockClear();
    act(() => cellEl("m|link:Turbines").focus());
    await userEvent.keyboard("{Enter}"); // a median opens nothing
    expect(onSelectThesis).not.toHaveBeenCalled();
  });
});

describe("the readout", () => {
  const bars = (key: string) => within(cellEl(key)).getAllByTestId("fm-bar");

  it("shows a prompt until a bar is hovered, then the filing behind it", async () => {
    setup();
    expect(readout()).toHaveTextContent("Hover a bar for the filing behind it");
    await userEvent.hover(bars(AAA_T)[11]);
    expect(readout()).toHaveTextContent("AAA");
    expect(readout()).toHaveTextContent("AAA Corp");
    expect(readout()).toHaveTextContent("Turbines");
    expect(readout()).toHaveTextContent("Q2 2026: period ending Jun 30, 2026 · $210 · native");
    expect(readout()).toHaveTextContent("+150% on the same period a year earlier (ending Jun 30, 2025)");
    expect(readout()).toHaveTextContent("dated by its filing: Aug 9, 2026 · accession 0000876544-26-080901");
    const link = within(readout()).getByRole("link", { name: "filing on EDGAR" });
    expect(link).toHaveAttribute("href", "https://www.sec.gov/Archives/edgar/data/876544/000087654426080901/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(readout()).toHaveTextContent("last print: Q2 2026 · filed Aug 9 · 5 days before the as-of · +150% on the year before");
  });

  it("stays on the last bar looked at, so the filing link can be reached, until Escape", async () => {
    setup();
    await userEvent.hover(bars(AAA_T)[3]);
    await userEvent.unhover(bars(AAA_T)[3]);
    expect(readout()).toHaveTextContent("Q2 2024");
    expect(within(readout()).getByRole("link", { name: "filing on EDGAR" })).toBeInTheDocument();
    act(() => cellEl(AAA_T).focus());
    await userEvent.keyboard("{Escape}");
    expect(readout()).toHaveTextContent("Hover a bar for the filing behind it");
  });

  it("words a derived quarter, a re-report's first knowable date and a bar with no change", async () => {
    setup();
    await userEvent.hover(bars(AAA_T)[1]);
    expect(readout()).toHaveTextContent("derived (the fiscal year minus nine months)");
    expect(readout()).toHaveTextContent("no row a year earlier in the read, so no change");
    await userEvent.hover(bars(AAA_T)[7]);
    expect(readout()).toHaveTextContent("first knowable Aug 8, 2025 in accession 0000876544-25-080801 (re-reported)");
  });

  it("notes a set-aside period, and says what an empty frame holds", async () => {
    setup();
    await userEvent.hover(bars("n|link:Turbines|s-BBB")[3]);
    expect(readout()).toHaveTextContent("1 period set aside (a data-quality set-aside)");
    act(() => cellEl("n|link:Turbines|ticker:UNR").focus());
    expect(readout()).toHaveTextContent("UNR");
    expect(readout()).toHaveTextContent("no resolved security");
    act(() => cellEl("n|old:Old wires|s-OLD").focus());
    expect(readout()).toHaveTextContent("no revenue in these quarters");
    expect(readout()).toHaveTextContent("last print: Q4 2022");
  });

  it("reads a median point as a median of its names, equal weight, and says why a cell has none", async () => {
    setup();
    const pts = within(cellEl("m|link:Turbines")).getAllByTestId("fm-point");
    await userEvent.hover(pts[2]);
    expect(readout()).toHaveTextContent("Turbines");
    expect(readout()).toHaveTextContent("median year-over-year change · 3 names · equal weight, no score");
    expect(readout()).toHaveTextContent("Q2 2026: median +35% (median of 2 names)");
    act(() => cellEl("m|old:Old wires").focus());
    expect(readout()).toHaveTextContent("No quarter has a median here");
  });
});

describe("keyboard", () => {
  it("is one tab stop, and focus drives the same readout (on the newest bar)", () => {
    setup();
    expect(tabStops()).toHaveLength(1);
    expect(tabStops()[0].getAttribute("data-key")).toBe("m|card");
    act(() => cellEl(AAA_T).focus());
    expect(tabStops().map((c) => c.getAttribute("data-key"))).toEqual([AAA_T]);
    expect(readout()).toHaveTextContent("Q2 2026: period ending Jun 30, 2026");
  });

  it("moves between charts in reading order and down to the next row's first chart", async () => {
    setup();
    act(() => cellEl("m|card").focus());
    await userEvent.keyboard("{ArrowRight}");
    expect(cellEl("m|link:Turbines")).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(cellEl(AAA_T)).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}"); // the next row: Cables, its median cell
    expect(cellEl("m|link:Cables")).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(cellEl("m|link:Turbines")).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(cellEl("m|card")).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}{ArrowLeft}"); // the start: stays
    expect(cellEl("m|card")).toHaveFocus();
  });

  it("steps through one chart's bars with Shift and the left and right arrows", async () => {
    setup();
    act(() => cellEl(AAA_T).focus());
    expect(readout()).toHaveTextContent("Q2 2026: period ending Jun 30, 2026");
    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(cellEl(AAA_T)).toHaveFocus(); // the focus stays on the chart
    expect(readout()).toHaveTextContent("Q1 2026: period ending Mar 31, 2026");
    await userEvent.keyboard("{Shift>}{ArrowLeft}{ArrowLeft}{/Shift}");
    expect(readout()).toHaveTextContent("Q3 2025: period ending Sep 30, 2025");
    await userEvent.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(readout()).toHaveTextContent("Q4 2025: period ending Dec 31, 2025");
    // and through a median cell's drawn points
    act(() => cellEl("m|link:Turbines").focus());
    expect(readout()).toHaveTextContent("Q2 2026: median +35%");
    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(readout()).toHaveTextContent("Q1 2026: median +20%");
  });

  it("keeps working when the toggle adds a row under the focus", async () => {
    setup();
    act(() => cellEl("n|old:Old wires|s-OLD").focus());
    await userEvent.click(screen.getByRole("checkbox", { name: /Show Context and Bench/ }));
    act(() => cellEl("n|old:Old wires|s-OLD").focus());
    await userEvent.keyboard("{ArrowRight}");
    expect(cellEl("n|silent:Context|s-DDD")).toHaveFocus();
  });

  it("drops the readout of a chart the toggle then hides", async () => {
    setup();
    const toggle = screen.getByRole("checkbox", { name: /Show Context and Bench/ });
    await userEvent.click(toggle);
    act(() => cellEl("n|silent:Context|s-DDD").focus());
    expect(readout()).toHaveTextContent("DDD Corp");
    await userEvent.click(toggle);
    expect(readout()).toHaveTextContent("Hover a bar for the filing behind it");
  });
});

describe("the picker and the page", () => {
  it("searches and switches cards; the first A→Z is the default", async () => {
    setup();
    const select = screen.getByRole("combobox", { name: "Card" });
    expect(select).toHaveValue("t-copper");
    expect(h.useFundamentalsMultiples).toHaveBeenLastCalledWith("2026-10-28", "t-copper");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links",
      "Grid · 3 links",
      "Nuclear · 2 links",
    ]);
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a card" }), "nuc");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links", // the current card stays selectable while the search narrows the rest
      "Nuclear · 2 links",
    ]);
    await userEvent.selectOptions(select, "t-nuke");
    expect(h.useFundamentalsMultiples).toHaveBeenLastCalledWith("2026-10-28", "t-nuke");
  });

  it("shows the work, the roster note, the legend and the known limits", () => {
    setup(payload({ roster_fallback: true, asof: "2026-09-01", cache: "computed", compute_ms: 4200 }));
    const work = screen.getByText(/Grid as of Sep 1, 2026/);
    expect(work).toHaveTextContent("9 roster rows (8 resolved, 1 without a resolved security, 1 in Context or Bench), 7 companies");
    expect(work).toHaveTextContent("1 revenue period set aside on this card's names");
    expect(work).toHaveTextContent("quarters drawn: Q3 2023 to Q2 2026");
    expect(work).toHaveTextContent("computed in 4.2 s");
    expect(screen.getByText(/Today's roster stands in for this card/)).toHaveTextContent("Sep 15, 2026");
    expect(screen.getByText(/Bars: quarterly revenue on each name's own scale/)).toHaveTextContent("a hatched bar is derived");
    expect(screen.getByText(/Revenue is the XBRL series only \(companyfacts\), quarterly/)).toHaveTextContent(
      "Q4 derived from the annual where no quarterly fact exists",
    );
    expect(screen.getByText(/Placed by period end, dated by filing/)).toHaveTextContent("not drawn");
    expect(screen.getByText(/Medians, equal weight, no score/)).toHaveTextContent("fewer than 5 quarters");
    expect(screen.getByText(/The card line: the median year-over-year change/)).toBeInTheDocument();
  });

  it("shows the slow first-load message while the first read computes, and an error when it fails", () => {
    const { unmount } = setup(undefined, { isLoading: true });
    expect(screen.getByText(/Reading this card's names — the first load after a run or a basket edit/)).toBeInTheDocument();
    unmount();
    setup(undefined, { error: new Error("boom") });
    expect(screen.getByText("Could not load the Fundamentals Multiples.")).toBeInTheDocument();
  });

  it("says so when no card existed, and when the cards fail to load", () => {
    h.useMultiplesCards.mockReturnValue({ data: { ...CARDS, cards: [] }, isLoading: false, error: null });
    h.useFundamentalsMultiples.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: null });
    const { unmount } = render(<FundamentalsMultiples asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("No card existed by this as-of.")).toBeInTheDocument();
    unmount();
    h.useMultiplesCards.mockReturnValue({ data: undefined, isLoading: false, error: new Error("x") });
    render(<FundamentalsMultiples asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Fundamentals Multiples' cards.")).toBeInTheDocument();
  });

  it("draws a card with no axis (no name has a print) as empty frames, not an error", () => {
    const empty = payload({
      axis: [],
      card_line: { counting: 1, points: [] },
      rows: [ROWS[2]],
      companies: [],
      counts: counts({ silent_companies: 0 }),
    });
    setup(empty);
    expect(screen.getByText(/no quarter to draw/)).toBeInTheDocument();
    expect(screen.getByTestId("fm-card-line")).toHaveTextContent("no quarter has a median");
  });
});
