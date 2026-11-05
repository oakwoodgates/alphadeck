import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OverlapMap from "../OverlapMap";
import { overlapFixture } from "./fixture";

// The Overlap Map body with its one hook stubbed: the three tabs, the threshold hiding lines without moving a
// bubble, the shared-companies dots at their cards' midpoints, a line click opening the Venn on that pair, the
// Venn's regions (counts and lists), the click panel, the strip, the Sectors grid and its cell click, and a ticker
// chip calling onSelectThesis(thesisId, ticker). The hook's request has its own test.

const h = vi.hoisted(() => ({ useOverlapMap: vi.fn() }));
vi.mock("../useOverlapMap", () => ({ useOverlapMap: h.useOverlapMap }));

const ok = (data = overlapFixture()) => ({ isLoading: false, error: null, data });

function setup(data = overlapFixture()) {
  h.useOverlapMap.mockReturnValue(ok(data));
  const onSelect = vi.fn();
  render(<OverlapMap asof="2026-11-04" onSelectThesis={onSelect} />);
  return { onSelect, user: userEvent.setup() };
}

const lines = () => document.querySelectorAll('[data-testid^="om-line-"]');
const bubbleCenter = (i: number) => {
  const c = screen.getByTestId(`om-bubble-${i}`).querySelector("circle")!;
  return [c.getAttribute("cx"), c.getAttribute("cy")];
};

beforeEach(() => h.useOverlapMap.mockReset());

