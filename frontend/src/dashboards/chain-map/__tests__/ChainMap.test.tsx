import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChainMap from "../ChainMap";
import type { ChainMapOut } from "../useChainMap";
import { CARDS, COLUMNS, counts, payload, rail } from "./fixtures";

// The Chain Map body with its two hooks stubbed: the columns in chain order (an old link marked, the quiet
// Context column behind its toggle), the nodes with their glyphs (a role tag on a chain name, a flagged name
// grayed, an unresolved name plain), the two rails with one edge per rail name (the counter-case dashed), the
// hover / focus readout, a click opening the Cockpit on the name, the arrow keys moving between names (one tab
// stop), the picker, the narrow (stacked) layout, and the quiet lines. The hooks' requests have their own test.

const h = vi.hoisted(() => ({ useChainMap: vi.fn(), useChainMapCards: vi.fn() }));
vi.mock("../useChainMap", () => ({ useChainMap: h.useChainMap, useChainMapCards: h.useChainMapCards }));

function setup(data: ChainMapOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useChainMapCards.mockReturnValue({ data: CARDS, isLoading: false, error: null });
  h.useChainMap.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<ChainMap asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const nodeEl = (key: string) =>
  screen.getAllByTestId("cm-node").find((n) => n.getAttribute("data-key") === key) as unknown as SVGGElement;
const nodeKeys = () => screen.getAllByTestId("cm-node").map((n) => n.getAttribute("data-key"));
const colEl = (key: string) =>
  screen.getAllByTestId("cm-col").find((c) => c.getAttribute("data-key") === key) as unknown as SVGGElement;

beforeEach(() => {
  h.useChainMap.mockReset();
  h.useChainMapCards.mockReset();
});

describe("ChainMap", () => {
  it("draws the real links in chain order, then the old link, with the quiet Context column hidden", () => {
    setup();
    const cols = screen.getAllByTestId("cm-col");
    expect(cols.map((c) => c.getAttribute("data-key"))).toEqual(["link:Reactors", "link:Fuel", "old:Old wires"]);
    expect(cols[0]).toHaveTextContent("Reactors");
    expect(cols[0]).toHaveTextContent("make the power"); // the descriptor
    expect(cols[0]).toHaveTextContent("3 names");
    expect(cols[1]).toHaveTextContent("2 names");
    expect(cols[2]).toHaveTextContent("not a link today");
    expect(cols[2]).toHaveTextContent("1 name");
    expect(colEl("old:Old wires")).toHaveClass("old_link");
  });

  it("shows the Context and Bench names with no role only behind a toggle that counts them", async () => {
    setup();
    expect(nodeKeys()).not.toContain("k-CTXN");
    const toggle = screen.getByRole("checkbox", { name: /Show Context and Bench without a role \(1\)/ });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(screen.getAllByTestId("cm-col").map((c) => c.getAttribute("data-key"))).toEqual([
      "link:Reactors",
      "link:Fuel",
      "old:Old wires",
      "silent:Context",
    ]);
    expect(colEl("silent:Context")).toHaveTextContent("no role · not in the call");
    expect(nodeKeys()).toContain("k-CTXN");
    expect(screen.getByText(/1 Context or Bench name with no role shown/)).toBeInTheDocument();
  });

  it("renders no toggle when no Context or Bench name lacks a role (a control that cannot discriminate)", () => {
    setup(payload({ columns: COLUMNS.slice(0, 3), counts: counts({ untagged_silent: 0 }) }));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("draws a node with its ticker, its role tag, its business type and its filing age", () => {
    setup();
    const oklo = within(nodeEl("k-OKLO"));
    expect(oklo.getByText("OKLO")).toBeInTheDocument();
    expect(oklo.getByText("utilities")).toBeInTheDocument();
    expect(oklo.getByText("3d")).toBeInTheDocument();
    expect(nodeEl("k-OKLO").querySelector(".cm-side")).not.toBeNull(); // above its 50-day average
    expect(nodeEl("k-OKLO").querySelector(".cm-role")).toBeNull(); // no role: no tag, no placeholder

    // a counter-case that sits in a real link keeps its place there, tagged, and has no rail copy
    const smr = within(nodeEl("k-SMR"));
    expect(smr.getByText("counter-case")).toBeInTheDocument();
    expect(smr.getByText("—")).toBeInTheDocument(); // no filing stored
    expect(nodeKeys().filter((k) => k === "k-SMR")).toHaveLength(1);

    // a business type the master lacks draws nothing in its place
    expect(nodeEl("k-OLDW").querySelector(".cm-type")).toBeNull();
    expect(within(nodeEl("k-CCJ")).getByText("miner")).toBeInTheDocument();
  });

  it("draws a flagged name grayed with no 50-day mark, and an unresolved name plain with no glyphs", () => {
    setup();
    const rbot = nodeEl("k-RBOT");
    expect(rbot).toHaveClass("flagged");
    expect(rbot.querySelector(".cm-side")).toBeNull();
    expect(within(rbot).getByText("3d")).toBeInTheDocument(); // a filing is not a price reading
    expect(rbot.getAttribute("aria-label")).toContain("50-day side left out: mixed-scale price history");
    const unr = nodeEl("k-UNR");
    expect(unr).toHaveClass("unresolved");
    expect(within(unr).getByText("no resolved security")).toBeInTheDocument();
    expect(unr.querySelector(".cm-age")).toBeNull();
    expect(unr.querySelector(".cm-side")).toBeNull();
    // nothing sets a flagged name apart but its gray: still on the map, still one of the column's names
    expect(colEl("link:Reactors")).toHaveTextContent("3 names");
  });

  it("draws the two rails beside the chain, one edge per rail name, the counter-case edge dashed", () => {
    const { container } = setup();
    const zones = container.querySelectorAll(".cm-frame > .cm-zone");
    expect(Array.from(zones).map((z) => z.className)).toEqual([
      "cm-zone cm-zone-left",
      "cm-zone cm-zone-chain",
      "cm-zone cm-zone-right",
    ]);
    expect(screen.getByText("what pays and funds")).toBeInTheDocument();
    expect(screen.getByText("what reads across")).toBeInTheDocument();
    expect(zones[0].querySelectorAll("[data-testid=cm-node]")).toHaveLength(2);
    expect(zones[2].querySelectorAll("[data-testid=cm-node]")).toHaveLength(2);
    expect(zones[1].querySelectorAll("[data-testid=cm-node]")).toHaveLength(6);

    const edges = screen.getAllByTestId("cm-edge");
    expect(edges.map((e) => e.getAttribute("data-role"))).toEqual(["payer", "financier", "direct_read", "counter_case"]);
    expect(edges.filter((e) => e.querySelector("path.dashed"))).toHaveLength(1);
    expect(edges[3].querySelector("path")).toHaveClass("dashed");
    // each edge ends in a small mark on a bracket, one bracket per rail
    expect(container.querySelectorAll(".cm-end")).toHaveLength(4);
    expect(container.querySelectorAll(".cm-bracket")).toHaveLength(2);
    // the role is the edge's label
    expect(Array.from(container.querySelectorAll(".cm-edge-label")).map((l) => l.textContent)).toEqual([
      "payer",
      "financier",
      "direct read",
      "counter-case",
    ]);
    expect(nodeEl("k-MSFT").querySelector(".cm-role")).toHaveTextContent("payer");
  });

  it("draws no rail zone and no edge when no role-tagged name stands outside the chain", () => {
    const { container } = setup(payload({ rails: [rail("left", []), rail("right", [])], edges: [] }));
    expect(container.querySelectorAll(".cm-frame > .cm-zone")).toHaveLength(1);
    expect(screen.queryAllByTestId("cm-edge")).toHaveLength(0);
    expect(screen.queryByText("what pays and funds")).toBeNull();
  });

  it("stacks the rails above and below the chain on a narrow screen", () => {
    const mm = vi.fn().mockImplementation((q: string) => ({
      matches: true,
      media: q,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    vi.stubGlobal("matchMedia", mm);
    try {
      const { container } = setup();
      const frame = container.querySelector(".cm-frame");
      expect(frame).toHaveClass("stacked");
      expect(Array.from(container.querySelectorAll(".cm-frame > .cm-zone")).map((z) => z.className)).toEqual([
        "cm-zone cm-zone-left",
        "cm-zone cm-zone-chain",
        "cm-zone cm-zone-right",
      ]);
      expect(screen.getAllByTestId("cm-edge")).toHaveLength(4);
      expect(container.querySelectorAll(".cm-edge-label")).toHaveLength(0); // each rail name carries its role tag
      expect(nodeEl("k-DIRX").querySelector(".cm-role")).toHaveTextContent("direct read");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("the readout and the Cockpit", () => {
  it("shows a prompt until a name is hovered, then its link, description, author, terms, side and filing", async () => {
    setup();
    const readout = screen.getByTestId("cm-readout");
    expect(readout).toHaveTextContent("Hover or focus a name");
    await userEvent.hover(nodeEl("k-OKLO"));
    expect(readout).toHaveTextContent("OKLO");
    expect(readout).toHaveTextContent("OKLO Corp");
    expect(readout).toHaveTextContent("Reactors");
    expect(readout).toHaveTextContent("builds small modular reactors");
    expect(readout).toHaveTextContent("(your words)");
    expect(readout).toHaveTextContent("Matched terms: SMR, reactor");
    expect(readout).toHaveTextContent("above its 50-day average (+4.2%)");
    expect(readout).toHaveTextContent("last filing: 8-K · Oct 25, 2026 (3 days before the as-of) · 8-K · 9.01");
    await userEvent.unhover(nodeEl("k-OKLO"));
    expect(readout).toHaveTextContent("Hover or focus a name");
  });

  it("shows a model draft as one, a role with its approved description, and 'none stored' as a statement", async () => {
    setup();
    const readout = screen.getByTestId("cm-readout");
    await userEvent.hover(nodeEl("k-SMR"));
    expect(readout).toHaveTextContent("a rival design");
    expect(readout).toHaveTextContent("(model draft)");
    expect(readout).toHaveTextContent("counter-case: Loses if the thesis plays out, or wins if it fails.");
    expect(readout).toHaveTextContent("below its 50-day average (-1.5%)");
    expect(readout).toHaveTextContent("no filing stored in the last year");
    // a rail name reads its place on the card and its role
    await userEvent.hover(nodeEl("k-MSFT"));
    expect(readout).toHaveTextContent("Microsoft");
    expect(readout).toHaveTextContent("Context");
    expect(readout).toHaveTextContent("payer: The demand side: buys the theme's product or pays its price.");
    // a flagged name says why its side is left out; an unresolved one says there is no security
    await userEvent.hover(nodeEl("k-RBOT"));
    expect(readout).toHaveTextContent("50-day side left out: mixed-scale price history");
    await userEvent.hover(nodeEl("k-UNR"));
    expect(readout).toHaveTextContent("no resolved security");
  });

  it("opens the Cockpit on the clicked name, rail names included, and on the card from its name", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(nodeEl("k-OKLO"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "OKLO");
    await userEvent.click(nodeEl("k-MSFT"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "MSFT");
    await userEvent.click(screen.getByRole("button", { name: "Nuclear" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke");
  });
});

describe("keyboard", () => {
  it("is one tab stop, and focus drives the same readout", () => {
    setup();
    const stops = screen.getAllByTestId("cm-node").filter((n) => n.getAttribute("tabindex") === "0");
    expect(stops).toHaveLength(1);
    expect(stops[0].getAttribute("data-key")).toBe("k-MSFT"); // the first name: the left rail's top
    act(() => nodeEl("k-OKLO").focus());
    expect(screen.getByTestId("cm-readout")).toHaveTextContent("OKLO Corp");
    expect(nodeEl("k-OKLO").getAttribute("tabindex")).toBe("0");
    expect(nodeEl("k-MSFT").getAttribute("tabindex")).toBe("-1");
    act(() => nodeEl("k-OKLO").blur());
    expect(screen.getByTestId("cm-readout")).toHaveTextContent("Hover or focus a name");
  });

  it("moves with the arrow keys down a column, across columns and onto the rails", async () => {
    const { onSelectThesis } = setup();
    act(() => nodeEl("k-OKLO").focus());
    await userEvent.keyboard("{ArrowDown}");
    expect(nodeEl("k-SMR")).toHaveFocus();
    expect(screen.getByTestId("cm-readout")).toHaveTextContent("a rival design");
    await userEvent.keyboard("{ArrowRight}"); // Fuel, the same row
    expect(nodeEl("k-UNR")).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}"); // the old link has one name: its only one
    expect(nodeEl("k-OLDW")).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}"); // onto the right rail
    expect(nodeEl("k-DIRX")).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(nodeEl("k-CNTR")).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}"); // the end of the rail: stays
    expect(nodeEl("k-CNTR")).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(nodeEl("k-OLDW")).toHaveFocus(); // the old link's only name
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}{ArrowLeft}"); // each step lands on the row it came from, or the last
    expect(nodeEl("k-MSFT")).toHaveFocus(); // the left rail's top
    await userEvent.keyboard("{ArrowDown}");
    expect(nodeEl("k-BX")).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "BX");
  });

  it("keeps working when the toggle changes the columns under the focus", async () => {
    setup();
    act(() => nodeEl("k-OLDW").focus());
    await userEvent.click(screen.getByRole("checkbox", { name: /Show Context and Bench without a role/ }));
    // the Context column joined the chain after the old link: one step right of it
    act(() => nodeEl("k-OLDW").focus());
    await userEvent.keyboard("{ArrowRight}");
    expect(nodeEl("k-CTXN")).toHaveFocus();
  });
});

describe("the picker and the page", () => {
  it("searches and switches cards; the first A→Z is the default", async () => {
    setup();
    const select = screen.getByRole("combobox", { name: "Card" });
    expect(select).toHaveValue("t-copper");
    expect(h.useChainMap).toHaveBeenLastCalledWith("2026-10-28", "t-copper");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links",
      "Grid · 5 links",
      "Nuclear · 2 links",
    ]);
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a card" }), "nuc");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links", // the current card stays selectable while the search narrows the rest
      "Nuclear · 2 links",
    ]);
    await userEvent.selectOptions(select, "t-nuke");
    expect(h.useChainMap).toHaveBeenLastCalledWith("2026-10-28", "t-nuke");
  });

  it("shows the work, the roster note, the flagged names, the glyph legend and the known limits", () => {
    setup(payload({ roster_fallback: true, asof: "2026-09-01", cache: "computed", compute_ms: 4200 }));
    const work = screen.getByText(/2 links today and 1 roster link/);
    expect(work).toHaveTextContent("11 roster rows (10 resolved, 1 without a resolved security), 11 companies");
    expect(work).toHaveTextContent("a 50-day side for 6 of 11 names (5 without)");
    expect(work).toHaveTextContent("computed in 4.2 s");
    expect(work).toHaveTextContent("as of Sep 1, 2026");
    expect(screen.getByText(/Today's roster stands in for this card/)).toHaveTextContent("Sep 15, 2026");
    expect(screen.getByText(/1 name left out for a flagged price history/)).toBeInTheDocument();
    expect(screen.getByText(/On each name: its business type/)).toHaveTextContent("none stored in the last year");
    expect(screen.getByText(/A role names a part, not a target/)).toHaveTextContent("never on a link or a name");
    expect(screen.getByText(/The roster is the card's roster as of Sep 1, 2026/)).toHaveTextContent("only the roster is snapshotted");
    expect(screen.getByText(/display context and never feed a call/)).toBeInTheDocument();
  });

  it("shows the slow first-load message while the first read computes, and an error when it fails", () => {
    const { unmount } = setup(undefined, { isLoading: true });
    expect(screen.getByText(/Reading this card's names — the first load after a run or a basket edit/)).toBeInTheDocument();
    unmount();
    setup(undefined, { error: new Error("boom") });
    expect(screen.getByText("Could not load the Chain Map.")).toBeInTheDocument();
  });

  it("says so when no card existed, and when the cards fail to load", () => {
    h.useChainMapCards.mockReturnValue({ data: { ...CARDS, cards: [] }, isLoading: false, error: null });
    h.useChainMap.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: null });
    const { unmount } = render(<ChainMap asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("No card existed by this as-of.")).toBeInTheDocument();
    unmount();
    h.useChainMapCards.mockReturnValue({ data: undefined, isLoading: false, error: new Error("x") });
    render(<ChainMap asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Chain Map's cards.")).toBeInTheDocument();
  });
});

afterEach(() => vi.unstubAllGlobals());