describe("the page", () => {
  it("shows the Map by default, with the three tabs", () => {
    setup();
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Map", "Venn", "Sectors"]);
    expect(screen.getByRole("tab", { name: "Map" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("om-bubble-0")).toBeInTheDocument();
    expect(screen.getByTestId("om-work").textContent).toContain("5 cards counted (1 created after the as-of left out)");
  });

  it("threads the Context, ETFs and Bench toggle into the read", async () => {
    const { user } = setup();
    expect(h.useOverlapMap).toHaveBeenLastCalledWith("2026-11-04", false);
    await user.click(screen.getByLabelText("Include Context, ETFs and Bench"));
    expect(h.useOverlapMap).toHaveBeenLastCalledWith("2026-11-04", true);
  });

  it("says so while loading and on an error", () => {
    h.useOverlapMap.mockReturnValue({ isLoading: true, error: null, data: undefined });
    const { unmount } = render(<OverlapMap asof="2026-11-04" onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/laying out the map/i)).toBeInTheDocument();
    unmount();
    h.useOverlapMap.mockReturnValue({ isLoading: false, error: new Error("x"), data: undefined });
    render(<OverlapMap asof="2026-11-04" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Overlap Map.")).toBeInTheDocument();
  });
});

describe("the Map tab", () => {
  it("hides lines with the threshold and never moves a bubble", async () => {
    const { user } = setup();
    const before = [0, 1, 2, 3].map(bubbleCenter);
    expect(lines()).toHaveLength(1); // the default 3: only Alpha-Beta
    expect(screen.getByTestId("om-groups").textContent).toBe("At 3+ shared names: one group of 2 cards; 3 cards with no line");
    await user.click(screen.getByRole("button", { name: "1" }));
    expect(lines()).toHaveLength(4);
    expect(screen.getByTestId("om-groups").textContent).toContain("one group of 4 cards; 1 card with no line");
    await user.click(screen.getByRole("button", { name: "5+" }));
    expect(lines()).toHaveLength(0);
    expect([0, 1, 2, 3].map(bubbleCenter)).toEqual(before);
  });

  it("puts the card with no shared name in the strip, not on the map", () => {
    setup();
    const strip = screen.getByTestId("om-strip");
    expect(within(strip).getByText("Shares no names")).toBeInTheDocument();
    expect(within(strip).getByRole("button", { name: /Solo/ })).toBeInTheDocument();
    expect(screen.queryByTestId("om-bubble-4")).toBeNull();
  });

  it("draws shared companies as dots at the midpoint of their cards, only when toggled on", async () => {
    const { user } = setup();
    expect(document.querySelectorAll('[data-testid^="om-dot-"]')).toHaveLength(0);
    await user.click(screen.getByLabelText("Show shared companies"));
    const dots = document.querySelectorAll('[data-testid^="om-dot-"]');
    expect(dots).toHaveLength(4); // BBB CCC DDD GGG
    const bbb = screen.getByTestId("om-dot-1"); // BBB: Alpha (200,200) and Beta (320,200)
    expect(bbb.getAttribute("cx")).toBe("260");
    expect(bbb.getAttribute("cy")).toBe("200");
    const ggg = screen.getByTestId("om-dot-6"); // GGG: Gamma (260,320) and Delta (260,420)
    expect(ggg.getAttribute("cx")).toBe("260");
    expect(ggg.getAttribute("cy")).toBe("370");
  });

  it("pins a dot on click, listing its cards as Cockpit links", async () => {
    const { user, onSelect } = setup();
    await user.click(screen.getByLabelText("Show shared companies"));
    await user.hover(screen.getByTestId("om-dot-6"));
    expect(screen.getByTestId("om-readout").textContent).toContain("GGG");
    await user.click(screen.getByTestId("om-dot-6"));
    await user.unhover(screen.getByTestId("om-dot-6"));
    const readout = screen.getByTestId("om-readout");
    expect(readout.textContent).toContain("GGG Corp");
    await user.click(within(readout).getByRole("button", { name: /Delta/ }));
    expect(onSelect).toHaveBeenCalledWith("t-delta", "GGG");
  });

  it("words a hovered line", async () => {
    const { user } = setup();
    await user.hover(screen.getByTestId("om-line-0-1"));
    expect(screen.getByTestId("om-readout").textContent).toBe(
      "Alpha · Beta: 3 shared names · 60% of their combined names",
    );
  });

  it("lists who a clicked bubble shares names with, and opens the Cockpit from a ticker", async () => {
    const { user, onSelect } = setup();
    await user.click(screen.getByTestId("om-bubble-0"));
    const panel = screen.getByTestId("om-panel");
    const first = within(panel).getByTestId("om-nb-1");
    expect(within(first).getByRole("button", { name: "Beta" })).toBeInTheDocument();
    expect(first.textContent).toContain("3 names");
    expect(within(first).getAllByRole("button", { name: /^(BBB|CCC|DDD)$/ })).toHaveLength(3);
    await user.click(within(first).getByRole("button", { name: "CCC" }));
    expect(onSelect).toHaveBeenLastCalledWith("t-beta", "CCC");
    await user.click(within(panel).getByRole("button", { name: "Alpha" }));
    expect(onSelect).toHaveBeenLastCalledWith("t-alpha");
    expect(within(panel).getAllByRole("button", { name: "Open in Venn" })).toHaveLength(2);
  });

  it("says a selected strip card shares no names", async () => {
    const { user } = setup();
    await user.click(within(screen.getByTestId("om-strip")).getByRole("button", { name: /Solo/ }));
    expect(screen.getByTestId("om-panel").textContent).toContain("Shares no names with any other card in view.");
  });

  it("moves between bubbles with the arrow keys", async () => {
    const { user } = setup();
    screen.getByRole("group", { name: /Card overlap map/ }).focus();
    await user.keyboard("{ArrowRight}");
    // focus lands on the biggest card (Alpha), then the arrow moves to its neighbor to the right
    expect(screen.getByTestId("om-readout").textContent).toContain("Beta");
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("om-panel").textContent).toContain("Beta");
  });
});

describe("the Venn tab", () => {
  it("opens on the pair of a clicked line, with exact counts", async () => {
    const { user } = setup();
    await user.click(screen.getByTestId("om-line-0-1"));
    expect(screen.getByRole("tab", { name: "Venn" })).toHaveAttribute("aria-selected", "true");
    const legend = screen.getByRole("list", { name: "The sets" });
    expect(legend.textContent).toContain("Alpha");
    expect(legend.textContent).toContain("Beta");
    expect(screen.getByTestId("om-region-1").textContent).toBe("1"); // AAA only
    expect(screen.getByTestId("om-region-2").textContent).toBe("1"); // EEE only
    expect(screen.getByTestId("om-region-3").textContent).toBe("3"); // BBB CCC DDD
    expect(screen.getByText(/Circle area is proportional to the set/)).toBeInTheDocument();
  });

  it("opens on a card's pair from 'Open in Venn'", async () => {
    const { user } = setup();
    await user.click(screen.getByTestId("om-bubble-2"));
    await user.click(within(screen.getByTestId("om-nb-3")).getByRole("button", { name: "Open in Venn" }));
    const legend = screen.getByRole("list", { name: "The sets" });
    expect(legend.textContent).toContain("Gamma");
    expect(legend.textContent).toContain("Delta");
  });

  it("lists a clicked region's companies, with chips that open the Cockpit on that card", async () => {
    const { user, onSelect } = setup();
    await user.click(screen.getByRole("tab", { name: "Venn" })); // the default pair: Alpha and Beta
    await user.click(screen.getByTestId("om-region-3"));
    const list = screen.getByTestId("om-region-list");
    expect(list.textContent).toContain("In Alpha and Beta");
    expect(list.textContent).toContain("3 companies");
    expect(within(list).getByText("CCC")).toBeInTheDocument();
    await user.click(within(list).getAllByRole("button", { name: /Beta · Core/ })[0]);
    expect(onSelect).toHaveBeenCalledWith("t-beta", expect.stringMatching(/^(BBB|CCC|DDD)$/));
  });

  it("adds a third set from the picker (three sets read approximate) and removes one back", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("tab", { name: "Venn" }));
    expect(screen.queryByText(/Areas are approximate/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add a set" }));
    const picker = screen.getByTestId("om-picker-list");
    expect(within(picker).getByRole("group", { name: "Cards" })).toBeInTheDocument();
    expect(within(picker).getByRole("group", { name: "Supersectors" })).toBeInTheDocument();
    await user.type(within(picker).getByRole("searchbox"), "tech");
    await user.click(within(picker).getByRole("button", { name: /^Technology/ }));
    expect(screen.getByText(/Areas are approximate for three sets; the counts are exact/)).toBeInTheDocument();
    expect(screen.getAllByTestId(/^om-region-[0-9]+$/)).toHaveLength(7);
    expect(screen.getByRole("button", { name: "Add a set" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Remove Technology" }));
    expect(screen.getAllByTestId(/^om-region-[0-9]+$/)).toHaveLength(3);
  });
});

describe("the Sectors tab", () => {
  it("shows a column header with cards and companies, rows in the stated order, and a cell's companies", async () => {
    const { user, onSelect } = setup();
    await user.click(screen.getByRole("tab", { name: "Sectors" }));
    const grid = screen.getByTestId("om-grid");
    const heads = within(grid)
      .getAllByRole("columnheader")
      .map((c) => c.textContent);
    expect(heads[0]).toBe("Card");
    expect(heads[1]).toBe("Materials3 cards · 2 companies");
    expect(heads.at(-1)).toBe("Unclassified2 cards · 2 companies");
    const rows = within(grid)
      .getAllByRole("row")
      .slice(1)
      .map((r) => within(r).getAllByRole("rowheader")[0].textContent);
    expect(rows).toEqual(["Alpha2 sectors", "Beta3 sectors", "Gamma3 sectors", "Delta2 sectors", "Solo1 sector"]);

    await user.click(screen.getByTestId("om-cell-0-materials"));
    const list = screen.getByTestId("om-cell-list");
    expect(list.textContent).toContain("Alpha · Materials");
    await user.click(within(list).getByRole("button", { name: "DDD" }));
    expect(onSelect).toHaveBeenCalledWith("t-alpha", "DDD");
    await user.click(within(grid).getByRole("button", { name: "Beta" }));
    expect(onSelect).toHaveBeenLastCalledWith("t-beta");
  });

  it("switches the columns to business types", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("tab", { name: "Sectors" }));
    await user.click(screen.getByRole("button", { name: "Business types" }));
    const heads = within(screen.getByTestId("om-grid"))
      .getAllByRole("columnheader")
      .map((c) => c.textContent ?? "");
    expect(heads.some((t) => t.startsWith("software"))).toBe(true);
    expect(heads.at(-1)).toContain("Unclassified");
  });
});
